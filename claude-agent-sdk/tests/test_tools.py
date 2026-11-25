"""The tools act for the turn's customer only, and never raise."""

import asyncio
import uuid

from app.clients.orders_api import Order, OrdersError, Return
from app.context import TurnContext
from app.tools import TOOL_NAMES, UNAVAILABLE, build_tools


class RecordingOrders:
    def __init__(self, fail: OrdersError | None = None):
        self.calls = []
        self.fail = fail

    async def _record(self, *args):
        self.calls.append(args)
        await asyncio.sleep(0)  # let another turn run in between
        if self.fail:
            raise self.fail

    async def get_order(self, customer_id, order_id):
        await self._record("get_order", customer_id, order_id)
        return Order(order_id, "shoes", "delivered", "2026-11-02T10:00:00+00:00")

    async def list_orders(self, customer_id, limit=5):
        await self._record("list_orders", customer_id, limit)
        return []

    async def start_return(self, customer_id, order_id, reason, key):
        await self._record("start_return", customer_id, order_id, reason, key)
        return Return("r1", order_id, "requested", reason)

    async def get_return(self, customer_id, order_id):
        await self._record("get_return", customer_id, order_id)
        return Return("r1", order_id, "requested", "too small")

    async def get_policy(self, customer_id):
        await self._record("get_policy", customer_id)
        return "30 days."


def tools_for(customer, orders):
    ctx = TurnContext(customer, uuid.uuid4(), "req")
    return ctx, {spec.name: spec for spec in build_tools(ctx, orders)}


def test_six_tools_and_none_takes_a_customer():
    _, tools = tools_for("C001", RecordingOrders())
    assert tuple(tools) == TOOL_NAMES
    for spec in tools.values():
        assert "customer_id" not in spec.parameters
        assert spec.description


async def test_two_turns_at_once_each_see_their_own_customer():
    orders = RecordingOrders()
    _, first = tools_for("C001", orders)
    _, second = tools_for("C002", orders)
    await asyncio.gather(
        first["look_up_order"].handler({"order_id": "A1001"}),
        second["look_up_order"].handler({"order_id": "A1002"}),
        first["list_recent_orders"].handler({}),
        second["return_policy"].handler({}),
    )
    seen = {(call[0], call[1], call[2] if len(call) > 2 else None) for call in orders.calls}
    assert ("get_order", "C001", "A1001") in seen
    assert ("get_order", "C002", "A1002") in seen
    assert ("list_orders", "C001", 5) in seen
    assert ("get_policy", "C002", None) in seen


async def test_a_customer_id_from_the_model_is_ignored():
    orders = RecordingOrders()
    _, tools = tools_for("C001", orders)
    await tools["look_up_order"].handler({"order_id": "A1002", "customer_id": "C002"})
    assert orders.calls == [("get_order", "C001", "A1002")]


async def test_start_return_uses_the_conversations_key_both_times():
    orders = RecordingOrders()
    ctx, tools = tools_for("C001", orders)
    await tools["start_return"].handler({"order_id": "A1001", "reason": "too small"})
    await tools["start_return"].handler({"order_id": "A1001", "reason": "too small"})
    keys = {call[4] for call in orders.calls}
    assert len(keys) == 1


async def test_an_orders_outage_is_a_sentence_not_an_error():
    _, tools = tools_for("C001", RecordingOrders(fail=OrdersError("unavailable")))
    for name, args in [
        ("look_up_order", {"order_id": "A1001"}),
        ("list_recent_orders", {}),
        ("start_return", {"order_id": "A1001", "reason": "x"}),
        ("return_status", {"order_id": "A1001"}),
        ("return_policy", {}),
    ]:
        assert await tools[name].handler(args) == UNAVAILABLE


async def test_not_found_does_not_say_whose_order_it_is():
    _, tools = tools_for("C001", RecordingOrders(fail=OrdersError("not_found")))
    text = await tools["look_up_order"].handler({"order_id": "A1002"})
    assert text == "There is no order A1002 on this customer's account."


async def test_handoff_writes_nothing_and_records_one_ticket():
    ctx, tools = tools_for("C001", RecordingOrders())
    first = await tools["hand_off_to_person"].handler({"reason": "wants a manager"})
    second = await tools["hand_off_to_person"].handler({"reason": "again"})
    assert ctx.ticket is not None and ctx.ticket.reason == "wants a manager"
    assert str(ctx.ticket.id) in first and str(ctx.ticket.id) in second
