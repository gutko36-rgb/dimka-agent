"""Standalone authenticated worker process for the multi-bot queue."""
from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Mapping
from typing import Any, Callable

from bot_identity import load_bot_identity
from bot_queue import BotQueueClient
from bot_worker import BotWorker
from finance_worker_handlers import build_finance_handlers


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def verify_worker_auth_user(user: Any, expected_bot_id: str) -> None:
    """Fail closed unless trusted app_metadata identifies this exact worker."""
    metadata = _field(user, "app_metadata") or {}
    actual = metadata.get("bot_id") if isinstance(metadata, Mapping) else None
    if actual != expected_bot_id:
        raise RuntimeError(
            f"Supabase Auth app_metadata.bot_id mismatch: expected {expected_bot_id!r}"
        )


def create_authenticated_queue(
    environ: Mapping[str, str] | None = None,
    *,
    client_factory: Callable[[str, str], Any] | None = None,
) -> tuple[BotQueueClient, Any]:
    source = os.environ if environ is None else environ
    identity = load_bot_identity(source)
    if identity.bot_id == "dimka":
        raise ValueError("The standalone worker must use a non-primary BOT_ID")

    url = source.get("SUPABASE_URL", "").strip()
    anon_key = source.get("SUPABASE_ANON_KEY", "").strip()
    email = source.get("BOT_AUTH_EMAIL", "").strip()
    password = source.get("BOT_AUTH_PASSWORD", "")
    if not all((url, anon_key, email, password)):
        raise ValueError(
            "SUPABASE_URL, SUPABASE_ANON_KEY, BOT_AUTH_EMAIL and BOT_AUTH_PASSWORD are required"
        )

    if client_factory is None:
        from supabase import create_client
        client_factory = create_client

    client = client_factory(url, anon_key)
    response = client.auth.sign_in_with_password({"email": email, "password": password})
    user = _field(response, "user")
    if user is None:
        raise RuntimeError("Supabase Auth sign-in did not return a user")
    verify_worker_auth_user(user, identity.bot_id)
    return BotQueueClient(client, identity), client


def build_worker(queue: BotQueueClient) -> BotWorker:
    """Build only the handlers explicitly allowed for this worker identity."""
    handlers = {"diagnostics.echo": lambda payload: {"echo": payload}}
    if queue.identity.bot_id == "finance":
        handlers.update(build_finance_handlers())
    return BotWorker(
        queue,
        handlers,
        batch_size=int(os.environ.get("BOT_WORKER_BATCH_SIZE", "10")),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one isolated Supabase queue worker")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if not 0.5 <= args.poll_seconds <= 300:
        parser.error("--poll-seconds must be between 0.5 and 300")

    try:
        from dotenv import load_dotenv
        load_dotenv('.env.worker', override=False)
        queue, client = create_authenticated_queue()
        worker = build_worker(queue)
    except Exception as exc:
        logging.error("Worker startup failed: %s: %s", type(exc).__name__, exc)
        return 1

    identity = queue.identity
    logging.info("Worker %s (%s) authenticated; starting queue processing", identity.bot_id, identity.role)
    try:
        while True:
            result = worker.run_once()
            if result.claimed:
                logging.info("Worker batch result: %s", result)
            if args.once:
                break
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        logging.info("Worker %s stopped", identity.bot_id)
    finally:
        try:
            client.auth.sign_out()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
