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

import hashlib
import io
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from fastapi import HTTPException, status
from packaging import version

from nomad import datamodel, search, utils
from nomad.app.v1.models import MetadataRequired
from nomad.archive.utils import read_archive, to_json, write_archive
from nomad.config import config
from nomad.config.models.config import BundleExportSettings, BundleImportSettings
from nomad.files import (
    BrowsableFileSource,
    CombinedFileSource,
    DiskFileSource,
    FileSource,
    PathObject,
    PublicUploadFiles,
    StagingUploadFiles,
    StandardJSONDecoder,
    StreamedFile,
    StreamedFileSource,
    UploadFiles,
    ZipFileSource,
    create_zipstream,
    json_to_streamed_file,
    zipfile,
)
from nomad.files import bundle_info_filename as BUNDLE_INFO_FILENAME
from nomad.metainfo import Definition, Package, Quantity, Reference, Section, SubSection
from nomad.metainfo.util import MDefNotFound
from nomad.mongo.package import PackageDefinition
from nomad.processing.base import ProcessStatus
from nomad.processing.data import Entry, Upload, mongo_entry_metadata
from nomad.public_storage import (
    choose_pack_fs,
    complete_published_write,
    detect_published_access,
)
from nomad.schemas import get_schema


@dataclass(frozen=True)
class _SchemaPackageExport:
    """A schema package and its synthetic entry representation in a bundle."""

    package: Package
    package_dict: dict[str, Any]
    raw_path: str
    entry_id: str


def _get_section_defs_for_upload(
    upload_id: str, definition_id_aliases: dict[str, str] | None = None
) -> list[Section]:
    """Get section definitions from all entries in an upload."""
    upload = Upload.get(upload_id)
    definitions_by_id: dict[str, Section] = {}

    entries = search.search_iterator(
        owner='all',
        query={'upload_id': upload_id, 'processed': True},
        user_id=upload.main_author,
        required=MetadataRequired(
            include=[
                'entry_id',
                'section_defs.definition_qualified_name',
                'section_defs.definition_id',
            ]
        ),
    )

    for entry in entries:
        for section_def in entry.get('section_defs', []):
            qualified_name = section_def.get('definition_qualified_name')
            if not qualified_name:
                continue

            definition_id = section_def.get('definition_id')
            try:
                definition = get_schema(qualified_name, definition_id)
            except MDefNotFound:
                if qualified_name.startswith('entry_id:'):
                    raise
                # A built-in definition may have been indexed with an older snapshot
                # than the currently loaded Python definition. Resolve that exact
                # snapshot from MongoDB so the bundle remains version-independent.
                try:
                    definition = get_schema(None, definition_id)
                except (MDefNotFound, HTTPException):
                    # Old installations did not persist all built-in snapshots. In
                    # that case, migrate the archive reference to the installed
                    # definition so the exported bundle remains resolvable.
                    definition = get_schema(qualified_name)
                    if definition_id_aliases is not None and definition_id:
                        definition_id_aliases[definition_id] = definition.definition_id
            if isinstance(definition, Section):
                definitions_by_id.setdefault(
                    definition.definition_id,
                    definition,
                )

    return list(definitions_by_id.values())


def _get_package_for_section(definition: Definition) -> Package | None:
    """Get schema package for a section definition."""
    parent = definition.m_parent
    while parent is not None and not isinstance(parent, Package):
        parent = getattr(parent, 'm_parent', None)
    return parent if isinstance(parent, Package) else None


def _get_package_dependencies(package: Package) -> set[Package]:
    """Collect packages referenced by definitions in a schema package."""
    dependencies: set[Package] = set()

    def add_definition(definition: Definition) -> None:
        if dependency := _get_package_for_section(definition):
            if dependency is not package:
                dependencies.add(dependency)

    for content in package.m_all_contents():
        if isinstance(content, SubSection):
            add_definition(content.sub_section.m_resolved())
        elif isinstance(content, Quantity) and isinstance(content.type, Reference):
            add_definition(content.type.target_section_def.m_resolved())
        elif isinstance(content, Section):
            for base_section in content.base_sections:
                add_definition(base_section)
        if isinstance(content, Definition):
            for category in content.categories:
                add_definition(category)

    return dependencies


def _get_schema_packages_for_upload(
    upload_id: str, definition_id_aliases: dict[str, str] | None = None
) -> list[Package]:
    """Collect schema packages and their transitive dependencies for an upload."""
    packages_by_key: dict[tuple[str | None, str | None, str | None], Package] = {}
    packages_to_visit: list[Package] = []

    for definition in _get_section_defs_for_upload(upload_id, definition_id_aliases):
        package = _get_package_for_section(definition)
        if package is not None:
            packages_to_visit.append(package)

    while packages_to_visit:
        package = packages_to_visit.pop()
        key = (package.upload_id, package.entry_id, package.name)
        if key in packages_by_key:
            continue
        packages_by_key[key] = package
        packages_to_visit.extend(_get_package_dependencies(package))

    return [
        packages_by_key[key]
        for key in sorted(
            packages_by_key, key=lambda item: tuple(v or '' for v in item)
        )
    ]


class BundleExporter:
    def __init__(
        self,
        upload: Upload,
        export_as_stream: bool,
        export_path: str | None,
        zipped: bool,
        overwrite: bool,
        export_settings: BundleExportSettings,
    ) -> None:
        """
        Class for exporting an upload as a *bundle*. Bundles are used to export and import
        uploads between different NOMAD installations. After instantiating a BundleExporter,
        use the `export_bundle` method to do the actual exporting.

        Arguments:
            upload:
                The upload to export
            export_as_stream: If the bundle should be exported as a stream, rather than saved
                to a file or folder. You must specify either `export_as_stream = True` or `export_path`.
                Further, `zipped` must be set to True when exporting as a stream.
                The stream is returned by the `export_bundle` function.
            export_path: Defines the output path, when exporting to disk rather than as a stream.
                You must specify either `export_as_stream = True` or `export_path`. Set to
                None if exporting as a stream.
            zipped: if the bundle should be zipped. Set to False to export the bundle to disk
                as an uncompressed folder. If exporting as a stream, zipped must be set to True.
            overwrite:
                If the target file/folder should be overwritten by this operation. Not
                applicable if `export_as_stream` is True.
            export_settings:
                Settings for controlling the bundle content. See the
                `config.BundeExportSettings` for applicable options.
                NOTE: the dictionary must specify a *complete* set of options.
        """
        BundleExporter.check_export_settings(export_settings)
        self.upload = upload
        self.export_as_stream = export_as_stream
        self.export_path = export_path
        self.zipped = zipped
        self.overwrite = overwrite
        self.export_settings = export_settings
        self._schema_packages: list[Package] | None = None
        self._schema_exports: list[_SchemaPackageExport] | None = None
        self._definition_id_aliases: dict[str, str] = {}

    @classmethod
    def check_export_settings(cls, export_settings: BundleExportSettings) -> None:
        assert (
            export_settings.include_archive_files or export_settings.include_raw_files
        ), 'Export must include the archive files or the raw files, or both'

    def export_bundle(self) -> Iterable[bytes] | None:
        # Safety checks
        if self.export_as_stream:
            assert self.export_path is None, (
                'Cannot have `export_path` set when exporting as a stream.'
            )
            assert self.zipped, (
                'Must have `zipped` set to True when exporting as stream.'
            )
        else:
            assert self.export_path is not None, (
                'You must specify either `export_as_stream = True` or `export_path`.'
            )
            assert self.overwrite or not os.path.exists(self.export_path), (
                '`export_path` already exists.'
            )

        assert not self.upload.process_running or self.upload.current_process in (
            'publish_externally',
            '_publish_externally',
        ), 'Upload is being processed.'

        file_source = CombinedFileSource(self._get_file_sources())

        # Export as stream
        if self.export_as_stream:
            return create_zipstream(file_source.to_streamed_files())

        # Create parent dir
        os.makedirs(os.path.dirname(os.path.abspath(self.export_path)), exist_ok=True)

        if self.zipped:
            file_source.to_zipfile(self.export_path, self.overwrite)
        else:
            file_source.to_disk(
                self.export_path, move_files=False, overwrite=self.overwrite
            )

        return None

    def _get_file_sources(self) -> Iterable[FileSource]:
        """Generator which yields all the `FileSource` objects to pack in the bundle."""
        # 1. The bundle info json
        bundle_info = self._create_bundle_info()
        yield StreamedFileSource(
            json_to_streamed_file(bundle_info, BUNDLE_INFO_FILENAME)
        )

        schema_exports = self._get_schema_exports()

        # 2. Synthetic schema archive files and their processed archive entries.
        for schema_export in schema_exports:
            yield StreamedFileSource(
                json_to_streamed_file(
                    {'definitions': schema_export.package_dict}, schema_export.raw_path
                )
            )
            yield StreamedFileSource(self._schema_archive_streamed_file(schema_export))

        # 3. Files from the upload dir. Rewriting happens only in the bundle stream.
        definition_references = self._schema_definition_references(schema_exports)
        for file_source in self.upload.upload_files.files_to_bundle(
            self.export_settings
        ):
            if definition_references:
                yield from self._rewritten_archive_file_sources(
                    file_source, definition_references
                )
            else:
                yield file_source

    def _get_schema_exports(self) -> list[_SchemaPackageExport]:
        """Build synthetic entries for packages without an entry in this upload."""
        if self._schema_exports is not None:
            return self._schema_exports

        self._schema_exports = []
        if not self.export_settings.include_schemas:
            return self._schema_exports

        existing_entry_ids = {
            entry.entry_id for entry in self.upload.successful_entries
        }
        for package in self._get_schema_packages():
            if (
                package.m_is_custom_package
                and package.upload_id == self.upload.upload_id
                and package.entry_id in existing_entry_ids
            ):
                continue

            package_dict = package.m_to_dict(
                with_out_meta=True,
                with_def_id=True,
                stable_references=True,
            )
            # Use `sort_keys` to ensure stable hashes from dictionary key order.
            package_json = json.dumps(package_dict, sort_keys=True).encode()
            package_hash = hashlib.md5(package_json).hexdigest()[:8]
            raw_path = f'raw/schema_package_{package_hash}.archive.json'
            self._schema_exports.append(
                _SchemaPackageExport(
                    package=package,
                    package_dict=package_dict,
                    raw_path=raw_path,
                    entry_id=utils.generate_entry_id(
                        self.upload.upload_id, raw_path.removeprefix('raw/')
                    ),
                )
            )

        return self._schema_exports

    def _get_schema_packages(self) -> list[Package]:
        """Return and cache all schema packages needed by this bundle."""
        if self._schema_packages is None:
            self._schema_packages = _get_schema_packages_for_upload(
                self.upload.upload_id, self._definition_id_aliases
            )
        return self._schema_packages

    def _schema_definition_references(
        self, schema_exports: Iterable[_SchemaPackageExport]
    ) -> dict[str, tuple[str, str]]:
        """Map section IDs to existing or synthetic bundle schema entries."""
        package_entry_ids = self._schema_package_entry_ids(schema_exports)

        references: dict[str, tuple[str, str]] = {}
        for package in self._get_schema_packages():
            if not (entry_id := package_entry_ids.get(id(package))):
                continue
            package_name = package.qualified_name()
            for definition in package.m_all_contents(include_self=False):
                if isinstance(definition, Section):
                    definition_name = definition.qualified_name().removeprefix(
                        f'{package_name}.'
                    )
                    references[definition.definition_id] = (
                        f'entry_id:{entry_id}.{definition_name}',
                        definition.definition_id,
                    )
        for (
            old_definition_id,
            current_definition_id,
        ) in self._definition_id_aliases.items():
            if reference := references.get(current_definition_id):
                references[old_definition_id] = reference
        return references

    def _schema_package_entry_ids(
        self, schema_exports: Iterable[_SchemaPackageExport]
    ) -> dict[int, str]:
        """Map schema packages to their existing or synthetic bundle entries."""
        if not self.export_settings.include_schemas:
            return {}

        package_entry_ids = {
            id(schema_export.package): schema_export.entry_id
            for schema_export in schema_exports
        }
        existing_entry_ids = {
            entry.entry_id for entry in self.upload.successful_entries
        }
        for package in self._get_schema_packages():
            if (
                package.m_is_custom_package
                and package.upload_id == self.upload.upload_id
                and package.entry_id in existing_entry_ids
            ):
                package_entry_ids[id(package)] = cast(str, package.entry_id)
        return package_entry_ids

    def _schema_archive_streamed_file(
        self, schema_export: _SchemaPackageExport
    ) -> StreamedFile:
        """Create the processed archive file for one synthetic schema entry."""
        metadata = datamodel.EntryMetadata(
            upload_id=self.upload.upload_id,
            entry_id=schema_export.entry_id,
            mainfile=schema_export.raw_path.removeprefix('raw/'),
            parser_name='parsers/archive',
            processed=True,
            entry_name=schema_export.package.name,
            entry_type='Schema',
        )
        return self._archive_streamed_file(
            f'archive/{schema_export.entry_id}-{self._archive_version_suffix()}.msg',
            {
                schema_export.entry_id: {
                    'metadata': metadata.m_to_dict(with_def_id=True),
                    'definitions': schema_export.package_dict,
                }
            },
        )

    def _rewritten_archive_file_sources(
        self,
        file_source: FileSource,
        definition_references: dict[str, tuple[str, str]],
    ) -> Iterable[FileSource]:
        """Yield upload files while replacing schema references in archive messages."""
        for streamed_file in file_source.to_streamed_files():
            is_archive_file = streamed_file.path.startswith(
                ('archive/', 'archive-')
            ) and streamed_file.path.endswith('.msg')
            if not is_archive_file:
                yield StreamedFileSource(streamed_file)
                continue

            with (
                streamed_file.src as archive_file,
                read_archive(archive_file) as archive,
            ):
                archive_data = {
                    entry_id: self._replace_definition_references(
                        to_json(archive[entry_id]), definition_references
                    )
                    for entry_id in archive.keys()
                }
            yield StreamedFileSource(
                self._archive_streamed_file(streamed_file.path, archive_data)
            )

    @staticmethod
    def _replace_definition_references(
        value: Any, definition_references: dict[str, tuple[str, str]]
    ) -> Any:
        """Replace serialized ``m_def`` values using their stable definition IDs."""
        if isinstance(value, list):
            return [
                BundleExporter._replace_definition_references(
                    item, definition_references
                )
                for item in value
            ]
        if not isinstance(value, dict):
            return value

        replaced = {
            key: BundleExporter._replace_definition_references(
                item, definition_references
            )
            for key, item in value.items()
        }
        if definition_id := replaced.get('m_def_id'):
            if replacement := definition_references.get(definition_id):
                replaced['m_def'], replaced['m_def_id'] = replacement
        if (
            'definition_qualified_name' in replaced
            and (definition_id := replaced.get('definition_id'))
            and (replacement := definition_references.get(definition_id))
        ):
            replaced['definition_id'] = replacement[1]
        return replaced

    @staticmethod
    def _archive_streamed_file(path: str, data: dict[str, Any]) -> StreamedFile:
        """Serialize archive data into a streamed NOMAD archive file."""
        archive_file = io.BytesIO()
        write_archive(archive_file, data)
        size = archive_file.tell()
        archive_file.seek(0)
        return StreamedFile(path=path, src=archive_file, size=size)

    @staticmethod
    def _archive_version_suffix() -> str:
        """Return the configured suffix used for newly written archive files."""
        suffix = config.fs.archive_version_suffix
        return suffix[0] if isinstance(suffix, list) else suffix

    def _create_bundle_info(self) -> dict[str, Any]:
        """Create the bundle_info.json manifest used to recreate the upload.

        The manifest stores source deployment metadata, export settings, upload
        metadata, successful entry metadata, and optionally dataset definitions.
        """
        bundle_info: dict[str, Any] = dict(
            upload_id=self.upload.upload_id,
            source=config.meta.dict(),  # Information about the source system, i.e. this NOMAD installation
            export_settings=self.export_settings.dict(),
            upload=self.upload.to_mongo().to_dict(),
            entries=[
                entry.to_mongo().to_dict() for entry in self.upload.successful_entries
            ],
        )
        for schema_export in self._get_schema_exports():
            schema_entry = Entry(
                upload_id=self.upload.upload_id,
                entry_id=schema_export.entry_id,
                mainfile=schema_export.raw_path.removeprefix('raw/'),
                parser_name='parsers/archive',
                process_status=ProcessStatus.SUCCESS,
            )
            bundle_info['entries'].append(schema_entry.to_mongo().to_dict())
        if self.export_settings.include_schemas:
            bundle_info['schema_entries'] = sorted(
                set(self._schema_package_entry_ids(self._get_schema_exports()).values())
            )
        # Handle datasets
        dataset_ids: set[str] = set()
        for entry_dict in bundle_info['entries']:
            entry_datasets = entry_dict.get('datasets')
            if entry_datasets:
                if not self.export_settings.include_datasets:
                    entry_dict['datasets'] = None
                else:
                    dataset_ids.update(entry_datasets)

        if self.export_settings.include_datasets:
            bundle_info['datasets'] = [
                datamodel.Dataset.m_def.a_mongo.get(dataset_id=dataset_id).m_to_dict()
                for dataset_id in sorted(dataset_ids)
            ]

        return bundle_info


class BundleImporter:
    def __init__(
        self,
        user: datamodel.User | None,
        import_settings: BundleImportSettings,
        embargo_length: int | None = None,
    ) -> None:
        """
        Class for importing an upload from a *bundle*.

        Arguments:
            user:
                The user requesting the import. Used to check permissions. Of omitted, no
                permission checks are done.
            import_settings:
                Settings for controlling the bundle content. See the
                `BundleImportSettings` for applicable options.
                NOTE: the dictionary must specify a complete set of options.
            embargo_length:
                Used to set the embargo length in months. If set to None, the value will be imported
                from the bundle. The value should be between 0 and 36. A value of 0 means
                no embargo.
        """
        self.user = user
        self.import_settings = import_settings
        self.embargo_length = embargo_length

        # Internals
        self.bundle_path: str | None = None
        self.bundle: BrowsableFileSource | None = None
        self.upload: Upload | None = None
        self.upload_files: UploadFiles | None = None
        self._bundle_info: dict[str, Any] | None = None

    @classmethod
    def looks_like_a_bundle(cls, path: str) -> bool:
        """Fast method to make a (very shallow) check if the object specified by `path` looks like a valid bundle."""
        assert os.path.exists(path), f'Path not found: {path}'
        if os.path.isfile(path):
            try:
                if not path.lower().endswith('.zip'):
                    return False
                zip_file = zipfile.ZipFile(path, 'r')
                zip_file.getinfo(BUNDLE_INFO_FILENAME)
                return True
            except Exception:
                return False
        return os.path.isfile(os.path.join(path, BUNDLE_INFO_FILENAME))

    def check_api_permissions(self) -> None:
        """
        Checks if the specified user is allowed to import a bundle via the api. Raises a
        HTTPException if not. This is a quick check, which does not require the bundle to be opened.
        """
        if not self.user:
            return  # No permission checks

        is_admin = self.user.is_admin

        if not is_admin:
            for k, v in self.import_settings.model_dump().items():
                if v != config.bundle_import.default_settings.model_dump().get(k):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f'Changing the setting {k} requires an admin user',
                    )

    def open(self, bundle_path: str) -> None:
        """Open a bundle from a directory or zip file path."""
        self.bundle_path = bundle_path
        if os.path.isdir(bundle_path):
            self.bundle = DiskFileSource(bundle_path)
        else:
            assert zipfile.is_zipfile(bundle_path), (
                '`path` must define a folder or a zipfile.'
            )
            self.bundle = ZipFileSource(bundle_path)

    def create_upload_skeleton(self) -> Upload:
        """
        Creates an upload "skeleton" to be populated with the imported data.
        """
        # Some basic sanity checks
        is_admin = not self.user or self.user.is_admin
        is_oasis = (
            not is_admin
            and self.user.is_oasis_admin
            and config.bundle_import.allow_bundles_from_oasis
        )

        if is_oasis and not config.bundle_import.allow_unpublished_bundles_from_oasis:
            if not self.bundle_info.get('upload', {}).get('publish_time'):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f'Bundles uploaded from an oasis must be published in the oasis first.',
                )

        _keys_exist(
            self.bundle_info,
            ('upload_id', 'upload.main_author'),
            'Missing key in bundle_info.json: {key}',
        )
        upload_id = self.bundle_info['upload_id']
        main_author = self.bundle_info['upload']['main_author']
        try:
            Upload.get(upload_id)
            assert False, f'Upload with id {upload_id} already exists'
        except KeyError:
            pass
        main_author_user = datamodel.User.get(user_id=main_author)
        assert main_author_user is not None, f'Invalid main_author: {main_author}'
        # Create the Upload
        self.upload = Upload.create(upload_id=upload_id, main_author=main_author_user)
        return self.upload

    def import_bundle(self, upload: Upload, running_locally: bool) -> str | None:
        """
        Import the data to the provided upload skeleton (the most costly step of the
        import process). Should only be invoked from a @process or a @process_local method on
        Upload (to get proper handling of process_status, errors, blocking other processes, etc).
        """
        self.upload = upload
        logger = self.upload.get_logger(bundle_path=self.bundle_path)
        current_time = datetime.now(timezone.utc)
        new_datasets: list[datamodel.Dataset] = []
        dataset_id_mapping: dict[str, str] = {}
        entry_data_to_index: list[datamodel.EntryArchive] = []  # Data to index in ES
        try:
            self._check_bundle_and_settings(running_locally)
            self._import_upload_mongo_data(current_time)
            if self.import_settings.include_datasets:
                new_datasets, dataset_id_mapping = self._import_datasets()
            entries = self._import_entries_mongo_data(current_time, dataset_id_mapping)
            self._import_files()
            self._import_schema_packages(entries)
            self.bundle.close()
            entry_data_to_index = self._get_entry_data_to_index(entries)
            # Everything looks good - save to mongo.
            self.upload.save()
            for entry in entries:
                entry.save()
            # Index ES
            self._index_search(entry_data_to_index)

            if self.import_settings.delete_bundle_on_success:
                self.delete_bundle()

            # Possibly reprocess
            if self.import_settings.trigger_processing:
                return self._reprocess_upload()
            return None

        except Exception as e:
            logger.error('could not import bundle', exc_info=e)
            self.bundle.close()
            if self.import_settings.delete_bundle_on_fail:
                self.delete_bundle()
            if self.import_settings.delete_upload_on_fail:
                # Delete everything
                self.upload.delete_upload_local()  # Will also delete files, entries and remove from elastic search
                if new_datasets:
                    for dataset in new_datasets:
                        dataset.a_mongo.delete()
                return (
                    ProcessStatus.DELETED
                )  # Used by the framework when running as a @process
            else:
                # Just ensure the upload is deleted from search
                with utils.timer(logger, 'upload deleted from index'):
                    search.delete_upload(self.upload.upload_id, refresh=True)
                raise e

    def close(self) -> None:
        """Close the currently opened bundle, if any."""
        if self.bundle:
            self.bundle.close()

    def delete_bundle(self) -> None:
        """Deletes the bundle file, and optionally, it's parent folder (if empty)."""
        parent_folder = os.path.dirname(self.bundle_path)

        if os.path.exists(self.bundle_path):
            PathObject(self.bundle_path).delete()

        if (
            self.import_settings.delete_bundle_include_parent_folder
            and os.path.isdir(parent_folder)
            and not os.listdir(parent_folder)
        ):
            PathObject(parent_folder).delete()

    @property
    def bundle_info(self) -> dict[str, Any]:
        """Load and cache the bundle manifest from `bundle_info.json`."""
        if not self._bundle_info:
            assert self.bundle, 'Must open a bundle before getting the bundle info.'
            with self.bundle.open(BUNDLE_INFO_FILENAME, 'rt') as f:
                self._bundle_info = json.load(f, cls=StandardJSONDecoder)
        return self._bundle_info

    def _check_bundle_and_settings(self, running_locally: bool) -> None:
        """Perform various initial sanity checks."""
        # Sanity checks of the settings
        assert not (self.import_settings.trigger_processing and running_locally), (
            'Cannot use `trigger_processing` when running locally.'
        )
        # Sanity checks of the bundle
        required_keys_root_level = (
            'upload_id',
            'source.version',
            'source.commit',
            'source.deployment',
            'source.deployment_url',
            'export_settings.include_raw_files',
            'export_settings.include_archive_files',
            'export_settings.include_datasets',
            'upload._id',
            'upload.main_author',
            'upload.upload_create_time',
            'upload.process_status',
            'upload.license',
            'upload.embargo_length',
            'entries',
        )

        _keys_exist(
            self.bundle_info,
            required_keys_root_level,
            'Missing key in bundle_info.json: {key}',
        )

        # Check version
        try:
            bundle_nomad_version = self.bundle_info['source']['version']
            assert version.parse(bundle_nomad_version) >= version.parse(
                config.bundle_import.required_nomad_version
            ), (
                f'Bundle created in NOMAD version {bundle_nomad_version}, '
                f'required at least {config.bundle_import.required_nomad_version}'
            )
        except Exception:
            assert False, 'Bad bundle version'

    def _import_upload_mongo_data(self, current_time: datetime) -> None:
        upload_dict = self.bundle_info['upload']
        assert (
            self.upload.upload_id == self.bundle_info['upload_id'] == upload_dict['_id']
        ), 'Inconsisten upload id information'
        published = upload_dict.get('publish_time') is not None
        if published:
            assert self.bundle_info['entries'], (
                'Upload published but no entries in bundle_info.json'
            )
        # Check user references
        _check_user_ids([upload_dict['main_author']], 'Invalid main_author: {id}')
        _check_user_ids(
            upload_dict.get('coauthors', []), 'Invalid coauthor reference: {id}'
        )
        _check_user_ids(
            upload_dict.get('reviewers', []), 'Invalid reviewers reference: {id}'
        )
        # Define which keys we think okay to copy from the bundle
        upload_keys_to_copy = [
            'upload_name',
            'main_author',
            'coauthors',
            'reviewers',
            'embargo_length',
            'license',
            'from_oasis',
            'oasis_deployment_url',
        ]
        if self.import_settings.keep_original_timestamps:
            upload_keys_to_copy.extend(
                (
                    'upload_create_time',
                    'publish_time',
                )
            )
        try:
            # Update the upload with data from the json, and validate it
            update = {
                k: upload_dict[k] for k in upload_keys_to_copy if k in upload_dict
            }
            self.upload.modify(**update)
            self.upload.validate()
        except Exception as e:
            assert False, 'Bad upload json data: ' + str(e)
        # Manage timestamps
        current_time_plus_tolerance = current_time + timedelta(minutes=2)
        if published and not self.import_settings.keep_original_timestamps:
            self.upload.publish_time = current_time
        for timestamp in (
            self.upload.upload_create_time,
            self.upload.last_update,
            self.upload.complete_time,
            self.upload.publish_time,
        ):
            assert (
                timestamp is None
                or timestamp.replace(tzinfo=timezone.utc) < current_time_plus_tolerance
            ), 'Timestamp is in the future'
        # Manage source info
        if self.import_settings.set_from_oasis:
            self.upload.from_oasis = True
            source_deployment_url = self.bundle_info['source']['deployment_url']
            assert source_deployment_url, 'No source deployment_url defined'
            if not self.upload.oasis_deployment_url:
                # Note, if oasis_deployment_url is set in the bundle_info, we keep this
                # value as it is, since it indicates that the upload has been imported from
                # somewhere else originally (i.e. source_deployment_url would not be the
                # original source)
                self.upload.oasis_deployment_url = source_deployment_url
        # Validate embargo settings
        if self.embargo_length is not None:
            self.upload.embargo_length = (
                self.embargo_length
            )  # Importing with different embargo
        assert (
            isinstance(self.upload.embargo_length, int)
            and 0 <= self.upload.embargo_length <= 36
        ), 'Invalid embargo_length, must be between 0 and 36 months'

    def _import_datasets(self) -> tuple[list[datamodel.Dataset], dict[str, str]]:
        """Creates datasets from the bundle."""
        required_keys_datasets = ('dataset_id', 'dataset_name', 'user_id')

        assert 'datasets' in self.bundle_info, (
            'Missing datasets definition in bundle_info.json'
        )
        datasets = self.bundle_info['datasets']
        new_datasets: list[datamodel.Dataset] = []
        dataset_id_mapping: dict[
            str, str
        ] = {}  # Map from old to new id (usually the same)
        for dataset_dict in datasets:
            _keys_exist(
                dataset_dict,
                required_keys_datasets,
                'Missing key in dataset definition: {key}',
            )
            _check_user_ids(
                [dataset_dict['user_id']], 'Invalid dataset creator id: {id}'
            )
            dataset_id = dataset_dict['dataset_id']
            try:
                existing_dataset = datamodel.Dataset.m_def.a_mongo.get(
                    user_id=dataset_dict['user_id'],
                    dataset_name=dataset_dict['dataset_name'],
                )
                # Dataset by the given dataset_name and user_id already exists
                dataset_id_mapping[dataset_id] = existing_dataset.dataset_id
                # Note, it may be that a dataset with the same dataset_name and creator
                # is created in both environments. In that case, we consider them
                # to be the "same" dataset, even if they do not have the same dataset_id.
                # Thus, in that case the dataset id needs to be translated.
                assert not existing_dataset.doi, (
                    f'Matched dataset {existing_dataset.dataset_id} has a DOI, cannot be updated'
                )
            except KeyError:
                # Completely new dataset, create it
                new_dataset = datamodel.Dataset(**dataset_dict)
                new_dataset.a_mongo.save()
                new_datasets.append(new_dataset)
                dataset_id_mapping[dataset_id] = dataset_id

        return new_datasets, dataset_id_mapping

    def _import_entries_mongo_data(
        self, current_time: datetime, dataset_id_mapping: dict[str, str]
    ) -> list[Entry]:
        """Creates mongo entries from the data in the bundle_info"""
        required_keys_entry_level = (
            '_id',
            'upload_id',
            'mainfile',
            'parser_name',
            'process_status',
            'entry_create_time',
        )

        entries = []
        for entry_dict in self.bundle_info['entries']:
            _keys_exist(
                entry_dict, required_keys_entry_level, 'Missing key for entry: {key}'
            )
            assert (
                entry_dict['process_status'] in ProcessStatus.STATUSES_NOT_PROCESSING
            ), 'Invalid entry `process_status`'
            # Check referential consistency
            assert entry_dict['upload_id'] == self.upload.upload_id, (
                'Mismatching upload_id in entry definition'
            )
            expected_entry_id = utils.generate_entry_id(
                self.upload.upload_id,
                entry_dict['mainfile'],
                entry_dict.get('mainfile_key'),
            )
            assert entry_dict['_id'] == expected_entry_id, (
                'Provided entry id does not match generated value'
            )
            _check_user_ids(
                entry_dict.get('entry_coauthors', []),
                'Invalid entry_coauthor reference: {id}',
            )

            # Instantiate an entry object from the json, and validate it
            entry_keys_to_copy = list(mongo_entry_metadata)
            entry_keys_to_copy.extend(
                (
                    'upload_id',
                    'errors',
                    'warnings',
                    'last_status_message',
                    'current_process',
                    'complete_time',
                    'worker_hostname',
                    'celery_task_id',
                )
            )
            try:
                update = {
                    k: entry_dict[k] for k in entry_keys_to_copy if k in entry_dict
                }
                update['entry_id'] = entry_dict['_id']
                if not self.import_settings.keep_original_timestamps:
                    update['entry_create_time'] = current_time
                entry: Entry = Entry.create(**update)
                entry.process_status = entry_dict['process_status']
                entry.validate()
            except Exception as e:
                assert False, 'Bad entry json data: ' + str(e)
            # Instantiate an EntryMetadata object to validate the format
            try:
                if self.import_settings.include_datasets:
                    entry_datasets = entry_dict.get('datasets')
                    if entry_datasets:
                        entry.datasets = [
                            dataset_id_mapping[id] for id in entry_datasets
                        ] or None
                else:
                    entry.datasets = None
                entry.mongo_metadata(self.upload)
            except Exception as e:
                assert False, 'Invalid entry metadata: ' + str(e)
            entries.append(entry)
        return entries

    def _import_files(self) -> None:
        try:
            cls = PublicUploadFiles if self.upload.published else StagingUploadFiles
            assert not cls.base_folder_for(self.upload.upload_id).exists(), (
                'Upload folder already exists'
            )
            self.upload_files = cls(self.upload.upload_id, create=True)

            for file_source in self.upload_files.files_from_bundle(
                self.bundle, self.import_settings
            ):
                if isinstance(file_source, DiskFileSource):
                    # When importing from an extracted bundle folder, copy the contents
                    # of `raw/` and `archive/` into the upload root instead of nesting
                    # them again as `raw/raw` or `archive/archive`.
                    FileSource.to_disk(
                        file_source, self.upload_files.os_path, overwrite=True
                    )
                else:
                    file_source.to_disk(self.upload_files.os_path, overwrite=True)

            if self.upload.published and self.embargo_length is not None:
                # Repack the upload
                PublicUploadFiles(self.upload.upload_id).re_pack(
                    with_embargo=self.embargo_length > 0
                )
            if self.upload.published:
                published_files = PublicUploadFiles(self.upload.upload_id)
                complete_published_write(
                    published_files.os_path,
                    published_files.upload_id,
                    detect_published_access(published_files.os_path, choose_pack_fs()),
                )
        except Exception:
            # Something went wrong. Delete the files and re-raise the original exception
            if self.upload_files:
                self.upload_files.delete()
            raise

    def _get_entry_data_to_index(
        self, entries: list[Entry]
    ) -> list[datamodel.EntryArchive]:
        entry_data_to_index = []
        if self.import_settings.include_archive_files:
            for entry in entries:
                try:
                    entry_metadata = entry.full_entry_metadata(self.upload)
                    entry_data_to_index.append(
                        cast(datamodel.EntryArchive, entry_metadata.m_parent)
                    )
                except Exception as e:
                    assert False, 'Invalid metadata in archive entry: ' + str(e)
            self.upload_files.close()  # Because full_entry_metadata reads the archive files.
        return entry_data_to_index

    def _import_schema_packages(self, entries: list[Entry]) -> None:
        """Register schema snapshots contained in imported archive entries."""
        if not self.import_settings.include_archive_files:
            return
        assert self.upload_files is not None

        entries_by_id = {entry.entry_id: entry for entry in entries}
        schema_entry_ids = self.bundle_info.get('schema_entries', [])
        assert all(entry_id in entries_by_id for entry_id in schema_entry_ids), (
            'Invalid schema entry in bundle_info.json'
        )

        for entry_id in schema_entry_ids:
            entry = entries_by_id[entry_id]
            try:
                with self.upload_files.read_archive(entry.entry_id) as archive:
                    entry_archive = archive[entry.entry_id]
                    if 'definitions' not in entry_archive:
                        continue
                    package_dict = to_json(entry_archive['definitions'])
            except KeyError:
                continue

            package_id = package_dict.get('definition_id')
            section_ids = [
                section.get('definition_id')
                for section in package_dict.get('section_definitions', [])
            ]
            if (
                not package_id
                or not section_ids
                or any(section_id is None for section_id in section_ids)
            ):
                # Older bundles do not carry snapshot IDs in serialized schema
                # packages and retain their previous import behaviour.
                continue

            if PackageDefinition.has_package(package_id):
                continue

            PackageDefinition(
                snapshot_package_id=package_id,
                upload_id=self.upload.upload_id,
                entry_id=entry.entry_id,
                qualified_name=f'entry_id:{entry.entry_id}',
                package_definition=package_dict,
                snapshot_section_ids=section_ids,
            ).save()

    def _index_search(self, entry_data_to_index: list[datamodel.EntryArchive]) -> None:
        """Index imported archive data in Elasticsearch."""
        if entry_data_to_index:
            search.index(
                entry_data_to_index,
                update_materials=config.process.index_materials,
                refresh=True,
            )

    def _reprocess_upload(self) -> str | None:
        """Trigger local reprocessing for the imported upload."""
        return self.upload._process_upload_local(
            reprocess_settings=self.import_settings.process_settings.model_dump()
        )


def _keys_exist(
    data: dict[str, Any], required_keys: Iterable[str], error_message: str
) -> None:
    """
    Checks if the specified keys exist in the provided dictionary structure `data`.
    Supports dot-notation to access subkeys.
    """
    for key in required_keys:
        current = data
        for sub_key in key.split('.'):
            assert sub_key in current, error_message.replace('{key}', key)
            current = current[sub_key]


def _check_user_ids(user_ids: Iterable[str], error_message: str) -> None:
    """
    Checks if all user_ids provided in the Iterable `user_ids` are valid. If not, raises an
    AssertionError with the specified error message. The string {id} in `error_message` is
    replaced with the bad value.
    """
    for user_id in user_ids:
        user = datamodel.User.get(user_id=user_id)
        assert user is not None, error_message.replace('{id}', user_id)
