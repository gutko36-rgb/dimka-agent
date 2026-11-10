"""PostgreSQL tests for additive Telegram write idempotency indexes."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

try:
    import psycopg
except ImportError:  # The standard Python test job need not install dev dependencies.
    psycopg = None

DATABASE_URL = os.environ.get("DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "Requires the PostgreSQL integration CI job")
class TelegramWriteIdempotencyPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute((ROOT / "tests/sql/telegram_idempotency_schema_stub.sql").read_text(encoding="utf-8"))
            conn.execute((ROOT / "docs/sql/TELEGRAM_WRITE_IDEMPOTENCY_PROPOSAL.sql").read_text(encoding="utf-8"))

    def setUp(self):
        tables = (
            "conversations", "finance_payments", "finance_purchases", "agent_projects",
            "agent_project_items", "daily_surveys", "analytics_reports", "daily_logs",
            "finance_transactions", "finance_pending_actions", "agent_project_assets",
        )
        with psycopg.connect(DATABASE_URL) as conn:
            for table in tables:
                conn.execute(f"DELETE FROM public.{table}")

    def test_conversation_update_role_keys_are_unique_and_nulls_remain_allowed(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                """
                INSERT INTO public.conversations (telegram_id, role, content, source_update_id, source_item_index)
                VALUES (42, 'user', 'hello', 9001, 0)
                ON CONFLICT (telegram_id, source_update_id, source_item_index) DO NOTHING
                """
            )
            conn.execute(
                """
                INSERT INTO public.conversations (telegram_id, role, content, source_update_id, source_item_index)
                VALUES (42, 'user', 'duplicate', 9001, 0)
                ON CONFLICT (telegram_id, source_update_id, source_item_index) DO NOTHING
                """
            )
            conn.execute(
                """
                INSERT INTO public.conversations (telegram_id, role, content, source_update_id, source_item_index)
                VALUES (42, 'assistant', 'answer', 9001, 1)
                """
            )
            conn.execute(
                "INSERT INTO public.conversations (telegram_id, role, content) VALUES (42, 'user', 'legacy-1'), (42, 'user', 'legacy-2')"
            )
            count = conn.execute(
                "SELECT count(*) FROM public.conversations WHERE telegram_id=42 AND source_update_id=9001"
            ).fetchone()[0]
            legacy_count = conn.execute(
                "SELECT count(*) FROM public.conversations WHERE telegram_id=42 AND source_update_id IS NULL"
            ).fetchone()[0]
            self.assertEqual(count, 2)
            self.assertEqual(legacy_count, 2)

    def test_finance_payments_and_purchases_reject_duplicate_updates(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                "INSERT INTO public.finance_payments (telegram_id,title,amount,day_of_month,source_update_id) VALUES (42,'Rent',100,5,9002)"
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO public.finance_payments (telegram_id,title,amount,day_of_month,source_update_id) VALUES (42,'Rent',100,5,9002)"
                )
            conn.rollback()
            conn.execute(
                "INSERT INTO public.finance_purchases (telegram_id,title,price,source_update_id) VALUES (42,'Laptop',1000,9003)"
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO public.finance_purchases (telegram_id,title,price,source_update_id) VALUES (42,'Laptop',1000,9003)"
                )

    def test_project_and_project_item_updates_are_idempotent(self):
        with psycopg.connect(DATABASE_URL) as conn:
            project_id = conn.execute(
                "INSERT INTO public.agent_projects (telegram_id,title,source_update_id) VALUES (42,'Project',9004) RETURNING id"
            ).fetchone()[0]
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO public.agent_projects (telegram_id,title,source_update_id) VALUES (42,'Project',9004)"
                )
            conn.rollback()
            conn.execute(
                """
                INSERT INTO public.agent_project_items
                    (telegram_id,project_id,kind,content,source_update_id,source_item_index)
                VALUES (42,%s,'stage','Stage A',9005,0), (42,%s,'stage','Stage B',9005,1)
                """,
                (project_id, project_id),
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    """
                    INSERT INTO public.agent_project_items
                        (telegram_id,project_id,kind,content,source_update_id,source_item_index)
                    VALUES (42,%s,'stage','Duplicate Stage A',9005,0)
                    """,
                    (project_id,),
                )

    def test_surveys_reports_and_daily_logs_have_natural_unique_keys(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                "INSERT INTO public.daily_surveys (telegram_id,survey_date,kind,question) VALUES (42,'2026-11-10','morning','How are you?')"
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO public.daily_surveys (telegram_id,survey_date,kind,question) VALUES (42,'2026-11-10','morning','Duplicate?')"
                )
            conn.rollback()
            conn.execute(
                "INSERT INTO public.analytics_reports (telegram_id,kind,period_start,period_end,content) VALUES (42,'weekly','2026-11-02','2026-11-08','report')"
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO public.analytics_reports (telegram_id,kind,period_start,period_end,content) VALUES (42,'weekly','2026-11-02','2026-11-08','duplicate')"
                )
            conn.rollback()
            conn.execute(
                "INSERT INTO public.daily_logs (telegram_id,log_date,checkin_type) VALUES (42,'2026-11-10','evening')"
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO public.daily_logs (telegram_id,log_date,checkin_type) VALUES (42,'2026-11-10','evening')"
                )

    def test_existing_transaction_pending_action_and_asset_keys_remain_unique(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                "INSERT INTO public.finance_transactions (telegram_id,source_update_id,source_item_index,kind,amount,category,tx_date) VALUES (42,9006,0,'expense',10,'food','2026-11-10')"
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO public.finance_transactions (telegram_id,source_update_id,source_item_index,kind,amount,category,tx_date) VALUES (42,9006,0,'expense',10,'food','2026-11-10')"
                )
            conn.rollback()
            conn.execute(
                "INSERT INTO public.finance_pending_actions (telegram_id,source_update_id,payload) VALUES (42,9007,'{}')"
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO public.finance_pending_actions (telegram_id,source_update_id,payload) VALUES (42,9007,'{}')"
                )
            conn.rollback()
            conn.execute(
                "INSERT INTO public.agent_project_assets (telegram_id,project_id,telegram_file_unique_id) VALUES (42,1,'file-1')"
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO public.agent_project_assets (telegram_id,project_id,telegram_file_unique_id) VALUES (42,1,'file-1')"
                )


if __name__ == "__main__":
    unittest.main()
