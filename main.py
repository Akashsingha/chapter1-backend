from fastapi import FastAPI, HTTPException, Header, Query, Request
from pydantic import BaseModel, field_validator
from database import supabase
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from datetime import datetime, date
from typing import Optional
import os
import re
import time

app = FastAPI()

# ── Rate limiter ──────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Config ────────────────────────────────────────────────
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")
DASHBOARD_API_KEY = os.getenv("DASHBOARD_API_KEY", DASHBOARD_PASSWORD)  # fallback to password if key not set

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://chapter1-menu-lppv.vercel.app",
    ],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-Dashboard-Key"],
)


# ── Auth helper ───────────────────────────────────────────
def require_dashboard_key(x_dashboard_key: str = Header(None)):
    """Verify the X-Dashboard-Key header matches our secret."""
    if not x_dashboard_key or x_dashboard_key != DASHBOARD_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden — invalid or missing dashboard key")


# ── Idempotency cache (in-memory, simple) ─────────────────
# Maps idempotency_key -> (timestamp, response_data)
_idempotency_cache: dict[str, tuple[float, dict]] = {}
IDEMPOTENCY_TTL = 60  # seconds


def _cleanup_idempotency_cache():
    """Remove expired entries."""
    now = time.time()
    expired = [k for k, (ts, _) in _idempotency_cache.items() if now - ts > IDEMPOTENCY_TTL]
    for k in expired:
        del _idempotency_cache[k]


# ── Models with validation ────────────────────────────────

class OrderItem(BaseModel):
    id: Optional[int] = None   # used for server-side price lookup
    name: str
    price: int
    quantity: int

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("Price must be greater than 0")
        return v

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_valid(cls, v):
        if v <= 0:
            raise ValueError("Quantity must be greater than 0")
        if v > 50:
            raise ValueError("Quantity cannot exceed 50")
        return v

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError("Item name cannot be empty")
        return v.strip()


class Order(BaseModel):
    customer_name: str
    customer_phone: str
    items: list[OrderItem]
    payment_method: str = "cash"
    idempotency_key: Optional[str] = None   # Fix #6 — duplicate prevention

    @field_validator("customer_name")
    @classmethod
    def name_must_be_valid(cls, v):
        if not v.strip():
            raise ValueError("Customer name is required")
        if len(v.strip()) > 100:
            raise ValueError("Customer name is too long")
        return v.strip()

    @field_validator("customer_phone")
    @classmethod
    def phone_must_be_valid(cls, v):
        if not re.match(r"^[6-9]\d{9}$", v):
            raise ValueError("Please enter a valid 10-digit Indian phone number")
        return v

    @field_validator("items")
    @classmethod
    def items_must_not_be_empty(cls, v):
        if len(v) == 0:
            raise ValueError("Order must have at least one item")
        return v

    @field_validator("payment_method")
    @classmethod
    def payment_method_must_be_valid(cls, v):
        if v not in ("cash", "upi"):
            raise ValueError("Payment method must be 'cash' or 'upi'")
        return v


class StatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def status_must_be_valid(cls, v):
        if v not in ("received", "preparing", "ready"):
            raise ValueError("Status must be 'received', 'preparing', or 'ready'")
        return v


class PaymentConfirm(BaseModel):
    payment_status: str

    @field_validator("payment_status")
    @classmethod
    def payment_status_must_be_valid(cls, v):
        if v not in ("pending", "confirmed"):
            raise ValueError("Payment status must be 'pending' or 'confirmed'")
        return v


class PasswordCheck(BaseModel):
    password: str


# ── Helper: format order items ────────────────────────────
def _format_items(items_data):
    """Map item_name -> name for frontend consistency."""
    formatted = []
    for item in items_data:
        formatted.append({
            "name": item.get("item_name", item.get("name", "")),
            "price": item.get("price", 0),
            "quantity": item.get("quantity", 0),
            "order_id": item.get("order_id"),
            "id": item.get("id"),
        })
    return formatted


# ── Public endpoints ──────────────────────────────────────

@app.get("/menu")
def get_menu():
    """Get all menu items — public. Returns all items including unavailable ones
    so the dashboard can manage them. Customer frontend filters is_available."""
    try:
        response = supabase.table("menu_items").select("*").execute()
        return response.data
    except Exception:
        raise HTTPException(status_code=500, detail="Could not load menu")


@app.post("/orders")
@limiter.limit("5/minute")
def create_order(request: Request, order: Order):
    """Place a new order — public. Rate limited to 5 per minute per IP."""

    # Fix #6 — Check idempotency key for duplicate prevention
    if order.idempotency_key:
        _cleanup_idempotency_cache()
        if order.idempotency_key in _idempotency_cache:
            _, cached_response = _idempotency_cache[order.idempotency_key]
            return cached_response

    # ── Server-side price validation ──────────────────────
    # Fetch actual prices from DB — never trust client-sent prices
    item_ids = [item.id for item in order.items if item.id is not None]
    menu_map: dict[int, dict] = {}
    if item_ids:
        try:
            menu_resp = supabase.table("menu_items").select("id,name,price,is_available").in_("id", item_ids).execute()
            menu_map = {m["id"]: m for m in menu_resp.data}
        except Exception:
            raise HTTPException(status_code=500, detail="Could not validate menu prices. Please try again.")

    total = 0
    for item in order.items:
        if item.id and item.id in menu_map:
            db_item = menu_map[item.id]
            # Block orders for unavailable items
            if db_item.get("is_available") is False:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{db_item['name']}' is currently unavailable. Please remove it from your cart."
                )
            # Use DB price — client price is completely ignored
            total += db_item["price"] * item.quantity
        else:
            # Fallback: item has no ID (shouldn't happen in normal flow)
            total += item.price * item.quantity

    try:
        # Save order header
        order_response = supabase.table("orders").insert({
            "customer_name": order.customer_name,
            "customer_phone": order.customer_phone,
            "total_amount": total,
            "status": "received",
            "payment_method": order.payment_method,
            "payment_status": "pending"
        }).execute()

        order_id = order_response.data[0]["id"]

        # ── Atomic batch insert for all order items ───────
        # Single DB call instead of a loop — reduces partial-data risk
        items_to_insert = [
            {
                "order_id": order_id,
                "item_name": item.name,
                "quantity": item.quantity,
                # Store validated DB price, not client price
                "price": menu_map[item.id]["price"] if (item.id and item.id in menu_map) else item.price,
            }
            for item in order.items
        ]
        supabase.table("order_items").insert(items_to_insert).execute()

        response_data = {
            "id": order_id,
            "customer_name": order.customer_name,
            "total": total,
            "total_amount": total,
            "status": "received",
            "payment_method": order.payment_method,
            "payment_status": "pending"
        }

        # Cache for idempotency
        if order.idempotency_key:
            _idempotency_cache[order.idempotency_key] = (time.time(), response_data)

        return response_data

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Could not place order. Please try again.")


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    """Get a single order by ID — public (needed for confirmed/payment pages).
    PII (customer_phone) is stripped — only visible in the authenticated dashboard."""
    try:
        response = supabase.table("orders").select("*").eq("id", order_id).execute()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid order ID")

    if not response.data:
        raise HTTPException(status_code=404, detail="Order not found")

    order = response.data[0]

    # Fetch and format order items
    try:
        items_response = supabase.table("order_items").select("*").eq("order_id", order_id).execute()
        order["items"] = _format_items(items_response.data)
    except Exception:
        order["items"] = []

    # Strip PII from public response
    order.pop("customer_phone", None)

    return order


# ── Dashboard endpoints (require API key) ─────────────────

@app.get("/orders")
def get_orders(
    x_dashboard_key: str = Header(None),
    date_filter: Optional[str] = Query(None, alias="date"),
):
    """Get all orders with full PII — requires dashboard key. Supports ?date=YYYY-MM-DD filter."""
    require_dashboard_key(x_dashboard_key)

    try:
        query = supabase.table("orders").select("*")

        if date_filter:
            try:
                filter_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
        else:
            filter_date = date.today()

        start = f"{filter_date}T00:00:00"
        end = f"{filter_date}T23:59:59"
        query = query.gte("created_at", start).lte("created_at", end)

        # Limit to 200 orders max
        query = query.order("created_at", desc=True).limit(200)

        response = query.execute()
        return response.data

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Could not fetch orders")


@app.patch("/orders/{order_id}/status")
def update_order_status(order_id: str, body: StatusUpdate, x_dashboard_key: str = Header(None)):
    """Update order status — requires dashboard key."""
    require_dashboard_key(x_dashboard_key)

    try:
        existing = supabase.table("orders").select("id, status").eq("id", order_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Order not found")

        supabase.table("orders").update({
            "status": body.status
        }).eq("id", order_id).execute()

        return {"message": "order updated"}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Could not update order status")


@app.patch("/orders/{order_id}/payment")
def confirm_payment(order_id: str, body: PaymentConfirm, x_dashboard_key: str = Header(None)):
    """Update payment status — requires dashboard key."""
    require_dashboard_key(x_dashboard_key)

    try:
        existing = supabase.table("orders").select("id").eq("id", order_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Order not found")

        supabase.table("orders").update({
            "payment_status": body.payment_status
        }).eq("id", order_id).execute()

        return {"message": "payment status updated"}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Could not update payment status")


@app.patch("/orders/{order_id}/cancel")
def cancel_order(order_id: str, x_dashboard_key: str = Header(None)):
    """Cancel an order — only if status is 'received'. Requires dashboard key."""
    require_dashboard_key(x_dashboard_key)

    try:
        existing = supabase.table("orders").select("id, status").eq("id", order_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Order not found")

        if existing.data[0]["status"] != "received":
            raise HTTPException(status_code=400, detail="Only 'received' orders can be cancelled")

        supabase.table("orders").update({
            "status": "cancelled"
        }).eq("id", order_id).execute()

        return {"message": "order cancelled"}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Could not cancel order")


@app.patch("/orders/{order_id}/acknowledge")
def acknowledge_order(order_id: str, x_dashboard_key: str = Header(None)):
    """Mark an order as acknowledged — persisted in DB so it syncs across all staff devices.
    Requires dashboard key."""
    require_dashboard_key(x_dashboard_key)

    try:
        existing = supabase.table("orders").select("id").eq("id", order_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Order not found")

        supabase.table("orders").update({"acknowledged": True}).eq("id", order_id).execute()
        return {"message": "acknowledged"}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Could not acknowledge order")


@app.patch("/menu/{item_id}/availability")
def toggle_menu_item_availability(item_id: int, x_dashboard_key: str = Header(None)):
    """Toggle a menu item between available and sold out. Requires dashboard key."""
    require_dashboard_key(x_dashboard_key)

    try:
        current = supabase.table("menu_items").select("id,name,is_available").eq("id", item_id).execute()
        if not current.data:
            raise HTTPException(status_code=404, detail="Menu item not found")

        current_avail = current.data[0].get("is_available", True)
        # If is_available is NULL (column just added), treat as True
        if current_avail is None:
            current_avail = True
        new_avail = not current_avail

        supabase.table("menu_items").update({"is_available": new_avail}).eq("id", item_id).execute()
        return {"id": item_id, "is_available": new_avail}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Could not update item availability")


# ── Auth ──────────────────────────────────────────────────

@app.post("/verify-dashboard-password")
def verify_dashboard_password(body: PasswordCheck):
    """Verify dashboard password and return API key on success."""
    if body.password == DASHBOARD_PASSWORD:
        return {
            "valid": True,
            "api_key": DASHBOARD_API_KEY,
        }
    return {"valid": False}