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

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from nomad.app.v1.routers import notifications
from nomad.notifications import NotificationRecord


def _record(source='user'):
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return NotificationRecord(
        id='ownership-transfer-request-1',
        user_id='user-1',
        source=source,
        notification_type='ownership_transfer',
        created_at=created_at,
        expires_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        actor_user_id=None,
        data={},
    )


def test_collect_notifications_defaults_to_all_sources(monkeypatch):
    list_for_user = MagicMock(return_value=[_record()])
    monkeypatch.setattr(
        notifications.notification_service, 'list_for_user', list_for_user
    )
    user = MagicMock(user_id='user-1')

    assert notifications.collect_notifications(user) == [
        {
            'id': 'ownership-transfer-request-1',
            'source': 'user',
            'type': 'ownership_transfer',
            'created_at': '2026-01-01T00:00:00+00:00',
            'data': {},
        }
    ]
    list_for_user.assert_called_once_with(user_id='user-1', source=None)


def test_collect_notifications_filters_by_source(monkeypatch):
    list_for_user = MagicMock(return_value=[_record()])
    monkeypatch.setattr(
        notifications.notification_service, 'list_for_user', list_for_user
    )
    user = MagicMock(user_id='user-1')

    notifications.collect_notifications(user, 'user')
    list_for_user.assert_called_once_with(user_id='user-1', source='user')


@pytest.mark.asyncio
async def test_notification_events_emit_json_payload(monkeypatch):
    records = [
        {
            'id': 'ownership-1',
            'source': 'user',
            'type': 'ownership_transfer',
            'created_at': '2026-01-01T00:00:00Z',
            'data': {},
        }
    ]
    monkeypatch.setattr(
        notifications, 'collect_notifications', lambda user, source: records
    )

    request = MagicMock()
    request.is_disconnected = AsyncMock(side_effect=[False, True])
    events = notifications._events(request, MagicMock(), 'all')
    event = await anext(events)
    await events.aclose()

    assert event.startswith('event: notifications\n')
    payload = json.loads(event.split('data: ', 1)[1])
    assert payload == {'notifications': records}
