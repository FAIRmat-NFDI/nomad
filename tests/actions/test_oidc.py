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
import time
from unittest.mock import AsyncMock

import httpx2
import pytest

from nomad.actions import oidc as oidc_module
from nomad.actions.oidc import OIDCAccessToken, OIDCTokenManager
from nomad.config.models.config import Temporal, TemporalOIDC


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


class FakeHTTPClient:
    def __init__(self, response, **kwargs):
        self.response = response
        self.kwargs = kwargs
        self.post = AsyncMock(return_value=response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None


@pytest.mark.asyncio
async def test_fetch_oidc_access_token_uses_client_credentials(monkeypatch):
    response = FakeResponse({'access_token': 'token-1', 'expires_in': 60})
    fake_client = FakeHTTPClient(response)
    monkeypatch.setattr(
        oidc_module.httpx2,
        'AsyncClient',
        lambda **kwargs: fake_client,
    )
    settings = TemporalOIDC(
        enabled=True,
        token_url='https://keycloak/realms/oasis-b/token',
        client_id='temporal-oasis-b',
        client_secret='secret',
        scope='temporal',
    )

    before = time.monotonic()
    token = await oidc_module.fetch_oidc_access_token(settings)

    assert token.value == 'token-1'
    assert token.expires_at >= before + 60
    fake_client.post.assert_awaited_once()
    call = fake_client.post.await_args
    assert call.args == ('https://keycloak/realms/oasis-b/token',)
    assert call.kwargs['data'] == {
        'grant_type': 'client_credentials',
        'scope': 'temporal',
    }
    assert isinstance(call.kwargs['auth'], httpx2.BasicAuth)


@pytest.mark.asyncio
async def test_fetch_oidc_access_token_rejects_invalid_response(monkeypatch):
    fake_client = FakeHTTPClient(FakeResponse({'expires_in': 60}))
    monkeypatch.setattr(
        oidc_module.httpx2,
        'AsyncClient',
        lambda **kwargs: fake_client,
    )
    settings = TemporalOIDC(
        enabled=True,
        token_url='https://keycloak/token',
        client_id='client',
        client_secret='secret',
    )

    with pytest.raises(ValueError, match='access_token'):
        await oidc_module.fetch_oidc_access_token(settings)


@pytest.mark.asyncio
async def test_token_manager_updates_existing_temporal_client():
    refreshed = asyncio.Event()
    tokens = iter(
        [
            OIDCAccessToken('token-1', time.monotonic() + 0.05),
            OIDCAccessToken('token-2', time.monotonic() + 60),
        ]
    )

    async def fetch_token():
        token = next(tokens)
        if token.value == 'token-2':
            refreshed.set()
        return token

    settings = TemporalOIDC(
        enabled=True,
        token_url='https://keycloak/token',
        client_id='client',
        client_secret='secret',
        refresh_margin=0,
    )
    manager = OIDCTokenManager(settings, token_fetcher=fetch_token)
    client = type('FakeClient', (), {'api_key': None})()

    assert await manager.initial_token() == 'token-1'
    manager.start(client)  # type: ignore[arg-type]
    try:
        await asyncio.wait_for(refreshed.wait(), timeout=1)
        assert client.api_key == 'token-2'
    finally:
        await manager.stop()


def test_temporal_oidc_requires_complete_client_credentials():
    with pytest.raises(ValueError, match='token_url, client_id, client_secret'):
        TemporalOIDC(enabled=True)


def test_temporal_rejects_static_api_key_with_oidc():
    with pytest.raises(ValueError, match='either temporal.api_key or temporal.oidc'):
        Temporal(
            api_key='static-key',
            oidc={
                'enabled': True,
                'token_url': 'https://keycloak/token',
                'client_id': 'client',
                'client_secret': 'secret',
            },
        )
