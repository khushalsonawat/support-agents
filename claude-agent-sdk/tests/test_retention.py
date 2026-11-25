import psycopg
import pytest

from app import retention


def test_expire_and_delete_by_customer(clean):
    uri = clean["agent"]
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO conversations (id, customer_id, created_at) VALUES"
            " (gen_random_uuid(), 'C001', now() - interval '100 days'),"
            " (gen_random_uuid(), 'C001', now()),"
            " (gen_random_uuid(), 'C002', now())"
        )
    assert retention.expire(uri, 90) == 1
    assert retention.delete_customer(uri, "C002") == 1
    with psycopg.connect(uri) as conn:
        assert conn.execute("SELECT customer_id FROM conversations").fetchall() == [("C001",)]


@pytest.mark.parametrize("days", ["0", "-5", "ninety"])
def test_a_retention_period_below_one_day_is_refused(clean, monkeypatch, capsys, days):
    uri = clean["agent"]
    with psycopg.connect(uri, autocommit=True) as conn:
        conn.execute("INSERT INTO conversations (id, customer_id) VALUES (gen_random_uuid(), 'C001')")
    monkeypatch.setenv("DATABASE_URL", uri)
    monkeypatch.setenv("RETENTION_DAYS", days)
    assert retention.main(["expire"]) == 2
    assert "RETENTION_DAYS" in capsys.readouterr().err
    with psycopg.connect(uri) as conn:
        assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 1
