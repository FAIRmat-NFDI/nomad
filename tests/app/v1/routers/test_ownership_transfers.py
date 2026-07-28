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
from datetime import timedelta

import pytest

from nomad.common import now
from nomad.config import config
from nomad.mongo.users import OwnershipTransferRecord
from nomad.processing import Upload
from tests.app.v1.routers.common import assert_response


def _prepare_transfer_resource(
    resource_type: str,
    client,
    auth_headers,
    users_dict,
    example_data_writeable=None,
) -> str:
    if resource_type == 'upload':
        assert example_data_writeable is not None
        return example_data_writeable['id_unpublished_w']
    if resource_type == 'group':
        return _create_group_for_transfer(client, auth_headers, users_dict)
    raise ValueError(f'Unsupported resource type: {resource_type}')


def _create_transfer(
    client,
    auth_headers,
    users_dict,
    *,
    resource_type: str,
    resource_id: str,
    source_user: str = 'user1',
    target_user: str = 'user2',
):
    return client.post(
        'ownership-transfers',
        headers=auth_headers[source_user],
        json={
            'resource_type': resource_type,
            'resource_id': resource_id,
            'target_user': users_dict[target_user].username,
            'target_user_type': 'username',
        },
    )


def _resource_record_type(resource_type: str) -> str:
    if resource_type == 'upload':
        return OwnershipTransferRecord.RESOURCE_TYPE_UPLOAD
    if resource_type == 'group':
        return OwnershipTransferRecord.RESOURCE_TYPE_GROUP
    raise ValueError(f'Unsupported resource type: {resource_type}')


@pytest.mark.parametrize('resource_type', ['upload', 'group'])
def test_transfer_create_and_list_basic(
    auth_headers,
    client,
    example_data_writeable,
    users_dict,
    resource_type,
):
    resource_id = _prepare_transfer_resource(
        resource_type,
        client,
        auth_headers,
        users_dict,
        example_data_writeable=example_data_writeable,
    )

    create_response = _create_transfer(
        client,
        auth_headers,
        users_dict,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    assert_response(create_response, 200)
    created = create_response.json()
    transfer_id = created['transfer_id']
    assert transfer_id
    assert created['resource_type'] == resource_type
    assert created['resource_id'] == resource_id

    list_incoming_response = client.get(
        f'ownership-transfers?direction=incoming&resource_type={resource_type}',
        headers=auth_headers['user2'],
    )
    assert_response(list_incoming_response, 200)
    incoming_items = list_incoming_response.json()['transfers']
    assert any(item['transfer_id'] == transfer_id for item in incoming_items)

    get_response = client.get(
        f'ownership-transfers/{transfer_id}?resource_type={resource_type}',
        headers=auth_headers['user2'],
    )
    assert_response(get_response, 200)
    assert get_response.json()['resource_id'] == resource_id


def _create_group_for_transfer(client, auth_headers, users_dict) -> str:
    create_group_response = client.post(
        'groups',
        headers=auth_headers['user1'],
        json={
            'group_name': 'group-transfer-test',
            'members_info': [
                {'user_id': users_dict['user1'].user_id, 'role': 'owner'},
                {'user_id': users_dict['user2'].user_id, 'role': 'member'},
            ],
        },
    )
    assert_response(create_group_response, 201)
    return create_group_response.json()['group_id']


@pytest.mark.parametrize(
    'resource_type, client_user, expected_status',
    [
        pytest.param('upload', 'user1', 200, id='upload-owner-can-initiate'),
        pytest.param('upload', 'user3', 403, id='upload-outsider-cannot-initiate'),
        pytest.param('group', 'user1', 200, id='group-owner-can-initiate'),
        pytest.param('group', 'user2', 403, id='group-member-cannot-initiate'),
        pytest.param('group', 'user3', 403, id='group-outsider-cannot-initiate'),
    ],
)
def test_transfer_create(
    auth_headers,
    client,
    example_data_writeable,
    users_dict,
    resource_type,
    client_user,
    expected_status,
):
    resource_id = _prepare_transfer_resource(
        resource_type,
        client,
        auth_headers,
        users_dict,
        example_data_writeable=example_data_writeable,
    )

    response = client.post(
        'ownership-transfers',
        headers=auth_headers[client_user],
        json={
            'resource_type': resource_type,
            'resource_id': resource_id,
            'target_user': users_dict['user2'].username,
            'target_user_type': 'username',
        },
    )
    assert_response(response, expected_status)


@pytest.mark.parametrize('resource_type', ['upload', 'group'])
@pytest.mark.parametrize('action', ['accept', 'refuse'])
@pytest.mark.parametrize(
    'client_user, expected_status',
    [
        pytest.param('user2', 200, id='target-can-respond'),
        pytest.param('user1', 403, id='source-cannot-respond'),
        pytest.param('user3', 403, id='outsider-cannot-respond'),
    ],
)
@pytest.mark.asyncio
async def test_transfer_respond(
    auth_headers,
    client,
    temporal_worker,
    example_data_writeable,
    users_dict,
    resource_type,
    action,
    client_user,
    expected_status,
):
    resource_id = _prepare_transfer_resource(
        resource_type,
        client,
        auth_headers,
        users_dict,
        example_data_writeable=example_data_writeable,
    )

    create_response = _create_transfer(
        client,
        auth_headers,
        users_dict,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    assert_response(create_response, 200)
    transfer_id = create_response.json()['transfer_id']

    if resource_type == 'upload':
        async with temporal_worker():
            response = await asyncio.to_thread(
                lambda: client.post(
                    f'ownership-transfers/{transfer_id}/respond?resource_type={resource_type}',
                    headers=auth_headers[client_user],
                    json={'action': action},
                )
            )
    else:
        response = client.post(
            f'ownership-transfers/{transfer_id}/respond?resource_type={resource_type}',
            headers=auth_headers[client_user],
            json={'action': action},
        )

    assert_response(response, expected_status)

    if expected_status == 200:
        response_data = response.json()
        resource_id_key = 'upload_id' if resource_type == 'upload' else 'group_id'
        assert response_data['resource_type'] == resource_type
        assert response_data['resource_id'] == resource_id
        assert response_data['result'][resource_id_key] == resource_id
        assert response_data['result']['data'][resource_id_key] == resource_id

        if resource_type == 'group':
            group_response = client.get(
                f'groups/{resource_id}',
                headers=auth_headers['user1'],
            )
            assert_response(group_response, 200)
            expected_owner_id = (
                users_dict['user2'].user_id
                if action == 'accept'
                else users_dict['user1'].user_id
            )
            assert group_response.json()['owner'] == expected_owner_id
            if action == 'accept':
                assert (
                    users_dict['user1'].user_id not in group_response.json()['members']
                )

    pending_record = OwnershipTransferRecord.objects(
        resource_type=_resource_record_type(resource_type),
        resource_id=resource_id,
        state=OwnershipTransferRecord.STATE_PENDING,
    ).first()
    if expected_status == 200:
        assert pending_record is None
    else:
        assert pending_record is not None


@pytest.mark.parametrize('resource_type', ['upload', 'group'])
@pytest.mark.parametrize(
    'client_user, expected_status',
    [
        pytest.param('user1', 200, id='source-can-cancel'),
        pytest.param('user2', 403, id='target-cannot-cancel'),
        pytest.param('user3', 403, id='outsider-cannot-cancel'),
    ],
)
@pytest.mark.asyncio
async def test_transfer_cancel(
    auth_headers,
    client,
    temporal_worker,
    example_data_writeable,
    users_dict,
    resource_type,
    client_user,
    expected_status,
):
    resource_id = _prepare_transfer_resource(
        resource_type,
        client,
        auth_headers,
        users_dict,
        example_data_writeable=example_data_writeable,
    )

    create_response = _create_transfer(
        client,
        auth_headers,
        users_dict,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    assert_response(create_response, 200)
    transfer_id = create_response.json()['transfer_id']

    if resource_type == 'upload':
        async with temporal_worker():
            response = await asyncio.to_thread(
                lambda: client.post(
                    f'ownership-transfers/{transfer_id}/cancel?resource_type={resource_type}',
                    headers=auth_headers[client_user],
                )
            )
    else:
        response = client.post(
            f'ownership-transfers/{transfer_id}/cancel?resource_type={resource_type}',
            headers=auth_headers[client_user],
        )

    assert_response(response, expected_status)

    if expected_status == 200:
        response_data = response.json()
        resource_id_key = 'upload_id' if resource_type == 'upload' else 'group_id'
        assert response_data['resource_type'] == resource_type
        assert response_data['resource_id'] == resource_id
        assert response_data['result'][resource_id_key] == resource_id
        assert response_data['result']['data'][resource_id_key] == resource_id

    pending_record = OwnershipTransferRecord.objects(
        resource_type=_resource_record_type(resource_type),
        resource_id=resource_id,
        state=OwnershipTransferRecord.STATE_PENDING,
    ).first()
    if expected_status == 200:
        assert pending_record is None
    else:
        assert pending_record is not None


@pytest.mark.parametrize('resource_type', ['upload', 'group'])
def test_transfer_list_omits_expired_pending_records(
    auth_headers,
    client,
    example_data_writeable,
    users_dict,
    resource_type,
):
    resource_id = _prepare_transfer_resource(
        resource_type,
        client,
        auth_headers,
        users_dict,
        example_data_writeable=example_data_writeable,
    )

    create_response = _create_transfer(
        client,
        auth_headers,
        users_dict,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    assert_response(create_response, 200)
    transfer_id = create_response.json()['transfer_id']

    OwnershipTransferRecord.objects(id=transfer_id).update_one(
        set__requested_at=now()
        - timedelta(seconds=config.mongo.ownership_transfer_record_ttl + 1)
    )

    list_incoming_response = client.get(
        f'ownership-transfers?direction=incoming&resource_type={resource_type}',
        headers=auth_headers['user2'],
    )
    assert_response(list_incoming_response, 200)
    incoming_items = list_incoming_response.json()['transfers']
    assert not any(item['transfer_id'] == transfer_id for item in incoming_items)


@pytest.mark.parametrize('resource_type', ['upload', 'group'])
def test_transfer_get_hides_expired_pending_records(
    auth_headers,
    client,
    example_data_writeable,
    users_dict,
    resource_type,
):
    resource_id = _prepare_transfer_resource(
        resource_type,
        client,
        auth_headers,
        users_dict,
        example_data_writeable=example_data_writeable,
    )

    create_response = _create_transfer(
        client,
        auth_headers,
        users_dict,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    assert_response(create_response, 200)
    transfer_id = create_response.json()['transfer_id']

    OwnershipTransferRecord.objects(id=transfer_id).update_one(
        set__requested_at=now()
        - timedelta(seconds=config.mongo.ownership_transfer_record_ttl + 1)
    )

    get_response = client.get(
        f'ownership-transfers/{transfer_id}?resource_type={resource_type}',
        headers=auth_headers['user2'],
    )
    assert_response(get_response, 404)


def test_transfer_unsupported_type(auth_headers, client, users_dict):
    resource_type = 'dataset'

    create_response = client.post(
        'ownership-transfers',
        headers=auth_headers['user1'],
        json={
            'resource_type': resource_type,
            'resource_id': 'dummy-dataset-id',
            'target_user': users_dict['user2'].username,
            'target_user_type': 'username',
        },
    )
    assert_response(create_response, 400)

    list_response = client.get(
        f'ownership-transfers?resource_type={resource_type}',
        headers=auth_headers['user1'],
    )
    assert_response(list_response, 400)

    get_response = client.get(
        f'ownership-transfers/dummy-transfer-id?resource_type={resource_type}',
        headers=auth_headers['user1'],
    )
    assert_response(get_response, 400)

    respond_response = client.post(
        f'ownership-transfers/dummy-transfer-id/respond?resource_type={resource_type}',
        headers=auth_headers['user1'],
        json={'action': 'refuse'},
    )
    assert_response(respond_response, 400)

    cancel_response = client.post(
        f'ownership-transfers/dummy-transfer-id/cancel?resource_type={resource_type}',
        headers=auth_headers['user1'],
    )
    assert_response(cancel_response, 400)


def test_respond_to_upload_transfer_accept_releases_claim_on_workflow_failure(
    auth_headers,
    client,
    example_data_writeable,
    users_dict,
    monkeypatch,
):
    upload_id = example_data_writeable['id_unpublished_w']

    create_response = client.post(
        'ownership-transfers',
        headers=auth_headers['user1'],
        json={
            'resource_type': 'upload',
            'resource_id': upload_id,
            'target_user': users_dict['user2'].username,
            'target_user_type': 'username',
        },
    )
    assert_response(create_response, 200)
    transfer_id = create_response.json()['transfer_id']

    def _raise_transfer_failure(self, new_owner_user_id, previous_owner_user_id):
        raise RuntimeError('simulated transfer workflow failure')

    monkeypatch.setattr(Upload, 'transfer_ownership', _raise_transfer_failure)

    response = client.post(
        f'ownership-transfers/{transfer_id}/respond?resource_type=upload',
        headers=auth_headers['user2'],
        json={'action': 'accept'},
    )
    assert_response(response, 500)
    assert 'Failed to execute transfer workflow' in response.json()['detail']

    pending_record = OwnershipTransferRecord.objects(
        id=transfer_id,
        state=OwnershipTransferRecord.STATE_PENDING,
    ).first()
    assert pending_record is not None
