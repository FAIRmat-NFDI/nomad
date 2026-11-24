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

from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic.main import BaseModel

from nomad import datamodel
from nomad.app.v1.routers.auth import get_current_user
from nomad.auth import user_management
from nomad.auth.scopes import Scope
from nomad.config import config
from nomad.utils import strip

from ..models import HTTPExceptionModel, User
from ..utils import create_responses

router = APIRouter()


class APITag(StrEnum):
    DEFAULT = 'users'


_authentication_required_response = (
    status.HTTP_401_UNAUTHORIZED,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Unauthorized. The operation requires authorization,
        but no or bad authentication credentials are given."""
        ),
    },
)

_bad_invite_response = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The invite is invalid."""
        ),
    },
)


class Users(BaseModel):
    data: list[User]


@router.get(
    '/me',
    tags=[APITag.DEFAULT],
    summary='Get your account data',
    description='Returns the account data of the authenticated user.',
    responses=create_responses(_authentication_required_response),
    response_model=User,
)
def read_users_me(
    current_user: Annotated[
        User,
        Depends(get_current_user([Scope.USERS_READ], allow_anonymous=False)),
    ],
):
    current_user_dict: dict = current_user.m_to_dict(
        with_out_meta=True, include_derived=True
    )
    additional_info: dict = datamodel.User.get(user_id=current_user.user_id).m_to_dict(
        with_out_meta=True, include_derived=True
    )
    current_user_dict.update(additional_info)
    return current_user_dict


@router.get(
    '',
    tags=[APITag.DEFAULT],
    summary='Get existing users',
    description='Get existing users for given criteria',
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    response_model=Users,
)
def get_users(
    _current_user: Annotated[
        User,
        Depends(get_current_user([Scope.USERS_READ])),
    ],
    prefix: Annotated[
        str | None,
        Query(
            description=strip(
                """
            Search the user with the given prefix.
        """
            )
        ),
    ] = None,
    user_id: Annotated[
        list[str] | None,
        Query(
            description=strip(
                """
            To get the user(s) by their user_id(s).
        """
            )
        ),
    ] = None,
    username: Annotated[
        list[str] | None,
        Query(
            description=strip(
                """
            To get the user(s) by their username(s).
        """
            )
        ),
    ] = None,
    email: Annotated[
        list[str] | None,
        Query(
            description=strip(
                """
            To get the user(s) by their email(s).
        """
            )
        ),
    ] = None,
):
    users: list[User] = []
    for key, values in dict(user_id=user_id, username=username, email=email).items():
        if not values:
            continue

        if isinstance(values, str):
            values = [values]

        for value in values:
            try:
                user = datamodel.User.get(**{key: str(value)}).m_copy()
                user.email = None
                users.append(user)
            except KeyError:
                pass

    if prefix:
        for user in user_management.user_management.search_user(prefix):
            user_dict = user.m_to_dict(include_derived=True)
            user_dict['email'] = None
            users.append(user_dict)

    return dict(data=users)


class PublicUserInfo(BaseModel):
    """User information that is publicly available."""

    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    affiliation: str | None = None
    affiliation_address: str | None = None
    user_id: str | None = None
    username: str | None = None


@router.get(
    '/{user_id}',
    tags=[APITag.DEFAULT],
    summary='Get existing users',
    description='Get the user using the given user_id',
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    response_model=PublicUserInfo,
)
def get_user(
    user_id: str,
    _current_user: Annotated[
        User,
        Depends(get_current_user([Scope.USERS_READ])),
    ],
):
    user = datamodel.User.get(user_id=str(user_id))
    if user is None:
        return None

    return user.m_to_dict(with_out_meta=True, include_derived=True)


@router.put(
    '/invite',
    tags=[APITag.DEFAULT],
    summary='Invite a new user',
    responses=create_responses(_authentication_required_response, _bad_invite_response),
    response_model=User,
)
def invite_user(
    user: User,
    _current_user: Annotated[
        User,
        Depends(get_current_user([Scope.USERS_INVITE], allow_anonymous=False)),
    ],
):
    if config.oasis.is_oasis:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='User invite does not work this NOMAD OASIS.',
        )

    json_data = user.dict()
    try:
        user = datamodel.User.m_from_dict(json_data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Invalid user data: {str(e)}',
        )

    if user.email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid user data: email is required',
        )

    try:
        error = user_management.user_management.add_user(user, invite=True)
    except KeyError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Invalid user data: {str(e)}',
        )

    if error is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Could not invite user: {str(error)}',
        )

    return datamodel.User.get(username=user.username)
