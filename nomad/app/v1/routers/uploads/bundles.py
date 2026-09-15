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
from typing import Annotated

import anyio
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Request,
    UploadFile,
    status,
)
from fastapi import Query as FastApiQuery
from fastapi.responses import StreamingResponse
from pydantic.json_schema import SkipJsonSchema

from nomad.bundles import BundleExporter, BundleImporter
from nomad.tracing import traced

from .default import (
    Scope,
    User,
    _bad_request,
    _not_authorized,
    _not_authorized_to_upload,
    _upload_not_found,
    config,
    create_responses,
    get_current_user,
    strip,
)
from .models import (
    APITag,
    UploadExportOptions,
    UploadImportOptions,
    UploadProcDataResponse,
    upload_export_options_parameters,
    upload_import_options_parameters,
)
from .utils import (
    _check_upload_not_processing,
    _get_files_if_provided,
    get_upload_with_read_access,
    upload_to_pydantic,
)

router = APIRouter()


def _cleanup_bundle_import(
    bundle_importer: BundleImporter, bundle_path: str | None, method: int | None
) -> None:
    bundle_importer.close()
    if bundle_path and method != 0:
        bundle_importer.delete_bundle()


_upload_bundle_response = (
    200,
    {'content': {'application/zip': {'example': '<zipped bundle data>'}}},
)


@router.get(
    '/{upload_id}/export',
    tags=[APITag.TRANSFER],
    summary='Exports the specified upload.',
    response_class=StreamingResponse,
    responses=create_responses(
        _upload_bundle_response,
        _upload_not_found,
        _not_authorized_to_upload,
        _bad_request,
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
@router.get(
    '/{upload_id}/bundle',
    tags=[APITag.BUNDLE],
    summary='Gets an *upload bundle* for the specified upload.',
    response_class=StreamingResponse,
    responses=create_responses(
        _upload_bundle_response,
        _upload_not_found,
        _not_authorized_to_upload,
        _bad_request,
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
@traced(span_name='uploads.get_upload_bundle')
def get_upload_bundle(
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_EXPORT])),
    ],
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    export_options: Annotated[
        UploadExportOptions, Depends(upload_export_options_parameters)
    ],
):
    """
    Get an *upload bundle* for the specified upload. An upload bundle is a file bundle which
    can be used to export and import uploads between different NOMAD deployments.
    """
    upload = get_upload_with_read_access(upload_id, user, include_others=True)
    _check_upload_not_processing(upload)

    export_settings = config.bundle_export.default_settings.customize(
        dict(
            include_raw_files=export_options.include_raw_files,
            include_archive_files=export_options.include_archive_files,
            include_datasets=export_options.include_datasets,
            include_schemas=export_options.include_schemas,
        )
    )

    try:
        stream = BundleExporter(
            upload,
            export_as_stream=True,
            export_path=None,
            zipped=True,
            overwrite=False,
            export_settings=export_settings,
        ).export_bundle()
    except Exception as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=strip(f'Could not export due to error: {e}'),
        )

    return StreamingResponse(stream, media_type='application/zip')


@router.post(
    '/import',
    tags=[APITag.TRANSFER],
    summary='Imports an upload to this NOMAD deployment.',
    response_model=UploadProcDataResponse,
    responses=create_responses(_not_authorized, _bad_request),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
@router.post(
    '/bundle',
    tags=[APITag.BUNDLE],
    summary='Posts an *upload bundle* to this NOMAD deployment.',
    response_model=UploadProcDataResponse,
    responses=create_responses(_not_authorized, _bad_request),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
async def post_upload_bundle(
    request: Request,
    user: Annotated[
        User,
        Depends(
            get_current_user(
                [Scope.UPLOADS_IMPORT],
                allow_anonymous=False,
                allow_upload_token=True,
            )
        ),
    ],
    import_options: Annotated[
        UploadImportOptions, Depends(upload_import_options_parameters)
    ],
    file: Annotated[
        list[UploadFile] | SkipJsonSchema[None],
        File(
            json_schema_extra={
                'items': {
                    'type': 'string',
                    'format': 'binary',
                    'contentMediaType': 'application/octet-stream',
                }
            }
        ),
    ] = None,
    local_path: Annotated[
        str | None,
        FastApiQuery(
            description=strip(
                """
            Internal/Admin use only."""
            )
        ),
    ] = None,
):
    """
    Posts an *upload bundle* to this NOMAD deployment. An upload bundle is a file bundle which
    can be used to export and import uploads between different NOMAD installations. The
    endpoint expects an upload bundle attached as a zipfile.

    **NOTE:** This endpoint is restricted to admin users and oasis admins. Further, all
    settings except `embargo_length` requires an admin user to change (these settings
    have default values specified by the system configuration).

    There are two basic ways to upload files: using multipart-formdata or streaming the
    file data in the HTTP request body. Both are supported. See the POST `uploads` endpoint for
    examples of `curl` commands for uploading files.
    """
    import_settings = config.bundle_import.default_settings.customize(
        dict(
            include_raw_files=import_options.include_raw_files,
            include_archive_files=import_options.include_archive_files,
            include_datasets=import_options.include_datasets,
            include_bundle_info=import_options.include_bundle_info,
            keep_original_timestamps=import_options.keep_original_timestamps,
            set_from_oasis=import_options.set_from_oasis,
            trigger_processing=import_options.trigger_processing,
        )
    )

    bundle_importer: BundleImporter | None = None
    bundle_path: str | None = None
    method = None

    if local_path:
        if not await anyio.to_thread.run_sync(os.path.isfile, local_path):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='You can only target a single bundle file using local_path.',
            )

    try:
        bundle_importer = BundleImporter(user, import_settings)
        bundle_importer.check_api_permissions()

        bundle_paths, _, method = await _get_files_if_provided(
            tmp_dir_prefix='bundle',
            request=request,
            file=file,
            local_path=local_path,
            file_name=None,
            user=user,
        )

        if not bundle_paths:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='No bundle file provided',
            )
        if len(bundle_paths) > 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='Can only provide one bundle file at a time',
            )
        bundle_path = bundle_paths[0]

        def do_import():
            bundle_importer.open(bundle_path)
            upload_obj = bundle_importer.create_upload_skeleton()
            bundle_importer.close()
            # Import the bundle using the unified method
            upload_obj.import_bundle(
                bundle_path=bundle_path,
                import_settings=import_settings.model_dump()
                if import_settings is not None
                else {},
                embargo_length=import_options.embargo_length,
            )
            return upload_obj

        upload = await anyio.to_thread.run_sync(do_import)

        upload_data = await anyio.to_thread.run_sync(upload_to_pydantic, upload)
        return UploadProcDataResponse(upload_id=upload.upload_id, data=upload_data)
    except Exception as e:
        if bundle_importer:
            await anyio.to_thread.run_sync(
                _cleanup_bundle_import, bundle_importer, bundle_path, method
            )
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f'Failed to import bundle: {str(e)}',
        )
