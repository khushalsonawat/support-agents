"""Remove conversations past the retention period, or one customer's on request.

    python -m app.retention expire                    # older than RETENTION_DAYS (default 90)
    python -m app.retention delete --customer C001    # a customer's own request

Messages and handoff tickets go with their conversation.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg

from app.config import ConfigError, _positive_int


def expire(conninfo: str, days: int) -> int:
    with psycopg.connect(conninfo) as conn:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE created_at < now() - make_interval(days => %s)",
            (days,),
        )
        return cursor.rowcount


def delete_customer(conninfo: str, customer_id: str) -> int:
    with psycopg.connect(conninfo) as conn:
        cursor = conn.execute("DELETE FROM conversations WHERE customer_id = %s", (customer_id,))
        return cursor.rowcount


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("expire")
    delete = commands.add_parser("delete")
    delete.add_argument("--customer", required=True)
    args = parser.parse_args(argv)
    conninfo = os.environ.get("DATABASE_URL", "").strip()
    if not conninfo:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    if args.command == "expire":
        # The same check the service makes at start: a period of zero or less would put the
        # cut-off at now or later and remove every conversation.
        try:
            days = _positive_int(os.environ, "RETENTION_DAYS", 90)
        except ConfigError as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"removed {expire(conninfo, days)} conversation(s) older than {days} days")
    else:
        print(f"removed {delete_customer(conninfo, args.customer)} conversation(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
