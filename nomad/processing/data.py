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

"""
This module comprises a set of persistent document classes that hold all user related
data. These are information about users, their uploads and datasets, the associated
entries, and files


.. autoclass:: Entry

.. autoclass:: Upload

"""

import asyncio
import base64
import concurrent.futures
import copy
import hashlib
import os
import os.path
import threading
import uuid
from collections.abc import Coroutine, Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Union, cast

import requests
import rfc3161ng
import validators
from fastapi.exceptions import RequestValidationError
from mongoengine import (
    BooleanField,
    DictField,
    EmbeddedDocument,
    EmbeddedDocumentField,
    IntField,
    ListField,
    StringField,
)
from pymongo import UpdateOne
from structlog import wrap_logger
from structlog.processors import StackInfoRenderer, TimeStamper, format_exc_info
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.service import RPCError, RPCStatusCode

from nomad import client, datamodel, infrastructure, metainfo, parsing, search, utils
from nomad.actions import TaskQueue
from nomad.actions.client import get_client
from nomad.app.v1.models import (
    Aggregation,
    MetadataEditRequest,
    MetadataPagination,
    MetadataRequired,
    TermsAggregation,
    restrict_query_to_upload,
)
from nomad.archive import to_json
from nomad.common import is_safe_relative_path
from nomad.config import config
from nomad.config.models.config import Reprocess
from nomad.config.models.plugins import ExampleUploadEntryPoint
from nomad.datamodel import (
    AuthLevel,
    EditableUserMetadata,
    EntryArchive,
    EntryMetadata,
    MongoEntryMetadata,
    MongoSystemMetadata,
    MongoUploadMetadata,
    ServerContext,
)
from nomad.datamodel.datamodel import RFC3161Timestamp
from nomad.files import (
    PathObject,
    PublicUploadFiles,
    RawPathInfo,
    StagingUploadFiles,
    UploadFiles,
    mkdtemp,
)
from nomad.metainfo.data_type import Datatype, Datetime
from nomad.mongo.doi import EmbeddedDOI
from nomad.mongo.fields import UTCDateTimeField
from nomad.mongo.groups import MongoUserGroup, user_group_exists
from nomad.mongo.package import PackageDefinition
from nomad.normalizing import normalizers
from nomad.parsing import Parser
from nomad.parsing.parsers import match_parser, parser_dict, parsers
from nomad.processing.base import (
    Proc,
    ProcessAlreadyRunning,
    ProcessFailure,
    ProcessStatus,
    process_local,
)
from nomad.search import update_metadata as es_update_metadata
from nomad.tracing import trace_span, traced
from nomad.utils.pydantic import CustomErrorWrapper
from nomad.utils.structlogging import ISO8601_UTC_FORMAT
from nomad.workflows.shared_objects import (
    DeleteUploadWorkflowInput,
    EditUploadMetadataWorkflowInput,
    ImportBundleWorkflowInput,
    ProcessEntryActivityInput,
    ProcessExampleUploadWorkflowInput,
    PublishExternallyWorkflowInput,
    PublishUploadWorkflowInput,
    TransferUploadOwnershipWorkflowInput,
    UploadProcessingWorkflowInput,
)
from nomad.workflows.utils import extract_temporal_error_details

section_metadata = datamodel.EntryArchive.metadata.name
section_workflow = datamodel.EntryArchive.workflow2.name
section_results = datamodel.EntryArchive.results.name
parser_min_level = min([parser.level for parser in parsers])

mongo_upload_metadata = tuple(
    quantity.name for quantity in MongoUploadMetadata.m_def.definitions
)
mongo_entry_metadata = tuple(
    quantity.name for quantity in MongoEntryMetadata.m_def.definitions
)
mongo_system_metadata = tuple(
    quantity.name for quantity in MongoSystemMetadata.m_def.definitions
)
mongo_entry_metadata_except_system_fields = tuple(
    quantity_name
    for quantity_name in mongo_entry_metadata
    if quantity_name not in mongo_system_metadata
)
editable_metadata: dict[str, metainfo.Definition] = {
    quantity.name: quantity
    for quantity in EditableUserMetadata.m_def.definitions
    if isinstance(quantity, metainfo.Quantity)
}


class RunThread(threading.Thread):
    def __init__(self, coro: Coroutine):
        self.coro = coro
        self.result = None
        self.exception = None
        super().__init__()

    def run(self):
        try:
            self.result = asyncio.run(self.coro)
        except Exception as e:
            self.exception = e


def run_async(coro: Coroutine):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # In jupyter/ fastapi there is already a loop running
        thread = RunThread(coro)
        thread.start()
        thread.join()
        if thread.exception is not None:
            raise thread.exception
        return thread.result
    else:
        # Create our own loop
        return asyncio.run(coro)


def assert_user_group_exists(group_id: str):
    assert user_group_exists(group_id), f"User group '{group_id}' does not exist."


def _pack_log_event(logger, method_name, event_dict):
    try:
        log_data = dict(event_dict)
        log_data.update(
            **{
                key: value
                for key, value in getattr(logger, '_context', {}).items()
                if key
                not in [
                    'service',
                    'deployment',
                    'upload_id',
                    'entry_id',
                    'mainfile',
                    'process_status',
                ]
            }
        )
        log_data.update(logger=logger.name)

        return log_data
    except Exception:
        # raising an exception would cause an indefinite loop
        return event_dict


_log_processors = [
    StackInfoRenderer(),
    _pack_log_event,
    format_exc_info,
    TimeStamper(fmt=ISO8601_UTC_FORMAT, utc=True),
]


def get_rfc3161_token(
    hash_string: str,
    server: str | None = None,
    cert: str | None = None,
    username: str | None = None,
    password: str | None = None,
    hash_algorithm: str | None = None,
) -> bytes | None:
    """
    Get RFC3161 compliant time stamp as a list of int.
    """
    if server is None:
        server = config.rfc3161_timestamp.server
    if cert is None:
        cert = config.rfc3161_timestamp.cert
    if username is None:
        username = config.rfc3161_timestamp.username
    if password is None:
        password = config.rfc3161_timestamp.password
    if hash_algorithm is None:
        hash_algorithm = config.rfc3161_timestamp.hash_algorithm

    # if no server assigned, does not apply RFC3161
    if not server:
        return None

    # when server requires authentication, use the provided credentials
    params: dict[str, Any] = dict(
        username=username,
        password=password,
        hashname=hash_algorithm if hash_algorithm else 'sha256',
    )

    try:
        if cert:
            if os.path.exists(cert):
                # a local file
                with open(cert, 'rb') as f:
                    params['certificate'] = f.read()
            else:
                # a network location
                params['certificate'] = requests.get(cert, timeout=10).content
        stamper = rfc3161ng.RemoteTimestamper(server, **params)
        return stamper(data=hash_string.encode('utf-8'))
    except Exception:
        return None


class MetadataEditRequestHandler:
    """
    Class for handling a request to edit metadata. The edit request can be defined either by
    metadata files in the raw directory or a json dictionary complying with the
    :class:`MetadataEditRequest` format. If the edit request is limited to a specific upload,
    `upload_id` should be specified (only when this is the case can upload level metadata be edited).
    """

    @classmethod
    def edit_metadata(
        cls,
        edit_request_json: dict[str, Any],
        upload_id: str,
        user: datamodel.User,
        *,
        wait_for_processing: bool = True,
    ) -> dict[str, Any]:
        """
        Method to verify and execute a generic request to edit metadata from a certain user.
        The request is specified as a json dictionary (requests defined by metadata files
        are not handled by this method). Optionally, the request could be restricted
        to a single upload by specifying `upload_id` (this is necessary when editing upload
        level attributes). If `edit_request_json` has `verify_only` set to True, only
        verification is carried out (i.e. nothing is actually updated). To just run the
        verification should be quick in comparison to actually executing the request (which
        may take some time and requires one or more @process to finish). If the request passes
        the verification step and `verify_only` is not set to True, we will send the request
        for execution, by initiating the @process :func:`edit_upload_metadata` for each affected
        upload.

        The method returns a json dictionary with verified metadata (references resolved to explicit
        IDs, list actions always expressed as dicts with "op" and "values", etc), or raises
        an exception, namely:
         -  A :class:`ValidationError` if the request json can't be parsed by pydantic
         -  A :class:`RequestValidationError` with information about validation failures and
            their location (most errors should be of this type, provided that the json is valid)
         -  A :class:`ProcessAlreadyRunning` exception if one of the affected uploads is blocked
            by another process
         -  Some other type of exception, if something goes wrong unexpectedly (should hopefully
            never happen)
        """
        logger = utils.get_logger('nomad.processing.edit_metadata')
        handler = MetadataEditRequestHandler(logger, user, edit_request_json, upload_id)
        # Validate the request
        handler.validate_json_request()  # Should raise an exception if something looks wrong

        if not edit_request_json.get('verify_only'):
            # Check if any of the affected uploads are processing
            for upload in handler.affected_uploads:
                upload.reload()
                if upload.queue_blocked:
                    raise ProcessAlreadyRunning(
                        f'Upload {upload.upload_id} is blocked by another process'
                    )
            # Looks good, try to trigger processing
            for upload in handler.affected_uploads:
                upload.edit_upload_metadata(
                    edit_request_json,
                    user.user_id,
                    wait_for_processing=wait_for_processing,
                )  # Trigger the process
        # All went well, return a verified json as response
        verified_json = copy.deepcopy(edit_request_json)
        verified_json['metadata'] = handler.verified_metadata
        verified_json['entries'] = handler.verified_entries
        return verified_json

    def __init__(
        self,
        logger,
        user: datamodel.User,
        edit_request: StagingUploadFiles | dict[str, Any],
        upload_id: str | None = None,
    ):
        # Initialization
        assert user, 'Must specify `user`'
        assert isinstance(edit_request, StagingUploadFiles | dict), (
            '`edit_request` must be either a json dictionary or a :class:`StagingUploadfiles` object'
        )
        self.logger = logger
        self.user = user
        self.edit_request = edit_request
        self.upload_id = upload_id

        self.errors: list[
            CustomErrorWrapper
        ] = []  # A list of all encountered errors, if any
        self.edit_attempt_locs: list[
            tuple[str, ...]
        ] = []  # locs where user has attempted to edit something
        self.required_auth_level = (
            AuthLevel.none
        )  # Maximum required auth level for the edit
        self.required_auth_level_locs: list[
            tuple[str, ...]
        ] = []  # locs where maximal auth level is needed
        self.encountered_users: dict[
            str, str
        ] = {}  # { ref: user_id | None }, ref = user_id | username | email
        self.encountered_datasets: dict[
            str, datamodel.Dataset
        ] = {}  # { ref : dataset | None }, ref = dataset_id | dataset_name

        # Used when edit_request = json dict
        self.edit_request_obj: MetadataEditRequest | None = None
        self.verified_metadata: dict[
            str, Any
        ] = {}  # The metadata specified at the top/root level
        self.verified_entries: dict[
            str, dict[str, Any]
        ] = {}  # Metadata specified for individual entries
        self.affected_uploads: list[Upload] | None = (
            None  # A MetadataEditRequest may involve multiple uploads
        )

        # Used when edit_request = files
        self.verified_file_metadata_cache: dict[str, dict[str, Any]] = {}
        self.root_file_entries: dict[str, dict[str, Any]] | None = (
            None  # `entries` defined in the root metadata file
        )

    def validate_json_request(self):
        """Validates the provided edit_request_json."""
        # Validate the request json. Will raise ValidationError if json is malformed
        assert isinstance(self.edit_request, dict), 'edit_request should be json dict'
        self.edit_request_obj = MetadataEditRequest(**self.edit_request)
        try:
            if not self.upload_id and not self.edit_request_obj.query:
                return self._error('Must specify `query` or `upload_id`', 'query')
            if self.edit_request_obj.entries and not self.edit_request_obj.entries_key:
                return self._error(
                    'Must specify `entries_key` when specifying `entries`',
                    'entries_key',
                )

            can_edit_upload_quantities = bool(
                self.upload_id and not self.edit_request_obj.query
            )
            if self.edit_request_obj.metadata:
                self.verified_metadata = self._verify_metadata(
                    self.edit_request['metadata'],
                    ('metadata',),
                    can_edit_upload_quantities,
                )
            if self.edit_request_obj.entries:
                for key, entry_metadata in self.edit_request['entries'].items():
                    verified_metadata = self._verify_metadata(
                        entry_metadata, ('entries', key), False
                    )
                    self.verified_entries[key] = verified_metadata

            if not self.edit_attempt_locs:
                return self._error('No fields to update specified', 'metadata')
            if self.required_auth_level == AuthLevel.admin and not self.user.is_admin:
                for loc in self.required_auth_level_locs:
                    self._error('Admin rights required', loc)
                return

            embargo_length: int = self.verified_metadata.get('embargo_length')
            try:
                self.affected_uploads = self._find_request_uploads()
            except Exception as e:
                return self._error('Could not evaluate query: ' + str(e), 'query')
            if not self.affected_uploads:
                if self.edit_request_obj.query:
                    return self._error('No matching entries found', 'query')
                return self._error('No matching upload found', 'upload_id')

            is_admin = self.user.is_admin
            group_ids = MongoUserGroup.get_ids_by_user_id(self.user.user_id)
            for upload in self.affected_uploads:
                is_main_author = self.user.user_id == upload.main_author
                is_coauthor = self.user.user_id in upload.coauthors or (
                    not set(group_ids).isdisjoint(upload.writer_groups)
                )

                if self.required_auth_level == AuthLevel.coauthor:
                    has_access = is_admin or is_main_author or is_coauthor
                elif self.required_auth_level == AuthLevel.main_author:
                    has_access = is_admin or is_main_author
                elif self.required_auth_level == AuthLevel.admin:
                    has_access = is_admin
                else:
                    assert False, 'Invalid required_auth_level'  # Should not happen

                if not has_access:
                    for loc in self.required_auth_level_locs:
                        self._error(
                            f'{self.required_auth_level} access required for upload '
                            f'{upload.upload_id}',
                            loc,
                        )
                    return

                # Other checks
                if embargo_length is not None:
                    if (
                        upload.published
                        and not self.user.is_admin
                        and embargo_length != 0
                    ):
                        self._error(
                            f'Upload {upload.upload_id} is published, embargo can only be lifted',
                            ('metadata', 'embargo_length'),
                        )
        except Exception as e:
            # Something unexpected has gone wrong
            self.logger.error(e)
            raise
        finally:
            if self.errors:
                raise RequestValidationError(errors=self.errors)

    def get_upload_mongo_metadata(self, upload: 'Upload') -> dict[str, Any]:
        """
        Returns a dictionary with metadata to set on the mongo Upload object. If the provided
        `edit_request` is a json dictionary the :func: `validate_json_request`) is assumed
        to have been run first.
        """
        if isinstance(self.edit_request, dict):
            # edit_request = json dict
            if self.verified_metadata:
                return self._mongo_metadata(upload, self.verified_metadata)
        else:
            # edit_request = files
            return self._mongo_metadata(
                upload, self._verified_file_metadata(path_dir='')
            )
        return {}

    def get_entry_mongo_metadata(
        self, upload: 'Upload', entry: 'Entry'
    ) -> dict[str, Any]:
        """
        Returns a dictionary with metadata to set on the mongo entry object. If the provided
        `edit_request` is a json dictionary the :func: `validate_json_request`) is assumed
        to have been run first.
        """
        verified_metadata: dict[str, Any] = {}
        if isinstance(self.edit_request, dict):
            # edit_request = json dict
            if self.verified_metadata:
                verified_metadata.update(self.verified_metadata)
            if self.verified_entries:
                entry_key = self._get_entry_key(
                    entry, self.edit_request_obj.entries_key
                )
                entry_metadata = self.verified_entries.get(entry_key)
                if entry_metadata:
                    # We also have metadata specified for this particular entry
                    verified_metadata.update(entry_metadata)
        else:
            # edit_request = files
            path_dir = os.path.dirname(entry.mainfile)
            while True:
                for quantity, verified_value in self._verified_file_metadata(
                    path_dir
                ).items():
                    verified_metadata.setdefault(quantity, verified_value)
                if not path_dir:
                    break  # We're done witht the root folder (the raw dir)
                path_dir = os.path.dirname(path_dir)  # Move to the parent folder

            if self.root_file_entries:
                entry_metadata = self.root_file_entries.get(entry.mainfile)
                if entry_metadata:
                    # Metadata for this entry specified under 'entries' on the root level
                    loc = ('entries', entry.mainfile)
                    if not isinstance(entry_metadata, dict):
                        self._error('Expected dictionary', loc)
                    else:
                        verified_entry_metadata = self._verify_metadata(
                            entry_metadata,
                            loc,
                            can_edit_upload_quantities=False,
                            auth_level=AuthLevel.admin
                            if self.user.is_admin
                            else AuthLevel.main_author,
                        )
                        verified_metadata.update(verified_entry_metadata)
        return self._mongo_metadata(entry, verified_metadata)

    def _error(self, msg: str, loc: str | tuple[str, ...]):
        """Registers an error associated with a particular location."""
        self.errors.append(CustomErrorWrapper(Exception(msg), loc=loc))
        self.logger.error(msg, loc=loc)

    def _verify_metadata(
        self,
        raw_metadata: dict[str, Any],
        loc: tuple[str, ...],
        can_edit_upload_quantities: bool,
        auth_level: AuthLevel | None = None,
    ) -> dict[str, Any]:
        """
        Performs basic validation of a dictionary with *raw* metadata (i.e. metadata with
        key-value pairs as defined in the request json dictionary or metadata files), and
        returns a dictionary with the same structure, but containing only the *verified* metadata.
        The verified metadata contains only the key-value pairs that pass validation. Moreover:
            1)  For lists, the verified value is always expressed as a list operation, i.e. a
                dictionary with `op` and `values`.
            2)  User references (which can be specified by a user_id, a username, or an email)
                are always converted to user_id
            3)  dataset references (which can be specified either by dataset_id or dataset_name)
                are always replaced with dataset_ids, and it is verified that none of the
                datasets has a doi.
            4)  Only `add` and `remove` operations are allowed for datasets.
        """
        rv = {}
        for quantity_name, raw_value in raw_metadata.items():
            if raw_value is not None:
                success, verified_value = self._verify_metadata_single(
                    quantity_name,
                    raw_value,
                    loc + (quantity_name,),
                    can_edit_upload_quantities,
                    auth_level,
                )
                if success:
                    rv[quantity_name] = verified_value
        return rv

    def _verify_metadata_single(
        self,
        quantity_name: str,
        raw_value: Any,
        loc: tuple[str, ...],
        can_edit_upload_quantities: bool,
        auth_level: AuthLevel,
    ) -> tuple[bool, Any]:
        """
        Performs validation of a single value. Returns (success, verified_value).
        """
        definition = editable_metadata.get(quantity_name)
        if not definition:
            self._error('Unknown quantity', loc)
            return False, None

        self.edit_attempt_locs.append(loc)

        quantity_auth_level = getattr(definition, 'a_auth_level', AuthLevel.coauthor)

        if auth_level is not None:
            # Our auth level is known, check it immediately
            if quantity_auth_level > auth_level:
                self._error(f'{quantity_auth_level} privileges required', loc)
                return False, None
        if quantity_auth_level > self.required_auth_level:
            self.required_auth_level = quantity_auth_level
            self.required_auth_level_locs = [loc]
        elif quantity_auth_level == self.required_auth_level:
            self.required_auth_level_locs.append(loc)
        if quantity_name in mongo_upload_metadata and not can_edit_upload_quantities:
            self._error('Quantity can only be edited on the upload level', loc)
            return False, None

        try:
            if definition.is_scalar:
                return True, self._verified_value_single(definition, raw_value)
            else:
                # We have a non-scalar quantity
                if isinstance(raw_value, dict):
                    # The raw value is a dict - expected to contain keys add/remove/set
                    assert raw_value, 'No operation specified'
                    for key in raw_value:
                        assert key in ('set', 'add', 'remove'), (
                            f'Allowed operations are `set`, `add`, and `remove`, got {key}'
                        )
                    assert 'set' not in raw_value or (
                        'add' not in raw_value and 'remove' not in raw_value
                    ), 'Cannot specify both `set` and `add`/`remove` operations'
                    if quantity_name == 'datasets' and 'set' in raw_value:
                        self._error(
                            'Only `add` and `remove` operations permitted for datasets',
                            loc,
                        )
                        return False, None
                    raw_ops = raw_value
                else:
                    # The raw value is not a dict, but a value or list of values
                    # -> interpret as a set-operation (or, for datasets: add-operation)
                    if quantity_name == 'datasets':
                        raw_ops = {'add': raw_value}
                    else:
                        raw_ops = {'set': raw_value}

                verified_ops = {}
                for op, values in raw_ops.items():
                    values = values if isinstance(values, list) else [values]
                    verified_values = [
                        self._verified_value_single(definition, v, op) for v in values
                    ]
                    verified_ops[op] = verified_values

                assert set(verified_ops.get('add', [])).isdisjoint(
                    verified_ops.get('remove', [])
                ), 'The same value cannot be specified for both `add` and `remove`'

                return True, verified_ops
        except Exception as e:
            self._error(str(e), loc)
            return False, None

    def _verified_value_single(
        self, definition: metainfo.Definition, value: Any, op: str | None = None
    ) -> Any:
        """
        Verifies a *singular* raw value (i.e. for list quantities we should run this method
        for each value in the list, not with the list itself as input). Returns the verified
        value, which may be different from the origial value. It:
            1) ensures a return value of a primitive type (str, int, float, bool or None),
            2) that user refs exist,
            3) that datasets refs exist and do not have a doi.
            4) Translates user refs to user_id and dataset refs to dataset_id, if needed.
        Raises exception in case of failures.
        """
        if isinstance(
            definition.type, Datatype
        ) and definition.type.standard_type().startswith(
            ('str', 'int', 'float', 'bool')
        ):
            if definition.name == 'embargo_length':
                assert 0 <= value <= 36, 'Value should be between 0 and 36'
            elif definition.name == 'references':
                assert validators.url(value), 'Please enter a valid URL ...'
            elif definition.name == 'coauthor_groups':
                assert value != 'all', (
                    "Special group 'all' is invalid for field coauthor_groups."
                )
                assert_user_group_exists(value)
            elif definition.name == 'reviewer_groups':
                assert_user_group_exists(value)
            return None if value == '' else value

        elif definition.type == metainfo.Datetime or isinstance(
            definition.type, Datetime
        ):
            if value is not None:
                datetime.fromisoformat(
                    value
                )  # Throws exception if badly formatted timestamp
            return None if value == '' else value

        elif isinstance(definition.type, metainfo.MEnum):
            assert isinstance(value, str), 'Expected a string value'
            if value == '':
                return None
            assert value in definition.type, f'Bad enum value {value}'
            return value

        elif isinstance(definition.type, metainfo.Reference):
            assert isinstance(value, str), 'Expected a string value'
            reference_type = definition.type.target_section_def.section_cls
            if reference_type in [datamodel.User, datamodel.Author]:
                if value in self.encountered_users:
                    user_id = self.encountered_users[value]
                else:
                    # New user reference encountered, try to fetch it
                    user_id = None
                    # TODO: should be explicit about identifier type
                    for key in ('user_id', 'username', 'email'):
                        try:
                            if (user := datamodel.User.get(**{key: value})) is None:
                                raise KeyError
                            user_id = user.user_id
                            break
                        except KeyError:
                            pass
                    self.encountered_users[value] = user_id
                assert user_id is not None, f'User reference not found: `{value}`'
                return user_id

            elif reference_type == datamodel.Dataset:
                dataset = self._get_dataset(value)
                assert dataset is not None, f'Dataset reference not found: `{value}`'
                assert self.user.is_admin or dataset.user_id == self.user.user_id, (
                    f'Dataset `{value}` does not belong to you'
                )
                assert op == 'add' or not dataset.doi, (
                    f'Dataset `{value}` has a doi, can only add entries to it'
                )
                return dataset.dataset_id

        else:
            assert False, 'Unhandled value type'  # Should not happen

    def _mongo_metadata(
        self, mongo_doc: Union['Upload', 'Entry'], verified_metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Calculates the upload or entry level *mongo* metadata, given a `mongo_doc` and a
        dictionary with *verified* metadata. The mongo metadata are the key-value pairs
        to set on `mongo_doc` in order to carry out the edit request.
        """
        rv: dict[str, Any] = {}
        for quantity_name, verified_value in verified_metadata.items():
            if (
                isinstance(mongo_doc, Entry)
                and quantity_name not in mongo_entry_metadata
            ):
                continue
            elif (
                isinstance(mongo_doc, Upload)
                and quantity_name not in mongo_upload_metadata
            ):
                continue
            rv[quantity_name] = self._mongo_value(
                mongo_doc, quantity_name, verified_value
            )
        return rv

    def _mongo_value(self, mongo_doc, quantity_name: str, verified_value: Any) -> Any:
        definition = editable_metadata[quantity_name]
        if definition.is_scalar:
            if (
                definition.type == metainfo.Datetime
                or isinstance(definition.type, Datetime)
            ) and verified_value:
                return datetime.fromisoformat(verified_value)
            return verified_value
        # Non-scalar property. The verified value should be a dict with operations
        old_list = getattr(mongo_doc, quantity_name, [])
        new_list = [] if 'set' in verified_value else old_list.copy()
        for op, values in verified_value.items():
            for v in values:
                if op == 'add' or op == 'set':
                    if v not in new_list:
                        if (
                            quantity_name in ('coauthors', 'reviewers')
                            and v == mongo_doc.main_author
                        ):
                            continue  # Prevent adding the main author to coauthors or reviewers
                        new_list.append(v)
                elif op == 'remove':
                    if v in new_list:
                        new_list.remove(v)
        return new_list

    def _get_entry_key(self, entry: 'Entry', entries_key: str) -> str:
        if entries_key == 'entry_id':
            return entry.entry_id
        elif entries_key == 'mainfile':
            return entry.mainfile
        assert False, f'Invalid entries_key: {entries_key}'

    def _get_dataset(self, ref: str) -> datamodel.Dataset:
        """
        Gets a dataset. They can be identified either using dataset_id or dataset_name, but
        only datasets belonging to the user can be specified using names. If no matching
        dataset can be found, None is returned.
        """
        if ref in self.encountered_datasets:
            return self.encountered_datasets[ref]
        else:
            # First time we encounter this ref
            try:
                dataset = datamodel.Dataset.m_def.a_mongo.get(dataset_id=ref)
            except KeyError:
                try:
                    dataset = datamodel.Dataset.m_def.a_mongo.get(
                        user_id=self.user.user_id, dataset_name=ref
                    )
                except KeyError:
                    dataset = None
            self.encountered_datasets[ref] = dataset
        return dataset

    def _restricted_request_query(self, upload_id: str | None = None):
        """
        Gets the query of the request, if it has any. If we have a query and if an `upload_id`
        is specified, we return a modified query, by restricting the original query to this upload.
        """
        query = self.edit_request_obj.query
        if upload_id and query:
            # Restrict query to the specified upload
            return restrict_query_to_upload(query, upload_id)
        return query

    def _find_request_uploads(self) -> list['Upload']:
        """Returns a list of :class:`Upload`s matching the edit request."""
        query = self._restricted_request_query(self.upload_id)
        if query:
            # Perform the search, aggregating by upload_id
            search_response = search.search(
                user_id=self.user.user_id,
                owner=self.edit_request_obj.owner,
                query=query,
                aggregations=dict(
                    agg=Aggregation(terms=TermsAggregation(quantity='upload_id'))
                ),
                pagination=MetadataPagination(page_size=0),
            )
            terms = search_response.aggregations['agg'].terms  # pylint: disable=no-member
            return [Upload.get(bucket.value) for bucket in terms.data]  # type: ignore
        elif self.upload_id:
            # Request just specifies an upload_id, no query
            try:
                return [Upload.get(self.upload_id)]
            except KeyError:
                pass
        return []

    def find_request_entries(self, upload: 'Upload') -> Iterable['Entry']:
        """Finds the entries of the specified upload which are effected by the request."""
        query = self._restricted_request_query(upload.upload_id)
        if query:
            # We have a query. Execute it to get the entries.
            search_result = search.search_iterator(
                user_id=self.user.user_id,
                owner=self.edit_request_obj.owner,
                query=query,
                required=MetadataRequired(include=['entry_id']),
            )
            for result in search_result:
                yield Entry.get(result['entry_id'])
        else:
            # We have no query. Return all entries for the upload
            yield from Entry.objects(upload_id=upload.upload_id)  # type: ignore

    def _verified_file_metadata(self, path_dir: str) -> dict[str, Any]:
        """
        Gets the verified metadata defined in a metadata file in the provided directory.
        The `path_dir` should be relative to the `raw` folder. Empty string gives the "root"
        level metadata (i.e. the metadata defined by a file located in the `raw` directory).
        If no parseable metadata file is found in the directory, an empty dict is returned.
        A cached value is used if possible, otherwise we read the file, verify the content,
        and caches and return the results.
        """
        if path_dir not in self.verified_file_metadata_cache:
            # Not cached
            file_metadata = cast(
                StagingUploadFiles, self.edit_request
            ).metadata_file_cached(path_dir)
            if path_dir == '':
                can_edit_upload_quantities = True
                loc: tuple[str, ...] = ('/',)
                if 'entries' in file_metadata:
                    self.root_file_entries = file_metadata.pop('entries')
                    if not isinstance(self.root_file_entries, dict):
                        self._error(
                            '`entries` defined in the root metadata file is not a dictionary',
                            'entries',
                        )
                        self.root_file_entries = None
            else:
                can_edit_upload_quantities = False
                loc = (path_dir,)
            verified_file_metadata = self._verify_metadata(
                file_metadata,
                loc,
                can_edit_upload_quantities,
                auth_level=AuthLevel.admin
                if self.user.is_admin
                else AuthLevel.main_author,
            )
            self.verified_file_metadata_cache[path_dir] = verified_file_metadata
        return self.verified_file_metadata_cache[path_dir]


class Timestamp(EmbeddedDocument):
    token_seed = StringField()
    token = StringField()
    tsa_server = StringField()


class Entry(Proc):
    """
    Instances of this class represent entries. This class manages the elastic
    search index entry, files, and archive for the respective entry.

    It also contains information about the entry's processing state.

    The attribute list, does not include the various metadata properties generated
    while parsing, including ``code_name``, ``code_version``, etc.

    Attributes:
        upload_id: the id of the upload to which this entry belongs
        entry_id: the id of this entry
        entry_hash: the hash of the entry files
        entry_create_time: the date and time of the creation of the entry
        last_processing_time: the date and time of the last processing
        last_edit_time: the date and time the user metadata was last edited
        mainfile: the mainfile (including path in upload) that was used to create this entry
        parser_name: the name of the parser used to process this entry
        pid: the legacy NOMAD pid of the entry
        external_id: a user provided external id. Usually the id for an entry in an
            external database where the data was imported from
        nomad_version: the NOMAD version used for the last processing
        nomad_commit: the NOMAD commit used for the last processing
        nomad_distro_commit_url: the NOMAD distro commit url used for the last processing
        comment: a user provided comment for this entry
        references: user provided references (URLs) for this entry
        entry_coauthors: a user provided list of co-authors specific for this entry. Note
            that normally, coauthors should be set on the upload level.
        datasets: a list of user curated datasets this entry belongs to
    """

    upload_id = StringField(required=True)
    entry_id = StringField(primary_key=True)
    entry_hash = StringField()
    entry_create_time = UTCDateTimeField(required=True)
    last_processing_time = UTCDateTimeField()
    last_edit_time = UTCDateTimeField()
    mainfile = StringField()
    mainfile_key = StringField()
    parser_name = StringField()
    pid = StringField()
    external_id = StringField()
    nomad_version = StringField()
    nomad_commit = StringField()
    nomad_distro_commit_url = StringField()
    comment = StringField()
    references = ListField(StringField())
    entry_coauthors = ListField()
    datasets = ListField(StringField())

    entry_timestamp = EmbeddedDocumentField(Timestamp)

    meta: Any = {
        'strict': False,
        'auto_create_index': False,
        'indexes': [
            'upload_id',
            'parser_name',
            ('upload_id', 'mainfile'),
            ('upload_id', 'mainfile', 'mainfile_key'),
            ('upload_id', 'parser_name'),
            ('upload_id', 'process_status'),
            ('upload_id', 'nomad_version'),
            'process_status',
            'last_processing_time',
            'entry_create_time',
            'datasets',
            'pid',
        ],
    }

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('entry_create_time', datetime.now(timezone.utc))
        super().__init__(*args, **kwargs)
        self._parser_results: EntryArchive = None
        self._is_initial_processing: bool = False
        self._upload: Upload = None
        self._upload_files: StagingUploadFiles = None
        self._proc_logs: list[Any] = []
        self._child_entries: list[Entry] = []

        self._entry_metadata: EntryMetadata = None
        self._perform_index = True

    def _temporal_heartbeat(self):
        """
        Sends a single heartbeat to the temporal server.

        This method checks if temporal is enabled and if the code is currently
        running within a temporal activity context. If both are true, it calls
        `activity.heartbeat()` to inform the temporal server that the activity is
        still alive.
        """
        if activity.in_activity():
            activity.heartbeat()

    @classmethod
    def get(cls, id) -> 'Entry':
        return cls.get_by_id(id, 'entry_id')

    @property
    def mainfile_file(self) -> PathObject:
        return self.upload_files.raw_file_object(self.mainfile)

    @property
    def processed(self) -> bool:
        return self.process_status == ProcessStatus.SUCCESS

    @property
    def upload(self) -> 'Upload':
        if not self._upload:
            self._upload = Upload.get(self.upload_id)
            self._upload.worker_hostname = self.worker_hostname
        return self._upload

    def _initialize_metadata_for_processing(self):
        """
        Initializes self._entry_metadata and self._parser_results in preparation for processing.
        Existing values in mongo are loaded first, then generated system values are
        applied.
        """
        self._entry_metadata = EntryMetadata()
        self._apply_metadata_from_mongo(self.upload, self._entry_metadata)
        self._apply_metadata_from_process(self._entry_metadata)

        self._parser_results = EntryArchive(m_context=self.upload.archive_context)
        self._parser_results.metadata = self._entry_metadata
        self.save()

    def _apply_metadata_from_process(self, entry_metadata: EntryMetadata):
        """
        Applies metadata generated when processing or re-processing an entry to `entry_metadata`.

        Only update timestamp when entry is new or changed.

        The timestamp is stored in both mongo and archive as metadata.
        The mongo record is treated as the source of truth.
        If the processed archives are re-processed or loaded from external sources,
        the mongo database has no previously stored timestamp.
        In this case, the timestamp stored in the archive is used.
        If no previous timestamp is available, a new timestamp is generated.
        """
        distro_commit_url = utils.nomad_distro_metadata()
        entry_metadata.nomad_version = config.meta.version
        entry_metadata.nomad_version = config.meta.version
        entry_metadata.nomad_commit = ''
        entry_metadata.nomad_distro_commit_url = distro_commit_url or ''
        entry_metadata.entry_hash = self.upload_files.entry_hash(
            self.mainfile, self.mainfile_key
        )

        stored_seed, stored_token, stored_server = None, None, None
        if self.entry_timestamp:
            stored_seed = self.entry_timestamp.token_seed
            stored_token = (
                base64.b64decode(self.entry_timestamp.token)
                if self.entry_timestamp.token
                else None
            )
            stored_server = self.entry_timestamp.tsa_server

        # check if the new hash is different from the old one that is used for the timestamp
        if (
            entry_metadata.entry_hash == stored_seed
            and stored_token is not None
            and stored_server is not None
        ):
            # if matches, meaning that the old one is still valid, keep the old timestamp
            entry_metadata.entry_timestamp = RFC3161Timestamp(
                token_seed=stored_seed,
                token=stored_token,
                tsa_server=stored_server,  # maybe missing or different from the current config
                timestamp=rfc3161ng.get_timestamp(stored_token),
            )
            # will be written back to mongo in `.save()`
        else:
            # different from the old one, meaning two cases:
            # 1. there is no previously stored timestamp in mongo, but the entry has not changed,
            # 2. the entry has changed.

            has_existing_timestamp: bool = True
            if any((stored_seed is None, stored_token is None, stored_server is None)):
                # no previously stored timestamp
                # try if archive has one
                try:
                    with self.upload_files.read_archive(self.entry_id) as archive:
                        entry_timestamp = archive[self.entry_id]['metadata'][
                            'entry_timestamp'
                        ]
                        stored_seed = entry_timestamp['token_seed']
                        stored_token = base64.b64decode(entry_timestamp['token'])
                        stored_server = entry_timestamp['tsa_server']
                except Exception:  # noqa
                    stored_seed, stored_token, stored_server = None, None, None
                    has_existing_timestamp = False

            get_timestamp: bool = True  # do we need to get a new timestamp?
            if config.process.rfc3161_skip_published and self.upload.published:
                get_timestamp = False
            if entry_metadata.entry_hash == stored_seed:
                get_timestamp = False

            if get_timestamp:
                # entry is new or has changed
                if token := get_rfc3161_token(entry_metadata.entry_hash):
                    entry_metadata.entry_timestamp = RFC3161Timestamp(
                        token_seed=entry_metadata.entry_hash,
                        token=token,
                        tsa_server=config.rfc3161_timestamp.server,
                        timestamp=rfc3161ng.get_timestamp(token),
                    )
                else:
                    self.get_logger().info(
                        f'RFC3161 timestamp could not be obtained'
                        f' as the server {config.rfc3161_timestamp.server} cannot be reached.'
                    )
            elif has_existing_timestamp:
                # entry is unchanged
                entry_metadata.entry_timestamp = RFC3161Timestamp(
                    token_seed=stored_seed,
                    token=stored_token,
                    tsa_server=stored_server,
                    timestamp=rfc3161ng.get_timestamp(stored_token),
                )

        entry_metadata.files = self.upload_files.entry_files(self.mainfile)
        entry_metadata.last_processing_time = datetime.now(timezone.utc)
        entry_metadata.processing_errors = []

    def _apply_metadata_from_mongo(
        self, upload: 'Upload', entry_metadata: EntryMetadata
    ):
        """
        Loads entry metadata from mongo (that is: from `self` and the provided `upload` object)
        and applies the values to `entry_metadata`.
        """
        assert upload.upload_id == self.upload_id, (
            'Could not apply metadata: upload_id mismatch'
        )
        # Upload metadata
        for quantity_name in mongo_upload_metadata:
            setattr(entry_metadata, quantity_name, getattr(upload, quantity_name))
        # Entry metadata
        for quantity_name in mongo_entry_metadata:
            setattr(entry_metadata, quantity_name, getattr(self, quantity_name))
        # Special case: domain. May be derivable from mongo, or may have to be read from the archive
        if self.parser_name is not None:
            parser = parser_dict.get(self.parser_name)
            if parser and parser.domain:
                entry_metadata.domain = parser.domain

    def _apply_metadata_to_mongo_entry(self, entry_metadata: EntryMetadata):
        """
        Applies the metadata fields that are stored on the mongo entry level to self.
        In other words, basically the reverse operation of :func:`_apply_metadata_from_mongo`,
        but excluding upload level metadata and system fields (like mainfile, parser_name etc.).
        """
        entry_metadata_dict = entry_metadata.m_to_dict(include_defaults=True)
        for quantity_name in mongo_entry_metadata_except_system_fields:
            setattr(self, quantity_name, entry_metadata_dict.get(quantity_name))

        if not (rfc3161_timestamp := entry_metadata_dict.get('entry_timestamp', None)):
            return

        # setting rfc3161 related fields
        timestamp = Timestamp()
        for item in ('token_seed', 'token', 'tsa_server'):
            if value := rfc3161_timestamp.get(item, None):
                setattr(timestamp, item, value)
        self.entry_timestamp = timestamp
        self.save()

    def set_mongo_entry_metadata(self, *args, **kwargs):
        """
        Sets the entry level metadata in mongo. Expects either a positional argument
        which is an instance of :class:`EntryMetadata` or keyword arguments with data to set.
        """
        assert not (args and kwargs), (
            'Cannot provide both keyword arguments and a positional argument'
        )
        if args:
            assert len(args) == 1 and isinstance(args[0], EntryMetadata), (
                'Expected exactly one keyword argument of type `EntryMetadata`'
            )
            self._apply_metadata_to_mongo_entry(args[0])
        else:
            for key, value in kwargs.items():
                if key in mongo_entry_metadata_except_system_fields:
                    setattr(self, key, value)
                else:
                    assert False, f'Cannot set metadata quantity: {key}'

    def full_entry_metadata(self, upload: 'Upload') -> EntryMetadata:
        """
        Returns a complete set of :class:`EntryMetadata` including
        both the mongo metadata and the metadata from the archive.

        Arguments:
            upload: The :class:`Upload` to which this entry belongs. Upload level metadata
                and the archive files will be read from this object.
        """
        assert upload.upload_id == self.upload_id, 'Mismatching upload_id encountered'
        try:
            # instead of loading the whole archive, it should be enough to load the
            # parts that are referenced by section_metadata/EntryMetadata
            # TODO somehow it should determine which root sections too load from the metainfo
            # or configuration
            with upload.upload_files.read_archive(self.entry_id) as archive:
                entry_archive = archive[self.entry_id]
                entry_archive_dict = {
                    section_metadata: to_json(entry_archive[section_metadata])
                }
                if section_workflow in entry_archive:
                    entry_archive_dict[section_workflow] = to_json(
                        entry_archive[section_workflow]
                    )
                if section_results in entry_archive:
                    entry_archive_dict[section_results] = to_json(
                        entry_archive[section_results]
                    )
                entry_metadata = datamodel.EntryArchive.m_from_dict(
                    entry_archive_dict, m_context=ServerContext(upload)
                )[section_metadata]
                self._apply_metadata_from_mongo(upload, entry_metadata)
        except KeyError:
            # Due to hard processing failures, it might be possible that an entry might not
            # have an archive. Return the metadata that is available.
            if self._entry_metadata is not None:
                entry_metadata = self._entry_metadata
            else:
                entry_metadata = self.mongo_metadata(upload)

        if not entry_metadata.m_parent:
            # Other parts of the code might assume that a entry metadata is always part of
            # an actual archive.
            EntryArchive(metadata=entry_metadata)

        return entry_metadata

    def mongo_metadata(self, upload: 'Upload') -> EntryMetadata:
        """
        Returns a :class:`EntryMetadata` with mongo metadata only
        (fetched from `self` and `upload`), no archive metadata.
        """
        assert upload.upload_id == self.upload_id, 'Mismatching upload_id encountered'
        entry_metadata = EntryMetadata()
        self._apply_metadata_from_mongo(upload, entry_metadata)
        return entry_metadata

    @property
    def upload_files(self) -> StagingUploadFiles:
        if not self._upload_files:
            self._upload_files = StagingUploadFiles(self.upload_id)
        return self._upload_files

    def get_logger(self, **kwargs):
        """
        Returns a wrapped logger that additionally saves all entries to the entry
        processing log in the archive.
        """
        logger = super().get_logger()
        logger = logger.bind(
            upload_id=self.upload_id,
            mainfile=self.mainfile,
            entry_id=self.entry_id,
            parser=self.parser_name,
            workflow_ids=self.upload.workflow_ids or [],
            **kwargs,
        )

        def save_to_entry_log(logger, method_name, event_dict):
            try:
                # sanitize the event_dict, because all kinds of values might have been added
                dump_dict = {key: str(value) for key, value in event_dict.items()}
                dump_dict.update(level=method_name.upper())
                self._proc_logs.append(dump_dict)

                if method_name == 'error':
                    error = event_dict.get('event', None)
                    if error is not None:
                        self._entry_metadata.processing_errors.append(error)

            except Exception:
                # Exceptions here will cause indefinite loop
                pass

            return event_dict

        return wrap_logger(logger, processors=_log_processors + [save_to_entry_log])

    def process_entry(self):
        """Processes or reprocesses an entry."""
        self.process_status = ProcessStatus.PENDING
        return run_async(self._start_process_entry_workflow())

    async def _start_process_entry_workflow(self):
        """
        Internal method to start a temporal process entry workflow.
        This method should be called from an async context.
        """
        client = await get_client()
        workflow_id = f'process-entry-{self.entry_id}-{uuid.uuid4()}'
        try:
            await client.execute_workflow(
                'ProcessEntryWorkflow',
                ProcessEntryActivityInput(
                    upload_id=self.upload_id,
                    entry_id=self.entry_id,
                    workflow_id=workflow_id,
                ),
                id=workflow_id,
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except Exception as e:
            raise ProcessFailure(f'Failed to start temporal workflow: {e}')

    @process_local
    def process_entry_local(self):
        """Processes or reprocesses an entry locally."""
        self._process_entry_local()

    def _process_entry_local(self):
        logger = self.get_logger()
        assert self.upload is not None, 'upload does not exist'
        assert self.mainfile_key is None, (
            'cannot process a child entry, only the parent entry'
        )

        # Get child entries, if any
        self._child_entries = list(
            Entry.objects(  # type: ignore
                upload_id=self.upload_id,
                mainfile=self.mainfile,
                mainfile_key__ne=None,
            )
        )
        if self._child_entries:
            for child_entry in self._child_entries:
                # Set status of child entries to running and do some optimizations
                child_entry._upload = self.upload
                child_entry._perform_index = False
                child_entry.process_status = ProcessStatus.RUNNING
            Entry._collection.update_many(
                {
                    'upload_id': self.upload_id,
                    'mainfile': self.mainfile,
                    'mainfile_key': {'$exists': True},
                },
                {
                    '$set': {
                        'process_status': ProcessStatus.RUNNING,
                        'last_status_message': 'Parent entry processing',
                    }
                },
            )

        # Load the reprocess settings from the upload, and apply defaults
        settings = config.reprocess.customize(self.upload.reprocess_settings)

        self.set_last_status_message('Determining action')
        # If this entry has been processed before, or imported from a bundle, nomad_version
        # should be set. If not, this is the initial processing.
        self._is_initial_processing = self.nomad_version is None
        self._perform_index = (
            self._is_initial_processing or settings.index_individual_entries
        )
        if not self.upload.published or self._is_initial_processing:
            should_parse = True
        elif not settings.reprocess_existing_entries:
            should_parse = False
        else:
            if settings.rematch_published and not settings.use_original_parser:
                with utils.timer(logger, 'parser matching executed'):
                    parser, _mainfile_keys = match_parser(
                        self.mainfile_file.os_path, strict=False
                    )
            else:
                parser = parser_dict[self.parser_name]

            if parser is None:
                # Should only be possible if the upload is published and we have
                logger.warn('no parser matches during process')
                self.warnings = ['no matching parser found during processing']
                parser = parser_dict[self.parser_name]

            assert parser is not None, 'could not determine a parser for this entry'
            should_parse = True
            parser_changed = (
                self.parser_name != parser.name
                and parser_dict[self.parser_name].name != parser.name
            )
            if parser_changed:
                if not settings.use_original_parser:
                    logger.info(
                        'different parser matches during process, use new parser',
                        parser=parser.name,
                    )
                    self.parser_name = parser.name  # Parser renamed

        if should_parse:
            self.set_last_status_message('Initializing metadata')
            for entry in self._main_and_child_entries():
                entry._initialize_metadata_for_processing()

            if len(self._entry_metadata.files) >= config.process.auxfile_cutoff:
                self.warning(
                    'This entry has many aux files in its directory. '
                    'Have you placed many mainfiles in the same directory?'
                )

            self.parsing()
            for entry in self._main_and_child_entries():
                entry.normalizing()
                entry.archiving()

        elif self.upload.published:
            self.set_last_status_message('Preserving entry data')
            try:
                upload_files = PublicUploadFiles(self.upload_id)
                with upload_files.read_archive(self.entry_id) as archive:
                    target = archive[self.entry_id]
                    if not isinstance(target, LazyItem):
                        target = to_json(target)
                    self.upload_files.write_archive(self.entry_id, target)

            except Exception as e:
                logger.error(
                    'could not copy archive for non-reprocessed entry', exc_info=e
                )
                raise

    def _main_and_child_entries(self) -> Iterable['Entry']:
        yield self
        yield from self._child_entries

    def on_success(self):
        # Mark any child entries as successfully completed (necessary because the child entries
        # are not processed the normal way)
        child_entries = list(
            Entry.objects(  # type: ignore
                upload_id=self.upload_id, mainfile=self.mainfile, mainfile_key__ne=None
            )
        )
        for child_entry in child_entries:
            child_entry.errors = []
            child_entry.process_status = ProcessStatus.SUCCESS
            child_entry.last_status_message = (
                'Process process_entry completed successfully'
            )
            child_entry.save()

    def on_fail(self):
        self._on_fail()
        # Mark any child entries as failed (necessary because the child entries
        # are not processed the normal way)
        child_entries = list(
            Entry.objects(  # type: ignore
                upload_id=self.upload_id, mainfile=self.mainfile, mainfile_key__ne=None
            )
        )
        for child_entry in child_entries:
            child_entry.errors = self.errors
            child_entry.process_status = ProcessStatus.FAILURE
            child_entry.last_status_message = (
                f'Process process_entry failed: {self.errors[-1]}'
            )
            child_entry._on_fail()
            child_entry.save()

    def _on_fail(self):
        # in case of failure, create a minimum set of metadata and mark
        # processing failure
        try:
            if self._entry_metadata is None:
                self._initialize_metadata_for_processing()
            self._entry_metadata.processed = False

            try:
                self._apply_metadata_to_mongo_entry(self._entry_metadata)
            except Exception as e:
                self.get_logger().error(
                    'could not apply entry metadata to entry', exc_info=e
                )

            try:
                self._entry_metadata.apply_archive_metadata(self._parser_results)
            except Exception as e:
                self.get_logger().error(
                    'could not apply domain metadata to entry', exc_info=e
                )

            try:
                self._parser_results.processing_logs = self._filtered_processing_logs()
            except Exception as e:
                self.get_logger().error(
                    'could not apply processing_logs to entry', exc_info=e
                )

        except Exception as e:
            self._parser_results = self._create_minimal_failed_archive()
            self.get_logger().error(
                'could not create minimal metadata after processing failure', exc_info=e
            )
            self.errors.append(str(e))

        if self._perform_index:
            try:
                indexing_errors = search.index(self._parser_results)
                assert not indexing_errors
            except Exception as e:
                self.get_logger().error(
                    'could not index archive after processing failure', exc_info=e
                )
                # As a last resort: try indexing a minimal archive instead
                try:
                    search.index(self._create_minimal_failed_archive())
                except Exception as e:
                    self.get_logger().error(
                        'could not index minimal archive after processing failure',
                        exc_info=e,
                    )

        try:
            self.write_archive(self._parser_results)
        except Exception as e:
            self.get_logger().error(
                'could not write archive after processing failure', exc_info=e
            )

    def _create_minimal_failed_archive(self) -> EntryArchive:
        return EntryArchive(
            m_context=self.upload.archive_context,
            metadata=self._parser_results.metadata if self._parser_results else None,
            processing_logs=self._filtered_processing_logs(),
        )

    def parent(self) -> 'Upload':
        return self.upload

    @traced(span_name='Entry.parsing')
    def parsing(self):
        """The process step that encapsulates all parsing related actions."""
        self.set_last_status_message('Parsing mainfile')
        context = dict(step=self.parser_name)
        logger = self.get_logger(**context)
        parser = parser_dict[self.parser_name]

        with utils.timer(logger, 'parser executed', input_size=self.mainfile_file.size):
            self._temporal_heartbeat()
            if not config.process.reuse_parser:
                if isinstance(parser, parsing.MatchingParserInterface):
                    try:
                        parser.new_parser_instance()
                    except Exception as e:
                        raise ProcessFailure(
                            'could not re-create parser instance',
                            exc_info=e,
                            error=str(e),
                            **context,
                        )
            try:
                if self._child_entries:
                    child_archives = {}
                    for child_entry in self._child_entries:
                        child_archives[child_entry.mainfile_key] = (
                            child_entry._parser_results
                        )
                    kwargs = dict(child_archives=child_archives)
                else:
                    kwargs = {}

                with trace_span(f'parser: {parser.name}'):
                    parser.parse(
                        self.mainfile_file.os_path,
                        self._parser_results,
                        logger=logger,
                        **kwargs,
                    )

            except Exception as e:
                raise ProcessFailure(
                    'parser failed with exception',
                    exc_info=e,
                    error=str(e),
                    **context,
                )
            except SystemExit:
                raise ProcessFailure(
                    'parser raised system exit',
                    error='system exit',
                    **context,
                )

    @traced(span_name='Entry.normalizing')
    def normalizing(self):
        """The process step that encapsulates all normalizing related actions."""
        self.set_last_status_message('Normalizing')
        # allow normalizer to access and add data to the entry metadata
        if self._parser_results.metadata is None:
            self._parser_results.m_add_sub_section(
                datamodel.EntryArchive.metadata, self._entry_metadata
            )

        context = {}
        for normalizer in normalizers:
            self._temporal_heartbeat()
            if (
                normalizer.domain is not None
                and normalizer.domain != parser_dict[self.parser_name].domain
            ):
                continue

            normalizer_name = normalizer.__name__
            context = dict(normalizer=normalizer_name, step=normalizer_name)
            logger = self.get_logger(**context)

            with utils.timer(
                logger, 'normalizer executed', input_size=self.mainfile_file.size
            ):
                try:
                    with trace_span(f'normalizer: {normalizer_name}'):
                        normalizer(self._parser_results).normalize(logger=logger)
                    logger.info('normalizer completed successfully', **context)
                except Exception as e:
                    raise ProcessFailure(
                        'normalizer failed with exception',
                        exc_info=e,
                        error=str(e),
                        **context,
                    )

        parser = parser_dict[self.parser_name]
        logger = self.get_logger()
        try:
            parser.after_normalization(self._parser_results, logger=logger)
        except Exception as e:
            raise ProcessFailure(
                'parser after normalization step failed with exception',
                exc_info=e,
                error=str(e),
                **context,
            )

    @traced(span_name='Entry.archiving')
    def archiving(self):
        """The process step that encapsulates all archival related actions."""
        self._temporal_heartbeat()
        self.set_last_status_message('Archiving')
        logger = self.get_logger()

        self._entry_metadata.apply_archive_metadata(self._parser_results)
        self._entry_metadata.processed = True
        self._entry_metadata.process_status = self.process_status

        if self.upload.publish_directly:
            self._entry_metadata.published |= True

        # persist the entry metadata
        with utils.timer(logger, 'entry metadata saved'):
            self._apply_metadata_to_mongo_entry(self._entry_metadata)
            self.save()

        # index in search
        if self._perform_index:
            with utils.timer(logger, 'entry metadata indexed'):
                assert self._parser_results.metadata == self._entry_metadata
                indexing_errors = search.index(self._parser_results)
                if indexing_errors:
                    raise RuntimeError(
                        'Failed to index in ES: ' + indexing_errors[self.entry_id]
                    )

        # persist the archive
        with utils.timer(
            logger, 'entry archived', input_size=self.mainfile_file.size
        ) as log_data:
            archive_size = self.write_archive(self._parser_results)
            log_data.update(archive_size=archive_size)

    def write_archive(self, archive: EntryArchive):
        # save the archive mongo entry
        if archive is not None:
            archive = archive.m_copy()
        else:
            archive = datamodel.EntryArchive(m_context=self.upload.archive_context)

        if archive.metadata is None:
            archive.m_add_sub_section(
                datamodel.EntryArchive.metadata, self._entry_metadata
            )

        archive.processing_logs = self._filtered_processing_logs()

        if archive.definitions is not None:
            PackageDefinition.create_new(archive.definitions)
        if archive.data is not None:
            pkg_definitions = getattr(archive.data.m_def.m_root(), 'definitions', None)
            if pkg_definitions is not None:
                PackageDefinition.create_new(pkg_definitions)

        # save the archive msg-pack
        try:
            return self.upload_files.write_archive(
                self.entry_id,
                archive.m_to_dict(with_def_id=True, return_as_generator=True),
            )
        except Exception:
            # most likely failed due to domain data, try to write metadata and processing logs
            archive = datamodel.EntryArchive(m_context=self.upload.archive_context)
            archive.m_add_sub_section(
                datamodel.EntryArchive.metadata, self._entry_metadata
            )
            archive.processing_logs = self._filtered_processing_logs()
            self.upload_files.write_archive(self.entry_id, archive.m_to_dict())
            raise

    def _filtered_processing_logs(self):
        """Returns the filtered processing logs, to add to the archive."""
        if len(self._proc_logs) > 100:
            return [log for log in self._proc_logs if log.get('level') != 'DEBUG']
        return self._proc_logs

    def __str__(self):
        return f'entry {super().__str__()} entry_id={self.entry_id} upload_id{self.upload_id}'


class Upload(Proc):
    """
    Represents uploads in the databases. Provides persistence access to the files storage,
    and processing state.

    Attributes:
        upload_id: The upload id generated by the database or the uploaded NOMAD deployment.
        upload_name: Optional user provided upload name.
        upload_create_time: Datetime of creation of the upload.
        external_db: the repository or external database where the original data resides
        main_author: The id of the main author of this upload (normally its creator).
        coauthors: A list of upload coauthors.
        coauthor_groups: A list of coauthor groups, cf. `coauthors`.
        reviewers: A user provided list of reviewers. Reviewers can see the whole upload,
            also if it is unpublished or embargoed.
        reviewer_groups: A list of reviewer groups, cf. `reviewers`.
        publish_time: Datetime when the upload was initially published on this NOMAD deployment.
        last_update: Datetime of the last modifying process run (publish, processing, upload).
        doi: The optional Document Object Identifier (DOI) for this upload. Only the DOI
            name (prefix + "/" + suffix) is stored.

        publish_directly: Boolean indicating that this upload should be published after initial processing.
        from_oasis: Boolean indicating that this upload is coming from another NOMAD deployment.
        oasis_id: The deployment id of the NOMAD that uploaded the upload.
        published_to: A list of deployment ids where this upload has been successfully uploaded to.
    """

    id_field = 'upload_id'

    upload_id = StringField(primary_key=True)
    upload_name: str | None = StringField(default=None)
    upload_create_time = UTCDateTimeField(required=True)
    description: str | None = StringField(default=None)
    external_db = StringField()
    main_author = StringField(required=True)
    coauthors = ListField(StringField())
    coauthor_groups = ListField(StringField())
    reviewers = ListField(StringField())
    reviewer_groups = ListField(StringField())
    last_update = UTCDateTimeField()
    publish_time = UTCDateTimeField()
    embargo_length = IntField(default=0, required=True)
    license = StringField(default='CC BY 4.0', required=True)
    doi = EmbeddedDocumentField(EmbeddedDOI, default=None)

    from_oasis = BooleanField(default=False)
    oasis_deployment_url = StringField(default=None)
    published_to = ListField(StringField())

    # Process parameters and state vars that need to be persisted during the process
    publish_directly = BooleanField(default=False)
    reprocess_settings = DictField(default=None)
    parser_level = IntField(default=None)
    workflow_ids = ListField(StringField())

    meta: Any = {
        'strict': False,
        'auto_create_index': False,
        'indexes': [
            'main_author',
            'coauthors',
            'coauthor_groups',
            'reviewers',
            'reviewer_groups',
            'process_status',
            'upload_create_time',
            'publish_time',
        ],
    }

    @property
    def viewers(self) -> set[str]:
        rv = {self.main_author}
        rv.update(self.coauthors, self.reviewers)
        return rv

    @property
    def viewer_groups(self) -> set[str]:
        rv = set(self.coauthor_groups)
        rv.update(self.reviewer_groups)
        return rv

    @property
    def writers(self) -> set[str]:
        rv = {self.main_author}
        rv.update(self.coauthors)
        return rv

    @property
    def writer_groups(self) -> set[str]:
        rv = set(self.coauthor_groups)
        return rv

    def __init__(self, **kwargs):
        kwargs.setdefault('upload_create_time', datetime.now(timezone.utc))
        super().__init__(**kwargs)
        self._upload_files: UploadFiles = None
        self.archive_context = ServerContext(self)

    def reset_entry_processing_status(
        self,
        path_filter: str | None = None,
        updated_files: set[str] | None = None,
    ):
        """
        Reset the process status of all entries in the upload that will be reprocessed.
        """
        query = {'upload_id': self.upload_id}
        if path_filter:
            query['mainfile__startswith'] = path_filter
        elif updated_files is not None:
            query['mainfile__in'] = list(updated_files)

        Entry.objects(**query).update(set__process_status=ProcessStatus.READY)

    async def await_workflows(self):
        self.reload()
        while self.process_running:
            await asyncio.sleep(0.1)
            self.reload()

        if self.workflow_ids:
            client = await get_client()
            for workflow_id in self.workflow_ids:
                try:
                    handle = client.get_workflow_handle(workflow_id)
                    await handle.result()
                except WorkflowFailureError:
                    self.reload()
                    if self.process_running:
                        raise
                except RPCError:
                    # might have already finished and been removed
                    pass

    @classmethod
    def get(cls, id: str) -> 'Upload':
        return cls.get_by_id(id, 'upload_id')

    @classmethod
    def user_uploads(cls, user: datamodel.User, **kwargs) -> Sequence['Upload']:
        """Returns all uploads for the given user. Kwargs are passed to mongo query."""
        return cls.objects(main_author=str(user.user_id), **kwargs)  # type: ignore

    @property
    def main_author_user(self) -> datamodel.User:
        return datamodel.User.get(user_id=self.main_author)

    @property
    def published(self) -> bool:
        return self.publish_time is not None

    @property
    def with_embargo(self) -> bool:
        return self.embargo_length > 0

    def get_logger(self, **kwargs):
        logger = super().get_logger()
        main_author_user = self.main_author_user
        main_author_name = f'{main_author_user.first_name} {main_author_user.last_name}'
        # We are not using 'main_author' because logstash (?) will filter these entries ?!
        logger = logger.bind(
            upload_id=self.upload_id,
            upload_name=self.upload_name,
            main_author_name=main_author_name,
            main_author=self.main_author,
            workflow_ids=self.workflow_ids or [],
            **kwargs,
        )
        return logger

    @classmethod
    def create(cls, main_author: datamodel.User | None = None, **kwargs) -> 'Upload':
        """
        Creates a new upload for the given main_author, a user given upload_name is optional.
        It will populate the record with a signed url and pending :class:`UploadProc`.
        The upload will be already saved to the database.

        Arguments:
            main_author: The main author of the upload.
        """
        # use kwargs to keep compatibility with super method
        assert main_author is not None, 'No `main_author` provided.'
        if 'upload_id' not in kwargs:
            kwargs.update(upload_id=utils.create_uuid())
        kwargs.update(main_author=main_author.user_id)
        # Pylint has trouble recognizing the correct type returned by this overridden
        # class method, so instead of using super().create(**kwargs), we use this
        # alternative as discussed in https://github.com/PyCQA/pylint/issues/981
        self = Proc.__dict__['create'].__func__(cls, **kwargs)

        return self

    def delete(self):
        """Deletes this upload and its entries."""
        Entry.objects(upload_id=self.upload_id).delete()  # type: ignore
        super().delete()

    def delete_upload_local(self):
        """
        Deletes the upload, including its processing state and
        staging files. Local version without celery processing.
        """
        try:
            upload_size = self.upload_files.size
        except KeyError:
            # Can happen for failed bundle imports, where there are no files imported.
            upload_size = 0

        logger = self.get_logger(upload_size=upload_size)

        with utils.lnr(logger, 'upload delete failed'):
            with utils.timer(logger, 'upload deleted from index'):
                search.delete_upload(self.upload_id, refresh=True)

            with utils.timer(logger, 'upload files deleted'):
                for cls in (StagingUploadFiles, PublicUploadFiles):
                    if cls.exists_for(self.upload_id):
                        cls(self.upload_id).delete()

            self.delete()

    def delete_upload(self, *, wait_for_processing: bool = True):
        """
        Deletes the upload, including its processing state and
        staging files. This starts the celery process of deleting the upload.
        """
        return run_async(
            self._start_delete_upload_workflow(wait_for_processing=wait_for_processing)
        )

    async def _start_delete_upload_workflow(self, wait_for_processing: bool = True):
        """
        Internal method to start a temporal delete upload workflow.
        This method should be called from an async context.
        """
        client = await get_client()
        (
            previous_workflow_ids,
            previous_process_status,
        ) = await self._terminate_non_delete_workflows_for_delete(client)
        workflow_id = f'delete-upload-{self.upload_id}-{uuid.uuid4()}'
        workflow_input = DeleteUploadWorkflowInput(upload_id=self.upload_id)
        self.setup_delete_upload_workflow(
            workflow_id, previous_workflow_ids, previous_process_status
        )
        try:
            if wait_for_processing:
                await client.execute_workflow(
                    'DeleteUploadWorkflow',
                    workflow_input,
                    id=workflow_id,
                    task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                return None
            handle = await client.start_workflow(
                'DeleteUploadWorkflow',
                workflow_input,
                id=workflow_id,
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            return handle
        except Exception as e:
            self.cleanup_upload_after_workflow_fail(workflow_id, e)
            action = 'execute' if wait_for_processing else 'start'
            raise ProcessFailure(f'Failed to {action} temporal workflow: {e}')

    async def _terminate_non_delete_workflows_for_delete(
        self, client
    ) -> tuple[list[str], str]:
        """
        Terminate workflows that would otherwise block upload deletion.

        Deleting an upload is allowed to preempt upload processing/edit workflows,
        but it must not start a second delete workflow for the same upload.
        """
        previous_workflow_ids = list(self.workflow_ids)  # type: ignore
        previous_process_status = str(self.process_status)

        for workflow_id in previous_workflow_ids:
            handle = client.get_workflow_handle(workflow_id)
            try:
                description = await handle.describe()
                workflow_type = (
                    description.raw_description.workflow_execution_info.type.name
                )
            except RPCError as e:
                if e.status == RPCStatusCode.NOT_FOUND:
                    continue
                raise e

            if workflow_type == 'DeleteUploadWorkflow':
                raise ProcessAlreadyRunning('Upload is already being deleted')

            try:
                await handle.terminate()
            except RPCError as e:
                if e.status != RPCStatusCode.NOT_FOUND:
                    raise e

        return previous_workflow_ids, previous_process_status

    def publish_upload(
        self, embargo_length: int | None = None, *, wait_for_processing: bool = True
    ):
        return run_async(
            self._start_publish_upload_workflow(
                embargo_length, wait_for_processing=wait_for_processing
            )
        )

    async def _start_publish_upload_workflow(
        self, embargo_length: int | None = None, wait_for_processing: bool = True
    ):
        client = await get_client()
        workflow_id = f'publish-upload-{self.upload_id}-{uuid.uuid4()}'
        self.setup_upload_for_workflow(workflow_id, '_publish_upload')
        try:
            if wait_for_processing:
                return await client.execute_workflow(
                    'PublishUploadWorkflow',
                    PublishUploadWorkflowInput(
                        upload_id=self.upload_id,
                        embargo_length=embargo_length,
                    ),
                    id=workflow_id,
                    task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
            handle = await client.start_workflow(
                'PublishUploadWorkflow',
                PublishUploadWorkflowInput(
                    upload_id=self.upload_id,
                    embargo_length=embargo_length,
                ),
                id=workflow_id,
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            return handle
        except Exception as e:
            self.cleanup_upload_after_workflow_fail(workflow_id, e)
            raise ProcessFailure(f'Failed to start temporal workflow: {e}')

    def _publish_upload_local(self, embargo_length: int | None = None):
        assert self.processed_entries_count > 0
        logger = self.get_logger(upload_size=self.upload_files.size)
        logger.info('started to publish')

        if embargo_length is not None:
            assert 0 <= embargo_length <= 36, (
                'Invalid embargo length, must be between 0 and 36 months'
            )
            self.embargo_length = embargo_length

        with utils.lnr(logger, 'publish failed'):
            with self.entries_metadata() as entries:
                if isinstance(self.upload_files, StagingUploadFiles):
                    with utils.timer(logger, 'staged upload files packed'):
                        self.staging_upload_files.pack(
                            entries, with_embargo=self.with_embargo
                        )

                with utils.timer(logger, 'index updated'):
                    search.publish(entries)

                if isinstance(self.upload_files, StagingUploadFiles):
                    with utils.timer(logger, 'upload staging files deleted'):
                        self.upload_files.delete()
                        self.publish_time = datetime.now(timezone.utc)
                        self.last_update = datetime.now(timezone.utc)
                        self.save()
                else:
                    self.last_update = datetime.now(timezone.utc)
                    self.save()

    def unpublish_upload(self):
        """
        Unpublish a published upload.
        Perform the minimum steps to unpublish an upload:
            1. Update search index to remove published flag
            2. Change file storage from public to staging
            3. Update upload metadata to remove publish time and embargo length
            4. Update last update time

        Usage is strongly discouraged.
        Only use when absolutely necessary.
        """
        if not self.published:
            return

        logger = self.get_logger()
        logger.info('started to unpublish')

        try:
            self.upload_files.to_staging(create=True, include_archive=True)
        except Exception as e:
            logger.error('unpublish failed', exc_info=e)
            raise

        with self.entries_metadata() as entries, utils.timer(logger, 'index updated'):
            search.unpublish(entries)

        self.upload_files.delete()

        self.embargo_length = 0
        self.publish_time = None
        self.last_update = datetime.now(timezone.utc)
        self.save()

    def publish_externally(
        self,
        embargo_length: int | None = None,
        target_deployment_url: str | None = None,
        auth_token: str | None = None,
        export_settings: dict[str, Any] | None = None,
    ):
        return run_async(
            self._start_publish_externally_workflow(
                embargo_length=embargo_length,
                target_deployment_url=target_deployment_url,
                auth_token=auth_token,
                export_settings=export_settings,
            )
        )

    async def _start_publish_externally_workflow(
        self,
        embargo_length: int | None = None,
        target_deployment_url: str | None = None,
        auth_token: str | None = None,
        export_settings: dict[str, Any] | None = None,
    ):
        client = await get_client()
        workflow_id = f'publish-externally-{self.upload_id}-{uuid.uuid4()}'
        self.setup_upload_for_workflow(workflow_id, '_publish_externally')
        try:
            await client.execute_workflow(
                'PublishExternallyWorkflow',
                PublishExternallyWorkflowInput(
                    upload_id=self.upload_id,
                    embargo_length=embargo_length,
                    target_deployment_url=target_deployment_url,
                    auth_token=auth_token,
                    export_settings=export_settings,
                ),
                id=workflow_id,
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except Exception as e:
            raise ProcessFailure(
                f'Failed to start temporal workflow: {extract_temporal_error_details(e)}'
            )

    def _publish_externally_local(
        self,
        embargo_length: int | None = None,
        target_deployment_url: str | None = None,
        auth_token: str | None = None,
        export_settings: dict[str, Any] | None = None,
    ):
        assert self.published, (
            'Only published uploads can be published to the central NOMAD.'
        )

        if target_deployment_url is None:
            target_deployment_url = config.oasis.central_nomad_deployment_url

        tmp_dir = mkdtemp('export_' + self.upload_id)
        bundle_path = os.path.join(tmp_dir, self.upload_id + '.zip')
        try:
            self.set_last_status_message('Creating bundle.')
            from nomad.bundles import BundleExporter

            BundleExporter(
                upload=self,
                export_as_stream=False,
                export_path=bundle_path,
                zipped=True,
                overwrite=False,
                export_settings=config.bundle_export.default_settings.customize(
                    export_settings or {}
                ),
            ).export_bundle()

            # upload to central NOMAD
            self.set_last_status_message('Uploading bundle externally')
            upload_parameters: dict[str, Any] = {'format': 'bundle'}
            if embargo_length is not None:
                upload_parameters.update(embargo_length=embargo_length)

            upload_url = f'{target_deployment_url}/v1/uploads/import'

            with open(bundle_path, 'rb') as f:
                if auth_token is not None:
                    response = requests.post(
                        upload_url,
                        params=upload_parameters,
                        data=f,
                        headers={'Authorization': f'Bearer {auth_token}'},
                    )
                else:
                    upload_auth = client.Auth(
                        user=config.keycloak.username, password=config.keycloak.password
                    )
                    response = requests.post(
                        upload_url, params=upload_parameters, data=f, auth=upload_auth
                    )

            if response.status_code != 200:
                self.get_logger().error(
                    'Could not upload to external deployment',
                    status_code=response.status_code,
                    body=response.text,
                )
                raise ProcessFailure(
                    f'Error message from external deployment: {response.text}'
                )

            self.published_to.append(target_deployment_url)
            self.save()
        finally:
            PathObject(tmp_dir).delete()

    def process_example_upload(
        self, entry_point_id: str, file_operations: list[dict[str, Any]] | None = None
    ):
        """Used to initiate the processing of an example upload entry point.
        This process is only triggered once per example upload, and any further
        reprocessing happens through the usual `process_upload` function.

        Arguments:
            entry_point_id: The unique indentifier for the example upload entry
                point id to initialize.
            file_operattions: Additional file operations that should be added on
                top of the file operations coming from the entry point.
        """
        self.process_status = ProcessStatus.PENDING
        # Start temporal workflow
        return run_async(
            self._start_process_example_upload_workflow(
                entry_point_id, file_operations
            ),
        )

    async def _start_process_example_upload_workflow(
        self,
        entry_point_id: str,
        file_operations: list[dict[str, Any]] | None = None,
    ):
        """
        Internal method to start a temporal process example upload workflow.
        This method should be called from an async context.
        """
        client = await get_client()
        workflow_id = f'process-example-upload-{self.upload_id}-{uuid.uuid4()}'
        try:
            await client.start_workflow(
                'ProcessExampleUploadWorkflow',
                ProcessExampleUploadWorkflowInput(
                    upload_id=self.upload_id,
                    example_upload_id=entry_point_id,
                    file_operations=file_operations,
                    publish_directly=self.publish_directly,
                    workflow_tmp_dir=mkdtemp(f'{self.upload_id}_{workflow_id}'),
                ),
                id=workflow_id,
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            self.process_status = ProcessStatus.PENDING
            self.save()
        except Exception as e:
            raise ProcessFailure(f'Failed to start temporal workflow: {e}')

    def setup_example_upload(
        self,
        entry_point_id: str,
    ):
        """Used to initiate the example upload files of an example upload entry point.
        Arguments:
            entry_point_id: The unique indentifier for the example upload entry
                point id to initialize.
        """
        logger = self.get_logger()
        logger.info('loading example upload entry point data')
        self.set_last_status_message('Loading example upload data.')

        try:
            entry_point = cast(
                ExampleUploadEntryPoint,
                config.get_plugin_entry_point(entry_point_id),
            )
        except Exception as e:
            raise ValueError(
                'error loading example upload entry point "{example_upload_entry_point_id}": could not load configuration'
            ) from e

        # Create the upload folder in staging
        self.set_last_status_message('Creating staging files')
        StagingUploadFiles(self.upload_id, create=True)
        upload_folder = self.staging_upload_files._raw_dir.os_path

        # Add files using example upload entry point
        try:
            entry_point.load(upload_folder)
        except Exception as e:
            raise ValueError(
                'error loading example upload entry point "{example_upload_entry_point_id}": error in load() function'
            ) from e

    def setup_upload_for_workflow(
        self, workflow_id: str, process_name: str
    ) -> dict[str, Any]:
        """
        Atomically reserve this upload for a Temporal workflow.

        The update only succeeds if the upload is still in the same
        non-processing status that this instance was loaded with and no
        workflow id has been registered.
        """
        old_record = self._get_collection().find_one_and_update(
            {
                '_id': self.id,
                '$and': [
                    {'process_status': self.process_status},
                    {'process_status': {'$nin': ProcessStatus.STATUSES_PROCESSING}},
                    {
                        '$or': [
                            {'workflow_ids': []},
                            {'workflow_ids': {'$exists': False}},
                        ]
                    },
                ],
            },
            {
                '$set': {
                    'process_status': ProcessStatus.PENDING,
                    'current_process': process_name,
                    'errors': [],
                },
                '$push': {'workflow_ids': workflow_id},
            },
        )

        if old_record is None:
            raise ProcessAlreadyRunning(
                'Upload is currently blocked by another process'
            )

        self.reload()
        return old_record

    def setup_delete_upload_workflow(
        self,
        workflow_id: str,
        previous_workflow_ids: list[str],
        previous_process_status: str,
    ) -> dict[str, Any]:
        """
        Atomically replace existing workflow state with a delete workflow.

        Delete may preempt other workflow types. To avoid racing with a newly
        scheduled workflow, only clear the old workflow ids/status if they still
        match the snapshot taken before terminating those workflows.
        """
        workflow_ids_filter: dict[str, Any]
        if previous_workflow_ids:
            workflow_ids_filter = {
                'workflow_ids': {
                    '$all': previous_workflow_ids,
                    '$size': len(previous_workflow_ids),
                }
            }
        else:
            workflow_ids_filter = {
                '$or': [{'workflow_ids': []}, {'workflow_ids': {'$exists': False}}]
            }

        old_record = self._get_collection().find_one_and_update(
            {
                '_id': self.id,
                '$and': [
                    {'process_status': previous_process_status},
                    workflow_ids_filter,
                ],
            },
            {
                '$set': {
                    'process_status': ProcessStatus.PENDING,
                    'current_process': 'delete_upload',
                    'errors': [],
                    'workflow_ids': [workflow_id],
                }
            },
        )

        if old_record is None:
            raise ProcessAlreadyRunning(
                'Upload is currently blocked by another process'
            )

        self.reload()
        return old_record

    def cleanup_upload_after_workflow_fail(self, workflow_id: str, error: Exception):
        if workflow_id in self.workflow_ids:  # type: ignore
            self.workflow_ids.remove(workflow_id)  # type: ignore
        self.last_status_message = str(error)
        self.fail(error)
        self.save()

    def process_upload(
        self,
        file_operations: list[dict[str, Any]] | None = None,
        reprocess_settings: dict[str, Any] | None = None,
        path_filter: str | None = None,
        only_updated_files: bool = False,
        trigger_processing: bool = True,
    ):
        # Start temporal workflow
        return run_async(
            self._start_process_upload_workflow(
                file_operations,
                reprocess_settings,
                path_filter,
                only_updated_files,
                trigger_processing=trigger_processing,
            ),
        )

    async def _start_process_upload_workflow(
        self,
        file_operations: list[dict[str, Any]] | None = None,
        reprocess_settings: dict[str, Any] | None = None,
        path_filter: str | None = None,
        only_updated_files: bool = False,
        trigger_processing: bool = True,
    ):
        """
        Internal method to start a temporal process upload workflow.
        This method should be called from an async context.
        """
        client = await get_client()
        workflow_id = f'process-upload-{self.upload_id}-{uuid.uuid4()}'
        data = UploadProcessingWorkflowInput(
            upload_id=self.upload_id,
            file_operations=file_operations,
            reprocess_settings=reprocess_settings,
            path_filter=path_filter,
            only_updated_files=only_updated_files,
            workflow_id=workflow_id,
            workflow_tmp_dir=mkdtemp(f'{self.upload_id}_{workflow_id}'),
            trigger_processing=trigger_processing,
        )
        self.setup_upload_for_workflow(workflow_id, '_process_upload')
        try:
            handle = await client.start_workflow(
                'UpdateUploadWorkflow',
                data,
                id=workflow_id,
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            return handle
        except Exception as e:
            self.cleanup_upload_after_workflow_fail(workflow_id, e)
            raise ProcessFailure(f'Failed to start temporal workflow: {e}')

    def _process_upload_local(
        self,
        file_operations: list[dict[str, Any]] | None = None,
        reprocess_settings: dict[str, Any] | None = None,
        path_filter: str | None = None,
        only_updated_files: bool = False,
    ):
        """
        The function doing the actual processing, but locally, not as a @process.
        See :func:`process_upload`
        """
        logger = self.get_logger()
        logger.info('starting to (re)process')
        settings: Reprocess = config.reprocess.customize(
            reprocess_settings
        )  # Add default settings
        if reprocess_settings:
            self.reprocess_settings = settings.model_dump()

        # Sanity checks
        if path_filter:
            assert is_safe_relative_path(path_filter), 'Invalid `path_filter`'
        assert not (path_filter and only_updated_files), (
            'Cannot specify both `path_filter` and `only_updated_files`'
        )
        if self.published:
            assert not file_operations, 'Upload is published, cannot update files'
            assert settings.rematch_published or settings.reprocess_existing_entries, (  # pylint: disable=no-member
                'Settings do no allow reprocessing of a published upload'
            )

        # Perform matching
        updated_files = self.update_files(file_operations, only_updated_files)
        self.match_all(settings, path_filter, updated_files)

        # Get the minimum level from the parsers and start processing at that level
        self.parser_level = None
        if self.parse_next_level(parser_min_level, path_filter, updated_files):
            self.set_last_status_message(
                f'Waiting for results (level {self.parser_level})'
            )
            return ProcessStatus.WAITING_FOR_RESULT
        else:
            self.cleanup()

    @traced(span_name='upload.put_file_and_process_local')
    def put_file_and_process_local(
        self, path, target_dir, reprocess_settings: Reprocess | None = None
    ) -> Entry:
        """
        Pushes a raw file, matches it, and if matched, runs the processing - all as a local process.
        If the the target path exists, it will be overwritten. If matched, we return the
        resulting main Entry, otherwise None.
        """
        assert not self.published, 'Upload cannot be published'
        assert os.path.isfile(path), '`path` does not specify a file'
        assert is_safe_relative_path(target_dir), 'Bad target path provided'
        target_path = os.path.join(target_dir, os.path.basename(path))
        staging_upload_files = self.staging_upload_files
        if staging_upload_files.raw_exists(target_path):
            assert staging_upload_files.raw_isfile(target_path), (
                'Target path is a directory'
            )

        if reprocess_settings:
            self.reprocess_settings = reprocess_settings.model_dump()

        # Push the file
        self.set_last_status_message('Putting the file')
        staging_upload_files.add_rawfiles(path, target_dir)

        # Match
        self.set_last_status_message('Matching')
        parser, mainfile_keys = match_parser(
            staging_upload_files.raw_file_object(target_path).os_path
        )

        # Process entries, if matched; remove existing entries if unmatched.
        old_entries_dict: dict[str, Entry] = {
            e.entry_id: e
            for e in Entry.objects(upload_id=self.upload_id, mainfile=target_path)  # type: ignore
        }
        entry_ids_to_delete = set(old_entries_dict.keys())
        main_entry: Entry = None
        processing_failed = False
        if parser:
            metadata_handler = MetadataEditRequestHandler(
                self.get_logger(),
                self.main_author_user,
                staging_upload_files,
                self.upload_id,
            )

            mainfile_keys_including_main_entry: list[str] = [None] + (
                mainfile_keys or []
            )  # type: ignore
            for mainfile_key in mainfile_keys_including_main_entry:
                entry_id = utils.generate_entry_id(
                    self.upload_id, target_path, mainfile_key
                )
                entry = old_entries_dict.get(entry_id)
                if entry:
                    # Entry already exists. Reset it and the parser_name attribute
                    entry.parser_name = parser.name
                    entry.reset(force=True)
                    entry.save()
                    entry_ids_to_delete.remove(entry_id)
                else:
                    # Create new entry
                    entry = Entry.create(
                        entry_id=entry_id,
                        mainfile=target_path,
                        mainfile_key=mainfile_key,
                        parser_name=parser.name,
                        upload_id=self.upload_id,
                        process_status=ProcessStatus.PENDING,
                    )
                    # Apply entry level metadata from files, if provided
                    entry_metadata = metadata_handler.get_entry_mongo_metadata(
                        self, entry
                    )
                    for quantity_name, mongo_value in entry_metadata.items():
                        setattr(entry, quantity_name, mongo_value)
                    entry.save()

                if not mainfile_key:
                    main_entry = entry  # This is the main entry

            # process locally
            self.set_last_status_message('Processing')
            try:
                main_entry.process_entry_local()
            except Exception:
                processing_failed = True
                pass  # The framework will have set entry to failed status, which is enough.

        # Delete existing unmatched entries
        if entry_ids_to_delete:
            for entry_id in entry_ids_to_delete:
                search.delete_entry(entry_id=entry_id)
                old_entries_dict[entry_id].delete()

        if processing_failed:
            self.set_last_status_message('Process failed')
        else:
            self.set_last_status_message('Process completed successfully')

        return main_entry

    async def _stop_processing_workflows(self):
        client = await get_client()
        for workflow_id in self.workflow_ids:  # type: ignore
            try:
                await client.get_workflow_handle(workflow_id).terminate()
            except Exception as e:
                if isinstance(e, RPCError):
                    if e.status == 5:
                        # Upload is already terminated
                        pass
                    else:
                        raise e
                else:
                    pass

    def stop_processing(self):
        run_async(self._stop_processing_workflows())

        self.workflow_ids = []
        self.process_status = ProcessStatus.READY
        self.last_status_message = 'Processing stopped'
        self.save()

    @property
    def upload_files(self) -> UploadFiles:
        upload_files_class = (
            StagingUploadFiles if not self.published else PublicUploadFiles
        )

        if not self._upload_files or not isinstance(
            self._upload_files, upload_files_class
        ):
            self._upload_files = upload_files_class(self.upload_id)

        return self._upload_files

    @property
    def staging_upload_files(self) -> StagingUploadFiles:
        return self.upload_files.to_staging()

    @classmethod
    def _passes_process_filter(
        cls,
        mainfile: str,
        path_filter: str | None = None,
        updated_files: set[str] | None = None,
    ) -> bool:
        if path_filter:
            # Filter by path_filter
            if mainfile == path_filter or mainfile.startswith(
                path_filter + os.path.sep
            ):
                return True
            return False
        elif updated_files is not None:
            # Filter by updated_files
            return mainfile in updated_files
        # No filetring
        return True

    def update_files(
        self,
        file_operations: list[dict[str, Any]] | None = None,
        only_updated_files: bool = False,
    ) -> set[str] | None:
        """
        Performed before the actual parsing/normalizing. It first ensures that there is a
        folder for the upload in the staging area (if the upload is published, the files
        will be temporarily extracted to the staging area for processing). It will then
        execute the file operation specified (if `file_operations` is set).
        """
        logger = self.get_logger()

        if self.published and PublicUploadFiles.exists_for(self.upload_id):
            # Clean up staging files, if they exist, and unpack the public files to the
            # staging area.
            self.set_last_status_message('Refreshing staging files')
            self._cleanup_staging_files()
            with utils.timer(logger, 'upload extracted'):
                self.upload_files.to_staging(create=True, include_archive=True)
        elif not StagingUploadFiles.exists_for(self.upload_id):
            # Create staging files
            self.set_last_status_message('Creating staging files')
            StagingUploadFiles(self.upload_id, create=True)

        staging_upload_files = self.staging_upload_files
        updated_files: set[str] | None = set() if only_updated_files else None

        # Execute the requested file_operations, if any
        if file_operations:
            for file_operation in file_operations:
                op = file_operation['op']
                if op == 'ADD':
                    self.set_last_status_message('Adding files')
                    with utils.timer(
                        logger,
                        'Adding file(s) to upload',
                        upload_size=staging_upload_files.size,
                    ):
                        staging_upload_files.add_rawfiles(
                            file_operation['path'],
                            file_operation['target_dir'],
                            cleanup_source_file_and_dir=file_operation['temporary'],
                            updated_files=updated_files,
                            auto_decompress=file_operation.get('auto_decompress', True),
                        )
                elif op == 'DELETE':
                    self.set_last_status_message('Deleting files')
                    with utils.timer(logger, 'Deleting files or folders from upload'):
                        staging_upload_files.delete_rawfiles(
                            file_operation['path'], updated_files
                        )
                elif op == 'COPY' or op == 'MOVE':
                    self.set_last_status_message(f'{op} the file')
                    with utils.timer(logger, f'{op} the file within the upload'):
                        staging_upload_files.copy_or_move(
                            file_operation['path_to_existing_file'],
                            file_operation['path_to_target_file'],
                            op,
                            updated_files,
                        )
                else:
                    raise ValueError(f'Unknown operation {op}')
        return updated_files

    def _preprocess_files(self, path):
        """
        Some files need preprocessing. Currently we need to add a stripped POTCAR version
        and always restrict/embargo the original.
        """
        if os.path.basename(path).startswith('POTCAR') and not os.path.basename(
            path
        ).endswith('.stripped'):
            # Unstripped POTCAR file found
            # create checksum
            hash = hashlib.sha224()
            is_potcar = False
            start_read = True
            lines_to_write = []
            with self.staging_upload_files.raw_file(
                path, mode='rb', compression='infer'
            ) as orig_f:
                for line in orig_f.readlines():
                    hash.update(line)
                    if b'End of Dataset' in line:
                        # start read next potcar block, if concatenated
                        start_read = True
                    if start_read:
                        lines_to_write.append(line.decode('utf-8'))
                    # check if this is indeed a POTCAR file
                    if b'END of PSCTR' in line:
                        is_potcar = True
                        start_read = False
            if not is_potcar:
                return

            checksum = hash.hexdigest()

            # created stripped POTCAR
            stripped_path = path + '.stripped'
            with open(
                self.staging_upload_files.raw_file_object(stripped_path).os_path, 'w'
            ) as stripped_f:
                stripped_f.write(
                    f'Stripped POTCAR file. Checksum of original file (sha224): {checksum}\n'
                )
                stripped_f.writelines(lines_to_write)
            if config.process.exclude_potcar:
                # remove unstripped POTCAR file
                self.warning('Removing POTCAR file from upload.')
                self.update_files([dict(op='DELETE', path=path)], True)

    def match_mainfiles(
        self, path_filter: str | None = None, updated_files: set[str] | None = None
    ) -> Iterator[tuple[str, str, Parser]]:
        """
        Generator function that matches all files in the upload to all parsers to
        determine the upload's mainfiles.

        Returns:
            Tuples of (mainfile, mainfile_key, parser)
        """
        staging_upload_files = self.staging_upload_files

        metadata = staging_upload_files.metadata_file_cached(path_dir='')
        skip_matching = metadata.get('skip_matching', False)
        entries_metadata = metadata.get('entries', {})

        if path_filter:
            # path_filter provided, just scan this path
            scan: list[tuple[str, bool]] = [(path_filter, True)]
        elif updated_files is not None:
            # Set with updated_files provided, only scan these
            scan = [(path, False) for path in updated_files]
        else:
            # Scan everything
            scan = [('', True)]

        # Collect all files to be processed
        all_path_infos: list[RawPathInfo] = []
        for path, recursive in scan:
            path_infos: Iterable[RawPathInfo] = (
                [RawPathInfo(path=path, is_file=True, size=None, access=None)]
                if staging_upload_files.raw_isfile(path)
                else staging_upload_files.raw_listdir(path, recursive, files_only=True)
            )
            all_path_infos.extend(path_infos)

        # 1. Sequential pre-pass for files that need preprocessing (e.g., POTCARs)
        # These are processed sequentially because _preprocess_files is not thread-safe.
        for path_info in all_path_infos:
            self._preprocess_files(path_info.path)

        # 2. Parallel matching for all collected files
        def match_task(path_info: RawPathInfo):
            if not staging_upload_files.raw_exists(path_info.path):
                return None
            if skip_matching and path_info.path not in entries_metadata:
                return None

            try:
                parser, mainfile_keys = parsing.parsers.match_parser(
                    staging_upload_files.raw_file_object(path_info.path).os_path
                )
                if parser is not None:
                    return path_info.path, mainfile_keys, parser
            except Exception as e:
                self.get_logger().error(
                    'exception while matching pot. mainfile',
                    mainfile=path_info.path,
                    exc_info=e,
                )
            return None

        # Determine number of workers based on CPU count but capped for I/O efficiency
        max_workers = min(32, (os.cpu_count() or 1) * 4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for result in executor.map(match_task, all_path_infos):
                if result:
                    path, mainfile_keys, parser = result
                    mainfile_keys_including_main_entry: list[str] = [None] + (
                        mainfile_keys or []
                    )  # type: ignore
                    for mainfile_key in mainfile_keys_including_main_entry:
                        yield path, mainfile_key, parser

    def match_all(
        self,
        reprocess_settings: Reprocess,
        path_filter: str | None = None,
        updated_files: set[str] | None = None,
    ):
        """
        The process step used to identify mainfile/parser combinations among the upload's files,
        and create or delete respective :class:`Entry` instances (if needed).
        """
        self.set_last_status_message('Matching')
        logger = self.get_logger()

        try:
            metadata_handler = None
            if not self.published and not self.total_entries_count:
                # In staging and no entries yet -> import upload level metadata from files if provided
                metadata_handler = MetadataEditRequestHandler(
                    logger,
                    self.main_author_user,
                    self.staging_upload_files,
                    self.upload_id,
                )
                upload_metadata = metadata_handler.get_upload_mongo_metadata(self)
                if upload_metadata:
                    for quantity_name, mongo_value in upload_metadata.items():
                        setattr(self, quantity_name, mongo_value)
                    self.save()

            if not self.published or reprocess_settings.rematch_published:
                old_entries = set()
                processing_entries = []
                # Pre-load all entries into a dictionary for O(1) lookup
                existing_entries_cache = {}
                with utils.timer(logger, 'existing entries scanned'):
                    for entry in Entry.objects(upload_id=self.upload_id).only(
                        'entry_id', 'mainfile', 'parser_name', 'process_status'
                    ):
                        existing_entries_cache[entry.entry_id] = entry
                        if entry.process_status in [
                            ProcessStatus.PENDING,
                            ProcessStatus.RUNNING,
                        ]:
                            processing_entries.append(entry.entry_id)
                        if self._passes_process_filter(
                            entry.mainfile, path_filter, updated_files
                        ):
                            old_entries.add(entry.entry_id)

                with utils.timer(logger, 'matching completed'):
                    entries = []
                    # Track entries that need parser name updates to batch update them later
                    parser_name_updates: dict[str, list[str]] = {}
                    for mainfile, mainfile_key, parser in self.match_mainfiles(
                        path_filter, updated_files
                    ):
                        (
                            entry,
                            was_created,
                            metadata_handler,
                        ) = self._get_or_create_entry(
                            mainfile,
                            mainfile_key,
                            parser,
                            raise_if_exists=False,
                            can_create=not self.published
                            or reprocess_settings.add_matched_entries_to_published,
                            metadata_handler=metadata_handler,
                            logger=logger,
                            existing_entries=existing_entries_cache,
                        )

                        if was_created:
                            entries.append(entry)
                        elif entry is not None:
                            # entry was found in cache
                            if not self.published and parser.name != entry.parser_name:
                                parser_name_updates.setdefault(parser.name, []).append(
                                    entry.entry_id
                                )
                            old_entries.remove(entry.entry_id)

                with utils.timer(logger, 'storing entry metadata to mongo'):
                    if entries:
                        Entry.objects.insert(entries)  # type: ignore

                    if parser_name_updates:
                        for parser_name, entry_ids in parser_name_updates.items():
                            Entry.objects(
                                upload_id=self.upload_id, entry_id__in=entry_ids
                            ).update(set__parser_name=parser_name)

                # Delete old entries
                if len(old_entries) > 0:
                    logger.warn('Some entries did not match', count=len(old_entries))
                    if (
                        not self.published
                        or reprocess_settings.delete_unmatched_published_entries
                    ):
                        old_entries_list = list(old_entries)
                        for entry_id in old_entries_list:
                            search.delete_entry(entry_id=entry_id)
                        Entry.objects(
                            upload_id=self.upload_id,
                            entry_id__in=old_entries_list,
                        ).delete()

                # No entries *should* be processing, but if there are, we reset them to
                # to minimize problems (should be safe to do so).
                if processing_entries:
                    logger.warn(
                        'Some entries are processing', count=len(processing_entries)
                    )
                    with utils.timer(logger, 'processing entries reset'):
                        Entry._get_collection().update_many(
                            {'_id': {'$in': processing_entries}},
                            {
                                '$set': Entry.reset_pymongo_update(
                                    process_status=ProcessStatus.FAILURE,
                                    errors=['process aborted'],
                                )
                            },
                        )

                # Finally, reset the process status of all relevant entries.
                # This logic replaces the need for match_all_activity to call
                # reset_entry_processing_status separately.
                with utils.timer(logger, 'resetting process status'):
                    self.reset_entry_processing_status(path_filter, updated_files)

        except Exception as e:
            # try to remove the staging copy in failure case
            logger.error('failed to perform matching', exc_info=e)
            if self.published:
                self._cleanup_staging_files()
            raise

    def _get_or_create_entry(
        self,
        mainfile: str,
        mainfile_key: str,
        parser: Parser,
        raise_if_exists: bool,
        can_create: bool,
        metadata_handler: MetadataEditRequestHandler,
        logger,
        existing_entries: dict[str, Entry] | None = None,
    ) -> tuple[Entry, bool, MetadataEditRequestHandler]:
        entry_id = utils.generate_entry_id(self.upload_id, mainfile, mainfile_key)
        entry = None
        was_created = False
        try:
            if existing_entries is not None:
                entry = existing_entries.get(entry_id)
                if entry is None:
                    raise KeyError()
            else:
                entry = Entry.get(entry_id)

            # Matching entry already exists.
            if raise_if_exists:
                assert False, f'An entry already exists for mainfile {mainfile}'
            # Ensure that we update the parser if in staging
            if not self.published and parser.name != entry.parser_name:
                entry.parser_name = parser.name
                # Note: individual save() is still used here if no batch mechanism provided by caller
                if existing_entries is None:
                    entry.save()
        except KeyError:
            # No existing entry found
            if can_create:
                # Create new entry
                entry = Entry(
                    entry_id=entry_id,
                    mainfile=mainfile,
                    mainfile_key=mainfile_key,
                    parser_name=parser.name,
                    worker_hostname=self.worker_hostname,
                    upload_id=self.upload_id,
                )
                # Apply entry level metadata from files, if provided
                if not metadata_handler:
                    metadata_handler = MetadataEditRequestHandler(
                        logger,
                        self.main_author_user,
                        self.staging_upload_files,
                        self.upload_id,
                    )
                entry_metadata = metadata_handler.get_entry_mongo_metadata(self, entry)
                for quantity_name, mongo_value in entry_metadata.items():
                    setattr(entry, quantity_name, mongo_value)
                was_created = True
        return entry, was_created, metadata_handler

    def parse_next_level(
        self,
        min_level: int,
        path_filter: str | None = None,
        updated_files: set[str] | None = None,
    ) -> bool:
        """
        Triggers processing on the next level of parsers (parsers with level >= min_level).
        Returns True if there is a next level of parsers that require processing.
        """
        logger = self.get_logger()
        try:
            next_entries = self.next_level_entries(
                min_level=min_level,
                path_filter=path_filter,
                updated_files=updated_files,
            )
            if next_entries:
                with utils.timer(logger, 'processes triggered'):
                    for entry in next_entries:
                        entry.worker_hostname = self.worker_hostname
                        entry.process_entry()
                return True
            return False

        except Exception as e:
            # try to remove the staging copy in failure case
            logger.error('failed to trigger processing of all entries', exc_info=e)
            if self.published:
                self._cleanup_staging_files()
            raise

    def next_level_entries(
        self,
        min_level: int,
        path_filter: str | None = None,
        updated_files: set[str] | None = None,
    ) -> list[Entry]:
        """
        Returns entries where there is a next level of parsers that require processing.
        """
        logger = self.get_logger()
        next_level: int = None
        next_entries: list[Entry] = []
        with utils.timer(logger, 'entries processing called'):
            # Determine what the next level is and which entries belongs to this level
            for entry in Entry.objects(upload_id=self.upload_id, mainfile_key=None):  # type: ignore
                parser = parser_dict.get(entry.parser_name)
                if parser:
                    level = parser.level
                    # Ignore lowest level parsers if not matching path filter
                    # TODO: why do we only ignore the lowest level parsers here?
                    if level == parser_min_level and not self._passes_process_filter(
                        entry.mainfile, path_filter, updated_files
                    ):
                        continue
                    if level >= min_level:
                        if next_level is None or level < next_level:
                            next_level = level
                            next_entries = [entry]
                        elif level == next_level:
                            next_entries.append(entry)
            if next_entries:
                self.parser_level = next_level
                # Trigger processing
                logger.info(
                    'Triggering next level',
                    next_level=next_level,
                    n_entries=len(next_entries),
                )
                self.set_last_status_message(f'Parsing level {next_level}')
        return next_entries

    def process_updated_raw_file(self, path: str, allow_modify: bool):
        """
        Used when parsers add/modify raw files during processing.
        """
        assert self.upload_files.raw_isfile(path), (
            'Provided path does not denote a file'
        )
        logger = self.get_logger()
        metadata_handler = None

        for mainfile, mainfile_key, parser in self.match_mainfiles(path, None):
            # File matched!
            entry, was_created, metadata_handler = self._get_or_create_entry(
                mainfile,
                mainfile_key,
                parser,
                raise_if_exists=not allow_modify or self.published,
                can_create=not self.published,
                metadata_handler=metadata_handler,
                logger=logger,
            )

            if was_created:
                entry.save()

            if entry:
                if (
                    self.current_process_flags is not None
                    and self.current_process_flags.is_local
                ):
                    # Run also this entry processing locally. If it fails, an
                    # exception will be raised.
                    entry.process_entry_local()
                else:
                    # NOTE: Temporal child workflows can't be spawned within an activitiy
                    # so we just process this from the current activity.
                    # NOTE: If this function is called by a parser, we only process the
                    # updated data if it is of smaller or equal level: this way we don't
                    # process entries that should be only processed at a later stage. When
                    # files are updated e.g. by a schema normalizer which is not
                    # associated with a parser, the parser level is not set, and thus we
                    # skip the check.
                    # NOTE: The parser level is persisted and not reset after processing.
                    # When the upload is not being processed (e.g. files are updated by an
                    # action or through the API), the stored level is stale and ignored.
                    if (
                        not self.process_running
                        or self.parser_level is None
                        or self.parser_level >= parser.level
                    ):
                        try:
                            entry._process_entry_local()
                            entry.process_status = ProcessStatus.SUCCESS
                        except Exception:
                            entry.process_status = ProcessStatus.FAILURE
                        entry.save()

    def child_cls(self):
        return Entry

    def join(self):
        """
        Called when all child processes (if any) on Entry are done. Process the next level
        of parsers (if any), otherwise cleanup and finalize the process.
        """
        if self.parse_next_level(self.parser_level + 1):
            self.set_last_status_message(
                f'Waiting for results (level {self.parser_level})'
            )
            return ProcessStatus.WAITING_FOR_RESULT
        self.cleanup()

    def _index_cleanup_entries(
        self, entries: list[EntryMetadata], refresh: bool = True
    ):
        logger = self.get_logger()
        with utils.timer(logger, 'upload entries indexed'):
            archives = cast(list[EntryArchive], [entry.m_parent for entry in entries])
            indexing_errors = search.index(archives, refresh=refresh)

            if indexing_errors:
                with utils.timer(logger, 'updated mongo entries failing to index'):
                    entry_mongo_writes = [
                        UpdateOne(
                            {'_id': entry_id},
                            {
                                '$set': dict(
                                    process_status=ProcessStatus.FAILURE,
                                    last_status_message='Failed to index in ES',
                                ),
                                '$push': dict(errors=f'Failed to index in ES: {error}'),
                            },
                        )
                        for entry_id, error in indexing_errors.items()
                    ]
                    Entry._get_collection().bulk_write(entry_mongo_writes)

                failed_archives = []
                with utils.timer(logger, 'created minimal archives to re-index'):
                    for archive in archives:
                        if archive.entry_id in indexing_errors:
                            try:
                                archive.metadata.processed = False
                                if not archive.metadata.processing_errors:
                                    archive.metadata.processing_errors = []
                                archive.metadata.processing_errors.append(
                                    f'Failed to index in ES: {indexing_errors[archive.entry_id]}'
                                )
                                failed_archives.append(
                                    EntryArchive(
                                        m_context=self.archive_context,
                                        metadata=archive.metadata,
                                    )
                                )
                            except Exception as e:
                                logger.warn(
                                    'could not create minimal failed archive',
                                    entry_id=archive.entry_id,
                                    exc_info=e,
                                )

                with utils.timer(logger, 're-indexed failed entries'):
                    indexing_errors = search.index(failed_archives, refresh=refresh)
                    if indexing_errors:
                        logger.warn(
                            'some failed entries could not be re-indexed',
                            entry_ids=sorted(indexing_errors.keys()),
                        )

    def cleanup_prepare(self):
        """
        Performs upload-level cleanup work before per-entry indexing.
        """
        self.set_last_status_message('Cleanup')
        logger = self.get_logger()

        self.reprocess_settings = None  # Don't need this anymore

        if self.published:
            # We have reprocessed an already published upload
            logger.info('started to repack re-processed upload')

            with utils.timer(logger, 'staged upload files re-packed'):
                self.staging_upload_files.pack(
                    self.entries_mongo_metadata(),
                    with_embargo=self.with_embargo,
                    create=False,
                    include_raw=False,
                )

            self._cleanup_staging_files()
            self.last_update = datetime.now(timezone.utc)
            self.save()

        if (
            self.publish_directly
            and not self.published
            and self.processed_entries_count > 0
        ):
            logger = self.get_logger(upload_size=self.upload_files.size)
            logger.info('started to publish upload directly')

            with utils.lnr(logger, 'publish failed'):
                with self.entries_metadata() as entries:
                    with utils.timer(logger, 'upload staging files packed'):
                        self.staging_upload_files.pack(
                            entries, with_embargo=self.with_embargo
                        )

                with utils.timer(logger, 'upload staging files deleted'):
                    self.staging_upload_files.delete()

                self.publish_time = datetime.now(timezone.utc)
                self.last_update = datetime.now(timezone.utc)
                self.save()

    def cleanup_entries_batch(
        self, entry_ids: Sequence[str] | None = None, refresh: bool = True
    ):
        with self.entries_metadata(entry_ids=entry_ids) as entries:
            self._index_cleanup_entries(entries, refresh=refresh)

    def cleanup_finalize(self):
        """
        Performs upload-level cleanup work after per-entry indexing.
        """
        logger = self.get_logger()

        # send email about process finish
        if not self.publish_directly and self.main_author_user.email:
            user = self.main_author_user
            upload_name_str = f'{self.upload_name}' if self.upload_name else ''
            author_name = f'{user.first_name} {user.last_name}'
            upload_time_string = self.upload_create_time.isoformat()  # pylint: disable=no-member
            message = utils.strip(
                f"""
                Dear {author_name},

                your data {upload_name_str}uploaded at {upload_time_string} has completed processing. You can review your data on your upload page: {config.gui_url(page='uploads')}

                If you encounter any issues with your upload, please let us know and reply to this email.

                The nomad team
            """
            )
            try:
                infrastructure.send_mail(
                    name=author_name,
                    email=user.email,
                    message=message,
                    subject='Processing completed',
                )
            except Exception as e:
                # probably due to email configuration problems
                # don't fail or present this error to clients
                logger.error('could not send after processing email', exc_info=e)

    def cleanup(self):
        """
        The process step that "cleans" the processing, i.e. removed obsolete files and performs
        pending archival operations. Depends on the type of processing.
        """
        self.cleanup_prepare()
        self.cleanup_entries_batch(refresh=True)
        self.cleanup_finalize()

    def _cleanup_staging_files(self):
        if self.published and PublicUploadFiles.exists_for(self.upload_id):
            if StagingUploadFiles.exists_for(self.upload_id):
                staging_upload_files = StagingUploadFiles(self.upload_id)
                with utils.timer(self.get_logger(), 'upload staging files deleted'):
                    staging_upload_files.delete()

    def get_entry(self, entry_id) -> Entry:
        """Returns the upload entry with the given id or ``None``."""
        return Entry.objects(upload_id=self.upload_id, entry_id=entry_id).first()  # type: ignore

    @property
    def processed_entries_count(self) -> int:
        """The number of entries that have finished processing (process_status == SUCCESS | FAILURE)."""
        return Entry.objects(  # type: ignore
            upload_id=self.upload_id,
            process_status__in=[ProcessStatus.SUCCESS, ProcessStatus.FAILURE],
        ).count()

    @property
    def total_entries_count(self) -> int:
        """The total number of entries for this upload (regardless of process status)."""
        return Entry.objects(upload_id=self.upload_id).count()  # type: ignore

    @property
    def failed_entries_count(self) -> int:
        """The number of entries with failed processing."""
        return Entry.objects(  # type: ignore
            upload_id=self.upload_id, process_status=ProcessStatus.FAILURE
        ).count()

    def entries_sublist(self, start, end, order_by=None) -> Sequence[Entry]:
        """
        Returns all entries, paginated and ordered.

        Arguments:
            start: the start index of the requested page
            end: the end index of the requested page
            order_by: the property to order by
        """
        query = Entry.objects(upload_id=self.upload_id)[start:end]  # type: ignore
        if not order_by:
            return query
        if isinstance(order_by, str):
            return query.order_by(order_by)
        assert type(order_by) is tuple, 'order_by must be a string or a tuple if set'
        return query.order_by(*order_by)

    @property
    def successful_entries(self) -> Sequence[Entry]:
        """All successfully processed entries."""
        return Entry.objects(  # type: ignore
            upload_id=self.upload_id, process_status=ProcessStatus.SUCCESS
        )

    @contextmanager
    def entries_metadata(
        self, entry_ids: Sequence[str] | None = None
    ) -> Iterator[list[EntryMetadata]]:
        """
        This is the :py:mod:`nomad.datamodel` transformation method to transform
        processing upload's entries into list of :class:`EntryMetadata` objects.
        """
        try:
            # read all entry objects first to avoid missing cursor errors
            entry_query = Entry.objects(upload_id=self.upload_id)  # type: ignore
            if entry_ids is not None:
                entry_query = entry_query.filter(entry_id__in=list(entry_ids))
            yield [entry.full_entry_metadata(self) for entry in list(entry_query)]

        finally:
            self.upload_files.close()  # Because full_entry_metadata reads the archive files.

    def entries_mongo_metadata(self) -> list[EntryMetadata]:
        """
        Returns a list of :class:`EntryMetadata` containing the mongo metadata
        only, for all entries of this upload.
        """
        return [  # type: ignore
            entry.mongo_metadata(self)
            for entry in Entry.objects(upload_id=self.upload_id)  # type: ignore
        ]

    def edit_upload_metadata(
        self,
        edit_request_json: dict[str, Any],
        user_id: str,
        *,
        wait_for_processing: bool = True,
    ):
        """
        A @process that executes a metadata edit request, restricted to a specific upload,
        on behalf of the provided user. The `edit_request_json` should be a json dict of the
        format specified by the pydantic model :class:`MetadataEditRequest` (we need to use
        primitive data types, i.e. the json format, to be able to pass the request to a
        rabbitmq task).
        """
        return run_async(
            self._start_edit_upload_metadata_workflow(
                edit_request_json, user_id, wait_for_processing=wait_for_processing
            )
        )

    def start_edit_upload_metadata(
        self, edit_request_json: dict[str, Any], user_id: str
    ):
        """
        Starts a metadata edit workflow and returns immediately.

        Use this for API operations that should not block while waiting for the
        metadata edit workflow to finish.
        """
        return self.edit_upload_metadata(
            edit_request_json, user_id, wait_for_processing=False
        )

    async def _start_edit_upload_metadata_workflow(
        self,
        edit_request_json: dict[str, Any],
        user_id: str,
        *,
        wait_for_processing: bool,
    ):
        client = await get_client()
        workflow_id = f'edit-upload-metadata-{self.upload_id}-{uuid.uuid4()}'
        workflow_input = EditUploadMetadataWorkflowInput(
            upload_id=self.upload_id,
            edit_request_json=edit_request_json,
            user_id=user_id,
        )
        self.setup_upload_for_workflow(workflow_id, '_edit_metadata')
        try:
            if wait_for_processing:
                await client.execute_workflow(
                    'EditUploadMetadataWorkflow',
                    workflow_input,
                    id=workflow_id,
                    task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                return None

            handle = await client.start_workflow(
                'EditUploadMetadataWorkflow',
                workflow_input,
                id=workflow_id,
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            return handle
        except Exception as e:
            action = 'execute' if wait_for_processing else 'start'
            raise ProcessFailure(f'Failed to {action} temporal workflow: {e}')

    def transfer_ownership(self, new_owner_user_id: str, previous_owner_user_id: str):
        """Run the ownership transfer workflow synchronously."""
        return run_async(
            self._start_transfer_ownership_workflow(
                new_owner_user_id, previous_owner_user_id
            )
        )

    async def _start_transfer_ownership_workflow(
        self,
        new_owner_user_id: str,
        previous_owner_user_id: str,
    ):
        client = await get_client()
        workflow_id = f'transfer-upload-ownership-{self.upload_id}-{uuid.uuid4()}'
        workflow_input = TransferUploadOwnershipWorkflowInput(
            upload_id=self.upload_id,
            new_owner_user_id=new_owner_user_id,
            previous_owner_user_id=previous_owner_user_id,
        )
        self.setup_upload_for_workflow(workflow_id, '_transfer_upload_ownership')
        try:
            await client.execute_workflow(
                'TransferUploadOwnershipWorkflow',
                workflow_input,
                id=workflow_id,
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except Exception as e:
            self.cleanup_upload_after_workflow_fail(workflow_id, e)
            raise ProcessFailure(f'Failed to execute temporal workflow: {e}')

    def _edit_upload_metadata_local(
        self, edit_request_json: dict[str, Any], user_id: str
    ):
        logger = self.get_logger()
        user = datamodel.User.get(user_id=user_id)
        assert not edit_request_json.get('verify_only'), 'Request has verify_only'

        # Validate the request (the @process could have been invoked directly, without previous validation)
        handler = MetadataEditRequestHandler(
            logger, user, edit_request_json, self.upload_id
        )
        handler.validate_json_request()  # Should raise errors if something looks wrong

        # Upload level metadata
        old_with_embargo = self.with_embargo
        upload_updates = handler.get_upload_mongo_metadata(self)
        if upload_updates:
            for quantity_name, mongo_value in upload_updates.items():
                setattr(self, quantity_name, mongo_value)
            self.save()

        if self.published and old_with_embargo != self.with_embargo:
            # Need to repack
            PublicUploadFiles(self.upload_id).re_pack(with_embargo=self.with_embargo)

        # Entry level metadata
        last_edit_time = datetime.now(timezone.utc)
        entry_mongo_writes = []
        updated_metadata: list[datamodel.EntryMetadata] = []
        for entry in handler.find_request_entries(self):
            entry_updates = handler.get_entry_mongo_metadata(self, entry)
            entry_updates['last_edit_time'] = last_edit_time
            # Add mongo entry update operation to bulk write list
            entry_mongo_writes.append(
                UpdateOne({'_id': entry.entry_id}, {'$set': entry_updates})
            )
            # Create updates for ES
            entry_metadata = entry.mongo_metadata(self)
            if upload_updates:
                entry_metadata.m_update_from_dict(upload_updates, force_none=True)
            entry_metadata.m_update_from_dict(entry_updates, force_none=True)
            updated_metadata.append(entry_metadata)

        # Update mongo
        if entry_mongo_writes:
            with utils.timer(
                logger, 'Mongo bulk write completed', nupdates=len(entry_mongo_writes)
            ):
                mongo_result = Entry._get_collection().bulk_write(entry_mongo_writes)
            mongo_errors = mongo_result.bulk_api_result.get('writeErrors')
            assert not mongo_errors, (
                f'Failed to update mongo! {len(mongo_errors)} failures, first is {mongo_errors[0]}'
            )
        # Update ES
        if updated_metadata:
            with utils.timer(logger, 'ES updated', nupdates=len(updated_metadata)):
                failed_es = es_update_metadata(updated_metadata, refresh=True)
                assert not failed_es, (
                    f'Failed to update ES, there were {failed_es} fails'
                )

    def entry_ids(self) -> list[str]:
        return [entry.entry_id for entry in Entry.objects(upload_id=self.upload_id)]  # type: ignore

    def import_bundle(
        self,
        bundle_path: str,
        import_settings: dict[str, Any],
        embargo_length: int | None = None,
    ):
        """
        A method that imports data from an upload bundle to the current upload (which should
        have been created using the :func:`BundleImporter.create_upload_skeleton` method).
        See the :class:`BundleImporter` class for more info. Does not check permissions.
        """
        return run_async(
            self._start_import_bundle_workflow(
                bundle_path, import_settings, embargo_length
            ),
        )

    async def _start_import_bundle_workflow(
        self,
        bundle_path: str,
        import_settings: dict[str, Any],
        embargo_length: int | None = None,
    ):
        client = await get_client()
        workflow_id = f'import-bundle-{self.upload_id}-{uuid.uuid4()}'
        self.setup_upload_for_workflow(workflow_id, '_import_bundle')
        try:
            await client.execute_workflow(
                'ImportBundleWorkflow',
                ImportBundleWorkflowInput(
                    upload_id=self.upload_id,
                    bundle_path=bundle_path,
                    import_settings=import_settings,
                    embargo_length=embargo_length,
                ),
                id=workflow_id,
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except Exception as e:
            self.cleanup_upload_after_workflow_fail(workflow_id, e)
            raise ProcessFailure(f'Failed to execute temporal workflow: {e}')

    def _import_bundle_local(
        self,
        bundle_path: str,
        import_settings: dict[str, Any],
        embargo_length: int | None = None,
    ):
        from nomad.bundles import BundleImporter

        bundle_importer: BundleImporter = None
        try:
            import_settings_obj = config.bundle_import.default_settings.customize(
                import_settings
            )
            bundle_importer = BundleImporter(None, import_settings_obj, embargo_length)
            bundle_importer.open(bundle_path)
            return bundle_importer.import_bundle(self, False)
        finally:
            if bundle_importer:
                bundle_importer.close()

    def __str__(self):
        return f'upload {super().__str__()} upload_id{self.upload_id}'
