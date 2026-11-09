"""Безопасное извлечение финансовых операций из обычного русского текста.

Модуль не обращается к БД и не выполняет запись. Он валидирует JSON от модели,
сопоставляет проекты только с реальным списком владельца и возвращает черновик.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any


_FINANCE_ACTION = re.compile(
    r"\b(?:потратил[а]?|израсходовал[а]?|купил[а]?|оплатил[а]?|"
    r"заплатил[а]?|получил[а]?|заработал[а]?|перевел[а]?|перевёл|перечислил[а]?|"
    r"пришл[аио]?|поступил[аио]?|начислили|выплатили|оплатили|списали|зачислили)\b",
    re.IGNORECASE,
)
_AMOUNT_HINT = re.compile(r"(?:\d|тысяч|тыс\.?|руб(?:лей|ля|ль)?|₽)", re.IGNORECASE)
_NEGATED_ACTION = re.compile(
    r"\bне\s+(?:потратил[а]?|израсходовал[а]?|купил[а]?|оплатил[а]?|заплатил[а]?|получил[а]?|заработал[а]?)\b",
    re.IGNORECASE,
)


def looks_like_finance_entry(message: str) -> bool:
    """True only for likely completed money events, not balance questions/plans."""
    text = (message or "").strip()
    if not text or _NEGATED_ACTION.search(text):
        return False
    if re.search(r"\b(?:если бы|хотел бы|мог бы|планирую|собираюсь)\b", text, re.IGNORECASE):
        return False
    return bool(_FINANCE_ACTION.search(text) and _AMOUNT_HINT.search(text))


_FINANCE_QUERY_TOPIC = re.compile(
    r"\b(?:расход(?:ы|ов)?|доход(?:ы|ов)?|финанс(?:ы|ах)?|бюджет|"
    r"потратил[а]?|заработал[а]?|платеж(?:и|ей)?|деньги)\b",
    re.IGNORECASE,
)
_FINANCE_QUERY_ACTION = re.compile(
    r"\b(?:сколько|покажи|сводка|итог(?:и)?|на что|куда|проанализируй|"
    r"по проекту|за месяц|за неделю|за сегодня)\b",
    re.IGNORECASE,
)


def looks_like_finance_question(message: str) -> bool:
    """Detect read-only finance questions without intercepting transaction statements."""
    text = (message or "").strip()
    if not text or looks_like_finance_entry(text):
        return False
    return bool(
        _FINANCE_QUERY_TOPIC.search(text)
        and (_FINANCE_QUERY_ACTION.search(text) or text.endswith("?"))
    )


def _norm(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).split())


def parse_finance_response(
    raw: str,
    today: date,
    projects: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate model JSON and resolve project titles against owner's project list."""
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Модель вернула некорректный JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("Ожидался JSON-объект")

    clarification = payload.get("clarification")
    if clarification:
        return {"transactions": [], "clarification": str(clarification)[:400]}

    rows = payload.get("transactions")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Не найдено ни одной финансовой операции")
    if len(rows) > 10:
        raise ValueError("За одно сообщение можно подготовить не более 10 операций")

    projects_by_title = {
        _norm(str(p.get("title", ""))): p
        for p in projects
        if p.get("title")
    }
    validated: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Операция №{index + 1} имеет неверный формат")

        kind = str(row.get("kind", "")).strip().lower()
        if kind not in {"income", "expense"}:
            raise ValueError(f"Не удалось определить тип операции №{index + 1}")

        try:
            amount = round(float(row.get("amount")), 2)
        except (TypeError, ValueError):
            raise ValueError(f"Не удалось определить сумму операции №{index + 1}")
        if not (0 < amount <= 100_000_000):
            raise ValueError(f"Сумма операции №{index + 1} вне допустимого диапазона")

        raw_date = str(row.get("date") or today.isoformat())
        try:
            tx_date = date.fromisoformat(raw_date)
        except ValueError:
            raise ValueError(f"Не удалось определить дату операции №{index + 1}")

        confidence = row.get("confidence", 1)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0
        if confidence < 0.75:
            question = row.get("needs_clarification") or f"Уточни операцию №{index + 1}."
            return {"transactions": [], "clarification": str(question)[:400]}

        category = str(row.get("category") or "").strip()
        if not category:
            return {
                "transactions": [],
                "clarification": f"К какой категории отнести операцию на {amount:g} ₽?",
            }
        category = category[:40]

        note = str(row.get("note") or "").strip()[:200] or None
        project_title = row.get("project_title")
        project_id = None
        resolved_project_title = None
        if project_title:
            key = _norm(str(project_title))
            project = projects_by_title.get(key)
            if not project:
                return {
                    "transactions": [],
                    "clarification": (
                        f"Не нашёл однозначно проект «{str(project_title)[:100]}». "
                        "Укажи точное название из /projects или напиши, что расход без проекта."
                    ),
                }
            project_id = int(project["id"])
            resolved_project_title = str(project["title"])

        validated.append({
            "kind": kind,
            "amount": amount,
            "category": category,
            "note": note,
            "tx_date": tx_date,
            "project_id": project_id,
            "project_title": resolved_project_title,
        })

    return {"transactions": validated, "clarification": None}
