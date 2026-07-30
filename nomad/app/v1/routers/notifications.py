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
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from nomad.app.v1.models import User
from nomad.app.v1.routers.auth import get_current_user
from nomad.auth.scopes import Scope
from nomad.mongo.groups import get_mongo_user_group
from nomad.processing import Upload

from .ownership_transfers import _list_ownership_transfers

router = APIRouter()


class Notification(BaseModel):
    """Common wire format for every streamed notification."""

    model_config = ConfigDict(extra='forbid')

    id: str
    source: Literal['user', 'system']
    type: str
    created_at: str
    actor_user_id: str | None = None
    data: dict[str, object] = Field(default_factory=dict)


def _ownership_notifications(user: User) -> list[dict]:
    records = []
    sources = (
        (
            'upload',
            Upload.get,
            lambda resource: resource.main_author,
            lambda resource: resource.upload_name,
        ),
        (
            'group',
            get_mongo_user_group,
            lambda resource: resource.owner,
            lambda resource: resource.group_name,
        ),
    )
    for resource_type, get_resource, get_owner, get_name in sources:
        for state, direction, event in (
            ('pending', 'incoming', 'request'),
            ('refused', 'outgoing', 'refused'),
        ):
            typed_direction = cast(Literal['incoming', 'outgoing'], direction)
            response = _list_ownership_transfers(
                resource_type,
                typed_direction,
                None,
                state,
                user,
                get_resource,
                get_owner,
                get_name,
            )
            for transfer in response.transfers:
                timestamp = (
                    transfer.requested_at if event == 'request' else transfer.updated_at
                )
                actor_user_id = (
                    transfer.source_user_id
                    if event == 'request'
                    else transfer.actor_user_id or transfer.target_user_id
                )
                records.append(
                    {
                        'id': f'ownership-transfer-{event}-{transfer.transfer_id}',
                        'source': 'user',
                        'type': 'ownership_transfer',
                        'created_at': timestamp,
                        'actor_user_id': actor_user_id,
                        'data': {
                            'resource_type': resource_type,
                            'event': event,
                            'resource_id': transfer.resource_id,
                            'resource_name': transfer.resource_name,
                        },
                    }
                )
    return records


def collect_notifications(user: User, source: str = 'all') -> list[dict]:
    """Collect all notification sources into one stable wire format."""
    all_collectors = {
        'user': [_ownership_notifications],
        'system': [],
    }
    if source == 'all':
        collectors = [
            item for collector in all_collectors.values() for item in collector
        ]
    else:
        collectors = all_collectors.get(source, [])
    return [
        Notification.model_validate(item).model_dump(mode='json', exclude_none=True)
        for collector in collectors
        for item in collector(user)
    ]


async def _events(
    request: Request, user: User, source: str | None
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
        await asyncio.sleep(15)


@router.get(
    '',
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
        Literal['all', 'system', 'user'],
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
