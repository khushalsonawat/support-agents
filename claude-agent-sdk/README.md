# Support agent: Claude Agent SDK

This is a customer-support agent for an online shop, built as a small service of the kind a team would really deploy. The shop's own backend (or its helpdesk) calls it on behalf of a signed-in customer. The agent can do these things for that customer:

- look up an order, or list their recent orders;
- start a return, or check a return's status;
- state the shop's return policy;
- hand the customer to a person.

The agent reads the shop's orders through the shop's orders service, over HTTP. Its own PostgreSQL keeps conversations and handoff tickets.

`app/agent.py` is the only file that depends on the Claude Agent SDK. Everything else is the same in each framework's folder of this repository.

## Run it locally

1. Copy the settings file and fill it in:

   ```sh
   cp .env.example .env
   ```

   Set `AGENT_API_TOKEN`, `ORDERS_API_TOKEN` and `ANTHROPIC_API_KEY`, and change the passwords.
2. Start everything:

   ```sh
   docker compose up --build
   ```

   Compose starts PostgreSQL, runs the migrations and the sample data once, then starts the orders service and the agent on port 8000.
3. Ask it something:

   ```sh
   curl -s localhost:8000/chat \
     -H "Authorization: Bearer $AGENT_API_TOKEN" -H 'content-type: application/json' \
     -d '{"customer_id": "C001", "message": "Where is my order A1001?"}'
   ```

   To continue the conversation, send back the `conversation_id` the reply gave you.

The sample customers are:

| Customer | Orders |
|---|---|
| `C001` | `A1001` trail running shoes (delivered), `A1003` water bottle (delivered), `A1004` hiking socks (processing) |
| `C002` | `A1002` rain jacket (shipped), `A1005` trekking poles (delivered) |
| `C003` | `A1006` camping stove (cancelled) |

## The API

`POST /chat` needs `Authorization: Bearer <AGENT_API_TOKEN>`. The reply is not streamed.

Request:

```json
{"customer_id": "C001", "conversation_id": null, "message": "Where is my order A1001?"}
```

Response:

```json
{"conversation_id": "…", "reply": "…", "handed_off": false, "ticket_id": null}
```

- `customer_id` is the signed-in customer, as the caller vouches for it. The model never chooses it, and the tools only ever act for it.
- A conversation belongs to the customer who started it. Asked for by anyone else, it is "not found".
- After a handoff, `handed_off` is `true` and `ticket_id` names the ticket. Later messages in that conversation get "A person from our team will reply here." without calling the model.

| Status | Error | When |
|---|---|---|
| 400 | `invalid_request` | bad JSON, an empty message, or a missing `customer_id` |
| 401 | `unauthorized` | no token, or a wrong one |
| 404 | `conversation_not_found` | an unknown conversation, or another customer's |
| 409 | `conversation_busy` | a message in this conversation is still being answered |
| 413 | `message_too_long` | more than 4,000 characters |
| 429 | `too_many_turns` | more than `MAX_CONCURRENT_TURNS` turns at once; see `Retry-After` |
| 503 | `model_unavailable` | the model provider failed |
| 503 | `not_ready` | the agent's database is down |
| 504 | `turn_timeout` | the turn took more than 55 seconds; nothing was saved, so send the message again |

`GET /health` says the process is alive. `GET /ready` says the database (with migrations applied) and the orders service answer; point a load balancer at this one.

## Settings

Every setting is in `.env.example`, with what happens when it is missing.

- The service does not start without `AGENT_API_TOKEN`, `DATABASE_URL`, `ORDERS_API_URL`, `ORDERS_API_TOKEN`, and a model credential.
- The model credential is either `ANTHROPIC_API_KEY`, or `ANTHROPIC_AUTH_TOKEN` together with `ANTHROPIC_BASE_URL`.
- The default model is `claude-opus-5-5`. Set `ANTHROPIC_MODEL` to change it.

## How it is built

- **The agent's tools are only its own six.** The Claude Agent SDK runs the Claude CLI, which has built-in tools: shell, files and web. They are all off (`tools=[]`, `strict_mcp_config`, no settings files), and so are its own skills and slash commands (`--disable-slash-commands`), so a customer typing `/doctor` sends plain text. The CLI runs in an empty throw-away folder for each turn. It does not get the service's database address or tokens.
- **The customer reaches the tools through the turn.** The tools are built for each turn around that turn's customer. Two customers' turns running at once never see each other's data.
- **Calls to the orders service:**
  - connect timeout 2 s, read timeout 5 s;
  - at most two retries, only on connection errors and 502/503/504;
  - a 12-second budget per tool call.
- **A return is started at most once,** with one key per conversation and order. The orders service also refuses a second open return for the same order.
- **One turn per conversation at a time.** A turn claims its conversation. Its messages and any ticket are saved together at the end. A failed turn saves nothing.
- **History goes to the model as a transcript of the last `HISTORY_MAX_MESSAGES` messages.** The SDK's own session files are not used, because they live on one machine.
- **The pinned `claude-agent-sdk` (0.2.174) wheel includes the Claude CLI,** so the image needs no Node.
- **The container runs as a non-root user with a read-only root filesystem.** Only `/tmp` is writable.

## Migrations and sample data

- Migrations are plain SQL files in `migrations/`, applied in order by `python -m app.db.migrate`. The orders service has its own in `orders_service/migrations/`. Each migration runs under an advisory lock, so two at once are safe.
- They run as a one-off step before the service starts, never from the service itself.
- The sample data (`python -m orders_service.seed`) is for local runs only.

## Logs and traces

- **Logs** are JSON on stdout. Each line carries `request_id` (from `X-Request-Id`, or a new one), and the conversation and customer where they apply.
- **Message text and tool arguments** are customer data, so they are logged only at `DEBUG`. Secrets are never logged.
- **OpenTelemetry tracing and metrics** are on when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. They give a span per request and per tool call, plus request, tool-error and handoff counts.

## Keeping data

Conversations are kept for `RETENTION_DAYS` (90 by default). Run these on a schedule or on request:

```sh
python -m app.retention expire                    # remove conversations past the period
python -m app.retention delete --customer C001    # remove one customer's conversations
```

## Handoff tickets

Tickets are stored in the agent's own database, with status `open`. A real deployment would send them to its helpdesk. `notify_helpdesk` in `app/tools/handoff.py` marks where.

## Tests

```sh
uv sync
uv run pytest
```

- The database tests use `TEST_DATABASE_URL` if it is set. If not, they start a local PostgreSQL with `pgserver`.
- The model is replaced by a fake that calls chosen tools (`tests/conftest.py`), so the tests make no paid model calls.

## Why each framework has its own full copy

A team builds its agent on one framework, so each folder is a complete service that can be copied out and deployed on its own, with nothing shared to install. The price is that the files other than `app/agent.py` are repeated in every folder; once a second framework is added, a CI check will keep those files identical.
