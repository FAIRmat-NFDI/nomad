#
# Copyright The NOMAD Authors.
#
# This file is part of NOMAD. See https://nomad-lab.eu for further info.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from datetime import datetime, timezone
from typing import Any

import pytest

from nomad import infrastructure
from nomad.actions.domain import ActionRecord
from nomad.actions.mongo_repository import (
    MongoAsyncActionRepository as AsyncActionRepository,
)
from nomad.actions.mongo_repository import (
    MongoSyncActionRepository as SyncActionRepository,
)
from nomad.config import config


def _record(action_instance_id: str, user_id: str, **overrides) -> ActionRecord:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        'action_id': 'my-action',
        'action_instance_id': action_instance_id,
        'user_id': user_id,
        'upload_id': None,
        'status': 'PENDING',
        'input_data': {'foo': 'bar'},
        'signal_input_requests': [],
        'signal_inputs_submitted': [],
        'results': None,
        'created_at': now,
        'updated_at': now,
    }
    payload.update(overrides)
    return ActionRecord(**payload)


def _comparable_record(record: ActionRecord) -> dict:
    payload = record.model_dump()
    payload.pop('updated_at', None)
    return payload


def test_sync_repository_create_and_get_for_user(mongo_function, user1):
    repo = SyncActionRepository()

    created = repo.create(_record('workflow-sync-1', user1.user_id))
    fetched = repo.get_for_user('workflow-sync-1', user1.user_id)

    assert created.action_instance_id == 'workflow-sync-1'
    assert fetched is not None
    assert fetched.action_id == 'my-action'
    assert fetched.input_data == {'foo': 'bar'}


def test_sync_repository_status_and_result_updates(mongo_function, user1):
    repo = SyncActionRepository()
    repo.create(_record('workflow-sync-2', user1.user_id))

    updated = repo.set_status_for_user('workflow-sync-2', user1.user_id, 'RUNNING')
    assert updated is not None
    assert updated.status == 'RUNNING'

    completed = repo.save_result_for_user(
        'workflow-sync-2',
        user1.user_id,
        'COMPLETED',
        {'answer': 42},
    )
    assert completed is not None
    assert completed.status == 'COMPLETED'
    assert completed.results == {'answer': 42}


def test_sync_repository_require_for_user_raises(mongo_function, user1):
    repo = SyncActionRepository()

    with pytest.raises(Exception, match='was not registered in the DB'):
        repo.require_for_user('missing-workflow', user1.user_id)


@pytest.mark.asyncio
async def test_async_repository_create_get_and_save_result(
    mongo_function, async_mongo_function, user1
):
    repo = AsyncActionRepository()
    await repo.create(_record('workflow-async-1', user1.user_id))

    fetched = await repo.get_for_user('workflow-async-1', user1.user_id)
    assert fetched is not None
    assert fetched.status == 'PENDING'

    patched = await repo.save_result_for_user(
        'workflow-async-1',
        user1.user_id,
        status='RUNNING',
        results={'progress': 10},
    )
    assert patched is not None
    assert patched.status == 'RUNNING'
    assert patched.results == {'progress': 10}


@pytest.mark.asyncio
async def test_async_repository_list_and_count(
    mongo_function, async_mongo_function, user1
):
    repo = AsyncActionRepository()
    await repo.create(_record('workflow-async-2', user1.user_id, upload_id='upload-a'))
    await repo.create(_record('workflow-async-3', user1.user_id, upload_id='upload-b'))

    records, count = await repo.list_for_user(user1.user_id, page_size=10)
    filtered_count = await repo.count_for_user(user1.user_id, upload_id='upload-a')

    assert count == 2
    assert len(records) == 2
    assert filtered_count == 1


@pytest.mark.asyncio
async def test_async_repository_pending_signal_input_roundtrip(
    mongo_function, async_mongo_function, user1
):
    repo = AsyncActionRepository()
    await repo.create(_record('workflow-async-4', user1.user_id, status='RUNNING'))

    request_info = {'signal_fn_name': 'approve', 'title': 'Approve action'}
    created = await repo.add_pending_signal_input(
        'workflow-async-4',
        user1.user_id,
        'approve',
        request_info,
    )
    assert created is True

    consumed = await repo.claim_pending_signal_input(
        'workflow-async-4',
        user1.user_id,
        'approve',
    )
    assert consumed is not None
    assert consumed.request['signal_fn_name'] == 'approve'

    await repo.restore_pending_signal_input(
        'workflow-async-4',
        user1.user_id,
        'approve',
        request_info,
    )
    restored = await repo.require_for_user('workflow-async-4', user1.user_id)
    assert restored.signal_input_requests[0]['signal_fn_name'] == 'approve'

    submitted_entry = {
        'signal_fn_name': 'approve',
        'data': {'accepted': True},
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    await repo.append_submitted_signal_input(
        'workflow-async-4',
        user1.user_id,
        submitted_entry,
    )
    updated = await repo.require_for_user('workflow-async-4', user1.user_id)
    assert updated.signal_inputs_submitted[0]['signal_fn_name'] == 'approve'


@pytest.mark.asyncio
async def test_typed_signal_claim_is_owned_and_single_use(
    mongo_function, async_mongo_function, user1
):
    from nomad.actions.domain import SignalInputClaim

    repo = AsyncActionRepository()
    request = {'signal_fn_name': 'approve', 'title': 'Approval'}
    await repo.create(
        _record(
            'claim-workflow',
            user1.user_id,
            status='RUNNING',
            signal_input_requests=[request],
        )
    )
    assert (
        await repo.claim_pending_signal_input('claim-workflow', 'other-user', 'approve')
        is None
    )
    claim = await repo.claim_pending_signal_input(
        'claim-workflow', user1.user_id, 'approve'
    )
    assert claim == SignalInputClaim(action_id='my-action', request=request)
    assert (
        await repo.claim_pending_signal_input(
            'claim-workflow', user1.user_id, 'approve'
        )
        is None
    )


@pytest.mark.asyncio
async def test_async_repository_require_for_user_raises(
    mongo_function, async_mongo_function, user1
):
    repo = AsyncActionRepository()

    with pytest.raises(Exception, match='was not registered in the DB'):
        await repo.require_for_user('missing-workflow', user1.user_id)


def test_sync_repository_uses_configured_collection(mongo_function):
    repo = SyncActionRepository()

    collection = repo.collection

    assert collection.name == 'action_document'
    assert collection.database.name == config.mongo.db_name
    assert infrastructure.mongo_client is not None


@pytest.mark.asyncio
async def test_sync_and_async_get_for_user_return_same_record(
    mongo_function, async_mongo_function, user1
):
    sync_repo = SyncActionRepository()
    async_repo = AsyncActionRepository()
    record = _record(
        'workflow-parity-1',
        user1.user_id,
        upload_id='upload-1',
        status='RUNNING',
        results={'step': 1},
        signal_input_requests=[{'signal_fn_name': 'approve'}],
    )

    sync_repo.create(record)

    sync_result = sync_repo.get_for_user('workflow-parity-1', user1.user_id)
    async_result = await async_repo.get_for_user('workflow-parity-1', user1.user_id)

    assert sync_result is not None
    assert async_result is not None
    assert _comparable_record(sync_result) == _comparable_record(async_result)


@pytest.mark.asyncio
async def test_sync_and_async_updates_produce_same_record(
    mongo_function, async_mongo_function, user1
):
    sync_repo = SyncActionRepository()
    async_repo = AsyncActionRepository()
    sync_record = _record('workflow-parity-sync', user1.user_id)
    async_record = sync_record.model_copy(
        update={'action_instance_id': 'workflow-parity-async'}
    )

    sync_repo.create(sync_record)
    await async_repo.create(async_record)

    sync_repo.set_status_for_user('workflow-parity-sync', user1.user_id, 'RUNNING')
    sync_repo.save_result_for_user(
        'workflow-parity-sync',
        user1.user_id,
        'COMPLETED',
        {'answer': 42},
    )
    sync_result = sync_repo.get_for_user('workflow-parity-sync', user1.user_id)

    await async_repo.set_status_for_user(
        'workflow-parity-async', user1.user_id, 'RUNNING'
    )
    await async_repo.save_result_for_user(
        'workflow-parity-async',
        user1.user_id,
        'COMPLETED',
        {'answer': 42},
    )
    async_result = await async_repo.get_for_user('workflow-parity-async', user1.user_id)

    assert sync_result is not None
    assert async_result is not None
    assert _comparable_record(
        sync_result.model_copy(update={'action_instance_id': 'shared-workflow'})
    ) == _comparable_record(
        async_result.model_copy(update={'action_instance_id': 'shared-workflow'})
    )
