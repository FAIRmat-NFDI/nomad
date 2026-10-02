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

from tests.app.v1.routers.common import assert_response


@pytest.mark.parametrize(
    'user_label, upload_id, published, expected_status_code, error_detail',
    [
        pytest.param(None, 'id_unpublished_w', False, 401, None, id='no-credentials'),
        pytest.param(
            'invalid', 'id_unpublished_w', False, 401, None, id='invalid-credentials'
        ),
        pytest.param('user2', 'id_unpublished_w', False, 403, None, id='no-access'),
        pytest.param(
            'user1', 'id_unpublished_w', False, 200, None, id='write-access-unpublished'
        ),
        pytest.param(
            'user0', 'id_unpublished_w', False, 200, None, id='admin-access-unpublished'
        ),
        pytest.param(
            'user1',
            'id_published_w',
            True,
            400,
            'Upload is already published, operation not possible.',
            id='already-published',
        ),
    ],
)
@pytest.mark.parametrize(
    'url_suffix', [pytest.param('', id='edit'), pytest.param('validate', id='validate')]
)
@pytest.mark.asyncio
async def test_post_included_entries(
    auth_headers,
    client,
    temporal_worker,
    create_upload,
    url_suffix,
    user_label,
    upload_id,
    published,
    expected_status_code,
    error_detail,
):
    user_auth = auth_headers[user_label]
    create_upload(dict(upload_id=upload_id, published=published), [{}])

    inc_id = 'inc_upload'
    create_upload(dict(upload_id=inc_id), [{}])
    response = client.post(
        f'uploads/{upload_id}/included-entries/{url_suffix}',
        headers=user_auth,
        json={'query': {'upload_id': {'any': [inc_id]}}},
    )
    assert_response(response, expected_status_code, error_detail)

    if expected_status_code == 200:
        data = response.json()
        assert data['query']['upload_id']['any'][0] == inc_id
