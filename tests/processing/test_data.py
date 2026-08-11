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
import os.path
import re
import uuid
import zipfile
from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
import yaml
from temporalio.client import WorkflowFailureError

from nomad import infrastructure, utils
from nomad.archive import to_json
from nomad.datamodel import ServerContext
from nomad.datamodel.datamodel import ArchiveSection, EntryArchive, EntryData
from nomad.files import FSUtility, PublicUploadFiles, StagingUploadFiles, UploadFiles
from nomad.metainfo import Package, Quantity, Reference, SubSection
from nomad.parsing import parsers
from nomad.parsing.parser import Parser
from nomad.processing import Entry, ProcessStatus, Upload
from nomad.processing.base import ProcessAlreadyRunning, ProcessFailure
from nomad.search import refresh as search_refresh
from nomad.search import search
from nomad.utils.exampledata import ExampleData
from tests.test_files import (
    assert_upload_files,
    example_file_aux,
    example_file_mainfile,
)
from tests.test_search import assert_search_upload
from tests.utils import create_template_upload_file, set_upload_entry_metadata

# Package with some metainfo schemas used only for testing.
m_package = Package(name='test_schemas')


class BatchSampleForTest(EntryData):
    batch_id = Quantity(type=str, description='Id for the batch')
    sample_number = Quantity(type=int, description='Sample index')
    comments = Quantity(
        type=str, description='Comments', a_eln=dict(component='RichTextEditQuantity')
    )


class BatchForTest(EntryData):
    batch_id = Quantity(
        type=str,
        description='Id for the batch',
        a_eln=dict(component='StringEditQuantity'),
    )
    n_samples = Quantity(
        type=int,
        description='Number of samples in batch',
        a_eln=dict(component='NumberEditQuantity'),
    )
    sample_refs = Quantity(
        type=Reference(BatchSampleForTest.m_def),
        shape=['*'],
        descriptions='The samples in the batch.',
    )

    def normalize(self, archive, logger):
        super().normalize(archive, logger)
        if not self.n_samples:
            return
        sample_refs = []
        for idx in range(self.n_samples):
            file_name = f'{self.batch_id}_{idx}.archive.json'
            if not archive.m_context.raw_path_exists(file_name):
                # Create new sample file
                sample = BatchSampleForTest(batch_id=self.batch_id, sample_number=idx)
                sample_entry = sample.m_to_dict(with_root_def=True)
                with archive.m_context.raw_file(file_name, 'w') as outfile:
                    json.dump({'data': sample_entry}, outfile)
                # Tell nomad to process it
                archive.m_context.process_updated_raw_file(file_name)
            sample_refs.append(f'../upload/archive/mainfile/{file_name}#data')
        self.sample_refs = sample_refs


class SectionForTest(ArchiveSection):
    pass


class ReferenceSectionForTest(ArchiveSection):
    reference = Quantity(type=Reference(SectionForTest))


class DataForTest(EntryData):
    test_section = SubSection(sub_section=SectionForTest, repeats=True)
    reference_section = SubSection(sub_section=ReferenceSectionForTest)


m_package.__init_metainfo__()


def test_generate_entry_id():
    assert (
        utils.generate_entry_id('an_upload_id', 'a/mainfile/path', None)
        == 'KUB1stwXd8Ll6lliZnM5OoNZlcaf'
    )
    assert (
        utils.generate_entry_id('an_upload_id', 'a/mainfile/path', 'child1')
        == 'di12O5zSSb0Al9ipG6BBp_I0JYgd'
    )


def test_send_mail(mails, monkeypatch):
    infrastructure.send_mail('test name', 'test@email.de', 'test message', 'subject')

    for message in mails.messages:
        assert re.search(r'test message', message.data.decode('utf-8')) is not None


def test_put_file_and_process_local_sets_success_message_on_success(
    tmp_path, monkeypatch
):
    raw_file = tmp_path / 'mainfile.txt'
    raw_file.write_text('content')

    status_messages: list[str] = []
    main_entry = MagicMock()
    main_entry.process_entry_local = MagicMock()
    main_entry.save = MagicMock()

    upload = SimpleNamespace(
        published=False,
        upload_id='upload-id',
        main_author_user=MagicMock(),
        reprocess_settings=None,
        set_last_status_message=status_messages.append,
        get_logger=MagicMock,
        staging_upload_files=SimpleNamespace(
            raw_exists=lambda _path: False,
            add_rawfiles=lambda _path, _target_dir: None,
            raw_file_object=lambda _path: SimpleNamespace(os_path=str(raw_file)),
        ),
    )

    monkeypatch.setattr(
        'nomad.processing.data.match_parser',
        lambda _path: (SimpleNamespace(name='dummy-parser'), None),
    )
    monkeypatch.setattr(
        'nomad.processing.data.MetadataEditRequestHandler',
        lambda *args, **kwargs: SimpleNamespace(
            get_entry_mongo_metadata=lambda _upload, _entry: {}
        ),
    )
    monkeypatch.setattr('nomad.processing.data.Entry.objects', lambda **kwargs: [])
    monkeypatch.setattr(
        'nomad.processing.data.Entry.create', lambda **kwargs: main_entry
    )
    monkeypatch.setattr(
        'nomad.processing.data.utils.generate_entry_id',
        lambda _upload_id, _path, _key: 'entry-id',
    )

    result = Upload.put_file_and_process_local(upload, str(raw_file), '')

    assert result is main_entry
    assert status_messages[-1] == 'Process completed successfully'
    main_entry.process_entry_local.assert_called_once()


def test_put_file_and_process_local_does_not_set_success_message_on_failure(
    tmp_path, monkeypatch
):
    raw_file = tmp_path / 'mainfile.txt'
    raw_file.write_text('content')

    status_messages: list[str] = []
    main_entry = MagicMock()
    main_entry.process_entry_local = MagicMock(
        side_effect=Exception('processing failed')
    )
    main_entry.save = MagicMock()

    upload = SimpleNamespace(
        published=False,
        upload_id='upload-id',
        main_author_user=MagicMock(),
        reprocess_settings=None,
        set_last_status_message=status_messages.append,
        get_logger=MagicMock,
        staging_upload_files=SimpleNamespace(
            raw_exists=lambda _path: False,
            add_rawfiles=lambda _path, _target_dir: None,
            raw_file_object=lambda _path: SimpleNamespace(os_path=str(raw_file)),
        ),
    )

    monkeypatch.setattr(
        'nomad.processing.data.match_parser',
        lambda _path: (SimpleNamespace(name='dummy-parser'), None),
    )
    monkeypatch.setattr(
        'nomad.processing.data.MetadataEditRequestHandler',
        lambda *args, **kwargs: SimpleNamespace(
            get_entry_mongo_metadata=lambda _upload, _entry: {}
        ),
    )
    monkeypatch.setattr('nomad.processing.data.Entry.objects', lambda **kwargs: [])
    monkeypatch.setattr(
        'nomad.processing.data.Entry.create', lambda **kwargs: main_entry
    )
    monkeypatch.setattr(
        'nomad.processing.data.utils.generate_entry_id',
        lambda _upload_id, _path, _key: 'entry-id',
    )

    result = Upload.put_file_and_process_local(upload, str(raw_file), '')

    assert result is main_entry
    assert status_messages[-1] == 'Process failed'
    assert 'Process completed successfully' not in status_messages
    main_entry.process_entry_local.assert_called_once()


@pytest.fixture(scope='function', autouse=True)
def mongo_forall(mongo_function):
    pass


@pytest.fixture
def uploaded_id_with_warning(
    raw_files_function,
) -> Generator[tuple[str, str], None, None]:
    example_file = 'tests/data/proc/examples_with_warning_template.zip'
    example_upload_id = os.path.basename(example_file).replace('.zip', '')

    yield example_upload_id, example_file


def run_processing(uploaded: tuple[str, str], main_author, **kwargs) -> Upload:
    upload_id, upload_path = uploaded
    upload_id += f'_{uuid.uuid4().hex[:8]}'  # randomize upload ID
    upload = Upload.create(upload_id=upload_id, main_author=main_author, **kwargs)
    assert upload.process_status == ProcessStatus.READY
    assert upload.last_status_message is None
    upload.process_upload(
        file_operations=[
            dict(
                op='ADD',
                path=upload_path,
                target_dir='',
                temporary=kwargs.get('temporary', False),
            )
        ]
    )
    asyncio.run(upload.await_workflows())

    return upload


def assert_processing(
    upload: Upload, published: bool = False, process='_process_upload'
):
    assert not upload.process_running
    assert upload.current_process == process
    assert upload.upload_id is not None
    assert len(upload.errors) == 0
    assert upload.process_status == ProcessStatus.SUCCESS

    upload_files = UploadFiles.get(upload.upload_id)
    if published:
        assert isinstance(upload_files, PublicUploadFiles)
    else:
        assert isinstance(upload_files, StagingUploadFiles)

    for entry in Entry.objects(upload_id=upload.upload_id):
        assert entry.parser_name is not None
        assert entry.mainfile is not None
        assert entry.process_status == ProcessStatus.SUCCESS

        with upload_files.read_archive(entry.entry_id) as archive:
            entry_archive = archive[entry.entry_id]
            assert 'run' in entry_archive
            assert 'metadata' in entry_archive
            assert 'processing_logs' in entry_archive

            has_test_event = False
            for log_data in entry_archive['processing_logs']:
                for key in ['event', 'entry_id', 'level']:
                    key in log_data
                assert 'workflow_ids' in log_data
                has_test_event = (
                    has_test_event or log_data['event'] == 'a test log entry'
                )

            assert has_test_event
        assert len(entry.errors) == 0

        with upload_files.raw_file(entry.mainfile) as f:
            f.read()

        entry_metadata = entry.full_entry_metadata(upload)

        for path in entry_metadata.files:
            with upload_files.raw_file(path) as f:
                f.read()

        # check some (domain) metadata
        assert entry_metadata.quantities
        assert len(entry_metadata.quantities) > 0
        assert len(entry_metadata.processing_errors) == 0
        assert entry_metadata.process_status == entry.process_status

        assert upload.get_entry(entry.entry_id) is not None

        upload_files.close()

    search_results = search(owner=None, query={'upload_id': upload.upload_id})
    assert (
        search_results.pagination.total
        == Entry.objects(upload_id=upload.upload_id).count()
    )
    for entry in search_results.data:
        assert entry['published'] == published
        assert entry['upload_id'] == upload.upload_id


def assert_user_metadata(entries_metadata, user_metadata):
    for entry_metadata in entries_metadata:
        entry_metadata_dict = entry_metadata.m_to_dict()
        for k, value_expected in user_metadata.items():
            value_actual = entry_metadata_dict[k]
            assert value_actual == value_expected, (
                f'Mismatch {k}: {value_expected} != {value_actual}'
            )


@pytest.mark.asyncio
async def test_processing(processed, no_warn, mails, monkeypatch):
    assert_processing(processed)

    assert len(mails.messages) == 1
    assert (
        re.search(r'Processing completed', mails.messages[0].data.decode('utf-8'))
        is not None
    )


@pytest.mark.asyncio
async def test_processing_two_runs(user1, temporal_worker, tmp):
    upload_file = create_template_upload_file(
        tmp, mainfiles=['tests/data/proc/templates/template_tworuns.json']
    )
    async with temporal_worker():
        processed = await asyncio.to_thread(
            lambda: run_processing(('test_upload_id', upload_file), user1)
        )
    assert_processing(processed)


@pytest.mark.asyncio
async def test_processing_with_large_dir(user1, temporal_worker, tmp):
    upload_path = create_template_upload_file(
        tmp, mainfiles=['tests/data/proc/templates/template.json'], auxfiles=150
    )
    upload_id = os.path.basename(upload_path)[:-4]
    async with temporal_worker():
        upload = await asyncio.to_thread(
            lambda: run_processing((upload_id, upload_path), user1)
        )
    for entry in upload.successful_entries:
        assert len(entry.warnings) >= 1


@pytest.mark.asyncio
async def test_publish(
    elastic_function,
    non_empty_processed_with_temporal: Upload,
    no_warn,
    internal_example_user_metadata,
    monkeypatch,
    temporal_worker,
):
    processed = non_empty_processed_with_temporal
    set_upload_entry_metadata(processed, internal_example_user_metadata)

    additional_keys = ['with_embargo']
    metadata_to_check = internal_example_user_metadata.copy()
    metadata_to_check['with_embargo'] = True

    async with temporal_worker():
        await asyncio.to_thread(lambda: processed.publish_upload(embargo_length=36))
        try:
            await processed.await_workflows()
        except Exception:
            pass

    with processed.entries_metadata() as entries:
        assert_user_metadata(entries, metadata_to_check)
        assert_upload_files(
            processed.upload_id, entries, PublicUploadFiles, published=True
        )
        assert_search_upload(entries, additional_keys, published=True)

    assert_processing(
        Upload.get(processed.upload_id), published=True, process='_publish_upload'
    )


@pytest.mark.asyncio
async def test_publish_directly(
    non_empty_uploaded, user1, temporal_worker, no_warn, monkeypatch, elastic_function
):
    async with temporal_worker():
        processed = await asyncio.to_thread(
            lambda: run_processing(non_empty_uploaded, user1, publish_directly=True)
        )

    with processed.entries_metadata() as entries:
        assert_upload_files(
            processed.upload_id, entries, PublicUploadFiles, published=True
        )
        assert_search_upload(entries, [], published=True)

    assert_processing(Upload.get(processed.upload_id), published=True)


@pytest.mark.asyncio
async def test_unpublish(
    non_empty_uploaded, user1, temporal_worker, no_warn, elastic_function
):
    async with temporal_worker():
        processed = await asyncio.to_thread(
            lambda: run_processing(non_empty_uploaded, user1, publish_directly=True)
        )

    assert_processing(Upload.get(processed.upload_id), published=True)
    with processed.entries_metadata() as entries:
        assert_upload_files(processed.upload_id, entries, PublicUploadFiles)
        assert_search_upload(entries, published=True)

    processed.unpublish_upload()

    assert_processing(Upload.get(processed.upload_id), published=False)
    with processed.entries_metadata() as entries:
        assert_upload_files(processed.upload_id, entries, StagingUploadFiles)
        assert_search_upload(entries, published=False)


@pytest.mark.asyncio
async def test_republish(
    elastic_function,
    non_empty_processed_with_temporal: Upload,
    no_warn,
    internal_example_user_metadata,
    monkeypatch,
    temporal_worker,
):
    processed = non_empty_processed_with_temporal
    set_upload_entry_metadata(processed, internal_example_user_metadata)

    additional_keys = ['with_embargo']
    metadata_to_check = internal_example_user_metadata.copy()
    metadata_to_check['with_embargo'] = True

    async with temporal_worker():
        await asyncio.to_thread(lambda: processed.publish_upload(embargo_length=36))
        await processed.await_workflows()
    assert processed.upload_id.startswith('examples_template_')
    assert Upload.get(processed.upload_id) is not None

    async with temporal_worker():
        await asyncio.to_thread(processed.publish_upload)
        await asyncio.to_thread(lambda: processed.block_until_complete(interval=0.01))

    with processed.entries_metadata() as entries:
        assert_user_metadata(entries, metadata_to_check)
        assert_upload_files(
            processed.upload_id, entries, PublicUploadFiles, published=True
        )
        assert_search_upload(entries, additional_keys, published=True)


@pytest.mark.asyncio
async def test_publish_failed(
    elastic_function,
    non_empty_uploaded: tuple[str, str],
    internal_example_user_metadata,
    user1,
    monkeypatch,
    temporal_worker,
):
    mock_failure(Entry, 'parsing', monkeypatch)

    async with temporal_worker():
        processed = await asyncio.to_thread(
            lambda: run_processing(non_empty_uploaded, user1)
        )
        set_upload_entry_metadata(processed, internal_example_user_metadata)

        additional_keys = ['with_embargo']
        metadata_to_check = internal_example_user_metadata.copy()
        metadata_to_check['with_embargo'] = True

        await asyncio.to_thread(lambda: processed.publish_upload(embargo_length=36))
        try:
            await processed.await_workflows()
        except Exception:
            pass

    with processed.entries_metadata() as entries:
        assert_user_metadata(entries, metadata_to_check)
        assert_search_upload(entries, additional_keys, published=True, processed=False)


@pytest.mark.asyncio
async def test_processing_with_warning(temporal_worker, user1, tmp):
    example_file = create_template_upload_file(
        tmp, 'tests/data/proc/templates/with_warning_template.json'
    )
    example_upload_id = os.path.basename(example_file).replace('.zip', '')

    async with temporal_worker():
        upload = await asyncio.to_thread(
            lambda: run_processing((example_upload_id, example_file), user1)
        )
    assert_processing(upload)


@pytest.mark.asyncio
async def test_process_non_existing(
    temporal_worker,
    user1,
):
    async with temporal_worker():
        upload = await asyncio.to_thread(
            lambda: run_processing(('__does_not_exist', '__does_not_exist'), user1)
        )

    assert not upload.process_running
    assert upload.process_status == ProcessStatus.FAILURE
    assert len(upload.errors) > 0


@pytest.mark.parametrize('with_failure', [None, 'before', 'after', 'not-matched'])
@pytest.mark.asyncio
async def test_re_processing(
    elastic_function,
    published: Upload,
    internal_example_user_metadata,
    monkeypatch,
    tmp,
    with_failure,
    temporal_worker,
):
    if with_failure == 'not-matched':
        monkeypatch.setattr('nomad.config.reprocess.use_original_parser', True)

    if with_failure == 'before':
        entry = published.entries_sublist(0, 1)[0]
        entry.process_status = ProcessStatus.FAILURE
        entry.errors = ['example error']
        entry.save()
        assert published.failed_entries_count > 0

    assert published.published
    assert published.upload_files.to_staging() is None

    old_upload_time = published.last_update
    first_entry: Entry = published.entries_sublist(0, 1)[0]
    old_entry_time = first_entry.last_processing_time

    with published.upload_files.read_archive(first_entry.entry_id) as reader:
        reader[first_entry.entry_id]['processing_logs']

    old_archive_files = list(
        archive_file
        for archive_file in os.listdir(published.upload_files.os_path)
        if 'archive' in archive_file
    )

    metadata_to_check = internal_example_user_metadata.copy()
    metadata_to_check['with_embargo'] = True

    with published.entries_metadata() as entries_generator:
        entries = list(entries_generator)
        assert_user_metadata(entries, metadata_to_check)

    if with_failure != 'not-matched':
        for archive_file in old_archive_files:
            # delete all archive files
            os.remove(published.upload_files.join_file(archive_file).os_path)

    if with_failure == 'after':
        raw_files = create_template_upload_file(
            tmp, 'tests/data/proc/templates/unparsable/template.json'
        )
    elif with_failure == 'not-matched':
        monkeypatch.setattr(
            'nomad.parsing.artificial.TemplateParser.is_mainfile',
            lambda *args, **kwargs: False,
        )
        raw_files = create_template_upload_file(
            tmp, 'tests/data/proc/templates/different_atoms/template.json'
        )
    else:
        raw_files = create_template_upload_file(
            tmp, 'tests/data/proc/templates/different_atoms/template.json'
        )

    upath = FSUtility.upath(
        published.upload_files.join_file('raw-restricted.plain.zip')
    )
    fs, location = upath.fs, upath.path
    fs.put_file(raw_files, location)

    # reprocess
    monkeypatch.setattr('nomad.config.meta.version', 're_process_test_version')
    async with temporal_worker():
        await asyncio.to_thread(published.process_upload)
        await published.await_workflows()

    published.reload()
    first_entry.reload()

    # assert new process time
    if with_failure != 'not-matched':
        assert published.last_update is not None
        assert old_upload_time is not None
        assert first_entry.last_processing_time is not None
        assert old_entry_time is not None
        assert published.last_update > old_upload_time
        assert first_entry.last_processing_time > old_entry_time

    # assert new process version
    if with_failure != 'not-matched':
        assert first_entry.nomad_version == 're_process_test_version'

    archive: EntryArchive
    # assert changed archive files
    if with_failure == 'after':
        with published.upload_files.read_archive(
            first_entry.entry_id
        ) as archive_reader:
            assert list(archive_reader[first_entry.entry_id].keys()) == [
                'processing_logs',
                'metadata',
            ]
            archive = EntryArchive.m_from_dict(
                to_json(archive_reader[first_entry.entry_id])
            )

    else:
        with published.upload_files.read_archive(
            first_entry.entry_id
        ) as archive_reader:
            assert (
                len(archive_reader[first_entry.entry_id]) > 2
            )  # contains more then logs and metadata
            archive = EntryArchive.m_from_dict(
                to_json(archive_reader[first_entry.entry_id])
            )

    # assert maintained user metadata (mongo+es)
    assert_upload_files(published.upload_id, entries, PublicUploadFiles, published=True)
    assert_search_upload(entries, published=True)
    if with_failure not in ['after', 'not-matched']:
        assert_processing(Upload.get(published.upload_id), published=True)

    # assert changed entry data
    if with_failure not in ['after']:
        assert archive.results.material.elements[0] == 'H'
    else:
        assert archive.results is None


@pytest.mark.parametrize(
    'publish,old_staging', [(False, False), (True, True), (True, False)]
)
@pytest.mark.asyncio
async def test_re_process_staging(
    non_empty_processed_with_temporal, publish, old_staging, temporal_worker
):
    upload = non_empty_processed_with_temporal

    if publish:
        async with temporal_worker():
            await asyncio.to_thread(upload.publish_upload)
            try:
                await upload.await_workflows()
            except Exception:
                pass

        if old_staging:
            StagingUploadFiles(upload.upload_id, create=True)

    async with temporal_worker():
        await asyncio.to_thread(upload.process_upload)
        try:
            await upload.await_workflows()
        except Exception:
            pass

    assert_processing(upload, published=publish)
    if publish:
        with pytest.raises(KeyError):
            StagingUploadFiles(upload.upload_id)
    else:
        StagingUploadFiles(upload.upload_id)


@pytest.mark.parametrize('published', [False, True])
@pytest.mark.asyncio
async def test_re_process_match(
    non_empty_processed_with_temporal, published, monkeypatch, no_warn, temporal_worker
):
    upload: Upload = non_empty_processed_with_temporal

    if published:
        async with temporal_worker():
            await asyncio.to_thread(lambda: upload.publish_upload(embargo_length=0))
            await upload.await_workflows()

    assert upload.total_entries_count == 1, upload.total_entries_count

    upload_files = UploadFiles.get(upload.upload_id)

    assert not upload_files.raw_exists('vasp.xml')

    if published:
        with upload_files._zip_fs('a') as zip_fs:
            zip_fs.put_file('tests/data/parsers/vasp/vasp.xml', 'vasp.xml')
    else:
        upload_files.to_staging().add_rawfiles('tests/data/parsers/vasp/vasp.xml')

    assert upload_files.raw_exists('vasp.xml')

    async with temporal_worker():
        await asyncio.to_thread(upload.process_upload)
        await upload.await_workflows()

    assert upload.total_entries_count == 2
    if not published:
        assert upload.published == published
        assert not upload.with_embargo


@pytest.mark.parametrize('reuse_parser', [False, True])
@pytest.mark.asyncio
async def test_reuse_parser(
    monkeypatch, tmp, user1, temporal_worker, reuse_parser, no_warn
):
    upload_path = os.path.join(tmp, 'example_upload.zip')
    with zipfile.ZipFile(upload_path, 'w') as zf:
        zf.write('tests/data/parsers/vasp/vasp.xml', 'one/run.vasp.xml')
        zf.write('tests/data/parsers/vasp/vasp.xml', 'two/run.vasp.xml')

    monkeypatch.setattr('nomad.config.process.reuse_parser', reuse_parser)
    async with temporal_worker():
        upload = await asyncio.to_thread(
            lambda: run_processing(
                (
                    'example_upload',
                    upload_path,
                ),
                user1,
            )
        )

    assert upload.total_entries_count == 2
    assert upload.process_status == 'SUCCESS'


@pytest.mark.parametrize(
    'args',
    [
        pytest.param(
            dict(
                add=['new_folder/new_sub_folder'],
                path_filter='new_folder/new_sub_folder/template.json',
                expected_result={
                    'examples_template/template.json': False,
                    'new_folder/new_sub_folder/template.json': True,
                },
            ),
            id='add-one-filter-file',
        ),
        pytest.param(
            dict(
                add=['new_folder/new_sub_folder'],
                path_filter='new_folder/new_sub_folder',
                expected_result={
                    'examples_template/template.json': False,
                    'new_folder/new_sub_folder/template.json': True,
                },
            ),
            id='add-one-filter-folder',
        ),
        pytest.param(
            dict(
                add=['new_folder/new_sub_folder1', 'new_folder/new_sub_folder2'],
                path_filter='new_folder',
                expected_result={
                    'examples_template/template.json': False,
                    'new_folder/new_sub_folder1/template.json': True,
                    'new_folder/new_sub_folder2/template.json': True,
                },
            ),
            id='add-two',
        ),
        pytest.param(
            dict(
                add=['examples_template/new_sub_folder'],
                path_filter='examples_template/new_sub_folder',
                expected_result={
                    'examples_template/template.json': False,
                    'examples_template/new_sub_folder/template.json': True,
                },
            ),
            id='add-to-existing-entry-folder',
        ),
        pytest.param(
            dict(
                add=['examples_template/new_sub_folder'],
                delete=['examples_template/template.json'],
                path_filter='examples_template',
                expected_result={
                    'examples_template/new_sub_folder/template.json': True
                },
            ),
            id='add-and-delete',
        ),
        pytest.param(
            dict(
                add=['new_folder/new_sub_folder'],
                path_filter='examples_template',
                expected_result={'examples_template/template.json': True},
            ),
            id='add-one-filter-other',
        ),
        pytest.param(
            dict(
                delete=['examples_template/template.json'],
                path_filter='examples_template',
                expected_result={},
            ),
            id='delete-everything',
        ),
        pytest.param(
            dict(
                add=[
                    'new_folder/new_sub_folder',
                    (example_file_aux, 'examples_template'),
                ],
                only_updated_files=True,
                expected_result={
                    'examples_template/template.json': False,
                    'new_folder/new_sub_folder/template.json': True,
                },
            ),
            id='flag-add-two-files',
        ),
        pytest.param(
            dict(
                add=['new_folder/new_sub_folder', 'examples_template'],
                only_updated_files=True,
                expected_result={
                    'examples_template/template.json': True,
                    'new_folder/new_sub_folder/template.json': True,
                },
            ),
            id='flag-add-new-and-overwrite-old',
        ),
        pytest.param(
            dict(
                add=['new_folder/new_sub_folder'],
                delete=['examples_template/template.json'],
                only_updated_files=True,
                expected_result={'new_folder/new_sub_folder/template.json': True},
            ),
            id='flag-add-new-and-delete-old',
        ),
        pytest.param(
            dict(
                add=['new_folder/new_sub_folder'],
                delete=['examples_template'],
                only_updated_files=True,
                expected_result={'new_folder/new_sub_folder/template.json': True},
            ),
            id='flag-add-new-and-delete-old-folder',
        ),
        pytest.param(
            dict(
                delete=['examples_template'],
                only_updated_files=True,
                expected_result={},
            ),
            id='flag-delete-everything',
        ),
    ],
)
@pytest.mark.asyncio
async def test_process_partial(
    temporal_worker, non_empty_processed_with_temporal: Upload, args
):
    add = args.get('add', [])
    delete = args.get('delete', [])
    path_filter = args.get('path_filter')
    only_updated_files = args.get('only_updated_files', False)
    expected_result = args['expected_result']
    old_timestamps = {
        e.mainfile: e.complete_time
        for e in non_empty_processed_with_temporal.successful_entries
    }
    file_operations = []
    for op in add:
        if type(op) is tuple:
            path, target_dir = op
        else:
            path, target_dir = example_file_mainfile, op
        file_operations.append(
            dict(op='ADD', path=path, target_dir=target_dir, temporary=False)
        )
    for path in delete:
        file_operations.append(dict(op='DELETE', path=path))

    async with temporal_worker():
        await asyncio.to_thread(
            lambda: non_empty_processed_with_temporal.process_upload(
                file_operations,
                path_filter=path_filter,
                only_updated_files=only_updated_files,
            )
        )
        await non_empty_processed_with_temporal.await_workflows()
    search_refresh()  # Process does not wait for search index to be refreshed when deleting
    assert_processing(non_empty_processed_with_temporal)
    new_timestamps = {
        e.mainfile: e.complete_time
        for e in non_empty_processed_with_temporal.successful_entries
    }
    assert new_timestamps.keys() == expected_result.keys()
    for key, expect_updated in expected_result.items():
        if expect_updated:
            if key not in old_timestamps:
                continue

            old_timestamp = old_timestamps[key]
            new_timestamp = new_timestamps[key]
            assert old_timestamp is not None
            assert new_timestamp is not None
            assert old_timestamp < new_timestamp


def test_re_pack(published: Upload):
    upload_id = published.upload_id
    upload_files: PublicUploadFiles = published.upload_files  # type: ignore
    assert upload_files.access == 'restricted'
    assert published.with_embargo

    # Lift embargo
    published.embargo_length = 0
    published.save()
    upload_files.re_pack(with_embargo=False)

    assert upload_files.access == 'public'
    for path_info in upload_files.raw_listdir(recursive=True, files_only=True):
        with upload_files.raw_file(path_info.path) as f:
            f.read()

    for entry in Entry.objects(upload_id=upload_id):
        with upload_files.read_archive(entry.entry_id) as archive:
            to_json(archive[entry.entry_id])

    published.reload()


def mock_failure(cls, function_name, monkeypatch):
    def mock(self, *args, **kwargs):
        raise ProcessFailure('fail for test')

    mock.__name__ = function_name

    monkeypatch.setattr(f'nomad.processing.data.{cls.__name__}.{function_name}', mock)


@pytest.mark.asyncio
async def test_await_workflows_ignores_settled_workflow_failure(monkeypatch):
    workflow_failure = WorkflowFailureError(cause=RuntimeError('workflow failed'))
    handle = SimpleNamespace(result=AsyncMock(side_effect=workflow_failure))
    client = SimpleNamespace(get_workflow_handle=Mock(return_value=handle))
    upload = Upload(
        upload_id='test-upload',
        main_author='test-user',
        process_status=ProcessStatus.FAILURE,
        workflow_ids=['test-workflow'],
    )
    monkeypatch.setattr(upload, 'reload', Mock())

    async def mock_get_client():
        return client

    monkeypatch.setattr('nomad.processing.data.get_client', mock_get_client)

    await upload.await_workflows()

    client.get_workflow_handle.assert_called_once_with('test-workflow')
    handle.result.assert_awaited_once()


@pytest.mark.asyncio
async def test_await_workflows_reraises_active_workflow_failure(monkeypatch):
    workflow_failure = WorkflowFailureError(cause=RuntimeError('workflow failed'))
    handle = SimpleNamespace(result=AsyncMock(side_effect=workflow_failure))
    client = SimpleNamespace(get_workflow_handle=Mock(return_value=handle))
    upload = Upload(
        upload_id='test-upload',
        main_author='test-user',
        process_status=ProcessStatus.FAILURE,
        workflow_ids=['test-workflow'],
    )
    reload_count = 0

    def reload():
        nonlocal reload_count
        reload_count += 1
        if reload_count == 2:
            upload.process_status = ProcessStatus.RUNNING

    monkeypatch.setattr(upload, 'reload', reload)

    async def mock_get_client():
        return client

    monkeypatch.setattr('nomad.processing.data.get_client', mock_get_client)

    with pytest.raises(WorkflowFailureError):
        await upload.await_workflows()


@pytest.mark.parametrize(
    'function', ['update_files', 'match_all', 'cleanup', 'parsing']
)
@pytest.mark.asyncio
async def test_process_failure(
    monkeypatch,
    uploaded,
    function,
    temporal_worker,
    user1,
):
    upload_id, _ = uploaded
    # mock the function to throw exceptions
    if hasattr(Upload, function):
        cls = Upload
    elif hasattr(Entry, function):
        cls = Entry
    else:
        assert False

    mock_failure(cls, function, monkeypatch)

    # run the test
    async with temporal_worker():
        upload = await asyncio.to_thread(lambda: run_processing(uploaded, user1))

    assert not upload.process_running

    if function != 'parsing':
        assert upload.process_status == ProcessStatus.FAILURE
        assert len(upload.errors) > 0
    else:
        # there is an empty example with no entries, even if past parsing_all step
        utils.get_logger(__name__).error('fake')
        if upload.total_entries_count > 0:  # pylint: disable=E1101
            assert upload.process_status == ProcessStatus.SUCCESS
            assert len(upload.errors) == 0
            for entry in upload.entries_sublist(0, 100):  # pylint: disable=E1101
                assert entry.process_status == ProcessStatus.FAILURE
                assert len(entry.errors) > 0

    entry = Entry.objects(upload_id=upload_id).first()
    if entry is not None:
        with upload.upload_files.read_archive(entry.entry_id) as archive:
            entry_archive = archive[entry.entry_id]
            assert 'metadata' in entry_archive
            if function != 'cleanup':
                assert len(entry_archive['metadata']['processing_errors']) > 0
            assert 'processing_logs' in entry_archive
            if function != 'parsing':
                assert 'run' in entry_archive


# consume_ram, segfault, and exit are not testable with the celery test worker
@pytest.mark.parametrize('failure', ['exception'])
@pytest.mark.asyncio
async def test_malicious_parser_failure(temporal_worker, failure, user1, tmp):
    example_file = os.path.join(tmp, 'upload.zip')
    with zipfile.ZipFile(example_file, mode='w') as zf:
        with zf.open('chaos.json', 'w') as f:
            f.write(f'"{failure}"'.encode())
    example_upload_id = f'chaos_{failure}'

    async with temporal_worker():
        upload = await asyncio.to_thread(
            lambda: run_processing((example_upload_id, example_file), user1)
        )

    assert not upload.process_running
    assert len(upload.errors) == 0
    assert upload.process_status == ProcessStatus.SUCCESS

    entries = Entry.objects(upload_id=upload.upload_id)
    assert entries.count() == 1
    entry = next(entries)
    assert not entry.process_running
    assert entry.process_status == ProcessStatus.FAILURE
    assert len(entry.errors) == 1


@pytest.mark.asyncio
async def test_parent_child_parser(temporal_worker, user1, tmp):
    # Create a dummy parser which creates child entries
    class ParentChildParser(Parser):
        name = 'parsers/parentchild'
        creates_children = True

        def is_mainfile(
            self,
            filename: str,
            mime: str,
            buffer: bytes,
            decoded_buffer: str,
            compression: str | None = None,
        ):
            if decoded_buffer.startswith('parentchild\n'):
                return set(
                    [
                        line.strip()
                        for line in decoded_buffer.split('\n')[1:]
                        if line.strip()
                    ]
                )
            return False

        def parse(
            self,
            mainfile: str,
            archive: EntryArchive,
            logger=None,
            child_archives: dict[str, EntryArchive] | None = None,
        ):
            archive.metadata.comment = 'parent'
            if child_archives is None:
                return
            for mainfile_key, child_archive in child_archives.items():
                child_archive.metadata.comment = mainfile_key

    # Register it
    test_parser = ParentChildParser()
    parsers.parsers.append(test_parser)
    parsers.parser_dict[test_parser.name] = test_parser

    # Test it
    example_upload_id = 'parentchild'
    example_filename = 'parentchild.txt'
    example_filepath = os.path.join(tmp, example_filename)
    for children in [('child1', 'child2'), ('child2', 'child3', 'child4')]:
        with open(example_filepath, 'w') as f:
            f.write('\n'.join(['parentchild', *children]))

        async with temporal_worker():
            upload = await asyncio.to_thread(
                lambda: run_processing((example_upload_id, example_filepath), user1)
            )

        assert upload.process_status == ProcessStatus.SUCCESS
        assert upload.total_entries_count == len(children) + 1
        assert set([e.mainfile_key for e in upload.successful_entries]) == set(
            [None, *children]
        )
        for entry in upload.successful_entries:
            metadata = entry.full_entry_metadata(upload)
            assert metadata.comment == (entry.mainfile_key or 'parent')

    upload = await asyncio.to_thread(lambda: Upload.get(upload.upload_id))
    async with temporal_worker():
        await asyncio.to_thread(
            lambda: upload.process_upload(
                file_operations=[dict(op='DELETE', path=example_filename)]
            )
        )
        await upload.await_workflows()
    assert upload.process_status == ProcessStatus.SUCCESS
    assert upload.total_entries_count == 0


@pytest.mark.asyncio
async def test_creating_new_entries_during_processing(temporal_worker, user1):
    """
    Tests a use-case where a schema has a normalizer that adds new mainfiles during processing.
    """
    upload_id = 'test_create_during_processing'
    upload = Upload.create(upload_id=upload_id, main_author=user1)
    upload_files = StagingUploadFiles(upload_id, create=True)
    with upload_files.raw_file('batch.archive.json', 'w') as outfile:
        json.dump(
            {
                'data': {
                    'm_def': 'tests.processing.test_data.BatchForTest',
                    'batch_id': 'my_batch',
                    'n_samples': 5,
                }
            },
            outfile,
        )
    async with temporal_worker():
        await asyncio.to_thread(upload.process_upload)
        await upload.await_workflows()
    assert upload.process_status == ProcessStatus.SUCCESS
    assert upload.total_entries_count == 6
    assert upload.failed_entries_count == 0
    for entry in upload.entries_sublist(0, 6):
        assert entry.process_status == ProcessStatus.SUCCESS
        if entry.mainfile.startswith('my_batch_'):
            idx = int(entry.mainfile.split('.')[0].split('_')[-1])
            with upload_files.read_archive(entry.entry_id) as archive:
                assert archive[entry.entry_id]['data']['batch_id'] == 'my_batch'
                assert archive[entry.entry_id]['data']['sample_number'] == idx


@pytest.mark.asyncio
async def test_qcms_data(
    elastic_function,
    temporal_worker,
    user1,
):
    async with temporal_worker():
        upload = await asyncio.to_thread(
            lambda: run_processing(
                ('test_qcms_upload', 'tests/data/proc/examples_qcms.zip'), user1
            )
        )

    additional_keys = [
        'results.method.simulation.program_name',
        'results.material.elements',
    ]
    assert upload.total_entries_count == 1
    assert len(upload.successful_entries) == 1

    with upload.entries_metadata() as entries:
        assert_upload_files(
            upload.upload_id, entries, StagingUploadFiles, published=False
        )
        assert_search_upload(entries, additional_keys, published=False)


@pytest.mark.asyncio
async def test_phonopy_data(
    elastic_function,
    temporal_worker,
    user1,
):
    async with temporal_worker():
        upload = await asyncio.to_thread(
            lambda: run_processing(
                ('test_upload', 'tests/data/proc/examples_phonopy.zip'), user1
            )
        )

    additional_keys = ['results.method.simulation.program_name']
    assert upload.total_entries_count == 2
    assert len(upload.successful_entries) == 2

    with upload.entries_metadata() as entries:
        assert_upload_files(
            upload.upload_id, entries, StagingUploadFiles, published=False
        )
        assert_search_upload(entries, additional_keys, published=False)


@pytest.mark.asyncio
async def test_read_metadata_from_file(temporal_worker, user1, user2, tmp):
    upload_file = os.path.join(tmp, 'upload.zip')
    with zipfile.ZipFile(upload_file, 'w') as zf:
        zf.write(
            'tests/data/proc/templates/template.json', 'examples/entry_1/template.json'
        )
        zf.write(
            'tests/data/proc/templates/template.json', 'examples/entry_2/template.json'
        )
        zf.write(
            'tests/data/proc/templates/template.json', 'examples/entry_3/template.json'
        )
        zf.write('tests/data/proc/templates/template.json', 'examples/template.json')
        entry_1 = dict(
            comment='Entry 1 of 3',
            references='http://test1.com',
            external_id='external_id_1',
        )
        with zf.open('examples/entry_1/nomad.yaml', 'w') as f:
            f.write(yaml.dump(entry_1).encode())
        entry_2 = dict(
            comment='Entry 2 of 3',
            references=['http://test2.com'],
            external_id='external_id_2',
        )
        with zf.open('examples/entry_2/nomad.json', 'w') as f:
            f.write(json.dumps(entry_2).encode())
        metadata = {
            'upload_name': 'my name',
            'coauthors': user2.user_id,
            'references': ['http://test0.com'],
            'entries': {
                'examples/entry_3/template.json': {
                    'comment': 'Entry 3 of 3',
                    'references': 'http://test3.com',
                    'external_id': 'external_id_3',
                },
                'examples/entry_1/template.json': {'comment': 'root entries comment 1'},
            },
        }
        with zf.open('nomad.json', 'w') as f:
            f.write(json.dumps(metadata).encode())

    async with temporal_worker():
        upload = await asyncio.to_thread(
            lambda: run_processing(('test_upload', upload_file), user1)
        )

    entries = Entry.objects(upload_id=upload.upload_id)
    entries = sorted(entries, key=lambda entry: entry.mainfile)

    comment = ['root entries comment 1', 'Entry 2 of 3', 'Entry 3 of 3', None]
    external_ids = ['external_id_1', 'external_id_2', 'external_id_3', None]
    references = [
        ['http://test1.com'],
        ['http://test2.com'],
        ['http://test3.com'],
        ['http://test0.com'],
    ]
    expected_coauthors = [user2]

    for i in range(len(entries)):
        entry_metadata = entries[i].full_entry_metadata(upload)
        assert entry_metadata.comment == comment[i]
        assert entry_metadata.references == references[i]
        assert entry_metadata.external_id == external_ids[i]
        coauthors = entry_metadata.coauthors
        assert len(coauthors) == len(expected_coauthors)
        for j in range(len(coauthors)):
            assert coauthors[j].user_id == expected_coauthors[j].user_id
            assert coauthors[j].username == expected_coauthors[j].username
            assert coauthors[j].email == expected_coauthors[j].email
            assert coauthors[j].first_name == expected_coauthors[j].first_name
            assert coauthors[j].last_name == expected_coauthors[j].last_name


@pytest.mark.asyncio
async def test_skip_matching(temporal_worker, user1):
    async with temporal_worker():
        upload = await asyncio.to_thread(
            lambda: run_processing(
                ('test_skip_matching', 'tests/data/proc/skip_matching.zip'), user1
            )
        )
    assert upload.total_entries_count == 1


@pytest.mark.parametrize(
    'url,normalized_url',
    [
        pytest.param(
            '../upload/archive/test_id#/data/test_section/0', None, id='entry-id'
        ),
        pytest.param(
            '../upload/archive/mainfile/my/test/file#/data/test_section/0',
            '../upload/archive/test_id#/data/test_section/0',
            id='mainfile',
        ),
    ],
)
def test_upload_context(
    raw_files_function, mongo_function, user1, url, normalized_url, monkeypatch
):
    monkeypatch.setattr(
        'nomad.utils.generate_entry_id', lambda *args, **kwargs: 'test_id'
    )

    data = ExampleData(main_author=user1)
    data.create_upload(upload_id='test_id', published=True)

    referenced_archive = EntryArchive(data=DataForTest())
    referenced_archive.data.test_section.append(SectionForTest())

    data.create_entry(
        upload_id='test_id',
        entry_id='test_id',
        mainfile='my/test/file',
        entry_archive=referenced_archive,
    )

    data.save(with_es=False)

    upload = Upload.objects(upload_id='test_id').first()
    assert upload is not None

    context = ServerContext(upload=upload)
    test_archive = EntryArchive(m_context=context)

    section_reference = ReferenceSectionForTest()
    test_archive.data = DataForTest(reference_section=section_reference)
    assert section_reference.m_root().m_context is not None
    section_reference.reference = url
    assert (
        section_reference.m_to_dict()['reference'] == normalized_url
        if normalized_url
        else url
    )
    assert section_reference.reference.m_root().metadata.entry_id == 'test_id'


@pytest.mark.asyncio
@pytest.mark.parametrize('exclude_potcar', [pytest.param(True), pytest.param(False)])
async def test_exclude_potcar(user1, temporal_worker, monkeypatch, exclude_potcar):
    monkeypatch.setattr('nomad.config.process.exclude_potcar', exclude_potcar)

    async with temporal_worker():
        upload = await asyncio.to_thread(
            lambda: run_processing(
                ('test_upload', 'tests/data/proc/vasp.potcar.zip'), user1
            )
        )

    for ext in ['', '.gz', '.xz', '.bz2']:
        assert upload.upload_files.raw_exists(f'test{ext}/vasprun.xml')
        assert upload.upload_files.raw_exists(f'test{ext}/POTCAR{ext}.stripped')
        with upload.upload_files.raw_file(f'test{ext}/POTCAR{ext}.stripped') as f:
            content = f.read().decode()
            assert 'Stripped POTCAR file' in content
            assert 'PAW_PBE' in content
            assert 'local part' not in content
        potcar_exists = upload.upload_files.raw_exists(f'test{ext}/POTCAR{ext}')
        if exclude_potcar:
            assert not potcar_exists
            assert 'Removing POTCAR file from upload.' in upload.warnings
        else:
            assert potcar_exists


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'wait_for_processing, should_fail, expected_error_message',
    [
        pytest.param(True, False, None, id='wait-for-result-success'),
        pytest.param(
            True,
            True,
            'Failed to execute temporal workflow: boom',
            id='wait-for-result-error',
        ),
        pytest.param(False, False, None, id='background-success'),
        pytest.param(
            False,
            True,
            'Failed to start temporal workflow: boom',
            id='background-error',
        ),
    ],
)
async def test_start_edit_upload_metadata_workflow(
    monkeypatch, wait_for_processing, should_fail, expected_error_message
):
    upload = Upload(upload_id='test-upload', main_author='test-author')
    handle = object()
    execute_workflow = AsyncMock()
    start_workflow = AsyncMock(return_value=handle)
    if should_fail:
        execute_workflow = AsyncMock(side_effect=RuntimeError('boom'))
        start_workflow = AsyncMock(side_effect=RuntimeError('boom'))

    client = SimpleNamespace(
        execute_workflow=execute_workflow,
        start_workflow=start_workflow,
    )
    save = Mock()

    async def mock_get_client():
        return client

    monkeypatch.setattr('nomad.processing.data.get_client', mock_get_client)
    monkeypatch.setattr(upload, 'save', save)

    def mock_setup_upload_for_workflow(workflow_id, process_name):
        upload.process_status = ProcessStatus.PENDING
        upload.current_process = process_name
        return {}

    monkeypatch.setattr(
        upload, 'setup_upload_for_workflow', mock_setup_upload_for_workflow
    )

    if should_fail:
        with pytest.raises(ProcessFailure) as exc:
            await upload._start_edit_upload_metadata_workflow(
                {'metadata': {'embargo_length': 0}},
                'test-user',
                wait_for_processing=wait_for_processing,
            )
        assert str(exc.value) == expected_error_message
    else:
        result = await upload._start_edit_upload_metadata_workflow(
            {'metadata': {'embargo_length': 0}},
            'test-user',
            wait_for_processing=wait_for_processing,
        )
        if wait_for_processing:
            assert result is None
            save.assert_not_called()
        else:
            assert result is handle
            assert upload.process_status == ProcessStatus.PENDING
            save.assert_not_called()

    if wait_for_processing:
        client.execute_workflow.assert_awaited_once()
        client.start_workflow.assert_not_called()
    else:
        client.execute_workflow.assert_not_called()
        client.start_workflow.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'wait_for_processing, should_fail, expected_error_message',
    [
        pytest.param(True, False, None, id='wait-for-result-success'),
        pytest.param(
            True,
            True,
            'Failed to execute temporal workflow: boom',
            id='wait-for-result-error',
        ),
        pytest.param(False, False, None, id='background-success'),
        pytest.param(
            False,
            True,
            'Failed to start temporal workflow: boom',
            id='background-error',
        ),
    ],
)
async def test_start_delete_upload_workflow(
    monkeypatch, wait_for_processing, should_fail, expected_error_message
):
    upload = Upload(upload_id='test-upload', main_author='test-author')
    handle = object()
    execute_workflow = AsyncMock()
    start_workflow = AsyncMock(return_value=handle)
    if should_fail:
        execute_workflow = AsyncMock(side_effect=RuntimeError('boom'))
        start_workflow = AsyncMock(side_effect=RuntimeError('boom'))

    client = SimpleNamespace(
        execute_workflow=execute_workflow,
        start_workflow=start_workflow,
    )
    setup_delete_upload_workflow = Mock()
    cleanup_upload_after_workflow_fail = Mock()

    async def mock_get_client():
        return client

    def mock_setup_delete_upload_workflow(
        workflow_id, previous_workflow_ids, previous_process_status
    ):
        upload.process_status = ProcessStatus.PENDING
        upload.current_process = 'delete_upload'
        upload.workflow_ids = [workflow_id]
        setup_delete_upload_workflow(
            workflow_id, previous_workflow_ids, previous_process_status
        )
        return {}

    monkeypatch.setattr('nomad.processing.data.get_client', mock_get_client)
    monkeypatch.setattr(
        upload, 'setup_delete_upload_workflow', mock_setup_delete_upload_workflow
    )
    monkeypatch.setattr(
        upload,
        'cleanup_upload_after_workflow_fail',
        cleanup_upload_after_workflow_fail,
    )

    if should_fail:
        with pytest.raises(ProcessFailure) as exc:
            await upload._start_delete_upload_workflow(
                wait_for_processing=wait_for_processing
            )
        assert str(exc.value) == expected_error_message
        cleanup_upload_after_workflow_fail.assert_called_once()
    else:
        result = await upload._start_delete_upload_workflow(
            wait_for_processing=wait_for_processing
        )
        if wait_for_processing:
            assert result is None
        else:
            assert result is handle
        assert upload.process_status == ProcessStatus.PENDING
        assert upload.current_process == 'delete_upload'
        cleanup_upload_after_workflow_fail.assert_not_called()

    setup_delete_upload_workflow.assert_called_once_with(
        setup_delete_upload_workflow.call_args.args[0], [], ProcessStatus.READY
    )
    if wait_for_processing:
        client.execute_workflow.assert_awaited_once()
        client.start_workflow.assert_not_called()
        workflow_input = client.execute_workflow.await_args.args[1]
    else:
        client.execute_workflow.assert_not_called()
        client.start_workflow.assert_awaited_once()
        workflow_input = client.start_workflow.await_args.args[1]
    assert workflow_input.upload_id == 'test-upload'


@pytest.mark.asyncio
async def test_delete_upload_terminates_non_delete_workflows_before_setup():
    upload = Upload(
        upload_id='test-upload',
        main_author='test-author',
        process_status=ProcessStatus.RUNNING,
        workflow_ids=['process-workflow'],
    )
    terminate = AsyncMock()
    handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(
                raw_description=SimpleNamespace(
                    workflow_execution_info=SimpleNamespace(
                        type=SimpleNamespace(name='UpdateUploadWorkflow')
                    )
                )
            )
        ),
        terminate=terminate,
    )
    client = SimpleNamespace(get_workflow_handle=Mock(return_value=handle))

    (
        workflow_ids,
        process_status,
    ) = await upload._terminate_non_delete_workflows_for_delete(client)

    assert workflow_ids == ['process-workflow']
    assert process_status == ProcessStatus.RUNNING
    client.get_workflow_handle.assert_called_once_with('process-workflow')
    terminate.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_upload_does_not_preempt_existing_delete_workflow():
    upload = Upload(
        upload_id='test-upload',
        main_author='test-author',
        process_status=ProcessStatus.PENDING,
        workflow_ids=['delete-workflow'],
    )
    handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(
                raw_description=SimpleNamespace(
                    workflow_execution_info=SimpleNamespace(
                        type=SimpleNamespace(name='DeleteUploadWorkflow')
                    )
                )
            )
        ),
        terminate=AsyncMock(),
    )
    client = SimpleNamespace(get_workflow_handle=Mock(return_value=handle))

    with pytest.raises(ProcessAlreadyRunning):
        await upload._terminate_non_delete_workflows_for_delete(client)

    handle.terminate.assert_not_called()
