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

import pytest
from pydantic import SecretStr
from temporalio.api.common.v1 import Payload

from nomad.actions._codec import EncryptionCodec
from nomad.config import config
from nomad.config.models.config import ModeEnum


@pytest.fixture
def codec_config(monkeypatch):
    monkeypatch.setattr(config.services, 'mode', ModeEnum.DEVELOPMENT)
    monkeypatch.setattr(
        config.services,
        'api_secret',
        SecretStr('local-secret-that-is-at-least-32-bytes'),
    )
    monkeypatch.setattr(config.temporal, 'payload_codec_key', None)
    monkeypatch.setattr(config.temporal, 'payload_codec_key_id', 'default')


@pytest.mark.asyncio
async def test_configured_codec_key_is_recorded_and_used(codec_config, monkeypatch):
    monkeypatch.setattr(
        config.temporal,
        'payload_codec_key',
        SecretStr('federation-secret-that-is-at-least-32-bytes'),
    )
    monkeypatch.setattr(config.temporal, 'payload_codec_key_id', 'federation-v1')
    codec = EncryptionCodec()
    payload = Payload(metadata={'encoding': b'json/plain'}, data=b'input')

    encoded = await codec.encode([payload])

    assert encoded[0].metadata['encryption-key-id'] == b'federation-v1'
    assert await codec.decode(encoded) == [payload]


@pytest.mark.asyncio
async def test_configured_codec_key_can_decode_default_payload(
    codec_config, monkeypatch
):
    payload = Payload(metadata={'encoding': b'json/plain'}, data=b'input')
    default_payload = (await EncryptionCodec().encode([payload]))[0]
    assert 'encryption-key-id' not in default_payload.metadata

    monkeypatch.setattr(
        config.temporal,
        'payload_codec_key',
        SecretStr('federation-secret-that-is-at-least-32-bytes'),
    )
    monkeypatch.setattr(config.temporal, 'payload_codec_key_id', 'federation-v1')

    assert await EncryptionCodec().decode([default_payload]) == [payload]


@pytest.mark.asyncio
async def test_unknown_codec_key_id_is_rejected(codec_config, monkeypatch):
    monkeypatch.setattr(
        config.temporal,
        'payload_codec_key',
        SecretStr('federation-secret-that-is-at-least-32-bytes'),
    )
    monkeypatch.setattr(config.temporal, 'payload_codec_key_id', 'federation-v1')
    codec = EncryptionCodec()
    encoded = await codec.encode([Payload(data=b'input')])
    encoded[0].metadata['encryption-key-id'] = b'federation-v2'

    with pytest.raises(ValueError, match="key ID 'federation-v2'"):
        await codec.decode(encoded)
