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

import click

from .admin import admin


@admin.command(
    help='Checks consistency of files and es vs mongo and deletes orphan entries.'
)
@click.option('--dry', is_flag=True, help='Do not delete anything, just check.')
@click.option(
    '--skip-entries', is_flag=True, help='Skip cleaning entries with missing uploads.'
)
@click.option('--skip-fs', is_flag=True, help='Skip cleaning the filesystem.')
@click.option('--skip-es', is_flag=True, help='Skip cleaning the es index.')
@click.option(
    '--staging-too',
    is_flag=True,
    help='Also clean published entries in staging, make sure these files are not due to reprocessing',
)
@click.option('--force', is_flag=True, help='Do not ask for confirmation.')
def clean(dry, skip_entries, skip_fs, skip_es, staging_too, force):
    import os
    import shutil

    import tabulate
    from elasticsearch.dsl import Search

    from nomad import infrastructure, processing
    from nomad.config import config as nomad_config
    from nomad.elastic_compat import delete_search
    from nomad.search import delete_by_query, quantity_values

    mongo_client = infrastructure.setup_mongo()
    infrastructure.setup_elastic()

    if not skip_entries:
        uploads_for_entries = mongo_client[nomad_config.mongo.db_name][
            'entry'
        ].distinct('upload_id')
        uploads = {}
        for upload in mongo_client[nomad_config.mongo.db_name]['upload'].distinct(
            '_id'
        ):
            uploads[upload] = True

        missing_uploads = []
        for upload_for_entry in uploads_for_entries:
            if upload_for_entry not in uploads:
                missing_uploads.append(upload_for_entry)

        if not dry and len(missing_uploads) > 0:
            if not force:
                input(
                    f'Will delete entries (mongo + es) for {len(missing_uploads)} missing uploads. Press any key to continue ...'
                )

            for upload in missing_uploads:
                mongo_client[nomad_config.mongo.db_name]['entry'].remove(
                    dict(upload_id=upload)
                )
                delete_search(
                    Search(index=nomad_config.elastic.entries_index).query(
                        'term', upload_id=upload
                    )
                )
        else:
            print(
                f'Found {len(missing_uploads)} uploads that have entries in mongo, but there is no upload entry.'
            )
            print('List first 10:')
            for upload in missing_uploads[:10]:
                print(upload)

    if not skip_fs:
        upload_dirs = []
        public_dirs, staging_dirs = {}, {}
        for bucket, dir_map in [
            (nomad_config.fs.public, public_dirs),
            (nomad_config.fs.staging, staging_dirs),
        ]:
            for prefix in os.listdir(bucket):
                for upload in os.listdir(os.path.join(bucket, prefix)):
                    upload_dirs.append((upload, os.path.join(bucket, prefix, upload)))
                    dir_map[upload] = os.path.join(bucket, prefix, upload)

        to_delete = list(
            path
            for upload, path in upload_dirs
            if processing.Upload.objects(upload_id=upload).first() is None  # type: ignore
        )

        if not dry and len(to_delete) > 0:
            if not force:
                input(
                    f'Will delete {len(to_delete)} upload directories. Press any key to continue ...'
                )

            for path in to_delete:
                shutil.rmtree(path)
        else:
            print(f'Found {len(to_delete)} upload directories with no upload in mongo.')
            print('List first 10:')
            for path in to_delete[:10]:
                print(path)

    if staging_too and not skip_fs:
        to_delete = list(
            path for upload, path in staging_dirs.items() if upload in public_dirs
        )

        if not dry and len(to_delete) > 0:
            if not force:
                input(
                    f'Will delete {len(to_delete)} staging upload directories. Press any key to continue ...'
                )

            for path in to_delete:
                shutil.rmtree(path)
        else:
            print(
                f'Found {len(to_delete)} staging upload directories with upload directory in public.'
            )
            print('List first 10:')
            for path in to_delete[:10]:
                print(path)

    if not skip_es:
        es_upload_buckets = quantity_values(
            'upload_id', owner='all', return_buckets=True
        )

        to_delete = list(
            (bucket.value, bucket.count)
            for bucket in es_upload_buckets
            if processing.Upload.objects(upload_id=bucket.value).first() is None  # type: ignore
        )

        entries = 0
        for _, upload_entries in to_delete:
            entries += upload_entries

        if not dry and len(to_delete) > 0:
            if not force:
                input(
                    f'Will delete {entries} entries in {len(to_delete)} uploads from ES. Press any key to continue ...'
                )
            for upload_id, _ in to_delete:
                delete_by_query(owner='all', query=dict(upload_id=upload_id))
        else:
            print(
                f'Found {entries} entries in {len(to_delete)} uploads from ES with no upload in mongo.'
            )
            print('List first 10:')
            tabulate.tabulate(to_delete, headers=['id', '#entries'])
