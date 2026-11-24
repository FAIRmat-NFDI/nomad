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

from datetime import UTC

import pytest
from pydantic import BaseModel

from nomad.models.common import UTCDateTime


@pytest.mark.parametrize(
    'value',
    [
        pytest.param('2026-01-02T03:04:05', id='naive-string'),
        pytest.param('2026-01-02T03:04:05+00:00', id='aware-string'),
    ],
)
def test_utc_datetime_field_alias_normalizes(value):
    class _Model(BaseModel):
        value: UTCDateTime

    model = _Model(value=value)
    assert model.value.tzinfo is not None
    assert model.value.utcoffset() == UTC.utcoffset(model.value)
