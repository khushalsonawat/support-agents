"""The agent through the real Claude CLI and a real model. Opt-in: every run is paid.

Skipped unless both are set:

    SUPPORT_AGENT_REAL_MODEL_TESTS=1 ANTHROPIC_API_KEY=... \\
        uv run --frozen --group dev pytest -q tests/test_real_model.py

ANTHROPIC_MODEL picks the model, as it does for the service. The other tests never start the CLI;
these check what only the real one can show: the tools it offers, one conversation, and that a
turn cut at the deadline leaves no CLI process behind.
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest
from claude_agent_sdk import ClaudeSDKClient, SystemMessage

from app import main
from app.agent import allowed_tool_names, build_options, build_prompt
from app.context import TurnContext
from app.runner import SYSTEM_PROMPT, TurnInput
from app.tools import build_tools
from tests.conftest import agent_service, make_settings

pytestmark = pytest.mark.skipif(
    os.environ.get("SUPPORT_AGENT_REAL_MODEL_TESTS") != "1"
    or not os.environ.get("ANTHROPIC_API_KEY", "").strip(),
    reason="paid: set SUPPORT_AGENT_REAL_MODEL_TESTS=1 and ANTHROPIC_API_KEY to run",
)


def real_settings() -> dict[str, str]:
    settings = {"anthropic_api_key": os.environ["ANTHROPIC_API_KEY"].strip()}
    if os.environ.get("ANTHROPIC_MODEL", "").strip():
        settings["anthropic_model"] = os.environ["ANTHROPIC_MODEL"].strip()
    return settings


def child_processes() -> set[str]:
    # Includes a child that exited but was never waited for, which is what a leaked CLI
    # looks like once its parent stops tracking it.
    found = subprocess.run(["pgrep", "-P", str(os.getpid())], capture_output=True, text=True)
    return set(found.stdout.split())


async def test_the_real_cli_offers_only_the_six_tools(tmp_path):
    ctx = TurnContext("C001", uuid.uuid4(), "real-model-tools")
    turn = TurnInput(SYSTEM_PROMPT, [], False, "Say hello.", build_tools(ctx, orders=None))
    options = build_options(make_settings(**real_settings()), turn, str(tmp_path))
    offered = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(build_prompt(turn))
        async for message in client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == "init":
                offered = message.data.get("tools")
    assert offered is not None, "the CLI sent no init message"
    assert sorted(offered) == sorted(allowed_tool_names(turn.tools))


async def test_one_conversation_with_the_real_model(clean):
    # No runner given: the service builds its real Claude runner from the settings.
    async with agent_service(clean, None, **real_settings()) as client:
        first = await client.post(
            "/chat", json={"customer_id": "C001", "message": "Where is my order A1001?"}
        )
        assert first.status_code == 200, first.text
        assert "deliver" in first.json()["reply"].lower()
        second = await client.post(
            "/chat",
            json={
                "customer_id": "C001",
                "conversation_id": first.json()["conversation_id"],
                "message": "And what was in that order?",
            },
        )
        assert second.status_code == 200, second.text
        assert "shoe" in second.json()["reply"].lower()


async def test_a_turn_cut_at_the_deadline_leaves_no_cli_running(clean, monkeypatch):
    # Short enough that the CLI is still starting or the model still answering when it fires.
    monkeypatch.setattr(main, "TURN_SECONDS", 3.0)
    before = child_processes()
    async with agent_service(clean, None, **real_settings()) as client:
        cut = await client.post(
            "/chat",
            json={
                "customer_id": "C001",
                "message": "List all my recent orders, check the return of each one, and quote"
                " the return policy in full.",
            },
        )
        assert cut.status_code == 504 and cut.json()["error"] == "turn_timeout"
        # The answer comes only after the SDK has closed the CLI, so nothing new is left.
        assert child_processes() <= before
