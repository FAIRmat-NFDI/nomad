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

from collections.abc import Callable
from datetime import timedelta
from enum import Enum
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi import Query as FastApiQuery
from mongoengine.queryset.visitor import Q
from pydantic import BaseModel, Field

from nomad import utils
from nomad.app.v1.models.groups import (
    UserGroup,
    UserGroupEdit,
    UserGroupMember,
    UserGroupMemberRole,
)
from nomad.app.v1.routers.auth import get_current_user
from nomad.app.v1.routers.groups_utils import get_user_role
from nomad.auth.scopes import Scope
from nomad.common import now
from nomad.config import config
from nomad.datamodel import User as DatamodelUser
from nomad.mongo.groups import get_mongo_user_group
from nomad.mongo.users import OwnershipTransferRecord
from nomad.notifications import notification_service
from nomad.processing import Upload
from nomad.uploads import add_upload_reviewers, remove_upload_reviewers

from ..models import User
from .uploads import (
    _check_upload_not_processing,
    _get_upload_with_write_access,
    upload_to_pydantic,
)

router = APIRouter()
logger = utils.get_logger(__name__)


class APITag(str, Enum):
    DEFAULT = 'ownership-transfers'


class OwnershipTransferCreateRequest(BaseModel):
    resource_type: str = Field(description='Transfer resource type, e.g. upload.')
    resource_id: str = Field(description='Transfer resource id.')
    target_user: str = Field(
        description='The target user identifier (user_id, username, or email).'
    )
    target_user_type: Literal['user_id', 'username', 'email'] = Field(
        description='Type of identifier provided in target_user.'
    )


class OwnershipTransferRespondRequest(BaseModel):
    action: Literal['accept', 'refuse'] = Field(
        description='Response action by target user: accept or refuse.'
    )


class OwnershipTransferResource(BaseModel):
    transfer_id: str
    resource_type: str
    resource_id: str
    resource_name: str | None = None
    source_user_id: str
    target_user_id: str
    requested_at: str
    updated_at: str
    actor_user_id: str | None = None


class OwnershipTransferResponse(BaseModel):
    transfers: list[OwnershipTransferResource]


class OwnershipTransferActionResponse(BaseModel):
    transfer_id: str
    resource_type: str
    resource_id: str | None = None
    result: dict[str, Any] | None = None


def _raise_transfer_not_found() -> None:
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail='Transfer does not exist.')


def _raise_transfer_unauthorized(
    action: Literal['access', 'respond', 'cancel'],
) -> None:
    detail = {
        'access': 'access this transfer',
        'respond': 'respond to this transfer request',
        'cancel': 'cancel this transfer request',
    }
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail=f'You are not authorized to {detail.get(action, "perform this action")}.',
    )


def _raise_transfer_pending_request_missing() -> None:
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        detail=(
            'No pending transfer request for this transfer id. '
            'The request may have expired or already been handled.'
        ),
    )


def _raise_resource_not_found(resource_name: str) -> None:
    raise HTTPException(
        status.HTTP_404_NOT_FOUND, detail=f'{resource_name.title()} does not exist.'
    )


def _get_target_user(
    request: OwnershipTransferCreateRequest,
    resource_name: str,
    current_owner_user_id: str | None,
) -> DatamodelUser:
    try:
        target_user = DatamodelUser.get(
            **{str(request.target_user_type): request.target_user}
        )
    except KeyError:
        target_user = None

    if target_user is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f'Could not resolve target user by {request.target_user_type}. '
                f'Provide a valid {request.target_user_type}.'
            ),
        )

    if target_user.user_id == current_owner_user_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f'The specified user is already the owner of this {resource_name}.',
        )

    return target_user


def _ensure_supported_resource_type(resource_type: str) -> None:
    supported_types = [
        OwnershipTransferRecord.RESOURCE_TYPE_UPLOAD,
        OwnershipTransferRecord.RESOURCE_TYPE_GROUP,
    ]
    if resource_type not in supported_types:
        supported_types_text = ', '.join(f'"{t}"' for t in supported_types)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f'Unsupported resource_type={resource_type!r}. '
                f'Currently only {supported_types_text} are supported.'
            ),
        )


def _map_ownership_transfer_resource(
    record: OwnershipTransferRecord,
    resource_name: str | None,
) -> OwnershipTransferResource:
    _ensure_supported_resource_type(record.resource_type)
    return OwnershipTransferResource(
        transfer_id=str(record.id),
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        resource_name=resource_name,
        source_user_id=record.source_user_id,
        target_user_id=record.target_user_id,
        requested_at=record.requested_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
        actor_user_id=record.actor_user_id,
    )


def _ownership_notification_dedup_key(transfer_id: str, event: str) -> str:
    return f'ownership-transfer-{event}-{transfer_id}'


def _emit_ownership_transfer_notification(
    record: OwnershipTransferRecord,
    event: Literal['request', 'refused'],
    resource_name: str | None,
) -> None:
    transfer_id = str(record.id)
    is_request = event == 'request'
    created_at = record.requested_at if is_request else record.updated_at
    notification_service.emit(
        user_id=record.target_user_id if is_request else record.source_user_id,
        source='user',
        notification_type='ownership_transfer',
        actor_user_id=(
            record.source_user_id
            if is_request
            else record.actor_user_id or record.target_user_id
        ),
        data={
            'resource_type': record.resource_type,
            'event': event,
            'resource_id': record.resource_id,
            'resource_name': resource_name,
        },
        dedup_key=_ownership_notification_dedup_key(transfer_id, event),
        created_at=created_at,
        expires_at=created_at
        + timedelta(seconds=config.mongo.ownership_transfer_record_ttl),
    )


def _retract_ownership_transfer_notification(
    record: OwnershipTransferRecord, event: Literal['request', 'refused'] = 'request'
) -> None:
    notification_service.retract(
        _ownership_notification_dedup_key(str(record.id), event)
    )


def _get_ownership_transfer_expiry_cutoff():
    return now() - timedelta(seconds=config.mongo.ownership_transfer_record_ttl)


def _is_stale_ownership_transfer_record(
    record: OwnershipTransferRecord,
    current_owner_user_id: str | None,
    expiry_cutoff=None,
) -> bool:
    if expiry_cutoff is None:
        expiry_cutoff = _get_ownership_transfer_expiry_cutoff()
    return current_owner_user_id != record.source_user_id or record.is_expired(
        expiry_cutoff
    )


def _delete_stale_pending_ownership_transfer_records(
    resource_type: str,
    resource_id: str,
    current_owner_user_id: str | None,
) -> list[OwnershipTransferRecord]:
    expiry_cutoff = _get_ownership_transfer_expiry_cutoff()
    stale_records: list[OwnershipTransferRecord] = []

    pending_records = OwnershipTransferRecord.objects(
        resource_type=resource_type,
        resource_id=resource_id,
        state=OwnershipTransferRecord.STATE_PENDING,
    )
    for record in pending_records:
        if not _is_stale_ownership_transfer_record(
            record, current_owner_user_id, expiry_cutoff
        ):
            continue

        stale_records.append(record)
        record.delete()
        _retract_ownership_transfer_notification(record)

    return stale_records


def _cleanup_stale_upload_ownership_transfer_records(upload: Upload) -> None:
    """Delete stale pending transfer records and revoke stale reviewer access."""
    stale_reviewer_ids = [
        record.target_user_id
        for record in _delete_stale_pending_ownership_transfer_records(
            OwnershipTransferRecord.RESOURCE_TYPE_UPLOAD,
            upload.upload_id,
            upload.main_author,
        )
    ]

    removed_count = remove_upload_reviewers(stale_reviewer_ids, upload=upload)
    if removed_count > 0:
        upload.reload()


def _cleanup_stale_group_ownership_transfer_records(group: UserGroup) -> None:
    _delete_stale_pending_ownership_transfer_records(
        OwnershipTransferRecord.RESOURCE_TYPE_GROUP,
        group.group_id,
        group.owner,
    )


def _create_upload_ownership_transfer(
    request: OwnershipTransferCreateRequest,
    user: User,
) -> OwnershipTransferResource:
    upload = _get_upload_with_write_access(
        request.resource_id,
        user,
        include_published=True,
        published_requires_admin=False,
        only_main_author=True,
    )
    _check_upload_not_processing(upload)
    _cleanup_stale_upload_ownership_transfer_records(upload)

    new_owner = _get_target_user(request, 'upload', upload.main_author)

    record = OwnershipTransferRecord.create_or_replace(
        resource_type=OwnershipTransferRecord.RESOURCE_TYPE_UPLOAD,
        resource_id=upload.upload_id,
        source_user_id=upload.main_author,
        target_user_id=new_owner.user_id,
    )

    add_upload_reviewers(new_owner.user_id, upload=upload)

    logger.info(
        'upload ownership transfer requested',
        upload_id=upload.upload_id,
        transfer_id=str(record.id),
        actor_user_id=user.user_id,
        actor_is_admin=user.is_admin,
        current_owner_user_id=upload.main_author,
        new_owner_user_id=new_owner.user_id,
    )

    upload.reload()
    result = _map_ownership_transfer_resource(record, upload.upload_name)
    _emit_ownership_transfer_notification(record, 'request', upload.upload_name)
    return result


def _list_ownership_transfers(
    resource_type: str,
    direction: Literal['incoming', 'outgoing', 'all'] | None,
    resource_id: str | None,
    state: str | None,
    user: User,
    get_resource: Callable[[str], Any | None],
    get_owner_user_id: Callable[[Any], str | None],
    get_resource_name: Callable[[Any], str | None],
) -> OwnershipTransferResponse:
    query = OwnershipTransferRecord.objects

    if direction == 'incoming':
        query = query(target_user_id=user.user_id)
    elif direction == 'outgoing':
        query = query(source_user_id=user.user_id)
    else:
        query = query(Q(target_user_id=user.user_id) | Q(source_user_id=user.user_id))

    if resource_id is not None:
        query = query(resource_type=resource_type, resource_id=resource_id)
    else:
        query = query(resource_type=resource_type)

    if state is not None:
        query = query(state=state)

    expiry_cutoff = _get_ownership_transfer_expiry_cutoff()

    transfers: list[OwnershipTransferResource] = []
    for record in query.order_by('-updated_at'):
        try:
            resource = get_resource(record.resource_id)
        except Exception:
            continue

        if resource is None:
            continue

        if (
            record.state == OwnershipTransferRecord.STATE_PENDING
            and _is_stale_ownership_transfer_record(
                record, get_owner_user_id(resource), expiry_cutoff
            )
        ):
            continue

        transfers.append(
            _map_ownership_transfer_resource(record, get_resource_name(resource))
        )

    return OwnershipTransferResponse(transfers=transfers)


def _get_upload_ownership_transfer(
    transfer_id: str, user: User
) -> OwnershipTransferResource:
    record = OwnershipTransferRecord.get_by_transfer_id(transfer_id)
    if record is None:
        _raise_transfer_not_found()

    if (
        record.target_user_id != user.user_id
        and record.source_user_id != user.user_id
        and not user.is_admin
    ):
        _raise_transfer_unauthorized('access')

    upload = Upload.get(record.resource_id)
    if upload is None:
        _raise_resource_not_found('Upload')

    if (
        record.state == OwnershipTransferRecord.STATE_PENDING
        and _is_stale_ownership_transfer_record(record, upload.main_author)
    ):
        _raise_transfer_not_found()

    return _map_ownership_transfer_resource(record, upload.upload_name)


def _respond_to_upload_ownership_transfer(
    transfer_id: str,
    request: OwnershipTransferRespondRequest,
    user: User,
) -> dict[str, Any]:
    record = OwnershipTransferRecord.claim_pending(
        transfer_id,
        OwnershipTransferRecord.STATE_RESPONDING,
        target_user_id=user.user_id,
    )
    if record is None:
        existing_record = OwnershipTransferRecord.get_by_transfer_id(transfer_id)
        if existing_record is None:
            _raise_transfer_not_found()
        if existing_record.target_user_id != user.user_id:
            _raise_transfer_unauthorized('respond')
        _raise_transfer_pending_request_missing()

    upload = Upload.get(record.resource_id)
    if upload is None:
        record.delete()
        _retract_ownership_transfer_notification(record)
        _raise_resource_not_found('Upload')

    try:
        _check_upload_not_processing(upload)
        _cleanup_stale_upload_ownership_transfer_records(upload)
    except Exception:
        OwnershipTransferRecord.release_claim(
            transfer_id,
            OwnershipTransferRecord.STATE_RESPONDING,
        )
        raise

    if _is_stale_ownership_transfer_record(record, upload.main_author):
        record.delete()
        _retract_ownership_transfer_notification(record)
        _raise_transfer_pending_request_missing()

    if request.action == 'accept':
        if not config.services.admin_user_id:
            OwnershipTransferRecord.release_claim(
                transfer_id,
                OwnershipTransferRecord.STATE_RESPONDING,
            )
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail='No admin user configured for transfer.',
            )

        previous_owner_user_id = upload.main_author
        try:
            upload.transfer_ownership(
                new_owner_user_id=user.user_id,
                previous_owner_user_id=previous_owner_user_id,
            )
        except Exception as e:
            OwnershipTransferRecord.release_claim(
                transfer_id,
                OwnershipTransferRecord.STATE_RESPONDING,
            )
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f'Failed to execute transfer workflow: {e}',
            )

        extra_log = dict(
            previous_owner_user_id=previous_owner_user_id,
            new_owner_user_id=user.user_id,
        )
    else:
        remove_upload_reviewers(user.user_id, upload=upload)
        record.refuse(actor_user_id=user.user_id)
        _retract_ownership_transfer_notification(record)
        _emit_ownership_transfer_notification(record, 'refused', upload.upload_name)
        extra_log = dict(owner_user_id=upload.main_author)

    logger.info(
        f'upload transfer {request.action}ed',
        upload_id=upload.upload_id,
        transfer_id=transfer_id,
        actor_user_id=user.user_id,
        actor_is_admin=user.is_admin,
        **extra_log,
    )

    upload.reload()
    return {
        'upload_id': upload.upload_id,
        'data': upload_to_pydantic(upload),
    }


def _cancel_upload_ownership_transfer(
    transfer_id: str,
    user: User,
) -> dict[str, Any]:
    record = OwnershipTransferRecord.claim_pending(
        transfer_id,
        OwnershipTransferRecord.STATE_CANCELING,
        source_user_id=user.user_id,
    )
    if record is None:
        existing_record = OwnershipTransferRecord.get_by_transfer_id(transfer_id)
        if existing_record is None:
            _raise_transfer_not_found()
        if existing_record.source_user_id != user.user_id:
            _raise_transfer_unauthorized('cancel')
        _raise_transfer_pending_request_missing()

    try:
        upload = _get_upload_with_write_access(
            record.resource_id,
            user,
            include_published=True,
            published_requires_admin=False,
            only_main_author=True,
        )
        _check_upload_not_processing(upload)
        _cleanup_stale_upload_ownership_transfer_records(upload)
    except Exception:
        OwnershipTransferRecord.release_claim(
            transfer_id,
            OwnershipTransferRecord.STATE_CANCELING,
        )
        raise

    if _is_stale_ownership_transfer_record(record, upload.main_author):
        record.delete()
        _retract_ownership_transfer_notification(record)
        _raise_transfer_pending_request_missing()

    duplicate_pending_records = list(
        OwnershipTransferRecord.objects(
            resource_type=OwnershipTransferRecord.RESOURCE_TYPE_UPLOAD,
            resource_id=upload.upload_id,
            state=OwnershipTransferRecord.STATE_PENDING,
        )
    )
    canceled_records = [record, *duplicate_pending_records]
    for canceled_record in canceled_records:
        canceled_record.delete()

    reviewer_access_removed = remove_upload_reviewers(
        [canceled_record.target_user_id for canceled_record in canceled_records],
        upload=upload,
    )
    if reviewer_access_removed > 0:
        upload.reload()
    for canceled_record in canceled_records:
        _retract_ownership_transfer_notification(canceled_record)

    logger.info(
        'upload transfer canceled',
        upload_id=upload.upload_id,
        transfer_id=transfer_id,
        actor_user_id=user.user_id,
        actor_is_admin=user.is_admin,
        owner_user_id=upload.main_author,
        reviewer_access_removed=reviewer_access_removed,
    )
    upload.reload()
    return {
        'upload_id': upload.upload_id,
        'data': upload_to_pydantic(upload),
    }


def _create_group_ownership_transfer(
    request: OwnershipTransferCreateRequest,
    user: User,
) -> OwnershipTransferResource:
    group = get_mongo_user_group(request.resource_id)
    if group is None:
        _raise_resource_not_found('User group')

    _cleanup_stale_group_ownership_transfer_records(group)

    group_member = get_user_role(group.members_info, user.user_id)

    unauthorized_error = HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail=(
            f"You are not authorized to transfer user group '{group.group_id}'."
            ' Only group owners and admins are allowed to transfer group ownership.'
        ),
    )

    if group_member is None:
        if group.owner != user.user_id and not user.is_admin:
            raise unauthorized_error
    else:
        if not user.is_admin and group_member.role != UserGroupMemberRole.OWNER:
            raise unauthorized_error

    new_owner = _get_target_user(request, 'group', group.owner)

    record = OwnershipTransferRecord.create_or_replace(
        resource_type=OwnershipTransferRecord.RESOURCE_TYPE_GROUP,
        resource_id=group.group_id,
        source_user_id=group.owner or group_member.user_id,
        target_user_id=new_owner.user_id,
    )

    logger.info(
        'group ownership transfer requested',
        group_id=group.group_id,
        transfer_id=str(record.id),
        actor_user_id=user.user_id,
        actor_is_admin=user.is_admin,
        current_owner_user_id=group.owner,
        new_owner_user_id=new_owner.user_id,
    )

    result = _map_ownership_transfer_resource(record, group.group_name)
    _emit_ownership_transfer_notification(record, 'request', group.group_name)
    return result


def _get_group_ownership_transfer(
    transfer_id: str, user: User
) -> OwnershipTransferResource:
    record = OwnershipTransferRecord.get_by_transfer_id(transfer_id)
    if record is None:
        _raise_transfer_not_found()

    if (
        record.target_user_id != user.user_id
        and record.source_user_id != user.user_id
        and not user.is_admin
    ):
        _raise_transfer_unauthorized('access')

    group = get_mongo_user_group(record.resource_id)
    if group is None:
        _raise_resource_not_found('User group')

    if (
        record.state == OwnershipTransferRecord.STATE_PENDING
        and _is_stale_ownership_transfer_record(record, group.owner)
    ):
        _raise_transfer_not_found()

    return _map_ownership_transfer_resource(record, group.group_name)


def _respond_to_group_ownership_transfer(
    transfer_id: str,
    request: OwnershipTransferRespondRequest,
    user: User,
) -> dict[str, Any]:
    record = OwnershipTransferRecord.claim_pending(
        transfer_id,
        OwnershipTransferRecord.STATE_RESPONDING,
        target_user_id=user.user_id,
    )
    if record is None:
        existing_record = OwnershipTransferRecord.get_by_transfer_id(transfer_id)
        if existing_record is None:
            _raise_transfer_not_found()
        if existing_record.target_user_id != user.user_id:
            _raise_transfer_unauthorized('respond')
        _raise_transfer_pending_request_missing()

    group = get_mongo_user_group(record.resource_id)
    if group is None:
        record.delete()
        _retract_ownership_transfer_notification(record)
        _raise_resource_not_found('User group')

    _cleanup_stale_group_ownership_transfer_records(group)
    if _is_stale_ownership_transfer_record(record, group.owner):
        record.delete()
        _retract_ownership_transfer_notification(record)
        _raise_transfer_pending_request_missing()

    if request.action == 'accept':
        previous_owner_user_id = group.owner
        updated_members_info = [
            UserGroupMember(user_id=member.user_id, role=member.role)
            for member in group.members_info
            if member.role != UserGroupMemberRole.OWNER
        ]
        found_target = False
        for member in updated_members_info:
            if member.user_id == user.user_id:
                member.role = UserGroupMemberRole.OWNER
                found_target = True

        if not found_target:
            updated_members_info.append(
                UserGroupMember(user_id=user.user_id, role=UserGroupMemberRole.OWNER)
            )

        group.clean_update_reload(UserGroupEdit(members_info=updated_members_info))
        record.delete()
        _retract_ownership_transfer_notification(record)
        extra_log = dict(
            previous_owner_user_id=previous_owner_user_id,
            new_owner_user_id=user.user_id,
        )
    else:
        record.refuse(actor_user_id=user.user_id)
        _retract_ownership_transfer_notification(record)
        _emit_ownership_transfer_notification(record, 'refused', group.group_name)
        extra_log = dict(owner_user_id=group.owner)

    logger.info(
        f'group transfer {request.action}ed',
        group_id=group.group_id,
        transfer_id=transfer_id,
        actor_user_id=user.user_id,
        actor_is_admin=user.is_admin,
        **extra_log,
    )

    group.reload()
    return {
        'group_id': group.group_id,
        'data': UserGroup.model_validate(group),
    }


def _cancel_group_ownership_transfer(
    transfer_id: str,
    user: User,
) -> dict[str, Any]:
    record = OwnershipTransferRecord.claim_pending(
        transfer_id,
        OwnershipTransferRecord.STATE_CANCELING,
        source_user_id=user.user_id,
    )
    if record is None:
        existing_record = OwnershipTransferRecord.get_by_transfer_id(transfer_id)
        if existing_record is None:
            _raise_transfer_not_found()
        if existing_record.source_user_id != user.user_id:
            _raise_transfer_unauthorized('cancel')
        _raise_transfer_pending_request_missing()

    group = get_mongo_user_group(record.resource_id)
    if group is None:
        record.delete()
        _retract_ownership_transfer_notification(record)
        _raise_resource_not_found('User group')

    _cleanup_stale_group_ownership_transfer_records(group)
    if _is_stale_ownership_transfer_record(record, group.owner):
        record.delete()
        _retract_ownership_transfer_notification(record)
        _raise_transfer_pending_request_missing()

    canceled_records = [record]
    record.delete()
    duplicate_pending_records = list(
        OwnershipTransferRecord.objects(
            resource_type=OwnershipTransferRecord.RESOURCE_TYPE_GROUP,
            resource_id=group.group_id,
            state=OwnershipTransferRecord.STATE_PENDING,
        )
    )
    for duplicate_record in duplicate_pending_records:
        duplicate_record.delete()
    canceled_records.extend(duplicate_pending_records)
    for canceled_record in canceled_records:
        _retract_ownership_transfer_notification(canceled_record)

    logger.info(
        'group transfer canceled',
        group_id=group.group_id,
        transfer_id=transfer_id,
        actor_user_id=user.user_id,
        actor_is_admin=user.is_admin,
        owner_user_id=group.owner,
    )
    group.reload()
    return {
        'group_id': group.group_id,
        'data': UserGroup.model_validate(group).model_dump(mode='json'),
    }


@router.get(
    '',
    tags=[APITag.DEFAULT],
    summary='List transfer resources for the current user.',
    response_model=OwnershipTransferResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def list_ownership_transfers(
    direction: Annotated[
        Literal['incoming', 'outgoing', 'all'] | None,
        FastApiQuery(description='Optional transfer direction filter.'),
    ] = 'all',
    resource_type: Annotated[
        str,
        FastApiQuery(description='Transfer resource type.'),
    ] = 'upload',
    resource_id: Annotated[
        str | None,
        FastApiQuery(description='Optional transfer resource id filter.'),
    ] = None,
    state: Annotated[
        str | None,
        FastApiQuery(description='Optional transfer state filter.'),
    ] = None,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_READ], allow_anonymous=False)),
    ] = None,
):
    _ensure_supported_resource_type(resource_type)
    resource_getter: Callable[[str], Any | None]
    if resource_type == 'upload':
        resource_type = OwnershipTransferRecord.RESOURCE_TYPE_UPLOAD
        resource_getter = Upload.get
        owner_getter = lambda upload: upload.main_author
        resource_name_getter = lambda upload: upload.upload_name
    elif resource_type == 'group':
        resource_type = OwnershipTransferRecord.RESOURCE_TYPE_GROUP
        resource_getter = get_mongo_user_group
        owner_getter = lambda group: group.owner
        resource_name_getter = lambda group: group.group_name
    return _list_ownership_transfers(
        resource_type,
        direction,
        resource_id,
        state,
        user,
        resource_getter,
        owner_getter,
        resource_name_getter,
    )


@router.post(
    '',
    tags=[APITag.DEFAULT],
    summary='Create a transfer resource.',
    response_model=OwnershipTransferResource,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def create_ownership_transfer(
    request: OwnershipTransferCreateRequest,
    user: Annotated[
        User,
        Depends(
            get_current_user(
                [Scope.UPLOADS_READ, Scope.UPLOADS_WRITE],
                allow_anonymous=False,
            )
        ),
    ],
):
    _ensure_supported_resource_type(request.resource_type)
    if request.resource_type == 'upload':
        return _create_upload_ownership_transfer(request=request, user=user)
    if request.resource_type == 'group':
        return _create_group_ownership_transfer(request=request, user=user)


@router.get(
    '/{transfer_id}',
    tags=[APITag.DEFAULT],
    summary='Get one transfer resource by id.',
    response_model=OwnershipTransferResource,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_ownership_transfer(
    transfer_id: Annotated[str, Path(description='Transfer id.')],
    resource_type: Annotated[
        str,
        FastApiQuery(description='Transfer resource type.'),
    ] = 'upload',
    user: Annotated[
        User,
        Depends(get_current_user([Scope.UPLOADS_READ], allow_anonymous=False)),
    ] = None,
):
    _ensure_supported_resource_type(resource_type)
    if resource_type == 'upload':
        return _get_upload_ownership_transfer(transfer_id=transfer_id, user=user)
    if resource_type == 'group':
        return _get_group_ownership_transfer(transfer_id=transfer_id, user=user)


@router.post(
    '/{transfer_id}/respond',
    tags=[APITag.DEFAULT],
    summary='Accept or refuse a pending transfer by transfer id.',
    response_model=OwnershipTransferActionResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def respond_to_ownership_transfer(
    transfer_id: Annotated[str, Path(description='Transfer id.')],
    request: OwnershipTransferRespondRequest,
    resource_type: Annotated[
        str,
        FastApiQuery(description='Transfer resource type.'),
    ] = 'upload',
    user: Annotated[
        User,
        Depends(
            get_current_user(
                [Scope.UPLOADS_READ, Scope.UPLOADS_WRITE],
                allow_anonymous=False,
            )
        ),
    ] = None,
):
    _ensure_supported_resource_type(resource_type)

    if resource_type == 'upload':
        result_payload = _respond_to_upload_ownership_transfer(
            transfer_id=transfer_id,
            request=OwnershipTransferRespondRequest(action=request.action),
            user=user,
        )
        resource_id = result_payload.get('upload_id')
    elif resource_type == 'group':
        result_payload = _respond_to_group_ownership_transfer(
            transfer_id=transfer_id,
            request=OwnershipTransferRespondRequest(action=request.action),
            user=user,
        )
        resource_id = result_payload.get('group_id')

    return OwnershipTransferActionResponse(
        transfer_id=transfer_id,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result_payload,
    )


@router.post(
    '/{transfer_id}/cancel',
    tags=[APITag.DEFAULT],
    summary='Cancel a pending transfer by transfer id.',
    response_model=OwnershipTransferActionResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def cancel_ownership_transfer(
    transfer_id: Annotated[str, Path(description='Transfer id.')],
    resource_type: Annotated[
        str,
        FastApiQuery(description='Transfer resource type.'),
    ] = 'upload',
    user: Annotated[
        User,
        Depends(
            get_current_user(
                [Scope.UPLOADS_READ, Scope.UPLOADS_WRITE],
                allow_anonymous=False,
            )
        ),
    ] = None,
):
    _ensure_supported_resource_type(resource_type)

    if resource_type == 'upload':
        result_payload = _cancel_upload_ownership_transfer(
            transfer_id=transfer_id, user=user
        )
        resource_id = result_payload.get('upload_id')
    elif resource_type == 'group':
        result_payload = _cancel_group_ownership_transfer(
            transfer_id=transfer_id, user=user
        )
        resource_id = result_payload.get('group_id')

    return OwnershipTransferActionResponse(
        transfer_id=transfer_id,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result_payload,
    )
