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

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Final

from fastapi_cache import Backend
from mongoengine import BinaryField, Document, DoesNotExist, StringField

from nomad.common import now
from nomad.mongo.fields import UTCDateTimeField

MONGO_CACHE_DEFAULT_TTL: Final[timedelta] = timedelta(hours=1)
SERVER_STATS_CACHE_KEY: Final[str] = 'server-stats'


class MongoCache(Document):
    """
    An arbitrary value to cache for fast retrieval.
    """

    DoesNotExist = DoesNotExist()
    key = StringField(required=True, unique=True)
    value = BinaryField()
    create_time = UTCDateTimeField(default=now)
    expire_time = UTCDateTimeField(required=True)

    meta = {
        'collection': 'cache',
        'indexes': [
            'key',
            {
                'fields': ['expire_time'],
                'expireAfterSeconds': 0,
            },
        ],
    }

    @classmethod
    def upsert(
        cls, key: str, value: bytes, create_time: datetime, expire_time: datetime
    ):
        return cls._get_collection().replace_one(
            {'key': key},
            {
                'key': key,
                'value': value,
                'create_time': create_time,
                'expire_time': expire_time,
            },
            upsert=True,
        )


class MongoBackend(Backend):
    """FastAPI Cache backend using BinaryField MongoCache model"""

    def __init__(self, namespace: str = ''):
        self.namespace = namespace

    def _make_key(self, key: str) -> str:
        """Add namespace prefix to key"""
        return f'{self.namespace}:{key}' if self.namespace else key

    def _get_cached(self, key: str):
        return MongoCache.objects(key=self._make_key(key)).get()

    async def get_with_ttl(self, key: str) -> tuple[int, bytes | None]:
        """Get cached value with TTL remaining"""

        def _get_cached():
            try:
                cached = self._get_cached(key)
                now_time = now()

                # Check if expired
                expire_time = cached.expire_time.replace(tzinfo=timezone.utc)
                if expire_time <= now_time:
                    return 0, None

                # Calculate remaining TTL in seconds
                ttl_remaining = int((expire_time - now_time).total_seconds())

                return ttl_remaining, cached.value

            except MongoCache.DoesNotExist:
                return 0, None

        return await asyncio.to_thread(_get_cached)

    async def get(self, key: str) -> bytes | None:
        """Get cached value"""
        ttl, value = await self.get_with_ttl(key)
        return value if ttl > 0 else None

    async def set(self, key: str, value: bytes, expire: int | None = None) -> None:
        """Set cached value with optional expiration"""

        def _set_cached():
            ttl = timedelta(seconds=expire) if expire else MONGO_CACHE_DEFAULT_TTL
            current = now()
            MongoCache.upsert(
                key=self._make_key(key),
                value=value,
                create_time=current,
                expire_time=current + ttl,
            )

        await asyncio.to_thread(_set_cached)

    async def clear(self, namespace: str | None = None, key: str | None = None) -> int:
        """Clear cache entries"""

        def _clear_cached():
            if key:
                # Clear specific key
                try:
                    cached = MongoCache.objects(key=self._make_key(key)).get()
                    cached.delete()
                    return 1
                except MongoCache.DoesNotExist:
                    return 0

            elif namespace:
                # Clear all keys with namespace prefix
                prefix = f'{namespace}:'
                deleted = MongoCache.objects(key__startswith=prefix).delete()
                return deleted

        return await asyncio.to_thread(_clear_cached) or 0
