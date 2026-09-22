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

import json
import threading
from typing import Literal

from keycloak import KeycloakOpenID
from keycloak.exceptions import KeycloakAuthenticationError
from pydantic import BaseModel

from nomad import utils
from nomad.config import config
from nomad.datamodel import User
from nomad.utils.structlogging import get_logger

logger = get_logger(__name__)


class OIDCToken(BaseModel):
    access_token: str
    token_type: Literal['Bearer']
    expires_in: int | None = None
    refresh_token: str | None = None
    scope: str | None = None
    id_token: str | None = None
    refresh_expires_in: int | None = None


class KeycloakError(Exception):
    pass


class Keycloak:
    """
    A class that encapsulates all keycloak related functions for easier mocking and
    configuration
    """

    _keycloak_lock = threading.Lock()

    def __init__(self) -> None:
        self.__oidc_client: KeycloakOpenID | None = None
        self.__public_keys: dict | None = None
        self.__issuer: str | None = None

    @property
    def _oidc_client(self) -> KeycloakOpenID:
        if self.__oidc_client is None:
            self.__oidc_client = KeycloakOpenID(
                server_url=config.keycloak.server_url + '/',
                client_id=config.keycloak.client_id,
                realm_name=config.keycloak.realm_name,
                client_secret_key=(
                    config.keycloak.client_secret.get_secret_value()
                    if config.keycloak.client_secret
                    else None
                ),
            )
        return self.__oidc_client

    @property
    def _public_keys(self) -> dict:
        if self.__public_keys is None:
            import jwt

            try:
                with Keycloak._keycloak_lock:
                    jwks = self._oidc_client.certs()
                    keys = {}
                    for jwk in jwks['keys']:
                        kid = jwk['kid']
                        keys[kid] = jwt.algorithms.RSAAlgorithm.from_jwk(  # type: ignore[index]
                            json.dumps(jwk)
                        )
                    self.__public_keys = keys
            except Exception as e:
                raise e

        return self.__public_keys

    @property
    def _issuer(self) -> str:
        if self.__issuer is None:
            with Keycloak._keycloak_lock:
                self.__issuer = self._oidc_client.well_known()['issuer']
        return self.__issuer

    def refresh_token(self, refresh_token: str) -> OIDCToken:
        """
        Use the OAuth2 refresh token grant to obtain a new token response.
        """
        return OIDCToken(**self._oidc_client.refresh_token(refresh_token))

    def basicauth(self, username: str, password: str) -> OIDCToken:
        """
        Performs basic authentication and returns the OAuth2 token response.

        Raises:
            KeycloakError
        """

        try:
            return OIDCToken(
                **self._oidc_client.token(username=username, password=password)
            )

        except KeycloakAuthenticationError as e:
            raise KeycloakError(e)
        except Exception as e:
            logger.error('cannot perform basicauth', exc_info=e)
            raise e

    def decode_access_token(self, access_token: str) -> dict:
        import jwt

        try:
            key_id = jwt.get_unverified_header(access_token)['kid']
            key = self._public_keys.get(key_id)
            if key is None:
                logger.error(
                    'The user provided keycloak public key does not exist. Does the UI use the right realm?'
                )
                raise KeycloakError(
                    utils.strip(
                        """
                    Could not validate credentials.
                    The user provided keycloak public key does not exist.
                    Does the UI use the right realm?"""
                    )
                )

            return jwt.decode(
                access_token,
                key=key,
                algorithms=['RS256'],
                options=dict(verify_aud=False),
                issuer=self._issuer,
            )
        except jwt.InvalidTokenError as e:
            logger.error('Keycloak token validation failed', exc_info=e)
            raise KeycloakError(
                'Could not validate credentials. The given token is invalid.'
            ) from e

    def tokenauth(self, access_token: str) -> User:
        """
        Authenticates the given access_token

        Returns:
            The user

        Raises:
            KeycloakError: if payload is invalid.
        """
        try:
            payload = self.decode_access_token(access_token)

            user_id: str | None = payload.get('sub')
            if user_id is None:
                raise KeycloakError(
                    utils.strip(
                        """
                    Could not validate credentials.
                    The given token does not contain a user_id."""
                    )
                )

            return User(
                user_id=user_id,
                username=payload.get('preferred_username', None),
                email=payload.get('email', None),
                first_name=payload.get('given_name', None),
                last_name=payload.get('family_name', None),
            )

        except Exception as e:
            logger.error('cannot perform tokenauth', exc_info=e)
            raise e


keycloak = Keycloak()
