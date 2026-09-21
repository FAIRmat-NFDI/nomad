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

"""Stable plugin-facing Actions API.

Internal callers use application services and owning modules directly. This public
boundary preserves plugin imports, call signatures, and Temporal status results.
"""

import os
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import RetryPolicy

from nomad.actions.bootstrap import action_service
from nomad.actions.domain import ActionStatus
from nomad.actions.models import (
    ActionRecord,
    ActionRecordPage,
    ActionSchemaInfo,
    ActionStreamEvent,
    ActionStreamEventSeverity,
    ActionStreamEventType,
    ActionStreamItem,
    ActionSummaryRecord,
    RequestSignalInputActivityInput,
)
from nomad.actions.plugin_adapter import get_all_action_schemas, validate_action_arg
from nomad.actions.streams import (
    ACTION_STREAM_TOPIC,
    PROCESSING_STREAM_TOPIC,
    ActionStreamUnavailable,
    action_event_publisher,
    publish_action_event,
    stream_action_events_for_user_async,
    stream_processing_events_for_user_async,
)
from nomad.config import config

ACTION_INSTANCE_ASSETS_DIRNAME = 'assets'
ACTION_INSTANCE_ARTIFACTS_DIRNAME = 'artifacts'
ACTION_INSTANCE_NOMAD_SYSTEM_DIRNAME = 'nomad_system'

__all__ = [
    'ActionRecord',
    'ActionRecordPage',
    'ActionSummaryRecord',
    'ActionSchemaInfo',
    'ActionStreamEvent',
    'ActionStreamEventSeverity',
    'ActionStreamEventType',
    'ActionStreamItem',
    'RequestSignalInputActivityInput',
    'ACTION_STREAM_TOPIC',
    'PROCESSING_STREAM_TOPIC',
    'ActionStreamUnavailable',
    'action_event_publisher',
    'publish_action_event',
    'stream_action_events_for_user_async',
    'stream_processing_events_for_user_async',
    'get_all_action_schemas',
    'validate_action_arg',
    'start_action',
    'start_action_async',
    'stop_action',
    'stop_action_async',
    'get_action_status',
    'get_action_status_async',
    'get_action_result',
    'get_action_result_async',
    'get_user_action',
    'list_user_actions',
    'submit_signal_input',
    'action_artifacts_dir',
    'action_instance_assets_dir',
    'action_instance_artifacts_dir',
    'action_log_file_path',
    'request_signal_input',
    'request_signal_input_activity',
]


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _action_instance_dir(action_instance_id: str, *parts: str) -> str:
    return _ensure_dir(os.path.join(config.fs.actions, action_instance_id, *parts))


def action_artifacts_dir() -> str:
    """
    Returns the path to the action artifacts directory.

    Activities can use this directory to store artifacts that can be used
    by multiple actions, such as ML training models, global configuration,
    or reference datasets.
    """

    return _ensure_dir(os.path.join(config.fs.actions, 'artifacts'))


def action_instance_assets_dir(action_instance_id: str) -> str:
    """
    Returns the path to user-uploaded assets for a specific instance.
    """
    return _action_instance_dir(action_instance_id, ACTION_INSTANCE_ASSETS_DIRNAME)


def action_instance_artifacts_dir(action_instance_id: str) -> str:
    """
    Returns the path to generated outputs for a specific instance.
    """
    return _action_instance_dir(action_instance_id, ACTION_INSTANCE_ARTIFACTS_DIRNAME)


def action_log_file_path(action_instance_id: str) -> str:
    """Return the instance-scoped log path; legacy global logs are not read."""
    log_dir = _action_instance_dir(
        action_instance_id, ACTION_INSTANCE_NOMAD_SYSTEM_DIRNAME, 'logs'
    )
    return os.path.join(log_dir, f'{action_instance_id}.log')


async def request_signal_input(
    action_instance_id: str,
    user_id: str,
    signal_fn_name: str,
    title: str | None = None,
    description: str | None = None,
    content: str | None = None,
    initial_data: dict[str, Any] | None = None,
    timeout: timedelta = timedelta(hours=1),
):
    """
    Record that a running action is requesting signal input for a specific signal.

    This is a helper function for workflow authors. It calls an activity that
    looks up the action instance for the given user, verifies it is active,
    and appends the requested signal dictionary to the action's pending
    signal-input requests list in the database.

    Args:
        action_instance_id: Unique identifier of the action instance (also used
            as the workflow ID).
        user_id: Identifier of the user who owns the action instance.
        signal_fn_name: Name of the workflow signal representing the requested
            signal input.
        title: Optional title to display in the frontend form.
        description: Optional description to display in the frontend form.
        content: Optional markdown content to display in the frontend form.
        initial_data: Optional initial data to pre-fill the signal input form.
        timeout: Optional timeout for the signal input activity. Defaults to 1 hour.

    Raises:
        Exception: If the action does not exist for the user or is not active.
    """
    await workflow.execute_activity(
        request_signal_input_activity,
        RequestSignalInputActivityInput(
            action_instance_id=action_instance_id,
            user_id=user_id,
            signal_fn_name=signal_fn_name,
            title=title,
            description=description,
            content=content,
            initial_data=initial_data,
        ),
        start_to_close_timeout=timeout,
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


@activity.defn
async def request_signal_input_activity(data: RequestSignalInputActivityInput):
    return await action_service.a_request_signal_input(data)


def _resolve_action_id(action_id: str | None, action_name: str | None) -> str:
    if action_id is not None and action_name is not None and action_id != action_name:
        raise ValueError('action_id and action_name must identify the same action.')
    resolved = action_id if action_id is not None else action_name
    if not resolved:
        raise ValueError('An action_id or action_name is required.')
    return resolved


def start_action(
    action_id: str | None = None, data: Any = None, *, action_name: str | None = None
) -> str:
    """Start an action; supports the documented ``action_name`` keyword."""
    return action_service.start(_resolve_action_id(action_id, action_name), data)


async def start_action_async(
    action_id: str | None = None, data: Any = None, *, action_name: str | None = None
) -> str:
    return await action_service.a_start(
        _resolve_action_id(action_id, action_name), data
    )


def stop_action(action_instance_id: str, user_id: str) -> None:
    return action_service.stop(action_instance_id, user_id)


async def stop_action_async(action_instance_id: str, user_id: str) -> None:
    return await action_service.a_stop(action_instance_id, user_id)


def get_action_status(
    action_instance_id: str, user_id: str | None = None
) -> WorkflowExecutionStatus:
    """Return Temporal's status enum for trusted in-process plugin code.

    With ``user_id``, enforce ownership. The documented one-argument form reads
    workflow status directly; HTTP endpoints must use the owned service method.
    """
    status = (
        action_service.get_status(action_instance_id, user_id)
        if user_id is not None
        else action_service.workflow.get_status(action_instance_id)
    )
    return WorkflowExecutionStatus[(status or ActionStatus.TERMINATED).name]


async def get_action_status_async(
    action_instance_id: str, user_id: str | None = None
) -> WorkflowExecutionStatus:
    status = (
        await action_service.a_get_status(action_instance_id, user_id)
        if user_id is not None
        else await action_service.a_workflow.get_status(action_instance_id)
    )
    return WorkflowExecutionStatus[(status or ActionStatus.TERMINATED).name]


def get_action_result(action_instance_id: str, user_id: str):
    return action_service.get_result(action_instance_id, user_id)


async def get_action_result_async(action_instance_id: str, user_id: str):
    return await action_service.a_get_result(action_instance_id, user_id)


async def get_user_action(action_instance_id: str, user_id: str) -> ActionRecord | None:
    record = await action_service.a_get_owned(action_instance_id, user_id)
    return (
        ActionRecord.model_validate(record, from_attributes=True)
        if record is not None
        else None
    )


async def list_user_actions(
    user_id: str,
    page_size: int = 20,
    cursor: str | None = None,
    upload_id: str | None = None,
) -> ActionRecordPage:
    return await action_service.a_list_owned(user_id, page_size, cursor, upload_id)


async def submit_signal_input(
    action_instance_id: str, user_id: str, signal_fn_name: str, data: Any
):
    return await action_service.a_submit_signal_input(
        action_instance_id, user_id, signal_fn_name, data
    )
