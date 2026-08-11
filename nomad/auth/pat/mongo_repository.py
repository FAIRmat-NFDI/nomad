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

import datetime

from bson import ObjectId

from nomad.mongo.pat import PAT

from .domain import PATRecord
from .ports import (
    PATCreationData,
    PATQuerySpec,
    PATRepository,
    PATSortOrder,
    PruneStats,
)


def to_record(pat: PAT) -> PATRecord:
    def to_utc(value: datetime.datetime | None) -> datetime.datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value

    created_at = to_utc(pat.created_at)
    assert created_at is not None

    return PATRecord(
        id=str(pat.id),
        user_id=pat.user_id,
        name=pat.name,
        description=pat.description,
        scopes=tuple(pat.scopes) if pat.scopes else (),
        token_digest=pat.token_digest,
        created_at=created_at,
        expired_at=to_utc(pat.expired_at),
        revoked=pat.revoked,
        revoked_at=to_utc(pat.revoked_at),
        updated_at=to_utc(pat.updated_at),
        last_used_at=to_utc(pat.last_used_at),
    )


class MongoPATRepository(PATRepository):
    """Concrete MongoEngine implementation of the PATRepository protocol."""

    def save_new(self, new_pat: PATCreationData) -> PATRecord:
        pat = PAT(
            user_id=new_pat.user_id,
            name=new_pat.name,
            description=new_pat.description,
            scopes=list(new_pat.scopes),
            token_digest=new_pat.token_digest,
            expired_at=new_pat.expired_at,
            created_at=new_pat.created_at,
            updated_at=new_pat.created_at,
        )
        pat.save()
        return to_record(pat)

    def get_owned(self, *, pat_id: str, user_id: str) -> PATRecord | None:
        if not ObjectId.is_valid(pat_id):
            return None
        from mongoengine.errors import DoesNotExist, ValidationError

        try:
            pat = PAT.objects(id=pat_id, user_id=user_id).first()
        except (ValidationError, DoesNotExist):
            return None
        return to_record(pat) if pat is not None else None

    def count_active(self, *, user_id: str, time: datetime.datetime) -> int:
        from mongoengine import Q

        query = (
            Q(user_id=user_id)
            & Q(revoked=False)
            & (Q(expired_at=None) | Q(expired_at__gte=time))
        )
        return PAT.objects(query).count()

    def list_owned(
        self,
        *,
        user_id: str,
        spec: PATQuerySpec,
        time: datetime.datetime,
        start: int,
        limit: int | None,
        order_by: PATSortOrder,
    ) -> tuple[list[PATRecord], int]:
        from mongoengine import Q

        query = Q(user_id=user_id)
        if spec.search:
            query = query & Q(name__icontains=spec.search)
        if spec.revoked is not None:
            query = query & Q(revoked=spec.revoked)
        if spec.state == 'active':
            query = (
                query
                & Q(revoked=False)
                & (Q(expired_at=None) | Q(expired_at__gte=time))
            )
        elif spec.state == 'inactive':
            query = query & (Q(revoked=True) | Q(expired_at__lt=time))

        for value, field, operator in (
            (spec.created_after, 'created_at', 'gte'),
            (spec.created_before, 'created_at', 'lte'),
            (spec.last_used_after, 'last_used_at', 'gte'),
            (spec.last_used_before, 'last_used_at', 'lte'),
            (spec.expires_after, 'expired_at', 'gte'),
            (spec.expires_before, 'expired_at', 'lte'),
        ):
            if value is not None:
                query = query & Q(**{f'{field}__{operator}': value})

        sort_mapping = {
            'created_asc': '+created_at',
            'created_desc': '-created_at',
            'expires_asc': '+expired_at',
            'expires_desc': '-expired_at',
            'last_used_asc': '+last_used_at',
            'last_used_desc': '-last_used_at',
            'name_asc': '+name',
            'name_desc': '-name',
        }

        queryset = PAT.objects(query).exclude('token_digest')
        total = queryset.count()

        if limit == 0:
            return [], total

        queryset = queryset.order_by(sort_mapping[order_by]).skip(start)
        if limit is not None:
            queryset = queryset.limit(limit)

        page = list(queryset)
        return [to_record(pat) for pat in page], total

    def revoke_owned(
        self, *, user_id: str, pat_id: str, revoked_at: datetime.datetime
    ) -> bool:
        if not ObjectId.is_valid(pat_id):
            return False
        pat = PAT.objects(id=pat_id, user_id=user_id).first()
        if pat is None:
            return False
        if pat.revoked:
            return True
        pat.update(
            set__revoked=True,
            set__revoked_at=revoked_at,
            set__updated_at=revoked_at,
        )
        return True

    def claim_active_for_rotation(
        self,
        *,
        user_id: str,
        pat_id: str,
        time: datetime.datetime,
        revoked_at: datetime.datetime,
    ) -> PATRecord | None:
        """Atomically revoke an active PAT and return its pre-rotation metadata."""
        if not ObjectId.is_valid(pat_id):
            return None

        old_pat = self.get_owned(pat_id=pat_id, user_id=user_id)
        if old_pat is None or not old_pat.is_active_at(time):
            return None

        from mongoengine import Q

        query = (
            Q(id=pat_id)
            & Q(user_id=user_id)
            & Q(revoked=False)
            & (Q(expired_at=None) | Q(expired_at__gte=time))
        )

        matched = PAT.objects(query).update(
            set__revoked=True,
            set__revoked_at=revoked_at,
            set__updated_at=revoked_at,
        )
        return old_pat if matched == 1 else None

    def find_by_digest(self, digest: str) -> PATRecord | None:
        pat = PAT.objects(token_digest=digest).first()
        return to_record(pat) if pat is not None else None

    def mark_used(self, *, pat_id: str, used_at: datetime.datetime) -> None:
        if not ObjectId.is_valid(pat_id):
            return
        PAT.objects(id=pat_id).update(
            set__last_used_at=used_at,
        )

    def prune(
        self,
        *,
        cutoff: datetime.datetime,
        expired: bool,
        revoked: bool,
        user_id: str | None,
        dry_run: bool,
    ) -> PruneStats:
        from mongoengine import Q

        base_query = Q(user_id=user_id) if user_id is not None else Q()
        expired_query = base_query & Q(expired_at__ne=None) & Q(expired_at__lte=cutoff)
        revoked_query = (
            base_query
            & Q(revoked=True)
            & Q(revoked_at__ne=None)
            & Q(revoked_at__lte=cutoff)
        )

        if expired and revoked:
            selected = expired_query | revoked_query
        elif expired:
            selected = expired_query
        else:
            selected = revoked_query

        matched = PAT.objects(selected).count()
        expired_matched = PAT.objects(expired_query).count() if expired else 0
        revoked_matched = PAT.objects(revoked_query).count() if revoked else 0

        deleted = 0
        if not dry_run:
            deleted = PAT.objects(selected).delete()

        return PruneStats(
            matched=matched,
            deleted=deleted,
            expired_matched=expired_matched,
            revoked_matched=revoked_matched,
        )
