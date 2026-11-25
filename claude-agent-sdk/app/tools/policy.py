from __future__ import annotations

from app.clients.orders_api import OrdersClient
from app.context import TurnContext


async def return_policy(ctx: TurnContext, orders: OrdersClient) -> str:
    """State the shop's return policy, as the shop currently sets it."""
    return await orders.get_policy(ctx.customer_id)
