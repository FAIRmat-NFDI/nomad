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

"""Pure domain rules for action instances.

This module must not import FastAPI, Temporal, MongoDB, or other infrastructure.
Domain records are separate from MongoDB documents and API request/response
schemas.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ActionStatus(str, Enum):
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    COMPLETED = 'COMPLETED'
    FAILED = 'FAILED'
    CANCELED = 'CANCELED'
    TERMINATED = 'TERMINATED'
    TIMED_OUT = 'TIMED_OUT'
    UNKNOWN = 'UNKNOWN'
    CONTINUED_AS_NEW = 'CONTINUED_AS_NEW'


_ACTIVE_STATUSES = frozenset({ActionStatus.PENDING.value, ActionStatus.RUNNING.value})


class ActionNotFoundError(Exception):
    """Raised when an action is absent or is not owned by the requesting user."""


@dataclass(frozen=True)
class ActionDefinition:
    action_id: str
    priority_key: int | None = None
    priority_fairness_key: str | None = None


@dataclass(frozen=True)
class SignalInputClaim:
    """An atomically claimed pending request, without database metadata."""

    action_id: str
    request: dict[str, Any]


class SignalInputNotFoundError(Exception):
    """No matching pending signal request exists."""


class ActionNotRunningError(Exception):
    """Raised when an operation requires an active action."""


def is_active_status(status: str) -> bool:
    """Return whether an action status permits active-action operations."""
    return status in _ACTIVE_STATUSES


def require_active_status(status: str) -> None:
    """Enforce the invariant that only pending/running actions can be stopped."""
    if not is_active_status(status):
        raise ActionNotRunningError('Action is not running.')


class ActionRecord(BaseModel):
    action_id: str
    action_instance_id: str
    user_id: str
    upload_id: str | None = None
    status: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    signal_input_requests: list[dict[str, Any]] = Field(default_factory=list)
    signal_inputs_submitted: list[dict[str, Any]] = Field(default_factory=list)
    results: Any = None
    created_at: datetime
    updated_at: datetime
    priority_key: int | None = None
    priority_fairness_key: Literal['user_id'] | None = None
