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

from collections.abc import Callable

from nomad.actions import TaskQueue
from nomad.actions.action import get_actions
from nomad.actions.manager import request_signal_input_activity
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
    prepare_cleanup_activity,
    prepare_next_level_entry_batches,
    process_entry_activity,
    process_entry_batch_activity,
    process_entry_batch_from_file_activity,
    publish_externally_activity,
    publish_upload_activity,
    server_stats_checkin_activity,
    setup_example_upload_activity,
    update_files_activity,
)


def get_nomad_internal_activities() -> list[Callable]:
    return [
        prepare_cleanup_activity,
        get_cleanup_entry_batch_from_file,
        cleanup_entries_batch_activity,
        finalize_cleanup_activity,
        finalize_upload_processing_activity,
        delete_upload_search_activity,
        delete_upload_files_activity,
        delete_upload_record_activity,
        process_entry_activity,
        process_entry_batch_activity,
        match_all_activity,
        update_files_activity,
        setup_example_upload_activity,
        edit_upload_metadata_activity,
        import_bundle_activity,
        publish_upload_activity,
        publish_externally_activity,
        handle_heartbeat_failure_activity,
        prepare_next_level_entry_batches,
        process_entry_batch_from_file_activity,
        handle_batch_heartbeat_failure_activity,
        complete_upload_ownership_transfer_activity,
        server_stats_checkin_activity,
    ]


def get_all_activities(task_queue: TaskQueue) -> list[Callable]:
    activities = []
    if task_queue != TaskQueue.NOMAD_INTERNAL_WORKFLOWS:
        activities.append(request_signal_input_activity)
    for action_entry_point in get_actions().values():
        action = action_entry_point.load()
        if action_entry_point.task_queue == task_queue:
            activities.extend(action.activities)
        activities.extend(_task_queue_activities(action, task_queue))
    if task_queue == TaskQueue.NOMAD_INTERNAL_WORKFLOWS:
        activities.extend(get_nomad_internal_activities())
    return list(set(activities))


def _task_queue_activities(action, task_queue: TaskQueue) -> list[Callable]:
    task_queue_activities = getattr(action, 'task_queue_activities', {})
    queue_value = getattr(task_queue, 'value', task_queue)
    return [
        activity
        for queue, activities in task_queue_activities.items()
        if queue == task_queue or queue == queue_value
        for activity in activities
    ]
