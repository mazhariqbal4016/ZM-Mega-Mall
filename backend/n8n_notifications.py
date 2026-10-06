"""Best-effort, secret-free notifications to n8n webhooks.

Notifications are sent after the database operation has completed and in a
background worker. A webhook timeout or n8n outage is logged and never raised
back into the inventory request.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


load_dotenv()
logger = logging.getLogger("zm_mega_mall.n8n")
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="n8n-webhook")

_EVENT_ENV_VARS = {
    "low_stock": "N8N_LOW_STOCK_WEBHOOK_URL",
    "out_of_stock": "N8N_LOW_STOCK_WEBHOOK_URL",
    "stock_event": "N8N_STOCK_EVENT_WEBHOOK_URL",
    "damaged_stock": "N8N_STOCK_EVENT_WEBHOOK_URL",
    "ai_stock_request_created": "N8N_AI_REQUEST_WEBHOOK_URL",
    "ai_stock_request_confirmed": "N8N_AI_REQUEST_WEBHOOK_URL",
    "ai_stock_request_cancelled": "N8N_AI_REQUEST_WEBHOOK_URL",
    "daily_manager_summary": "N8N_DAILY_SUMMARY_WEBHOOK_URL",
}

_SENSITIVE_KEY_PARTS = ("password", "token", "api_key", "service_role", "secret")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _timeout_seconds() -> float:
    try:
        return min(max(float(os.getenv("N8N_WEBHOOK_TIMEOUT_SECONDS", "3")), 1.0), 10.0)
    except ValueError:
        return 3.0


def _post_webhook(url: str, event_type: str, payload: dict[str, Any]) -> None:
    safe_payload = _safe_value(payload)
    if isinstance(safe_payload, dict):
        safe_payload.pop("event_type", None)
    body = {
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        **safe_payload,
    }
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "zm-mega-mall-backend/1.0"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=_timeout_seconds()) as response:
            if response.status >= 400:
                logger.warning("n8n webhook returned HTTP %s for %s", response.status, event_type)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("n8n webhook failed for %s: %s", event_type, exc)
    except Exception:
        logger.exception("Unexpected n8n notification error for %s", event_type)


def queue_n8n_event(event_type: str, payload: dict[str, Any]) -> bool:
    """Queue a best-effort notification; return False when not configured."""
    env_name = _EVENT_ENV_VARS.get(event_type)
    url = os.getenv(env_name, "").strip() if env_name else ""
    if not url:
        logger.debug("No n8n webhook configured for %s", event_type)
        return False
    _executor.submit(_post_webhook, url, event_type, payload)
    return True


def build_stock_event_payload(
    *,
    event_type: str,
    product: dict[str, Any],
    movement_type: str,
    quantity: int,
    reason: str | None,
    user_id: str,
    old_quantity: int | None,
    new_quantity: int | None,
) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "product_id": product.get("id"),
        "product_name": product.get("name"),
        "sku": product.get("sku"),
        "movement_type": movement_type,
        "quantity": quantity,
        "old_quantity": old_quantity,
        "new_quantity": new_quantity,
        "minimum_stock": product.get("minimum_stock"),
        "location": product.get("location"),
        "reason": reason,
        "user_id": user_id,
    }


def queue_stock_change_notifications(
    *,
    product: dict[str, Any],
    movement_type: str,
    quantity: int,
    reason: str | None,
    user_id: str,
) -> None:
    """Queue stock, damaged, low-stock, and out-of-stock events."""
    new_quantity = product.get("quantity")
    if movement_type in {"stock_in", "return"}:
        old_quantity = new_quantity - quantity if new_quantity is not None else None
    elif movement_type in {"stock_out", "damaged"}:
        old_quantity = new_quantity + quantity if new_quantity is not None else None
    else:
        old_quantity = None

    event_type = "damaged_stock" if movement_type == "damaged" else "stock_event"
    event_payload = build_stock_event_payload(
        event_type=event_type,
        product=product,
        movement_type=movement_type,
        quantity=quantity,
        reason=reason,
        user_id=user_id,
        old_quantity=old_quantity,
        new_quantity=new_quantity,
    )
    queue_n8n_event(event_type, event_payload)

    minimum_stock = product.get("minimum_stock")
    if new_quantity == 0:
        queue_n8n_event("out_of_stock", event_payload)
    elif (
        new_quantity is not None
        and minimum_stock is not None
        and new_quantity > 0
        and new_quantity <= minimum_stock
    ):
        queue_n8n_event("low_stock", event_payload)
