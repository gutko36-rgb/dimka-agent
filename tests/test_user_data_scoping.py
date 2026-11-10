"""Regression tests for owner-scoped reads and writes in the main Telegram bot."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

# bot.py validates its runtime configuration at import time. These harmless
# placeholders allow the module to be imported without starting Telegram or
# making any external requests.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:test-token")
os.environ.setdefault("TELEGRAM_USER_ID", "1")
os.environ.setdefault("GROQ_API_KEY", "gsk_test-placeholder")
os.environ.setdefault("GROQ_MODEL", "llama-3.3-70b-versatile")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-placeholder-key")

import bot  # noqa: E402


class FakeQuery:
    def __init__(self, database, table):
        self.database = database
        self.table_name = table
        self.filters = []
        self.operation = None
        self.payload = None

    def select(self, *args, **kwargs):
        self.operation = "select"
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def upsert(self, payload, **kwargs):
        self.operation = "upsert"
        self.payload = payload
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def execute(self):
        self.database.queries.append(self)
        data = [{"id": 1}] if self.operation == "insert" and self.table_name == "agent_project_items" else []
        return SimpleNamespace(data=data, count=0)


class FakeDatabase:
    def __init__(self):
        self.queries = []

    def table(self, name):
        return FakeQuery(self, name)


async def direct_db_call(callable_query):
    return callable_query()


class UserDataScopingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = FakeDatabase()
        self.supabase_patch = patch.object(bot, "supabase", self.database)
        self.supabase_patch.start()
        self.db_call_patch = patch.object(bot, "db_call", direct_db_call)
        self.db_call_patch.start()

    async def asyncTearDown(self):
        self.db_call_patch.stop()
        self.supabase_patch.stop()

    def assert_every_query_is_owner_scoped(self, telegram_id):
        self.assertTrue(self.database.queries)
        for query in self.database.queries:
            with self.subTest(table=query.table_name, operation=query.operation):
                self.assertIn(("telegram_id", telegram_id), query.filters)

    async def test_stats_only_read_the_current_owners_rows(self):
        await bot.build_stats_text(telegram_id=42)
        self.assert_every_query_is_owner_scoped(42)

    async def test_backup_only_exports_the_current_owners_rows(self):
        await bot.make_backup_file(telegram_id=42)
        self.assert_every_query_is_owner_scoped(42)

    async def test_survey_answer_update_checks_both_record_and_owner(self):
        await bot.record_survey_answer(
            42,
            {"id": 17, "answer": "previous"},
            "new answer",
            "summary",
        )
        query = self.database.queries[-1]
        self.assertEqual(query.table_name, "daily_surveys")
        self.assertIn(("id", 17), query.filters)
        self.assertIn(("telegram_id", 42), query.filters)

    async def test_project_item_insert_refuses_project_not_owned_by_user(self):
        async def no_project(_user_id, _project_id):
            return None

        with patch.object(bot, "pj_get", no_project):
            with self.assertRaises(ValueError):
                await bot.pj_add_item(42, 999, "task", "not mine", None)

        self.assertFalse(any(q.operation == "insert" for q in self.database.queries))

    async def test_project_item_insert_rechecks_owner_before_write(self):
        async def owned_project(user_id, project_id):
            self.assertEqual((user_id, project_id), (42, 3))
            return {"id": 3, "telegram_id": 42}

        with patch.object(bot, "pj_get", owned_project):
            await bot.pj_add_item(42, 3, "task", "owned task", None)

        insert = next(q for q in self.database.queries if q.operation == "insert")
        self.assertEqual(insert.payload["telegram_id"], 42)
        self.assertEqual(insert.payload["project_id"], 3)


if __name__ == "__main__":
    unittest.main()
