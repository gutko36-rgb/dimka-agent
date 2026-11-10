import unittest
from datetime import date
from decimal import Decimal

from finance_debt_workflow import (
    parse_budget_set,
    parse_budget_weight,
    parse_debt_create,
    parse_debt_payment,
    parse_distribution,
    render_budget_report,
    render_debts,
    render_distribution,
)


class FinanceDebtWorkflowTests(unittest.TestCase):
    def test_parse_debt_create_with_due_date_and_note(self):
        draft = parse_debt_create(
            "owed_by_me | 12 500,50 | Банк | Автокредит | 2026-12-01 | Платёж каждый месяц"
        )
        self.assertEqual(draft["direction"], "owed_by_me")
        self.assertEqual(draft["amount"], Decimal("12500.50"))
        self.assertEqual(draft["counterparty"], "Банк")
        self.assertEqual(draft["due_date"], date(2026, 12, 1))
        self.assertEqual(draft["note"], "Платёж каждый месяц")

    def test_parse_debt_create_without_due_date(self):
        draft = parse_debt_create("owed_to_me | 5000 | Алексей | За материалы")
        self.assertIsNone(draft["due_date"])
        self.assertEqual(draft["direction"], "owed_to_me")

    def test_parse_debt_create_rejects_invalid_direction_date_and_amount(self):
        for raw in (
            "unknown | 100 | Банк | Кредит",
            "owed_by_me | 100 | Банк | Кредит | 01.12.2026",
            "owed_by_me | 0 | Банк | Кредит",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_debt_create(raw)

    def test_parse_debt_payment_requires_positive_id_and_amount(self):
        self.assertEqual(
            parse_debt_payment("7 | 2500"),
            {"debt_id": 7, "amount": Decimal("2500.00")},
        )
        with self.assertRaises(ValueError):
            parse_debt_payment("0 | 2500")
        with self.assertRaises(ValueError):
            parse_debt_payment("7 | -1")

    def test_parse_budget_and_weight(self):
        budget = parse_budget_set("2026-12 | Еда | 20000", date(2026, 11, 10))
        self.assertEqual(budget["period_month"], date(2026, 12, 1))
        self.assertEqual(budget["limit_amount"], Decimal("20000.00"))
        default_month = parse_budget_set("Транспорт | 0", date(2026, 11, 10))
        self.assertEqual(default_month["period_month"], date(2026, 11, 1))
        self.assertEqual(parse_budget_weight("Еда | 2.5")["weight"], Decimal("2.50"))

    def test_budget_report_uses_real_transactions_and_limits(self):
        text = render_budget_report(
            [
                {"kind": "expense", "amount": 1200, "category": "Еда", "tx_date": "2026-11-03"},
                {"kind": "expense", "amount": 500, "category": "Еда", "tx_date": "2026-11-05"},
            ],
            [{"category": "Еда", "limit_amount": 2000}],
            date(2026, 11, 10),
        )
        self.assertIn("1 700", text)
        self.assertIn("2 000", text)
        self.assertIn("осталось 300", text)

    def test_render_debts_shows_direction_and_remaining_amount(self):
        text = render_debts([{
            "id": 3,
            "direction": "owed_by_me",
            "counterparty": "Банк",
            "title": "Кредит",
            "amount": 10000,
            "amount_remaining": 7500,
            "due_date": "2026-12-01",
        }])
        self.assertIn("№3", text)
        self.assertIn("Я должен", text)
        self.assertIn("7 500", text)

    def test_distribution_uses_only_supplied_weights(self):
        values = parse_distribution("100000 | 20000 | 5000 | 10000")
        text = render_distribution(values, {"Еда": 2, "Транспорт": 1})
        self.assertIn("Доступно: 65 000", text)
        self.assertIn("Еда: 43 333.33", text)
        self.assertIn("Транспорт: 21 666.67", text)

    def test_distribution_rejects_negative_inputs(self):
        with self.assertRaises(ValueError):
            parse_distribution("10000 | -1 | 0 | 0")


if __name__ == "__main__":
    unittest.main()
