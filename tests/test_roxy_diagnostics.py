import json
import shutil
import subprocess
import unittest
from unittest.mock import Mock

from core import roxy_registration as roxy
from core.browser_traffic import SeleniumTrafficTracker


class RoxyDiagnosticsTests(unittest.TestCase):
    def test_network_diagnostics_keep_failure_code_and_hide_credentials(self):
        driver = Mock()
        driver.get_log.return_value = []
        driver.execute_cdp_cmd.return_value = {"body": json.dumps({
            "error": {"code": "invalid_auth_step", "message": "private-password"},
            "access_token": "private-token",
        })}
        tracker = SeleniumTrafficTracker(driver)
        for request_id in ("rejected", "failed", "pending"):
            tracker._handle_cdp_event("Network.requestWillBeSent", {
                "requestId": request_id, "type": "Fetch",
                "request": {"method": "POST", "url": "https://auth.openai.com/api/accounts/user/register?token=private-query",
                            "postData": "private-password", "headers": {"Cookie": "private-cookie"}},
            })
        tracker._handle_cdp_event("Network.responseReceived", {
            "requestId": "rejected", "response": {"status": 400, "mimeType": "application/json"},
        })
        tracker._handle_cdp_event("Network.loadingFinished", {"requestId": "rejected"})
        tracker._handle_cdp_event("Network.loadingFailed", {
            "requestId": "failed", "errorText": "net::ERR_CONNECTION_RESET private-error", "canceled": False,
        })
        with self.assertLogs("core.browser_traffic", level="INFO") as logs:
            tracker.log_auth_diagnostics("password_timeout")
        text = "\n".join(logs.output)
        self.assertIn("invalid_auth_step", text)
        self.assertIn("ERR_CONNECTION_RESET", text)
        self.assertIn('"finished": false', text)
        self.assertIn('"status": 400', text)
        self.assertNotIn("private-", text)
        self.assertEqual(tracker.failed_request_count, 1)

    @unittest.skipUnless(shutil.which("node"), "Node is needed for the page script check")
    def test_page_diagnostics_do_not_expose_input_values(self):
        driver = Mock()
        roxy._password_page_state(driver)
        script = driver.execute_script.call_args.args[0]
        harness = r"""
        const assert = require('node:assert/strict');
        global.location = {href:'https://auth.openai.com/create-account/password?token=private-query',
          origin:'https://auth.openai.com', pathname:'/create-account/password'};
        global.getComputedStyle = ()=>({visibility:'visible',display:'block'});
        const values = ['private-password!', '123456', 'private-token', 'a@example.com'];
        const elements = values.map(value=>({value, type:'password', getAttribute:k=>k==='type'?'password':'',
          offsetWidth:10, validity:{valid:false,tooShort:true}, validationMessage:'invalid '+value}));
        const error = {offsetWidth:10, innerText:'invalid '+values.join(' ')};
        global.document = {title:'Create password',readyState:'complete',querySelectorAll:selector=>{
          if(selector==='input') return elements;
          if(selector==='form') return [{getAttribute:()=>location.href}];
          if(selector.startsWith('button')) return [];
          return [error];
        }};
        const state = new Function(SCRIPT)();
        const text = JSON.stringify(state);
        for(const value of [...values,'private-query']) assert(!text.includes(value),value);
        assert.equal(state.inputs[0].length, values[0].length);
        assert.equal(state.inputs[0].tooShort, true);
        assert.equal(state.readyState,'complete');
        assert.equal(state.errors.length,1);
        """.replace("SCRIPT", json.dumps(script))
        subprocess.run([shutil.which("node"), "-e", harness], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
