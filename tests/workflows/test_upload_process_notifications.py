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

from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from temporalio.testing import ActivityEnvironment

from nomad.processing.base import ProcessStatus
from nomad.workflows.activities import finalize_upload_processing_activity
from nomad.workflows.shared_objects import (
    FinalizeUploadProcessingFailureInput,
    FinalizeUploadProcessingSuccessInput,
)


@pytest.fixture(autouse=True)
def activity_info(monkeypatch):
    info = MagicMock(
        return_value=replace(
            ActivityEnvironment().info,
            workflow_id='workflow-1',
            workflow_run_id='run-1',
        )
    )
    monkeypatch.setattr('nomad.workflows.activities.activity.info', info)
    return info


@pytest.mark.parametrize(
    'process',
    [
        '_process_upload',
        '_publish_upload',
        '_publish_externally',
        '_edit_metadata',
        '_import_bundle',
    ],
)
@pytest.mark.parametrize('result', ['success', 'failure'])
def test_finalize_notifies_owner(monkeypatch, tmp_path, process, result, activity_info):
    upload = MagicMock(
        upload_id='upload-1',
        upload_name='Project One',
        main_author='owner-1',
        current_process=process,
        workflow_ids=['workflow-1'],
    )
    monkeypatch.setattr('nomad.workflows.activities.Upload.get', lambda _: upload)
    emit = MagicMock()
    monkeypatch.setattr('nomad.workflows.activities.notification_service.emit', emit)
    temporary_directory = tmp_path / 'workflow'
    temporary_directory.mkdir()
    kwargs = dict(
        upload_id='upload-1',
        workflow_id='workflow-1',
        workflow_tmp_dir=str(temporary_directory),
    )
    input = (
        FinalizeUploadProcessingSuccessInput(result='success', **kwargs)
        if result == 'success'
        else FinalizeUploadProcessingFailureInput(
            result='failure',
            failure_message='Job failed',
            error_details='details',
            **kwargs,
        )
    )

    finalize_upload_processing_activity(input)

    assert upload.workflow_ids == []
    assert not temporary_directory.exists()
    upload.save.assert_called_once()
    if result == 'success':
        assert upload.process_status == ProcessStatus.SUCCESS
    else:
        upload.fail.assert_called_once_with('details')
    emit.assert_called_once_with(
        user_id='owner-1',
        source='user',
        notification_type='upload_process',
        dedup_key='run-1',
        data={
            'resource_type': 'upload',
            'resource_id': 'upload-1',
            'resource_name': 'Project One',
            'process': process,
            'result': result,
            'message': 'Job failed'
            if result == 'failure'
            else 'Process completed successfully',
        },
    )

    # A retry after finalization still emits with the same deduplication key.
    finalize_upload_processing_activity(input)
    assert emit.call_args_list[0] == emit.call_args_list[1]

    # Reusing the workflow ID for another run must produce a separate inbox row.
    activity_info.return_value = replace(
        activity_info.return_value, workflow_run_id='run-2'
    )
    finalize_upload_processing_activity(input)
    assert emit.call_args.kwargs['dedup_key'] == 'run-2'


@pytest.mark.parametrize(
    'process,owner,trigger_processing',
    [
        (None, 'owner-1', True),
        ('', 'owner-1', True),
        ('_process_upload', None, True),
        ('_process_upload', 'owner-1', False),
        ('_transfer_upload_ownership', 'owner-1', True),
    ],
)
@pytest.mark.parametrize('result', ['success', 'failure'])
def test_finalize_skips_notifications(
    monkeypatch, process, owner, trigger_processing, result
):
    upload = MagicMock(
        main_author=owner, current_process=process, workflow_ids=['workflow-1']
    )
    monkeypatch.setattr('nomad.workflows.activities.Upload.get', lambda _: upload)
    emit = MagicMock()
    monkeypatch.setattr('nomad.workflows.activities.notification_service.emit', emit)
    kwargs = dict(upload_id='upload-1', workflow_id='workflow-1')
    input = (
        FinalizeUploadProcessingSuccessInput(
            result='success', trigger_processing=trigger_processing, **kwargs
        )
        if result == 'success'
        else FinalizeUploadProcessingFailureInput(result='failure', **kwargs)
    )
    finalize_upload_processing_activity(input)
    if result == 'failure' and process == '_process_upload' and owner:
        # A failed file update is a real failure even if processing was deferred.
        emit.assert_called_once()
    else:
        emit.assert_not_called()
    upload.save.assert_called_once()
    assert upload.workflow_ids == []


@pytest.mark.parametrize('result', ['success', 'failure'])
def test_notification_failure_only_logs_warning(monkeypatch, tmp_path, result):
    upload = MagicMock(
        main_author='owner-1',
        current_process='_process_upload',
        workflow_ids=['workflow-1'],
    )
    monkeypatch.setattr('nomad.workflows.activities.Upload.get', lambda _: upload)
    emit = MagicMock(side_effect=RuntimeError('Inbox write failed'))
    monkeypatch.setattr('nomad.workflows.activities.notification_service.emit', emit)
    directory = tmp_path / 'workflow'
    directory.mkdir()
    kwargs = dict(
        upload_id='upload-1',
        workflow_id='workflow-1',
        workflow_tmp_dir=str(directory),
    )
    input = (
        FinalizeUploadProcessingSuccessInput(result='success', **kwargs)
        if result == 'success'
        else FinalizeUploadProcessingFailureInput(result='failure', **kwargs)
    )

    finalize_upload_processing_activity(input)

    emit.assert_called_once()
    upload.get_logger.return_value.warning.assert_called_once_with(
        'could not emit process notification', exc_info=True
    )
    upload.save.assert_called_once()
    assert upload.workflow_ids == []
    assert not directory.exists()
    if result == 'success':
        assert upload.process_status == ProcessStatus.SUCCESS
    else:
        upload.fail.assert_called_once()
