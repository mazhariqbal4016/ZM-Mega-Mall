"""Read-only, role-aware inventory queries used by the AI assistant."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from database import get_service_role_client


_STOP_WORDS = {
    "a", "an", "and", "are", "available", "can", "current", "do", "for",
    "how", "i", "in", "is", "many", "me", "of", "on", "our", "please",
    "product", "products", "show", "stock", "tell", "the", "what", "which",
    "with", "item", "items", "today", "there", "this", "that", "week",
    "cost", "price", "value", "profit", "history", "movement", "report",
    "units", "does", "have", "happened", "to", "inventory", "kitna", "kitni",
    "kitne", "mein", "main", "hai", "hain", "kya", "dikhao", "dikhaye",
    "batao", "bataiye", "ka", "ki", "ke", "ko", "se", "par", "pe", "mujhe",
    "mera", "hamara", "aur", "konsi", "kaun", "wala", "wale",
}

_CATEGORY_ALIASES = {
    "grocery": {"grocery"},
    "clothing": {"clothing", "clothes", "apparel"},
    "electronics": {"electronics", "electronic"},
    "household": {"household", "household items", "home"},
    "cosmetics": {"cosmetics", "cosmetic", "makeup", "beauty"},
    "toys & stationery": {"toys", "toy", "stationery", "school stationery", "office stationery"},
    "food court / restaurants": {"food court", "restaurant", "restaurants", "food"},
    "pharmacy": {"pharmacy", "medical"},
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stock_status(quantity: int, minimum_stock: int) -> str:
    if quantity <= 0:
        return "out_of_stock"
    if quantity <= minimum_stock:
        return "low_stock"
    return "in_stock"


def _fetch_products() -> list[dict[str, Any]]:
    return (
        get_service_role_client()
        .table("products")
        .select(
            "id,name,sku,category_id,supplier_id,quantity,minimum_stock,"
            "selling_price,cost_price,location,is_active,created_at,updated_at,"
            "categories(id,name),suppliers(id,name)"
        )
        .order("id")
        .execute()
        .data
    )


def _fetch_categories() -> list[dict[str, Any]]:
    return (
        get_service_role_client()
        .table("categories")
        .select("id,name")
        .order("id")
        .execute()
        .data
    )


def _product_data(product: dict[str, Any], role: str) -> dict[str, Any]:
    category = _as_dict(product.get("categories"))
    supplier = _as_dict(product.get("suppliers"))
    quantity = product.get("quantity") or 0
    minimum_stock = product.get("minimum_stock") or 0
    result = {
        "id": product.get("id"),
        "name": product.get("name"),
        "sku": product.get("sku"),
        "category": category.get("name"),
        "quantity": quantity,
        "minimum_stock": minimum_stock,
        "location": product.get("location"),
        "stock_status": _stock_status(quantity, minimum_stock),
    }
    if role == "manager":
        result.update(
            {
                "supplier": supplier.get("name"),
                "selling_price": product.get("selling_price"),
                "cost_price": product.get("cost_price"),
            }
        )
    return result


def _search_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[\w-]+", query.lower())
    return [token for token in tokens if token not in _STOP_WORDS and len(token) > 1]


def _matches_query(product: dict[str, Any], query: str) -> bool:
    if not query.strip():
        return True
    category = _as_dict(product.get("categories"))
    searchable = " ".join(
        str(value or "")
        for value in (
            product.get("name"),
            product.get("sku"),
            category.get("name"),
            product.get("location"),
        )
    ).lower()
    query_lower = query.lower().strip()
    if query_lower in searchable:
        return True
    tokens = _search_tokens(query)
    if not tokens:
        return False
    return all(token in searchable or token.rstrip("s") in searchable for token in tokens)


def search_products(query: str, role: str) -> list[dict[str, Any]]:
    """Search active products by name, SKU, or category using verified rows."""
    products = [product for product in _fetch_products() if product.get("is_active")]
    return [
        _product_data(product, role)
        for product in products
        if _matches_query(product, query)
    ][:50]


def get_product_stock(query: str, role: str) -> list[dict[str, Any]]:
    return search_products(query, role)


def _category_matches(product: dict[str, Any], category_query: str | None) -> bool:
    if not category_query:
        return True
    category = str(_as_dict(product.get("categories")).get("name") or "").lower()
    query = category_query.lower().strip()
    aliases = _CATEGORY_ALIASES.get(query, {query})
    return any(alias in category or category in alias for alias in aliases)


def get_low_stock(role: str, category_query: str | None = None) -> list[dict[str, Any]]:
    products = [product for product in _fetch_products() if product.get("is_active")]
    return [
        _product_data(product, role)
        for product in products
        if _category_matches(product, category_query)
        and (product.get("quantity") or 0) > 0
        and (product.get("quantity") or 0) <= (product.get("minimum_stock") or 0)
    ]


def get_out_of_stock(role: str, category_query: str | None = None) -> list[dict[str, Any]]:
    products = [product for product in _fetch_products() if product.get("is_active")]
    return [
        _product_data(product, role)
        for product in products
        if _category_matches(product, category_query)
        and (product.get("quantity") or 0) == 0
    ]


def _movement_data(row: dict[str, Any], role: str) -> dict[str, Any]:
    product = _as_dict(row.get("products"))
    result = {
        "id": row.get("id"),
        "product_id": row.get("product_id"),
        "product_name": product.get("name"),
        "sku": product.get("sku"),
        "movement_type": row.get("movement_type"),
        "quantity": row.get("quantity"),
        "old_quantity": row.get("old_quantity"),
        "new_quantity": row.get("new_quantity"),
        "reason": row.get("reason"),
        "created_at": row.get("created_at"),
    }
    if role == "manager":
        result["changed_by"] = row.get("changed_by")
    return result


def _fetch_movements(
    *,
    start_utc: str | None = None,
    movement_type: str | None = None,
    product_id: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    query = (
        get_service_role_client()
        .table("stock_movements")
        .select(
            "id,product_id,movement_type,quantity,old_quantity,new_quantity,"
            "supplier_id,reason,changed_by,created_at,products(id,name,sku,category_id)"
        )
        .order("created_at", desc=True)
    )
    if start_utc:
        query = query.gte("created_at", start_utc)
    if movement_type:
        query = query.eq("movement_type", movement_type)
    if product_id is not None:
        query = query.eq("product_id", product_id)
    if limit is not None:
        query = query.limit(limit)
    return query.execute().data


def get_stock_history(query: str | None, role: str) -> list[dict[str, Any]]:
    product_ids: list[int] | None = None
    if query:
        product_ids = [row["id"] for row in search_products(query, role)]
        if not product_ids:
            return []
    rows = _fetch_movements(limit=100)
    if product_ids is not None:
        rows = [row for row in rows if row.get("product_id") in product_ids]
    return [_movement_data(row, role) for row in rows]


def get_recent_movements(limit: int, role: str) -> list[dict[str, Any]]:
    if role != "manager":
        raise PermissionError("Recent movement reports are manager-only")
    safe_limit = min(max(limit, 1), 100)
    return [_movement_data(row, role) for row in _fetch_movements(limit=safe_limit)]


def get_top_moving_products(days: int, role: str) -> list[dict[str, Any]]:
    if role != "manager":
        raise PermissionError("Top moving products are manager-only")
    start_utc = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    movements = _fetch_movements(start_utc=start_utc)
    movements = [row for row in movements if row.get("movement_type") in {"stock_out", "sale"}]
    products = {
        product["id"]: product
        for product in _fetch_products()
        if product.get("is_active")
    }
    totals: dict[int, int] = defaultdict(int)
    names: dict[int, dict[str, Any]] = {}
    for movement in movements:
        product_id = movement.get("product_id")
        if product_id is None:
            continue
        totals[product_id] += movement.get("quantity") or 0
        names[product_id] = _as_dict(movement.get("products"))
    result = []
    for product_id, quantity in totals.items():
        product = products.get(product_id, {})
        result.append(
            {
                "product_id": product_id,
                "product_name": names.get(product_id, {}).get("name"),
                "sku": names.get(product_id, {}).get("sku"),
                "total_outgoing_quantity": quantity,
                "current_quantity": product.get("quantity"),
            }
        )
    return sorted(result, key=lambda row: row["total_outgoing_quantity"], reverse=True)


def get_category_summary(role: str) -> list[dict[str, Any]]:
    products = [product for product in _fetch_products() if product.get("is_active")]
    result = []
    for category in _fetch_categories():
        category_products = [
            product for product in products if product.get("category_id") == category["id"]
        ]
        result.append(
            {
                "category_id": category["id"],
                "category_name": category["name"],
                "active_products_count": len(category_products),
                "total_quantity": sum(product.get("quantity") or 0 for product in category_products),
                "low_stock_count": sum(
                    1
                    for product in category_products
                    if 0 < (product.get("quantity") or 0) <= (product.get("minimum_stock") or 0)
                ),
                "out_of_stock_count": sum(
                    1 for product in category_products if (product.get("quantity") or 0) == 0
                ),
            }
        )
    return result


def get_supplier_summary(role: str) -> list[dict[str, Any]]:
    if role != "manager":
        raise PermissionError("Supplier reports are manager-only")
    suppliers = (
        get_service_role_client().table("suppliers").select("id,name").order("id").execute().data
    )
    products = [product for product in _fetch_products() if product.get("is_active")]
    stock_in = _fetch_movements(movement_type="stock_in")
    result = []
    for supplier in suppliers:
        supplier_products = [
            product for product in products if product.get("supplier_id") == supplier["id"]
        ]
        result.append(
            {
                "supplier_id": supplier["id"],
                "supplier_name": supplier["name"],
                "active_products_count": len(supplier_products),
                "total_current_stock": sum(product.get("quantity") or 0 for product in supplier_products),
                "stock_in_quantity": sum(
                    row.get("quantity") or 0
                    for row in stock_in
                    if row.get("supplier_id") == supplier["id"]
                ),
            }
        )
    return result


def get_inventory_value(role: str) -> dict[str, Any]:
    if role != "manager":
        raise PermissionError("Inventory value is manager-only")
    products = [product for product in _fetch_products() if product.get("is_active")]
    result = []
    total_cost = 0.0
    total_selling = 0.0
    for product in products:
        quantity = product.get("quantity") or 0
        cost_price = float(product.get("cost_price") or 0)
        selling_price = float(product.get("selling_price") or 0)
        cost_value = quantity * cost_price
        selling_value = quantity * selling_price
        total_cost += cost_value
        total_selling += selling_value
        result.append(
            {
                "product_id": product.get("id"),
                "product_name": product.get("name"),
                "sku": product.get("sku"),
                "quantity": quantity,
                "cost_price": cost_price,
                "selling_price": selling_price,
                "inventory_cost_value": cost_value,
                "inventory_selling_value": selling_value,
            }
        )
    return {
        "active_products_count": len(products),
        "total_inventory_cost_value": total_cost,
        "total_inventory_selling_value": total_selling,
        "estimated_potential_profit": total_selling - total_cost,
        "products": result,
    }


def get_stock_movement_summary(role: str) -> dict[str, Any]:
    if role != "manager":
        raise PermissionError("Stock movement summaries are manager-only")
    rows = _fetch_movements()
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        totals[row.get("movement_type") or "unknown"] += row.get("quantity") or 0
    return {
        "total_stock_in_quantity": totals["stock_in"],
        "total_stock_out_quantity": totals["stock_out"],
        "total_sale_quantity": totals["sale"],
        "total_damaged_quantity": totals["damaged"],
        "total_returned_quantity": totals["return"],
        "total_adjustments": totals["adjustment"],
        "number_of_movements": len(rows),
    }


def get_damaged_stock(role: str) -> dict[str, Any]:
    if role != "manager":
        raise PermissionError("Damaged stock reports are manager-only")
    rows = _fetch_movements(movement_type="damaged")
    return {
        "total_damaged_quantity": sum(row.get("quantity") or 0 for row in rows),
        "records": [
            {
                "product": _as_dict(row.get("products")).get("name"),
                "product_id": row.get("product_id"),
                "damaged_quantity": row.get("quantity"),
                "reason": row.get("reason"),
                "changed_by": row.get("changed_by"),
                "date": row.get("created_at"),
            }
            for row in rows
        ],
    }
