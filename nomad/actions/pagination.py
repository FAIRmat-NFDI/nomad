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

"""Action cursor encoding, shared by the application query use cases."""

import base64
from datetime import datetime, timezone

_CURSOR_DT_FMT = '%Y-%m-%dT%H:%M:%S.%f+00:00'


def _encode_cursor(dt: datetime) -> str:
    """
    Encode a datetime as an opaque, base64url cursor string.

    The cursor encodes the ``created_at`` timestamp of the *last item on the
    current page*.  The next query will return documents whose ``created_at``
    is strictly less than this value, giving stable forward-only pagination
    even as new documents are inserted at the head of the collection.
    """
    # Beanie/Mongo can return naive datetimes when tz-awareness is disabled;
    # treat those values as UTC to avoid timezone-shifted cursors.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # Always work in UTC so the encoded string is unambiguous.
    utc_dt = dt.astimezone(timezone.utc)
    token = utc_dt.strftime(_CURSOR_DT_FMT)
    return base64.urlsafe_b64encode(token.encode()).decode()


def _decode_cursor(cursor: str) -> datetime:
    """
    Decode a cursor string produced by :func:`_encode_cursor`.

    Raises ``ValueError`` when the token is not a valid base64url string or
    does not decode to the expected timestamp format.
    """
    try:
        token = base64.urlsafe_b64decode(cursor.encode()).decode()
        return datetime.strptime(token, _CURSOR_DT_FMT).replace(tzinfo=timezone.utc)
    except Exception as exc:
        raise ValueError(f'Invalid pagination cursor: {cursor!r}') from exc
