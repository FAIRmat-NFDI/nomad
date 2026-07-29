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
import random

from ase.data import chemical_symbols
from locust import HttpUser, task


class HelloWorldUser(HttpUser):
    @task
    def prod(self):
        self.client.get('http://cloud.nomad-lab.eu/prod/v1/alive')

    @task
    def homepage(self):
        self.client.get('http://cloud.nomad-lab.eu/nomad-lab/index.html')

    @task
    def search(self):
        url = 'http://cloud.nomad-lab.eu/prod/v1/api/v1/entries?owner=public&include=entry_id'
        element = random.choice(chemical_symbols)
        url += f'&q=results.material.elements__{element}'
        self.client.request_name = (
            '/prod/v1/api/v1/entries?q=results.material.elements__X'
        )
        self.client.get(url)

    @task
    def search_sort(self):
        url = 'http://cloud.nomad-lab.eu/prod/v1/api/v1/entries?owner=public&include=entry_id&order_by=upload_create_time&order=desc'
        element = random.choice(chemical_symbols)
        url += f'&q=results.material.elements__{element}'
        self.client.request_name = '/prod/v1/api/v1/entries?q=results.material.elements__X&order_by=upload_create_time'
        self.client.get(url)

    @task
    def file(self):
        url = 'https://cloud.nomad-lab.eu/prod/v1/api/v1/uploads/Hy3Otg19QAesKS_Muv9GLw/raw/archive/S-edge/RIs_0.375ML/S-edge-CO%2BOH%2B3H.vasprun.xml?length=16384&decompress=true&ignore_mime_type=true'
        self.client.request_name = '/prod/v1/api/v1/uploads/<id>/raw/archive/<path>'
        self.client.get(url)

    @task
    def archive(self):
        url = 'https://cloud.nomad-lab.eu/prod/v1/api/v1/entries/xQiOo1umiJlIe_ZlTzoVnL-yFt3F/archive'
        self.client.request_name = '/prod/v1/api/v1/entries/<id>/archive'
        self.client.get(url)
