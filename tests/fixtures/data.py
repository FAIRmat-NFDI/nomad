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
import math
import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fsspec.implementations.local import LocalFileSystem

from nomad import bundles, datamodel, processing, utils
from nomad.archive import read_archive, to_json, write_archive
from nomad.datamodel import EntryArchive, OptimadeEntry, User
from nomad.datamodel.datamodel import SearchableQuantity
from nomad.files import FSUtility, UploadFiles
from nomad.metainfo.elasticsearch_extension import schema_separator
from nomad.processing import ProcessStatus
from nomad.processing.data import Upload
from nomad.utils.exampledata import ExampleData
from tests.fixtures.infrastructure import TemporalWorkerContext
from tests.normalizing.conftest import run_normalize
from tests.parsing import test_parsing
from tests.processing import test_data as test_processing
from tests.test_files import empty_file, example_file_vasp_with_binary
from tests.utils import (
    build_url,
    create_template_upload_file,
    set_upload_entry_metadata,
)
from tests.variables import python_schema_name, yaml_schema_name, yaml_schema_root


@pytest.fixture(scope='session')
def example_mainfile() -> tuple[str, str]:
    return ('parsers/template', 'tests/data/templates/template.json')


@pytest.fixture(scope='function', params=['empty_file', 'example_file'])
def example_upload(request, tmp) -> str:
    if request.param == 'empty_file':
        return create_template_upload_file(tmp, mainfiles=[], auxfiles=0)

    return create_template_upload_file(
        tmp, mainfiles=['tests/data/proc/templates/template.json']
    )


@pytest.fixture(scope='function')
def non_empty_example_upload(tmp):
    return create_template_upload_file(
        tmp, mainfiles=['tests/data/proc/templates/template.json']
    )


@pytest.fixture(scope='session')
def non_empty_example_upload_vasp_with_binary():
    return example_file_vasp_with_binary


@pytest.fixture(scope='session')
def empty_upload():
    return empty_file


@pytest.fixture(scope='module')
def example_user_metadata(user2) -> dict:
    return {
        'comment': 'test comment',
        'references': ['http://external.ref/one', 'http://external.ref/two'],
        'entry_coauthors': [user2.user_id],
        '_pid': '256',
        'external_id': 'external_test_id',
    }


@pytest.fixture(scope='module')
def internal_example_user_metadata(example_user_metadata) -> dict:
    return {
        key[1:] if key[0] == '_' else key: value
        for key, value in example_user_metadata.items()
    }


@pytest.fixture(scope='session')
def parsed(example_mainfile: tuple[str, str]) -> EntryArchive:
    """Provides a parsed entry in the form of an EntryArchive."""
    parser, mainfile = example_mainfile
    return test_parsing.run_singular_parser(parser, mainfile)


@pytest.fixture(scope='session')
def normalized(parsed: EntryArchive) -> EntryArchive:
    """Provides a normalized entry in the form of a EntryArchive."""
    return run_normalize(parsed)


@pytest.fixture(scope='function')
def uploaded(example_upload: str, raw_files_function) -> tuple[str, str]:
    """
    Provides a uploaded with uploaded example file and gives the upload_id.
    Clears files after test.
    """
    example_upload_id = os.path.basename(example_upload).replace('.zip', '')
    example_upload_id += f'_{uuid.uuid4().hex[:8]}'
    return example_upload_id, example_upload


@pytest.fixture(scope='function')
def non_empty_uploaded(
    non_empty_example_upload: str, raw_files_function
) -> tuple[str, str]:
    example_upload_id = os.path.basename(non_empty_example_upload).replace('.zip', '')
    example_upload_id += f'_{uuid.uuid4().hex[:8]}'
    return example_upload_id, non_empty_example_upload


@pytest_asyncio.fixture(scope='function')
async def oasis_publishable_upload(
    api_v1,
    proc_infra,
    non_empty_processed_with_temporal: processing.Upload,
    internal_example_user_metadata,
    monkeypatch,
    user1,
    temporal_worker: TemporalWorkerContext,
):
    """
    Creates a published upload which can be used with Upload.publish_externally. Some monkeypatching
    is done which replaces IDs when importing.
    """
    # Create a published upload
    set_upload_entry_metadata(
        non_empty_processed_with_temporal, internal_example_user_metadata
    )
    async with temporal_worker():
        await asyncio.to_thread(non_empty_processed_with_temporal.publish_upload)
        await non_empty_processed_with_temporal.await_workflows()

    suffix = '_2'  # Will be added to all IDs in the mirrored upload
    upload_id = non_empty_processed_with_temporal.upload_id

    # Do some tricks to add suffix to the ID fields
    old_bundle_importer_open = bundles.BundleImporter.open

    def new_bundle_importer_open(self, *args, **kwargs):
        old_bundle_importer_open(self, *args, **kwargs)
        # Change the id's in the bundle_info dict behind the scenes when loading the bundle
        bundle_info = self.bundle_info
        bundle_info['upload_id'] += suffix
        bundle_info['upload']['_id'] += suffix
        for entry_dict in bundle_info['entries']:
            entry_dict['_id'] = utils.generate_entry_id(
                upload_id + suffix,
                entry_dict['mainfile'],
                entry_dict.get('mainfile_key'),
            )
            entry_dict['upload_id'] += suffix

    old_bundle_import_files = bundles.BundleImporter._import_files

    def new_bundle_import_files(self, *args, **kwargs):
        old_bundle_import_files(self, *args, **kwargs)
        # Overwrite the archive files with files containing the updated IDs
        archive_path = self.upload_files.os_path
        archive_upath = FSUtility.upath(archive_path)
        file_names = [
            os.path.basename(entry)
            for entry in archive_upath.fs.ls(archive_upath.path, detail=False)
        ]
        for file_name in file_names:
            if file_name.endswith('.msg'):
                full_path = os.path.join(archive_path, file_name)
                new_data: dict = {}
                with read_archive(full_path) as data:
                    for entry_id in data.keys():
                        archive_dict = to_json(data[entry_id])
                        section_metadata = archive_dict['metadata']
                        section_metadata['upload_id'] += suffix
                        new_entry_id = utils.generate_entry_id(
                            section_metadata['upload_id'],
                            section_metadata['mainfile'],
                            section_metadata.get('mainfile_key'),
                        )
                        section_metadata['entry_id'] = new_entry_id
                        new_data[new_entry_id] = archive_dict
                write_archive(full_path, new_data)

    monkeypatch.setattr('nomad.bundles.BundleImporter.open', new_bundle_importer_open)
    monkeypatch.setattr(
        'nomad.bundles.BundleImporter._import_files', new_bundle_import_files
    )

    async def async_post(url, data, params, **kwargs):
        async with temporal_worker():
            return await asyncio.to_thread(
                lambda: api_v1.post(
                    build_url(url.lstrip('/api/v1/'), params),
                    data=data.read(),
                    **kwargs,
                )
            )

    # Further monkey patching
    def new_post(url, data=None, params=None, **kwargs):
        if params is None:
            params = {}

        # Run async work inside a loop explicitly
        return asyncio.run(async_post(url, data, params, **kwargs))

    monkeypatch.setattr('requests.post', new_post)
    monkeypatch.setattr('nomad.config.oasis.is_oasis', True)
    monkeypatch.setattr('nomad.config.keycloak.username', user1.username)

    monkeypatch.setattr('nomad.config.oasis.central_nomad_deployment_url', '/api')

    # create a dataset to also test this aspect of oasis uploads
    entry = non_empty_processed_with_temporal.successful_entries[0]
    datamodel.Dataset(
        dataset_id='dataset_id', dataset_name='dataset_name', user_id=user1.user_id
    ).a_mongo.save()
    entry.datasets = ['dataset_id']
    entry.save()
    return non_empty_processed_with_temporal.upload_id, suffix


@pytest_asyncio.fixture(scope='function')
async def processed(
    uploaded: tuple[str, str], user1: User, temporal_worker, mails
) -> processing.Upload:
    """
    Provides a processed upload. Upload was uploaded with user1.
    """
    async with temporal_worker():
        result = await asyncio.to_thread(
            lambda: test_processing.run_processing(uploaded, user1)
        )
        return result


@pytest_asyncio.fixture(scope='function')
async def processeds(
    non_empty_example_upload: str, user1: User, temporal_worker
) -> list[processing.Upload]:
    results: list[processing.Upload] = []
    async with temporal_worker():
        for i in range(2):
            upload_id = (
                f'{os.path.basename(non_empty_example_upload).replace(".zip", "")}_{i}'
            )
            result = await asyncio.to_thread(
                lambda: test_processing.run_processing(
                    (upload_id, non_empty_example_upload), user1
                )
            )
            results.append(result)

    return results


@pytest.fixture(scope='function')
def non_empty_processed(
    non_empty_uploaded: tuple[str, str], user1: User, proc_infra
) -> processing.Upload:
    """
    Provides a processed upload. Upload was uploaded with user1.
    """
    return test_processing.run_processing(non_empty_uploaded, user1)


@pytest_asyncio.fixture(scope='function')
async def non_empty_processed_with_temporal(
    non_empty_uploaded: tuple[str, str],
    user1: User,
    temporal_worker: TemporalWorkerContext,
) -> processing.Upload:
    """
    Provides a processed upload that was uploaded by user1.
    """
    uploaded_id, uploaded_path = non_empty_uploaded
    upload = Upload.create(upload_id=uploaded_id, main_author=user1)
    upload.save()
    assert upload.process_status == ProcessStatus.READY
    assert upload.last_status_message is None
    async with temporal_worker() as _:
        handle = await upload._start_process_upload_workflow(
            file_operations=[
                dict(
                    op='ADD',
                    path=uploaded_path,
                    target_dir='',
                    temporary=False,
                )
            ]
        )
        await handle.result()

    upload.reload()
    return upload


@pytest_asyncio.fixture(scope='function')
async def published(
    non_empty_processed_with_temporal: processing.Upload,
    internal_example_user_metadata,
    temporal_worker: TemporalWorkerContext,
) -> processing.Upload:
    """
    Provides a processed published upload. Upload was uploaded with user1 and is embargoed.
    """
    set_upload_entry_metadata(
        non_empty_processed_with_temporal, internal_example_user_metadata
    )
    async with temporal_worker():
        await asyncio.to_thread(
            non_empty_processed_with_temporal.publish_upload, embargo_length=12
        )
        await non_empty_processed_with_temporal.await_workflows()

    return non_empty_processed_with_temporal


@pytest_asyncio.fixture(scope='function')
async def published_wo_user_metadata(
    non_empty_processed_with_temporal: processing.Upload,
    temporal_worker: TemporalWorkerContext,
) -> processing.Upload:
    """
    Provides a processed upload. Upload was uploaded with user1.
    """
    async with temporal_worker() as _:
        await asyncio.to_thread(non_empty_processed_with_temporal.publish_upload)
        await non_empty_processed_with_temporal.await_workflows()

    return non_empty_processed_with_temporal


@pytest.fixture(scope='module')
def example_data(
    elastic_module,
    raw_files_module,
    mongo_module,
    user1,
    user2,
    normalized,
    request,
):
    """
    Provides a couple of uploads and entries including metadata, raw-data, and
    archive files.

    id_embargo:
        1 entry, 1 material, published with embargo
    id_embargo_w_coauthor:
        1 entry, 1 material, published with embargo and coauthor
    id_embargo_w_reviewer:
        1 entry, 1 material, published with embargo and reviewer
    id_unpublished:
        1 entry, 1 material, unpublished
    id_unpublished_w_coauthor:
        1 entry, 1 material, unpublished with coauthor
    id_unpublished_w_reviewer:
        1 entry, 1 material, unpublished with reviewer
    id_published:
        23 entries, 6 materials published without embargo
        partial archive exists only for id_01
        raw files and archive file for id_02 are missing
        id_10, id_11 reside in the same directory
    id_child_entries:
        1 parent entry and 2 child entries from one mainfile, 1 material, unpublished
    id_processing:
        unpublished upload without any entries, in status processing
    id_empty:
        unpublished upload without any entries
    """
    data = ExampleData(main_author=user1)

    # 6 uploads with different combinations of main_type and sub_type
    for main_type in ('embargo', 'unpublished'):
        for sub_type in ('', 'w_coauthor', 'w_reviewer'):
            upload_id = 'id_' + main_type + ('_' if sub_type else '') + sub_type
            if main_type == 'embargo':
                published = True
                embargo_length = 12
                upload_name = 'name_' + upload_id[3:]
            else:
                published = False
                embargo_length = 0
                upload_name = None
            entry_id = upload_id + '_1'
            coauthors = [user2.user_id] if sub_type == 'w_coauthor' else None
            reviewers = [user2.user_id] if sub_type == 'w_reviewer' else None
            data.create_upload(
                upload_id=upload_id,
                upload_name=upload_name,
                description=f'Descripton for upload {upload_id}',
                coauthors=coauthors,
                reviewers=reviewers,
                published=published,
                embargo_length=embargo_length,
            )
            data.create_entry(
                upload_id=upload_id,
                entry_id=entry_id,
                material_id=upload_id,
                mainfile=f'test_content/{entry_id}/mainfile.json',
            )

    # one upload with 23 entries, published, no embargo
    data.create_upload(
        upload_id='id_published', upload_name='name_published', published=True
    )
    for i in range(1, 24):
        entry_id = f'id_{i:02d}'
        material_id = f'id_{int(math.floor(i / 4)) + 1:02d}'
        mainfile = f'test_content/subdir/test_entry_{i:02d}/mainfile.json'
        kwargs = dict(
            optimade=OptimadeEntry(nelements=2, elements=['H', 'O']),
        )
        if i == 11:
            mainfile = 'test_content/subdir/test_entry_10/mainfile_11.json'
        if i == 1:
            kwargs['pid'] = '123'
        data.create_entry(
            upload_id='id_published',
            entry_id=entry_id,
            material_id=material_id,
            mainfile=mainfile,
            **kwargs,
        )

    # 3 entries from one mainfile, 1 material, unpublished
    upload_id = 'id_child_entries'
    data.create_upload(
        upload_id=upload_id, upload_name='name_child_entries', published=False
    )
    for mainfile_key in (None, 'child1', 'child2'):
        data.create_entry(
            upload_id=upload_id,
            entry_id=upload_id + '_' + (mainfile_key or 'main'),
            material_id=upload_id,
            mainfile=f'test_content/mainfile_w_children.json',
            mainfile_key=mainfile_key,
        )

    # one upload, no entries, still processing
    data.create_upload(
        upload_id='id_processing', published=False, process_status=ProcessStatus.RUNNING
    )

    # one upload, no entries, unpublished
    data.create_upload(upload_id='id_empty', published=False)

    data.save(with_files=False)
    del data.archives['id_02']
    data.save(with_files=True, with_es=False, with_mongo=False)

    if request.config.getoption('--s3-storage'):
        upload_files = UploadFiles.get('id_published')
        archive_path = upload_files.msg_fp(upload_files.access, fallback=True).os_path
        upath = FSUtility.upath(archive_path)
        assert not isinstance(upath.fs, LocalFileSystem)
        assert not os.path.exists(archive_path)
        assert upath.exists()

    # yield

    # # The data is deleted after fixture goes out of scope
    # data.delete()


@pytest.fixture(scope='function')
def example_data_published_doi(
    elastic_module,
    raw_files_module,
    mongo_module,
    user1,
):
    data = ExampleData(main_author=user1)
    data.create_upload(
        upload_id='id_published_doi',
        upload_name='name_published_doi',
        published=True,
        doi={'id': '10.83696/nomad.test-test'},
    )
    data.save()


@pytest.fixture(scope='function')
def example_data_schema_python(
    elastic_module, raw_files_module, mongo_module, user1, normalized
):
    """
    Contains entries that store data using a python schema.
    """
    data = ExampleData(main_author=user1)
    upload_id = 'id_plugin_schema_published'
    date_value = datetime.now(timezone.utc)

    data.create_upload(upload_id=upload_id, upload_name=upload_id, published=True)
    for i in range(0, 15):
        data.create_entry(
            upload_id=upload_id,
            entry_id=f'test_entry_{i}',
            mainfile=f'test_content/test.archive{i}.json',
            search_quantities=[
                SearchableQuantity(
                    id=f'data.name{schema_separator}{python_schema_name}',
                    definition='nomadschemaexample.schema.MySchema.name',
                    path_archive='data.name',
                    str_value=f'test{i}',
                    segments={
                        '1': {
                            'path': 'data',
                            'definitions': [
                                'nomadschemaexample.schema.MySchema',
                                'nomadschemaexample.schema.MyBaseSchemaA',
                                'nomadschemaexample.schema.MyBaseSchemaB',
                            ],
                        },
                        '2': {
                            'path': 'name',
                            'definitions': [
                                'nomadschemaexample.schema.MySchema',
                            ],
                        },
                    },
                ),
                SearchableQuantity(
                    id=f'data.inherited_a{schema_separator}{python_schema_name}',
                    definition='nomadschemaexample.schema.MyBaseSchemaA.inherited_a',
                    path_archive='data.inherited_a',
                    str_value=f'test{i}',
                    segments={
                        '1': {
                            'path': 'data',
                            'definitions': [
                                'nomadschemaexample.schema.MySchema',
                                'nomadschemaexample.schema.MyBaseSchemaA',
                                'nomadschemaexample.schema.MyBaseSchemaB',
                            ],
                        },
                        '2': {
                            'path': 'inherited_a',
                            'definitions': [
                                'nomadschemaexample.schema.MyBaseSchemaA',
                            ],
                        },
                    },
                ),
                SearchableQuantity(
                    id=f'data.inherited_b{schema_separator}{python_schema_name}',
                    definition='nomadschemaexample.schema.MyBaseSchemaA.inherited_b',
                    path_archive='data.inherited_b',
                    str_value=f'test{i}',
                    segments={
                        '1': {
                            'path': 'data',
                            'definitions': [
                                'nomadschemaexample.schema.MySchema',
                                'nomadschemaexample.schema.MyBaseSchemaA',
                                'nomadschemaexample.schema.MyBaseSchemaB',
                            ],
                        },
                        '2': {
                            'path': 'inherited_b',
                            'definitions': [
                                'nomadschemaexample.schema.MyBaseSchemaB',
                            ],
                        },
                    },
                ),
                SearchableQuantity(
                    id=f'data.valid{schema_separator}{python_schema_name}',
                    definition='nomadschemaexample.schema.MySchema.valid',
                    path_archive='data.valid',
                    bool_value=i % 2 == 0,
                ),
                SearchableQuantity(
                    id=f'data.message{schema_separator}{python_schema_name}',
                    definition='nomadschemaexample.schema.MySchema.message',
                    path_archive='data.message',
                    str_value='A' if i % 2 == 0 else 'B',
                ),
                SearchableQuantity(
                    id=f'data.frequency{schema_separator}{python_schema_name}',
                    definition='nomadschemaexample.schema.MySchema.frequency',
                    path_archive='data.frequency',
                    float_value=i + 0.5,
                ),
                SearchableQuantity(
                    id=f'data.count{schema_separator}{python_schema_name}',
                    definition='nomadschemaexample.schema.MySchema.count',
                    path_archive='data.count',
                    int_value=i,
                ),
                SearchableQuantity(
                    id=f'data.timestamp{schema_separator}{python_schema_name}',
                    definition='nomadschemaexample.schema.MySchema.timestamp',
                    path_archive='data.timestamp',
                    datetime_value=date_value,
                ),
                SearchableQuantity(
                    id=f'data.child.name{schema_separator}{python_schema_name}',
                    definition='nomadschemaexample.schema.MySection.name',
                    path_archive='data.child.name',
                    str_value=f'test_child{i}',
                ),
                SearchableQuantity(
                    id=f'data.child_repeating.name{schema_separator}{python_schema_name}',
                    definition='nomadschemaexample.schema.MySection.name',
                    path_archive='data.child_repeating.0.name',
                    str_value=f'test_child_repeating{i}',
                ),
            ],
        )
    data.save(with_files=False)

    yield

    # The data is deleted after fixture goes out of scope
    data.delete()


@pytest.fixture(scope='function')
def example_data_schema_yaml(
    elastic_module,
    raw_files_function,
    no_warn,
    raw_files_module,
    mongo_module,
    user1,
    normalized,
):
    """
    Contains entries that store data using a YAML schema.
    """
    data = ExampleData(main_author=user1)
    upload_id = 'id_plugin_schema_published'
    date_value = datetime.now(timezone.utc)

    data.create_upload(upload_id=upload_id, upload_name=upload_id, published=True)
    for i in range(0, 15):
        data.create_entry(
            upload_id=upload_id,
            entry_id=f'test_entry_{i}',
            mainfile=f'test_content/test.archive{i}.json',
            search_quantities=[
                SearchableQuantity(
                    id=f'data.name{schema_separator}{yaml_schema_name}',
                    definition=f'{yaml_schema_root}/quantities/0',
                    path_archive='data.name',
                    str_value=f'test{i}',
                ),
                SearchableQuantity(
                    id=f'data.valid{schema_separator}{yaml_schema_name}',
                    definition=f'{yaml_schema_root}/quantities/1',
                    path_archive='data.valid',
                    bool_value=i % 2 == 0,
                ),
                SearchableQuantity(
                    id=f'data.message{schema_separator}{yaml_schema_name}',
                    definition=f'{yaml_schema_root}/quantities/1',
                    path_archive='data.message',
                    str_value='A' if i % 2 == 0 else 'B',
                ),
                SearchableQuantity(
                    id=f'data.frequency{schema_separator}{yaml_schema_name}',
                    definition=f'{yaml_schema_root}/quantities/5',
                    path_archive='data.frequency',
                    float_value=i + 0.5,
                ),
                SearchableQuantity(
                    id=f'data.count{schema_separator}{yaml_schema_name}',
                    definition=f'{yaml_schema_root}/quantities/4',
                    path_archive='data.count',
                    int_value=i,
                ),
                SearchableQuantity(
                    id=f'data.timestamp{schema_separator}{yaml_schema_name}',
                    definition=f'{yaml_schema_root}/quantities/1',
                    path_archive='data.timestamp',
                    datetime_value=date_value,
                ),
                SearchableQuantity(
                    id=f'data.child.name{schema_separator}{yaml_schema_name}',
                    definition=f'{yaml_schema_root}/section_definitions/0/quantities/0',
                    path_archive='data.child.name',
                    str_value=f'test_child{i}',
                ),
                SearchableQuantity(
                    id=f'data.child_repeating.name{schema_separator}{yaml_schema_name}',
                    definition=f'{yaml_schema_root}/section_definitions/0/quantities/0',
                    path_archive='data.child_repeating.0.name',
                    str_value=f'test_child_repeating{i}',
                ),
            ],
        )
    data.save(with_files=False)

    yield

    # The data is deleted after fixture goes out of scope
    data.delete()


@pytest.fixture(scope='function')
def example_data_writeable(mongo_function, user1, normalized, elastic_function):
    data = ExampleData(main_author=user1)

    upload_id_map = {
        'id_published_w': f'id_published_w_{uuid.uuid4().hex[:8]}',
        'id_unpublished_w': f'id_unpublished_w_{uuid.uuid4().hex[:8]}',
        'id_processing_w': f'id_processing_w{uuid.uuid4().hex[:8]}',
        'id_empty_w': f'id_empty_w{uuid.uuid4().hex[:8]}',
    }

    # one upload with one entry, published
    uid = upload_id_map['id_published_w']
    data.create_upload(
        upload_id=uid,
        description=f'Description for writable upload {uid}',
        published=True,
        embargo_length=12,
    )
    data.create_entry(
        upload_id=upload_id_map['id_published_w'],
        entry_id='id_published_w_entry',
        mainfile='test_content/test_embargo_entry/mainfile.json',
    )

    # one upload with one entry, unpublished
    uid = upload_id_map['id_unpublished_w']
    data.create_upload(
        upload_id=uid,
        description=f'Description for writable upload {uid}',
        published=False,
        embargo_length=12,
    )
    data.create_entry(
        upload_id=upload_id_map['id_unpublished_w'],
        entry_id='id_unpublished_w_entry',
        mainfile='test_content/test_embargo_entry/mainfile.json',
    )

    # one upload, no entries, running a blocking processing
    uid = upload_id_map['id_processing_w']
    data.create_upload(
        upload_id=uid,
        description=f'Description for writable upload {uid}',
        published=False,
        process_status=ProcessStatus.RUNNING,
        current_process='publish_upload',
    )

    # one upload, no entries, unpublished
    uid = upload_id_map['id_empty_w']
    data.create_upload(
        upload_id=uid,
        description=f'Description for writable upload {uid}',
        published=False,
    )

    data.save()

    yield upload_id_map

    data.delete()


@pytest.fixture(scope='function')
def example_datasets(mongo_function, user1, user2):
    dataset_specs = (
        ('test_dataset_1', user1, None),
        ('test_dataset_2', user1, 'test_doi_2'),
        ('test_dataset_3', user2, None),
    )
    datasets = []
    for dataset_name, user, doi in dataset_specs:
        now = datetime.now(timezone.utc)
        dataset = datamodel.Dataset(
            dataset_id=utils.create_uuid(),
            dataset_name=dataset_name,
            doi=doi,
            user_id=user.user_id,
            dataset_create_time=now,
            dataset_modified_time=now,
            dataset_type='owned',
        )
        dataset.a_mongo.create()
        datasets.append(dataset)

    yield datasets

    while datasets:
        datasets.pop().a_mongo.delete()
