"""The caller's service token: the shop's backend, or its helpdesk, never a browser."""

from __future__ import annotations

import hmac


def token_matches(header_value: str | None, expected: str) -> bool:
    if not header_value or not header_value.startswith("Bearer "):
        return False
    given = header_value[len("Bearer ") :].strip()
    return hmac.compare_digest(given.encode(), expected.encode())
