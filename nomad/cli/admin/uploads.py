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

import json
import os
import traceback

import click
from orjson import dumps

from nomad.config import config

from .admin import admin


def _run_parallel(
    uploads, parallel: int, callable, label: str, print_progress: int = 0
):
    import threading
    import time

    from nomad import processing as proc
    from nomad import utils

    if isinstance(uploads, tuple | list):
        uploads_count = len(uploads)

    else:
        uploads_count = uploads.count()
        uploads = list(
            uploads
        )  # copy the whole mongo query set to avoid cursor timeouts

    cv = threading.Condition()
    threads: list[threading.Thread] = []

    state = dict(completed_count=0, skipped_count=0, available_threads_count=parallel)

    logger = utils.get_logger(__name__)

    print(f'{uploads_count} uploads selected, {label} ...')

    def process_upload(upload: proc.Upload):
        logger.info(f'{label} started', upload_id=upload.upload_id)

        completed = False
        try:
            if callable(upload, logger):
                completed = True
        except Exception as e:
            completed = True
            logger.error(f'{label} failed', upload_id=upload.upload_id, exc_info=e)

        with cv:
            state['completed_count'] += 1 if completed else 0
            state['skipped_count'] += 1 if not completed else 0
            state['available_threads_count'] += 1

            print(
                '   {} {} and skipped {} of {} uploads'.format(
                    label,
                    state['completed_count'],
                    state['skipped_count'],
                    uploads_count,
                )
            )

            cv.notify()

    for upload in uploads:
        logger.info(
            f'cli schedules parallel {label} processing for upload',
            current_process=upload.current_process,
            last_status_message=upload.last_status_message,
            upload_id=upload.upload_id,
        )
        with cv:
            cv.wait_for(lambda: state['available_threads_count'] > 0)
            state['available_threads_count'] -= 1
            thread = threading.Thread(target=lambda: process_upload(upload))
            threads.append(thread)
            thread.start()

    def print_progress_lines():
        while True:
            time.sleep(print_progress)
            print('.', flush=True)

    if print_progress > 0:
        progress_thread = threading.Thread(target=print_progress_lines)
        progress_thread.daemon = True
        progress_thread.start()

    for thread in threads:
        thread.join()


def _run_processing(
    uploads,
    parallel: int,
    process,
    label: str,
    process_running: bool = False,
    wait_until_complete: bool = True,
    reset_first: bool = False,
    **kwargs,
):
    from nomad import processing as proc

    def run_process(upload, logger):
        logger.info(
            f'cli calls {label} processing',
            current_process=upload.current_process,
            last_status_message=upload.last_status_message,
            upload_id=upload.upload_id,
        )
        if upload.process_running and not process_running:
            logger.warn(
                f'cannot trigger {label}, since the upload is already/still processing',
                current_process=upload.current_process,
                last_status_message=upload.last_status_message,
                upload_id=upload.upload_id,
            )
            return False

        if reset_first:
            upload.reset(force=True)
        elif upload.process_running:
            upload.reset(force=True, process_status=proc.ProcessStatus.FAILURE)

        upload.reload()
        process(upload)
        if wait_until_complete:
            upload.block_until_complete(interval=0.5)
        else:
            upload.block_until_complete_or_waiting_for_result(interval=0.5)

        if upload.process_status == proc.ProcessStatus.FAILURE:
            logger.info(f'{label} with failure', upload_id=upload.upload_id)

        logger.info(f'{label} complete', upload_id=upload.upload_id)
        return True

    _run_parallel(
        uploads, parallel=parallel, callable=run_process, label=label, **kwargs
    )


@admin.group(help='Upload related commands')
@click.option('--uploads-mongo-query', type=str, help='A query')
@click.option('--entries-mongo-query', type=str, help='A query')
@click.option('--entries-es-query', type=str, help='A query')
@click.option('--unpublished', help='Select only uploads in staging', is_flag=True)
@click.option('--published', help='Select only uploads that are publised', is_flag=True)
@click.option(
    '--outdated', help='Select published uploads with older nomad version', is_flag=True
)
@click.option('--processing', help='Select only processing uploads', is_flag=True)
@click.option(
    '--processing-failure-uploads',
    is_flag=True,
    help='Select uploads with failed processing',
)
@click.option(
    '--processing-failure-entries',
    is_flag=True,
    help='Select uploads with entries with failed processing',
)
@click.option(
    '--processing-failure',
    is_flag=True,
    help='Select uploads where the upload or any entry has failed processing',
)
@click.option(
    '--processing-incomplete-uploads',
    is_flag=True,
    help='Select uploads that have not yet been processed',
)
@click.option(
    '--processing-incomplete-entries',
    is_flag=True,
    help='Select uploads where any entry has net yot been processed',
)
@click.option(
    '--processing-incomplete',
    is_flag=True,
    help='Select uploads where the upload or any entry has not yet been processed',
)
@click.option(
    '--processing-necessary',
    is_flag=True,
    help='Select uploads where the upload or any entry has either not been processed or processing has failed in the past',
)
@click.option(
    '--unindexed',
    is_flag=True,
    help='Select uploads that have no entries in the elastic search index.',
)
@click.pass_context
def uploads(ctx, **kwargs):
    ctx.obj.uploads_kwargs = kwargs


def _query_uploads(
    uploads,
    unpublished: bool,
    published: bool,
    processing: bool,
    outdated: bool,
    uploads_mongo_query: str,
    entries_mongo_query: str,
    entries_es_query: str,
    processing_failure_uploads: bool,
    processing_failure_entries: bool,
    processing_failure: bool,
    processing_incomplete_uploads: bool,
    processing_incomplete_entries: bool,
    processing_incomplete: bool,
    processing_necessary: bool,
    unindexed: bool,
):
    """
    Produces a list of uploads (mongoengine proc.Upload objects) based on a given
    list of upoad ids and further filter parameters.
    """

    import json
    from typing import cast

    from mongoengine import Q

    from nomad import infrastructure, search
    from nomad import processing as proc
    from nomad.app.v1 import models

    infrastructure.setup_mongo()
    infrastructure.setup_elastic()

    if uploads is not None and len(uploads) == 0:
        uploads = None  # None meaning all uploads
    else:
        uploads = set(uploads)

    entries_mongo_query_q = Q()
    if entries_mongo_query:
        entries_mongo_query_q = Q(**json.loads(entries_mongo_query))

    entries_query_uploads: set[str] | None = None

    if entries_es_query is not None:
        entries_es_query_dict = json.loads(entries_es_query)
        results = search.search(
            owner='admin',
            query=entries_es_query_dict,
            pagination=models.MetadataPagination(page_size=0),
            user_id=config.services.admin_user_id,
            aggregations={
                'uploads': models.Aggregation(
                    terms=models.TermsAggregation(
                        quantity='upload_id',
                        pagination=models.AggregationPagination(page_size=10000),
                    )
                )
            },
        )

        entries_query_uploads = {
            cast(str, bucket.value)
            for bucket in results.aggregations['uploads'].terms.data
            if results.aggregations['uploads'].terms.data is not None
        }  # pylint: disable=no-member

    if outdated:
        entries_mongo_query_q &= Q(nomad_version={'$ne': config.meta.version})

    if processing_failure_entries or processing_failure or processing_necessary:
        entries_mongo_query_q &= Q(process_status=proc.ProcessStatus.FAILURE)

    if processing_incomplete_entries or processing_incomplete or processing_necessary:
        entries_mongo_query_q &= Q(
            process_status__in=proc.ProcessStatus.STATUSES_PROCESSING
        )

    upload_filter_q = Q(upload_id__in=list(uploads)) if uploads is not None else Q()
    if entries_mongo_query_q == Q():
        # If there is no entry based query, we get the list of all uploads from the upload
        # and not the entry collection. This ensures that we will also catch uploads that
        # do not have an entry.
        mongo_entry_based_uploads = set(
            proc.Upload.objects(upload_filter_q).distinct(field='upload_id')  # type: ignore
        )
    else:
        mongo_entry_based_uploads = set(
            proc.Entry.objects(entries_mongo_query_q & upload_filter_q).distinct(
                field='upload_id'
            )  # type: ignore
        )

    if entries_query_uploads is not None:
        entries_query_uploads = entries_query_uploads.intersection(
            mongo_entry_based_uploads
        )
    else:
        entries_query_uploads = mongo_entry_based_uploads

    if entries_query_uploads:
        uploads_mongo_query_q = Q(upload_id__in=list(entries_query_uploads))
    else:
        uploads_mongo_query_q = Q()

    if uploads_mongo_query:
        uploads_mongo_query_q &= Q(**json.loads(uploads_mongo_query))

    if published:
        uploads_mongo_query_q &= Q(publish_time__exists=True)

    if unpublished:
        uploads_mongo_query_q &= Q(publish_time__exists=False)

    if processing:
        uploads_mongo_query_q &= Q(
            process_status__in=proc.ProcessStatus.STATUSES_PROCESSING
        )

    if processing_failure_uploads or processing_failure or processing_necessary:
        uploads_mongo_query_q &= Q(process_status=proc.ProcessStatus.FAILURE)

    if processing_incomplete_uploads or processing_incomplete or processing_necessary:
        uploads_mongo_query_q &= Q(
            process_status__in=proc.ProcessStatus.STATUSES_PROCESSING
        )

    final_query = uploads_mongo_query_q
    if uploads is not None:
        final_query &= Q(upload_id__in=list(uploads))

    if unindexed:
        # Select uploads that currently have no indexed entries in ES.
        candidate_uploads = proc.Upload.objects(final_query)  # type: ignore
        unindexed_upload_ids: list[str] = []
        for upload in candidate_uploads:
            if upload.total_entries_count == 0:
                unindexed_upload_ids.append(upload.upload_id)
                continue

            results = search.search(
                owner='admin',
                query={'upload_id': upload.upload_id},
                pagination=models.MetadataPagination(page_size=0),
                user_id=config.services.admin_user_id,
            )
            if results.pagination.total == 0:
                unindexed_upload_ids.append(upload.upload_id)

        final_query &= Q(upload_id__in=unindexed_upload_ids)

    return final_query, proc.Upload.objects(final_query)  # type: ignore


@uploads.command(help='List selected uploads')
@click.argument('UPLOADS', nargs=-1)
@click.option('--required', type=str, help='The required in JSON format')
@click.option('-o', '--output', type=str, help='The file to write data to')
@click.pass_context
def export(ctx, uploads, required, output: str):
    import sys
    import time
    import zipfile

    from nomad.archive import ArchiveQueryError, RequiredReader
    from nomad.files import UploadFiles
    from nomad.processing import Entry
    from nomad.utils import get_logger

    logger = get_logger(__name__)

    if not output:
        logger.error('no output given')
        sys.exit(1)

    if not output.endswith('.zip'):
        logger.error('only zip output is supported')
        sys.exit(1)

    output_file = zipfile.ZipFile(output, 'w', allowZip64=True)

    def write(entry_id, archive_data):
        archive_json = json.dumps(archive_data)
        output_file.writestr(
            f'{entry_id}.json', archive_json, compress_type=zipfile.ZIP_DEFLATED
        )

    _, uploads = _query_uploads(uploads, **ctx.obj.uploads_kwargs)

    try:
        required_data = json.loads(required)
    except Exception as e:
        logger.error('could not parse required', exc_info=e)
        sys.exit(1)

    try:
        required_reader = RequiredReader(required_data)
    except Exception as e:
        logger.error('could not validate required', exc_info=e)
        sys.exit(1)

    def get_rss():
        return time.time()

    start_time = get_rss()

    upload_count = 0
    total_count = 0
    for upload in uploads:
        upload_id = upload.upload_id
        upload_files = UploadFiles.get(upload_id)
        upload_count += 1
        entry_ids = list(entry.entry_id for entry in Entry.objects(upload_id=upload_id))  # type: ignore
        entry_count = 0
        for entry_id in entry_ids:
            entry_count += 1
            total_count += 1
            try:
                if upload_files is not None:  # Added check
                    with upload_files.read_archive(entry_id) as archive:
                        archive_data = required_reader.read(
                            archive, entry_id, upload_id
                        )
                        write(entry_id, archive_data)
            except ArchiveQueryError as e:
                logger.error('could not read archive', exc_info=e, entry_id=entry_id)
            except KeyError as e:
                logger.error('missing archive', exc_info=e, entry_id=entry_id)

            if total_count % 100 == 0:
                print(
                    f'{upload_count:5}/{len(uploads)} {entry_count:5}/{len(entry_ids)} {total_count:5} {(get_rss() - start_time)} {upload_id}'
                )

        if upload_files is not None:  # Added check
            upload_files.close()

    output_file.close()


@uploads.command(help='List selected uploads')
@click.argument('UPLOADS', nargs=-1)
@click.option(
    '-e',
    '--entries',
    is_flag=True,
    help='Include details about upload entries in the output.',
)
@click.option(
    '--ids',
    is_flag=True,
    help='Only include the upload ids in the output.',
)
@click.option(
    '--json', is_flag=True, help='Output a JSON array instead of a tabulated list.'
)
@click.option(
    '--size',
    type=int,
    default=10,
    help='Controls the maximum size of returned uploads, use -1 to return all.',
)
@click.pass_context
def ls(ctx, uploads, entries, ids, json, size):
    import tabulate

    _, uploads = _query_uploads(uploads, **ctx.obj.uploads_kwargs)

    def row(upload):
        if ids:
            return [upload.upload_id]

        rows = [
            upload.upload_id,
            upload.upload_name,
            upload.main_author,
            upload.process_status,
            upload.published,
        ]

        if entries:
            rows += [
                upload.total_entries_count,
                upload.failed_entries_count,
                upload.total_entries_count - upload.processed_entries_count,
            ]

        return rows

    if ids:
        headers = ['id']
    else:
        headers = ['id', 'upload_name', 'user', 'process', 'published']
        if entries:
            headers += ['entries', 'failed', 'processing']

    total_count = uploads.count()

    if size >= 0:
        uploads = uploads[:size]

    if json:
        print(
            dumps(
                [{k: v for k, v in zip(headers, row(upload))} for upload in uploads]
            ).decode()
        )
    else:
        if total_count > uploads.count():
            print(
                f'Showing the first {uploads.count()} (out of {total_count}) uploads selected...'
            )
        else:
            print(f'Showing all {uploads.count()} uploads selected...')
        print(tabulate.tabulate([row(upload) for upload in uploads], headers=headers))


@uploads.command(help='Change the owner of the upload and all its entries.')
@click.argument('USERNAME', nargs=1)
@click.argument('UPLOADS', nargs=-1)
@click.pass_context
def chown(ctx, username, uploads):
    from nomad import datamodel
    from nomad.config import config

    _, uploads = _query_uploads(uploads, **ctx.obj.uploads_kwargs)

    print(f'{uploads.count()} uploads selected, changing owner ...')

    admin_user_id = config.services.admin_user_id
    if not admin_user_id:
        raise click.ClickException('No admin user configured for ownership transfer.')

    user = datamodel.User.get(username=username)
    for upload in uploads:
        upload.start_edit_upload_metadata(
            {'metadata': {'main_author': user.user_id}}, admin_user_id
        )


@uploads.command(help='Reset the processing state.')
@click.argument('UPLOADS', nargs=-1)
@click.option('--with-entries', is_flag=True, help='Also reset all entries.')
@click.option(
    '--success',
    is_flag=True,
    help='Set the process status to success instead of pending',
)
@click.option(
    '--failure',
    is_flag=True,
    help='Set the process status to failure instead of pending.',
)
@click.pass_context
def reset(ctx, uploads, with_entries, success, failure):
    from nomad import processing as proc

    _, uploads = _query_uploads(uploads, **ctx.obj.uploads_kwargs)
    uploads_count = uploads.count()

    print(f'{uploads_count} uploads selected, resetting their processing ...')

    i = 0
    for upload in uploads:
        if with_entries:
            entry_update = proc.Entry.reset_pymongo_update()
            if success:
                entry_update['process_status'] = proc.ProcessStatus.SUCCESS
            if failure:
                entry_update['process_status'] = proc.ProcessStatus.FAILURE

            proc.Entry._get_collection().update_many(
                dict(upload_id=upload.upload_id), {'$set': entry_update}
            )

        upload.reset(force=True)
        if success:
            upload.process_status = proc.ProcessStatus.SUCCESS
        if failure:
            upload.process_status = proc.ProcessStatus.FAILURE
        upload.save()
        i += 1
        print(f'resetted {i} of {uploads_count} uploads')


@uploads.command(help='(Re-)index all entries of the given uploads.')
@click.argument('UPLOADS', nargs=-1)
@click.option(
    '--parallel',
    default=1,
    type=int,
    help='Use the given amount of parallel processes. Default is 1.',
)
@click.option(
    '--transformer',
    help='Qualified name to a Python function that should be applied to each EntryMetadata.',
)
@click.option(
    '--print-progress',
    default=0,
    type=int,
    help='Prints a dot every given seconds. Can be used to keep terminal open that have an i/o-based timeout.',
)
@click.pass_context
def index(ctx, uploads, parallel, transformer, print_progress):
    from nomad import search

    transformer_func = None
    if transformer is not None:
        import importlib

        module_name, func_name = transformer.rsplit('.', 1)
        module = importlib.import_module(module_name)
        transformer_func = getattr(module, func_name)

    _, uploads = _query_uploads(uploads, **ctx.obj.uploads_kwargs)

    def transform(entries):
        for entry in entries:
            try:
                entry = transformer_func(entry)
            except Exception as e:
                import traceback

                traceback.print_exc()
                print(
                    f'   ERROR failed to transform entry (stop transforming for upload): {str(e)}'
                )
                break

    def index_upload(upload, logger):
        with upload.entries_metadata() as entries:
            if transformer is not None:
                transform(entries)
            archives = [entry.m_parent for entry in entries]
            search.index(archives, refresh=True)

        return True

    _run_parallel(
        uploads, parallel, index_upload, 'index', print_progress=print_progress
    )


def delete_upload(
    upload, skip_es: bool = False, skip_files: bool = False, skip_mongo: bool = False
):
    from nomad import files, search, utils
    from nomad import processing as proc

    # delete elastic
    if not skip_es:
        search.delete_upload(upload_id=upload.upload_id, refresh=True)

    # delete files
    if not skip_files:
        # do it twice to get the two potential versions 'public' and 'staging'
        for _ in range(0, 2):
            upload_files = files.UploadFiles.get(upload_id=upload.upload_id)

            try:
                if upload_files is not None:
                    upload_files.delete()
            except Exception as e:
                logger = utils.get_logger(__name__)
                logger.error('could not delete files', exc_info=e)
                break

    # delete mongo
    if not skip_mongo:
        proc.Entry.objects(upload_id=upload.upload_id).delete()  # type: ignore
        upload.delete()


@uploads.command(help='Delete selected upload')
@click.argument('UPLOADS', nargs=-1)
@click.option(
    '--skip-es', help='Keep the elastic index version of the data.', is_flag=True
)
@click.option('--skip-mongo', help='Keep uploads and entries in mongo.', is_flag=True)
@click.option('--skip-files', help='Keep all related files.', is_flag=True)
@click.pass_context
def rm(ctx, uploads, skip_es, skip_mongo, skip_files):
    _, uploads = _query_uploads(uploads, **ctx.obj.uploads_kwargs)

    print(f'{uploads.count()} uploads selected, deleting ...')

    for upload in uploads:
        delete_upload(
            upload, skip_es=skip_es, skip_mongo=skip_mongo, skip_files=skip_files
        )


@uploads.command(help='Reprocess selected uploads.')
@click.argument('UPLOADS', nargs=-1)
@click.option(
    '--parallel',
    default=1,
    type=int,
    help='Use the given amount of parallel processes. Default is 1.',
)
@click.option(
    '--process-running', is_flag=True, help='Also reprocess already running processes.'
)
@click.option(
    '--setting',
    type=str,
    multiple=True,
    help='key=value to overwrite a default reprocess config setting.',
)
@click.option(
    '--print-progress',
    default=0,
    type=int,
    help='Prints a dot every given seconds. Can be used to keep terminal open that have an i/o-based timeout.',
)
@click.pass_context
def process(
    ctx,
    uploads,
    parallel: int,
    process_running: bool,
    setting: list[str],
    print_progress: int,
):
    _, uploads = _query_uploads(uploads, **ctx.obj.uploads_kwargs)
    settings: dict[str, bool] = {}
    for settings_str in setting:
        key, value = settings_str.split('=')
        settings[key] = bool(value)
    _run_processing(
        uploads,
        parallel,
        lambda upload: upload.process_upload(reprocess_settings=settings),
        'processing',
        process_running=process_running,
        reset_first=True,
        print_progress=print_progress,
    )


@uploads.command(help='Publish selected uploads.')
@click.argument('UPLOADS', nargs=-1)
@click.option(
    '--parallel',
    default=1,
    type=int,
    help='Use the given amount of parallel processes. Default is 1.',
)
@click.option(
    '--embargo-length',
    default=None,
    type=int,
    help='Use an embargo length (months) for the publication.',
)
@click.pass_context
def publish(
    ctx,
    uploads,
    parallel: int,
    embargo_length: int,
):
    _, uploads = _query_uploads(uploads, **ctx.obj.uploads_kwargs)
    _run_processing(
        uploads,
        parallel,
        lambda upload: upload.publish_upload(embargo_length=embargo_length),
        'publishing',
    )


@uploads.command(
    help="""Unpublish selected uploads.
WARNING: this operation changes the visibility of the target uploads!
This may break existing dependencies and should only be used with caution."""
)
@click.argument('UPLOADS', nargs=-1)
@click.option(
    '--parallel',
    default=1,
    type=int,
    help='Use the given amount of parallel processes. Default is 1.',
)
@click.pass_context
def unpublish(ctx, uploads, parallel: int):
    _, targets = _query_uploads(uploads, **ctx.obj.uploads_kwargs)
    _run_processing(
        targets,
        parallel,
        lambda upload: upload.unpublish_upload(),
        'unpublishing',
    )


@uploads.command(help='Repack selected uploads.')
@click.argument('UPLOADS', nargs=-1)
@click.pass_context
def re_pack(ctx, uploads):
    _, uploads = _query_uploads(uploads, **ctx.obj.uploads_kwargs)

    for upload in uploads:
        if not upload.published:
            print(f'Cannot repack unpublished upload {upload.upload_id}')
            continue

        upload.upload_files.re_pack(upload.with_embargo)
        print(f'successfully re-packed {upload.upload_id}')


@uploads.command(help='Attempt to abort the processing of uploads.')
@click.argument('UPLOADS', nargs=-1)
@click.option('--entries', is_flag=True, help='Only stop entries processing.')
@click.option(
    '--kill', is_flag=True, help='Use the kill signal and force task failure.'
)
@click.option(
    '--no-celery', is_flag=True, help='Do not attempt to stop the actual celery tasks'
)
@click.pass_context
def stop(ctx, uploads, entries: bool, kill: bool, no_celery: bool):
    import mongoengine

    from nomad import processing as proc
    from nomad import utils

    query, _ = _query_uploads(uploads, **ctx.obj.uploads_kwargs)

    logger = utils.get_logger(__name__)

    def stop_all(query):
        for process in query:
            logger_kwargs = dict(upload_id=process.upload_id)
            if isinstance(process, proc.Entry):
                logger_kwargs.update(entry_id=process.entry_id)

            if not no_celery:
                logger.info(
                    'send terminate celery task',
                    celery_task_id=process.celery_task_id,
                    kill=kill,
                    **logger_kwargs,
                )

            kwargs = {}
            if kill:
                kwargs.update(signal='SIGKILL')

                logger.info(
                    'fail proc',
                    celery_task_id=process.celery_task_id,
                    kill=kill,
                    **logger_kwargs,
                )

                process.fail('process terminate via nomad cli')

    running_query = query & mongoengine.Q(
        process_status__in=proc.ProcessStatus.STATUSES_PROCESSING
    )
    stop_all(proc.Entry.objects(running_query))  # type: ignore
    if not entries:
        stop_all(proc.Upload.objects(running_query))  # type: ignore


@uploads.command(
    help='Check certain integrity criteria and return a list of upload IDs.'
)
@click.argument('UPLOADS', nargs=-1)
@click.option(
    '--both-storages',
    is_flag=True,
    help='Select uploads that have both staging and public versions.',
)
@click.option(
    '--missing-storage',
    is_flag=True,
    help='Select uploads of which the corresponding raw folder (for staging) or the raw zip archive (for public)'
    ' is missing in the file system. This only checks the existence of folder/archive uploaded by user.'
    ' To check the contents, use the --missing-raw-files flag.',
)
@click.option(
    '--missing-raw-files',
    is_flag=True,
    help='Select uploads that any of the files listed in metadata/files is missing.'
    ' Use --check-all-entries to check files for all entries in the upload.'
    ' It uses the indexed ES data and does not open the msgpack archive files.',
)
@click.option(
    '--missing-archive-files',
    is_flag=True,
    help='Select uploads that miss archive (msgpack) files.',
)
@click.option(
    '--missing-index',
    is_flag=True,
    help='Select uploads of which the ES index information is missing.',
)
@click.option(
    '--entry-mismatch',
    is_flag=True,
    help='Select uploads that have different numbers of entries in mongo and ES.',
)
@click.option(
    '--nomad-version-mismatch',
    is_flag=True,
    help='Select uploads that have different nomad versions in archive and ES.',
)
@click.option(
    '--old-archive-format',
    is_flag=True,
    help='Select uploads that are using the old archive format (v1).',
)
@click.option(
    '--not-preferred-suffix',
    is_flag=True,
    help='Select uploads that are using the preferred (first) suffix in the configuration.',
)
@click.option(
    '--check-all-entries',
    is_flag=True,
    help='Check all entries in the upload, otherwise only check one entry per upload.',
)
@click.pass_context
def integrity(
    ctx,
    uploads,
    both_storages,
    missing_storage,
    missing_raw_files,
    missing_archive_files,
    missing_index,
    entry_mismatch,
    nomad_version_mismatch,
    old_archive_format,
    not_preferred_suffix,
    check_all_entries,
):
    from msglc import LazyWriter

    from nomad.app.v1.models import MetadataPagination, MetadataRequired
    from nomad.files import PublicUploadFiles, StagingUploadFiles
    from nomad.processing import Entry, Upload
    from nomad.search import search

    def search_params(upload_id: str):
        return {
            'query': {'upload_id': upload_id},
            'user_id': config.services.admin_user_id,
            'owner': 'admin',
        }

    def _check_both_storages(upload: Upload) -> bool:
        """
        Check if both storage versions are available.

        If both are available, return True.
        """
        try:
            # this will raise KeyError in either case since create=False
            return not (
                StagingUploadFiles(upload.upload_id).is_empty()
                or PublicUploadFiles(upload.upload_id).is_empty()
            )
        except KeyError:
            return False

    def _check_missing_storage(upload: Upload) -> bool:
        """
        Check if the corresponding upload structure is missing.

        If missing, return True.
        """
        return upload.upload_files.is_empty()

    def _check_missing_raw_files(upload: Upload) -> bool:
        """
        Check if any raw files are missing.

        If any are missing, return True.
        """
        search_results = search(
            required=MetadataRequired(include=['files', 'entry_id']),
            pagination=MetadataPagination(
                page_size=upload.total_entries_count if check_all_entries else 1
            ),
            **search_params(upload.upload_id),
        )

        upload_files = upload.upload_files

        return any(
            not upload_files.raw_exists(file)
            for entry in search_results.data
            for file in entry['files']
        )

    def _check_missing_archive_files(upload: Upload) -> bool:
        """
        Check if all archive files exist.

        If not, return True.
        """

        def _check_file_exist(path) -> bool:
            return not os.path.exists(path)

        if upload.published:
            upload_files = PublicUploadFiles(upload.upload_id)

            return _check_file_exist(
                upload_files.msg_fp(upload_files.access, True).os_path
            )

        upload_files = StagingUploadFiles(upload.upload_id)  # type: ignore

        entries = Entry.objects(upload_id=upload.upload_id)  # type: ignore
        if not check_all_entries:
            entries = [entries.first()]

        # noinspection PyProtectedMember
        return any(
            _check_file_exist(
                upload_files._archive_file_object(entry.entry_id, True).os_path  # type: ignore
            )
            for entry in entries
        )

    def _check_missing_index(upload: Upload) -> bool:
        """
        Check if the index exists in ES.

        If not indexed, return True.
        """
        search_results = search(
            pagination=MetadataPagination(page_size=0),
            **search_params(upload.upload_id),
        )

        return search_results.pagination.total == 0

    def _check_entry_mismatch(upload: Upload) -> bool:
        """
        Check if the number of entries in the mongo and in the ES are different.

        If different, return True.
        """
        search_results = search(
            pagination=MetadataPagination(page_size=0),
            **search_params(upload.upload_id),
        )

        return search_results.pagination.total != upload.total_entries_count

    def _check_nomad_version_mismatch(upload: Upload) -> bool:
        """
        Check if the nomad version in the archive and in the ES are different.

        If different, return True.
        """
        search_results = search(
            required=MetadataRequired(include=['nomad_version', 'entry_id']),
            pagination=MetadataPagination(page_size=upload.total_entries_count),
            **search_params(upload.upload_id),
        )

        entries = search_results.data
        if not check_all_entries:
            entries = [entries[0]]

        for entry in entries:
            entry_id = entry['entry_id']
            es_nomad_version = entry['nomad_version']
            with upload.upload_files.read_archive(entry_id) as archive:
                archive_nomad_version = archive[entry_id]['metadata']['nomad_version']
                if es_nomad_version != archive_nomad_version:
                    return True

        return False

    def _check_old_archive_format(upload: Upload) -> bool:
        """
        Check if the archives are using the old format by testing the magic bytes.

        If the magic bytes are not found, return True.
        If the magic bytes are found, return False.
        """

        def _check_magic(path) -> bool:
            with open(path, 'rb') as f:
                return LazyWriter.magic != f.read(LazyWriter.magic_len())

        if upload.published:
            upload_files = PublicUploadFiles(upload.upload_id)

            return _check_magic(upload_files.msg_fp(upload_files.access, True).os_path)

        upload_files = StagingUploadFiles(upload.upload_id)  # type: ignore

        entries = Entry.objects(upload_id=upload.upload_id)  # type: ignore
        if not check_all_entries:
            entries = [entries.first()]

        # noinspection PyProtectedMember
        return any(
            _check_magic(
                upload_files._archive_file_object(entry.entry_id, True).os_path  # type: ignore
            )
            for entry in entries
        )

    def _check_not_preferred_suffix(upload: Upload) -> bool:
        """
        Check if the archive version suffix matches the first configured archive_version_suffix.

        If archive_version_suffix is not in the path, return True.
        """
        suffix = config.fs.archive_version_suffix
        if isinstance(suffix, list):
            suffix = suffix[0]

        def _check_suffix(path) -> bool:
            return suffix not in path

        if upload.published:
            upload_files = PublicUploadFiles(upload.upload_id)

            return _check_suffix(upload_files.msg_fp(upload_files.access, True).os_path)

        upload_files = StagingUploadFiles(upload.upload_id)  # type: ignore

        entries = Entry.objects(upload_id=upload.upload_id)  # type: ignore
        if not check_all_entries:
            entries = [entries.first()]

        # noinspection PyProtectedMember
        return any(
            _check_suffix(
                upload_files._archive_file_object(entry.entry_id, True).os_path  # type: ignore
            )
            for entry in entries
        )

    all_checks = []
    if both_storages:
        all_checks.append(_check_both_storages)
    if missing_storage:
        all_checks.append(_check_missing_storage)
    if missing_raw_files:
        all_checks.append(_check_missing_raw_files)
    if missing_archive_files:
        all_checks.append(_check_missing_archive_files)
    if missing_index:
        all_checks.append(_check_missing_index)
    if entry_mismatch:
        all_checks.append(_check_entry_mismatch)
    if nomad_version_mismatch:
        all_checks.append(_check_nomad_version_mismatch)
    if old_archive_format:
        all_checks.append(_check_old_archive_format)
    if not_preferred_suffix:
        all_checks.append(_check_not_preferred_suffix)

    _, selected = _query_uploads(uploads, **ctx.obj.uploads_kwargs)

    for item in selected:
        try:
            if any(checker(item) for checker in all_checks):
                print(item.upload_id)
        except Exception:  # noqa
            print(item.upload_id)


@uploads.command(help="""Export one or more uploads as bundles.""")
@click.argument('UPLOADS', nargs=-1)
@click.option(
    '--out-dir',
    type=str,
    help=f'Output folder. Default value is "{config.bundle_export.default_cli_bundle_export_path}" (defined in config)',
)
@click.option(
    '--uncompressed',
    is_flag=True,
    help='Specify to export each bundle as an uncompressed folder, instead of a zip-file.',
)
@click.option(
    '--overwrite',
    is_flag=True,
    help='Specify to, for each bundle, overwrite the destination file/folder if it already exists.',
)
@click.option(
    '--settings',
    '-s',
    type=str,
    help="""The export settings, specified as json. Settings not specified in the dictionary
            will be set to the default values.""",
)
@click.option(
    '--ignore-errors',
    '-i',
    is_flag=True,
    help="""Specify to ignore errors on individual uploads, and continue exporting (the default
            behaviour is to abort on first failing upload).""",
)
@click.pass_context
def export_bundle(
    ctx, uploads, out_dir, uncompressed, overwrite, settings, ignore_errors
):
    from nomad.bundles import BundleExporter

    _, uploads = _query_uploads(uploads, **ctx.obj.uploads_kwargs)

    default_export_settings = config.bundle_export.default_settings.customize(
        config.bundle_export.default_settings_cli
    )
    if settings:
        settings = json.loads(settings)
        try:
            export_settings = default_export_settings.customize(settings)
            BundleExporter.check_export_settings(export_settings)
        except Exception as e:
            # Invalid setting provided
            print(e)
            print('\nAvailable settings and their configured default values:')
            for k, v in default_export_settings.model_dump().items():
                print(f'    {k:<40}: {v}')
            return -1
    else:
        export_settings = default_export_settings

    out_dir = out_dir or config.bundle_export.default_cli_bundle_export_path

    count = 0
    count_failed = 0
    try:
        for upload in uploads:
            try:
                count += 1
                print(f'Exporting upload {count} of {len(uploads)}: {upload.upload_id}')
                bundle_name = f'bundle_{upload.upload_id}' + (
                    '.zip' if not uncompressed else ''
                )
                export_path = os.path.abspath(os.path.join(out_dir, bundle_name))

                BundleExporter(
                    upload,
                    export_as_stream=False,
                    export_path=export_path,
                    zipped=not uncompressed,
                    overwrite=overwrite,
                    export_settings=export_settings,
                ).export_bundle()

            except Exception:
                count_failed += 1
                print(f'ERROR: Failed to export bundle: {upload.upload_id}')
                traceback.print_exc()
                if not ignore_errors:
                    print('Aborting export ...')
                    return -1
    finally:
        print('-' * 80 + '\nSummary:\n' + '-' * 80)
        print(f'Successfully exported: {count - count_failed} out of {len(uploads)}')
        if count_failed:
            print(f'FAILED to export: {count_failed}')
            if not ignore_errors:
                print(f'Aborted export for {len(uploads) - count} subsequent uploads.')


@uploads.command(
    help="""Import one or more uploads from bundles. Unless specified by the user,
                         the configured default import settings are used."""
)
@click.option(
    '--in',
    'input_path',
    type=str,
    help='The input path, specifying a bundle or a folder containing multiple bundles.',
)
@click.option(
    '--multi',
    '-m',
    is_flag=True,
    help=f"""Specify this flag if the input_path is a folder containing multiple bundles, and
             all these should be imported. If this option is specified without specifying --in, we
             will default the input path to {config.bundle_import.default_cli_bundle_import_path}""",
)
@click.option(
    '--settings',
    '-s',
    type=str,
    help="""The import settings, specified as json. Settings not specified in the dictionary
            will be set to the default values.""",
)
@click.option(
    '--embargo_length',
    '-e',
    type=int,
    help="""The embargo length (0-36 months). 0 means no embargo. If unspecified, the embargo
            period defined in the bundle will be used.""",
)
@click.option(
    '--ignore-errors',
    '-i',
    is_flag=True,
    help="""Specify this flag to ignore errors on individual bundles, and continue importing
            (the default behaviour is to abort on first failing bundle).""",
)
@click.pass_context
def import_bundle(ctx, input_path, multi, settings, embargo_length, ignore_errors):
    from nomad import infrastructure
    from nomad.bundles import BundleImporter

    for key, value in ctx.obj.uploads_kwargs.items():
        if value:
            print(
                f'Bad argument: "{key}" (query args are not applicable for bundle-import)'
            )
            return -1
    if not input_path and not multi:
        print('Need to specify a bundle source, using --in, --multi, or both.')
        return -1
    if multi and not input_path:
        input_path = config.bundle_import.default_cli_bundle_import_path

    if multi:
        if not os.path.isdir(input_path):
            print(f'No such folder: "{input_path}"')
            return -1
        bundle_paths = [
            os.path.abspath(os.path.join(input_path, element))
            for element in sorted(os.listdir(input_path))
        ]
    else:
        if not os.path.exists(input_path):
            print(f'Path not found: {input_path}')
        bundle_paths = [os.path.abspath(input_path)]

    default_import_settings = config.bundle_import.default_settings.customize(
        config.bundle_import.default_settings_cli
    )

    if settings:
        settings = json.loads(settings)
        try:
            import_settings = default_import_settings.customize(settings)
        except Exception as e:
            # Invalid setting provided
            print(e)
            print('\nAvailable settings and their configured default values:')
            for k, v in default_import_settings.dict().items():
                print(f'    {k:<40}: {v}')
            return -1
    else:
        import_settings = default_import_settings

    infrastructure.setup()

    count = count_failed = 0
    try:
        for bundle_path in bundle_paths:
            if BundleImporter.looks_like_a_bundle(bundle_path):
                count += 1
                print(f'Importing bundle: {bundle_path}')
                bundle_importer: BundleImporter | None = None
                try:
                    bundle_importer = BundleImporter(
                        None, import_settings, embargo_length
                    )
                    bundle_importer.open(bundle_path)
                    upload = bundle_importer.create_upload_skeleton()
                    try:
                        bundle_importer.import_bundle(upload, True)
                    finally:
                        bundle_importer.close()
                    if upload.errors:
                        raise RuntimeError(f'Import failed: {upload.errors[0]}')
                except Exception:
                    count_failed += 1
                    print(f'ERROR: Failed to import bundle: {bundle_path}')
                    traceback.print_exc()
                    if not ignore_errors:
                        print('Aborting import ...')
                        return -1
                finally:
                    if bundle_importer:
                        bundle_importer.close()
            else:
                print(f'Skipping, does not look like a bundle: {bundle_path}')
    finally:
        print('-' * 80 + '\nSummary:\n' + '-' * 80)
        print(f'Number of bundles successfully imported: {count - count_failed}')
        if count_failed:
            print(f'FAILED to import: {count_failed}')
            if not ignore_errors:
                print('Aborted import after first failure.')


def rename(path: str):
    return path.replace('v1.msg', 'v1.2.msg')


def only_v1(path: str):
    if 'v1.2.msg' in path:
        return True
    if 'v1.msg' in path:
        return not os.path.exists(path.replace('v1.msg', 'v1.2.msg'))

    return False


@uploads.command(help='Convert selected uploads to the new format.')
@click.argument('UPLOADS', nargs=-1)
@click.option(
    '--overwrite',
    '-o',
    is_flag=True,
    help='Overwrite existing target files.',
)
@click.option(
    '--delete-old',
    '-d',
    is_flag=True,
    help='Delete old archives once successfully converted.',
)
@click.option(
    '--migrate',
    '-m',
    is_flag=True,
    help='Only convert v1 archive files to v1.2 archive files.',
)
@click.option(
    '--force-repack',
    '-f',
    is_flag=True,
    help='Force repacking existing archives that are already in the new format',
)
@click.option(
    '--parallel',
    '-p',
    type=int,
    default=os.cpu_count(),
    help='Number of processes to use for conversion. Default is os.cpu_count().',
)
@click.option(
    '--size-limit',
    '-s',
    type=int,
    default=-1,
    help='Only handle archives under limited size in GB. Default is -1 (no limit).',
)
@click.pass_context
def convert_archive(
    ctx, uploads, overwrite, delete_old, migrate, force_repack, parallel, size_limit
):
    _, selected = _query_uploads(uploads, **ctx.obj.uploads_kwargs)

    from nomad.archive.converter import convert_upload

    if migrate:
        convert_upload(
            selected,
            overwrite=overwrite,
            delete_old=delete_old,
            transform=rename,
            if_include=only_v1,
            processes=parallel,
            force_repack=force_repack,
            size_limit=size_limit,
        )
    else:
        convert_upload(
            selected,
            overwrite=overwrite,
            delete_old=delete_old,
            processes=parallel,
            force_repack=force_repack,
            size_limit=size_limit,
        )
