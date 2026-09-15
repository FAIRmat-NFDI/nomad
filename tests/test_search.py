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

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from unittest.mock import patch

import pytest

from nomad import infrastructure, utils
from nomad.app.v1.models import (
    Direction,
    MetadataPagination,
    MetadataRequired,
    WithQuery,
)
from nomad.config import config
from nomad.datamodel.datamodel import EntryArchive, EntryData, EntryMetadata
from nomad.metainfo.elasticsearch_extension import (
    dtype_separator,
    entry_index,
    entry_type,
    schema_separator,
)
from nomad.metainfo.metainfo import Datetime, Quantity
from nomad.metainfo.util import MEnum
from nomad.search import AuthenticationRequiredError as ARE
from nomad.search import (
    PermissionDeniedError,
    _api_to_es_query,
    delete_by_query,
    normalize_api_query,
    quantity_values,
    refresh,
    search,
    update_by_query,
)
from nomad.utils import deep_get
from nomad.utils.exampledata import ExampleData
from tests.variables import python_schema_name, yaml_schema_name


def split(path):
    return [int(x) if x.isdigit() else x for x in path.split('.')]


def assert_search_upload(
    entries: int | Iterable = -1,
    additional_keys: list[str] = [],
    upload_id: str | None = None,
    **kwargs,
):
    if isinstance(entries, list):
        size = len(entries)
    elif isinstance(entries, int):
        size = entries
    else:
        assert False

    keys = ['entry_id', 'upload_id', 'mainfile']
    refresh()
    body: dict[str, Any] = {}
    body.update(size=10)
    if upload_id is not None:
        body['query'] = dict(match=dict(upload_id=upload_id))

    assert infrastructure.elastic_client is not None
    search_results = infrastructure.elastic_client.search(
        index=config.elastic.entries_index, body=body
    )['hits']

    if size != -1:
        assert search_results['total']['value'] == size

    if search_results['total']['value'] > 0:
        for hit in search_results['hits']:
            hit = utils.flatten_dict(hit['_source'])
            for key, value in kwargs.items():
                assert hit.get(key, None) == value, key

            if 'pid' in hit:
                assert int(hit.get('pid')) > 0

            for key in keys:
                assert key in hit, f'{key} is missing'

            for key in additional_keys:
                assert key in hit, f'{key} is missing'
                assert hit[key] != config.services.unavailable_value

            for coauthor in hit.get('entry_coauthors', []):
                assert coauthor.get('name', None) is not None


def test_mapping_compatibility(elastic_infra):
    from nomad.infrastructure import elastic_client

    v0 = elastic_client.indices.get(config.elastic.entries_index)
    v1 = elastic_client.indices.get(config.elastic.entries_index)

    def get_mapping(index):
        assert len(index) == 1
        index = index[next(iter(index))]
        assert len(index['mappings']) == 1
        return index['mappings'][next(iter(index['mappings']))]

    v0, v1 = get_mapping(v0), get_mapping(v1)

    def compare(a, b, path='', results=None):
        if results is None:
            results = []
        if path != '':
            path += '.'
        for key in set(list(a.keys()) + list(b.keys())):
            if key in a and key in b:
                next_a, next_b = a[key], b[key]
                if isinstance(next_a, dict) and isinstance(next_b, dict):
                    compare(next_a, next_b, f'{path}{key}', results=results)
                    continue

                if next_a == next_b:
                    continue

            results.append(f'{"v0" if key in a else "v1"}:{path}{key}')

        return results

    for diff in compare(v0, v1):
        # assert that there are only top-level differences and mapping types and fields are
        # the same
        assert len([c for c in diff if c == '.']) == 1, diff


def get_schema_fixture(type, request):
    return request.getfixturevalue(f'example_data_schema_{type}')


def get_schema_quantity(type, quantity):
    if quantity is None:
        return None
    if not quantity.startswith('data.'):
        return quantity
    dtype = ''
    if type == 'python':
        name = python_schema_name
    elif type == 'yaml':
        name = yaml_schema_name
        dtype = {
            'data.name': 'str',
            'data.count': 'int',
            'data.frequency': 'float',
            'data.child_repeating.name': 'str',
        }[quantity]
        dtype = f'{dtype_separator}{dtype}'

    return f'{quantity}{schema_separator}{name}{dtype}'


@pytest.fixture()
def example_data(elastic_function, user1):
    data = ExampleData(main_author=user1)
    data.create_upload(upload_id='test_upload_id', published=True, embargo_length=12)
    for i in range(0, 4):
        data.create_entry(
            upload_id='test_upload_id',
            entry_id=f'test_entry_id_{i}',
            mainfile='test_content/test_embargo_entry/mainfile.json',
        )

    data.save(with_files=False, with_mongo=False)


@pytest.fixture()
def example_text_search_data(mongo_module, elastic_function, user1):
    data = ExampleData(main_author=user1)
    data.create_upload(upload_id='test_upload_text_search', published=True)
    data.create_entry(
        upload_id='test_upload_text_search',
        entry_id='test_entry_text_search',
        mainfile='test_content/test_embargo_entry/mainfile.json',
        text_search_contents=['this is a test keyword'],
    )
    data.save(with_files=False, with_mongo=False)

    yield

    data.delete()


@pytest.fixture()
def example_eln_data(elastic_function, user1):
    class DataSection(EntryData):
        text = Quantity(type=str)
        keyword = Quantity(type=MEnum('one', 'two'))
        long = Quantity(type=int)
        double = Quantity(type=float)
        date = Quantity(type=Datetime)
        boolean = Quantity(type=bool)

    data = ExampleData(main_author=user1)
    data.create_upload(upload_id='test_upload_id', published=True, embargo_length=12)

    parameters = [
        ('text', 'test value'),
        ('keyword', 'one'),
        ('long', 1),
        ('double', 1.2),
        ('date', datetime.fromtimestamp(0)),
        ('boolean', False),
    ]

    for index, item in enumerate(parameters):
        quantity, value = item
        archive = EntryArchive(metadata=EntryMetadata(), data=DataSection())
        archive.data.m_set(DataSection.m_def.all_quantities[quantity], value)
        archive.metadata.apply_archive_metadata(archive)

        data.create_entry(
            entry_archive=archive,
            upload_id='test_upload_id',
            entry_id=f'test_entry_id_{index}',
            mainfile=f'test_content/test_embargo_entry/mainfile_{index}.archive.json',
        )

    data.save(with_files=False, with_mongo=False)


def test_index(indices, example_data):
    assert entry_index.get(id='test_entry_id_0') is not None


@pytest.fixture()
def indices(elastic_function):
    pass


def test_indices(indices):
    assert entry_type.quantities.get('entry_id') is not None
    assert entry_type.quantities.get('upload_id') is not None


@pytest.mark.parametrize(
    'api_query, total',
    [
        pytest.param('{}', 4, id='empty'),
        pytest.param(
            '{"results.method.simulation.program_name": "VASP"}', 4, id='match'
        ),
        pytest.param(
            '{"results.method.simulation.program_name": "VASP", "results.method.simulation.dft.xc_functional_type": "dne"}',
            0,
            id='match_all',
        ),
        pytest.param(
            '{"and": [{"results.method.simulation.program_name": "VASP"}, {"results.method.simulation.dft.xc_functional_type": "dne"}]}',
            0,
            id='and',
        ),
        pytest.param(
            '{"or":[{"results.method.simulation.program_name": "VASP"}, {"results.method.simulation.dft.xc_functional_type": "dne"}]}',
            4,
            id='or',
        ),
        pytest.param(
            '{"not":{"results.method.simulation.program_name": "VASP"}}', 0, id='not'
        ),
        pytest.param(
            '{"results.method.simulation.program_name": {"all": ["VASP", "dne"]}}',
            0,
            id='all',
        ),
        pytest.param(
            '{"results.method.simulation.program_name": {"any": ["VASP", "dne"]}}',
            4,
            id='any',
        ),
        pytest.param(
            '{"results.method.simulation.program_name": {"none": ["VASP", "dne"]}}',
            0,
            id='none',
        ),
        pytest.param(
            '{"results.method.simulation.program_name": {"gte": "VASP"}}', 4, id='gte'
        ),
        pytest.param(
            '{"results.method.simulation.program_name": {"gt": "A"}}', 4, id='gt'
        ),
        pytest.param(
            '{"results.method.simulation.program_name": {"lte": "VASP"}}', 4, id='lte'
        ),
        pytest.param(
            '{"results.method.simulation.program_name": {"lt": "A"}}', 0, id='lt'
        ),
    ],
)
def test_search_query(indices, example_data, api_query, total):
    api_query = json.loads(api_query)
    results = search(owner='all', query=WithQuery(query=api_query).query)
    assert results.pagination.total == total  # pylint: disable=no-member


@pytest.mark.parametrize(
    'api_query, is_terms',
    [
        pytest.param({'entry_id:any': ['id-1', 'id-2']}, True, id='term-quantity-any'),
        pytest.param(
            {'text_search_contents:any': ['keyword', 'another']},
            False,
            id='non-term-quantity-any',
        ),
    ],
)
def test_any_query(api_query, is_terms):
    query = normalize_api_query(api_query)
    es_query = _api_to_es_query(query)
    if is_terms:
        assert es_query.to_dict() == {'terms': {'entry_id': ['id-1', 'id-2']}}
    else:
        assert 'bool' in es_query.to_dict()
        assert 'should' in es_query.to_dict()['bool']


class TestsWithGroups:
    @pytest.mark.parametrize(
        'owner, user, exc_or_total',
        [
            pytest.param('admin', None, ARE, id='admin-none'),
            pytest.param('admin', 'user1', PermissionDeniedError, id='admin-user1'),
            pytest.param('admin', 'user0', 15, id='admin-user0'),
            pytest.param('user', None, ARE, id='user-none'),
            pytest.param('user', 'user1', 14, id='user-user1'),
            pytest.param('user', 'user2', 1, id='user-user2'),
            pytest.param('shared', None, ARE, id='shared-none'),
            pytest.param('shared', 'user1', 14, id='shared-user1'),
            pytest.param('shared', 'user2', 10, id='shared-user2'),
            pytest.param('staging', None, ARE, id='staging-none'),
            pytest.param('staging', 'user1', 12, id='staging-user1'),
            pytest.param('staging', 'user2', 10, id='staging-user2'),
            pytest.param('visible', None, 2, id='visible-none'),
            pytest.param('visible', 'user1', 14, id='visible-user1'),
            pytest.param('visible', 'user2', 11, id='visible-user2'),
            pytest.param('public', None, 1, id='public-none'),
            pytest.param('public', 'user1', 1, id='public-user1'),
            pytest.param('public', 'user2', 1, id='public-user2'),
            pytest.param('all', None, 3, id='all-none'),
            pytest.param('all', 'user1', 14, id='all-user1'),
            pytest.param('all', 'user2', 12, id='all-user2'),
        ],
    )
    def test_search_query_groups(
        self,
        users_dict,
        uploads_search_query_groups,
        owner,
        user,
        exc_or_total,
    ):
        user = users_dict.get(user)
        user_id = user.user_id if user is not None else None

        if not isinstance(exc_or_total, int):
            with pytest.raises(exc_or_total):
                search(owner=owner, user_id=user_id)
            return

        results = search(owner=owner, user_id=user_id)
        assert results.pagination.total == exc_or_total


def test_update_by_query(indices, example_data):
    update_by_query(
        update_script="""
            ctx._source.entry_id = "other test id";
        """,
        owner='all',
        query={},
        index='v1',
    )

    entry_index.refresh()

    results = search(owner='all', query=dict(entry_id='other test id'))
    assert results.pagination.total == 4


@pytest.fixture()
def two_user_data(elastic_function, user1, user2):
    for user, upload_id in [(user1, 'user1_upload'), (user2, 'user2_upload')]:
        data = ExampleData(main_author=user)
        data.create_upload(upload_id=upload_id, published=True)
        data.create_entry(
            upload_id=upload_id,
            entry_id=f'{upload_id}_entry',
            mainfile='test_content/test_entry/mainfile.json',
        )
        data.save(with_files=False, with_mongo=False)


def test_update_by_query_owner(indices, two_user_data, user1):
    update_by_query(
        update_script="""
            ctx._source.entry_name = "updated";
        """,
        query={},
        owner='user',
        user_id=user1.user_id,
        refresh=True,
    )

    assert entry_index.get(id='user1_upload_entry')['_source']['entry_name'] == (
        'updated'
    )
    assert entry_index.get(id='user2_upload_entry')['_source'].get('entry_name') != (
        'updated'
    )


def test_delete_by_query_owner(indices, two_user_data, user1):
    delete_by_query(query={}, owner='user', user_id=user1.user_id, refresh=True)

    hits = entry_index.search(body=dict(query=dict(match_all={})))['hits']['hits']
    assert [hit['_id'] for hit in hits] == ['user2_upload_entry']


def test_quantity_values(indices, example_data):
    results = list(quantity_values('entry_id', page_size=1, owner='all'))
    assert results == [
        'test_entry_id_0',
        'test_entry_id_1',
        'test_entry_id_2',
        'test_entry_id_3',
    ]


def test_index_large_payload(indices, elastic_function, user1, monkeypatch):
    monkeypatch.setattr('nomad.config.elastic.max_payload_size', 10)

    data = ExampleData(main_author=user1)

    upload_id = 'test_large_upload_id'

    data.create_upload(upload_id=upload_id, published=True, embargo_length=0)

    num_entries = 10  # A number large enough to trigger batching

    for i in range(num_entries):
        data.create_entry(
            upload_id=upload_id,
            entry_id=f'test_entry_id_{i}',
        )

    # Call the index function, which should now handle batching internally
    from nomad import search
    from nomad.metainfo import elasticsearch_extension

    with patch(
        'nomad.metainfo.elasticsearch_extension._generate_entry_batches',
        wraps=elasticsearch_extension._generate_entry_batches,
    ) as mock_generate_entry_batches:
        search.index(list(data.archives.values()), refresh=True)
        assert mock_generate_entry_batches.called

    # Verify that all entries are indexed
    results = search.search(owner='all', query={'upload_id': upload_id})

    assert results.pagination.total == num_entries

    for i in range(num_entries):
        entry_id = f'test_entry_id_{i}'

        assert search.entry_index.get(id=entry_id) is not None


@pytest.mark.parametrize(
    'api_query, total',
    [
        pytest.param({}, 6, id='empty'),
        pytest.param(
            {
                'search_quantities': {
                    'id': f'data.text{schema_separator}tests.test_search.DataSection',
                    'str_value': 'test',
                }
            },
            1,
            id='simple-positive',
        ),
        pytest.param(
            {
                'search_quantities': {
                    'id': f'data.text{schema_separator}tests.test_search.DataSection',
                    'str_value': 'wrong',
                }
            },
            0,
            id='simple-negative',
        ),
        pytest.param(
            {
                'search_quantities': {
                    'id': f'data.keyword{schema_separator}tests.test_search.DataSection',
                    'str_value': 'one',
                }
            },
            1,
            id='keyword-as-text',
        ),
        pytest.param(
            {
                'search_quantities': {
                    'id': f'data.long{schema_separator}tests.test_search.DataSection',
                    'int_value': {'lte': 1},
                }
            },
            1,
            id='int',
        ),
        pytest.param(
            {
                'search_quantities': {
                    'id': f'data.double{schema_separator}tests.test_search.DataSection',
                    'float_value': {'lt': 1.3},
                }
            },
            1,
            id='float',
        ),
        pytest.param(
            {
                'search_quantities': {
                    'id': f'data.date{schema_separator}tests.test_search.DataSection',
                    'datetime_value': {'lt': '1971-01-01'},
                }
            },
            1,
            id='datetime',
        ),
        pytest.param(
            {
                'search_quantities': {
                    'id': f'data.boolean{schema_separator}tests.test_search.DataSection',
                    'bool_value': False,
                }
            },
            1,
            id='bool',
        ),
        pytest.param(
            {
                'search_quantities': {
                    'id': f'data.text{schema_separator}tests.test_search.DataSection',
                    'str_value': 'one',
                }
            },
            0,
            id='uses-nested',
        ),
    ],
)
def test_search_quantity_data_types(indices, example_eln_data, api_query, total):
    """Tests that search queries targeting search_quantities work for different data types."""
    results = search(owner='all', query=WithQuery(query=api_query).query)
    assert results.pagination.total == total  # pylint: disable=no-member


@pytest.mark.parametrize(
    'api_query',
    [
        pytest.param(
            {f'data.name{schema_separator}{python_schema_name}': 'test1'},
            id='own-quantity',
        ),
        pytest.param(
            {
                f'data.inherited_a{schema_separator}nomadschemaexample.schema.MyBaseSchemaA': 'test1'
            },
            id='inherited-quantity',
        ),
        pytest.param(
            {
                f'data.inherited_b{schema_separator}nomadschemaexample.schema.MyBaseSchemaB': 'test1'
            },
            id='multiple-inheritance',
        ),
    ],
)
@pytest.mark.parametrize('schema_type', ['python'])
def test_search_quantities_inheritance(
    indices, plugin_schema, api_query, schema_type, request
):
    """Tests that queries that target inherited definitions work as expected. TODO:
    Currently only quantities in the data section are registered with inheritance taken
    into account, and there is no syntax for targeting anything else besides this data
    section.
    """
    get_schema_fixture(schema_type, request)
    results = search(owner='all', query=WithQuery(query=api_query).query)
    assert results.pagination.total == 1  # pylint: disable=no-member


@pytest.mark.parametrize(
    'include, exclude, include_response, exclude_response',
    [
        pytest.param(
            None,
            None,
            ['search_quantities.0.str_value', f'data.name'],
            [],
            id='no-required',
        ),
        pytest.param(
            [],
            None,
            [],
            ['search_quantities.0.str_value', f'data.name'],
            id='include-nothing',
        ),
        pytest.param(
            None,
            ['search_quantities*'],
            [f'data.name'],
            ['search_quantities.0.str_value'],
            id='exclude-searchable-quantities',
        ),
        pytest.param(
            None,
            ['search_quantities.str_value'],
            [f'data.name', 'search_quantities.0.id'],
            ['search_quantities/0/str_value'],
            id='exclude-searchable-quantities-subset',
        ),
        pytest.param(
            None,
            [f'data.name'],
            ['search_quantities.0.str_value'],
            [f'data.name'],
            id='exclude-dynamic',
        ),
        pytest.param(
            ['search_quantities.str_value'],
            None,
            ['search_quantities.0.str_value'],
            [f'data.name', 'search_quantities.0.id'],
            id='include-searchable-quantities',
        ),
        pytest.param(
            [f'data.name'],
            None,
            [f'data.name'],
            ['search_quantities/0/id'],
            id='include-dynamic',
        ),
        pytest.param(
            [f'data.count', f'data.frequency'],
            None,
            [f'data.count', f'data.frequency'],
            ['search_quantities.0.int_value', 'search_quantities.0.float_value'],
            id='include-dynamic-multiple',
        ),
        pytest.param(
            None,
            [f'data.name', 'search_quantities*'],
            [],
            [f'data.name', 'search_quantities.0.id'],
            id='exclude-both',
        ),
        pytest.param(
            [f'data.name', 'search_quantities*'],
            None,
            [f'data.name', 'search_quantities.0.id'],
            [],
            id='include-both',
        ),
        pytest.param(
            ['data.child_repeating.name'],
            None,
            [f'data.child_repeating.0.name'],
            [],
            id='repeating-section',
        ),
    ],
)
@pytest.mark.parametrize('schema_type', ['python', 'yaml'])
def test_search_query_dynamic_required(
    indices,
    plugin_schema,
    example_data_schema_python,
    schema_type,
    include,
    exclude,
    include_response,
    exclude_response,
):
    """Tests that the requiring works correctly for dynamic fields."""
    if include:
        include = [get_schema_quantity(schema_type, x) for x in include]
    if exclude:
        exclude = [get_schema_quantity(schema_type, x) for x in exclude]
    results = search(
        owner='all', required=MetadataRequired(include=include, exclude=exclude)
    )

    for path in include_response:
        assert deep_get(results.data, 0, *split(path)) is not None
    for path in exclude_response:
        with pytest.raises(ValueError):
            deep_get(results.data, 0, *split(path))


@pytest.mark.parametrize(
    'include',
    [
        pytest.param([(f'data.name', 'test0')], id='root-section'),
        pytest.param([(f'data.child.name', 'test_child0')], id='nested-section'),
        pytest.param(
            [(f'data.child_repeating.0.name', 'test_child_repeating0')],
            id='nested-repeating-section',
        ),
    ],
)
@pytest.mark.parametrize('schema_type', ['python', 'yaml'])
def test_search_hits_dynamic(indices, plugin_schema, schema_type, include, request):
    """Tests that the response hit structure is properly reconstructed for
    dynamic quantities that target a schema.
    """
    get_schema_fixture(schema_type, request)
    results = search(owner='all')
    for path, value in include:
        assert deep_get(results.data, 0, *split(path)) == value


@pytest.mark.parametrize(
    'order_by, order, expected',
    [
        pytest.param(None, None, None, id='default'),
        pytest.param(
            f'data.name',
            Direction.asc,
            [f'test{i}' for i in [0, 1, 10, 11, 12, 13, 14, 2, 3, 4]],
            id='sort-string-single-asc',
        ),
        pytest.param(
            f'data.name',
            Direction.desc,
            [f'test{i}' for i in [9, 8, 7, 6, 5, 4, 3, 2, 14, 13]],
            id='sort-string-single-desc',
        ),
        pytest.param(f'data.count', Direction.asc, range(10), id='sort-int-single-asc'),
        pytest.param(
            f'data.count', Direction.desc, range(14, 4, -1), id='sort-int-single-desc'
        ),
        pytest.param(
            f'data.frequency',
            Direction.asc,
            [x + 0.5 for x in range(10)],
            id='sort-float-single-asc',
        ),
        pytest.param(
            f'data.frequency',
            Direction.desc,
            [x + 0.5 for x in range(14, 4, -1)],
            id='sort-float-single-desc',
        ),
    ],
)
@pytest.mark.parametrize('schema_type', ['python', 'yaml'])
def test_pagination_dynamic(
    indices, plugin_schema, schema_type, order_by, order, expected, request
):
    """Tests that sorting by a dynamic field works as expected."""
    get_schema_fixture(schema_type, request)
    order_by = get_schema_quantity(schema_type, order_by)
    pagination = MetadataPagination(order_by=order_by, page_size=10)
    if order is not None:
        pagination.order = order
    results = search(owner='all', pagination=pagination)
    if expected:
        assert len(results.data) == len(expected)
        path = order_by.split(schema_separator)[0]
        for i, data in enumerate(results.data):
            assert deep_get(data, *split(path)) == expected[i]


@pytest.mark.parametrize(
    'query, expected',
    [
        pytest.param(
            '{"text_search_contents": "this is a test keyword"}', 1, id='exact match'
        ),
        pytest.param('{"text_search_contents": "keyword"}', 1, id='single word match'),
        pytest.param('{"text_search_contents": "KEYWORD"}', 1, id='case insensitive'),
        pytest.param(
            '{"text_search_contents": "keyw"}', 1, id='also match for partial word'
        ),
        pytest.param('{"text_search_contents": "nope"}', 0, id='no match'),
    ],
)
def test_text_search(example_text_search_data, query, expected):
    """Tests that free-text queries correctly use the correct ES query type to
    return reasonable matches.
    """
    results = search(owner='all', query=WithQuery(query=json.loads(query)).query)
    assert len(results.data) == expected
