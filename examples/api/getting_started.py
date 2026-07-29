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
"""
Demonstrates how to use requests for a simple query and archive access.
"""

import json

import requests

base_url = 'https://nomad-lab.eu/prod/v1/api/v1'

response = requests.post(
    f'{base_url}/entries/query',
    json={
        'query': {'results.material.elements': {'all': ['Ti', 'O']}},
        'pagination': {'page_size': 1},
        'required': {'include': ['entry_id']},
    },
)
response_json = response.json()
print(json.dumps(response.json(), indent=2))


first_entry_id = response_json['data'][0]['entry_id']
response = requests.post(
    f'{base_url}/entries/{first_entry_id}/archive/query',
    json={
        'required': {
            'workflow': {
                'calculation_result_ref': {
                    'energy': '*',
                    'system_ref': {'chemical_composition': '*'},
                }
            }
        }
    },
)
response_json = response.json()
print(json.dumps(response_json, indent=2))


from nomad.datamodel import EntryArchive
from nomad.metainfo import units

archive = EntryArchive.m_from_dict(response_json['data']['archive'])
result = archive.workflow.results.calculation_result_ref
print(result.system_ref.chemical_composition)
print(result.energy.total.value.to(units('eV')))
