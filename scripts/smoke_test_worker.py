"""Send a diagnostics.echo task and wait for the isolated worker's result.

Run this only on a trusted administrator machine with the service-role key.
"""
from __future__ import annotations

import json
import os
import sys
import time
from uuid import uuid4

from bot_identity import BotIdentity
from bot_queue import BotQueueClient


def main() -> int:
    url = os.environ.get("SUPABASE_URL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    worker_id = os.environ.get("SMOKE_TEST_WORKER_ID", "finance").strip().lower()
    if not url or not service_key:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY on this trusted admin machine.", file=sys.stderr)
        return 2

    try:
        from supabase import create_client
    except ImportError:
        print("Install project dependencies first: pip install -r requirements.txt", file=sys.stderr)
        return 2

    admin = create_client(url, service_key)
    queue = BotQueueClient(
        admin,
        BotIdentity("dimka", "personal_assistant", "Димка"),
    )
    key = f"smoke-test:{uuid4()}"
    payload = {"task_type": "diagnostics.echo", "input": {"probe": key, "message": "queue round-trip"}}
    try:
        queue.enqueue(
            to_bot_id=worker_id,
            message_type="task",
            payload=payload,
            idempotency_key=key,
        )
        inserted = (
            admin.table("bot_messages")
            .select("id")
            .eq("from_bot_id", "dimka")
            .eq("idempotency_key", key)
            .limit(1)
            .execute()
        )
        rows = getattr(inserted, "data", None) or []
        if not rows:
            raise RuntimeError("Task was not visible after enqueue")
        message_id = rows[0]["id"]

        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            response = (
                admin.table("bot_messages")
                .select("status,payload")
                .eq("parent_message_id", message_id)
                .eq("from_bot_id", worker_id)
                .eq("to_bot_id", "dimka")
                .eq("message_type", "result")
                .limit(1)
                .execute()
            )
            results = getattr(response, "data", None) or []
            if results:
                print(json.dumps(results[0], ensure_ascii=False, indent=2, default=str))
                return 0 if results[0].get("payload", {}).get("ok") is True else 1
            time.sleep(2)

        raise TimeoutError("No worker result arrived within 90 seconds")
    except Exception as exc:
        print(f"Smoke test failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
