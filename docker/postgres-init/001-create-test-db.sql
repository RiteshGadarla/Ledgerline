-- Runs only against a fresh data volume (docker-entrypoint-initdb.d scripts
-- run once, at first container init). Keeps the test suite's create_all/
-- drop_all cycle off the dev database that `alembic upgrade` targets.
CREATE DATABASE ledgerline_test;
