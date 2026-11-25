"""The one interface the framework-specific file implements: run one turn.

`app/agent.py` implements it with the Claude Agent SDK. Tests swap in a fake that calls chosen
tools and returns set text, so no test depends on the CLI's wire format.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.tools import ToolSpec

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text().strip()


class ModelUnavailable(Exception):
    """The model provider failed this turn; the caller answers 503."""


@dataclass(frozen=True)
class TurnInput:
    system_prompt: str
    # Oldest first; role is "customer" or "agent".
    history: list[tuple[str, str]]
    # True when older messages were left out of `history`.
    history_truncated: bool
    message: str
    tools: list[ToolSpec]


class AgentRunner(Protocol):
    async def run_turn(self, turn: TurnInput) -> str: ...
