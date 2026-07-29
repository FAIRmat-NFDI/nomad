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
The scenario is that you have a lot of <name>-*.zip files for upload. This script
will add a nomad.json to the uploads, upload them 1 at a time, watch the processing, repeat.
"""

import os.path
import sys
import zipfile
from typing import Any

import requests

from nomad.client import Auth, upload_file
from nomad.config import config

nomad_url = config.client.url
user = 'youruser'
password = 'yourpassword'
main_author = None


# create an auth object
auth = Auth(user=user, password=password)


def upload(
    path: str,
    local_path: bool = False,
    metadata_path: str = None,
    publish_directly: bool = False,
    main_author: str = None,
):
    """
    Arguments:
        path: The file path to the upload file.
        local: If this file should be uploaded with &local_path
        metadata_path: Optional nomad.(yaml|json) metadata file
        publish_directly: If the upload should be published directly
    """

    assert os.path.isfile(path), f'The {path} is not a file'

    # add metadata
    if metadata_path is not None:
        assert os.path.isfile(metadata_path), f'The {metadata_path} is not a file'
        assert os.path.basename(metadata_path).endswith('.json'), (
            f'The {metadata_path} is not a nomad metadata file'
        )
        assert path.endswith('.zip'), (
            'Adding nomad metadata is only supported for .zip files'
        )

        with zipfile.ZipFile(path, 'a') as zip:
            zip.write(metadata_path, 'nomad.json')

    # upload
    print(f'uploading {path}')
    kwargs: dict[str, Any] = {}
    if publish_directly:
        kwargs['publish_directly'] = True

    if main_author is not None:
        kwargs['main_author'] = main_author

    upload_id = upload_file(path, auth, local_path=local_path)
    if upload_id is None:
        print('something went wrong')
        # try to delete the unsuccessful upload
        requests.delete(f'{nomad_url}/v1/upload/{upload_id}', auth=auth)
        return False

    return True


if __name__ == '__main__':
    metadata_path = None
    if sys.argv[1].endswith('json'):
        metadata_path = sys.argv[1]
        paths = sys.argv[2:]
    else:
        paths = sys.argv[1:]

    for path in paths:
        upload(
            path,
            metadata_path=metadata_path,
            local_path=True,
            publish_directly=True,
            main_author=main_author,
        )
