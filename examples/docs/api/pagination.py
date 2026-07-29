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
import requests

base_url = 'https://nomad-lab.eu/prod/v1/api/v1'
json_body = {
    'query': {'results.material.elements': {'all': ['Ti', 'O']}},
    'pagination': {'page_size': 10},
    'required': {'include': ['results.material.chemical_formula_hill']},
}

formulas = set()

while len(formulas) < 100:
    response = requests.post(f'{base_url}/entries/query', json=json_body)
    response_json = response.json()

    for data in response_json['data']:
        formulas.add(data['results']['material']['chemical_formula_hill'])

    next_value = response_json['pagination'].get('next_page_after_value')
    if not next_value:
        break
    json_body['pagination']['page_after_value'] = next_value

print(formulas)
