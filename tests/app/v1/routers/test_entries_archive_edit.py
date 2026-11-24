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

import json
from datetime import UTC, datetime

import pytest
import yaml
from fastapi import HTTPException

from nomad import utils
from nomad.app.v1.routers.entries import (
    ArchiveChange,
    ArchiveChangeAction,
    _apply_archive_change_to_dict,
    _resolve_archive_change_target_in_dict,
    _section_def_from_dict,
)
from nomad.datamodel.data import EntryData
from nomad.datamodel.datamodel import EntryArchive, EntryMetadata
from nomad.datamodel.metainfo.basesections import BaseSection
from nomad.datamodel.metainfo.eln import ELNSample
from nomad.files import UploadFiles
from nomad.metainfo import Quantity, Section, SubSection
from nomad.processing.base import ProcessStatus
from nomad.processing.data import Upload
from nomad.utils.exampledata import ExampleData
from tests.test_files import create_test_upload_files


class TypedComponent(BaseSection):
    m_def = Section()

    mass = Quantity(type=float)


class EditSubSection(BaseSection):
    m_def = Section()


class SingleSubEntryData(BaseSection, EntryData):
    m_def = Section()

    sub = SubSection(sub_section=EditSubSection)


class RepeatedSubEntryData(BaseSection, EntryData):
    m_def = Section()

    sub = SubSection(sub_section=EditSubSection, repeats=True)


class RepeatedQuantityEntryData(BaseSection, EntryData):
    m_def = Section()

    tags = Quantity(type=str, shape=['*'])


class TypedEntryData(EntryData):
    m_def = Section()

    components = SubSection(sub_section=TypedComponent, repeats=True)


def assert_edit_reprocessed_successfully(upload_id: str, entry_id: str, mainfile: str):
    entry = Upload.get(upload_id).get_entry(entry_id)
    assert entry is not None
    assert entry.process_status == ProcessStatus.SUCCESS, (
        f'Processing failed: {entry.errors}'
    )
    assert entry.mainfile == mainfile
    assert entry.errors in (None, [])


@pytest.mark.parametrize(
    'edit, result, user, data_cls',
    [
        pytest.param(
            {'changes': [{'path': 'data/name', 'new_value': 'NewName'}]},
            {'data': {'name': 'NewName'}},
            'user1',
            SingleSubEntryData,
            id='quantity',
        ),
        pytest.param(
            {'changes': [{'path': 'data/sub', 'new_value': {'name': 'NewName'}}]},
            {'data': {'name': 'TestName', 'sub': {'name': 'NewName'}}},
            'user1',
            SingleSubEntryData,
            id='sub-section',
        ),
        pytest.param(
            {'changes': [{'path': 'data/sub/0', 'new_value': {'name': 'NewName'}}]},
            {'data': {'name': 'TestName', 'sub': [{'name': 'NewName'}]}},
            'user1',
            RepeatedSubEntryData,
            id='repeated-sub-section',
        ),
        pytest.param(
            {'changes': [{'path': 'data/sub/name', 'new_value': 'NewName'}]},
            {'data': {'name': 'TestName', 'sub': {'name': 'NewName'}}},
            'user1',
            SingleSubEntryData,
            id='missing-sub-section',
        ),
        pytest.param(
            {'changes': [{'path': 'data/sub/0/name', 'new_value': 'NewName'}]},
            {'data': {'name': 'TestName', 'sub': [{'name': 'NewName'}]}},
            'user1',
            RepeatedSubEntryData,
            id='missing-repeated-sub-section',
        ),
        pytest.param(
            {'changes': [{'path': 'data/name', 'action': 'remove'}]},
            {'data': {}},
            'user1',
            SingleSubEntryData,
            id='remove-quantity',
        ),
        pytest.param(
            {
                'changes': [
                    {'path': 'data/sub/name', 'new_value': 'NewName'},
                    {'path': 'data/sub', 'action': 'remove'},
                ]
            },
            {
                'data': {
                    'name': 'TestName',
                }
            },
            'user1',
            SingleSubEntryData,
            id='remove-sub-section',
        ),
        pytest.param(
            {
                'changes': [
                    {'path': 'data/sub/1/name', 'new_value': 'NewName'},
                    {'path': 'data/sub/1', 'action': 'remove'},
                ]
            },
            {'data': {'name': 'TestName', 'sub': []}},
            'user1',
            RepeatedSubEntryData,
            id='remove-repeated-sub-section',
        ),
        pytest.param(
            {
                'changes': [
                    {'path': 'data/sub/0', 'action': 'upsert', 'new_value': {}},
                    {
                        'path': 'data/sub/0/name',
                        'action': 'upsert',
                        'new_value': 'NewName1',
                    },
                    {'path': 'data/sub/1', 'action': 'upsert', 'new_value': {}},
                    {
                        'path': 'data/sub/1/name',
                        'action': 'upsert',
                        'new_value': 'NewName2',
                    },
                ]
            },
            {
                'data': {
                    'name': 'TestName',
                    'sub': [{'name': 'NewName1'}, {'name': 'NewName2'}],
                }
            },
            'user1',
            RepeatedSubEntryData,
            id='add-multiple-repeated-sub-section',
        ),
    ],
)
def test_post_entry_edit(
    edit,
    result,
    user,
    data_cls,
    client,
    auth_headers,
    users_dict,
    elastic_function,
    mongo_function,
    raw_files_function,
):
    mainfile = 'mainfile.archive.json'
    entry_id = utils.generate_entry_id('upload_id', mainfile)
    data = ExampleData(main_author=users_dict[user])
    data.create_upload(upload_id='upload_id', published=False)
    data.create_entry(entry_id=entry_id, upload_id='upload_id', mainfile=mainfile)
    data.save(with_files=False)

    upload_files = create_test_upload_files('upload_id', published=False, archives=[])
    with upload_files.raw_file(mainfile, 'wt') as f:
        json.dump(
            EntryArchive(
                metadata=EntryMetadata(
                    entry_id=entry_id,
                    mainfile=mainfile,
                ),
                data=data_cls(name='TestName'),
            ).m_to_dict(),
            f,
        )

    user_auth = auth_headers[user]
    url = f'entries/{entry_id}/edit'
    response = client.post(url, headers=user_auth, json=edit)

    assert response.status_code == 200, response.text
    archive_data = None
    with upload_files.raw_file(mainfile, 'rt') as f:
        archive_data = json.load(f)

    assert json.dumps(
        {key: value for key, value in archive_data['data'].items() if key != 'm_def'}
    ) == json.dumps(result['data'])

    assert_edit_reprocessed_successfully('upload_id', entry_id, mainfile)


def test_post_entry_edit_creates_typed_repeated_sub_sections(
    client,
    auth_headers,
    users_dict,
    elastic_function,
    mongo_function,
    raw_files_function,
):
    mainfile = 'typed_mainfile.archive.json'
    entry_id = utils.generate_entry_id('typed_upload_id', mainfile)
    data = ExampleData(main_author=users_dict['user1'])
    data.create_upload(upload_id='typed_upload_id', published=False)
    data.create_entry(entry_id=entry_id, upload_id='typed_upload_id', mainfile=mainfile)
    data.save(with_files=False)

    upload_files = create_test_upload_files(
        'typed_upload_id', published=False, archives=[]
    )
    with upload_files.raw_file(mainfile, 'wt') as f:
        json.dump(
            EntryArchive(
                metadata=EntryMetadata(
                    entry_id=entry_id,
                    mainfile=mainfile,
                ),
                data=TypedEntryData(name='TypedName'),
            ).m_to_dict(),
            f,
        )

    response = client.post(
        f'entries/{entry_id}/edit',
        headers=auth_headers['user1'],
        json={
            'changes': [
                {
                    'path': 'data/components/0/mass',
                    'new_value': 3e-6,
                }
            ]
        },
    )

    assert response.status_code == 200, response.text

    with upload_files.raw_file(mainfile, 'rt') as f:
        archive_data = json.load(f)

    assert archive_data['data']['m_def'] == TypedEntryData.m_def.qualified_name()
    assert archive_data['data']['components'] == [{'mass': 3e-6}]

    assert_edit_reprocessed_successfully('typed_upload_id', entry_id, mainfile)


def test_post_entry_edit_preserves_eln_sample_components(
    client,
    auth_headers,
    users_dict,
    elastic_function,
    mongo_function,
    raw_files_function,
):
    mainfile = 'eln_sample.archive.json'
    entry_id = utils.generate_entry_id('eln_upload_id', mainfile)
    data = ExampleData(main_author=users_dict['user1'])
    data.create_upload(upload_id='eln_upload_id', published=False)
    data.create_entry(entry_id=entry_id, upload_id='eln_upload_id', mainfile=mainfile)
    data.save(with_files=False)

    upload_files = create_test_upload_files(
        'eln_upload_id', published=False, archives=[]
    )
    with upload_files.raw_file(mainfile, 'wt') as f:
        json.dump(
            EntryArchive(
                metadata=EntryMetadata(
                    entry_id=entry_id,
                    mainfile=mainfile,
                ),
                data=ELNSample(name='Sample'),
            ).m_to_dict(),
            f,
        )

    response = client.post(
        f'entries/{entry_id}/edit',
        headers=auth_headers['user1'],
        json={
            'changes': [
                {
                    'path': 'data/components/0',
                    'action': 'upsert',
                    'new_value': {},
                },
                {
                    'path': 'data/components/0/name',
                    'action': 'upsert',
                    'new_value': 'fee',
                },
                {
                    'path': 'data/components/0/mass',
                    'action': 'upsert',
                    'new_value': 3e-6,
                },
            ]
        },
    )

    assert response.status_code == 200, response.text

    with upload_files.raw_file(mainfile, 'rt') as f:
        archive_data = json.load(f)

    assert archive_data['data']['m_def'] == ELNSample.m_def.qualified_name()
    assert archive_data['data']['name'] == 'Sample'
    assert archive_data['data']['components'] == [{'name': 'fee', 'mass': 3e-6}]

    assert_edit_reprocessed_successfully('eln_upload_id', entry_id, mainfile)


def test_post_entry_edit_resolves_same_upload_yaml_schema(
    client,
    auth_headers,
    users_dict,
    elastic_function,
    mongo_function,
    raw_files_function,
):
    upload_id = 'custom_schema_edit_upload'
    schema_mainfile = 'schema.archive.yaml'
    data_mainfile = 'data.archive.json'
    entry_id = utils.generate_entry_id(upload_id, data_mainfile)

    example_data = ExampleData(main_author=users_dict['user1'])
    example_data.create_upload(upload_id=upload_id, published=False)
    metadata = EntryMetadata(
        entry_id=entry_id,
        upload_id=upload_id,
        mainfile=data_mainfile,
        entry_hash=f'dummy_hash_{entry_id}',
        domain='dft',
        entry_create_time=datetime.now(UTC),
        processed=True,
        parser_name='parsers/archive',
        main_author=users_dict['user1'],
    )
    example_data.entries[entry_id] = metadata
    example_data.archives[entry_id] = EntryArchive(metadata=metadata)
    example_data.upload_entries[upload_id] = [entry_id]
    example_data.save(with_files=False)

    upload_files = create_test_upload_files(upload_id, published=False, archives=[])
    with upload_files.raw_file(schema_mainfile, 'wt') as f:
        yaml.safe_dump(
            {
                'definitions': {
                    'name': 'Edit test schema',
                    'sections': {
                        'CustomEntry': {
                            'base_sections': ['nomad.datamodel.data.EntryData'],
                            'quantities': {'custom_value': {'type': 'str'}},
                        }
                    },
                }
            },
            f,
        )
    with upload_files.raw_file(data_mainfile, 'wt') as f:
        json.dump(
            {
                'data': {
                    'm_def': (
                        f'../upload/raw/{schema_mainfile}'
                        '#/definitions/section_definitions/0'
                    ),
                    'custom_value': 'before',
                }
            },
            f,
        )

    response = client.post(
        f'entries/{entry_id}/edit',
        headers=auth_headers['user1'],
        json={'changes': [{'path': 'data/custom_value', 'new_value': 'after'}]},
    )

    assert response.status_code == 200, response.text
    with upload_files.raw_file(data_mainfile, 'rt') as f:
        archive_data = json.load(f)
    assert archive_data['data']['custom_value'] == 'after'
    assert archive_data['data']['m_def'] == (
        f'../upload/raw/{schema_mainfile}#/definitions/section_definitions/0'
    )
    assert_edit_reprocessed_successfully(upload_id, entry_id, data_mainfile)


def create_yaml_archive_entry(
    users_dict, upload_id: str, mainfile: str, content: dict
) -> tuple[str, UploadFiles]:
    """Registers an archive entry and writes *content* as its YAML mainfile."""
    entry_id = utils.generate_entry_id(upload_id, mainfile)
    example_data = ExampleData(main_author=users_dict['user1'])
    example_data.create_upload(upload_id=upload_id, published=False)
    example_data.create_entry(
        entry_id=entry_id,
        upload_id=upload_id,
        mainfile=mainfile,
        parser_name='parsers/archive',
    )
    example_data.save(with_files=False)

    upload_files = create_test_upload_files(upload_id, published=False, archives=[])
    with upload_files.raw_file(mainfile, 'wt') as f:
        yaml.safe_dump(content, f, sort_keys=False)

    return entry_id, upload_files


def test_post_entry_edit_resolves_inline_yaml_schema(
    client,
    auth_headers,
    users_dict,
    elastic_function,
    mongo_function,
    raw_files_function,
):
    # The data references a section defined in the same file by its bare name.
    upload_id = 'inline_schema_edit_upload'
    mainfile = 'inline_schema.archive.yaml'
    entry_id, upload_files = create_yaml_archive_entry(
        users_dict,
        upload_id,
        mainfile,
        {
            'definitions': {
                'sections': {
                    'InlineEntry': {
                        'base_sections': ['nomad.datamodel.data.EntryData'],
                        'quantities': {'custom_value': {'type': 'str'}},
                        'sub_sections': {
                            'points': {
                                'repeats': True,
                                'section': {'quantities': {'x': {'type': 'float'}}},
                            }
                        },
                    }
                },
            },
            'data': {
                'm_def': 'InlineEntry',
                'custom_value': 'before',
                'points': [{'x': 1.0}],
            },
        },
    )

    response = client.post(
        f'entries/{entry_id}/edit',
        headers=auth_headers['user1'],
        json={
            'changes': [
                {'path': 'data/custom_value', 'new_value': 'after'},
                {'path': 'data/points/0/x', 'new_value': 2.0},
            ]
        },
    )

    assert response.status_code == 200, response.text
    with upload_files.raw_file(mainfile, 'rt') as f:
        archive_data = yaml.safe_load(f)
    assert archive_data['data'] == {
        'm_def': 'InlineEntry',
        'custom_value': 'after',
        'points': [{'x': 2.0}],
    }
    assert_edit_reprocessed_successfully(upload_id, entry_id, mainfile)


def test_section_def_from_dict():
    # 1. Fallback (no m_def)
    assert _section_def_from_dict({}, EntryData.m_def) == EntryData.m_def

    # 2. Resolution (valid m_def)
    sec = _section_def_from_dict(
        {'m_def': 'nomad.datamodel.datamodel.EntryArchive'}, EntryData.m_def
    )
    assert sec == EntryArchive.m_def

    # 3. Error (invalid m_def)
    with pytest.raises(HTTPException) as exc:
        _section_def_from_dict({'m_def': 'invalid.package.NotExists'}, EntryData.m_def)
    assert exc.value.status_code == 400


def test_resolve_archive_change_target_in_dict():
    m_def_name = RepeatedSubEntryData.m_def.qualified_name()
    archive_data = {
        'data': {
            'm_def': m_def_name,
            'name': 'TestName',
            'sub': [{'name': 'R1'}, {'name': 'R2'}],
        }
    }

    # 1. Basic resolution (Singular)
    parent, prop_def, idx = _resolve_archive_change_target_in_dict(
        archive_data, 'data/name', create_missing=False
    )
    assert parent is archive_data['data']
    assert prop_def.name == 'name'
    assert idx is None

    # 2. Basic resolution (Repeating)
    parent, prop_def, idx = _resolve_archive_change_target_in_dict(
        archive_data, 'data/sub/1', create_missing=False
    )
    assert parent is archive_data['data']
    assert prop_def.name == 'sub'
    assert idx == 1

    # 3. Missing Intermediate (create_missing=True)
    empty_archive = {'data': {'m_def': m_def_name}}
    parent, prop_def, idx = _resolve_archive_change_target_in_dict(
        empty_archive, 'data/sub/0/name', create_missing=True
    )
    assert empty_archive['data']['sub'][0] == {}
    assert parent is empty_archive['data']['sub'][0]
    assert prop_def.name == 'name'
    assert idx is None

    # 4. Missing Intermediate (create_missing=False)
    with pytest.raises(HTTPException) as exc:
        _resolve_archive_change_target_in_dict(
            {'data': {'m_def': m_def_name}}, 'data/sub/0/name', create_missing=False
        )
    assert exc.value.status_code == 400

    # 5. Invalid Path format
    for bad_path in ['data//name', 'data/sub/', '/data']:
        with pytest.raises(HTTPException) as exc:
            _resolve_archive_change_target_in_dict(
                archive_data, bad_path, create_missing=False
            )
        assert exc.value.status_code == 400

    # 6. Index out of bounds
    with pytest.raises(HTTPException) as exc:
        _resolve_archive_change_target_in_dict(
            archive_data, 'data/sub/5', create_missing=False
        )
    assert exc.value.status_code == 400

    # 7. Missing Index for repeating property
    with pytest.raises(HTTPException) as exc:
        _resolve_archive_change_target_in_dict(
            archive_data, 'data/sub/name', create_missing=False
        )
    assert exc.value.status_code == 400

    # 8. Invalid property
    with pytest.raises(HTTPException) as exc:
        _resolve_archive_change_target_in_dict(
            archive_data, 'data/unknown', create_missing=False
        )
    assert exc.value.status_code == 400


def test_apply_archive_change_to_dict():
    m_def_single = SingleSubEntryData.m_def.qualified_name()
    m_def_repeated = RepeatedSubEntryData.m_def.qualified_name()
    m_def_repeated_quantity = RepeatedQuantityEntryData.m_def.qualified_name()

    # 1. Upsert Quantity
    d1 = {'data': {'m_def': m_def_single, 'name': 'Old'}}
    _apply_archive_change_to_dict(
        d1,
        ArchiveChange(
            path='data/name', action=ArchiveChangeAction.upsert, new_value='New'
        ),
    )
    assert d1['data']['name'] == 'New'

    # 2. Upsert Sub-Section
    d2 = {'data': {'m_def': m_def_single}}
    _apply_archive_change_to_dict(
        d2,
        ArchiveChange(
            path='data/sub',
            action=ArchiveChangeAction.upsert,
            new_value={'name': 'Sub'},
        ),
    )
    assert d2['data']['sub'] == {'name': 'Sub'}

    # 3. Upsert Repeating (Append)
    d3 = {'data': {'m_def': m_def_repeated, 'sub': [{'name': 'R1'}]}}
    _apply_archive_change_to_dict(
        d3,
        ArchiveChange(
            path='data/sub/1',
            action=ArchiveChangeAction.upsert,
            new_value={'name': 'R2'},
        ),
    )
    assert len(d3['data']['sub']) == 2
    assert d3['data']['sub'][1] == {'name': 'R2'}

    # 4. Upsert Repeating (Modify)
    d4 = {'data': {'m_def': m_def_repeated, 'sub': [{'name': 'R1'}]}}
    _apply_archive_change_to_dict(
        d4,
        ArchiveChange(
            path='data/sub/0/name', action=ArchiveChangeAction.upsert, new_value='Mod'
        ),
    )
    assert d4['data']['sub'][0]['name'] == 'Mod'

    # 5. Remove Quantity
    d5 = {'data': {'m_def': m_def_single, 'name': 'RemoveMe'}}
    _apply_archive_change_to_dict(
        d5, ArchiveChange(path='data/name', action=ArchiveChangeAction.remove)
    )
    assert 'name' not in d5['data']

    # 6. Remove Repeating (Pop last)
    d6 = {'data': {'m_def': m_def_repeated, 'sub': [{'name': 'R1'}, {'name': 'R2'}]}}
    _apply_archive_change_to_dict(
        d6, ArchiveChange(path='data/sub/1', action=ArchiveChangeAction.remove)
    )
    assert len(d6['data']['sub']) == 1
    assert d6['data']['sub'][0]['name'] == 'R1'

    # 7. Remove Repeating (Pop intermediate)
    d7 = {
        'data': {
            'm_def': m_def_repeated,
            'sub': [{'name': 'R1'}, {'name': 'R2'}, {'name': 'R3'}],
        }
    }
    _apply_archive_change_to_dict(
        d7, ArchiveChange(path='data/sub/1', action=ArchiveChangeAction.remove)
    )
    assert d7['data']['sub'] == [{'name': 'R1'}, {'name': 'R3'}]

    # 8. Remove Repeating (Pop last, clears trailing None)
    d8 = {
        'data': {'m_def': m_def_repeated, 'sub': [{'name': 'R1'}, None, {'name': 'R3'}]}
    }
    _apply_archive_change_to_dict(
        d8, ArchiveChange(path='data/sub/2', action=ArchiveChangeAction.remove)
    )
    assert len(d8['data']['sub']) == 1

    # 9. Remove Repeated Quantity (Pop intermediate)
    d9 = {'data': {'m_def': m_def_repeated_quantity, 'tags': ['a', 'b', 'c']}}
    _apply_archive_change_to_dict(
        d9, ArchiveChange(path='data/tags/1', action=ArchiveChangeAction.remove)
    )
    assert d9['data']['tags'] == ['a', 'c']
