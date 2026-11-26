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

import os

import pytest

from nomad.app.v1.models.north import ReadMode, ToolModel
from nomad.files import StagingUploadFiles
from nomad.north import get_mounts
from nomad.processing import Upload


@pytest.mark.usefixtures('mongo_function', 'raw_files_function')
def test_get_mounts(user1):
    upload_id = 'test_upload_id'
    Upload.create(upload_id=upload_id, main_author=user1, upload_name='My project')
    upload_files = StagingUploadFiles(upload_id, create=True)
    tool = ToolModel(name='jupyter', image='image', mount_path='/home/jovyan')

    mounts = get_mounts(user1, tool)

    assert [mount.target for mount in mounts] == [
        '/home/jovyan/work',
        f'/home/jovyan/projects/{upload_id}',
    ]
    project_mount = mounts[1]
    assert project_mount.upload_id == upload_id
    assert project_mount.mode == ReadMode.rw
    assert project_mount.source == os.path.join(upload_files.external_os_path, 'raw')
