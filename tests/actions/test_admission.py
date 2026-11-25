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

"""MongoDB capacity reservations shared by synchronous and asynchronous starts."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel

from nomad.actions.application import ActionCreation, ActionService
from nomad.actions.domain import (
    ActionConcurrencyLimitError,
    ActionDefinition,
    ActionStatus,
)
from nomad.actions.mongo_repository import (
    MongoAsyncActionRepository,
    MongoSyncActionRepository,
)


async def _reserved_instances(repo: MongoAsyncActionRepository) -> list[str]:
    document = await repo.capacity.find_one({'_id': 'action'})
    return [run['instance_id'] for run in document['active_runs']] if document else []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'per_user, global_limit, expected', [(10, 3, 3), (2, 10, 4), (2, 3, 3)]
)
async def test_concurrent_sync_and_async_starts(
    mongo_function, async_mongo_function, per_user, global_limit, expected
):
    repository = MongoSyncActionRepository()
    a_repository = MongoAsyncActionRepository()

    definition = ActionDefinition(
        'action',
        max_concurrent_runs_per_user=per_user,
        max_concurrent_runs=global_limit,
    )
    service = ActionService(
        repository=repository,
        a_repository=a_repository,
        workflow=SimpleNamespace(start=Mock(), get_status=Mock(return_value=None)),
        a_workflow=SimpleNamespace(
            start=AsyncMock(), get_status=AsyncMock(return_value=None)
        ),
        creation=ActionCreation(
            SimpleNamespace(get=lambda action_id: definition),
            SimpleNamespace(
                has_assets=lambda data: False,
                consume=AsyncMock(return_value=[]),
                rollback=AsyncMock(),
            ),
        ),
    )

    class Input(BaseModel):
        user_id: str

    async def start(index):
        data = Input(user_id=f'owner-{(index // 2) % 2}')
        if index % 2:
            return await service.a_start('action', data)
        return await asyncio.to_thread(service.start, 'action', data)

    results = await asyncio.gather(
        *(start(index) for index in range(20)), return_exceptions=True
    )
    accepted = [result for result in results if isinstance(result, str)]
    rejected = [
        result for result in results if isinstance(result, ActionConcurrencyLimitError)
    ]
    assert len(accepted) == expected
    assert len(accepted) + len(rejected) == 20
    records = await a_repository.list_active(None, 20)
    assert {record.action_instance_id for record in records} == set(accepted)
    for user_id in ['owner-0', 'owner-1']:
        assert sum(record.user_id == user_id for record in records) <= per_user
    assert set(await _reserved_instances(a_repository)) == set(accepted)


@pytest.mark.asyncio
async def test_pending_reservation_survives_status_check_before_temporal_start(
    mongo_function, async_mongo_function
):
    repo = MongoAsyncActionRepository()
    definition = ActionDefinition('action', max_concurrent_runs=1)

    class Input(BaseModel):
        user_id: str = 'owner'

    async def start_workflow(definition, data, instance_id, user_id):
        assert await _reserved_instances(repo) == [instance_id]
        await service.a_get_status(instance_id, user_id)
        assert (await service.a_get_owned(instance_id, user_id)).status == 'PENDING'
        with pytest.raises(ActionConcurrencyLimitError):
            await service.a_start('action', Input())

    service = ActionService(
        repository=MongoSyncActionRepository(),
        a_repository=repo,
        workflow=Mock(),
        a_workflow=SimpleNamespace(
            start=start_workflow, get_status=AsyncMock(return_value=None)
        ),
        creation=ActionCreation(
            SimpleNamespace(get=lambda action_id: definition),
            SimpleNamespace(consume=AsyncMock(return_value=[])),
        ),
    )
    await service.a_start('action', Input())


@pytest.mark.asyncio
@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('stopped', [False, True])
async def test_reconciliation_reclaims_seeded_slot_after_workflow_ends(
    mongo_function, async_mongo_function, asynchronous, stopped
):
    sync = MongoSyncActionRepository()
    repo = MongoAsyncActionRepository()
    definition = ActionDefinition('action', max_concurrent_runs_per_user=1)
    workflow = Mock(get_status=Mock(return_value=ActionStatus.RUNNING))
    a_workflow = SimpleNamespace(
        start=AsyncMock(),
        cancel=AsyncMock(),
        get_status=AsyncMock(return_value=ActionStatus.RUNNING),
        get_result=AsyncMock(return_value=None),
    )
    creation = ActionCreation(
        SimpleNamespace(get=lambda action_id: definition),
        SimpleNamespace(
            has_assets=lambda data: False, consume=AsyncMock(return_value=[])
        ),
    )
    service = ActionService(
        repository=sync,
        a_repository=repo,
        workflow=workflow,
        a_workflow=a_workflow,
        creation=creation,
    )

    class Input(BaseModel):
        user_id: str = '$owner'

    _, existing = creation.prepare('action', Input())
    await repo.create(existing)

    async def start():
        if asynchronous:
            return await service.a_start('action', Input())
        return service.start('action', Input())

    with pytest.raises(ActionConcurrencyLimitError):
        await start()
    assert await _reserved_instances(repo) == [existing.action_instance_id]
    if stopped:
        await service.a_stop(existing.action_instance_id, existing.user_id)
    await service.a_reconcile_active()
    assert await _reserved_instances(repo) == [existing.action_instance_id]

    a_workflow.get_status.return_value = (
        ActionStatus.CANCELED if stopped else ActionStatus.COMPLETED
    )
    with pytest.raises(ActionConcurrencyLimitError):
        await start()
    a_workflow.get_status.assert_awaited_once_with(existing.action_instance_id)
    await service.a_reconcile_active()
    replacement = await start()
    assert await _reserved_instances(repo) == [replacement]
    await repo.release_slots('action', [existing.action_instance_id])
    assert await _reserved_instances(repo) == [replacement]


@pytest.mark.asyncio
@pytest.mark.parametrize('claimed_age_minutes', [1, 4])
async def test_reconciler_reclaims_stale_missing_workflows(
    mongo_function, async_mongo_function, claimed_age_minutes
):
    sync = MongoSyncActionRepository()
    repo = MongoAsyncActionRepository()
    definition = ActionDefinition('action')
    creation = ActionCreation(Mock(get=Mock(return_value=definition)), Mock())
    workflow = Mock(get_status=AsyncMock(return_value=None))
    service = ActionService(
        repository=sync,
        a_repository=repo,
        workflow=Mock(),
        a_workflow=workflow,
        creation=creation,
    )

    class Input(BaseModel):
        user_id: str = 'owner'

    records = [creation.prepare('action', Input())[1] for _ in range(4)]
    orphan, pending, submitted, fresh = records
    for record in records:
        assert await repo.reserve_slot(definition, record)
    await repo.create(pending)
    submitted.created_at = service.clock() - timedelta(minutes=claimed_age_minutes)
    await repo.create(submitted)
    assert sync.claim_submission(submitted)
    await repo.capacity.update_one(
        {'_id': 'action'},
        {
            '$set': {
                'active_runs.$[slot].unsubmitted_since': service.clock()
                - timedelta(minutes=4)
            }
        },
        array_filters=[
            {
                'slot.instance_id': {
                    '$in': [orphan.action_instance_id, pending.action_instance_id]
                }
            }
        ],
    )

    await service.a_reconcile_active()

    remaining = {fresh.action_instance_id}
    if claimed_age_minutes < 3:
        remaining.add(submitted.action_instance_id)
    assert set(await _reserved_instances(repo)) == remaining
    assert not sync.claim_submission(orphan)
    assert not await repo.claim_submission(pending)
    assert (
        await repo.require_for_user(pending.action_instance_id, pending.user_id)
    ).status == 'FAILED'
    submitted_record = await repo.require_for_user(
        submitted.action_instance_id, submitted.user_id
    )
    assert submitted_record.status == (
        'PENDING' if claimed_age_minutes < 3 else 'FAILED'
    )
    workflow.get_status.assert_awaited_once_with(submitted.action_instance_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['assets', 'start'])
async def test_start_error_releases_only_if_temporal_was_not_called(
    mongo_function, async_mongo_function, failure
):
    repo = MongoAsyncActionRepository()
    definition = ActionDefinition('action', max_concurrent_runs=1)
    assets = SimpleNamespace(consume=AsyncMock(return_value=[]), rollback=AsyncMock())
    workflow = SimpleNamespace(
        start=AsyncMock(), get_status=AsyncMock(return_value=None)
    )
    target = assets.consume if failure == 'assets' else workflow.start
    target.side_effect = RuntimeError('failed')
    service = ActionService(
        repository=MongoSyncActionRepository(),
        a_repository=repo,
        workflow=Mock(),
        a_workflow=workflow,
        creation=ActionCreation(
            SimpleNamespace(get=lambda action_id: definition), assets
        ),
    )

    class Input(BaseModel):
        user_id: str = 'owner'

    with pytest.raises(RuntimeError, match='failed'):
        await service.a_start('action', Input())
    if failure == 'assets':
        assert await _reserved_instances(repo) == []
        workflow.start.assert_not_awaited()
    else:
        assert len(await _reserved_instances(repo)) == 1
        with pytest.raises(ActionConcurrencyLimitError):
            await service.a_start('action', Input())
