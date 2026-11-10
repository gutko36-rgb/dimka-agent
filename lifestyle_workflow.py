"""Pure parsing and presentation helpers for goals and habits."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


def _fields(value: str, allowed: tuple[int, ...]) -> list[str]:
    if not isinstance(value, str):
        raise ValueError("Аргументы должны быть текстом с разделителями |")
    parts = [part.strip() for part in value.split("|")]
    if len(parts) not in allowed or any(not part for part in parts):
        raise ValueError("Проверь формат: разделяй поля символом |")
    return parts


def _text(value: str, field: str, limit: int) -> str:
    result = value.strip()
    if not result or len(result) > limit:
        raise ValueError(f"{field}: требуется текст от 1 до {limit} символов")
    return result


def _optional(value: str, limit: int) -> str | None:
    if value == "-":
        return None
    return _text(value, "Текст", limit)


def _date(value: str) -> date | None:
    if value == "-":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Срок укажи как YYYY-MM-DD или -") from exc


def parse_goal_create(value: str) -> dict[str, Any]:
    fields = _fields(value, (2, 3))
    return {
        "title": _text(fields[0], "Название цели", 180),
        "description": _optional(fields[1], 2000),
        "target_date": _date(fields[2]) if len(fields) == 3 else None,
    }


def parse_goal_progress(value: str) -> dict[str, Any]:
    fields = _fields(value, (2, 3))
    if not fields[0].isdigit() or int(fields[0]) <= 0:
        raise ValueError("Первым полем укажи номер цели")
    try:
        progress = Decimal(fields[1].replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Прогресс должен быть числом от 0 до 100") from exc
    if not progress.is_finite() or not 0 <= progress <= 100:
        raise ValueError("Прогресс должен быть числом от 0 до 100")
    note = _optional(fields[2], 2000) if len(fields) == 3 else None
    return {"goal_id": int(fields[0]), "progress": progress, "note": note}


def parse_habit_create(value: str) -> dict[str, Any]:
    fields = _fields(value, (2, 3))
    if not fields[1].isdigit() or not 1 <= int(fields[1]) <= 7:
        raise ValueError("Целевую частоту укажи числом от 1 до 7 раз в неделю")
    description = _optional(fields[2], 2000) if len(fields) == 3 else None
    return {
        "title": _text(fields[0], "Название привычки", 180),
        "target_per_week": int(fields[1]),
        "description": description,
    }


def parse_habit_log(value: str) -> dict[str, Any]:
    fields = _fields(value, (2, 3))
    if not fields[0].isdigit() or int(fields[0]) <= 0:
        raise ValueError("Первым полем укажи номер привычки")
    status = fields[1].lower()
    if status not in {"done", "skipped"}:
        raise ValueError("Статус должен быть done (выполнено) или skipped (пропуск)")
    note = _optional(fields[2], 1000) if len(fields) == 3 else None
    return {"habit_id": int(fields[0]), "status": status, "note": note}


def render_goals(rows: Iterable[Mapping[str, Any]]) -> str:
    items = list(rows)
    if not items:
        return "Активных целей пока нет. Добавь первую командой /goal_add."
    lines = ["Твои активные цели:", ""]
    for row in items:
        due = row.get("target_date") or "без срока"
        lines.append(
            f"№{row['id']} · {row['title']}\n"
            f"Прогресс: {row.get('progress', 0)}% · срок: {due}"
        )
    return "\n".join(lines)


def render_habits(rows: Iterable[Mapping[str, Any]]) -> str:
    items = list(rows)
    if not items:
        return "Активных привычек пока нет. Добавь первую командой /habit_add."
    lines = ["Твои активные привычки:", ""]
    for row in items:
        lines.append(
            f"№{row['id']} · {row['title']}\n"
            f"Цель: {row['target_per_week']} раз(а) в неделю"
        )
    return "\n".join(lines)


def render_habit_history(
    habits: Iterable[Mapping[str, Any]],
    logs: Iterable[Mapping[str, Any]],
    *,
    start_date: date,
    end_date: date,
) -> str:
    habit_rows = list(habits)
    log_rows = list(logs)
    if not habit_rows:
        return "Активных привычек пока нет."
    lines = [f"Привычки за {start_date.isoformat()} — {end_date.isoformat()}:", ""]
    latest_by_day: dict[tuple[Any, Any], Mapping[str, Any]] = {}
    ordered_logs = sorted(
        log_rows,
        key=lambda row: (
            str(row.get("log_date") or ""),
            str(row.get("created_at") or ""),
            int(row.get("id") or 0),
        ),
    )
    for row in ordered_logs:
        latest_by_day[(row.get("habit_id"), row.get("log_date"))] = row

    for habit in habit_rows:
        records = [
            row for (habit_id, _log_date), row in latest_by_day.items()
            if habit_id == habit.get("id")
        ]
        done = sum(1 for row in records if row.get("status") == "done")
        skipped = sum(1 for row in records if row.get("status") == "skipped")
        lines.append(
            f"• {habit['title']}: выполнено {done}, пропущено {skipped} "
            f"из цели {habit['target_per_week']} в неделю"
        )
    return "\n".join(lines)
