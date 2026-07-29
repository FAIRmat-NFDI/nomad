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
# This example reads all Python define metainfo and computes
# basic metrics on how many sections and properties are defined
# per package

from nomad.datamodel import all_metainfo_packages
from nomad.metainfo import Package, Property, Quantity, Section

all_sections = 0
all_properties = 0
all_quantities = 0
sections = dict()
properties = dict()
quantities = dict()
definitions = set()
metainfo = all_metainfo_packages()

for definition, _, _, _ in metainfo.m_traverse():
    if definition in definitions:
        continue
    definitions.add(definition)

    package = definition
    while package and not isinstance(package, Package):
        package = package.m_parent
    if package is None:
        continue

    if isinstance(definition, Section):
        all_sections += 1
        sections[package] = sections.get(package, 0) + 1
    if isinstance(definition, Property):
        all_properties += 1
        properties[package] = properties.get(package, 0) + 1
    if isinstance(definition, Quantity):
        all_quantities += 1
        quantities[package] = quantities.get(package, 0) + 1


for package, section in sections.items():
    print(
        f'{package.name}: {section}, {properties.get(package, 0)}, {quantities.get(package, 0)}'
    )

print(f'SUM: {all_sections}, {all_properties}, {all_quantities}')
