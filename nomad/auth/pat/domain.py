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
import secrets
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum

from nomad.auth.scopes import Scope
from nomad.common import now

PAT_PREFIX = 'nomad_pat_'
_PAT_FORBIDDEN_SCOPES = frozenset(
    {
        Scope.TOKENS_CREATE.value,
        Scope.TOKENS_DELETE.value,
        Scope.TOKENS_READ.value,
    }
)


class PATState(str, Enum):
    ACTIVE = 'active'
    REVOKED = 'revoked'
    EXPIRED = 'expired'


@dataclass(frozen=True)
class PATRecord:
    """Persistence-independent representation of a personal access token."""

    id: str
    user_id: str
    name: str
    description: str | None
    scopes: tuple[str, ...]
    token_digest: str | None
    created_at: datetime.datetime
    expired_at: datetime.datetime | None = (
        None  # None indicates an infinite token (no expiration)
    )
    revoked: bool = False
    revoked_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None
    last_used_at: datetime.datetime | None = None

    def state_at(self, time: datetime.datetime) -> PATState:
        if self.revoked:
            return PATState.REVOKED
        if self.is_expired_at(time):
            return PATState.EXPIRED
        return PATState.ACTIVE

    def is_expired_at(self, time: datetime.datetime) -> bool:
        if self.expired_at is None:
            return False
        comparable_now = time
        if self.expired_at.tzinfo is None and comparable_now.tzinfo is not None:
            comparable_now = comparable_now.replace(tzinfo=None)
        return self.expired_at < comparable_now

    def is_active_at(self, time: datetime.datetime) -> bool:
        return self.state_at(time) is PATState.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.is_active_at(now())

    @property
    def is_expired(self) -> bool:
        return self.is_expired_at(now())

    def rotation_lifetime(self) -> int | None:
        """Calculates the original token lifetime in days to preserve it during rotation.

        Returns None for infinite (non-expiring) tokens.
        """
        if self.expired_at is None:
            return None
        return (self.expired_at - self.created_at).days


@dataclass(frozen=True)
class PATSecret:
    """New token material. Only ``digest`` may cross the persistence boundary."""

    raw: str
    digest: str

    @classmethod
    def generate(cls) -> PATSecret:
        raw = f'{PAT_PREFIX}{secrets.token_urlsafe(32)}'
        return cls(raw=raw, digest=hash_token(raw))


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def validate_creation(
    *,
    scopes: Collection[str],
    expires_in_days: int | None,
    valid_scopes: Collection[str],
    max_lifetime_days: int | None,
) -> None:
    if expires_in_days is not None and expires_in_days <= 0:
        raise ValueError('Cannot create an already expired token.')
    if max_lifetime_days is not None:
        if expires_in_days is None:
            raise ValueError(
                f'Infinite tokens are disabled. Max lifetime is {max_lifetime_days} days.'
            )
        if expires_in_days > max_lifetime_days:
            raise ValueError(
                f'Requested lifetime ({expires_in_days} days) exceeds the maximum allowed ({max_lifetime_days} days).'
            )
    if not scopes:
        raise ValueError('At least one scope must be selected.')
    if invalid_scopes := set(scopes) - set(valid_scopes):
        raise ValueError(f'Invalid scopes: {invalid_scopes}')
    if set(scopes) & _PAT_FORBIDDEN_SCOPES:
        raise ValueError('Personal access tokens are not allowed to operate on PATs.')


def resolve_prune_cutoff(
    *,
    time: datetime.datetime,
    inactive_for: datetime.timedelta | None,
    inactive_before: datetime.datetime | None,
) -> datetime.datetime:
    if inactive_before is not None and inactive_for is not None:
        raise ValueError('inactive_before and inactive_for are mutually exclusive.')
    if inactive_before is not None:
        return inactive_before
    if inactive_for is None:
        raise ValueError('Either inactive_before or inactive_for must be provided.')
    if inactive_for.total_seconds() < 0:
        raise ValueError('inactive_for must be non-negative.')
    return time - inactive_for
