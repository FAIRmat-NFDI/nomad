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

import asyncio
import functools
import os
from enum import Enum
from typing import Annotated, Any, Final, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi_cache.decorator import cache
from pydantic import BaseModel

from nomad.actions.action import get_actions
from nomad.actions.assets.models import ActionAssetPurpose, ActionAssetUploadResult
from nomad.actions.assets.service import clone_action_asset, upload_action_asset
from nomad.actions.bootstrap import action_service
from nomad.actions.domain import (
    ActionNotFoundError,
    ActionNotRunningError,
    SignalInputNotFoundError,
)
from nomad.actions.manager import action_log_file_path
from nomad.actions.models import (
    ActionRecord,
    ActionRecordPage,
    ActionSchemaInfo,
    ActionStreamEvent,
    ActionStreamEventType,
)
from nomad.actions.plugin_adapter import get_all_action_schemas, validate_action_arg
from nomad.actions.streams import (
    ActionStreamUnavailable,
    stream_action_events_for_user_async,
    stream_processing_events_for_user_async,
)
from nomad.app.v1.models import User
from nomad.app.v1.routers.auth import get_current_user
from nomad.auth.scopes import Scope
from nomad.mongo.groups import MongoUserGroup
from nomad.utils import strip

from ..models import HTTPExceptionModel
from ..utils import create_responses

router = APIRouter()


class APITag(str, Enum):
    DEFAULT = 'actions'


class ActionStart(BaseModel):
    data: dict


class ActionSignalInput(BaseModel):
    signal_fn_name: str
    data: Any


class ActionAssetCloneRequest(BaseModel):
    purpose: ActionAssetPurpose
    action_id: str | None = None
    action_instance_id: str | None = None
    signal_fn_name: str | None = None
    source_action_instance_id: str | None = None


def _format_sse_event(
    data: str,
    *,
    event: str | None = None,
    event_id: int | None = None,
) -> str:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f'id: {event_id}')
    if event:
        event_name = event.replace('\n', ' ').replace('\r', ' ')
        lines.append(f'event: {event_name}')
    lines.extend(f'data: {line}' for line in data.splitlines() or [''])
    return '\n'.join(lines) + '\n\n'


SCHEMA_CACHE_TTL: Final[int] = 1 * 24 * 60 * 60  # 1 day in seconds


@functools.lru_cache(maxsize=1024)
def _count_total_lines_cached(
    log_file: str, _file_size: int, _file_mtime_ns: int
) -> int:
    """Count lines in a file. Extra params are used only as cache-busting keys."""
    line_count = 0
    last_byte = b''
    with open(log_file, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            line_count += chunk.count(b'\n')
            last_byte = chunk[-1:]
    # Count a trailing line without a newline
    if last_byte and last_byte != b'\n':
        line_count += 1
    return line_count


def _count_total_lines(log_file: str) -> int:
    stat = os.stat(log_file)
    return _count_total_lines_cached(log_file, stat.st_size, stat.st_mtime_ns)


def _count_lines_before_offset(log_file: str, offset: int) -> int:
    if offset <= 0:
        return 0

    line_count = 0
    remaining = offset
    with open(log_file, 'rb') as f:
        while remaining > 0:
            chunk = f.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            line_count += chunk.count(b'\n')
            remaining -= len(chunk)
    return line_count


@router.post(
    '/assets/upload',
    tags=[APITag.DEFAULT],
    summary='Upload an action asset',
    response_model=ActionAssetUploadResult,
)
async def action_asset_upload(
    file: Annotated[UploadFile, File(...)],
    purpose: Annotated[ActionAssetPurpose, Form(...)],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ACTIONS_RUN], allow_anonymous=False)),
    ],
    action_id: Annotated[str | None, Form()] = None,
    action_instance_id: Annotated[str | None, Form()] = None,
    signal_fn_name: Annotated[str | None, Form()] = None,
    expected_media_type: Annotated[str | None, Form()] = None,
    expected_sha256: Annotated[str | None, Form()] = None,
):
    try:
        return await upload_action_asset(
            user_id=user.user_id,
            upload_file=file,
            purpose=purpose,
            action_id=action_id,
            action_instance_id=action_instance_id,
            signal_fn_name=signal_fn_name,
            expected_media_type=expected_media_type,
            expected_sha256=expected_sha256,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    '/assets/{filename}/clone',
    tags=[APITag.DEFAULT],
    summary='Clone an action asset into a new staged asset',
    response_model=ActionAssetUploadResult,
)
async def action_asset_clone(
    filename: str,
    clone_data: ActionAssetCloneRequest,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ACTIONS_RUN], allow_anonymous=False)),
    ],
):
    try:
        return await clone_action_asset(
            user_id=user.user_id,
            source_filename=filename,
            purpose=clone_data.purpose,
            action_id=clone_data.action_id,
            action_instance_id=clone_data.action_instance_id,
            signal_fn_name=clone_data.signal_fn_name,
            source_action_instance_id=clone_data.source_action_instance_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    '/{action_id}/start',
    tags=[APITag.DEFAULT],
    summary='Start an action',
    description='Starts a new action with the given ID and input data.',
)
async def action_start(
    action_id: str,
    start_data: ActionStart,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ACTIONS_RUN], allow_anonymous=False)),
    ],
):
    """
    Starts a new action.

    Args:
        action_id: The ID of the action to start.
        start_data: The input data for the action.
        user: The authenticated user.

    Returns:
        The ID of the started action instance.
    """
    action_entry_point = get_actions().get(action_id)
    if not action_entry_point:
        raise HTTPException(status_code=404, detail='Action not found.')

    if action_entry_point.users or action_entry_point.groups:
        is_authorized = False
        if action_entry_point.users and user.user_id in action_entry_point.users:
            is_authorized = True
        if not is_authorized and action_entry_point.groups:
            user_groups = await asyncio.to_thread(
                lambda: MongoUserGroup.get_ids_by_user_id(user.user_id)
            )
            if any(group in action_entry_point.groups for group in user_groups):
                is_authorized = True

        if not is_authorized:
            raise HTTPException(
                status_code=403, detail='User is not authorized to execute this action.'
            )

    start_data.data['user_id'] = user.user_id
    try:
        input_data = validate_action_arg(action_id, start_data.data)
        action_instance_id = await action_service.a_start(
            action_id=action_id, data=input_data
        )
        return {'action_instance_id': action_instance_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    '/{action_instance_id}/stop',
    tags=[APITag.DEFAULT],
    summary='Stop an action',
    description='Stops a running action instance.',
)
async def action_stop(
    action_instance_id: str,
    user: Annotated[
        User,
        Depends(
            get_current_user([Scope.ACTIONS_RUN], allow_anonymous=False),
        ),
    ],
):
    """
    Stops an action.

    Args:
        action_instance_id: The ID of the action instance to stop.
        user: The authenticated user.
    """
    try:
        await action_service.a_stop(
            action_instance_id=action_instance_id, user_id=user.user_id
        )
        return {'status': 'stopped'}
    except ActionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ActionNotRunningError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    '/{action_instance_id}/signal-input',
    tags=[APITag.DEFAULT],
    summary='Submit signal input',
    description='Submits signal input to a running action instance.',
)
async def action_signal_input(
    action_instance_id: str,
    signal_input_data: ActionSignalInput,
    user: Annotated[
        User,
        Depends(
            get_current_user([Scope.ACTIONS_RUN], allow_anonymous=False),
        ),
    ],
):
    """
    Submits signal input to a running action.

    Args:
        action_instance_id: The ID of the action instance to submit input to.
        signal_input_data: The input data including signal name and payload.
        user: The authenticated user.
    """
    try:
        await action_service.a_submit_signal_input(
            action_instance_id=action_instance_id,
            user_id=user.user_id,
            signal_fn_name=signal_input_data.signal_fn_name,
            data=signal_input_data.data,
        )
        return {'status': 'signal_input_submitted'}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (ActionNotFoundError, SignalInputNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ActionNotRunningError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    '/{action_instance_id}/status',
    tags=[APITag.DEFAULT],
    summary='Get action status',
    description='Retrieves the current status of a specific action instance.',
)
async def action_status(
    action_instance_id: str,
    user: Annotated[
        User,
        Depends(
            get_current_user([Scope.ACTIONS_READ], allow_anonymous=False),
        ),
    ],
):
    """
    Gets the status of an action.

    Args:
        action_instance_id: The ID of the action instance.
        user: The authenticated user.

    Returns:
        The status of the action.
    """
    try:
        status = await action_service.a_get_status(
            action_instance_id=action_instance_id, user_id=user.user_id
        )
        if status is None:
            return {'status': 'UNKNOWN'}
        return {'status': status.name}
    except ActionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    '/{action_instance_id}/result',
    tags=[APITag.DEFAULT],
    summary='Get action result',
    description='Retrieves the result of a specific action instance.',
)
async def action_result(
    action_instance_id: str,
    user: Annotated[
        User,
        Depends(
            get_current_user([Scope.ACTIONS_READ], allow_anonymous=False),
        ),
    ],
):
    """
    Gets the result of an action.

    Args:
        action_instance_id: The ID of the action instance.
        user: The authenticated user.

    Returns:
        The result of the action.
    """
    try:
        result = await action_service.a_get_result(
            action_instance_id=action_instance_id, user_id=user.user_id
        )
        return result
    except ActionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    '/schemas',
    tags=[APITag.DEFAULT],
    response_model=list[ActionSchemaInfo],
    summary='Get action schemas',
    description='Retrieves the input schemas for all available actions.',
)
@cache(expire=SCHEMA_CACHE_TTL)
async def action_input_schemas(
    _user: Annotated[
        User,
        Depends(
            get_current_user([Scope.ACTIONS_READ], allow_anonymous=False),
        ),
    ],
):
    """
    Gets the input schemas for all available actions.

    Args:
        user: The authenticated user.

    Returns:
        A list of action input schemas.
    """
    try:
        result = get_all_action_schemas()
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


_not_authorized = (
    status.HTTP_401_UNAUTHORIZED,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Unauthorized. Authorization is required, but no or bad authentication credentials provided."""
        ),
    },
)


@router.get(
    '/{action_instance_id}',
    tags=[APITag.DEFAULT],
    summary='Get a specific action of the authenticated user.',
    response_model=ActionRecord,
    responses=create_responses(_not_authorized),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
async def action(
    action_instance_id: str,
    user: Annotated[
        User,
        Depends(
            get_current_user([Scope.ACTIONS_READ], allow_anonymous=False),
        ),
    ],
):
    """
    Gets a specific action for the authenticated user.

    Args:
        action_instance_id: The ID of the action instance.
        user: The authenticated user.

    Returns:
        The action.
    """
    try:
        result = await action_service.a_get_owned(
            action_instance_id=action_instance_id, user_id=user.user_id
        )
        if result is None:
            raise HTTPException(status_code=404, detail='Action not found.')
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def stream_logs(
    log_file: str,
    action_instance_id: str,
    user_id: str,
    first_line: int,
    offset_lines: int | None = None,
):
    with open(log_file) as f:
        if offset_lines is None:
            f.seek(0, os.SEEK_END)
        else:
            line_number = 1
            while line_number < first_line:
                if not f.readline():
                    break
                line_number += 1

        while True:
            line = await asyncio.to_thread(f.readline)

            if line:
                yield line
            else:
                # check if workflow status is running/pending, otherwise break
                try:
                    status = await action_service.a_get_status(
                        action_instance_id=action_instance_id,
                        user_id=user_id,
                    )
                    if status.name not in ('PENDING', 'RUNNING'):
                        break
                except Exception:
                    break

                await asyncio.sleep(1)


async def stream_sse_events(
    event_items,
    *,
    topic: Literal['action', 'processing'],
    action_instance_id: str | None = None,
    user_id: str | None = None,
):
    async for item in event_items:
        yield _format_sse_event(
            item.event.model_dump_json(),
            event=item.event.type,
            event_id=item.offset,
        )

    # Only action streams expose action status that we can append as a terminal event.
    if topic != 'action' or action_instance_id is None or user_id is None:
        return

    try:
        action_status = await action_service.a_get_status(
            action_instance_id=action_instance_id,
            user_id=user_id,
        )
    except Exception:
        return

    terminal_event = ActionStreamEvent(
        type=ActionStreamEventType.STATE,
        name='workflow_status',
        message=f'Action workflow reached {action_status.name}.',
        data={'status': action_status.name},
        terminal=True,
    )
    yield _format_sse_event(
        terminal_event.model_dump_json(),
        event=terminal_event.type,
    )


@router.get(
    '/{action_instance_id}/events',
    tags=[APITag.DEFAULT],
    summary='Stream workflow events',
    description='Subscribes to opt-in Workflow Streams events for action or processing topics.',
    responses=create_responses(_not_authorized),
)
async def action_events(
    action_instance_id: str,
    user: Annotated[
        User,
        Depends(
            get_current_user([Scope.ACTIONS_READ], allow_anonymous=False),
        ),
    ],
    from_offset: Annotated[
        int | None,
        Query(
            ge=0,
            description=(
                'Temporal stream offset to start from. If omitted, Last-Event-ID '
                'is used when present, otherwise streaming starts at offset 0.'
            ),
        ),
    ] = None,
    last_event_id: Annotated[str | None, Header(alias='Last-Event-ID')] = None,
    topic: Annotated[
        Literal['action', 'processing'],
        Query(description='Event topic to subscribe to.'),
    ] = 'action',
):
    """Streams opt-in structured workflow events as Server-Sent Events."""

    start_offset = from_offset
    if start_offset is None:
        start_offset = 0
        if last_event_id:
            try:
                start_offset = int(last_event_id) + 1
            except ValueError:
                raise HTTPException(
                    status_code=400, detail='Invalid Last-Event-ID header.'
                )

    try:
        if topic == 'action':
            event_items = await stream_action_events_for_user_async(
                action_instance_id=action_instance_id,
                user_id=user.user_id,
                from_offset=start_offset,
            )
        else:
            event_items = await stream_processing_events_for_user_async(
                upload_id=action_instance_id,
                user=user,
                from_offset=start_offset,
            )
    except HTTPException:
        raise
    except ActionStreamUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ActionNotFoundError as e:
        raise HTTPException(status_code=404, detail='Action not found.') from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return StreamingResponse(
        stream_sse_events(
            event_items,
            topic=topic,
            action_instance_id=action_instance_id,
            user_id=user.user_id,
        ),
        media_type='text/event-stream',
        headers={'X-Workflow-Event-First-Offset': str(start_offset)},
    )


@router.get(
    '/{action_instance_id}/logs',
    tags=[APITag.DEFAULT],
    summary='Get action logs',
    description='Retrieves the logs for a specific action instance as a plain text file or stream.',
    responses=create_responses(_not_authorized),
)
async def action_logs(
    action_instance_id: str,
    user: Annotated[
        User,
        Depends(
            get_current_user([Scope.ACTIONS_READ], allow_anonymous=False),
        ),
    ],
    stream: bool = False,
    offset_lines: Annotated[
        int | None,
        Query(
            description='Line offset for log retrieval. Negative values tail from EOF.'
        ),
    ] = None,
):
    """
    Gets the logs of an action instance.

    Args:
        action_instance_id: The ID of the action instance.
        user: The authenticated user.
        stream: Whether to stream the logs as SSE.
        offset_lines: Start line offset when stream is True. Non-negative values are
            absolute from the beginning of the file. Negative values are relative to
            file end.

    Returns:
        A FileResponse streaming the log file, or StreamingResponse if stream is True.
    """
    try:
        # First check if the user has access to this action.
        result = await action_service.a_get_owned(
            action_instance_id=action_instance_id,
            user_id=user.user_id,
        )
        if result is None:
            raise HTTPException(status_code=404, detail='Action not found.')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    MAX_LOG_SIZE = 2 * 1024 * 1024  # 2MB
    log_file = action_log_file_path(action_instance_id)
    if not os.path.exists(log_file):
        raise HTTPException(
            status_code=404, detail='Log file not found for this action.'
        )

    file_size = os.path.getsize(log_file)
    if stream:

        def _prepare_stream_metadata() -> int:
            total_lines = _count_total_lines(log_file)
            return (
                total_lines + 1
                if offset_lines is None
                else (
                    max(1, total_lines + offset_lines + 1)
                    if offset_lines < 0
                    else min(offset_lines + 1, total_lines + 1)
                )
            )

        first_line = await asyncio.to_thread(_prepare_stream_metadata)
        return StreamingResponse(
            stream_logs(
                log_file,
                action_instance_id,
                user.user_id,
                first_line=first_line,
                offset_lines=offset_lines,
            ),
            media_type='text/event-stream',
            headers={'X-Log-First-Line': str(first_line)},
        )

    if file_size <= MAX_LOG_SIZE:
        return FileResponse(
            log_file,
            media_type='text/plain',
            headers={'X-Log-First-Line': '1'},
        )

    def _read_truncated_content() -> tuple[bytes, int]:
        start_offset = max(file_size - MAX_LOG_SIZE, 0)
        lines_before = _count_lines_before_offset(log_file, start_offset)
        first_line = lines_before + 1
        with open(log_file, 'rb') as f:
            f.seek(start_offset)
            return f.read(), first_line

    truncated_content, first_line = await asyncio.to_thread(_read_truncated_content)

    return Response(
        content=truncated_content,
        media_type='text/plain',
        headers={'X-Log-First-Line': str(first_line)},
    )


@router.get(
    '',
    tags=[APITag.DEFAULT],
    summary='List actions of the authenticated user (paginated)',
    description=(
        'Retrieves a paginated list of action instances initiated by the authenticated user. '
        'Results are ordered by creation time, newest first. '
        'Pass the returned ``next_cursor`` value as the ``cursor`` query parameter to '
        'fetch the next page.'
    ),
    response_model=ActionRecordPage,
    responses=create_responses(_not_authorized),
    response_model_exclude_unset=True,
    response_model_exclude_none=False,
)
async def actions(
    user: Annotated[
        User,
        Depends(
            get_current_user([Scope.ACTIONS_READ], allow_anonymous=False),
        ),
    ],
    page_size: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description='Number of action instances to return per page (1-100, default 20).',
        ),
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(
            description=(
                'Opaque pagination cursor returned by the previous response as ``next_cursor``. '
                'Omit to start from the first (newest) page.'
            ),
        ),
    ] = None,
    upload_id: Annotated[
        str | None,
        Query(
            description='Optional upload ID to filter actions by.',
        ),
    ] = None,
):
    """
    Lists actions for the authenticated user with cursor-based pagination.

    Args:
        user: The authenticated user.
        page_size: Maximum number of items per page.
        cursor: Opaque pagination token from a previous response.
        upload_id: Optional upload ID to filter actions by.

    Returns:
        An ActionRecordPage with items, optional next_cursor, and total count.
    """
    try:
        result = await action_service.a_list_owned(
            user_id=user.user_id,
            page_size=page_size,
            cursor=cursor,
            upload_id=upload_id,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
