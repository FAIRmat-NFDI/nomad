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

"""Temporal implementation of workflow ports; no dependency on the manager."""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from temporalio.client import WorkflowExecutionStatus
from temporalio.common import Priority
from temporalio.service import RPCError, RPCStatusCode

from nomad.actions import plugin_adapter
from nomad.actions.client import get_client
from nomad.actions.domain import ActionDefinition, ActionStatus


class RunThread(threading.Thread):
    def __init__(self, coro: Coroutine[Any, Any, Any]):
        self.coro = coro
        self.result = None
        self.error: BaseException | None = None
        super().__init__()

    def run(self):
        try:
            self.result = asyncio.run(self.coro)
        except BaseException as exc:
            self.error = exc


def _run_temporal_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        thread = RunThread(coro)
        thread.start()
        thread.join()
        if thread.error is not None:
            raise thread.error
        return thread.result
    return asyncio.run(coro)


async def _get_workflow_status_safe(
    action_instance_id: str,
) -> WorkflowExecutionStatus | None:
    """
    Safely retrieves workflow status, returning None if workflow not found.

    Args:
        action_instance_id: The unique ID of the action instance.

    Returns:
        The workflow status, or None if workflow not found.

    Raises:
        Exception: For errors other than workflow not found.
    """
    try:
        client = await get_client()
        handle = client.get_workflow_handle(action_instance_id)
        desc = await handle.describe()
        return desc.status
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return None
        raise


async def _get_workflow_result_safe(action_instance_id: str) -> dict[str, Any] | None:
    """
    Safely retrieves workflow result, returning None if workflow not found.

    Args:
        action_instance_id: The unique ID of the action instance.

    Returns:
        The workflow result, or None if workflow not found.

    Raises:
        Exception: For errors other than workflow not found.
    """
    try:
        client = await get_client()
        handle = client.get_workflow_handle(action_instance_id)
        return await handle.result()
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return None
        raise


async def _async_start_workflow(action, data, workflow_id, priority) -> str:
    """
    Asynchronously starts a workflow.

    Args:
        action: The action to start.
        data: The input data for the workflow.
        workflow_id: The ID of the workflow to start.
        priority: The priority of the workflow to start.

    Returns:
        The ID of the started workflow.
    """
    client = await get_client()
    await client.start_workflow(
        action.workflow.run,
        data,
        id=workflow_id,
        task_queue=action.task_queue,
        priority=priority,
    )
    return workflow_id


async def _async_stop_workflow(workflow_id: str):
    """
    Asynchronously stops a workflow.

    Args:
        workflow_id: The ID of the workflow to stop.
    """
    client = await get_client()
    handle = client.get_workflow_handle(workflow_id)
    await handle.cancel()


async def _async_signal_workflow(
    workflow_cls,
    workflow_id: str,
    signal_fn_name: str,
    data: Any,
) -> None:
    """
    Asynchronously send a signal to a running Temporal workflow execution.

    This helper obtains a workflow handle using the provided workflow ID,
    resolves the specified signal method from the workflow class, and sends
    the signal with the supplied payload.

    Args:
        workflow_cls: The Temporal workflow class that defines the signal.
        workflow_id: The ID of the target workflow execution. This targets the
            latest run for the given ID unless a run ID is specified elsewhere.
        signal_fn_name: The name of the signal method on the workflow class.
            The method must be decorated with ``@workflow.signal``.
        data: The payload to send with the signal. Must be serializable by the
            Temporal payload converter configured for the client.
    """
    client = await get_client()
    handle = client.get_workflow_handle(workflow_id)

    try:
        signal_fn: Callable = getattr(workflow_cls, signal_fn_name)
    except AttributeError as e:
        raise ValueError(
            f"Signal '{signal_fn_name}' not found on workflow {workflow_cls.__name__}"
        ) from e

    if not callable(signal_fn):
        raise TypeError(
            f"Attribute '{signal_fn_name}' on {workflow_cls.__name__} is not callable"
        )

    await handle.signal(signal_fn, data)


class TemporalAsyncActionWorkflow:
    async def start(
        self, definition: ActionDefinition, data: Any, instance_id: str, user_id: str
    ) -> None:
        entry = plugin_adapter.get_actions().get(definition.action_id)
        if entry is None:
            raise ValueError('Action not found')
        await _async_start_workflow(
            entry.load(),
            data,
            instance_id,
            Priority(
                priority_key=definition.priority_key,
                fairness_key=user_id
                if definition.priority_fairness_key == 'user_id'
                else None,
            ),
        )

    async def cancel(self, action_instance_id: str) -> None:
        await _async_stop_workflow(action_instance_id)

    async def signal(
        self, action_id: str, instance_id: str, signal_fn_name: str, data: Any
    ) -> None:
        entry = plugin_adapter.get_actions().get(action_id)
        if entry is None:
            raise ValueError('Action not found')
        await _async_signal_workflow(
            entry.load().workflow,
            workflow_id=instance_id,
            signal_fn_name=signal_fn_name,
            data=data,
        )

    async def get_status(self, instance_id: str) -> ActionStatus | None:
        status = await _get_workflow_status_safe(instance_id)
        return ActionStatus(status.name) if status is not None else None

    async def get_result(self, instance_id: str) -> Any:
        return await _get_workflow_result_safe(instance_id)


class TemporalSyncActionWorkflow:
    def __init__(self, workflow: TemporalAsyncActionWorkflow):
        self.workflow = workflow

    def start(
        self, definition: ActionDefinition, data: Any, instance_id: str, user_id: str
    ) -> None:
        _run_temporal_sync(self.workflow.start(definition, data, instance_id, user_id))

    def cancel(self, action_instance_id: str) -> None:
        _run_temporal_sync(self.workflow.cancel(action_instance_id))

    def get_status(self, instance_id: str) -> ActionStatus | None:
        return _run_temporal_sync(self.workflow.get_status(instance_id))

    def get_result(self, instance_id: str) -> Any:
        return _run_temporal_sync(self.workflow.get_result(instance_id))
