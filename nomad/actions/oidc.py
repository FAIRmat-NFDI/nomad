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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx2
from temporalio.client import Client

from nomad.config.models.config import TemporalOIDC
from nomad.utils.structlogging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class OIDCAccessToken:
    value: str
    expires_at: float


TokenFetcher = Callable[[], Awaitable[OIDCAccessToken]]


async def fetch_oidc_access_token(settings: TemporalOIDC) -> OIDCAccessToken:
    """Obtain a short-lived access token using OAuth client credentials."""
    assert settings.token_url is not None
    assert settings.client_id is not None
    assert settings.client_secret is not None

    data = {'grant_type': 'client_credentials'}
    if settings.scope:
        data['scope'] = settings.scope

    async with httpx2.AsyncClient(timeout=settings.request_timeout) as client:
        response = await client.post(
            settings.token_url,
            data=data,
            auth=httpx2.BasicAuth(
                settings.client_id, settings.client_secret.get_secret_value()
            ),
        )
        response.raise_for_status()
        body = response.json()

    access_token = body.get('access_token')
    if not isinstance(access_token, str) or not access_token:
        raise ValueError('OIDC token response did not contain an access_token')

    try:
        expires_in = float(body['expires_in'])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            'OIDC token response did not contain a valid expires_in value'
        ) from exc
    if expires_in <= 0:
        raise ValueError('OIDC access token must have a positive lifetime')

    return OIDCAccessToken(
        value=access_token,
        expires_at=time.monotonic() + expires_in,
    )


class OIDCTokenManager:
    """Keep the bearer token used by a Temporal client fresh."""

    def __init__(
        self,
        settings: TemporalOIDC,
        token_fetcher: TokenFetcher | None = None,
    ) -> None:
        self._settings = settings
        self._token_fetcher = token_fetcher or (
            lambda: fetch_oidc_access_token(self._settings)
        )
        self._token: OIDCAccessToken | None = None
        self._task: asyncio.Task[None] | None = None

    async def initial_token(self) -> str:
        self._token = await self._token_fetcher()
        return self._token.value

    def start(self, client: Client) -> asyncio.Task[None]:
        if self._token is None:
            raise RuntimeError('An initial OIDC token must be obtained before refresh')
        if self._task is not None and not self._task.done():
            raise RuntimeError('OIDC token refresh is already running')

        self._task = asyncio.create_task(
            self._refresh_loop(client), name='temporal-oidc-token-refresh'
        )
        return self._task

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def _refresh_delay(self) -> float:
        assert self._token is not None
        remaining = max(0.0, self._token.expires_at - time.monotonic())
        # Never consume more than half of a short token's lifetime as margin.
        margin = min(self._settings.refresh_margin, remaining / 2)
        return max(0.0, remaining - margin)

    async def _refresh_loop(self, client: Client) -> None:
        while True:
            await asyncio.sleep(self._refresh_delay())

            attempt = 0
            while True:
                try:
                    token = await self._token_fetcher()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    attempt += 1
                    retry_delay = min(30.0, float(2 ** min(attempt - 1, 5)))
                    logger.warning(
                        'Could not renew the Temporal OIDC access token; retrying.',
                        exc_info=exc,
                    )
                    await asyncio.sleep(retry_delay)
                    continue

                client.api_key = token.value
                self._token = token
                logger.info('Renewed the Temporal OIDC access token.')
                break
