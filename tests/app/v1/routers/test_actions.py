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

import os
import uuid
from datetime import datetime
from unittest.mock import MagicMock, PropertyMock

import pytest
import pytest_asyncio
from httpx2 import AsyncClient

from nomad.config import config
from nomad.mongo.action import ActionDocument


async def _noop_update_status(action):
    return action


@pytest.fixture
def client(async_api_v1: AsyncClient) -> AsyncClient:
    return async_api_v1


@pytest_asyncio.fixture
async def saved_action_document(mongo_function, async_mongo_function, user1):
    action_instance_id = f'workflow-{uuid.uuid4().hex}'
    action = ActionDocument(
        action_id='my-action',
        action_instance_id=action_instance_id,
        status='RUNNING',
        user_id=user1.user_id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        input_data={},
        results={},
    )
    await action.insert()
    return action


@pytest.mark.asyncio
async def test_action_start(
    client: AsyncClient, auth_headers, async_mongo_function, monkeypatch
):
    mock_action = MagicMock()
    mock_action.users = None
    mock_action.groups = None
    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.get_actions',
        lambda: {'my-action': mock_action},
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.validate_action_arg', lambda action_id, data: data
    )

    async def mock_start_action(action_id, data):
        return 'workflow-123'

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_start',
        mock_start_action,
    )

    response = await client.post(
        '/actions/my-action/start',
        json={'data': {'arg1': 'test'}},
        headers=auth_headers['user1'],
    )

    assert response.status_code == 200
    assert response.json() == {'action_instance_id': 'workflow-123'}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'allowed_users, allowed_groups, expected_status',
    [
        (['00000000-0000-0000-0000-000000000001'], None, 200),
        (['00000000-0000-0000-0000-000000000002'], None, 403),
        (None, ['group1'], 200),
        (None, ['group2'], 403),
        (['00000000-0000-0000-0000-000000000001'], ['group2'], 200),
        (['00000000-0000-0000-0000-000000000002'], ['group1'], 200),
        (['00000000-0000-0000-0000-000000000002'], ['group2'], 403),
    ],
)
async def test_action_start_authorization(
    client: AsyncClient,
    auth_headers,
    async_mongo_function,
    monkeypatch,
    user1,
    allowed_users,
    allowed_groups,
    expected_status,
):
    mock_action = MagicMock()
    mock_action.users = allowed_users
    mock_action.groups = allowed_groups

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.get_actions',
        lambda: {'protected-action': mock_action},
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.validate_action_arg',
        lambda action_id, data: data,
    )

    async def mock_start_action(action_id, data):
        return 'workflow-123'

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_start',
        mock_start_action,
    )
    monkeypatch.setattr(
        'nomad.mongo.groups.MongoUserGroup.get_ids_by_user_id',
        lambda user_id: ['group1'] if user_id == user1.user_id else [],
    )

    response = await client.post(
        '/actions/protected-action/start',
        json={'data': {}},
        headers=auth_headers['user1'],
    )

    assert response.status_code == expected_status
    if expected_status == 403:
        assert (
            response.json()['detail']
            == 'User is not authorized to execute this action.'
        )


@pytest.mark.asyncio
async def test_action_status(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    mock_status = MagicMock()
    type(mock_status).name = PropertyMock(return_value='RUNNING')

    async def mock_get_action_status(action_instance_id, user_id):
        return mock_status

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_get_status',
        mock_get_action_status,
    )
    response = await client.get(
        f'/actions/{saved_action_document.action_instance_id}/status',
        headers=auth_headers['user1'],
    )
    assert response.status_code == 200
    assert response.json() == {'status': 'RUNNING'}


@pytest.mark.asyncio
async def test_action_result(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    async def mock_get_action_result(action_instance_id, user_id):
        return {'result': 'success'}

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_get_result',
        mock_get_action_result,
    )
    response = await client.get(
        f'/actions/{saved_action_document.action_instance_id}/result',
        headers=auth_headers['user1'],
    )
    assert response.status_code == 200
    assert response.json() == {'result': 'success'}


@pytest.mark.asyncio
async def test_action_input_schemas(
    client: AsyncClient, auth_headers, async_mongo_function, monkeypatch, fastapi_cache
):
    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.get_all_action_schemas',
        lambda: [{'action_id': 'my-action', 'json_schema': {}}],
    )
    response = await client.get('/actions/schemas', headers=auth_headers['user1'])
    assert response.status_code == 200
    assert response.json()[0]['action_id'] == 'my-action'


@pytest.mark.asyncio
async def test_actions_list(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    async def mock_update_status(action):
        return action

    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', mock_update_status
    )
    response = await client.get('/actions', headers=auth_headers['user1'])
    assert response.status_code == 200
    response_json = response.json()
    # Response is now an ActionPage object, not a plain list.
    assert 'items' in response_json
    assert 'total' in response_json
    assert 'next_cursor' in response_json
    assert response_json['items'][0]['action_id'] == saved_action_document.action_id
    assert 'results' not in response_json['items'][0]
    assert 'input_data' not in response_json['items'][0]


@pytest.mark.asyncio
async def test_get_action(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    async def mock_update_status(action):
        return action

    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', mock_update_status
    )
    response = await client.get(
        f'/actions/{saved_action_document.action_instance_id}',
        headers=auth_headers['user1'],
    )
    assert response.status_code == 200
    response_json = response.json()
    assert response_json['action_id'] == saved_action_document.action_id
    assert (
        response_json['action_instance_id'] == saved_action_document.action_instance_id
    )
    assert response_json['status'] == saved_action_document.status


@pytest.mark.asyncio
async def test_get_action_not_found(
    client: AsyncClient, auth_headers, async_mongo_function, mongo_function
):
    response = await client.get('/actions/workflow-2', headers=auth_headers['user1'])
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_action_stop(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    async def mock_stop_action(action_instance_id, user_id):
        return None

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_stop',
        mock_stop_action,
    )
    response = await client.post(
        f'/actions/{saved_action_document.action_instance_id}/stop',
        headers=auth_headers['user1'],
    )
    assert response.status_code == 200
    assert response.json() == {'status': 'stopped'}


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['stop', 'signal-input'])
@pytest.mark.parametrize('missing', [True, False])
async def test_action_domain_error_response(
    client, auth_headers, monkeypatch, endpoint, missing
):
    from nomad.actions.domain import ActionNotFoundError, ActionNotRunningError

    async def fail(**kwargs):
        # Deliberately do not use the legacy message strings.
        raise (
            ActionNotFoundError('missing')
            if missing
            else ActionNotRunningError('inactive')
        )

    function = 'stop' if endpoint == 'stop' else 'submit_signal_input'
    monkeypatch.setattr(
        f'nomad.app.v1.routers.actions.action_service.a_{function}', fail
    )
    response = await client.post(
        f'/actions/workflow/{endpoint}',
        headers=auth_headers['user1'],
        json={'signal_fn_name': 'approve', 'data': {}},
    )
    assert response.status_code == (404 if missing else 409)


@pytest.mark.asyncio
async def test_action_signal_input_submit(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    async def mock_submit_signal_input(
        action_instance_id, user_id, signal_fn_name, data
    ):
        return None

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_submit_signal_input',
        mock_submit_signal_input,
    )
    response = await client.post(
        f'/actions/{saved_action_document.action_instance_id}/signal-input',
        json={'signal_fn_name': 'my_signal', 'data': {'foo': 'bar'}},
        headers=auth_headers['user1'],
    )
    assert response.status_code == 200
    assert response.json() == {'status': 'signal_input_submitted'}


@pytest.mark.asyncio
async def test_action_signal_input_submit_error(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    async def mock_submit_signal_input_raise(
        action_instance_id, user_id, signal_fn_name, data
    ):
        from nomad.actions.domain import SignalInputNotFoundError

        raise SignalInputNotFoundError(
            'No pending signal input request found for signal'
        )

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_submit_signal_input',
        mock_submit_signal_input_raise,
    )
    response = await client.post(
        f'/actions/{saved_action_document.action_instance_id}/signal-input',
        json={'signal_fn_name': 'my_signal', 'data': {'foo': 'bar'}},
        headers=auth_headers['user1'],
    )
    assert response.status_code == 404
    assert 'No pending signal input request' in response.json()['detail']


@pytest.mark.asyncio
async def test_action_logs(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch, tmp_path
):
    # Mock get_user_action so authorization passes (and bypasses Temporal fetching)
    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', _noop_update_status
    )

    # Setup dummy log file
    log_dir = os.path.join(
        config.fs.actions,
        saved_action_document.action_instance_id,
        'nomad_system',
        'logs',
    )
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'{saved_action_document.action_instance_id}.log')
    with open(log_file, 'w') as f:
        f.write('Test log line 1\nTest log line 2\n')

    try:
        response = await client.get(
            f'/actions/{saved_action_document.action_instance_id}/logs',
            headers=auth_headers['user1'],
        )
        assert response.status_code == 200
        assert response.text == 'Test log line 1\nTest log line 2\n'
        assert response.headers['content-type'] == 'text/plain; charset=utf-8'
        assert response.headers['x-log-first-line'] == '1'
    finally:
        if os.path.exists(log_file):
            os.remove(log_file)


@pytest.mark.asyncio
async def test_action_logs_not_found(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    # Mock get_user_action so authorization passes
    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', _noop_update_status
    )

    response = await client.get(
        f'/actions/{saved_action_document.action_instance_id}/logs',
        headers=auth_headers['user1'],
    )
    assert response.status_code == 404
    assert response.json()['detail'] == 'Log file not found for this action.'


@pytest.mark.asyncio
async def test_action_logs_truncate(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    # Mock get_user_action so authorization passes
    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', _noop_update_status
    )

    # Setup dummy log file
    log_dir = os.path.join(
        config.fs.actions,
        saved_action_document.action_instance_id,
        'nomad_system',
        'logs',
    )
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'{saved_action_document.action_instance_id}.log')

    # Create a file slightly larger than 2MB
    chunk = b'A' * 1024 * 1024
    with open(log_file, 'wb') as f:
        f.write(chunk)
        f.write(chunk)
        f.write(b'B' * 10)

    try:
        response = await client.get(
            f'/actions/{saved_action_document.action_instance_id}/logs',
            headers=auth_headers['user1'],
        )
        assert response.status_code == 200
        assert len(response.content) == 2 * 1024 * 1024
        assert response.content.endswith(b'B' * 10)
        assert int(response.headers['x-log-first-line']) >= 1
    finally:
        if os.path.exists(log_file):
            os.remove(log_file)


@pytest.mark.asyncio
async def test_action_logs_stream(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    # Mock get_user_action so authorization passes
    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', _noop_update_status
    )

    # Setup dummy log file
    log_dir = os.path.join(
        config.fs.actions,
        saved_action_document.action_instance_id,
        'nomad_system',
        'logs',
    )
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'{saved_action_document.action_instance_id}.log')

    with open(log_file, 'w') as f:
        f.write('Initial line 1\nInitial line 2\n')

    status_calls = 0

    async def mock_status(*args, **kwargs):
        nonlocal status_calls
        status_calls += 1
        if status_calls == 1:
            with open(log_file, 'a') as f:
                f.write('New streaming line 1\nNew streaming line 2\n')

        mock_obj = MagicMock()
        type(mock_obj).name = PropertyMock(
            return_value='RUNNING' if status_calls <= 2 else 'SUCCESS'
        )
        return mock_obj

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_get_status', mock_status
    )

    try:
        response = await client.get(
            f'/actions/{saved_action_document.action_instance_id}/logs?stream=true',
            headers=auth_headers['user1'],
        )
        assert response.status_code == 200
        assert response.headers['content-type'] == 'text/event-stream; charset=utf-8'
        assert response.headers['x-log-first-line'] == '3'

        # Since it streams from the end, the initial lines shouldn't be there.
        assert 'Initial line' not in response.text
        assert 'New streaming line 1' in response.text
        assert 'New streaming line 2' in response.text
    finally:
        if os.path.exists(log_file):
            os.remove(log_file)


@pytest.mark.asyncio
async def test_action_logs_stream_with_tail_offset(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', _noop_update_status
    )

    log_dir = os.path.join(
        config.fs.actions,
        saved_action_document.action_instance_id,
        'nomad_system',
        'logs',
    )
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'{saved_action_document.action_instance_id}.log')

    with open(log_file, 'w') as f:
        f.write('line 1\nline 2\nline 3\nline 4\n')

    mock_status = MagicMock()
    type(mock_status).name = PropertyMock(return_value='SUCCESS')

    async def mock_status_fn(*_args, **_kwargs):
        return mock_status

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_get_status',
        mock_status_fn,
    )

    try:
        response = await client.get(
            f'/actions/{saved_action_document.action_instance_id}/logs?stream=true&offset_lines=-2',
            headers=auth_headers['user1'],
        )
        assert response.status_code == 200
        assert response.headers['x-log-first-line'] == '3'
        assert response.text.endswith('line 3\nline 4\n')
    finally:
        if os.path.exists(log_file):
            os.remove(log_file)


@pytest.mark.asyncio
async def test_action_logs_stream_with_large_positive_offset_clamped(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', _noop_update_status
    )

    log_dir = os.path.join(
        config.fs.actions,
        saved_action_document.action_instance_id,
        'nomad_system',
        'logs',
    )
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'{saved_action_document.action_instance_id}.log')

    with open(log_file, 'w') as f:
        f.write('line 1\nline 2\n')

    status_calls = 0

    async def mock_status(*args, **kwargs):
        nonlocal status_calls
        status_calls += 1
        if status_calls == 1:
            with open(log_file, 'a') as f:
                f.write('line 3\n')

        mock_obj = MagicMock()
        type(mock_obj).name = PropertyMock(
            return_value='RUNNING' if status_calls <= 1 else 'SUCCESS'
        )
        return mock_obj

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_get_status', mock_status
    )

    try:
        response = await client.get(
            f'/actions/{saved_action_document.action_instance_id}/logs?stream=true&offset_lines=100',
            headers=auth_headers['user1'],
        )
        assert response.status_code == 200
        assert response.headers['x-log-first-line'] == '3'
        assert response.text == 'line 3\n'
    finally:
        if os.path.exists(log_file):
            os.remove(log_file)


@pytest.mark.asyncio
async def test_action_logs_stream_with_large_negative_offset_returns_full_available_log(
    client: AsyncClient, auth_headers, saved_action_document, monkeypatch
):
    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', _noop_update_status
    )

    log_dir = os.path.join(
        config.fs.actions,
        saved_action_document.action_instance_id,
        'nomad_system',
        'logs',
    )
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'{saved_action_document.action_instance_id}.log')

    with open(log_file, 'w') as f:
        f.write('Initial line 1\nInitial line 2\n')

    status_calls = 0

    async def mock_status(*args, **kwargs):
        nonlocal status_calls
        status_calls += 1
        if status_calls == 1:
            with open(log_file, 'a') as f:
                f.write('New streaming line 1\n')

        mock_obj = MagicMock()
        type(mock_obj).name = PropertyMock(
            return_value='RUNNING' if status_calls <= 2 else 'SUCCESS'
        )
        return mock_obj

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_get_status', mock_status
    )

    try:
        response = await client.get(
            f'/actions/{saved_action_document.action_instance_id}/logs?stream=true&offset_lines=-2000',
            headers=auth_headers['user1'],
        )
        assert response.status_code == 200
        assert response.headers['x-log-first-line'] == '1'
        assert '[... earlier log lines truncated:' not in response.text
        assert 'Initial line 1' in response.text
        assert 'Initial line 2' in response.text
        assert 'New streaming line 1' in response.text
    finally:
        if os.path.exists(log_file):
            os.remove(log_file)


@pytest.mark.parametrize(
    'method, endpoint',
    [
        ('GET', '/actions/workflow-1/status'),
        ('POST', '/actions/workflow-1/stop'),
        ('GET', '/actions/workflow-1/result'),
        ('GET', '/actions/workflow-1'),
        ('GET', '/actions/workflow-1/logs'),
    ],
)
@pytest.mark.asyncio
async def test_action_endpoints_unauthorized(
    client: AsyncClient, async_mongo_function, method: str, endpoint: str
):
    response = await client.request(method, endpoint)
    assert response.status_code == 401


async def mock_get_action_status_raise(*args, **kwargs):
    raise Exception('Action status not found')


async def mock_stop_action_raise(*args, **kwargs):
    raise Exception(
        'The action was not registered in the DB or was registered under a different user.'
    )


async def mock_get_action_result_raise(*args, **kwargs):
    raise Exception('Action result not found.')


@pytest.mark.parametrize(
    'method, endpoint, mock_function_name, mock_function, expected_status_code',
    [
        (
            'GET',
            '/actions/workflow-1/status',
            'nomad.app.v1.routers.actions.action_service.a_get_status',
            mock_get_action_status_raise,
            500,
        ),
        (
            'POST',
            '/actions/workflow-1/stop',
            'nomad.app.v1.routers.actions.action_service.a_stop',
            mock_stop_action_raise,
            500,
        ),
        (
            'GET',
            '/actions/workflow-1/result',
            'nomad.app.v1.routers.actions.action_service.a_get_result',
            mock_get_action_result_raise,
            500,
        ),
        ('GET', '/actions/workflow-1', None, None, 404),
        ('GET', '/actions/workflow-1/logs', None, None, 404),
    ],
)
@pytest.mark.asyncio
async def test_action_endpoints_wrong_user(
    client: AsyncClient,
    auth_headers,
    monkeypatch,
    method: str,
    endpoint: str,
    mock_function_name: str,
    mock_function,
    expected_status_code: int,
    saved_action_document,
):
    if mock_function_name:
        monkeypatch.setattr(mock_function_name, mock_function)
    response = await client.request(method, endpoint, headers=auth_headers['user2'])
    assert response.status_code == expected_status_code


# Test for GET /actions not containing other users' actions
@pytest.mark.asyncio
async def test_actions_list_does_not_contain_other_users_actions(
    client: AsyncClient,
    auth_headers,
    mongo_function,
    async_mongo_function,
    user1,
    user2,
    monkeypatch,
):
    async def mock_update_status(action):
        return action

    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', mock_update_status
    )

    # user1 has one action, user2 has another
    await ActionDocument(
        action_id='action1',
        action_instance_id='wf1',
        status='RUNNING',
        created_at=datetime.now(),
        updated_at=datetime.now(),
        user_id=user1.user_id,
        input_data={},
        results={},
    ).insert()
    await ActionDocument(
        action_id='action2',
        action_instance_id='wf2',
        status='RUNNING',
        created_at=datetime.now(),
        updated_at=datetime.now(),
        user_id=user2.user_id,
        input_data={},
        results={},
    ).insert()

    # request as user1
    response = await client.get('/actions', headers=auth_headers['user1'])
    assert response.status_code == 200
    response_json = response.json()
    assert response_json['total'] == 1
    assert len(response_json['items']) == 1
    assert response_json['items'][0]['action_id'] == 'action1'

    # request as user2
    response = await client.get('/actions', headers=auth_headers['user2'])
    assert response.status_code == 200
    response_json = response.json()
    assert response_json['total'] == 1
    assert len(response_json['items']) == 1
    assert response_json['items'][0]['action_id'] == 'action2'


# Pagination-specific tests
async def _insert_actions(user, n: int, base_id: str = 'wf') -> list[ActionDocument]:
    """Helper: insert *n* action documents for *user*, oldest first."""
    from datetime import timedelta

    docs = []
    for i in range(n):
        doc = ActionDocument(
            action_id=f'action-{i}',
            action_instance_id=f'{base_id}-{i}',
            status='COMPLETED',
            created_at=datetime(2024, 1, 1, tzinfo=__import__('datetime').timezone.utc)
            + timedelta(seconds=i),
            updated_at=datetime.now(),
            user_id=user.user_id,
            input_data={},
            results={},
        )
        await doc.insert()
        docs.append(doc)
    return docs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'target_page, expected_length, expects_next_cursor, expected_first_action',
    [
        (1, 3, True, 'action-4'),
        (2, 2, False, 'action-1'),
    ],
)
async def test_actions_list_pagination_pages(
    client: AsyncClient,
    auth_headers,
    mongo_function,
    async_mongo_function,
    user1,
    monkeypatch,
    target_page,
    expected_length,
    expects_next_cursor,
    expected_first_action,
):
    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', lambda _: None
    )
    await _insert_actions(user1, n=5)

    response = await client.get('/actions?page_size=3', headers=auth_headers['user1'])
    assert response.status_code == 200
    data = response.json()
    if target_page == 2:
        cursor = data['next_cursor']
        assert cursor is not None
        response = await client.get(
            f'/actions?page_size=3&cursor={cursor}', headers=auth_headers['user1']
        )
        assert response.status_code == 200
        data = response.json()

    assert data['total'] == 5
    assert len(data['items']) == expected_length
    assert (data['next_cursor'] is not None) is expects_next_cursor
    assert data['items'][0]['action_id'] == expected_first_action


@pytest.mark.asyncio
async def test_actions_list_pagination_empty(
    client: AsyncClient, auth_headers, mongo_function, async_mongo_function, monkeypatch
):
    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', lambda _: None
    )
    response = await client.get('/actions?page_size=10', headers=auth_headers['user1'])
    assert response.status_code == 200
    data = response.json()
    assert data['items'] == []
    assert data['next_cursor'] is None
    assert data['total'] == 0


@pytest.mark.asyncio
async def test_actions_list_pagination_exact_page(
    client: AsyncClient,
    auth_headers,
    mongo_function,
    async_mongo_function,
    user1,
    monkeypatch,
):
    """When total == page_size there should be no next_cursor."""
    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', lambda _: None
    )
    await _insert_actions(user1, n=3)

    response = await client.get('/actions?page_size=3', headers=auth_headers['user1'])
    assert response.status_code == 200
    data = response.json()
    assert data['total'] == 3
    assert len(data['items']) == 3
    assert data['next_cursor'] is None


@pytest.mark.asyncio
async def test_actions_list_pagination_invalid_cursor(
    client: AsyncClient, auth_headers, async_mongo_function
):
    """A garbage cursor value should return HTTP 400."""
    response = await client.get(
        '/actions?cursor=not-a-valid-cursor', headers=auth_headers['user1']
    )
    assert response.status_code == 400
    assert 'Invalid pagination cursor' in response.json()['detail']


@pytest.mark.asyncio
async def test_actions_list_filters_by_upload_id(
    client: AsyncClient,
    auth_headers,
    mongo_function,
    async_mongo_function,
    user1,
    monkeypatch,
):
    monkeypatch.setattr(
        'nomad.actions.bootstrap.action_service.a_refresh', lambda _: None
    )
    await ActionDocument(
        action_id='action-1',
        action_instance_id='wf-upload-1',
        upload_id='upload-a',
        status='COMPLETED',
        created_at=datetime.now(),
        updated_at=datetime.now(),
        user_id=user1.user_id,
        input_data={},
        results={},
    ).insert()
    await ActionDocument(
        action_id='action-2',
        action_instance_id='wf-upload-2',
        upload_id='upload-b',
        status='COMPLETED',
        created_at=datetime.now(),
        updated_at=datetime.now(),
        user_id=user1.user_id,
        input_data={},
        results={},
    ).insert()

    response = await client.get(
        '/actions?upload_id=upload-a', headers=auth_headers['user1']
    )
    assert response.status_code == 200
    data = response.json()
    assert data['total'] == 1
    assert len(data['items']) == 1
    assert data['items'][0]['action_instance_id'] == 'wf-upload-1'
