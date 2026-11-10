"""Deterministic intent routing with explicit internal-agent boundaries.

The dispatcher only chooses a handler. It never accesses Supabase, executes a
write, or bypasses the main bot's owner-confirmation flow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from finance_agent import looks_like_finance_entry, looks_like_finance_question
from project_agent import looks_like_project_intent


ConfirmationPolicy = Literal["required", "not_required", "handler_enforced"]


@dataclass(frozen=True)
class AgentPolicy:
    agent_id: str
    capabilities: frozenset[str]
    writes_require_main_bot_confirmation: bool = True


@dataclass(frozen=True)
class IntentDecision:
    agent_id: str
    action: str
    capability: str
    confirmation_policy: ConfirmationPolicy


AGENT_POLICIES: dict[str, AgentPolicy] = {
    "assistant": AgentPolicy(
        agent_id="assistant",
        capabilities=frozenset({"assistant.chat", "memory.read", "memory.propose"}),
    ),
    "finance": AgentPolicy(
        agent_id="finance",
        capabilities=frozenset({"finance.read", "finance.draft"}),
    ),
    "projects": AgentPolicy(
        agent_id="projects",
        capabilities=frozenset({"projects.read", "projects.draft"}),
    ),
}


def get_agent_policy(agent_id: str) -> AgentPolicy:
    """Return a known policy; unknown agents are denied by default."""
    try:
        return AGENT_POLICIES[agent_id]
    except KeyError as exc:
        raise ValueError(f"Unknown internal agent: {agent_id}") from exc


def _decision(
    agent_id: str,
    action: str,
    capability: str,
    confirmation_policy: ConfirmationPolicy,
) -> IntentDecision:
    policy = get_agent_policy(agent_id)
    if capability not in policy.capabilities:
        raise RuntimeError(f"Agent {agent_id} is not allowed capability {capability}")
    if capability.endswith(".draft") and (
        not policy.writes_require_main_bot_confirmation
        or confirmation_policy not in {"required", "handler_enforced"}
    ):
        raise RuntimeError("Draft-producing agents must remain behind main-bot confirmation")
    return IntentDecision(agent_id, action, capability, confirmation_policy)


def dispatch_intent(message: str) -> IntentDecision:
    """Route a user message to a read-only or draft-producing internal handler."""
    text = (message or "").strip()
    if not text:
        return _decision("assistant", "chat", "assistant.chat", "not_required")

    if looks_like_finance_entry(text):
        return _decision(
            "finance",
            "prepare_finance_draft",
            "finance.draft",
            "required",
        )

    if looks_like_finance_question(text):
        return _decision(
            "finance",
            "answer_finance_question",
            "finance.read",
            "not_required",
        )

    if looks_like_project_intent(text):
        # The project parser distinguishes read-only questions from write drafts.
        # Its handler must ask for confirmation before any write.
        return _decision(
            "projects",
            "prepare_project_action",
            "projects.draft",
            "handler_enforced",
        )

    return IntentDecision("assistant", "chat", "assistant.chat", "not_required")
