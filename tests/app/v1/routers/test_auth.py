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

import datetime
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException, Request, status

from nomad.app.v1.models.models import User
from nomad.app.v1.routers.auth import (
    PATPagination,
    PATQueryResponse,
    _resolve_user_with_scopes,
    get_current_user,
)
from nomad.auth.keycloak import KeycloakError
from nomad.auth.pat import PAT_PREFIX, PATQuery
from nomad.auth.pat import hash_token as _hash_token
from nomad.auth.scopes import Scope
from nomad.auth.tokens import AuthResult
from nomad.common import now
from nomad.config.models.config import ModeEnum
from nomad.mongo.pat import PAT
from tests.test_config import load_test_config

DEFAULT_TEST_SCOPES: list[Scope] = [Scope.UPLOADS_READ]

# Tests for OIDC authentication endpoints


@pytest.mark.parametrize(
    'form_data, expected_status',
    [
        pytest.param(
            dict(username='user1', password='password', grant_type='password'),
            200,
            id='valid-credentials',
        ),
        pytest.param(
            dict(username='bad', password='credentials', grant_type='password'),
            401,
            id='invalid-credentials',
        ),
        pytest.param(
            dict(username='user1', password='password'),
            422,
            id='missing-grant-type',
        ),
    ],
)
def test_post_token_various_cases(client, user1, form_data, expected_status):
    if form_data.get('username') == 'user1':
        form_data['username'] = user1.username

    response = client.post('auth/token', data=form_data)
    assert response.status_code == expected_status

    if expected_status == 200:
        assert response.headers.get('Cache-Control') == 'no-store'
        assert response.headers.get('Pragma') == 'no-cache'


# Tests for NOMAD custom tokens (simple token, upload token)


@pytest.mark.parametrize(
    'auth_key, expected_status',
    [
        pytest.param('user1', 200, id='authorized'),
        pytest.param(None, 401, id='no-auth'),
        pytest.param('invalid', 401, id='invalid-auth'),
    ],
)
def test_get_signature_token(auth_headers, client, auth_key, expected_status):
    headers = auth_headers.get(auth_key) if auth_key else None
    response = client.get('auth/signature_token', headers=headers)
    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json().get('signature_token') is not None


@pytest.mark.parametrize(
    'auth_key, expires_in, expected_status',
    [
        pytest.param('user1', 0, 422, id='valid-auth-expires-too-short'),
        pytest.param('user1', 30 * 60, 200, id='valid-auth-expires-30min'),
        pytest.param('user1', 2 * 60 * 60, 200, id='valid-auth-expires-2h'),
        pytest.param('user1', 31 * 24 * 60 * 60, 422, id='valid-auth-expires-too-long'),
        pytest.param('user1', None, 422, id='valid-auth-expires-missing'),
        pytest.param(None, 60, 401, id='no-auth'),
        pytest.param('invalid', 60, 401, id='invalid-auth'),
    ],
)
def test_get_app_token(auth_headers, client, auth_key, expires_in, expected_status):
    headers = auth_headers.get(auth_key) if auth_key else None
    response = client.get(
        'auth/app_token',
        headers=headers,
        params={'expires_in': expires_in},
    )
    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json().get('app_token') is not None


# Tests for `get_current_user`


@pytest.fixture
def allowed_user():
    return User(user_id='123', email='test@example.com', username='tester')


@pytest.fixture
def patch_user_get(monkeypatch):
    """
    Patch datamodel.User.get.

    Usage:
        patch_user_get(user)   -> User.get(...) returns user
        patch_user_get(None)   -> User.get(...) returns None
    """

    def _patch(user: User | None) -> None:
        monkeypatch.setattr(
            'nomad.app.v1.routers.auth.datamodel.User.get',
            lambda *args, **kwargs: user,
        )

    return _patch


class MockPAT:
    """A simple mock to mimic the MongoEngine PAT object needed by the dependency."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.scopes: list[str] = []


@pytest.mark.parametrize('allow_keycloak_token', [True, False])
@pytest.mark.parametrize('allow_simple_token', [True, False])
@pytest.mark.parametrize('allow_upload_token', [True, False])
@pytest.mark.parametrize('allow_personal_access_token', [True, False])
@pytest.mark.parametrize('get_user_from_keycloak_token', [True, False])
@pytest.mark.parametrize('get_user_from_simple_token', [True, False])
@pytest.mark.parametrize('get_user_from_upload_token', [True, False])
@pytest.mark.parametrize('authenticate_pat', [True, False])
def test_get_current_user_auth_methods(
    allow_keycloak_token: bool,
    allow_simple_token: bool,
    allow_upload_token: bool,
    allow_personal_access_token: bool,
    get_user_from_keycloak_token: bool,
    get_user_from_simple_token: bool,
    get_user_from_upload_token: bool,
    authenticate_pat: bool,
    allowed_user,
    patch_user_get,
    monkeypatch,
):
    if allow_simple_token:  # ensure dummy simple token could decode as JWT
        monkeypatch.setattr(
            'nomad.app.v1.routers.auth.jwt.decode',
            lambda *args, **kwargs: {'user': allowed_user.user_id, 'exp': 600},
        )

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_keycloak_token',
        lambda _token: (
            AuthResult(allowed_user, set()) if get_user_from_keycloak_token else None
        ),
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_simple_token',
        lambda _token: (
            AuthResult(allowed_user, set()) if get_user_from_simple_token else None
        ),
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_upload_token',
        lambda _token: (
            AuthResult(allowed_user, set()) if get_user_from_upload_token else None
        ),
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.pat_service.authenticate',
        lambda _token: MockPAT(allowed_user.user_id) if authenticate_pat else None,
    )

    patch_user_get(allowed_user)

    dep = get_current_user(
        required_scopes=[],
        allow_anonymous=False,
        allow_keycloak_token=allow_keycloak_token,
        allow_simple_token=allow_simple_token,
        allow_upload_token=allow_upload_token,
        allow_personal_access_token=allow_personal_access_token,
    )

    if any(
        [
            allow_keycloak_token and get_user_from_keycloak_token,
            allow_simple_token and get_user_from_simple_token,
            allow_upload_token and get_user_from_upload_token,
            allow_personal_access_token and authenticate_pat,
        ]
    ):
        assert (
            dep(
                keycloak_token='abc' if allow_keycloak_token else None,
                simple_token='def' if allow_simple_token else None,
                upload_token='ghi' if allow_upload_token else None,
                personal_access_token='jkl' if allow_personal_access_token else None,
            )
            == allowed_user
        )
    else:
        with pytest.raises(HTTPException, match='Authentication required.') as exc:
            dep()
        assert exc.value.status_code == 401


def test_get_current_user_keycloak_token_from_cookie(
    monkeypatch, allowed_user, patch_user_get
):
    monkeypatch.setattr(
        'nomad.auth.keycloak.keycloak.tokenauth',
        lambda _token: allowed_user,
    )
    patch_user_get(allowed_user)

    dep = get_current_user(
        required_scopes=[],
        allow_anonymous=False,
        allow_keycloak_token=True,
    )

    # Success case
    request = Request(
        {
            'type': 'http',
            'headers': [],
            'path': '/',
            'query_string': b'',
        }
    )
    request._cookies = {'Authorization': 'Bearer abc'}
    assert dep(request=request) == allowed_user

    # Failure case: no token in cookies
    request._cookies = {}
    with pytest.raises(HTTPException, match='Authentication required.') as exc:
        dep(request=request)
    assert exc.value.status_code == 401


def test_pat_can_authenticate_with_keycloak_token_allowed(
    allowed_user,
    patch_user_get,
    monkeypatch,
):
    """Ensure checking on the keycloak token path wouldn't
    block authenticate via PAT.
    """

    monkeypatch.setattr(
        'nomad.auth.keycloak.keycloak.tokenauth',
        lambda _token: (_ for _ in ()).throw(KeycloakError('invalid token')),
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.pat_service.authenticate',
        lambda _token: MockPAT(allowed_user.user_id),
    )

    patch_user_get(allowed_user)

    dep = get_current_user(
        required_scopes=[],
        allow_anonymous=False,
        allow_keycloak_token=True,
        allow_personal_access_token=True,
    )

    token = f'{PAT_PREFIX}dummy'
    user = dep(
        keycloak_token=token,
        personal_access_token=token,
    )

    assert user == allowed_user


@pytest.mark.parametrize('allow_anonymous', [True, False])
def test_get_current_user_allow_anonymous(allow_anonymous):
    dep = get_current_user(
        required_scopes=[Scope.ENTRIES_READ], allow_anonymous=allow_anonymous
    )

    if allow_anonymous:
        assert dep() is None

    else:
        with pytest.raises(HTTPException, match='Authentication required.') as exc:
            dep()
        assert exc.value.status_code == 401


def test_get_current_user_unknown_user(allowed_user, monkeypatch):
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_keycloak_token',
        lambda _token: AuthResult(allowed_user, set()),
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.datamodel.User.get',
        lambda *args, **kwargs: None,
    )

    dep = get_current_user(required_scopes=[])
    with pytest.raises(HTTPException, match='logged in with an unknown user') as exc:
        dep(keycloak_token='abc')
    assert exc.value.status_code == 403


def test_get_current_user_keycloak_lookup_failure(allowed_user, monkeypatch):
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_keycloak_token',
        lambda _token: AuthResult(allowed_user, set()),
    )

    def _raise(*args, **kwargs):
        raise RuntimeError('keycloak unavailable')

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.datamodel.User.get',
        _raise,
    )

    dep = get_current_user(required_scopes=[])
    with pytest.raises(
        HTTPException, match='Failed to verify authenticated user'
    ) as exc:
        dep(keycloak_token='abc')

    assert exc.value.status_code == 500


@pytest.mark.parametrize('tester', [None, 'tester'])
@pytest.mark.parametrize('mode', [ModeEnum.PRODUCTION, ModeEnum.DEVELOPMENT])
def test_get_current_user_assume_auth_for_username(
    tester, mode, allowed_user, patch_user_get, monkeypatch
):
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.config.tests.assume_auth_for_username', tester
    )
    monkeypatch.setattr('nomad.app.v1.routers.auth.config.services.mode', mode)

    patch_user_get(allowed_user)

    dep = get_current_user(required_scopes=[], allow_anonymous=False)

    if tester is None:
        with pytest.raises(HTTPException, match='Authentication required.') as exc:
            dep()
        assert exc.value.status_code == 401

    elif mode == ModeEnum.PRODUCTION:
        with pytest.raises(
            ValueError, match='assume_auth_for_username is development-only'
        ):
            dep()

    else:
        assert dep() == allowed_user


@pytest.mark.parametrize(
    'user, required_scopes, require_authentication, reject_unauthorized_users, authorized_users, status_code, exc_msg',
    [
        pytest.param(
            'tester',
            [],
            True,
            True,
            ['tester'],
            200,
            None,
            id='authenticated-user-in-allowed-users',
        ),
        pytest.param(
            'tester',
            [],
            True,
            True,
            ['my-user'],
            403,
            'You are not authorized to access this Oasis',
            id='authenticated-user-not-in-allowed-users',
        ),
        pytest.param(
            'tester',
            [],
            True,
            False,
            ['my-user'],
            200,
            None,
            id='authenticated-user-no-authorization-required',
        ),
        pytest.param(
            None,
            [],
            True,
            True,
            ['tester'],
            401,
            'Authentication required',
            id='unauthenticated-user-authentication-required',
        ),
        pytest.param(
            None,
            [],
            False,
            True,
            None,
            200,
            None,
            id='unauthenticated-user-authentication-not-required',
        ),
        pytest.param(
            None,
            ['uploads:read'],
            False,
            True,
            None,
            403,
            'Missing scopes:',
            id='unauthenticated-user-authentication-not-required-invalid-scope',
        ),
    ],
)
def test_get_current_user(
    user,
    required_scopes: list[str],
    require_authentication: bool,
    reject_unauthorized_users: bool,
    authorized_users: list[str],
    status_code: int,
    exc_msg: str,
    allowed_user,
    patch_user_get,
    monkeypatch,
):
    if user == 'tester':
        auth_user = allowed_user
    elif user is None:
        auth_user = None
    else:
        raise ValueError(f'Invalid user value: {user}')

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.config.auth.require_authentication',
        require_authentication,
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.config.auth.reject_unauthorized_users',
        reject_unauthorized_users,
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.config.auth.authorized_users', authorized_users
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_keycloak_token',
        lambda _token: AuthResult(auth_user, set()),
    )
    if auth_user is not None:
        patch_user_get(auth_user)

    dep = get_current_user(required_scopes=required_scopes)

    if status_code != 200:
        with pytest.raises(HTTPException, match=exc_msg) as exc:
            dep(keycloak_token='abc')
        assert exc.value.status_code == status_code
    else:
        patch_user_get(allowed_user)
        reveived_user = dep(keycloak_token='abc')
        if user is not None:
            assert reveived_user == allowed_user


def test_get_current_user_deleted_user_warning(mongo_function, monkeypatch):
    """
    Test that if a valid PAT is used but the associated user is missing,
    the dependency rejects the request with a warning.
    """
    raw_token = f'{PAT_PREFIX}pat_mock_secret_token_for_test'
    token_digest = _hash_token(raw_token)

    # Manually insert an orphaned PAT
    pat = PAT(
        user_id='deleted_user_123',
        name='Orphaned Token',
        token_digest=token_digest,
        scopes=['uploads:read'],
    )
    pat.save()
    assert PAT.objects.filter(id=pat.id).count() == 1

    # Simulate the user missing from Keycloak
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.datamodel.User.get', lambda *args, **kwargs: None
    )

    dep = get_current_user(
        required_scopes=[],
        allow_anonymous=False,
        allow_keycloak_token=False,
    )

    # Attempt to authenticate using the orphaned token
    warnings = []

    def fake_warning(msg, *args, **kwargs):
        warnings.append(msg)

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.logger.warning',
        fake_warning,
    )

    with pytest.raises(HTTPException) as exc:
        dep(personal_access_token=raw_token)

    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert f'Valid PAT used for missing user_id: {pat.user_id}' in warnings


# Tests for scope enforcing (`_resolve_user_with_scopes`)

# Anonymous users


def test_scopes_anonymous_allowed_with_permission(monkeypatch):
    """
    Anonymous user should be allowed when allow_anonymous=True and
    unauthenticated_user_scopes contains the required scopes.
    """

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.config.auth.unauthenticated_user_scopes',
        {'include': [Scope.UPLOADS_READ]},
    )

    dep = get_current_user(required_scopes=[Scope.UPLOADS_READ], allow_anonymous=True)

    assert dep() is None


def test_scopes_anonymous_not_allowed(monkeypatch):
    """
    Anonymous user should be rejected when not allow_anonymous.
    """

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.config.auth.unauthenticated_user_scopes',
        {'include': [Scope.UPLOADS_READ]},
    )

    dep = get_current_user(required_scopes=[Scope.UPLOADS_READ], allow_anonymous=False)

    with pytest.raises(HTTPException, match='Authentication required') as exc:
        dep()
    assert exc.value.status_code == 401


# Authenticated user


def test_scopes_authenticated_missing_scope(monkeypatch, allowed_user, patch_user_get):
    """
    Authenticated user should be forbidden (403) when scopes do not include required scopes.
    """
    patch_user_get(allowed_user)
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_keycloak_token',
        lambda _token: AuthResult(allowed_user, {Scope.UPLOADS_READ}),
    )

    dep = get_current_user(
        required_scopes=[Scope.GROUPS_READ],
        allow_anonymous=False,
        allow_keycloak_token=True,
    )

    with pytest.raises(HTTPException, match='Missing scopes') as exc:
        dep(keycloak_token='abc')
    assert exc.value.status_code == 403
    assert Scope.GROUPS_READ in str(exc.value.detail)


def test_scopes_authenticated_success(monkeypatch, allowed_user, patch_user_get):
    """
    Authenticated user should succeed when required scopes are present.
    """
    patch_user_get(allowed_user)
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_keycloak_token',
        lambda _token: AuthResult(allowed_user, {Scope.GROUPS_READ}),
    )

    dep = get_current_user(
        required_scopes=[Scope.GROUPS_READ],
        allow_anonymous=False,
        allow_keycloak_token=True,
    )

    assert dep(keycloak_token='abc') == allowed_user


# Scopes for simple/upload tokens


def test_scopes_simple_token_missing_scope(monkeypatch, allowed_user, patch_user_get):
    patch_user_get(allowed_user)

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.jwt.decode',
        lambda *args, **kwargs: {'user': allowed_user.user_id, 'exp': 600},
    )

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_simple_token',
        lambda _token: AuthResult(allowed_user, {Scope.UPLOADS_READ}),
    )

    dep = get_current_user(
        required_scopes=[Scope.TOKENS_CREATE],
        allow_anonymous=False,
        allow_keycloak_token=False,
        allow_simple_token=True,
        allow_upload_token=False,
    )

    with pytest.raises(HTTPException, match='Missing scopes') as exc:
        dep(simple_token='dummy-simple-token')
    assert exc.value.status_code == 403
    assert Scope.TOKENS_CREATE in str(exc.value.detail)


def test_scopes_simple_token_success(monkeypatch, allowed_user, patch_user_get):
    patch_user_get(allowed_user)

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.jwt.decode',
        lambda *args, **kwargs: {'user': allowed_user.user_id, 'exp': 600},
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_simple_token',
        lambda _token: AuthResult(allowed_user, {Scope.GROUPS_READ}),
    )

    dep = get_current_user(
        required_scopes=[Scope.GROUPS_READ],
        allow_anonymous=False,
        allow_keycloak_token=False,
        allow_simple_token=True,
        allow_upload_token=False,
    )

    assert dep(simple_token='dummy-simple-token') == allowed_user


def test_scopes_upload_token_allows_uploads_read(
    monkeypatch, allowed_user, patch_user_get
):
    patch_user_get(allowed_user)

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_upload_token',
        lambda _token: AuthResult(allowed_user, {Scope.UPLOADS_READ}),
    )

    dep = get_current_user(
        required_scopes=[Scope.UPLOADS_READ],
        allow_anonymous=False,
        allow_keycloak_token=False,
        allow_simple_token=False,
        allow_upload_token=True,
    )

    assert dep(upload_token='dummy-upload-token') == allowed_user


def test_scopes_upload_token_missing_non_upload_scope(
    monkeypatch, allowed_user, patch_user_get
):
    patch_user_get(allowed_user)

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_upload_token',
        lambda _token: AuthResult(allowed_user, {Scope.UPLOADS_READ}),
    )

    dep = get_current_user(
        required_scopes=[Scope.GROUPS_READ],
        allow_anonymous=False,
        allow_keycloak_token=False,
        allow_simple_token=False,
        allow_upload_token=True,
    )

    with pytest.raises(HTTPException, match='Missing scopes') as exc:
        dep(upload_token='dummy-upload-token')
    assert exc.value.status_code == 403
    assert Scope.GROUPS_READ in str(exc.value.detail)


# Tests the final outcome of `_resolve_user_with_scopes`, i.e. whether a request
# is allowed or rejected after applying authentication, authorization, and
# scope resolution logic (interaction with configs).
# Refer to the diagram in https://fairmat-nfdi.github.io/nomad-docs/explanation/auth.html#authentication-and-authorization


def test_resolve_user_anonymous_requires_auth(mockopen, monkeypatch):
    """Anonymous requests (no valid token) should be rejected
    when authentication is required (`require_authentication=True`).
    """
    runtime_config = load_test_config(
        {
            'auth': {
                'require_authentication': True,
                'unauthenticated_user_scopes': {'include': ['*:read']},
            }
        },
        None,
        mockopen,
        monkeypatch,
    )
    monkeypatch.setattr('nomad.app.v1.routers.auth.config', runtime_config)

    with pytest.raises(HTTPException) as exc_info:
        _resolve_user_with_scopes(
            required_scopes=set(),
            allow_anonymous=True,
        )

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == 'Authentication required.'
    assert exc_info.value.headers == {'WWW-Authenticate': 'Bearer'}


@pytest.mark.parametrize(
    ('required_scopes', 'should_raise'),
    [
        pytest.param({'uploads:read'}, False, id='required-scope-present'),
        pytest.param({'uploads:write'}, True, id='required-scope-missing'),
    ],
)
def test_resolve_user_anonymous_uses_unauthenticated_scopes(
    required_scopes,
    should_raise,
    mockopen,
    monkeypatch,
):
    """Anonymous requests should use `unauthenticated_user_scopes` when
    authentication is not required (`require_authentication=False`).
    """
    runtime_config = load_test_config(
        {
            'auth': {
                'require_authentication': False,
                'unauthenticated_user_scopes': {'include': ['uploads:read']},
            }
        },
        None,
        mockopen,
        monkeypatch,
    )
    monkeypatch.setattr('nomad.app.v1.routers.auth.config', runtime_config)

    if should_raise:
        with pytest.raises(HTTPException) as exc_info:
            _resolve_user_with_scopes(
                required_scopes=required_scopes,
                allow_anonymous=True,
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == f'Missing scopes: {sorted(required_scopes)}'

    else:
        user = _resolve_user_with_scopes(
            required_scopes=required_scopes,
            allow_anonymous=True,
            request=None,
            keycloak_token=None,
            personal_access_token=None,
            simple_token=None,
            upload_token=None,
        )

        assert user is None


@pytest.mark.parametrize(
    'authorized_users',
    [
        pytest.param(None, id='no-whitelist-configured'),
        pytest.param(['alice@example.com'], id='user-in-whitelist'),
    ],
)
def test_resolve_user_authenticated_authorized_uses_token_scopes(
    authorized_users,
    mockopen,
    monkeypatch,
):
    """Authenticated users should be allowed and use their token scopes when
    no whitelist is configured or when they are included in `authorized_users`.
    """

    runtime_config = load_test_config(
        {
            'auth': {
                'require_authentication': True,
                'authorized_users': authorized_users,
            }
        },
        None,
        mockopen,
        monkeypatch,
    )
    monkeypatch.setattr('nomad.app.v1.routers.auth.config', runtime_config)

    user = User(
        user_id='user-id',
        username='alice',
        email='alice@example.com',
    )
    pat = SimpleNamespace(
        user_id='user-id',
        scopes=['uploads:read'],
    )

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.pat_service.authenticate',
        lambda *args, **kwargs: pat,
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.datamodel.User.get',
        lambda *args, **kwargs: user,
    )

    resolved_user = _resolve_user_with_scopes(
        required_scopes={'uploads:read'},
        allow_anonymous=True,
        personal_access_token='fake-pat',
    )

    assert resolved_user is user


@pytest.mark.parametrize(
    ('reject_unauthorized_users', 'required_scopes', 'should_raise'),
    [
        pytest.param(
            True,
            {'uploads:read'},
            True,
            id='reject-unauthorized-user',
        ),
        pytest.param(
            False,
            {'uploads:read'},
            False,
            id='fallback-to-unauthorized-user-scopes',
        ),
        pytest.param(
            False,
            {'uploads:write'},
            True,
            id='fallback-scopes-missing-required-scope',
        ),
    ],
)
def test_resolve_user_authenticated_not_in_whitelist(
    reject_unauthorized_users,
    required_scopes,
    should_raise,
    mockopen,
    monkeypatch,
):
    """This tests cases where authorized_users configured and user
    NOT in that list.
    - if `reject_unauthorized_users=True`, they would be rejected
    - otherwise scopes defined in `unauthorized_user_scopes` would be used.
    """

    runtime_config = load_test_config(
        {
            'auth': {
                'require_authentication': True,
                'reject_unauthorized_users': reject_unauthorized_users,
                'authorized_users': ['alice@example.com'],
                'unauthorized_user_scopes': {'include': ['uploads:read']},
            }
        },
        None,
        mockopen,
        monkeypatch,
    )
    monkeypatch.setattr('nomad.app.v1.routers.auth.config', runtime_config)

    user = User(
        user_id='user-id',
        username='bob',
        email='bob@example.com',
    )
    pat = SimpleNamespace(user_id='user-id', scopes=['uploads:write'])

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.pat_service.authenticate',
        lambda *args, **kwargs: pat,
    )
    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.datamodel.User.get',
        lambda *args, **kwargs: user,
    )

    if should_raise:
        with pytest.raises(HTTPException) as exc_info:
            _resolve_user_with_scopes(
                required_scopes=required_scopes,
                allow_anonymous=True,
                personal_access_token='fake-pat',
            )

        expected_status = status.HTTP_403_FORBIDDEN
        assert exc_info.value.status_code == expected_status

        if reject_unauthorized_users:
            assert (
                exc_info.value.detail == 'You are not authorized to access this Oasis'
            )
        else:
            assert exc_info.value.detail == f'Missing scopes: {sorted(required_scopes)}'

    else:
        resolved_user = _resolve_user_with_scopes(
            required_scopes=required_scopes,
            allow_anonymous=True,
            personal_access_token='fake-pat',
        )

        assert resolved_user is user


# Tests for personal access token (PAT) endpoints


def test_create_pat_success(client, auth_headers, mongo_function):
    """Successful creation of a PAT."""
    payload = {
        'metadata': {
            'name': 'API Test Token',
            'scopes': ['uploads:read'],
            'description': 'Created via test',
        },
        'expires_in_days': 30,
    }

    headers = auth_headers['user1']
    response = client.post('auth/pats', json=payload, headers=headers)

    assert response.status_code == status.HTTP_201_CREATED

    data = response.json()
    assert 'raw_token' in data
    assert 'pat' in data
    assert data['pat']['name'] == 'API Test Token'
    assert data['pat']['description'] == 'Created via test'
    assert data['pat']['scopes'] == ['uploads:read']
    assert data['pat']['revoked'] is False

    # Ensure token digest are dropped (only raw token)
    assert 'token_digest' not in data['pat']


def test_create_pat_invalid_lifespan(client, auth_headers, mongo_function):
    """Test that the API rejects negative lifespans with a 400 Bad Request."""
    payload = {
        'metadata': {'name': 'Invalid Lifespan Token', 'scopes': DEFAULT_TEST_SCOPES},
        'expires_in_days': -5,
    }

    headers = auth_headers['user1']
    response = client.post('auth/pats', json=payload, headers=headers)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'already expired' in response.json()['detail']


def test_create_pat_requires_nonempty_scopes(client, auth_headers, mongo_function):
    """Test that the API rejects PAT creation without any scopes."""
    payload = {
        'metadata': {'name': 'Missing Scopes Token', 'scopes': []},
        'expires_in_days': 30,
    }

    headers = auth_headers['user1']
    response = client.post('auth/pats', json=payload, headers=headers)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'At least one scope must be selected' in response.json()['detail']


def test_create_pat_invalid_scope(client, auth_headers, mongo_function):
    """Test that the API rejects invalid scope with a 400 Bad Request."""
    payload = {
        'metadata': {'name': 'Invalid Scope Token', 'scopes': ['invalid-scope']},
        'expires_in_days': 30,
    }

    headers = auth_headers['user1']
    response = client.post('auth/pats', json=payload, headers=headers)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'Invalid scope' in response.json()['detail']


def test_get_pat_success(client, auth_headers, mongo_function):
    """Test retrieving a single PAT by ID."""
    headers = auth_headers['user1']

    # Create a token
    create_resp = client.post(
        'auth/pats',
        json={
            'metadata': {'name': 'Get Me', 'scopes': DEFAULT_TEST_SCOPES},
            'expires_in_days': 30,
        },
        headers=headers,
    )
    pat_id = create_resp.json()['pat']['id']

    # Retrieve it
    response = client.get(f'auth/pats/{pat_id}', headers=headers)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data['id'] == pat_id
    assert data['name'] == 'Get Me'


@pytest.mark.parametrize(
    'pat_id',
    [
        pytest.param('invalid-pat-id', id='invalid-format'),
        pytest.param(str(ObjectId()), id='valid-format-non-existent'),
    ],
)
def test_get_pat_non_existent_invalid(client, auth_headers, mongo_function, pat_id):
    """Test retrieving a PAT with an invalid format, and a valid but non-existent ID."""
    user1_headers = auth_headers['user1']

    response = client.get(f'auth/pats/{pat_id}', headers=user1_headers)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert 'Token not found or does not belong to the user' in response.json()['detail']


def test_get_pat_cross_user(client, auth_headers, mongo_function):
    """Retrieve a PAT belonging to another user."""
    user1_headers = auth_headers['user1']
    user2_headers = auth_headers['user2']

    # Create a token as User 1
    create_resp = client.post(
        'auth/pats',
        json={
            'metadata': {'name': 'User 1 Private Token', 'scopes': DEFAULT_TEST_SCOPES},
            'expires_in_days': 30,
        },
        headers=user1_headers,
    )
    assert create_resp.status_code == status.HTTP_201_CREATED
    pat_id = create_resp.json()['pat']['id']

    # Attempt to retrieve it as User 2
    response = client.get(f'auth/pats/{pat_id}', headers=user2_headers)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert 'Token not found or does not belong to the user' in response.json()['detail']


def assert_pat_list_response(
    response,
    *,
    expected_status: int = status.HTTP_200_OK,
    expected_total: int | None = None,
    expected_page: int | None = None,
    expected_page_size: int | None = None,
    expected_order_by: str | None = None,
) -> dict:
    """
    Assert the standard PAT list response envelope and pagination metadata.

    Returns:
        The decoded JSON payload for further item-specific assertions.
    """
    assert response.status_code == expected_status
    payload = response.json()

    assert set(payload) == set(PATQueryResponse.model_fields.keys())
    assert set(payload['query']) == set(PATQuery.model_fields.keys())
    assert isinstance(payload['data'], list)

    pagination = payload['pagination']
    assert set(pagination) >= {'total', 'page', 'page_size', 'order_by'}

    if expected_total is not None:
        assert pagination['total'] == expected_total
    if expected_page is not None:
        assert pagination['page'] == expected_page
    if expected_page_size is not None:
        assert pagination['page_size'] == expected_page_size
    if expected_order_by is not None:
        assert pagination['order_by'] == expected_order_by

    for token in payload['data']:
        assert 'token_digest' not in token

    return payload


def test_list_pat_success(client, auth_headers, mongo_function):
    """
    Test listing PATs for a user, ensuring no cross-user leakage,
    and verifying pagination works correctly.
    """
    headers_user1 = auth_headers['user1']
    headers_user2 = auth_headers['user2']

    # Create two tokens for User 1
    client.post(
        'auth/pats',
        json={
            'metadata': {'name': 'Token A', 'scopes': DEFAULT_TEST_SCOPES},
            'expires_in_days': 30,
        },
        headers=headers_user1,
    )
    client.post(
        'auth/pats',
        json={
            'metadata': {'name': 'Token B', 'scopes': DEFAULT_TEST_SCOPES},
            'expires_in_days': 30,
        },
        headers=headers_user1,
    )

    # Create one token for User 2
    client.post(
        'auth/pats',
        json={
            'metadata': {'name': 'User 2 Secret Token', 'scopes': DEFAULT_TEST_SCOPES},
            'expires_in_days': 30,
        },
        headers=headers_user2,
    )

    default_order_by = PATPagination.model_fields['order_by'].default

    # Test 1: Default Listing (No explicit pagination)
    response = client.get('auth/pats', headers=headers_user1)
    payload = assert_pat_list_response(
        response,
        expected_total=2,
        expected_page=1,
        expected_page_size=10,
        expected_order_by=default_order_by,
    )

    data = payload['data']
    # User 1 should only get 2 tokens back, not User 2's token
    assert len(data) == 2
    assert {token['name'] for token in data} == {'Token A', 'Token B'}

    # Test 2: Explicit Pagination (page_size=1 and sort alphabetically)
    # Page 1
    response_page1 = client.get(
        'auth/pats?page_size=1&page=1&order_by=name_asc',
        headers=headers_user1,
    )
    payload_p1 = assert_pat_list_response(
        response_page1,
        expected_total=2,
        expected_page=1,
        expected_page_size=1,
        expected_order_by='name_asc',
    )

    data_p1 = payload_p1['data']
    assert len(data_p1) == 1
    assert data_p1[0]['name'] == 'Token A'

    # Page 2
    response_page2 = client.get(
        'auth/pats?page_size=1&page=2&order_by=name_asc',
        headers=headers_user1,
    )
    payload_p2 = assert_pat_list_response(
        response_page2,
        expected_total=2,
        expected_page=2,
        expected_page_size=1,
        expected_order_by='name_asc',
    )

    data_p2 = payload_p2['data']
    assert len(data_p2) == 1
    assert data_p2[0]['name'] == 'Token B'

    # Page 3 (Out of bounds)
    response_page3 = client.get(
        'auth/pats?page_size=2&page=3&order_by=name_asc',
        headers=headers_user1,
    )
    assert response_page3.status_code == status.HTTP_400_BAD_REQUEST
    assert 'Page out of range' in response_page3.text


def test_rotate_pat_success(client, auth_headers, mongo_function):
    """Test rotating an existing PAT."""
    headers = auth_headers['user1']

    # Create initial token
    create_resp = client.post(
        'auth/pats',
        json={
            'metadata': {'name': 'Rotate Me', 'scopes': DEFAULT_TEST_SCOPES},
            'expires_in_days': 30,
        },
        headers=headers,
    )
    old_pat_id = create_resp.json()['pat']['id']
    old_raw_token = create_resp.json()['raw_token']

    # Rotate it
    response = client.post(f'auth/pats/{old_pat_id}/rotate', headers=headers)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    new_pat_id = data['pat']['id']
    new_raw_token = data['raw_token']

    # Verify
    assert new_pat_id != old_pat_id
    assert new_raw_token != old_raw_token
    assert data['pat']['name'] == 'Rotate Me'

    # Ensure old token is revoked in DB
    old_pat_req = client.get(f'auth/pats/{old_pat_id}', headers=headers)
    assert old_pat_req.json()['revoked'] is True


def test_rotate_pat_cross_user(client, auth_headers, mongo_function):
    """Test that a user cannot rotate another user's PAT."""
    user1_headers = auth_headers['user1']
    user2_headers = auth_headers['user2']

    # Create a token as User 1
    create_resp = client.post(
        'auth/pats',
        json={
            'metadata': {'name': 'User 1 Token', 'scopes': DEFAULT_TEST_SCOPES},
            'expires_in_days': 30,
        },
        headers=user1_headers,
    )
    pat_id = create_resp.json()['pat']['id']

    # Attempt to rotate it as User 2
    response = client.post(f'auth/pats/{pat_id}/rotate', headers=user2_headers)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert 'Token not found or does not belong to the user' in response.json()['detail']


def test_rotate_pat_revoked_token(client, auth_headers, mongo_function):
    """Test that a revoked PAT cannot be rotated."""
    headers = auth_headers['user1']

    # Create initial token
    create_resp = client.post(
        'auth/pats',
        json={
            'metadata': {'name': 'Revoked Token', 'scopes': DEFAULT_TEST_SCOPES},
            'expires_in_days': 30,
        },
        headers=headers,
    )
    pat_id = create_resp.json()['pat']['id']

    # Revoke
    pat = PAT.objects(id=ObjectId(pat_id)).first()
    assert pat is not None
    pat.revoked = True
    pat.save()

    # Attempt rotation
    response = client.post(f'auth/pats/{pat_id}/rotate', headers=headers)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'Cannot rotate an expired' in response.json()['detail']


def test_rotate_pat_expired_token(client, auth_headers, mongo_function):
    """Test that an expired PAT cannot be rotated."""
    headers = auth_headers['user1']

    # Create initial token
    create_resp = client.post(
        'auth/pats',
        json={
            'metadata': {'name': 'Expired Token', 'scopes': DEFAULT_TEST_SCOPES},
            'expires_in_days': 30,
        },
        headers=headers,
    )
    pat_id = create_resp.json()['pat']['id']

    # Expire
    pat = PAT.objects(id=ObjectId(pat_id)).first()
    assert pat is not None
    pat.expired_at = now() - datetime.timedelta(seconds=1)
    pat.save()

    # Attempt rotation
    response = client.post(f'auth/pats/{pat_id}/rotate', headers=headers)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'Cannot rotate an expired' in response.json()['detail']


@pytest.mark.parametrize(
    'pat_id',
    [
        pytest.param('invalid-pat-id', id='invalid-format'),
        pytest.param(str(ObjectId()), id='valid-format-non-existent'),
    ],
)
def test_rotate_pat_non_existent_invalid(client, auth_headers, mongo_function, pat_id):
    """Test rotating a PAT with an invalid format, and a valid but non-existent ID."""
    user1_headers = auth_headers['user1']

    response = client.post(f'auth/pats/{pat_id}/rotate', headers=user1_headers)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert 'Token not found or does not belong to the user' in response.json()['detail']


def test_revoke_pat_success(client, auth_headers, mongo_function):
    """Test revoking a PAT."""
    headers = auth_headers['user1']

    # Create a token
    create_resp = client.post(
        'auth/pats',
        json={
            'metadata': {'name': 'Revoke Me', 'scopes': DEFAULT_TEST_SCOPES},
            'expires_in_days': 30,
        },
        headers=headers,
    )
    pat_id = create_resp.json()['pat']['id']

    # Revoke it
    response = client.delete(f'auth/pats/{pat_id}', headers=headers)
    assert response.status_code == status.HTTP_204_NO_CONTENT

    # Verify it is revoked
    check_resp = client.get(f'auth/pats/{pat_id}', headers=headers)
    assert check_resp.json()['revoked'] is True

    # Second Revoke: Should also succeed (idempotent)
    response_2 = client.delete(f'auth/pats/{pat_id}', headers=headers)
    assert response_2.status_code == status.HTTP_204_NO_CONTENT


def test_revoke_pat_cross_user(client, auth_headers, mongo_function):
    """Test that a user cannot revoke another user's PAT."""
    user1_headers = auth_headers['user1']
    user2_headers = auth_headers['user2']

    # Create a token as User 1
    create_resp = client.post(
        'auth/pats',
        json={
            'metadata': {'name': 'User 1 Token', 'scopes': DEFAULT_TEST_SCOPES},
            'expires_in_days': 30,
        },
        headers=user1_headers,
    )
    pat_id = create_resp.json()['pat']['id']

    # Attempt to revoke it as User 2
    response = client.delete(f'auth/pats/{pat_id}', headers=user2_headers)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert 'Token not found or does not belong to the user' in response.json()['detail']


@pytest.mark.parametrize(
    'pat_id',
    [
        pytest.param('invalid-pat-id', id='invalid-format'),
        pytest.param(str(ObjectId()), id='valid-format-non-existent'),
    ],
)
def test_revoke_pat_non_existent_invalid(client, auth_headers, mongo_function, pat_id):
    """Test revoking a PAT with an invalid format, and a valid but non-existent ID."""
    user1_headers = auth_headers['user1']

    response = client.delete(f'auth/pats/{pat_id}', headers=user1_headers)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert 'Token not found or does not belong to the user' in response.json()['detail']


@pytest.mark.parametrize(
    'method, endpoint, payload',
    [
        ('POST', '/pats', {'metadata': {'name': 'Unauth Test'}, 'expires_in_days': 30}),
        ('POST', '/pats/dummy-pat-id/rotate', None),
        ('GET', '/pats', None),
        ('GET', '/pats/dummy-pat-id', None),
        ('DELETE', '/pats/dummy-pat-id', None),
    ],
    ids=['create_pat', 'rotate_pat', 'list_pat', 'get_pat', 'revoke_pat'],
)
def test_pat_endpoints_unauthenticated(
    client,
    method,
    endpoint,
    payload,
):
    """
    Test that all PAT endpoints correctly reject requests
    that lack authentication.
    """
    url = f'auth{endpoint}'

    # NOT including any headers here
    request_kwargs = {}
    if payload:
        request_kwargs['json'] = payload

    response = client.request(method, url, **request_kwargs)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert 'Authentication required.' in response.json()['detail']


@pytest.mark.parametrize(
    'method, endpoint, payload',
    [
        ('POST', '/pats', {'metadata': {'name': 'Scope Test'}, 'expires_in_days': 30}),
        ('POST', '/pats/dummy-pat-id/rotate', None),
        ('GET', '/pats', None),
        ('GET', '/pats/dummy-pat-id', None),
        ('DELETE', '/pats/dummy-pat-id', None),
    ],
    ids=['create_pat', 'rotate_pat', 'list_pat', 'get_pat', 'revoke_pat'],
)
def test_pat_endpoints_missing_scopes(
    client,
    auth_headers,
    monkeypatch,
    allowed_user,
    patch_user_get,
    method,
    endpoint,
    payload,
):
    """
    Test that all PAT endpoints correctly reject authenticated users
    who lack TOKEN scopes.
    """
    patch_user_get(allowed_user)

    # Simulate missing scope
    restricted_auth = AuthResult(allowed_user, set())

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_keycloak_token',
        lambda _token: restricted_auth,
    )

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.jwt.decode',
        lambda *args, **kwargs: {'sub': allowed_user.user_id, 'exp': 3600},
    )

    url = f'auth{endpoint}'
    request_kwargs = {'headers': auth_headers['user1']}
    if payload:
        request_kwargs['json'] = payload

    response = client.request(method, url, **request_kwargs)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert 'Missing scopes' in response.json()['detail']
