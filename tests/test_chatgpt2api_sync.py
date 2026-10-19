# -*- coding: utf-8 -*-
import unittest
from unittest.mock import Mock, patch

from core import chatgpt2api_sync


class ChatGPT2ApiSyncTests(unittest.TestCase):
    @patch("core.chatgpt2api_sync.requests.post")
    def test_sync_posts_new_access_token(self, post):
        post.return_value = Mock(ok=True, status_code=201, text='{"added":1,"synced":1}')
        post.return_value.json.return_value = {"added": 1, "skipped": 0, "synced": 1, "errors": []}
        with patch.object(chatgpt2api_sync._cfg, "ENABLE_CHATGPT2API_SYNC", True), patch.object(
            chatgpt2api_sync._cfg, "CHATGPT2API_ACCOUNTS_URL", "https://api.example.test/api/accounts"
        ), patch.object(chatgpt2api_sync._cfg, "CHATGPT2API_BEARER", "admin-token"), patch.object(
            chatgpt2api_sync._cfg, "CHATGPT2API_TIMEOUT", 12
        ):
            result = chatgpt2api_sync.sync_account("new-access-token")

        self.assertTrue(result["ok"])
        post.assert_called_once_with(
            "https://api.example.test/api/accounts",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Authorization": "Bearer admin-token",
                "Content-Type": "application/json",
                "User-Agent": chatgpt2api_sync.USER_AGENT,
            },
            json={
                "tokens": ["new-access-token"],
                "accounts": [{
                    "access_token": "new-access-token",
                    "source_type": "web",
                    "status": "正常",
                }],
                "sync_after_import": True,
                "restore": False,
                "return_items": False,
            },
            timeout=120,
        )

    @patch("core.chatgpt2api_sync.requests.post")
    def test_sync_posts_refresh_token_when_present(self, post):
        post.return_value = Mock(ok=True, status_code=200, text="{}")
        post.return_value.json.return_value = {"added": 1, "skipped": 0, "synced": 1, "errors": []}
        with patch.object(chatgpt2api_sync._cfg, "ENABLE_CHATGPT2API_SYNC", True), patch.object(
            chatgpt2api_sync._cfg, "CHATGPT2API_BEARER", "admin-token"
        ), patch.object(chatgpt2api_sync._cfg, "CHATGPT2API_ACCOUNTS_URL", "https://api.example.test/api/accounts"):
            result = chatgpt2api_sync.sync_account("new-access-token", refresh_token="rt-1")

        self.assertTrue(result["ok"])
        self.assertTrue(result["has_refresh_token"])
        self.assertEqual(
            post.call_args.kwargs["json"]["accounts"][0]["refresh_token"],
            "rt-1",
        )

    @patch("core.chatgpt2api_sync.requests.post")
    def test_sync_reports_remote_sync_failure(self, post):
        post.return_value = Mock(ok=True, status_code=200, text="{}")
        post.return_value.json.return_value = {
            "added": 1,
            "skipped": 0,
            "synced": 0,
            "errors": [{"message": "remote check failed"}],
        }
        with patch.object(chatgpt2api_sync._cfg, "ENABLE_CHATGPT2API_SYNC", True), patch.object(
            chatgpt2api_sync._cfg, "CHATGPT2API_BEARER", "admin-token"
        ):
            result = chatgpt2api_sync.sync_account("new-access-token")

        self.assertFalse(result["ok"])
        self.assertIn("remote check failed", result["message"])

    @patch("core.chatgpt2api_sync.requests.post")
    def test_sync_is_disabled_by_default(self, post):
        with patch.object(chatgpt2api_sync._cfg, "ENABLE_CHATGPT2API_SYNC", False):
            result = chatgpt2api_sync.sync_account("new-access-token")

        self.assertEqual(result["status"], "skipped")
        post.assert_not_called()

    @patch("core.chatgpt2api_sync.requests.get")
    def test_list_follows_pages(self, get):
        first = Mock(ok=True, status_code=200)
        first.json.return_value = {
            "items": [{"id": "a", "email": "a@test.com"}],
            "total": 2,
            "page": 1,
            "page_size": 1,
        }
        second = Mock(ok=True, status_code=200)
        second.json.return_value = {
            "items": [{"id": "b", "email": "b@test.com"}],
            "total": 2,
            "page": 2,
            "page_size": 1,
        }
        get.side_effect = [first, second]
        with patch.object(chatgpt2api_sync._cfg, "CHATGPT2API_BEARER", "admin-token"), patch.object(
            chatgpt2api_sync._cfg, "CHATGPT2API_ACCOUNTS_URL", "https://api.example.test/api/accounts"
        ), patch.object(chatgpt2api_sync._cfg, "CHATGPT2API_TIMEOUT", 12):
            result = chatgpt2api_sync.list_remote_accounts()

        self.assertTrue(result["ok"])
        self.assertEqual([item["id"] for item in result["items"]], ["a", "b"])
        self.assertEqual(get.call_args_list[1].kwargs["params"]["page"], 2)


if __name__ == "__main__":
    unittest.main()
