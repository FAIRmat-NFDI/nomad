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
from typing import cast

from nomad.common import now

from .domain import (
    NotificationRecord,
    NotificationSource,
    compute_dedup_key,
    validate_notification,
)
from .ports import NotificationRepository

DEFAULT_NOTIFICATION_TTL = datetime.timedelta(days=90)
DEFAULT_NOTIFICATION_LIMIT = 50


class NotificationService:
    """Application service for materialized per-user notification inboxes."""

    def __init__(
        self,
        repository: NotificationRepository,
        clock: Callable[[], datetime.datetime] = now,
        default_ttl: datetime.timedelta = DEFAULT_NOTIFICATION_TTL,
    ):
        self._repository = repository
        self._clock = clock
        self._default_ttl = default_ttl

    def emit(
        self,
        *,
        source: NotificationSource,
        notification_type: str,
        data: dict[str, object],
        user_id: str | None = None,
        actor_user_id: str | None = None,
        dedup_key: str | None = None,
        created_at: datetime.datetime | None = None,
        expires_at: datetime.datetime | None = None,
    ) -> NotificationRecord:
        created_at = created_at or self._clock()
        expires_at = expires_at or created_at + self._default_ttl
        dedup_key = dedup_key or compute_dedup_key(
            user_id=user_id,
            source=source,
            notification_type=notification_type,
            data=data,
        )
        validate_notification(
            source=source,
            notification_type=notification_type,
            dedup_key=dedup_key,
            created_at=created_at,
            expires_at=expires_at,
        )
        record = NotificationRecord(
            id=dedup_key,
            user_id=user_id,
            source=source,
            notification_type=notification_type,
            created_at=created_at,
            expires_at=expires_at,
            actor_user_id=actor_user_id,
            data=dict(data),
        )

        # Inbox writes are idempotent. One immediate retry covers transient Mongo
        # failures while still surfacing a persistent failure to the request.
        for attempt in range(2):
            try:
                return self._repository.upsert(record)
            except Exception:
                if attempt == 1:
                    raise
        raise AssertionError('unreachable')

    def retract(self, dedup_key: str) -> bool:
        if not dedup_key.strip():
            raise ValueError('Notification dedup key must not be empty.')
        return self._repository.delete_by_id(dedup_key)

    def list_for_user(
        self,
        *,
        user_id: str,
        source: NotificationSource | None = None,
        limit: int = DEFAULT_NOTIFICATION_LIMIT,
    ) -> list[NotificationRecord]:
        if not user_id:
            raise ValueError('A user id is required to list notifications.')
        if source is not None and source not in ('user', 'system'):
            raise ValueError(f'Unsupported notification source: {source!r}.')
        if limit <= 0:
            raise ValueError('Notification limit must be positive.')
        return self._repository.list_for_user(
            user_id=user_id,
            source=cast(NotificationSource | None, source),
            time=self._clock(),
            limit=limit,
        )
