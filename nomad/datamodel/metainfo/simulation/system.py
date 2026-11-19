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

# DO NOT ANYMORE USE OR EXTEND THE SCHEMA.
# Only for purpose of compatibility. Use run schema plugin.
# https://github.com/nomad-coe/nomad-schema-plugin-run.git

import typing  # noqa: F401

import numpy as np  # noqa: F401

from nomad.datamodel.data import ArchiveSection
from nomad.metainfo import (  # noqa: F401
    Category,
    MCategory,
    MEnum,
    MSection,
    Package,
    Quantity,
    Reference,
    Section,
    SectionProxy,
    SubSection,
    derived,
)
from nomad.metainfo.data_type import m_float64
from nomad.units import ureg

from ..common import FastAccess

m_package = Package()


class AtomsGroup(MSection):
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


class Atoms(MSection):
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
        type=m_float64(dtype=np.float64).no_shape_check(),
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
        shape=[],
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
            from ase import Atoms

            return Atoms(
                symbols=self.labels,
                positions=self.positions.to(ureg.angstroms).m,
                cell=self.lattice_vectors.to(ureg.angstroms).m,
                pbc=self.periodic,
            )
        except Exception as e:
            if raise_exp:
                raise e
            return None


class Symmetry(MSection):
    """
    Section containing information about the symmetry properties of the atomic system.
    """

    m_def = Section(validate=False)

    bravais_lattice = Quantity(
        type=str,
        shape=[],
        description="""
        Identifier for the Bravais lattice in Pearson notation. The first lowercase letter
        identifies the crystal family and can be one of the following: a (triclinic), b
        (monoclinic), o (orthorhombic), t (tetragonal), h (hexagonal) or c (cubic). The
        second uppercase letter identifies the centring and can be one of the following: P
        (primitive), S (face centred), I (body centred), R (rhombohedral centring) or F
        (all faces centred).
        """,
    )

    choice = Quantity(
        type=str,
        shape=[],
        description="""
        String that specifies the centering, origin and basis vector settings of the 3D
        space group that defines the symmetry group of the simulated physical system (see
        section system). Values are as defined by spglib.
        """,
    )

    crystal_system = Quantity(
        type=str,
        shape=[],
        description="""
        Name of the crystal system. Can be one of the following: triclinic, monoclinic,
        orthorhombic, tetragonal, trigonal, hexagonal or cubic.
        """,
    )

    hall_number = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        The Hall number for this system.
        """,
    )

    hall_symbol = Quantity(
        type=str,
        shape=[],
        description="""
        The Hall symbol for this system.
        """,
    )

    international_short_symbol = Quantity(
        type=str,
        shape=[],
        description="""
        Specifies the International Union of Crystallography (IUC) short symbol of the 3D
        space group of this system
        """,
    )

    origin_shift = Quantity(
        type=np.dtype(np.float64),
        shape=[3],
        description="""
        Vector $\\mathbf{p}$ from the origin of the standardized system to the origin of
        the original system. Together with the matrix $\\mathbf{P}$, found in
        space_group_3D_transformation_matrix, the transformation between the standardized
        coordinates $\\mathbf{x}_s$ and original coordinates $\\mathbf{x}$ is then given
        by $\\mathbf{x}_s = \\mathbf{P} \\mathbf{x} + \\mathbf{p}$.
        """,
    )

    point_group = Quantity(
        type=str,
        shape=[],
        description="""
        Symbol of the crystallographic point group in the Hermann-Mauguin notation.
        """,
    )

    space_group_number = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Specifies the International Union of Crystallography (IUC) number of the 3D space
        group of this system.
        """,
    )

    symmetry_method = Quantity(
        type=str,
        shape=[],
        description="""
        Identifies the source of the symmetry information contained within this section.
        If equal to 'spg_normalized' the information comes from a normalization step.
        """,
    )

    transformation_matrix = Quantity(
        type=np.dtype(np.float64),
        shape=[3, 3],
        description="""
        Matrix $\\mathbf{P}$ that is used to transform the standardized coordinates to the
        original coordinates. Together with the vector $\\mathbf{p}$, found in
        space_group_3D_origin_shift, the transformation between the standardized
        coordinates $\\mathbf{x}_s$ and original coordinates $\\mathbf{x}$ is then given by
        $\\mathbf{x}_s = \\mathbf{P} \\mathbf{x} + \\mathbf{p}$.
        """,
    )

    system_original = SubSection(sub_section=Atoms.m_def, repeats=True)

    system_primitive = SubSection(sub_section=Atoms.m_def, repeats=True)

    system_std = SubSection(sub_section=Atoms.m_def, repeats=True)


class Prototype(MSection):
    """
    Information on the prototype corresponding to the current section.
    """

    m_def = Section(validate=False)

    aflow_id = Quantity(
        type=str,
        shape=[],
        description="""
        AFLOW id of the prototype (see
        http://aflowlib.org/CrystalDatabase/prototype_index.html) identified on the basis
        of the space_group and normalized_wyckoff.
        """,
    )

    aflow_url = Quantity(
        type=str,
        shape=[],
        description="""
        Url to the AFLOW definition of the prototype (see
        http://aflowlib.org/CrystalDatabase/prototype_index.html) identified on the basis
        of the space_group and normalized_wyckoff.
        """,
    )

    assignment_method = Quantity(
        type=str,
        shape=[],
        description="""
        Method used to identify the prototype.
        """,
    )

    label = Quantity(
        type=str,
        shape=[],
        description="""
        Label of the prototype identified on the basis of the space_group and
        normalized_wyckoff. The label is in the same format as in the read_prototypes
        function: <space_group_number>-<prototype_name>-<Pearson's symbol>).
        """,
    )


class SpringerMaterial(MSection):
    """
    Contains results of classification of materials with the same formula according to
    Springer Materials. These include material and compound classification, formula,
    id, and references from the Springer Materials database.
    """

    m_def = Section(validate=False)

    id = Quantity(
        type=str,
        shape=[],
        description="""
        Id of the classified material according to Springer Materials.
        """,
    )

    alphabetical_formula = Quantity(
        type=str,
        shape=[],
        description="""
        The alphabetical formula of the material according to Springer Materials Database
        """,
    )

    url = Quantity(
        type=str,
        shape=[],
        description="""
        Url to the source page in Springer Materials describing the current entry
        """,
    )

    compound_class = Quantity(
        type=str,
        shape=['N'],
        description="""
        Name of a class of the current compound, as defined in by Springer Materials. This
        is a property of the chemical formula of the compound
        """,
    )

    classification = Quantity(
        type=str,
        shape=['N'],
        description="""
        Contains the classification name of the current material according to Springer
        Materials
        """,
    )


class Constraint(MSection):
    """
    Section describing a constraint between arbitrary atoms.
    """

    m_def = Section(validate=False)

    kind = Quantity(
        type=str,
        shape=[],
        description="""
        Short and unique name for this constraint type. Could be fix_a where a can be
        x, y, z denoting a constraint along a cartesian direction, xy, xz, yz denoting a
        constraint along a cartesian plane, xyz denoting a fixed position, distance
        denoting a fixed distance between two atoms, angle denoting a fixed angle between
        three atoms, and dihedral denoting a fixed dihedral angle.
        """,
    )

    n_constraints = Quantity(
        type=int,
        shape=[],
        description="""
        Number of constraints of this type.
        """,
    )

    n_atoms = Quantity(
        type=int,
        shape=[],
        description="""
        Number of atoms involved in this constraint.
        """,
    )

    atom_indices = Quantity(
        type=np.dtype(np.int32),
        shape=['n_constraints', 'n_atoms'],
        description="""
        List of the indexes involved in this constraint.
        """,
    )

    parameters = Quantity(
        type=typing.Any,
        shape=[],
        description="""
        Explicit constraint parameters for this kind of constraint (depending on the
        constraint type, some might be given implicitly through other means).
        """,
    )


class SOAP(MSection):
    m_def = Section(
        description="""
        SOAP descriptors and representations
        """
    )
    n_max = Quantity(
        type=int,
        description="""
        Number of radial basis functions in density expansion
        """,
    )
    l_max = Quantity(
        type=int,
        description="""
        Order of Spherical harmonics.
        """,
    )
    r_cut = Quantity(
        type=np.dtype('float64'),
        description="""
        Cutoff distance defining each local environment.
        """,
    )
    atom_sigma = Quantity(
        type=np.dtype('float64'),
        description="""
        Width of Gaussians used in density expansion
        """,
    )

    soap = Quantity(
        type=np.dtype('float64'),
        shape=['n_sites', '*', '*', '*'],
        description="""
        Full SOAP stored in array format.
        """,
    )
    tr_soap = Quantity(
        type=np.dtype('float64'),
        shape=['n_sites', '*'],
        description="""
        Normalised, tensor-reduced SOAP stored as a flat vector.
        """,
    )
    global_soap = Quantity(
        type=np.dtype('float64'),
        shape=['*', '*', '*'],
        description="""
        Full SOAP, c_znlm averaged across sites, stored in array format.
        """,
    )
    global_tr_soap = Quantity(
        type=np.dtype('float64'),
        shape=['*'],
        description="""
        Normalised, tensor-reduced global, c_znlm averaged across sites, SOAP stored as a flat vector.
        """,
    )


class Descriptors(MSection):
    m_def = Section(
        description="""
        Contains atomic descriptors and structural representations
        """
    )
    soap = SubSection(sub_section=SOAP.m_def, repeats=False)


class System(ArchiveSection):
    """
    Contains parameters describing a system of atomic configuration. These include the
    compound name, atomic positions, lattice vectors, constraints on the atoms, etc.
    """

    m_def = Section(validate=False)

    name = Quantity(
        type=str,
        shape=[],
        description="""
        Specifies the name of the system. This information is provided by the user in some
        codes and is stored here for debugging or visualization purposes.
        """,
    )

    type = Quantity(
        type=str,
        shape=[],
        description="""
        Type of the system (atom, bulk, surface, etc.) which is determined by the
        normalizer.
        """,
    )

    configuration_raw_gid = Quantity(
        type=str,
        shape=[],
        description="""
        checksum of the configuration_core, i.e. the geometry of the system. The values
        are not normalized in any way so equivalent configurations might have different
        values
        """,
    )

    is_representative = Quantity(
        type=bool,
        shape=[],
        description="""
        Most systems in a run are only minor variations of each other. Systems marked
        representative where chosen to be representative for all systems in the run.
        """,
    )

    n_references = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of references to the current section system.
        """,
    )

    sub_system_ref = Quantity(
        type=Reference(SectionProxy('System')),
        shape=[],
        description="""
        Links the current section system to a sub system.
        """,
        categories=[FastAccess],
    )

    systems_ref = Quantity(
        type=Reference(SectionProxy('System')),
        shape=['n_references'],
        description="""
        Links the current section system to other section systems. Such a link is
        necessary for example between the supercell and the reference unit cell in a phonon
        calculation. The relationship should be described by kind and the referred section
        system is given by value. An external url can also be provided in place of value.
        """,
        categories=[FastAccess],
    )

    atoms = SubSection(sub_section=Atoms.m_def, categories=[FastAccess])

    atoms_group = SubSection(sub_section=AtomsGroup.m_def, repeats=True)

    chemical_composition = Quantity(
        type=str,
        shape=[],
        description="""
        The full chemical composition of the system, based on atom species.
        """,
    )

    chemical_composition_hill = Quantity(
        type=str,
        shape=[],
        description="""
        The chemical composition in the hill convention of the system, based on atom species.
        """,
    )

    chemical_composition_reduced = Quantity(
        type=str,
        shape=[],
        description="""
        The chemical composition as reduced formula of the system, based on atom species.
        """,
    )

    chemical_composition_anonymous = Quantity(
        type=str,
        shape=[],
        description="""
        The chemical composition without explicit element names of the system, based on
        atom species.
        """,
    )

    constraint = SubSection(sub_section=Constraint.m_def, repeats=True)

    prototype = SubSection(
        sub_section=Prototype.m_def, repeats=True, categories=[FastAccess]
    )

    springer_material = SubSection(
        sub_section=SpringerMaterial.m_def, repeats=True, categories=[FastAccess]
    )

    symmetry = SubSection(
        sub_section=Symmetry.m_def, repeats=True, categories=[FastAccess]
    )

    descriptors = SubSection(
        sub_section=Descriptors.m_def, repeats=False, categories=[FastAccess]
    )


m_package.__init_metainfo__()
