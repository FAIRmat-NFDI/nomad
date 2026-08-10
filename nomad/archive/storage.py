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

from collections.abc import Generator, Mapping, Sequence
from typing import BinaryIO, cast

import msgspec

from nomad import utils
from nomad.archive.utils import to_json
from nomad.config import config


def _decode(position: bytes) -> tuple[int, int]:
    return int.from_bytes(
        position[:5], byteorder='little', signed=False
    ), int.from_bytes(position[5:], byteorder='little', signed=False)


class ArchiveError(Exception):
    """An error that indicates a broken archive."""

    pass


class ArchiveItem:
    def __init__(self, f: BinaryIO, offset: int = 0):
        self._f = f
        self._offset = offset

    def _direct_read(self, size: int, offset: int):
        self._f.seek(offset)
        return self._f.read(size)

    def _read(self, position: tuple[int, int]):
        start, end = position
        raw_data = self._direct_read(end - start, start + self._offset)
        return msgspec.msgpack.decode(raw_data)

    def _child(self, child_toc_entry):
        if isinstance(child_toc_entry, dict):
            if child_toc_entry.get('toc', None):
                return ArchiveDict(child_toc_entry, self._f, self._offset)

            return self._read(child_toc_entry['pos'])

        if isinstance(child_toc_entry, list):
            return ArchiveList(child_toc_entry, self._f, self._offset)

        assert False, 'unreachable'


class ArchiveList(ArchiveItem, Sequence):
    def __init__(self, toc_entry: list, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toc_entry = toc_entry
        self._full_list = None

    def __getitem__(self, index):
        if self._full_list is None:
            child_entry = self._toc_entry[index]
            return self._child(child_entry)

        return self._full_list[index]

    def __len__(self):
        return self._toc_entry.__len__()

    def to_json(self):
        if self._full_list is None:
            self._full_list = [
                to_json(self._child(child_entry)) for child_entry in self._toc_entry
            ]

        return self._full_list


class ArchiveDict(ArchiveItem, Mapping):
    def __init__(self, toc_entry: dict, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._toc_entry = toc_entry

    def __getitem__(self, key):
        try:
            child_toc_entry = self._toc_entry['toc'][key]
            return self._child(child_toc_entry)
        except KeyError:
            return self.to_json().__getitem__(key)

    def __iter__(self):
        return self.to_json().__iter__()

    def __len__(self):
        return self.to_json().__len__()

    def to_json(self):
        # todo: potential bug here, what if children are ArchiveList or ArchiveDict?
        return self._read(self._toc_entry['pos'])


class ArchiveReader(ArchiveDict):
    def __init__(self, file_or_path: str | BinaryIO):
        self._file_or_path = file_or_path

        if isinstance(self._file_or_path, str):
            f: BinaryIO = cast(
                BinaryIO,
                open(
                    self._file_or_path, 'rb', buffering=config.archive.read_buffer_size
                ),
            )
        elif hasattr(self._file_or_path, 'read') and hasattr(
            self._file_or_path, 'seek'
        ):
            f = cast(BinaryIO, self._file_or_path)
        else:
            raise ValueError('not a file or path')

        # noinspection PyTypeChecker
        super().__init__(None, f)

        # this number is determined by the msgpack encoding of the file beginning:
        # { 'toc_pos': <...>
        #              ^11
        self._toc_entry = self._read(_decode(self._direct_read(10, 11)))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _locate_position(self, key: str) -> tuple:
        positions = self._toc_entry[key]
        return _decode(positions[0]), _decode(positions[1])

    def __getitem__(self, key):
        toc_position, data_position = self._locate_position(utils.adjust_uuid_size(key))

        return ArchiveDict(self._read(toc_position), self._f, data_position[0])

    def get_raw(self, key: str) -> tuple[dict, Generator]:
        """
        Get raw bytes of the data and the TOC of the entry.
        This is used to read the data without decoding it.
        This is used in combining individual entries into a single archive file.
        """
        toc_position, data_position = self._locate_position(utils.adjust_uuid_size(key))

        def _iter(position: tuple[int, int]):
            start, end = position
            total_size = end - start
            while total_size > 0:
                size = min(total_size, config.archive.copy_chunk_size)
                yield self._direct_read(size, start + self._offset)
                start += size
                total_size -= size

        return self._read(toc_position), _iter(data_position)

    def __iter__(self):
        return self._toc_entry.__iter__()

    def __len__(self):
        return self._toc_entry.__len__()

    def close(self):
        if isinstance(self._file_or_path, str):
            self._f.close()

    def is_closed(self):
        return self._f.closed if isinstance(self._file_or_path, str) else True


if __name__ == '__main__':
    pass
