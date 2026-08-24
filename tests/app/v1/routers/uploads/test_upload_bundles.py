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

import asyncio
import io
import json
import os
import zipfile

import pytest

from nomad.bundles import BundleExporter
from nomad.config import config
from nomad.metainfo import MSection, Package, Quantity
from nomad.processing import Upload
from tests.app.v1.routers.common import assert_response, perform_get
from tests.processing import test_data as test_processing
from tests.utils import build_url, set_upload_entry_metadata

from .test_uploads import assert_processing, perform_post_put_file


@pytest.mark.parametrize(
    'upload_id, user, query_args, expected_status_code',
    [
        # Test published
        pytest.param('id_published_w', 'user1', dict(), 200, id='published-owner'),
        pytest.param('id_published_w', 'user0', dict(), 200, id='published-admin'),
        pytest.param('id_published_w', 'user2', dict(), 403, id='published-not-owner'),
        pytest.param(
            'id_published_w',
            'user1',
            dict(include_raw_files=False),
            200,
            id='published-owner-exclude-raw',
        ),
        pytest.param(
            'id_published_w',
            'user1',
            dict(include_archive_files=False),
            200,
            id='published-owner-exclude-archive',
        ),
        pytest.param(
            'id_published_w',
            'user1',
            dict(include_schemas=True),
            200,
            id='published-owner-include-schemas',
        ),
        # Test unpublished
        pytest.param('id_unpublished_w', 'user1', dict(), 200, id='unpublished-owner'),
        pytest.param('id_unpublished_w', 'user0', dict(), 200, id='unpublished-admin'),
        pytest.param(
            'id_unpublished_w',
            'user2',
            dict(),
            403,
            id='unpublished-not-owner',
        ),
    ],
)
@pytest.mark.asyncio
async def test_get_upload_bundle(
    auth_headers,
    client,
    temporal_worker,
    example_data_writeable,
    upload_id,
    user,
    query_args,
    expected_status_code,
):
    if upload_id in example_data_writeable:
        upload_id = example_data_writeable[upload_id]
    include_raw_files = query_args.get('include_raw_files', True)
    include_archive_files = query_args.get('include_archive_files', True)
    include_schemas = query_args.get('include_schemas', False)

    url = build_url(f'uploads/{upload_id}/bundle', query_args)
    response = perform_get(client, url, user_auth=auth_headers[user])
    assert_response(response, expected_status_code)
    if expected_status_code == 200:
        with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
            bundle_info = json.loads(zip_file.read('bundle_info.json'))
            upload = Upload.get(upload_id)
            upload_files = upload.upload_files
            expected_files = set(['bundle_info.json'])
            for dirpath, __, filenames in os.walk(upload_files.os_path):
                for filename in filenames:
                    os_path = os.path.join(dirpath, filename)
                    rel_path = os.path.relpath(os_path, upload_files.os_path)
                    include = False
                    include |= (
                        rel_path.startswith('raw')
                        and not rel_path.endswith('.h5')
                        and include_raw_files
                    )
                    include |= rel_path.startswith('archive') and include_archive_files
                    if include:
                        expected_files.add(rel_path)
            assert expected_files <= set(zip_file.namelist())

            assert bundle_info['upload_id'] == upload_id
            assert (
                bundle_info['export_settings']['include_raw_files'] is include_raw_files
            )
            assert (
                bundle_info['export_settings']['include_archive_files']
                is include_archive_files
            )
            assert bundle_info['export_settings']['include_schemas'] is include_schemas
            assert len(bundle_info['entries']) == len(upload.successful_entries)
            assert 'schemas' not in bundle_info
            assert not any(
                name.startswith('raw/schema_package_')
                and name.endswith('.archive.json')
                for name in zip_file.namelist()
            )


@pytest.mark.asyncio
async def test_get_upload_bundle_includes_schema_raw_file(
    auth_headers,
    client,
    temporal_worker,
    example_data_writeable,
    monkeypatch,
):
    package = Package(name='tests.upload_bundle_schema')

    class UploadBundleSchema(MSection):
        value = Quantity(type=str)

    package.section_definitions.append(UploadBundleSchema.m_def)

    monkeypatch.setattr(
        'nomad.bundles._get_schema_packages_for_upload',
        lambda upload_id, definition_id_aliases=None: [package],
    )

    upload_id = example_data_writeable['id_published_w']
    url = build_url(f'uploads/{upload_id}/bundle', dict(include_schemas=True))
    response = perform_get(client, url, user_auth=auth_headers['user1'])
    assert_response(response, 200)

    with zipfile.ZipFile(io.BytesIO(response.content)) as zip_file:
        schema_files = [
            name
            for name in zip_file.namelist()
            if name.startswith('raw/schema_package_') and name.endswith('.archive.json')
        ]
        assert len(schema_files) == 1
        assert json.loads(zip_file.read(schema_files[0])) == {
            'definitions': package.m_to_dict(
                with_out_meta=True,
                with_def_id=True,
                stable_references=True,
            )
        }


@pytest.mark.parametrize(
    'publish, test_duplicate, user, export_args, query_args, expected_status_code',
    [
        pytest.param(True, False, 'user0', dict(), dict(), 200, id='published-admin'),
        pytest.param(
            False, False, 'user0', dict(), dict(), 200, id='unpublished-admin'
        ),
        pytest.param(True, True, 'user0', dict(), dict(), 400, id='duplicate'),
        # Test access/permission
        pytest.param(True, False, 'user2', dict(), dict(), 200, id='not-oasis-admin'),
        pytest.param(True, False, None, dict(), dict(), 401, id='no-credentials'),
    ],
)
@pytest.mark.asyncio
async def test_post_upload_bundle(
    auth_headers,
    client,
    temporal_worker,
    non_empty_uploaded,
    internal_example_user_metadata,
    publish,
    test_duplicate,
    user,
    users_dict,
    export_args,
    query_args,
    expected_status_code,
):
    async with temporal_worker():
        non_empty_processed = await asyncio.to_thread(
            lambda: test_processing.run_processing(
                non_empty_uploaded, users_dict[user or 'user0']
            )
        )
        # Create the bundle
        set_upload_entry_metadata(non_empty_processed, internal_example_user_metadata)
        if publish:
            await asyncio.to_thread(non_empty_processed.publish_upload)
            await non_empty_processed.await_workflows()
        upload = non_empty_processed
        upload_id = upload.upload_id
        export_path = os.path.join(config.fs.tmp, 'bundle_' + upload_id)
        export_args_with_defaults = dict(
            export_as_stream=False,
            export_path=export_path,
            zipped=True,
            overwrite=True,
            export_settings=config.bundle_export.default_settings,
        )
        export_args_with_defaults.update(export_args)
        BundleExporter(upload, **export_args_with_defaults).export_bundle()

        if not test_duplicate:
            # Delete the upload so we can import the bundle without id collisions
            upload.delete_upload_local()
        # Finally, import the bundle
        user_auth = auth_headers[user]
        response = await asyncio.to_thread(
            lambda: perform_post_put_file(
                client,
                'POST',
                'uploads/bundle',
                'stream',
                export_path,
                user_auth,
                **query_args,
            )
        )
    assert_response(response, expected_status_code)
    if expected_status_code == 200:
        assert_processing(client, upload_id, user_auth, published=publish)
        upload = Upload.get(upload_id)
        assert upload.from_oasis and upload.oasis_deployment_url
