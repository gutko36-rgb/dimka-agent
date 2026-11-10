import unittest
from datetime import datetime

from bot_result_inbox import BotResultInbox, format_result_message


class Response:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, response):
        self.response = response
        self.filters = []
        self.updated = None

    def update(self, value):
        self.updated = value
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def select(self, value):
        return self

    def execute(self):
        return self.response


class FakeClient:
    def __init__(self, messages=None, updated=None):
        self.messages = messages or []
        self.updated = updated or [{"id": "result-id"}]
        self.rpc_calls = []
        self.query = Query(Response(self.updated))

    def rpc(self, name, args):
        self.rpc_calls.append((name, args))
        return Query(Response(self.messages))

    def table(self, name):
        self.table_name = name
        return self.query


class BotResultInboxTests(unittest.TestCase):
    def test_only_primary_identity_can_be_constructed(self):
        with self.assertRaises(ValueError):
            BotResultInbox(FakeClient(), bot_id="finance")

    def test_claim_uses_restricted_primary_rpc(self):
        message = {"id": "result-id", "message_type": "result"}
        client = FakeClient(messages=[message])
        inbox = BotResultInbox(client)

        self.assertEqual(inbox.claim(), [message])
        self.assertEqual(
            client.rpc_calls,
            [("claim_bot_results_for_primary", {"p_limit": 10, "p_lease_seconds": 300})],
        )

    def test_claim_validates_limits(self):
        inbox = BotResultInbox(FakeClient())
        with self.assertRaises(ValueError):
            inbox.claim(limit=0)
        with self.assertRaises(ValueError):
            inbox.claim(lease_seconds=1)

    def test_complete_is_scoped_to_processing_result_for_dimka(self):
        client = FakeClient()
        inbox = BotResultInbox(client)

        self.assertTrue(inbox.complete("result-id"))
        self.assertEqual(client.table_name, "bot_messages")
        self.assertEqual(
            client.query.filters,
            [
                ("id", "result-id"),
                ("to_bot_id", "dimka"),
                ("message_type", "result"),
                ("status", "processing"),
            ],
        )
        self.assertIsInstance(
            datetime.fromisoformat(client.query.updated["completed_at"]),
            datetime,
        )
        self.assertIsNotNone(datetime.fromisoformat(client.query.updated["completed_at"]).tzinfo)

    def test_format_successful_finance_result(self):
        text = format_result_message({
            "message_type": "result",
            "from_bot_id": "finance",
            "payload": {"task_type": "diagnostics.echo", "ok": True, "result": {"echo": {"value": 7}}},
        })
        self.assertIn("Результат от финансового агента", text)
        self.assertIn("diagnostics.echo", text)
        self.assertIn('"value": 7', text)

    def test_rejects_non_result_message(self):
        with self.assertRaises(ValueError):
            format_result_message({"message_type": "task", "from_bot_id": "finance", "payload": {}})

    def test_finance_summary_is_formatted_as_readable_russian_text(self):
        text = format_result_message({
            "message_type": "result",
            "from_bot_id": "finance",
            "payload": {
                "task_type": "finance.summary",
                "ok": True,
                "result": {
                    "period_label": "за текущий месяц",
                    "income": "10000.00",
                    "expense": "3500.00",
                    "difference": "6500.00",
                    "count": 3,
                    "expense_categories": [{"category": "Еда", "amount": "3000.00"}],
                },
            },
        })
        self.assertIn("📊 Финансовый агент", text)
        self.assertIn("Доходы: 10000.00 ₽", text)
        self.assertIn("Расходы: 3500.00 ₽", text)
        self.assertIn("Еда: 3000.00 ₽", text)
        self.assertNotIn('"task_type"', text)

    def test_finance_forecast_is_formatted_readably(self):
        text = format_result_message({
            "message_type": "result",
            "from_bot_id": "finance",
            "payload": {
                "task_type": "finance.forecast",
                "ok": True,
                "result": {
                    "as_of": "2026-11-10",
                    "until": "2026-12-10",
                    "current_balance": "90000.00",
                    "projected_balance": "65000.00",
                    "events": [{"date": "2026-11-15", "label": "Аренда", "amount": "-20000.00"}],
                    "note": "По записям",
                },
            },
        })
        self.assertIn("Прогноз от финансового агента", text)
        self.assertIn("90000.00 ₽", text)
        self.assertIn("Аренда", text)

    def test_finance_failure_shows_safe_error_code_without_json(self):
        text = format_result_message({
            "message_type": "result",
            "from_bot_id": "finance",
            "payload": {
                "task_type": "finance.summary",
                "ok": False,
                "error": "handler_error",
            },
        })
        self.assertIn("Финансовый агент не смог обработать запрос", text)
        self.assertIn("handler_error", text)
        self.assertNotIn('"error"', text)

    def test_project_summary_is_formatted_as_a_project_card(self):
        text = format_result_message({
            "message_type": "result",
            "from_bot_id": "projects",
            "payload": {
                "task_type": "projects.summary",
                "ok": True,
                "result": {
                    "project_id": 7,
                    "title": "Пульт",
                    "status": "active",
                    "deadline": "2026-11-09",
                    "project_overdue": True,
                    "stage_progress": {"done": 1, "total": 2},
                    "task_progress": {"done": 0, "total": 1},
                    "overdue_items": [{"content": "Собрать корпус", "due_date": "2026-11-09"}],
                    "upcoming_items": [{"content": "Заказать детали", "due_date": "2026-11-15"}],
                },
            },
        })
        self.assertIn("📁 Агент проектов: Пульт", text)
        self.assertIn("Статус: В работе", text)
        self.assertIn("ПРОСРОЧЕН", text)
        self.assertIn("Собрать корпус", text)
        self.assertNotIn('"task_type"', text)

    def test_project_failure_uses_safe_error_code(self):
        text = format_result_message({
            "message_type": "result",
            "from_bot_id": "projects",
            "payload": {
                "task_type": "projects.summary",
                "ok": False,
                "error": "handler_error",
            },
        })
        self.assertIn("Агент проектов не смог обработать запрос", text)
        self.assertIn("handler_error", text)


if __name__ == "__main__":
    unittest.main()
