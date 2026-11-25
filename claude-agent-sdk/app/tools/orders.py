from __future__ import annotations

from app.clients.orders_api import OrdersClient, OrdersError
from app.context import TurnContext


async def look_up_order(ctx: TurnContext, orders: OrdersClient, order_id: str) -> str:
    """Look up one of the customer's orders by its id and report its item and status."""
    try:
        order = await orders.get_order(ctx.customer_id, order_id)
    except OrdersError as exc:
        if exc.kind == "not_found":
            return f"There is no order {order_id} on this customer's account."
        raise
    return f"Order {order.id}: {order.item}, status {order.status}, placed {order.placed_at[:10]}."


async def list_recent_orders(ctx: TurnContext, orders: OrdersClient) -> str:
    """List the customer's five most recent orders."""
    found = await orders.list_orders(ctx.customer_id, limit=5)
    if not found:
        return "This customer has no orders."
    lines = [f"- {o.id}: {o.item}, {o.status}, placed {o.placed_at[:10]}" for o in found]
    return "The customer's recent orders:\n" + "\n".join(lines)
