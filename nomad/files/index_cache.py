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

"""Shared identity, disk storage, and memory caching for published object indexes."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Generic, NamedTuple, TypeVar

from cachetools import LRUCache
from fsspec import AbstractFileSystem

from nomad.config import config

logger = logging.getLogger(__name__)
_SAFE_ETAG_RE = re.compile(r'[^A-Za-z0-9._-]')


class ObjectIdentity(NamedTuple):
    location: str
    etag: str
    size: int


def object_identity(fs: AbstractFileSystem, path: str) -> ObjectIdentity:
    """Return ``(location, etag, size)`` for an object from one ``info()`` call.

    ETag strings are kept as the filesystem returned them. S3 quotes ETags;
    ``If-Match`` needs that quoted form. Disk cache filenames still sanitise
    the value via ``_SAFE_ETAG_RE``.
    """
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
        etag = str(etag)
    return ObjectIdentity(path, etag, size)


_KIND_RE = re.compile(r'^[a-z]+$')
T = TypeVar('T')


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

    def key_filename(self, identity: ObjectIdentity) -> str:
        path, etag, size = identity
        path_hash = hashlib.sha1(path.encode()).hexdigest()
        safe_etag = _SAFE_ETAG_RE.sub('_', etag)[:64]
        return f'{path_hash}-{safe_etag}-{size}.{self._kind}.v1.msgpack'

    def load(self, identity: ObjectIdentity) -> bytes | None:
        path = os.path.join(self._directory, self.key_filename(identity))
        try:
            with open(path, 'rb') as fileobj:
                return fileobj.read()
        except FileNotFoundError:
            # Another worker may sweep or invalidate an index at any time.
            return None
        except OSError:
            logger.warning('failed to load cached index at %s', path, exc_info=True)
            return None

    def discard(self, identity: ObjectIdentity) -> None:
        path = os.path.join(self._directory, self.key_filename(identity))
        with suppress(OSError):
            os.unlink(path)

    def store(self, identity: ObjectIdentity, data: bytes) -> None:
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

    def get_or_build(
        self,
        identity: ObjectIdentity,
        *,
        decode: Callable[[bytes], T],
        build: Callable[[], T | None],
        encode: Callable[[T], bytes],
    ) -> T | None:
        """Return the cached value for identity, or build and persist it."""
        value: T | None = None
        data = self.load(identity)
        if data is not None:
            try:
                value = decode(data)
            except Exception:
                logger.warning(
                    'failed to decode cached %s index for %s',
                    self._kind,
                    identity.location,
                    exc_info=True,
                )
                self.discard(identity)
        if value is None:
            value = build()
            if value is not None:
                self.store(identity, encode(value))
        return value

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


@dataclass(frozen=True, slots=True)
class CachedIndex(Generic[T]):
    identity: ObjectIdentity | None
    index: T
    os_path: str


class IndexCache(Generic[T]):
    """One index kind: TTL-revalidated process memory over the per-node disk store.

    Memory is a byte-bounded LRU (``memory_max_mb``; 0 disables it). The bound is
    read once when the LRU is created; ``clear()`` drops it so the next access
    recreates it with the currently configured value. ``revalidate_seconds == 0``
    always HEADs. Bounded striped locks serialize builds, revalidation, and
    invalidation for the same location. A separate lock protects the memory LRU;
    remote I/O does not hold that global lock.
    """

    def __init__(
        self,
        kind: str,
        *,
        decode: Callable[[bytes], T],
        encode: Callable[[T], bytes],
        sizeof: Callable[[T], int],
    ):
        self._kind = kind
        self._decode = decode
        self._encode = encode
        self._index_sizeof = sizeof
        self._lock = threading.Lock()
        self._key_locks = tuple(threading.RLock() for _ in range(64))
        self._store: LRUCache[str, tuple[CachedIndex[T], float]] | None = None

    def is_enabled(self) -> bool:
        return self._disk_store() is not None

    def get_or_build(
        self,
        fs: AbstractFileSystem,
        location: str,
        os_path: str,
        *,
        build: Callable[[], T | None],
    ) -> CachedIndex[T] | None:
        with self._key_lock(location):
            store = self._disk_store()
            if store is None:
                return None
            cached = self._fresh(fs, location)
            if cached is not None:
                return cached
            try:
                identity = object_identity(fs, location)
            except FileNotFoundError:
                return None
            index = store.get_or_build(
                identity,
                decode=self._decode,
                build=build,
                encode=self._encode,
            )
            if index is None:
                return None
            cached = CachedIndex(identity=identity, index=index, os_path=os_path)
            self._put(location, cached)
            return cached

    def get(
        self,
        fs: AbstractFileSystem,
        location: str,
    ) -> CachedIndex[T] | None:
        """Memory hit with TTL revalidation. ``None`` on miss, stale identity, or missing."""
        with self._key_lock(location):
            return self._fresh(fs, location)

    def _fresh(self, fs: AbstractFileSystem, location: str) -> CachedIndex[T] | None:
        """Return a still-valid memory entry. Caller must hold the location lock."""
        if self._disk_store() is None:
            return None
        with self._lock:
            cache = self._memory()
            if cache is None:
                return None
            entry = cache.get(location)
        if entry is None:
            return None
        cached, validated_at = entry
        settings = config.fs.public_fs.metadata_cache
        now = time.monotonic()
        if (
            settings.revalidate_seconds > 0
            and (now - validated_at) < settings.revalidate_seconds
        ):
            return cached
        try:
            identity = object_identity(fs, location)
        except FileNotFoundError:
            self.discard(cached)
            return None
        if identity != cached.identity:
            self.discard(cached)
            return None
        self._touch(location, now)
        return cached

    def peek(self, location: str) -> CachedIndex[T] | None:
        with self._lock:
            cache = self._memory()
            if cache is None:
                return None
            entry = cache.get(location)
            return None if entry is None else entry[0]

    def discard(self, cached: CachedIndex[T]) -> None:
        identity = cached.identity
        if identity is None:
            return
        location = getattr(identity, 'location', identity[0])
        with self._key_lock(location):
            with self._lock:
                entry = self._store.get(location) if self._store is not None else None
                if entry is not None and entry[0].identity == identity:
                    self._store.pop(location, None)
            if (store := self._disk_store()) is not None:
                store.discard(identity)

    def clear(self) -> None:
        with self._lock:
            self._store = None

    def _key_lock(self, location: str):
        return self._key_locks[hash(location) % len(self._key_locks)]

    def _disk_store(self) -> IndexDiskStore | None:
        settings = config.fs.public_fs.metadata_cache
        if not settings.is_enabled(config.fs.public_fs.protocol):
            return None
        directory = settings.directory or os.path.join(
            config.fs.local_tmp, 'nomad-zip-index'
        )
        return IndexDiskStore(directory, settings.max_disk_mb * 1024 * 1024, self._kind)

    def _sizeof(self, entry: tuple[CachedIndex[T], float]) -> int:
        return max(1, int(self._index_sizeof(entry[0].index)))

    def _memory(self) -> LRUCache[str, tuple[CachedIndex[T], float]] | None:
        if self._store is not None:
            return self._store
        max_bytes = int(config.fs.public_fs.metadata_cache.memory_max_mb * 1024 * 1024)
        if max_bytes <= 0:
            return None
        self._store = LRUCache(maxsize=max_bytes, getsizeof=self._sizeof)
        return self._store

    def _put(self, location: str, cached: CachedIndex[T]) -> None:
        with self._lock:
            cache = self._memory()
            if cache is None:
                return
            try:
                cache[location] = (cached, time.monotonic())
            except ValueError:
                cache.pop(location, None)

    def _touch(self, location: str, validated_at: float) -> None:
        with self._lock:
            cache = self._memory()
            if cache is None:
                return
            entry = cache.get(location)
            if entry is None:
                return
            cache[location] = (entry[0], validated_at)
