import unittest
from datetime import date
from types import SimpleNamespace

from finance_debt import FinanceDebtStore


async def direct_db_call(method):
    return method()


class FakeQuery:
    def __init__(self, client, name, is_rpc=False):
        self.client = client
        self.name = name
        self.is_rpc = is_rpc
        self.operation = "select"
        self.payload = None
        self.options = {}
        self.filters = []
        self.order_by = []

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, key, **kwargs):
        self.order_by.append((key, kwargs))
        return self

    def limit(self, _value):
        return self

    def upsert(self, payload, **kwargs):
        self.operation = "upsert"
        self.payload = payload
        self.options = kwargs
        return self

    def execute(self):
        self.client.calls.append(self)
        key = (self.name, self.operation)
        data = self.client.results.get(key, [])
        return SimpleNamespace(data=data)


class FakeClient:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, name, params):
        query = FakeQuery(self, name, is_rpc=True)
        query.operation = "rpc"
        query.payload = params
        self.results[(name, "rpc")] = params if isinstance(self.results.get((name, "rpc")), dict) else self.results.get((name, "rpc"), {})
        return query


class FinanceDebtStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeClient()
        self.store = FinanceDebtStore(self.client, direct_db_call)

    async def test_list_debts_always_filters_owner(self):
        self.client.results[("finance_debts", "select")] = [{"id": 3}]
        rows = await self.store.list_debts(42)
        self.assertEqual(rows, [{"id": 3}])
        query = self.client.calls[-1]
        self.assertIn(("telegram_id", 42), query.filters)
        self.assertIn(("status", "open"), query.filters)

    async def test_create_debt_uses_owner_update_key_and_remaining_amount(self):
        expected = {"id": 7, "telegram_id": 42, "direction": "owed_by_me", "counterparty": "Bank", "title": "Loan", "amount": "10000.00", "due_date": "2026-12-01"}
        self.client.results[("finance_debts", "upsert")] = [expected]
        row = await self.store.create_debt(
            42,
            direction="owed_by_me",
            counterparty="Bank",
            title="Loan",
            amount="10000",
            due_date=date(2026, 12, 1),
            source_update_id=9001,
        )
        self.assertEqual(row["id"], 7)
        query = self.client.calls[-1]
        self.assertEqual(query.options["on_conflict"], "telegram_id,source_update_id")
        self.assertTrue(query.options["ignore_duplicates"])
        self.assertEqual(query.payload["telegram_id"], 42)
        self.assertEqual(query.payload["source_update_id"], 9001)
        self.assertEqual(query.payload["amount_remaining"], "10000.00")

    async def test_duplicate_debt_update_returns_existing_record(self):
        self.client.results[("finance_debts", "upsert")] = []
        self.client.results[("finance_debts", "select")] = [{
            "id": 7,
            "direction": "owed_to_me",
            "counterparty": "Alex",
            "title": "Loan",
            "amount": "5000.00",
            "due_date": None,
        }]
        row = await self.store.create_debt(
            42,
            direction="owed_to_me",
            counterparty="Alex",
            title="Loan",
            amount=5000,
            due_date=None,
            source_update_id=9002,
        )
        self.assertEqual(row["id"], 7)
        self.assertEqual(len(self.client.calls), 2)
        self.assertIn(("telegram_id", 42), self.client.calls[-1].filters)
        self.assertIn(("source_update_id", 9002), self.client.calls[-1].filters)

    async def test_same_update_cannot_be_reused_for_different_debt(self):
        self.client.results[("finance_debts", "upsert")] = []
        self.client.results[("finance_debts", "select")] = [{
            "id": 7,
            "direction": "owed_by_me",
            "counterparty": "Bank",
            "title": "Loan",
            "amount": "5000.00",
            "due_date": None,
        }]
        with self.assertRaises(ValueError):
            await self.store.create_debt(
                42,
                direction="owed_by_me",
                counterparty="Bank",
                title="Loan",
                amount=9000,
                due_date=None,
                source_update_id=9003,
            )

    async def test_payment_calls_atomic_rpc_with_owner_and_update_id(self):
        self.client.results[("record_finance_debt_payment", "rpc")] = [{
            "duplicate": False,
            "payment_id": 5,
            "transaction_id": 11,
            "amount_remaining": 2500,
            "status": "open",
        }]
        result = await self.store.record_payment(
            42, 7, 2500, payment_date=date(2026, 11, 10), source_update_id=9004
        )
        self.assertEqual(result["payment_id"], 5)
        query = self.client.calls[-1]
        self.assertEqual(query.name, "record_finance_debt_payment")
        self.assertEqual(query.payload["p_telegram_id"], 42)
        self.assertEqual(query.payload["p_debt_id"], 7)
        self.assertEqual(query.payload["p_source_update_id"], 9004)

    async def test_budget_and_weight_upserts_are_owner_scoped(self):
        self.client.results[("finance_budgets", "upsert")] = [{"id": 1}]
        await self.store.set_budget(
            42, period_month=date(2026, 11, 18), category="Еда", limit_amount=20000
        )
        budget = self.client.calls[-1]
        self.assertEqual(budget.payload["period_month"], "2026-11-01")
        self.assertEqual(budget.payload["telegram_id"], 42)

        self.client.results[("finance_budget_weights", "upsert")] = [{"id": 2}]
        await self.store.set_weight(42, category="Еда", weight=3)
        weight = self.client.calls[-1]
        self.assertEqual(weight.payload["telegram_id"], 42)
        self.assertEqual(weight.payload["weight"], "3.00")

    async def test_invalid_owner_direction_amount_and_update_id_are_rejected(self):
        with self.assertRaises(ValueError):
            await self.store.list_debts(0)
        with self.assertRaises(ValueError):
            await self.store.create_debt(
                42,
                direction="unknown",
                counterparty="Bank",
                title="Loan",
                amount=100,
                due_date=None,
                source_update_id=9005,
            )
        with self.assertRaises(ValueError):
            await self.store.record_payment(
                42, 1, 0, payment_date=date(2026, 11, 10), source_update_id=9006
            )


if __name__ == "__main__":
    unittest.main()
