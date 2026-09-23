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

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from nomad.config import config
from nomad.files.index_cache import IndexCache, IndexDiskStore, ObjectIdentity


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'enabled', True)
    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'directory', str(tmp_path))
    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'memory_max_mb', 1)
    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'revalidate_seconds', 60)
    return IndexCache('test', decode=bytes, encode=bytes, sizeof=len)


class ObjectFS:
    etag = 'v1'

    def info(self, path, **kwargs):
        return {'ETag': self.etag, 'size': 100}


def test_concurrent_cold_reads_share_one_build(cache):
    barrier = Barrier(8)
    builds = []
    fs = ObjectFS()

    def build():
        builds.append(True)
        # Let the other requests contend while this request fetches the index.
        time.sleep(0.02)
        return b'index'

    def read(_):
        barrier.wait(timeout=5)
        return cache.get_or_build(fs, 'object', '/object', build=build)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read, range(8)))
    assert len(builds) == 1
    assert all(result is results[0] for result in results)


def test_late_invalidation_preserves_newer_identity(cache, monkeypatch):
    fs = ObjectFS()
    old = cache.get_or_build(fs, 'object', '/object', build=lambda: b'old')
    fs.etag = 'v2'
    monkeypatch.setattr(config.fs.public_fs.metadata_cache, 'revalidate_seconds', 0)
    new = cache.get_or_build(fs, 'object', '/object', build=lambda: b'new')

    # An older request can fail its conditional GET after a newer one refreshed.
    cache.discard(old)
    assert cache.peek('object') is new
    assert cache._disk_store().load(new.identity) == b'new'


def test_disk_cache_read_failure_is_a_miss(tmp_path, monkeypatch):
    store = IndexDiskStore(str(tmp_path), 1024, 'test')
    identity = ObjectIdentity('object', 'v1', 100)
    store.store(identity, b'index')

    def inaccessible(*args, **kwargs):
        raise PermissionError('cache directory unavailable')

    monkeypatch.setattr('builtins.open', inaccessible)
    assert store.load(identity) is None
