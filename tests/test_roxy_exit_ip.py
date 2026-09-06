# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import db
from core import roxy_registration as roxy


class RoxyExitIpTests(unittest.TestCase):
    def setUp(self):
        roxy._ROXY_EXIT_IP_CLAIMS.clear()

    def tearDown(self):
        roxy._ROXY_EXIT_IP_CLAIMS.clear()

    def test_reads_ip_from_the_roxy_browser(self):
        driver = Mock()
        driver.execute_script.return_value = '{"ip":"203.0.113.8"}'
        with patch.object(roxy, "_safe_get") as get:
            self.assertEqual(roxy._read_roxy_exit_ip(driver), "203.0.113.8")

        get.assert_called_once()

    def test_claim_blocks_another_running_task_on_the_same_ip(self):
        with patch("core.db.get_account_by_exit_ip", return_value=None):
            roxy._claim_roxy_exit_ip("203.0.113.8")
            with self.assertRaisesRegex(RuntimeError, "已被另一注册任务占用"):
                roxy._claim_roxy_exit_ip("203.0.113.8")

    def test_exit_ip_fallback_and_errors(self):
        from selenium.common.exceptions import WebDriverException

        for failure in ("警告" * 120 + " NET::ERR_CERT_AUTHORITY_INVALID", "invalid IP", WebDriverException("net::ERR_CERT_DATE_INVALID")):
            with self.subTest(failure=failure):
                driver = Mock()
                driver.execute_script.side_effect = [failure, "203.0.113.8"]
                with patch.object(roxy, "_safe_get") as get:
                    self.assertEqual(roxy._read_roxy_exit_ip(driver), "203.0.113.8")
                self.assertEqual(get.call_args_list[-1].args[1], "https://checkip.amazonaws.com/")

        driver = Mock()
        driver.execute_script.return_value = "警告" * 120 + " NET::ERR_CERT_AUTHORITY_INVALID"
        with patch.object(roxy, "_safe_get"), self.assertRaisesRegex(RuntimeError, "查询全部失败.*NET::ERR_CERT_AUTHORITY_INVALID"):
            roxy._read_roxy_exit_ip(driver)

    def test_saved_ip_is_found_after_a_restart(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            missing = root / "missing.json"
            with patch.multiple(
                db,
                _ACCOUNTS_JSON=root / "accounts.json",
                _OUTLOOK_JSON=missing,
                _GENERIC_API_EMAIL_JSON=missing,
                _DOMAIN_EMAIL_JSON=missing,
                _JOBS_JSON=missing,
                _LEGACY_ACCOUNTS_JSON=missing,
                _LEGACY_OUTLOOK_JSON=missing,
                _LEGACY_JOBS_JSON=missing,
                _LEGACY_SQLITE=root / "legacy.db",
                _CODEX_DIR=root / "codex_accounts",
                _CODEX_AGENT_DIR=root / "codex_agent_accounts",
                _LEGACY_CODEX_EXPORT_STATE=missing,
                _SQLITE_READY=False,
                _SQLITE_READY_PATH=None,
            ):
                db.insert_account(email="ip@test.com", access_token="token", exit_ip="203.0.113.8")
                account = db.get_account_by_exit_ip("203.0.113.8")

        self.assertEqual(account["email"], "ip@test.com")


if __name__ == "__main__":
    unittest.main()
