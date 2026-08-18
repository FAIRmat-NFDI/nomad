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

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from pydantic_core import PydanticCustomError

from .pagination import Direction, Pagination, PaginationResponse


class UserGroupMemberRole(Enum):
    MEMBER = 'member'
    MAINTAINER = 'maintainer'
    OWNER = 'owner'


class UserGroupMember(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str = Field(description='User id of the member.')
    role: UserGroupMemberRole = Field(
        default=UserGroupMemberRole.MEMBER,
        description=f'Role of the member in the group.',
    )


# API models


class UserGroupEdit(BaseModel):
    """
    Model for creating or editing user groups.
    To modify members, use the 'members_info' field containing a
    full list of all members with their user_id and role.

    For backward compatibility, the deprecated 'members' field can be used instead.
    """

    model_config = ConfigDict(from_attributes=True)

    group_name: (
        Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)] | None
    ) = Field(
        default=None,
        description='Displayed name of the group.',
    )
    description: str | None = Field(
        default=None,
        description='Description of the group.',
    )
    members_info: list[UserGroupMember] | None = Field(
        default=None,
        description='Group members with user_id and role.',
    )
    members: set[str] | None = Field(
        default=None,
        description='User ids of the group members (includes owner).'
        " Deprecated: Use 'members_info' instead.",
        deprecated=True,
    )


class UserGroup(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_id: str = Field(description='Unique id of the group.')
    group_name: str = Field(
        default='Default Group Name', description='Displayed name of the group.'
    )
    description: str | None = Field(
        default=None,
        description='Description of the group.',
    )
    owner: str = Field(
        description="User id of the group owner. Mirrored from 'members_info'."
    )
    members: list[str] = Field(
        default_factory=list,
        description='User ids of the group members (includes owner).'
        " Mirrored from 'members_info'.",
    )
    members_info: list[UserGroupMember] = Field(
        default_factory=list,
        description='Group members with user ids and roles.',
    )


class UserGroupResponse(BaseModel):
    pagination: PaginationResponse | None = Field(None)
    data: list[UserGroup]


class UserGroupQuery(BaseModel):
    group_id: str | list[str] | None = Field(
        None, description='Search groups by their full id (scalar or list).'
    )
    user_id: str | None = Field(
        None, description="Search groups by their members' ids."
    )
    search_terms: str | None = Field(
        None, description='Search groups by parts of their name.'
    )


class UserGroupPagination(Pagination):
    @field_validator('order_by')
    @classmethod
    def validate_order_by(cls, order_by):  # pylint: disable=no-self-argument
        valid_fields = (None, 'group_id', 'group_name', 'owner')
        if order_by not in valid_fields:
            raise PydanticCustomError(
                'invalid_order_by', f'order_by must be one of {valid_fields}'
            )
        return order_by

    def order_result(self, result):
        if self.order_by is None:
            return result

        prefix: str = '-' if self.order == Direction.desc else '+'
        order_list: list = [f'{prefix}{self.order_by}', 'group_id']

        return result.order_by(*order_list)
