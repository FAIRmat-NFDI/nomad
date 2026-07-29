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
This is a brief example on how use requests with authentication to talks to the NOMAD API.
"""

import requests

from nomad.client import Auth
from nomad.config import config

nomad_url = config.client.url
user = 'yourusername'
password = 'yourpassword'

# create an auth object
auth = Auth(user=user, password=password)

# simple search request to print number of user entries
response = requests.get(f'{nomad_url}/v1/entries', params=dict(owner='user'), auth=auth)
print(response.json()['data'])
