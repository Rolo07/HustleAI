# Integration tests

MCP startup/discovery tests use the local subprocess transport and no Zoho writes.
Future PostgreSQL tests should require an explicit isolated test database URL,
apply migrations in disposable test state, and never use production credentials.
