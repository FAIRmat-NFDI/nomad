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
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class CreateEntryInput:
    upload_id: str
    entry_id: str
    mainfile: str
    parser_name: str
    mainfile_key: str | None = None


@dataclass
class MatchedFile:
    parser_name: str
    parser_level: int
    mainfile: str
    mainfile_key: str | None = None


@dataclass
class PerformFileOpsInput:
    upload_id: str
    file_operations: list[dict[str, Any]]
    is_public_upload: bool


@dataclass
class PerformFileOpsOutput:
    updated_files: set[str]


@dataclass
class ProcessEntryActivityInput:
    upload_id: str
    entry_id: str
    workflow_id: str
    error_details: str | None = None


@dataclass
class ArchiveEntryData:
    processing_logs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class UploadWorkflowIdInput:
    upload_id: str
    workflow_id: str
    process_name: str
    failure_message: str | None = None
    error_details: str | None = None
    trigger_processing: bool = True


@dataclass
class NextLevelEntryResult:
    next_parser_level: int | None
    entries_to_be_processed: list[ProcessEntryActivityInput]


@dataclass
class UpdatedFilesResult:
    files: list[str] | None = None
    file_path: str | None = None

    def get_files(self) -> set[str] | None:
        """Get the files, loading from file if necessary"""
        if self.files is not None:
            return set(self.files)

        if self.file_path is not None:
            with open(self.file_path) as f:
                return set(json.load(f))

        return None


UploadProcessingPhase = Literal['setup', 'match', 'process']


@dataclass
class UploadProcessingWorkflowInput:
    upload_id: str
    workflow_id: str
    workflow_tmp_dir: str
    file_operations: list[dict[str, Any]] | None = None
    trigger_processing: bool = True
    reprocess_settings: dict[str, Any] | None = None
    path_filter: str | None = None
    only_updated_files: bool = False
    publish_directly_after_processing: bool = False
    updated_files: UpdatedFilesResult = field(default_factory=UpdatedFilesResult)
    min_level: int = 0
    phase: UploadProcessingPhase = 'setup'
    current_batch_dir: str | None = None
    current_batch_index: int = 0
    total_batches: int = 0
    entry_activity_batch_size: int = 1
    next_parser_level: int | None = None


@dataclass
class ProcessEntryBatchFromFileInput:
    upload_id: str
    batch_dir_path: str
    chunk_id: int
    offset: int
    limit: int


@dataclass
class EntriesToBeProcessedResult:
    upload_id: str
    directory: str | None = None
    total_batches: int = 0
    entry_activity_batch_size: int = 1
    next_parser_level: int | None = None


@dataclass
class CleanupEntryBatchFromFileInput:
    upload_id: str
    batch_dir_path: str
    batch_id: int


@dataclass
class CleanupEntriesBatchActivityInput:
    """Input for one cleanup indexing batch."""

    upload_id: str
    entry_ids: list[str]
    refresh: bool = True


@dataclass
class CleanupEntriesResult:
    """Prepared cleanup work. Empty means the fast path already finished cleanup."""

    upload_id: str
    entry_ids: list[str] | None = None
    directory: str | None = None
    current_sub_batch_index: int = 0
    total_batches: int = 0


@dataclass
class PublishUploadWorkflowInput:
    upload_id: str
    embargo_length: int | None = None


@dataclass
class DeleteUploadWorkflowInput:
    upload_id: str


@dataclass
class EditUploadMetadataWorkflowInput:
    upload_id: str
    user_id: str
    edit_request_json: dict[str, Any]
    upload_id_for_direct_edit: str | None = None


@dataclass
class TransferUploadOwnershipWorkflowInput:
    upload_id: str
    new_owner_user_id: str
    previous_owner_user_id: str


@dataclass
class ImportBundleWorkflowInput:
    upload_id: str
    bundle_path: str
    import_settings: dict[str, Any]
    embargo_length: int | None = None


@dataclass
class ProcessExampleUploadWorkflowInput:
    upload_id: str
    example_upload_id: str
    workflow_tmp_dir: str
    file_operations: list[dict[str, Any]] | None = None
    publish_directly: bool = False


@dataclass
class PublishExternallyWorkflowInput:
    upload_id: str
    embargo_length: int | None = None
    target_deployment_url: str | None = None
    auth_token: str | None = None
    export_settings: dict[str, Any] | None = None


@dataclass
class FinalizeUploadProcessingSuccessInput:
    result: Literal['success']
    upload_id: str
    workflow_id: str
    workflow_tmp_dir: str | None = None
    trigger_processing: bool = True


@dataclass
class FinalizeUploadProcessingFailureInput:
    result: Literal['failure']
    upload_id: str
    workflow_id: str
    workflow_tmp_dir: str | None = None
    failure_message: str | None = None
    error_details: str | None = None


FinalizeUploadProcessingInput = (
    FinalizeUploadProcessingSuccessInput | FinalizeUploadProcessingFailureInput
)
