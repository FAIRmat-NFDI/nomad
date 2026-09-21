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
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.exception_handlers import (
    http_exception_handler as default_http_exception_handler,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi_cache import FastAPICache
from starlette.exceptions import HTTPException as StarletteHTTPException
from temporalio.client import Client

from nomad.actions.client import close_client, get_client
from nomad.auth.scopes import Scope
from nomad.auth.tokens import check_api_secret
from nomad.config import config
from nomad.config.models.plugins import APIEntryPoint, DashboardEntryPoint
from nomad.mongo.cache import MongoBackend
from nomad.utils.structlogging import get_logger

from .static import GuiFiles
from .static import app as static_files_app
from .v1.main import app as v1_app
from .v1.routers import apps as apps_router


def mount_with_trailing_slash_redirect(
    parent_app: FastAPI, mount_path: str, mounted_app: FastAPI
):
    normalized_path = mount_path.rstrip('/')

    @parent_app.api_route(
        normalized_path, methods=['GET', 'HEAD'], include_in_schema=False
    )
    async def redirect_to_trailing_slash(request: Request):
        path_with_slash = (
            request.url.path
            if request.url.path.endswith('/')
            else f'{request.url.path}/'
        )
        query = f'?{request.url.query}' if request.url.query else ''
        return RedirectResponse(
            url=f'{path_with_slash}{query}',
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    parent_app.mount(normalized_path, mounted_app)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from nomad import infrastructure
    from nomad.actions.workflows.utils import setup_server_stats
    from nomad.cli.dev import generate_gui_artifacts_js, get_gui_config
    from nomad.metainfo.elasticsearch_extension import entry_type
    from nomad.parsing.parsers import import_all_parsers

    import_all_parsers()

    # each subprocess is supposed to disconnect and
    # connect again: https://jira.mongodb.org/browse/PYTHON-2090
    try:
        from mongoengine import disconnect

        disconnect()
    except Exception:
        pass

    entry_type.reload_quantities_dynamic()

    GuiFiles.bootstrap(generate_gui_artifacts_js(), get_gui_config())

    infrastructure.setup()

    await infrastructure.init_async_mongo()

    FastAPICache.init(backend=MongoBackend())

    # By this point all of the schemas packages from plugins are loaded.
    apps_router.initialize_search_quantities()

    # Validate API secret
    check_api_secret()

    try:
        app.state.temporal_client = await get_client()
        await setup_server_stats(app.state.temporal_client)

        yield
    except Exception as e:
        logger = get_logger(__name__)

        logger.error(f'Failed to connect to temporal', exc_info=e)
        raise
    finally:
        await close_client()
        if infrastructure.async_mongo_client is not None:
            await infrastructure.async_mongo_client.close()
            infrastructure.async_mongo_client = None
            infrastructure.async_mongo_loop = None
        if GuiFiles.gui_artifacts_path is not None and os.path.exists(
            GuiFiles.gui_artifacts_path
        ):
            os.remove(GuiFiles.gui_artifacts_path)


app = FastAPI(lifespan=lifespan)

from nomad.metrics import setup_prometheus
from nomad.tracing import setup_tracing

setup_tracing(app)
setup_prometheus(app)

app_base = config.services.route_prefix


def temporal_client() -> Client:
    return app.state.temporal_client


@app.get(config.services.join_path('alive'))
async def alive():
    return 'I am, alive!'


@app.get('/-/health', status_code=status.HTTP_200_OK)
async def health():
    return {'healthcheck': 'ok'}


app.mount(config.services.join_path('api', 'v1'), v1_app)
v1_app.add_middleware(
    CORSMiddleware,  # CORS has to be the first to act on request
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['Content-Disposition'],
)

if config.services.optimade_enabled:
    from starlette.middleware.base import BaseHTTPMiddleware

    from .optimade import optimade_app

    class RequireScopesMiddleware(BaseHTTPMiddleware):
        """
        Enforces the presence of required backend scopes.

        This middleware resolves the current user from a bearer token (Keycloak or
        simple token) and verifies that the request has all required scopes before
        delegating to the wrapped application.

        It is primarily intended for protecting externally mounted sub-applications
        (e.g. OPTIMADE), where FastAPI dependencies are not evaluated.
        Upload tokens are thus intentionally not supported in this middleware.
        """

        def __init__(self, app, *, required_scopes: set[str]):
            super().__init__(app)
            self.required_scopes = required_scopes

        async def dispatch(self, request: Request, call_next) -> Response:
            from nomad.app.v1.routers.auth import _resolve_user_with_scopes

            auth = request.headers.get('authorization')
            bearer_token = None
            if auth and auth.lower().startswith('bearer '):
                bearer_token = auth.split(' ', 1)[1]

            try:
                await _resolve_user_with_scopes(
                    required_scopes=self.required_scopes,
                    allow_anonymous=True,
                    request=request,
                    keycloak_token=bearer_token,
                    simple_token=bearer_token,
                )

            except StarletteHTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={'detail': exc.detail},
                    headers=getattr(exc, 'headers', None) or {},
                )

            return await call_next(request)

    optimade_wrapper = FastAPI()
    optimade_wrapper.add_middleware(
        RequireScopesMiddleware,
        required_scopes={Scope.EXTERNAL_OPTIMADE_READ},
    )

    optimade_wrapper.mount('/', optimade_app)
    app.mount(config.services.join_path('optimade'), optimade_wrapper)


if config.services.dcat_enabled:
    from .dcat.main import app as dcat_app

    app.mount(config.services.join_path('dcat'), dcat_app)


if config.services.h5grove_enabled:
    from .h5grove_app import app as h5grove_app

    app.mount(config.services.join_path('h5grove'), h5grove_app)


@app.middleware('http')
async def _dashboard_frame_ancestors_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(config.services.join_path('dashboards') + '/'):
        sources = ' '.join(config.services.dashboard_frame_ancestors)
        response.headers['Content-Security-Policy'] = f'frame-ancestors {sources}'
    return response


# Mount API and dashboard plugin apps
if config.plugins is None:
    raise RuntimeError('No plugins are loaded')

for entry_point in config.plugins.entry_points.filtered_values():
    if isinstance(entry_point, APIEntryPoint):
        api_app = entry_point.load()
        assert isinstance(api_app, FastAPI), (
            f'Error loading entry point "{entry_point.id}": The load method of an API entry point must return a FastAPI instance'
        )
        mount_with_trailing_slash_redirect(
            app,
            config.services.join_path(entry_point.prefix),
            api_app,
        )
    elif isinstance(entry_point, DashboardEntryPoint):
        if entry_point.external_url is not None:
            continue
        try:
            dashboard_app = entry_point.load()
            if not isinstance(dashboard_app, FastAPI):
                raise TypeError(
                    'load() must return a FastAPI instance when external_url is not set'
                )
            mount_with_trailing_slash_redirect(
                app,
                config.services.join_path('dashboards', entry_point.id_url_safe),
                dashboard_app,
            )
        except Exception as exc:
            get_logger(__name__).error(
                'failed to mount dashboard entry point; skipping',
                entry_point_id=entry_point.id,
                exc_info=exc,
            )

# Make sure to mount this last, as it is a catch-all routes that are not yet mounted.
app.mount(app_base, static_files_app)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    if exc.status_code != status.HTTP_404_NOT_FOUND:
        return await default_http_exception_handler(request, exc)

    try:
        accept = request.headers['accept']
    except Exception:
        accept = None

    if accept is not None and 'html' in accept:
        return HTMLResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=f"""
        <html>
            <head><title>{config.meta.name}</title></head>
            <body>
                <h1>NOMAD app</h1>
                <h2>info</h2>
                {'<br/>'.join(f'{key}: {value}' for key, value in config.meta.model_dump().items())}
                <h2>apis</h2>
                <a href="{app_base}/api/v1/extensions/docs">NOMAD API v1</a><br/>
                <a href="{app_base}/optimade/v1/extensions/docs">Optimade API</a><br/>
                <a href="{app_base}/dcat/extensions/docs">DCAT API</a><br/>
            </body>
        </html>
        """,
        )

    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            'detail': 'Not found',
            'info': {
                'app': config.meta.model_dump(),
                'apis': {
                    'v1': {
                        'root': f'{app_base}/api/v1',
                        'dashboard': f'{app_base}/api/v1/extensions/docs',
                    },
                    'optimade': {
                        'root': f'{app_base}/optimade/v1',
                        'dashboard': f'{app_base}/optimade/v1/extensions/docs',
                    },
                    'dcat': {
                        'root': f'{app_base}/dcat',
                        'dashboard': f'{app_base}/dcat/extensions/docs',
                    },
                },
            },
        },
    )
