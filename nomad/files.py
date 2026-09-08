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

"""
Contains classes and functions to create and maintain file structures
for uploads, and some generic file utilities.

There are two different structures for uploads in two different states: *staging* and *public*.
Possible operations on uploads differ based on this state. Staging is used for
processing, heavily editing, creating hashes, etc. Public is supposed to be a
almost readonly (beside metadata) storage.

.. code-block:: sh

    fs/staging/<upload>/raw/**
                       /archive/<entry_id>.msg
    fs/public/<upload>/raw-{access}.plain.zip
                      /archive-{access}.msg.msg

Where `access` is either "public" (non-embargoed) or "restricted" (embargoed).

There is an implicit relationship between files, based on them being in the same
directory. Each directory with at least one *mainfile* is an *entry directory*
and all the files are *aux* files to that mainfile. This is independent of whether the
respective files actually contributes data or not. An entry directory might
contain multiple mainfiles. E.g., user simulated multiple states of the same system, have
one entry based on the other, etc. In this case the other mainfile is an *aux file* to the
original mainfile, and vice versa.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import stat
import tarfile
import tempfile
import time
import warnings
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import IO, Any, Literal, NamedTuple, cast

import magic
import yaml
import zipstream
from fsspec import AbstractFileSystem, filesystem
from fsspec.implementations.cached import SimpleCacheFileSystem
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.tar import TarFileSystem
from fsspec.implementations.zip import ZipFileSystem
from h5py import File
from msglc.reader import LazyItem
from pathvalidate import sanitize_filename, sanitize_filepath
from pydantic import BaseModel
from upath import UPath

from nomad import datamodel, utils
from nomad.archive import (
    ArchiveReader,
    combine_archive,
    read_archive,
    to_json,
    write_archive,
)
from nomad.common import (
    get_compression_format,
    has_glob_wildcards,
    is_safe_relative_path,
)
from nomad.config import config
from nomad.config.models.config import BundleExportSettings, BundleImportSettings
from nomad.public_storage import (
    Access,
    choose_pack_fs,
    choose_read_fs,
    clear_ready_cache,
    complete_published_write,
    delete_access_artifacts,
    delete_ready_marker,
    detect_published_access,
    rename_published_artifacts,
)
from nomad.zip_index import (
    IndexDiskStore,
    RangeTailFile,
    ZipMemberIndex,
    object_identity,
    open_zip_member,
)

bundle_info_filename = 'bundle_info.json'

logger = logging.getLogger(__name__)

empty_zip_file_size = 22
empty_archive_file_size = 32
empty_hdf5_file_size = 96


class _ReadStats:
    """Accumulates bytes read and wall-clock read time from a remote filesystem."""

    __slots__ = ('read_time', 'read_bytes')

    def __init__(self):
        self.read_time = 0.0
        self.read_bytes = 0


# A stack of active measurement contexts. Reads accumulate into every active
# accumulator so that nested ``measure_fs_reads`` contexts (e.g. a whole-query
# wrapper around ``load_archive``) all observe the same underlying I/O.
_current_read_stats: ContextVar[tuple[_ReadStats, ...]] = ContextVar(
    'nomad_fs_read_stats', default=()
)


@contextmanager
def measure_fs_reads() -> Iterator[_ReadStats]:
    """
    Time reads against the configured remote filesystem.

    Reads performed through :meth:`FSUtility.open` while this context is active
    are accumulated into the yielded :class:`_ReadStats` object. This gives the
    floor that graph queries cannot improve below: the time spent pulling archive
    bytes from remote storage. It is a no-op when no remote filesystem is used.
    """
    stats = _ReadStats()
    token = _current_read_stats.set((*_current_read_stats.get(), stats))
    try:
        yield stats
    finally:
        _current_read_stats.reset(token)


class _TimedReadFile(io.BufferedIOBase):
    """
    Wrap a file handle opened via :meth:`FSUtility.open`, timing ``read`` calls
    into all currently active :class:`_ReadStats` accumulators.
    """

    def __init__(self, file: IO[bytes]):
        super().__init__()
        self._file = file

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        start = time.perf_counter()
        data = self._file.read(size)
        elapsed = time.perf_counter() - start
        for stats in _current_read_stats.get():
            stats.read_time += elapsed
            stats.read_bytes += len(data)
        return data

    def readinto(self, buffer) -> int:
        if (readinto := getattr(self._file, 'readinto', None)) is None:
            data = self._file.read(len(buffer))
            count = len(data)
            buffer[:count] = data
        else:
            start = time.perf_counter()
            count = readinto(buffer)
            elapsed = time.perf_counter() - start
            for stats in _current_read_stats.get():
                stats.read_time += elapsed
                stats.read_bytes += count
            return count
        for stats in _current_read_stats.get():
            stats.read_bytes += count
        return count

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        return self._file.tell()

    def close(self) -> None:
        return self._file.close()

    def fileno(self) -> int:
        return self._file.fileno()

    def flush(self) -> None:
        return self._file.flush()

    def isatty(self) -> bool:
        return False

    def __enter__(self) -> _TimedReadFile:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    @property
    def closed(self) -> bool:
        return self._file.closed


class FSUtility:
    @staticmethod
    def remote_path(path: str | UPath | PathObject) -> str:
        """Return the configured remote location for a nominal public path."""
        if isinstance(path, UPath):
            path = path.path
        elif isinstance(path, PathObject):
            path = path.os_path

        public_fs = config.fs.public_fs
        segment = path
        if public_fs.simplify_path:
            segment = path.split(config.fs.public, 1)[-1]
        return f'{public_fs.bucket}/{segment.removeprefix("/")}'

    @staticmethod
    def upath(path: str | UPath | PathObject) -> UPath:
        """
        The `path` could be either relative or absolute.

        Returns the `UPath` object with filesystem information embedded.
        """
        # falls back to default local file system
        if isinstance(path, UPath):
            path = path.path
        elif isinstance(path, PathObject):
            path = path.os_path

        public_fs = config.fs.public_fs
        if config.fs.public not in path or public_fs.protocol is None:
            return UPath(path)

        public_fs.ensure_buffer_size()

        return UPath(
            FSUtility.remote_path(path), protocol=public_fs.protocol, **public_fs.extra
        )

    @staticmethod
    def is_local(path: str, *, fs: AbstractFileSystem | None = None) -> bool:
        return (
            isinstance(fs, LocalFileSystem)
            if fs is not None
            else isinstance(FSUtility.upath(path).fs, LocalFileSystem)
        )

    @staticmethod
    @contextmanager
    def open(
        path: str,
        mode: Literal['r', 'w', 'a'] = 'r',
        *,
        fs: AbstractFileSystem | None = None,
    ):
        """
        Open the target as plain IO object.
        """
        if fs is None:
            upath = FSUtility.upath(path)
            fs, location = upath.fs, upath.path
        else:
            location = (
                path if isinstance(fs, LocalFileSystem) else FSUtility.remote_path(path)
            )
        if isinstance(fs, LocalFileSystem) or mode == 'r':
            cached_fs = fs
        else:
            # remote file system may not support random access write
            # thus needs a local cache for writing and appending
            cached_fs = SimpleCacheFileSystem(fs=fs)

        with cached_fs.open(location, f'{mode}b') as file:
            if mode == 'r' and _current_read_stats.get():
                yield _TimedReadFile(file)
            else:
                yield file

    @staticmethod
    @contextmanager
    def open_h5(
        path: str,
        mode: Literal['r', 'w', 'a'] = 'r',
        *,
        fs: AbstractFileSystem | None = None,
        **kwargs,
    ):
        """
        Open the target as HDF5 file object.
        """
        with (
            FSUtility.open(path, mode, fs=fs) as file,
            File(file, mode, **kwargs) as h5_file,
        ):
            yield h5_file

    @staticmethod
    @contextmanager
    def _open_archive_fs(
        path: str, mode: Literal['a', 'w', 'r'], protocol=None, extra=None
    ):
        if path.lower().endswith(('.zip', '.eln')):
            fs_class = ZipFileSystem
            fs_options = [mode]
            if protocol:
                fs_options.extend((protocol, extra))
        elif path.lower().endswith(('.tgz', '.gz', '.tar.gz', '.tar.bz2', '.tar')):
            fs_class = TarFileSystem
            fs_options = [None]
            if protocol:
                fs_options.extend((extra, protocol))
        else:
            raise ValueError(f'Unrecognized archive format: {path}')

        def _wrap(_fs):
            yield _fs
            if hasattr(_fs, 'close'):
                _fs.close()

        if mode == 'r' or not protocol:
            yield from _wrap(fs_class(path, *fs_options))
        else:
            # remote write or append
            remote_fs: AbstractFileSystem = filesystem(protocol, **extra)
            with tempfile.TemporaryDirectory() as tmp_dir:
                _, tmp_path = tempfile.mkstemp(None, None, dir=tmp_dir)
                if mode == 'a':
                    remote_fs.get_file(path, tmp_path)
                yield from _wrap(fs_class(tmp_path, fs_options[0]))
                remote_fs.put_file(tmp_path, path)

    @staticmethod
    @contextmanager
    def open_archive(
        path: str,
        mode: Literal['a', 'w', 'r'] = 'r',
        *,
        fs: AbstractFileSystem | None = None,
    ):
        """
        Open the target as `ZipFileSystem` or `TarFileSystem`.
        """
        if fs is not None:
            if isinstance(fs, LocalFileSystem):
                with FSUtility._open_archive_fs(path, mode) as archive_fs:
                    yield archive_fs
            else:
                protocol = fs.protocol
                if isinstance(protocol, tuple):
                    protocol = protocol[0]
                with FSUtility._open_archive_fs(
                    FSUtility.remote_path(path),
                    mode,
                    protocol,
                    fs.storage_options,
                ) as archive_fs:
                    yield archive_fs
        elif config.fs.public in path:
            with FSUtility._open_archive_fs(
                FSUtility.upath(path).path,
                mode,
                config.fs.public_fs.protocol,
                config.fs.public_fs.extra,
            ) as archive_fs:
                yield archive_fs
        else:
            with FSUtility._open_archive_fs(path, mode) as archive_fs:
                yield archive_fs


@dataclass(slots=True)
class _RawEntry:
    """Internal lightweight listing entry.

    Stores only the information needed for sorting, paging, and response
    construction. Directory sizes are intentionally not tracked.
    """

    path: str
    is_file: bool
    size: int | None = None


def mkdtemp(prefix: str):
    return tempfile.mkdtemp(None, sanitize_filename(prefix), config.fs.tmp)


class PathObject:
    """
    Object storage-like abstraction for paths in general.
    Attributes:
        os_path: The full os path of the object.
    """

    def __init__(self, os_path: str | UPath, *, fs: AbstractFileSystem | None = None):
        self.os_path = os_path if isinstance(os_path, str) else os_path.as_posix()
        self._fs = fs or LocalFileSystem()

    @property
    def location(self):
        """
        The actual location on the file system.
        For local file system, it is `os_path`.
        For other file systems, it is the location stored in `FSUtility.upath`.
        """
        if isinstance(self._fs, LocalFileSystem):
            return self.os_path

        return FSUtility.remote_path(self)

    def delete(self):
        if self.exists():
            self._fs.rm(self.location, recursive=True)

    def exists(self):
        return self._fs.exists(self.location)

    def move_to(self, dest: PathObject):
        assert type(self._fs) is type(dest._fs)
        if self.exists():
            self._fs.mv(self.location, dest.location)

    @property
    def size(self):
        return self._fs.size(self.location)

    def __repr__(self) -> str:
        return self.location


class DirectoryObject(PathObject):
    """
    Object storage-like abstraction for directories.
    """

    def __init__(
        self,
        os_path: str | UPath,
        create: bool = False,
        *,
        fs: AbstractFileSystem | None = None,
    ):
        super().__init__(os_path, fs=fs)
        if create:
            self._fs.mkdirs(self.os_path, exist_ok=True)

    def join_dir(self, path, create: bool = False) -> DirectoryObject:
        return DirectoryObject(os.path.join(self.os_path, path), create, fs=self._fs)

    def join_file(self, path, *, fs: AbstractFileSystem | None = None) -> PathObject:
        return PathObject(os.path.join(self.os_path, path), fs=fs or self._fs)

    def exists(self) -> bool:
        return self._fs.isdir(self.os_path)

    def zip_fp(self, access: str, *, fs: AbstractFileSystem | None = None):
        return self.join_file(f'raw-{access}.plain.zip', fs=fs)

    def msg_fp(
        self,
        access: str,
        fallback: bool = False,
        *,
        fs: AbstractFileSystem | None = None,
    ):
        def versioned_file_name(version_suffix):
            return f'archive-{access}{version_suffix}.msg.msg'

        return _versioned_archive_file_object(
            self, versioned_file_name, fallback, fs=fs
        )

    def h5_fp(self, access: str, *, fs: AbstractFileSystem | None = None):
        return self.join_file(f'archive-{access}.h5', fs=fs)


class RawPathInfo(NamedTuple):
    """
    Stores basic info about a file or folder located at a specific raw path.
    """

    path: str
    is_file: bool
    size: int
    access: str


class RawDirPage(NamedTuple):
    """
    A paginated slice of raw directory metadata.
    """

    content: list[RawPathInfo]
    total: int


class StreamedFile(BaseModel):
    """
    Convenience class for representing a streamed file, together with information about
    file size and an associated path.
    """

    src: Any = None
    path: str
    size: int


def _is_published_artifact_filename(file_path: str) -> bool:
    name = os.path.basename(file_path)
    return (name.startswith('archive-') and name.endswith(('.msg', '.h5'))) or (
        name.startswith('raw-') and name.endswith('.zip')
    )


class FileSource(ABC):
    """
    An abstract class which represents a generic "file source", from which some number of files
    can be retrieved. There are several different ways to create a file source, see subclasses.
    The files in the source are associated with paths and have known sizes.
    """

    def __init__(self, fs: AbstractFileSystem | None = None):
        self._fs = fs or LocalFileSystem()

    @abstractmethod
    def to_streamed_files(self) -> Iterable[StreamedFile]:
        """
        Retrieves the files in the source as :class:`StreamedFile` objects.
        The caller should close the streams when consumed.
        """
        ...

    def to_zipfile(self, path, overwrite: bool = False):
        """
        Generates a zip file from the files in this FileSource and stores it to disk. The
        zipfile content is created by calling :func:`to_zipstream`.
        """
        assert not self._fs.isdir(path), (
            'Exporting to zip file requires a file path, not directory.'
        )
        assert overwrite or not self._fs.exists(path), (
            '`path` already exists. Use `overwrite` to overwrite.'
        )
        with self._fs.open(path, 'wb') as f:
            for chunk in create_zipstream(self.to_streamed_files()):
                f.write(chunk)

    def to_disk(
        self, destination_dir: str, move_files: bool = False, overwrite: bool = False
    ):
        """
        Writes the files from this FileSource to disk, uncompressed. The default implementation
        makes use of :func:`to_streamed_files`. The `destination_dir` should be a directory
        (it will be created if it does not exist). The `move_files` argument instructs
        the method to move the source files if possible.
        """
        dest_path = UPath(destination_dir)
        self._fs.mkdirs(dest_path, exist_ok=True)

        pack_directly_to_remote = (
            config.fs.public_fs.resolved_write_mode == 'remote_only'
            and not FSUtility.is_local(dest_path.as_posix())
        )

        for streamed_file in self.to_streamed_files():
            full_path = dest_path / streamed_file.path
            if pack_directly_to_remote and _is_published_artifact_filename(
                streamed_file.path
            ):
                remote_path = FSUtility.upath(full_path)
                if remote_path.exists():
                    assert overwrite, 'Target already exists and `overwrite` is False'
                with remote_path.open('wb') as output, streamed_file.src as src:
                    while chunk := src.read(config.archive.copy_chunk_size):
                        output.write(chunk)
                continue

            if full_path.exists():
                assert overwrite, 'Target already exists and `overwrite` is False'
            self._fs.mkdirs(full_path.parent, exist_ok=True)
            with (
                self._fs.open(full_path.as_posix(), 'wb') as output_file,
                streamed_file.src,
            ):
                shutil.copyfileobj(streamed_file.src, output_file)

    def close(self):
        """Perform "closing" of the source, if applicable."""
        pass


class BrowsableFileSource(FileSource, ABC):
    """
    A :class:`FileSource` which can be "browsed", like a folder on disk or a zip archive.
    """

    @abstractmethod
    def open(self, path, mode='rb') -> IO:
        """Opens a file by the specified path."""
        ...

    @abstractmethod
    def find(self, path: str) -> list[str]:
        """
        Returns a list of directory contents, located in the directory denoted by `path`
        in this file source.
        """
        ...

    @abstractmethod
    def child(self, path: str) -> BrowsableFileSource:
        """
        Creates a new instance of :class:`BrowsableFileSource` which just contains the
        files located under the specified path.
        """
        ...


class StreamedFileSource(FileSource):
    """
    A :class:`FileSource` created from a single :class:`StreamedFile`.
    """

    def __init__(
        self, streamed_file: StreamedFile, fs: AbstractFileSystem | None = None
    ):
        super().__init__(fs)
        self._file = streamed_file

    def to_streamed_files(self) -> Iterable[StreamedFile]:
        yield self._file


class DiskFileSource(BrowsableFileSource):
    """
    A :class:`FileSource` corresponding to a single file or a folder on disk. The object
    is identified by a `base_path` and a `relative path`. The `base_path` should be a folder,
    the `relative_path` is optional, and used for selecting only a specific file or folder
    located under `base_folder`. The paths of the files retrieved from this source are given
    relative to the `base_path`.
    """

    def __init__(
        self,
        base_path: str,
        relative_path: str | None = None,
        fs: AbstractFileSystem | None = None,
    ):
        super().__init__(fs)
        assert self._fs.isdir(base_path)
        if relative_path:
            relative_path = sanitize_filepath(relative_path)
            assert is_safe_relative_path(relative_path), 'Unsafe relative_path received'
            self.full_path = os.path.join(base_path, relative_path)
            assert self._fs.exists(self.full_path)
        else:
            self.full_path = base_path
        self.base_path = base_path
        self.relative_path = relative_path

    def to_streamed_files(self) -> Iterable[StreamedFile]:
        for target_path in self._fs.find(self.full_path):
            yield StreamedFile(
                path=os.path.relpath(target_path, self.base_path),
                src=self._fs.open(target_path, 'rb'),
                size=self._fs.size(target_path),
            )

    def to_disk(
        self, destination_dir: str, move_files: bool = False, overwrite: bool = False
    ):
        destination_path = UPath(destination_dir)
        if self.relative_path:
            destination_path /= self.relative_path

        self._fs.mkdirs(destination_path.parent, exist_ok=True)

        if self._fs.exists(destination_path):
            assert overwrite, (
                f'Target {destination_path} already exists and `overwrite` is False'
            )

        self._fs.put(self.full_path, destination_path, recursive=True)

        if move_files:
            self._fs.rm(self.full_path, recursive=True)

    def open(self, path, mode='rb') -> IO:
        assert is_safe_relative_path(path)
        return self._fs.open(os.path.join(self.base_path, path), mode)

    def find(self, path: str) -> list[str]:
        assert is_safe_relative_path(path)
        return self._fs.find(os.path.join(self.base_path, path))

    def child(self, path: str) -> DiskFileSource:
        assert is_safe_relative_path(path)
        return DiskFileSource(self.base_path, path)


def _disk_file_source(path_obj: PathObject) -> DiskFileSource:
    location = path_obj.location
    return DiskFileSource(
        os.path.dirname(location), os.path.basename(location), path_obj._fs
    )


class ZipFileSource(BrowsableFileSource):
    """
    Allows us to "wrap" a :class:`zipfile.ZipFile` object and use it as a :class:`BrowsableFileSource`,
    i.e. it denotes a resource (single file or folder) stored in a ZipFile.
    """

    def __init__(
        self,
        zip_file: str | None,
        sub_path: str = '',
        fs: AbstractFileSystem | None = None,
        zip_fs: ZipFileSystem | None = None,
    ):
        super().__init__(fs)
        assert is_safe_relative_path(sub_path)
        self.sub_path = sub_path
        self._zip_fs = zip_fs or ZipFileSystem(zip_file)
        self._owns_zip_fs = zip_fs is None

    def to_streamed_files(self) -> Iterable[StreamedFile]:
        for target_path in self._zip_fs.find(self.sub_path):
            yield StreamedFile(
                path=target_path,
                src=self._zip_fs.open(target_path),
                size=self._zip_fs.size(target_path),
            )

    def open(self, path, mode='rb') -> IO:
        assert 'r' in mode, 'Mode must be a read mode'
        assert all(c in 'rbt' for c in mode), f'Invalid mode for open command: {mode}'
        f = self._zip_fs.open(path)
        return io.TextIOWrapper(f) if 't' in mode else f

    def find(self, path: str) -> list[str]:
        return self._zip_fs.find(path)

    def child(self, path: str) -> ZipFileSource:
        assert is_safe_relative_path(path), 'Unsafe path provided'
        if self.sub_path:
            assert path.startswith(self.sub_path + os.path.sep), (
                'Provided `path` is not a sub path.'
            )
        return ZipFileSource(
            None,
            path,
            fs=self._fs,
            zip_fs=self._zip_fs,
        )

    def close(self):
        if self._owns_zip_fs:
            self._zip_fs.close()


class CombinedFileSource(FileSource):
    """
    Class for defining a :class:`FileSource` by combining multiple "subsources" into one.
    """

    def __init__(
        self, file_sources: Iterable[FileSource], fs: AbstractFileSystem | None = None
    ):
        """file_sources: an Iterable for getting FileSources."""
        super().__init__(fs)
        self._files = file_sources

    def to_streamed_files(self) -> Iterable[StreamedFile]:
        for file in self._files:
            yield from file.to_streamed_files()

    def to_disk(
        self, destination_dir: str, move_files: bool = False, overwrite: bool = False
    ):
        for file in self._files:
            file.to_disk(destination_dir, move_files, overwrite)


class StandardJSONDecoder(json.JSONDecoder):
    """Our standard JSONDecoder, with support for marshaling of datetime objects"""

    def __init__(self, *args, **kwargs):
        def dict_to_object(d: dict):
            if len(d) == 1 and (v := d.get('$datetime')) is not None:
                return datetime.fromisoformat(v)
            return d

        kwargs['object_hook'] = dict_to_object
        super().__init__(**kwargs)


def json_to_streamed_file(json_dict: dict[str, Any], path: str) -> StreamedFile:
    """Converts a json dictionary structure to a :class:`StreamedFile`."""

    class StandardJSONEncoder(json.JSONEncoder):
        """Our standard JSONEncoder with support for marshaling of datetime objects"""

        def default(self, obj):
            if isinstance(obj, datetime):
                return {'$datetime': obj.isoformat()}
            return super().default(obj)

    json_bytes = json.dumps(json_dict, cls=StandardJSONEncoder).encode()
    return StreamedFile(path=path, src=io.BytesIO(json_bytes), size=len(json_bytes))


def create_zipstream(streamed_files: Iterable[StreamedFile], compress: bool = False):
    """
    Creates a zip stream, i.e. a streamed zip file.
    """
    zs = zipstream.ZipStream(
        compress_type=zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED,
        compress_level=9,
    )

    def content_generator(file):
        with file.src as f:
            while data := f.read(1024 * 1024):
                yield data

    for streamed_file in streamed_files:
        zs.add(content_generator(streamed_file), streamed_file.path)

    yield from zs


async def create_zipstream_async(
    streamed_files: Iterable[StreamedFile], compress: bool = False
):
    for x in create_zipstream(streamed_files, compress):
        yield x


def _versioned_archive_file_object(
    target_dir: DirectoryObject,
    file_name: Callable[[str], str],
    fallback: bool,
    *,
    fs: AbstractFileSystem | None = None,
) -> PathObject:
    """
    Creates a file object for an archive file depending on the directory it is or
    will be created in, the recipe to construct the name from a version suffix, and
    a bool that denotes if alternative version suffixes should be considered.
    """
    suffixes = config.fs.archive_version_suffix

    fs = fs or target_dir._fs

    if not isinstance(suffixes, list):
        suffixes = [suffixes]

    if len(suffixes) <= 1:
        return target_dir.join_file(
            file_name(f'-{suffixes[0]}' if suffixes[0] else ''), fs=fs
        )

    if not fallback:
        return target_dir.join_file(file_name(f'-{suffixes[0]}'), fs=fs)

    for suffix in suffixes:
        current_file = target_dir.join_file(file_name(f'-{suffix}'), fs=fs)
        if current_file.exists():
            return current_file

    return target_dir.join_file(file_name(f'-{suffixes[0]}'), fs=fs)


class RawPathReader:
    """Request-scoped access to a single raw path.

    The base implementation is intentionally thin so staging uploads retain their
    existing filesystem behaviour. ``PublicUploadFiles`` supplies the ZIP-aware
    implementation below.
    """

    def __init__(self, upload_files: UploadFiles, path: str):
        self.upload_files = upload_files
        self.path = path

    def exists(self) -> bool:
        return self.upload_files.raw_exists(self.path)

    def isfile(self) -> bool:
        return self.upload_files.raw_isfile(self.path)

    def mime_type(self) -> str:
        return self.upload_files.raw_file_mime_type(self.path)

    @contextmanager
    def open(self, *args, **kwargs):
        with self.upload_files.raw_file(self.path, *args, **kwargs) as file:
            yield file

    def close(self):
        """Release request-scoped resources, if any."""


class UploadFiles(DirectoryObject):
    """Abstract base class for upload files."""

    def __init__(
        self,
        upload_id: str,
        create: bool = False,
        *,
        fs: AbstractFileSystem | None = None,
    ):
        self.logger = utils.get_logger(__name__, upload_id=upload_id)

        super().__init__(os_path=self.base_folder_for(upload_id), create=create, fs=fs)

        if not create and not self.exists():
            raise KeyError(upload_id)

        self.upload_id = upload_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @classmethod
    def _file_area(cls) -> UPath:
        """
        Full path to where the upload files of this class are stored (i.e. either
        staging or public file area).
        """
        raise NotImplementedError()

    @property
    def external_os_path(self):
        """
        Full path to where the upload files of this class are stored on the server.
        This is equal to `self.os_path` if no external path substitutes for staging
        and public area are configured. This is helpful, when nomad is run in a container
        and the mounted path used by nomad are different from the actual paths on the
        host server.
        """
        raise NotImplementedError()

    @classmethod
    def base_folder_for(cls, upload_id: str) -> UPath:
        """
        Full path to the base folder for the upload files (of this class) for the
        specified upload_id.
        """
        return cls._file_area() / upload_id[: config.fs.prefix_size] / upload_id

    @classmethod
    def exists_for(cls, upload_id: str) -> bool:
        """
        If an UploadFiles object (of this class) has been created for this upload_id.
        """
        return cls.base_folder_for(upload_id).exists()

    @staticmethod
    def get(upload_id: str) -> UploadFiles:
        for class_type in (PublicUploadFiles, StagingUploadFiles):
            if class_type.exists_for(upload_id):
                return class_type(upload_id)

        return None  # type: ignore

    def to_staging(
        self, create: bool = False, include_archive: bool = False
    ) -> StagingUploadFiles | None:
        """Casts to or creates corresponding staging upload files or returns None."""
        raise NotImplementedError()

    def is_empty(self) -> bool:
        """If this upload has no content yet."""
        raise NotImplementedError()

    def raw_exists(self, path: str) -> bool:
        """
        Returns True if the specified path is a valid raw path (either file or directory)
        """
        raise NotImplementedError()

    def raw_isfile(self, path: str) -> bool:
        """
        Returns True if the specified path points to a file (rather than a directory).
        """
        raise NotImplementedError()

    def raw_listdir(
        self,
        path: str = '',
        recursive: bool = False,
        files_only: bool = False,
        depth: int = -1,
    ) -> Iterable[RawPathInfo]:
        """
        Returns an iterable of RawPathInfo, one for each element (file or folder) in
        the directory specified by `path`. If `recursive` is set to True, subdirectories are
        also crawled. If `files_only` is set, only the file objects found are returned.
        If path is not a valid directory, the result will be empty. Selecting empty string
        as path (which is the default value) gives the content of the whole raw directory.
        The `path_prefix` argument can be used to filter out elements where the path starts
        with a specific prefix.

        The `depth` argument can be used to limit the depth of the recursion.
        """
        raise NotImplementedError()

    def raw_listdir_page(
        self,
        path: str = '',
        *,
        start: int = 0,
        end: int | None = None,
        recursive: bool = False,
        files_only: bool = False,
        depth: int = -1,
        order: Literal['asc', 'desc'] = 'asc',
        group_directories_first: bool = False,
    ) -> RawDirPage:
        """
        Returns a paginated slice of raw directory metadata.

        Subclasses can override this to avoid materializing full metadata for items outside
        the requested page.
        """
        items = list(self.raw_listdir(path, recursive, files_only, depth))

        if group_directories_first:
            folders = [item for item in items if not item.is_file]
            files = [item for item in items if item.is_file]
            ordered = folders + files
            if order != 'asc':
                ordered = list(reversed(files)) + list(reversed(folders))
        else:
            ordered = items if order == 'asc' else list(reversed(items))

        if end is None:
            end = len(ordered)

        return RawDirPage(content=ordered[start:end], total=len(items))

    def raw_path_exists(self, path: str) -> bool:
        warnings.warn(
            'raw_path_exists() is deprecated; use raw_exists() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.raw_exists(path)

    def raw_path_is_file(self, path: str) -> bool:
        warnings.warn(
            'raw_path_is_file() is deprecated; use raw_isfile() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.raw_isfile(path)

    def raw_directory_list(
        self,
        path: str = '',
        recursive=False,
        files_only=False,
        depth: int = -1,
    ) -> Iterable[RawPathInfo]:
        warnings.warn(
            'raw_directory_list() is deprecated; use raw_listdir() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.raw_listdir(path, recursive, files_only, depth)

    @contextmanager
    def raw_file(self, file_path: str, *args, **kwargs):
        """
        Opens a raw file and returns a file-like object. Additional args, kwargs are
        delegated to the respective `open` call.
        Arguments:
            file_path: The path to the file relative to the upload.
        Raises:
            KeyError: If the file does not exist.
        """
        raise NotImplementedError()

    def raw_file_size(self, file_path: str) -> int:
        """
        Returns:
            The size of the given raw file.
        """
        raise NotImplementedError()

    def raw_file_mime_type(self, file_path: str) -> str:
        assert self.raw_isfile(file_path), (
            'Provided path does not specify a file, or is invalid.'
        )
        with self.raw_file(file_path, 'br') as raw_file:
            return (
                magic.from_buffer(raw_file.read(2048), mime=True)
                or 'application/octet-stream'
            )

    def raw_path_reader(self, path: str) -> RawPathReader:
        """Return a short-lived reader for inspecting and opening one raw path.

        The default reader delegates to the existing raw-file methods. Published
        uploads override this with a reader that reuses the request-scoped ZIP
        member index for the whole download request.
        """
        return RawPathReader(self, path)

    @contextmanager
    def read_archive(self, entry_id: str) -> Iterator[ArchiveReader]:
        """
        Returns an :class:`nomad.archive.ArchiveReader` that contains the
        given entry_id.
        """
        raise NotImplementedError()

    def close(self):
        """Release possibly held system resources (e.g. file handles)."""
        pass

    def delete(self) -> None:
        super().delete()
        if config.fs.prefix_size > 0 and not self._fs.ls(
            parent := os.path.dirname(self.os_path), False
        ):
            self._fs.rm(parent, recursive=True)

    def files_to_bundle(
        self, export_settings: BundleExportSettings
    ) -> Iterable[FileSource]:
        """
        A generator of :class:`FileSource` objects, defining the files/folders to be included in an
        upload bundle when *exporting*. The arguments allows for further filtering of what to include.

        Note, this only yields files to copy from the regular upload directory, not "special" files,
        like the bundle_info.json file, which is created by the :class:`BundleExporter`.
        """
        raise NotImplementedError()

    @classmethod
    def files_from_bundle(
        cls,
        bundle_file_source: BrowsableFileSource,
        import_settings: BundleImportSettings,
    ) -> Iterable[FileSource]:
        """
        Returns an Iterable of :class:`FileSource`, defining the files/folders to be included in an
        upload bundle when *importing*. Only the files specified by the import_settings are included.
        """
        raise NotImplementedError()

    def archive_hdf5_location(self, entry_id: str) -> str:
        """
        Returns the OS path to the target HDF5 file.
        The str will be passed to h5py module for reading and writing.
        We do not provide a raw IO object here, since later this file may be a web resource.
        """
        raise NotImplementedError()


class StagingUploadFiles(UploadFiles):
    def __init__(self, upload_id: str, create: bool = False):
        super().__init__(upload_id, create)

        self._raw_dir = self.join_dir('raw', create)
        self._archive_dir = self.join_dir('archive', create)

    @classmethod
    def _file_area(cls):
        return UPath(config.fs.staging)

    @property
    def _frozen_file(self):
        return self.join_file('.frozen')

    @property
    def external_os_path(self):
        if not config.fs.staging_external:
            return self.os_path

        return self.os_path.replace(config.fs.staging, config.fs.staging_external)

    def to_staging(
        self, create: bool = False, include_archive: bool = False
    ) -> StagingUploadFiles | None:
        return self

    @property
    def size(self) -> int:
        return self._fs.du(self._raw_dir.os_path)

    def _full_path(self, path: str):
        return UPath(self._raw_dir.os_path) / path

    def is_empty(self) -> bool:
        return not self._fs.ls(self._raw_dir.os_path, False)

    def raw_exists(self, path: str) -> bool:
        return is_safe_relative_path(path) and self._fs.exists(self._full_path(path))

    def raw_isfile(self, path: str) -> bool:
        return is_safe_relative_path(path) and self._fs.isfile(self._full_path(path))

    def raw_create_directory(self, path: str):
        assert path and is_safe_relative_path(path), 'Bad path provided'
        self._fs.makedirs(self._full_path(path).as_posix(), True)

    def raw_listdir(
        self,
        path: str = '',
        recursive: bool = False,
        files_only: bool = False,
        depth: int = -1,
    ) -> Iterable[RawPathInfo]:
        if not is_safe_relative_path(path) or depth == 0:
            return

        fs = self._fs
        for target in fs.find(
            os.path.join(self._raw_dir.os_path, path),
            (depth if depth > 0 else None) if recursive else 1,
            not files_only,
        ):
            relpath = UPath(os.path.relpath(target, self._raw_dir.os_path))
            if not (isfile := fs.isfile(target)) and relpath == UPath(path):
                # skip folder itself
                continue
            yield RawPathInfo(
                path=relpath.as_posix(),
                is_file=isfile,
                size=fs.size(target) if isfile else fs.du(target),
                access='unpublished',
            )

    def raw_listdir_page(
        self,
        path: str = '',
        *,
        start: int = 0,
        end: int | None = None,
        recursive: bool = False,
        files_only: bool = False,
        depth: int = -1,
        order: Literal['asc', 'desc'] = 'asc',
        group_directories_first: bool = False,
    ) -> RawDirPage:
        """Return a paginated raw directory listing.

        This implementation is optimized for low metadata overhead:

        - performs a single initial probe to determine whether ``path`` is a file
            or directory
        - collects names and file sizes during the walk where cheaply available
        - sorts and pages entirely in memory after collection
        """
        if not is_safe_relative_path(path) or depth == 0:
            return RawDirPage(content=[], total=0)

        os_path = self._full_path(path).as_posix()
        normalised_path = path.rstrip('/')

        path_kind, path_size = self._probe_path_kind_and_size(os_path)
        if path_kind is None:
            return RawDirPage(content=[], total=0)

        entries: list[_RawEntry] = []

        if path_kind == 'file':
            entries.append(
                _RawEntry(
                    path=normalised_path,
                    is_file=True,
                    size=path_size,
                )
            )
        else:
            self._collect_raw_entries(
                os_path,
                normalised_path,
                entries,
                recursive=recursive,
                files_only=files_only,
                depth=depth,
            )

        # Phase 2 onwards is filesystem-agnostic: sort, slice, and materialize.
        entries = self._sort_raw_entries(
            entries,
            order=order,
            group_directories_first=group_directories_first,
        )

        total = len(entries)

        if end is None or end > total:
            end = total
        start = max(start, 0)
        start = min(start, end)

        page = entries[start:end]

        content = [
            RawPathInfo(
                path=entry.path,
                is_file=entry.is_file,
                size=(entry.size or 0) if entry.is_file else 0,
                access='unpublished',
            )
            for entry in page
        ]

        return RawDirPage(content=content, total=total)

    def _probe_path_kind_and_size(
        self,
        os_path: str,
    ) -> tuple[Literal['file', 'dir'] | None, int | None]:
        """Probe a path once to determine existence, type, and file size.

        Returns:
            ('file', size):
                for a regular file
            ('dir', None):
                for a directory
            (None, None):
                if the path does not exist, is inaccessible, or is not a supported
                file/directory entry

        This avoids the extra metadata round trip of calling ``exists()`` and then
        ``isfile()`` separately.
        """
        if isinstance(self._fs, LocalFileSystem):
            try:
                st = os.stat(os_path, follow_symlinks=False)
            except (FileNotFoundError, PermissionError, OSError):
                return None, None

            mode = st.st_mode
            if stat.S_ISREG(mode):
                return 'file', st.st_size
            if stat.S_ISDIR(mode):
                return 'dir', None

            # Skip symlinks, devices, sockets, etc.
            return None, None

        try:
            info = self._fs.info(os_path)
        except Exception:
            return None, None

        entry_type = info.get('type')
        if entry_type == 'file':
            size = info.get('size')
            try:
                size = int(size) if size is not None else 0
            except (TypeError, ValueError):
                size = 0
            return 'file', size

        if entry_type in {'directory', 'dir'}:
            return 'dir', None

        return None, None

    def _collect_raw_entries(
        self,
        os_path: str,
        relative_path: str,
        entries: list[_RawEntry],
        *,
        recursive: bool,
        files_only: bool,
        depth: int,
    ) -> None:
        """Dispatch to the appropriate walk implementation based on filesystem type."""
        if isinstance(self._fs, LocalFileSystem):
            self._collect_local_raw_entries(
                os_path,
                relative_path,
                entries,
                recursive=recursive,
                files_only=files_only,
                depth=depth,
            )
        else:
            self._collect_generic_raw_entries(
                os_path,
                relative_path,
                entries,
                recursive=recursive,
                files_only=files_only,
                depth=depth,
            )

    @staticmethod
    def _collect_local_raw_entries(
        os_path: str,
        relative_path: str,
        entries: list[_RawEntry],
        *,
        recursive: bool,
        files_only: bool,
        depth: int,
    ) -> None:
        """Collect listing entries from a local filesystem using ``os.scandir``.

        This keeps the local path fast by relying on ``DirEntry`` methods, which
        usually avoid extra stat syscalls compared with naive ``os.listdir`` +
        ``os.stat`` loops.

        File sizes are captured during traversal when cheaply available.
        Directory sizes are never computed.
        """
        remaining_depth = (
            depth if recursive and depth > 0 else (None if recursive else 1)
        )

        def _walk(
            current_os_path: str,
            current_relative_path: str,
            current_depth: int | None,
        ) -> None:
            try:
                with os.scandir(current_os_path) as it:
                    for child in it:
                        child_relative_path = (
                            child.name
                            if not current_relative_path
                            else f'{current_relative_path}/{child.name}'
                        )

                        try:
                            if child.is_file(follow_symlinks=False):
                                try:
                                    size = child.stat(follow_symlinks=False).st_size
                                except (OSError, ValueError):
                                    size = None

                                entries.append(
                                    _RawEntry(
                                        path=child_relative_path,
                                        is_file=True,
                                        size=size,
                                    )
                                )
                                continue

                            if not child.is_dir(follow_symlinks=False):
                                # Skip symlinks, broken entries, devices, etc.
                                continue

                        except OSError:
                            continue

                        if not files_only:
                            entries.append(
                                _RawEntry(
                                    path=child_relative_path,
                                    is_file=False,
                                    size=None,
                                )
                            )

                        if current_depth is None or current_depth > 1:
                            _walk(
                                child.path,
                                child_relative_path,
                                None if current_depth is None else current_depth - 1,
                            )

            except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
                return

        _walk(os_path, relative_path, remaining_depth)

    def _collect_generic_raw_entries(
        self,
        os_path: str,
        relative_path: str,
        entries: list[_RawEntry],
        *,
        recursive: bool,
        files_only: bool,
        depth: int,
    ) -> None:
        """Collect listing entries from a generic fsspec filesystem.

        Uses ``ls(detail=True)`` so the backend can provide file/directory type and,
        where available, file size in the same listing response.

        This is intended to work for non-local backends such as NFS-mounted
        implementations exposed via fsspec, S3-like stores, GCS, and similar
        filesystems.

        Directory sizes are never computed.
        """
        remaining_depth = (
            depth if recursive and depth > 0 else (None if recursive else 1)
        )

        def _walk(
            current_os_path: str,
            current_relative_path: str,
            current_depth: int | None,
        ) -> None:
            try:
                # detail=True is required to distinguish files from directories.
                batch = self._fs.ls(current_os_path, detail=True)
            except Exception:
                return

            for item in batch:
                item_path = item.get('name')
                if not item_path:
                    continue

                item_name = item_path.rstrip('/').rsplit('/', 1)[-1]
                child_relative_path = (
                    item_name
                    if not current_relative_path
                    else f'{current_relative_path}/{item_name}'
                )

                item_type = item.get('type')

                if item_type == 'file':
                    size = item.get('size')
                    try:
                        size = int(size) if size is not None else None
                    except (TypeError, ValueError):
                        size = None

                    entries.append(
                        _RawEntry(
                            path=child_relative_path,
                            is_file=True,
                            size=size,
                        )
                    )
                    continue

                if item_type not in {'directory', 'dir'}:
                    continue

                if not files_only:
                    entries.append(
                        _RawEntry(
                            path=child_relative_path,
                            is_file=False,
                            size=None,
                        )
                    )

                if current_depth is None or current_depth > 1:
                    _walk(
                        item_path,
                        child_relative_path,
                        None if current_depth is None else current_depth - 1,
                    )

        _walk(os_path, relative_path, remaining_depth)

    @staticmethod
    def _sort_raw_entries(
        entries: list[_RawEntry],
        *,
        order: Literal['asc', 'desc'],
        group_directories_first: bool,
    ):
        """Sort entries according to requested ordering.

        When ``group_directories_first`` is enabled, directories and files are
        sorted separately and concatenated. Otherwise entries are sorted only by
        path.
        """
        reverse = order == 'desc'

        if not group_directories_first:
            return sorted(entries, key=lambda e: e.path, reverse=reverse)

        dirs = [e for e in entries if not e.is_file]
        files = [e for e in entries if e.is_file]

        dirs.sort(key=lambda e: e.path, reverse=reverse)
        files.sort(key=lambda e: e.path, reverse=reverse)

        return dirs + files

    @contextmanager
    def raw_file(self, file_path: str, *args, **kwargs):
        assert is_safe_relative_path(file_path)

        full_path = self.raw_file_object(file_path).os_path

        try:
            with self._fs.open(full_path, *args, **kwargs) as f:
                yield f
        except (FileNotFoundError, IsADirectoryError) as e:
            raise KeyError(full_path) from e

    def raw_file_size(self, file_path: str) -> int:
        assert is_safe_relative_path(file_path)
        return self._fs.size(self.raw_file_object(file_path).os_path)

    def raw_file_object(self, file_path: str) -> PathObject:
        assert is_safe_relative_path(file_path)
        return self._raw_dir.join_file(file_path)

    def archive_hdf5_location(self, entry_id: str) -> str:
        return self.join_dir('archive').join_file(f'{entry_id}.h5').os_path

    def write_archive(self, entry_id: str, data: Any) -> int:
        """Writes the data as archive file and returns the archive file size."""
        archive_file_object = self._archive_file_object(entry_id)
        try:
            write_archive(archive_file_object.os_path, {entry_id: data})
        except Exception:
            # in case of failure, remove the possible corrupted archive file
            archive_file_object.delete()

            raise

        return archive_file_object.size

    @contextmanager
    def read_archive(self, entry_id: str) -> Iterator[ArchiveReader]:
        try:
            with read_archive(
                self._archive_file_object(entry_id, True).os_path
            ) as archive:
                yield archive
        except FileNotFoundError as e:
            raise KeyError(entry_id) from e

    def _archive_file_object(self, entry_id: str, fallback: bool = False) -> PathObject:
        def versioned_file_name(version_suffix):
            return f'{entry_id}{version_suffix}.msg'

        return _versioned_archive_file_object(
            self._archive_dir, versioned_file_name, fallback=fallback
        )

    def add_rawfiles(
        self,
        target_path: str | PathObject,
        target_dir: str = '',
        cleanup_source_file_and_dir: bool = False,
        updated_files: set[str] | None = None,
        auto_decompress: bool = True,
    ) -> None:
        """Adds files or directories to the upload, optionally decompressing archives.

        If `path` refers to an archive (ZIP, TAR) and `auto_decompress` is True,
        the archive is extracted before merging. Otherwise, archives are treated as single files.

        Args:
            target_path (str): Path to the file or directory to add.
            target_dir (str, optional): Relative path within the upload's raw directory.
                Defaults to "".
            cleanup_source_file_and_dir (bool, optional): If True, deletes the source path
                and its parent directory after processing. Defaults to False.
            updated_files (set[str], optional): Set to track paths of files updated or added.
            auto_decompress (bool, optional): If True, automatically decompress archives.
                Defaults to True.

        Raises:
            AssertionError: If file format is unrecognized or merge conflicts occur.
        """
        assert not self.is_frozen
        if isinstance(target_path, str):
            assert self._fs.exists(target_path), f'{target_path} does not exist'
            path = target_path
            target_fs = self._fs
            location = target_path
        else:
            assert target_path.exists(), f'{target_path} does not exist'
            path = target_path.os_path
            target_fs = target_path._fs
            location = target_path.location
        assert is_safe_relative_path(target_dir)

        archive_format = (
            get_compression_format(path, fs=target_fs) if auto_decompress else None
        )
        if archive_format == 'error':
            raise ValueError('Bad archive.')

        dst_root = os.path.join(self._raw_dir.os_path, target_dir)

        try:
            if archive_format == 'tar':
                with target_fs.open(location, 'rb') as f:
                    with tarfile.open(fileobj=f, mode='r|*') as tar:
                        for member in tar:
                            rel_path = member.name
                            if not is_safe_relative_path(rel_path):
                                continue
                            dst_path = os.path.join(dst_root, rel_path)
                            if member.isfile():
                                if self._fs.exists(dst_path) and not self._fs.isfile(
                                    dst_path
                                ):
                                    raise ValueError(
                                        f'Cannot merge a file with a directory or vice versa: {rel_path}.'
                                    )
                                self._fs.mkdirs(
                                    os.path.dirname(dst_path), exist_ok=True
                                )
                                with (
                                    tar.extractfile(member) as src_f,
                                    self._fs.open(dst_path, 'wb') as dst_f,
                                ):
                                    shutil.copyfileobj(src_f, dst_f)

                                if updated_files is not None:
                                    updated_files.add(
                                        os.path.join(target_dir, rel_path)
                                    )
                            elif member.isdir():
                                if self._fs.exists(dst_path) and not self._fs.isdir(
                                    dst_path
                                ):
                                    raise ValueError(
                                        f'Cannot merge a file with a directory or vice versa: {rel_path}.'
                                    )
                                self._fs.mkdirs(dst_path, True)
            else:

                @contextmanager
                def open_archive() -> Iterator[tuple[str, str, AbstractFileSystem]]:
                    if archive_format == 'zip':
                        with FSUtility.open_archive(path, fs=target_fs) as _fs:
                            yield '', '', _fs
                    else:
                        yield (
                            path,
                            os.path.dirname(path) if self._fs.isfile(path) else path,
                            self._fs,
                        )

                with open_archive() as pack:
                    src_root, src_parent, src_fs = pack
                    for item, info in src_fs.find(src_root, None, True, True).items():
                        rel_path = os.path.relpath(item, src_parent)
                        dst_path = os.path.join(dst_root, rel_path)
                        if info['type'] == 'file':
                            if self._fs.exists(dst_path) and not self._fs.isfile(
                                dst_path
                            ):
                                raise ValueError(
                                    f'Cannot merge a file with a directory or vice versa: {rel_path}.'
                                )
                            if src_fs is not self._fs or item != dst_path:
                                self._fs.mkdirs(
                                    os.path.dirname(dst_path), exist_ok=True
                                )
                                with (
                                    src_fs.open(item) as src_f,
                                    self._fs.open(dst_path, 'wb') as dst_f,
                                ):
                                    shutil.copyfileobj(src_f, dst_f)

                            if updated_files is not None:
                                updated_files.add(os.path.join(target_dir, rel_path))
                        elif info['type'] == 'directory':
                            if self._fs.exists(dst_path) and not self._fs.isdir(
                                dst_path
                            ):
                                raise ValueError(
                                    f'Cannot merge a file with a directory or vice versa: {rel_path}.'
                                )
                            self._fs.mkdirs(dst_path, True)
        finally:
            if cleanup_source_file_and_dir:
                self._fs.rm(path, recursive=True)
                if self._fs.exists(parent := os.path.dirname(path)) and not self._fs.ls(
                    parent, False
                ):
                    self._fs.rm(parent, recursive=True)

    def delete_rawfiles(self, path, updated_files: set[str] | None = None):
        assert is_safe_relative_path(path)
        raw_os_path = UPath(self.os_path) / 'raw'
        os_path = raw_os_path / path
        if not self._fs.exists(os_path):
            return
        if updated_files is not None:
            updated_files.update(
                os.path.relpath(target, raw_os_path.as_posix())
                for target in self._fs.find(os_path)
            )
        self._fs.rm(os_path, recursive=True)
        if raw_os_path == os_path:
            # Special case - deleting everything, i.e. the entire raw folder. Need to recreate.
            self._fs.makedirs(os_path)

    def copy_or_move(
        self,
        src: str,
        dest: str,
        copy_or_move: Literal['copy', 'move'],
        updated_files: set[str] | None = None,
    ):
        """
        Copies or moves a raw file or folder from `src` to `dest`, both given as
        paths relative to the `raw` folder. Fails if `dest` already exists, or if
        `src` and `dest` contain glob wildcard characters ('*', '?', '[' or ']').
        Does nothing if `src` does not exist.

        If `updated_files` is provided, it is populated with the paths (relative
        to the `raw` folder) of all files affected by the operation: for a move,
        both the source and destination paths of every file that was moved; for a
        rename of a single file, both the old and new path (even when unchanged,
        the destination is added).
        """
        assert is_safe_relative_path(src)
        assert is_safe_relative_path(dest)
        if has_glob_wildcards(src) or has_glob_wildcards(dest):
            # `self._fs.cp`/`self._fs.mv` (fsspec) interpret '*', '?' and '[...]'
            # in paths as glob patterns rather than literal characters, which
            # would silently copy/move the wrong files or blow up with a
            # RecursionError.
            raise ValueError(
                'File and folder names must not contain the wildcard characters '
                "'*', '?', '[' or ']'."
            )
        src_full_path = os.path.join(self._raw_dir.os_path, src)
        dest_full_path = os.path.join(self._raw_dir.os_path, dest)
        src_is_folder = self._fs.isdir(src_full_path)
        mode = copy_or_move.lower()
        if not self._fs.exists(src_full_path):
            return
        if dest == src or dest.startswith(f'{src}/'):
            # Prevent moving/copying a folder into itself or one of its subfolders
            raise ValueError(
                f"Cannot {mode} '{src}' into itself or one of its own subfolders "
                f"('{dest}')."
            )
        if self._fs.exists(dest_full_path):
            raise ValueError(
                f'A {"folder" if src_is_folder else "file"} with the same name already exists.'
            )

        if src_is_folder and updated_files is not None and mode == 'move':
            # Recursively add the paths of all files currently inside the source folder to `updated_files` BEFORE the folder is moved.
            updated_files.update(
                x.path for x in self.raw_listdir(src, recursive=True, files_only=True)
            )
        if mode == 'move':
            self._fs.mv(src_full_path, dest_full_path, recursive=src_is_folder)
        elif mode == 'copy':
            self._fs.cp(src_full_path, dest_full_path, recursive=src_is_folder)
        else:
            raise ValueError('Invalid operation. Must be "copy" or "move".')
        if updated_files is not None:
            if src_is_folder:
                # Recursively add the paths of all files currently inside the destination folder to `updated_files` AFTER the folder is moved.
                updated_files.update(
                    x.path
                    for x in self.raw_listdir(dest, recursive=True, files_only=True)
                )
            else:
                updated_files.add(dest)
                # if both the new and old name are the same then no new entry will be
                # added to the set. but if different, we add the old one so that later on
                # when self.matchall is called in data.py, the old filename is removed
                # from mongo database
                if mode == 'move':
                    updated_files.add(src)

    def metadata_file_cached(self, path_dir: str = ''):
        """
        Gets the content of the metadata file located in the directory defined by `path_dir`.
        The `path_dir` should be relative to the `raw` folder.
        """

        def json_load(_f):
            return json.load(f)

        def yaml_load(_f):
            return yaml.safe_load(_f)

        def dummy_load(_):
            return {}

        loader = {'json': json_load, 'yaml': yaml_load, 'yml': yaml_load}

        base = UPath(self._raw_dir.os_path) / path_dir
        for ext in config.process.metadata_file_extensions:
            if not self._fs.isfile(
                full_path := base / f'{config.process.metadata_file_name}.{ext}'
            ):
                continue
            try:
                with self._fs.open(full_path.as_posix()) as f:
                    return loader.get(ext, dummy_load)(f)
            except Exception as e:
                # ignore the file contents if the file is not parsable, just warn.
                self.logger.warn(
                    'could not parse nomad.yaml/json', path=path_dir, exc_info=e
                )
        return {}

    @property
    def is_frozen(self) -> bool:
        """Returns True if this upload is already *bagged*."""
        return self._frozen_file.exists()

    def pack(
        self,
        entries: list[datamodel.EntryMetadata],
        with_embargo: bool,
        create: bool = True,
        include_raw: bool = True,
        include_archive: bool = True,
    ) -> None:
        """
        Packs raw and/or archive files, to create the contents in the public file area.
        This method should be called when an upload is published, or when a
        published upload has been reprocessed.

        If the public upload files directory does not exist, it will be created.
        If the target archive file or raw file zip exists, they will be overwritten.
        If an archive file or raw file zip with the wrong access exists, they will be deleted.
        This is potentially a long running operation.

        Arguments:
            entries: A list of EntryMetadata to pack in the archive files
            with_embargo: If the upload is embargoed (determines which "access" is used in
                the file names)
            create: if the public upload files directory should be created. True by default.
            include_raw: determines if the raw data should be packed. True by default.
            include_archive: determines of the archive data should be packed. True by default.
        """
        self.logger.info('started to pack upload')

        # freeze the upload
        assert not self.is_frozen, 'Cannot pack an upload that is packed, or packing.'
        with self._fs.open(self._frozen_file.os_path, 'w') as f:
            f.write('frozen')

        # Check embargo flag consistency
        for entry in entries:
            assert entry.with_embargo == with_embargo

        access: Access = 'restricted' if with_embargo else 'public'
        other_access: Access = 'public' if with_embargo else 'restricted'

        # Get or create a target dir in the public area
        target_dir = DirectoryObject(
            PublicUploadFiles.base_folder_for(self.upload_id), create=create
        )
        if os.listdir(target_dir.os_path):
            # Target dir contains files. Check that the target access is identical
            assert PublicUploadFiles(self.upload_id).access == access, (
                'Inconsistent access'
            )

        pack_fs = choose_pack_fs()
        delete_ready_marker(target_dir.os_path)

        # zip archives
        if include_archive:
            with utils.timer(self.logger, 'packed msgpack archive') as log_data:
                log_data.update(
                    number_of_entries=self._pack_archive_files(
                        target_dir,
                        list(entry.entry_id for entry in entries),
                        access,
                        pack_fs,
                    )
                )

        # zip raw files
        if include_raw:
            with utils.timer(self.logger, 'packed raw files'):
                self._pack_raw_files(target_dir, access, pack_fs)

        delete_access_artifacts(
            target_dir.os_path,
            other_access,
            include_raw=include_raw,
            include_archive=include_archive,
        )
        complete_published_write(target_dir.os_path, self.upload_id, access)

    def _pack_archive_files(
        self,
        target_dir: DirectoryObject,
        entries: list[str],
        access: str,
        fs: AbstractFileSystem,
    ):
        def create_iterator():
            for item in entries:
                fo = self._archive_file_object(item)
                yield item, fo if fo.exists() else None

        try:
            combine_archive(target_dir.msg_fp(access, fs=fs), create_iterator(), fs=fs)

            write_h5 = any(
                [
                    self.join_dir('archive').join_file(f'{entry_id}.h5').exists()
                    for entry_id in entries
                ]
            )
            if write_h5:
                with FSUtility.open_h5(
                    target_dir.h5_fp(access, fs=fs).os_path, 'w', fs=fs
                ) as hdf5_target:
                    for entry_id in entries:
                        with File(
                            self.archive_hdf5_location(entry_id), 'a'
                        ) as hdf5_source:
                            group = hdf5_target.create_group(entry_id)
                            for key in hdf5_source.keys():
                                hdf5_source.copy(key, group)
        except Exception as e:
            self.logger.error('exception during packing archives', exc_info=e)
            raise

        return len(entries)

    def _pack_raw_files(
        self, target_dir: DirectoryObject, access: str, fs: AbstractFileSystem
    ):
        try:
            with FSUtility.open_archive(
                target_dir.zip_fp(access, fs=fs).os_path, 'w', fs=fs
            ) as zip_fs:
                for path_info in self.raw_listdir(recursive=True):
                    basename = os.path.basename(path_info.path)
                    # TODO remove extra handling of POTCAR files once processed uploads are published.
                    if basename.startswith('POTCAR'):
                        if not basename.endswith('.stripped'):
                            continue  # Skip the unstripped POTCAR files when publishing
                        if basename.endswith('.stripped.stripped'):
                            continue  # Skip redundantly stripped POTCAR files (created due to bug #979) when publishing
                    zip_fs.put_file(
                        self._raw_dir.join_file(path_info.path).os_path, path_info.path
                    )
        except Exception as e:
            self.logger.error('exception during packing raw files', exc_info=e)
            raise

    def entry_files(
        self, mainfile: str, with_mainfile: bool = True, with_cutoff: bool = True
    ) -> Iterable[str]:
        """
        Returns all the auxfiles and mainfile for a given mainfile. This implements
        nomad's logic about what is part of an entry and what not. The mainfile
        is the first element, the rest is sorted.
        Arguments:
            mainfile: The mainfile path relative to upload
            with_mainfile: Do include the mainfile, default is True
        """
        mainfile_object = self._raw_dir.join_file(mainfile)
        if not mainfile_object.exists():
            raise KeyError(mainfile)

        mainfile_basename = os.path.basename(mainfile)
        entry_dir = os.path.dirname(mainfile_object.os_path)
        entry_relative_dir = entry_dir[len(self._raw_dir.os_path) + 1 :]

        file_count = 0
        aux_files: list[str] = []
        dir_elements = os.listdir(entry_dir)
        dir_elements.sort()
        for dir_element in dir_elements:
            if dir_element != mainfile_basename and os.path.isfile(
                os.path.join(entry_dir, dir_element)
            ):
                aux_files.append(os.path.join(entry_relative_dir, dir_element))
                file_count += 1

            if with_cutoff and file_count > config.process.auxfile_cutoff:
                # If there are too many of them, its probably just a directory with lots of
                # mainfiles/entries. In this case it does not make any sense to provide thousands of
                # aux files.
                break

        aux_files = sorted(aux_files)

        if with_mainfile:
            return [mainfile] + aux_files
        else:
            return aux_files

    def entry_hash(self, mainfile: str, mainfile_key: str) -> str:
        """
        Calculates a hash for the given entry based on file contents and aux file contents.
        Arguments:
            mainfile: The mainfile path relative to the upload that identifies the entry in
                the folder structure.
            mainfile_key: The mainfile_key of the entry (if any)
        Returns:
            The calculated hash
        Raises:
            KeyError: If the mainfile does not exist.
        """
        hash = hashlib.sha512()
        for filepath in self.entry_files(mainfile):
            with self._fs.open(self._raw_dir.join_file(filepath).os_path) as f:
                for data in iter(lambda: f.read(65536), b''):
                    hash.update(data)
        if mainfile_key:
            hash.update(mainfile_key.encode('utf8'))
        return utils.make_websave(hash)

    def files_to_bundle(
        self, export_settings: BundleExportSettings
    ) -> Iterable[FileSource]:
        # Defines files for upload bundles of staging uploads.
        if export_settings.include_raw_files:
            yield DiskFileSource(self.os_path, 'raw')
        if export_settings.include_archive_files:
            yield DiskFileSource(self.os_path, 'archive')

    @classmethod
    def files_from_bundle(
        cls,
        bundle_file_source: BrowsableFileSource,
        import_settings: BundleImportSettings,
    ) -> Iterable[FileSource]:
        # Files to import for a staging upload
        if import_settings.include_raw_files:
            yield bundle_file_source.child('raw')
        if import_settings.include_archive_files:
            yield bundle_file_source.child('archive')
        if import_settings.include_bundle_info:
            yield bundle_file_source.child(bundle_info_filename)


class ZipRawPathReader(RawPathReader):
    """Published raw paths share ``PublicUploadFiles`` request-scoped ZIP state.

    Exists/isfile/open/mime_type delegate to ``PublicUploadFiles``. ``close()``
    is a no-op so download streams can close the reader after
    ``PublicUploadFiles.close()`` without double-closing the ZIP.
    """

    upload_files: PublicUploadFiles


def clear_index_caches() -> None:
    """Drop process-level artifact-probe caches. Intended for tests."""
    clear_ready_cache()


class PublicUploadFiles(UploadFiles):
    def __init__(
        self,
        upload_id: str,
        create: bool = False,
        *,
        fs: AbstractFileSystem | None = None,
    ):
        super().__init__(upload_id, create, fs=fs)
        self._zip_index: ZipMemberIndex | None = None

    @classmethod
    def _file_area(cls):
        return UPath(config.fs.public)

    @property
    def external_os_path(self):
        if not config.fs.public_external:
            return self.os_path

        return self.os_path.replace(config.fs.public, config.fs.public_external)

    @cached_property
    def storage_fs(self) -> AbstractFileSystem:
        """The single backend from which this published upload is read."""
        return choose_read_fs(self.os_path)

    @cached_property
    def access(self) -> Access:
        """
        Which "access" is used, either 'public' (uploads without embargo) or 'restricted'
        (uploads with embargo). This is reflected in the names of the files holding the
        raw data and the archive data. The reason for this is so that it should be easy to
        see, by just looking at the files, if a published upload is embargoed or not.

        The access is determined by inspecting which files exist/contain data. If both
        public and restricted files exist/contain data, or if neither exists/contain data,
        a KeyError will be thrown (this should not happen if the upload is correctly packed).
        The read filesystem is tried first; if it has no artifacts, the write
        destination is inspected so a dual-write import can complete before the
        copy to remote. The inspection of the files is only done on the first
        call, and the cached result is used in subsequent calls. The only way to
        change the access is to call :func:`re_pack`.
        """
        return detect_published_access(self.os_path, self.storage_fs, choose_pack_fs())

    def raw_zip_file_object(self, access: str = None) -> PathObject:
        """
        Gets the raw zip file, either public or restricted, depending on which one is used.
        If both public and restricted files exist, or if none of them exist, a KeyError will
        be thrown.
        """
        return self.zip_fp(access or self.access, fs=self.storage_fs)

    def msg_fp(
        self,
        access: str,
        fallback: bool = False,
        *,
        fs: AbstractFileSystem | None = None,
    ):
        return super().msg_fp(access, fallback=fallback, fs=fs or self.storage_fs)

    def h5_fp(self, access: str, *, fs: AbstractFileSystem | None = None):
        return super().h5_fp(access, fs=fs or self.storage_fs)

    @contextmanager
    def _zip_fs(self, mode: Literal['a', 'w', 'r'] = 'r'):
        if mode != 'r':
            self.close()
        with FSUtility.open_archive(
            self.raw_zip_file_object().os_path, mode, fs=self.storage_fs
        ) as zip_fs:
            yield zip_fs

    def _open_raw_zip_fileobj(self) -> IO[bytes]:
        zip_obj = self.raw_zip_file_object()
        fs = self.storage_fs
        location = zip_obj.location
        if isinstance(fs, LocalFileSystem):
            return fs.open(location, 'rb')
        return cast(IO[bytes], RangeTailFile.from_filesystem(fs, location))

    def _open_zip_member_fileobj(self) -> IO[bytes]:
        """Open the ZIP object for a member read without prefetching the tail."""
        zip_obj = self.raw_zip_file_object()
        return self.storage_fs.open(zip_obj.location, 'rb')

    def _parse_raw_zip_index(self) -> ZipMemberIndex:
        fileobj = self._open_raw_zip_fileobj()
        try:
            zip_file = zipfile.ZipFile(fileobj, 'r')
        except Exception:
            with suppress(Exception):
                fileobj.close()
            raise
        try:
            return ZipMemberIndex.from_zipfile(zip_file)
        finally:
            with suppress(Exception):
                zip_file.close()
            if not getattr(fileobj, 'closed', False):
                with suppress(Exception):
                    fileobj.close()

    def _index_store(self, kind: str) -> IndexDiskStore:
        settings = config.fs.public_fs.metadata_cache
        directory = settings.directory or os.path.join(
            config.fs.local_tmp, 'nomad-zip-index'
        )
        return IndexDiskStore(directory, settings.max_disk_mb * 1024 * 1024, kind)

    def _ensure_raw_zip(self) -> ZipMemberIndex:
        if self._zip_index is not None:
            return self._zip_index
        settings = config.fs.public_fs.metadata_cache
        fs = self.storage_fs
        location = self.raw_zip_file_object().location
        if not settings.is_enabled(config.fs.public_fs.protocol):
            zip_obj = self.raw_zip_file_object()
            if not zip_obj.exists():
                self._zip_index = ZipMemberIndex.empty()
                return self._zip_index
            self._zip_index = self._parse_raw_zip_index()
            return self._zip_index
        try:
            identity = object_identity(fs, location)
        except FileNotFoundError:
            self._zip_index = ZipMemberIndex.empty()
            return self._zip_index
        store = self._index_store('zip')
        index: ZipMemberIndex | None = None
        data = store.load(identity)
        if data is not None:
            try:
                index = ZipMemberIndex.from_bytes(data)
            except Exception:
                logger.warning(
                    'failed to decode cached ZIP index for %s',
                    location,
                    exc_info=True,
                )
                store.discard(identity)
        if index is None:
            index = self._parse_raw_zip_index()
            store.store(identity, index.to_bytes())
        self._zip_index = index
        return index

    def close(self):
        self._zip_index = None

    def raw_path_reader(self, path: str) -> RawPathReader:
        return ZipRawPathReader(self, path)

    def archive_hdf5_location(self, entry_id: str) -> str:
        fp = self.h5_fp(self.access)
        if not fp.exists():
            raise FileNotFoundError()

        return fp.os_path

    @contextmanager
    def _open_msg_file(self) -> Iterator[ArchiveReader]:
        msg_file = self.msg_fp(self.access, fallback=True)
        # ``read_archive`` accepts a seekable file object, allowing the selected
        # local backend to remain local even when remote public storage is enabled.
        with FSUtility.open(msg_file.os_path, fs=self.storage_fs) as file_obj:
            with read_archive(file_obj) as archive:
                yield archive

    def to_staging(
        self, create: bool = False, include_archive: bool = False
    ) -> StagingUploadFiles | None:
        if StagingUploadFiles.exists_for(self.upload_id):
            if create:
                raise FileExistsError('Staging upload does already exist')
            return StagingUploadFiles(self.upload_id)

        if not create:
            return None

        staging_upload_files = StagingUploadFiles(self.upload_id, create=True)
        if (raw_zip_file := self.raw_zip_file_object()).exists():
            staging_upload_files.add_rawfiles(raw_zip_file)

        if include_archive:
            with suppress(FileNotFoundError):
                with self._open_msg_file() as archive:
                    for entry_id, data in archive.items():
                        target = data if isinstance(data, LazyItem) else to_json(data)
                        staging_upload_files.write_archive(entry_id.strip(), target)

                with FSUtility.open_h5(
                    self.archive_hdf5_location(''), fs=self.storage_fs
                ) as hdf5_source:
                    for entry_id, data in hdf5_source.items():
                        with File(
                            staging_upload_files.archive_hdf5_location(entry_id), 'w'
                        ) as hdf5_target:
                            for key in data.keys():
                                data.copy(key, hdf5_target)

        return staging_upload_files

    def is_empty(self) -> bool:
        return self._ensure_raw_zip().is_empty()

    def delete(self) -> None:
        """Delete every configured copy of this published upload.

        Published files can be read from a remote filesystem with a local fallback.
        Deletion must therefore target both locations independently; in particular, a
        remote error must not leave a readable local fallback behind.
        """
        self.close()
        errors: list[Exception] = []
        try:
            delete_ready_marker(self.os_path)
        except Exception as exc:
            errors.append(exc)

        def delete_directory(fs: AbstractFileSystem, cleanup_prefix: bool) -> None:
            PathObject(self.os_path, fs=fs).delete()

            # Keep the local prefix cleanup performed by DirectoryObject.delete, but
            # make it safe when a retry finds that the upload/prefix is already gone.
            if cleanup_prefix and config.fs.prefix_size > 0:
                parent = os.path.dirname(self.os_path)
                if fs.exists(parent) and not fs.ls(parent, detail=False):
                    fs.rm(parent, recursive=True)

        public_fs = config.fs.public_fs
        if public_fs.protocol is not None:
            try:
                delete_directory(public_fs.target_fs, cleanup_prefix=False)
            except Exception as exc:
                errors.append(exc)

        try:
            delete_directory(self._fs, cleanup_prefix=True)
        except Exception as exc:
            errors.append(exc)

        if len(errors) == 1:
            raise errors[0]
        if errors:
            # Python 3.10 is supported, so retain both failures through exception
            # chaining instead of using the Python 3.11 ExceptionGroup type.
            try:
                raise errors[1]
            except Exception as local_error:
                raise errors[0] from local_error

    def raw_exists(self, path: str) -> bool:
        if not is_safe_relative_path(path):
            return False
        return self._ensure_raw_zip().exists(path)

    def raw_isfile(self, path: str) -> bool:
        if not is_safe_relative_path(path):
            return False
        return self._ensure_raw_zip().isfile(path)

    def raw_listdir(
        self,
        path: str = '',
        recursive: bool = False,
        files_only: bool = False,
        depth: int = -1,
    ) -> Iterable[RawPathInfo]:
        if not is_safe_relative_path(path) or depth == 0:
            return

        index = self._ensure_raw_zip()
        for member in index.listdir(
            path, recursive=recursive, files_only=files_only, depth=depth
        ):
            yield RawPathInfo(
                path=member.path,
                is_file=not member.is_dir,
                size=index.size(member.path),
                access=self.access,
            )

    @contextmanager
    def raw_file(self, file_path: str, *args, **kwargs):
        assert is_safe_relative_path(file_path)
        mode = kwargs.pop('mode', None)
        if len(args) > 0:
            mode = args[0]
        mode = mode or 'rb'
        encoding = kwargs.pop('encoding', None)

        index = self._ensure_raw_zip()
        member = index.get(file_path)
        if member is None or member.is_dir or not member.zip_name:
            raise KeyError(file_path)

        fileobj = self._open_zip_member_fileobj()
        try:
            member_file = open_zip_member(fileobj, member)
        except Exception as e:
            if not getattr(fileobj, 'closed', False):
                with suppress(Exception):
                    fileobj.close()
            if isinstance(e, (FileNotFoundError, IsADirectoryError, KeyError)):
                raise KeyError(file_path) from e
            if isinstance(e, zipfile.BadZipFile):
                raise KeyError(file_path) from e
            raise

        try:
            if 't' in mode:
                yield io.TextIOWrapper(member_file, encoding=encoding)
            else:
                yield member_file
        finally:
            if not member_file.closed:
                member_file.close()

    def raw_file_size(self, file_path: str) -> int:
        assert is_safe_relative_path(file_path)

        index = self._ensure_raw_zip()
        if not index.isfile(file_path):
            raise KeyError(file_path)
        if file_size := index.size(file_path):
            return file_size
        raise KeyError(file_path)

    @contextmanager
    def read_archive(self, entry_id: str) -> Iterator[ArchiveReader]:
        try:
            with self._open_msg_file() as archive:
                if entry_id not in archive:
                    raise KeyError(entry_id)
                yield archive
        except FileNotFoundError as e:
            raise KeyError(entry_id) from e

    def re_pack(self, with_embargo: bool) -> None:
        """
        Repacks the files when changing the embargo flag on the upload. That is: when lifting the
        embargo the file names of the raw zip file and the archive file change from containing
        the keyword "restricted" to "public". Adding embargo to a non-embargoed published
        upload is also supported, but only admins should be allowed to do this. The existing
        files are just renamed, so this should be a rather quick operation. The upload must
        be correctly packed (i.e. there cannot be non-empty public and restricted files
        at the same time).
        """
        if (self.access == 'restricted') == with_embargo:
            return

        self.close()

        old_access = self.access
        new_access: Access = 'restricted' if with_embargo else 'public'
        delete_ready_marker(self.os_path)
        rename_published_artifacts(self.os_path, old_access, new_access)
        complete_published_write(self.os_path, self.upload_id, new_access)

        self.__dict__.pop('access', None)
        self.__dict__.pop('storage_fs', None)

    def files_to_bundle(
        self, export_settings: BundleExportSettings
    ) -> Iterable[FileSource]:
        if export_settings.include_raw_files:
            yield _disk_file_source(self.raw_zip_file_object())

        if export_settings.include_archive_files:
            for artifact in (self.msg_fp(self.access), self.h5_fp(self.access)):
                if artifact.exists():
                    yield _disk_file_source(artifact)

    @classmethod
    def files_from_bundle(
        cls,
        bundle_file_source: BrowsableFileSource,
        import_settings: BundleImportSettings,
    ) -> Iterable[FileSource]:
        for filename in bundle_file_source.find(''):
            if filename.startswith('raw-') and import_settings.include_raw_files:
                yield bundle_file_source.child(filename)
            if (
                filename.startswith('archive-')
                and import_settings.include_archive_files
            ):
                yield bundle_file_source.child(filename)
            if filename == bundle_info_filename and import_settings.include_bundle_info:
                yield bundle_file_source.child(filename)
