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
import json

import numpy as np

from nomad.metainfo.data_frames import DataFrameTemplate, ValuesTemplate
from nomad.metainfo.metainfo import MSection, Package, Quantity, SubSection

m_package = Package()

Energy = ValuesTemplate(
    name='Energy',
    type=np.float64,
    shape=[],
    unit='J',
    iri='https://www.wikidata.org/wiki/Q11379',
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

Count = ValuesTemplate(
    name='Count',
    type=np.int64,
    shape=[],
    unit='1',
    iri='https://www.wikidata.org/wiki/Q1520033',
)

BandGap = DataFrameTemplate(
    name='BandGap',
    mandatory_fields=[Energy],
)

Dos = DataFrameTemplate(
    name='Dos',
    mandatory_fields=[Count],
    mandatory_variables=[Energy],
)

m_package.__init_metainfo__()


class MySection(MSection):
    band_gaps = BandGap()


my_section = MySection()
my_section.band_gaps = BandGap.create()
my_section.band_gaps.fields = [Energy.create(1.0, 1.1)]
my_section.band_gaps.variables = [Temperature.create(200, 220)]


# If really necessary, you can specialize the template generated section class,
# but generally we would like to incentivise that users use the containing section
# to do this.
class MyBandGap(BandGap.section_cls):  # type: ignore
    type = Quantity(type=str)

    def normalize(self, archive, logger):
        pass


class MySection(MSection):
    band_gap = Energy()  # Instantiate the Energy values template, creates a quantity
    band_gaps = (
        BandGap()
    )  # Instantiate the BandGap data frame template, creates a sub section
    my_band_gaps = SubSection(section=MyBandGap)
    dos = Dos()


# Value template instances (quantities) are used like quantities
my_section = MySection()
my_section.band_gap = 1.0

# Example of a "heatmap" scenario
my_section = MySection()
my_section.band_gaps = BandGap.create()
my_section.band_gaps.fields = [
    Energy.create(np.array([[1.0, 1.1], [1.3, 1.4], [1.6, 1.7]]))
]
my_section.band_gaps.variables = [
    Temperature.create(200, 220),
    Pressure.create(1e5, 1.2e5, 1.4e5),
]

# Example of a "scatter plot" scenario
my_section = MySection()
my_section.band_gaps = BandGap.create()
my_section.band_gaps.fields = [Energy.create(1.0, 1.1, 1.2)]
my_section.band_gaps.variables = [
    Temperature.create(200, 220, 240, spanned_dimensions=[0]),
    Pressure.create(1e5, 1.2e5, 1.4e5, spanned_dimensions=[0]),
]

# Explicitly spanned dimensions
my_section = MySection()
my_section.band_gaps = BandGap.create()
my_section.band_gaps.fields = [
    Energy.create(np.array([[1.0, 1.1], [1.3, 1.4], [1.6, 1.7]]))
]
my_section.band_gaps.variables = [
    Temperature.create(200, 220, spanned_dimensions=[0]),
    Pressure.create(1e5, 1.2e5, 1.4e5, spanned_dimensions=[1]),
]

# You can also reference values instead of setting them directly
my_section.dos = Dos.create(
    fields=[Count.create(1, 2, 2, 4)],
    variables=[
        Energy.create(1.0, 1.1),
        Temperature.create(my_section.band_gaps.get_variable(Temperature)),
    ],
)

# If you have a specialized template section section its a normal sub section
# and the interface is a bit different
my_band_gaps = MyBandGap(type='foo')
my_section.band_gaps.fields = [Energy.create(1.0, 1.1)]
my_section.band_gaps.variables = [
    Temperature.create(200, 220, spanned_dimensions=[0]),
    Pressure.create(1e5, 1.2e5, spanned_dimensions=[0]),
]

# Access references values
print('###', my_section.dos.get_variable(Temperature).get_values())

# Run the constraints to validate field, variables, and dimensions
my_section.m_all_validate()

print('---- schema ----')
print(json.dumps(m_package.m_to_dict(), indent=2))
print('---- data ----')
print(json.dumps(my_section.m_to_dict(), indent=2))
