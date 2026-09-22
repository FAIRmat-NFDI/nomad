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

"""ZIP tail prefetch and member index for published raw archives.

Published raw ZIPs live on remote object storage. ``RangeTailFile`` serves the
ZIP tail from one Range GET while reporting the real file size so ``zipfile``
can parse the central directory. ``ZipMemberIndex`` is the immutable listing
built from ``infolist()`` including implicit parent directories and ``du``-style
directory sizes.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import struct
import tempfile
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from types import MappingProxyType
from typing import IO

import msgpack
from fsspec import AbstractFileSystem

from nomad.config import config

logger = logging.getLogger(__name__)

_SAFE_ETAG_RE = re.compile(r'[^A-Za-z0-9._-]')
_STRUCT_FILE_HEADER = '<4s2B4HL2L2H'
_SIZE_FILE_HEADER = struct.calcsize(_STRUCT_FILE_HEADER)
_STRING_FILE_HEADER = b'PK\x03\x04'


def zip_tail_prefetch_bytes() -> int:
    """Return the configured ZIP tail prefetch size in bytes."""
    prefetch_kb = config.fs.public_fs.zip_tail_prefetch_kb
    return max(0, int(prefetch_kb)) * 1024


def object_identity(fs: AbstractFileSystem, path: str) -> tuple[str, str, int]:
    """Return ``(path, etag, size)`` for a ZIP object from one ``info()`` call."""
    try:
        info = fs.info(path, refresh=True)
    except TypeError:
        info = fs.info(path)

    size = int(info['size'])

    etag = info.get('ETag') or info.get('etag')
    if etag is None:
        last_modified = info.get('mtime') or info.get('LastModified')
        etag = str(last_modified) if last_modified is not None else str(size)
    else:
        etag = str(etag).strip('"')
    return (path, etag, size)


def normalize_zip_member_path(path: str) -> str:
    """Normalize a ZIP member or listing path to a relative POSIX path.

    Root is the empty string. Trailing slashes are stripped so directory lookups
    match both explicit directory members and implicit prefixes.
    """
    if not path:
        return ''
    normalized = path.replace('\\', '/').strip('/')
    return '' if normalized in {'.', ''} else normalized


class RangeTailFile(io.RawIOBase):
    """Seekable file-like that reports real size ``L`` and serves a RAM tail.

    ZIP EOCD offsets are absolute from byte 0. Wrapping only the tail in
    ``BytesIO`` would make ``seek(0, SEEK_END)`` return ``len(tail)`` and break
    central-directory parsing. Reads in ``[tail_start, L)`` come from memory;
    earlier reads fall back to fsspec (``cat_file`` / ``read_block`` / open+seek).
    """

    def __init__(
        self,
        fs: AbstractFileSystem,
        path: str,
        size: int,
        tail: bytes,
        tail_start: int,
    ):
        super().__init__()
        if size < 0:
            raise ValueError('size must be non-negative')
        if tail_start < 0 or tail_start > size:
            raise ValueError('tail_start must be in [0, size]')
        if len(tail) != size - tail_start:
            raise ValueError('tail length must equal size - tail_start')
        self.name = path
        self._fs = fs
        self._path = path
        self._size = size
        self._tail = tail
        self._tail_start = tail_start
        self._pos = 0
        self._fallback_fp: IO[bytes] | None = None

    @classmethod
    def from_filesystem(
        cls,
        fs: AbstractFileSystem,
        path: str,
        prefetch_bytes: int | None = None,
    ) -> RangeTailFile:
        size = cls._file_size(fs, path)
        if prefetch_bytes is None:
            prefetch_bytes = zip_tail_prefetch_bytes()
        prefetch_bytes = min(max(0, prefetch_bytes), size)
        tail_start = size - prefetch_bytes
        if prefetch_bytes == 0:
            tail = b''
        else:
            tail, fallback_fp = cls._fetch_range(fs, path, tail_start, size)
            if fallback_fp is not None:
                with suppress(Exception):
                    fallback_fp.close()
            if len(tail) == size and tail_start > 0:
                tail = tail[tail_start:]
            elif len(tail) > prefetch_bytes:
                tail = tail[-prefetch_bytes:]
            elif len(tail) < prefetch_bytes:
                tail_start = size - len(tail)
        return cls(fs, path, size, tail, tail_start)

    @staticmethod
    def _file_size(fs: AbstractFileSystem, path: str) -> int:
        info = None
        if hasattr(fs, 'info'):
            with suppress(Exception):
                info = fs.info(path)
        if isinstance(info, Mapping) and 'size' in info:
            return int(info['size'])
        return int(fs.size(path))

    @staticmethod
    def _fetch_range(
        fs: AbstractFileSystem,
        path: str,
        start: int,
        end: int,
        fallback_fp: IO[bytes] | None = None,
    ) -> tuple[bytes, IO[bytes] | None]:
        if start >= end:
            return b'', fallback_fp
        length = end - start
        if hasattr(fs, 'cat_file'):
            with suppress(TypeError, AttributeError):
                data = fs.cat_file(path, start=start, end=end)
                if data is not None:
                    return data, fallback_fp
        if hasattr(fs, 'read_block'):
            with suppress(TypeError, AttributeError):
                data = fs.read_block(path, start, length)
                if data is not None:
                    return data, fallback_fp
        if fallback_fp is None:
            fallback_fp = fs.open(path, 'rb')
        fallback_fp.seek(start)
        return fallback_fp.read(length), fallback_fp

    def _fetch(self, start: int, end: int) -> bytes:
        data, self._fallback_fp = self._fetch_range(
            self._fs, self._path, start, end, self._fallback_fp
        )
        return data

    def readable(self) -> bool:
        return not self.closed

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def tell(self) -> int:
        if self.closed:
            raise ValueError('I/O operation on closed file')
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if self.closed:
            raise ValueError('I/O operation on closed file')
        if whence == io.SEEK_SET:
            pos = offset
        elif whence == io.SEEK_CUR:
            pos = self._pos + offset
        elif whence == io.SEEK_END:
            pos = self._size + offset
        else:
            raise ValueError(f'invalid whence: {whence}')
        if pos < 0:
            raise OSError(22, 'Invalid argument')
        self._pos = pos
        return self._pos

    def read(self, size: int | None = -1) -> bytes:
        if self.closed:
            raise ValueError('I/O operation on closed file')
        if size is None or size < 0:
            end = self._size
        else:
            end = min(self._pos + size, self._size)
        start = self._pos
        if start >= self._size or start >= end:
            return b''

        chunks: list[bytes] = []
        cursor = start
        if cursor < self._tail_start:
            remote_end = min(end, self._tail_start)
            chunks.append(self._fetch(cursor, remote_end))
            cursor = remote_end
        if cursor < end:
            rel = cursor - self._tail_start
            rel_end = end - self._tail_start
            chunks.append(self._tail[rel:rel_end])
        data = b''.join(chunks)
        self._pos = start + len(data)
        return data

    def readinto(self, buffer) -> int:
        data = self.read(len(buffer))
        n = len(data)
        buffer[:n] = data
        return n

    def close(self) -> None:
        if self.closed:
            return
        fallback = self._fallback_fp
        self._fallback_fp = None
        if fallback is not None:
            with suppress(Exception):
                fallback.close()
        super().close()


@dataclass(frozen=True, slots=True)
class ZipMember:
    """One ZIP member or a synthesized directory prefix."""

    path: str
    header_offset: int
    compress_type: int
    file_size: int
    compress_size: int
    is_dir: bool
    zip_name: str = ''
    crc: int = 0
    flag_bits: int = 0
    orig_filename: str = ''

    def to_zipinfo(self) -> zipfile.ZipInfo:
        """Build the ``ZipInfo`` ``ZipExtFile`` needs to open this member."""
        name = self.orig_filename or self.zip_name or self.path
        zinfo = zipfile.ZipInfo(filename=name)
        zinfo.orig_filename = name
        zinfo.compress_type = self.compress_type
        zinfo.compress_size = self.compress_size
        zinfo.file_size = self.file_size
        zinfo.CRC = self.crc
        zinfo.flag_bits = self.flag_bits
        zinfo.header_offset = self.header_offset
        return zinfo


class ZipMemberIndex:
    """Immutable ZIP listing index, including implicit parent directories.

    Published raw ZIPs are written with ``zip_fs.put_file`` and often omit
    directory members. Listing still has to treat every ancestor as a directory.
    Directory ``size`` is the sum of descendant uncompressed file sizes (the
    previous ``zip_fs.du`` behaviour).
    """

    __slots__ = ('_members', '_dir_sizes')

    def __init__(
        self,
        members: Mapping[str, ZipMember],
        dir_sizes: Mapping[str, int],
    ):
        self._members = MappingProxyType(dict(members))
        self._dir_sizes = MappingProxyType(dict(dir_sizes))

    @classmethod
    def empty(cls) -> ZipMemberIndex:
        root = ZipMember(
            path='',
            header_offset=0,
            compress_type=zipfile.ZIP_STORED,
            file_size=0,
            compress_size=0,
            is_dir=True,
        )
        return cls({'': root}, {'': 0})

    @classmethod
    def from_infolist(cls, infolist: Iterable[zipfile.ZipInfo]) -> ZipMemberIndex:
        members: dict[str, ZipMember] = {
            '': ZipMember(
                path='',
                header_offset=0,
                compress_type=zipfile.ZIP_STORED,
                file_size=0,
                compress_size=0,
                is_dir=True,
            )
        }
        dir_sizes: dict[str, int] = {'': 0}

        def ensure_dir(path: str) -> None:
            if path in members:
                return
            members[path] = ZipMember(
                path=path,
                header_offset=0,
                compress_type=zipfile.ZIP_STORED,
                file_size=0,
                compress_size=0,
                is_dir=True,
            )
            dir_sizes.setdefault(path, 0)

        for info in infolist:
            name = normalize_zip_member_path(info.filename)
            is_dir = bool(info.is_dir() or info.filename.endswith(('/', '\\')))
            if not name:
                continue
            members[name] = ZipMember(
                path=name,
                header_offset=info.header_offset,
                compress_type=info.compress_type,
                file_size=info.file_size,
                compress_size=info.compress_size,
                is_dir=is_dir,
                zip_name=info.filename,
                crc=getattr(info, 'CRC', 0),
                flag_bits=getattr(info, 'flag_bits', 0),
                orig_filename=getattr(info, 'orig_filename', info.filename),
            )
            parts = name.split('/')
            ancestors = ['']
            ancestors.extend('/'.join(parts[:i]) for i in range(1, len(parts)))
            for ancestor in ancestors:
                ensure_dir(ancestor)
                if not is_dir:
                    dir_sizes[ancestor] = dir_sizes.get(ancestor, 0) + info.file_size
            if is_dir:
                dir_sizes.setdefault(name, 0)

        return cls(members, dir_sizes)

    @classmethod
    def from_zipfile(cls, zip_file: zipfile.ZipFile) -> ZipMemberIndex:
        return cls.from_infolist(zip_file.infolist())

    @classmethod
    def from_bytes(cls, data: bytes) -> ZipMemberIndex:
        payload = msgpack.unpackb(data, raw=False, strict_map_key=False)
        if not isinstance(payload, Mapping) or payload.get('v') != 1:
            raise ValueError('unsupported ZIP member index format')
        members_raw = payload.get('members')
        dir_sizes_raw = payload.get('dir_sizes')
        if not isinstance(members_raw, list) or not isinstance(dir_sizes_raw, Mapping):
            raise ValueError('unsupported ZIP member index format')

        members: dict[str, ZipMember] = {}
        for row in members_raw:
            if not isinstance(row, list) or len(row) != 10:
                raise ValueError('unsupported ZIP member index format')
            (
                path,
                header_offset,
                compress_type,
                file_size,
                compress_size,
                is_dir,
                zip_name,
                crc,
                flag_bits,
                orig_filename,
            ) = row
            members[str(path)] = ZipMember(
                path=str(path),
                header_offset=int(header_offset),
                compress_type=int(compress_type),
                file_size=int(file_size),
                compress_size=int(compress_size),
                is_dir=bool(is_dir),
                zip_name=str(zip_name),
                crc=int(crc),
                flag_bits=int(flag_bits),
                orig_filename=str(orig_filename),
            )
        dir_sizes = {str(path): int(size) for path, size in dir_sizes_raw.items()}
        return cls(members, dir_sizes)

    def to_bytes(self) -> bytes:
        members = [
            [
                member.path,
                member.header_offset,
                member.compress_type,
                member.file_size,
                member.compress_size,
                member.is_dir,
                member.zip_name,
                member.crc,
                member.flag_bits,
                member.orig_filename,
            ]
            for member in self._members.values()
        ]
        dir_sizes = dict(self._dir_sizes)
        return msgpack.packb(
            {'v': 1, 'members': members, 'dir_sizes': dir_sizes},
            use_bin_type=True,
        )

    def exists(self, path: str) -> bool:
        return normalize_zip_member_path(path) in self._members

    def isfile(self, path: str) -> bool:
        member = self._members.get(normalize_zip_member_path(path))
        return member is not None and not member.is_dir

    def get(self, path: str) -> ZipMember | None:
        return self._members.get(normalize_zip_member_path(path))

    def size(self, path: str) -> int:
        key = normalize_zip_member_path(path)
        member = self._members[key]
        if member.is_dir:
            return self._dir_sizes.get(key, 0)
        return member.file_size

    def is_empty(self) -> bool:
        return all(path == '' for path in self._members)

    def listdir(
        self,
        path: str = '',
        recursive: bool = False,
        files_only: bool = False,
        depth: int = -1,
    ) -> Iterator[ZipMember]:
        """Yield members the way ``ZipFileSystem.find`` + skip-folder-itself did."""
        if depth == 0:
            return
        path = normalize_zip_member_path(path)
        member = self._members.get(path)
        if member is None:
            return
        if not member.is_dir:
            yield member
            return

        path_parts = [part for part in path.split('/') if part]
        path_depth = len(path_parts)
        maxdepth = None if recursive and depth < 0 else (1 if not recursive else depth)

        results: list[ZipMember] = []
        for name, item in self._members.items():
            if item.is_dir and files_only:
                continue
            name_parts = [part for part in name.split('/') if part]
            if len(name_parts) < path_depth:
                continue
            if any(left != right for left, right in zip(path_parts, name_parts)):
                continue
            if maxdepth is not None and name.count('/') >= maxdepth + path_depth:
                continue
            if item.is_dir and name == path:
                continue
            results.append(item)

        results.sort(key=lambda item: item.path)
        yield from results


def open_zip_member(fileobj: IO[bytes], member: ZipMember) -> zipfile.ZipExtFile:
    """Open a ZIP member at ``header_offset`` without constructing ``ZipFile``.

    Mirrors ``zipfile.ZipFile.open``: seek to the local file header, skip the
    30-byte header plus filename and extra (local extra can differ from the
    central directory, including ZIP64), then return a ``ZipExtFile`` positioned
    at the compressed payload. The caller must pass a range-capable file-like
    opened on the ZIP object; this function takes ownership via
    ``close_fileobj=True``.
    """
    fileobj.seek(member.header_offset)
    header = fileobj.read(_SIZE_FILE_HEADER)
    if len(header) != _SIZE_FILE_HEADER:
        raise zipfile.BadZipFile('Truncated file header')
    unpacked = struct.unpack(_STRUCT_FILE_HEADER, header)
    if unpacked[0] != _STRING_FILE_HEADER:
        raise zipfile.BadZipFile('Bad magic number for file header')

    filename_len = unpacked[10]
    extra_len = unpacked[11]
    fname = fileobj.read(filename_len)
    if extra_len:
        fileobj.read(extra_len)

    zinfo = member.to_zipinfo()
    local_flags = unpacked[3]
    fname_str = fname.decode('utf-8') if local_flags & 0x800 else fname.decode('cp437')
    if fname_str != zinfo.orig_filename:
        raise zipfile.BadZipFile(
            f'File name in directory {zinfo.orig_filename!r} and header {fname_str!r} differ.'
        )
    if zinfo.flag_bits & 0x20:
        raise NotImplementedError('compressed patched data (flag bit 5)')
    if zinfo.flag_bits & 0x40:
        raise NotImplementedError('strong encryption (flag bit 6)')
    if zinfo.flag_bits & 0x1:
        raise RuntimeError(
            f'File {member.path!r} is encrypted, password required for extraction'
        )
    return zipfile.ZipExtFile(fileobj, 'r', zinfo, None, True)


_KIND_RE = re.compile(r'^[a-z]+$')


class IndexDiskStore:
    """Per-node on-disk cache of immutable object indexes keyed by identity.

    Index files are keyed by object identity ``(path, etag, size)`` and a payload
    ``kind`` (for example ``zip`` or ``toc``). All kinds share one directory and
    are swept together when ``max_bytes`` is exceeded.
    """

    def __init__(self, directory: str, max_bytes: int, kind: str):
        if not _KIND_RE.match(kind):
            raise ValueError(f'invalid index kind: {kind!r}')
        self._directory = directory
        self._max_bytes = max_bytes
        self._kind = kind

    def key_filename(self, identity: tuple[str, str, int]) -> str:
        path, etag, size = identity
        path_hash = hashlib.sha1(path.encode()).hexdigest()
        safe_etag = _SAFE_ETAG_RE.sub('_', etag)[:64]
        return f'{path_hash}-{safe_etag}-{size}.{self._kind}.v1.msgpack'

    def load(self, identity: tuple[str, str, int]) -> bytes | None:
        path = os.path.join(self._directory, self.key_filename(identity))
        if not os.path.isfile(path):
            return None
        with open(path, 'rb') as fileobj:
            return fileobj.read()

    def discard(self, identity: tuple[str, str, int]) -> None:
        path = os.path.join(self._directory, self.key_filename(identity))
        with suppress(OSError):
            os.unlink(path)

    def store(self, identity: tuple[str, str, int], data: bytes) -> None:
        final_path = os.path.join(self._directory, self.key_filename(identity))
        tmp_path: str | None = None
        try:
            os.makedirs(self._directory, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self._directory, delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            os.replace(tmp_path, final_path)
            tmp_path = None
        except OSError:
            logger.warning(
                'failed to store cached index at %s', final_path, exc_info=True
            )
            if tmp_path is not None:
                with suppress(OSError):
                    os.unlink(tmp_path)
            return
        self._sweep()

    def _sweep(self) -> None:
        if self._max_bytes <= 0:
            return
        try:
            entries: list[tuple[float, str, int]] = []
            with os.scandir(self._directory) as scan:
                for entry in scan:
                    if not entry.is_file() or not entry.name.endswith('.v1.msgpack'):
                        continue
                    stat_result = entry.stat()
                    entries.append(
                        (stat_result.st_mtime, entry.path, stat_result.st_size)
                    )
            total = sum(size for _, _, size in entries)
            if total <= self._max_bytes:
                return
            entries.sort(key=lambda item: item[0])
            for _, path, size in entries:
                if total <= self._max_bytes:
                    break
                with suppress(OSError):
                    os.unlink(path)
                total -= size
        except OSError:
            logger.warning(
                'failed to sweep cached indexes in %s',
                self._directory,
                exc_info=True,
            )
