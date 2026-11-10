import unittest
from types import SimpleNamespace

from bot_identity import BotIdentity
from bot_queue import BotQueueClient
from bot_worker_service import (
    build_worker,
    create_authenticated_queue,
    verify_worker_auth_user,
)


class FakeQuery:
    def __init__(self, rows=None):
        self.rows = rows or []

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class FakeAuth:
    def __init__(self, user):
        self.user = user
        self.signed_in = None

    def sign_in_with_password(self, credentials):
        self.signed_in = credentials
        return SimpleNamespace(user=self.user)


class FakeClient:
    def __init__(self, user):
        self.auth = FakeAuth(user)


class WorkerServiceTests(unittest.TestCase):
    def test_verify_requires_trusted_app_metadata(self):
        verify_worker_auth_user(
            {"app_metadata": {"bot_id": "finance"}},
            "finance",
        )

    def test_verify_rejects_user_metadata_spoof(self):
        with self.assertRaises(RuntimeError):
            verify_worker_auth_user(
                {"user_metadata": {"bot_id": "finance"}},
                "finance",
            )

    def test_verify_rejects_wrong_bot_identity(self):
        with self.assertRaises(RuntimeError):
            verify_worker_auth_user(
                {"app_metadata": {"bot_id": "projects"}},
                "finance",
            )

    def test_create_authenticated_queue_uses_anon_key_and_checks_identity(self):
        user = {"app_metadata": {"bot_id": "finance"}}
        client = FakeClient(user)
        calls = []

        def factory(url, key):
            calls.append((url, key))
            return client

        queue, returned_client = create_authenticated_queue(
            {
                "BOT_ID": "finance",
                "BOT_ROLE": "finance",
                "BOT_DISPLAY_NAME": "Finance",
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_ANON_KEY": "publishable",
                "BOT_AUTH_EMAIL": "finance@example.com",
                "BOT_AUTH_PASSWORD": "private-password",
            },
            client_factory=factory,
        )

        self.assertEqual(calls, [("https://example.supabase.co", "publishable")])
        self.assertEqual(queue.identity.bot_id, "finance")
        self.assertIs(returned_client, client)
        self.assertEqual(
            client.auth.signed_in,
            {"email": "finance@example.com", "password": "private-password"},
        )

    def test_primary_dimka_cannot_start_as_standalone_worker(self):
        with self.assertRaises(ValueError):
            create_authenticated_queue(
                {
                    "BOT_ID": "dimka",
                    "SUPABASE_URL": "url",
                    "SUPABASE_ANON_KEY": "anon",
                    "BOT_AUTH_EMAIL": "dimka@example.com",
                    "BOT_AUTH_PASSWORD": "private-password",
                },
                client_factory=lambda *_: self.fail("client must not be created"),
            )

    def test_missing_worker_credentials_fail_before_network_access(self):
        with self.assertRaises(ValueError):
            create_authenticated_queue(
                {"BOT_ID": "finance", "SUPABASE_URL": "url"},
                client_factory=lambda *_: self.fail("client must not be created"),
            )

    def test_build_worker_registers_finance_domain_handlers(self):
        queue = BotQueueClient(
            object(),
            BotIdentity("finance", "finance", "Финансовый агент"),
        )
        worker = build_worker(queue)

        self.assertEqual(
            set(worker.handlers),
            {
                "diagnostics.echo",
                "finance.summary",
                "finance.forecast",
                "finance.budget_analysis",
                "finance.distribution",
            },
        )
        result = worker.handlers["finance.summary"]({
            "transactions": [
                {"kind": "income", "amount": 1000, "tx_date": "2026-11-01"},
                {"kind": "expense", "amount": 250, "category": "food", "tx_date": "2026-11-02"},
            ]
        })
        self.assertEqual(result["income"], "1000.00")
        self.assertEqual(result["expense"], "250.00")


if __name__ == "__main__":
    unittest.main()
