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

import pytest

from nomad.datamodel import EntryArchive
from nomad.datamodel.metainfo import SCHEMA_IMPORT_ERROR, simulationworkflowschema
from nomad.graph.graph_reader import EntryReader, Token
from nomad.utils.exampledata import ExampleData
from tests.archive.test_archive import assert_dict


def assert_path_exists(path, response):
    for segment in path:
        response = response[segment]
    # empty container implies wrong id
    if isinstance(response, dict):
        response.pop('m_errors', None)
    if not response:
        raise KeyError


def contains(subset: dict, superset: dict) -> bool:
    """
    Recursively checks if the 'subset' dictionary is contained within the 'superset' dictionary.

    For every key-value pair in 'subset', the same key must exist in 'superset'.
    If the value is a dictionary, then the function checks recursively that the nested dictionary is also contained.

    Parameters:
        subset (dict): The dictionary to be checked for containment.
        superset (dict): The dictionary that should contain the subset.

    Returns:
        bool: True if every key-value pair in subset is found in supserset, otherwise False.
    """
    for key, value in subset.items():
        # Check if the key exists in haystack
        if key not in superset:
            return False

        # If the value is a dictionary, perform a recursive check.
        if isinstance(value, dict):
            if not isinstance(superset[key], dict):
                return False
            if not contains(value, superset[key]):
                return False
        else:
            # Direct comparison for non-dictionary values.
            if superset[key] != value:
                return False
    return True


def test_graph_query_random(auth_headers, client, example_data):
    user_auth = auth_headers['user1']
    response = client.post(
        'graph/raw_query',
        json={
            Token.UPLOADS: {
                'id_embargo': {
                    Token.RAW: {
                        'test_content': {
                            'id_embargo_1': {'m_request': {'directive': 'plain'}}
                        }
                    },
                    Token.ENTRIES: {
                        'id_embargo_1': {
                            'mainfile': {'m_request': {'directive': 'plain'}}
                        }
                    },
                },
            }
        },
        headers={'Accept': 'application/json'} | (user_auth if user_auth else {}),
    )

    assert response.status_code == 200

    data = response.json()
    assert 'uploads' in data
    assert 'id_embargo' in data['uploads']
    assert 'files' in data['uploads']['id_embargo']
    assert 'entries' in data['uploads']['id_embargo']


@pytest.mark.parametrize(
    'upload_id,entry_id,user,expected_value,status_code',
    [
        pytest.param(
            'id_embargo',
            'id_embargo_1',
            'user1',
            {'uploads': {'id_embargo': {'entries': {'id_embargo_1': {}}}}},
            200,
            id='ok',
        ),
        pytest.param(
            'id_child_entries',
            'id_child_entries_child1',
            'user1',
            {
                'uploads': {
                    'id_child_entries': {'entries': {'id_child_entries_child1': {}}}
                }
            },
            200,
            id='child-entry',
        ),
        pytest.param(
            'id_embargo',
            'id_embargo_1',
            'user0',
            {'uploads': {'id_embargo': {'entries': {'id_embargo_1': {}}}}},
            200,
            id='admin-access',
        ),
        pytest.param(
            'id_embargo',
            'id_embargo_1',
            None,
            {
                'uploads': {
                    'id_embargo': {
                        'm_errors': [
                            {
                                'error_type': 'NOACCESS',
                                'message': 'No access to upload id_embargo.',
                            }
                        ]
                    }
                }
            },
            200,
            id='no-credentials',
        ),
        pytest.param(
            'id_embargo', 'id_embargo_1', 'invalid', {}, 401, id='invalid-credentials'
        ),
        pytest.param(
            'id_embargo',
            'id_embargo_1',
            'user2',
            {
                'uploads': {
                    'id_embargo': {
                        'm_errors': [
                            {
                                'error_type': 'NOACCESS',
                                'message': 'No access to upload id_embargo.',
                            }
                        ]
                    }
                }
            },
            200,
            id='no-access',
        ),
        pytest.param(
            'silly_value',
            'id_embargo_1',
            'user1',
            {
                'uploads': {
                    'silly_value': {
                        'm_errors': [
                            {
                                'error_type': 'NOTFOUND',
                                'message': 'The value silly_value is not a valid upload id.',
                            }
                        ]
                    }
                }
            },
            200,
            id='invalid-upload_id',
        ),
        pytest.param(
            'id_embargo',
            'silly_value',
            'user1',
            {
                'uploads': {
                    'id_embargo': {
                        'entries': {
                            'silly_value': {
                                'm_errors': [
                                    {
                                        'error_type': 'NOACCESS',
                                        'message': 'The value silly_value is not a valid entry id or not visible to current user.',
                                    }
                                ]
                            }
                        }
                    }
                }
            },
            200,
            id='invalid-entry_id',
        ),
    ],
)
def test_graph_query(
    auth_headers,
    client,
    example_data,
    upload_id,
    entry_id,
    user,
    expected_value,
    status_code,
):
    user_auth = auth_headers[user]
    response = client.post(
        'graph/query',
        json={Token.UPLOADS: {upload_id: {Token.ENTRIES: {entry_id: '*'}}}},
        headers={'Accept': 'application/json'} | (user_auth if user_auth else {}),
    )

    assert response.status_code == status_code
    if status_code == 200:
        assert contains(expected_value, response.json())


@pytest.mark.parametrize(
    'required,status_code',
    [
        pytest.param('*', 200, id='full'),
        pytest.param({'metadata': '*'}, 200, id='partial'),
        pytest.param({'run': {'system[NOTANINT]': '*'}}, 422, id='bad-required-1'),
        pytest.param(
            {'metadata': {'viewers[NOTANINT]': '*'}}, 422, id='bad-required-2'
        ),
        pytest.param({'DOESNOTEXIST': '*'}, 422, id='bad-required-3'),
    ],
)
def test_graph_archive_query(auth_headers, client, example_data, required, status_code):
    response = client.post(
        'graph/archive/query',
        headers=auth_headers['user1'],
        json={'owner': 'user', 'required': required},
    )

    json_response = response.json()

    m_response = json_response['search'].pop('m_response', None)

    assert m_response['pagination']['total'] == 32

    for key, value in json_response[Token.SEARCH].items():
        value = value[Token.ENTRIES]
        if key == 'id_02':
            # since id_02 is missing (see example_data)
            assert 'm_errors' in value
        elif status_code != 200:
            # nonexistent required field
            assert 'm_errors' in value[Token.ARCHIVE]
        else:
            archive = value[Token.ARCHIVE]
            if required == '*':
                for item in ['metadata', 'run']:
                    assert item in archive
            else:
                for item in required:
                    assert item in archive


@pytest.fixture(scope='function')
def example_archive():
    json_dict = {
        'metadata': {'entry_id': 'test_id', 'upload_id': 'test_id'},
        'results': {
            'material': {'material_id': 'random_id'},
            'properties': {
                'electronic': {
                    'dos_electronic': [
                        {'energies': '/run/0/calculation/1/dos_electronic/0/energies'}
                    ]
                }
            },
        },
        'run': [
            {
                'm_def': 'runschema.run.Run',
                'system': [
                    {
                        'atoms': {'labels': ['He']},
                        'symmetry': [{'space_group_number': 221}],
                    },
                    {
                        'atoms': {'labels': ['H']},
                        'symmetry': [{'space_group_number': 221}],
                    },
                ],
                'calculation': [
                    {
                        'system_ref': '/run/0/system/1',
                        'energy': {'total': {'value': 0.1}},
                    },
                    {
                        'system_ref': '/run/0/system/1',
                        'energy': {'total': {'value': 0.2}},
                        'dos_electronic': [{'energies': [0.0, 0.1]}],
                        'eigenvalues': [],
                    },
                    {
                        'system_ref': '/run/0/system/1',
                        'energy': {'total': {'value': 0.1}},
                    },
                ],
            }
        ],
        'workflow2': {
            'm_def': 'simulationworkflowschema.SimulationWorkflow',
            'results': {'calculation_result_ref': '/run/0/calculation/1'},
        },
    }

    archive = EntryArchive.m_from_dict(json_dict)
    assert archive.run is not None
    assert len(archive.run) == 1
    return archive


@pytest.fixture(scope='function')
def example_upload(example_archive, user1, mongo_function, elastic_function):
    data = ExampleData(main_author=user1)
    data.create_upload(
        upload_id='test_id', upload_name='name_published', published=True
    )
    data.create_entry(
        upload_id='test_id', entry_id='test_id', entry_archive=example_archive
    )
    data.save(with_files=True, with_es=True, with_mongo=True)

    yield data

    data.delete()


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
            ),
            id='no-args',
        ),
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
        pytest.param(
            dict(user=None, expected_status_code=200, expected_upload_ids=[]),
            id='no-credentials',
        ),
        pytest.param(
            dict(user='invalid', expected_status_code=401), id='invalid-credentials'
        ),
        pytest.param(
            dict(
                query_params={'is_processing': True},
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
        pytest.param(
            dict(
                query_params={'upload_id': ['id_published']},
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
        pytest.param(
            dict(
                query_params={'upload_name': ['name_published']},
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
                pagination={'page_size': 2},
                expected_upload_ids=['id_embargo', 'id_embargo_w_coauthor'],
            ),
            id='pag-page-1',
        ),
        pytest.param(
            dict(
                pagination={'page_size': 2, 'page': 2},
                expected_upload_ids=['id_embargo_w_reviewer', 'id_unpublished'],
            ),
            id='pag-page-2',
        ),
        pytest.param(
            dict(
                pagination={'page_size': 4, 'page': 3},
                expected_upload_ids=['id_processing', 'id_empty'],
            ),
            id='pag-page-3',
        ),
        pytest.param(
            dict(
                pagination={'page_size': 5, 'page': 3},
                expected_upload_ids=[],
            ),
            id='pag-page-out-of-range',
        ),
        pytest.param(
            dict(
                pagination={'page_size': 2, 'order': 'desc'},
                expected_upload_ids=['id_empty', 'id_processing'],
            ),
            id='pag-page-order-desc',
        ),
    ],
)
@pytest.mark.skipif(simulationworkflowschema is None, reason=SCHEMA_IMPORT_ERROR)
def test_get_uploads_graph(auth_headers, client, example_data, kwargs):
    user = kwargs.get('user', 'user1')
    query_params = kwargs.get('query_params', None)
    pagination = kwargs.get('pagination', None)
    expected_status_code = kwargs.get('expected_status_code', 200)
    expected_upload_ids = kwargs.get('expected_upload_ids', None)
    user_auth = auth_headers[user]

    query_body = {Token.UPLOADS: {'m_request': {}}}

    if query_params is None and pagination is None:
        # noinspection PyTypedDict
        query_body[Token.UPLOADS]['m_request'] = {'directive': 'plain'}
    else:
        if query_params is not None:
            query_body[Token.UPLOADS]['m_request'].setdefault('query', query_params)
        if pagination is not None:
            query_body[Token.UPLOADS]['m_request'].setdefault('pagination', pagination)

    response = client.post(
        'graph/query',
        json=query_body,
        headers={'Accept': 'application/json'} | (user_auth if user_auth else {}),
    )
    result = response.json()

    def assert_upload_ids(a, b):
        assert set(a.keys()) == set(b)

    assert response.status_code == expected_status_code
    if expected_status_code == 200:
        uploads = result.get(Token.UPLOADS, {})
        uploads.pop('m_response', None)
        assert_upload_ids(uploads, expected_upload_ids)


@pytest.mark.parametrize(
    'depth,result',
    [
        pytest.param(
            1,
            {
                'uploads': {
                    'id_unpublished': {
                        'files': {
                            'm_is': 'Directory',
                            'm_response': {
                                'depth': 1,
                                'directive': 'plain',
                                'include': ['*'],
                                'pagination': {
                                    'order': 'asc',
                                    'page': 1,
                                    'page_size': 10,
                                    'total': 1,
                                },
                            },
                            'test_content': {
                                'm_is': 'Directory',
                            },
                        }
                    }
                }
            },
            id='depth-1',
        ),
        pytest.param(
            2,
            {
                'uploads': {
                    'id_unpublished': {
                        'files': {
                            'm_is': 'Directory',
                            'm_response': {
                                'depth': 2,
                                'directive': 'plain',
                                'include': ['*'],
                                'pagination': {
                                    'order': 'asc',
                                    'page': 1,
                                    'page_size': 10,
                                    'total': 2,
                                },
                            },
                            'test_content': {
                                'id_unpublished_1': {
                                    'm_is': 'Directory',
                                },
                                'm_is': 'Directory',
                            },
                        }
                    }
                }
            },
            id='depth-2',
        ),
        pytest.param(
            3,
            {
                'uploads': {
                    'id_unpublished': {
                        'files': {
                            'm_is': 'Directory',
                            'm_response': {
                                'depth': 3,
                                'directive': 'plain',
                                'include': ['*'],
                                'pagination': {
                                    'order': 'asc',
                                    'page': 1,
                                    'page_size': 10,
                                    'total': 7,
                                },
                            },
                            'test_content': {
                                'id_unpublished_1': {
                                    '1.aux': {
                                        'm_is': 'File',
                                        'path': 'test_content/id_unpublished_1/1.aux',
                                        'size': 8,
                                    },
                                    '2.aux': {
                                        'm_is': 'File',
                                        'path': 'test_content/id_unpublished_1/2.aux',
                                        'size': 8,
                                    },
                                    '3.aux': {
                                        'm_is': 'File',
                                        'path': 'test_content/id_unpublished_1/3.aux',
                                        'size': 8,
                                    },
                                    '4.aux': {
                                        'm_is': 'File',
                                        'path': 'test_content/id_unpublished_1/4.aux',
                                        'size': 8,
                                    },
                                    'm_is': 'Directory',
                                    'mainfile.json': {
                                        'm_is': 'File',
                                        'path': 'test_content/id_unpublished_1/mainfile.json',
                                        'size': 3227,
                                    },
                                },
                                'm_is': 'Directory',
                            },
                        }
                    }
                }
            },
            id='depth-3',
        ),
    ],
)
def test_fs_graph(auth_headers, client, example_data, depth, result):
    user_auth = auth_headers['user1']

    response = client.post(
        'graph/query',
        json={
            Token.UPLOADS: {
                'id_unpublished': {
                    Token.RAW: {
                        'm_request': {
                            'directive': 'plain',
                            'depth': depth,
                        }
                    }
                }
            }
        },
        headers={'Accept': 'application/json'} | (user_auth if user_auth else {}),
    )

    assert response.json() == result


@pytest.mark.parametrize(
    'required,error',
    [
        pytest.param('include', False, id='include-all'),
        pytest.param('*', False, id='include-all-alias'),
        # pytest.param({'metadata': '*'}, False, id='include-sub-section'),
        # pytest.param({'metadata': {'entry_id': '*'}}, False, id='include-quantity'),
        pytest.param(
            {
                'workflow2': {
                    'results': {'calculation_result_ref': {'energy': {'total': '*'}}}
                }
            },
            None,
            id='resolve-with-required',
        ),
        pytest.param(
            {'workflow2': {'results': {'calculation_result_ref': 'include-resolved'}}},
            False,
            id='resolve-with-directive',
        ),
        pytest.param(
            {'workflow2': 'include-resolved', 'results': 'include-resolved'},
            False,
            id='include-resolved',
        ),
        pytest.param(
            {
                'results': {
                    'properties': {
                        'electronic': {
                            'dos_electronic': {'energies': 'include-resolved'}
                        }
                    }
                }
            },
            False,
            id='resolve-quantity-ref',
        ),
        pytest.param(
            {'metadata': {'entry_id': {'doesnotexist': '*'}}}, True, id='not-a-section'
        ),
    ],
)
@pytest.mark.skipif(simulationworkflowschema is None, reason=SCHEMA_IMPORT_ERROR)
def test_entry_reader_with_reference(
    example_archive, required, error, user1, example_upload
):
    with EntryReader({Token.ARCHIVE: required}, user=user1) as reader:
        results = reader.sync_read('test_id')

    if error:
        assert 'm_errors' in results[Token.ARCHIVE]
    elif required in ('include', '*'):
        assert_dict(example_archive.m_to_dict(), results[Token.ARCHIVE])
    else:
        full_archive = example_archive.m_to_dict()
        assert isinstance(required, dict)

        def _goto(path, root):
            if isinstance(path, str) and '/' in path:
                current = root
                for item in [i for i in path.split('/') if i]:
                    current = current[int(item) if item.isdigit() else item]
                return current
            return path

        def assert_equal(a, b):
            if isinstance(a, list):
                assert len(a) == len(b)
                for i in range(len(a)):
                    assert_equal(a[i], b[i])
            elif isinstance(a, dict):
                a.pop('m_def', None)
                assert set(a.keys()) == set(b.keys())
                for key in a.keys():
                    assert_equal(a[key], b[key])
            elif not isinstance(a, str) and not isinstance(b, str):
                assert a == b

        def _walk(a, b, root):
            for key, value in root.items():
                key = int(key) if key.isdigit() else key
                # simplified, just consider workflow[0]
                a_child = _goto(
                    a[key] if isinstance(a, dict) else a[0][key], full_archive
                )
                b_child = _goto(b[key] if isinstance(b, dict) else b[0][key], results)

                if not isinstance(value, str):
                    _walk(a_child, b_child, value)
                else:
                    assert_equal(a_child, b_child)

        _walk(full_archive, results[Token.ARCHIVE], required)


@pytest.mark.parametrize(
    'required,result',
    [
        pytest.param(
            {'run[0]': {'m_request': {'directive': 'plain', 'include': ['c*']}}},
            {
                'run': [
                    {
                        'calculation': [
                            {
                                'energy': {'total': {'value': 0.1}},
                                'system_ref': '/run/0/system/1',
                            },
                            {
                                'dos_electronic': [{'energies': [0.0, 0.1]}],
                                'energy': {'total': {'value': 0.2}},
                                'system_ref': '/run/0/system/1',
                            },
                            {
                                'energy': {'total': {'value': 0.1}},
                                'system_ref': '/run/0/system/1',
                            },
                        ]
                    }
                ]
            },
            id='include-pattern',
        ),
    ],
)
def test_graph_query_archive_functionality(
    auth_headers, client, example_upload, required, result
):
    user_auth = auth_headers['user1']
    response = client.post(
        'graph/query',
        json={'uploads': {'test_id': {'entries': {'test_id': {'archive': required}}}}},
        headers={'Accept': 'application/json'} | (user_auth if user_auth else {}),
    )

    assert (
        response.json()['uploads']['test_id']['entries']['test_id']['archive'] == result
    )


def test_graph_query_archive_structure(auth_headers, client, example_upload):
    def structure(depth, **pagination):
        return {
            'm_request': {
                'include_quantities': False,
                'depth': depth,
                'pagination': pagination,
            }
        }

    response = client.post(
        'graph/query',
        json={
            'entries': {
                'test_id': {
                    'archive': {
                        **structure(2, page_size=20),
                        'run': structure(1, page_containing=0),
                        'run[0]': {
                            **structure(2),
                            'calculation': structure(
                                1, page_size=2, page_containing='2'
                            ),
                        },
                    }
                }
            }
        },
        headers={'Accept': 'application/json'} | auth_headers['user1'],
    )
    assert response.status_code == 200
    archive = response.json()['entries']['test_id']['archive']

    assert {'results', 'run', 'workflow2'} <= set(archive.keys())
    assert archive['m_response'] == {
        'subsection_lists': {
            'run': {'pagination': {'page': 1, 'page_size': 10, 'total': 1}}
        }
    }
    run = archive['run'][0]
    assert run['m_response']['subsection_lists']['calculation'] == {
        'pagination': {'page': 2, 'page_size': 2, 'total': 3}
    }
    assert run['calculation'][:2] == [None, None]
    assert isinstance(run['calculation'][2], dict)
    # structure only: no values of the run are returned
    for key, value in run.items():
        if key == 'm_response':
            continue
        assert isinstance(value, dict | list) or value.startswith('__INTERNAL__:'), key


def test_graph_query_numeric_folder(
    auth_headers, client, mongo_function, elastic_function
):
    client.post('uploads', headers=auth_headers['user1'])
    response = client.get('uploads', headers=auth_headers['user1']).json()
    assert len(response['data']) == 1
    upload = response['data'][0]
    upload_id = upload['upload_id']
    numeric_folder_name = '1'

    # create a new empty numeric folder
    client.post(
        f'uploads/{upload_id}/raw-create-dir/base/{numeric_folder_name}',
        headers=auth_headers['user1'],
    )
    inner_folders = ['a', 'b', 'c']
    # Create some inner folders to test the graph query response with numeric folder names

    for folder in inner_folders:
        client.post(
            f'uploads/{upload_id}/raw-create-dir/base/{numeric_folder_name}/{folder}',
            headers=auth_headers['user1'],
        )

    graph_response = client.post(
        'graph/query',
        json={
            'uploads': {
                upload_id: {
                    'files': {
                        'base': {
                            numeric_folder_name: {
                                'm_request': {
                                    'depth': 1,
                                    'directive': 'resolved',
                                    'pagination': {'page': 1, 'page_size': 20},
                                }
                            }
                        },
                    }
                }
            }
        },
        headers=auth_headers['user1'],
    )
    files_response = graph_response.json()['uploads'][upload_id]['files']['base'][
        numeric_folder_name
    ]
    assert all(folder in files_response for folder in inner_folders)
