import unittest
from datetime import date
from types import SimpleNamespace

from lifestyle_agent import LifestyleStore


async def direct_db_call(method):
    return method()


class FakeQuery:
    def __init__(self, client, name):
        self.client = client
        self.name = name
        self.operation = "select"
        self.payload = None
        self.options = {}
        self.filters = []

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def gte(self, key, value):
        self.filters.append((key, ">=", value))
        return self

    def lte(self, key, value):
        self.filters.append((key, "<=", value))
        return self

    def order(self, *_args, **_kwargs):
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
        return SimpleNamespace(data=self.client.results.get((self.name, self.operation), []))


class FakeClient:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, name, params):
        query = FakeQuery(self, name)
        query.operation = "rpc"
        query.payload = params
        self.results[(name, "rpc")] = self.results.get((name, "rpc"), {})
        return query


class LifestyleStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeClient()
        self.store = LifestyleStore(self.client, direct_db_call)

    async def test_list_goals_is_owner_scoped(self):
        self.client.results[("lifestyle_goals", "select")] = [{"id": 1}]
        rows = await self.store.list_goals(42)
        self.assertEqual(rows, [{"id": 1}])
        self.assertIn(("telegram_id", 42), self.client.calls[-1].filters)
        self.assertIn(("status", "active"), self.client.calls[-1].filters)

    async def test_create_goal_uses_owner_and_source_update_id(self):
        self.client.results[("lifestyle_goals", "upsert")] = [{
            "id": 5, "telegram_id": 42, "title": "Учить английский",
            "description": "20 минут в день", "target_date": "2027-01-01",
        }]
        row = await self.store.create_goal(
            42,
            title="Учить английский",
            description="20 минут в день",
            target_date=date(2027, 1, 1),
            source_update_id=1001,
        )
        self.assertEqual(row["id"], 5)
        query = self.client.calls[-1]
        self.assertEqual(query.payload["telegram_id"], 42)
        self.assertEqual(query.payload["source_update_id"], 1001)
        self.assertEqual(query.options["on_conflict"], "telegram_id,source_update_id")
        self.assertTrue(query.options["ignore_duplicates"])

    async def test_goal_progress_uses_atomic_rpc_and_owner(self):
        self.client.results[("record_lifestyle_goal_progress", "rpc")] = {
            "duplicate": False, "goal_id": 5, "progress": 25, "status": "active"
        }
        result = await self.store.update_goal_progress(
            42, 5, 25, note="Первый этап", source_update_id=1002
        )
        self.assertEqual(result["progress"], 25)
        query = self.client.calls[-1]
        self.assertEqual(query.name, "record_lifestyle_goal_progress")
        self.assertEqual(query.payload["p_telegram_id"], 42)
        self.assertEqual(query.payload["p_goal_id"], 5)
        self.assertEqual(query.payload["p_source_update_id"], 1002)

    async def test_create_habit_uses_owner_frequency_and_date(self):
        self.client.results[("lifestyle_habits", "upsert")] = [{
            "id": 7, "telegram_id": 42, "title": "Ходьба", "target_per_week": 5
        }]
        row = await self.store.create_habit(
            42, title="Ходьба", description=None, target_per_week=5,
            start_date=date(2026, 11, 10), source_update_id=1003
        )
        self.assertEqual(row["id"], 7)
        payload = self.client.calls[-1].payload
        self.assertEqual(payload["telegram_id"], 42)
        self.assertEqual(payload["target_per_week"], 5)
        self.assertEqual(payload["start_date"], "2026-11-10")

    async def test_habit_log_checks_owner_and_is_idempotent_by_day(self):
        self.client.results[("lifestyle_habits", "select")] = [{
            "id": 7, "telegram_id": 42, "is_active": True
        }]
        self.client.results[("lifestyle_habit_logs", "upsert")] = [{
            "id": 9, "telegram_id": 42, "habit_id": 7, "status": "done"
        }]
        row = await self.store.log_habit(
            42, 7, log_date=date(2026, 11, 10), status="done",
            note="Утренняя прогулка", source_update_id=1004
        )
        self.assertEqual(row["id"], 9)
        upsert = [q for q in self.client.calls if q.name == "lifestyle_habit_logs"][-1]
        self.assertEqual(upsert.options["on_conflict"], "telegram_id,habit_id,log_date")
        self.assertEqual(upsert.payload["telegram_id"], 42)
        self.assertEqual(upsert.payload["source_update_id"], 1004)

    async def test_habit_log_rejects_foreign_or_inactive_habit(self):
        self.client.results[("lifestyle_habits", "select")] = []
        with self.assertRaises(ValueError):
            await self.store.log_habit(
                42, 99, log_date=date(2026, 11, 10), status="done",
                note=None, source_update_id=1005
            )
        self.assertFalse(any(q.name == "lifestyle_habit_logs" for q in self.client.calls))

    async def test_invalid_owner_progress_frequency_and_status_are_rejected(self):
        with self.assertRaises(ValueError):
            await self.store.list_goals(0)
        with self.assertRaises(ValueError):
            await self.store.update_goal_progress(
                42, 1, 101, note=None, source_update_id=1006
            )
        with self.assertRaises(ValueError):
            await self.store.create_habit(
                42, title="Бег", description=None, target_per_week=8,
                start_date=date(2026, 11, 10), source_update_id=1007
            )
        with self.assertRaises(ValueError):
            await self.store.log_habit(
                42, 1, log_date=date(2026, 11, 10), status="missed",
                note=None, source_update_id=1008
            )


if __name__ == "__main__":
    unittest.main()
