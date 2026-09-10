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

import jsonschema
import pytest

from nomad.metainfo.util import SCHEMA_ENDPOINT
from tests.metainfo.test_metainfo import SectionWithBoth, Simulation, unit_quantity

from .common import assert_response


@pytest.mark.parametrize(
    'identifier, expected_status',
    [
        pytest.param('nonexistent', 404, id='non-existent-module'),
        pytest.param('non.existent', 404, id='non-existent-class'),
        pytest.param(
            'nomad.metainfo.data_type.Datatype', 400, id='valid-class-no-m_def'
        ),
        pytest.param(
            f'tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}',
            200,
            id='section-with-correct-tag',
        ),
        pytest.param(
            'tests.metainfo.test_metainfo.SectionWithBoth',
            200,
            id='section-without-tag',
        ),
        pytest.param(
            f'tests.metainfo.test_metainfo.SectionWithBoth@nonexistent',
            404,
            id='section-with-incorrect-tag',
        ),
        pytest.param(
            f'tests.metainfo.test_metainfo.SectionWithBoth.nonexistent',
            404,
            id='non-existent-property',
        ),
        pytest.param(
            f'tests.metainfo.test_metainfo.SectionWithBoth.quantity',
            200,
            id='property-quantity-without-tag',
        ),
        pytest.param(
            f'tests.metainfo.test_metainfo.SectionWithBoth.quantity@{SectionWithBoth.quantity.definition_id}',
            200,
            id='property-quantity-with-tag',
        ),
        pytest.param(
            f'tests.metainfo.test_metainfo.SectionWithBoth.quantity@nonexistent',
            404,
            id='property-quantity-with-wrong-tag',
        ),
        pytest.param(
            f'tests.metainfo.test_metainfo.SectionWithBoth.subsection_norepeat',
            200,
            id='property-subsection-without-tag',
        ),
        pytest.param(
            f'tests.metainfo.test_metainfo.SectionWithBoth.subsection_norepeat@{SectionWithBoth.subsection_norepeat.definition_id}',
            200,
            id='property-subsection-with-tag',
        ),
        pytest.param(
            f'tests.metainfo.test_metainfo.SectionWithBoth.subsection_norepeat@nonexistent',
            404,
            id='property-subsection-with-wrong-tag',
        ),
    ],
)
def test_json_schema_by_id(client, identifier, expected_status):
    """Test resolving schemas by identifier."""
    response = client.get(f'schemas/{identifier}')
    assert_response(response, expected_status)


def test_json_schema_by_m_def(client):
    """Test identifier as m_def."""
    identifier = 'tests.metainfo.test_metainfo.SectionWithBoth'

    response = client.get(f'schemas/{identifier}')

    assert_response(response, 200)

    schema = response.json()
    jsonschema.Draft202012Validator.check_schema(schema)

    assert response.headers['content-type'] == 'application/schema+json'

    assert schema['title'] == 'SectionWithBoth'
    assert (
        schema['$id']
        == f'{SCHEMA_ENDPOINT}/{identifier}@{SectionWithBoth.m_def.definition_id}'
    )

    assert schema['properties'] == {
        'quantity': {
            'type': 'string',
            'nomad_type': 'str',
            'description': 'Quantity for test.',
            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.quantity@{SectionWithBoth.quantity.definition_id}',
        },
        'subsection_repeat': {
            'type': 'array',
            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_repeat@{SectionWithBoth.subsection_repeat.definition_id}',
            'items': {
                '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}'
            },
            'description': 'Definition for test.',
        },
        'subsection_norepeat': {
            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}',
            'description': 'Definition for test.',
            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_norepeat@{SectionWithBoth.subsection_norepeat.definition_id}',
        },
    }

    assert schema['$defs'] == {
        'tests.metainfo.test_metainfo.Simulation': {
            'title': 'Simulation',
            'type': 'object',
            'description': 'Definition for test.',
            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}',
            'properties': {
                'program_name': {
                    'type': 'string',
                    'nomad_type': 'str',
                    'description': 'Quantity for test.',
                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation.program_name@{Simulation.program_name.definition_id}',
                }
            },
        }
    }


@pytest.mark.parametrize(
    'identifier, option, expected',
    [
        pytest.param(
            f'tests.metainfo.test_metainfo.unit_quantity',
            'unit_value',
            f'{SCHEMA_ENDPOINT}/nomad.metainfo.metainfo.Quantity@{unit_quantity.definition_id}?unit_value=true',
            id='unit_value',
        ),
        pytest.param(
            f'tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}',
            'property_subtypes',
            f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}?property_subtypes=true',
            id='property_subtypes',
        ),
        pytest.param(
            f'tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}',
            'section_subtypes',
            f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}?section_subtypes=true',
            id='section_subtypes',
        ),
    ],
)
def test_json_schema_options(client, identifier, option, expected):
    """Test options for schema output."""
    response = client.get(f'schemas/{identifier}?{option}=true')

    assert_response(response, 200)

    schema = response.json()
    jsonschema.Draft202012Validator.check_schema(schema)

    assert response.headers['content-type'] == 'application/schema+json'

    assert schema['$id'] == expected
