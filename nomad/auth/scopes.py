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

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class Scope(str, Enum):
    """
    Backend authorization scopes.

    Each scope is represented as a colon-separated string of the form:
        `resource:action`

    where:
    - `resource` identifies a protected backend domain or API surface
      (e.g. `datasets`, `uploads`, `users`).
    - `action` identifies the permitted operation on that resource
      (e.g. `read`, `write`, `delete`, `run`).
    """

    def __new__(cls, value: str, description: str):
        """Unpack value and description from tuple."""
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.description = description
        return obj

    # actions
    ACTIONS_READ = ('actions:read', 'Read action definitions and action metadata.')
    ACTIONS_RUN = ('actions:run', 'Execute actions.')

    # apps
    APPS_READ = ('apps:read', 'Read app definitions and app content.')

    # Personal access tokens (PAT) and custom NOMAD tokens
    TOKENS_CREATE = (
        'tokens:create',
        'Create personal access tokens and other NOMAD tokens.',
    )
    TOKENS_READ = ('tokens:read', 'Read personal access tokens and other NOMAD tokens.')
    TOKENS_DELETE = (
        'tokens:delete',
        'Delete or revoke personal access tokens and other NOMAD tokens.',
    )

    # datasets
    DATASETS_READ = ('datasets:read', 'Read datasets.')
    DATASETS_WRITE = ('datasets:write', 'Create or update datasets.')
    DATASETS_DELETE = ('datasets:delete', 'Delete datasets.')
    DATASETS_ASSIGN_DOI = ('datasets:assign_doi', 'Assign DOIs to datasets.')

    # entries
    ENTRIES_READ = ('entries:read', 'Read entries.')
    ENTRIES_WRITE = ('entries:write', 'Create or update entries.')

    # graph
    GRAPH_READ = ('graph:read', 'Read graph API data.')

    # groups
    GROUPS_READ = ('groups:read', 'Read groups.')
    GROUPS_WRITE = ('groups:write', 'Create or update groups.')
    GROUPS_DELETE = ('groups:delete', 'Delete groups.')

    # info
    INFO_READ = ('info:read', 'Read instance and service information.')

    # materials
    MATERIALS_READ = ('materials:read', 'Read materials data.')

    # metainfo
    METAINFO_READ = ('metainfo:read', 'Read metainfo definitions.')

    # north
    NORTH_READ = ('north:read', 'Read NOMAD Remote Tools Hub resources.')
    NORTH_RUN = ('north:run', 'Run NOMAD Remote Tools Hub tools or jobs.')

    # schemas
    SCHEMAS_READ = ('schemas:read', 'Read schema definitions.')

    # suggestions
    SUGGESTIONS_READ = ('suggestions:read', 'Read suggestion data.')

    # systems
    SYSTEMS_READ = ('systems:read', 'Read systems data.')

    # uploads
    UPLOADS_READ = ('uploads:read', 'Read uploads.')
    UPLOADS_WRITE = ('uploads:write', 'Create or update uploads and upload contents.')
    UPLOADS_PUBLISH = ('uploads:publish', 'Publish uploads.')
    UPLOADS_PROCESS = ('uploads:process', 'Process uploads.')
    UPLOADS_ASSIGN_DOI = ('uploads:assign_doi', 'Assign DOIs to uploads.')
    UPLOADS_EXPORT = ('uploads:export', 'Export uploads.')
    UPLOADS_IMPORT = ('uploads:import', 'Import uploads.')

    # users
    USERS_READ = ('users:read', 'Read user information.')
    USERS_INVITE = ('users:invite', 'Invite users.')

    # external apps
    EXTERNAL_OPTIMADE_READ = (
        'external_optimade:read',
        'Access the external OPTIMADE API.',
    )
    EXTERNAL_DCAT_READ = ('external_dcat:read', 'Access the external DCAT API.')
    EXTERNAL_H5GROVE_READ = (
        'external_h5grove:read',
        'Access the external H5Grove API.',
    )

    description: str

    @property
    def resource(self) -> str:
        """Return the resource segment of the scope string."""
        return self.value.split(':', maxsplit=1)[0]

    @property
    def action(self) -> str:
        """Return the action segment of the scope string."""
        return self.value.rsplit(':', maxsplit=1)[-1]

    @classmethod
    def all_values(cls) -> set[str]:
        """Return all concrete scope strings."""
        return {scope.value for scope in cls}


_LEGACY_SCOPE_ALIASES = {
    'uploads_bundle:read': Scope.UPLOADS_EXPORT.value,
    'uploads_bundle:write': Scope.UPLOADS_IMPORT.value,
}


def resolve_scope_aliases(scopes: Iterable[str]) -> set[str]:
    """Map deprecated scope values to their current equivalents."""
    return {_LEGACY_SCOPE_ALIASES.get(scope, scope) for scope in scopes}


def _resolve_scopes(
    scopes: str | Iterable[str],
) -> set[str]:
    """Resolve and validate configured scopes, supporting '*' as wildcards.

    Supported wildcard forms:
      - '*:*'         -> all known scopes
      - 'resource:*'  -> all actions for a resource
      - '*:action'    -> all resources for an action

    Unsupported:
      - partial wildcards like 'upl*', '*load'
      - glob syntax beyond '*'

    Args:
        scopes: Scope strings from configuration.
    """
    if isinstance(scopes, str):
        scopes = {scopes}
    else:
        scopes = set(scopes)
    known_scopes: set[str] = Scope.all_values()
    resolved_scopes: set[str] = set()

    unknown_concrete: set[str] = set()
    unmatched_wildcards: set[str] = set()

    for raw in scopes:
        scope = raw.strip()
        if not scope:
            continue
        scope = _LEGACY_SCOPE_ALIASES.get(scope, scope)

        scope_parts: list[str] = scope.split(':')

        if len(scope_parts) != 2:
            raise ValueError(
                f"Illegal scope {scope}; expected format is 'resource:action'."
            )

        resource, action = scope_parts

        # Reject partial wildcard like "up*:read" or "uploads:r*".
        for seg in (resource, action):
            if seg != '*' and '*' in seg:
                raise ValueError(f'Partial wildcard in {scope} is not allowed.')

        # Concrete scope
        if '*' not in scope:
            if scope in known_scopes:
                resolved_scopes.add(scope)
            else:
                unknown_concrete.add(scope)
            continue

        # Wildcards
        if scope == '*:*':
            resolved_scopes |= known_scopes
            continue

        if resource == '*' and action != '*':  # *:action
            matches = {s for s in known_scopes if s.endswith(f':{action}')}
        else:  # resource:*
            matches = {s for s in known_scopes if s.startswith(f'{resource}:')}

        if matches:
            resolved_scopes |= matches
        else:
            unmatched_wildcards.add(scope)
        continue

    if unknown_concrete or unmatched_wildcards:
        err_msg: list[str] = ['Invalid scope configuration.']
        if unknown_concrete:
            err_msg.append(f'Unknown concrete scopes: {sorted(unknown_concrete)}')
        if unmatched_wildcards:
            err_msg.append(f'Wildcards matched nothing: {sorted(unmatched_wildcards)}')
        raise ValueError(' '.join(err_msg))

    return resolved_scopes
