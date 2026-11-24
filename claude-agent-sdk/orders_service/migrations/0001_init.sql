-- The stand-in for the shop's own orders system. The agent never reads these tables; it
-- calls this service's API.

CREATE TABLE customers (
    id   text PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE orders (
    id          text PRIMARY KEY,
    customer_id text NOT NULL REFERENCES customers (id),
    item        text NOT NULL,
    status      text NOT NULL CHECK (status IN ('processing', 'shipped', 'delivered', 'cancelled')),
    placed_at   timestamptz NOT NULL
);

CREATE INDEX orders_customer_idx ON orders (customer_id, placed_at DESC);

CREATE TABLE returns (
    id         uuid PRIMARY KEY,
    order_id   text NOT NULL REFERENCES orders (id),
    reason     text NOT NULL,
    status     text NOT NULL CHECK (status IN ('requested', 'approved', 'refunded', 'rejected')),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- At most one open return per order, whatever any caller does.
CREATE UNIQUE INDEX returns_one_open_per_order
    ON returns (order_id) WHERE status IN ('requested', 'approved');

-- The first answer to each Idempotency-Key, returned again to a repeat.
CREATE TABLE idempotency_keys (
    customer_id text NOT NULL,
    key         text NOT NULL,
    status_code integer NOT NULL,
    response    jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (customer_id, key)
);

CREATE TABLE policy (
    id   integer PRIMARY KEY CHECK (id = 1),
    text text NOT NULL
);
