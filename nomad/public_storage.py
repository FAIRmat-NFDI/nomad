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

"""Published zip/msg/h5: where pack writes, and when S3 is safe to read.

Pack never tees two zip writers. It writes once (``choose_pack_fs``), then
``complete_published_write`` may copy that result to S3 and write
``.nomad-remote-ready.json``.

``choose_read_fs`` uses S3 only when that marker is present and every listed
object still HEADs with the recorded size and etag. A process-local cache skips
those artifact HEADs, but the marker itself is revalidated on every read so a
delete is visible to other workers. Otherwise it stays on local NFS if anything
is there (copy in flight, or copy failed). Legacy S3-only publishes with an
empty local dir still read from S3.

Typical callers::

    pack()     delete_ready_marker → write zip/msg → complete_published_write
    re_pack()  delete_ready_marker → rename both disks → complete_published_write
    delete()   delete_ready_marker → remove local and remote prefixes

A remote I/O error while checking the marker is not treated as "use local".
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from cachetools import TTLCache
from fsspec import AbstractFileSystem
from fsspec.implementations.local import LocalFileSystem

from nomad.common import now
from nomad.config import config
from nomad.zip_index import object_identity

if TYPE_CHECKING:
    from nomad.files import DirectoryObject, PathObject

logger = logging.getLogger(__name__)

MARKER_FILENAME = '.nomad-remote-ready.json'
MARKER_SCHEMA_VERSION = 1
Access = Literal['public', 'restricted']
_ACCESSES: tuple[Access, Access] = ('public', 'restricted')

# Positive artifact HEAD matches. The marker is revalidated on every read so a
# delete is visible to other workers. Negatives are never cached: a dual-write
# can finish, or a legacy object can appear, while this process runs.
_READY_CACHE_TTL_SECONDS = 600
_ready_cache: TTLCache = TTLCache(maxsize=16384, ttl=_READY_CACHE_TTL_SECONDS)
_ready_cache_lock = threading.Lock()

_UNREADABLE_MARKER = (
    UnicodeDecodeError,
    json.JSONDecodeError,
    TypeError,
    ValueError,
    KeyError,
)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """One published object listed in the remote-ready marker."""

    name: str
    size: int
    etag: str


@dataclass(frozen=True, slots=True)
class RemoteReadyMarker:
    """S3 is safe to read only while this marker matches live HEAD size/etag.

    Written last after a successful remote pack or local-to-remote copy.
    Deleted first before mutating published artifacts. Remote is authoritative;
    a copy on the local prefix is best-effort so a restore includes it.
    """

    schema_version: int
    upload_id: str
    access: Access
    created_at: str
    artifacts: tuple[ArtifactRecord, ...]

    @classmethod
    def create(
        cls, upload_id: str, access: Access, artifacts: list[ArtifactRecord]
    ) -> RemoteReadyMarker:
        return cls(
            schema_version=MARKER_SCHEMA_VERSION,
            upload_id=upload_id,
            access=access,
            created_at=now().isoformat(),
            artifacts=tuple(artifacts),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'schema_version': self.schema_version,
            'upload_id': self.upload_id,
            'access': self.access,
            'created_at': self.created_at,
            'artifacts': [
                {'name': item.name, 'size': item.size, 'etag': item.etag}
                for item in self.artifacts
            ],
        }

    @classmethod
    def from_dict(cls, data: Any) -> RemoteReadyMarker:
        payload = _parsed_marker_payload(data)
        artifacts = tuple(
            _artifact_record_from_dict(item) for item in payload['artifacts']
        )
        return cls(
            schema_version=MARKER_SCHEMA_VERSION,
            upload_id=payload['upload_id'],
            access=payload['access'],
            created_at=payload['created_at'],
            artifacts=artifacts,
        )

    @classmethod
    def load(
        cls, upload_os_path: str, fs: AbstractFileSystem
    ) -> RemoteReadyMarker | None:
        """Return the marker on ``fs``, or None if it is absent or unreadable.

        Invalid JSON or an unsupported payload is treated as absent. Filesystem
        errors other than not-found propagate.
        """
        marker = _path_object(upload_os_path, MARKER_FILENAME, fs)
        try:
            with fs.open(marker.location, 'rb') as file_obj:
                raw = file_obj.read()
        except FileNotFoundError:
            return None
        try:
            return cls.from_dict(json.loads(raw))
        except _UNREADABLE_MARKER:
            logger.warning(
                'ignoring unreadable remote-ready marker at %s', marker.location
            )
            return None

    def save(self, upload_os_path: str, fs: AbstractFileSystem) -> None:
        marker = _path_object(upload_os_path, MARKER_FILENAME, fs)
        _ensure_parent_dir(fs, marker.location)
        payload = json.dumps(self.to_dict(), indent=2).encode('utf-8')
        with fs.open(marker.location, 'wb') as file_obj:
            file_obj.write(payload)

    @classmethod
    def delete(cls, upload_os_path: str, fs: AbstractFileSystem) -> None:
        _path_object(upload_os_path, MARKER_FILENAME, fs).delete()

    def matches_remote(self, fs: AbstractFileSystem, upload_os_path: str) -> bool:
        """HEAD each listed artifact; size and etag must match.

        A missing object is a mismatch (return False). Other filesystem errors
        propagate so a ready marker is not silently ignored.
        """
        for artifact in self.artifacts:
            location = _path_object(upload_os_path, artifact.name, fs).location
            try:
                _, etag, size = object_identity(fs, location)
            except FileNotFoundError:
                return False
            if size != artifact.size or etag != artifact.etag:
                return False
        return True


def choose_pack_fs() -> AbstractFileSystem:
    """Filesystem pack writes zip/msg/h5 onto.

    ``local_only`` and ``local_then_remote`` use local NFS.
    ``remote_only`` uses S3 when ``protocol`` is set.
    """
    if config.fs.public_fs.resolved_write_mode == 'remote_only':
        return _remote_fs() or LocalFileSystem()
    return LocalFileSystem()


def complete_published_write(
    upload_os_path: str, upload_id: str, access: Access
) -> None:
    """After a successful pack, published bundle import, or embargo re_pack.

    ``local_then_remote`` copies missing artifacts from S3 onto local NFS, copies
    the complete local set back to S3, then writes the marker.
    ``remote_only`` writes the marker from S3 HEADs (objects are already there).
    ``local_only``, or no remote filesystem, is a no-op.

    re_pack must use this rather than ``write_ready_marker`` so a partial remote
    copy is overwritten from local before a new marker is written.
    """
    mode = config.fs.public_fs.resolved_write_mode
    if mode == 'local_only' or _remote_fs() is None:
        return
    if mode == 'local_then_remote':
        copy_to_remote(upload_os_path, upload_id, access)
    elif mode == 'remote_only':
        write_ready_marker(upload_os_path, upload_id, access)


def copy_to_remote(upload_os_path: str, upload_id: str, access: Access) -> None:
    """Copy finished local published objects to S3, then write the marker.

    Legacy remote-only publishes have no local copy. Missing artifacts are pulled
    from S3 first so reprocessing and embargo rename can complete. Deletes any
    existing marker after that hydrate, then copies. Raises if ``put_file`` or
    the size check fails; the marker is left absent so ``remote_then_local``
    keeps reading local.
    """
    remote_fs = _remote_fs()
    if remote_fs is None:
        raise RuntimeError('copy_to_remote requires fs.public_fs.protocol to be set')

    _hydrate_local_from_remote(upload_os_path, access, remote_fs)
    delete_ready_marker(upload_os_path)

    records: list[ArtifactRecord] = []
    for artifact in _published_artifact_files(
        upload_os_path, access, LocalFileSystem()
    ):
        records.append(_copy_one_artifact(upload_os_path, artifact, remote_fs))

    if not _save_marker(upload_os_path, upload_id, access, records, remote_fs):
        raise RuntimeError(
            f'cannot write remote-ready marker for {upload_id}: '
            'no published artifacts found'
        )


def write_ready_marker(upload_os_path: str, upload_id: str, access: Access) -> bool:
    """Write a marker from S3 HEADs after a remote pack or rename.

    Does not compare against local sizes. Returns False (and writes nothing)
    when no published artifacts are present on remote.
    """
    remote_fs = _remote_fs()
    if remote_fs is None:
        return False
    records = _records_from_existing(upload_os_path, access, remote_fs)
    return _save_marker(upload_os_path, upload_id, access, records, remote_fs)


def delete_ready_marker(upload_os_path: str) -> None:
    """Delete the completion marker before mutating published artifacts.

    Remote is attempted first (it is the read gate). A local copy is removed
    when present. Missing markers are ignored. Other remote errors propagate.
    """
    remote_fs = _remote_fs()
    if remote_fs is not None:
        RemoteReadyMarker.delete(upload_os_path, remote_fs)
    RemoteReadyMarker.delete(upload_os_path, LocalFileSystem())
    invalidate_ready_cache(upload_os_path)


def choose_read_fs(upload_os_path: str) -> AbstractFileSystem:
    """Filesystem a published upload is read from.

    No remote filesystem → local. ``remote_only`` → remote.
    ``remote_then_local`` follows the marker rule in the module docstring.
    """
    remote_fs = _remote_fs()
    if remote_fs is None:
        return LocalFileSystem()
    if config.fs.public_fs.read_mode != 'remote_then_local':
        return remote_fs
    return _choose_remote_then_local(upload_os_path, remote_fs)


def delete_access_artifacts(
    upload_os_path: str,
    access: Access,
    *,
    include_raw: bool,
    include_archive: bool,
) -> None:
    """Remove one access variant from every filesystem that may hold it.

    Pack writes the live access to ``choose_pack_fs`` only. The inverted access
    (public vs restricted) must be cleared on both local and remote so a
    dual-write cannot leave a stale embargoed object next to a new public one.
    """
    for fs in _local_and_remote_filesystems():
        directory = _upload_directory(upload_os_path, fs)
        if include_archive:
            directory.msg_fp(access, fs=fs).delete()
            directory.h5_fp(access, fs=fs).delete()
        if include_raw:
            directory.zip_fp(access, fs=fs).delete()


def rename_published_artifacts(
    upload_os_path: str, old_access: Access, new_access: Access
) -> None:
    """Rename zip/msg/h5 on every filesystem that has them.

    Local and remote are updated independently; ``PathObject.move_to`` requires
    the same filesystem type on both sides.
    """
    for fs in _local_and_remote_filesystems():
        directory = _upload_directory(upload_os_path, fs)
        directory.msg_fp(old_access, fs=fs).move_to(directory.msg_fp(new_access, fs=fs))
        directory.zip_fp(old_access, fs=fs).move_to(directory.zip_fp(new_access, fs=fs))
        directory.h5_fp(old_access, fs=fs).move_to(directory.h5_fp(new_access, fs=fs))


def invalidate_ready_cache(os_path: str) -> None:
    """Drop a cached "remote is ready" result after the marker or artifacts change."""
    with _ready_cache_lock:
        _ready_cache.pop(os_path, None)


def clear_ready_cache() -> None:
    """Drop process-level remote-ready caches. Intended for tests."""
    with _ready_cache_lock:
        _ready_cache.clear()


def detect_published_access(
    upload_os_path: str, *filesystems: AbstractFileSystem
) -> Access:
    """Return public/restricted by inspecting artifacts on ``filesystems``.

    Filesystems are tried in order; the first that has published artifacts
    wins. Raises KeyError if one filesystem has both accesses, or if none has
    either. Used to determine access from the write destination before read
    routing (bundle import, dual-write completion).
    """
    if not filesystems:
        raise KeyError('Neither public nor restricted files found')
    for fs in filesystems:
        found = _access_on_fs(upload_os_path, fs)
        if found is not None:
            return found
    raise KeyError('Neither public nor restricted files found')


def _choose_remote_then_local(
    upload_os_path: str, remote_fs: AbstractFileSystem
) -> AbstractFileSystem:
    marker = RemoteReadyMarker.load(upload_os_path, remote_fs)
    if marker is None:
        # Marker gone: drop a stale positive cache from this process. Other
        # workers never saw the writer's invalidate_ready_cache.
        invalidate_ready_cache(upload_os_path)
        # Prefer local if this prefix still has artifacts (copy in progress,
        # or copy failed). Fall back to remote only for legacy publishes that
        # have remote objects and an empty local prefix.
        if _has_published_artifacts(upload_os_path, LocalFileSystem()):
            return LocalFileSystem()
        if _has_published_artifacts(upload_os_path, remote_fs):
            _cache_as_ready(upload_os_path)
            return remote_fs
        return LocalFileSystem()

    if _cached_as_ready(upload_os_path):
        return remote_fs
    if marker.matches_remote(remote_fs, upload_os_path):
        _cache_as_ready(upload_os_path)
        return remote_fs
    # Marker exists but HEAD disagrees: do not serve a partial S3 copy.
    return LocalFileSystem()


def _remote_fs() -> AbstractFileSystem | None:
    public_fs = config.fs.public_fs
    if public_fs.protocol is None:
        return None
    return public_fs.target_fs


def _local_and_remote_filesystems() -> list[AbstractFileSystem]:
    filesystems: list[AbstractFileSystem] = [LocalFileSystem()]
    remote_fs = _remote_fs()
    if remote_fs is not None:
        filesystems.append(remote_fs)
    return filesystems


def _cached_as_ready(os_path: str) -> bool:
    with _ready_cache_lock:
        return bool(_ready_cache.get(os_path))


def _cache_as_ready(os_path: str) -> None:
    with _ready_cache_lock:
        _ready_cache[os_path] = True


def _upload_directory(upload_os_path: str, fs: AbstractFileSystem) -> DirectoryObject:
    from nomad.files import DirectoryObject

    return DirectoryObject(upload_os_path, fs=fs)


def _published_artifact_files(
    upload_os_path: str, access: Access, fs: AbstractFileSystem
) -> list[PathObject]:
    from nomad.files import (
        empty_archive_file_size,
        empty_hdf5_file_size,
        empty_zip_file_size,
    )

    directory = _upload_directory(upload_os_path, fs)
    found = []
    for artifact, empty_size in (
        (directory.zip_fp(access, fs=fs), empty_zip_file_size),
        (directory.msg_fp(access, fallback=True, fs=fs), empty_archive_file_size),
        (directory.h5_fp(access, fs=fs), empty_hdf5_file_size),
    ):
        if artifact.exists() and artifact.size > empty_size:
            found.append(artifact)
    return found


def _has_published_artifacts(upload_os_path: str, fs: AbstractFileSystem) -> bool:
    return any(
        _published_artifact_files(upload_os_path, access, fs) for access in _ACCESSES
    )


def _path_object(upload_os_path: str, name: str, fs: AbstractFileSystem) -> PathObject:
    from nomad.files import PathObject

    return PathObject(os.path.join(upload_os_path, name), fs=fs)


def _access_on_fs(upload_os_path: str, fs: AbstractFileSystem) -> Access | None:
    found: Access | None = None
    for access in _ACCESSES:
        if _published_artifact_files(upload_os_path, access, fs):
            if found is not None:
                raise KeyError('Inconsistency: both public and restricted files found')
            found = access
    return found


def _hydrate_local_from_remote(
    upload_os_path: str, access: Access, remote_fs: AbstractFileSystem
) -> None:
    """Copy remote artifacts that are missing locally.

    Does not overwrite a non-empty local file, so a newly packed archive is
    kept when the raw ZIP still lives only on remote. Downloads land in a
    sibling ``.part`` file and are renamed into place only after the size
    matches, so an interrupted copy cannot become the local artifact.
    """
    local_fs = LocalFileSystem()
    local_names = {
        os.path.basename(item.os_path)
        for item in _published_artifact_files(upload_os_path, access, local_fs)
    }
    for artifact in _published_artifact_files(upload_os_path, access, remote_fs):
        name = os.path.basename(artifact.os_path)
        if name in local_names:
            continue
        _copy_remote_artifact_to_local(upload_os_path, artifact, remote_fs, local_fs)


def _copy_remote_artifact_to_local(
    upload_os_path: str,
    artifact: PathObject,
    remote_fs: AbstractFileSystem,
    local_fs: AbstractFileSystem,
) -> None:
    name = os.path.basename(artifact.os_path)
    local = _path_object(upload_os_path, name, local_fs)
    _ensure_parent_dir(local_fs, local.location)
    dest = os.path.abspath(local.location)
    part_path = f'{dest}.part'
    try:
        with (
            remote_fs.open(artifact.location, 'rb') as src,
            local_fs.open(part_path, 'wb') as dst,
        ):
            shutil.copyfileobj(src, dst)
        part_size = local_fs.size(part_path)
        remote_size = artifact.size
        if part_size != remote_size:
            raise RuntimeError(
                f'local size mismatch for {name}: remote={remote_size} local={part_size}'
            )
        os.replace(part_path, dest)
    except Exception:
        if local_fs.exists(part_path):
            local_fs.rm(part_path)
        raise


def _copy_one_artifact(
    upload_os_path: str, artifact: PathObject, remote_fs: AbstractFileSystem
) -> ArtifactRecord:
    name = os.path.basename(artifact.os_path)
    local_size = artifact.size
    remote = _path_object(upload_os_path, name, remote_fs)
    _ensure_parent_dir(remote_fs, remote.location)
    remote_fs.put_file(os.path.abspath(artifact.os_path), remote.location)
    _, etag, remote_size = object_identity(remote_fs, remote.location)
    if remote_size != local_size:
        raise RuntimeError(
            f'remote size mismatch for {name}: local={local_size} remote={remote_size}'
        )
    return ArtifactRecord(name=name, size=remote_size, etag=etag)


def _ensure_parent_dir(fs: AbstractFileSystem, location: str) -> None:
    parent, _, _ = location.replace('\\', '/').rpartition('/')
    if parent:
        fs.makedirs(parent, exist_ok=True)


def _parsed_marker_payload(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError('marker is not an object')
    if data.get('schema_version') != MARKER_SCHEMA_VERSION:
        raise ValueError('unsupported marker schema_version')
    access = data.get('access')
    if access not in ('public', 'restricted'):
        raise ValueError('invalid marker access')
    upload_id = data.get('upload_id')
    created_at = data.get('created_at')
    if not isinstance(upload_id, str) or not upload_id:
        raise ValueError('invalid marker upload_id')
    if not isinstance(created_at, str) or not created_at:
        raise ValueError('invalid marker created_at')
    raw_artifacts = data.get('artifacts')
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ValueError('marker artifacts are missing')
    return {
        'upload_id': upload_id,
        'access': access,
        'created_at': created_at,
        'artifacts': raw_artifacts,
    }


def _artifact_record_from_dict(data: Any) -> ArtifactRecord:
    if not isinstance(data, dict):
        raise ValueError('artifact entry is not an object')
    name = data.get('name')
    if not isinstance(name, str) or not name:
        raise ValueError('invalid artifact name')
    if '/' in name or '\\' in name or name in {'.', '..'}:
        raise ValueError('artifact name must be a basename')
    size = data.get('size')
    etag = data.get('etag')
    if not isinstance(size, int) or size < 0:
        raise ValueError('invalid artifact size')
    if not isinstance(etag, str) or not etag:
        raise ValueError('invalid artifact etag')
    return ArtifactRecord(name=name, size=size, etag=etag.strip('"'))


def _records_from_existing(
    upload_os_path: str, access: Access, fs: AbstractFileSystem
) -> list[ArtifactRecord]:
    records = []
    for artifact in _published_artifact_files(upload_os_path, access, fs):
        _, etag, size = object_identity(fs, artifact.location)
        records.append(
            ArtifactRecord(
                name=os.path.basename(artifact.os_path), size=size, etag=etag
            )
        )
    return records


def _save_marker(
    upload_os_path: str,
    upload_id: str,
    access: Access,
    records: list[ArtifactRecord],
    remote_fs: AbstractFileSystem,
) -> bool:
    if not records:
        return False
    marker = RemoteReadyMarker.create(upload_id, access, records)
    marker.save(upload_os_path, remote_fs)
    try:
        marker.save(upload_os_path, LocalFileSystem())
    except Exception:
        logger.warning(
            'could not copy remote-ready marker to local prefix for %s',
            upload_id,
            exc_info=True,
        )
    invalidate_ready_cache(upload_os_path)
    return True
