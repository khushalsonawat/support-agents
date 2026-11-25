import pytest

from app.config import ConfigError, load_settings

BASE = {
    "AGENT_API_TOKEN": "agent-token-123456",
    "DATABASE_URL": "postgresql://agent@db/support_agent",
    "ORDERS_API_URL": "http://orders:8001/",
    "ORDERS_API_TOKEN": "orders-token-123456",
    "ANTHROPIC_API_KEY": "key-123456789",
}


def test_defaults():
    settings = load_settings(BASE)
    assert settings.anthropic_model == "claude-opus-5-5"
    assert settings.max_concurrent_turns == 4
    assert settings.history_max_messages == 20
    assert settings.orders_api_url == "http://orders:8001"


@pytest.mark.parametrize(
    "name", ["AGENT_API_TOKEN", "DATABASE_URL", "ORDERS_API_URL", "ORDERS_API_TOKEN"]
)
def test_a_required_setting_stops_the_start(name):
    env = {**BASE, name: "  "}
    with pytest.raises(ConfigError, match=name):
        load_settings(env)


def test_no_model_credentials_stops_the_start():
    env = {k: v for k, v in BASE.items() if k != "ANTHROPIC_API_KEY"}
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_settings(env)


def test_a_token_without_its_base_url_stops_the_start():
    env = {**BASE, "ANTHROPIC_AUTH_TOKEN": "gateway-token-123"}
    with pytest.raises(ConfigError, match="ANTHROPIC_BASE_URL"):
        load_settings(env)


def test_gateway_mode():
    env = {
        **{k: v for k, v in BASE.items() if k != "ANTHROPIC_API_KEY"},
        "ANTHROPIC_AUTH_TOKEN": "gateway-token-123",
        "ANTHROPIC_BASE_URL": "https://gateway.example",
    }
    settings = load_settings(env)
    assert settings.anthropic_auth_token and settings.anthropic_base_url


def test_bad_numbers_and_urls():
    with pytest.raises(ConfigError, match="MAX_CONCURRENT_TURNS"):
        load_settings({**BASE, "MAX_CONCURRENT_TURNS": "0"})
    with pytest.raises(ConfigError, match="ORDERS_API_URL"):
        load_settings({**BASE, "ORDERS_API_URL": "orders:8001"})


def test_secrets_are_not_in_the_repr():
    text = repr(load_settings(BASE))
    for secret in ("agent-token-123456", "orders-token-123456", "key-123456789", "postgresql://"):
        assert secret not in text
