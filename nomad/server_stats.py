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
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from nomad import normalizing
from nomad.common import now
from nomad.config import config
from nomad.mongo.cache import SERVER_STATS_CACHE_KEY, MongoCache
from nomad.parsing import parsers
from nomad.parsing.parsers import code_metadata
from nomad.search import get_statistics

SERVER_STATS_TTL: Final[timedelta] = timedelta(days=7)


def uncached_info_payload() -> dict[str, Any]:
    """Identity fields that are cheap to read when no snapshot exists yet."""
    return {
        'parsers': [],
        'metainfo_packages': [],
        'codes': [],
        'normalizers': [],
        'plugin_entry_points': [],
        'plugin_packages': [],
        'version': config.meta.version,
        'deployment': config.meta.deployment,
        'oasis': config.oasis.is_oasis,
        'git': {},
    }


def load_server_stats() -> dict[str, Any] | None:
    cached = MongoCache.objects(key=SERVER_STATS_CACHE_KEY).first()
    if cached is None or cached.value is None:
        return None

    expire_time = cached.expire_time
    if expire_time.tzinfo is None:
        expire_time = expire_time.replace(tzinfo=timezone.utc)
    if expire_time <= now():
        return None

    payload = json.loads(cached.value)
    return payload if isinstance(payload, dict) else None


def collect_server_stats() -> dict[str, Any]:
    """Build the /info snapshot and upsert it into the cache collection."""
    timestamp_now: datetime = now()
    payload = _build_payload(timestamp_now)
    MongoCache.upsert(
        key=SERVER_STATS_CACHE_KEY,
        value=json.dumps(payload, default=_json_default).encode(),
        create_time=timestamp_now,
        expire_time=timestamp_now + SERVER_STATS_TTL,
    )
    return payload


def _build_payload(timestamp_now: datetime) -> dict[str, Any]:
    parser_names = sorted(
        [re.sub(r'^(parsers?|missing)/', '', key) for key in parsers.parser_dict.keys()]
    )

    config.load_plugins()

    public_fs = config.fs.public_fs
    public_data_size = public_fs.target_fs.du(
        public_fs.bucket if public_fs.protocol else config.fs.public
    )

    return {
        'collect_time': timestamp_now,
        'parsers': parser_names,
        'metainfo_packages': [
            'general',
            'general.experimental',
            'common',
            'public',
        ]
        + parser_names,
        'codes': [
            {
                'code_name': x.get('codeLabel', 'unknown code'),
                'code_homepage': x.get('codeUrl'),
            }
            for x in sorted(
                code_metadata.values(),
                key=lambda info: info.get('codeLabel', 'unknown code').lower(),
            )
        ],
        'normalizers': [normalizer.__name__ for normalizer in normalizing.normalizers],
        'plugin_entry_points': [
            entry_point.dict_safe()
            for entry_point in config.plugins.entry_points.filtered_values()
        ]
        if config.plugins and config.plugins.entry_points
        else [],
        'plugin_packages': [
            plugin_package.model_dump()
            for plugin_package in config.plugins.plugin_packages.values()
        ]
        if config.plugins and config.plugins.plugin_packages
        else [],
        'statistics': get_statistics() | {'public_data_size': public_data_size},
        'version': config.meta.version,
        'deployment': config.meta.deployment,
        'oasis': config.oasis.is_oasis,
        'git': {},
    }


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')
