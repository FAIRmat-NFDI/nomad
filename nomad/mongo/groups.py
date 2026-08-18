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

from collections.abc import Iterable

from mongoengine import (
    Document,
    EmbeddedDocument,
    EmbeddedDocumentListField,
    EnumField,
    ListField,
    Q,
    QuerySet,
    StringField,
    signals,
)

from nomad.app.v1.models.groups import (
    UserGroupEdit,
    UserGroupMember,
    UserGroupMemberRole,
    UserGroupQuery,
)
from nomad.app.v1.routers.groups_utils import (
    convert_members_to_info,
    get_owner_and_members,
    validate_members_info,
)
from nomad.utils import create_uuid


class MongoUserGroupMember(EmbeddedDocument):
    """
    A user that is member of a user group.
    """

    user_id = StringField(required=True)
    role = EnumField(
        UserGroupMemberRole, default=UserGroupMemberRole.MEMBER, required=True
    )


class MongoUserGroup(Document):
    """
    A group of users.

    Members are users, one of them is the owner, some may be maintainers.
    Field 'members_info' is the ground truth, 'members' and 'owner' are
    kept for compatibility.
    """

    id_field = 'group_id'

    group_id = StringField(primary_key=True)
    group_name = StringField()
    description = StringField()

    # .members_info supersedes .members and .owner, they are kept for compatibility.
    # .owner was previously not in members, now it should be; but for filtering it must
    # still be dealt with separately.
    # It's enforced by calling clean() when saving or instantiating the object.
    members_info = EmbeddedDocumentListField(MongoUserGroupMember, required=True)
    members = ListField(StringField())
    owner = StringField(required=True)

    meta = {
        'strict': False,
        'collection': 'user_group',
        'auto_create_index': False,
        'indexes': [
            'group_name',
            'owner',
            'members',
            'members_info.user_id',
        ],
    }

    @classmethod
    def q_by_ids(cls, group_ids: str | Iterable[str]) -> Q:
        """
        Returns UserGroup Q for group_ids.
        """
        if isinstance(group_ids, str):
            return Q(group_id=group_ids)
        else:
            return Q(group_id__in=group_ids)

    @classmethod
    def q_by_user_id(cls, user_id: str | None) -> Q:
        """
        Returns UserGroup Q where user_id appears in the group.

        Does not imply special group 'all' because it has no UserGroup object.
        """
        return Q(owner=user_id) | Q(members=user_id) | Q(members_info__user_id=user_id)

    @classmethod
    def q_by_search_terms(cls, search_terms: str) -> Q:
        """
        Returns UserGroup Q where group_name includes search_terms (no case).

        Each space-separated term must be included in group_name.
        """
        q = Q()
        for term in search_terms.split():
            q &= Q(group_name__icontains=term)

        return q

    @classmethod
    def get_by_query(cls, query: UserGroupQuery) -> QuerySet:
        """
        Returns UserGroup objects according to query, sub queries are connected by AND.
        """
        q = Q()
        if query.group_id is not None:
            q &= cls.q_by_ids(query.group_id)
        if query.user_id is not None:
            q &= cls.q_by_user_id(query.user_id)
        if query.search_terms is not None:
            q &= cls.q_by_search_terms(query.search_terms)

        groups = cls.objects(q)
        return groups

    @classmethod
    def get_ids_by_user_id(cls, user_id: str | None, *, include_all=True) -> list[str]:
        """
        Returns ids of all user groups where user_id is a member.

        When include_all is true, special group 'all' is included,
        even if user_id is missing or not a user.
        """
        group_ids = ['all'] if include_all else []
        if user_id is not None:
            query = UserGroupQuery(user_id=user_id)
            groups = cls.get_by_query(query)
            group_ids.extend(group.group_id for group in groups)
        return group_ids

    def clean(self):
        """
        Clean data before saving and when instantiating the object (cf. signal).

        Constraints:
            - All user_ids in .members_info are unique.
            - 1-to-1 mapping between .members_info and .members.
            - One entry in .members_info with role owner, must match .owner.
        """
        if getattr(self, '_is_upsert', False):
            self.validate_and_fill_members_fields()

        elif self.pk and not self.members_info:
            self.fill_members_info()
            self.members = [m.user_id for m in self.members_info]

        super().clean()

    def fill_members_info(self) -> None:
        """Fill members_info from legacy members and owner fields."""
        if not self.owner:
            self.members_info = []
            return

        info = convert_members_to_info(self.members or [], self.owner)
        self.members_info = [
            MongoUserGroupMember(user_id=m.user_id, role=m.role) for m in info
        ]

    def validate_and_fill_members_fields(self) -> None:
        """Validate members_info and fill members/owner fields."""
        if not self.members_info:
            raise ValueError('members_info must be set and non-empty')

        validate_members_info(self.members_info)

        self.owner, self.members = get_owner_and_members(self.members_info)

    def clean_update_reload(self, updates: UserGroupEdit):
        """Returns updated group after cleaning, validating, and saving to the DB.

        Use this instead of `update` or `modify` to ensure the object is cleaned.

        Deprecated field 'members' will be overridden on save.
        """
        if info := updates.members_info:
            updates.members_info = [
                MongoUserGroupMember(user_id=m.user_id, role=m.role) for m in info
            ]

        for key in updates.model_fields_set:
            if value := getattr(updates, key):
                setattr(self, key, value)

        self._is_upsert = True
        return self.save()

    @classmethod
    def _post_init_clean(cls, sender, document, **kwargs):
        """Clean document on retrieval."""
        if getattr(document, '_clean_on_init', True):
            document.clean()

    def reload_without_clean(self, *args, **kwargs):
        """Reload document from database without running post_init."""
        func = self.__class__._post_init_clean
        signals.post_init.disconnect(func, sender=self.__class__)
        try:
            return self.reload(*args, **kwargs)
        finally:
            signals.post_init.connect(func, sender=self.__class__)


signals.post_init.connect(MongoUserGroup._post_init_clean, sender=MongoUserGroup)


def create_mongo_user_group(data: UserGroupEdit) -> MongoUserGroup:
    """Create a new user group with validation.

    Deprecated field 'members' will be overridden on creation."""
    user_group = MongoUserGroup(
        group_id=create_uuid(),
        group_name=data.group_name,
        description=data.description,
    )
    if user_group.group_name is None:
        user_group.group_name = user_group.group_id
    user_group.members_info = [
        MongoUserGroupMember(user_id=m.user_id, role=m.role)
        for m in (data.members_info or [])
    ]

    user_group._is_upsert = True
    user_group.save()
    return user_group


def create_mongo_user_group_for_test(
    *,
    group_id: str | None = None,
    group_name: str | None = None,
    owner: str | None = None,
    members: Iterable[str] | None = None,
    members_info: Iterable[UserGroupMember] | None = None,
) -> MongoUserGroup:
    """Create a user group for testing without cleaning or validation."""
    signals.post_init.disconnect(MongoUserGroup._post_init_clean, sender=MongoUserGroup)
    try:
        user_group = MongoUserGroup(
            group_id=group_id,
            group_name=group_name,
            owner=owner,
            members=list(members) if members else None,
        )

        if members_info is not None:
            user_group.members_info = [
                MongoUserGroupMember(user_id=m.user_id, role=m.role)
                for m in members_info
            ]

        user_group._clean_on_init = False
        user_group.save(clean=False, validate=False)
        return user_group
    finally:
        signals.post_init.connect(
            MongoUserGroup._post_init_clean, sender=MongoUserGroup
        )


def get_mongo_user_group(group_id: str) -> MongoUserGroup | None:
    return MongoUserGroup.objects(group_id=group_id).first()


def user_group_exists(group_id: str, *, include_all=True) -> bool:
    if include_all and group_id == 'all':
        return True
    return get_mongo_user_group(group_id) is not None
