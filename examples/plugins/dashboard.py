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
from nomad.config.models.ui import (
    Axis,
    Dashboard,
    Layout,
    WidgetHistogram,
    WidgetPeriodicTable,
    WidgetScatterPlot,
    WidgetTerms,
)

schema = 'nomad_example.schema_packages.mypackage.MySchema'

dashboard = Dashboard(
    widgets=[
        WidgetPeriodicTable(
            title='Elements of the material',
            layout={
                'lg': Layout(h=8, minH=8, minW=12, w=12, x=0, y=0),
            },
            search_quantity='results.material.elements',
            scale='linear',
        ),
        WidgetTerms(
            title='Widget Terms Title',
            layout={
                'lg': Layout(h=8, minH=3, minW=3, w=6, x=12, y=0),
            },
            search_quantity=f'data.mysection.myquantity#{schema}',
            showinput=True,
            scale='linear',
        ),
        WidgetHistogram(
            title='Histogram Title',
            show_input=False,
            autorange=True,
            nbins=30,
            scale='linear',
            x=Axis(search_quantity=f'data.mysection.myquantity#{schema}'),
            layout={'lg': Layout(minH=3, minW=3, h=8, w=12, y=8, x=0)},
        ),
        WidgetScatterPlot(
            title='Scatterplot title',
            autorange=True,
            layout={
                'lg': Layout(h=8, minH=3, minW=8, w=12, x=12, y=8),
            },
            x=Axis(
                search_quantity=f'data.mysection.mynumericalquantity#{schema}',
                title='quantity x',
            ),
            y=Axis(search_quantity=f'data.mysection.myothernumericalquantity#{schema}'),
            color=f'data.mysection.myquantity#{schema}',  # optional, if set has to be scalar value
            size=1000,  # maximum number of entries loaded
        ),
    ]
)
