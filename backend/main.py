from collections import defaultdict
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
import hmac
import os
from typing import Any, Literal

import ai_tools

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from ai_assistant import (
    AIUnavailableError,
    AIPermissionError,
    chat_with_inventory_assistant,
)
from ai_agent import handle_inventory_agent
from ai_stock_requests import (
    cancel_request,
    confirm_request,
    list_pending_requests,
    prepare_stock_request,
)
from database import (
    get_public_supabase_client,
    get_service_role_client,
    get_supabase_client,
)
from n8n_notifications import queue_n8n_event, queue_stock_change_notifications


app = FastAPI(title="ZM Mega Mall Inventory System")

_cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173,https://mazhariqbal4016.github.io",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bearer_scheme = HTTPBearer(auto_error=False)
# Pakistan uses UTC+05:00 year-round; this avoids a tzdata runtime dependency.
LOCAL_TIMEZONE = timezone(timedelta(hours=5))

MALL_OPERATIONAL_AREAS = [
    {
        "name": "Parking Area",
        "type": "operational",
        "inventory_enabled": False,
        "description": "Mall operations area; payment and product stock stay separate.",
    },
    {
        "name": "Billing Counters",
        "type": "operational",
        "inventory_enabled": False,
        "description": "Operational area; POS/payment workflows are not part of inventory.",
    },
    {
        "name": "Store Room / Warehouse",
        "type": "inventory_location",
        "inventory_enabled": True,
        "description": "Products can use Main Store, Store Room, Warehouse, or Billing Floor as locations.",
    },
    {
        "name": "Food Court / Restaurants",
        "type": "inventory_department",
        "inventory_enabled": True,
        "description": "Food-related stock is supported through the Food Court / Restaurants category.",
    },
    {
        "name": "Pharmacy",
        "type": "inventory_department",
        "inventory_enabled": True,
        "description": "Optional non-prescription inventory category; no prescription workflow is enabled.",
    },
]


class SignupRequest(BaseModel):
    full_name: str
    email: str
    password: str
    role: str = "staff"


class LoginRequest(BaseModel):
    email: str
    password: str


class AIChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)


class AIStockRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1000)


class AIAgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class AIAgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)
    conversation: list[AIAgentMessage] = Field(default_factory=list, max_length=8)


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    sku: str
    category_id: int | None = None
    supplier_id: int | None = None
    quantity: int = Field(default=0, ge=0)
    minimum_stock: int = Field(default=5, ge=0)
    selling_price: float | None = Field(default=None, ge=0)
    cost_price: float | None = Field(default=None, ge=0)
    location: str | None = None
    is_active: bool = True


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    sku: str | None = None
    category_id: int | None = None
    supplier_id: int | None = None
    minimum_stock: int | None = Field(default=None, ge=0)
    selling_price: float | None = Field(default=None, ge=0)
    cost_price: float | None = Field(default=None, ge=0)
    location: str | None = None
    is_active: bool | None = None


class StockChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    quantity: int = Field(gt=0)
    supplier_id: int | None = None
    reason: str | None = None


class StockAdjustmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    new_quantity: int = Field(ge=0)
    reason: str


def _profile_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "full_name": row.get("full_name"),
        "email": row.get("email"),
        "role": row.get("role"),
        "is_active": row.get("is_active", True),
    }


def _get_profile(user_id: str) -> dict[str, Any]:
    response = (
        get_service_role_client()
        .table("profiles")
        .select("id,full_name,email,role,is_active")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=403, detail="User profile not found")
    return _profile_from_row(response.data[0])


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    """Validate a Supabase access token and return the matching active profile."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_response = get_public_supabase_client().auth.get_user(
            credentials.credentials
        )
        auth_user = user_response.user
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if not auth_user:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        profile = _get_profile(str(auth_user.id))
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not profile["is_active"]:
        raise HTTPException(status_code=403, detail="User account is inactive")
    return profile


def require_manager(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if user["role"] != "manager":
        raise HTTPException(status_code=403, detail="Manager access required")
    return user


def require_manager_or_automation(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    automation_secret: str | None = Header(default=None, alias="X-Automation-Secret"),
) -> dict[str, Any]:
    """Allow manager JWTs or a server-to-server secret for n8n summaries."""
    expected_secret = os.getenv("AUTOMATION_SECRET", "").strip()
    if expected_secret and automation_secret and hmac.compare_digest(
        automation_secret, expected_secret
    ):
        return {"id": "automation", "role": "manager", "is_active": True}

    user = get_current_user(credentials)
    if user["role"] != "manager":
        raise HTTPException(status_code=403, detail="Manager access required")
    return user


def _supabase_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    return message or "Supabase request failed"


def _stock_status(quantity: int, minimum_stock: int) -> str:
    if quantity <= 0:
        return "out_of_stock"
    if quantity <= minimum_stock:
        return "low_stock"
    return "in_stock"


def _category_name(row: dict[str, Any]) -> str | None:
    category = row.get("categories")
    return category.get("name") if isinstance(category, dict) else None


def _write_audit_log(
    user_id: str,
    action: str,
    table_name: str,
    record_id: str,
    old_data: Any = None,
    new_data: Any = None,
) -> None:
    get_service_role_client().table("audit_logs").insert(
        {
            "user_id": user_id,
            "action": action,
            "table_name": table_name,
            "record_id": record_id,
            "old_data": old_data,
            "new_data": new_data,
        }
    ).execute()


def _apply_stock_change(
    movement_type: str,
    payload: StockChangeRequest | StockAdjustmentRequest,
    user: dict[str, Any],
) -> dict[str, Any]:
    if movement_type == "adjustment":
        quantity = payload.new_quantity  # type: ignore[attr-defined]
        reason = payload.reason
        supplier_id = None
    else:
        quantity = payload.quantity  # type: ignore[attr-defined]
        reason = payload.reason
        supplier_id = payload.supplier_id  # type: ignore[attr-defined]

    try:
        response = get_service_role_client().rpc(
            "apply_stock_change",
            {
                "p_product_id": payload.product_id,
                "p_movement_type": movement_type,
                "p_quantity": quantity,
                "p_supplier_id": supplier_id,
                "p_reason": reason,
                "p_changed_by": user["id"],
            },
        ).execute()
        if not response.data:
            raise RuntimeError("Stock change did not return a product")
        product = response.data if isinstance(response.data, dict) else response.data[0]
        queue_stock_change_notifications(
            product=product,
            movement_type=movement_type,
            quantity=quantity,
            reason=reason,
            user_id=user["id"],
        )
        return response.data
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Stock change failed: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/")
def root():
    return {"message": "ZM Mega Mall Inventory Backend is running"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db-health")
def database_health():
    try:
        # A small read verifies both credentials and Data API connectivity.
        get_supabase_client().table("products").select("id").limit(1).execute()
        return {"status": "connected", "database": "supabase"}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "supabase", "message": str(exc)},
        )


@app.get("/products")
def get_products():
    try:
        response = (
            get_supabase_client()
            .table("products")
            .select("*")
            .order("id")
            .execute()
        )
        return response.data
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": f"Could not load products: {exc}"},
        )


@app.post("/signup", status_code=201)
def signup(payload: SignupRequest):
    if payload.role != "staff":
        raise HTTPException(status_code=403, detail="Public signup is limited to staff")
    if not payload.full_name.strip():
        raise HTTPException(status_code=422, detail="full_name is required")
    if "@" not in payload.email or not payload.email.strip():
        raise HTTPException(status_code=422, detail="A valid email is required")
    if len(payload.password) < 8:
        raise HTTPException(
            status_code=422, detail="Password must be at least 8 characters"
        )

    try:
        admin = get_service_role_client()
        auth_response = admin.auth.admin.create_user(
            {
                "email": payload.email.strip().lower(),
                "password": payload.password,
                "email_confirm": True,
                "user_metadata": {"full_name": payload.full_name.strip()},
            }
        )
        auth_user = auth_response.user
        if not auth_user:
            raise RuntimeError("Supabase did not return the new user")

        admin.table("profiles").insert(
            {
                "id": str(auth_user.id),
                "full_name": payload.full_name.strip(),
                "email": payload.email.strip().lower(),
                "role": "staff",
                "is_active": True,
            }
        ).execute()
        return {
            "id": str(auth_user.id),
            "full_name": payload.full_name.strip(),
            "email": payload.email.strip().lower(),
            "role": "staff",
            "is_active": True,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_supabase_error_message(exc)) from exc


@app.post("/login")
def login(payload: LoginRequest):
    try:
        response = get_public_supabase_client().auth.sign_in_with_password(
            {"email": payload.email.strip().lower(), "password": payload.password}
        )
        if not response.session or not response.user:
            raise RuntimeError("Login did not return an access token")

        try:
            profile = _get_profile(str(response.user.id))
        except HTTPException:
            raise
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not profile["is_active"]:
            raise HTTPException(status_code=403, detail="User account is inactive")

        return {
            "access_token": response.session.access_token,
            "id": profile["id"],
            "full_name": profile["full_name"],
            "role": profile["role"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid email or password") from exc


@app.get("/me")
def current_user(user: dict[str, Any] = Depends(get_current_user)):
    return {
        "id": user["id"],
        "full_name": user["full_name"],
        "email": user["email"],
        "role": user["role"],
    }


@app.post("/ai/chat")
def ai_chat(
    request: AIChatRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    """Answer verified inventory questions without allowing inventory writes."""
    try:
        return chat_with_inventory_assistant(request.message, user["role"])
    except AIPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"AI assistant unavailable: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="AI assistant unavailable. Normal inventory APIs remain available.",
        ) from exc


@app.post("/ai/agent")
def ai_agent(
    request: AIAgentRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    """Use one controlled agent for verified answers or pending stock previews."""
    try:
        return handle_inventory_agent(
            request.message,
            user,
            [message.model_dump() for message in request.conversation],
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AIPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AIUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"AI assistant unavailable: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="AI agent unavailable. Normal inventory APIs remain available.",
        ) from exc


@app.post("/ai/stock-request", status_code=201)
def ai_stock_request(
    request: AIStockRequestBody,
    user: dict[str, Any] = Depends(get_current_user),
):
    """Parse and save a pending request; never changes product stock."""
    try:
        return prepare_stock_request(request.message, user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not create stock-change request: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/stock-change-requests/pending")
def pending_stock_change_requests(user: dict[str, Any] = Depends(get_current_user)):
    try:
        return list_pending_requests(user)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load pending stock-change requests: {_supabase_error_message(exc)}",
        ) from exc


@app.post("/stock-change-requests/{request_id}/confirm")
def confirm_stock_change_request(
    request_id: int,
    user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return confirm_request(request_id, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not confirm stock-change request: {_supabase_error_message(exc)}",
        ) from exc


@app.post("/stock-change-requests/{request_id}/cancel")
def cancel_stock_change_request(
    request_id: int,
    user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return cancel_request(request_id, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not cancel stock-change request: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/staff/products")
def staff_products(user: dict[str, Any] = Depends(get_current_user)):
    del user
    try:
        response = (
            get_service_role_client()
            .table("products")
            .select(
                "id,name,sku,quantity,minimum_stock,selling_price,location,categories(name)"
            )
            .order("id")
            .execute()
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "sku": row["sku"],
                "category": _category_name(row),
                "quantity": row["quantity"],
                "minimum_stock": row["minimum_stock"],
                "selling_price": row["selling_price"],
                "location": row["location"],
                "stock_status": _stock_status(row["quantity"], row["minimum_stock"]),
            }
            for row in response.data
        ]
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Could not load staff products: {_supabase_error_message(exc)}"
        ) from exc


@app.get("/manager/products")
def manager_products(user: dict[str, Any] = Depends(require_manager)):
    del user
    try:
        response = (
            get_service_role_client()
            .table("products")
            .select("*,categories(name),suppliers(id,name,phone,email,address)")
            .order("id")
            .execute()
        )
        return [
            {
                **row,
                "category": row.pop("categories", None),
                "supplier": row.pop("suppliers", None),
            }
            for row in response.data
        ]
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load manager products: {_supabase_error_message(exc)}",
        ) from exc


def _find_product(product_id: int) -> dict[str, Any]:
    response = (
        get_service_role_client()
        .table("products")
        .select("*")
        .eq("id", product_id)
        .limit(1)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Product not found")
    return response.data[0]


def _safe_product_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "sku": row["sku"],
        "category": _category_name(row),
        "quantity": row["quantity"],
        "minimum_stock": row["minimum_stock"],
        "selling_price": row["selling_price"],
        "location": row["location"],
        "stock_status": _stock_status(row["quantity"], row["minimum_stock"]),
    }


@app.get("/products/{product_id}")
def product_detail(
    product_id: int,
    user: dict[str, Any] = Depends(get_current_user),
):
    try:
        if user["role"] == "manager":
            response = (
                get_service_role_client()
                .table("products")
                .select("*,categories(name),suppliers(id,name,phone,email,address)")
                .eq("id", product_id)
                .limit(1)
                .execute()
            )
            if not response.data:
                raise HTTPException(status_code=404, detail="Product not found")
            row = response.data[0]
            return {
                **row,
                "category": row.pop("categories", None),
                "supplier": row.pop("suppliers", None),
            }

        response = (
            get_service_role_client()
            .table("products")
            .select(
                "id,name,sku,quantity,minimum_stock,selling_price,location,categories(name)"
            )
            .eq("id", product_id)
            .limit(1)
            .execute()
        )
        if not response.data:
            raise HTTPException(status_code=404, detail="Product not found")
        return _safe_product_row(response.data[0])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load product: {_supabase_error_message(exc)}",
        ) from exc


@app.post("/products", status_code=201)
def create_product(
    payload: ProductCreate,
    user: dict[str, Any] = Depends(require_manager),
):
    if payload.quantity != 0:
        raise HTTPException(
            status_code=422,
            detail="Create the product with quantity 0, then use the stock-in endpoint",
        )

    try:
        response = (
            get_service_role_client()
            .table("products")
            .insert(payload.model_dump())
            .execute()
        )
        if not response.data:
            raise RuntimeError("Product was not created")
        product = response.data[0]
        _write_audit_log(
            user["id"],
            "create",
            "products",
            str(product["id"]),
            new_data=product,
        )
        return product
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not create product: {_supabase_error_message(exc)}",
        ) from exc


@app.put("/products/{product_id}")
def update_product(
    product_id: int,
    payload: ProductUpdate,
    user: dict[str, Any] = Depends(require_manager),
):
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=422, detail="At least one field is required")

    try:
        old_product = _find_product(product_id)
        response = (
            get_service_role_client()
            .table("products")
            .update(updates)
            .eq("id", product_id)
            .execute()
        )
        product = response.data[0] if response.data else _find_product(product_id)
        _write_audit_log(
            user["id"],
            "update",
            "products",
            str(product_id),
            old_data=old_product,
            new_data=product,
        )
        return product
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not update product: {_supabase_error_message(exc)}",
        ) from exc


@app.delete("/products/{product_id}")
def delete_product(
    product_id: int,
    user: dict[str, Any] = Depends(require_manager),
):
    try:
        old_product = _find_product(product_id)
        get_service_role_client().table("products").delete().eq("id", product_id).execute()
        _write_audit_log(
            user["id"],
            "delete",
            "products",
            str(product_id),
            old_data=old_product,
        )
        return {"message": "Product deleted", "id": product_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not delete product: {_supabase_error_message(exc)}",
        ) from exc


@app.post("/stock/in")
def stock_in(
    payload: StockChangeRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    return _apply_stock_change("stock_in", payload, user)


@app.post("/stock/quick-restock")
def quick_restock(
    payload: StockChangeRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    """Receive one of the safe quick-restock quantities exposed by the UI."""
    if payload.quantity not in {10, 25, 50, 100}:
        raise HTTPException(
            status_code=422,
            detail="Quick restock quantity must be 10, 25, 50, or 100 units",
        )
    return _apply_stock_change("stock_in", payload, user)


@app.post("/stock/out")
def stock_out(
    payload: StockChangeRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    return _apply_stock_change("stock_out", payload, user)


@app.post("/stock/damaged")
def stock_damaged(
    payload: StockChangeRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    return _apply_stock_change("damaged", payload, user)


@app.post("/stock/return")
def stock_return(
    payload: StockChangeRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    return _apply_stock_change("return", payload, user)


@app.post("/stock/adjustment")
def stock_adjustment(
    payload: StockAdjustmentRequest,
    user: dict[str, Any] = Depends(require_manager),
):
    return _apply_stock_change("adjustment", payload, user)


@app.get("/stock/history")
def stock_history(
    product_id: int | None = None,
    movement_type: str | None = None,
    limit: int = 100,
    user: dict[str, Any] = Depends(get_current_user),
):
    del user
    try:
        query = (
            get_service_role_client()
            .table("stock_movements")
            .select("*,products(name,sku),suppliers(name)")
            .order("created_at", desc=True)
            .limit(min(max(limit, 1), 100))
        )
        if product_id is not None:
            query = query.eq("product_id", product_id)
        if movement_type:
            query = query.eq("movement_type", movement_type)
        return query.execute().data
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load stock history: {_supabase_error_message(exc)}",
        ) from exc


def _filtered_stock_products(out_of_stock: bool) -> list[dict[str, Any]]:
    response = (
        get_service_role_client()
        .table("products")
        .select("id,name,sku,quantity,minimum_stock,selling_price,location,categories(name)")
        .order("id")
        .execute()
    )
    if out_of_stock:
        rows = [row for row in response.data if row["quantity"] == 0]
    else:
        rows = [
            row
            for row in response.data
            if row["quantity"] > 0 and row["quantity"] <= row["minimum_stock"]
        ]
    return [_safe_product_row(row) for row in rows]


@app.get("/stock/low")
def low_stock(user: dict[str, Any] = Depends(get_current_user)):
    del user
    try:
        return _filtered_stock_products(out_of_stock=False)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load low-stock products: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/stock/out-of-stock")
def out_of_stock(user: dict[str, Any] = Depends(get_current_user)):
    del user
    try:
        return _filtered_stock_products(out_of_stock=True)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load out-of-stock products: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/audit-logs")
def audit_logs(
    limit: int = 100,
    user: dict[str, Any] = Depends(require_manager),
):
    del user
    try:
        return (
            get_service_role_client()
            .table("audit_logs")
            .select("*")
            .order("created_at", desc=True)
            .limit(min(max(limit, 1), 100))
            .execute()
            .data
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load audit logs: {_supabase_error_message(exc)}",
        ) from exc


def _utc_bounds(
    start_date: date_type | None = None,
    end_date: date_type | None = None,
) -> tuple[str | None, str | None]:
    """Convert inclusive Pakistan dates into UTC query bounds."""
    start_utc = None
    end_utc = None
    if start_date:
        start_local = datetime.combine(
            start_date, datetime.min.time(), tzinfo=LOCAL_TIMEZONE
        )
        start_utc = start_local.astimezone(timezone.utc).isoformat()
    if end_date:
        end_local = datetime.combine(
            end_date + timedelta(days=1), datetime.min.time(), tzinfo=LOCAL_TIMEZONE
        )
        end_utc = end_local.astimezone(timezone.utc).isoformat()
    return start_utc, end_utc


def _fetch_report_products() -> list[dict[str, Any]]:
    return (
        get_service_role_client()
        .table("products")
        .select(
            "id,name,sku,category_id,supplier_id,quantity,minimum_stock,"
            "selling_price,cost_price,location,is_active,categories(id,name),suppliers(id,name)"
        )
        .order("id")
        .execute()
        .data
    )


def _fetch_report_movements(
    start_date: date_type | None = None,
    end_date: date_type | None = None,
    movement_type: str | None = None,
    product_id: int | None = None,
) -> list[dict[str, Any]]:
    start_utc, end_utc = _utc_bounds(start_date, end_date)
    query = (
        get_service_role_client()
        .table("stock_movements")
        .select(
            "id,product_id,movement_type,quantity,old_quantity,new_quantity,"
            "supplier_id,reason,changed_by,created_at,products(id,name,sku,category_id),suppliers(name)"
        )
        .order("created_at", desc=True)
    )
    if start_utc:
        query = query.gte("created_at", start_utc)
    if end_utc:
        query = query.lt("created_at", end_utc)
    if movement_type:
        query = query.eq("movement_type", movement_type)
    if product_id is not None:
        query = query.eq("product_id", product_id)
    return query.execute().data


def _movement_report_row(row: dict[str, Any]) -> dict[str, Any]:
    product = row.get("products") or {}
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "product_name": product.get("name"),
        "sku": product.get("sku"),
        "movement_type": row["movement_type"],
        "quantity": row["quantity"],
        "old_quantity": row["old_quantity"],
        "new_quantity": row["new_quantity"],
        "reason": row.get("reason"),
        "changed_by": row.get("changed_by"),
        "created_at": row["created_at"],
    }


@app.get("/manager/dashboard-summary")
def manager_dashboard_summary(user: dict[str, Any] = Depends(require_manager)):
    del user
    try:
        products = _fetch_report_products()
        active_products = [product for product in products if product["is_active"]]
        today = datetime.now(LOCAL_TIMEZONE).date()
        today_movements = _fetch_report_movements(start_date=today, end_date=today)
        categories = get_service_role_client().table("categories").select("id").execute().data
        suppliers = get_service_role_client().table("suppliers").select("id").execute().data

        return {
            "total_active_products": len(active_products),
            "total_stock_quantity": sum(product["quantity"] for product in active_products),
            "low_stock_products_count": sum(
                1
                for product in active_products
                if product["quantity"] > 0
                and product["quantity"] <= product["minimum_stock"]
            ),
            "out_of_stock_products_count": sum(
                1 for product in active_products if product["quantity"] == 0
            ),
            "total_categories": len(categories),
            "total_suppliers": len(suppliers),
            "total_stock_movements_today": len(today_movements),
            "total_damaged_quantity_today": sum(
                movement["quantity"]
                for movement in today_movements
                if movement["movement_type"] == "damaged"
            ),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load dashboard summary: {_supabase_error_message(exc)}",
        ) from exc


def _daily_manager_summary_payload() -> dict[str, Any]:
    dashboard = manager_dashboard_summary({"role": "manager"})
    inventory_value = ai_tools.get_inventory_value("manager")
    recent_movements = ai_tools.get_recent_movements(10, "manager")
    return {
        **dashboard,
        "recent_movements": recent_movements,
        "inventory_cost_value": inventory_value["total_inventory_cost_value"],
        "inventory_selling_value": inventory_value["total_inventory_selling_value"],
        "estimated_profit": inventory_value["estimated_potential_profit"],
        "total_inventory_cost_value": inventory_value["total_inventory_cost_value"],
    }


@app.get("/manager/automation/daily-summary")
def manager_daily_summary(user: dict[str, Any] = Depends(require_manager_or_automation)):
    """Return the manager-only payload that an n8n schedule can email."""
    del user
    try:
        return _daily_manager_summary_payload()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load daily manager summary: {_supabase_error_message(exc)}",
        ) from exc


@app.post("/manager/automation/daily-summary/notify")
def notify_manager_daily_summary(user: dict[str, Any] = Depends(require_manager)):
    """Queue a manager-only daily summary notification for n8n."""
    del user
    try:
        payload = _daily_manager_summary_payload()
        queued = queue_n8n_event("daily_manager_summary", payload)
        return {"status": "queued" if queued else "not_configured", "event_type": "daily_manager_summary"}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not prepare daily manager summary: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/manager/recent-movements")
def manager_recent_movements(
    limit: int = 10,
    user: dict[str, Any] = Depends(require_manager),
):
    del user
    try:
        movements = _fetch_report_movements()
        return [_movement_report_row(row) for row in movements[: min(max(limit, 1), 100)]]
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load recent movements: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/manager/operational-areas")
def manager_operational_areas(user: dict[str, Any] = Depends(require_manager)):
    """Return mall operational areas without mixing them into product stock tables."""
    del user
    return MALL_OPERATIONAL_AREAS


def _report_stock_product(product: dict[str, Any]) -> dict[str, Any]:
    category = product.get("categories") or {}
    return {
        "id": product["id"],
        "name": product["name"],
        "sku": product["sku"],
        "quantity": product["quantity"],
        "minimum_stock": product["minimum_stock"],
        "category": category.get("name"),
        "location": product.get("location"),
    }


@app.get("/reports/low-stock")
def low_stock_report(user: dict[str, Any] = Depends(get_current_user)):
    del user
    try:
        products = _fetch_report_products()
        return [
            _report_stock_product(product)
            for product in products
            if product["is_active"]
            and product["quantity"] > 0
            and product["quantity"] <= product["minimum_stock"]
        ]
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load low-stock report: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/reports/out-of-stock")
def out_of_stock_report(user: dict[str, Any] = Depends(get_current_user)):
    del user
    try:
        products = _fetch_report_products()
        return [
            _report_stock_product(product)
            for product in products
            if product["is_active"] and product["quantity"] == 0
        ]
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load out-of-stock report: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/manager/reports/stock-movements")
def stock_movement_report(
    start_date: date_type | None = None,
    end_date: date_type | None = None,
    movement_type: str | None = None,
    product_id: int | None = None,
    category_id: int | None = None,
    user: dict[str, Any] = Depends(require_manager),
):
    del user
    valid_types = {"stock_in", "stock_out", "sale", "damaged", "return", "adjustment"}
    if movement_type and movement_type not in valid_types:
        raise HTTPException(status_code=422, detail="Invalid movement_type")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date cannot be after end_date")

    try:
        movements = _fetch_report_movements(
            start_date=start_date,
            end_date=end_date,
            movement_type=movement_type,
            product_id=product_id,
        )
        if category_id is not None:
            movements = [
                movement
                for movement in movements
                if (movement.get("products") or {}).get("category_id") == category_id
            ]

        totals = defaultdict(int)
        for movement in movements:
            totals[movement["movement_type"]] += movement["quantity"]

        return {
            "filters": {
                "start_date": start_date,
                "end_date": end_date,
                "movement_type": movement_type,
                "product_id": product_id,
                "category_id": category_id,
            },
            "summary": {
                "total_stock_in_quantity": totals["stock_in"],
                "total_stock_out_quantity": totals["stock_out"],
                "total_sale_quantity": totals["sale"],
                "total_damaged_quantity": totals["damaged"],
                "total_returned_quantity": totals["return"],
                "total_adjustments": totals["adjustment"],
                "number_of_movements": len(movements),
            },
            "movements": [_movement_report_row(movement) for movement in movements],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load stock movement report: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/manager/reports/top-moving-products")
def top_moving_products(
    days: int = 7,
    user: dict[str, Any] = Depends(require_manager),
):
    del user
    if days < 1 or days > 365:
        raise HTTPException(status_code=422, detail="days must be between 1 and 365")

    try:
        start_utc = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        movements = (
            get_service_role_client()
            .table("stock_movements")
            .select("product_id,movement_type,quantity,created_at,products(id,name,sku)")
            .gte("created_at", start_utc)
            .in_("movement_type", ["stock_out", "sale"])
            .execute()
            .data
        )
        products = {
            product["id"]: product
            for product in get_service_role_client()
            .table("products")
            .select("id,quantity")
            .execute()
            .data
        }
        totals: dict[int, int] = defaultdict(int)
        names: dict[int, dict[str, Any]] = {}
        for movement in movements:
            product = movement.get("products") or {}
            product_id = movement["product_id"]
            totals[product_id] += movement["quantity"]
            names[product_id] = product

        result = [
            {
                "product_id": product_id,
                "product_name": names[product_id].get("name"),
                "sku": names[product_id].get("sku"),
                "total_outgoing_quantity": quantity,
                "current_quantity": products.get(product_id, {}).get("quantity"),
            }
            for product_id, quantity in totals.items()
        ]
        return sorted(result, key=lambda item: item["total_outgoing_quantity"], reverse=True)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load top-moving report: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/manager/reports/damaged-stock")
def damaged_stock_report(
    start_date: date_type | None = None,
    end_date: date_type | None = None,
    product_id: int | None = None,
    user: dict[str, Any] = Depends(require_manager),
):
    del user
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date cannot be after end_date")
    try:
        movements = _fetch_report_movements(
            start_date=start_date,
            end_date=end_date,
            movement_type="damaged",
            product_id=product_id,
        )
        return {
            "total_damaged_quantity": sum(row["quantity"] for row in movements),
            "records": [
                {
                    "product": (row.get("products") or {}).get("name"),
                    "product_id": row["product_id"],
                    "damaged_quantity": row["quantity"],
                    "reason": row.get("reason"),
                    "changed_by": row.get("changed_by"),
                    "date": row["created_at"],
                }
                for row in movements
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load damaged-stock report: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/manager/reports/suppliers")
def supplier_report(user: dict[str, Any] = Depends(require_manager)):
    del user
    try:
        suppliers = (
            get_service_role_client().table("suppliers").select("id,name").order("id").execute().data
        )
        products = _fetch_report_products()
        stock_in_movements = _fetch_report_movements(movement_type="stock_in")
        result = []
        for supplier in suppliers:
            supplier_products = [
                product
                for product in products
                if product["is_active"] and product["supplier_id"] == supplier["id"]
            ]
            stock_in_quantity = sum(
                movement["quantity"]
                for movement in stock_in_movements
                if movement.get("supplier_id") == supplier["id"]
            )
            result.append(
                {
                    "supplier_id": supplier["id"],
                    "supplier_name": supplier["name"],
                    "active_products_count": len(supplier_products),
                    "total_current_stock": sum(
                        product["quantity"] for product in supplier_products
                    ),
                    "stock_in_quantity": stock_in_quantity,
                }
            )
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load supplier report: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/manager/reports/categories")
def category_report(user: dict[str, Any] = Depends(require_manager)):
    del user
    try:
        categories = (
            get_service_role_client().table("categories").select("id,name").order("id").execute().data
        )
        products = _fetch_report_products()
        result = []
        for category in categories:
            category_products = [
                product
                for product in products
                if product["is_active"] and product["category_id"] == category["id"]
            ]
            result.append(
                {
                    "category_id": category["id"],
                    "category_name": category["name"],
                    "active_products_count": len(category_products),
                    "total_quantity": sum(product["quantity"] for product in category_products),
                    "low_stock_count": sum(
                        1
                        for product in category_products
                        if product["quantity"] > 0
                        and product["quantity"] <= product["minimum_stock"]
                    ),
                    "out_of_stock_count": sum(
                        1 for product in category_products if product["quantity"] == 0
                    ),
                }
            )
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load category report: {_supabase_error_message(exc)}",
        ) from exc


@app.get("/manager/reports/inventory-value")
def inventory_value_report(user: dict[str, Any] = Depends(require_manager)):
    """Return current inventory valuation for active products only."""
    del user
    try:
        products = [product for product in _fetch_report_products() if product["is_active"]]
        product_values = []
        total_cost_value = 0.0
        total_selling_value = 0.0

        for product in products:
            quantity = product["quantity"] or 0
            cost_price = float(product["cost_price"] or 0)
            selling_price = float(product["selling_price"] or 0)
            cost_value = quantity * cost_price
            selling_value = quantity * selling_price
            total_cost_value += cost_value
            total_selling_value += selling_value
            product_values.append(
                {
                    "product_id": product["id"],
                    "product_name": product["name"],
                    "sku": product["sku"],
                    "quantity": quantity,
                    "cost_price": cost_price,
                    "selling_price": selling_price,
                    "inventory_cost_value": cost_value,
                    "inventory_selling_value": selling_value,
                }
            )

        return {
            "active_products_count": len(products),
            "total_inventory_cost_value": total_cost_value,
            "total_inventory_selling_value": total_selling_value,
            "estimated_potential_profit": total_selling_value - total_cost_value,
            "products": product_values,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Could not load inventory-value report: {_supabase_error_message(exc)}",
        ) from exc
