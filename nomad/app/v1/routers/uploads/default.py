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

import functools
import os
import shutil
from datetime import datetime, timezone
from typing import Annotated, Any, cast

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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from nomad import files, utils
from nomad.auth.scopes import Scope
from nomad.auth.tokens import generate_upload_token
from nomad.common import get_compression_format, is_safe_basename, is_safe_relative_path
from nomad.config import config
from nomad.config.models.config import Reprocess
from nomad.config.models.plugins import ExampleUploadEntryPoint
from nomad.files import FSUtility, PublicUploadFiles, StagingUploadFiles
from nomad.mongo.search import MongoQueryError, create_mongo_query
from nomad.processing import (
    Entry,
    MetadataEditRequestHandler,
    ProcessAlreadyRunning,
    ProcessStatus,
    Upload,
)
from nomad.search import refresh as search_refresh
from nomad.search import search
from nomad.tracing import traced
from nomad.utils import strip

from ...models import (
    Direction,
    Files,
    HTTPExceptionModel,
    MetadataEditRequest,
    MetadataPagination,
    PaginationResponse,
    User,
    WithQuery,
    files_parameters,
)
from ...utils import (
    DownloadItem,
    browser_download_headers,
    create_download_stream_raw_file,
    create_download_stream_zipped,
    create_responses,
    create_stream_from_string,
)
from ..auth import get_current_user
from ..entries import EntryArchiveResponse, answer_entry_archive_request
from .models import (
    APITag,
    EntryProcDataPagination,
    EntryProcDataQueryResponse,
    EntryProcDataResponse,
    ProcessingData,
    PutRawFileResponse,
    RawDirDirectoryMetadata,
    RawDirElementMetadata,
    RawDirFileMetadata,
    RawDirPagination,
    RawDirResponse,
    UploadCommandExamplesResponse,
    UploadProcDataPagination,
    UploadProcDataQuery,
    UploadProcDataQueryResponse,
    UploadProcDataResponse,
    UploadRole,
    entry_proc_data_pagination_parameters,
    rawdir_pagination_parameters,
    upload_proc_data_pagination_parameters,
    upload_proc_data_query_parameters,
)
from .utils import (
    _get_files_if_provided,
    _get_upload_with_write_access,
    _query_mongodb,
    entry_to_pydantic,
    get_role_query,
    get_upload_with_read_access,
    upload_to_pydantic,
)

router = APIRouter()
logger = utils.get_logger(__name__)

# Custom exceptions


def _create_exception(status_code: int, response_dict: dict[str, Any]) -> HTTPException:
    """Create an HTTP exception from a documented response definition."""
    return HTTPException(status_code, detail=response_dict.get('description'))


_not_authorized = (
    status.HTTP_401_UNAUTHORIZED,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Unauthorized. Authorization is required, but no or bad authentication credentials provided."""
        ),
    },
)

_not_authorized_to_upload = (
    status.HTTP_401_UNAUTHORIZED,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Unauthorized. No credentials provided, or you do not have permissions to the
        specified upload."""
        ),
    },
)

_not_authorized_to_entry = (
    status.HTTP_401_UNAUTHORIZED,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Unauthorized. No credentials provided, or you do not have permissions to the
        specified upload or entry."""
        ),
    },
)

_bad_request = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Bad request. The request could not be processed because of some error/invalid argument."""
        ),
    },
)

_bad_pagination = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Bad request. Invalid pagination arguments supplied."""
        ),
    },
)

_upload_not_found = (
    status.HTTP_404_NOT_FOUND,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The specified upload could not be found."""
        ),
    },
)

_entry_not_found = (
    status.HTTP_404_NOT_FOUND,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The specified upload or entry could not be found."""
        ),
    },
)

_upload_or_path_not_found = (
    status.HTTP_404_NOT_FOUND,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The specified upload, or a resource with the specified path within the upload,
        could not be found."""
        ),
    },
)

_existing_upload_with_findable_state = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': 'The upload has failed to be submitted previously.',
    },
)

_upload_already_has_doi = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The upload already has a DOI and cannot be changed anymore.
    """
        ),
    },
)

_upload_is_empty = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The Upload is empty. No DOI can be assigned at this moment. Add some published
        contents to the upload first.
    """
        ),
    },
)

_upload_is_unpublished = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The upload is unpublished. No DOI can be assigned at the moment.
        Publish the upload first.
    """
        ),
    },
)


# Custom responses

_thank_you_message = f"""
Thanks for uploading your data to nomad.
Go back to {config.gui_url()} and press
reload to see the progress on your upload
and publish your data."""

_post_upload_response = (
    200,
    {
        'model': UploadProcDataResponse,
        'content': {
            'application/json': {},
            'text/plain': {'example': 'Thanks for uploading your data to nomad.'},
        },
        'description': strip(
            """
        A json structure with upload data, or a plain text information string.
        It will be a json structure if the request headers specifies `Accept = application/json`."""
        ),
    },
)

_put_raw_file_response = (
    200,
    {
        'model': PutRawFileResponse,
        'content': {
            'application/json': {},
            'text/plain': {'example': 'Thanks for uploading your data to nomad.'},
        },
        'description': strip(
            """
        A json structure with upload data and possibly information from the processing,
        or a plain text information string.
        It will be a json structure if the request headers specifies `Accept = application/json`
        or if `wait_for_processing` is set."""
        ),
    },
)


_raw_path_response = (
    200,
    {
        'content': {
            'application/octet-stream': {'example': 'file data'},
            'application/zip': {'example': '<zipped file or directory content>'},
        },
        'description': strip(
            """
        If `path` denotes a file: a stream with the file content, zipped if `compress = true`.
        If `path` denotes a directory, and `compress = true`, the directory content, zipped."""
        ),
    },
)


# Default (uploads) endpoints


@router.get(
    '/command-examples',
    tags=[APITag.DEFAULT],
    summary='Get example commands for shell based uploads.',
    response_model=UploadCommandExamplesResponse,
    responses=create_responses(_not_authorized),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_command_examples(
    user: Annotated[
        User,
        Depends(get_current_user([Scope.TOKENS_CREATE], allow_anonymous=False)),
    ],
):
    """Get URL and example command for shell based uploads."""
    token = generate_upload_token(user)
    api_url = config.api_url(ssl=config.services.https_upload, api='api/v1')
    upload_url = f'{api_url}/uploads'
    header_flag = f"-H 'Upload-Token: {token}'"

    upload_command = f"curl -X POST {header_flag} '{upload_url}' -T <local_file>"
    upload_command_form = (
        f"curl -X POST {header_flag} '{upload_url}' -F file=@<local_file>"
    )
    upload_command_with_name = (
        f"curl -X POST {header_flag} '{upload_url}?upload_name=<name>' -T <local_file>"
    )
    upload_progress_command = upload_command + ' | xargs echo'
    upload_tar_command = f"tar -cf - <local_folder> | curl -# {header_flag} '{upload_url}' -X POST -T - | xargs echo"

    return UploadCommandExamplesResponse(
        upload_url=upload_url,
        upload_command=upload_command,
        upload_command_form=upload_command_form,
        upload_command_with_name=upload_command_with_name,
        upload_progress_command=upload_progress_command,
        upload_tar_command=upload_tar_command,
    )


@router.post(
    '',
    tags=[APITag.DEFAULT],
    summary='Submit a new upload',
    response_class=StreamingResponse,
    responses=create_responses(_post_upload_response, _not_authorized, _bad_request),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
async def post_upload(
    request: Request,
    user: Annotated[
        User,
        Depends(
            get_current_user(
                [Scope.UPLOADS_WRITE], allow_anonymous=False, allow_upload_token=True
            )
        ),
    ],
    file: Annotated[list[UploadFile] | None, File()] = None,
    local_path: Annotated[
        str | None,
        FastApiQuery(
            description=strip(
                """
            Internal/Admin use only."""
            )
        ),
    ] = None,
    example_upload_id: Annotated[
        str | None,
        FastApiQuery(
            description=strip(
                """
            If provided, instantiates a new upload from the given example upload
            entry point id. You may use this parameter in combination with other
            file sources.
            """
            )
        ),
    ] = None,
    file_name: Annotated[
        str | None,
        FastApiQuery(
            description=strip(
                """
            Specifies the name of the file, when using method 2."""
            )
        ),
    ] = None,
    upload_name: Annotated[
        str | None,
        FastApiQuery(
            description=strip(
                """
            A human readable name for the upload."""
            )
        ),
    ] = None,
    embargo_length: Annotated[
        int,
        FastApiQuery(
            description=strip(
                """
            The requested embargo length, in months, if any (0-36)."""
            )
        ),
    ] = 0,
    publish_directly: Annotated[
        bool | None,
        FastApiQuery(
            description=strip(
                """
            If the upload should be published directly. False by default."""
            )
        ),
    ] = None,
    auto_decompress: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """
            Automatically decompress uploaded files upon receiving (ZIP or TAR). True by default."""
            )
        ),
    ] = True,
):
    """
    Creates a new, empty upload and, optionally, uploads one or more files to it. If zip or
    tar files are uploaded, they will first be extracted, then added.

    It is recommended to give the upload itself a descriptive `upload_name`. If not specified,
    and a single file is provided, `upload_name` will be set to the name of this file. The
    `upload_name` can also be edited afterwards (as long as the upload is not published).

    There are two basic ways to upload files: in the multipart-formdata or streaming the
    file data in the http body. Both are supported. Note, however, that the second method
    only allows the upload of a single file, and that it does not transfer a filename. If a
    transfer is made using method 2, you can specify the query argument `file_name` to name it.
    This *needs* to be specified when using method 2, unless you are uploading a zip file
    (for zip files the names don't matter since they are extracted).

    Example `curl` commands for creating an upload and uploading a file:

    Method 1: multipart/formdata

        curl -X 'POST' "url" -F file=@local_file

    Method 2: streaming data

        curl -X 'POST' "url?file_name=filename" -T local_file

    Authentication is required. This can either be done using the regular bearer token,
    or using the simplified upload token. To use the simplified upload token, just
    specify it as a header, i.e.

        curl -H 'Upload-Token: ABC.XYZ' -X 'POST' "url"  ...

    Note, there is a limit on how many unpublished uploads a user can have. If exceeded,
    error code 400 will be returned.
    """
    if not user.is_admin:
        # Check upload limit
        limit_exceeded = await anyio.to_thread.run_sync(
            lambda: (
                _query_mongodb(main_author=str(user.user_id), publish_time=None).count()
                >= config.services.upload_limit
            )
        )
        if limit_exceeded:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=strip(
                    """
                Limit of unpublished uploads exceeded for user."""
                ),
            )

    if not 0 <= embargo_length <= 36:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='`embargo_length` must be between 0 and 36 months.',
        )

    upload_id = utils.create_uuid()

    upload_paths, upload_folders, method = await _get_files_if_provided(
        upload_id, request, file, local_path, file_name, user
    )

    if not upload_name:
        # Try to default upload_name
        if example_upload_id:
            try:
                entry_point = cast(
                    ExampleUploadEntryPoint,
                    config.get_plugin_entry_point(example_upload_id),
                )
            except Exception:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=f'Could not find example upload with id "{example_upload_id}"',
                )
            upload_name = entry_point.title
        elif method == 2:
            upload_name = file_name or None
        elif len(upload_paths) == 1:
            upload_name = os.path.basename(upload_paths[0])

    file_operations = [
        dict(
            op='ADD',
            path=upload_path,
            target_dir=upload_folders[i_path],
            temporary=(method != 0),
            auto_decompress=auto_decompress,
        )
        for i_path, upload_path in enumerate(upload_paths)
    ]

    def create_upload_sync() -> Upload:
        upload_obj: Upload = Upload.create(
            upload_id=upload_id,
            main_author=user,
            upload_name=upload_name,
            upload_create_time=datetime.now(timezone.utc),
            embargo_length=embargo_length,
            publish_directly=publish_directly,
        )

        # Create staging files
        files.StagingUploadFiles(upload_id=upload_id, create=True)

        logger.info('upload created', upload_id=upload_id)

        # If creating an example upload, the contents are loaded only during the
        # first processing: they should not be loaded anymore in later reprocessing.
        if example_upload_id is not None:
            upload_obj.process_example_upload(example_upload_id, file_operations)
        elif upload_paths:
            upload_obj.process_upload(file_operations)
        return upload_obj

    upload = await anyio.to_thread.run_sync(create_upload_sync)

    if request.headers.get('Accept') == 'application/json':
        upload_data = await anyio.to_thread.run_sync(upload_to_pydantic, upload)
        upload_proc_data_response = UploadProcDataResponse(
            upload_id=upload_id, data=upload_data
        )
        response_text = upload_proc_data_response.model_dump_json()
        media_type = 'application/json'
    else:
        response_text = _thank_you_message
        media_type = 'text/plain'

    return StreamingResponse(
        create_stream_from_string(response_text), media_type=media_type
    )


@router.delete(
    '/{upload_id}',
    tags=[APITag.DEFAULT],
    summary='Delete an upload',
    response_model=UploadProcDataResponse,
    responses=create_responses(
        _upload_not_found, _not_authorized_to_upload, _bad_request
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def delete_upload(
    upload_id: Annotated[
        str, Path(description='The unique id of the upload to delete.')
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_WRITE], allow_anonymous=False)),
    ],
    wait_for_processing: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """Waits for the processing to complete and return information about the outcome in the response (**USE WITH CARE**)."""
            )
        ),
    ] = True,
):
    """
    Delete an existing upload.

    Only uploads that are sill in staging, not already deleted, not still uploaded, and
    not currently processed, can be deleted.
    """
    upload = _get_upload_with_write_access(
        upload_id,
        user,
        include_published=True,
        published_requires_admin=True,
        include_failed_imports=True,
        only_main_author=True,
    )
    try:
        upload.delete_upload(wait_for_processing=wait_for_processing)
    except ProcessAlreadyRunning:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=strip(
                """
            The upload is still being processed."""
            ),
        )
    except Exception as e:
        logger.error('could not delete processing upload', exc_info=e)
        raise

    return UploadProcDataResponse(upload_id=upload_id, data=upload_to_pydantic(upload))


# Metadata endpoints


@router.get(
    '',
    tags=[APITag.METADATA],
    summary='List uploads of authenticated user.',
    response_model=UploadProcDataQueryResponse,
    responses=create_responses(_not_authorized, _bad_pagination, _bad_request),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_uploads(
    request: Request,
    query: Annotated[UploadProcDataQuery, Depends(upload_proc_data_query_parameters)],
    pagination: Annotated[
        UploadProcDataPagination, Depends(upload_proc_data_pagination_parameters)
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_READ], allow_anonymous=False)),
    ],
    roles: Annotated[
        list[UploadRole] | None,
        FastApiQuery(
            description='Only return uploads where the user has one of the given roles.'
        ),
    ] = None,
    include_all: Annotated[
        bool,
        FastApiQuery(description='Include uploads that are shared with all users.'),
    ] = False,
):
    """
    Retrieves metadata about all uploads that match the given query criteria.
    """
    # Build query
    role_query = get_role_query(roles, user, include_all=include_all)
    try:
        mongo_query = create_mongo_query(
            query,
            base_query=role_query,
            auth_user_id=str(user.user_id),
        )
    except MongoQueryError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Create response
    start = pagination.get_simple_index()
    end = start + pagination.page_size

    # Fetch data from DB
    mongodb_query = pagination.order_result(Upload.objects.filter(mongo_query))  # type: ignore
    uploads = list(mongodb_query[start:end])
    upload_ids = [upload.upload_id for upload in uploads]

    # Batch fetch entry counts
    counts = {
        item['_id']: item['count']
        for item in Entry.objects(upload_id__in=upload_ids).aggregate(
            [{'$group': {'_id': '$upload_id', 'count': {'$sum': 1}}}]
        )
    }

    data = [upload_to_pydantic(upload, include_total_count=False) for upload in uploads]
    for pydantic_upload in data:
        pydantic_upload.entries = counts.get(pydantic_upload.upload_id, 0)

    pagination_response = PaginationResponse(
        total=mongodb_query.count(), **pagination.dict()
    )
    pagination_response.populate_simple_index_and_urls(request)

    return UploadProcDataQueryResponse(
        query=query, pagination=pagination_response, data=data
    )


@router.get(
    '/{upload_id}',
    tags=[APITag.METADATA],
    summary='Get a specific upload',
    response_model=UploadProcDataResponse,
    responses=create_responses(_upload_not_found, _not_authorized_to_upload),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_upload(
    upload_id: Annotated[
        str, Path(description='The unique id of the upload to retrieve.')
    ],
    user: Annotated[User, Depends(get_current_user([Scope.UPLOADS_READ]))],
):
    """
    Fetches a specific upload by its upload_id.
    """
    # Get upload (or throw exception if nonexistent/no access)
    upload = get_upload_with_read_access(upload_id, user, include_others=True)

    return UploadProcDataResponse(upload_id=upload_id, data=upload_to_pydantic(upload))


@router.get(
    '/{upload_id}/entries',
    tags=[APITag.METADATA],
    summary='Get the entries of the specific upload as a list',
    response_model=EntryProcDataQueryResponse,
    responses=create_responses(
        _upload_not_found, _not_authorized_to_upload, _bad_pagination
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_upload_entries(
    request: Request,
    upload_id: Annotated[
        str, Path(description='The unique id of the upload to retrieve entries for.')
    ],
    pagination: Annotated[
        EntryProcDataPagination, Depends(entry_proc_data_pagination_parameters)
    ],
    user: Annotated[User, Depends(get_current_user([Scope.UPLOADS_READ]))],
):
    """
    Fetches the entries of a specific upload. Pagination is used to browse through the
    results.
    """
    upload = get_upload_with_read_access(upload_id, user, include_others=True)

    order_by = pagination.order_by
    assert order_by is not None
    order_by_with_sign = (
        order_by if pagination.order == Direction.asc else '-' + order_by
    )

    start = pagination.get_simple_index()
    end = start + pagination.page_size

    # load upload's entries. Use entry_id as tie breaker for ordering.
    entries = list(
        upload.entries_sublist(start, end, order_by=(order_by_with_sign, 'entry_id'))
    )
    failed_entries_count = upload.failed_entries_count

    # load entries's metadata from search
    metadata_entries_query = WithQuery(
        query={'entry_id:any': list(entry.entry_id for entry in entries)}
    ).query
    metadata_entries = search(
        pagination=MetadataPagination(page_size=len(entries)),
        owner='admin' if user is not None and user.is_admin else 'visible',
        user_id=user.user_id if user is not None else None,
        query=metadata_entries_query,
    )
    metadata_entries_map = {
        metadata_entry['entry_id']: metadata_entry
        for metadata_entry in metadata_entries.data
    }

    # convert data to pydantic
    data = []
    for entry in entries:
        pydantic_entry = entry_to_pydantic(entry)
        pydantic_entry.entry_metadata = metadata_entries_map.get(entry.entry_id)
        data.append(pydantic_entry)

    pagination_response = PaginationResponse(
        total=upload.total_entries_count, **pagination.dict()
    )
    pagination_response.populate_simple_index_and_urls(request)

    return EntryProcDataQueryResponse(
        pagination=pagination_response,
        processing_successful=upload.processed_entries_count - failed_entries_count,
        processing_failed=failed_entries_count,
        upload=upload_to_pydantic(upload),
        data=data,
    )


@router.get(
    '/{upload_id}/entries/{entry_id}',
    tags=[APITag.METADATA],
    summary='Get a specific entry for a specific upload',
    response_model=EntryProcDataResponse,
    responses=create_responses(_entry_not_found, _not_authorized_to_entry),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_upload_entry(
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    entry_id: Annotated[
        str,
        Path(
            description='The unique id of the entry, belonging to the specified upload.'
        ),
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_READ], allow_anonymous=False)),
    ],
):
    """
    Fetches a specific entry for a specific upload.
    """
    upload = get_upload_with_read_access(upload_id, user)
    entry = upload.get_entry(entry_id)
    if not entry:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=strip(
                """
            An entry by that id could not be found in the specified upload."""
            ),
        )

    data = entry_to_pydantic(entry, add_es_metadata=True, user=user)

    return EntryProcDataResponse(entry_id=entry_id, data=data)


@router.post(
    '/{upload_id}/edit',
    tags=[APITag.METADATA],
    summary='Updates the metadata of the specified upload.',
    response_model=UploadProcDataResponse,
    responses=create_responses(
        _upload_not_found, _not_authorized_to_upload, _bad_request
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
async def post_upload_edit(
    request: Request,
    data: MetadataEditRequest,
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_WRITE], allow_anonymous=False)),
    ],
    wait_for_processing: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """Waits for the processing to complete and return information about the outcome in the response (**USE WITH CARE**)."""
            )
        ),
    ] = True,
):
    """
    Updates the metadata of the specified upload and entries. An optional `query` can be
    specified to select only some of the entries of the upload (the query results are
    automatically restricted to the specified upload).

    **Note:**
      - Only admins can edit some of the fields.
      - The embargo of a published upload is lifted by setting the `embargo_length` attribute
        to 0.
      - If the upload is published, the only operations permitted using this endpoint is to
        lift the embargo, i.e. set `embargo_length` to 0, and to edit the entries in datasets
        that where created by the current user.
      - If a query is specified, it is not possible to edit upload level metadata (like
        `upload_name`, `coauthors`, etc.), as the purpose of queries is to select only a
        subset of the upload entries to edit, but changing upload level metadata would affect
        **all** entries of the upload.
    """
    edit_request_json = await request.json()
    try:
        await anyio.to_thread.run_sync(
            functools.partial(
                MetadataEditRequestHandler.edit_metadata,
                edit_request_json,
                upload_id,
                user,
                wait_for_processing=wait_for_processing,
            )
        )
        upload = await anyio.to_thread.run_sync(Upload.get, upload_id)
        upload_data = await anyio.to_thread.run_sync(upload_to_pydantic, upload)
        return UploadProcDataResponse(upload_id=upload_id, data=upload_data)
    except RequestValidationError:
        raise  # A problem which we have handled explicitly. Fastapi does json conversion.
    except Exception as e:
        # The upload is processing or some kind of unexpected error has occurred
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


# Raw file endpoints


@router.get(
    '/{upload_id}/rawdir/{path:path}',
    tags=[APITag.RAW],
    summary='Get the metadata for the raw file or folder located at the specified path in the specified upload.',
    response_model=RawDirResponse,
    responses=create_responses(
        _upload_or_path_not_found, _not_authorized_to_upload, _bad_request
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
@traced(span_name='uploads.get_upload_rawdir_path')
def get_upload_rawdir_path(
    request: Request,
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    path: Annotated[str, Path(description='The path within the upload raw files.')],
    pagination: Annotated[RawDirPagination, Depends(rawdir_pagination_parameters)],
    user: Annotated[User, Depends(get_current_user([Scope.UPLOADS_READ]))],
    include_entry_info: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """
                If the fields `entry_id` and `parser_name` should be populated for all
                encountered mainfiles."""
            )
        ),
    ] = False,
):
    """
    For the upload specified by `upload_id`, gets the raw file or directory metadata
    located at the given `path`. The response will either contain a `file_metadata` or
    `directory_metadata` key. For files, basic data about the file is returned, such as its
    name and size. For directories, the response includes a list of elements
    (files and folders) in the directory. For directories, the result is paginated.
    """
    # Get upload
    upload = get_upload_with_read_access(upload_id, user, include_others=True)
    upload_files = None
    try:
        # Get upload files
        upload_files = upload.upload_files
        if not upload_files.raw_exists(path):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=strip(
                    """
                Not found. Invalid path?"""
                ),
            )

        response = RawDirResponse(
            path=path.rstrip('/'),
            access='unpublished'
            if not upload.published
            else ('embargoed' if upload.embargo_length else 'public'),
        )

        if upload_files.raw_isfile(path):
            # Path denotes a file
            response.file_metadata = RawDirFileMetadata(
                name=os.path.basename(path), size=upload_files.raw_file_size(path)
            )
            if include_entry_info:
                entry: Entry = Entry.objects(  # type: ignore
                    upload_id=upload_id, mainfile=path, mainfile_key=None
                ).first()
                if entry:
                    response.file_metadata.entry_id = entry.entry_id
                    response.file_metadata.parser_name = entry.parser_name
        else:
            # Path denotes a directory
            start = pagination.get_simple_index()
            end = start + pagination.page_size
            directory_list = upload_files.raw_listdir(path)
            upload_files.close()
            content = []
            path_to_element: dict[str, RawDirElementMetadata] = {}
            total = 0
            total_size = 0
            for i, path_info in enumerate(directory_list):
                total += 1
                total_size += path_info.size
                if start <= i < end:
                    element = RawDirElementMetadata(
                        name=os.path.basename(path_info.path),
                        is_file=path_info.is_file,
                        size=path_info.size,
                    )
                    content.append(element)
                    if include_entry_info:
                        path_to_element[path_info.path] = element

            if include_entry_info and content:
                for entry in Entry.objects(  # type: ignore  # type: ignore
                    upload_id=upload_id,
                    mainfile__in=path_to_element.keys(),
                    mainfile_key=None,
                ):
                    element = path_to_element[entry.mainfile]
                    element.entry_id = entry.entry_id
                    element.parser_name = entry.parser_name

            response.directory_metadata = RawDirDirectoryMetadata(
                name=os.path.basename(path), size=total_size, content=content
            )

            pagination_response = PaginationResponse(total=total, **pagination.dict())
            pagination_response.populate_simple_index_and_urls(request)
            response.pagination = pagination_response

        return response
    except Exception:
        if upload_files:
            upload_files.close()
        raise


@router.get(
    '/{upload_id}/raw',
    tags=[APITag.RAW],
    summary='Downloads the published upload .zip file with all the raw files of the upload.',
    response_class=StreamingResponse,
    responses=create_responses(
        _raw_path_response,
        _upload_or_path_not_found,
        _not_authorized_to_upload,
        _bad_request,
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_upload_raw(
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    user: Annotated[User, Depends(get_current_user([Scope.UPLOADS_READ]))],
):
    """
    NOMAD manages the raw files of published uploads as a .zip file. This endpoint
    allows to download it. While the outcome is similar to using `/uploads/<upload_id>/raw/`
    which creates a .zip file on the fly, this endpoint is more efficient
    because it simply streams an already existing .zip file. On the other hand, this
    endpoint is only available for published uploads and does not allow to selectively
    filter the files.
    """

    # Get upload
    upload = get_upload_with_read_access(upload_id, user, include_others=True)
    # Get upload files
    upload_files = upload.upload_files
    if not isinstance(upload_files, PublicUploadFiles):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=strip(
                """
            Cannot download raw files .zip from non published uploads. Use '/{upload_id}/raw/' instead
            to recursively create and download a .zip file with all files."""
            ),
        )

    raw_zip_file = upload_files.raw_zip_file_object()
    file_path = raw_zip_file.os_path
    storage_fs = upload_files.storage_fs
    if FSUtility.is_local(file_path, fs=storage_fs):
        return FileResponse(file_path, media_type='application/zip')

    if config.fs.public_fs.redirect_downloads and upload_files.access == 'public':
        return RedirectResponse(
            storage_fs.url(
                raw_zip_file.location,
                expires=config.fs.public_fs.signed_url_expiration,
            )
        )

    def file_stream():
        with FSUtility.open(file_path, fs=storage_fs) as file_obj:
            while chunk := file_obj.read(2**20):
                yield chunk

    return StreamingResponse(file_stream(), media_type='application/zip')


@router.get(
    '/{upload_id}/raw/{path:path}',
    tags=[APITag.RAW],
    summary='Download the raw file or folder located at the specified path in the specified upload.',
    response_class=StreamingResponse,
    responses=create_responses(
        _raw_path_response,
        _upload_or_path_not_found,
        _not_authorized_to_upload,
        _bad_request,
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
@traced(span_name='uploads.get_upload_raw_path')
def get_upload_raw_path(
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    path: Annotated[str, Path(description='The path within the upload raw files.')],
    files_params: Annotated[Files, Depends(files_parameters)],
    user: Annotated[User, Depends(get_current_user([Scope.UPLOADS_READ]))],
    offset: Annotated[
        int | None,
        FastApiQuery(
            description=strip(
                """
                When dowloading individual files with `compress = false`, this can be
                used to seek to a specified position within the file in question. Default
                is 0, i.e. the start of the file."""
            )
        ),
    ] = 0,
    length: Annotated[
        int | None,
        FastApiQuery(
            description=strip(
                """
                When dowloading individual files with `compress = false`, this can be
                used to specify the number of bytes to read. By default, the value is -1,
                which means that the remainder of the file is streamed."""
            )
        ),
    ] = -1,
    decompress: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """
                Set if compressed files should be decompressed before streaming the
                content (that is: if there are compressed files *within* the raw files).
                Note, only some compression formats are supported."""
            )
        ),
    ] = False,
    ignore_mime_type: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """
                Sets the mime type specified in the response headers to `application/octet-stream`
                instead of the actual mime type."""
            )
        ),
    ] = False,
):
    """
    For the upload specified by `upload_id`, gets the raw file or directory content located
    at the given `path`. The data is zipped if `compress = true`.

    It is possible to download both individual files and directories, but directories can
    only be downloaded if `compress = true`. When downloading a directory, it is also
    possible to specify `re_pattern`, `glob_pattern` or `include_files` to filter the files
    based on the file names.

    When downloading a file, you can specify `decompress` to attempt to decompress the data
    if the file is compressed before streaming it. You can also specify `offset` and `length`
    to download only a segment of the file (*Note:* `offset` and `length` does not work if
    `compress` is set to true).
    """
    if files_params.compress and (offset != 0 or length != -1):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=strip(
                """
            Cannot specify `offset` or `length` when `compress` is true"""
            ),
        )
    # Get upload
    upload = get_upload_with_read_access(upload_id, user, include_others=True)
    # Get upload files
    upload_files = upload.upload_files
    raw_path_reader = upload_files.raw_path_reader(path)
    try:
        if not raw_path_reader.exists():
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=strip(
                    """
                Not found. Invalid path?"""
                ),
            )
        if raw_path_reader.isfile():
            # File
            if files_params.compress:
                media_type = 'application/zip'
                content = create_download_stream_zipped(
                    DownloadItem(
                        upload_id=upload_id,
                        raw_path=path,
                        zip_path=os.path.basename(path),
                    ),
                    upload_files,
                    compress=True,
                )
            else:
                if offset is not None and offset < 0:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail=strip(
                            """
                        Invalid offset provided."""
                        ),
                    )
                if length is not None and length <= 0 and length != -1:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail=strip(
                            """
                        Invalid length provided. Should be greater than 0, or -1 if the remainder
                        of the file should be read."""
                        ),
                    )
                if ignore_mime_type or not (offset == 0 and length == -1):
                    media_type = 'application/octet-stream'
                else:
                    media_type = raw_path_reader.mime_type()
                content = create_download_stream_raw_file(
                    upload_files, path, offset, length, decompress, raw_path_reader
                )
                # The download stream closes the reader after the body is sent.
                raw_path_reader = None
            return StreamingResponse(
                content,
                headers=browser_download_headers(
                    filename=os.path.basename(path)
                    + ('.zip' if files_params.compress else ''),
                    media_type=media_type,
                ),
            )
        else:
            # Directory
            if not files_params.compress:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=strip(
                        """
                    Path is a directory, `compress` must be set to true"""
                    ),
                )
            # Stream directory content, compressed.
            return StreamingResponse(
                create_download_stream_zipped(
                    DownloadItem(upload_id=upload_id, raw_path=path, zip_path=''),
                    upload_files,
                    re_pattern=files_params.re_pattern,
                    recursive=True,
                    create_manifest_file=False,
                    compress=True,
                ),
                headers=browser_download_headers(
                    (
                        upload.upload_id
                        if not path
                        else os.path.basename(path.rstrip('/'))
                    )
                    + '.zip',
                    media_type='application/zip',
                ),
            )
    except Exception as e:
        if not isinstance(e, HTTPException):
            logger.error('exception while streaming download', exc_info=e)
        upload_files.close()
        raise
    finally:
        if raw_path_reader is not None:
            raw_path_reader.close()


@router.put(
    '/{upload_id}/raw/{path:path}',
    tags=[APITag.RAW],
    summary='Upload a raw file to the specified path (directory) in the specified upload.',
    response_class=StreamingResponse,
    responses=create_responses(
        _put_raw_file_response,
        _upload_not_found,
        _not_authorized_to_upload,
        _bad_request,
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
async def put_upload_raw_path(
    request: Request,
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    path: Annotated[str, Path(description='The path within the upload raw files.')],
    user: Annotated[
        User,
        Depends(
            get_current_user(
                [Scope.UPLOADS_WRITE], allow_anonymous=False, allow_upload_token=True
            )
        ),
    ],
    file: Annotated[list[UploadFile] | None, File()] = None,
    local_path: Annotated[
        str | None, FastApiQuery(description=strip("""Internal/Admin use only."""))
    ] = None,
    file_name: Annotated[
        str | None,
        FastApiQuery(
            description=strip(
                """Specifies the name of the file, when using method 2."""
            )
        ),
    ] = None,
    overwrite_if_exists: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """If set to True (default), overwrites the file if it already exists."""
            )
        ),
    ] = True,
    copy_or_move: Annotated[
        str | None,
        FastApiQuery(
            description=strip(
                """If moving or copying a file within the same upload, specify which operation to do: move or copy"""
            )
        ),
    ] = None,
    copy_or_move_source_path: Annotated[
        str | None,
        FastApiQuery(
            description=strip(
                """If moving or copying a file within the same upload, specify the path to the source file."""
            )
        ),
    ] = None,
    wait_for_processing: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """Waits for the processing to complete and return information about the outcome in the response (**USE WITH CARE**)."""
            )
        ),
    ] = False,
    include_archive: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """If the archive data should be included in the response when using `wait_for_processing` (**USE WITH CARE**)."""
            )
        ),
    ] = False,
    entry_hash: Annotated[
        str | None,
        FastApiQuery(description=strip("""The hash code of the not modified entry.""")),
    ] = None,
    auto_decompress: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """
            Automatically decompress uploaded files upon receiving (ZIP or TAR). True by default."""
            )
        ),
    ] = True,
    trigger_processing: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """
            If set to true (default), reprocesses the upload after deleting the file/folder."""
            ),
        ),
    ] = True,
):
    """
    Upload one or more files to the directory specified by `path` in the upload specified by `upload_id`.

    When uploading a zip or tar archive, it will first be extracted, and the content will be
    *merged* with the existing content, i.e. new files are added, and if there is a collision
    (an old file with the same path and name as one of the new files), the old file will
    be overwritten, but the rest of the old files will remain untouched. If the file is not
    a zip or tar archive, the file will just be uploaded as it is, overwriting the existing
    file if there is one.

    The `path` should denote a directory. The empty string gives the "root" directory.

    If a single file is uploaded (and it is not a zip or tar archive), it is possible to specify
    `wait_for_processing`. This means that the file (and only this file) will be matched and
    processed, and information about the outcome will be returned with the response. **NOTE**:
    this should be used with caution! When this option is set, the call will block until
    processing is complete, which may take some time. Also note, that just processing the
    new/modified file may not be enough in some cases (since adding/modifying a file somewhere
    in the directory structure may affect other entries). Also note that
    processing.entry.entry_metadata will not be populated in the response.

    There are two basic ways to upload files: in the multipart-formdata or streaming the
    file data in the http body. Both are supported. Note, however, that the second method
    only allows the upload of a single file, and that it does not transfer a filename. If a
    transfer is made using method 2, you can specify the query argument `file_name` to name it.
    This *needs* to be specified when using method 2, unless you are uploading a zip/tar file
    (for zip/tar files the names don't matter since they are extracted). See the POST `uploads`
    endpoint for examples of `curl` commands for uploading files.

    Also, this path can be used to copy/move a file from one directory to another. Three
    query parameters are required for a successful operation: 1) `copy_or_move` param to specify
    if the file needs to be moved (if set to move then the original file will be removed), 2)
    `file_name` param that contains the new name for the file moved/copied file and 3) `copy_or_move_source_path`
    param that contains the path of the original/existing local file to be copied or moved.
    """
    if include_archive and not wait_for_processing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='`include_archive` requires `wait_for_processing`.',
        )
    if wait_for_processing and not trigger_processing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='`trigger_processing` must be true when `wait_for_processing` is set.`',
        )

    upload = await anyio.to_thread.run_sync(
        _get_upload_with_write_access, upload_id, user, False
    )

    if local_path and not await anyio.to_thread.run_sync(os.path.isfile, local_path):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='Uploading folders with local_path is not yet supported.',
        )

    if not is_safe_relative_path(path):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Bad path provided.')

    if copy_or_move is not None or copy_or_move_source_path is not None:
        if copy_or_move not in ['copy', 'move']:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="The copy_or_move query parameter should be one of 'copy' or 'move' options.",
            )

        if (
            copy_or_move is None
            or copy_or_move_source_path is None
            or file_name is None
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="""For a successful copy/move operation, all three query parameters: file_name, copy_or_move and copy_or_move_source_path are required.""",
            )

        if not is_safe_basename(file_name):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail='Bad file name provided'
            )

        if not is_safe_relative_path(copy_or_move_source_path):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='Bad source path provided.',
            )

    upload_paths, _, method = await _get_files_if_provided(
        upload_id, request, file, local_path, file_name, user
    )

    if not upload_paths and not (
        copy_or_move and copy_or_move_source_path and file_name
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='Either an upload file or the query parameters for moving/copying a file should be provided.',
        )

    def execute_put_raw():
        if entry_hash:
            upload_path = upload_paths[0]
            full_path = os.path.join(path, os.path.basename(upload_path))
            entry_id = utils.generate_entry_id(upload_id, full_path)
            entry = upload.get_entry(entry_id)
            if entry and entry_hash != entry.entry_hash or not entry:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail='The provided hash did not match the current file.',
                )

        upload_files = StagingUploadFiles(upload_id)

        compression_format = None
        for upload_path in upload_paths:
            compression_format = (
                get_compression_format(upload_path) if auto_decompress else None
            )
            if compression_format == 'error':
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail='Cannot extract file. Bad file format or file extension?',
                )
            if not compression_format and not overwrite_if_exists:
                full_path = os.path.join(path, os.path.basename(upload_path))
                if upload_files.raw_exists(full_path):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail='The provided path already exists and overwrite_if_exists is set to False.',
                    )

        if not wait_for_processing:
            # Process on worker (normal case)
            if copy_or_move:  # the case for move/copy an existing file
                path_to_target_file = os.path.join(path, file_name)
                if upload_files.raw_exists(path_to_target_file):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail='The provided path already exists.',
                    )
                if not upload_files.raw_exists(path):
                    raise HTTPException(
                        status.HTTP_404_NOT_FOUND,
                        detail='No file or folder with that path found.',
                    )
                if not upload_files.raw_exists(copy_or_move_source_path):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail=f'No file or folder with that source path: {copy_or_move_source_path}',
                    )
                if path_to_target_file == copy_or_move_source_path or (
                    path_to_target_file.startswith(f'{copy_or_move_source_path}/')
                ):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail=(
                            f"Cannot {copy_or_move} '{copy_or_move_source_path}' into "
                            f"itself or one of its own subfolders ('{path_to_target_file}')."
                        ),
                    )
                file_operations = [
                    dict(
                        op=copy_or_move.upper(),
                        path_to_existing_file=copy_or_move_source_path,
                        path_to_target_file=path_to_target_file,
                    )
                ]
            else:
                file_operations = [
                    dict(
                        op='ADD',
                        path=upload_path,
                        target_dir=path,
                        temporary=(method != 0),
                        auto_decompress=auto_decompress,
                    )
                    for upload_path in upload_paths
                ]

            # Initiate processing
            try:
                upload.process_upload(
                    file_operations=file_operations,
                    only_updated_files=True,
                    trigger_processing=trigger_processing,
                )
            except ProcessAlreadyRunning:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail='The upload is currently blocked by another process.',
                )

            # Create response
            if request.headers.get('Accept') == 'application/json':
                response = PutRawFileResponse(
                    upload_id=upload_id, data=upload_to_pydantic(upload)
                )
                response_text = response.model_dump_json()
                media_type = 'application/json'
            else:
                response_text = _thank_you_message
                media_type = 'text/plain'

            return StreamingResponse(
                create_stream_from_string(response_text), media_type=media_type
            )

        # Process locally
        if copy_or_move:  # case for move/copy an existing file
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='Cannot move/copy the file with wait_for_processing set to true.',
            )

        if len(upload_paths) != 1 or compression_format:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='`wait_for_processing` can only be used with single files, and not with compressed files.',
            )

        upload_path = upload_paths[0]
        full_path = os.path.join(path, os.path.basename(upload_path))
        try:
            entry = upload.put_file_and_process_local(
                upload_path,
                path,
                reprocess_settings=Reprocess(
                    index_individual_entries=True, reprocess_existing_entries=True
                ),
            )

            search_refresh()

            archive = None
            if (
                entry
                and entry.process_status == ProcessStatus.SUCCESS
                and include_archive
            ):
                # NOTE: We can't rely on ES to get the metadata for the entry, since it may
                # not have had enough time to update its index etc. For now, we will just
                # ignore this, as we do not need it.
                archive = answer_entry_archive_request(
                    dict(upload_id=upload_id, mainfile=full_path),
                    required='*',
                    user=user,
                    entry_metadata=dict(
                        upload_id=upload_id,
                        entry_id=entry.entry_id,
                        parser_name=entry.parser_name,
                    ),
                )['data']['archive']

            response = PutRawFileResponse(
                upload_id=upload_id,
                data=upload_to_pydantic(upload),
                processing=ProcessingData(
                    upload_id=upload_id,
                    path=full_path,
                    entry_id=entry.entry_id if entry else None,
                    parser_name=entry.parser_name if entry else None,
                    entry=entry_to_pydantic(entry) if entry else None,
                    archive=archive,
                ),
            )

            return StreamingResponse(
                create_stream_from_string(response.model_dump_json()),
                media_type='application/json',
            )
        except HTTPException:
            raise
        except ProcessAlreadyRunning:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='The upload is currently being processed, operation not allowed.',
            )
        except Exception as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f'Unexpected exception occurred: {e}',
            )

    try:
        return await anyio.to_thread.run_sync(execute_put_raw)
    finally:
        if wait_for_processing and method != 0 and upload_paths:
            try:
                await anyio.to_thread.run_sync(
                    shutil.rmtree, os.path.dirname(upload_paths[0])
                )
            except Exception:  # noqa
                pass


@router.delete(
    '/{upload_id}/raw/{path:path}',
    tags=[APITag.RAW],
    summary='Delete the raw file or folder located at the specified path in the specified upload.',
    response_model=UploadProcDataResponse,
    responses=create_responses(
        _upload_not_found, _not_authorized_to_upload, _bad_request
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def delete_upload_raw_path(
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    path: Annotated[str, Path(description='The path within the upload raw files.')],
    user: Annotated[
        User,
        Depends(
            get_current_user(
                [Scope.UPLOADS_WRITE], allow_anonymous=False, allow_upload_token=True
            )
        ),
    ],
    trigger_processing: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """
            If set to true (default), reprocesses the upload after deleting the file/folder."""
            ),
        ),
    ] = True,
):
    """
    Delete file or folder located at the specified path in the specified upload. The upload
    must not be published. This also automatically triggers a reprocessing of the upload.
    Choosing the empty string as `path` deletes all files.
    """
    upload = _get_upload_with_write_access(upload_id, user, include_published=False)

    if not is_safe_relative_path(path):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Bad path provided.')

    upload_files = StagingUploadFiles(upload_id)

    if not upload_files.raw_exists(path):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail='No file or folder with that path found.',
        )

    try:
        upload.process_upload(
            file_operations=[dict(op='DELETE', path=path)],
            only_updated_files=True,
            trigger_processing=trigger_processing,
        )
    except ProcessAlreadyRunning:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='The upload is currently blocked by another process.',
        )

    return UploadProcDataResponse(upload_id=upload_id, data=upload_to_pydantic(upload))


@router.post(
    '/{upload_id}/raw-create-dir/{path:path}',
    tags=[APITag.RAW],
    summary='Create a new empty directory with the specified path in the specified upload.',
    response_model=UploadProcDataResponse,
    responses=create_responses(
        _upload_not_found, _not_authorized_to_upload, _bad_request
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def post_upload_raw_create_dir_path(
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    path: Annotated[str, Path(description='The path within the upload raw files.')],
    user: Annotated[
        User,
        Depends(
            get_current_user(
                [Scope.UPLOADS_WRITE], allow_anonymous=False, allow_upload_token=True
            )
        ),
    ],
):
    """
    Create a new empty directory in the specified upload. The `path` should be the full path
    to the new directory (i.e. ending with the name of the new directory). The api call returns
    immediately (no processing is necessary).
    """
    upload = _get_upload_with_write_access(upload_id, user, include_published=False)

    if not path or not is_safe_relative_path(path):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail='Bad path provided.')
    if upload.staging_upload_files.raw_exists(path):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f'Path `{path}` already exists.',
        )
    try:
        upload.staging_upload_files.raw_create_directory(path)
        # No real processing is needed when just adding a folder, but we should signal that
        # the upload has changed.
        upload.complete_time = datetime.now(timezone.utc)
        upload.save()
    except Exception as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f'Failed to create directory: {e}',
        )

    return UploadProcDataResponse(upload_id=upload_id, data=upload_to_pydantic(upload))


# Archive endpoints


@router.get(
    '/{upload_id}/archive/mainfile/{mainfile:path}',
    tags=[APITag.ARCHIVE],
    summary='Get the full archive for the given upload and mainfile path.',
    response_model=EntryArchiveResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    responses=create_responses(_upload_or_path_not_found, _not_authorized_to_upload),
)
def get_upload_entry_archive_mainfile(
    user: Annotated[User, Depends(get_current_user([Scope.UPLOADS_READ]))],
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    mainfile: Annotated[
        str, Path(description="The mainfile path within the upload's raw files.")
    ],
    mainfile_key: Annotated[
        str | None,
        FastApiQuery(description='The mainfile_key, for accessing child entries.'),
    ] = None,
):
    """
    For the upload specified by `upload_id`, gets the full archive of a single entry that
    is identified by the given `mainfile`.
    """
    get_upload_with_read_access(upload_id, user, include_others=True)
    query = dict(upload_id=upload_id, mainfile=mainfile)
    if mainfile_key:
        query.update(mainfile_key=mainfile_key)
    return answer_entry_archive_request(query, required='*', user=user)


@router.get(
    '/{upload_id}/archive/{entry_id}',
    tags=[APITag.ARCHIVE],
    summary='Get the full archive for the given upload and entry.',
    response_model=EntryArchiveResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    responses=create_responses(_upload_or_path_not_found, _not_authorized_to_upload),
)
def get_upload_entry_archive(
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    entry_id: Annotated[str, Path(description='The unique entry id.')],
    user: Annotated[User, Depends(get_current_user([Scope.UPLOADS_READ]))],
):
    """
    For the upload specified by `upload_id`, gets the full archive of a single entry that
    is identified by the given `entry_id`.
    """
    get_upload_with_read_access(upload_id, user, include_others=True)
    return answer_entry_archive_request(
        dict(upload_id=upload_id, entry_id=entry_id), required='*', user=user
    )
