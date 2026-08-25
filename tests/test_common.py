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

from nomad.common import (
    has_glob_wildcards,
    is_email,
    is_safe_path,
    is_safe_relative_path,
    parse_timedelta,
)


@pytest.mark.parametrize(
    ('value', 'result'),
    [
        pytest.param('alice@example.com', True, id='valid-basic'),
        pytest.param('alice.smith+tag@sub.example.co', True, id='valid-subdomain-plus'),
        pytest.param("john.o'hara@example.com", True, id='valid-apostrophe'),
        pytest.param('alice', False, id='username-not-email'),
        pytest.param('.alice@example.com', False, id='local-part-starts-with-dot'),
        pytest.param('alice.@example.com', False, id='local-part-ends-with-dot'),
        pytest.param('alice..smith@example.com', False, id='consecutive-dots-local'),
        pytest.param('alice@localhost', False, id='missing-tld'),
        pytest.param('alice@example', False, id='missing-dot-in-domain'),
        pytest.param('alice@-example.com', False, id='domain-label-starts-with-hyphen'),
        pytest.param('alice@example-.com', False, id='domain-label-ends-with-hyphen'),
        pytest.param('alice@aol...com', False, id='consecutive-dots-domain'),
        pytest.param('alice@@example.com', False, id='double-at'),
        pytest.param('', False, id='empty'),
    ],
)
def test_is_email(value, result):
    assert is_email(value) is result


@pytest.mark.parametrize(
    'path, safe_path, is_directory, is_safe',
    [
        pytest.param(
            '/safe/a', '/safe/', True, True, id='safe absolute path to folder'
        ),
        pytest.param(
            '/safe/a/../b', '/safe/', True, True, id='safe relative path to folder'
        ),
        pytest.param(
            '/unsafe/../c', '/safe/', True, False, id='unsafe absolute path to folder'
        ),
        pytest.param(
            '/safe/../unsafe',
            '/safe/',
            True,
            False,
            id='unsafe relative path to folder',
        ),
        pytest.param(
            '/safe2/',
            '/safe',
            True,
            False,
            id='unsafe absolute path to folder with same prefix',
        ),
        pytest.param(
            '/safe/safe_file.zip', '/safe/safe_file.zip', False, True, id='safe file'
        ),
        pytest.param(
            '/safe2/unsafe_file.zip',
            '/safe/safe_file.zip',
            False,
            False,
            id='unsafe file',
        ),
        pytest.param(
            '/safe2/safe_file.zip2',
            '/safe/safe_file.zip',
            False,
            False,
            id='unsafe file with same prefix',
        ),
    ],
)
def test_is_safe_path(path, safe_path, is_directory, is_safe):
    assert is_safe_path(path, safe_path, is_directory) == is_safe


@pytest.mark.parametrize(
    'path, is_safe',
    [
        pytest.param('', True, id='root path implicit'),
        pytest.param('.', True, id='root path explicit'),
        pytest.param('subfolder', True, id='subfolder implicit'),
        pytest.param('./subfolder', True, id='subfolder excplicit'),
        pytest.param('/unsafe/a', False, id='absolute path'),
        pytest.param('../unsafe/a', False, id='outside root start'),
        pytest.param('subfolder/../../unsafe', False, id='outside root middle'),
        pytest.param('safe/../safe', True, id='redundant traversal'),
        pytest.param(None, False, id='Not string'),
        pytest.param('safe//', False, id='Invalid 1'),
        pytest.param('safe\n', False, id='Invalid 2'),
    ],
)
def test_is_safe_relative_path(path, is_safe):
    assert is_safe_relative_path(path) == is_safe


@pytest.mark.parametrize(
    'path, has_wildcards',
    [
        pytest.param('subfolder/file.txt', False, id='safe path'),
        pytest.param('', False, id='empty path'),
        pytest.param('folder/file[1].txt', True, id='square brackets'),
        pytest.param('*.txt', True, id='star at start'),
        pytest.param('file*', True, id='star at end'),
        pytest.param('file?.txt', True, id='question mark middle'),
        pytest.param('[abc]folder/file.txt', True, id='bracket group at start'),
    ],
)
def test_has_glob_wildcards(path, has_wildcards):
    assert has_glob_wildcards(path) == has_wildcards


@pytest.mark.parametrize(
    'unit, multiplier_seconds',
    [
        pytest.param('s', 1, id='seconds'),
        pytest.param('min', 60, id='minutes'),
        pytest.param('hours', 3600, id='hours'),
        pytest.param('d', 86400, id='days'),
        pytest.param('week', 7 * 86400, id='weeks'),
        pytest.param('months', 30 * 86400, id='months'),
        pytest.param('y', 365 * 86400, id='years'),
    ],
)
@pytest.mark.parametrize(
    'number_str, number',
    [
        pytest.param('2', 2.0, id='int'),
        pytest.param('1.5', 1.5, id='float'),
    ],
)
@pytest.mark.parametrize('spacer', ['', ' '], ids=['no-space', 'space'])
def test_parse_timedelta(unit, multiplier_seconds, number_str, number, spacer):
    value = f'{number_str}{spacer}{unit}'
    assert parse_timedelta(value).total_seconds() == pytest.approx(
        number * multiplier_seconds
    )


def test_parse_timedelta_warns_without_unit():
    with pytest.warns(UserWarning, match='No unit specified'):
        td = parse_timedelta('90')
    assert td.total_seconds() == 90 * 86400


@pytest.mark.parametrize(
    'value, match',
    [
        pytest.param('-1d', 'Duration must be non-negative', id='negative-duration'),
        pytest.param(
            '-1.5 seconds', 'Duration must be non-negative', id='negative-duration'
        ),
        pytest.param('abc', 'Invalid duration', id='missing-number'),
        pytest.param('1fortnight', 'Unsupported duration unit', id='unsupported-unit'),
        pytest.param('', 'Duration value must not be empty', id='empty-value'),
    ],
)
def test_parse_timedelta_invalid(value, match):
    with pytest.raises(ValueError, match=match):
        parse_timedelta(value)
