"""Real PostgreSQL tests for the queue RLS policies and atomic claim RPC.

The CI job provisions a disposable PostgreSQL 17 service and applies the
reviewed SQL proposal. These tests never connect to the user's Supabase project.
"""
from __future__ import annotations

import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import psycopg
except ImportError:  # Local unit-test environments need not install dev dependencies.
    psycopg = None


DATABASE_URL = os.environ.get("DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(DATABASE_URL and psycopg is not None, "Requires the PostgreSQL CI job and psycopg dev dependency")
class BotQueuePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute((ROOT / "tests/sql/supabase_auth_stub.sql").read_text(encoding="utf-8"))
            conn.execute((ROOT / "docs/sql/MULTI_BOT_QUEUE_PROPOSAL.sql").read_text(encoding="utf-8"))
            conn.execute(
                """
                INSERT INTO public.bot_registry (bot_id, role, display_name)
                VALUES
                    ('finance', 'finance', 'Finance test bot'),
                    ('projects', 'projects', 'Projects test bot')
                ON CONFLICT (bot_id) DO UPDATE
                    SET is_active = true
                """
            )

    def setUp(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("DELETE FROM public.bot_messages")

    def bot_connection(self, bot_id: str):
        conn = psycopg.connect(DATABASE_URL)
        conn.execute("SET ROLE authenticated")
        conn.execute(
            "SELECT set_config('request.jwt.claims', %s, false)",
            (json.dumps({"app_metadata": {"bot_id": bot_id}}),),
        )
        return conn

    def enqueue_as_dimka(self, key: str) -> str:
        conn = self.bot_connection("dimka")
        try:
            row = conn.execute(
                """
                INSERT INTO public.bot_messages
                    (task_id, from_bot_id, to_bot_id, message_type, payload, idempotency_key)
                VALUES
                    (gen_random_uuid(), 'dimka', 'finance', 'task', '{}'::jsonb, %s)
                RETURNING id
                """,
                (key,),
            ).fetchone()
            conn.commit()
            return str(row[0])
        finally:
            conn.close()

    def test_recipient_only_can_read_and_claim_message(self):
        message_id = self.enqueue_as_dimka("recipient-isolation")
        conn = self.bot_connection("projects")
        try:
            rows = conn.execute(
                "SELECT id FROM public.bot_messages WHERE id = %s",
                (message_id,),
            ).fetchall()
            self.assertEqual(rows, [])
            conn.commit()
        finally:
            conn.close()

        conn = self.bot_connection("finance")
        try:
            claimed = conn.execute(
                "SELECT id, status, attempt_count FROM public.claim_bot_messages(1, 300)"
            ).fetchall()
            self.assertEqual(len(claimed), 1)
            self.assertEqual(str(claimed[0][0]), message_id)
            self.assertEqual(claimed[0][1], "processing")
            self.assertEqual(claimed[0][2], 1)
            conn.commit()
        finally:
            conn.close()

    def test_claim_ignores_non_task_messages(self):
        conn = self.bot_connection("dimka")
        try:
            conn.execute(
                """
                INSERT INTO public.bot_messages
                    (task_id, from_bot_id, to_bot_id, message_type, payload, idempotency_key)
                VALUES
                    (gen_random_uuid(), 'dimka', 'finance', 'result', '{}'::jsonb, 'result-not-a-task')
                """
            )
            conn.commit()
        finally:
            conn.close()

        conn = self.bot_connection("finance")
        try:
            rows = conn.execute(
                "SELECT id FROM public.claim_bot_messages(10, 300)"
            ).fetchall()
            self.assertEqual(rows, [])
            result_rows = conn.execute(
                "SELECT id FROM public.claim_bot_messages(10, 300, 'result')"
            ).fetchall()
            self.assertEqual(len(result_rows), 1)
            conn.commit()
        finally:
            conn.close()

    def test_future_available_at_is_not_claimed_until_due(self):
        conn = self.bot_connection("dimka")
        try:
            row = conn.execute(
                """
                INSERT INTO public.bot_messages
                    (task_id, from_bot_id, to_bot_id, message_type, payload,
                     idempotency_key, available_at)
                VALUES
                    (gen_random_uuid(), 'dimka', 'finance', 'task', '{}'::jsonb,
                     'scheduled-message', now() + interval '1 hour')
                RETURNING id
                """
            ).fetchone()
            message_id = str(row[0])
            conn.commit()
        finally:
            conn.close()

        conn = self.bot_connection("finance")
        try:
            rows = conn.execute(
                "SELECT id FROM public.claim_bot_messages(10, 300)"
            ).fetchall()
            self.assertEqual(rows, [])
            conn.commit()
        finally:
            conn.close()

        with psycopg.connect(DATABASE_URL) as admin:
            admin.execute(
                "UPDATE public.bot_messages SET available_at = now() - interval '1 second' WHERE id = %s",
                (message_id,),
            )

        conn = self.bot_connection("finance")
        try:
            rows = conn.execute(
                "SELECT id FROM public.claim_bot_messages(10, 300)"
            ).fetchall()
            self.assertEqual([str(row[0]) for row in rows], [message_id])
            conn.commit()
        finally:
            conn.close()

    def test_expired_pending_message_is_marked_failed(self):
        conn = self.bot_connection("dimka")
        try:
            row = conn.execute(
                """
                INSERT INTO public.bot_messages
                    (task_id, from_bot_id, to_bot_id, message_type, payload,
                     idempotency_key, created_at, expires_at)
                VALUES
                    (gen_random_uuid(), 'dimka', 'finance', 'task', '{}'::jsonb,
                     'expired-before-claim', now() - interval '2 minutes',
                     now() - interval '1 minute')
                RETURNING id
                """
            ).fetchone()
            message_id = str(row[0])
            conn.commit()
        finally:
            conn.close()

        conn = self.bot_connection("finance")
        try:
            rows = conn.execute(
                "SELECT id FROM public.claim_bot_messages(10, 300)"
            ).fetchall()
            self.assertEqual(rows, [])
            conn.commit()
        finally:
            conn.close()

        with psycopg.connect(DATABASE_URL) as admin:
            row = admin.execute(
                "SELECT status, last_error, completed_at FROM public.bot_messages WHERE id = %s",
                (message_id,),
            ).fetchone()
            self.assertEqual(row[0], "failed")
            self.assertEqual(row[1], "Message expired before processing")
            self.assertIsNotNone(row[2])

    def test_message_expiring_during_lease_is_not_retried(self):
        message_id = self.enqueue_as_dimka("expires-during-lease")
        conn = self.bot_connection("finance")
        try:
            first = conn.execute(
                "SELECT id FROM public.claim_bot_messages(1, 30)"
            ).fetchone()
            self.assertEqual(str(first[0]), message_id)
            conn.commit()
        finally:
            conn.close()

        with psycopg.connect(DATABASE_URL) as admin:
            admin.execute(
                """
                UPDATE public.bot_messages
                   SET created_at = now() - interval '2 minutes',
                       expires_at = now() - interval '1 minute',
                       claimed_at = now() - interval '2 minutes'
                 WHERE id = %s
                """,
                (message_id,),
            )

        conn = self.bot_connection("finance")
        try:
            rows = conn.execute(
                "SELECT id FROM public.claim_bot_messages(1, 30)"
            ).fetchall()
            self.assertEqual(rows, [])
            conn.commit()
        finally:
            conn.close()

        with psycopg.connect(DATABASE_URL) as admin:
            row = admin.execute(
                "SELECT status, last_error FROM public.bot_messages WHERE id = %s",
                (message_id,),
            ).fetchone()
            self.assertEqual(row[0], "failed")
            self.assertEqual(row[1], "Message expired before processing")

    def test_anonymous_role_cannot_execute_worker_rpc(self):
        conn = psycopg.connect(DATABASE_URL)
        try:
            conn.execute("SET ROLE anon")
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT * FROM public.claim_bot_messages(1, 300)")
            conn.rollback()
        finally:
            conn.close()

    def test_sender_cannot_spoof_another_bot_identity(self):
        conn = self.bot_connection("dimka")
        try:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    """
                    INSERT INTO public.bot_messages
                        (task_id, from_bot_id, to_bot_id, message_type, payload, idempotency_key)
                    VALUES
                        (gen_random_uuid(), 'finance', 'projects', 'task', '{}'::jsonb, 'spoofed-sender')
                    """
                )
            conn.rollback()
        finally:
            conn.close()

    def test_concurrent_workers_claim_distinct_messages(self):
        self.enqueue_as_dimka("concurrent-1")
        self.enqueue_as_dimka("concurrent-2")
        barrier = threading.Barrier(2)

        def claim_one():
            conn = self.bot_connection("finance")
            try:
                barrier.wait(timeout=10)
                rows = conn.execute(
                    "SELECT id FROM public.claim_bot_messages(1, 300)"
                ).fetchall()
                conn.commit()
                return [str(row[0]) for row in rows]
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim_one(), range(2)))

        claimed_ids = [item for batch in results for item in batch]
        self.assertEqual(len(claimed_ids), 2)
        self.assertEqual(len(set(claimed_ids)), 2)

    def test_expired_lease_is_retried_and_attempt_limit_fails_message(self):
        message_id = self.enqueue_as_dimka("lease-retry")
        conn = self.bot_connection("finance")
        try:
            first = conn.execute(
                "SELECT id, attempt_count FROM public.claim_bot_messages(1, 30)"
            ).fetchone()
            self.assertEqual(str(first[0]), message_id)
            self.assertEqual(first[1], 1)
            conn.commit()
        finally:
            conn.close()

        with psycopg.connect(DATABASE_URL) as admin:
            admin.execute(
                """
                UPDATE public.bot_messages
                   SET claimed_at = now() - interval '2 minutes',
                       max_attempts = 2
                 WHERE id = %s
                """,
                (message_id,),
            )

        conn = self.bot_connection("finance")
        try:
            second = conn.execute(
                "SELECT id, attempt_count FROM public.claim_bot_messages(1, 30)"
            ).fetchone()
            self.assertEqual(str(second[0]), message_id)
            self.assertEqual(second[1], 2)
            conn.commit()
        finally:
            conn.close()

        with psycopg.connect(DATABASE_URL) as admin:
            admin.execute(
                """
                UPDATE public.bot_messages
                   SET claimed_at = now() - interval '2 minutes'
                 WHERE id = %s
                """,
                (message_id,),
            )

        conn = self.bot_connection("finance")
        try:
            rows = conn.execute(
                "SELECT id FROM public.claim_bot_messages(1, 30)"
            ).fetchall()
            self.assertEqual(rows, [])
            conn.commit()
        finally:
            conn.close()

        with psycopg.connect(DATABASE_URL) as admin:
            status = admin.execute(
                "SELECT status FROM public.bot_messages WHERE id = %s",
                (message_id,),
            ).fetchone()[0]
            self.assertEqual(status, "failed")


if __name__ == "__main__":
    unittest.main()
