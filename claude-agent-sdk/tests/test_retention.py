import psycopg

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
