"""Claim and format results addressed to the primary Telegram bot."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


class BotResultInbox:
    """Uses the trusted primary backend client; workers never receive this client."""

    def __init__(self, supabase_client: Any, *, bot_id: str = "dimka") -> None:
        if bot_id != "dimka":
            raise ValueError("The primary result inbox is restricted to dimka")
        self._client = supabase_client
        self.bot_id = bot_id

    def claim(self, *, limit: int = 10, lease_seconds: int = 300) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or not 30 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        response = self._client.rpc(
            "claim_bot_results_for_primary",
            {"p_limit": limit, "p_lease_seconds": lease_seconds},
        ).execute()
        return list(getattr(response, "data", None) or [])

    def complete(self, message_id: str) -> bool:
        response = (
            self._client.table("bot_messages")
            .update({"status": "completed", "completed_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", message_id)
            .eq("to_bot_id", self.bot_id)
            .eq("message_type", "result")
            .eq("status", "processing")
            .select("id")
            .execute()
        )
        return bool(getattr(response, "data", None))


def _format_finance_result(task_type: str, body: dict[str, Any]) -> str | None:
    if task_type == "finance.summary":
        lines = ["📊 Финансовый агент", f"Период: {body.get('period_label', 'по запросу')}"]
        if body.get("project_title"):
            lines.append(f"Проект: {str(body['project_title'])[:120]}")
        terms = body.get("search_terms")
        if isinstance(terms, list) and terms:
            lines.append("Поиск: " + ", ".join(str(term)[:40] for term in terms[:5]))
        kind = body.get("kind_filter")
        if kind == "expense":
            lines.append(f"Расходы: {body.get('expense', '0.00')} ₽")
        elif kind == "income":
            lines.append(f"Доходы: {body.get('income', '0.00')} ₽")
        else:
            lines.extend([
                f"Доходы: {body.get('income', '0.00')} ₽",
                f"Расходы: {body.get('expense', '0.00')} ₽",
                f"Разница: {body.get('difference', '0.00')} ₽",
            ])
        categories = body.get("expense_categories")
        if isinstance(categories, list) and categories:
            lines.extend(["", "Расходы по категориям:"])
            for item in categories[:6]:
                if isinstance(item, dict):
                    lines.append(f"- {str(item.get('category', 'Без категории'))[:60]}: {item.get('amount', '0.00')} ₽")
        if not body.get("count"):
            lines.extend(["", "Подходящих операций в базе не найдено."])
        else:
            lines.extend(["", f"Операций найдено: {body['count']}."])
        lines.extend(["", "Это сумма сохранённых записей, а не фактический остаток банковского счёта."])
        return "\n".join(lines)

    if task_type == "finance.forecast":
        lines = [
            "📈 Прогноз от финансового агента",
            f"На дату: {body.get('as_of', '—')}",
            f"Текущий расчётный остаток: {body.get('current_balance', '—')} ₽",
            f"Прогноз на {body.get('until', '—')}: {body.get('projected_balance', '—')} ₽",
        ]
        events = body.get("events")
        if isinstance(events, list) and events:
            lines.extend(["", "Ожидаемые события:"])
            for event in events[:20]:
                if isinstance(event, dict):
                    lines.append(f"- {event.get('date', '—')} · {str(event.get('label', 'Событие'))[:80]}: {event.get('amount', '0.00')} ₽")
        if body.get("note"):
            lines.extend(["", str(body["note"])[:250]])
        return "\n".join(lines)

    if task_type == "finance.budget_analysis":
        lines = [f"📋 Бюджет за {body.get('month', 'указанный месяц')}"]
        categories = body.get("categories")
        for item in categories[:30] if isinstance(categories, list) else []:
            if isinstance(item, dict):
                status = " · ПРЕВЫШЕН" if item.get("over_budget") else ""
                limit = item.get("limit")
                suffix = f" / {limit} ₽" if limit is not None else ""
                lines.append(f"- {str(item.get('category', 'Категория'))[:60]}: {item.get('spent', '0.00')} ₽{suffix}{status}")
        return "\n".join(lines)

    if task_type == "finance.distribution":
        lines = [
            "🧮 Распределение от финансового агента",
            f"Доступно после обязательных сумм: {body.get('available', '0.00')} ₽",
            f"Дефицит: {body.get('deficit', '0.00')} ₽",
        ]
        allocations = body.get("allocations")
        if isinstance(allocations, dict):
            lines.extend(["", "Распределение:"])
            for name, amount in list(allocations.items())[:30]:
                lines.append(f"- {str(name)[:60]}: {amount} ₽")
        if body.get("note"):
            lines.extend(["", str(body["note"])[:250]])
        return "\n".join(lines)
    return None


def format_result_message(message: dict[str, Any]) -> str:
    if not isinstance(message, dict) or message.get("message_type") != "result":
        raise ValueError("Only result messages can be formatted")
    sender = message.get("from_bot_id")
    payload = message.get("payload")
    if not isinstance(sender, str) or not isinstance(payload, dict):
        raise ValueError("Malformed result message")
    task_type = payload.get("task_type", "unknown")
    if not isinstance(task_type, str):
        task_type = "unknown"
    title = {"finance": "финансового агента", "projects": "агента проектов"}.get(
        sender, f"агента {sender}"
    )
    if payload.get("ok") is True:
        body = payload.get("result", {})
        if sender == "finance" and isinstance(body, dict):
            formatted = _format_finance_result(task_type, body)
            if formatted is not None:
                return formatted
        heading = f"Результат от {title}"
    else:
        body = {"error": payload.get("error", "worker_failed")}
        heading = f"Не удалось выполнить задачу у {title}"
    rendered = json.dumps(body, ensure_ascii=False, indent=2, default=str)
    return f"📨 {heading}\nЗадача: {task_type}\n\n{rendered}"
