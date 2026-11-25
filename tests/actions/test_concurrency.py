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

"""Action concurrency configuration and admission checks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel, ValidationError

from nomad.actions.application import ActionCreation, ActionService
from nomad.actions.domain import ActionConcurrencyLimitError, ActionDefinition
from nomad.config.models.plugins import ActionEntryPoint


@pytest.mark.parametrize(
    'field', ['max_concurrent_runs_per_user', 'max_concurrent_runs']
)
@pytest.mark.parametrize('limit', [None, 1, 5, 0, -1])
def test_concurrency_configuration(field, limit):
    if limit is None or limit < 1:
        with pytest.raises(ValidationError):
            ActionEntryPoint(**{field: limit})
    else:
        entry = ActionEntryPoint(**{field: limit})
        assert getattr(entry, field) == limit


def test_catalog_preserves_concurrency_limits(monkeypatch):
    from nomad.actions.plugin_adapter import PluginActionCatalog

    entry = ActionEntryPoint(max_concurrent_runs_per_user=2, max_concurrent_runs=5)
    monkeypatch.setattr(
        'nomad.actions.plugin_adapter.get_actions', lambda: {'action': entry}
    )
    definition = PluginActionCatalog().get('action')
    assert definition.max_concurrent_runs_per_user == 2
    assert definition.max_concurrent_runs == 5


@pytest.mark.asyncio
@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize(
    'per_user, global_limit, user_count, global_count, rejected',
    [
        (2, 10, 1, 5, False),
        (2, 10, 2, 5, True),
        (2, 10, 3, 5, True),
        (2, 2, 0, 1, False),
        (2, 2, 0, 2, True),
        (2, 2, 0, 3, True),
        (2, 3, 1, 3, True),
        (2, 3, 2, 2, True),
        (2, 3, 1, 2, False),
    ],
)
async def test_start_concurrency(
    asynchronous, per_user, global_limit, user_count, global_count, rejected
):
    reserved = user_count < per_user and global_count < global_limit
    sync_repository = Mock(
        reserve_slot=Mock(return_value=reserved),
    )
    async_repository = SimpleNamespace(
        reserve_slot=AsyncMock(return_value=reserved),
        claim_submission=AsyncMock(return_value=True),
        create=AsyncMock(),
    )
    sync_workflow = Mock()
    async_workflow = SimpleNamespace(start=AsyncMock())
    assets = SimpleNamespace(
        has_assets=Mock(return_value=False),
        consume=AsyncMock(return_value=[]),
        rollback=AsyncMock(),
    )
    definition = ActionDefinition(
        'action',
        max_concurrent_runs_per_user=per_user,
        max_concurrent_runs=global_limit,
    )
    service = ActionService(
        repository=sync_repository,
        a_repository=async_repository,
        workflow=sync_workflow,
        a_workflow=async_workflow,
        creation=ActionCreation(Mock(get=Mock(return_value=definition)), assets),
    )

    class Input(BaseModel):
        user_id: str

    data = Input(user_id='owner')

    async def start():
        if asynchronous:
            return await service.a_start('action', data)
        return service.start('action', data)

    if rejected:
        with pytest.raises(ActionConcurrencyLimitError, match='maximum number'):
            await start()
        sync_repository.create.assert_not_called()
        async_repository.create.assert_not_awaited()
        sync_workflow.start.assert_not_called()
        async_workflow.start.assert_not_awaited()
        assets.consume.assert_not_awaited()
    else:
        assert (await start()).startswith('action-owner-')
        if asynchronous:
            async_repository.create.assert_awaited_once()
            async_workflow.start.assert_awaited_once()
        else:
            sync_repository.create.assert_called_once()
            sync_workflow.start.assert_called_once()
    if asynchronous:
        async_repository.reserve_slot.assert_awaited_once()
    else:
        sync_repository.reserve_slot.assert_called_once()
    sync_workflow.get_status.assert_not_called()
