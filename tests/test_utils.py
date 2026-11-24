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

import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError

import pandas as pd
import pytest

from nomad import files, utils
from nomad.metainfo.metainfo import MSection, Quantity, SubSection
from nomad.processing import Upload
from nomad.utils import (
    dataframe_to_dict,
    deep_get,
    dict_to_dataframe,
    flatten_dict,
    nomad_distro_metadata,
    prune_dict,
    rebuild_dict,
    structlogging,
)
from nomad.utils.structlogging import ISO8601_UTC_FORMAT


def test_decode_handle_id():
    assert utils.decode_handle_id('a1') == 321
    assert utils.decode_handle_id('6i370') == 6884576
    with pytest.raises(ValueError):
        utils.decode_handle_id('zz')


def test_timer(log_output):
    with utils.timer(utils.get_logger('test_logger'), 'test measure'):
        time.sleep(0.1)

    assert log_output.entries[0]['event'] == 'test measure'


def test_sleep_timer():
    sleep = utils.SleepTimeBackoff(start_time=0.1)
    start = time.time()

    for _ in range(0, 3):
        sleep()

    duration = time.time() - start
    assert duration > 0.7
    assert duration < 1


def test_sanitize_logevent():
    assert structlogging.sanitize_logevent('numbers 2 and 45.2') == 'numbers X and X'
    assert (
        structlogging.sanitize_logevent('list [2, 3.3, 10] and (273.9, .92)')
        == 'list L and L'
    )
    assert structlogging.sanitize_logevent('mat [2, [3.3, 2], 10]') == 'mat M'


def test_iso8601_datetime_format():
    dummy_time = datetime(2025, 9, 2, 12, 34, 56, 789)

    assert dummy_time.strftime(ISO8601_UTC_FORMAT) == '2025-09-02T12:34:56Z'


@pytest.mark.parametrize(
    'value',
    [
        pytest.param(
            datetime(2026, 1, 2, 3, 4, 5),
            id='naive->utc',
        ),
        pytest.param(
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            id='aware-utc->same',
        ),
    ],
)
def test_normalize_datetime_utc(value):
    assert utils.normalize_datetime_utc(value) == datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=UTC
    )


def test_normalize_datetime_utc_none():
    assert utils.normalize_datetime_utc(None) is None


def test_logging(no_warn):
    utils.get_logger(__name__).info('test msg')

    received_test_event = False
    for record in no_warn.entries:
        assert record['log_level'] == 'info'
        assert 'event' in record
        assert record['event'] == 'test msg'
        received_test_event = True
    assert received_test_event


def test_common_prefix():
    assert utils.common_prefix(['aa/bb/cc', 'aa/bb/dd']) == 'aa/bb/'
    assert utils.common_prefix(['aa/bb/dc', 'aa/bb/d']) == 'aa/bb/'
    assert utils.common_prefix(['aa/b/dc', 'aa/bb/d']) == 'aa/'
    assert utils.common_prefix(['a', 'a']) == ''
    assert utils.common_prefix(['a', 'ab']) == ''
    assert utils.common_prefix(['/a', '/a']) == '/'


def test_uuid():
    uuid = utils.create_uuid()
    assert uuid is not None


def test_class_logger():
    logger = utils.ClassicLogger(__name__, test='value')
    logger.warn('hello world', test='other value')


class DummySubSection(MSection):
    name = Quantity(type=str)
    subsection_number = Quantity(type=int)


class DummySection(MSection):
    quantity_section = Quantity(type=int)
    subsection = SubSection(sub_section=DummySubSection, repeats=True)


def test_extract_section():
    test_section = DummySection(
        quantity_section=5,
        subsection=[
            DummySubSection(name='subsection1', subsection_number=1),
            DummySubSection(name='subsection2'),
        ],
    )
    # Check if quantities are properly extracted
    assert utils.extract_section(test_section, ['quantity_section']) == 5
    # Check if last section is properly extracted:
    path = ['subsection']
    assert utils.extract_section(test_section, path).name == 'subsection2'
    # Check if last section in path is not a repeated section
    assert not isinstance(utils.extract_section(test_section, path), list)
    # Check if full_list works
    assert isinstance(utils.extract_section(test_section, path, full_list=True), list)
    assert len(utils.extract_section(test_section, path, full_list=True)) == 2
    # Check if not section is reported if the last of the intermediate elements do not contain it
    path.append('subsection_number')
    assert not utils.extract_section(test_section, path)


def create_upload(upload_id, user_id, file_paths=None):
    if file_paths is None:
        file_paths = []
    upload = Upload(upload_id=upload_id, main_author=user_id)
    upload.save()
    files.StagingUploadFiles(upload_id=upload.upload_id, create=True)
    for file_path in file_paths:
        upload.staging_upload_files.add_rawfiles(file_path)
    upload.process_upload()
    upload.block_until_complete()
    return upload


@pytest.mark.parametrize(
    'data, flatten_list',
    [
        pytest.param({}, True, id='empty'),
        pytest.param({'a': 1}, True, id='dict'),
        pytest.param({'a': {'b': 1}}, True, id='dict-dict'),
        pytest.param({'a': [1, 2, 3]}, True, id='dict-list'),
        pytest.param({'a': [{'b': 1}, {'c': 2}, {'d': 3}]}, True, id='dict-list-dict'),
        pytest.param({'a': [{'b': [1, 2, 3]}]}, True, id='dict-list-dict-list'),
        pytest.param({'a': [1, 2, 3]}, False, id='dict-list-no-list-flattening'),
    ],
)
def test_dict_flatten_rebuild(data, flatten_list):
    flattened = flatten_dict(data, flatten_list=flatten_list)
    rebuilt = rebuild_dict(flattened)

    assert rebuilt == data


@pytest.mark.parametrize(
    'data, include, exclude, expected',
    [
        pytest.param(
            {'a': 1, 'b': {'c': 2}, 'd': [{'e': 3}, {'f': 4}]},
            None,
            None,
            {'a': 1, 'b': {'c': 2}, 'd': [{'e': 3}, {'f': 4}]},
            id='all',
        ),
        pytest.param(
            {'a': 1, 'b': {'c': 2}, 'd': [{'e': 3}, {'f': 4}]},
            ['d.f'],
            None,
            {'d': [{'f': 4}]},
            id='include exact',
        ),
        pytest.param(
            {'a': 1, 'b': {'c': 2}, 'd': [{'e': 3}, {'f': 4}]},
            ['d.*'],
            None,
            {'d': [{'e': 3}, {'f': 4}]},
            id='include wildcard',
        ),
        pytest.param(
            {'a': 1, 'b': {'c': 2}, 'd': [{'e': 3}, {'f': 4}]},
            ['d*'],
            None,
            {'d': [{'e': 3}, {'f': 4}]},
            id='include wildcard no dot',
        ),
        pytest.param(
            {'a': 1, 'b': {'c': 2}, 'd': [{'e': 3}, {'f': 4}]},
            None,
            ['d.f'],
            {'a': 1, 'b': {'c': 2}, 'd': [{'e': 3}]},
            id='exclude exact',
        ),
        pytest.param(
            {'a': 1, 'b': {'c': 2}, 'd': [{'e': 3}, {'f': 4}]},
            None,
            ['d.*'],
            {'a': 1, 'b': {'c': 2}},
            id='exclude wildcard',
        ),
        pytest.param(
            {'a': {'b': 1}},
            None,
            ['a*'],
            {},
            id='exclude wildcard no dot',
        ),
        pytest.param(
            {'a': {'b': 1}},
            None,
            ['a.*'],
            {},
            id='exclude everything',
        ),
        pytest.param(
            {'a': [], 'b': {}},
            ['a', 'b'],
            None,
            {'a': [], 'b': {}},
            id='preserve empty structures if they are included',
        ),
    ],
)
def test_prune_dict(data, include, exclude, expected):
    pruned = prune_dict(data, include, exclude)
    assert pruned == expected


@pytest.mark.parametrize(
    'data, path, value, exception',
    [
        pytest.param({'a': []}, ['a'], [], False, id='empty-list'),
        pytest.param({'a': []}, ['a', 0], None, True, id='missing-index'),
        pytest.param({'a': [{}]}, ['a', 0], {}, False, id='empty-dict'),
        pytest.param({}, ['not-there'], None, True, id='missing-key-1'),
        pytest.param({'a': [{}]}, ['a', 0, 'b'], None, True, id='missing-key-2'),
    ],
)
def test_deep_get(data, path, value, exception):
    if exception:
        with pytest.raises(ValueError):
            deep_get(data, *path) == value
    else:
        assert deep_get(data, *path) == value


class TestDictDataFrameConverter:
    @pytest.mark.parametrize(
        'input_dict, expected_df, keys_to_filter',
        [
            pytest.param(
                {'a': {'b': 1, 'c': {'d': 2}}},
                pd.DataFrame({'a.b': [1], 'a.c.d': [2]}),
                None,
                id='dict',
            ),
            pytest.param(
                [{'a': 1, 'b': {'c': 2}}, {'a': 3, 'b': {'c': 4}}],
                pd.DataFrame({'a': [1, 3], 'b.c': [2, 4]}),
                None,
                id='list-dict',
            ),
            pytest.param(
                {
                    'a': {'x': 1, 'y': 2},
                    'b': {'x': 3, 'y': 4},
                    'c': {'x': 5, 'y': 6},
                },
                pd.DataFrame({'a.x': [1], 'c.y': [6]}),
                ['a.x', 'c.y'],
                id='dict_with_keys_to_filter',
            ),
        ],
    )
    def test_dict_to_dataframe(self, input_dict, expected_df, keys_to_filter):
        result_df = dict_to_dataframe(input_dict, keys_to_filter=keys_to_filter)
        pd.testing.assert_frame_equal(result_df, expected_df)

    @pytest.mark.parametrize(
        'input_dict, expected_df',
        [
            pytest.param(
                {'a': {'b': 1, 'c': {'d': 2}}},
                pd.DataFrame({'a.b': [1], 'a.c.d': [2]}),
                id='dict',
            ),
            pytest.param(
                [{'a': 1, 'b': {'c': 2}}, {'a': 3, 'b': {'c': 4}}],
                pd.DataFrame({'a': [1, 3], 'b.c': [2, 4]}),
                id='list-dict',
            ),
        ],
    )
    def test_dataframe_to_dict(self, input_dict, expected_df):
        df = dict_to_dataframe(input_dict)
        result_dict = dataframe_to_dict(df)
        assert result_dict == input_dict

    @pytest.mark.parametrize('invalid_input', ['invalid_input', 123, None])
    def test_invalid_input_type(self, invalid_input):
        with pytest.raises(ValueError, match='Input must be a dictionary'):
            dict_to_dataframe(invalid_input)


@pytest.mark.parametrize(
    'project_urls, version_str, expected_url',
    [
        (
            ['repository, https://github.com/example/repo'],
            '1.2.3+gabcdef',
            'https://github.com/example/repo/tree/abcdef',
        ),
        (['notrepository, https://github.com/example/repo'], '1.2.3+gabcdef', None),
        (
            ['repository, https://github.com/example/repo'],
            '1.2.3',
            'https://github.com/example/repo/tree/v1.2.3',
        ),
        ([], '1.2.3+gabcdef', None),
        (['repository, '], '1.2.3+gabcdef', None),
    ],
)
def test_nomad_distro_metadata(monkeypatch, project_urls, version_str, expected_url):
    def mock_metadata(package_name):
        class MockMetadata:
            def get_all(self, key, default=[]):
                if key == 'Project-URL':
                    return project_urls
                return default

        return MockMetadata()

    def mock_version(package_name):
        return version_str

    monkeypatch.setattr('nomad.utils.metadata', mock_metadata)
    monkeypatch.setattr('nomad.utils.version', mock_version)

    actual_url = nomad_distro_metadata()
    assert actual_url == expected_url


def test_nomad_distro_package_not_found(monkeypatch):
    def mock_metadata(package_name):
        raise PackageNotFoundError

    def mock_version(package_name):
        return '1.2.3'

    monkeypatch.setattr('nomad.utils.metadata', mock_metadata)
    monkeypatch.setattr('nomad.utils.version', mock_version)

    actual_url = nomad_distro_metadata()
    assert actual_url is None
