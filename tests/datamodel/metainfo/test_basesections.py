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

from datetime import UTC, datetime, timedelta

import pytest

from nomad.datamodel import EntryArchive, EntryMetadata
from nomad.datamodel.data import User
from nomad.datamodel.metainfo import common
from nomad.datamodel.metainfo.basesections import v1, v2
from nomad.datamodel.metainfo.common import default_datetime
from nomad.datamodel.metainfo.eln import ELNProcess, ELNSample, ELNSubstance, SampleID
from nomad.utils import get_logger
from tests.normalizing.conftest import run_normalize, run_processing

ENTRY_CREATE_TIME = datetime(2026, 10, 2, 13, 27, tzinfo=UTC)


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


@pytest.fixture
def clock(monkeypatch):
    """Mocks the current time used by `default_datetime`. Set `clock['value']`."""
    clock = {'value': ENTRY_CREATE_TIME}
    monkeypatch.setattr(common, 'now', lambda: clock['value'])
    return clock


def create_archive(entry_create_time=ENTRY_CREATE_TIME, data=None) -> EntryArchive:
    """Creates the archive that processing an entry with the given raw `data` starts with."""
    archive = EntryArchive(
        metadata=EntryMetadata(
            mainfile='P3HT_powder.archive.json',
            entry_create_time=entry_create_time,
        ),
        data=data,
    )
    archive.metadata.main_author = User.m_from_dict(
        {
            'first_name': 'Fabian',
            'last_name': 'Trier',
            'affiliation': 'Humboldt-Universität zu Berlin',
        }
    )
    return archive


@pytest.mark.parametrize(
    'archive, expected',
    [
        pytest.param(
            EntryArchive(metadata=EntryMetadata(entry_create_time=ENTRY_CREATE_TIME)),
            ENTRY_CREATE_TIME,
            id='entry-create-time',
        ),
        pytest.param(
            EntryArchive(metadata=EntryMetadata()),
            ENTRY_CREATE_TIME + timedelta(days=3),
            id='no-entry-create-time',
        ),
        pytest.param(
            EntryArchive(), ENTRY_CREATE_TIME + timedelta(days=3), id='no-metadata'
        ),
        pytest.param(None, ENTRY_CREATE_TIME + timedelta(days=3), id='no-archive'),
    ],
)
def test_default_datetime(clock, archive, expected):
    clock['value'] = ENTRY_CREATE_TIME + timedelta(days=3)
    assert default_datetime(archive) == expected


@pytest.mark.parametrize(
    'section_cls, identifiers',
    [
        pytest.param(ELNSubstance, 'substance_identifiers', id='ELNSubstance'),
        pytest.param(ELNSample, 'sample_identifiers', id='ELNSample'),
        pytest.param(ELNProcess, 'process_identifiers', id='ELNProcess'),
    ],
)
def test_eln_datetime_and_lab_id_stable_across_reprocessing(
    clock, section_cls, identifiers
):
    # The raw file only holds what the user entered. GUI v2 only writes the changed
    # fields, so the values filled in by the normalizers are never stored.
    raw_data = {'name': 'P3HT powder', identifiers: {}}

    def process():
        archive = create_archive(data=section_cls.m_from_dict(raw_data))
        return run_normalize(archive)

    first = process()
    clock['value'] = ENTRY_CREATE_TIME + timedelta(days=3)
    second = process()

    expected_lab_id = 'HUB_FaTr_20261002_P3HT-powder'
    for archive in (first, second):
        assert archive.data.datetime == ENTRY_CREATE_TIME
        assert archive.data.m_get(identifiers).datetime == ENTRY_CREATE_TIME
        assert archive.data.m_get(identifiers).lab_id == expected_lab_id
        assert archive.data.lab_id == expected_lab_id
        assert archive.results.eln.lab_ids == [expected_lab_id]


@pytest.mark.parametrize(
    'identifiers_cls, datetime_name, lab_id_name',
    [
        pytest.param(v1.ReadableIdentifiers, 'datetime', 'lab_id', id='v1'),
        pytest.param(v2.ReadableIdentifiers, 'datetime', 'lab_id', id='v2'),
        pytest.param(SampleID, 'creation_datetime', 'sample_id', id='SampleID'),
    ],
)
def test_identifiers_datetime_and_lab_id_stable_across_reprocessing(
    clock, identifiers_cls, datetime_name, lab_id_name
):
    def process():
        identifiers = identifiers_cls()
        identifiers.normalize(create_archive(), get_logger(__name__))
        return identifiers

    first = process()
    clock['value'] = ENTRY_CREATE_TIME + timedelta(days=3)
    second = process()

    for identifiers in (first, second):
        assert identifiers.m_get(datetime_name) == ENTRY_CREATE_TIME
        assert identifiers.m_get(lab_id_name) == 'HUB_FaTr_20261002_P3HT-powder'
