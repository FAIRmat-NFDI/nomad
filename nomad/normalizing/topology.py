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
import pathlib
from collections import defaultdict
from typing import TYPE_CHECKING

import numpy as np
from ase import Atoms
from ase.data import chemical_symbols as _chemical_symbols
from matid.classification.classifications import (
    Atom,
    Class0D,
    Class1D,
    Class2D,
    Class3D,
    Material2D,
    Surface,
)
from matid.clustering import SBC, Cluster
from matid.symmetry.symmetryanalyzer import SymmetryAnalyzer

from nomad import atomutils, utils
from nomad.config import config
from nomad.datamodel.datamodel import EntryArchive
from nomad.datamodel.results import (
    CoreHole,
    Material,
    Relation,
    System,
    structure_name_map,
)
from nomad.datamodel.results import SymmetryNew as Symmetry
from nomad.normalizing.common import (
    ase_atoms_from_nomad_atoms,
    cell_from_ase_atoms,
    material_id_1d,
    material_id_2d,
    material_id_bulk,
    nomad_atoms_from_ase_atoms,
    structures_2d,
    wyckoff_sets_from_matid,
)

if TYPE_CHECKING:
    from numpy.typing import NDArray

conventional_description: str = 'The conventional cell of the material from which the subsystem is constructed from.'
subsystem_description: str = 'Automatically detected subsystem.'
chemical_symbols: 'NDArray[np.str_]' = np.array(_chemical_symbols)
with open(pathlib.Path(__file__).parent / 'data/top_50k_material_ids.json') as fin:
    top_50k_material_ids = json.load(fin)


def get_topology_id(index: int) -> str:
    """Returns a valid topology identifier with the given index.
    Args:
        index: The index of the topology. Must be unique.

    Returns:
        An identifier string that can be stored in topology.system_id.
    """
    return f'results/material/topology/{index}'


def get_topology_original(atoms=None, archive: EntryArchive | None = None) -> System:
    """
    Creates a new topology item for the original structure.
    """
    dimensionality = None
    try:
        classification = archive.run[0].m_cache['classification']
    except Exception:
        pass
    else:
        dimensionality_map = {
            Class0D: 0,
            Atom: 0,
            Class1D: 1,
            Class2D: 2,
            Surface: 2,
            Material2D: 2,
            Class3D: 3,
        }
        dimension = dimensionality_map.get(classification)
        if dimension is not None:
            dimensionality = f'{dimension}D'

    original = System(
        method='parser',
        label='original',
        description='A representative system chosen from the original simulation.',
        dimensionality=dimensionality,
        system_relation=Relation(type='root'),
        atoms_ref=atoms,
    )

    return original


def add_system_info(
    system: System,
    topologies: dict[str, System],
    masses: list[float] | dict[str, float] | None = None,
) -> None:
    """Given a system with minimal information, attempts to add all values than
    can be derived.
    """

    def get_atoms(system):
        """Resolves the atoms that the system is constructed from."""
        if system.atoms:
            return system.atoms
        if system.atoms_ref:
            return system.atoms_ref
        if system.indices is not None:
            return get_atoms(topologies[system.parent_system])

    atoms = get_atoms(system)
    if atoms:
        if system.cell is None:
            if system.atoms or system.atoms_ref:
                ase_atoms = ase_atoms_from_nomad_atoms(atoms)
                system.cell = cell_from_ase_atoms(ase_atoms, masses, atoms.labels)
        atomic_numbers = (
            atoms.species if atoms.species is not None else atoms.atomic_numbers
        )
        symbols = chemical_symbols[atomic_numbers]

        if system.indices is not None:
            system.atoms_ref = atoms
            total_atoms = len(atomic_numbers)
            atomic_numbers = atomic_numbers if not masses else None
            sub_mass = atomutils.get_summed_mass(
                atomic_numbers=atomic_numbers,
                masses=masses,
                indices=system.indices[0],
                atom_labels=atoms.labels,
            )
            total_mass = atomutils.get_summed_mass(
                atomic_numbers=atomic_numbers,
                masses=masses,
                indices=[],
                atom_labels=atoms.labels,
            )
            if sub_mass and total_mass:
                system.mass_fraction = sub_mass / total_mass

            symbols = symbols[system.indices[0]]
            system.atomic_fraction = len(symbols) / total_atoms
        n_atoms = len(symbols)
        if system.n_atoms is None:
            system.n_atoms = n_atoms
        try:
            formula = atomutils.Formula(''.join(symbols))
        except Exception:
            pass
        else:
            formula.populate(system, descriptive_format='descriptive')


def add_system(
    system: System, topologies: dict[str, System], parent: System | None = None
) -> None:
    """Adds the given system to the topology."""
    index = len(topologies)
    system.system_id = get_topology_id(index)
    if parent:
        children = parent.child_systems if parent.child_systems else []
        children.append(system.system_id)
        if parent.child_systems is not children:
            parent.child_systems = children
        system.parent_system = parent.system_id
    topologies[system.system_id] = system


class TopologyNormalizer:
    """Handles the creation of topology information."""

    def __init__(self, entry_archive, repr_system, repr_symmetry, conv_atoms, logger):
        self.entry_archive = entry_archive
        self.repr_system = repr_system
        self.repr_symmetry = repr_symmetry
        self.structural_type = None
        self.conv_atoms = conv_atoms
        self.logger = logger
        self.masses = atomutils.get_masses_from_computational_model(
            entry_archive, repr_system=repr_system
        )

    def topology(self, material) -> list[System] | None:
        """Returns a dictionary that contains all of the topologies mapped by id."""
        # If topology already exists (e.g. written by another normalizer), do
        # not overwrite it.
        topology = self.entry_archive.m_xpath('results.material.topology')
        if topology:
            return None
        # Next use the topology from the calculation
        topology_calc = self.topology_calculation()
        if topology_calc:
            return topology_calc
        # Finally if no other topology exists, try creating one with MatID
        with utils.timer(self.logger, 'calculating topology with matid'):
            topology_matid = self.topology_matid(material)
        if topology_matid:
            return topology_matid

        return None

    def topology_calculation(self) -> list[System] | None:
        """Extracts the system topology as defined in the original calculation.
        This topology typically comes from e.g. classical force fields that
        define a topology for the system.
        """
        try:
            groups = self.entry_archive.run[0].system[0].atoms_group
            if len(groups) == 0:
                return None
        except Exception:
            return None
        try:
            atoms = self.repr_system.atoms
        except Exception:
            atoms = None
        if atoms is None:
            return None
        elif atoms.positions is None or len(atoms.positions) == 0:
            return None
        elif (atoms.species is None or len(atoms.species) == 0) and (
            atoms.atomic_numbers is None or len(atoms.atomic_numbers) == 0
        ):
            return None

        topology: dict[str, System] = {}
        original = get_topology_original(atoms, self.entry_archive)
        original.atoms_ref = atoms
        add_system(original, topology)
        label_to_indices: dict[str, list] = defaultdict(list)

        def add_group(groups, parent=None):
            if not groups:
                return
            for group in groups:
                label = group.label
                # Groups with the same label are mapped to the same system.  # TODO: change this logic for active orbitals
                old_labels = label_to_indices[label]
                instance_indices = group.atom_indices
                if not len(old_labels):
                    description_map = {
                        'molecule': 'Molecule extracted from the calculation topology.',
                        'molecule_group': 'Group of molecules extracted from the calculation topology.',
                        'monomer_group': 'Group of monomers extracted from the calculation topology.',
                        'monomer': 'Monomer extracted from the calculation topology.',
                        'active_orbitals': 'Orbitals targeted by the calculation.',
                    }
                    structural_type_map = {
                        'active_orbitals': 'active orbitals',
                        'molecule': 'molecule',
                        'molecule_group': 'group',
                        'monomer': 'monomer',
                        'monomer_group': 'group',
                    }  # determines the sub-title
                    building_block_map = {
                        'molecule': 'molecule',
                        'monomer': 'monomer',
                    }
                    relation_map = {
                        'active_orbitals': 'group',
                        'molecule': 'subsystem',
                        'molecule_group': 'group',
                        'monomer': 'subsystem',
                        'monomer_group': 'group',
                    }  # determines the shading of non-highlighted atoms
                    system = System(
                        method='parser',
                        description=description_map.get(group.type),
                        label=group.label,
                        structural_type=structural_type_map.get(group.type),
                        building_block=building_block_map.get(group.type),
                        system_relation=Relation(type=relation_map.get(group.type)),
                    )
                    add_system(system, topology, parent)
                    add_group(group.atoms_group, system)
                    old_labels.append(instance_indices)
                else:
                    if len(old_labels[0]) == len(instance_indices):
                        old_labels.append(instance_indices)
                    else:
                        self.logger.warn(
                            'the topology contains entries with the same label but with '
                            'different number of atoms'
                        )

        add_group(groups, original)
        active_orbital_states = self._extract_orbital()

        # Add the derived system information once all indices etc. are gathered.
        for top in topology.values():
            top.indices = label_to_indices.get(top.label)
            add_system_info(top, topology, masses=self.masses)
            if top.structural_type == 'active orbitals':
                try:
                    top.active_orbitals = active_orbital_states[0]
                    active_orbital_states.pop(0)
                except (
                    IndexError
                ):  # FIXME temporary fix to prevent output from projection parsers
                    self.logger.warn(
                        'Cannot assign all active orbital states to the topology.'
                    )

        return list(topology.values())

    def topology_matid(self, material: Material) -> list[System] | None:
        """
        Returns a list of systems that have been identified with MatID.
        """
        # See if a system is available
        try:
            nomad_atoms = self.repr_system.atoms
            atoms = ase_atoms_from_nomad_atoms(nomad_atoms)
        except Exception:
            return None
        if not atoms or len(atoms) == 0:
            return None

        # Create topology for the original system
        topology: dict[str, System] = {}
        original = get_topology_original(nomad_atoms, self.entry_archive)
        original.atoms_ref = nomad_atoms
        add_system(original, topology)
        add_system_info(original, topology, masses=self.masses)

        # Since we still need to run the old classification code
        # (matid.classification.classify), we use it's results to populate the
        # topology for bulk and 1D systems. Also the new clustering cannot
        # currently be run for systems without a cell. In other cases we run the
        # new classification code (matid.clustering.clusterer).
        n_atoms = len(atoms)
        cell = atoms.get_cell()
        if material.structural_type == 'bulk':
            self._topology_bulk(original, topology)
        elif material.structural_type == '1D':
            self._topology_1d(original, topology)
        # Continue creating topology if system size is not too large
        elif n_atoms <= config.normalize.clustering_size_limit:
            # Add all meaningful clusters to the topology
            sbc = SBC()
            try:
                clusters = sbc.get_clusters(atoms, pos_tol=0.8)
            except Exception as e:
                self.logger.warning(
                    'issue in matid clustering',
                    exc_info=e,
                    error=str(e),
                )
            else:
                for cluster in clusters:
                    subsystem = self._create_subsystem(cluster)
                    if not subsystem:
                        continue
                    structural_type = subsystem.structural_type
                    # If the found cell has many basis atoms, it is more likely that
                    # some of the symmetries were not correctly found than the cell
                    # actually being very complicated. Thus we ignore these clusters to
                    # minimize false-positive and to limit the time spent on symmetry
                    # calculation.
                    cell = cluster.get_cell()
                    if len(cell) > 8:
                        self.logger.info(
                            f'cell with many atoms ({len(cell)}) was ignored'
                        )
                        continue
                    try:
                        conventional_cell = self._create_conv_cell_system(
                            cluster, structural_type
                        )
                    except Exception as e:
                        self.logger.error(
                            'conventional cell information could not be created',
                            exc_info=e,
                            error=str(e),
                        )
                        continue
                    # We only accept the subsystem if the material id exists in the top
                    # 50k materials with most entries attached to them. This ensures
                    # that the material_id link points to valid materials and that we
                    # don't report anything too weird. The top 50k materials are
                    # pre-stored in a pickle file that has been created by using ES
                    # terms aggregation.
                    if conventional_cell.material_id in top_50k_material_ids:
                        add_system(subsystem, topology, original)
                        add_system_info(subsystem, topology, masses=self.masses)
                        add_system(conventional_cell, topology, subsystem)
                        add_system_info(
                            conventional_cell,
                            topology,
                            masses=self.masses
                            if isinstance(self.masses, dict)
                            else None,
                        )
                    else:
                        self.logger.info(
                            f'material_id {conventional_cell.material_id} could not be verified'
                        )

        return list(topology.values())

    def _topology_bulk(self, original, topology) -> None:
        """Creates a topology for bulk structures as detected by the old matid
        classification."""
        if self.conv_atoms is None:
            return None

        # Subsystem
        subsystem = System(
            method='matid',
            label='subsystem',
            dimensionality='3D',
            structural_type='bulk',
            description=subsystem_description,
            system_relation=Relation(type='subsystem'),
            indices=[list(range(original.n_atoms))],
        )
        add_system(subsystem, topology, original)
        add_system_info(subsystem, topology, masses=self.masses)

        # Conventional system
        conv_system = System(
            method='matid',
            label='conventional cell',
            system_relation=Relation(type='conventional_cell'),
            dimensionality='3D',
            structural_type='bulk',
            description=conventional_description,
        )
        conv_system.atoms = nomad_atoms_from_ase_atoms(self.conv_atoms)
        symmetry_analyzer = self.repr_symmetry.m_cache.get('symmetry_analyzer')
        conv_system.symmetry = self._create_symmetry(symmetry_analyzer)
        conv_system.cell = cell_from_ase_atoms(
            self.conv_atoms, masses=self.masses, atom_labels=None
        )
        conv_system.material_id = material_id_bulk(
            symmetry_analyzer.get_space_group_number(),
            symmetry_analyzer.get_wyckoff_sets_conventional(),
        )
        add_system(conv_system, topology, subsystem)
        add_system_info(conv_system, topology, masses=self.masses)

    def _topology_1d(self, original, topology):
        """Creates a topology for 1D structures as detected by the old matid
        classification."""
        if self.conv_atoms is None:
            return None

        # Subsystem
        subsystem = System(
            method='matid',
            label='subsystem',
            dimensionality='1D',
            structural_type='1D',
            description=subsystem_description,
            system_relation=Relation(type='subsystem'),
            indices=[list(range(original.n_atoms))],
        )
        add_system(subsystem, topology, original)
        add_system_info(subsystem, topology, masses=self.masses)

        # Conventional system
        conv_system = System(
            method='matid',
            label='conventional cell',
            system_relation=Relation(type='conventional_cell'),
            dimensionality='1D',
            structural_type='1D',
        )
        conv_system.atoms = nomad_atoms_from_ase_atoms(self.conv_atoms)
        conv_system.cell = cell_from_ase_atoms(
            self.conv_atoms, masses=self.masses, atom_labels=None
        )

        # The lattice parameters that are not well defined for 1D structures are unset
        conv_system.cell.b = None
        conv_system.cell.c = None
        conv_system.cell.alpha = None
        conv_system.cell.beta = None
        conv_system.cell.gamma = None
        conv_system.cell.atomic_density = None
        conv_system.cell.mass_density = None
        conv_system.cell.volume = None

        conv_system.material_id = material_id_1d(self.conv_atoms)
        add_system(conv_system, topology, subsystem)
        add_system_info(conv_system, topology, masses=self.masses)

    def _create_subsystem(self, cluster: Cluster) -> System | None:
        """
        Creates a new subsystem as detected by MatID.
        """
        try:
            dimensionality = cluster.get_dimensionality()
            cell = cluster.get_cell()
            n_repeated_directions = sum(cell.get_pbc())
        except Exception as e:
            self.logger.error(
                'matid cluster classification failed', exc_info=e, error=str(e)
            )
            return None
        structural_type = None
        building_block = None
        if dimensionality == 3:
            structural_type = 'bulk'
        elif dimensionality == 2:
            if n_repeated_directions == 2:
                structural_type = '2D'
                building_block = '2D material'
            elif n_repeated_directions == 3:
                structural_type = 'surface'
                building_block = 'surface'
        if not structural_type:
            return None

        subsystem = System(
            method='matid',
            label='subsystem',
            description=subsystem_description,
            system_relation=Relation(type='subsystem'),
            indices=[list(cluster.indices)],
        )

        subsystem.dimensionality = f'{dimensionality}D'
        subsystem.structural_type = structural_type
        subsystem.building_block = building_block

        return subsystem

    def _create_conv_cell_system(self, cluster: Cluster, structural_type: str):
        """
        Creates a new topology item for a conventional cell.
        """
        symmsystem = System(
            method='matid',
            label='conventional cell',
            system_relation=Relation(type='conventional_cell'),
        )
        if structural_type == '2D':
            self._add_conventional_2d(cluster, symmsystem)
        else:
            self._add_conventional_bulk(cluster, symmsystem)
        symmsystem.description = conventional_description

        return symmsystem

    def _add_conventional_bulk(self, cluster: Cluster, subsystem: System) -> None:
        """
        Creates the subsystem with the symmetry information of the conventional cell
        """
        cell = cluster.get_cell()
        # A big tolerance is used here to allow deviations from exact symmetry
        symm = SymmetryAnalyzer(cell, 1.0)
        conv_system = symm.get_conventional_system()
        subsystem.atoms = nomad_atoms_from_ase_atoms(conv_system)
        spg_number = symm.get_space_group_number()
        subsystem.cell = cell_from_ase_atoms(
            conv_system, masses=self.masses, atom_labels=None
        )
        symmetry = self._create_symmetry(symm)
        wyckoff_sets = symm.get_wyckoff_sets_conventional()
        material_id = material_id_bulk(spg_number, wyckoff_sets)
        subsystem.structural_type = 'bulk'
        subsystem.dimensionality = '3D'
        subsystem.material_id = material_id
        subsystem.symmetry = symmetry

    def _add_conventional_2d(self, cluster: Cluster, subsystem: System) -> None:
        """
        Creates the subsystem with the symmetry information of the conventional cell.
        """
        cell = cluster.get_cell()
        conv_atoms, _, wyckoff_sets, spg_number = structures_2d(cell)
        subsystem.cell = cell_from_ase_atoms(
            conv_atoms, masses=self.masses, atom_labels=None
        )
        subsystem.atoms = nomad_atoms_from_ase_atoms(conv_atoms)

        # Here we zero out the irrelevant lattice parameters to correctly handle
        # 2D systems with nonzero thickness (e.g. MoS2).
        subsystem.cell.c = None
        subsystem.cell.alpha = None
        subsystem.cell.beta = None
        subsystem.cell.atomic_density = None
        subsystem.cell.mass_density = None
        subsystem.cell.volume = None

        subsystem.structural_type = '2D'
        subsystem.dimensionality = '2D'
        subsystem.building_block = '2D material'
        subsystem.material_id = material_id_2d(spg_number, wyckoff_sets)

    def _create_symmetry(self, symm: SymmetryAnalyzer) -> Symmetry:
        international_short = symm.get_space_group_international_short()
        conv_system = symm.get_conventional_system()

        sec_symmetry = Symmetry()
        sec_symmetry.symmetry_method = 'MatID'
        sec_symmetry.space_group_number = symm.get_space_group_number()
        sec_symmetry.space_group_symbol = international_short
        sec_symmetry.hall_number = symm.get_hall_number()
        sec_symmetry.hall_symbol = symm.get_hall_symbol()
        sec_symmetry.point_group = symm.get_point_group()
        sec_symmetry.crystal_system = symm.get_crystal_system()
        sec_symmetry.bravais_lattice = symm.get_bravais_lattice()
        sec_symmetry.origin_shift = symm._get_spglib_origin_shift()
        sec_symmetry.transformation_matrix = symm._get_spglib_transformation_matrix()
        sec_symmetry.wyckoff_sets = wyckoff_sets_from_matid(
            symm.get_wyckoff_sets_conventional()
        )

        spg_number = symm.get_space_group_number()
        atom_species = conv_system.get_atomic_numbers()
        if type(conv_system) is Atoms or conv_system.wyckoff_letters is None:
            wyckoffs = symm.get_wyckoff_letters_conventional()
        else:
            wyckoffs = conv_system.wyckoff_letters
        norm_wyckoff = atomutils.get_normalized_wyckoff(atom_species, wyckoffs)
        protoDict = atomutils.search_aflow_prototype(spg_number, norm_wyckoff)

        if protoDict is not None:
            sec_symmetry.prototype_label_aflow = protoDict.get('aflow_prototype_id')
            sec_symmetry.prototype_name = structure_name_map.get(protoDict.get('Notes'))

        return sec_symmetry

    def _extract_orbital(self) -> list[CoreHole]:
        """
        Gather atomic orbitals from `run.method.atom_parameters.core_hole`.
        Also apply normalization in the process.
        """
        # Collect the active orbitals from method
        methods = self.entry_archive.run[-1].method
        if not methods:
            return []
        atom_params = getattr(methods[-1], 'atom_parameters', [])
        active_orbitals_run = [
            param.core_hole for param in atom_params if param.core_hole is not None
        ]
        if (
            len(active_orbitals_run) > 1
        ):  # FIXME: currently only one set of active orbitals is supported, remove for multiple
            self.logger.warn(
                """Multiple sets of active orbitals found.
                Currently, the topology only supports 1, so only the first set is used."""
            )
        # Map the active orbitals to the topology
        active_orbitals_results: list[CoreHole] = []
        for param in getattr(methods[-1], 'atom_parameters', []):
            if (active_orbitals := getattr(param, 'core_hole')) is not None:
                active_orbitals.normalize(
                    EntryArchive(), None
                )  # leave out `archive` and `logger`, to keep normalization local
                active_orbitals_new = CoreHole()
                for quantity_name in active_orbitals.quantities:
                    setattr(
                        active_orbitals_new,
                        quantity_name,
                        getattr(active_orbitals, quantity_name),
                    )
                active_orbitals_new.normalize(None, None)
                active_orbitals_results.append(active_orbitals_new)
                break  # FIXME: currently only one set of active orbitals is supported, remove for multiple
        return active_orbitals_results
