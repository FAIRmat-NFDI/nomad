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

import os
from collections.abc import Callable, Mapping
from typing import Any

import click

from nomad.cli.cli import config_file_option
from nomad.config import config
from nomad.config.models.config import WorkerConfig

from .admin import admin


@admin.group(help='Run a nomad service locally (outside docker).')
@config_file_option
def run():
    # The config files are applied by `nomad.cli.config_files` before any nomad module
    # is imported, they cannot be applied from here.
    pass


@run.command(help='Run the jupyter hub.')
@config_file_option
def hub():
    run_hub()


@run.command(help='Run the action cpu worker.')
@click.option(
    '--pool-size', '--workers', type=int, default=None, help='Number of workers.'
)
@config_file_option
def action_cpu_worker(pool_size: int | None):
    import asyncio

    from nomad.actions.workers import cpu

    config_dict = config.temporal.cpu_worker.model_dump()
    if pool_size is not None:
        config_dict['pool_size'] = pool_size

    worker_config = WorkerConfig.model_validate(config_dict)

    asyncio.run(cpu.run_worker(worker_config))


@run.command(help='Run the action gpu worker.')
@click.option(
    '--pool-size', '--workers', type=int, default=None, help='Number of workers.'
)
@config_file_option
def action_gpu_worker(pool_size: int | None):
    import asyncio

    from nomad.actions.workers import gpu

    config_dict = config.temporal.gpu_worker.model_dump()
    if pool_size is not None:
        config_dict['pool_size'] = pool_size

    worker_config = WorkerConfig.model_validate(config_dict)

    asyncio.run(gpu.run_worker(worker_config))


@run.command(help='Run the action internal worker.')
@click.option(
    '--pool-size', '--workers', type=int, default=None, help='Number of workers.'
)
@click.option(
    '--max-tasks-per-child',
    type=int,
    default=None,
    help='Number of tasks per worker.',
)
@click.option(
    '--max-concurrent-activities',
    type=int,
    default=None,
    help='Maximum number of concurrent activities for the internal worker.',
)
@config_file_option
def action_internal_worker(
    pool_size: int | None,
    max_tasks_per_child: int | None,
    max_concurrent_activities: int | None,
):
    run_action_internal_worker(
        pool_size=pool_size,
        max_tasks_per_child=max_tasks_per_child,
        max_concurrent_activities=max_concurrent_activities,
    )


@run.command(help='Run the action internal worker.')
@click.option(
    '--pool-size', '--workers', type=int, default=None, help='Number of workers.'
)
@click.option(
    '--max-tasks-per-child',
    type=int,
    default=None,
    help='Number of tasks per worker.',
)
@click.option(
    '--max-concurrent-activities',
    type=int,
    default=None,
    help='Maximum number of concurrent activities for the internal worker.',
)
@config_file_option
def worker(
    pool_size: int | None,
    max_tasks_per_child: int | None,
    max_concurrent_activities: int | None,
):
    run_action_internal_worker(
        pool_size=pool_size,
        max_tasks_per_child=max_tasks_per_child,
        max_concurrent_activities=max_concurrent_activities,
    )


@run.command(help='Run the nomad development app with all apis.')
@click.option(
    '--with-gui',
    help='The app will configure the gui for production and service it.',
    is_flag=True,
)
@click.option('--host', type=str, help='Passed as host parameter.')
@click.option('--port', type=int, help='Passed as port parameter.')
@click.option('--log-config', type=str, help='Passed as log-config parameter.')
@click.option(
    '--gunicorn',
    is_flag=True,
    type=bool,
    help='Run app with gunicorn instead of uvicorn.',
)
@click.option('--workers', type=int, help='Passed to uvicorn workers parameter.')
@config_file_option
def app(with_gui: bool, **kwargs):
    run_app(with_gui=with_gui, **kwargs)


def run_action_internal_worker(
    *,
    pool_size: int | None = None,
    max_tasks_per_child: int | None = None,
    max_concurrent_activities: int | None = None,
):
    import asyncio

    from nomad.actions.workers import internal_worker

    config_dict = config.temporal.internal_worker.model_dump()
    if pool_size is not None:
        config_dict['pool_size'] = pool_size
    if max_tasks_per_child is not None:
        config_dict['max_tasks_per_child'] = max_tasks_per_child
    if max_concurrent_activities is not None:
        config_dict['max_concurrent_activities'] = max_concurrent_activities

    worker_config = WorkerConfig.model_validate(config_dict)

    asyncio.run(internal_worker.run_worker(worker_config))


def run_app(
    *,
    with_gui: bool = False,
    gunicorn: bool = False,
    host: str | None = None,
    log_config: str | None = None,
    port: int | None = None,
    **kwargs,
):
    config.meta.service = 'app'
    host = host or config.services.api_host or '0.0.0.0'
    # TODO: respect `config.services.api_port` instead of defaulting to 8000
    # port = int(port or config.services.api_port or 8000)
    port = int(port or 8000)

    if with_gui:
        import glob
        import shutil

        gui_folder = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '../../app/static/gui')
        )
        run_gui_folder = os.path.join(
            config.fs.working_directory, 'run', 'gui_configured'
        )
        if not os.path.exists(run_gui_folder):
            os.makedirs(run_gui_folder)

        # copy
        shutil.rmtree(run_gui_folder, ignore_errors=True)
        shutil.copytree(gui_folder, run_gui_folder)

        # replace base path in all GUI files
        source_file_globs = [
            '**/*.json',
            '**/*.html',
            '**/*.js',
            '**/*.js.map',
            '**/*.css',
        ]
        for source_file_glob in source_file_globs:
            source_files = glob.glob(
                os.path.join(run_gui_folder, source_file_glob), recursive=True
            )
            for source_file in source_files:
                with open(source_file) as f:
                    file_data = f.read()
                file_data = file_data.replace(
                    '/nomad-oasis', config.services.route_prefix
                )
                with open(source_file, 'w') as f:
                    f.write(file_data)

        # App and gui are served from the same server, same port. Replace the base urls with
        # relative paths
        config.ui.app_base = config.services.route_prefix
        config.ui.north_base = config.services.join_path('north')

    from nomad.utils import get_logger

    if gunicorn:
        import logging.config

        from gunicorn.app.wsgiapp import WSGIApplication

        if log_config:
            logging.config.fileConfig(log_config)

        if not kwargs.get('workers', None):
            kwargs['workers'] = 4

        class App(WSGIApplication):
            def __init__(self):
                self.app_uri = 'nomad.app.main:app'
                super().__init__()

            def load_config(self):
                self.cfg.set('timeout', config.services.api_timeout)
                self.cfg.set('worker_class', 'uvicorn.workers.UvicornWorker')
                self.cfg.set('bind', f'{host}:{port}')
                # Trust X-Forwarded-* from any upstream. The app is expected
                # to run behind a reverse proxy / ingress that terminates TLS;
                # without this, scope['scheme'] stays 'http' and absolute URLs
                # (e.g. the slash-redirect target) are emitted as http://.
                self.cfg.set('forwarded_allow_ips', '*')

                if config.telemetry.metrics.api_prometheus_enabled:
                    import shutil

                    multiproc_dir = os.path.join(
                        config.fs.local_tmp, 'prometheus_multiproc'
                    )
                    os.environ['PROMETHEUS_MULTIPROC_DIR'] = multiproc_dir

                    from prometheus_client import multiprocess

                    def on_starting(server):
                        if os.path.exists(multiproc_dir):
                            shutil.rmtree(multiproc_dir)
                        os.makedirs(multiproc_dir, exist_ok=True)

                    def child_exit(server, worker):
                        multiprocess.mark_process_dead(worker.pid)

                    self.cfg.set('on_starting', on_starting)
                    self.cfg.set('child_exit', child_exit)

                for key, value in kwargs.items():
                    if key in self.cfg.settings and value is not None:
                        self.cfg.set(key, value)

        gunicorn_app = App()
        get_logger(__name__).info('created gunicorn server', data=str(gunicorn_app.cfg))
        gunicorn_app.run()
    else:
        from uvicorn import Config, Server

        kwargs['log_config'] = log_config

        uv_config = Config(
            'nomad.app.main:app',
            log_level='info',
            host=host,
            port=port,
            proxy_headers=True,
            forwarded_allow_ips='*',
            **{k: v for k, v in kwargs.items() if v is not None},
        )

        server = Server(config=uv_config)
        get_logger(__name__).info('created uvicorn server', data=uv_config.__dict__)
        server.run()


def run_hub():
    import os
    import subprocess
    import sys

    from jupyterhub.app import main

    if 'JUPYTERHUB_CRYPT_KEY' not in os.environ:
        crypt_key = config.north.jupyterhub_crypt_key
        if crypt_key is None:
            crypt_key = (
                subprocess.check_output('openssl rand -hex 32'.split(' '))
                .decode()
                .strip('\n')
            )
        os.environ['JUPYTERHUB_CRYPT_KEY'] = crypt_key

    config.meta.service = 'hub'
    sys.exit(
        main(argv=['-f', 'nomad/jupyterhub_config.py', '--Application.log_level=INFO'])
    )


def run_appworker(
    *,
    app_host: str | None = None,
    app_port: int | None = None,
    fastapi_workers: int | None = None,
    temporal_pool_size: int | None = None,
    dev: bool = False,
):
    import multiprocessing
    import sys
    import time

    if dev:
        fastapi_workers = 1
        temporal_pool_size = 1

    tasks: list[tuple[Callable[..., Any], Mapping[str, Any]]] = [
        (run_action_internal_worker, {'pool_size': temporal_pool_size}),
        (run_app, {'workers': fastapi_workers, 'host': app_host, 'port': app_port}),
    ]

    processes = []

    for target, kwargs in tasks:
        p = multiprocessing.Process(target=target, kwargs=kwargs)
        p.start()
        processes.append(p)

    try:
        while True:
            for p in processes:
                # Exit if any process died with an error code
                if not p.is_alive() and p.exitcode != 0:
                    # Kill the other process
                    for other_p in processes:
                        other_p.terminate()
                    sys.exit(p.exitcode)
            time.sleep(5)

    except KeyboardInterrupt:
        for p in processes:
            p.terminate()
        sys.exit(0)


@run.command(help='Run both app and worker.')
@click.option(
    '--app-host', type=str, default=None, help='Passed as app host parameter.'
)
@click.option(
    '--app-port', type=int, default=None, help='Passed as app port parameter.'
)
@click.option(
    '--fastapi-workers', type=int, default=None, help='Number of FastAPI workers.'
)
@click.option(
    '--temporal-pool-size',
    '--temporal-workers',
    type=int,
    default=None,
    help='Number of temporal workers.',
)
@click.option(
    '--dev', is_flag=True, default=False, help='Use one worker (for dev. env.).'
)
@config_file_option
def appworker(**kwargs):
    run_appworker(**kwargs)
