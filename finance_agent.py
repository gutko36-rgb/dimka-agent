"""Pure validation helpers for the natural-language finance workflow.

No network or database access lives here so the rules can be tested offline.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

FINANCE_CUES = re.compile(
    r"(?iu)\b(?:потратил[а-яё]*|купил[а-яё]*|оплат[а-яё]*|заплатил[а-яё]*|"
    r"расход[а-яё]*|доход[а-яё]*|получил[а-яё]*|заработал[а-яё]*|"
    r"перев[её]л[а-яё]*|пришл[а-яё]*|списал[а-яё]*|поступил[а-яё]*|"
    r"покупк[а-яё]*|транзакц[а-яё]*)\b"
)


def looks_like_finance(text: str) -> bool:
    """Return True only when a message contains a transaction cue."""
    return bool(text and FINANCE_CUES.search(text))


FOREIGN_CURRENCY_CUES = re.compile(
    r"(?iu)(?:\b(?:USD|EUR|KZT|UAH|GBP|доллар[а-яё]*|евро|тенге|гривн[а-яё]*|фунт[а-яё]*)\b|[$€₸£])"
)


def has_foreign_currency(text: str) -> bool:
    """Detect currencies not supported by the current RUB-only ledger."""
    return bool(text and FOREIGN_CURRENCY_CUES.search(text))


def validate_finance_operations(
    raw: Any,
    projects: list[dict],
    today: date,
) -> tuple[list[dict], str | None]:
    """Validate model output and normalize it into database-ready operations.

    Returns (operations, clarification). A clarification means nothing may be written.
    """
    if not isinstance(raw, dict):
        return [], "Не получилось разобрать операции. Напиши сумму, тип операции и назначение."
    if raw.get("is_finance") is not True:
        return [], None

    clarification = raw.get("clarification")
    if clarification:
        return [], str(clarification)[:500]

    items = raw.get("operations")
    if not isinstance(items, list) or not items:
        return [], "Уточни, пожалуйста: это доход или расход и какая точная сумма?"
    if len(items) > 10:
        return [], "В одном сообщении можно разобрать не больше 10 операций. Раздели список на несколько сообщений."

    project_ids = {int(p["id"]) for p in projects}
    normalized: list[dict] = []

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            return [], f"Не удалось разобрать операцию №{index + 1}. Напиши её отдельно."
        if item.get("kind") not in ("income", "expense"):
            return [], f"Для операции №{index + 1} неясно, доход это или расход."
        try:
            amount = Decimal(str(item.get("amount", ""))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        except (InvalidOperation, ValueError):
            return [], f"Не распознал сумму операции №{index + 1}. Уточни сумму."
        if not amount.is_finite() or amount <= 0 or amount >= Decimal("1000000000"):
            return [], f"Проверь сумму операции №{index + 1}."
        try:
            confidence = float(item.get("confidence", 1))
        except (TypeError, ValueError):
            confidence = 0
        if confidence < 0.75:
            return [], f"Не уверен в деталях операции №{index + 1}. Уточни её, пожалуйста."

        currency = str(item.get("currency") or "RUB").strip().upper()
        if currency not in ("RUB", "RUR", "₽", "РУБ", "РУБЛЬ", "РУБЛЕЙ"):
            return [], "В финансовом учёте сейчас используется рубль. Уточни сумму в рублях; валютную операцию пока не записывал."

        raw_date = item.get("date")
        try:
            tx_date = date.fromisoformat(raw_date) if raw_date else today
        except (TypeError, ValueError):
            return [], f"Не распознал дату операции №{index + 1}. Напиши дату явно."
        if tx_date > today:
            return [], f"Дата операции №{index + 1} оказалась в будущем. Проверь её."

        project_id = item.get("project_id")
        if project_id in ("", "null", "None"):
            project_id = None
        if project_id is not None:
            try:
                project_id = int(project_id)
            except (TypeError, ValueError):
                return [], f"Не распознал проект операции №{index + 1}."
            if project_id not in project_ids:
                return [], f"Проект для операции №{index + 1} не найден среди твоих активных проектов."

        category = str(item.get("category") or "без категории").strip().lower()[:40]
        note = str(item.get("note") or "").strip()[:200] or None
        normalized.append({
            "kind": item["kind"],
            "amount": float(amount),
            "category": category or "без категории",
            "note": note,
            "tx_date": tx_date.isoformat(),
            "project_id": project_id,
        })

    return normalized, None


def format_finance_operations(operations: list[dict]) -> str:
    """Readable summary shown to the user before confirmation."""
    lines = []
    for i, op in enumerate(operations, 1):
        sign = "+" if op["kind"] == "income" else "−"
        amount = f'{op["amount"]:,.2f}'.replace(",", " ").rstrip("0").rstrip(".")
        details = [op["category"], op["tx_date"]]
        if op.get("note"):
            details.append(op["note"])
        if op.get("project_title"):
            details.append(f'проект: {op["project_title"]}')
        lines.append(f'{i}. {sign}{amount} — ' + " · ".join(details))
    return "\n".join(lines)
