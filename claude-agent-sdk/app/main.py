"""The service's web API: POST /chat, GET /health, GET /ready.

    uvicorn app.main:app --host 0.0.0.0 --port 8000

It checks who is calling, cuts a turn at 55 seconds, runs one turn per conversation at a time,
and answers readiness. The README describes the API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any, AsyncIterator

import anyio
import httpx
import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from pydantic import BaseModel, Field, ValidationError

from app import observability
from app.auth import token_matches
from app.clients.orders_api import OrdersClient
from app.config import Settings, load_settings
from app.context import TurnContext
from app.conversations import ClaimLost, ClaimStatus, ConversationStore
from app.db.migrate import latest_version
from app.db.session import make_pool
from app.runner import SYSTEM_PROMPT, AgentRunner, ModelUnavailable, TurnInput
from app.tools import build_tools
from app.tools.handoff import notify_helpdesk

logger = logging.getLogger("app")

TURN_SECONDS = 55.0
MAX_MESSAGE_CHARS = 4000
HANDED_OFF_REPLY = "A person from our team will reply here."
_DB_ERRORS = (psycopg.OperationalError, PoolTimeout)


class ChatRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    conversation_id: uuid.UUID | None = None
    message: str


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


def _error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": code, "message": message}, status_code=status, headers=headers
    )


class Service:
    """Everything a request needs, made once at start-up."""

    def __init__(
        self,
        settings: Settings,
        pool: AsyncConnectionPool,
        orders: OrdersClient,
        runner: AgentRunner,
    ) -> None:
        self.settings = settings
        self.pool = pool
        self.orders = orders
        self.runner = runner
        self.conversations = ConversationStore(pool)
        self.turn_slots = asyncio.Semaphore(settings.max_concurrent_turns)

    async def ready(self) -> dict[str, bool]:
        database = False
        try:
            async with self.pool.connection() as conn:
                row = await (
                    await conn.execute("SELECT max(version) FROM schema_migrations")
                ).fetchone()
            applied = row[0] if row else None
            # A database ahead of this build is ready too: migrations run before the next build
            # starts, and this build keeps serving until it is replaced.
            database = applied is not None and applied >= latest_version()
        except (*_DB_ERRORS, psycopg.errors.UndefinedTable):
            database = False
        orders = await self.orders.health()
        return {"database": database, "orders_service": orders}

    async def chat(self, body: ChatRequest, request_id: str) -> dict[str, Any]:
        message = body.message.strip()
        if not message:
            raise ApiError(400, "invalid_request", "message is empty")
        if len(message) > MAX_MESSAGE_CHARS:
            raise ApiError(413, "message_too_long", f"at most {MAX_MESSAGE_CHARS} characters")
        if self.turn_slots.locked():
            raise ApiError(
                429, "too_many_turns", "the service is busy; try again shortly",
                {"Retry-After": "5"},
            )
        async with self.turn_slots:
            return await self._turn(body.customer_id, body.conversation_id, message, request_id)

    async def _turn(
        self,
        customer_id: str,
        conversation_id: uuid.UUID | None,
        message: str,
        request_id: str,
    ) -> dict[str, Any]:
        try:
            if conversation_id is None:
                conversation_id, started_at = await self.conversations.create_claimed(customer_id)
                handed_off = False
            else:
                claim = await self.conversations.claim(conversation_id, customer_id)
                if claim.status is ClaimStatus.NOT_FOUND:
                    raise ApiError(404, "conversation_not_found", "no such conversation")
                if claim.status is ClaimStatus.BUSY:
                    raise ApiError(
                        409, "conversation_busy", "a message in this conversation is still being answered"
                    )
                handed_off = claim.handed_off
                started_at = claim.started_at
        except _DB_ERRORS:
            raise ApiError(503, "not_ready", "the service's database is not available") from None

        ctx = TurnContext(customer_id, conversation_id, request_id)
        try:
            if handed_off:
                reply = HANDED_OFF_REPLY
            else:
                reply = await self._run_agent(ctx, message)
            try:
                await self.conversations.save_turn(
                    conversation_id, started_at, customer_id, message, reply, ctx.ticket
                )
            except ClaimLost:
                raise ApiError(
                    409, "conversation_busy",
                    "a newer message in this conversation is being answered; this one was not saved",
                ) from None
            except _DB_ERRORS:
                raise ApiError(503, "not_ready", "the service's database is not available") from None
        except BaseException:
            # Every failure frees the conversation at once, unless a newer turn holds it.
            with contextlib.suppress(Exception):
                await self.conversations.release(conversation_id, started_at)
            raise
        if ctx.ticket is not None:
            notify_helpdesk(customer_id, conversation_id, ctx.ticket)
            observability.count("handoffs")
        return {
            "conversation_id": str(conversation_id),
            "reply": reply,
            "handed_off": handed_off or ctx.ticket is not None,
            "ticket_id": str(ctx.ticket.id) if ctx.ticket else None,
        }

    async def _run_agent(self, ctx: TurnContext, message: str) -> str:
        # The cut covers the history reads as well as the model, so slow reads cannot push the
        # turn past its claim. It is an anyio deadline rather than asyncio.timeout because the
        # SDK closes the CLI inside an anyio shield, which holds off only anyio cancellation: an
        # asyncio deadline landing while the client closes stops the close before it terminates
        # the CLI, and leaves that process running. The close is bounded by the SDK (about 20
        # seconds at worst), so a cut turn can answer that much after TURN_SECONDS.
        try:
            with anyio.fail_after(TURN_SECONDS):
                turn = await self._turn_input(ctx, message)
                with observability.span("agent turn", conversation_id=str(ctx.conversation_id)):
                    return await self.runner.run_turn(turn)
        except TimeoutError:
            raise ApiError(504, "turn_timeout", "the answer took too long; send the message again") from None
        except ModelUnavailable as exc:
            observability.log(logger, logging.WARNING, "model_unavailable", detail=str(exc))
            raise ApiError(503, "model_unavailable", "the model is not available right now") from None

    async def _turn_input(self, ctx: TurnContext, message: str) -> TurnInput:
        try:
            limit = self.settings.history_max_messages
            history = await self.conversations.history(ctx.conversation_id, limit)
            truncated = await self.conversations.has_more_history_than(
                ctx.conversation_id, limit
            )
        except _DB_ERRORS:
            raise ApiError(503, "not_ready", "the service's database is not available") from None
        return TurnInput(
            system_prompt=SYSTEM_PROMPT,
            history=history,
            history_truncated=truncated,
            message=message,
            tools=build_tools(ctx, self.orders),
        )


def create_app(
    settings: Settings | None = None,
    *,
    runner: AgentRunner | None = None,
    orders_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    observability.setup(settings.log_level, settings.secrets)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = make_pool(settings.database_url)
        await pool.open(wait=False)
        orders = OrdersClient(
            settings.orders_api_url, settings.orders_api_token, transport=orders_transport
        )
        if runner is None:
            from app.agent import ClaudeAgentRunner

            agent_runner: AgentRunner = ClaudeAgentRunner(settings)
        else:
            agent_runner = runner
        app.state.service = Service(settings, pool, orders, agent_runner)
        try:
            yield
        finally:
            await orders.aclose()
            await pool.close()

    app = FastAPI(title="Support agent", lifespan=lifespan)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = (request.headers.get("X-Request-Id") or str(uuid.uuid4()))[:64]
        token = observability.request_id_var.set(request_id)
        status: int | None = None
        took = {"seconds": 0.0}
        # In a `finally`, so every request gets its log line and count, whatever happened.
        try:
            with observability.timed() as took:
                try:
                    response = await call_next(request)
                except Exception:
                    # An error no handler answered still gets the API's JSON shape. It is caught
                    # here, inside the log line, so the line carries the status the caller got.
                    logger.exception("unhandled_error")
                    response = _error(
                        500, "internal_error", "something went wrong; the request ID is in the logs"
                    )
            status = response.status_code
            response.headers["X-Request-Id"] = request_id
            return response
        finally:
            observability.log(
                logger,
                logging.INFO,
                "request",
                method=request.method,
                path=request.url.path,
                status=status,
                seconds=took["seconds"],
            )
            observability.count("requests", path=request.url.path, status=status)
            observability.count("request_seconds", took["seconds"], path=request.url.path)
            observability.request_id_var.reset(token)

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(400, "invalid_request", "the request body is not valid")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        checks = await request.app.state.service.ready()
        return JSONResponse(checks, status_code=200 if all(checks.values()) else 503)

    async def require_token(request: Request) -> None:
        # A dependency, so a caller without the token gets 401 before the body is looked at.
        if not token_matches(request.headers.get("Authorization"), settings.agent_api_token):
            raise ApiError(401, "unauthorized", "a valid service token is required")

    @app.post("/chat", dependencies=[Depends(require_token)])
    async def chat(request: Request) -> dict[str, Any]:
        # The body is read here, after the token check, so a caller without the token learns
        # nothing about what a valid body looks like.
        try:
            body = ChatRequest.model_validate_json(await request.body())
        except ValidationError:
            raise ApiError(400, "invalid_request", "the request body is not valid") from None
        return await request.app.state.service.chat(
            body, observability.request_id_var.get()
        )

    return app


def __getattr__(name: str):
    # `uvicorn app.main:app` builds the app on first use, so importing this module (as the
    # tests do) does not need the settings.
    if name == "app":
        return create_app()
    raise AttributeError(name)
