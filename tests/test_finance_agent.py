import unittest
from datetime import date

from finance_agent import (
    format_finance_operations,
    has_foreign_currency,
    looks_like_finance,
    validate_finance_operations,
)


class FinanceIntentTests(unittest.TestCase):
    def test_finance_cues(self):
        self.assertTrue(looks_like_finance("Сегодня потратил 450 рублей на обед"))
        self.assertTrue(looks_like_finance("Получил зарплату 75000"))
        self.assertFalse(looks_like_finance("Помоги спланировать неделю"))

    def test_foreign_currency_detection(self):
        self.assertTrue(has_foreign_currency("потратил 20 долларов на такси"))
        self.assertTrue(has_foreign_currency("оплатил 10 EUR"))
        self.assertFalse(has_foreign_currency("потратил 500 рублей"))

    def test_single_expense(self):
        ops, error = validate_finance_operations(
            {
                "is_finance": True,
                "operations": [{
                    "kind": "expense", "amount": "450.50", "category": "еда",
                    "note": "обед", "date": None, "project_id": None, "confidence": 0.98,
                }],
            },
            [],
            date(2026, 11, 10),
        )
        self.assertIsNone(error)
        self.assertEqual(ops[0]["amount"], 450.5)
        self.assertEqual(ops[0]["tx_date"], "2026-11-10")

    def test_multiple_operations_and_project(self):
        ops, error = validate_finance_operations(
            {
                "is_finance": True,
                "operations": [
                    {"kind": "income", "amount": 50000, "category": "зарплата", "confidence": 0.99},
                    {"kind": "expense", "amount": 1200, "category": "материалы", "project_id": 7, "confidence": 0.99},
                ],
            },
            [{"id": 7}],
            date(2026, 11, 10),
        )
        self.assertIsNone(error)
        self.assertEqual(len(ops), 2)
        self.assertEqual(ops[1]["project_id"], 7)

    def test_reject_foreign_currency(self):
        ops, error = validate_finance_operations(
            {"is_finance": True, "operations": [
                {"kind": "expense", "amount": 10, "currency": "USD", "confidence": 0.99}
            ]},
            [],
            date(2026, 11, 10),
        )
        self.assertEqual(ops, [])
        self.assertIn("руб", error.lower())

    def test_reject_unknown_project(self):
        ops, error = validate_finance_operations(
            {"is_finance": True, "operations": [
                {"kind": "expense", "amount": 100, "project_id": 99, "confidence": 0.99}
            ]},
            [{"id": 7}],
            date(2026, 11, 10),
        )
        self.assertEqual(ops, [])
        self.assertIn("проект", error.lower())

    def test_reject_low_confidence_and_future_date(self):
        for operation, expected in [
            ({"kind": "expense", "amount": 10, "confidence": 0.2}, "уверен"),
            ({"kind": "expense", "amount": 10, "date": "2026-11-11", "confidence": 0.99}, "будущем"),
        ]:
            ops, error = validate_finance_operations(
                {"is_finance": True, "operations": [operation]}, [], date(2026, 11, 10)
            )
            self.assertEqual(ops, [])
            self.assertIn(expected, error.lower())

    def test_format_draft(self):
        text = format_finance_operations([
            {"kind": "expense", "amount": 450.0, "category": "еда", "tx_date": "2026-11-10"}
        ])
        self.assertIn("−450", text)
        self.assertIn("еда", text)


if __name__ == "__main__":
    unittest.main()
