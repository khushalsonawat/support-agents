"""HTTP client for the shop's orders service.

Every call names the customer (`X-Customer-Id`) and carries the agent's own service token. The
orders service answers only about that customer's orders.

Time limits: connect 2 s, read 5 s; at most 2 retries, after 0.2 s and 0.8 s,
on a connection error, a timeout or 502/503/504, never on a 4xx. A call stops retrying when
less than 6 s of its 12 s budget is left. Only GETs and POSTs that carry an Idempotency-Key are
retried, so a retry can never start a second return.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

CONNECT_TIMEOUT = 2.0
READ_TIMEOUT = 5.0
RETRY_DELAYS = (0.2, 0.8)
CALL_BUDGET = 12.0
MIN_TIME_FOR_RETRY = 6.0
_RETRY_STATUSES = frozenset({502, 503, 504})


class OrdersError(Exception):
    """A call to the orders service did not give an answer the tools can use."""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind  # unavailable | not_found | not_allowed | rejected
        self.message = message


@dataclass(frozen=True)
class Order:
    id: str
    item: str
    status: str
    placed_at: str


@dataclass(frozen=True)
class Return:
    id: str
    order_id: str
    status: str
    reason: str


def return_idempotency_key(conversation_id: uuid.UUID, order_id: str) -> str:
    """The same conversation asking twice to return the same order is one request."""
    return str(uuid.uuid5(conversation_id, f"return:{order_id}"))


class OrdersClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            transport=transport,
        )
        self._sleep = sleep
        self._clock = clock

    async def aclose(self) -> None:
        await self._http.aclose()

    async def health(self) -> bool:
        try:
            response = await self._http.get("/health")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def _call(
        self,
        method: str,
        path: str,
        customer_id: str,
        *,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers = {"X-Customer-Id": customer_id}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        retryable = method == "GET" or idempotency_key is not None
        started = self._clock()
        attempt = 0
        while True:
            try:
                response = await self._http.request(
                    method, path, headers=headers, json=json, params=params
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                failure: str = type(exc).__name__
                response = None
            else:
                if response.status_code not in _RETRY_STATUSES:
                    return response
                failure = f"HTTP {response.status_code}"
            left = CALL_BUDGET - (self._clock() - started)
            if not retryable or attempt >= len(RETRY_DELAYS) or left < MIN_TIME_FOR_RETRY:
                raise OrdersError("unavailable", failure)
            await self._sleep(RETRY_DELAYS[attempt])
            attempt += 1

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            body = response.json()
        except ValueError:
            body = {}
        message = str(body.get("message") or "")
        if response.status_code == 404:
            raise OrdersError("not_found", message)
        if response.status_code == 409:
            raise OrdersError("not_allowed", message)
        if response.status_code in (401, 403):
            # The agent's own credentials are wrong: an operator problem, not the customer's.
            raise OrdersError("unavailable", "the orders service refused the agent's token")
        raise OrdersError("rejected", message or f"HTTP {response.status_code}")

    async def list_orders(self, customer_id: str, limit: int = 5) -> list[Order]:
        response = await self._call(
            "GET", "/orders", customer_id, params={"limit": limit}
        )
        self._raise_for(response)
        return [Order(**_order_fields(item)) for item in response.json()["orders"]]

    async def get_order(self, customer_id: str, order_id: str) -> Order:
        response = await self._call("GET", f"/orders/{_segment(order_id)}", customer_id)
        self._raise_for(response)
        return Order(**_order_fields(response.json()))

    async def start_return(
        self, customer_id: str, order_id: str, reason: str, idempotency_key: str
    ) -> Return:
        response = await self._call(
            "POST",
            f"/orders/{_segment(order_id)}/returns",
            customer_id,
            json={"reason": reason},
            idempotency_key=idempotency_key,
        )
        self._raise_for(response)
        return Return(**_return_fields(response.json()))

    async def get_return(self, customer_id: str, order_id: str) -> Return:
        response = await self._call(
            "GET", f"/orders/{_segment(order_id)}/return", customer_id
        )
        self._raise_for(response)
        return Return(**_return_fields(response.json()))

    async def get_policy(self, customer_id: str) -> str:
        response = await self._call("GET", "/policy", customer_id)
        self._raise_for(response)
        return str(response.json()["text"])


def _segment(order_id: str) -> str:
    # The order id comes from the model. Keep it to one path segment.
    cleaned = order_id.strip()
    if not cleaned or len(cleaned) > 64 or not all(c.isalnum() or c in "-_" for c in cleaned):
        raise OrdersError("not_found", "")
    return cleaned


def _order_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "item": item["item"],
        "status": item["status"],
        "placed_at": item["placed_at"],
    }


def _return_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "order_id": item["order_id"],
        "status": item["status"],
        "reason": item["reason"],
    }
