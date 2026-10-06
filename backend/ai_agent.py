"""Single, controlled AI inventory agent orchestration.

The agent only chooses between existing safe read helpers and the existing
pending stock-change workflow. It never receives a database client or a write
tool and it never applies stock changes itself.
"""

from typing import Any

import ai_assistant
from ai_stock_requests import prepare_stock_request


def _conversation_messages(conversation: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Keep only a small, plain-text conversation window supplied by the client."""
    if not conversation:
        return []
    safe: list[dict[str, str]] = []
    for message in conversation[-8:]:
        role = message.get("role")
        content = str(message.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            safe.append({"role": role, "content": content[:1000]})
    return safe


def _resolve_follow_up(message: str, conversation: list[dict[str, Any]] | None, role: str) -> str:
    """Resolve safe short follow-ups without granting any new permissions."""
    current = message.strip()
    safe_conversation = _conversation_messages(conversation)
    previous_user = next(
        (item["content"] for item in reversed(safe_conversation) if item["role"] == "user"),
        None,
    )
    if not previous_user:
        return current

    lowered = current.lower()
    if any(phrase in lowered for phrase in ("first one", "first item", "that one", "the first")):
        try:
            previous_context = ai_assistant.build_verified_context(previous_user, role)
            rows = previous_context.get("data") or []
            first = rows[0] if isinstance(rows, list) and rows else {}
            first_name = first.get("name") or first.get("product_name")
            if first_name:
                return f"How many units are available for {first_name}?"
        except Exception:
            # The normal verified query path will return a safe unavailable response.
            pass

    if len(current.split()) <= 8 and any(
        phrase in lowered for phrase in ("only ", "just ", "electronics", "grocery", "clothing", "household")
    ):
        return f"{previous_user}. Follow-up filter: {current}"
    return current


def handle_inventory_agent(
    message: str,
    user: dict[str, Any],
    conversation: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Route one message to a read-only answer or a pending request preview."""
    message = message.strip()
    if not message:
        raise ValueError("message cannot be empty")

    role = user["role"]
    if ai_assistant._is_write_request(message):
        try:
            preview = prepare_stock_request(message, user)
        except LookupError:
            return {
                "mode": "not_found",
                "answer": "I could not find that product in the inventory.",
                "read_only": True,
                "request": None,
            }
        except ValueError as exc:
            return {
                "mode": "clarification",
                "answer": str(exc),
                "read_only": True,
                "request": None,
            }
        return {
            "mode": "stock_request",
            "answer": "I prepared a pending stock-change request. Please confirm or cancel it.",
            "read_only": False,
            "request": preview,
            **preview,
        }

    resolved_message = _resolve_follow_up(message, conversation, role)
    response = ai_assistant.chat_with_inventory_assistant(resolved_message, role)
    return {
        "mode": "read_only",
        "request": None,
        "conversation_used": resolved_message != message,
        **response,
    }
