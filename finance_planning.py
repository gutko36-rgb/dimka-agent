"""Pure financial planning calculations.

This module never reads or writes Supabase. Amounts use Decimal to avoid
floating-point rounding in balances, forecasts, and budget allocations.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping


CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Некорректная денежная сумма") from exc
    if not amount.is_finite():
        raise ValueError("Сумма должна быть конечным числом")
    return amount


def _positive_money(value: Any, field: str) -> Decimal:
    amount = money(value)
    if amount <= ZERO:
        raise ValueError(f"{field} должна быть больше нуля")
    return amount


def _as_date(value: Any, field: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Некорректная дата: {field}") from exc


def calculate_balance(
    transactions: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None = None,
) -> Decimal:
    """Calculate recorded cash balance: income minus expense."""
    balance = ZERO
    for row in transactions:
        tx_date = _as_date(row.get("tx_date") or row.get("date"), "tx_date")
        if as_of is not None and tx_date > as_of:
            continue
        amount = _positive_money(row.get("amount"), "amount")
        kind = str(row.get("kind", "")).strip().lower()
        if kind == "income":
            balance += amount
        elif kind == "expense":
            balance -= amount
        else:
            raise ValueError(f"Неизвестный тип транзакции: {kind}")
    return balance.quantize(CENT)


@dataclass(frozen=True)
class ForecastEvent:
    event_date: date
    label: str
    amount: Decimal  # Positive = expected inflow; negative = expected outflow.
    source: str
    category: str | None = None


@dataclass(frozen=True)
class BalanceForecast:
    as_of: date
    until: date
    current_balance: Decimal
    projected_balance: Decimal
    events: tuple[ForecastEvent, ...]


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _monthly_due_date(after_or_on: date, day_of_month: int) -> date:
    if not 1 <= day_of_month <= 31:
        raise ValueError("day_of_month должен быть от 1 до 31")
    day = min(day_of_month, monthrange(after_or_on.year, after_or_on.month)[1])
    due = date(after_or_on.year, after_or_on.month, day)
    if due < after_or_on:
        year, month = _next_month(after_or_on.year, after_or_on.month)
        due = date(year, month, min(day_of_month, monthrange(year, month)[1]))
    return due


def forecast_balance(
    transactions: Iterable[Mapping[str, Any]],
    recurring_payments: Iterable[Mapping[str, Any]],
    debts: Iterable[Mapping[str, Any]],
    *,
    as_of: date,
    horizon_days: int = 30,
) -> BalanceForecast:
    """Project the recorded balance using known recurring payments and dated debts.

    No future salary or unconfirmed income is invented. Debts with no due date
    are excluded from the dated projection and should be shown separately.
    """
    if not isinstance(horizon_days, int) or isinstance(horizon_days, bool) or not 1 <= horizon_days <= 366:
        raise ValueError("horizon_days должен быть от 1 до 366")
    until = as_of + timedelta(days=horizon_days)
    current = calculate_balance(transactions, as_of=as_of)
    events: list[ForecastEvent] = []

    for payment in recurring_payments:
        amount = _positive_money(payment.get("amount"), "payment amount")
        day = int(payment.get("day_of_month"))
        due = _monthly_due_date(as_of, day)
        while due <= until:
            events.append(
                ForecastEvent(
                    event_date=due,
                    label=str(payment.get("title") or "Регулярный платёж")[:120],
                    amount=-amount,
                    source="recurring_payment",
                    category="recurring",
                )
            )
            year, month = _next_month(due.year, due.month)
            due = date(year, month, min(day, monthrange(year, month)[1]))

    for debt in debts:
        status = str(debt.get("status") or "open").strip().lower()
        if status in {"paid", "cancelled", "closed"}:
            continue
        raw_due = debt.get("due_date")
        if not raw_due:
            continue
        due = _as_date(raw_due, "debt due_date")
        if due > until:
            continue
        if due < as_of:
            due = as_of  # Overdue open debt is due immediately in the forecast.
        raw_amount = debt.get("amount_remaining")
        if raw_amount is None:
            raw_amount = debt.get("amount")
        amount = _positive_money(raw_amount, "debt amount")
        direction = str(debt.get("direction") or "").strip().lower()
        if direction in {"owed_by_me", "i_owe", "payable"}:
            signed_amount = -amount
        elif direction in {"owed_to_me", "they_owe_me", "receivable"}:
            signed_amount = amount
        else:
            raise ValueError(f"Неизвестное направление долга: {direction}")
        events.append(
            ForecastEvent(
                event_date=due,
                label=str(debt.get("counterparty") or debt.get("title") or "Долг")[:120],
                amount=signed_amount,
                source="debt",
                category="debt",
            )
        )

    events.sort(key=lambda item: (item.event_date, item.amount, item.label))
    projected = current + sum((event.amount for event in events), ZERO)
    return BalanceForecast(
        as_of=as_of,
        until=until,
        current_balance=current,
        projected_balance=projected.quantize(CENT),
        events=tuple(events),
    )


@dataclass(frozen=True)
class BudgetLine:
    category: str
    spent: Decimal
    limit: Decimal | None
    remaining: Decimal | None
    utilization: Decimal | None


def analyze_budget(
    transactions: Iterable[Mapping[str, Any]],
    budgets: Mapping[str, Any],
    *,
    month: date,
) -> tuple[BudgetLine, ...]:
    """Compare actual monthly expenses with category limits."""
    start = date(month.year, month.month, 1)
    end = date(month.year, month.month, monthrange(month.year, month.month)[1])
    spent_by_category: dict[str, Decimal] = {}

    for row in transactions:
        tx_date = _as_date(row.get("tx_date") or row.get("date"), "tx_date")
        if not start <= tx_date <= end or str(row.get("kind", "")).lower() != "expense":
            continue
        category = str(row.get("category") or "Без категории").strip() or "Без категории"
        spent_by_category[category] = spent_by_category.get(category, ZERO) + _positive_money(
            row.get("amount"), "amount"
        )

    categories = set(spent_by_category) | {str(key) for key in budgets}
    result: list[BudgetLine] = []
    for category in sorted(categories, key=str.casefold):
        spent = spent_by_category.get(category, ZERO).quantize(CENT)
        raw_limit = budgets.get(category)
        if raw_limit is None:
            result.append(BudgetLine(category, spent, None, None, None))
            continue
        limit = money(raw_limit)
        if limit < ZERO:
            raise ValueError(f"Бюджет {category} не может быть отрицательным")
        remaining = (limit - spent).quantize(CENT)
        utilization = (
            (spent / limit * Decimal("100")).quantize(CENT)
            if limit > ZERO
            else (ZERO if spent == ZERO else None)
        )
        result.append(BudgetLine(category, spent, limit, remaining, utilization))
    return tuple(result)


@dataclass(frozen=True)
class DistributionPlan:
    income: Decimal
    fixed_expenses: Decimal
    debt_due: Decimal
    savings_target: Decimal
    available: Decimal
    deficit: Decimal
    allocations: dict[str, Decimal]


def plan_distribution(
    *,
    income: Any,
    fixed_expenses: Any,
    debt_due: Any,
    savings_target: Any,
    category_weights: Mapping[str, Any],
) -> DistributionPlan:
    """Allocate remaining money by caller-supplied weights; invents no preferences."""
    income_value = money(income)
    fixed_value = money(fixed_expenses)
    debt_value = money(debt_due)
    savings_value = money(savings_target)
    if min(income_value, fixed_value, debt_value, savings_value) < ZERO:
        raise ValueError("Суммы распределения не могут быть отрицательными")

    available = income_value - fixed_value - debt_value - savings_value
    deficit = max(-available, ZERO)
    distributable = max(available, ZERO)

    weights: dict[str, Decimal] = {}
    for name, raw_weight in category_weights.items():
        weight = money(raw_weight)
        if weight < ZERO:
            raise ValueError("Вес категории не может быть отрицательным")
        if weight > ZERO:
            weights[str(name)] = weight

    total_weight = sum(weights.values(), ZERO)
    allocations: dict[str, Decimal] = {}
    if total_weight > ZERO:
        allocated = ZERO
        names = list(weights)
        for name in names[:-1]:
            share = (distributable * weights[name] / total_weight).quantize(CENT, rounding=ROUND_HALF_UP)
            allocations[name] = share
            allocated += share
        allocations[names[-1]] = (distributable - allocated).quantize(CENT)

    return DistributionPlan(
        income=income_value,
        fixed_expenses=fixed_value,
        debt_due=debt_value,
        savings_target=savings_value,
        available=available.quantize(CENT),
        deficit=deficit.quantize(CENT),
        allocations=allocations,
    )
