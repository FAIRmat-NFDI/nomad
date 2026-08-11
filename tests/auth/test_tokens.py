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


import datetime
import time
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
from bson.objectid import ObjectId

from nomad.auth.pat import (
    PAT_PREFIX,
    PATCreationData,
    PATCreationSpec,
    PATQuery,
    PATQuerySpec,
    PATRecord,
    PATRepository,
    PATService,
    PATSortOrder,
    PATState,
    PruneStats,
    pat_service,
    to_record,
)
from nomad.auth.pat import hash_token as _hash_token
from nomad.auth.scopes import Scope
from nomad.common import now
from nomad.config import config
from nomad.mongo.pat import PAT


# Test wrappers delegating to composed pat_service
def create_pat(*args, **kwargs):
    return pat_service.create(*args, **kwargs)


def rotate_pat(*args, **kwargs):
    return pat_service.rotate(*args, **kwargs)


def list_pat(*args, **kwargs):
    return pat_service.list_owned(*args, **kwargs)


def get_pat(*args, **kwargs):
    return pat_service.get(*args, **kwargs)


def revoke_pat(*args, **kwargs):
    return pat_service.revoke(*args, **kwargs)


def prune_pat(*args, **kwargs):
    return pat_service.prune(*args, **kwargs)


def authenticate_pat(*args, **kwargs):
    return pat_service.authenticate(*args, **kwargs)


DEFAULT_PAT_EXPIRES_IN_DAYS = 30
DEFAULT_PAT_TEST_SCOPES = [Scope.UPLOADS_READ]
DEFAULT_PAT_TEST_NAME = 'Test PAT'


def test_domain_record_lifecycle_at_expiry_boundary():
    expiry = now()
    record = PATRecord(
        id='pat-id',
        user_id='user-id',
        name='domain token',
        description=None,
        scopes=(Scope.UPLOADS_READ,),
        token_digest=None,
        created_at=expiry - datetime.timedelta(days=1),
        expired_at=expiry,
    )

    # At exact expiry time, expired_at < time is False, so token is still active
    assert record.is_expired_at(expiry) is False
    assert record.state_at(expiry) is PATState.ACTIVE

    # After expiry, token is expired
    after_expiry = expiry + datetime.timedelta(microseconds=1)
    assert record.is_expired_at(after_expiry) is True
    assert record.state_at(after_expiry) is PATState.EXPIRED


def test_domain_record_revocation_takes_precedence_over_expiry():
    current_time = now()
    record = PATRecord(
        id='pat-id',
        user_id='user-id',
        name='revoked token',
        description=None,
        scopes=(Scope.UPLOADS_READ,),
        token_digest=None,
        created_at=current_time - datetime.timedelta(days=2),
        expired_at=current_time - datetime.timedelta(days=1),
        revoked=True,
        revoked_at=current_time,
    )

    assert record.state_at(current_time) is PATState.REVOKED


def create_test_pat(
    user_id: str,
    name: str = DEFAULT_PAT_TEST_NAME,
    scopes: list[Scope] = DEFAULT_PAT_TEST_SCOPES,
    expires_in_days: int | None = DEFAULT_PAT_EXPIRES_IN_DAYS,
    revoked: bool = False,
    expired: bool = False,
    **updates,
):
    default_name_parts = [DEFAULT_PAT_TEST_NAME]
    if revoked:
        default_name_parts.append('revoked')
    if expired:
        default_name_parts.append('expired')
    resolved_name = ' '.join(default_name_parts) if name is None else name

    result = create_pat(
        user_id=user_id,
        metadata=PATCreationSpec(name=resolved_name, scopes=list(scopes)),
        expires_in_days=expires_in_days,
    )
    pat = PAT.objects.get(id=result.pat.id)

    if revoked:
        updates.setdefault('revoked', True)
        updates.setdefault('revoked_at', now())

    if expired:
        updates.setdefault('expired_at', now() - datetime.timedelta(days=1))

    for field, value in updates.items():
        setattr(pat, field, value)

    if updates:
        pat.save()

    return result


# Test `create`


def test_create_returns_correct_structure(mongo_function):
    """
    Verifies that create() returns the NamedTuple with a raw token
    and a saved DB object.
    """
    result = create_test_pat(
        user_id='user_123',
        expires_in_days=DEFAULT_PAT_EXPIRES_IN_DAYS,
        name='CI/CD Token',
        scopes=DEFAULT_PAT_TEST_SCOPES,
    )

    # Check return type
    assert result.raw_token is not None
    assert isinstance(result.pat, PATRecord)

    # Check raw token format
    assert result.raw_token.startswith(PAT_PREFIX)
    assert len(result.raw_token) > 40  # Prefix + 32 bytes (b64 encoded)

    # Check DB object
    saved_pat = PAT.objects.get(id=result.pat.id)
    assert saved_pat.user_id == 'user_123'
    assert saved_pat.name == 'CI/CD Token'
    assert saved_pat.scopes == list(DEFAULT_PAT_TEST_SCOPES)
    assert saved_pat.updated_at == saved_pat.created_at


def test_create_hashing_security(mongo_function):
    """
    Verify that we NEVER store the raw token, only the hash.
    """
    result = create_test_pat(user_id='u1', expires_in_days=1)

    raw = result.raw_token
    stored_digest = PAT.objects.get(id=result.pat.id).token_digest

    # Raw token should NOT be equal to stored digest
    assert raw != stored_digest

    # Re-hashing the raw token should match the stored digest
    assert _hash_token(raw) == stored_digest


def test_create_expiration_logic(mongo_function):
    """Test standard expiration math."""
    days = 10
    result = create_test_pat(user_id='u1', expires_in_days=days)

    expected_date = now() + datetime.timedelta(days=days)
    # Check if dates are close (within 10 seconds)
    delta = abs((result.pat.expired_at - expected_date).total_seconds())
    assert delta < 10


@pytest.mark.parametrize('expires_in_days', [0, -1])
def test_create_invalid_lifespan(mongo_function, expires_in_days):
    """
    Verifies that the service rejects requests to create tokens
    with a lifespan of 0 or negative days.
    """
    with pytest.raises(ValueError, match='already expired'):
        create_test_pat(
            user_id='u_invalid_life',
            name='incorrect expiration',
            expires_in_days=expires_in_days,
        )


def test_create_requires_nonempty_scopes(mongo_function):
    """Verifies that the service rejects tokens without scopes."""
    with pytest.raises(ValueError, match='At least one scope must be selected'):
        create_pat(
            user_id='u_missing_scopes',
            metadata=PATCreationSpec(name='missing scopes', scopes=[]),
            expires_in_days=30,
        )


@pytest.mark.parametrize(
    'requested_days, expected_match',
    [
        (None, 'Infinite tokens are disabled'),
        (60, 'exceeds the maximum allowed'),
    ],
)
def test_create_exceeds_configurable_lifetime(
    mongo_function, monkeypatch, requested_days, expected_match
):
    """
    Verifies that the service rejects requests that violate the
    configured maximum token lifetime (e.g., requesting infinite or exceeding max).
    """
    monkeypatch.setattr(config.auth, 'pat_max_lifetime', 30)

    with pytest.raises(ValueError, match=expected_match):
        create_test_pat(
            user_id='u_exceeds_life',
            name='configurable expiration',
            expires_in_days=requested_days,
        )


def test_create_within_configurable_lifetime(mongo_function, monkeypatch):
    """
    Verifies that the service successfully creates a token when
    the requested lifetime is within the configured limits.
    """
    monkeypatch.setattr(config.auth, 'pat_max_lifetime', 30)

    result = create_test_pat(
        user_id='u_valid_life', name='valid expiration', expires_in_days=15
    )

    pat_obj, raw_secret = result
    assert pat_obj is not None
    assert raw_secret is not None

    # Test that we can create non-expiring tokens
    monkeypatch.setattr(config.auth, 'pat_max_lifetime', None)
    result = create_test_pat(user_id='u1', expires_in_days=None)

    assert result.pat.expired_at is None


def test_create_exceeds_max_per_user(mongo_function, monkeypatch):
    """
    Verifies that the service raises a ValueError when a user attempts
    to create more tokens than the configured maximum limit.
    """
    # Restrict the maximum number of ative tokens per user to 2
    monkeypatch.setattr(config.auth, 'pat_max_active_per_user', 2)
    user_id = 'u_limit_test'

    # Create the first token (should succeed)
    result_1 = create_test_pat(user_id=user_id)
    assert result_1.pat is not None

    # Create the second token (should succeed - now at the limit)
    result_2 = create_test_pat(user_id=user_id)
    assert result_2.pat is not None

    # Attempt to create a third token
    with pytest.raises(ValueError, match='Maximum number of active'):
        create_test_pat(user_id=user_id)


@pytest.mark.parametrize(
    'scope', [Scope.TOKENS_CREATE, Scope.TOKENS_DELETE, Scope.TOKENS_READ]
)
def test_create_rejects_token_operating_scope(mongo_function, scope):
    """
    Verifies that requesting any tokens scope immediately throws a ValueError.
    """
    with pytest.raises(
        ValueError, match='Personal access tokens are not allowed to operate on PATs'
    ):
        create_test_pat(
            user_id='user_123',
            expires_in_days=DEFAULT_PAT_EXPIRES_IN_DAYS,
            name='Malicious Token',
            scopes=[scope],
        )


def test_create_rejects_token_invalid_scope(mongo_function):
    """
    Verifies that requesting invalid scope immediately throws a ValueError.
    """
    with pytest.raises(ValueError, match='Invalid scope'):
        create_pat(
            user_id='user_123',
            expires_in_days=30,
            metadata=PATCreationSpec(
                name='Invalid Scope Token',
                scopes=[
                    'invalid',
                ],
            ),
        )


# Test `rotate`


def test_rotate_basic_success(mongo_function):
    """
    Test the sucess path:
    1. Old token is revoked.
    2. New token is created with a NEW secret.
    3. Metadata (name, scopes) is copied.
    """
    user_id = 'u_rotate_happy'

    # Create original
    original_res = create_test_pat(
        user_id=user_id, name='CI Token', scopes=DEFAULT_PAT_TEST_SCOPES
    )
    original_id = original_res.pat.id
    original_secret = original_res.raw_token

    # Rotate
    new_res = rotate_pat(user_id=user_id, pat_id=str(original_id))

    # Verify old token is revoked
    old_db = PAT.objects.get(id=original_id)
    assert old_db.revoked is True

    # Verify new token
    assert new_res.pat.id != original_id
    assert new_res.raw_token != original_secret
    assert new_res.pat.name == 'CI Token'
    assert new_res.pat.scopes == tuple(DEFAULT_PAT_TEST_SCOPES)
    assert new_res.pat.is_active is True


def test_rotate_preserves_original_lifespan(mongo_function):
    """
    If one creates a 1-Year token and rotate it 6 months later,
    the new token should be valid for 1 Year.
    """
    user_id = 'u_lifespan'
    lifespan_days = 365

    # Create the token
    original_res = create_test_pat(
        user_id=user_id, name='Yearly', expires_in_days=lifespan_days
    )
    original = original_res.pat

    # Time Travel: Move BOTH dates back by 180 days
    # This simulates a token that was created 6 months ago
    # and was intended to expire 6 months from now.
    time_shift = datetime.timedelta(days=180)

    original_db = PAT.objects.get(id=original.id)
    original_db.created_at -= time_shift
    original_db.expired_at -= time_shift
    original_db.save()

    # Ensure the gap is still 365 days before we rotate
    initial_duration = (original_db.expired_at - original_db.created_at).days
    assert abs(initial_duration - lifespan_days) <= 1

    # Rotate
    new_res = rotate_pat(user_id=user_id, pat_id=str(original.id))

    # Check duration of NEW token
    # It should be a fresh 365 days from now
    new_duration = new_res.pat.expired_at - new_res.pat.created_at
    assert abs(new_duration.days - lifespan_days) <= 1


def test_rotate_infinite_token(mongo_function, monkeypatch):
    """
    Test that rotating a token with NO expiration (None)
    creates a new token with NO expiration.
    """
    monkeypatch.setattr(config.auth, 'pat_max_lifetime', None)

    user_id = 'u_infinite'
    original = create_test_pat(
        user_id=user_id, name='Forever', expires_in_days=None
    ).pat

    new_res = rotate_pat(user_id=user_id, pat_id=str(original.id))

    original_db = PAT.objects.get(id=original.id)
    assert original_db.expired_at is None
    assert new_res.pat.expired_at is None


def test_rotate_wrong_user(mongo_function):
    """
    User A cannot rotate User B's token.
    """
    victim_id = 'u_victim'
    attacker_id = 'u_attacker'

    original = create_test_pat(user_id=victim_id).pat

    # Attacker tries to rotate
    result = rotate_pat(user_id=attacker_id, pat_id=str(original.id))

    # Should fail
    assert result is None

    # Original should NOT be revoked
    original_db = PAT.objects.get(id=original.id)
    assert original_db.revoked is False


def test_rotate_non_existent_invalid(mongo_function):
    """Sanity check for non-existent or invalid PAT ID."""
    assert rotate_pat(user_id='user_1', pat_id=str(ObjectId())) is None
    assert rotate_pat(user_id='user_1', pat_id='invalid') is None


def test_rotate_revoked(mongo_function):
    """Shouldn't be able to rotate revoked token."""
    user_id = 'u_owner'
    # Create a token
    result = create_test_pat(user_id=user_id)
    token_id = result.pat.id

    # Revoke it
    success = revoke_pat(user_id=user_id, pat_id=str(token_id))
    assert PAT.objects.get(id=token_id).revoked is True

    with pytest.raises(ValueError, match='Cannot rotate an expired/revoked token'):
        rotate_pat(user_id=user_id, pat_id=token_id)


def test_rotate_expired(mongo_function):
    """Shouldn't be able to rotate expired token."""
    user_id = 'u_owner'

    result = create_test_pat(user_id=user_id)
    token_id = result.pat.id

    # Manually expire the token
    expired_time = now() - datetime.timedelta(minutes=1)
    PAT.objects(id=token_id).update(set__expired_at=expired_time)

    token = PAT.objects.get(id=token_id)
    assert to_record(token).is_active is False

    current_time = now()
    if token.expired_at.tzinfo is None and current_time.tzinfo is not None:
        current_time = current_time.replace(tzinfo=None)
    assert token.expired_at < current_time

    # Attempt rotation
    with pytest.raises(ValueError, match='Cannot rotate an expired/revoked token'):
        rotate_pat(user_id=user_id, pat_id=token_id)


def test_rotate_at_max_limit(mongo_function, monkeypatch):
    """
    Verifies that rotating a PAT succeeds when the user is at their
    maximum active token limit.
    """
    monkeypatch.setattr(config.auth, 'pat_max_active_per_user', 1)
    user_id = 'u_rotate_limit_test'

    create_result = create_test_pat(user_id=user_id)
    assert create_result.pat is not None

    rotate_pat(user_id=user_id, pat_id=create_result.pat.id)


# Test `list`


def test_list_isolation(mongo_function):
    """
    Test that we only see tokens for the requested user.
    """
    # Create tokens for User A
    create_test_pat(user_id='user_A', name='A1')
    create_test_pat(user_id='user_A', name='A2')

    # Create token for User B
    create_test_pat(user_id='user_B', name='A1')

    # List user A
    tokens_A = list_pat(user_id='user_A')
    assert tokens_A.total == 2
    assert all(t.user_id == 'user_A' for t in tokens_A.data)
    # Ensure digest is dropped by the DB query
    assert all(t.token_digest is None for t in tokens_A.data)

    # List user B
    tokens_B = list_pat(user_id='user_B')
    assert tokens_B.total == 1
    assert tokens_B.data[0].name == 'A1'
    assert tokens_B.data[0].token_digest is None


def test_list_pagination(mongo_function):
    """
    Test that start and limit correctly slice the results while maintaining the total count.
    """
    user_id = 'u_page_test'

    # Create 5 tokens with predictable names
    for i in range(5):
        create_test_pat(user_id=user_id, name=f'Token_{i}')

    # Sort by name_asc so the order is perfectly predictable: 0, 1, 2, 3, 4

    # Page 1: Items 0, 1
    page_1 = list_pat(user_id=user_id, start=0, limit=2, order_by='name_asc')
    assert page_1.total == 5
    assert len(page_1.data) == 2
    assert [t.name for t in page_1.data] == ['Token_0', 'Token_1']

    # Page 2: Items 2, 3
    page_2 = list_pat(user_id=user_id, start=2, limit=2, order_by='name_asc')
    assert page_2.total == 5
    assert len(page_2.data) == 2
    assert [t.name for t in page_2.data] == ['Token_2', 'Token_3']

    # Page 3: Item 4 (Partial page)
    page_3 = list_pat(user_id=user_id, start=4, limit=2, order_by='name_asc')
    assert page_3.total == 5
    assert len(page_3.data) == 1
    assert page_3.data[0].name == 'Token_4'

    # Page 4: Out of bounds
    page_empty = list_pat(user_id=user_id, start=10, limit=2)
    assert page_empty.total == 5
    assert len(page_empty.data) == 0

    # Page 5: limit=0 (count-only request)
    page_count_only = list_pat(user_id=user_id, start=0, limit=0, order_by='name_asc')
    assert page_count_only.total == 5
    assert len(page_count_only.data) == 0


def test_list_pat_filter_search(mongo_function):
    """
    Test filtering by token name search.
    """
    user_id = 'u_test'

    create_test_pat(user_id=user_id, name='Active Token')
    create_test_pat(user_id=user_id, name='Other')

    results = list_pat(user_id=user_id, query=PATQuery(search='active'))

    assert results.total == 1
    assert results.data[0].name == 'Active Token'


def test_list_pat_filter_revoked(mongo_function):
    """
    Test filtering by revoked status.
    """
    user_id = 'u_test'

    create_test_pat(user_id=user_id, name='Revoked Token', revoked=True)
    create_test_pat(user_id=user_id, name='Valid Token')

    results = list_pat(user_id=user_id, query=PATQuery(revoked=True))

    assert results.total == 1
    assert results.data[0].name == 'Revoked Token'


def test_list_pat_filter_state_active(mongo_function):
    """
    Test filtering by active state.
    """
    user_id = 'u_test'
    current_time = now()

    create_test_pat(user_id=user_id, name='Active Token')
    create_test_pat(
        user_id=user_id,
        name='Expired Token',
        expired_at=current_time - datetime.timedelta(days=1),
    )

    results = list_pat(user_id=user_id, query=PATQuery(state='active'))

    assert results.total == 1
    assert results.data[0].name == 'Active Token'


def test_list_pat_filter_state_inactive_expired(mongo_function):
    """
    Test that inactive state matches expired tokens.
    """
    user_id = 'u_test'
    current_time = now()

    create_test_pat(
        user_id=user_id,
        name='Expired Token',
        expired_at=current_time - datetime.timedelta(days=1),
    )
    create_test_pat(user_id=user_id, name='Active Token')

    results = list_pat(user_id=user_id, query=PATQuery(state='inactive'))

    assert results.total == 1
    assert results.data[0].name == 'Expired Token'


def test_list_pat_filter_state_inactive_revoked(mongo_function):
    """
    Test that inactive state matches revoked tokens.
    """
    user_id = 'u_test'

    create_test_pat(user_id=user_id, name='Revoked Token', revoked=True)
    create_test_pat(user_id=user_id, name='Active Token')

    results = list_pat(user_id=user_id, query=PATQuery(state='inactive'))

    assert results.total == 1
    assert results.data[0].name == 'Revoked Token'


@pytest.mark.parametrize(
    ('query_field', 'matching_name'),
    [
        pytest.param('created_before', 'Old Token', id='created_before'),
        pytest.param('created_after', 'New Token', id='created_after'),
    ],
)
def test_list_pat_filter_created_bounds(
    mongo_function, query_field: str, matching_name: str
):
    """
    Test filtering by created_at lower/upper bounds.
    """
    user_id = 'u_test'
    current_time = now()

    create_test_pat(
        user_id=user_id,
        name='Old Token',
        created_at=current_time - datetime.timedelta(days=10),
    )
    create_test_pat(
        user_id=user_id,
        name='New Token',
        created_at=current_time - datetime.timedelta(days=1),
    )

    cutoff = current_time - datetime.timedelta(days=5)
    results = list_pat(
        user_id=user_id,
        query=PATQuery(**{query_field: cutoff}),
    )

    assert results.total == 1
    assert results.data[0].name == matching_name


@pytest.mark.parametrize(
    ('query_field', 'matching_name'),
    [
        pytest.param('last_used_before', 'Old Used Token', id='last_used_before'),
        pytest.param('last_used_after', 'Recent Used Token', id='last_used_after'),
    ],
)
def test_list_pat_filter_last_used_bounds(
    mongo_function, query_field: str, matching_name: str
):
    """
    Test filtering by last_used_at lower/upper bounds.
    """
    user_id = 'u_test'
    current_time = now()

    create_test_pat(
        user_id=user_id,
        name='Old Used Token',
        last_used_at=current_time - datetime.timedelta(days=10),
    )
    create_test_pat(
        user_id=user_id,
        name='Recent Used Token',
        last_used_at=current_time - datetime.timedelta(days=1),
    )

    cutoff = current_time - datetime.timedelta(days=5)
    results = list_pat(
        user_id=user_id,
        query=PATQuery(**{query_field: cutoff}),
    )

    assert results.total == 1
    assert results.data[0].name == matching_name


@pytest.mark.parametrize(
    ('query_field', 'matching_name'),
    [
        pytest.param('expires_before', 'Expired Token', id='expires_before'),
        pytest.param('expires_after', 'Valid Token', id='expires_after'),
    ],
)
def test_list_pat_filter_expires_bounds(
    mongo_function, query_field: str, matching_name: str
):
    """
    Test filtering by expired_at lower/upper bounds.
    """
    user_id = 'u_test'
    current_time = now()

    create_test_pat(
        user_id=user_id,
        name='Expired Token',
        expired_at=current_time - datetime.timedelta(days=1),
    )
    create_test_pat(
        user_id=user_id,
        name='Valid Token',
        expired_at=current_time + datetime.timedelta(days=10),
    )

    results = list_pat(
        user_id=user_id,
        query=PATQuery(**{query_field: current_time}),
    )

    assert results.total == 1
    assert results.data[0].name == matching_name


@pytest.mark.parametrize(
    ('order_by', 'expected_names'),
    [
        pytest.param('created_asc', ['Old', 'New'], id='created_asc'),
        pytest.param('created_desc', ['New', 'Old'], id='created_desc'),
    ],
)
def test_list_pat_order_created(mongo_function, order_by, expected_names: list[str]):
    """
    Test ordering by created_at.
    """
    user_id = 'u_order_created'
    current_time = now()

    create_test_pat(
        user_id=user_id,
        name='Old',
        created_at=current_time - datetime.timedelta(days=1),
    )
    create_test_pat(user_id=user_id, name='New', created_at=current_time)

    results = list_pat(user_id=user_id, order_by=order_by)

    assert results.total == 2
    assert [token.name for token in results.data] == expected_names


@pytest.mark.parametrize(
    ('order_by', 'expected_names'),
    [
        pytest.param('name_asc', ['Apple', 'Zebra'], id='name_asc'),
        pytest.param('name_desc', ['Zebra', 'Apple'], id='name_desc'),
    ],
)
def test_list_pat_order_name(mongo_function, order_by, expected_names: list[str]):
    """
    Test ordering by name.
    """
    user_id = 'u_order_name'

    create_test_pat(user_id=user_id, name='Zebra')
    create_test_pat(user_id=user_id, name='Apple')

    results = list_pat(user_id=user_id, order_by=order_by)

    assert results.total == 2
    assert [token.name for token in results.data] == expected_names


@pytest.mark.parametrize(
    ('order_by', 'expected_names'),
    [
        pytest.param('last_used_asc', ['Old', 'New'], id='last_used_asc'),
        pytest.param('last_used_desc', ['New', 'Old'], id='last_used_desc'),
    ],
)
def test_list_pat_order_last_used(mongo_function, order_by, expected_names: list[str]):
    """
    Test ordering by last_used_at.
    """
    user_id = 'u_order_last_used'
    current_time = now()

    create_test_pat(
        user_id=user_id,
        name='Old',
        last_used_at=current_time - datetime.timedelta(days=1),
    )
    create_test_pat(user_id=user_id, name='New', last_used_at=current_time)

    results = list_pat(user_id=user_id, order_by=order_by)

    assert results.total == 2
    assert [token.name for token in results.data] == expected_names


@pytest.mark.parametrize(
    ('order_by', 'expected_names'),
    [
        pytest.param('expires_asc', ['Soon', 'Later'], id='expires_asc'),
        pytest.param('expires_desc', ['Later', 'Soon'], id='expires_desc'),
    ],
)
def test_list_pat_order_expires(mongo_function, order_by, expected_names: list[str]):
    """
    Test ordering by expired_at.
    """
    user_id = 'u_order_expires'
    current_time = now()

    create_test_pat(
        user_id=user_id,
        name='Soon',
        expired_at=current_time + datetime.timedelta(days=1),
    )
    create_test_pat(
        user_id=user_id,
        name='Later',
        expired_at=current_time + datetime.timedelta(days=10),
    )

    results = list_pat(user_id=user_id, order_by=order_by)

    assert results.total == 2
    assert [token.name for token in results.data] == expected_names


def test_list_empty(mongo_function):
    """Test that a user with no tokens gets an empty list, not None."""
    tokens = list_pat(user_id='ghost_user')
    assert isinstance(tokens.data, list)
    assert tokens.total == 0


# Test `get`


def test_get_own_token_success(mongo_function):
    """
    Test that a user can successfully retrieve their own token.
    """
    user_id = 'u_owner'

    # Create token
    created = create_test_pat(user_id=user_id, name='My Token')
    pat_id = str(created.pat.id)

    # Retrieve it
    retrieved = get_pat(user_id=user_id, pat_id=pat_id)

    # Verify
    assert retrieved is not None
    assert retrieved.id == created.pat.id
    assert retrieved.name == 'My Token'
    assert retrieved.user_id == user_id

    # Digest should be dropped by the DB query
    assert retrieved.token_digest is None


def test_get_others_token_fails(mongo_function):
    """
    Ensure User A cannot fetch User B's token.
    """
    victim_id = 'u_victim'
    attacker_id = 'u_attacker'

    # Victim creates a token
    victim_token = create_test_pat(user_id=victim_id).pat
    pat_id = str(victim_token.id)

    # Attacker tries to get it
    result = get_pat(user_id=attacker_id, pat_id=pat_id)

    assert result is None


def test_get_expired_token(mongo_function):
    """
    Test that an expired token can still be retrieved directly.
    """
    user_id = 'u_expired_get_test'

    created = create_test_pat(user_id=user_id, name='Expired Token')
    pat = created.pat

    # Backdate the expiration date to simulate natural expiration
    pat_db = PAT.objects.get(id=pat.id)
    pat_db.expired_at = pat_db.created_at - datetime.timedelta(days=5)
    pat_db.save()

    pat_id = str(pat.id)

    retrieved = get_pat(user_id=user_id, pat_id=pat_id)

    assert retrieved is not None
    assert retrieved.id == pat.id
    assert retrieved.name == 'Expired Token'
    assert retrieved.user_id == user_id


def test_get_revoked_token(mongo_function):
    """
    Test that can get metadata for a revoked token.
    """
    user_id = 'u_revoked_get_test'

    created = create_test_pat(user_id=user_id, name='Revoked Token')
    pat_id = str(created.pat.id)

    # Revoke it
    revoke_pat(user_id=user_id, pat_id=pat_id)

    # Retrieve it
    retrieved = get_pat(user_id=user_id, pat_id=pat_id)

    assert retrieved is not None
    assert str(retrieved.id) == pat_id
    assert retrieved.name == 'Revoked Token'
    assert retrieved.revoked is True
    assert retrieved.is_active is False


def test_get_non_existent_invalid_token(mongo_function):
    """
    Test retrieving a nonexistent or invalid ID returns None.
    """
    assert get_pat(user_id='any_user', pat_id=str(ObjectId())) is None
    assert get_pat(user_id='any_user', pat_id='123') is None


# Test `revoke`


def test_revoke_success(mongo_function):
    """
    Test that a user can successfully revoke their own token.

    Revocation should mark the token as revoked and record `revoked_at`.
    """
    user_id = 'u_owner'

    # Create a token
    result = create_test_pat(user_id=user_id)
    token_id = result.pat.id
    original_expires_at = result.pat.expired_at

    # Revoke it
    success = revoke_pat(user_id=user_id, pat_id=str(token_id))
    assert success is True

    # Verify database state
    updated_pat = PAT.objects.get(id=token_id)
    assert updated_pat.revoked is True
    assert updated_pat.revoked_at is not None

    # Strip timezones for comparison
    revoked_at_naive = updated_pat.revoked_at.replace(tzinfo=None)
    updated_at_naive = updated_pat.updated_at.replace(tzinfo=None)

    original_expired_naive = original_expires_at.replace(tzinfo=None)
    updated_expired_naive = updated_pat.expired_at.replace(tzinfo=None)
    expire_diff = abs((updated_expired_naive - original_expired_naive).total_seconds())
    assert expire_diff < 0.1  # allow 100 ms drift

    # The revocation time should be roughly equal to `updated_at`
    time_diff = abs((revoked_at_naive - updated_at_naive).total_seconds())
    assert time_diff < 5  # seconds


def test_revoke_wrong_user_security(mongo_function):
    """
    Test that User A cannot revoke User B's token.
    """
    owner_id = 'u_victim'
    attacker_id = 'u_attacker'

    # Create token for victim
    result = create_test_pat(user_id=owner_id)
    token_id = result.pat.id

    # Attacker tries to revoke it
    success = revoke_pat(user_id=attacker_id, pat_id=str(token_id))

    # Should fail
    assert success is False

    # Token should still be active
    updated_pat = PAT.objects.get(id=token_id)
    assert updated_pat.revoked is False


def test_revoke_non_existent_invalid(mongo_function):
    """
    Test revoking a non-existent or invalid ID returns False.
    """
    assert revoke_pat(user_id='user_1', pat_id=str(ObjectId())) is False
    assert revoke_pat(user_id='user_1', pat_id='invalid') is False


def test_revoke_already_revoked(mongo_function, monkeypatch):
    """
    Test that revoking an already revoked token is safe.
    It should return True (because the end state is 'revoked=True')
    and short-circuit without touching the database.
    """
    user_id = 'u_double'
    result = create_test_pat(user_id=user_id)
    token_id = str(result.pat.id)

    # First revoke
    assert revoke_pat(user_id=user_id, pat_id=token_id) is True

    persisted_pat = PAT.objects.get(id=token_id)
    first_updated_at = persisted_pat.updated_at
    first_revoked_at = persisted_pat.revoked_at
    first_expired_at = persisted_pat.expired_at

    assert persisted_pat.revoked is True
    assert first_revoked_at is not None

    mock_save = MagicMock()
    monkeypatch.setattr('nomad.mongo.pat.PAT.save', mock_save)

    # Second revoke (already revoked, should be a no-op)
    assert revoke_pat(user_id=user_id, pat_id=token_id) is True

    # Make sure DB state is not changed
    mock_save.assert_not_called()
    persisted_pat = PAT.objects.get(id=token_id)

    assert persisted_pat.revoked is True
    assert persisted_pat.updated_at == first_updated_at
    assert persisted_pat.revoked_at == first_revoked_at
    assert persisted_pat.expired_at == first_expired_at


# Test `prune_pat`


@pytest.mark.parametrize('dry_run', [True, False])
@pytest.mark.parametrize(
    'cutoff_selector, expired, revoked, expected_expired_matched, expected_revoked_matched, expected_matched, expects_user_filter',
    [
        # Cutoff set via `inactive_for`
        pytest.param(
            'inactive_for',
            True,
            False,
            3,
            0,
            3,
            False,
            id='inactive-for-expired-only',
        ),
        pytest.param(
            'inactive_for',
            False,
            True,
            0,
            2,
            2,
            False,
            id='inactive-for-revoked-only',
        ),
        pytest.param(
            'inactive_for',
            True,
            True,
            3,
            2,
            4,
            False,
            id='inactive-for-expired-and-revoked',
        ),
        # Cutoff set via `inactive_before`
        pytest.param(
            'inactive_before',
            True,
            False,
            2,
            0,
            2,
            True,
            id='inactive-before-expired-only',
        ),
        pytest.param(
            'inactive_before',
            False,
            True,
            0,
            2,
            2,
            True,
            id='inactive-before-revoked-only',
        ),
        pytest.param(
            'inactive_before',
            True,
            True,
            2,
            2,
            3,
            True,
            id='inactive-before-expired-and-revoked',
        ),
    ],
)
def test_prune_pat_filters(
    mongo_function,
    dry_run,
    cutoff_selector,
    expired,
    revoked,
    expected_expired_matched,
    expected_revoked_matched,
    expected_matched,
    expects_user_filter,
):
    current_time = now()
    user_id = f'u_prune_{cutoff_selector}'
    cutoff = (
        current_time - datetime.timedelta(days=7)
        if cutoff_selector == 'inactive_for'
        else current_time - datetime.timedelta(days=20)
    )

    # Setup test PATs
    old_expired = create_test_pat(
        user_id=user_id,
        name='old expired',
        expired_at=cutoff - datetime.timedelta(days=1),
    ).pat
    recent_expired = create_test_pat(
        user_id=user_id,
        name='recent expired',
        expired_at=cutoff + datetime.timedelta(days=1),
    ).pat

    old_revoked = create_test_pat(
        user_id=user_id,
        name='old revoked',
        revoked=True,
        revoked_at=cutoff - datetime.timedelta(days=1),
        expired_at=current_time + datetime.timedelta(days=2),
    ).pat
    recent_revoked = create_test_pat(
        user_id=user_id,
        name='recent revoked',
        revoked=True,
        revoked_at=cutoff + datetime.timedelta(days=1),
        expired_at=current_time + datetime.timedelta(days=10),
    ).pat

    old_expired_and_revoked = create_test_pat(
        user_id=user_id,
        name='old expired+revoked',
        revoked=True,
        revoked_at=cutoff - datetime.timedelta(days=1),
        expired_at=cutoff - datetime.timedelta(days=1),
    ).pat
    active_pat = create_test_pat(user_id=user_id, name='active pat').pat
    other_user = create_test_pat(
        user_id='u_prune_date_other',
        name='other user expired',
        expired_at=cutoff - datetime.timedelta(days=1),
    ).pat

    # Run `prune` and check result
    result = prune_pat(
        dry_run=dry_run,
        inactive_for=datetime.timedelta(days=7)
        if cutoff_selector == 'inactive_for'
        else None,
        inactive_before=cutoff if cutoff_selector == 'inactive_before' else None,
        expired=expired,
        revoked=revoked,
        user_id=user_id if expects_user_filter else None,
    )

    # Check summary counters
    assert result.expired_matched == expected_expired_matched
    assert result.revoked_matched == expected_revoked_matched
    assert result.matched == expected_matched
    assert result.deleted == (0 if dry_run else expected_matched)

    # Old expired/revoked/both tokens
    old_expired_selected = expired
    old_revoked_selected = revoked
    old_expired_and_revoked_selected = expired or revoked

    assert (PAT.objects(id=old_expired.id).first() is not None) is (
        dry_run or not old_expired_selected
    )
    assert (PAT.objects(id=old_revoked.id).first() is not None) is (
        dry_run or not old_revoked_selected
    )
    assert (PAT.objects(id=recent_expired.id).first() is not None) is True
    assert (PAT.objects(id=recent_revoked.id).first() is not None) is True
    assert (PAT.objects(id=old_expired_and_revoked.id).first() is not None) is (
        dry_run or not old_expired_and_revoked_selected
    )
    assert (PAT.objects(id=active_pat.id).first() is not None) is True

    # Another-user's token
    other_user_selected = (not expects_user_filter) and expired
    assert (PAT.objects(id=other_user.id).first() is not None) is (
        dry_run or not other_user_selected
    )


@pytest.mark.parametrize(
    'inactive_for, inactive_before, expired, revoked, error',
    [
        pytest.param(
            datetime.timedelta(days=90),
            now(),
            True,
            True,
            'inactive_before and inactive_for',
            id='both-cutoffs-provided-conflict',
        ),
        pytest.param(
            None,
            None,
            True,
            True,
            'Either inactive_before or inactive_for',
            id='missing-both-cutoff',
        ),
        pytest.param(
            datetime.timedelta(days=-1),
            None,
            True,
            True,
            'inactive_for must be non-negative',
            id='negative-inactive-for',
        ),
        pytest.param(
            datetime.timedelta(days=7),
            None,
            False,
            False,
            'At least one of',
            id='empty-expired-revoked-selection',
        ),
    ],
)
def test_prune_pat_rejects_invalid_selection(
    mongo_function, inactive_for, inactive_before, expired, revoked, error
):
    with pytest.raises(ValueError, match=error):
        prune_pat(
            inactive_before=inactive_before,
            inactive_for=inactive_for,
            expired=expired,
            revoked=revoked,
        )


# Test `authenticate`


def test_authenticate_success(mongo_function):
    """
    A valid raw token should return the PAT object.
    """
    user_id = 'u_auth_success'

    # Create a valid token
    result = create_test_pat(user_id=user_id)
    raw_token = result.raw_token

    # Authenticate
    authenticated_pat = authenticate_pat(raw_token)

    # Verify
    assert authenticated_pat is not None
    assert authenticated_pat.id == result.pat.id
    assert authenticated_pat.user_id == user_id


def test_authenticate_updates_last_used(mongo_function):
    """
    Verify that authenticating successfully updates the 'last_used_at' timestamp.
    """
    # Create token
    result = create_test_pat(user_id='u_usage')

    # Ensure usage is initially None
    assert result.pat.last_used_at is None
    original_updated_at = PAT.objects.get(id=result.pat.id).updated_at

    # Sleep briefly to ensure the timestamp will be different
    time.sleep(0.01)

    # Authenticate
    authenticate_pat(result.raw_token)

    # Reload from DB
    updated_pat = PAT.objects.get(id=result.pat.id)
    assert updated_pat.last_used_at is not None
    assert updated_pat.updated_at == original_updated_at

    current_time = now()

    # Verify it happened "just now"
    assert (current_time - updated_pat.last_used_at).total_seconds() < 1


def test_authenticate_fails_wrong_prefix(mongo_function):
    """
    Strings without prefix should fail immediately.
    """
    assert authenticate_pat('wrong-prefix-token') is None


def test_authenticate_fails_wrong_secret(mongo_function):
    """
    A token with correct prefix but wrong random bytes fails.
    """
    # Create a real token
    real_result = create_test_pat(user_id='u_hacker')

    # Modify the secret slightly
    fake_token = real_result.raw_token[:-1] + 'X'

    # Attempt auth
    assert authenticate_pat(fake_token) is None


def test_authenticate_fails_revoked(mongo_function):
    """
    A revoked token is rejected.
    """
    # Create & Revoke
    result = create_test_pat(user_id='u_revoked')
    revoke_pat(user_id='u_revoked', pat_id=str(result.pat.id))

    pat = authenticate_pat(result.raw_token)
    assert pat is None


def test_authenticate_fails_expired(mongo_function):
    """
    An expired token is rejected.
    """
    result = create_test_pat(user_id='u_expired', expired=True)

    assert authenticate_pat(result.raw_token) is None


# ==============================================================================
# Pure In-Memory Unit Tests (Hexagonal Architecture / Ports & Adapters)
# ==============================================================================


class InMemoryPATRepository(PATRepository):
    def __init__(self):
        self.tokens: dict[str, PATRecord] = {}

    def save_new(self, new_pat: PATCreationData) -> PATRecord:
        import uuid

        pat_id = str(uuid.uuid4())
        record = PATRecord(
            id=pat_id,
            user_id=new_pat.user_id,
            name=new_pat.name,
            description=new_pat.description,
            scopes=tuple(new_pat.scopes),
            token_digest=new_pat.token_digest,
            created_at=new_pat.created_at,
            expired_at=new_pat.expired_at,
            revoked=False,
            revoked_at=None,
            updated_at=new_pat.created_at,
            last_used_at=None,
        )
        self.tokens[pat_id] = record
        return record

    def get_owned(self, *, pat_id: str, user_id: str) -> PATRecord | None:
        token = self.tokens.get(pat_id)
        if token and token.user_id == user_id:
            return token
        return None

    def count_active(self, *, user_id: str, time: datetime.datetime) -> int:
        return sum(
            1
            for t in self.tokens.values()
            if t.user_id == user_id and t.is_active_at(time)
        )

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
        matched = []
        for t in self.tokens.values():
            if t.user_id != user_id:
                continue
            if spec.search and spec.search.lower() not in t.name.lower():
                continue
            if spec.revoked is not None and t.revoked != spec.revoked:
                continue
            if spec.state == 'active' and not t.is_active_at(time):
                continue
            if spec.state == 'inactive' and t.is_active_at(time):
                continue
            if spec.created_after and t.created_at < spec.created_after:
                continue
            if spec.created_before and t.created_at > spec.created_before:
                continue
            if spec.last_used_after and (
                t.last_used_at is None or t.last_used_at < spec.last_used_after
            ):
                continue
            if spec.last_used_before and (
                t.last_used_at is None or t.last_used_at > spec.last_used_before
            ):
                continue
            if spec.expires_after and (
                t.expired_at is None or t.expired_at < spec.expires_after
            ):
                continue
            if spec.expires_before and (
                t.expired_at is None or t.expired_at > spec.expires_before
            ):
                continue
            matched.append(t)

        sort_keys = {
            'created_asc': (lambda t: (t.created_at is None, t.created_at), False),
            'created_desc': (lambda t: (t.created_at is None, t.created_at), True),
            'expires_asc': (lambda t: (t.expired_at is None, t.expired_at), False),
            'expires_desc': (lambda t: (t.expired_at is not None, t.expired_at), True),
            'last_used_asc': (
                lambda t: (t.last_used_at is None, t.last_used_at),
                False,
            ),
            'last_used_desc': (
                lambda t: (t.last_used_at is not None, t.last_used_at),
                True,
            ),
            'name_asc': (lambda t: t.name.lower(), False),
            'name_desc': (lambda t: t.name.lower(), True),
        }

        key_fn, reverse = sort_keys.get(order_by, (lambda t: t.created_at, True))
        matched.sort(key=key_fn, reverse=reverse)

        total = len(matched)
        if limit == 0:
            return [], total

        page = matched[start : (start + limit) if limit is not None else None]
        return [replace(t, token_digest=None) for t in page], total

    def revoke_owned(
        self, *, user_id: str, pat_id: str, revoked_at: datetime.datetime
    ) -> bool:
        token = self.tokens.get(pat_id)
        if token and token.user_id == user_id:
            if token.revoked:
                return True
            updated = replace(
                token, revoked=True, revoked_at=revoked_at, updated_at=revoked_at
            )
            self.tokens[pat_id] = updated
            return True
        return False

    def claim_active_for_rotation(
        self,
        *,
        user_id: str,
        pat_id: str,
        time: datetime.datetime,
        revoked_at: datetime.datetime,
    ) -> PATRecord | None:
        token = self.tokens.get(pat_id)
        if token and token.user_id == user_id and token.is_active_at(time):
            updated = replace(
                token, revoked=True, revoked_at=revoked_at, updated_at=revoked_at
            )
            self.tokens[pat_id] = updated
            return token
        return None

    def find_by_digest(self, digest: str) -> PATRecord | None:
        for t in self.tokens.values():
            if t.token_digest == digest:
                return t
        return None

    def mark_used(self, *, pat_id: str, used_at: datetime.datetime) -> None:
        token = self.tokens.get(pat_id)
        if token:
            updated = replace(token, last_used_at=used_at)
            self.tokens[pat_id] = updated

    def prune(
        self,
        *,
        cutoff: datetime.datetime,
        expired: bool,
        revoked: bool,
        user_id: str | None,
        dry_run: bool,
    ) -> PruneStats:
        matched = 0
        deleted = 0
        expired_matched = 0
        revoked_matched = 0
        to_delete = []

        for pat_id, t in list(self.tokens.items()):
            if user_id is not None and t.user_id != user_id:
                continue
            is_exp = t.expired_at is not None and t.expired_at <= cutoff
            is_rev = t.revoked and t.revoked_at is not None and t.revoked_at <= cutoff

            matches_expired = expired and is_exp
            matches_revoked = revoked and is_rev

            if matches_expired or matches_revoked:
                matched += 1
                if matches_expired:
                    expired_matched += 1
                if matches_revoked:
                    revoked_matched += 1
                if not dry_run:
                    to_delete.append(pat_id)
                    deleted += 1

        for pat_id in to_delete:
            del self.tokens[pat_id]

        return PruneStats(
            matched=matched,
            deleted=deleted,
            expired_matched=expired_matched,
            revoked_matched=revoked_matched,
        )


def test_in_memory_create_enforces_max_active_tokens():
    repo = InMemoryPATRepository()
    fixed_time = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    service = PATService(repository=repo, clock=lambda: fixed_time)

    user_id = 'user_in_memory'
    metadata = PATCreationSpec(name='Token 1', scopes=[Scope.UPLOADS_READ])

    # We can create up to config.auth.pat_max_active_per_user (default is 10)
    for i in range(config.auth.pat_max_active_per_user):
        service.create(
            user_id=user_id,
            metadata=PATCreationSpec(name=f'Token_{i}', scopes=[Scope.UPLOADS_READ]),
            expires_in_days=10,
        )

    # Creating one more should raise ValueError
    with pytest.raises(ValueError, match='Maximum number of active PAT'):
        service.create(user_id=user_id, metadata=metadata, expires_in_days=10)


def test_in_memory_rotate_concurrent_revocation_failure():
    repo = InMemoryPATRepository()
    fixed_time = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    service = PATService(repository=repo, clock=lambda: fixed_time)

    user_id = 'user_in_memory'
    result = service.create(
        user_id=user_id,
        metadata=PATCreationSpec(name='RotateMe', scopes=[Scope.UPLOADS_READ]),
        expires_in_days=10,
    )
    token_id = result.pat.id

    # Mock claim_active_for_rotation to return None (simulating concurrent rotation/revocation)
    def mock_claim(*args, **kwargs):
        return None

    repo.claim_active_for_rotation = mock_claim

    with pytest.raises(ValueError, match='Cannot rotate an expired/revoked token'):
        service.rotate(user_id=user_id, pat_id=token_id)


def test_in_memory_token_digest_redacted():
    repo = InMemoryPATRepository()
    fixed_time = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    service = PATService(repository=repo, clock=lambda: fixed_time)

    user_id = 'user_in_memory'
    result = service.create(
        user_id=user_id,
        metadata=PATCreationSpec(name='SecretToken', scopes=[Scope.UPLOADS_READ]),
        expires_in_days=10,
    )

    # Assert public output redacts token_digest
    assert result.pat.token_digest is None

    # Assert repo actually stores token_digest
    stored_token = repo.tokens[result.pat.id]
    assert stored_token.token_digest is not None
    assert stored_token.token_digest == _hash_token(result.raw_token)


def test_in_memory_list_owned_filters_and_ordering():
    repo = InMemoryPATRepository()
    t0 = datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    service = PATService(repository=repo, clock=lambda: t0)

    user_id = 'user_search'
    service.create(
        user_id=user_id,
        metadata=PATCreationSpec(name='Alpha Token', scopes=[Scope.UPLOADS_READ]),
        expires_in_days=10,
    )
    res_beta = service.create(
        user_id=user_id,
        metadata=PATCreationSpec(name='Beta Token', scopes=[Scope.UPLOADS_READ]),
        expires_in_days=20,
    )
    service.create(
        user_id=user_id,
        metadata=PATCreationSpec(name='Gamma Token', scopes=[Scope.UPLOADS_READ]),
        expires_in_days=30,
    )

    # Revoke beta
    service.revoke(user_id=user_id, pat_id=res_beta.pat.id)

    # Test search filter
    search_res = service.list_owned(
        user_id=user_id,
        query=PATQuery(search='alpha'),
    )
    assert search_res.total == 1
    assert search_res.data[0].name == 'Alpha Token'

    # Test state=active filter
    active_res = service.list_owned(
        user_id=user_id,
        query=PATQuery(state='active'),
    )
    assert active_res.total == 2
    assert {t.name for t in active_res.data} == {'Alpha Token', 'Gamma Token'}

    # Test state=inactive filter
    inactive_res = service.list_owned(
        user_id=user_id,
        query=PATQuery(state='inactive'),
    )
    assert inactive_res.total == 1
    assert inactive_res.data[0].name == 'Beta Token'

    # Test sorting by name asc / desc
    name_asc = service.list_owned(user_id=user_id, order_by='name_asc')
    assert [t.name for t in name_asc.data] == [
        'Alpha Token',
        'Beta Token',
        'Gamma Token',
    ]

    name_desc = service.list_owned(user_id=user_id, order_by='name_desc')
    assert [t.name for t in name_desc.data] == [
        'Gamma Token',
        'Beta Token',
        'Alpha Token',
    ]
