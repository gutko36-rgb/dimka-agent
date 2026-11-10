"""PostgreSQL integration tests for the finance debt and budget proposal."""
from __future__ import annotations

import json
import os
from decimal import Decimal
import unittest
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
class FinanceDebtBudgetPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute((ROOT / "tests/sql/finance_debt_schema_stub.sql").read_text(encoding="utf-8"))
            conn.execute((ROOT / "supabase/migrations/20261110120000_finance_debt_budget.sql").read_text(encoding="utf-8"))
            conn.execute("GRANT ALL ON TABLE public.finance_transactions TO service_role")
            conn.execute("GRANT USAGE, SELECT ON SEQUENCE public.finance_transactions_id_seq TO service_role")

    def setUp(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                """
                TRUNCATE TABLE
                    public.finance_debt_payments,
                    public.finance_debts,
                    public.finance_budgets,
                    public.finance_budget_weights,
                    public.finance_transactions
                RESTART IDENTITY CASCADE
                """
            )

    def create_debt(self, conn, *, owner=42, direction="owed_by_me", amount="10000", due_date="2026-12-01"):
        return conn.execute(
            """
            INSERT INTO public.finance_debts
                (telegram_id,direction,counterparty,title,amount,amount_remaining,due_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (owner, direction, "Контрагент", "Тестовый долг", amount, amount, due_date),
        ).fetchone()[0]

    def pay(self, conn, owner, debt_id, amount, update_id, payment_date="2026-11-10"):
        return json_value(
            conn.execute(
                """
                SELECT public.record_finance_debt_payment(%s,%s,%s,%s,%s)
                """,
                (owner, debt_id, amount, payment_date, update_id),
            ).fetchone()[0]
        )

    def test_partial_repayment_is_atomic_and_duplicate_update_does_not_double_charge(self):
        with psycopg.connect(DATABASE_URL) as conn:
            debt_id = self.create_debt(conn)
            conn.execute("SET ROLE service_role")

            first = self.pay(conn, 42, debt_id, "2500", 91001)
            self.assertFalse(first["duplicate"])
            self.assertEqual(Decimal(str(first["amount_remaining"])), Decimal("7500.00"))
            self.assertEqual(first["status"], "open")

            repeated = self.pay(conn, 42, debt_id, "2500", 91001)
            self.assertTrue(repeated["duplicate"])
            self.assertEqual(Decimal(str(repeated["amount_remaining"])), Decimal("7500.00"))

            counts = conn.execute(
                """
                SELECT
                    (SELECT count(*) FROM public.finance_debt_payments WHERE telegram_id=42),
                    (SELECT count(*) FROM public.finance_transactions WHERE telegram_id=42)
                """
            ).fetchone()
            self.assertEqual(counts, (1, 1))

    def test_final_repayment_closes_debt_and_records_expense(self):
        with psycopg.connect(DATABASE_URL) as conn:
            debt_id = self.create_debt(conn, amount="5000")
            conn.execute("SET ROLE service_role")
            result = self.pay(conn, 42, debt_id, "5000", 91002)
            self.assertEqual(result["status"], "paid")
            self.assertEqual(Decimal(str(result["amount_remaining"])), Decimal("0.00"))
            transaction = conn.execute(
                "SELECT kind,category,amount FROM public.finance_transactions WHERE telegram_id=42"
            ).fetchone()
            self.assertEqual(transaction, ("expense", "Погашение долга", 5000))

    def test_receivable_repayment_records_income(self):
        with psycopg.connect(DATABASE_URL) as conn:
            debt_id = self.create_debt(conn, direction="owed_to_me", amount="3000")
            conn.execute("SET ROLE service_role")
            result = self.pay(conn, 42, debt_id, "1000", 91003)
            self.assertEqual(result["status"], "open")
            transaction = conn.execute(
                "SELECT kind,category FROM public.finance_transactions WHERE telegram_id=42"
            ).fetchone()
            self.assertEqual(transaction, ("income", "Возврат долга"))

    def test_wrong_owner_and_overpayment_are_rejected_without_writes(self):
        with psycopg.connect(DATABASE_URL) as conn:
            debt_id = self.create_debt(conn)
            conn.execute("SET ROLE service_role")
            with self.assertRaises(psycopg.Error):
                self.pay(conn, 99, debt_id, "100", 91004)
            conn.rollback()

            conn.execute("SET ROLE service_role")
            with self.assertRaises(psycopg.Error):
                self.pay(conn, 42, debt_id, "10001", 91005)
            conn.rollback()

            counts = conn.execute(
                """
                SELECT
                    (SELECT count(*) FROM public.finance_debt_payments),
                    (SELECT count(*) FROM public.finance_transactions)
                """
            ).fetchone()
            self.assertEqual(counts, (0, 0))

    def test_same_update_cannot_be_reused_for_a_different_payment(self):
        with psycopg.connect(DATABASE_URL) as conn:
            debt_id = self.create_debt(conn)
            conn.execute("SET ROLE service_role")
            self.pay(conn, 42, debt_id, "1000", 91006)
            with self.assertRaises(psycopg.Error):
                self.pay(conn, 42, debt_id, "2000", 91006)

    def test_budget_and_distribution_weight_keys_are_owner_scoped(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute(
                "INSERT INTO public.finance_budgets (telegram_id,period_month,category,limit_amount) VALUES (42,'2026-11-01','Еда',20000)"
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO public.finance_budgets (telegram_id,period_month,category,limit_amount) VALUES (42,'2026-11-01','Еда',25000)"
                )
            conn.rollback()
            conn.execute(
                "INSERT INTO public.finance_budget_weights (telegram_id,category,weight) VALUES (42,'Еда',3)"
            )
            with self.assertRaises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO public.finance_budget_weights (telegram_id,category,weight) VALUES (42,'Еда',4)"
                )

    def test_authenticated_role_cannot_read_private_debt_tables_or_execute_payment_rpc(self):
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("SET ROLE authenticated")
            with self.assertRaises(psycopg.Error):
                conn.execute("SELECT * FROM public.finance_debts")
            conn.rollback()
            conn.execute("SET ROLE authenticated")
            with self.assertRaises(psycopg.Error):
                conn.execute(
                    "SELECT public.record_finance_debt_payment(42,1,1,'2026-11-10',91007)"
                )


if __name__ == "__main__":
    unittest.main()
