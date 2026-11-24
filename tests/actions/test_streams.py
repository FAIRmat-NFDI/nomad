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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nomad.actions import streams
from nomad.actions.models import ActionStreamEvent, ActionStreamEventType
from nomad.actions.streams import (
    ACTION_STREAM_TOPIC,
    PROCESSING_STREAM_TOPIC,
    ActionStreamUnavailable,
    publish_action_event,
    stream_action_events_for_user_async,
    stream_processing_events_for_user_async,
)


@dataclass
class _FakeStreamItem:
    offset: int
    topic: str
    data: ActionStreamEvent


class _FakeStreamClient:
    def __init__(self, items: list[_FakeStreamItem], fail_offset: bool = False):
        self._items = items
        self._fail_offset = fail_offset
        self.subscribe_calls: list[dict] = []

    async def get_offset(self) -> int:
        if self._fail_offset:
            raise RuntimeError('no stream')
        return 7

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def subscribe(
        self,
        topic: str,
        *,
        from_offset: int,
        result_type,
        poll_cooldown,
    ) -> AsyncIterator[_FakeStreamItem]:
        self.subscribe_calls.append(
            dict(
                topic=topic,
                from_offset=from_offset,
                result_type=result_type,
                poll_cooldown=poll_cooldown,
            )
        )
        for item in self._items:
            yield item


@pytest.mark.asyncio
async def test_stream_workflow_events_async_yields_typed_items(monkeypatch):
    event = ActionStreamEvent(
        type=ActionStreamEventType.MESSAGE,
        message='hello',
        timestamp=datetime.now(UTC),
    )
    fake_client = _FakeStreamClient(
        [_FakeStreamItem(offset=3, topic=ACTION_STREAM_TOPIC, data=event)]
    )
    fake_factory = SimpleNamespace(create=lambda *_args, **_kwargs: fake_client)

    monkeypatch.setattr('nomad.actions.streams.WorkflowStreamClient', fake_factory)

    async def _mock_get_client():
        return MagicMock()

    monkeypatch.setattr('nomad.actions.streams.get_client', _mock_get_client)

    stream = await streams._stream_workflow_events_async(
        workflow_id='wf-1',
        topic=ACTION_STREAM_TOPIC,
        from_offset=2,
    )
    first = await anext(stream)

    assert first.offset == 3
    assert first.topic == ACTION_STREAM_TOPIC
    assert first.event.type == ActionStreamEventType.MESSAGE
    assert first.event.message == 'hello'
    assert fake_client.subscribe_calls[0]['from_offset'] == 2
    assert fake_client.subscribe_calls[0]['topic'] == ACTION_STREAM_TOPIC


@pytest.mark.asyncio
async def test_stream_workflow_events_async_raises_when_stream_unavailable(monkeypatch):
    fake_client = _FakeStreamClient([], fail_offset=True)
    fake_factory = SimpleNamespace(create=lambda *_args, **_kwargs: fake_client)

    monkeypatch.setattr('nomad.actions.streams.WorkflowStreamClient', fake_factory)

    async def _mock_get_client():
        return MagicMock()

    monkeypatch.setattr('nomad.actions.streams.get_client', _mock_get_client)

    with pytest.raises(ActionStreamUnavailable):
        await streams._stream_workflow_events_async('wf-2', ACTION_STREAM_TOPIC)


@pytest.mark.asyncio
async def test_stream_processing_events_for_user_async_uses_processing_topic(
    monkeypatch,
):
    event = ActionStreamEvent(
        type=ActionStreamEventType.MESSAGE,
        message='processing hello',
        timestamp=datetime.now(UTC),
    )
    fake_client = _FakeStreamClient(
        [_FakeStreamItem(offset=4, topic=PROCESSING_STREAM_TOPIC, data=event)]
    )
    fake_factory = SimpleNamespace(create=lambda *_args, **_kwargs: fake_client)

    monkeypatch.setattr('nomad.actions.streams.WorkflowStreamClient', fake_factory)

    async def _mock_get_client():
        return MagicMock()

    monkeypatch.setattr('nomad.actions.streams.get_client', _mock_get_client)
    monkeypatch.setattr(
        'nomad.actions.streams.asyncio.to_thread',
        AsyncMock(return_value=object()),
    )

    stream = await stream_processing_events_for_user_async(
        upload_id='upload-1',
        user=object(),
        from_offset=1,
    )
    first = await anext(stream)

    assert first.offset == 4
    assert first.topic == PROCESSING_STREAM_TOPIC
    assert fake_client.subscribe_calls[0]['topic'] == PROCESSING_STREAM_TOPIC


@pytest.mark.asyncio
async def test_publish_action_event_uses_topic_publish(monkeypatch):
    published: list[tuple[ActionStreamEvent, bool]] = []

    class _Topic:
        def publish(self, event: ActionStreamEvent, force_flush: bool = False):
            published.append((event, force_flush))

    @asynccontextmanager
    async def _mock_publisher(*_args, **_kwargs):
        yield _Topic()

    monkeypatch.setattr('nomad.actions.streams.action_event_publisher', _mock_publisher)

    event = ActionStreamEvent(type=ActionStreamEventType.STATE, message='working')
    await publish_action_event(event, force_flush=True)

    assert len(published) == 1
    assert published[0][0].type == ActionStreamEventType.STATE
    assert published[0][1] is True


@pytest.mark.asyncio
async def test_stream_action_events_for_user_async_validates_ownership(monkeypatch):
    require_for_user = AsyncMock(return_value=None)
    delegated_stream = object()
    stream_workflow_events_async_mock = AsyncMock(return_value=delegated_stream)

    monkeypatch.setattr(
        'nomad.actions.streams._async_action_repository.require_for_user',
        require_for_user,
    )
    monkeypatch.setattr(
        'nomad.actions.streams._stream_workflow_events_async',
        stream_workflow_events_async_mock,
    )

    result = await stream_action_events_for_user_async(
        action_instance_id='wf-3',
        user_id='u-3',
        from_offset=5,
    )

    assert result is delegated_stream
    require_for_user.assert_awaited_once_with('wf-3', 'u-3')
    stream_workflow_events_async_mock.assert_awaited_once()
    assert stream_workflow_events_async_mock.await_args.kwargs == {
        'workflow_id': 'wf-3',
        'topic': ACTION_STREAM_TOPIC,
        'from_offset': 5,
        'poll_cooldown': timedelta(seconds=1),
    }
