"""Claim and format results addressed to the primary Telegram bot."""
from __future__ import annotations

import json
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
            .update({"status": "completed", "completed_at": "now()"})
            .eq("id", message_id)
            .eq("to_bot_id", self.bot_id)
            .eq("message_type", "result")
            .eq("status", "processing")
            .select("id")
            .execute()
        )
        return bool(getattr(response, "data", None))


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
        heading = f"Результат от {title}"
    else:
        body = {"error": payload.get("error", "worker_failed")}
        heading = f"Не удалось выполнить задачу у {title}"
    rendered = json.dumps(body, ensure_ascii=False, indent=2, default=str)
    return f"📨 {heading}\nЗадача: {task_type}\n\n{rendered}"
