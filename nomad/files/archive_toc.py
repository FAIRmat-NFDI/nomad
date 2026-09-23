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

"""Combined archive TOC index for offset-based entry reads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import BinaryIO

import msgpack
from msglc.codec import CBORCodec, MsgspecCodec
from msglc.writer import LazyWriter

from nomad.archive.utils import check_archive_version


@dataclass(frozen=True, slots=True)
class ArchiveTocIndex:
    version: int
    entries: Mapping[str, tuple[int, int]]

    def to_bytes(self) -> bytes:
        return msgpack.packb(
            {'v': 1, 'version': self.version, 'entries': dict(self.entries)},
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ArchiveTocIndex:
        payload = msgpack.unpackb(data, raw=False, strict_map_key=False)
        if not isinstance(payload, Mapping) or payload.get('v') != 1:
            raise ValueError('unsupported archive TOC index format')
        version = payload.get('version')
        entries_raw = payload.get('entries')
        if not isinstance(version, int) or not isinstance(entries_raw, Mapping):
            raise ValueError('unsupported archive TOC index format')
        entries: dict[str, tuple[int, int]] = {}
        for entry_id, span in entries_raw.items():
            if not isinstance(span, (list, tuple)) or len(span) != 2:
                raise ValueError('unsupported archive TOC index format')
            start, end = span
            if not isinstance(start, int) or not isinstance(end, int):
                raise ValueError('unsupported archive TOC index format')
            entries[str(entry_id)] = (start, end)
        return cls(version, MappingProxyType(entries))

    def __contains__(self, entry_id: object) -> bool:
        return entry_id in self.entries

    def span(self, entry_id: str) -> tuple[int, int]:
        return self.entries[entry_id]

    def has_offsets(self) -> bool:
        """True when entry spans can be used for offset reads."""
        return self.version == 3


def build_archive_toc_index(file_obj: BinaryIO) -> ArchiveTocIndex | None:
    """Read the version magic and, for v3, the combined TOC from an open archive.

    A non-v3 archive returns an index with no spans. That result is cacheable,
    so a legacy file is not probed on every read. ``None`` means a v3 TOC could
    not be parsed and must not be cached.
    """
    version = check_archive_version(file_obj)
    if version != 3:
        return ArchiveTocIndex(version, MappingProxyType({}))

    magic = LazyWriter.magic_len()
    sep_a, sep_b, sep_c = magic, magic + 10, magic + 20
    file_obj.seek(0)
    header = file_obj.read(sep_c)
    if len(header) != sep_c:
        raise ValueError('truncated archive header')

    codec = MsgspecCodec() if header[sep_a] == 0 else CBORCodec()
    toc_start = codec.decode(header[sep_a:sep_b].lstrip(b'\0'))
    toc_size = codec.decode(header[sep_b:sep_c].lstrip(b'\0'))
    if not isinstance(toc_start, int) or not isinstance(toc_size, int):
        raise ValueError('invalid archive header')

    file_obj.seek(sep_c + toc_start)
    toc_bytes = file_obj.read(toc_size)
    if len(toc_bytes) != toc_size:
        raise ValueError('truncated archive TOC')

    toc = codec.decode(toc_bytes)
    if not isinstance(toc, Mapping):
        raise ValueError('invalid archive TOC')
    entry_starts = toc.get('t')
    if not isinstance(entry_starts, Mapping):
        return None

    if not all(isinstance(start, int) for start in entry_starts.values()):
        return None

    absolute_starts = {
        str(entry_id): sep_c + start for entry_id, start in entry_starts.items()
    }
    ordered = sorted(absolute_starts.items(), key=lambda item: item[1])
    ends = [start for _, start in ordered[1:]] + [sep_c + toc_start]
    entries = {entry_id: (start, end) for (entry_id, start), end in zip(ordered, ends)}

    return ArchiveTocIndex(version, MappingProxyType(entries))
