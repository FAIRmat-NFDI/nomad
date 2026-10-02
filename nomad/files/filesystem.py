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

"""Filesystem primitives used by NOMAD upload storage."""

from __future__ import annotations

import io
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import IO, Literal

from fsspec import AbstractFileSystem, filesystem
from fsspec.implementations.cached import SimpleCacheFileSystem
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.tar import TarFileSystem
from fsspec.implementations.zip import ZipFileSystem
from h5py import File
from pathvalidate import sanitize_filename
from s3fs.core import S3File
from s3fs.utils import FileExpired
from upath import UPath

from nomad.config import config

bundle_info_filename = 'bundle_info.json'

logger = logging.getLogger('nomad.files')

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


def _remote_read_open_kwargs(
    fs: AbstractFileSystem,
    *,
    size: int | None = None,
    block_size: int | None = None,
) -> dict[str, int]:
    """Kwargs for a binary open: ``block_size`` always, ``size`` only on remote fs."""
    kwargs: dict[str, int] = {}
    if block_size is not None:
        kwargs['block_size'] = block_size
    if size is not None and not isinstance(fs, LocalFileSystem):
        kwargs['size'] = size
    return kwargs


def _apply_if_match(
    fs: AbstractFileSystem,
    file_obj: IO[bytes],
    etag: str | None,
    *,
    size: int | None = None,
) -> None:
    if etag is None or isinstance(fs, LocalFileSystem):
        return
    if isinstance(file_obj, S3File) and size is not None:
        details = dict(file_obj._details or {})
        details.setdefault('name', file_obj.path)
        details.setdefault('size', size)
        details.setdefault('ETag', etag)
        details.setdefault('type', 'file')
        file_obj.details = details
    # s3fs >= 2026.9 uses read_kw for reads; older versions use req_kw.
    for attribute in ('read_kw', 'req_kw'):
        request_kwargs = getattr(file_obj, attribute, None)
        if isinstance(request_kwargs, dict):
            request_kwargs['IfMatch'] = etag
            break


def _is_stale_object_error(error: Exception) -> bool:
    """Recognize S3 identity mismatches without retrying unrelated I/O errors."""
    if isinstance(error, FileExpired):
        return True
    # s3fs translates ClientError to OSError and preserves it as the cause.
    response = getattr(error.__cause__ or error, 'response', {})
    return response.get('Error', {}).get('Code') in {'PreconditionFailed', '412'}


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
        block_size: int | None = None,
        size: int | None = None,
        if_match: str | None = None,
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

        open_kwargs = _remote_read_open_kwargs(fs, size=size, block_size=block_size)
        with cached_fs.open(location, f'{mode}b', **open_kwargs) as file:
            _apply_if_match(fs, file, if_match, size=size)
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


def _versioned_archive_file_objects(
    target_dir: DirectoryObject,
    file_name: Callable[[str], str],
    *,
    fs: AbstractFileSystem | None = None,
) -> list[PathObject]:
    """PathObjects for each archive version suffix, without probing ``exists``."""
    suffixes = config.fs.archive_version_suffix
    fs = fs or target_dir._fs
    actual_dir = DirectoryObject(target_dir.os_path, fs=fs)
    if not isinstance(suffixes, list):
        suffixes = [suffixes]
    if len(suffixes) <= 1:
        suffix = f'-{suffixes[0]}' if suffixes[0] else ''
        return [actual_dir.join_file(file_name(suffix))]
    return [actual_dir.join_file(file_name(f'-{suffix}')) for suffix in suffixes]


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
    candidates = _versioned_archive_file_objects(target_dir, file_name, fs=fs)
    if not fallback or len(candidates) == 1:
        return candidates[0]
    for current_file in candidates:
        if current_file.exists():
            return current_file
    return candidates[0]
