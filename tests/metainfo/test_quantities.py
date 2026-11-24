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
import json
from zoneinfo import ZoneInfo

import numpy as np
import pint
import pytest

from nomad.metainfo.metainfo import (
    JSON,
    URL,
    Bytes,
    Capitalized,
    Datetime,
    Dimension,
    MSection,
    Quantity,
    Unit,
    units,
)
from tests.metainfo import MTypes


@pytest.mark.parametrize(
    'def_type, value',
    [
        pytest.param(str, 'hello', id='str'),
        pytest.param(int, 23, id='int'),
        pytest.param(float, 3.14e23, id='float'),
        pytest.param(complex, 3.14e23 - 2j, id='complex'),
        pytest.param(np.complex128, 3.14e23 - 2j, id='np.complex128'),
        pytest.param(bool, True, id='bool'),
        pytest.param(JSON, dict(key='value'), id='JSON'),
        pytest.param(Unit, units.parse_units('m*m/s'), id='Unit'),
        pytest.param(Dimension, '*', id='Dimension-*'),
        pytest.param(Dimension, 1, id='Dimension-1'),
        pytest.param(Dimension, 'quantity', id='Dimension-quantity'),
        pytest.param(URL, 'http://google.com', id='Url-link'),
        pytest.param(Datetime, datetime.datetime.now(datetime.UTC), id='Datetime'),
        pytest.param(
            Datetime,
            datetime.datetime.now(ZoneInfo('America/Los_Angeles')),
            id='Datetime',
        ),
        pytest.param(Datetime, datetime.date.today(), id='Date'),
        pytest.param(Capitalized, 'Hello', id='Capitalize'),
        pytest.param(Bytes, b'hello', id='Bytes'),
    ],
)
def test_basic_types(def_type, value):
    class TestSectionA(MSection):
        quantity = Quantity(type=def_type)

    section = TestSectionA()
    assert section.quantity is None
    section.quantity = value
    if not isinstance(value, datetime.datetime) and isinstance(value, datetime.date):
        assert section.quantity == datetime.datetime.combine(
            value, datetime.datetime.min.time()
        ).replace(tzinfo=datetime.UTC)
    else:
        assert section.quantity == value

    section_serialized = section.m_to_dict()
    json.dumps(section_serialized)
    section = TestSectionA.m_from_dict(section_serialized)
    if not isinstance(value, datetime.datetime) and isinstance(value, datetime.date):
        assert section.quantity == datetime.datetime.combine(
            value, datetime.datetime.min.time()
        ).replace(tzinfo=datetime.UTC)
    else:
        assert section.quantity == value

    class TestSectionB(MSection):
        quantity = Quantity(type=def_type, default=value)

    section = TestSectionB()
    assert section.quantity == value
    assert 'quantity' not in section.m_to_dict()


@pytest.mark.parametrize(
    'def_type, orig_value, normalized_value',
    [
        pytest.param(Unit, 'm*m/s', units.parse_units('m*m/s'), id='Unit'),
        pytest.param(Datetime, '1970-01-01 01:00:00', None, id='Datetime-str'),
        pytest.param(Datetime, '1970-01-01 01:00+01', None, id='Datetime-str-tz'),
        pytest.param(Datetime, '1970-01-01 01:00:00.0000', None, id='Datetime-str-ms'),
        pytest.param(
            Datetime, 'Wed, 01 Jan 1970 00:00:00 -0100', None, id='Datetime-rfc822'
        ),
        pytest.param(
            Datetime, '1970-01-01T00:00:00Z', None, id='Datetime-aniso861-time'
        ),
        pytest.param(Datetime, '1970-01-01', None, id='Datetime-aniso861-date'),
        pytest.param(
            Datetime,
            '2022-05-19T05:16:32.237914-07:00',
            None,
            id='Datetime-conversion-from-localtime-to-UTC',
        ),
        pytest.param(Capitalized, 'hello', 'Hello', id='Capitalize'),
        pytest.param(URL, 'http://google.com', 'http://google.com', id='URL'),
    ],
)
def test_normalization_string(def_type, orig_value, normalized_value):
    class TestSection(MSection):
        quantity = Quantity(type=def_type)

    section = TestSection()
    assert section.quantity is None
    section.quantity = orig_value
    assert normalized_value is None or section.quantity == normalized_value


@pytest.mark.parametrize(
    'def_type, unit, shape, input, output, valid',
    [
        pytest.param(x, None, [], 1, 1, True, id=f'0D type without unit: {x.__name__}')
        for x in sorted(MTypes.int, key=lambda t: t.__name__)
    ]
    + [
        pytest.param(
            x, None, [], 1.0, 1.0, True, id=f'0D type without unit: {x.__name__}'
        )
        for x in sorted(MTypes.float, key=lambda t: t.__name__)
    ]
    + [
        pytest.param(
            x,
            'm',
            [],
            100 * units('cm'),
            1 * units('m'),
            True,
            id=f'0D type with unit: {x.__name__}',
        )
        for x in sorted(MTypes.int - {int}, key=lambda t: t.__name__)
    ]
    + [
        pytest.param(
            int,
            'inch',
            [],
            100 * units('m'),
            100 * units('m'),
            False,
            id='precision loss: 0D int to int with unit',
        )
    ]
    + [
        pytest.param(
            x,
            'm',
            [],
            100.0 * units('cm'),
            1.0 * units('m'),
            True,
            id=f'0D type with unit: {x.__name__}',
        )
        for x in sorted(MTypes.float, key=lambda t: t.__name__)
    ],
)
def test_normalization_number(def_type, unit, shape, input, output, valid):
    """Numeric quantities with a unit should always return a full pint.Quantity
    that contains both the magnitude and the unit. This way the unit information
    is not lost when using these values in e.g. assignments between two fields.
    """

    def define():
        class TestSection(MSection):
            quantity = Quantity(type=def_type, unit=unit, shape=shape)

        section = TestSection()
        assert section.quantity is None
        section.quantity = input
        assert section.quantity == output

    if not valid:
        with pytest.raises(Exception):
            define()
    else:
        define()


@pytest.mark.parametrize(
    'unit',
    [
        pytest.param('m', id='has-unit'),
        pytest.param(None, id='no-unit'),
    ],
)
@pytest.mark.parametrize(
    'quantity_type,value,shape',
    [
        pytest.param(complex, 1j, None, id='complex-scalar'),
        pytest.param(complex, 1, None, id='complex-from-int'),
        pytest.param(complex, 1.21, None, id='complex-from-float'),
        pytest.param(complex, pint.Quantity(1.242, 'mm'), None, id='complex-from-pint'),
        pytest.param(complex, '1j', None, id='complex-scalar-str'),
        pytest.param(np.complex128, 1j, None, id='np.complex128-scalar'),
        pytest.param(np.complex128, '1j', None, id='np.complex128-scalar-str'),
        pytest.param(complex, [1j, 2j], ['*'], id='complex-list'),
        pytest.param(np.complex128, [1j, 2j], ['*'], id='np.complex128-vector'),
        pytest.param(
            np.complex128, np.array([1j, 2j]), ['*'], id='np.complex128-nparray'
        ),
        pytest.param(np.complex128, ['1j', '2j'], ['*'], id='np.complex128-vector-str'),
    ],
)
def test_complex_number(unit, quantity_type, value, shape):
    class TestSection(MSection):
        quantity = Quantity(type=quantity_type, unit=unit, shape=shape)

    def assert_complex_equal():
        result = section.quantity.m if unit else section.quantity
        if isinstance(value, list | np.ndarray):
            for a, b in zip(result, value):
                assert a == quantity_type(b)
        elif not isinstance(value, pint.Quantity):
            assert result == quantity_type(value)
        elif unit:
            assert result == quantity_type(value.to(unit).magnitude)
        else:
            assert result == quantity_type(value.magnitude)

    def roster(_value):
        if isinstance(value, str):
            for i in 'iIjJ':
                yield _value.replace('j', i)
        if isinstance(_value, list) and isinstance(_value[0], str):
            for i in 'iIjJ':
                yield [_v.replace('j', i) for _v in _value]
        yield _value

    for v in roster(value):
        section = TestSection()
        section.quantity = v
        assert_complex_equal()

        section = TestSection.m_from_dict(section.m_to_dict())
        assert_complex_equal()


@pytest.mark.parametrize(
    'unit',
    [
        pytest.param('m', id='has-unit'),
        pytest.param(None, id='no-unit'),
    ],
)
@pytest.mark.parametrize(
    'quantity_type,value,result,shape',
    [
        pytest.param(complex, {'im': -1}, -1j, None, id='complex-scalar'),
        pytest.param(
            complex, {'re': 1.25}, complex(1.25), None, id='complex-from-float'
        ),
        pytest.param(
            np.complex128,
            {'im': 1},
            np.complex128(1j),
            None,
            id='np.complex128-scalar-im',
        ),
        pytest.param(
            np.complex128,
            {'re': 1.25},
            np.complex128(1.25),
            None,
            id='np.complex128-scalar-re',
        ),
        pytest.param(
            complex, {'re': 1.25, 'im': -2312}, 1.25 - 2312j, None, id='complex-full'
        ),
        pytest.param(
            np.complex128,
            {
                're': [[1, 2, 3], [4, 5, 6]],
                'im': [[1, 2, 3], [4, 5, 6]],
            },  # no shape checking anyway
            np.array([[1, 2, 3], [4, 5, 6]]) + 1j * np.array([[1, 2, 3], [4, 5, 6]]),
            ['*', '*'],
            id='complex-full-2nd',
        ),
    ],
)
def test_complex_number_dict(unit, quantity_type, value, result, shape):
    class TestSection(MSection):
        quantity = Quantity(type=quantity_type, unit=unit, shape=shape)

    def assert_complex_equal():
        quantity = section.quantity.m if unit else section.quantity
        if isinstance(result, np.ndarray):
            assert np.all(quantity == result)
        else:
            assert quantity == result

    section = TestSection()
    section.quantity = value
    assert_complex_equal()

    section = TestSection.m_from_dict(section.m_to_dict())
    assert_complex_equal()


@pytest.mark.parametrize(
    'quantity_type,value',
    [
        pytest.param(np.complex128, np.int64(1), id='downcast-from-int-128'),
        pytest.param(np.complex128, {'re': [1.25, 1], 'im': 1}, id='mismatch-shape'),
        pytest.param(np.complex128, {}, id='empty-dict'),
    ],
)
def test_complex_number_exception(quantity_type, value):
    class TestSection(MSection):
        quantity = Quantity(type=quantity_type)

    section = TestSection()
    with pytest.raises((ValueError, AssertionError)):
        section.quantity = value
