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

from typing import Any

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from nomad.actions import TaskQueue
from nomad.actions.action import get_actions
from nomad.config import config
from nomad.workflows.workflows import (
    BatchCleanupEntriesWorkflow,
    DeleteUploadWorkflow,
    EditUploadMetadataWorkflow,
    ImportBundleWorkflow,
    ProcessEntryWorkflow,
    ProcessExampleUploadWorkflow,
    PublishExternallyWorkflow,
    PublishUploadWorkflow,
    ServerStatsWorkflow,
    TransferUploadOwnershipWorkflow,
    UpdateUploadWorkflow,
)

SERVER_STATS_WORKFLOW_ID = 'server-stats'


def get_nomad_internal_workflows() -> list:
    return [
        BatchCleanupEntriesWorkflow,
        DeleteUploadWorkflow,
        UpdateUploadWorkflow,
        ProcessEntryWorkflow,
        ProcessExampleUploadWorkflow,
        EditUploadMetadataWorkflow,
        ImportBundleWorkflow,
        PublishUploadWorkflow,
        PublishExternallyWorkflow,
        TransferUploadOwnershipWorkflow,
        ServerStatsWorkflow,
    ]


def get_all_workflows(task_queue: TaskQueue) -> list:
    workflows: list[Any] = []

    for action_entry_point in get_actions().values():
        if action_entry_point.task_queue == task_queue:
            action = action_entry_point.load()
            workflows.append(action.workflow)
            workflows.extend(action.child_workflows)

    if task_queue == TaskQueue.NOMAD_INTERNAL_WORKFLOWS:
        workflows.extend(get_nomad_internal_workflows())

    return list(set(workflows))


async def setup_server_stats(client: Client):
    handle = client.get_workflow_handle(SERVER_STATS_WORKFLOW_ID)

    if not config.services.collect_server_stats:
        try:
            await handle.terminate()
        except RPCError as e:
            if e.status != RPCStatusCode.NOT_FOUND:
                raise
        return

    desired_cron = config.services.collect_server_stats_cron_expression
    try:
        description = await handle.describe()
        existing_cron = getattr(description.raw_info, 'cron_schedule', '') or ''
        if existing_cron == desired_cron:
            return
        await handle.terminate()
    except RPCError as e:
        if e.status != RPCStatusCode.NOT_FOUND:
            raise

    try:
        await client.start_workflow(
            ServerStatsWorkflow.run,
            id=SERVER_STATS_WORKFLOW_ID,
            task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS.value,
            cron_schedule=desired_cron,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
    except WorkflowAlreadyStartedError:
        pass
