"""Shared test fixtures.

Database tests need PostgreSQL. With TEST_DATABASE_URL set (a server URL whose login may
create databases), they use it; otherwise they start a local one with `pgserver`. If neither
is available they are skipped, never faked.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import psycopg
import pytest

from app.config import Settings
from app.db import migrate
from app.runner import TurnInput
from orders_service import seed as orders_seed

ROOT = Path(__file__).resolve().parents[1]
AGENT_TOKEN = "test-agent-token-123456"
ORDERS_TOKEN = "test-orders-token-123456"


def _with_database(uri: str, name: str) -> str:
    conninfo = psycopg.conninfo.conninfo_to_dict(uri)
    conninfo["dbname"] = name
    return psycopg.conninfo.make_conninfo(**conninfo)


@pytest.fixture(scope="session")
def pg_server_uri() -> Any:
    uri = os.environ.get("TEST_DATABASE_URL", "").strip()
    if uri:
        yield uri
        return
    try:
        import pgserver
    except ImportError:
        pytest.skip("no TEST_DATABASE_URL and pgserver is not installed")
    server = pgserver.get_server(tempfile.mkdtemp(prefix="support-agent-pg-"), cleanup_mode="stop")
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


@pytest.fixture(scope="session")
def databases(pg_server_uri: str) -> dict[str, str]:
    names = {"agent": "support_agent_test", "orders": "orders_test"}
    with psycopg.connect(pg_server_uri, autocommit=True) as conn:
        for name in names.values():
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{name}"')
    uris = {key: _with_database(pg_server_uri, name) for key, name in names.items()}
    migrate.run(uris["agent"], ROOT / "migrations")
    migrate.run(uris["orders"], ROOT / "orders_service" / "migrations")
    orders_seed.seed(uris["orders"])
    return uris


@pytest.fixture
def clean(databases: dict[str, str]) -> dict[str, str]:
    with psycopg.connect(databases["agent"], autocommit=True) as conn:
        conn.execute("TRUNCATE conversations CASCADE")
    with psycopg.connect(databases["orders"], autocommit=True) as conn:
        conn.execute("TRUNCATE returns, idempotency_keys")
    return databases


def make_settings(database_url: str = "postgresql://unused/db", **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "agent_api_token": AGENT_TOKEN,
        "database_url": database_url,
        "orders_api_url": "http://orders.test",
        "orders_api_token": ORDERS_TOKEN,
        "anthropic_model": "claude-opus-5-5",
        "anthropic_api_key": "test-anthropic-key-123456",
    }
    values.update(overrides)
    return Settings(**values)


class FakeRunner:
    """Runs a turn by calling chosen tools, then returning set text.

    `script` is a list of (tool name, arguments) calls; `reply` is the text to return, given
    the tool results. Every turn it ran is kept in `turns`.
    """

    def __init__(self, script=None, reply=None, error: Exception | None = None, delay: float = 0):
        self.script = script or []
        self.reply = reply or (lambda results: results[-1] if results else "Hello.")
        self.error = error
        self.delay = delay
        self.turns: list[TurnInput] = []

    async def run_turn(self, turn: TurnInput) -> str:
        import asyncio

        self.turns.append(turn)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        tools = {spec.name: spec for spec in turn.tools}
        results = [await tools[name].handler(args) for name, args in self.script]
        return self.reply(results)


@contextlib.asynccontextmanager
async def running(app) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI app with its start-up and shut-down run, and a client that calls it."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@contextlib.asynccontextmanager
async def agent_service(databases: dict[str, str], runner: FakeRunner, **settings: Any):
    """The agent app wired to the real orders app in-process, both on the test databases."""
    from app.main import create_app
    from orders_service.app import create_app as create_orders_app

    orders_app = create_orders_app(databases["orders"], ORDERS_TOKEN)
    async with orders_app.router.lifespan_context(orders_app):
        app = create_app(
            make_settings(databases["agent"], **settings),
            runner=runner,
            orders_transport=httpx.ASGITransport(app=orders_app),
        )
        async with running(app) as client:
            client.headers["Authorization"] = f"Bearer {AGENT_TOKEN}"
            yield client
