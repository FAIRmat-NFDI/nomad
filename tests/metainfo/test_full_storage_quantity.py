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

import pytest

from nomad.metainfo import (
    Attribute,
    MetainfoError,
    MSection,
    Quantity,
    Section,
    SubSection,
)
from nomad.metainfo.util import MQuantity
from nomad.units import ureg


class SectionA(MSection):
    plain = Quantity(type=float)
    full = Quantity(
        type=float,
        unit='m',
        dimensionality='[length]',
        attributes=[Attribute(name='gender', type=str)],
    )
    VARIABLE_game = Quantity(
        type=str,
        variable=True,
        attributes=[
            Attribute(name='year', type=int),
            Attribute(name='aka', type=str, shape=['1..3']),
        ],
    )
    VARIABLE_measurement = Quantity(
        type=float,
        unit='m',
        variable=True,
    )
    a_attribute = Attribute(type=str)


class SectionB(MSection):
    out_plain = Quantity(type=int)
    b_attribute = Attribute(type=str)
    subsection = SubSection(section=SectionA.m_def)


def test_full_storage_quantity():
    a_section = SectionA()
    b_section = SectionB()

    a_section.plain = 1.0
    assert a_section.plain == 1.0

    # wrong dimensionality
    with pytest.raises(MetainfoError):
        a_section.full = ureg.Quantity('2*s')

    a_section.full = ureg.Quantity('2*cm')
    assert a_section.full == ureg.Quantity('0.02*m')

    # for variadic quantity, it is not allowed to set the value directly
    with pytest.raises(MetainfoError):
        a_section.gta5_game = 'gta5'

    # need to wrap it in a MQuantity with the correct name
    a_section.gta5_game = MQuantity.wrap('gta5', 'gta5_game')
    assert a_section.gta5_game == 'gta5'

    # possible to use variadic name to set the value
    a_section.VARIABLE_game = MQuantity.wrap('gta3', 'gta3_game')
    assert a_section.gta3_game == 'gta3'

    # wrong type but implicitly convertible
    a_section.m_set_quantity_attribute('full', 'gender', 0)
    assert a_section.m_get_quantity_attribute('full', 'gender') == '0'

    a_section.m_set_quantity_attribute('full', 'gender', 'Male')
    assert a_section.m_get_quantity_attribute('full', 'gender') == 'Male'

    a_section.m_set_quantity_attribute('gta5_game', 'year', 2013)
    assert a_section.m_get_quantity_attribute('gta5_game', 'year') == 2013

    a_section.m_set_quantity_attribute('gta3_game', 'year', 2001)
    assert a_section.m_get_quantity_attribute('gta3_game', 'year') == 2001

    # shape error
    # with pytest.raises(MetainfoError):
    #     a_section.m_set_quantity_attribute(
    #         'gta3_game', 'aka', ['rockstar games', 'gta', 'gta 3', 'GTA3']
    #     )

    a_section.m_set_quantity_attribute(
        'gta3_game', 'aka', ['rockstar games', 'gta', 'gta 3']
    )
    a_section.m_set_section_attribute('a_attribute', 'easy')

    assert a_section.m_get_quantity_attribute('gta3_game', 'aka') == [
        'rockstar games',
        'gta',
        'gta 3',
    ]
    assert a_section.m_get_section_attribute('a_attribute') == 'easy'

    b_section.subsection = a_section

    json = a_section.m_to_dict(with_out_meta=True)

    assert json == SectionA.m_from_dict(json).m_to_dict(with_out_meta=True)

    json = b_section.m_to_dict(with_out_meta=True)
    assert json == SectionB.m_from_dict(json).m_to_dict(with_out_meta=True)

    json = a_section.m_def.m_to_dict(with_out_meta=True)
    assert json == Section.m_from_dict(json).m_to_dict(with_out_meta=True)

    json = b_section.m_def.m_to_dict(with_out_meta=True)
    assert json == Section.m_from_dict(json).m_to_dict(with_out_meta=True)


def test_full_storage_quantity_with_unit_generic_access():
    """A variable=True quantity that also declares a unit must not crash when
    accessed via its definition without naming a specific stored instance --
    the access pattern used by definition-driven code such as
    create_searchable_quantity, which walks quantity definitions rather than
    known runtime instance names.
    """
    a_section = SectionA()
    a_section.VARIABLE_measurement = MQuantity.wrap(
        ureg.Quantity('2*cm'), 'width_measurement'
    )
    a_section.VARIABLE_measurement = MQuantity.wrap(
        ureg.Quantity('3*cm'), 'height_measurement'
    )

    # resolved access via the real instance name still attaches the unit
    assert a_section.width_measurement == ureg.Quantity('0.02*m')
    assert a_section.height_measurement == ureg.Quantity('0.03*m')

    # generic access via the definition (no known instance name) must return
    # the raw {name: MQuantity} dict rather than crash trying to build a
    # pint Quantity out of it
    value = a_section.m_get(SectionA.VARIABLE_measurement)
    assert isinstance(value, dict)
    assert set(value.keys()) == {'width_measurement', 'height_measurement'}
