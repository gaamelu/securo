"""Opt-in PostgreSQL proofs for migration 071 and per-connection sync locks.

Run against a disposable PostgreSQL database (or a database where creating a
temporary schema is acceptable):

    SECURO_TEST_POSTGRES_URL=postgresql+asyncpg://... pytest -q \
        tests/test_provider_bill_reconciliation_postgres.py

The regular suite remains SQLite-only, so these tests are skipped unless the
explicit PostgreSQL URL is supplied.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import date
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.connection_service import _get_connection_for_sync


POSTGRES_URL = os.getenv("SECURO_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="set SECURO_TEST_POSTGRES_URL to run PostgreSQL integration proofs",
)


def _load_migration_071():
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "071_transaction_provider_bill_id.py"
    )
    spec = importlib.util.spec_from_file_location("migration_071", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_migration_071_upgrade_backfill_and_downgrade_on_postgres():
    assert POSTGRES_URL is not None
    engine = create_async_engine(POSTGRES_URL)
    schema = f"test_bill_migration_{uuid.uuid4().hex}"
    bill_id = uuid.uuid4()
    migration = _load_migration_071()

    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await connection.execute(text(
                "CREATE TABLE credit_card_bills (id uuid PRIMARY KEY)"
            ))
            await connection.execute(text(
                """
                CREATE TABLE transactions (
                    id uuid PRIMARY KEY,
                    source varchar(20) NOT NULL,
                    bill_id uuid NULL REFERENCES credit_card_bills(id),
                    effective_bill_date date NULL
                )
                """
            ))
            await connection.execute(
                text("INSERT INTO credit_card_bills (id) VALUES (:bill_id)"),
                {"bill_id": bill_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO transactions
                        (id, source, bill_id, effective_bill_date)
                    VALUES
                        (:eligible, 'sync', :bill_id, NULL),
                        (:override, 'sync', :bill_id, DATE '2026-09-01'),
                        (:manual, 'manual', :bill_id, NULL)
                    """
                ),
                {
                    "eligible": uuid.uuid4(),
                    "override": uuid.uuid4(),
                    "manual": uuid.uuid4(),
                    "bill_id": bill_id,
                },
            )

            def upgrade(sync_connection):
                migration.op = Operations(MigrationContext.configure(sync_connection))
                migration.upgrade()

            await connection.run_sync(upgrade)
            rows = (await connection.execute(text(
                """
                SELECT source, effective_bill_date,
                       provider_bill_id, provider_bill_membership_known
                FROM transactions
                ORDER BY source, effective_bill_date NULLS FIRST
                """
            ))).all()
            assert rows == [
                ("manual", None, None, False),
                ("sync", None, bill_id, True),
                ("sync", date(2026, 9, 1), None, False),
            ]

            index_count = await connection.scalar(text(
                """
                SELECT count(*) FROM pg_indexes
                WHERE schemaname = :schema
                  AND indexname = 'ix_transactions_provider_bill_id'
                """
            ), {"schema": schema})
            fk_count = await connection.scalar(text(
                """
                SELECT count(*)
                FROM pg_constraint c
                JOIN pg_namespace n ON n.oid = c.connamespace
                WHERE n.nspname = :schema
                  AND c.conname = 'fk_transactions_provider_bill_id_credit_card_bills'
                """
            ), {"schema": schema})
            assert index_count == 1
            assert fk_count == 1

            def downgrade(sync_connection):
                migration.op = Operations(MigrationContext.configure(sync_connection))
                migration.downgrade()

            await connection.run_sync(downgrade)
            remaining = await connection.scalar(text(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name = 'transactions'
                  AND column_name IN (
                      'provider_bill_id', 'provider_bill_membership_known'
                  )
                """
            ), {"schema": schema})
            assert remaining == 0
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


@pytest.mark.asyncio
async def test_connection_sync_lock_serializes_on_postgres():
    assert POSTGRES_URL is not None
    engine = create_async_engine(POSTGRES_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    schema = f"test_sync_lock_{uuid.uuid4().hex}"
    connection_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await connection.execute(text(
                """
                CREATE TABLE bank_connections (
                    id uuid PRIMARY KEY,
                    user_id uuid NOT NULL,
                    workspace_id uuid NOT NULL,
                    provider varchar(50) NOT NULL,
                    external_id varchar(255) NOT NULL,
                    institution_name varchar(255) NOT NULL,
                    display_name varchar(255),
                    logo_url varchar(500),
                    credentials json,
                    settings json,
                    status varchar(50) NOT NULL,
                    last_sync_at timestamptz,
                    created_at timestamptz NOT NULL
                )
                """
            ))
            await connection.execute(text(
                """
                INSERT INTO bank_connections (
                    id, user_id, workspace_id, provider, external_id,
                    institution_name, credentials, settings, status, created_at
                ) VALUES (
                    :id, :user_id, :workspace_id, 'test', 'external',
                    'Lock Test', '{}', '{}', 'active', now()
                )
                """
            ), {
                "id": connection_id,
                "user_id": uuid.uuid4(),
                "workspace_id": workspace_id,
            })

        async with sessions() as first, sessions() as second:
            await first.execute(text(f'SET search_path TO "{schema}"'))
            locked = await _get_connection_for_sync(
                first, connection_id, workspace_id
            )
            assert locked is not None

            await second.execute(text(f'SET search_path TO "{schema}"'))
            await second.execute(text("SET LOCAL lock_timeout = '250ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                await _get_connection_for_sync(second, connection_id, workspace_id)
            await second.rollback()
            await first.rollback()

        async with sessions() as third:
            await third.execute(text(f'SET search_path TO "{schema}"'))
            reacquired = await _get_connection_for_sync(
                third, connection_id, workspace_id
            )
            assert reacquired is not None
            await third.rollback()
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()
