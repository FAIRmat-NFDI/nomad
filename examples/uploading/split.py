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
# Simple script that distributes the contents of a tar file over even sized 32GB zip file.
# It will not split directories to preserve the mainfile/auxiliary file structure.

import os.path
import tarfile
import zipfile

file = './file-to-split.tar.gz'
name = 'zip-file-name'

tar_file = tarfile.open(file, 'r:gz')

size_per_zip = 32 * 1024 * 1024 * 1024

current_zip = None
current_zip_number = 0
current_dir = None
current_size = 0

while True:
    info = tar_file.next()

    if not info.isfile():
        continue

    this_dir = os.path.dirname(info.name)
    current_size += info.size

    if current_size > size_per_zip and this_dir != current_dir:
        # split
        current_zip.close()
        current_zip = None
        current_zip_number += 1
        current_size = info.size

    if current_zip is None:
        zip_file_name = f'{name}-{current_zip_number}.zip'
        current_zip = zipfile.ZipFile(
            zip_file_name, mode='w', compression=0, allowZip64=True
        )

    reader = tar_file.extractfile(info)
    with current_zip.open(info.name, mode='w') as f:
        f.write(reader.read())

    current_dir = this_dir

tar_file.close()
