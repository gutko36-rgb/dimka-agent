"""PostgreSQL integration tests for goals and habits proposal."""
from __future__ import annotations

import json
import os
import unittest
from decimal import Decimal
from pathlib import Path

try:
    import psycopg
except ImportError:
    psycopg = None

DATABASE_URL = os.environ.get("DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]


def json_value(value):
    return json.loads(value) if isinstance(value, str) else value


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "Requires the PostgreSQL integration CI job")
class LifestylePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute((ROOT / "tests/sql/lifestyle_schema_stub.sql").read_text(encoding="utf-8"))
            conn.execute((ROOT / "docs/sql/LIFESTYLE_AGENT_PROPOSAL.sql").read_text(encoding="utf-8"))

    def setUp(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                """
                TRUNCATE TABLE
                    public.lifestyle_habit_logs,
                    public.lifestyle_goal_updates,
                    public.lifestyle_habits,
                    public.lifestyle_goals
                RESTART IDENTITY CASCADE
                """
            )

    def create_goal(self, conn, *, owner=42, title="Goal"):
        return conn.execute(
            """
            INSERT INTO public.lifestyle_goals (telegram_id,title)
            VALUES (%s,%s)
            RETURNING id
            """,
            (owner, title),
        ).fetchone()[0]

    def create_habit(self, conn, *, owner=42, title="Walk"):
        return conn.execute(
            """
            INSERT INTO public.lifestyle_habits (telegram_id,title,target_per_week)
            VALUES (%s,%s,5)
            RETURNING id
            """,
            (owner, title),
        ).fetchone()[0]

    def progress(self, conn, owner, goal_id, value, note, update_id):
        return json_value(
            conn.execute(
                "SELECT public.record_lifestyle_goal_progress(%s,%s,%s,%s,%s)",
                (owner, goal_id, value, note, update_id),
            ).fetchone()[0]
        )

    def test_goal_progress_is_atomic_and_duplicate_update_is_idempotent(self):
        with psycopg.connect(DATABASE_URL) as conn:
            goal_id = self.create_goal(conn)
            conn.execute("SET ROLE service_role")
            first = self.progress(conn, 42, goal_id, 25, "Первый шаг", 92001)
            self.assertFalse(first["duplicate"])
            self.assertEqual(first["progress"], 25)

            repeated = self.progress(conn, 42, goal_id, 25, "Первый шаг", 92001)
            self.assertTrue(repeated["duplicate"])
            self.assertEqual(repeated["progress"], 25)

            state = conn.execute(
                "SELECT progress,status FROM public.lifestyle_goals WHERE id=%s AND telegram_id=42",
                (goal_id,),
            ).fetchone()
            history = conn.execute(
                "SELECT count(*) FROM public.lifestyle_goal_updates WHERE telegram_id=42 AND goal_id=%s",
                (goal_id,),
            ).fetchone()[0]
            self.assertEqual(state, (Decimal("25.00"), "active"))
            self.assertEqual(history, 1)

    def test_goal_completion_updates_status_and_history(self):
        with psycopg.connect(DATABASE_URL) as conn:
            goal_id = self.create_goal(conn)
            conn.execute("SET ROLE service_role")
            result = self.progress(conn, 42, goal_id, 100, "Готово", 92002)
            self.assertEqual(result["status"], "completed")
            state = conn.execute(
                "SELECT progress,status FROM public.lifestyle_goals WHERE id=%s",
                (goal_id,),
            ).fetchone()
            self.assertEqual(state, (Decimal("100.00"), "completed"))

    def test_wrong_owner_and_reused_update_id_are_rejected(self):
        with psycopg.connect(DATABASE_URL) as conn:
            goal_id = self.create_goal(conn)
            conn.commit()
            conn.execute("SET ROLE service_role")
            with self.assertRaises(psycopg.Error):
                self.progress(conn, 99, goal_id, 20, "wrong owner", 92003)
            conn.rollback()

            conn.execute("SET ROLE service_role")
            self.progress(conn, 42, goal_id, 20, "first", 92004)
            with self.assertRaises(psycopg.Error):
                self.progress(conn, 42, goal_id, 30, "different payload", 92004)

    def test_habit_logs_are_owner_scoped_and_append_only(self):
        with psycopg.connect(DATABASE_URL) as conn:
            habit_id = self.create_habit(conn)
            conn.execute("SET ROLE service_role")
            conn.execute(
                """
                INSERT INTO public.lifestyle_habit_logs
                    (telegram_id,habit_id,log_date,status,source_update_id)
                VALUES (42,%s,'2026-11-10','done',92005)
                """,
                (habit_id,),
            )
            conn.execute(
                """
                INSERT INTO public.lifestyle_habit_logs
                    (telegram_id,habit_id,log_date,status,source_update_id)
                VALUES (42,%s,'2026-11-10','skipped',92006)
                """,
                (habit_id,),
            )
            count = conn.execute(
                "SELECT count(*) FROM public.lifestyle_habit_logs WHERE telegram_id=42 AND habit_id=%s AND log_date='2026-11-10'",
                (habit_id,),
            ).fetchone()[0]
            self.assertEqual(count, 2)

            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    """
                    INSERT INTO public.lifestyle_habit_logs
                        (telegram_id,habit_id,log_date,status,source_update_id)
                    VALUES (42,%s,'2026-11-11','done',92005)
                    """,
                    (habit_id,),
                )
            conn.rollback()

            conn.execute("SET ROLE service_role")
            foreign_test_habit = self.create_habit(conn, owner=42, title="Foreign key test")
            with self.assertRaises(psycopg.Error):
                conn.execute(
                    """
                    INSERT INTO public.lifestyle_habit_logs
                        (telegram_id,habit_id,log_date,status,source_update_id)
                    VALUES (99,%s,'2026-11-10','done',92007)
                    """,
                    (foreign_test_habit,),
                )

    def test_authenticated_role_cannot_read_lifestyle_tables_or_execute_rpc(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("SET ROLE authenticated")
            with self.assertRaises(psycopg.Error):
                conn.execute("SELECT * FROM public.lifestyle_goals")
            conn.rollback()

            conn.execute("SET ROLE authenticated")
            with self.assertRaises(psycopg.Error):
                conn.execute(
                    "SELECT public.record_lifestyle_goal_progress(42,1,10,NULL,92008)"
                )


if __name__ == "__main__":
    unittest.main()
