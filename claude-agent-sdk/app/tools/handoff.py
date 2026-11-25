from __future__ import annotations

import logging
import uuid

from app.context import HandoffTicket, TurnContext

logger = logging.getLogger(__name__)


async def hand_off_to_person(ctx: TurnContext, reason: str) -> str:
    """Hand the customer to a person on the support team, with the reason, when you cannot help."""
    # Nothing is written here: the ticket is saved with the turn's messages when the turn ends,
    # so a turn that fails leaves no ticket behind.
    if ctx.ticket is None:
        ctx.ticket = HandoffTicket(
            id=uuid.uuid4(),
            reason=(reason or "not given")[:500],
            summary=(reason or "")[:500],
        )
    return (
        f"Handoff ticket {ctx.ticket.id} is open. Tell the customer a person from our team "
        "will reply in this conversation."
    )


def notify_helpdesk(customer_id: str, conversation_id: uuid.UUID, ticket: HandoffTicket) -> None:
    """Where a real deployment sends the ticket to its helpdesk. Here it only logs."""
    logger.info(
        "handoff_ticket_opened",
        extra={
            "fields": {
                "ticket_id": str(ticket.id),
                "conversation_id": str(conversation_id),
                "customer_id": customer_id,
            }
        },
    )
