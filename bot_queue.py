"""Small wrapper for the Supabase multi-bot message queue.

The caller must supply a Supabase client authenticated as this bot with its own
JWT. Never pass a service_role client to a specialized worker.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from bot_identity import BotIdentity

BOT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MESSAGE_TYPES = {"task", "result", "status", "event", "approval_request"}
MAX_HOP_COUNT = 8


class BotQueueValidationError(ValueError):
    """Raised when an outgoing queue message is invalid."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BotQueueValidationError("Datetime values must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


class BotQueueClient:
    """Queue operations scoped to one bot identity.

    Database RLS is the security boundary. Local checks are defense-in-depth
    and do not replace database authorization.
    """

    def __init__(self, supabase_client: Any, identity: BotIdentity):
        if not isinstance(identity, BotIdentity):
            raise TypeError("identity must be a BotIdentity")
        if not isinstance(identity.bot_id, str) or not BOT_ID_RE.fullmatch(identity.bot_id):
            raise BotQueueValidationError("identity.bot_id is invalid")
        self._client = supabase_client
        self.identity = identity

    def enqueue(
        self,
        *,
        to_bot_id: str,
        message_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        task_id: str | None = None,
        parent_message_id: str | None = None,
        schema_version: int = 1,
        hop_count: int = 0,
        available_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Create one message; duplicate sender/key pairs are ignored safely."""
        if not isinstance(to_bot_id, str):
            raise BotQueueValidationError("to_bot_id must be a string")
        recipient = to_bot_id.strip().lower()
        if not BOT_ID_RE.fullmatch(recipient):
            raise BotQueueValidationError("Invalid to_bot_id")
        if not isinstance(message_type, str) or message_type not in MESSAGE_TYPES:
            raise BotQueueValidationError("Unsupported message_type")
        if not isinstance(payload, dict):
            raise BotQueueValidationError("payload must be a JSON object")
        try:
            json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise BotQueueValidationError("payload must contain only JSON-compatible values") from exc
        for field_name, field_value in (("task_id", task_id), ("parent_message_id", parent_message_id)):
            if field_value is not None:
                try:
                    UUID(str(field_value))
                except (TypeError, ValueError, AttributeError) as exc:
                    raise BotQueueValidationError(f"{field_name} must be a UUID") from exc
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.strip()) <= 200:
            raise BotQueueValidationError("idempotency_key must contain 1..200 characters")
        if not isinstance(schema_version, int) or isinstance(schema_version, bool) or not 1 <= schema_version <= 32767:
            raise BotQueueValidationError("schema_version must be between 1 and 32767")
        if not isinstance(hop_count, int) or isinstance(hop_count, bool) or not 0 <= hop_count <= MAX_HOP_COUNT:
            raise BotQueueValidationError(f"hop_count must be between 0 and {MAX_HOP_COUNT}")

        now = _utc_now()
        available = available_at or now
        expires = expires_at
        available_text = _iso(available)
        expires_text = _iso(expires) if expires is not None else None
        if expires is not None and expires <= now:
            raise BotQueueValidationError("expires_at must be in the future")
        if expires is not None and expires <= available:
            raise BotQueueValidationError("expires_at must be later than available_at")

        row = {
            "task_id": task_id or str(uuid4()),
            "parent_message_id": parent_message_id,
            "from_bot_id": self.identity.bot_id,
            "to_bot_id": recipient,
            "message_type": message_type,
            "payload": dict(payload),
            "schema_version": schema_version,
            "idempotency_key": idempotency_key.strip(),
            "hop_count": hop_count,
            "available_at": available_text,
            "expires_at": expires_text,
        }
        result = (
            self._client.table("bot_messages")
            .upsert(
                row,
                on_conflict="from_bot_id,idempotency_key",
                ignore_duplicates=True,
            )
            .execute()
        )
        rows = getattr(result, "data", None) or []
        return rows[0] if rows else None


    def claim_inbox(
        self,
        *,
        limit: int = 20,
        lease_seconds: int = 300,
        message_type: str = "task",
    ) -> list[dict[str, Any]]:
        """Atomically claim messages of one type for this bot.

        PostgreSQL uses FOR UPDATE SKIP LOCKED so concurrent workers cannot
        claim the same message. Expired leases are retried up to max_attempts.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise BotQueueValidationError("limit must be between 1 and 100")
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not 30 <= lease_seconds <= 3600
        ):
            raise BotQueueValidationError("lease_seconds must be between 30 and 3600")
        if not isinstance(message_type, str) or message_type not in MESSAGE_TYPES:
            raise BotQueueValidationError("Unsupported message_type")

        result = self._client.rpc(
            "claim_bot_messages",
            {
                "p_limit": limit,
                "p_lease_seconds": lease_seconds,
                "p_message_type": message_type,
            },
        ).execute()
        return list(getattr(result, "data", None) or [])

    def fetch_inbox(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Read pending, available messages addressed to this bot."""
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise BotQueueValidationError("limit must be between 1 and 100")
        result = (
            self._client.table("bot_messages")
            .select("*")
            .eq("to_bot_id", self.identity.bot_id)
            .eq("status", "pending")
            .lte("available_at", _iso(_utc_now()))
            .order("created_at")
            .limit(limit)
            .execute()
        )
        return list(getattr(result, "data", None) or [])

    def update_status(
        self,
        *,
        message_id: str,
        status: str,
        last_error: str | None = None,
    ) -> list[dict[str, Any]]:
        """Set a terminal status only for a currently claimed message addressed to this bot."""
        if not isinstance(status, str) or status not in {"completed", "failed", "cancelled"}:
            raise BotQueueValidationError("Only terminal statuses can be set directly; claim_inbox() sets processing")
        if not isinstance(message_id, str) or not message_id.strip():
            raise BotQueueValidationError("message_id must not be empty")

        now = _iso(_utc_now())
        update: dict[str, Any] = {"status": status}
        if status in {"completed", "failed", "cancelled"}:
            update["completed_at"] = now
        if last_error is not None:
            update["last_error"] = str(last_error)[:2000]

        result = (
            self._client.table("bot_messages")
            .update(update)
            .eq("id", message_id)
            .eq("to_bot_id", self.identity.bot_id)
            .eq("status", "processing")
            .select("id,status")
            .execute()
        )
        return list(getattr(result, "data", None) or [])
