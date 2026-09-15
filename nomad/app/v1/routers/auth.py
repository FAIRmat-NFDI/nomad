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

import urllib
from collections.abc import Callable, Collection
from enum import Enum
from inspect import Parameter, Signature
from typing import Annotated

import anyio
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi import Query as FastApiQuery
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestFormStrict
from pydantic import BaseModel, Field, field_validator

from nomad import datamodel
from nomad.auth.keycloak import KeycloakError, OIDCToken, keycloak
from nomad.auth.pat import (
    PAT_PREFIX,
    PATCreationSpec,
    PATQuery,
    PATSortOrder,
    pat_service,
)
from nomad.auth.scopes import Scope, resolve_scope_aliases
from nomad.auth.tokens import (
    AuthResult,
    generate_simple_token,
    get_user_from_keycloak_token,
    get_user_from_simple_token,
    get_user_from_upload_token,
)
from nomad.config import config
from nomad.config.models.config import ModeEnum
from nomad.models.common import UTCDateTime
from nomad.tracing import traced
from nomad.utils import get_logger

from ..common import root_path
from ..models import HTTPExceptionModel, MetadataPagination, PaginationResponse, User
from ..utils import create_responses, parameter_dependency_from_model

logger = get_logger(__name__)

router = APIRouter()


class APITag(str, Enum):
    OIDC = 'OpenID Connect Token Endpoints'
    PAT = 'Personal Access Token (PAT) Endpoints'
    CUSTOM = 'NOMAD Custom Token Endpoints'


# Authentication (resolve user) and authorization (enforce scopes)


oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f'{root_path}/auth/token', auto_error=False
)


@traced(span_name='graph.auth.resolve_user')
async def _resolve_user_with_scopes(
    *,
    required_scopes: set[str],
    allow_anonymous: bool,
    request: Request | None = None,
    keycloak_token: str | None = None,
    personal_access_token: str | None = None,
    simple_token: str | None = None,
    upload_token: str | None = None,
) -> User | None:
    """Resolve User/scopes from token and validate."""
    # Resolve user and extract scopes from (simple->keycloak->upload) token
    auth_result: AuthResult | None = None

    # TODO: after deprecated custom tokens are removed,
    # cleanup the token detection path

    # Resolve user from simple token
    if auth_result is None and simple_token and not simple_token.startswith(PAT_PREFIX):
        try:
            unverified_payload = jwt.decode(
                simple_token, options={'verify_signature': False}
            )
            # This is used to distinguish simple token from keycloak token:
            # simple token only has `user/exp` in payload,
            # while the keycloak has much more (RFC 7519)
            if unverified_payload.keys() == {'user', 'exp'}:
                auth_result = await get_user_from_simple_token(simple_token)
        except jwt.DecodeError as e:  # token could be non-JWT (for testing)
            logger.error('Failed to decode simple token', exc_info=e)

    # Resolve user from keycloak token (cookie or header)
    if auth_result is None and (keycloak_token or request):
        # Get token from cookie
        if keycloak_token is None and request is not None:
            auth_cookie = request.cookies.get('Authorization')
            if auth_cookie is not None:
                auth_cookie = urllib.parse.unquote(auth_cookie)
                keycloak_token = auth_cookie.removeprefix('Bearer ')

        if keycloak_token is not None:
            auth_result = get_user_from_keycloak_token(keycloak_token)

    # Resolve user from personal access token
    if auth_result is None and personal_access_token:
        # TODO: migrate to `beanie`
        pat = await anyio.to_thread.run_sync(
            pat_service.authenticate, personal_access_token
        )

        if pat is not None:
            user = await datamodel.User.a_get(user_id=pat.user_id)
            if user:
                auth_result = AuthResult(user=user, scopes=set(pat.scopes))
            else:
                # The user was deleted, but their PAT still exists
                logger.warning(f'Valid PAT used for missing user_id: {pat.user_id}')

    # Resolve user from upload token
    if auth_result is None and upload_token:
        auth_result = await get_user_from_upload_token(upload_token)

    if auth_result is None:  # user resolving failed: anonymous user
        user = None
        scopes = config.auth.unauthenticated_user_scopes_resolved
    else:
        user = auth_result.user
        scopes = auth_result.scopes

    # [DEV ONLY] allow tester to bypass auth
    if config.tests.assume_auth_for_username:
        if config.services.mode != ModeEnum.DEVELOPMENT:
            raise ValueError('assume_auth_for_username is development-only')

        user = await datamodel.User.a_get(
            username=config.tests.assume_auth_for_username
        )
        scopes = Scope.all_values()  # full permission for tester

    # Anonymous users
    if user is None:
        if not allow_anonymous or config.auth.require_authentication:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Authentication required.',
                headers={'WWW-Authenticate': 'Bearer'},
            )

    # Non-anonymous user
    else:
        # Validate user against Keycloak
        try:
            existing_user = await datamodel.User.a_get(user_id=user.user_id)
        except Exception as e:
            logger.error(
                'Failed to lookup authenticated user in Keycloak.',
                exc_info=e,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail='Failed to verify authenticated user',
            ) from e

        if existing_user is None:
            logger.warning(
                'API usage by unknown user.',
                extra={'user_id': user.user_id},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail='You are logged in with an unknown user',
            )

        # Check user whitelist (via `authorized_users`)
        if (
            config.auth.authorized_users is not None
            and user.email not in config.auth.authorized_users
            and user.username not in config.auth.authorized_users
        ):
            if config.auth.reject_unauthorized_users:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail='You are not authorized to access this Oasis',
                )
            else:
                scopes = config.auth.unauthorized_user_scopes_resolved

    # TODO: should check user CURRENT "roles"
    # 1. currently any user could require any scope
    # 2. imagine someone was admin before but not anymore,
    # they shouldn't be able to use old tokens with admin permission

    # Enforce backend scopes. Resolve deprecated aliases so existing PATs keep
    # working while newly created tokens only expose the current scope names.
    resolved_scopes = resolve_scope_aliases(scopes)
    if missing_scopes := required_scopes - resolved_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f'Missing scopes: {sorted(missing_scopes)}',
        )

    return user


def get_current_user(
    required_scopes: Collection[str] | str,
    *,
    allow_anonymous: bool = True,
    allow_keycloak_token: bool = True,
    allow_personal_access_token: bool = True,
    allow_simple_token: bool = True,
    allow_upload_token: bool = False,
) -> Callable:
    """
    Build a FastAPI dependency that resolves User and enforces scopes.

    Args:
        required_scopes: scope(s) this endpoint needs.
        allow_anonymous: whether to allow anonymous (no-login) access.
        allow_*_token: toggle which tokens are accepted.
    """
    if isinstance(required_scopes, str):
        required_scopes = {required_scopes}
    else:
        required_scopes = set(required_scopes)

    async def current_user(**kwargs) -> User | None:
        return await _resolve_user_with_scopes(
            required_scopes=required_scopes,
            allow_anonymous=allow_anonymous,
            request=kwargs.get('request'),
            keycloak_token=kwargs.get('keycloak_token'),
            personal_access_token=kwargs.get('personal_access_token'),
            simple_token=kwargs.get('simple_token'),
            upload_token=kwargs.get('upload_token'),
        )

    # Build signature
    parameters: list[Parameter] = []

    if allow_keycloak_token:
        parameters.append(
            Parameter(
                name='request',
                annotation=Request,  # for getting keycloak token from cookie
                kind=Parameter.POSITIONAL_OR_KEYWORD,
            )
        )
        parameters.append(
            Parameter(
                name='keycloak_token',
                annotation=str | None,
                default=Depends(oauth2_scheme),
                kind=Parameter.KEYWORD_ONLY,
            )
        )

    if allow_personal_access_token:
        parameters.append(
            Parameter(
                name='personal_access_token',
                annotation=str | None,
                default=Depends(oauth2_scheme),
                kind=Parameter.KEYWORD_ONLY,
            )
        )

    if allow_simple_token:
        parameters.append(
            Parameter(
                name='simple_token',
                annotation=str | None,
                default=Depends(oauth2_scheme),
                kind=Parameter.KEYWORD_ONLY,
            )
        )

    if allow_upload_token:
        parameters.append(
            Parameter(
                name='upload_token',
                annotation=str,
                default=Header(
                    None,
                    alias='Upload-Token',
                    description='HMAC-signed upload token.',
                ),
                kind=Parameter.KEYWORD_ONLY,
            )
        )

    current_user.__signature__ = Signature(parameters)  # type: ignore[attr-defined]
    return current_user


# OpenID Connect (OIDC) endpoints


_bad_credentials_response = (
    status.HTTP_401_UNAUTHORIZED,
    {
        'model': HTTPExceptionModel,
        'description': 'Unauthorized. The provided credentials were not recognized.',
    },
)


@router.post(
    '/token',
    tags=[APITag.OIDC],
    summary='Get an OIDC token response',
    responses=create_responses(_bad_credentials_response),
)
def get_token(
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestFormStrict, Depends()],
) -> OIDCToken:
    """
    Implements the OpenID Connect (OIDC) Resource Owner Password Credentials (ROPC) grant flow.

    Clients can obtain a token set by posting a username and password as form data.
    The response includes `access_token`, `id_token`, `refresh_token`, and related metadata.

    The `access_token` must be included in the `Authorization` header for subsequent
    API requests, e.g.:
        Authorization: Bearer <access_token>

    On the OpenAPI dashboard, you can use the *Authorize* button at the top.
    """
    try:
        token = keycloak.basicauth(form_data.username, form_data.password)
        # Add mandatory headers (RFC 6749 §5.1)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
        return token

    except KeycloakError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Incorrect username or password',
            headers={'WWW-Authenticate': 'Basic'},
        )


# NOMAD Personal Access Token (PAT)


class PATCreateRequest(BaseModel):
    """Payload for creating a new token."""

    metadata: PATCreationSpec
    expires_in_days: int | None = 30


class PATResponse(BaseModel):
    """Standard representation of a token (safe to return to user)."""

    id: str
    name: str
    scopes: list[str]
    description: str | None = None
    revoked: bool

    created_at: UTCDateTime
    expired_at: UTCDateTime | None = None
    last_used_at: UTCDateTime | None = None

    class Config:
        from_attributes = True

    @field_validator('id', mode='before')
    @classmethod
    def convert_objectid_to_str(cls, value):
        """Forces MongoDB ObjectIds to cleanly serialize into strings."""
        return str(value)


class PATCreationResponse(BaseModel):
    """Returned ONLY upon creation or rotation."""

    pat: PATResponse
    raw_token: str


_pat_bad_request_response = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': 'Bad request. Invalid personal access token request.',
    },
)

_pat_not_found_response = (
    status.HTTP_404_NOT_FOUND,
    {
        'model': HTTPExceptionModel,
        'description': 'Not found. Token does not exist or is not owned by the user.',
    },
)


@router.post(
    '/pats',
    response_model=PATCreationResponse,
    status_code=status.HTTP_201_CREATED,
    summary='Create a personal access token',
    tags=[APITag.PAT],
    responses=create_responses(_pat_bad_request_response),
)
def create_pat_endpoint(
    request: PATCreateRequest,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.TOKENS_CREATE], allow_anonymous=False)),
    ],
):
    """
    Creates a new PAT.

    **WARNING**: The `raw_token` field in the response is only visible once.

    Raises:
        400 Bad Request: If `expires_in_days` is invalid (e.g., negative).
    """
    if not request.metadata.scopes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='At least one scope must be selected.',
        )

    try:
        return pat_service.create(
            user_id=user.user_id,
            metadata=request.metadata,
            expires_in_days=request.expires_in_days,
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    '/pats/{pat_id}/rotate',
    response_model=PATCreationResponse,
    summary='Rotate a personal access token',
    tags=[APITag.PAT],
    responses=create_responses(_pat_bad_request_response, _pat_not_found_response),
)
def rotate_pat_endpoint(
    pat_id: str,
    user: Annotated[
        User,
        Depends(
            get_current_user(
                [Scope.TOKENS_CREATE, Scope.TOKENS_DELETE], allow_anonymous=False
            )
        ),
    ],
):
    """
    Rotates an existing PAT.

    This revokes the old token and issues a new one,
    copying the original metadata and calculating
    a new expiration date based on the original token's lifespan.

    Raises:
        400 Bad Request: If the token is expired or revoked (i.e., not active).
        404 Not Found: If the target token does not exist.
    """
    try:
        result = pat_service.rotate(user_id=user.user_id, pat_id=pat_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Token not found or does not belong to the user.',
        )

    return result


class PATPagination(MetadataPagination):
    order_by: PATSortOrder = Field(
        'created_desc',
        description='Order the results. Defaults to created_desc (newest first).',
    )


class PATQueryResponse(BaseModel):
    query: PATQuery
    pagination: PaginationResponse
    data: list[PATResponse]


pat_query_parameters = parameter_dependency_from_model(
    'pat_query_parameters',
    PATQuery,
)

pat_pagination_parameters = parameter_dependency_from_model(
    'pat_pagination_parameters',
    PATPagination,
)


@router.get(
    '/pats',
    summary='List personal access tokens',
    tags=[APITag.PAT],
    response_model=PATQueryResponse,
)
def list_pat_endpoint(
    request: Request,
    query: Annotated[PATQuery, Depends(pat_query_parameters)],
    pagination: Annotated[PATPagination, Depends(pat_pagination_parameters)],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.TOKENS_READ], allow_anonymous=False)),
    ],
):
    """
    Retrieves a paginated list of PATs.
    """
    # Fetch data and total count from the service layer
    result = pat_service.list_owned(
        user_id=user.user_id,
        query=query,
        start=pagination.get_simple_index(),
        limit=pagination.page_size,
        order_by=pagination.order_by,
    )

    pydantic_data = [PATResponse.model_validate(pat) for pat in result.data]

    pagination_response = PaginationResponse(total=result.total, **pagination.dict())
    pagination_response.populate_simple_index_and_urls(request)

    return PATQueryResponse(
        query=query, pagination=pagination_response, data=pydantic_data
    )


@router.get(
    '/pats/{pat_id}',
    response_model=PATResponse,
    summary='Retrieve metadata for a personal access token',
    tags=[APITag.PAT],
    responses=create_responses(_pat_bad_request_response, _pat_not_found_response),
)
def get_pat_endpoint(
    pat_id: str,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.TOKENS_READ], allow_anonymous=False)),
    ],
):
    """
    Retrieves metadata for a specific PAT owned by the user.

    Raises:
        400 bad request: If the token ID format is invalid.
        404 Not Found: If the token does not exist or belongs to another user.
    """
    pat = pat_service.get(user_id=user.user_id, pat_id=pat_id)

    if pat is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Token not found or does not belong to the user.',
        )

    return pat


@router.delete(
    '/pats/{pat_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Revoke a personal access token',
    tags=[APITag.PAT],
    responses=create_responses(_pat_not_found_response),
)
def revoke_pat_endpoint(
    pat_id: str,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.TOKENS_DELETE], allow_anonymous=False)),
    ],
):
    """
    Revokes a personal access token.

    Raises:
        404 Not Found: If the target token does not exist.
    """
    success = pat_service.revoke(user_id=user.user_id, pat_id=pat_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Token not found or does not belong to the user.',
        )


# NOMAD custom token (DEPRECATED)


class SignatureToken(BaseModel):
    signature_token: str


class AppToken(BaseModel):
    app_token: str


@router.get(
    '/signature_token',
    tags=[APITag.CUSTOM],
    summary='Get a signature token',
)
async def get_signature_token(
    user: Annotated[
        User,
        Depends(get_current_user([Scope.TOKENS_CREATE], allow_anonymous=False)),
    ],
) -> SignatureToken:
    """
    Generate a signature token for the authenticated user.
    Authentication has to be provided via access token.
    """
    return SignatureToken(
        signature_token=generate_simple_token(user.user_id, expires_in=10)
    )


@router.get(
    '/app_token',
    tags=[APITag.CUSTOM],
    summary='Get an app token',
)
async def get_app_token(
    expires_in: Annotated[
        int, FastApiQuery(gt=0, le=config.services.app_token_max_expires_in)
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.TOKENS_CREATE], allow_anonymous=False)),
    ],
) -> AppToken:
    """
    Generate an app token with the requested expiration time for the
    authenticated user. Authentication has to be provided via access token.

    This app token can be used like the access token (see `/auth/token`) on subsequent API
    calls to authenticate you using the HTTP header `Authorization: Bearer <app token>`.
    It is provided for user convenience with a user-defined (probably longer) expiration time.
    """
    return AppToken(app_token=generate_simple_token(user.user_id, expires_in))
