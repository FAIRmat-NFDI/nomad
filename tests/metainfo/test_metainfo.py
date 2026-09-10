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

import json
import math
from datetime import datetime

import jsonschema
import numpy as np
import pandas as pd
import pint
import pytest

from nomad.datamodel.hdf5 import HDF5Dataset, HDF5Reference
from nomad.datamodel.metainfo.eln import ELNAnnotation
from nomad.metainfo import (
    Annotation,
    AnnotationModel,
    DefinitionAnnotation,
    SectionAnnotation,
)
from nomad.metainfo.data_type import JSON, Bytes, Datetime
from nomad.metainfo.example import SCC, Parsing, Run, System, SystemHash, VaspRun
from nomad.metainfo.example import m_package as example_package
from nomad.metainfo.metainfo import (
    Definition,
    DeriveError,
    MEnum,
    MetainfoError,
    MSection,
    Package,
    Quantity,
    Reference,
    Section,
    SectionProxy,
    SubSection,
    derived,
)
from nomad.metainfo.util import SCHEMA_ENDPOINT
from nomad.units import ureg
from tests.metainfo import MTypes


def assert_section_def(section_def: Section):
    assert isinstance(section_def, Section)
    assert section_def.m_def is not None
    assert isinstance(section_def.m_def, Section)
    assert section_def.m_def.name is not None
    assert section_def.m_def.m_def == Section.m_def

    assert section_def.name is not None


def assert_section_instance(section: MSection):
    assert section.m_def is not None
    assert_section_def(section.m_def)

    if section.m_parent is not None:
        assert section.m_parent_sub_section is not None
        assert (
            section.m_parent.m_get_sub_section(
                section.m_parent_sub_section, section.m_parent_index
            )
            == section
        )


class TestM3:
    """Test for meta-info definition that are used to define other definitions."""

    def test_section(self):
        assert Section.m_def == Section.m_def.m_def
        assert Section.m_def.name == 'Section'
        assert Section.name is not None
        assert Section.name == Definition.name
        assert Section.name.m_def == Quantity.m_def
        assert Section.description.description is not None

        for quantity in iter(Section.m_def.quantities):
            assert quantity.name in Section.m_def.all_properties
            assert quantity.name in Section.m_def.all_quantities
            assert quantity.m_parent == Section.m_def

        for sub_section in iter(Section.m_def.sub_sections):
            assert sub_section.name in Section.m_def.all_properties
            assert sub_section.name in Section.m_def.all_sub_sections
            assert sub_section.sub_section in Section.m_def.all_sub_sections_by_section
            assert sub_section.m_parent == Section.m_def

        assert 'quantities' in Section.m_def.all_sub_sections
        assert 'sub_sections' in Section.m_def.all_sub_sections

        assert_section_instance(Section.m_def)

    def test_quantity(self):
        assert Quantity.m_def.m_def == Section.m_def
        assert Quantity.m_def.name == 'Quantity'

        assert_section_instance(Quantity.m_def)

    def test_definition(self):
        assert len(Section.m_def.base_sections) == 1
        assert len(Section.m_def.all_base_sections) == 1
        assert Section.m_def.m_follows(Definition.m_def)


class TestPureReflection:
    """Test for using meta-info instances without knowing/using the respective definitions."""

    def test_instantiation(self):
        test_section_def = Section(name='TestSection')
        test_section_def.m_create(Quantity, name='test_quantity')

        obj = MSection(m_def=test_section_def)
        assert obj.m_def.name == 'TestSection'
        # FIXME assert obj.m_get('test_quantity') is None
        setattr(obj, 'test_quantity', 'test_value')
        assert getattr(obj, 'test_quantity') == 'test_value'


class TestM2:
    """Test for meta-info definitions."""

    def test_basics(self):
        assert_section_def(Run.m_def)
        assert_section_def(System.m_def)

    def test_default_section_def(self):
        """A section class without an explicit section def must set a default section def."""
        assert Run.m_def is not None
        assert Run.m_def.name == 'Run'

    def test_quantities(self):
        assert len(Run.m_def.extending_sections) == 1
        assert len(Run.m_def.all_quantities) == 3
        assert Run.m_def.all_quantities['code_name'] in Run.m_def.quantities
        assert Run.m_def.all_quantities['code_name'] == Run.__dict__['code_name']

    def test_sub_sections(self):
        assert len(Run.m_def.sub_sections) == 3
        assert Run.m_def.all_sub_sections['systems'] in Run.m_def.sub_sections
        assert Run.m_def.all_sub_sections['systems'].sub_section == System.m_def
        assert len(Run.m_def.all_sub_sections_by_section[System.m_def]) == 1
        assert (
            Run.m_def.all_sub_sections_by_section[System.m_def][0].sub_section
            == System.m_def
        )

    def test_unset_sub_section(self):
        run = Run()
        assert run.systems == []  # pylint: disable=use-implicit-booleaness-not-comparison
        assert run.parsing is None

    def test_properties(self):
        assert len(Run.m_def.all_properties) == 6

    def test_get_quantity_def(self):
        assert System.n_atoms == System.m_def.all_properties['n_atoms']

    def test_section_name(self):
        assert Run.m_def.name == 'Run'

    def test_quantity_name(self):
        assert Run.code_name.name == 'code_name'

    def test_section_description(self):
        assert Run.m_def.description is not None
        assert Run.m_def.description.strip() == Run.m_def.description.strip()

    def test_quantity_description(self):
        assert Run.code_name.description is not None
        assert Run.code_name.description == 'The name of the code that was run.'
        assert Run.code_name.description.strip() == Run.code_name.description.strip()

    def test_direct_category(self):
        assert len(System.atom_labels.categories) == 1
        assert SystemHash.m_def in System.atom_labels.categories
        assert System.atom_labels in SystemHash.m_def.definitions

    def test_package(self):
        assert example_package.name == 'nomad.metainfo.example'
        assert example_package.description == 'An example metainfo package.'
        assert example_package.m_sub_section_count(Package.section_definitions) == 5
        assert example_package.m_sub_section_count(Package.category_definitions) == 1
        assert len(example_package.all_definitions) == 6

    def test_base_sections(self):
        assert Definition.m_def in iter(Section.m_def.base_sections)
        assert 'name' in Section.m_def.all_quantities
        assert 'name' in Quantity.m_def.all_quantities
        assert Quantity.m_def in Definition.m_def.all_inheriting_sections

    def test_unit(self):
        assert System.lattice_vectors.unit is not None

    @pytest.mark.parametrize(
        'unit, serialized, deserialized',
        [
            pytest.param(
                'delta_degC',
                'delta_degree_Celsius',
                'delta_degree_Celsius',
                id='explicit delta full, non-multiplicative, string',
            ),
            pytest.param(
                'ΔdegC',
                'delta_degree_Celsius',
                'delta_degree_Celsius',
                id='explicit delta short, non-multiplicative, string',
            ),
            pytest.param(
                'delta_degC / hr',
                'delta_degree_Celsius / hour',
                'delta_degree_Celsius / hour',
                id='explicit delta full, multiplicative, string',
            ),
            pytest.param(
                'ΔdegC / hr',
                'delta_degree_Celsius / hour',
                'delta_degree_Celsius / hour',
                id='explicit delta short, multiplicative, string',
            ),
            pytest.param(
                ureg.delta_degC / ureg.hour,
                'delta_degree_Celsius / hour',
                'delta_degree_Celsius / hour',
                id='explicit delta, multiplicative, objects',
            ),
            pytest.param(
                'degC / hr',
                'delta_degree_Celsius / hour',
                'delta_degree_Celsius / hour',
                id='implicit delta, multiplicative, string',
            ),
            pytest.param(
                'degC',
                'degree_Celsius',
                'degree_Celsius',
                id='no delta, non-multiplicative',
            ),
            pytest.param(
                ureg.parse_units('degC / hr', as_delta=False),
                'degree_Celsius / hour',
                'delta_degree_Celsius / hour',
                id='non-delta units ignored in deserialization 1',
            ),
            pytest.param(
                ureg.degC / ureg.hour,
                'degree_Celsius / hour',
                'delta_degree_Celsius / hour',
                id='non-delta units ignored in deserialization 2',
            ),
        ],
    )
    def test_unit_delta(self, unit, serialized, deserialized):
        quantity = Quantity(type=np.dtype(np.float64), unit=unit)
        serialized_dict = quantity.m_to_dict()
        deserialized_obj = quantity.m_from_dict(serialized_dict)

        assert serialized_dict['unit'] == serialized
        assert str(deserialized_obj.unit) == deserialized

    @pytest.mark.parametrize(
        'dtype', [pytest.param(np.longlong), pytest.param(np.ulonglong)]
    )
    def test_unsupported_type(self, dtype):
        with pytest.raises(MetainfoError):
            Quantity(type=dtype)

    def test_extension(self):
        assert getattr(Run, 'x_vasp_raw_format', None) is not None
        assert 'x_vasp_raw_format' in Run.m_def.all_quantities

    def test_constraints(self):
        assert len(Run.m_def.constraints) > 0

    def test_unique_names(self):
        class TestBase(MSection):
            name = Quantity(type=str)

        # this is possible, can overwrite existing quantity
        class TestSection(TestBase):  # pylint: disable=unused-variable
            name = Quantity(type=int)

        pkg = Package(section_definitions=[TestBase.m_def, TestSection.m_def])
        pkg.init_metainfo()

    def test_unique_names_extends(self):
        class TestBase(MSection):
            name = Quantity(type=str)

        class TestSection(TestBase):  # pylint: disable=unused-variable
            m_def = Section(extends_base_section=True)
            name = Quantity(type=int)

        pkg = Package(section_definitions=[TestBase.m_def, TestSection.m_def])

        # this is not possible, cant replace existing quantity
        with pytest.raises(MetainfoError):
            pkg.init_metainfo()

    def test_alias(self):
        class SubTest(MSection):
            pass

        class Test(MSection):
            one = Quantity(type=str, aliases=['two', 'three'])
            section_one = SubSection(sub_section=SubTest, aliases=['section_two'])

        t = Test()
        t.one = 'value 1'
        assert t.one == 'value 1'
        assert t.two == 'value 1'
        assert t.three == 'value 1'

        t.two = 'value 2'
        assert t.one == 'value 2'
        assert t.two == 'value 2'
        assert t.three == 'value 2'

        sub_section = t.m_create(SubTest)
        assert t.section_one == sub_section
        assert t.section_two == sub_section

    def test_underscore_prefixed_alias_resolves(self):
        class Test(MSection):
            value = Quantity(type=str, aliases=['_value'])

        t = Test()
        t._value = 'x'
        assert t.value == 'x'

    def test_multiple_sub_sections(self):
        class TestSection(MSection):  # pylint: disable=unused-variable
            one = SubSection(sub_section=System)
            two = SubSection(sub_section=System)

        assert len(TestSection.m_def.all_sub_sections_by_section[System.m_def]) == 2

    def test_dimension_exists(self):
        class TestSection(MSection):  # pylint: disable=unused-variable
            test = Quantity(type=str, shape=['does_not_exist'])

        pkg = Package(section_definitions=[TestSection.m_def])
        pkg.init_metainfo()
        assert len(pkg.warnings) > 0

    def test_dimension_is_int(self):
        class TestSection(MSection):  # pylint: disable=unused-variable
            dim = Quantity(type=str)
            test = Quantity(type=str, shape=['dim'])

        pkg = Package(section_definitions=[TestSection.m_def])
        pkg.init_metainfo()
        assert len(pkg.warnings) > 0

    def test_dimension_is_shapeless(self):
        class TestSection(MSection):  # pylint: disable=unused-variable
            dim = Quantity(type=int, shape=[1])
            test = Quantity(type=str, shape=['dim'])

        pkg = Package(section_definitions=[TestSection.m_def])
        pkg.init_metainfo()
        assert len(pkg.warnings) > 0

    # TODO
    @pytest.mark.skip(
        reason=(
            'We disabled the constraint that is tested here, because some Nexus definitions '
            'are violating it.'
        )
    )
    def test_higher_shapes_require_dtype(self):
        class TestSection(MSection):  # pylint: disable=unused-variable
            test = Quantity(type=int, shape=[3, 3])

        with pytest.raises(MetainfoError):
            pkg = Package(section_definitions=[TestSection.m_def])
            pkg.init_metainfo()

    def test_only_extends_one_base(self):
        with pytest.raises(MetainfoError):

            class TestSection(Run, System):  # pylint: disable=unused-variable
                m_def = Section(extends_base_section=True)

    def test_extending_sections_registration(self):
        class TestBase(MSection):
            base_value = Quantity(type=str)

        class TestExtension(TestBase):
            m_def = Section(extends_base_section=True)
            extension_value = Quantity(type=str)

        pkg = Package(section_definitions=[TestBase.m_def, TestExtension.m_def])
        pkg.init_metainfo()
        pkg.init_metainfo()  # should be deduplicated

        assert TestExtension.m_def not in TestBase.m_def.inheriting_sections
        assert TestBase.m_def.extending_sections == [TestExtension.m_def]

    def test_qualified_name(self):
        assert System.m_def.qualified_name() == 'nomad.metainfo.example.System'

    def test_derived_virtual(self):
        assert System.n_atoms.virtual

    def test_annotations(self):
        class TestSectionAnnotation(SectionAnnotation):
            def init_annotation(self, definition):
                super().init_annotation(definition)
                section_cls = definition.section_cls
                assert definition.name == 'TestSection'
                assert 'test_quantity' in definition.all_quantities
                assert section_cls.test_quantity.m_get_annotations('test').initialized
                assert section_cls.test_quantity.a_test.initialized
                assert section_cls.test_quantity.m_get_annotations(
                    'test', as_list=True
                )[0].initialized
                assert section_cls.test_quantity.m_get_annotations(
                    Annotation
                ).initialized
                assert all(a.initialized for a in section_cls.list_test_quantity.a_test)
                assert all(
                    a.initialized
                    for a in section_cls.list_test_quantity.m_get_annotations(
                        Annotation
                    )
                )
                self.initialized = True

            def new(self, section):
                return dict(test='test annotation')

        class TestDefinitionAnnotation(DefinitionAnnotation):
            def init_annotation(self, definition):
                super().init_annotation(definition)
                assert definition.name in [
                    'test_quantity',
                    'list_test_quantity',
                    'test_sub_section',
                ]
                assert definition.m_parent is not None
                self.initialized = True

        class TestSection(MSection):
            m_def = Section(a_test=TestSectionAnnotation())

            test_quantity = Quantity(type=str, a_test=TestDefinitionAnnotation())
            list_test_quantity = Quantity(
                type=str,
                a_test=[TestDefinitionAnnotation(), TestDefinitionAnnotation()],
            )

            test_sub_section = SubSection(
                sub_section=System, a_test=TestDefinitionAnnotation()
            )

        assert TestSection.m_def.a_test.initialized
        assert TestSection.m_def.m_get_annotations(TestSectionAnnotation).initialized
        assert TestSection().a_test == 'test annotation'

        assert TestSection.test_quantity.a_test is not None
        assert (
            len(
                TestSection.list_test_quantity.m_get_annotations(
                    TestDefinitionAnnotation
                )
            )
            == 2
        )
        assert TestSection.test_sub_section.a_test is not None

    @pytest.mark.parametrize(
        'annotation, passes',
        [
            pytest.param(dict(string='test_value'), True, id='passes-string'),
            pytest.param(dict(integer=1), True, id='passes-int'),
            pytest.param(dict(integer='string'), False, id='fails'),
        ],
    )
    def test_annotation_models(self, annotation, passes):
        class TestAnnotation(AnnotationModel):
            string: str = 'default'
            integer: int = 0
            no_default: str | None = None

        AnnotationModel.m_registry['test'] = TestAnnotation

        if passes:
            section_def = Section(name='test', a_test=annotation)
            assert isinstance(section_def.a_test, TestAnnotation)
            assert section_def.a_test.m_definition is not None
            as_dict = section_def.m_to_dict(with_meta=True)
            assert 'm_definition' not in as_dict
            section_def = Section.m_from_dict(as_dict)
            assert isinstance(section_def.a_test[0], TestAnnotation)
            assert section_def.a_test[0].m_definition is not None
        else:
            section_def = Section(name='test', a_test=annotation)
            assert isinstance(section_def.a_test, AnnotationModel)
            assert section_def.a_test.m_error is not None
            errors, _ = section_def.m_all_validate()
            assert len(errors) == 1

    def test_more_property(self):
        class TestSection(MSection):
            m_def = Section(this_does_not_exist_in_metainfo='value')
            test_quantity = Quantity(
                type=str, also_no_metainfo_quantity=1, one_more=False
            )
            test_delayed_more_quantity = Quantity(type=str)
            another_test_quantity = Quantity(type=str)

        TestSection.test_delayed_more_quantity.more = dict(one_more='test')

        assert TestSection.m_def.more['this_does_not_exist_in_metainfo'] == 'value'
        assert TestSection.test_quantity.more['also_no_metainfo_quantity'] == 1
        assert not TestSection.test_quantity.more['one_more']
        assert TestSection.test_delayed_more_quantity.more['one_more'] == 'test'
        assert len(TestSection.another_test_quantity.more) == 0

        assert TestSection.m_def.this_does_not_exist_in_metainfo == 'value'
        assert TestSection.test_quantity.also_no_metainfo_quantity == 1
        assert not TestSection.test_quantity.one_more
        with pytest.raises(AttributeError):
            assert TestSection.not_even_in_more is None

        serialized = TestSection.m_def.m_to_dict()
        assert 'more' in serialized
        assert 'this_does_not_exist_in_metainfo' in serialized['more']
        assert 'this_does_not_exist_in_metainfo' not in serialized


existing_repeating = Run()
existing_repeating.systems.append(System())
existing_nonrepeating = Run()
existing_nonrepeating.parsing = Parsing()
existing_multiple = Run()
existing_multiple.systems = [System(), System()]


class TestM1:
    """Test for meta-info instances."""

    def test_run(self):
        class Run(MSection):
            pass

        run = Run()

        assert run.m_def == Run.m_def
        assert run.m_def.name == 'Run'

        assert_section_instance(run)

    def test_system(self):
        class System(MSection):
            m_def = Section()
            atom_labels = Quantity(type=str, shape=['1..*'])

        system = System()
        system.atom_labels = ['H']
        assert len(system.atom_labels) == 1

        assert_section_instance(system)

    def test_set_none(self):
        def run_test(section, quantity):
            assert getattr(section, quantity.name) is not None
            assert section.m_is_set(quantity)

            for _ in range(0, 2):
                setattr(section, quantity.name, None)
                assert not section.m_is_set(quantity)
                assert getattr(section, quantity.name) is None
                assert quantity.name not in section.m_to_dict()

        run = Run()
        run.code_name = 'test'
        run_test(run, Run.code_name)

        system = System()
        system.atom_positions = [[0, 0, 0]]
        run_test(system, System.atom_positions)

    def test_set_subsection(self):
        run = Run()
        first = Parsing()
        run.parsing = first
        assert first.m_parent == run
        assert run.parsing == first

        second = Parsing()
        run.parsing = second
        assert first.m_parent is None
        assert second.m_parent == run
        assert run.parsing == second

        run.parsing = None
        assert run.parsing is None

    def test_sub_section_lst(self):
        run = Run()
        assert run.systems == []  # pylint: disable=use-implicit-booleaness-not-comparison
        run.systems.append(System())

        assert len(run.systems) == 1
        assert run.systems[0].m_parent == run
        assert run.systems[0].m_parent_index == 0
        assert run.systems[0].m_parent_sub_section == Run.systems

        run.systems.append(System())
        first = run.systems[0]
        del run.systems[0]
        assert first.m_parent is None
        assert run.systems[0].m_parent_index == 0

    def test_defaults(self):
        assert len(System().periodic_dimensions) == 3
        assert System().atom_labels is None

        with pytest.raises(AttributeError):
            getattr(System(), 'does_not_exist')

    def test_m_section(self):
        assert Run().m_def == Run.m_def

    def test_children_parent(self):
        run = Run()
        system1 = run.m_create(System)
        run.m_create(System)

        assert run.systems[0] == system1  # pylint: disable=E1101
        assert run.m_get_sub_section(Run.systems, 0) == system1
        assert run.m_sub_section_count(Run.systems) == 2

    def test_parent_repeats(self):
        run = Run()
        system = run.m_create(System)

        assert system.m_parent == run
        assert system.m_parent_index == 0

    def test_parent_not_repeats(self):
        run = Run()
        parsing = run.m_create(Parsing)

        assert parsing.m_parent == run
        assert parsing.m_parent_index == -1

    def test_wrong_type(self):
        Run().code_name = 1

    def test_wrong_shape_1(self):
        with pytest.raises(ValueError):
            Run().code_name = ['name']

    def test_wrong_shape_2(self):
        with pytest.raises(ValueError):
            System().atom_labels = 'label'

    def test_np_array(self):
        system = System()
        system.atom_positions = [[1, 2, 3]]
        assert isinstance(system.atom_positions, pint.Quantity)

    def test_pd_dataframe(self):
        system = System()
        system.atom_positions = pd.DataFrame([[1, 2], [3, 4]])
        assert isinstance(system.atom_positions, pint.Quantity)
        assert np.all(system.atom_positions.m == [[1, 2], [3, 4]])

    def test_np_scalar(self):
        class TestSection(MSection):
            test_quantity = Quantity(type=np.dtype('int32'))

        test_section = TestSection()

        for value in [12, np.array([12], dtype=np.int32)]:
            test_section.test_quantity = value
            assert test_section.test_quantity == 12
            assert type(test_section.test_quantity) is np.int32

        with pytest.raises(ValueError):
            test_section.test_quantity = np.array([12, 13], dtype=np.int32)

    def test_scalar_float(self):
        class TestSection(MSection):
            test_quantity = Quantity(type=float)

        assert math.isnan(
            TestSection.m_from_dict(
                {'test_quantity': None}, treat_none_as_nan=True
            ).test_quantity
        )

    def test_pd_dataframe_quantity(self):
        class TestSection(MSection):
            test_quantity = Quantity(type=np.dtype('int32'), shape=['*', '*'])

        test_section = TestSection()
        test_section.test_quantity = pd.DataFrame([[1, 2]])
        assert np.all(test_section.test_quantity == [1, 2])

    def test_unit_conversion(self):
        system = System()
        system.atom_positions = [[1, 2, 3]] * ureg.angstrom
        assert system.atom_positions.units == ureg.meter
        assert system.atom_positions[0][0] < 0.1 * ureg.meter

    def test_offset_unit(self):
        class MySection(MSection):
            temperature = Quantity(unit='degree_Celsius', type=float)

        system = MySection()
        system.temperature = 100
        assert pytest.approx(system.temperature.to(ureg('K')).m) == 373.15

    @pytest.mark.parametrize('dtype', MTypes.num)
    @pytest.mark.parametrize('shape', [None, [1, 2]])
    def test_setting_with_dimensionless_unit(self, dtype, shape):
        if dtype not in MTypes.numpy:
            shape = None

        class TestSection(MSection):
            test_quantity = Quantity(type=dtype, shape=shape)

        test_section = TestSection()
        if dtype in MTypes.int:
            value = 42
        elif dtype in MTypes.float:
            value = dtype(3.14)
        elif dtype in MTypes.complex:
            value = 1 + 2j
        else:
            raise Exception('Unsupported type')
        if shape:
            value = np.full(shape, value, dtype=dtype)
        test_section.test_quantity = value * ureg.dimensionless
        if shape:
            assert np.all(test_section.test_quantity == value)
        elif dtype == np.float16:
            assert test_section.test_quantity == pytest.approx(value, 1e-2)
        elif dtype == np.float32:
            assert test_section.test_quantity == pytest.approx(value, 1e-7)
        else:
            assert test_section.test_quantity == value

    def test_synonym(self):
        system = System()
        system.lattice_vectors = [[1.2e-10, 0, 0], [0, 1.2e-10, 0], [0, 0, 1.2e-10]]
        assert isinstance(system.lattice_vectors, pint.Quantity)
        assert isinstance(system.unit_cell, pint.Quantity)
        assert np.array_equal(
            system.unit_cell.magnitude, system.lattice_vectors.magnitude
        )  # pylint: disable=no-member

    @pytest.fixture(scope='function')
    def example_data(self):
        run = Run()
        run.code_name = 'test code name'
        run.m_create(Parsing)
        system: System = run.m_create(System)
        system.atom_labels = ['H', 'H', 'O']
        system.atom_positions = np.array(
            [[1.2e-10, 0, 0], [0, 1.2e-10, 0], [0, 0, 1.2e-10]]
        )
        system.atom_labels = np.array(['H', 'H', 'O'])
        return run

    def assert_example_data(self, data: Run):
        assert_section_instance(data)
        assert data.m_def == Run.m_def
        assert data.code_name == 'test code name'
        system: System = data.systems[0]
        assert_section_instance(system)
        assert system.m_def == System.m_def
        assert system.n_atoms == 3
        assert system.atom_labels == ['H', 'H', 'O']
        assert isinstance(system.atom_positions, pint.Quantity)

    def test_derived(self):
        system = System()

        with pytest.raises(DeriveError):
            assert system.n_atoms == 3

        system.atom_labels = ['H', 'H', 'O']
        assert system.n_atoms == 3

        assert 'n_atoms' not in system.m_to_dict()
        assert 'n_atoms' in system.m_to_dict(include_derived=True)
        pass

    def test_derived_cached(self):
        class TestSection(MSection):
            value = Quantity(type=str)
            list = Quantity(type=str, shape=[1])

            @derived(cached=True)
            def derived(self):
                return self.value + self.list[0]

        assert TestSection.derived.cached  # pylint: disable=no-member
        test_section = TestSection(value='test', list=['1'])
        assert test_section.derived == 'test1'
        test_section.value = '2'
        assert test_section.derived == '21'
        test_section.list[0] = '2'
        assert test_section.derived == '21'

    def test_from_dict_tracks_single_bulk_change_and_invalidates_cached_derived(self):
        class Child(MSection):
            value = Quantity(type=str)

        class Parent(MSection):
            left = Quantity(type=str, default='')
            right = Quantity(type=str, default='')
            child = SubSection(sub_section=Child)

            @derived(cached=True)
            def joined(self):
                return f'{self.left}:{self.right}'

            def on_set(self, quantity_def, value):
                if quantity_def == Parent.left:
                    assert self.joined == f'{value}:'

        section = Parent.m_from_dict(
            {'left': 'A', 'right': 'B', 'child': {'value': 'C'}}
        )

        assert section.m_mod_count == 1
        assert section.child.m_mod_count == 1
        assert section.__dict__['_cached_joined'] == [0, 'A:']
        assert section.joined == 'A:B'
        assert section.__dict__['_cached_joined'] == [1, 'A:B']

        section.child.value = 'D'
        assert section.m_mod_count == 2
        assert section.child.m_mod_count == 2

    def test_from_dict_tracks_external_tree_changes_during_event_handlers(self):
        class RemoteTree(MSection):
            value = Quantity(type=str, default='initial')

            @derived(cached=True)
            def cached_value(self):
                return self.value

        remote_tree = RemoteTree()
        assert remote_tree.cached_value == 'initial'

        class LocalTree(MSection):
            trigger = Quantity(type=str)

            def on_set(self, quantity_def, value):
                if quantity_def == LocalTree.trigger:
                    remote_tree.value = value

        LocalTree.m_from_dict({'trigger': 'updated'})

        assert remote_tree.value == 'updated'
        assert remote_tree.m_mod_count == 1
        assert remote_tree.cached_value == 'updated'

    def test_derived_deserialize(self):
        class TestSection(MSection):
            value = Quantity(type=str, derived=lambda _: 'test_value')

        test_section = TestSection()
        assert test_section.value == 'test_value'

        with pytest.raises(MetainfoError):
            test_section = TestSection(value='wrong')

        test_section = TestSection.m_from_dict({'test_value': 'wrong'})
        assert test_section.value == 'test_value'

    def test_extension(self):
        run = Run()
        run.m_as(VaspRun).x_vasp_raw_format = 'outcar'
        assert run.m_as(VaspRun).x_vasp_raw_format == 'outcar'

    def test_resolve(self):
        run = Run()
        system = run.m_create(System)

        assert run.m_resolve('/systems/0') == system
        assert system.m_resolve('/systems/0') == system

    def test_validate(self):
        run = Run()
        run.m_create(System)

        errors, _ = run.m_validate()
        assert len(errors) == 1

    def test_validate_dimension(self):
        system = System()
        system.atom_labels = ['H']
        system.atom_positions = [[]]
        assert len(system.m_validate()) > 0

    def test_multiple_sub_sections(self):
        class TestSection(MSection):  # pylint: disable=unused-variable
            one = SubSection(sub_section=System)
            two = SubSection(sub_section=System)

        test_section = TestSection()
        with pytest.raises(Exception):
            test_section.m_create(System)

        test_section.m_create(System, TestSection.one)

        assert test_section.one is not None

    def test_mapping(self):
        run = Run()
        run.m_create(Parsing).parser_name = 'test'
        system = run.m_create(System)
        system.atom_labels = ['H', 'O']

        assert run.systems[0].atom_labels == ['H', 'O']
        assert run['systems.0.atom_labels'] == ['H', 'O']
        assert run['systems/0/atom_labels'] == ['H', 'O']
        assert run['parsing.parser_name'] == 'test'

    def test_np_dtype(self):
        scc = SCC()
        scc.energy_total_0 = 1.0
        scc.an_int = 1
        assert scc.energy_total_0.m == 1.0  # pylint: disable=no-member
        assert scc.energy_total_0 == 1.0 * ureg.J
        assert scc.m_to_dict()['energy_total_0'] == 1.0
        assert scc.an_int == 1
        assert scc.an_int.__class__ == np.int32
        assert scc.an_int.item() == 1  # pylint: disable=no-member

        scc.energy_total_0 = 1
        assert scc.energy_total_0.m == 1.0  # pylint: disable=no-member

        system = System()
        value = [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]]
        system.lattice_vectors = value
        assert isinstance(system.lattice_vectors.m, np.ndarray)  # pylint: disable=no-member
        assert system.lattice_vectors.m[1][2] == 1.0  # pylint: disable=no-member
        assert system.m_to_dict()['lattice_vectors'] == value

        class TestSection(MSection):
            f32_default = Quantity(type=np.dtype(np.float32), shape=[], default=1.0)
            f32 = Quantity(type=np.dtype(np.float32), shape=[])
            f64 = Quantity(type=np.dtype(np.float64), shape=[])

        section = TestSection()
        section.f32_default = -200
        section.f32 = -200
        section.f64 = -200

    def test_np_allow_wrong_shape(self):
        with pytest.raises(ValueError):
            SCC().energy_total_0 = np.array([1.0, 1.0, 1.0])

    def test_copy(self):
        run = Run()
        run.m_create(Parsing).parser_name = 'test'
        system = run.m_create(System)
        system.atom_labels = ['H', 'O']

        copy = run.m_copy()
        assert copy is not run
        assert copy.m_def is run.m_def
        assert copy.systems is run.systems

        copy = run.m_copy(deep=True)
        assert copy is not run
        assert copy.systems is not run.systems
        assert copy.systems[0] is not run.systems[0]
        assert copy.systems[0].m_parent_index == 0
        assert (
            copy.systems[0].m_parent_sub_section is run.systems[0].m_parent_sub_section
        )
        for k, v in copy.__dict__.items():
            if k not in (
                'm_def',
                'm_parent',
                'm_parent_index',
                'm_parent_sub_section',
                'm_context',
            ):
                assert v is not run.__dict__[k]

    def test_copy_keeps_m_sub_section_list(self):
        run = Run()
        run.m_create(Parsing).parser_name = 'test'
        system = run.m_create(System)
        system.atom_labels = ['H', 'O']

        copy = run.m_copy(deep=True)
        copy.systems.append(System())

        copy2 = copy.m_copy(deep=True)
        copy2.m_to_dict()

    def test_not_default_defaults(self):
        class TestSection(MSection):
            int_quantity = Quantity(type=int)
            float_quantity = Quantity(type=float)
            bool_quantity = Quantity(type=bool)

        section = TestSection()
        assert section.int_quantity is None
        assert section.float_quantity is None
        assert section.bool_quantity is None

    @pytest.mark.filterwarnings('ignore')
    def test_xpath(self):
        run = Run()
        run.code_name = 'amazingX'
        system = run.m_create(System)
        system.atom_labels = ['H', 'O']
        system.system_type = 'molecule'
        calc = run.m_create(SCC)
        calc.energy_total = -1.20e-23
        calc.system = system

        assert run.m_xpath('code_name') == 'amazingX'
        assert run.m_xpath('systems[-1].system_type') == 'molecule'
        assert run.m_xpath('sccs[0].system.atom_labels') == ['H', 'O']
        assert run.m_xpath('systems[?system_type == `molecule`].atom_labels') == [
            ['H', 'O']
        ]
        assert run.m_xpath('sccs[?energy_total < `1.0E-23`].system') == [
            {'atom_labels': ['H', 'O'], 'system_type': 'molecule'}
        ]

    def test_m_update(self):
        class Child(MSection):
            pass

        class Parent(MSection):
            quantity = Quantity(type=str)
            single_sub_section = SubSection(sub_section=Child)
            many_sub_section = SubSection(sub_section=Child, repeats=True)

        parent = Parent()
        parent.m_update(
            quantity='Hello',
            single_sub_section=Child(),
            many_sub_section=[Child(), Child()],
        )

        assert parent.quantity == 'Hello'
        assert parent.single_sub_section is not None
        assert len(parent.many_sub_section) == 2

    @pytest.mark.parametrize(
        'root,path,exception',
        [
            pytest.param(
                Run(), 'parsing', None, id='non-existing non-repeating section'
            ),
            pytest.param(Run(), 'systems/2', None, id='non-existing repeating section'),
            pytest.param(
                existing_nonrepeating,
                'parsing',
                None,
                id='existing non-repeating section',
            ),
            pytest.param(
                existing_repeating, 'systems', None, id='existing repeating section'
            ),
            pytest.param(
                Run(),
                'code_name',
                'Could not find section definition with name "code_name".',
                id='cannot target quantity',
            ),
            pytest.param(
                Run(),
                'missing',
                'Cannot find a proper definition for name "missing".',
                id='invalid path',
            ),
            pytest.param(
                existing_multiple,
                'systems/noindex',
                'Please provide index',
                id='ambiguous path',
            ),
        ],
    )
    def test_m_setdefault(self, root: MSection, path: str, exception: str):
        if not exception:
            system = root.m_setdefault(path)
            assert system
        else:
            with pytest.raises(Exception) as exc_info:
                system = root.m_setdefault(path)
            assert exception in str(exc_info.value)

    def test_m_traverse(self):
        class Child(MSection):
            quantity = Quantity(type=int)

        class ChildRepeated(MSection):
            quantity = Quantity(type=int)

        class Parent(MSection):
            quantity = Quantity(type=int)
            child = SubSection(sub_section=Child)
            child_repeated = SubSection(sub_section=ChildRepeated, repeats=True)

        section = Parent(
            quantity=1,
            child=Child(quantity=2),
            child_repeated=[ChildRepeated(quantity=3), ChildRepeated(quantity=4)],
        )

        expected = [
            [[section], ['quantity']],
            [[section, section.child], ['child', 'quantity']],
            [[section], ['child']],
            [[section, section.child_repeated[0]], ['child_repeated', 0, 'quantity']],
            [[section], ['child_repeated']],
            [[section, section.child_repeated[1]], ['child_repeated', 1, 'quantity']],
            [[section], ['child_repeated']],
        ]

        for i, [sections, _, _, path] in enumerate(
            section.m_traverse(return_all_sections=True)
        ):
            assert sections == expected[i][0]
            assert path == expected[i][1]


@pytest.mark.parametrize('as_dict', [True, False])
@pytest.mark.parametrize('add_key', [True, False])
@pytest.mark.parametrize('str_type', [True, False])
@pytest.mark.parametrize('dup_key', [True, False])
def test_serialise_as_dict(as_dict, add_key, str_type, dup_key):
    class TestSection(MSection):
        q = Quantity(type=str if str_type else int)

    class TestContainer(MSection):
        s = SubSection(
            sub_section=TestSection, repeats=True, key_quantity='q' if add_key else None
        )

    container = TestContainer()

    def __key(_i):
        if str_type:
            return 'abc' if dup_key else f'abc{_i}'

        return 0 if dup_key else _i

    for i in range(3):
        section = TestSection(q=__key(i))
        container.m_add_sub_section(TestContainer.s, section)

    kwarg = {'subsection_as_dict': as_dict}
    if not str_type and add_key:
        with pytest.raises(TypeError):
            for i in range(3):
                _ = container.s[i].m_key
    else:
        for i in range(3):
            assert container.s[i].q == __key(i)
            if add_key and not dup_key:
                assert container.s[f'abc{i}'].q == f'abc{i}'
            else:
                with pytest.raises(KeyError):
                    _ = container.s[f'abc{i}'].q

        json_dict = container.m_to_dict(**kwarg)

        if not as_dict or add_key and dup_key:
            assert isinstance(json_dict['s'], list)
        else:
            assert isinstance(json_dict['s'], dict)
        assert json_dict == TestContainer.m_from_dict(json_dict).m_to_dict(**kwarg)


class Simulation(MSection):
    m_def = Section(description='Definition for test.')
    program_name = Quantity(type=str, description='Quantity for test.')


quantity = Quantity(type=str, description='Quantity for test.')
unit_quantity = Quantity(
    type=float, unit='m', description='Quantity with unit for test.'
)
array_unit_quantity = Quantity(
    type=float, unit='m', shape=['*', '*', '*'], description='Array quantity for test.'
)


class QuantityOnly(MSection):
    m_def = Section(description='Test Quantity only MSection.')
    quantity = Quantity(type=str, description='Quantity for test.')


class SubSectionOnly(MSection):
    m_def = Section(description='Test SubSection only MSection.')
    subsection_repeat = SubSection(sub_section=Simulation.m_def, repeats=True)
    subsection_norepeat = SubSection(sub_section=Simulation.m_def, repeats=False)


class SectionWithBoth(MSection):
    m_def = Section(description='Test MSection with both Quantity and SubSection.')
    quantity = Quantity(type=str, description='Quantity for test.')
    subsection_repeat = SubSection(sub_section=Simulation.m_def, repeats=True)
    subsection_norepeat = SubSection(sub_section=Simulation.m_def, repeats=False)


class SpecialSimulation(Simulation):
    m_def = Section(description='Special simulation with inheritance.')


class SectionWithInheritance(SectionWithBoth):
    m_def = Section(description='Test MSection with inheritance.')
    quantity = Quantity(type=float, description='Overridden quantity for test.')


class SectionWithSelfReference(MSection):
    m_def = Section(description='Test MSection with self reference.')
    quantity = Quantity(type=str, description='Quantity for test.')
    subsection = SubSection(
        sub_section=SectionProxy('SectionWithSelfReference'),
        repeats=False,
        description='SubSection with self reference for test.',
    )


class TestToJsonSchema:
    """Test `m_to_json_schema` methods for Quantity and Definition."""

    def test_quantity_basics(self):
        quantity = Quantity(
            type=float,
            description='Test',
            unit='m',
            title='test quantity',
            default=1.0,
            a_eln=ELNAnnotation(
                props=dict(minValue=0.0, maxValue=10.0),
            ),
        )
        schema = quantity.m_to_json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)

        json.dumps(schema)
        assert 'json-schema.org/' in schema['$schema']

        assert (
            schema['$id']
            == f'{SCHEMA_ENDPOINT}/nomad.metainfo.metainfo.Quantity@{quantity.definition_id}'
        )
        assert schema['title'] == quantity.title
        assert schema['description'] == quantity.description
        assert schema['unit'] == quantity.unit
        assert schema['default'] == quantity.default
        assert schema['minimum'] == quantity.m_annotations['eln'].props['minValue']
        assert schema['maximum'] == quantity.m_annotations['eln'].props['maxValue']

        enum_quantity = Quantity(
            type=MEnum(['test_1', 'test_2', 'test_3']), description='Test enum quantity'
        )

        enum_schema = enum_quantity.m_to_json_schema()
        jsonschema.Draft202012Validator.check_schema(enum_schema)

        assert enum_schema['type'] == 'string'
        assert enum_schema['enum'] == ['test_1', 'test_2', 'test_3']

    def test_subsection_basics(self):
        subsection_norepeat = SubSection(sub_section=Simulation.m_def, repeats=False)
        schema = subsection_norepeat.m_to_json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)

        json.dumps(schema)

        assert schema['type'] == 'object'
        assert (
            schema['$ref']
            == f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}'
        )

        subsection_repeat = SubSection(sub_section=Simulation.m_def, repeats=True)
        repeat_schema = subsection_repeat.m_to_json_schema()
        jsonschema.Draft202012Validator.check_schema(repeat_schema)
        json.dumps(repeat_schema)

        assert repeat_schema['type'] == 'array'
        assert (
            repeat_schema['items']['$ref']
            == f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}'
        )

    @pytest.mark.parametrize(
        'shape, expected_subschema',
        [
            pytest.param([], {'type': 'number'}, id='0D'),
            pytest.param(
                [1],
                {
                    'type': 'array',
                    'minItems': 1,
                    'maxItems': 1,
                    'items': {'type': 'number'},
                },
                id='single-item-array',
            ),
            pytest.param(
                [3],
                {
                    'type': 'array',
                    'minItems': 3,
                    'maxItems': 3,
                    'items': {'type': 'number'},
                },
                id='1D-fixed',
            ),
            pytest.param(
                ['*'],
                {
                    'type': 'array',
                    'items': {'type': 'number'},
                },
                id='1D-flexible',
            ),
            pytest.param(
                [3, 3],
                {
                    'type': 'array',
                    'minItems': 3,
                    'maxItems': 3,
                    'items': {
                        'type': 'array',
                        'minItems': 3,
                        'maxItems': 3,
                        'items': {'type': 'number'},
                    },
                },
                id='2D-fixed',
            ),
            pytest.param(
                ['*', '*'],
                {
                    'type': 'array',
                    'items': {'type': 'array', 'items': {'type': 'number'}},
                },
                id='2D-flexible',
            ),
            pytest.param(
                [3, 3, 3],
                {
                    'type': 'array',
                    'minItems': 3,
                    'maxItems': 3,
                    'items': {
                        'type': 'array',
                        'minItems': 3,
                        'maxItems': 3,
                        'items': {
                            'type': 'array',
                            'minItems': 3,
                            'maxItems': 3,
                            'items': {'type': 'number'},
                        },
                    },
                },
                id='3D-fixed',
            ),
            pytest.param(
                ['*', '*', '*'],
                {
                    'type': 'array',
                    'items': {
                        'type': 'array',
                        'items': {'type': 'array', 'items': {'type': 'number'}},
                    },
                },
                id='3D-flexible',
            ),
            pytest.param(
                [3, '*', 3],
                {
                    'type': 'array',
                    'minItems': 3,
                    'maxItems': 3,
                    'items': {
                        'type': 'array',
                        'items': {
                            'type': 'array',
                            'minItems': 3,
                            'maxItems': 3,
                            'items': {'type': 'number'},
                        },
                    },
                },
                id='mixed',
            ),
            pytest.param(
                ['n_sites', 3],
                {
                    'items': {
                        'items': {'type': 'number'},
                        'maxItems': 3,
                        'minItems': 3,
                        'type': 'array',
                    },
                    'type': 'array',
                },
                id='ref-another-quantity',
            ),
        ],
    )
    def test_quantity_shape(self, shape, expected_subschema):
        quantity = Quantity(type=float, shape=shape)
        schema = quantity.m_to_json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)

        json.dumps(schema)

        # Inline recursive sub-dict comparison
        def assert_subdict(actual, expected):
            assert isinstance(actual, dict), f'Expected dict, got {type(actual)}'
            for key, expected_value in expected.items():
                assert key in actual, f'Missing key: {key}'
                actual_value = actual[key]
                if isinstance(expected_value, dict):
                    assert_subdict(actual_value, expected_value)
                else:
                    assert actual_value == expected_value, (
                        f"For key '{key}', expected {expected_value}, got {actual_value}"
                    )

        assert_subdict(schema, expected_subschema)
        complex_quantity = Quantity(type=complex, shape=shape)
        complex_schema = complex_quantity.m_to_json_schema()
        jsonschema.Draft202012Validator.check_schema(complex_schema)
        assert_subdict(complex_schema['properties']['re'], expected_subschema)
        assert_subdict(complex_schema['properties']['im'], expected_subschema)

    @pytest.mark.parametrize(
        'm_types, expected_type',
        [
            pytest.param(MTypes.int, 'integer', id='integer'),
            pytest.param(MTypes.float, 'number', id='number'),
            pytest.param(
                MTypes.str
                | {
                    MEnum('test'),
                    Bytes,
                    HDF5Dataset,
                    HDF5Reference,
                    Reference(unit_quantity),
                },
                'string',
                id='string',
            ),
            pytest.param(MTypes.bool, 'boolean', id='boolean'),
            pytest.param({Datetime}, 'date-time', id='datetime'),
            pytest.param({JSON}, 'object', id='json'),
            pytest.param(MTypes.complex, 'object', id='complex'),
        ],
    )
    def test_quantity_type(self, m_types, expected_type):
        for type_ in m_types:
            quantity = Quantity(type=type_)
            schema = quantity.m_to_json_schema()
            jsonschema.Draft202012Validator.check_schema(schema)

            json.dumps(schema)

            if expected_type == 'date-time':
                assert schema['type'] == 'string'
                assert schema.get('format') == 'date-time'
            elif isinstance(type_, Reference):
                assert schema['type'] == 'string'
                assert (
                    schema['reference_section_def']
                    == f'{unit_quantity.qualified_name()}@{unit_quantity.definition_id}'
                )
            elif type_ in MTypes.complex:
                assert schema['type'] == 'object'
                assert 'properties' in schema
                assert 're' in schema['properties']
                assert 'im' in schema['properties']
                assert schema['properties']['re']['type'] == 'number'
                assert schema['properties']['im']['type'] == 'number'
            else:
                assert schema['type'] == expected_type

    @pytest.mark.parametrize(
        'mtype, default, expected_default',
        [
            pytest.param(MTypes.int, 42, 42, id='int'),
            pytest.param(
                MTypes.float, 1.125, 1.125, id='float'
            ),  # 1.125 is representable by float16
            pytest.param(
                MTypes.str
                | {
                    MEnum('test'),
                    HDF5Dataset,
                    HDF5Reference,
                    Reference(quantity),
                },
                'test',
                'test',
                id='str',
            ),
            pytest.param(MTypes.bool, True, True, id='bool'),
            pytest.param(
                {Datetime},
                datetime(2023, 1, 1),
                '2023-01-01T00:00:00+00:00',
                id='datetime',
            ),
            pytest.param({JSON}, {'key': 'value'}, {'key': 'value'}, id='json'),
            pytest.param(MTypes.complex, 1 + 2j, {'re': 1.0, 'im': 2.0}, id='complex'),
            pytest.param({Bytes}, b'test', 'dGVzdA==', id='bytes'),
        ],
    )
    def test_quantity_default(self, mtype, default, expected_default):
        for type_ in mtype:
            quantity = Quantity(type=type_, default=default)
            schema = quantity.m_to_json_schema()
            jsonschema.Draft202012Validator.check_schema(schema)

            json.dumps(schema)
            assert schema['default'] == expected_default

    @pytest.mark.parametrize(
        'quantity, expected',
        [
            pytest.param(
                quantity,
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    '$id': f'{SCHEMA_ENDPOINT}/nomad.metainfo.metainfo.Quantity@{quantity.definition_id}?unit_value=true',
                    'description': 'Quantity for test.',
                    'type': 'string',
                    'nomad_type': 'str',
                },
                id='quantity-with-no-unit',
            ),
            pytest.param(
                unit_quantity,
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    '$id': f'{SCHEMA_ENDPOINT}/nomad.metainfo.metainfo.Quantity@{unit_quantity.definition_id}?unit_value=true',
                    'description': 'Quantity with unit for test.',
                    'nomad_type': 'float64',
                    'type': 'object',
                    'properties': {
                        'value': {'type': 'number', 'unit': 'meter'},
                        'unit': {'type': 'string', 'enum': ['meter']},
                    },
                },
                id='quantity-with-unit',
            ),
            pytest.param(
                array_unit_quantity,
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    '$id': f'{SCHEMA_ENDPOINT}/nomad.metainfo.metainfo.Quantity@{array_unit_quantity.definition_id}?unit_value=true',
                    'description': 'Array quantity for test.',
                    'nomad_type': 'float64',
                    'type': 'object',
                    'properties': {
                        'value': {
                            'type': 'array',
                            'items': {
                                'type': 'array',
                                'items': {
                                    'type': 'array',
                                    'items': {'type': 'number', 'unit': 'meter'},
                                },
                            },
                        },
                        'unit': {'type': 'string', 'enum': ['meter']},
                    },
                },
                id='3d-array-quantity-with-unit',
            ),
        ],
    )
    def test_unit_quantity(self, quantity, expected):
        schema = quantity.m_to_json_schema(add_unit_value=True)
        jsonschema.Draft202012Validator.check_schema(schema)
        json.dumps(schema)
        assert schema == expected

    def test_arrays_with_units(self):
        quantity = Quantity(type=float, shape=['*', '*'], unit='m', description='Test')
        schema = quantity.m_to_json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        json.dumps(schema)

        assert schema['items']['items']['type'] == 'number'
        assert schema['items']['items']['unit'] == 'meter'

        complex_quantity = Quantity(
            type=complex, shape=['*', '*'], unit='m', description='Test'
        )
        complex_schema = complex_quantity.m_to_json_schema()
        jsonschema.Draft202012Validator.check_schema(complex_schema)
        json.dumps(complex_schema)

        assert complex_schema['properties']['re']['items'] == schema['items']
        assert complex_schema['properties']['im']['items'] == schema['items']

        quantity = Quantity(type=float, shape=['*', '*'], unit='m', description='Test')
        schema = quantity.m_to_json_schema(add_unit_value=True)
        jsonschema.Draft202012Validator.check_schema(schema)
        json.dumps(schema)

        sub_schema = schema['properties']['value']['items']['items']

        assert sub_schema['type'] == 'number'
        assert sub_schema['unit'] == 'meter'
        assert schema['properties']['unit']['type'] == 'string'
        assert schema['properties']['unit']['enum'] == ['meter']

        complex_quantity = Quantity(
            type=complex, shape=['*', '*'], unit='m', description='Test'
        )
        complex_schema = complex_quantity.m_to_json_schema(add_unit_value=True)
        jsonschema.Draft202012Validator.check_schema(complex_schema)
        json.dumps(complex_schema)

        assert (
            complex_schema['properties']['value']['properties']['re']['items']['items']
            == sub_schema
        )
        assert (
            complex_schema['properties']['value']['properties']['im']['items']['items']
            == sub_schema
        )
        assert complex_schema['properties']['unit']['type'] == 'string'
        assert complex_schema['properties']['unit']['enum'] == ['meter']

    @pytest.mark.parametrize(
        'section, expected',
        [
            pytest.param(
                QuantityOnly,
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    'title': 'QuantityOnly',
                    'type': 'object',
                    'description': 'Test Quantity only MSection.',
                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.QuantityOnly@{QuantityOnly.m_def.definition_id}',
                    'properties': {
                        'quantity': {
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.QuantityOnly.quantity@{QuantityOnly.quantity.definition_id}',
                            'type': 'string',
                            'description': 'Quantity for test.',
                            'nomad_type': 'str',
                        }
                    },
                },
                id='quantity-only',
            ),
            pytest.param(
                SubSectionOnly,
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    'title': 'SubSectionOnly',
                    'type': 'object',
                    'description': 'Test SubSection only MSection.',
                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SubSectionOnly@{SubSectionOnly.m_def.definition_id}',
                    'properties': {
                        'subsection_repeat': {
                            'type': 'array',
                            'items': {
                                '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}'
                            },
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SubSectionOnly.subsection_repeat@{SubSectionOnly.subsection_repeat.definition_id}',
                            'description': 'Definition for test.',
                        },
                        'subsection_norepeat': {
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SubSectionOnly.subsection_norepeat@{SubSectionOnly.subsection_norepeat.definition_id}',
                            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}',
                            'description': 'Definition for test.',
                        },
                    },
                    '$defs': {
                        'tests.metainfo.test_metainfo.Simulation': {
                            'title': 'Simulation',
                            'type': 'object',
                            'description': 'Definition for test.',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}',
                            'properties': {
                                'program_name': {
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation.program_name@{Simulation.program_name.definition_id}',
                                    'type': 'string',
                                    'description': 'Quantity for test.',
                                    'nomad_type': 'str',
                                }
                            },
                        }
                    },
                },
                id='subsection-only',
            ),
            pytest.param(
                SectionWithBoth,
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    'title': 'SectionWithBoth',
                    'type': 'object',
                    'description': 'Test MSection with both Quantity and SubSection.',
                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}',
                    'properties': {
                        'quantity': {
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.quantity@{SectionWithBoth.quantity.definition_id}',
                            'type': 'string',
                            'description': 'Quantity for test.',
                            'nomad_type': 'str',
                        },
                        'subsection_repeat': {
                            'type': 'array',
                            'items': {
                                '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}'
                            },
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_repeat@{SectionWithBoth.subsection_repeat.definition_id}',
                            'description': 'Definition for test.',
                        },
                        'subsection_norepeat': {
                            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_norepeat@{SectionWithBoth.subsection_norepeat.definition_id}',
                            'description': 'Definition for test.',
                        },
                    },
                    '$defs': {
                        'tests.metainfo.test_metainfo.Simulation': {
                            'title': 'Simulation',
                            'type': 'object',
                            'description': 'Definition for test.',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}',
                            'properties': {
                                'program_name': {
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation.program_name@{Simulation.program_name.definition_id}',
                                    'type': 'string',
                                    'description': 'Quantity for test.',
                                    'nomad_type': 'str',
                                }
                            },
                        }
                    },
                },
                id='section-with-both',
            ),
            pytest.param(
                SectionWithInheritance,
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    'title': 'SectionWithInheritance',
                    'type': 'object',
                    'description': 'Test MSection with inheritance.',
                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithInheritance@{SectionWithInheritance.m_def.definition_id}',
                    'allOf': [
                        {
                            'description': 'SectionWithBoth : Test MSection with both Quantity and SubSection.',
                            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}',
                        }
                    ],
                    'properties': {
                        'quantity': {
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithInheritance.quantity@{SectionWithInheritance.quantity.definition_id}',
                            'nomad_type': 'float64',
                            'type': 'number',
                            'description': 'Overridden quantity for test.',
                        },
                    },
                    '$defs': {
                        'tests.metainfo.test_metainfo.Simulation': {
                            'title': 'Simulation',
                            'type': 'object',
                            'description': 'Definition for test.',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}',
                            'properties': {
                                'program_name': {
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation.program_name@{Simulation.program_name.definition_id}',
                                    'type': 'string',
                                    'description': 'Quantity for test.',
                                    'nomad_type': 'str',
                                }
                            },
                        },
                        'tests.metainfo.test_metainfo.SectionWithBoth': {
                            'title': 'SectionWithBoth',
                            'type': 'object',
                            'description': 'Test MSection with both Quantity and SubSection.',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}',
                            'properties': {
                                'quantity': {
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.quantity@{SectionWithBoth.quantity.definition_id}',
                                    'type': 'string',
                                    'description': 'Quantity for test.',
                                    'nomad_type': 'str',
                                },
                                'subsection_repeat': {
                                    'type': 'array',
                                    'items': {
                                        '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}'
                                    },
                                    'description': 'Definition for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_repeat@{SectionWithBoth.subsection_repeat.definition_id}',
                                },
                                'subsection_norepeat': {
                                    '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}',
                                    'description': 'Definition for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_norepeat@{SectionWithBoth.subsection_norepeat.definition_id}',
                                },
                            },
                        },
                    },
                },
                id='section-with-inheritance',
            ),
            pytest.param(
                SectionWithSelfReference,
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    'title': 'SectionWithSelfReference',
                    'type': 'object',
                    'description': 'Test MSection with self reference.',
                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithSelfReference@{SectionWithSelfReference.m_def.definition_id}',
                    'properties': {
                        'quantity': {
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithSelfReference.quantity@{SectionWithSelfReference.quantity.definition_id}',
                            'type': 'string',
                            'description': 'Quantity for test.',
                            'nomad_type': 'str',
                        },
                        'subsection': {
                            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithSelfReference@{SectionWithSelfReference.m_def.definition_id}',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithSelfReference.subsection@{SectionWithSelfReference.subsection.definition_id}',
                            'description': 'SubSection with self reference for test.',
                        },
                    },
                },
                id='section-with-self-reference',
            ),
        ],
    )
    def test_definition(self, section, expected):
        schema = section.m_def.m_to_json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)

        json.dumps(schema)
        assert schema == expected

    @pytest.mark.parametrize(
        'section, exclude, expected',
        [
            pytest.param(
                SectionWithInheritance,
                [
                    'tests.metainfo.test_metainfo.SectionWithInheritance.quantity',
                    'tests.metainfo.test_metainfo.SectionWithBoth.subsection_repeat',
                    'tests.metainfo.test_metainfo.SectionWithBoth.subsection_norepeat',
                ],
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    'title': 'SectionWithInheritance',
                    'type': 'object',
                    'description': 'Test MSection with inheritance.',
                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithInheritance@{SectionWithInheritance.m_def.definition_id}',
                    'allOf': [
                        {
                            'description': 'SectionWithBoth : Test MSection with both Quantity and SubSection.',
                            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}',
                        }
                    ],
                    '$defs': {
                        'tests.metainfo.test_metainfo.SectionWithBoth': {
                            'title': 'SectionWithBoth',
                            'type': 'object',
                            'description': 'Test MSection with both Quantity and SubSection.',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}',
                            'properties': {
                                'quantity': {
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.quantity@{SectionWithBoth.quantity.definition_id}',
                                    'type': 'string',
                                    'description': 'Quantity for test.',
                                    'nomad_type': 'str',
                                },
                            },
                        },
                    },
                },
                id='exclude-subsections',
            )
        ],
    )
    def test_definition_exclude(self, section, exclude, expected):
        schema = section.m_def.m_to_json_schema(exclude=exclude)
        jsonschema.Draft202012Validator.check_schema(schema)

        json.dumps(schema)
        assert schema == expected

    @pytest.mark.parametrize(
        'section, add_section_subtypes, add_property_subtypes, expected',
        [
            pytest.param(
                SectionWithBoth,
                True,
                True,
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    'title': 'SectionWithBoth',
                    'description': 'Test MSection with both Quantity and SubSection.',
                    'type': 'object',
                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}?section_subtypes=true&property_subtypes=true',
                    'anyOf': [
                        {
                            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithInheritance@{SectionWithInheritance.m_def.definition_id}?property_subtypes=true',
                            'description': 'SectionWithInheritance : Test MSection with inheritance.',
                        },
                        {
                            'description': 'SectionWithBoth : Test MSection with both Quantity and SubSection.',
                            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}?property_subtypes=true',
                        },
                    ],
                    '$defs': {
                        'tests.metainfo.test_metainfo.SectionWithBoth': {
                            '$schema': 'https://json-schema.org/draft/2020-12/schema',
                            'title': 'SectionWithBoth',
                            'description': 'Test MSection with both Quantity and SubSection.',
                            'type': 'object',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}?property_subtypes=true',
                            'properties': {
                                'quantity': {
                                    'description': 'Quantity for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.quantity@{SectionWithBoth.quantity.definition_id}?property_subtypes=true',
                                    'type': 'string',
                                    'nomad_type': 'str',
                                },
                                'subsection_repeat': {
                                    'type': 'array',
                                    'items': {
                                        'anyOf': [
                                            {
                                                '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SpecialSimulation@{SpecialSimulation.m_def.definition_id}?property_subtypes=true',
                                                'description': 'SpecialSimulation : Special simulation with inheritance.',
                                            },
                                            {
                                                '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}?property_subtypes=true',
                                                'description': 'Simulation : Definition for test.',
                                            },
                                        ]
                                    },
                                    'description': 'Definition for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_repeat@{SectionWithBoth.subsection_repeat.definition_id}?property_subtypes=true',
                                },
                                'subsection_norepeat': {
                                    'anyOf': [
                                        {
                                            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SpecialSimulation@{SpecialSimulation.m_def.definition_id}?property_subtypes=true',
                                            'description': 'SpecialSimulation : Special simulation with inheritance.',
                                        },
                                        {
                                            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}?property_subtypes=true',
                                            'description': 'Simulation : Definition for test.',
                                        },
                                    ],
                                    'description': 'Definition for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_norepeat@{SectionWithBoth.subsection_norepeat.definition_id}?property_subtypes=true',
                                },
                            },
                        },
                        'tests.metainfo.test_metainfo.Simulation': {
                            'title': 'Simulation',
                            'description': 'Definition for test.',
                            'type': 'object',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}?property_subtypes=true',
                            'properties': {
                                'program_name': {
                                    'description': 'Quantity for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation.program_name@{Simulation.program_name.definition_id}?property_subtypes=true',
                                    'type': 'string',
                                    'nomad_type': 'str',
                                }
                            },
                        },
                        'tests.metainfo.test_metainfo.SpecialSimulation': {
                            'title': 'SpecialSimulation',
                            'description': 'Special simulation with inheritance.',
                            'type': 'object',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SpecialSimulation@{SpecialSimulation.m_def.definition_id}?property_subtypes=true',
                            'allOf': [
                                {
                                    '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}?property_subtypes=true',
                                    'description': 'Simulation : Definition for test.',
                                }
                            ],
                        },
                        'tests.metainfo.test_metainfo.SectionWithInheritance': {
                            'title': 'SectionWithInheritance',
                            'description': 'Test MSection with inheritance.',
                            'type': 'object',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithInheritance@{SectionWithInheritance.m_def.definition_id}?property_subtypes=true',
                            'allOf': [
                                {
                                    '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}?property_subtypes=true',
                                    'description': 'SectionWithBoth : Test MSection with both Quantity and SubSection.',
                                }
                            ],
                            'properties': {
                                'quantity': {
                                    'description': 'Overridden quantity for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithInheritance.quantity@{SectionWithInheritance.quantity.definition_id}?property_subtypes=true',
                                    'type': 'number',
                                    'nomad_type': 'float64',
                                }
                            },
                        },
                    },
                },
                id='both-subtypes',
            ),
            pytest.param(
                SectionWithBoth,
                True,
                False,
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    'title': 'SectionWithBoth',
                    'description': 'Test MSection with both Quantity and SubSection.',
                    'type': 'object',
                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}?section_subtypes=true',
                    'anyOf': [
                        {
                            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithInheritance@{SectionWithInheritance.m_def.definition_id}',
                            'description': 'SectionWithInheritance : Test MSection with inheritance.',
                        },
                        {
                            'description': 'SectionWithBoth : Test MSection with both Quantity and SubSection.',
                            '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}',
                        },
                    ],
                    '$defs': {
                        'tests.metainfo.test_metainfo.SectionWithBoth': {
                            '$schema': 'https://json-schema.org/draft/2020-12/schema',
                            'title': 'SectionWithBoth',
                            'description': 'Test MSection with both Quantity and SubSection.',
                            'type': 'object',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}',
                            'properties': {
                                'quantity': {
                                    'description': 'Quantity for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.quantity@{SectionWithBoth.quantity.definition_id}',
                                    'type': 'string',
                                    'nomad_type': 'str',
                                },
                                'subsection_repeat': {
                                    'type': 'array',
                                    'items': {
                                        '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}',
                                    },
                                    'description': 'Definition for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_repeat@{SectionWithBoth.subsection_repeat.definition_id}',
                                },
                                'subsection_norepeat': {
                                    '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}',
                                    'description': 'Definition for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_norepeat@{SectionWithBoth.subsection_norepeat.definition_id}',
                                },
                            },
                        },
                        'tests.metainfo.test_metainfo.Simulation': {
                            'title': 'Simulation',
                            'description': 'Definition for test.',
                            'type': 'object',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}',
                            'properties': {
                                'program_name': {
                                    'description': 'Quantity for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation.program_name@{Simulation.program_name.definition_id}',
                                    'type': 'string',
                                    'nomad_type': 'str',
                                }
                            },
                        },
                        'tests.metainfo.test_metainfo.SectionWithInheritance': {
                            'title': 'SectionWithInheritance',
                            'description': 'Test MSection with inheritance.',
                            'type': 'object',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithInheritance@{SectionWithInheritance.m_def.definition_id}',
                            'allOf': [
                                {
                                    '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}',
                                    'description': 'SectionWithBoth : Test MSection with both Quantity and SubSection.',
                                }
                            ],
                            'properties': {
                                'quantity': {
                                    'description': 'Overridden quantity for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithInheritance.quantity@{SectionWithInheritance.quantity.definition_id}',
                                    'type': 'number',
                                    'nomad_type': 'float64',
                                }
                            },
                        },
                    },
                },
                id='section-subtypes',
            ),
            pytest.param(
                SectionWithBoth,
                False,
                True,
                {
                    '$schema': 'https://json-schema.org/draft/2020-12/schema',
                    'title': 'SectionWithBoth',
                    'description': 'Test MSection with both Quantity and SubSection.',
                    'type': 'object',
                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth@{SectionWithBoth.m_def.definition_id}?property_subtypes=true',
                    'properties': {
                        'quantity': {
                            'description': 'Quantity for test.',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.quantity@{SectionWithBoth.quantity.definition_id}?property_subtypes=true',
                            'type': 'string',
                            'nomad_type': 'str',
                        },
                        'subsection_repeat': {
                            'type': 'array',
                            'items': {
                                'anyOf': [
                                    {
                                        '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SpecialSimulation@{SpecialSimulation.m_def.definition_id}?property_subtypes=true',
                                        'description': 'SpecialSimulation : Special simulation with inheritance.',
                                    },
                                    {
                                        '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}?property_subtypes=true',
                                        'description': 'Simulation : Definition for test.',
                                    },
                                ]
                            },
                            'description': 'Definition for test.',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_repeat@{SectionWithBoth.subsection_repeat.definition_id}?property_subtypes=true',
                        },
                        'subsection_norepeat': {
                            'anyOf': [
                                {
                                    '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SpecialSimulation@{SpecialSimulation.m_def.definition_id}?property_subtypes=true',
                                    'description': 'SpecialSimulation : Special simulation with inheritance.',
                                },
                                {
                                    '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}?property_subtypes=true',
                                    'description': 'Simulation : Definition for test.',
                                },
                            ],
                            'description': 'Definition for test.',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SectionWithBoth.subsection_norepeat@{SectionWithBoth.subsection_norepeat.definition_id}?property_subtypes=true',
                        },
                    },
                    '$defs': {
                        'tests.metainfo.test_metainfo.Simulation': {
                            'title': 'Simulation',
                            'description': 'Definition for test.',
                            'type': 'object',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}?property_subtypes=true',
                            'properties': {
                                'program_name': {
                                    'description': 'Quantity for test.',
                                    '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation.program_name@{Simulation.program_name.definition_id}?property_subtypes=true',
                                    'type': 'string',
                                    'nomad_type': 'str',
                                }
                            },
                        },
                        'tests.metainfo.test_metainfo.SpecialSimulation': {
                            'title': 'SpecialSimulation',
                            'description': 'Special simulation with inheritance.',
                            'type': 'object',
                            '$id': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.SpecialSimulation@{SpecialSimulation.m_def.definition_id}?property_subtypes=true',
                            'allOf': [
                                {
                                    '$ref': f'{SCHEMA_ENDPOINT}/tests.metainfo.test_metainfo.Simulation@{Simulation.m_def.definition_id}?property_subtypes=true',
                                    'description': 'Simulation : Definition for test.',
                                }
                            ],
                        },
                    },
                },
                id='property-subtypes',
            ),
        ],
    )
    def test_definition_add_subtypes(
        self, section, add_section_subtypes, add_property_subtypes, expected
    ):
        schema = section.m_def.m_to_json_schema(
            add_section_subtypes=add_section_subtypes,
            add_property_subtypes=add_property_subtypes,
        )
        jsonschema.Draft202012Validator.check_schema(schema)

        assert schema == expected


class TestStableReferences:
    """Tests for the stable_references flag in m_to_dict."""

    def test_m_def_uses_stable_identifier(self):
        """When stable_references=True, the m_def field should use
        qualified_name@definition_id format."""

        class Parent(MSection):
            pass

        class Child(Parent):
            pass

        child = Child()
        result = child.m_to_dict(with_meta=True, stable_references=True)
        expected_ref = f'{Child.m_def.qualified_name()}@{Child.m_def.definition_id}'
        assert result['m_def'] == expected_ref

    def test_m_def_without_stable_references(self):
        """Without stable_references, m_def should use the default
        definition_reference."""

        class Parent(MSection):
            pass

        class Child(Parent):
            pass

        child = Child()
        result_stable = child.m_to_dict(with_meta=True, stable_references=True)
        result_default = child.m_to_dict(with_meta=True, stable_references=False)
        # The stable format includes @<hash>, the default does not
        assert '@' in result_stable['m_def']
        assert result_stable['m_def'] != result_default['m_def']

    def test_base_sections_use_stable_identifier(self):
        """When stable_references=True, base_sections references in a definition's
        serialized form should use the stable format."""

        class Base(MSection):
            pass

        class Derived(Base):
            pass

        result = Derived.m_def.m_to_dict(stable_references=True)
        base_refs = result.get('base_sections', [])
        assert len(base_refs) > 0
        for ref in base_refs:
            assert '@' in ref, f'Expected stable reference format, got: {ref}'
            name, hash_part = ref.rsplit('@', 1)
            assert len(hash_part) > 0
            assert name == Base.m_def.qualified_name()

    def test_sub_section_type_uses_stable_identifier(self):
        """When stable_references=True, the sub_section's type reference
        should use the stable format."""

        class Inner(MSection):
            pass

        class Outer(MSection):
            inner = SubSection(sub_section=Inner)

        result = Outer.m_def.m_to_dict(stable_references=True)
        sub_sections = result.get('sub_sections', [])
        assert len(sub_sections) > 0
        inner_sub = sub_sections[0]
        sub_section_ref = inner_sub.get('sub_section')
        # sub_section is a Reference-typed quantity, serialized as a string
        assert isinstance(sub_section_ref, str)
        assert '@' in sub_section_ref, (
            f'Expected stable reference format, got: {sub_section_ref}'
        )

    def test_quantity_type_reference_uses_stable_identifier(self):
        """When stable_references=True, a quantity whose type is a Reference
        should serialize the type using the stable format."""

        class Target(MSection):
            pass

        class Container(MSection):
            ref = Quantity(type=Target)

        result = Container.m_def.m_to_dict(stable_references=True)
        quantities = result.get('quantities', [])
        ref_qty = [q for q in quantities if q['name'] == 'ref'][0]
        type_info = ref_qty['type']
        assert type_info['type_kind'] == 'reference'
        assert '@' in type_info['type_data']

    def test_stable_references_propagates_to_subsections(self):
        """The stable_references flag should propagate recursively to
        nested subsection data."""

        class Inner(MSection):
            value = Quantity(type=str)

        class Middle(MSection):
            inner = SubSection(sub_section=Inner)

        class Outer(MSection):
            middle = SubSection(sub_section=Middle)

        outer = Outer()
        middle = Middle()
        inner = Inner(value='test')
        middle.m_add_sub_section(Middle.inner, inner)
        outer.m_add_sub_section(Outer.middle, middle)

        result = outer.m_to_dict(with_meta=True, stable_references=True)
        # Check nested m_def values use stable format
        middle_dict = result['middle']
        assert '@' in middle_dict['m_def']
        inner_dict = middle_dict['inner']
        assert '@' in inner_dict['m_def']

    def test_stable_reference_format_is_deterministic(self):
        """The stable reference should be deterministic — calling m_to_dict
        twice should yield the same result."""

        class MySection(MSection):
            pass

        section = MySection()
        result1 = section.m_to_dict(with_meta=True, stable_references=True)
        result2 = section.m_to_dict(with_meta=True, stable_references=True)
        assert result1 == result2

    def test_stable_reference_contains_qualified_name_and_hash(self):
        """The stable reference format should be
        'qualified_name@definition_id'."""

        class MySection(MSection):
            pass

        section = MySection()
        result = section.m_to_dict(with_meta=True, stable_references=True)
        m_def_ref = result['m_def']
        parts = m_def_ref.split('@')
        assert len(parts) == 2
        assert parts[0] == MySection.m_def.qualified_name()
        assert parts[1] == MySection.m_def.definition_id

    def test_definition_m_def_uses_instance_identity(self):
        """For Definition instances (Section, Package, etc.), the m_def should
        use the instance's own qualified_name@definition_id, not the
        meta-type's (e.g. 'nomad.metainfo.metainfo.Section')."""

        class Base(MSection):
            pass

        class Derived(Base):
            pass

        # Derived.m_def is a Section (which is a Definition)
        result = Derived.m_def.m_to_dict(stable_references=True)
        m_def_ref = result['m_def']
        parts = m_def_ref.split('@')
        assert len(parts) == 2
        # Should be the instance's own identity, not 'nomad.metainfo.metainfo.Section'
        assert parts[0] == Derived.m_def.qualified_name()
        assert parts[1] == Derived.m_def.definition_id
        assert 'nomad.metainfo.metainfo.Section' not in parts[0]

    def test_data_section_reference_unchanged(self):
        """Data-level references between section instances are not affected by
        stable_references: only definition references are stabilized."""

        class Target(MSection):
            name = Quantity(type=str)

        class Container(MSection):
            ref = Quantity(type=Target)

        target = Target(name='hello')
        container = Container()
        container.ref = target

        result_stable = container.m_to_dict(stable_references=True)
        result_default = container.m_to_dict(stable_references=False)
        # Data references should be identical regardless of stable_references
        assert result_stable.get('ref') == result_default.get('ref')
