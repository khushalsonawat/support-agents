"""Sample customers and orders, for local runs and tests only. A real shop has no seed.

    python -m orders_service.seed      # uses ORDERS_DATABASE_URL

The three orders A1001-A1003 keep the items and statuses today's one-file agents use, so
earlier scenarios still read the same. Running it twice changes nothing.
"""

from __future__ import annotations

import os
import sys

import psycopg

CUSTOMERS = [("C001", "Alex Morgan"), ("C002", "Sam Patel"), ("C003", "Jordan Lee")]

ORDERS = [
    ("A1001", "C001", "trail running shoes", "delivered", "2026-11-02T10:00:00Z"),
    ("A1002", "C002", "rain jacket", "shipped", "2026-11-18T09:30:00Z"),
    ("A1003", "C001", "water bottle", "delivered", "2026-11-05T15:20:00Z"),
    ("A1004", "C001", "hiking socks", "processing", "2026-11-21T08:00:00Z"),
    ("A1005", "C002", "trekking poles", "delivered", "2026-10-12T12:00:00Z"),
    ("A1006", "C003", "camping stove", "cancelled", "2026-11-10T17:45:00Z"),
]

POLICY = (
    "Delivered items can be returned within 30 days of delivery for a full refund. "
    "Items must be unused and in their original packaging. Refunds go back to the original "
    "payment method within 5 business days of the return being received."
)


def seed(conninfo: str) -> None:
    with psycopg.connect(conninfo) as conn:
        conn.cursor().executemany(
            "INSERT INTO customers (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            CUSTOMERS,
        )
        conn.cursor().executemany(
            "INSERT INTO orders (id, customer_id, item, status, placed_at)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            ORDERS,
        )
        conn.execute(
            "INSERT INTO policy (id, text) VALUES (1, %s) ON CONFLICT DO NOTHING", (POLICY,)
        )


def main() -> int:
    conninfo = os.environ.get("ORDERS_DATABASE_URL", "").strip()
    if not conninfo:
        print("ORDERS_DATABASE_URL is required", file=sys.stderr)
        return 2
    seed(conninfo)
    print(f"seeded {len(CUSTOMERS)} customers and {len(ORDERS)} orders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
