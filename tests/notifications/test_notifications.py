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

import datetime

import pytest

from nomad.notifications import NotificationRecord, NotificationService


class InMemoryNotificationRepository:
    def __init__(self):
        self.records: dict[str, NotificationRecord] = {}
        self.upsert_attempts = 0
        self.fail_upserts = 0

    def upsert(self, notification):
        self.upsert_attempts += 1
        if self.fail_upserts:
            self.fail_upserts -= 1
            raise RuntimeError('temporary write failure')
        self.records[notification.id] = notification
        return notification

    def delete_by_id(self, notification_id):
        return self.records.pop(notification_id, None) is not None

    def list_for_user(self, *, user_id, source, time, limit):
        return sorted(
            (
                record
                for record in self.records.values()
                if record.user_id in (None, user_id)
                and (source is None or record.source == source)
                and record.expires_at > time
            ),
            key=lambda record: record.created_at,
            reverse=True,
        )[:limit]


@pytest.fixture
def current_time():
    return datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


@pytest.fixture
def repository():
    return InMemoryNotificationRepository()


@pytest.fixture
def service(repository, current_time):
    return NotificationService(repository=repository, clock=lambda: current_time)


def test_emit_computes_stable_dedup_id_and_expiry(service, repository, current_time):
    first = service.emit(
        user_id='user-1',
        source='user',
        notification_type='example',
        data={'resource_id': 'one'},
    )
    second = service.emit(
        user_id='user-1',
        source='user',
        notification_type='example',
        data={'resource_id': 'one'},
    )

    assert first.id == second.id
    assert first.expires_at == current_time + datetime.timedelta(days=90)
    assert len(repository.records) == 1


def test_emit_retries_once_and_surfaces_a_second_failure(service, repository):
    repository.fail_upserts = 1
    service.emit(source='system', notification_type='example', data={})
    assert repository.upsert_attempts == 2

    repository.fail_upserts = 2
    with pytest.raises(RuntimeError, match='temporary write failure'):
        service.emit(source='system', notification_type='another', data={})
    assert repository.upsert_attempts == 4


def test_list_includes_user_and_global_rows_newest_first(service, current_time):
    service.emit(
        user_id='user-1',
        source='user',
        notification_type='old',
        data={},
        created_at=current_time,
    )
    service.emit(
        source='system',
        notification_type='global',
        data={},
        created_at=current_time + datetime.timedelta(seconds=1),
    )
    service.emit(
        user_id='user-2',
        source='user',
        notification_type='other-user',
        data={},
        created_at=current_time + datetime.timedelta(seconds=2),
    )

    records = service.list_for_user(user_id='user-1')
    assert [record.notification_type for record in records] == ['global', 'old']
    assert service.list_for_user(user_id='user-1', source='system') == [records[0]]


def test_retract_is_idempotent(service):
    record = service.emit(source='system', notification_type='example', data={})
    assert service.retract(record.id) is True
    assert service.retract(record.id) is False
