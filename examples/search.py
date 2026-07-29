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
from nomad import infrastructure
from nomad.app.v1.models import MetadataPagination
from nomad.config import config
from nomad.search import search

config.elastic.host = 'localhost'
config.elastic.port = 19202
config.elastic.entries_index = 'fairdi_nomad_prod_v0_8'

infrastructure.setup_elastic()

for entry in search(
    pagination=MetadataPagination(page_size=1000), query=dict(authors='Emre Ahmetcik')
).data:
    print('entry_id', entry['entry_id'])
