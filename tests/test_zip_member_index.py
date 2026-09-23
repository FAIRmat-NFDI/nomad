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
import os
import stat
import sys
import zipfile
from typing import NamedTuple

import pytest
from fsspec.implementations.zip import ZipFileSystem
from upath import UPath

from nomad.config import config
from nomad.files.index_cache import IndexCache, IndexDiskStore, object_identity
from nomad.files.zip_index import (
    RangeTailFile,
    ZipMember,
    ZipMemberIndex,
    open_zip_member,
)


class CountingRangeFS:
    """Minimal fsspec-like FS that counts size/range/open calls."""

    def __init__(self, data: bytes, path: str = 'bucket/raw.zip', etag: str = 'v1'):
        self.data = data
        self.path = path
        self.etag = etag
        self.info_calls = 0
        self.cat_file_calls = 0
        self.open_calls = 0
        self.read_block_calls = 0

    def info(self, path, refresh=False):
        self.info_calls += 1
        self.last_refresh = refresh
        return {
            'size': len(self.data),
            'type': 'file',
            'ETag': self.etag,
            'etag': self.etag,
        }

    def size(self, path):
        return self.info(path)['size']

    def exists(self, path):
        return True

    def cat_file(self, path, start=None, end=None):
        self.cat_file_calls += 1
        return self.data[start:end]

    def read_block(self, path, offset, length):
        self.read_block_calls += 1
        return self.data[offset : offset + length]

    def open(self, path, mode='rb'):
        self.open_calls += 1
        return io.BytesIO(self.data)


def stored_zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as zip_file:
        for name, content in entries.items():
            zip_file.writestr(name, content)
    return buffer.getvalue()


NESTED_ZIP_ENTRIES = {
    'a/b/c.txt': b'hello',
    'a/d.txt': b'world!!',
    'e.txt': b'root',
}


def _index_listing(
    index: ZipMemberIndex,
    path: str = '',
    recursive: bool = False,
    files_only: bool = False,
    depth: int = -1,
) -> list[tuple[str, bool, int]]:
    return [
        (member.path, not member.is_dir, index.size(member.path))
        for member in index.listdir(
            path, recursive=recursive, files_only=files_only, depth=depth
        )
    ]


def _zip_fs_listing(
    data: bytes,
    path: str = '',
    recursive: bool = False,
    files_only: bool = False,
    depth: int = -1,
) -> list[tuple[str, bool, int]]:
    zip_fs = ZipFileSystem(io.BytesIO(data))
    try:
        if depth == 0:
            return []
        items: list[tuple[str, bool, int]] = []
        for target in zip_fs.find(
            path, (depth if depth > 0 else None) if recursive else 1, not files_only
        ):
            isfile = zip_fs.isfile(target)
            if not isfile and UPath(target) == UPath(path):
                continue
            items.append(
                (
                    target,
                    isfile,
                    zip_fs.size(target) if isfile else zip_fs.du(target),
                )
            )
        return items
    finally:
        zip_fs.close()


def test_range_tail_file_seek_end_reports_real_size_not_tail_length():
    data = b'x' * 1000
    fs = CountingRangeFS(data)
    with RangeTailFile.from_filesystem(fs, fs.path, prefetch_bytes=50) as fileobj:
        assert fileobj.seek(0, io.SEEK_END) == 1000
        assert fileobj.tell() == 1000
        assert len(fileobj._tail) == 50
        fileobj.seek(960)
        assert fileobj.read(20) == data[960:980]
        fileobj.seek(0)
        assert fileobj.read(4) == data[:4]
    assert fileobj.closed
    assert fs.cat_file_calls >= 2  # tail prefetch + fallback before tail_start
    assert fs.info_calls == 1


def test_range_tail_file_and_index_parse_zip_once():
    data = stored_zip_bytes(NESTED_ZIP_ENTRIES)
    fs = CountingRangeFS(data)
    fileobj = RangeTailFile.from_filesystem(fs, fs.path, prefetch_bytes=len(data))
    zip_file = zipfile.ZipFile(fileobj)
    index = ZipMemberIndex.from_zipfile(zip_file)

    assert fs.info_calls == 1
    assert fs.cat_file_calls == 1
    assert fs.open_calls == 0

    for _ in range(5):
        assert index.exists('')
        assert index.exists('a')
        assert index.exists('a/b')
        assert index.exists('a/b/c.txt')
        assert not index.exists('missing')
        assert index.isfile('a/b/c.txt')
        assert not index.isfile('a')
        assert not index.isfile('')
        list(index.listdir('', recursive=True))
        list(index.listdir('a', recursive=True))
        list(index.listdir('', files_only=True, recursive=True))

    assert fs.cat_file_calls == 1
    assert fs.open_calls == 0

    zip_file.close()
    fileobj.close()
    assert fileobj.closed


def test_range_tail_file_fallback_reads_central_directory():
    data = stored_zip_bytes({f'f{i:02d}.txt': b'n' * 80 for i in range(40)})
    fs = CountingRangeFS(data)
    fileobj = RangeTailFile.from_filesystem(fs, fs.path, prefetch_bytes=22)
    zip_file = zipfile.ZipFile(fileobj)
    names = zip_file.namelist()
    assert len(names) == 40
    assert fs.cat_file_calls >= 2
    zip_file.close()
    fileobj.close()


class _ListingCase(NamedTuple):
    path: str = ''
    recursive: bool = False
    files_only: bool = False
    depth: int = -1


def test_zip_member_index_listing_parity_with_zip_filesystem():
    data = stored_zip_bytes(NESTED_ZIP_ENTRIES)
    index = ZipMemberIndex.from_infolist(zipfile.ZipFile(io.BytesIO(data)).infolist())

    cases = [
        _ListingCase(path='', recursive=False, files_only=False, depth=-1),
        _ListingCase(path='', recursive=True, files_only=False, depth=-1),
        _ListingCase(path='', recursive=True, files_only=True, depth=-1),
        _ListingCase(path='a', recursive=False, files_only=False, depth=-1),
        _ListingCase(path='a', recursive=True, files_only=False, depth=-1),
        _ListingCase(path='a', recursive=True, files_only=False, depth=1),
        _ListingCase(path='a/b', recursive=False, files_only=False, depth=-1),
        _ListingCase(path='a/b/c.txt', recursive=False, files_only=False, depth=-1),
        _ListingCase(path='', recursive=True, files_only=False, depth=0),
    ]
    for case in cases:
        assert _index_listing(
            index, case.path, case.recursive, case.files_only, case.depth
        ) == _zip_fs_listing(
            data, case.path, case.recursive, case.files_only, case.depth
        )

    assert index.exists('')
    assert index.size('a/b/c.txt') == 5
    assert index.size('a') == 5 + 7
    assert index.size('') == 5 + 7 + 4
    assert index.size('e.txt') == 4


def test_empty_zip_root_exists_and_listdir_is_empty():
    data = stored_zip_bytes({})
    index = ZipMemberIndex.from_infolist(zipfile.ZipFile(io.BytesIO(data)).infolist())
    assert index.exists('')
    assert not index.exists('anything')
    assert not index.isfile('')
    assert index.is_empty()
    assert _index_listing(index, '') == []
    assert _index_listing(index, '', recursive=True) == []


def test_zip_member_index_empty_sentinel():
    index = ZipMemberIndex.empty()
    assert index.exists('')
    assert not index.isfile('')
    assert index.is_empty()
    assert list(index.listdir('')) == []


def _assert_index_parity(
    original: ZipMemberIndex, restored: ZipMemberIndex, data: bytes
):
    cases = [
        _ListingCase(path='', recursive=False, files_only=False, depth=-1),
        _ListingCase(path='', recursive=True, files_only=False, depth=-1),
        _ListingCase(path='', recursive=True, files_only=True, depth=-1),
        _ListingCase(path='a', recursive=True, files_only=False, depth=-1),
    ]
    for case in cases:
        assert _index_listing(
            restored, case.path, case.recursive, case.files_only, case.depth
        ) == _index_listing(
            original, case.path, case.recursive, case.files_only, case.depth
        )
    for member in original.listdir('', recursive=True):
        assert restored.size(member.path) == original.size(member.path)
        assert restored.get(member.path) == original.get(member.path)
    with zipfile.ZipFile(io.BytesIO(data)) as zip_file:
        for name in zip_file.namelist():
            member = restored.get(name)
            if member is None or member.is_dir:
                continue
            with zip_file.open(name) as source:
                expected = source.read()
            opened = open_zip_member(io.BytesIO(data), member)
            try:
                assert opened.read() == expected
            finally:
                opened.close()


def test_zip_member_index_bytes_round_trip_nested_and_mixed():
    for data in (
        stored_zip_bytes(NESTED_ZIP_ENTRIES),
        _mixed_zip_bytes(),
    ):
        original = ZipMemberIndex.from_infolist(
            zipfile.ZipFile(io.BytesIO(data)).infolist()
        )
        restored = ZipMemberIndex.from_bytes(original.to_bytes())
        _assert_index_parity(original, restored, data)


def test_zip_member_index_from_bytes_rejects_invalid_payload():
    with pytest.raises(ValueError):
        ZipMemberIndex.from_bytes(b'garbage')
    index = ZipMemberIndex.empty()
    payload = index.to_bytes()
    bad_version = bytearray(payload)
    bad_version[2] ^= 0xFF
    with pytest.raises(ValueError):
        ZipMemberIndex.from_bytes(bytes(bad_version))


def _member_index_with_name(name: str) -> ZipMemberIndex:
    members = {
        '': ZipMember(
            path='',
            header_offset=0,
            compress_type=zipfile.ZIP_STORED,
            file_size=0,
            compress_size=0,
            is_dir=True,
        ),
        name: ZipMember(
            path=name,
            header_offset=0,
            compress_type=zipfile.ZIP_STORED,
            file_size=1,
            compress_size=1,
            is_dir=False,
            zip_name=name,
            orig_filename=name,
        ),
    }
    return ZipMemberIndex(members, {'': 1})


def _mixed_zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zip_file:
        zip_file.writestr(
            'stored.txt', b'hello stored', compress_type=zipfile.ZIP_STORED
        )
        zip_file.writestr(
            'deflated.txt', b'hello deflated' * 50, compress_type=zipfile.ZIP_DEFLATED
        )
        zip_file.writestr(
            'a/b/nested.txt', b'nested-bytes', compress_type=zipfile.ZIP_STORED
        )
        info = zipfile.ZipInfo('zip64.txt')
        with zip_file.open(info, 'w', force_zip64=True) as member:
            member.write(b'zip64 payload')
    return buffer.getvalue()


def test_open_zip_member_parity_stored_deflated_seek_and_zip64():
    data = _mixed_zip_bytes()
    index = ZipMemberIndex.from_infolist(zipfile.ZipFile(io.BytesIO(data)).infolist())
    assert index.exists('a')
    assert index.exists('a/b')
    assert index.isfile('a/b/nested.txt')
    assert _index_listing(index, '', recursive=True, files_only=False)

    with zipfile.ZipFile(io.BytesIO(data)) as zip_file:
        for name in ('stored.txt', 'deflated.txt', 'a/b/nested.txt', 'zip64.txt'):
            member = index.get(name)
            assert member is not None and not member.is_dir
            with zip_file.open(name) as source:
                expected = source.read()
            opened = open_zip_member(io.BytesIO(data), member)
            try:
                assert opened.read() == expected
            finally:
                opened.close()

            opened = open_zip_member(io.BytesIO(data), member)
            try:
                opened.seek(2)
                assert opened.read(4) == expected[2:6]
                opened.seek(0)
                assert opened.read() == expected
            finally:
                opened.close()


def test_object_identity_prefers_refresh_and_falls_back():
    data = stored_zip_bytes(NESTED_ZIP_ENTRIES)
    fs = CountingRangeFS(data, etag='v1')
    path, etag, size = object_identity(fs, fs.path)
    assert path == fs.path
    assert etag == 'v1'
    assert size == len(data)
    assert fs.last_refresh is True

    class NoKwargsFS:
        def __init__(self, data, path='bucket/raw.zip', etag='v2'):
            self.data = data
            self.path = path
            self.etag = etag
            self.info_calls = 0

        def info(self, path):
            self.info_calls += 1
            return {'size': len(self.data), 'ETag': self.etag}

    fallback_fs = NoKwargsFS(data, etag='v2')
    path2, etag2, size2 = object_identity(fallback_fs, fallback_fs.path)
    assert path2 == fallback_fs.path
    assert etag2 == 'v2'
    assert size2 == len(data)
    assert fallback_fs.info_calls == 1


@pytest.mark.parametrize('segment', ['x' * 190, 'α' * 90])
def test_memory_budget_accounts_for_long_filenames(tmp_path, monkeypatch, segment):
    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'directory', str(tmp_path))
    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'memory_max_mb', 1)
    cache = IndexCache(
        'zip',
        decode=ZipMemberIndex.from_bytes,
        encode=ZipMemberIndex.to_bytes,
        sizeof=lambda index: index.memory_size + 1024,
    )
    for prefix, fits in [('', True), ((segment + '/') * 10, False)]:
        infos = []
        for number in range(1000):
            info = zipfile.ZipInfo(f'{prefix}file_{number}.txt')
            info.header_offset = number * 100
            infos.append(info)
        index = ZipMemberIndex.from_infolist(infos)
        # Disk deserialization may retain separate strings for the same path.
        index = ZipMemberIndex.from_bytes(index.to_bytes())
        fs = CountingRangeFS(b'archive', path=f'object-{fits}')
        result = cache.get_or_build(fs, fs.path, '/archive', build=lambda: index)
        assert result.index is index  # oversized indexes remain usable by callers
        assert (cache.peek(fs.path) is not None) == fits
        assert (index.memory_size < 1024 * 1024) == fits


def test_object_identity_keeps_quoted_etag_and_disk_key_is_safe(tmp_path):
    data = stored_zip_bytes(NESTED_ZIP_ENTRIES)
    fs = CountingRangeFS(data, etag='"abc123"')
    identity = object_identity(fs, fs.path)
    assert identity[1] == '"abc123"'
    filename = IndexDiskStore(str(tmp_path), max_bytes=1024, kind='toc').key_filename(
        identity
    )
    assert '"' not in filename
    assert filename.endswith('.toc.v1.msgpack')


def test_index_disk_store_load_store_and_corruption(tmp_path):
    data = stored_zip_bytes(NESTED_ZIP_ENTRIES)
    index = ZipMemberIndex.from_infolist(zipfile.ZipFile(io.BytesIO(data)).infolist())
    identity = ('bucket/raw.zip', 'etag1', len(data))
    store = IndexDiskStore(str(tmp_path), max_bytes=1024 * 1024, kind='zip')

    assert store.load(identity) is None
    store.store(identity, index.to_bytes())
    key_path = tmp_path / store.key_filename(identity)
    assert key_path.is_file()
    loaded = ZipMemberIndex.from_bytes(store.load(identity))
    _assert_index_parity(index, loaded, data)

    key_path.write_bytes(b'garbage')
    with pytest.raises(ValueError):
        ZipMemberIndex.from_bytes(store.load(identity))
    store.discard(identity)
    assert not key_path.exists()

    store.store(identity, index.to_bytes())
    store.store(identity, index.to_bytes())
    assert len(list(tmp_path.glob('*.v1.msgpack'))) == 1


def test_index_disk_store_invalid_kind():
    with pytest.raises(ValueError, match='invalid index kind'):
        IndexDiskStore('/tmp', 1024, 'ZIP')


def test_index_disk_store_sweep_and_unwritable(tmp_path, monkeypatch, caplog):
    store = IndexDiskStore(str(tmp_path), max_bytes=0, kind='zip')
    for idx, etag in enumerate(('e1', 'e2', 'e3')):
        index = _member_index_with_name(f'file{idx}.txt')
        identity = (f'path{idx}.zip', etag, idx + 1)
        store.store(identity, index.to_bytes())
        key_path = tmp_path / store.key_filename(identity)
        os.utime(key_path, (idx, idx))

    files = sorted(tmp_path.glob('*.v1.msgpack'), key=lambda p: p.stat().st_mtime)
    assert len(files) == 3
    total = sum(path.stat().st_size for path in files)
    assert total > 200
    sweep_store = IndexDiskStore(str(tmp_path), max_bytes=200, kind='zip')
    sweep_store._sweep()
    remaining = list(tmp_path.glob('*.v1.msgpack'))
    assert remaining
    assert sum(path.stat().st_size for path in remaining) <= 200

    no_sweep = IndexDiskStore(str(tmp_path / 'unbounded'), max_bytes=0, kind='zip')
    no_sweep.store(('x.zip', 'e', 1), _member_index_with_name('a').to_bytes())
    assert len(list((tmp_path / 'unbounded').glob('*.v1.msgpack'))) == 1
    no_sweep._sweep()
    assert len(list((tmp_path / 'unbounded').glob('*.v1.msgpack'))) == 1

    if os.geteuid() == 0 or sys.platform == 'win32':
        pytest.skip('cannot chmod directory unwritable as root or on Windows')
    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    os.chmod(blocked, stat.S_IRUSR | stat.S_IXUSR)
    blocked_store = IndexDiskStore(str(blocked), max_bytes=1024, kind='zip')
    blocked_store.store(('x.zip', 'e', 1), _member_index_with_name('a').to_bytes())
    assert not list(blocked.glob('*.v1.msgpack'))
    os.chmod(blocked, stat.S_IRWXU)
