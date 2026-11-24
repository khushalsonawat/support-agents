"""The stand-in orders service: ownership, idempotency, one open return per order."""

import asyncio

import psycopg
import pytest

from orders_service.app import create_app
from tests.conftest import ORDERS_TOKEN, running


@pytest.fixture
async def orders(clean):
    async with running(create_app(clean["orders"], ORDERS_TOKEN)) as client:
        client.headers["Authorization"] = f"Bearer {ORDERS_TOKEN}"
        yield client


def as_customer(customer):
    return {"X-Customer-Id": customer}


async def test_the_token_and_the_customer_are_required(orders):
    assert (await orders.get("/orders/A1001", headers={"Authorization": "Bearer wrong", **as_customer("C001")})).status_code == 401
    assert (await orders.get("/orders/A1001")).status_code == 400


async def test_a_customer_sees_only_their_own_orders(orders):
    mine = await orders.get("/orders/A1001", headers=as_customer("C001"))
    assert mine.status_code == 200 and mine.json()["item"] == "trail running shoes"
    theirs = await orders.get("/orders/A1001", headers=as_customer("C002"))
    missing = await orders.get("/orders/Z9999", headers=as_customer("C002"))
    # Another customer's order is answered exactly like one that does not exist.
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()


async def test_recent_orders_are_the_customers_newest_first(orders):
    found = (await orders.get("/orders?limit=5", headers=as_customer("C001"))).json()["orders"]
    assert [o["id"] for o in found] == ["A1004", "A1003", "A1001"]


async def test_a_repeated_key_returns_the_first_answer_and_starts_one_return(orders, clean):
    headers = {**as_customer("C001"), "Idempotency-Key": "k-1"}
    first, second = await asyncio.gather(
        orders.post("/orders/A1001/returns", json={"reason": "too small"}, headers=headers),
        orders.post("/orders/A1001/returns", json={"reason": "too small"}, headers=headers),
    )
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    with psycopg.connect(clean["orders"]) as conn:
        assert conn.execute("SELECT count(*) FROM returns WHERE order_id = 'A1001'").fetchone()[0] == 1


async def test_a_second_open_return_is_refused(orders):
    await orders.post("/orders/A1003/returns", json={"reason": "leaks"}, headers={**as_customer("C001"), "Idempotency-Key": "a"})
    again = await orders.post("/orders/A1003/returns", json={"reason": "leaks"}, headers={**as_customer("C001"), "Idempotency-Key": "b"})
    assert again.status_code == 409 and "already" in again.json()["message"]


async def test_an_order_not_yet_delivered_cannot_be_returned(orders):
    response = await orders.post("/orders/A1004/returns", json={"reason": "late"}, headers=as_customer("C001"))
    assert response.status_code == 409 and "processing" in response.json()["message"]


async def test_another_customers_order_cannot_be_returned_or_checked(orders):
    assert (await orders.post("/orders/A1001/returns", json={"reason": "x"}, headers=as_customer("C002"))).status_code == 404
    await orders.post("/orders/A1001/returns", json={"reason": "x"}, headers=as_customer("C001"))
    assert (await orders.get("/orders/A1001/return", headers=as_customer("C002"))).status_code == 404
    assert (await orders.get("/orders/A1001/return", headers=as_customer("C001"))).json()["status"] == "requested"


async def test_sql_in_an_order_id_is_only_a_value(orders, clean):
    response = await orders.get("/orders/'; DROP TABLE orders; --", headers=as_customer("C001"))
    assert response.status_code == 404
    with psycopg.connect(clean["orders"]) as conn:
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 6


async def test_the_policy_comes_from_the_shop(orders):
    text = (await orders.get("/policy", headers=as_customer("C001"))).json()["text"]
    assert text.startswith("Delivered items can be returned within 30 days")
