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

import uuid
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from nomad.actions.assets.models import (
    ACTION_ASSET_REF_TYPE,
    ActionAssetPurpose,
    ActionAssetRef,
)
from nomad.actions.assets.service import (
    _assert_media_type_allowed,
    clone_action_asset,
    consume_staged_assets,
    extract_action_asset_refs,
    resolve_action_asset_path,
    rollback_consumed_assets,
    upload_action_asset,
)
from nomad.config import config
from nomad.config.models.config import ActionAssets
from nomad.mongo.action import ActionDocument


def _asset_ref(uploaded):
    return ActionAssetRef(
        nomad_type=ACTION_ASSET_REF_TYPE,
        filename=uploaded.filename,
        media_type=uploaded.media_type,
        size=uploaded.size,
        sha256=uploaded.sha256,
    )


def _assets_path(action_instance_id: str, filename: str | None = None) -> Path:
    path = Path(config.fs.actions) / action_instance_id / 'assets'
    return path / filename if filename else path


@pytest.mark.parametrize(
    'allowed, additional, media_type, accepted',
    [
        pytest.param(['text/plain'], [], 'text/plain', True, id='base-exact'),
        pytest.param(['image/*'], [], 'image/png', True, id='base-wildcard'),
        pytest.param(['*/*'], [], 'text/csv', True, id='base-all-types'),
        pytest.param(
            ['text/plain'], ['text/csv'], 'text/csv', True, id='additional-exact'
        ),
        pytest.param(
            ['text/plain'], ['image/*'], 'image/png', True, id='additional-wildcard'
        ),
        pytest.param(
            ['text/plain'], ['*/*'], 'text/csv', True, id='additional-all-types'
        ),
        pytest.param(
            ['text/plain'], ['text/csv'], 'text/plain', True, id='base-retained'
        ),
        pytest.param(
            ['text/plain'], ['text/plain'], 'text/plain', True, id='duplicate'
        ),
        pytest.param([], ['text/csv'], 'text/csv', True, id='empty-base'),
        pytest.param(
            ['text/plain'], ['text/csv'], 'image/png', False, id='removed-default'
        ),
        pytest.param(
            ['text/plain'], ['image/*'], 'video/mp4', False, id='unlisted-type'
        ),
        pytest.param([], [], 'text/plain', False, id='empty-lists'),
    ],
)
def test_action_asset_media_type_allowlist(
    allowed, additional, media_type, accepted, monkeypatch
):
    monkeypatch.setattr(
        config.actions,
        'action_assets',
        ActionAssets(
            allowed_media_types=allowed, additional_allowed_media_types=additional
        ),
    )

    if accepted:
        _assert_media_type_allowed(media_type)
    else:
        with pytest.raises(HTTPException) as exc:
            _assert_media_type_allowed(media_type)
        assert exc.value.status_code == 400
        assert exc.value.detail == f'Unsupported media type: {media_type}'

    assert config.actions.action_assets.allowed_media_types == allowed
    assert config.actions.action_assets.additional_allowed_media_types == additional


@pytest.mark.asyncio
async def test_upload_and_consume_action_start_filesystem_only(
    mongo_function, async_mongo_function, user1, raw_files_function
):
    upload = UploadFile(
        filename='recording.webm',
        file=BytesIO(b'audio-data'),
    )

    uploaded = await upload_action_asset(
        user_id=user1.user_id,
        upload_file=upload,
        purpose=ActionAssetPurpose.ACTION_START,
        action_id='my-action',
        expected_media_type='audio/webm',
    )

    refs = [_asset_ref(uploaded)]
    workflow_id = f'workflow-{uuid.uuid4().hex}'

    await consume_staged_assets(
        refs=refs,
        user_id=user1.user_id,
        purpose=ActionAssetPurpose.ACTION_START,
        target_action_instance_id=workflow_id,
        action_id='my-action',
    )

    assets_path = _assets_path(workflow_id, 'recording.webm')
    assert assets_path.exists()
    assert assets_path.read_bytes() == b'audio-data'

    resolved = resolve_action_asset_path(refs[0], workflow_id)
    assert resolved == assets_path


@pytest.mark.asyncio
async def test_upload_rejects_path_traversal_scope_components(
    mongo_function, async_mongo_function, user1, raw_files_function
):
    upload = UploadFile(
        filename='recording.webm',
        file=BytesIO(b'audio-data'),
    )

    with pytest.raises(HTTPException) as exc:
        await upload_action_asset(
            user_id=user1.user_id,
            upload_file=upload,
            purpose=ActionAssetPurpose.ACTION_START,
            action_id='../../outside',
            expected_media_type='audio/webm',
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == 'Invalid action_id.'


@pytest.mark.asyncio
async def test_rollback_consumed_assets_moves_file_back(
    mongo_function, async_mongo_function, user1, raw_files_function
):
    upload = UploadFile(
        filename='signal.json',
        file=BytesIO(b'{}'),
    )

    uploaded = await upload_action_asset(
        user_id=user1.user_id,
        upload_file=upload,
        purpose=ActionAssetPurpose.ACTION_SIGNAL,
        action_instance_id='workflow-2',
        signal_fn_name='test_signal',
        expected_media_type='application/octet-stream',
    )

    refs = [_asset_ref(uploaded)]

    rollback_items = await consume_staged_assets(
        refs=refs,
        user_id=user1.user_id,
        purpose=ActionAssetPurpose.ACTION_SIGNAL,
        target_action_instance_id='workflow-2',
        signal_fn_name='test_signal',
    )

    destination = _assets_path('workflow-2', 'signal.json')
    assert destination.exists()

    await rollback_consumed_assets(rollback_items)

    staged_scope = (
        Path(config.fs.tmp)
        / 'tmp_action_asset'
        / 'action_signal'
        / user1.user_id
        / 'workflow-2'
        / 'test_signal'
    )
    staged = staged_scope / uploaded.filename
    assert staged.exists()
    assert not destination.exists()


@pytest.mark.asyncio
async def test_clone_action_asset_from_action_assets(
    mongo_function, async_mongo_function, user1, raw_files_function
):
    await ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-source',
        user_id=user1.user_id,
        status='COMPLETED',
        input_data={},
    ).insert()

    source_dir = _assets_path('workflow-source')
    source_dir.mkdir(parents=True, exist_ok=True)
    source_file = source_dir / 'recording.webm'
    source_file.write_bytes(b'from-source')

    cloned = await clone_action_asset(
        user_id=user1.user_id,
        source_filename='recording.webm',
        purpose=ActionAssetPurpose.ACTION_START,
        action_id='my-action',
        source_action_instance_id='workflow-source',
    )
    assert cloned.media_type in {'video/webm', 'application/octet-stream'}

    refs = [_asset_ref(cloned)]
    target_workflow_id = f'workflow-target-{uuid.uuid4().hex}'

    await consume_staged_assets(
        refs=refs,
        user_id=user1.user_id,
        purpose=ActionAssetPurpose.ACTION_START,
        target_action_instance_id=target_workflow_id,
        action_id='my-action',
    )

    target = _assets_path(target_workflow_id, 'recording.webm')
    assert target.exists()
    assert target.read_bytes() == b'from-source'


@pytest.mark.asyncio
async def test_clone_action_asset_rejects_foreign_action_assets(
    mongo_function, async_mongo_function, user1, raw_files_function
):
    await ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-source-foreign',
        user_id='another-user',
        status='COMPLETED',
        input_data={},
    ).insert()

    source_dir = _assets_path('workflow-source-foreign')
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / 'recording.webm').write_bytes(b'foreign-data')

    with pytest.raises(HTTPException) as exc:
        await clone_action_asset(
            user_id=user1.user_id,
            source_filename='recording.webm',
            purpose=ActionAssetPurpose.ACTION_START,
            action_id='my-action',
            source_action_instance_id='workflow-source-foreign',
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == 'Source action was not found for the user.'


def test_extract_action_asset_refs_ignores_shape_collisions():
    payload = {
        'domain_object': {
            'filename': 'not-an-action-asset.txt',
            'media_type': 'application/json',
            'size': 12,
            'other_field': 'keeps this from being interpreted as action asset ref',
        },
        'real_asset_ref': {
            '_nomad_type': ACTION_ASSET_REF_TYPE,
            'filename': 'ast_123.webm',
            'media_type': 'audio/webm',
            'size': 12,
            'sha256': 'abc',
        },
    }
    refs = extract_action_asset_refs(payload)
    assert len(refs) == 1
    assert refs[0].filename == 'ast_123.webm'


@pytest.mark.asyncio
async def test_upload_enforces_user_quota_from_filesystem(
    mongo_function, async_mongo_function, user1, monkeypatch, raw_files_function
):
    monkeypatch.setattr(config.actions.action_assets, 'per_user_quota_bytes', 10)

    upload1 = UploadFile(
        filename='first.bin',
        file=BytesIO(b'12345678'),
    )
    await upload_action_asset(
        user_id=user1.user_id,
        upload_file=upload1,
        purpose=ActionAssetPurpose.ACTION_START,
        action_id='my-action',
        expected_media_type='application/octet-stream',
    )

    upload2 = UploadFile(
        filename='second.bin',
        file=BytesIO(b'1234'),
    )
    with pytest.raises(HTTPException) as exc:
        await upload_action_asset(
            user_id=user1.user_id,
            upload_file=upload2,
            purpose=ActionAssetPurpose.ACTION_START,
            action_id='my-action',
            expected_media_type='application/octet-stream',
        )
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_upload_quota_counts_non_staged_action_assets(
    mongo_function, async_mongo_function, user1, monkeypatch, raw_files_function
):
    monkeypatch.setattr(config.actions.action_assets, 'per_user_quota_bytes', 10)

    await ActionDocument(
        action_id='my-action',
        action_instance_id='workflow-with-generated-files',
        user_id=user1.user_id,
        status='COMPLETED',
        input_data={},
    ).insert()
    assets_dir = _assets_path('workflow-with-generated-files')
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / 'huge-generated-output.bin').write_bytes(b'X' * 1024)

    upload = UploadFile(
        filename='small.bin',
        file=BytesIO(b'12345678'),
    )
    with pytest.raises(HTTPException) as exc:
        await upload_action_asset(
            user_id=user1.user_id,
            upload_file=upload,
            purpose=ActionAssetPurpose.ACTION_START,
            action_id='my-action',
            expected_media_type='application/octet-stream',
        )
    assert exc.value.status_code == 413
