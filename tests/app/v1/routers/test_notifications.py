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
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from nomad.app.v1.routers import notifications


def test_collect_notifications_defaults_to_all_sources(monkeypatch):
    ownership = [
        {
            'id': 'ownership-1',
            'source': 'user',
            'type': 'ownership_transfer',
            'created_at': '2026-01-01T00:00:00Z',
            'data': {},
        }
    ]
    monkeypatch.setattr(
        notifications, '_ownership_notifications', lambda user: ownership
    )

    assert notifications.collect_notifications(MagicMock()) == ownership


def test_collect_notifications_filters_by_source(monkeypatch):
    ownership = [
        {
            'id': 'ownership-1',
            'source': 'user',
            'type': 'ownership_transfer',
            'created_at': '2026-01-01T00:00:00Z',
            'data': {},
        }
    ]
    monkeypatch.setattr(
        notifications, '_ownership_notifications', lambda user: ownership
    )

    assert notifications.collect_notifications(MagicMock(), 'user') == ownership


def test_collect_notifications_validates_records(monkeypatch):
    monkeypatch.setattr(
        notifications, '_ownership_notifications', lambda user: [{'id': 'invalid'}]
    )

    with pytest.raises(ValidationError):
        notifications.collect_notifications(MagicMock())


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
    event = await anext(notifications._events(request, MagicMock(), 'all'))

    assert event.startswith('event: notifications\n')
    payload = json.loads(event.split('data: ', 1)[1])
    assert payload == {'notifications': records}
