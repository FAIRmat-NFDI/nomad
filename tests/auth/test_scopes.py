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


import pytest

from nomad.auth.scopes import Scope, _resolve_scopes, resolve_scope_aliases


def test_scope_metadata():
    # Check backwards compatibility as StrEnum
    assert Scope.DATASETS_READ == 'datasets:read'
    assert isinstance(Scope.DATASETS_READ, str)

    assert Scope.DATASETS_READ.value == 'datasets:read'
    assert Scope.DATASETS_READ.name == 'DATASETS_READ'
    assert Scope.DATASETS_READ.resource == 'datasets'
    assert Scope.DATASETS_READ.action == 'read'
    assert Scope.DATASETS_READ.description == 'Read datasets.'


@pytest.mark.parametrize(
    'scopes, expected',
    [
        pytest.param(
            {'uploads:read', 'datasets:write', 'info:read'},
            {'uploads:read', 'datasets:write', 'info:read'},
            id='no-wildcard',
        ),
        pytest.param(
            ['  uploads:read  ', '', '   ', '\n', 'info:read\t'],
            {'uploads:read', 'info:read'},
            id='whitespace-handling',
        ),
        pytest.param(
            {'uploads_bundle:read', 'uploads_bundle:write'},
            {'uploads:export', 'uploads:import'},
            id='legacy-upload-bundle-aliases',
        ),
        pytest.param(
            {'*:*'},
            Scope.all_values(),
            id='wildcard-all',
        ),
        pytest.param(
            {'uploads:*'},
            {s for s in Scope.all_values() if s.startswith('uploads:')},
            id='wildcard-resource',
        ),
        pytest.param(
            {'*:read'},
            {s for s in Scope.all_values() if s.endswith(':read')},
            id='wildcard-action',
        ),
        pytest.param(
            {'uploads:*', 'uploads:read', '*:read'},
            {s for s in Scope.all_values() if s.startswith('uploads:')}
            | {s for s in Scope.all_values() if s.endswith(':read')},
            id='wildcards-deduplicate',
        ),
    ],
)
def test_resolve_scopes_valid(scopes, expected):
    assert _resolve_scopes(scopes) == expected


def test_resolve_scope_aliases_for_existing_tokens():
    assert resolve_scope_aliases(
        {'uploads_bundle:read', 'uploads_bundle:write', 'datasets:read'}
    ) == {'uploads:export', 'uploads:import', 'datasets:read'}


@pytest.mark.parametrize(
    'scopes, match',
    [
        pytest.param(
            {'uploads:unknown_action'},
            'Unknown concrete scopes',
            id='unknown-action',
        ),
        pytest.param(
            {'unknown_resource:read'},
            'Unknown concrete scopes',
            id='unknown-resource',
        ),
        pytest.param(
            {'uploads'},
            'Illegal scope uploads',
            id='illegal-format-no-colon',
        ),
        pytest.param(
            {'uploads:read:extra'},
            'Illegal scope uploads:read:extra',
            id='illegal-format-extra-colon',
        ),
        pytest.param(
            {'*:*:*'},
            r'Illegal scope \*:\*:\*',
            id='illegal-format-triple-wildcard',
        ),
        pytest.param(
            {'*'},
            r'Illegal scope \*',
            id='illegal-format-single-wildcard',
        ),
        pytest.param(
            {'up*:read'},
            'Partial wildcard',
            id='partial-wildcard-resource-suffix',
        ),
        pytest.param(
            {'*load:read'},
            'Partial wildcard',
            id='partial-wildcard-resource-prefix',
        ),
        pytest.param(
            {'uploads:r*'},
            'Partial wildcard',
            id='partial-wildcard-action-suffix',
        ),
        pytest.param(
            {'uploads:*d'},
            'Partial wildcard',
            id='partial-wildcard-action-prefix',
        ),
        pytest.param(
            {'not_a_resource:*'},
            'Wildcards matched nothing',
            id='unmatched-wildcard-resource',
        ),
        pytest.param(
            {'*:not_an_action'},
            'Wildcards matched nothing',
            id='unmatched-wildcard-action',
        ),
    ],
)
def test_resolve_scopes_raises(scopes, match):
    with pytest.raises(ValueError, match=match):
        _resolve_scopes(scopes)
