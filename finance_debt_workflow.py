"""Validation and presentation helpers for debt and budget Telegram workflows.

This module is pure: it does not access Telegram, Supabase, or environment secrets.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping

from finance_planning import analyze_budget, money, plan_distribution


def _fields(value: str, expected: tuple[int, ...]) -> list[str]:
    if not isinstance(value, str):
        raise ValueError("Аргументы должны быть текстом с разделителями |")
    fields = [part.strip() for part in value.split("|")]
    if len(fields) not in expected or any(not part for part in fields):
        raise ValueError("Проверь формат команды: разделяй поля символом |")
    return fields


def _amount(value: str, *, allow_zero: bool = False) -> Decimal:
    normalized = value.replace(" ", "").replace(",", ".")
    result = money(normalized)
    if result < 0 or (result == 0 and not allow_zero):
        raise ValueError("Сумма должна быть больше нуля")
    return result


def _text(value: str, field: str, max_length: int) -> str:
    result = value.strip()
    if not result or len(result) > max_length:
        raise ValueError(f"{field}: требуется текст от 1 до {max_length} символов")
    return result


def parse_debt_create(value: str) -> dict[str, Any]:
    """Parse direction | amount | counterparty | title [| due_date|- [| note]]."""
    fields = _fields(value, (4, 5, 6))
    direction = fields[0].lower()
    if direction not in {"owed_by_me", "owed_to_me"}:
        raise ValueError("Направление: owed_by_me (я должен) или owed_to_me (мне должны)")
    due_date = None
    note = None
    if len(fields) >= 5 and fields[4] != "-":
        try:
            due_date = date.fromisoformat(fields[4])
        except ValueError as exc:
            raise ValueError("Срок долга укажи как ГГГГ-ММ-ДД или -") from exc
    if len(fields) == 6:
        note = _text(fields[5], "Заметка", 1000)
    return {
        "direction": direction,
        "amount": _amount(fields[1]),
        "counterparty": _text(fields[2], "Контрагент", 120),
        "title": _text(fields[3], "Название", 180),
        "due_date": due_date,
        "note": note,
    }


def parse_debt_payment(value: str) -> dict[str, Any]:
    fields = _fields(value, (2,))
    if not fields[0].isdigit() or int(fields[0]) <= 0:
        raise ValueError("Первым полем укажи положительный номер долга")
    return {"debt_id": int(fields[0]), "amount": _amount(fields[1])}


def parse_budget_set(value: str, default_month: date) -> dict[str, Any]:
    """Parse category | limit or YYYY-MM | category | limit."""
    fields = _fields(value, (2, 3))
    if len(fields) == 2:
        month = date(default_month.year, default_month.month, 1)
        category, raw_limit = fields
    else:
        try:
            month = date.fromisoformat(fields[0] + "-01")
        except ValueError as exc:
            raise ValueError("Месяц укажи в формате ГГГГ-ММ") from exc
        category, raw_limit = fields[1], fields[2]
    return {
        "period_month": month,
        "category": _text(category, "Категория", 80),
        "limit_amount": _amount(raw_limit, allow_zero=True),
    }


def parse_budget_weight(value: str) -> dict[str, Any]:
    fields = _fields(value, (2,))
    return {
        "category": _text(fields[0], "Категория", 80),
        "weight": _amount(fields[1], allow_zero=True),
    }


def parse_distribution(value: str) -> dict[str, Decimal]:
    fields = _fields(value, (4,))
    names = ("income", "fixed_expenses", "debt_due", "savings_target")
    return {name: _amount(raw, allow_zero=True) for name, raw in zip(names, fields)}


def render_debts(rows: Iterable[Mapping[str, Any]]) -> str:
    items = list(rows)
    if not items:
        return "Открытых долгов нет."
    lines = ["Открытые долги:", ""]
    for row in items:
        direction = "Я должен" if row.get("direction") == "owed_by_me" else "Мне должны"
        due = row.get("due_date") or "срок не указан"
        lines.append(
            f"№{row['id']} · {direction}: {row.get('counterparty', '—')} — "
            f"{row.get('title', '—')}\n"
            f"Остаток: {money(row.get('amount_remaining', row.get('amount')))} из "
            f"{money(row.get('amount'))} · срок: {due}"
        )
    lines.extend(["", "Частичное погашение: /debt_pay ID | сумма"])
    return "\n".join(lines)


def render_budget_report(
    transactions: Iterable[Mapping[str, Any]],
    budget_rows: Iterable[Mapping[str, Any]],
    month: date,
) -> str:
    budgets = {str(row["category"]): row["limit_amount"] for row in budget_rows}
    lines = analyze_budget(transactions, budgets, month=month)
    if not lines:
        return "За этот месяц пока нет расходов и настроенных бюджетов."
    output = [f"Бюджет за {month.year:04d}-{month.month:02d}:", ""]
    for line in lines:
        if line.limit is None:
            output.append(f"• {line.category}: потрачено {money(line.spent)} · лимит не задан")
            continue
        if line.limit == 0:
            status = "лимит 0"
        elif line.remaining is not None and line.remaining < 0:
            status = f"превышение {money(abs(line.remaining))}"
        else:
            status = f"осталось {money(line.remaining or 0)}"
        output.append(
            f"• {line.category}: {money(line.spent)} / {money(line.limit)} · {status}"
        )
    return "\n".join(output)


def render_weights(rows: Iterable[Mapping[str, Any]]) -> str:
    items = list(rows)
    if not items:
        return "Веса категорий ещё не заданы. Добавь их командой /budget_weight Категория | вес."
    return "Веса для распределения денег:\n" + "\n".join(
        f"• {row['category']}: {row['weight']}" for row in items
    )


def render_distribution(
    values: Mapping[str, Decimal],
    weights: Mapping[str, Any],
) -> str:
    plan = plan_distribution(
        income=values["income"],
        fixed_expenses=values["fixed_expenses"],
        debt_due=values["debt_due"],
        savings_target=values["savings_target"],
        category_weights=weights,
    )
    lines = [
        "Черновик распределения (ничего не записываю):",
        f"Доход: {money(plan.income)}",
        f"Обязательные расходы: {money(plan.fixed_expenses)}",
        f"Платежи по долгам: {money(plan.debt_due)}",
        f"Цель накоплений: {money(plan.savings_target)}",
        f"Доступно: {money(plan.available)}",
    ]
    if plan.deficit > 0:
        lines.append(f"Не хватает: {money(plan.deficit)}")
    if plan.allocations:
        lines.extend(["", "По заданным тобой весам:"])
        lines.extend(f"• {name}: {money(amount)}" for name, amount in plan.allocations.items())
    elif plan.available > 0:
        lines.extend(["", "Веса не заданы. Сначала настрой /budget_weight для категорий."])
    return "\n".join(lines)
