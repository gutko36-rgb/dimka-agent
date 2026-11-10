import unittest
from datetime import date
from types import SimpleNamespace

from finance_debt_telegram import build_finance_debt_handlers


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.edits = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answers = []
        self.edits = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeStore:
    def __init__(self):
        self.calls = []
        self.debt = {
            "id": 3,
            "telegram_id": 42,
            "direction": "owed_by_me",
            "counterparty": "Банк",
            "title": "Кредит",
            "amount": "10000.00",
            "amount_remaining": "7500.00",
            "due_date": "2026-12-01",
            "status": "open",
        }
        self.budgets = []
        self.weights = []

    async def list_debts(self, owner):
        self.calls.append(("list_debts", owner))
        return [self.debt]

    async def get_debt(self, owner, debt_id):
        self.calls.append(("get_debt", owner, debt_id))
        return self.debt if owner == 42 and debt_id == 3 else None

    async def create_debt(self, owner, **kwargs):
        self.calls.append(("create_debt", owner, kwargs))
        return {"id": 9, **kwargs, "amount_remaining": kwargs["amount"]}

    async def record_payment(self, owner, debt_id, amount, **kwargs):
        self.calls.append(("record_payment", owner, debt_id, amount, kwargs))
        return {
            "duplicate": False,
            "transaction_id": 101,
            "amount_remaining": "5000.00",
            "status": "open",
        }

    async def list_budgets(self, owner, month):
        self.calls.append(("list_budgets", owner, month))
        return list(self.budgets)

    async def set_budget(self, owner, **kwargs):
        self.calls.append(("set_budget", owner, kwargs))
        self.budgets = [{
            "category": kwargs["category"],
            "limit_amount": str(kwargs["limit_amount"]),
        }]

    async def list_weights(self, owner):
        self.calls.append(("list_weights", owner))
        return list(self.weights)

    async def set_weight(self, owner, **kwargs):
        self.calls.append(("set_weight", owner, kwargs))
        self.weights = [{
            "category": kwargs["category"],
            "weight": str(kwargs["weight"]),
        }]


class FinanceDebtTelegramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = FakeStore()
        self.message = FakeMessage()
        self.user = SimpleNamespace(id=42)
        self.update = SimpleNamespace(
            effective_user=self.user,
            effective_message=self.message,
            update_id=9001,
            callback_query=None,
        )
        self.context = SimpleNamespace(
            args=[],
            user_data={},
        )

        async def today(_user_id):
            return date(2026, 11, 10)

        async def load_transactions(_user_id, _start, _end):
            return []

        async def reject(_update):
            return None

        self.handlers = build_finance_debt_handlers(
            store=self.store,
            enabled=True,
            is_authorized=lambda _update: True,
            reject_unauthorized=reject,
            user_today=today,
            load_transactions=load_transactions,
            format_money=lambda value: f"{value} ₽",
            main_menu=None,
        )

    async def test_debt_creation_requires_confirmation(self):
        self.context.args = ["owed_by_me", "|", "10000", "|", "Банк", "|", "Кредит"]
        await self.handlers["debt_add"](self.update, self.context)
        self.assertEqual(self.context.user_data["finance_debt_pending"]["action"], "create")
        self.assertFalse(any(call[0] == "create_debt" for call in self.store.calls))
        self.assertIn("До подтверждения база не меняется", self.message.replies[-1][0])

    async def test_confirm_creates_debt_once_and_clears_draft(self):
        self.context.args = ["owed_by_me", "|", "10000", "|", "Банк", "|", "Кредит"]
        await self.handlers["debt_add"](self.update, self.context)
        query = FakeQuery("debt:confirm")
        callback_update = SimpleNamespace(
            effective_user=self.user,
            effective_message=self.message,
            callback_query=query,
        )
        await self.handlers["callback"](callback_update, self.context)
        calls = [call for call in self.store.calls if call[0] == "create_debt"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 42)
        self.assertEqual(calls[0][2]["source_update_id"], 9001)
        self.assertNotIn("finance_debt_pending", self.context.user_data)
        self.assertIn("Долг сохранён", query.edits[-1][0])

    async def test_cancel_does_not_write(self):
        self.context.args = ["owed_by_me", "|", "10000", "|", "Банк", "|", "Кредит"]
        await self.handlers["debt_add"](self.update, self.context)
        query = FakeQuery("debt:cancel")
        callback_update = SimpleNamespace(
            effective_user=self.user,
            effective_message=self.message,
            callback_query=query,
        )
        await self.handlers["callback"](callback_update, self.context)
        self.assertFalse(any(call[0] == "create_debt" for call in self.store.calls))
        self.assertNotIn("finance_debt_pending", self.context.user_data)
        self.assertIn("не изменялись", query.edits[-1][0])

    async def test_debt_payment_is_not_recorded_before_confirmation(self):
        self.context.args = ["3", "|", "2500"]
        await self.handlers["debt_pay"](self.update, self.context)
        self.assertEqual(self.context.user_data["finance_debt_pending"]["action"], "payment")
        self.assertFalse(any(call[0] == "record_payment" for call in self.store.calls))
        self.assertIn("Подтверди погашение", self.message.replies[-1][0])

    async def test_confirmed_debt_payment_uses_original_update_id(self):
        self.context.args = ["3", "|", "2500"]
        await self.handlers["debt_pay"](self.update, self.context)
        query = FakeQuery("debt:confirm")
        callback_update = SimpleNamespace(
            effective_user=self.user,
            effective_message=self.message,
            callback_query=query,
        )
        await self.handlers["callback"](callback_update, self.context)
        calls = [call for call in self.store.calls if call[0] == "record_payment"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 42)
        self.assertEqual(calls[0][2], 3)
        self.assertEqual(str(calls[0][3]), "2500.00")
        self.assertEqual(calls[0][4]["source_update_id"], 9001)
        self.assertIn("Финансовая транзакция №101 создана", query.edits[-1][0])

    async def test_budget_change_requires_confirmation_and_reads_back(self):
        self.context.args = ["Еда", "|", "20000"]
        await self.handlers["budget_set"](self.update, self.context)
        self.assertFalse(any(call[0] == "set_budget" for call in self.store.calls))
        query = FakeQuery("debt:confirm")
        callback_update = SimpleNamespace(
            effective_user=self.user,
            effective_message=self.message,
            callback_query=query,
        )
        await self.handlers["callback"](callback_update, self.context)
        self.assertTrue(any(call[0] == "set_budget" for call in self.store.calls))
        self.assertIn("Лимит сохранён", query.edits[-1][0])

    async def test_disabled_feature_does_not_touch_store(self):
        disabled = build_finance_debt_handlers(
            store=self.store,
            enabled=False,
            is_authorized=lambda _update: True,
            reject_unauthorized=lambda _update: None,
            user_today=lambda _user_id: None,
            load_transactions=lambda *_args: None,
            format_money=str,
            main_menu=None,
        )
        await disabled["debts"](self.update, self.context)
        self.assertEqual(self.store.calls, [])
        self.assertIn("пока выключен", self.message.replies[-1][0])


if __name__ == "__main__":
    unittest.main()
