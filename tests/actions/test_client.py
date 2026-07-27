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
from unittest.mock import AsyncMock

import pytest

from nomad.actions import client as client_module
from nomad.config import config
from nomad.config.models.config import ModeEnum


@pytest.mark.skip(reason='CI cannot connect for some reason.')
@pytest.mark.asyncio
async def test_get_client():
    client = await client_module.get_client()
    assert client is not None


class FakeRuntime:
    def __init__(self, *, telemetry, worker_heartbeat_interval=None):
        self.telemetry = telemetry
        self.worker_heartbeat_interval = worker_heartbeat_interval


@pytest.mark.asyncio
async def test_get_client_reuses_runtime(monkeypatch):
    created_runtimes = []
    connect = AsyncMock(side_effect=[object(), object()])

    def runtime_factory(*, telemetry, worker_heartbeat_interval=None):
        runtime = FakeRuntime(
            telemetry=telemetry,
            worker_heartbeat_interval=worker_heartbeat_interval,
        )
        created_runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(client_module, '_runtime', None)
    monkeypatch.setattr(client_module, 'Runtime', runtime_factory)
    monkeypatch.setattr(client_module.Client, 'connect', connect)
    monkeypatch.setattr(config.services, 'mode', ModeEnum.DEVELOPMENT)
    monkeypatch.setattr(config.telemetry.tracing, 'enabled', False)
    monkeypatch.setattr(config.meta, 'service', 'worker')
    monkeypatch.setattr(config.temporal, 'prometheus_bind_address', '127.0.0.1:9100')

    await client_module.get_client()
    await client_module.get_client()

    assert len(created_runtimes) == 1
    assert created_runtimes[0].telemetry.metrics.bind_address == '127.0.0.1:9100'
    assert connect.await_count == 2
    assert connect.await_args_list[0].kwargs['runtime'] is created_runtimes[0]
    assert connect.await_args_list[1].kwargs['runtime'] is created_runtimes[0]


@pytest.mark.asyncio
async def test_get_client_disables_prometheus_exporter_for_app(monkeypatch):
    created_runtimes = []
    connect = AsyncMock(return_value=object())

    def runtime_factory(*, telemetry, worker_heartbeat_interval=None):
        runtime = FakeRuntime(
            telemetry=telemetry,
            worker_heartbeat_interval=worker_heartbeat_interval,
        )
        created_runtimes.append(runtime)
        return runtime

    monkeypatch.setattr(client_module, '_runtime', None)
    monkeypatch.setattr(client_module, 'Runtime', runtime_factory)
    monkeypatch.setattr(client_module.Client, 'connect', connect)
    monkeypatch.setattr(config.services, 'mode', ModeEnum.DEVELOPMENT)
    monkeypatch.setattr(config.telemetry.tracing, 'enabled', False)
    monkeypatch.setattr(config.meta, 'service', 'app')
    monkeypatch.setattr(config.temporal, 'prometheus_bind_address', '127.0.0.1:9100')

    await client_module.get_client()

    assert len(created_runtimes) == 1
    assert created_runtimes[0].telemetry.metrics is None


@pytest.mark.asyncio
async def test_get_client_passes_api_key_and_tls_config_basic(monkeypatch):
    connect = AsyncMock(return_value=object())
    monkeypatch.setattr(client_module, '_runtime', object())
    monkeypatch.setattr(client_module.Client, 'connect', connect)
    monkeypatch.setattr(config.services, 'mode', ModeEnum.DEVELOPMENT)
    monkeypatch.setattr(config.telemetry.tracing, 'enabled', False)
    monkeypatch.setattr(config.temporal, 'api_key', 'my-secret-key')
    monkeypatch.setattr(config.temporal, 'use_tls', True)
    monkeypatch.setattr(config.temporal, 'tls_client_cert', None)
    monkeypatch.setattr(config.temporal, 'tls_client_key', None)
    monkeypatch.setattr(config.temporal, 'tls_server_root_ca_cert', None)
    monkeypatch.setattr(config.temporal, 'tls_domain', None)

    await client_module.get_client()

    assert connect.call_count == 1
    assert connect.call_args.kwargs['api_key'] == 'my-secret-key'
    assert connect.call_args.kwargs['tls'] is True


@pytest.mark.asyncio
async def test_get_client_passes_custom_tls_config(monkeypatch, tmp_path):
    cert_file = tmp_path / 'cert.pem'
    cert_file.write_bytes(b'cert_bytes')

    connect = AsyncMock(return_value=object())
    monkeypatch.setattr(client_module, '_runtime', object())
    monkeypatch.setattr(client_module.Client, 'connect', connect)
    monkeypatch.setattr(config.services, 'mode', ModeEnum.DEVELOPMENT)
    monkeypatch.setattr(config.telemetry.tracing, 'enabled', False)
    monkeypatch.setattr(config.temporal, 'api_key', None)
    monkeypatch.setattr(config.temporal, 'use_tls', False)
    monkeypatch.setattr(config.temporal, 'tls_client_cert', str(cert_file))
    monkeypatch.setattr(config.temporal, 'tls_client_key', 'raw_key_string')
    monkeypatch.setattr(config.temporal, 'tls_server_root_ca_cert', None)
    monkeypatch.setattr(config.temporal, 'tls_domain', 'temporal.example.com')

    await client_module.get_client()

    assert connect.call_count == 1
    tls_config = connect.call_args.kwargs['tls']
    from temporalio.client import TLSConfig

    assert isinstance(tls_config, TLSConfig)
    assert tls_config.client_cert == b'cert_bytes'
    assert tls_config.client_private_key == b'raw_key_string'
    assert tls_config.server_root_ca_cert is None
    assert tls_config.domain == 'temporal.example.com'


@pytest.mark.asyncio
async def test_get_client_uses_managed_oidc_token_and_plaintext_locally(monkeypatch):
    connect = AsyncMock(return_value=object())

    class FakeTokenManager:
        def __init__(self, settings):
            self.task = None

        async def initial_token(self):
            return 'oidc-token'

        def start(self, client):
            self.task = asyncio.create_task(asyncio.sleep(3600))
            return self.task

        async def stop(self):
            assert self.task is not None
            self.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await self.task

    monkeypatch.setattr(client_module, '_runtime', object())
    monkeypatch.setattr(client_module.Client, 'connect', connect)
    monkeypatch.setattr(client_module, 'OIDCTokenManager', FakeTokenManager)
    monkeypatch.setattr(config.services, 'mode', ModeEnum.DEVELOPMENT)
    monkeypatch.setattr(config.telemetry.tracing, 'enabled', False)
    monkeypatch.setattr(config.temporal, 'api_key', None)
    monkeypatch.setattr(config.temporal.oidc, 'enabled', True)
    monkeypatch.setattr(config.temporal, 'use_tls', False)
    monkeypatch.setattr(config.temporal, 'tls_client_cert', None)
    monkeypatch.setattr(config.temporal, 'tls_client_key', None)
    monkeypatch.setattr(config.temporal, 'tls_server_root_ca_cert', None)
    monkeypatch.setattr(config.temporal, 'tls_domain', None)

    try:
        first = await client_module.get_client()
        second = await client_module.get_client()

        assert first is second
        assert connect.await_count == 1
        assert connect.await_args.kwargs['api_key'] == 'oidc-token'
        assert connect.await_args.kwargs['tls'] is False
    finally:
        await client_module.close_client()


@pytest.mark.asyncio
async def test_get_client_rejects_oidc_plaintext_outside_development(monkeypatch):
    connect = AsyncMock(return_value=object())
    monkeypatch.setattr(client_module.Client, 'connect', connect)
    monkeypatch.setattr(config.services, 'mode', ModeEnum.PRODUCTION)
    monkeypatch.setattr(config.telemetry.tracing, 'enabled', False)
    monkeypatch.setattr(config.temporal.oidc, 'enabled', True)
    monkeypatch.setattr(config.temporal, 'use_tls', False)
    monkeypatch.setattr(config.temporal, 'tls_client_cert', None)
    monkeypatch.setattr(config.temporal, 'tls_client_key', None)
    monkeypatch.setattr(config.temporal, 'tls_server_root_ca_cert', None)
    monkeypatch.setattr(config.temporal, 'tls_domain', None)

    with pytest.raises(
        AssertionError,
        match='without TLS is only allowed in development mode',
    ):
        await client_module._connect_client('oidc-token')

    connect.assert_not_awaited()


def test_load_cert_or_key(tmp_path):
    # Test None/empty
    assert client_module._load_cert_or_key(None) is None
    assert client_module._load_cert_or_key('') is None

    # Test file path that exists
    f = tmp_path / 'test.key'
    f.write_text('file_contents', encoding='utf-8')
    assert client_module._load_cert_or_key(str(f)) == b'file_contents'

    directory = tmp_path / 'cert-dir'
    directory.mkdir()
    with pytest.raises(OSError):
        client_module._load_cert_or_key(str(directory))

    # Test raw PEM / string content
    raw_str = '-----BEGIN CERTIFICATE-----\nsomething\n-----END CERTIFICATE-----'
    assert client_module._load_cert_or_key(raw_str) == raw_str.encode('utf-8')
