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

"""Application tests with fake ports; no MongoDB or Temporal required."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nomad.actions.application import ActionService
from nomad.actions.domain import ActionNotFoundError, ActionNotRunningError


@pytest.mark.asyncio
async def test_single_service_routes_sync_and_async_calls_to_separate_ports():
    from nomad.actions.domain import ActionStatus

    repository = Mock()
    workflow = Mock()
    workflow.get_status.return_value = ActionStatus.RUNNING
    a_repository = SimpleNamespace(
        require_for_user=AsyncMock(), set_status_for_user=AsyncMock()
    )
    a_workflow = SimpleNamespace(
        get_status=AsyncMock(return_value=ActionStatus.COMPLETED)
    )
    service = ActionService(
        repository=repository,
        workflow=workflow,
        a_repository=a_repository,
        a_workflow=a_workflow,
        creation=Mock(),
    )

    assert service.get_status('sync-instance', 'owner') is ActionStatus.RUNNING
    a_repository.require_for_user.assert_not_awaited()
    a_workflow.get_status.assert_not_awaited()
    assert (
        await service.a_get_status('async-instance', 'owner') is ActionStatus.COMPLETED
    )
    repository.require_for_user.assert_called_once_with('sync-instance', 'owner')
    workflow.get_status.assert_called_once_with('sync-instance')
    a_repository.require_for_user.assert_awaited_once_with('async-instance', 'owner')
    a_workflow.get_status.assert_awaited_once_with('async-instance')


@pytest.mark.asyncio
async def test_public_status_wrappers_preserve_names_and_temporal_enum(monkeypatch):
    from temporalio.client import WorkflowExecutionStatus

    from nomad.actions import manager
    from nomad.actions.domain import ActionStatus

    sync = Mock(return_value=ActionStatus.RUNNING)
    asynchronous = AsyncMock(return_value=ActionStatus.COMPLETED)
    monkeypatch.setattr(manager.action_service, 'get_status', sync)
    monkeypatch.setattr(manager.action_service, 'a_get_status', asynchronous)

    assert (
        manager.get_action_status('instance', 'owner')
        is WorkflowExecutionStatus.RUNNING
    )
    assert (
        await manager.get_action_status_async('instance', 'owner')
        is WorkflowExecutionStatus.COMPLETED
    )
    sync.assert_called_once_with('instance', 'owner')
    asynchronous.assert_awaited_once_with('instance', 'owner')


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['PENDING', 'RUNNING'])
async def test_async_stop_orders_effects(status):
    calls = []
    repository = SimpleNamespace(
        require_for_user=AsyncMock(return_value=SimpleNamespace(status=status)),
        set_status_for_user=AsyncMock(
            side_effect=lambda *args: calls.append('persist')
        ),
    )
    workflow = SimpleNamespace(
        cancel=AsyncMock(side_effect=lambda *args: calls.append('cancel'))
    )
    await ActionService(
        repository=Mock(),
        workflow=Mock(),
        a_repository=repository,
        a_workflow=workflow,
        creation=Mock(),
    ).a_stop('instance', 'owner')
    assert calls == ['cancel', 'persist']
    repository.require_for_user.assert_awaited_once_with('instance', 'owner')
    repository.set_status_for_user.assert_awaited_once_with(
        'instance', 'owner', 'CANCELED'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'inactive', 'cancel'])
async def test_async_stop_failure_does_not_persist(failure):
    repository = SimpleNamespace(
        require_for_user=AsyncMock(
            return_value=SimpleNamespace(
                status='COMPLETED' if failure == 'inactive' else 'RUNNING'
            )
        ),
        set_status_for_user=AsyncMock(),
    )
    workflow = SimpleNamespace(cancel=AsyncMock())
    if failure == 'missing':
        repository.require_for_user.side_effect = ActionNotFoundError()
    if failure == 'cancel':
        workflow.cancel.side_effect = RuntimeError('Temporal unavailable')
    expected = {
        'missing': ActionNotFoundError,
        'inactive': ActionNotRunningError,
        'cancel': RuntimeError,
    }[failure]
    with pytest.raises(expected):
        await ActionService(
            repository=Mock(),
            workflow=Mock(),
            a_repository=repository,
            a_workflow=workflow,
            creation=Mock(),
        ).a_stop('instance', 'owner')
    repository.set_status_for_user.assert_not_awaited()
    if failure != 'cancel':
        workflow.cancel.assert_not_awaited()


@pytest.mark.parametrize('failure', [None, 'missing', 'inactive', 'cancel'])
def test_sync_stop(failure):
    calls = []
    repository = SimpleNamespace(
        require_for_user=Mock(
            return_value=SimpleNamespace(
                status='COMPLETED' if failure == 'inactive' else 'RUNNING'
            )
        ),
        set_status_for_user=Mock(side_effect=lambda *args: calls.append('persist')),
    )
    workflow = SimpleNamespace(
        cancel=Mock(side_effect=lambda *args: calls.append('cancel'))
    )
    if failure == 'missing':
        repository.require_for_user.side_effect = ActionNotFoundError()
    if failure == 'cancel':
        workflow.cancel.side_effect = RuntimeError('Temporal unavailable')
    service = ActionService(
        repository=repository,
        workflow=workflow,
        a_repository=Mock(),
        a_workflow=Mock(),
        creation=Mock(),
    )
    if failure:
        expected = {
            'missing': ActionNotFoundError,
            'inactive': ActionNotRunningError,
            'cancel': RuntimeError,
        }[failure]
        with pytest.raises(expected):
            service.stop('instance', 'owner')
        repository.set_status_for_user.assert_not_called()
        if failure != 'cancel':
            workflow.cancel.assert_not_called()
    else:
        service.stop('instance', 'owner')
        assert calls == ['cancel', 'persist']
        repository.set_status_for_user.assert_called_once_with(
            'instance', 'owner', 'CANCELED'
        )
    repository.require_for_user.assert_called_once_with('instance', 'owner')


@pytest.fixture
def service_ports():
    from nomad.actions.application import ActionCreation
    from nomad.actions.domain import ActionDefinition

    repository = SimpleNamespace(
        create=AsyncMock(),
        require_for_user=AsyncMock(),
        set_status_for_user=AsyncMock(),
        save_result_for_user=AsyncMock(),
        claim_pending_signal_input=AsyncMock(),
        restore_pending_signal_input=AsyncMock(),
        append_submitted_signal_input=AsyncMock(),
    )
    workflow = SimpleNamespace(
        start=AsyncMock(),
        signal=AsyncMock(),
        get_result=AsyncMock(),
        get_status=AsyncMock(),
    )
    assets = SimpleNamespace(
        consume=AsyncMock(return_value=['receipt']), rollback=AsyncMock()
    )
    catalog = SimpleNamespace(get=Mock(return_value=ActionDefinition('action')))
    service = ActionService(
        repository=Mock(),
        workflow=Mock(),
        a_repository=repository,
        a_workflow=workflow,
        creation=ActionCreation(catalog, assets),
    )
    return service, repository, workflow, assets


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['create', 'start'])
async def test_start_compensates_assets(service_ports, failure):
    from pydantic import BaseModel, SecretStr

    class Input(BaseModel):
        user_id: str
        secret: SecretStr

    service, repository, workflow, assets = service_ports
    target = repository.create if failure == 'create' else workflow.start
    target.side_effect = RuntimeError('failed')
    with pytest.raises(RuntimeError, match='failed'):
        await service.a_start('action', Input(user_id='owner', secret='hidden'))
    assets.rollback.assert_awaited_once_with(['receipt'])
    record = repository.create.call_args.args[0]
    assert record.input_data == {'user_id': 'owner'}
    assert record.created_at == record.updated_at
    if failure == 'create':
        workflow.start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['consume', 'signal', 'rollback'])
async def test_signal_restores_claim_on_failure(service_ports, failure):
    from nomad.actions.domain import SignalInputClaim

    service, repository, workflow, assets = service_ports
    request = {'signal_fn_name': 'approve'}
    repository.claim_pending_signal_input.return_value = SignalInputClaim(
        'action', request
    )
    if failure == 'consume':
        assets.consume.side_effect = RuntimeError('consume failed')
    else:
        workflow.signal.side_effect = RuntimeError('signal failed')
    if failure == 'rollback':
        assets.rollback.side_effect = RuntimeError('rollback failed')
    with pytest.raises(RuntimeError):
        await service.a_submit_signal_input('instance', 'owner', 'approve', {})
    repository.restore_pending_signal_input.assert_awaited_once_with(
        'instance',
        'owner',
        'approve',
        request,
    )
    repository.append_submitted_signal_input.assert_not_awaited()
    if failure == 'consume':
        workflow.signal.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_delivery_is_not_restored_on_history_failure(service_ports):
    from nomad.actions.domain import SignalInputClaim

    service, repository, workflow, assets = service_ports
    repository.claim_pending_signal_input.return_value = SignalInputClaim('action', {})
    repository.append_submitted_signal_input.side_effect = RuntimeError(
        'database unavailable'
    )
    with pytest.raises(RuntimeError):
        await service.a_submit_signal_input('instance', 'owner', 'approve', {})
    workflow.signal.assert_awaited_once()
    assets.rollback.assert_not_awaited()
    repository.restore_pending_signal_input.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['get_status', 'get_result'])
async def test_reads_require_ownership_before_workflow_access(service_ports, method):
    service, repository, workflow, _ = service_ports
    repository.require_for_user.side_effect = ActionNotFoundError()
    with pytest.raises(ActionNotFoundError):
        await getattr(service, 'a_' + method)('instance', 'other-user')
    getattr(workflow, method).assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('result', [False, 0, {}, [], ''])
async def test_falsey_results_are_persisted(service_ports, result):
    service, repository, workflow, _ = service_ports
    workflow.get_result.return_value = result
    assert await service.a_get_result('instance', 'owner') == result
    repository.save_result_for_user.assert_awaited_once_with(
        'instance',
        'owner',
        'COMPLETED',
        result,
    )


@pytest.mark.asyncio
async def test_refresh_returns_updated_record_and_preserves_empty_result(service_ports):
    from nomad.actions.domain import ActionStatus

    service, repository, workflow, _ = service_ports
    old = SimpleNamespace(
        action_instance_id='instance', user_id='owner', status='RUNNING'
    )
    updated = SimpleNamespace(status='COMPLETED', results={})
    workflow.get_status.return_value = ActionStatus.COMPLETED
    workflow.get_result.return_value = {}
    repository.save_result_for_user.return_value = updated
    assert await service.a_refresh(old) is updated
    repository.save_result_for_user.assert_awaited_once_with(
        'instance', 'owner', 'COMPLETED', {}
    )


def test_payload_list_redacts_nested_secrets():
    from pydantic import SecretStr

    from nomad.actions.serialization import serialize_payload

    assert serialize_payload(
        [SecretStr('hidden'), {'secret': SecretStr('hidden'), 'value': 1}]
    ) == [{'value': 1}]


def test_application_and_adapters_do_not_depend_on_manager():
    import ast
    from pathlib import Path

    from nomad.actions import application

    directory = Path(application.__file__).parent
    for name in (
        'application.py',
        'ports.py',
        'domain.py',
        'workflow_adapter.py',
        'asset_adapter.py',
        'plugin_adapter.py',
    ):
        tree = ast.parse((directory / name).read_text())
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        assert 'nomad.actions.manager' not in imports
        if name in ('application.py', 'ports.py', 'domain.py'):
            forbidden = (
                'temporalio',
                'fastapi',
                'pymongo',
                'beanie',
                'nomad.mongo',
                'nomad.actions.bootstrap',
            )
            assert not any(module.startswith(forbidden) for module in imports)
