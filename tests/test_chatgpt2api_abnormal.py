# -*- coding: utf-8 -*-
"""ChatGPT2API 异常账号筛选 + 查活后导入/删除的判定逻辑。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import chatgpt2api_sync, db

_OLD_TOKEN = "old-token"
_NEW_TOKEN = "new-token"


def _remote(email: str, status: str, token: str) -> dict:
    return {"email": email, "status": status, "access_token": token}


class ChatGPT2ApiAbnormalTests(unittest.TestCase):
    def setUp(self):
        chatgpt2api_sync.forget_old_tokens(1)

    def test_scan_matches_local_accounts_and_remembers_tokens(self):
        remote = [
            _remote("a@test.com", "异常", _OLD_TOKEN),
            _remote("b@test.com", "正常", "live-token"),
            _remote("missing@test.com", "异常", "orphan-token"),
        ]
        with patch.object(chatgpt2api_sync, "list_remote_accounts", return_value={"ok": True, "items": remote}), \
             patch.object(chatgpt2api_sync.db, "get_account_by_email", side_effect=lambda e: {"id": 1, "email": e} if e == "a@test.com" else None):
            result = chatgpt2api_sync.scan_abnormal_accounts()

        self.assertEqual(result["abnormal_count"], 2)
        self.assertEqual(result["account_ids"], [1])
        self.assertEqual(result["unmatched_remote"], 1)
        self.assertEqual(chatgpt2api_sync._old_tokens(1), [_OLD_TOKEN])

    def test_live_account_imports_new_token_then_deletes_old(self):
        chatgpt2api_sync._remember_old_tokens(1, [_OLD_TOKEN])
        with patch.object(chatgpt2api_sync, "sync_account", return_value={"ok": True, "status": "success"}) as sync, \
             patch.object(chatgpt2api_sync, "delete_accounts", return_value={"ok": True, "status": "success"}) as delete:
            out = chatgpt2api_sync.handle_live_check_finished(1, {"ok": True, "status": "live", "access_token": _NEW_TOKEN})

        self.assertTrue(out["handled"])
        sync.assert_called_once_with(_NEW_TOKEN, force=True)
        delete.assert_called_once_with([_OLD_TOKEN])
        self.assertEqual(chatgpt2api_sync._old_tokens(1), [])

    def test_deactivated_account_only_deletes(self):
        chatgpt2api_sync._remember_old_tokens(1, [_OLD_TOKEN])
        with patch.object(chatgpt2api_sync, "sync_account") as sync, \
             patch.object(chatgpt2api_sync, "delete_accounts", return_value={"ok": True, "status": "success"}) as delete:
            out = chatgpt2api_sync.handle_live_check_finished(1, {"ok": False, "status": "deactivated"})

        self.assertTrue(out["handled"])
        sync.assert_not_called()
        delete.assert_called_once_with([_OLD_TOKEN])

    def test_other_failure_keeps_remote_account(self):
        chatgpt2api_sync._remember_old_tokens(1, [_OLD_TOKEN])
        with patch.object(chatgpt2api_sync, "delete_accounts") as delete:
            out = chatgpt2api_sync.handle_live_check_finished(1, {"ok": False, "status": "failed", "error": "HTTP 403"})

        self.assertFalse(out["handled"])
        delete.assert_not_called()
        self.assertEqual(chatgpt2api_sync._old_tokens(1), [_OLD_TOKEN])

    def test_import_failure_keeps_old_account(self):
        chatgpt2api_sync._remember_old_tokens(1, [_OLD_TOKEN])
        with patch.object(chatgpt2api_sync, "sync_account", return_value={"ok": False, "status": "failed"}), \
             patch.object(chatgpt2api_sync, "delete_accounts") as delete:
            out = chatgpt2api_sync.handle_live_check_finished(1, {"ok": True, "status": "live", "access_token": _NEW_TOKEN})

        self.assertFalse(out["handled"])
        delete.assert_not_called()

    def test_untracked_account_is_ignored(self):
        with patch.object(chatgpt2api_sync, "delete_accounts") as delete:
            self.assertIsNone(chatgpt2api_sync.handle_live_check_finished(999, {"ok": True, "access_token": _NEW_TOKEN}))
        delete.assert_not_called()

    def test_delete_requires_bearer(self):
        with patch.object(chatgpt2api_sync._cfg, "CHATGPT2API_BEARER", ""):
            self.assertEqual(chatgpt2api_sync.delete_accounts([_OLD_TOKEN])["status"], "skipped")

    def test_delete_sends_tokens_payload(self):
        with patch.object(chatgpt2api_sync._cfg, "CHATGPT2API_BEARER", "admin-token"), \
             patch.object(chatgpt2api_sync._cfg, "CHATGPT2API_ACCOUNTS_URL", "https://api.example.test/api/accounts"), \
             patch.object(chatgpt2api_sync._cfg, "CHATGPT2API_TIMEOUT", 12), \
             patch.object(chatgpt2api_sync.requests, "delete", return_value=Mock(ok=True, status_code=200, text="{}")) as req:
            result = chatgpt2api_sync.delete_accounts([_OLD_TOKEN, " ", _OLD_TOKEN])

        self.assertTrue(result["ok"])
        self.assertEqual(req.call_args.kwargs["json"], {"tokens": [_OLD_TOKEN, _OLD_TOKEN]})


class AccountsIdFilterTests(unittest.TestCase):
    def test_ids_filter_selects_only_listed_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accounts_path = root / "accounts.json"
            accounts_path.write_text(json.dumps([
                {"id": 1, "email": "a@test.com"},
                {"id": 2, "email": "b@test.com"},
                {"id": 3, "email": "c@test.com"},
            ]), encoding="utf-8")
            with patch.multiple(
                db,
                _ACCOUNTS_JSON=accounts_path,
                _LEGACY_ACCOUNTS_JSON=root / "legacy-accounts.json",
                _ACCOUNTS_TXT=root / "accounts.txt",
                _SQLITE_PATH=root / "turb.sqlite3",
                _SQLITE_READY=False,
            ):
                self.assertEqual(
                    [r["id"] for r in db.list_accounts_page(limit=20, ids=[3, 1])["items"]],
                    [3, 1],
                )
                # 空 ID 集合必须命中 0 条，而不是退化成全部账号。
                self.assertEqual(db.list_accounts_page(limit=20, ids=[])["total"], 0)
                self.assertEqual(db.list_accounts_page(limit=20)["total"], 3)


if __name__ == "__main__":
    unittest.main()
