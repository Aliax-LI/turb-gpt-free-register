import unittest
from unittest.mock import patch

from config import proxy


class ProxyStickySessionTests(unittest.TestCase):
    def test_replaces_lsid_and_ttl_per_task(self):
        source = "socks5h://user-res-JP-Lsid-803155789-TTL-300:pass@prem.country.iprocket.io:9595"
        with patch.object(proxy, "PROXY_STICKY_SESSION_PER_TASK", True), \
             patch.object(proxy, "PROXY_STICKY_SESSION_TTL", 600), \
             patch.object(proxy, "_new_sticky_session_id", return_value="123456789"):
            self.assertEqual(
                proxy._proxy_with_task_sticky_session(source),
                "socks5h://user-res-JP-Lsid-123456789-TTL-600:pass@prem.country.iprocket.io:9595",
            )

    def test_adds_session_parameters_when_username_has_no_markers(self):
        source = "socks5h://user-res-JP:pass@prem.country.iprocket.io:9595"
        with patch.object(proxy, "PROXY_STICKY_SESSION_PER_TASK", True), \
             patch.object(proxy, "PROXY_STICKY_SESSION_TTL", 300), \
             patch.object(proxy, "_new_sticky_session_id", return_value="123456789"):
            result = proxy._proxy_with_task_sticky_session(source)
        self.assertIn("user-res-JP-Lsid-123456789-TTL-300", result)

    def test_disabled_setting_keeps_original_url(self):
        source = "socks5h://user-res-JP-Lsid-803155789-TTL-300:pass@prem.country.iprocket.io:9595"
        with patch.object(proxy, "PROXY_STICKY_SESSION_PER_TASK", False):
            self.assertEqual(proxy._proxy_with_task_sticky_session(source), source)


if __name__ == "__main__":
    unittest.main()
