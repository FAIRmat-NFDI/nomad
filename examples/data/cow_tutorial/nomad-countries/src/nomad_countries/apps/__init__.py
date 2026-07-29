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
from nomad.config.models.plugins import AppEntryPoint
from nomad.config.models.ui import (
    App,
    Axis,
    Column,
    Columns,
    Dashboard,
    FilterMenus,
    Filters,
    Layout,
    WidgetHistogram,
    WidgetScatterPlot,
)

schema = 'nomad_countries.schema_packages.country.Country'

country = AppEntryPoint(
    name='Countries of the World app',
    app=App(
        label='Countries of the World',
        path='countries',
        description='Search app for the tutorial data',
        category='Other',
        filters_locked={'section_defs.definition_qualified_name': schema},
        columns=Columns(
            selected=[
                f'data.name#{schema}',
                f'data.population#{schema}',
                f'data.area#{schema}',
                f'data.population_density#{schema}',
                f'data.literacy#{schema}',
                f'data.net_migration#{schema}',
                f'data.infant_mortality#{schema}',
                f'data.birthrate#{schema}',
                f'data.deathrate#{schema}',
            ],
            options={
                f'data.name#{schema}': Column(),
                f'data.population#{schema}': Column(),
                f'data.area#{schema}': Column(),
                f'data.population_density#{schema}': Column(),
                f'data.literacy#{schema}': Column(),
                f'data.net_migration#{schema}': Column(),
                f'data.infant_mortality#{schema}': Column(),
                f'data.birthrate#{schema}': Column(),
                f'data.deathrate#{schema}': Column(),
            },
        ),
        filters=Filters(include=[f'*{schema}']),
        filter_menus=FilterMenus(options={}),
        dashboard=Dashboard(
            widgets=[
                WidgetScatterPlot(
                    layout={'lg': Layout(h=6, w=8, x=0, y=0)},
                    x=Axis(quantity=f'data.literacy#{schema}'),
                    y=Axis(quantity=f'data.industry#{schema}'),
                    size='1000',
                    autorange=True,
                ),
                WidgetScatterPlot(
                    layout={'lg': Layout(h=6, w=8, x=8, y=0)},
                    x=Axis(quantity=f'data.literacy#{schema}'),
                    y=Axis(quantity=f'data.agriculture#{schema}'),
                    size='1000',
                    autorange=True,
                ),
                WidgetScatterPlot(
                    layout={'lg': Layout(h=6, w=8, x=16, y=0)},
                    x=Axis(quantity=f'data.literacy#{schema}'),
                    y=Axis(quantity=f'data.service#{schema}'),
                    size='1000',
                    autorange=True,
                ),
                WidgetHistogram(
                    layout={'lg': Layout(h=3, w=8, x=0, y=6)},
                    quantity=f'data.phones#{schema}',
                    scale='1/2',
                    nbins=30,
                    show_input=False,
                    autorange=True,
                ),
                WidgetHistogram(
                    layout={'lg': Layout(h=3, w=8, x=8, y=6)},
                    quantity=f'data.birthrate#{schema}',
                    scale='linear',
                    nbins=30,
                    show_input=False,
                    autorange=True,
                ),
                WidgetHistogram(
                    layout={'lg': Layout(h=3, w=8, x=16, y=6)},
                    quantity=f'data.net_migration#{schema}',
                    scale='linear',
                    nbins=30,
                    show_input=False,
                    autorange=True,
                ),
            ]
        ),
    ),
)
