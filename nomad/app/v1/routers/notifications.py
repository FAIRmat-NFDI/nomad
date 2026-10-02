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

import asyncio
import json
from collections.abc import AsyncIterator
from enum import Enum
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from nomad.app.v1.models import User
from nomad.app.v1.routers.auth import get_current_user
from nomad.auth.scopes import Scope
from nomad.notifications import notification_service

router = APIRouter()


class APITag(str, Enum):
    DEFAULT = 'notifications'


NotificationSourceFilter = Literal['all', 'system', 'user']


class Notification(BaseModel):
    """Common wire format for every streamed notification."""

    model_config = ConfigDict(extra='forbid')

    id: str
    source: Literal['user', 'system']
    type: str
    created_at: str
    actor_user_id: str | None = None
    data: dict[str, object] = Field(default_factory=dict)


def collect_notifications(
    user: User, source: NotificationSourceFilter = 'all'
) -> list[dict]:
    """Read the user's materialized inbox in the stable SSE wire format."""
    records = notification_service.list_for_user(
        user_id=user.user_id,
        source=None if source == 'all' else source,
    )
    return [
        Notification(
            id=record.id,
            source=record.source,
            type=record.notification_type,
            created_at=record.created_at.isoformat(),
            actor_user_id=record.actor_user_id,
            data=record.data,
        ).model_dump(mode='json', exclude_none=True)
        for record in records
    ]


async def _events(
    request: Request, user: User, source: NotificationSourceFilter
) -> AsyncIterator[str]:
    previous: str | None = None
    while not await request.is_disconnected():
        records = await asyncio.to_thread(collect_notifications, user, source)
        payload = json.dumps({'notifications': records}, default=str)
        if payload != previous:
            yield f'event: notifications\ndata: {payload}\n\n'
            previous = payload
        else:
            yield ': keep-alive\n\n'
        await asyncio.sleep(5)


@router.get(
    '',
    tags=[APITag.DEFAULT],
    response_class=StreamingResponse,
    summary='Stream notifications.',
    description=(
        'Opens an authenticated Server-Sent Events (SSE) stream containing the '
        'current notifications. Use the `source` query parameter to select '
        '`all`, `user`, or `system` notifications. Each `notifications` event '
        'contains a JSON object with a `notifications` array. Records include '
        '`id`, `source`, `type`, `created_at`, optional `actor_user_id`, and '
        '`data`. The `data` object is source-specific; ownership transfer '
        'records contain `resource_type`, `event`, `resource_id`, and '
        '`resource_name`. The server emits keep-alive comments while there are '
        'no changes and closes the stream when the client disconnects.'
    ),
)
async def stream_notifications(
    request: Request,
    user: Annotated[
        User, Depends(get_current_user([Scope.UPLOADS_READ], allow_anonymous=False))
    ],
    source: Annotated[
        NotificationSourceFilter,
        Query(
            description=(
                'Notification source filter. Defaults to all sources.'
                ' Currently supported: all, system, and user.'
            ),
        ),
    ] = 'all',
):
    return StreamingResponse(
        _events(request, user, source),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )
