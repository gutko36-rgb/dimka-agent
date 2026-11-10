"""Owner-scoped repository for debts and monthly budgets.

The repository is deliberately injected with a Supabase client and async db_call
wrapper so it can be tested without network access or bot.py imports.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Awaitable, Callable, Mapping

from finance_planning import money


DbCall = Callable[[Callable[[], Any]], Awaitable[Any]]


def _owner_id(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("telegram_id должен быть положительным целым числом")
    return value


def _update_id(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("source_update_id должен быть положительным целым числом")
    return value


def _required_text(value: str, field: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > max_length:
        raise ValueError(f"{field}: требуется текст от 1 до {max_length} символов")
    return text


def _due_date(value: date | str | None) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError("due_date должен быть датой YYYY-MM-DD") from exc


def _positive_amount(value: Any) -> Decimal:
    amount = money(value)
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля")
    return amount


def _response_rows(result: Any) -> list[dict]:
    data = getattr(result, "data", None)
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    return list(data)


class FinanceDebtStore:
    def __init__(self, supabase_client: Any, db_call: DbCall):
        self.client = supabase_client
        self.db_call = db_call

    async def list_debts(self, telegram_id: int, *, include_closed: bool = False) -> list[dict]:
        owner = _owner_id(telegram_id)
        query = (
            self.client.table("finance_debts")
            .select("*")
            .eq("telegram_id", owner)
            .order("due_date")
        )
        if not include_closed:
            query = query.eq("status", "open")
        result = await self.db_call(query.execute)
        return _response_rows(result)

    async def get_debt(self, telegram_id: int, debt_id: int) -> dict | None:
        owner = _owner_id(telegram_id)
        if not isinstance(debt_id, int) or isinstance(debt_id, bool) or debt_id <= 0:
            raise ValueError("debt_id должен быть положительным целым числом")
        result = await self.db_call(
            self.client.table("finance_debts")
            .select("*")
            .eq("telegram_id", owner)
            .eq("id", debt_id)
            .limit(1)
            .execute
        )
        rows = _response_rows(result)
        return rows[0] if rows else None

    async def create_debt(
        self,
        telegram_id: int,
        *,
        direction: str,
        counterparty: str,
        title: str,
        amount: Any,
        due_date: date | str | None,
        source_update_id: int,
        note: str | None = None,
    ) -> dict:
        owner = _owner_id(telegram_id)
        update_id = _update_id(source_update_id)
        if direction not in {"owed_by_me", "owed_to_me"}:
            raise ValueError("direction должен быть owed_by_me или owed_to_me")
        value = _positive_amount(amount)
        payload = {
            "telegram_id": owner,
            "direction": direction,
            "counterparty": _required_text(counterparty, "counterparty", 120),
            "title": _required_text(title, "title", 180),
            "amount": str(value),
            "amount_remaining": str(value),
            "due_date": _due_date(due_date),
            "status": "open",
            "note": str(note).strip()[:1000] if note else None,
            "source_update_id": update_id,
        }
        result = await self.db_call(
            self.client.table("finance_debts")
            .upsert(
                payload,
                on_conflict="telegram_id,source_update_id",
                ignore_duplicates=True,
            )
            .execute
        )
        rows = _response_rows(result)
        if rows:
            return rows[0]

        existing = await self.get_debt_by_update(owner, update_id)
        if not existing:
            raise RuntimeError("Не удалось получить созданную запись долга")
        expected = ("direction", "counterparty", "title", "amount", "due_date", "note")
        for key in expected:
            if str(existing.get(key)) != str(payload.get(key)):
                raise ValueError("Этот Telegram update уже использован для другого долга")
        return existing

    async def get_debt_by_update(self, telegram_id: int, source_update_id: int) -> dict | None:
        owner = _owner_id(telegram_id)
        update_id = _update_id(source_update_id)
        result = await self.db_call(
            self.client.table("finance_debts")
            .select("*")
            .eq("telegram_id", owner)
            .eq("source_update_id", update_id)
            .limit(1)
            .execute
        )
        rows = _response_rows(result)
        return rows[0] if rows else None

    async def record_payment(
        self,
        telegram_id: int,
        debt_id: int,
        amount: Any,
        *,
        payment_date: date,
        source_update_id: int,
    ) -> dict:
        owner = _owner_id(telegram_id)
        if not isinstance(debt_id, int) or isinstance(debt_id, bool) or debt_id <= 0:
            raise ValueError("debt_id должен быть положительным целым числом")
        value = _positive_amount(amount)
        if not isinstance(payment_date, date):
            raise ValueError("payment_date должен быть datetime.date")
        update_id = _update_id(source_update_id)
        result = await self.db_call(
            self.client.rpc(
                "record_finance_debt_payment",
                {
                    "p_telegram_id": owner,
                    "p_debt_id": debt_id,
                    "p_amount": str(value),
                    "p_payment_date": payment_date.isoformat(),
                    "p_source_update_id": update_id,
                },
            ).execute
        )
        data = getattr(result, "data", None)
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            raise RuntimeError("RPC погашения долга вернул неожиданный ответ")
        return data

    async def list_budgets(self, telegram_id: int, period_month: date) -> list[dict]:
        owner = _owner_id(telegram_id)
        month = date(period_month.year, period_month.month, 1)
        result = await self.db_call(
            self.client.table("finance_budgets")
            .select("*")
            .eq("telegram_id", owner)
            .eq("period_month", month.isoformat())
            .order("category")
            .execute
        )
        return _response_rows(result)

    async def set_budget(
        self,
        telegram_id: int,
        *,
        period_month: date,
        category: str,
        limit_amount: Any,
    ) -> dict | None:
        owner = _owner_id(telegram_id)
        month = date(period_month.year, period_month.month, 1)
        limit_value = money(limit_amount)
        if limit_value < 0:
            raise ValueError("Лимит бюджета не может быть отрицательным")
        payload = {
            "telegram_id": owner,
            "period_month": month.isoformat(),
            "category": _required_text(category, "category", 80),
            "limit_amount": str(limit_value),
        }
        result = await self.db_call(
            self.client.table("finance_budgets")
            .upsert(
                payload,
                on_conflict="telegram_id,period_month,category",
            )
            .execute
        )
        rows = _response_rows(result)
        return rows[0] if rows else None

    async def list_weights(self, telegram_id: int) -> list[dict]:
        owner = _owner_id(telegram_id)
        result = await self.db_call(
            self.client.table("finance_budget_weights")
            .select("*")
            .eq("telegram_id", owner)
            .order("category")
            .execute
        )
        return _response_rows(result)

    async def set_weight(self, telegram_id: int, *, category: str, weight: Any) -> dict | None:
        owner = _owner_id(telegram_id)
        value = money(weight)
        if value < 0:
            raise ValueError("Вес категории не может быть отрицательным")
        payload = {
            "telegram_id": owner,
            "category": _required_text(category, "category", 80),
            "weight": str(value),
        }
        result = await self.db_call(
            self.client.table("finance_budget_weights")
            .upsert(payload, on_conflict="telegram_id,category")
            .execute
        )
        rows = _response_rows(result)
        return rows[0] if rows else None
