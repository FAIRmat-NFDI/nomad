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

import pytest

from nomad.auth.pat import hash_token as _hash_token
from nomad.auth.pat import to_record
from nomad.common import now
from nomad.config import config
from nomad.mongo.pat import PAT


def test_pat_ttl_index_configuration(mongo_function):
    """
    Verifies that the MongoDB TTL index is configured correctly based on the app config.
    """
    # Force MongoEngine to build indexes
    PAT.ensure_indexes()

    # Retrieve raw index information
    index_info = PAT._get_collection().index_information()

    # Search for the TTL index on the 'expired_at' field
    ttl_index = None
    for index_name, details in index_info.items():
        keys = details.get('key', [])
        if any(field_name == 'expired_at' for field_name, _ in keys):
            ttl_index = details
            break

    # Verify the index exists and configuration matches
    assert ttl_index is not None, "TTL index on 'expired_at' was not created!"

    assert 'expireAfterSeconds' in ttl_index, 'Index is missing the TTL property!'
    assert ttl_index['expireAfterSeconds'] == config.auth.pat_pruning_time * 86400


# Test `is_expired` via to_record


def test_pat_is_expired_true(mongo_function):
    """
    Test that a token with a past expiration date is correctly flagged as expired.
    """
    pat = PAT(
        user_id='u_past',
        name='Past Token',
        token_digest=_hash_token('digest_past'),
        expired_at=now() - datetime.timedelta(hours=1),
        revoked=False,
    )
    pat.save()

    assert to_record(pat).is_expired is True


@pytest.mark.parametrize(
    'expired_at',
    [
        pytest.param(now() + datetime.timedelta(hours=1), id='future_date'),
        pytest.param(None, id='no_expiration'),
    ],
)
def test_pat_is_expired_false(mongo_function, expired_at):
    """
    Test that a token with a future expiration date or NO expiration date is not expired.
    """
    pat = PAT(
        user_id='u_not_expired',
        name='Not Expired Token',
        token_digest=_hash_token('digest_not_expired_test'),
        expired_at=expired_at,
        revoked=False,
    )
    pat.save()

    assert to_record(pat).is_expired is False


def test_pat_is_expired_none(mongo_function):
    """
    Test that a token with no expiration date is never expired.
    """
    pat = PAT(
        user_id='u_forever',
        name='Forever Token',
        token_digest=_hash_token('digest_forever'),
        expired_at=None,
        revoked=False,
    )
    pat.save()

    assert to_record(pat).is_expired is False


# Test `is_active` via to_record


def test_pat_is_active_active(mongo_function):
    """
    Test a standard active token.
    """
    pat = PAT(
        user_id='u_happy',
        name='Valid Token',
        token_digest=_hash_token('digest_valid'),
        expired_at=now() + datetime.timedelta(hours=1),
        revoked=False,
    )
    pat.save()

    assert to_record(pat).is_active is True


def test_pat_is_active_revoked(mongo_function):
    """
    Test that a revoked token is inactive, even if the date is fine.
    """
    pat = PAT(
        user_id='u_revoked',
        name='Revoked Token',
        token_digest=_hash_token('digest_revoked'),
        expired_at=now() + datetime.timedelta(hours=1),
        revoked=True,
    )
    pat.save()

    assert to_record(pat).is_active is False


def test_pat_is_active_expired(mongo_function):
    """
    Test that an expired token is inactive.
    """
    pat = PAT(
        user_id='u_expired',
        name='Expired Token',
        token_digest=_hash_token('digest_expired'),
        expired_at=now() - datetime.timedelta(seconds=1),
        revoked=False,
    )
    pat.save()

    assert to_record(pat).is_active is False


def test_pat_is_active_no_expiration(mongo_function):
    """
    Test that a token with NO expiration date (None) is active.
    """
    pat = PAT(
        user_id='u_forever',
        name='Forever Token',
        token_digest=_hash_token('digest_forever'),
        expired_at=None,
        revoked=False,
    )
    pat.save()

    assert to_record(pat).is_active is True
