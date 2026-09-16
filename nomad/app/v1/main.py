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

import traceback

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, ORJSONResponse, RedirectResponse
from pyinstrument import Profiler
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from nomad import utils
from nomad.config import config

from .common import root_path
from .routers import (
    actions,
    apps,
    auth,
    datasets,
    entries,
    graph,
    groups,
    info,
    metainfo,
    north,
    north_new,
    notifications,
    ownership_transfers,
    schemas,
    suggestions,
    systems,
    uploads,
    users,
)

logger = utils.get_logger(__name__)


class LoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        with utils.timer(logger, 'request handled', path=scope.get('path')):
            await self.app(scope, receive, send)


async def profile_request(request: Request, call_next):
    if not request.query_params.get('__profile__', False):
        return await call_next(request)

    with Profiler(async_mode='strict') as profiler:
        await call_next(request)

    return HTMLResponse(profiler.output_html())


app = FastAPI(
    root_path=root_path,
    openapi_url='/openapi.json',
    docs_url='/extensions/docs',
    redoc_url='/extensions/redoc',
    swagger_ui_oauth2_redirect_url='/extensions/docs/oauth2-redirect',
    title='NOMAD API',
    version=f'v1, NOMAD {config.meta.version}',
    description=utils.strip(
        f"""
        Please visit the [API section of the NOMAD documentation]({config.api_url(True, 'docs/howto/manage/program/api.html')})
        for a introduction and examples.
    """
    ),
    default_response_class=ORJSONResponse,
    middleware=[
        Middleware(LoggingMiddleware),
        Middleware(BaseHTTPMiddleware, dispatch=profile_request),
    ],
)


async def redirect_to_docs(_):
    return RedirectResponse(f'{root_path}/extensions/docs')


# app.add_route(f'{root_path}', redirect_to_docs, include_in_schema=False)
app.add_route('/', redirect_to_docs, include_in_schema=False)


@app.exception_handler(Exception)
async def unicorn_exception_handler(request: Request, e: Exception):
    logger.error('unexpected exception in API', url=request.url, exc_info=e)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            'detail': {
                'reason': 'Unexpected exception while handling your request',
                'exception': str(e),
                'exception_class': e.__class__.__name__,
                'exception_traceback': traceback.format_exc(),
            }
        },
    )


app.include_router(actions.router, prefix='/actions')
app.include_router(auth.router, prefix='/auth')
app.include_router(apps.router, prefix='/apps')
app.include_router(datasets.router, prefix='/datasets')
app.include_router(entries.router, prefix='/entries')
app.include_router(graph.router, prefix='/graph')
app.include_router(groups.router, prefix='/groups')
app.include_router(info.router, prefix='/info')
app.include_router(metainfo.router, prefix='/metainfo')
if config.north.enabled:
    if config.north.enable_new_hub_api:
        app.include_router(north_new.router, prefix='/north', tags=['north'])
    else:
        app.include_router(north.router, prefix='/north')
app.include_router(schemas.router, prefix='/schemas')
app.include_router(suggestions.router, prefix='/suggestions')
app.include_router(systems.router, prefix='/systems')
app.include_router(ownership_transfers.router, prefix='/ownership-transfers')
app.include_router(notifications.router, prefix='/notifications')
app.include_router(uploads.router, prefix='/uploads')
app.include_router(users.router, prefix='/users')
