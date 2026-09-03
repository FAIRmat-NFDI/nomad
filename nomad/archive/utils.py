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

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, BinaryIO

from msglc import FileInfo, LazyReader, LazyWriter, combine, dump

if TYPE_CHECKING:
    from typing import Any

v2_magic: bytes = b'nomad-archive-v2023'
v2_magic_len: int = len(v2_magic)


def check_archive_version(file_or_path: str | BinaryIO) -> int:
    from nomad.files import FSUtility

    magic_len = max(v2_magic_len, LazyWriter.magic_len())

    if isinstance(file_or_path, str):
        with FSUtility.open(file_or_path) as f:
            magic = f.read(magic_len)
    else:
        file_or_path.seek(0)
        magic = file_or_path.read(magic_len)
        file_or_path.seek(0)

    if magic.startswith(LazyWriter.magic):
        return 3
    if magic.startswith(v2_magic):
        return 2
    return 1


def write_archive(path_or_file: str | BinaryIO, data: dict) -> None:
    from nomad.files import FSUtility

    if not isinstance(path_or_file, str):
        dump(path_or_file, data, backend='rust')
    else:
        dump(FSUtility.upath(path_or_file), data, backend='rust')


def combine_archive(target_fp, data: Iterable[tuple]):
    from nomad.files import FSUtility

    def _kernel():
        for uuid, msg_fp in data:
            if not msg_fp:
                yield FileInfo(None, uuid, obj={})
            elif (
                msg_version := check_archive_version(msg_path := msg_fp.os_path)
            ) == 3:
                with (
                    FSUtility.open(msg_path) as file_obj,
                    LazyReader(file_obj, cached=False) as reader,
                ):
                    yield FileInfo(None, uuid, obj=reader[uuid])
            else:
                with read_archive(msg_path, detected_version=msg_version) as reader:
                    yield FileInfo(None, uuid, obj=to_json(reader[uuid]))

    upath = FSUtility.upath(target_fp)
    combine(upath.path, _kernel(), fs=upath.fs, backend='rust')


def read_archive(file_or_path: str | BinaryIO, **kwargs):
    """
    Allows to read a msgpack-based archive.

    Arguments:
        file_or_path: A file path or file-like to the archive file that should be read. The
            respective file has to be closed by the user. The returned obj supports the
            'with' statement and has a 'close' method.

    Returns:
        A mapping (dict-like) that can be used to access the archive data. The mapping
        will lazily load data as it is used. The mapping needs to be closed or used within
        a 'with' statement to free the underlying file resource after use.
    """
    from nomad.files import FSUtility

    from .storage import ArchiveReader
    from .storage_v2 import ArchiveReader as ArchiveReaderNew

    archive_version = kwargs.pop('detected_version', None) or check_archive_version(
        file_or_path
    )

    if archive_version == 1:
        # todo: replace implementation to enable automatic conversion
        # if isinstance(file_or_path, str):
        #     from nomad.archive.converter import convert_archive
        #
        #     convert_archive(file_or_path, overwrite=True)
        #
        # return ArchiveReaderNew(file_or_path)
        return ArchiveReader(file_or_path)
    if archive_version == 2:
        return ArchiveReaderNew(file_or_path)
    if archive_version == 3:
        if isinstance(file_or_path, str):
            if (upath := FSUtility.upath(file_or_path)).exists():
                return LazyReader(upath, **kwargs)
        return LazyReader(file_or_path, **kwargs)

    # should not reach here
    raise NotImplementedError


_PRIMITIVE_TYPES = (str, int, float, bool)


def to_json(data: Any) -> Any:
    """Convert data into JSON-compatible Python data."""
    if data is None or type(data) in _PRIMITIVE_TYPES:
        return data

    if hasattr(data, 'to_json'):
        return data.to_json()

    if hasattr(data, 'to_obj'):
        return data.to_obj()

    # no need to convert build-in types
    return data
