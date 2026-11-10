"""Tests for the isolated-worker smoke-test configuration loader."""
import unittest

from scripts.smoke_test_worker import admin_credentials


class SmokeTestWorkerConfigTests(unittest.TestCase):
    def test_uses_existing_server_side_key_from_project_env(self):
        url, key = admin_credentials({
            "SUPABASE_URL": " https://project.supabase.co ",
            "SUPABASE_KEY": " server-secret ",
        })
        self.assertEqual(url, "https://project.supabase.co")
        self.assertEqual(key, "server-secret")

    def test_prefers_explicit_service_role_key(self):
        _url, key = admin_credentials({
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_KEY": "legacy-key",
            "SUPABASE_SERVICE_ROLE_KEY": "service-role-key",
        })
        self.assertEqual(key, "service-role-key")


if __name__ == "__main__":
    unittest.main()
