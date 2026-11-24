-- The agent's own data: conversations, their messages, and handoff tickets.
-- The shop's orders are not here; the agent reads them from the orders service over HTTP.

CREATE TABLE conversations (
    id              uuid PRIMARY KEY,
    customer_id     text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    handed_off      boolean NOT NULL DEFAULT false,
    -- Set while a turn is running. A value older than the turn limit is stale.
    turn_started_at timestamptz
);

CREATE INDEX conversations_customer_idx ON conversations (customer_id, created_at);

CREATE TABLE messages (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role            text NOT NULL CHECK (role IN ('customer', 'agent')),
    content         text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX messages_conversation_idx ON messages (conversation_id, id);

CREATE TABLE handoff_tickets (
    id              uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    customer_id     text NOT NULL,
    reason          text NOT NULL,
    summary         text NOT NULL,
    status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX handoff_tickets_open_idx ON handoff_tickets (status, created_at);
