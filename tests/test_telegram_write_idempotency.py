"""Unit tests for Telegram update idempotency compatibility helpers."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test-token")
os.environ.setdefault("TELEGRAM_USER_ID", "1")
os.environ.setdefault("GROQ_API_KEY", "gsk_test-placeholder")
os.environ.setdefault("GROQ_MODEL", "llama-3.3-70b-versatile")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-placeholder-key")

import bot  # noqa: E402


class FakeAPIError(Exception):
    def __init__(self, code):
        super().__init__(f"fake API error {code}")
        self.code = code


class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.operation = None
        self.payload = None
        self.options = {}
        self.filters = []

    def upsert(self, payload, **kwargs):
        self.operation = "upsert"
        self.payload = payload
        self.options = kwargs
        self.db.calls.append(("upsert", self.table, payload, kwargs))
        if self.db.upsert_error:
            raise self.db.upsert_error
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload
        self.db.calls.append(("insert", self.table, payload, {}))
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        self.db.calls.append(("update", self.table, payload, {}))
        return self

    def select(self, *args, **kwargs):
        self.operation = "select"
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        self.db.calls.append(("execute", self.table, self.operation, self.filters))
        return SimpleNamespace(data=self.db.result_data.get(self.table, []), count=0)


class FakeDatabase:
    def __init__(self, upsert_error=None, result_data=None):
        self.upsert_error = upsert_error
        self.result_data = result_data or {}
        self.calls = []

    def table(self, name):
        return FakeQuery(self, name)


async def direct_db_call(method, *args, **kwargs):
    return method(*args, **kwargs)


class TelegramWriteIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = FakeDatabase()
        self.db_patch = patch.object(bot, "supabase", self.db)
        self.db_patch.start()
        self.call_patch = patch.object(bot, "db_call", direct_db_call)
        self.call_patch.start()

    async def asyncTearDown(self):
        self.call_patch.stop()
        self.db_patch.stop()

    def test_missing_schema_detection_is_narrow(self):
        self.assertTrue(bot.is_missing_idempotency_schema_error(FakeAPIError("PGRST204")))
        self.assertTrue(bot.is_missing_idempotency_schema_error(FakeAPIError("42P10")))
        self.assertFalse(bot.is_missing_idempotency_schema_error(FakeAPIError("23505")))
        self.assertFalse(bot.is_missing_idempotency_schema_error(RuntimeError("network timeout")))

    async def test_idempotent_upsert_falls_back_only_for_missing_schema(self):
        self.db.upsert_error = FakeAPIError("PGRST204")
        result = await bot.idempotent_upsert(
            "finance_payments",
            {"telegram_id": 42, "source_update_id": 7, "title": "Rent"},
            "telegram_id,source_update_id",
            {"telegram_id": 42, "title": "Rent"},
        )
        self.assertEqual(self.db.calls[0][0], "upsert")
        self.assertEqual(self.db.calls[1][0], "insert")
        self.assertNotIn("source_update_id", self.db.calls[1][2])
        self.assertEqual(result.data, [])

    async def test_conversation_save_uses_update_id_and_message_index(self):
        await bot.save_message(
            42,
            "assistant",
            "Answer",
            source_update_id=9001,
            source_item_index=1,
        )
        operation = self.db.calls[0]
        self.assertEqual(operation[0], "upsert")
        self.assertEqual(operation[1], "conversations")
        self.assertEqual(operation[2]["telegram_id"], 42)
        self.assertEqual(operation[2]["source_update_id"], 9001)
        self.assertEqual(operation[2]["source_item_index"], 1)
        self.assertTrue(operation[3]["ignore_duplicates"])

    async def test_survey_creation_returns_none_for_duplicate_daily_survey(self):
        self.db.result_data["daily_surveys"] = []
        created = await bot.create_survey(42, "morning", "Question", "2026-11-10")
        self.assertIsNone(created)
        self.assertEqual(self.db.calls[0][0], "upsert")
        self.assertEqual(self.db.calls[0][3]["on_conflict"], "telegram_id,survey_date,kind")

    async def test_duplicate_survey_answer_update_is_ignored(self):
        await bot.record_survey_answer(
            42,
            {"id": 3, "answer": "already recorded", "last_answer_update_id": 9002},
            "already recorded",
            "summary",
            source_update_id=9002,
        )
        self.assertEqual(self.db.calls, [])


if __name__ == "__main__":
    unittest.main()
