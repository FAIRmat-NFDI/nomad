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

"""Action lifecycle use cases, independent of database and workflow adapters."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from nomad.actions.domain import (
    ActionRecord,
    ActionStatus,
    SignalInputNotFoundError,
    is_active_status,
    require_active_status,
)
from nomad.actions.models import (
    ActionRecordPage,
    ActionSummaryRecord,
    RequestSignalInputActivityInput,
)
from nomad.actions.pagination import _decode_cursor, _encode_cursor
from nomad.actions.ports import (
    ActionAssets,
    ActionCatalog,
    AsyncActionRepository,
    AsyncActionWorkflow,
    SyncActionRepository,
    SyncActionWorkflow,
)
from nomad.actions.serialization import _to_dict, serialize_payload
from nomad.common import now


class ActionCreation:
    """Shared preparation rules for sync and async starts."""

    def __init__(
        self,
        catalog: ActionCatalog,
        assets: ActionAssets,
        clock: Callable[[], datetime] = now,
    ):
        self.catalog = catalog
        self.assets = assets
        self.clock = clock

    def prepare(self, action_id: str, data: Any):
        if not getattr(data, 'user_id', None):
            raise ValueError('Action input must contain a user_id.')
        definition = self.catalog.get(action_id)
        time = self.clock()
        record = ActionRecord(
            action_id=action_id,
            action_instance_id=f'{action_id}-{data.user_id}-{uuid.uuid4()}',
            user_id=data.user_id,
            upload_id=getattr(data, 'upload_id', None),
            status=ActionStatus.PENDING.value,
            input_data=_to_dict(data),
            created_at=time,
            updated_at=time,
            priority_key=definition.priority_key,
            priority_fairness_key=definition.priority_fairness_key,
        )
        return definition, record


class ActionService:
    """Action use cases with synchronous methods and explicit ``a_`` async methods.

    Each execution mode uses its own I/O ports; async methods never bridge to
    blocking sync operations. Public plugin wrappers retain their original names.
    """

    def __init__(
        self,
        *,
        repository: SyncActionRepository,
        workflow: SyncActionWorkflow,
        a_repository: AsyncActionRepository,
        a_workflow: AsyncActionWorkflow,
        creation: ActionCreation,
        clock: Callable[[], datetime] = now,
    ):
        """Initialize the service with synchronous and asynchronous ports."""
        self.repository = repository
        self.workflow = workflow
        self.a_repository = a_repository
        self.a_workflow = a_workflow
        self.creation = creation
        self.clock = clock

    def start(self, action_id: str, data: Any) -> str:
        """Create and start an action for a synchronous caller."""
        definition, record = self.creation.prepare(action_id, data)
        if self.creation.assets.has_assets(data):
            raise ValueError(
                'ActionAssetRef inputs are not supported from ELNs. Use the new Action form in the GUI to create an action.'
            )
        self.repository.create(record)
        self.workflow.start(definition, data, record.action_instance_id, record.user_id)
        return record.action_instance_id

    def stop(self, action_instance_id: str, user_id: str) -> None:
        """Cancel an owned active action and persist its canceled status."""
        action = self.repository.require_for_user(action_instance_id, user_id)
        require_active_status(action.status)
        self.workflow.cancel(action_instance_id)
        self.repository.set_status_for_user(
            action_instance_id, user_id, ActionStatus.CANCELED.value
        )

    def get_status(self, action_instance_id: str, user_id: str) -> ActionStatus:
        """Fetch and persist the current status of an owned action."""
        self.repository.require_for_user(action_instance_id, user_id)
        status = self.workflow.get_status(action_instance_id) or ActionStatus.TERMINATED
        self.repository.set_status_for_user(action_instance_id, user_id, status.value)
        return status

    def get_result(self, action_instance_id: str, user_id: str) -> Any:
        """Fetch, serialize, and persist the result of an owned action."""
        self.repository.require_for_user(action_instance_id, user_id)
        result = self.workflow.get_result(action_instance_id)
        if result is None:
            return None
        result = serialize_payload(result)
        self.repository.save_result_for_user(
            action_instance_id, user_id, ActionStatus.COMPLETED.value, result
        )
        return result

    async def a_start(self, action_id: str, data: Any) -> str:
        """Create and asynchronously start an action, consuming staged assets."""
        definition, record = self.creation.prepare(action_id, data)
        receipt = await self.creation.assets.consume(
            data,
            user_id=record.user_id,
            instance_id=record.action_instance_id,
            action_id=action_id,
        )
        try:
            # Asset consumption may rewrite refs in the input; serialize afterwards.
            record.input_data = _to_dict(data)
            await self.a_repository.create(record)
            await self.a_workflow.start(
                definition, data, record.action_instance_id, record.user_id
            )
        except Exception:
            await self.creation.assets.rollback(receipt)
            raise
        return record.action_instance_id

    async def a_stop(self, action_instance_id: str, user_id: str) -> None:
        """Asynchronously cancel an owned active action and persist its status."""
        action = await self.a_repository.require_for_user(action_instance_id, user_id)
        require_active_status(action.status)
        await self.a_workflow.cancel(action_instance_id)
        await self.a_repository.set_status_for_user(
            action_instance_id, user_id, ActionStatus.CANCELED.value
        )

    async def a_get_status(self, action_instance_id: str, user_id: str) -> ActionStatus:
        """Asynchronously fetch and persist an owned action's current status."""
        await self.a_repository.require_for_user(action_instance_id, user_id)
        status = await self.a_workflow.get_status(action_instance_id)
        status = status or ActionStatus.TERMINATED
        await self.a_repository.set_status_for_user(
            action_instance_id, user_id, status.value
        )
        return status

    async def a_get_result(self, action_instance_id: str, user_id: str) -> Any:
        """Asynchronously fetch, serialize, and persist an owned action result."""
        await self.a_repository.require_for_user(action_instance_id, user_id)
        result = await self.a_workflow.get_result(action_instance_id)
        if result is None:
            return None
        result = serialize_payload(result)
        await self.a_repository.save_result_for_user(
            action_instance_id, user_id, ActionStatus.COMPLETED.value, result
        )
        return result

    async def a_refresh(self, action: ActionRecord) -> ActionRecord:
        """Refresh a stored action's status and completed result from its workflow."""
        status = (
            await self.a_workflow.get_status(action.action_instance_id)
            or ActionStatus.UNKNOWN
        )
        if status == ActionStatus.COMPLETED:
            result = await self.a_workflow.get_result(action.action_instance_id)
            if result is not None:
                updated = await self.a_repository.save_result_for_user(
                    action.action_instance_id,
                    action.user_id,
                    status.value,
                    serialize_payload(result),
                )
                return updated or action
        updated = await self.a_repository.set_status_for_user(
            action.action_instance_id, action.user_id, status.value
        )
        return updated or action

    async def a_get_owned(
        self, action_instance_id: str, user_id: str
    ) -> ActionRecord | None:
        """Retrieve an owned action and refresh stale workflow state when needed."""
        action = await self.a_repository.get_for_user(action_instance_id, user_id)
        if action is None:
            return None
        if is_active_status(action.status):
            return await self.a_refresh(action)
        if action.status == ActionStatus.COMPLETED.value and action.results is None:
            result = await self.a_workflow.get_result(action_instance_id)
            if result is not None:
                return await self.a_repository.save_result_for_user(
                    action_instance_id,
                    user_id,
                    action.status,
                    serialize_payload(result),
                )
        return action

    async def a_list_owned(
        self,
        user_id: str,
        page_size: int = 20,
        cursor: str | None = None,
        upload_id: str | None = None,
    ) -> ActionRecordPage:
        """List a cursor-paginated page of owned actions and refresh active ones."""
        if not 1 <= page_size <= 100:
            raise ValueError('page_size must be between 1 and 100.')
        records, _ = await self.a_repository.list_for_user(
            user_id,
            page_size + 1,
            upload_id,
            _decode_cursor(cursor) if cursor is not None else None,
        )
        page = records[:page_size]

        async def refresh_if_active(record):
            return (
                await self.a_refresh(record)
                if is_active_status(record.status)
                else record
            )

        refreshed = await asyncio.gather(
            *(refresh_if_active(record) for record in page)
        )
        return ActionRecordPage(
            items=[
                ActionSummaryRecord.model_construct(**record.model_dump())
                for record in refreshed
            ],
            next_cursor=_encode_cursor(page[-1].created_at)
            if len(records) > page_size
            else None,
            total=await self.a_repository.count_for_user(user_id, upload_id),
        )

    async def a_request_signal_input(self, data: RequestSignalInputActivityInput):
        """Register a pending user-input request for an active action."""
        request = data.model_dump(
            exclude_none=True, exclude={'action_instance_id', 'user_id'}
        )
        if await self.a_repository.add_pending_signal_input(
            data.action_instance_id,
            data.user_id,
            data.signal_fn_name,
            request,
        ):
            return {'status': 'signal_input_requested'}
        action = await self.a_repository.require_for_user(
            data.action_instance_id, data.user_id
        )
        require_active_status(action.status)
        raise ValueError(
            f"Action already has a pending signal input request for signal '{data.signal_fn_name}'."
        )

    async def a_submit_signal_input(
        self, action_instance_id: str, user_id: str, signal_fn_name: str, data: Any
    ):
        """Claim pending input, signal the workflow, and record the submission."""
        claim = await self.a_repository.claim_pending_signal_input(
            action_instance_id, user_id, signal_fn_name
        )
        if claim is None:
            action = await self.a_repository.require_for_user(
                action_instance_id, user_id
            )
            require_active_status(action.status)
            raise SignalInputNotFoundError(
                f"No pending signal input request found for signal '{signal_fn_name}'."
            )
        receipt = []
        try:
            receipt = await self.creation.assets.consume(
                data,
                user_id=user_id,
                instance_id=action_instance_id,
                signal_fn_name=signal_fn_name,
            )
            await self.a_workflow.signal(
                claim.action_id, action_instance_id, signal_fn_name, data
            )
        except Exception:
            try:
                await self.creation.assets.rollback(receipt)
            finally:
                # Restore the claim even if asset rollback itself fails.
                await self.a_repository.restore_pending_signal_input(
                    action_instance_id,
                    user_id,
                    signal_fn_name,
                    claim.request,
                )
            raise
        submitted = {
            'signal_fn_name': signal_fn_name,
            'data': serialize_payload(data),
            'timestamp': self.clock().isoformat(),
            **{
                key: claim.request[key]
                for key in ('title', 'description', 'content')
                if claim.request.get(key) is not None
            },
        }
        # Do not restore a claim after successful delivery if history persistence fails.
        await self.a_repository.append_submitted_signal_input(
            action_instance_id, user_id, submitted
        )
