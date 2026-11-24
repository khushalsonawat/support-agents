"""A customer's conversations: history, the per-conversation turn claim, and saving a turn.

A turn first claims its conversation with a conditional update, so a second
message for the same conversation while one is running is refused (409) instead of answering a
history that never existed. No transaction is held open while the model runs; the turn's
messages, its ticket and the release of the claim are written together at the end. On any
failure the claim is released and nothing else is written.

A claim is identified by the time it was taken. A turn that outlives its claim may find a newer
turn holding the conversation, so saving and releasing only touch the row while it still carries
this turn's claim time.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool

from app.context import HandoffTicket

# A claim older than the turn limit belongs to a turn that died without releasing it.
STALE_CLAIM_SECONDS = 60


class ClaimLost(Exception):
    """The turn's claim went stale and a newer turn took the conversation; nothing was saved."""


class ClaimStatus(enum.Enum):
    CLAIMED = "claimed"
    NOT_FOUND = "not_found"
    BUSY = "busy"


@dataclass(frozen=True)
class Claim:
    status: ClaimStatus
    handed_off: bool = False
    # Set when claimed: the claim's own time, which saving and releasing must match.
    started_at: datetime | None = None


class ConversationStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create_claimed(self, customer_id: str) -> tuple[uuid.UUID, datetime]:
        conversation_id = uuid.uuid4()
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "INSERT INTO conversations (id, customer_id, turn_started_at)"
                    " VALUES (%s, %s, now())"
                    " RETURNING turn_started_at",
                    (conversation_id, customer_id),
                )
            ).fetchone()
        assert row is not None
        return conversation_id, row[0]

    async def claim(self, conversation_id: uuid.UUID, customer_id: str) -> Claim:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "UPDATE conversations SET turn_started_at = now()"
                    " WHERE id = %s AND customer_id = %s"
                    "   AND (turn_started_at IS NULL"
                    "        OR turn_started_at < now() - make_interval(secs => %s))"
                    " RETURNING handed_off, turn_started_at",
                    (conversation_id, customer_id, STALE_CLAIM_SECONDS),
                )
            ).fetchone()
            if row is not None:
                return Claim(ClaimStatus.CLAIMED, handed_off=row[0], started_at=row[1])
            exists = await (
                await conn.execute(
                    "SELECT 1 FROM conversations WHERE id = %s AND customer_id = %s",
                    (conversation_id, customer_id),
                )
            ).fetchone()
        # Another customer's conversation is "not found", the same as one that does not exist.
        return Claim(ClaimStatus.BUSY if exists else ClaimStatus.NOT_FOUND)

    async def history(self, conversation_id: uuid.UUID, limit: int) -> list[tuple[str, str]]:
        async with self._pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT role, content FROM ("
                    "  SELECT id, role, content FROM messages WHERE conversation_id = %s"
                    "  ORDER BY id DESC LIMIT %s"
                    ") recent ORDER BY id",
                    (conversation_id, limit),
                )
            ).fetchall()
        return [(role, content) for role, content in rows]

    async def has_more_history_than(self, conversation_id: uuid.UUID, limit: int) -> bool:
        async with self._pool.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT count(*) > %s FROM messages WHERE conversation_id = %s",
                    (limit, conversation_id),
                )
            ).fetchone()
        return bool(row and row[0])

    async def save_turn(
        self,
        conversation_id: uuid.UUID,
        started_at: datetime,
        customer_id: str,
        message: str,
        reply: str,
        ticket: HandoffTicket | None,
    ) -> None:
        """Save the turn and free the conversation, or raise ClaimLost and write nothing."""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                # The claim is checked and released first: the update locks the row, so a newer
                # turn cannot take it between this check and the inserts below.
                released = await conn.execute(
                    "UPDATE conversations"
                    " SET turn_started_at = NULL, handed_off = handed_off OR %s"
                    " WHERE id = %s AND turn_started_at = %s",
                    (ticket is not None, conversation_id, started_at),
                )
                if released.rowcount != 1:
                    raise ClaimLost()
                await conn.execute(
                    "INSERT INTO messages (conversation_id, role, content)"
                    " VALUES (%s, 'customer', %s), (%s, 'agent', %s)",
                    (conversation_id, message, conversation_id, reply),
                )
                if ticket is not None:
                    await conn.execute(
                        "INSERT INTO handoff_tickets"
                        " (id, conversation_id, customer_id, reason, summary)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        (ticket.id, conversation_id, customer_id, ticket.reason, ticket.summary),
                    )

    async def release(self, conversation_id: uuid.UUID, started_at: datetime) -> None:
        """Free the conversation, unless a newer turn has taken it since."""
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE conversations SET turn_started_at = NULL"
                " WHERE id = %s AND turn_started_at = %s",
                (conversation_id, started_at),
            )
