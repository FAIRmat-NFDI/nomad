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

import time

import requests
from keycloak import KeycloakOpenID

from nomad.config import config


class APIError(Exception):
    pass


def _call_requests(method, path: str, ssl: bool = True, *args, **kwargs):
    url = f'{config.client.url}/v1/{path}'
    return getattr(requests, method)(url, *args, **kwargs)


def get(*args, **kwargs):
    return _call_requests('get', *args, **kwargs)


def post(*args, **kwargs):
    return _call_requests('post', *args, **kwargs)


def put(*args, **kwargs):
    return _call_requests('put', *args, **kwargs)


def delete(*args, **kwargs):
    return _call_requests('delete', *args, **kwargs)


def url(path):
    """Returns the full NOMAD API url for the given api path."""
    return f'{config.client.url}/v1/{path}'


class Auth(requests.auth.AuthBase):
    """
    A request Auth class that can be used to authenticate in request callcs like this:

    .. code::

        requests.get('https://nomad-lab.eu/prod/rae/api/v1/entries', auth=Auth())

    This Auth will given username/password to acquire a token, it will keep and manage
    the token, and apply it to the headers requests.

    Arguments:
        user: Optional user name or email, default is take from ``config.client.user``
        password: Optional password, default is taken from ``config.client.password``
        from_api: If true, the necessary access token is acquired through the NOMAD API
            and not via keycloak directly. Default is False. Not recommended, but might
            be useful, if keycloak can't be configured (e.g. during tests) or reached.
    """

    def __init__(
        self,
        user: str | None = config.client.user,
        password: str | None = (
            config.client.password.get_secret_value()
            if config.client.password
            else None
        ),
        from_api: bool = False,
    ):
        self.user = user
        self._password = password
        self.from_api = from_api

        self.__oidc = KeycloakOpenID(
            server_url=config.keycloak.server_url + '/',
            realm_name=config.keycloak.realm_name,
            client_id=config.keycloak.client_id,
        )

        if user and password:
            # force to get access token from user and password
            self._token = None
        elif config.client.access_token:
            # use pre-set access token
            self._token = dict(
                access_token=config.client.access_token.get_secret_value()
            )
        else:
            # no token, no auth
            self._token = None

    def get_access_token_from_api(self) -> None:
        if self._token is not None:
            return

        response = requests.post(
            url('auth/token'),
            data={
                'username': self.user,
                'password': self._password,
                'grant_type': 'password',
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )

        if response.status_code != 200:
            response_json = response.json()
            raise APIError(
                f'Could not acquire authentication token: '
                f'{response_json.get("description") or response_json.get("detail") or "unknown reason"} '
                f'({response_json.get("code", response.status_code)})'
            )

        self._token = response.json()

    def get_access_token_from_keycloak(self):
        if self._token is None and self.user and self._password:
            self._token = self.__oidc.token(username=self.user, password=self._password)
            self._token['time'] = time.time()
        elif not self._token or not (
            'expires_in' in self._token and 'time' in self._token
        ):
            # cannot refresh
            return
        elif self._token['expires_in'] < int(time.time()) - self._token['time'] + 10:
            try:
                self._token = self.__oidc.refresh_token(self._token['refresh_token'])
                self._token['time'] = time.time()
            except Exception:
                self._token = self.__oidc.token(
                    username=self.user, password=self._password
                )
                self._token['time'] = time.time()

    def __call__(self, request):
        request.headers.update(self.headers())
        return request

    def headers(self):
        if self.from_api:
            self.get_access_token_from_api()
        else:
            self.get_access_token_from_keycloak()

        if not self._token:
            return {}

        return dict(Authorization=f'Bearer {self._token["access_token"]}')
