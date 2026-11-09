"""Shared, side-effect-free identity configuration for specialized bots."""

from dataclasses import dataclass
import os
import re
from collections.abc import Mapping


_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class BotIdentity:
    """Stable public identity; contains no credentials or user-specific secrets."""

    bot_id: str
    role: str
    display_name: str
    coordination_chat_id: int | None = None


def _identifier(value: str, variable: str) -> str:
    normalized = value.strip().lower()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(
            f"{variable} must start with a letter and contain only "
            "letters, digits, underscores or hyphens (max 64 characters)"
        )
    return normalized


def load_bot_identity(environ: Mapping[str, str] | None = None) -> BotIdentity:
    """Load optional bot identity settings without reading or changing process state."""
    source = os.environ if environ is None else environ

    bot_id = _identifier(source.get("BOT_ID", "dimka"), "BOT_ID")
    role = _identifier(source.get("BOT_ROLE", "personal_assistant"), "BOT_ROLE")
    display_name = source.get("BOT_DISPLAY_NAME", "Димка").strip()
    if not display_name:
        raise ValueError("BOT_DISPLAY_NAME must not be empty")

    raw_chat_id = source.get("BOT_COORDINATION_CHAT_ID", "").strip()
    coordination_chat_id = None
    if raw_chat_id:
        try:
            coordination_chat_id = int(raw_chat_id)
        except ValueError as error:
            raise ValueError("BOT_COORDINATION_CHAT_ID must be an integer") from error
        if coordination_chat_id >= 0:
            raise ValueError("BOT_COORDINATION_CHAT_ID must be a negative Telegram group ID")

    return BotIdentity(
        bot_id=bot_id,
        role=role,
        display_name=display_name,
        coordination_chat_id=coordination_chat_id,
    )
