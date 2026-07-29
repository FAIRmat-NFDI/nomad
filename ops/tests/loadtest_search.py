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
import os.path
import random

from locust import HttpUser, between, task

chemical_symbols = [
    # 0
    'X',
    # 1
    'H',
    'He',
    # 2
    'Li',
    'Be',
    'B',
    'C',
    'N',
    'O',
    'F',
    'Ne',
    # 3
    'Na',
    'Mg',
    'Al',
    'Si',
    'P',
    'S',
    'Cl',
    'Ar',
    # 4
    'K',
    'Ca',
    'Sc',
    'Ti',
    'V',
    'Cr',
    'Mn',
    'Fe',
    'Co',
    'Ni',
    'Cu',
    'Zn',
    'Ga',
    'Ge',
    'As',
    'Se',
    'Br',
    'Kr',
    # 5
    'Rb',
    'Sr',
    'Y',
    'Zr',
    'Nb',
    'Mo',
    'Tc',
    'Ru',
    'Rh',
    'Pd',
    'Ag',
    'Cd',
    'In',
    'Sn',
    'Sb',
    'Te',
    'I',
    'Xe',
    # 6
    'Cs',
    'Ba',
    'La',
    'Ce',
    'Pr',
    'Nd',
    'Pm',
    'Sm',
    'Eu',
    'Gd',
    'Tb',
    'Dy',
    'Ho',
    'Er',
    'Tm',
    'Yb',
    'Lu',
    'Hf',
    'Ta',
    'W',
    'Re',
    'Os',
    'Ir',
    'Pt',
    'Au',
    'Hg',
    'Tl',
    'Pb',
    'Bi',
    'Po',
    'At',
    'Rn',
    # 7
    'Fr',
    'Ra',
    'Ac',
    'Th',
    'Pa',
    'U',
    'Np',
    'Pu',
    'Am',
    'Cm',
    'Bk',
    'Cf',
    'Es',
    'Fm',
    'Md',
    'No',
    'Lr',
    'Rf',
    'Db',
    'Sg',
    'Bh',
    'Hs',
    'Mt',
    'Ds',
    'Rg',
    'Cn',
    'Nh',
    'Fl',
    'Mc',
    'Lv',
    'Ts',
    'Og',
]


# These are the API requests from the search UI with various tabs and statistics
query_params = [
    'page=1&per_page=10&order_by=upload_create_time&order=-1&domain=dft&owner=public&atoms=Co&statistics=atoms&exclude=atoms,only_atoms,dft.files,quantities,optimade,dft.labels,dft.geometries',
    'page=1&per_page=10&order_by=upload_create_time&order=-1&domain=dft&owner=public&statistics=dft.labels_springer_compound_class&statistics=dft.system&statistics=dft.crystal_system&statistics=dft.compound_type&exclude=atoms,only_atoms,dft.files,quantities,optimade,dft.labels,dft.geometries',
    'page=1&per_page=10&order_by=upload_create_time&order=-1&domain=dft&owner=public&statistics=dft.code_name&statistics=dft.basis_set&statistics=dft.xc_functional&exclude=atoms,only_atoms,dft.files,quantities,optimade,dft.labels,dft.geometries',
    'page=1&per_page=10&order_by=upload_create_time&order=-1&domain=dft&owner=public&statistics=dft.search_quantities&statistics=dft.labels_springer_classification&statistics=dft.workflow.workflow_type&exclude=atoms,only_atoms,dft.files,quantities,optimade,dft.labels,dft.geometries',
    'page=1&per_page=10&order_by=upload_create_time&order=-1&domain=dft&owner=public&statistics=dft.search_quantities&statistics=dft.labels_springer_classification&statistics=dft.workflow.workflow_type&exclude=atoms,only_atoms,dft.files,quantities,optimade,dft.labels,dft.geometries&datasets_grouped=true',
    'page=1&per_page=10&order_by=upload_create_time&order=-1&domain=dft&owner=public&metrics=dft.calculations&statistics=atoms&exclude=atoms,only_atoms,dft.files,quantities,optimade,dft.labels,dft.geometries&datasets_grouped=true',
]


class QuickstartUser(HttpUser):
    wait_time = between(1, 2)

    @task
    def empty_search(self):
        self.client.get(f'/prod/rae/beta/api/repo/?{query_params[0]}')

    def run_random_query(self):
        return self.client.get(
            f'/prod/rae/beta/api/repo/?{random.choice(query_params)}&atoms={random.choice(chemical_symbols[1:])}'
        )

    @task(3)
    def elements_search(self):
        self.run_random_query()

    @task(1)
    def rawfile_access(self):
        data = self.run_random_query().json()

        if len(data['results']) == 0:
            return

        entry = data['results'][0]
        entry_id = entry['entry_id']
        upload_id = entry['upload_id']
        mainfile = entry['mainfile']

        self.client.get(
            f'/prod/rae/beta/api/raw/calc/{upload_id}/{entry_id}/{os.path.basename(mainfile)}?length=16384&decompress=true'
        )

    @task(1)
    def archive_access(self):
        data = self.run_random_query().json()

        if len(data['results']) == 0:
            return

        entry = data['results'][0]
        entry_id = entry['entry_id']
        upload_id = entry['upload_id']

        self.client.get(f'/prod/rae/beta/api/archive/{upload_id}/{entry_id}')

    def on_start(self):
        pass
