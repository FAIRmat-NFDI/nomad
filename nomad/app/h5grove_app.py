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

from __future__ import annotations

import re
import traceback
import urllib.parse
from collections.abc import Callable
from typing import Any

import anyio
import h5py
from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from h5grove import fastapi_utils as h5grove_router
from h5grove.utils import open_file_with_error_fallback

from nomad import utils
from nomad.app.v1.models import User
from nomad.app.v1.routers.auth import get_current_user
from nomad.app.v1.routers.uploads import get_upload_with_read_access
from nomad.auth.scopes import Scope
from nomad.files import FSUtility, PublicUploadFiles, UploadFiles

logger = utils.get_logger(__name__)


def open_zipped_h5_file(
    filepath: str,
    create_error: Callable[[int, str], Exception],
    h5py_options: dict[str, Any] = {},
):
    """
    Patched h5grove utils function open_file_with_error_fallback in order to open h5 file
    in zipped folder.
    """
    import io
    import re

    from nomad import files

    match = re.match(
        r'.*?/uploads/(?P<upload_id>.+?)/(?P<directory>.+?)/(?P<path_or_id>.+)',
        filepath,
    )
    if not match:
        raise create_error(404, 'File not found!')

    upload_files = files.UploadFiles.get(match['upload_id'])
    if upload_files is None:
        raise create_error(404, 'File not found!')
    path_or_id = match['path_or_id']
    try:
        if match['directory'] == 'raw':
            with (
                upload_files.raw_file(path_or_id, 'rb') as file_object,
                h5py.File(file_object, **h5py_options) as f,
            ):
                yield f
        else:
            with FSUtility.open_h5(
                upload_files.archive_hdf5_location(path_or_id), **h5py_options
            ) as f:
                yield f
    except OSError as e:
        if isinstance(e, FileNotFoundError) or 'No such file or directory' in str(e):
            raise create_error(404, 'File not found!')
        if isinstance(e, PermissionError) or 'Permission denied' in str(e):
            raise create_error(403, 'Cannot read file: Permission denied!')
        if isinstance(e, io.UnsupportedOperation):
            raise create_error(404, 'File not found!')
        raise e
    except Exception:
        raise create_error(404, 'File not found!')


open_file_with_error_fallback.__closure__[0].cell_contents = open_zipped_h5_file  # noqa


def check_user_access(
    upload_id: str,
    user: User = Depends(
        get_current_user([Scope.UPLOADS_READ, Scope.EXTERNAL_H5GROVE_READ])
    ),
):
    get_upload_with_read_access(upload_id, user, include_others=True)


app = FastAPI(dependencies=[Depends(check_user_access)])

app.add_middleware(
    CORSMiddleware,  # type: ignore
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def add_upload_folder_path(request: Request, call_next):
    upload_id = request.query_params['upload_id']
    file = request.query_params['file']
    path = request.query_params['path']
    source = request.query_params['source']

    upload_path = f'/uploads/{upload_id}/{source}/'
    if source == 'archive' and isinstance(
        await anyio.to_thread.run_sync(UploadFiles.get, upload_id), PublicUploadFiles
    ):
        path = f'{file}{path}'

    scope = request.scope
    old_file = urllib.parse.quote(request.query_params['file'], safe=[])
    new_file = urllib.parse.quote(upload_path + request.query_params['file'], safe=[])
    scope['query_string'] = scope['query_string'].replace(
        old_file.encode('utf-8'), new_file.encode('utf-8')
    )
    query_string = scope['query_string'].decode('utf-8')
    query_string = re.sub(
        r'path=.+?(?:&|\Z)', f'path={urllib.parse.quote(path)}&', query_string
    )
    scope['query_string'] = query_string.encode('utf-8')

    response = await call_next(Request(scope))
    return response


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


app.include_router(h5grove_router.router)

# Register trailing-slash aliases for h5grove endpoints so FastAPI does not issue
# automatic redirects that can reconstruct the external URL with the wrong scheme.
for route in list(h5grove_router.router.routes):
    path = getattr(route, 'path', None)
    if not path or path == '/' or path.endswith('/'):
        continue
    app.add_api_route(
        f'{path}/',
        route.endpoint,
        methods=list(route.methods),
        name=f'{route.name}_slash',
        include_in_schema=False,
    )
