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
import itertools
import os
import pathlib
import re
import shutil
import time
import uuid
import zipfile
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pytest
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.memory import MemoryFileSystem

from nomad import datamodel, utils
from nomad.archive import to_json
from nomad.config import config
from nomad.config.models.config import BundleExportSettings
from nomad.files import (
    DirectoryObject,
    FSUtility,
    PathObject,
    PublicUploadFiles,
    StagingUploadFiles,
    UploadFiles,
    clear_index_caches,
    empty_archive_file_size,
    empty_zip_file_size,
    measure_fs_reads,
    public_storage,
)
from nomad.files.public_storage import (
    MARKER_FILENAME,
    ArtifactRecord,
    RemoteReadyMarker,
    choose_pack_fs,
    complete_published_write,
    copy_to_remote,
    delete_access_artifacts,
    detect_published_access,
    invalidate_ready_cache,
    write_ready_marker,
)
from nomad.files.zip_index import RangeTailFile
from nomad.mongo.package import PackageDefinition
from nomad.processing import Upload

EntryWithFiles = tuple[datamodel.EntryMetadata, str]
UploadWithFiles = tuple[str, list[datamodel.EntryMetadata], UploadFiles]
StagingUploadWithFiles = tuple[str, list[datamodel.EntryMetadata], StagingUploadFiles]
PublicUploadWithFiles = tuple[str, list[datamodel.EntryMetadata], PublicUploadFiles]

# example_file uses an artificial parser for faster test execution, can also be
# changed to examples_vasp.zip for using vasp parser
example_mainfile_raw_path = 'examples_template/template.json'

example_file = 'tests/data/proc/examples_template.zip'
example_directory = 'tests/data/proc/examples_template'
example_file_contents = [
    'examples_template/template.json',
    'examples_template/1.aux',
    'examples_template/2.aux',
    'examples_template/3.aux',
    'examples_template/4.aux',
]
example_file_aux = 'tests/data/proc/examples_template/1.aux'
example_file_mainfile = 'tests/data/proc/examples_template/template.json'
example_file_mainfile_different_atoms = (
    'tests/data/proc/templates/different_atoms/template.json'
)
example_file_unparsable = 'tests/data/proc/templates/unparsable/template.json'
example_file_vasp_with_binary = 'tests/data/proc/example_vasp_with_binary.zip'
example_file_corrupt_zip = 'tests/data/proc/examples_corrupt_zip.zip'
empty_file = 'tests/data/proc/empty.zip'
example_archive_contents = {
    'run': [],
    'metadata': {},
    'processing_logs': [{'entry': 'test'}],
}


@pytest.fixture(scope='function', autouse=True)
def raw_files_on_all_tests(raw_files_function):
    """Autouse fixture to apply raw_files to all tests."""
    pass


@pytest.fixture(scope='session')
def example_mainfile_contents():
    with zipfile.ZipFile(example_file, 'r') as zf:
        with zf.open(example_mainfile_raw_path) as f:
            return f.read().decode()


def test_measure_fs_reads_counts_only_within_context(tmp_path):
    path = str(tmp_path / 'data.bin')
    with open(path, 'wb') as f:
        f.write(b'x' * 4096)

    # Reads outside a measurement context are not counted.
    with FSUtility.open(path) as f:
        f.read()

    with measure_fs_reads() as stats:
        with FSUtility.open(path) as f:
            assert f.read() == b'x' * 4096
        assert stats.read_bytes == 4096
        assert stats.read_time >= 0.0

    # Reads after the context exits are not counted.
    with FSUtility.open(path) as f:
        f.read()
    assert stats.read_bytes == 4096


def test_s3_cached_identity_skips_head_on_first_read(monkeypatch):
    from types import SimpleNamespace

    from s3fs import S3FileSystem

    from nomad.files.index_cache import CachedIndex, ObjectIdentity

    calls: list[tuple[str, dict]] = []

    class Body:
        async def read(self):
            return b'x'

        def close(self):
            pass

    async def call_s3(operation, *args, **kwargs):
        calls.append((operation, kwargs))
        if operation == 'head_object':
            return {'ContentLength': 100, 'ETag': '"abc"'}
        assert operation == 'get_object'
        return {'ContentLength': 100, 'ETag': '"abc"', 'Body': Body()}

    fs = S3FileSystem(anon=True, skip_instance_cache=True)
    monkeypatch.setattr(fs, '_call_s3', call_s3)
    with FSUtility.open(
        'bucket/key', fs=fs, size=100, if_match='"abc"', block_size=20
    ) as file_obj:
        assert file_obj.read(1) == b'x'

    assert [operation for operation, _ in calls] == ['get_object']
    assert calls[0][1]['IfMatch'] == '"abc"'

    calls.clear()
    with fs.open('bucket/key', 'rb', block_size=20) as file_obj:
        assert file_obj.read(1) == b'x'

    assert [operation for operation, _ in calls] == ['head_object', 'get_object']
    assert calls[1][1]['IfMatch'] == '"abc"'

    calls.clear()
    public = object.__new__(PublicUploadFiles)
    public.__dict__['storage_fs'] = fs
    public._zip = CachedIndex(ObjectIdentity('bucket/key', '"abc"', 100), None, '')
    public.raw_zip_file_object = lambda: SimpleNamespace(location='bucket/key')
    with public._open_zip_member_fileobj() as file_obj:
        assert file_obj.read(1) == b'x'

    assert [operation for operation, _ in calls] == ['get_object']
    assert calls[0][1]['IfMatch'] == '"abc"'


def test_measure_fs_reads_nested_contexts_accumulate(tmp_path):
    path = str(tmp_path / 'data.bin')
    with open(path, 'wb') as f:
        f.write(b'y' * 2048)

    with measure_fs_reads() as outer:
        with measure_fs_reads() as inner:
            with FSUtility.open(path) as f:
                f.read()
        assert inner.read_bytes == 2048
        assert outer.read_bytes == 2048


class TestObjects:
    @pytest.fixture(scope='function')
    def test_area(self):
        yield config.fs.staging

        if os.path.exists(config.fs.staging):
            shutil.rmtree(config.fs.staging)

    def test_file_dir_existing(self, test_area):
        file = PathObject(os.path.join(test_area, 'sub/test_id'))
        assert not os.path.exists(os.path.dirname(file.os_path))

    @pytest.mark.parametrize('dirpath', ['test', os.path.join('sub', 'test')])
    @pytest.mark.parametrize('create', [True, False])
    def test_directory(self, test_area: str, dirpath: str, create: bool) -> None:
        directory = DirectoryObject(os.path.join(test_area, dirpath), create=create)
        assert directory.exists() == create
        assert os.path.isdir(directory.os_path) == create

    @pytest.mark.parametrize('dirpath', ['test', os.path.join('sub', 'test')])
    @pytest.mark.parametrize('create', [True, False])
    @pytest.mark.parametrize('join_create', [True, False])
    def test_directory_join(
        self, test_area: str, dirpath: str, create: bool, join_create: bool
    ) -> None:
        directory = DirectoryObject(os.path.join(test_area, 'parent'), create=create)
        directory = directory.join_dir(dirpath, create=join_create)

        assert directory.exists() == join_create
        assert os.path.isdir(directory.os_path) == join_create


example_entry: dict[str, Any] = {
    'entry_id': '0',
    'mainfile': 'examples_template/template.json',
    'data': 'value',
}
example_entry_id = example_entry['entry_id']


def generate_example_entry(
    entry_id: int, with_mainfile_prefix: bool, subdirectory: str | None = None, **kwargs
) -> EntryWithFiles:
    """Generate an example entry with :class:`EntryMetadata` and rawfile."""

    example_entry = datamodel.EntryMetadata(domain='dft', entry_id=str(entry_id))

    if with_mainfile_prefix:
        mainfile = f'{entry_id}.template.json'
    else:
        mainfile = 'template.json'

    if subdirectory is not None:
        mainfile = os.path.join(subdirectory, mainfile)

    example_entry.mainfile = mainfile
    example_entry.m_update(**kwargs)

    example_file = os.path.join(config.fs.tmp, 'example.zip')
    example_entry.files = []
    with zipfile.ZipFile(example_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for filepath in example_file_contents:
            filename = os.path.basename(filepath)
            arcname = filename
            if arcname == 'template.json' and with_mainfile_prefix:
                arcname = f'{entry_id}.template.json'

            if subdirectory is not None:
                arcname = os.path.join(subdirectory, arcname)
            example_entry.files.append(arcname)
            zf.write(os.path.join(example_directory, filename), arcname)

    return example_entry, example_file


def assert_example_files(names, with_mainfile: bool = True):
    # TODO its complicated
    # To compare the files with the example_file_contents list we have to assume
    # - different subdirectories
    # - mainfile prefixes
    # - mainfiles among aux files
    is_multi = any(re.search(r'[0-9].t', name) for name in names)

    def normalized_file(name):
        name = re.sub(r'[0-9].t', 't', name)
        name = re.sub(r'^[0-9]\/', '', name)
        return name

    source = sorted(
        set(
            normalized_file(name)
            for name in names
            if not name.endswith('template.json') or with_mainfile or not is_multi
        )
    )
    target = sorted(
        name
        for name in example_file_contents
        if not name.endswith('template.json') or with_mainfile
    )
    assert source == target


def assert_example_entry(entry):
    assert entry is not None
    assert entry['data'] == example_entry['data']


class UploadFilesFixtures:
    @pytest.fixture(scope='function')
    def test_upload_id(self) -> Generator[str, None, None]:
        upload_id = f'test_upload_{uuid.uuid4().hex}'
        try:
            yield upload_id
        finally:
            for cls in [StagingUploadFiles, PublicUploadFiles]:
                DirectoryObject(cls.base_folder_for(upload_id)).delete()  # type: ignore


class UploadFilesContract(UploadFilesFixtures):
    @pytest.fixture(scope='function', params=['r'])
    def test_upload(self, request, test_upload_id) -> UploadWithFiles:
        raise NotImplementedError()

    @pytest.fixture(scope='function')
    def empty_test_upload(self, test_upload_id) -> UploadFiles:
        raise NotImplementedError()

    def test_create(self, empty_test_upload):
        assert (
            UploadFiles.get(empty_test_upload.upload_id).__class__
            == empty_test_upload.__class__
        )

    def test_os_path(self, test_upload: UploadWithFiles):
        upload_files = test_upload[2]
        assert upload_files.os_path is not None
        if upload_files.external_os_path:
            os_posix_path = pathlib.Path(upload_files.os_path).as_posix()
            assert upload_files.external_os_path.endswith(os_posix_path)

    def test_rawfile(self, test_upload: UploadWithFiles):
        _, entries, upload_files = test_upload
        for entry in entries:
            for file_path in entry.files:
                mode = 'rb' if file_path.endswith('.h5') else 'rt'
                with upload_files.raw_file(file_path, mode) as f:
                    assert len(f.read()) > 0
                if 't' in mode:
                    with upload_files.raw_file(file_path, mode, encoding='utf-8') as f:
                        content = f.read()
                        assert isinstance(content, str), (
                            'Content should be a string in text mode'
                        )
                        assert len(content) > 0, (
                            f'File {file_path} with utf-8 encoding should not be empty'
                        )

    def test_rawfile_size(self, test_upload: UploadWithFiles):
        _, entries, upload_files = test_upload
        for entry in entries:
            for file_path in entry.files:
                assert upload_files.raw_file_size(file_path) > 0

    def test_raw_directory_list_prefix(self, test_upload: UploadWithFiles):
        _, _, upload_files = test_upload
        path_infos = upload_files.raw_listdir(recursive=True, files_only=True)
        raw_files = list(path_info.path for path_info in path_infos)
        assert_example_files(raw_files)

    @pytest.mark.parametrize('path', ['', 'examples_template'])
    def test_raw_directory_list(self, test_upload: UploadWithFiles, path: str):
        upload_id, _, upload_files = test_upload
        # Add file to root to test corner case
        append_raw_files(upload_id, 'tests/data/proc/examples_template/1.aux', '1.aux')
        # Test recursive call (but do not verify result)
        upload_files.raw_listdir(path, files_only=False, recursive=True)
        # Test non-recursive call, verify result partially
        raw_files = list(upload_files.raw_listdir(path, files_only=True))
        if not path:
            assert len(raw_files) == 1
            assert raw_files[0].size == 8
        else:
            assert '1.aux' in list(
                os.path.basename(path_info.path) for path_info in raw_files
            )
            for path_info in raw_files:
                if path_info.path.endswith('.aux'):
                    assert path_info.size == 8
                else:
                    assert path_info.size > 0
            assert_example_files([path_info.path for path_info in raw_files])

    @pytest.mark.parametrize('with_access', [False, True])
    def test_read_archive(self, test_upload: UploadWithFiles, with_access: str):
        _, _, upload_files = test_upload

        with upload_files.read_archive(example_entry_id) as archive:
            assert to_json(archive[example_entry_id]) == example_archive_contents

    def test_archive_hdf5_file(self, test_upload: UploadWithFiles):
        _, _, upload_files = test_upload
        with FSUtility.open(upload_files.archive_hdf5_location(example_entry_id)) as f:
            assert len(f.read()) > 0


def create_staging_upload(
    upload_id: str, entry_specs: str, embargo_length: int = 0
) -> StagingUploadWithFiles:
    """
    Create an upload according to given spec. Additional arguments are given to
    the StagingUploadFiles contstructor.

    Arguments:
        upload_id: The id that should be given to this test upload.
        entry_specs: A string that determines the properties of the given upload.
            With letters determining example entries being public `p` or restricted `r`.
            The entries will be copies of entries in `example_file`.
            First entry is at top level, following entries will be put under 1/, 2/, etc.
            All entries with capital `P`/`R` will be put in the same directory under multi/.
    """

    upload_files = StagingUploadFiles(upload_id, create=True)
    entries = []

    prefix = 0
    for entry_spec in entry_specs:
        is_multi = entry_spec in ['R', 'P']
        entry_spec = entry_spec.lower()
        assert (entry_spec == 'r') == (embargo_length > 0)
        if is_multi or prefix == 0:
            directory = 'examples_template'
        else:
            directory = os.path.join(str(prefix), 'examples_template')

        entry, entry_file = generate_example_entry(
            prefix,
            with_mainfile_prefix=is_multi,
            subdirectory=directory,
            with_embargo=embargo_length > 0,
        )

        upload_files.add_rawfiles(entry_file)
        upload_files.write_archive(entry.entry_id, example_archive_contents)
        with FSUtility.open_h5(
            upload_files.archive_hdf5_location(entry.entry_id), 'w'
        ) as f:
            f.create_dataset('value', data=1.0)

        entries.append(entry)
        prefix += 1

    assert len(entries) == len(entry_specs)
    return upload_id, entries, upload_files


class TestStagingUploadFiles(UploadFilesContract):
    @pytest.fixture(scope='function', params=['r', 'rr', 'p', 'pp', 'RR', 'PP'])
    def test_upload(self, request, test_upload_id: str) -> StagingUploadWithFiles:
        embargo_length = 12 if 'r' in request.param.lower() else 0
        return create_staging_upload(
            test_upload_id, entry_specs=request.param, embargo_length=embargo_length
        )

    @pytest.fixture(scope='function')
    def empty_test_upload(self, test_upload_id) -> UploadFiles:
        return StagingUploadFiles(test_upload_id, create=True)

    @pytest.mark.parametrize('target_dir', ['', 'subdir'])
    def test_add_rawfiles_zip(self, test_upload_id, target_dir):
        test_upload = StagingUploadFiles(test_upload_id, create=True)
        test_upload.add_rawfiles(example_file, target_dir=target_dir)
        for filepath in example_file_contents:
            filepath = os.path.join(target_dir, filepath) if target_dir else filepath
            with test_upload.raw_file(filepath) as f:
                content = f.read()
                if filepath == example_mainfile_raw_path:
                    assert len(content) > 0

    @pytest.mark.parametrize('target_dir', ['', 'subdir'])
    def test_add_rawfiles_zip_no_decompression(self, test_upload_id, target_dir):
        test_upload = StagingUploadFiles(test_upload_id, create=True)
        test_upload.add_rawfiles(
            example_file, target_dir=target_dir, auto_decompress=False
        )
        filepath = os.path.join(
            test_upload.external_os_path, 'raw', target_dir, 'examples_template.zip'
        )
        assert os.path.isfile(filepath)

    @pytest.fixture(scope='function')
    def example_tar_gz_file(self, tmp_path):
        import tarfile

        tar_path = tmp_path / 'examples_template.tar.gz'
        with tarfile.open(tar_path, 'w:gz') as tar:
            for filepath in example_file_contents:
                local_path = os.path.join(
                    example_directory, filepath.removeprefix('examples_template/')
                )
                tar.add(local_path, arcname=filepath)
        return str(tar_path)

    @pytest.mark.parametrize('target_dir', ['', 'subdir'])
    def test_add_rawfiles_tar(self, test_upload_id, target_dir, example_tar_gz_file):
        test_upload = StagingUploadFiles(test_upload_id, create=True)
        test_upload.add_rawfiles(example_tar_gz_file, target_dir=target_dir)
        for filepath in example_file_contents:
            filepath = os.path.join(target_dir, filepath) if target_dir else filepath
            with test_upload.raw_file(filepath) as f:
                content = f.read()
                if filepath == example_mainfile_raw_path:
                    assert len(content) > 0

    def test_pack(self, test_upload: StagingUploadWithFiles):
        _, entries, upload_files = test_upload
        upload_files.pack(entries, with_embargo=entries[0].with_embargo)

    @pytest.mark.parametrize('entry_specs', ['r', 'p'])
    def test_pack_potcar(self, entry_specs):
        embargo_length = 12 if 'r' in entry_specs.lower() else 0
        upload_id, entries, upload_files = create_staging_upload(
            'test_potcar', entry_specs=entry_specs, embargo_length=embargo_length
        )
        # Add potcar files: one stripped and one unstripped
        filenames = ('POTCAR', 'POTCAR.stripped')
        for filename in filenames:
            with open(
                os.path.join(
                    upload_files.os_path, 'raw', 'examples_template', filename
                ),
                'w',
            ) as f:
                f.write('some content')
        upload_files.pack(entries, with_embargo=embargo_length > 0)
        upload_files.delete()
        upload_files = PublicUploadFiles(upload_id)
        for filename in filenames:
            try:
                with upload_files.raw_file('examples_template/' + filename) as pf:
                    pf.read()
                assert filename.endswith('.stripped'), (
                    'Non-stripped POTCAR file could be read'
                )
            except KeyError:
                assert not filename.endswith('.stripped'), (
                    'Only non-stripped file should be removed'
                )

    @pytest.mark.parametrize('with_mainfile', [True, False])
    def test_entry_files(self, test_upload: StagingUploadWithFiles, with_mainfile):
        _, entries, upload_files = test_upload
        for entry in entries:
            mainfile = entry.mainfile
            entry_files = upload_files.entry_files(
                mainfile, with_mainfile=with_mainfile
            )
            assert_example_files(entry_files, with_mainfile=with_mainfile)

    def test_delete(self, test_upload: StagingUploadWithFiles):
        _, _, upload_files = test_upload
        upload_files.delete()
        assert not upload_files.exists()

    def test_add_rawfiles(self, test_upload_id):
        test_upload = StagingUploadFiles(test_upload_id, create=True)
        assert test_upload.is_empty()
        test_upload.add_rawfiles(example_file)
        path_infos = test_upload.raw_listdir(recursive=True, files_only=True)
        assert sorted(list(path_info.path for path_info in path_infos)) == sorted(
            example_file_contents
        )

    def test_raw_listdir_page(self, test_upload_id):
        test_upload = StagingUploadFiles(test_upload_id, create=True)
        test_upload.add_rawfiles(example_file)

        all_items = list(
            test_upload.raw_listdir(
                'examples_template', recursive=True, files_only=True
            )
        )
        page = test_upload.raw_listdir_page(
            'examples_template',
            start=1,
            end=3,
            recursive=True,
            files_only=True,
            order='desc',
        )

        expected_paths = [
            path_info.path
            for path_info in sorted(
                all_items, key=lambda item: item.path, reverse=True
            )[1:3]
        ]

        assert page.total == len(all_items)
        assert [path_info.path for path_info in page.content] == expected_paths

    @pytest.mark.parametrize('prefix_size', [0, 2])
    def test_prefix_size(self, monkeypatch, prefix_size):
        monkeypatch.setattr('nomad.config.fs.prefix_size', prefix_size)
        upload_id = 'test_upload'
        upload_files = StagingUploadFiles(upload_id, create=True)
        if not prefix_size:
            assert upload_files.os_path == os.path.join(config.fs.staging, upload_id)
        else:
            prefix = upload_id[:prefix_size]
            assert upload_files.os_path == os.path.join(
                config.fs.staging, prefix, upload_id
            )
        upload_files.delete()

    def test_delete_prefix(self, monkeypatch):
        monkeypatch.setattr('nomad.config.fs.prefix_size', 2)
        upload_1 = StagingUploadFiles('test_upload_1', create=True)
        upload_2 = StagingUploadFiles('test_upload_2', create=True)
        upload_1.delete()
        upload_2.delete()

        prefix = os.path.dirname(upload_1.os_path)
        assert len(os.path.basename(prefix)) == 2
        assert not os.path.exists(prefix)


def create_public_upload(
    upload_id: str, entry_specs: str, embargo_length: int = 0, with_upload: bool = True
) -> PublicUploadWithFiles:
    _, entries, upload_files = create_staging_upload(
        upload_id, entry_specs, embargo_length
    )

    upload_files.pack(entries, with_embargo=embargo_length > 0)
    upload_files.delete()
    if with_upload:
        upload = Upload.get(upload_id)
        upload.publish_time = datetime.utcnow()
        assert upload.embargo_length == embargo_length, 'Wrong embargo_length provided'
        upload.save()
    return upload_id, entries, PublicUploadFiles(upload_id)


class RecordingRemoteFS:
    """Proxy that logs selected fsspec calls and is not a LocalFileSystem."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[tuple[str, str, dict]] = []

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _record(self, method: str, path: str, kwargs: dict) -> None:
        self.calls.append((method, path, dict(kwargs)))

    def info(self, path, **kwargs):
        self._record('info', path, kwargs)
        info = dict(self._inner.info(path, **kwargs))
        try:
            data = self._inner.cat_file(path)
            info['ETag'] = f'"{hashlib.md5(data).hexdigest()}"'
        except Exception:
            pass
        return info

    def exists(self, path, **kwargs):
        self._record('exists', path, kwargs)
        return self._inner.exists(path, **kwargs)

    def size(self, path, **kwargs):
        self._record('size', path, kwargs)
        return self._inner.size(path)

    def isfile(self, path, **kwargs):
        self._record('isfile', path, kwargs)
        return self._inner.isfile(path, **kwargs)

    def isdir(self, path, **kwargs):
        self._record('isdir', path, kwargs)
        return self._inner.isdir(path, **kwargs)

    def ls(self, path, **kwargs):
        self._record('ls', path, kwargs)
        return self._inner.ls(path, **kwargs)

    def open(self, path, mode='rb', **kwargs):
        recorded = dict(kwargs)
        recorded['mode'] = mode
        self._record('open', path, recorded)
        return self._inner.open(path, mode, **kwargs)

    def cat_file(self, path, **kwargs):
        self._record('cat_file', path, kwargs)
        return self._inner.cat_file(path, **kwargs)

    def read_block(self, path, offset, length, **kwargs):
        recorded = dict(kwargs)
        recorded['offset'] = offset
        recorded['length'] = length
        self._record('read_block', path, recorded)
        return self._inner.read_block(path, offset, length, **kwargs)

    def rm(self, path, **kwargs):
        self._record('rm', path, kwargs)
        return self._inner.rm(path, **kwargs)

    def mv(self, path1, path2, **kwargs):
        recorded = dict(kwargs)
        recorded['dest'] = path2
        self._record('mv', path1, recorded)
        return self._inner.mv(path1, path2, **kwargs)


def _published_msg_calls(
    calls: list[tuple[str, str, dict]], access: str
) -> list[tuple[str, str, dict]]:
    needle = f'archive-{access}-'
    return [
        call for call in calls if needle in call[1] and call[1].endswith('.msg.msg')
    ]


def _published_zip_calls(
    calls: list[tuple[str, str, dict]], access: str
) -> list[tuple[str, str, dict]]:
    needle = f'raw-{access}.plain.zip'
    return [call for call in calls if call[1].endswith(needle) or needle in call[1]]


def _copy_upload_tree_to_memory_fs(
    upload_files: PublicUploadFiles, memory_fs: MemoryFileSystem
) -> None:
    for dirpath, _dirnames, filenames in os.walk(upload_files.os_path):
        for name in filenames:
            local_path = os.path.join(dirpath, name)
            dest = FSUtility.remote_path(local_path)
            parent, _, _ = dest.rpartition('/')
            if parent:
                memory_fs.makedirs(parent, exist_ok=True)
            memory_fs.put_file(os.path.abspath(local_path), dest)


def _remote_marker_location(upload_os_path: str) -> str:
    return FSUtility.remote_path(os.path.join(upload_os_path, MARKER_FILENAME))


class TestPublicUploadFiles(UploadFilesContract):
    @pytest.fixture(autouse=True)
    def _clear_index_memory_caches(self):
        clear_index_caches()
        yield
        clear_index_caches()

    def _setup_recording_archive_fs(
        self,
        monkeypatch,
        tmp_path,
        test_upload_id,
        entry_specs='pp',
        read_mode='remote_only',
    ):
        monkeypatch.setattr(config.fs.public_fs, 'protocol', None)
        _, entries, upload_files = create_public_upload(
            test_upload_id, entry_specs=entry_specs, with_upload=False
        )
        memory_fs = MemoryFileSystem()
        _copy_upload_tree_to_memory_fs(upload_files, memory_fs)
        proxy = RecordingRemoteFS(memory_fs)
        monkeypatch.setattr(config.fs.public_fs, 'protocol', 's3')
        monkeypatch.setattr(config.fs.public_fs, 'read_mode', read_mode)
        monkeypatch.setattr(
            type(config.fs.public_fs), 'target_fs', property(lambda _: proxy)
        )
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        write_ready_marker(upload_files.os_path, test_upload_id, 'public')
        return proxy, entries, upload_files

    def _setup_memory_public_fs(
        self,
        monkeypatch,
        *,
        read_mode='remote_then_local',
        write_mode='local_then_remote',
    ):
        remote_fs = MemoryFileSystem()
        monkeypatch.setattr(config.fs.public_fs, 'protocol', 's3')
        monkeypatch.setattr(config.fs.public_fs, 'read_mode', read_mode)
        monkeypatch.setattr(config.fs.public_fs, 'write_mode', write_mode)
        monkeypatch.setattr(
            type(config.fs.public_fs), 'target_fs', property(lambda _: remote_fs)
        )
        return remote_fs

    @staticmethod
    def _create_remote_delete_copy(monkeypatch, upload_files):
        """Configure a unique in-memory remote copy for deletion tests."""
        remote_fs = MemoryFileSystem()
        monkeypatch.setattr(config.fs.public_fs, 'protocol', 's3')
        monkeypatch.setattr(
            config.fs.public_fs, 'bucket', f'public-delete-{uuid.uuid4().hex}'
        )
        monkeypatch.setattr(
            type(config.fs.public_fs), 'target_fs', property(lambda _: remote_fs)
        )

        remote_path = FSUtility.remote_path(upload_files.os_path)
        remote_fs.makedirs(remote_path, exist_ok=True)
        remote_fs.pipe(f'{remote_path}/remote-marker', b'remote')
        return remote_fs, remote_path

    @staticmethod
    def _create_local_delete_copy(upload_files):
        with upload_files._fs.open(
            upload_files.join_file('local-marker').location, 'wb'
        ) as file:
            file.write(b'local')

    @staticmethod
    def _calls_for_artifacts(
        calls: list[tuple[str, str, dict]],
    ) -> list[tuple[str, str, dict]]:
        return [call for call in calls if MARKER_FILENAME not in call[1]]

    def test_delete_removes_remote_and_local_copies(self, monkeypatch, test_upload_id):
        upload_files = PublicUploadFiles(test_upload_id, create=True)
        self._create_local_delete_copy(upload_files)
        remote_fs, remote_path = self._create_remote_delete_copy(
            monkeypatch, upload_files
        )
        monkeypatch.setattr(config.fs.public_fs, 'read_mode', 'remote_then_local')

        upload_files.delete()

        assert not upload_files.exists()
        assert not remote_fs.exists(remote_path)

    def test_delete_remote_failure_still_removes_local_copy(
        self, monkeypatch, test_upload_id
    ):
        upload_files = PublicUploadFiles(test_upload_id, create=True)
        self._create_local_delete_copy(upload_files)
        remote_fs, remote_path = self._create_remote_delete_copy(
            monkeypatch, upload_files
        )
        original_rm = remote_fs.rm

        def fail_remote_delete(*args, **kwargs):
            raise OSError('remote delete failed')

        monkeypatch.setattr(remote_fs, 'rm', fail_remote_delete)

        with pytest.raises(OSError, match='remote delete failed'):
            upload_files.delete()

        assert not upload_files.exists()
        assert remote_fs.exists(remote_path)
        original_rm(remote_path, recursive=True)

    def test_delete_local_failure_still_removes_remote_copy(
        self, monkeypatch, test_upload_id
    ):
        upload_files = PublicUploadFiles(test_upload_id, create=True)
        self._create_local_delete_copy(upload_files)
        remote_fs, remote_path = self._create_remote_delete_copy(
            monkeypatch, upload_files
        )
        original_rm = upload_files._fs.rm

        def fail_local_delete(*args, **kwargs):
            raise OSError('local delete failed')

        monkeypatch.setattr(upload_files._fs, 'rm', fail_local_delete)

        with pytest.raises(OSError, match='local delete failed'):
            upload_files.delete()

        assert upload_files.exists()
        assert not remote_fs.exists(remote_path)
        monkeypatch.setattr(upload_files._fs, 'rm', original_rm)

    def test_delete_is_idempotent_when_copies_are_missing(
        self, monkeypatch, test_upload_id
    ):
        upload_files = PublicUploadFiles(test_upload_id, create=True)
        remote_fs, remote_path = self._create_remote_delete_copy(
            monkeypatch, upload_files
        )

        upload_files.delete()
        upload_files.delete()

        assert not upload_files.exists()
        assert not remote_fs.exists(remote_path)

    def test_remote_then_local_reads_a_complete_local_backend(
        self, monkeypatch, test_upload_id
    ):
        """A migration fallback selects local once, rather than mixing artifacts."""
        # The fallback scenario requires an existing local copy. Make its setup
        # independent of whether the test suite itself uses S3 storage.
        monkeypatch.setattr(config.fs.public_fs, 'protocol', None)
        _, entries, local_upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        local_raw = local_upload_files.raw_zip_file_object().os_path

        remote_fs = MemoryFileSystem()
        monkeypatch.setattr(config.fs.public_fs, 'protocol', 's3')
        monkeypatch.setattr(config.fs.public_fs, 'read_mode', 'remote_then_local')
        monkeypatch.setattr(
            type(config.fs.public_fs), 'target_fs', property(lambda _: remote_fs)
        )

        fallback_upload_files = PublicUploadFiles(test_upload_id)
        assert fallback_upload_files.storage_fs is not remote_fs
        with fallback_upload_files.raw_file(entries[0].mainfile) as file_obj:
            assert file_obj.read()
        with fallback_upload_files.read_archive(entries[0].entry_id) as archive:
            assert entries[0].entry_id in archive

        remote_raw = FSUtility.remote_path(local_raw)
        remote_fs.makedirs(os.path.dirname(remote_raw), exist_ok=True)
        remote_fs.put_file(local_raw, remote_raw)

        partial_upload_files = PublicUploadFiles(test_upload_id)
        assert partial_upload_files.storage_fs is not remote_fs

        copy_to_remote(local_upload_files.os_path, test_upload_id, 'public')

        remote_upload_files = PublicUploadFiles(test_upload_id)
        assert remote_upload_files.storage_fs is remote_fs
        with remote_upload_files.raw_file(entries[0].mainfile) as file_obj:
            assert file_obj.read()

    def test_remote_then_local_disk_cache_skips_marker_and_artifact_requests(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch,
            tmp_path,
            test_upload_id,
            entry_specs='p',
            read_mode='remote_then_local',
        )
        first = PublicUploadFiles(test_upload_id)
        proxy.calls.clear()
        assert first.storage_fs is proxy
        first.close()

        proxy.calls.clear()
        second = PublicUploadFiles(test_upload_id)
        assert second.storage_fs is proxy
        assert proxy.calls == []
        second.close()

    def test_remote_then_local_does_not_cache_negative_artifact_probe(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config.fs.public_fs, 'protocol', None)
        create_public_upload(test_upload_id, entry_specs='p', with_upload=False)
        proxy = RecordingRemoteFS(MemoryFileSystem())
        monkeypatch.setattr(config.fs.public_fs, 'protocol', 's3')
        monkeypatch.setattr(config.fs.public_fs, 'read_mode', 'remote_then_local')
        monkeypatch.setattr(
            type(config.fs.public_fs), 'target_fs', property(lambda _: proxy)
        )
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )

        first = PublicUploadFiles(test_upload_id)
        assert isinstance(first.storage_fs, LocalFileSystem)
        assert proxy.calls
        first_probe = list(proxy.calls)
        first.close()

        proxy.calls.clear()
        second = PublicUploadFiles(test_upload_id)
        assert isinstance(second.storage_fs, LocalFileSystem)
        assert proxy.calls == first_probe
        second.close()

    def test_remote_then_local_repack_invalidates_artifact_probe(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch,
            tmp_path,
            test_upload_id,
            entry_specs='p',
            read_mode='remote_then_local',
        )
        packed = PublicUploadFiles(test_upload_id)
        assert packed.storage_fs is proxy
        packed.re_pack(with_embargo=True)
        packed.close()

        proxy.calls.clear()
        after = PublicUploadFiles(test_upload_id)
        assert after.storage_fs is proxy
        assert proxy.calls
        after.close()

    def test_remote_then_local_artifact_probe_error_is_not_cached(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch,
            tmp_path,
            test_upload_id,
            entry_specs='p',
            read_mode='remote_then_local',
        )
        original_load = RemoteReadyMarker.load
        attempts = {'n': 0}

        def fail_once(cls, upload_os_path, fs):
            attempts['n'] += 1
            if attempts['n'] == 1:
                raise OSError('remote probe failed')
            return original_load(upload_os_path, fs)

        monkeypatch.setattr(RemoteReadyMarker, 'load', classmethod(fail_once))

        with pytest.raises(OSError, match='remote probe failed'):
            PublicUploadFiles(test_upload_id).storage_fs
        assert attempts['n'] == 1

        recovered = PublicUploadFiles(test_upload_id)
        assert recovered.storage_fs is proxy
        assert attempts['n'] == 2
        recovered.close()

        proxy.calls.clear()
        cached = PublicUploadFiles(test_upload_id)
        assert cached.storage_fs is proxy
        assert self._calls_for_artifacts(proxy.calls) == []
        assert attempts['n'] == 2
        cached.close()

    def test_local_then_remote_pack_copies_artifacts_and_writes_marker(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        staging.pack(entries, with_embargo=False)
        staging.delete()

        public = PublicUploadFiles(test_upload_id)
        local_fs = LocalFileSystem()
        local_dir = DirectoryObject(public.os_path, fs=local_fs)
        zip_file = local_dir.zip_fp('public', fs=local_fs)
        msg_file = local_dir.msg_fp('public', fs=local_fs)
        assert zip_file.exists()
        assert msg_file.exists()
        assert remote_fs.exists(FSUtility.remote_path(zip_file.os_path))
        assert remote_fs.exists(FSUtility.remote_path(msg_file.os_path))
        assert remote_fs.exists(_remote_marker_location(public.os_path))
        assert os.path.isfile(os.path.join(public.os_path, MARKER_FILENAME))
        marker = RemoteReadyMarker.load(public.os_path, remote_fs)
        assert marker is not None
        assert marker.matches_remote(remote_fs, public.os_path)
        assert public.storage_fs is remote_fs

    def test_remote_only_pack_does_not_write_local_marker(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch, write_mode='remote_only')
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        staging.pack(entries, with_embargo=False)
        staging.delete()

        public = PublicUploadFiles(test_upload_id)
        assert remote_fs.exists(_remote_marker_location(public.os_path))
        assert not os.path.exists(os.path.join(public.os_path, MARKER_FILENAME))

    def test_remote_marker_access_is_reused_after_storage_selection(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, read_mode='remote_then_local'
        )
        original_load = RemoteReadyMarker.load
        loads = {'count': 0}

        def count_load(cls, upload_os_path, fs):
            loads['count'] += 1
            return original_load(upload_os_path, fs)

        monkeypatch.setattr(RemoteReadyMarker, 'load', classmethod(count_load))
        public = PublicUploadFiles(test_upload_id)
        assert public.storage_fs is proxy
        proxy.calls.clear()
        assert public.access == 'public'
        assert loads['count'] == 1
        assert proxy.calls == []

    def test_remote_only_loads_marker_once_when_access_is_requested(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, read_mode='remote_only'
        )
        original_load = RemoteReadyMarker.load
        loads = {'count': 0}

        def count_load(cls, upload_os_path, fs):
            loads['count'] += 1
            return original_load(upload_os_path, fs)

        monkeypatch.setattr(RemoteReadyMarker, 'load', classmethod(count_load))
        public = PublicUploadFiles(test_upload_id)
        assert public.access == 'public'
        assert public.access == 'public'
        assert public.storage_fs is proxy
        assert loads['count'] == 1

    def test_remote_then_local_does_not_reload_a_missing_marker_for_access(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, read_mode='remote_then_local'
        )
        proxy.rm(_remote_marker_location(upload_files.os_path))
        for name in os.listdir(upload_files.os_path):
            path = os.path.join(upload_files.os_path, name)
            if os.path.isfile(path):
                os.remove(path)
        original_load = RemoteReadyMarker.load
        loads = {'count': 0}

        def count_load(cls, upload_os_path, fs):
            loads['count'] += 1
            return original_load(upload_os_path, fs)

        monkeypatch.setattr(RemoteReadyMarker, 'load', classmethod(count_load))
        public = PublicUploadFiles(test_upload_id)
        assert public.storage_fs is proxy
        assert public.access == 'public'
        assert loads['count'] == 1

    def test_remote_then_local_stale_ready_cache_rechecks_marker(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)
        _, _entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        staging.pack(_entries, with_embargo=False)
        staging.delete()

        clock = {'value': datetime.fromtimestamp(1_000_000)}
        monkeypatch.setattr(public_storage, 'now', lambda: clock['value'])

        warm = PublicUploadFiles(test_upload_id)
        assert warm.storage_fs is remote_fs
        warm.close()

        remote_fs.rm(_remote_marker_location(warm.os_path))
        clock['value'] = datetime.fromtimestamp(1_000_061)

        after = PublicUploadFiles(test_upload_id)
        assert isinstance(after.storage_fs, LocalFileSystem)

    def test_remote_marker_revalidate_zero_disables_disk_cache(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, read_mode='remote_then_local'
        )
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'revalidate_seconds', 0)

        assert PublicUploadFiles(test_upload_id).storage_fs is proxy
        cache_path = public_storage._marker_cache_path(upload_files.os_path)
        assert cache_path is not None
        assert not os.path.exists(cache_path)

        proxy.calls.clear()
        assert PublicUploadFiles(test_upload_id).storage_fs is proxy
        assert any(MARKER_FILENAME in call[1] for call in proxy.calls)
        assert not os.path.exists(cache_path)

    def test_remote_marker_disk_cache_expires_without_extending_deadline(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, read_mode='remote_then_local'
        )
        clock = {'value': datetime.fromtimestamp(1_000_000)}
        monkeypatch.setattr(public_storage, 'now', lambda: clock['value'])

        assert PublicUploadFiles(test_upload_id).storage_fs is proxy
        proxy.calls.clear()
        clock['value'] = datetime.fromtimestamp(1_000_030)
        assert PublicUploadFiles(test_upload_id).storage_fs is proxy
        assert proxy.calls == []

        clock['value'] = datetime.fromtimestamp(1_000_061)
        assert PublicUploadFiles(test_upload_id).storage_fs is proxy
        assert any(MARKER_FILENAME in call[1] for call in proxy.calls)
        assert upload_files.os_path

    def test_corrupt_remote_marker_disk_cache_refetches(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, read_mode='remote_then_local'
        )
        assert PublicUploadFiles(test_upload_id).storage_fs is proxy
        cache_path = public_storage._marker_cache_path(upload_files.os_path)
        assert cache_path is not None
        pathlib.Path(cache_path).write_bytes(b'not json')

        proxy.calls.clear()
        assert PublicUploadFiles(test_upload_id).storage_fs is proxy
        assert any(MARKER_FILENAME in call[1] for call in proxy.calls)

    def test_remote_only_cache_does_not_bypass_remote_then_local_validation(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, read_mode='remote_only'
        )
        assert PublicUploadFiles(test_upload_id).access == 'public'

        monkeypatch.setattr(config.fs.public_fs, 'read_mode', 'remote_then_local')
        proxy.calls.clear()
        assert PublicUploadFiles(test_upload_id).storage_fs is proxy
        assert self._calls_for_artifacts(proxy.calls)

    def test_rejected_marker_with_empty_local_prefix_stays_local(
        self, monkeypatch, test_upload_id, tmp_path
    ):
        proxy, _entries, upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, read_mode='remote_then_local'
        )
        marker = RemoteReadyMarker.load(upload_files.os_path, proxy)
        assert marker is not None
        RemoteReadyMarker(
            schema_version=marker.schema_version,
            upload_id=marker.upload_id,
            access=marker.access,
            created_at=marker.created_at,
            artifacts=tuple(
                ArtifactRecord(name=item.name, size=item.size, etag='wrong')
                for item in marker.artifacts
            ),
        ).save(upload_files.os_path, proxy)
        for name in os.listdir(upload_files.os_path):
            path = os.path.join(upload_files.os_path, name)
            if os.path.isfile(path):
                os.remove(path)

        public = PublicUploadFiles(test_upload_id)
        assert isinstance(public.storage_fs, LocalFileSystem)

    def test_local_then_remote_copy_writes_marker_for_zip_only_publish(
        self, monkeypatch, test_upload_id
    ):
        monkeypatch.setattr(config.fs.public_fs, 'protocol', None)
        _, _entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        local_dir = DirectoryObject(upload_files.os_path, fs=LocalFileSystem())
        local_dir.msg_fp('public').delete()
        if local_dir.h5_fp('public').exists():
            local_dir.h5_fp('public').delete()

        remote_fs = self._setup_memory_public_fs(monkeypatch)
        copy_to_remote(upload_files.os_path, test_upload_id, 'public')

        marker = RemoteReadyMarker.load(upload_files.os_path, remote_fs)
        assert marker is not None
        names = {item.name for item in marker.artifacts}
        assert any(
            name.startswith('raw-') and name.endswith('.plain.zip') for name in names
        )
        assert not any(name.endswith('.msg.msg') for name in names)
        zip_path = local_dir.zip_fp('public').os_path
        assert remote_fs.exists(FSUtility.remote_path(zip_path))

    def test_published_access_uses_local_write_destination_when_remote_empty(
        self, monkeypatch, test_upload_id
    ):
        monkeypatch.setattr(config.fs.public_fs, 'protocol', None)
        _, _entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        remote_fs = self._setup_memory_public_fs(
            monkeypatch, read_mode='remote_only', write_mode='local_then_remote'
        )

        public = PublicUploadFiles(test_upload_id)
        assert public.access == 'public'
        access = detect_published_access(public.os_path, choose_pack_fs())
        assert access == 'public'
        complete_published_write(public.os_path, test_upload_id, access)

        zip_path = DirectoryObject(public.os_path).zip_fp('public').os_path
        assert remote_fs.exists(FSUtility.remote_path(zip_path))
        marker = RemoteReadyMarker.load(public.os_path, remote_fs)
        assert marker is not None

    def test_local_then_remote_hydrates_legacy_remote_artifacts(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        staging.pack(entries, with_embargo=False)
        staging.delete()

        public = PublicUploadFiles(test_upload_id)
        local_dir = DirectoryObject(public.os_path, fs=LocalFileSystem())
        local_dir.zip_fp('public').delete()
        local_dir.msg_fp('public').delete()
        if local_dir.h5_fp('public').exists():
            local_dir.h5_fp('public').delete()
        assert not local_dir.zip_fp('public').exists()
        assert not local_dir.msg_fp('public').exists()

        complete_published_write(public.os_path, test_upload_id, 'public')

        assert local_dir.zip_fp('public').exists()
        assert local_dir.msg_fp('public').exists()
        marker = RemoteReadyMarker.load(public.os_path, remote_fs)
        assert marker is not None
        assert marker.matches_remote(remote_fs, public.os_path)

    def test_local_then_remote_hydrate_does_not_overwrite_local_archive(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        staging.pack(entries, with_embargo=False)
        staging.delete()

        public = PublicUploadFiles(test_upload_id)
        local_dir = DirectoryObject(public.os_path, fs=LocalFileSystem())
        local_dir.zip_fp('public').delete()
        msg = local_dir.msg_fp('public', fs=LocalFileSystem())
        new_payload = b'n' * (empty_archive_file_size + 50)
        with open(msg.os_path, 'wb') as file_obj:
            file_obj.write(new_payload)

        complete_published_write(public.os_path, test_upload_id, 'public')

        with open(msg.os_path, 'rb') as file_obj:
            assert file_obj.read() == new_payload
        assert local_dir.zip_fp('public').exists()
        remote_msg = DirectoryObject(public.os_path, fs=remote_fs).msg_fp(
            'public', fs=remote_fs
        )
        with remote_fs.open(remote_msg.location, 'rb') as file_obj:
            assert file_obj.read() == new_payload

    def test_hydrate_interrupted_download_is_not_authoritative_on_retry(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        staging.pack(entries, with_embargo=False)
        staging.delete()

        public = PublicUploadFiles(test_upload_id)
        local_dir = DirectoryObject(public.os_path, fs=LocalFileSystem())
        zip_file = local_dir.zip_fp('public')
        remote_zip = FSUtility.remote_path(zip_file.os_path)
        original_remote = remote_fs.cat_file(remote_zip)
        assert len(original_remote) > 100
        zip_file.delete()
        local_dir.msg_fp('public').delete()
        if local_dir.h5_fp('public').exists():
            local_dir.h5_fp('public').delete()

        original_copy = shutil.copyfileobj

        def interrupt_first_download(src, dst, *args, **kwargs):
            dst.write(src.read(100))
            raise OSError('download interrupted')

        monkeypatch.setattr(
            'nomad.files.public_storage.shutil.copyfileobj', interrupt_first_download
        )
        with pytest.raises(OSError, match='download interrupted'):
            complete_published_write(public.os_path, test_upload_id, 'public')

        assert not zip_file.exists()
        assert not os.path.exists(f'{zip_file.os_path}.part')
        assert remote_fs.cat_file(remote_zip) == original_remote
        assert RemoteReadyMarker.load(public.os_path, remote_fs) is not None

        monkeypatch.setattr(
            'nomad.files.public_storage.shutil.copyfileobj', original_copy
        )
        complete_published_write(public.os_path, test_upload_id, 'public')

        with open(zip_file.os_path, 'rb') as file_obj:
            assert file_obj.read() == original_remote
        assert remote_fs.cat_file(remote_zip) == original_remote
        marker = RemoteReadyMarker.load(public.os_path, remote_fs)
        assert marker is not None
        assert marker.matches_remote(remote_fs, public.os_path)

    def test_local_then_remote_repack_hydrates_legacy_remote_only_upload(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        staging.pack(entries, with_embargo=False)
        staging.delete()

        public = PublicUploadFiles(test_upload_id)
        local_dir = DirectoryObject(public.os_path, fs=LocalFileSystem())
        local_dir.zip_fp('public').delete()
        local_dir.msg_fp('public').delete()
        if local_dir.h5_fp('public').exists():
            local_dir.h5_fp('public').delete()

        public.re_pack(with_embargo=True)

        remote_dir = DirectoryObject(public.os_path, fs=remote_fs)
        assert local_dir.zip_fp('restricted').exists()
        assert local_dir.msg_fp('restricted').exists()
        assert remote_dir.zip_fp('restricted').exists()
        assert not remote_dir.zip_fp('public').exists()
        marker = RemoteReadyMarker.load(public.os_path, remote_fs)
        assert marker is not None
        assert marker.access == 'restricted'

    def test_local_then_remote_mirror_failure_leaves_no_marker(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)

        def fail_put(*args, **kwargs):
            raise OSError('remote put failed')

        monkeypatch.setattr(remote_fs, 'put_file', fail_put)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        with pytest.raises(OSError, match='remote put failed'):
            staging.pack(entries, with_embargo=False)

        public = PublicUploadFiles(test_upload_id)
        assert not remote_fs.exists(_remote_marker_location(public.os_path))
        assert public.storage_fs is not remote_fs
        assert isinstance(public.storage_fs, LocalFileSystem)

    def test_local_then_remote_size_mismatch_leaves_no_marker(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)
        original_info = remote_fs.info

        def mismatched_info(path, **kwargs):
            info = dict(original_info(path, **kwargs))
            if str(path).endswith('.plain.zip'):
                info['size'] = int(info['size']) + 1
            return info

        monkeypatch.setattr(remote_fs, 'info', mismatched_info)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        with pytest.raises(RuntimeError, match='remote size mismatch'):
            staging.pack(entries, with_embargo=False)

        public = PublicUploadFiles(test_upload_id)
        assert not remote_fs.exists(_remote_marker_location(public.os_path))
        assert isinstance(public.storage_fs, LocalFileSystem)

    def test_remote_then_local_no_marker_with_local_artifacts_stays_local(
        self, monkeypatch, test_upload_id
    ):
        monkeypatch.setattr(config.fs.public_fs, 'protocol', None)
        _, _entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        remote_fs = MemoryFileSystem()
        zip_path = DirectoryObject(upload_files.os_path).zip_fp('public').os_path
        remote_zip = FSUtility.remote_path(zip_path)
        remote_fs.makedirs(os.path.dirname(remote_zip), exist_ok=True)
        remote_fs.put_file(os.path.abspath(zip_path), remote_zip)
        monkeypatch.setattr(config.fs.public_fs, 'protocol', 's3')
        monkeypatch.setattr(config.fs.public_fs, 'read_mode', 'remote_then_local')
        monkeypatch.setattr(
            type(config.fs.public_fs), 'target_fs', property(lambda _: remote_fs)
        )

        public = PublicUploadFiles(test_upload_id)
        assert isinstance(public.storage_fs, LocalFileSystem)

    def test_remote_then_local_legacy_remote_when_local_is_empty(
        self, monkeypatch, test_upload_id
    ):
        monkeypatch.setattr(config.fs.public_fs, 'protocol', None)
        _, _entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        remote_fs = MemoryFileSystem()
        _copy_upload_tree_to_memory_fs(upload_files, remote_fs)
        for name in os.listdir(upload_files.os_path):
            path = os.path.join(upload_files.os_path, name)
            if os.path.isfile(path):
                os.remove(path)
        monkeypatch.setattr(config.fs.public_fs, 'protocol', 's3')
        monkeypatch.setattr(config.fs.public_fs, 'read_mode', 'remote_then_local')
        monkeypatch.setattr(
            type(config.fs.public_fs), 'target_fs', property(lambda _: remote_fs)
        )

        public = PublicUploadFiles(test_upload_id)
        assert public.storage_fs is remote_fs

    def test_remote_then_local_valid_marker_selects_remote(
        self, monkeypatch, test_upload_id
    ):
        monkeypatch.setattr(config.fs.public_fs, 'protocol', None)
        _, _entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        remote_fs = self._setup_memory_public_fs(monkeypatch, write_mode='local_only')
        _copy_upload_tree_to_memory_fs(upload_files, remote_fs)
        write_ready_marker(upload_files.os_path, test_upload_id, 'public')

        public = PublicUploadFiles(test_upload_id)
        assert public.storage_fs is remote_fs
        marker = RemoteReadyMarker.load(upload_files.os_path, remote_fs)
        assert marker is not None
        assert marker.matches_remote(remote_fs, upload_files.os_path)

    def test_remote_then_local_marker_mismatch_stays_local(
        self, monkeypatch, test_upload_id
    ):
        monkeypatch.setattr(config.fs.public_fs, 'protocol', None)
        _, _entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        remote_fs = self._setup_memory_public_fs(monkeypatch, write_mode='local_only')
        _copy_upload_tree_to_memory_fs(upload_files, remote_fs)
        write_ready_marker(upload_files.os_path, test_upload_id, 'public')
        marker = RemoteReadyMarker.load(upload_files.os_path, remote_fs)
        assert marker is not None
        mismatched = RemoteReadyMarker(
            schema_version=marker.schema_version,
            upload_id=marker.upload_id,
            access='restricted',
            created_at=marker.created_at,
            artifacts=tuple(
                ArtifactRecord(name=item.name, size=item.size, etag='not-the-etag')
                for item in marker.artifacts
            ),
        )
        mismatched.save(upload_files.os_path, remote_fs)
        invalidate_ready_cache(upload_files.os_path)

        public = PublicUploadFiles(test_upload_id)
        assert isinstance(public.storage_fs, LocalFileSystem)
        assert public.access == 'public'

    def test_repack_renames_both_sides_and_rewrites_marker(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, upload_files = self._setup_recording_archive_fs(
            monkeypatch,
            tmp_path,
            test_upload_id,
            entry_specs='p',
            read_mode='remote_then_local',
        )
        local_dir = DirectoryObject(upload_files.os_path, fs=LocalFileSystem())
        remote_dir = DirectoryObject(upload_files.os_path, fs=proxy)
        assert local_dir.zip_fp('public').exists()
        assert remote_dir.zip_fp('public').exists()

        packed = PublicUploadFiles(test_upload_id)
        assert packed.access == 'public'
        packed.re_pack(with_embargo=True)
        assert packed.access == 'restricted'
        packed.close()

        assert not local_dir.zip_fp('public').exists()
        assert local_dir.zip_fp('restricted').exists()
        assert not remote_dir.zip_fp('public').exists()
        assert remote_dir.zip_fp('restricted').exists()

        old_marker = RemoteReadyMarker.load(upload_files.os_path, proxy)
        assert old_marker is not None
        names = {item.name for item in old_marker.artifacts}
        assert 'raw-restricted.plain.zip' in names
        assert 'raw-public.plain.zip' not in names
        assert any(name.endswith('.msg.msg') and 'restricted' in name for name in names)

        after = PublicUploadFiles(test_upload_id)
        assert after.storage_fs is proxy
        after.close()

    def test_delete_removes_remote_ready_marker_and_artifacts(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        staging.pack(entries, with_embargo=False)
        staging.delete()

        public = PublicUploadFiles(test_upload_id)
        marker_location = _remote_marker_location(public.os_path)
        zip_location = FSUtility.remote_path(
            DirectoryObject(public.os_path).zip_fp('public').os_path
        )
        assert remote_fs.exists(marker_location)
        assert remote_fs.exists(zip_location)

        public.delete()

        assert not remote_fs.exists(marker_location)
        assert not remote_fs.exists(zip_location)
        assert not public.exists()

    def test_delete_access_artifacts_clears_local_and_remote(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        staging.pack(entries, with_embargo=False)
        staging.delete()

        public = PublicUploadFiles(test_upload_id)
        local_dir = DirectoryObject(public.os_path, fs=LocalFileSystem())
        remote_dir = DirectoryObject(public.os_path, fs=remote_fs)
        for directory, fs in (
            (local_dir, LocalFileSystem()),
            (remote_dir, remote_fs),
        ):
            for artifact in (
                directory.zip_fp('restricted', fs=fs),
                directory.msg_fp('restricted', fs=fs),
            ):
                artifact._fs.makedirs(
                    os.path.dirname(artifact.location).replace('\\', '/'),
                    exist_ok=True,
                )
                with artifact._fs.open(artifact.location, 'wb') as file_obj:
                    file_obj.write(b'stale-restricted-artifact')

        delete_access_artifacts(
            public.os_path, 'restricted', include_raw=True, include_archive=True
        )

        assert not local_dir.zip_fp('restricted').exists()
        assert not local_dir.msg_fp('restricted').exists()
        assert not remote_dir.zip_fp('restricted').exists()
        assert not remote_dir.msg_fp('restricted').exists()
        assert local_dir.zip_fp('public').exists()
        assert remote_dir.zip_fp('public').exists()

    def test_files_to_bundle_uses_selected_backend(self, monkeypatch, test_upload_id):
        remote_fs = self._setup_memory_public_fs(monkeypatch)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        staging.pack(entries, with_embargo=False)
        staging.delete()

        public = PublicUploadFiles(test_upload_id)
        assert public.storage_fs is remote_fs
        sources = list(public.files_to_bundle(BundleExportSettings()))
        assert sources
        assert all(source._fs is remote_fs for source in sources)

    def test_files_to_bundle_stays_local_when_mirror_fails(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)

        def fail_put(*args, **kwargs):
            raise OSError('remote put failed')

        monkeypatch.setattr(remote_fs, 'put_file', fail_put)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        with pytest.raises(OSError, match='remote put failed'):
            staging.pack(entries, with_embargo=False)

        public = PublicUploadFiles(test_upload_id)
        sources = list(public.files_to_bundle(BundleExportSettings()))
        assert sources
        assert all(isinstance(source._fs, LocalFileSystem) for source in sources)

    def test_to_staging_after_failed_copy_extracts_local_zip(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)

        def fail_put(*args, **kwargs):
            raise OSError('remote put failed')

        monkeypatch.setattr(remote_fs, 'put_file', fail_put)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        with pytest.raises(OSError, match='remote put failed'):
            staging.pack(entries, with_embargo=False)
        staging.delete()

        public = PublicUploadFiles(test_upload_id)
        assert isinstance(public.storage_fs, LocalFileSystem)
        restored = public.to_staging(create=True)
        with restored.raw_file(entries[0].mainfile) as file_obj:
            assert file_obj.read()

    def test_repack_after_partial_copy_recopies_from_local(
        self, monkeypatch, test_upload_id
    ):
        remote_fs = self._setup_memory_public_fs(monkeypatch)
        original_put = remote_fs.put_file

        def put_zip_only(lpath, rpath, **kwargs):
            if str(rpath).endswith('.msg.msg'):
                raise OSError('remote put failed')
            return original_put(lpath, rpath, **kwargs)

        monkeypatch.setattr(remote_fs, 'put_file', put_zip_only)
        _, entries, staging = create_staging_upload(test_upload_id, entry_specs='p')
        with pytest.raises(OSError, match='remote put failed'):
            staging.pack(entries, with_embargo=False)

        public = PublicUploadFiles(test_upload_id)
        assert isinstance(public.storage_fs, LocalFileSystem)
        monkeypatch.setattr(remote_fs, 'put_file', original_put)
        public.re_pack(with_embargo=True)

        remote_dir = DirectoryObject(public.os_path, fs=remote_fs)
        assert remote_dir.zip_fp('restricted').exists()
        assert remote_dir.msg_fp('restricted').exists()
        marker = RemoteReadyMarker.load(public.os_path, remote_fs)
        assert marker is not None
        assert marker.matches_remote(remote_fs, public.os_path)
        assert public.storage_fs is remote_fs

    @pytest.fixture(scope='function')
    def empty_test_upload(self, test_upload_id: str) -> UploadFiles:
        _, _, upload_files = create_public_upload(
            test_upload_id, entry_specs='', with_upload=False
        )

        return upload_files

    @pytest.fixture(
        scope='function',
        params=itertools.product(['r', 'rr', 'p', 'pp', 'RR', 'PP'], [True, False]),
    )
    def test_upload(self, request, test_upload_id: str) -> PublicUploadWithFiles:
        entry_specs, both_accesses = request.param
        embargo_length = 12 if 'r' in entry_specs.lower() else 0
        _, entries, upload_files = create_staging_upload(
            test_upload_id, entry_specs=entry_specs, embargo_length=embargo_length
        )
        upload_files.pack(entries, with_embargo=embargo_length > 0)
        upload_files.delete()
        public_upload_files = PublicUploadFiles(test_upload_id)
        if both_accesses:
            # Artificially create an empty archive files and raw zip file with the opposite access
            # TODO: This should only be needed for an interim period
            other_access = 'public' if embargo_length else 'restricted'
            # Fill them with dummy content (we should never try to open them)
            with open(public_upload_files.zip_fp(other_access).os_path, mode='wb') as f:
                f.write(b'-' * empty_zip_file_size)
            with open(public_upload_files.msg_fp(other_access).os_path, mode='wb') as f:
                f.write(b'-' * empty_archive_file_size)
        return test_upload_id, entries, PublicUploadFiles(test_upload_id)

    def test_to_staging_upload_files(self, test_upload):
        _, entries, upload_files = test_upload
        access = upload_files.access
        assert upload_files.to_staging() is None
        staging_upload_files = upload_files.to_staging(create=True)
        assert staging_upload_files is not None
        assert str(staging_upload_files) == str(upload_files.to_staging())

        upload_path = upload_files.os_path
        all_files = list(
            os.path.join(upload_path, f)
            for f in os.listdir(upload_path)
            if os.path.isfile(os.path.join(upload_path, f))
        )

        # We override the public files before packing to see what packing does to the files
        for f in all_files:
            with open(f, 'wb') as fh:
                if access in os.path.basename(f):
                    fh.write(b'-' * 50)
                else:
                    fh.write(b'')

        staging_upload_files.pack(
            entries,
            with_embargo=entries[0].with_embargo,
            create=False,
            include_raw=False,
        )
        staging_upload_files.delete()

        # We do a very simple check. Files that are expected to be modified by pack
        # should have a different size, and files that have been removed should have the wrong access.
        for file_name in all_files:
            if not os.path.exists(file_name):
                assert access not in os.path.basename(file_name)
            elif access in os.path.basename(file_name):
                if file_name.endswith('.msg.msg'):
                    assert os.path.getsize(file_name) > 100, (
                        'Archive files should have been packed'
                    )
                else:
                    assert os.path.getsize(file_name) == 50, (
                        'Raw file should not have been changed'
                    )
            else:
                # other access
                assert os.path.getsize(file_name) <= empty_archive_file_size, (
                    'Files with other access should be empty'
                )

        assert upload_files.to_staging() is None

    @pytest.mark.parametrize(
        'with_source_h5',
        [True, False],
        ids=['with-source-h5', 'without-source-h5'],
    )
    def test_to_staging_upload_files_include_archive_h5(
        self, test_upload_id, with_source_h5
    ):
        _, entries, public_upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        if not with_source_h5:
            public_upload_files.h5_fp(public_upload_files.access).delete()

        restored_staging_upload_files = public_upload_files.to_staging(
            create=True, include_archive=True
        )
        assert restored_staging_upload_files is not None

        for entry in entries:
            with restored_staging_upload_files.read_archive(entry.entry_id) as archive:
                assert entry.entry_id in archive

            archive_h5 = PathObject(
                restored_staging_upload_files.archive_hdf5_location(entry.entry_id)
            )
            assert archive_h5.exists() == with_source_h5

        restored_staging_upload_files.delete()

    def test_repack(self, test_upload):
        upload_id, entries, upload_files = test_upload
        for entry in entries:
            entry.with_embargo = False
        upload_files.re_pack(with_embargo=False)
        assert_upload_files(upload_id, entries, PublicUploadFiles, with_embargo=False)
        assert upload_files.access == 'public'
        with pytest.raises(KeyError):
            StagingUploadFiles(upload_files.upload_id)

    @pytest.mark.parametrize(
        'suffixes,suffix',
        [
            pytest.param(None, '', id='none'),
            pytest.param('v1', '-v1', id='single'),
            pytest.param(['v2', 'v1'], '-v2', id='fallback'),
        ],
    )
    def test_archive_version_suffix(
        self, monkeypatch, test_upload_id, suffixes, suffix, mongo_function, request
    ):
        monkeypatch.setattr('nomad.config.fs.archive_version_suffix', suffixes)
        _, entries, upload_files = create_staging_upload(
            test_upload_id, entry_specs='p'
        )
        upload_files.pack(entries, with_embargo=False)
        upload_files.delete()

        public_upload_files = PublicUploadFiles(test_upload_id)

        assert public_upload_files.raw_zip_file_object().exists()
        if (
            not request.config.getoption('--s3-storage')
            and not config.fs.public_fs.protocol
        ):
            assert public_upload_files.join_file(
                f'archive-public{suffix}.msg.msg'
            ).exists()

        assert_upload_files(test_upload_id, entries, PublicUploadFiles)

    def test_archive_version_suffix_fallback(self, monkeypatch, test_upload_id):
        monkeypatch.setattr('nomad.config.fs.archive_version_suffix', ['v1'])
        _, entries, upload_files = create_staging_upload(
            test_upload_id, entry_specs='p'
        )
        upload_files.pack(entries, with_embargo=False)

        monkeypatch.setattr('nomad.config.fs.archive_version_suffix', ['v2', 'v1'])
        v1_file = upload_files._archive_file_object(0, fallback=True)
        v2_file = upload_files._archive_file_object(0, fallback=False)
        assert os.path.basename(v1_file.os_path) == '0-v1.msg'
        assert os.path.basename(v2_file.os_path) == '0-v2.msg'

        upload_files.write_archive('0', {})
        assert v1_file.exists()
        assert v2_file.exists()
        assert (
            os.path.basename(
                upload_files._archive_file_object(0, fallback=True).os_path
            )
            == '0-v2.msg'
        )

        upload_files.delete()

        public_upload_files = PublicUploadFiles(test_upload_id)
        v2_file = public_upload_files.msg_fp(public_upload_files.access, fallback=False)
        v1_file = public_upload_files.msg_fp(public_upload_files.access, fallback=True)
        assert not v2_file.exists()
        assert os.path.basename(v1_file.os_path) == 'archive-public-v1.msg.msg'

    def test_zip_index_parse_once_and_close(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, _, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )

        def fail_zip_fs(self, *args, **kwargs):
            raise AssertionError('listing must not open a new ZipFileSystem')

        monkeypatch.setattr(PublicUploadFiles, '_zip_fs', fail_zip_fs)

        zipfile_calls = {'n': 0}
        original_zipfile = zipfile.ZipFile

        class CountingZipFile(original_zipfile):
            def __init__(self, *args, **kwargs):
                zipfile_calls['n'] += 1
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(zipfile, 'ZipFile', CountingZipFile)

        assert upload_files.raw_exists('')
        assert upload_files.raw_exists('examples_template')
        assert not upload_files.raw_exists('missing-dir')
        assert upload_files.raw_isfile('examples_template/template.json')
        assert not upload_files.raw_isfile('examples_template')
        recursive = list(upload_files.raw_listdir('', recursive=True))
        nested = list(
            upload_files.raw_listdir(
                'examples_template', recursive=True, files_only=True
            )
        )
        assert recursive
        assert nested
        assert not upload_files.is_empty()

        assert upload_files._zip is not None
        with upload_files.raw_file('examples_template/template.json', 'rb') as raw_file:
            assert raw_file.read()

        first_parses = zipfile_calls['n']
        assert first_parses == 1
        assert len(list(tmp_path.glob('*.v1.msgpack'))) == 1

        upload_files.close()
        assert upload_files._zip is None
        upload_files.close()

        other = PublicUploadFiles(test_upload_id)
        assert other.raw_exists('examples_template')
        list(other.raw_listdir('', recursive=True))
        with other.raw_file('examples_template/template.json', 'rb') as raw_file:
            assert raw_file.read()
        assert other._zip is not None
        assert zipfile_calls['n'] == first_parses
        other.close()

    def test_remote_storage_parses_zip_from_one_tail_fetch(
        self, test_upload_id, monkeypatch
    ):
        from tests.test_zip_member_index import CountingRangeFS

        _, _, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        raw_zip_obj = upload_files.raw_zip_file_object()
        data = upload_files.storage_fs.read_bytes(raw_zip_obj.location)
        counting_fs = CountingRangeFS(data)
        monkeypatch.setattr(
            PublicUploadFiles,
            '_open_raw_zip_fileobj',
            lambda self: RangeTailFile.from_filesystem(
                counting_fs, counting_fs.path, len(data)
            ),
        )

        assert upload_files.raw_exists('examples_template')
        assert upload_files.raw_isfile('examples_template/template.json')
        list(upload_files.raw_listdir('', recursive=True))
        list(upload_files.raw_listdir('examples_template', recursive=True))
        with upload_files.raw_file('examples_template/template.json', 'rb') as raw_file:
            assert raw_file.read()

        assert counting_fs.info_calls == 1
        assert counting_fs.cat_file_calls == 1
        assert counting_fs.open_calls == 0
        upload_files.close()

    def test_empty_published_zip_root_exists(self, empty_test_upload):
        upload_files = empty_test_upload
        assert upload_files.raw_exists('')
        assert not upload_files.raw_exists('missing')
        assert list(upload_files.raw_listdir('')) == []
        assert list(upload_files.raw_listdir('', recursive=True)) == []
        assert upload_files.is_empty()
        upload_files.close()
        upload_files.close()

    def test_zip_metadata_cache_warm_disk_skips_parse(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        from tests.test_zip_member_index import CountingRangeFS

        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, _, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        raw_zip_obj = upload_files.raw_zip_file_object()
        data = upload_files.storage_fs.read_bytes(raw_zip_obj.location)
        counting_fs = CountingRangeFS(data)
        zipfile_calls = {'n': 0}
        original_zipfile = zipfile.ZipFile

        class CountingZipFile(original_zipfile):
            def __init__(self, *args, **kwargs):
                zipfile_calls['n'] += 1
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(zipfile, 'ZipFile', CountingZipFile)
        monkeypatch.setattr(
            PublicUploadFiles,
            '_open_raw_zip_fileobj',
            lambda self: RangeTailFile.from_filesystem(
                counting_fs, counting_fs.path, len(data)
            ),
        )

        assert upload_files.raw_exists('examples_template')
        listing = list(upload_files.raw_listdir('', recursive=True))
        assert listing
        assert zipfile_calls['n'] == 1
        assert counting_fs.cat_file_calls == 1
        assert len(list(tmp_path.glob('*.v1.msgpack'))) == 1
        upload_files.close()
        assert upload_files._zip is None

        other = PublicUploadFiles(test_upload_id)
        assert other.raw_exists('examples_template')
        assert list(other.raw_listdir('', recursive=True))
        assert zipfile_calls['n'] == 1
        assert counting_fs.cat_file_calls == 1
        with other.raw_file('examples_template/template.json', 'rb') as raw_file:
            payload = raw_file.read()
            raw_file.seek(1)
            assert raw_file.read(3) == payload[1:4]
        other.close()

    def test_zip_metadata_cache_etag_mismatch_refetches(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        from nomad.files.index_cache import object_identity as original_identity

        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'revalidate_seconds', 0)
        _, _, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        token = {'etag': 'v1'}
        zipfile_calls = {'n': 0}
        original_zipfile = zipfile.ZipFile

        class CountingZipFile(original_zipfile):
            def __init__(self, *args, **kwargs):
                zipfile_calls['n'] += 1
                super().__init__(*args, **kwargs)

        def fake_identity(fs, path):
            _path, _etag, size = original_identity(fs, path)
            return (_path, token['etag'], size)

        monkeypatch.setattr(zipfile, 'ZipFile', CountingZipFile)
        monkeypatch.setattr('nomad.files.index_cache.object_identity', fake_identity)

        assert upload_files.raw_exists('examples_template')
        upload_files.close()
        assert zipfile_calls['n'] == 1
        assert len(list(tmp_path.glob('*.v1.msgpack'))) == 1

        other = PublicUploadFiles(test_upload_id)
        assert other.raw_exists('examples_template')
        other.close()
        assert zipfile_calls['n'] == 1

        token['etag'] = 'v2'
        refreshed = PublicUploadFiles(test_upload_id)
        assert refreshed.raw_exists('examples_template')
        refreshed.close()
        assert zipfile_calls['n'] == 2
        assert len(list(tmp_path.glob('*.zip.v1.msgpack'))) == 1

    def test_zip_metadata_cache_close_then_ensure_reuses_disk(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, _, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        zipfile_calls = {'n': 0}
        original_zipfile = zipfile.ZipFile

        class CountingZipFile(original_zipfile):
            def __init__(self, *args, **kwargs):
                zipfile_calls['n'] += 1
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(zipfile, 'ZipFile', CountingZipFile)
        upload_files._ensure_zip_index()
        upload_files.close()
        upload_files._ensure_zip_index()
        assert zipfile_calls['n'] == 1
        upload_files.close()

    def test_zip_metadata_cache_disabled_reparses_each_instance(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', False)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, _, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        zipfile_calls = {'n': 0}
        original_zipfile = zipfile.ZipFile

        class CountingZipFile(original_zipfile):
            def __init__(self, *args, **kwargs):
                zipfile_calls['n'] += 1
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(zipfile, 'ZipFile', CountingZipFile)
        upload_files.raw_exists('examples_template')
        upload_files.close()
        assert zipfile_calls['n'] == 1
        assert list(tmp_path.glob('*.v1.msgpack')) == []

        other = PublicUploadFiles(test_upload_id)
        other.raw_exists('examples_template')
        other.close()
        assert zipfile_calls['n'] == 2

    def test_zip_metadata_cache_corrupt_file_reparsed(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        from nomad.files.index_cache import IndexDiskStore, object_identity

        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, _, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        zipfile_calls = {'n': 0}
        original_zipfile = zipfile.ZipFile

        class CountingZipFile(original_zipfile):
            def __init__(self, *args, **kwargs):
                zipfile_calls['n'] += 1
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(zipfile, 'ZipFile', CountingZipFile)

        assert upload_files.raw_exists('examples_template')
        assert list(upload_files.raw_listdir('', recursive=True))
        assert zipfile_calls['n'] == 1
        assert len(list(tmp_path.glob('*.zip.v1.msgpack'))) == 1

        location = upload_files.raw_zip_file_object().location
        identity = object_identity(upload_files.storage_fs, location)
        key_path = tmp_path / IndexDiskStore(str(tmp_path), 1024, 'zip').key_filename(
            identity
        )
        key_path.write_bytes(b'garbage')
        upload_files.close()
        clear_index_caches()

        other = PublicUploadFiles(test_upload_id)
        assert other.raw_exists('examples_template')
        assert list(other.raw_listdir('', recursive=True))
        other.close()
        assert zipfile_calls['n'] == 2
        assert key_path.read_bytes() != b'garbage'
        assert len(list(tmp_path.glob('*.zip.v1.msgpack'))) == 1

    def test_archive_toc_cache_warm_disk_skips_version_check(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        from nomad.archive.utils import check_archive_version as original_check
        from nomad.files.archive_toc import ArchiveTocIndex

        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='pp', with_upload=False
        )
        entry_a, entry_b = entries[0].entry_id, entries[1].entry_id
        with upload_files.read_archive(entry_a) as archive:
            cached_a = to_json(archive[entry_a])
        assert len(list(tmp_path.glob('*.toc.v1.msgpack'))) == 1
        upload_files.close()

        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', False)
        baseline = PublicUploadFiles(test_upload_id)
        with baseline.read_archive(entry_a) as archive:
            assert to_json(archive[entry_a]) == cached_a
        with baseline.read_archive(entry_b) as archive:
            cached_b = to_json(archive[entry_b])
        baseline.close()
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)

        toc_data = next(tmp_path.glob('*.toc.v1.msgpack')).read_bytes()
        index = ArchiveTocIndex.from_bytes(toc_data)
        start, end = index.span(entry_b)
        expected_block_size = min(end - start, 32 * 1024 * 1024)

        version_calls = {'n': 0}
        open_calls: list[dict] = []

        def counting_check(file_or_path, *args, **kwargs):
            version_calls['n'] += 1
            return original_check(file_or_path, *args, **kwargs)

        original_open = FSUtility.open

        @contextmanager
        def recording_open(*args, **kwargs):
            open_calls.append(dict(kwargs))
            with original_open(*args, **kwargs) as file_obj:
                yield file_obj

        monkeypatch.setattr(
            'nomad.files.archive_toc.check_archive_version', counting_check
        )
        monkeypatch.setattr('nomad.files.FSUtility.open', recording_open)

        other = PublicUploadFiles(test_upload_id)
        with other.read_archive(entry_b) as archive:
            assert to_json(archive[entry_b]) == cached_b
        assert version_calls['n'] == 0
        assert open_calls[-1].get('block_size') == expected_block_size
        other.close()

    def test_archive_toc_cache_unknown_entry_no_io(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='pp', with_upload=False
        )
        with upload_files.read_archive(entries[0].entry_id) as archive:
            assert to_json(archive[entries[0].entry_id])
        upload_files.close()

        open_calls = {'n': 0}
        original_open = FSUtility.open

        @contextmanager
        def counting_open(*args, **kwargs):
            open_calls['n'] += 1
            with original_open(*args, **kwargs) as file_obj:
                yield file_obj

        monkeypatch.setattr('nomad.files.FSUtility.open', counting_open)
        other = PublicUploadFiles(test_upload_id)
        with pytest.raises(KeyError):
            with other.read_archive('missing-entry'):
                pass
        assert open_calls['n'] == 0
        other.close()

    def test_archive_toc_cache_corrupt_file_reparsed(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        from nomad.files.index_cache import IndexDiskStore, object_identity

        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='pp', with_upload=False
        )
        entry_id = entries[0].entry_id
        with upload_files.read_archive(entry_id) as archive:
            expected = to_json(archive[entry_id])
        assert len(list(tmp_path.glob('*.toc.v1.msgpack'))) == 1

        msg_file = upload_files.msg_fp(upload_files.access, fallback=True)
        identity = object_identity(upload_files.storage_fs, msg_file.location)
        key_path = tmp_path / IndexDiskStore(str(tmp_path), 1024, 'toc').key_filename(
            identity
        )
        key_path.write_bytes(b'garbage')
        upload_files.close()
        clear_index_caches()

        other = PublicUploadFiles(test_upload_id)
        with other.read_archive(entry_id) as archive:
            assert to_json(archive[entry_id]) == expected
        other.close()
        assert key_path.read_bytes() != b'garbage'

    def test_archive_toc_cache_disabled_unchanged(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        from nomad.archive.utils import check_archive_version as original_check

        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', False)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='pp', with_upload=False
        )
        version_calls = {'n': 0}

        def counting_check(file_or_path, *args, **kwargs):
            version_calls['n'] += 1
            return original_check(file_or_path, *args, **kwargs)

        monkeypatch.setattr('nomad.archive.utils.check_archive_version', counting_check)
        entry_a, entry_b = entries[0].entry_id, entries[1].entry_id
        with upload_files.read_archive(entry_a) as archive:
            assert to_json(archive[entry_a])
        upload_files.close()
        first_calls = version_calls['n']
        assert first_calls >= 1
        assert list(tmp_path.glob('*.toc.v1.msgpack')) == []

        other = PublicUploadFiles(test_upload_id)
        with other.read_archive(entry_b) as archive:
            assert to_json(archive[entry_b])
        other.close()
        assert version_calls['n'] > first_calls

    def test_archive_toc_prefers_newer_suffix_over_cached_fallback(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        monkeypatch.setattr(config.fs, 'archive_version_suffix', ['v1.2', 'v1'])
        _, entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        entry_id = entries[0].entry_id
        access = upload_files.access
        newer = os.path.join(upload_files.os_path, f'archive-{access}-v1.2.msg.msg')
        older = os.path.join(upload_files.os_path, f'archive-{access}-v1.msg.msg')
        newer_bytes = pathlib.Path(newer).read_bytes()
        os.rename(newer, older)
        upload_files.close()

        cached = PublicUploadFiles(test_upload_id)
        with cached.read_archive(entry_id) as archive:
            expected = to_json(archive[entry_id])
        cached.close()
        pathlib.Path(newer).write_bytes(newer_bytes)

        opened: list[str] = []
        original_open = FSUtility.open

        @contextmanager
        def recording_open(path, *args, **kwargs):
            opened.append(str(path))
            with original_open(path, *args, **kwargs) as file_obj:
                yield file_obj

        monkeypatch.setattr(FSUtility, 'open', recording_open)
        current = PublicUploadFiles(test_upload_id)
        with current.read_archive(entry_id) as archive:
            assert to_json(archive[entry_id]) == expected
        current.close()
        assert any(path.endswith(f'archive-{access}-v1.2.msg.msg') for path in opened)
        assert not any(path.endswith(f'archive-{access}-v1.msg.msg') for path in opened)

    def test_legacy_archive_toc_is_not_reparsed(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        from nomad.archive.utils import check_archive_version as original_check
        from nomad.archive.utils import v2_magic

        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        entry_id = entries[0].entry_id
        msg_file = upload_files.msg_fp(upload_files.access, fallback=True)
        pathlib.Path(msg_file.os_path).write_bytes(v2_magic + b'\x00' * 64)
        upload_files.close()
        clear_index_caches()

        version_calls = {'n': 0}
        full_opens = {'n': 0}

        def counting_check(file_or_path, *args, **kwargs):
            version_calls['n'] += 1
            return original_check(file_or_path, *args, **kwargs)

        @contextmanager
        def fake_full_archive(self, requested_id):
            full_opens['n'] += 1
            yield {requested_id: {'metadata': {'entry_id': requested_id}}}

        monkeypatch.setattr(
            'nomad.files.archive_toc.check_archive_version', counting_check
        )
        monkeypatch.setattr(PublicUploadFiles, '_open_full_archive', fake_full_archive)

        first = PublicUploadFiles(test_upload_id)
        with first.read_archive(entry_id) as archive:
            assert archive[entry_id]['metadata']['entry_id'] == entry_id
        first_calls = version_calls['n']
        assert first_calls >= 1
        assert full_opens['n'] == 1
        assert len(list(tmp_path.glob('*.toc.v1.msgpack'))) == 1
        first.close()

        second = PublicUploadFiles(test_upload_id)
        with second.read_archive(entry_id) as archive:
            assert archive[entry_id]['metadata']['entry_id'] == entry_id
        second.close()
        assert version_calls['n'] == first_calls
        assert full_opens['n'] == 2

    def test_archive_toc_cache_consumer_error_does_not_discard(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        from nomad.archive.utils import check_archive_version as original_check

        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        entry_id = entries[0].entry_id
        with upload_files.read_archive(entry_id) as archive:
            to_json(archive[entry_id])
        assert len(list(tmp_path.glob('*.toc.v1.msgpack'))) == 1
        upload_files.close()

        upload_files = PublicUploadFiles(test_upload_id)
        with pytest.raises(ValueError, match='consumer'):
            with upload_files.read_archive(entry_id) as archive:
                to_json(archive[entry_id])
                raise ValueError('consumer')
        assert len(list(tmp_path.glob('*.toc.v1.msgpack'))) == 1
        upload_files.close()

        version_calls = {'n': 0}

        def counting_check(file_or_path, *args, **kwargs):
            version_calls['n'] += 1
            return original_check(file_or_path, *args, **kwargs)

        monkeypatch.setattr(
            'nomad.files.archive_toc.check_archive_version', counting_check
        )

        other = PublicUploadFiles(test_upload_id)
        with other.read_archive(entry_id) as archive:
            assert to_json(archive[entry_id])
        assert version_calls['n'] == 0
        other.close()

    def test_archive_toc_cache_lazy_reader_failure_falls_back(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        import nomad.files as files_module

        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='pp', with_upload=False
        )
        entry_id = entries[0].entry_id
        with upload_files.read_archive(entry_id) as archive:
            expected = to_json(archive[entry_id])
        assert len(list(tmp_path.glob('*.toc.v1.msgpack'))) == 1
        upload_files.close()

        original_lazy_reader = files_module.LazyReader
        calls = {'n': 0}

        class FailingLazyReader(original_lazy_reader):
            def __init__(self, *args, **kwargs):
                calls['n'] += 1
                if calls['n'] == 1:
                    raise ValueError('bad offset')
                super().__init__(*args, **kwargs)

        monkeypatch.setattr('nomad.files.uploads.LazyReader', FailingLazyReader)

        other = PublicUploadFiles(test_upload_id)
        with other.read_archive(entry_id) as archive:
            assert to_json(archive[entry_id]) == expected
        assert list(tmp_path.glob('*.toc.v1.msgpack')) == []
        other.close()

    def test_archive_toc_memory_cache_warm_read_is_single_open(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id
        )
        entry_id = entries[0].entry_id
        cold = PublicUploadFiles(test_upload_id)
        with cold.read_archive(entry_id) as archive:
            expected = to_json(archive[entry_id])
        cold.close()
        proxy.calls.clear()

        warm = PublicUploadFiles(test_upload_id)
        with warm.read_archive(entry_id) as archive:
            assert to_json(archive[entry_id]) == expected
        warm.close()

        msg_calls = [
            call
            for call in _published_msg_calls(proxy.calls, warm.access)
            if call[0] not in ('exists', 'size')
        ]
        assert [call[0] for call in msg_calls] == ['open']
        assert 'size' in msg_calls[0][2]
        assert 'block_size' in msg_calls[0][2]
        assert msg_calls[0][2]['size'] is not None
        assert msg_calls[0][2]['block_size'] is not None

    def test_archive_toc_memory_cache_ttl_expiry_revalidates(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        clock = {'now': 1_000_000.0}
        monkeypatch.setattr(time, 'monotonic', lambda: clock['now'])
        proxy, entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id
        )
        entry_id = entries[0].entry_id
        cold = PublicUploadFiles(test_upload_id)
        with cold.read_archive(entry_id) as archive:
            expected = to_json(archive[entry_id])
        cold.close()
        proxy.calls.clear()

        clock['now'] += 61
        expired = PublicUploadFiles(test_upload_id)
        with expired.read_archive(entry_id) as archive:
            assert to_json(archive[entry_id]) == expected
        expired.close()

        msg_calls = [
            call
            for call in _published_msg_calls(proxy.calls, expired.access)
            if call[0] not in ('exists', 'size')
        ]
        assert [call[0] for call in msg_calls] == ['info', 'open']
        assert msg_calls[0][2].get('refresh') is True
        assert 'size' in msg_calls[1][2]
        assert 'block_size' in msg_calls[1][2]

    def test_archive_toc_memory_cache_identity_change_rebuilds(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        clock = {'now': 1_000_000.0}
        monkeypatch.setattr(time, 'monotonic', lambda: clock['now'])
        proxy, entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, entry_specs='pp'
        )
        entry_a = entries[0].entry_id
        cold = PublicUploadFiles(test_upload_id)
        with cold.read_archive(entry_a) as archive:
            to_json(archive[entry_a])
        msg_path = cold.msg_fp(cold.access, fallback=False).location
        cold.close()

        monkeypatch.setattr(config.fs.public_fs, 'protocol', None)
        _, new_entries, new_local = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        _copy_upload_tree_to_memory_fs(new_local, proxy._inner)
        monkeypatch.setattr(config.fs.public_fs, 'protocol', 's3')
        new_entry_id = new_entries[0].entry_id

        clock['now'] += 61
        rebuilt = PublicUploadFiles(test_upload_id)
        with rebuilt.read_archive(new_entry_id) as archive:
            assert to_json(archive[new_entry_id])
        rebuilt.close()
        with pytest.raises(KeyError):
            with PublicUploadFiles(test_upload_id).read_archive(entries[1].entry_id):
                pass
        assert any(
            call[0] == 'info' and call[2].get('refresh') is True
            for call in proxy.calls
            if call[1] == msg_path
        )

    def test_archive_toc_memory_cache_byte_bound_evicts(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'memory_max_mb', 0.002)
        proxy, entries_a, _upload_a = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, entry_specs='p'
        )
        other_id = f'test_upload_{uuid.uuid4().hex}'
        monkeypatch.setattr(config.fs.public_fs, 'protocol', None)
        _, entries_b, upload_b = create_public_upload(
            other_id, entry_specs='p', with_upload=False
        )
        _copy_upload_tree_to_memory_fs(upload_b, proxy._inner)
        monkeypatch.setattr(config.fs.public_fs, 'protocol', 's3')

        first = PublicUploadFiles(test_upload_id)
        with first.read_archive(entries_a[0].entry_id):
            pass
        first_key = first.msg_fp(first.access, fallback=False).location
        first.close()
        from nomad.files import _toc_cache

        assert _toc_cache.peek(first_key) is not None

        second = PublicUploadFiles(other_id)
        with second.read_archive(entries_b[0].entry_id):
            pass
        second_key = second.msg_fp(second.access, fallback=False).location
        second.close()
        assert _toc_cache.peek(first_key) is None
        assert _toc_cache.peek(second_key) is not None

        DirectoryObject(PublicUploadFiles.base_folder_for(other_id)).delete()

    def test_archive_toc_memory_cache_revalidate_zero_always_heads(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'revalidate_seconds', 0)
        proxy, entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id
        )
        entry_id = entries[0].entry_id
        cold = PublicUploadFiles(test_upload_id)
        with cold.read_archive(entry_id):
            pass
        cold.close()
        proxy.calls.clear()

        warm = PublicUploadFiles(test_upload_id)
        with warm.read_archive(entry_id):
            pass
        warm.close()
        msg_calls = [
            call
            for call in _published_msg_calls(proxy.calls, warm.access)
            if call[0] not in ('exists', 'size')
        ]
        assert [call[0] for call in msg_calls] == ['info', 'open']
        assert msg_calls[0][2].get('refresh') is True

    def test_archive_toc_memory_cache_disabled_still_uses_disk(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        from nomad.archive.utils import check_archive_version as original_check

        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'memory_max_mb', 0)
        proxy, entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id
        )
        entry_id = entries[0].entry_id
        cold = PublicUploadFiles(test_upload_id)
        with cold.read_archive(entry_id) as archive:
            expected = to_json(archive[entry_id])
        cold.close()
        assert len(list(tmp_path.glob('*.toc.v1.msgpack'))) == 1
        proxy.calls.clear()

        version_calls = {'n': 0}

        def counting_check(file_or_path, *args, **kwargs):
            version_calls['n'] += 1
            return original_check(file_or_path, *args, **kwargs)

        monkeypatch.setattr(
            'nomad.files.archive_toc.check_archive_version', counting_check
        )
        warm = PublicUploadFiles(test_upload_id)
        with warm.read_archive(entry_id) as archive:
            assert to_json(archive[entry_id]) == expected
        warm.close()
        assert version_calls['n'] == 0
        msg_methods = [
            call[0] for call in _published_msg_calls(proxy.calls, warm.access)
        ]
        assert 'info' in msg_methods
        assert 'open' in msg_methods
        from nomad.files import _toc_cache

        assert (
            _toc_cache.peek(warm.msg_fp(warm.access, fallback=False).location) is None
        )

    def test_archive_toc_if_match_set_to_quoted_etag(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, entry_specs='p'
        )
        entry_id = entries[0].entry_id
        cold = PublicUploadFiles(test_upload_id)
        with cold.read_archive(entry_id):
            pass
        assert cold._toc is not None and cold._toc.identity is not None
        etag = cold._toc.identity.etag
        assert etag.startswith('"') and etag.endswith('"')
        cold.close()

        captured: dict[str, Any] = {}
        original_open = FSUtility.open

        @contextmanager
        def open_with_req_kw(*args, **kwargs):
            captured['if_match'] = kwargs.get('if_match')
            with original_open(*args, **kwargs) as file_obj:
                yield file_obj

        monkeypatch.setattr('nomad.files.FSUtility.open', open_with_req_kw)
        other = PublicUploadFiles(test_upload_id)
        with other.read_archive(entry_id) as archive:
            to_json(archive[entry_id])
        other.close()
        assert captured['if_match'] == etag

    def test_archive_toc_if_match_failure_falls_back(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        entry_id = entries[0].entry_id
        with upload_files.read_archive(entry_id) as archive:
            expected = to_json(archive[entry_id])
        assert len(list(tmp_path.glob('*.toc.v1.msgpack'))) == 1
        upload_files.close()

        original_open = FSUtility.open
        opens = {'n': 0}

        class ExpiredFile:
            def __init__(self):
                self.req_kw: dict[str, str] = {}

            def tell(self):
                return 0

            def seek(self, *args, **kwargs):
                return 0

            def read(self, *args, **kwargs):
                raise OSError('expired')

            def close(self):
                return None

        @contextmanager
        def open_maybe_expired(*args, **kwargs):
            opens['n'] += 1
            if opens['n'] == 1:
                yield ExpiredFile()
                return
            with original_open(*args, **kwargs) as file_obj:
                yield file_obj

        monkeypatch.setattr('nomad.files.FSUtility.open', open_maybe_expired)
        other = PublicUploadFiles(test_upload_id)
        with other.read_archive(entry_id) as archive:
            assert to_json(archive[entry_id]) == expected
        assert list(tmp_path.glob('*.toc.v1.msgpack')) == []
        other.close()
        from nomad.files import _toc_cache

        assert (
            _toc_cache.peek(other.msg_fp(other.access, fallback=False).location) is None
        )

    def test_zip_memory_cache_warm_read_is_single_open(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, entry_specs='p'
        )
        member = 'examples_template/template.json'
        cold = PublicUploadFiles(test_upload_id)
        with cold.raw_file(member, 'rb') as raw_file:
            expected = raw_file.read()
        cold.close()
        proxy.calls.clear()

        warm = PublicUploadFiles(test_upload_id)
        with warm.raw_file(member, 'rb') as raw_file:
            assert raw_file.read() == expected
        zip_calls = _published_zip_calls(proxy.calls, warm.access)
        assert [call[0] for call in zip_calls if call[0] not in ('exists', 'size')] == [
            'open'
        ]
        assert 'size' in [call[2] for call in zip_calls if call[0] == 'open'][0]
        warm.close()

    def test_zip_memory_cache_ttl_expiry_revalidates(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        clock = {'now': 1_000_000.0}
        monkeypatch.setattr(time, 'monotonic', lambda: clock['now'])
        proxy, _entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, entry_specs='p'
        )
        member = 'examples_template/template.json'
        cold = PublicUploadFiles(test_upload_id)
        with cold.raw_file(member, 'rb') as raw_file:
            expected = raw_file.read()
        cold.close()
        proxy.calls.clear()

        clock['now'] += 61
        expired = PublicUploadFiles(test_upload_id)
        with expired.raw_file(member, 'rb') as raw_file:
            assert raw_file.read() == expected
        expired.close()
        zip_calls = [
            call
            for call in _published_zip_calls(proxy.calls, expired.access)
            if call[0] not in ('exists', 'size')
        ]
        assert [call[0] for call in zip_calls] == ['info', 'open']
        assert zip_calls[0][2].get('refresh') is True
        assert 'size' in zip_calls[1][2]

    def test_zip_memory_cache_identity_change_rebuilds(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        clock = {'now': 1_000_000.0}
        monkeypatch.setattr(time, 'monotonic', lambda: clock['now'])
        proxy, _entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, entry_specs='p'
        )
        cold = PublicUploadFiles(test_upload_id)
        listing = [info.path for info in cold.raw_listdir('', recursive=True)]
        assert 'examples_template/template.json' in listing
        zip_path = cold.raw_zip_file_object().location
        cold.close()

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zip_file:
            zip_file.writestr('only_new.txt', b'hello')
        proxy._inner.pipe(zip_path, buf.getvalue())

        clock['now'] += 61
        rebuilt = PublicUploadFiles(test_upload_id)
        new_listing = [info.path for info in rebuilt.raw_listdir('', recursive=True)]
        rebuilt.close()
        assert 'only_new.txt' in new_listing
        assert 'examples_template/template.json' not in new_listing

    def test_zip_memory_cache_stale_index_retries(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        proxy, _entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, entry_specs='p'
        )
        member = 'examples_template/template.json'
        cold = PublicUploadFiles(test_upload_id)
        with cold.raw_file(member, 'rb') as raw_file:
            assert raw_file.read()
        zip_path = cold.raw_zip_file_object().location
        from nomad.files import _zip_cache

        old_entry = _zip_cache.peek(zip_path)
        assert old_entry is not None
        old_identity = old_entry.identity
        cold.close()

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zip_file:
            zip_file.writestr(member, b'replaced-member-bytes')
        proxy._inner.pipe(zip_path, buf.getvalue())
        assert _zip_cache.peek(zip_path) is not None
        assert _zip_cache.peek(zip_path).identity == old_identity

        stale = PublicUploadFiles(test_upload_id)
        with stale.raw_file(member, 'rb') as raw_file:
            assert raw_file.read() == b'replaced-member-bytes'
        stale.close()
        replaced = _zip_cache.peek(zip_path)
        assert replaced is not None
        assert replaced.identity != old_identity

    def test_zip_read_oserror_is_not_a_missing_file(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
        monkeypatch.setattr(
            config.fs.public_fs.metadata_cache, 'directory', str(tmp_path)
        )
        _, _entries, upload_files = create_public_upload(
            test_upload_id, entry_specs='p', with_upload=False
        )
        member = 'examples_template/template.json'
        with upload_files.raw_file(member, 'rb') as raw_file:
            raw_file.read()
        zip_path = upload_files.raw_zip_file_object().location
        upload_files.close()

        def fail_open(*args, **kwargs):
            raise OSError('connection reset')

        monkeypatch.setattr('nomad.files.uploads.open_zip_member', fail_open)
        current = PublicUploadFiles(test_upload_id)
        with pytest.raises(OSError, match='connection reset'):
            with current.raw_file(member, 'rb'):
                pass
        from nomad.files import _zip_cache

        assert _zip_cache.peek(zip_path) is not None
        current.close()

    @pytest.mark.parametrize('error_kind', ['expired', 'precondition'])
    def test_zip_stale_s3_etag_rebuilds_before_retry(
        self, test_upload_id, monkeypatch, tmp_path, error_kind
    ):
        from botocore.exceptions import ClientError
        from s3fs.errors import translate_boto_error
        from s3fs.utils import FileExpired

        from nomad.files import _zip_cache

        proxy, _, _ = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, entry_specs='p'
        )
        member = 'examples_template/template.json'
        cold = PublicUploadFiles(test_upload_id)
        with cold.raw_file(member) as raw_file:
            raw_file.read()
        path = cold.raw_zip_file_object().location
        old_identity = _zip_cache.peek(path).identity
        cold.close()

        replacement = io.BytesIO()
        with zipfile.ZipFile(replacement, 'w') as archive:
            archive.writestr(member, b'updated')
        proxy._inner.pipe(path, replacement.getvalue())
        current_etag = proxy.info(path)['ETag']
        opened = []
        original_open = proxy.open

        class ConditionalFile(io.BytesIO):
            def __init__(self):
                super().__init__(replacement.getvalue())
                self.req_kw = {}

            def read(self, *args):
                if self.req_kw.get('IfMatch') != current_etag:
                    if error_kind == 'expired':
                        raise FileExpired(path, self.req_kw.get('IfMatch'))
                    raise translate_boto_error(
                        ClientError(
                            {
                                'Error': {
                                    'Code': 'PreconditionFailed',
                                    'Message': 'changed',
                                }
                            },
                            'GetObject',
                        )
                    )
                return super().read(*args)

        def conditional_open(location, mode='rb', **kwargs):
            if location != path:
                return original_open(location, mode, **kwargs)
            file = ConditionalFile()
            opened.append(file)
            return file

        monkeypatch.setattr(proxy, 'open', conditional_open)
        with PublicUploadFiles(test_upload_id).raw_file(member) as raw_file:
            assert raw_file.read() == b'updated'
        assert len(opened) == 2
        assert all(file.closed for file in opened)
        assert opened[0].req_kw['IfMatch'] == old_identity.etag
        assert opened[1].req_kw['IfMatch'] == current_etag
        assert _zip_cache.peek(path).identity.etag == current_etag

    def test_zip_repeated_s3_expiry_stops_after_one_retry(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        from s3fs.utils import FileExpired

        from nomad.files import _zip_cache

        self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, entry_specs='p'
        )
        public = PublicUploadFiles(test_upload_id)
        attempts = []

        def expire(fileobj, member):
            attempts.append(fileobj)
            raise FileExpired('raw.zip', 'stale')

        monkeypatch.setattr(public, '_open_zip_member_fileobj', io.BytesIO)
        monkeypatch.setattr('nomad.files.uploads.open_zip_member', expire)
        with pytest.raises(FileExpired):
            with public.raw_file('examples_template/template.json'):
                pass
        assert len(attempts) == 2
        assert all(file.closed for file in attempts)
        assert _zip_cache.peek(public.raw_zip_file_object().location) is None

    def test_zip_memory_cache_disabled_still_uses_disk(
        self, test_upload_id, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'memory_max_mb', 0)
        proxy, _entries, _upload_files = self._setup_recording_archive_fs(
            monkeypatch, tmp_path, test_upload_id, entry_specs='p'
        )
        member = 'examples_template/template.json'
        cold = PublicUploadFiles(test_upload_id)
        with cold.raw_file(member, 'rb') as raw_file:
            expected = raw_file.read()
        cold.close()
        assert len(list(tmp_path.glob('*.zip.v1.msgpack'))) == 1
        proxy.calls.clear()

        zipfile_calls = {'n': 0}
        original_zipfile = zipfile.ZipFile

        class CountingZipFile(original_zipfile):
            def __init__(self, *args, **kwargs):
                zipfile_calls['n'] += 1
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(zipfile, 'ZipFile', CountingZipFile)
        warm = PublicUploadFiles(test_upload_id)
        with warm.raw_file(member, 'rb') as raw_file:
            assert raw_file.read() == expected
        warm.close()
        assert zipfile_calls['n'] == 0
        zip_methods = [
            call[0]
            for call in _published_zip_calls(proxy.calls, warm.access)
            if call[0] not in ('exists', 'size')
        ]
        assert 'info' in zip_methods
        assert 'open' in zip_methods
        from nomad.files import _zip_cache

        assert _zip_cache.peek(warm.raw_zip_file_object().location) is None


def assert_upload_files(
    upload_id: str,
    entries: Iterable[datamodel.EntryMetadata],
    cls,
    no_archive: bool = False,
    **kwargs,
):
    """
    Asserts the files aspect of uploaded data after processing or publishing

    Arguments:
        upload_id: The id of the upload to assert
        cls: The :class:`UploadFiles` subclass that this upload should have
        no_archive:
        **kwargs: Key, value pairs that each entry metadata should have
    """
    upload_files = UploadFiles.get(upload_id)
    assert upload_files is not None
    assert isinstance(upload_files, cls)

    upload_files = UploadFiles.get(upload_id)
    for entry in entries:
        with upload_files.raw_file(entry.mainfile, 'rb') as f:
            f.read()

        try:
            with upload_files.read_archive(entry.entry_id) as archive:
                assert entry.entry_id in archive
        except KeyError:
            assert no_archive

    upload_files.close()


def create_test_upload_files(
    upload_id: str | None,
    archives: list[datamodel.EntryArchive] | None = None,
    published: bool = True,
    embargo_length: int = 0,
    raw_files: str | None = None,
    template_files: str = example_file,
    template_mainfile: str = example_mainfile_raw_path,
    additional_files_path: str | None = None,
) -> UploadFiles:
    """
    Creates an upload_files object and the underlying files for test/mock purposes.

    Arguments:
        upload_id: The upload id for the upload. Will generate a random UUID if None.
        archives: A list of class:`datamodel.EntryArchive` metainfo objects. This will
            be used to determine the mainfiles. Will create respective directories and
            copy the template entry to create raw files for each archive.
            Will also be used to fill the archives in the create upload.
        published: Creates a :class:`PublicUploadFiles` object with published files
            instead of a :class:`StagingUploadFiles` object with staging files. Default
            is published.
        embargo_length: The embargo length
        raw_files: A directory path. All files here will be copied into the raw files
            dir of the created upload files.
        template_files: A zip file with example files in it. One directory will be used
            as a template. It will be copied for each given archive.
        template_mainfile: Path of the template mainfile within the given template_files.
        additional_files_path: Path to additional files to add.
    """
    if upload_id is None:
        upload_id = utils.create_uuid()
    if archives is None:
        archives = []

    upload_files = StagingUploadFiles(upload_id, create=True)
    if raw_files:
        shutil.rmtree(upload_files._raw_dir.os_path)
        shutil.copytree(raw_files, upload_files._raw_dir.os_path)
    upload_files.add_rawfiles(template_files)
    if additional_files_path:
        upload_files.add_rawfiles(additional_files_path)

    upload_raw_files = upload_files.join_dir('raw')
    source = upload_raw_files.join_dir(os.path.dirname(template_mainfile)).os_path

    for archive in archives:
        # create a copy of the given template files for each archive
        mainfile = archive.metadata.mainfile
        mainfile_key = archive.metadata.mainfile_key
        assert mainfile is not None, (
            'Archives to create test upload must have a mainfile'
        )
        target = upload_raw_files.join_file(os.path.dirname(mainfile)).os_path
        if not mainfile_key:
            if os.path.exists(target):
                for file_ in os.listdir(source):
                    shutil.copy(os.path.join(source, file_), target)
            else:
                shutil.copytree(source, target)
            os.rename(
                os.path.join(target, os.path.basename(template_mainfile)),
                os.path.join(target, os.path.basename(mainfile)),
            )

        # create an archive "file" for each archive
        entry_id = archive.metadata.entry_id
        assert entry_id is not None, (
            'Archives to create test upload must have an entry_id'
        )
        if archive.definitions is not None:
            if not archive.definitions.upload_id:
                archive.definitions.upload_id = upload_id
            if not archive.definitions.entry_id:
                archive.definitions.entry_id = entry_id
            PackageDefinition.create_new(archive.definitions)
        upload_files.write_archive(entry_id, archive.m_to_dict(with_def_id=True))

    # remove the template
    shutil.rmtree(source)

    if published:
        upload_files.pack(
            [archive.metadata for archive in archives], with_embargo=embargo_length > 0
        )
        upload_files.delete()
        return UploadFiles.get(upload_id)

    return upload_files


def append_raw_files(upload_id: str, path_source: str, path_in_upload: str):
    """
    Used to append files to the raw files of an upload (published or not), for
    testing purposes.
    """
    upload_files = UploadFiles.get(upload_id)
    if isinstance(upload_files, PublicUploadFiles):
        with FSUtility.open_archive(
            upload_files.raw_zip_file_object().os_path, 'a'
        ) as zip_fs:
            zip_fs.put_file(path_source, path_in_upload)
    else:
        path = upload_files._raw_dir.os_path  # type: ignore
        shutil.copy(path_source, os.path.join(path, path_in_upload))


def test_test_upload_files(raw_files_infra):
    upload_id = utils.create_uuid()
    archives: datamodel.EntryArchive = []
    for index in range(0, 3):
        archive = datamodel.EntryArchive()
        metadata = archive.m_create(datamodel.EntryMetadata)
        metadata.entry_id = f'example_entry_id_{index}'
        metadata.mainfile = f'test/test/entry_{index}/mainfile_{index}.json'
        archives.append(archive)

    upload_files = create_test_upload_files(upload_id, archives, embargo_length=0)

    try:
        assert_upload_files(
            upload_id, [archive.metadata for archive in archives], PublicUploadFiles
        )
    finally:
        if upload_files.exists():
            upload_files.delete()


def _create_raw_folder_with_file(
    upload_files: StagingUploadFiles, folder_name: str, file_name: str, content: str
) -> None:
    folder_path = os.path.join(upload_files._raw_dir.os_path, folder_name)
    os.makedirs(folder_path)
    with open(os.path.join(folder_path, file_name), 'w') as f:
        f.write(content)


@pytest.mark.parametrize(
    'folder_name',
    [
        pytest.param('my[abc]folder', id='square-brackets'),
        pytest.param('my*folder', id='asterisk'),
        pytest.param('my?folder', id='question-mark'),
    ],
)
@pytest.mark.parametrize('copy_or_move', ['copy', 'move'])
def test_copy_or_move_folder_with_glob_wildcard_in_name_is_rejected(
    raw_files_infra, folder_name, copy_or_move
):
    """
    ``copy_or_move`` copies/moves folders via ``self._fs.cp``/``self._fs.mv``,
    i.e. fsspec's ``AbstractFileSystem.copy``/``mv``. Unlike a literal path
    operation, these interpret '*', '?' and '[...]' in the source path as glob
    patterns (``glob.has_magic`` / ``expand_path``) rather than literal
    characters, which can silently operate on the wrong files or raise a
    RecursionError. Such names must be rejected outright with a clear error,
    and the source/destination must be left untouched.
    """
    upload_id = utils.create_uuid()
    upload_files = StagingUploadFiles(upload_id, create=True)
    _create_raw_folder_with_file(upload_files, folder_name, 'data.txt', 'real content')

    with pytest.raises(ValueError):
        upload_files.copy_or_move(folder_name, 'copied_folder', copy_or_move)

    assert not upload_files.raw_exists('copied_folder')
    assert upload_files.raw_exists(f'{folder_name}/data.txt')


def test_copy_folder_with_square_brackets_does_not_touch_unrelated_sibling(
    raw_files_infra,
):
    """
    Before being rejected outright, a folder name containing '[...]' could
    cause ``copy_or_move`` to silently copy an unrelated sibling folder's
    content instead of the requested folder's content, if the sibling's name
    happened to match the glob pattern derived from the '[...]' name. Ensure
    the operation is rejected before it can touch anything.
    """
    upload_id = utils.create_uuid()
    upload_files = StagingUploadFiles(upload_id, create=True)
    folder_name = 'my[a]folder'
    _create_raw_folder_with_file(
        upload_files, folder_name, 'real_data.txt', 'real content'
    )
    # Unrelated sibling folder that happens to match the glob pattern that
    # 'my[a]folder' expands to (i.e. 'myafolder').
    _create_raw_folder_with_file(
        upload_files, 'myafolder', 'unrelated_secret.txt', 'unrelated content'
    )

    with pytest.raises(ValueError):
        upload_files.copy_or_move(folder_name, 'copied_folder', 'copy')

    assert not upload_files.raw_exists('copied_folder')
    assert upload_files.raw_exists(f'{folder_name}/real_data.txt')
    assert upload_files.raw_exists('myafolder/unrelated_secret.txt')


@pytest.mark.parametrize('copy_or_move', ['copy', 'move'])
def test_copy_or_move_folder_into_own_subtree_is_rejected(
    raw_files_infra, copy_or_move
):
    """
    ``copy_or_move`` must reject an operation whose destination lies inside
    the source folder itself (e.g. copying/moving 'folder' to
    'folder/subfolder'). Copying into the own subtree would recurse forever /
    silently create nested duplicates, and moving into the own subtree fails
    deep inside ``shutil.move`` with an unhandled ``shutil.Error``
    ("Cannot move a directory into itself"), crashing the processing worker
    instead of being reported as a normal validation error. Both cases must
    instead be rejected with a ``ValueError`` and leave the source untouched.
    """
    upload_id = utils.create_uuid()
    upload_files = StagingUploadFiles(upload_id, create=True)
    _create_raw_folder_with_file(upload_files, 'folder', 'a.txt', 'hello')

    dest = 'folder/subfolder'
    with pytest.raises(
        ValueError,
        match=r"Cannot (copy|move) 'folder' into itself or one of its own subfolders",
    ):
        upload_files.copy_or_move('folder', dest, copy_or_move)

    assert not upload_files.raw_exists(dest)
    assert upload_files.raw_exists('folder/a.txt')
    assert {x.path for x in upload_files.raw_listdir('folder')} == {'folder/a.txt'}
