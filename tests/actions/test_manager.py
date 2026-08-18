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

import asyncio
from datetime import datetime, timezone
from importlib.metadata import EntryPoint
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field, SecretBytes, SecretStr, field_serializer
from temporalio import workflow
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import Priority

from nomad import infrastructure
from nomad.actions.action import Action
from nomad.actions.assets.models import ACTION_ASSET_REF_TYPE, ActionAssetRef
from nomad.actions.manager import (
    RequestSignalInputActivityInput,
    _async_stop_workflow,
    _get_param_schema,
    _to_dict,
    _validate_with_pydantic,
    get_action_result,
    get_action_result_async,
    get_action_status,
    get_action_status_async,
    get_all_action_schemas,
    list_user_actions,
    request_signal_input_activity,
    start_action,
    start_action_async,
    stop_action,
    stop_action_async,
    submit_signal_input,
    validate_action_arg,
)
from nomad.config import config
from nomad.mongo.action import ActionDocument


class MyActionArgs(BaseModel):
    arg1: str
    arg2: int
    user_id: str | None = None


class MyActionArgsWithAsset(BaseModel):
    user_id: str
    recording: ActionAssetRef


def _recording_asset_ref() -> ActionAssetRef:
    return ActionAssetRef(
        nomad_type=ACTION_ASSET_REF_TYPE,
        filename='recording.webm',
        media_type='audio/webm',
        size=8,
        sha256='abc',
    )


async def my_workflow_run(args: MyActionArgs):
    pass


@pytest.fixture
def mock_action_entry_point():
    mock_action = MagicMock(spec=Action)
    mock_action.name = 'My Action'
    mock_action.description = 'My action description'
    mock_action.task_queue = 'my-task-queue'

    class DummyWorkflow:
        async def run(self, args: MyActionArgs):
            pass

    mock_action.workflow = DummyWorkflow

    mock_entry_point = MagicMock(spec=EntryPoint)
    mock_entry_point.load.return_value = mock_action
    mock_entry_point.name = 'My Action'
    mock_entry_point.description = 'My action description'
    mock_entry_point.task_queue = 'my-task-queue'
    mock_entry_point.groups = None
    mock_entry_point.users = None
    mock_entry_point.plugin_package = 'my-plugin'
    mock_entry_point.priority_key = None
    mock_entry_point.priority_fairness_key = None

    return mock_entry_point


def test_validate_with_pydantic():
    def my_func(args: MyActionArgs):
        pass

    validated = _validate_with_pydantic(my_func, {'arg1': 'test', 'arg2': 123})
    assert isinstance(validated, MyActionArgs)
    assert validated.arg1 == 'test'
    assert validated.arg2 == 123

    with pytest.raises(Exception):
        _validate_with_pydantic(my_func, {'arg1': 'test'})


def test_to_dict():
    class NestedModel(BaseModel):
        secret_val: SecretStr
        normal_val: str = Field(serialization_alias='normalValue')

        @field_serializer('secret_val')
        def serialize_secret(self, value: SecretStr) -> str:
            return value.get_secret_value()

        @field_serializer('normal_val')
        def serialize_normal(self, value: str) -> str:
            return value.upper()

    class TestModel(BaseModel):
        direct_secret: SecretStr
        secret_bytes: SecretBytes
        nested_model: NestedModel
        list_secrets: list[SecretStr]
        list_models: list[NestedModel]

        @field_serializer('direct_secret')
        def serialize_secret(self, value: SecretStr) -> str:
            return value.get_secret_value()

    data = TestModel(
        direct_secret=SecretStr('secret1'),
        secret_bytes=SecretBytes(b'secret-bytes'),
        nested_model=NestedModel(secret_val=SecretStr('secret2'), normal_val='normal'),
        list_secrets=[SecretStr('secret3'), SecretStr('secret4')],
        list_models=[
            NestedModel(secret_val=SecretStr('secret5'), normal_val='normal2')
        ],
    )

    serialized = _to_dict(data)
    assert 'direct_secret' not in serialized
    assert 'secret_bytes' not in serialized
    assert 'secret_val' not in serialized['nested_model']
    assert serialized['nested_model']['normalValue'] == 'NORMAL'
    assert serialized['list_secrets'] == []
    assert 'secret_val' not in serialized['list_models'][0]
    assert serialized['list_models'][0]['normalValue'] == 'NORMAL2'


def test_get_param_schema():
    def my_func(args: MyActionArgs):
        pass

    schema = _get_param_schema(my_func)
    assert 'arg1' in schema['properties']
    assert 'arg2' in schema['properties']


def test_validate_action_arg(monkeypatch, mock_action_entry_point):
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_action_entry_point},
    )

    validated = validate_action_arg('my-action', {'arg1': 'test', 'arg2': 123})
    assert isinstance(validated, MyActionArgs)

    with pytest.raises(ValueError):
        validate_action_arg('nonexistent-action', {})


def test_get_all_action_schemas(monkeypatch, mock_action_entry_point):
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_action_entry_point},
    )
    schemas = get_all_action_schemas()
    assert len(schemas) == 1
    assert schemas[0].action_id == 'my-action'
    assert 'arg1' in schemas[0].json_schema['properties']


def test_get_param_schema_uses_alias_for_action_asset_refs():
    def my_func(args: MyActionArgsWithAsset):
        pass

    schema = _get_param_schema(my_func)
    recording_schema = schema['properties']['recording']
    assert recording_schema['$ref'] == '#/$defs/ActionAssetRef'

    asset_ref_schema = schema['$defs']['ActionAssetRef']
    assert '_nomad_type' in asset_ref_schema['properties']
    assert (
        asset_ref_schema['properties']['_nomad_type']['const'] == ACTION_ASSET_REF_TYPE
    )
    assert '_nomad_type' in asset_ref_schema['required']


@workflow.defn
class RealTemporalWorkflowWithSignal:
    @workflow.run
    async def run(self, args: MyActionArgs):
        pass

    @workflow.signal
    def test_signal(self, signal_arg: int):
        pass


@workflow.defn
class RealTemporalWorkflowWithAliasedSignal:
    @workflow.run
    async def run(self, args: MyActionArgs):
        pass

    @workflow.signal(name='runtime_signal_name')
    def method_signal_name(self, signal_arg: int):
        pass


@workflow.defn
class RealTemporalWorkflowWithAssetSignal:
    @workflow.run
    async def run(self, args: MyActionArgs):
        pass

    @workflow.signal
    def test_signal(self, signal_arg: MyActionArgsWithAsset):
        pass


def test_get_all_action_schemas_temporal_signal(monkeypatch):
    mock_action = MagicMock(spec=Action)
    mock_action.name = 'Temporal Action'
    mock_action.description = 'Temporal action description'
    mock_action.task_queue = 'temporal-task-queue'
    mock_action.workflow = RealTemporalWorkflowWithSignal

    mock_entry_point = MagicMock(spec=EntryPoint)
    mock_entry_point.load.return_value = mock_action
    mock_entry_point.name = 'Temporal Action'
    mock_entry_point.description = 'Temporal action description'
    mock_entry_point.task_queue = 'temporal-task-queue'
    mock_entry_point.groups = None
    mock_entry_point.users = None
    mock_entry_point.plugin_package = 'temporal-plugin'

    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'temporal-action': mock_entry_point},
    )
    schemas = get_all_action_schemas()
    assert len(schemas) == 1
    assert schemas[0].action_id == 'temporal-action'
    assert schemas[0].signals is not None
    assert len(schemas[0].signals) == 1

    signal_schema = schemas[0].signals[0].get('test_signal')
    assert signal_schema is not None
    assert signal_schema.get('type') == 'integer'


def test_get_all_action_schemas_uses_python_method_name_for_signal(monkeypatch):
    mock_action = MagicMock(spec=Action)
    mock_action.name = 'Temporal Action'
    mock_action.description = 'Temporal action description'
    mock_action.task_queue = 'temporal-task-queue'
    mock_action.workflow = RealTemporalWorkflowWithAliasedSignal

    mock_entry_point = MagicMock(spec=EntryPoint)
    mock_entry_point.load.return_value = mock_action
    mock_entry_point.name = 'Temporal Action'
    mock_entry_point.description = 'Temporal action description'
    mock_entry_point.task_queue = 'temporal-task-queue'
    mock_entry_point.groups = None
    mock_entry_point.users = None
    mock_entry_point.plugin_package = 'temporal-plugin'

    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'temporal-action': mock_entry_point},
    )
    schemas = get_all_action_schemas()
    assert len(schemas) == 1
    assert schemas[0].signals is not None
    assert schemas[0].signals[0].get('method_signal_name') is not None
    assert schemas[0].signals[0].get('runtime_signal_name') is None


def test_start_action_sync_facade_returns_value(
    monkeypatch, mongo_function, user1, mock_action_entry_point
):
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_action_entry_point},
    )

    async def mock_async_start_workflow(action, data, workflow_id, priority):
        assert workflow_id.startswith('my-action-')
        return workflow_id

    monkeypatch.setattr(
        'nomad.actions.manager._async_start_workflow',
        mock_async_start_workflow,
    )

    args = MyActionArgs(arg1='test', arg2=123, user_id=user1.user_id)
    action_instance_id = start_action('my-action', args)
    assert action_instance_id.startswith('my-action-')


def test_start_action_sync_facade_passes_priority_and_persists_metadata(
    monkeypatch, mongo_function, user1, mock_action_entry_point
):
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_action_entry_point},
    )
    mock_action_entry_point.priority_key = 2
    mock_action_entry_point.priority_fairness_key = 'user_id'

    async def mock_async_start_workflow(action, data, workflow_id, priority):
        assert workflow_id.startswith('my-action-')
        assert priority == Priority(priority_key=2, fairness_key=user1.user_id)
        return workflow_id

    monkeypatch.setattr(
        'nomad.actions.manager._async_start_workflow',
        mock_async_start_workflow,
    )

    args = MyActionArgs(arg1='test', arg2=123, user_id=user1.user_id)
    action_instance_id = start_action('my-action', args)

    doc = (
        infrastructure.mongo_client.get_database(config.mongo.db_name)
        .get_collection('action_document')
        .find_one({'action_instance_id': action_instance_id})
    )
    assert doc is not None
    assert doc['priority_key'] == 2
    assert doc['priority_fairness_key'] == 'user_id'


@pytest.mark.asyncio
async def test_start_action_sync_facade_works_inside_running_loop(
    monkeypatch, mongo_function, async_mongo_function, user1, mock_action_entry_point
):
    running_loop = asyncio.get_running_loop()

    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_action_entry_point},
    )

    async def mock_async_start_workflow(action, data, workflow_id, priority):
        return workflow_id

    monkeypatch.setattr(
        'nomad.actions.manager._async_start_workflow',
        mock_async_start_workflow,
    )

    args = MyActionArgs(arg1='test', arg2=123, user_id=user1.user_id)
    action_instance_id = start_action('my-action', args)
    assert action_instance_id.startswith('my-action-')
    assert running_loop.is_running()

    doc = await ActionDocument.find_one(
        ActionDocument.action_instance_id == action_instance_id
    )
    assert doc is not None
    assert doc.status == 'PENDING'


@pytest.mark.asyncio
async def test_start_action_sync_facade_propagates_exceptions(
    monkeypatch, mongo_function, user1, mock_action_entry_point
):
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_action_entry_point},
    )

    async def mock_async_start_workflow(action, data, workflow_id, priority):
        raise RuntimeError('boom')

    monkeypatch.setattr(
        'nomad.actions.manager._async_start_workflow',
        mock_async_start_workflow,
    )

    args = MyActionArgs(arg1='test', arg2=123, user_id=user1.user_id)
    with pytest.raises(RuntimeError, match='boom'):
        start_action('my-action', args)


def test_start_action_sync_facade_rejects_asset_refs(
    monkeypatch, mongo_function, user1, mock_action_entry_point
):
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_action_entry_point},
    )

    args = MyActionArgsWithAsset(
        user_id=user1.user_id,
        recording=_recording_asset_ref(),
    )

    with pytest.raises(
        ValueError,
        match='ActionAssetRef inputs are not supported from ELNs',
    ):
        start_action('my-action', args)


@pytest.mark.asyncio
async def test_start_action(
    monkeypatch, mongo_function, async_mongo_function, user1, mock_action_entry_point
):
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_action_entry_point},
    )

    async def mock_async_start_workflow(*args, **kwargs):
        return 'workflow-id-start-test'

    monkeypatch.setattr(
        'nomad.actions.manager._async_start_workflow', mock_async_start_workflow
    )

    args = MyActionArgs(arg1='test', arg2=123, user_id=user1.user_id)
    action_instance_id = await start_action_async('my-action', args)

    assert action_instance_id is not None

    # Verify the document was actually created in the DB
    doc = await ActionDocument.find_one(
        ActionDocument.action_instance_id == action_instance_id
    )
    assert doc is not None
    assert doc.action_id == 'my-action'
    assert doc.status == 'PENDING'


@pytest.mark.asyncio
async def test_start_action_does_not_consume_assets_for_missing_action(
    monkeypatch, mongo_function, async_mongo_function, user1
):
    called = {'count': 0}

    async def mock_consume_staged_assets(**kwargs):
        called['count'] += 1
        return []

    monkeypatch.setattr('nomad.actions.manager.get_actions', lambda: {})
    monkeypatch.setattr(
        'nomad.actions.manager.consume_staged_assets', mock_consume_staged_assets
    )

    args = MyActionArgsWithAsset(
        user_id=user1.user_id,
        recording=_recording_asset_ref(),
    )

    with pytest.raises(AssertionError, match='No action data'):
        await start_action_async('missing-action', args)

    assert called['count'] == 0


@pytest.mark.asyncio
async def test_start_action_consumes_asset_refs(
    monkeypatch, mongo_function, async_mongo_function, user1
):
    class WorkflowWithAsset:
        async def run(self, args: MyActionArgsWithAsset):
            pass

    mock_action = MagicMock(spec=Action)
    mock_action.workflow = WorkflowWithAsset
    mock_action.task_queue = 'my-task-queue'
    mock_entry_point = MagicMock(spec=EntryPoint)
    mock_entry_point.load.return_value = mock_action
    mock_entry_point.priority_key = None
    mock_entry_point.priority_fairness_key = None
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_entry_point},
    )

    called = {'count': 0}

    async def mock_consume_staged_assets(**kwargs):
        called['count'] += 1
        assert kwargs['purpose'].value == 'action_start'
        assert kwargs['action_id'] == 'my-action'
        assert kwargs['user_id'] == user1.user_id

    async def mock_async_start_workflow(*args, **kwargs):
        return 'workflow-id-start-test'

    monkeypatch.setattr(
        'nomad.actions.manager.consume_staged_assets', mock_consume_staged_assets
    )
    monkeypatch.setattr(
        'nomad.actions.manager._async_start_workflow', mock_async_start_workflow
    )

    args = MyActionArgsWithAsset(
        user_id=user1.user_id,
        recording=_recording_asset_ref(),
    )
    await start_action_async('my-action', args)
    assert called['count'] == 1


@pytest.mark.asyncio
async def test_start_action_rolls_back_assets_if_workflow_start_fails(
    monkeypatch, mongo_function, async_mongo_function, user1
):
    class WorkflowWithAsset:
        async def run(self, args: MyActionArgsWithAsset):
            pass

    mock_action = MagicMock(spec=Action)
    mock_action.workflow = WorkflowWithAsset
    mock_action.task_queue = 'my-task-queue'
    mock_entry_point = MagicMock(spec=EntryPoint)
    mock_entry_point.load.return_value = mock_action
    mock_entry_point.priority_key = None
    mock_entry_point.priority_fairness_key = None
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_entry_point},
    )

    rollback_called = {'count': 0}

    async def mock_consume_staged_assets(**kwargs):
        return ['rollback-item']

    async def mock_rollback_consumed_assets(items):
        rollback_called['count'] += 1
        assert items == ['rollback-item']

    async def mock_async_start_workflow(*args, **kwargs):
        raise RuntimeError('temporal start failed')

    monkeypatch.setattr(
        'nomad.actions.manager.consume_staged_assets', mock_consume_staged_assets
    )
    monkeypatch.setattr(
        'nomad.actions.manager.rollback_consumed_assets', mock_rollback_consumed_assets
    )
    monkeypatch.setattr(
        'nomad.actions.manager._async_start_workflow', mock_async_start_workflow
    )

    args = MyActionArgsWithAsset(
        user_id=user1.user_id,
        recording=_recording_asset_ref(),
    )
    with pytest.raises(RuntimeError, match='temporal start failed'):
        await start_action_async('my-action', args)
    assert rollback_called['count'] == 1


def test_get_action_status_sync_facade_returns_value(
    monkeypatch, mongo_function, user1
):
    (
        infrastructure.mongo_client.get_database(config.mongo.db_name)
        .get_collection('action_document')
        .insert_one(
            {
                'action_id': 'my-action',
                'action_instance_id': 'workflow-1',
                'user_id': user1.user_id,
                'status': 'PENDING',
                'input_data': {},
                'created_at': datetime.now(timezone.utc),
                'updated_at': datetime.now(timezone.utc),
            }
        )
    )

    async def mock_get_workflow_status_safe(action_instance_id):
        assert action_instance_id == 'workflow-1'
        return WorkflowExecutionStatus.RUNNING

    monkeypatch.setattr(
        'nomad.actions.manager._get_workflow_status_safe',
        mock_get_workflow_status_safe,
    )

    status = get_action_status('workflow-1', user1.user_id)
    assert status == WorkflowExecutionStatus.RUNNING
    assert status.name == 'RUNNING'


def test_stop_action_sync_facade_cancels_workflow(monkeypatch, mongo_function, user1):
    (
        infrastructure.mongo_client.get_database(config.mongo.db_name)
        .get_collection('action_document')
        .insert_one(
            {
                'action_id': 'my-action',
                'action_instance_id': 'workflow-1',
                'user_id': user1.user_id,
                'status': 'RUNNING',
                'input_data': {},
                'created_at': datetime.now(timezone.utc),
                'updated_at': datetime.now(timezone.utc),
            }
        )
    )

    async def mock_async_stop_workflow(action_instance_id):
        assert action_instance_id == 'workflow-1'

    monkeypatch.setattr(
        'nomad.actions.manager._async_stop_workflow',
        mock_async_stop_workflow,
    )

    assert stop_action('workflow-1', user1.user_id) is None

    doc = (
        infrastructure.mongo_client.get_database(config.mongo.db_name)
        .get_collection('action_document')
        .find_one({'action_instance_id': 'workflow-1'})
    )
    assert doc is not None
    assert doc['status'] == WorkflowExecutionStatus.CANCELED.name


@pytest.mark.asyncio
async def test_stop_action_async_cancels_workflow(
    monkeypatch, mongo_function, async_mongo_function, user1
):
    action_doc = ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-async-stop',
        user_id=user1.user_id,
        status='RUNNING',
        input_data={},
    )
    await action_doc.insert()

    async def mock_async_stop_workflow(action_instance_id):
        assert action_instance_id == 'workflow-async-stop'

    monkeypatch.setattr(
        'nomad.actions.manager._async_stop_workflow',
        mock_async_stop_workflow,
    )

    assert await stop_action_async('workflow-async-stop', user1.user_id) is None

    updated_doc = await ActionDocument.find_one(
        ActionDocument.action_instance_id == 'workflow-async-stop'
    )
    assert updated_doc is not None
    assert updated_doc.status == WorkflowExecutionStatus.CANCELED.name


def test_get_action_result_sync_facade_returns_value(
    monkeypatch, mongo_function, user1
):
    class WorkflowResult(BaseModel):
        result: str
        secret: SecretStr

        @field_serializer('secret')
        def serialize_secret(self, value: SecretStr) -> str:
            return value.get_secret_value()

    (
        infrastructure.mongo_client.get_database(config.mongo.db_name)
        .get_collection('action_document')
        .insert_one(
            {
                'action_id': 'my-action',
                'action_instance_id': 'workflow-1',
                'user_id': user1.user_id,
                'status': 'RUNNING',
                'input_data': {},
                'created_at': datetime.now(timezone.utc),
                'updated_at': datetime.now(timezone.utc),
            }
        )
    )

    async def mock_get_workflow_result_safe(action_instance_id):
        assert action_instance_id == 'workflow-1'
        return WorkflowResult(result='success', secret=SecretStr('secret'))

    monkeypatch.setattr(
        'nomad.actions.manager._get_workflow_result_safe',
        mock_get_workflow_result_safe,
    )

    result = get_action_result('workflow-1', user1.user_id)
    assert result == {'result': 'success'}


@pytest.mark.asyncio
async def test_start_action_async_passes_priority_and_persists_metadata(
    monkeypatch, mongo_function, async_mongo_function, user1, mock_action_entry_point
):
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_action_entry_point},
    )
    mock_action_entry_point.priority_key = 4
    mock_action_entry_point.priority_fairness_key = 'user_id'

    async def mock_async_start_workflow(action, data, workflow_id, priority):
        assert workflow_id.startswith('my-action-')
        assert priority == Priority(priority_key=4, fairness_key=user1.user_id)
        return workflow_id

    monkeypatch.setattr(
        'nomad.actions.manager._async_start_workflow', mock_async_start_workflow
    )

    args = MyActionArgs(arg1='test', arg2=123, user_id=user1.user_id)
    action_instance_id = await start_action_async('my-action', args)

    doc = await ActionDocument.find_one(
        ActionDocument.action_instance_id == action_instance_id
    )
    assert doc is not None
    assert doc.priority_key == 4
    assert doc.priority_fairness_key == 'user_id'


@pytest.mark.asyncio
async def test_async_stop_workflow_uses_temporal_cancel(monkeypatch):
    mock_client = MagicMock()
    mock_handle = MagicMock()
    mock_handle.cancel = AsyncMock()
    mock_client.get_workflow_handle.return_value = mock_handle

    async def get_client():
        return mock_client

    monkeypatch.setattr('nomad.actions.manager.get_client', get_client)

    await _async_stop_workflow('workflow-cancel')

    mock_client.get_workflow_handle.assert_called_once_with('workflow-cancel')
    mock_handle.cancel.assert_awaited_once_with()


@pytest.fixture
def mock_temporal_client(monkeypatch):
    mock_client = MagicMock()
    mock_handle = MagicMock()

    async def mock_describe(*args, **kwargs):
        mock_status = MagicMock()
        mock_status.status = WorkflowExecutionStatus.RUNNING
        return mock_status

    async def mock_result(*args, **kwargs):
        return {'result': 'success'}

    mock_handle.describe = mock_describe
    mock_handle.result = mock_result

    mock_client.get_workflow_handle.return_value = mock_handle

    async def get_client():
        return mock_client

    monkeypatch.setattr('nomad.actions.manager.get_client', get_client)
    return mock_client


@pytest.mark.asyncio
async def test_get_action_status(
    mongo_function, async_mongo_function, user1, mock_temporal_client
):
    action_doc = ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-123',
        user_id=user1.user_id,
        status='PENDING',
        input_data={},
    )
    await action_doc.insert()

    status = await get_action_status_async('workflow-123', user1.user_id)
    assert status == WorkflowExecutionStatus.RUNNING

    # Verify the status was updated in the DB
    updated_doc = await ActionDocument.find_one(
        ActionDocument.action_instance_id == 'workflow-123'
    )
    assert updated_doc.status == 'RUNNING'

    with pytest.raises(Exception):
        await get_action_status_async('nonexistent-workflow', user1.user_id)


@pytest.mark.asyncio
async def test_get_action_status_marks_unknown_when_workflow_is_missing(
    monkeypatch, mongo_function, async_mongo_function, user1
):
    action_doc = ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-missing',
        user_id=user1.user_id,
        status='RUNNING',
        input_data={},
    )
    await action_doc.insert()

    async def mock_get_workflow_status_safe(action_instance_id):
        return None

    monkeypatch.setattr(
        'nomad.actions.manager._get_workflow_status_safe',
        mock_get_workflow_status_safe,
    )

    status = await get_action_status_async('workflow-missing', user1.user_id)
    assert status == WorkflowExecutionStatus.TERMINATED

    updated_doc = await ActionDocument.find_one(
        ActionDocument.action_instance_id == 'workflow-missing'
    )
    assert updated_doc.status == 'TERMINATED'


@pytest.mark.asyncio
async def test_get_action_result(
    mongo_function, async_mongo_function, user1, mock_temporal_client
):
    action_doc = ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-123',
        user_id=user1.user_id,
        status='RUNNING',
        input_data={},
    )
    await action_doc.insert()

    result = await get_action_result_async('workflow-123', user1.user_id)
    assert result == {'result': 'success'}

    # Verify results were saved to DB
    updated_doc = await ActionDocument.find_one(
        ActionDocument.action_instance_id == 'workflow-123'
    )
    assert updated_doc.status == 'COMPLETED'
    assert updated_doc.results == {'result': 'success'}

    with pytest.raises(Exception):
        await get_action_result_async('nonexistent-workflow', user1.user_id)


@pytest.mark.asyncio
async def test_list_user_actions(
    monkeypatch, mongo_function, async_mongo_function, user1
):
    await ActionDocument(
        action_id='my-action-1',
        action_instance_id='workflow-1',
        user_id=user1.user_id,
        status='PENDING',
        input_data={},
    ).insert()
    await ActionDocument(
        action_id='my-action-2',
        action_instance_id='workflow-2',
        user_id=user1.user_id,
        status='COMPLETED',
        input_data={},
    ).insert()

    async def mock_update_status(action):
        pass

    monkeypatch.setattr(
        'nomad.actions.manager._refresh_action_status', mock_update_status
    )

    actions_page = await list_user_actions(user1.user_id)
    assert len(actions_page.items) == 2

    # Test no actions for user
    actions_page = await list_user_actions('other-user')
    assert len(actions_page.items) == 0


@pytest.mark.asyncio
async def test_submit_signal_input_requires_pending_request(
    monkeypatch, mongo_function, async_mongo_function, user1
):
    action_doc = ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-submit-1',
        user_id=user1.user_id,
        status='RUNNING',
        input_data={},
    )
    await action_doc.insert()

    mock_action = MagicMock(spec=Action)
    mock_action.workflow = RealTemporalWorkflowWithSignal
    mock_entry_point = MagicMock(spec=EntryPoint)
    mock_entry_point.load.return_value = mock_action

    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_entry_point},
    )

    with pytest.raises(Exception, match='No pending signal input request found'):
        await submit_signal_input(
            action_instance_id='workflow-submit-1',
            user_id=user1.user_id,
            signal_fn_name='test_signal',
            data=1,
        )


@pytest.mark.asyncio
async def test_submit_signal_input_clears_pending_request(
    monkeypatch, mongo_function, async_mongo_function, user1
):
    action_doc = ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-submit-2',
        user_id=user1.user_id,
        status='RUNNING',
        input_data={},
        signal_input_requests=[
            {'signal_fn_name': 'test_signal', 'title': 'x', 'content': 'hello'}
        ],
    )
    await action_doc.insert()

    mock_action = MagicMock(spec=Action)
    mock_action.workflow = RealTemporalWorkflowWithSignal
    mock_entry_point = MagicMock(spec=EntryPoint)
    mock_entry_point.load.return_value = mock_action

    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_entry_point},
    )

    async def mock_signal_workflow(*args, **kwargs):
        return None

    monkeypatch.setattr(
        'nomad.actions.manager._async_signal_workflow', mock_signal_workflow
    )

    await submit_signal_input(
        action_instance_id='workflow-submit-2',
        user_id=user1.user_id,
        signal_fn_name='test_signal',
        data=1,
    )

    # Re-fetch from DB instead of .reload() (Beanie has no .reload())
    updated_doc = await ActionDocument.find_one(
        ActionDocument.action_instance_id == 'workflow-submit-2'
    )
    assert updated_doc.signal_input_requests == []
    assert len(updated_doc.signal_inputs_submitted) == 1
    submitted_input = updated_doc.signal_inputs_submitted[0]
    assert submitted_input['signal_fn_name'] == 'test_signal'
    assert submitted_input['data'] == 1
    assert submitted_input['title'] == 'x'
    assert submitted_input['content'] == 'hello'
    assert 'timestamp' in submitted_input


@pytest.mark.asyncio
async def test_submit_signal_input_rolls_back_assets_if_signal_fails(
    monkeypatch, mongo_function, async_mongo_function, user1
):
    action_doc = ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-submit-4',
        user_id=user1.user_id,
        status='RUNNING',
        input_data={},
        signal_input_requests=[{'signal_fn_name': 'test_signal'}],
    )
    await action_doc.insert()

    mock_action = MagicMock(spec=Action)
    mock_action.workflow = RealTemporalWorkflowWithAssetSignal
    mock_entry_point = MagicMock(spec=EntryPoint)
    mock_entry_point.load.return_value = mock_action
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_entry_point},
    )

    async def mock_consume_staged_assets(**kwargs):
        return ['rollback-item']

    rollback_called = {'count': 0}

    async def mock_rollback_consumed_assets(items):
        rollback_called['count'] += 1
        assert items == ['rollback-item']

    async def mock_signal_workflow(*args, **kwargs):
        raise RuntimeError('signal send failed')

    monkeypatch.setattr(
        'nomad.actions.manager.consume_staged_assets', mock_consume_staged_assets
    )
    monkeypatch.setattr(
        'nomad.actions.manager.rollback_consumed_assets', mock_rollback_consumed_assets
    )
    monkeypatch.setattr(
        'nomad.actions.manager._async_signal_workflow', mock_signal_workflow
    )

    with pytest.raises(RuntimeError, match='signal send failed'):
        await submit_signal_input(
            action_instance_id='workflow-submit-4',
            user_id=user1.user_id,
            signal_fn_name='test_signal',
            data={
                'user_id': user1.user_id,
                'recording': {
                    '_nomad_type': ACTION_ASSET_REF_TYPE,
                    'filename': 'recording.webm',
                    'media_type': 'audio/webm',
                    'size': 8,
                },
            },
        )
    assert rollback_called['count'] == 1


@pytest.mark.asyncio
async def test_submit_signal_input_restores_pending_request_if_asset_consume_fails(
    monkeypatch, mongo_function, async_mongo_function, user1
):
    action_doc = ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-submit-asset-fail',
        user_id=user1.user_id,
        status='RUNNING',
        input_data={},
        signal_input_requests=[{'signal_fn_name': 'test_signal', 'title': 'x'}],
    )
    await action_doc.insert()

    mock_action = MagicMock(spec=Action)
    mock_action.workflow = RealTemporalWorkflowWithAssetSignal
    mock_entry_point = MagicMock(spec=EntryPoint)
    mock_entry_point.load.return_value = mock_action
    monkeypatch.setattr(
        'nomad.actions.manager.get_actions',
        lambda: {'my-action': mock_entry_point},
    )

    async def mock_consume_staged_assets(**kwargs):
        raise ValueError('asset missing')

    async def mock_signal_workflow(*args, **kwargs):
        raise AssertionError('signal should not be sent')

    monkeypatch.setattr(
        'nomad.actions.manager.consume_staged_assets', mock_consume_staged_assets
    )
    monkeypatch.setattr(
        'nomad.actions.manager._async_signal_workflow', mock_signal_workflow
    )

    with pytest.raises(ValueError, match='asset missing'):
        await submit_signal_input(
            action_instance_id='workflow-submit-asset-fail',
            user_id=user1.user_id,
            signal_fn_name='test_signal',
            data={
                'user_id': user1.user_id,
                'recording': {
                    '_nomad_type': ACTION_ASSET_REF_TYPE,
                    'filename': 'recording.webm',
                    'media_type': 'audio/webm',
                    'size': 8,
                },
            },
        )

    restored = await ActionDocument.find_one(
        ActionDocument.action_instance_id == 'workflow-submit-asset-fail'
    )
    assert restored is not None
    assert restored.signal_input_requests == [
        {'signal_fn_name': 'test_signal', 'title': 'x'}
    ]


@pytest.mark.asyncio
async def test_request_signal_input_activity_appends_without_replacing_existing_requests(
    mongo_function, async_mongo_function, user1
):
    action_doc = ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-request-1',
        user_id=user1.user_id,
        status='RUNNING',
        input_data={},
        signal_input_requests=[{'signal_fn_name': 'existing_signal'}],
    )
    await action_doc.insert()

    result = await request_signal_input_activity(
        RequestSignalInputActivityInput(
            action_instance_id='workflow-request-1',
            user_id=user1.user_id,
            signal_fn_name='new_signal',
            title='Need input',
        )
    )

    assert result == {'status': 'signal_input_requested'}
    updated_doc = await ActionDocument.find_one(
        ActionDocument.action_instance_id == 'workflow-request-1'
    )
    assert updated_doc is not None
    assert updated_doc.signal_input_requests == [
        {'signal_fn_name': 'existing_signal'},
        {'signal_fn_name': 'new_signal', 'title': 'Need input'},
    ]
