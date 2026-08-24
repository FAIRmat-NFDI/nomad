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

import io
import json
import os
import zipfile
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from nomad import utils
from nomad.archive.utils import read_archive, to_json, write_archive
from nomad.bundles import (
    BundleExporter,
    BundleImporter,
    _get_package_for_section,
    _get_schema_packages_for_upload,
    _get_section_defs_for_upload,
)
from nomad.config import config
from nomad.datamodel import EntryArchive
from nomad.datamodel.context import ServerContext
from nomad.datamodel.data import EntryData
from nomad.datamodel.datamodel import EntryMetadata
from nomad.files import StreamedFile, StreamedFileSource
from nomad.metainfo import MSection, Package, Quantity, Section
from nomad.metainfo.util import MDefNotFound
from nomad.mongo.package import PackageDefinition
from nomad.processing import Entry, Upload
from nomad.schemas import _mongo_definition_cache, _mongo_package_cache, get_schema

# Test for helper functions


def test_get_section_defs_for_upload(non_empty_processed_with_temporal):
    indexed_definition_ids = set()
    entry_ids = [
        entry.entry_id for entry in non_empty_processed_with_temporal.successful_entries
    ]

    with non_empty_processed_with_temporal.entries_metadata(
        entry_ids=entry_ids
    ) as entries_metadata:
        for entry_metadata in entries_metadata:
            for section_def in entry_metadata.section_defs:
                indexed_definition_ids.add(section_def.definition_id)

    definitions = _get_section_defs_for_upload(
        non_empty_processed_with_temporal.upload_id
    )

    definition_ids = [definition.definition_id for definition in definitions]
    assert len(definition_ids) == len(set(definition_ids))
    assert set(definition_ids) == indexed_definition_ids

    qualified_names = {definition.qualified_name() for definition in definitions}
    assert EntryArchive.m_def.qualified_name() in qualified_names
    assert EntryMetadata.m_def.qualified_name() in qualified_names
    assert 'nomad.datamodel.results.Results' in qualified_names
    assert 'runschema.run.Run' in qualified_names


def test_get_section_defs_for_upload_with_custom_schema(mongo_module, monkeypatch):
    package = Package(name='tests.custom_bundle_schema')
    package.upload_id = 'test-upload'
    package.entry_id = 'test-schema-entry'

    class CustomSchema(MSection):
        value = Quantity(type=str)

    package.section_definitions.append(CustomSchema.m_def)
    PackageDefinition.create_new(package)

    monkeypatch.setattr(
        'nomad.bundles.Upload.get',
        lambda upload_id: SimpleNamespace(main_author=None),
    )
    monkeypatch.setattr(
        'nomad.bundles.search.search_iterator',
        lambda **kwargs: [
            {
                'section_defs': [
                    {
                        'definition_qualified_name': package.qualified_name()
                        + f'.{CustomSchema.m_def.name}',
                        'definition_id': CustomSchema.m_def.definition_id,
                    }
                ]
            }
        ],
    )

    definitions = _get_section_defs_for_upload('test-upload')

    assert len(definitions) == 1
    assert definitions[0].definition_id == CustomSchema.m_def.definition_id
    assert definitions[0].qualified_name() == (
        f'entry_id:{package.entry_id}.{CustomSchema.m_def.name}'
    )


def test_get_section_defs_for_upload_resolves_indexed_builtin_snapshot(monkeypatch):
    """Resolve a historical built-in by snapshot or migrate it to the current ID."""
    monkeypatch.setattr(
        'nomad.bundles.Upload.get',
        lambda upload_id: SimpleNamespace(main_author=None),
    )
    monkeypatch.setattr(
        'nomad.bundles.search.search_iterator',
        lambda **kwargs: [
            {
                'section_defs': [
                    {
                        'definition_qualified_name': 'nomad.datamodel.datamodel.EntryArchive',
                        'definition_id': 'indexed-definition-id',
                    },
                ]
            }
        ],
    )

    get_schema_calls = []

    def get_schema_or_raise(qualified_name, definition_id):
        get_schema_calls.append((qualified_name, definition_id))
        if qualified_name is not None:
            raise MDefNotFound('The loaded Python definition has a different ID.')
        return EntryArchive.m_def

    monkeypatch.setattr('nomad.bundles.get_schema', get_schema_or_raise)

    assert _get_section_defs_for_upload('test-upload') == [EntryArchive.m_def]
    assert get_schema_calls == [
        (EntryArchive.m_def.qualified_name(), 'indexed-definition-id'),
        (None, 'indexed-definition-id'),
    ]

    get_schema_calls.clear()

    def get_schema_without_snapshot(qualified_name, definition_id=None):
        get_schema_calls.append((qualified_name, definition_id))
        if (
            qualified_name == EntryArchive.m_def.qualified_name()
            and definition_id is None
        ):
            return EntryArchive.m_def
        if qualified_name is None:
            raise HTTPException(status_code=404, detail='Package not found.')
        raise MDefNotFound('The loaded Python definition has a different ID.')

    monkeypatch.setattr('nomad.bundles.get_schema', get_schema_without_snapshot)
    definition_id_aliases = {}

    assert _get_section_defs_for_upload('test-upload', definition_id_aliases) == [
        EntryArchive.m_def
    ]
    assert definition_id_aliases == {
        'indexed-definition-id': EntryArchive.m_def.definition_id
    }
    assert get_schema_calls == [
        (EntryArchive.m_def.qualified_name(), 'indexed-definition-id'),
        (None, 'indexed-definition-id'),
        (EntryArchive.m_def.qualified_name(), None),
    ]

    current_reference = 'entry_id:synthetic-entry.EntryArchive'
    assert BundleExporter._replace_definition_references(
        {
            'm_def': EntryArchive.m_def.qualified_name(),
            'm_def_id': 'indexed-definition-id',
            'section_defs': [
                {
                    'definition_qualified_name': EntryArchive.m_def.qualified_name(),
                    'definition_id': 'indexed-definition-id',
                }
            ],
        },
        {
            'indexed-definition-id': (
                current_reference,
                EntryArchive.m_def.definition_id,
            )
        },
    ) == {
        'm_def': current_reference,
        'm_def_id': EntryArchive.m_def.definition_id,
        'section_defs': [
            {
                'definition_qualified_name': EntryArchive.m_def.qualified_name(),
                'definition_id': EntryArchive.m_def.definition_id,
            }
        ],
    }


def test_get_section_defs_for_upload_does_not_fallback_for_custom_schema(monkeypatch):
    """Do not use built-in schema fallbacks for an upload-local schema."""
    qualified_name = 'entry_id:test-schema-entry.CustomSchema'
    definition_id = 'indexed-definition-id'
    monkeypatch.setattr(
        'nomad.bundles.Upload.get',
        lambda upload_id: SimpleNamespace(main_author=None),
    )
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

    def get_schema_or_raise(name, snapshot_id=None):
        if (name, snapshot_id) != (qualified_name, definition_id):
            pytest.fail('built-in schema fallback called for a custom schema')
        raise MDefNotFound('The custom schema name and ID do not match.')

    monkeypatch.setattr('nomad.bundles.get_schema', get_schema_or_raise)

    with pytest.raises(MDefNotFound):
        _get_section_defs_for_upload('test-upload')


def test_bundle_export_reuses_custom_schema_entry(monkeypatch):
    """Reuse an upload's schema entry and rewrite data references to that entry."""

    class BoundedReadBytesIO(io.BytesIO):
        def read(self, size=-1):
            assert size >= 0, 'archive source must not be read into memory at once'
            return super().read(size)

    # Create a test upload with custom schema
    package = Package(name='tests.custom_bundle_export_schema')
    package.upload_id = 'test-upload'
    package.entry_id = 'test-schema-entry'

    class CustomSchema(MSection):
        value = Quantity(type=str)

    package.section_definitions.append(CustomSchema.m_def)
    source_reference = (
        '../uploads/test-upload/archive/test-schema-entry'
        '#definitions/section_definitions/0'
    )
    archive_suffix = config.fs.archive_version_suffix
    archive_suffix = (
        archive_suffix[0] if isinstance(archive_suffix, list) else archive_suffix
    )
    data_archive_path = f'archive/test-data-entry-{archive_suffix}.msg'
    schema_archive_path = f'archive/test-schema-entry-{archive_suffix}.msg'
    archive_file = BoundedReadBytesIO()
    write_archive(
        archive_file,
        {
            'test-data-entry': {
                'data': {
                    'm_def': source_reference,
                    'm_def_id': CustomSchema.m_def.definition_id,
                    'value': 'test value',
                }
            }
        },
    )
    archive_file.seek(0)
    schema_archive_file = io.BytesIO()
    write_archive(
        schema_archive_file,
        {
            'test-schema-entry': {
                'definitions': package.m_to_dict(with_def_id=True),
            }
        },
    )
    schema_archive_file.seek(0)

    class UploadFilesStub:
        def files_to_bundle(self, _settings):
            yield StreamedFileSource(
                StreamedFile(
                    path=schema_archive_path,
                    src=schema_archive_file,
                    size=schema_archive_file.getbuffer().nbytes,
                )
            )
            yield StreamedFileSource(
                StreamedFile(
                    path=data_archive_path,
                    src=archive_file,
                    size=archive_file.getbuffer().nbytes,
                )
            )

    class UploadStub:
        upload_id = 'test-upload'
        process_running = False
        current_process = None
        upload_files = UploadFilesStub()

        successful_entries = [
            SimpleNamespace(
                entry_id='test-schema-entry',
                to_mongo=lambda: SimpleNamespace(
                    to_dict=lambda: {
                        '_id': 'test-schema-entry',
                        'upload_id': 'test-upload',
                        'mainfile': 'custom.schema.archive.json',
                    }
                ),
            )
        ]

        def to_mongo(self):
            return SimpleNamespace(to_dict=lambda: {'_id': self.upload_id})

    monkeypatch.setattr(
        'nomad.bundles._get_schema_packages_for_upload',
        lambda upload_id, definition_id_aliases=None: [package],
    )
    exporter = BundleExporter(
        upload=UploadStub(),
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,
        export_settings=config.bundle_export.default_settings.customize(
            dict(
                include_raw_files=False,
                include_archive_files=True,
                include_schemas=True,
            )
        ),
    )

    with zipfile.ZipFile(io.BytesIO(b''.join(exporter.export_bundle()))) as zf:
        assert not any(name.startswith('raw/schema_package_') for name in zf.namelist())
        assert schema_archive_path in zf.namelist()
        with read_archive(io.BytesIO(zf.read(data_archive_path))) as archive:
            exported_data = to_json(archive['test-data-entry'])['data']

    assert exported_data['m_def'] == (
        f'entry_id:test-schema-entry.{CustomSchema.m_def.name}'
    )
    assert exported_data['m_def_id'] == CustomSchema.m_def.definition_id
    assert source_reference not in exported_data.values()


def test_get_package_for_section():
    package = Package(name='tests.package_for_section')

    class PackageForSectionSchema(MSection):
        value = Quantity(type=str)

    package.section_definitions.append(PackageForSectionSchema.m_def)

    assert _get_package_for_section(PackageForSectionSchema.m_def) is package
    assert _get_package_for_section(EntryArchive.m_def) is EntryArchive.m_def.m_parent


def test_get_schema_packages_for_upload(monkeypatch):
    package_a = Package(name='tests.schema_package_a')
    package_a.upload_id = 'upload-a'
    package_a.entry_id = 'entry-a'
    package_b = Package(name='tests.schema_package_b')
    package_b.upload_id = 'upload-b'
    package_b.entry_id = 'entry-b'
    builtin_package = Package(name='tests.builtin_schema_package')

    class BuiltinSchema(MSection):
        value = Quantity(type=str)

    class PackageASchema(BuiltinSchema):
        value = Quantity(type=str)

    class PackageBSchema(MSection):
        value = Quantity(type=str)

    package_a.section_definitions.append(PackageASchema.m_def)
    package_b.section_definitions.append(PackageBSchema.m_def)
    builtin_package.section_definitions.append(BuiltinSchema.m_def)

    monkeypatch.setattr(
        'nomad.bundles._get_section_defs_for_upload',
        lambda upload_id, definition_id_aliases=None: [
            PackageBSchema.m_def,
            PackageASchema.m_def,
            PackageASchema.m_def,
        ],
    )

    assert _get_schema_packages_for_upload('test-upload') == [
        builtin_package,
        package_a,
        package_b,
    ]


# Test bundle export


def test_archive_preserving_bundle_export_as_stream(non_empty_processed_with_temporal):
    """Test for `include_archive_files=True`"""
    # Create (export) the bundle
    exporter = BundleExporter(
        upload=non_empty_processed_with_temporal,
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,  # not applicable for streaming
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


def test_bundle_export_includes_schema_raw_files(
    non_empty_processed_with_temporal, monkeypatch
):
    # Select a real upload-scoped schema referenced by the processed fixture
    definition = next(
        definition
        for definition in _get_section_defs_for_upload(
            non_empty_processed_with_temporal.upload_id
        )
        if definition.qualified_name()
        == 'simulationworkflowschema.geometry_optimization.GeometryOptimization'
    )
    package = _get_package_for_section(definition)
    assert isinstance(package, Package)

    # Keep this test focused on one schema package and its one rewritten reference
    monkeypatch.setattr(
        'nomad.bundles._get_schema_packages_for_upload',
        lambda upload_id, definition_id_aliases=None: [package],
    )

    exporter = BundleExporter(
        upload=non_empty_processed_with_temporal,
        export_as_stream=True,
        export_path=None,
        zipped=True,
        overwrite=False,
        export_settings=config.bundle_export.default_settings.customize(
            dict(
                include_raw_files=False,
                include_archive_files=True,
                include_schemas=True,
            )
        ),
    )

    with zipfile.ZipFile(io.BytesIO(b''.join(exporter.export_bundle()))) as zf:
        # The generated raw schema mainfile determines the synthetic entry ID
        schema_raw_path = next(
            name
            for name in zf.namelist()
            if name.startswith('raw/schema_package_') and name.endswith('.archive.json')
        )
        assert json.loads(zf.read(schema_raw_path)) == {
            'definitions': package.m_to_dict(
                with_out_meta=True,
                with_def_id=True,
                stable_references=True,
            )
        }
        schema_entry_id = utils.generate_entry_id(
            non_empty_processed_with_temporal.upload_id,
            schema_raw_path.removeprefix('raw/'),
        )

        # A schema entry needs both manifest metadata and a processed archive file
        bundle_info = json.loads(zf.read('bundle_info.json'))
        assert any(entry['_id'] == schema_entry_id for entry in bundle_info['entries'])
        archive_suffix = config.fs.archive_version_suffix
        archive_suffix = (
            archive_suffix[0] if isinstance(archive_suffix, list) else archive_suffix
        )
        schema_archive_path = f'archive/{schema_entry_id}-{archive_suffix}.msg'
        assert schema_archive_path in zf.namelist()

        # Inspect a data archive, rather than the synthetic schema archive itself
        archive_path = next(
            name
            for name in zf.namelist()
            if name.startswith('archive/') and schema_entry_id not in name
        )
        with read_archive(io.BytesIO(zf.read(archive_path))) as archive:
            archive_data = to_json(archive[list(archive.keys())[0]])

        def collect_m_def_references(value):
            if isinstance(value, dict):
                if 'm_def' in value:
                    yield value['m_def'], value.get('m_def_id')
                for item in value.values():
                    yield from collect_m_def_references(item)
            elif isinstance(value, list):
                for item in value:
                    yield from collect_m_def_references(item)

        m_def_references = list(collect_m_def_references(archive_data))

        # The section ID must resolve only to the synthetic entry shipped in this ZIP
        assert {
            m_def
            for m_def, m_def_id in m_def_references
            if m_def_id == definition.definition_id
        } == {f'entry_id:{schema_entry_id}.{definition.name}'}
        assert all(
            f'../uploads/{non_empty_processed_with_temporal.upload_id}/archive/'
            not in m_def
            for m_def, _ in m_def_references
        )

        # The synthetic archive must provide the schema package named by the reference
        with read_archive(io.BytesIO(zf.read(schema_archive_path))) as archive:
            schema_archive_data = to_json(archive[schema_entry_id])
        assert schema_archive_data['definitions'] == package.m_to_dict(
            with_out_meta=True,
            with_def_id=True,
            stable_references=True,
        )


# Tests for bundle transfer roundtrip


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


@pytest.mark.asyncio
async def test_schema_bundle_export_import_roundtrip(
    tmp_path, user1, temporal_worker, raw_files_function
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
        f'entry_id:{schema_entry.entry_id}.MySection'
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
