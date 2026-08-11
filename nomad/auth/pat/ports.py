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
from dataclasses import dataclass
from typing import Literal, NamedTuple, Protocol

from .domain import PATRecord

PATSortOrder = Literal[
    'created_asc',
    'created_desc',
    'expires_asc',
    'expires_desc',
    'last_used_asc',
    'last_used_desc',
    'name_asc',
    'name_desc',
]


class PruneStats(NamedTuple):
    matched: int
    deleted: int
    expired_matched: int
    revoked_matched: int


@dataclass(frozen=True)
class PATQuerySpec:
    search: str | None = None
    revoked: bool | None = None
    state: Literal['active', 'inactive'] | None = None
    created_after: datetime.datetime | None = None
    created_before: datetime.datetime | None = None
    last_used_after: datetime.datetime | None = None
    last_used_before: datetime.datetime | None = None
    expires_after: datetime.datetime | None = None
    expires_before: datetime.datetime | None = None


@dataclass(frozen=True)
class PATCreationData:
    """Data passed across the repository port when persisting a new PAT."""

    user_id: str
    name: str
    description: str | None
    scopes: tuple[str, ...]
    token_digest: str
    expired_at: datetime.datetime | None
    created_at: datetime.datetime


class PATRepository(Protocol):
    """Interface (Port) defining persistence operations for Personal Access Tokens."""

    def save_new(self, new_pat: PATCreationData) -> PATRecord:
        """Saves a new Personal Access Token."""
        ...

    def get_owned(self, *, pat_id: str, user_id: str) -> PATRecord | None:
        """Retrieves a specific PAT belonging to a user."""
        ...

    def count_active(self, *, user_id: str, time: datetime.datetime) -> int:
        """Counts active tokens for a user at the specified time."""
        ...

    def list_owned(
        self,
        *,
        user_id: str,
        spec: PATQuerySpec,
        time: datetime.datetime,
        start: int,
        limit: int | None,
        order_by: PATSortOrder,
    ) -> tuple[list[PATRecord], int]:
        """Lists and paginates tokens for a user matching the query specification.

        Returns:
            A tuple of (records, total_count) where records is the paginated list
            of matching tokens (with sensitive digest redacted) and total_count
            is the total number of matching tokens before pagination.
        """
        ...

    def revoke_owned(
        self, *, user_id: str, pat_id: str, revoked_at: datetime.datetime
    ) -> bool:
        """Revokes a specific token belonging to a user."""
        ...

    def claim_active_for_rotation(
        self,
        *,
        user_id: str,
        pat_id: str,
        time: datetime.datetime,
        revoked_at: datetime.datetime,
    ) -> PATRecord | None:
        """Atomically revokes an active token and returns its pre-rotation metadata."""
        ...

    def find_by_digest(self, digest: str) -> PATRecord | None:
        """Retrieves a token by its cryptographic digest."""
        ...

    def mark_used(self, *, pat_id: str, used_at: datetime.datetime) -> None:
        """Updates the last used timestamp of a token."""
        ...

    def prune(
        self,
        *,
        cutoff: datetime.datetime,
        expired: bool,
        revoked: bool,
        user_id: str | None,
        dry_run: bool,
    ) -> PruneStats:
        """Prunes inactive tokens from the database based on criteria."""
        ...
