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

import asyncio
import hashlib
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status

from nomad.actions.assets.models import (
    ACTION_ASSET_REF_TYPE,
    ActionAssetPurpose,
    ActionAssetRef,
    ActionAssetUploadResult,
)
from nomad.config import config
from nomad.mongo.action import ActionDocument

ACTION_INSTANCE_ASSETS_DIRNAME = 'assets'


@dataclass
class ConsumedAssetRollback:
    """Represents a completed move that can be reverted on failure."""

    staged_path: Path
    destination: Path


def _staging_root() -> Path:
    """Return the root directory for temporary action-asset staging."""

    return Path(config.fs.tmp) / 'tmp_action_asset'


def _safe_filename(filename: str | None) -> str:
    """Normalize a user-provided filename into a safe basename.

    Invalid or empty names are replaced with ``upload.bin``.
    """

    base = os.path.basename(filename or '')
    if not base:
        return 'upload.bin'
    return re.sub(r'[^\w.\-]', '_', base).strip() or 'upload.bin'


def _safe_scope_component(value: str, field_name: str) -> str:
    """Validate an identifier before using it as one filesystem path part."""

    if (
        not value
        or value in {'.', '..'}
        or '/' in value
        or '\\' in value
        or '\x00' in value
        or Path(value).is_absolute()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Invalid {field_name}.',
        )
    return value


def _make_storage_filename(original_filename: str) -> str:
    """Build a storage filename as ``<uuid>__<original>``."""

    return f'{uuid.uuid4().hex}__{original_filename}'


def _extract_original_filename(storage_or_original_filename: str) -> str:
    """Extract original filename from ``<uuid>__<original>`` if present."""

    prefix, sep, original = storage_or_original_filename.partition('__')
    if sep and len(prefix) == 32 and all(c in '0123456789abcdef' for c in prefix):
        return original or storage_or_original_filename
    return storage_or_original_filename


def _assert_media_type_allowed(media_type: str):
    """Validate a media type against configured exact and wildcard allowlists.

    Raises:
        HTTPException: If media type is not permitted.
    """

    allowed = config.actions.action_assets.allowed_media_types
    if '*/*' in allowed:
        return
    if media_type in allowed:
        return
    major, _, _ = media_type.partition('/')
    if f'{major}/*' in allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f'Unsupported media type: {media_type}',
    )


def _assert_scope(
    purpose: ActionAssetPurpose,
    action_id: str | None,
    action_instance_id: str | None,
    signal_fn_name: str | None,
):
    """Validate purpose-specific scope identifiers.

    Raises:
        HTTPException: If required scope fields for ``purpose`` are missing.
    """

    if purpose == ActionAssetPurpose.ACTION_START and not action_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='action_id is required for action_start assets.',
        )
    if purpose == ActionAssetPurpose.ACTION_SIGNAL and (
        not action_instance_id or not signal_fn_name
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='action_instance_id and signal_fn_name are required for action_signal assets.',
        )


def _scope_staging_dir(
    *,
    user_id: str,
    purpose: ActionAssetPurpose,
    action_id: str | None,
    action_instance_id: str | None,
    signal_fn_name: str | None,
) -> Path:
    """Resolve the scope directory under staging for a user and purpose."""

    root = _staging_root()
    safe_user_id = _safe_scope_component(user_id, 'user_id')
    if purpose == ActionAssetPurpose.ACTION_START:
        assert action_id
        safe_action_id = _safe_scope_component(action_id, 'action_id')
        return root / purpose.value / safe_user_id / safe_action_id
    assert action_instance_id and signal_fn_name
    safe_action_instance_id = _safe_scope_component(
        action_instance_id, 'action_instance_id'
    )
    safe_signal_fn_name = _safe_scope_component(signal_fn_name, 'signal_fn_name')
    return (
        root
        / purpose.value
        / safe_user_id
        / safe_action_instance_id
        / safe_signal_fn_name
    )


def _instance_assets_dir(action_instance_id: str) -> Path:
    """Return (and create) the user-asset directory for an action instance."""

    safe_action_instance_id = _safe_scope_component(
        action_instance_id, 'action_instance_id'
    )
    path = (
        Path(config.fs.actions)
        / safe_action_instance_id
        / ACTION_INSTANCE_ASSETS_DIRNAME
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _existing_instance_assets_dir(action_instance_id: str) -> Path:
    """Return the user-asset directory for an action instance without creating it."""

    safe_action_instance_id = _safe_scope_component(
        action_instance_id, 'action_instance_id'
    )
    return (
        Path(config.fs.actions)
        / safe_action_instance_id
        / ACTION_INSTANCE_ASSETS_DIRNAME
    )


def _resolve_staged_path(scope_dir: Path, filename: str) -> Path | None:
    """Resolve a staged file path by filename within a scope directory."""

    path = scope_dir / filename
    if path.exists() and path.is_file():
        return path
    return None


def _sha256_and_size(path: Path) -> tuple[str, int]:
    """Compute SHA-256 digest and size in bytes for a file."""

    sha = hashlib.sha256()
    size = 0
    with path.open('rb') as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            sha.update(chunk)
    return sha.hexdigest(), size


def _write_upload_file(
    upload_file: UploadFile,
    staged_path: Path,
    max_size: int,
    user_quota: int,
    current_usage: int,
) -> tuple[str, int]:
    """Persist an uploaded file, computing checksum and size during the write."""

    sha = hashlib.sha256()
    size = 0

    try:
        with staged_path.open('wb') as target:
            while True:
                chunk = upload_file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail='Uploaded file exceeds configured maximum size.',
                    )
                if user_quota > 0 and (current_usage + size) > user_quota:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail='User action asset quota exceeded.',
                    )
                sha.update(chunk)
                target.write(chunk)
    except Exception:
        staged_path.unlink(missing_ok=True)
        raise

    return sha.hexdigest(), size


def _move_file(source: Path, destination: Path) -> None:
    """Atomically move a file between two paths."""

    os.replace(source, destination)


def _path_size_bytes(path: Path) -> int:
    """Return recursive size of a file/directory in bytes."""

    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size

    return sum(
        file_path.stat().st_size for file_path in path.rglob('*') if file_path.is_file()
    )


async def _current_user_usage_bytes(user_id: str) -> int:
    """Compute current filesystem usage for action assets owned by a user.

    Includes:
    - staged files in tmp action-asset folders for both start and signal flows
    - consumed files under ``<fs.actions>/<action_instance_id>/assets`` for
      action documents owned by the user
    """

    total = 0

    staged_start = _staging_root() / ActionAssetPurpose.ACTION_START.value / user_id
    staged_signal = _staging_root() / ActionAssetPurpose.ACTION_SIGNAL.value / user_id
    total += await asyncio.to_thread(_path_size_bytes, staged_start)
    total += await asyncio.to_thread(_path_size_bytes, staged_signal)

    user_actions = await ActionDocument.find({'user_id': user_id}).to_list()
    for action in user_actions:
        instance_id = getattr(action, 'action_instance_id', None)
        if not instance_id:
            continue
        total += await asyncio.to_thread(
            _path_size_bytes, _existing_instance_assets_dir(instance_id)
        )

    return total


async def _validate_artifact_source_ownership(
    user_id: str, source_action_instance_id: str
) -> Path:
    """Validate that an artifact source action belongs to the requesting user."""

    action = await ActionDocument.find_one(
        ActionDocument.action_instance_id == source_action_instance_id,
        ActionDocument.user_id == user_id,
    )
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Source action was not found for the user.',
        )
    return _existing_instance_assets_dir(source_action_instance_id)


async def _clone_source_path(
    *,
    user_id: str,
    safe_source_filename: str,
    source_original_filename: str,
    purpose: ActionAssetPurpose,
    action_id: str | None,
    action_instance_id: str | None,
    signal_fn_name: str | None,
    source_action_instance_id: str | None,
) -> Path:
    if source_action_instance_id:
        source_dir = await _validate_artifact_source_ownership(
            user_id, source_action_instance_id
        )
        return source_dir / source_original_filename

    source_scope_dir = _scope_staging_dir(
        user_id=user_id,
        purpose=purpose,
        action_id=action_id,
        action_instance_id=action_instance_id,
        signal_fn_name=signal_fn_name,
    )
    source_path = _resolve_staged_path(source_scope_dir, safe_source_filename)
    if source_path is None:
        raise ValueError(f'Asset filename {safe_source_filename} was not found.')
    return source_path


async def upload_action_asset(
    user_id: str,
    upload_file: UploadFile,
    purpose: ActionAssetPurpose,
    action_id: str | None = None,
    action_instance_id: str | None = None,
    signal_fn_name: str | None = None,
    expected_media_type: str | None = None,
    expected_sha256: str | None = None,
) -> ActionAssetUploadResult:
    """Upload one file into scoped staging and return a filename-based asset ref.

    The file is stored under:
    ``tmp_action_asset/<purpose>/<user>/<scope>/<uuid>__<original_filename>``.
    Size, media type, checksum, and per-user quota are validated during upload.

    Raises:
        HTTPException: For invalid scope, media type, file size/quota, or checksum.
    """

    _assert_scope(purpose, action_id, action_instance_id, signal_fn_name)

    media_type = (
        upload_file.content_type or expected_media_type or 'application/octet-stream'
    )
    _assert_media_type_allowed(media_type)

    scope_dir = _scope_staging_dir(
        user_id=user_id,
        purpose=purpose,
        action_id=action_id,
        action_instance_id=action_instance_id,
        signal_fn_name=signal_fn_name,
    )
    scope_dir.mkdir(parents=True, exist_ok=True)

    original_filename = _safe_filename(upload_file.filename)
    filename = _make_storage_filename(original_filename)
    staged_path = scope_dir / filename

    max_size = config.actions.action_assets.max_file_size_bytes
    user_quota = config.actions.action_assets.per_user_quota_bytes
    current_usage = await _current_user_usage_bytes(user_id) if user_quota > 0 else 0

    try:
        await upload_file.seek(0)
        digest, size = await asyncio.to_thread(
            _write_upload_file,
            upload_file,
            staged_path,
            max_size,
            user_quota,
            current_usage,
        )
    finally:
        await upload_file.close()

    if expected_sha256 and expected_sha256.lower() != digest.lower():
        staged_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Uploaded file checksum does not match expected_sha256.',
        )

    return ActionAssetUploadResult(
        nomad_type=ACTION_ASSET_REF_TYPE,
        filename=filename,
        media_type=media_type,
        size=size,
        sha256=digest,
        purpose=purpose,
        action_id=action_id,
        action_instance_id=action_instance_id,
        signal_fn_name=signal_fn_name,
    )


async def clone_action_asset(
    *,
    user_id: str,
    source_filename: str,
    purpose: ActionAssetPurpose,
    action_id: str | None = None,
    action_instance_id: str | None = None,
    signal_fn_name: str | None = None,
    source_action_instance_id: str | None = None,
) -> ActionAssetUploadResult:
    """Clone an existing file into a new staged file for the requested scope.

    Source resolution order:
    - ``source_action_instance_id`` assets folder, if provided
    - staged file with matching filename in the same target scope

    The clone result is a new staged file with a fresh ``<uuid>__`` prefix.

    Raises:
        ValueError: If source file is not found.
        HTTPException: For source-size or user-quota violations.
    """

    _assert_scope(purpose, action_id, action_instance_id, signal_fn_name)

    safe_source_filename = _safe_filename(source_filename)
    source_original_filename = _extract_original_filename(safe_source_filename)
    source_path = await _clone_source_path(
        user_id=user_id,
        safe_source_filename=safe_source_filename,
        source_original_filename=source_original_filename,
        purpose=purpose,
        action_id=action_id,
        action_instance_id=action_instance_id,
        signal_fn_name=signal_fn_name,
        source_action_instance_id=source_action_instance_id,
    )

    if not source_path.exists():
        raise ValueError(f'Asset filename {safe_source_filename} was not found.')

    source_size = source_path.stat().st_size
    max_size = config.actions.action_assets.max_file_size_bytes
    if source_size > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail='Source file exceeds configured maximum size.',
        )

    user_quota = config.actions.action_assets.per_user_quota_bytes
    if user_quota > 0:
        current_usage = await _current_user_usage_bytes(user_id)
        if (current_usage + source_size) > user_quota:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail='User action asset quota exceeded.',
            )

    scope_dir = _scope_staging_dir(
        user_id=user_id,
        purpose=purpose,
        action_id=action_id,
        action_instance_id=action_instance_id,
        signal_fn_name=signal_fn_name,
    )
    scope_dir.mkdir(parents=True, exist_ok=True)

    destination_filename = _make_storage_filename(source_original_filename)
    destination = scope_dir / destination_filename
    await asyncio.to_thread(shutil.copyfile, source_path, destination)
    digest, size = await asyncio.to_thread(_sha256_and_size, destination)

    return ActionAssetUploadResult(
        nomad_type=ACTION_ASSET_REF_TYPE,
        filename=destination_filename,
        media_type='application/octet-stream',
        size=size,
        sha256=digest,
        purpose=purpose,
        action_id=action_id,
        action_instance_id=action_instance_id,
        signal_fn_name=signal_fn_name,
    )


async def consume_staged_assets(
    *,
    refs: list[ActionAssetRef],
    user_id: str,
    purpose: ActionAssetPurpose,
    target_action_instance_id: str,
    action_id: str | None = None,
    signal_fn_name: str | None = None,
) -> list[ConsumedAssetRollback]:
    """Move staged refs into an action instance's user-asset directory.

    Each reference is resolved by exact staged filename in the scope and then
    atomically moved into:
    ``<fs.actions>/<target_action_instance_id>/assets``.

    If any move fails, previously moved files are restored.

    Returns:
        Rollback entries describing completed moves.
    """

    if not refs:
        return []

    scope_dir = _scope_staging_dir(
        user_id=user_id,
        purpose=purpose,
        action_id=action_id,
        action_instance_id=target_action_instance_id
        if purpose == ActionAssetPurpose.ACTION_SIGNAL
        else None,
        signal_fn_name=signal_fn_name,
    )
    destination_dir = _instance_assets_dir(target_action_instance_id)
    destination_names: list[str] = []
    staged_items: list[tuple[Path, Path]] = []

    for ref in refs:
        storage_filename = _safe_filename(ref.filename)
        staged_path = _resolve_staged_path(scope_dir, storage_filename)
        if staged_path is None:
            raise ValueError(
                f'Asset filename {storage_filename} was not found in staged scope.'
            )
        original_filename = _extract_original_filename(storage_filename)
        destination = destination_dir / original_filename
        destination_names.append(original_filename)
        staged_items.append((staged_path, destination))

    if len(destination_names) != len(set(destination_names)):
        raise ValueError(
            'Input asset filenames must be unique per action instance after removing UUID prefixes.'
        )

    existing = [d.name for _, d in staged_items if d.exists()]
    if existing:
        raise ValueError(
            f'Cannot consume assets; destination files already exist: {", ".join(sorted(existing))}'
        )

    rollback_items: list[ConsumedAssetRollback] = []
    try:
        for staged_path, destination in staged_items:
            await asyncio.to_thread(_move_file, staged_path, destination)
            rollback_items.append(
                ConsumedAssetRollback(staged_path=staged_path, destination=destination)
            )
    except Exception:
        await rollback_consumed_assets(rollback_items)
        raise

    return rollback_items


async def rollback_consumed_assets(rollback_items: list[ConsumedAssetRollback]) -> None:
    """Best-effort rollback for partially consumed files.

    Moves each destination path back to its original staged path in reverse order.
    """

    for item in reversed(rollback_items):
        if await asyncio.to_thread(item.destination.exists):
            item.staged_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(_move_file, item.destination, item.staged_path)


def resolve_action_asset_path(
    asset_ref: ActionAssetRef,
    action_instance_id: str,
) -> Path:
    """Resolve a filename-based asset reference to an instance asset path.

    Raises:
        ValueError: If the file does not exist under the instance assets dir.
    """

    sanitized = _safe_filename(asset_ref.filename)
    assets_dir = _existing_instance_assets_dir(action_instance_id)
    for filename in dict.fromkeys([sanitized, _extract_original_filename(sanitized)]):
        path = assets_dir / filename
        if path.exists():
            return path
    raise ValueError(f'Asset file for {asset_ref.filename} was not found.')


def open_action_asset(
    asset_ref: ActionAssetRef,
    action_instance_id: str,
    mode: str = 'rb',
):
    """Open a resolved action-asset file handle for an action instance."""

    path = resolve_action_asset_path(asset_ref, action_instance_id)
    return path.open(mode)


def extract_action_asset_refs(data: Any) -> list[ActionAssetRef]:
    """Recursively collect unique ``ActionAssetRef`` objects from nested payloads.

    References are deduplicated by filename.
    """

    refs: list[ActionAssetRef] = []

    def _visit(value: Any):
        if isinstance(value, ActionAssetRef):
            refs.append(value)
            return

        if hasattr(value, 'model_dump'):
            _visit(value.model_dump(by_alias=True))
            return

        if isinstance(value, dict):
            if value.get('_nomad_type') == ACTION_ASSET_REF_TYPE:
                refs.append(ActionAssetRef.model_validate(value))
                return
            for item in value.values():
                _visit(item)
            return

        if isinstance(value, (list, tuple, set)):
            for item in value:
                _visit(item)

    _visit(data)

    return list({ref.filename: ref for ref in refs}.values())
