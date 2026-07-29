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
This is a brief example demonstrating the public nomad@FAIRDI API for doing operations
that might be necessary to integrate external project data.
"""

import os.path
import time

import requests

from nomad.client import Auth
from nomad.config import config

nomad_url = config.client.url
user = 'youruser'
password = 'yourpassword'

upload_file = os.path.join(os.path.dirname(__file__), 'example.zip')

# create an auth object
auth = Auth(user=user, password=password)

# upload data
print('uploading  a file with "external_id/AcAg/vasp.xml" inside ...')
with open(upload_file, 'rb') as f:
    response = requests.post(
        f'{nomad_url}/v1/uploads', auth=auth, data=f, params=dict(publish_directly=True)
    )
    assert response.status_code == 200
    upload = response.json()['data']

print('processing ...')
while upload['process_running']:
    response = requests.get(f'{nomad_url}/v1/uploads/{upload["upload_id"]}', auth=auth)
    assert response.status_code == 200
    upload = response.json()['data']
    time.sleep(5)
    print(
        f'processed: {upload["processed_entries_count"]}, failures: {upload["failed_entries_count"]}'
    )

# check if processing was a success
if upload['process_status'] != 'SUCCESS':
    print('something went wrong')
    print(f'errors: {str(upload.errors)}')
    # try to delete the unsuccessful upload
    requests.delete(f'{nomad_url}/v1/uploads/{upload["upload_id"]}', auth=auth)
