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

import pytest

from nomad import utils
from nomad.app.optimade import parse_filter
from nomad.app.optimade.common import provider_specific_fields
from nomad.processing import Upload
from nomad.search import search
from nomad.utils.exampledata import ExampleData
from tests.fixtures.infrastructure import clear_elastic, clear_raw_files


def test_get_entry(published: Upload):
    entry_id = list(published.successful_entries)[0].entry_id
    with published.upload_files.read_archive(entry_id) as archive:
        data = archive[entry_id]
        assert data['metadata']['optimade'] is not None

    search_result = search(owner='all', query=dict(entry_id=entry_id)).data[0]
    assert 'optimade.chemical_formula_hill' in utils.flatten_dict(search_result)


def test_no_optimade(
    mongo_function, elastic_function, raw_files_function, client, user1
):
    example_data = ExampleData(main_author=user1)
    example_data.create_upload(
        upload_id='test_upload', published=True, embargo_length=0
    )
    example_data.create_structure('test_upload', 1, 2, 1, [], 0)
    example_data.create_structure('test_upload', 2, 2, 1, [], 0, optimade=False)
    example_data.save()

    rv = client.get('/optimade/structures')
    assert rv.status_code == 200
    data = rv.json()
    assert data['meta']['data_returned'] == 1


@pytest.fixture(scope='module')
def example_structures(
    elastic_infra,
    elastic_test_indices,
    mongo_db_name,
    mongo_module,
    raw_files_infra,
    user1,
):
    clear_elastic(elastic_infra, elastic_test_indices)
    mongo_module.drop_database(mongo_db_name)

    example_data = ExampleData(main_author=user1)
    example_data.create_upload(
        upload_id='test_upload',
        upload_create_time='1978-04-08T10:10:00Z',
        published=True,
        embargo_length=0,
    )
    example_data.create_structure('test_upload', 1, 2, 1, [], 0)
    example_data.create_structure('test_upload', 2, 2, 1, ['C'], 0)
    example_data.create_structure('test_upload', 3, 2, 1, [], 1)
    example_data.create_structure(
        'test_upload', 4, 1, 1, [], 0, metadata=dict(comment='A comment')
    )
    example_data.save()

    yield
    clear_elastic(elastic_infra, elastic_test_indices)
    clear_raw_files()


@pytest.mark.parametrize(
    'query, results',
    [
        ('nelements > 1', 4),
        ('nelements >= 2', 4),
        ('nelements > 2', 1),
        ('nelements < 4', 4),
        ('nelements < 3', 3),
        ('nelements <= 3', 4),
        ('nelements != 2', 1),
        ('2 != nelements', 1),
        ('1 < nelements', 4),
        ('3 <= nelements', 1),
        ('elements HAS "H"', 4),
        ('elements HAS ALL "H", "O"', 4),
        ('elements HAS ALL "H", "C"', 1),
        ('elements HAS ANY "H", "C"', 4),
        ('elements HAS ANY "C"', 1),
        ('elements HAS ONLY "C"', 0),
        ('elements HAS ALL "H", "O" AND nelements = 2', 3),
        ('elements HAS ONLY "H", "O"', 3),
        ('nelements >= 2 AND elements HAS ONLY "H", "O"', 3),
        ('nelements >= 2 AND elements HAS ALL "H", "O", "C"', 1),
        ('nelements >= 2 AND NOT elements HAS ALL "H", "O", "C"', 3),
        ('NOT nelements = 2 AND elements HAS ANY "H", "O", "C"', 1),
        ('NOT nelements = 3 AND NOT elements HAS ONLY "H", "O"', 0),
        ('elements:elements_ratios HAS "H":>0.66', 2),
        ('elements:elements_ratios HAS ALL "O":>0.33', 3),
        ('elements:elements_ratios HAS ALL "O":>0.33,"O":<0.34', 2),
        ('elements IS KNOWN', 4),
        ('elements IS UNKNOWN', 0),
        ('chemical_formula_reduced = "H2O"', 2),
        ('chemical_formula_reduced CONTAINS "H2"', 3),
        ('chemical_formula_reduced CONTAINS "H"', 4),
        ('chemical_formula_reduced CONTAINS "C"', 1),
        ('chemical_formula_reduced STARTS "H2"', 2),
        ('chemical_formula_reduced STARTS WITH "H2"', 2),
        ('chemical_formula_reduced ENDS WITH "O"', 4),
        ('chemical_formula_reduced ENDS "C"', 0),
        ('chemical_formula_hill CONTAINS "1"', 0),
        (
            'chemical_formula_hill STARTS WITH "H" AND chemical_formula_hill ENDS WITH "O"',
            3,
        ),
        ('NOT chemical_formula_descriptive ENDS WITH "1"', 4),
        (
            'chemical_formula_descriptive CONTAINS "C" AND NOT chemical_formula_descriptive STARTS WITH "O"',
            1,
        ),
        ('NOT chemical_formula_anonymous STARTS WITH "A"', 0),
        (
            'chemical_formula_anonymous CONTAINS "A2B" AND chemical_formula_anonymous ENDS WITH "C"',
            1,
        ),
        ('nsites >=3 AND elements LENGTH = 2', 2),
        ('elements LENGTH = 2', 3),
        ('elements LENGTH 2', 3),
        ('elements LENGTH = 3', 1),
        ('nperiodic_dimensions = 0', 3),
        ('nperiodic_dimensions = 1', 1),
        ('nelements = 2 AND nperiodic_dimensions = 1', 1),
        ('nelements = 3 AND nperiodic_dimensions = 1', 0),
        ('nelements = 3 OR nperiodic_dimensions = 1', 2),
        ('nelements > 1 OR nperiodic_dimensions = 1 AND nelements = 2', 4),
        ('(nelements > 1 OR nperiodic_dimensions = 1) AND nelements = 2', 3),
        ('NOT nperiodic_dimensions = 1', 3),
        ('nelements LENGTH = 1', -1),
        ('LENGTH nelements = 1', -1),
        ('chemical_formula_anonymous starts with "A"', -1),
        ('elements HAS ONY "H", "O"', -1),  # typos: ignore
        ('last_modified >= "2009-02-01T20:07:00Z"', 0),
        ('_nmd_results_material_structural_type = "molecule / cluster"', 3),
        ('_nmd_results_material_chemical_formula_reduced = "H20"', 0),
    ],
)
def test_optimade_parser(example_structures, query, results):
    if results >= 0:
        query = parse_filter(query)
        search_result = search(query=query)
        assert search_result.pagination.total == results
    else:
        with pytest.raises(Exception):
            query = parse_filter(query)


def test_list_endpoint(client, example_structures):
    rv = client.get('/optimade/structures')
    assert rv.status_code == 200
    data = rv.json()
    for entry in ['data', 'links', 'meta']:
        assert entry in data
    assert len(data['data']) == 4


def assert_eq_attrib(data, key, ref, item=None):
    if item is None:
        assert data['data']['attributes'][key] == ref
    else:
        assert data['data'][item]['attributes'][key] == ref


@pytest.mark.parametrize('limit, offset, results', [(1, 1, 1), (3, 2, 2), (5, 0, 4)])
def test_list_endpoint_pagination(client, example_structures, limit, offset, results):
    rv = client.get(f'/optimade/structures?page_limit={limit}&page_offset={offset}')
    assert rv.status_code == 200
    data = rv.json()
    assert len(data['data']) == results


@pytest.mark.parametrize('sort, order', [('nelements', 1), ('-nelements', -1)])
def test_list_endpoint_sort(client, example_structures, sort, order):
    rv = client.get(f'/optimade/structures?sort={sort}')
    assert rv.status_code == 200
    data = rv.json()['data']

    assert len(data) > 0
    for i, item in enumerate(data):
        if i > 0:
            if order == 1:
                assert (
                    item['attributes']['nelements']
                    >= data[i - 1]['attributes']['nelements']
                )
            else:
                assert (
                    item['attributes']['nelements']
                    <= data[i - 1]['attributes']['nelements']
                )


def test_list_endpoint_response_fields(client, example_structures):
    rv = client.get('/optimade/structures?response_fields=nelements,elements')
    assert rv.status_code == 200, json.dumps(rv.json(), indent=2)
    data = rv.json()
    ref_elements = [['H', 'O'], ['C', 'H', 'O'], ['H', 'O'], ['H', 'O']]
    data['data'] = sorted(data['data'], key=lambda x: x['id'])
    for i in range(len(data['data'])):
        rf = sorted(list(data['data'][i]['attributes'].keys()))
        assert rf == ['elements', 'nelements']
        assert_eq_attrib(data, 'elements', ref_elements[i], i)
        assert_eq_attrib(data, 'nelements', len(ref_elements[i]), i)


def test_single_endpoint_response_fields(client, example_structures):
    rv = client.get(
        '/optimade/structures/{}?response_fields=nelements,elements'.format(
            'test_entry_id_1'
        )
    )
    assert rv.status_code == 200, json.dumps(rv.json(), indent=2)
    data = rv.json()
    ref_elements = ['H', 'O']
    rf = sorted(list(data['data']['attributes'].keys()))
    assert rf == ['elements', 'nelements']
    assert_eq_attrib(data, 'elements', ref_elements)
    assert_eq_attrib(data, 'nelements', len(ref_elements))


def test_single_endpoint(client, example_structures):
    rv = client.get('/optimade/structures/{}'.format('test_entry_id_1'))
    assert rv.status_code == 200
    data = rv.json()
    for key in ['type', 'id', 'attributes']:
        assert key in data['data']
    fields = [
        'elements',
        'nelements',
        'elements_ratios',
        'chemical_formula_descriptive',
        'chemical_formula_reduced',
        'chemical_formula_hill',
        'chemical_formula_anonymous',
        'dimension_types',
        'lattice_vectors',
        'cartesian_site_positions',
        'nsites',
        'species_at_sites',
        'species',
    ]
    for field in fields:
        assert field in data['data']['attributes']


def test_base_info_endpoint(client):
    rv = client.get('/optimade/info')
    assert rv.status_code == 200
    data = rv.json()
    for key in ['type', 'id', 'attributes']:
        assert key in data['data']
    assert data['data']['type'] == 'info'
    assert data['data']['id'] == '/'


def test_entry_info_endpoint(client):
    rv = client.get('/optimade/info/structures')
    assert rv.status_code == 200
    data = rv.json()
    for key in ['description', 'properties', 'formats', 'output_fields_by_format']:
        assert key in data['data']

    # TODO this does not seem to be supported by optimade-python-tools
    # assert '_nmd_atoms' in data['data']['properties']
    # assert '_nmd_dft_system' in data['data']['properties']


def test_links_endpoint(client, example_structures):
    rv = client.get('/optimade/links')
    assert rv.status_code == 200
    data = rv.json()

    nomad_link = next(link for link in data['data'] if link['id'] == 'index')
    assert nomad_link['attributes']['base_url'].endswith('/nmd')


def test_structures_endpoint(client, example_structures):
    rv = client.get('/optimade/structures')
    assert rv.status_code == 200
    data = rv.json()
    assert len(data['data']) == 4
    for d in data['data']:
        for key in ['id', 'attributes']:
            assert d.get(key) is not None
        required_keys = [
            'last_modified',
            'elements',
            'nelements',
            'elements_ratios',
            'chemical_formula_descriptive',
            'chemical_formula_reduced',
            'chemical_formula_anonymous',
            'dimension_types',
            'cartesian_site_positions',
            'nsites',
            'species_at_sites',
            'species',
            'structure_features',
        ]
        for key in required_keys:
            assert key in d['attributes']


def test_structure_endpoint(client, example_structures):
    rv = client.get('/optimade/structures/{}'.format('test_entry_id_1'))
    assert rv.status_code == 200
    data = rv.json()
    assert data.get('data') is not None
    attr = data['data'].get('attributes')
    assert attr is not None
    assert attr.get('elements') == ['H', 'O']
    assert len(attr.get('dimension_types')) == 3


def test_nmd_properties_info(client, example_structures):
    rv = client.get('/optimade/info/structures')
    assert rv.status_code == 200
    data = rv.json()
    assert '_nmd_results_material_structural_type' in data['data']['properties']
    assert (
        '_nmd_results_material_chemical_formula_reduced' in data['data']['properties']
    )
    assert '_nmd_results_material_elements' in data['data']['properties']
    assert '_nmd_archive_url' in data['data']['properties']


def test_nmd_properties(client, example_structures):
    rv = client.get(
        '/optimade/structures/{}'.format(
            'test_entry_id_1?response_fields=_nmd_results_material_elements,_nmd_results_material_structural_type,_nmd_doesnotexist,_nmd_archive_url'
        )
    )
    assert rv.status_code == 200
    data = rv.json()
    assert data.get('data') is not None
    attr = data['data'].get('attributes')
    assert attr is not None

    assert attr.get('_nmd_results_material_elements') == ['H', 'O']
    assert '_nmd_results_material_structural_type' in attr
    assert attr['_nmd_doesnotexist'] is None
    assert '_nmd_archive_url' in attr


def test_nmd_properties_include_all(client, example_structures):
    all_fields = [f'_nmd_{name}' for name in provider_specific_fields()]
    rv = client.get(
        f'/optimade/structures/test_entry_id_1?response_fields={",".join(all_fields)}'
    )
    assert rv.status_code == 200
    data = rv.json()
    assert data.get('data') is not None
    attr = data['data'].get('attributes')
    assert attr is not None
    assert attr.get('_nmd_results_material_elements') == ['H', 'O']
    assert '_nmd_results_material_structural_type' in attr
    assert '_nmd_results_material_chemical_formula_reduced' in attr
