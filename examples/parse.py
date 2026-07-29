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

from nomad.client import normalize_all, parse

# match and run the parser
archives = parse(sys.argv[1])
for archive in archives:
    # run all normalizers
    normalize_all(archive)

    # get the 'main section' section_run as a metainfo object
    section_run = archive.run[0]

    # get the same data as JSON serializable Python dict
    python_dict = section_run.m_to_dict()
