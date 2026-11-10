"""PostgreSQL integration tests for foreign-key covering indexes."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

try:
    import psycopg
except ImportError:
    psycopg = None

DATABASE_URL = os.environ.get("DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]

EXPECTED_INDEXES = {
    "agent_project_assets_project_id_idx",
    "analytics_reports_telegram_id_idx",
    "bot_messages_parent_message_id_idx",
    "daily_surveys_telegram_id_idx",
    "finance_debt_payments_debt_owner_idx",
    "finance_debt_payments_transaction_owner_idx",
    "lifestyle_goal_updates_goal_owner_idx",
    "lifestyle_habit_logs_habit_owner_idx",
}


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "Requires the PostgreSQL integration CI job")
class ForeignKeyIndexesPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute((ROOT / "tests/sql/foreign_key_index_schema_stub.sql").read_text(encoding="utf-8"))
            conn.execute((ROOT / "supabase/migrations/20261110140000_foreign_key_indexes.sql").read_text(encoding="utf-8"))

    def test_every_foreign_key_index_exists(self):
        with psycopg.connect(DATABASE_URL) as conn:
            rows = conn.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname = ANY(%s)
                """,
                (list(EXPECTED_INDEXES),),
            ).fetchall()
        self.assertEqual({row[0] for row in rows}, EXPECTED_INDEXES)


if __name__ == "__main__":
    unittest.main()
