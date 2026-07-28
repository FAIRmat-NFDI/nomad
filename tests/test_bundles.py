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
from types import SimpleNamespace

from nomad.bundles import (
    BundleExporter,
    BundleImporter,
    _get_package_for_section,
    _get_schema_packages_for_upload,
    _get_section_defs_for_upload,
)
from nomad.config import config
from nomad.datamodel import EntryArchive
from nomad.datamodel.datamodel import EntryMetadata
from nomad.metainfo import MSection, Package, Quantity
from nomad.mongo.package import PackageDefinition
from nomad.processing import Entry, Upload

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

    class PackageASchema(MSection):
        value = Quantity(type=str)

    class PackageBSchema(MSection):
        value = Quantity(type=str)

    class BuiltinSchema(MSection):
        value = Quantity(type=str)

    package_a.section_definitions.append(PackageASchema.m_def)
    package_b.section_definitions.append(PackageBSchema.m_def)
    builtin_package.section_definitions.append(BuiltinSchema.m_def)

    monkeypatch.setattr(
        'nomad.bundles._get_section_defs_for_upload',
        lambda upload_id: [
            PackageBSchema.m_def,
            PackageASchema.m_def,
            PackageASchema.m_def,
            BuiltinSchema.m_def,
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


def test_bundle_export_includes_schema_raw_files(monkeypatch):
    # Prepare a fake upload
    package = Package(name='tests.bundle_export_schema')

    class BundleSchema(MSection):
        value = Quantity(type=str)

    class MongoStub:
        def to_dict(self):
            return {'_id': 'test-upload'}

    class UploadFilesStub:
        def files_to_bundle(self, _settings):
            return []

    class UploadStub:
        upload_id = 'test-upload'
        process_running = False
        current_process = None
        successful_entries = []
        upload_files = UploadFilesStub()

        def to_mongo(self):
            return MongoStub()

    package.section_definitions.append(BundleSchema.m_def)

    fake_upload = UploadStub()

    monkeypatch.setattr(
        'nomad.bundles._get_schema_packages_for_upload',
        lambda upload_id: [package],
    )

    exporter = BundleExporter(
        upload=fake_upload,
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
        schema_files = [
            name
            for name in zf.namelist()
            if name.startswith('raw/schema_package_') and name.endswith('.archive.json')
        ]
        assert len(schema_files) == 1
        assert json.loads(zf.read(schema_files[0])) == {
            'definitions': package.m_to_dict(with_out_meta=True)
        }


# Tests for bundle transfer roundtrip


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
