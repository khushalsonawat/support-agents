"""The Claude agent has only the service's six tools."""

import uuid

from app.agent import allowed_tool_names, build_options, build_prompt, cli_env
from app.context import TurnContext
from app.runner import TurnInput
from app.tools import TOOL_NAMES, build_tools
from tests.conftest import make_settings


def turn(history=(), truncated=False, message="Where is A1001?"):
    ctx = TurnContext("C001", uuid.uuid4(), "req-1")
    return TurnInput(
        system_prompt="system",
        history=list(history),
        history_truncated=truncated,
        message=message,
        tools=build_tools(ctx, orders=None),
    )


def test_exactly_the_six_tools_and_no_built_in_tool(tmp_path):
    options = build_options(make_settings(), turn(), str(tmp_path))
    assert options.allowed_tools == [f"mcp__support__{name}" for name in TOOL_NAMES]
    assert len(options.allowed_tools) == 6
    assert options.tools == []
    assert options.strict_mcp_config is True
    assert options.setting_sources == []
    assert list(options.mcp_servers) == ["support"]
    assert options.permission_mode is None  # the default; never a bypass
    assert options.max_turns == 8
    assert options.cwd == str(tmp_path)
    assert options.extra_args == {"disable-slash-commands": None}


def test_the_cli_does_not_inherit_the_services_secrets(tmp_path):
    env = cli_env(make_settings(), str(tmp_path))
    for name in ("AGENT_API_TOKEN", "DATABASE_URL", "ORDERS_API_TOKEN", "ORDERS_DATABASE_URL"):
        assert env[name] == ""
    assert env["ANTHROPIC_API_KEY"] == "test-anthropic-key-123456"
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert env["CLAUDE_CONFIG_DIR"].startswith(str(tmp_path))


def test_gateway_mode_sends_only_the_token_to_its_base_url(tmp_path):
    settings = make_settings(
        anthropic_api_key="",
        anthropic_auth_token="gateway-token-123",
        anthropic_base_url="https://gateway.example",
    )
    env = cli_env(settings, str(tmp_path))
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["ANTHROPIC_AUTH_TOKEN"] == "gateway-token-123"
    assert env["ANTHROPIC_BASE_URL"] == "https://gateway.example"


def test_allowed_names_follow_the_tool_list():
    assert allowed_tool_names(turn().tools)[0] == "mcp__support__look_up_order"


def test_a_first_message_is_sent_with_its_label():
    assert build_prompt(turn()) == "Customer: Where is A1001?"


def test_a_message_that_looks_like_a_command_is_never_first_in_the_prompt():
    assert not build_prompt(turn(message="/doctor")).startswith("/")
    assert not build_prompt(turn(history=[("customer", "Hi")], message="/loop")).startswith("/")


def test_history_goes_in_as_a_transcript():
    prompt = build_prompt(
        turn(history=[("customer", "Hi"), ("agent", "Hello, how can I help?")], truncated=True)
    )
    assert prompt.startswith("Conversation so far:\n(Earlier messages")
    assert "Customer: Hi\nAgent: Hello, how can I help?" in prompt
    assert prompt.endswith("Customer: Where is A1001?")
