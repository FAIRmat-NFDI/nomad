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

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from nomad.common import now
from nomad.models.common import UTCDateTime

__all__ = [
    'ActionRecord',
    'ActionSummaryRecord',
    'ActionRecordPage',
    'ActionSchemaInfo',
    'ActionStreamEventSeverity',
    'ActionStreamEventType',
    'ActionStreamEvent',
    'ActionStreamItem',
    'RequestSignalInputActivityInput',
]


class ActionStreamEventType(str, Enum):
    """Generic event categories that action UIs can handle consistently."""

    STATE = 'state'
    MESSAGE = 'message'
    OUTPUT_DELTA = 'output_delta'


class ActionStreamEventSeverity(str, Enum):
    """User-facing severity for action stream events."""

    INFO = 'info'
    SUCCESS = 'success'
    WARNING = 'warning'
    ERROR = 'error'


class ActionStreamEvent(BaseModel):
    """Structured event that plugin workflows and activities can stream to clients."""

    type: ActionStreamEventType = Field(
        ..., description='Generic event category for frontend rendering.'
    )
    name: str | None = Field(
        default=None,
        description='Optional plugin-specific event name, e.g. search_started.',
    )
    message: str | None = Field(
        default=None, description='Short user-facing event message.'
    )
    progress: float | None = Field(
        default=None, description='Optional progress percentage from 0 to 100.'
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description='Optional structured data for event-specific UI rendering.',
    )
    severity: ActionStreamEventSeverity = Field(
        default=ActionStreamEventSeverity.INFO,
        description='User-facing event severity.',
    )
    terminal: bool = Field(
        default=False, description='True when this event marks the stream terminal.'
    )
    timestamp: datetime = Field(
        default_factory=now,
        description='Event creation timestamp.',
    )


class ActionStreamItem(BaseModel):
    """A stream event paired with its durable Temporal stream offset."""

    offset: int
    topic: str
    event: ActionStreamEvent


class ActionRecord(BaseModel):
    """API response DTO; repository records live in ``nomad.actions.domain``."""

    action_id: str
    action_instance_id: str
    user_id: str
    upload_id: str | None = None
    status: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    signal_input_requests: list[dict[str, Any]] = Field(default_factory=list)
    signal_inputs_submitted: list[dict[str, Any]] = Field(default_factory=list)
    results: Any = None
    created_at: UTCDateTime
    updated_at: UTCDateTime
    priority_key: int | None = None
    priority_fairness_key: Literal['user_id'] | None = None


class ActionSummaryRecord(BaseModel):
    action_id: str
    action_instance_id: str
    upload_id: str | None = None
    signal_input_requests: list[dict[str, str | None]] = Field(default_factory=list)
    status: str
    created_at: UTCDateTime
    updated_at: UTCDateTime


class ActionRecordPage(BaseModel):
    """Paginated response for action list queries."""

    items: list[ActionSummaryRecord]
    next_cursor: str | None = None
    total: int


class ActionSchemaInfo(BaseModel):
    action_id: str
    json_schema: dict[str, Any]
    name: str | None = None
    plugin_package: str | None = None
    description: str | None = None
    task_queue: str | None = None
    groups: list[str] | None = None
    users: list[str] | None = None
    signals: list[dict[str, Any]] | None = None


class RequestSignalInputActivityInput(BaseModel):
    """Input parameters for the activity that delegates to request_signal_input."""

    action_instance_id: str = Field(
        ..., description='The ID of the action instance/workflow.'
    )
    user_id: str = Field(..., description='The ID of the user who owns the action.')
    signal_fn_name: str = Field(
        ..., description='The Temporal signal name to add to pending requests.'
    )
    title: str | None = Field(
        default=None, description='Optional title to use for the signal input form.'
    )
    description: str | None = Field(
        default=None, description='Optional description for the signal input form.'
    )
    content: str | None = Field(
        default=None, description='Optional markdown content for the signal input form.'
    )
    initial_data: dict[str, Any] | None = Field(
        default=None, description='Optional initial data for the signal input form.'
    )
