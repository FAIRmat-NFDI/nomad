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

from nomad.common import now
from nomad.mongo.notifications import Notification
from nomad.notifications import MongoNotificationRepository, NotificationRecord


def _record(*, id, user_id, source, created_at, expires_at):
    return NotificationRecord(
        id=id,
        user_id=user_id,
        source=source,
        notification_type='example',
        created_at=created_at,
        expires_at=expires_at,
        actor_user_id=None,
        data={'version': 1},
    )


def test_notification_indexes(mongo_function):
    Notification.ensure_indexes()
    indexes = Notification._get_collection().index_information()

    assert '_id_' in indexes
    assert any(index.get('expireAfterSeconds') == 0 for index in indexes.values())


def test_repository_upsert_and_user_global_listing(mongo_function):
    repository = MongoNotificationRepository()
    current_time = now()
    future = current_time + datetime.timedelta(days=1)

    repository.upsert(
        _record(
            id='user-old',
            user_id='user-1',
            source='user',
            created_at=current_time,
            expires_at=future,
        )
    )
    repository.upsert(
        _record(
            id='global-new',
            user_id=None,
            source='system',
            created_at=current_time + datetime.timedelta(seconds=1),
            expires_at=future,
        )
    )
    repository.upsert(
        _record(
            id='other-user',
            user_id='user-2',
            source='user',
            created_at=current_time + datetime.timedelta(seconds=2),
            expires_at=future,
        )
    )

    records = repository.list_for_user(
        user_id='user-1', source=None, time=current_time, limit=50
    )
    assert [record.id for record in records] == ['global-new', 'user-old']

    updated = _record(
        id='user-old',
        user_id='user-1',
        source='user',
        created_at=current_time + datetime.timedelta(seconds=3),
        expires_at=future,
    )
    repository.upsert(updated)
    assert Notification.objects(id='user-old').count() == 1
    assert repository.delete_by_id('user-old') is True
