import unittest
from datetime import datetime, timezone

from bot_result_inbox import BotResultInbox, format_result_message


class Response:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, response):
        self.response = response
        self.filters = []
        self.updated = None

    def update(self, value):
        self.updated = value
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def select(self, value):
        return self

    def execute(self):
        return self.response


class FakeClient:
    def __init__(self, messages=None, updated=None):
        self.messages = messages or []
        self.updated = updated or [{"id": "result-id"}]
        self.rpc_calls = []
        self.query = Query(Response(self.updated))

    def rpc(self, name, args):
        self.rpc_calls.append((name, args))
        return Query(Response(self.messages))

    def table(self, name):
        self.table_name = name
        return self.query


class BotResultInboxTests(unittest.TestCase):
    def test_only_primary_identity_can_be_constructed(self):
        with self.assertRaises(ValueError):
            BotResultInbox(FakeClient(), bot_id="finance")

    def test_claim_uses_restricted_primary_rpc(self):
        message = {"id": "result-id", "message_type": "result"}
        client = FakeClient(messages=[message])
        inbox = BotResultInbox(client)

        self.assertEqual(inbox.claim(), [message])
        self.assertEqual(
            client.rpc_calls,
            [("claim_bot_results_for_primary", {"p_limit": 10, "p_lease_seconds": 300})],
        )

    def test_claim_validates_limits(self):
        inbox = BotResultInbox(FakeClient())
        with self.assertRaises(ValueError):
            inbox.claim(limit=0)
        with self.assertRaises(ValueError):
            inbox.claim(lease_seconds=1)

    def test_complete_is_scoped_to_processing_result_for_dimka(self):
        client = FakeClient()
        inbox = BotResultInbox(client)

        self.assertTrue(inbox.complete("result-id"))
        self.assertEqual(client.table_name, "bot_messages")
        self.assertEqual(
            client.query.filters,
            [
                ("id", "result-id"),
                ("to_bot_id", "dimka"),
                ("message_type", "result"),
                ("status", "processing"),
            ],
        )
        self.assertIsInstance(
            datetime.fromisoformat(client.query.updated["completed_at"]),
            datetime,
        )
        self.assertIsNotNone(datetime.fromisoformat(client.query.updated["completed_at"]).tzinfo)

    def test_format_successful_finance_result(self):
        text = format_result_message({
            "message_type": "result",
            "from_bot_id": "finance",
            "payload": {"task_type": "diagnostics.echo", "ok": True, "result": {"echo": {"value": 7}}},
        })
        self.assertIn("Результат от финансового агента", text)
        self.assertIn("diagnostics.echo", text)
        self.assertIn('"value": 7', text)

    def test_rejects_non_result_message(self):
        with self.assertRaises(ValueError):
            format_result_message({"message_type": "task", "from_bot_id": "finance", "payload": {}})


if __name__ == "__main__":
    unittest.main()
