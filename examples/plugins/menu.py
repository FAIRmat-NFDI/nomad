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
from nomad.config.models.ui import Axis, Menu, MenuItemHistogram, MenuItemTerms

# This is a top level menu that is always visible. It shows two items: a terms
# item and a submenu beneath it.
menu = Menu(
    size='sm',
    items=[
        MenuItemTerms(search_quantity='authors.name', options=5),
        # This is a submenu whose items become visible once selected. It
        # contains three items: one full-width histogram and two terms items
        # which are displayed side-by-side.
        Menu(
            title='Submenu',
            size='md',
            items=[
                MenuItemHistogram(x=Axis(search_quantity='upload_create_time')),
                # These items target data from a custom schema
                MenuItemTerms(
                    width=6,
                    search_quantity='data.quantity1#nomad_example.schema_packages.mypackage.MySchema',
                ),
                MenuItemTerms(
                    width=6,
                    search_quantity='data.quantity2#nomad_example.schema_packages.mypackage.MySchema',
                ),
            ],
        ),
    ],
)
