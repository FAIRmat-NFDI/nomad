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

import datetime
import hashlib
import hmac
import uuid
from dataclasses import dataclass
from typing import cast

from nomad import datamodel, utils
from nomad.auth import keycloak
from nomad.auth.keycloak import KeycloakError
from nomad.auth.pat import PAT_PREFIX
from nomad.auth.scopes import _resolve_scopes
from nomad.common import now
from nomad.config import config
from nomad.config.models.config import _DEFAULT_API_KEY, ModeEnum
from nomad.datamodel import User


@dataclass(frozen=True)
class AuthResult:
    user: User
    scopes: set[str]


# Keycloak token


def get_user_from_keycloak_token(keycloak_token: str | None) -> AuthResult | None:
    """
    Verifies keycloak bearer token.

    Returns:
        The corresponding AuthResult object,
        or None if cannot resolve.
    """
    from fastapi import HTTPException, status

    if keycloak_token is None or keycloak_token.startswith(PAT_PREFIX):
        return None

    try:
        user = cast(datamodel.User, keycloak.keycloak.tokenauth(keycloak_token))

    except KeycloakError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={'WWW-Authenticate': 'Bearer'},
        )

    return AuthResult(user, _resolve_scopes(['*:*']))


# [Deprecated] Other NOMAD custom tokens (signature/simple/upload token)


JWT_ALGORITHM = 'HS256'
HMAC_DIGESTMOD = hashlib.sha256


def check_api_secret() -> None:
    if (
        config.services.mode == ModeEnum.PRODUCTION
        and config.services.api_secret == _DEFAULT_API_KEY
    ):
        raise ValueError(
            'When running NOMAD in production mode, value for config.services.api_secret must be set to a minimum 32 character string through the environment variable NOMAD_SERVICES_API_SECRET. '
            'Alternatively you can run NOMAD in an insecure development mode by setting config.services.mode to development.'
        )


def generate_simple_token(user_id: str, expires_in: float) -> str:
    """
    Generate a simple token: JWT encoded user_id and expiration time,
    signed with the API secret.
    """
    import jwt

    check_api_secret()
    expires_at = now() + datetime.timedelta(seconds=expires_in)
    payload = dict(user=user_id, exp=expires_at)
    return jwt.encode(
        payload=payload, key=config.services.api_secret, algorithm=JWT_ALGORITHM
    )


async def get_user_from_simple_token(simple_token: str | None) -> AuthResult | None:
    """
    Verifies a simple token (throwing HTTPException if illegal value provided).

    Returns:
        The corresponding AuthResult object,
        or None if cannot resolve.
    """
    import jwt
    from fastapi import HTTPException, status

    if simple_token is None:
        return None

    check_api_secret()

    try:
        decoded = jwt.decode(
            simple_token, config.services.api_secret, algorithms=[JWT_ALGORITHM]
        )
        user = await User.a_get(user_id=decoded['user'])
        scopes = _resolve_scopes(['*:*']) - _resolve_scopes(['tokens:*'])
        return AuthResult(user, scopes)

    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Token with invalid/unexpected payload.',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Expired token.',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid token.',
            headers={'WWW-Authenticate': 'Bearer'},
        )


def generate_upload_token(user: User) -> str:
    """Generate an upload token for user."""
    check_api_secret()
    payload = uuid.UUID(user.user_id).bytes
    signature = hmac.new(
        config.services.api_secret.encode('utf-8'),
        msg=payload,
        digestmod=HMAC_DIGESTMOD,
    )

    return f'{utils.base64_encode(payload)}.{utils.base64_encode(signature.digest())}'


async def get_user_from_upload_token(upload_token: str | None) -> AuthResult | None:
    """
    Verifies the upload token (throwing HTTPException if illegal value provided).

    Returns:
        The corresponding AuthResult object,
        or None if cannot resolve.
    """
    from fastapi import HTTPException, status

    if upload_token is None:
        return None

    check_api_secret()

    try:
        payload, signature = upload_token.split('.', 1)
        payload_bytes = utils.base64_decode(payload)
        signature_bytes = utils.base64_decode(signature)

        expected = hmac.new(
            config.services.api_secret.encode('utf-8'),
            msg=payload_bytes,
            digestmod=HMAC_DIGESTMOD,
        )

        if not hmac.compare_digest(signature_bytes, expected.digest()):
            raise ValueError('Invalid HMAC signature')

        user_id = str(uuid.UUID(bytes=payload_bytes))
        user = await User.a_get(user_id=user_id)
        return AuthResult(user, _resolve_scopes(['uploads:*']))

    except Exception:
        # Decode error, format error, user not found, etc.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='An invalid upload token was supplied.',
            headers={'WWW-Authenticate': 'Bearer'},
        )
