import itertools
import shutil
import subprocess
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from core import roxy_registration as roxy


class PasswordTransitionTests(unittest.TestCase):
    def run_flow(self, otp_states, click_results, page_states, expected_error=None, diagnostics=None):
        driver = Mock(current_url="https://auth.openai.com/create-account/password")
        driver.execute_script.return_value = {"ok": True, "input": Mock(), "button": Mock()}
        with ExitStack() as stack:
            for name in ("_human_type_text", "_human_click", "human_delay", "_click_continue"):
                stack.enter_context(patch.object(roxy, name))
            stack.enter_context(patch.object(roxy.time, "sleep"))
            stack.enter_context(patch.object(roxy.time, "time", side_effect=itertools.count()))
            stack.enter_context(patch.object(roxy, "_has_access_token", return_value=False))
            stack.enter_context(patch.object(roxy, "_is_signup_password_page", return_value=True))
            stack.enter_context(patch.object(roxy, "_is_login_password_page", return_value=False))
            stack.enter_context(patch.object(roxy, "_registration_password", return_value="test-password"))
            stack.enter_context(patch.object(roxy, "_is_email_verification_page", side_effect=otp_states))
            stack.enter_context(patch.object(roxy, "_password_page_state", side_effect=page_states))
            click = stack.enter_context(patch.object(roxy, "_click_continue_with_password_if_present", side_effect=click_results))
            if expected_error:
                with self.assertRaisesRegex(RuntimeError, expected_error):
                    roxy._fill_password_page_if_present(driver, "test@example.com", diagnostics=diagnostics)
            else:
                self.assertEqual(roxy._fill_password_page_if_present(driver, "test@example.com", diagnostics=diagnostics), "test-password")
            self.scripts = [call.args[0] for call in driver.execute_script.call_args_list]
            return click.call_count

    def test_waits_after_click_without_clicking_stale_button_again(self):
        calls = self.run_flow([True, True, False, True], [{"ok": True}], [{}, {}])
        self.assertEqual(calls, 1)

    def test_stale_element_rechecks_page_instead_of_returning_to_otp(self):
        calls = self.run_flow([True, False, True], [{"ok": False, "reason": "StaleElementReferenceException: stale"}], [{}, {}])
        self.assertEqual(calls, 1)

    def test_password_rejection_reports_page_error(self):
        self.run_flow([False, False], [], [{}, {"inputs": [{"visible": True, "type": "password"}], "errors": ["アカウントを作成できませんでした。もう一度お試しください"]}], "密码提交失败.*アカウント")

    def test_diagnostics_capture_submit_and_do_not_break_registration(self):
        diagnostics = Mock()
        self.run_flow([False, True], [], [{}, {}], diagnostics=diagnostics)
        self.assertEqual([call.args[0] for call in diagnostics.call_args_list],
                         ["password_entry", "password_before_submit", "password_after_submit"])
        diagnostics.side_effect = RuntimeError("diagnostics unavailable")
        self.run_flow([False, True], [], [{}, {}], diagnostics=diagnostics)

    def test_otp_required_error_is_not_a_password_rejection(self):
        self.run_flow([False, True], [], [{}, {"errors": ["検証コードが必要です"]}])

    def test_transition_errors_without_password_input_wait_for_otp(self):
        self.run_flow([False, False, True], [], [{}, {"errors": ["検証コードが必要です"]}, {}])

    def test_password_timeout_does_not_continue_to_otp(self):
        self.run_flow(itertools.repeat(False), [], itertools.repeat({}), "密码提交后未确认进入验证码页")

    @unittest.skipUnless(shutil.which("node"), "Node is needed to execute the browser script")
    def test_retry_script_cannot_submit_an_otp_form(self):
        self.run_flow(itertools.repeat(False), [], itertools.repeat({}), "密码提交后未确认进入验证码页")
        script = self.scripts[-1]
        harness = """
        const assert = require('node:assert/strict');
        let clicks = 0, inputs = [];
        const submit = {disabled:false, getClientRects:()=>[1], getAttribute:()=>null, click:()=>clicks++};
        const form = {querySelector:()=>submit};
        const pass = {value:'test-password', getClientRects:()=>[1], closest:()=>form};
        global.document = {querySelectorAll:()=>inputs};
        const retry = () => { SCRIPT };
        assert.equal(retry().clicked, false);
        assert.equal(clicks, 0);
        inputs = [pass];
        assert.equal(retry().clicked, true);
        assert.equal(clicks, 1);
        submit.disabled = true;
        assert.equal(retry().clicked, false);
        assert.equal(clicks, 1);
        """.replace("SCRIPT", script)
        subprocess.run([shutil.which("node"), "-e", harness], check=True, capture_output=True, text=True)

    def test_switch_timeout_does_not_continue_to_otp(self):
        self.run_flow(itertools.repeat(True), [{"ok": True}], [], "等待密码页或验证码页超时")

    def test_otp_page_without_password_option_can_continue(self):
        with patch.object(roxy, "_is_email_verification_page", return_value=True), patch.object(
            roxy, "_click_continue_with_password_if_present",
            return_value={"ok": False, "reason": "missing_continue_with_password"},
        ):
            self.assertIsNone(roxy._fill_password_page_if_present(Mock(), "test@example.com"))


if __name__ == "__main__":
    unittest.main()
