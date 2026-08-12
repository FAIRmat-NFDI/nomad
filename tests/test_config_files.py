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

import pytest

from nomad.cli.config_files import (
    CONFIG_ENV,
    apply_config_files_from_argv,
    parse_config_files,
)


@pytest.fixture(autouse=True)
def _isolate_config_env():
    """
    `apply_config_files_from_argv` writes to os.environ directly, which monkeypatch
    cannot undo for a variable that was not set before. Restore it here so that nothing
    leaks into other tests.
    """
    saved = os.environ.get(CONFIG_ENV)
    yield
    if saved is None:
        os.environ.pop(CONFIG_ENV, None)
    else:
        os.environ[CONFIG_ENV] = saved


@pytest.mark.parametrize(
    'argv, expected',
    [
        pytest.param([], [], id='no arguments'),
        pytest.param(['admin', 'run', 'appworker'], [], id='no config files'),
        pytest.param(
            ['-f', 'nomad.yaml', 'admin', 'run', 'appworker'],
            ['nomad.yaml'],
            id='single file',
        ),
        pytest.param(
            ['-f', 'nomad.yaml', '-f', 'nomad-dev.yaml', 'admin', 'run', 'appworker'],
            ['nomad.yaml', 'nomad-dev.yaml'],
            id='multiple files keep their order',
        ),
        pytest.param(
            ['--config-file', 'a.yaml', '--config-file=b.yaml', '-fc.yaml', 'admin'],
            ['a.yaml', 'b.yaml', 'c.yaml'],
            id='all option spellings',
        ),
        pytest.param(
            ['-v', '--debug', '-f', 'nomad.yaml', 'admin'],
            ['nomad.yaml'],
            id='mixed with flags',
        ),
        pytest.param(
            ['--log-label', 'admin', '-f', 'nomad.yaml', 'admin'],
            ['nomad.yaml'],
            id='value of another option is not a sub-command',
        ),
        pytest.param(
            ['admin', 'run', '-f', 'nomad.yaml', 'appworker'],
            ['nomad.yaml'],
            id='on the run group',
        ),
        pytest.param(
            ['admin', 'run', 'appworker', '-f', 'nomad.yaml', '-f', 'nomad-dev.yaml'],
            ['nomad.yaml', 'nomad-dev.yaml'],
            id='on the run sub-command',
        ),
        pytest.param(
            ['-f', 'a.yaml', 'admin', 'run', 'appworker', '-f', 'b.yaml'],
            ['a.yaml', 'b.yaml'],
            id='positions can be mixed',
        ),
        pytest.param(
            ['admin', 'run', 'app', '--host', '0.0.0.0', '-f', 'nomad.yaml'],
            ['nomad.yaml'],
            id='after an option value of the sub-command',
        ),
        pytest.param(
            ['admin', 'uploads', 'convert-archives', '-f'],
            [],
            id='sub-command -f (--force-repack) is not touched',
        ),
        pytest.param(
            ['dev', 'config', 'auth', '-f', 'nomad.yaml'],
            ['nomad.yaml'],
            id='on dev config',
        ),
        pytest.param(
            ['dev', 'gui-config', '-f', 'nomad.yaml'],
            [],
            id='other dev commands are not scanned',
        ),
        pytest.param(
            ['parse', '-f', 'some.archive.json'],
            [],
            id='other command trees are not scanned',
        ),
        pytest.param(['-f'], [], id='missing value'),
        pytest.param(
            ['--', '-f', 'nomad.yaml'], [], id='nothing is parsed after a double dash'
        ),
    ],
)
def test_parse_config_files(argv, expected):
    assert parse_config_files(argv) == expected


def test_apply_config_files_replaces_config_env(monkeypatch):
    """
    The files overwrite NOMAD_CONFIG, they are not added to it. Like
    `docker compose -f`, which overrides COMPOSE_FILE.
    """
    monkeypatch.setenv(CONFIG_ENV, 'from-env.yaml')

    applied = apply_config_files_from_argv(
        ['-f', 'first.yaml', '-f', 'second.yaml', 'admin', 'run', 'appworker']
    )

    assert applied == ['first.yaml', 'second.yaml']
    assert os.environ[CONFIG_ENV] == os.pathsep.join(['first.yaml', 'second.yaml'])


def test_apply_config_files_without_option(monkeypatch):
    """Without the option NOMAD_CONFIG is left as it is."""
    monkeypatch.setenv(CONFIG_ENV, 'from-env.yaml')

    assert apply_config_files_from_argv(['admin', 'run', 'appworker']) == []
    assert os.environ[CONFIG_ENV] == 'from-env.yaml'
