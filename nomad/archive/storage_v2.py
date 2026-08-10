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

from collections.abc import Generator
from io import BytesIO
from typing import BinaryIO

import msgspec.msgpack
from bitarray import bitarray
from msgpack import Unpacker

from nomad import utils
from nomad.archive import ArchiveError
from nomad.archive.utils import to_json, v2_magic_len
from nomad.config import config


class Utility:
    @staticmethod
    def decode(position: bytes) -> tuple[int, int]:
        """
        Decode the start and end offsets from a byte string.
        """
        return int.from_bytes(
            position[:5], byteorder='little', signed=False
        ), int.from_bytes(position[5:], byteorder='little', signed=False)

    # noinspection SpellCheckingInspection
    @staticmethod
    def unpackb(o):
        return msgspec.msgpack.decode(o)


class ArchiveItem:  # noqa: PLW1641
    def __init__(self, f: BinaryIO, offset: int = 0):
        self._f = f
        self._offset: int = offset
        # to record how many items have been accessed
        self._accessed_items: int = 0

    def __len__(self):
        raise NotImplementedError

    def __eq__(self, other):
        return self.to_json() == to_json(other)

    def __str__(self):
        return self.to_json().__str__()

    def _direct_read(self, size: int, offset: int):
        if self._f.closed:
            raise ArchiveError('Archive is closed')
        self._f.seek(offset)
        return self._f.read(size)

    # noinspection SpellCheckingInspection
    def _readb(self, start: int, end: int):
        return self._direct_read(end - start, start + self._offset)

    def _read(self, start: int, end: int):
        return Utility.unpackb(self._readb(start, end))

    def _child(self, toc: dict, offset: int | None = None):
        child_offset: int = offset or self._offset

        self._accessed_items += 1

        if (child_toc := toc.get('toc', None)) is None:
            child_pos: list = toc['pos']

            if (
                2 == len(child_pos)
                and isinstance(child_pos[0], int)
                and isinstance(child_pos[1], int)
            ):
                start, end = child_pos
                if self._offset == 0:
                    start += offset
                    end += offset
                return self._read(start, end)

            return ArchiveList(toc, self._f, child_offset)

        if isinstance(child_toc, list):
            return ArchiveList(toc, self._f, child_offset)

        if isinstance(child_toc, dict):
            return ArchiveDict(toc, self._f, child_offset)

        raise ArchiveError(f'Invalid TOC: {toc}')

    @property
    def _fast_loading(self):
        return config.archive.fast_loading and self._accessed_items < (
            config.archive.fast_loading_threshold * len(self)
        )

    def to_json(self):
        """
        Ensure the result is JSON serializable.
        Suitable for multiple accesses.
        """
        raise NotImplementedError


class ArchiveList(ArchiveItem):
    def __init__(self, toc: dict, f: BinaryIO, offset: int = 0):
        super().__init__(f, offset)
        self._toc: list = toc.get('toc', [])  # if empty, it's a list of small objects
        self._pos: list = toc['pos']
        self._cache = [None] * len(self)
        self._index: int = 0
        self._mask: bitarray = bitarray(len(self))
        self._mask.setall(0)
        self._full_loaded: bool = False

    def __getitem__(self, index):
        if isinstance(index, slice):
            index_range = range(*index.indices(len(self)))
        elif isinstance(index, int):
            index_range = [index]
        else:
            raise TypeError(f'Invalid type: {type(index)} for index {index}')

        for item in index_range:
            if 0 == self._mask[item]:
                if self._toc:
                    # has individual toc
                    self._mask[item] = 1
                    self._cache[item] = self._child(self._toc[item])
                else:
                    if item < 0:
                        item += len(self)
                    # grouped into blocks
                    # load the corresponding block
                    num_start, num_end = 0, 0
                    for size, start, end in self._pos:
                        num_end += size
                        if num_start <= item < num_end:
                            self._mask[num_start:num_end] = 1
                            self._cache[num_start:num_end] = list(
                                Unpacker(BytesIO(self._readb(start, end)))
                            )
                            break
                        num_start = num_end

        return self._cache[index]

    def __iter__(self):
        self._index = 0
        return self

    def __next__(self):
        if self._index >= len(self):
            raise StopIteration

        item = self[self._index]
        self._index += 1
        return item

    def __len__(self):
        return self._toc.__len__() if self._toc else sum(x[0] for x in self._pos)

    def to_json(self):
        if not self._full_loaded:
            self._full_loaded = True
            if not self._fast_loading:
                for index in range(len(self)):
                    self._cache[index] = to_json(self[index])  # type: ignore
            elif self._toc:
                self._cache = self._read(*self._pos)
            else:
                num_start, num_end = 0, 0
                for size, start, end in self._pos:
                    num_end += size
                    if 0 == self._mask[num_start]:
                        self._cache[num_start:num_end] = list(
                            Unpacker(BytesIO(self._readb(start, end)))
                        )
                    num_start = num_end

            self._mask.setall(1)

        return self._cache


class ArchiveDict(ArchiveItem):
    def __init__(self, toc: dict, f: BinaryIO, offset: int = 0):
        super().__init__(f, offset)
        self._toc: dict = toc['toc']
        self._pos: list = toc['pos']
        self._cache: dict = {}
        self._full_loaded: bool = False

    def __getitem__(self, key):
        if key not in self._cache:
            self._cache[key] = self._child(self._toc[key])

        return self._cache[key]

    def __contains__(self, item):
        return item in self._toc

    def __iter__(self):
        return self._toc.__iter__()

    def __len__(self):
        return self._toc.__len__()

    def get(self, key, default=None):
        return self[key] if key in self._toc else default

    def items(self):
        for k in self._toc:
            yield k, self[k]

    def keys(self):
        return self._toc.keys()

    def values(self):
        for k in self._toc:
            yield self[k]

    def to_json(self):
        if not self._full_loaded:
            self._full_loaded = True
            if self._fast_loading and self._pos:
                self._cache = self._read(*self._pos)
            else:
                for k in self:
                    self._cache[k] = to_json(self[k])

        return self._cache


class ArchiveReader(ArchiveItem):
    def __init__(self, file_or_path: str | BinaryIO):
        self._file_or_path = file_or_path

        if isinstance(self._file_or_path, str):
            f = open(
                self._file_or_path, 'rb', buffering=config.archive.read_buffer_size
            )
        elif hasattr(self._file_or_path, 'read') and hasattr(
            self._file_or_path, 'seek'
        ):
            f = self._file_or_path
        else:
            raise ValueError('not a file or path')

        super().__init__(f)  # type: ignore

        self._cache: dict = {}
        self._full_cache: dict = None  # type: ignore

        # this number is determined by the msgpack encoding of the file beginning:
        # { 'toc_pos': <...>
        #              ^11
        # 11 ==> 1 (0b0000XXXX for map) + 1 (0b101XXXXX for str key) + 7 ('toc_pos') + 2 (0xc4 0bXXXXXXXX for bin 8)
        self._toc_entry: dict = self._read(
            *Utility.decode(self._direct_read(10, 11 + v2_magic_len))
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        if exc_val:
            raise exc_val

    def _locate_position(self, key: str) -> tuple:
        positions = self._toc_entry[key]
        return Utility.decode(positions[0]), Utility.decode(positions[1])

    def __getitem__(self, key: str) -> ArchiveDict:
        key = utils.adjust_uuid_size(key)

        if self._full_cache is not None:
            return self._full_cache[key]

        if key in self._cache:
            return self._cache[key]

        toc_position, data_position = self._locate_position(key)
        self._cache[key] = self._child(self._read(*toc_position), data_position[0])  # type: ignore

        return self._cache[key]

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

        return self._read(*toc_position), _iter(data_position)

    def __contains__(self, item):
        try:
            _ = self[item]
            return True
        except KeyError:
            return False

    def __iter__(self):
        return self._toc_entry.__iter__()

    def __len__(self):
        return self._toc_entry.__len__()

    def get(self, key: str, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def items(self):
        for k in self._toc_entry:
            yield k, self[k]

    def keys(self):
        return self._toc_entry.keys()

    def values(self):
        for k in self._toc_entry:
            yield self[k]

    def close(self, close_unowned: bool = False):
        if close_unowned or isinstance(self._file_or_path, str):
            self._f.close()

    def is_closed(self):
        # If the input is a BytesIO, it is assumed that the file is always closed
        # If the input is a path, need to check if the file is closed
        return self._f.closed if isinstance(self._file_or_path, str) else True

    def to_json(self):
        if self._full_cache is None:
            self._full_cache = {k: to_json(v) for k, v in self.items()}

        return self._full_cache
