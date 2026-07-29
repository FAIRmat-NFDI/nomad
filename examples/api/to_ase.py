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

import requests
from ase import Atoms

base_url = 'https://nomad-lab.eu/prod/v1/api/v1'
response = requests.post(
    f'{base_url}/entries/archive/query',
    json={
        'pagination': {'page_size': 1},
        'required': {'run': {'system[-1]': {'atoms': '*'}}},
    },
)
response_json = response.json()
nomad_atoms = response_json['data'][0]['archive']['run'][0]['system'][-1]['atoms']
atoms = Atoms(
    symbols=nomad_atoms['labels'],
    positions=nomad_atoms['positions'],
    cell=nomad_atoms['lattice_vectors'],
    pbc=nomad_atoms['periodic'],
)

print(atoms)


from nomad.client import ArchiveQuery

query = ArchiveQuery(required={'run': {'system[-1]': {'atoms': '*'}}})

result = query.download(1)[0]
atoms = result.run[0].system[-1].atoms.to_ase()

print(atoms.get_chemical_formula(mode='reduce'))
