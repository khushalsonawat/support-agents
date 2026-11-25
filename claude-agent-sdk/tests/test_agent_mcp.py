"""The six tools as the Claude CLI sees them: through the SDK's in-process MCP server.

This drives the same JSON-RPC messages the CLI sends (initialize, tools/list, tools/call) with
no model and no CLI. It uses the SDK's internal bridge, so a change of SDK version may need
this test updated; that is the point of pinning the SDK.
"""

import uuid

from claude_agent_sdk._internal.sdk_mcp_bridge import SdkMcpBridge

from app.agent import MCP_SERVER_NAME, build_options
from app.context import TurnContext
from app.runner import TurnInput
from app.tools import TOOL_NAMES, build_tools
from tests.conftest import make_settings
from tests.test_tools import RecordingOrders

EXPECTED_ARGUMENTS = {
    "look_up_order": ["order_id"],
    "list_recent_orders": [],
    "start_return": ["order_id", "reason"],
    "return_status": ["order_id"],
    "return_policy": [],
    "hand_off_to_person": ["reason"],
}


async def bridge_for(customer, orders, tmp_path):
    ctx = TurnContext(customer, uuid.uuid4(), "req")
    turn = TurnInput("system", [], False, "hi", build_tools(ctx, orders))
    server = build_options(make_settings(), turn, str(tmp_path)).mcp_servers[MCP_SERVER_NAME]
    bridge = SdkMcpBridge(MCP_SERVER_NAME, server["instance"])
    reply = await bridge.handle(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
    )
    assert "result" in reply, reply
    await bridge.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return ctx, bridge


async def test_the_cli_is_offered_the_six_tools_with_their_arguments(tmp_path):
    _, bridge = await bridge_for("C001", RecordingOrders(), tmp_path)
    try:
        reply = await bridge.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {t["name"]: t for t in reply["result"]["tools"]}
        assert sorted(tools) == sorted(TOOL_NAMES)
        for name, arguments in EXPECTED_ARGUMENTS.items():
            schema = tools[name]["inputSchema"]
            assert sorted(schema["properties"]) == sorted(arguments), name
            assert sorted(schema.get("required", [])) == sorted(arguments), name
            assert "customer_id" not in schema["properties"]
            assert tools[name]["description"].strip(), name
    finally:
        await bridge.aclose()


async def test_a_tool_call_from_the_cli_acts_for_the_turns_customer(tmp_path):
    orders = RecordingOrders()
    _, bridge = await bridge_for("C002", orders, tmp_path)
    try:
        reply = await bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "look_up_order",
                    "arguments": {"order_id": "A1002", "customer_id": "C001"},
                },
            }
        )
        content = reply["result"]["content"]
        assert content[0]["type"] == "text"
        assert "A1002" in content[0]["text"]
        assert orders.calls == [("get_order", "C002", "A1002")]
    finally:
        await bridge.aclose()
