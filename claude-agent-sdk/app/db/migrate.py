"""Apply versioned SQL files in order, once each.

    python -m app.db.migrate                        # the agent's tables, DATABASE_URL
    python -m app.db.migrate --dir orders_service/migrations --url-env ORDERS_DATABASE_URL

It runs as a one-off step before the service starts (in compose, the `migrate` service), never
from the service itself. An advisory lock makes two runs at once safe: the second waits, then
finds nothing left to do. Each file runs in its own transaction with its record in
`schema_migrations`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

# Any fixed number; both databases use their own `schema_migrations`, so one lock id is enough.
LOCK_ID = 7_201_123

APP_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DIR = APP_DIR / "migrations"


def migration_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.sql") if p.name[:4].isdigit())


def latest_version(directory: Path = DEFAULT_DIR) -> str:
    files = migration_files(directory)
    return files[-1].stem if files else ""


def run(conninfo: str, directory: Path) -> list[str]:
    applied: list[str] = []
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {
                row[0]
                for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for path in migration_files(directory):
                if path.stem in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text())
                    conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,)
                    )
                applied.append(path.stem)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--url-env", default="DATABASE_URL")
    args = parser.parse_args(argv)
    conninfo = os.environ.get(args.url_env, "").strip()
    if not conninfo:
        print(f"{args.url_env} is required", file=sys.stderr)
        return 2
    applied = run(conninfo, args.dir)
    print(f"applied {len(applied)} migration(s): {', '.join(applied) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
