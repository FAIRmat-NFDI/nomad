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


import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any

import unidecode
from httpx2 import AsyncClient
from keycloak import KeycloakAdmin
from keycloak.exceptions import KeycloakGetError

from nomad import utils
from nomad.auth.keycloak import KeycloakError
from nomad.config import config
from nomad.utils.structlogging import get_logger

if TYPE_CHECKING:
    from nomad.datamodel import User

logger = get_logger(__name__)


class UserManagement(ABC):
    @abstractmethod
    def add_user(
        self, user, bcrypt_password: str | None = None, invite: bool = False
    ) -> str | None:
        """
        Adds the given :class:`nomad.datamodel.User` instance to the configured keycloak
        realm using the keycloak admin API.
        """
        ...

    @abstractmethod
    def search_user(self, query: str): ...

    @abstractmethod
    def get_user(
        self,
        *,
        user_id: str | None = None,
        username: str | None = None,
        email: str | None = None,
    ):
        """
        Retrives all available information about a user from the local keycloak admin
        interface or the central NOMAD installation. This can be used to retrieve
        complete user information, because the info solely gathered from tokens is generally
        incomplete.
        """
        ...

    @abstractmethod
    async def a_get_user(
        self,
        *,
        user_id: str | None = None,
        username: str | None = None,
        email: str | None = None,
    ): ...


class CentralUserManagement(UserManagement):
    """
    Resolve users via the central NOMAD `/v1/users` API.

    This backend is selected when
    `config.oasis.uses_central_user_management == True`.
    In that mode, user metadata (username/email/attributes) is sourced from the
    central NOMAD deployment configured by
    `config.oasis.central_nomad_deployment_url`, not from local Keycloak Admin
    API queries.

    Note:
        It is a read-oriented adapter to the central user registry,
        and cannot create users.
    """

    def __init__(self, users_api_url: str | None = None):
        if users_api_url:
            self._users_api_url = users_api_url
        else:
            self._users_api_url = (
                f'{config.oasis.central_nomad_deployment_url}/v1/users'
            )

    def add_user(self, user, bcrypt_password=None, invite=False) -> str:
        return 'Adding a user is not possible for an Oasis using the central user management.'

    def __user_from_api_user(self, api_user) -> 'User':
        from nomad import datamodel

        del api_user['is_admin']
        del api_user['is_oasis_admin']
        return datamodel.User.m_from_dict(api_user)

    def search_user(self, query: str) -> list['User']:
        import requests

        response = requests.get(self._users_api_url, params=dict(prefix=query))
        if response.status_code != 200:
            raise KeycloakError("Could not request central nomad's user management.")

        return list(self.__user_from_api_user(user) for user in response.json()['data'])

    def get_user(
        self,
        *,
        user_id: str | None = None,
        username: str | None = None,
        email: str | None = None,
    ) -> 'User | None':
        import requests

        kwargs = {}
        if user_id:
            kwargs['user_id'] = user_id
        elif username:
            kwargs['username'] = username
        elif email:
            kwargs['email'] = email
        else:
            return None

        response = requests.get(self._users_api_url, params=kwargs)
        if response.status_code != 200:
            raise KeycloakError("Could not request central nomad's user management.")

        data = response.json()
        if len(data['data']) == 0:
            return None

        return self.__user_from_api_user(data['data'][0])

    async def a_get_user(
        self,
        *,
        user_id: str | None = None,
        username: str | None = None,
        email: str | None = None,
    ) -> 'User | None':
        kwargs = {}
        if user_id:
            kwargs['user_id'] = user_id
        elif username:
            kwargs['username'] = username
        elif email:
            kwargs['email'] = email
        else:
            return None

        async with AsyncClient() as client:
            response = await client.get(self._users_api_url, params=kwargs)

        if response.status_code != 200:
            raise KeycloakError("Could not request central nomad's user management.")

        data = response.json()
        if len(data['data']) == 0:
            return None

        return self.__user_from_api_user(data['data'][0])


class KeycloakUserManagement(UserManagement):
    """
    Resolve and manage users through the configured Keycloak Admin API.

    This backend is selected when
    `config.oasis.uses_central_user_management == False`.
    It uses `config.keycloak.*` (server URL, realm, admin credentials) to
    perform user lookup/search/create operations directly against Keycloak.
    """

    def __init__(self) -> None:
        self.__admin_client: KeycloakAdmin | None = None

    def __create_username(self, user: 'User') -> None:
        if user.first_name is not None and user.last_name is not None:
            user.username = f'{user.first_name[:1]}{user.last_name}'
        elif user.last_name is not None:
            user.username = user.last_name
        elif '@' in user.username:
            user.username = user.username.split('@')[0]

        user.username = unidecode.unidecode(user.username.lower())
        user.username = re.sub(r'[^0-9a-zA-Z_\-\.]+', '', user.username)

        index = 1
        try:
            while self.get_user(username=user.username):
                user.username += f'{index}'
                index += 1
        except KeyError:
            pass

    def add_user(
        self,
        user: 'User | dict[str, Any]',
        bcrypt_password: str | None = None,
        invite: bool = False,
    ) -> str | None:
        """
        Add a user to Keycloak and NOMAD's internal database.

        Returns:
            str | None:
                - A string with an error message if user creation fails
                - None if the user was created successfully.
        """
        from nomad import datamodel

        if not isinstance(user, datamodel.User):
            if 'user_id' not in user:
                user['user_id'] = 'not set'

            if 'password' in user:
                bcrypt_password = user.pop('password')

            created = user.get('created', None)
            if created is not None and not isinstance(created, datetime):
                user['created'] = datetime.fromtimestamp(created / 1000)

            user = datamodel.User(**user)

        if user.username is None or not re.match(r'^[a-zA-Z0-9_\-\.]+$', user.username):
            self.__create_username(user)

        keycloak_user = dict(
            id=user.user_id if user.user_id != 'not set' else None,
            email=user.email,
            username=user.username,
            firstName=user.first_name,
            lastName=user.last_name,
            attributes=dict(
                repo_user_id=user.repo_user_id,
                affiliation=user.affiliation if user.affiliation is not None else '',
                affiliation_address=user.affiliation_address
                if user.affiliation_address is not None
                else '',
            ),
            createdTimestamp=user.created.timestamp() * 1000
            if user.created is not None
            else None,
            enabled=True,
            emailVerified=True,
        )

        if invite:
            keycloak_user['requiredActions'] = [
                'UPDATE_PASSWORD',
                'UPDATE_PROFILE',
                'VERIFY_EMAIL',
            ]

        if bcrypt_password is not None:
            keycloak_user['credentials'] = [
                dict(
                    type='password',
                    hashedSaltedValue=bcrypt_password,
                    algorithm='bcrypt',
                )
            ]

        keycloak_user = {
            key: value for key, value in keycloak_user.items() if value is not None
        }

        if user.user_id != 'not_set':
            try:
                self._admin_client.get_user(user.user_id)
                return f'User {user.email} with given id already exists'
            except KeycloakGetError:
                pass

        if self._admin_client.get_user_id(user.email) is not None:
            return f'User with email {user.email} already exists'

        try:
            self._admin_client.create_user(keycloak_user)
        except KeycloakGetError as e:
            try:
                return json.loads(e.response_body)['errorMessage']
            except Exception:
                return str(e)
        except Exception as e:
            return str(e)

        if invite:
            try:
                user = self.get_user(username=user.username)
                self._admin_client.send_verify_email(user_id=user.user_id)
            except Exception as e:
                logger.error('could not send verify email', exc_info=e)

        return None

    def __user_from_keycloak_user(self, keycloak_user: dict[str, Any]) -> 'User':
        from nomad import datamodel

        kwargs = {
            key: None if len(value) == 0 else value[0]
            for key, value in keycloak_user.get('attributes', {}).items()
        }
        oasis_admin = kwargs.pop('is_oasis_admin', None) is not None
        return datamodel.User(
            user_id=keycloak_user['id'],
            email=keycloak_user.get('email'),
            username=keycloak_user.get('username'),
            first_name=keycloak_user.get('firstName'),
            last_name=keycloak_user.get('lastName'),
            is_oasis_admin=oasis_admin,
            created=datetime.fromtimestamp(keycloak_user['createdTimestamp'] / 1000),
            **kwargs,
        )

    def search_user(self, query: str) -> list['User']:
        kwargs: dict[str, Any] = {}
        if query is not None:
            kwargs['query'] = dict(search=query, max=1000)
        else:
            kwargs['query'] = dict(max=1000)

        try:
            keycloak_results = self._admin_client.get_users(**kwargs)
        except Exception as e:
            logger.error('Could not retrieve users from keycloak', exc_info=e)
            raise e

        return [
            self.__user_from_keycloak_user(keycloak_user)
            for keycloak_user in keycloak_results
        ]

    def get_user(
        self,
        *,
        user_id: str | None = None,
        username: str | None = None,
        email: str | None = None,
    ) -> 'User':
        if username is not None and user_id is None:
            with utils.lnr(logger, 'Could not use keycloak admin client'):
                user_id = self._admin_client.get_user_id(username)

            if user_id is None:
                raise KeyError(f'User with username {username} does not exist')

        if email is not None and user_id is None:
            with utils.lnr(logger, 'Could not use keycloak admin client'):
                users = self._admin_client.get_users(query=dict(email=email))

            if len(users) > 0:
                user_id = users[0]['id']

            if user_id is None:
                raise KeyError(f'User with email {email} does not exist')

        if user_id is None:
            raise KeycloakError('Could not determine user from given kwargs')

        try:
            keycloak_user = self._admin_client.get_user(user_id)

        except Exception as e:
            if str(getattr(e, 'response_code', 404)) == '404':
                raise KeyError('User does not exist')

            # logger.error('Could not retrieve user from keycloak', exc_info=e)
            raise e

        return self.__user_from_keycloak_user(keycloak_user)

    async def a_get_user(
        self,
        *,
        user_id: str | None = None,
        username: str | None = None,
        email: str | None = None,
    ) -> 'User':
        """Async version of `get_user`."""
        if username is not None and user_id is None:
            with utils.lnr(logger, 'Could not use keycloak admin client'):
                user_id = await self._admin_client.a_get_user_id(username)

            if user_id is None:
                raise KeyError(f'User with username {username} does not exist')

        if email is not None and user_id is None:
            with utils.lnr(logger, 'Could not use keycloak admin client'):
                users = await self._admin_client.a_get_users(query=dict(email=email))

            if len(users) > 0:
                user_id = users[0]['id']

            if user_id is None:
                raise KeyError(f'User with email {email} does not exist')

        if user_id is None:
            raise KeycloakError('Could not determine user from given kwargs')

        try:
            keycloak_user = await self._admin_client.a_get_user(user_id)

        except Exception as e:
            if str(getattr(e, 'response_code', 404)) == '404':
                raise KeyError('User does not exist')

            # logger.error('Could not retrieve user from keycloak', exc_info=e)
            raise e

        return self.__user_from_keycloak_user(keycloak_user)

    @property
    def _admin_client(self) -> KeycloakAdmin:
        if (
            True
        ):  # TODO (self.__admin_client is None:), client becomes unusable after 60s
            self.__admin_client = KeycloakAdmin(
                server_url=config.keycloak.server_url + '/',
                username=config.keycloak.username,
                password=config.keycloak.password.get_secret_value(),
                realm_name=config.keycloak.realm_name,
                verify=True,
            )
            self.__admin_client.realm_name = config.keycloak.realm_name  # type: ignore[attr-defined]

        return self.__admin_client


if config.oasis.uses_central_user_management:
    user_management: UserManagement = CentralUserManagement()
else:
    user_management = KeycloakUserManagement()
