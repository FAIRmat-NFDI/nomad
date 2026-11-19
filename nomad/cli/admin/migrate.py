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
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from pymongo import ReplaceOne
from pymongo.database import Collection, Database

from nomad import utils
from nomad.datamodel import Dataset
from nomad.parsing.parsers import parser_dict
from nomad.processing import Entry, ProcessStatus, Upload

_upload_keys_to_remove_v0 = (
    'published',
    'upload_path',
    'upload_time',
    'temporary',
    'joined',
)
_metadata_keys_to_flatten_v0 = (
    'pid',
    'external_id',
    'nomad_version',
    'nomad_commit',
    'comment',
    'references',
    'datasets',
)
_metadata_keys_to_remove_v0 = (
    'upload_name',
    'upload_time',
    'uploader',
    'published',
    'license',
    'with_embargo',
    'external_db',
    'shared_with',
    'coauthors',
    'processed',
    'domain',
    'raw_id',
)


class _CollectionStatistics(BaseModel):
    collection_name: str
    total: int = 0
    converted: int = 0
    migrated: int = 0


class _UpgradeStatistics(BaseModel):
    uploads: _CollectionStatistics = _CollectionStatistics(collection_name='Uploads')
    entries: _CollectionStatistics = _CollectionStatistics(collection_name='Entries')
    datasets: _CollectionStatistics = _CollectionStatistics(collection_name='Datasets')
    dois: _CollectionStatistics = _CollectionStatistics(collection_name='DOIs')


class _DatasetCacheItem(BaseModel):
    converted_dataset_dict: dict[str, Any] | None = None
    converted_doi_dict: dict[str, Any] | None = None
    ready_to_commit: bool = False


def create_collections_if_needed(db_dst: Database):
    """
    If the collections haven't yet been created, create them by calling .objects() on the
    MongoDocument class.
    """
    if 'upload' not in db_dst.list_collection_names():
        Upload.objects()  # type: ignore
    if 'entry' not in db_dst.list_collection_names():
        Entry.objects()  # type: ignore
    if 'dataset' not in db_dst.list_collection_names():
        Dataset.m_def.a_mongo.objects()


def migrate_mongo_uploads(
    db_src: Database,
    db_dst: Database,
    uploads_query: Any,
    failed_ids_to_file: bool,
    upload_update: dict[str, Any],
    entry_update: dict[str, Any],
    overwrite: str,
    fix_problems: bool,
    dry: bool,
):
    """Converts and/or migrates an upload and all related entries and datasets."""
    logger = utils.get_logger(__name__)

    uploads = db_src.upload.find(uploads_query)
    number_of_uploads = db_src.upload.count_documents(uploads_query)
    print(f'Found {number_of_uploads} uploads to import.')
    src_entry_collection = (
        db_src.calc if 'calc' in db_src.list_collection_names() else db_src.entry
    )
    dataset_cache: dict[str, _DatasetCacheItem] = {}
    stats = _UpgradeStatistics()
    stats.uploads.total = number_of_uploads
    count_treated = count_failures = count_processing = 0
    failed_and_skipped = []
    count_ignored = 0
    start_time = time.time()
    next_report_time = start_time + 60
    for upload_dict in uploads:
        upload_id = upload_dict.get('_id')
        try:
            if _is_processing(upload_dict):
                logger.warn(
                    f'Skipping upload because it is currently processing',
                    upload_id=upload_id,
                )

                count_processing += 1
                failed_and_skipped.append(upload_id)
            else:
                upload_dict_dst = db_dst.upload.find_one({'_id': upload_id})
                if upload_dict_dst:
                    if overwrite == 'always':
                        assert not _is_processing(upload_dict_dst), (
                            'Destination upload is processing'
                        )
                    elif overwrite == 'if-newer':
                        complete_time = upload_dict.get('complete_time', datetime.min)
                        complete_time_dst = upload_dict_dst.get(
                            'complete_time', datetime.min
                        )
                        if (
                            _is_processing(upload_dict_dst)
                            or complete_time <= complete_time_dst
                        ):
                            count_ignored += 1
                            continue
                    elif overwrite == 'never':
                        count_ignored += 1
                        continue

                entry_dicts, dataset_dicts, doi_dicts = _convert_mongo_upload(
                    db_src,
                    src_entry_collection,
                    upload_dict,
                    upload_update,
                    entry_update,
                    fix_problems,
                    dataset_cache,
                    stats,
                    logger,
                )
                if not dry:
                    _commit_upload(
                        upload_dict,
                        entry_dicts,
                        dataset_dicts,
                        doi_dicts,
                        db_dst,
                        stats,
                    )
                del entry_dicts, dataset_dicts  # To free up memory immediately
        except Exception as e:
            logger.error(
                f'Failed to migrate upload: {str(e)}', upload_id=upload_id, exc_info=e
            )
            count_failures += 1
            failed_and_skipped.append(upload_id)
        count_treated += 1
        # log progress report and estimated remaining time at regular intervals
        t = time.time()
        if t > next_report_time:
            elapsed = t - start_time
            progress = count_treated / number_of_uploads
            est_remain = elapsed * number_of_uploads / count_treated - elapsed
            print(
                f'Elapsed: {_seconds_to_h_m(elapsed)}, progress: {progress * 100.0:6.2f} %, '
                f'est. remain: {_seconds_to_h_m(est_remain)}'
            )
            next_report_time += 60
    # Write failed ids to file, if requested
    if failed_ids_to_file:
        with open(failed_ids_to_file, 'w') as f:
            for upload_id in failed_and_skipped:
                f.write(upload_id + '\n')
    # Print a summary
    summary = (
        f'Summary:\n\nElapsed time: {_seconds_to_h_m(time.time() - start_time)}\n\n'
    )
    summary += f'{"Doc type":<10}{"found":>20}{"converted":>20}{"migrated":>20}\n'
    summary += '-' * 70 + '\n'
    for sub_stats in (stats.uploads, stats.entries, stats.datasets, stats.dois):
        summary += (
            f'{sub_stats.collection_name:<10}{sub_stats.total:>20}'  # pylint: disable=E1101
            f'{sub_stats.converted:>20}{sub_stats.migrated:>20}\n'
        )
    if count_failures:
        summary += f'\n!!! THERE WERE ERRORS: {count_failures} upload(s) could not be converted/migrated\n\n'
    else:
        summary += '\nNo errors occurred :-)\n\n'
    if count_processing:
        summary += f'{count_processing} uploads were skipped since they are currently processing\n\n'
    if count_ignored:
        summary += f'{count_ignored} uploads were ignored because '
        if overwrite == 'if-newer':
            summary += 'they are not newer than the record in the destination db.\n\n'
        else:
            summary += 'they already exist in the destination db.\n\n'
    if dry:
        summary += 'Dry run - nothing written to the destination db\n\n'
    print(summary)
    if count_failures:
        return -1


def _convert_mongo_upload(
    db_src: Database,
    src_entry_collection: Collection,
    upload_dict: dict[str, Any],
    upload_update: dict[str, Any],
    entry_update: dict[str, Any],
    fix_problems: bool,
    dataset_cache: dict[str, _DatasetCacheItem],
    stats: _UpgradeStatistics,
    logger,
):
    """
    Converts (upgrades) an upload_dict and all related records. If successful,
    returns three lists: one with converted entry dicts, and one with converted dataset dicts,
    and one with converted DOI dicts.
    """
    upload_id = upload_dict['_id']
    published = upload_dict.get('publish_time') is not None
    # Determine which version we are migrating from
    is_v0 = 'user_id' in upload_dict

    _convert_mongo_proc(upload_dict)

    if is_v0:
        _rename_key(upload_dict, 'name', 'upload_name')
        _rename_key(upload_dict, 'create_time', 'upload_create_time')
        _rename_key(upload_dict, 'user_id', 'main_author')
        # Verify and then remove redundant legacy field 'published'.
        if 'published' in upload_dict:
            assert upload_dict['published'] == published, (
                'Inconsistency: published flag vs publish_time'
            )
        # Set license if missing
        if 'license' not in upload_dict:
            upload_dict['license'] = 'CC BY 4.0'
        for key in _upload_keys_to_remove_v0:
            if key in upload_dict:
                upload_dict.pop(key)

    # Fetch all entries as a dictionary
    entry_dicts = list(src_entry_collection.find({'upload_id': upload_id}))
    stats.entries.total += len(entry_dicts)
    if is_v0 and entry_dicts:
        # Need to pass through all entries to check consistency and calculate common_coauthos
        first_metadata = entry_dicts[0].get('metadata', {})
        first_with_embargo = first_metadata.get('with_embargo')
        first_shared_with = first_metadata.get('shared_with')
        first_shared_with_set = set(first_shared_with or ())
        first_entry_uploader = first_metadata.get('uploader')
        first_external_db = first_metadata.get('external_db')
        first_entry_coauthors = first_metadata.get('coauthors', ())
        common_coauthors = {_wrap_author(ca) for ca in first_entry_coauthors}

        fixed_external_db = False
        for entry_dict in entry_dicts:
            assert 'metadata' in entry_dict, 'Entry dict has no metadata key'
            entry_metadata_dict: dict[str, Any] = entry_dict['metadata']
            with_embargo = entry_metadata_dict.get('with_embargo')
            assert with_embargo == first_with_embargo, (
                'Inconsistent embargo settings for entries'
            )
            shared_with_set = set(entry_metadata_dict.get('shared_with') or ())
            assert shared_with_set == first_shared_with_set, (
                'Inconsistent shared_with settings for entries'
            )
            uploader = entry_metadata_dict.get('uploader')
            assert uploader == first_entry_uploader, 'Inconsistent uploader for entries'
            external_db = entry_metadata_dict.get('external_db')
            if external_db != first_external_db:
                if external_db and first_external_db:
                    # Problem is unfixable (two different non-empty values encountered)
                    assert False, 'Inconsistent external_db for entries - unfixable'
                elif not fix_problems:
                    assert False, (
                        'Inconsistent external_db for entries - use --fix-problems to fix'
                    )
                first_external_db = first_external_db or external_db  # Fix it
                fixed_external_db = True
            common_coauthors.intersection_update(
                _wrap_author(ca) for ca in entry_metadata_dict.get('coauthors', ())
            )

        if fixed_external_db:
            logger.warn(
                'Fixed inconsistent external_db for upload', upload_id=upload_id
            )

        # Check embargo setting on the upload
        if published:
            if first_with_embargo:
                assert upload_dict.get('embargo_length'), (
                    f'Embargo flag set on entries, but no embargo_length specified on upload'
                )
            else:
                upload_dict['embargo_length'] = 0
        if 'embargo_length' not in upload_dict:
            upload_dict['embargo_length'] = 0
        # Fields moving to the upload level
        upload_dict['reviewers'] = first_shared_with
        upload_dict['external_db'] = first_external_db
        # main_author
        if first_entry_uploader != upload_dict['main_author']:
            assert first_external_db, (
                'Different uploader on entry and upload, but external_db not set'
            )
            # It's ok, but we should fetch main_author from entries.uploader instead of upload.user_id
            upload_dict['main_author'] = first_entry_uploader
        # coauthors - put as many as possible in Upload.coauthors, the rest in entry.entry_coauthors
        if common_coauthors:
            upload_dict['coauthors'] = [
                ca
                for ca in first_entry_coauthors
                if _wrap_author(ca) in common_coauthors
            ]
    else:
        common_coauthors = {_wrap_author(ca) for ca in upload_dict.get('coauthors', ())}

    # Check that all required fields are there
    for field in (
        '_id',
        'upload_create_time',
        'main_author',
        'embargo_length',
        'license',
    ):
        assert upload_dict.get(field) is not None, (
            f'Missing required upload field: {field}'
        )

    if upload_update:
        upload_dict.update(upload_update)

    # migrate entries
    newly_encountered_dataset_ids: set[str] = set()
    for entry_dict in entry_dicts:
        assert not _is_processing(entry_dict), (
            f'the entry {entry_dict["_id"]} has status processing, but the upload is not processing.'
        )
        _convert_mongo_entry(entry_dict, common_coauthors, fix_problems, logger)
        # Convert datasets
        datasets = entry_dict.get('datasets')
        if datasets:
            converted_datasets = []
            for dataset_id in datasets:
                if dataset_id in dataset_cache:
                    ds_cache = dataset_cache[dataset_id]
                else:
                    # First time we encounter this dataset ref
                    newly_encountered_dataset_ids.add(dataset_id)
                    ds_cache = _get_dataset_cache_data(dataset_id, db_src, logger)
                    dataset_cache[dataset_id] = ds_cache
                    if ds_cache.converted_dataset_dict is not None:
                        stats.datasets.total += 1
                    if ds_cache.converted_doi_dict is not None:
                        stats.dois.total += 1
                if ds_cache.converted_dataset_dict is None:
                    # Dataset record does not exist
                    assert fix_problems, (
                        f'Missing dataset reference encountered: {dataset_id} - '
                        'use --fix-problems to fix'
                    )
                    logger.warn(
                        'Fixing (removing) missing dataset reference for entry',
                        entry_id=entry_dict['_id'],
                        dataset_id=dataset_id,
                        upload_id=upload_id,
                    )
                elif not ds_cache.converted_dataset_dict:
                    # The value must be the empty dict, which represents a conversion failure
                    assert False, f'Reference to unconvertible dataset {dataset_id}'
                else:
                    # Dataset record exists and has been converted suuccessfully
                    converted_datasets.append(dataset_id)
            entry_dict['datasets'] = converted_datasets

        if entry_update:
            entry_dict.update(entry_update)

    # All conversion successful! Ready to migrate
    dataset_dicts: list[dict[str, Any]] = []
    doi_dicts: list[dict[str, Any]] = []
    for dataset_id in newly_encountered_dataset_ids:
        ds_cache = dataset_cache[dataset_id]
        if not ds_cache.ready_to_commit:
            if ds_cache.converted_dataset_dict:
                dataset_dicts.append(ds_cache.converted_dataset_dict)
                stats.datasets.converted += 1
            if ds_cache.converted_doi_dict:
                doi_dicts.append(ds_cache.converted_doi_dict)
                stats.dois.converted += 1
            ds_cache.ready_to_commit = True
    stats.entries.converted += len(entry_dicts)
    stats.uploads.converted += 1
    return entry_dicts, dataset_dicts, doi_dicts


def _convert_mongo_entry(
    entry_dict: dict[str, Any], common_coauthors: set, fix_problems: bool, logger
):
    _convert_mongo_proc(entry_dict)
    # Validate the id and possibly fix problems
    generated_entry_id = utils.generate_entry_id(
        entry_dict['upload_id'], entry_dict['mainfile'], entry_dict.get('mainfile_key')
    )
    if entry_dict['_id'] != generated_entry_id:
        if not fix_problems:
            assert False, (
                f'Entry id {entry_dict["_id"]} does not match generated value - use --fix-problems to fix'
            )
        logger.warn(
            'Fixing bad id for entry',
            old_id=entry_dict['_id'],
            entry_id=generated_entry_id,
        )
        entry_dict['_id'] = generated_entry_id
    # Convert old v0 metadata
    if 'metadata' in entry_dict:
        _rename_key(entry_dict, 'calc_id', 'entry_id')
        _rename_key(entry_dict, 'parser', 'parser_name')
        _rename_key(entry_dict, 'create_time', 'entry_create_time')
        _rename_key(entry_dict, 'metadata.last_processing', 'last_processing_time')
        _rename_key(entry_dict, 'metadata.last_edit', 'last_edit_time')
        _rename_key(entry_dict, 'metadata.calc_hash', 'entry_hash')

        entry_metadata = entry_dict['metadata']
        # Entry coauthors
        entry_coauthors = entry_metadata.get('coauthors')
        if entry_coauthors:
            entry_coauthors = [
                ca for ca in entry_coauthors if _wrap_author(ca) not in common_coauthors
            ]
            entry_dict['entry_coauthors'] = entry_coauthors or None
        # Flatten keys
        for key in _metadata_keys_to_flatten_v0:
            if key in entry_metadata:
                entry_dict[key] = entry_metadata.pop(key)
        # Remove redundant keys from the metadata dict; then it should be empty
        for key in _metadata_keys_to_remove_v0:
            if key in entry_metadata:
                entry_metadata.pop(key)
        assert not entry_metadata, (
            f'Unexpected fields in Calc.metadata: {repr(entry_metadata)}'
        )
        entry_dict.pop('metadata')

    # Check that all required fields are populated
    for field in ('_id', 'upload_id', 'entry_create_time', 'parser_name'):
        assert entry_dict.get(field) is not None, (
            f'Missing required entry field: {field}'
        )

    # Check if the parser exists
    parser_name = entry_dict.get('parser_name')
    if parser_name == 'parsers/gpaw2':
        parser_name = 'parsers/gpaw'
    entry_dict['parser_name'] = parser_name
    assert parser_name in parser_dict, f'Parser does not exist: {parser_name}'


def _convert_mongo_proc(proc_dict: dict[str, Any]):
    if 'tasks_status' in proc_dict:
        # Old v0 version
        process_status = proc_dict['tasks_status']
        if process_status == 'CREATED':
            process_status = ProcessStatus.READY
        assert process_status in ProcessStatus.STATUSES_VALID_IN_DB, (
            f'Invalid tasks_status: {process_status}'
        )
        proc_dict['process_status'] = process_status
        last_status_message = proc_dict.get('last_status_message')
        if not last_status_message:
            # Generate a nicer last_status_message
            current_process: str | None = proc_dict.get('current_process')
            errors: list[str] | None = proc_dict.get('errors')
            if errors:
                last_status_message = f'Process {current_process} failed: {errors[-1]}'
            elif current_process and process_status == ProcessStatus.SUCCESS:
                last_status_message = (
                    f'Process {current_process} completed successfully'
                )
            else:
                assert False, (
                    f'Unexpected proc state: {current_process} / {process_status}'
                )
            proc_dict['last_status_message'] = last_status_message

        for field in ('tasks', 'current_task', 'tasks_status'):
            proc_dict.pop(field, None)


def _convert_mongo_dataset(dataset_dict: dict[str, Any]):
    _rename_key(dataset_dict, 'name', 'dataset_name')
    _rename_key(dataset_dict, 'created', 'dataset_create_time')
    _rename_key(dataset_dict, 'modified', 'dataset_modified_time')
    # Check that all required fields are there
    for field in ('dataset_name',):
        assert dataset_dict.get(field) is not None, (
            f'Dataset missing required field {field}'
        )


def _convert_mongo_doi(doi_dict: dict[str, Any]):
    pass


def _get_dataset_cache_data(
    dataset_id: str, db_src: Database, logger
) -> _DatasetCacheItem:
    """
    Fetches and converts a dataset and related doi record. None values will be used in the
    returned :class:`_DatasetCacheItem` if a record is missing; empty dicts are used if the
    record exists but can't be converted
    """
    dataset_dict = db_src.dataset.find_one({'_id': dataset_id})
    doi_dict = None
    if dataset_dict:
        try:
            _convert_mongo_dataset(dataset_dict)
            doi = dataset_dict.get('doi')
            if doi:
                doi_dict = db_src.d_o_i.find_one({'_id': doi})
                # DOI records were not stored originally, so some older datasets with
                # doi might not have a corresponding DOI record. The data is also
                # not super critical, so if it doesn't exist we will just ignore it
                if doi_dict:
                    _convert_mongo_doi(doi_dict)
        except Exception as e:
            # Conversion failed
            logger.error(str(e), dataset_id=dataset_id, exc_info=e)
            dataset_dict = {}
            doi_dict = {}
    return _DatasetCacheItem(
        converted_dataset_dict=dataset_dict, converted_doi_dict=doi_dict
    )


def _is_processing(proc_dict: dict[str, Any]) -> bool:
    process_status = proc_dict.get('tasks_status')  # Used in v0
    if not process_status:
        process_status = proc_dict['process_status']
    return process_status in ProcessStatus.STATUSES_PROCESSING


def _commit_upload(
    upload_dict: dict[str, Any],
    entry_dicts: list[dict[str, Any]],
    dataset_dicts: list[dict[str, Any]],
    doi_dicts: list[dict[str, Any]],
    db_dst: Database,
    stats: _UpgradeStatistics,
):
    # Commit datasets
    if dataset_dicts:
        dataset_writes = []
        for dataset_dict in dataset_dicts:
            dataset_writes.append(
                ReplaceOne({'_id': dataset_dict['_id']}, dataset_dict, upsert=True)
            )
        db_dst.dataset.bulk_write(dataset_writes)
        stats.datasets.migrated += len(dataset_dicts)
    # Commit DOIs
    if doi_dicts:
        doi_writes = []
        for doi_dict in doi_dicts:
            doi_writes.append(
                ReplaceOne({'_id': doi_dict['_id']}, doi_dict, upsert=True)
            )
        db_dst.d_o_i.bulk_write(doi_writes)
        stats.dois.migrated += len(doi_writes)
    # Commit upload
    db_dst.upload.replace_one({'_id': upload_dict['_id']}, upload_dict, upsert=True)
    stats.uploads.migrated += 1
    # Commit entries
    if entry_dicts:
        entry_writes = []
        for entry_dict in entry_dicts:
            entry_writes.append(
                ReplaceOne({'_id': entry_dict['_id']}, entry_dict, upsert=True)
            )
        db_dst.entry.bulk_write(entry_writes)
        stats.entries.migrated += len(entry_dicts)


def _rename_key(d: dict[str, Any], old_name: str, new_name: str):
    """
    Renames a key in the provided dictionary `d`, from `old_name` to `new_name`. We may use
    "point notation" in `old_name`, i.e. "metadata.external_id" will look for a
    key 'external_id' in d['metadata'].
    """
    parts = old_name.split('.')
    d_current = d
    for i, part in enumerate(parts):
        if i == len(parts) - 1:
            if d_current and part in d_current:
                d[new_name] = d_current.pop(part)
        else:
            if d_current and part in d_current:
                d_current = d_current[part]
            else:
                break  # Not found


def _seconds_to_h_m(seconds: float):
    """Converts a time in seconds to a nicer string with hour and minute parts."""
    return f'{seconds // 3600:>3.0f} h {(seconds % 3600) / 60:2.0f} min'


def _wrap_author(author):
    """
    Takes an author reference (str or dict) and if it is a dict, converts it into a tuple.
    If the author is a str, it is instead returned as it is. This is used to get an object
    which is hashable and can be used in sets.
    """
    if isinstance(author, str):
        return author
    return tuple((k, author[k]) for k in sorted(author.keys()))
