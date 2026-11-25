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

from datetime import UTC, datetime
from typing import Any

from pymongo import ReadPreference, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern

from nomad import infrastructure
from nomad.actions.domain import (
    ActionDefinition,
    ActionNotFoundError,
    ActionRecord,
    SignalInputClaim,
)
from nomad.common import now
from nomad.config import config
from nomad.mongo.action import ActionDocument

__all__ = ['MongoAsyncActionRepository', 'MongoSyncActionRepository']

# Stop older start paths before cutover: initialization seeds existing active
# records once, and subsequent starts must use the shared reservation document.
_CAPACITY_COLLECTION = 'action_capacity'


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
            payload[name] = value.replace(tzinfo=UTC)
    return ActionRecord.model_validate(payload)


def _active_filter(action_id: str) -> dict[str, Any]:
    """Match pending and running records for an action."""
    return {'action_id': action_id, 'status': {'$in': ['PENDING', 'RUNNING']}}


def _capacity_filter(definition: ActionDefinition, user_id: str) -> dict[str, Any]:
    """Match the capacity document only if both limits allow another run."""
    user_runs = {
        '$filter': {
            'input': '$active_runs',
            'as': 'run',
            'cond': {'$eq': ['$$run.user_id', {'$literal': user_id}]},
        }
    }
    return {
        '_id': definition.action_id,
        '$expr': {
            '$and': [
                {'$lt': [{'$size': '$active_runs'}, definition.max_concurrent_runs]},
                {
                    '$lt': [
                        {'$size': user_runs},
                        definition.max_concurrent_runs_per_user,
                    ]
                },
            ]
        },
    }


def _slot(record: ActionRecord) -> dict[str, Any]:
    """Store the reservation and its pre-submission expiry marker."""
    return {
        'instance_id': record.action_instance_id,
        'user_id': record.user_id,
        'unsubmitted_since': _utcnow(),
    }


def _unsubmitted_slot_filter(record: ActionRecord) -> dict[str, Any]:
    """Match the reservation only while it is eligible for expiry."""
    return {
        '_id': record.action_id,
        'active_runs': {
            '$elemMatch': {
                'instance_id': record.action_instance_id,
                'unsubmitted_since': {'$exists': True},
            }
        },
    }


def _seed_slots(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Convert existing active MongoDB records to initial reservations."""
    return [
        {'instance_id': record['action_instance_id'], 'user_id': record['user_id']}
        for record in records
    ]


class MongoAsyncActionRepository:
    """MongoDB adapter for the asynchronous action repository port."""

    @property
    def capacity(self):
        """Return the async capacity collection with primary reads and majority writes."""
        return ActionDocument.get_pymongo_collection().database.get_collection(
            _CAPACITY_COLLECTION,
            write_concern=WriteConcern('majority'),
            read_preference=ReadPreference.PRIMARY,
        )

    async def reserve_slot(
        self, definition: ActionDefinition, record: ActionRecord
    ) -> bool:
        """Atomically reserve a slot if neither concurrency limit is reached.

        On first use, seed the capacity document from existing active records.
        Return whether the slot was reserved; this does not create an action record.
        """
        capacity = self.capacity
        query = _capacity_filter(definition, record.user_id)
        update = {'$push': {'active_runs': _slot(record)}}
        if (
            await capacity.find_one_and_update(query, update, projection={'_id': 1})
            is not None
        ):
            return True
        # Seed once from existing runs. A concurrent initializer cannot replace the winner.
        if await capacity.find_one({'_id': record.action_id}, {'_id': 1}) is None:
            records = (
                await ActionDocument.get_pymongo_collection()
                .with_options(read_preference=ReadPreference.PRIMARY)
                .find(
                    _active_filter(record.action_id),
                    {'action_instance_id': 1, 'user_id': 1},
                )
                .to_list()
            )
            try:
                await capacity.update_one(
                    {'_id': record.action_id},
                    {'$setOnInsert': {'active_runs': _seed_slots(records)}},
                    upsert=True,
                )
            except DuplicateKeyError:
                pass  # Another request initialized this action's capacity.
            return (
                await capacity.find_one_and_update(query, update, projection={'_id': 1})
                is not None
            )
        return False

    async def claim_submission(self, record: ActionRecord) -> bool:
        """Atomically remove the expiry marker before calling Temporal."""
        result = await self.capacity.update_one(
            _unsubmitted_slot_filter(record),
            {'$unset': {'active_runs.$.unsubmitted_since': ''}},
        )
        return result.modified_count == 1

    async def expire_unsubmitted_slots(self, cutoff: datetime) -> None:
        """Release expired pre-submission slots, including those without a record."""
        expired = {'unsubmitted_since': {'$lte': cutoff}}
        # The pull and claim compete on the same document. Once claimed, a
        # reservation cannot be expired even if Temporal does not find it yet.
        cursor = self.capacity.find({'active_runs': {'$elemMatch': expired}})
        async for document in cursor:
            for slot in document['active_runs']:
                if 'unsubmitted_since' not in slot:
                    continue
                instance_id = slot['instance_id']
                match = {'instance_id': instance_id, **expired}
                result = await self.capacity.update_one(
                    {'_id': document['_id'], 'active_runs': {'$elemMatch': match}},
                    {'$pull': {'active_runs': match}},
                )
                if result.modified_count:
                    await self.finish_if_active(instance_id, 'FAILED')

    async def release_slots(self, action_id: str, instance_ids: list[str]) -> None:
        """Release the specified reservations; missing reservations are ignored."""
        await self.capacity.update_one(
            {'_id': action_id},
            {'$pull': {'active_runs': {'instance_id': {'$in': instance_ids}}}},
        )

    async def create(self, record: ActionRecord) -> ActionRecord:
        """Insert a new action record."""
        document = ActionDocument(**record.model_dump())
        await document.insert()
        return _record_from_document(document)

    async def list_active(
        self, after_instance_id: str | None, limit: int
    ) -> list[ActionRecord]:
        """Page through active actions and non-active records that still hold slots."""
        active_statuses = ['PENDING', 'RUNNING']
        filters: dict[str, Any] = {'status': {'$in': active_statuses}}
        reserved: list[dict[str, Any]] = [{'$unwind': '$active_runs'}]
        if after_instance_id is not None:
            after = {'$gt': after_instance_id}
            filters['action_instance_id'] = after
            reserved.append({'$match': {'active_runs.instance_id': after}})
        collection = ActionDocument.get_pymongo_collection()
        # A cancellation request can mark a record CANCELED before Temporal ends.
        # Keep checking it while it holds a slot, without scanning old history.
        reserved.extend(
            [
                {
                    '$lookup': {
                        'from': collection.name,
                        'localField': 'active_runs.instance_id',
                        'foreignField': 'action_instance_id',
                        'as': 'record',
                    }
                },
                {'$unwind': '$record'},
                {
                    '$match': {
                        'record.status': {'$nin': active_statuses}  # typos: ignore
                    }
                },
                {'$replaceWith': '$record'},
            ]
        )
        page: list[dict[str, Any]] = [
            {'$sort': {'action_instance_id': 1}},
            {'$limit': limit},
        ]
        cursor = await collection.aggregate(
            [
                {'$match': filters},
                *page,
                {
                    '$unionWith': {
                        'coll': _CAPACITY_COLLECTION,
                        'pipeline': [*reserved, *page],
                    }
                },
                *page,
            ]
        )
        return [_record_from_mongo(document) async for document in cursor]

    async def finish_if_active(
        self, action_instance_id: str, status: str, results: Any = None
    ) -> ActionRecord | None:
        """Persist a terminal workflow state without reverting a concurrent stop."""
        update = {'status': status, 'updated_at': _utcnow()}
        if results is not None:
            update['results'] = results
        document = await ActionDocument.get_pymongo_collection().find_one_and_update(
            {
                'action_instance_id': action_instance_id,
                'status': {'$in': ['PENDING', 'RUNNING']},
            },
            {'$set': update},
            return_document=ReturnDocument.AFTER,
        )
        return _record_from_mongo(document) if document is not None else None

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
        """Atomically remove and return a pending signal request, if present."""
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
    def capacity(self):
        """Return the sync capacity collection shared with async callers."""
        return self.collection.database.get_collection(
            _CAPACITY_COLLECTION,
            write_concern=WriteConcern('majority'),
            read_preference=ReadPreference.PRIMARY,
        )

    def reserve_slot(self, definition: ActionDefinition, record: ActionRecord) -> bool:
        """Atomically reserve a slot if neither concurrency limit is reached.

        On first use, seed the capacity document from existing active records.
        Return whether the slot was reserved; this does not create an action record.
        """
        capacity = self.capacity
        query = _capacity_filter(definition, record.user_id)
        update = {'$push': {'active_runs': _slot(record)}}
        if (
            capacity.find_one_and_update(query, update, projection={'_id': 1})
            is not None
        ):
            return True
        if capacity.find_one({'_id': record.action_id}, {'_id': 1}) is None:
            records = (
                self.collection.with_options(read_preference=ReadPreference.PRIMARY)
                .find(
                    _active_filter(record.action_id),
                    {'action_instance_id': 1, 'user_id': 1},
                )
                .to_list()
            )
            try:
                capacity.update_one(
                    {'_id': record.action_id},
                    {'$setOnInsert': {'active_runs': _seed_slots(records)}},
                    upsert=True,
                )
            except DuplicateKeyError:
                pass  # Another request initialized this action's capacity.
            return (
                capacity.find_one_and_update(query, update, projection={'_id': 1})
                is not None
            )
        return False

    def claim_submission(self, record: ActionRecord) -> bool:
        """Atomically remove the expiry marker before calling Temporal."""
        result = self.capacity.update_one(
            _unsubmitted_slot_filter(record),
            {'$unset': {'active_runs.$.unsubmitted_since': ''}},
        )
        return result.modified_count == 1

    def release_slots(self, action_id: str, instance_ids: list[str]) -> None:
        """Release the specified reservations; missing reservations are ignored."""
        self.capacity.update_one(
            {'_id': action_id},
            {'$pull': {'active_runs': {'instance_id': {'$in': instance_ids}}}},
        )

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
