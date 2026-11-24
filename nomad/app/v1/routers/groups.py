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

from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from nomad.app.v1.models.groups import (
    UserGroup,
    UserGroupEdit,
    UserGroupMember,
    UserGroupMemberRole,
    UserGroupPagination,
    UserGroupQuery,
    UserGroupResponse,
)
from nomad.app.v1.models.pagination import PaginationResponse
from nomad.app.v1.routers.auth import get_current_user
from nomad.app.v1.routers.groups_utils import (
    convert_members_to_info,
    get_user_role,
    merge_info_with_members,
)
from nomad.app.v1.routers.groups_utils import (
    validate_members_info as validate_members_info_util,
)
from nomad.app.v1.utils import parameter_dependency_from_model
from nomad.auth.scopes import Scope
from nomad.datamodel import User as UserDataModel
from nomad.mongo.groups import (
    MongoUserGroup,
    create_mongo_user_group,
    get_mongo_user_group,
)

from ..models import User

router = APIRouter()


class APITag(StrEnum):
    DEFAULT = 'groups'


user_group_query_parameters = parameter_dependency_from_model(
    'user_group_query_parameters',
    UserGroupQuery,
)

user_group_pagination_parameters = parameter_dependency_from_model(
    'user_group_pagination_parameters',
    UserGroupPagination,
)


def get_user_group_or_404(group_id: str) -> MongoUserGroup:
    user_group = get_mongo_user_group(group_id)
    if user_group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User group '{group_id}' was not found.",
        )

    return user_group


def resolve_members_info(
    owner_id: str,
    members: Iterable[str] | None,
    old_members_info: Iterable[UserGroupMember] | None = None,
) -> list[UserGroupMember] | None:
    """Resolve members_info from members list. Returns None if no change."""
    if members is None:
        return None

    if old_members_info is not None:
        return merge_info_with_members(old_members_info, members, owner_id)

    return convert_members_to_info(members, owner_id)


def validate_members_info(
    members_info: list[UserGroupMember],
    owner_id: str,
):
    """Validate owner in members_info and check all user IDs exist."""
    try:
        validate_members_info_util(members_info, owner_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{'loc': ['body', 'members_info'], 'msg': str(e)}],
        ) from e

    for i, id in enumerate(m.user_id for m in members_info):
        try:
            UserDataModel.get(user_id=id)
        except KeyError as exc:
            loc = ['body', 'members_info', i, 'user_id']
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[{'loc': loc, 'msg': f"User '{id}' was not found."}],
            ) from exc


def check_user_may_edit_user_group(user: User, user_group: UserGroup):
    if user.is_admin:
        return

    member = get_user_role(user_group.members_info, user.user_id)
    if member and member.role in [
        UserGroupMemberRole.OWNER,
        UserGroupMemberRole.MAINTAINER,
    ]:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Not authorized to edit user group '{user_group.group_id}'."
        ' Only group owners, maintainers and admins are allowed to edit a group.',
    )


def check_user_may_delete_user_group(user: User, user_group: UserGroup):
    if user.is_admin:
        return

    member = get_user_role(user_group.members_info, user.user_id)
    if member and member.role == UserGroupMemberRole.OWNER:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Not authorized to delete user group '{user_group.group_id}'."
        ' Only group owners and admins are allowed to delete a group.',
    )


def check_mutually_exclusive_members_fields(user_group_edit: UserGroupEdit):
    if user_group_edit.members is not None and user_group_edit.members_info is not None:
        msg = (
            "Fields 'members' and 'members_info' are mutually exclusive."
            " Remove the deprecated 'members' field from your request."
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{'loc': ['body'], 'msg': (msg)}],
        )


@router.get(
    '',
    tags=[APITag.DEFAULT],
    summary='List user groups.',
    response_model=UserGroupResponse,
)
def get_user_groups(
    request: Request,
    query: Annotated[UserGroupQuery, Depends(user_group_query_parameters)],
    pagination: Annotated[
        UserGroupPagination, Depends(user_group_pagination_parameters)
    ],
    user: Annotated[User, Depends(get_current_user([Scope.GROUPS_READ]))],
):
    """Get data about user groups."""
    db_groups = MongoUserGroup.get_by_query(query)
    db_groups = pagination.order_result(db_groups)

    total = db_groups.count()
    pagination_response = PaginationResponse(total=total, **pagination.model_dump())
    pagination_response.populate_simple_index_and_urls(request)

    start = pagination.get_simple_index()
    end = start + pagination.page_size
    data = db_groups[start:end]
    return {'pagination': pagination_response, 'data': data}


@router.get(
    '/{group_id}',
    tags=[APITag.DEFAULT],
    summary='Get data about user group.',
    response_model=UserGroup,
)
def get_user_group(
    group_id: str,
    _user: Annotated[User, Depends(get_current_user([Scope.GROUPS_READ]))],
):
    """Get data about user group."""
    user_group = get_user_group_or_404(group_id)

    return user_group


@router.post(
    '',
    tags=[APITag.DEFAULT],
    status_code=status.HTTP_201_CREATED,
    summary='Create user group.',
    response_model=UserGroup,
)
def create_user_group(
    user_group_edit: UserGroupEdit,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.GROUPS_WRITE], allow_anonymous=False)),
    ],
):
    """Create user group."""
    check_mutually_exclusive_members_fields(user_group_edit)
    if user_group_edit.members_info is None:
        user_group_edit.members_info = resolve_members_info(
            user.user_id, user_group_edit.members or []
        )
        user_group_edit.members = None

    assert user_group_edit.members_info is not None
    validate_members_info(user_group_edit.members_info, user.user_id)

    user_group = create_mongo_user_group(user_group_edit)
    return user_group


@router.post(
    '/{group_id}/edit',
    tags=[APITag.DEFAULT],
    summary='Update user group.',
    response_model=UserGroup,
)
def update_user_group(
    group_id: str,
    user_group_edit: UserGroupEdit,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.GROUPS_WRITE], allow_anonymous=False)),
    ],
):
    """Update user group."""
    mongo_user_group = get_user_group_or_404(group_id)
    user_group = UserGroup.model_validate(mongo_user_group)
    check_user_may_edit_user_group(user, user_group)

    check_mutually_exclusive_members_fields(user_group_edit)
    if user_group_edit.members is not None:
        user_group_edit.members_info = resolve_members_info(
            user.user_id,
            user_group_edit.members,
            old_members_info=user_group.members_info,
        )
        user_group_edit.members = None

    if user_group_edit.members_info is not None:
        validate_members_info(user_group_edit.members_info, user_group.owner)

    mongo_user_group.clean_update_reload(user_group_edit)
    return mongo_user_group


@router.delete(
    '/{group_id}',
    tags=[APITag.DEFAULT],
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Delete user group.',
)
def delete_user_group(
    group_id: str,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.GROUPS_DELETE], allow_anonymous=False)),
    ],
):
    """Delete user group."""
    mongo_user_group = get_user_group_or_404(group_id)
    user_group = UserGroup.model_validate(mongo_user_group)
    check_user_may_delete_user_group(user, user_group)

    mongo_user_group.delete()
