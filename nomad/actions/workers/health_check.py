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

from aiohttp import web

from nomad.config import config
from nomad.config.models.config import ModeEnum, WorkerConfig


def should_start_health_server(worker_config: WorkerConfig) -> bool:
    return (
        worker_config.healthcheck_enabled
        and config.services.mode != ModeEnum.DEVELOPMENT
    )


# There's no built in health check in Temporal, but we can use the following endpoint to setup
# a healthcheck and then Docker/Kubernetes would detect worker failures/crashes and restart the container/pod.
# This approach is suggested in https://temporal.io/blog/deploying-temporal-workers-to-amazon-ecs
async def start_health_server(
    host: str, port: int, worker_task: asyncio.Task | None = None
) -> web.AppRunner:
    async def handle_health(_):
        if worker_task is not None and worker_task.done():
            return web.Response(status=503, text='UNHEALTHY')
        return web.Response(text='OK')

    app = web.Application()
    app.router.add_get('/health', handle_health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, host, port)
    await site.start()

    return runner
