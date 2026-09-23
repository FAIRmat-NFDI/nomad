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

import asyncio
import json
import zipfile
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
import yaml

from nomad import utils
from nomad.datamodel import EntryArchive, ServerContext
from nomad.datamodel.metainfo.simulation import run
from nomad.files import PublicUploadFiles, StagingUploadFiles
from nomad.graph.graph_reader import (
    ArchiveReader,
    EntryReader,
    FileSystemReader,
    GeneralReader,
    GraphNode,
    MongoReader,
    Token,
    UploadReader,
    UserReader,
)
from nomad.graph.lazy_wrapper import LazyWrapper
from nomad.utils.exampledata import ExampleData
from tests.normalizing.conftest import run_processing, simulationworkflowschema
from tests.utils import ListWithSortKey


def rprint(msg):
    print(msg)
    # try:
    #     import rich
    #     rich.print(msg)
    # except ImportError:
    #     print(msg)


def assert_time(i, j):
    try:
        datetime.fromisoformat(i)
        datetime.fromisoformat(j)
    except Exception:
        assert i == j


def assert_list(observed, expected):
    assert len(observed) == len(expected)
    if isinstance(expected, ListWithSortKey):
        observed = sorted(observed, key=expected.sort_key)
        expected = sorted(expected, key=expected.sort_key)
    for i, j in zip(observed, expected):
        if isinstance(i, LazyWrapper):
            i = i.to_json()
        if isinstance(i, dict):
            assert_dict(i, j)
        elif isinstance(i, list):
            assert_list(i, j)
        else:
            assert_time(i, j)


def assert_dict(observed, expected):
    observed.pop(GeneralReader.__CACHE__, None)
    observed.pop('m_response', None)
    observed.pop('m_def', None)
    observed.pop('m_def_id', None)
    # we do not check the definition ID in this file
    # it has been systematically tested in the other test file
    observed.pop('definition_id', None)
    expected.pop('m_def', None)
    expected.pop('m_def_id', None)
    expected.pop('definition_id', None)
    assert set(observed.keys()) == set(expected.keys())
    for k, v in observed.items():
        if k == 'categories':
            continue
        if k == 'upload_files_server_path':
            continue
        if isinstance(v, LazyWrapper):
            v = v.to_json()
        if isinstance(v, dict):
            assert_dict(v, expected[k])
        elif isinstance(v, list):
            assert_list(v, expected[k])
        else:
            assert_time(v, expected[k])


user1_dict = {
    'name': 'Sheldon Cooper',
    'first_name': 'Sheldon',
    'last_name': 'Cooper',
    'email': 'sheldon.cooper@nomad-coe.eu',
    'user_id': '00000000-0000-0000-0000-000000000001',
    'username': 'scooper',
    'is_admin': False,
    'is_oasis_admin': True,
}

user2_dict = {
    'name': 'Leonard Hofstadter',
    'first_name': 'Leonard',
    'last_name': 'Hofstadter',
    'email': 'leonard.hofstadter@nomad-fairdi.tests.de',
    'user_id': '00000000-0000-0000-0000-000000000002',
    'username': 'lhofstadter',
    'is_admin': False,
}

user3_dict = {
    'name': 'Howard Wolowitz',
    'first_name': 'Howard',
    'last_name': 'Wolowitz',
    'email': 'howard.wolowitz@nomad-fairdi.tests.de',
    'user_id': '00000000-0000-0000-0000-000000000003',
    'username': 'hwolowitz',
    'is_admin': False,
}


def test_rewrite_mainfile_archive_reference_with_dotted_filename():
    from nomad.graph.graph_reader import _convert_ref_to_path_string

    upload_id = 'current-upload'
    mainfile = 'mp-999540/vasprun.xml.gz'
    entry_id = utils.generate_entry_id(upload_id, mainfile)

    assert (
        _convert_ref_to_path_string(
            f'../upload/archive/mainfile/{mainfile}#/workflow2', upload_id
        )
        == f'uploads/{upload_id}/entries/{entry_id}/archive/workflow2'
    )


def increment():
    n = 0
    while True:
        n += 1
        yield n


@pytest.mark.parametrize(
    ('span', 'expected_measurements'),
    [
        (None, 0),
        (SimpleNamespace(is_recording=lambda: False), 0),
        (SimpleNamespace(is_recording=lambda: True, set_attribute=lambda *_: None), 1),
    ],
    ids=['tracing-disabled', 'unsampled', 'sampled'],
)
def test_load_archive_measures_reads_only_for_recording_spans(
    monkeypatch, span, expected_measurements
):
    measurements = []

    @contextmanager
    def fake_trace_span(*args, **kwargs):
        yield span

    @contextmanager
    def fake_measure_fs_reads():
        measurements.append(True)
        yield SimpleNamespace(read_bytes=10, read_time=0.001)

    class UploadFiles:
        @contextmanager
        def read_archive(self, entry_id):
            yield {entry_id: {'value': 1}}

    monkeypatch.setattr('nomad.graph.graph_reader.trace_span', fake_trace_span)
    monkeypatch.setattr(
        'nomad.graph.graph_reader.measure_fs_reads', fake_measure_fs_reads
    )

    reader = ArchiveReader({})
    reader.upload_pool['upload-id'] = UploadFiles()

    with reader.load_archive('upload-id', 'entry-id') as archive:
        assert archive == {'value': 1}

    assert len(measurements) == expected_measurements


@pytest.mark.asyncio
async def test_definition_resolution_is_cached_per_graph_request(monkeypatch):
    upload_calls = []
    fetch_calls = []
    definitions = {'first-id': object(), 'second-id': object()}

    def get_upload(upload_id, user, include_others):
        upload_calls.append((upload_id, user, include_others))
        return object()

    def fetch_section(context, m_def, m_def_id):
        fetch_calls.append((m_def, m_def_id))
        return definitions[m_def_id]

    monkeypatch.setattr(
        'nomad.graph.graph_reader.get_upload_with_read_access', get_upload
    )
    monkeypatch.setattr(ServerContext, 'fetch_section', fetch_section)

    with ArchiveReader({}) as reader:
        node = GraphNode(
            upload_id='upload-id',
            entry_id='entry-id',
            current_path=[],
            result_root={},
            ref_result_root={},
            archive={},
            archive_root={},
            definition=None,
            visited_path=set(),
            current_depth=0,
            reader=reader,
        )

        assert (
            await reader._retrieve_definition('First', 'first-id', node)
            is definitions['first-id']
        )
        assert (
            await reader._retrieve_definition('First', 'first-id', node)
            is definitions['first-id']
        )
        assert (
            await reader._retrieve_definition('Second', 'second-id', node)
            is definitions['second-id']
        )

        with ArchiveReader({}, reader_cache=reader._reader_cache) as child_reader:
            assert (
                await child_reader._retrieve_definition('First', 'first-id', node)
                is definitions['first-id']
            )

    assert upload_calls == [('upload-id', None, True)]
    assert fetch_calls == [('First', 'first-id'), ('Second', 'second-id')]


def test_duplicate_graph_errors_are_not_logged_repeatedly(monkeypatch):
    from types import SimpleNamespace

    debug_messages = []
    monkeypatch.setattr(
        'nomad.graph.graph_reader.logger',
        SimpleNamespace(debug=debug_messages.append),
    )

    with ArchiveReader({}) as reader:
        reader._log('Definition value is not found.')
        reader._log('Definition value is not found.')
        reader._log('Definition other is not found.')

        assert reader.errors == {
            'GENERAL': {
                'Definition value is not found.',
                'Definition other is not found.',
            }
        }

    assert debug_messages == [
        'Definition value is not found.',
        'Definition other is not found.',
    ]


def test_short_definition_references_are_cached_per_graph_request():
    from nomad.graph.graph_reader import _get_short_definition_reference

    class RuntimeDefinition:
        """Slots-only stand-in so Level 1 cannot store instance cache fields."""

        __slots__ = ('definition_id', 'qualified_name_calls')

        def __init__(self):
            self.definition_id = 'definition-id'
            self.qualified_name_calls = 0

        def qualified_name(self):
            self.qualified_name_calls += 1
            return 'package.Section'

    definition = RuntimeDefinition()
    with ArchiveReader({}) as reader:
        cache = reader._reader_cache
        expected = 'package.Section@definition-id'

        assert _get_short_definition_reference(cache, definition) == expected
        assert _get_short_definition_reference(cache, definition) == expected
        assert definition.qualified_name_calls == 1
        assert cache.short_definition_references[id(definition)][0] is definition


def test_short_definition_references_are_cached_across_requests():
    from nomad.graph.graph_reader import _get_short_definition_reference

    class RuntimeDefinition:
        definition_id = 'definition-id'
        qualified_name_calls = 0
        m_mod_count = 0

        def qualified_name(self):
            self.qualified_name_calls += 1
            return 'package.Section'

    definition = RuntimeDefinition()
    expected = 'package.Section@definition-id'

    # Request 1
    with ArchiveReader({}) as reader1:
        assert (
            _get_short_definition_reference(reader1._reader_cache, definition)
            == expected
        )
        assert definition.qualified_name_calls == 1

    # Request 2 (different reader and fresh reader cache)
    with ArchiveReader({}) as reader2:
        assert (
            _get_short_definition_reference(reader2._reader_cache, definition)
            == expected
        )
        # Should hit definition-level cache without calling qualified_name again
        assert definition.qualified_name_calls == 1

    # Modification invalidates definition-level cache
    definition.m_mod_count += 1
    definition.definition_id = 'definition-id-v2'
    expected_v2 = 'package.Section@definition-id-v2'

    with ArchiveReader({}) as reader3:
        assert (
            _get_short_definition_reference(reader3._reader_cache, definition)
            == expected_v2
        )
        assert definition.qualified_name_calls == 2


def test_short_definition_references_invalidate_on_ancestor_rename():
    from nomad.graph.graph_reader import _get_short_definition_reference
    from nomad.metainfo import Package, Section

    package = Package(name='short_ref_ancestor_v1')
    section = Section(name='MySection')
    package.m_add_sub_section(Package.section_definitions, section)

    with ArchiveReader({}) as reader1:
        ref_v1 = _get_short_definition_reference(reader1._reader_cache, section)
    assert ref_v1.startswith('short_ref_ancestor_v1.MySection@')

    # Renaming the package bumps only the package's m_mod_count, not the child's.
    package.name = 'short_ref_ancestor_v2'

    with ArchiveReader({}) as reader2:
        ref_v2 = _get_short_definition_reference(reader2._reader_cache, section)
    assert ref_v2.startswith('short_ref_ancestor_v2.MySection@')
    assert ref_v2 != ref_v1


def test_metainfo_property_definitions_are_cached_per_graph_request():
    from types import SimpleNamespace

    from nomad.graph.graph_reader import _get_property_definition

    child_definition = object()

    class RuntimeDefinition:
        all_properties = {'child': child_definition}
        resolve_calls = 0

        def m_resolved(self):
            self.resolve_calls += 1
            return self

    definition = RuntimeDefinition()
    with ArchiveReader({}) as reader:
        node = GraphNode(
            upload_id='upload-id',
            entry_id='entry-id',
            current_path=[],
            result_root={},
            ref_result_root={},
            archive=SimpleNamespace(m_def=None),
            archive_root={},
            definition=definition,
            visited_path=set(),
            current_depth=0,
            reader=reader,
        )

        assert _get_property_definition(node, 'child') is child_definition
        assert _get_property_definition(node, 'child') is child_definition
        assert definition.resolve_calls == 1


def test_fast_request_config_copy_matches_validated_copy_and_resets_hash():
    from nomad.graph.graph_reader import _copy_request_config
    from nomad.graph.model import DirectiveType, RequestConfig

    parent = RequestConfig(
        directive=DirectiveType.resolved,
        include=frozenset({'value'}),
        index=(0,),
    )
    parent_hash = parent.hash

    child = _copy_request_config(
        parent,
        property_name='child',
        include=frozenset({'*'}),
        exclude=None,
        index=None,
    )
    validated_child = parent.new(
        {
            'property_name': 'child',
            'include': ['*'],
            'exclude': None,
            'index': None,
        }
    )

    assert child.model_dump() == validated_child.model_dump()
    assert child.hash != parent_hash


def test_fast_request_config_copy_uses_supported_path_for_subclasses():
    from pydantic import ConfigDict, PrivateAttr

    from nomad.graph.graph_reader import _copy_request_config
    from nomad.graph.model import RequestConfig

    class ExtendedRequestConfig(RequestConfig):
        model_config = ConfigDict(extra='allow')
        extra_field: str = 'default'
        _private_state: list[str] = PrivateAttr(default_factory=list)

    parent = ExtendedRequestConfig(extra_field='custom', extension='preserved')
    parent._private_state.append('preserved')
    child = _copy_request_config(parent, property_name='child')

    assert type(child) is ExtendedRequestConfig
    assert child.extra_field == 'custom'
    assert child.extension == 'preserved'
    assert child._private_state == ['preserved']
    assert child.property_name == 'child'


counter = increment()


# noinspection SpellCheckingInspection,DuplicatedCode
def test_remote_reference(json_dict, example_data_with_reference, user1):
    def __user_print(msg, required, *, result: dict | None = None):
        with UserReader(required, user=user1) as reader:
            if result:
                assert_dict(reader.sync_read(user1.user_id), result)
            else:
                rprint(f'\n\nExample: {next(counter)} -> {msg}:')
                rprint(required)
                rprint('output:')
                rprint(reader.sync_read(user1.user_id))

    __user_print(
        'plain user',
        {'m_request': {'directive': 'plain'}},
        result={
            'name': 'Sheldon Cooper',
            'first_name': 'Sheldon',
            'last_name': 'Cooper',
            'email': 'sheldon.cooper@nomad-coe.eu',
            'user_id': '00000000-0000-0000-0000-000000000001',
            'username': 'scooper',
            'is_admin': False,
            'is_oasis_admin': True,
        },
    )
    __user_print(
        'plain user',
        '*',
        result={
            'name': 'Sheldon Cooper',
            'first_name': 'Sheldon',
            'last_name': 'Cooper',
            'email': 'sheldon.cooper@nomad-coe.eu',
            'user_id': '00000000-0000-0000-0000-000000000001',
            'username': 'scooper',
            'is_admin': False,
            'is_oasis_admin': True,
        },
    )

    __user_print(
        'link to uploads',
        {
            'm_request': {'directive': 'plain'},
            Token.UPLOADS: {
                'm_request': {'directive': 'plain'},
            },
        },
        result={
            'name': 'Sheldon Cooper',
            'first_name': 'Sheldon',
            'last_name': 'Cooper',
            'email': 'sheldon.cooper@nomad-coe.eu',
            'user_id': '00000000-0000-0000-0000-000000000001',
            'username': 'scooper',
            'is_admin': False,
            'is_oasis_admin': True,
            Token.UPLOADS: {'id_published_with_ref': 'id_published_with_ref'},
        },
    )

    __user_print(
        'link to uploads, resolve with metadata',
        {
            'm_request': {'directive': 'plain'},
            Token.UPLOADS: {
                'm_request': {'directive': 'resolved', 'resolve_type': 'upload'},
            },
        },
        result={
            'name': 'Sheldon Cooper',
            'first_name': 'Sheldon',
            'last_name': 'Cooper',
            'email': 'sheldon.cooper@nomad-coe.eu',
            'user_id': '00000000-0000-0000-0000-000000000001',
            'username': 'scooper',
            'is_admin': False,
            'is_oasis_admin': True,
            Token.UPLOADS: {
                'id_published_with_ref': {
                    'process_running': False,
                    'current_process': 'process_upload',
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': '2023-03-05T21:24:22.172000',
                    'upload_id': 'id_published_with_ref',
                    'upload_name': 'name_published',
                    'upload_create_time': '2023-03-05T21:24:22.171000',
                    'description': 'Test Description',
                    'doi': None,
                    'main_author': user1_dict,
                    'coauthors': [],
                    'reviewers': [],
                    'coauthor_groups': [],
                    'reviewer_groups': [],
                    'writers': [user1_dict],
                    'viewers': [user1_dict],
                    'writer_groups': [],
                    'viewer_groups': [],
                    'published': False,
                    'processing_failed': 0,
                    'processing_successful': 6,
                    'published_to': [],
                    'publish_time': None,
                    'with_embargo': False,
                    'embargo_length': 0,
                    'license': 'CC BY 4.0',
                    'n_entries': 6,
                    'upload_files_server_path': 'id_published_with_ref',
                }
            },
        },
    )

    __user_print(
        'link to uploads, resolve with metadata, form 2 using dict style with explicit upload id',
        {
            'm_request': {'directive': 'plain'},
            Token.UPLOADS: {
                'id_published_with_ref': {
                    'm_request': {
                        'directive': 'plain',
                    },
                }
            },
        },
        result={
            'name': 'Sheldon Cooper',
            'first_name': 'Sheldon',
            'last_name': 'Cooper',
            'email': 'sheldon.cooper@nomad-coe.eu',
            'user_id': '00000000-0000-0000-0000-000000000001',
            'username': 'scooper',
            'is_admin': False,
            'is_oasis_admin': True,
            Token.UPLOADS: {
                'id_published_with_ref': {
                    'process_running': False,
                    'current_process': 'process_upload',
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': '2023-03-05T21:30:22.807000',
                    'upload_id': 'id_published_with_ref',
                    'upload_name': 'name_published',
                    'upload_create_time': '2023-03-05T21:30:22.806000',
                    'description': 'Test Description',
                    'doi': None,
                    'main_author': user1_dict,
                    'coauthors': [],
                    'reviewers': [],
                    'coauthor_groups': [],
                    'reviewer_groups': [],
                    'writers': [user1_dict],
                    'viewers': [user1_dict],
                    'writer_groups': [],
                    'viewer_groups': [],
                    'published': False,
                    'processing_failed': 0,
                    'processing_successful': 6,
                    'published_to': [],
                    'publish_time': None,
                    'with_embargo': False,
                    'embargo_length': 0,
                    'license': 'CC BY 4.0',
                    'n_entries': 6,
                    'upload_files_server_path': 'id_published_with_ref',
                }
            },
        },
    )

    __user_print(
        'link to entries directly from user, resolve with metadata',
        {
            'm_request': {'directive': 'plain'},
            Token.ENTRIES: {
                'm_request': {'directive': 'resolved', 'resolve_type': 'entry'},
            },
        },
        result={
            'name': 'Sheldon Cooper',
            'first_name': 'Sheldon',
            'last_name': 'Cooper',
            'email': 'sheldon.cooper@nomad-coe.eu',
            'user_id': '00000000-0000-0000-0000-000000000001',
            'username': 'scooper',
            'is_admin': False,
            'is_oasis_admin': True,
            Token.ENTRIES: {
                'id_01': {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_01',
                    'entry_create_time': '2023-03-05T21:27:24.488000',
                    'mainfile_path': 'mainfile_for_id_01',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
                'id_02': {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_02',
                    'entry_create_time': '2023-03-05T21:27:24.489000',
                    'mainfile_path': 'mainfile_for_id_02',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
                'id_03': {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_03',
                    'entry_create_time': '2023-03-05T21:27:24.490000',
                    'mainfile_path': 'mainfile_for_id_03',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
                'id_04': {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_04',
                    'entry_create_time': '2023-03-05T21:27:24.491000',
                    'mainfile_path': 'mainfile_for_id_04',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
                'id_05': {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_05',
                    'entry_create_time': '2023-03-05T21:27:24.492000',
                    'mainfile_path': 'mainfile_for_id_05',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
                'id_06': {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_06',
                    'entry_create_time': '2023-03-05T21:27:24.493000',
                    'mainfile_path': 'mainfile_for_id_06',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
            },
        },
    )

    __user_print(
        'link to entries directly from user, resolve with metadata, form 2 using dict style with explicit entry id',
        {'m_request': {'directive': 'plain'}, Token.ENTRIES: {'id_01': '*'}},
        result={
            'name': 'Sheldon Cooper',
            'first_name': 'Sheldon',
            'last_name': 'Cooper',
            'email': 'sheldon.cooper@nomad-coe.eu',
            'user_id': '00000000-0000-0000-0000-000000000001',
            'username': 'scooper',
            'is_admin': False,
            'is_oasis_admin': True,
            Token.ENTRIES: {
                'id_01': {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_01',
                    'entry_create_time': '2023-03-05T21:30:22.809000',
                    'mainfile_path': 'mainfile_for_id_01',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                }
            },
        },
    )

    __user_print(
        'link to entries from uploads, resolve with metadata',
        {
            'm_request': {'directive': 'plain'},
            Token.UPLOADS: {
                'id_published_with_ref': {
                    'm_request': {
                        'directive': 'plain',
                    },
                    Token.ENTRIES: {
                        'm_request': {'directive': 'resolved', 'resolve_type': 'entry'},
                    },
                }
            },
        },
        result={
            'name': 'Sheldon Cooper',
            'first_name': 'Sheldon',
            'last_name': 'Cooper',
            'email': 'sheldon.cooper@nomad-coe.eu',
            'user_id': '00000000-0000-0000-0000-000000000001',
            'username': 'scooper',
            'is_admin': False,
            'is_oasis_admin': True,
            Token.UPLOADS: {
                'id_published_with_ref': {
                    'process_running': False,
                    'current_process': 'process_upload',
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': '2023-03-05T21:31:56.873000',
                    'upload_id': 'id_published_with_ref',
                    'upload_name': 'name_published',
                    'upload_create_time': '2023-03-05T21:31:56.872000',
                    'description': 'Test Description',
                    'doi': None,
                    'main_author': user1_dict,
                    'coauthors': [],
                    'reviewers': [],
                    'coauthor_groups': [],
                    'reviewer_groups': [],
                    'writers': [user1_dict],
                    'viewers': [user1_dict],
                    'writer_groups': [],
                    'viewer_groups': [],
                    'published': False,
                    'processing_failed': 0,
                    'processing_successful': 6,
                    'published_to': [],
                    'publish_time': None,
                    'with_embargo': False,
                    'embargo_length': 0,
                    'license': 'CC BY 4.0',
                    'n_entries': 6,
                    'upload_files_server_path': 'id_published_with_ref',
                    Token.ENTRIES: {
                        'id_01': {
                            'process_running': False,
                            'current_process': None,
                            'process_status': 'SUCCESS',
                            'last_status_message': None,
                            'errors': [],
                            'warnings': [],
                            'complete_time': None,
                            'entry_id': 'id_01',
                            'entry_create_time': '2023-03-05T21:31:56.875000',
                            'mainfile_path': 'mainfile_for_id_01',
                            'mainfile_key': None,
                            'upload_id': 'id_published_with_ref',
                            'parser_name': 'parsers/vasp',
                        },
                        'id_02': {
                            'process_running': False,
                            'current_process': None,
                            'process_status': 'SUCCESS',
                            'last_status_message': None,
                            'errors': [],
                            'warnings': [],
                            'complete_time': None,
                            'entry_id': 'id_02',
                            'entry_create_time': '2023-03-05T21:31:56.876000',
                            'mainfile_path': 'mainfile_for_id_02',
                            'mainfile_key': None,
                            'upload_id': 'id_published_with_ref',
                            'parser_name': 'parsers/vasp',
                        },
                        'id_03': {
                            'process_running': False,
                            'current_process': None,
                            'process_status': 'SUCCESS',
                            'last_status_message': None,
                            'errors': [],
                            'warnings': [],
                            'complete_time': None,
                            'entry_id': 'id_03',
                            'entry_create_time': '2023-03-05T21:31:56.877000',
                            'mainfile_path': 'mainfile_for_id_03',
                            'mainfile_key': None,
                            'upload_id': 'id_published_with_ref',
                            'parser_name': 'parsers/vasp',
                        },
                        'id_04': {
                            'process_running': False,
                            'current_process': None,
                            'process_status': 'SUCCESS',
                            'last_status_message': None,
                            'errors': [],
                            'warnings': [],
                            'complete_time': None,
                            'entry_id': 'id_04',
                            'entry_create_time': '2023-03-05T21:31:56.878000',
                            'mainfile_path': 'mainfile_for_id_04',
                            'mainfile_key': None,
                            'upload_id': 'id_published_with_ref',
                            'parser_name': 'parsers/vasp',
                        },
                        'id_05': {
                            'process_running': False,
                            'current_process': None,
                            'process_status': 'SUCCESS',
                            'last_status_message': None,
                            'errors': [],
                            'warnings': [],
                            'complete_time': None,
                            'entry_id': 'id_05',
                            'entry_create_time': '2023-03-05T21:31:56.879000',
                            'mainfile_path': 'mainfile_for_id_05',
                            'mainfile_key': None,
                            'upload_id': 'id_published_with_ref',
                            'parser_name': 'parsers/vasp',
                        },
                        'id_06': {
                            'process_running': False,
                            'current_process': None,
                            'process_status': 'SUCCESS',
                            'last_status_message': None,
                            'errors': [],
                            'warnings': [],
                            'complete_time': None,
                            'entry_id': 'id_06',
                            'entry_create_time': '2023-03-05T21:31:56.880000',
                            'mainfile_path': 'mainfile_for_id_06',
                            'mainfile_key': None,
                            'upload_id': 'id_published_with_ref',
                            'parser_name': 'parsers/vasp',
                        },
                    },
                }
            },
        },
    )

    __user_print(
        'link to entries from uploads, resolve with metadata, dict style',
        {
            'm_request': {'directive': 'plain'},
            Token.UPLOADS: {
                'id_published_with_ref': {
                    'm_request': {
                        'directive': 'plain',
                    },
                    Token.ENTRIES: {
                        'id_01': {
                            'm_request': {
                                'directive': 'plain',
                            }
                        },
                        '*': {
                            'm_request': {
                                'directive': 'plain',
                                'include': ['entry_id', 'mainfile_path'],
                            },
                        },
                    },
                }
            },
        },
        result={
            'name': 'Sheldon Cooper',
            'first_name': 'Sheldon',
            'last_name': 'Cooper',
            'email': 'sheldon.cooper@nomad-coe.eu',
            'user_id': '00000000-0000-0000-0000-000000000001',
            'username': 'scooper',
            'is_admin': False,
            'is_oasis_admin': True,
            Token.UPLOADS: {
                'id_published_with_ref': {
                    'process_running': False,
                    'current_process': 'process_upload',
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': '2023-03-05T21:31:56.873000',
                    'upload_id': 'id_published_with_ref',
                    'upload_name': 'name_published',
                    'upload_create_time': '2023-03-05T21:31:56.872000',
                    'description': 'Test Description',
                    'doi': None,
                    'main_author': user1_dict,
                    'coauthors': [],
                    'reviewers': [],
                    'coauthor_groups': [],
                    'reviewer_groups': [],
                    'writers': [user1_dict],
                    'viewers': [user1_dict],
                    'writer_groups': [],
                    'viewer_groups': [],
                    'published': False,
                    'processing_failed': 0,
                    'processing_successful': 6,
                    'published_to': [],
                    'publish_time': None,
                    'with_embargo': False,
                    'embargo_length': 0,
                    'license': 'CC BY 4.0',
                    'n_entries': 6,
                    'upload_files_server_path': 'id_published_with_ref',
                    Token.ENTRIES: {
                        'id_02': {
                            'entry_id': 'id_02',
                            'mainfile_path': 'mainfile_for_id_02',
                        },
                        'id_03': {
                            'entry_id': 'id_03',
                            'mainfile_path': 'mainfile_for_id_03',
                        },
                        'id_04': {
                            'entry_id': 'id_04',
                            'mainfile_path': 'mainfile_for_id_04',
                        },
                        'id_05': {
                            'entry_id': 'id_05',
                            'mainfile_path': 'mainfile_for_id_05',
                        },
                        'id_06': {
                            'entry_id': 'id_06',
                            'mainfile_path': 'mainfile_for_id_06',
                        },
                        'id_01': {
                            'process_running': False,
                            'current_process': None,
                            'process_status': 'SUCCESS',
                            'last_status_message': None,
                            'errors': [],
                            'warnings': [],
                            'complete_time': None,
                            'entry_id': 'id_01',
                            'entry_create_time': '2023-03-05T21:31:56.875000',
                            'mainfile_path': 'mainfile_for_id_01',
                            'mainfile_key': None,
                            'upload_id': 'id_published_with_ref',
                            'parser_name': 'parsers/vasp',
                        },
                    },
                }
            },
        },
    )

    __user_print(
        'uploads to entries back to uploads',
        {
            Token.UPLOADS: {
                'id_published_with_ref': {
                    Token.ENTRIES: {
                        'id_01': {
                            'm_request': {
                                'directive': 'plain',
                            },
                            'upload_id': {
                                'm_request': {
                                    'directive': 'resolved',
                                    'resolve_type': 'upload',
                                }
                            },
                        }
                    }
                }
            }
        },
        result={
            Token.UPLOADS: {
                'id_published_with_ref': {
                    Token.ENTRIES: {
                        'id_01': {
                            'process_running': False,
                            'current_process': None,
                            'process_status': 'SUCCESS',
                            'last_status_message': None,
                            'errors': [],
                            'warnings': [],
                            'complete_time': None,
                            'entry_id': 'id_01',
                            'entry_create_time': '2023-03-05T21:31:56.875000',
                            'mainfile_path': 'mainfile_for_id_01',
                            'mainfile_key': None,
                            'parser_name': 'parsers/vasp',
                            'upload_id': {
                                'process_running': False,
                                'current_process': 'process_upload',
                                'process_status': 'SUCCESS',
                                'last_status_message': None,
                                'errors': [],
                                'warnings': [],
                                'complete_time': '2023-03-05T21:31:56.873000',
                                'upload_id': 'id_published_with_ref',
                                'upload_name': 'name_published',
                                'upload_create_time': '2023-03-05T21:31:56.872000',
                                'description': 'Test Description',
                                'doi': None,
                                'main_author': user1_dict,
                                'coauthors': [],
                                'reviewers': [],
                                'coauthor_groups': [],
                                'reviewer_groups': [],
                                'writers': [user1_dict],
                                'viewers': [user1_dict],
                                'writer_groups': [],
                                'viewer_groups': [],
                                'published': False,
                                'processing_failed': 0,
                                'processing_successful': 6,
                                'published_to': [],
                                'publish_time': None,
                                'with_embargo': False,
                                'embargo_length': 0,
                                'license': 'CC BY 4.0',
                                'n_entries': 6,
                                'upload_files_server_path': 'id_published_with_ref',
                            },
                        }
                    }
                }
            }
        },
    )

    __user_print(
        'uploads to entries to archive',
        {
            Token.UPLOADS: {
                'id_published_with_ref': {
                    Token.ENTRIES: {
                        'id_01': {
                            'm_request': {
                                'directive': 'plain',
                            },
                            Token.ARCHIVE: {
                                'm_request': {
                                    'directive': 'plain',
                                    'include': ['results'],
                                },
                            },
                        }
                    }
                }
            }
        },
        result={
            Token.UPLOADS: {
                'id_published_with_ref': {
                    Token.ENTRIES: {
                        'id_01': {
                            'process_running': False,
                            'current_process': None,
                            'process_status': 'SUCCESS',
                            'last_status_message': None,
                            'errors': [],
                            'warnings': [],
                            'complete_time': None,
                            'entry_id': 'id_01',
                            'entry_create_time': '2023-03-05T21:31:56.875000',
                            'mainfile_path': 'mainfile_for_id_01',
                            'mainfile_key': None,
                            'upload_id': 'id_published_with_ref',
                            'parser_name': 'parsers/vasp',
                            Token.ARCHIVE: {
                                'results': {
                                    'material': {
                                        'dimensionality': '3D',
                                        'material_id': 'test_material_id',
                                        'elements': ['H', 'O'],
                                        'symmetry': {'crystal_system': 'cubic'},
                                    },
                                    'method': {
                                        'simulation': {
                                            'program_name': 'VASP',
                                            'dft': {'xc_functional_type': 'GGA'},
                                        }
                                    },
                                    'properties': {
                                        'n_calculations': 1,
                                        'electronic': {
                                            'dos_electronic': [
                                                {
                                                    'spin_polarized': False,
                                                    'band_gap': [{'type': 'indirect'}],
                                                }
                                            ]
                                        },
                                    },
                                }
                            },
                        }
                    }
                }
            }
        },
    )

    def __upload_print(msg, required, *, result: dict | None = None):
        with UploadReader(required, user=user1) as reader:
            if result:
                assert_dict(reader.sync_read('id_published_with_ref'), result)
            else:
                rprint(f'\n\nExample: {next(counter)} -> {msg}:')
                rprint(required)
                rprint('output:')
                rprint(reader.sync_read('id_published_with_ref'))

    __upload_print(
        'plain upload reader',
        {
            'm_request': {
                'directive': 'plain',
            },
        },
        result={
            'process_running': False,
            'current_process': 'process_upload',
            'process_status': 'SUCCESS',
            'last_status_message': None,
            'errors': [],
            'warnings': [],
            'complete_time': '2023-03-05T22:12:08.421000',
            'upload_id': 'id_published_with_ref',
            'upload_name': 'name_published',
            'upload_create_time': '2023-03-05T22:12:08.420000',
            'description': 'Test Description',
            'doi': None,
            'main_author': user1_dict,
            'coauthors': [],
            'reviewers': [],
            'coauthor_groups': [],
            'reviewer_groups': [],
            'writers': [user1_dict],
            'viewers': [user1_dict],
            'writer_groups': [],
            'viewer_groups': [],
            'published': False,
            'published_to': [],
            'publish_time': None,
            'with_embargo': False,
            'embargo_length': 0,
            'processing_failed': 0,
            'processing_successful': 6,
            'license': 'CC BY 4.0',
            'n_entries': 6,
            'upload_files_server_path': 'id_published_with_ref',
        },
    )

    __upload_print(
        'upload, resolve raw files',
        {
            'm_request': '*',
            Token.RAW: {
                'm_request': '*',
                'mainfile_for_id_01': {
                    'm_request': {
                        'directive': 'resolved',
                    },
                },
            },
        },
        result={
            'process_running': False,
            'current_process': 'process_upload',
            'process_status': 'SUCCESS',
            'last_status_message': None,
            'errors': [],
            'warnings': [],
            'complete_time': '2023-03-05T22:16:52.436000',
            'upload_id': 'id_published_with_ref',
            'upload_name': 'name_published',
            'upload_create_time': '2023-03-05T22:16:52.435000',
            'description': 'Test Description',
            'doi': None,
            'main_author': user1_dict,
            'coauthors': [],
            'reviewers': [],
            'coauthor_groups': [],
            'reviewer_groups': [],
            'writers': [user1_dict],
            'viewers': [user1_dict],
            'writer_groups': [],
            'viewer_groups': [],
            'published': False,
            'published_to': [],
            'publish_time': None,
            'with_embargo': False,
            'embargo_length': 0,
            'processing_failed': 0,
            'processing_successful': 6,
            'license': 'CC BY 4.0',
            'n_entries': 6,
            'upload_files_server_path': 'id_published_with_ref',
            Token.RAW: {
                'm_is': 'Directory',
                '1.aux': {'path': '1.aux', 'm_is': 'File', 'size': 8},
                '2.aux': {'path': '2.aux', 'm_is': 'File', 'size': 8},
                '3.aux': {'path': '3.aux', 'm_is': 'File', 'size': 8},
                '4.aux': {'path': '4.aux', 'm_is': 'File', 'size': 8},
                'mainfile_for_id_02': {
                    'path': 'mainfile_for_id_02',
                    'm_is': 'File',
                    'size': 3227,
                },
                'mainfile_for_id_03': {
                    'path': 'mainfile_for_id_03',
                    'm_is': 'File',
                    'size': 3227,
                },
                'mainfile_for_id_04': {
                    'path': 'mainfile_for_id_04',
                    'm_is': 'File',
                    'size': 3227,
                },
                'mainfile_for_id_05': {
                    'path': 'mainfile_for_id_05',
                    'm_is': 'File',
                    'size': 3227,
                },
                'mainfile_for_id_06': {
                    'path': 'mainfile_for_id_06',
                    'm_is': 'File',
                    'size': 3227,
                },
                'mainfile_for_id_01': {
                    'path': 'mainfile_for_id_01',
                    'm_is': 'File',
                    'size': 3227,
                    Token.ENTRY: {
                        'process_running': False,
                        'current_process': None,
                        'process_status': 'SUCCESS',
                        'last_status_message': None,
                        'errors': [],
                        'warnings': [],
                        'complete_time': None,
                        'entry_id': 'id_01',
                        'entry_create_time': '2023-03-05T22:16:52.438000',
                        'mainfile_path': 'mainfile_for_id_01',
                        'mainfile_key': None,
                        'upload_id': 'id_published_with_ref',
                        'parser_name': 'parsers/vasp',
                    },
                },
            },
        },
    )

    __upload_print(
        'upload, resolve user',
        {
            'm_request': {
                'directive': 'plain',
            },
            'viewers': {'m_request': {'directive': 'resolved', 'resolve_type': 'user'}},
        },
        result={
            'process_running': False,
            'current_process': 'process_upload',
            'process_status': 'SUCCESS',
            'last_status_message': None,
            'errors': [],
            'warnings': [],
            'complete_time': '2023-03-05T22:16:52.436000',
            'upload_id': 'id_published_with_ref',
            'upload_name': 'name_published',
            'upload_create_time': '2023-03-05T22:16:52.435000',
            'description': 'Test Description',
            'doi': None,
            'main_author': user1_dict,
            'coauthors': [],
            'reviewers': [],
            'coauthor_groups': [],
            'reviewer_groups': [],
            'writers': [user1_dict],
            'writer_groups': [],
            'viewer_groups': [],
            'published': False,
            'published_to': [],
            'publish_time': None,
            'with_embargo': False,
            'embargo_length': 0,
            'license': 'CC BY 4.0',
            'n_entries': 6,
            'processing_failed': 0,
            'processing_successful': 6,
            'upload_files_server_path': 'id_published_with_ref',
            'viewers': [user1_dict],
        },
    )

    __upload_print(
        'resolve itself using upload id',
        {
            'm_request': {
                'directive': 'plain',
            },
            'upload_id': {
                'm_request': {'directive': 'resolved', 'resolve_type': 'upload'}
            },
        },
        result={
            'process_running': False,
            'current_process': 'process_upload',
            'process_status': 'SUCCESS',
            'last_status_message': None,
            'errors': [],
            'warnings': [],
            'complete_time': '2023-03-05T22:16:52.436000',
            'upload_name': 'name_published',
            'upload_create_time': '2023-03-05T22:16:52.435000',
            'description': 'Test Description',
            'doi': None,
            'main_author': user1_dict,
            'coauthors': [],
            'reviewers': [],
            'coauthor_groups': [],
            'reviewer_groups': [],
            'writers': [user1_dict],
            'viewers': [user1_dict],
            'writer_groups': [],
            'viewer_groups': [],
            'published': False,
            'published_to': [],
            'publish_time': None,
            'with_embargo': False,
            'embargo_length': 0,
            'license': 'CC BY 4.0',
            'n_entries': 6,
            'processing_failed': 0,
            'processing_successful': 6,
            'upload_files_server_path': 'id_published_with_ref',
            'upload_id': {
                'process_running': False,
                'current_process': 'process_upload',
                'process_status': 'SUCCESS',
                'last_status_message': None,
                'errors': [],
                'warnings': [],
                'complete_time': '2023-03-05T22:16:52.436000',
                'upload_id': 'id_published_with_ref',
                'upload_name': 'name_published',
                'upload_create_time': '2023-03-05T22:16:52.435000',
                'description': 'Test Description',
                'doi': None,
                'main_author': user1_dict,
                'coauthors': [],
                'reviewers': [],
                'coauthor_groups': [],
                'reviewer_groups': [],
                'writers': [user1_dict],
                'viewers': [user1_dict],
                'writer_groups': [],
                'viewer_groups': [],
                'published': False,
                'published_to': [],
                'publish_time': None,
                'with_embargo': False,
                'embargo_length': 0,
                'license': 'CC BY 4.0',
                'n_entries': 6,
                'processing_failed': 0,
                'processing_successful': 6,
                'upload_files_server_path': 'id_published_with_ref',
            },
        },
    )

    __upload_print(
        'resolve itself twice then go to entry',
        {
            'upload_id': {
                'm_request': {
                    'directive': 'plain',
                },
                'upload_id': {
                    Token.ENTRIES: {
                        'id_01': {
                            'm_request': {
                                'directive': 'plain',
                            },
                        }
                    }
                },
            }
        },
        result={
            'upload_id': {
                'process_running': False,
                'current_process': 'process_upload',
                'process_status': 'SUCCESS',
                'last_status_message': None,
                'errors': [],
                'warnings': [],
                'complete_time': '2023-03-05T22:16:52.436000',
                'upload_name': 'name_published',
                'upload_create_time': '2023-03-05T22:16:52.435000',
                'description': 'Test Description',
                'doi': None,
                'main_author': user1_dict,
                'coauthors': [],
                'reviewers': [],
                'coauthor_groups': [],
                'reviewer_groups': [],
                'writers': [user1_dict],
                'viewers': [user1_dict],
                'writer_groups': [],
                'viewer_groups': [],
                'published': False,
                'published_to': [],
                'publish_time': None,
                'with_embargo': False,
                'embargo_length': 0,
                'license': 'CC BY 4.0',
                'n_entries': 6,
                'processing_failed': 0,
                'processing_successful': 6,
                'upload_files_server_path': 'id_published_with_ref',
                'upload_id': {
                    Token.ENTRIES: {
                        'id_01': {
                            'process_running': False,
                            'current_process': None,
                            'process_status': 'SUCCESS',
                            'last_status_message': None,
                            'errors': [],
                            'warnings': [],
                            'complete_time': None,
                            'entry_id': 'id_01',
                            'entry_create_time': '2023-03-05T22:16:52.438000',
                            'mainfile_path': 'mainfile_for_id_01',
                            'mainfile_key': None,
                            'upload_id': 'id_published_with_ref',
                            'parser_name': 'parsers/vasp',
                        }
                    }
                },
            }
        },
    )

    __upload_print(
        'resolve itself twice then go to entry, collect other info on different levels',
        {
            'm_request': {
                'directive': 'plain',
            },
            'upload_id': {
                'upload_id': {
                    Token.ENTRIES: {
                        'id_01': {
                            'm_request': {
                                'directive': 'plain',
                            },
                        }
                    }
                }
            },
        },
        result={
            'process_running': False,
            'current_process': 'process_upload',
            'process_status': 'SUCCESS',
            'last_status_message': None,
            'errors': [],
            'warnings': [],
            'complete_time': '2023-03-05T22:16:52.436000',
            'upload_name': 'name_published',
            'upload_create_time': '2023-03-05T22:16:52.435000',
            'description': 'Test Description',
            'doi': None,
            'main_author': user1_dict,
            'coauthors': [],
            'reviewers': [],
            'coauthor_groups': [],
            'reviewer_groups': [],
            'writers': [user1_dict],
            'viewers': [user1_dict],
            'writer_groups': [],
            'viewer_groups': [],
            'published': False,
            'published_to': [],
            'publish_time': None,
            'with_embargo': False,
            'embargo_length': 0,
            'license': 'CC BY 4.0',
            'n_entries': 6,
            'processing_failed': 0,
            'processing_successful': 6,
            'upload_files_server_path': 'id_published_with_ref',
            'upload_id': {
                'upload_id': {
                    Token.ENTRIES: {
                        'id_01': {
                            'process_running': False,
                            'current_process': None,
                            'process_status': 'SUCCESS',
                            'last_status_message': None,
                            'errors': [],
                            'warnings': [],
                            'complete_time': None,
                            'entry_id': 'id_01',
                            'entry_create_time': '2023-03-05T22:16:52.438000',
                            'mainfile_path': 'mainfile_for_id_01',
                            'mainfile_key': None,
                            'upload_id': 'id_published_with_ref',
                            'parser_name': 'parsers/vasp',
                        }
                    }
                }
            },
        },
    )

    def __entry_print(
        msg, required, *, to_file: bool = False, result: dict | None = None
    ):
        with EntryReader(required, user=user1) as reader:
            if result:
                assert_dict(reader.sync_read('id_03'), result)
            else:
                rprint(f'\n\nExample: {next(counter)} -> {msg}:')
                rprint(required)
                if not to_file:
                    rprint('output:')
                    rprint(reader.sync_read('id_03'))
                else:
                    with open('entry_reader_test.json', 'w') as f:
                        f.write(json.dumps(reader.sync_read('id_03')))

    __entry_print(
        'plain entry reader',
        {
            'm_request': {
                'directive': 'plain',
            },
        },
        result={
            'process_running': False,
            'current_process': None,
            'process_status': 'SUCCESS',
            'last_status_message': None,
            'errors': [],
            'warnings': [],
            'complete_time': None,
            'entry_id': 'id_03',
            'entry_create_time': '2023-03-05T22:16:52.440000',
            'mainfile_path': 'mainfile_for_id_03',
            'mainfile_key': None,
            'upload_id': 'id_published_with_ref',
            'parser_name': 'parsers/vasp',
        },
    )
    __entry_print(
        'plain entry reader, resolve inplace',
        {
            Token.ARCHIVE: {
                'm_request': {
                    'directive': 'resolved',
                    'resolve_inplace': True,
                    'include': ['workflow2'],
                },
            }
        },
        result={
            Token.UPLOADS: {
                'id_published_with_ref': {
                    Token.ENTRIES: {
                        'id_01': {
                            Token.ARCHIVE: {
                                'workflow2': {
                                    'results': {
                                        'calculation_result_ref': 'uploads/id_published_with_ref/entries/id_01/archive/run/0/calculation/1'
                                    }
                                },
                                'run': [
                                    {
                                        'calculation': [
                                            None,
                                            {
                                                'system_ref': 'uploads/id_published_with_ref/entries/id_01/archive/run/0/system/1',
                                                'energy': {'total': {'value': 0.2}},
                                                'dos_electronic': [
                                                    {
                                                        'energies': [
                                                            0.0,
                                                            0.1,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                        ]
                                                    }
                                                ],
                                            },
                                        ],
                                        'system': [
                                            None,
                                            {
                                                'atoms': {'labels': ['H']},
                                                'symmetry': [
                                                    {'space_group_number': 221}
                                                ],
                                            },
                                        ],
                                    }
                                ],
                            }
                        }
                    }
                }
            },
            Token.ARCHIVE: {
                'workflow2': {
                    'tasks': [
                        {
                            'task': 'uploads/id_published_with_ref/entries/id_01/archive/workflow2'
                        }
                    ]
                }
            },
        },
    )
    __entry_print(
        'plain entry reader, resolve to root',
        {
            Token.ARCHIVE: {
                'm_request': {
                    'directive': 'resolved',
                    'resolve_inplace': False,
                    'include': ['workflow2'],
                },
            }
        },
        result={
            Token.UPLOADS: {
                'id_published_with_ref': {
                    Token.ENTRIES: {
                        'id_01': {
                            Token.ARCHIVE: {
                                'workflow2': {
                                    'results': {
                                        'calculation_result_ref': 'uploads/id_published_with_ref/entries/id_01/archive/run/0/calculation/1'
                                    }
                                },
                                'run': [
                                    {
                                        'calculation': [
                                            None,
                                            {
                                                'system_ref': 'uploads/id_published_with_ref/entries/id_01/archive/run/0/system/1',
                                                'energy': {'total': {'value': 0.2}},
                                                'dos_electronic': [
                                                    {
                                                        'energies': [
                                                            0.0,
                                                            0.1,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                            1.0,
                                                        ]
                                                    }
                                                ],
                                            },
                                        ],
                                        'system': [
                                            None,
                                            {
                                                'atoms': {'labels': ['H']},
                                                'symmetry': [
                                                    {'space_group_number': 221}
                                                ],
                                            },
                                        ],
                                    }
                                ],
                            }
                        }
                    }
                }
            },
            Token.ARCHIVE: {
                'workflow2': {
                    'tasks': [
                        {
                            'task': 'uploads/id_published_with_ref/entries/id_01/archive/workflow2'
                        }
                    ]
                }
            },
        },
    )
    __entry_print(
        'plain entry reader, resolve to root',
        {
            Token.ARCHIVE: {
                'metadata': {
                    'm_request': {'directive': 'plain', 'depth': 1, 'max_list_size': 1},
                }
            }
        },
        result={
            'archive': {
                'metadata': {
                    'domain': 'dft',
                    'embargo_length': 0,
                    'entry_create_time': '2024-05-28T19:14:10.754059+00:00',
                    'entry_hash': 'dummy_hash_id_03',
                    'entry_id': 'id_03',
                    'entry_references': '__INTERNAL__:../uploads/id_published_with_ref/archive/id_03#/metadata/entry_references',
                    'license': 'CC BY 4.0',
                    'main_author': '00000000-0000-0000-0000-000000000001',
                    'mainfile': 'mainfile_for_id_03',
                    'n_quantities': 67,
                    'parser_name': 'parsers/vasp',
                    'processed': True,
                    'published': False,
                    'quantities': '__INTERNAL__:../uploads/id_published_with_ref/archive/id_03#/metadata/quantities',
                    'section_defs': '__INTERNAL__:../uploads/id_published_with_ref/archive/id_03#/metadata/section_defs',
                    'sections': '__INTERNAL__:../uploads/id_published_with_ref/archive/id_03#/metadata/sections',
                    'text_search_contents': '__INTERNAL__:../uploads/id_published_with_ref/archive/id_03#/metadata/text_search_contents',
                    'upload_create_time': '2024-05-28T19:14:10.749059+00:00',
                    'upload_id': 'id_published_with_ref',
                    'upload_name': 'name_published',
                    'description': 'Test Description',
                    'with_embargo': False,
                }
            }
        },
    )
    if simulationworkflowschema is not None:
        __entry_print(
            'entry reader to definition reader',
            {
                Token.ARCHIVE: {
                    'workflow2': {
                        'm_def': {
                            'm_request': {
                                'directive': 'plain',
                            }
                        }
                    }
                }
            },
            result={
                'metainfo': {
                    'simulationworkflowschema.general': {
                        'section_definitions': [
                            None,
                            None,
                            {
                                'name': 'SimulationWorkflow',
                                'base_sections': [
                                    'metainfo/nomad.datamodel.metainfo.workflow/section_definitions/3'
                                ],
                                'sub_sections': [
                                    {
                                        'name': 'method',
                                        'sub_section': 'metainfo/simulationworkflowschema.general/section_definitions/0',
                                    },
                                    {
                                        'name': 'results',
                                        'categories': ['/category_definitions/0'],
                                        'sub_section': 'metainfo/simulationworkflowschema.general/section_definitions/1',
                                    },
                                ],
                            },
                        ]
                    }
                },
                'archive': {
                    'workflow2': {
                        'm_def': {
                            'm_def': 'metainfo/simulationworkflowschema.general/section_definitions/2'
                        }
                    }
                },
            },
        )
        __entry_print(
            'plain without rewriting references',
            {
                Token.ARCHIVE: {
                    'workflow2': {
                        'm_request': {
                            'directive': 'plain',
                        }
                    }
                }
            },
            result={
                'archive': {
                    'workflow2': {
                        'tasks': [
                            {
                                'm_def': 'nomad.datamodel.metainfo.workflow.TaskReference',
                                'task': '../entries/id_01/archive#/workflow2',
                            }
                        ]
                    }
                }
            },
        )
        __entry_print(
            'plain with rewriting references',
            {
                Token.ARCHIVE: {
                    'workflow2': {
                        'm_request': {
                            'directive': 'plain',
                            'always_rewrite_references': True,
                        }
                    }
                }
            },
            result={
                'archive': {
                    'workflow2': {
                        'tasks': [
                            {
                                'task': 'uploads/id_published_with_ref/entries/id_01/archive/workflow2'
                            }
                        ]
                    }
                }
            },
        )

    __entry_print(
        'go to upload, resolve explicitly',
        {
            'm_request': {
                'directive': 'plain',
            },
            'upload_id': {
                'm_request': {'directive': 'resolved', 'resolve_type': 'upload'}
            },
        },
        result={
            'process_running': False,
            'current_process': None,
            'process_status': 'SUCCESS',
            'last_status_message': None,
            'errors': [],
            'warnings': [],
            'complete_time': None,
            'entry_id': 'id_03',
            'entry_create_time': '2023-03-05T22:16:52.440000',
            'mainfile_path': 'mainfile_for_id_03',
            'mainfile_key': None,
            'parser_name': 'parsers/vasp',
            'upload_id': {
                'process_running': False,
                'current_process': 'process_upload',
                'process_status': 'SUCCESS',
                'last_status_message': None,
                'errors': [],
                'warnings': [],
                'complete_time': '2023-03-05T22:16:52.436000',
                'upload_id': 'id_published_with_ref',
                'upload_name': 'name_published',
                'upload_create_time': '2023-03-05T22:16:52.435000',
                'description': 'Test Description',
                'doi': None,
                'main_author': user1_dict,
                'coauthors': [],
                'reviewers': [],
                'coauthor_groups': [],
                'reviewer_groups': [],
                'writers': [user1_dict],
                'viewers': [user1_dict],
                'writer_groups': [],
                'viewer_groups': [],
                'published': False,
                'published_to': [],
                'publish_time': None,
                'with_embargo': False,
                'embargo_length': 0,
                'processing_failed': 0,
                'processing_successful': 6,
                'license': 'CC BY 4.0',
                'n_entries': 6,
                'upload_files_server_path': 'id_published_with_ref',
            },
        },
    )

    __entry_print(
        'go to upload, resolve implicitly, resolve main author explicitly',
        {
            'm_request': {
                'directive': 'plain',
            },
            'upload_id': {
                'm_request': {
                    'directive': 'plain',
                },
                'main_author': {
                    'm_request': {'directive': 'resolved', 'resolve_type': 'user'}
                },
            },
        },
        result={
            'process_running': False,
            'current_process': None,
            'process_status': 'SUCCESS',
            'last_status_message': None,
            'errors': [],
            'warnings': [],
            'complete_time': None,
            'entry_id': 'id_03',
            'entry_create_time': '2023-03-05T22:16:52.440000',
            'mainfile_path': 'mainfile_for_id_03',
            'mainfile_key': None,
            'parser_name': 'parsers/vasp',
            'upload_id': {
                'process_running': False,
                'current_process': 'process_upload',
                'process_status': 'SUCCESS',
                'last_status_message': None,
                'errors': [],
                'warnings': [],
                'complete_time': '2023-03-05T22:16:52.436000',
                'upload_id': 'id_published_with_ref',
                'upload_name': 'name_published',
                'upload_create_time': '2023-03-05T22:16:52.435000',
                'description': 'Test Description',
                'doi': None,
                'coauthors': [],
                'reviewers': [],
                'coauthor_groups': [],
                'reviewer_groups': [],
                'writers': [user1_dict],
                'viewers': [user1_dict],
                'writer_groups': [],
                'viewer_groups': [],
                'published': False,
                'published_to': [],
                'publish_time': None,
                'with_embargo': False,
                'embargo_length': 0,
                'license': 'CC BY 4.0',
                'n_entries': 6,
                'processing_failed': 0,
                'processing_successful': 6,
                'upload_files_server_path': 'id_published_with_ref',
                'main_author': user1_dict,
            },
        },
    )

    def __fs_print(msg, required, *, result: dict | None = None):
        with FileSystemReader(required, user=user1) as reader:
            if result:
                assert_dict(reader.sync_read('id_published_with_ref'), result)
            else:
                rprint(f'\n\nExample: {next(counter)} -> {msg}:')
                rprint(required)
                rprint('output:')
                rprint(reader.sync_read('id_published_with_ref'))

    __fs_print(
        'plain file system reader',
        {
            'm_request': {
                'directive': 'plain',
            },
        },
        result={
            'm_is': 'Directory',
            '1.aux': {'path': '1.aux', 'm_is': 'File', 'size': 8},
            '2.aux': {'path': '2.aux', 'm_is': 'File', 'size': 8},
            '3.aux': {'path': '3.aux', 'm_is': 'File', 'size': 8},
            '4.aux': {'path': '4.aux', 'm_is': 'File', 'size': 8},
            'mainfile_for_id_01': {
                'path': 'mainfile_for_id_01',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_02': {
                'path': 'mainfile_for_id_02',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_03': {
                'path': 'mainfile_for_id_03',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_04': {
                'path': 'mainfile_for_id_04',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_05': {
                'path': 'mainfile_for_id_05',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_06': {
                'path': 'mainfile_for_id_06',
                'm_is': 'File',
                'size': 3227,
            },
        },
    )

    __fs_print(
        'go to entry',
        {
            'm_request': {
                'directive': 'resolved',
            },
        },
        result={
            'm_is': 'Directory',
            '1.aux': {'path': '1.aux', 'm_is': 'File', 'size': 8},
            '2.aux': {'path': '2.aux', 'm_is': 'File', 'size': 8},
            '3.aux': {'path': '3.aux', 'm_is': 'File', 'size': 8},
            '4.aux': {'path': '4.aux', 'm_is': 'File', 'size': 8},
            'mainfile_for_id_01': {
                'path': 'mainfile_for_id_01',
                'm_is': 'File',
                'size': 3227,
                Token.ENTRY: {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_01',
                    'entry_create_time': '2023-03-05T22:16:52.438000',
                    'mainfile_path': 'mainfile_for_id_01',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
            },
            'mainfile_for_id_02': {
                'path': 'mainfile_for_id_02',
                'm_is': 'File',
                'size': 3227,
                Token.ENTRY: {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_02',
                    'entry_create_time': '2023-03-05T22:16:52.439000',
                    'mainfile_path': 'mainfile_for_id_02',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
            },
            'mainfile_for_id_03': {
                'path': 'mainfile_for_id_03',
                'm_is': 'File',
                'size': 3227,
                Token.ENTRY: {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_03',
                    'entry_create_time': '2023-03-05T22:16:52.440000',
                    'mainfile_path': 'mainfile_for_id_03',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
            },
            'mainfile_for_id_04': {
                'path': 'mainfile_for_id_04',
                'm_is': 'File',
                'size': 3227,
                Token.ENTRY: {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_04',
                    'entry_create_time': '2023-03-05T22:16:52.441000',
                    'mainfile_path': 'mainfile_for_id_04',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
            },
            'mainfile_for_id_05': {
                'path': 'mainfile_for_id_05',
                'm_is': 'File',
                'size': 3227,
                Token.ENTRY: {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_05',
                    'entry_create_time': '2023-03-05T22:16:52.442000',
                    'mainfile_path': 'mainfile_for_id_05',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
            },
            'mainfile_for_id_06': {
                'path': 'mainfile_for_id_06',
                'm_is': 'File',
                'size': 3227,
                Token.ENTRY: {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_06',
                    'entry_create_time': '2023-03-05T22:16:52.443000',
                    'mainfile_path': 'mainfile_for_id_06',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
            },
        },
    )

    __fs_print(
        'go to selected entry',
        {
            'm_request': {
                'directive': 'plain',
            },
            'mainfile_for_id_01': {
                'm_request': {
                    'directive': 'resolved',
                },
            },
        },
        result={
            'm_is': 'Directory',
            '1.aux': {'path': '1.aux', 'm_is': 'File', 'size': 8},
            '2.aux': {'path': '2.aux', 'm_is': 'File', 'size': 8},
            '3.aux': {'path': '3.aux', 'm_is': 'File', 'size': 8},
            '4.aux': {'path': '4.aux', 'm_is': 'File', 'size': 8},
            'mainfile_for_id_02': {
                'path': 'mainfile_for_id_02',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_03': {
                'path': 'mainfile_for_id_03',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_04': {
                'path': 'mainfile_for_id_04',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_05': {
                'path': 'mainfile_for_id_05',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_06': {
                'path': 'mainfile_for_id_06',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_01': {
                'path': 'mainfile_for_id_01',
                'm_is': 'File',
                'size': 3227,
                Token.ENTRY: {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_01',
                    'entry_create_time': '2023-03-05T22:16:52.438000',
                    'mainfile_path': 'mainfile_for_id_01',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
            },
        },
    )

    __fs_print(
        'go to selected entry then to upload',
        {
            'm_request': {
                'directive': 'plain',
            },
            'mainfile_for_id_01': {
                'm_request': {
                    'directive': 'plain',
                },
                Token.ENTRY: {
                    'upload_id': {
                        'm_request': {
                            'directive': 'resolved',
                            'resolve_type': 'upload',
                        },
                    }
                },
            },
        },
        result={
            'm_is': 'Directory',
            '1.aux': {'path': '1.aux', 'm_is': 'File', 'size': 8},
            '2.aux': {'path': '2.aux', 'm_is': 'File', 'size': 8},
            '3.aux': {'path': '3.aux', 'm_is': 'File', 'size': 8},
            '4.aux': {'path': '4.aux', 'm_is': 'File', 'size': 8},
            'mainfile_for_id_02': {
                'path': 'mainfile_for_id_02',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_03': {
                'path': 'mainfile_for_id_03',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_04': {
                'path': 'mainfile_for_id_04',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_05': {
                'path': 'mainfile_for_id_05',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_06': {
                'path': 'mainfile_for_id_06',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_01': {
                'path': 'mainfile_for_id_01',
                'm_is': 'File',
                'size': 3227,
                Token.ENTRY: {
                    'upload_id': {
                        'process_running': False,
                        'current_process': 'process_upload',
                        'process_status': 'SUCCESS',
                        'last_status_message': None,
                        'errors': [],
                        'warnings': [],
                        'complete_time': '2023-03-05T22:16:52.436000',
                        'upload_id': 'id_published_with_ref',
                        'upload_name': 'name_published',
                        'upload_create_time': '2023-03-05T22:16:52.435000',
                        'description': 'Test Description',
                        'doi': None,
                        'main_author': user1_dict,
                        'coauthors': [],
                        'reviewers': [],
                        'coauthor_groups': [],
                        'reviewer_groups': [],
                        'writers': [user1_dict],
                        'viewers': [user1_dict],
                        'writer_groups': [],
                        'viewer_groups': [],
                        'published': False,
                        'published_to': [],
                        'publish_time': None,
                        'with_embargo': False,
                        'embargo_length': 0,
                        'license': 'CC BY 4.0',
                        'processing_failed': 0,
                        'processing_successful': 6,
                        'n_entries': 6,
                        'upload_files_server_path': 'id_published_with_ref',
                    }
                },
            },
        },
    )

    __fs_print(
        'go to selected entry then to upload, skipping file info',
        {
            'm_request': {
                'directive': 'plain',
            },
            'mainfile_for_id_01': {
                Token.ENTRY: {
                    'upload_id': {
                        'm_request': {
                            'directive': 'resolved',
                            'resolve_type': 'upload',
                        },
                    }
                }
            },
        },
        result={
            'm_is': 'Directory',
            '1.aux': {'path': '1.aux', 'm_is': 'File', 'size': 8},
            '2.aux': {'path': '2.aux', 'm_is': 'File', 'size': 8},
            '3.aux': {'path': '3.aux', 'm_is': 'File', 'size': 8},
            '4.aux': {'path': '4.aux', 'm_is': 'File', 'size': 8},
            'mainfile_for_id_02': {
                'path': 'mainfile_for_id_02',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_03': {
                'path': 'mainfile_for_id_03',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_04': {
                'path': 'mainfile_for_id_04',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_05': {
                'path': 'mainfile_for_id_05',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_06': {
                'path': 'mainfile_for_id_06',
                'm_is': 'File',
                'size': 3227,
            },
            'mainfile_for_id_01': {
                'path': 'mainfile_for_id_01',
                'm_is': 'File',
                'size': 3227,
                Token.ENTRY: {
                    'upload_id': {
                        'process_running': False,
                        'current_process': 'process_upload',
                        'process_status': 'SUCCESS',
                        'last_status_message': None,
                        'errors': [],
                        'warnings': [],
                        'complete_time': '2023-03-05T22:16:52.436000',
                        'upload_id': 'id_published_with_ref',
                        'upload_name': 'name_published',
                        'upload_create_time': '2023-03-05T22:16:52.435000',
                        'description': 'Test Description',
                        'doi': None,
                        'main_author': user1_dict,
                        'coauthors': [],
                        'reviewers': [],
                        'coauthor_groups': [],
                        'reviewer_groups': [],
                        'writers': [user1_dict],
                        'viewers': [user1_dict],
                        'writer_groups': [],
                        'viewer_groups': [],
                        'published': False,
                        'published_to': [],
                        'publish_time': None,
                        'with_embargo': False,
                        'embargo_length': 0,
                        'license': 'CC BY 4.0',
                        'n_entries': 6,
                        'processing_failed': 0,
                        'processing_successful': 6,
                        'upload_files_server_path': 'id_published_with_ref',
                    }
                },
            },
        },
    )

    __fs_print(
        'go to selected entry then to upload, resolve user',
        {'mainfile_for_id_01': {Token.ENTRY: {'upload_id': {'main_author': '*'}}}},
        result={
            'm_is': 'Directory',
            'mainfile_for_id_01': {
                Token.ENTRY: {
                    'upload_id': {
                        'main_author': {
                            'name': 'Sheldon Cooper',
                            'first_name': 'Sheldon',
                            'last_name': 'Cooper',
                            'email': 'sheldon.cooper@nomad-coe.eu',
                            'user_id': '00000000-0000-0000-0000-000000000001',
                            'username': 'scooper',
                            'is_admin': False,
                            'is_oasis_admin': True,
                        }
                    }
                }
            },
        },
    )


# noinspection DuplicatedCode,SpellCheckingInspection
def test_group_reader(groups_function, user1):
    def __ge_print(msg, required, *, to_file: bool = False, result: dict | None = None):
        with MongoReader(required, user=user1) as reader:
            if result:
                assert_dict(reader.sync_read(), result)
            else:
                rprint(f'\n\nExample: {next(counter)} -> {msg}:')
                rprint(required)
                if not to_file:
                    rprint('output:')
                    rprint(reader.sync_read())
                else:
                    with open('archive_reader_test.json', 'w') as f:
                        f.write(json.dumps(reader.sync_read()))

    __ge_print(
        'general start from group; id: *',
        {
            Token.GROUP: {
                'GGGGGGGGGGGGGGGGGG12m3': '*',
            }
        },
        result={
            'group': {
                'GGGGGGGGGGGGGGGGGG12m3': {
                    'group_id': 'GGGGGGGGGGGGGGGGGG12m3',
                    'group_name': 'Group 12m3',
                    'owner': {
                        'name': 'Sheldon Cooper',
                        'first_name': 'Sheldon',
                        'last_name': 'Cooper',
                        'email': 'sheldon.cooper@nomad-coe.eu',
                        'user_id': '00000000-0000-0000-0000-000000000001',
                        'username': 'scooper',
                        'is_admin': False,
                        'is_oasis_admin': True,
                    },
                    'members': ListWithSortKey(
                        (
                            {
                                'name': 'Sheldon Cooper',
                                'first_name': 'Sheldon',
                                'last_name': 'Cooper',
                                'email': 'sheldon.cooper@nomad-coe.eu',
                                'user_id': '00000000-0000-0000-0000-000000000001',
                                'username': 'scooper',
                                'is_admin': False,
                                'is_oasis_admin': True,
                            },
                            {
                                'name': 'Leonard Hofstadter',
                                'first_name': 'Leonard',
                                'last_name': 'Hofstadter',
                                'email': 'leonard.hofstadter@nomad-fairdi.tests.de',
                                'user_id': '00000000-0000-0000-0000-000000000002',
                                'username': 'lhofstadter',
                                'is_admin': False,
                            },
                            {
                                'name': 'Howard Wolowitz',
                                'first_name': 'Howard',
                                'last_name': 'Wolowitz',
                                'email': 'howard.wolowitz@nomad-fairdi.tests.de',
                                'user_id': '00000000-0000-0000-0000-000000000003',
                                'username': 'hwolowitz',
                                'is_admin': False,
                            },
                        ),
                        sort_key=lambda x: x['user_id'],
                    ),
                    'members_info': ListWithSortKey(
                        (
                            {
                                'user_id': '00000000-0000-0000-0000-000000000001',
                                'user': {
                                    'name': 'Sheldon Cooper',
                                    'first_name': 'Sheldon',
                                    'last_name': 'Cooper',
                                    'email': 'sheldon.cooper@nomad-coe.eu',
                                    'user_id': '00000000-0000-0000-0000-000000000001',
                                    'username': 'scooper',
                                    'is_admin': False,
                                    'is_oasis_admin': True,
                                },
                                'role': 'owner',
                            },
                            {
                                'user_id': '00000000-0000-0000-0000-000000000002',
                                'user': {
                                    'name': 'Leonard Hofstadter',
                                    'first_name': 'Leonard',
                                    'last_name': 'Hofstadter',
                                    'email': 'leonard.hofstadter@nomad-fairdi.tests.de',
                                    'user_id': '00000000-0000-0000-0000-000000000002',
                                    'username': 'lhofstadter',
                                    'is_admin': False,
                                },
                                'role': 'maintainer',
                            },
                            {
                                'user_id': '00000000-0000-0000-0000-000000000003',
                                'user': {
                                    'name': 'Howard Wolowitz',
                                    'first_name': 'Howard',
                                    'last_name': 'Wolowitz',
                                    'email': 'howard.wolowitz@nomad-fairdi.tests.de',
                                    'user_id': '00000000-0000-0000-0000-000000000003',
                                    'username': 'hwolowitz',
                                    'is_admin': False,
                                },
                                'role': 'member',
                            },
                        ),
                        sort_key=lambda x: x['user_id'],
                    ),
                }
            }
        },
    )
    __ge_print(
        'general start from group; id: owner.email',
        {
            Token.GROUP: {
                'GGGGGGGGGGGGGGGGGGGG14': {
                    'owner': {
                        'email': '*',
                    },
                },
            }
        },
        result={
            'group': {
                'GGGGGGGGGGGGGGGGGGGG14': {
                    'owner': {'email': 'sheldon.cooper@nomad-coe.eu'}
                }
            }
        },
    )
    __ge_print(
        'general start from group; *: group_name',
        {
            Token.GROUP: {
                'm_request': {
                    'pagination': {'page_size': 20},
                },
                '*': {'group_name': '*'},
            }
        },
        result={
            'group': {
                'GGGGGGGGGGGGGGGGGGGGG0': {'group_name': 'Group 0'},
                'GGGGGGGGGGGGGGGGGGGGG1': {'group_name': 'Group 1'},
                'GGGGGGGGGGGGGGGGGGGGG2': {'group_name': 'Group 2'},
                'GGGGGGGGGGGGGGGGGGGGG3': {'group_name': 'Group 3'},
                'GGGGGGGGGGGGGGGGGGGGG6': {'group_name': 'Group 6'},
                'GGGGGGGGGGGGGGGGGGGGG8': {'group_name': 'Group 8'},
                'GGGGGGGGGGGGGGGGGGGGG9': {'group_name': 'Group 9'},
                'GGGGGGGGGGGGGGGGGGGG14': {'group_name': 'Group 14'},
                'GGGGGGGGGGGGGGGGGGGG15': {'group_name': 'Group 15'},
                'GGGGGGGGGGGGGGGGGGGG18': {'group_name': 'Group 18'},
                'GGGGGGGGGGGGGGGGGGGG19': {'group_name': 'Group 19'},
                'GGGGGGGGGGGGGGGGGGG123': {'group_name': 'Group 123'},
                'GGGGGGGGGGGGGGGGGG12m3': {'group_name': 'Group 12m3'},
                'GGGGGGGGGGGGGGGGGGUniq': {'group_name': 'Group Uniq'},
                'GGGGGGGGGGGGGGTwin One': {'group_name': 'Group Twin One'},
                'GGGGGGGGGGGGGGTwin Two': {'group_name': 'Group Twin Two'},
                'GGGGGGGGGGGGGGdirty234': {'group_name': 'Group Dirty 234'},
                'GGGGGGGGGOne Two Three': {'group_name': 'Group One Two Three'},
            }
        },
    )
    __ge_print(
        'general start from group; query: [group_id]; *: group_name',
        {
            Token.GROUP: {
                'm_request': {'query': {'group_id': ['GGGGGGGGGGGGGGGGGGG123']}},
                '*': {'group_name': '*'},
            }
        },
        result={
            'group': {
                'GGGGGGGGGGGGGGGGGGG123': {'group_name': 'Group 123'},
            }
        },
    )
    __ge_print(
        'general start from group; query: user_id; *: group_name',
        {
            Token.GROUP: {
                'm_request': {
                    'query': {'user_id': '00000000-0000-0000-0000-000000000008'}
                },
                '*': {'group_name': '*'},
            }
        },
        result={
            'group': {
                'GGGGGGGGGGGGGGGGGGGGG8': {'group_name': 'Group 8'},
                'GGGGGGGGGGGGGGGGGGGG18': {'group_name': 'Group 18'},
            }
        },
    )
    __ge_print(
        'general start from group; query: search_terms; *: group_name',
        {
            Token.GROUP: {
                'm_request': {'query': {'search_terms': 'win'}},
                '*': {'group_name': '*'},
            }
        },
        result={
            'group': {
                'GGGGGGGGGGGGGGTwin One': {'group_name': 'Group Twin One'},
                'GGGGGGGGGGGGGGTwin Two': {'group_name': 'Group Twin Two'},
            }
        },
    )
    __ge_print(
        'general start from group; query: user_id, search_terms; *: group_name',
        {
            Token.GROUP: {
                'm_request': {
                    'query': {
                        'user_id': '00000000-0000-0000-0000-000000000008',
                        'search_terms': '1',
                    }
                },
                '*': {'group_name': '*'},
            }
        },
        result={
            'group': {
                'GGGGGGGGGGGGGGGGGGGG18': {'group_name': 'Group 18'},
            }
        },
    )


# noinspection DuplicatedCode,SpellCheckingInspection
def test_general_reader(json_dict, example_data_with_reference, user1):
    def __ge_print(msg, required, *, to_file: bool = False, result: dict | None = None):
        with MongoReader(required, user=user1) as reader:
            if result:
                assert_dict(reader.sync_read(), result)
            else:
                rprint(f'\n\nExample: {next(counter)} -> {msg}:')
                rprint(required)
                if not to_file:
                    rprint('output:')
                    rprint(reader.sync_read())
                else:
                    with open('archive_reader_test.json', 'w') as f:
                        f.write(json.dumps(reader.sync_read()))

    __ge_print(
        'general start from entry',
        {
            Token.ENTRIES: {
                'm_request': {
                    'directive': 'resolved',
                    'resolve_type': 'entry',
                    'pagination': {'page_size': 2, 'page_after_value': 'id_03'},
                },
            }
        },
        result={
            Token.ENTRIES: {
                'id_04': {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_04',
                    'entry_create_time': '2023-03-05T22:29:55.842000',
                    'mainfile_path': 'mainfile_for_id_04',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
                'id_05': {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_05',
                    'entry_create_time': '2023-03-05T22:29:55.843000',
                    'mainfile_path': 'mainfile_for_id_05',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
            }
        },
    )
    __ge_print(
        'general start from entry with wildcard',
        {
            Token.ENTRIES: {
                'id_01': {
                    'm_request': {
                        'directive': 'plain',
                    },
                },
                '*': {
                    'm_request': {
                        'directive': 'plain',
                        'include': ['entry_id', 'mainfile_path'],
                    },
                    'upload_id': '*',
                },
            }
        },
        result={
            Token.ENTRIES: {
                'id_01': {
                    'process_running': False,
                    'current_process': None,
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': None,
                    'entry_id': 'id_01',
                    'entry_create_time': '2023-03-05T22:20:46.586000',
                    'mainfile_path': 'mainfile_for_id_01',
                    'mainfile_key': None,
                    'upload_id': 'id_published_with_ref',
                    'parser_name': 'parsers/vasp',
                },
                'id_02': {
                    'entry_id': 'id_02',
                    'mainfile_path': 'mainfile_for_id_02',
                    'upload_id': 'id_published_with_ref',
                },
                'id_03': {
                    'entry_id': 'id_03',
                    'mainfile_path': 'mainfile_for_id_03',
                    'upload_id': 'id_published_with_ref',
                },
                'id_04': {
                    'entry_id': 'id_04',
                    'mainfile_path': 'mainfile_for_id_04',
                    'upload_id': 'id_published_with_ref',
                },
                'id_05': {
                    'entry_id': 'id_05',
                    'mainfile_path': 'mainfile_for_id_05',
                    'upload_id': 'id_published_with_ref',
                },
                'id_06': {
                    'entry_id': 'id_06',
                    'mainfile_path': 'mainfile_for_id_06',
                    'upload_id': 'id_published_with_ref',
                },
            }
        },
    )
    __ge_print(
        'general start from entry to metadata',
        {
            Token.ENTRIES: {
                'id_01': {
                    'metadata': {
                        'results': {
                            'm_request': {
                                'directive': 'plain',
                            }
                        }
                    }
                }
            }
        },
        result={
            'entries': {
                'id_01': {
                    'metadata': {
                        'results': {
                            'material': {
                                'dimensionality': '3D',
                                'symmetry': {'crystal_system': 'cubic'},
                                'elements': ['H', 'O'],
                                'elements_exclusive': 'H O',
                                'material_id': 'test_material_id',
                                'structural_type': 'not processed',
                                'n_elements': 2,
                            },
                            'method': {
                                'simulation': {
                                    'program_version': 'not processed',
                                    'program_version_internal': 'not processed',
                                    'dft': {
                                        'basis_set_type': 'unavailable',
                                        'core_electron_treatment': 'unavailable',
                                        'xc_functional_type': 'GGA',
                                        'xc_functional_names': [],
                                        'jacobs_ladder': 'not processed',
                                    },
                                    'program_name': 'VASP',
                                }
                            },
                            'properties': {
                                'available_properties': ['dos_electronic'],
                                'n_calculations': 1,
                                'electronic': {
                                    'dos_electronic': [
                                        {
                                            'spin_polarized': False,
                                            'band_gap': [{'type': 'indirect'}],
                                        }
                                    ]
                                },
                            },
                        }
                    }
                }
            }
        },
    )
    # only check if those keys work
    # the result order is not checked
    for order_by in ['entry_create_time', 'mainfile_path']:
        __ge_print(
            'general start from entry WITHOUT retrieval of metadata (just listing)',
            {
                Token.SEARCH: {
                    'm_request': {
                        'directive': 'plain',
                        'pagination': {'page_size': 2, 'page': 2, 'order_by': order_by},
                        'query': {'owner': 'user'},
                    },
                }
            },
            result={
                Token.SEARCH: {
                    'id_03': 'id_03',
                    'id_04': 'id_04',
                }
            },
        )
    __ge_print(
        'general start from upload',
        {
            Token.UPLOADS: {
                'm_request': {
                    'directive': 'resolved',
                    'resolve_type': 'upload',
                    'pagination': {
                        'page_size': 10,
                    },
                },
            }
        },
        result={
            Token.UPLOADS: {
                'id_published_with_ref': {
                    'process_running': False,
                    'current_process': 'process_upload',
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': '2023-03-05T22:20:46.584000',
                    'upload_id': 'id_published_with_ref',
                    'upload_name': 'name_published',
                    'upload_create_time': '2023-03-05T22:20:46.583000',
                    'description': 'Test Description',
                    'doi': None,
                    'main_author': user1_dict,
                    'coauthors': [],
                    'reviewers': [],
                    'coauthor_groups': [],
                    'reviewer_groups': [],
                    'writers': [user1_dict],
                    'viewers': [user1_dict],
                    'writer_groups': [],
                    'viewer_groups': [],
                    'published': False,
                    'published_to': [],
                    'publish_time': None,
                    'with_embargo': False,
                    'embargo_length': 0,
                    'license': 'CC BY 4.0',
                    'n_entries': 6,
                    'processing_failed': 0,
                    'processing_successful': 6,
                    'upload_files_server_path': 'id_published_with_ref',
                },
            }
        },
    )
    # only check if those keys work
    # the result order is not checked
    for order_by in ['entry_create_time', 'mainfile_path']:
        __ge_print(
            'general start from entry with query and pagination',
            {
                Token.ENTRIES: {
                    'm_request': {
                        'directive': 'plain',
                        'pagination': {'page_size': 10, 'order_by': order_by},
                    },
                }
            },
            result={
                'entries': {
                    'id_01': 'id_01',
                    'id_02': 'id_02',
                    'id_03': 'id_03',
                    'id_04': 'id_04',
                    'id_05': 'id_05',
                    'id_06': 'id_06',
                }
            },
        )
    __ge_print(
        'general start from upload with query and pagination',
        {
            Token.UPLOADS: {
                'm_request': {
                    'directive': 'resolved',
                    'resolve_type': 'upload',
                    'pagination': {'page_size': 10, 'order_by': 'upload_create_time'},
                    'query': {'is_processing': False},
                },
            }
        },
        result={
            Token.UPLOADS: {
                'id_published_with_ref': {
                    'process_running': False,
                    'current_process': 'process_upload',
                    'process_status': 'SUCCESS',
                    'last_status_message': None,
                    'errors': [],
                    'warnings': [],
                    'complete_time': '2023-03-05T22:20:46.584000',
                    'upload_id': 'id_published_with_ref',
                    'upload_name': 'name_published',
                    'upload_create_time': '2023-03-05T22:20:46.583000',
                    'description': 'Test Description',
                    'doi': None,
                    'main_author': user1_dict,
                    'coauthors': [],
                    'reviewers': [],
                    'coauthor_groups': [],
                    'reviewer_groups': [],
                    'writers': [user1_dict],
                    'viewers': [user1_dict],
                    'writer_groups': [],
                    'viewer_groups': [],
                    'published': False,
                    'published_to': [],
                    'publish_time': None,
                    'with_embargo': False,
                    'embargo_length': 0,
                    'license': 'CC BY 4.0',
                    'n_entries': 6,
                    'processing_failed': 0,
                    'processing_successful': 6,
                    'upload_files_server_path': 'id_published_with_ref',
                },
            }
        },
    )
    __ge_print(
        'general start from user, does NOT perform search from security',
        {
            Token.USER: {
                'me': {
                    'm_request': {'directive': 'plain'},
                }
            }
        },
        result={
            Token.USER: {
                'me': {
                    'name': 'Sheldon Cooper',
                    'first_name': 'Sheldon',
                    'last_name': 'Cooper',
                    'email': 'sheldon.cooper@nomad-coe.eu',
                    'user_id': '00000000-0000-0000-0000-000000000001',
                    'username': 'scooper',
                    'is_admin': False,
                    'is_oasis_admin': True,
                }
            }
        },
    )
    __ge_print(
        'general start from me, with its user id like fields resolved',
        {
            Token.USER: {
                'me': {
                    'm_request': {'directive': 'resolved', 'resolve_type': 'user'},
                }
            }
        },
        result={
            Token.USER: {
                'me': {
                    'name': 'Sheldon Cooper',
                    'first_name': 'Sheldon',
                    'last_name': 'Cooper',
                    'email': 'sheldon.cooper@nomad-coe.eu',
                    'user_id': {
                        'name': 'Sheldon Cooper',
                        'first_name': 'Sheldon',
                        'last_name': 'Cooper',
                        'email': 'sheldon.cooper@nomad-coe.eu',
                        'user_id': '00000000-0000-0000-0000-000000000001',
                        'username': 'scooper',
                        'is_admin': False,
                        'is_oasis_admin': True,
                    },
                    'username': 'scooper',
                    'is_admin': False,
                    'is_oasis_admin': True,
                }
            }
        },
    )


# noinspection DuplicatedCode,SpellCheckingInspection
def test_metainfo_reader(mongo_function_with_indexed_def, user1):
    def __ge_print(msg, required, *, to_file: bool = False, result: dict | None = None):
        with MongoReader(required, user=user1) as reader:
            if result:
                assert_dict(reader.sync_read(), result)
            else:
                rprint(f'\n\nExample: {next(counter)} -> {msg}:')
                rprint(required)
                if not to_file:
                    rprint('output:')
                    rprint(reader.sync_read())
                else:
                    with open('archive_reader_test.json', 'w') as f:
                        f.write(json.dumps(reader.sync_read()))

    __ge_print(
        'general start from metainfo',
        {
            Token.METAINFO: {
                'nomad.datamodel.metainfo.simulation.run': {
                    'section_definitions[2]': {
                        'm_request': {'directive': 'plain'},
                    }
                }
            }
        },
        result={
            'metainfo': {
                'nomad.datamodel.metainfo.simulation.run': {
                    'section_definitions': [
                        None,
                        None,
                        {
                            'name': 'MessageRun',
                            'description': 'Contains warning, error, and info messages of the run.',
                            'quantities': [
                                {
                                    'name': 'type',
                                    'description': 'Type of the message. Can be one of warning, error, info, debug.',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'shape': [],
                                },
                                {
                                    'name': 'value',
                                    'description': 'Value of the message of the computational program, given by type.',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'shape': [],
                                },
                            ],
                        },
                    ]
                }
            },
        },
    )

    __ge_print(
        'general start from metainfo definition id',
        {
            Token.METAINFO: {
                run.m_package.definition_id: {
                    'section_definitions[2]': {
                        'm_request': {'directive': 'plain'},
                    }
                }
            }
        },
        result={
            'metainfo': {
                'nomad.datamodel.metainfo.simulation.run': {
                    'section_definitions': [
                        None,
                        None,
                        {
                            'name': 'MessageRun',
                            'description': 'Contains warning, error, and info messages of the run.',
                            'quantities': [
                                {
                                    'name': 'type',
                                    'description': 'Type of the message. Can be one of warning, error, info, debug.',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'shape': [],
                                },
                                {
                                    'name': 'value',
                                    'description': 'Value of the message of the computational program, given by type.',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'shape': [],
                                },
                            ],
                        },
                    ]
                }
            },
        },
    )

    __ge_print(
        'general start from metainfo',
        {
            Token.METAINFO: {
                'm_request': {
                    'include': ['*nomad.datamodel.metainfo.simulation.run'],
                    'pagination': {'page_size': 500},
                },
                '*': {'m_request': {'index': [2]}},
            }
        },
        result={
            'metainfo': {
                'nomad.datamodel.metainfo.simulation.run': {
                    'name': 'nomad.datamodel.metainfo.simulation.run',
                    'section_definitions': [
                        {
                            'name': 'Program',
                            'description': 'Contains the specifications of the program.',
                            'quantities': [
                                {
                                    'name': 'name',
                                    'description': 'Specifies the name of the program that generated the data.',
                                    'categories': [
                                        '/category_definitions/0',
                                        '/category_definitions/1',
                                    ],
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'shape': [],
                                    'definition_id': '96c065737139d152724f00732f72cac3c4773c9b',
                                },
                                {
                                    'name': 'version',
                                    'description': 'Specifies the official release version of the program that was used.',
                                    'categories': [
                                        '/category_definitions/0',
                                        '/category_definitions/1',
                                    ],
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'shape': [],
                                    'definition_id': 'dfe002f69c3a7aeab19379ccedcad666410e7eef',
                                },
                                {
                                    'name': 'version_internal',
                                    'description': 'Specifies a program version tag used internally for development purposes.\nAny kind of tagging system is supported, including git commit hashes.',
                                    'categories': ['/category_definitions/1'],
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'definition_id': 'd4aa1db63fec24fc47c88c99cf35d9025fb36be3',
                                },
                                {
                                    'name': 'compilation_datetime',
                                    'description': 'Contains the program compilation date and time from *Unix epoch* (00:00:00 UTC on\n1 January 1970) in seconds. For date and times without a timezone, the default\ntimezone GMT is used.',
                                    'categories': [
                                        '/category_definitions/0',
                                        '/category_definitions/1',
                                    ],
                                    'type': {
                                        'type_kind': 'numpy',
                                        'type_data': 'float64',
                                    },
                                    'shape': [],
                                    'unit': 'second',
                                    'definition_id': 'aa94ca3c39629d1432c0e83a33b4c6e27b975c7c',
                                },
                                {
                                    'name': 'compilation_host',
                                    'description': 'Specifies the host on which the program was compiled.',
                                    'categories': [
                                        '/category_definitions/0',
                                        '/category_definitions/1',
                                    ],
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'shape': [],
                                    'definition_id': '657170b0a288ca9d26b3ad170dd2ac28d817ba8b',
                                },
                            ],
                            'definition_id': '86451301befbcfc7e550518314b70839d693aa93',
                        },
                        {
                            'name': 'TimeRun',
                            'description': 'Contains information on timing information of the run.',
                            'quantities': [
                                {
                                    'name': 'date_end',
                                    'description': 'Stores the end date of the run as time since the *Unix epoch* (00:00:00 UTC on 1\nJanuary 1970) in seconds. For date and times without a timezone, the default\ntimezone GMT is used.',
                                    'type': {
                                        'type_kind': 'numpy',
                                        'type_data': 'float64',
                                    },
                                    'shape': [],
                                    'unit': 'second',
                                    'definition_id': '2c1e8d38838ca62812c5ddb5bed6d6a78cb929d7',
                                },
                                {
                                    'name': 'date_start',
                                    'description': 'Stores the start date of the run as time since the *Unix epoch* (00:00:00 UTC on 1\nJanuary 1970) in seconds. For date and times without a timezone, the default\ntimezone GMT is used.',
                                    'type': {
                                        'type_kind': 'numpy',
                                        'type_data': 'float64',
                                    },
                                    'shape': [],
                                    'unit': 'second',
                                    'definition_id': 'ab74cf67ac1737ec08b9f9d7bfbf0898df4219eb',
                                },
                                {
                                    'name': 'cpu1_end',
                                    'description': 'Stores the end time of the run on CPU 1.',
                                    'type': {
                                        'type_kind': 'numpy',
                                        'type_data': 'float64',
                                    },
                                    'shape': [],
                                    'unit': 'second',
                                    'definition_id': '3844d7bc9f216f424803afea9329d20371e5edcc',
                                },
                                {
                                    'name': 'cpu1_start',
                                    'description': 'Stores the start time of the run on CPU 1.',
                                    'type': {
                                        'type_kind': 'numpy',
                                        'type_data': 'float64',
                                    },
                                    'shape': [],
                                    'unit': 'second',
                                    'definition_id': 'ce7522071075ddd1fb2c82b7a18960b3d49ede4b',
                                },
                                {
                                    'name': 'wall_end',
                                    'description': 'Stores the internal wall-clock time at the end of the run.',
                                    'type': {
                                        'type_kind': 'numpy',
                                        'type_data': 'float64',
                                    },
                                    'shape': [],
                                    'unit': 'second',
                                    'definition_id': '84462f4f10bdd7ab416e14df678eb2443b953832',
                                },
                                {
                                    'name': 'wall_start',
                                    'description': 'Stores the internal wall-clock time from the start of the run.',
                                    'type': {
                                        'type_kind': 'numpy',
                                        'type_data': 'float64',
                                    },
                                    'shape': [],
                                    'unit': 'second',
                                    'definition_id': '64647f7ffb60b22239918817de1885da6a297de3',
                                },
                            ],
                            'definition_id': 'b4e272bb4d048593e22a049eff27a9fa78f10034',
                        },
                        {
                            'name': 'MessageRun',
                            'description': 'Contains warning, error, and info messages of the run.',
                            'quantities': [
                                {
                                    'name': 'type',
                                    'description': 'Type of the message. Can be one of warning, error, info, debug.',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'shape': [],
                                    'definition_id': 'e6b4591b647cbbc16f986a07bf580c6648078d63',
                                },
                                {
                                    'name': 'value',
                                    'description': 'Value of the message of the computational program, given by type.',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'shape': [],
                                    'definition_id': '2fa8e629e8c46a75ec2cce66736f405ee50f36b7',
                                },
                            ],
                            'definition_id': '24fdb21e0f62a0e50ba36a32efc0663a092f1f29',
                        },
                        {
                            'name': 'Run',
                            'description': 'Every section run represents a single call of a program.',
                            'base_sections': [
                                'metainfo/nomad.datamodel.data/section_definitions/0'
                            ],
                            'quantities': [
                                {
                                    'name': 'calculation_file_uri',
                                    'description': 'Contains the nomad uri of a raw the data file connected to the current run. There\nshould be an value for the main_file_uri and all ancillary files.',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'shape': [],
                                    'definition_id': 'fd75dc888698e657233bfbf264ee419e7d318ea7',
                                },
                                {
                                    'name': 'clean_end',
                                    'description': 'Indicates whether this run terminated properly (true), or if it was killed or\nexited with an error code unequal to zero (false).',
                                    'type': {
                                        'type_kind': 'python',
                                        'type_data': 'bool',
                                    },
                                    'shape': [],
                                    'definition_id': '409e1cd4095b6ffb1475cb6832ad55669493fdb2',
                                },
                                {
                                    'name': 'raw_id',
                                    'description': 'An optional calculation id, if one is found in the code input/output files.',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'shape': [],
                                    'definition_id': '691c2a29ff5c16fe1f6738cae7c616407c8f4949',
                                },
                                {
                                    'name': 'starting_run_ref',
                                    'description': 'Links the current section run to a section run containing the calculations from\nwhich the current section starts.',
                                    'categories': ['/category_definitions/0'],
                                    'type': {
                                        'type_kind': 'reference',
                                        'type_data': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/3',
                                    },
                                    'shape': [],
                                    'definition_id': 'df9b71faa553729b77bea7ee296c1943b6a8a5f9',
                                },
                                {
                                    'name': 'n_references',
                                    'description': 'Number of references to the current section calculation.',
                                    'type': {
                                        'type_kind': 'numpy',
                                        'type_data': 'int32',
                                    },
                                    'shape': [],
                                    'definition_id': '01e5776ba5702e1a7a3f408db385bf15c795dec5',
                                },
                                {
                                    'name': 'runs_ref',
                                    'description': 'Links the the current section to other run sections. Such a link is necessary for\nexample for workflows that may contain a series of runs.',
                                    'categories': ['/category_definitions/0'],
                                    'type': {
                                        'type_kind': 'reference',
                                        'type_data': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/3',
                                    },
                                    'shape': ['n_references'],
                                    'definition_id': '18591ca4c43a4dbbce7e90c2c55c3569631df480',
                                },
                            ],
                            'sub_sections': [
                                {
                                    'name': 'program',
                                    'sub_section': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/0',
                                    'definition_id': 'd0cd1b46d4865b30c35291883f09b4d304363cc2',
                                },
                                {
                                    'name': 'time_run',
                                    'sub_section': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/1',
                                    'definition_id': '4b79ae7d5e1c35a034dfc06dfaf084a7fa4ce3b5',
                                },
                                {
                                    'name': 'message',
                                    'sub_section': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/2',
                                    'definition_id': 'a82d079d0aea175d37e9c7052ef7202bfae5a5d8',
                                },
                                {
                                    'name': 'method',
                                    'sub_section': 'metainfo/nomad.datamodel.metainfo.simulation.method/section_definitions/44',
                                    'repeats': True,
                                    'definition_id': 'a2ce15fe5b72f715b039160a789dbb380841a56d',
                                },
                                {
                                    'name': 'system',
                                    'sub_section': 'metainfo/nomad.datamodel.metainfo.simulation.system/section_definitions/8',
                                    'repeats': True,
                                    'definition_id': 'de67ee488cf87484391f229ec1ad1931bb74aec5',
                                },
                                {
                                    'name': 'calculation',
                                    'sub_section': 'metainfo/nomad.datamodel.metainfo.simulation.calculation/section_definitions/36',
                                    'repeats': True,
                                    'definition_id': '2f6e767dde313f0ae66851a6fd576488da296fc0',
                                },
                            ],
                            'definition_id': 'a675306701ab58fcf997a932d70016b76473d349',
                        },
                    ],
                    'category_definitions': [
                        {
                            'name': 'AccessoryInfo',
                            'description': 'Information that *in theory* should not affect the results of the calculations (e.g.,\ntiming).',
                            'definition_id': 'be10cbedeeab1e6f3c79d1a4f6b3f9bc6c369ef6',
                        },
                        {
                            'name': 'ProgramInfo',
                            'description': 'Contains information on the program that generated the data, i.e. the program_name,\nprogram_version, program_compilation_host and program_compilation_datetime as direct\nchildren of this field.',
                            'categories': ['/category_definitions/0'],
                            'definition_id': 'e726f44543844b80e229df453338e64174e1de39',
                        },
                    ],
                    'definition_id': '7052ced4bd81d4f3f9886e73439b8798bc2b283f',
                    'all_quantities': {
                        'Program.name': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/0/quantities/0',
                        'Program.version': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/0/quantities/1',
                        'Program.version_internal': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/0/quantities/2',
                        'Program.compilation_datetime': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/0/quantities/3',
                        'Program.compilation_host': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/0/quantities/4',
                        'TimeRun.date_end': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/1/quantities/0',
                        'TimeRun.date_start': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/1/quantities/1',
                        'TimeRun.cpu1_end': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/1/quantities/2',
                        'TimeRun.cpu1_start': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/1/quantities/3',
                        'TimeRun.wall_end': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/1/quantities/4',
                        'TimeRun.wall_start': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/1/quantities/5',
                        'MessageRun.type': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/2/quantities/0',
                        'MessageRun.value': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/2/quantities/1',
                        'Run.calculation_file_uri': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/3/quantities/0',
                        'Run.clean_end': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/3/quantities/1',
                        'Run.raw_id': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/3/quantities/2',
                        'Run.starting_run_ref': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/3/quantities/3',
                        'Run.n_references': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/3/quantities/4',
                        'Run.runs_ref': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/3/quantities/5',
                    },
                    'all_sub_sections': {
                        'Calculation': 'metainfo/nomad.datamodel.metainfo.simulation.calculation/section_definitions/36',
                        'Method': 'metainfo/nomad.datamodel.metainfo.simulation.method/section_definitions/44',
                        'Program': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/0',
                        'TimeRun': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/1',
                        'MessageRun': 'metainfo/nomad.datamodel.metainfo.simulation.run/section_definitions/2',
                        'System': 'metainfo/nomad.datamodel.metainfo.simulation.system/section_definitions/8',
                    },
                    'all_base_sections': {
                        'ArchiveSection': 'metainfo/nomad.datamodel.data/section_definitions/0'
                    },
                }
            },
        },
    )

    __ge_print(
        'general start from metainfo',
        {
            Token.METAINFO: {
                'm_request': {
                    'include': ['*tabular'],
                    'pagination': {'page_size': 500},
                }
            }
        },
        result={
            'metainfo': {
                'nomad.parsing.tabular': {
                    'name': 'nomad.parsing.tabular',
                    'section_definitions': [
                        {
                            'name': 'TableData',
                            'description': 'Table data',
                            'base_sections': [
                                'metainfo/nomad.datamodel.data/section_definitions/0'
                            ],
                            'quantities': [
                                {
                                    'm_annotations': {
                                        'eln': [{'component': 'BoolEditQuantity'}]
                                    },
                                    'name': 'fill_archive_from_datafile',
                                    'description': 'While checked, it allows the parser to fill all the Quantities from the data file.\nBe cautious though! as checking this box will cause overwriting your fields with data parsed from the data file',
                                    'type': {
                                        'type_kind': 'python',
                                        'type_data': 'bool',
                                    },
                                    'default': True,
                                    'definition_id': '407156f00219b56a347be07e11cca722381693a5',
                                }
                            ],
                            'definition_id': '01444576adb49e3e8f9228c5db694f3194c4228e',
                        }
                    ],
                    'definition_id': 'c2d1ed505653e17dab8ee7e41608aaedab63211d',
                    'all_quantities': {
                        'TableData.fill_archive_from_datafile': 'metainfo/nomad.parsing.tabular/section_definitions/0/quantities/0'
                    },
                    'all_sub_sections': {},
                    'all_base_sections': {
                        'ArchiveSection': 'metainfo/nomad.datamodel.data/section_definitions/0'
                    },
                }
            },
        },
    )


# noinspection DuplicatedCode,SpellCheckingInspection
def test_general_reader_search(json_dict, example_data_with_reference, user1):
    def __ge_print(msg, required, *, to_file: bool = False, result: dict | None = None):
        with MongoReader(required, user=user1) as reader:
            if result:
                assert_dict(reader.sync_read(), result)
            else:
                rprint(f'\n\nExample: {next(counter)} -> {msg}:')
                rprint(required)
                if not to_file:
                    rprint('output:')
                    rprint(reader.sync_read())
                else:
                    with open('archive_reader_test.json', 'w') as f:
                        f.write(json.dumps(reader.sync_read()))

    __ge_print(
        'general start from elastic search',
        {
            Token.SEARCH: {
                'm_request': {'query': {}, 'exclude': ['*']},
                'id_01': {Token.ENTRIES: {'mainfile': {'..': '*'}}},
            }
        },
        result={
            'search': {
                'id_01': {
                    'entries': {
                        'mainfile': {
                            'mainfile_for_id_01': {
                                '..': {
                                    'm_is': 'Directory',
                                    '1.aux': {
                                        'm_is': 'File',
                                        'path': '1.aux',
                                        'size': 8,
                                    },
                                    '2.aux': {
                                        'm_is': 'File',
                                        'path': '2.aux',
                                        'size': 8,
                                    },
                                    '3.aux': {
                                        'm_is': 'File',
                                        'path': '3.aux',
                                        'size': 8,
                                    },
                                    '4.aux': {
                                        'm_is': 'File',
                                        'path': '4.aux',
                                        'size': 8,
                                    },
                                    'mainfile_for_id_01': {
                                        'm_is': 'File',
                                        'path': 'mainfile_for_id_01',
                                        'size': 3227,
                                    },
                                    'mainfile_for_id_02': {
                                        'm_is': 'File',
                                        'path': 'mainfile_for_id_02',
                                        'size': 3227,
                                    },
                                    'mainfile_for_id_03': {
                                        'm_is': 'File',
                                        'path': 'mainfile_for_id_03',
                                        'size': 3227,
                                    },
                                    'mainfile_for_id_04': {
                                        'm_is': 'File',
                                        'path': 'mainfile_for_id_04',
                                        'size': 3227,
                                    },
                                    'mainfile_for_id_05': {
                                        'm_is': 'File',
                                        'path': 'mainfile_for_id_05',
                                        'size': 3227,
                                    },
                                    'mainfile_for_id_06': {
                                        'm_is': 'File',
                                        'path': 'mainfile_for_id_06',
                                        'size': 3227,
                                    },
                                }
                            }
                        }
                    }
                },
            }
        },
    )


def test_general_reader_access_via_group(
    json_dict, uploads_graph_access_via_group, user2, user3
):
    def __ge_print(
        msg,
        required,
        *,
        to_file: bool = False,
        result: dict | None = None,
        user: dict | None = None,
    ):
        with MongoReader(required, user=user) as reader:
            if result:
                assert_dict(reader.sync_read(), result)
            else:
                rprint(f'\n\nExample: {next(counter)} -> {msg}:')
                rprint(required)
                if not to_file:
                    rprint('output:')
                    rprint(reader.sync_read())
                else:
                    with open('archive_reader_test.json', 'w') as f:
                        f.write(json.dumps(reader.sync_read()))

    __ge_print(
        'user2 has upload access via coauthor group and reviewer group (as group owner)',
        {
            Token.UPLOADS: {
                '*': {
                    'm_request': {
                        'include': [
                            'upload_id',
                            'main_author',
                            'coauthors',
                            'reviewers',
                            'coauthor_groups',
                            'reviewer_groups',
                        ]
                    },
                }
            }
        },
        user=user2,
        result={
            Token.UPLOADS: {
                'id_CGg2': {
                    'upload_id': 'id_CGg2',
                    'main_author': user1_dict,
                    'coauthors': [],
                    'reviewers': [],
                    'coauthor_groups': ['GGGGGGGGGGGGGGGGGGGGG2'],
                    'reviewer_groups': [],
                },
                'id_RGg2': {
                    'upload_id': 'id_RGg2',
                    'main_author': user1_dict,
                    'coauthors': [],
                    'reviewers': [],
                    'coauthor_groups': [],
                    'reviewer_groups': ['GGGGGGGGGGGGGGGGGGGGG2'],
                },
            }
        },
    )

    __ge_print(
        'user2 has entry access via coauthor group and reviewer group (as group owner)',
        {
            Token.ENTRIES: {
                '*': {
                    'm_request': {
                        'directive': 'resolved',
                        'include': ['upload_id', 'entry_id'],
                    },
                }
            }
        },
        user=user2,
        result={
            Token.ENTRIES: {
                'id_CGg2_1': {'upload_id': 'id_CGg2', 'entry_id': 'id_CGg2_1'},
                'id_RGg2_1': {'upload_id': 'id_RGg2', 'entry_id': 'id_RGg2_1'},
            }
        },
    )

    __ge_print(
        'user3 does not have upload access',
        {Token.UPLOADS: {}},
        user=user3,
        result={Token.UPLOADS: {}},
    )

    __ge_print(
        'user3 does not have entry access',
        {Token.ENTRIES: {}},
        user=user3,
        result={Token.ENTRIES: {}},
    )


@pytest_asyncio.fixture(scope='function')
async def custom_data(user1, temporal_worker):
    yaml_archive = yaml.safe_load(
        """
definitions:
  name: test_package_name
  section_definitions:
  - name: MySection
    base_sections:
    - nomad.datamodel.data.EntryData
    quantities:
    - name: my_quantity
      type:
        type_kind: python
        type_data: str
    - name: datetime_list
      type:
        type_kind: custom
        type_data: nomad.metainfo.data_type.Datetime
      shape:
      - "*"
data:
  m_def: "/definitions/section_definitions/0"
  my_quantity: test_value
  datetime_list:
  - '2022-04-01'
  - '2022-04-02'
"""
    )
    archive = EntryArchive.m_from_dict(yaml_archive, m_context=ServerContext())
    data = ExampleData(main_author=user1)

    data.create_upload(
        upload_id='id_custom', upload_name='name_published', published=True
    )
    async with temporal_worker():
        data.create_entry(
            upload_id='id_custom', entry_id='id_example', entry_archive=archive
        )
        await asyncio.to_thread(
            lambda: data.save(
                with_files=True,
                with_es=True,
                with_mongo=True,
                additional_files_path='tests/data/proc/nested.zip',
            )
        )

    yield data

    await asyncio.to_thread(data.delete)


def test_custom_schema_archive_and_definition(user1, custom_data):
    def __entry_print(
        msg, required, *, to_file: bool = False, result: dict | None = None
    ):
        with EntryReader(required, user=user1) as reader:
            response = reader.sync_read('id_example')
            if result:
                assert_dict(response, result)
            else:
                rprint(f'\n\nExample: {next(counter)} -> {msg}:')
                rprint(required)
                if not to_file:
                    rprint('output:')
                    rprint(response)
                else:
                    with open('entry_reader_test.json', 'w') as f:
                        f.write(json.dumps(response))

    __entry_print(
        'custom',
        {
            'm_request': {
                'directive': 'plain',
            },
            Token.ARCHIVE: {
                'data': {
                    'm_request': {
                        'directive': 'plain',
                    },
                    'm_def': {
                        'm_request': {
                            'directive': 'plain',
                            'export_whole_package': True,
                        },
                    },
                }
            },
        },
        result={
            'process_running': False,
            'current_process': None,
            'process_status': 'SUCCESS',
            'last_status_message': None,
            'errors': [],
            'warnings': [],
            'complete_time': None,
            'entry_id': 'id_example',
            'entry_create_time': '2025-08-20T10:46:57.535000',
            'mainfile_key': None,
            'upload_id': 'id_custom',
            'parser_name': 'parsers/vasp',
            'mainfile_path': 'mainfile_for_id_example',
            'uploads': {
                'id_custom': {
                    'entries': {
                        'id_example': {
                            'archive': {
                                'definitions': {
                                    'name': 'test_package_name',
                                    'section_definitions': [
                                        {
                                            'name': 'MySection',
                                            'base_sections': [
                                                'metainfo/nomad.datamodel.data/section_definitions/1'
                                            ],
                                            'quantities': [
                                                {
                                                    'name': 'my_quantity',
                                                    'type': {
                                                        'type_kind': 'python',
                                                        'type_data': 'str',
                                                    },
                                                },
                                                {
                                                    'name': 'datetime_list',
                                                    'shape': ['*'],
                                                    'type': {
                                                        'type_kind': 'custom',
                                                        'type_data': 'nomad.metainfo.data_type.Datetime',
                                                    },
                                                },
                                            ],
                                        }
                                    ],
                                    'all_quantities': {
                                        'MySection.my_quantity': 'uploads/id_custom/entries/id_example/archive/definitions/section_definitions/0/quantities/0',
                                        'MySection.datetime_list': 'uploads/id_custom/entries/id_example/archive/definitions/section_definitions/0/quantities/1',
                                    },
                                    'all_sub_sections': {},
                                    'all_base_sections': {
                                        'ArchiveSection': 'metainfo/nomad.datamodel.data/section_definitions/0',
                                        'EntryData': 'metainfo/nomad.datamodel.data/section_definitions/1',
                                    },
                                }
                            }
                        }
                    }
                }
            },
            'archive': {
                'data': {
                    'my_quantity': 'test_value',
                    'datetime_list': [
                        '2022-04-01T00:00:00+00:00',
                        '2022-04-02T00:00:00+00:00',
                    ],
                    'm_def': {
                        'm_def': 'uploads/id_custom/entries/id_example/archive/definitions/section_definitions/0',
                    },
                }
            },
        },
    )

    __entry_print(
        'custom',
        {
            Token.ARCHIVE: {
                'data': {
                    'm_def': {
                        'm_request': {
                            'directive': 'resolved',
                            'export_whole_package': True,
                            'depth': 1,
                        },
                    },
                }
            },
        },
        result={
            'uploads': {
                'id_custom': {
                    'entries': {
                        'id_example': {
                            'archive': {
                                'definitions': {
                                    'name': 'test_package_name',
                                    'section_definitions': [
                                        {
                                            'name': 'MySection',
                                            'base_sections': [
                                                'metainfo/nomad.datamodel.data/section_definitions/1'
                                            ],
                                            'quantities': [
                                                {
                                                    'name': 'my_quantity',
                                                    'type': {
                                                        'type_kind': 'python',
                                                        'type_data': 'str',
                                                    },
                                                },
                                                {
                                                    'name': 'datetime_list',
                                                    'type': {
                                                        'type_kind': 'custom',
                                                        'type_data': 'nomad.metainfo.data_type.Datetime',
                                                    },
                                                    'shape': ['*'],
                                                },
                                            ],
                                        }
                                    ],
                                    'all_quantities': {
                                        'MySection.my_quantity': 'uploads/id_custom/entries/id_example/archive/definitions/section_definitions/0/quantities/0',
                                        'MySection.datetime_list': 'uploads/id_custom/entries/id_example/archive/definitions/section_definitions/0/quantities/1',
                                    },
                                    'all_sub_sections': {},
                                    'all_base_sections': {
                                        'ArchiveSection': 'metainfo/nomad.datamodel.data/section_definitions/0',
                                        'EntryData': 'metainfo/nomad.datamodel.data/section_definitions/1',
                                    },
                                    'base_sections': [
                                        'metainfo/nomad.datamodel.data/section_definitions/1'
                                    ],
                                }
                            }
                        }
                    }
                }
            },
            'metainfo': {
                'nomad.datamodel.data': {
                    'name': 'nomad.datamodel.data',
                    'section_definitions': [
                        {
                            'name': 'ArchiveSection',
                            'description': 'Base class for sections in a NOMAD archive. Provides a framework for custom section normalization via the `normalize` function.',
                        },
                        {
                            'name': 'EntryData',
                            'description': 'An empty base section definition. This can be used to add new top-level sections to an entry.',
                            'base_sections': [
                                'metainfo/nomad.datamodel.data/section_definitions/0'
                            ],
                        },
                        {
                            'name': 'Author',
                            'description': 'A person that is author of data in NOMAD or references by NOMAD.',
                            'quantities': [
                                {
                                    'm_annotations': {
                                        'elasticsearch': [
                                            'viewers.name',
                                            'viewers.name.text',
                                            'viewers.name__suggestion',
                                        ]
                                    },
                                    'name': 'name',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                    'virtual': True,
                                },
                                {
                                    'name': 'first_name',
                                    'description': 'The users first name (including all other given names)',
                                    'type': {
                                        'type_kind': 'custom',
                                        'type_data': 'nomad.metainfo.data_type.Capitalized',
                                    },
                                },
                                {
                                    'name': 'last_name',
                                    'description': 'The users last name',
                                    'type': {
                                        'type_kind': 'custom',
                                        'type_data': 'nomad.metainfo.data_type.Capitalized',
                                    },
                                },
                                {
                                    'name': 'email',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                },
                                {
                                    'name': 'affiliation',
                                    'description': 'The name of the company and institutes the user identifies with',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                },
                                {
                                    'name': 'affiliation_address',
                                    'description': 'The address of the given affiliation',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                },
                            ],
                        },
                        {
                            'm_annotations': {'pydantic': ['PydanticModel']},
                            'name': 'User',
                            'description': 'A NOMAD user. Typically a NOMAD user has a NOMAD account. The user related data is managed by\nNOMAD keycloak user-management system. Users are used to denote authors,\nreviewers, and owners of datasets.',
                            'base_sections': [
                                'metainfo/nomad.datamodel.data/section_definitions/2'
                            ],
                            'quantities': [
                                {
                                    'm_annotations': {
                                        'elasticsearch': ['viewers.user_id']
                                    },
                                    'name': 'user_id',
                                    'description': 'The unique, persistent keycloak UUID',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                },
                                {
                                    'name': 'username',
                                    'description': 'The unique, persistent, user chosen username',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                },
                                {
                                    'name': 'created',
                                    'description': 'The time the account was created',
                                    'type': {
                                        'type_kind': 'custom',
                                        'type_data': 'nomad.metainfo.data_type.Datetime',
                                    },
                                },
                                {
                                    'name': 'repo_user_id',
                                    'description': 'Optional, legacy user id from the old NOMAD CoE repository.',
                                    'type': {'type_kind': 'python', 'type_data': 'str'},
                                },
                                {
                                    'name': 'is_admin',
                                    'description': 'Bool that indicated, if the user is the admin',
                                    'type': {
                                        'type_kind': 'python',
                                        'type_data': 'bool',
                                    },
                                    'virtual': True,
                                },
                                {
                                    'name': 'is_oasis_admin',
                                    'type': {
                                        'type_kind': 'python',
                                        'type_data': 'bool',
                                    },
                                    'default': False,
                                },
                            ],
                        },
                    ],
                    'category_definitions': [
                        {
                            'name': 'EntryDataCategory',
                        },
                        {
                            'name': 'ElnIntegrationCategory',
                            'label': 'Third-party ELN Integration',
                            'categories': ['/category_definitions/0'],
                        },
                        {
                            'name': 'BasicElnCategory',
                            'label': 'Basic ELN',
                            'categories': ['/category_definitions/0'],
                        },
                        {
                            'name': 'ElnExampleCategory',
                            'label': 'Example ELNs',
                            'categories': ['/category_definitions/0'],
                        },
                        {
                            'name': 'UseCaseElnCategory',
                            'label': 'Use-cases',
                            'categories': ['/category_definitions/0'],
                        },
                        {
                            'name': 'WorkflowsElnCategory',
                            'label': 'Workflows',
                            'categories': ['/category_definitions/0'],
                        },
                    ],
                    'all_quantities': {
                        'Author.name': 'metainfo/nomad.datamodel.data/section_definitions/2/quantities/0',
                        'Author.first_name': 'metainfo/nomad.datamodel.data/section_definitions/2/quantities/1',
                        'Author.last_name': 'metainfo/nomad.datamodel.data/section_definitions/2/quantities/2',
                        'Author.email': 'metainfo/nomad.datamodel.data/section_definitions/2/quantities/3',
                        'Author.affiliation': 'metainfo/nomad.datamodel.data/section_definitions/2/quantities/4',
                        'Author.affiliation_address': 'metainfo/nomad.datamodel.data/section_definitions/2/quantities/5',
                        'User.user_id': 'metainfo/nomad.datamodel.data/section_definitions/3/quantities/0',
                        'User.username': 'metainfo/nomad.datamodel.data/section_definitions/3/quantities/1',
                        'User.created': 'metainfo/nomad.datamodel.data/section_definitions/3/quantities/2',
                        'User.repo_user_id': 'metainfo/nomad.datamodel.data/section_definitions/3/quantities/3',
                        'User.is_admin': 'metainfo/nomad.datamodel.data/section_definitions/3/quantities/4',
                        'User.is_oasis_admin': 'metainfo/nomad.datamodel.data/section_definitions/3/quantities/5',
                    },
                    'all_sub_sections': {},
                    'all_base_sections': {
                        'ArchiveSection': 'metainfo/nomad.datamodel.data/section_definitions/0',
                        'Author': 'metainfo/nomad.datamodel.data/section_definitions/2',
                    },
                }
            },
            'archive': {
                'data': {
                    'm_def': {
                        'm_def': 'uploads/id_custom/entries/id_example/archive/definitions/section_definitions/0',
                    }
                }
            },
        },
    )

    __entry_print(
        'custom',
        {
            Token.ARCHIVE: {
                'data': {
                    'm_request': {
                        'directive': 'plain',
                    },
                    'm_def': {
                        'm_request': {
                            'directive': 'plain',
                        },
                    },
                }
            },
        },
        result={
            'uploads': {
                'id_custom': {
                    'entries': {
                        'id_example': {
                            'archive': {
                                'definitions': {
                                    'section_definitions': [
                                        {
                                            'name': 'MySection',
                                            'base_sections': [
                                                'metainfo/nomad.datamodel.data/section_definitions/1'
                                            ],
                                            'quantities': [
                                                {
                                                    'name': 'my_quantity',
                                                    'type': {
                                                        'type_kind': 'python',
                                                        'type_data': 'str',
                                                    },
                                                },
                                                {
                                                    'name': 'datetime_list',
                                                    'shape': ['*'],
                                                    'type': {
                                                        'type_kind': 'custom',
                                                        'type_data': 'nomad.metainfo.data_type.Datetime',
                                                    },
                                                },
                                            ],
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            },
            'archive': {
                'data': {
                    'my_quantity': 'test_value',
                    'datetime_list': [
                        '2022-04-01T00:00:00+00:00',
                        '2022-04-02T00:00:00+00:00',
                    ],
                    'm_def': {
                        'm_def': 'uploads/id_custom/entries/id_example/archive/definitions/section_definitions/0',
                    },
                }
            },
        },
    )

    def __fs_print(msg, required, *, result: dict | None = None):
        with FileSystemReader(required, user=user1) as reader:
            if result:
                assert_dict(reader.sync_read('id_custom'), result)
            else:
                rprint(f'\n\nExample: {next(counter)} -> {msg}:')
                rprint(required)
                rprint('output:')
                rprint(reader.sync_read('id_custom'))

    __fs_print(
        'one level deep second page',
        {
            'm_request': {
                'directive': 'plain',
                'depth': 1,
                'pagination': {'page_size': 10, 'page': 2},
            },
        },
        result={
            'm_is': 'Directory',
            'mainfile_for_id_example': {
                'm_is': 'File',
                'path': 'mainfile_for_id_example',
                'size': 3227,
            },
        },
    )

    __fs_print(
        'two levels',
        {
            'm_request': {
                'directive': 'plain',
                'depth': 2,
                'pagination': {'page_size': 10, 'page': 2},
            },
        },
        result={
            'm_is': 'Directory',
            '3.aux': {'m_is': 'File', 'path': '3.aux', 'size': 8},
            '4.aux': {'m_is': 'File', 'path': '4.aux', 'size': 8},
            'edge_names': {
                '!"┬º$%&()=?.txt': {
                    'm_is': 'File',
                    'path': 'edge_names/!"┬º$%&()=?.txt',
                    'size': 0,
                },
                'suuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuper-long.txt': {
                    'm_is': 'File',
                    'path': 'edge_names/suuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuper-long.txt',
                    'size': 0,
                },
            },
            'entry.archive.json': {
                'm_is': 'File',
                'path': 'entry.archive.json',
                'size': 185,
            },
            'file.txt': {'m_is': 'File', 'path': 'file.txt', 'size': 0},
            'mainfile_for_id_example': {
                'm_is': 'File',
                'path': 'mainfile_for_id_example',
                'size': 3227,
            },
            'many_files': {
                'file1.txt': {
                    'm_is': 'File',
                    'path': 'many_files/file1.txt',
                    'size': 0,
                },
                'file10.txt': {
                    'm_is': 'File',
                    'path': 'many_files/file10.txt',
                    'size': 0,
                },
                'file100.txt': {
                    'm_is': 'File',
                    'path': 'many_files/file100.txt',
                    'size': 0,
                },
            },
        },
    )

    __fs_print(
        'different configs',
        {
            'm_request': {
                'directive': 'plain',
                'depth': 1,
                'pagination': {'page_size': 2, 'page': 2},
            },
            'many_files': {
                'm_request': {
                    'directive': 'plain',
                    'depth': 1,
                    'pagination': {'page_size': 3, 'page': 2},
                },
            },
        },
        result={
            'm_is': 'Directory',
            'preview': {'m_is': 'Directory'},
            'subdirs': {'m_is': 'Directory'},
            'many_files': {
                'm_is': 'Directory',
                'file11.txt': {
                    'm_is': 'File',
                    'path': 'many_files/file11.txt',
                    'size': 0,
                },
                'file12.txt': {
                    'm_is': 'File',
                    'path': 'many_files/file12.txt',
                    'size': 0,
                },
                'file13.txt': {
                    'm_is': 'File',
                    'path': 'many_files/file13.txt',
                    'size': 0,
                },
            },
        },
    )


@pytest.fixture(scope='function')
def example_data_with_reference(
    elastic_function, raw_files_module, mongo_function, user1, json_dict
):
    """
    Provides a couple of entries with references.

    Only used in test_required_reader_with_remote_reference.
    """
    data = ExampleData(main_author=user1)

    data.create_upload(
        upload_id='id_published_with_ref',
        upload_name='name_published',
        description='Test Description',
        published=False,
    )

    ref_list = [
        {
            'results': {'calculation_result_ref': '/run/0/calculation/1'}
        },  # plain direct reference
        {
            'results': {'calculation_result_ref': '#/run/0/calculation/1'}
        },  # new-style reference
        {
            'tasks': [
                {
                    'm_def': 'nomad.datamodel.metainfo.workflow.TaskReference',
                    'task': '../entries/id_01/archive#/workflow2',
                }
            ]
        },  # reference to another archive
        {
            'tasks': [
                {
                    'm_def': 'nomad.datamodel.metainfo.workflow.TaskReference',
                    'task': '../entries/id_05/archive#/workflow2',
                }
            ]
        },  # circular reference
        {
            'tasks': [
                {
                    'm_def': 'nomad.datamodel.metainfo.workflow.TaskReference',
                    'task': '../entries/id_04/archive#/workflow2',
                }
            ]
        },  # circular reference
        {
            'tasks': [
                {
                    'm_def': 'nomad.datamodel.metainfo.workflow.TaskReference',
                    'task': 'https://another.domain/entries/id_03/archive#/workflow2',
                }
            ]
        },  # remote reference
    ]

    for index, ref in enumerate(ref_list):
        ref['m_def'] = 'simulationworkflowschema.SimulationWorkflow'
        json_copy = {k: v for k, v in json_dict.items() if k is not 'results'}
        json_copy['workflow2'] = ref
        data.create_entry(
            upload_id='id_published_with_ref',
            entry_id=f'id_{index + 1:02d}',
            entry_archive=EntryArchive.m_from_dict(json_copy),
        )

    for archive in data.archives.values():
        archive.metadata.apply_archive_metadata(archive)

    data.save(with_files=True, with_es=True, with_mongo=True)

    yield data
    data.delete()


@pytest.fixture(scope='function')
def json_dict():
    return {
        'metadata': {'entry_id': 'test_id', 'upload_id': 'id_published_with_ref'},
        'results': {
            'properties': {
                'electronic': {
                    'dos_electronic': [
                        {'energies': '/run/0/calculation/1/dos_electronic/0/energies'}
                    ]
                }
            }
        },
        'run': [
            {
                'm_def': 'runschema.run.Run',
                'system': [
                    {
                        'atoms': {'labels': ['He']},
                        'symmetry': [{'space_group_number': 221}],
                    },
                    {
                        'atoms': {'labels': ['H']},
                        'symmetry': [{'space_group_number': 221}],
                    },
                ],
                'calculation': [
                    {
                        'system_ref': '/run/0/system/1',
                        'energy': {'total': {'value': 0.1}},
                    },
                    {
                        'system_ref': '/run/0/system/1',
                        'energy': {'total': {'value': 0.2}},
                        'dos_electronic': [
                            {'energies': [0.0, 0.1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]}
                        ],
                        'eigenvalues': [],
                    },
                    {
                        'system_ref': '/run/0/system/1',
                        'energy': {'total': {'value': 0.1}},
                    },
                ],
            }
        ],
        'workflow2': {
            'm_def': 'simulationworkflowschema.SimulationWorkflow',
            'results': {'calculation_result_ref': '/run/0/calculation/1'},
        },
    }


@pytest_asyncio.fixture(scope='function')
async def example_data_with_figure(
    elastic_function,
    raw_files_module,
    mongo_function,
    user1,
    user2,
    temporal_worker,
):
    data = ExampleData(main_author=user1)

    data.create_upload(
        upload_id='id_published_with_ref',
        upload_name='name_published',
        published=False,
        coauthors=[user2.user_id],
        reviewers=[user2.user_id],
    )

    directory = 'tests/data/datamodel/metainfo/plotly'
    mainfile = 'plotly.schema.archive.yaml'
    async with temporal_worker():
        data.create_entry(
            upload_id='id_published_with_ref',
            entry_id='id_plotly',
            entry_archive=await asyncio.to_thread(
                lambda: run_processing(directory, mainfile)
            ),
        )

        for archive in data.archives.values():
            archive.metadata.apply_archive_metadata(archive)

        await asyncio.to_thread(
            lambda: data.save(
                with_files=True,
                with_es=True,
                with_mongo=True,
                additional_files_path='tests/data/proc/nested.zip',
            )
        )

    yield data

    await asyncio.to_thread(data.delete)


@pytest.mark.parametrize(
    'query,result',
    [
        # plain get default quantities
        # the references are not resolved
        pytest.param(
            {
                Token.ARCHIVE: {
                    'data': {
                        'figures[0]': {
                            'm_request': {
                                'directive': 'plain',
                            },
                        }
                    }
                },
            },
            {
                'archive': {
                    'data': {
                        'figures': [
                            {
                                'label': 'graph object 1',
                                'figure': {
                                    'data': {'x': '#xArr', 'y': '#xArr'},
                                    'layout': {
                                        'title': {'text': 'Plot in section level'},
                                        'xaxis': {'title': {'text': 'x data'}},
                                        'yaxis': {'title': {'text': 'y data'}},
                                    },
                                },
                            }
                        ]
                    }
                }
            },
            id='plain-read',
        ),
        pytest.param(
            {
                Token.ARCHIVE: {
                    'data': {
                        'figures[0]': {
                            'm_request': {
                                'directive': 'resolved',
                            },
                        }
                    }
                },
            },
            {
                'archive': {
                    'data': {
                        'xArr': [1.1, 2.0, 3.0, 4.0, 5.0],
                        'figures': [
                            {
                                'label': 'graph object 1',
                                'figure': {
                                    'data': {'x': '#xArr', 'y': '#xArr'},
                                    'layout': {
                                        'title': {'text': 'Plot in section level'},
                                        'xaxis': {'title': {'text': 'x data'}},
                                        'yaxis': {'title': {'text': 'y data'}},
                                    },
                                },
                            }
                        ],
                    }
                }
            },
            id='read-resolved',
        ),
    ],
)
def test_figure_resolution(user1, example_data_with_figure, query, result):
    def __entry_print(required, *, result=None):
        with EntryReader(required, user=user1) as reader:
            response = reader.sync_read('id_plotly')
            if result:
                assert_dict(response, result)

    __entry_print(query, result=result)


def test_auto_layout_populates_archive_payload(user1, user2, example_data_with_figure):
    """
    Test that auto_from_layout derives the archive payload needed by the layout.
    """
    from nomad.graph.graph_reader import EntryReader

    required = {
        Token.METADATA: {
            'entry_type': '*',
            'entry_name': '*',
            'published': '*',
            'with_embargo': '*',
            'main_author': '*',
            'coauthors': '*',
            'reviewers': '*',
            'coauthor_groups': '*',
            'reviewer_groups': '*',
            'writers': '*',
            'writer_groups': '*',
            'viewers': '*',
            'viewer_groups': '*',
        },
        'matching_layouts': '*',
        'default_layout_id': '*',
        'resolved_layout_id': '*',
        Token.ARCHIVE: {'m_request': {'directive': 'auto_from_layout'}},
    }

    with EntryReader(required, user=user1) as reader:
        response = reader.sync_read('id_plotly')
        assert 'resolved_layout_id' in response
        assert response['resolved_layout_id'] == 'default'
        assert response['default_layout_id'] == 'default'
        assert response['matching_layouts'][0]['id'] == 'default'
        assert response['matching_layouts'][0]['overview']['type'] == 'container'
        archive_metadata = example_data_with_figure.archives['id_plotly'].metadata
        metadata = response[Token.METADATA]
        assert metadata['entry_type'] == archive_metadata.entry_type
        assert metadata['entry_name'] == archive_metadata.entry_name
        assert metadata['published'] is False
        assert metadata['with_embargo'] is False
        assert metadata['main_author'] == user1.user_id
        assert metadata['coauthors'] == [user2.user_id]
        assert metadata['reviewers'] == [user2.user_id]
        assert metadata.get('coauthor_groups', []) == []
        assert metadata.get('reviewer_groups', []) == []
        assert metadata.get('writers', []) == []
        assert metadata.get('writer_groups', []) == []
        assert metadata.get('viewers', []) == []
        assert metadata.get('viewer_groups', []) == []
        assert 'archive' in response
        assert 'm_def' in response['archive'], (
            f'Root m_def is missing. Archive keys: {list(response["archive"].keys())}'
        )

        # Verify that figures (which are triggered by the layout) are present
        data = response['archive'].get('data', {})
        assert 'figures' in data
        assert len(data['figures']) > 0

        # Keep parity with the frontend layout request shape: compact m_def strings
        # are resolved by the GUI against its metainfo cache.
        data_request = response['resolved_archive_request']['data']
        assert data_request['m_request']['m_def_format'] == 'short'
        assert data_request['m_def']['m_request']['m_def_format'] == 'short'


def test_auto_layout_rejects_unknown_layout(user1, example_data_with_figure):
    from nomad.graph.graph_reader import ConfigError, EntryReader

    required = {
        Token.ARCHIVE: {
            'm_request': {
                'directive': 'auto_from_layout',
                'layout_id': 'does-not-exist',
            }
        }
    }

    with (
        EntryReader(required, user=user1) as reader,
        pytest.raises(ConfigError, match='Unknown layout id'),
    ):
        reader.sync_read('id_plotly')


def test_layout_like_data_request_includes_inherited_figures_without_definition_errors(
    user1, example_data_with_figure
):
    required = {
        Token.ARCHIVE: {
            'data': {
                'm_request': {
                    'directive': 'plain',
                    'include_definition': 'both',
                    'm_def_format': 'short',
                    'depth': 2,
                },
                'm_def': {
                    'm_request': {
                        'directive': 'plain',
                        'm_def_format': 'short',
                        'export_whole_package': True,
                    }
                },
                'figures': '*',
            }
        }
    }

    with EntryReader(required, user=user1) as reader:
        response = reader.sync_read('id_plotly')
        assert 'figures' in response.get('archive', {}).get('data', {})
        messages = [error.get('message') for error in response.get('m_errors', [])]
        assert 'Definition figures is not found.' not in messages, (
            f'Unexpected errors in response: {response.get("m_errors")}'
        )


def test_mongo_reader_explicit_upload_lookup_skips_container_query(
    monkeypatch, user1, example_data_with_reference
):
    async def _fail_query_uploads(self, config):
        raise AssertionError('explicit upload lookup should not hit _query_uploads')

    monkeypatch.setattr(MongoReader, '_query_uploads', _fail_query_uploads)

    required = {
        Token.UPLOADS: {
            'id_published_with_ref': {
                'm_request': {
                    'directive': 'resolved',
                    'resolve_type': 'upload',
                },
            }
        }
    }

    with MongoReader(required, user=user1) as reader:
        response = reader.sync_read()

    assert (
        response['uploads']['id_published_with_ref']['upload_name'] == 'name_published'
    )


def test_file_system_reader_resolved_directory_uses_batch_lookup(
    monkeypatch, user1, example_data_with_reference
):
    async def _fail_offload(self, upload_id, main_file, required, parent_config):
        raise AssertionError(
            'resolved directory listings should not use per-file _offload'
        )

    monkeypatch.setattr(FileSystemReader, '_offload', _fail_offload)

    required = {
        'm_request': {
            'directive': 'resolved',
        }
    }

    with FileSystemReader(required, user=user1) as reader:
        response = reader.sync_read('id_published_with_ref')

    assert response['mainfile_for_id_01']['entry']['entry_id'] == 'id_01'
    assert response['mainfile_for_id_02']['entry']['entry_id'] == 'id_02'


def test_file_system_reader_plain_listing(user1, example_data_with_reference):
    required = {
        'm_request': {
            'directive': 'plain',
        }
    }
    with FileSystemReader(required, user=user1) as reader:
        response = reader.sync_read('id_published_with_ref')
        assert 'id_published_with_ref' in reader.upload_pool
        assert response['m_is'] == 'Directory'
        assert response['1.aux'] == {'path': '1.aux', 'm_is': 'File', 'size': 8}
        assert response['mainfile_for_id_01']['m_is'] == 'File'
        assert response['mainfile_for_id_01']['size'] == 3227


def test_file_system_reader_pools_and_closes_published_zip(user1, example_data):
    required = {
        'm_request': {
            'directive': 'plain',
            'depth': 2,
        }
    }
    with FileSystemReader(required, user=user1) as reader:
        response = reader.sync_read('id_published')
        assert 'id_published' in reader.upload_pool
        upload_files = reader.upload_pool['id_published']
        assert isinstance(upload_files, PublicUploadFiles)
        assert upload_files._zip is not None
        assert response['m_is'] == 'Directory'
        assert 'test_content' in response

    assert upload_files._zip is None


def test_file_system_reader_reuses_zip_index_across_queries(
    monkeypatch, tmp_path, user1, example_data
):
    from nomad.config import config

    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'directory', str(tmp_path))

    zipfile_calls = {'n': 0}
    original_zipfile = zipfile.ZipFile

    class CountingZipFile(original_zipfile):
        def __init__(self, *args, **kwargs):
            zipfile_calls['n'] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(zipfile, 'ZipFile', CountingZipFile)

    required = {
        'm_request': {
            'directive': 'plain',
            'depth': 2,
        }
    }
    with FileSystemReader(required, user=user1) as reader:
        response = reader.sync_read('id_published')
        upload_files = reader.upload_pool['id_published']
        assert response['m_is'] == 'Directory'
        assert upload_files._zip is not None
    assert upload_files._zip is None
    first_parses = zipfile_calls['n']
    assert first_parses >= 1

    with FileSystemReader(required, user=user1) as reader:
        response = reader.sync_read('id_published')
        upload_files = reader.upload_pool['id_published']
        assert response['m_is'] == 'Directory'
        assert 'test_content' in response
        assert upload_files._zip is not None
    assert zipfile_calls['n'] == first_parses


def test_file_system_reader_reuses_archive_toc_across_queries(
    monkeypatch, tmp_path, user1, example_data
):
    from nomad.archive.utils import check_archive_version as original_check
    from nomad.config import config

    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'directory', str(tmp_path))

    version_calls = {'n': 0}

    def counting_check(file_or_path, *args, **kwargs):
        version_calls['n'] += 1
        return original_check(file_or_path, *args, **kwargs)

    monkeypatch.setattr('nomad.files.archive_toc.check_archive_version', counting_check)

    required = {
        'm_request': {'directive': 'plain'},
        Token.ARCHIVE: {
            'metadata': {
                'm_request': {'directive': 'plain', 'include': ['entry_id']},
            }
        },
    }

    with EntryReader(required, user=user1) as reader:
        first = reader.sync_read('id_01')
    first_calls = version_calls['n']
    assert first_calls >= 1

    with EntryReader(required, user=user1) as reader:
        second = reader.sync_read('id_03')
    assert version_calls['n'] == first_calls
    assert first['archive']['metadata']['entry_id'] == 'id_01'
    assert second['archive']['metadata']['entry_id'] == 'id_03'


def test_file_system_reader_reuses_zip_index_across_queries_with_ref(
    monkeypatch, tmp_path, user1, example_data_with_reference
):
    from nomad.config import config

    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'directory', str(tmp_path))

    zipfile_calls = {'n': 0}
    original_zipfile = zipfile.ZipFile

    class CountingZipFile(original_zipfile):
        def __init__(self, *args, **kwargs):
            zipfile_calls['n'] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(zipfile, 'ZipFile', CountingZipFile)

    required = {
        'm_request': {
            'directive': 'plain',
            'depth': 2,
        }
    }
    with FileSystemReader(required, user=user1) as reader:
        response = reader.sync_read('id_published_with_ref')
        upload_files = reader.upload_pool['id_published_with_ref']
        assert response['m_is'] == 'Directory'
        assert isinstance(upload_files, StagingUploadFiles)
        assert 'mainfile_for_id_01' in response
    assert zipfile_calls['n'] == 0

    with FileSystemReader(required, user=user1) as reader:
        response = reader.sync_read('id_published_with_ref')
        upload_files = reader.upload_pool['id_published_with_ref']
        assert response['m_is'] == 'Directory'
        assert 'mainfile_for_id_01' in response
    assert zipfile_calls['n'] == 0


def test_entry_reader_retrieve_entry_does_not_call_perform_search(
    monkeypatch, user1, example_data_with_reference
):
    def _fail_search(*args, **kwargs):
        raise AssertionError('retrieve_entry should not call perform_search')

    monkeypatch.setattr('nomad.graph.graph_reader.perform_search', _fail_search)

    with EntryReader({'m_request': {'directive': 'plain'}}, user=user1) as reader:
        response = reader.sync_read('id_03')

    assert response['entry_id'] == 'id_03'


def test_entry_reader_retrieve_entry_group_visibility(
    uploads_graph_access_via_group, user2, user3
):
    required = {'m_request': {'directive': 'plain'}}

    with EntryReader(required, user=user2) as reader:
        response = reader.sync_read('id_CGg2_1')
    assert response['entry_id'] == 'id_CGg2_1'

    with EntryReader(required, user=user2) as reader:
        response = reader.sync_read('id_RGg2_1')
    assert response['entry_id'] == 'id_RGg2_1'

    with EntryReader(required, user=user3) as reader:
        response = reader.sync_read('id_CGg2_1')
    assert response['m_errors'][0]['error_type'] == 'NOACCESS'


def test_entry_reader_retrieve_entry_anonymous_all_group_visibility(uploads_get_groups):
    required = {'m_request': {'directive': 'plain'}}

    with EntryReader(required, user=None) as reader:
        visible = reader.sync_read('id_RGall_1')
    assert visible['entry_id'] == 'id_RGall_1'

    with EntryReader(required, user=None) as reader:
        hidden = reader.sync_read('id_CGg2_1')
    assert hidden['m_errors'][0]['error_type'] == 'NOACCESS'


def test_m_def_format_short(user1, custom_data):
    """Test that m_def_format='short' produces compact 'qualified_name@definition_id' strings."""

    def _read_entry(required):
        with EntryReader(required, user=user1) as reader:
            return reader.sync_read('id_example')

    # --- Test 1: custom definition with m_def_format='short' ---
    # The 'data' section has a custom m_def pointing to a local definition (MySection).
    # With m_def_format='short', we expect m_def to be a compact string.
    response = _read_entry(
        {
            'm_request': {'directive': 'plain'},
            Token.ARCHIVE: {
                'data': {
                    'm_request': {
                        'directive': 'plain',
                        'm_def_format': 'short',
                    },
                }
            },
        }
    )
    archive_data = response['archive']['data']
    m_def_value = archive_data['m_def']
    # m_def should be a string, not a dict
    assert isinstance(m_def_value, str), (
        f'Expected m_def to be a compact string, got {type(m_def_value)}: {m_def_value}'
    )
    # It should contain '@' separator between qualified_name and definition_id
    assert '@' in m_def_value, (
        f'Expected m_def to contain @ separator, got: {m_def_value}'
    )
    qualified_name, definition_id = m_def_value.split('@', 1)
    assert 'MySection' in qualified_name, (
        f'Expected qualified_name to contain MySection, got: {qualified_name}'
    )
    assert len(definition_id) > 0, 'Expected non-empty definition_id'
    # The other quantities should still be present
    assert archive_data['my_quantity'] == 'test_value'

    # --- Test 2: standard (non-custom) section with m_def_format='short' ---
    # The 'metadata' section uses a standard definition (EntryMetadata).
    # With m_def_format='short', each sub-section should get a compact m_def string.
    response = _read_entry(
        {
            'm_request': {'directive': 'plain'},
            Token.ARCHIVE: {
                'metadata': {
                    'm_request': {
                        'directive': 'plain',
                        'm_def_format': 'short',
                    },
                }
            },
        }
    )
    archive_metadata = response['archive']['metadata']
    m_def_value = archive_metadata['m_def']
    assert isinstance(m_def_value, str), (
        f'Expected m_def to be a compact string, got {type(m_def_value)}: {m_def_value}'
    )
    assert '@' in m_def_value
    qualified_name, definition_id = m_def_value.split('@', 1)
    # EntryMetadata is the section definition for metadata
    assert 'EntryMetadata' in qualified_name, (
        f'Expected qualified_name to contain EntryMetadata, got: {qualified_name}'
    )

    # --- Test 3: default m_def_format (full) should still return dict ---
    # Without m_def_format or with m_def_format='full',
    # the m_def should remain a dict (the legacy behavior).
    response = _read_entry(
        {
            'm_request': {'directive': 'plain'},
            Token.ARCHIVE: {
                'data': {
                    'm_request': {
                        'directive': 'plain',
                        'include_definition': 'both',
                    },
                }
            },
        }
    )
    archive_data = response['archive']['data']
    m_def_value = archive_data['m_def']
    assert isinstance(m_def_value, dict), (
        f'Expected m_def to be a dict with default format, got {type(m_def_value)}: {m_def_value}'
    )

    # --- Test 4: m_def_format='short' with multiple named sections ---
    # Request both 'data' and 'metadata' sections with short m_def format.
    # Both sections should get compact m_def strings.
    response = _read_entry(
        {
            'm_request': {
                'directive': 'plain',
                'm_def_format': 'short',
            },
            Token.ARCHIVE: {
                'data': {
                    'm_request': {
                        'directive': 'plain',
                        'm_def_format': 'short',
                    },
                },
                'metadata': {
                    'm_request': {
                        'directive': 'plain',
                        'm_def_format': 'short',
                    },
                },
            },
        }
    )
    archive = response['archive']
    # 'data' section should have compact m_def
    assert isinstance(archive['data']['m_def'], str)
    assert '@' in archive['data']['m_def']
    # 'metadata' section should also have compact m_def
    assert isinstance(archive['metadata']['m_def'], str)
    assert '@' in archive['metadata']['m_def']


def test_m_def_format_short_with_explicit_m_def(user1, custom_data):
    """Test that m_def_format='short' works when m_def is also explicitly requested.

    When both m_def_format='short' and an explicit m_def sub-request are present,
    the short format should take precedence and the request should not fail with
    "'str' object does not support item assignment".
    """

    def _read_entry(required):
        with EntryReader(required, user=user1) as reader:
            return reader.sync_read('id_example')

    # m_def_format='short' on config, plus explicit m_def sub-request
    response = _read_entry(
        {
            'm_request': {'directive': 'plain'},
            Token.ARCHIVE: {
                'data': {
                    'm_request': {
                        'directive': 'plain',
                        'm_def_format': 'short',
                    },
                    'm_def': {
                        'm_request': {
                            'directive': 'plain',
                        },
                    },
                }
            },
        }
    )
    # Should not have any errors
    assert 'm_errors' not in response, (
        f'Unexpected errors in response: {response.get("m_errors")}'
    )
    archive_data = response['archive']['data']
    # m_def should be a compact short string (not a dict), since m_def_format takes precedence
    m_def_value = archive_data['m_def']
    assert isinstance(m_def_value, str), (
        f'Expected m_def to be a compact string, got {type(m_def_value)}: {m_def_value}'
    )
    assert '@' in m_def_value
    assert 'MySection' in m_def_value
    # Other quantities should still be present
    assert archive_data['my_quantity'] == 'test_value'


def test_archive_reader_filtered_v3_lazy_reader():
    """Regression test: v3 combined archives return msglc.LazyReader for entry archives.

    Ensure ArchiveReader respects include/exclude filters on LazyReader and does
    not treat it as a primitive value that gets copied wholesale.
    """
    from io import BytesIO

    from msglc import FileInfo, LazyReader, combine, dump

    target = BytesIO()

    def generate_entries():
        buf = BytesIO()
        dump(
            buf,
            {
                'entry_id_1': {
                    'metadata': {
                        'upload_id': 'upload_id_1',
                        'entry_id': 'entry_id_1',
                        'mainfile': 'test.json',
                    },
                    'results': {
                        'material': {
                            'chemical_formula_reduced': 'H2O',
                        },
                    },
                }
            },
            backend='rust',
        )
        buf.seek(0)
        with LazyReader(buf) as r:
            yield FileInfo(None, 'entry_id_1', obj=r['entry_id_1'])

    combine(target, generate_entries(), backend='rust')
    target.seek(0)

    with LazyReader(target) as combined_reader:
        entry = combined_reader['entry_id_1'].unwrap()
        assert not isinstance(entry, LazyReader)

        # 1. exclude: ['*'] should return empty dict
        with ArchiveReader(
            {'m_request': {'directive': 'plain', 'exclude': ['*']}}
        ) as reader:
            res = reader.sync_read(entry)
            assert res == {}, f'Expected empty dict, got {res}'

        # 2. include: ['results'] should only contain results, not metadata
        with ArchiveReader(
            {'m_request': {'directive': 'plain', 'include': ['results']}}
        ) as reader:
            res = reader.sync_read(entry)
            assert 'metadata' not in res
            assert res.get('results') == {
                'material': {
                    'chemical_formula_reduced': 'H2O',
                },
            }

        # 3. exclude: ['metadata'] should omit metadata
        with ArchiveReader(
            {'m_request': {'directive': 'plain', 'exclude': ['metadata']}}
        ) as reader:
            res = reader.sync_read(entry)
            assert 'metadata' not in res
            assert 'results' in res

        # 4. Filtered subsection query on metadata
        with ArchiveReader(
            {
                'metadata': {
                    'm_request': {
                        'directive': 'plain',
                        'include': ['entry_id'],
                    }
                }
            }
        ) as reader:
            res = reader.sync_read(entry)
            assert res == {'metadata': {'entry_id': 'entry_id_1'}}, f'Got {res}'


def test_archive_reader_resolves_repeated_subsection_definition_once(monkeypatch):
    from nomad.graph.model import DirectiveType, RequestConfig
    from nomad.metainfo import MSection, Package, SubSection

    m_package = Package(name='test_repeated_subsection_definition')

    class Child(MSection):
        pass

    class Parent(MSection):
        children = SubSection(section_def=Child, repeats=True)

    m_package.__init_metainfo__()
    original_m_resolved = MSection.m_resolved
    resolution_count = 0

    def count_child_resolution(definition):
        nonlocal resolution_count
        if definition is Child.m_def:
            resolution_count += 1
        return original_m_resolved(definition)

    monkeypatch.setattr(MSection, 'm_resolved', count_child_resolution)
    children = [{}, {}, {}]
    result = {}

    with ArchiveReader({}) as reader:
        node = GraphNode(
            upload_id='upload',
            entry_id='entry',
            current_path=['children'],
            result_root=result,
            ref_result_root=result,
            archive=children,
            archive_root={'children': children},
            definition=Parent.children,
            visited_path=set(),
            current_depth=0,
            reader=reader,
        )
        asyncio.run(
            reader._resolve(node, RequestConfig(directive=DirectiveType.resolved))
        )

    assert result == {'children': []}
    assert resolution_count == 1


def test_archive_reader_plain_subsection_does_not_resolve_definition(monkeypatch):
    from nomad.graph.model import DirectiveType, RequestConfig
    from nomad.metainfo import MSection, Package, Quantity, SubSection

    m_package = Package(name='test_plain_subsection_definition')

    class Child(MSection):
        value = Quantity(type=str)

    class Parent(MSection):
        child = SubSection(section_def=Child)

    m_package.__init_metainfo__()
    original_m_resolved = MSection.m_resolved
    resolution_count = 0

    def count_child_resolution(definition):
        nonlocal resolution_count
        if definition is Child.m_def:
            resolution_count += 1
        return original_m_resolved(definition)

    monkeypatch.setattr(MSection, 'm_resolved', count_child_resolution)
    archive = {'child': {'value': 'test'}}
    result = {}

    with ArchiveReader({}) as reader:
        node = GraphNode(
            upload_id='upload',
            entry_id='entry',
            current_path=[],
            result_root=result,
            ref_result_root=result,
            archive=archive,
            archive_root=archive,
            definition=Parent.m_def,
            visited_path=set(),
            current_depth=0,
            reader=reader,
        )
        asyncio.run(reader._resolve(node, RequestConfig(directive=DirectiveType.plain)))

    assert result == archive
    assert resolution_count == 0


def test_archive_reader_direct_containers_preserve_filtered_results():
    archive = {
        'metadata': {
            'entry_id': 'entry',
            'upload_id': 'upload',
            'quantities': ['first', 'second'],
            'text_search_contents': [],
        }
    }

    with ArchiveReader(
        {
            'metadata': {
                'm_request': {
                    'directive': 'plain',
                    'include': ['does_not_exist'],
                }
            }
        }
    ) as reader:
        assert reader.sync_read(archive) == {}

    with ArchiveReader(
        {
            'metadata': {
                'm_request': {
                    'directive': 'plain',
                    'max_list_size': 1,
                }
            }
        }
    ) as reader:
        assert reader.sync_read(archive) == {
            'metadata': {
                'entry_id': 'entry',
                'upload_id': 'upload',
                'quantities': (
                    '__INTERNAL__:../uploads/upload/archive/entry#/metadata/quantities'
                ),
                'text_search_contents': [],
            }
        }

    with ArchiveReader(
        {
            'metadata': {
                'm_request': {
                    'directive': 'plain',
                    'include': ['text_search_contents'],
                    'depth': 1,
                }
            }
        }
    ) as reader:
        assert reader.sync_read(archive) == {
            'metadata': {
                'text_search_contents': (
                    '__INTERNAL__:../uploads/upload/archive/entry#/metadata/text_search_contents'
                )
            }
        }


def test_archive_reader_result_containers_do_not_alias_the_source_archive():
    archive = {
        'metadata': {
            'entry_id': 'entry',
            'upload_id': 'upload',
            'quantities': ['first', 'second'],
        }
    }

    with ArchiveReader({'metadata': '*'}) as reader:
        result = reader.sync_read(archive)

    result['metadata']['result_only'] = True
    assert 'result_only' not in archive['metadata']


def test_archive_reader_discards_container_pointer_after_reference_root_switch():
    archive = {
        'metadata': {'entry_id': 'entry', 'upload_id': 'upload'},
        'run': [
            {
                'system': [{'atoms': {'labels': ['H']}}],
                'calculation': [{'system_ref': '/run/0/system/0'}],
            }
        ],
    }
    query = {
        'run[0]': {
            'calculation[0]': {
                'm_request': {
                    'directive': 'resolved',
                    'include': ['system_ref'],
                }
            }
        }
    }

    with ArchiveReader(query) as reader:
        assert reader.sync_read(archive) == {
            'run': [
                {
                    'calculation': [
                        {
                            'system_ref': (
                                'uploads/upload/entries/entry/archive/run/0/system/0'
                            )
                        }
                    ]
                }
            ],
            'uploads': {
                'upload': {
                    'entries': {
                        'entry': {
                            'archive': {
                                'run': [{'system': [{'atoms': {'labels': ['H']}}]}]
                            }
                        }
                    }
                }
            },
        }


def test_archive_reader_json_and_any_quantities():
    """Ensure JSON and Any quantities with dict values are resolved correctly
    without treating inner dictionary keys as Section definitions and without
    triggering Section.__getattr__('type') lookups.
    """
    from unittest.mock import patch

    from nomad.graph.graph_reader import RequestConfig, ResultCursor
    from nomad.metainfo import MSection, Package, Quantity, SubSection
    from nomad.metainfo.data_type import JSON, Any

    m_package = Package(name='test_package_json_any')

    class SubSectionData(MSection):
        sub_prop = Quantity(type=str)

    class MySectionData(MSection):
        json_quantity = Quantity(type=JSON)
        any_quantity = Quantity(type=Any)
        str_quantity = Quantity(type=str)
        sub = SubSection(section_def=SubSectionData)

    m_package.__init_metainfo__()

    archive_data = {
        'json_quantity': {'k': 'v', 'nested': {'num': 123}},
        'any_quantity': {'any_k': [1, 2], 'flag': True},
        'str_quantity': 'test_str',
        'sub': {'sub_prop': 'sub_val'},
    }

    # 1. Full resolve: verify dict-valued JSON and Any quantities resolve properly
    result = {}
    with ArchiveReader({}) as reader:
        node = GraphNode(
            upload_id='upload_1',
            entry_id='entry_1',
            current_path=['data'],
            result_root=result,
            ref_result_root=result,
            archive=archive_data,
            archive_root={'data': archive_data},
            definition=MySectionData.m_def,
            visited_path=set(),
            current_depth=0,
            reader=reader,
            result_cursor=ResultCursor.from_path(result, ['data']),
        )
        asyncio.run(reader._resolve(node, RequestConfig()))

    data = result['data']
    assert data['json_quantity'] == {'k': 'v', 'nested': {'num': 123}}
    assert data['any_quantity'] == {'any_k': [1, 2], 'flag': True}
    assert data['str_quantity'] == 'test_str'
    assert data['sub']['sub_prop'] == 'sub_val'

    # 2. Stripping: verify container size limit stripping works for dict quantities
    result_stripped = {}
    with ArchiveReader({}) as reader:
        node = GraphNode(
            upload_id='upload_1',
            entry_id='entry_1',
            current_path=['data'],
            result_root=result_stripped,
            ref_result_root=result_stripped,
            archive=archive_data,
            archive_root={'data': archive_data},
            definition=MySectionData.m_def,
            visited_path=set(),
            current_depth=0,
            reader=reader,
            result_cursor=ResultCursor.from_path(result_stripped, ['data']),
        )
        asyncio.run(reader._resolve(node, RequestConfig(max_dict_size=1)))

    data_stripped = result_stripped['data']
    assert data_stripped['json_quantity'].startswith('__INTERNAL__:')
    assert data_stripped['any_quantity'].startswith('__INTERNAL__:')
    assert data_stripped['str_quantity'] == 'test_str'

    # 3. Regression test: Section.__getattr__ is never called with 'type'
    original_getattr = MSection.__getattr__
    type_lookups = []

    def mock_getattr(self, name):
        if name == 'type':
            type_lookups.append(name)
        return original_getattr(self, name)

    with patch.object(MSection, '__getattr__', mock_getattr):
        result_perf = {}
        with ArchiveReader({}) as reader:
            node = GraphNode(
                upload_id='upload_1',
                entry_id='entry_1',
                current_path=['data'],
                result_root=result_perf,
                ref_result_root=result_perf,
                archive=archive_data,
                archive_root={'data': archive_data},
                definition=MySectionData.m_def,
                visited_path=set(),
                current_depth=0,
                reader=reader,
                result_cursor=ResultCursor.from_path(result_perf, ['data']),
            )
            asyncio.run(reader._resolve(node, RequestConfig()))

        assert len(type_lookups) == 0, (
            f"Expected 0 'type' lookups on Section, got {len(type_lookups)}"
        )


def test_archive_reader_quantity_fast_path_preserves_definition_output():
    from nomad.graph.model import DirectiveType, MDefFormatType, RequestConfig
    from nomad.metainfo import MSection, Package, Quantity
    from nomad.metainfo.data_type import JSON

    m_package = Package(name='test_quantity_fast_path_definition')

    class MySection(MSection):
        payload = Quantity(type=JSON)

    m_package.__init_metainfo__()
    archive = {'payload': {'key': 'value'}}
    result = {}

    with ArchiveReader({}) as reader:
        node = GraphNode(
            upload_id='upload',
            entry_id='entry',
            current_path=['data'],
            result_root=result,
            ref_result_root=result,
            archive=archive,
            archive_root={'data': archive},
            definition=MySection.m_def,
            visited_path=set(),
            current_depth=0,
            reader=reader,
        )
        asyncio.run(
            reader._resolve(
                node,
                RequestConfig(
                    directive=DirectiveType.resolved,
                    m_def_format=MDefFormatType.short,
                ),
            )
        )

    assert result['data']['payload']['key'] == 'value'
    assert result['data']['payload']['m_def'].startswith(
        f'{MySection.payload.qualified_name()}@'
    )


def test_archive_reader_quantity_fast_path_uses_child_resolver_config():
    from nomad.graph.model import DirectiveType, RequestConfig
    from nomad.metainfo import MSection, Package, Quantity

    m_package = Package(name='test_quantity_fast_path_resolver_config')

    class MySection(MSection):
        main_author = Quantity(type=str)

    m_package.__init_metainfo__()
    archive = {'main_author': 'user-id'}
    result = {}
    resolver_properties = []

    with ArchiveReader({}) as reader:
        apply_resolver = reader._apply_resolver

        async def record_resolver(node, config):
            resolver_properties.append(config.property_name)
            return await apply_resolver(node, config)

        reader._apply_resolver = record_resolver
        node = GraphNode(
            upload_id='upload',
            entry_id='entry',
            current_path=['data'],
            result_root=result,
            ref_result_root=result,
            archive=archive,
            archive_root={'data': archive},
            definition=MySection.m_def,
            visited_path=set(),
            current_depth=0,
            reader=reader,
        )
        asyncio.run(
            reader._resolve(node, RequestConfig(directive=DirectiveType.resolved))
        )

    assert result == {'data': {'main_author': 'user-id'}}
    assert resolver_properties == ['main_author']


def test_archive_reader_quantity_fast_path_skips_resolver_for_lists():
    from nomad.graph.model import DirectiveType, RequestConfig, ResolveType
    from nomad.metainfo import MSection, Package, Quantity

    m_package = Package(name='test_quantity_fast_path_list_resolver')

    class MySection(MSection):
        identifiers = Quantity(type=str, shape=['*'])

    m_package.__init_metainfo__()
    archive = {'identifiers': ['first', 'second']}
    result = {}

    with ArchiveReader({}) as reader:

        async def unexpected_resolver(*args, **kwargs):
            pytest.fail('list-valued quantities must bypass the value resolver')

        reader._apply_resolver = unexpected_resolver
        node = GraphNode(
            upload_id='upload',
            entry_id='entry',
            current_path=['data'],
            result_root=result,
            ref_result_root=result,
            archive=archive,
            archive_root={'data': archive},
            definition=MySection.m_def,
            visited_path=set(),
            current_depth=0,
            reader=reader,
        )
        asyncio.run(
            reader._resolve(
                node,
                RequestConfig(
                    directive=DirectiveType.resolved,
                    resolve_type=ResolveType.user,
                ),
            )
        )

    assert result == {'data': {'identifiers': ['first', 'second']}}


def test_archive_reader_subsection_plain_wildcard_filters_unknown_properties():
    archive = {
        'metadata': {
            'entry_id': 'entry_1',
            'upload_id': 'upload_1',
            'quantities': ['q1', 'q2'],
            'not_in_definition': 'must not leak',
        },
        'run': [
            {
                'calculation': [
                    {'energy': {'total': {'value': 1.0}}},
                    {
                        'energy': {
                            'total': {'value': 2.0},
                            'nested_not_in_definition': 'plain child remains atomic',
                        },
                        'not_in_definition': 'must not leak',
                    },
                ]
            }
        ],
    }

    query = {
        'metadata': '*',
        'run[0]': {
            'calculation[-1]': '*',
        },
    }

    with ArchiveReader(query) as reader:
        result = reader.sync_read(archive)

    assert result == {
        'm_errors': [
            {
                'error_type': 'GENERAL',
                'message': 'Definition not_in_definition is not found.',
            }
        ],
        'metadata': {
            'entry_id': 'entry_1',
            'upload_id': 'upload_1',
            'quantities': ['q1', 'q2'],
        },
        'run': [
            {
                'calculation': [
                    None,
                    {
                        'energy': {
                            'total': {'value': 2.0},
                            'nested_not_in_definition': 'plain child remains atomic',
                        }
                    },
                ]
            }
        ],
    }

    # Verify no container aliasing
    result['metadata']['result_only'] = True
    result['run'][0]['calculation'][1]['result_only'] = True
    assert 'result_only' not in archive['metadata']
    assert 'result_only' not in archive['run'][0]['calculation'][1]
