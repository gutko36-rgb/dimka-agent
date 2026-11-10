import unittest
from datetime import date
from types import SimpleNamespace

from lifestyle_telegram import build_lifestyle_handlers


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.edits = []

    async def answer(self, *args, **kwargs):
        return None

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeStore:
    def __init__(self):
        self.calls = []
        self.goal = {"id": 5, "title": "Английский", "status": "active"}
        self.habit = {"id": 7, "title": "Ходьба", "is_active": True}
        self.logs = []

    async def list_goals(self, owner):
        self.calls.append(("list_goals", owner))
        return [self.goal]

    async def get_goal(self, owner, goal_id):
        self.calls.append(("get_goal", owner, goal_id))
        return self.goal if owner == 42 and goal_id == 5 else None

    async def create_goal(self, owner, **kwargs):
        self.calls.append(("create_goal", owner, kwargs))
        return {"id": 9, **kwargs}

    async def update_goal_progress(self, owner, goal_id, progress, **kwargs):
        self.calls.append(("update_goal_progress", owner, goal_id, progress, kwargs))
        return {"duplicate": False, "progress": progress}

    async def list_habits(self, owner):
        self.calls.append(("list_habits", owner))
        return [self.habit]

    async def get_habit(self, owner, habit_id):
        self.calls.append(("get_habit", owner, habit_id))
        return self.habit if owner == 42 and habit_id == 7 else None

    async def create_habit(self, owner, **kwargs):
        self.calls.append(("create_habit", owner, kwargs))
        return {"id": 11, **kwargs}

    async def log_habit(self, owner, habit_id, **kwargs):
        self.calls.append(("log_habit", owner, habit_id, kwargs))
        return {"log_date": kwargs["log_date"].isoformat(), "status": kwargs["status"]}

    async def list_habit_logs(self, owner, **kwargs):
        self.calls.append(("list_habit_logs", owner, kwargs))
        return list(self.logs)


class LifestyleTelegramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = FakeStore()
        self.message = FakeMessage()
        self.user = SimpleNamespace(id=42)
        self.update = SimpleNamespace(
            effective_user=self.user,
            effective_message=self.message,
            update_id=93001,
            callback_query=None,
        )
        self.context = SimpleNamespace(args=[], user_data={})

        async def today(_user_id):
            return date(2026, 11, 10)

        async def reject(_update):
            return None

        self.handlers = build_lifestyle_handlers(
            store=self.store,
            enabled=True,
            is_authorized=lambda _update: True,
            reject_unauthorized=reject,
            user_today=today,
            main_menu=None,
        )

    async def test_goal_create_requires_confirmation(self):
        self.context.args = ["Английский", "|", "20 минут", "|", "2027-01-01"]
        await self.handlers["goal_add"](self.update, self.context)
        self.assertEqual(self.context.user_data["lifestyle_pending"]["action"], "goal_create")
        self.assertFalse(any(call[0] == "create_goal" for call in self.store.calls))
        self.assertIn("До подтверждения база не меняется", self.message.replies[-1][0])

    async def test_confirm_goal_creation(self):
        self.context.args = ["Английский", "|", "-", "|", "-"]
        await self.handlers["goal_add"](self.update, self.context)
        token = self.context.user_data["lifestyle_pending"]["draft_id"]
        query = FakeQuery("life:confirm:" + token)
        callback_update = SimpleNamespace(effective_user=self.user, callback_query=query)
        await self.handlers["callback"](callback_update, self.context)
        calls = [call for call in self.store.calls if call[0] == "create_goal"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 42)
        self.assertEqual(calls[0][2]["source_update_id"], 93001)
        self.assertIn("Цель сохранена", query.edits[-1][0])

    async def test_goal_progress_is_only_written_after_confirmation(self):
        self.context.args = ["5", "|", "50", "|", "Первый этап"]
        await self.handlers["goal_progress"](self.update, self.context)
        self.assertFalse(any(call[0] == "update_goal_progress" for call in self.store.calls))
        token = self.context.user_data["lifestyle_pending"]["draft_id"]
        query = FakeQuery("life:confirm:" + token)
        callback_update = SimpleNamespace(effective_user=self.user, callback_query=query)
        await self.handlers["callback"](callback_update, self.context)
        self.assertTrue(any(call[0] == "update_goal_progress" for call in self.store.calls))
        self.assertIn("50.00%", query.edits[-1][0])

    async def test_habit_log_requires_confirmation(self):
        self.context.args = ["7", "|", "done", "|", "Прогулка"]
        await self.handlers["habit_log"](self.update, self.context)
        self.assertFalse(any(call[0] == "log_habit" for call in self.store.calls))
        self.assertIn("Черновик отметки привычки", self.message.replies[-1][0])

    async def test_confirm_habit_log(self):
        self.context.args = ["7", "|", "done", "|", "Прогулка"]
        await self.handlers["habit_log"](self.update, self.context)
        token = self.context.user_data["lifestyle_pending"]["draft_id"]
        query = FakeQuery("life:confirm:" + token)
        callback_update = SimpleNamespace(effective_user=self.user, callback_query=query)
        await self.handlers["callback"](callback_update, self.context)
        calls = [call for call in self.store.calls if call[0] == "log_habit"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 42)
        self.assertEqual(calls[0][2], 7)
        self.assertEqual(calls[0][3]["source_update_id"], 93001)
        self.assertIn("Отметка сохранена", query.edits[-1][0])

    async def test_old_button_cannot_confirm_a_newer_draft(self):
        self.context.args = ["Английский", "|", "-", "|", "-"]
        await self.handlers["goal_add"](self.update, self.context)
        old_token = self.context.user_data["lifestyle_pending"]["draft_id"]
        self.context.args = ["Ходьба", "|", "5", "|", "30 минут"]
        self.update.update_id = 93002
        await self.handlers["habit_add"](self.update, self.context)
        new_token = self.context.user_data["lifestyle_pending"]["draft_id"]
        query = FakeQuery("life:confirm:" + old_token)
        callback_update = SimpleNamespace(effective_user=self.user, callback_query=query)
        await self.handlers["callback"](callback_update, self.context)
        self.assertFalse(any(call[0] in {"create_goal", "create_habit"} for call in self.store.calls))
        self.assertEqual(self.context.user_data["lifestyle_pending"]["draft_id"], new_token)
        self.assertIn("устаревшему черновику", query.edits[-1][0])

    async def test_disabled_lifestyle_feature_does_not_access_database(self):
        disabled = build_lifestyle_handlers(
            store=self.store,
            enabled=False,
            is_authorized=lambda _update: True,
            reject_unauthorized=lambda _update: None,
            user_today=lambda _user_id: None,
            main_menu=None,
        )
        await disabled["goals"](self.update, self.context)
        self.assertEqual(self.store.calls, [])
        self.assertIn("пока выключен", self.message.replies[-1][0])


if __name__ == "__main__":
    unittest.main()
