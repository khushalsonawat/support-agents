"""The agent's connection pool.

A connection is checked before it is handed out, so a database restart, or anything else that
ends open connections, costs one reconnect rather than a failed turn. The pool opens without
waiting: a database that is down at start-up leaves `/ready` false instead of stopping the
service.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

STATEMENT_TIMEOUT_MS = 5000
ACQUIRE_TIMEOUT_SECONDS = 2.0


def make_pool(conninfo: str, *, max_size: int = 10) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=max_size,
        timeout=ACQUIRE_TIMEOUT_SECONDS,
        check=AsyncConnectionPool.check_connection,
        kwargs={"options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}"},
        open=False,
    )
