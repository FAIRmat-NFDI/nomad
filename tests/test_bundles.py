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
import io
import json
import os
import zipfile
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from msglc import FileInfo, combine

from nomad import search, utils
from nomad.archive.utils import read_archive, to_json, write_archive
from nomad.bundles import BundleExporter, BundleImporter
from nomad.config import config
from nomad.datamodel import EntryArchive
from nomad.datamodel.context import ServerContext
from nomad.datamodel.data import EntryData
from nomad.datamodel.metainfo.tabulartree import TabularTreeNodeInfo
from nomad.files import StreamedFile, StreamedFileSource, ZipFileSource
from nomad.metainfo import MSection, Package, Section
from nomad.metainfo.util import MDefNotFound
from nomad.mongo.package import PackageDefinition
from nomad.processing import Entry, Upload
from nomad.schemas import _mongo_definition_cache, _mongo_package_cache, get_schema


def test_archive_preserving_bundle_export_as_stream(non_empty_processed_with_temporal):
    """Test for `include_archive_files=True`"""
    exporter = BundleExporter(
        upload=non_empty_processed_with_temporal,
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,
        export_settings=config.bundle_export.default_settings,
    )
    assert exporter.export_settings.include_archive_files is True

    bundle_stream = exporter.export_bundle()
    assert bundle_stream is not None

    with zipfile.ZipFile(io.BytesIO(b''.join(bundle_stream))) as zf:
        names = set(zf.namelist())
        bundle_info = json.loads(zf.read('bundle_info.json'))

        expected_stable = {
            'bundle_info.json',
            'raw/examples_template/0.aux',
            'raw/examples_template/1.aux',
            'raw/examples_template/2.aux',
            'raw/examples_template/3.aux',
            'raw/examples_template/template.json',
        }
        assert expected_stable.issubset(names)

        archive_files = {name for name in names if name.startswith('archive/')}
        assert len(archive_files) == 1

        assert bundle_info['upload_id'] == non_empty_processed_with_temporal.upload_id
        assert bundle_info['export_settings']['include_raw_files'] is True
        assert bundle_info['export_settings']['include_archive_files'] is True
        assert bundle_info['export_settings']['include_datasets'] is True
        assert bundle_info['export_settings']['include_schemas'] is False
        assert len(bundle_info['entries']) == len(
            non_empty_processed_with_temporal.successful_entries
        )
        assert not any(
            name.startswith('raw/schema_package_') and name.endswith('.archive.json')
            for name in names
        )


# Tests for the bundle "export" phase


@pytest.mark.parametrize('zipped', [True, False])
def test_stale_schema_export_does_not_write_output(monkeypatch, tmp_path, zipped):
    export_path = tmp_path / 'new-directory' / 'bundle'
    exporter = BundleExporter(
        upload=SimpleNamespace(process_running=False, successful_entries=[]),
        export_as_stream=False,
        export_path=str(export_path),
        zipped=zipped,
        overwrite=False,
        export_settings=config.bundle_export.default_settings.customize(
            dict(include_schemas=True)
        ),
    )
    monkeypatch.setattr(
        exporter,
        '_get_used_schemas',
        lambda: (
            set(),
            {(TabularTreeNodeInfo.m_def.qualified_name(), 'stale-definition-id')},
        ),
    )

    with pytest.raises(MDefNotFound, match='stale-definition-id does not match'):
        exporter.export_bundle()

    assert not export_path.parent.exists()


@pytest.mark.parametrize(
    ('use_in_upload', 'use_installed'),
    [
        pytest.param(False, True, id='installed-only'),
        pytest.param(True, False, id='in-upload-only'),
        pytest.param(True, True, id='both'),
    ],
)
def test_get_used_schemas(monkeypatch, use_in_upload, use_installed):
    # Create the dummy test upload
    upload = SimpleNamespace(
        upload_id='test-upload',
        main_author='test-user',
        successful_entries=[SimpleNamespace(entry_id='schema-entry')],
    )
    section_defs = []
    if use_in_upload:
        section_defs.append(
            {
                'definition_qualified_name': 'entry_id:schema-entry.MySection',
                'definition_id': 'local-definition-id',
            }
        )
    if use_installed:
        section_defs.append(
            {
                'definition_qualified_name': TabularTreeNodeInfo.m_def.qualified_name(),
                'definition_id': TabularTreeNodeInfo.m_def.definition_id,
            }
        )

    def search_entries(**kwargs):
        assert kwargs['query'] == {'upload_id': upload.upload_id, 'processed': True}
        assert kwargs['user_id'] == upload.main_author
        return [
            {'section_defs': section_defs[:1]},
            {'section_defs': section_defs[1:]},
        ]

    monkeypatch.setattr('nomad.bundles.search.search_iterator', search_entries)

    exporter = BundleExporter(
        upload=upload,
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,
        export_settings=config.bundle_export.default_settings,
    )

    assert exporter._get_used_schemas() == (
        {'schema-entry'} if use_in_upload else set(),
        {
            (
                TabularTreeNodeInfo.m_def.qualified_name(),
                TabularTreeNodeInfo.m_def.definition_id,
            )
        }
        if use_installed
        else set(),
    )


def test_get_used_schemas_classifies_cross_upload_as_external(monkeypatch):
    upload = SimpleNamespace(
        upload_id='target-upload',
        main_author='test-user',
        successful_entries=[SimpleNamespace(entry_id='target-entry')],
    )
    qualified_name = 'entry_id:source-schema-entry.ExternalSection'
    definition_id = 'external-definition-id'
    monkeypatch.setattr(
        'nomad.bundles.search.search_iterator',
        lambda **kwargs: [
            {
                'section_defs': [
                    {
                        'definition_qualified_name': qualified_name,
                        'definition_id': definition_id,
                    }
                ]
            }
        ],
    )
    exporter = BundleExporter(
        upload=upload,
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,
        export_settings=config.bundle_export.default_settings,
    )

    assert exporter._get_used_schemas() == (
        set(),
        {(qualified_name, definition_id)},
    )


@pytest.mark.parametrize(
    ('use_in_upload', 'use_installed'),
    [
        pytest.param(True, False, id='in-upload-only'),
        pytest.param(False, True, id='installed-only'),
        pytest.param(True, True, id='both'),
    ],
)
def test_gather_and_export_schemas(monkeypatch, use_in_upload, use_installed):
    upload = SimpleNamespace(upload_id='test-upload', successful_entries=[])
    exporter = BundleExporter(
        upload=upload,
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,
        export_settings=config.bundle_export.default_settings,
    )
    local_ids = {'existing-schema-entry'} if use_in_upload else set()
    installed = (
        {
            (
                TabularTreeNodeInfo.m_def.qualified_name(),
                TabularTreeNodeInfo.m_def.definition_id,
            )
        }
        if use_installed
        else set()
    )
    monkeypatch.setattr(exporter, '_get_used_schemas', lambda: (local_ids, installed))

    files, entries, schema_entry_ids, schema_references = (
        exporter._gather_and_export_schemas()
    )
    assert local_ids <= schema_entry_ids

    if not use_installed:
        assert files == []
        assert entries == []
        assert schema_entry_ids == local_ids
        assert schema_references == {}
        return

    assert len(files) == 2
    assert len(entries) == 1
    entry = entries[0]
    assert entry.upload_id == upload.upload_id
    assert entry.parser_name == 'parsers/archive'
    assert entry.entry_id in schema_entry_ids
    assert schema_entry_ids == local_ids | {entry.entry_id}
    assert schema_references[TabularTreeNodeInfo.m_def.definition_id] == (
        f'entry_id:{entry.entry_id}.TabularTreeNodeInfo'
    )

    upload.successful_entries = [entry]
    reused_files, reused_entries, reused_ids, reused_references = (
        exporter._gather_and_export_schemas()
    )
    assert reused_files == []
    assert reused_entries == []
    assert reused_ids == schema_entry_ids
    assert reused_references == schema_references

    raw_file = next(files[0].to_streamed_files())
    archive_file = next(files[1].to_streamed_files())
    assert raw_file.path == f'raw/{entry.mainfile}'
    assert entry.entry_id == utils.generate_entry_id(upload.upload_id, entry.mainfile)
    assert archive_file.path.startswith(f'archive/{entry.entry_id}-')
    assert archive_file.path.endswith('.msg')

    package = TabularTreeNodeInfo.m_def.m_parent
    expected_package = package.m_to_dict(
        with_out_meta=True, with_def_id=True, stable_references=True
    )
    assert len(expected_package['section_definitions']) > 1
    assert json.load(raw_file.src) == {'definitions': expected_package}
    with read_archive(archive_file.src) as archive:
        exported = to_json(archive[entry.entry_id])
    assert exported['definitions'] == expected_package
    assert exported['metadata']['entry_id'] == entry.entry_id


def test_gather_and_export_cross_upload_schema(monkeypatch, mongo_function):
    package = Package(name='tests.cross_upload_bundle_schema')
    package.upload_id = 'source-upload'
    package.entry_id = 'source-schema-entry'

    class ExternalSection(MSection):
        pass

    package.section_definitions.append(ExternalSection.m_def)
    PackageDefinition.create_new(package)
    qualified_name = f'entry_id:{package.entry_id}.{ExternalSection.m_def.name}'

    exporter = BundleExporter(
        upload=SimpleNamespace(upload_id='target-upload', successful_entries=[]),
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,
        export_settings=config.bundle_export.default_settings,
    )
    monkeypatch.setattr(
        exporter,
        '_get_used_schemas',
        lambda: (
            set(),
            {(qualified_name, ExternalSection.m_def.definition_id)},
        ),
    )

    files, entries, schema_entry_ids, references = exporter._gather_and_export_schemas()

    assert len(files) == 2
    assert len(entries) == 1
    synthetic_entry = entries[0]
    assert synthetic_entry.upload_id == 'target-upload'
    assert synthetic_entry.entry_id in schema_entry_ids
    assert references[ExternalSection.m_def.definition_id] == (
        f'entry_id:{synthetic_entry.entry_id}.ExternalSection'
    )
    assert BundleExporter._replace_schema_references(
        {
            'm_def': qualified_name,
            'm_def_id': ExternalSection.m_def.definition_id,
        },
        references,
    ) == {
        'm_def': f'entry_id:{synthetic_entry.entry_id}.ExternalSection',
        'm_def_id': ExternalSection.m_def.definition_id,
    }

    raw_file = next(files[0].to_streamed_files())
    assert json.load(raw_file.src)['definitions']['name'] == package.name


@pytest.mark.parametrize(
    'archive_path', ['archive/data-v1.msg', 'archive-public.msg.msg']
)
def test_rewrite_installed_schema_references(monkeypatch, archive_path):
    exporter = BundleExporter(
        upload=SimpleNamespace(upload_id='test-upload', successful_entries=[]),
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,
        export_settings=config.bundle_export.default_settings,
    )
    monkeypatch.setattr(
        exporter,
        '_get_used_schemas',
        lambda: (
            {'local-schema'},
            {
                (
                    TabularTreeNodeInfo.m_def.qualified_name(),
                    TabularTreeNodeInfo.m_def.definition_id,
                )
            },
        ),
    )
    _, entries, _, references = exporter._gather_and_export_schemas()
    original = io.BytesIO()
    write_archive(
        original,
        {
            'data-entry': {
                'data': [
                    {
                        'm_def': TabularTreeNodeInfo.m_def.qualified_name(),
                        'm_def_id': TabularTreeNodeInfo.m_def.definition_id,
                        'value': 'example',
                    },
                    {
                        'm_def': 'entry_id:local-schema.MySection',
                        'm_def_id': 'local-id',
                    },
                ]
            }
        },
    )
    original_bytes = original.getvalue()
    original.seek(0)
    source = StreamedFileSource(
        StreamedFile(path=archive_path, src=original, size=len(original_bytes))
    )
    raw = StreamedFileSource(
        StreamedFile(path='raw/input.json', src=io.BytesIO(b'raw data'), size=8)
    )

    rewritten = list(
        exporter._rewrite_external_schema_references([raw, source], references)
    )
    assert len(rewritten) == 2
    raw_file = next(rewritten[0].to_streamed_files())
    assert raw_file.path == 'raw/input.json'
    assert raw_file.src.read() == b'raw data'
    streamed_file = next(rewritten[1].to_streamed_files())
    assert streamed_file.path == archive_path
    rewritten_bytes = streamed_file.src.getvalue()
    with read_archive(streamed_file.src) as archive:
        sections = to_json(archive['data-entry'])['data']
    assert sections[0]['m_def'] == (
        f'entry_id:{entries[0].entry_id}.TabularTreeNodeInfo'
    )
    assert sections[0]['m_def_id'] == TabularTreeNodeInfo.m_def.definition_id
    assert sections[0]['value'] == 'example'
    assert sections[1] == {
        'm_def': 'entry_id:local-schema.MySection',
        'm_def_id': 'local-id',
    }
    assert original_bytes != rewritten_bytes


def test_packed_raw_appends_without_reading_existing_members(monkeypatch, tmp_path):
    original = io.BytesIO()
    content = b'original raw content' * 100
    with zipfile.ZipFile(original, 'w', compression=zipfile.ZIP_DEFLATED) as raw_zip:
        raw_zip.writestr('folder/', b'')
        raw_zip.writestr('folder/input.txt', content)
    original_bytes = original.getvalue()
    with zipfile.ZipFile(io.BytesIO(original_bytes)) as raw_zip:
        member_data_end = raw_zip.start_dir
    original.seek(0)
    schema = b'{"definitions": {}}'
    original_open = zipfile.ZipFile.open

    def write_only_open(self, name, mode='r', *args, **kwargs):
        assert mode == 'w', 'Existing ZIP members must not be decompressed'
        return original_open(self, name, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(zipfile.ZipFile, 'open', write_only_open)
        patch.setattr(config.fs, 'tmp', str(tmp_path))
        packed = BundleExporter._packed_raw_with_schemas(
            StreamedFile(
                path='raw-public.plain.zip', src=original, size=len(original_bytes)
            ),
            [
                StreamedFile(
                    path='raw/schema_package_test.archive.json',
                    src=io.BytesIO(schema),
                    size=len(schema),
                )
            ],
        )

    with packed.src as output:
        # Local headers and compressed member bytes are preserved verbatim.
        assert output.read(member_data_end) == original_bytes[:member_data_end]
        output.seek(0)
        with zipfile.ZipFile(output) as raw_zip:
            assert raw_zip.read('folder/input.txt') == content
            assert raw_zip.read('schema_package_test.archive.json') == schema
            assert raw_zip.getinfo('folder/').is_dir()
            assert raw_zip.getinfo('folder/input.txt').compress_type == (
                zipfile.ZIP_DEFLATED
            )
    assert original.closed


@pytest.mark.parametrize('existing_member', [True, False])
def test_packed_raw_rejects_duplicate_schema_mainfiles(existing_member):
    schema_path = 'schema_package_test.archive.json'
    original = io.BytesIO()
    with zipfile.ZipFile(original, 'w') as raw_zip:
        if existing_member:
            raw_zip.writestr(schema_path, '{}')
    size = original.tell()
    original.seek(0)
    schema_files = [
        StreamedFile(path=f'raw/{schema_path}', src=io.BytesIO(b'{}'), size=2)
        for _ in range(1 if existing_member else 2)
    ]
    with pytest.raises(ValueError, match='Schema mainfile already exists'):
        BundleExporter._packed_raw_with_schemas(
            StreamedFile(path='raw-public.plain.zip', src=original, size=size),
            schema_files,
        )


@pytest.mark.parametrize(
    ('include_raw', 'include_archive'),
    [
        pytest.param(True, True, id='both'),
        pytest.param(False, True, id='archive-only'),
        pytest.param(True, False, id='raw-only'),
    ],
)
def test_published_bundle_packs_synthetic_schema(
    monkeypatch, tmp_path, include_raw, include_archive
):
    original_raw = io.BytesIO()
    with zipfile.ZipFile(original_raw, 'w') as raw_zip:
        raw_zip.writestr('input.archive.json', '{}')
    original_raw.seek(0)
    original_archive = io.BytesIO()
    combine(
        original_archive,
        [
            FileInfo(
                None,
                'data-entry',
                obj={
                    'data': {
                        'value': 42,
                        'm_def': TabularTreeNodeInfo.m_def.qualified_name(),
                        'm_def_id': TabularTreeNodeInfo.m_def.definition_id,
                    },
                    'metadata': {
                        'section_defs': [
                            {
                                'definition_qualified_name': (
                                    TabularTreeNodeInfo.m_def.qualified_name()
                                ),
                                'definition_id': TabularTreeNodeInfo.m_def.definition_id,
                                'used_directly': True,
                            }
                        ]
                    },
                },
            )
        ],
        backend='rust',
    )
    original_archive.seek(0)
    suffix = config.fs.archive_version_suffix
    suffix = suffix[0] if isinstance(suffix, list) else suffix
    archive_path = f'archive-public-{suffix}.msg.msg'

    class PublishedFiles:
        access = 'public'

        def files_to_bundle(self, settings):
            if settings.include_raw_files:
                yield StreamedFileSource(
                    StreamedFile(
                        path='raw-public.plain.zip',
                        src=original_raw,
                        size=original_raw.getbuffer().nbytes,
                    )
                )
            if settings.include_archive_files:
                yield StreamedFileSource(
                    StreamedFile(
                        path=archive_path,
                        src=original_archive,
                        size=original_archive.getbuffer().nbytes,
                    )
                )

        def raw_zip_file_object(self):
            return SimpleNamespace(os_path='raw-public.plain.zip')

        def msg_fp(self, access):
            assert access == 'public'
            return SimpleNamespace(os_path=archive_path)

    class PublishedUpload:
        upload_id = 'test-upload'
        main_author = 'test-user'
        published = True
        process_running = False
        upload_files = PublishedFiles()
        successful_entries = [
            SimpleNamespace(
                entry_id='data-entry',
                to_mongo=lambda: SimpleNamespace(to_dict=lambda: {}),
            )
        ]

        def to_mongo(self):
            return SimpleNamespace(to_dict=lambda: {'_id': self.upload_id})

    settings = config.bundle_export.default_settings.customize(
        dict(
            include_raw_files=include_raw,
            include_archive_files=include_archive,
            include_schemas=True,
        )
    )
    exporter = BundleExporter(PublishedUpload(), True, None, True, False, settings)
    monkeypatch.setattr(
        exporter,
        '_rewrite_external_schema_references',
        lambda *args: pytest.fail(
            'Published archives must be rewritten during packing'
        ),
    )
    monkeypatch.setattr(
        exporter,
        '_get_used_schemas',
        lambda: (
            set(),
            {
                (
                    TabularTreeNodeInfo.m_def.qualified_name(),
                    TabularTreeNodeInfo.m_def.definition_id,
                )
            },
        ),
    )

    bundle_bytes = b''.join(exporter.export_bundle())
    with zipfile.ZipFile(io.BytesIO(bundle_bytes)) as bundle:
        assert set(bundle.namelist()) == {
            'bundle_info.json',
            'raw-public.plain.zip',
            archive_path,
        }
        info = json.loads(bundle.read('bundle_info.json'))
        assert len(info['schema_entries']) == 1
        schema_entry_id = info['schema_entries'][0]
        assert any(entry.get('_id') == schema_entry_id for entry in info['entries'])
        with zipfile.ZipFile(
            io.BytesIO(bundle.read('raw-public.plain.zip'))
        ) as raw_zip:
            names = set(raw_zip.namelist())
            assert any(name.startswith('schema_package_') for name in names)
            assert ('input.archive.json' in names) is include_raw
        with read_archive(io.BytesIO(bundle.read(archive_path))) as archive:
            assert schema_entry_id in archive
            assert ('data-entry' in archive) is include_archive
            if include_archive:
                data = to_json(archive['data-entry'])
                reference = f'entry_id:{schema_entry_id}.TabularTreeNodeInfo'
                assert data['data']['m_def'] == reference
                assert data['data']['value'] == 42
                assert data['metadata']['section_defs'] == [
                    {
                        'definition_qualified_name': reference,
                        'definition_id': TabularTreeNodeInfo.m_def.definition_id,
                        'used_directly': True,
                    }
                ]
            schema = to_json(archive[schema_entry_id])
            assert schema['definitions']['name'] == (
                TabularTreeNodeInfo.m_def.m_parent.name
            )

    if include_raw and include_archive:
        # Exercise the real published-file importer: synthetic entries must be
        # readable from the packed archive after import, not just from the bundle.
        bundle_path = tmp_path / 'bundle.zip'
        bundle_path.write_bytes(bundle_bytes)
        monkeypatch.setattr(config.fs, 'public', str(tmp_path / 'public'))
        importer = BundleImporter(None, config.bundle_import.default_settings)
        importer.bundle = ZipFileSource(str(bundle_path))
        importer.upload = SimpleNamespace(
            upload_id='test-upload', published=True, embargo_length=0
        )
        try:
            importer._import_files()
            with importer.upload_files.read_archive(schema_entry_id) as archive:
                assert to_json(archive[schema_entry_id])['definitions']['name'] == (
                    TabularTreeNodeInfo.m_def.m_parent.name
                )
            with importer.upload_files.raw_file(
                next(name for name in names if name.startswith('schema_package_')),
                'rb',
            ) as raw_file:
                assert json.load(raw_file)['definitions']['name'] == (
                    TabularTreeNodeInfo.m_def.m_parent.name
                )
        finally:
            importer.close()
            importer.upload_files.close()


def test_packed_archive_uses_configured_temp_directory(monkeypatch, tmp_path):
    original = io.BytesIO()
    combine(
        original,
        [FileInfo(None, f'entry-{i}', obj={'data': {'value': i}}) for i in range(3)],
        backend='rust',
    )
    size = original.tell()
    original.seek(0)
    monkeypatch.setattr(config.fs, 'tmp', str(tmp_path))
    packed = BundleExporter._packed_archive_with_schemas(
        'archive-public.msg.msg',
        StreamedFile(path='archive-public.msg.msg', src=original, size=size),
        [],
        {},
    )

    with packed.src as output:
        temp_path = output.name
        assert os.path.dirname(temp_path) == str(tmp_path)
        assert os.fstat(output.fileno()).st_size == packed.size
        with read_archive(temp_path) as archive:
            for i in range(3):
                assert to_json(archive[f'entry-{i}']) == {'data': {'value': i}}
    assert not os.path.exists(temp_path)
    assert original.closed


@pytest.mark.parametrize('definition_id', [['a'], {'id': 'a'}, 42, None])
def test_schema_reference_rewriting_preserves_non_string_ids_in_json(definition_id):
    json_quantity = {
        'm_def': 'user-value',
        'm_def_id': definition_id,
        'definition_qualified_name': 'user-value',
        'definition_id': definition_id,
    }
    archive = {
        'data': {
            'm_def': 'installed.Section',
            'm_def_id': 'schema-id',
            'json_quantity': json_quantity,
        },
        'metadata': {
            'section_defs': [
                {
                    'definition_qualified_name': 'installed.Section',
                    'definition_id': 'schema-id',
                }
            ]
        },
    }

    rewritten = BundleExporter._replace_schema_references(
        archive, {'schema-id': 'entry_id:synthetic.Section'}
    )

    assert rewritten['data']['json_quantity'] == json_quantity
    assert rewritten['data']['m_def'] == 'entry_id:synthetic.Section'
    assert rewritten['metadata']['section_defs'][0] == {
        'definition_qualified_name': 'entry_id:synthetic.Section',
        'definition_id': 'schema-id',
    }


def test_section_defs_references_are_rewritten_by_definition_id():
    original = {
        'metadata': {
            'section_defs': [
                {
                    'definition_qualified_name': name,
                    'definition_id': definition_id,
                    'used_directly': used_directly,
                }
                for name, definition_id, used_directly in [
                    ('nomad.datamodel.data.ArchiveSection', 'copied-id', False),
                    ('entry_id:external.Schema', 'external-id', True),
                    ('entry_id:local.Schema', 'local-id', True),
                    ('unbundled.Schema', 'unbundled-id', False),
                ]
            ]
        }
    }
    references = {
        'copied-id': 'entry_id:synthetic.ArchiveSection',
        'external-id': 'entry_id:synthetic-external.Schema',
    }

    rewritten = BundleExporter._replace_schema_references(original, references)
    for before, after in zip(
        original['metadata']['section_defs'], rewritten['metadata']['section_defs']
    ):
        assert after == {
            **before,
            'definition_qualified_name': references.get(
                before['definition_id'], before['definition_qualified_name']
            ),
        }
    assert original['metadata']['section_defs'][0]['definition_qualified_name'] == (
        'nomad.datamodel.data.ArchiveSection'
    )


def test_in_upload_schema_reference_is_not_rewritten():
    exporter = BundleExporter(
        upload=SimpleNamespace(upload_id='test-upload'),
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,
        export_settings=config.bundle_export.default_settings,
    )
    original = io.BytesIO()
    local_data = {
        'm_def': 'entry_id:local-schema.MySection',
        'm_def_id': 'local-definition-id',
        'value': 12,
    }
    write_archive(original, {'data-entry': {'data': local_data}})
    size = original.tell()
    original.seek(0)
    source = StreamedFileSource(
        StreamedFile(path='archive/data-entry-v1.msg', src=original, size=size)
    )

    rewritten = list(
        exporter._rewrite_external_schema_references(
            [source],
            {TabularTreeNodeInfo.m_def.definition_id: 'entry_id:synthetic.Section'},
        )
    )
    with read_archive(next(rewritten[0].to_streamed_files()).src) as archive:
        assert to_json(archive['data-entry'])['data'] == local_data


def test_import_schema_packages_skips_legacy_package_without_snapshot_ids(
    monkeypatch,
):
    """Import legacy schema entries without registering unavailable snapshots."""
    entry_id = 'legacy-schema-entry'
    legacy_package = {
        'name': 'legacy_package',
        'section_definitions': [{'name': 'LegacySection'}],
    }
    importer = BundleImporter(None, config.bundle_import.default_settings)
    importer.upload = SimpleNamespace(upload_id='test-upload')
    importer.upload_files = SimpleNamespace(
        read_archive=lambda requested_entry_id: nullcontext(
            {requested_entry_id: {'definitions': legacy_package}}
        )
    )
    importer._bundle_info = {'schema_entries': [entry_id]}
    monkeypatch.setattr(
        PackageDefinition,
        'has_package',
        lambda package_id: pytest.fail('legacy package snapshot was registered'),
    )

    importer._import_schema_packages([SimpleNamespace(entry_id=entry_id)])


def test_archive_preserving_bundle_roundtrip(
    non_empty_processed_with_temporal, tmp_path
):
    """A bundle with archive files should import without reprocessing."""

    # Export bundle to file
    exporter = BundleExporter(
        upload=non_empty_processed_with_temporal,
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,
        export_settings=config.bundle_export.default_settings,
    )
    bundle_bytes = b''.join(exporter.export_bundle())

    bundle_zip_path = tmp_path / 'bundle.zip'
    bundle_zip_path.write_bytes(bundle_bytes)

    extracted_bundle_path = tmp_path / 'bundle'
    with zipfile.ZipFile(bundle_zip_path) as zf:
        zf.extractall(extracted_bundle_path)

    non_empty_processed_with_temporal.delete_upload_local()

    # Import bundle and check
    importer = BundleImporter(
        None,
        config.bundle_import.default_settings.customize(
            dict(
                trigger_processing=False,
                delete_bundle_on_success=False,
                delete_bundle_on_fail=False,
            )
        ),
    )
    importer.open(str(extracted_bundle_path))
    try:
        imported_upload = importer.create_upload_skeleton()
        importer.import_bundle(imported_upload, True)
    finally:
        importer.close()

    imported_upload = Upload.get(imported_upload.upload_id)
    assert imported_upload.upload_id == non_empty_processed_with_temporal.upload_id

    imported_entries = list(Entry.objects(upload_id=imported_upload.upload_id))
    assert len(imported_entries) == len(
        non_empty_processed_with_temporal.successful_entries
    )

    imported_files = set()
    for dirpath, _, filenames in os.walk(imported_upload.upload_files.os_path):
        for filename in filenames:
            imported_files.add(
                os.path.relpath(
                    os.path.join(dirpath, filename),
                    imported_upload.upload_files.os_path,
                )
            )

    expected_stable = {
        'raw/examples_template/0.aux',
        'raw/examples_template/1.aux',
        'raw/examples_template/2.aux',
        'raw/examples_template/3.aux',
        'raw/examples_template/template.json',
    }
    assert expected_stable.issubset(imported_files)

    archive_files = {name for name in imported_files if name.startswith('archive/')}
    assert len(archive_files) > 0


@pytest.mark.parametrize('reexport_published', [False, True])
@pytest.mark.asyncio
async def test_schema_bundle_export_import_roundtrip(
    tmp_path, user1, temporal_worker, raw_files_function, reexport_published
):
    """Custom and built-in schemas should resolve from the imported bundle."""
    with open('tests/data/metainfo/schema.archive.json') as f:
        schema_data = json.load(f)
    schema_data['definitions']['section_definitions'][0]['quantities'].append(
        {
            'name': 'referenced_section',
            'type': {
                'type_kind': 'reference',
                'type_data': (
                    f'{EntryData.m_def.qualified_name()}'
                    f'@{EntryData.m_def.definition_id}'
                ),
            },
        }
    )
    source_zip = tmp_path / 'schema-upload.zip'
    with zipfile.ZipFile(source_zip, 'w') as zf:
        zf.writestr('schema.archive.json', json.dumps(schema_data))
        zf.write('tests/data/metainfo/inter-entry.archive.json', 'data.archive.json')

    source_upload = Upload.create(
        upload_id='schema_bundle_roundtrip', main_author=user1
    )
    source_upload.save()
    async with temporal_worker():
        handle = await source_upload._start_process_upload_workflow(
            file_operations=[
                dict(
                    op='ADD',
                    path=str(source_zip),
                    target_dir='',
                    temporary=False,
                )
            ]
        )
        await handle.result()
    source_upload.reload()
    source_entries = {
        entry.mainfile: entry
        for entry in Entry.objects(upload_id=source_upload.upload_id)
    }
    schema_entry = source_entries['schema.archive.json']
    data_entry = source_entries['data.archive.json']

    bundle_path = tmp_path / 'bundle'
    BundleExporter(
        upload=source_upload,
        export_as_stream=False,
        export_path=str(bundle_path),
        zipped=False,
        overwrite=False,
        export_settings=config.bundle_export.default_settings.customize(
            dict(include_schemas=True)
        ),
    ).export_bundle()

    synthetic_raw_files = sorted(
        (bundle_path / 'raw').glob('schema_package_*.archive.json')
    )
    synthetic_packages = [
        json.loads(path.read_text())['definitions'] for path in synthetic_raw_files
    ]
    synthetic_package_names = {package['name'] for package in synthetic_packages}

    assert (bundle_path / 'raw' / 'schema.archive.json').is_file()
    assert 'test_package_name' not in synthetic_package_names
    assert EntryData.m_def.m_parent.name in synthetic_package_names
    bundle_info = json.loads((bundle_path / 'bundle_info.json').read_text())
    assert schema_entry.entry_id in bundle_info['schema_entries']
    assert data_entry.entry_id not in bundle_info['schema_entries']
    assert len(bundle_info['schema_entries']) == len(synthetic_packages) + 1

    data_archive_path = next(
        (bundle_path / 'archive').glob(f'{data_entry.entry_id}-*.msg')
    )
    with read_archive(str(data_archive_path)) as archive:
        exported_data_archive = to_json(archive[data_entry.entry_id])

    custom_definition_id = exported_data_archive['data']['m_def_id']
    assert exported_data_archive['data']['m_def'] == (
        '../upload/schema_bundle_roundtrip/raw/schema.archive.json'
        '#/definitions/section_definitions/0'
    )

    schema_archive_path = next(
        (bundle_path / 'archive').glob(f'{schema_entry.entry_id}-*.msg')
    )
    with read_archive(str(schema_archive_path)) as archive:
        exported_schema_archive = to_json(archive[schema_entry.entry_id])

    def collect_definition_references(value):
        if isinstance(value, dict):
            if 'm_def' in value:
                yield value['m_def'], value.get('m_def_id')
            for item in value.values():
                yield from collect_definition_references(item)
        elif isinstance(value, list):
            for item in value:
                yield from collect_definition_references(item)

    builtin_reference = next(
        m_def
        for m_def, m_def_id in collect_definition_references(exported_schema_archive)
        if m_def_id == Section.m_def.definition_id
    )
    assert builtin_reference.startswith('entry_id:')

    exported_section_defs = exported_data_archive['metadata']['section_defs']
    builtin_section_def = next(
        definition
        for definition in exported_section_defs
        if definition['definition_id'] == EntryData.m_def.definition_id
    )
    builtin_schema_entry_id = next(
        entry['_id']
        for entry in bundle_info['entries']
        if entry['mainfile'].startswith('schema_package_')
        and json.loads((bundle_path / 'raw' / entry['mainfile']).read_text())[
            'definitions'
        ]['name']
        == EntryData.m_def.m_parent.name
    )
    assert builtin_section_def['definition_qualified_name'] == (
        f'entry_id:{builtin_schema_entry_id}.EntryData'
    )
    assert builtin_section_def['used_directly'] is False

    package_ids = set()
    for archive_path in (bundle_path / 'archive').glob('*.msg'):
        with read_archive(str(archive_path)) as archive:
            for entry_id in archive.keys():
                entry_archive = to_json(archive[entry_id])
                if definitions := entry_archive.get('definitions'):
                    package_ids.add(definitions['definition_id'])

    assert package_ids
    PackageDefinition.objects(snapshot_package_id__in=package_ids).delete()
    _mongo_definition_cache.clear()
    _mongo_package_cache.clear()
    source_upload.delete_upload_local()

    importer = BundleImporter(
        None,
        config.bundle_import.default_settings.customize(
            dict(
                trigger_processing=False,
                delete_bundle_on_success=False,
                delete_bundle_on_fail=False,
            )
        ),
    )
    importer.open(str(bundle_path))
    try:
        imported_upload = importer.create_upload_skeleton()
        importer.import_bundle(imported_upload, True)
    finally:
        importer.close()

    assert all(PackageDefinition.has_package(package_id) for package_id in package_ids)

    _mongo_definition_cache.clear()
    _mongo_package_cache.clear()
    imported_upload = Upload.get(source_upload.upload_id)
    assert imported_upload.upload_id == source_upload.upload_id
    imported_entry_ids = {
        entry.entry_id for entry in Entry.objects(upload_id=imported_upload.upload_id)
    }
    assert {schema_entry.entry_id, data_entry.entry_id} <= imported_entry_ids

    with imported_upload.upload_files.read_archive(data_entry.entry_id) as archive:
        imported_archive_dict = to_json(archive[data_entry.entry_id])
        imported_archive = EntryArchive.m_from_dict(
            imported_archive_dict,
            m_context=ServerContext(imported_upload),
        )

    assert imported_archive_dict['metadata']['upload_id'] == source_upload.upload_id
    assert imported_archive_dict['metadata']['entry_id'] == data_entry.entry_id
    assert imported_archive_dict['data'] == exported_data_archive['data']
    assert imported_archive_dict['metadata']['section_defs'] == exported_section_defs
    indexed_entry = search.search(
        owner='all',
        user_id=imported_upload.main_author,
        query={'entry_id': data_entry.entry_id},
    ).data[0]
    assert sorted(
        indexed_entry['section_defs'],
        key=lambda definition: definition['definition_id'],
    ) == sorted(
        exported_section_defs, key=lambda definition: definition['definition_id']
    )
    assert imported_archive.data.m_def.definition_id == custom_definition_id
    reference_quantity = next(
        quantity
        for quantity in imported_archive.data.m_def.quantities
        if quantity.name == 'referenced_section'
    )
    reference_target = reference_quantity.type.target_section_def.m_resolved()
    assert reference_target.name == EntryData.m_def.name
    assert reference_target.definition_id == EntryData.m_def.definition_id
    assert EntryData.m_def.definition_id in {
        base_section.definition_id
        for base_section in imported_archive.data.m_def.base_sections
    }
    assert get_schema(builtin_reference, Section.m_def.definition_id).name == (
        Section.m_def.name
    )

    # When plugins are available, an imported upload may be reprocessed. Its
    # raw installed references must not cause duplicate synthetic entries.
    async with temporal_worker():
        if reexport_published:
            await asyncio.to_thread(imported_upload.publish_upload)
            await imported_upload.await_workflows()
        handle = await imported_upload._start_process_upload_workflow(
            path_filter='data.archive.json'
        )
        await handle.result()
    imported_upload.reload()
    assert imported_upload.failed_entries_count == 0
    existing_entries = list(imported_upload.successful_entries)
    existing_ids = {entry.entry_id for entry in existing_entries}
    exporter = BundleExporter(
        imported_upload,
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,
        export_settings=config.bundle_export.default_settings.customize(
            dict(include_schemas=True)
        ),
    )
    files, new_entries, schema_ids, references = exporter._gather_and_export_schemas()
    assert files == []
    assert new_entries == []
    assert builtin_schema_entry_id in schema_ids
    assert references[EntryData.m_def.definition_id] == (
        f'entry_id:{builtin_schema_entry_id}.EntryData'
    )
    # Close the preview streams; export_bundle creates its own schema streams.
    for file in files:
        for streamed_file in file.to_streamed_files():
            streamed_file.src.close()

    with zipfile.ZipFile(io.BytesIO(b''.join(exporter.export_bundle()))) as bundle:
        info = json.loads(bundle.read('bundle_info.json'))
        ids = [entry['_id'] for entry in info['entries']]
        assert len(ids) == len(set(ids))
        assert set(ids) == existing_ids | {entry.entry_id for entry in new_entries}
        assert len(bundle.namelist()) == len(set(bundle.namelist()))
        if reexport_published:
            raw_name = next(name for name in bundle.namelist() if name.endswith('.zip'))
            with zipfile.ZipFile(io.BytesIO(bundle.read(raw_name))) as raw:
                assert len(raw.namelist()) == len(set(raw.namelist()))
            archive_name = next(
                name for name in bundle.namelist() if name.endswith('.msg')
            )
            archive_source = io.BytesIO(bundle.read(archive_name))
        else:
            archive_name = next(
                name
                for name in bundle.namelist()
                if name.startswith(f'archive/{data_entry.entry_id}-')
            )
            archive_source = io.BytesIO(bundle.read(archive_name))
        with read_archive(archive_source) as archive:
            metadata = to_json(archive[data_entry.entry_id])['metadata']
        assert (
            next(
                definition['definition_qualified_name']
                for definition in metadata['section_defs']
                if definition['definition_id'] == EntryData.m_def.definition_id
            )
            == f'entry_id:{builtin_schema_entry_id}.EntryData'
        )
