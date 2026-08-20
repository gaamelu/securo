from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.tasks import sync_tasks


class _SessionContext:
    def __init__(self, workspace_id):
        self.workspace_id = workspace_id

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def scalar(self, _statement):
        return self.workspace_id


@pytest.mark.asyncio
async def test_scheduled_sync_requests_fresh_provider_data():
    connection_id = uuid.uuid4()
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    session_maker = MagicMock(return_value=_SessionContext(workspace_id))

    with patch.object(
        sync_tasks.connection_service,
        "sync_connection",
        new_callable=AsyncMock,
    ) as sync_connection:
        await sync_tasks._sync_one(session_maker, connection_id, user_id)

    sync_connection.assert_awaited_once()
    assert sync_connection.await_args.kwargs["trigger_provider_refresh"] is True
