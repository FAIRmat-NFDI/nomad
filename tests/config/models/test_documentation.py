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

from nomad.config.models import config as config_module
from nomad.config.models.config import (
    Config,
    Documentation,
    get_default_documentation_url,
)


@pytest.mark.parametrize(
    'nomad_version, expected',
    [
        pytest.param('1.4.3', 'https://docs.nomad-lab.eu/1.4.3', id='release'),
        pytest.param('2.0.0', 'https://docs.nomad-lab.eu/2.0.0', id='major-release'),
        pytest.param(
            '1.4.4.dev242+gb18de31c1', 'https://docs.nomad-lab.eu', id='dev-build'
        ),
        pytest.param('2.0.0rc1', 'https://docs.nomad-lab.eu', id='release-candidate'),
        pytest.param('1.4', 'https://docs.nomad-lab.eu', id='incomplete'),
        pytest.param('', 'https://docs.nomad-lab.eu', id='empty'),
        pytest.param(None, 'https://docs.nomad-lab.eu', id='missing'),
    ],
)
def test_default_documentation_url(nomad_version, expected):
    assert get_default_documentation_url(nomad_version) == expected


@pytest.mark.parametrize(
    'nomad_version, expected',
    [
        pytest.param('1.4.3', 'https://docs.nomad-lab.eu/1.4.3', id='release'),
        pytest.param('1.4.4.dev1+g123', 'https://docs.nomad-lab.eu', id='dev-build'),
    ],
)
def test_documentation_url_is_filled_from_installed_version(
    monkeypatch, nomad_version, expected
):
    monkeypatch.setattr(config_module, '__version__', nomad_version)
    assert Documentation().url == expected
    assert Config().documentation.url == expected


def test_documentation_url_can_be_overridden():
    documentation = Documentation(url='https://my-oasis.org/docs/')
    assert documentation.url == 'https://my-oasis.org/docs'
