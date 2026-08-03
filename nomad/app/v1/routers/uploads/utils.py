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

import io
import os
import shutil
import tarfile
import zipfile
from typing import Any
from urllib.parse import unquote, urlparse

import anyio
import requests
from fastapi import HTTPException, Request, UploadFile, status
from mongoengine.queryset.visitor import Q

from nomad import files
from nomad import utils as nomad_utils
from nomad.common import is_safe_basename
from nomad.mongo.groups import MongoUserGroup
from nomad.processing import Entry, ProcessStatus, Upload
from nomad.search import search
from nomad.utils import strip

from ...models import MetadataPagination, User
from .models import EntryProcData, UploadProcData, UploadRole

logger = nomad_utils.get_logger(__name__)


def _get_local_files(local_path: str) -> tuple[list[str], list[str]]:
    """Return files below a local upload path without touching the event loop."""
    if not os.path.exists(local_path):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='The specified local_path cannot be found.',
        )
    if os.path.isfile(local_path):
        return [local_path], ['']

    upload_paths = []
    upload_folders = []
    for root, _, filepaths in os.walk(local_path):
        for uploaded_file in filepaths:
            upload_paths.append(os.path.abspath(os.path.join(root, uploaded_file)))
            folder = os.path.relpath(root, local_path)
            upload_folders.append('' if folder == '.' else folder)
    return upload_paths, upload_folders


def _remove_directory(path: str) -> None:
    if os.path.exists(path):
        shutil.rmtree(path)


def _add_archive_extension(upload_path: str) -> str | None:
    ext = (
        '.zip'
        if zipfile.is_zipfile(upload_path)
        else '.tar'
        if tarfile.is_tarfile(upload_path)
        else None
    )
    if ext:
        shutil.move(upload_path, upload_path + ext)
        return upload_path + ext
    return None


def validate_target_deployment_url(url: str) -> None:
    """Validate the scheme, host, and API path of a target deployment URL."""
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'}:
        raise HTTPException(
            status_code=422, detail='URL must start with http:// or https://'
        )

    if not parsed.netloc:
        raise HTTPException(
            status_code=422, detail='URL must contain a valid host (e.g., example.com)'
        )

    if not parsed.path.endswith('/api'):
        raise HTTPException(status_code=422, detail="URL path must end with '/api'")


def perform_status_check(url: str) -> requests.Response:
    """Issue a GET request used for target deployment health checks."""
    return requests.get(url, timeout=15)


def check_external_deployment_status(deployment_url: str) -> None:
    """Verify that the target deployment responds successfully to its health endpoint."""
    parsed_url = urlparse(deployment_url)
    base_url = f'{parsed_url.scheme}://{parsed_url.netloc}'
    try:
        response = perform_status_check(f'{base_url}/-/health')
        if response.status_code != status.HTTP_200_OK:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='The target deployment is not available or the URL is incorrectly formatted. The target deployment URL should end with /api.',
            )
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='The target deployment is not available. Connection refused.',
        )
    except requests.exceptions.Timeout:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='The target deployment is not available. Timeout.',
        )
    except Exception as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f'Failed to check external deployment health. Error: {str(e)}',
        )


async def _get_files_if_provided(
    tmp_dir_prefix: str,
    request: Request,
    file: list[UploadFile],
    local_path: str,
    file_name: str,
    user: User,
) -> tuple[list[str], list[str], None | int]:
    """
    If the user provides one or more files with the API call, load and save them to a temporary
    folder (or, if method 0 is used, just "forward" the file path). The method thus needs to identify
    which file transfer method was used (0-2), and save the data to disk (if method is 1 or 2).

    Returns file paths, file folders, and the transfer method, or empty paths/folders with
    method None if no file data was provided with the API call.
    """
    # Determine the source data stream
    sources: list[tuple[Any, str]] = []  # List of tuples (source, filename)
    if local_path:
        # Method 0: Local file - only for admin use.
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=strip("""
                You are not authorized to access this path.
                """),
            )
        method = 0
    elif file:
        # Method 1: Data provided as formdata
        method = 1

        async def _async_reader(_f):
            try:
                while _data := await _f.read(io.DEFAULT_BUFFER_SIZE):
                    yield _data
            except Exception as _e:
                raise _e
            finally:
                await _f.close()

        sources = [
            (_async_reader(multipart_file), unquote(multipart_file.filename))
            for multipart_file in file
        ]
    else:
        # Method 2: Data has to be sent streamed in the body
        method = 2
        sources = [(request.stream(), file_name or 'NO NAME')]

    no_file_name_info_provided = not file_name

    for _, source_file_name in sources:
        if not is_safe_basename(source_file_name):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail='Bad file name provided.'
            )

    # Forward the file path (if method == 0) or save the file(s)
    if method == 0:
        upload_paths, upload_folders = await anyio.to_thread.run_sync(
            _get_local_files, local_path
        )
    else:
        tmp_dir = await anyio.to_thread.run_sync(files.mkdtemp, tmp_dir_prefix)
        upload_paths = []
        uploaded_bytes = 0
        upload_folders = []
        for source_stream, source_file_name in sources:
            upload_path = os.path.join(tmp_dir, source_file_name)
            try:
                async with await anyio.open_file(upload_path, 'wb') as f:
                    uploaded_bytes = 0
                    log_interval = 1e9
                    next_log_at = log_interval
                    async for chunk in source_stream:
                        if not chunk:
                            # End of data stream
                            break
                        uploaded_bytes += len(chunk)
                        await f.write(chunk)
                        if uploaded_bytes > next_log_at:
                            logger.info(
                                'large upload in progress',
                                uploaded_bytes=uploaded_bytes,
                            )
                            next_log_at += log_interval
                    logger.info(f'upload completed', uploaded_bytes={uploaded_bytes})
            except Exception as e:
                if not (isinstance(e, RuntimeError) and 'Stream consumed' in str(e)):
                    await anyio.to_thread.run_sync(_remove_directory, tmp_dir)
                    logger.warn('IO error receiving upload data', exc_info=e)
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail='Some IO went wrong, upload probably aborted/disrupted.',
                    )
            upload_paths.append(upload_path)
            upload_folders.append('')

        if not uploaded_bytes and method == 2:
            # No data was provided
            await anyio.to_thread.run_sync(_remove_directory, tmp_dir)
            return [], [], None

    logger.info(f'received uploaded file(s)')
    if method == 2 and no_file_name_info_provided:
        # Only ok if uploaded file is a zip or a tar archive.
        archive_path = await anyio.to_thread.run_sync(
            _add_archive_extension, upload_path
        )
        if archive_path is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='No file name provided, and the file does not look like a zip or tar file.',
            )
        upload_paths = [archive_path]
        upload_folders = ['']

    return upload_paths, upload_folders, method


def _query_mongodb(**kwargs: Any) -> Any:
    """Return uploads matching the given MongoEngine filters."""
    return Upload.objects(**kwargs)


def get_role_query(
    roles: list[UploadRole],
    user: User,
    include_all: bool = False,
) -> Q:
    """Create a MongoDB role filter for a user, defaulting to all upload roles."""
    if not roles:
        roles = list(UploadRole)

    group_ids = MongoUserGroup.get_ids_by_user_id(user.user_id, include_all=include_all)

    role_query = Q()
    if UploadRole.main_author in roles:
        role_query |= Q(main_author=user.user_id)
    if UploadRole.coauthor in roles:
        role_query |= Q(coauthors=user.user_id) | Q(coauthor_groups__in=group_ids)
    if UploadRole.reviewer in roles:
        role_query |= Q(reviewers=user.user_id) | Q(reviewer_groups__in=group_ids)

    return role_query


def is_user_upload_viewer(upload: Upload, user: User | None) -> bool:
    """Check whether a user has read access to an upload."""
    if 'all' in upload.reviewer_groups:
        return True

    if user is None:
        return False

    if user.is_admin:
        return True

    if user.user_id in upload.viewers:
        return True

    group_ids = MongoUserGroup.get_ids_by_user_id(user.user_id)
    if not set(group_ids).isdisjoint(upload.viewer_groups):
        return True

    return False


def is_user_upload_writer(upload: Upload, user: User) -> bool:
    """Check whether a user has write access to an upload."""
    if user.is_admin:
        return True

    if user.user_id in upload.writers:
        return True

    group_ids = MongoUserGroup.get_ids_by_user_id(user.user_id)
    if not set(group_ids).isdisjoint(upload.writer_groups):
        return True

    return False


def get_upload_with_read_access(
    upload_id: str, user: User | None, include_others: bool = False
) -> Upload:
    """
    Determines if the user has read access to the upload. If so, the corresponding Upload
    object is returned. If the upload does not exist, or the user has no read access to
    it, an HTTPException is raised.

    Arguments:
        upload_id: The id of the requested upload.
        user: The authenticated user, if any.
        include_others: If uploads owned by others should be included. Access to the
        uploads of other users is only granted if the upload is published and not under
        embargo.
    """
    mongodb_query = _query_mongodb(upload_id=upload_id)
    upload = mongodb_query.first()
    if upload is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail='The specified upload_id was not found.'
        )

    if is_user_upload_viewer(upload, user):
        return upload

    if not include_others:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail='You do not have access to the specified upload.',
        )

    if not upload.published:
        if user is None:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail='You need to log in to access the specified upload.',
            )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail='You do not have access to the specified upload.',
        )

    if upload.with_embargo:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail='You do not have access to the specified upload - published with embargo.',
        )

    return upload


def _get_upload_with_write_access(
    upload_id: str,
    user: User,
    include_published: bool = False,
    published_requires_admin: bool = True,
    include_failed_imports: bool = False,
    only_main_author: bool = False,
) -> Upload:
    """
    Determines if the user has write access to the upload. If so, the corresponding Upload
    object is returned. If the upload does not exist, or the user has no write access to
    it, an HTTPException is raised.
    """
    if not user:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail='User authentication required to access uploads.',
        )

    upload = _query_mongodb(upload_id=upload_id).first()
    if upload is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail='The specified upload_id was not found.'
        )

    if not is_user_upload_writer(upload, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail='You do not have write access to the specified upload.',
        )

    if only_main_author and not user.is_admin and upload.main_author != user.user_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail='Only main author has permissions for this operation.',
        )

    if not upload.published:
        return upload

    if not include_published:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='Upload is already published, operation not possible.',
        )

    is_failed_import = (
        upload.current_process
        and upload.current_process.startswith('import_bundle')
        and upload.process_status == ProcessStatus.FAILURE
    ) or upload.last_status_message == 'Import bundle failed'
    if (
        published_requires_admin
        and not user.is_admin
        and not (is_failed_import and include_failed_imports)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail='Upload is already published, only admins can perform this operation.',
        )

    return upload


def upload_to_pydantic(
    upload: Upload, *, include_total_count: bool = True
) -> UploadProcData:
    """Converts the mongo db object to an UploadProcData object."""
    pydantic_upload = UploadProcData.model_validate(upload)
    if include_total_count:
        pydantic_upload.entries = upload.total_entries_count
    try:
        pydantic_upload.upload_files_server_path = upload.upload_files.external_os_path
    except KeyError:
        # In case the files are missing for one reason or another
        pass

    return pydantic_upload


def entry_to_pydantic(
    entry: Entry, add_es_metadata: bool = False, user: User | None = None
) -> EntryProcData:
    """
    Converts the mongo db object to an EntryProcData object, and optionally also adds metadata
    from ES
    """
    rv = EntryProcData.model_validate(entry)
    if add_es_metadata:
        # load entries's metadata from search
        metadata_entries = search(
            pagination=MetadataPagination(page_size=1),
            owner='admin' if user.is_admin else 'visible',
            user_id=user.user_id,
            query=dict(entry_id=entry.entry_id),
        )
        if len(metadata_entries.data) == 1:
            rv.entry_metadata = metadata_entries.data[0]
    return rv


def _check_upload_not_processing(upload: Upload) -> None:
    """
    Checks if the upload is processing, and raises a HTTPException (err code 400) if so.
    """
    if upload.process_running:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='The upload is currently being processed, operation not allowed.',
        )
