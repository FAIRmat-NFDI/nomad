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

from collections import defaultdict

import numpy as np
import pytest

from nomad.datamodel.metainfo import runschema
from nomad.units import ureg
from tests.normalizing.conftest import (  # noqa: F401
    boron_nitride,
    boron_nitride_topology,
    check_template_active_orbitals,
    conv_bcc,
    conv_fcc,
    get_template_active_orbitals,
    get_template_computation,
    get_template_for_structure,
    get_template_topology,
    graphene,
    graphene_topology,
    mos2,
    mos2_topology,
    rattle,
    run_normalize,
    single_cr_surface_topology,
    single_cu_surface_topology,
    stack,
    stacked_cu_ni_surface_topology,
    stacked_graphene_boron_nitride_topology,
    surf,
)


def assert_topology(topology):
    """Checks that the given topology has a valid structure."""
    child_map = {}
    child_map_determined = defaultdict(list)
    for top in topology:
        assert top.system_id is not None
        assert top.parent_system is not None or top.system_relation.type == 'root'
        assert (
            top.atoms is not None
            or top.atoms_ref is not None
            or top.indices is not None
        )
        assert top.n_atoms is not None
        assert top.elements is not None
        assert top.n_elements is not None
        assert top.chemical_formula_hill is not None
        assert top.chemical_formula_reduced is not None
        assert top.chemical_formula_anonymous is not None
        assert top.elemental_composition
        for comp in top.elemental_composition:
            assert comp.element
            assert comp.mass
            assert comp.mass_fraction
            assert comp.atomic_fraction
        if top.parent_system:
            child_map_determined[top.parent_system].append(top.system_id)
        if top.child_systems:
            child_map[top.system_id] = top.child_systems
        if top.indices is not None:
            assert top.atoms_ref is not None
            assert len(top.indices.shape) == 2
            assert top.mass_fraction is not None
            assert top.atomic_fraction is not None

    assert len(child_map) == len(child_map_determined)
    for key, child_map_value in child_map.items():
        assert child_map_value == child_map_determined[key]


@pytest.mark.parametrize(
    'pbc',
    [
        pytest.param(True, id='fully periodic'),
        pytest.param(False, id='non-periodic'),
    ],
)
def test_topology_calculation(pbc):
    """Tests that a topology that originates from the calculation itself is
    correctly extracted.
    """
    topology_calculation = get_template_topology(pbc)
    topology = topology_calculation.results.material.topology
    assert_topology(topology)
    assert len(topology) == 5

    # Test the original structure
    original = topology[0]
    assert original.system_relation.type == 'root'
    assert original.atoms_ref.positions.shape == (6, 3)
    assert len(original.atoms_ref.labels) == 6
    assert original.atoms_ref.lattice_vectors.shape == (3, 3)
    expected_pbc = np.zeros(3, bool)
    expected_pbc[:] = pbc
    assert original.atoms_ref.periodic == expected_pbc.tolist()
    assert original.chemical_formula_hill == 'H4O2'
    assert original.chemical_formula_reduced == 'H2O'
    assert original.chemical_formula_anonymous == 'A2B'
    assert original.elements == ['H', 'O']
    assert original.n_elements == 2
    assert original.n_atoms == 6
    assert original.parent_system is None
    assert original.child_systems == ['results/material/topology/1']

    # Test molecule group
    mol_group = topology[1]
    assert mol_group.structural_type == 'group'
    assert mol_group.dimensionality is None
    assert mol_group.building_block is None
    assert mol_group.atomic_fraction == pytest.approx(1, abs=0, rel=1e-5)
    assert mol_group.mass_fraction == pytest.approx(1, abs=0, rel=1e-5)
    assert mol_group.system_relation.type == 'group'
    assert np.array_equal(mol_group.indices, [[0, 1, 2, 3, 4, 5]])
    assert original.chemical_formula_hill == 'H4O2'
    assert original.chemical_formula_reduced == 'H2O'
    assert original.chemical_formula_anonymous == 'A2B'
    assert mol_group.elements == ['H', 'O']
    assert mol_group.n_elements == 2
    assert mol_group.n_atoms == 6
    assert mol_group.parent_system == 'results/material/topology/0'
    assert mol_group.child_systems == ['results/material/topology/2']

    # Test molecule
    mol = topology[2]
    assert mol.structural_type == 'molecule'
    assert mol.dimensionality is None
    assert mol.building_block == 'molecule'
    assert mol.atomic_fraction == pytest.approx(0.5, abs=0, rel=1e-5)
    assert mol.mass_fraction == pytest.approx(0.5, abs=0, rel=1e-5)
    assert mol.system_relation.type == 'subsystem'
    assert np.array_equal(mol.indices, [[0, 1, 2], [3, 4, 5]])
    assert mol.chemical_formula_hill == 'H2O'
    assert mol.chemical_formula_reduced == 'H2O'
    assert mol.chemical_formula_anonymous == 'A2B'
    assert mol.elements == ['H', 'O']
    assert mol.n_elements == 2
    assert mol.n_atoms == 3
    assert mol.parent_system == 'results/material/topology/1'
    assert mol.child_systems == ['results/material/topology/3']

    # Test monomer group
    mon_group = topology[3]
    assert mon_group.structural_type == 'group'
    assert mon_group.dimensionality is None
    assert mon_group.building_block is None
    assert mon_group.atomic_fraction == pytest.approx(1 / 3, abs=0, rel=1e-5)
    assert mon_group.mass_fraction == pytest.approx(0.055949, abs=0, rel=1e-5)
    assert mon_group.system_relation.type == 'group'
    assert np.array_equal(mon_group.indices, [[1, 2]])
    assert mon_group.chemical_formula_hill == 'H2'
    assert mon_group.chemical_formula_reduced == 'H'
    assert mon_group.chemical_formula_anonymous == 'A'
    assert mon_group.elements == ['H']
    assert mon_group.n_elements == 1
    assert mon_group.n_atoms == 2
    assert mon_group.parent_system == 'results/material/topology/2'
    assert mon_group.child_systems == ['results/material/topology/4']

    # Test monomer
    mon = topology[4]
    assert mon.structural_type == 'monomer'
    assert mon.dimensionality is None
    assert mon.building_block == 'monomer'
    assert mon.atomic_fraction == pytest.approx(1 / 3, abs=0, rel=1e-5)
    assert mon.mass_fraction == pytest.approx(0.055949, abs=0, rel=1e-5)
    assert mon.system_relation.type == 'subsystem'
    assert np.array_equal(mon.indices, [[1, 2]])
    assert mon.chemical_formula_hill == 'H2'
    assert mon.chemical_formula_reduced == 'H'
    assert mon.chemical_formula_anonymous == 'A'
    assert mon.elements == ['H']
    assert mon.n_elements == 1
    assert mon.n_atoms == 2
    assert mon.parent_system == 'results/material/topology/3'
    assert mon.child_systems is None


@pytest.mark.parametrize(
    'fixture, dimensionality',
    [
        pytest.param('atom', '0D', id='atom'),
        pytest.param('molecule', '0D', id='molecule'),
    ],
)
def test_topology_original(fixture, dimensionality, request):
    # Test that some entries only get a topology for the original system.
    entry = request.getfixturevalue(fixture)
    topology = entry.results.material.topology
    assert_topology(topology)
    assert len(topology) == 1
    assert topology[0].label == 'original'
    assert topology[0].structural_type is None
    assert topology[0].building_block is None
    assert topology[0].dimensionality == dimensionality


def test_topology_1d(one_d):
    """Test that typical 1D entries get a topology that contains one subsystem
    and the conventional cell.
    """
    topology = one_d.results.material.topology
    assert_topology(topology)
    assert len(topology) == 3
    orig = topology[0]
    assert orig.label == 'original'
    assert orig.structural_type is None
    assert orig.dimensionality == '1D'
    assert orig.building_block is None
    sub = topology[1]
    assert sub.label == 'subsystem'
    assert sub.system_relation.type == 'subsystem'
    assert sub.structural_type == '1D'
    assert sub.dimensionality == '1D'
    assert sub.building_block is None
    conv = topology[2]
    assert conv.label == 'conventional cell'
    assert conv.material_id is not None
    assert conv.system_relation.type == 'conventional_cell'
    assert conv.structural_type == '1D'
    assert conv.dimensionality == '1D'
    assert conv.building_block is None
    assert conv.symmetry is None
    assert conv.n_atoms == 4
    assert conv.atoms.labels == ['C', 'C', 'H', 'H']
    assert np.array_equal(conv.atoms.periodic, [True, False, False])
    assert conv.cell.a.to(ureg.angstrom).magnitude == pytest.approx(2.459, abs=1e-3)
    assert conv.cell.b is None
    assert conv.cell.c is None
    assert conv.cell.alpha is None
    assert conv.cell.beta is None
    assert conv.cell.gamma is None


@pytest.mark.parametrize(
    'surface, ref_topologies',
    [
        pytest.param(
            rattle(surf(conv_fcc('Cu'), [1, 0, 0])),
            single_cu_surface_topology(),
            id='single surface Cu FCC 100',
        ),
        pytest.param(
            rattle(surf(conv_fcc('Cu'), [1, 1, 0])),
            single_cu_surface_topology(),
            id='single surface Cu FCC 110',
        ),
        pytest.param(
            rattle(surf(conv_bcc('Cr'), [1, 0, 0])),
            single_cr_surface_topology(),
            id='single surface Cr BCC 100',
        ),
        pytest.param(
            rattle(surf(conv_bcc('Cr'), [1, 1, 0])),
            single_cr_surface_topology(),
            id='single surface Cr BCC 110',
        ),
        pytest.param(
            rattle(
                stack(
                    surf(conv_fcc('Cu'), [1, 0, 0], vacuum=None),
                    surf(conv_fcc('Ni'), [1, 0, 0], vacuum=None),
                )
            ),
            stacked_cu_ni_surface_topology(),
            id='stacked surfaces of Cu and Ni',
        ),
        pytest.param(
            rattle(graphene()), graphene_topology(), id='single 2D layer of graphene'
        ),
        pytest.param(
            rattle(boron_nitride()),
            boron_nitride_topology(),
            id='single 2D layer of BN',
        ),
        pytest.param(rattle(mos2()), mos2_topology(), id='single 2D layer of MoS2'),
        pytest.param(
            rattle(stack(graphene(), boron_nitride())),
            stacked_graphene_boron_nitride_topology(),
            id='stacked layer of BN and C',
        ),
    ],
)
def test_topology_2d(surface, ref_topologies):
    def compare_section(real, ref):
        """Used to compare two metainfo sections."""
        for name in ref.m_def.all_quantities.keys():
            compare_quantity(name, real, ref)

    def compare_quantity(name, value, ref):
        """Used to compare two metainfo quantities."""
        real_value = getattr(value, name)
        ref_value = getattr(ref, name)
        if ref_value is None:
            assert real_value is None
        elif isinstance(ref_value, ureg.Quantity):
            assert real_value.magnitude == pytest.approx(
                ref_value.magnitude, rel=0.01, abs=0
            )
        elif isinstance(ref_value, np.ndarray | list):
            real_array = np.array(real_value)
            ref_array = np.array(ref_value)
            if ref_array.dtype == bool:
                assert np.array_equal(real_array, ref_array)
            else:
                assert np.isclose(real_array, ref_array, rtol=0.01, atol=0)
        else:
            raise ValueError('Could not compare values.')

    entry_archive = get_template_for_structure(surface)
    topology = entry_archive.results.material.topology
    assert_topology(topology)
    assert topology[0].system_relation.type == 'root'
    subsystem_topologies = topology[1:]
    # Compare topology with reference system topology. topology[0] is the original system
    assert len(subsystem_topologies) == len(ref_topologies)
    for res_system in subsystem_topologies:
        formula_hill = res_system.chemical_formula_hill
        for ref_top_counter, ref_topology in enumerate(ref_topologies):
            if ref_topology.chemical_formula_hill == formula_hill:
                ref_formula_hill = ref_topology.chemical_formula_hill
                ref_index = ref_top_counter
                break
        ref_elements = ref_topologies[ref_index].elements
        elements = res_system.elements
        assert elements == ref_elements
        assert formula_hill == ref_formula_hill

        ref_system = ref_topologies[ref_index]
        assert res_system.structural_type == ref_system.structural_type
        assert res_system.dimensionality == ref_system.dimensionality
        assert res_system.building_block == ref_system.building_block
        assert res_system.system_relation.type == ref_system.system_relation.type

        if res_system.label == 'conventional cell':
            # Cell
            compare_section(res_system.cell, ref_topologies[ref_index].cell)

            # Symmetry
            if ref_topologies[ref_index].symmetry:
                symmetry = res_system.symmetry.m_to_dict()
                ref_symmetry = ref_topologies[ref_index].symmetry.m_to_dict()
                for (
                    ref_symmetry_property_key,
                    ref_symmetry_property,
                ) in ref_symmetry.items():
                    symmetry_property = symmetry[ref_symmetry_property_key]
                    assert ref_symmetry_property == symmetry_property
            else:
                assert res_system.symmetry == ref_topologies[ref_index].symmetry

            # Atoms
            atoms = res_system.atoms.m_to_dict()
            ref_atoms = ref_topologies[ref_index].atoms.m_to_dict()
            for ref_atoms_property_key, ref_atoms_property in ref_atoms.items():
                if ref_atoms_property_key == 'm_def':
                    continue
                atoms_property = atoms[ref_atoms_property_key]
                if isinstance(atoms_property, list):
                    property = atoms_property[0]
                    if isinstance(property, list):
                        assert np.allclose(
                            atoms_property, ref_atoms_property, rtol=1e-05, atol=1e-9
                        )
                    elif isinstance(property, dict):
                        for property_keys, property_values in property.items():
                            ref_property = ref_atoms_property[0][property_keys]
                            assert property_values == ref_property
                elif isinstance(atoms_property, dict):
                    for property_keys, property_values in atoms_property.items():
                        ref_property_value = ref_atoms_property[property_keys]
                        if isinstance(property_values, float):
                            assert np.allclose(
                                property_values,
                                ref_property_value,
                                rtol=1e-05,
                                atol=1e-9,
                            )
                        else:
                            assert ref_atoms_property == property_values
                elif isinstance(atoms_property, float):
                    assert np.allclose(
                        ref_atoms_property, atoms_property, rtol=1e-05, atol=1e-9
                    )
                else:
                    assert ref_atoms_property == atoms_property

        elif res_system.label == 'subsystem':
            # Indices: passes if the index overlap is large enough
            ref_indices = ref_topologies[ref_index].indices[0].tolist()
            indices = res_system.indices[0].tolist()
            indices_overlap = set(ref_indices).intersection(set(indices))
            assert len(indices_overlap) / len(ref_indices) > 0.85


def test_topology_3d(bulk):
    """Test that typical bulk entries get a topology that contains one subsystem
    and the conventional cell.
    """
    topology = bulk.results.material.topology
    assert_topology(topology)
    assert len(topology) == 3
    orig = topology[0]
    assert orig.label == 'original'
    assert orig.structural_type is None
    assert orig.dimensionality == '3D'
    assert orig.building_block is None
    sub = topology[1]
    assert sub.label == 'subsystem'
    assert sub.system_relation.type == 'subsystem'
    assert sub.structural_type == 'bulk'
    assert sub.dimensionality == '3D'
    assert sub.building_block is None
    conv = topology[2]
    assert conv.label == 'conventional cell'
    assert conv.material_id is not None
    assert conv.system_relation.type == 'conventional_cell'
    assert conv.structural_type == 'bulk'
    assert conv.dimensionality == '3D'
    assert conv.building_block is None
    assert conv.cell is not None

    assert conv.n_atoms == 8
    assert conv.atoms.labels == ['Si', 'Si', 'Si', 'Si', 'Si', 'Si', 'Si', 'Si']
    assert np.array_equal(conv.atoms.periodic, [True, True, True])
    assert conv.cell.a.to(ureg.angstrom).magnitude == pytest.approx(5.431, abs=1e-3)
    assert conv.cell.b.to(ureg.angstrom).magnitude == pytest.approx(5.431, abs=1e-3)
    assert conv.cell.c.to(ureg.angstrom).magnitude == pytest.approx(5.431, abs=1e-3)
    assert conv.cell.alpha.magnitude == pytest.approx(np.pi / 2)
    assert conv.cell.beta.magnitude == pytest.approx(np.pi / 2)
    assert conv.cell.gamma.magnitude == pytest.approx(np.pi / 2)

    assert conv.symmetry.crystal_system is not None
    assert conv.symmetry.bravais_lattice is not None
    assert conv.symmetry.space_group_symbol is not None
    assert conv.symmetry.space_group_number is not None
    assert conv.symmetry.point_group is not None
    assert conv.symmetry.hall_number is not None
    assert conv.symmetry.hall_symbol is not None
    assert conv.symmetry.prototype_name is not None
    assert conv.symmetry.prototype_label_aflow is not None
    assert conv.symmetry.wyckoff_sets is not None
    assert conv.atoms is not None


def test_topology_tb_wannier(tb_wannier):
    system = tb_wannier.run[-1].system[-1]
    assert system.type == 'bulk'
    assert len(system.atoms_group) == 1
    sec_atoms_group = system.atoms_group[-1]
    assert sec_atoms_group.type == 'active_orbitals'
    assert sec_atoms_group.n_atoms == 1
    assert not sec_atoms_group.is_molecule
    assert sec_atoms_group.atom_indices[0] == 0
    assert not sec_atoms_group.atoms_group
    material = tb_wannier.results.material
    assert material.structural_type == system.type
    assert material.topology
    topology = material.topology
    assert_topology(topology)
    assert len(topology) == 2
    assert topology[0].label == 'original'
    assert topology[0].system_relation.type == 'root'
    assert topology[1].label == sec_atoms_group.label == 'projection'
    assert topology[1].structural_type == 'active orbitals'
    assert topology[1].building_block is None
    assert topology[1].system_relation.type == 'group'
    assert len(topology[0].child_systems) == 1
    assert topology[0].child_systems[-1] == topology[1].system_id
    assert topology[0].elements == ['Br', 'K', 'Si']
    assert topology[1].elements == ['Br']


@pytest.mark.parametrize(
    'settings',
    [
        {'e': 0.15, 'li': 3, 'ls': 'f'},
        {'state': 'initial', 'e': 0.3, 'li': 3, 'ls': 'f', 'n': 4},
        {'e': 0.9, 'li': 2, 'ls': 'd', 'n': 4, 'ml': -2, 'mls': 'xy'},
        {'e': 0.25, 'li': 3, 'ls': 'f', 'ms': False},
        {'e': 0.5, 'li': 1, 'ls': 'p', 'n': 4, 'ml': 0, 'mls': 'z', 'ms': False},
        # {'state': 'initial', 'li': 2, 'ji': [2.5], 'ms': True, 'mss': 'up'},
        # {'e': .6, 'li': 2, 'mj': [1.5]},
        # {'i': [1, 2], 'e': [1, 0], 'li': [1, 2], 'n': [2, 4], 'ml': [0, None], 'ms': [None, False]},
    ],
)
def test_topology_core_hole(settings):
    """test several core-hole setups"""
    template = get_template_active_orbitals(
        settings.get(
            'i', [0]
        ),  # FIXME: remove this once multiple core_holes are supported
        n_electrons_excited=settings.get('e', 0),
        l_quantum_number=settings.get('li', 0),
        n_quantum_number=settings.get('n'),
        ml_quantum_number=settings.get('ml'),
        ms_quantum_bool=settings.get('ms'),
        j_quantum_number=settings.get('ji', []),
        mj_quantum_number=settings.get('mj', []),
    )
    template = run_normalize(template)
    evaluation = check_template_active_orbitals(
        template,
        # degeneracy=settings.get('degen', 1),
        n_electrons_excited=settings.get('e', 0),
        l_quantum_number=settings.get('li', 0),
        l_quantum_symbol=settings.get('ls', 's'),
        n_quantum_number=settings.get('n'),
        ml_quantum_number=settings.get('ml'),
        ml_quantum_symbol=settings.get('mls'),
        ms_quantum_bool=settings.get('ms'),
        ms_quantum_symbol=settings.get('mss'),
        j_quantum_number=settings.get('ji'),
        mj_quantum_number=settings.get('mj'),
    )
    assert all(evaluation.values())


@pytest.mark.parametrize(
    'atom_labels, atom_parameters, expected_mass_density',
    [
        pytest.param(
            ['Br', 'K', 'Si', 'Si'],
            [('Si', 0.0), ('K', 0.0), ('Br', 0.0)],
            2148.4338041312812,
            id='test the calculation of mass_density via the default route, i.e., using masses based on atomic masses, even when masses via atom parameters (compuational model) is available',
        ),
        pytest.param(
            ['XBr', 'XK', 'XSi', 'XSi'],
            [
                ('XSi', 280.0855 * ureg.amu),
                ('XK', 390.0983 * ureg.amu),
                ('XBr', 790.904 * ureg.amu),
            ],
            21354.827342813187,
            id='test the calculation of mass_density for the case of unknown elements, with the masses in atom_parameters are stored as a dict',
        ),
        pytest.param(
            ['XBr', 'XK', 'XSi', 'XSi'],
            [
                (None, 790.904 * ureg.amu),
                (None, 390.0983 * ureg.amu),
                (None, 280.0855 * ureg.amu),
                (None, 280.0855 * ureg.amu),
            ],
            21354.827342813187,
            id='test the calculation of mass_density for the case of unknown elements, with the masses in atom_parameters are stored as a list',
        ),
    ],
)
def test_mass_density(atom_labels, atom_parameters, expected_mass_density):
    """Checks the mass_density is calculated correctly with different mass sources."""
    template = get_template_computation()
    run = template.run[-1]
    system = run.system[-1]
    if runschema:
        method = runschema.method.Method()
        run.method.append(method)
        method.atom_parameters = [
            runschema.method.AtomParameters(label=param[0], mass=param[1])
            for param in atom_parameters
        ]
        system.atoms.labels = atom_labels
        run_normalize(template)
        cell = template.results.material.topology[-1].cell
        assert cell.mass_density.magnitude == pytest.approx(
            expected_mass_density, abs=0, rel=1e-6
        )
