"""One turn per conversation at a time, and the migrations, against a real PostgreSQL."""

import asyncio
import uuid

import psycopg

from app.context import HandoffTicket
from app.conversations import ClaimStatus, ConversationStore
from app.db import migrate
from app.db.session import make_pool
from tests.conftest import ROOT


async def store_for(uri):
    pool = make_pool(uri)
    await pool.open()
    return pool, ConversationStore(pool)


async def test_two_claims_at_once_one_is_busy(clean):
    pool, store = await store_for(clean["agent"])
    try:
        conversation = await store.create_claimed("C001")
        await store.release(conversation)
        first, second = await asyncio.gather(
            store.claim(conversation, "C001"), store.claim(conversation, "C001")
        )
        assert sorted([first.status.value, second.status.value]) == ["busy", "claimed"]
    finally:
        await pool.close()


async def test_another_customers_conversation_is_not_found(clean):
    pool, store = await store_for(clean["agent"])
    try:
        conversation = await store.create_claimed("C001")
        await store.release(conversation)
        assert (await store.claim(conversation, "C002")).status is ClaimStatus.NOT_FOUND
        assert (await store.claim(uuid.uuid4(), "C001")).status is ClaimStatus.NOT_FOUND
    finally:
        await pool.close()


async def test_a_stale_claim_can_be_taken_again(clean):
    pool, store = await store_for(clean["agent"])
    try:
        conversation = await store.create_claimed("C001")
        with psycopg.connect(clean["agent"], autocommit=True) as conn:
            conn.execute(
                "UPDATE conversations SET turn_started_at = now() - interval '61 seconds' WHERE id = %s",
                (conversation,),
            )
        assert (await store.claim(conversation, "C001")).status is ClaimStatus.CLAIMED
    finally:
        await pool.close()


async def test_a_turn_is_saved_together_and_frees_the_conversation(clean):
    pool, store = await store_for(clean["agent"])
    try:
        conversation = await store.create_claimed("C001")
        ticket = HandoffTicket(uuid.uuid4(), "wants a person", "wants a person")
        await store.save_turn(conversation, "C001", "help", "A person will reply.", ticket)
        claim = await store.claim(conversation, "C001")
        assert claim.status is ClaimStatus.CLAIMED and claim.handed_off is True
        assert await store.history(conversation, 20) == [
            ("customer", "help"),
            ("agent", "A person will reply."),
        ]
    finally:
        await pool.close()


async def test_history_keeps_the_most_recent_messages_in_order(clean):
    pool, store = await store_for(clean["agent"])
    try:
        conversation = await store.create_claimed("C001")
        for n in range(3):
            await store.save_turn(conversation, "C001", f"q{n}", f"a{n}", None)
        assert await store.history(conversation, 3) == [("agent", "a1"), ("customer", "q2"), ("agent", "a2")]
        assert await store.has_more_history_than(conversation, 3) is True
        assert await store.has_more_history_than(conversation, 6) is False
    finally:
        await pool.close()


def test_running_the_migrations_again_changes_nothing(databases):
    assert migrate.run(databases["agent"], ROOT / "migrations") == []
    assert migrate.latest_version() == "0001_init"
