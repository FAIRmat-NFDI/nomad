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
from mongoengine import Document, StringField

from nomad.mongo.fields import UTCDateTimeField


@pytest.mark.parametrize(
    'value',
    [
        pytest.param(datetime.datetime(2026, 1, 1, 12, 0, 0), id='naive'),
        pytest.param(
            datetime.datetime(2026, 1, 1, 12, 0, 0, tzinfo=datetime.UTC),
            id='aware-utc',
        ),
    ],
)
def test_utc_datetime_field_is_timezone_aware_on_read(mongo_function, value):
    class _UTCFieldDoc(Document):
        meta = {'collection': 'utc_field_doc_test'}
        name = StringField()
        dt = UTCDateTimeField()

    doc = _UTCFieldDoc(
        name='tz-test',
        dt=value,
    )
    doc.save()

    loaded = _UTCFieldDoc.objects(id=doc.id).first()
    assert loaded is not None
    assert loaded.dt is not None
    assert loaded.dt.tzinfo is not None
