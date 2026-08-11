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
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from nomad.auth.scopes import Scope
from nomad.common import now
from nomad.config import config

from .domain import (
    PAT_PREFIX,
    PATRecord,
    PATSecret,
    hash_token,
    resolve_prune_cutoff,
    validate_creation,
)
from .ports import PATCreationData, PATQuerySpec, PATRepository, PATSortOrder


class PATCreationResult(NamedTuple):
    """A saved PAT and its raw secret, which is returned only on creation."""

    pat: PATRecord
    raw_token: str


class PATCreationSpec(BaseModel):
    """User-controlled PAT attributes used during creation and rotation."""

    name: str
    scopes: list[str]
    description: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PATQuery(BaseModel):
    search: str | None = Field(
        None, description='Search by token name (case-insensitive)'
    )
    revoked: bool | None = Field(
        None, description='Filter by explicitly revoked status'
    )
    state: Literal['active', 'inactive'] | None = Field(
        None, description='Filter by active/inactive state'
    )
    created_after: datetime.datetime | None = None
    created_before: datetime.datetime | None = None
    last_used_after: datetime.datetime | None = None
    last_used_before: datetime.datetime | None = None
    expires_after: datetime.datetime | None = None
    expires_before: datetime.datetime | None = None


class PATQueryResult(BaseModel):
    data: list[PATRecord]
    total: int = Field(ge=0)

    model_config = ConfigDict(arbitrary_types_allowed=True)


@dataclass(frozen=True)
class PATPruneResult:
    matched: int
    deleted: int
    expired_matched: int
    revoked_matched: int
    cutoff: datetime.datetime
    dry_run: bool
    user_id: str | None


class PATService:
    """Application service coordinating Personal Access Token (PAT) use cases."""

    def __init__(
        self,
        repository: PATRepository,
        clock: Callable[[], datetime.datetime] = now,
    ):
        self._repository = repository
        self._clock = clock

    def create(
        self,
        *,
        user_id: str,
        metadata: PATCreationSpec,
        expires_in_days: int | None,
    ) -> PATCreationResult:
        validate_creation(
            scopes=metadata.scopes,
            expires_in_days=expires_in_days,
            valid_scopes=Scope.all_values(),
            max_lifetime_days=int(config.auth.pat_max_lifetime)
            if config.auth.pat_max_lifetime is not None
            else None,
        )
        current_time = self._clock()
        active_count = self._repository.count_active(user_id=user_id, time=current_time)
        if active_count >= config.auth.pat_max_active_per_user:
            raise ValueError(
                f'Maximum number of active PAT ({config.auth.pat_max_active_per_user}) reached.'
            )

        secret = PATSecret.generate()
        expires_at = (
            current_time + datetime.timedelta(days=expires_in_days)
            if expires_in_days is not None
            else None
        )
        new_pat = PATCreationData(
            user_id=user_id,
            name=metadata.name,
            description=metadata.description,
            scopes=tuple(metadata.scopes),
            token_digest=secret.digest,
            expired_at=expires_at,
            created_at=current_time,
        )
        pat = self._repository.save_new(new_pat)
        public_pat = replace(pat, token_digest=None)
        return PATCreationResult(pat=public_pat, raw_token=secret.raw)

    def rotate(self, *, user_id: str, pat_id: str) -> PATCreationResult | None:
        current_time = self._clock()
        old_pat = self._repository.get_owned(
            pat_id=pat_id,
            user_id=user_id,
        )
        if old_pat is None:
            return None
        if not old_pat.is_active_at(current_time):
            raise ValueError('Cannot rotate an expired/revoked token.')

        old_pat = self._repository.claim_active_for_rotation(
            user_id=user_id,
            pat_id=pat_id,
            time=current_time,
            revoked_at=current_time,
        )
        if old_pat is None:
            # A concurrent revoke/rotation won the lifecycle transition after the
            # initial read. Do not mint another replacement token.
            raise ValueError('Cannot rotate an expired/revoked token.')

        return self.create(
            user_id=user_id,
            metadata=PATCreationSpec.model_validate(old_pat),
            expires_in_days=old_pat.rotation_lifetime(),
        )

    def list_owned(
        self,
        *,
        user_id: str,
        query: PATQuery | None = None,
        start: int = 0,
        limit: int | None = None,
        order_by: PATSortOrder = 'created_desc',
    ) -> PATQueryResult:
        spec = PATQuerySpec(**(query.model_dump() if query is not None else {}))
        data, total = self._repository.list_owned(
            user_id=user_id,
            spec=spec,
            time=self._clock(),
            start=start,
            limit=limit,
            order_by=order_by,
        )
        return PATQueryResult(data=data, total=total)

    def get(self, *, pat_id: str, user_id: str) -> PATRecord | None:
        pat = self._repository.get_owned(
            pat_id=pat_id,
            user_id=user_id,
        )
        if pat is None:
            return None
        return replace(pat, token_digest=None)

    def revoke(self, *, user_id: str, pat_id: str) -> bool:
        return self._repository.revoke_owned(
            user_id=user_id,
            pat_id=pat_id,
            revoked_at=self._clock(),
        )

    def prune(
        self,
        *,
        dry_run: bool = True,
        inactive_for: datetime.timedelta | None = None,
        inactive_before: datetime.datetime | None = None,
        expired: bool = False,
        revoked: bool = False,
        user_id: str | None = None,
    ) -> PATPruneResult:
        if not expired and not revoked:
            raise ValueError('At least one of expired or revoked must be True.')
        current_time = self._clock()
        cutoff = resolve_prune_cutoff(
            time=current_time,
            inactive_for=inactive_for,
            inactive_before=inactive_before,
        )
        prune_stats = self._repository.prune(
            cutoff=cutoff,
            expired=expired,
            revoked=revoked,
            user_id=user_id,
            dry_run=dry_run,
        )
        return PATPruneResult(
            matched=prune_stats.matched,
            deleted=prune_stats.deleted,
            expired_matched=prune_stats.expired_matched,
            revoked_matched=prune_stats.revoked_matched,
            cutoff=cutoff,
            dry_run=dry_run,
            user_id=user_id,
        )

    def authenticate(self, raw_token: str) -> PATRecord | None:
        if not raw_token.startswith(PAT_PREFIX):
            return None
        pat = self._repository.find_by_digest(hash_token(raw_token))
        current_time = self._clock()
        if pat is None or not pat.is_active_at(current_time):
            return None
        self._repository.mark_used(pat_id=pat.id, used_at=current_time)
        return replace(pat, token_digest=None, last_used_at=current_time)
