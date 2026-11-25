"""The agent's six tools, built for one turn.

`build_tools` closes every tool over the turn's `TurnContext`, so the customer a tool acts for
is the authenticated caller's, never one the model names. None of the tools takes a customer
id, and none raises: a failure is one plain sentence back to the model.

The tool functions are plain module-level coroutines (`look_up_order` and so on) so a tracer
can find them by name; `build_tools` binds them to the turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial
from typing import Any, Awaitable, Callable

from app import observability
from app.clients.orders_api import OrdersClient, OrdersError
from app.context import TurnContext
from app.tools.handoff import hand_off_to_person
from app.tools.orders import list_recent_orders, look_up_order
from app.tools.policy import return_policy
from app.tools.returns import return_status, start_return

logger = logging.getLogger(__name__)

UNAVAILABLE = "The orders system is not answering right now. Please try again in a moment."


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    # Argument name -> Python type. Every argument is required.
    parameters: dict[str, type]
    handler: Callable[[dict[str, Any]], Awaitable[str]]


TOOL_NAMES = (
    "look_up_order",
    "list_recent_orders",
    "start_return",
    "return_status",
    "return_policy",
    "hand_off_to_person",
)


def _guarded(
    name: str,
    ctx: TurnContext,
    function: Callable[..., Awaitable[str]],
    parameters: dict[str, type],
) -> Callable[[dict[str, Any]], Awaitable[str]]:
    async def handler(args: dict[str, Any]) -> str:
        values = {key: str(args.get(key, "")).strip() for key in parameters}
        outcome = "ok"
        with observability.timed() as took, observability.span(
            f"tool {name}", tool=name, conversation_id=str(ctx.conversation_id)
        ):
            try:
                result = await function(**values)
            except OrdersError as exc:
                outcome = f"orders_{exc.kind}"
                result = UNAVAILABLE
            except Exception:  # noqa: BLE001 - a tool never fails the turn
                outcome = "error"
                logger.exception("tool_failed", extra={"fields": {"tool": name}})
                result = "Something went wrong on our side with that request."
        if outcome != "ok":
            observability.count("tool_errors", tool=name)
        observability.log(
            logger,
            logging.INFO,
            "tool_call",
            tool=name,
            outcome=outcome,
            seconds=took["seconds"],
            conversation_id=str(ctx.conversation_id),
            customer_id=ctx.customer_id,
        )
        observability.log(logger, logging.DEBUG, "tool_call_detail", tool=name, args=values)
        return result

    return handler


def build_tools(ctx: TurnContext, orders: OrdersClient) -> list[ToolSpec]:
    def spec(name: str, function: Callable[..., Awaitable[str]], parameters: dict[str, type]):
        return ToolSpec(
            name=name,
            # A partial's own __doc__ is partial's; take the tool function's.
            description=(getattr(function, "func", function).__doc__ or "")
            .strip()
            .splitlines()[0],
            parameters=parameters,
            handler=_guarded(name, ctx, function, parameters),
        )

    return [
        spec("look_up_order", partial(look_up_order, ctx, orders), {"order_id": str}),
        spec("list_recent_orders", partial(list_recent_orders, ctx, orders), {}),
        spec(
            "start_return",
            partial(start_return, ctx, orders),
            {"order_id": str, "reason": str},
        ),
        spec("return_status", partial(return_status, ctx, orders), {"order_id": str}),
        spec("return_policy", partial(return_policy, ctx, orders), {}),
        spec("hand_off_to_person", partial(hand_off_to_person, ctx), {"reason": str}),
    ]
