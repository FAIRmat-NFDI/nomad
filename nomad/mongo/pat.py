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

from mongoengine import BooleanField, Document, ListField, StringField

from nomad.common import now
from nomad.config import config
from nomad.mongo.fields import UTCDateTimeField


class PAT(Document):
    """
    A MongoDB document for storing personal access token (PAT).
    """

    # Metadata
    name = StringField(required=True)
    description = StringField()

    # Security
    user_id = StringField(required=True)
    token_digest = StringField(required=True, unique=True, min_length=64, max_length=64)
    scopes = ListField(StringField())

    # Lifecycle
    revoked = BooleanField(default=False)
    revoked_at = UTCDateTimeField()
    expired_at = UTCDateTimeField()
    created_at = UTCDateTimeField(default=now)
    updated_at = UTCDateTimeField(default=now)
    last_used_at = UTCDateTimeField()

    meta = {
        'collection': 'personal_access_tokens',
        'indexes': [
            ('user_id', '-created_at'),
            # Auto-delete expired/revoked tokens after set time
            {
                'fields': ['expired_at'],
                'expireAfterSeconds': config.auth.pat_pruning_time * 86400,
            },
            {
                'fields': ['revoked_at'],
                'expireAfterSeconds': config.auth.pat_pruning_time * 86400,
            },
        ],
    }
