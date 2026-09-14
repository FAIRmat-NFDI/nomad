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
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi import status

from nomad.datamodel import results
from nomad.utils import deep_get
from tests.utils import assert_at_least, assert_url_query_args, build_url

n_code_names = results.Simulation.program_name.a_elasticsearch[
    0
].default_aggregation_size


def post_query_test_parameters(
    entity_id: str, total: int, material_prefix: str, entry_prefix: str
):
    """Convenience function for constructing POST query test parameters.

    Returns: List of pytest parameters for testing queries.
    """
    elements = f'{material_prefix}elements'
    program_name = f'{entry_prefix}results.method.simulation.program_name'
    method = f'{entry_prefix}results.method'
    properties = f'{entry_prefix}results.properties'
    upload_create_time = f'{entry_prefix}upload_create_time'

    return [
        pytest.param({}, 200, total, id='empty'),
        pytest.param('str', 422, -1, id='not-dict'),
        pytest.param({entity_id: 'id_01'}, 200, 1, id='match'),
        pytest.param({'mispelled': 'id_01'}, 422, -1, id='not-quantity'),
        pytest.param({entity_id: ['id_01', 'id_02']}, 200, 0, id='match-list-0'),
        pytest.param(
            {entity_id: 'id_01', elements: ['H', 'O']}, 200, 1, id='match-list-1'
        ),
        pytest.param({f'{entity_id}:any': ['id_01', 'id_02']}, 200, 2, id='any-short'),
        pytest.param({entity_id: {'any': ['id_01', 'id_02']}}, 200, 2, id='any'),
        pytest.param({entity_id: {'any': 'id_01'}}, 422, -1, id='any-not-list'),
        pytest.param({f'{entity_id}:any': 'id_01'}, 422, -1, id='any-short-not-list'),
        pytest.param({f'{entity_id}:gt': 'id_01'}, 200, total - 1, id='gt-short'),
        pytest.param({entity_id: {'gt': 'id_01'}}, 200, total - 1, id='gt'),
        pytest.param({entity_id: {'gt': ['id_01']}}, 422, total - 1, id='gt-list'),
        pytest.param({entity_id: {'missspelled': 'id_01'}}, 422, -1, id='not-op'),
        pytest.param({f'{entity_id}:lt': ['id_01']}, 422, -1, id='gt-shortlist'),
        pytest.param({f'{entity_id}:misspelled': 'id_01'}, 422, -1, id='not-op-short'),
        pytest.param(
            {'or': [{entity_id: 'id_01'}, {entity_id: 'id_02'}]}, 200, 2, id='or'
        ),
        pytest.param(
            {'or': {entity_id: 'id_01', program_name: 'VASP'}},
            422,
            -1,
            id='or-not-list',
        ),
        pytest.param(
            {'and': [{entity_id: 'id_01'}, {entity_id: 'id_02'}]}, 200, 0, id='and'
        ),
        pytest.param({'not': {entity_id: 'id_01'}}, 200, total - 1, id='not'),
        pytest.param({'not': [{entity_id: 'id_01'}]}, 422, -1, id='not-list'),
        pytest.param(
            {'not': {'not': {entity_id: 'id_01'}}}, 200, 1, id='not-nested-not'
        ),
        pytest.param(
            {'not': {f'{entity_id}:any': ['id_01', 'id_02']}},
            200,
            total - 2,
            id='not-nested-any',
        ),
        pytest.param(
            {
                'and': [
                    {f'{entity_id}:any': ['id_01', 'id_02']},
                    {f'{entity_id}:any': ['id_02', 'id_03']},
                ]
            },
            200,
            1,
            id='and-nested-any',
        ),
        pytest.param(
            {'and': [{'not': {entity_id: 'id_01'}}, {'not': {entity_id: 'id_02'}}]},
            200,
            total - 2,
            id='not-nested-not',
        ),
        pytest.param(
            {method: {'simulation.program_name': 'VASP'}}, 200, total, id='inner-object'
        ),
        pytest.param(
            {f'{properties}.electronic.dos_electronic.band_gap.type': 'direct'},
            200,
            1,
            id='nested-implicit',
        ),
        pytest.param(
            {f'{properties}.electronic.dos_electronic.band_gap': {'type': 'direct'}},
            200,
            1,
            id='nested-explicit',
        ),
        pytest.param(
            {properties: {'electronic.dos_electronic.band_gap': {'type': 'direct'}}},
            200,
            1,
            id='nested-explicit-explicit',
        ),
        pytest.param(
            {f'{upload_create_time}:gt': '1970-01-01'}, 200, total, id='date-1'
        ),
        pytest.param(
            {f'{upload_create_time}:lt': '2099-01-01'}, 200, total, id='date-2'
        ),
        pytest.param({f'{upload_create_time}:gt': '2099-01-01'}, 200, 0, id='date-3'),
    ]


def get_query_test_parameters(
    str: dict, int: dict, date: dict, subsection: dict, total: int
) -> list[Any]:
    """Convenience function for constructing GET query test parameters.

    Args:
        str: Contains name and values for a string quantity.
        int: Contains name and values for an int quantity.
        date: Contains name for a date quantity.
        nested_str: Contains name and values for a nested string quantity.

    Returns: List of pytest parameters for testing queries.
    """
    return [
        # Empty
        pytest.param({}, 200, total, id='empty'),
        # Errors
        pytest.param({'mispelled': 'no-value'}, 200, total, id='not-quantity'),
        # Match str
        pytest.param(
            {str['name']: str['values'][0]}, 200, str['total'], id='str-match'
        ),
        pytest.param(
            {str['name'] + '__any': str['values']},
            200,
            str['total_any'],
            id='str-match-many-any',
        ),
        pytest.param(
            {f'{str["name"]}__any': str['values'][0]},
            200,
            str['total'],
            id='str-any-not-list',
        ),
        pytest.param(
            {str['name'] + '__all': str['values']},
            200,
            str['total_all'],
            id='str-match-many-all',
        ),
        pytest.param(
            {f'{str["name"]}__missspelled': 'id_01'}, 422, -1, id='str-not-op'
        ),
        pytest.param(
            {f'{str["name"]}__gt': str['values'][0]}, 200, str['total_gt'], id='str-gt'
        ),
        pytest.param({f'{str["name"]}__gt': str['values']}, 422, -1, id='str-gt-list'),
        # Match int
        pytest.param(
            {int['name']: int['values'][0]}, 200, int['total'], id='int-match'
        ),
        pytest.param(
            {int['name'] + '__any': int['values']},
            200,
            int['total_any'],
            id='int-match-many-any',
        ),
        pytest.param(
            {int['name'] + '__all': int['values']},
            200,
            int['total_all'],
            id='int-match-many-all',
        ),
        pytest.param(
            {int['name'] + '__gt': int['values'][0]}, 200, int['total_gt'], id='int-gt'
        ),
        pytest.param({int['name'] + '__missspelled': 2}, 422, -1, id='int-not-op-2'),
        # Date
        pytest.param(
            {date['name'] + '__gt': '1970-01-01'}, 200, date['total'], id='date-gt'
        ),
        # Q query
        pytest.param(
            {'q': f'{date["name"]}__gt__1970-01-01'}, 200, date['total'], id='q-date-gt'
        ),
        pytest.param(
            {
                'q': [
                    f'{str["name"]}__all__{str["values"][0]}',
                    f'{str["name"]}__all__{str["values"][1]}',
                ]
            },
            200,
            str['total_all'],
            id='q-str-all-1',
        ),
        pytest.param(
            {
                'q': [
                    f'{str["name"]}__all__{str["values"][0]}',
                    f'{str["name"]}__all__notpresent',
                ]
            },
            200,
            0,
            id='q-str-all-2',
        ),
        pytest.param(
            {'q': f'{str["name"]}__{str["values"][0]}'},
            200,
            str['total'],
            id='q-str-match',
        ),
        pytest.param(
            {'q': f'{int["name"]}__{int["values"][0]}'},
            200,
            int['total'],
            id='q-int-match',
        ),
        pytest.param(
            {'q': f'{int["name"]}__gt__{int["values"][0]}'},
            200,
            int['total_gt'],
            id='q-int-gt',
        ),
        pytest.param({'q': 'bad_encoded'}, 422, -1, id='q-bad-encode'),
        pytest.param({'q': 'missspelled__id_01'}, 422, -1, id='q-bad-quantity'),
        # JSON
        pytest.param(
            {'json_query': f'{{"{str["name"]}:any": {json.dumps(str["values"])}}}'},
            200,
            str['total_any'],
            id='json_query',
        ),
        pytest.param(
            {'json_query': f'{{"{str["name"]}": ["H", "O"}}'},
            422,
            0,
            id='invalid-json_query',
        ),
        # Search subsection
        pytest.param(
            {subsection['name']: subsection['values'][0]},
            200,
            subsection['total'],
            id='subsection-match',
        ),
    ]


def owner_test_parameters():
    return [
        # (owner, user, status_code, total_entries, total_mainfiles)
        # Test missing authentication
        pytest.param('user', None, 401, -1, -1, id='user-wo-auth'),
        pytest.param('staging', None, 401, -1, -1, id='staging-wo-auth'),
        pytest.param('visible', None, 200, 23, 23, id='visible-wo-auth'),
        pytest.param('admin', None, 401, -1, -1, id='admin-wo-auth'),
        pytest.param('shared', None, 401, -1, -1, id='shared-wo-auth'),
        pytest.param('public', None, 200, 23, 23, id='public-wo-auth'),
        # Test accessing as `user1`
        pytest.param('user', 'user1', 200, 32, 30, id='user-user1'),
        pytest.param('staging', 'user1', 200, 6, 4, id='staging-user1'),
        pytest.param('visible', 'user1', 200, 32, 30, id='visible-user1'),
        pytest.param('admin', 'user1', 403, -1, -1, id='admin-user1'),
        pytest.param('shared', 'user1', 200, 32, 30, id='shared-user1'),
        pytest.param('public', 'user1', 200, 23, 23, id='public-user1'),
        # Test accessing as `user2`
        pytest.param('user', 'user2', 200, 0, 0, id='user-user2'),
        pytest.param('staging', 'user2', 200, 2, 2, id='staging-user2'),
        pytest.param('visible', 'user2', 200, 27, 27, id='visible-user2'),
        pytest.param('shared', 'user2', 200, 4, 4, id='shared-user2'),
        pytest.param('public', 'user2', 200, 23, 23, id='public-user2'),
        # Test owner as `all`
        pytest.param('all', 'user1', 403, 32, 30, id='metadata-all-user1'),
        pytest.param('all', 'user2', 403, 28, 28, id='metadata-all-user2'),
        pytest.param('all', 'invalid', 401, -1, -1, id='bad-credentials'),
        pytest.param('all', None, 403, 26, 26, id='metadata-all-wo-auth'),
        # Test accessing as `user0` (admin)
        pytest.param('admin', 'user0', 200, 32, 30, id='admin-user0'),
    ]


def pagination_test_parameters(
    elements: str, n_elements: str, crystal_system: str, total: int
):
    return [
        pytest.param(
            {},
            {'total': total, 'page_size': 10, 'next_page_after_value': 'id_10'},
            200,
            id='empty',
        ),
        pytest.param(
            {'page_size': 1},
            {'total': total, 'page_size': 1, 'next_page_after_value': 'id_01'},
            200,
            id='size',
        ),
        pytest.param(
            {'page_size': 0}, {'total': total, 'page_size': 0}, 200, id='size-0'
        ),
        pytest.param(
            {'page_size': 1, 'page_after_value': 'id_01'},
            {'page_after_value': 'id_01', 'next_page_after_value': 'id_02'},
            200,
            id='after',
        ),
        pytest.param(
            {'page_size': 1, 'page_after_value': 'id_02', 'order': 'desc'},
            {'next_page_after_value': 'id_01'},
            200,
            id='after-desc',
        ),
        pytest.param(
            {'page_size': 10, 'page_after_value': 'id_22', 'order': 'asc'},
            {'next_page_after_value': None},
            200,
            id='after-exhausted',
        ),
        pytest.param(
            {'page_size': 1, 'order_by': n_elements},
            {'next_page_after_value': '2:id_01'},
            200,
            id='order-by-after-int',
        ),
        pytest.param(
            {'page_size': 1, 'order_by': crystal_system},
            {'next_page_after_value': 'cubic:id_01'},
            200,
            id='order-by-after-nested',
        ),
        pytest.param({'page_size': -1}, None, 422, id='bad-size'),
        pytest.param({'order': 'misspelled'}, None, 422, id='bad-order'),
        pytest.param({'order_by': 'misspelled'}, None, 422, id='bad-order-by'),
        pytest.param(
            {'order_by': elements, 'page_after_value': 'H:id_01'},
            None,
            422,
            id='order-by-list',
        ),
        pytest.param(
            {'order_by': n_elements, 'page_after_value': 'some'},
            None,
            400,
            id='order-by-bad-after',
        ),
        pytest.param(
            {'page_offset': 0, 'page_size': 1},
            {'total': total, 'next_page_after_value': 'id_01', 'page_offset': 0},
            200,
            id='page-offset-1',
        ),
        pytest.param(
            {'page_offset': 1, 'page_size': 1},
            {'total': total, 'next_page_after_value': 'id_02', 'page_offset': 1},
            200,
            id='page-offset-2',
        ),
        pytest.param({'page_offset': 9999}, None, 422, id='page-offset-too-large'),
        pytest.param(
            {'page_offset': 9989}, None, 200, id='page-offset-just-small-enough'
        ),
        pytest.param(
            {'page': 1, 'page_size': 1},
            {
                'total': total,
                'page_size': 1,
                'next_page_after_value': 'id_01',
                'page': 1,
            },
            200,
            id='page-1',
        ),
        pytest.param(
            {'page': 2, 'page_size': 1},
            {
                'total': total,
                'page_size': 1,
                'next_page_after_value': 'id_02',
                'page': 2,
            },
            200,
            id='page-2',
        ),
        pytest.param({'page': 1000, 'page_size': 10}, None, 422, id='page-too-large'),
        pytest.param(
            {'page': 9999, 'page_size': 1}, None, 200, id='page-just-small-enough'
        ),
        pytest.param(
            {'page_offset': 1, 'page': 1}, None, 422, id='only-one-page-param'
        ),
        pytest.param(
            {'page_offset': 1, 'page_size': 0},
            None,
            422,
            id='page-param-only-with-page-size',
        ),
    ]


def aggregation_test_parameters(
    str: dict,
    enum: dict,
    bool: dict,
    int: dict,
    pagination: dict,
    pagination_order_by: dict | None,
    histogram_int: dict,
    histogram_date: dict,
    include: dict,
    metrics: dict,
    empty: dict,
    fixed: dict | None,
) -> list[Any]:
    """Convenience function for constructing aggregation tests.

    Args:
        str: Contains name, total, and size for a string quantity.
        enum: Contains name, total, and size for an enum quantity.
        bool: Contains name, total, and size for a bool quantity.
        int: Contains name, total, and size for an int quantity.
        pagination: Contains name, total, size, page_after_value, page_after_value_tiebreaker and page_after_value_size for a quantity that should be paginated.
        pagination_order_by: Contains name_str, name_date, name_int for testing pagination by different field types.
        histogram_int: Contains name, interval, interval_size, buckets and bucket_size for testing int histogram.
        histogram_date: Contains name, default_size, interval and interval_size for testing date histogram.
        histogram_date: Contains name, default_size, interval and interval_size for testing date histogram.
        include: Contains name, include, total and size for testing include.
        metrics: Contains name, total and size for testing metrics.
        empty: Contains name for testing quantity that should not contain results.
        fixed: Contains name, total and size for testing quantity with fixed number or returned terms.

    Returns: List of pytest parameters for testing aggregation calls.
    """
    default_size = 10
    default_name = str['name']
    default_total = str['total']
    section_path = str['name'].rsplit('.', 1)[0] + '.*'

    tests = [
        # Terms
        pytest.param(
            {'terms': {'quantity': str['name']}},
            str['total'],
            min(str['size'], default_size),
            200,
            'user1',
            id='terms-str',
        ),
        pytest.param(
            {'terms': {'quantity': enum['name']}},
            enum['total'],
            min(enum['size'], default_size),
            200,
            'user1',
            id='terms-enum',
        ),
        pytest.param(
            {'terms': {'quantity': bool['name']}},
            bool['total'],
            min(bool['size'], default_size),
            200,
            'user1',
            id='terms-bool',
        ),
        pytest.param(
            {'terms': {'quantity': empty['name']}},
            0,
            0,
            200,
            'user1',
            id='no-results',
        ),
        pytest.param(
            {'terms': {'quantity': default_name, 'entries': {'size': 10}}},
            default_total,
            min(default_total, default_size),
            200,
            'user1',
            id='entries',
        ),
        pytest.param(
            {'terms': {'quantity': default_name, 'entries': {'size': 1}}},
            default_total,
            min(default_total, default_size),
            200,
            'user1',
            id='entries-size',
        ),
        pytest.param(
            {'terms': {'quantity': default_name, 'entries': {'size': 0}}},
            -1,
            -1,
            422,
            'user1',
            id='bad-entries',
        ),
        pytest.param(
            {
                'terms': {
                    'quantity': default_name,
                    'entries': {
                        'size': 10,
                        'required': {'include': [default_name, section_path]},
                    },
                }
            },
            str['total'],
            min(str['size'], default_size),
            200,
            'user1',
            id='entries-include',
        ),
        pytest.param(
            {
                'terms': {
                    'quantity': default_name,
                    'entries': {
                        'size': 10,
                        'required': {'exclude': ['files', 'mainfile*']},
                    },
                }
            },
            str['total'],
            min(str['size'], default_size),
            200,
            'user1',
            id='entries-exclude',
        ),
        pytest.param(
            {'terms': {'quantity': default_name, 'size': default_total - 1}},
            default_total,
            default_total - 1,
            200,
            None,
            id='size',
        ),
        pytest.param(
            {'terms': {'quantity': default_name, 'size': 1000}},
            default_total,
            default_total,
            200,
            None,
            id='size-too-large',
        ),
        pytest.param(
            {'terms': {'quantity': default_name}},
            default_total,
            default_size,
            200,
            None,
            id='size-default',
        ),
        pytest.param(
            {'terms': {'quantity': default_name, 'size': -1}},
            -1,
            -1,
            422,
            None,
            id='bad-size-1',
        ),
        pytest.param(
            {'terms': {'quantity': default_name, 'size': 0}},
            -1,
            -1,
            422,
            None,
            id='bad-size-2',
        ),
        pytest.param(
            {'terms': {'quantity': 'does not exist'}},
            -1,
            -1,
            422,
            None,
            id='bad-quantity',
        ),
        pytest.param(
            {'terms': {'quantity': pagination['name'], 'pagination': {'order': 'asc'}}},
            pagination['total'],
            min(pagination['size'], default_size),
            200,
            'user1',
            id='order-direction',
        ),
        pytest.param(
            {
                'terms': {
                    'quantity': pagination['name'],
                    'pagination': {'page_after_value': pagination['page_after_value']},
                }
            },
            pagination['total'],
            pagination['page_after_value_size'],
            200,
            'user1',
            id='after',
        ),
        pytest.param(
            {'terms': {'quantity': metrics['name'], 'metrics': ['n_uploads']}},
            metrics['total'],
            metrics['size'],
            200,
            None,
            id='metrics',
        ),
        pytest.param(
            {'terms': {'quantity': metrics['name'], 'metrics': ['does not exist']}},
            -1,
            -1,
            422,
            None,
            id='bad-metric',
        ),
        pytest.param(
            {'terms': {'quantity': include['name'], 'include': include['include']}},
            include['total'],
            include['size'],
            200,
            None,
            id='terms-include',
        ),
        pytest.param(
            {'terms': {'quantity': include['name'], 'include': '.*_0.*'}},
            -1,
            -1,
            422,
            None,
            id='terms-bad-include',
        ),
        # Date histogram
        pytest.param(
            {'date_histogram': {'quantity': histogram_date['name']}},
            histogram_date['default_size'],
            histogram_date['default_size'],
            200,
            'user1',
            id='date-histogram-default',
        ),
        pytest.param(
            {
                'date_histogram': {
                    'quantity': histogram_date['name'],
                    'interval': histogram_date['interval'],
                }
            },
            histogram_date['interval_size'],
            histogram_date['interval_size'],
            200,
            'user1',
            id='date-histogram-interval',
        ),
        pytest.param(
            {
                'date_histogram': {
                    'quantity': histogram_date['name'],
                    'interval': histogram_date['interval'],
                    'metrics': ['n_uploads'],
                }
            },
            histogram_date['interval_size'],
            histogram_date['interval_size'],
            200,
            'user1',
            id='date-histogram-metrics',
        ),
        pytest.param(
            {'date_histogram': {'quantity': str['name']}},
            -1,
            -1,
            422,
            'user1',
            id='date-histogram-no-date',
        ),
        pytest.param(
            {'date_histogram': {'quantity': histogram_date['name'], 'interval': '1xy'}},
            -1,
            -1,
            400,
            'user1',
            id='date-histogram-bad-interval',
        ),
        # Int histogram
        pytest.param(
            {
                'histogram': {
                    'quantity': histogram_int['name'],
                    'interval': histogram_int['interval'],
                }
            },
            histogram_int['interval_size'],
            histogram_int['interval_size'],
            200,
            None,
            id='histogram-interval',
        ),
        pytest.param(
            {
                'histogram': {
                    'quantity': histogram_int['name'],
                    'buckets': histogram_int['buckets'],
                }
            },
            histogram_int['bucket_size'],
            histogram_int['bucket_size'],
            200,
            'user1',
            id='histogram-buckets',
        ),
        pytest.param(
            {
                'histogram': {
                    'quantity': histogram_int['name'],
                    'interval': histogram_int['interval'],
                    'metrics': ['n_uploads'],
                }
            },
            histogram_int['interval_size'],
            histogram_int['interval_size'],
            200,
            None,
            id='histogram-metric',
        ),
        pytest.param(
            {'histogram': {'quantity': histogram_int['name']}},
            -1,
            -1,
            422,
            None,
            id='histogram-no-interval-or-buckets',
        ),
        pytest.param(
            {'histogram': {'quantity': str['name']}},
            -1,
            -1,
            422,
            None,
            id='histogram-no-number',
        ),
        # Min-max
        pytest.param(
            {'min_max': {'quantity': int['name']}}, 1, 1, 200, None, id='min-max'
        ),
        pytest.param(
            {'min_max': {'quantity': str['name']}},
            -1,
            -1,
            422,
            None,
            id='min-max-no-number',
        ),
    ]

    # Optional tests for terms aggregation for fields with fixed number of return values
    if fixed:
        tests += [
            pytest.param(
                {'terms': {'quantity': fixed['name']}},
                fixed['total'],
                fixed['size'],
                200,
                None,
                id='fixed-values',
            )
        ]
    # Optional tests for using order_by in pagination
    if pagination_order_by:
        tests += [
            pytest.param(
                {
                    'terms': {
                        'quantity': pagination['name'],
                        'pagination': {'order_by': pagination_order_by['name_str']},
                    }
                },
                pagination['total'],
                min(pagination['size'], default_size),
                200,
                'user1',
                id='order-str',
            ),
            pytest.param(
                {
                    'terms': {
                        'quantity': pagination['name'],
                        'pagination': {'order_by': pagination_order_by['name_date']},
                    }
                },
                pagination['total'],
                min(pagination['size'], default_size),
                200,
                'user1',
                id='order-date',
            ),
            pytest.param(
                {
                    'terms': {
                        'quantity': pagination['name'],
                        'pagination': {'order_by': pagination_order_by['name_int']},
                    }
                },
                pagination['total'],
                min(pagination['size'], default_size),
                200,
                'user1',
                id='order-int',
            ),
            pytest.param(
                {
                    'terms': {
                        'quantity': pagination['name'],
                        'pagination': {
                            'order_by': pagination_order_by['name_str'],
                            'page_after_value': pagination[
                                'page_after_value_tiebreaker'
                            ],
                        },
                    }
                },
                pagination['total'],
                pagination['page_after_value_size'],
                200,
                'user1',
                id='after-order',
            ),
        ]

    return tests


def aggregation_test_parameters_default():
    """Convenience function for constructing default aggregation tests.

    Returns: List of pytest parameters for testing aggregation calls.
    """
    return aggregation_test_parameters(
        str={'name': 'entry_id', 'total': 23, 'size': 32},
        empty={'name': 'results.material.symmetry.structure_name'},
        enum={
            'name': 'results.material.dimensionality',
            'total': 1,
            'size': 1,
        },
        bool={
            'name': 'results.properties.electronic.dos_electronic.spin_polarized',
            'total': 2,
            'size': 2,
        },
        int={'name': 'results.properties.n_calculations'},
        pagination={
            'name': 'upload_id',
            'total': 8,
            'size': 8,
            'page_after_value': 'id_published',
            'page_after_value_tiebreaker': 'Sheldon Cooper:id_published',
            'page_after_value_size': 3,
        },
        histogram_int={
            'name': 'results.properties.n_calculations',
            'interval': 1,
            'buckets': 10,
            'interval_size': 1,
            'bucket_size': 1,
        },
        histogram_date={
            'name': 'upload_create_time',
            'interval': '1d',
            'interval_size': 1,
            'default_size': 1,
        },
        include={
            'name': 'entry_id',
            'include': '_0',
            'total': 9,
            'size': 9,
        },
        metrics={
            'name': 'results.method.simulation.program_name',
            'total': n_code_names,
            'size': n_code_names,
        },
        fixed={
            'name': 'results.method.simulation.program_name',
            'total': n_code_names,
            'size': n_code_names,
        },
        pagination_order_by={
            'name_str': 'main_author.name',
            'name_int': 'results.properties.n_calculations',
            'name_date': 'upload_create_time',
        },
    )


def aggregation_exclude_from_search_test_parameters(total_per_entity: int, total: int):
    entry_id = 'entry_id'
    upload_id = 'upload_id'
    program_name = 'results.method.simulation.program_name'
    n_elements = 'results.material.n_elements'
    band_gap = 'results.properties.electronic.band_structure_electronic.band_gap.value'

    def make_aggs(aggs):
        """Given a list of aggregation definitions, returns the API-compatible
        aggregations together with their types and expected result sizes.
        """
        aggs_api = {}
        types = []
        lengths = []
        for i, agg in enumerate(aggs):
            outer_agg = {f'{agg["type"]}': agg}
            aggs_api[f'agg_{i}'] = outer_agg
            types.append(agg['type'])
            lengths.append(agg['n_results'])
            del agg['n_results']
            del agg['type']
        return [aggs_api, types, lengths]

    return [
        pytest.param(
            {
                f'{entry_id}:any': ['id_01'],
                upload_id: 'id_published',
                program_name: 'VASP',
            },
            make_aggs([]),
            1,
            200,
            id='empty',
        ),
        pytest.param(
            {f'{entry_id}:any': ['id_01']},
            make_aggs(
                [
                    {
                        'type': 'terms',
                        'quantity': entry_id,
                        'exclude_from_search': True,
                        'n_results': 10,
                    }
                ]
            ),
            1,
            200,
            id='exclude',
        ),
        pytest.param(
            {f'{entry_id}:any': ['id_01']},
            make_aggs(
                [
                    {
                        'type': 'terms',
                        'quantity': entry_id,
                        'exclude_from_search': False,
                        'n_results': total_per_entity,
                    }
                ]
            ),
            1,
            200,
            id='dont-exclude',
        ),
        pytest.param(
            {
                f'{entry_id}:any': ['id_01'],
                upload_id: 'id_published',
                program_name: 'VASP',
            },
            make_aggs(
                [
                    {
                        'type': 'terms',
                        'quantity': entry_id,
                        'exclude_from_search': True,
                        'n_results': 10,
                    },
                    {
                        'type': 'terms',
                        'quantity': upload_id,
                        'exclude_from_search': True,
                        'n_results': 1,
                    },
                ]
            ),
            1,
            200,
            id='two-aggs',
        ),
        pytest.param(
            {f'{entry_id}:any': ['id_01']},
            make_aggs(
                [
                    {
                        'type': 'terms',
                        'quantity': entry_id,
                        'exclude_from_search': True,
                        'n_results': 10,
                    },
                    {
                        'type': 'terms',
                        'quantity': entry_id,
                        'exclude_from_search': False,
                        'n_results': total_per_entity,
                    },
                ]
            ),
            1,
            200,
            id='two-aggs-same-quantity',
        ),
        pytest.param(
            {},
            make_aggs(
                [
                    {
                        'type': 'terms',
                        'quantity': entry_id,
                        'exclude_from_search': True,
                        'n_results': 10,
                    }
                ]
            ),
            total,
            200,
            id='not-in-query',
        ),
        pytest.param(
            {},
            make_aggs(
                [
                    {
                        'type': 'terms',
                        'quantity': entry_id,
                        'exclude_from_search': True,
                        'pagination': {'page_size': 20},
                        'n_results': 20,
                    }
                ]
            ),
            total,
            422,
            id='with-pagination',
        ),
        pytest.param(
            {},
            make_aggs(
                [
                    {
                        'type': 'terms',
                        'quantity': entry_id,
                        'size': 20,
                        'exclude_from_search': True,
                        'n_results': 20,
                    }
                ]
            ),
            total,
            200,
            id='with-size',
        ),
        pytest.param(
            {'or': [{entry_id: 'id_01'}, {entry_id: 'id_05'}]},
            make_aggs(
                [
                    {
                        'type': 'terms',
                        'quantity': entry_id,
                        'exclude_from_search': True,
                        'n_results': 10,
                    }
                ]
            ),
            2,
            200,
            id='non-dict-query',
        ),
        pytest.param(
            {f'{n_elements}': {'gte': 0, 'lte': 10}},
            make_aggs(
                [
                    {
                        'type': 'min_max',
                        'quantity': n_elements,
                        'exclude_from_search': True,
                        'n_results': 2,
                    }
                ]
            ),
            total,
            200,
            id='range_min_max',
        ),
        pytest.param(
            {f'{band_gap}': {'gte': 0, 'lte': 10}},
            make_aggs(
                [
                    {
                        'type': 'min_max',
                        'quantity': band_gap,
                        'exclude_from_search': True,
                        'n_results': 2,
                    }
                ]
            ),
            0,
            200,
            id='nested_range_min_max',
        ),
    ]


def assert_response(response, status_code: int | None = None) -> None:
    """General assertions for status code and error messages"""
    if status_code is None:
        return

    actual_status: int = response.status_code

    if actual_status != status_code:
        try:
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))
        except Exception:
            pass

        if actual_status == status.HTTP_422_UNPROCESSABLE_CONTENT:
            pytest.fail(
                f'Expected {status_code}, but got 422 Unprocessable Entity.\n'
                f'Detail: {response.json()["detail"]}'
            )
        else:
            pytest.fail(
                f'Expected {status_code}, but got {actual_status}.\n'
                f'Response JSON: {response.json()}'
            )

    if status_code == status.HTTP_422_UNPROCESSABLE_CONTENT:
        response_json = response.json()
        details = response_json['detail']
        assert len(details) > 0
        for detail in details:
            assert 'loc' in detail
            assert 'msg' in detail
        return

    if 400 <= status_code < 500:
        response_json = response.json()
        assert 'detail' in response_json


def assert_base_metadata_response(response, status_code=None):
    assert_response(response, status_code)

    if status_code != 200 or response.status_code != 200:
        return None

    response_json = response.json()
    assert 'es_query' not in response_json
    assert 'data' in response_json
    return response_json


def assert_metadata(response_json):
    if isinstance(response_json['data'], list):
        metadatas = response_json['data']
    else:
        metadatas = [response_json['data']]

    for metadata in metadatas:
        if 'required' not in response_json:
            assert 'license' in metadata
            if 'entry_id' in metadata:
                if metadata['entry_id'].startswith('id_child_entries_child'):
                    assert metadata['mainfile_key']
                else:
                    assert 'mainfile_key' not in metadata

        if 'main_author' in metadata:
            assert 'email' not in metadata['main_author']


def assert_metadata_response(response, status_code=None):
    response_json = assert_base_metadata_response(response, status_code=status_code)
    if response_json is not None:
        assert_metadata(response_json)
    return response_json


def assert_required(data, required, default_key: str):
    # We flat out all keys in data and then make sure that the full qualified keys in the
    # data are consistent with the keys given in the required include and exclude.
    keys: set[str] = set()

    def collect_keys(data, prefix=None):
        if isinstance(data, list):
            for item in data:
                collect_keys(item, prefix=prefix)

        elif isinstance(data, dict):
            for key, value in data.items():
                collect_keys(
                    value, prefix=f'{prefix}.{key}' if prefix is not None else key
                )

        else:
            keys.add(prefix)

    collect_keys(data)

    if 'include' in required:
        for key in keys:
            found_include = False
            for include in required['include']:
                include_re = include.replace('.', r'\.').replace('*', r'[^\.\*]*')
                if key == default_key or re.match(include_re, key):
                    found_include = True

            assert found_include, key
    if 'exclude' in required:
        for exclude in required['exclude']:
            found_exclude = None
            for key in keys:
                exclude_re = exclude.replace('.', r'\.').replace('*', r'[^\.\*]*')
                if key != default_key and re.match(exclude_re, key):
                    found_exclude = key

            assert found_exclude is None, (
                f'{exclude} excluded but found {found_exclude}'
            )


def assert_aggregations(
    response_json,
    name,
    agg,
    total: int = -1,
    size: int = -1,
    default_key: str = '',
):
    assert 'aggregations' in response_json
    assert name in response_json['aggregations']
    agg_response_obj = response_json['aggregations'][name]
    assert len(agg_response_obj) == 1
    agg_type = next(iter(agg_response_obj.keys()))
    agg_response = agg_response_obj[agg_type]

    assert 'data' in agg_response
    if agg_type != 'statistics':
        assert 'quantity' in agg_response

    assert_at_least(agg, agg_response)

    data = agg_response['data']
    n_data = len(data)

    if 'pagination' in agg:
        assert agg_response['pagination']['total'] >= n_data
        if size >= 0:
            assert n_data == size
        if total >= 0:
            assert agg_response['pagination']['total'] == total

        assert_pagination(
            agg.get('pagination', {}), agg_response['pagination'], data, is_get=False
        )

    if agg_type == 'min_max':
        assert len(data) == 2
        assert isinstance(data[0], float | int)
        assert isinstance(data[1], float | int)
    elif agg_type == 'percentiles':
        assert isinstance(data, list)
        assert len(data) >= 1
        for item in data:
            assert 'percentiles' in item, 'Missing percentiles dict in response'
            assert isinstance(item['percentiles'], dict)
            assert 'count' in item
            assert isinstance(item['count'], int)
    elif agg_type == 'statistics':
        assert 'metrics' in agg_response
        for metric in agg.get('metrics', []):
            assert metric in data
            assert isinstance(data[metric], float | int)
    else:
        assert total == -1 or total >= n_data
        assert size == -1 or size == n_data

        for bucket in data:
            assert 'value' in bucket
            if len(agg.get('metrics', [])) > 0:
                assert 'metrics' in bucket
            else:
                assert 'metrics' not in bucket
            assert 'count' in bucket

            value = bucket['value']
            if agg_type == 'date_histogram':
                assert re.match(r'\d{4}\-\d{2}\-\d{2}', value)
            elif agg_type == 'histogram':
                assert isinstance(value, float | int)

            for metric in agg.get('metrics', []):
                assert metric in bucket['metrics']
                assert isinstance(bucket['metrics'][metric], float | int)

    if 'entries' in agg:
        for bucket in data:
            assert 'entries' in bucket
            assert agg['entries'].get('size', 10) >= len(bucket['entries']) > 0
            if 'required' in agg['entries']:
                for entry in bucket['entries']:
                    assert_required(
                        entry, agg['entries']['required'], default_key=default_key
                    )


def assert_query_response(client, test_method, query, total, status_code):
    """Checks that the query response is as expected."""
    response_json = test_method(
        client, query=query, status_code=status_code, total=total, http_method='get'
    )

    if response_json is None:
        return

    if 'pagination' not in response_json:
        return

    response = client.get(f'entries?{urlencode(query, doseq=True)}')

    response_json = assert_metadata_response(response, status_code=status_code)

    if response_json is None:
        return

    pagination = response_json['pagination']
    assert pagination['total'] == total
    assert pagination['page_size'] == 10
    assert pagination['order_by'] == 'entry_id'
    assert pagination['order'] == 'asc'
    assert ('next_page_after_value' in pagination) == (total > 10)


def assert_aggregation_response(
    auth_headers,
    client,
    aggregation,
    total,
    size,
    status_code,
    user,
):
    """Checks that the aggregation response is as expected."""

    agg_id = 'test_agg_name'
    aggregations = {agg_id: aggregation}
    default_key = 'entry_id'
    metadata_test = perform_entries_metadata_test

    response_json = metadata_test(
        client,
        headers=auth_headers[user],
        owner='visible',
        aggregations=aggregations,
        pagination=dict(page_size=0),
        status_code=status_code,
        http_method='post',
    )

    if response_json is None:
        return

    for aggregation_obj in aggregation.values():
        assert_aggregations(
            response_json,
            agg_id,
            aggregation_obj,
            total=total,
            size=size,
            default_key=default_key,
        )


def assert_pagination(
    pagination, pagination_response, data, order_by=None, order=None, is_get=True
):
    assert_at_least(pagination, pagination_response)
    assert len(data) <= pagination_response['page_size']
    assert len(data) <= pagination_response['total']

    if order is None:
        order = pagination_response.get('order', 'asc')
    if order_by is None:
        order_by = pagination_response.get('order_by')

    if order_by is not None:
        for index, item in enumerate(data):
            if index < len(data) - 1 and order_by in item:
                if order == 'desc':
                    assert item[order_by] >= data[index + 1][order_by]
                else:
                    assert item[order_by] <= data[index + 1][order_by]

    if is_get:
        page_size = pagination_response['page_size']
        page = pagination_response.get('page')
        page_url = pagination_response.get('page_url')
        first_page_url = pagination_response.get('first_page_url')
        prev_page_url = pagination_response.get('prev_page_url')
        next_page_url = pagination_response.get('next_page_url')
        next_page_after_value = pagination_response.get('next_page_after_value')

        assert page_url
        if page_size:
            assert first_page_url
            assert_url_query_args(first_page_url, page_after_value=None, page=None)
        if next_page_after_value:
            assert next_page_url
            assert_url_query_args(
                next_page_url, page_after_value=next_page_after_value, page=None
            )
        if page and page > 1:
            assert prev_page_url
            assert_url_query_args(prev_page_url, page=page - 1, page_after_value=None)


def assert_browser_download_headers(response, media_type: str, filename: str):
    if media_type:
        assert (
            response.headers['Content-Type'].split(';')[0]
            == media_type.split(';', maxsplit=1)[0]
        )
    content_disposition = response.headers['Content-Disposition']
    assert 'attachment;' in content_disposition
    if filename:
        filename_in_header = content_disposition.split('filename="')[1][:-1]
        assert filename_in_header == filename


def perform_metadata_test(
    client,
    endpoint: str,
    owner=None,
    headers={},
    status_code=200,
    total=None,
    http_method='get',
    **kwargs,
):
    # Currently operations with `owner=all` would be allowed for the `entries`
    # endpoints only, others would reject with status code 403
    if status_code == 403 and owner == 'all':
        status_code = 200

    if http_method == 'get':
        params = {}
        if owner is not None:
            params['owner'] = owner
        for value in kwargs.values():
            params.update(**value)
        response = client.get(
            f'{endpoint}?{urlencode(params, doseq=True)}', headers=headers
        )

    elif http_method == 'post':
        body = dict(**kwargs)
        if owner is not None:
            body['owner'] = owner
        response = client.post(f'{endpoint}/query', headers=headers, json=body)

    else:
        assert False

    response_json = assert_metadata_response(response, status_code=status_code)

    if response_json is None:
        return

    assert 'pagination' in response_json

    if total is not None and total >= 0:
        assert response_json['pagination']['total'] == total, response_json[
            'pagination'
        ]['total']

    return response_json


def perform_entries_metadata_test(*args, **kwargs):
    kwargs.update(endpoint='entries')
    return perform_metadata_test(*args, **kwargs)


def perform_owner_test(
    auth_headers,
    client,
    owner,
    user,
    status_code,
    total,
    http_method,
    test_method,
):
    test_method(
        client,
        headers=auth_headers[user],
        owner=owner,
        status_code=status_code,
        total=total,
        http_method=http_method,
    )


def perform_quantity_search_test(quantity, search, result, client):
    body = {'query': {f'{quantity}': search}}
    response = client.post(f'entries/query', json=body, headers={})
    assert_response(response, 200)
    response = response.json()
    api_result = deep_get(response['data'][0], *quantity.split('.'))
    assert api_result == result


def build_headers(accept: str | None = None, user_auth: dict | None = None):
    headers = {}
    if accept:
        headers['Accept'] = accept
    if user_auth:
        headers.update(user_auth)

    return headers


def perform_get(
    client, base_url, user_auth=None, accept='application/json', **query_args
):
    headers = build_headers(accept, user_auth)
    url = build_url(base_url, query_args)
    response = client.get(url, headers=headers)
    return response


def perform_post(
    client,
    base_url,
    user_auth=None,
    accept='application/json',
    data=None,
    json=None,
    **query_args,
):
    headers = build_headers(accept, user_auth)
    url = build_url(base_url, query_args)
    response = client.post(url, headers=headers, data=data, json=json)
    return response


def perform_delete(
    client, base_url, user_auth=None, accept='application/json', **query_args
):
    headers = build_headers(accept, user_auth)
    url = build_url(base_url, query_args)
    response = client.delete(url, headers=headers)
    return response
