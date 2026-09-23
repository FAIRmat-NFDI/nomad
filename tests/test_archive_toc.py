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

import io

import pytest
from msglc import FileInfo, LazyReader, combine, dump

from nomad.archive.utils import read_archive, to_json, v2_magic, write_archive
from nomad.files.archive_toc import ArchiveTocIndex, build_archive_toc_index


def _entry_data(entry_id: str) -> dict:
    return {
        entry_id: {
            'metadata': {
                'entry_id': entry_id,
                'upload_id': 'upload',
                'mainfile': f'{entry_id}.json',
            },
            'results': {'value': entry_id},
        }
    }


def _make_combined_archive(entry_ids: tuple[str, ...]) -> io.BytesIO:
    target = io.BytesIO()

    def generate_entries():
        for entry_id in entry_ids:
            buf = io.BytesIO()
            dump(buf, _entry_data(entry_id), backend='rust')
            buf.seek(0)
            with LazyReader(buf) as reader:
                yield FileInfo(None, entry_id, obj=reader[entry_id])

    combine(target, generate_entries(), backend='rust')
    target.seek(0)
    return target


def test_build_archive_toc_index_v3_combined():
    archive = _make_combined_archive(('entry_a', 'entry_b'))
    index = build_archive_toc_index(archive)
    assert index is not None
    assert index.version == 3
    assert set(index.entries) == {'entry_a', 'entry_b'}

    ordered = sorted(index.entries.items(), key=lambda item: item[1][0])
    for (_, (start, end)), (_, (next_start, _)) in zip(
        ordered, ordered[1:], strict=False
    ):
        assert start < end == next_start

    with read_archive(archive) as reader:
        expected = {entry_id: to_json(reader[entry_id]) for entry_id in reader.keys()}

    for entry_id, (start, _) in index.entries.items():
        archive.seek(start)
        with LazyReader(archive, from_combined=True) as entry_reader:
            assert to_json(entry_reader) == expected[entry_id]


def test_build_archive_toc_index_ordinary_v3_returns_none(tmp_path):
    path = str(tmp_path / 'ordinary-v3.msg')
    # This payload exceeds the 4 MiB indexed-archive threshold used in production.
    payload = {f'entry_{i}': {'data': list(range(10_000))} for i in range(200)}
    write_archive(path, payload)

    with open(path, 'rb') as archive:
        assert build_archive_toc_index(archive) is None
    with read_archive(path) as archive:
        assert to_json(archive['entry_0']) == payload['entry_0']


def test_archive_toc_index_round_trip():
    archive = _make_combined_archive(('entry_a', 'entry_b'))
    original = build_archive_toc_index(archive)
    assert original is not None
    restored = ArchiveTocIndex.from_bytes(original.to_bytes())
    assert restored == original
    assert 'entry_a' in restored
    assert restored.span('entry_a') == original.span('entry_a')


def test_archive_toc_index_from_bytes_rejects_garbage():
    with pytest.raises(ValueError):
        ArchiveTocIndex.from_bytes(b'garbage')


def test_build_archive_toc_index_non_v3():
    v2_archive = io.BytesIO(v2_magic + b'\x00' * 32)
    v2_index = build_archive_toc_index(v2_archive)
    assert v2_index is not None
    assert v2_index.version == 2
    assert dict(v2_index.entries) == {}
    assert not v2_index.has_offsets()
    assert ArchiveTocIndex.from_bytes(v2_index.to_bytes()) == v2_index

    v1_archive = io.BytesIO(b'not-a-msglc-archive')
    v1_index = build_archive_toc_index(v1_archive)
    assert v1_index is not None
    assert v1_index.version == 1
    assert not v1_index.has_offsets()
