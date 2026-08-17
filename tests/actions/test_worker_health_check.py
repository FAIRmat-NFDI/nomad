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

import pytest
from aiohttp import ClientSession

from nomad.actions.workers.health_check import (
    should_start_health_server,
    start_health_server,
)
from nomad.config import config
from nomad.config.models.config import ModeEnum, WorkerConfig


@pytest.mark.asyncio
async def test_start_health_server_serves_health(unused_tcp_port):
    runner = await start_health_server('127.0.0.1', unused_tcp_port)

    try:
        async with ClientSession() as session:
            async with session.get(
                f'http://127.0.0.1:{unused_tcp_port}/health'
            ) as response:
                assert response.status == 200
                assert await response.text() == 'OK'
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_start_health_server_returns_503_when_worker_task_done(
    unused_tcp_port,
):
    async def dummy_worker():
        return

    worker_task = asyncio.create_task(dummy_worker())
    await worker_task

    runner = await start_health_server(
        '127.0.0.1', unused_tcp_port, worker_task=worker_task
    )

    try:
        async with ClientSession() as session:
            async with session.get(
                f'http://127.0.0.1:{unused_tcp_port}/health'
            ) as response:
                assert response.status == 503
                assert await response.text() == 'UNHEALTHY'
    finally:
        await runner.cleanup()


def test_should_start_health_server_respects_development_mode(monkeypatch):
    worker_config = WorkerConfig(healthcheck_enabled=True)

    monkeypatch.setattr(config.services, 'mode', ModeEnum.PRODUCTION)
    assert should_start_health_server(worker_config)

    monkeypatch.setattr(config.services, 'mode', ModeEnum.DEVELOPMENT)
    assert not should_start_health_server(worker_config)


def test_should_start_health_server_respects_worker_config(monkeypatch):
    monkeypatch.setattr(config.services, 'mode', ModeEnum.PRODUCTION)

    assert not should_start_health_server(WorkerConfig(healthcheck_enabled=False))
