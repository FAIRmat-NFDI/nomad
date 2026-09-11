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

from mongoengine.queryset.visitor import Q
from pymongo import ReturnDocument

from nomad.mongo.notifications import Notification

from .domain import NotificationRecord, NotificationSource
from .ports import NotificationRepository


def to_record(notification: Notification) -> NotificationRecord:
    return NotificationRecord(
        id=str(notification.id),
        user_id=notification.user_id,
        source=notification.source,
        notification_type=notification.notification_type,
        created_at=notification.created_at,
        expires_at=notification.expires_at,
        actor_user_id=notification.actor_user_id,
        data=dict(notification.data or {}),
    )


class MongoNotificationRepository(NotificationRepository):
    """MongoEngine adapter for notification inbox records."""

    def upsert(self, notification: NotificationRecord) -> NotificationRecord:
        raw_document = Notification._get_collection().find_one_and_update(
            {'_id': notification.id},
            {
                '$set': {
                    'user_id': notification.user_id,
                    'source': notification.source,
                    'notification_type': notification.notification_type,
                    'actor_user_id': notification.actor_user_id,
                    'data': notification.data,
                    'created_at': notification.created_at,
                    'expires_at': notification.expires_at,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        assert raw_document is not None
        document = Notification._from_son(raw_document)
        return to_record(document)

    def delete_by_id(self, notification_id: str) -> bool:
        return Notification.objects(id=notification_id).delete() > 0

    def list_for_user(
        self,
        *,
        user_id: str,
        source: NotificationSource | None,
        time: datetime.datetime,
        limit: int,
    ) -> list[NotificationRecord]:
        audience = Q(user_id=user_id) | Q(user_id=None)
        query = Notification.objects(audience, expires_at__gt=time)
        if source is not None:
            query = query.filter(source=source)
        return [to_record(item) for item in query.order_by('-created_at')[:limit]]
