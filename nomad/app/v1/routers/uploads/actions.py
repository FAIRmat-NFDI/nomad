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

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi import Query as FastApiQuery
from fastapi.exceptions import RequestValidationError
from mongoengine.errors import MongoEngineException

from nomad.app.v1.models.models import TransferBundleRequest
from nomad.datacite import DataCiteException
from nomad.datacite.service import create_doi_for_upload, publish_doi
from nomad.mongo.doi import EmbeddedDOI
from nomad.processing.base import ProcessFailure
from nomad.search import QueryValidationError, search_iterator
from nomad.tracing import traced

from ...models import HTTPExceptionModel, MetadataRequired, restrict_query_to_upload
from . import utils as upload_utils
from .default import (
    MetadataEditRequestHandler,
    ProcessAlreadyRunning,
    ProcessStatus,
    Scope,
    User,
    _bad_request,
    _create_exception,
    _existing_upload_with_findable_state,
    _not_authorized_to_upload,
    _upload_already_has_doi,
    _upload_is_empty,
    _upload_is_unpublished,
    _upload_not_found,
    config,
    create_responses,
    get_current_user,
    strip,
)
from .models import APITag, DeleteEntryFilesRequest, UploadProcDataResponse
from .utils import (
    _check_upload_not_processing,
    _get_upload_with_write_access,
    get_upload_with_read_access,
    upload_to_pydantic,
)

router = APIRouter()

# Custom exceptions

_datacite_not_enabled = (
    status.HTTP_403_FORBIDDEN,
    {
        'model': HTTPExceptionModel,
        'description': 'The DataCite DOI service is not enabled on this deployment.',
    },
)

_datacite_draft_failed = (
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        An error occurred while creating the DOI draft at DataCite. Please contact the administrator.
    """
        ),
    },
)

_datacite_publish_failed = (
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        An error occurred while publishing the DOI at DataCite. Please contact the administrator.
    """
        ),
    },
)
_db_set_upload_doi_failed = (
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        An error occurred while saving the upload doi to the database. Please contact the administrator.
    """
        ),
    },
)


@router.post(
    '/{upload_id}/action/publish',
    tags=[APITag.ACTION],
    summary='Publish an upload',
    response_model=UploadProcDataResponse,
    responses=create_responses(
        _upload_not_found, _not_authorized_to_upload, _bad_request
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def post_upload_action_publish(
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_PUBLISH], allow_anonymous=False)),
    ],
    upload_id: Annotated[
        str,
        Path(
            description=strip(
                """
                The unique id of the upload to publish."""
            )
        ),
    ],
    embargo_length: Annotated[
        int | None,
        FastApiQuery(
            description=strip(
                """
                If provided, updates the embargo length of the upload. The value should
                be between 0 and 36 months. 0 means no embargo."""
            )
        ),
    ] = None,
    to_central_nomad: Annotated[
        bool,
        FastApiQuery(
            description=strip(
                """
            DEPRECATED
            To publish to an external oasis or to the central nomad you can use the new entpoint /uploads/{upload_id}/action/transfer.
                """
            ),
            deprecated=True,
        ),
    ] = False,
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
    Publishes an upload. The upload cannot be modified after this point (except for special
    cases, like when lifting the embargo prematurely, and by admins). After the upload is
    published and the embargo period (if any) is expired, the generated archive entries
    will be publicly visible.
    """
    upload = _get_upload_with_write_access(
        upload_id, user, include_published=True, published_requires_admin=False
    )

    if upload.published and not user.is_admin and not to_central_nomad:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail='Upload already published.'
        )

    _check_upload_not_processing(upload)

    if upload.process_status == ProcessStatus.FAILURE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='Cannot publish an upload that failed processing.',
        )
    if upload.processed_entries_count == 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='Cannot publish an upload without any resulting entries.',
        )
    if embargo_length is not None and not 0 <= embargo_length <= 36:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='Invalid embargo_length. Must be between 0 and 36 months.',
        )

    if to_central_nomad:
        # Publish from an OASIS to the central repository
        if not config.oasis.is_oasis:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='Must be on an OASIS to publish to the central NOMAD repository.',
            )
        if not upload.published:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='The upload must be published on the OASIS first.',
            )
        if not user.is_admin:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail='Only admin of OASIS can publish to the central NOMAD.',
            )
        # Everything looks ok, try to publish it to the central NOMAD!
        upload.publish_externally(embargo_length=embargo_length)
    else:
        # Publish to this repository
        if upload.published:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='The upload is already published.',
            )
        try:
            upload.publish_upload(
                embargo_length=embargo_length, wait_for_processing=wait_for_processing
            )
        except ProcessAlreadyRunning:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='The upload is still/already processed.',
            )

    return UploadProcDataResponse(upload_id=upload_id, data=upload_to_pydantic(upload))


@router.post(
    '/{upload_id}/action/process',
    tags=[APITag.ACTION],
    summary='Manually triggers processing of an upload.',
    response_model=UploadProcDataResponse,
    responses=create_responses(
        _upload_not_found, _not_authorized_to_upload, _bad_request
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def post_upload_action_process(
    upload_id: Annotated[
        str, Path(description='The unique id of the upload to process.')
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_PROCESS], allow_anonymous=False)),
    ],
):
    """
    Processes an upload, i.e. parses the files and updates the NOMAD archive. Only admins
    can process an already published upload.
    """
    upload = _get_upload_with_write_access(
        upload_id, user, include_published=True, published_requires_admin=True
    )

    _check_upload_not_processing(upload)

    upload.process_upload()
    return UploadProcDataResponse(upload_id=upload_id, data=upload_to_pydantic(upload))


@router.post(
    '/{upload_id}/action/delete-entry-files',
    tags=[APITag.ACTION],
    summary='Deletes the files of the entries specified by a query.',
    response_model=UploadProcDataResponse,
    responses=create_responses(
        _upload_not_found, _not_authorized_to_upload, _bad_request
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def post_upload_action_delete_entry_files(
    data: DeleteEntryFilesRequest,
    upload_id: Annotated[
        str,
        Path(
            description='The unique id of the upload within which to delete entry files.'
        ),
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_WRITE], allow_anonymous=False)),
    ],
):
    """Deletes the files of the entries specified by the provided query."""

    upload = _get_upload_with_write_access(upload_id, user, include_published=False)

    # Evaluate query
    if not data.query:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=strip(
                """
            A query must be specified."""
            ),
        )
    restricted_query = restrict_query_to_upload(data.query, upload_id)
    es_entries = search_iterator(
        user_id=user.user_id,
        owner=data.owner,
        query=restricted_query,
        required=MetadataRequired(include=['mainfile']),
    )

    # Determine paths to delete
    try:
        paths_to_delete: set[str] = set()
        for es_entry in es_entries:
            mainfile = es_entry['mainfile']
            path_to_delete = (
                os.path.dirname(mainfile) if data.include_parent_folders else mainfile
            )
            paths_to_delete.add(path_to_delete)
    except QueryValidationError as e:
        raise RequestValidationError(errors=e.errors)

    # Execute operation
    if paths_to_delete:
        try:
            upload.process_upload(
                file_operations=[
                    dict(op='DELETE', path=path_to_delete)
                    for path_to_delete in sorted(paths_to_delete)
                ],
                only_updated_files=True,
            )
        except ProcessAlreadyRunning:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='The upload is currently blocked by another process.',
            )

    return UploadProcDataResponse(upload_id=upload_id, data=upload_to_pydantic(upload))


@router.post(
    '/{upload_id}/action/lift-embargo',
    tags=[APITag.ACTION],
    summary='Lifts the embargo of an upload.',
    response_model=UploadProcDataResponse,
    responses=create_responses(
        _upload_not_found, _not_authorized_to_upload, _bad_request
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def post_upload_action_lift_embargo(
    upload_id: Annotated[
        str, Path(description='The unique id of the upload to lift the embargo for.')
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_PUBLISH], allow_anonymous=False)),
    ],
):
    """Lifts the embargo of an upload."""
    upload = _get_upload_with_write_access(
        upload_id, user, include_published=True, published_requires_admin=False
    )
    _check_upload_not_processing(upload)
    if not upload.published:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=strip(
                """
            Upload is not published, no embargo to lift."""
            ),
        )
    if not upload.with_embargo:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=strip(
                """
            Upload has no embargo."""
            ),
        )
    # Lift the embargo using MetadataEditRequestHandler.edit_metadata
    try:
        MetadataEditRequestHandler.edit_metadata(
            {'metadata': {'embargo_length': 0}}, upload_id, user
        )
        upload.reload()
        return UploadProcDataResponse(
            upload_id=upload_id, data=upload_to_pydantic(upload)
        )
    except Exception as e:
        # Should only happen if the upload just started processing or something unexpected happens
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    '/{upload_id}/action/stop-processing',
    tags=[APITag.ACTION],
    summary='Stops the processing of the upload.',
    response_model=UploadProcDataResponse,
    responses=create_responses(
        _upload_not_found, _not_authorized_to_upload, _bad_request
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def stop_upload_processing(
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_PROCESS], allow_anonymous=False)),
    ],
):
    """
    Stops the processing of the specified upload.
    """
    upload = _get_upload_with_write_access(upload_id, user, include_published=False)

    if upload.process_status != ProcessStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='This functionality is only available when upload process state is pending.',
        )
    upload.stop_processing()

    return UploadProcDataResponse(upload_id=upload_id, data=upload_to_pydantic(upload))


@router.post(
    '/{upload_id}/action/transfer',
    tags=[APITag.ACTION],
    summary='Transfer upload to another NOMAD deployment.',
    response_model=UploadProcDataResponse,
    responses=create_responses(
        _upload_not_found, _not_authorized_to_upload, _bad_request
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
@traced(span_name='uploads.transfer_upload_bundle')
def transfer_upload_bundle(
    transfer_options: TransferBundleRequest,
    upload_id: Annotated[
        str,
        Path(
            description=strip(
                """
                The unique id of the upload to transfer."""
            )
        ),
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_BUNDLE_READ], allow_anonymous=False)),
    ],
):
    """
    Start a transfer of an upload to another NOMAD deployment.
    By default the transfer will target the central nomad deployment if no `target_deployment_url` is provided.
    `auth_token` is required to authenticate the transfer process in the target deploment (custom OASIS or central NOMAD).
    """
    upload = get_upload_with_read_access(
        upload_id=upload_id, user=user, include_others=True
    )
    if not upload.published:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='The upload should be published first.',
        )

    target_deployment_url = transfer_options.target_deployment_url

    upload_utils.validate_target_deployment_url(target_deployment_url)
    _check_upload_not_processing(upload)
    upload_utils.check_external_deployment_status(target_deployment_url)

    try:
        upload.publish_externally(
            target_deployment_url=target_deployment_url,
            auth_token=transfer_options.auth_token,
            embargo_length=transfer_options.embargo_length,
        )
        return UploadProcDataResponse(
            upload_id=upload_id, data=upload_to_pydantic(upload)
        )
    except ProcessFailure as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    '/{upload_id}/action/assign-doi',
    tags=[APITag.ACTION],
    summary='Assign a DOI to an upload',
    response_model=UploadProcDataResponse,
    responses=create_responses(
        _datacite_not_enabled,
        _upload_not_found,
        _bad_request,
        _not_authorized_to_upload,
        _existing_upload_with_findable_state,
        _upload_already_has_doi,
        _upload_is_empty,
        _upload_is_unpublished,
        _datacite_draft_failed,
        _db_set_upload_doi_failed,
        _datacite_publish_failed,
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def assign_doi(
    upload_id: Annotated[str, Path(description='The unique id of the upload.')],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_ASSIGN_DOI], allow_anonymous=False)),
    ],
):
    """
    Assign a DOI at DataCite to this upload.

    Conditions:

    - The DataCite service must be enabled on this deployment.
    - The upload must be published.
    - The upload must contain at least one entry.
    - The user must be the main author of the upload.
    """

    if not config.datacite.enabled:
        raise _create_exception(*_datacite_not_enabled)

    upload = _get_upload_with_write_access(
        upload_id,
        user,
        include_published=True,
        only_main_author=True,
        published_requires_admin=False,
    )

    if upload.doi is not None:
        raise _create_exception(*_upload_already_has_doi)

    if upload.total_entries_count == 0:
        raise _create_exception(*_upload_is_empty)

    if not upload.published:
        raise _create_exception(*_upload_is_unpublished)

    try:
        doi_id = create_doi_for_upload(upload)
    except DataCiteException:
        raise _create_exception(*_datacite_draft_failed)

    try:
        upload.doi = EmbeddedDOI(id=doi_id)
        upload.save()
    except MongoEngineException:
        raise _create_exception(*_db_set_upload_doi_failed)

    try:
        publish_doi(doi_id)
    except DataCiteException:
        raise _create_exception(*_datacite_publish_failed)

    return {'upload_id': upload.upload_id, 'data': upload}
