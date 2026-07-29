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
import numpy as np
import pandas as pd
import plotly.express as px
from nomad.datamodel import Schema
from nomad.datamodel.metainfo.plot import PlotlyFigure, PlotSection
from nomad.metainfo import MSection, Package, Quantity, SubSection

m_package = Package(name='Countries of the World')


class Timeseries(MSection):
    year = Quantity(type=np.int32, shape=['*'])
    value = Quantity(type=np.float64, shape=['*'])


class Country(PlotSection, Schema):
    name = Quantity(type=str)
    population = Quantity(type=np.int32)
    area = Quantity(type=np.float64, unit='km^2')
    population_density = Quantity(type=np.float64, unit='1/km^2')
    coastline = Quantity(type=np.float64, description='cost/area ratio')
    net_migration = Quantity(type=np.float64)
    infant_mortality = Quantity(type=np.float64, description='per 1000 births')
    literacy = Quantity(
        type=np.float64, description='Literacy in % of adult population'
    )
    phones = Quantity(type=np.float64, description='Phones per 1,000 people')
    birthrate = Quantity(type=np.float64, description='per 1,000 people per year')
    deathrate = Quantity(type=np.float64, description='per 1,000 people per year')
    agriculture = Quantity(type=np.float64)
    industry = Quantity(type=np.float64)
    service = Quantity(type=np.float64)

    gdp = SubSection(
        section=Timeseries, description='GDP per capita (constant 2005 US$)'
    )
    birth_rate = SubSection(section=Timeseries, description='per 1,000 people per year')

    def normalize(self, archive, logger):
        super(Country, self).normalize(archive, logger)
        archive.metadata.entry_name = self.name
        self.population_density = self.population / self.area

        self.figures.append(
            PlotlyFigure(
                figure=px.line(
                    pd.DataFrame(dict(year=self.gdp.year, GDP=self.gdp.value)),
                    x='year',
                    y=['GDP'],
                ).to_plotly_json()
            )
        )


m_package.__init_metainfo__()
