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

from datetime import date
from unittest.mock import Mock

import numpy as np
import pytest
from elasticsearch_dsl import Keyword

from nomad.config import config
from nomad.datamodel.datamodel import SearchableQuantity
from nomad.metainfo import Datetime, MEnum, MSection, Quantity, SubSection, Unit
from nomad.metainfo.elasticsearch_extension import (
    Elasticsearch,
    _generate_entry_batches,
    create_indices,
    create_searchable_quantity,
    entry_index,
    entry_type,
    index_entries,
)
from nomad.utils.exampledata import ExampleData
from tests.app.v1.routers.common import perform_quantity_search_test
from tests.fixtures.infrastructure import clear_elastic_infra


@pytest.fixture(scope='module')
def example_data_normalizers(
    elastic_module,
    raw_files_module,
    mongo_module,
    user1,
    user2,
    normalized,
):
    data = ExampleData(main_author=user1)
    upload_id = 'normalizer_upload'

    data.create_upload(upload_id=upload_id, published=True)
    data.create_entry(
        upload_id=upload_id,
        entry_id='normalizer_entry',
        material_id='normalizer_material',
        mainfile='test_content/test_entry/mainfile.json',
        results={'material': {'chemical_formula_hill': 'H2O'}},
    )

    data.save()

    yield

    data.delete()
    from nomad.search import search

    assert search(query=dict(upload_id=upload_id)).pagination.total == 0


@pytest.fixture(scope='module')
def example_data_large_keyword(
    elastic_module,
    raw_files_module,
    mongo_module,
    user1,
    user2,
    normalized,
):
    data = ExampleData(main_author=user1)
    upload_id = 'id_search_quantities_index'

    data.create_upload(upload_id=upload_id, upload_name=upload_id, published=True)
    data.create_entry(
        upload_id=upload_id,
        entry_id=f'test_entry',
        mainfile=f'test_content/test.archive.json',
        search_quantities=[
            SearchableQuantity(
                id=f'data.name',
                definition=f'data.name',
                path_archive='data.name',
                str_value=' '.join(['test'] * 10000),
            ),
        ],
    )
    data.save(with_files=False, with_mongo=False)

    yield

    # The data is deleted
    data.delete()


@pytest.mark.parametrize(
    'quantity, search_str, response_str',
    [
        pytest.param(
            'results.material.chemical_formula_hill', 'OH2', 'H2O', id='formula-reorder'
        )
    ],
)
def test_normalizer(
    quantity, search_str, response_str, api_v1, example_data_normalizers
):
    """Test that the normalizer specified for different annotations works
    properly in the API queries.
    """
    perform_quantity_search_test(quantity, search_str, response_str, api_v1)


def test_keyword_ignore(example_data_large_keyword):
    """Test that large keywords are ignored correctly."""
    # 'match' queries against the 'text' mapping should still work
    from nomad.search import search

    assert search(query={'search_quantities.str_value': 'test'}).pagination.total == 1


class Material(MSection):
    material_id = Quantity(type=str, a_elasticsearch=Elasticsearch())

    formula = Quantity(
        type=str,
        a_elasticsearch=[
            Elasticsearch(),
            Elasticsearch(field='text', mapping='text'),
        ],
    )

    springer_labels = Quantity(
        type=str,
        shape=['*'],
        a_elasticsearch=(Elasticsearch(mapping=Keyword())),
    )


class Data(MSection):
    n_points = Quantity(
        type=int,
        derived=lambda data: len(data.points[0]) if data.points is not None else 0,
        a_elasticseach=Elasticsearch(),
    )

    points = Quantity(type=np.dtype(np.float64), shape=['*', '*'])

    n_series = Quantity(
        type=int,
        derived=lambda data: len(data.series) if data.series is not None else 0,
    )

    series = Quantity(type=np.dtype(np.float64), shape=['*'])


class Dos(MSection):
    channel = Quantity(type=int, a_elasticsearch=Elasticsearch())


class Properties(MSection):
    available_properties = Quantity(
        type=str, shape=['*'], a_elasticsearch=Elasticsearch()
    )

    band_gap = Quantity(type=float, unit='J', a_elasticsearch=Elasticsearch())

    data = Quantity(type=Data, a_elasticsearch=Elasticsearch())

    n_series = Quantity(type=Data.n_series, a_elasticsearch=Elasticsearch())

    dos = SubSection(
        sub_section=Dos.m_def, repeats=True, a_elasticsearch=Elasticsearch(nested=True)
    )


class Results(MSection):
    material = SubSection(sub_section=Material.m_def)
    properties = SubSection(sub_section=Properties.m_def)


class User(MSection):
    user_id = Quantity(type=str, a_elasticsearch=Elasticsearch())
    name = Quantity(type=str, a_elasticsearch=Elasticsearch())


class Entry(MSection):
    entry_id = Quantity(type=str, a_elasticsearch=Elasticsearch())

    upload_id = Quantity(
        type=str,
        a_elasticsearch=Elasticsearch(metrics=dict(uploads='cardinality')),
    )

    mainfile = Quantity(
        type=str,
        a_elasticsearch=Elasticsearch(index=False, value=lambda _: 'other_mainfile'),  # type: ignore
    )

    files = Quantity(
        type=str,
        shape=['*'],
        a_elasticsearch=[
            Elasticsearch(_es_field='keyword'),
            Elasticsearch(mapping='text', field='path', _es_field=''),
        ],
    )

    upload_create_time = Quantity(type=Datetime, a_elasticsearch=Elasticsearch())

    entry_create_time = Quantity(type=Datetime, a_elasticsearch=Elasticsearch())

    publish_time = Quantity(type=Datetime, a_elasticsearch=Elasticsearch())

    results = SubSection(
        sub_section=Results.m_def,
        a_eleasticsearch=Elasticsearch(auto_include_subsections=True),
    )
    data = SubSection(sub_section=Data.m_def)

    viewers = Quantity(type=User, shape=['*'], a_elasticsearch=Elasticsearch())

    not_indexed = Quantity(type=str)


def assert_mapping(
    mapping: dict, path: str, es_type: str, field: str | None = None, **kwargs
):
    for segment in path.split('.'):
        assert 'properties' in mapping
        mapping = mapping['properties'].get(segment)

    if es_type is None:
        assert mapping is None
    else:
        assert mapping is not None
        if field is not None:
            mapping = mapping['fields'][field]

        assert mapping.get('type') == es_type
        for key, value in kwargs.items():
            assert mapping.get(key) == value


def assert_entry_indexed(entry: Entry):
    entry_doc = entry_index.get(id=entry.entry_id)['_source']
    assert entry_doc['entry_id'] == entry.entry_id
    assert (
        entry_doc['results']['material']['material_id']
        == entry.results.material.material_id
    )


def assert_entries_indexed(entries: list[Entry]):
    """
    Assert that the given entries and only the given entries are indexed, and that
    their material data is up to date.
    """
    entry_docs = {
        hit['_source']['entry_id']: hit['_source']
        for hit in entry_index.search(body=dict(query=dict(match_all={})))['hits'][
            'hits'
        ]
    }

    assert sorted(entry_docs) == sorted(entry.entry_id for entry in entries)

    assert Material.m_def is not None
    for entry in entries:
        if entry.results is None or entry.results.material is None:
            continue
        material = entry.results.material
        material_doc = entry_docs[entry.entry_id]['results']['material']
        for quantity in Material.m_def.quantities:
            if material.m_is_set(quantity):
                assert material_doc[quantity.name] == getattr(material, quantity.name)
            else:
                assert quantity.name not in material_doc


@pytest.fixture
def example_entry():
    entry = Entry()
    entry.entry_id = 'test_entry_id'
    entry.m_create(Results).m_create(Material, material_id='test_material_id')

    return entry


@pytest.fixture(scope='module')
def indices(elastic_infra, elastic_test_indices):
    # remove whatever the infrastructure created by default
    from nomad.infrastructure import elastic_client

    try:
        elastic_client.indices.delete(index=config.elastic.entries_index)
    except Exception:
        pass

    create_indices(Entry.m_def)
    yield
    # re-establish the default elasticsearch setup.
    clear_elastic_infra(elastic_test_indices)


def test_mappings(indices):
    entry_mapping = entry_type.mapping

    assert_mapping(entry_mapping, 'entry_id', 'keyword')
    assert_mapping(entry_mapping, 'mainfile', 'keyword', index=False)
    assert_mapping(entry_mapping, 'upload_create_time', 'date')
    assert_mapping(entry_mapping, 'results.material.material_id', 'keyword')
    assert_mapping(entry_mapping, 'results.material.formula', 'keyword')
    assert_mapping(entry_mapping, 'results.material.formula', 'text', 'text')
    assert_mapping(entry_mapping, 'results.properties.available_properties', 'keyword')
    assert_mapping(entry_mapping, 'results.properties.band_gap', 'double')
    assert_mapping(entry_mapping, 'results.properties.data.n_points', 'integer')
    assert_mapping(entry_mapping, 'results.properties.n_series', 'integer')
    assert_mapping(entry_mapping, 'viewers.user_id', 'keyword')
    assert_mapping(entry_mapping, 'viewers.name', 'keyword')
    assert_mapping(entry_mapping, 'files', 'text')
    assert_mapping(entry_mapping, 'files', 'keyword', 'keyword')
    assert_mapping(entry_mapping, 'results.properties.dos', 'nested')
    assert_mapping(entry_mapping, 'results.properties.dos.channel', 'integer')

    formula_annotations = Material.formula.m_get_annotations(Elasticsearch)
    assert (
        entry_type.quantities.get('results.material.formula').annotation
        == formula_annotations[0]
    )
    assert (
        entry_type.quantities.get('results.material.formula.text').annotation
        == formula_annotations[1]
    )

    files_annotations = Entry.files.m_get_annotations(Elasticsearch)
    assert files_annotations[0].name == 'files'
    assert files_annotations[1].name == 'files.path'
    assert entry_type.quantities.get('files').annotation == files_annotations[0]
    assert entry_type.quantities.get('files.path').annotation == files_annotations[1]

    assert entry_type.metrics['uploads'] == (
        'cardinality',
        entry_type.quantities['upload_id'],
    )

    assert 'viewers.user_id' in entry_type.quantities
    assert Entry.viewers in entry_type.indexed_properties
    assert User.user_id in entry_type.indexed_properties


def test_index_docs(indices):
    user = User(user_id='test_user_id', name='Test User')
    entry = Entry(
        entry_id='test_entry_id',
        mainfile='test_mainfile',
        viewers=[user, user],
        not_indexed='value',
    )
    data = entry.m_create(Data, points=[[0.1, 0.2], [1.1, 1.2]])
    results = entry.m_create(Results)
    results.m_create(
        Material,
        material_id='test_material_id',
        formula='H20',
        springer_labels=['water'],
    )
    results.m_create(
        Properties,
        data=data,
        n_series=data,
        band_gap=1e-12,
        available_properties=['data', 'band_gap'],
    )

    entry_doc = entry_type.create_index_doc(entry)

    assert entry_doc == {
        'entry_id': 'test_entry_id',
        'mainfile': 'other_mainfile',
        'viewers': [
            {'user_id': 'test_user_id', 'name': 'Test User'},
            {'user_id': 'test_user_id', 'name': 'Test User'},
        ],
        'results': {
            'material': {
                'material_id': 'test_material_id',
                'formula': 'H20',
                'springer_labels': ['water'],
            },
            'properties': {
                'available_properties': ['data', 'band_gap'],
                'band_gap': 1e-12,
                'data': {'n_points': 2},
                'n_series': 0,
            },
        },
    }


def test_index_entry(elastic_function, indices, example_entry):
    index_entries([example_entry], refresh=True)
    assert_entry_indexed(example_entry)


@pytest.mark.parametrize(
    'metainfo_type,es_type',
    [
        [str, 'keyword'],
        [np.dtype(np.float32), 'float'],
        [float, 'double'],
        [np.dtype(np.float64), 'double'],
        [int, 'integer'],
        [np.dtype(np.int32), 'integer'],
        [np.dtype(np.int64), 'long'],
        [bool, 'boolean'],
        [Datetime, 'date'],
        [Unit, 'keyword'],
        [MEnum('test'), 'keyword'],
    ],
)
def test_mapping_detection(metainfo_type, es_type):
    """Tests that the mappings are correctly determined from the quantity type."""
    a = Quantity(type=metainfo_type, a_elasticsearch=Elasticsearch())
    a.__init_metainfo__()
    assert a.m_annotations['elasticsearch'].mapping['type'] == es_type


def create_entry(spec: str, material_kwargs: dict | None = None):
    entry_id, material_id = spec.split('-')
    changed = material_id.endswith('*')
    if changed:
        material_id = material_id[:-1]
    entry = Entry(entry_id=entry_id)
    entry.m_create(Results).m_create(
        Material,
        material_id=material_id,
        springer_labels=['A', 'B'] if changed else ['A'],
    )
    return entry


def create_entries(spec: str):
    return [
        create_entry(entry_spec.strip())
        for entry_spec in spec.split(',')
        if entry_spec.strip() != ''
    ]


# The parameters are before, to_index, after. Before and after describes what is in the index
# before and after the test. To_index describes what is to be indexed in the test.
# Both are strings with csv, where each value is a dash separated pair of numbers. The
# first number describes an entry id, the second the material id of that entry.
@pytest.mark.parametrize(
    'before,to_index,after',
    [
        pytest.param('', '1-1', '1-1', id='add-first-material'),
        pytest.param('1-1', '1-1', '1-1', id='updated-material'),
        pytest.param('1-1', '2-2', '1-1, 2-2', id='added-new-material'),
        pytest.param('1-1', '2-1', '1-1, 2-1', id='added-entry-to-material'),
        pytest.param('1-1', '1-2', '1-2', id='moved-entry-between-materials-empty'),
        pytest.param(
            '1-1, 2-1', '1-2', '2-1, 1-2', id='moved-entry-between-materials-remaining'
        ),
        pytest.param('1-1', '1-1*', '1-1*', id='update-material-property'),
    ],
)
def test_index_entries(elastic_function, indices, before, to_index, after):
    index_entries(create_entries(before), refresh=True)
    index_entries(create_entries(to_index), refresh=True)

    assert_entries_indexed(create_entries(after))


class ClassA(MSection):
    float_value = Quantity(type=float)
    int_value = Quantity(type=int)
    str_value = Quantity(type=str)
    datetime_value = Quantity(type=Datetime)
    bool_value = Quantity(type=bool)


class ClassB(MSection):
    multiple_inheritance = Quantity(type=bool)


class ClassC(ClassA, ClassB):
    new_value = Quantity(type=bool)


class ClassD(ClassC):
    pass


@pytest.mark.parametrize(
    'quantity_def, path, sections, expected',
    [
        pytest.param(
            ClassA.float_value,
            'float_value',
            [ClassC(float_value=1.2)],
            SearchableQuantity(
                float_value=1.2,
                id='float_value#test',
                definition='tests.metainfo.test_elasticsearch_extension.ClassA.float_value',
                path_archive='test',
                segments={
                    '1': {
                        'path': 'float_value',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassA',
                        ],
                    },
                },
            ),
            id='float',
        ),
        pytest.param(
            ClassA.int_value,
            'int_value',
            [ClassC(int_value=3)],
            SearchableQuantity(
                int_value=3,
                id='int_value#test',
                definition='tests.metainfo.test_elasticsearch_extension.ClassA.int_value',
                path_archive='test',
                segments={
                    '1': {
                        'path': 'int_value',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassA',
                        ],
                    },
                },
            ),
            id='int',
        ),
        pytest.param(
            ClassA.str_value,
            'str_value',
            [ClassC(str_value='testing')],
            SearchableQuantity(
                str_value='testing',
                id='str_value#test',
                definition='tests.metainfo.test_elasticsearch_extension.ClassA.str_value',
                path_archive='test',
                segments={
                    '1': {
                        'path': 'str_value',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassA',
                        ],
                    },
                },
            ),
            id='str',
        ),
        pytest.param(
            ClassA.datetime_value,
            'datetime_value',
            [ClassC(datetime_value=date(2000, 12, 31))],
            SearchableQuantity(
                datetime_value=date(2000, 12, 31).isoformat(),
                id='datetime_value#test',
                definition='tests.metainfo.test_elasticsearch_extension.ClassA.datetime_value',
                path_archive='test',
                segments={
                    '1': {
                        'path': 'datetime_value',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassA',
                        ],
                    },
                },
            ),
            id='date',
        ),
        pytest.param(
            ClassA.bool_value,
            'bool_value',
            [ClassC(bool_value=True)],
            SearchableQuantity(
                bool_value=True,
                id='bool_value#test',
                definition='tests.metainfo.test_elasticsearch_extension.ClassA.bool_value',
                path_archive='test',
                segments={
                    '1': {
                        'path': 'bool_value',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassA',
                        ],
                    },
                },
            ),
            id='bool',
        ),
        pytest.param(
            ClassA.float_value,
            'data.float_value',
            [None, ClassA(float_value=1)],
            SearchableQuantity(
                float_value=1,
                id='data.float_value#test',
                definition='tests.metainfo.test_elasticsearch_extension.ClassA.float_value',
                path_archive='test',
                segments={
                    '1': {
                        'path': 'data',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassA',
                        ],
                    },
                    '2': {
                        'path': 'float_value',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassA',
                        ],
                    },
                },
            ),
            id='sections',
        ),
        pytest.param(
            ClassC.multiple_inheritance,
            'data.multiple_inheritance',
            [None, ClassC(multiple_inheritance=True)],
            SearchableQuantity(
                bool_value=True,
                id='data.multiple_inheritance#test',
                definition='tests.metainfo.test_elasticsearch_extension.ClassB.multiple_inheritance',
                path_archive='test',
                segments={
                    '1': {
                        'path': 'data',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassC',
                            'tests.metainfo.test_elasticsearch_extension.ClassA',
                            'tests.metainfo.test_elasticsearch_extension.ClassB',
                        ],
                    },
                    '2': {
                        'path': 'multiple_inheritance',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassB',
                        ],
                    },
                },
            ),
            id='multiple-inheritance',
        ),
        pytest.param(
            ClassC.new_value,
            'new_value',
            [ClassC(new_value=True)],
            SearchableQuantity(
                bool_value=True,
                id='new_value#test',
                definition='tests.metainfo.test_elasticsearch_extension.ClassC.new_value',
                path_archive='test',
                segments={
                    '1': {
                        'path': 'new_value',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassC',
                        ],
                    }
                },
            ),
            id='new-value',
        ),
        pytest.param(
            ClassD.bool_value,
            'data.bool_value',
            [None, ClassD(bool_value=True)],
            SearchableQuantity(
                bool_value=True,
                id='data.bool_value#test',
                definition='tests.metainfo.test_elasticsearch_extension.ClassA.bool_value',
                path_archive='test',
                segments={
                    '1': {
                        'path': 'data',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassD',
                            'tests.metainfo.test_elasticsearch_extension.ClassC',
                            'tests.metainfo.test_elasticsearch_extension.ClassA',
                            'tests.metainfo.test_elasticsearch_extension.ClassB',
                        ],
                    },
                    '2': {
                        'path': 'bool_value',
                        'definitions': [
                            'tests.metainfo.test_elasticsearch_extension.ClassA',
                        ],
                    },
                },
            ),
            id='multi-level-inheritance',
        ),
        pytest.param(
            ClassC.float_value,
            'float_value',
            [ClassC(float_value=float('NaN'))],
            None,
            id='nan',
        ),
        pytest.param(
            ClassC.float_value,
            'float_value',
            [ClassC(float_value=float('Infinity'))],
            None,
            id='infinity',
        ),
        pytest.param(
            ClassC.float_value,
            'float_value',
            [ClassC(float_value=float('-Infinity'))],
            None,
            id='-infinity',
        ),
        pytest.param(
            ClassC.float_value,
            'float_value',
            [ClassC(float_value=None)],
            None,
            id='none',
        ),
    ],
)
def test_create_searchable_quantity(quantity_def, path, sections, expected):
    searchable_quantity = create_searchable_quantity(
        quantity_def, path, sections, 'test', 'test'
    )
    if expected is None:
        assert searchable_quantity is None
    else:
        assert searchable_quantity.m_to_dict() == expected.m_to_dict()


class MockEntryType:
    """Mock entry_type object for testing."""

    def create_index_doc(self, entry):
        """Returns a mock document with size proportional to entry data."""
        return {'entry_id': entry['entry_id'], 'data': entry.get('data', '')}


@pytest.fixture
def mock_logger():
    """Fixture providing a mock logger."""
    return Mock()


@pytest.fixture
def mock_entry_type():
    """Fixture providing a mock entry_type."""
    return MockEntryType()


@pytest.mark.parametrize(
    'entries,max_size,expected_batch_sizes',
    [
        pytest.param([], 1000, [], id='empty'),
        pytest.param(
            [{'entry_id': 'e1', 'data': 'x'}],
            1000,
            [1],
            id='single-small-entry',
        ),
        pytest.param(
            [{'entry_id': f'e{i}', 'data': 'x' * 10} for i in range(5)],
            5000,
            [5],
            id='multiple-entries-one-batch',
        ),
        pytest.param(
            [{'entry_id': f'e{i}', 'data': 'x' * 100} for i in range(10)],
            500,
            [3, 3, 3, 1],
            id='multiple-entries-multiple-batches',
        ),
        pytest.param(
            [{'entry_id': 'e1', 'data': 'x' * 1000}],
            100,
            [1],
            id='single-large-entry-exceeding-max_size',
        ),
        pytest.param(
            [
                {'entry_id': 'e1', 'data': 'x' * 100},
                {'entry_id': 'e2', 'data': 'x' * 200},
                {'entry_id': 'e3', 'data': 'x' * 100},
            ],
            500,
            [2, 1],
            id='mixed-size-batches',
        ),
    ],
)
def test_generate_entry_batches(
    entries, max_size, expected_batch_sizes, mock_entry_type, mock_logger
):
    """Test that _generate_entry_batches generates the expected number of batches."""
    batches = list(
        _generate_entry_batches(entries, mock_entry_type, max_size, mock_logger)
    )
    # Verify that the number of batches matches expected
    assert len(batches) == len(expected_batch_sizes)

    # Verify that each batch has the expected size
    for batch, expected_size in zip(batches, expected_batch_sizes):
        actual_size = len(batch) // 2
        assert actual_size == expected_size

    # Verify total number of entries processed
    total_entries_processed = sum(len(batch) // 2 for batch in batches)
    assert total_entries_processed == len(entries)
