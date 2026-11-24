"""What one turn knows about who is talking.

The customer comes from the authenticated caller, never from the model. The tools are built
for each turn around one `TurnContext` (see `app/tools`), so two turns running at once never
see each other's customer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass
class HandoffTicket:
    id: uuid.UUID
    reason: str
    summary: str


@dataclass
class TurnContext:
    customer_id: str
    conversation_id: uuid.UUID
    request_id: str
    # Set by `hand_off_to_person` during the turn and saved with the turn's messages at the
    # end, so a turn that fails leaves no ticket behind.
    ticket: HandoffTicket | None = field(default=None)
