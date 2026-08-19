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

"""
User fixtures:
- user0: admin user
- user1: default user to use
- user2, user3, ...: additional users for access or interaction tests
"""

import pytest

from nomad.auth import keycloak, user_management
from nomad.auth.keycloak import KeycloakError, OIDCToken
from nomad.config import config
from nomad.datamodel import User
from tests.utils import fake_user_uuid, generate_convert_label

admin_user_id = fake_user_uuid(0)


def fake_user(num, first_name, last_name, *, email=None, **kwargs):
    """Return a dict with test user data based on the number and name."""
    if email is None:
        email = f'{first_name}.{last_name}@nomad-fairdi.tests.de'.lower()

    username = f'{first_name[0]}{last_name}'.lower()

    return dict(
        user_id=fake_user_uuid(num),
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        **kwargs,
    )


users = {
    fake_user_uuid(0): dict(username='admin', email='admin', user_id=fake_user_uuid(0)),
    fake_user_uuid(1): fake_user(
        1,
        'Sheldon',
        'Cooper',
        email='sheldon.cooper@nomad-coe.eu',  # domain differs from default
        is_oasis_admin=True,
    ),
    fake_user_uuid(2): fake_user(2, 'Leonard', 'Hofstadter'),
    fake_user_uuid(3): fake_user(3, 'Howard', 'Wolowitz'),
    fake_user_uuid(4): fake_user(4, 'Rajesh', 'Koothrappali'),
    fake_user_uuid(5): fake_user(5, 'Penny', 'Hofstadter'),
    fake_user_uuid(6): fake_user(6, 'Bernadette', 'Rostenkowski-Wolowitz'),
    fake_user_uuid(7): fake_user(7, 'Amy', 'Fowler'),
    fake_user_uuid(8): fake_user(8, 'Stuart', 'Bloom'),
    fake_user_uuid(9): fake_user(9, 'Emily', 'Sweeney'),
}


@pytest.fixture(scope='session')
def user_molds():
    """Return a dict: user labels -> user data (dict)."""
    return {f'user{i}': user for i, user in enumerate(users.values())}


@pytest.fixture(scope='session')
def user0():
    """Return the admin user object."""
    return User(**users[fake_user_uuid(0)])


@pytest.fixture(scope='session')
def user1():
    """Return the default user object."""
    return User(**users[fake_user_uuid(1)])


@pytest.fixture(scope='session')
def user2():
    """Return an alternative user object."""
    return User(**users[fake_user_uuid(2)])


@pytest.fixture(scope='session')
def user3():
    """Return an another user object."""
    return User(**users[fake_user_uuid(3)])


@pytest.fixture(scope='session')
def users_dict(user_molds):
    """Return a dict: user labels -> user objects."""
    return {k: User(**v) for k, v in user_molds.items()}


@pytest.fixture(scope='session')
def user_label_id_mapping(user_molds):
    """Return a dict: user labels -> user ids."""
    return {label: value.get('user_id') for label, value in user_molds.items()}


@pytest.fixture(scope='session')
def convert_user_labels_to_ids(user_label_id_mapping):
    """Returned function converts user labels to ids, also in lists and dicts."""
    return generate_convert_label(user_label_id_mapping)


@pytest.fixture(scope='session', autouse=True)
def configure_admin_user_id(monkeysession):
    monkeysession.setattr('nomad.config.services.admin_user_id', admin_user_id)


class KeycloakMock:
    def __init__(self):
        self.id_counter = len(users)
        self.users = dict(**users)

    def tokenauth(self, access_token: str):
        if access_token in self.users:
            return User(**self.users[access_token])

        raise KeycloakError('user does not exist')

    def add_user(self, user, *args, **kwargs):
        self.id_counter += 1
        user.user_id = fake_user_uuid(self.id_counter)
        user.username = (user.first_name[0] + user.last_name).lower()
        self.users[user.user_id] = dict(
            email=user.email,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            user_id=user.user_id,
        )

    def get_user(self, *, user_id=None, username=None, email=None):
        if user_id is not None:
            return User(**self.users[user_id])

        if username is not None:
            for _, user_values in self.users.items():
                if user_values['username'] == username:
                    return User(**user_values)
            raise KeyError('Only test user usernames are recognized')

        elif email is not None:
            for _, user_values in self.users.items():
                if user_values['email'] == email:
                    return User(**user_values)
            raise KeyError('Only test user emails are recognized')

        else:
            assert False, 'no token based get_user during tests'

    async def a_get_user(self, *, user_id=None, username=None, email=None):
        return self.get_user(user_id=user_id, username=username, email=email)

    def search_user(self, query):
        return [
            User(**user)
            for user in self.users.values()
            if query in ' '.join([str(value) for value in user.values()])
        ]

    def basicauth(self, username: str, password: str) -> OIDCToken:
        for user in self.users.values():
            if user['username'] == username or user['email'] == username:
                return OIDCToken(
                    access_token=f'fake-access-{user["user_id"]}',
                    token_type='Bearer',
                    expires_in=3600,
                    refresh_expires_in=7200,
                    refresh_token=f'fake-refresh-{user["user_id"]}',
                    id_token=f'fake-id-{user["user_id"]}',
                    scope='openid profile email',
                )

        raise KeycloakError()


config.keycloak.realm_name = 'fairdi_nomad_test'
config.keycloak.password = 'password'

_keycloak = keycloak.keycloak
_user_management = user_management.user_management


@pytest.fixture(scope='session', autouse=True)
def mocked_keycloak_session(monkeysession):
    mock = KeycloakMock()
    monkeysession.setattr('nomad.auth.keycloak.keycloak', mock)
    monkeysession.setattr('nomad.auth.user_management.user_management', mock)
    monkeysession.setattr('nomad.app.v1.routers.auth.keycloak', mock)


@pytest.fixture(scope='function')  # type: ignore[no-redef]
def keycloak(monkeypatch):
    monkeypatch.setattr('nomad.auth.keycloak.keycloak', _keycloak)
    monkeypatch.setattr('nomad.auth.user_management.user_management', _user_management)


@pytest.fixture(scope='function')
def with_central_user_management(monkeypatch):
    from nomad.auth.user_management import CentralUserManagement

    monkeypatch.setattr(
        'nomad.auth.user_management.user_management', CentralUserManagement()
    )
    yield
    monkeypatch.setattr('nomad.auth.user_management.user_management', _user_management)
