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

import ase
import numpy as np

from nomad.datamodel.data import ArchiveSection
from nomad.metainfo import Package, Quantity, Section, SectionProxy, SubSection
from nomad.units import ureg

# TODO System should be redefined from base section


m_package = Package()


class AtomsGroup(ArchiveSection):
    """
    Describes a group of atoms which may constitute a sub system as in the case of a
    molecule.
    """

    m_def = Section(validate=False)

    label = Quantity(
        type=str,
        shape=[],
        description="""
        Label of the group.
        """,
    )

    type = Quantity(
        type=str,
        shape=[],
        description="""
        Type of the group.
        """,
    )

    index = Quantity(
        type=int,
        shape=[],
        description="""
        Index of the group with respect to its parent group.
        """,
    )

    composition_formula = Quantity(
        type=str,
        shape=[],
        description="""
        The overall composition of the group with respect to its subgroups.
        The syntax for a groups composed of X and Y with x and y components of each,
        respectively, is X(x)Y(y).
        """,
    )

    n_atoms = Quantity(
        type=int,
        shape=[],
        description="""
        The total number of atoms in the group.
        """,
    )

    atom_indices = Quantity(
        type=np.dtype(np.int32),
        shape=['n_atoms'],
        description="""
        Indices of the atoms in the group with respect to the system.
        """,
    )

    is_molecule = Quantity(
        type=bool,
        shape=[],
        description="""
        Denotes if the atoms in this group represent a molecule. That is, all atoms
        in the group are connected via bonds, and no other atoms contain bonds
        with these atoms.
        """,
    )

    bond_list = Quantity(
        type=np.int32,
        shape=[],
        description="""
        List of pairs of atom indices corresponding to bonds (e.g., as defined by a force field) within this atoms_group.
        """,
    )

    atoms_group = SubSection(sub_section=SectionProxy('AtomsGroup'), repeats=True)


class Atoms(ArchiveSection):
    """
    Describes the atomic structure of the physical system. This includes the atom
    positions, lattice vectors, etc.
    """

    m_def = Section(validate=False)

    n_atoms = Quantity(
        type=int,
        shape=[],
        description="""
        The total number of species (atoms, particles) in the system.
        """,
    )

    atomic_numbers = Quantity(
        type=np.dtype(np.int32),
        shape=['n_atoms'],
        description="""
        List of atomic numbers Z of the atoms identified in labels. If a species cannot
        be assigned Z, a negative value can also be used to distinguish it.
        """,
    )

    equivalent_atoms = Quantity(
        type=np.dtype(np.int32),
        shape=['n_atoms'],
        description="""
        Gives a mapping table of atoms to symmetrically independent atoms in the
        standardized cell. This is used to find symmetrically equivalent atoms.
        """,
    )

    wyckoff_letters = Quantity(
        type=str,
        shape=['n_atoms'],
        description="""
        Wyckoff letters corresponding to each atom.
        """,
    )

    concentrations = Quantity(
        type=np.dtype(np.float64),
        shape=['n_atoms'],
        description="""
        Concentrations of the species defined by labels which can be assigned for systems
        with variable compositions.
        """,
    )

    species = Quantity(
        type=np.dtype(np.int32),
        shape=['n_atoms'],
        description="""
        Species of the atom (normally the atomic number Z, 0 or negative for unidentifed
        species or particles that are not atoms.
        """,
    )

    labels = Quantity(
        type=str,
        shape=['n_atoms'],
        description="""
        List containing the labels of the atoms. In the usual case, these correspond to
        the chemical symbols of the atoms. One can also append an index if there is a
        need to distinguish between species with the same symbol, e.g., atoms of the
        same species assigned to different atom-centered basis sets or pseudo-potentials,
        or simply atoms in different locations in the structure such as those in the bulk
        and on the surface. In the case where a species is not an atom, and therefore
        cannot be represented by a chemical symbol, the label can simply be the name of
        the particles.
        """,
    )

    positions = Quantity(
        type=np.dtype(np.float64),
        shape=['n_atoms', 3],
        unit='meter',
        description="""
        Positions of all the species, in cartesian coordinates. This metadata defines a
        configuration and is therefore required. For alloys where concentrations of
        species are given for each site in the unit cell, it stores the position of the
        sites.
        """,
    )

    velocities = Quantity(
        type=np.dtype(np.float64),
        shape=['n_atoms', 3],
        unit='meter / second',
        description="""
        Velocities of the nuclei, defined as the change in cartesian coordinates of the
        nuclei with respect to time.
        """,
    )

    lattice_vectors = Quantity(
        type=np.dtype(np.float64),
        shape=[3, 3],
        unit='meter',
        description="""
        Lattice vectors of the simulation cell in cartesian coordinates. The
        last (fastest) index runs over the $x,y,z$ Cartesian coordinates, and the first
        index runs over the 3 lattice vectors.
        """,
    )

    lattice_vectors_reciprocal = Quantity(
        type=np.dtype(np.float64),
        shape=[3, 3],
        unit='1/meter',
        description="""
        Reciprocal lattice vectors of the simulation cell, in cartesian coordinates and with the 2 $pi$ pre-factor.
        The first index runs over the $x,y,z$ Cartesian coordinates, and the second index runs
        over the 3 lattice vectors.
        """,
    )

    local_rotations = Quantity(
        type=np.dtype(np.float64),
        shape=['n_atoms', 3, 3],
        description="""
        A rotation matrix defining the orientation of each atom. If the rotation matrix
        cannot be specified for an atom, the remaining atoms should set it to
        the zero matrix (not the identity!)
        """,
    )

    periodic = Quantity(
        type=bool,
        shape=[3],
        description="""
        Denotes if periodic boundary condition is applied to each of the lattice vectors.'
        """,
    )

    supercell_matrix = Quantity(
        type=np.dtype(np.int32),
        shape=[3, 3],
        description="""
        Specifies the matrix that transforms the unit-cell into the super-cell in which
        the actual calculation is performed.
        """,
    )

    symmorphic = Quantity(
        type=bool,
        shape=[],
        description="""
        Specifies if the space group is symmorphic. Set to True if all translations are
        zero.
        """,
    )

    bond_list = Quantity(
        type=np.int32,
        shape=['*', '2'],
        description="""
        List of pairs of atom indices corresponding to bonds (e.g., as defined by a force field) within the entire system.
        """,
    )

    def to_ase(self, raise_exp: bool = False):
        """
        Returns an ASE Object that represents the data in this section.

        Arguments:
            raise_exp: Optional flag to raise an exception instead of return None, if
                the ASE object could not be created.

        Returns:
            The ASE Object or None, if the ASE Object could not be created (e.g. due
            to missing data).
        """
        try:
            return ase.Atoms(
                symbols=self.labels,
                positions=self.positions.to(ureg.angstroms).m,
                cell=self.lattice_vectors.to(ureg.angstroms).m,
                pbc=self.periodic,
            )
        except Exception as e:
            if raise_exp:
                raise e
            return None


m_package.__init_metainfo__()
