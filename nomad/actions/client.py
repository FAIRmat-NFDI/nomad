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
import threading
import weakref
from dataclasses import dataclass

import temporalio.converter
from temporalio.client import Client, TLSConfig
from temporalio.contrib.opentelemetry import OpenTelemetryPlugin
from temporalio.contrib.pydantic import PydanticPayloadConverter
from temporalio.runtime import PrometheusConfig, Runtime, TelemetryConfig

from nomad.actions._codec import EncryptionCodec
from nomad.actions.oidc import OIDCTokenManager
from nomad.config import config
from nomad.config.models.config import ModeEnum

_runtime: Runtime | None = None
_runtime_lock = threading.Lock()


@dataclass
class _ManagedOIDCClient:
    client: Client
    token_manager: OIDCTokenManager
    refresh_task: asyncio.Task[None]


_oidc_clients: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, _ManagedOIDCClient
] = weakref.WeakKeyDictionary()
_oidc_creation_locks: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Lock
] = weakref.WeakKeyDictionary()
_oidc_clients_lock = threading.Lock()


def _get_metrics_config() -> PrometheusConfig | None:
    bind_address = config.temporal.prometheus_bind_address
    if bind_address is None:
        return None

    # The Temporal Prometheus exporter binds a process-local HTTP server.
    # The web app may run multiple Gunicorn workers in one deployment, so the
    # dedicated Temporal workers are the safer place to expose this endpoint.
    if config.meta.service == 'app':
        return None

    return PrometheusConfig(bind_address=bind_address)


def _get_runtime() -> Runtime:
    global _runtime

    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = Runtime(
                    telemetry=TelemetryConfig(metrics=_get_metrics_config()),
                )

    return _runtime


def _load_cert_or_key(val: str | None) -> bytes | None:
    if not val:
        return None
    import os

    if os.path.exists(val):
        with open(val, 'rb') as f:
            return f.read()
    return val.encode('utf-8')


def _get_oidc_creation_lock(loop: asyncio.AbstractEventLoop) -> asyncio.Lock:
    with _oidc_clients_lock:
        lock = _oidc_creation_locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            _oidc_creation_locks[loop] = lock
        return lock


def _get_managed_oidc_client(
    loop: asyncio.AbstractEventLoop,
) -> _ManagedOIDCClient | None:
    with _oidc_clients_lock:
        managed = _oidc_clients.get(loop)
        if managed is not None and managed.refresh_task.done():
            _oidc_clients.pop(loop, None)
            return None
        return managed


def _forget_managed_oidc_client(
    loop_ref: weakref.ReferenceType[asyncio.AbstractEventLoop],
    refresh_task: asyncio.Task[None],
) -> None:
    loop = loop_ref()
    if loop is None:
        return
    with _oidc_clients_lock:
        managed = _oidc_clients.get(loop)
        if managed is not None and managed.refresh_task is refresh_task:
            _oidc_clients.pop(loop, None)


async def close_client() -> None:
    """Stop OIDC credential renewal for the client owned by this event loop."""
    loop = asyncio.get_running_loop()
    with _oidc_clients_lock:
        managed = _oidc_clients.pop(loop, None)
        _oidc_creation_locks.pop(loop, None)
    if managed is not None:
        await managed.token_manager.stop()


async def _connect_client(api_key: str | None) -> Client:
    # Ensure telemetry is initialized in this process before Temporal plugin checks
    # the global tracer provider type.
    if config.telemetry.tracing.enabled:
        from nomad.tracing import setup_tracing

        setup_tracing()

    host = f'{config.temporal.host}:{config.temporal.port}'
    tls_configured = bool(
        config.temporal.use_tls
        or config.temporal.tls_client_cert
        or config.temporal.tls_client_key
        or config.temporal.tls_server_root_ca_cert
        or config.temporal.tls_domain
    )
    if config.temporal.oidc.enabled and not tls_configured:
        assert config.services.mode == ModeEnum.DEVELOPMENT, (
            'Temporal OIDC authentication without TLS is only allowed in development mode.'
        )

    data_converter = temporalio.converter.DataConverter(
        payload_converter_class=PydanticPayloadConverter,
        # Development deployments remain unencrypted unless an explicit
        # Temporal payload key is configured (needed for cross-Oasis Nexus).
        payload_codec=(
            EncryptionCodec()
            if (
                config.services.mode != ModeEnum.DEVELOPMENT
                or config.temporal.payload_codec_key
            )
            else None
        ),
    )
    plugins = []
    if config.telemetry.tracing.enabled:
        plugins.append(OpenTelemetryPlugin(add_temporal_spans=True))

    # The SDK enables TLS automatically when an API key is supplied. Explicitly
    # disable that behavior for an OIDC-enabled local development deployment that
    # has not enabled TLS. Production federation deployments should enable TLS.
    tls: bool | TLSConfig | None = False if config.temporal.oidc.enabled else None
    if tls_configured:
        if (
            config.temporal.tls_client_cert
            or config.temporal.tls_client_key
            or config.temporal.tls_server_root_ca_cert
            or config.temporal.tls_domain
        ):
            tls = TLSConfig(
                client_cert=_load_cert_or_key(config.temporal.tls_client_cert),
                client_private_key=_load_cert_or_key(
                    config.temporal.tls_client_key.get_secret_value()
                    if config.temporal.tls_client_key
                    else None
                ),
                server_root_ca_cert=_load_cert_or_key(
                    config.temporal.tls_server_root_ca_cert
                ),
                domain=config.temporal.tls_domain,
            )
        else:
            tls = True

    client = await Client.connect(
        host,
        namespace=config.temporal.namespace,
        api_key=api_key,
        tls=tls,
        data_converter=data_converter,
        runtime=_get_runtime(),
        plugins=plugins,
    )
    return client


async def get_client() -> Client:
    if not config.temporal.oidc.enabled:
        return await _connect_client(
            config.temporal.api_key.get_secret_value()
            if config.temporal.api_key
            else None
        )

    loop = asyncio.get_running_loop()
    managed = _get_managed_oidc_client(loop)
    if managed is not None:
        return managed.client

    async with _get_oidc_creation_lock(loop):
        managed = _get_managed_oidc_client(loop)
        if managed is not None:
            return managed.client

        token_manager = OIDCTokenManager(config.temporal.oidc)
        token = await token_manager.initial_token()
        client = await _connect_client(token)
        refresh_task = token_manager.start(client)
        managed = _ManagedOIDCClient(client, token_manager, refresh_task)
        with _oidc_clients_lock:
            _oidc_clients[loop] = managed
        loop_ref = weakref.ref(loop)

        def forget_client(task: asyncio.Task[None]) -> None:
            _forget_managed_oidc_client(loop_ref, task)

        refresh_task.add_done_callback(forget_client)
        return client
