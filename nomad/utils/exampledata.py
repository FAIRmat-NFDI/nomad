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

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from nomad import files, search
from nomad.datamodel import EntryArchive, EntryMetadata, Results
from nomad.datamodel.metainfo import runschema
from nomad.datamodel.metainfo.workflow import Workflow
from nomad.mongo.doi import EmbeddedDOI
from nomad.mongo.included_entries import IncludedEntries
from nomad.normalizing import normalizers
from nomad.processing.data import mongo_upload_metadata


class ExampleData:
    """
    Allows to define, create, and manage a set of example data. Will create respective
    data via raw files, archives, in mongodb, and in both elasticsearch indices.

    Requires initialized infrastructure.

    Attributes:
        upload_entries: A dictionary with upload_ids as keys and lists of entry_ids as values.
        uploads: A dictionary with upload_ids as keys and their upload information as values.
        entries: A dictionary with entry_ids as keys and their ``EntryMetadata`` as values.
        archives: A dictionary with entry_ids as keys and their ``EntryArchives`` as values.
    """

    def __init__(self, **kwargs):
        self.upload_entries: dict[str, list[str]] = dict()
        self.uploads: dict[str, dict[str, Any]] = dict()
        self.entries: dict[str, EntryMetadata] = dict()
        self.archives: dict[str, EntryArchive] = dict()

        self.entry_defaults = kwargs
        self._entry_id_counter = 1

        self._time_stamp = datetime.now(timezone.utc)

    def save(
        self,
        with_files: bool = True,
        with_mongo: bool = True,
        with_es: bool = True,
        additional_files_path: str | None = None,
        es_nomad_version: str | None = None,
        archive_nomad_version: str | None = None,
    ):
        from nomad import processing as proc
        from tests.test_files import create_test_upload_files

        errors = None

        # Save
        if with_mongo:
            for upload_id, upload_dict in self.uploads.items():
                upload_dict['main_author'] = upload_dict['main_author'].user_id
                mongo_upload = proc.Upload(**upload_dict)
                mongo_upload.save()

            for entry_metadata in self.entries.values():
                process_status = (
                    proc.ProcessStatus.SUCCESS
                    if entry_metadata.processed
                    else proc.ProcessStatus.FAILURE
                )
                mongo_entry = proc.Entry(
                    entry_create_time=entry_metadata.entry_create_time,
                    entry_id=entry_metadata.entry_id,
                    upload_id=entry_metadata.upload_id,
                    mainfile=entry_metadata.mainfile,
                    parser_name='parsers/vasp',
                    process_status=process_status,
                )
                mongo_entry.set_mongo_entry_metadata(entry_metadata)
                mongo_entry.save()

        if es_nomad_version is not None:
            for archive in self.archives.values():
                archive.metadata.nomad_version = es_nomad_version

        if with_es:
            archives = list(self.archives.values())
            errors = search.index(archives, refresh=True)
            assert not errors, (
                f'The following errors encountered during indexing: {errors}'
            )

        if archive_nomad_version is not None:
            for archive in self.archives.values():
                archive.metadata.nomad_version = archive_nomad_version

        if with_files:
            for upload_id, upload_dict in self.uploads.items():
                entry_ids = self.upload_entries.get(upload_id, [])
                archives = []
                for entry_id in entry_ids:
                    if entry_id in self.archives:
                        archives.append(self.archives[entry_id])

                create_test_upload_files(
                    upload_id,
                    archives,
                    published=upload_dict.get('publish_time') is not None,
                    embargo_length=upload_dict['embargo_length'],
                    additional_files_path=additional_files_path,
                )
                from nomad import files

                assert files.UploadFiles.get(upload_id) is not None

    def delete(self):
        from nomad import processing as proc

        for upload_id in self.upload_entries:
            search.delete_upload(upload_id, refresh=True)
            upload_proc = proc.Upload.objects(upload_id=upload_id).first()  # type: ignore
            if upload_proc is not None:
                upload_proc.delete()
            upload_files = files.UploadFiles.get(upload_id)
            if upload_files is not None:
                upload_files.delete()

    def create_entry_from_file(
        self,
        mainfile: str,
        entry_archive: EntryArchive | None = None,
        entry_id: str | None = None,
        upload_id: str | None = None,
        parser_name: str | None = None,
    ):
        """Creates an entry from a mainfile which then gets parsed and normalized."""
        from nomad import parsing
        from nomad.parsing import parsers

        assert upload_id in self.uploads, 'Must create the upload first'

        if entry_id is None:
            entry_id = f'test_entry_id_{self._entry_id_counter}'
            self._entry_id_counter += 1

        if entry_archive is None:
            entry_archive = EntryArchive()

        entry_metadata = entry_archive.m_setdefault('metadata')
        entry_metadata.m_update(entry_id=entry_id, upload_id=upload_id)

        mainfile_path = os.path.abspath(mainfile)
        parser, _ = parsers.match_parser(
            mainfile_path, strict=True, parser_name=parser_name
        )
        if isinstance(parser, parsing.MatchingParser):
            parser_name = parser.name
        else:
            parser_name = parser.__class__.__name__

        assert parser is not None, f'there is no parser matching {mainfile}'
        parser.parse(mainfile=mainfile, archive=entry_archive)

        entry_metadata.m_update(
            mainfile=mainfile,
            entry_hash='dummy_hash_' + entry_id,
            domain='dft',
            entry_create_time=self._next_time_stamp(),
            processed=True,
            parser_name=parser_name,
            **self.entry_defaults,
        )

        for normalizer_class in normalizers:
            normalizer = normalizer_class(entry_archive)
            normalizer.normalize()

        self.archives[entry_id] = entry_archive
        self.upload_entries.setdefault(entry_metadata.upload_id, []).append(entry_id)

        return entry_archive

    def create_upload(self, upload_id, published=None, **kwargs):
        """
        Creates a dictionary holding all the upload information.
        Default values are used/generated, and can be set via kwargs.
        """
        upload_dict = {
            'upload_id': upload_id,
            'current_process': 'process_upload',
            'process_status': 'SUCCESS',
            'errors': [],
            'warnings': [],
            'upload_create_time': self._next_time_stamp(),
            'complete_time': self._next_time_stamp(),
            'last_update': self._next_time_stamp(),
            'embargo_length': 0,
            'publish_time': None,
            'license': 'CC BY 4.0',
            'published_to': [],
        }
        upload_dict.update(kwargs)

        if upload_dict.get('doi') is not None:
            upload_dict['doi'] = EmbeddedDOI(**upload_dict['doi'])

        key = 'included_entries'
        if upload_dict.get(key) is not None:
            upload_dict[key] = IncludedEntries(**upload_dict[key])

        if published is not None:
            if published and not upload_dict['publish_time']:
                upload_dict['publish_time'] = self._next_time_stamp()
            elif not published:
                assert not upload_dict.get('publish_time')

        for key, default in self.entry_defaults.items():
            upload_dict.setdefault(key, default)

        self.uploads[upload_id] = upload_dict

    def create_entry(
        self,
        entry_archive: EntryArchive | None = None,
        entry_id: str | None = None,
        upload_id: str | None = None,
        material_id: str | None = None,
        mainfile: str | None = None,
        results: Results | dict | None = None,
        archive: dict | None = None,
        **kwargs,
    ) -> EntryArchive:
        assert upload_id in self.uploads, f'Must create the upload {upload_id} first'
        upload_dict = self.uploads[upload_id]

        if entry_id is None:
            entry_id = f'test_entry_id_{self._entry_id_counter}'
            self._entry_id_counter += 1

        if mainfile is None:
            mainfile = f'mainfile_for_{entry_id}'

        if entry_archive is None:
            entry_archive = EntryArchive()

        if material_id is None:
            material_id = 'test_material_id'

        entry_metadata = entry_archive.metadata
        if entry_metadata is None:
            entry_metadata = entry_archive.m_create(EntryMetadata)

        entry_metadata.m_update(
            entry_id=entry_id,
            upload_id=upload_id,
            mainfile=mainfile,
            entry_hash='dummy_hash_' + entry_id,
            domain='dft',
            entry_create_time=self._next_time_stamp(),
            processed=True,
            parser_name='parsers/vasp',
        )
        entry_metadata.m_update(**self.entry_defaults)
        # Fetch data from Upload
        upload_values = {
            k: upload_dict[k] for k in mongo_upload_metadata if k in upload_dict
        }
        upload_values['with_embargo'] = upload_dict['embargo_length'] > 0
        upload_values['published'] = upload_dict.get('publish_time') is not None
        for k in list(mongo_upload_metadata) + ['with_embargo', 'published']:
            assert k not in kwargs, (
                f'Upload level metadata specified on entry level: {k}'
            )
        entry_metadata.m_update(**upload_values)
        entry_metadata.m_update(**kwargs)

        # create v1 default data
        if entry_archive.results is None:
            if results is None:
                results = {
                    'material': {
                        'material_id': material_id,
                        'dimensionality': '3D',
                        'elements': ['H', 'O'],
                        'nelements': 2,
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
                                    'spin_polarized': entry_id.endswith('04'),
                                    'band_gap': [
                                        {
                                            'type': 'direct'
                                            if entry_id.endswith('04')
                                            else 'indirect'
                                        }
                                    ],
                                }
                            ]
                        },
                    },
                }
            if isinstance(results, dict):
                section_results = Results.m_from_dict(results)
            else:
                section_results = results
            assert isinstance(section_results, Results)
            entry_archive.m_add_sub_section(EntryArchive.results, section_results)

        if len(entry_archive.run) == 0 and runschema:
            entry_archive.run.append(runschema.run.Run())

        if archive is not None:
            entry_archive.m_update(**archive)

        if (
            entry_archive.results
            and entry_archive.results.material
            and entry_archive.results.material.material_id is None
        ):
            entry_archive.results.material.material_id = material_id

        self.archives[entry_id] = entry_archive
        self.entries[entry_id] = entry_metadata
        self.upload_entries.setdefault(entry_metadata.upload_id, []).append(entry_id)

        return entry_archive

    def _next_time_stamp(self):
        """
        Returns self._time_stamp and ticks up the time stamp with 1 millisecond. This
        utility guarantees that we get unique and increasing time stamps for each entity.
        """
        self._time_stamp += timedelta(milliseconds=1)
        return self._time_stamp

    def create_structure(
        self,
        upload_id: str,
        id: int,
        h: int,
        o: int,
        extra: list[str],
        periodicity: int,
        optimade: bool = True,
        metadata: dict | None = None,
    ):
        """Creates an entry in Elastic and Mongodb with the given properties.

        Does require initialized :func:`elastic_infra` and :func:`mongo_infra`.

        Args:
            meta_info: A legace metainfo env.
            id: A number to create ``test_entry_id_<number>`` ids.
            h: The amount of H atoms
            o: The amount of O atoms
            extra: A list of further atoms
            periodicity: The number of dimensions to repeat the structure in
            optimade: A boolean. If true the entry will have optimade metadata. Default is True.
            metadata: Additional (user) metadata.
        """
        test_vector = [0, 0, 0]
        atom_labels = ['H' for i in range(0, h)] + ['O' for i in range(0, o)] + extra

        archive = EntryArchive()
        assert runschema is not None
        run = runschema.run.Run()
        archive.run.append(run)
        run.m_create(runschema.run.Program, name='VASP')
        run.m_create(runschema.system.System).m_create(
            runschema.system.Atoms,
            labels=atom_labels,
            positions=[test_vector for i in range(0, len(atom_labels))],
            lattice_vectors=[test_vector, test_vector, test_vector],
            periodic=[True for _ in range(0, periodicity)]
            + [False for _ in range(periodicity, 3)],
        )

        for normalizer_class in normalizers:
            normalizer = normalizer_class(archive)
            normalizer.normalize()

        entry_metadata = archive.metadata
        entry_metadata.domain = 'dft'
        entry_metadata.apply_archive_metadata(archive)

        if not optimade:
            entry_metadata.optimade = None
            entry_metadata.quantities.remove('metadata.optimade')

        if metadata is not None:
            kwargs = metadata
        else:
            kwargs = {}

        self.create_entry(
            entry_archive=archive,
            upload_id=upload_id,
            entry_id=f'test_entry_id_{id}',
            domain='dft',
            **kwargs,
        )


def create_entry_archive(
    metadata: dict | None = None,
    results: dict | None = None,
    run: dict | None = None,
    workflow: dict | None = None,
):
    """Creates an entry archive out of python objects.

    Args:
        metadata: The archive metadata
        results: The archive results
        run: The archive run
        workflow: The archive workflow
    """
    entry = EntryArchive()
    if metadata:
        entry_metadata = entry.m_create(EntryMetadata)
        entry_metadata.m_update(**metadata)

    if results:
        entry_results = Results.m_from_dict(results)
        entry.m_add_sub_section(EntryArchive.results, entry_results)

    if run and runschema is not None:
        entry_run = runschema.run.Run.m_from_dict(run)
        entry.m_add_sub_section(EntryArchive.run, entry_run)

    if workflow:
        entry_workflow = Workflow.m_from_dict(workflow)
        entry.m_add_sub_section(EntryArchive.workflow2, entry_workflow)

    return entry
