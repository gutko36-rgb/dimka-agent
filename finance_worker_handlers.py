"""Pure financial domain handlers for the isolated finance worker.

The worker receives an explicitly scoped data snapshot from the primary bot.
It never queries Supabase user tables, writes records, or calls external APIs.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Mapping

from finance_planning import analyze_budget, forecast_balance, money, plan_distribution


ZERO = Decimal("0.00")
MAX_TRANSACTIONS = 5000
MAX_EVENTS = 500


def _records(value: Any, field: str, *, limit: int = MAX_TRANSACTIONS) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{field} must be a list with at most {limit} rows")
    if any(not isinstance(row, dict) for row in value):
        raise ValueError(f"Every {field} row must be an object")
    return value


def _date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc


def _money_text(value: Any) -> str:
    return format(money(value), ".2f")


def finance_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Calculate exact totals and category breakdown for a primary-bot snapshot."""
    rows = _records(payload.get("transactions"), "transactions")
    kind_filter = payload.get("kind")
    if kind_filter not in (None, "income", "expense"):
        raise ValueError("kind must be income, expense, or null")

    date_from = _date(payload["date_from"], "date_from") if payload.get("date_from") else None
    date_to = _date(payload["date_to"], "date_to") if payload.get("date_to") else None
    if date_from and date_to and date_to < date_from:
        raise ValueError("date_to must not be earlier than date_from")

    income = ZERO
    expense = ZERO
    categories: dict[str, Decimal] = {}
    count = 0
    for row in rows:
        tx_date = _date(row.get("tx_date") or row.get("date"), "transaction date")
        if date_from and tx_date < date_from:
            continue
        if date_to and tx_date > date_to:
            continue
        kind = str(row.get("kind", "")).strip().lower()
        if kind not in {"income", "expense"}:
            raise ValueError("transaction kind must be income or expense")
        if kind_filter and kind != kind_filter:
            continue
        amount = money(row.get("amount"))
        if amount <= ZERO:
            raise ValueError("transaction amount must be positive")
        count += 1
        if kind == "income":
            income += amount
        else:
            expense += amount
            category = str(row.get("category") or "Без категории").strip() or "Без категории"
            categories[category] = categories.get(category, ZERO) + amount

    result: dict[str, Any] = {
        "count": count,
        "income": _money_text(income),
        "expense": _money_text(expense),
        "difference": _money_text(income - expense),
        "expense_categories": [
            {"category": name, "amount": _money_text(total)}
            for name, total in sorted(categories.items(), key=lambda item: (-item[1], item[0].casefold()))
        ],
    }
    if kind_filter:
        result["kind_filter"] = kind_filter
    if date_from:
        result["date_from"] = date_from.isoformat()
    if date_to:
        result["date_to"] = date_to.isoformat()
    if payload.get("period_label"):
        result["period_label"] = str(payload["period_label"])[:120]
    if payload.get("project_title"):
        result["project_title"] = str(payload["project_title"])[:120]
    search_terms = payload.get("search_terms")
    if isinstance(search_terms, list):
        result["search_terms"] = [str(term)[:40] for term in search_terms[:5]]
    return result


def finance_forecast(payload: dict[str, Any]) -> dict[str, Any]:
    """Project recorded balance using only supplied payments and dated debts."""
    transactions = _records(payload.get("transactions"), "transactions")
    payments = _records(payload.get("recurring_payments", []), "recurring_payments", limit=MAX_EVENTS)
    debts = _records(payload.get("debts", []), "debts", limit=MAX_EVENTS)
    as_of = _date(payload.get("as_of"), "as_of")
    horizon = payload.get("horizon_days", 30)
    if not isinstance(horizon, int) or isinstance(horizon, bool):
        raise ValueError("horizon_days must be an integer")

    forecast = forecast_balance(
        transactions,
        payments,
        debts,
        as_of=as_of,
        horizon_days=horizon,
    )
    return {
        "as_of": forecast.as_of.isoformat(),
        "until": forecast.until.isoformat(),
        "current_balance": _money_text(forecast.current_balance),
        "projected_balance": _money_text(forecast.projected_balance),
        "events": [
            {
                "date": event.event_date.isoformat(),
                "label": event.label,
                "amount": _money_text(event.amount),
                "source": event.source,
                "category": event.category,
            }
            for event in forecast.events
        ],
        "note": "Расчёт по переданным записям; неподтверждённые будущие доходы не учитываются.",
    }


def finance_budget_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    """Compare the supplied month's expenses with explicit category budgets."""
    transactions = _records(payload.get("transactions"), "transactions")
    budgets = payload.get("budgets")
    if not isinstance(budgets, dict):
        raise ValueError("budgets must be an object")
    month = _date(payload.get("month"), "month")
    lines = analyze_budget(transactions, budgets, month=month)
    return {
        "month": month.strftime("%Y-%m"),
        "categories": [
            {
                "category": line.category,
                "spent": _money_text(line.spent),
                "limit": _money_text(line.limit) if line.limit is not None else None,
                "remaining": _money_text(line.remaining) if line.remaining is not None else None,
                "utilization_percent": _money_text(line.utilization) if line.utilization is not None else None,
                "over_budget": line.remaining is not None and line.remaining < ZERO,
            }
            for line in lines
        ],
    }


def finance_distribution(payload: dict[str, Any]) -> dict[str, Any]:
    """Allocate only the remainder and only by weights explicitly supplied by the owner."""
    weights = payload.get("category_weights")
    if not isinstance(weights, dict):
        raise ValueError("category_weights must be an object")
    plan = plan_distribution(
        income=payload.get("income"),
        fixed_expenses=payload.get("fixed_expenses"),
        debt_due=payload.get("debt_due"),
        savings_target=payload.get("savings_target"),
        category_weights=weights,
    )
    return {
        "income": _money_text(plan.income),
        "fixed_expenses": _money_text(plan.fixed_expenses),
        "debt_due": _money_text(plan.debt_due),
        "savings_target": _money_text(plan.savings_target),
        "available": _money_text(plan.available),
        "deficit": _money_text(plan.deficit),
        "allocations": {name: _money_text(amount) for name, amount in plan.allocations.items()},
        "note": "Распределение рассчитано только по переданным суммам и весам; веса не угадываются.",
    }


def build_finance_handlers() -> dict[str, Any]:
    """Return the allowlisted, side-effect-free finance task handlers."""
    return {
        "finance.summary": finance_summary,
        "finance.forecast": finance_forecast,
        "finance.budget_analysis": finance_budget_analysis,
        "finance.distribution": finance_distribution,
    }
