# Support agents

The same customer-support agent for an online shop, built once per agent framework. Each one is a small service of the kind a team would really deploy:

- an HTTP API (`POST /chat`, `/health`, `/ready`) that the shop's backend calls for a signed-in customer;
- six tools that call the shop's orders service over HTTP: look up an order, list recent orders, start a return, check a return, state the return policy, and hand off to a person;
- its own PostgreSQL for conversations and handoff tickets, with migrations;
- settings from the environment, JSON logs, optional OpenTelemetry tracing, and a retention job.

| Folder | Framework | State |
|---|---|---|
| [`claude-agent-sdk/`](claude-agent-sdk/) | Claude Agent SDK | ready for review |

More frameworks follow, one folder each. In every folder only `app/agent.py` depends on the framework; everything else is the same.

Each folder stands alone, with its own README, tests, Dockerfile and `docker-compose.yml`, which also runs a stand-in orders service with sample customers. Start with [`claude-agent-sdk/README.md`](claude-agent-sdk/README.md).
