import uuid

import httpx
import pytest

from app.clients.orders_api import OrdersClient, OrdersError, return_idempotency_key

ORDER = {"id": "A1001", "item": "shoes", "status": "delivered", "placed_at": "2026-11-02T10:00:00+00:00"}
RETURN = {"id": "r1", "order_id": "A1001", "status": "requested", "reason": "too small"}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def client_with(responses, clock=None):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        if clock is not None:
            clock.now += seconds

    client = OrdersClient(
        "http://orders.test", "orders-token", transport=httpx.MockTransport(handler),
        sleep=sleep, clock=clock or Clock(),
    )
    return client, calls, slept


async def test_every_call_names_the_customer_and_carries_the_token():
    client, calls, _ = client_with([httpx.Response(200, json=ORDER)])
    await client.get_order("C001", "A1001")
    assert calls[0].headers["X-Customer-Id"] == "C001"
    assert calls[0].headers["Authorization"] == "Bearer orders-token"


async def test_a_get_is_retried_on_503_then_succeeds():
    client, calls, slept = client_with([httpx.Response(503), httpx.Response(200, json=ORDER)])
    order = await client.get_order("C001", "A1001")
    assert order.id == "A1001" and len(calls) == 2 and slept == [0.2]


async def test_a_get_gives_up_after_two_retries():
    client, calls, slept = client_with([httpx.ConnectError("down")])
    with pytest.raises(OrdersError) as exc:
        await client.get_order("C001", "A1001")
    assert exc.value.kind == "unavailable" and len(calls) == 3 and slept == [0.2, 0.8]


async def test_a_4xx_is_never_retried():
    client, calls, _ = client_with([httpx.Response(404, json={"error": "order_not_found"})])
    with pytest.raises(OrdersError) as exc:
        await client.get_order("C001", "A1001")
    assert exc.value.kind == "not_found" and len(calls) == 1


async def test_a_post_with_a_key_is_retried_and_keeps_its_key():
    client, calls, _ = client_with([httpx.ReadTimeout("slow"), httpx.Response(201, json=RETURN)])
    await client.start_return("C001", "A1001", "too small", "key-1")
    assert len(calls) == 2
    assert {c.headers["Idempotency-Key"] for c in calls} == {"key-1"}


async def test_the_call_budget_stops_retries():
    clock = Clock()

    def slow(request):
        clock.now += 7.0  # each attempt uses 7 s of the 12 s budget
        raise httpx.ReadTimeout("slow")

    client = OrdersClient(
        "http://orders.test", "t", transport=httpx.MockTransport(slow),
        sleep=lambda s: _noop(), clock=clock,
    )
    with pytest.raises(OrdersError):
        await client.get_order("C001", "A1001")
    assert clock.now == 7.0  # one attempt: 5 s left is under the 6 s needed to retry


async def _noop():
    return None


async def test_a_wrong_agent_token_is_an_operator_problem_not_the_customers():
    client, _, _ = client_with([httpx.Response(401)])
    with pytest.raises(OrdersError) as exc:
        await client.get_policy("C001")
    assert exc.value.kind == "unavailable"


@pytest.mark.parametrize("bad", ["../admin", "A1001/return", "'; DROP TABLE orders; --", "", "x" * 65])
async def test_an_order_id_from_the_model_stays_one_path_segment(bad):
    client, calls, _ = client_with([httpx.Response(200, json=ORDER)])
    with pytest.raises(OrdersError) as exc:
        await client.get_order("C001", bad)
    assert exc.value.kind == "not_found" and calls == []


def test_the_return_key_is_the_same_for_the_same_conversation_and_order():
    conversation = uuid.uuid4()
    assert return_idempotency_key(conversation, "A1001") == return_idempotency_key(conversation, "A1001")
    assert return_idempotency_key(conversation, "A1001") != return_idempotency_key(conversation, "A1003")
    assert return_idempotency_key(uuid.uuid4(), "A1001") != return_idempotency_key(conversation, "A1001")
