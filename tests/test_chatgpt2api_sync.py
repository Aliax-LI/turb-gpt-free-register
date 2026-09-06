# -*- coding: utf-8 -*-
import unittest
from unittest.mock import Mock, patch

from core import chatgpt2api_sync


class ChatGPT2ApiSyncTests(unittest.TestCase):
    @patch("core.chatgpt2api_sync.requests.post")
    def test_sync_posts_new_access_token(self, post):
        post.return_value = Mock(ok=True, status_code=201, text='{"ok":true}')
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
            json={"tokens": ["new-access-token"], "accounts": []},
            timeout=12,
        )

    @patch("core.chatgpt2api_sync.requests.post")
    def test_sync_is_disabled_by_default(self, post):
        with patch.object(chatgpt2api_sync._cfg, "ENABLE_CHATGPT2API_SYNC", False):
            result = chatgpt2api_sync.sync_account("new-access-token")

        self.assertEqual(result["status"], "skipped")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
