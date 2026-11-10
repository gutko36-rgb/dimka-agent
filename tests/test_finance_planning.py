import unittest
from datetime import date
from decimal import Decimal

from finance_planning import (
    analyze_budget,
    calculate_balance,
    forecast_balance,
    plan_distribution,
)


class FinancePlanningTests(unittest.TestCase):
    def test_balance_is_income_minus_expense(self):
        rows = [
            {"kind": "income", "amount": "100000", "tx_date": "2026-11-01"},
            {"kind": "expense", "amount": "1250.50", "tx_date": "2026-11-02"},
            {"kind": "expense", "amount": "250", "tx_date": "2026-12-01"},
        ]
        self.assertEqual(calculate_balance(rows, as_of=date(2026, 11, 30)), Decimal("98749.50"))

    def test_forecast_includes_monthly_payment_and_debt_due(self):
        forecast = forecast_balance(
            transactions=[
                {"kind": "income", "amount": "100000", "tx_date": "2026-11-01"},
                {"kind": "expense", "amount": "10000", "tx_date": "2026-11-03"},
            ],
            recurring_payments=[{"title": "Rent", "amount": "20000", "day_of_month": 5}],
            debts=[
                {"counterparty": "Bank", "direction": "owed_by_me", "amount_remaining": "15000", "due_date": "2026-11-20", "status": "open"},
                {"counterparty": "Friend", "direction": "owed_to_me", "amount_remaining": "5000", "due_date": "2026-11-25", "status": "open"},
                {"counterparty": "Closed", "direction": "owed_by_me", "amount": "9999", "due_date": "2026-11-22", "status": "paid"},
            ],
            as_of=date(2026, 11, 10),
            horizon_days=30,
        )
        self.assertEqual(forecast.current_balance, Decimal("90000.00"))
        self.assertEqual(forecast.projected_balance, Decimal("60000.00"))
        self.assertEqual(len(forecast.events), 3)

    def test_overdue_open_debt_is_counted_as_due_today(self):
        forecast = forecast_balance(
            transactions=[],
            recurring_payments=[],
            debts=[{
                "counterparty": "Overdue",
                "direction": "owed_by_me",
                "amount_remaining": 1200,
                "due_date": "2026-11-01",
                "status": "open",
            }],
            as_of=date(2026, 11, 10),
            horizon_days=7,
        )
        self.assertEqual(forecast.events[0].event_date, date(2026, 11, 10))
        self.assertEqual(forecast.projected_balance, Decimal("-1200.00"))

    def test_month_end_payment_is_clamped_to_last_day(self):
        forecast = forecast_balance(
            transactions=[],
            recurring_payments=[{"title": "Internet", "amount": 1000, "day_of_month": 31}],
            debts=[],
            as_of=date(2026, 2, 1),
            horizon_days=28,
        )
        self.assertEqual(forecast.events[0].event_date, date(2026, 2, 28))

    def test_budget_analysis_reports_remaining_and_overrun(self):
        rows = [
            {"kind": "expense", "amount": 3000, "category": "Еда", "tx_date": "2026-11-02"},
            {"kind": "expense", "amount": 2500, "category": "Еда", "tx_date": "2026-11-08"},
            {"kind": "expense", "amount": 400, "category": "Транспорт", "tx_date": "2026-10-31"},
            {"kind": "income", "amount": 5000, "category": "Еда", "tx_date": "2026-11-10"},
        ]
        lines = {line.category: line for line in analyze_budget(rows, {"Еда": 5000, "Транспорт": 2000}, month=date(2026, 11, 15))}
        self.assertEqual(lines["Еда"].spent, Decimal("5500.00"))
        self.assertEqual(lines["Еда"].remaining, Decimal("-500.00"))
        self.assertEqual(lines["Транспорт"].spent, Decimal("0.00"))
        zero_budget = {line.category: line for line in analyze_budget(
            [{"kind": "expense", "amount": 10, "category": "Other", "tx_date": "2026-11-10"}],
            {"Other": 0},
            month=date(2026, 11, 1),
        )}["Other"]
        self.assertEqual(zero_budget.remaining, Decimal("-10.00"))
        self.assertIsNone(zero_budget.utilization)

    def test_distribution_respects_user_weights_and_keeps_rounding_exact(self):
        plan = plan_distribution(
            income="100000",
            fixed_expenses="25000",
            debt_due="10000",
            savings_target="15000",
            category_weights={"food": 3, "transport": 1},
        )
        self.assertEqual(plan.available, Decimal("50000.00"))
        self.assertEqual(plan.allocations["food"], Decimal("37500.00"))
        self.assertEqual(plan.allocations["transport"], Decimal("12500.00"))
        self.assertEqual(sum(plan.allocations.values()), plan.available)

    def test_distribution_reports_deficit_without_negative_allocations(self):
        plan = plan_distribution(
            income=50000,
            fixed_expenses=30000,
            debt_due=25000,
            savings_target=10000,
            category_weights={"food": 2, "other": 1},
        )
        self.assertEqual(plan.available, Decimal("-15000.00"))
        self.assertEqual(plan.deficit, Decimal("15000.00"))
        self.assertEqual(plan.allocations, {"food": Decimal("0.00"), "other": Decimal("0.00")})

    def test_invalid_finance_rows_are_rejected(self):
        with self.assertRaises(ValueError):
            calculate_balance([{"kind": "income", "amount": "NaN", "tx_date": "2026-11-01"}])
        with self.assertRaises(ValueError):
            forecast_balance([], [], [], as_of=date(2026, 11, 10), horizon_days=500)


if __name__ == "__main__":
    unittest.main()
