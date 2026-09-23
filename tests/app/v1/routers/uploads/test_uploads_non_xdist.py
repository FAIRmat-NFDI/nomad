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

import io
import os
import time
import zipfile
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from nomad.config import config
from nomad.config.models.config import NOMADFileSystem, NomadStorageGateway
from nomad.files import PublicUploadFiles
from nomad.processing import ProcessStatus
from tests.app.v1.routers.common import (
    assert_browser_download_headers,
    assert_response,
    perform_get,
)
from tests.app.v1.routers.uploads.test_uploads import (
    assert_file_upload_and_processing,
    assert_pagination,
)
from tests.test_files import example_file_mainfile_different_atoms

from ..test_entries import assert_archive_response
from .common import assert_entry


@pytest.mark.parametrize(
    'mode, user, expected_status_code',
    [pytest.param('multipart', 'user1', 409, id='conflict_in_concurrent_editing')],
)
@pytest.mark.xfail(reason='Flaky when run concurrently via pytest-xdist')
def test_editing_raw_file(
    proc_infra,
    auth_headers,
    upload_tokens,
    client,
    non_empty_processed,
    example_data_writeable,
    mode,
    user,
    expected_status_code,
):
    upload_id = non_empty_processed.upload_id
    target_path = 'examples_template'
    action = 'PUT'
    path = 'examples_template/template.json'
    archive_url = f'uploads/{upload_id}/archive/mainfile/{path}'
    target_url = f'uploads/{upload_id}/raw/{target_path}'
    user_auth = auth_headers[user]

    # Get an existing upload with entries
    response = perform_get(client, archive_url, user_auth=user_auth)
    assert response.status_code == 200
    response_json = response.json()
    entry_hash = response_json['data']['archive']['metadata']['entry_hash']

    # First edit
    query_args = {
        'file_name': 'example.json',
        'wait_for_processing': True,
        'include_archive': True,
        'entry_hash': entry_hash,
    }
    response, _ = assert_file_upload_and_processing(
        auth_headers,
        upload_tokens,
        client,
        action,
        target_url,
        mode,
        user,
        upload_id,
        example_file_mainfile_different_atoms,
        target_path,
        query_args,
        True,
        False,
        200,
        ProcessStatus.SUCCESS,
        ['examples_template/template.json'],
        False,
        True,
    )

    # Second edit on the same entry
    query_args = {
        'file_name': 'example.json',
        'wait_for_processing': True,
        'entry_hash': entry_hash,
    }
    response, _ = assert_file_upload_and_processing(
        auth_headers,
        upload_tokens,
        client,
        action,
        target_url,
        mode,
        user,
        upload_id,
        example_file_mainfile_different_atoms,
        target_path,
        query_args,
        True,
        False,
        expected_status_code,
        None,
        None,
        None,
        None,
    )

    # Get the updated archive
    response = perform_get(
        client, f'uploads/{upload_id}/archive/mainfile/{path}', user_auth=user_auth
    )
    assert response.status_code == 200
    response_json = response.json()
    entry_hash = response_json['data']['archive']['metadata']['entry_hash']

    # Edit with the updated hash code
    query_args = {
        'file_name': 'example.json',
        'wait_for_processing': True,
        'include_archive': True,
        'entry_hash': entry_hash,
    }
    response, _ = assert_file_upload_and_processing(
        auth_headers,
        upload_tokens,
        client,
        action,
        target_url,
        mode,
        user,
        upload_id,
        example_file_mainfile_different_atoms,
        target_path,
        query_args,
        True,
        False,
        200,
        ProcessStatus.SUCCESS,
        ['examples_template/template.json'],
        False,
        True,
    )

    # Get the updated archive
    response = perform_get(
        client, f'uploads/{upload_id}/archive/mainfile/{path}', user_auth=user_auth
    )
    assert response.status_code == 200
    response_json = response.json()
    entry_hash = response_json['data']['archive']['metadata']['entry_hash']

    # Somebody deletes the file faster
    response = client.delete(f'uploads/{upload_id}/raw/{path}', headers=user_auth)
    assert response.status_code == 200
    time.sleep(1)

    # Editing the file which was deleted by someone else
    query_args = {
        'file_name': 'example.json',
        'wait_for_processing': True,
        'entry_hash': entry_hash,
    }
    response, _ = assert_file_upload_and_processing(
        auth_headers,
        upload_tokens,
        client,
        action,
        target_url,
        mode,
        user,
        upload_id,
        example_file_mainfile_different_atoms,
        target_path,
        query_args,
        True,
        False,
        expected_status_code,
        None,
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    'kwargs',
    [
        pytest.param(
            dict(
                expected_data_len=1,
                expected_response={'processing_successful': 1, 'processing_failed': 0},
                expected_pagination={
                    'total': 1,
                    'page': 1,
                    'page_after_value': None,
                    'next_page_after_value': None,
                    'page_url': Any,
                    'next_page_url': None,
                    'prev_page_url': None,
                    'first_page_url': Any,
                },
            ),
            id='no-args',
        ),
        pytest.param(
            dict(
                upload_id='id_child_entries',
                expected_data_len=3,
                expected_response={'processing_successful': 3, 'processing_failed': 0},
                expected_pagination={
                    'total': 3,
                    'page': 1,
                    'page_after_value': None,
                    'next_page_after_value': None,
                    'page_url': Any,
                    'next_page_url': None,
                    'prev_page_url': None,
                    'first_page_url': Any,
                },
            ),
            id='upload-w-child-entries',
        ),
        pytest.param(
            dict(upload_id='silly_value', expected_status_code=404),
            id='invalid-upload_id',
        ),
        # Test access/permission
        pytest.param(
            dict(
                user=None,
                expected_status_code=200,
                upload_id='id_published',  # avoid falling back to `id_embargo`
                expected_data_len=10,
            ),
            id='published-visible-nologin',
        ),
        pytest.param(
            dict(user='invalid', expected_status_code=401), id='invalid-credentials'
        ),
        pytest.param(
            dict(user='user2', upload_id='id_embargo', expected_status_code=403),
            id='no-access-embargo',
        ),
        pytest.param(
            dict(user=None, upload_id='id_embargo', expected_status_code=403),
            id='nologin-embargo',
        ),
        pytest.param(dict(user='user0', expected_data_len=1), id='admin-access'),
        # Test pagination
        pytest.param(
            dict(
                upload_id='id_published',
                query_args={'page_size': 5},
                expected_data_len=5,
                expected_response={'processing_successful': 23, 'processing_failed': 0},
                expected_pagination={
                    'total': 23,
                    'page': 1,
                    'page_after_value': None,
                    'next_page_after_value': '4',
                    'order_by': 'mainfile',
                    'page_url': Any,
                    'next_page_url': Any,
                    'prev_page_url': None,
                    'first_page_url': Any,
                },
            ),
            id='pag-page-1',
        ),
        pytest.param(
            dict(
                upload_id='id_published',
                query_args={'page_size': 5, 'page': 1},
                expected_data_len=5,
                expected_response={'processing_successful': 23, 'processing_failed': 0},
                expected_pagination={
                    'total': 23,
                    'page': 1,
                    'page_after_value': None,
                    'next_page_after_value': '4',
                    'order_by': 'mainfile',
                    'page_url': Any,
                    'next_page_url': Any,
                    'prev_page_url': None,
                    'first_page_url': Any,
                },
            ),
            id='pag-page-1-by-page',
        ),
        pytest.param(
            dict(
                upload_id='id_published',
                query_args={'page_size': 10, 'page': 3},
                expected_data_len=3,
                expected_response={'processing_successful': 23, 'processing_failed': 0},
                expected_pagination={
                    'total': 23,
                    'page': 3,
                    'page_after_value': '19',
                    'next_page_after_value': None,
                    'order_by': 'mainfile',
                    'page_url': Any,
                    'next_page_url': None,
                    'prev_page_url': Any,
                    'first_page_url': Any,
                },
            ),
            id='pag-page-3-by-page',
        ),
        pytest.param(
            dict(
                upload_id='id_published',
                query_args={'page_size': 10, 'page_after_value': '19'},
                expected_data_len=3,
                expected_response={'processing_successful': 23, 'processing_failed': 0},
                expected_pagination={
                    'total': 23,
                    'page': 3,
                    'page_after_value': '19',
                    'next_page_after_value': None,
                    'order_by': 'mainfile',
                    'page_url': Any,
                    'next_page_url': None,
                    'prev_page_url': Any,
                    'first_page_url': Any,
                },
            ),
            id='pag-page-3-by-page_after_value',
        ),
        pytest.param(
            dict(
                upload_id='id_published',
                query_args={'page_size': 0},
                expected_data_len=0,
                expected_response={'processing_successful': 23, 'processing_failed': 0},
                expected_pagination={
                    'total': 23,
                    'page': 1,
                    'page_after_value': None,
                    'next_page_after_value': None,
                    'order_by': 'mainfile',
                    'page_url': Any,
                    'next_page_url': None,
                    'prev_page_url': None,
                    'first_page_url': None,
                },
            ),
            id='pag-page_size-zero',
        ),
        pytest.param(
            dict(query_args={'page_size': 1, 'page': 3}, expected_status_code=400),
            id='pag-out-of-rage-page',
        ),
        pytest.param(
            dict(
                query_args={'page_size': 1, 'page_after_value': '1'},
                expected_status_code=400,
            ),
            id='pag-out-of-rage-page_after_value',
        ),
        pytest.param(
            dict(
                upload_id='id_published',
                query_args={'page_size': 1, 'order_by': 'parser_name'},
                expected_data_len=1,
                expected_response={'processing_successful': 23, 'processing_failed': 0},
                expected_pagination={
                    'total': 23,
                    'page': 1,
                    'page_after_value': None,
                    'next_page_after_value': '0',
                    'order_by': 'parser_name',
                    'page_url': Any,
                    'next_page_url': Any,
                    'prev_page_url': None,
                    'first_page_url': Any,
                },
            ),
            id='pag-order_by-parser_name',
        ),
        pytest.param(
            dict(
                query_args={'page_size': 1, 'order_by': 'entry_id'},
                expected_status_code=422,
            ),
            id='pag-order_by-illegal',
        ),
        pytest.param(
            dict(
                query_args={'page_size': 1, 'page': 2, 'page_after_value': '0'},
                expected_status_code=422,
            ),
            id='pag-overspecified',
        ),
    ],
)
def test_get_upload_entries(auth_headers, client, mongo_module, example_data, kwargs):
    """
    Fetches the entries for a specific upload, by calling uploads/{upload_id}/entries,
    with the provided query paramters, and checks the result.
    """
    upload_id = kwargs.get('upload_id', 'id_embargo')
    user = kwargs.get('user', 'user1')
    query_args = kwargs.get('query_args', {})
    expected_status_code = kwargs.get('expected_status_code', 200)
    expected_data_len = kwargs.get('expected_data_len', 1)
    expected_response = kwargs.get('expected_response', {})
    expected_pagination = kwargs.get('expected_pagination', {})

    response = perform_get(
        client, f'uploads/{upload_id}/entries', auth_headers[user], **query_args
    )
    assert_response(response, expected_status_code)
    if expected_status_code == 200:
        response_json = response.json()
        response_data = response_json['data']

        if expected_data_len is not None:
            assert len(response_data) == expected_data_len

        for entry in response_data:
            assert_entry(entry)

        for key, value in expected_response.items():
            assert response_json.get(key, None) == value

        pagination = response_json['pagination']
        assert_pagination(pagination, expected_pagination)


@pytest.mark.parametrize(
    'upload_id, entry_id, user, expected_status_code',
    [
        pytest.param('id_embargo', 'id_embargo_1', 'user1', 200, id='ok'),
        pytest.param(
            'id_child_entries',
            'id_child_entries_child1',
            'user1',
            200,
            id='child-entry',
        ),
        # Test access/permission
        pytest.param('id_embargo', 'id_embargo_1', None, 401, id='no-credentials'),
        pytest.param(
            'id_embargo', 'id_embargo_1', 'invalid', 401, id='invalid-credentials'
        ),
        pytest.param('id_embargo', 'id_embargo_1', 'user2', 403, id='no-access'),
        pytest.param('id_embargo', 'id_embargo_1', 'user0', 200, id='admin-access'),
        # Test invalid upload/entry IDs
        pytest.param(
            'silly_value', 'id_embargo_1', 'user1', 404, id='invalid-upload_id'
        ),
        pytest.param('id_embargo', 'silly_value', 'user1', 404, id='invalid-entry_id'),
    ],
)
def test_get_upload_entry(
    auth_headers,
    client,
    mongo_module,
    example_data,
    upload_id,
    entry_id,
    user,
    expected_status_code,
):
    """
    Fetches an entry via a call to uploads/{upload_id}/entries/{entry_id} and checks it.
    """
    user_auth = auth_headers[user]
    response = perform_get(client, f'uploads/{upload_id}/entries/{entry_id}', user_auth)
    assert_response(response, expected_status_code)
    if expected_status_code == 200:
        response_json = response.json()
        assert response_json['entry_id'] == entry_id
        response_data = response_json['data']
        assert_entry(response_data)


@pytest.mark.parametrize(
    'user, upload_id, path, query_args, expected_status_code, expected_content, expected_file_metadata, expected_pagination',
    [
        pytest.param(
            'user1',
            'id_published',
            'test_content/subdir/silly_value',
            {},
            404,
            None,
            None,
            None,
            id='bad-path',
        ),
        pytest.param(
            'user1',
            'id_published',
            'test_content/subdir/test_entry_01',
            {},
            200,
            ['1.aux', '2.aux', '3.aux', '4.aux', 'mainfile.json'],
            None,
            {'total': 5},
            id='published-dir',
        ),
        pytest.param(
            'user1',
            'id_published',
            'test_content/subdir/test_entry_01',
            {'include_entry_info': True},
            200,
            ['1.aux', '2.aux', '3.aux', '4.aux', 'mainfile.json'],
            None,
            {'total': 5},
            id='published-dir-include_entry_info',
        ),
        pytest.param(
            'user1',
            'id_published',
            'test_content/subdir/test_entry_01',
            {'include_entry_info': True, 'page_size': 2, 'page': 3},
            200,
            ['mainfile.json'],
            None,
            {'total': 5},
            id='published-dir-include_entry_info-page3',
        ),
        pytest.param(
            'user1',
            'id_published',
            '',
            {},
            200,
            ['test_content'],
            None,
            {'total': 1},
            id='published-dir-root',
        ),
        pytest.param(
            'user1',
            'id_unpublished',
            'test_content/id_unpublished_1/',
            {},
            200,
            ['1.aux', '2.aux', '3.aux', '4.aux', 'mainfile.json'],
            None,
            {'total': 5},
            id='unpublished-dir',
        ),
        pytest.param(
            'user1',
            'id_unpublished',
            'test_content/id_unpublished_1/',
            {'page_size': 3, 'page': 1},
            200,
            ['1.aux', '2.aux', '3.aux'],
            None,
            {'total': 5, 'next_page_after_value': '2'},
            id='unpublished-dir-page1',
        ),
        pytest.param(
            'user1',
            'id_unpublished',
            'test_content/id_unpublished_1/',
            {'page_size': 2, 'page': 4},
            400,
            None,
            None,
            None,
            id='unpublished-dir-page-out-of-range',
        ),
        pytest.param(
            'user1',
            'id_unpublished',
            'test_content/id_unpublished_1/',
            {'include_entry_info': True},
            200,
            ['1.aux', '2.aux', '3.aux', '4.aux', 'mainfile.json'],
            None,
            {'total': 5},
            id='unpublished-dir-include_entry_info',
        ),
        pytest.param(
            'user1',
            'id_child_entries',
            'test_content',
            {'include_entry_info': True},
            200,
            ['1.aux', '2.aux', '3.aux', '4.aux', 'mainfile_w_children.json'],
            None,
            {'total': 5},
            id='dir-child-entries-include_entry_info',
        ),
        pytest.param(
            'user1',
            'id_unpublished',
            '',
            {},
            200,
            ['test_content'],
            None,
            {'total': 1},
            id='unpublished-dir-root',
        ),
        pytest.param(
            'user1',
            'id_unpublished',
            'test_content/id_unpublished_1/2.aux',
            {'include_entry_info': True},
            200,
            None,
            {'name': '2.aux', 'size': 8, 'entry_id': None, 'parser_name': None},
            None,
            id='unpublished-aux-file',
        ),
        pytest.param(
            'user1',
            'id_published',
            'test_content/subdir/test_entry_01/mainfile.json',
            {'include_entry_info': True},
            200,
            None,
            {
                'name': 'mainfile.json',
                'size': 3227,
                'entry_id': 'id_01',
                'parser_name': 'parsers/vasp',
            },
            None,
            id='published-main-file',
        ),
        pytest.param(
            'user2',
            'id_unpublished',
            'test_content/id_unpublished_1',
            {},
            403,
            None,
            None,
            None,
            id='unpublished-no-access',
        ),
        pytest.param(
            'user2',
            'id_embargo',
            'test_content/id_embargo_1',
            {},
            403,
            None,
            None,
            None,
            id='embargoed-no-access',
        ),
        pytest.param(
            'user2',
            'id_embargo_w_coauthor',
            'test_content/id_embargo_w_coauthor_1',
            {},
            200,
            ['1.aux', '2.aux', '3.aux', '4.aux', 'mainfile.json'],
            None,
            {'total': 5},
            id='embargoed-coauthor-access',
        ),
    ],
)
def test_get_upload_rawdir_path(
    auth_headers,
    client,
    example_data,
    user,
    upload_id,
    path,
    query_args,
    expected_status_code,
    expected_content,
    expected_file_metadata,
    expected_pagination,
):
    response = perform_get(
        client,
        f'uploads/{upload_id}/rawdir/{path}',
        user_auth=auth_headers[user],
        **query_args,
    )

    assert_response(response, expected_status_code)
    if expected_status_code == 200:
        data = response.json()
        assert data['path'] == (path.rstrip('/') or '')
        if expected_content is not None:
            dir_content_returned = data['directory_metadata']['content']
            assert [d['name'] for d in dir_content_returned] == expected_content, (
                'Incorrect list of files returned'
            )
            for d in dir_content_returned:
                if query_args.get('include_entry_info'):
                    assert (d.get('entry_id') is not None) == ('mainfile' in d['name'])
                    assert (d.get('parser_name') is not None) == (
                        'mainfile' in d['name']
                    )
                else:
                    assert 'entry_id' not in d and 'parser_name' not in d
        elif expected_file_metadata is not None:
            file_metadata_returned = data['file_metadata']
            for k, v in expected_file_metadata.items():
                if v is None:
                    assert k not in file_metadata_returned
                else:
                    assert file_metadata_returned.get(k) == v
        if expected_pagination is None:
            assert 'pagination' not in data
        else:
            pagination_returned = data['pagination']
            for k, v in expected_pagination.items():
                if v is None:
                    assert k not in pagination_returned
                else:
                    assert pagination_returned.get(k) == v


@pytest.mark.parametrize(
    'kwargs',
    [
        pytest.param(
            dict(
                expected_upload_ids=[
                    'id_embargo',
                    'id_embargo_w_coauthor',
                    'id_embargo_w_reviewer',
                    'id_unpublished',
                    'id_unpublished_w_coauthor',
                    'id_unpublished_w_reviewer',
                    'id_published',
                    'id_child_entries',
                    'id_processing',
                    'id_empty',
                ],
                expected_pagination={
                    'total': 10,
                    'page': 1,
                    'page_after_value': None,
                    'next_page_after_value': None,
                    'page_url': Any,
                    'next_page_url': None,
                    'prev_page_url': None,
                    'first_page_url': Any,
                },
            ),
            id='no-args',
        ),
        # Test access/permission
        pytest.param(
            dict(
                user='user2',
                expected_upload_ids=[
                    'id_embargo_w_coauthor',
                    'id_embargo_w_reviewer',
                    'id_unpublished_w_coauthor',
                    'id_unpublished_w_reviewer',
                ],
            ),
            id='user2',
        ),
        pytest.param(dict(user=None, expected_status_code=401), id='no-credentials'),
        pytest.param(
            dict(user='invalid', expected_status_code=401), id='invalid-credentials'
        ),
        # Test filter by `is_processing`
        pytest.param(
            dict(
                query_params={'is_processing': True, 'roles': 'main_author'},
                expected_upload_ids=['id_processing'],
            ),
            id='filter-is_processing-True',
        ),
        pytest.param(
            dict(
                query_params={'is_processing': False},
                expected_upload_ids=[
                    'id_embargo',
                    'id_embargo_w_coauthor',
                    'id_embargo_w_reviewer',
                    'id_unpublished',
                    'id_unpublished_w_coauthor',
                    'id_unpublished_w_reviewer',
                    'id_published',
                    'id_child_entries',
                    'id_empty',
                ],
            ),
            id='filter-is_processing-False',
        ),
        # Test filter by `is_published`
        pytest.param(
            dict(
                query_params={'is_published': True},
                expected_upload_ids=[
                    'id_embargo',
                    'id_embargo_w_coauthor',
                    'id_embargo_w_reviewer',
                    'id_published',
                ],
            ),
            id='filter-is_published-True',
        ),
        pytest.param(
            dict(
                query_params={'is_published': False},
                expected_upload_ids=[
                    'id_unpublished',
                    'id_unpublished_w_coauthor',
                    'id_unpublished_w_reviewer',
                    'id_child_entries',
                    'id_processing',
                    'id_empty',
                ],
            ),
            id='filter-is_published-False',
        ),
        # Test filter by `upload_id`
        pytest.param(
            dict(
                query_params={'upload_id': 'id_published'},
                expected_upload_ids=['id_published'],
            ),
            id='filter-upload_id-single',
        ),
        pytest.param(
            dict(
                query_params={'upload_id': ['id_published', 'id_embargo']},
                expected_upload_ids=['id_embargo', 'id_published'],
            ),
            id='filter-upload_id-multiple',
        ),
        # Test filter by `upload_name`
        pytest.param(
            dict(
                query_params={'upload_name': 'name_published'},
                expected_upload_ids=['id_published'],
            ),
            id='filter-upload_name-single',
        ),
        pytest.param(
            dict(
                query_params={'upload_name': ['name_published', 'name_embargo']},
                expected_upload_ids=['id_embargo', 'id_published'],
            ),
            id='filter-upload_name-multiple',
        ),
        pytest.param(
            dict(
                query_params={
                    'upload_name': '{"value":"name_published","type":"exact"}',
                },
                expected_upload_ids=['id_published'],
            ),
            id='filter-upload_name-explicit-exact-single',
        ),
        pytest.param(
            dict(
                query_params={
                    'upload_name': '{"value":"published","type":"fuzzy"}',
                },
                expected_upload_ids=['id_published'],
            ),
            id='filter-upload_name-fuzzy-single',
        ),
        pytest.param(
            dict(
                query_params={
                    'upload_name': '{"value":"name_","type":"fuzzy"}',
                },
                expected_upload_ids=[
                    'id_embargo',
                    'id_embargo_w_coauthor',
                    'id_embargo_w_reviewer',
                    'id_published',
                    'id_child_entries',
                ],
            ),
            id='filter-upload_name-fuzzy-multiple',
        ),
        pytest.param(
            dict(
                query_params={
                    'upload_name': [
                        '{"value":"published","type":"fuzzy"}',
                        '{"value":"embargo","type":"fuzzy"}',
                    ],
                },
                expected_upload_ids=[
                    'id_embargo',
                    'id_embargo_w_coauthor',
                    'id_embargo_w_reviewer',
                    'id_published',
                ],
            ),
            id='filter-upload_name-fuzzy-two-terms',
        ),
        pytest.param(
            dict(
                query_params={
                    'upload_name': '{"value":"does-not-exist","type":"fuzzy"}',
                },
                expected_upload_ids=[],
            ),
            id='filter-upload_name-fuzzy-no-match',
        ),
        pytest.param(
            dict(
                query_params={
                    'upload_name': '{"value":"   ","type":"fuzzy"}',
                },
                expected_status_code=400,
            ),
            id='filter-upload_name-fuzzy-empty',
        ),
        # Test pagination
        pytest.param(
            dict(
                query_params={'page_size': 2},
                expected_upload_ids=['id_embargo', 'id_embargo_w_coauthor'],
                expected_pagination={
                    'total': 10,
                    'page': 1,
                    'page_after_value': None,
                    'next_page_after_value': '1',
                    'page_url': Any,
                    'next_page_url': Any,
                    'prev_page_url': None,
                    'first_page_url': Any,
                },
            ),
            id='pag-page-1',
        ),
        pytest.param(
            dict(
                query_params={'page_size': 2, 'page': 2},
                expected_upload_ids=['id_embargo_w_reviewer', 'id_unpublished'],
                expected_pagination={
                    'total': 10,
                    'page': 2,
                    'page_after_value': '1',
                    'next_page_after_value': '3',
                    'page_url': Any,
                    'next_page_url': Any,
                    'prev_page_url': Any,
                    'first_page_url': Any,
                },
            ),
            id='pag-page-2',
        ),
        pytest.param(
            dict(
                query_params={'page_size': 4, 'page': 3},
                expected_upload_ids=['id_processing', 'id_empty'],
                expected_pagination={
                    'total': 10,
                    'page': 3,
                    'page_after_value': '7',
                    'next_page_after_value': None,
                    'page_url': Any,
                    'next_page_url': None,
                    'prev_page_url': Any,
                    'first_page_url': Any,
                },
            ),
            id='pag-page-3',
        ),
        pytest.param(
            dict(query_params={'page_size': 5, 'page': 3}, expected_status_code=400),
            id='pag-page-out-of-range',
        ),
        pytest.param(
            dict(
                query_params={'page_size': 2, 'order': 'desc'},
                expected_upload_ids=['id_empty', 'id_processing'],
                expected_pagination={
                    'total': 10,
                    'page': 1,
                    'page_after_value': None,
                    'next_page_after_value': '1',
                    'page_url': Any,
                    'next_page_url': Any,
                    'prev_page_url': None,
                    'first_page_url': Any,
                },
            ),
            id='pag-page-order-desc',
        ),
        pytest.param(
            dict(query_params={'order_by': 'upload_id'}, expected_status_code=422),
            id='pag-invalid-order_by',
        ),
        # Test `roles`
        pytest.param(
            dict(
                user='user2',
                query_params={'roles': 'coauthor'},
                expected_pagination={'total': 2},
            ),
            id='roles-coauthor',
        ),
        pytest.param(
            dict(
                user='user2',
                query_params={'roles': 'reviewer'},
                expected_pagination={'total': 2},
            ),
            id='roles-reviewer',
        ),
        pytest.param(
            dict(
                user='user1',
                query_params={'roles': 'main_author'},
                expected_pagination={'total': 10},
            ),
            id='roles-main-author',
        ),
        pytest.param(
            dict(
                user='user2',
                query_params={'roles': ['reviewer', 'coauthor']},
                expected_pagination={'total': 4},
            ),
            id='roles-multiple',
        ),
    ],
)
def test_get_uploads(auth_headers, client, mongo_module, example_data, kwargs):
    """Makes a get request to uploads in various different ways."""
    # Extract kwargs
    user = kwargs.get('user', 'user1')
    query_params = kwargs.get('query_params', {})
    expected_status_code = kwargs.get('expected_status_code', 200)
    expected_upload_ids = kwargs.get('expected_upload_ids', None)
    expected_pagination = kwargs.get('expected_pagination', {})
    # Api call
    response = perform_get(client, 'uploads', auth_headers[user], **query_params)
    # Verify result
    assert_response(response, expected_status_code)
    if expected_status_code == 200:
        response_json = response.json()
        response_data = response_json['data']

        if expected_upload_ids is not None:
            assert len(response_data) == len(expected_upload_ids), (
                f'Wrong number of records returned, expected {len(expected_upload_ids)}, got {len(response_data)}'
            )
            found_upload_ids = [upload['upload_id'] for upload in response_data]
            assert expected_upload_ids == found_upload_ids, (
                f'Wrong upload is list returned. Expected {repr(expected_upload_ids)}, got {repr(found_upload_ids)}.'
            )

        assert_pagination(response_json['pagination'], expected_pagination)


@pytest.mark.parametrize(
    'args, expected_status_code, expected_content',
    [
        pytest.param(
            dict(
                user='user1',
                upload_id='id_published',
            ),
            200,
            {'test_content/subdir/test_entry_01/mainfile.json': 'method'},
            id='published-file',
        ),
        pytest.param(
            dict(user='user1', upload_id='id_unpublished'),
            400,
            None,
            id='unpublished-file',
        ),
        pytest.param(
            dict(user='user2', upload_id='id_embargo'),
            403,
            None,
            id='embargo-file',
        ),
        pytest.param(
            dict(user='user1', upload_id='silly_value'),
            404,
            None,
            id='bad-upload-id',
        ),
    ],
)
def test_get_upload_raw(
    auth_headers,
    client,
    example_data,
    args,
    expected_status_code,
    expected_content,
):
    user = args['user']
    upload_id = args['upload_id']
    user_auth = auth_headers[user]

    response = perform_get(client, f'uploads/{upload_id}/raw', user_auth=user_auth)

    assert_response(response, expected_status_code)
    if expected_status_code == 200:
        mime_type = response.headers.get('Content-Type')
        assert mime_type == 'application/zip'
        with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
            for name, content in expected_content.items():
                with zip_file.open(name, 'r') as f:
                    file_content = f.read()
                    assert content.encode() in file_content


def _assert_presigned_gateway_redirect(
    location: str,
    *,
    path_prefix: str,
    user_id: str,
    expires: int = 24 * 60 * 60,
    query_params: dict[str, str] | None = None,
) -> None:
    parsed = urlparse(location)
    assert parsed.scheme == 'https'
    assert parsed.path.startswith(path_prefix)
    qs = parse_qs(parsed.query)
    assert qs['X-Amz-Algorithm'] == ['AWS4-HMAC-SHA256']
    assert qs.get('X-Amz-Credential')
    assert qs.get('X-Amz-Date')
    assert qs['X-Amz-Expires'] == [str(expires)]
    assert qs.get('X-Amz-Signature')
    assert qs['user_id'] == [user_id]
    if query_params:
        for key, value in query_params.items():
            assert qs[key] == [value]


def test_get_upload_raw_redirects_to_signed_remote_url(
    monkeypatch, auth_headers, client, example_data
):
    class RemoteFS:
        protocol = 's3'

        def url(self, path, expires):
            assert expires == 24 * 60 * 60
            return f'https://storage.example.test/{path}?signature=test'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'uploads/id_published/raw',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers['location'].startswith('https://storage.example.test/')


def test_get_upload_raw_redirects_with_public_endpoint_url(
    monkeypatch, auth_headers, client, example_data
):
    class RemoteFS:
        protocol = 's3'

    class SigningFS:
        def url(self, path, expires):
            return f'https://nomad-lab.eu/files/signed/{path}?expires={expires}'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects,
        'public_endpoint_url',
        'https://nomad-lab.eu/files',
    )
    monkeypatch.setattr(NOMADFileSystem, 'signing_fs', property(lambda _: SigningFS()))
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'uploads/id_published/raw',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers['location'].startswith('https://nomad-lab.eu/files/signed/')


def test_get_upload_whole_zip_redirects_to_gateway(
    monkeypatch, auth_headers, client, example_data, user1
):
    class RemoteFS:
        protocol = 's3'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects,
        'public_endpoint_url',
        'https://nomad-lab.eu/files',
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'zip', True
    )
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'uploads/id_published/raw',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 307
    _assert_presigned_gateway_redirect(
        response.headers['location'],
        path_prefix='/files/zip/id_published',
        user_id=user1.user_id,
    )


def test_get_upload_whole_zip_gateway_does_not_require_raw_files(
    monkeypatch, auth_headers, client, example_data, user1
):
    class RemoteFS:
        protocol = 's3'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects,
        'public_endpoint_url',
        'https://nomad-lab.eu/files',
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'zip', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'raw_files', False
    )
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'uploads/id_published/raw',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 307
    _assert_presigned_gateway_redirect(
        response.headers['location'],
        path_prefix='/files/zip/id_published',
        user_id=user1.user_id,
    )


def test_get_upload_whole_zip_raw_files_does_not_enable_gateway_zip(
    monkeypatch, auth_headers, client, example_data
):
    class RemoteFS:
        protocol = 's3'

        def url(self, path, expires):
            return f'https://storage.example.test/{path}?signature=test'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'zip', False
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'raw_files', True
    )
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'uploads/id_published/raw',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers['location'].startswith('https://storage.example.test/')
    assert '/files/zip/' not in response.headers['location']


def test_get_upload_raw_compressed_root_redirects_to_gateway_zip(
    monkeypatch, auth_headers, client, example_data, user1
):
    class RemoteFS:
        protocol = 's3'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects,
        'public_endpoint_url',
        'https://nomad-lab.eu/files',
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'zip', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'raw_files', False
    )
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'uploads/id_published/raw/?compress=true',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 307
    _assert_presigned_gateway_redirect(
        response.headers['location'],
        path_prefix='/files/zip/id_published',
        user_id=user1.user_id,
        query_params={'compress': 'true'},
    )


def test_get_upload_raw_compressed_root_redirects_to_s3(
    monkeypatch, auth_headers, client, example_data
):
    class RemoteFS:
        protocol = 's3'

        def url(self, path, expires):
            assert expires == 24 * 60 * 60
            return f'https://storage.example.test/{path}?signature=test'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', False
    )
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'uploads/id_published/raw/?compress=true',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers['location'].startswith('https://storage.example.test/')


def test_get_upload_raw_file_redirects_to_gateway(
    monkeypatch, auth_headers, client, example_data, user1
):
    class RemoteFS:
        protocol = 's3'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects,
        'public_endpoint_url',
        'https://nomad-lab.eu/files',
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'raw_files', True
    )
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'uploads/id_published/raw/1_1.aux?offset=100&length=200',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 307
    _assert_presigned_gateway_redirect(
        response.headers['location'],
        path_prefix='/files/file/id_published/1_1.aux',
        user_id=user1.user_id,
        query_params={'offset': '100', 'length': '200'},
    )


def test_get_upload_raw_file_overwrites_client_user_id(
    monkeypatch, auth_headers, client, example_data, user1
):
    class RemoteFS:
        protocol = 's3'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects,
        'public_endpoint_url',
        'https://nomad-lab.eu/files',
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'raw_files', True
    )
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'uploads/id_published/raw/1_1.aux?user_id=victim&offset=100',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 307
    _assert_presigned_gateway_redirect(
        response.headers['location'],
        path_prefix='/files/file/id_published/1_1.aux',
        user_id=user1.user_id,
        query_params={'offset': '100'},
    )
    assert parse_qs(urlparse(response.headers['location']).query)['user_id'] != [
        'victim'
    ]


def test_get_upload_whole_zip_anonymous_mints_sentinel(
    monkeypatch, client, example_data
):
    class RemoteFS:
        protocol = 's3'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects,
        'public_endpoint_url',
        'https://nomad-lab.eu/files',
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'zip', True
    )
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'uploads/id_published/raw',
        follow_redirects=False,
    )

    assert response.status_code == 307
    _assert_presigned_gateway_redirect(
        response.headers['location'],
        path_prefix='/files/zip/id_published',
        user_id=NomadStorageGateway.ANONYMOUS_USER_ID,
    )


def test_get_entry_raw_redirects_to_gateway(
    monkeypatch, auth_headers, client, example_data, user1
):
    class RemoteFS:
        protocol = 's3'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects,
        'public_endpoint_url',
        'https://nomad-lab.eu/files',
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'raw_files', True
    )
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'entries/id_01/raw',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 307
    _assert_presigned_gateway_redirect(
        response.headers['location'],
        path_prefix='/files/zip/id_published/',
        user_id=user1.user_id,
    )


def test_get_entry_archive_redirects_to_gateway(
    monkeypatch, auth_headers, client, example_data, user1
):
    class RemoteFS:
        protocol = 's3'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects,
        'public_endpoint_url',
        'https://nomad-lab.eu/files',
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'archives', True
    )
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'entries/id_01/archive',
        headers={**auth_headers['user1'], 'Accept': 'application/msgpack'},
        follow_redirects=False,
    )

    assert response.status_code == 307
    _assert_presigned_gateway_redirect(
        response.headers['location'],
        path_prefix='/files/archive/id_published/id_01',
        user_id=user1.user_id,
        query_params={'format': 'msgpack'},
    )


def test_get_entry_archive_download_redirects_to_gateway(
    monkeypatch, auth_headers, client, example_data, user1
):
    class RemoteFS:
        protocol = 's3'

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects,
        'public_endpoint_url',
        'https://nomad-lab.eu/files',
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'archives', True
    )
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'public'))

    response = client.get(
        'entries/id_01/archive/download',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 307
    _assert_presigned_gateway_redirect(
        response.headers['location'],
        path_prefix='/files/archive/id_published/id_01',
        user_id=user1.user_id,
        query_params={'format': 'json'},
    )


def test_get_embargoed_upload_raw_does_not_redirect(
    monkeypatch, auth_headers, client, example_data
):
    class RemoteFS:
        protocol = 's3'

        def open(self, path, mode):
            return io.BytesIO(b'restricted raw ZIP')

        def url(self, path, expires):
            raise AssertionError('embargoed downloads must not use signed URLs')

    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(PublicUploadFiles, 'storage_fs', property(lambda _: RemoteFS()))
    monkeypatch.setattr(PublicUploadFiles, 'access', property(lambda _: 'restricted'))

    response = client.get(
        'uploads/id_embargo/raw',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.content == b'restricted raw ZIP'


def test_get_upload_raw_file_gateway_disabled_streams_locally(
    monkeypatch, auth_headers, client, example_data
):
    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', False
    )

    response = client.get(
        'uploads/id_published/raw/test_content/subdir/test_entry_01/mainfile.json',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert not response.is_redirect


def test_get_entry_archive_gateway_disabled_serves_json(
    monkeypatch, auth_headers, client, example_data
):
    monkeypatch.setattr(config.fs.public_fs.redirects, 'enabled', True)
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'enabled', True
    )
    monkeypatch.setattr(
        config.fs.public_fs.redirects.nomad_storage_gateway, 'archives', False
    )

    response = client.get(
        'entries/id_01/archive',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert not response.is_redirect
    assert 'data' in response.json()


@pytest.mark.parametrize(
    'args, expected_status_code, expected_mime_type, expected_content',
    [
        # Test unpublished file
        pytest.param(
            dict(
                user='user1',
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/1.aux',
            ),
            200,
            'text/plain; charset=utf-8',
            'content',
            id='unpublished-file',
        ),
        pytest.param(
            dict(
                user='user1',
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/1.aux',
                ignore_mime_type=True,
            ),
            200,
            'application/octet-stream',
            'content',
            id='unpublished-file-ignore_mime_type',
        ),
        pytest.param(
            dict(
                user='user2',
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/1.aux',
            ),
            403,
            None,
            None,
            id='unpublished-file-unauthorized',
        ),
        pytest.param(
            dict(
                user='user0',
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/1.aux',
            ),
            200,
            'text/plain; charset=utf-8',
            'content',
            id='unpublished-file-admin-auth',
        ),
        # Test published file
        pytest.param(
            dict(
                user='user1',
                upload_id='id_published',
                path='test_content/subdir/test_entry_01/mainfile.json',
            ),
            200,
            'text/plain; charset=utf-8',
            'method',
            id='published-file',
        ),
        pytest.param(
            dict(
                user='user1',
                upload_id='id_published',
                path='test_content/subdir/test_entry_01/mainfile.json',
                ignore_mime_type=True,
            ),
            200,
            'application/octet-stream',
            'method',
            id='published-file-ignore_mime_type',
        ),
        pytest.param(
            dict(
                user='user0',
                upload_id='id_published',
                path='test_content/subdir/test_entry_01/1.aux',
            ),
            200,
            'text/plain; charset=utf-8',
            'content',
            id='published-file-admin-auth',
        ),
        # Test compression
        pytest.param(
            dict(
                user='user1',
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/1.aux',
                compress=True,
            ),
            200,
            'application/zip',
            'content',
            id='unpublished-file-compressed',
        ),
        pytest.param(
            dict(
                user='user1',
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/',
                compress=True,
            ),
            200,
            'application/zip',
            ['1.aux', '2.aux', '3.aux', '4.aux', 'mainfile.json'],
            id='unpublished-dir-compressed',
        ),
        pytest.param(
            dict(user='user1', upload_id='id_unpublished', path='', compress=True),
            200,
            'application/zip',
            [
                'test_content',
                'test_content/id_unpublished_1/1.aux',
                'test_content/id_unpublished_1/mainfile.json',
            ],
            id='unpublished-dir-compressed-root',
        ),
        pytest.param(
            dict(
                user='user1',
                upload_id='id_published',
                path='test_content/subdir/test_entry_01/1.aux',
                compress=True,
            ),
            200,
            'application/zip',
            'content',
            id='published-file-compressed',
        ),
        pytest.param(
            dict(
                user='user1',
                upload_id='id_published',
                path='test_content/subdir/test_entry_01',
                compress=True,
            ),
            200,
            'application/zip',
            ['1.aux', '2.aux', '3.aux', '4.aux', 'mainfile.json'],
            id='published-dir-compressed',
        ),
        pytest.param(
            dict(user='user1', upload_id='id_published', path='', compress=True),
            200,
            'application/zip',
            ['test_content', 'test_content/subdir/test_entry_01/1.aux'],
            id='published-dir-compressed-root',
        ),
        # Test failures
        pytest.param(
            dict(
                user='user1',
                upload_id='silly_value',
                path='test_content/subdir/test_entry_01/1.aux',
                compress=True,
            ),
            404,
            None,
            None,
            id='bad-upload-id',
        ),
        pytest.param(
            dict(
                user='user1',
                upload_id='id_published',
                path='test_content/silly_name',
                compress=True,
            ),
            404,
            None,
            None,
            id='bad-path',
        ),
        # Test query param: offset and length
        pytest.param(
            dict(
                user='user1',
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/1.aux',
                offset=2,
            ),
            200,
            'application/octet-stream',
            'ntent\n',
            id='unpublished-file-offset',
        ),
        pytest.param(
            dict(
                user='user1',
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/1.aux',
                offset=2,
                length=4,
            ),
            200,
            'application/octet-stream',
            'nten',
            id='unpublished-file-offset-and-length',
        ),
        pytest.param(
            dict(
                user='user1',
                upload_id='id_published',
                path='test_content/subdir/test_entry_01/1.aux',
                offset=2,
            ),
            200,
            'application/octet-stream',
            'ntent\n',
            id='published-file-offset',
        ),
        pytest.param(
            dict(
                user='user1',
                upload_id='id_published',
                path='test_content/subdir/test_entry_01/1.aux',
                offset=2,
                length=4,
            ),
            200,
            'application/octet-stream',
            'nten',
            id='published-file-offset-and-length',
        ),
        # Test invalid query parameters
        pytest.param(
            dict(
                user='user1',
                upload_id='id_published',
                path='test_content/subdir/test_entry_01/1.aux',
                offset=-3,
            ),
            400,
            None,
            None,
            id='invalid-offset',
        ),
        pytest.param(
            dict(
                user='user1',
                upload_id='id_published',
                path='test_content/subdir/test_entry_01/1.aux',
                offset=3,
                length=-3,
            ),
            400,
            None,
            None,
            id='invalid-length',
        ),
        # Test access/permission
        pytest.param(
            dict(
                user=None,
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/1.aux',
            ),
            401,
            None,
            None,
            id='no-credentials',
        ),
        pytest.param(
            dict(
                user='invalid',
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/1.aux',
            ),
            401,
            None,
            None,
            id='invalid-credentials',
        ),
        pytest.param(
            dict(
                user='user2',
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/1.aux',
            ),
            403,
            None,
            None,
            id='no-access',
        ),
        pytest.param(
            dict(
                user='user0',
                upload_id='id_unpublished',
                path='test_content/id_unpublished_1/1.aux',
            ),
            200,
            'text/plain; charset=utf-8',
            'content',
            id='admin-access',
        ),
    ],
)
def test_get_upload_raw_path(
    auth_headers,
    client,
    example_data,
    args,
    expected_status_code,
    expected_mime_type,
    expected_content,
):
    user = args['user']
    upload_id = args['upload_id']
    path = args['path']
    accept = args.get('accept', None)
    compress = args.get('compress', None)
    re_pattern = args.get('re_pattern', None)
    offset = args.get('offset', None)
    length = args.get('length', None)
    ignore_mime_type = args.get('ignore_mime_type', None)
    query_args = dict(
        ignore_mime_type=ignore_mime_type,
        compress=compress,
        re_pattern=re_pattern,
        offset=offset,
        length=length,
    )

    response = perform_get(
        client,
        f'uploads/{upload_id}/raw/{path}',
        user_auth=auth_headers[user],
        accept=accept,
        **query_args,
    )

    assert_response(response, expected_status_code)
    if expected_status_code == 200:
        mime_type = response.headers.get('Content-Type')
        if not path:
            expected_filename = upload_id + '.zip'
        else:
            expected_filename = os.path.basename(path.rstrip('/')) + (
                '.zip' if mime_type == 'application/zip' else ''
            )
        assert_browser_download_headers(response, expected_mime_type, expected_filename)
        if mime_type == 'application/zip':
            if expected_content:
                with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
                    if isinstance(expected_content, str):
                        # Single file - check content
                        with zip_file.open(os.path.basename(path), 'r') as f:
                            file_content = f.read()
                            assert expected_content.encode() in file_content
                    else:
                        assert isinstance(expected_content, list)
                        # Directory - check content
                        zip_paths = zip_file.namelist()
                        # Check: only root elements specified in expected_content are allowed
                        for zip_path in zip_paths:
                            first_path_element = zip_path.split(os.path.sep)[0]
                            assert first_path_element in expected_content, (
                                f'Unexpected entry found in the zip root folder: {first_path_element}'
                            )
                        # Check: all elements specified in expected_content must exist
                        for expected_path in expected_content:
                            found = False
                            for zip_path in zip_paths:
                                if zip_path == expected_path or zip_path.startswith(
                                    expected_path + os.path.sep
                                ):
                                    found = True
                                    break
                            assert found, (
                                f'Missing expected path in zip file: {expected_path}'
                            )
        else:
            if expected_content:
                if offset is not None:
                    assert response.text == expected_content, (
                        'Wrong content (offset and length)'
                    )
                else:
                    assert expected_content in response.text, (
                        'Expected content not found'
                    )


def test_get_published_raw_file_reuses_and_closes_one_zip_filesystem(
    monkeypatch, auth_headers, client, example_data
):
    """Metadata lookup and streaming share one ZIP parse for a file response."""
    opened = 0
    closed = 0
    original_ensure = PublicUploadFiles._ensure_zip_index
    original_close = PublicUploadFiles.close

    def tracked_ensure(self):
        nonlocal opened
        first_open = self._zip is None
        result = original_ensure(self)
        if first_open:
            opened += 1
        return result

    def tracked_close(self):
        nonlocal closed
        had_index = self._zip is not None
        original_close(self)
        if had_index:
            closed += 1

    monkeypatch.setattr(PublicUploadFiles, '_ensure_zip_index', tracked_ensure)
    monkeypatch.setattr(PublicUploadFiles, 'close', tracked_close)

    response = client.get(
        'uploads/id_published/raw/test_content/subdir/test_entry_01/mainfile.json',
        headers=auth_headers['user1'],
    )

    assert response.status_code == 200
    assert response.content
    assert opened == 1
    assert closed == 1


@pytest.mark.parametrize(
    'upload_id, mainfile, user, status_code',
    [
        pytest.param(
            'id_published',
            'test_content/subdir/test_entry_01/mainfile.json',
            None,
            200,
            id='published',
        ),
        pytest.param(
            'id_child_entries',
            'test_content/mainfile_w_children.json',
            'user1',
            200,
            id='entry-w-child-entries',
        ),
        # Test failures
        pytest.param(
            'id_published',
            'test_content/doesnotexist.json',
            None,
            404,
            id='bad-mainfile',
        ),
        pytest.param(
            'id_doesnotexist',
            'test_content/subdir/test_entry_01/mainfile.json',
            None,
            404,
            id='bad-upload-id',
        ),
        # Test access/permission
        pytest.param(
            'id_unpublished',
            'test_content/id_unpublished_1/mainfile.json',
            None,
            401,
            id='unpublished-nologin',
        ),
        pytest.param(
            'id_unpublished',
            'test_content/id_unpublished_1/mainfile.json',
            'user2',
            403,
            id='unpublished-login-no-access',
        ),
        pytest.param(
            'id_unpublished',
            'test_content/id_unpublished_1/mainfile.json',
            'user1',
            200,
            id='ok-with-access',
        ),
    ],
)
def test_get_upload_entry_archive_mainfile(
    auth_headers,
    client,
    example_data,
    upload_id: str,
    mainfile: str,
    user: str,
    status_code: int,
):
    response = client.get(
        f'uploads/{upload_id}/archive/mainfile/{mainfile}', headers=auth_headers[user]
    )
    assert_response(response, status_code)
    if status_code == 200:
        assert_archive_response(response.json())


@pytest.mark.parametrize(
    'upload_id, entry_id, user, status_code',
    [
        pytest.param('id_published', 'id_01', None, 200, id='published'),
        pytest.param(
            'id_child_entries',
            'id_child_entries_child1',
            'user1',
            200,
            id='child-entry',
        ),
        # Test failures
        pytest.param('id_published', 'doesnotexist', None, 404, id='bad-entry-id'),
        pytest.param('id_doesnotexist', 'id_01', None, 404, id='bad-upload-id'),
        # Test access/permission
        pytest.param(
            'id_unpublished', 'id_unpublished_1', None, 401, id='unpublished-nologin'
        ),
        pytest.param('id_unpublished', 'id_unpublished_1', 'user1', 200, id='auth'),
    ],
)
def test_get_upload_entry_archive(
    auth_headers,
    client,
    example_data,
    upload_id: str,
    entry_id: str,
    user: str,
    status_code: int,
):
    url = f'uploads/{upload_id}/archive/{entry_id}'
    response = client.get(url, headers=auth_headers[user])
    assert_response(response, status_code)
    if status_code == 200:
        assert_archive_response(response.json())
