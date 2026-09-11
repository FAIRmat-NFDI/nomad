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

from mongoengine import DictField, Document, StringField

from nomad.mongo.fields import UTCDateTimeField


class Notification(Document):
    """Persisted notification inbox entry.

    Each document is a disposable, user-facing projection emitted by a source
    domain. It contains the data needed to render a notification without loading
    the source resource. It does not represent or control workflow state; that
    remains in the source domain and determines when this entry is emitted,
    replaced, or retracted.
    """

    id = StringField(primary_key=True)
    user_id = StringField(null=True)
    source = StringField(required=True, choices=('user', 'system'))
    notification_type = StringField(required=True)
    actor_user_id = StringField(null=True)
    data = DictField(default=dict)
    created_at = UTCDateTimeField(required=True)
    expires_at = UTCDateTimeField(required=True)

    meta = {
        'collection': 'notifications',
        'indexes': [
            ('user_id', 'source', '-created_at'),
            {'fields': ['expires_at'], 'expireAfterSeconds': 0},
        ],
    }
