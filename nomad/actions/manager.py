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
This module provides utility functions for working with NOMAD actions.

It includes functions for:
- Validating action arguments.
- Retrieving action schemas.
- Managing action execution and results.
- Interacting with the Temporal workflow engine.
"""

import asyncio
import base64
import inspect
import os
import threading
import uuid
import warnings
from collections.abc import Coroutine
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, get_type_hints

from pydantic import BaseModel, SecretBytes, SecretStr, TypeAdapter
from temporalio import activity, workflow
from temporalio.client import WorkflowExecutionStatus
from temporalio.common import Priority, RetryPolicy
from temporalio.service import RPCError, RPCStatusCode

from nomad import infrastructure
from nomad.actions.action import get_actions
from nomad.actions.assets.models import ActionAssetPurpose
from nomad.actions.assets.service import (
    consume_staged_assets,
    extract_action_asset_refs,
    rollback_consumed_assets,
)
from nomad.actions.client import get_client
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
from nomad.actions.repositories import AsyncActionRepository, SyncActionRepository
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
from nomad.metainfo.metainfo import Callable
from nomad.utils.structlogging import get_logger

if TYPE_CHECKING:
    from nomad.files import PublicUploadFiles, StagingUploadFiles

__all__ = [
    'ActionRecord',
    'ActionSummaryRecord',
    'ActionRecordPage',
    'ActionSchemaInfo',
    'ActionStreamEvent',
    'ActionStreamEventSeverity',
    'ActionStreamEventType',
    'ActionStreamItem',
    'RequestSignalInputActivityInput',
    'ACTION_STREAM_TOPIC',
    'PROCESSING_STREAM_TOPIC',
    'action_artifacts_dir',
    'action_instance_assets_dir',
    'action_instance_artifacts_dir',
    'action_log_file_path',
    'get_action_result',
    'get_action_result_async',
    'get_action_status',
    'get_action_status_async',
    'get_all_action_schemas',
    'get_upload_files',
    'get_user_action',
    'list_user_actions',
    'ActionStreamUnavailable',
    'action_event_publisher',
    'publish_action_event',
    'request_signal_input',
    'request_signal_input_activity',
    'start_action',
    'start_action_async',
    'stop_action',
    'stop_action_async',
    'stream_processing_events_for_user_async',
    'stream_action_events_for_user_async',
    'submit_signal_input',
    'validate_action_arg',
]

ACTION_INSTANCE_ASSETS_DIRNAME = 'assets'
ACTION_INSTANCE_ARTIFACTS_DIRNAME = 'artifacts'
ACTION_INSTANCE_NOMAD_SYSTEM_DIRNAME = 'nomad_system'


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _action_instance_dir(action_instance_id: str, *parts: str) -> str:
    return _ensure_dir(os.path.join(config.fs.actions, action_instance_id, *parts))


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


_async_action_repository = AsyncActionRepository()
_sync_action_repository = SyncActionRepository()


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    async def _run_with_action_infra():
        await infrastructure.init_async_mongo()
        return await coro

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        raise RuntimeError(
            'Synchronous action APIs cannot be called from an active event loop. '
            'Use the corresponding *_async function and await it.'
        )

    # If async mongo has already been initialized on a running app loop,
    # execute this coroutine on that same loop to avoid loop-bound client issues.
    target_loop = infrastructure.async_mongo_loop
    if target_loop is not None and target_loop.is_running():
        future = asyncio.run_coroutine_threadsafe(_run_with_action_infra(), target_loop)
        return future.result()

    # Create our own loop when no shared app loop is available.
    return asyncio.run(_run_with_action_infra())


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


def _to_dict(data: Any) -> dict:
    """Convert data to a dictionary without persisting Pydantic secrets."""

    def _secret_exclusions(val: Any) -> Any:
        """Build a Pydantic exclusion tree from values before serialization."""
        if isinstance(val, (SecretStr, SecretBytes)):
            return True
        if isinstance(val, BaseModel):
            model_exclusions: dict[Any, Any] = {}
            for field_name in type(val).model_fields:
                exclusion = _secret_exclusions(getattr(val, field_name))
                if exclusion:
                    model_exclusions[field_name] = exclusion
            for field_name, field_value in (val.model_extra or {}).items():
                exclusion = _secret_exclusions(field_value)
                if exclusion:
                    model_exclusions[field_name] = exclusion
            return model_exclusions or None
        if isinstance(val, dict):
            dict_exclusions: dict[Any, Any] = {}
            for key, item in val.items():
                exclusion = _secret_exclusions(item)
                if exclusion:
                    dict_exclusions[key] = exclusion
            return dict_exclusions or None
        if isinstance(val, (list, tuple, set)):
            sequence_exclusions: dict[Any, Any] = {}
            for index, item in enumerate(val):
                exclusion = _secret_exclusions(item)
                if exclusion:
                    sequence_exclusions[index] = exclusion
            return sequence_exclusions or None
        return None

    def _remove_secrets(val: Any) -> Any:
        """Recursively remove secrets while preserving normal serialization."""
        if isinstance(val, (SecretStr, SecretBytes)):
            return None
        if isinstance(val, BaseModel):
            serialized = val.model_dump(by_alias=True, exclude=_secret_exclusions(val))
            return _remove_secrets(serialized)
        if isinstance(val, dict):
            new_data = {}
            for k, v in val.items():
                if isinstance(v, (SecretStr, SecretBytes)):
                    continue
                new_data[k] = _remove_secrets(v)
            return new_data
        if isinstance(val, list):
            return [
                _remove_secrets(item)
                for item in val
                if not isinstance(item, (SecretStr, SecretBytes))
            ]
        if isinstance(val, tuple):
            return tuple(
                _remove_secrets(item)
                for item in val
                if not isinstance(item, (SecretStr, SecretBytes))
            )
        if isinstance(val, set):
            return {
                _remove_secrets(item)
                for item in val
                if not isinstance(item, (SecretStr, SecretBytes))
            }
        return val

    if isinstance(data, BaseModel):
        return _remove_secrets(data)
    if is_dataclass(data) and not isinstance(data, type):
        return _remove_secrets(asdict(data))
    if isinstance(data, dict):
        return _remove_secrets(data)
    raise TypeError(f'Unsupported type: {type(data)}')


def _validate_with_pydantic(func: Callable, arg, entry_point: Any = None):
    """
    Validate the single argument of a function against its type hint using Pydantic.

    Args:
        func: The function with the argument to validate.
        arg: The argument to validate.
        entry_point: Optional action entry point containing active configuration.

    Returns:
        The validated argument.
    """
    hints = get_type_hints(func)

    # get the single non-return annotation
    [(_, param_type)] = [(n, t) for n, t in hints.items() if n != 'return']

    if entry_point is not None and hasattr(param_type, 'get_schema_for_entry_point'):
        param_type = param_type.get_schema_for_entry_point(entry_point)

    adapter = TypeAdapter(param_type)
    return adapter.validate_python(arg)


def _get_non_self_params(func: Callable) -> list[inspect.Parameter]:
    """Return callable parameters excluding a leading ``self`` parameter."""

    sig = inspect.signature(func)  # type: ignore[arg-type]
    return [param for param in sig.parameters.values() if param.name != 'self']


def _get_param_schema(func: Callable, entry_point: Any = None) -> dict[str, Any]:
    """
    Generate a JSON Schema for the single argument of a function.

    This is useful for generating frontend forms for actions.

    Args:
        func: The function with the argument to generate the schema for.
        entry_point: Optional action entry point containing active configuration.

    Returns:
        The JSON schema for the argument.
    """
    hints = get_type_hints(func)

    # get the single non-return annotation
    [(_, param_type)] = [(n, t) for n, t in hints.items() if n != 'return']

    if isinstance(param_type, type) and issubclass(param_type, BaseModel):
        if entry_point is not None and hasattr(
            param_type, 'get_schema_for_entry_point'
        ):
            param_type = param_type.get_schema_for_entry_point(entry_point)
        schema = param_type.model_json_schema(by_alias=True)
    else:
        adapter = TypeAdapter(param_type)
        schema = adapter.json_schema()

    # remove the user_id from the schema,
    # we rely on the user_id of the logged in user instead of form input.
    schema.get('properties', {}).pop('user_id', None)
    required = schema.get('required', [])
    if 'user_id' in required:
        required.remove('user_id')
    return schema


def _get_signal_schema(signal_fn: Callable) -> dict[str, Any]:
    """
    Generate a JSON Schema for the single argument of a signal function.
    Raises ValueError if there is more than one argument (excluding 'self').
    """
    params = _get_non_self_params(signal_fn)

    if len(params) > 1:
        name_str = getattr(signal_fn, '__name__', str(signal_fn))
        raise ValueError(
            f'Signal {name_str} has more than one argument. Only zero or one arguments are supported.'
        )

    if len(params) == 0:
        return {}

    hints = get_type_hints(signal_fn)
    param_type = hints.get(params[0].name, Any)

    if isinstance(param_type, type) and issubclass(param_type, BaseModel):
        return param_type.model_json_schema(by_alias=True)
    else:
        adapter = TypeAdapter(param_type)
        return adapter.json_schema()


def validate_action_arg(action_id: str, arg: Any):
    """
    Validate the argument for a given action's `workflow.run` function
    against its type hint. Raises if the action does not exist or the
    argument is invalid.
    """
    action = get_actions().get(action_id)
    if not action:
        raise ValueError('Action not found')
    return _validate_with_pydantic(action.load().workflow.run, arg, entry_point=action)


def get_all_action_schemas() -> list[ActionSchemaInfo]:
    """
    Return a list of JSON Schemas for all registered actions'
    `workflow.run` parameters, keyed by action_id.
    """
    data: list[ActionSchemaInfo] = []
    for action_id, action in get_actions().items():
        workflow_cls = action.load().workflow

        signals = []
        for attr_name in dir(workflow_cls):
            if attr_name.startswith('__'):
                continue
            attr = getattr(workflow_cls, attr_name, None)
            if hasattr(attr, '__temporal_signal_definition'):
                signal_fn: Callable | None = getattr(
                    getattr(attr, '__temporal_signal_definition'),
                    'fn',
                    attr,
                )
                if signal_fn is not None:
                    schema = _get_signal_schema(signal_fn)
                    # Expose Python method names as the canonical API key.
                    signals.append({attr_name: schema})

        data.append(
            ActionSchemaInfo(
                action_id=action_id,
                json_schema=_get_param_schema(workflow_cls.run, entry_point=action),
                description=action.description,
                task_queue=action.task_queue,
                groups=action.groups,
                users=action.users,
                name=action.name,
                plugin_package=action.plugin_package,
                signals=signals,
            )
        )
    return data


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


async def _get_action_status_async(
    action_instance_id: str, user_id: str
) -> WorkflowExecutionStatus:
    """
    Retrieves the current execution status of an action.

    Args:
        action_instance_id: The unique ID of the action instance to check.
        user_id: The user who initiated the action.

    Returns:
        The current status of the action. If workflow is not found, returns
        TERMINATED.
    """
    action = await _async_action_repository.require_for_user(
        action_instance_id, user_id
    )
    logger = get_logger(__name__)

    status = await _get_workflow_status_safe(action_instance_id)

    if status is None:
        logger.warning(
            f'Workflow {action_instance_id} could not be found for user {user_id}. '
            f'Setting status to TERMINATED.'
        )
        await _async_action_repository.set_status_for_user(
            action_instance_id, user_id, WorkflowExecutionStatus.TERMINATED.name
        )
        return WorkflowExecutionStatus.TERMINATED

    await _async_action_repository.set_status_for_user(
        action_instance_id, user_id, status.name
    )
    return status


def get_action_status(action_instance_id: str, user_id: str) -> WorkflowExecutionStatus:
    """
    Retrieves the current execution status of an action.

    Synchronous callers can call this function directly.
    Asynchronous callers should use ``await get_action_status_async(...)``.
    """
    _sync_action_repository.require_for_user(action_instance_id, user_id)

    logger = get_logger(__name__)
    status = _run_temporal_sync(_get_workflow_status_safe(action_instance_id))
    if status is None:
        logger.warning(
            f'Workflow {action_instance_id} could not be found for user {user_id}. '
            f'Setting status to TERMINATED.'
        )
        _sync_action_repository.set_status_for_user(
            action_instance_id, user_id, WorkflowExecutionStatus.TERMINATED.name
        )
        return WorkflowExecutionStatus.TERMINATED

    _sync_action_repository.set_status_for_user(
        action_instance_id, user_id, status.name
    )
    return status


async def get_action_status_async(
    action_instance_id: str, user_id: str
) -> WorkflowExecutionStatus:
    """
    Async-only variant of ``get_action_status`` for typed async call sites.
    """
    return await _get_action_status_async(action_instance_id, user_id)


def get_action_result(action_instance_id: str, user_id: str) -> dict[str, Any] | None:
    """
    Retrieves the result of a completed action.

    Synchronous callers can call this function directly.
    Asynchronous callers should use ``await get_action_result_async(...)``.
    """
    _sync_action_repository.require_for_user(action_instance_id, user_id)

    logger = get_logger(__name__)
    results = _run_temporal_sync(_get_workflow_result_safe(action_instance_id))
    if results is None:
        logger.warning(
            f'Workflow {action_instance_id} could not be found for user {user_id}. '
            f'Result could not be retrieved.'
        )
        return None

    serialized_results = _to_dict(results)
    _sync_action_repository.save_result_for_user(
        action_instance_id,
        user_id,
        WorkflowExecutionStatus.COMPLETED.name,
        serialized_results,
    )
    return serialized_results


async def get_action_result_async(
    action_instance_id: str, user_id: str
) -> dict[str, Any] | None:
    """
    Retrieves the result of a completed action.
    """
    logger = get_logger(__name__)
    await _async_action_repository.require_for_user(action_instance_id, user_id)

    results = await _get_workflow_result_safe(action_instance_id)

    if results is None:
        logger.warning(
            f'Workflow {action_instance_id} could not be found for user {user_id}. '
            f'Result could not be retrieved.'
        )
        return None

    serialized_results = _to_dict(results)
    await _async_action_repository.save_result_for_user(
        action_instance_id,
        user_id,
        WorkflowExecutionStatus.COMPLETED.name,
        serialized_results,
    )
    return serialized_results


async def _refresh_action_status(action: ActionRecord):
    """
    Update the status of an action in the database.
    Silently handles workflow not found errors by setting status to UNKNOWN.

    Args:
        action: The action document to update.
    """
    status = await _get_workflow_status_safe(action.action_instance_id)
    logger = get_logger(__name__)

    if status is None:
        # Workflow not found - mark as unknown and return
        logger.warning(
            f'Workflow {action.action_instance_id} could not be found. '
            f'Setting status to UNKNOWN.'
        )
        await _async_action_repository.set_status_for_user(
            action.action_instance_id, action.user_id, 'UNKNOWN'
        )
        return

    updates: dict[str, Any] = {'status': str(status.name)}

    if status.name == 'COMPLETED':
        results = await _get_workflow_result_safe(action.action_instance_id)
        if results:
            try:
                updates['results'] = _to_dict(results)
            except TypeError:
                updates['results'] = results

    await _async_action_repository.patch_for_user(
        action.action_instance_id, action.user_id, **updates
    )


_CURSOR_DT_FMT = '%Y-%m-%dT%H:%M:%S.%f+00:00'


def _encode_cursor(dt: datetime) -> str:
    """
    Encode a datetime as an opaque, base64url cursor string.

    The cursor encodes the ``created_at`` timestamp of the *last item on the
    current page*.  The next query will return documents whose ``created_at``
    is strictly less than this value, giving stable forward-only pagination
    even as new documents are inserted at the head of the collection.
    """
    # Beanie/Mongo can return naive datetimes when tz-awareness is disabled;
    # treat those values as UTC to avoid timezone-shifted cursors.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Always work in UTC so the encoded string is unambiguous.
    utc_dt = dt.astimezone(timezone.utc)
    token = utc_dt.strftime(_CURSOR_DT_FMT)
    return base64.urlsafe_b64encode(token.encode()).decode()


def _decode_cursor(cursor: str) -> datetime:
    """
    Decode a cursor string produced by :func:`_encode_cursor`.

    Raises ``ValueError`` when the token is not a valid base64url string or
    does not decode to the expected timestamp format.
    """
    try:
        token = base64.urlsafe_b64decode(cursor.encode()).decode()
        return datetime.strptime(token, _CURSOR_DT_FMT).replace(tzinfo=timezone.utc)
    except Exception as exc:
        raise ValueError(f'Invalid pagination cursor: {cursor!r}') from exc


async def list_user_actions(
    user_id: str,
    page_size: int = 20,
    cursor: str | None = None,
    upload_id: str | None = None,
) -> 'ActionRecordPage':
    """
    Get a page of actions for a given user, ordered by ``created_at`` descending
    (newest first).

    This function also updates the status of any pending or running actions
    within the returned page.

    Args:
        user_id: The ID of the user.
        page_size: Maximum number of items to return (default 20).
        cursor: Opaque pagination token returned by a previous call.  When
            supplied the query returns the next page of results after the
            cursor position.
        upload_id: Optional upload ID to filter actions by.

    Returns:
        An :class:`ActionRecordPage` containing the items, an optional
        ``next_cursor`` for the following page, and the ``total`` count of
        all documents belonging to this user.
    """
    # Decode cursor and narrow the query to documents strictly older than it.
    cursor_dt = None
    if cursor is not None:
        cursor_dt = _decode_cursor(cursor)

    # Fetch one extra document to detect whether a next page exists.
    fetch_limit = page_size + 1
    action_documents, _ = await _async_action_repository.list_for_user(
        user_id=user_id,
        page_size=fetch_limit,
        upload_id=upload_id,
        created_before=cursor_dt,
    )

    has_next = len(action_documents) == fetch_limit
    page_docs = action_documents[:page_size]

    # Update status only for PENDING/RUNNING items in this page.
    active_actions = [a for a in page_docs if a.status in ('PENDING', 'RUNNING')]
    if active_actions:
        await asyncio.gather(*(_refresh_action_status(a) for a in active_actions))

    next_cursor: str | None = None
    if has_next and page_docs:
        next_cursor = _encode_cursor(page_docs[-1].created_at)

    # Cheap total count (uses the (user_id, created_at) compound index).
    total = await _async_action_repository.count_for_user(user_id, upload_id)

    return ActionRecordPage(
        items=[
            ActionSummaryRecord.model_construct(**doc.model_dump()) for doc in page_docs
        ],
        next_cursor=next_cursor,
        total=total,
    )


async def get_user_action(action_instance_id: str, user_id: str) -> ActionRecord | None:
    """
    Get a specific action for a given user.

    This function also updates the status of the action if it's pending or running.

    Args:
        action_instance_id: The ID of the action instance.
        user_id: The ID of the user.

    Returns:
        The action if found, otherwise None.
    """
    action_document = await _async_action_repository.get_for_user(
        action_instance_id, user_id
    )

    if not action_document:
        return None

    if action_document.status in ('PENDING', 'RUNNING'):
        await _refresh_action_status(action_document)
    elif action_document.status == 'COMPLETED' and not action_document.results:
        # Backfill results for completed rows where results were not persisted yet.
        results = await _get_workflow_result_safe(action_instance_id)
        if results is not None:
            try:
                serialized_results = _to_dict(results)
            except TypeError:
                serialized_results = results
            action_document = await _async_action_repository.save_result_for_user(
                action_instance_id,
                user_id,
                action_document.status,
                serialized_results,
            )

    return ActionRecord.model_validate(action_document)


def get_upload_files(
    upload_id: str, user_id: str
) -> 'StagingUploadFiles | PublicUploadFiles':
    """
    NOTE: This function is deprecated. Import `get_upload_files` from nomad.uploads
    instead.

    Retrieves files for an upload after verifying user authorization.

    Checks if the user is the main author or a coauthor.

    Args:
        upload_id: The unique identifier for the upload.
        user_id: The unique identifier for the user.

    Returns:
        The UploadFiles object if found and authorized.
    """
    warnings.warn(
        '`nomad.actions.manager.get_upload_files` is deprecated; '
        'import `get_upload_files` from `nomad.uploads` instead.',
        DeprecationWarning,
        stacklevel=2,
    )

    from nomad.uploads import get_upload_files as nomad_get_upload_files

    upload_files = nomad_get_upload_files(upload_id, user_id)

    return upload_files


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
    """
    Returns the file path for the logs of a given action instance.
    Logs are stored in
    config.fs.actions/<action_instance_id>/nomad_system/logs/<action_instance_id>.log.
    For backwards compatibility, when the new file does not exist yet but the
    legacy config.fs.actions/logs/<action_instance_id>.log exists, the legacy
    path is returned.
    """
    log_filename = f'{action_instance_id}.log'

    new_log_dir = _action_instance_dir(
        action_instance_id, ACTION_INSTANCE_NOMAD_SYSTEM_DIRNAME, 'logs'
    )
    new_log_path = os.path.join(new_log_dir, log_filename)

    legacy_log_dir = _ensure_dir(os.path.join(config.fs.actions, 'logs'))
    legacy_log_path = os.path.join(legacy_log_dir, log_filename)

    if not os.path.exists(new_log_path) and os.path.exists(legacy_log_path):
        return legacy_log_path

    return new_log_path


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


async def _start_action_async(action_id: str, data: Any) -> str:
    """
    Starts a new Action with the given ID and input data.

    Args:
        action_id: The ID of the action to start.
        data: Input data for the action.

    Returns:
        The unique ID of the started action instance.
    """
    assert hasattr(data, 'user_id')
    user_id = data.user_id
    workflow_id = f'{action_id}-{user_id}-{uuid.uuid4()}'
    action_entry_point = get_actions().get(action_id)
    assert action_entry_point, f'No action data for the given {action_id} ID'
    action = action_entry_point.load()
    priority = Priority(
        priority_key=action_entry_point.priority_key,
        fairness_key=user_id
        if action_entry_point.priority_fairness_key == 'user_id'
        else None,
    )

    rollback_items = []
    try:
        asset_refs = extract_action_asset_refs(data)
        if asset_refs:
            rollback_items = await consume_staged_assets(
                refs=asset_refs,
                user_id=user_id,
                purpose=ActionAssetPurpose.ACTION_START,
                target_action_instance_id=workflow_id,
                action_id=action_id,
            )

        upload_id = getattr(data, 'upload_id', None)
        new_action = ActionRecord(
            action_id=action_id,
            action_instance_id=workflow_id,
            user_id=user_id,
            upload_id=upload_id,
            status='PENDING',
            input_data=_to_dict(data),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            priority_fairness_key=action_entry_point.priority_fairness_key,
            priority_key=action_entry_point.priority_key,
        )
        await _async_action_repository.create(new_action)
        await _async_start_workflow(
            action=action, data=data, workflow_id=workflow_id, priority=priority
        )
    except Exception:
        if rollback_items:
            await rollback_consumed_assets(rollback_items)
        raise
    return workflow_id


def start_action(action_id: str, data: Any) -> str:
    """
    Starts a new Action with the given ID and input data.

    Synchronous callers can call this function directly.
    Asynchronous callers should use ``await start_action_async(...)``.
    """
    assert hasattr(data, 'user_id')
    user_id = data.user_id
    workflow_id = f'{action_id}-{user_id}-{uuid.uuid4()}'
    action_entry_point = get_actions().get(action_id)
    assert action_entry_point, f'No action data for the given {action_id} ID'
    action = action_entry_point.load()
    if extract_action_asset_refs(data):
        raise ValueError(
            'ActionAssetRef inputs are not supported from ELNs. '
            'Use the new Action form in the GUI to create an action.'
        )

    priority = Priority(
        priority_key=action_entry_point.priority_key,
        fairness_key=user_id
        if action_entry_point.priority_fairness_key == 'user_id'
        else None,
    )

    record = ActionRecord(
        action_id=action_id,
        action_instance_id=workflow_id,
        user_id=user_id,
        upload_id=getattr(data, 'upload_id', None),
        status='PENDING',
        input_data=_to_dict(data),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        priority_fairness_key=action_entry_point.priority_fairness_key,
        priority_key=action_entry_point.priority_key,
    )
    _sync_action_repository.create(record)
    _run_temporal_sync(_async_start_workflow(action, data, workflow_id, priority))
    return workflow_id


async def start_action_async(action_id: str, data: Any) -> str:
    """
    Async-only variant of ``start_action`` for typed async call sites.
    """
    return await _start_action_async(action_id, data)


async def _stop_action_async(action_instance_id: str, user_id: str):
    """
    Stops a running action.

    Args:
        action_instance_id: The unique ID of the action instance to stop.
        user_id: The user who initiated the action.
    """
    action = await _async_action_repository.require_for_user(
        action_instance_id, user_id
    )

    if action.status not in ('PENDING', 'RUNNING'):
        raise Exception('Action is not running.')

    await _async_stop_workflow(action_instance_id)

    await _async_action_repository.set_status_for_user(
        action_instance_id, user_id, WorkflowExecutionStatus.CANCELED.name
    )


def stop_action(action_instance_id: str, user_id: str):
    """
    Stops a running action.

    Synchronous callers can call this function directly.
    Asynchronous callers should use ``await stop_action_async(...)``.
    """
    action = _sync_action_repository.require_for_user(action_instance_id, user_id)

    if action.status not in ('PENDING', 'RUNNING'):
        raise Exception('Action is not running.')

    _run_temporal_sync(_async_stop_workflow(action_instance_id))
    _sync_action_repository.set_status_for_user(
        action_instance_id, user_id, WorkflowExecutionStatus.CANCELED.name
    )


async def stop_action_async(action_instance_id: str, user_id: str):
    """
    Async-only variant of ``stop_action`` for typed async call sites.
    """
    return await _stop_action_async(action_instance_id, user_id)


@activity.defn
async def request_signal_input_activity(data: RequestSignalInputActivityInput):
    """
    Activity that interacts with the main nomad API state to record that
    this workflow is waiting on a signal input signal.
    """
    request_info: dict[str, Any] = {
        'signal_fn_name': data.signal_fn_name,
    }
    if data.title is not None:
        request_info['title'] = data.title
    if data.description is not None:
        request_info['description'] = data.description
    if data.content is not None:
        request_info['content'] = data.content
    if data.initial_data is not None:
        request_info['initial_data'] = data.initial_data

    created = await _async_action_repository.add_pending_signal_input(
        action_instance_id=data.action_instance_id,
        user_id=data.user_id,
        signal_fn_name=data.signal_fn_name,
        request_info=request_info,
    )
    if created:
        return {'status': 'signal_input_requested'}

    action = await _async_action_repository.require_for_user(
        data.action_instance_id, data.user_id
    )
    if action.status not in ('PENDING', 'RUNNING'):
        raise Exception('Action is not running.')
    raise Exception(
        f"Action already has a pending signal input request for signal '{data.signal_fn_name}'."
    )


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


async def submit_signal_input(
    action_instance_id: str, user_id: str, signal_fn_name: str, data: Any
):
    """
    Submit signal input to a running action by signaling its workflow.

    Validates that the action exists for the user and is active, resolves the
    associated Temporal workflow class from the action registry, and sends the
    specified signal with the provided payload to the workflow execution.

    Args:
        action_instance_id: Unique identifier of the action instance (also used
            as the workflow ID).
        user_id: Identifier of the user who owns the action instance.
        signal_fn_name: Name of the workflow signal to invoke.
        data: Payload to send with the signal. Must be serializable by the
            Temporal payload converter.

    Raises:
        Exception: If the action does not exist for the user or is not active.
        AssertionError: If no workflow entry point is registered for the action.
        temporalio.exceptions.TemporalError: If signaling the workflow fails.
    """
    action_data = await _async_action_repository.consume_pending_signal_input(
        action_instance_id=action_instance_id,
        user_id=user_id,
        signal_fn_name=signal_fn_name,
    )
    if not action_data:
        action = await _async_action_repository.require_for_user(
            action_instance_id, user_id
        )
        if action.status not in ('PENDING', 'RUNNING'):
            raise Exception('Action is not running.')
        raise Exception(
            f"No pending signal input request found for signal '{signal_fn_name}'."
        )

    pending_requests = action_data.get('signal_input_requests') or []
    matching_request = next(
        (
            req
            for req in pending_requests
            if req.get('signal_fn_name') == signal_fn_name
        ),
        None,
    )
    if not matching_request:
        raise Exception(
            f"No pending signal input request found for signal '{signal_fn_name}'."
        )

    rollback_items = []
    try:
        action_id = action_data.get('action_id')
        if action_id is None:
            raise Exception(
                'The action was not registered in the DB or was registered under a different user.'
            )
        action_entry_point = get_actions().get(action_id)
        assert action_entry_point, f'No action data for the given {action_id} ID'
        workflow_cls = action_entry_point.load().workflow
        asset_refs = extract_action_asset_refs(data)
        if asset_refs:
            rollback_items = await consume_staged_assets(
                refs=asset_refs,
                user_id=user_id,
                purpose=ActionAssetPurpose.ACTION_SIGNAL,
                target_action_instance_id=action_instance_id,
                signal_fn_name=signal_fn_name,
            )
        await _async_signal_workflow(
            workflow_cls,
            workflow_id=action_instance_id,
            signal_fn_name=signal_fn_name,
            data=data,
        )
    except Exception:
        if rollback_items:
            await rollback_consumed_assets(rollback_items)
        # Best-effort rollback of the pending request if asset handling or
        # signaling fails.
        await _async_action_repository.restore_pending_signal_input(
            action_instance_id=action_instance_id,
            user_id=user_id,
            signal_fn_name=signal_fn_name,
            request_info=matching_request,
        )
        raise

    # Save the submitted signal data in db.
    serialized_data: Any
    try:
        serialized_data = _to_dict(data)
    except TypeError:
        serialized_data = (
            str(data)
            if not isinstance(data, (int, float, bool, str, list, dict, type(None)))
            else data
        )

    submitted_entry: dict[str, Any] = {
        'signal_fn_name': signal_fn_name,
        'data': serialized_data,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    if matching_request.get('title') is not None:
        submitted_entry['title'] = matching_request.get('title')
    if matching_request.get('description') is not None:
        submitted_entry['description'] = matching_request.get('description')
    if matching_request.get('content') is not None:
        submitted_entry['content'] = matching_request.get('content')

    await _async_action_repository.append_submitted_signal_input(
        action_instance_id=action_instance_id,
        user_id=user_id,
        submitted_entry=submitted_entry,
    )
