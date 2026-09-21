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

"""Application-facing ports for action persistence."""

from datetime import datetime
from typing import Any, Protocol

from nomad.actions.domain import (
    ActionDefinition,
    ActionRecord,
    ActionStatus,
    SignalInputClaim,
)


class AsyncActionRepository(Protocol):
    async def create(self, record: ActionRecord) -> ActionRecord: ...

    async def get_for_user(
        self, action_instance_id: str, user_id: str
    ) -> ActionRecord | None: ...

    async def require_for_user(
        self, action_instance_id: str, user_id: str
    ) -> ActionRecord: ...

    async def list_for_user(
        self,
        user_id: str,
        page_size: int,
        upload_id: str | None = None,
        created_before: datetime | None = None,
    ) -> tuple[list[ActionRecord], int]: ...

    async def count_for_user(
        self, user_id: str, upload_id: str | None = None
    ) -> int: ...

    async def set_status_for_user(
        self, action_instance_id: str, user_id: str, status: str
    ) -> ActionRecord | None: ...

    async def save_result_for_user(
        self,
        action_instance_id: str,
        user_id: str,
        status: str,
        results: Any,
    ) -> ActionRecord | None: ...

    async def add_pending_signal_input(
        self,
        action_instance_id: str,
        user_id: str,
        signal_fn_name: str,
        request_info: dict[str, Any],
    ) -> bool: ...

    async def claim_pending_signal_input(
        self, action_instance_id: str, user_id: str, signal_fn_name: str
    ) -> SignalInputClaim | None: ...

    async def restore_pending_signal_input(
        self,
        action_instance_id: str,
        user_id: str,
        signal_fn_name: str,
        request_info: dict[str, Any],
    ) -> None: ...

    async def append_submitted_signal_input(
        self,
        action_instance_id: str,
        user_id: str,
        submitted_entry: dict[str, Any],
    ) -> None: ...


class ActionCatalog(Protocol):
    def get(self, action_id: str) -> ActionDefinition: ...


class ActionAssets(Protocol):
    def has_assets(self, data: Any) -> bool: ...

    async def consume(
        self,
        data: Any,
        *,
        user_id: str,
        instance_id: str,
        action_id: str | None = None,
        signal_fn_name: str | None = None,
    ) -> list[Any]: ...

    async def rollback(self, receipt: list[Any]) -> None: ...


class AsyncActionWorkflow(Protocol):
    async def start(
        self, definition: ActionDefinition, data: Any, instance_id: str, user_id: str
    ) -> None: ...

    async def cancel(self, action_instance_id: str) -> None: ...

    async def signal(
        self, action_id: str, instance_id: str, signal_fn_name: str, data: Any
    ) -> None: ...

    async def get_status(self, instance_id: str) -> ActionStatus | None: ...

    async def get_result(self, instance_id: str) -> Any: ...


class SyncActionWorkflow(Protocol):
    def start(
        self, definition: ActionDefinition, data: Any, instance_id: str, user_id: str
    ) -> None: ...

    def cancel(self, action_instance_id: str) -> None: ...

    def get_status(self, instance_id: str) -> ActionStatus | None: ...

    def get_result(self, instance_id: str) -> Any: ...


class SyncActionRepository(Protocol):
    def create(self, record: ActionRecord) -> ActionRecord: ...

    def get_for_user(
        self, action_instance_id: str, user_id: str
    ) -> ActionRecord | None: ...

    def require_for_user(
        self, action_instance_id: str, user_id: str
    ) -> ActionRecord: ...

    def set_status_for_user(
        self, action_instance_id: str, user_id: str, status: str
    ) -> ActionRecord | None: ...

    def save_result_for_user(
        self,
        action_instance_id: str,
        user_id: str,
        status: str,
        results: Any,
    ) -> ActionRecord | None: ...
