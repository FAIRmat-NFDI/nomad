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


from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

from nomad.app.v1.main import app as api_v1_app
from nomad.app.v1.models.models import User
from nomad.auth.scopes import Scope, _resolve_scopes
from nomad.auth.tokens import AuthResult

# Tests for `/alive`, `/-/health` and static files app (`/docs` and `/gui`)


def test_alive_health(client):
    assert client.get('/alive').status_code == 200
    assert client.get('/-/health').status_code == 200


@pytest.mark.parametrize('path', ['env.js', 'artifacts.js'])
def test_gui(client, path, monkeypatch):
    monkeypatch.setattr('nomad.app.main.GuiFiles.gui_env_data', 'env.js')
    monkeypatch.setattr('nomad.app.main.GuiFiles.gui_data_etag', 'etag')

    with NamedTemporaryFile(delete=True) as tmp:
        monkeypatch.setattr('nomad.app.main.GuiFiles.gui_artifacts_path', tmp.name)

        rv = client.get(f'/gui/{path}')
        assert rv.status_code == 200
        if path == 'env.js':
            assert rv.text == path
        else:
            # empty file
            assert rv.text == ''
        assert rv.headers.get('Etag') == '"etag"'

        rv = client.get(f'/gui/{path}', headers={'if-none-match': 'etag'})
        assert rv.status_code == 304

        rv = client.get(f'/gui/{path}', headers={'if-none-match': 'W/"etag"'})
        assert rv.status_code == 304

        rv = client.get(f'/gui/{path}', headers={'if-none-match': 'different-etag'})
        assert rv.status_code == 200


# Test scopes enforcing


@pytest.fixture
def allowed_user() -> User:
    return User(user_id='123', email='test@example.com', username='tester')


@pytest.fixture
def patch_user_get(monkeypatch):
    """
    Patch nomad.app.v1.routers.auth.datamodel.User.get and User.a_get.
    """

    def _apply(user: User | None) -> None:
        monkeypatch.setattr(
            'nomad.app.v1.routers.auth.datamodel.User.get',
            lambda *args, **kwargs: user,
        )

        async def a_get(*args, **kwargs):
            return user

        monkeypatch.setattr('nomad.app.v1.routers.auth.datamodel.User.a_get', a_get)

    return _apply


@pytest.fixture
def patch_keycloak_result(monkeypatch, allowed_user):
    """
    Patch keycloak token resolution to return an AuthResult with chosen scopes.
    """

    def _apply(scopes: set[str]) -> None:
        monkeypatch.setattr(
            'nomad.app.v1.routers.auth.get_user_from_keycloak_token',
            lambda _token: AuthResult(allowed_user, _resolve_scopes(scopes)),
        )

    return _apply


@pytest.fixture
def force_keycloak_path(monkeypatch):
    """
    Ensure the resolver does not authenticate via simple token first.

    Both simple_token and keycloak_token can read from the same Authorization header.
    The resolver checks simple-token first, so we force that branch to return None.
    """

    async def get_user_from_simple_token(_token):
        return None

    monkeypatch.setattr(
        'nomad.app.v1.routers.auth.get_user_from_simple_token',
        get_user_from_simple_token,
    )


def test_anonymous_allowed(client, monkeypatch):
    monkeypatch.setattr(
        'nomad.config.config.auth.unauthenticated_user_scopes',
        {'include': ['*:read']},
    )

    response = client.get('/api/v1/apps/entry-points')
    assert response.status_code == 200

    monkeypatch.setattr(
        'nomad.config.config.auth.unauthenticated_user_scopes', {'include': []}
    )

    response = client.get('/api/v1/apps/entry-points')
    assert response.status_code == 403
    assert 'Missing scopes' in response.json()['detail']


def test_anonymous_not_allowed(client, monkeypatch):
    monkeypatch.setattr(
        'nomad.config.config.auth.unauthenticated_user_scopes', {'include': {'*:*'}}
    )

    response = client.get('/api/v1/auth/signature_token')
    assert response.status_code == 401
    assert response.json()['detail'] == 'Authentication required.'


def test_authenticated_missing_scope(
    client,
    auth_headers,
    allowed_user,
    patch_user_get,
    patch_keycloak_result,
    force_keycloak_path,
):
    patch_user_get(allowed_user)

    patch_keycloak_result({Scope.UPLOADS_READ})

    response = client.get('/api/v1/apps/entry-points', headers=auth_headers['user1'])
    assert response.status_code == 403
    detail = response.json()['detail']
    assert 'Missing scopes' in detail
    assert Scope.APPS_READ in detail


def test_authenticated_success(
    client,
    auth_headers,
    allowed_user,
    patch_user_get,
    patch_keycloak_result,
    force_keycloak_path,
):
    patch_user_get(allowed_user)
    patch_keycloak_result({Scope.APPS_READ})

    response = client.get('/api/v1/apps/entry-points', headers=auth_headers['user1'])
    assert response.status_code == 200


def test_external_app_optimade(
    client,
    auth_headers,
    allowed_user,
    patch_user_get,
    patch_keycloak_result,
    force_keycloak_path,
):
    patch_user_get(allowed_user)

    patch_keycloak_result({Scope.EXTERNAL_OPTIMADE_READ})
    response = client.get('/optimade/info', headers=auth_headers['user1'])

    assert response.status_code == 200

    patch_keycloak_result(set())
    response = client.get('/optimade/info', headers=auth_headers['user1'])

    assert response.status_code == 403
    assert 'Missing scopes' in response.json()['detail']


def test_external_app_h5grove(
    client,
    auth_headers,
    allowed_user,
    patch_user_get,
    patch_keycloak_result,
    force_keycloak_path,
    example_data,
):
    patch_user_get(allowed_user)

    patch_keycloak_result({Scope.EXTERNAL_H5GROVE_READ, Scope.UPLOADS_READ})
    url = '/h5grove/?upload_id=id_published&file=id_published_1&path=/&source=archive'

    response = client.get(url, headers=auth_headers['user1'])
    assert response.status_code == 200

    patch_keycloak_result(set())
    response = client.get(url, headers=auth_headers['user1'])

    assert response.status_code == 403
    assert 'Missing scopes' in response.json()['detail']


def test_external_app_dcat(
    client,
    auth_headers,
    allowed_user,
    patch_user_get,
    patch_keycloak_result,
    force_keycloak_path,
):
    patch_user_get(allowed_user)

    patch_keycloak_result(set())
    response = client.get('/dcat/catalog/?format=turtle', headers=auth_headers['user1'])

    assert response.status_code == 403
    assert 'Missing scopes' in response.json()['detail']

    # TODO: here I didn't manage to do a "success" case test because
    # dcat doesn't seem to have any lightweight endpoint and would
    # always need some data (see `test_dcat.py`)


# Ensure auth dependency is injected correctly to all endpoints


def test_user_dependency_on_all_endpoints():
    from nomad.app.main import app
    from nomad.config import config

    ignored_mounts = {'/optimade', '/dcat', '/h5grove'}

    whitelisted_endpoints = {
        '/alive',
        '/-/health',
        '/metrics',
        f'{config.services.api_base_path}/metrics',
    }
    expected_unprotected = {'/api/v1/auth/token'}

    missing_auth_routes = []
    unexpected_auth_routes = []

    def inspect_routes(routes, prefix=''):
        for route in routes:
            if isinstance(route, Mount):
                if route.path in ignored_mounts:
                    continue
                if hasattr(route.app, 'routes'):
                    inspect_routes(route.app.routes, prefix + route.path)

            elif isinstance(route, APIRoute):
                full_path = prefix + route.path

                if full_path in whitelisted_endpoints:
                    continue

                # Introspect the dependency tree
                has_auth = False
                if hasattr(route, 'dependant'):
                    for dep in route.dependant.dependencies:
                        callable_name = getattr(dep.call, '__name__', '')
                        if callable_name == 'current_user':
                            has_auth = True
                            break

                method = list(route.methods)[0] if route.methods else 'UNKNOWN'

                if full_path in expected_unprotected:
                    if has_auth:
                        unexpected_auth_routes.append(f'{method} {full_path}')
                else:
                    if not has_auth:
                        missing_auth_routes.append(f'{method} {full_path}')

    inspect_routes(app.routes)

    assert not missing_auth_routes, (
        'The following endpoints are missing the auth dependency:\n'
        + '\n'.join(missing_auth_routes)
    )
    assert not unexpected_auth_routes, (
        "The following endpoints require auth but shouldn't:\n"
        + '\n'.join(unexpected_auth_routes)
    )


# Tests for mount_with_trailing_slash_redirect


@pytest.fixture
def api_v1_client():
    return TestClient(api_v1_app, follow_redirects=False)


@pytest.mark.parametrize(
    ('method', 'legacy_path', 'canonical_path'),
    [
        pytest.param('GET', '/datasets/', '/datasets', id='datasets-get'),
        pytest.param('POST', '/datasets/', '/datasets', id='datasets-post'),
        pytest.param(
            'POST', '/federation/logs/', '/federation/logs', id='federation-logs'
        ),
        pytest.param('GET', '/north/', '/north', id='north'),
    ],
)
def test_api_v1_trailing_slash_redirects_to_canonical_path(
    api_v1_client, method, legacy_path, canonical_path
):
    response = api_v1_client.request(method, legacy_path)

    assert response.status_code == 307
    assert urlparse(response.headers['location']).path == canonical_path


@pytest.fixture
def plugin_client():
    """
    Build a minimal parent app with a sub-app mounted via
    mount_with_trailing_slash_redirect, and return a TestClient
    configured not to follow redirects automatically.
    """
    from nomad.app.main import mount_with_trailing_slash_redirect

    parent = FastAPI()
    sub = FastAPI()

    @sub.get('/')
    async def sub_root():
        return {'ok': True}

    mount_with_trailing_slash_redirect(parent, '/my-plugin', sub)
    return TestClient(parent, follow_redirects=False)


def test_api_plugin_no_slash_redirects(plugin_client):
    """GET /my-plugin (no trailing slash) must return 307 → /my-plugin/."""
    response = plugin_client.get('/my-plugin')
    assert response.status_code == 307
    assert response.headers['location'] == '/my-plugin/'


def test_api_plugin_no_slash_preserves_query_string(plugin_client):
    """GET /my-plugin?foo=bar must redirect to /my-plugin/?foo=bar."""
    response = plugin_client.get('/my-plugin?foo=bar')
    assert response.status_code == 307
    assert response.headers['location'] == '/my-plugin/?foo=bar'


def test_api_plugin_with_slash_serves(plugin_client):
    """GET /my-plugin/ (with trailing slash) must return 200 directly."""
    response = plugin_client.get('/my-plugin/', follow_redirects=True)
    assert response.status_code == 200
