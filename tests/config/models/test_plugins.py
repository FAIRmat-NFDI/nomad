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

import os
import tempfile

import pytest

from nomad.config import Config
from nomad.config.models.north import NORTHTool
from nomad.config.models.plugins import (
    APIEntryPoint,
    DashboardEntryPoint,
    EntryPoints,
    ExampleUploadEntryPoint,
    NORTHToolEntryPoint,
    SchemaPackageEntryPoint,
    UploadResource,
)


@pytest.mark.parametrize(
    'include, exclude, expected_keys',
    [
        pytest.param(None, None, ['package_a:one', 'package_a:two', 'package_b:one']),
        pytest.param([], None, []),
        pytest.param(
            ['package_b:*', 'package_a:two'], None, ['package_b:one', 'package_a:two']
        ),
        pytest.param(['package_a:*'], ['*:two'], ['package_a:one']),
        pytest.param(
            ['package_a:*', '*:one'],
            None,
            ['package_a:one', 'package_a:two', 'package_b:one'],
        ),
        pytest.param(None, ['package_a:*'], ['package_b:one']),
        pytest.param(
            ['*'],
            None,
            ['package_a:one', 'package_a:two', 'package_b:one'],
            id='include-all',
        ),
        pytest.param(['*'], ['*'], [], id='exclude-all'),
        pytest.param(['missing:*'], None, [], id='unmatched-include'),
        pytest.param(
            None,
            ['missing:*'],
            ['package_a:one', 'package_a:two', 'package_b:one'],
            id='unmatched-exclude',
        ),
        pytest.param(
            ['package_b:*', '*'],
            None,
            ['package_b:one', 'package_a:one', 'package_a:two'],
            id='include-pattern-order-with-catch-all',
        ),
    ],
)
def test_entry_points_filtered_keys_wildcards(include, exclude, expected_keys):
    options = {
        key: SchemaPackageEntryPoint()
        for key in ['package_a:one', 'package_a:two', 'package_b:one']
    }
    entry_points = EntryPoints(options=options, include=include, exclude=exclude)

    assert entry_points.filtered_keys() == expected_keys


def mock_plugin_package(monkeypatch, directory):
    """Used for mocking the presence of a plugin package installation
    location."""

    def mock_get_package_path(package_name):
        return directory

    monkeypatch.setattr(
        'nomad.config.models.plugins.get_package_path', mock_get_package_path
    )


def mock_example_upload_entry_point(
    monkeypatch, example_upload: ExampleUploadEntryPoint
):
    """Used for mocking an example upload entry point."""

    def mock_get_plugin_entry_point(self, package_name):
        if package_name == example_upload.id:
            return example_upload
        return cached(package_name)

    cached = Config.get_plugin_entry_point
    monkeypatch.setattr(Config, 'get_plugin_entry_point', mock_get_plugin_entry_point)


@pytest.mark.parametrize(
    'resources, files_package, upload_files',
    [
        pytest.param(
            'subfolder/data.txt',
            ['subfolder/data.txt'],
            ['data.txt'],
            id='file, no target',
        ),
        pytest.param(
            UploadResource(path='subfolder/data.txt', target='data_copy_no_suffix'),
            ['subfolder/data.txt'],
            ['data_copy_no_suffix'],
            id='file, with target pointing to a new file',
        ),
        pytest.param(
            UploadResource(
                path='subfolder/data.txt', target='upload_subfolder/data_copy_no_suffix'
            ),
            ['subfolder/data.txt'],
            ['upload_subfolder/data_copy_no_suffix'],
            id='file, with target pointing to a new folder+file',
        ),
        pytest.param(
            [
                UploadResource(path='subfolder1', target='upload_subfolder1'),
                UploadResource(path='subfolder2/data.txt', target='upload_subfolder1'),
            ],
            ['subfolder1/data.csv', 'subfolder2/data.txt'],
            [
                'upload_subfolder1/data.csv',
                'upload_subfolder1/data.txt',
            ],
            id='file, with target pointing to an existing folder',
        ),
        pytest.param(
            'subfolder',
            [
                'subfolder/data1.txt',
                'subfolder/data2.csv',
                'subfolder/subsubfolder/data.npy',
            ],
            [
                'subfolder/data1.txt',
                'subfolder/data2.csv',
                'subfolder/subsubfolder/data.npy',
            ],
            id='folder, no target',
        ),
        pytest.param(
            UploadResource(path='subfolder', target='upload_subfolder'),
            [
                'subfolder/data1.txt',
                'subfolder/data2.csv',
                'subfolder/subsubfolder/data.npy',
            ],
            [
                'upload_subfolder/data1.txt',
                'upload_subfolder/data2.csv',
                'upload_subfolder/subsubfolder/data.npy',
            ],
            id='folder, with target pointing to a new folder',
        ),
        pytest.param(
            UploadResource(
                path='subfolder', target='upload_subfolder/upload_subsubfolder'
            ),
            [
                'subfolder/data1.txt',
                'subfolder/data2.csv',
                'subfolder/subsubfolder/data.npy',
            ],
            [
                'upload_subfolder/upload_subsubfolder/data1.txt',
                'upload_subfolder/upload_subsubfolder/data2.csv',
                'upload_subfolder/upload_subsubfolder/subsubfolder/data.npy',
            ],
            id='folder, with target pointing to a new folder within folder',
        ),
        pytest.param(
            [
                UploadResource(path='subfolder1'),
                UploadResource(path='subfolder2', target='subfolder1'),
            ],
            [
                'subfolder1/data3.html',
                'subfolder2/data1.txt',
                'subfolder2/data2.csv',
                'subfolder2/subsubfolder/data.npy',
            ],
            [
                'subfolder1/data3.html',
                'subfolder1/subfolder2/data1.txt',
                'subfolder1/subfolder2/data2.csv',
                'subfolder1/subfolder2/subsubfolder/data.npy',
            ],
            id='folder, with target pointing to an existing folder',
        ),
        pytest.param(
            'subfolder/*',
            [
                'subfolder/data1.txt',
                'subfolder/data2.csv',
                'subfolder/subsubfolder/data.npy',
            ],
            [
                'data1.txt',
                'data2.csv',
                'subsubfolder/data.npy',
            ],
            id='folder contents, no target',
        ),
        pytest.param(
            UploadResource(path='subfolder/*', target='upload_subfolder'),
            [
                'subfolder/data1.txt',
                'subfolder/data2.csv',
                'subfolder/subsubfolder/data.npy',
            ],
            [
                'upload_subfolder/data1.txt',
                'upload_subfolder/data2.csv',
                'upload_subfolder/subsubfolder/data.npy',
            ],
            id='folder contents, with target pointing to a new folder',
        ),
        pytest.param(
            [
                UploadResource(path='subfolder1'),
                UploadResource(path='subfolder2/*', target='subfolder1'),
            ],
            [
                'subfolder1/data3.html',
                'subfolder2/data1.txt',
                'subfolder2/data2.csv',
                'subfolder2/subsubfolder/data.npy',
            ],
            [
                'subfolder1/data1.txt',
                'subfolder1/data2.csv',
                'subfolder1/data3.html',
                'subfolder1/subsubfolder/data.npy',
            ],
            id='folder contents, with target pointing to an existing folder',
        ),
        pytest.param(
            'https://nomad-lab.eu/nomad-lab/index.html',
            [],
            ['index.html'],
            id='url, no target',
        ),
        pytest.param(
            UploadResource(
                path='https://nomad-lab.eu/nomad-lab/index.html',
                target='test.html',
            ),
            [],
            ['test.html'],
            id='url, with target pointing to a new file.',
        ),
        pytest.param(
            [
                UploadResource(path='subfolder1', target='upload_subfolder1'),
                UploadResource(
                    path='https://nomad-lab.eu/nomad-lab/index.html',
                    target='upload_subfolder1',
                ),
            ],
            ['subfolder1/data.csv'],
            [
                'upload_subfolder1/data.csv',
                'upload_subfolder1/index.html',
            ],
            id='url, with target pointing to an existing folder',
        ),
        pytest.param(
            UploadResource(
                path='https://nomad-lab.eu/nomad-lab/index.html',
                target='upload_subfolder/test.html',
            ),
            [],
            ['upload_subfolder/test.html'],
            id='url, with target pointing to a new folder+file',
        ),
        pytest.param(
            ['subfolder/data1.txt', 'subfolder/data2.csv'],
            ['subfolder/data1.txt', 'subfolder/data2.csv'],
            ['data1.txt', 'data2.csv'],
            id='multiple files',
        ),
        pytest.param(
            ['subfolder1', 'subfolder2'],
            ['subfolder1/data1.txt', 'subfolder2/data2.npy'],
            ['subfolder1/data1.txt', 'subfolder2/data2.npy'],
            id='multiple folders',
        ),
        pytest.param(
            [
                'https://nomad-lab.eu/nomad-lab/index.html',
                'https://nomad-lab.eu/nomad-lab/terms.html',
            ],
            [],
            [
                'index.html',
                'terms.html',
            ],
            id='multiple urls',
        ),
        pytest.param(
            [
                'subfolder1/data1.txt',
                'subfolder2',
                'https://nomad-lab.eu/nomad-lab/index.html',
            ],
            [
                'subfolder1/data1.txt',
                'subfolder2/data2.npy',
            ],
            [
                'data1.txt',
                'index.html',
                'subfolder2/data2.npy',
            ],
            id='mixed files, folders and urls',
        ),
    ],
)
def test_example_upload_entry_point_resources(
    resources, files_package, upload_files, monkeypatch
):
    # Create tmp directory that will be used as a mocked package location.
    with tempfile.TemporaryDirectory() as tmp_package_directory:
        # Create folders and files in the package directory
        for file in files_package:
            dirname = os.path.join(tmp_package_directory, os.path.dirname(file))
            os.makedirs(dirname, exist_ok=True)
            with open(os.path.join(tmp_package_directory, file), 'w'):
                pass

        mock_plugin_package(monkeypatch, tmp_package_directory)

        def mock_download_file(url, filepath):
            with open(filepath, 'w'):
                pass

        monkeypatch.setattr(
            'nomad.config.models.plugins.download_file', mock_download_file
        )

        entry_point_id = 'nomad_plugin.module:identifier'
        config = {
            'plugin_package': 'nomad_test_plugin',
            'id': entry_point_id,
            'title': 'test',
            'description': 'test',
            'category': 'test',
            'resources': resources,
        }
        entry_point = ExampleUploadEntryPoint(**config)
        with tempfile.TemporaryDirectory() as tmp_upload_directory:
            entry_point.load(tmp_upload_directory)

            # Add upload folder as root to all expected upload_filepath
            upload_files = [
                os.path.join(tmp_upload_directory, path) for path in upload_files
            ]

            # Check that all expected upload_files are created
            real_upload_files = []
            for dirpath, _, filenames in os.walk(tmp_upload_directory):
                for filename in filenames:
                    real_upload_files.append(
                        os.path.abspath(os.path.join(dirpath, filename))
                    )
            assert sorted(real_upload_files) == upload_files


@pytest.mark.parametrize(
    'config, error',
    [
        pytest.param(
            {
                'id': 'test',
                'title': 'test',
                'description': 'test',
                'category': 'test',
                'resources': 'no_matches',
            },
            'Upload resource path "no_matches" in example upload "test" could not be found.',
            id='upload path source not found',
        ),
        pytest.param(
            {
                'id': 'test',
                'title': 'test',
                'description': 'test',
                'category': 'test',
                'resources': '../hidden_file.txt',
            },
            'Upload resource path "../hidden_file.txt" in example upload "test" is targeting files outside the Python package directory.',
            id='upload path source invalid location',
        ),
        pytest.param(
            {
                'id': 'test',
                'title': 'test',
                'description': 'test',
                'category': 'test',
                'resources': UploadResource(
                    path='data.txt', target='../sibling_folder'
                ),
            },
            'Upload resource target "../sibling_folder" in example upload "test" is targeting files outside the upload directory.',
            id='upload path target invalid location',
        ),
        pytest.param(
            {
                'id': 'test',
                'title': 'test',
                'description': 'test',
                'category': 'test',
                'resources': 'https://test.zip',
            },
            'Could not fetch file from URL: https://test.zip',
            id='cannot find url',
        ),
    ],
)
def test_example_upload_entry_point_load_invalid(config, error, monkeypatch):
    # Create tmp directory that will be used as a mocked package location.
    with tempfile.TemporaryDirectory() as tmp_root:
        # Create hidden file that is not supposed to be accessible
        with open(os.path.join(tmp_root, 'hidden_file.txt'), 'w'):
            pass
        # Create package folder
        package_directory = os.path.join(tmp_root, 'test')
        os.makedirs(package_directory)
        # Create accessible file
        with open(os.path.join(package_directory, 'data.txt'), 'w'):
            pass

        mock_plugin_package(monkeypatch, package_directory)
        config['plugin_package'] = 'nomad_test_plugin'

        with tempfile.TemporaryDirectory() as tmp_upload_directory:
            with pytest.raises(Exception) as exc_info:
                entry_point = ExampleUploadEntryPoint(**config)
                entry_point.load(tmp_upload_directory)

    assert exc_info.match(error.format(package_directory=package_directory))


@pytest.mark.parametrize(
    'config, error, value',
    [
        pytest.param({}, 'prefix must be defined', None, id='prefix-must-be-defined'),
        pytest.param(
            {'prefix': None},
            'prefix must be defined',
            None,
            id='prefix-must-not-be-none',
        ),
        pytest.param(
            {'prefix': ''}, 'prefix must be defined', None, id='prefix-must-not-empty'
        ),
        pytest.param({'prefix': '/foo/bar/'}, None, 'foo/bar', id='prefix-slashes'),
        pytest.param(
            {'prefix': 'not_$url& save'},
            'prefix must be a valid URL path',
            None,
            id='prefix-is-valid-url',
        ),
    ],
)
def test_api_entry_point_invalid(config, error, value):
    class MyAPIEntryPoint(APIEntryPoint):
        def load(self):
            pass

    if error:
        with pytest.raises(Exception) as exc_info:
            MyAPIEntryPoint(**config)
        assert exc_info.match(error)

    if not error:
        entry_point = MyAPIEntryPoint(**config)
        assert entry_point.prefix == value


def test_api_entry_point():
    class MyAPIEntryPoint(APIEntryPoint):
        def load(self):
            return 'loaded_api'

    entry_point = MyAPIEntryPoint(
        id='test_api',
        prefix='/my/api',
    )

    assert entry_point.load() == 'loaded_api'
    assert entry_point.prefix == 'my/api'
    assert entry_point.dict_safe() == {
        'id': 'test_api',
        'entry_point_type': 'api',
        'prefix': 'my/api',
    }


def test_north_tool_entry_point():
    tool = NORTHTool(
        short_description='A test tool',
        description='A test tool for testing',
        image='test_image',
    )
    entry_point = NORTHToolEntryPoint(
        id='test_tool',
        north_tool=tool,
    )

    assert entry_point.load() == tool
    assert entry_point.dict_safe() == {
        'id': 'test_tool',
        'entry_point_type': 'north_tool',
        'north_tool': {
            'short_description': 'A test tool',
            'description': 'A test tool for testing',
            'image': 'test_image',
            'image_pull_policy': 'Always',
            'privileged': False,
            'seccomp_unconfined': False,
            'use_gpu': False,
            'with_path': False,
            'file_extensions': [],
            'maintainer': [],
            'external_mounts': [],
        },
    }


@pytest.mark.parametrize(
    'config, error',
    [
        pytest.param(
            {},
            None,
            id='defaults-internal',
        ),
        pytest.param(
            {'external_url': 'https://example.com/dashboard'},
            None,
            id='external-url',
        ),
        pytest.param(
            {'external_url': 'javascript:alert(1)'},
            'external_url must be an absolute http',
            id='external-url-javascript-rejected',
        ),
        pytest.param(
            {'external_url': 'ftp://example.com'},
            'external_url must be an absolute http',
            id='external-url-ftp-rejected',
        ),
        pytest.param(
            {'external_url': '/relative/path'},
            'external_url must be an absolute http',
            id='external-url-relative-rejected',
        ),
        pytest.param(
            {'icon': 'https://cdn.example.com/icon.svg'},
            None,
            id='icon-https-allowed',
        ),
        pytest.param(
            {'icon': '/relative/icon.svg'},
            'icon must be an absolute http',
            id='icon-relative-rejected',
        ),
        pytest.param(
            {'icon': 'javascript:alert(1)'},
            'icon must be an absolute http',
            id='icon-javascript-rejected',
        ),
    ],
)
def test_dashboard_entry_point_validation(config, error):
    if error:
        with pytest.raises(Exception) as exc_info:
            DashboardEntryPoint(**config)
        assert exc_info.match(error)
    else:
        DashboardEntryPoint(**config)


def test_dashboard_entry_point_launch_modes_default():
    ep = DashboardEntryPoint(id='x')
    assert ep.launch_modes == ['embedded', 'tab']


def test_dashboard_entry_point_launch_modes_empty_rejected():
    with pytest.raises(Exception) as exc_info:
        DashboardEntryPoint(id='x', launch_modes=[])
    assert exc_info.match('launch_modes must contain at least one entry')


def test_dashboard_entry_point_launch_modes_order_preserved():
    # The first entry is the default action when the dashboard is clicked
    # in the GUI, so order must be preserved.
    ep = DashboardEntryPoint(id='x', launch_modes=['tab', 'embedded'])
    assert ep.launch_modes == ['tab', 'embedded']


def test_dashboard_entry_point_dict_safe():
    ep = DashboardEntryPoint(
        id='demo',
        name='Demo',
        external_url='https://example.com/dashboard',
        launch_modes=['tab'],
        icon='https://cdn.example.com/icon.svg',
    )
    data = ep.dict_safe()
    assert data['id'] == 'demo'
    assert data['entry_point_type'] == 'dashboard'
    assert data['external_url'] == 'https://example.com/dashboard'
    assert data['launch_modes'] == ['tab']
    assert data['icon'] == 'https://cdn.example.com/icon.svg'
