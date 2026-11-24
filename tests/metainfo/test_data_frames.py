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

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from pint import Quantity

from nomad.metainfo.data_frames import DataFrameTemplate, ValuesTemplate
from nomad.metainfo.metainfo import Datetime, MEnum, MSection, Package
from nomad.units import ureg

m_package = Package()

# Values
ScalarValue = ValuesTemplate(
    name='Scalar',
    description='Scalar',
    shape=[],
    type=np.float64,
    unit='J',
    iri='',
)

DatetimeValue = ValuesTemplate(
    name='Datetime',
    description='Datetime',
    shape=[],
    type=Datetime,
    unit='',
    iri='',
)

StringValue = ValuesTemplate(
    name='String',
    description='String',
    shape=[],
    type=str,
    unit='',
    iri='',
)

BooleanValue = ValuesTemplate(
    name='Boolean',
    description='Boolean',
    shape=[],
    type=bool,
    unit='',
    iri='',
)

EnumValue = ValuesTemplate(
    name='Enum',
    description='Enum',
    shape=[],
    type=MEnum(['A', 'B', 'C']),
    unit='',
    iri='',
)

# Data frames
GeneralDataFrame = DataFrameTemplate(
    name='GeneralDataFrame',
    mandatory_fields=[],
)

# Examples
Time = ValuesTemplate(
    name='Time',
    type=np.float64,
    shape=[],
    unit='s',
    iri='https://www.wikidata.org/wiki/Q11471',
)

Temperature = ValuesTemplate(
    name='Temperature',
    type=np.float64,
    shape=[],
    unit='K',
    iri='https://www.wikidata.org/wiki/Q11466',
)

Pressure = ValuesTemplate(
    name='Pressure',
    type=np.float64,
    shape=[],
    unit='Pa',
    iri='https://www.wikidata.org/wiki/Q39552',
)

Latitude = ValuesTemplate(
    name='Latitude',
    description='Latitude',
    shape=[],
    type=np.float64,
    unit='deg',
    iri='',
)

Longitude = ValuesTemplate(
    name='Longitude',
    description='Longitude',
    shape=[],
    type=np.float64,
    unit='deg',
    iri='',
)

CauchyStressTensor = ValuesTemplate(
    name='CauchyStressTensor',
    type=np.float64,
    shape=[3, 3],
    unit='Pa',
    iri='https://en.wikipedia.org/wiki/Cauchy_stress_tensor',
)

Stress = DataFrameTemplate(
    name='Stress',
    mandatory_fields=[CauchyStressTensor],
)

ProcessConditions = DataFrameTemplate(
    name='ProcessConditions',
    mandatory_fields=[Temperature, Pressure],
    mandatory_variables=[Time],
)

TemperatureDataFrame = DataFrameTemplate(
    name='Temperature',
    mandatory_fields=[Temperature],
    mandatory_variables=[Longitude, Latitude, Time, StringValue],
)


class MySection(MSection):
    # Values
    datetime_value = DatetimeValue()
    string_value = StringValue()
    boolean_value = BooleanValue()
    enum_value = EnumValue()
    scalar_value = ScalarValue()
    # Data frames
    general_data_frame = GeneralDataFrame()
    # Examples
    process_conditions = ProcessConditions()
    temperature_measurement = TemperatureDataFrame()
    stress = Stress()


m_package.__init_metainfo__()


@pytest.mark.parametrize(
    'values_quantity, input_value, output_value',
    [
        pytest.param(
            'scalar_value',
            1.6e-19,
            ureg.Quantity(1.6e-19, 'J'),
            id='scalar-no-unit',
        ),
        pytest.param(
            'scalar_value',
            ureg.Quantity(1.6e-19, 'J'),
            ureg.Quantity(1.6e-19, 'J'),
            id='scalar-same-unit',
        ),
        pytest.param(
            'scalar_value',
            ureg.Quantity(1.5, 'kcal'),
            ureg.Quantity(6276, 'J'),
            id='scalar-different-unit',
        ),
        pytest.param(
            'string_value',
            'Hello world',
            'Hello world',
            id='string',
        ),
        pytest.param(
            'boolean_value',
            True,
            True,
            id='boolean',
        ),
        pytest.param(
            'enum_value',
            'A',
            'A',
            id='enum',
        ),
        pytest.param(
            'datetime_value',
            datetime.datetime(2021, 1, 1, 12, 0, 0, tzinfo=datetime.UTC),
            datetime.datetime(2021, 1, 1, 12, 0, 0, tzinfo=datetime.UTC),
            id='datetime',
        ),
    ],
)
def test_set_values(values_quantity, input_value, output_value):
    my_section = MySection()
    setattr(my_section, values_quantity, input_value)
    assert getattr(my_section, values_quantity) == output_value


def test_override_values_template():
    unit = 'degree_Celsius'
    description = 'My temperature'

    class Test(MSection):
        temperature = Temperature(unit=unit, description=description)

    assert Test.temperature.unit == unit
    assert Test.temperature.description == description


def test_override_data_frame_template():
    description = 'My DOS'

    class Test(MSection):
        my_data_frame = GeneralDataFrame(description=description)

    assert Test.my_data_frame.description == description


@pytest.mark.parametrize(
    'values_template, create_args, second_value',
    [
        pytest.param(
            ScalarValue,
            (1.6e-19, 1.7e-19),
            ureg.Quantity(1.7e-19, 'J'),
            id='multiple_args',
        ),
        pytest.param(
            ScalarValue, ([1.6e-19, 1.7e-19],), ureg.Quantity(1.7e-19, 'J'), id='list'
        ),
        pytest.param(
            ScalarValue,
            (np.array([1.6e-19, 1.7e-19]),),
            ureg.Quantity(1.7e-19, 'J'),
            id='numpy_array',
        ),
        pytest.param(
            ScalarValue,
            (ureg.Quantity(1.6e-19, 'J'), ureg.Quantity(1.7e-19, 'J')),
            ureg.Quantity(1.7e-19, 'J'),
            id='pint_quantity_args',
        ),
        pytest.param(
            ScalarValue,
            (ureg.Quantity([1.6e-19, 1.7e-19], 'J'),),
            ureg.Quantity(1.7e-19, 'J'),
            id='pint_quantity_array',
        ),
        pytest.param(
            StringValue,
            ('Hello', 'World'),
            'World',
            id='string',
        ),
        pytest.param(
            BooleanValue,
            (True, False),
            False,
            id='boolean',
        ),
        # pytest.param(
        #     EnumValue,
        #     ('A', 'B'),
        #     'B',
        #     id='enum',
        # ),
        # pytest.param(
        #     DatetimeValue,
        #     (
        #         datetime.datetime(2021, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc),
        #         datetime.datetime(2021, 1, 2, 12, 0, 0, tzinfo=datetime.timezone.utc),
        #     ),
        #     datetime.datetime(2021, 1, 2, 12, 0, 0, tzinfo=datetime.timezone.utc),
        #     id='datetime',
        # )
    ],
)
def test_set_data_frame_values(
    values_template: ValuesTemplate, create_args, second_value
):
    my_section = MySection(
        general_data_frame=GeneralDataFrame.create(
            fields=[values_template.create(*create_args)]
        ),
    )
    assert my_section.general_data_frame.fields[0].get_values()[1] == second_value


def test_get_data_frame_values():
    my_section = MySection(
        general_data_frame=GeneralDataFrame.create(
            fields=[ScalarValue.create(1.6e-19, 1.7e-19)]
        ),
    )
    assert my_section.general_data_frame.get_field(ScalarValue).values[
        0
    ] == ureg.Quantity(1.6e-19, 'J')
    assert my_section.general_data_frame.get_field('Scalar_1').values[
        0
    ] == ureg.Quantity(1.6e-19, 'J')


def test_original_shape():
    my_section = MySection(
        stress=Stress.create(
            fields=[CauchyStressTensor.create(np.random.rand(3, 3, 4, 5))],
            variables=[
                Temperature.create(np.random.rand(4)),
                Pressure.create(np.random.rand(5)),
            ],
        ),
    )

    assert my_section.stress.fields[0].original_shape == [3, 3, 4, 5]
    assert my_section.stress.variables[0].original_shape == [4]
    assert my_section.stress.fields[0].values.shape == (3, 3, 20)
    assert my_section.stress.fields[0].get_values().shape == (3, 3, 4, 5)

    my_section_2 = MySection(
        stress=Stress.create(
            fields=[
                CauchyStressTensor.create(
                    np.random.rand(3, 3, 20), original_shape=[3, 3, 4, 5]
                )
            ],
            variables=[
                Temperature.create(np.random.rand(4)),
                Pressure.create(np.random.rand(5)),
            ],
        ),
    )

    assert my_section_2.stress.fields[0].original_shape == [3, 3, 4, 5]


temperature: Quantity = ureg.Quantity([300.0, 320.0, 340.0], 'K')
pressure: Quantity = ureg.Quantity([1e5, 1.2e5, 1.4e5], 'Pa')
time: Quantity = ureg.Quantity([1.0, 2.0, 3.0], 's')


@pytest.mark.parametrize(
    'data_frame, ds',
    [
        pytest.param(
            ProcessConditions.create(
                fields=[
                    Temperature.create(*temperature, name='my_temp'),
                    Pressure.create(*pressure),
                ],
                variables=[Time.create(*time, name='time')],
            ),
            xr.Dataset(
                data_vars=dict(
                    my_temp=(
                        ['time'],
                        temperature,
                        dict(
                            units='kelvin',
                            long_name=None,
                            description=None,
                            iri='https://www.wikidata.org/wiki/Q11466',
                        ),
                    ),
                    Pressure_1=(
                        ['time'],
                        pressure,
                        dict(
                            units='pascal',
                            long_name=None,
                            description=None,
                            iri='https://www.wikidata.org/wiki/Q39552',
                        ),
                    ),
                ),
                coords=dict(
                    time=(
                        ['time'],
                        time,
                        dict(
                            units='second',
                            long_name=None,
                            description=None,
                            iri='https://www.wikidata.org/wiki/Q11471',
                        ),
                    ),
                ),
                attrs=dict(
                    description=None,
                    long_name=None,
                ),
            ),
            id='single-variable, multiple-fields',
        ),
    ],
)
def test_to_xarray(data_frame, ds):
    my_section = MySection(process_conditions=data_frame)
    assert my_section.process_conditions.to_xarray().equals(ds)


@pytest.mark.parametrize(
    'data_frame, df',
    [
        pytest.param(
            ProcessConditions.create(
                fields=[
                    Temperature.create(*temperature, name='my_temp'),
                    Pressure.create(*pressure),
                ],
                variables=[Time.create(*time, name='time')],
            ),
            pd.DataFrame(
                dict(my_temp=temperature.magnitude, Pressure_1=pressure.magnitude),
                index=pd.Index(time.magnitude, name='time'),
            ),
            id='single-variable, multiple-fields',
        ),
    ],
)
def test_to_pandas(data_frame, df):
    my_section = MySection(process_conditions=data_frame)
    assert my_section.process_conditions.to_pandas().equals(df)


def test_multiple_spanned_dimensions():
    np.random.seed(0)
    temperature = 15 + 8 * np.random.randn(2, 2, 3)
    lon = np.array([[-99.83, -99.32], [-99.79, -99.23]])
    lat = np.array([[42.25, 42.21], [42.63, 42.59]])
    # time = pd.date_range('2014-09-06', periods=3)
    # reference_time = pd.Timestamp('2014-09-05')
    time = np.arange(3)
    reference_time = '2014-09-05'

    ds = xr.DataArray(
        data=temperature,
        dims=['x', 'y', 'time'],
        coords=dict(
            lon=(['x', 'y'], lon),
            lat=(['x', 'y'], lat),
            time=time,
            reference_time=reference_time,
        ),
        attrs=dict(
            description='Ambient temperature.',
            units='degC',
        ),
    ).to_dataset(name='temperature')

    my_section = MySection()
    my_section.temperature_measurement = TemperatureDataFrame.create(
        fields=[Temperature.create(temperature)],
        variables=[
            Longitude.create(lon, spanned_dimensions=[0, 1], name='lon'),
            Latitude.create(lat, spanned_dimensions=[0, 1], name='lat'),
            Time.create(time, spanned_dimensions=[2], name='time'),
            StringValue.create(reference_time, name='reference_time'),
        ],
    )

    with pytest.raises(NotImplementedError):
        my_section.temperature_measurement.to_xarray()
