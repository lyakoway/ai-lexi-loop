"""Integration tests: real PostgreSQL, no Telegram.

These tests require the TEST_DATABASE_URL environment variable and are
skipped otherwise. They run migrations through Alembic — exactly the code
path used in production — rather than ``create_all()``.
"""
