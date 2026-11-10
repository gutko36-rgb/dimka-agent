"""Framework for isolated, side-effect-free multi-bot task workers.

Handlers should compute a result only. Any external write or user-visible action
must be represented as a proposal for the main bot and require owner approval.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from bot_queue import MAX_HOP_COUNT, BotQueueClient


TaskHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class WorkerBatchResult:
    claimed: int
    completed: int
    failed: int
    deferred: int


class BotWorker:
    """Claims task messages, runs registered pure handlers, and replies."""

    def __init__(
        self,
        queue: BotQueueClient,
        handlers: dict[str, TaskHandler],
        *,
        batch_size: int = 10,
        lease_seconds: int = 300,
    ) -> None:
        if not isinstance(handlers, dict) or not handlers:
            raise ValueError("At least one task handler must be registered")
        if any(not isinstance(name, str) or not name.strip() or not callable(fn)
               for name, fn in handlers.items()):
            raise ValueError("Each handler needs a non-empty name and callable")
        if not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        self.queue = queue
        self.handlers = dict(handlers)
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds

    def run_once(self) -> WorkerBatchResult:
        messages = self.queue.claim_inbox(
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )
        completed = 0
        failed = 0
        deferred = 0

        for message in messages:
            try:
                task_type, task_input, sender, message_id, task_id, hop_count = (
                    self._validate_message(message)
                )
            except (TypeError, ValueError, KeyError):
                if self._mark_failed(message, "invalid_task_message"):
                    failed += 1
                else:
                    deferred += 1
                continue

            handler = self.handlers.get(task_type)
            if handler is None:
                if self._mark_failed(message, "unknown_task_type"):
                    failed += 1
                else:
                    deferred += 1
                continue

            if hop_count >= MAX_HOP_COUNT:
                if self._mark_failed(message, "hop_limit_exceeded"):
                    failed += 1
                else:
                    deferred += 1
                continue

            try:
                result_payload = handler(task_input)
                if not isinstance(result_payload, dict):
                    raise ValueError("Handler result must be a JSON object")
                json.dumps(result_payload, allow_nan=False)
            except Exception:
                if self._mark_failed(message, "handler_error"):
                    failed += 1
                else:
                    deferred += 1
                continue

            try:
                # The sender-scoped unique key makes retry after a worker crash safe.
                # If the result already exists, enqueue() ignores the duplicate.
                self.queue.enqueue(
                    to_bot_id=sender,
                    message_type="result",
                    payload={
                        "task_type": task_type,
                        "ok": True,
                        "result": result_payload,
                    },
                    idempotency_key=f"result:{message_id}",
                    task_id=task_id,
                    parent_message_id=message_id,
                    hop_count=hop_count + 1,
                )
                self.queue.update_status(
                    message_id=message_id,
                    status="completed",
                )
                completed += 1
            except Exception:
                # Leave the lease in place. A later claim retries the task, and the
                # deterministic result idempotency key prevents duplicate replies.
                deferred += 1

        return WorkerBatchResult(
            claimed=len(messages),
            completed=completed,
            failed=failed,
            deferred=deferred,
        )

    @staticmethod
    def _validate_message(message: dict[str, Any]) -> tuple[str, dict[str, Any], str, str, str, int]:
        if not isinstance(message, dict) or message.get("message_type") != "task":
            raise ValueError("Only task messages are accepted")

        message_id = str(UUID(str(message["id"])))
        task_id = str(UUID(str(message["task_id"])))
        sender = message["from_bot_id"]
        if not isinstance(sender, str) or not sender.strip():
            raise ValueError("Missing sender")

        hop_count = message.get("hop_count", 0)
        if not isinstance(hop_count, int) or isinstance(hop_count, bool) or hop_count < 0:
            raise ValueError("Invalid hop count")

        payload = message.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("Payload must be an object")
        task_type = payload.get("task_type")
        task_input = payload.get("input")
        if not isinstance(task_type, str) or not task_type.strip():
            raise ValueError("Missing task type")
        if not isinstance(task_input, dict):
            raise ValueError("Task input must be an object")

        return task_type.strip(), task_input, sender, message_id, task_id, hop_count

    def _mark_failed(self, message: dict[str, Any], reason: str) -> bool:
        message_id = message.get("id") if isinstance(message, dict) else None
        if not isinstance(message_id, str) or not message_id.strip():
            return False
        try:
            self.queue.update_status(
                message_id=message_id,
                status="failed",
                last_error=reason,
            )
            return True
        except Exception:
            return False
