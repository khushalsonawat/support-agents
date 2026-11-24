"""A small stand-in for the shop's own orders system.

    uvicorn orders_service.app:app --host 0.0.0.0 --port 8001

Every call carries the agent's service token and `X-Customer-Id`. An order of another customer
is answered exactly like an order that does not exist, so nothing leaks. Starting a return
honours `Idempotency-Key` and allows one open return per order.
"""

from __future__ import annotations

import contextlib
import hmac
import os
import uuid
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from app.db.session import make_pool


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message or code


class ReturnRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def _order(row: tuple) -> dict[str, Any]:
    order_id, item, status, placed_at = row
    return {"id": order_id, "item": item, "status": status, "placed_at": placed_at.isoformat()}


def _return(row: tuple) -> dict[str, Any]:
    return_id, order_id, reason, status = row
    return {"id": str(return_id), "order_id": order_id, "reason": reason, "status": status}


def create_app(conninfo: str | None = None, token: str | None = None) -> FastAPI:
    conninfo = conninfo or os.environ["ORDERS_DATABASE_URL"]
    token = token or os.environ["ORDERS_API_TOKEN"]

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = make_pool(conninfo)
        await pool.open(wait=False)
        app.state.pool = pool
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="Orders service (stand-in)", lifespan=lifespan)

    async def customer(
        authorization: str | None = Header(default=None),
        x_customer_id: str | None = Header(default=None),
    ) -> str:
        given = (authorization or "").removeprefix("Bearer ").strip()
        if not given or not hmac.compare_digest(given.encode(), token.encode()):
            raise ServiceError(401, "unauthorized")
        if not x_customer_id:
            raise ServiceError(400, "customer_required", "X-Customer-Id is required")
        return x_customer_id

    def pool(request: Request) -> AsyncConnectionPool:
        return request.app.state.pool

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": "invalid_request", "message": "invalid request"}, status_code=400)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/orders")
    async def list_orders(
        request: Request, limit: int = 5, customer_id: str = Depends(customer)
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 20))
        async with pool(request).connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT id, item, status, placed_at FROM orders WHERE customer_id = %s"
                    " ORDER BY placed_at DESC LIMIT %s",
                    (customer_id, limit),
                )
            ).fetchall()
        return {"orders": [_order(row) for row in rows]}

    @app.get("/orders/{order_id}")
    async def get_order(
        request: Request, order_id: str, customer_id: str = Depends(customer)
    ) -> dict[str, Any]:
        async with pool(request).connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT id, item, status, placed_at FROM orders"
                    " WHERE id = %s AND customer_id = %s",
                    (order_id, customer_id),
                )
            ).fetchone()
        if row is None:
            raise ServiceError(404, "order_not_found")
        return _order(row)

    @app.post("/orders/{order_id}/returns", status_code=201)
    async def start_return(
        request: Request,
        order_id: str,
        body: ReturnRequest,
        customer_id: str = Depends(customer),
        idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        async with pool(request).connection() as conn:
            async with conn.transaction():
                # Locking the order serialises every return request for it, so the key check
                # and the one-open-return check below cannot race.
                order = await (
                    await conn.execute(
                        "SELECT status FROM orders WHERE id = %s AND customer_id = %s FOR UPDATE",
                        (order_id, customer_id),
                    )
                ).fetchone()
                if order is None:
                    raise ServiceError(404, "order_not_found")
                if idempotency_key:
                    stored = await (
                        await conn.execute(
                            "SELECT status_code, response FROM idempotency_keys"
                            " WHERE customer_id = %s AND key = %s",
                            (customer_id, idempotency_key),
                        )
                    ).fetchone()
                    if stored is not None:
                        return JSONResponse(stored[1], status_code=stored[0])
                if order[0] != "delivered":
                    status, payload = 409, {
                        "error": "return_not_allowed",
                        "message": f"the order is {order[0]}, not delivered yet",
                    }
                else:
                    existing = await (
                        await conn.execute(
                            "SELECT 1 FROM returns WHERE order_id = %s"
                            " AND status IN ('requested', 'approved')",
                            (order_id,),
                        )
                    ).fetchone()
                    if existing is not None:
                        status, payload = 409, {
                            "error": "return_not_allowed",
                            "message": "the order already has a return",
                        }
                    else:
                        row = await (
                            await conn.execute(
                                "INSERT INTO returns (id, order_id, reason, status)"
                                " VALUES (%s, %s, %s, 'requested')"
                                " RETURNING id, order_id, reason, status",
                                (uuid.uuid4(), order_id, body.reason),
                            )
                        ).fetchone()
                        status, payload = 201, _return(row)
                if idempotency_key:
                    await conn.execute(
                        "INSERT INTO idempotency_keys (customer_id, key, status_code, response)"
                        " VALUES (%s, %s, %s, %s)",
                        (customer_id, idempotency_key, status, Jsonb(payload)),
                    )
        return JSONResponse(payload, status_code=status)

    @app.get("/orders/{order_id}/return")
    async def get_return(
        request: Request, order_id: str, customer_id: str = Depends(customer)
    ) -> dict[str, Any]:
        async with pool(request).connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT r.id, r.order_id, r.reason, r.status FROM returns r"
                    " JOIN orders o ON o.id = r.order_id"
                    " WHERE r.order_id = %s AND o.customer_id = %s"
                    " ORDER BY r.created_at DESC LIMIT 1",
                    (order_id, customer_id),
                )
            ).fetchone()
        if row is None:
            raise ServiceError(404, "return_not_found")
        return _return(row)

    @app.get("/policy")
    async def get_policy(request: Request, customer_id: str = Depends(customer)) -> dict[str, str]:
        async with pool(request).connection() as conn:
            row = await (await conn.execute("SELECT text FROM policy WHERE id = 1")).fetchone()
        if row is None:
            raise ServiceError(404, "policy_not_found")
        return {"text": row[0]}

    return app


def __getattr__(name: str):
    if name == "app":
        return create_app()
    raise AttributeError(name)
