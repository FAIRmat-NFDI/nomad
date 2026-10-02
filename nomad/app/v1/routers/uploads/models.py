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

from enum import Enum
from typing import Any

from fastapi import Body
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from nomad.config import config
from nomad.models.common import UTCDateTime
from nomad.processing import ProcessStatus
from nomad.utils import strip

from ...models import Direction, Owner, Pagination, PaginationResponse, WithQuery
from ...utils import parameter_dependency_from_model


class APITag(str, Enum):
    DEFAULT = 'uploads'
    METADATA = 'uploads/metadata'
    RAW = 'uploads/raw'
    ARCHIVE = 'uploads/archive'
    ACTION = 'uploads/action'
    BUNDLE = 'uploads/bundle'
    TRANSFER = 'uploads/transfer'


class UploadTransferFormat(str, Enum):
    bundle = 'bundle'


class UploadExportOptions(BaseModel):
    format: UploadTransferFormat = Field(
        UploadTransferFormat.bundle,
        title='Format',
        description='The export format. Currently only `bundle` is supported.',
    )
    include_raw_files: bool | None = Field(
        True,
        title='Include raw files',
        description='If raw files should be included in the bundle.',
        json_schema_extra={'export_formats': ['bundle']},
    )
    include_archive_files: bool | None = Field(
        True,
        title='Include processed archives',
        description='If archive files (i.e. parsed entries data) should be included in the bundle.',
        json_schema_extra={'export_formats': ['bundle']},
    )
    include_datasets: bool | None = Field(
        True,
        title='Include dataset references',
        description='If dataset references should be included in the bundle.',
        json_schema_extra={'export_formats': ['bundle']},
    )
    include_schemas: bool | None = Field(
        False,
        title='Include schemas',
        description='If schemas should be included in the bundle.',
        json_schema_extra={'export_formats': ['bundle']},
    )


upload_export_options_parameters = parameter_dependency_from_model(
    'upload_export_options_parameters', UploadExportOptions
)


def _get_target_deployment_url():
    return config.oasis.central_nomad_deployment_url


class UploadTransferRequest(BaseModel):
    include_raw_files: bool = Field(
        True,
        title='Include raw files',
        description='If raw files should be included in the transferred bundle.',
    )
    include_archive_files: bool = Field(
        True,
        title='Include processed archives',
        description='If archive files (i.e. parsed entries data) should be included in the transferred bundle.',
    )
    include_datasets: bool = Field(
        True,
        title='Include dataset references',
        description='If dataset references should be included in the transferred bundle.',
    )
    include_schemas: bool = Field(
        False,
        title='Include schemas',
        description='If schema snapshots should be included in the transferred bundle.',
    )
    target_deployment_url: str = Field(
        default_factory=_get_target_deployment_url,  # Factory has better behavior for testing
        description='If not provided, the transfer will target the central nomad deployment. The url must end with /api',
        examples=['https://nomad-lab.eu/prod/v1/api'],
    )
    auth_token: str = Field(
        ...,
        description='Token used to authenticate the upload transfer in the target deployment. You can use the /auth/token API endpoint in the target depoyment to retrieve the token. Provide the plain token, do not include the "Bearer"',
        examples=['eyJhbGciOiJSUzI1NiIsInR5cCI...'],
    )
    embargo_length: int | None = Field(
        None,
        description="""
                If provided, updates the embargo length of the upload. The value should
                be between 0 and 36 months. 0 means no embargo.""",
        ge=0,
        le=36,
    )

    @model_validator(mode='after')
    def validate_transfer_contents(self):
        if not self.include_raw_files and not self.include_archive_files:
            raise ValueError('Include raw files or processed archives, or both.')
        return self


class UploadImportOptions(BaseModel):
    format: UploadTransferFormat = Field(
        UploadTransferFormat.bundle,
        title='Format',
        description='The import format. Currently only `bundle` is supported.',
    )
    embargo_length: int | None = Field(
        None,
        ge=0,
        le=36,
        title='Embargo period (months)',
        description='The embargo period for the imported project. Uses the bundle value if omitted.',
    )
    include_raw_files: bool | None = Field(
        None,
        title='Include raw files',
        description='Import raw files. Only administrators can override the deployment default.',
        json_schema_extra={'import_formats': ['bundle']},
    )
    include_archive_files: bool | None = Field(
        None,
        title='Include processed archives',
        description='Import processed archives. Only administrators can override the deployment default.',
        json_schema_extra={'import_formats': ['bundle']},
    )
    include_datasets: bool | None = Field(
        None,
        title='Include dataset references',
        description='Import dataset references. Only administrators can override the deployment default.',
        json_schema_extra={'import_formats': ['bundle']},
    )
    include_bundle_info: bool | None = Field(
        None,
        title='Keep bundle information',
        description='Keep the bundle information file. Only administrators can override the deployment default.',
        json_schema_extra={'import_formats': ['bundle']},
    )
    keep_original_timestamps: bool | None = Field(
        None,
        title='Keep original timestamps',
        description='Keep original upload, entry, and publication timestamps. Only administrators can override the deployment default.',
        json_schema_extra={'import_formats': ['bundle']},
    )
    set_from_oasis: bool | None = Field(
        None,
        title='Mark as imported from an Oasis',
        description='Set the source Oasis metadata. Only administrators can override the deployment default.',
        json_schema_extra={'import_formats': ['bundle']},
    )
    trigger_processing: bool | None = Field(
        None,
        title='Trigger processing',
        description='Process the project after import. Only administrators can override the deployment default.',
        json_schema_extra={'import_formats': ['bundle']},
    )


upload_import_options_parameters = parameter_dependency_from_model(
    'upload_import_options_parameters', UploadImportOptions
)


class UploadRole(str, Enum):
    main_author = 'main_author'
    reviewer = 'reviewer'
    coauthor = 'coauthor'


class DOI(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description='The DOI name, e.g. 10.2345/nomad.6789-wxyz')


class IncludedEntries(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    query: dict[str, Any] = Field()
    timestamp: UTCDateTime | None = Field(None)


class ProcData(BaseModel):
    process_running: bool = Field(description='If a process is running')
    current_process: str | None = Field(
        None, description='Name of the current or last completed process'
    )
    process_status: str = Field(
        ProcessStatus.READY,
        description='The status of the current or last completed process',
    )
    last_status_message: str | None = Field(
        None,
        description='A short, human readable message from the current process, with '
        'information about what the current process is doing, or information '
        'about the completion (successful or not) of the last process, if no '
        'process is currently running.',
    )
    errors: list[str] = Field(
        description='A list of error messages that occurred during the last processing'
    )
    warnings: list[str] = Field(
        description='A list of warning messages that occurred during the last processing'
    )
    complete_time: UTCDateTime | None = Field(
        None, description='Date and time of the completion of the last process'
    )
    model_config = ConfigDict(from_attributes=True)


class UploadProcData(ProcData):
    upload_id: str = Field(description='The unique id for the upload.')
    upload_name: str | None = Field(
        None,
        description='The name of the upload. This can be provided during upload '
        'using the `upload_name` query parameter.',
    )
    upload_create_time: UTCDateTime | None = Field(
        None, description='Date and time of the creation of the upload.'
    )
    description: str | None = Field(
        None, description='Further information about the upload.'
    )
    main_author: str | None = Field(
        None, description=strip('The main author of the upload.')
    )
    coauthors: list[str] | None = Field(
        None, description=strip('A list of upload coauthors.')
    )
    coauthor_groups: list[str] | None = Field(
        None, description=strip('A list of upload coauthor groups.')
    )
    reviewers: list[str] | None = Field(
        None, description=strip('A list of upload reviewers.')
    )
    reviewer_groups: list[str] | None = Field(
        None, description=strip('A list of upload reviewer groups.')
    )
    writers: list[str] | None = Field(
        None, description=strip('All writer users (main author, upload coauthors).')
    )
    writer_groups: list[str] | None = Field(
        None, description=strip('All writer groups (coauthor groups).')
    )
    viewers: list[str] | None = Field(
        None,
        description=strip(
            'All viewer users (main author, upload coauthors, and reviewers)'
        ),
    )
    viewer_groups: list[str] | None = Field(
        None,
        description=strip('All viewer groups (coauthor groups, reviewer groups).'),
    )
    published: bool = Field(False, description='If this upload is already published.')
    published_to: list[str] | None = Field(
        None,
        description='A list of other NOMAD deployments that this upload was uploaded to already.',
    )
    publish_time: UTCDateTime | None = Field(
        None,
        description='Date and time of publication, if the upload has been published.',
    )
    with_embargo: bool = Field(
        description='If the upload has an embargo set (embargo_length not equal to zero).'
    )
    embargo_length: int = Field(
        description='The length of the requested embargo, in months. 0 if no embargo is requested.'
    )
    license: str = Field(
        description='The license under which this upload is distributed.'
    )
    doi: DOI | None = Field(None, description='The DOI assigned to this upload.')
    included_entries: IncludedEntries | None = Field(
        None, description='Entries included from other uploads.'
    )
    entries: int = Field(
        0, description='The number of identified entries in this upload.'
    )
    upload_files_server_path: str | None = Field(
        None, description='The path to the uploads files on the server.'
    )


class EntryProcData(ProcData):
    entry_id: str = Field()
    entry_create_time: UTCDateTime = Field()
    mainfile: str = Field()
    mainfile_key: str | None = Field(None)
    upload_id: str = Field()
    parser_name: str = Field()
    entry_metadata: dict | None = Field(None)


class UploadProcDataPagination(Pagination):
    @model_validator(mode='before')
    @classmethod
    def check_order_by(cls, data):
        order_by_choices = (
            'upload_create_time',
            'publish_time',
            'upload_name',
            'last_status_message',
            'process_status',
        )
        if isinstance(data, dict):
            order_by = data.get('order_by')
            if order_by is None:
                order_by = 'upload_create_time'  # Default value
                data['order_by'] = order_by
            if order_by not in order_by_choices:
                raise PydanticCustomError(
                    'invalid_order_by',
                    f"order_by is '{order_by}', must be one of {order_by_choices}",
                )
        return data

    @field_validator('page_after_value')
    @classmethod
    def validate_page_after_value(cls, page_after_value, values):
        # Validation handled elsewhere
        return page_after_value

    @field_validator('order_by')
    @classmethod
    def validate_order_by(cls, order_by, values):
        # Validation handled elsewhere
        return order_by

    def order_result(self, result):
        if self.order_by is None:
            return result

        prefix: str = '-' if self.order == Direction.desc else '+'
        order_list: list = [f'{prefix}{self.order_by}']
        if self.order_by == 'upload_create_time':
            order_list.append('upload_id')
        else:
            order_list.extend(['upload_create_time', 'upload_id'])

        return result.order_by(*order_list)


upload_proc_data_pagination_parameters = parameter_dependency_from_model(
    'upload_proc_data_pagination_parameters',
    UploadProcDataPagination,
)


class EntryProcDataPagination(Pagination):
    @field_validator('order_by')
    @classmethod
    def validate_order_by(cls, order_by):  # pylint: disable=no-self-argument
        order_by_choices = (
            'mainfile',
            'parser_name',
            'process_status',
            'current_process',
            'entry_create_time',
        )

        if order_by == 'mainfile_path':
            return 'mainfile'
        if order_by is None:
            return 'mainfile'  # Default value
        if order_by not in order_by_choices:
            raise PydanticCustomError(
                'invalid_order_by',
                f"order_by is '{order_by}', must be one of {order_by_choices}",
            )
        return order_by

    @field_validator('page_after_value')
    @classmethod
    def validate_page_after_value(cls, page_after_value, values):
        # Validation handled elsewhere
        return page_after_value

    def order_result(self, result):
        if self.order_by is None:
            return result

        prefix: str = '-' if self.order == Direction.desc else '+'
        order_list: list = [f'{prefix}{self.order_by}', 'entry_id']

        return result.order_by(*order_list)


entry_proc_data_pagination_parameters = parameter_dependency_from_model(
    'entry_proc_data_pagination_parameters',
    EntryProcDataPagination,
)


class UploadProcDataResponse(BaseModel):
    upload_id: str | None = Field(
        None,
        description=strip(
            """
        Unique id of the upload."""
        ),
    )
    data: UploadProcData | None = Field(
        None,
        description=strip(
            """
        The upload data as a dictionary."""
        ),
    )


class UploadProcDataQuery(BaseModel):
    upload_id: list[str] | None = Field(
        None,
        description='Search for uploads matching the given id. Multiple values can be specified.',
    )
    doi: list[str] | None = Field(
        None,
        description='Search for uploads matching the given doi. Multiple values can be specified.',
    )
    upload_name: list[str] | None = Field(
        None,
        description=strip(
            """
            Search for uploads by upload_name.

            Implicit exact form:
            - `upload_name=value`

            Explicit form:
            - `upload_name={"value":"name","type":"exact"}`
            - `upload_name={"value":"term","type":"fuzzy"}`

            Multiple values can be specified.
            """
        ),
    )
    is_processing: bool | None = Field(
        None,
        description=strip(
            """
            If True, only include currently processing uploads.
            If False, do not include currently processing uploads.
            If unset, include everything."""
        ),
    )
    is_published: bool | None = Field(
        None,
        description=strip(
            """
            If True: only include published uploads.
            If False: only include unpublished uploads.
            If unset: include everything."""
        ),
    )
    process_status: str | None = Field(
        None, description=strip('Search by the process status.')
    )
    is_owned: bool | None = Field(
        None,
        description=strip(
            """
            If True: only include owned uploads.
            If False: only include shared uploads.
            If unset: include everything."""
        ),
    )

    @field_validator('process_status')
    @classmethod
    def upper_process_status(cls, process_status: str):  # pylint: disable=no-self-argument
        return process_status.upper() if process_status else None


upload_proc_data_query_parameters = parameter_dependency_from_model(
    'upload_proc_data_query_parameters',
    UploadProcDataQuery,
)


class UploadProcDataQueryResponse(BaseModel):
    query: UploadProcDataQuery = Field()
    pagination: PaginationResponse = Field()
    data: list[UploadProcData] | None = Field(
        None,
        description=strip(
            """
        The upload data as a list. Each item is a dictionary with the data for each
        upload."""
        ),
    )


class EntryProcDataResponse(BaseModel):
    entry_id: str = Field()
    data: EntryProcData = Field()


class EntryProcDataQueryResponse(BaseModel):
    pagination: PaginationResponse = Field()
    processing_successful: int | None = Field(
        None,
        description=strip(
            """
        Number of entries that has been processed successfully.
        """
        ),
    )
    processing_failed: int | None = Field(
        None,
        description=strip(
            """
        Number of entries that failed to process.
        """
        ),
    )
    upload: UploadProcData | None = Field(
        None,
        description=strip(
            """
        The upload processing data of the upload.
        """
        ),
    )
    data: list[EntryProcData] | None = Field(
        None,
        description=strip(
            """
        The entries data as a list. Each item is a dictionary with the data for one entry.
        """
        ),
    )


class RawDirPagination(Pagination):
    @field_validator('order_by')
    @classmethod
    def validate_order_by(cls, order_by):  # pylint: disable=no-self-argument
        assert not order_by, 'Cannot specify `order_by` for rawdir calls'
        if order_by:
            raise PydanticCustomError(
                'invalid_order_by', 'Cannot specify `order_by` for rawdir calls'
            )

    @field_validator('page_after_value')
    @classmethod
    def validate_page_after_value(cls, page_after_value, values):
        # Validation handled elsewhere
        return page_after_value


rawdir_pagination_parameters = parameter_dependency_from_model(
    'rawdir_pagination_parameters',
    RawDirPagination,
    exclude=['order', 'order_by'],
)


class RawDirFileMetadata(BaseModel):
    """Metadata about a file"""

    name: str = Field()
    size: int | None = Field(None)
    entry_id: str | None = Field(
        None,
        description=strip(
            """
        If this is a mainfile: the ID of the corresponding entry."""
        ),
    )
    parser_name: str | None = Field(
        None,
        description=strip(
            """
        If this is a mainfile: the name of the matched parser."""
        ),
    )


class RawDirElementMetadata(RawDirFileMetadata):
    """Metadata about an directory *element*, i.e. a file or a directory"""

    is_file: bool = Field()


class RawDirDirectoryMetadata(BaseModel):
    """Metadata about a directory"""

    name: str = Field()
    size: int | None = Field(None)
    content: list[RawDirElementMetadata] = Field(
        examples=[
            [
                {'name': 'a_directory', 'is_file': False, 'size': 456},
                {
                    'name': 'a_file.json',
                    'is_file': True,
                    'size': 123,
                    'entry_id': 'XYZ',
                    'parser_name': 'parsers/vasp',
                },
            ]
        ]
    )


class RawDirResponse(BaseModel):
    path: str = Field(examples=['The/requested/path'])
    access: str = Field()
    file_metadata: RawDirFileMetadata | None = Field(None)
    directory_metadata: RawDirDirectoryMetadata | None = Field(None)
    pagination: PaginationResponse | None = Field(None)


class ProcessingData(BaseModel):
    upload_id: str = Field()
    path: str = Field()
    entry_id: str | None = Field(None)
    parser_name: str | None = Field(None)
    entry: EntryProcData | None = Field(None)
    archive: dict[str, Any] | None = Field(None)


class PutRawFileResponse(BaseModel):
    upload_id: str | None = Field(
        None,
        description=strip(
            """
        Unique id of the upload."""
        ),
    )
    data: UploadProcData | None = Field(
        None,
        description=strip(
            """
        The upload data as a dictionary."""
        ),
    )
    processing: ProcessingData | None = Field(
        None,
        description=strip(
            """
        Information about the processing, including the entry (if one was generated) and
        [optionally] the archive data of this entry."""
        ),
    )


class DeleteEntryFilesRequest(WithQuery):
    """Defines a request to delete entry files."""

    owner: Owner | None = Body('all')
    include_parent_folders: bool | None = Field(
        False,
        description=strip(
            """
            If the delete operation should include not only the mainfiles of the selected entries,
            but also their folders."""
        ),
    )


class UploadCommandExamplesResponse(BaseModel):
    upload_url: str = Field()
    upload_command: str = Field()
    upload_command_with_name: str = Field()
    upload_progress_command: str = Field()
    upload_command_form: str = Field()
    upload_tar_command: str = Field()
