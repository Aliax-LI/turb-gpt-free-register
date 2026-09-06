import json
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

from core import roxy_registration as roxy
from core.roxybrowser_client import RoxyBrowserClient, RoxyOpenResult
from core.roxy_codex_oauth import clear_roxy_browser_auth_state


class RoxyHybridTests(unittest.TestCase):
    def test_entry_allocates_after_csrf_and_keeps_full_authorize_url(self):
        driver = Mock(current_url="https://chatgpt.com/api/auth/csrf")
        driver.execute_script.return_value = '{"csrfToken":"csrf"}'
        supplier = Mock(return_value="test@example.com")
        authorize = "https://auth.openai.com/authorize?state=" + "x" * 350
        with patch.object(roxy, "_safe_get") as get, patch.object(
            roxy, "_submit_email_via_browser_nextauth", return_value={"ok": True, "url": authorize}
        ) as signin:
            self.assertTrue(roxy._start_roxy_hybrid(driver, supplier))
        supplier.assert_called_once()
        signin.assert_called_once_with(driver, "test@example.com", csrf_token="csrf")
        self.assertEqual(get.call_args_list[-1].args[1], authorize)

    def test_unready_csrf_does_not_allocate_email(self):
        driver = Mock(current_url="https://chatgpt.com/api/auth/csrf")
        driver.execute_script.return_value = "<html>verification required</html>"
        supplier = Mock()
        with patch.object(roxy, "_safe_get"):
            self.assertFalse(roxy._start_roxy_hybrid(driver, supplier))
        supplier.assert_not_called()

    def test_untrusted_redirect_and_uncertain_post_are_not_retried(self):
        for result in ({"ok": True, "url": "https://auth.openai.com.evil.test/"}, {"ok": False, "stage": "exception"}):
            driver = Mock(current_url="https://chatgpt.com/api/auth/csrf")
            driver.execute_script.return_value = '{"csrfToken":"csrf"}'
            with self.subTest(result=result), patch.object(roxy, "_safe_get") as get, patch.object(
                roxy, "_submit_email_via_browser_nextauth", return_value=result
            ) as signin, self.assertRaises(RuntimeError):
                roxy._start_roxy_hybrid(driver, lambda: "test@example.com")
            signin.assert_called_once()
            get.assert_called_once()

    def test_missing_login_form_recovers_same_email_once(self):
        driver = Mock()
        states = [{"inputs": [{"value": "test@example.com"}]}, {"inputs": [], "url": "https://chatgpt.com/auth/login"}]
        with patch.object(roxy, "_type_email_address") as type_email, patch.object(roxy, "_submit_email_step"), patch.object(
            roxy, "_email_input_value_state", side_effect=states
        ), patch.object(roxy, "_wait_email_submit_next_state", side_effect=["unknown", "otp"]), patch.object(
            roxy, "_start_roxy_hybrid", side_effect=lambda d, supplier: supplier() == "test@example.com"
        ) as recover, patch.object(roxy, "human_delay"):
            self.assertEqual(roxy._submit_email_and_wait_next(driver, "test@example.com"), "otp")
        type_email.assert_called_once()
        recover.assert_called_once()

    @unittest.skipUnless(shutil.which("node"), "Node is needed for the browser script check")
    def test_browser_http_uses_same_session_and_rejects_foreign_origin(self):
        driver = Mock(current_url="https://chatgpt.com/api/auth/csrf")
        driver.get_cookie.return_value = {"value": "existing-device"}
        driver.execute_async_script.return_value = {"ok": True}
        roxy._submit_email_via_browser_nextauth(driver, "a+b@example.com", csrf_token="csrf")
        driver.add_cookie.assert_not_called()
        script = driver.execute_async_script.call_args.args[0]
        harness = r"""
        const assert = require('node:assert/strict');
        const run = new Function(SCRIPT);
        global.location = {origin:'https://chatgpt.com', href:'https://chatgpt.com/api/auth/csrf'};
        let requests = 0, target = 'https://auth.openai.com/authorize?state=' + 'x'.repeat(350);
        global.fetch = async (url, options) => {
          requests++;
          assert.equal(options.credentials, 'include');
          assert.equal(options.redirect, 'error');
          assert.equal(options.method, 'POST');
          assert.equal(new URL(url, location.origin).searchParams.get('login_hint'), 'a+b@example.com');
          assert.equal(new URLSearchParams(options.body).get('csrfToken'), 'csrf');
          assert.equal(new URLSearchParams(options.body).get('callbackUrl'), 'https://chatgpt.com/api/auth/session');
          return {ok:true, status:200, text:async()=>JSON.stringify({url:target})};
        };
        (async()=>{
          const invoke = () => new Promise(resolve=>run('a+b@example.com','existing-device','log','csrf',resolve));
          assert.equal((await invoke()).ok, true);
          assert.equal(requests, 1);
          target = 'https://evil.test/';
          assert.equal((await invoke()).stage, 'invalid_authorize_url');
          location.origin = 'https://evil.test';
          assert.equal((await invoke()).stage, 'origin');
          assert.equal(requests, 2);
        })().catch(e=>{console.error(e);process.exitCode=1;});
        """.replace("SCRIPT", json.dumps(script))
        subprocess.run([shutil.which("node"), "-e", harness], check=True, capture_output=True, text=True)

    def test_dispatches_hybrid_without_switching_default(self):
        import main
        with patch.object(main._roxy_cfg, "REGISTRATION_DRIVER", "roxy_hybrid"), patch.object(roxy, "run_roxy_registration", return_value={"success": True}) as run:
            main.run_registration("test@example.com", "Test", birthday="1995-01-01")
        self.assertTrue(run.call_args.kwargs["hybrid"])

    @unittest.skipUnless(shutil.which("node"), "Node is needed for the browser script check")
    def test_session_checks_avoid_cross_origin_and_reuse_loaded_json(self):
        driver = Mock()
        driver.execute_async_script.return_value = None
        roxy._has_access_token(driver)
        has_script = driver.execute_async_script.call_args.args[0]
        roxy._read_chatgpt_session_once(driver)
        read_script = driver.execute_async_script.call_args.args[0]
        harness = r"""
        const assert = require('node:assert/strict');
        const has = new Function(HAS_SCRIPT), read = new Function(READ_SCRIPT);
        let requests = 0;
        global.location = {origin:'https://auth.openai.com',pathname:'/create-account/password'};
        global.document = {body:{innerText:'{"accessToken":"test-token"}'}};
        global.fetch = async()=>{ requests++; return {json:async()=>({accessToken:'test-token'})}; };
        (async()=>{
          assert.equal(await new Promise(done=>has(done)),false);
          assert.equal((await new Promise(done=>read(done))).ok,false);
          assert.equal(requests,0);
          location.origin='https://chatgpt.com'; location.pathname='/api/auth/session';
          assert.equal((await new Promise(done=>read(done))).data.accessToken,'test-token');
          assert.equal(requests,0);
          document.body.innerText='{}';
          assert.equal((await new Promise(done=>read(done))).data.accessToken,'test-token');
          assert.equal(requests,1);
        })().catch(e=>{console.error(e);process.exitCode=1;});
        """.replace("HAS_SCRIPT", json.dumps(has_script)).replace("READ_SCRIPT", json.dumps(read_script))
        subprocess.run([shutil.which("node"), "-e", harness], check=True, capture_output=True, text=True)

    def test_completed_page_parking_preserves_debug_mode_and_save_on_error(self):
        driver = Mock()
        with patch.object(roxy._cfg, "ROXY_KEEP_BROWSER_OPEN", True):
            roxy._park_completed_roxy_page(driver)
        driver.get.assert_not_called()
        with patch.object(roxy._cfg, "ROXY_KEEP_BROWSER_OPEN", False):
            roxy._park_completed_roxy_page(driver)
            driver.get.assert_called_once_with("about:blank")
            driver.get.side_effect = RuntimeError("navigation failed")
            roxy._park_completed_roxy_page(driver)


class RoxyIsolationTests(unittest.TestCase):
    def test_fresh_mode_rejects_fixed_profile(self):
        with patch.object(roxy._cfg, "ROXY_PROFILE_ID", "old"), patch.object(roxy._cfg, "ROXY_ONE_PROFILE_PER_ACCOUNT", False):
            with self.assertRaisesRegex(RuntimeError, "一号一环境"):
                RoxyBrowserClient().open_profile(require_fresh=True)

    def test_open_failure_cleans_up_owned_profile(self):
        client = RoxyBrowserClient()
        with patch.object(roxy._cfg, "ROXY_PROFILE_ID", ""), patch.object(client, "create_profile", return_value="new"), patch.object(
            client, "_open_existing_profile", side_effect=RuntimeError("open failed")
        ), patch.object(client, "close_profile") as close, patch.object(client, "delete_profile") as delete:
            with self.assertRaisesRegex(RuntimeError, "open failed"):
                client.open_profile(require_fresh=True)
        close.assert_called_once_with("new")
        delete.assert_called_once_with("new")

    def test_extra_parameters_cannot_replace_new_profile(self):
        client = RoxyBrowserClient()
        with patch.object(roxy._cfg, "ROXY_OPEN_EXTRA_PARAMS", {"dirId": "old"}), patch.object(
            client, "request", return_value={"data": {"http": "127.0.0.1:9000"}}
        ) as request:
            opened = client._open_existing_profile("new", True)
        self.assertEqual((request.call_args.kwargs.get("json_body") or request.call_args.kwargs["params"])["dirId"], "new")
        self.assertTrue(opened.created_by_run)

    def test_cleaning_fails_closed(self):
        for failed_command in ("Network.clearBrowserCookies", "Network.clearBrowserCache", "Storage.clearDataForOrigin"):
            driver = Mock()
            def command(name, params):
                if name == failed_command:
                    raise RuntimeError("CDP failed")
            driver.execute_cdp_cmd.side_effect = command
            with self.subTest(command=failed_command), self.assertRaisesRegex(RuntimeError, "清理失败"):
                clear_roxy_browser_auth_state(driver, strict=True)

    def test_new_environment_closes_other_tabs_before_cleaning(self):
        driver = Mock(current_window_handle="main", window_handles=["main", "startup"])
        with patch("core.roxy_codex_oauth.clear_roxy_browser_auth_state") as clear:
            roxy._prepare_roxy_registration_environment(driver, RoxyOpenResult("new", {}, created_by_run=True))
        driver.close.assert_called_once()
        driver.get.assert_called_once_with("about:blank")
        clear.assert_called_once_with(driver, strict=True)
        with self.assertRaisesRegex(RuntimeError, "新建"):
            roxy._prepare_roxy_registration_environment(driver, RoxyOpenResult("old", {}))


if __name__ == "__main__":
    unittest.main()
