"""Provision one isolated Supabase Auth identity for a multi-bot worker.

Run only on a trusted administrator machine. The service-role key is used here
once to create the Auth user and registry row; it is never written to the worker
environment or printed.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
from typing import Any

BOT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def provision_bot_identity(
    client: Any,
    *,
    bot_id: str,
    role: str,
    display_name: str,
    email: str,
    password: str,
) -> str:
    """Create an inactive registry row, trusted Auth user, then activate the bot."""
    bot_id = bot_id.strip().lower()
    if not BOT_ID_RE.fullmatch(bot_id) or bot_id == "dimka":
        raise ValueError("bot_id must be a valid non-primary worker ID")
    if not role.strip() or len(role.strip()) > 80:
        raise ValueError("role must contain 1..80 characters")
    if not display_name.strip() or len(display_name.strip()) > 100:
        raise ValueError("display_name must contain 1..100 characters")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
        raise ValueError("A valid email address is required")
    if len(password) < 24:
        raise ValueError("Worker password must contain at least 24 characters")

    existing = (
        client.table("bot_registry")
        .select("bot_id")
        .eq("bot_id", bot_id)
        .limit(1)
        .execute()
    )
    if getattr(existing, "data", None):
        raise RuntimeError(f"Registry entry {bot_id!r} already exists; inspect it before retrying")

    client.table("bot_registry").insert({
        "bot_id": bot_id,
        "role": role.strip(),
        "display_name": display_name.strip(),
        "is_active": False,
    }).execute()

    user_id = None
    try:
        response = client.auth.admin.create_user({
            "email": email.strip(),
            "password": password,
            "email_confirm": True,
            "app_metadata": {"bot_id": bot_id},
        })
        user = getattr(response, "user", None)
        if user is None and isinstance(response, dict):
            user = response.get("user", response)
        user_id = getattr(user, "id", None)
        if user_id is None and isinstance(user, dict):
            user_id = user.get("id")
        if not user_id:
            raise RuntimeError("Supabase Auth did not return the created user ID")

        client.table("bot_registry").update({"is_active": True}).eq("bot_id", bot_id).execute()
        return str(user_id)
    except Exception:
        # Fail closed: a partially provisioned worker must never become active.
        try:
            client.table("bot_registry").delete().eq("bot_id", bot_id).execute()
        except Exception:
            pass
        if user_id:
            try:
                client.auth.admin.delete_user(str(user_id))
            except Exception:
                pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a least-privilege Supabase Auth identity for one worker")
    parser.add_argument("--bot-id", required=True, help="Stable worker ID, e.g. finance")
    parser.add_argument("--role", required=True, help="Worker role, e.g. finance")
    parser.add_argument("--display-name", required=True, help="Human-readable worker name")
    parser.add_argument("--email", required=True, help="Unique Auth email for this worker")
    args = parser.parse_args()

    url = os.environ.get("SUPABASE_URL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not service_key:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the trusted admin environment.", file=sys.stderr)
        return 2

    try:
        from supabase import create_client
    except ImportError:
        print("Install project dependencies first: pip install -r requirements.txt", file=sys.stderr)
        return 2

    password = secrets.token_urlsafe(36)
    admin = create_client(url, service_key)
    try:
        user_id = provision_bot_identity(
            admin,
            bot_id=args.bot_id,
            role=args.role,
            display_name=args.display_name,
            email=args.email,
            password=password,
        )
    except Exception as exc:
        print(f"Provisioning failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("Worker identity created. Copy these credentials directly to the worker host's private .env:")
    print(f"BOT_ID={args.bot_id.strip().lower()}")
    print(f"BOT_ROLE={args.role.strip()}")
    print(f"BOT_DISPLAY_NAME={args.display_name.strip()}")
    print(f"BOT_AUTH_EMAIL={args.email.strip()}")
    print(f"BOT_AUTH_PASSWORD={password}")
    print(f"Supabase Auth user ID: {user_id}")
    print("Do not send the password to Telegram, commit it, or give the worker a service-role key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
