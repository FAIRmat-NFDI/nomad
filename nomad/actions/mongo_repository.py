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

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument

from nomad import infrastructure
from nomad.actions.domain import ActionNotFoundError, ActionRecord, SignalInputClaim
from nomad.common import now
from nomad.config import config
from nomad.mongo.action import ActionDocument

__all__ = ['MongoAsyncActionRepository', 'MongoSyncActionRepository']


def _utcnow() -> datetime:
    """Return the current UTC timestamp through the mockable project clock."""
    return now()


def _record_from_document(doc: ActionDocument) -> ActionRecord:
    """Convert a Beanie document into an action record."""
    return _record_from_mongo(doc.model_dump())


def _record_from_mongo(doc: dict[str, Any]) -> ActionRecord:
    """Convert a raw Mongo document into an action record."""
    payload = dict(doc)
    payload.pop('_id', None)
    for name in ('created_at', 'updated_at'):
        value = payload.get(name)
        if value is not None and value.tzinfo is None:
            payload[name] = value.replace(tzinfo=timezone.utc)
    return ActionRecord.model_validate(payload)


class MongoAsyncActionRepository:
    """MongoDB adapter for the asynchronous action repository port."""

    async def create(self, record: ActionRecord) -> ActionRecord:
        """Insert a new action record."""
        document = ActionDocument(**record.model_dump())
        await document.insert()
        return _record_from_document(document)

    async def get_for_user(
        self, action_instance_id: str, user_id: str
    ) -> ActionRecord | None:
        """Fetch an action owned by the given user."""
        document = await ActionDocument.find_one(
            ActionDocument.action_instance_id == action_instance_id,
            ActionDocument.user_id == user_id,
        )
        if document is None:
            return None
        return _record_from_document(document)

    async def require_for_user(
        self, action_instance_id: str, user_id: str
    ) -> ActionRecord:
        """Fetch an owned action or raise if it does not exist."""
        record = await self.get_for_user(action_instance_id, user_id)
        if record is None:
            raise ActionNotFoundError(
                'The action was not registered in the DB or was registered under a different user.'
            )
        return record

    async def list_for_user(
        self,
        user_id: str,
        page_size: int,
        upload_id: str | None = None,
        created_before: datetime | None = None,
    ) -> tuple[list[ActionRecord], int]:
        """List actions for a user ordered by creation time."""
        query_filters = [ActionDocument.user_id == user_id]
        if upload_id is not None:
            query_filters.append(ActionDocument.upload_id == upload_id)
        if created_before is not None:
            query_filters.append(ActionDocument.created_at < created_before)

        documents = (
            await ActionDocument.find(*query_filters)
            .sort('-created_at')
            .limit(page_size)
            .to_list()
        )
        return [_record_from_document(doc) for doc in documents], len(documents)

    async def count_for_user(self, user_id: str, upload_id: str | None = None) -> int:
        """Count actions for a user with an optional upload filter."""
        query_filters = [ActionDocument.user_id == user_id]
        if upload_id is not None:
            query_filters.append(ActionDocument.upload_id == upload_id)
        return await ActionDocument.find(*query_filters).count()

    async def set_status_for_user(
        self, action_instance_id: str, user_id: str, status: str
    ) -> ActionRecord | None:
        """Update only the status field for an owned action."""
        document = await ActionDocument.find_one(
            ActionDocument.action_instance_id == action_instance_id,
            ActionDocument.user_id == user_id,
        )
        if document is None:
            return None
        document.status = status
        await document.save()
        return _record_from_document(document)

    async def save_result_for_user(
        self,
        action_instance_id: str,
        user_id: str,
        status: str,
        results: Any,
    ) -> ActionRecord | None:
        """Persist results and status for an owned action."""
        document = await ActionDocument.find_one(
            ActionDocument.action_instance_id == action_instance_id,
            ActionDocument.user_id == user_id,
        )
        if document is None:
            return None
        document.status = status
        document.results = results
        await document.save()
        return _record_from_document(document)

    async def add_pending_signal_input(
        self,
        action_instance_id: str,
        user_id: str,
        signal_fn_name: str,
        request_info: dict[str, Any],
    ) -> bool:
        """Append a pending signal-input request for an active action."""
        collection = ActionDocument.get_pymongo_collection()
        result = await collection.update_one(
            {
                'action_instance_id': action_instance_id,
                'user_id': user_id,
                'status': {'$in': ['PENDING', 'RUNNING']},
                'signal_input_requests.signal_fn_name': {'$ne': signal_fn_name},
            },
            {
                '$push': {'signal_input_requests': request_info},
                '$set': {'updated_at': _utcnow()},
            },
        )
        return result.modified_count == 1

    async def claim_pending_signal_input(
        self, action_instance_id: str, user_id: str, signal_fn_name: str
    ) -> SignalInputClaim | None:
        document = await self._consume_pending_signal_input(
            action_instance_id, user_id, signal_fn_name
        )
        if document is None:
            return None
        request = next(
            request
            for request in document['signal_input_requests']
            if request.get('signal_fn_name') == signal_fn_name
        )
        return SignalInputClaim(action_id=document['action_id'], request=request)

    async def _consume_pending_signal_input(
        self, action_instance_id: str, user_id: str, signal_fn_name: str
    ) -> dict[str, Any] | None:
        """Remove and return a matching pending signal-input request."""
        collection = ActionDocument.get_pymongo_collection()
        return await collection.find_one_and_update(
            {
                'action_instance_id': action_instance_id,
                'user_id': user_id,
                'status': {'$in': ['PENDING', 'RUNNING']},
                'signal_input_requests.signal_fn_name': signal_fn_name,
            },
            {
                '$pull': {'signal_input_requests': {'signal_fn_name': signal_fn_name}},
                '$set': {'updated_at': _utcnow()},
            },
            return_document=ReturnDocument.BEFORE,
        )

    async def restore_pending_signal_input(
        self,
        action_instance_id: str,
        user_id: str,
        signal_fn_name: str,
        request_info: dict[str, Any],
    ) -> None:
        """Re-add a pending signal-input request after a failed signal send."""
        collection = ActionDocument.get_pymongo_collection()
        await collection.update_one(
            {
                'action_instance_id': action_instance_id,
                'user_id': user_id,
                'signal_input_requests.signal_fn_name': {'$ne': signal_fn_name},
            },
            {
                '$push': {'signal_input_requests': request_info},
                '$set': {'updated_at': _utcnow()},
            },
        )

    async def append_submitted_signal_input(
        self,
        action_instance_id: str,
        user_id: str,
        submitted_entry: dict[str, Any],
    ) -> None:
        """Append a submitted signal-input payload for an action."""
        collection = ActionDocument.get_pymongo_collection()
        await collection.update_one(
            {
                'action_instance_id': action_instance_id,
                'user_id': user_id,
            },
            {
                '$push': {'signal_inputs_submitted': submitted_entry},
                '$set': {'updated_at': _utcnow()},
            },
        )


class MongoSyncActionRepository:
    """MongoDB adapter for the synchronous action repository port."""

    @property
    def collection(self):
        """Return the sync Mongo collection for action documents."""
        if infrastructure.mongo_client is None:
            infrastructure.setup_mongo()
        return infrastructure.mongo_client.get_database(
            config.mongo.db_name
        ).get_collection('action_document')

    def create(self, record: ActionRecord) -> ActionRecord:
        """Insert a new action record."""
        payload = record.model_dump()
        self.collection.insert_one(payload)
        return record

    def get_for_user(
        self, action_instance_id: str, user_id: str
    ) -> ActionRecord | None:
        """Fetch an action owned by the given user."""
        document = self.collection.find_one(
            {'action_instance_id': action_instance_id, 'user_id': user_id}
        )
        if document is None:
            return None
        return _record_from_mongo(document)

    def require_for_user(self, action_instance_id: str, user_id: str) -> ActionRecord:
        """Fetch an owned action or raise if it does not exist."""
        record = self.get_for_user(action_instance_id, user_id)
        if record is None:
            raise ActionNotFoundError(
                'The action was not registered in the DB or was registered under a different user.'
            )
        return record

    def set_status_for_user(
        self, action_instance_id: str, user_id: str, status: str
    ) -> ActionRecord | None:
        """Update only the status field for an owned action."""
        document = self.collection.find_one_and_update(
            {'action_instance_id': action_instance_id, 'user_id': user_id},
            {'$set': {'status': status, 'updated_at': _utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return _record_from_mongo(document)

    def save_result_for_user(
        self,
        action_instance_id: str,
        user_id: str,
        status: str,
        results: Any,
    ) -> ActionRecord | None:
        """Persist results and status for an owned action."""
        document = self.collection.find_one_and_update(
            {'action_instance_id': action_instance_id, 'user_id': user_id},
            {'$set': {'status': status, 'results': results, 'updated_at': _utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return _record_from_mongo(document)
