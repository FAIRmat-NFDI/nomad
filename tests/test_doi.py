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

import datetime

import pytest

from nomad.config import config
from nomad.datacite import DataCiteException
from nomad.datacite.utils import generate_unique_doi_name
from nomad.mongo.doi import DOI
from tests.fixtures.infrastructure import DataciteMock
from tests.utils import assert_doi_name


def test_create(mongo_function, user1, no_warn):
    doi = DOI.create()
    doi.save()

    assert DOI.objects(doi=doi.doi).first() is not None
    assert_doi_name(doi.doi)
    assert doi.state == 'created'
    assert doi.url.endswith(doi.doi)
    assert doi.create_time is not None

    assert doi.doi_url is None
    assert doi.metadata_url is None
    assert doi.metadata_xml is None

    doi2 = DOI.create()
    doi2.save()
    assert doi.doi != doi2.doi


def test_create_draft_doi(datacite_mock, mongo_function, user1, no_warn):
    doi = DOI.create()
    doi.create_draft('the_title', 2026, user1)
    assert doi.state == 'draft'

    doi.delete()
    assert DOI.objects(doi=doi.doi).first() is None


@pytest.mark.parametrize(
    'status_code,response_ok,is_findable,text',
    [
        pytest.param(200, True, False, 'Success', id='pass-with-200-only-drafted'),
        pytest.param(200, True, True, 'Success', id='pass-with-200-with-findable'),
        pytest.param(400, False, False, 'Bad Request', id='fail-with-400'),
    ],
)
def test_datacite_requests(
    datacite_mock: DataciteMock,
    mongo_function,
    user1,
    status_code,
    response_ok,
    is_findable,
    text,
):
    datacite_mock.set_requests(status_code, response_ok, text)
    doi = DOI.create()
    doi.save()

    if response_ok:
        doi.create_draft('the_title', 2026, user1)
        assert DOI.objects(doi=doi.doi).first() is not None
        assert DOI.objects(doi=doi.doi).first().state == 'draft'

        if is_findable:
            doi.make_findable()
            assert DOI.objects(doi=doi.doi).first().state == 'findable'
        else:
            doi.delete()
            assert DOI.objects(doi=doi.doi).first() is None

    elif not response_ok and config.datacite.enabled:
        with pytest.raises(DataCiteException):
            doi.create_draft('the_title', 2026, user1)


def test_generate_unique_doi_name():
    doi1 = generate_unique_doi_name()
    doi2 = generate_unique_doi_name()

    assert_doi_name(doi1)
    assert_doi_name(doi2)
    assert doi1 != doi2


@pytest.mark.parametrize(
    'affiliation,affiliation_address,expected_affiliation_name',
    [
        (None, None, ''),
        ('Uni', None, 'Uni'),
        (None, '123 Uni St, Uni City', '; 123 Uni St, Uni City'),
        ('Uni', '123 Uni St, Uni City', 'Uni; 123 Uni St, Uni City'),
    ],
)
def test_convert_user_to_creator(
    affiliation, affiliation_address, expected_affiliation_name
):
    from nomad.datacite.service import convert_user_to_creator
    from nomad.datamodel import User

    user = User(
        user_id='00000000-0000-0000-0000-000000000001',
        first_name='John',
        last_name='Doe',
        affiliation=affiliation,
        affiliation_address=affiliation_address,
    )
    creator = convert_user_to_creator(user)
    assert creator.name == 'John Doe'
    assert creator.affiliation[0].name == expected_affiliation_name


@pytest.fixture
def captured_doi_attributes(monkeypatch):
    """Captures the attributes sent to DataCite without resolving real users."""
    from nomad.datacite import service
    from nomad.datacite.models import Creator

    captured = []
    monkeypatch.setattr(service, 'create_doi', captured.append)
    monkeypatch.setattr(
        service, 'convert_user_id_to_creator', lambda user_id: Creator(name=user_id)
    )
    monkeypatch.setattr(config.services, 'https', True)
    monkeypatch.setattr(config.services, 'api_host', 'nomad.example.org')
    monkeypatch.setattr(config.services, 'api_port', 443)
    return captured


@pytest.mark.parametrize('api_base_path', ['/backend', '/'])
def test_create_doi_for_upload_targets_project_page(
    captured_doi_attributes, monkeypatch, api_base_path
):
    from types import SimpleNamespace

    from nomad.datacite import create_doi_for_upload

    monkeypatch.setattr(config.services, 'api_base_path', api_base_path)
    upload = SimpleNamespace(
        doi=None,
        upload_id='upload_id',
        upload_name='the_title',
        main_author='main_author',
        upload_create_time=datetime.datetime(2026, 1, 1),
    )

    doi = create_doi_for_upload(upload)

    (attributes,) = captured_doi_attributes
    assert attributes.doi == doi
    assert attributes.url == 'https://nomad.example.org/projects/upload_id'


@pytest.mark.parametrize(
    'api_base_path, dataset_path, expected_path',
    [
        pytest.param('/backend', None, '/datasets/doi', id='default'),
        pytest.param('/', None, '/datasets/doi', id='root-api'),
        pytest.param('/backend', 'prod/dataset', '/prod/dataset', id='no-slashes'),
        pytest.param(
            '/backend', '/prod/dataset/', '/prod/dataset', id='trailing-slash'
        ),
        pytest.param('/backend', '', '', id='empty'),
        pytest.param('/backend', '/', '', id='slash'),
    ],
)
def test_create_doi_for_dataset_targets_dataset_path(
    captured_doi_attributes, monkeypatch, api_base_path, dataset_path, expected_path
):
    from types import SimpleNamespace

    from nomad.datacite import create_doi_for_dataset

    monkeypatch.setattr(config.services, 'api_base_path', api_base_path)
    if dataset_path is not None:
        monkeypatch.setattr(config.datacite, 'dataset_path', dataset_path)
    dataset = SimpleNamespace(
        doi=None,
        dataset_name='the_title',
        user_id='user_id',
        dataset_create_time=datetime.datetime(2026, 1, 1),
    )

    doi = create_doi_for_dataset(dataset)

    (attributes,) = captured_doi_attributes
    assert attributes.url == f'https://nomad.example.org{expected_path}/{doi}'
