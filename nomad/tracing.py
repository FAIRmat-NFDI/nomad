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

from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

import os

from nomad.config import config

_setup_done = False
_setup_pid = None
_traced_enabled = False
_instrumented_apps: set[tuple[int, int]] = set()
telemetry_enabled = config.telemetry.tracing.enabled


class TracingProcessPoolExecutor(ProcessPoolExecutor):
    """
    A ProcessPoolExecutor that propagates the OpenTelemetry context to the child processes.
    """

    def submit(self, fn, /, *args, **kwargs):
        from opentelemetry import propagate

        # Capture the current context as a simple dictionary of headers
        headers = {}
        if telemetry_enabled:
            propagate.inject(headers)
        return super().submit(_tracing_wrapper, headers, fn, *args, **kwargs)


def _tracing_wrapper(headers, fn, *args, **kwargs):
    """
    Wrapper function that attaches the context from headers before running the function.
    """
    from opentelemetry import context, propagate

    # Extract the context from the headers and attach it
    ctx = propagate.extract(headers)
    token = context.attach(ctx)
    try:
        return fn(*args, **kwargs)
    finally:
        context.detach(token)


def traced(func=None, *, span_name=None):
    """
    Decorator to trace a function call with OpenTelemetry.
    """
    import asyncio
    from functools import wraps

    def decorator(f):
        if asyncio.iscoroutinefunction(f):

            @wraps(f)
            async def wrapper(*args, **kwargs):
                if not telemetry_enabled:
                    return await f(*args, **kwargs)

                from opentelemetry import trace

                tracer = trace.get_tracer(f.__module__)
                name = span_name or f'{f.__qualname__}'
                with tracer.start_as_current_span(name) as span:
                    try:
                        result = await f(*args, **kwargs)
                        return result
                    except Exception as e:
                        span.record_exception(e)
                        span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                        raise

            return wrapper
        else:

            @wraps(f)
            def wrapper(*args, **kwargs):
                if not telemetry_enabled:
                    return f(*args, **kwargs)

                from opentelemetry import trace

                tracer = trace.get_tracer(f.__module__)
                name = span_name or f'{f.__qualname__}'
                with tracer.start_as_current_span(name) as span:
                    try:
                        result = f(*args, **kwargs)
                        return result
                    except Exception as e:
                        span.record_exception(e)
                        span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                        raise

            return wrapper

    if func:
        return decorator(func)
    return decorator


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None):
    """
    Context manager to trace a block of code with OpenTelemetry.
    """
    if not telemetry_enabled:
        yield None
        return

    from opentelemetry import trace

    tracer = trace.get_tracer('nomad')
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            raise


def setup_tracing(app: 'FastAPI | None' = None):
    global _setup_done, _setup_pid
    configured_service_name = config.telemetry.tracing.service_name

    if not telemetry_enabled:
        return
    from nomad.utils.structlogging import get_logger

    logger = get_logger(__name__)

    if app:
        app_key = (os.getpid(), id(app))
        if app_key not in _instrumented_apps:
            from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            app.add_middleware(OpenTelemetryMiddleware)
            FastAPIInstrumentor().instrument()
            _instrumented_apps.add(app_key)

    # If we already set up tracing in this process, we are done.
    # We check the PID to detect if we have been forked since the last setup.
    # OpenTelemetry background processors and threads do not survive forks.
    current_pid = os.getpid()
    if _setup_done and _setup_pid == current_pid:
        from opentelemetry import trace

        logger.info(
            'tracing already initialized',
            pid=current_pid,
            service_name=configured_service_name,
            tracer_provider_type=type(trace.get_tracer_provider()).__name__,
            otlp_endpoint=config.telemetry.tracing.otlp_endpoint,
        )
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter as OTLPHTTPSpanExporter,
    )
    from opentelemetry.instrumentation.elasticsearch import ElasticsearchInstrumentor
    from opentelemetry.instrumentation.pymongo import PymongoInstrumentor
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )
    from temporalio.contrib.opentelemetry import create_tracer_provider

    if not _setup_done or _setup_pid != current_pid:
        set_global_textmap(TraceContextTextMapPropagator())
        name = configured_service_name
        resource = Resource.create({'service.name': name})
        sampler = TraceIdRatioBased(config.telemetry.tracing.sampler_ratio)
        provider = create_tracer_provider(resource=resource, sampler=sampler)

        if config.telemetry.tracing.otlp_endpoint:
            if config.telemetry.tracing.otlp_endpoint.startswith('http'):
                processor = BatchSpanProcessor(
                    OTLPHTTPSpanExporter(
                        endpoint=config.telemetry.tracing.otlp_endpoint
                    )
                )
            else:
                insecure = (
                    config.telemetry.tracing.otlp_endpoint.startswith('http://')
                    or 'localhost' in config.telemetry.tracing.otlp_endpoint
                )
                processor = BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=config.telemetry.tracing.otlp_endpoint,
                        insecure=insecure,
                    )
                )
            provider.add_span_processor(processor)

        trace.set_tracer_provider(provider)
        # If we are in a child process (detected by PID mismatch from parent),
        # we might need to force-set the provider if opentelemetry's set_tracer_provider
        # ignored it because it was already set in the parent.
        if trace.get_tracer_provider() is not provider:
            trace._TRACER_PROVIDER = provider

        logger.info(
            'tracing initialized',
            pid=current_pid,
            service_name=name,
            tracer_provider_type=type(trace.get_tracer_provider()).__name__,
            otlp_endpoint=config.telemetry.tracing.otlp_endpoint,
            sampler_ratio=config.telemetry.tracing.sampler_ratio,
        )

        PymongoInstrumentor().instrument()
        if config.elastic.version == '9':
            ElasticsearchInstrumentor().instrument()
        else:
            # The OpenTelemetry Elasticsearch instrumentor currently patches
            # only the un-namespaced current client. ES7 is intentionally kept
            # behind nomad.elastic_compat, so its transport calls are not
            # automatically instrumented.
            logger.warning(
                'elasticsearch_v7_tracing_unavailable',
                configured_version='7',
            )
        _setup_done = True
        _setup_pid = current_pid
