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
import time
from collections.abc import Callable

import anyio
from fastapi import FastAPI, Response
from starlette.routing import Mount

from nomad.config import config

# Set multiprocess directory before importing prometheus_client
if config.telemetry.metrics.api_prometheus_enabled:
    multiproc_dir = os.path.join(config.fs.local_tmp, 'prometheus_multiproc')
    os.makedirs(multiproc_dir, exist_ok=True)
    os.environ['PROMETHEUS_MULTIPROC_DIR'] = multiproc_dir

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)

REQUEST_LATENCY_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1.0,
    2.5,
    5.0,
    7.5,
    10.0,
    15.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
    float('inf'),
)

PAYLOAD_SIZE_BUCKETS = (
    128,
    512,
    2048,
    8192,
    32768,
    131072,
    524288,
    2097152,
    8388608,
    33554432,
    134217728,
    536870912,
    2147483648,
    float('inf'),
)

EVENT_LOOP_LAG_BUCKETS = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    float('inf'),
)

RUNTIME_METRICS_SAMPLE_INTERVAL_SECONDS = 1.0

REQUEST_COUNT = Counter(
    'nomad_fastapi_requests_total',
    'Total count of FastAPI requests.',
    ['method', 'path', 'status_code'],
)

REQUEST_LATENCY = Histogram(
    'nomad_fastapi_request_duration_seconds',
    'Request latency in seconds.',
    ['method', 'path'],
    buckets=REQUEST_LATENCY_BUCKETS,
)

TIME_TO_FIRST_BYTE = Histogram(
    'nomad_fastapi_time_to_first_byte_seconds',
    'Time from receiving a request until the first response body chunk is ready.',
    ['method', 'path'],
    buckets=REQUEST_LATENCY_BUCKETS,
)

REQUEST_SIZE = Histogram(
    'nomad_fastapi_request_size_bytes',
    'Size of incoming FastAPI request bodies in bytes.',
    ['method', 'path'],
    buckets=PAYLOAD_SIZE_BUCKETS,
)

RESPONSE_SIZE = Histogram(
    'nomad_fastapi_response_size_bytes',
    'Size of outgoing FastAPI response bodies in bytes.',
    ['method', 'path'],
    buckets=PAYLOAD_SIZE_BUCKETS,
)

REQUESTS_IN_PROGRESS = Gauge(
    'nomad_fastapi_requests_in_progress',
    'Number of concurrent FastAPI requests in progress.',
    ['method'],
    multiprocess_mode='livesum',
)

# These gauges are process-local because each Uvicorn worker has its own AnyIO
# limiter and event loop. ``liveall`` preserves that distinction in Prometheus'
# multiprocess mode, allowing dashboards to show both the sum and the worst worker.
ANYIO_THREADPOOL_BORROWED_TOKENS = Gauge(
    'nomad_anyio_threadpool_borrowed_tokens',
    'Number of AnyIO worker-thread tokens currently borrowed by this process.',
    multiprocess_mode='liveall',
)

ANYIO_THREADPOOL_TOTAL_TOKENS = Gauge(
    'nomad_anyio_threadpool_total_tokens',
    'Total AnyIO worker-thread tokens available to this process.',
    multiprocess_mode='liveall',
)

ANYIO_THREADPOOL_TASKS_WAITING = Gauge(
    'nomad_anyio_threadpool_tasks_waiting',
    'Number of tasks waiting for an AnyIO worker-thread token in this process.',
    multiprocess_mode='liveall',
)

EVENT_LOOP_LAG = Gauge(
    'nomad_event_loop_lag_seconds',
    'Delay beyond the runtime metrics sampling interval in this process.',
    multiprocess_mode='liveall',
)

EVENT_LOOP_LAG_OBSERVATIONS = Histogram(
    'nomad_event_loop_lag_observation_seconds',
    'Distribution of delay beyond the runtime metrics sampling interval.',
    buckets=EVENT_LOOP_LAG_BUCKETS,
)


def _sample_anyio_threadpool_metrics() -> None:
    statistics = anyio.to_thread.current_default_thread_limiter().statistics()
    ANYIO_THREADPOOL_BORROWED_TOKENS.set(statistics.borrowed_tokens)
    ANYIO_THREADPOOL_TOTAL_TOKENS.set(statistics.total_tokens)
    ANYIO_THREADPOOL_TASKS_WAITING.set(statistics.tasks_waiting)


async def _monitor_runtime_metrics(
    *,
    sample_interval: float = RUNTIME_METRICS_SAMPLE_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.perf_counter,
) -> None:
    """Continuously sample process-local AnyIO and event-loop saturation."""
    _sample_anyio_threadpool_metrics()

    while True:
        before_sleep = clock()
        await anyio.sleep(sample_interval)
        lag = max(0.0, clock() - before_sleep - sample_interval)
        EVENT_LOOP_LAG.set(lag)
        EVENT_LOOP_LAG_OBSERVATIONS.observe(lag)
        _sample_anyio_threadpool_metrics()


class PrometheusASGIMiddleware:
    def __init__(self, app, metrics_path: str):
        self.app = app
        self.metrics_path = metrics_path

    @staticmethod
    def _join_path(root_path: str, route_path: str) -> str:
        if route_path == '/':
            return root_path or '/'
        return f'{root_path}{route_path}'

    @classmethod
    def _resolve_templated_path(cls, scope) -> str | None:
        route = scope.get('route')
        root_path = scope.get('root_path', '')

        if route is not None and hasattr(route, 'path'):
            if isinstance(route, Mount):
                if isinstance(route.app, FastAPI):
                    return None
                return f'{root_path.rstrip("/") or "/"}/*'
            return cls._join_path(root_path, route.path)

        # Starlette Mounts (like StaticFiles) do not set scope['route'].
        # Fall back to root_path to identify matched mounts coarsely.
        if root_path:
            return f'{root_path.rstrip("/") or "/"}/*'

        return '/unmatched'

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(_monitor_runtime_metrics)
                try:
                    await self.app(scope, receive, send)
                finally:
                    task_group.cancel_scope.cancel()
            return

        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        # Do not instrument the metrics endpoint itself
        path = scope.get('path', '')
        if path == self.metrics_path:
            await self.app(scope, receive, send)
            return

        # Extract request size from content-length header
        request_size = 0
        for name, value in scope.get('headers', []):
            if name == b'content-length':
                try:
                    request_size = int(value.decode('latin1'))
                except ValueError:
                    pass
                break

        method = scope['method']
        depth = scope.get('_nomad_metrics_depth', 0) + 1
        scope['_nomad_metrics_depth'] = depth

        in_progress_owner_depth = scope.get('_nomad_metrics_in_progress_owner_depth')
        if in_progress_owner_depth is not None:
            REQUESTS_IN_PROGRESS.labels(method=method).dec()

        REQUESTS_IN_PROGRESS.labels(method=method).inc()
        scope['_nomad_metrics_in_progress_owner_depth'] = depth

        start_time = time.perf_counter()
        status_code = [500]
        response_size = [0]

        async def wrapped_send(message):
            if message['type'] == 'http.response.start':
                status_code[0] = message['status']
            elif message['type'] == 'http.response.body':
                if not scope.get('_nomad_ttfb_recorded'):
                    templated_path = self._resolve_templated_path(scope)
                    if templated_path is not None:
                        TIME_TO_FIRST_BYTE.labels(
                            method=method, path=templated_path
                        ).observe(time.perf_counter() - start_time)
                        scope['_nomad_ttfb_recorded'] = True

                body_chunk = message.get('body', b'')
                response_size[0] += len(body_chunk)
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        except Exception as exc:
            status_code[0] = 500
            raise exc
        finally:
            if scope.get('_nomad_metrics_in_progress_owner_depth') == depth:
                REQUESTS_IN_PROGRESS.labels(method=method).dec()

            if not scope.get('_nomad_metrics_recorded'):
                latency = time.perf_counter() - start_time

                # Resolve templated path to prevent cardinality explosion while
                # still distinguishing static mounts from genuine route misses.
                templated_path = self._resolve_templated_path(scope)
                if templated_path is not None:
                    REQUEST_COUNT.labels(
                        method=method,
                        path=templated_path,
                        status_code=str(status_code[0]),
                    ).inc()

                    REQUEST_LATENCY.labels(method=method, path=templated_path).observe(
                        latency
                    )

                    REQUEST_SIZE.labels(method=method, path=templated_path).observe(
                        request_size
                    )

                    RESPONSE_SIZE.labels(method=method, path=templated_path).observe(
                        response_size[0]
                    )

                    scope['_nomad_metrics_recorded'] = True


def _instrument_prometheus(app: FastAPI, metrics_path: str, seen_apps: set[int]):
    app_id = id(app)
    if app_id in seen_apps:
        return
    seen_apps.add(app_id)

    app.add_middleware(PrometheusASGIMiddleware, metrics_path=metrics_path)

    for route in app.routes:
        if isinstance(route, Mount) and isinstance(route.app, FastAPI):
            _instrument_prometheus(route.app, metrics_path, seen_apps)


def setup_prometheus(app: FastAPI):
    if not config.telemetry.metrics.api_prometheus_enabled:
        return

    metrics_path = config.services.join_path('metrics')

    # Instrument FastAPI apps recursively so mounted sub-apps can report their
    # own templated routes. Non-FastAPI mounts are still counted coarsely by the
    # nearest FastAPI parent.
    _instrument_prometheus(app, metrics_path, seen_apps=set())

    @app.get(metrics_path, include_in_schema=False)
    async def metrics():
        if 'PROMETHEUS_MULTIPROC_DIR' in os.environ:
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
            data = generate_latest(registry)
        else:
            data = generate_latest()
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)
