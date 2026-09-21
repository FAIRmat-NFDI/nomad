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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from temporalio.contrib.workflow_streams import WorkflowStreamClient

from nomad.actions.bootstrap import async_action_repository
from nomad.actions.client import get_client
from nomad.actions.models import ActionStreamEvent, ActionStreamItem

ACTION_STREAM_TOPIC = 'action'
PROCESSING_STREAM_TOPIC = 'processing'

__all__ = [
    'ACTION_STREAM_TOPIC',
    'PROCESSING_STREAM_TOPIC',
    'ActionStreamUnavailable',
    'action_event_publisher',
    'publish_action_event',
    'stream_processing_events_for_user_async',
    'stream_action_events_for_user_async',
]

_async_action_repository = async_action_repository


class ActionStreamUnavailable(Exception):
    """Raised when an action workflow does not expose the action event stream."""


async def _stream_workflow_events_async(
    workflow_id: str,
    topic: str,
    from_offset: int = 0,
    poll_cooldown: timedelta = timedelta(seconds=1),
) -> AsyncIterator[ActionStreamItem]:
    """
    Subscribe to an opt-in action workflow event stream.

    The stream availability check happens before the iterator is returned, so
    API callers can still map failures to normal HTTP errors.
    """
    client = await get_client()
    stream_client = WorkflowStreamClient.create(
        client,
        workflow_id=workflow_id,
    )

    try:
        await stream_client.get_offset()
    except Exception as exc:
        raise ActionStreamUnavailable(
            'Action does not expose an event stream or the stream is no longer available.'
        ) from exc

    async def _events() -> AsyncIterator[ActionStreamItem]:
        async with stream_client:
            async for item in stream_client.subscribe(
                topic,
                from_offset=from_offset,
                result_type=ActionStreamEvent,
                poll_cooldown=poll_cooldown,
            ):
                yield ActionStreamItem(
                    offset=item.offset,
                    topic=item.topic,
                    event=item.data,
                )

    return _events()


async def stream_processing_events_for_user_async(
    upload_id: str,
    user: Any,
    from_offset: int = 0,
    poll_cooldown: timedelta = timedelta(seconds=1),
) -> AsyncIterator[ActionStreamItem]:
    """Validate upload visibility, then stream processing events."""
    from nomad.app.v1.routers.uploads import get_upload_with_read_access

    await asyncio.to_thread(get_upload_with_read_access, upload_id, user, True)
    return await _stream_workflow_events_async(
        workflow_id=upload_id,
        topic=PROCESSING_STREAM_TOPIC,
        from_offset=from_offset,
        poll_cooldown=poll_cooldown,
    )


async def stream_action_events_for_user_async(
    action_instance_id: str,
    user_id: str,
    from_offset: int = 0,
    poll_cooldown: timedelta = timedelta(seconds=1),
) -> AsyncIterator[ActionStreamItem]:
    """Validate ownership, then stream action events for the underlying workflow."""
    await _async_action_repository.require_for_user(action_instance_id, user_id)
    return await _stream_workflow_events_async(
        workflow_id=action_instance_id,
        topic=ACTION_STREAM_TOPIC,
        from_offset=from_offset,
        poll_cooldown=poll_cooldown,
    )


@asynccontextmanager
async def action_event_publisher(
    workflow_id: str | None = None,
    *,
    batch_interval: timedelta = timedelta(milliseconds=200),
    max_batch_size: int = 100,
    max_retry_duration: timedelta = timedelta(minutes=10),
):
    """
    Open a batched publisher for action events from an async activity.

    When ``workflow_id`` is omitted, events are published to the activity's
    parent workflow. Pass the root action instance ID from activity input when
    publishing from child-workflow activities that should surface on the root
    action stream.
    """

    if workflow_id is None:
        stream_client = WorkflowStreamClient.from_within_activity(
            batch_interval=batch_interval,
            max_batch_size=max_batch_size,
            max_retry_duration=max_retry_duration,
        )
    else:
        from temporalio import activity

        stream_client = WorkflowStreamClient.create(
            activity.client(),
            workflow_id=workflow_id,
            batch_interval=batch_interval,
            max_batch_size=max_batch_size,
            max_retry_duration=max_retry_duration,
        )

    async with stream_client:
        yield stream_client.topic(ACTION_STREAM_TOPIC, type=ActionStreamEvent)


async def publish_action_event(
    event: ActionStreamEvent,
    workflow_id: str | None = None,
    *,
    force_flush: bool = False,
) -> None:
    """Publish one action event from an async activity."""

    async with action_event_publisher(workflow_id) as events:
        events.publish(event, force_flush=force_flush)
