"""The /chat API end to end, with a fake model and the real orders service in-process."""

import asyncio

import psycopg
import pytest

from app import main
from app.runner import ModelUnavailable
from tests.conftest import FakeRunner, agent_service


def chat(customer="C001", message="Where is my order A1001?", conversation_id=None):
    return {"customer_id": customer, "message": message, "conversation_id": conversation_id}


async def test_health_needs_nothing(clean):
    async with agent_service(clean, FakeRunner()) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        ready = await client.get("/ready")
        assert ready.status_code == 200 and ready.json() == {"database": True, "orders_service": True}


async def test_no_token_is_401_before_the_body_is_read(clean):
    async with agent_service(clean, FakeRunner()) as client:
        response = await client.post("/chat", content="not json", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401


@pytest.mark.parametrize(
    "body, status, code",
    [
        ({"message": "hi"}, 400, "invalid_request"),
        (chat(message="   "), 400, "invalid_request"),
        (chat(message="x" * 4001), 413, "message_too_long"),
        (chat(conversation_id="not-a-uuid"), 400, "invalid_request"),
    ],
)
async def test_bad_requests(clean, body, status, code):
    async with agent_service(clean, FakeRunner()) as client:
        response = await client.post("/chat", json=body)
        assert response.status_code == status and response.json()["error"] == code


async def test_a_conversation_with_tool_calls_and_history(clean):
    runner = FakeRunner(script=[("look_up_order", {"order_id": "A1001"})])
    async with agent_service(clean, runner) as client:
        first = (await client.post("/chat", json=chat())).json()
        assert first["reply"].startswith("Order A1001: trail running shoes, status delivered")
        assert first["handed_off"] is False and first["ticket_id"] is None
        second = (await client.post("/chat", json=chat(message="And A1003?", conversation_id=first["conversation_id"]))).json()
        assert second["conversation_id"] == first["conversation_id"]
    assert runner.turns[1].history == [("customer", "Where is my order A1001?"), ("agent", first["reply"])]
    assert runner.turns[1].message == "And A1003?"


async def test_the_tools_act_for_the_callers_customer_only(clean):
    runner = FakeRunner(script=[("look_up_order", {"order_id": "A1001"})])
    async with agent_service(clean, runner) as client:
        reply = (await client.post("/chat", json=chat(customer="C002"))).json()["reply"]
    assert reply == "There is no order A1001 on this customer's account."


async def test_another_customers_conversation_is_not_found(clean):
    async with agent_service(clean, FakeRunner()) as client:
        mine = (await client.post("/chat", json=chat())).json()
        response = await client.post("/chat", json=chat(customer="C002", conversation_id=mine["conversation_id"]))
        assert response.status_code == 404 and response.json()["error"] == "conversation_not_found"


async def test_a_second_message_while_one_runs_is_409(clean):
    runner = FakeRunner(delay=0.3)
    async with agent_service(clean, runner) as client:
        first = (await client.post("/chat", json=chat())).json()
        slow, busy = await asyncio.gather(
            client.post("/chat", json=chat(message="one", conversation_id=first["conversation_id"])),
            _after(0.05, client.post("/chat", json=chat(message="two", conversation_id=first["conversation_id"]))),
        )
        assert slow.status_code == 200
        assert busy.status_code == 409 and busy.json()["error"] == "conversation_busy"


async def _after(seconds, coroutine):
    await asyncio.sleep(seconds)
    return await coroutine


async def test_a_started_return_is_kept_once(clean):
    runner = FakeRunner(script=[("start_return", {"order_id": "A1003", "reason": "leaks"})] * 2)
    async with agent_service(clean, runner) as client:
        response = (await client.post("/chat", json=chat(message="Return A1003, it leaks"))).json()
    assert response["reply"].startswith("Return ") and "A1003" in response["reply"]
    with psycopg.connect(clean["orders"]) as conn:
        assert conn.execute("SELECT count(*) FROM returns WHERE order_id = 'A1003'").fetchone()[0] == 1


async def test_a_handoff_is_saved_and_later_messages_skip_the_model(clean):
    runner = FakeRunner(
        script=[("hand_off_to_person", {"reason": "wants a manager"})],
        reply=lambda results: "A person from our team will reply here.",
    )
    async with agent_service(clean, runner) as client:
        first = (await client.post("/chat", json=chat(message="Get me a manager"))).json()
        assert first["handed_off"] is True and first["ticket_id"]
        later = (await client.post("/chat", json=chat(message="Hello?", conversation_id=first["conversation_id"]))).json()
    assert later["reply"] == main.HANDED_OFF_REPLY and later["handed_off"] is True
    assert len(runner.turns) == 1
    with psycopg.connect(clean["agent"]) as conn:
        row = conn.execute("SELECT id::text, customer_id, reason, status FROM handoff_tickets").fetchone()
    assert row == (first["ticket_id"], "C001", "wants a manager", "open")


@pytest.mark.parametrize(
    "runner, status, code",
    [
        (FakeRunner(error=ModelUnavailable("down")), 503, "model_unavailable"),
        (FakeRunner(delay=5), 504, "turn_timeout"),
    ],
)
async def test_a_failed_turn_saves_nothing_and_frees_the_conversation(clean, monkeypatch, runner, status, code):
    monkeypatch.setattr(main, "TURN_SECONDS", 0.2)
    async with agent_service(clean, FakeRunner()) as client:
        first = (await client.post("/chat", json=chat())).json()
    async with agent_service(clean, runner) as client:
        failed = await client.post("/chat", json=chat(message="again", conversation_id=first["conversation_id"]))
        assert failed.status_code == status and failed.json()["error"] == code
    async with agent_service(clean, FakeRunner()) as client:
        after = await client.post("/chat", json=chat(message="third", conversation_id=first["conversation_id"]))
        assert after.status_code == 200
    with psycopg.connect(clean["agent"]) as conn:
        messages = [r[0] for r in conn.execute("SELECT content FROM messages WHERE role = 'customer' ORDER BY id")]
    assert messages == ["Where is my order A1001?", "third"]


async def test_turns_past_the_limit_get_429(clean):
    async with agent_service(clean, FakeRunner(delay=0.3), max_concurrent_turns=1) as client:
        first, second = await asyncio.gather(
            client.post("/chat", json=chat()),
            _after(0.05, client.post("/chat", json=chat(message="other"))),
        )
        assert first.status_code == 200
        assert second.status_code == 429 and second.headers["Retry-After"] == "5"


async def test_sql_in_a_message_is_stored_as_text(clean):
    text = "'); DROP TABLE messages; --"
    async with agent_service(clean, FakeRunner()) as client:
        assert (await client.post("/chat", json=chat(message=text))).status_code == 200
    with psycopg.connect(clean["agent"]) as conn:
        assert conn.execute("SELECT count(*) FROM messages WHERE content = %s", (text,)).fetchone()[0] == 1


async def test_ready_is_false_when_the_orders_service_is_down(clean):
    import httpx

    from app.main import create_app
    from tests.conftest import make_settings, running

    def down(request):
        raise httpx.ConnectError("down")

    app = create_app(make_settings(clean["agent"]), runner=FakeRunner(), orders_transport=httpx.MockTransport(down))
    async with running(app) as client:
        ready = await client.get("/ready")
    assert ready.status_code == 503 and ready.json() == {"database": True, "orders_service": False}
