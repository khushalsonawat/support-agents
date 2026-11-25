"""The Claude Agent SDK agent: the only framework-specific file in this service.

Each turn starts the Claude CLI through `ClaudeSDKClient` with:
- only this service's six tools, served in-process over MCP and built for this turn's customer;
- no built-in tools (`tools=[]`), no other MCP servers (`strict_mcp_config`), no settings
  files (`setting_sources=[]`), no skills or slash commands (`--disable-slash-commands`), and
  the default permission mode;
- a fresh, empty config directory and working directory, removed after the turn;
- the service's own secrets blanked in the CLI's environment, since the SDK merges `env` over
  the service's process environment.

The SDK takes a prompt rather than a list of messages, so the stored history goes in as a
transcript.
"""

from __future__ import annotations

import shutil
import tempfile
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)

from app.config import Settings
from app.observability import redact
from app.runner import ModelUnavailable, TurnInput
from app.tools import ToolSpec

MCP_SERVER_NAME = "support"
MAX_TURNS = 8

# The service's own settings that the CLI must not inherit.
_BLANKED = (
    "AGENT_API_TOKEN",
    "DATABASE_URL",
    "ORDERS_API_TOKEN",
    "ORDERS_DATABASE_URL",
)


def allowed_tool_names(tools: list[ToolSpec]) -> list[str]:
    return [f"mcp__{MCP_SERVER_NAME}__{spec.name}" for spec in tools]


def _sdk_tool(spec: ToolSpec):
    @tool(spec.name, spec.description, dict(spec.parameters))
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        text = await spec.handler(args)
        return {"content": [{"type": "text", "text": text}]}

    return handler


def build_options(
    settings: Settings, turn: TurnInput, workdir: str
) -> ClaudeAgentOptions:
    server = create_sdk_mcp_server(
        name=MCP_SERVER_NAME,
        version="1.0.0",
        tools=[_sdk_tool(spec) for spec in turn.tools],
    )
    return ClaudeAgentOptions(
        system_prompt=turn.system_prompt,
        mcp_servers={MCP_SERVER_NAME: server},
        allowed_tools=allowed_tool_names(turn.tools),
        tools=[],
        strict_mcp_config=True,
        setting_sources=[],
        max_turns=MAX_TURNS,
        model=settings.anthropic_model,
        cwd=workdir,
        env=cli_env(settings, workdir),
        # The CLI ships its own skills and slash commands. The customer's text is the prompt, so
        # a message such as "/doctor" must reach the model as text, not run a command.
        extra_args={"disable-slash-commands": None},
    )


def cli_env(settings: Settings, workdir: str) -> dict[str, str]:
    env = {name: "" for name in _BLANKED}
    env.update(
        {
            "ANTHROPIC_MODEL": settings.anthropic_model,
            "CLAUDE_CONFIG_DIR": workdir + "/.claude",
            # The image's root filesystem is read-only; the CLI's home is the turn's folder.
            "HOME": workdir,
            "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            # The CLI's default retries on a failing provider outlast the 55-second turn, so an
            # outage would answer 504 instead of 503. Two retries fail within the turn.
            "CLAUDE_CODE_MAX_RETRIES": "2",
        }
    )
    if settings.anthropic_auth_token:
        # Gateway mode: the token is the only credential, sent to the base URL it belongs to.
        env.update(
            {
                "ANTHROPIC_AUTH_TOKEN": settings.anthropic_auth_token,
                "ANTHROPIC_BASE_URL": settings.anthropic_base_url,
                "ANTHROPIC_API_KEY": "",
            }
        )
    else:
        env.update({"ANTHROPIC_API_KEY": settings.anthropic_api_key, "ANTHROPIC_AUTH_TOKEN": ""})
    return env


def build_prompt(turn: TurnInput) -> str:
    # The prompt always starts with a label, never with the customer's own text, so the CLI
    # never reads the customer's text as a command.
    if not turn.history:
        return "Customer: " + turn.message
    lines = []
    if turn.history_truncated:
        lines.append("(Earlier messages in this conversation are not shown.)")
    for role, content in turn.history:
        lines.append(f"{'Customer' if role == 'customer' else 'Agent'}: {content}")
    return (
        "Conversation so far:\n"
        + "\n".join(lines)
        + "\n\nCustomer: "
        + turn.message
    )


class ClaudeAgentRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def run_turn(self, turn: TurnInput) -> str:
        workdir = tempfile.mkdtemp(prefix="support-turn-")
        try:
            options = build_options(self._settings, turn, workdir)
            result: ResultMessage | None = None
            last_text = ""
            try:
                # A client per turn rather than query(): tracing instrumentors wrap
                # ClaudeSDKClient, so a turn made through query() would make no spans.
                async with ClaudeSDKClient(options=options) as client:
                    await client.query(build_prompt(turn))
                    async for message in client.receive_response():
                        if isinstance(message, AssistantMessage):
                            text = "".join(
                                block.text
                                for block in message.content
                                if isinstance(block, TextBlock)
                            )
                            if text:
                                last_text = text
                        elif isinstance(message, ResultMessage):
                            result = message
            except ClaudeSDKError as exc:
                raise ModelUnavailable(redact(str(exc))[:500]) from None
            if result is None:
                raise ModelUnavailable("the model turn ended without a result")
            if result.is_error:
                # On a provider error the CLI reports is_error with the error text as the result.
                detail = redact(result.result or result.subtype)[:300]
                raise ModelUnavailable(f"the model turn failed: {detail}")
            reply = (result.result or last_text).strip()
            if not reply:
                raise ModelUnavailable("the model returned no reply")
            return reply
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
