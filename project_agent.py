"""Безопасное извлечение действий по проектам из обычного русского текста.

Модуль не обращается к БД и не выполняет запись. Он валидирует JSON модели,
разрешает проекты только по точному списку владельца и возвращает черновик.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any


ACTIONS = {"create_project", "add_stage", "add_task", "add_note", "update_project", "project_query"}
PROJECT_FIELDS = {"description", "goal", "status", "deadline", "result"}
PROJECT_STATUSES = {"idea", "active", "paused", "done"}

_WRITE_HINT = re.compile(
    r"\b(?:создай|создать|добавь|добавить|запиши|записать|зафиксируй|"
    r"сохрани|сохранить|поставь|поставить|обнови|измени|изменить|"
    r"перенеси|перенести|отметь|отметить)\b",
    re.IGNORECASE,
)
_READ_HINT = re.compile(
    r"\b(?:проект(?:ы|а|е|у|ом)?|этап(?:ы|а|е|у|ов)?|задач(?:а|и|у|ей)?|"
    r"заметк(?:а|и|у|е)|материал(?:ы|ов)?|ход работ|прогресс)\b",
    re.IGNORECASE,
)
_QUESTION_HINT = re.compile(
    r"\b(?:как|что|какие|сколько|покажи|расскажи|статус|сроки|"
    r"прогресс|готово|осталось)\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:не надо|не добавляй|не создавай|не записывай|не сохраняй)\b", re.IGNORECASE)


def looks_like_project_intent(message: str) -> bool:
    """Detect explicit project-related requests without intercepting ordinary chat."""
    text = (message or "").strip()
    if not text or _NEGATION.search(text):
        return False
    if not _READ_HINT.search(text):
        return False
    # "Сколько задач на сегодня?" относится к Todoist, а не к проектам.
    project_context = re.search(
        r"\b(?:проект(?:ы|а|е|у|ом)?|этап(?:ы|а|е|у|ов)?|заметк(?:а|и|у|е)|"
        r"материал(?:ы|ов)?|ход работ|прогресс)\b",
        text,
        re.IGNORECASE,
    )
    if not project_context:
        return False
    return bool(_WRITE_HINT.search(text) or _QUESTION_HINT.search(text) or text.endswith("?"))


def _norm(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).split())


def parse_project_response(
    raw: str,
    today: date,
    projects: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate model JSON and resolve project references against the owner's project list."""
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Модель вернула некорректный JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("Ожидался JSON-объект")

    clarification = payload.get("clarification")
    if clarification:
        return {"action": None, "clarification": str(clarification)[:400]}

    action = str(payload.get("action") or "").strip()
    if action not in ACTIONS:
        raise ValueError("Неизвестное действие проекта")

    try:
        confidence = float(payload.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    if confidence < 0.75:
        return {
            "action": None,
            "clarification": str(payload.get("question") or "Уточни, что именно нужно сделать с проектом.")[:400],
        }

    projects_by_title = {_norm(str(p.get("title", ""))): p for p in projects if p.get("title")}
    project_title = str(payload.get("project_title") or "").strip()
    project = None
    if action != "create_project" and project_title:
        project = projects_by_title.get(_norm(project_title))
        if not project:
            return {
                "action": None,
                "clarification": (
                    f"Не нашёл точное название проекта «{project_title[:100]}». "
                    "Проверь название в /projects или уточни его."
                ),
            }
    elif action not in {"create_project", "project_query"}:
        return {"action": None, "clarification": "Уточни, к какому проекту относится запись."}

    result: dict[str, Any] = {
        "action": action,
        "project_id": int(project["id"]) if project else None,
        "project_title": str(project["title"]) if project else None,
        "clarification": None,
    }

    if action == "create_project":
        title = str(payload.get("title") or "").strip()
        if not title:
            return {"action": None, "clarification": "Как назвать новый проект?"}
        if len(title) > 100:
            raise ValueError("Название проекта слишком длинное")
        if _norm(title) in projects_by_title:
            return {"action": None, "clarification": f"Проект «{title}» уже существует. Выбери его в /projects."}
        result["title"] = title
        result["description"] = str(payload.get("description") or "").strip()[:1000] or None
        result["goal"] = str(payload.get("goal") or "").strip()[:1000] or None

    elif action in {"add_stage", "add_task", "add_note"}:
        content = str(payload.get("content") or "").strip()
        if not content:
            return {"action": None, "clarification": "Что именно записать в проект?"}
        if len(content) > 1000:
            raise ValueError("Текст проектной записи слишком длинный")
        result["content"] = content
        raw_due = str(payload.get("due_date") or "").strip()
        if raw_due:
            try:
                due = date.fromisoformat(raw_due)
            except ValueError as exc:
                raise ValueError("Некорректная дата этапа или задачи") from exc
            result["due_date"] = due
        else:
            result["due_date"] = None

    elif action == "update_project":
        field = str(payload.get("field") or "").strip()
        if field not in PROJECT_FIELDS:
            raise ValueError("Недопустимое поле проекта")
        value = str(payload.get("value") or "").strip()
        clear_field = payload.get("clear") is True
        if clear_field and field not in {"description", "goal", "deadline", "result"}:
            raise ValueError("Это поле нельзя очистить")
        if not clear_field and not value:
            return {"action": None, "clarification": "Уточни новое значение поля. Если нужно очистить его, скажи об этом явно."}
        if clear_field:
            value = ""
        if field == "status":
            aliases = {
                "идея": "idea", "в работе": "active", "активный": "active",
                "пауза": "paused", "на паузе": "paused", "завершён": "done",
                "завершен": "done", "готово": "done",
            }
            value = aliases.get(value.casefold(), value.casefold())
            if value not in PROJECT_STATUSES:
                return {"action": None, "clarification": "Статус проекта: идея, в работе, пауза или завершён."}
        elif field == "deadline":
            if value:
                try:
                    value = date.fromisoformat(value).isoformat()
                except ValueError as exc:
                    raise ValueError("Срок нужно вернуть в формате ГГГГ-ММ-ДД") from exc
        elif field in {"description", "goal", "result"}:
            value = value[:1000]
        result["field"] = field
        result["value"] = None if clear_field else value

    return result
