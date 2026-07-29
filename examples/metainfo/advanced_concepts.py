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

from nomad.metainfo import MSection
from nomad.metainfo.metainfo import Package, Quantity, SectionProxy, SubSection

m_package = Package(name='advanced_metainfo_example')


class BaseSection(MSection):
    notes = Quantity(
        type=str,
        description='Additional notes about this section in Markdown.',
        links=['https://markdown.org'],
        # 'format' does not exist in metainfo schemas, but will be added as 'more'
        format='Markdown',
    )

    # Section can have inner section definitions. Those will be mapped to 'inner_section_definitions'
    # in the schema.
    class User(MSection):
        # The class doc string will be used as Section schema description
        """A section for basis user information."""

        first_name = Quantity(type=str)
        last_name = Quantity(type=str)
        # 'optional' does not exist in metainfo schemas, but will be added add 'more'
        email = Quantity(type=str, format='email', optional=True)

    authors = SubSection(
        # You can use inner sections as usual.
        section_def=User,
        description='The user that authored this section.',
        # Repeats controlles if the multiplicity in instances. Here authors is defined as
        # a list of User objects.
        repeats=True,
    )


# Section classes can be sub-classed, the base class section will be added as 'base_sections'
# in the schema.
class ApplicationSection(BaseSection):
    # Inner section definitions can be sub-classed as well.
    class User(BaseSection.User):
        user_id = Quantity(type=int)
        # 'email' is already defined in the base section. All schema properties of email
        # are inherited, so you do not have to repeat the type or other properties.
        email = Quantity(
            # 'deprecated' is an actual metainfo property.
            # Can be added to all definitions. It is a string that describes how to
            # deal with the deprecation propery.
            deprecated='Use user_id as a replacement'
        )

    # ApplicationSection.User only defined a special version of BaseSection.User. The
    # inherited sub section 'authors' would sill use BaseSection.User, we have to
    # overwrite the section schema that the sub section is using with the new definition.
    authors = SubSection(section_def=User)

    data = SubSection(section_def=SectionProxy('ApplicationData'), repeats=True)


class ApplicationData(MSection):
    name = Quantity(type=str)
    value = Quantity(type=str)


if __name__ == '__main__':
    print(
        'Schema --------------------\n',
        json.dumps(m_package.m_to_dict(with_meta=False), indent=2),
    )

    archive = ApplicationSection(
        notes='Some example data about artifical movie life',
        authors=[
            ApplicationSection.User(
                user_id=1, first_name='Sandor', last_name='Brockhauser'
            )
        ],
    )

    archive.data.append(ApplicationData(name='robot', value='THX-1138'))
    archive.data.append(ApplicationData(name='droid', value='C-3PO'))

    print(
        'Data ----------------------\n',
        json.dumps(archive.m_to_dict(with_meta=False), indent=2),
    )
