"""Natural-language stock requests and the human confirmation workflow."""

import re
from typing import Any

from database import get_service_role_client

import ai_tools
from n8n_notifications import queue_n8n_event, queue_stock_change_notifications


ALLOWED_ACTIONS = {"stock_in", "stock_out", "damaged", "return", "adjustment"}
STAFF_ACTIONS = {"stock_in", "stock_out", "damaged", "return"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _write_audit_log(
    user_id: str,
    action: str,
    record_id: str,
    old_data: Any = None,
    new_data: Any = None,
) -> None:
    get_service_role_client().table("audit_logs").insert(
        {
            "user_id": user_id,
            "action": action,
            "table_name": "stock_change_requests",
            "record_id": record_id,
            "old_data": old_data,
            "new_data": new_data,
        }
    ).execute()


def _detect_action(message: str) -> str | None:
    lowered = message.lower()
    if re.search(r"\b(damaged|damage|broken|breakage)\b", lowered):
        return "damaged"
    if re.search(r"\b(return|returned)\b", lowered):
        return "return"
    if re.search(r"\b(adjust|adjustment|set\s+(?:the\s+)?quantity)\b", lowered):
        return "adjustment"
    if re.search(r"\b(remove|subtract|deduct|take\s+out|stock\s*out|sell|sold|issue)\b", lowered):
        return "stock_out"
    if re.search(r"\b(add|receive|received|restock|purchase|stock\s*in|bring\s+in)\b", lowered):
        return "stock_in"
    return None


def _extract_quantity(message: str) -> int:
    match = re.search(r"(?<![\w-])(-?\d+(?:\.\d+)?)(?![\w-])", message)
    if not match:
        raise ValueError("Quantity is required; I will not guess it")
    raw_value = match.group(1)
    if "." in raw_value:
        raise ValueError("Quantity must be a whole number greater than zero")
    quantity = int(raw_value)
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero")
    return quantity


def _normal_tokens(value: str) -> set[str]:
    return {
        token.rstrip("s")
        for token in re.findall(r"[\w-]+", value.lower())
        if len(token) > 1
    }


def _find_product_in_message(message: str) -> dict[str, Any]:
    message_tokens = _normal_tokens(message)
    rows = (
        get_service_role_client()
        .table("products")
        .select("id,name,sku,is_active,quantity,minimum_stock,categories(name),suppliers(id,name)")
        .eq("is_active", True)
        .order("id")
        .execute()
        .data
    )
    matches = []
    for row in rows:
        name_tokens = _normal_tokens(str(row.get("name", "")))
        sku = str(row.get("sku", "")).lower()
        if (name_tokens and name_tokens.issubset(message_tokens)) or (sku and sku in message.lower()):
            matches.append(row)
    if not matches:
        raise LookupError("Product was not found in the active inventory")
    if len(matches) > 1:
        names = ", ".join(str(row.get("name")) for row in matches[:5])
        raise ValueError(f"More than one product matched: {names}. Please be specific")
    return matches[0]


def _find_supplier_in_message(message: str) -> dict[str, Any] | None:
    rows = (
        get_service_role_client()
        .table("suppliers")
        .select("id,name")
        .order("id")
        .execute()
        .data
    )
    message_tokens = _normal_tokens(message)
    matches = []
    for row in rows:
        supplier_tokens = _normal_tokens(str(row.get("name", "")))
        if supplier_tokens and supplier_tokens.issubset(message_tokens):
            matches.append(row)
    if len(matches) > 1:
        raise ValueError("More than one supplier matched. Please be specific")
    if matches:
        return matches[0]
    if re.search(r"\b(from|supplier|vendor)\b", message, re.IGNORECASE):
        raise LookupError("Supplier was not found in the supplier list")
    return None


def _extract_reason(message: str) -> str | None:
    match = re.search(r"\b(?:because|reason(?:\s+is)?|note)\s*[:\-]?\s*(.+)$", message, re.IGNORECASE)
    if match:
        reason = match.group(1).strip()
        return reason[:500] or None
    return None


def _preview(row: dict[str, Any], product: dict[str, Any], supplier: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "request_id": row.get("id"),
        "status": row.get("status", "pending"),
        "product": product.get("name"),
        "action": row.get("requested_action"),
        "quantity": row.get("quantity"),
        "supplier": supplier.get("name") if supplier else None,
        "reason": row.get("reason"),
        "message": "Please confirm or cancel this stock change.",
    }


def _request_event_payload(
    row: dict[str, Any],
    *,
    status: str,
    product: dict[str, Any] | None = None,
    supplier: dict[str, Any] | None = None,
    approved_by: str | None = None,
) -> dict[str, Any]:
    product = product or _as_dict(row.get("products"))
    supplier = supplier or _as_dict(row.get("suppliers"))
    return {
        "request_id": row.get("id"),
        "product_id": row.get("product_id"),
        "product": product.get("name"),
        "sku": product.get("sku"),
        "action": row.get("requested_action"),
        "quantity": row.get("quantity"),
        "supplier": supplier.get("name") if supplier else None,
        "reason": row.get("reason"),
        "requested_by": row.get("requested_by"),
        "approved_by": approved_by or row.get("approved_by"),
        "status": status,
        "created_at": row.get("created_at"),
        "confirmed_at": row.get("confirmed_at"),
    }


def prepare_stock_request(message: str, user: dict[str, Any]) -> dict[str, Any]:
    action = _detect_action(message)
    if not action:
        raise ValueError("I could not identify a stock action such as add, remove, damaged, return, or adjustment")
    if action not in ALLOWED_ACTIONS:
        raise ValueError("This stock action is not supported")
    if user["role"] == "staff" and action not in STAFF_ACTIONS:
        raise PermissionError("Staff cannot create adjustment requests")

    quantity = _extract_quantity(message)
    product = _find_product_in_message(message)
    supplier = _find_supplier_in_message(message)
    reason = _extract_reason(message)
    payload = {
        "product_id": product["id"],
        "requested_action": action,
        "quantity": quantity,
        "supplier_id": supplier["id"] if supplier else None,
        "reason": reason,
        "requested_by": user["id"],
        "status": "pending",
    }
    response = get_service_role_client().table("stock_change_requests").insert(payload).execute()
    if not response.data:
        raise RuntimeError("Stock change request was not created")
    row = response.data[0]
    _write_audit_log(user["id"], "ai_stock_request_created", str(row["id"]), new_data=row)
    queue_n8n_event(
        "ai_stock_request_created",
        _request_event_payload(row, status="pending", product=product, supplier=supplier),
    )
    return _preview(row, product, supplier)


def _request_select():
    return (
        "id,product_id,requested_action,quantity,supplier_id,reason,requested_by,"
        "status,approved_by,created_at,confirmed_at,products(id,name,sku,quantity,"
        "minimum_stock,is_active),suppliers(id,name)"
    )


def _request_view(row: dict[str, Any]) -> dict[str, Any]:
    product = _as_dict(row.get("products"))
    supplier = _as_dict(row.get("suppliers"))
    return {
        "request_id": row.get("id"),
        "product_id": row.get("product_id"),
        "product": product.get("name"),
        "sku": product.get("sku"),
        "requested_action": row.get("requested_action"),
        "quantity": row.get("quantity"),
        "supplier_id": row.get("supplier_id"),
        "supplier": supplier.get("name") if supplier else None,
        "reason": row.get("reason"),
        "requested_by": row.get("requested_by"),
        "status": row.get("status"),
        "approved_by": row.get("approved_by"),
        "created_at": row.get("created_at"),
        "confirmed_at": row.get("confirmed_at"),
    }


def list_pending_requests(user: dict[str, Any]) -> list[dict[str, Any]]:
    query = (
        get_service_role_client()
        .table("stock_change_requests")
        .select(_request_select())
        .eq("status", "pending")
        .order("created_at", desc=True)
    )
    if user["role"] != "manager":
        query = query.eq("requested_by", user["id"])
    return [_request_view(row) for row in query.execute().data]


def _load_request(request_id: int) -> dict[str, Any]:
    response = (
        get_service_role_client()
        .table("stock_change_requests")
        .select(_request_select())
        .eq("id", request_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise LookupError("Stock change request not found")
    return response.data[0]


def _check_request_access(row: dict[str, Any], user: dict[str, Any]) -> None:
    if user["role"] != "manager" and row.get("requested_by") != user["id"]:
        raise PermissionError("You can only manage your own stock-change requests")


def confirm_request(request_id: int, user: dict[str, Any]) -> dict[str, Any]:
    row = _load_request(request_id)
    _check_request_access(row, user)
    if row.get("status") != "pending":
        raise ValueError(f"Request is already {row.get('status')}")
    product = _as_dict(row.get("products"))
    if not product or not product.get("is_active"):
        raise ValueError("Product does not exist or is inactive")
    if row.get("quantity", 0) <= 0:
        raise ValueError("Request quantity must be greater than zero")
    if user["role"] == "staff" and row.get("requested_action") not in STAFF_ACTIONS:
        raise PermissionError("Staff cannot confirm adjustment requests")

    try:
        response = get_service_role_client().rpc(
            "confirm_stock_change_request",
            {
                "p_request_id": request_id,
                "p_approved_by": user["id"],
                "p_actor_role": user["role"],
            },
        ).execute()
    except Exception as exc:
        raise RuntimeError(str(exc) or "Could not confirm stock-change request") from exc
    result = response.data
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        raise RuntimeError("Confirmation did not return a request")
    confirmed_request = _as_dict(result.get("request"))
    confirmed_product = _as_dict(result.get("product"))
    queue_stock_change_notifications(
        product=confirmed_product or product,
        movement_type=confirmed_request.get("requested_action", row.get("requested_action")),
        quantity=confirmed_request.get("quantity", row.get("quantity")),
        reason=confirmed_request.get("reason", row.get("reason")),
        user_id=user["id"],
    )
    queue_n8n_event(
        "ai_stock_request_confirmed",
        _request_event_payload(
            {**row, **confirmed_request},
            status="confirmed",
            product=confirmed_product or product,
            approved_by=user["id"],
        ),
    )
    return {
        "request_id": confirmed_request.get("id", request_id),
        "status": confirmed_request.get("status", "confirmed"),
        "product": product.get("name"),
        "action": confirmed_request.get("requested_action", row.get("requested_action")),
        "quantity": confirmed_request.get("quantity", row.get("quantity")),
        "new_quantity": confirmed_product.get("quantity"),
        "message": "Stock change confirmed and applied atomically.",
    }


def cancel_request(request_id: int, user: dict[str, Any]) -> dict[str, Any]:
    row = _load_request(request_id)
    _check_request_access(row, user)
    if row.get("status") != "pending":
        raise ValueError(f"Request is already {row.get('status')}")
    try:
        response = get_service_role_client().rpc(
            "cancel_stock_change_request",
            {
                "p_request_id": request_id,
                "p_cancelled_by": user["id"],
                "p_actor_role": user["role"],
            },
        ).execute()
    except Exception as exc:
        raise RuntimeError(str(exc) or "Could not cancel stock-change request") from exc
    result = response.data
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        raise RuntimeError("Cancellation did not return a request")
    product = _as_dict(row.get("products"))
    queue_n8n_event(
        "ai_stock_request_cancelled",
        _request_event_payload({**row, **result}, status="cancelled", product=product),
    )
    return {
        "request_id": result.get("id", request_id),
        "status": result.get("status", "cancelled"),
        "product": product.get("name"),
        "action": result.get("requested_action", row.get("requested_action")),
        "quantity": result.get("quantity", row.get("quantity")),
        "message": "Stock-change request cancelled. Inventory was not changed.",
    }
