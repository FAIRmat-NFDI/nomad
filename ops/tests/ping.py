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
import sys
import time
from datetime import datetime

import requests

base_url = 'https://nomad-lab.eu/prod/rae/api/v1'
if len(sys.argv) > 1:
    base_url = sys.argv[1]

while True:
    try:
        start = time.time()
        response = requests.get(f'{base_url}/entries', params=dict(owner='public'))
        end = time.time()
        print(f'PING – {response.status_code} – {end - start} - {datetime.now()}')
        time.sleep(5)
    except Exception as e:
        print(f'ERROR – {e}')
