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
import json
from dataclasses import dataclass
from typing import Literal

NotificationSource = Literal['user', 'system']


@dataclass(frozen=True)
class NotificationRecord:
    """Persistence-independent notification inbox record."""

    id: str
    user_id: str | None
    source: NotificationSource
    notification_type: str
    created_at: datetime.datetime
    expires_at: datetime.datetime
    actor_user_id: str | None
    data: dict[str, object]


def compute_dedup_key(
    *,
    user_id: str | None,
    source: NotificationSource,
    notification_type: str,
    data: dict[str, object],
) -> str:
    """Build a stable key when a source domain does not supply one."""
    canonical = json.dumps(
        {
            'user_id': user_id,
            'source': source,
            'type': notification_type,
            'data': data,
        },
        sort_keys=True,
        separators=(',', ':'),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_notification(
    *,
    source: NotificationSource,
    notification_type: str,
    dedup_key: str,
    created_at: datetime.datetime,
    expires_at: datetime.datetime,
) -> None:
    if source not in ('user', 'system'):
        raise ValueError(f'Unsupported notification source: {source!r}.')
    if not notification_type.strip():
        raise ValueError('Notification type must not be empty.')
    if not dedup_key.strip():
        raise ValueError('Notification dedup key must not be empty.')
    if expires_at <= created_at:
        raise ValueError('Notification expiry must be after its creation time.')
