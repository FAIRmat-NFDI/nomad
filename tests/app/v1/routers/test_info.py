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

from unittest.mock import MagicMock

from nomad.mongo.cache import SERVER_STATS_CACHE_KEY, MongoCache
from nomad.server_stats import collect_server_stats


def test_info_without_snapshot(client, mongo_function):
    rv = client.get('info')
    assert rv.status_code == 200
    data = rv.json()
    assert 'version' in data
    assert 'parsers' in data
    assert data['parsers'] == []
    assert 'statistics' not in data
    assert 'collect_time' not in data


def test_info_does_not_collect_on_get(client, mongo_function, monkeypatch):
    collect = MagicMock(side_effect=AssertionError('GET /info must not collect'))
    monkeypatch.setattr('nomad.server_stats.collect_server_stats', collect)

    rv = client.get('info')
    assert rv.status_code == 200
    collect.assert_not_called()
    assert 'statistics' not in rv.json()


def test_info_reads_cached_snapshot(
    client, mongo_function, elastic_function, raw_files_function
):
    collect_server_stats()

    cached = MongoCache.objects(key=SERVER_STATS_CACHE_KEY).first()
    assert cached is not None

    rv = client.get('info')
    assert rv.status_code == 200
    data = rv.json()
    assert 'codes' in data
    assert 'parsers' in data
    assert 'statistics' in data
    assert 'public_data_size' in data['statistics']
    assert 'collect_time' in data
    assert len(data['parsers']) >= len(data['codes'])


def test_info_survives_startup_cache_flush(
    client, mongo_function, elastic_function, raw_files_function
):
    collect_server_stats()
    MongoCache.objects(key__ne=SERVER_STATS_CACHE_KEY).delete()

    rv = client.get('info')
    assert rv.status_code == 200
    assert 'statistics' in rv.json()
