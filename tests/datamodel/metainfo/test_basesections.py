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

import pytest

from tests.normalizing.conftest import run_processing


@pytest.mark.parametrize(
    'mainfile, entry_name',
    [
        pytest.param(
            'test_basesection.archive.yaml',
            'different than file name',
            id='BaseSection-w-name',
        ),
        pytest.param(
            'test_nested_basesection.archive.yaml',
            'test nested basesection',
            id='BaseSection-nested',
        ),
    ],
)
def test_basesection(mainfile, entry_name):
    directory = 'tests/data/datamodel/metainfo'
    test_archive = run_processing(directory, mainfile)

    # Check that the composed basesection was properly normalized
    assert test_archive.data.composed_basesections[0].name is None
    assert test_archive.data.composed_basesections[1].name == 'composed basesection 2'

    # Check that top level basesection was properly normalized
    assert test_archive.data.name == entry_name
    assert test_archive.data.datetime is None

    # Check that the metadata section was properly populated
    assert test_archive.metadata.entry_name == entry_name

    # Check that the results section was properly populated
    assert test_archive.results.eln.names == [
        'composed basesection 2',
        entry_name,
    ]
    assert test_archive.results.eln.sections == [
        'BaseSection',
        'TopLevelBaseSection',
    ]
    assert test_archive.results.eln.lab_ids == [
        'composed-basesection-1',
        'top-level-basesection',
    ]
    assert test_archive.results.eln.descriptions == [
        'Description for composed basesection 1',
        'Description for composed basesection 2',
    ]
