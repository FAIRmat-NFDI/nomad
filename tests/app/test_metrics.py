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

import anyio
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.staticfiles import StaticFiles

from nomad.config import config
from nomad.metrics import setup_prometheus


def test_prometheus_monitoring_disabled(monkeypatch):
    monkeypatch.setattr(config.telemetry.metrics, 'api_prometheus_enabled', False)

    app = FastAPI()
    setup_prometheus(app)

    client = TestClient(app)

    # Assert metrics endpoint is not registered
    metrics_path = config.services.join_path('metrics')
    assert client.get(metrics_path).status_code == 404


def test_prometheus_monitoring_enabled(monkeypatch):
    monkeypatch.setattr(config.telemetry.metrics, 'api_prometheus_enabled', True)

    app = FastAPI()

    # Add a dummy route to test path template routing
    @app.get('/test/{item_id}')
    async def get_item(item_id: str):
        return {'item': item_id}

    setup_prometheus(app)
    client = TestClient(app)

    # First, fetch metrics (should exist and return 200)
    metrics_path = config.services.join_path('metrics')
    response = client.get(metrics_path)
    assert response.status_code == 200
    assert 'nomad_fastapi_requests_total' in response.text

    # Make a request to the test route
    response = client.get('/test/123')
    assert response.status_code == 200

    # Fetch metrics again and check that the test route was instrumented
    metrics_response = client.get(metrics_path)
    assert metrics_response.status_code == 200
    assert (
        'nomad_fastapi_requests_total{method="GET",path="/test/{item_id}",status_code="200"}'
        in metrics_response.text
    )
    # Check that request/response sizes and in progress Gauges are present
    assert 'nomad_fastapi_request_size_bytes' in metrics_response.text
    assert 'nomad_fastapi_response_size_bytes' in metrics_response.text
    assert 'nomad_fastapi_requests_in_progress' in metrics_response.text
    assert 'nomad_fastapi_time_to_first_byte_seconds' in metrics_response.text
    assert (
        'nomad_fastapi_time_to_first_byte_seconds_count{method="GET",path="/test/{item_id}"} 1.0'
        in metrics_response.text
    )
    assert (
        'nomad_fastapi_request_duration_seconds_bucket{le="600.0",method="GET",path="/test/{item_id}"}'
        in metrics_response.text
    )


def test_prometheus_runtime_saturation_metrics(monkeypatch):
    monkeypatch.setattr(config.telemetry.metrics, 'api_prometheus_enabled', True)

    app = FastAPI()
    setup_prometheus(app)

    metrics_path = config.services.join_path('metrics')
    with TestClient(app) as client:
        metrics_response = client.get(metrics_path)

    assert metrics_response.status_code == 200
    assert 'nomad_anyio_threadpool_borrowed_tokens' in metrics_response.text
    assert 'nomad_anyio_threadpool_total_tokens' in metrics_response.text
    assert 'nomad_anyio_threadpool_tasks_waiting' in metrics_response.text
    assert 'nomad_event_loop_lag_seconds' in metrics_response.text
    assert 'nomad_event_loop_lag_observation_seconds' in metrics_response.text
    assert ' 40.0' in next(
        line
        for line in metrics_response.text.splitlines()
        if line.startswith('nomad_anyio_threadpool_total_tokens')
    )


def test_prometheus_ttfb_includes_stream_setup(monkeypatch):
    monkeypatch.setattr(config.telemetry.metrics, 'api_prometheus_enabled', True)

    app = FastAPI()

    @app.get('/delayed-stream')
    async def get_delayed_stream():
        async def stream():
            await anyio.sleep(0.02)
            yield b'payload'

        return StreamingResponse(stream())

    setup_prometheus(app)

    client = TestClient(app)
    response = client.get('/delayed-stream')
    assert response.status_code == 200

    metrics_path = config.services.join_path('metrics')
    metrics_response = client.get(metrics_path)
    ttfb = float(
        next(
            line.rsplit(' ', 1)[1]
            for line in metrics_response.text.splitlines()
            if line.startswith(
                'nomad_fastapi_time_to_first_byte_seconds_sum{method="GET",path="/delayed-stream"}'
            )
        )
    )
    duration = float(
        next(
            line.rsplit(' ', 1)[1]
            for line in metrics_response.text.splitlines()
            if line.startswith(
                'nomad_fastapi_request_duration_seconds_sum{method="GET",path="/delayed-stream"}'
            )
        )
    )
    assert ttfb >= 0.015
    assert duration >= ttfb


def test_prometheus_monitoring_unmatched(monkeypatch):
    monkeypatch.setattr(config.telemetry.metrics, 'api_prometheus_enabled', True)

    app = FastAPI()
    setup_prometheus(app)
    client = TestClient(app)

    # Hit an unmatched endpoint (404)
    client.get('/invalid-route-abc')

    metrics_path = config.services.join_path('metrics')
    metrics_response = client.get(metrics_path)
    assert metrics_response.status_code == 200
    assert (
        'nomad_fastapi_requests_total{method="GET",path="/unmatched",status_code="404"}'
        in metrics_response.text
    )


def test_prometheus_monitoring_mounted_fastapi_uses_templated_path(monkeypatch):
    monkeypatch.setattr(config.telemetry.metrics, 'api_prometheus_enabled', True)

    app = FastAPI()
    mounted_app = FastAPI()

    @mounted_app.get('/child/{item_id}')
    async def get_item(item_id: str):
        return {'item': item_id}

    app.mount('/mounted-fastapi', mounted_app)
    setup_prometheus(app)

    client = TestClient(app)
    response = client.get('/mounted-fastapi/child/123')
    assert response.status_code == 200

    metrics_path = config.services.join_path('metrics')
    metrics_response = client.get(metrics_path)
    assert metrics_response.status_code == 200
    assert (
        'nomad_fastapi_requests_total{method="GET",path="/mounted-fastapi/child/{item_id}",status_code="200"}'
        in metrics_response.text
    )
    assert '/mounted-fastapi/*' not in metrics_response.text


def test_prometheus_monitoring_static_mount_uses_coarse_path(monkeypatch, tmp_path):
    monkeypatch.setattr(config.telemetry.metrics, 'api_prometheus_enabled', True)

    docs_dir = tmp_path / 'docs'
    docs_dir.mkdir()
    (docs_dir / 'index.html').write_text('ok', encoding='utf-8')

    app = FastAPI()
    mounted_app = FastAPI()
    mounted_app.mount('/docs', StaticFiles(directory=docs_dir), name='docs')
    app.mount('/mounted-static', mounted_app)
    setup_prometheus(app)

    client = TestClient(app)
    response = client.get('/mounted-static/docs/index.html')
    assert response.status_code == 200

    metrics_path = config.services.join_path('metrics')
    metrics_response = client.get(metrics_path)
    assert metrics_response.status_code == 200
    assert (
        'nomad_fastapi_requests_total{method="GET",path="/mounted-static/docs/*",status_code="200"}'
        in metrics_response.text
    )
