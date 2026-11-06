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

import h5py
import pytest
from fastapi.testclient import TestClient

from nomad.app import h5grove_app
from nomad.config import config
from nomad.files import StagingUploadFiles
from nomad.utils.exampledata import ExampleData
from tests.app.v1.routers.common import assert_response
from tests.test_files import append_raw_files


@pytest.fixture
def h5grove_api(monkeypatch, raw_files_function):
    h5grove_app.h5grove_router.settings.base_dir = config.fs.staging
    return TestClient(h5grove_app.app, base_url='http://testserver/')


@pytest.fixture
def upload_id():
    return 'nexus_test_upload'


@pytest.fixture
def example_data_nxs(user1, upload_id):
    data = ExampleData(main_author=user1)
    data.create_upload(upload_id)
    data.create_entry(upload_id=upload_id, entry_id='nexus_test_entry')
    data.save(with_files=False, with_mongo=True)


@pytest.fixture(scope='module')
def h5grove_raw_data(
    elastic_module, raw_files_module, mongo_module, user1, user2, tmp_path_factory
):
    """
    Creates actually processed uploads with a real raw ``.h5`` file, covering the
    different access scenarios for reading raw files via h5grove:

    - ``h5_published``: published without embargo (raw files live in a zip). Readable
      by anyone, including anonymous users.
    - ``h5_embargo``: published with embargo. Only readable by members and admins.
    - ``h5_unpublished``: unpublished (raw files in staging). Only readable by the
      owner and admins.
    - ``h5_unpublished_member``: unpublished with a coauthor. Also readable by the
      coauthor.

    The ``.h5`` file contains a single dataset ``/entry`` with the value ``'test'``.
    """
    local_h5 = str(tmp_path_factory.mktemp('h5grove') / 'test.h5')
    with h5py.File(local_h5, 'w') as f:
        f.create_dataset('entry', data='test')

    uploads = {
        'h5_published': dict(published=True, embargo_length=0),
        'h5_embargo': dict(published=True, embargo_length=12),
        'h5_unpublished': dict(published=False),
        'h5_unpublished_member': dict(published=False, coauthors=[user2.user_id]),
    }

    data = ExampleData(main_author=user1)
    for upload, kwargs in uploads.items():
        data.create_upload(upload_id=upload, **kwargs)
        data.create_entry(upload_id=upload, entry_id=f'{upload}_1')
    data.save(with_files=True, with_mongo=True, with_es=False)

    # Add the real raw .h5 file to each upload. ``append_raw_files`` writes into the
    # zip for published uploads and copies into staging for unpublished ones.
    for upload in uploads:
        append_raw_files(upload, local_h5, 'test.h5')

    return list(uploads.keys())


@pytest.mark.parametrize(
    'upload, user, status_code',
    [
        # Published without embargo: readable by anyone, including anonymous users.
        pytest.param('h5_published', None, 200, id='published-anonymous'),
        pytest.param(
            'h5_published', 'invalid', 401, id='published-invalid-credentials'
        ),
        pytest.param('h5_published', 'user1', 200, id='published-owner'),
        pytest.param('h5_published', 'user3', 200, id='published-other-user'),
        pytest.param('h5_published', 'user0', 200, id='published-admin'),
        # Published with embargo: only members and admins.
        pytest.param('h5_embargo', None, 403, id='embargo-anonymous'),
        pytest.param('h5_embargo', 'user1', 200, id='embargo-owner'),
        pytest.param('h5_embargo', 'user3', 403, id='embargo-other-user'),
        pytest.param('h5_embargo', 'user0', 200, id='embargo-admin'),
        # Unpublished: anonymous -> 401, logged-in without access -> 403.
        pytest.param('h5_unpublished', None, 401, id='unpublished-anonymous'),
        pytest.param(
            'h5_unpublished', 'invalid', 401, id='unpublished-invalid-credentials'
        ),
        pytest.param('h5_unpublished', 'user1', 200, id='unpublished-owner'),
        pytest.param('h5_unpublished', 'user3', 403, id='unpublished-other-user'),
        pytest.param('h5_unpublished', 'user0', 200, id='unpublished-admin'),
        # Unpublished with a coauthor: the coauthor can read it.
        pytest.param('h5_unpublished_member', 'user2', 200, id='member-coauthor'),
        pytest.param('h5_unpublished_member', None, 401, id='member-anonymous'),
        pytest.param('h5_unpublished_member', 'user3', 403, id='member-other-user'),
    ],
)
def test_h5grove_raw(client, auth_headers, h5grove_raw_data, upload, user, status_code):
    """
    Reads a raw ``.h5`` file from actually processed uploads via h5grove, checking
    that access is granted/denied based on the publication status and the user's
    permissions.
    """
    url = f'/h5grove/data?file=test.h5&path=/entry&upload_id={upload}&source=raw'
    response = client.get(url, headers=auth_headers[user])
    assert_response(response, status_code)
    if status_code == 200:
        assert response.content == b'"test"'


@pytest.mark.parametrize(
    'upload, user',
    [
        pytest.param('h5_published', 'user1', id='published-owner'),
        pytest.param('h5_published', None, id='published-anonymous'),
        pytest.param('h5_unpublished', 'user1', id='unpublished-owner'),
    ],
)
def test_h5grove_raw_file_not_found(
    client, auth_headers, h5grove_raw_data, upload, user
):
    """
    Requesting a non-existent raw file returns 404 once read access is granted.

    The error body is produced by h5grove itself (``{"message": ...}``), not in
    NOMAD's ``{"detail": ...}`` format, so we assert the status code directly.
    """
    url = f'/h5grove/data?file=missing.h5&path=/entry&upload_id={upload}&source=raw'
    response = client.get(url, headers=auth_headers[user])
    assert response.status_code == 404


@pytest.mark.parametrize(
    'filename, encoded',
    [
        pytest.param('a b.h5', 'a%20b.h5', id='space-percent-encoded'),
        pytest.param('a b.h5', 'a+b.h5', id='space-plus-encoded'),
        pytest.param('a+b.h5', 'a%2Bb.h5', id='literal-plus'),
        pytest.param('a%b.h5', 'a%25b.h5', id='literal-percent'),
        pytest.param('a&b=c.h5', 'a%26b%3Dc.h5', id='ampersand-equals'),
    ],
)
def test_h5grove_raw_special_characters(
    client, auth_headers, h5grove_raw_data, tmp_path, filename, encoded
):
    """
    Raw files whose names contain spaces or other special characters can be read,
    independent of how the client encodes the query string (e.g. ``+`` or ``%20``).
    """
    local_h5 = str(tmp_path / 'special.h5')
    with h5py.File(local_h5, 'w') as f:
        f.create_dataset('entry', data='test')
    append_raw_files('h5_unpublished', local_h5, filename)

    url = f'/h5grove/data?upload_id=h5_unpublished&source=raw&file={encoded}&path=%2Fentry'
    response = client.get(url, headers=auth_headers['user1'])
    assert_response(response, 200)
    assert response.content == b'"test"'


def test_h5grove_auth(client, example_data):
    """
    Tests that the h5grove endpoint is correctly protected based on upload publication status.
    """
    upload_id = 'id_unpublished'
    entry_id = 'id_unpublished_1'
    url = f'/h5grove/?upload_id={upload_id}&file={entry_id}&path=/&source=archive'

    # First, check that we can't access it when it is not published
    response = client.get(url)
    assert_response(response, 401)

    # Check a published entry is visible
    upload_id = 'id_published'
    entry_id = 'id_published_1'
    url = f'/h5grove/?upload_id={upload_id}&file={entry_id}&path=/&source=archive'

    response = client.get(url)
    assert_response(response, 200)


def test_h5grove_trailing_slash_does_not_redirect(
    auth_headers,
    h5grove_api,
    upload_id,
    temporal_worker,
    example_data_nxs,
):
    test_file = 'test.h5'
    file_path = f'{StagingUploadFiles(upload_id=upload_id, create=True)._raw_dir}{os.sep}{test_file}'
    h5file = h5py.File(file_path, 'w')
    h5file.create_dataset('entry', data='test')
    h5file.close()

    response = h5grove_api.get(
        f'/meta/?file={test_file}&path=/&upload_id={upload_id}&source=raw',
        headers=auth_headers['user1'],
        follow_redirects=False,
    )

    assert response.status_code == 200
