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

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from elasticsearch.dsl import Search

from nomad import infrastructure
from nomad.config import config
from nomad.elastic_compat import (
    create_elastic_client,
    delete_search,
    execute_search,
    verify_server_version,
)


def test_verify_server_version_accepts_es7_dict_response():
    client = Mock()
    client.info.return_value = {'version': {'number': '7.17.13'}}

    assert verify_server_version(client, '7') == '7.17.13'


def test_verify_server_version_accepts_es9_wrapped_response():
    client = Mock()
    client.info.return_value = SimpleNamespace(body={'version': {'number': '9.5.0'}})

    assert verify_server_version(client, '9') == '9.5.0'


def test_verify_server_version_rejects_mismatch():
    client = Mock()
    client.info.return_value = {'version': {'number': '7.17.13'}}

    with pytest.raises(RuntimeError, match='version 9.*reports version 7'):
        verify_server_version(client, '9')


def test_execute_search_normalizes_es7_response(monkeypatch):
    client = Mock()
    client.search.return_value = {
        'took': 0,
        'timed_out': False,
        '_shards': {'total': 1, 'successful': 1, 'skipped': 0, 'failed': 0},
        'hits': {
            'total': {'value': 0, 'relation': 'eq'},
            'max_score': None,
            'hits': [],
        },
    }
    monkeypatch.setattr(infrastructure, 'elastic_client', client)
    monkeypatch.setattr(config.elastic, 'version', '7')

    search = Search(index='entries').query('match_all')
    response = execute_search(search)

    assert response.hits.total.value == 0
    client.search.assert_called_once()
    assert execute_search(search) is response


def test_delete_search_uses_selected_transport(monkeypatch):
    client = Mock()
    client.delete_by_query.return_value = {'deleted': 1}
    monkeypatch.setattr(infrastructure, 'elastic_client', client)
    monkeypatch.setattr(config.elastic, 'version', '7')

    result = delete_search(Search(index='entries').query('term', upload_id='u1'))

    assert result == {'deleted': 1}
    kwargs = client.delete_by_query.call_args.kwargs
    assert kwargs['index'] == ['entries']
    assert kwargs['body']['query']['term']['upload_id'] == 'u1'


def test_create_elastic_client_uses_es7_parameter_names(monkeypatch):
    import elasticsearch7

    client = Mock()
    constructor = Mock(return_value=client)
    monkeypatch.setattr(elasticsearch7, 'Elasticsearch', constructor)
    monkeypatch.setattr(config.elastic, 'version', '7')

    assert create_elastic_client(verify=False) is client
    kwargs = constructor.call_args.kwargs
    assert kwargs['timeout'] == config.elastic.timeout
    assert kwargs['http_auth'] is None
    assert kwargs['hosts'] == [
        f'{config.elastic.scheme}://{config.elastic.host}:{config.elastic.port}'
    ]
    assert 'request_timeout' not in kwargs
    assert 'basic_auth' not in kwargs


def test_create_elastic_client_uses_es9_parameter_names(monkeypatch):
    import elasticsearch

    client = Mock()
    constructor = Mock(return_value=client)
    monkeypatch.setattr(elasticsearch, 'Elasticsearch', constructor)
    monkeypatch.setattr(config.elastic, 'version', '9')

    assert create_elastic_client(verify=False) is client
    kwargs = constructor.call_args.kwargs
    assert kwargs['request_timeout'] == config.elastic.timeout
    assert kwargs['basic_auth'] is None
    assert kwargs['hosts'] == [
        f'{config.elastic.scheme}://{config.elastic.host}:{config.elastic.port}'
    ]
    assert 'timeout' not in kwargs
    assert 'http_auth' not in kwargs


def test_elastic_config_version_integer_coercion():
    from pydantic import ValidationError

    from nomad.config.models.config import Elastic

    assert Elastic(version=7).version == '7'
    assert Elastic(version=9).version == '9'
    assert Elastic(version='7').version == '7'
    assert Elastic(version='9').version == '9'

    with pytest.raises(ValidationError):
        Elastic(version=8)

    with pytest.raises(ValidationError):
        Elastic(version='8')

    with pytest.raises(ValidationError):
        Elastic(version=True)


def test_elastic_config_scheme_normalization():
    from pydantic import ValidationError

    from nomad.config.models.config import Elastic

    assert Elastic().scheme == 'http'
    assert Elastic(scheme='http').scheme == 'http'
    assert Elastic(scheme='https').scheme == 'https'
    assert Elastic(scheme='http://').scheme == 'http'
    assert Elastic(scheme='HTTPS://').scheme == 'https'

    with pytest.raises(ValidationError):
        Elastic(scheme='ftp')


def test_create_elastic_client_uses_configured_scheme(monkeypatch):
    import elasticsearch

    client = Mock()
    constructor = Mock(return_value=client)
    monkeypatch.setattr(elasticsearch, 'Elasticsearch', constructor)
    monkeypatch.setattr(config.elastic, 'version', '9')
    monkeypatch.setattr(config.elastic, 'scheme', 'https')

    assert create_elastic_client(verify=False) is client
    assert constructor.call_args.kwargs['hosts'] == [
        f'https://{config.elastic.host}:{config.elastic.port}'
    ]


def test_transport_error_types_includes_apierror():
    from elasticsearch.exceptions import ApiError as Elasticsearch9ApiError

    from nomad.elastic_compat import TRANSPORT_ERROR_TYPES, ApiError

    assert Elasticsearch9ApiError in TRANSPORT_ERROR_TYPES
    assert ApiError is Elasticsearch9ApiError

    with pytest.raises(TRANSPORT_ERROR_TYPES):
        raise Elasticsearch9ApiError('simulated api error', meta=Mock(), body={})


@pytest.mark.parametrize(
    'intervals, calendar_interval, fixed_interval',
    [
        ({}, '1M', None),
        (
            {'interval': None, 'calendar_interval': None, 'fixed_interval': None},
            '1M',
            None,
        ),
        ({'calendar_interval': '1w'}, '1w', None),
        ({'fixed_interval': '30m'}, None, '30m'),
        ({'interval': '1s'}, '1s', None),
        ({'interval': '1d'}, '1d', None),
        ({'interval': 'month'}, 'month', None),
        ({'interval': '1M'}, '1M', None),
        ({'interval': '1m'}, '1m', None),
        ({'interval': '30m'}, None, '30m'),
        ({'interval': '24h'}, None, '24h'),
    ],
)
def test_date_histogram_aggregation_validation(
    intervals, calendar_interval, fixed_interval
):
    from nomad.app.v1.models.models import (
        DateHistogramAggregation,
        DateHistogramAggregationResponse,
    )
    from tests.app.v1.routers.common import assert_aggregations

    request = {'quantity': 'upload_create_time', **intervals}
    agg = DateHistogramAggregation(**request)
    assert agg.interval is None
    assert agg.calendar_interval == calendar_interval
    assert agg.fixed_interval == fixed_interval
    assert DateHistogramAggregation.model_validate(agg.model_dump()) == agg

    response = DateHistogramAggregationResponse(data=[], **agg.model_dump()).model_dump(
        exclude_none=True
    )
    assert 'interval' not in response
    assert response.get('calendar_interval') == calendar_interval
    assert response.get('fixed_interval') == fixed_interval
    assert_aggregations(
        {'aggregations': {'test_agg': {'date_histogram': response}}},
        'test_agg',
        request,
    )


def test_date_histogram_aggregation_conflicting_intervals():
    from pydantic import ValidationError

    from nomad.app.v1.models.models import DateHistogramAggregation

    # Conflicting intervals
    with pytest.raises(ValidationError):
        DateHistogramAggregation(
            quantity='upload_create_time',
            calendar_interval='1d',
            fixed_interval='24h',
        )

    with pytest.raises(ValidationError):
        DateHistogramAggregation(
            quantity='upload_create_time',
            interval='1d',
            calendar_interval='1d',
        )

    with pytest.raises(ValidationError):
        DateHistogramAggregation(
            quantity='upload_create_time',
            interval='1d',
            fixed_interval='24h',
        )


def test_date_histogram_aggregation_es_query_building():
    from nomad.app.v1.models.models import DateHistogramAggregation
    from nomad.search import _api_to_es_aggregation

    # 1. Explicit calendar_interval
    s = Search()
    agg = DateHistogramAggregation(
        quantity='upload_create_time', calendar_interval='1w'
    )
    _api_to_es_aggregation(s, 'test_agg', agg, None, None)
    d = s.to_dict()['aggs']['agg:test_agg']['date_histogram']
    assert d['calendar_interval'] == '1w'
    assert 'fixed_interval' not in d

    # 2. Explicit fixed_interval
    s = Search()
    agg = DateHistogramAggregation(quantity='upload_create_time', fixed_interval='2h')
    _api_to_es_aggregation(s, 'test_agg', agg, None, None)
    d = s.to_dict()['aggs']['agg:test_agg']['date_histogram']
    assert d['fixed_interval'] == '2h'
    assert 'calendar_interval' not in d

    # 3. Backwards compatible calendar interval
    s = Search()
    agg = DateHistogramAggregation(quantity='upload_create_time', interval='1d')
    _api_to_es_aggregation(s, 'test_agg', agg, None, None)
    d = s.to_dict()['aggs']['agg:test_agg']['date_histogram']
    assert d['calendar_interval'] == '1d'
    assert 'fixed_interval' not in d

    # 4. Backwards compatible fixed interval
    s = Search()
    agg = DateHistogramAggregation(quantity='upload_create_time', interval='30m')
    _api_to_es_aggregation(s, 'test_agg', agg, None, None)
    d = s.to_dict()['aggs']['agg:test_agg']['date_histogram']
    assert d['fixed_interval'] == '30m'
    assert 'calendar_interval' not in d

    # 5. Default interval ('1M' -> calendar_interval)
    s = Search()
    agg = DateHistogramAggregation(quantity='upload_create_time')
    _api_to_es_aggregation(s, 'test_agg', agg, None, None)
    d = s.to_dict()['aggs']['agg:test_agg']['date_histogram']
    assert d['calendar_interval'] == '1M'
    assert 'fixed_interval' not in d


def test_optimade_elasticsearch_dsl_patch():
    import sys

    import elasticsearch.dsl

    import nomad.app.optimade  # noqa: F401

    assert 'elasticsearch_dsl' in sys.modules
    assert sys.modules['elasticsearch_dsl'] is elasticsearch.dsl
