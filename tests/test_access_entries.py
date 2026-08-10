import unittest

from access_entries import AccessEntryError, entry_mode, normalize_entry_url, switch_url


class AccessEntryTests(unittest.TestCase):
    def test_normalizes_valid_origins_and_rejects_non_origins(self):
        self.assertEqual(normalize_entry_url(" HTTPS://Ink.Example.com:8443/ "), "https://ink.example.com:8443")
        for invalid in ("ink.example.com", "ftp://ink.example.com", "https://ink.example.com/gallery", "https://user:pass@ink.example.com"):
            with self.assertRaises(AccessEntryError):
                normalize_entry_url(invalid)

    def test_detects_configured_mode_without_network_probing(self):
        self.assertEqual(entry_mode("http://192.168.1.2:8766", "http://192.168.1.2:8766", "https://ink.example.com"), "internal")
        self.assertEqual(entry_mode("https://ink.example.com", "http://192.168.1.2:8766", "https://ink.example.com"), "external")
        self.assertIsNone(entry_mode("http://127.0.0.1:8766", "http://192.168.1.2:8766", "https://ink.example.com"))

    def test_switch_keeps_path_and_query_without_auth_token(self):
        url = switch_url("https://ink.example.com", "/push-studio/42?from=gallery")
        self.assertEqual(url, "https://ink.example.com/push-studio/42?from=gallery")
        self.assertNotIn("token", url)
        with self.assertRaises(AccessEntryError):
            switch_url("https://ink.example.com", "//other.example.com/login")
