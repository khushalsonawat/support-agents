#!/bin/sh
# Two databases with two logins on one PostgreSQL server. Neither login can
# connect to the other's database. Runs once, when the data volume is first created.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v agent_password="$AGENT_DB_PASSWORD" -v orders_password="$ORDERS_DB_PASSWORD" <<'SQL'
CREATE ROLE agent_app LOGIN PASSWORD :'agent_password';
CREATE ROLE orders_app LOGIN PASSWORD :'orders_password';
CREATE DATABASE support_agent OWNER agent_app;
CREATE DATABASE orders OWNER orders_app;
REVOKE CONNECT ON DATABASE support_agent FROM PUBLIC;
REVOKE CONNECT ON DATABASE orders FROM PUBLIC;
GRANT CONNECT ON DATABASE support_agent TO agent_app;
GRANT CONNECT ON DATABASE orders TO orders_app;
SQL
