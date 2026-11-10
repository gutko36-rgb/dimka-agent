"""Pure, read-only project summary handlers for the isolated projects worker."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any


MAX_PROJECT_ITEMS = 500


def _as_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc


def _text(value: Any, field: str, *, limit: int = 1000) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    return value.strip()[:limit] or None


def project_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic status snapshot from owner-scoped project data."""
    project = payload.get("project")
    items = payload.get("items")
    if not isinstance(project, dict):
        raise ValueError("project must be an object")
    if not isinstance(items, list) or len(items) > MAX_PROJECT_ITEMS:
        raise ValueError(f"items must be a list with at most {MAX_PROJECT_ITEMS} rows")

    title = _text(project.get("title"), "project.title", limit=120)
    if not title:
        raise ValueError("project.title is required")
    status = str(project.get("status") or "").strip().lower()
    if status not in {"idea", "active", "paused", "done"}:
        raise ValueError("project.status is invalid")
    today = _as_date(payload.get("today"), "today")
    deadline = _as_date(project["deadline"], "project.deadline") if project.get("deadline") else None

    stages: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []
    counts = {
        "stage": {"done": 0, "total": 0},
        "task": {"done": 0, "total": 0},
    }

    for row in items:
        if not isinstance(row, dict):
            raise ValueError("every project item must be an object")
        kind = str(row.get("kind") or "").strip().lower()
        if kind not in {"stage", "task", "note"}:
            raise ValueError("project item kind is invalid")
        content = _text(row.get("content"), "item.content", limit=1000)
        if not content:
            raise ValueError("project item content is required")
        done = row.get("done", False)
        if not isinstance(done, bool):
            raise ValueError("item.done must be boolean")
        due_date = _as_date(row["due_date"], "item.due_date") if row.get("due_date") else None
        item = {
            "id": row.get("id"),
            "kind": kind,
            "content": content,
            "due_date": due_date.isoformat() if due_date else None,
            "done": done,
            "created_at": str(row.get("created_at") or "")[:30],
        }
        if kind == "note":
            notes.append(item)
            continue

        counts[kind]["total"] += 1
        if done:
            counts[kind]["done"] += 1
        if due_date and not done:
            if due_date < today:
                overdue.append(item)
            elif due_date <= today + timedelta(days=14):
                upcoming.append(item)
        if kind == "stage":
            stages.append(item)
        else:
            tasks.append(item)

    notes.sort(key=lambda item: item["created_at"])
    overdue.sort(key=lambda item: (item["due_date"], item["kind"], item["id"] or 0))
    upcoming.sort(key=lambda item: (item["due_date"], item["kind"], item["id"] or 0))

    return {
        "project_id": project.get("id"),
        "title": title,
        "status": status,
        "deadline": deadline.isoformat() if deadline else None,
        "project_overdue": bool(deadline and deadline < today and status != "done"),
        "goal": _text(project.get("goal"), "project.goal"),
        "description": _text(project.get("description"), "project.description"),
        "result": _text(project.get("result"), "project.result"),
        "stage_progress": counts["stage"],
        "task_progress": counts["task"],
        "overdue_items": overdue[:50],
        "upcoming_items": upcoming[:50],
        "stages": stages[:100],
        "tasks": tasks[:20],
        "remaining_task_count": max(0, len(tasks) - 20),
        "notes": notes[-5:],
        "today": today.isoformat(),
    }


def build_project_handlers() -> dict[str, Any]:
    """Return the allowlisted, side-effect-free project task handlers."""
    return {"projects.summary": project_summary}
