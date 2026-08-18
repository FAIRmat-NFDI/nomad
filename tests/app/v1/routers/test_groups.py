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

from collections.abc import Mapping
from enum import Enum

import pytest

from nomad.app.v1.models.groups import UserGroup, UserGroupMember, UserGroupResponse
from nomad.mongo.groups import MongoUserGroup, get_mongo_user_group, user_group_exists

from .common import assert_response, perform_get, perform_post

base_url = 'groups'


def get_val(obj, key):
    if isinstance(obj, Mapping):
        val = obj[key]
    else:
        val = getattr(obj, key)

    if isinstance(val, Enum):
        val = val.value

    return val


def assert_unordered_lists(list1, list2):
    assert sorted(list1) == sorted(list2)


def assert_member_object(member, ref_member):
    keys = UserGroupMember.model_fields
    for key in keys:
        assert get_val(member, key) == get_val(ref_member, key)


def assert_members_info(info1, info2):
    assert len(info1) == len(info2)
    sorted_info1 = sorted(info1, key=lambda m: get_val(m, 'user_id'))
    sorted_info2 = sorted(info2, key=lambda m: get_val(m, 'user_id'))
    for m1, m2 in zip(sorted_info1, sorted_info2):
        assert_member_object(m1, m2)


def assert_group(group, ref_group, keys=None):
    if keys is None:
        keys = UserGroup.model_fields

    excluded_fields = {'members', 'members_info'}
    fields = set(keys) - excluded_fields
    for field in fields:
        assert get_val(group, field) == get_val(ref_group, field)

    if 'members' in keys:
        val = get_val(group, 'members')
        ref_val = get_val(ref_group, 'members')
        assert_unordered_lists(val, ref_val)

    if 'members_info' in keys:
        val = get_val(group, 'members_info')
        ref_val = get_val(ref_group, 'members_info')
        assert_members_info(
            get_val(group, 'members_info'), get_val(ref_group, 'members_info')
        )


# tests using group fixtures with scope: 'module'


def test_group_collection_name(groups_module):
    MongoUserGroup._get_collection_name() == 'user_group'


def test_dirty_group_in_default_groups(groups_module, group_molds):
    mold = group_molds['dirty234']
    assert mold['owner'] not in mold['members']
    assert mold.get('members_info') is None

    group = groups_module['dirty234']
    assert group.owner not in group.members
    assert not group.members_info


@pytest.mark.parametrize(
    'user_label, expected_status_code',
    [
        pytest.param('user1', 200, id='user1'),
        pytest.param('user2', 200, id='user2'),
        pytest.param('invalid', 401, id='invalid-user'),
        pytest.param(None, 200, id='guest-user'),
    ],
)
def test_get_groups(
    auth_headers,
    client,
    groups_module,
    user_label,
    expected_status_code,
):
    response = perform_get(client, base_url, auth_headers[user_label])
    assert_response(response, expected_status_code)

    if expected_status_code != 200:
        return

    response_groups = UserGroupResponse.model_validate_json(response.content)
    for response_group in response_groups.data:
        group = get_mongo_user_group(response_group.group_id)
        assert_group(group, response_group)


@pytest.mark.parametrize(
    'filters, ref_group_labels',
    [
        # group_id
        pytest.param({'group_id': ['group1']}, ['group1'], id='group1'),
        pytest.param(
            {'group_id': ['group1', 'group2']}, ['group1', 'group2'], id='group1+2'
        ),
        # user_id
        pytest.param(
            {'user_id': 'user1'},
            [f'group{n}' for n in (1, 14, 15, 18, 19, 123, '12m3')],
            id='user1',
        ),
        pytest.param(
            {'user_id': 'user2'},
            ['group2', 'group123', 'group12m3', 'dirty234'],
            id='user2',
        ),
        pytest.param(
            {'user_id': 'user3'},
            ['group3', 'group123', 'group12m3', 'dirty234'],
            id='user3',
        ),
        pytest.param({'user_id': 'user4'}, ['group14', 'dirty234'], id='user4'),
        pytest.param({'user_id': 'user5'}, ['group15'], id='user5'),
        pytest.param({'user_id': 'user6'}, ['group6'], id='user6'),
        pytest.param({'user_id': 'user7'}, [], id='user7'),
        pytest.param({'user_id': 'user8'}, ['group8', 'group18'], id='user8'),
        pytest.param({'user_id': 'user9'}, ['group9', 'group19'], id='user9'),
        pytest.param({'user_id': 'invalid'}, [], id='invalid-user'),
        # search_terms
        pytest.param({'search_terms': 'Uniq'}, ['uniq'], id='uniq'),
        pytest.param({'search_terms': 'iq'}, ['uniq'], id='uniq-partial'),
        pytest.param({'search_terms': 'Twin'}, ['twin1', 'twin2'], id='twins'),
        pytest.param({'search_terms': 'Twin One'}, ['twin1'], id='twin1'),
        pytest.param(
            {'search_terms': 'One'}, ['twin1', 'numerals'], id='twin1-numerals'
        ),
        pytest.param({'search_terms': 'One Two'}, ['numerals'], id='numerals'),
        pytest.param(
            {'search_terms': 'Tw'}, ['twin1', 'twin2', 'numerals'], id='tw-partial'
        ),
        # mixed filters
        pytest.param(
            {'user_id': 'user8', 'search_terms': '1'}, ['group18'], id='user8-term1'
        ),
    ],
)
def test_get_filtered_groups(
    auth_headers,
    client,
    convert_agent_labels_to_ids,
    groups_module,
    filters,
    ref_group_labels,
):
    filters = convert_agent_labels_to_ids(filters)
    response = perform_get(client, base_url, auth_headers['user1'], **filters)
    assert_response(response, 200)

    response_groups = UserGroupResponse.model_validate_json(response.content)
    response_ids = [group.group_id for group in response_groups.data]
    ref_group_ids = convert_agent_labels_to_ids(ref_group_labels)
    assert_unordered_lists(response_ids, ref_group_ids)

    for response_group in response_groups.data:
        group = get_mongo_user_group(response_group.group_id)
        assert_group(group, response_group)


@pytest.mark.parametrize(
    'user_label, expected_status_code',
    [
        pytest.param('user1', 200, id='user1'),
        pytest.param('user2', 200, id='user2'),
        pytest.param('invalid', 401, id='invalid-user'),
        pytest.param(None, 200, id='guest-user'),
    ],
)
def test_get_group(
    auth_headers,
    client,
    groups_module,
    user_label,
    expected_status_code,
):
    user_auth = auth_headers[user_label]
    ref_group = groups_module['group2']

    response = perform_get(client, f'{base_url}/{ref_group.group_id}', user_auth)
    assert_response(response, expected_status_code)

    if expected_status_code != 200:
        return

    response_group = UserGroup.model_validate_json(response.content)
    group = get_mongo_user_group(response_group.group_id)
    assert_group(group, response_group)
    assert_group(group, ref_group)


@pytest.mark.parametrize(
    'user_label, expected_status_code',
    [
        pytest.param('user1', 404, id='user1'),
        pytest.param('user2', 404, id='user2'),
        pytest.param('invalid', 401, id='invalid-user'),
        pytest.param(None, 404, id='guest-user'),
    ],
)
def test_get_group_invalid(
    auth_headers,
    client,
    groups_module,
    user_label,
    expected_status_code,
):
    user_auth = auth_headers[user_label]

    response = perform_get(client, f'{base_url}/invalid-group-id', user_auth)
    assert_response(response, expected_status_code)


# tests using group fixtures with scope: 'function' (default)


def test_owner_not_member(auth_headers, client, group_molds, group_owner_not_member):
    """Old groups do not have owner in the members list and don't use members_info.
    Check that this is fixed automatically on GET and POST."""
    ref_group = group_molds['dirty234_ref']

    group = group_owner_not_member
    assert group.owner not in group.members
    group.reload()
    assert group.owner in group.members
    group.reload_without_clean()
    assert group.owner not in group.members

    # GET returns cleaned group but does not change db
    url = f'{base_url}/{group.group_id}'
    response = perform_get(client, url, auth_headers['user2'])
    assert_response(response, 200)

    response_group = UserGroup.model_validate_json(response.content)
    assert_group(response_group, ref_group, ref_group.keys())

    group.reload_without_clean()
    assert group.owner not in group.members

    # POST cleans group in db and returns it
    url = f'{base_url}/{group.group_id}/edit'
    group_edit = {'group_name': group.group_name}
    response = perform_post(client, url, auth_headers['user2'], json=group_edit)
    assert_response(response, 200)

    response_group = UserGroup.model_validate_json(response.content)
    assert_group(response_group, ref_group, ref_group.keys())

    # not checking if DB was changed, because this is flaky for unknown reasons


@pytest.mark.parametrize(
    'user_label, new_group_label, ref_group_label, expected_status_code',
    [
        pytest.param('user1', 'edit_x23', None, 422, id='no-owner-fails'),
        pytest.param('user1', 'edit_123', 'edit_123_ref', 201, id='user1-ok'),
        pytest.param('user2', 'edit_123', None, 422, id='user2-fails'),
        pytest.param('invalid', 'edit_123', None, 401, id='invalid-user-fails'),
        pytest.param(None, 'edit_123', None, 401, id='guest-user-fails'),
        pytest.param('user1', 'edit_14m5', 'edit_14m5_ref', 201, id='maintained-ok'),
        pytest.param('user1', 'no_name', 'no_name_ref', 201, id='no-name-ok'),
        pytest.param('user1', 'empty_name', None, 422, id='empty-name-fails'),
        pytest.param('user1', 'whitespace_name', None, 422, id='whitespace-name-fails'),
        pytest.param('user1', 'short_name', 'short_name', 201, id='short-name-ok'),
        pytest.param('user1', 'long_name', 'long_name', 201, id='long-name-ok'),
        pytest.param(
            'user1',
            'special_chars_name',
            'special_chars_name',
            201,
            id='special-chars-ok',
        ),
        pytest.param(
            'user1',
            'only_name',
            'only_name_ref',
            201,
            id='only-name-ok',
        ),
        pytest.param(
            'user1',
            'edit_1232',
            None,
            422,
            id='double-member-fails',
        ),
        pytest.param(
            'user1', 'old_edit_x23', 'old_edit_x23_ref1', 201, id='old-user1-ok'
        ),
        pytest.param(
            'user2', 'old_edit_x23', 'old_edit_x23_ref2', 201, id='old-user2-ok'
        ),
        pytest.param('invalid', 'old_edit_x23', None, 401, id='old-invalid-user-fails'),
        pytest.param(None, 'old_edit_x23', None, 401, id='old-guest-user-fails'),
        pytest.param(
            'user1',
            'old_edit_1232',
            'old_edit_1232_ref',
            201,
            id='old-double-member-skipped',
        ),
        pytest.param('user1', 'both_fields', None, 422, id='both-fields-fails'),
    ],
)
def test_create_group(
    auth_headers,
    client,
    mongo_function,
    request,
    group_molds,
    user_label,
    new_group_label,
    ref_group_label,
    expected_status_code,
):
    new_group = group_molds[new_group_label]
    auth = auth_headers[user_label]

    response = perform_post(client, base_url, auth, json=new_group)
    assert_response(response, expected_status_code)

    if response.status_code != 201:
        return

    response_group = UserGroup.model_validate_json(response.content)
    group = get_mongo_user_group(response_group.group_id)
    assert_group(group, response_group)
    ref_group = group_molds[ref_group_label]
    assert_group(group, ref_group, ref_group.keys())


def test_group_description_create_and_edit(
    auth_headers,
    client,
    group_molds,
    mongo_function,
):
    """Group descriptions are persisted and returned on create and edit."""
    group_data = group_molds['group1'].copy()
    group_data.pop('members', None)
    group_data.update(
        group_name='Description group',
        description='Initial group description',
    )

    response = perform_post(client, base_url, auth_headers['user1'], json=group_data)
    assert_response(response, 201)

    created_group = UserGroup.model_validate_json(response.content)
    stored_group = get_mongo_user_group(created_group.group_id)
    assert stored_group.description == 'Initial group description'
    assert created_group.description == 'Initial group description'

    response = perform_post(
        client,
        f'{base_url}/{created_group.group_id}/edit',
        auth_headers['user1'],
        json={'description': 'Updated group description'},
    )
    assert_response(response, 200)

    edited_group = UserGroup.model_validate_json(response.content)
    stored_group = get_mongo_user_group(created_group.group_id)
    assert stored_group.description == 'Updated group description'
    assert edited_group.description == 'Updated group description'


@pytest.mark.parametrize(
    'user_label, group_target_label, group_edit_label, ref_group_label, expected_status_code',
    [
        pytest.param(None, 'group1', 'edit_123', None, 401, id='guest-fails'),
        pytest.param('invalid', 'group1', 'edit_123', None, 401, id='faker-fails'),
        pytest.param('user2', 'group1', 'edit_123', None, 403, id='user2-fails'),
        pytest.param('user1', 'group1', 'edit_123', 'edit_123_ref', 200, id='edit-ok'),
        pytest.param('user1', 'group1', 'no_name', 'no_name_ref', 200, id='no-name-ok'),
        pytest.param('user1', 'group1', 'empty_name', None, 422, id='empty-name-fails'),
        pytest.param(
            'user1', 'group1', 'whitespace_name', None, 422, id='whitespace-name-fails'
        ),
        pytest.param(
            'user1', 'group1', 'short_name', 'short_name', 200, id='short-name-ok'
        ),
        pytest.param(
            'user1', 'group1', 'long_name', 'long_name', 200, id='long-name-ok'
        ),
        pytest.param(
            'user1',
            'group1',
            'special_chars_name',
            'special_chars_name',
            200,
            id='special-chars-ok',
        ),
        pytest.param(
            'user1', 'group1', 'only_name', 'only_name_ref', 200, id='only-name-ok'
        ),
        pytest.param(
            'user2',
            'group12m3',
            'edit_14m5',
            'edit_14m5_ref',
            200,
            id='maintainer-ok',
        ),
        pytest.param(
            'user1',
            'group1',
            'edit_1232',
            None,
            422,
            id='double-member-fails',
        ),
        pytest.param(
            'user1',
            'group1',
            'old_edit_1232',
            'old_edit_1232_ref',
            200,
            id='old-double-member-skipped',
        ),
        pytest.param(
            'user1',
            'group1',
            'old_edit_x23',
            'old_edit_x23_ref1',
            200,
            id='old-user1-ok',
        ),
        pytest.param(
            'user2', 'group1', 'old_edit_x23', None, 403, id='old-user2-fails'
        ),
        pytest.param(
            'invalid', 'group1', 'old_edit_x23', None, 401, id='old-invalid-user-fails'
        ),
        pytest.param(None, 'group1', 'old_edit_x23', None, 401, id='old-guest-fails'),
        pytest.param(
            'user1',
            'group1',
            'old_edit_x123',
            'old_edit_x123_ref',
            200,
            id='old-add-with-owner-ok',
        ),
        pytest.param(
            'user1',
            'group12m3',
            'old_edit_x24',
            'old_edit_x24_ref',
            200,
            id='old-keep-maintainer-ok',
        ),
        pytest.param(
            'user1',
            'group1',
            'both_fields',
            None,
            422,
            id='both-fields-fails',
        ),
    ],
)
def test_edit_group(
    auth_headers,
    client,
    group_molds,
    groups_function,
    user_label,
    group_target_label,
    group_edit_label,
    ref_group_label,
    expected_status_code,
):
    group_before = get_mongo_user_group(groups_function[group_target_label].group_id)
    group_edit = group_molds[group_edit_label]

    url = f'{base_url}/{group_before.group_id}/edit'
    response = perform_post(client, url, auth_headers[user_label], json=group_edit)
    assert_response(response, expected_status_code)
    group_after = get_mongo_user_group(group_before.group_id)

    if response.status_code != 200:
        assert_group(group_after, group_before)
        return

    response_group = UserGroup.model_validate_json(response.content)
    assert_group(group_after, response_group)
    ref_group = group_molds[ref_group_label]
    assert_group(group_after, ref_group, ref_group.keys())


@pytest.mark.parametrize(
    'group_label, user_label, expected_status_code',
    [
        pytest.param('group12m3', 'user1', 204, id='owner-ok'),
        pytest.param('group12m3', 'user2', 403, id='maintainer-fails'),
        pytest.param('group12m3', 'user3', 403, id='member-fails'),
        pytest.param('group12m3', 'invalid', 401, id='invalid-user'),
        pytest.param('group12m3', None, 401, id='guest-user'),
        pytest.param('invalid-group', 'user1', 404, id='invalid-group-user1'),
        pytest.param('invalid-group', 'user2', 404, id='invalid-group-user2'),
        pytest.param('invalid-group', 'user3', 404, id='invalid-group-user3'),
        pytest.param('invalid-group', 'invalid', 401, id='invalid-group-invalid-user'),
        pytest.param('invalid-group', None, 401, id='invalid-group-guest-user'),
    ],
)
def test_delete_group(
    auth_headers,
    client,
    groups_function,
    group_label,
    user_label,
    expected_status_code,
):
    user_auth = auth_headers[user_label]
    if group_label == 'invalid-group':
        target_id = 'invalid-group'
    else:
        target_id = groups_function[group_label].group_id
    do_not_delete_id = groups_function['group123'].group_id

    response = client.delete(f'{base_url}/{target_id}', headers=user_auth)
    assert_response(response, expected_status_code)

    if expected_status_code == 204 or group_label == 'invalid-group':
        assert not user_group_exists(target_id)
    else:
        assert user_group_exists(target_id)

    assert user_group_exists(do_not_delete_id)
