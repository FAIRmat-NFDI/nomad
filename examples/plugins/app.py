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
    Dashboard,
    Layout,
    Menu,
    MenuItemHistogram,
    MenuItemPeriodicTable,
    MenuItemTerms,
    SearchQuantities,
    WidgetHistogram,
)

schema = 'nomad_example.schema_packages.mypackage.MySchema'
myapp = AppEntryPoint(
    name='MyApp',
    description='App defined using the new plugin mechanism.',
    app=App(
        # Label of the App
        label='My App',
        # Path used in the URL, must be unique
        path='myapp',
        # Used to categorize apps in the explore menu
        category='Theory',
        # Brief description used in the app menu
        description='An app customized for me.',
        # Longer description that can also use markdown
        readme='Here is a much longer description of this app.',
        # If you want to use quantities from a custom schema, you need to load
        # the search quantities from it first here. Note that you can use a glob
        # syntax to load the entire package, or just a single schema from a
        # package.
        search_quantities=SearchQuantities(
            include=['*#nomad_example.schema_packages.mypackage.MySchema'],
        ),
        # Controls which columns are shown in the results table
        columns=[
            Column(quantity='entry_id', selected=True),
            Column(quantity=f'data.section.myquantity#{schema}', selected=True),
            Column(
                quantity=f'data.my_repeated_section[*].myquantity#{schema}',
                selected=True,
            ),
            Column(quantity='upload_create_time'),
        ],
        # Dictionary of search filters that are always enabled for queries made
        # within this app. This is especially important to narrow down the
        # results to the wanted subset. Any available search filter can be
        # targeted here. This example makes sure that only entries that use
        # MySchema are included.
        filters_locked={'section_defs.definition_qualified_name': [schema]},
        # Controls the menu shown on the left
        menu=Menu(
            title='Material',
            items=[
                Menu(
                    title='elements',
                    items=[
                        MenuItemPeriodicTable(
                            quantity='results.material.elements',
                        ),
                        MenuItemTerms(
                            quantity='results.material.chemical_formula_hill',
                            width=6,
                            options=0,
                        ),
                        MenuItemTerms(
                            quantity='results.material.chemical_formula_iupac',
                            width=6,
                            options=0,
                        ),
                        MenuItemHistogram(
                            x='results.material.n_elements',
                        ),
                    ],
                )
            ],
        ),
        # Controls the default dashboard shown in the search interface
        dashboard=Dashboard(
            widgets=[
                WidgetHistogram(
                    title='Histogram Title',
                    show_input=False,
                    autorange=True,
                    nbins=30,
                    scale='linear',
                    x=Axis(search_quantity=f'data.mysection.myquantity#{schema}'),
                    layout={'lg': Layout(minH=3, minW=3, h=4, w=12, y=0, x=0)},
                )
            ]
        ),
    ),
)
