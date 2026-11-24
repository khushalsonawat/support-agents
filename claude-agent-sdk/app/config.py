"""Settings, read from the environment and checked once at start-up.

Each setting is described in `.env.example`. A missing required setting stops the start with
one sentence naming it; a database or orders service that does not answer does not (the
service starts, `/ready` is false, and the tools say so).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping


class ConfigError(ValueError):
    """A setting is missing or wrong; the service must not start."""


def _text(env: Mapping[str, str], name: str) -> str:
    # A blank value counts as unset.
    return (env.get(name) or "").strip()


def _required(env: Mapping[str, str], name: str) -> str:
    value = _text(env, name)
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _text(env, name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, not {raw!r}") from None
    if value < 1:
        raise ConfigError(f"{name} must be at least 1")
    return value


@dataclass(frozen=True)
class Settings:
    agent_api_token: str = field(repr=False)
    database_url: str = field(repr=False)
    orders_api_url: str
    orders_api_token: str = field(repr=False)
    anthropic_model: str
    anthropic_api_key: str = field(default="", repr=False)
    anthropic_auth_token: str = field(default="", repr=False)
    anthropic_base_url: str = ""
    max_concurrent_turns: int = 4
    history_max_messages: int = 20
    retention_days: int = 90
    log_level: str = "INFO"

    @property
    def secrets(self) -> tuple[str, ...]:
        """Values that must never appear in a log line or an error."""
        return tuple(
            value
            for value in (
                self.agent_api_token,
                self.orders_api_token,
                self.anthropic_api_key,
                self.anthropic_auth_token,
                self.database_url,
            )
            if value
        )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    api_key = _text(env, "ANTHROPIC_API_KEY")
    auth_token = _text(env, "ANTHROPIC_AUTH_TOKEN")
    base_url = _text(env, "ANTHROPIC_BASE_URL")
    if auth_token and not base_url:
        # Without a base URL the CLI would send the token to Anthropic's own API, which is
        # the wrong host for a gateway token.
        raise ConfigError(
            "ANTHROPIC_AUTH_TOKEN is set but ANTHROPIC_BASE_URL is empty: set the endpoint "
            "the token belongs to"
        )
    if not api_key and not auth_token:
        raise ConfigError(
            "set ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN with ANTHROPIC_BASE_URL"
        )

    orders_api_url = _required(env, "ORDERS_API_URL").rstrip("/")
    if not orders_api_url.startswith(("http://", "https://")):
        raise ConfigError("ORDERS_API_URL must start with http:// or https://")

    log_level = (_text(env, "LOG_LEVEL") or "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ConfigError("LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")

    return Settings(
        agent_api_token=_required(env, "AGENT_API_TOKEN"),
        database_url=_required(env, "DATABASE_URL"),
        orders_api_url=orders_api_url,
        orders_api_token=_required(env, "ORDERS_API_TOKEN"),
        anthropic_model=_text(env, "ANTHROPIC_MODEL") or "claude-opus-5-5",
        anthropic_api_key=api_key,
        anthropic_auth_token=auth_token,
        anthropic_base_url=base_url,
        max_concurrent_turns=_positive_int(env, "MAX_CONCURRENT_TURNS", 4),
        history_max_messages=_positive_int(env, "HISTORY_MAX_MESSAGES", 20),
        retention_days=_positive_int(env, "RETENTION_DAYS", 90),
        log_level=log_level,
    )
