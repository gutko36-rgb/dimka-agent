import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot_identity import BotIdentity
from bot_queue import BotQueueClient, BotQueueValidationError


class FakeQuery:
    def __init__(self, calls):
        self.calls = calls

    def __getattr__(self, name):
        if name in {"insert", "upsert", "update", "select", "eq", "lte", "order", "limit"}:
            def method(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                return self
            return method
        raise AttributeError(name)

    def execute(self):
        self.calls.append(("execute", (), {}))
        return SimpleNamespace(data=[])


class FakeRpc:
    def __init__(self, calls, name, params):
        self.calls = calls
        self.name = name
        self.params = params

    def execute(self):
        self.calls.append(("rpc_execute", (self.name, self.params), {}))
        return SimpleNamespace(data=[])


class FakeSupabase:
    def __init__(self):
        self.calls = []

    def table(self, name):
        self.calls.append(("table", (name,), {}))
        return FakeQuery(self.calls)

    def rpc(self, name, params):
        self.calls.append(("rpc", (name, params), {}))
        return FakeRpc(self.calls, name, params)


class BotQueueClientTests(unittest.TestCase):
    def setUp(self):
        self.supabase = FakeSupabase()
        self.identity = BotIdentity("dimka", "personal_assistant", "Димка")
        self.queue = BotQueueClient(self.supabase, self.identity)

    def calls_named(self, name):
        return [call for call in self.supabase.calls if call[0] == name]

    def test_enqueue_uses_configured_sender_identity(self):
        self.queue.enqueue(
            to_bot_id="finance",
            message_type="task",
            payload={"action": "categorize"},
            idempotency_key="telegram-update-123",
        )
        upsert = self.calls_named("upsert")[0]
        insert = upsert[1][0]
        self.assertEqual(insert["from_bot_id"], "dimka")
        self.assertEqual(insert["to_bot_id"], "finance")
        self.assertEqual(insert["message_type"], "task")
        self.assertEqual(insert["payload"], {"action": "categorize"})
        self.assertEqual(insert["idempotency_key"], "telegram-update-123")
        self.assertEqual(insert["hop_count"], 0)
        self.assertEqual(upsert[2], {"on_conflict": "from_bot_id,idempotency_key", "ignore_duplicates": True})

    def test_enqueue_rejects_invalid_recipient(self):
        with self.assertRaises(BotQueueValidationError):
            self.queue.enqueue(
                to_bot_id="Finance Bot",
                message_type="task",
                payload={},
                idempotency_key="key",
            )
        self.assertEqual(self.calls_named("upsert"), [])

    def test_enqueue_rejects_non_object_payload(self):
        with self.assertRaises(BotQueueValidationError):
            self.queue.enqueue(
                to_bot_id="finance",
                message_type="task",
                payload=["not", "an", "object"],
                idempotency_key="key",
            )

    def test_enqueue_rejects_non_json_payload_and_invalid_task_id(self):
        with self.assertRaises(BotQueueValidationError):
            self.queue.enqueue(
                to_bot_id="finance",
                message_type="task",
                payload={"not_json": object()},
                idempotency_key="json-invalid",
            )
        with self.assertRaises(BotQueueValidationError):
            self.queue.enqueue(
                to_bot_id="finance",
                message_type="task",
                payload={},
                idempotency_key="uuid-invalid",
                task_id="not-a-uuid",
            )
        self.assertEqual(self.calls_named("upsert"), [])

    def test_enqueue_rejects_invalid_message_type_and_hop_count(self):
        args = dict(to_bot_id="finance", payload={}, idempotency_key="key")
        with self.assertRaises(BotQueueValidationError):
            self.queue.enqueue(message_type="unknown", **args)
        with self.assertRaises(BotQueueValidationError):
            self.queue.enqueue(message_type="task", hop_count=9, **args)

    def test_enqueue_rejects_expired_message(self):
        with self.assertRaises(BotQueueValidationError):
            self.queue.enqueue(
                to_bot_id="finance",
                message_type="task",
                payload={},
                idempotency_key="key",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )

    def test_claim_inbox_uses_atomic_rpc_for_current_bot(self):
        self.queue.claim_inbox(limit=7, lease_seconds=600)
        self.assertIn(
            ("rpc", ("claim_bot_messages", {"p_limit": 7, "p_lease_seconds": 600, "p_message_type": "task"}), {}),
            self.supabase.calls,
        )

    def test_claim_inbox_rejects_invalid_limits(self):
        for kwargs in ({"limit": 0}, {"limit": True}, {"lease_seconds": 10}, {"lease_seconds": 3601}, {"message_type": "unknown"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(BotQueueValidationError):
                    self.queue.claim_inbox(**kwargs)
        self.assertEqual(self.calls_named("rpc"), [])

    def test_fetch_inbox_is_scoped_to_current_bot(self):
        self.queue.fetch_inbox(limit=5)
        self.assertIn(("eq", ("to_bot_id", "dimka"), {}), self.supabase.calls)
        self.assertIn(("eq", ("status", "pending"), {}), self.supabase.calls)
        self.assertIn(("limit", (5,), {}), self.supabase.calls)

    def test_fetch_inbox_rejects_unreasonable_limit(self):
        with self.assertRaises(BotQueueValidationError):
            self.queue.fetch_inbox(limit=101)
        self.assertEqual(self.calls_named("select"), [])

    def test_update_status_is_scoped_to_current_bot(self):
        self.queue.update_status(message_id="message-1", status="completed")
        update = self.calls_named("update")[0][1][0]
        self.assertEqual(update["status"], "completed")
        self.assertIn("completed_at", update)
        self.assertIn(("eq", ("id", "message-1"), {}), self.supabase.calls)
        self.assertIn(("eq", ("to_bot_id", "dimka"), {}), self.supabase.calls)
        self.assertIn(("eq", ("status", "processing"), {}), self.supabase.calls)

    def test_update_status_rejects_unknown_status(self):
        with self.assertRaises(BotQueueValidationError):
            self.queue.update_status(message_id="message-1", status="deleted")
        self.assertEqual(self.calls_named("update"), [])

    def test_rejects_naive_datetimes(self):
        with self.assertRaises(BotQueueValidationError):
            self.queue.enqueue(
                to_bot_id="finance",
                message_type="task",
                payload={},
                idempotency_key="key",
                available_at=datetime.now(),
            )


if __name__ == "__main__":
    unittest.main()
