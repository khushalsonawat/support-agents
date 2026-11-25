from __future__ import annotations

from app.clients.orders_api import OrdersClient, OrdersError, return_idempotency_key
from app.context import TurnContext


async def start_return(
    ctx: TurnContext, orders: OrdersClient, order_id: str, reason: str
) -> str:
    """Start a return for one of the customer's delivered orders, giving the customer's reason."""
    if not reason:
        return "Ask the customer why they are returning it, then try again."
    key = return_idempotency_key(ctx.conversation_id, order_id)
    try:
        started = await orders.start_return(ctx.customer_id, order_id, reason[:500], key)
    except OrdersError as exc:
        if exc.kind == "not_found":
            return f"There is no order {order_id} on this customer's account."
        if exc.kind == "not_allowed":
            return f"Order {order_id} cannot be returned: {exc.message or 'not allowed'}."
        raise
    return f"Return {started.id} started for order {started.order_id}, status {started.status}."


async def return_status(ctx: TurnContext, orders: OrdersClient, order_id: str) -> str:
    """Report the status of the return on one of the customer's orders."""
    try:
        found = await orders.get_return(ctx.customer_id, order_id)
    except OrdersError as exc:
        if exc.kind == "not_found":
            return f"There is no return on order {order_id} for this customer."
        raise
    return f"The return on order {found.order_id} is {found.status} (reason given: {found.reason})."
