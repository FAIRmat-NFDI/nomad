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
All workflow class definitions for NOMAD workflows.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import Priority, RetryPolicy
from temporalio.exceptions import ActivityError

from nomad.workflows.utils import extract_temporal_error_details

EDIT_UPLOAD_METADATA_PRIORITY = Priority(priority_key=1)
PUBLISH_UPLOAD_PRIORITY = Priority(priority_key=2)
PUBLISH_EXTERNALLY_PRIORITY = Priority(priority_key=2)
DELETE_UPLOAD_PRIORITY = Priority(priority_key=3)
IMPORT_BUNDLE_PRIORITY = Priority(priority_key=4)
PROCESS_EXAMPLE_UPLOAD_PRIORITY = Priority(priority_key=4)
BATCH_PROCESS_ENTRIES_PRIORITY = Priority(priority_key=5)
PROCESS_UPLOAD_PRIORITY = Priority(priority_key=4)
UPDATE_UPLOAD_PRIORITY = Priority(priority_key=4)
BATCH_PROCESS_ENTRY_PRIORITY = Priority(priority_key=5)
PROCESS_ENTRY_PRIORITY = Priority(priority_key=5)


with workflow.unsafe.imports_passed_through():
    from nomad.config import config
    from nomad.workflows.activities import (
        cleanup_entries_batch_activity,
        complete_upload_ownership_transfer_activity,
        delete_upload_files_activity,
        delete_upload_record_activity,
        delete_upload_search_activity,
        edit_upload_metadata_activity,
        finalize_cleanup_activity,
        finalize_upload_processing_activity,
        get_cleanup_entry_batch_from_file,
        handle_batch_heartbeat_failure_activity,
        handle_heartbeat_failure_activity,
        import_bundle_activity,
        match_all_activity,
        parser_min_level,
        prepare_cleanup_activity,
        prepare_next_level_entry_batches,
        process_entry_activity,
        process_entry_batch_from_file_activity,
        publish_externally_activity,
        publish_upload_activity,
        server_stats_checkin_activity,
        setup_example_upload_activity,
        update_files_activity,
    )
    from nomad.workflows.shared_objects import (
        CleanupEntriesBatchActivityInput,
        CleanupEntriesResult,
        CleanupEntryBatchFromFileInput,
        DeleteUploadWorkflowInput,
        EditUploadMetadataWorkflowInput,
        FinalizeUploadProcessingFailureInput,
        FinalizeUploadProcessingSuccessInput,
        ImportBundleWorkflowInput,
        ProcessEntryActivityInput,
        ProcessEntryBatchFromFileInput,
        ProcessExampleUploadWorkflowInput,
        PublishExternallyWorkflowInput,
        PublishUploadWorkflowInput,
        TransferUploadOwnershipWorkflowInput,
        UploadProcessingWorkflowInput,
    )
    from nomad.workflows.utils import (
        CLEANUP_ENTRY_BATCH_SIZE,
        ENTRY_ACTIVITY_BATCHES_PER_WORKFLOW_RUN,
        ENTRY_BATCH_FILE_SIZE,
        generate_batches,
    )


@workflow.defn
class DeleteUploadWorkflow:
    @workflow.run
    async def run(self, input: DeleteUploadWorkflowInput):
        retry_policy = RetryPolicy(
            maximum_attempts=2,
        )
        timeout = timedelta(
            seconds=config.temporal.processing_timeouts.delete_upload_timeout
        )
        heartbeat_timeout = timedelta(
            seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
        )
        await workflow.execute_activity(
            delete_upload_search_activity,
            input,
            schedule_to_close_timeout=timeout,
            heartbeat_timeout=heartbeat_timeout,
            retry_policy=retry_policy,
            priority=DELETE_UPLOAD_PRIORITY,
        )
        await workflow.execute_activity(
            delete_upload_files_activity,
            input,
            schedule_to_close_timeout=timeout,
            heartbeat_timeout=heartbeat_timeout,
            retry_policy=retry_policy,
            priority=DELETE_UPLOAD_PRIORITY,
        )
        await workflow.execute_activity(
            delete_upload_record_activity,
            input,
            schedule_to_close_timeout=timeout,
            heartbeat_timeout=heartbeat_timeout,
            retry_policy=retry_policy,
            priority=DELETE_UPLOAD_PRIORITY,
        )


@workflow.defn
class ProcessEntryWorkflow:
    @workflow.run
    async def run(self, input: ProcessEntryActivityInput):
        retry_policy = RetryPolicy(
            maximum_attempts=2,
        )
        try:
            result = await workflow.execute_activity(
                process_entry_activity,
                input,
                schedule_to_close_timeout=timedelta(
                    seconds=config.temporal.processing_timeouts.process_entry_timeout
                ),
                heartbeat_timeout=timedelta(
                    seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
                ),
                retry_policy=retry_policy,
                priority=PROCESS_ENTRY_PRIORITY,
            )
        except ActivityError as e:
            if 'heartbeat timeout' in str(e.cause):
                await workflow.execute_activity(
                    handle_heartbeat_failure_activity,
                    input,
                    schedule_to_close_timeout=timedelta(
                        seconds=config.temporal.processing_timeouts.process_entry_timeout
                    ),
                    priority=PROCESS_ENTRY_PRIORITY,
                )
            raise e

        return result


@workflow.defn
class BatchCleanupEntriesWorkflow:
    @workflow.run
    async def run(self, cleanup_entries_result: CleanupEntriesResult):
        retry_policy = RetryPolicy(
            maximum_attempts=2,
        )
        cleanup_activity_retry_policy = RetryPolicy(
            maximum_attempts=5,
            initial_interval=timedelta(seconds=1),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=30),
        )
        cleanup_batch_semaphore = asyncio.Semaphore(
            config.temporal.entry_concurrency_target
        )

        if cleanup_batch_directory := cleanup_entries_result.directory:

            async def process_file_batch(batch_id: int):
                async with cleanup_batch_semaphore:
                    entry_ids = await workflow.execute_activity(
                        get_cleanup_entry_batch_from_file,
                        CleanupEntryBatchFromFileInput(
                            upload_id=cleanup_entries_result.upload_id,
                            batch_dir_path=cleanup_batch_directory,
                            batch_id=batch_id,
                        ),
                        schedule_to_close_timeout=timedelta(
                            seconds=config.temporal.processing_timeouts.cleanup_timeout
                        ),
                        retry_policy=retry_policy,
                        priority=PROCESS_UPLOAD_PRIORITY,
                    )
                    await workflow.execute_activity(
                        cleanup_entries_batch_activity,
                        CleanupEntriesBatchActivityInput(
                            upload_id=cleanup_entries_result.upload_id,
                            entry_ids=entry_ids,
                            refresh=True,
                        ),
                        schedule_to_close_timeout=timedelta(
                            seconds=config.temporal.processing_timeouts.cleanup_timeout
                        ),
                        heartbeat_timeout=timedelta(
                            seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
                        ),
                        retry_policy=cleanup_activity_retry_policy,
                        priority=PROCESS_UPLOAD_PRIORITY,
                    )

            await asyncio.gather(
                *[
                    process_file_batch(batch_id)
                    for batch_id in range(cleanup_entries_result.total_batches)
                ]
            )
        elif entry_ids := cleanup_entries_result.entry_ids:

            async def process_entry_batch(entry_batch: list[str]):
                async with cleanup_batch_semaphore:
                    await workflow.execute_activity(
                        cleanup_entries_batch_activity,
                        CleanupEntriesBatchActivityInput(
                            upload_id=cleanup_entries_result.upload_id,
                            entry_ids=entry_batch,
                            refresh=True,
                        ),
                        schedule_to_close_timeout=timedelta(
                            seconds=config.temporal.processing_timeouts.cleanup_timeout
                        ),
                        heartbeat_timeout=timedelta(
                            seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
                        ),
                        retry_policy=cleanup_activity_retry_policy,
                        priority=PROCESS_UPLOAD_PRIORITY,
                    )

            await asyncio.gather(
                *[
                    process_entry_batch(entry_batch)
                    for entry_batch in generate_batches(
                        entry_ids,
                        max_desired_batch_size=CLEANUP_ENTRY_BATCH_SIZE,
                    )
                ]
            )


@workflow.defn
class UpdateUploadWorkflow:
    """
    Workflow to update an upload's files and optionally reprocess them.
    1. Update files
    2. (Optional) Reprocess updated files inline
    3. Mark upload as successful or failed
    By default, reprocessing is triggered unless specified otherwise.
    """

    async def process_upload(
        self,
        parse_all_input: UploadProcessingWorkflowInput,
        heartbeat_timeout: timedelta,
        parent_workflow_id: str,
    ) -> bool:
        """Process entries and cleanup, returning True when continue-as-new is needed.

        The input carries two kinds of resume state: a coarse phase for setup/match
        checkpoints and a fine-grained batch cursor for entry processing. Keeping the
        cursor in the workflow input lets a continued run resume without re-running
        already completed file updates, matching, or entry batches.
        """
        process_retry_policy = RetryPolicy(maximum_attempts=2)

        if parse_all_input.phase == 'match':
            # Step 2: Match all, pass updated_files as set. This must only run once;
            # continue-as-new resumes from the current phase/cursor below.
            await workflow.execute_activity(
                match_all_activity,
                parse_all_input,
                schedule_to_close_timeout=timedelta(
                    seconds=config.temporal.processing_timeouts.match_all_timeout
                ),
                heartbeat_timeout=heartbeat_timeout,
                retry_policy=process_retry_policy,
                priority=UPDATE_UPLOAD_PRIORITY,
            )
            parse_all_input.phase = 'process'

        # Step 3: Parse next level(s)
        # Outer loop: continue until no more parser levels to process.
        while True:
            if parse_all_input.phase != 'process':
                break

            if parse_all_input.current_batch_dir is None:
                has_entries_to_process = await self._prepare_entry_level(
                    parse_all_input,
                    heartbeat_timeout,
                    process_retry_policy,
                )
                if not has_entries_to_process:
                    break

            should_continue_as_new = await self._process_entry_batch_window(
                parse_all_input=parse_all_input,
                retry_policy=process_retry_policy,
            )
            if should_continue_as_new:
                return True

            self._advance_to_next_parser_level(parse_all_input)

            if workflow.info().is_continue_as_new_suggested():
                return True

        # Step 4: Cleanup
        cleanup_entries_result: (
            CleanupEntriesResult | None
        ) = await workflow.execute_activity(
            prepare_cleanup_activity,
            parse_all_input,
            schedule_to_close_timeout=timedelta(
                seconds=config.temporal.processing_timeouts.cleanup_timeout
            ),
            heartbeat_timeout=heartbeat_timeout,
            retry_policy=process_retry_policy,
            priority=UPDATE_UPLOAD_PRIORITY,
        )

        # An empty result means prepare_cleanup_activity already completed the fast path.
        if cleanup_entries_result is None:
            return False

        await workflow.execute_child_workflow(
            BatchCleanupEntriesWorkflow.run,
            cleanup_entries_result,
            id=f'{parent_workflow_id}-cleanup-batch-processor',
            parent_close_policy=workflow.ParentClosePolicy.TERMINATE,
            retry_policy=RetryPolicy(maximum_attempts=1),
            priority=UPDATE_UPLOAD_PRIORITY,
        )

        await workflow.execute_activity(
            finalize_cleanup_activity,
            parse_all_input,
            schedule_to_close_timeout=timedelta(
                seconds=config.temporal.processing_timeouts.cleanup_timeout
            ),
            heartbeat_timeout=heartbeat_timeout,
            retry_policy=process_retry_policy,
            priority=UPDATE_UPLOAD_PRIORITY,
        )
        return False

    async def _prepare_entry_level(
        self,
        parse_all_input: UploadProcessingWorkflowInput,
        heartbeat_timeout: timedelta,
        retry_policy: RetryPolicy,
    ) -> bool:
        """Prepare file-backed entry batches for the current parser level.

        Returns False when there are no more entries to process. Otherwise, it stores
        the batch directory and sizing information on the workflow input so the next
        processing window can resume from the same partitioning after continue-as-new.
        """
        next_level_entries_result = await workflow.execute_activity(
            prepare_next_level_entry_batches,
            parse_all_input,
            schedule_to_close_timeout=timedelta(
                seconds=config.temporal.processing_timeouts.next_level_entries_timeout
            ),
            heartbeat_timeout=heartbeat_timeout,
            retry_policy=retry_policy,
            priority=UPDATE_UPLOAD_PRIORITY,
        )

        if not next_level_entries_result:
            return False

        parse_all_input.current_batch_dir = next_level_entries_result.directory
        parse_all_input.current_batch_index = 0
        parse_all_input.total_batches = next_level_entries_result.total_batches
        parse_all_input.entry_activity_batch_size = (
            next_level_entries_result.entry_activity_batch_size
        )
        parse_all_input.next_parser_level = next_level_entries_result.next_parser_level
        return True

    def _advance_to_next_parser_level(
        self, parse_all_input: UploadProcessingWorkflowInput
    ):
        """Clear the completed level cursor and move to the next parser level."""
        next_parser_level = (
            parse_all_input.next_parser_level or parse_all_input.min_level
        )
        parse_all_input.min_level = next_parser_level + 1
        parse_all_input.current_batch_dir = None
        parse_all_input.current_batch_index = 0
        parse_all_input.total_batches = 0
        parse_all_input.next_parser_level = None

    def _process_entry_batch_from_file_input(
        self,
        parse_all_input: UploadProcessingWorkflowInput,
        batch_dir: str,
        batch_id: int,
        entry_activity_batch_size: int,
    ) -> ProcessEntryBatchFromFileInput:
        """Build the file/chunk cursor for one activity-sized entry batch."""
        start_entry_index = batch_id * entry_activity_batch_size
        return ProcessEntryBatchFromFileInput(
            upload_id=parse_all_input.upload_id,
            batch_dir_path=batch_dir,
            chunk_id=start_entry_index // ENTRY_BATCH_FILE_SIZE,
            offset=start_entry_index % ENTRY_BATCH_FILE_SIZE,
            limit=entry_activity_batch_size,
        )

    async def _process_entry_batch_window(
        self,
        parse_all_input: UploadProcessingWorkflowInput,
        retry_policy: RetryPolicy,
    ) -> bool:
        """Process a bounded window of entry batches from the prepared files.

        Activity failures are handled after Temporal has exhausted the supplied retry
        policy. Failed entries record their own processing errors, but they should not
        prevent unrelated entries, cleanup, or upload finalization from running. The
        cursor advances only after the window has completed, and the return value tells
        the caller whether another continue-as-new is needed for the remaining batches.
        """
        batch_dir = parse_all_input.current_batch_dir
        if (
            not batch_dir
            or parse_all_input.current_batch_index >= parse_all_input.total_batches
        ):
            return False

        entry_activity_batch_size = max(1, parse_all_input.entry_activity_batch_size)
        batch_activity_concurrency = max(
            1,
            (
                config.temporal.entry_concurrency_target
                * config.temporal.entry_workflow_batch_concurrency
            )
            // entry_activity_batch_size,
        )
        start_batch_index = parse_all_input.current_batch_index
        end_batch_index = min(
            start_batch_index + ENTRY_ACTIVITY_BATCHES_PER_WORKFLOW_RUN,
            parse_all_input.total_batches,
        )
        batch_activity_semaphore = asyncio.Semaphore(batch_activity_concurrency)

        async def process_file_batch(batch_id: int):
            activity_input = self._process_entry_batch_from_file_input(
                parse_all_input,
                batch_dir,
                batch_id,
                entry_activity_batch_size,
            )
            timeout_seconds = (
                config.temporal.processing_timeouts.process_entry_timeout
                * entry_activity_batch_size
            )
            try:
                async with batch_activity_semaphore:
                    await workflow.execute_activity(
                        process_entry_batch_from_file_activity,
                        activity_input,
                        schedule_to_close_timeout=timedelta(seconds=timeout_seconds),
                        heartbeat_timeout=timedelta(
                            seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
                        ),
                        retry_policy=retry_policy,
                        priority=BATCH_PROCESS_ENTRY_PRIORITY,
                    )
            except ActivityError as e:
                if 'heartbeat timeout' in str(e.cause):
                    await workflow.execute_activity(
                        handle_batch_heartbeat_failure_activity,
                        activity_input,
                        schedule_to_close_timeout=timedelta(seconds=timeout_seconds),
                        priority=BATCH_PROCESS_ENTRIES_PRIORITY,
                    )
                return e

        await asyncio.gather(
            *[
                process_file_batch(batch_id)
                for batch_id in range(start_batch_index, end_batch_index)
            ]
        )
        parse_all_input.current_batch_index = end_batch_index
        return end_batch_index < parse_all_input.total_batches

    @workflow.run
    async def run(self, input: UploadProcessingWorkflowInput):
        retry_policy = RetryPolicy(
            maximum_attempts=2,
        )
        timeout = timedelta(
            seconds=config.temporal.processing_timeouts.process_upload_timeout
        )
        heartbeat_timeout = timedelta(
            seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
        )
        workflow_info = workflow.info()
        # Default to failure so finalize always removes workflow_id and cleans temp dir.
        finalize_input: (
            FinalizeUploadProcessingSuccessInput | FinalizeUploadProcessingFailureInput
        ) = FinalizeUploadProcessingFailureInput(
            result='failure',
            upload_id=input.upload_id,
            workflow_id=workflow_info.workflow_id,
            workflow_tmp_dir=input.workflow_tmp_dir,
            failure_message='Process upload failed',
        )
        skip_finalize = False
        try:
            if input.phase == 'setup':
                # Step 1: Update files
                updated_files = await workflow.execute_activity(
                    update_files_activity,
                    input,
                    schedule_to_close_timeout=timedelta(
                        seconds=config.temporal.processing_timeouts.update_files_timeout
                    ),
                    heartbeat_timeout=heartbeat_timeout,
                    retry_policy=retry_policy,
                    priority=UPDATE_UPLOAD_PRIORITY,
                )

                input.updated_files = updated_files
                input.min_level = parser_min_level
                input.phase = 'match'

            if input.trigger_processing:
                should_continue_as_new = await self.process_upload(
                    parse_all_input=input,
                    heartbeat_timeout=heartbeat_timeout,
                    parent_workflow_id=workflow_info.workflow_id,
                )
                if should_continue_as_new:
                    skip_finalize = True
                    workflow.continue_as_new(input)

            finalize_input = FinalizeUploadProcessingSuccessInput(
                result='success',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
                workflow_tmp_dir=input.workflow_tmp_dir,
                trigger_processing=input.trigger_processing,
            )

        except workflow.ContinueAsNewError:
            raise
        except asyncio.CancelledError as e:
            finalize_input = FinalizeUploadProcessingFailureInput(
                result='failure',
                failure_message='Workflow was cancelled',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
                workflow_tmp_dir=input.workflow_tmp_dir,
                error_details=str(e),
            )
            raise
        except Exception as e:
            finalize_input = FinalizeUploadProcessingFailureInput(
                result='failure',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
                workflow_tmp_dir=input.workflow_tmp_dir,
                failure_message='Process upload failed',
                error_details=extract_temporal_error_details(e),
            )
            raise e

        finally:
            if not skip_finalize:
                await workflow.execute_activity(
                    finalize_upload_processing_activity,
                    finalize_input,
                    schedule_to_close_timeout=timedelta(
                        seconds=config.temporal.processing_timeouts.process_upload_timeout
                    ),
                    retry_policy=retry_policy,
                    priority=UPDATE_UPLOAD_PRIORITY,
                )


@workflow.defn
class ProcessExampleUploadWorkflow:
    @workflow.run
    async def run(self, input: ProcessExampleUploadWorkflowInput):
        # Step 1: Setup example upload
        timeout = timedelta(
            seconds=config.temporal.processing_timeouts.process_example_upload_timeout
        )
        heartbeat_timeout = timedelta(
            seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
        )
        await workflow.execute_activity(
            setup_example_upload_activity,
            input,
            schedule_to_close_timeout=timeout,
            heartbeat_timeout=heartbeat_timeout,
            priority=PROCESS_EXAMPLE_UPLOAD_PRIORITY,
        )
        current_workflow_id = workflow.info().workflow_id

        # Step 2: Process upload using the standard workflow
        process_upload_input = UploadProcessingWorkflowInput(
            upload_id=input.upload_id,
            file_operations=input.file_operations,
            publish_directly_after_processing=input.publish_directly,
            workflow_id=current_workflow_id,
            workflow_tmp_dir=input.workflow_tmp_dir,
        )

        await workflow.execute_child_workflow(
            UpdateUploadWorkflow.run,
            process_upload_input,
            id=f'process-upload-workflow-{current_workflow_id}-{input.upload_id}',
            parent_close_policy=workflow.ParentClosePolicy.TERMINATE,
            priority=PROCESS_EXAMPLE_UPLOAD_PRIORITY,
        )


@workflow.defn
class EditUploadMetadataWorkflow:
    @workflow.run
    async def run(self, input: EditUploadMetadataWorkflowInput):
        retry_policy = RetryPolicy(
            maximum_attempts=2,
        )
        timeout = timedelta(
            seconds=config.temporal.processing_timeouts.edit_upload_metadata_timeout
        )
        heartbeat_timeout = timedelta(
            seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
        )
        workflow_info = workflow.info()
        # Default to failure so finalize always removes workflow_id.
        finalize_input: (
            FinalizeUploadProcessingSuccessInput | FinalizeUploadProcessingFailureInput
        ) = FinalizeUploadProcessingFailureInput(
            result='failure',
            upload_id=input.upload_id,
            workflow_id=workflow_info.workflow_id,
            failure_message='Edit metadata failed',
        )

        try:
            # Edit upload metadata
            await workflow.execute_activity(
                edit_upload_metadata_activity,
                input,
                schedule_to_close_timeout=timeout,
                heartbeat_timeout=heartbeat_timeout,
                retry_policy=retry_policy,
                priority=EDIT_UPLOAD_METADATA_PRIORITY,
            )

            finalize_input = FinalizeUploadProcessingSuccessInput(
                result='success',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
            )
        except Exception as e:
            finalize_input = FinalizeUploadProcessingFailureInput(
                result='failure',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
                failure_message='Edit metadata failed',
                error_details=extract_temporal_error_details(e),
            )
            raise e

        finally:
            await workflow.execute_activity(
                finalize_upload_processing_activity,
                finalize_input,
                schedule_to_close_timeout=timeout,
                retry_policy=retry_policy,
                priority=EDIT_UPLOAD_METADATA_PRIORITY,
            )


@workflow.defn
class TransferUploadOwnershipWorkflow:
    @workflow.run
    async def run(self, input: TransferUploadOwnershipWorkflowInput):
        retry_policy = RetryPolicy(
            maximum_attempts=2,
        )
        timeout = timedelta(
            seconds=config.temporal.processing_timeouts.edit_upload_metadata_timeout
        )
        heartbeat_timeout = timedelta(
            seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
        )
        workflow_info = workflow.info()
        metadata_edit_input = EditUploadMetadataWorkflowInput(
            upload_id=input.upload_id,
            user_id=config.services.admin_user_id,
            edit_request_json={'metadata': {'main_author': input.new_owner_user_id}},
        )
        # Default to failure so finalize always removes workflow_id.
        finalize_input: (
            FinalizeUploadProcessingSuccessInput | FinalizeUploadProcessingFailureInput
        ) = FinalizeUploadProcessingFailureInput(
            result='failure',
            upload_id=input.upload_id,
            workflow_id=workflow_info.workflow_id,
            failure_message='Ownership transfer failed',
        )

        try:
            await workflow.execute_activity(
                edit_upload_metadata_activity,
                metadata_edit_input,
                schedule_to_close_timeout=timeout,
                heartbeat_timeout=heartbeat_timeout,
                retry_policy=retry_policy,
                priority=EDIT_UPLOAD_METADATA_PRIORITY,
            )
            await workflow.execute_activity(
                complete_upload_ownership_transfer_activity,
                input,
                schedule_to_close_timeout=timeout,
                retry_policy=retry_policy,
                priority=EDIT_UPLOAD_METADATA_PRIORITY,
            )
            finalize_input = FinalizeUploadProcessingSuccessInput(
                result='success',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
            )

        except Exception as e:
            finalize_input = FinalizeUploadProcessingFailureInput(
                result='failure',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
                failure_message='Ownership transfer failed',
                error_details=extract_temporal_error_details(e),
            )
            raise e
        finally:
            await workflow.execute_activity(
                finalize_upload_processing_activity,
                finalize_input,
                schedule_to_close_timeout=timeout,
                retry_policy=retry_policy,
                priority=EDIT_UPLOAD_METADATA_PRIORITY,
            )


@workflow.defn
class ImportBundleWorkflow:
    @workflow.run
    async def run(self, input: ImportBundleWorkflowInput):
        retry_policy = RetryPolicy(maximum_attempts=1)
        timeout = timedelta(
            seconds=config.temporal.processing_timeouts.import_bundle_timeout
        )
        heartbeat_timeout = timedelta(
            seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
        )
        workflow_info = workflow.info()
        # Default to failure so finalize always removes workflow_id.
        finalize_input: (
            FinalizeUploadProcessingSuccessInput | FinalizeUploadProcessingFailureInput
        ) = FinalizeUploadProcessingFailureInput(
            result='failure',
            upload_id=input.upload_id,
            workflow_id=workflow_info.workflow_id,
            failure_message='Import bundle failed',
        )

        try:
            # Import bundle
            await workflow.execute_activity(
                import_bundle_activity,
                input,
                schedule_to_close_timeout=timeout,
                heartbeat_timeout=heartbeat_timeout,
                retry_policy=retry_policy,
                priority=IMPORT_BUNDLE_PRIORITY,
            )

            finalize_input = FinalizeUploadProcessingSuccessInput(
                result='success',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
            )
        except Exception as e:
            finalize_input = FinalizeUploadProcessingFailureInput(
                result='failure',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
                failure_message='Import bundle failed',
                error_details=extract_temporal_error_details(e),
            )
            raise e

        finally:
            await workflow.execute_activity(
                finalize_upload_processing_activity,
                finalize_input,
                schedule_to_close_timeout=timeout,
                retry_policy=retry_policy,
                priority=IMPORT_BUNDLE_PRIORITY,
            )


@workflow.defn
class PublishUploadWorkflow:
    @workflow.run
    async def run(self, input: PublishUploadWorkflowInput):
        retry_policy = RetryPolicy(
            maximum_attempts=2,
        )
        timeout = timedelta(
            seconds=config.temporal.processing_timeouts.publish_upload_timeout
        )
        heartbeat_timeout = timedelta(
            seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
        )
        workflow_info = workflow.info()
        # Default to failure so finalize always removes workflow_id.
        finalize_input: (
            FinalizeUploadProcessingSuccessInput | FinalizeUploadProcessingFailureInput
        ) = FinalizeUploadProcessingFailureInput(
            result='failure',
            upload_id=input.upload_id,
            workflow_id=workflow_info.workflow_id,
            failure_message='Publish upload failed',
        )

        try:
            # Publish upload
            await workflow.execute_activity(
                publish_upload_activity,
                input,
                schedule_to_close_timeout=timeout,
                heartbeat_timeout=heartbeat_timeout,
                retry_policy=retry_policy,
                priority=PUBLISH_UPLOAD_PRIORITY,
            )

            finalize_input = FinalizeUploadProcessingSuccessInput(
                result='success',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
            )

        except Exception as e:
            finalize_input = FinalizeUploadProcessingFailureInput(
                result='failure',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
                failure_message='Publish upload failed',
                error_details=extract_temporal_error_details(e),
            )
            raise e

        finally:
            await workflow.execute_activity(
                finalize_upload_processing_activity,
                finalize_input,
                schedule_to_close_timeout=timeout,
                retry_policy=retry_policy,
                priority=PUBLISH_UPLOAD_PRIORITY,
            )


@workflow.defn
class PublishExternallyWorkflow:
    @workflow.run
    async def run(self, input: PublishExternallyWorkflowInput):
        retry_policy = RetryPolicy(
            maximum_attempts=2,
        )
        # Publishing externally creates the upload on the target deployment and
        # is therefore not safe to retry.
        publish_retry_policy = RetryPolicy(maximum_attempts=1)
        timeout = timedelta(
            seconds=config.temporal.processing_timeouts.publish_externally_timeout
        )
        heartbeat_timeout = timedelta(
            seconds=config.temporal.processing_timeouts.internal_processing_heartbeat_timeout
        )
        workflow_info = workflow.info()
        # Default to failure so finalize always removes workflow_id.
        finalize_input: (
            FinalizeUploadProcessingSuccessInput | FinalizeUploadProcessingFailureInput
        ) = FinalizeUploadProcessingFailureInput(
            result='failure',
            upload_id=input.upload_id,
            workflow_id=workflow_info.workflow_id,
            failure_message='Publish externally failed',
        )

        try:
            # Publish externally
            await workflow.execute_activity(
                publish_externally_activity,
                input,
                schedule_to_close_timeout=timeout,
                heartbeat_timeout=heartbeat_timeout,
                retry_policy=publish_retry_policy,
                priority=PUBLISH_EXTERNALLY_PRIORITY,
            )

            finalize_input = FinalizeUploadProcessingSuccessInput(
                result='success',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
            )

        except Exception as e:
            print('THIS IS THE ORIGINAL ERRROR', e)
            finalize_input = FinalizeUploadProcessingFailureInput(
                result='failure',
                upload_id=input.upload_id,
                workflow_id=workflow_info.workflow_id,
                failure_message='Publish externally failed',
                error_details=extract_temporal_error_details(e),
            )
            raise

        finally:
            await workflow.execute_activity(
                finalize_upload_processing_activity,
                finalize_input,
                schedule_to_close_timeout=timeout,
                retry_policy=retry_policy,
                priority=PUBLISH_EXTERNALLY_PRIORITY,
            )


@workflow.defn
class ServerStatsWorkflow:
    @workflow.run
    async def run(self):
        await workflow.execute_activity(
            server_stats_checkin_activity,
            start_to_close_timeout=timedelta(seconds=300),
        )
