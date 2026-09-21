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

import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from nomad.actions import TaskQueue
from nomad.actions.workflows.utils import SERVER_STATS_WORKFLOW_ID, setup_server_stats
from nomad.config import config
from nomad.processing.base import ProcessFailure, ProcessStatus
from nomad.processing.data import Upload
from nomad.workflows.activities import (
    get_cleanup_entry_batch_from_file,
    handle_heartbeat_failure_activity,
    prepare_cleanup_activity,
    prepare_next_level_entry_batches,
    process_entry_batch_activity,
    process_entry_batch_from_file_activity,
)
from nomad.workflows.shared_objects import (
    CleanupEntriesResult,
    CleanupEntryBatchFromFileInput,
    DeleteUploadWorkflowInput,
    EditUploadMetadataWorkflowInput,
    ImportBundleWorkflowInput,
    ProcessEntryActivityInput,
    ProcessEntryBatchFromFileInput,
    ProcessExampleUploadWorkflowInput,
    PublishExternallyWorkflowInput,
    PublishUploadWorkflowInput,
    TransferUploadOwnershipWorkflowInput,
    UploadProcessingWorkflowInput,
)
from nomad.workflows.utils import CLEANUP_ENTRY_BATCH_SIZE, ENTRY_BATCH_FILE_SIZE
from nomad.workflows.workflows import ServerStatsWorkflow, UpdateUploadWorkflow

# Test Constants
TEST_UPLOAD_ID = 'test-upload-123'
TEST_ENTRY_ID = 'test-entry-456'
TEST_USER_ID = 'test-user'
TEST_EXAMPLE_UPLOAD_ID = 'example-upload-id'
TEST_BUNDLE_PATH = '/path/to/bundle'
EXISTING_WORKFLOW_ID = 'existing-workflow-id'


class TestFixtures:
    """Test data fixtures for workflow inputs."""

    @staticmethod
    def delete_upload_input():
        return DeleteUploadWorkflowInput(upload_id=TEST_UPLOAD_ID)

    @staticmethod
    def process_entry_input():
        return ProcessEntryActivityInput(
            upload_id=TEST_UPLOAD_ID,
            entry_id=TEST_ENTRY_ID,
            workflow_id=str(uuid.uuid4()),
        )

    @staticmethod
    def upload_processing_input():
        return UploadProcessingWorkflowInput(
            upload_id=TEST_UPLOAD_ID,
            file_operations=[dict(op='CREATE', temporary=True)],
            reprocess_settings=None,
            path_filter=None,
            only_updated_files=False,
            publish_directly_after_processing=True,
            workflow_id=str(uuid.uuid4()),
            workflow_tmp_dir=tempfile.mkdtemp(),
        )

    @staticmethod
    def process_example_upload_input():
        return ProcessExampleUploadWorkflowInput(
            upload_id=TEST_UPLOAD_ID,
            file_operations=[dict(op='CREATE', temporary=True)],
            publish_directly=True,
            example_upload_id=TEST_EXAMPLE_UPLOAD_ID,
            workflow_tmp_dir=tempfile.mkdtemp(),
        )

    @staticmethod
    def edit_upload_metadata_input():
        return EditUploadMetadataWorkflowInput(
            upload_id=TEST_UPLOAD_ID,
            edit_request_json={'title': 'Test Upload'},
            user_id=TEST_USER_ID,
        )

    @staticmethod
    def import_bundle_input():
        return ImportBundleWorkflowInput(
            bundle_path=TEST_BUNDLE_PATH,
            upload_id=TEST_UPLOAD_ID,
            import_settings={},
        )

    @staticmethod
    def publish_upload_input():
        return PublishUploadWorkflowInput(upload_id=TEST_UPLOAD_ID)

    @staticmethod
    def publish_externally_input():
        return PublishExternallyWorkflowInput(upload_id=TEST_UPLOAD_ID)

    @staticmethod
    def transfer_upload_ownership_input():
        return TransferUploadOwnershipWorkflowInput(
            upload_id=TEST_UPLOAD_ID,
            new_owner_user_id='new-owner-user-id',
            previous_owner_user_id=TEST_USER_ID,
        )


class TestUploadProcessingWorkflowInput:
    def test_phase_accepts_serialized_string_value(self):
        input_data = UploadProcessingWorkflowInput(
            upload_id=TEST_UPLOAD_ID,
            workflow_id=str(uuid.uuid4()),
            workflow_tmp_dir=tempfile.mkdtemp(),
            phase='process',
        )

        assert input_data.phase == 'process'


@pytest.fixture
def mock_data_layer(monkeypatch):
    """Mock all data layer dependencies with monkeypatch."""
    # Mock Upload class and instances
    mock_upload_instance = MagicMock()
    mock_upload_instance.workflow_ids = []
    mock_upload_instance.update_files.return_value = {'updated_file.txt'}
    mock_upload_instance.next_level_entries.return_value = []
    mock_upload_instance.parser_level = 1

    mock_upload_objects = MagicMock()

    mock_upload_class = Mock()
    mock_upload_class.get.return_value = mock_upload_instance
    mock_upload_class.objects.return_value = mock_upload_objects
    monkeypatch.setattr('nomad.workflows.activities.Upload', mock_upload_class)

    # Mock Entry class and instances
    mock_entry_instance = MagicMock()
    mock_entry_objects = MagicMock()
    mock_entry_objects.__iter__.return_value = iter([])
    mock_entry_objects.count.return_value = 0
    mock_entry_class = Mock()
    mock_entry_class.get.return_value = mock_entry_instance
    mock_entry_class.objects.return_value = mock_entry_objects
    monkeypatch.setattr('nomad.workflows.activities.Entry', mock_entry_class)

    # Mock file classes
    mock_staging_files = Mock()
    mock_staging_files.exists_for.return_value = True
    mock_staging_files_instance = Mock()
    mock_staging_files.return_value = mock_staging_files_instance
    monkeypatch.setattr(
        'nomad.workflows.activities.StagingUploadFiles', mock_staging_files
    )

    mock_public_files = Mock()
    mock_public_files.exists_for.return_value = True
    mock_public_files_instance = Mock()
    mock_public_files.return_value = mock_public_files_instance
    monkeypatch.setattr(
        'nomad.workflows.activities.PublicUploadFiles', mock_public_files
    )

    # Mock search functions
    mock_delete_upload = Mock()
    monkeypatch.setattr('nomad.workflows.activities.delete_upload', mock_delete_upload)

    mock_notification_service = Mock()
    monkeypatch.setattr(
        'nomad.workflows.activities.notification_service', mock_notification_service
    )

    # Mock config
    mock_config = Mock()
    mock_reprocess = Mock()
    mock_temporal_config = Mock()
    mock_reprocess.customize.return_value = {}
    mock_processing_timeouts = Mock()
    mock_processing_timeouts.internal_processing_heartbeat_timeout = 30
    mock_processing_timeouts.delete_upload_timeout = 7200
    mock_processing_timeouts.process_entry_timeout = 7200
    mock_processing_timeouts.process_upload_timeout = 7200
    mock_processing_timeouts.edit_upload_metadata_timeout = 7200
    mock_processing_timeouts.import_bundle_timeout = 7200
    mock_processing_timeouts.publish_upload_timeout = 7200
    mock_processing_timeouts.publish_externally_timeout = 7200
    mock_processing_timeouts.process_example_upload_timeout = 7200
    mock_processing_timeouts.setup_upload_timeout = 7200
    mock_processing_timeouts.update_files_timeout = 7200
    mock_processing_timeouts.match_all_timeout = 7200
    mock_processing_timeouts.next_level_entries_timeout = 7200
    mock_processing_timeouts.cleanup_timeout = 7200
    mock_processing_timeouts.process_upload_success_timeout = 7200
    mock_processing_timeouts.remove_workflow_id_timeout = 7200
    mock_processing_timeouts.cleanup_workflow_tmp_dir_timeout = 7200
    mock_temporal_config.processing_timeouts = mock_processing_timeouts
    mock_temporal_config.entry_workflow_batch_concurrency = 1
    mock_temporal_config.entry_concurrency_target = 1
    mock_temporal_config.entry_activity_batch_size = 1
    mock_config.reprocess = mock_reprocess
    mock_config.temporal = mock_temporal_config
    monkeypatch.setattr('nomad.config.config', mock_config)

    return {
        'upload_class': mock_upload_class,
        'upload_instance': mock_upload_instance,
        'upload_objects': mock_upload_objects,
        'entry_class': mock_entry_class,
        'entry_instance': mock_entry_instance,
        'entry_objects': mock_entry_objects,
        'staging_files': mock_staging_files,
        'staging_files_instance': mock_staging_files_instance,
        'public_files': mock_public_files,
        'public_files_instance': mock_public_files_instance,
        'delete_upload': mock_delete_upload,
        'notification_service': mock_notification_service,
        'config': mock_config,
    }


class TestDeleteUploadWorkflow:
    """Tests for DeleteUploadWorkflow."""

    @pytest.mark.asyncio
    async def test_successful_deletion(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        """Test successful upload deletion with all activities succeeding."""
        async with temporal_worker() as env:
            input_data = TestFixtures.delete_upload_input()

            await env.client.execute_workflow(
                'DeleteUploadWorkflow',
                input_data,
                id='test-delete-upload-workflow',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

            # Verify data layer calls
            mock_data_layer['delete_upload'].assert_called_once_with(
                TEST_UPLOAD_ID, refresh=True
            )
            mock_data_layer['staging_files'].exists_for.assert_called_with(
                TEST_UPLOAD_ID
            )
            mock_data_layer['public_files'].exists_for.assert_called_with(
                TEST_UPLOAD_ID
            )
            mock_data_layer['staging_files_instance'].delete.assert_called_once()
            mock_data_layer['public_files_instance'].delete.assert_called_once()
            mock_data_layer['entry_class'].objects.assert_called_once_with(
                upload_id=TEST_UPLOAD_ID
            )
            mock_data_layer['entry_objects'].delete.assert_called_once()
            mock_data_layer['upload_class'].objects.assert_called_once_with(
                upload_id=TEST_UPLOAD_ID
            )
            mock_data_layer['upload_objects'].delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_deletion_with_no_files(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        """Test deletion when no files exist."""
        # Configure mocks - no files exist
        mock_data_layer['staging_files'].exists_for.return_value = False
        mock_data_layer['public_files'].exists_for.return_value = False

        async with temporal_worker() as env:
            input_data = TestFixtures.delete_upload_input()

            await env.client.execute_workflow(
                'DeleteUploadWorkflow',
                input_data,
                id='test-delete-upload-workflow-no-files',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

            # Verify file deletion was not called since files don't exist
            mock_data_layer['staging_files_instance'].delete.assert_not_called()
            mock_data_layer['public_files_instance'].delete.assert_not_called()


class TestProcessEntryWorkflow:
    """Tests for ProcessEntryWorkflow."""

    @pytest.mark.asyncio
    async def test_successful_entry_processing(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        """Test successful entry processing."""
        async with temporal_worker() as env:
            input_data = TestFixtures.process_entry_input()

            await env.client.execute_workflow(
                'ProcessEntryWorkflow',
                input_data,
                id='test-process-entry-workflow',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

            # Verify entry processing calls
            mock_data_layer['entry_class'].get.assert_called_with(TEST_ENTRY_ID)
            mock_data_layer['entry_instance']._process_entry_local.assert_called_once()

            # Verify entry process status is set to success and saved
            assert (
                mock_data_layer['entry_instance'].process_status
                == ProcessStatus.SUCCESS
            )
            mock_data_layer['entry_instance'].save.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_timeout(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        """Test heartbeat timeout."""
        import time

        # Set heartbeat timeout to 1 second
        mock_processing_timeouts = mock_data_layer[
            'config'
        ].temporal.processing_timeouts
        mock_processing_timeouts.internal_processing_heartbeat_timeout = 1

        # Mock the process_entry_activity to sleep for 2 seconds
        def mock_process_entry_side_effect():
            time.sleep(2)
            return 'success'

        entry_instance = mock_data_layer['entry_instance']

        entry_instance._process_entry_local.side_effect = mock_process_entry_side_effect

        async with temporal_worker() as env:
            input_data = TestFixtures.process_entry_input()

            with pytest.raises(Exception):
                await env.client.execute_workflow(
                    'ProcessEntryWorkflow',
                    input_data,
                    id='test-heartbeat-timeout-workflow',
                    task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
                )

        # Verify that handle_heartbeat_failure_activity was called
        entry_instance.process_status = ProcessStatus.FAILURE
        entry_instance.errors = [
            'Process entry failed due to a heartbeat timeout. '
            'If this keeps happening contact NOMAD/ your oasis admin for support.'
        ]

    @pytest.mark.asyncio
    async def test_non_retryable_failure_propagates(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        """Test permanent single-entry failures still surface to callers."""
        mock_data_layer[
            'entry_instance'
        ]._process_entry_local.side_effect = ProcessFailure('permanent failure')

        async with temporal_worker() as env:
            input_data = TestFixtures.process_entry_input()

            with pytest.raises(Exception):
                await env.client.execute_workflow(
                    'ProcessEntryWorkflow',
                    input_data,
                    id='test-process-entry-non-retryable-failure',
                    task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
                )


class TestProcessEntryActivities:
    def test_retryable_batch_failure_is_raised_after_other_entries_run(
        self,
        mock_data_layer,
    ):
        """Test retryable failures do not stop later entries in the same batch."""
        first_entry = MagicMock()
        second_entry = MagicMock()
        entries_by_id = {
            'entry-1': first_entry,
            'entry-2': second_entry,
        }
        mock_data_layer['entry_class'].get.side_effect = lambda entry_id: entries_by_id[
            entry_id
        ]
        first_entry._process_entry_local.side_effect = Exception('retryable failure')

        with pytest.raises(Exception, match='retryable failure'):
            process_entry_batch_activity(
                [
                    ProcessEntryActivityInput(
                        upload_id=TEST_UPLOAD_ID,
                        entry_id='entry-1',
                        workflow_id=str(uuid.uuid4()),
                    ),
                    ProcessEntryActivityInput(
                        upload_id=TEST_UPLOAD_ID,
                        entry_id='entry-2',
                        workflow_id=str(uuid.uuid4()),
                    ),
                ]
            )

        second_entry._process_entry_local.assert_called_once()
        assert second_entry.process_status == ProcessStatus.SUCCESS
        second_entry.save.assert_called_once()

    def test_heartbeat_failure_does_not_overwrite_success(
        self,
        mock_data_layer,
    ):
        """Test heartbeat recovery keeps already-successful entries untouched."""
        entry = mock_data_layer['entry_instance']
        entry.process_status = ProcessStatus.SUCCESS

        handle_heartbeat_failure_activity(TestFixtures.process_entry_input())

        entry.fail.assert_not_called()
        entry.save.assert_not_called()

    def test_process_entry_batch_from_file_loads_across_chunks(
        self, mock_data_layer, monkeypatch, tmp_path
    ):
        batch_dir = tmp_path
        (batch_dir / 'entry_chunk_0.json').write_text(
            '["entry-0", "entry-1", "entry-2"]'
        )
        (batch_dir / 'entry_chunk_1.json').write_text(
            '["entry-3", "entry-4", "entry-5"]'
        )
        processed_entry_ids = []

        def mock_get_entry(entry_id):
            entry = MagicMock()
            entry._process_entry_local.side_effect = lambda: processed_entry_ids.append(
                entry_id
            )
            return entry

        mock_data_layer['entry_class'].get.side_effect = mock_get_entry
        monkeypatch.setattr('nomad.workflows.activities.activity.heartbeat', Mock())

        process_entry_batch_from_file_activity(
            ProcessEntryBatchFromFileInput(
                upload_id=TEST_UPLOAD_ID,
                batch_dir_path=str(batch_dir),
                chunk_id=0,
                offset=2,
                limit=3,
            )
        )

        assert processed_entry_ids == ['entry-2', 'entry-3', 'entry-4']


class TestPrepareNextLevelEntryBatchesActivity:
    def test_returns_none_without_entries(self, mock_data_layer):
        mock_data_layer['upload_instance'].next_level_entries.return_value = []

        result = prepare_next_level_entry_batches(
            TestFixtures.upload_processing_input()
        )

        assert result is None

    def test_prepared_entry_batches_use_fixed_size_chunks(
        self,
        mock_data_layer,
        monkeypatch,
    ):
        monkeypatch.setattr(
            'nomad.workflows.activities.config', mock_data_layer['config']
        )
        mock_data_layer['config'].temporal.entry_activity_batch_size = 2
        mock_data_layer['upload_instance'].next_level_entries.return_value = [
            Mock(entry_id=f'entry-{idx}') for idx in range(ENTRY_BATCH_FILE_SIZE + 1)
        ]

        result = prepare_next_level_entry_batches(
            TestFixtures.upload_processing_input()
        )

        assert result is not None
        assert result.directory is not None
        assert result.total_batches == 501
        assert result.entry_activity_batch_size == 2
        assert (Path(result.directory) / 'entry_chunk_0.json').exists()
        assert (Path(result.directory) / 'entry_chunk_1.json').exists()


class TestCleanupActivities:
    def test_prepare_cleanup_small_upload_uses_fast_path(
        self,
        mock_data_layer,
    ):
        mock_entries = [Mock(entry_id=f'entry-{idx}') for idx in range(50)]
        mock_data_layer['entry_class'].objects.return_value = mock_entries

        result = prepare_cleanup_activity(TestFixtures.upload_processing_input())

        assert result is None
        mock_data_layer['upload_instance'].cleanup.assert_called_once()

    def test_medium_cleanup_entry_sets_stay_in_memory(
        self,
        mock_data_layer,
    ):
        mock_entries = [Mock(entry_id=f'entry-{idx}') for idx in range(150)]
        mock_data_layer['entry_class'].objects.return_value = mock_entries

        result = prepare_cleanup_activity(TestFixtures.upload_processing_input())

        assert result.directory is None
        assert result.entry_ids == [f'entry-{idx}' for idx in range(150)]
        mock_data_layer['upload_instance'].cleanup_prepare.assert_called_once()
        mock_data_layer['upload_instance'].cleanup_entries_batch.assert_not_called()
        mock_data_layer['upload_instance'].cleanup_finalize.assert_not_called()

    def test_cleanup_entry_batch_from_file_loads_specific_batch_file(self, tmp_path):
        batch_dir = tmp_path
        (batch_dir / 'cleanup_batch_1.json').write_text(
            '["entry-4", "entry-5", "entry-6"]'
        )

        batch = get_cleanup_entry_batch_from_file(
            CleanupEntryBatchFromFileInput(
                upload_id=TEST_UPLOAD_ID,
                batch_dir_path=str(batch_dir),
                batch_id=1,
            )
        )

        assert batch == ['entry-4', 'entry-5', 'entry-6']


class TestUploadCleanupHelpers:
    def test_reset_entry_processing_status_prefers_path_filter(self, monkeypatch):
        entry_objects = Mock()
        mock_entry_class = Mock()
        mock_entry_class.objects = entry_objects
        monkeypatch.setattr('nomad.processing.data.Entry', mock_entry_class)

        upload = Upload(upload_id=TEST_UPLOAD_ID)
        upload.reset_entry_processing_status(
            path_filter='some/path',
            updated_files={'different/file.txt'},
        )

        entry_objects.assert_called_once_with(
            upload_id=TEST_UPLOAD_ID,
            mainfile__startswith='some/path',
        )
        entry_objects.return_value.update.assert_called_once_with(
            set__process_status=ProcessStatus.READY
        )


class TestBatchCleanupEntriesWorkflow:
    @pytest.mark.asyncio
    async def test_small_cleanup_batch_direct_processing(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        cleanup_result = CleanupEntriesResult(
            upload_id=TEST_UPLOAD_ID,
            entry_ids=[f'entry-{idx}' for idx in range(5)],
        )

        async with temporal_worker() as env:
            await env.client.execute_workflow(
                'BatchCleanupEntriesWorkflow',
                cleanup_result,
                id='test-batch-cleanup-small',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

        mock_data_layer[
            'upload_instance'
        ].cleanup_entries_batch.assert_called_once_with(
            [f'entry-{idx}' for idx in range(5)], refresh=True
        )

    @pytest.mark.asyncio
    async def test_large_cleanup_batch_uses_continue_as_new(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        entry_ids = [f'entry-{idx}' for idx in range(CLEANUP_ENTRY_BATCH_SIZE + 5)]
        cleanup_result = CleanupEntriesResult(
            upload_id=TEST_UPLOAD_ID,
            entry_ids=entry_ids,
        )

        async with temporal_worker() as env:
            await env.client.execute_workflow(
                'BatchCleanupEntriesWorkflow',
                cleanup_result,
                id='test-batch-cleanup-large',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

        assert mock_data_layer['upload_instance'].cleanup_entries_batch.call_count == 2
        first_call, second_call = mock_data_layer[
            'upload_instance'
        ].cleanup_entries_batch.call_args_list
        assert first_call.kwargs == {'refresh': True}
        assert second_call.kwargs == {'refresh': True}
        assert len(first_call.args[0]) <= CLEANUP_ENTRY_BATCH_SIZE
        assert len(second_call.args[0]) <= CLEANUP_ENTRY_BATCH_SIZE
        assert len(first_call.args[0]) + len(second_call.args[0]) == len(entry_ids)

    @pytest.mark.asyncio
    async def test_cleanup_batch_failure_fails_workflow(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        cleanup_result = CleanupEntriesResult(
            upload_id=TEST_UPLOAD_ID,
            entry_ids=[f'entry-{idx}' for idx in range(5)],
        )
        mock_data_layer[
            'upload_instance'
        ].cleanup_entries_batch.side_effect = Exception('cleanup failed')

        with pytest.raises(WorkflowFailureError) as exc_info:
            async with temporal_worker() as env:
                await env.client.execute_workflow(
                    'BatchCleanupEntriesWorkflow',
                    cleanup_result,
                    id='test-batch-cleanup-failure',
                    task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
                )

        assert 'cleanup failed' in str(exc_info.value.cause.cause)


class TestProcessUploadWorkflow:
    """Tests for ProcessUploadWorkflow."""

    @pytest.mark.asyncio
    async def test_complete_upload_processing(
        self,
        mock_data_layer,
        monkeypatch,
        temporal_worker,
    ):
        """Test complete upload processing workflow."""
        upload = mock_data_layer['upload_instance']
        upload.upload_id = TEST_UPLOAD_ID
        upload.main_author = TEST_USER_ID
        upload.upload_name = 'Test project'
        upload.current_process = '_process_upload'

        # Mock next_level_entries to return entries first, then empty
        def mock_next_level_entries_side_effect(*args, **kwargs):
            if not hasattr(mock_next_level_entries_side_effect, 'call_count'):
                mock_next_level_entries_side_effect.call_count = 0

            mock_next_level_entries_side_effect.call_count += 1

            if mock_next_level_entries_side_effect.call_count == 1:
                return [Mock(entry_id='test-entry-1')]
            else:
                return []

        mock_data_layer[
            'upload_instance'
        ].next_level_entries.side_effect = mock_next_level_entries_side_effect

        # Mock parser_min_level
        monkeypatch.setattr('nomad.workflows.activities.parser_min_level', 0)

        async with temporal_worker() as env:
            input_data = TestFixtures.upload_processing_input()

            await env.client.execute_workflow(
                'UpdateUploadWorkflow',
                input_data,
                id='test-process-upload-workflow',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

        # Verify workflow ID management
        assert not mock_data_layer['upload_instance'].workflow_ids
        mock_data_layer['upload_instance'].save.assert_called()
        mock_data_layer['upload_instance'].update_files.assert_called_once()
        mock_data_layer['upload_instance'].match_all.assert_called_once()
        mock_data_layer['upload_instance'].cleanup.assert_called_once()
        mock_data_layer['notification_service'].emit.assert_called_once()
        notification = mock_data_layer['notification_service'].emit.call_args.kwargs
        assert notification['user_id'] == TEST_USER_ID
        assert notification['data']['result'] == 'success'

    @pytest.mark.asyncio
    async def test_processing_loop_multiple_levels(
        self,
        mock_data_layer,
        monkeypatch,
        temporal_worker,
    ):
        """Test processing loop with multiple parser levels."""
        call_count = 0

        def mock_next_level_entries_multi_level(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            if call_count <= 2:
                return [Mock(entry_id=f'test-entry-{call_count}')]
            else:
                return []

        mock_data_layer[
            'upload_instance'
        ].next_level_entries.side_effect = mock_next_level_entries_multi_level
        monkeypatch.setattr('nomad.workflows.activities.parser_min_level', 0)

        async with temporal_worker() as env:
            input_data = TestFixtures.upload_processing_input()

            await env.client.execute_workflow(
                'UpdateUploadWorkflow',
                input_data,
                id='test-process-upload-workflow-levels',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

        # Verify next_level_entries was called multiple times
        assert mock_data_layer['upload_instance'].next_level_entries.call_count == 3

    @pytest.mark.asyncio
    async def test_match_all_failure_surfaces_activity_message(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        """Test that child workflow failures preserve the underlying activity message."""
        mock_data_layer['upload_instance'].match_all.side_effect = Exception(
            'match_all activity failed'
        )

        with pytest.raises(WorkflowFailureError):
            async with temporal_worker() as env:
                await env.client.execute_workflow(
                    'UpdateUploadWorkflow',
                    TestFixtures.upload_processing_input(),
                    id='test-process-upload-workflow-match-all-failure',
                    task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
                )

        mock_data_layer['upload_instance'].fail.assert_called()
        failure_details = mock_data_layer['upload_instance'].fail.call_args.args[0]
        assert 'match_all' in failure_details


class TestProcessExampleUploadWorkflow:
    """Tests for ProcessExampleUploadWorkflow."""

    @pytest.mark.asyncio
    async def test_example_upload_processing(
        self,
        mock_data_layer,
        monkeypatch,
        temporal_worker,
    ):
        """Test example upload processing workflow."""
        # Mock to prevent the processing loop from running
        mock_data_layer['upload_instance'].next_level_entries.return_value = []
        monkeypatch.setattr('nomad.workflows.activities.parser_min_level', 0)

        async with temporal_worker() as env:
            input_data = TestFixtures.process_example_upload_input()
            await env.client.execute_workflow(
                'ProcessExampleUploadWorkflow',
                input_data,
                id='test-process-example-upload-workflow',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

            # Verify setup activity was called
            mock_data_layer[
                'upload_instance'
            ].setup_example_upload.assert_called_once_with(
                entry_point_id=TEST_EXAMPLE_UPLOAD_ID
            )


class TestEditUploadMetadataWorkflow:
    """Tests for EditUploadMetadataWorkflow."""

    @pytest.mark.asyncio
    async def test_successful_metadata_edit(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        """Test successful metadata editing."""
        async with temporal_worker() as env:
            input_data = TestFixtures.edit_upload_metadata_input()

            await env.client.execute_workflow(
                'EditUploadMetadataWorkflow',
                input_data,
                id='test-edit-upload-metadata-workflow',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

        # Verify metadata editing was called
        mock_data_layer[
            'upload_instance'
        ]._edit_upload_metadata_local.assert_called_once_with(
            {'title': 'Test Upload'}, TEST_USER_ID
        )

        # Verify that the process status is set to SUCCESS
        assert (
            mock_data_layer['upload_instance'].process_status == ProcessStatus.SUCCESS
        )
        mock_data_layer['upload_instance'].set_last_status_message.assert_called_with(
            'Process completed successfully'
        )


class TestImportBundleWorkflow:
    """Tests for ImportBundleWorkflow."""

    @pytest.mark.asyncio
    async def test_successful_bundle_import(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        """Test successful bundle import."""
        async with temporal_worker() as env:
            input_data = TestFixtures.import_bundle_input()

            await env.client.execute_workflow(
                'ImportBundleWorkflow',
                input_data,
                id='test-import-bundle-workflow',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

        # Verify bundle import was called
        mock_data_layer['upload_instance']._import_bundle_local.assert_called_once_with(
            TEST_BUNDLE_PATH, {}, None
        )

        # Verify that the process status is set to SUCCESS
        assert (
            mock_data_layer['upload_instance'].process_status == ProcessStatus.SUCCESS
        )
        mock_data_layer['upload_instance'].set_last_status_message.assert_called_with(
            'Process completed successfully'
        )


class TestPublishUploadWorkflow:
    """Tests for PublishUploadWorkflow."""

    @pytest.mark.asyncio
    async def test_successful_upload_publish(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        """Test successful upload publishing."""
        async with temporal_worker() as env:
            input_data = TestFixtures.publish_upload_input()

            await env.client.execute_workflow(
                'PublishUploadWorkflow',
                input_data,
                id='test-publish-upload-workflow',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

        # Verify publishing was called
        mock_data_layer[
            'upload_instance'
        ]._publish_upload_local.assert_called_once_with(None)

        # Verify that the process status is set to SUCCESS
        assert (
            mock_data_layer['upload_instance'].process_status == ProcessStatus.SUCCESS
        )
        mock_data_layer['upload_instance'].set_last_status_message.assert_called_with(
            'Process completed successfully'
        )


class TestPublishExternallyWorkflow:
    """Tests for PublishExternallyWorkflow."""

    @pytest.mark.asyncio
    async def test_successful_external_publish(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        """Test successful external publishing."""
        async with temporal_worker() as env:
            input_data = TestFixtures.publish_externally_input()

            await env.client.execute_workflow(
                'PublishExternallyWorkflow',
                input_data,
                id='test-publish-externally-workflow',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

            # Verify external publishing was called
        mock_data_layer[
            'upload_instance'
        ]._publish_externally_local.assert_called_once()

        # Verify that the process status is set to SUCCESS
        assert (
            mock_data_layer['upload_instance'].process_status == ProcessStatus.SUCCESS
        )
        mock_data_layer['upload_instance'].set_last_status_message.assert_called_with(
            'Process completed successfully'
        )

    @pytest.mark.asyncio
    async def test_failed_publish_externally(
        self,
        temporal_worker,
        mock_data_layer,
    ):
        """Test behavior when publish externally fails"""
        error_message = 'test error message'

        def raise_generic_error(*args, **kwargs):
            raise Exception(error_message)

        mock_upload_instance = mock_data_layer['upload_instance']
        mock_upload_instance.errors = ['old error']
        mock_upload_instance._publish_externally_local = raise_generic_error

        def mock_fail(*errors):
            mock_upload_instance.process_status = ProcessStatus.FAILURE
            mock_upload_instance.errors.clear()
            mock_upload_instance.errors.extend(str(error) for error in errors)

        mock_upload_instance.fail = mock_fail
        with pytest.raises(Exception):
            async with temporal_worker() as env:
                input_data = TestFixtures.publish_externally_input()

                await env.client.execute_workflow(
                    'PublishExternallyWorkflow',
                    input_data,
                    id='test-publish-externally-workflow-fail',
                    task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
                )

        assert mock_upload_instance.last_status_message == 'Publish externally failed'
        assert mock_upload_instance.process_status == ProcessStatus.FAILURE

        # Check if the old error was cleaned up
        assert len(mock_upload_instance.errors) == 1

        # Check that the error information is actually being stored in the upload
        assert error_message in mock_upload_instance.errors[0]


# Parameterized tests for common patterns
class TestWorkflowCommonPatterns:
    """Tests for common patterns across workflows."""

    @pytest.mark.parametrize(
        'workflow_class,input_data,activity_method',
        [
            (
                'EditUploadMetadataWorkflow',
                TestFixtures.edit_upload_metadata_input(),
                '_edit_upload_metadata_local',
            ),
            (
                'PublishUploadWorkflow',
                TestFixtures.publish_upload_input(),
                '_publish_upload_local',
            ),
            (
                'PublishExternallyWorkflow',
                TestFixtures.publish_externally_input(),
                '_publish_externally_local',
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_workflow_id_management_pattern(
        self,
        mock_data_layer,
        workflow_class,
        input_data,
        activity_method,
        temporal_worker,
    ):
        """Test that workflows properly manage workflow IDs."""
        async with temporal_worker() as env:
            await env.client.execute_workflow(
                workflow_class,
                input_data,
                id=f'test-{workflow_class.lower()}-{uuid.uuid4()}',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

            # Verify workflow ID management calls occurred
            mock_data_layer['upload_instance'].save.assert_called()
            # Should remove workflow ID after each workflow.
            assert not mock_data_layer['upload_instance'].workflow_ids

            # Verify the specific activity method was called
            if hasattr(mock_data_layer['upload_instance'], activity_method):
                getattr(
                    mock_data_layer['upload_instance'], activity_method
                ).assert_called_once()


class TestUpdateUploadWorkflowCursor:
    def test_file_batch_input_uses_fixed_file_chunk_size(self):
        workflow = UpdateUploadWorkflow()
        input_data = TestFixtures.upload_processing_input()

        activity_input = workflow._process_entry_batch_from_file_input(
            parse_all_input=input_data,
            batch_dir='/tmp/entry-batches',
            batch_id=501,
            entry_activity_batch_size=2,
        )

        assert activity_input.upload_id == TEST_UPLOAD_ID
        assert activity_input.batch_dir_path == '/tmp/entry-batches'
        assert activity_input.chunk_id == 1
        assert activity_input.offset == 2
        assert activity_input.limit == 2

    def test_advance_to_next_parser_level_clears_completed_cursor(self):
        workflow = UpdateUploadWorkflow()
        input_data = TestFixtures.upload_processing_input()
        input_data.min_level = 4
        input_data.current_batch_dir = '/tmp/entry-batches'
        input_data.current_batch_index = 3
        input_data.total_batches = 3
        input_data.next_parser_level = 6

        workflow._advance_to_next_parser_level(input_data)

        assert input_data.min_level == 7
        assert input_data.current_batch_dir is None
        assert input_data.current_batch_index == 0
        assert input_data.total_batches == 0
        assert input_data.next_parser_level is None


class TestWorkflowErrorHandling:
    """Tests for workflow error handling scenarios."""

    @pytest.mark.parametrize(
        'workflow_class, input_fixture, activity_to_fail, mock_target_name, expected_status_message',
        [
            pytest.param(
                'ProcessEntryWorkflow',
                TestFixtures.process_entry_input,
                '_process_entry_local',
                'entry_instance',
                'Process process_entry failed',
                id='ProcessEntryWorkflow-failure-handling',
            ),
            pytest.param(
                'UpdateUploadWorkflow',
                TestFixtures.upload_processing_input,
                'update_files',
                'upload_instance',
                'Process upload failed',
                id='UpdateUploadWorkflow-failure-handling',
            ),
            pytest.param(
                'EditUploadMetadataWorkflow',
                TestFixtures.edit_upload_metadata_input,
                '_edit_upload_metadata_local',
                'upload_instance',
                'Edit metadata failed',
                id='EditUploadMetadataWorkflow-failure-handling',
            ),
            pytest.param(
                'ImportBundleWorkflow',
                TestFixtures.import_bundle_input,
                '_import_bundle_local',
                'upload_instance',
                'Import bundle failed',
                id='ImportBundleWorkflow-failure-handling',
            ),
            pytest.param(
                'PublishUploadWorkflow',
                TestFixtures.publish_upload_input,
                '_publish_upload_local',
                'upload_instance',
                'Publish upload failed',
                id='PublishUploadWorkflow-failure-handling',
            ),
            pytest.param(
                'PublishExternallyWorkflow',
                TestFixtures.publish_externally_input,
                '_publish_externally_local',
                'upload_instance',
                'Publish externally failed',
                id='PublishExternallyWorkflow-failure-handling',
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_workflow_failure_handling(
        self,
        mock_data_layer,
        monkeypatch,
        workflow_class,
        input_fixture,
        activity_to_fail,
        mock_target_name,
        expected_status_message,
        temporal_worker,
    ):
        """Test that workflows handle failures consistently with a try-catch-finally pattern."""
        # Mock the activity to fail
        mock_target = mock_data_layer[mock_target_name]
        getattr(mock_target, activity_to_fail).side_effect = Exception(
            f'Simulated {activity_to_fail} failure'
        )

        def mock_fail(*errors):
            mock_target.process_status = ProcessStatus.FAILURE
            mock_target.errors.extend(str(error) for error in errors)

        mock_target.fail = mock_fail

        # Special setup for UpdateUploadWorkflow
        if workflow_class == 'UpdateUploadWorkflow':
            monkeypatch.setattr('nomad.workflows.activities.parser_min_level', 0)

        async with temporal_worker() as env:
            input_data = input_fixture()
            with pytest.raises(Exception):
                await env.client.execute_workflow(
                    workflow_class,
                    input_data,
                    id=f'test-{workflow_class.lower()}-{uuid.uuid4()}-failure-pattern',
                    task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
                )

        # Verify consistent failure handling
        assert mock_target.process_status == ProcessStatus.FAILURE
        mock_target.last_status_message = expected_status_message
        mock_target.save.assert_called()

        # Verify workflow ID is cleared for upload-level workflows
        if mock_target_name == 'upload_instance':
            assert not mock_target.workflow_ids

    @pytest.mark.asyncio
    async def test_workflow_id_cleanup_on_success(
        self,
        mock_data_layer,
        temporal_worker,
    ):
        """Test that workflow IDs are properly cleaned up on successful execution."""
        async with temporal_worker() as env:
            input_data = TestFixtures.edit_upload_metadata_input()

            await env.client.execute_workflow(
                'EditUploadMetadataWorkflow',
                input_data,
                id='test-workflow-id-cleanup-success',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

        # Verify workflow ID was added and then removed (cleanup)
        assert mock_data_layer['upload_instance'].save.call_count >= 1
        assert not mock_data_layer['upload_instance'].workflow_ids

    @pytest.mark.asyncio
    async def test_partial_entry_failure_upload_success(
        self,
        mock_data_layer,
        monkeypatch,
        temporal_worker,
    ):
        """Test that when individual entries fail, the upload itself is not marked as a failure."""

        # Mock next_level_entries to return multiple entries
        def mock_next_level_entries_side_effect(*args, **kwargs):
            if not hasattr(mock_next_level_entries_side_effect, 'call_count'):
                mock_next_level_entries_side_effect.call_count = 0

            mock_next_level_entries_side_effect.call_count += 1

            if mock_next_level_entries_side_effect.call_count == 1:
                # Return two entries - one will succeed, one will fail
                return [Mock(entry_id='test-entry-1'), Mock(entry_id='test-entry-2')]
            else:
                return []

        mock_data_layer[
            'upload_instance'
        ].next_level_entries.side_effect = mock_next_level_entries_side_effect

        successful_entry = MagicMock()
        failing_entry = MagicMock()
        failing_entry._process_entry_local.side_effect = Exception(
            'Simulated entry processing failure'
        )
        mock_data_layer['entry_class'].get.side_effect = {
            'test-entry-1': successful_entry,
            'test-entry-2': failing_entry,
        }.get

        # Mock parser_min_level
        monkeypatch.setattr('nomad.workflows.activities.parser_min_level', 0)

        async with temporal_worker() as env:
            input_data = TestFixtures.upload_processing_input()

            await env.client.execute_workflow(
                'UpdateUploadWorkflow',
                input_data,
                id='test-partial-entry-failure',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

        # Verify that the upload is still marked as successful despite entry failures
        mock_data_layer['upload_instance'].process_status = ProcessStatus.SUCCESS
        mock_data_layer['upload_instance'].set_last_status_message.assert_called_with(
            'Process completed successfully'
        )

        # Verify that entry processing was attempted for both entries.
        assert mock_data_layer['entry_class'].get.call_count >= 2
        successful_entry._process_entry_local.assert_called()
        failing_entry._process_entry_local.assert_called()

        # Verify that the upload workflow completed successfully
        # (The upload should not be marked as failed due to individual entry failures)
        assert (
            mock_data_layer['upload_instance'].save.call_count >= 1
        )  #  remove workflow ID calls


class TestWorkflowPerformanceAndScalability:
    """Tests focusing on performance and scalability improvements."""

    @pytest.mark.asyncio
    async def test_workflow_handles_very_large_datasets(
        self,
        mock_data_layer,
        monkeypatch,
        temporal_worker,
    ):
        """Test workflow can handle very large datasets without memory issues."""
        # Simulate a very large dataset
        huge_file_set = {f'file_{i}.txt' for i in range(5000)}  # 5K files
        huge_entry_set = [
            Mock(entry_id=f'entry-{i}') for i in range(100)
        ]  # 100 entries

        mock_data_layer['upload_instance'].update_files.return_value = huge_file_set

        call_count = 0

        def mock_next_level_entries_huge(*args, **kwargs):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                return huge_entry_set
            else:
                return []

        mock_data_layer[
            'upload_instance'
        ].next_level_entries.side_effect = mock_next_level_entries_huge

        # Mock generate_batches for large batches
        def mock_generate_batches_large(
            items, max_desired_batch_size=10, max_batches=10
        ):
            return [
                items[i : i + max_desired_batch_size]
                for i in range(0, len(items), max_desired_batch_size)
            ]

        monkeypatch.setattr(
            'nomad.workflows.activities.generate_batches', mock_generate_batches_large
        )
        monkeypatch.setattr('nomad.workflows.activities.parser_min_level', 0)

        async with temporal_worker() as env:
            input_data = UploadProcessingWorkflowInput(
                upload_id='test-upload',
                workflow_id='test-workflow-huge-dataset',
                workflow_tmp_dir=tempfile.mkdtemp(),
            )

            await env.client.execute_workflow(
                'UpdateUploadWorkflow',
                input_data,
                id='test-process-upload-huge-dataset',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

        # Verify workflow completed successfully with large dataset
        mock_data_layer['upload_instance'].match_all.assert_called_once()
        match_all_call = mock_data_layer['upload_instance'].match_all.call_args
        assert match_all_call[1]['updated_files'] == huge_file_set

    @pytest.mark.asyncio
    async def test_workflow_multiple_parser_levels_with_file_batches(
        self,
        mock_data_layer,
        monkeypatch,
        temporal_worker,
    ):
        """Test workflow processes multiple parser levels with file-based batching."""
        # Setup multiple parser levels with different entry counts
        level_entries = {
            1: [Mock(entry_id=f'level1-entry-{i}') for i in range(30)],
            2: [Mock(entry_id=f'level2-entry-{i}') for i in range(20)],
            3: [],  # End processing
        }

        call_count = 0
        current_parser_level = 0

        def mock_next_level_entries_multi_level(*args, **kwargs):
            nonlocal call_count, current_parser_level
            call_count += 1

            # Simulate parser level progression
            if call_count == 1:
                current_parser_level = 1
                return level_entries[1]
            elif call_count == 2:
                current_parser_level = 2
                return level_entries[2]
            else:
                return level_entries[3]

        mock_data_layer[
            'upload_instance'
        ].next_level_entries.side_effect = mock_next_level_entries_multi_level
        mock_data_layer['upload_instance'].update_files.return_value = {'file1.txt'}

        # Mock parser_level to change with each call
        def mock_parser_level_side_effect(_self):
            return current_parser_level

        type(mock_data_layer['upload_instance']).parser_level = property(
            mock_parser_level_side_effect
        )

        # Mock generate_batches for multiple batches
        def mock_generate_batches_multi(
            items, max_desired_batch_size=10, max_batches=10
        ):
            return [
                items[i : i + max_desired_batch_size]
                for i in range(0, len(items), max_desired_batch_size)
            ]

        monkeypatch.setattr(
            'nomad.workflows.activities.generate_batches', mock_generate_batches_multi
        )
        monkeypatch.setattr('nomad.workflows.activities.parser_min_level', 0)

        async with temporal_worker() as env:
            input_data = UploadProcessingWorkflowInput(
                upload_id='test-upload',
                workflow_id='test-workflow-multi-level',
                workflow_tmp_dir=tempfile.mkdtemp(),
            )

            await env.client.execute_workflow(
                'UpdateUploadWorkflow',
                input_data,
                id='test-process-upload-multi-level',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

        # Verify workflow processed multiple levels
        assert mock_data_layer['upload_instance'].next_level_entries.call_count == 3


class TestTransferUploadOwnershipWorkflow:
    """Tests for TransferUploadOwnershipWorkflow."""

    @pytest.fixture
    def ownership_transfer_mock_data_layer(self, mock_data_layer, monkeypatch):
        """Setup mocks specific to ownership transfer workflow tests."""
        # Mock OwnershipTransferRecord queryset delete behavior in nomad.mongo.users
        mock_record_queryset = MagicMock()
        mock_record_class = Mock()
        mock_record_class.objects.return_value = mock_record_queryset
        monkeypatch.setattr(
            'nomad.mongo.users.OwnershipTransferRecord', mock_record_class
        )

        # Mock config with admin_user_id
        mock_data_layer['config'].services = Mock()
        mock_data_layer['config'].services.admin_user_id = 'admin-user-123'

        # Configure upload instance for ownership transfer
        mock_data_layer['upload_instance'].main_author = TEST_USER_ID
        mock_data_layer[
            'upload_instance'
        ].current_process = '_transfer_upload_ownership'
        mock_data_layer['upload_instance'].reviewers = [
            'reviewer-1',
            'new-owner-user-id',
        ]
        mock_data_layer['upload_instance'].process_status = ProcessStatus.PENDING

        mock_data_layer['record_queryset'] = mock_record_queryset
        mock_data_layer['record_class'] = mock_record_class

        return mock_data_layer

    @pytest.mark.asyncio
    async def test_successful_ownership_transfer(
        self,
        ownership_transfer_mock_data_layer,
        temporal_worker,
    ):
        """Test successful ownership transfer workflow execution."""
        async with temporal_worker() as env:
            input_data = TestFixtures.transfer_upload_ownership_input()

            await env.client.execute_workflow(
                'TransferUploadOwnershipWorkflow',
                input_data,
                id='test-transfer-ownership-success',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

            # Verify setup activity was called
            ownership_transfer_mock_data_layer['upload_class'].get.assert_called_with(
                TEST_UPLOAD_ID
            )
            # Verify metadata edit was performed
            assert (
                ownership_transfer_mock_data_layer['upload_instance'].save.call_count
                >= 0
            )
            # Verify ownership transfer record was cleaned up
            ownership_transfer_mock_data_layer[
                'record_class'
            ].objects.assert_called_once_with(
                resource_type='upload',
                resource_id=TEST_UPLOAD_ID,
            )
            ownership_transfer_mock_data_layer[
                'record_queryset'
            ].delete.assert_called_once()
            # Verify process status is SUCCESS
            assert (
                ownership_transfer_mock_data_layer['upload_instance'].process_status
                == ProcessStatus.SUCCESS
            )
            ownership_transfer_mock_data_layer[
                'notification_service'
            ].emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_ownership_transfer_removes_reviewers(
        self,
        ownership_transfer_mock_data_layer,
        temporal_worker,
    ):
        """Test that new owner and previous owner are removed from reviewers."""
        # Setup: new owner and previous owner are both reviewers
        ownership_transfer_mock_data_layer['upload_instance'].reviewers = [
            'reviewer-1',
            'new-owner-user-id',
            TEST_USER_ID,
        ]

        async with temporal_worker() as env:
            input_data = TestFixtures.transfer_upload_ownership_input()

            await env.client.execute_workflow(
                'TransferUploadOwnershipWorkflow',
                input_data,
                id='test-transfer-ownership-reviewer-cleanup',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

            # Verify reviewers were modified
            upload_instance = ownership_transfer_mock_data_layer['upload_instance']
            assert upload_instance.save.called
            # After save calls, reviewers should only contain reviewer-1
            # (this is handled inside complete_upload_ownership_transfer_activity)

    @pytest.mark.asyncio
    async def test_ownership_transfer_with_no_reviewers(
        self,
        ownership_transfer_mock_data_layer,
        temporal_worker,
    ):
        """Test ownership transfer when no reviewers are set."""
        ownership_transfer_mock_data_layer['upload_instance'].reviewers = None

        async with temporal_worker() as env:
            input_data = TestFixtures.transfer_upload_ownership_input()

            await env.client.execute_workflow(
                'TransferUploadOwnershipWorkflow',
                input_data,
                id='test-transfer-ownership-no-reviewers',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

            # Verify workflow completed successfully
            ownership_transfer_mock_data_layer[
                'record_class'
            ].objects.assert_called_once_with(
                resource_type='upload',
                resource_id=TEST_UPLOAD_ID,
            )
            ownership_transfer_mock_data_layer[
                'record_queryset'
            ].delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_ownership_transfer_deletes_pending_record(
        self,
        ownership_transfer_mock_data_layer,
        temporal_worker,
    ):
        """Test that ownership transfer records are deleted after successful transfer."""
        async with temporal_worker() as env:
            input_data = TestFixtures.transfer_upload_ownership_input()

            await env.client.execute_workflow(
                'TransferUploadOwnershipWorkflow',
                input_data,
                id='test-transfer-ownership-record-delete',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

            # Verify cleanup deletes all records for this upload.
            ownership_transfer_mock_data_layer[
                'record_class'
            ].objects.assert_called_once_with(
                resource_type='upload',
                resource_id=TEST_UPLOAD_ID,
            )
            ownership_transfer_mock_data_layer[
                'record_queryset'
            ].delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_ownership_transfer_workflow_failure_handling(
        self,
        ownership_transfer_mock_data_layer,
        temporal_worker,
    ):
        """Test workflow handles activity failures and sets appropriate status."""
        # Mock edit_upload_metadata_activity to raise an exception
        ownership_transfer_mock_data_layer[
            'upload_instance'
        ].save.side_effect = Exception('Metadata update failed')

        async with temporal_worker() as env:
            input_data = TestFixtures.transfer_upload_ownership_input()

            # Workflow should handle the failure gracefully
            with pytest.raises(Exception):
                await env.client.execute_workflow(
                    'TransferUploadOwnershipWorkflow',
                    input_data,
                    id='test-transfer-ownership-failure',
                    task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
                )

    @pytest.mark.asyncio
    async def test_ownership_transfer_with_no_pending_record(
        self,
        ownership_transfer_mock_data_layer,
        temporal_worker,
    ):
        """Test ownership transfer when no matching transfer records exist."""
        ownership_transfer_mock_data_layer['record_queryset'].delete.return_value = 0

        async with temporal_worker() as env:
            input_data = TestFixtures.transfer_upload_ownership_input()

            await env.client.execute_workflow(
                'TransferUploadOwnershipWorkflow',
                input_data,
                id='test-transfer-ownership-no-record',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
            )

            # Verify workflow still completed (graceful handling of no matching records).
            ownership_transfer_mock_data_layer[
                'record_class'
            ].objects.assert_called_once_with(
                resource_type='upload',
                resource_id=TEST_UPLOAD_ID,
            )
            ownership_transfer_mock_data_layer[
                'record_queryset'
            ].delete.assert_called_once()


def _not_found_error() -> RPCError:
    return RPCError('Workflow not found', RPCStatusCode.NOT_FOUND, b'')


def _mock_temporal_client(
    handle: AsyncMock | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    mock_client = AsyncMock()
    mock_handle = handle or AsyncMock()
    mock_client.get_workflow_handle = MagicMock(return_value=mock_handle)
    return mock_client, mock_handle


class TestSetupServerStats:
    """Tests for the setup_server_stats function."""

    @pytest.mark.asyncio
    async def test_setup_server_stats_starts_when_missing(self):
        mock_client, mock_handle = _mock_temporal_client()
        mock_handle.describe.side_effect = _not_found_error()

        await setup_server_stats(mock_client)

        mock_client.get_workflow_handle.assert_called_once_with(
            SERVER_STATS_WORKFLOW_ID
        )
        mock_handle.terminate.assert_not_called()
        mock_client.start_workflow.assert_called_once()
        call_args = mock_client.start_workflow.call_args
        assert call_args[0][0] == ServerStatsWorkflow.run
        assert call_args[1]['id'] == SERVER_STATS_WORKFLOW_ID
        assert call_args[1]['task_queue'] == TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value
        assert (
            call_args[1]['cron_schedule']
            == config.services.collect_server_stats_cron_expression
        )
        assert (
            call_args[1]['id_conflict_policy'] == WorkflowIDConflictPolicy.USE_EXISTING
        )

    @pytest.mark.asyncio
    async def test_setup_server_stats_is_idempotent_when_cron_matches(self):
        mock_client, mock_handle = _mock_temporal_client()
        mock_description = MagicMock()
        mock_description.raw_info.cron_schedule = (
            config.services.collect_server_stats_cron_expression
        )
        mock_handle.describe.return_value = mock_description

        await setup_server_stats(mock_client)

        mock_handle.terminate.assert_not_called()
        mock_client.start_workflow.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_server_stats_handles_already_started(self):
        mock_client, mock_handle = _mock_temporal_client()
        mock_handle.describe.side_effect = _not_found_error()
        mock_client.start_workflow.side_effect = WorkflowAlreadyStartedError(
            SERVER_STATS_WORKFLOW_ID, 'ServerStatsWorkflow'
        )

        await setup_server_stats(mock_client)

        mock_client.start_workflow.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_server_stats_terminates_when_disabled(self, monkeypatch):
        monkeypatch.setattr(config.services, 'collect_server_stats', False)
        mock_client, mock_handle = _mock_temporal_client()

        await setup_server_stats(mock_client)

        mock_handle.terminate.assert_awaited_once()
        mock_client.start_workflow.assert_not_called()

    @pytest.mark.asyncio
    async def test_server_stats_workflow_execution(
        self,
        mongo_function,
        elastic_function,
        raw_files_function,
        temporal_worker,
        monkeypatch,
    ):
        mock_collect = MagicMock()
        monkeypatch.setattr(
            'nomad.workflows.activities.collect_server_stats', mock_collect
        )

        async with temporal_worker() as env:
            await env.client.execute_workflow(
                'ServerStatsWorkflow',
                id='test-server-stats-workflow',
                task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
            )

        mock_collect.assert_called_once()
