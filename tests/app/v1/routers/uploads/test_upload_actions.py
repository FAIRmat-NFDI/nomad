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
import time
from datetime import datetime, timezone
from typing import Literal
from unittest.mock import MagicMock, Mock

import pytest
from fastapi.testclient import TestClient

from nomad.app.v1.routers.uploads.models import UploadTransferRequest
from nomad.common import now
from nomad.config import config
from nomad.processing import ProcessStatus, Upload
from tests.app.v1.routers.common import assert_response
from tests.fixtures.infrastructure import DataciteMock, TemporalWorkerContext

# Imported for side effects: ExampleData.create_entry expects the test runschema
# registered by tests.processing.test_data.
from tests.processing import test_data as test_processing  # noqa: F401
from tests.processing.test_edit_metadata import assert_metadata_edited
from tests.utils import assert_doi_name, build_url, set_upload_entry_metadata

from .common import assert_upload
from .test_uploads import (
    assert_gets_published,
    assert_processing,
    block_until_completed,
)


def perform_post_upload_action(
    client, user_auth, upload_id, action, json=None, **query_args
):
    return client.post(
        build_url(f'uploads/{upload_id}/action/{action}', query_args),
        headers=user_auth,
        json=json,
    )


@pytest.mark.parametrize(
    'upload_id, publish, user, expected_status_code',
    [
        # Test access/permission
        pytest.param(None, True, 'user0', 200, id='published-admin'),
        pytest.param(None, True, 'user1', 403, id='published-not-admin'),
        pytest.param(None, False, None, 401, id='no-credentials'),
        pytest.param(None, False, 'invalid', 401, id='invalid-credentials'),
        pytest.param(None, False, 'user2', 403, id='no-access'),
        # Test state
        pytest.param(None, False, 'user1', 200, id='not-published'),
        pytest.param('id_processing_w', False, 'user1', 400, id='already-processing'),
        # Test failure
        pytest.param('silly_value', False, 'user1', 404, id='invalid-upload_id'),
    ],
)
@pytest.mark.asyncio
async def test_post_upload_action_process(
    auth_headers,
    client,
    temporal_worker,
    monkeypatch,
    example_data_writeable,
    non_empty_processed_with_temporal,
    internal_example_user_metadata,
    upload_id,
    publish,
    user,
    expected_status_code,
):
    async with temporal_worker():
        if publish:
            set_upload_entry_metadata(
                non_empty_processed_with_temporal, internal_example_user_metadata
            )
            await asyncio.to_thread(non_empty_processed_with_temporal.publish_upload)

        monkeypatch.setattr('nomad.config.meta.version', 're_process_test_version')
        monkeypatch.setattr('nomad.config.meta.commit', 're_process_test_commit')
        user_auth = auth_headers[user]

        if upload_id is None:
            upload_id = non_empty_processed_with_temporal.upload_id
        elif example_data_upload_id := example_data_writeable.get(upload_id):
            upload_id = example_data_upload_id
        response = await asyncio.to_thread(
            lambda: perform_post_upload_action(client, user_auth, upload_id, 'process')
        )
        assert_response(response, expected_status_code)
        if expected_status_code == 200:
            await asyncio.to_thread(
                lambda: assert_processing(
                    client,
                    upload_id,
                    auth_headers['user1'],
                    check_files=False,
                    published=True,
                )
            )


@pytest.mark.parametrize(
    'upload_id, user, owner, query, include_parent_folders, expected_status_code, expect_exists, expect_not_exists',
    [
        pytest.param(
            'id_unpublished_w',
            'user1',
            None,
            None,
            False,
            400,
            [],
            [],
            id='no-query',
        ),
        # Test success
        pytest.param(
            'id_unpublished_w',
            'user1',
            None,
            {'entry_id': ['id_unpublished_w_entry', 'silly']},
            False,
            200,
            ['test_content/test_embargo_entry/1.aux'],
            ['test_content/test_embargo_entry/mainfile.json'],
            id='ok',
        ),
        pytest.param(
            'id_unpublished_w',
            'user1',
            None,
            {'entry_id': 'id_unpublished_w_entry'},
            True,
            200,
            ['test_content'],
            ['test_content/test_embargo_entry'],
            id='ok-delete-folder',
        ),
        pytest.param(
            'id_unpublished_w',
            'user0',
            'admin',
            {'entry_id': 'id_unpublished_w_entry'},
            False,
            200,
            ['test_content/test_embargo_entry/1.aux'],
            ['test_content/test_embargo_entry/mainfile.json'],
            id='ok-admin-access',
        ),
        # Test access/permission
        pytest.param(
            'id_published_w',
            'user0',
            None,
            {'entry_id': 'id_published_w_entry'},
            False,
            400,
            [],
            [],
            id='published-admin',
        ),
        pytest.param(
            'id_unpublished_w',
            'user2',
            None,
            {'entry_id': 'id_unpublished_w_entry'},
            False,
            403,
            [],
            [],
            id='unpublished-no-access',
        ),
    ],
)
@pytest.mark.asyncio
async def test_post_upload_action_delete_entry_files(
    auth_headers,
    client,
    temporal_worker: TemporalWorkerContext,
    example_data_writeable,
    upload_id,
    user,
    owner,
    query,
    include_parent_folders,
    expected_status_code,
    expect_exists,
    expect_not_exists,
):
    if upload_id in example_data_writeable:
        upload_id = example_data_writeable[upload_id]
    json: dict = {}
    if include_parent_folders is not None:
        json.update(include_parent_folders=include_parent_folders)
    if owner is not None:
        json.update(owner=owner)
    if query is not None:
        json.update(query=query)

    async with temporal_worker() as env:
        response = await asyncio.to_thread(
            lambda: perform_post_upload_action(
                client, auth_headers[user], upload_id, 'delete-entry-files', json=json
            )
        )
        assert_response(response, expected_status_code)
        if expected_status_code == 200:
            upload = Upload.get(upload_id)
            upload.reload()
            # allow enough time to start processing the workflow
            while True:
                if upload.process_status == ProcessStatus.PENDING:
                    await asyncio.to_thread(lambda: time.sleep(1))
                    upload.reload()
                else:
                    break
            if workflow_ids := upload.workflow_ids:
                handle = env.client.get_workflow_handle(workflow_ids[0])
                await handle.result()
            for path in expect_exists or []:
                assert upload.upload_files.raw_exists(path), (
                    f'Missing expected path: {path}'
                )
            for path in expect_not_exists or []:
                assert not upload.upload_files.raw_exists(path), (
                    f'Expected path not to exist: {path}'
                )


@pytest.mark.parametrize(
    'has_write_access,is_published,upload_state,expected_status',
    [
        pytest.param(True, False, ProcessStatus.PENDING, 200, id='success-case'),
        # Test state
        pytest.param(True, True, ProcessStatus.PENDING, 400, id='published-upload'),
        pytest.param(
            True, False, ProcessStatus.SUCCESS, 400, id='success-state-invalid'
        ),
        pytest.param(
            True, False, ProcessStatus.FAILURE, 400, id='failure-state-invalid'
        ),
        # Test access/permission
        pytest.param(False, False, ProcessStatus.PENDING, 403, id='permission-denied'),
    ],
)
def test_stop_processing_action(
    has_write_access,
    is_published,
    upload_state,
    expected_status,
    non_empty_uploaded,
    user1,
    user2,
    auth_headers,
    client,
    temporal_worker,
    monkeypatch,
):
    """Tests the endpoint for stopping the processing of an upload."""
    upload_id, _ = non_empty_uploaded

    # Create upload with appropriate owner based on access test
    upload_owner = user1 if has_write_access else user2
    upload = Upload.create(
        upload_id=upload_id,
        main_author=upload_owner,
        publish_time=datetime.now(timezone.utc) if is_published else None,
        workflow_ids=['example-workflow-id'],
    )
    upload.save()
    upload.process_status = upload_state
    upload.save()

    # Always use user1's auth headers for the request
    user_auth = auth_headers['user1']

    # Mock the stop processing workflow method
    async def mock_stop_processing_workflows(self):
        pass

    monkeypatch.setattr(
        Upload, '_stop_processing_workflows', mock_stop_processing_workflows
    )

    # Perform the request
    response = perform_post_upload_action(
        client, user_auth, upload_id, 'stop-processing'
    )

    assert_response(response, expected_status)

    if expected_status == 200:
        upload.reload()
        assert len(upload.workflow_ids) == 0
        assert upload.process_status == ProcessStatus.READY
        assert upload.last_status_message == 'Processing stopped'


@pytest.mark.parametrize(
    'kwargs',
    [
        pytest.param(dict(expected_status_code=200), id='no-args'),
        # Test `embargo_length`
        pytest.param(
            dict(query_args={'embargo_length': 12}, expected_status_code=200),
            id='non-standard-embargo',
        ),
        pytest.param(
            dict(query_args={'embargo_length': 24}, expected_status_code=200),
            id='non-standard-embargo-length-only',
        ),
        pytest.param(
            dict(query_args={'embargo_length': 100}, expected_status_code=400),
            id='illegal-embargo-length',
        ),
        pytest.param(
            dict(query_args={'embargo_length': 0}, expected_status_code=200),
            id='no-embargo',
        ),
        # Test state (empty/processing/published)
        pytest.param(
            dict(upload_id='id_empty_w', expected_status_code=400), id='empty'
        ),
        pytest.param(
            dict(upload_id='id_processing_w', expected_status_code=400), id='processing'
        ),
        pytest.param(
            dict(upload_id='id_published_w', expected_status_code=400),
            id='already-published',
        ),
        # Test access/permission
        pytest.param(dict(user=None, expected_status_code=401), id='no-credentials'),
        pytest.param(
            dict(user='invalid', expected_status_code=401), id='invalid-credentials'
        ),
        pytest.param(dict(user='user2', expected_status_code=403), id='no-access'),
    ],
)
@pytest.mark.asyncio
async def test_post_upload_action_publish(
    auth_headers, client, temporal_worker, example_data_writeable, kwargs
):
    """Tests the publish action with various arguments."""
    upload_id = kwargs.get('upload_id', 'id_unpublished_w')
    if upload_id in example_data_writeable:
        upload_id = example_data_writeable[upload_id]
    query_args = kwargs.get('query_args', {})
    expected_status_code = kwargs.get('expected_status_code', 200)
    user = kwargs.get('user', 'user1')
    user_auth = auth_headers[user]
    async with temporal_worker():
        response = await asyncio.to_thread(
            lambda: perform_post_upload_action(
                client, user_auth, upload_id, 'publish', **query_args
            )
        )

    assert_response(response, expected_status_code)
    if expected_status_code == 200:
        upload = assert_upload(response.json())
        assert upload['process_running']

        assert_gets_published(
            client, upload_id, user_auth, current_embargo_length=12, **query_args
        )


@pytest.mark.parametrize(
    'upload_id, user, preprocess, expected_status_code',
    [
        pytest.param('id_published_w', 'user1', None, 200, id='ok'),
        pytest.param('id_unpublished_w', 'user1', None, 400, id='not-published'),
        pytest.param('id_published_w', 'user1', 'lift', 400, id='already-lifted'),
        # Test access/permission
        pytest.param('id_published_w', 'user2', None, 403, id='no-access'),
        pytest.param('id_published_w', 'user2', 'make-coauthor', 200, id='ok-coauthor'),
        pytest.param('id_published_w', None, None, 401, id='no-credentials'),
        pytest.param('id_published_w', 'invalid', None, 401, id='invalid-credentials'),
    ],
)
@pytest.mark.asyncio
async def test_post_upload_action_lift_embargo(
    auth_headers,
    client,
    example_data_writeable,
    users_dict,
    upload_id,
    user,
    preprocess,
    expected_status_code,
    temporal_worker,
):
    if upload_id in example_data_writeable:
        upload_id = example_data_writeable[upload_id]
    user_auth = auth_headers[user]
    user = users_dict.get(user)

    async with temporal_worker():
        if preprocess:
            if preprocess == 'lift':
                metadata = {'embargo_length': 0}
            elif preprocess == 'make-coauthor':
                metadata = {'coauthors': user.user_id}
            upload = Upload.get(upload_id)
            await upload._start_edit_upload_metadata_workflow(
                dict(metadata=metadata),
                config.services.admin_user_id,
                wait_for_processing=True,
            )

        response = await asyncio.to_thread(
            lambda: perform_post_upload_action(
                client, user_auth, upload_id, 'lift-embargo'
            )
        )
    assert_response(response, expected_status_code)
    if expected_status_code == 200:
        assert_metadata_edited(user, {'embargo_length': 0}, [upload_id])


@pytest.fixture
def create_upload(elastic_function, raw_files_function, mongo_function, user1):
    from nomad.utils.exampledata import ExampleData

    default_upload = dict(
        upload_id='upload_id',
    )

    default_entry = dict(
        upload_id=default_upload['upload_id'],
        entry_id='entry_id',
    )

    def _create(
        *,
        upload: dict | None = None,
        entry: dict | None = None,
        skip_entry: bool = False,
    ):
        data = ExampleData(main_author=user1)
        upload = default_upload | (upload or {})
        data.create_upload(**upload)

        if not skip_entry:
            entry = default_entry | (entry or {})
            data.create_entry(**entry)

        data.save()
        return data

    return _create


@pytest.mark.parametrize(
    'upload_label, user, datacite_enabled, status_code',
    [
        pytest.param('published', 'user1', True, 200, id='plain'),
        # Test access/permission
        pytest.param('published', None, True, 401, id='no-user'),
        pytest.param('published', 'user2', True, 403, id='wrong-user'),
        # Test failures
        pytest.param('published', 'user1', False, 403, id='datacite-disabled'),
        pytest.param('with_doi', 'user1', True, 400, id='with-doi'),
        pytest.param('unpublished', 'user1', True, 400, id='unpublished'),
        pytest.param('empty', 'user1', True, 400, id='empty'),
        pytest.param(None, 'user1', True, 404, id='non-existing'),
    ],
)
def test_assign_doi_upload(
    datacite_mock: DataciteMock,
    auth_headers,
    client,
    create_upload,
    upload_label,
    user,
    datacite_enabled,
    status_code,
):
    datacite_mock.set_enabled(datacite_enabled)

    if upload_label == 'published':
        data = create_upload(upload={'publish_time': now()})
    elif upload_label == 'with_doi':
        upload = {'publish_time': now(), 'doi': {'id': '10.83696/test-doi'}}
        data = create_upload(upload=upload)
    elif upload_label == 'unpublished':
        data = create_upload()
    elif upload_label == 'empty':
        data = create_upload(skip_entry=True)

    headers = auth_headers[user]
    response = client.post(f'uploads/upload_id/action/assign-doi', headers=headers)

    assert_response(response, status_code)
    if not datacite_enabled:
        assert 'not enabled' in response.json()['detail']
    if status_code != 200:
        return

    response = response.json()
    assert_upload(response)
    doi_name = response['data']['doi']['id']
    assert_doi_name(doi_name)


def test_assign_doi_upload_datacite_error(
    datacite_mock: DataciteMock,
    auth_headers,
    client,
    create_upload,
):
    datacite_mock.set_requests(401, False, 'Bad credentials.')
    data = create_upload(upload={'publish_time': now()})

    headers = auth_headers['user1']
    response = client.post(f'uploads/upload_id/action/assign-doi', headers=headers)

    assert_response(response, 500)
    msg = response.json()['detail']
    assert 'An error occurred while creating the DOI draft at DataCite.' in msg
    upload = Upload.get('upload_id')
    assert upload.doi is None


def test_assign_doi_upload_mongo_error(
    mock_mongo_fail_save,
    datacite_mock: DataciteMock,
    auth_headers,
    client,
    create_upload,
):
    data = create_upload(upload={'publish_time': now()})
    mock_mongo_fail_save(Upload)

    headers = auth_headers['user1']
    response = client.post(f'uploads/upload_id/action/assign-doi', headers=headers)

    assert_response(response, 500)
    msg = response.json()['detail']
    assert 'An error occurred while saving the upload doi to the database.' in msg
    upload = Upload.get('upload_id')
    assert upload.doi is None


@pytest.fixture(autouse=True)
def setup_for_transfer_bundle(request, monkeypatch, mongo_function):
    disable_health_check_patch = request.node.get_closest_marker(
        'disable_health_check_patch'
    )
    if not disable_health_check_patch:
        monkeypatch.setattr(
            'nomad.app.v1.routers.uploads.utils.check_external_deployment_status',
            MagicMock(),
        )
    enable_target_deployment_url_validation = request.node.get_closest_marker(
        'enable_target_deployment_url_validation'
    )
    if not enable_target_deployment_url_validation:
        monkeypatch.setattr(
            'nomad.app.v1.routers.uploads.utils.validate_target_deployment_url',
            MagicMock(),
        )


@pytest.mark.parametrize('include_schemas', [False, True])
def test_transfer_rejects_missing_contents(
    client, auth_headers, monkeypatch, include_schemas
):
    publish_externally = Mock()
    monkeypatch.setattr(Upload, 'publish_externally', publish_externally)

    response = client.post(
        'uploads/test-upload/action/transfer',
        headers=auth_headers['user0'],
        json={
            'auth_token': 'destination-token',
            'include_raw_files': False,
            'include_archive_files': False,
            'include_schemas': include_schemas,
        },
    )

    assert response.status_code == 422, response.text
    assert 'Include raw files or processed archives, or both.' in response.text
    publish_externally.assert_not_called()


@pytest.mark.parametrize(
    'options',
    [
        {},
        {'include_raw_files': True, 'include_archive_files': True},
        {'include_raw_files': True, 'include_archive_files': False},
        {'include_raw_files': False, 'include_archive_files': True},
    ],
)
def test_transfer_accepts_valid_contents(options):
    request = UploadTransferRequest(auth_token='destination-token', **options)
    assert request.include_raw_files or request.include_archive_files


async def _perform_transfer_request(
    upload_id,
    client: TestClient,
    request_auth,
    embargo_length: int | None = None,
    target_deployment_url: str | None = None,
    target_deployment_token: str | None = None,
):
    transfer_config = {
        'auth_token': target_deployment_token,
        'target_deployment_url': target_deployment_url,
        'embargo_length': embargo_length,
    }
    transfer_config = {
        key: value for key, value in transfer_config.items() if value is not None
    }
    response = await asyncio.to_thread(
        lambda: client.post(
            f'uploads/{upload_id}/action/transfer',
            headers=request_auth,
            json=transfer_config,
        )
    )
    body = response.json()
    return response, body


def _get_token(auth_headers, user):
    token = auth_headers[user]['Authorization'].split(' ')[1]
    return token


def _compare_entries_meta_info(old_upload, new_upload, embargo_length):
    old_entry = old_upload.successful_entries[0]
    new_entry = new_upload.successful_entries[0]
    old_entry_metadata_dict = old_entry.full_entry_metadata(old_upload).m_to_dict()
    new_entry_metadata_dict = new_entry.full_entry_metadata(new_upload).m_to_dict()
    for k, v in old_entry_metadata_dict.items():
        if k == 'with_embargo':
            assert new_entry_metadata_dict[k] == (embargo_length > 0)
        elif k not in (
            'upload_id',
            'entry_id',
            'upload_create_time',
            'entry_create_time',
            'last_processing_time',
            'publish_time',
            'embargo_length',
            'n_quantities',
            'quantities',
        ):
            assert new_entry_metadata_dict[k] == v, f'Metadata not matching: {k}'


async def _check_success_transfer_upload(
    response, client, upload_id, suffix, user_auth, embargo_length
):
    old_upload = Upload.get(upload_id)
    expected_status_code = 200
    assert response.status_code == expected_status_code
    upload = assert_upload(response.json())
    assert upload['current_process'] == '_publish_externally'
    assert upload['process_running']

    await asyncio.to_thread(
        lambda: assert_processing(
            client, upload_id, user_auth, published=old_upload.published
        )
    )
    await asyncio.to_thread(
        lambda: assert_processing(
            client, upload_id + suffix, user_auth, published=old_upload.published
        )
    )

    old_upload = Upload.get(upload_id)
    assert old_upload['current_process'] == '_publish_externally'
    new_upload = Upload.get(upload_id + suffix)
    assert len(old_upload.successful_entries) == len(new_upload.successful_entries) == 1

    _compare_entries_meta_info(old_upload, new_upload, embargo_length)
    assert old_upload.published_to[0] == config.oasis.central_nomad_deployment_url
    assert new_upload.from_oasis and new_upload.oasis_deployment_url
    assert new_upload.embargo_length == embargo_length
    assert (
        new_upload.upload_files.access == 'restricted'  # type: ignore
        if embargo_length > 0
        else 'public'
    )


async def _request_transfer_start(
    auth_headers,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
    embargo_length: int = 0,
    user: str = 'user0',
    target_deployment_user: str | None = None,
    check_success: bool = False,
    target_deployment_url: str | None = None,
):
    """
    Hit the endpoint to start a transfer and check success if required
    params:
        user: Used to authorize the request.
        target_deployment_user: Will be used to authorize the transfer in the target deployment. If not provided, it will use the same user of the request.
        check_success: Verify if the whole transfer process is successfull. This includes waiting for the workflow to finish and compare internal variables to check the integrity of the transfer
    """
    upload_id, suffix = oasis_publishable_upload
    user_auth = auth_headers[user]
    target_deployment_token = _get_token(auth_headers, target_deployment_user or user)
    response, body = await _perform_transfer_request(
        upload_id,
        client,
        request_auth=user_auth,
        embargo_length=embargo_length,
        target_deployment_url=target_deployment_url,
        target_deployment_token=target_deployment_token,
    )
    if check_success:
        await _check_success_transfer_upload(
            response, client, upload_id, suffix, user_auth, embargo_length
        )
    return response, body


@pytest.mark.parametrize(
    'embargo_length, expected_response_code',
    [
        pytest.param(-10, 422, id='embargo_length=-10'),
        pytest.param(0, 200, id='embargo_length=0'),
        pytest.param(5, 200, id='embargo_length=5'),
        pytest.param(36, 200, id='embargo_length=36'),
        pytest.param(40, 422, id='embargo_length=40'),
    ],
)
@pytest.mark.asyncio
async def test_embargo_length(
    auth_headers,
    temporal_worker,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
    embargo_length: int,
    expected_response_code: int,
):
    async with temporal_worker():
        response, body = await _request_transfer_start(
            auth_headers,
            client,
            oasis_publishable_upload,
            embargo_length,
            check_success=expected_response_code < 400,
        )

    assert response.status_code == expected_response_code
    if expected_response_code >= 400:
        assert len(body['detail']) > 0  # Check error message info


async def _check_workflow_failure(
    client, upload_id, user_auth, error_messages: list[str]
):
    """
    Waits until the workflow fails and check that the error messages
    are being stored in the upload
    params:
        error_messages: The messages to be checked if exist in the upload
    """
    # The workflow should successfully start
    upload = Upload.get(upload_id)
    await asyncio.to_thread(lambda: block_until_completed(client, upload_id, user_auth))
    await upload.await_workflows()
    upload = Upload.get(upload_id)
    assert upload.process_status == ProcessStatus.FAILURE
    assert len(upload.errors) > 0
    upload_error = upload['errors'][0]

    for expected_error in error_messages:
        assert expected_error in upload_error


@pytest.mark.asyncio
async def test_bad_formatted_token(
    temporal_worker,
    auth_headers,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
):
    upload_id, _ = oasis_publishable_upload
    auth = auth_headers['user0']

    async with temporal_worker():
        response, _ = await _perform_transfer_request(
            upload_id, client, request_auth=auth, target_deployment_token='abcdef'
        )
        errors = [
            'Error message from external deployment',
            'user does not exist',
        ]
        await _check_workflow_failure(
            client,
            upload_id,
            user_auth=auth,
            error_messages=errors,
        )
        for error_message in errors:
            assert error_message in response.json()['detail']


@pytest.mark.asyncio
async def test_invalid_token(
    temporal_worker,
    auth_headers,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
):
    upload_id, _ = oasis_publishable_upload
    user = 'user1'
    user_auth = auth_headers[user]

    async with temporal_worker():
        # try:
        response, _ = await _request_transfer_start(
            auth_headers,
            client,
            oasis_publishable_upload,
            0,
            user,
            target_deployment_user='invalid',
            check_success=False,
        )
        # Check that the workflow properly handled the error
        # and stored a meaningful error message in the upload
        error_messages = [
            'Error message from external deployment',
            'user does not exist',
        ]
        await _check_workflow_failure(
            client,
            upload_id,
            user_auth=user_auth,
            error_messages=error_messages,
        )
        # The response should also contain the error messages from the workflow
        for error_message in error_messages:
            assert error_message in response.json()['detail']


@pytest.mark.asyncio
async def test_transfer_externally_workflow_failed(
    temporal_worker,
    auth_headers,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
    monkeypatch,
):
    """
    Test a success transfer start and there is an internal
    failure in the workflow.
    The expected behavior is the following:
    1. Transfer start request answer with 200 (OK).
    2. Wait until the process is finished.
    3. The internal process fails with some message.
    4. The information about the failure is stored in the upload.
    """

    error_message = 'test error message'

    monkeypatch.setattr(
        'nomad.processing.data.Upload._publish_externally_local',
        Mock(side_effect=Exception(error_message)),
    )
    upload_id, _ = oasis_publishable_upload
    user = 'user0'
    async with temporal_worker():
        response, _ = await _request_transfer_start(
            auth_headers,
            client,
            oasis_publishable_upload,
            user=user,
            check_success=False,
        )
        await _check_workflow_failure(
            client,
            upload_id,
            user_auth=auth_headers[user],
            error_messages=[error_message],
        )
        assert error_message in response.json()['detail']


@pytest.mark.parametrize(
    'user',
    [
        'user0',  # admin
        'user1',  # oasis admin
        'user2',  # normal user
    ],
)
@pytest.mark.asyncio
async def test_different_user_roles(
    temporal_worker,
    auth_headers,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
    user,
):
    async with temporal_worker():
        await _request_transfer_start(
            auth_headers, client, oasis_publishable_upload, 0, user, check_success=True
        )


@pytest.mark.asyncio
async def test_token_not_provided(
    temporal_worker,
    auth_headers,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
):
    upload_id, _ = oasis_publishable_upload
    async with temporal_worker():
        response, body = await _perform_transfer_request(
            upload_id,
            client,
            request_auth=auth_headers['user0'],
            target_deployment_token=None,
        )
        assert response.status_code == 422
        assert len(body['detail']) > 0


@pytest.mark.asyncio
@pytest.mark.disable_health_check_patch
async def test_external_deployment_health_failed(
    temporal_worker,
    auth_headers,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
    monkeypatch,
):
    error_message = 'generic message'

    monkeypatch.setattr(
        'nomad.app.v1.routers.uploads.utils.perform_status_check',
        Mock(side_effect=Exception(error_message)),
    )
    async with temporal_worker():
        response, body = await _request_transfer_start(
            auth_headers, client, oasis_publishable_upload, check_success=False
        )
        assert 'detail' in body
        assert 'Failed to check external deployment health' in body['detail']
        assert error_message in body['detail']
        assert response.status_code == 400


@pytest.mark.enable_target_deployment_url_validation
@pytest.mark.parametrize(
    'target_url, expected_message',
    [
        pytest.param('abcde', 'URL must start with http:// or https://', id='no-http'),
        pytest.param('http://', 'URL must contain a valid host', id='no-hostname'),
        pytest.param(
            'http://google.com', "URL path must end with '/api'", id='bad-ending'
        ),
    ],
)
@pytest.mark.asyncio
async def test_invalid_target_url(
    temporal_worker,
    auth_headers,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
    target_url,
    expected_message,
):
    async with temporal_worker():
        response, body = await _request_transfer_start(
            auth_headers,
            client,
            oasis_publishable_upload,
            check_success=False,
            target_deployment_url=target_url,
        )
        assert expected_message in body['detail']
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_default_target_url(
    temporal_worker,
    auth_headers,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
):
    upload_id, _ = oasis_publishable_upload
    async with temporal_worker():
        await _request_transfer_start(
            auth_headers,
            client,
            oasis_publishable_upload,
            check_success=True,
        )
        old_upload = Upload.get(upload_id)
        assert len(old_upload.published_to) == 1
        assert old_upload.published_to[0] == config.oasis.central_nomad_deployment_url


@pytest.mark.asyncio
async def test_transfer_processing_upload(
    temporal_worker,
    auth_headers,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
):
    """
    Transfer an upload that is being processed should fail
    """
    upload_id, _ = oasis_publishable_upload
    async with temporal_worker():
        upload = Upload.get(upload_id)
        upload.process_upload()
        response, body = await _request_transfer_start(
            auth_headers,
            client,
            oasis_publishable_upload,
            check_success=False,
        )

        assert 'detail' in body
        assert (
            body['detail']
            == 'The upload is currently being processed, operation not allowed.'
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_non_published_upload(
    temporal_worker,
    auth_headers,
    client: TestClient,
    non_empty_processed_with_temporal: Upload,
):
    """
    Non published uploads should not be able to be transferred
    """
    upload_id = non_empty_processed_with_temporal.upload_id
    async with temporal_worker():
        response, body = await _perform_transfer_request(
            upload_id,
            client,
            request_auth=auth_headers['user0'],
            target_deployment_token=_get_token(auth_headers, 'user0'),
        )
        assert response.status_code == 400
        assert body['detail'] == 'The upload should be published first.'


@pytest.mark.asyncio
async def test_transfer_duplicated_upload(
    temporal_worker,
    auth_headers,
    client: TestClient,
    oasis_publishable_upload: tuple[str, Literal['_v2']],
):
    upload_id, _ = oasis_publishable_upload
    user = 'user0'
    user_auth = auth_headers[user]
    async with temporal_worker() as _:
        await _request_transfer_start(
            auth_headers,
            client,
            oasis_publishable_upload,
            user=user,
            check_success=True,
        )

    async with temporal_worker() as _:
        # Trying to transfer again should fail
        response, _ = await _request_transfer_start(
            auth_headers,
            client,
            oasis_publishable_upload,
            user=user,
            check_success=False,
        )
        error_messages = [
            'Error message from external deployment',
            'Failed to import bundle: Upload with id',
            'already exists',
        ]
        await _check_workflow_failure(
            client,
            upload_id,
            user_auth=user_auth,
            error_messages=error_messages,
        )
        for error_message in error_messages:
            assert error_message in response.json()['detail']
