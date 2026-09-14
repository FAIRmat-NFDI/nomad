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

import csv
import io
import json
import zipfile
from typing import Literal
from urllib.parse import urlencode

import pytest

from nomad.app.v1.routers import entries as entries_router
from nomad.metainfo.elasticsearch_extension import entry_type, schema_separator
from nomad.utils.exampledata import ExampleData
from tests.test_files import append_raw_files, example_mainfile_contents  # noqa: F401
from tests.variables import python_schema_name

from .common import (
    aggregation_exclude_from_search_test_parameters,
    aggregation_test_parameters,
    aggregation_test_parameters_default,
    assert_aggregation_response,
    assert_aggregations,
    assert_base_metadata_response,
    assert_browser_download_headers,
    assert_metadata_response,
    assert_pagination,
    assert_query_response,
    assert_required,
    assert_response,
    get_query_test_parameters,
    owner_test_parameters,
    pagination_test_parameters,
    perform_entries_metadata_test,
    perform_owner_test,
    post_query_test_parameters,
)

# These are the tests for all API operations below ``entries``. The tests are organized
# using the following type of methods: fixtures, ``perfrom_*_test``, ``assert_*``, and
# ``test_*``. While some ``test_*`` methods test individual API operations, some
# test methods will test multiple API operations that use common aspects like
# supporting queries, pagination, or the owner parameter. The test methods will use
# ``perform_*_test`` methods as an parameter. Similarely, the ``assert_*`` methods allow
# to assert for certain aspects in the responses.


def perform_entries_raw_test(
    client,
    headers={},
    query={},
    owner=None,
    files={},
    total=-1,
    files_per_entry=5,
    status_code=200,
    http_method='get',
):
    if isinstance(total, int):
        total_entries = total_mainfiles = total
    else:
        total_entries, total_mainfiles = total

    if http_method == 'post':
        body = {'query': query, 'files': files}
        if owner is not None:
            body['owner'] = owner
        response = client.post('entries/raw/query', headers=headers, json=body)

    elif http_method == 'get':
        params = dict(**query)
        params.update(**files)
        if owner is not None:
            params['owner'] = owner
        response = client.get(
            f'entries/raw?{urlencode(params, doseq=True)}', headers=headers
        )

    else:
        pytest.fail(f'Invalid HTTP method {http_method}')

    assert_response(response, status_code)
    if status_code == 200:
        assert_raw_zip_file(
            response,
            files=total_mainfiles * files_per_entry + 1,
            manifest_entries=total_entries,
            compressed=files.get('compress', False),
        )


def perform_entries_rawdir_test(
    client,
    owner=None,
    headers={},
    status_code=200,
    total=None,
    http_method='get',
    files_per_entry=-1,
    **kwargs,
):
    if http_method == 'get':
        params = {}
        if owner is not None:
            params['owner'] = owner
        for value in kwargs.values():
            params.update(**value)
        response = client.get(
            f'entries/rawdir?{urlencode(params, doseq=True)}', headers=headers
        )

    elif http_method == 'post':
        body = dict(**kwargs)
        if owner is not None:
            body['owner'] = owner
        response = client.post('entries/rawdir/query', headers=headers, json=body)

    else:
        pytest.fail(f'Invalid HTTP method {http_method}')

    response_json = assert_base_metadata_response(response, status_code=status_code)

    if response_json is None:
        return None

    assert 'pagination' in response_json
    if total is not None:
        assert response_json['pagination']['total'] == total

    assert_entries_rawdir_response(response_json, files_per_entry=files_per_entry)

    return response_json


def perform_entries_archive_download_test(
    client,
    headers={},
    query={},
    owner=None,
    required=None,
    files={},
    total=-1,
    status_code=200,
    http_method='get',
):
    if http_method == 'post':
        body = {
            'query': query,
            'required': '*' if required is None else required,
            'files': files,
        }
        if owner is not None:
            body['owner'] = owner
        response = client.post(
            'entries/archive/download/query', headers=headers, json=body
        )

    elif http_method == 'get':
        params = dict(**query)
        params.update(**files)
        if owner is not None:
            params['owner'] = owner
        response = client.get(
            f'entries/archive/download?{urlencode(params, doseq=True)}',
            headers=headers,
        )

    else:
        pytest.fail(f'Invalid HTTP method {http_method}')

    assert_response(response, status_code)
    if status_code == 200:
        assert_archive_zip_file(
            response, total=total, compressed=files.get('compress', False)
        )


def perform_entries_archive_test(
    client, headers={}, total=-1, status_code=200, http_method='get', **kwargs
):
    if http_method == 'get':
        assert 'required' not in kwargs
        params = {}
        if 'owner' in kwargs:
            params.update(owner=kwargs['owner'])
        if 'query' in kwargs:
            params.update(**kwargs['query'])
        if 'pagination' in kwargs:
            params.update(**kwargs['pagination'])
        response = client.get(
            f'entries/archive?{urlencode(params, doseq=True)}', headers=headers
        )

    else:
        body = dict(**kwargs)
        response = client.post('entries/archive/query', headers=headers, json=body)

    assert_response(response, status_code)
    if status_code != 200:
        return None

    json_response = response.json()
    if total >= 0:
        assert json_response['pagination']['total'] == total
    for archive_data in json_response['data']:
        required = kwargs.get('required', '*')
        archive = archive_data['archive']
        if required == '*':
            for key in ['metadata', 'run']:
                assert key in archive
        else:
            for key in required:
                assert key in archive
            for key in archive:
                if key != 'm_ref_archives':
                    assert key in required

    return json_response


def assert_raw_zip_file(
    response, files: int = -1, manifest_entries: int = -1, compressed: bool = False
):
    manifest_keys = ['entry_id', 'upload_id', 'mainfile']

    assert len(response.content) > 0
    assert_browser_download_headers(response, 'application/zip', 'raw_files.zip')
    with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
        with zip_file.open('manifest.json', 'r') as f:
            manifest = json.load(f)

        with_missing_files = any(entry['entry_id'] == 'id_02' for entry in manifest)
        with_overlapping_files = any(entry['entry_id'] == 'id_11' for entry in manifest)

        assert zip_file.testzip() is None
        zip_files = set(zip_file.namelist())
        if files >= 0:
            if with_missing_files or with_overlapping_files:
                assert (
                    files
                    - (5 if with_missing_files else 0)
                    - (4 if with_overlapping_files else 0)
                    <= len(zip_files)
                    < files
                )
            else:
                assert len(zip_files) == files
            assert (
                zip_file.getinfo(zip_file.namelist()[0]).compress_type > 0
            ) == compressed

        for path in zip_files:
            assert path == 'manifest.json' or path.startswith('id_')

        if manifest_entries >= 0:
            assert len(manifest) == manifest_entries

            for entry in manifest:
                if 'mainfile' in manifest:
                    manifest['mainfile'] in zip_files
                assert all(key in entry for key in manifest_keys)
                assert all(key in manifest_keys for key in entry)


def assert_entries_rawdir_response(response_json, files_per_entry: int = -1):
    assert 'data' in response_json
    for entry in response_json['data']:
        assert_entry_rawdir(entry, files_per_entry)


def assert_entry_rawdir_response(response_json, files_per_entry: int = -1):
    for key in ['entry_id', 'data']:
        assert key in response_json
    assert_entry_rawdir(response_json['data'], files_per_entry=files_per_entry)


def assert_entry_rawdir(data, files_per_entry: int = -1):
    for key in ['upload_id', 'entry_id', 'files']:
        assert key in data
    files = data['files']
    if files_per_entry >= 0:
        if data['entry_id'] == 'id_02':
            # missing files
            assert len(files) == 0
        elif data['entry_id'] in ['id_10', 'id_11']:
            # overlapping files
            assert len(files) == files_per_entry + 1
        else:
            assert len(files) == files_per_entry
    for file_ in files:
        assert 'size' in file_
        assert 'path' in file_


def assert_archive_zip_file(response, total: int = -1, compressed: bool = False):
    manifest_keys = ['entry_id', 'upload_id', 'path', 'parser_name']

    assert len(response.content) > 0
    assert_browser_download_headers(response, 'application/zip', 'archives.zip')
    with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
        assert zip_file.testzip() is None
        with zip_file.open('manifest.json', 'r') as f:
            manifest = json.load(f)

        with_missing_files = any(entry['entry_id'] == 'id_02' for entry in manifest)

        zip_files = set(zip_file.namelist())
        if total >= 0:
            assert len(zip_files) == total + 1 - (1 if with_missing_files else 0)
            assert (
                zip_file.getinfo(zip_file.namelist()[0]).compress_type > 0
            ) == compressed

        for path in zip_files:
            assert path.endswith('.json')
            with zip_file.open(path, 'r') as f:
                data = json.load(f)
                if path != 'manifest.json':
                    for key in ['entry_id', 'archive']:
                        assert key in data
                    assert_archive(data['archive'])

        if total >= 0:
            assert len(manifest) == total

            for entry in manifest:
                if 'mainfile' in manifest:
                    manifest['path'] in zip_files
                assert all(key in entry for key in manifest_keys)
                assert all(key in manifest_keys for key in entry)


def assert_archive_response(response_json, required=None):
    for key in ['entry_id', 'required', 'data']:
        assert key in response_json
    if required is not None:
        assert required == response_json['required']
    for key in ['entry_id', 'upload_id', 'parser_name', 'archive']:
        assert key in response_json['data']
    assert_archive(response_json['data']['archive'], required=required)


def assert_archive(archive, required=None):
    for key in ['metadata']:
        assert key in archive


PROGRAM_NAME: str = 'results.method.simulation.program_name'


def test_entries_all_metrics(client, example_data):
    aggregations = {
        quantity: {
            'terms': {
                'quantity': quantity,
                'metrics': [metric for metric in entry_type.metrics],
            }
        }
        for quantity in entry_type.quantities
        if entry_type.quantities[quantity].annotation.aggregatable
    }
    response_json = perform_entries_metadata_test(
        client, aggregations=aggregations, status_code=200, http_method='post'
    )
    for name, agg in aggregations.items():
        assert_aggregations(response_json, name, agg['terms'])


@pytest.mark.parametrize(
    'aggregation, total, size, status_code, user',
    aggregation_test_parameters_default(),
)
def test_entries_aggregations(
    auth_headers, client, example_data, aggregation, total, size, status_code, user
):
    """Tests aggregation calls for regular statically mapped quantities."""
    assert_aggregation_response(
        auth_headers,
        client,
        aggregation,
        total,
        size,
        status_code,
        user,
    )


@pytest.mark.parametrize(
    'aggregation, total, size, status_code, user',
    aggregation_test_parameters(
        str={
            'name': f'data.name{schema_separator}{python_schema_name}',
            'total': 15,
            'size': 10,
        },
        empty={'name': f'data.empty{schema_separator}{python_schema_name}'},
        enum={
            'name': f'data.message{schema_separator}{python_schema_name}',
            'total': 2,
            'size': 2,
        },
        bool={
            'name': f'data.valid{schema_separator}{python_schema_name}',
            'total': 2,
            'size': 2,
        },
        int={'name': f'data.count{schema_separator}{python_schema_name}'},
        pagination={
            'name': f'data.name{schema_separator}{python_schema_name}',
            'total': 15,
            'size': 10,
            'page_after_value': 'test5',
            'page_after_value_tiebreaker': 'Sheldon Cooper:test5',
            'page_after_value_size': 4,
        },
        pagination_order_by=None,
        histogram_int={
            'name': f'data.count{schema_separator}{python_schema_name}',
            'interval': 1,
            'buckets': 10,
            'interval_size': 15,
            'bucket_size': 8,
        },
        histogram_date={
            'name': f'data.timestamp{schema_separator}{python_schema_name}',
            'interval': '1s',
            'interval_size': 1,
            'default_size': 1,
        },
        include={
            'name': f'data.name{schema_separator}{python_schema_name}',
            'include': 'test5',
            'total': 1,
            'size': 1,
        },
        metrics={
            'name': f'data.name{schema_separator}{python_schema_name}',
            'total': 15,
            'size': 10,
        },
        fixed=None,
    ),
)
def test_entries_aggregations_dynamic(
    auth_headers,
    plugin_schema,
    client,
    example_data_schema_python,
    aggregation,
    total,
    size,
    status_code,
    user,
):
    """Tests aggregation calls for dynamically mapped quantities (quantities stored in
    search_quantities).
    """
    assert_aggregation_response(
        auth_headers,
        client,
        aggregation,
        total,
        size,
        status_code,
        user,
    )


@pytest.mark.parametrize(
    'query,agg_data,total,status_code',
    aggregation_exclude_from_search_test_parameters(total_per_entity=1, total=23),
)
def test_entries_aggregations_exclude_from_search(
    client, example_data, query, agg_data, total, status_code
):
    aggs, types, lengths = agg_data
    response_json = perform_entries_metadata_test(
        client,
        owner='visible',
        query=query,
        aggregations=aggs,
        pagination=dict(page_size=0),
        status_code=status_code,
        http_method='post',
    )

    if response_json is None:
        return

    assert response_json['pagination']['total'] == total
    for i, (type, length) in enumerate(zip(types, lengths)):
        response_agg = response_json['aggregations'][f'agg_{i}'][type]
        assert len(response_agg['data']) == length


@pytest.mark.parametrize(
    'aggregation, status_code, expected_groups, query',
    [
        pytest.param(
            {'percentiles': {'quantity': 'results.properties.n_calculations'}},
            200,
            1,
            None,
            id='percentiles-ungrouped',
        ),
        pytest.param(
            {
                'percentiles': {
                    'quantity': 'results.properties.n_calculations',
                    'group_by': 'results.material.dimensionality',
                }
            },
            200,
            1,
            None,
            id='percentiles-grouped',
        ),
        pytest.param(
            {
                'percentiles': {
                    'quantity': 'results.properties.n_calculations',
                    'group_by': 'results.method.simulation.program_name',
                    'group_by_size': 5,
                }
            },
            200,
            1,
            None,
            id='percentiles-grouped-with-size',
        ),
        pytest.param(
            {'percentiles': {'quantity': 'entry_id'}},
            422,
            None,
            None,
            id='percentiles-non-numeric',
        ),
        pytest.param(
            {'percentiles': {'quantity': 'does_not_exist'}},
            422,
            None,
            None,
            id='percentiles-bad-quantity',
        ),
        pytest.param(
            {
                'percentiles': {
                    'quantity': 'results.properties.geometry_optimization.final_energy_difference',
                    'group_by': 'results.material.dimensionality',
                }
            },
            200,
            0,
            {'entry_id': 'nonexistent_entry_id'},
            id='percentiles-grouped-no-y-data',
        ),
        pytest.param(
            {
                'percentiles': {
                    'quantity': 'results.properties.n_calculations',
                    'percents': [10, 50, 90],
                }
            },
            200,
            1,
            None,
            id='percentiles-custom-percents',
        ),
        pytest.param(
            {'percentiles': {'quantity': 'results.properties.n_calculations'}},
            200,
            0,
            {'entry_id': 'nonexistent_entry_id'},
            id='percentiles-ungrouped-no-results',
        ),
    ],
)
def test_entries_aggregations_percentiles(
    client, example_data, aggregation, status_code, expected_groups, query
):
    """Tests percentiles aggregation calls."""

    response_json = perform_entries_metadata_test(
        client,
        owner='visible',
        aggregations={'test_agg': aggregation},
        pagination=dict(page_size=0),
        status_code=status_code,
        http_method='post',
        query=query,
    )

    if response_json is None:
        return

    agg_response = response_json['aggregations']['test_agg']['percentiles']
    assert 'data' in agg_response
    data = agg_response['data']
    assert len(data) == expected_groups

    requested_percents = aggregation['percentiles'].get(
        'percents', [0, 25, 50, 75, 100]
    )
    for item in data:
        assert 'percentiles' in item
        percentiles = item['percentiles']
        for p in requested_percents:
            # ES returns keys without trailing ".0" for whole numbers
            matching_keys = [k for k in percentiles if float(k) == float(p)]
            assert matching_keys, f'Missing percentile {p} in response: {percentiles}'
            val = percentiles[matching_keys[0]]
            assert isinstance(val, (float, int, type(None)))
        assert 'count' in item
        assert isinstance(item['count'], int)
        assert item['count'] > 0

    # For grouped percentiles, check that labels are present
    if 'group_by' in aggregation.get('percentiles', {}):
        for item in data:
            assert 'label' in item
            assert item['label'] is not None


@pytest.mark.parametrize(
    'aggregation, status_code, expected_groups',
    [
        pytest.param(
            {
                'percentiles': {
                    'quantity': f'data.count{schema_separator}{python_schema_name}',
                }
            },
            200,
            1,
            id='percentiles-dynamic-ungrouped',
        ),
        pytest.param(
            {
                'percentiles': {
                    'quantity': f'data.count{schema_separator}{python_schema_name}',
                    'group_by': 'upload_id',
                }
            },
            200,
            1,
            id='percentiles-dynamic-grouped-by-static',
        ),
    ],
)
def test_entries_aggregations_percentiles_dynamic(
    client,
    plugin_schema,
    example_data_schema_python,
    aggregation,
    status_code,
    expected_groups,
):
    """Tests percentiles aggregation calls for dynamically mapped quantities."""

    response_json = perform_entries_metadata_test(
        client,
        owner='visible',
        aggregations={'test_agg': aggregation},
        pagination=dict(page_size=0),
        status_code=status_code,
        http_method='post',
    )

    if response_json is None:
        return

    agg_response = response_json['aggregations']['test_agg']['percentiles']
    assert 'data' in agg_response
    data = agg_response['data']
    assert len(data) == expected_groups

    requested_percents = aggregation['percentiles'].get(
        'percents', [0, 25, 50, 75, 100]
    )
    for item in data:
        assert 'percentiles' in item
        percentiles = item['percentiles']
        for p in requested_percents:
            matching_keys = [k for k in percentiles if float(k) == float(p)]
            assert matching_keys, f'Missing percentile {p} in response: {percentiles}'
            val = percentiles[matching_keys[0]]
            assert isinstance(val, (float, int, type(None)))
        assert 'count' in item
        assert isinstance(item['count'], int)
        assert item['count'] > 0

    if 'group_by' in aggregation.get('percentiles', {}):
        for item in data:
            assert 'label' in item
            assert item['label'] is not None


@pytest.mark.parametrize(
    'required, status_code',
    [
        pytest.param({'include': ['entry_id', 'upload_id']}, 200, id='include'),
        pytest.param(
            {'include': ['results.*', 'upload_id']}, 200, id='include-section'
        ),
        pytest.param({'exclude': ['upload_id']}, 200, id='exclude'),
        pytest.param(
            {'exclude': ['missspelled', 'upload_id']}, 422, id='bad-quantitiy'
        ),
        pytest.param({'exclude': ['entry_id']}, 200, id='exclude-id'),
        pytest.param(
            {'exclude': ['results.material.*']}, 200, id='exclude-sub-section'
        ),
        pytest.param(
            {'exclude': ['files', 'results.material.*', 'results.method.*']},
            200,
            id='exclude-multiple',
        ),
        pytest.param({'include': ['upload_id']}, 200, id='include-id'),
    ],
)
@pytest.mark.parametrize('http_method', ['post', 'get'])
def test_entries_required(client, example_data, required, status_code, http_method):
    response_json = perform_entries_metadata_test(
        client,
        required=required,
        pagination={'page_size': 1},
        status_code=status_code,
        http_method=http_method,
    )

    if response_json is None:
        return

    assert_required(response_json['data'][0], required, default_key='entry_id')


@pytest.mark.parametrize(
    'user, entry_id, required, status_code',
    [
        pytest.param(None, 'id_01', {}, 200, id='id'),
        pytest.param('user1', 'id_child_entries_child1', {}, 200, id='id-child-entry'),
        pytest.param(None, 'doesnotexist', {}, 404, id='404'),
        pytest.param(
            None, 'id_01', {'include': ['entry_id', 'upload_id']}, 200, id='include'
        ),
        pytest.param(None, 'id_01', {'exclude': ['upload_id']}, 200, id='exclude'),
        pytest.param(
            None,
            'id_01',
            {'exclude': ['entry_id', 'upload_id']},
            200,
            id='exclude-entry-id',
        ),
    ],
)
def test_entry_metadata(
    auth_headers, client, example_data, user, entry_id, required, status_code
):
    response = client.get(
        f'entries/{entry_id}?{urlencode(required, doseq=True)}',
        headers=auth_headers[user],
    )
    response_json = assert_metadata_response(response, status_code=status_code)

    if response_json is None:
        return

    assert_required(response_json['data'], required, default_key='entry_id')


@pytest.mark.parametrize(
    'user, owner, query, files, total, files_per_entry, status_code',
    [
        pytest.param(None, None, {}, {}, 23, 5, 200, id='all'),
        pytest.param(None, None, {'entry_id': 'id_01'}, {}, 1, 5, 200, id='one-entry'),
        pytest.param(
            'user1',
            'visible',
            {'upload_id': 'id_child_entries'},
            {},
            3,
            5,
            200,
            id='child-entries',
        ),
        pytest.param(
            None, None, {PROGRAM_NAME: 'DOESNOTEXIST'}, {}, 0, 5, 200, id='empty'
        ),
    ],
)
@pytest.mark.parametrize('http_method', ['post', 'get'])
def test_entries_rawdir(
    auth_headers,
    client,
    example_data,
    user,
    owner,
    query,
    files,
    total,
    files_per_entry,
    status_code,
    http_method,
):
    perform_entries_rawdir_test(
        client,
        owner=owner,
        status_code=status_code,
        query=query,
        files=files,
        total=total,
        files_per_entry=files_per_entry,
        http_method=http_method,
        headers=auth_headers[user],
    )


@pytest.mark.parametrize('endpoint', ['archive', 'rawdir'])
@pytest.mark.parametrize(
    'pagination, expected_entries',
    [
        pytest.param({'page_size': 0}, 0, id='zero-page-size'),
        pytest.param({'page': 2}, 0, id='second-page'),
        pytest.param({'page_offset': 1}, 0, id='page-offset'),
        pytest.param({'page': 1}, 1, id='first-page'),
    ],
)
def test_exact_entry_mongo_pagination(
    client, example_data, endpoint, pagination, expected_entries
):
    response = client.post(
        f'entries/{endpoint}/query',
        json={
            'owner': 'public',
            'query': {'entry_id': 'id_01'},
            'pagination': pagination,
        },
    )

    assert_response(response, 200)
    response_json = response.json()
    assert response_json['pagination']['total'] == 1
    assert len(response_json['data']) == expected_entries
    for key, value in pagination.items():
        assert response_json['pagination'][key] == value


@pytest.mark.parametrize('endpoint', ['archive', 'rawdir'])
def test_exact_entry_mongo_respects_public_owner(
    auth_headers, client, example_data, endpoint
):
    response = client.post(
        f'entries/{endpoint}/query',
        headers=auth_headers['user1'],
        json={
            'owner': 'public',
            'query': {'entry_id': 'id_child_entries_child1'},
        },
    )

    assert_response(response, 200)
    assert response.json()['pagination']['total'] == 0
    assert response.json()['data'] == []


@pytest.mark.parametrize('endpoint', ['archive', 'rawdir'])
def test_exact_entry_mongo_preserves_owner_authentication(
    client, example_data, endpoint
):
    response = client.post(
        f'entries/{endpoint}/query',
        json={'owner': 'shared', 'query': {'entry_id': 'id_01'}},
    )

    assert_response(response, 401)


@pytest.mark.parametrize('endpoint', ['archive', 'rawdir'])
def test_exact_entry_collections_do_not_search(
    client, example_data, monkeypatch, endpoint
):
    def fail_search(*args, **kwargs):
        raise AssertionError('exact entry lookup should not search Elasticsearch')

    monkeypatch.setattr(entries_router, 'perform_search', fail_search)

    response = client.post(
        f'entries/{endpoint}/query',
        json={'owner': 'public', 'query': {'entry_id': 'id_01'}},
    )

    assert_response(response, 200)
    response_json = response.json()
    assert len(response_json['data']) == 1
    assert response_json['query'] == {'name': 'entry_id', 'value': 'id_01'}
    if endpoint == 'archive':
        assert set(response_json['data'][0]) == {
            'entry_id',
            'upload_id',
            'parser_name',
            'archive',
        }


@pytest.mark.parametrize('endpoint', ['archive', 'rawdir'])
def test_exact_entry_mongo_preserves_order_validation(client, example_data, endpoint):
    response = client.post(
        f'entries/{endpoint}/query',
        json={
            'owner': 'public',
            'query': {'entry_id': 'id_01'},
            'pagination': {'order_by': 'does_not_exist'},
        },
    )

    assert_response(response, 422)


@pytest.mark.parametrize(
    'user, owner, query, files, total, files_per_entry, status_code',
    [
        pytest.param(None, None, {}, {}, 23, 5, 200, id='all'),
        pytest.param(
            'user1',
            'visible',
            {'upload_id': 'id_child_entries'},
            {},
            (3, 1),
            5,
            200,
            id='child-entries',
        ),
        pytest.param(
            None, None, {PROGRAM_NAME: 'DOESNOTEXIST'}, {}, 0, 5, 200, id='empty'
        ),
        pytest.param(None, None, {}, {'glob_pattern': '*.json'}, 23, 1, 200, id='glob'),
        pytest.param(
            None, None, {}, {'re_pattern': '[a-z]*\\.aux'}, 23, 4, 200, id='re'
        ),
        pytest.param(
            None,
            None,
            {},
            {'re_pattern': 'test_entry_02'},
            1,
            5,
            200,
            id='re-filter-entries',
        ),
        pytest.param(
            None,
            None,
            {},
            {'re_pattern': 'test_entry_02/.*\\.json'},
            1,
            1,
            200,
            id='re-filter-entries-and-files',
        ),
        pytest.param(
            None,
            None,
            {},
            {'glob_pattern': '*.json', 're_pattern': '.*\\.aux'},
            23,
            4,
            200,
            id='re-overwrites-glob',
        ),
        pytest.param(
            None, None, {}, {'re_pattern': '**'}, -1, -1, 422, id='bad-re-pattern'
        ),
        pytest.param(None, None, {}, {'compress': True}, 23, 5, 200, id='compress'),
        pytest.param(
            None, None, {}, {'include_files': ['1.aux']}, 23, 1, 200, id='file'
        ),
        pytest.param(
            None,
            None,
            {},
            {'include_files': ['1.aux', '2.aux']},
            23,
            2,
            200,
            id='files',
        ),
    ],
)
@pytest.mark.parametrize('http_method', ['post', 'get'])
def test_entries_raw(
    auth_headers,
    client,
    example_data,
    user,
    owner,
    query,
    files,
    total,
    files_per_entry,
    status_code,
    http_method,
):
    perform_entries_raw_test(
        client,
        headers=auth_headers[user],
        owner=owner,
        status_code=status_code,
        query=query,
        files=files,
        total=total,
        files_per_entry=files_per_entry,
        http_method=http_method,
    )


@pytest.mark.parametrize('http_method', ['post', 'get'])
@pytest.mark.parametrize(
    'test_method',
    [
        pytest.param(perform_entries_raw_test, id='raw'),
        pytest.param(perform_entries_archive_download_test, id='archive-download'),
    ],
)
def test_entries_download_max(
    monkeypatch, client, example_data, test_method, http_method
):
    monkeypatch.setattr('nomad.config.services.max_entry_download', 20)

    test_method(client, status_code=400, http_method=http_method)


class TestEntriesExportMetadata:
    @pytest.mark.parametrize(
        'user, owner, query, status_code',
        [
            pytest.param(None, None, {}, 200, id='all'),
            pytest.param(None, 'all', {}, 403, id='owner_all'),
        ],
    )
    @pytest.mark.parametrize('content_type', ['application/json', 'text/csv'])
    def test_entries_export_metadata(
        self,
        auth_headers,
        client,
        example_data,
        user,
        owner,
        query,
        status_code,
        content_type,
        page_size: int = 10_000,
    ):
        params = dict(**query)
        params['page_size'] = page_size

        if owner is not None:
            params['owner'] = owner

        response = client.get(
            f'entries/export?{urlencode(params)}',
            headers={**(auth_headers[user] or {}), 'Content-Type': content_type},
        )

        assert_response(response, status_code)

        if status_code == 200:
            if content_type == 'application/json':
                for i, entry in enumerate(response.json(), start=1):
                    assert entry['upload_id'] == 'id_published'
                    assert entry['entry_id'] == f'id_{i:02d}'

            else:
                csv_data = io.StringIO(response.text)
                rows = list(csv.reader(csv_data))
                assert len(rows) >= 2

                assert set(rows[0]).issuperset({'upload_id', 'entry_id'})

                for i, entry_row in enumerate(rows[1:], start=1):
                    assert 'id_published' in entry_row  # upload_id
                    assert f'id_{i:02d}' in entry_row  # entry_id

    @pytest.mark.parametrize(
        'user, owner, query, status_code, content_type',
        [
            pytest.param(None, None, {}, 415, 'invalid', id='all'),
        ],
    )
    def test_invalid_content_type(
        self,
        auth_headers,
        client,
        example_data,
        user,
        owner,
        query,
        status_code,
        content_type,
    ):
        params = dict(**query)

        if owner is not None:
            params['owner'] = owner

        response = client.get(
            f'entries/export?{urlencode(params)}',
            headers={**(auth_headers[user] or {}), 'Content-Type': content_type},
        )

        assert_response(response, status_code)

    @pytest.mark.parametrize(
        'user, owner, query, status_code',
        [
            pytest.param(None, None, {}, 400, id='all'),
        ],
    )
    @pytest.mark.parametrize('content_type', ['application/json', 'text/csv'])
    def test_max_entry_metadata_download(
        self,
        auth_headers,
        client,
        example_data,
        monkeypatch,
        user,
        owner,
        query,
        status_code,
        content_type: Literal['application/json', 'text/csv'],
    ):
        monkeypatch.setattr('nomad.config.services.max_entry_metadata_download', 1)

        params = dict(**query)
        if owner is not None:
            params['owner'] = owner

        response = client.get(
            'entries/export',
            headers={**(auth_headers[user] or {}), 'Content-Type': content_type},
        )

        assert response.status_code == status_code

        expected_message = (
            'The limit of maximum number of metadata in a single download'
        )
        assert expected_message in response.json()['detail']


@pytest.mark.parametrize(
    'user, entry_id, files_per_entry, status_code',
    [
        pytest.param(None, 'id_01', 5, 200, id='id'),
        pytest.param('user1', 'id_child_entries_child1', 5, 200, id='child-entries'),
        pytest.param(None, 'id_embargo', -1, 404, id='embargoed'),
        pytest.param(None, 'doesnotexist', -1, 404, id='bad-entry_id'),
    ],
)
def test_entry_rawdir(
    auth_headers, client, example_data, user, entry_id, files_per_entry, status_code
):
    response = client.get(f'entries/{entry_id}/rawdir', headers=auth_headers[user])
    assert_response(response, status_code)
    if status_code == 200:
        assert_entry_rawdir_response(response.json(), files_per_entry=files_per_entry)


@pytest.mark.parametrize(
    'user, entry_id, files, files_per_entry, status_code',
    [
        pytest.param(None, 'id_01', {}, 5, 200, id='id'),
        pytest.param('user1', 'id_child_entries_child1', {}, 5, 200, id='child-entry'),
        pytest.param(None, 'doesnotexist', {}, -1, 404, id='404'),
        pytest.param(None, 'id_01', {'glob_pattern': '*.json'}, 1, 200, id='glob'),
        pytest.param(None, 'id_01', {'re_pattern': '[a-z]*\\.aux'}, 4, 200, id='re'),
        pytest.param(None, 'id_01', {'re_pattern': '**'}, -1, 422, id='bad-re-pattern'),
        pytest.param(None, 'id_01', {'compress': True}, 5, 200, id='compress'),
        pytest.param(None, 'id_01', {'include_files': ['1.aux']}, 1, 200, id='file'),
        pytest.param(
            None, 'id_01', {'include_files': ['1.aux', '2.aux']}, 2, 200, id='files'
        ),
    ],
)
def test_entry_raw(
    auth_headers,
    client,
    example_data,
    user,
    entry_id,
    files,
    files_per_entry,
    status_code,
):
    response = client.get(
        f'entries/{entry_id}/raw?{urlencode(files, doseq=True)}',
        headers=auth_headers[user],
    )
    assert_response(response, status_code)
    if status_code == 200:
        assert_raw_zip_file(
            response,
            files=files_per_entry + 1,
            manifest_entries=1,
            compressed=files.get('compress', False),
        )


@pytest.fixture(scope='function')
def example_data_with_compressed_files(
    elastic_module,
    raw_files_module,
    mongo_module,
    user1,
    user2,
    normalized,
):
    data = ExampleData(main_author=user1)

    data.create_upload(upload_id='with_compr_published', published=True)
    data.create_entry(
        upload_id='with_compr_published',
        entry_id='with_compr_published',
        mainfile='test_content/test_entry/mainfile.json',
    )
    data.create_upload(upload_id='with_compr_unpublished', published=False)
    data.create_entry(
        upload_id='with_compr_unpublished',
        entry_id='with_compr_unpublished',
        mainfile='test_content/test_entry/mainfile.json',
    )

    data.save()

    append_raw_files(
        'with_compr_published',
        'tests/data/api/mainfile.xz',
        'test_content/test_entry/mainfile.xz',
    )
    append_raw_files(
        'with_compr_published',
        'tests/data/api/mainfile.gz',
        'test_content/test_entry/mainfile.gz',
    )
    append_raw_files(
        'with_compr_unpublished',
        'tests/data/api/mainfile.xz',
        'test_content/test_entry/mainfile.xz',
    )
    append_raw_files(
        'with_compr_unpublished',
        'tests/data/api/mainfile.gz',
        'test_content/test_entry/mainfile.gz',
    )

    yield

    data.delete()
    from nomad.search import search

    assert search(query=dict(upload_id='with_compr_published')).pagination.total == 0


@pytest.mark.parametrize(
    'entry_id, path, params, status_code',
    [
        pytest.param('id_01', 'mainfile.json', {}, 200, id='id'),
        pytest.param(
            'id_child_entries_child1',
            'mainfile_w_children.json',
            {'user': 'user1'},
            200,
            id='child-entry',
        ),
        pytest.param('doesnotexist', 'mainfile.json', {}, 404, id='404-entry'),
        pytest.param('id_01', 'doesnot.exist', {}, 404, id='404-file'),
        pytest.param(
            'id_01',
            'mainfile.json',
            {'offset': 10, 'length': 10},
            200,
            id='offset-length',
        ),
        pytest.param(
            'id_01', 'mainfile.json', {'length': 1000000}, 200, id='length-too-large'
        ),
        pytest.param(
            'id_01', 'mainfile.json', {'offset': 1000000}, 200, id='offset-too-large'
        ),
        pytest.param('id_01', 'mainfile.json', {'offset': -1}, 422, id='bad-offset'),
        pytest.param('id_01', 'mainfile.json', {'length': -2}, 422, id='bad-length'),
        pytest.param(
            'id_01', 'mainfile.json', {'decompress': True}, 200, id='decompress-json'
        ),
        pytest.param(
            'with_compr_published',
            'mainfile.xz',
            {'decompress': True},
            200,
            id='decompress-xz-published',
        ),
        pytest.param(
            'with_compr_published',
            'mainfile.gz',
            {'decompress': True},
            200,
            id='decompress-gz-published',
        ),
        pytest.param(
            'with_compr_unpublished',
            'mainfile.xz',
            {'decompress': True, 'user': 'user1'},
            200,
            id='decompress-xz-unpublished',
        ),
        pytest.param(
            'with_compr_unpublished',
            'mainfile.gz',
            {'decompress': True, 'user': 'user1'},
            200,
            id='decompress-gz-unpublished',
        ),
        pytest.param('id_unpublished', 'mainfile.json', {}, 404, id='404-unpublished'),
        pytest.param(
            'id_embargo_1', 'mainfile.json', {}, 404, id='404-embargo-no-user'
        ),
        pytest.param(
            'id_embargo_1',
            'mainfile.json',
            {'user': 'user2'},
            404,
            id='404-embargo-no-access',
        ),
        pytest.param(
            'id_embargo_1',
            'mainfile.json',
            {'user': 'user1'},
            200,
            id='embargo-main_author',
        ),
        pytest.param(
            'id_embargo_w_coauthor_1',
            'mainfile.json',
            {'user': 'user2'},
            200,
            id='embargo-coauthor',
        ),
        pytest.param(
            'id_embargo_w_reviewer_1',
            'mainfile.json',
            {'user': 'user2'},
            200,
            id='embargo-reviewer',
        ),
    ],
)
def test_entry_raw_file(
    auth_headers,
    client,
    example_data,
    example_data_with_compressed_files,
    example_mainfile_contents,
    entry_id,
    path,
    params,
    status_code,
):
    user = params.get('user')
    if user:
        del params['user']

    response = client.get(
        f'entries/{entry_id}/raw/{path}?{urlencode(params, doseq=True)}',
        headers=auth_headers[user],
    )

    assert_response(response, status_code)
    if status_code == 200:
        content = response.text
        if path.endswith('.json'):
            offset = params.get('offset', 0)
            length = params.get('length', len(example_mainfile_contents) - offset)
            assert content == example_mainfile_contents[offset : offset + length]
        else:
            assert content == 'test content\n'


@pytest.mark.parametrize(
    'path',
    [
        pytest.param('entries/id_01/rawdir', id='rawdir'),
        pytest.param('entries/id_01/raw/mainfile.json', id='raw-file'),
    ],
)
def test_exact_entry_raw_endpoints_do_not_search(
    client, example_data, monkeypatch, path
):
    def fail_search(*args, **kwargs):
        raise AssertionError('exact entry lookup should not search Elasticsearch')

    monkeypatch.setattr(entries_router, 'perform_search', fail_search)

    response = client.get(path)

    assert_response(response, 200)


def test_exact_entry_raw_download_does_not_search(client, example_data, monkeypatch):
    def fail_search(*args, **kwargs):
        raise AssertionError('exact entry lookup should not search Elasticsearch')

    monkeypatch.setattr(entries_router, 'perform_search', fail_search)

    response = client.get('entries/id_01/raw')

    assert_response(response, 200)
    assert_raw_zip_file(
        response,
        files=6,
        manifest_entries=1,
        compressed=False,
    )


@pytest.mark.parametrize(
    'user, owner, query, required, files, total, status_code',
    [
        pytest.param(None, None, {}, None, {}, 23, 200, id='all'),
        pytest.param(
            'user1',
            'visible',
            {'upload_id': 'id_child_entries'},
            None,
            {},
            3,
            200,
            id='child-entries',
        ),
        pytest.param(None, None, {}, {'metadata': '*'}, {}, 23, 200, id='required'),
        pytest.param(
            None, None, {PROGRAM_NAME: 'DOESNOTEXIST'}, None, {}, -1, 200, id='empty'
        ),
        pytest.param(None, None, {}, None, {'compress': True}, 23, 200, id='compress'),
    ],
)
@pytest.mark.parametrize('http_method', ['post', 'get'])
def test_entries_archive_download(
    auth_headers,
    client,
    example_data,
    user,
    owner,
    query,
    required,
    files,
    total,
    status_code,
    http_method,
):
    perform_entries_archive_download_test(
        client,
        headers=auth_headers[user],
        owner=owner,
        status_code=status_code,
        query=query,
        http_method=http_method,
        required=required,
        files=files,
        total=total,
    )


@pytest.mark.parametrize(
    'required, status_code',
    [
        pytest.param('*', 200, id='full'),
        pytest.param({'metadata': '*'}, 200, id='partial'),
        pytest.param({'run': {'system[NOTANINT]': '*'}}, 422, id='bad-required-1'),
        pytest.param(
            {'metadata': {'viewers[NOTANINT]': '*'}}, 422, id='bad-required-2'
        ),
        # pytest.param({'DOESNOTEXIST': '*'}, 422, id='bad-required-3'),
    ],
)
def test_entries_archive(client, example_data, required, status_code):
    perform_entries_archive_test(
        client, status_code=status_code, required=required, http_method='post'
    )


@pytest.mark.parametrize(
    'user, entry_id, status_code',
    [
        pytest.param(None, 'id_01', 200, id='id'),
        pytest.param('user1', 'id_child_entries_child1', 200, id='child-entry'),
        pytest.param(None, 'id_02', 404, id='404-not-visible'),
        pytest.param(None, 'doesnotexist', 404, id='404-does-not-exist'),
    ],
)
def test_entry_archive(auth_headers, client, example_data, user, entry_id, status_code):
    response = client.get(f'entries/{entry_id}/archive', headers=auth_headers[user])
    assert_response(response, status_code)
    if status_code == 200:
        assert_archive_response(response.json())


@pytest.mark.parametrize(
    'user, entry_id, status_code',
    [
        pytest.param(None, 'id_01', 200, id='id'),
        pytest.param(None, 'id_01', 200, id='id'),
        pytest.param('user1', 'id_child_entries_child1', 200, id='child-entry'),
        pytest.param('user1', 'id_child_entries_child1', 200, id='child-entry'),
        pytest.param(None, 'id_02', 404, id='404-not-visible'),
        pytest.param(None, 'doesnotexist', 404, id='404-does-not-exist'),
    ],
)
def test_entry_archive_download(
    auth_headers, client, example_data, user, entry_id, status_code
):
    response = client.get(
        f'entries/{entry_id}/archive/download',
        headers=auth_headers[user],
    )
    assert_response(response, status_code)
    if status_code == 200:
        archive = response.json()
        assert 'metadata' in archive
        assert 'run' in archive


@pytest.mark.parametrize(
    'user, entry_id, required, status_code',
    [
        pytest.param(None, 'id_01', '*', 200, id='full'),
        pytest.param(
            'user1', 'id_child_entries_child1', '*', 200, id='full-child-entry'
        ),
        pytest.param(None, 'id_02', '*', 404, id='404'),
        pytest.param(None, 'id_01', {'metadata': '*'}, 200, id='partial'),
        pytest.param(
            None, 'id_01', {'run': {'system[NOTANINT]': '*'}}, 422, id='bad-required-1'
        ),
        pytest.param(
            None,
            'id_01',
            {'metadata': {'viewers[NOTANINT]': '*'}},
            422,
            id='bad-required-2',
        ),
        # pytest.param(None, 'id_01', {'DOESNOTEXIST': '*'}, 422, id='bad-required-3'),
        pytest.param(
            None,
            'id_01',
            {'resolve-inplace': 'NotBool', 'workflow': '*'},
            422,
            id='bad-required-4',
        ),
        pytest.param(
            None,
            'id_01',
            {'resolve-inplace': True, 'metadata': 'include-resolved'},
            200,
            id='resolve-inplace',
        ),
    ],
)
def test_entry_archive_query(
    auth_headers, client, example_data, user, entry_id, required, status_code
):
    response = client.post(
        f'entries/{entry_id}/archive/query',
        json={'required': required},
        headers=auth_headers[user],
    )
    assert_response(response, status_code)
    if status_code == 200:
        assert_archive_response(response.json(), required=required)


ELEMENTS: str = 'results.material.elements'
N_ELEMENTS: str = 'results.material.n_elements'


@pytest.mark.parametrize(
    'query, status_code, total',
    post_query_test_parameters(
        'entry_id', total=23, material_prefix='results.material.', entry_prefix=''
    )
    + [
        pytest.param({'pid': '123'}, 200, 1, id='number-valued-string'),
        pytest.param(
            {'optimade_filter': 'nelements = 2'}, 200, 23, id='optimade-filter-positive'
        ),
        pytest.param(
            {'optimade_filter': 'nelements < 2'}, 200, 0, id='optimade-filter-negative'
        ),
        pytest.param(
            {'optimade_filter': '#broken syntax'},
            422,
            0,
            id='optimade-filter-broken-syntax',
        ),
        pytest.param(
            {'optimade_filter': 'doesnotexist = 1'},
            422,
            0,
            id='optimade-filter-broken-semantics',
        ),
    ],
)
@pytest.mark.parametrize(
    'test_method',
    [
        pytest.param(perform_entries_metadata_test, id='metadata'),
        pytest.param(perform_entries_raw_test, id='raw'),
        pytest.param(perform_entries_rawdir_test, id='rawdir'),
        pytest.param(perform_entries_archive_test, id='archive'),
        pytest.param(perform_entries_archive_download_test, id='archive-download'),
    ],
)
def test_entries_post_query(
    client, example_data, query, status_code, total, test_method
):
    response_json = test_method(
        client, query=query, status_code=status_code, total=total, http_method='post'
    )

    response = client.post('entries/query', json={'query': query})
    response_json = assert_metadata_response(response, status_code=status_code)

    if response_json is None:
        return

    if 'pagination' not in response_json:
        return

    pagination = response_json['pagination']
    assert pagination['total'] == total
    assert pagination['page_size'] == 10
    assert pagination['order_by'] == 'entry_id'
    assert pagination['order'] == 'asc'
    assert ('next_page_after_value' in pagination) == (total > 10)


@pytest.mark.parametrize(
    'query, status_code, total',
    get_query_test_parameters(
        str={
            'name': 'entry_id',
            'values': ['id_01', 'id_02'],
            'total': 1,
            'total_any': 2,
            'total_all': 0,
            'total_gt': 22,
        },
        int={
            'name': N_ELEMENTS,
            'values': [2, 1],
            'total': 23,
            'total_any': 23,
            'total_all': 0,
            'total_gt': 0,
        },
        date={'name': 'upload_create_time', 'total': 23},
        subsection={
            'name': 'results.material.material_id',
            'values': ['id_01'],
            'total': 3,
        },
        total=23,
    )
    + [pytest.param({'q': 'domain__dft'}, 200, 23, id='enum')],
)
@pytest.mark.parametrize(
    'test_method',
    [
        pytest.param(perform_entries_metadata_test, id='metadata'),
        pytest.param(perform_entries_raw_test, id='raw'),
        pytest.param(perform_entries_rawdir_test, id='rawdir'),
        pytest.param(perform_entries_archive_test, id='archive'),
        pytest.param(perform_entries_archive_download_test, id='archive-download'),
    ],
)
def test_entries_get_query(
    client, example_data, query, status_code, total, test_method
):
    assert_query_response(client, test_method, query, total, status_code)


@pytest.mark.parametrize(
    'query, status_code, total',
    get_query_test_parameters(
        str={
            'name': f'data.name{schema_separator}{python_schema_name}',
            'values': ['test1', 'test2'],
            'total': 1,
            'total_any': 2,
            'total_all': 0,
            'total_gt': 13,
        },
        int={
            'name': f'data.count{schema_separator}{python_schema_name}',
            'values': [1, 2],
            'total': 1,
            'total_any': 2,
            'total_all': 0,
            'total_gt': 13,
        },
        date={
            'name': f'data.timestamp{schema_separator}{python_schema_name}',
            'total': 15,
        },
        subsection={
            'name': f'data.child.name{schema_separator}{python_schema_name}',
            'values': ['test_child1'],
            'total': 1,
        },
        total=38,  # Note that this includes also the data coming from the fixture 'example_data' which has a bigger scope
    ),
)
@pytest.mark.parametrize(
    'test_method', [pytest.param(perform_entries_metadata_test, id='metadata')]
)
def test_entries_get_query_dynamic(
    plugin_schema,
    client,
    example_data_schema_python,
    query,
    status_code,
    total,
    test_method,
):
    assert_query_response(client, test_method, query, total, status_code)


@pytest.mark.parametrize(
    'owner, user, status_code, total_entries, total_mainfiles',
    owner_test_parameters(),
)
@pytest.mark.parametrize('http_method', ['post', 'get'])
@pytest.mark.parametrize(
    'test_method',
    [
        pytest.param(perform_entries_metadata_test, id='metadata'),
        pytest.param(perform_entries_raw_test, id='raw'),
        pytest.param(perform_entries_rawdir_test, id='rawdir'),
        pytest.param(perform_entries_archive_test, id='archive'),
        pytest.param(perform_entries_archive_download_test, id='archive-download'),
    ],
)
def test_entries_owner(
    auth_headers,
    client,
    example_data,
    owner,
    user,
    status_code,
    total_entries,
    total_mainfiles,
    http_method,
    test_method,
):
    total = (
        (total_entries, total_mainfiles)
        if test_method == perform_entries_raw_test
        else total_entries
    )
    perform_owner_test(
        auth_headers,
        client,
        owner,
        user,
        status_code,
        total,
        http_method,
        test_method,
    )


@pytest.mark.parametrize(
    'pagination, response_pagination, status_code',
    pagination_test_parameters(
        elements=ELEMENTS,
        n_elements=N_ELEMENTS,
        crystal_system='results.material.symmetry.crystal_system',
        total=23,
    ),
)
@pytest.mark.parametrize('http_method', ['post', 'get'])
@pytest.mark.parametrize(
    'test_method',
    [
        pytest.param(perform_entries_metadata_test, id='metadata'),
        pytest.param(perform_entries_rawdir_test, id='rawdir'),
        pytest.param(perform_entries_archive_test, id='archive'),
    ],
)
def test_entries_pagination(
    client,
    example_data,
    pagination,
    response_pagination,
    status_code,
    http_method,
    test_method,
):
    response_json = test_method(
        client, pagination=pagination, status_code=status_code, http_method=http_method
    )

    if response_json is None:
        return

    assert_pagination(
        pagination,
        response_json['pagination'],
        response_json['data'],
        is_get=(http_method == 'get'),
    )

    if response_pagination is None:
        return
    for key in response_pagination:
        if response_pagination[key] is None:
            assert key not in response_json['pagination']
        else:
            assert response_json['pagination'][key] == response_pagination[key]
    if len(response_json['data']) > 0 and 'order_by' not in pagination:
        if response_pagination['next_page_after_value'] is not None:
            assert (
                response_json['data'][-1]['entry_id']
                == response_pagination['next_page_after_value']
            )
