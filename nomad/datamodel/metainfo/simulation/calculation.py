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

import numpy as np  # noqa: F401

from nomad.datamodel.data import ArchiveSection
from nomad.datamodel.metainfo.common import PropertySection, ProvenanceTracker
from nomad.datamodel.metainfo.simulation.method import HoppingMatrix, Method
from nomad.datamodel.metainfo.simulation.system import AtomsGroup, System
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

from ..common import FastAccess

m_package = Package()


class ScfInfo(MCategory):
    """
    Contains information on the self-consistent field (SCF) procedure, i.e. the number of
    SCF iterations (number_of_scf_iterations) or a section_scf_iteration section with
    detailed information on the SCF procedure of specified quantities.
    """

    m_def = Category()


class AccessoryInfo(MCategory):
    """
    Information that *in theory* should not affect the results of the calculations (e.g.,
    timing).
    """

    m_def = Category()


class TimeInfo(MCategory):
    """
    Stores information on the date and timings of the calculation. They are useful for,
    e.g., debugging or visualization purposes.
    """

    m_def = Category(categories=[AccessoryInfo])


class EnergyValue(MCategory):
    """
    This metadata stores an energy value.
    """

    m_def = Category()


class EnergyTypeReference(MCategory):
    """
    This metadata stores an energy used as reference point.
    """

    m_def = Category(categories=[EnergyValue])


class ErrorEstimateContribution(MCategory):
    """
    An estimate of a partial quantity contributing to the error for a given quantity.
    """

    m_def = Category()


class Atomic(MSection):
    """
    Generic section containing the values and information reqarding an atomic quantity
    such as charges, forces, multipoles.
    """

    m_def = Section(validate=False)

    kind = Quantity(
        type=str,
        shape=[],
        description="""
        Kind of the quantity.
        """,
    )

    n_orbitals = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of orbitals used in the projection.
        """,
    )

    n_atoms = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of atoms.
        """,
    )

    n_spin_channels = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of spin channels.
        """,
    )


class AtomicValues(MSection):
    """
    Generic section containing information regarding the values of an atomic quantity.
    """

    m_def = Section(validate=False)

    # TODO rename this to spin_channel
    spin = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Spin channel corresponding to the atomic quantity.
        """,
    )

    atom_label = Quantity(
        type=str,
        shape=[],
        description="""
        Label of the atomic species corresponding to the atomic quantity.
        """,
    )

    atom_index = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Index of the atomic species corresponding to the atomic quantity.
        """,
    )

    m_kind = Quantity(
        type=str,
        shape=[],
        description="""
        String describing what the integer numbers of $m$ lm mean used in orbital
        projections. The allowed values are listed in the [m_kind wiki page]
        (https://gitlab.rzg.mpg.de/nomad-lab/nomad-meta-info/wikis/metainfo/m-kind).
        """,
    )

    lm = Quantity(
        type=np.dtype(np.int32),
        shape=[2],
        description="""
        Tuples of $l$ and $m$ values for which the atomic quantity are given. For
        the quantum number $l$ the conventional meaning of azimuthal quantum number is
        always adopted. For the integer number $m$, besides the conventional use as
        magnetic quantum number ($l+1$ integer values from $-l$ to $l$), a set of
        different conventions is accepted (see the [m_kind wiki
        page](https://gitlab.rzg.mpg.de/nomad-lab/nomad-meta-info/wikis/metainfo/m-kind).
        The adopted convention is specified by m_kind.
        """,
    )

    orbital = Quantity(
        type=str,
        shape=[],
        description="""
        String representation of the of the atomic orbital.
        """,
    )


class AtomicGroup(MSection):
    """
    Generic section containing the values and information reqarding a molecular or sub-molecular
    quantity that is a function of an atomic group such as radius of gyration...
    """

    m_def = Section(validate=False)

    kind = Quantity(
        type=str,
        shape=[],
        description="""
        Kind of the quantity.
        """,
    )


class AtomicGroupValues(MSection):
    """
    Generic section containing information regarding the values of a trajectory property.
    """

    m_def = Section(validate=False)

    label = Quantity(
        type=str,
        shape=[],
        description="""
        Describes the atoms or molecule types involved in determining the property.
        """,
    )

    atomsgroup_ref = Quantity(
        type=Reference(AtomsGroup.m_def),
        shape=[1],
        description="""
        References to the atoms_group section containing the molecule for which Rg was calculated.
        """,
    )


class EnergyEntry(Atomic):
    """
    Section describing a type of energy or a contribution to the total energy.
    """

    m_def = Section(validate=False)

    reference = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the reference energy to be subtracted from value to obtain a
        code-independent value of the energy.
        """,
    )

    # TODO Can we remove reference to unit cell in this description to make more general?
    value = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the energy of the unit cell.
        """,
    )

    value_per_atom = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the energy normalized by the total number of atoms in the simulation
        cell.
        """,
    )

    # TODO rename this to value_atomic
    values_per_atom = Quantity(
        type=np.dtype(np.float64),
        shape=['n_atoms'],
        unit='joule',
        description="""
        Value of the atom-resolved energies.
        """,
    )

    potential = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the potential energy.
        """,
    )

    kinetic = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the kinetic energy.
        """,
    )

    correction = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the correction to the energy.
        """,
    )

    short_range = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the short range contributions to the energy.
        """,
    )

    long_range = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the long range contributions to the energy.
        """,
    )


class Energy(MSection):
    """
    Section containing all energy types and contributions.
    """

    m_def = Section(validate=False)

    total = SubSection(
        sub_section=EnergyEntry.m_def,
        categories=[FastAccess],
        description="""
        Contains the value and information regarding the total energy of the system.
        """,
    )

    # TODO this should be removed and replaced by correction in EnergyEntry
    current = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the energy calculated with
        calculation_method_current. energy_current is equal to energy_total for
        non-perturbative methods. For perturbative methods, energy_current is equal to the
        correction: energy_total minus energy_total of the calculation_to_calculation_ref
        with calculation_to_calculation_kind = starting_point
        """,
    )

    zero_point = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the converged zero-point
        vibrations energy calculated using the method described in zero_point_method.
        """,
    )
    # this should be removed and replaced by electronic.kinetic
    kinetic_electronic = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the self-consistent electronic
        kinetic energy.
        """,
    )

    electronic = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the self-consistent electronic
        energy.
        """,
    )

    correlation = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the correlation energy calculated
        using the method described in XC_functional.
        """,
    )

    exchange = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the exchange energy calculated
        using the method described in XC_functional.
        """,
    )

    xc = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the exchange-correlation (XC)
        energy calculated with the functional stored in XC_functional.
        """,
    )

    # TODO Remove this should use xc.potential
    xc_potential = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the exchange-correlation (XC)
        potential energy: the integral of the first order derivative of the functional
        stored in XC_functional (integral of v_xc*electron_density), i.e., the component
        of XC that is in the sum of the eigenvalues. Value associated with the
        configuration, should be the most converged value..
        """,
    )

    electrostatic = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the total electrostatic energy
        (nuclei + electrons), defined consistently with calculation_method.
        """,
    )

    nuclear_repulsion = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the total nuclear-nuclear repulsion
        energy.
        """,
    )

    # TODO remove this or electrostatic
    coulomb = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the Coulomb energy.
        """,
    )

    madelung = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the Madelung energy.
        """,
    )

    # TODO I suggest ewald is moved to "long range" under electrostatic->energyentry, unless there is some other usage I am misunderstanding
    ewald = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the Ewald energy.
        """,
    )

    free = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the free energy (nuclei + electrons)
        (whose minimum gives the smeared occupation density calculated with
        smearing_kind).
        """,
    )

    sum_eigenvalues = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the sum of the eigenvalues of the
        Hamiltonian matrix.
        """,
    )

    total_t0 = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the total energy extrapolated to
        $T=0$, based on a free-electron gas argument.
        """,
    )

    van_der_waals = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the Van der Waals energy. A multiple
        occurrence is expected when more than one van der Waals methods are defined. The
        van der Waals kind should be specified in Energy.kind
        """,
    )

    hartree_fock_x_scaled = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Scaled exact-exchange energy that depends on the mixing parameter of the
        functional. For example in hybrid functionals, the exchange energy is given as a
        linear combination of exact-energy and exchange energy of an approximate DFT
        functional; the exact exchange energy multiplied by the mixing coefficient of the
        hybrid functional would be stored in this metadata. Defined consistently with
        XC_method.
        """,
    )

    contributions = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains other energy contributions to the total energy not already defined.
        """,
        repeats=True,
    )

    types = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains other energy types not already defined.
        """,
        repeats=True,
    )

    enthalpy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the calculated enthalpy per cell i.e. energy_total + pressure * volume.
        """,
    )

    # TODO Shouldn't this be moved out of energy?
    entropy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule / kelvin',
        description="""
        Value of the entropy.
        """,
    )

    chemical_potential = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the chemical potential.
        """,
    )

    internal = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the internal energy.
        """,
    )

    double_counting = SubSection(
        sub_section=EnergyEntry.m_def,
        categories=[FastAccess],
        description="""
        Double counting correction when performing Hubbard model calculations.
        """,
    )

    # TODO remove this should be be entropy.correction
    correction_entropy = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Entropy correction to the potential energy to compensate for the change in
        occupation so that forces at finite T do not need to keep the change of occupation
        in account. Defined consistently with XC_method.
        """,
    )

    # TODO remove this should be in electrostatic.correction
    correction_hartree = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Correction to the density-density electrostatic energy in the sum of eigenvalues
        (that uses the mixed density on one side), and the fully consistent density-
        density electrostatic energy. Defined consistently with XC_method.
        """,
    )

    # TODO remove this should be in xc.correction
    correction_xc = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Correction to energy_XC.
        """,
    )

    change = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Stores the change of total energy with respect to the previous step.
        """,
        categories=[ErrorEstimateContribution, EnergyValue],
    )

    fermi = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Fermi energy (separates occupied from unoccupied single-particle states)
        """,
        categories=[EnergyTypeReference, EnergyValue],
    )

    highest_occupied = Quantity(
        type=np.dtype(np.float64),
        unit='joule',
        shape=[],
        description="""
        The highest occupied energy.
        """,
    )

    lowest_unoccupied = Quantity(
        type=np.dtype(np.float64),
        unit='joule',
        shape=[],
        description="""
        The lowest unoccupied energy.
        """,
    )

    kinetic = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the kinetic energy.
        """,
    )

    potential = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the potential energy.
        """,
    )

    pressure_volume_work = SubSection(
        sub_section=EnergyEntry.m_def,
        description="""
        Contains the value and information regarding the instantaneous pV work.
        """,
    )


class ForcesEntry(Atomic):
    """
    Section describing a contribution to or type of atomic forces.
    """

    m_def = Section(validate=False)

    value = Quantity(
        type=np.dtype(np.float64),
        shape=['n_atoms', 3],
        unit='newton',
        description="""
        Value of the forces acting on the atoms. This is calculated as minus gradient of
        the corresponding energy type or contribution **including** constraints, if
        present. The derivatives with respect to displacements of nuclei are evaluated in
        Cartesian coordinates.  In addition, these are obtained by filtering out the
        unitary transformations (center-of-mass translations and rigid rotations for
        non-periodic systems, see value_raw for the unfiltered counterpart).
        """,
    )

    value_raw = Quantity(
        type=np.dtype(np.float64),
        shape=['n_atoms', 3],
        unit='newton',
        description="""
        Value of the forces acting on the atoms **not including** such as fixed atoms,
        distances, angles, dihedrals, etc.""",
    )


class Forces(MSection):
    """
    Section containing all forces types and contributions.
    """

    m_def = Section(validate=False)

    total = SubSection(
        sub_section=ForcesEntry.m_def,
        description="""
        Contains the value and information regarding the total forces on the atoms
        calculated as minus gradient of energy_total.
        """,
    )

    free = SubSection(
        sub_section=ForcesEntry.m_def,
        description="""
        Contains the value and information regarding the forces on the atoms
        corresponding to the minus gradient of energy_free. The (electronic) energy_free
        contains the information on the change in (fractional) occupation of the
        electronic eigenstates, which are accounted for in the derivatives, yielding a
        truly energy-conserved quantity.
        """,
    )

    t0 = SubSection(
        sub_section=ForcesEntry.m_def,
        description="""
        Contains the value and information regarding the forces on the atoms
        corresponding to the minus gradient of energy_T0.
        """,
    )

    contributions = SubSection(
        sub_section=ForcesEntry.m_def,
        description="""
        Contains other forces contributions to the total atomic forces not already
        defined.
        """,
        repeats=True,
    )

    types = SubSection(
        sub_section=ForcesEntry.m_def,
        description="""
        Contains other types of forces not already defined.
        """,
        repeats=True,
    )


class StressEntry(Atomic):
    """
    Section describing a contribution to or a type of stress.
    """

    m_def = Section(validate=False)

    value = Quantity(
        type=np.dtype(np.float64),
        shape=[3, 3],
        unit='joule/meter**3',
        description="""
        Value of the stress on the simulation cell. It is given as the functional
        derivative of the corresponding energy with respect to the deformation tensor.
        """,
    )

    values_per_atom = Quantity(
        type=np.dtype(np.float64),
        shape=['number_of_atoms', 3, 3],
        unit='joule/meter**3',
        description="""
        Value of the atom-resolved stresses.
        """,
    )


class Stress(MSection):
    """
    Section containing all stress types and contributions.
    """

    m_def = Section(validate=False)

    total = SubSection(
        sub_section=StressEntry.m_def,
        description="""
        Contains the value and information regarding the stress on the simulation cell
        and the atomic stresses corresponding to energy_total.
        """,
    )

    contributions = SubSection(
        sub_section=StressEntry.m_def,
        description="""
        Contains contributions for the total stress.
        """,
        repeats=True,
    )

    types = SubSection(
        sub_section=StressEntry.m_def,
        description="""
        Contains other types of stress.
        """,
        repeats=True,
    )


class ChargesValue(AtomicValues):
    """
    Contains information on the charge on an atom or projected onto an orbital.
    """

    m_def = Section(validate=False)

    value = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='coulomb',
        description="""
        Value of the charge projected on atom and orbital.
        """,
    )

    n_electrons = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Value of the number of electrons projected on atom and orbital.
        """,
    )

    spin_z = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Value of the azimuthal spin projected on atom and orbital.
        """,
    )


class Charges(Atomic):
    """
    Section describing the charges on the atoms obtained through a given analysis
    method. Also contains information on the orbital projection of charges.
    """

    m_def = Section(validate=False)

    analysis_method = Quantity(
        type=str,
        shape=[],
        description="""
        Analysis method employed in evaluating the atom and partial charges.
        """,
    )

    value = Quantity(
        type=np.dtype(np.float64),
        shape=['n_atoms'],
        unit='coulomb',
        description="""
        Value of the atomic charges calculated through analysis_method.
        """,
    )

    n_electrons = Quantity(
        type=np.dtype(np.float64),
        shape=['n_atoms'],
        description="""
        Value of the number of electrons on the atoms.
        """,
    )

    # TODO should this be on a separate section magnetic_moments or charges should be
    # renamed population
    spins = Quantity(
        type=np.dtype(np.float64),
        shape=['n_atoms'],
        description="""
        Value of the atomic spins.
        """,
    )

    total = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='coulomb',
        description="""
        Value of the total charge of the system.
        """,
    )

    spin_projected = SubSection(sub_section=ChargesValue.m_def, repeats=True)

    orbital_projected = SubSection(sub_section=ChargesValue.m_def, repeats=True)


class BandGapDeprecated(PropertySection):
    """
    Base class for breaking up circular dependencies between BandGap, Dos, and
    BandStructure.
    """

    m_def = Section(validate=False)

    index = Quantity(
        type=np.int32,
        description="""
        The spin channel index.
        """,
    )

    value = Quantity(
        type=np.float64,
        shape=[],
        unit='joule',
        description="""
        The actual value of the band gap. Value of zero indicates a vanishing band gap and
        is distinct from sources lacking any band gap measurement or calculation.""",
    )

    type = Quantity(
        type=MEnum('direct', 'indirect'),
        shape=[],
        description="""
        Band gap type.
        """,
    )

    energy_highest_occupied = Quantity(
        type=np.float64,
        unit='joule',
        shape=[],
        description="""
        The highest occupied energy.
        """,
    )

    energy_lowest_unoccupied = Quantity(
        type=np.float64,
        unit='joule',
        shape=[],
        description="""
        The lowest unoccupied energy.
        """,
    )


class BandEnergies(MSection):
    """
    This section describes the eigenvalue spectrum for a set of kpoints given by
    band_energies_kpoints.
    """

    m_def = Section(validate=False)

    n_spin_channels = Quantity(
        type=int,
        shape=[],
        description="""
        Number of spin channels.
        """,
    )

    n_bands = Quantity(
        type=int,
        shape=[],
        description="""
        Number of bands for which the eigenvalues are evaluated.
        """,
    )

    n_kpoints = Quantity(
        type=int,
        shape=[],
        description="""
        Number of kpoints for which the eigenvalues are evaluated.
        """,
    )

    kpoints = Quantity(
        type=np.dtype(np.float64),
        shape=['n_kpoints', 3],
        description="""
        Fractional coordinates of the $k$ or $q$ points (in the basis of the reciprocal-
        lattice vectors) for which the eigenvalues are evaluated.
        """,
    )

    kpoints_weights = Quantity(
        type=np.dtype(np.float64),
        shape=['n_kpoints'],
        description="""
        Weights of the $k$ points in the calculation of the band energy.
        """,
    )

    kpoints_multiplicities = Quantity(
        type=np.dtype(np.float64),
        shape=['n_kpoints'],
        description="""
        Multiplicities of the $k$ point (i.e., how many distinct points per cell this
        expands to after applying all symmetries). This defaults to 1. If expansion is
        performed then each point will have weight
        band_energies_kpoints_weights/band_energies_kpoints_multiplicities.
        """,
    )

    endpoints_labels = Quantity(
        type=str,
        shape=[2],
        description="""
        Labels of the points along a one-dimensional path sampled in the $k$-space or
        $q$-space, using the conventional symbols, e.g., Gamma, K, L.
        """,
    )

    orbital_labels = Quantity(
        type=str,
        shape=['n_bands'],
        description="""
        Labels corresponding to each band/orbital
        """,
    )

    occupations = Quantity(
        type=np.dtype(np.float64),
        shape=['n_spin_channels', 'n_kpoints', 'n_bands'],
        description="""
        Values of the occupations of the bands.
        """,
    )

    energies = Quantity(
        type=np.dtype(np.float64),
        shape=['n_spin_channels', 'n_kpoints', 'n_bands'],
        unit='joule',
        description="""
        Values of the band energies.
        """,
    )

    qp_linearization_prefactor = Quantity(
        type=np.dtype(np.float64),
        shape=['n_spin_channels', 'n_kpoints', 'n_bands'],
        description="""
        Values of the GW quasi particle linearization pre-factor.
        """,
    )

    value_xc_potential = Quantity(
        type=np.dtype(np.float64),
        shape=['n_spin_channels', 'n_kpoints', 'n_bands'],
        unit='joule',
        description="""
        Diagonal matrix elements of the GW exchange-correlation potential.
        """,
    )

    value_correlation = Quantity(
        type=np.dtype(np.float64),
        shape=['n_spin_channels', 'n_kpoints', 'n_bands'],
        unit='joule',
        description="""
        Diagonal matrix elements of the GW correlation energy.
        """,
    )

    value_exchange = Quantity(
        type=np.dtype(np.float64),
        shape=['n_spin_channels', 'n_kpoints', 'n_bands'],
        unit='joule',
        description="""
        Diagonal matrix elements of the GW exchange energy.
        """,
    )

    value_xc = Quantity(
        type=np.dtype(np.float64),
        shape=['n_spin_channels', 'n_kpoints', 'n_bands'],
        unit='joule',
        description="""
        Diagonal matrix elements of the GW exchange-correlation energy.
        """,
    )

    value_qp = Quantity(
        type=np.dtype(np.float64),
        shape=['n_spin_channels', 'n_kpoints', 'n_bands'],
        unit='joule',
        description="""
        Diagonal matrix elements of the GW quasi-particle energy.
        """,
    )

    value_ks = Quantity(
        type=np.dtype(np.float64),
        shape=['n_spin_channels', 'n_kpoints', 'n_bands'],
        unit='joule',
        description="""
        Diagonal matrix elements of the Kohn-Sham energy.
        """,
    )

    value_ks_xc = Quantity(
        type=np.dtype(np.float64),
        shape=['n_spin_channels', 'n_kpoints', 'n_bands'],
        unit='joule',
        description="""
        Diagonal matrix elements of the Kohn-Sham exchange-correlation energy.
        """,
    )

    band_gap = SubSection(
        sub_section=BandGapDeprecated.m_def, repeats=True
    )  # TODO: check if this can be removed


class BandStructure(MSection):
    """
    This section stores information on a band structure evaluation along one-dimensional
    pathways in the $k$ or $q$ (reciprocal) space given in section_band_segment.
    Eigenvalues calculated at the actual $k$-mesh used for energy_total evaluations,
    can be found in the eigenvalues section.
    """

    m_def = Section(validate=False)

    path_standard = Quantity(
        type=str,
        shape=[],
        description="""
        String to specify the standard used for the kpoints path within bravais
        lattice.
        """,
    )

    reciprocal_cell = Quantity(
        type=np.dtype(np.float64),
        shape=[3, 3],
        unit='1 / meter',
        description="""
        The reciprocal cell within which the band structure is calculated.
        """,
    )

    band_gap = SubSection(sub_section=BandGapDeprecated.m_def, repeats=True)

    energy_fermi = Quantity(
        type=np.dtype(np.float64),
        unit='joule',
        shape=[],
        description="""
        Fermi energy.
        """,
    )

    segment = SubSection(sub_section=BandEnergies.m_def, repeats=True)


class DosFingerprint(MSection):
    """
    Section for the fingerprint of the electronic density-of-states (DOS). DOS
    fingerprints are a modification of the D-Fingerprints reported in Chem. Mater. 2015,
    27, 3, 735–743 (doi:10.1021/cm503507h). The fingerprint consists of a binary
    representation of the DOS, that is used to evaluate the similarity of materials based
    on their electronic structure.
    """

    m_def = Section(validate=False)

    bins = Quantity(
        type=str,
        shape=[],
        description="""
        Byte representation of the DOS fingerprint.
        """,
    )

    indices = Quantity(
        type=np.dtype(np.int32),
        shape=[2],
        description="""
        Indices used to compare DOS fingerprints of different energy ranges.
        """,
    )

    stepsize = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Stepsize of interpolation in the first step of the generation of DOS fingerprints.
        """,
    )

    filling_factor = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Proportion of 1 bins in the DOS fingerprint.
        """,
    )

    grid_id = Quantity(
        type=str,
        shape=[],
        description="""
        Identifier of the DOS grid that was used for the creation of the fingerprint.
        Similarity can only be calculated if the same grid was used for both fingerprints.
        """,
    )


class DosValues(AtomicValues):
    """
    Section containing information regarding the values of the density of states (DOS).
    """

    m_def = Section(validate=False)

    phonon_mode = Quantity(
        type=str,
        shape=[],
        description="""
        Phonon mode corresponding to the DOS used for phonon projections.
        """,
    )

    normalization_factor = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Normalization factor for DOS values to get a cell-independent intensive DOS,
        defined as the DOS integral from the lowest energy state to the Fermi level for a neutrally charged system.
        """,
    )

    value = Quantity(
        type=np.dtype(np.float64),
        shape=['n_energies'],
        unit='1/joule',
        description="""
        Values of DOS, i.e. number of states for a given energy. The set of discrete
        energy values is given in energies.
        """,
    )

    value_integrated = Quantity(
        type=np.dtype(np.float64),
        shape=['n_energies'],
        description="""
        A cumulative DOS starting from the mimunum energy available up to the energy level specified in `energies`.
        """,
    )


class Dos(Atomic):
    """
    Section containing information of an electronic-energy or phonon density of states
    (DOS) evaluation per spin channel.

    It includes the total DOS and the projected DOS values. We differentiate `species_projected` as the
    projected DOS for same atomic species, `atom_projected` as the projected DOS for different
    atoms in the cell, and `orbital_projected` as the projected DOS for the orbitals of each
    atom. These are hierarchically connected as:

        atom_projected = sum_{orbitals} orbital_projected

        species_projected = sum_{atoms} atom_projected

        total = sum_{species} species_projected
    """

    m_def = Section(validate=False)

    n_energies = Quantity(
        type=int,
        shape=[],
        description="""
        Gives the number of energy values for the DOS, see energies.
        """,
    )

    energies = Quantity(
        type=np.float64,
        shape=['n_energies'],
        unit='joule',
        description="""
        Contains the set of discrete energy values for the DOS.
        """,
    )

    energy_fermi = Quantity(
        type=np.float64,
        unit='joule',
        shape=[],
        description="""
        Fermi energy.
        """,
    )

    energy_ref = Quantity(
        type=np.float64,
        unit='joule',
        shape=[],
        description="""
        Energy level denoting the origin along the energy axis, used for comparison and visualization.
        It is defined as the energy_highest_occupied and does not necessarily coincide with energy_fermi.
        """,
    )

    spin_channel = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Spin channel of the corresponding DOS. It can take values of 0 or 1.
        """,
    )

    # TODO total is neither repeated, nor inheriting from AtomicValues; we have to change this when overhauling.
    total = SubSection(sub_section=DosValues.m_def, repeats=True)

    species_projected = SubSection(sub_section=DosValues.m_def, repeats=True)

    atom_projected = SubSection(sub_section=DosValues.m_def, repeats=True)

    orbital_projected = SubSection(sub_section=DosValues.m_def, repeats=True)

    fingerprint = SubSection(sub_section=DosFingerprint.m_def, repeats=False)

    # TODO deprecate this subsection
    band_gap = SubSection(sub_section=BandGapDeprecated.m_def, repeats=True)


class ElectronicStructureProvenance(ProvenanceTracker):
    """
    Provenance information for electronic structure calculations.
    """

    m_def = Section(
        description="""
    """
    )

    dos = Quantity(
        type=Reference(DosValues.m_def),
        shape=[],
        description="""
        """,
    )
    band_structure = Quantity(
        type=Reference(BandEnergies.m_def),
        shape=[],
        description="""
        """,
    )
    methodology = Quantity(
        type=Reference(Method.m_def),
        shape=[],
        description="""
        Reference to the specific method section.
        """,
    )


class BandGap(BandGapDeprecated):
    """
    Band gap information for each spin channel.
    """

    m_def = Section(
        description="""
        Contains information for each present spin channel.
        """
    )

    provenance = SubSection(sub_section=ElectronicStructureProvenance.m_def)


class MultipolesValues(AtomicValues):
    """
    Section containing the values of the multipoles projected unto an atom or orbital.
    """

    m_def = Section(validate=False)

    value = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Value of the multipole.
        """,
    )


class MultipolesEntry(Atomic):
    """
    Section describing a multipole term. The unit of the values are given by C * m ^ n,
    where n = 1 for dipole, 2 for quadrupole, etc.
    """

    m_def = Section(validate=False)

    origin = Quantity(
        type=np.dtype(np.float64),
        shape=[3],
        unit='meter',
        description="""
        Origin in cartesian space.
        """,
    )

    n_multipoles = Quantity(
        type=int,
        shape=[],
        description="""
        Number of multipoles.
        """,
    )

    value = Quantity(
        type=np.dtype(np.float64),
        shape=['n_atoms', 'n_multipoles'],
        description="""
        Value of the multipoles projected unto the atoms.
        """,
    )

    total = Quantity(
        type=np.dtype(np.float64),
        shape=['n_multipoles'],
        description="""
        Total value of the multipoles.
        """,
    )

    orbital_projected = SubSection(sub_section=MultipolesValues.m_def, repeats=True)


class Multipoles(MSection):
    """
    Section containing the multipoles (dipoles, quadrupoles, ...)
    for each atom.
    """

    m_def = Section(validate=False)

    kind = Quantity(
        type=str,
        shape=[],
        description="""
        Kind of the multipoles being described.
        """,
    )

    dipole = SubSection(sub_section=MultipolesEntry.m_def, repeats=False)

    quadrupole = SubSection(sub_section=MultipolesEntry.m_def, repeats=False)

    octupole = SubSection(sub_section=MultipolesEntry.m_def, repeats=False)

    higher_order = SubSection(sub_section=MultipolesEntry.m_def, repeats=True)


# TODO remove this section
class Thermodynamics(MSection):
    """
    Section containing results related to a thermodynamics calculation.
    """

    m_def = Section(validate=False)

    enthalpy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the calculated enthalpy per cell i.e. energy_total + pressure * volume.
        """,
    )

    entropy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule / kelvin',
        description="""
        Value of the entropy.
        """,
    )

    chemical_potential = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the chemical potential.
        """,
    )

    kinetic_energy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the kinetic energy.
        """,
    )

    potential_energy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the potential energy.
        """,
    )

    internal_energy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the internal energy.
        """,
    )

    vibrational_free_energy_at_constant_volume = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the vibrational free energy per cell unit at constant volume.
        """,
    )

    pressure = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='pascal',
        description="""
        Value of the pressure of the system.
        """,
    )

    temperature = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='kelvin',
        description="""
        Value of the temperature of the system at which the properties are calculated.
        """,
    )

    volume = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='m ** 3',
        description="""
        Value of the volume of the system at which the properties are calculated.
        """,
    )

    heat_capacity_c_v = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule / kelvin',
        description="""
        Stores the heat capacity per cell unit at constant volume.
        """,
    )

    heat_capacity_c_p = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule / kelvin',
        description="""
        Stores the heat capacity per cell unit at constant pressure.
        """,
    )

    time_step = Quantity(
        type=int,
        shape=[],
        description="""
        The number of time steps with respect to the start of the calculation.
        """,
    )


class Volumetric(MSection):
    """
    Section defining a set of volumetric data on a uniform real-space grid.
    Kind should be specified if the data is not explicitly defined by a metainfo class.
    """

    m_def = Section(validate=False)

    kind = Quantity(
        type=str,
        shape=[],
        description="""
        The kind of function if not already defined.
        """,
    )

    multiplicity = Quantity(
        type=int,
        shape=[],
        description="""
        Number of functions stored.
        """,
    )

    n_x = Quantity(
        type=int,
        shape=[],
        description="""
        number of points along x axis
        """,
    )

    n_y = Quantity(
        type=int,
        shape=[],
        description="""
        number of points along y axis
        """,
    )

    n_z = Quantity(
        type=int,
        shape=[],
        description="""
        number of points along z axis
        """,
    )

    displacements = Quantity(
        type=np.dtype(np.float64),
        shape=[3, 3],
        unit='meter',
        description="""
        displacement vectors between grid points along each axis; same indexing rules as
        lattice_vectors.  In many cases, displacements and number of points are related to
        lattice_vectors through: [displacement] * [number of points + N] =
        [lattice_vector],where N is 1 for periodic directions and 0 for non-periodic ones
        """,
    )

    origin = Quantity(
        type=np.dtype(np.float64),
        shape=[3],
        unit='meter',
        description="""
        location of the first grid point; same coordinate system as atom_positions when
        applicable.
        """,
    )

    value = Quantity(
        type=np.dtype(np.float64),
        shape=['multiplicity', 'n_x', 'n_y', 'n_z'],
        description="""
        Values of the volumetric data defined by kind.
        """,
    )


class PotentialValue(Volumetric):
    """
    Section containing the values of the potential evaluated on a uniform real-space grid.
    """

    m_def = Section(validate=False)

    value = Quantity(
        type=np.dtype(np.float64),
        shape=['multiplicity', 'n_x', 'n_y', 'n_z'],
        unit='J / m ** 3',
        description="""
        Values of the potential evaluated at each grid point.
        """,
    )


class Potential(Volumetric):
    """
    Section containing all potential types.
    """

    m_def = Section(validate=False)

    effective = SubSection(sub_section=PotentialValue.m_def, repeats=True)

    hartree = SubSection(sub_section=PotentialValue.m_def, repeats=True)


class Density(Volumetric):
    """
    Section containing the values of the density evaluated on a uniform real-space grid.
    """

    m_def = Section(validate=False)

    value = Quantity(
        type=np.dtype(np.float64),
        shape=['multiplicity', 'n_x', 'n_y', 'n_z'],
        unit='1 / m ** 3',
        description="""
        Values of the potential evaluated at each grid point.
        """,
    )

    # TODO rename this to value or restructure metainfo def for densities and perhaps
    # rename density_charge to charge_density if no other densities are to be added.
    value_hdf5 = Quantity(
        type=str,
        shape=[],
        description="""
        Specifies the HDF5 file and the path to the value in the file .
        """,
    )


class Spectra(ArchiveSection):
    """
    Section containing the spectra properties.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=str,
        description="""
        A string identifier for the type of spectrum: XAS, RIXS, XES, ARPES, etc.
        """,
    )

    n_energies = Quantity(
        type=int,
        shape=[],
        description="""
        Number of excited states.
        """,
    )

    excitation_energies = Quantity(
        type=np.float64,
        shape=['n_energies'],
        unit='joule',
        description="""
        Excitation energies.
        """,
        categories=[EnergyValue],
    )

    energy_zero_ref = Quantity(
        type=np.float64,
        unit='joule',
        shape=[],
        description="""
        Reference energy to set the origin of the spectra to 0 eV.
        """,
    )

    intensities = Quantity(
        type=np.float64,
        shape=['n_energies'],
        description="""
        Excitation intensities in arbitrary units.
        """,
    )

    intensities_units = Quantity(
        type=str,
        description="""
        Units in which the intensities of the spectra are returned by a calculation. The
        typical units for the dielectric constant are `F/m`.
        """,
    )

    oscillator_strengths = Quantity(
        type=np.float64,
        shape=['n_energies'],
        description="""
        Excited states oscillator strengths.
        """,
    )

    transition_dipole_moments = Quantity(
        type=np.float64,
        shape=['n_energies', 3],
        unit='coulomb * meter',
        description="""
        Transition dipole moments.
        """,
    )

    provenance = SubSection(
        sub_section=ElectronicStructureProvenance.m_def, repeats=True
    )


class GreensFunctions(MSection):
    """
    Green's functions properties in different time/frequency basis.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=MEnum('impurity', 'lattice'),
        description="""
        Type of Green's function calculated from the mapping of the Hubbard-Kanamori model
        into the Anderson impurity model. These calculations are converged if both types of
        Green's functions converge to each other (G_impurity == G_lattice).
        """,
    )

    matsubara_freq = Quantity(
        type=np.float64,
        shape=['*'],
        description="""
        Matsubara frequencies (imaginary frequencies). Can be either positives or both positives
        and negatives.
        """,
    )

    tau = Quantity(
        type=np.float64,
        shape=['n_tau'],
        description="""
        Imaginary times.
        """,
    )

    frequencies = Quantity(
        type=np.float64,
        shape=['n_frequencies'],
        description="""
        Real space frequencies.
        """,
    )

    chemical_potential = Quantity(
        type=np.float64,
        unit='joule',
        description="""
        Chemical potential.
        """,
    )

    self_energy_iw = Quantity(
        type=np.complex128,
        shape=['n_atoms_per_unit_cell', 2, 'n_correlated_orbitals', '*'],
        description="""
        Self-energy tensor in Matsubara frequencies.
        """,
    )

    greens_function_iw = Quantity(
        type=np.complex128,
        shape=['n_atoms_per_unit_cell', 2, 'n_correlated_orbitals', '*'],
        description="""
        Green's function tensor in Matsubara frequencies.
        """,
    )

    hybridization_function_iw = Quantity(
        type=np.complex128,
        shape=['n_atoms_per_unit_cell', 2, 'n_correlated_orbitals', '*'],
        description="""
        Hybridization function tensor in Matsubara frequencies.
        """,
    )

    greens_function_tau = Quantity(
        type=np.complex128,
        shape=['n_atoms_per_unit_cell', 2, 'n_correlated_orbitals', 'n_tau'],
        description="""
        Green's function tensor in tau (imaginary time).
        """,
    )

    self_energy_freq = Quantity(
        type=np.complex128,
        shape=['n_atoms_per_unit_cell', 2, 'n_correlated_orbitals', 'n_frequencies'],
        description="""
        Self-energy tensor in real frequencies.
        """,
    )

    greens_function_freq = Quantity(
        type=np.complex128,
        shape=['n_atoms_per_unit_cell', 2, 'n_correlated_orbitals', 'n_frequencies'],
        description="""
        Green's function tensor in real frequencies.
        """,
    )

    hybridization_function_freq = Quantity(
        type=np.complex128,
        shape=['n_atoms_per_unit_cell', 2, 'n_correlated_orbitals', 'n_frequencies'],
        description="""
        Hybridization function tensor in real frequencies.
        """,
    )

    orbital_occupations = Quantity(
        type=np.float64,
        shape=['n_atoms_per_unit_cell', 2, 'n_correlated_orbitals'],
        description="""
        Orbital occupation per correlated atom in the unit cell and per spin.
        """,
    )

    quasiparticle_weights = Quantity(
        type=np.float64,
        shape=['n_atoms_per_unit_cell', 2, 'n_correlated_orbitals'],
        description="""
        Quasiparticle weights of each orbital per site and spin. Calculated from:
            Z = inv(1.0 - d [Re Sigma] / dw at w=0)
        it takes values ∈ [0.0, 1.0], being Z=1 non-correlated, and Z=0 in a Mott state.
        """,
    )


class VibrationalFrequenciesValues(MSection):
    """
    Section describing a vibrational spectrum.
    """

    m_def = Section(validate=False)

    kind = Quantity(
        type=str,
        shape=[],
        description="""
        Kind of the vibration.
        """,
    )

    description = Quantity(
        type=str,
        shape=[],
        description="""
        Short description of the vibration.
        """,
    )

    n_vibrations = Quantity(
        type=int,
        shape=[],
        description="""
        Number of values in the vibration spectrum.
        """,
    )

    activity = Quantity(
        type=str,
        shape=['n_vibrations'],
        description="""
        Describes the activity corresponding to each of the value of the vibration
        spectrum.
        """,
    )

    intensity = Quantity(
        type=np.dtype(np.float64),
        shape=['n_vibrations'],
        description="""
        Intensity of the vibration.
        """,
    )


class VibrationalFrequencies(MSection):
    """
    Section containing results related to vibrational frequencies.
    """

    m_def = Section(validate=False)

    n_frequencies = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of vibration frequencies
        """,
    )

    value = Quantity(
        type=np.dtype(np.float64),
        shape=['n_frequencies'],
        unit='1 / meter',
        description="""
        Values of vibrational frequencies (m-1)
        """,
    )

    # TODO add normal modes

    raman = SubSection(sub_section=VibrationalFrequenciesValues.m_def, repeats=False)

    infrared = SubSection(sub_section=VibrationalFrequenciesValues.m_def, repeats=False)


class RadiusOfGyrationValues(AtomicGroupValues):
    """
    Section containing information regarding the values of
    radius of gyration (Rg).
    """

    m_def = Section(validate=False)

    value = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='m',
        description="""
        Value of Rg.
        """,
    )


class RadiusOfGyration(AtomicGroup):
    """
    Section containing information about the calculation of
    radius of gyration (Rg).
    """

    m_def = Section(validate=False)

    radius_of_gyration_values = SubSection(
        sub_section=RadiusOfGyrationValues.m_def, repeats=True
    )


class BaseCalculation(ArchiveSection):
    """
    Contains computed properties of a configuration as defined by the corresponding
    section system and with the simulation method defined by section method. The
    references to the system and method sections are given by system_ref and method_ref,
    respectively.

    Properties derived from a group of configurations are not included in this section but
    can be accessed in section workflow.
    """

    m_def = Section(validate=False)

    system_ref = Quantity(
        type=Reference(System.m_def),
        shape=[],
        description="""
        Links the calculation to a section system.
        """,
        categories=[FastAccess],
    )

    method_ref = Quantity(
        type=Reference(Method.m_def),
        shape=[],
        description="""
        Links the calculation to a section method.
        """,
        categories=[FastAccess],
    )

    starting_calculation_ref = Quantity(
        type=Reference(SectionProxy('Calculation')),
        shape=[],
        description="""
        Links the current section calculation to the starting calculation.
        """,
        categories=[FastAccess],
    )

    n_references = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
         Number of references to the current section calculation.
        """,
    )

    calculations_ref = Quantity(
        type=Reference(SectionProxy('Calculation')),
        shape=['n_references'],
        description="""
        Links the current section calculation to other section calculations. Such a link
        is necessary for example if the referenced calculation is a self-consistent
        calculation that serves as a starting point or a calculation is part of a domain
        decomposed simulation that needs to be connected.
        """,
        categories=[FastAccess],
    )

    calculations_path = Quantity(
        type=str,
        shape=['n_references'],
        description="""
        Links the current section calculation to other section calculations. Such a link
        is necessary for example if the referenced calculation is a self-consistent
        calculation that serves as a starting point or a calculation is part of a domain
        decomposed simulation that needs to be connected.
        """,
    )

    calculation_converged = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates whether a the calculation is converged.
        """,
    )

    hessian_matrix = Quantity(
        type=np.dtype(np.float64),
        shape=['number_of_atoms', 'number_of_atoms', 3, 3],
        description="""
        The matrix with the second derivative of the energy with respect to atom
        displacements.
        """,
    )

    spin_S2 = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Stores the value of the total spin moment operator $S^2$ for the converged
        wavefunctions calculated with the XC_method. It can be used to calculate the spin
        contamination in spin-unrestricted calculations.
        """,
    )

    time_calculation = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='second',
        description="""
        Stores the wall-clock time needed to complete the calculation i.e. the real time
        that has elapsed from start to end of calculation.
        """,
        categories=[TimeInfo, AccessoryInfo],
    )

    time_physical = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='second',
        description="""
        The elapsed real time at the end of the calculation with respect to the start of
        the simulation.
        """,
    )

    energy = SubSection(sub_section=Energy.m_def, categories=[FastAccess])

    forces = SubSection(sub_section=Forces.m_def)

    stress = SubSection(sub_section=Stress.m_def)

    band_gap = SubSection(sub_section=BandGap.m_def, repeats=True)

    dos_electronic = SubSection(sub_section=Dos.m_def, repeats=True)

    dos_phonon = SubSection(sub_section=Dos.m_def, repeats=True)

    eigenvalues = SubSection(sub_section=BandEnergies.m_def, repeats=True)

    band_structure_electronic = SubSection(
        sub_section=BandStructure.m_def, repeats=True
    )

    band_structure_phonon = SubSection(sub_section=BandStructure.m_def, repeats=True)

    thermodynamics = SubSection(sub_section=Thermodynamics.m_def, repeats=True)

    hopping_matrix = SubSection(sub_section=HoppingMatrix.m_def, repeats=True)

    spectra = SubSection(sub_section=Spectra.m_def, repeats=True)

    greens_functions = SubSection(sub_section=GreensFunctions.m_def, repeats=True)

    vibrational_frequencies = SubSection(
        sub_section=VibrationalFrequencies.m_def, repeats=True
    )

    potential = SubSection(sub_section=Potential.m_def, repeats=True)

    multipoles = SubSection(sub_section=Multipoles.m_def, repeats=True)

    charges = SubSection(sub_section=Charges.m_def, repeats=True)

    density_charge = SubSection(sub_section=Density.m_def, repeats=True)

    radius_of_gyration = SubSection(sub_section=RadiusOfGyration.m_def, repeats=True)

    volume = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='m ** 3',
        description="""
        Value of the volume of the system.
        """,
    )

    density = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='kg / m ** 3',
        description="""
        Value of the density of the system.
        """,
    )

    pressure = Quantity(
        type=np.float64,
        shape=[],
        unit='pascal',
        description="""
        Value of the pressure of the system.
        """,
    )

    pressure_tensor = Quantity(
        type=np.float64,
        shape=[3, 3],
        unit='pascal',
        description="""
        Value of the pressure in terms of the x, y, z components of the simulation cell.
        Typically calculated as the difference between the kinetic energy and the virial.
        """,
    )

    virial_tensor = Quantity(
        type=np.dtype(np.float64),
        shape=[3, 3],
        unit='joule',
        description="""
        Value of the virial in terms of the x, y, z components of the simulation cell.
        Typically calculated as the cross product between positions and forces.
        """,
    )

    enthalpy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Value of the calculated enthalpy per cell i.e. energy_total + pressure * volume.
        """,
    )

    temperature = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='kelvin',
        description="""
        Value of the temperature of the system.
        """,
    )

    step = Quantity(
        type=int,
        shape=[],
        description="""
        The number of time steps with respect to the start of the simulation.
        """,
    )

    time = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='second',
        description="""
        The elapsed simulated physical time since the start of the simulation.
        """,
    )


class ScfIteration(BaseCalculation):
    """
    Every scf_iteration section represents a self-consistent field (SCF) iteration,
    and gives detailed information on the SCF procedure of the specified quantities.
    """

    m_def = Section(validate=False)


class Calculation(BaseCalculation):
    """
    Every calculation section contains the values computed
    during a *single configuration calculation*, i.e. a calculation performed on a given
    configuration of the system (as defined in section_system) and a given computational
    method (e.g., exchange-correlation method, basis sets, as defined in section_method).

    The link between the current section calculation and the related
    system and method sections is established by the values stored in system_ref and
    method_ref, respectively.

    The reason why information on the system configuration and computational method is
    stored separately is that several *single configuration calculations* can be performed
    on the same system configuration, viz. several system configurations can be evaluated
    with the same computational method. This storage strategy avoids redundancies.
    """

    m_def = Section(validate=False)

    n_scf_iterations = Quantity(
        type=int,
        shape=[],
        description="""
        Gives the number of performed self-consistent field (SCF) iterations.
        """,
        categories=[ScfInfo],
    )

    scf_iteration = SubSection(sub_section=ScfIteration.m_def, repeats=True)


m_package.__init_metainfo__()
