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

import pytest

from nomad.app.v1.utils import browser_download_headers, get_query_keys


@pytest.mark.parametrize(
    'source, exclude_keys, expected_keys',
    [
        pytest.param({'a': 1, 'b': 2}, None, {'a', 'b'}, id='simple_dict'),
        pytest.param([{'a': 1}, {'b': 2}], None, {'a', 'b'}, id='list_of_dicts'),
        pytest.param({'a': {'b': 1, 'c': 2}}, None, {'a.b', 'a.c'}, id='nested_dict'),
        pytest.param(
            {'a': [{'b': 1}, {'c': 2}]}, None, {'a.b', 'a.c'}, id='dict_with_list'
        ),
        pytest.param({'a': 1, 'b': 2}, ['a'], {'b'}, id='with_exclude'),
        pytest.param(
            {'a': {'b': 1, 'c': 2}}, ['a'], {'b', 'c'}, id='nested_with_exclude'
        ),
        pytest.param({}, None, set(), id='empty_dict'),
        pytest.param([], None, set(), id='empty_list'),
        pytest.param({'a': []}, None, {'a'}, id='dict_with_empty_list'),
        pytest.param({'a': {}}, None, {'a'}, id='dict_with_empty_dict'),
    ],
)
def test_get_query_keys(source, exclude_keys, expected_keys):
    assert get_query_keys(source, exclude_keys) == expected_keys


@pytest.mark.parametrize(
    'filename, expected_fallback, expected_encoded',
    [
        pytest.param('file.txt', 'file.txt', 'file.txt', id='plain'),
        pytest.param('my file.txt', 'my file.txt', 'my%20file.txt', id='space'),
        pytest.param('a"b.txt', 'a\\"b.txt', 'a%22b.txt', id='double quote'),
        pytest.param('a\\b.txt', 'a\\\\b.txt', 'a%5Cb.txt', id='backslash'),
        # Characters that cannot be sent verbatim in a header value. Without the
        # fallback substitution these would let a file name break the response.
        pytest.param(
            'a\r\nX-Evil: 1.txt',
            'a__X-Evil: 1.txt',
            'a%0D%0AX-Evil%3A%201.txt',
            id='crlf',
        ),
        pytest.param('a\tb.txt', 'a_b.txt', 'a%09b.txt', id='tab'),
        pytest.param('\u00e4\u00f6.txt', '__.txt', '%C3%A4%C3%B6.txt', id='non-ascii'),
    ],
)
def test_browser_download_headers(filename, expected_fallback, expected_encoded):
    headers = browser_download_headers(filename)
    assert headers['Content-Disposition'] == (
        f'attachment; filename="{expected_fallback}"; '
        f"filename*=UTF-8''{expected_encoded}"
    )
    # Every header value must be sendable as-is
    for value in headers.values():
        assert not any(c < ' ' or c == '\x7f' for c in value)
        value.encode('ascii')
