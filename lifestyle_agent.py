"""Owner-scoped repository for personal goals and habits."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable


DbCall = Callable[[Callable[[], Any]], Awaitable[Any]]


def _owner_id(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("telegram_id должен быть положительным целым числом")
    return value


def _update_id(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("source_update_id должен быть положительным целым числом")
    return value


def _text(value: str, field: str, limit: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise ValueError(f"{field}: требуется текст от 1 до {limit} символов")
    return result


def _optional_text(value: str | None, limit: int) -> str | None:
    if value is None or not str(value).strip():
        return None
    result = str(value).strip()
    if len(result) > limit:
        raise ValueError(f"Текст должен быть не длиннее {limit} символов")
    return result


def _as_date(value: date | str | None, field: str, *, required: bool = False) -> str | None:
    if value is None or value == "":
        if required:
            raise ValueError(f"{field}: дата обязательна")
        return None
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field}: дата должна быть в формате YYYY-MM-DD") from exc


def _progress(value: Any) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Прогресс должен быть числом от 0 до 100") from exc
    if not result.is_finite() or result < 0 or result > 100:
        raise ValueError("Прогресс должен быть числом от 0 до 100")
    return result


def _rows(result: Any) -> list[dict]:
    data = getattr(result, "data", None)
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    return list(data)


class LifestyleStore:
    def __init__(self, supabase_client: Any, db_call: DbCall):
        self.client = supabase_client
        self.db_call = db_call

    async def list_goals(self, telegram_id: int, *, include_closed: bool = False) -> list[dict]:
        owner = _owner_id(telegram_id)
        query = (
            self.client.table("lifestyle_goals")
            .select("*")
            .eq("telegram_id", owner)
            .order("target_date")
        )
        if not include_closed:
            query = query.eq("status", "active")
        return _rows(await self.db_call(query.execute))

    async def get_goal(self, telegram_id: int, goal_id: int) -> dict | None:
        owner = _owner_id(telegram_id)
        if not isinstance(goal_id, int) or isinstance(goal_id, bool) or goal_id <= 0:
            raise ValueError("goal_id должен быть положительным целым числом")
        result = await self.db_call(
            self.client.table("lifestyle_goals")
            .select("*")
            .eq("telegram_id", owner)
            .eq("id", goal_id)
            .limit(1)
            .execute
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def get_goal_by_update(self, telegram_id: int, source_update_id: int) -> dict | None:
        owner = _owner_id(telegram_id)
        update_id = _update_id(source_update_id)
        result = await self.db_call(
            self.client.table("lifestyle_goals")
            .select("*")
            .eq("telegram_id", owner)
            .eq("source_update_id", update_id)
            .limit(1)
            .execute
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def create_goal(
        self,
        telegram_id: int,
        *,
        title: str,
        description: str | None,
        target_date: date | str | None,
        source_update_id: int,
    ) -> dict:
        owner = _owner_id(telegram_id)
        update_id = _update_id(source_update_id)
        payload = {
            "telegram_id": owner,
            "title": _text(title, "Название цели", 180),
            "description": _optional_text(description, 2000),
            "target_date": _as_date(target_date, "target_date"),
            "status": "active",
            "progress": "0",
            "source_update_id": update_id,
        }
        result = await self.db_call(
            self.client.table("lifestyle_goals")
            .upsert(payload, on_conflict="telegram_id,source_update_id", ignore_duplicates=True)
            .execute
        )
        rows = _rows(result)
        if rows:
            return rows[0]
        existing = await self.get_goal_by_update(owner, update_id)
        if not existing:
            raise RuntimeError("Не удалось получить созданную цель")
        for key in ("title", "description", "target_date"):
            if str(existing.get(key)) != str(payload.get(key)):
                raise ValueError("Этот Telegram update уже использован для другой цели")
        return existing

    async def update_goal_progress(
        self,
        telegram_id: int,
        goal_id: int,
        progress: Any,
        *,
        note: str | None,
        source_update_id: int,
    ) -> dict:
        owner = _owner_id(telegram_id)
        if not isinstance(goal_id, int) or isinstance(goal_id, bool) or goal_id <= 0:
            raise ValueError("goal_id должен быть положительным целым числом")
        update_id = _update_id(source_update_id)
        value = _progress(progress)
        result = await self.db_call(
            self.client.rpc(
                "record_lifestyle_goal_progress",
                {
                    "p_telegram_id": owner,
                    "p_goal_id": goal_id,
                    "p_progress": str(value),
                    "p_note": _optional_text(note, 2000),
                    "p_source_update_id": update_id,
                },
            ).execute
        )
        data = getattr(result, "data", None)
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            raise RuntimeError("RPC прогресса цели вернул неожиданный ответ")
        return data

    async def list_habits(self, telegram_id: int, *, active_only: bool = True) -> list[dict]:
        owner = _owner_id(telegram_id)
        query = (
            self.client.table("lifestyle_habits")
            .select("*")
            .eq("telegram_id", owner)
            .order("title")
        )
        if active_only:
            query = query.eq("is_active", True)
        return _rows(await self.db_call(query.execute))

    async def get_habit(self, telegram_id: int, habit_id: int) -> dict | None:
        owner = _owner_id(telegram_id)
        if not isinstance(habit_id, int) or isinstance(habit_id, bool) or habit_id <= 0:
            raise ValueError("habit_id должен быть положительным целым числом")
        result = await self.db_call(
            self.client.table("lifestyle_habits")
            .select("*")
            .eq("telegram_id", owner)
            .eq("id", habit_id)
            .limit(1)
            .execute
        )
        rows = _rows(result)
        return rows[0] if rows else None

    async def create_habit(
        self,
        telegram_id: int,
        *,
        title: str,
        description: str | None,
        target_per_week: int,
        start_date: date,
        source_update_id: int,
    ) -> dict:
        owner = _owner_id(telegram_id)
        update_id = _update_id(source_update_id)
        if not isinstance(target_per_week, int) or isinstance(target_per_week, bool) or not 1 <= target_per_week <= 7:
            raise ValueError("target_per_week должен быть от 1 до 7")
        if not isinstance(start_date, date):
            raise ValueError("start_date должен быть датой")
        payload = {
            "telegram_id": owner,
            "title": _text(title, "Название привычки", 180),
            "description": _optional_text(description, 2000),
            "target_per_week": target_per_week,
            "is_active": True,
            "start_date": start_date.isoformat(),
            "source_update_id": update_id,
        }
        result = await self.db_call(
            self.client.table("lifestyle_habits")
            .upsert(payload, on_conflict="telegram_id,source_update_id", ignore_duplicates=True)
            .execute
        )
        rows = _rows(result)
        if rows:
            return rows[0]
        existing = await self.db_call(
            self.client.table("lifestyle_habits")
            .select("*")
            .eq("telegram_id", owner)
            .eq("source_update_id", update_id)
            .limit(1)
            .execute
        )
        found = _rows(existing)
        if not found:
            raise RuntimeError("Не удалось получить созданную привычку")
        for key in ("title", "description", "target_per_week", "start_date"):
            if str(found[0].get(key)) != str(payload.get(key)):
                raise ValueError("Этот Telegram update уже использован для другой привычки")
        return found[0]

    async def log_habit(
        self,
        telegram_id: int,
        habit_id: int,
        *,
        log_date: date,
        status: str,
        note: str | None,
        source_update_id: int,
    ) -> dict:
        owner = _owner_id(telegram_id)
        update_id = _update_id(source_update_id)
        if not isinstance(habit_id, int) or isinstance(habit_id, bool) or habit_id <= 0:
            raise ValueError("habit_id должен быть положительным целым числом")
        if not isinstance(log_date, date):
            raise ValueError("log_date должен быть датой")
        if status not in {"done", "skipped"}:
            raise ValueError("status должен быть done или skipped")
        habit = await self.get_habit(owner, habit_id)
        if not habit or not habit.get("is_active"):
            raise ValueError("Активная привычка не найдена у этого владельца")
        payload = {
            "telegram_id": owner,
            "habit_id": habit_id,
            "log_date": log_date.isoformat(),
            "status": status,
            "note": _optional_text(note, 1000),
            "source_update_id": update_id,
        }
        result = await self.db_call(
            self.client.table("lifestyle_habit_logs")
            .upsert(
                payload,
                on_conflict="telegram_id,source_update_id",
                ignore_duplicates=True,
            )
            .execute
        )
        rows = _rows(result)
        if rows:
            return rows[0]
        existing = await self.db_call(
            self.client.table("lifestyle_habit_logs")
            .select("*")
            .eq("telegram_id", owner)
            .eq("source_update_id", update_id)
            .limit(1)
            .execute
        )
        found = _rows(existing)
        if not found:
            raise RuntimeError("Не удалось подтвердить запись привычки")
        expected = ("habit_id", "log_date", "status", "note")
        for key in expected:
            if str(found[0].get(key)) != str(payload.get(key)):
                raise ValueError("Этот Telegram update уже использован для другой отметки привычки")
        return found[0]

    async def list_habit_logs(
        self,
        telegram_id: int,
        *,
        start_date: date,
        end_date: date,
    ) -> list[dict]:
        owner = _owner_id(telegram_id)
        if start_date > end_date:
            raise ValueError("start_date не может быть позже end_date")
        result = await self.db_call(
            self.client.table("lifestyle_habit_logs")
            .select("*")
            .eq("telegram_id", owner)
            .gte("log_date", start_date.isoformat())
            .lte("log_date", end_date.isoformat())
            .order("log_date")
            .order("created_at")
            .order("id")
            .execute
        )
        return _rows(result)
