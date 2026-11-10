import unittest

from finance_worker_handlers import (
    build_finance_handlers,
    finance_budget_analysis,
    finance_distribution,
    finance_forecast,
    finance_summary,
)


class FinanceWorkerHandlerTests(unittest.TestCase):
    def test_summary_calculates_exact_totals_and_expense_categories(self):
        result = finance_summary({
            "transactions": [
                {"kind": "income", "amount": "10000", "category": "salary", "tx_date": "2026-11-01"},
                {"kind": "expense", "amount": "3000", "category": "Еда", "tx_date": "2026-11-02"},
                {"kind": "expense", "amount": "500", "category": "Транспорт", "tx_date": "2026-11-03"},
                {"kind": "expense", "amount": "999", "category": "Еда", "tx_date": "2026-12-01"},
            ],
            "date_from": "2026-11-01",
            "date_to": "2026-11-30",
            "period_label": "за ноябрь",
            "project_title": "Пульт",
            "search_terms": ["материалы"],
        })
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["period_label"], "за ноябрь")
        self.assertEqual(result["project_title"], "Пульт")
        self.assertEqual(result["search_terms"], ["материалы"])
        self.assertEqual(result["income"], "10000.00")
        self.assertEqual(result["expense"], "3500.00")
        self.assertEqual(result["difference"], "6500.00")
        self.assertEqual(result["expense_categories"], [
            {"category": "Еда", "amount": "3000.00"},
            {"category": "Транспорт", "amount": "500.00"},
        ])

    def test_summary_can_filter_income_or_expense(self):
        rows = [
            {"kind": "income", "amount": 2000, "tx_date": "2026-11-01"},
            {"kind": "expense", "amount": 500, "category": "food", "tx_date": "2026-11-01"},
        ]
        self.assertEqual(finance_summary({"transactions": rows, "kind": "income"})["count"], 1)
        self.assertEqual(finance_summary({"transactions": rows, "kind": "expense"})["expense"], "500.00")

    def test_summary_rejects_invalid_rows_and_ranges(self):
        with self.assertRaises(ValueError):
            finance_summary({"transactions": [{"kind": "income", "amount": "NaN", "tx_date": "2026-11-01"}]})
        with self.assertRaises(ValueError):
            finance_summary({"transactions": [], "date_from": "2026-12-01", "date_to": "2026-11-01"})
        with self.assertRaises(ValueError):
            finance_summary({"transactions": [{"kind": "transfer", "amount": 100, "tx_date": "2026-11-01"}]})

    def test_forecast_returns_known_payments_and_debts_only(self):
        result = finance_forecast({
            "transactions": [
                {"kind": "income", "amount": "100000", "tx_date": "2026-11-01"},
                {"kind": "expense", "amount": "10000", "tx_date": "2026-11-03"},
            ],
            "recurring_payments": [{"title": "Аренда", "amount": "20000", "day_of_month": 15}],
            "debts": [{
                "counterparty": "Банк",
                "direction": "owed_by_me",
                "amount_remaining": "5000",
                "due_date": "2026-11-20",
                "status": "open",
            }],
            "as_of": "2026-11-10",
            "horizon_days": 30,
        })
        self.assertEqual(result["current_balance"], "90000.00")
        self.assertEqual(result["projected_balance"], "65000.00")
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["note"].startswith("Расчёт по переданным записям"), True)

    def test_budget_analysis_flags_over_budget(self):
        result = finance_budget_analysis({
            "transactions": [
                {"kind": "expense", "amount": 3000, "category": "Еда", "tx_date": "2026-11-02"},
                {"kind": "expense", "amount": 2500, "category": "Еда", "tx_date": "2026-11-08"},
            ],
            "budgets": {"Еда": 5000, "Транспорт": 2000},
            "month": "2026-11-10",
        })
        food = next(row for row in result["categories"] if row["category"] == "Еда")
        self.assertEqual(food["spent"], "5500.00")
        self.assertEqual(food["remaining"], "-500.00")
        self.assertTrue(food["over_budget"])

    def test_distribution_uses_only_explicit_weights_and_preserves_total(self):
        result = finance_distribution({
            "income": 100000,
            "fixed_expenses": 25000,
            "debt_due": 10000,
            "savings_target": 15000,
            "category_weights": {"Еда": 3, "Транспорт": 1},
        })
        self.assertEqual(result["available"], "50000.00")
        self.assertEqual(result["allocations"]["Еда"], "37500.00")
        self.assertEqual(result["allocations"]["Транспорт"], "12500.00")
        self.assertEqual(sum(map(float, result["allocations"].values())), 50000.0)

    def test_handler_registry_is_allowlisted(self):
        handlers = build_finance_handlers()
        self.assertEqual(set(handlers), {
            "finance.summary",
            "finance.forecast",
            "finance.budget_analysis",
            "finance.distribution",
        })
        self.assertTrue(all(callable(handler) for handler in handlers.values()))

    def test_payload_size_is_bounded(self):
        with self.assertRaises(ValueError):
            finance_summary({"transactions": [{}] * 5001})


if __name__ == "__main__":
    unittest.main()
