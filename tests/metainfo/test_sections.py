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

# Contains more general test cases that are replaced continuously by more specialized
# in-depth tests in test_* files of the same module.

import pytest

from nomad.metainfo import MSection
from nomad.metainfo.metainfo import Package, Quantity, Section, SubSection


def test_base_section():
    class BaseSection(MSection):
        pass

    class Section(BaseSection):
        pass

    assert Section.m_def.base_sections == [BaseSection.m_def]
    assert BaseSection.m_def.base_sections == []

    assert isinstance(Section(), Section)
    assert isinstance(Section(), BaseSection)


def test_quantity_inheritance():
    class BaseSection(MSection):
        test_quantity = Quantity(type=str)

    class Section(BaseSection):
        pass

    assert 'test_quantity' in Section.m_def.all_properties
    assert Section.test_quantity == BaseSection.test_quantity

    section = Section(test_quantity='test_value')
    assert section.test_quantity == 'test_value'
    assert section.m_to_dict()['test_quantity'] == 'test_value'


def test_quantity_overwrite():
    class BaseSection(MSection):
        test_quantity = Quantity(type=str)

    class Section(BaseSection):
        test_quantity = Quantity(type=int)

    assert 'test_quantity' in Section.m_def.all_properties
    assert Section.m_def.all_properties['test_quantity'] == Section.test_quantity
    assert BaseSection.test_quantity != Section.test_quantity
    assert (
        BaseSection.m_def.all_properties['test_quantity']
        != Section.m_def.all_properties['test_quantity']
    )

    with pytest.raises((TypeError, ValueError)):
        Section(test_quantity='test_value')

    section = Section(test_quantity=1)
    assert section.test_quantity == 1
    assert section.m_to_dict()['test_quantity'] == 1


def test_quantity_partial_overwrite():
    class BaseSection(MSection):
        test_quantity = Quantity(type=str, description='test_description')

    class Section(BaseSection):
        test_quantity = Quantity(type=int)

    assert Section.test_quantity.description == 'test_description'
    assert Section.test_quantity.type.standard_type() == 'int32'


def test_sub_section_inheritance():
    class OtherSection(MSection):
        pass

    class BaseSection(MSection):
        test_sub_section = SubSection(sub_section=OtherSection)

    class Section(BaseSection):
        pass

    assert 'test_sub_section' in Section.m_def.all_properties
    assert Section.test_sub_section == BaseSection.test_sub_section

    section = Section(test_sub_section=OtherSection())
    assert section.test_sub_section.m_def == OtherSection.m_def
    assert section.m_to_dict()['test_sub_section'] == {}


def test_sub_section_overwrite():
    class OtherSection(MSection):
        pass

    class BaseSection(MSection):
        test_sub_section = SubSection(sub_section=OtherSection)

    class Section(BaseSection):
        test_sub_section = SubSection(sub_section=OtherSection, repeats=True)

    assert 'test_sub_section' in Section.m_def.all_properties
    assert Section.m_def.all_properties['test_sub_section'] == Section.test_sub_section
    assert BaseSection.test_sub_section != Section.test_sub_section
    assert (
        BaseSection.m_def.all_properties['test_sub_section']
        != Section.m_def.all_properties['test_sub_section']
    )

    with pytest.raises(TypeError):
        Section(test_sub_section=OtherSection())

    section = Section(test_sub_section=[OtherSection()])
    assert len(section.test_sub_section) == 1
    assert section.m_to_dict()['test_sub_section'] == [{}]


def test_sub_section_partial_overwrite():
    class OtherSection(MSection):
        pass

    class BaseSection(MSection):
        test_sub_section = SubSection(
            sub_section=OtherSection, description='test_description'
        )

    class Section(BaseSection):
        test_sub_section = SubSection(repeats=True)

    assert Section.test_sub_section.description == 'test_description'
    assert Section.test_sub_section.sub_section == OtherSection.m_def
    assert Section.test_sub_section.repeats


def test_overwrite_programmatic():
    class BaseSection(MSection):
        test_quantity = Quantity(type=str, description='test_description')

    section_def = Section(name='Section', base_sections=[BaseSection.m_def])
    section_def.m_add_sub_section(Section.quantities, Quantity(name='test_quantity'))

    # This happens automatically in Python class based section definitions, but
    # has to be called manually in programmatic section definitions.
    section_def.__init_metainfo__()

    section_cls = section_def.section_cls

    assert section_cls.test_quantity.description == 'test_description'
    assert section_cls.test_quantity.type.standard_type() == 'str'


def test_inner_sections():
    class OuterSection(MSection):
        class InnerSection(MSection):
            pass

    class OtherSection(MSection):
        sub_section = SubSection(sub_section=OuterSection.InnerSection)
        reference = Quantity(type=OuterSection.InnerSection)

    m_package = Package(
        name=__name__, section_definitions=[OuterSection.m_def, OtherSection.m_def]
    )

    assert (
        OuterSection.m_def.qualified_name() + '.InnerSection'
        == OuterSection.InnerSection.m_def.qualified_name()
    )
    assert OuterSection.m_def.inner_section_definitions == [
        OuterSection.InnerSection.m_def
    ]
    assert OuterSection.InnerSection.m_def.m_parent == OuterSection.m_def
    assert (
        OuterSection.InnerSection.m_def.m_parent_sub_section
        == Section.inner_section_definitions
    )

    serialized_json = m_package.m_to_dict()
    deserialized_pkg = Package.m_from_dict(serialized_json)
    deserialized_pkg.__init_metainfo__()
    assert (
        deserialized_pkg.section_definitions[0].inner_section_definitions[0].name
        == 'InnerSection'
    )


def test_inner_sections_inheritance():
    class BaseSection(MSection):
        class InnerSection(MSection):
            test_quantity = Quantity(type=int, description='test_description')

        test_sub_section = SubSection(
            sub_section=InnerSection, description='test_description'
        )

    class OuterSection(BaseSection):
        class InnerSection(BaseSection.InnerSection):
            test_quantity = Quantity(type=str, description='overwritten_description')

        test_sub_section = SubSection(sub_section=InnerSection)

    assert OuterSection.test_sub_section.description == 'test_description'
    assert OuterSection.InnerSection.test_quantity.type.standard_type() == 'str'
    assert (
        OuterSection.InnerSection.test_quantity.description == 'overwritten_description'
    )
    assert (
        OuterSection.m_def.qualified_name() + '.InnerSection'
        == OuterSection.InnerSection.m_def.qualified_name()
    )  # pylint: disable=no-member
    assert (
        BaseSection.m_def.qualified_name() + '.InnerSection'
        == BaseSection.InnerSection.m_def.qualified_name()
    )

    section = OuterSection(
        test_sub_section=OuterSection.InnerSection(test_quantity='test_value')
    )
    assert section.test_sub_section.test_quantity == 'test_value'


def test_path():
    class ChildSection(MSection):
        pass

    class EntryArchive(MSection):
        child = SubSection(sub_section=ChildSection.m_def)

    pkg = Package()
    pkg.section_definitions.append(ChildSection.m_def)
    pkg.section_definitions.append(EntryArchive.m_def)
    pkg.__init_metainfo__()

    assert SubSection.used_sections[ChildSection.m_def] == [EntryArchive.child]
    assert ChildSection.m_def.path == 'child'

    from nomad.datamodel.metainfo.workflow import Task, Workflow

    assert Workflow.m_def.path == 'workflow2'
    assert Task.m_def.path == 'workflow2.tasks'
