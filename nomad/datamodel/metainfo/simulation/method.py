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

import typing
from logging import Logger

import numpy as np  # noqa: F401
from pint.util import SharedRegistryObject  # noqa: F401

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
)
from nomad.quantum_states import RussellSaundersState

from ..common import FastAccess

m_package = Package()


unavailable = 'unavailable'
not_processed = 'not processed'
orbitals = {
    -1: dict(zip(range(4), ('s', 'p', 'd', 'f'))),
    0: {0: ''},
    1: dict(zip(range(-1, 2), ('x', 'z', 'y'))),
    2: dict(zip(range(-2, 3), ('xy', 'xz', 'z^2', 'yz', 'x^2-y^2'))),
    3: dict(
        zip(
            range(-3, 4),
            ('x(x^2-3y^2)', 'xyz', 'xz^2', 'z^3', 'yz^2', 'z(x^2-y^2)', 'y(3x^2-y^2)'),
        )
    ),
}
invert_dict = lambda x: dict(zip(x.values(), x.keys()))


def is_none_or_empty(x):
    if x is None:
        return True
    if isinstance(x, np.ndarray):
        return x.size == 0
    return not x


class Mesh(MSection):
    """
    Contains the settings for a sampling mesh.
    Supports uniformly-spaced meshes and symmetry-reduced representations.
    """

    m_def = Section(validate=False)

    dimensionality = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Dimensionality of the mesh.
        """,
    )

    sampling_method = Quantity(
        type=MEnum(
            'Gamma-centered',
            'Monkhorst-Pack',
            'Gamma-offcenter',
            'Line-path',
            'Equidistant',
            'Logarithmic',
            'Tan',
            'Gauss-Legendre',
            'Gauss-LaguerreClenshaw-Curtis',
            'Newton-Cotes',
            'Gauss-Hermite',
        ),
        shape=[],
        description="""
        Method used to generate the mesh:

        | Name      | Description                      | Reference             |

        | --------- | -------------------------------- | --------------------- |

        | `'Gamma-centered'` | Regular mesh is centered around Gamma. No offset. |

        | `'Monkhorst-Pack'` | Regular mesh with an offset of half the reciprocal lattice vector. |

        | `'Gamma-offcenter'` | Regular mesh with an offset that is neither `'Gamma-centered'`, nor `'Monkhorst-Pack'`. |

        | `'Line-path'` | Line path along high-symmetry points. Typically employed for simulating band structures. |

        | `'Equidistant'`  | Equidistant 1D grid (also known as 'Newton-Cotes')                      |

        | `'Logarithmic'`  | log distance 1D grid               |

        | `'Tan'`  | Non-uniform tan mesh for 1D grids. More dense at low abs values of the points, while less dense for higher values |

        | `'Gauss-Legendre'` | Quadrature rule for integration using Legendre polynomials |

        | `'Gauss-Laguerre'` | Quadrature rule for integration using Laguerre polynomials |

        | `'Clenshaw-Curtis'`  | Quadrature rule for integration using Chebyshev polynomials using discrete cosine transformations |

        | `'Gauss-Hermite'`  | Quadrature rule for integration using Hermite polynomials |
        """,
    )

    n_points = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Total number of points in the mesh, accounting for the multiplicities.
        """,
    )

    grid = Quantity(
        type=np.int32,
        shape=['dimensionality'],
        description="""
        Amount of mesh point sampling along each axis, i.e. [nx, ny, nz].
        """,
    )

    points = Quantity(
        type=np.complex128,
        shape=['*', 'dimensionality'],
        description="""
        List of all the points in the mesh.
        """,
    )

    multiplicities = Quantity(
        type=np.float64,
        shape=['*'],
        description="""
        The amount of times the same point reappears. These are accounted for in `n_points`.
        A value larger than 1, typically indicates a symmetry operation that was applied to the mesh.
        """,
    )

    weights = Quantity(
        type=np.float64,
        shape=['*'],
        description="""
        The frequency of times the same point reappears.
        A value larger than 1, typically indicates a symmetry operation that was applied to the mesh.
        """,
    )


class LinePathSegment(MSection):
    """
    Contains the settings for a single line path segment in a mesh.
    """

    m_def = Section(validate=False)

    start_point = Quantity(
        type=str,
        shape=[],
        description="""
        Name of the high-symmetry starting point of the line path segment.
        """,
    )

    end_point = Quantity(
        type=str,
        shape=[],
        description="""
        Name of the high-symmetry end point of the line path segment.
        """,
    )

    n_points = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of points in the line path segment.
        """,
    )

    points = Quantity(
        type=np.float64,
        shape=['*', 3],
        description="""
        List of all the points in the line path segment.
        """,
    )


class KMesh(Mesh):
    """
    Contains the settings for a sampling mesh in 3D reciprocal space.
    Supports uniformly-spaced meshes, line paths along high-symmetry points,
    as well as symmetry-reduced and full representations.
    """

    m_def = Section(validate=False)

    offset = Quantity(
        type=np.float64,
        shape=[3],
        description="""
        Offset vector shifting the mesh with respect to a Gamma-centered case.
        """,
    )

    all_points = Quantity(
        type=np.float64,
        shape=['*', 3],
        description="""
        Full list of the mesh points without any symmetry operations.
        """,
    )

    high_symmetry_points = Quantity(
        type=str,
        shape=['*'],
        description="""
        Named high symmetry points in the mesh.
        """,
    )

    line_path_segments = SubSection(sub_section=LinePathSegment.m_def, repeats=True)


class FrequencyMesh(Mesh):
    """
    Contains the settings for a sampling mesh in 1D frequency space, either real or imaginary.
    """

    m_def = Section(validate=False)

    points = Quantity(
        type=np.complex128,
        shape=['n_points', 'dimensionality'],
        unit='joule',
        description="""
        List of all the points in the mesh in joules.
        """,
    )

    smearing = Quantity(
        type=np.float64,
        shape=[],
        description="""
        Numerical smearing parameter used for convolutions.
        """,
    )


class TimeMesh(Mesh):
    """
    Contains the settings for a sampling mesh in 1D time space, either real or imaginary.
    """

    m_def = Section(validate=False)

    smearing = Quantity(
        type=np.float64,
        shape=[],
        description="""
        Numerical smearing parameter used for convolutions.
        """,
    )


class Scf(MSection):
    """
    Section containing the parameters related to self consistency.
    """

    m_def = Section(validate=False)

    native_tier = Quantity(
        type=str,
        shape=[],
        description="""
        The code-specific tag indicating the precision used
        for the self-consistent cycle.

        Supported codes (with hyperlinks to the relevant documentation):
        - `Orca`
        """,
    )

    n_max_iteration = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Specifies the maximum number of allowed self-consistent field (SCF) iterations in
        a calculation.
        """,
    )

    threshold_energy_change = Quantity(
        type=np.float64,
        shape=[],
        unit='joule',
        description="""
        Specifies the threshold for the total energy change between two subsequent
        self-consistent field (SCF) iterations. The SCF is considered converged when the
        total-energy change between two SCF cycles is below the threshold (possibly in
        combination with other criteria).
        """,
    )

    threshold_density_change = Quantity(
        type=np.float64,
        shape=[],
        description="""
        Specifies the threshold for the average charge density change between two
        subsequent self-consistent field (SCF) iterations. The SCF is considered converged
        when the density change between two SCF cycles is below the threshold (possibly in
        combination with other criteria).
        """,
    )  # TODO: unit

    minimization_algorithm = Quantity(
        type=str,
        shape=[],
        description="""
        Specifies the algorithm used for self consistency minimization.
        """,
    )


class HubbardKanamoriModel(MSection):
    """
    Setup of the local Hubbard model.
    """

    m_def = Section(validate=False)

    orbital = Quantity(
        type=str,
        shape=[],
        description="""
        Orbital label corresponding to the Hubbard model. The typical orbitals with strong
        Hubbard interactions have partially filled '3d', '4d' and '4f' orbitals.
        """,
    )

    n_orbital = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of non-degenerated orbitals of the same type (s, p, d, f, ...).
        """,
    )

    u = Quantity(
        type=np.float64,
        shape=[],
        unit='joule',
        description="""
        Value of the (intraorbital) Hubbard interaction
        """,
    )

    jh = Quantity(
        type=np.float64,
        shape=[],
        unit='joule',
        description="""
        Value of the (interorbital) Hund's coupling.
        """,
    )

    up = Quantity(
        type=np.float64,
        shape=[],
        unit='joule',
        description="""
        Value of the (interorbital) Coulomb interaction. In rotational invariant
        systems, up = u - 2 * jh.
        """,
    )

    j = Quantity(
        type=np.float64,
        shape=[],
        unit='joule',
        description="""
        Value of the exchange interaction. In rotational invariant systems, j = jh.
        """,
    )

    u_effective = Quantity(
        type=np.float64,
        shape=[],
        unit='joule',
        description="""
        Value of the effective U parameter (u - j).
        """,
    )

    slater_integrals = Quantity(
        type=np.float64,
        shape=[3],
        unit='joule',
        description="""
        Value of the Slater integrals (F0, F2, F4) in spherical harmonics used to derive
        the local Hubbard interactions:

            u = ((2.0 / 7.0) ** 2) * (F0 + 5.0 * F2 + 9.0 * F4) / (4.0*np.pi)

            up = ((2.0 / 7.0) ** 2) * (F0 - 5.0 * F2 + 3.0 * 0.5 * F4) / (4.0*np.pi)

            jh = ((2.0 / 7.0) ** 2) * (5.0 * F2 + 15.0 * 0.25 * F4) / (4.0*np.pi)

        Ref.: Elbio Dagotto, Nanoscale Phase Separation and Colossal Magnetoresistance,
        Chapter 4, Springer Berlin (2003).
        """,
    )

    umn = Quantity(
        type=np.float64,
        shape=['n_orbital', 'n_orbital'],
        unit='joule',
        description="""
        Value of the local Coulomb interaction matrix.
        """,
    )

    double_counting_correction = Quantity(
        type=str,
        shape=[],
        description="""
        Name of the double counting correction algorithm applied.
        """,
    )


class Pseudopotential(MSection):
    """"""

    m_def = Section(validate=False)

    name = Quantity(
        type=str,
        shape=[],
        description="""
        Native code name of the pseudopotential.
        """,
    )

    type = Quantity(
        type=MEnum('US V', 'US MBK', 'PAW'),
        shape=[],
        description="""
        Pseudopotential classification.
        | abbreviation | description | DOI |
        | ------------ | ----------- | --------- |
        | `'US'`       | Ultra-soft  | |
        | `'PAW'`      | Projector augmented wave | |
        | `'V'`        | Vanderbilt | https://doi.org/10.1103/PhysRevB.47.6728 |
        | `'MBK'`      | Morrison-Bylander-Kleinman | https://doi.org/10.1103/PhysRevB.41.7892 |
        """,
    )

    norm_conserving = Quantity(
        type=bool,
        shape=[],
        description="""
        Denotes whether the pseudopotential is norm-conserving.
        """,
    )

    cutoff = Quantity(
        type=np.float64,
        shape=[],
        unit='joule',
        description="""
        Minimum recommended spherical cutoff energy for any plane-wave basis set
        using the pseudopotential.
        """,
    )

    xc_functional_name = Quantity(
        type=str,
        shape=['*'],
        description="""
        Name of the exchange-correlation functional used to generate the pseudopotential.
        Follows the libxc naming convention.
        """,
    )

    l_max = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Maximum angular momentum of the pseudopotential projectors.
        """,
    )

    lm_max = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Maximum magnetic momentum of the pseudopotential projectors.
        """,
    )


class SingleElectronState(MSection):  # inherit from AtomicOrbitalState?
    """
    An `AtomicOrbitalState` which supports fast notation for single-electron states.
    """

    quantities: list[str] = [
        'n_quantum_number',
        'l_quantum_number',
        'ml_quantum_number',
        'ms_quantum_bool',
        'j_quantum_number',
        'mj_quantum_number',
        'occupation',
        'degeneracy',
    ]
    s_quantum_number = 0.5
    # TODO: Introduce nominal_occupation with a compatibility alias in a separate fix.
    nominal_occuption = 1.0  # typos: ignore

    l_quantum_symbols = orbitals[-1]
    ml_quantum_symbols = {i: orbitals[i] for i in range(4)}
    l_quantum_numbers = invert_dict(l_quantum_symbols)
    ml_quantum_numbers = {k: invert_dict(v) for k, v in ml_quantum_symbols.items()}

    ms_quantum_map = (True, False)
    ms_quantum_symbols = dict(zip(ms_quantum_map, ('up', 'down')))
    ms_quantum_values = dict(zip(ms_quantum_map, (s_quantum_number, -s_quantum_number)))

    def __setattr__(self, name, value):
        if name == 'l_quantum_number':
            try:
                value = abs(value)
            except TypeError:
                return
            if self.n_quantum_number is not None:
                if value > self.n_quantum_number - 1:
                    raise ValueError(f'Invalid value for {name}: {value}')
        elif name == 'j_quantum_number':
            try:
                value = [abs(x) for x in value]
            except TypeError:
                return
        elif name == 'ml_quantum_number':
            if isinstance(value, int):
                if value not in self.ml_quantum_symbols[self.l_quantum_number]:
                    raise ValueError(f'Invalid value for {name}: {value}')
        elif name == 's_quantum_number':
            raise AttributeError(
                'Cannot alter the spin quantum number $s$ of a single-electron state.'
            )
        super().__setattr__(name, value)

    def normalize(self, archive, logger: Logger | None):
        # self.set_degeneracy()
        pass

    def set_j_quantum_number(self) -> list[float]:
        """Given $l$ and $m_s$, set the total angular momentum $j$.
        Return $j$, fi already set."""
        if is_none_or_empty(self.j_quantum_number):
            if (jj := self.l_quantum_number) is None:
                self.j_quantum_number = []
            else:
                jjs = [jj + s for s in self.ms_quantum_values.values()]
                self.j_quantum_number = RussellSaundersState.generate_Js(
                    jjs[0], jjs[1], rising=True
                )
        return self.j_quantum_number

    def set_mj_quantum_number(self) -> list[float]:
        """Given $m_l$ and $m_s$, set $m_j$. Return $m_j$, if already set."""
        if is_none_or_empty(self.mj_quantum_number):
            mj = self.ml_quantum_number
            if mj is None:
                self.mj_quantum_number = []
            else:
                if self.ms_quantum_bool is None:
                    self.mj_quantum_number = mj + np.array(
                        tuple(self.ms_quantum_values.values())
                    )
                else:
                    self.mj_quantum_number = (
                        mj + self.ms_quantum_values[self.ms_quantum_bool]
                    )
        return self.mj_quantum_number

    def get_l_degeneracy(self) -> int:
        """Return the multiplicity of the orbital angular momentum $l$."""
        try:
            return 2 * (
                2 * self.l_quantum_number + 1
            )  # TODO: replace with RussellSaundersState.multiplicity
        except TypeError:
            raise ValueError('Cannot get $l$ degeneracy without $l$.')

    def set_degeneracy(self) -> int:
        """Set the degeneracy based on how specifically determined the quantum state is.
        This function can be triggered anytime to update the degeneracy."""
        # TODO: there are certain j (mj) specifications that may straddle just one l value
        if self.ml_quantum_number is not None:
            if self.ms_quantum_bool is not None:
                self.degeneracy = 1
            else:
                self.degeneracy = 2
        elif self.j_quantum_number is not None:
            degeneracy = 0
            for jj in self.j_quantum_number:
                if self.mj_quantum_number is not None:
                    mjs = RussellSaundersState.generate_MJs(
                        self.j_quantum_number[0], rising=True
                    )
                    degeneracy += len(
                        [mj for mj in mjs if mj in self.mj_quantum_number]
                    )
                else:
                    degeneracy += RussellSaundersState(jj, 1).degeneracy
            if self.ms_quantum_bool is not None:
                self.degeneracy = degeneracy / 2
            else:
                self.degeneracy = degeneracy
        elif self.l_quantum_number is not None:
            if self.ms_quantum_bool is not None:
                self.degeneracy = self.get_l_degeneracy() / 2
            else:
                self.degeneracy = self.get_l_degeneracy()
        return self.degeneracy

    n_quantum_number = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Principal quantum number $n$.
        """,
    )
    l_quantum_number = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Orbital angular quantum number $l$.
        """,
    )
    ml_quantum_number = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Azimuthal projection of the $l$ vector.
        """,
    )
    j_quantum_number = Quantity(
        type=np.float64,
        shape=['1..2'],
        description="""
        Total angular momentum quantum number $j = |l-s| ... l+s$.
        **Necessary with strong L-S coupling or non-collinear spin systems.**
        """,
    )
    mj_quantum_number = Quantity(
        type=np.float64,
        shape=['*'],
        description="""
        Azimuthal projection of the $j$ vector.
        **Necessary with strong L-S coupling or non-collinear spin systems.**
        """,
    )
    ms_quantum_bool = Quantity(
        type=bool,
        shape=[],
        description="""
        Boolean representation of the spin state $m_s$.
        `False` for spin down, `True` for spin up.
        In non-collinear spin systems, the projection axis $z$ should also be defined.
        """,
    )
    # TODO: add the relativistic kappa_quantum_number
    degeneracy = Quantity(
        type=np.int32,
        description="""
        The number of states under the filling constraints applied to the orbital set.
        This implicitly assumes that all orbitals in the set are degenerate.
        """,
    )


class CoreHole(SingleElectronState):
    """
    Describes the quantum state of a single hole in an open-shell core state. This is the physical interpretation.
    For modelling purposes, the electron charge excited may lie between 0 and 1. This follows a so-called Janak state.
    Sometimes, no electron is actually, excited, but just marked for excitation. This is denoted as an `initial` state.
    Any missing quantum numbers indicate some level of arbitrariness in the choice of the core hole, represented in the degeneracy.
    """

    quantities: list[str] = SingleElectronState.quantities + ['n_electrons_excited']

    def __setattr__(self, name, value):
        if name == 'n_electrons_excited':
            if value < 0.0:
                raise ValueError('Number of excited electrons must be positive.')
            if value > 1.0:
                raise ValueError('Number of excited electrons must be less than 1.')
        elif name == 'dscf_state':
            if value == 'initial':
                self.n_electrons_excited = 0.0
                self.degeneracy = 1
        super().__setattr__(name, value)

    def normalize(self, archive, logger: Logger | None):
        super().normalize(archive, logger)
        self.set_occupation()

    def set_occupation(self) -> float:
        """Set the occupation based on the number of excited electrons."""
        if not self.occupation:
            try:
                self.occupation = self.set_degeneracy() - self.n_electrons_excited
            except TypeError:
                raise AttributeError(
                    'Cannot set occupation without `n_electrons_excited`.'
                )
        return self.occupation

    n_electrons_excited = Quantity(
        type=np.float64,
        shape=[],
        description="""
        The electron charge excited for modelling purposes.
        Choices that deviate from 0 or 1 typically leverage Janak composition.
        Unless the `initial` state is chosen, the model corresponds to a single electron being excited in physical reality.
        """,
    )
    occupation = Quantity(
        type=np.float64,
        description="""
        The total number of electrons within the state (as defined by degeneracy)
        after exciting the model charge.
        """,
    )
    dscf_state = Quantity(
        type=MEnum('initial', 'final'),
        shape=[],
        description="""
        The $\\Delta$-SCF state tag, used to identify the role in the workflow of the same name.
        Allowed values are `initial` (not to be confused with the _initial-state approximation_) and `final`.
        """,
    )


class AtomParameters(MSection):
    """
    Contains method-related information about a kind of atom identified by label. This
    allows the assignment of an atom-centered basis set or pseudopotential for different
    atoms belonging to the same kind.

    Through this section we use the wording "active" mainly for defining orbital-related
    quantities. Active refers to the relevant orbital parameters in the atom.
    """

    m_def = Section(validate=False)

    atom_number = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Atomic number (number of protons) of this atom kind, use 0 if not an atom.
        """,
    )

    atom_index = Quantity(
        type=np.int32,
        shape=['*'],
        description="""
        The atom index with respect to the parsed system atoms section.
        """,
    )

    n_valence_electrons = Quantity(
        type=np.float64,
        shape=[],
        description="""
        Number of valence electrons.
        """,
    )

    n_core_electrons = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of core electrons.
        """,
    )

    label = Quantity(
        type=str,
        shape=[],
        description="""
        String used to identify the atoms of this kind. This should correspond to the
        atom labels of the configuration. It is possible for one atom kind to have
        multiple labels (in order to allow two atoms of the same kind to have two
        differently defined sets of atom-centered basis functions or two different pseudo-
        potentials). Atom kind is typically the symbol of the atomic species but it can be
        also a ghost or pseudo-atom.
        """,
    )

    mass = Quantity(
        type=np.float64,
        shape=[],
        unit='kg',
        description="""
        Mass of the atom.
        """,
    )

    pseudopotential_name = Quantity(  # TODO: deprecate
        type=str,
        shape=[],
        description="""
        Name identifying the pseudopotential used.
        """,
    )

    pseudopotential = SubSection(sub_section=Pseudopotential.m_def)

    core_hole = SubSection(sub_section=CoreHole.m_def)

    n_orbitals = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of active orbitals of the atom.
        """,
    )

    orbitals = Quantity(
        type=str,
        shape=['n_orbitals'],
        description="""
        Label of the active orbitals of the atoms.
        """,
    )

    onsite_energies = Quantity(
        type=np.float64,
        shape=['n_orbitals'],
        unit='joule',
        description="""
        Values of the atomic onsite energy corresponding to each orbital.
        """,
    )

    charge = Quantity(
        type=np.float64,
        shape=[],
        unit='coulomb',
        description="""
        Total charge of the atom.
        """,
    )

    charges = Quantity(
        type=np.float64,
        shape=['n_orbitals'],
        unit='coulomb',
        description="""
        Values of the charge corresponding to each orbital.
        """,
    )

    hubbard_kanamori_model = SubSection(sub_section=HubbardKanamoriModel.m_def)


class MoleculeParameters(MSection):
    """
    Contains method-related information about a kind of atom identified by label. This
    allows the assignment of an atom-centered basis set or pseudopotential for different
    atoms belonging to the same kind.
    """

    m_def = Section(validate=False)

    label = Quantity(
        type=str,
        shape=[],
        description="""
        String to identify the molecule.
        """,
    )

    n_atoms = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of atoms in the molecule.
        """,
    )

    atom_parameters = SubSection(sub_section=AtomParameters.m_def, repeats=True)


class Photon(MSection):
    """
    Section containing the details of the photon field used for spectrum calculations.
    """

    m_def = Section(validate=False)

    multipole_type = Quantity(
        type=str,
        description="""
        Type used for the multipolar expansion: dipole, quadrupole, NRIXS, Raman, etc.
        """,
    )

    polarization = Quantity(
        type=np.float64,
        shape=[3],
        description="""
        Direction of the photon polarization in cartesian coordinates.
        """,
    )

    energy = Quantity(
        type=np.float64,
        unit='joule',
        description="""
        Photon energy.
        """,
    )

    momentum_transfer = Quantity(
        type=np.float64,
        shape=[3],
        description="""
        Momentum transfer which would be important for quadrupole or NRIXS or Raman.
        """,
    )


class GaussianBasisGroup(MSection):
    """
    Section that describes a group of Gaussian contractions. Groups allow one to calculate
    the primitive Gaussian integrals once for several different linear combinations of
    them. This defines basis functions with radial part $f_i(r) = r^{l_i} \\sum_{j} c_{i j}
    A(l_i, \\alpha_j) exp(-\\alpha_j r^2)$ where $A(l_i, \\alpha_j)$ is a the normalization
    coefficient for primitive Gaussian basis functions. Here, $\\alpha_j$ is defined in
    gaussian_basis_group_exponents, $l_i$ is given in gaussian_basis_group_ls, and $c_{i
    j}$ is given in gaussian_basis_group_contractions, whereas the radial part is given by
    the spherical harmonics $Y_{l m}$.

    This section is defined only if the original basis function uses Gaussian basis
    functions, and the sequence of radial functions $f_i$ across all
    section_gaussian_basis_group in section_basis_set_atom_centered should match the one
    of basis_set_atom_centered_radial_functions.
    """

    m_def = Section(validate=False)

    n_contractions = Quantity(
        type=int,
        shape=[],
        description="""
        Gives the number of different contractions, i.e. resulting basis functions in a
        gaussian_basis_group section.
        """,
    )

    n_exponents = Quantity(
        type=int,
        shape=[],
        description="""
        Gives the number of different Gaussian exponents in a section_gaussian_basis_group
        section.
        """,
    )

    contractions = Quantity(
        type=np.float64,
        shape=['n_contractions', 'n_exponents'],
        description="""
        contraction coefficients $c_{i j}$ defining the contracted basis functions with
        respect to *normalized* primitive Gaussian functions. They define the Gaussian
        basis functions as described in section_gaussian_basis_group.
        """,
    )

    exponents = Quantity(
        type=np.float64,
        shape=['n_exponents'],
        unit='1 / meter ** 2',
        description="""
        Exponents $\\alpha_j$ of the Gaussian functions defining this basis set
        $exp(-\\alpha_j r^2)$. One should be careful about the units of the coefficients.
        """,
    )

    ls = Quantity(
        type=np.float64,
        shape=['n_contractions'],
        description="""
        Azimuthal quantum number ($l$) values (of the angular part given by the spherical
        harmonic $Y_{l m}$ of the various contracted basis functions).
        """,
    )


class BasisSetAtomCentered(MSection):
    """
    This section describes the atom-centered basis set. The main contained information is
    a short, non unique but human-interpretable, name for identifying the basis set
    (short_name), a longer unique name, the atomic number of the atomic species the
    basis set is meant for.
    """

    m_def = Section(validate=False)

    name = Quantity(
        type=str,
        shape=[],
        description="""
        Code-specific, but explicative, base name for the basis set.
        """,
    )

    formula = Quantity(
        type=str,
        shape=[],
        description="""
        Generalized representation of the basis set, e.g. 'STO-3G', '6-31G(d)', 'cc-pVDZ',
        etc.
        """,
    )

    atom_number = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Atomic number (i.e., number of protons) of the atom for which this basis set is
        constructed (0 means unspecified or a pseudo atom).
        """,
    )

    n_basis_functions = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Gives the number of different basis functions in a basis_set_atom_centered
        section. This equals the number of actual coefficients that are specified when
        using this basis set.
        """,
    )

    gaussian_basis_group = SubSection(
        sub_section=GaussianBasisGroup.m_def, repeats=True
    )


class OrbitalAPW(MSection):
    """Definition of a APW wavefunction per orbital."""

    m_def = Section(validate=False)

    type = Quantity(
        type=MEnum('APW', 'LAPW', 'LO', 'spherical Dirac'),
        shape=[],
        description="""
        State
        """,
    )

    n_quantum_number = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Main quantum number $n$ specifying the orbital.
        """,
    )

    l_quantum_number = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Angular momentum / azimuthal quantum number $l$ specifying the orbital.
        """,
    )

    j_quantum_number = Quantity(
        type=np.float64,
        shape=[],
        description="""
        Total angular momentum quantum number $j$ specifying the orbital,
        where $j$ ranges from $l-s$ to $l+s$.
        """,
    )

    kappa_quantum_number = Quantity(
        type=np.float64,
        shape=[],
        description="""
        Relativistic angular momentum quantum number specifying the orbital
        $\\kappa = (l-j)(2j+1)$.
        """,
    )

    occupation = Quantity(
        type=np.float64,
        shape=[],
        description="""
        Number of electrons populating the orbital.
        """,
    )

    core_level = Quantity(
        type=bool,
        shape=[],
        description="""
        Boolean denoting whether the orbital is treated differently from valence orbitals.
        """,
    )

    energy_parameter = Quantity(
        type=np.float64,
        shape=['*'],
        unit='joule',
        description="""
        Reference energy parameter for the augmented plane wave (APW) basis set.
        Is used to set the energy parameter for each state.
        """,
    )

    energy_parameter_n = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Reference number of radial nodes for the augmented plane wave (APW) basis set.
        This is used to derive the `energy_parameter`.
        """,
    )

    order = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Derivative order of the radial wavefunction term.
        """,
    )

    boundary_condition_order = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Differential order to which the radial wavefunction is matched at the boundary.
        """,
    )

    update = Quantity(
        type=bool,
        shape=[],
        description="""
        Allow the code to optimize the initial energy parameter.
        """,
    )

    updated = Quantity(
        type=bool,
        shape=[],
        description="""
        Initial energy parameter after code optimization.
        """,
    )


class BasisSetMesh(MSection):
    """All geometry-related information of the basis set (mesh)."""

    m_def = Section(validate=False)

    shape = Quantity(
        type=MEnum('cubic', 'rectangular', 'spherical', 'ellipsoidal', 'cylindrical'),
        shape=[],
        description="""
        Geometry of the basis set mesh.
        """,
    )

    box_lengths = Quantity(
        type=np.float64,
        shape=[3],
        unit='meter',
        description="""
        Dimensions of the box containing the basis set mesh.
        """,
    )

    radius = Quantity(
        type=np.float64,
        shape=[],
        unit='meter',
        description="""
        Radius of the sphere.
        """,
    )

    grid_spacing = Quantity(
        type=np.float64,
        shape=['*'],
        unit='meter',
        description="""
        Grid spacing of a Cartesian mesh.
        """,
    )

    radius_lin_spacing = Quantity(
        type=np.float64,
        shape=[],
        unit='meter',
        description="""
        The equidistant spacing of the radial grid.
        """,
    )

    radius_log_spacing = Quantity(
        type=np.float64,
        shape=[],
        description="""
        The logarithmic spacing of the radial grid.
        """,
    )

    n_grid_points = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Total number of grid points.
        """,
    )

    n_radial_grid_points = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of grid points on the radial grid.
        """,
    )

    n_spherical_grid_points = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of grid points on the spherical grid.
        """,
    )


class BasisSet(BasisSetMesh):
    """
    This section contains all basis sets used to represent the wavefunction or electron
    density.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=MEnum(
            'numeric AOs',
            'gaussians',
            'plane waves',
            'psinc functions',
            'real-space grid',
            'pbeVaspFit2015',
            'Koga',
            'Bunge',
        ),
        shape=[],
        description="""
        The type of basis set used by the program.

        | Value                          |                                            Description |
        | ------------------------------ | ------------------------------------------------------ |
        | `'numeric AOs'`                | Numerical atomic orbitals                              |
        | `'gaussians'`                  | Gaussian basis set                                     |
        | `'plane waves'`                | Plane waves                                            |
        | `'psinc functions'`            | Pseudopotential sinc functions                         |
        | `'real-space grid'`            | Real-space grid                                        |
        | `'pbeVaspFit2015'`             | Lobster algorithm for projection plane waves onto LCAO |
        | `'Koga'`                       | Lobster algorithm for projection plane waves onto LCAO |
        | `'Bunge'`                      | Lobster algorithm for projection plane waves onto LCAO |
        """,
    )

    scope = Quantity(
        type=str,
        shape=['*'],
        description="""
        The extent of the electronic structure that the basis set encodes.
        The partitions could be energetic (e.g. `core`, `valence`) in nature,
        spatial (e.g. `muffin-tin`, `interstitial`), or cover
        Hamiltonian components (e.g. `kinetic energy`,
        `electron-electron interaction`), etc.
        """,
    )

    cutoff = Quantity(
        type=np.float64,
        shape=[],
        unit='joule',
        description="""
        Spherical cutoff in reciprocal space for a plane-wave basis set. It is the energy
        of the highest plane-wave ($\\frac{\\hbar^2|k+G|^2}{2m_e}$) included in the basis
        set.
        """,
    )

    cutoff_fractional = Quantity(
        type=np.float64,
        shape=[],
        description="""
        The spherical cutoff parameter for the interstitial plane waves in the LAPW family.
        This cutoff is unitless, referring to the product of the smallest muffin-tin radius
        and the length of the cutoff reciprocal vector ($r_{MT} * |K_{cut}|$).
        """,
    )

    frozen_core = Quantity(
        type=bool,
        shape=[],
        description="""
        Boolean denoting whether the frozen-core approximation was applied.
        """,
    )

    atom_centered = SubSection(sub_section=BasisSetAtomCentered.m_def, repeats=True)

    spherical_harmonics_cutoff = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Maximum angular momentum $l$ for the spherical harmonics.
        """,
    )

    atom_parameters = Quantity(
        type=Reference(SectionProxy('AtomParameters')),
        shape=[],
        description="""
        Reference to a particular atom parameter setup further specifying the basis set.
        """,
    )

    orbital = SubSection(sub_section=OrbitalAPW, repeats=True)


class BasisSetContainer(MSection):
    """Container class for `BasisSet`"""

    m_def = Section(validate=False)

    native_tier = Quantity(
        type=str,
        shape=[],
        description="""
        The code-specific tag indicating the precision used
        for the basis set and meshes of numerical routines.

        Supported codes (with hyperlinks to the relevant documentation):
        - [`VASP`](https://www.vasp.at/wiki/index.php/PREC)
        - `FHI-aims`
        - [`CASTEP`](http://www.tcm.phy.cam.ac.uk/castep/documentation/WebHelp/CASTEP.html#modules/castep/tskcastepsetelecquality.htm?Highlight=ultra-fine)
        """,
    )

    basis_sets = [
        'atom-centered orbitals',
        'APW',
        'LAPW',
        'APW+lo',
        'LAPW+lo',
        '(L)APW',
        '(L)APW+lo',
        'plane waves',
        'gaussians + plane waves',
        'real-space grid',
        'support functions',
        unavailable,
        not_processed,
    ]

    type = Quantity(
        type=MEnum(basis_sets),
        default=unavailable,
        description="""
        The type of basis set used by the program.

        | Value                          |                       Description |
        | ------------------------------ | --------------------------------- |
        | `'APW'`                        | Augmented plane waves             |
        | `'LAPW'`                       | Linearized augmented plane waves  |
        | `'APW+lo'`             | Augmented plane waves with local orbitals |
        | `'LAPW+lo'` | Linearized augmented plane waves with local orbitals |
        | `'(L)APW'`                     |     A combination of APW and LAPW |
        | `'(L)APW+lo'`  | A combination of APW and LAPW with local orbitals |
        | `'plane waves'`                | Plane waves                       |
        | `'gaussians + plane waves'`    | Basis set of the Quickstep algorithm (DOI: 10.1016/j.cpc.2004.12.014) |
        | `'real-space grid'`            | Real-space grid                   |
        | `'support functions'`         | Support functions                 |
        """,
    )

    scope = BasisSet.scope.m_copy()  # Change name to usage?

    basis_set = SubSection(sub_section=BasisSet.m_def, repeats=True)


class Interaction(MSection):
    """
    Section containing the parameters of a contribution to a force field model.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=str,
        shape=[],
        description="""
        Denotes the classification of the potential.
        """,
    )

    name = Quantity(
        type=str,
        shape=[],
        description="""
        Specifies the name of the potential. Can contain information on the species,
        cut-offs, potential versions.
        """,
    )

    n_interactions = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Total number of interactions of this type for interaction groupings.
        """,
    )

    n_atoms = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of atoms included in (each instance of) the interaction.
        """,
    )

    atom_labels = Quantity(
        type=np.dtype(str),
        shape=['n_interactions', 'n_atoms'],
        description="""
        Labels of the atoms described by the interaction. Can be a list of lists for interaction groupings.
        """,
    )

    atom_indices = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Indices of the atoms in the system described by the interaction. Can be a list of lists for interaction groupings.
        """,
    )

    functional_form = Quantity(
        type=str,
        shape=[],
        description="""
        Specifies the functional form of the interaction potential.
        """,
    )

    n_parameters = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Specifies the number of parameters in the interaction potential.
        """,
    )

    parameters = Quantity(
        type=typing.Any,
        shape=[],
        description="""
        Dictionary of label and parameters of the interaction potential.
        """,
    )

    contributions = SubSection(sub_section=SectionProxy('Interaction'), repeats=True)


class Model(MSection):
    """
    Section containing the parameters of a force field model. If specified, the parameters
    corresponding to the individual contributions to the model are given in contributions.
    Otherwise, the parameters can also be found in a reference to the published model.
    """

    m_def = Section(validate=False)

    name = Quantity(
        type=str,
        shape=[],
        description="""
        Identifies the name of the model.
        """,
    )

    reference = Quantity(
        type=str,
        shape=[],
        description="""
        Reference to the model e.g. DOI, URL.
        """,
    )

    contributions = SubSection(sub_section=Interaction.m_def, repeats=True)


class Functional(MSection):
    """
    Section containing the parameters of an exchange or correlation functional.
    """

    m_def = Section(validate=False)

    name = Quantity(
        type=str,
        shape=[],
        description="""
        Provides the name of one of the exchange and/or correlation (XC) functional
        following the libbx convention.
        """,
    )

    parameters = Quantity(
        type=typing.Any,
        shape=[],
        description="""
        Contains an associative list of non-default values of the parameters for the
        functional.

        For example, if a calculations using a hybrid XC functional (e.g., HSE06)
        specifies a user-given value of the mixing parameter between exact and GGA
        exchange, then this non-default value is stored in this metadata.

        The labels and units of these values may be defined in name.

        If this metadata is not given, the default parameter values for the functional
        are assumed.
        """,
    )

    weight = Quantity(
        type=np.float64,
        shape=[],
        description="""
        Provides the value of the weight for the functional.

        This weight is used in the linear combination of the different functionals. If not
        specified then the default is set to 1.
        """,
    )


class XCFunctional(Model):
    """
    Section describing the exchange-correlation functional used in the DFT calculation.
    The name of the exchange-correlation functional is given by name and the reference to
    the published functional is provided by reference. Other contributions to the
    functional not covered by exchange, correlation or hybrid types may be specified in
    contributions.
    """

    m_def = Section(validate=False)

    exchange = SubSection(sub_section=Functional.m_def, repeats=True)

    correlation = SubSection(sub_section=Functional.m_def, repeats=True)

    hybrid = SubSection(sub_section=Functional.m_def, repeats=True)

    contributions = SubSection(sub_section=Functional.m_def, repeats=True)

    def normalize_hybrid(self):
        for hyb in self.hybrid:
            if 'exact_exchange_mixing_factor' in hyb.parameters.keys() and not hyb.name:
                hyb.name += '+alpha'


class DFT(MSection):
    """
    Section containing the various parameters that define a DFT calculation. These include
    settings for the exchange correlation functionals, LDA+U, etc.
    """

    m_def = Section(validate=False)

    self_interaction_correction_method = Quantity(
        type=str,
        shape=[],
        description="""
        Contains the name for the self-interaction correction (SIC) treatment used to
        calculate the final energy and related quantities. If skipped or empty, no special
        correction is applied.

        The following SIC methods are available:

        | SIC method                | Description                       |

        | ------------------------- | --------------------------------  |

        | `""`                      | No correction                     |

        | `"SIC_AD"`                | The average density correction    |

        | `"SIC_SOSEX"`             | Second order screened exchange    |

        | `"SIC_EXPLICIT_ORBITALS"` | (scaled) Perdew-Zunger correction explicitly on a
        set of orbitals |

        | `"SIC_MAURI_SPZ"`         | (scaled) Perdew-Zunger expression on the spin
        density / doublet unpaired orbital |

        | `"SIC_MAURI_US"`          | A (scaled) correction proposed by Mauri and co-
        workers on the spin density / doublet unpaired orbital |
        """,
    )

    xc_functional = SubSection(sub_section=XCFunctional.m_def)


class TightBindingOrbital(MSection):
    """
    Section to define an orbital including the name of orbital and shell number and the on-site energy.
    """

    m_def = Section(validate=False)

    orbital_name = Quantity(
        type=str,
        description="""
        The name of the orbital.
        """,
    )

    cell_index = Quantity(
        type=np.int32,
        shape=[3],
        description="""
            The index of the cell in 3 dimensional.
            """,
    )

    atom_index = Quantity(
        type=np.int32,
        description="""
        The index of the atom.
        """,
    )

    shell = Quantity(
        type=np.int32,
        description="""
        The shell number.
        """,
    )

    onsite_energy = Quantity(
        type=np.float64,
        description="""
        On-site energy of the orbital.
        """,
    )


class TwoCenterBond(MSection):
    """
    Section to define a two-center approximation bond between two atoms.
    """

    m_def = Section(validate=False)

    bond_label = Quantity(
        type=str,
        shape=[],
        description="""
        Name of the Slater-Koster bond to identify the bond.
        """,
    )

    center1 = SubSection(
        sub_section=TightBindingOrbital.m_def,
        repeats=False,
        description="""
        Name of the Slater-Koster bond to identify the bond.
        """,
    )

    center2 = SubSection(
        sub_section=TightBindingOrbital.m_def,
        repeats=False,
        description="""
            Name of the Slater-Koster bond to identify the bond.
            """,
    )


class SlaterKosterBond(TwoCenterBond):
    """
    Section to define a two-center approximation bond between two atoms
    """

    m_def = Section(validate=False)

    sss = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type sigma between two s orbitals.
        """,
    )

    sps = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type sigma between s and p orbitals.
        """,
    )

    sds = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type sigma between s and d orbitals.
        """,
    )

    sfs = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type sigma between s and f orbitals.
        """,
    )

    pps = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type sigma between two p orbitals.
        """,
    )

    ppp = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type pi between two p orbitals.
        """,
    )

    pds = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type sigma between p and d orbitals.
        """,
    )

    pdp = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type pi between p and d orbitals.
        """,
    )

    pfs = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type sigma between p and f orbitals.
        """,
    )

    pfp = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type pi between p and f orbitals.
        """,
    )

    dds = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type sigma between two d orbitals.
        """,
    )

    ddp = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type pi between two d orbitals.
        """,
    )

    ddd = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type delta between two d orbitals.
        """,
    )

    dfs = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type sigma between d and f orbitals.
        """,
    )

    dfp = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type pi between d and f orbitals.
        """,
    )

    dfd = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type delta between d and f orbitals.
        """,
    )

    ffs = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type sigma between two f orbitals.
        """,
    )

    ffp = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type pi between two f orbitals.
        """,
    )

    ffd = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type delta between two f orbitals.
        """,
    )

    fff = Quantity(
        type=np.float64,
        description="""
        The Slater Koster integral of type phi between two f orbitals.
        """,
    )


class SlaterKoster(MSection):
    """
    Section containing the various parameters that define a Slater-Koster
    """

    m_def = Section(validate=False)

    orbitals = SubSection(sub_section=TightBindingOrbital.m_def, repeats=True)
    bonds = SubSection(sub_section=SlaterKosterBond.m_def, repeats=True)
    overlaps = SubSection(sub_section=SlaterKosterBond.m_def, repeats=True)


class xTB(Model):
    """
    Section containing the parameters pertaining to an extended tight-binding (xTB) calculation.
    """

    m_def = Section(validate=False)

    hamiltonian = SubSection(sub_section=Interaction.m_def, repeats=True)

    overlap = SubSection(sub_section=Interaction.m_def, repeats=True)

    repulsion = SubSection(sub_section=Interaction.m_def, repeats=True)

    magnetic = SubSection(sub_section=Interaction.m_def, repeats=True)

    coulomb = SubSection(sub_section=Interaction.m_def, repeats=True)


class Wannier(MSection):
    """
    Section containing the various parameters that define a Wannier tight-binding method.
    """

    m_def = Section(validate=False)

    n_projected_orbitals = Quantity(
        type=np.int32,
        description="""
        Number of Wannier orbitals used to fit the DFT band structure
        """,
    )

    n_bands = Quantity(
        type=np.int32,
        description="""
        Number of input Bloch bands to calculate the projection matrix.
        """,
    )

    is_maximally_localized = Quantity(
        type=bool,
        description="""
        Are the projected orbitals maximally localized or just a single-shot projection?
        """,
    )

    convergence_tolerance_max_localization = Quantity(
        type=np.float64,
        description="""
        Convergence tolerance for maximal localization of the projected orbitals.
        """,
    )

    energy_window_outer = Quantity(
        type=np.float64,
        unit='electron_volt',
        shape=[2],
        description="""
        Bottom and top of the outer energy window used for the projection.
        """,
    )

    energy_window_inner = Quantity(
        type=np.float64,
        unit='electron_volt',
        shape=[2],
        description="""
        Bottom and top of the inner energy window used for the projection.
        """,
    )


class TB(Model):
    """
    Section containing the parameters pertaining to a tight-binding calculation. The TB
    model can be derived from the Slater-Koster integrals, the xTB perturbation theory, or
    the Wannier projection.
    """

    m_def = Section(validate=False)

    slater_koster = SubSection(sub_section=SlaterKoster.m_def, repeats=False)
    xtb = SubSection(sub_section=xTB.m_def, repeats=False)
    wannier = SubSection(sub_section=Wannier.m_def, repeats=False)


class HoppingMatrix(MSection):
    """
    Section containing the hopping/overlap matrix elements between N projected orbitals. This
    is also the output of a TB calculation.
    """

    m_def = Section(validate=False)

    n_orbitals = Quantity(
        type=np.int32,
        description="""
        Number of projected orbitals.
        """,
    )

    n_wigner_seitz_points = Quantity(
        type=np.int32,
        description="""
        Number of Wigner-Seitz real points.
        """,
    )

    degeneracy_factors = Quantity(
        type=np.int32,
        shape=['n_wigner_seitz_points'],
        description="""
        Degeneracy of each Wigner-Seitz grid point.
        """,
    )

    value = Quantity(
        type=np.float64,
        shape=['n_wigner_seitz_points', 'n_orbitals * n_orbitals', 7],
        description="""
        Real space hopping matrix for each Wigner-Seitz grid point. The elements are
        defined as follows:

            n_x   n_y   n_z   orb_1   orb_2   real_part + j * imag_part

        where (n_x, n_y, n_z) define the Wigner-Seitz cell vector in fractional coordinates,
        (orb_1, orb_2) indicates the hopping amplitude between orb_1 and orb_2, and the
        real and imaginary parts of the hopping in electron_volt.
        """,
    )


class LatticeModelHamiltonian(MSection):
    """
    Section containing the parameters of the non-interacting parts of a lattice model Hamiltonian.
    """

    m_def = Section(validate=False)

    # I defined t_parameters apart from HoppingMatrix to simplify the parsing (writing
    # everything in the HoppingMatrix basis might be tedious).
    # TODO generalize parsers to write HoppingMatrix as default even for simple models.
    lattice_name = Quantity(
        type=str,
        shape=[],
        description="""
        Name of the lattice to identify the model. E.g., 'Square', 'Honeycomb'.
        """,
    )

    n_neighbors = Quantity(
        type=np.int32,
        description="""
        Number of direct neighbors considered for the hopping integrals.
        """,
    )

    t_parameters = Quantity(
        type=np.complex128,
        shape=['n_neighbors'],
        description="""
        Hopping parameters for simple models, with [t, t`, t``, etc].
        """,
    )

    hopping_matrix = SubSection(sub_section=HoppingMatrix.m_def, repeats=False)

    hubbard_kanamori_model = SubSection(
        sub_section=HubbardKanamoriModel.m_def, repeats=True
    )


class CoreHoleSpectra(MSection):
    """
    Section containing the various parameters that define a calculation of core-hole spectra.
    It can be within BSE as a "core" subsection.
    """

    m_def = Section(validate=False)

    solver = Quantity(
        type=str,
        description="""
        Solver algorithm used for the core-hole spectra.
        """,
    )

    edge = Quantity(
        type=MEnum(
            'K',
            'L1',
            'L2',
            'L3',
            'L23',
            'M1',
            'M2',
            'M3',
            'M23',
            'M4',
            'M5',
            'M45',
            'N1',
            'N2',
            'N3',
            'N23',
            'N4',
            'N5',
            'N45',
        ),
        description="""
        Edge to be calculated for the core-hole spectra.
        """,
    )

    mode = Quantity(
        type=MEnum('absorption', 'emission'),
        description="""
        Type of spectra to be calculated: absorption or emission.
        """,
    )

    broadening = Quantity(
        type=np.float64,
        unit='joule',
        description="""
        Core-hole lifetime broadening applied to the edge spectra in full-width at half maximum.
        """,
    )


class ExcitedStateMethodology(MSection):
    """
    Base class containing the common numerical parameters typical of excited-state
    calculations.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=str,
        shape=[],
        description="""
        Type which allows to identify the excited-state calculation with a
        common string.
        """,
    )

    n_states = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of states used to calculate the excitations.
        """,
    )

    n_empty_states = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of empty states used to calculate the excitations. This quantity is
        complementary to `n_states`.
        """,
    )

    broadening = Quantity(
        type=np.float64,
        unit='joule',
        description="""
        Lifetime broadening applied to the spectra in full-width at half maximum.
        """,
    )

    # Used to define the meshes of sub_sections that are not base classes yet, e.g., screening
    k_mesh = SubSection(sub_section=KMesh.m_def)

    q_mesh = SubSection(sub_section=KMesh.m_def)

    frequency_mesh = SubSection(sub_section=FrequencyMesh.m_def)


class Screening(ExcitedStateMethodology):
    """
    Section containing the various parameters that define a screening calculation, as for
    example, in RPA.
    """

    m_def = Section(validate=False)

    dielectric_infinity = Quantity(
        type=np.int32,
        description="""
        Value of the static dielectric constant at infinite q. For metals, this is infinite
        (or a very large value), while for insulators is finite.
        """,
    )


class GW(ExcitedStateMethodology):
    """
    Section containing the various parameters that define a GW calculation.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=MEnum(
            'G0W0',
            'scGW',
            'scGW0',
            'scG0W',
            'ev-scGW0',
            'ev-scGW',
            'qp-scGW0',
            'qp-scGW',
        ),
        shape=[],
        description="""
        GW Hedin's self-consistency cycle:

        | Name      | Description                      | Reference             |

        | --------- | -------------------------------- | --------------------- |

        | `'G0W0'`  | single-shot                      | PRB 74, 035101 (2006) |

        | `'scGW'`  | self-consistent G and W               | PRB 75, 235102 (2007) |

        | `'scGW0'` | self-consistent G with fixed W0  | PRB 54, 8411 (1996)   |

        | `'scG0W'` | self-consistent W with fixed G0  | -                     |

        | `'ev-scGW0'`  | eigenvalues self-consistent G with fixed W0   | PRB 34, 5390 (1986)   |

        | `'ev-scGW'`  | eigenvalues self-consistent G and W   | PRB 74, 045102 (2006)   |

        | `'qp-scGW0'`  | quasiparticle self-consistent G with fixed W0 | PRL 99, 115109 (2007) |

        | `'qp-scGW'`  | quasiparticle self-consistent G and W | PRL 96, 226402 (2006) |
        """,
    )

    analytical_continuation = Quantity(
        type=MEnum(
            'pade',
            'contour_deformation',
            'ppm_GodbyNeeds',
            'ppm_HybertsenLouie',
            'ppm_vonderLindenHorsh',
            'ppm_FaridEngel',
            'multi_pole',
        ),
        shape=[],
        description="""
        Analytical continuation approximations of the GW self-energy:

        | Name           | Description         | Reference                        |

        | -------------- | ------------------- | -------------------------------- |

        | `'pade'` | Pade's approximant  | J. Low Temp. Phys 29, 179 (1977) |

        | `'contour_deformation'` | Contour deformation | PRB 67, 155208 (2003) |

        | `'ppm_GodbyNeeds'` | Godby-Needs plasmon-pole model | PRL 62, 1169 (1989) |

        | `'ppm_HybertsenLouie'` | Hybertsen and Louie plasmon-pole model | PRB 34, 5390 (1986) |

        | `'ppm_vonderLindenHorsh'` | von der Linden and P. Horsh plasmon-pole model | PRB 37, 8351 (1988) |

        | `'ppm_FaridEngel'` | Farid and Engel plasmon-pole model  | PRB 47, 15931 (1993) |

        | `'multi_pole'` | Multi-pole fitting  | PRL 74, 1827 (1995) |
        """,
    )

    interval_qp_corrections = Quantity(
        type=np.int32,
        shape=[2],
        description="""
        Band indices (in an interval) for which the GW quasiparticle corrections are
        calculated.
        """,
    )

    screening = SubSection(sub_section=Screening.m_def)


class BSE(ExcitedStateMethodology):
    """
    Section containing the various parameters that define a BSE calculation.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=MEnum('Singlet', 'Triplet', 'IP', 'RPA'),
        shape=[],
        description="""
        Type of BSE hamiltonian solved:

            H_BSE = H_diagonal + 2 * gx * Hx - gc * Hc

        where gx, gc specifies the type.

        Online resources for the theory:
        - http://exciting.wikidot.com/carbon-excited-states-from-bse#toc1
        - https://www.vasp.at/wiki/index.php/Bethe-Salpeter-equations_calculations
        - https://docs.abinit.org/theory/bse/
        - https://www.yambo-code.eu/wiki/index.php/Bethe-Salpeter_kernel

        | Name | Description |

        | --------- | ----------------------- |

        | `'Singlet'` | gx = 1, gc = 1 |

        | `'Triplet'` | gx = 0, gc = 1 |

        | `'IP'` | Independent-particle approach |

        | `'RPA'` | Random Phase Approximation |
        """,
    )

    solver = Quantity(
        type=MEnum('Full-diagonalization', 'Lanczos-Haydock', 'GMRES', 'SLEPc', 'TDA'),
        shape=[],
        description="""
        Solver algorithm used to diagonalize the BSE Hamiltonian.

        | Name | Description | Reference |

        | --------- | ----------------------- | ----------- |

        | `'Full-diagonalization'` | Full diagonalization of the BSE Hamiltonian | - |

        | `'Lanczos-Haydock'` | Subspace iterative Lanczos-Haydock algorithm | https://doi.org/10.1103/PhysRevB.59.5441 |

        | `'GMRES'` | Generalized minimal residual method | https://doi.org/10.1137/0907058 |

        | `'SLEPc'` | Scalable Library for Eigenvalue Problem Computations | https://slepc.upv.es/ |

        | `'TDA'` | Tamm-Dancoff approximation | https://doi.org/10.1016/S0009-2614(99)01149-5 |
        """,
    )

    screening = SubSection(sub_section=Screening.m_def)

    core_hole = SubSection(sub_section=CoreHoleSpectra.m_def)


class DMFT(MSection):
    """
    Section containing the various parameters that define a DMFT calculation
    """

    m_def = Section(validate=False)

    n_impurities = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of impurities mapped from the correlated atoms in the unit cell.
        """,
    )

    n_correlated_orbitals = Quantity(
        type=np.int32,
        shape=['n_impurities'],
        description="""
        Number of correlated orbitals per impurity.
        """,
    )

    n_electrons = Quantity(
        type=np.float64,
        shape=['n_impurities'],
        description="""
        Initial number of valence electrons per impurity.
        """,
    )  # Question: I guess the shape makes it incompatible with Electronic.n_electrons? We should apply polymorphism here with base sections.

    inverse_temperature = Quantity(
        type=np.float64,
        unit='1/joule',
        shape=[],
        description="""
        Inverse temperature = 1/(kB*T).
        """,
    )

    magnetic_state = Quantity(
        type=MEnum('paramagnetic', 'ferromagnetic', 'antiferromagnetic'),
        shape=[],
        description="""
        Magnetic state in which the DMFT calculation is done:

        | Name                  | State                   |

        | --------------------- | ----------------------- |

        | `'paramagnetic'`      | paramagnetic state      |

        | `'ferromagnetic'`     | ferromagnetic state     |

        | `'antiferromagnetic'` | antiferromagnetic state |
        """,
    )

    impurity_solver = Quantity(
        type=MEnum(
            'CT-INT',
            'CT-HYB',
            'CT-AUX',
            'ED',
            'NRG',
            'MPS',
            'IPT',
            'NCA',
            'OCA',
            'slave_bosons',
            'hubbard_I',
        ),
        shape=[],
        description="""
        Impurity solver method used in the DMFT loop:

        | Name              | Reference                            |

        | ----------------- | ------------------------------------ |

        | `'CT-INT'`        | Rubtsov et al., JEPT Lett 80 (2004)  |

        | `'CT-HYB'`        | Werner et al., PRL 97 (2006)         |

        | `'CT-AUX'`        | Gull et al., EPL 82 (2008)           |

        | `'ED'`            | Caffarrel et al, PRL 72 (1994)       |

        | `'NRG'`           | Bulla et al., RMP 80 (2008)          |

        | `'MPS'`           | Ganahl et al., PRB 90 (2014)         |

        | `'IPT'`           | Georges et al., PRB 45 (1992)        |

        | `'NCA'`           | Pruschke et al., PRB 47 (1993)       |

        | `'OCA'`           | Pruschke et al., PRB 47 (1993)       |

        | `'slave_bosons'`  | Kotliar et al., PRL 57 (1986)        |

        | `'hubbard_I'`     | -                                    |
        """,
    )


class NeighborSearching(MSection):
    """
    Section containing the parameters for neighbor searching/lists during a molecular dynamics run.
    """

    m_def = Section(validate=False)

    neighbor_update_frequency = Quantity(
        type=int,
        shape=[],
        description="""
        Number of timesteps between updating the neighbor list.
        """,
    )

    neighbor_update_cutoff = Quantity(
        type=np.float64,
        shape=[],
        unit='m',
        description="""
        The distance cutoff for determining the neighbor list.
        """,
    )


class ForceCalculations(MSection):
    """
    Section containing the parameters for force calculations according to the referenced force field
    during a molecular dynamics run.
    """

    m_def = Section(validate=False)

    vdw_cutoff = Quantity(
        type=np.float64,
        shape=[],
        unit='m',
        description="""
        Cutoff for calculating VDW forces.
        """,
    )

    coulomb_type = Quantity(
        type=MEnum(
            'cutoff',
            'ewald',
            'multilevel_summation',
            'particle_mesh_ewald',
            'particle_particle_particle_mesh',
            'reaction_field',
        ),
        shape=[],
        description="""
        Method used for calculating long-ranged Coulomb forces.

        Allowed values are:

        | Barostat Name          | Description                               |

        | ---------------------- | ----------------------------------------- |

        | `""`                   | No thermostat               |

        | `"Cutoff"`          | Simple cutoff scheme. |

        | `"Ewald"` | Standard Ewald summation as described in any solid-state physics text. |

        | `"Multi-Level Summation"` |  D. Hardy, J.E. Stone, and K. Schulten,
        [Parallel. Comput. **35**, 164](https://doi.org/10.1016/j.parco.2008.12.005)|

        | `"Particle-Mesh-Ewald"`        | T. Darden, D. York, and L. Pedersen,
        [J. Chem. Phys. **98**, 10089 (1993)](https://doi.org/10.1063/1.464397) |

        | `"Particle-Particle Particle-Mesh"` | See e.g. Hockney and Eastwood, Computer Simulation Using Particles,
        Adam Hilger, NY (1989). |

        | `"Reaction-Field"` | J.A. Barker and R.O. Watts,
        [Mol. Phys. **26**, 789 (1973)](https://doi.org/10.1080/00268977300102101)|
        """,
    )

    coulomb_cutoff = Quantity(
        type=np.float64,
        shape=[],
        unit='m',
        description="""
        Cutoff for calculating short-ranged Coulomb forces.
        """,
    )

    neighbor_searching = SubSection(sub_section=NeighborSearching.m_def, repeats=False)


class ForceField(MSection):
    """
    Section containing the parameters pertaining to a force field calculation.
    """

    m_def = Section(validate=False)

    model = SubSection(sub_section=Model.m_def, repeats=True)

    force_calculations = SubSection(sub_section=ForceCalculations.m_def, repeats=False)


class Smearing(MSection):
    """
    Section containing the parameters related to the smearing of the electronic density of
    states at the Fermi level.
    """

    m_def = Section(validate=False)

    kind = Quantity(
        type=str,
        shape=[],
        description="""
        Specifies the kind of smearing on the electron occupation used to calculate the
        free energy (see energy_free)

        Valid values are:

        | Smearing kind             | Description                       |

        | ------------------------- | --------------------------------- |

        | `"empty"`                 | No smearing is applied            |

        | `"gaussian"`              | Gaussian smearing                 |

        | `"fermi"`                 | Fermi smearing                    |

        | `"marzari-vanderbilt"`    | Marzari-Vanderbilt smearing       |

        | `"methfessel-paxton"`     | Methfessel-Paxton smearing        |

        | `"tetrahedra"`            | Interpolation of state energies and occupations
        (ignores smearing_width) |
        """,
    )

    width = Quantity(
        type=np.float64,
        shape=[],
        description="""
        Specifies the width of the smearing in energy for the electron occupation used to
        calculate the free energy (see energy_free).

        *NOTE:* Not all methods specified in smearing_kind uses this value.
        """,
    )


class Electronic(MSection):
    """
    Section containing the parameters related to the electronic structure.
    """

    m_def = Section(validate=False)

    spin_target = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Stores the target (user-imposed) value of the spin multiplicity $M=2S+1$, where
        $S$ is the total spin. It is an integer number. This value is not necessarily the
        value obtained at the end of the calculation. See spin_S2 for the converged value
        of the spin moment.
        """,
    )

    charge = Quantity(
        type=np.float64,
        shape=[],
        unit='coulomb',
        description="""
        Stores the total charge of the system.
        """,
    )

    n_bands = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Specifies the number of bands used in the calculation.
        """,
    )

    n_spin_channels = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Gives the number of spin channels.
        """,
    )

    n_electrons = Quantity(
        type=np.float64,
        shape=[],
        description="""
        Number of valence electrons in the system.
        """,
    )

    method = Quantity(
        type=str,
        shape=[],
        description="""
        Non-unique string identifying the used electronic structure method. It is not
        unique in the sense that two calculations with the same
        electronic structure method string may have not been performed with exactly the
        same method.
        """,
    )

    relativity_method = Quantity(
        type=MEnum(
            [
                'scalar_relativistic',
                'pseudo_scalar_relativistic',
                'scalar_relativistic_atomic_ZORA',
            ]
        ),
        shape=[],
        description="""
        Describes the relativistic treatment used for the calculation of the final energy
        and related quantities. If skipped or empty, no relativistic treatment is applied.
        """,
    )

    van_der_waals_method = Quantity(
        type=str,
        shape=[],
        description="""
        Describes the Van der Waals method. If skipped or an empty string is used, it
        means no Van der Waals correction is applied.

        Allowed values are:

        | Van der Waals method  | Description                               |

        | --------------------- | ----------------------------------------- |

        | `""`                  | No Van der Waals correction               |

        | `"TS"`                | A. Tkatchenko and M. Scheffler, [Phys. Rev. Lett.
        **102**, 073005 (2009)](http://dx.doi.org/10.1103/PhysRevLett.102.073005) |

        | `"OBS"`               | F. Ortmann, F. Bechstedt, and W. G. Schmidt, [Phys. Rev.
        B **73**, 205101 (2006)](http://dx.doi.org/10.1103/PhysRevB.73.205101) |

        | `"G06"`               | S. Grimme, [J. Comput. Chem. **27**, 1787
        (2006)](http://dx.doi.org/10.1002/jcc.20495) |

        | `"JCHS"`              | P. Jurečka, J. Černý, P. Hobza, and D. R. Salahub,
        [Journal of Computational Chemistry **28**, 555
        (2007)](http://dx.doi.org/10.1002/jcc.20570) |

        | `"MDB"`               | Many-body dispersion. A. Tkatchenko, R. A. Di Stasio Jr,
        R. Car, and M. Scheffler, [Physical Review Letters **108**, 236402
        (2012)](http://dx.doi.org/10.1103/PhysRevLett.108.236402) and A. Ambrosetti, A. M.
        Reilly, R. A. Di Stasio Jr, and A. Tkatchenko, [The Journal of Chemical Physics
        **140**, 18A508 (2014)](http://dx.doi.org/10.1063/1.4865104) |

        | `"XC"`                | The method to calculate the Van der Waals energy uses a
        non-local functional which is described in section_XC_functionals. |
        """,
    )

    smearing = SubSection(sub_section=Smearing.m_def)


class Method(ArchiveSection):
    """
    Section containing the various parameters that define the theory and the
    approximations (convergence, thresholds, etc.) behind the calculation.
    """

    m_def = Section(validate=False)

    stress_tensor_method = Quantity(
        type=str,
        shape=[],
        description="""
        Specifies the method used to calculate stress_tensor for, e.g., molecular dynamics
        and geometry optimization.

        The allowed values are:

        * numeric

        * analytic
        """,
    )

    starting_method_ref = Quantity(
        type=Reference(SectionProxy('Method')),
        shape=[],
        description="""
        Links the current section method to a section method containing the starting
        parameters.
        """,
        categories=[FastAccess],
    )

    core_method_ref = Quantity(
        type=Reference(SectionProxy('Method')),
        shape=[],
        description="""
        Links the current section method to a section method containing the core settings.
        """,
        categories=[FastAccess],
    )

    n_references = Quantity(
        type=np.int32,
        shape=[],
        description="""
        Number of references to the current method.
        """,
    )

    methods_ref = Quantity(
        type=Reference(SectionProxy('Method')),
        shape=['n_references'],
        description="""
        Links the section method to other method sections. For instance, one calculation
        is a perturbation performed using a self-consistent field (SCF) calculation as
        starting point, or a simulated system is partitioned in regions with different but
        connected Hamiltonians (e.g., QM/MM, or a region treated via Kohn-Sham DFT
        embedded into a region treated via orbital-free DFT).
        """,
        categories=[FastAccess],
    )

    dft = SubSection(sub_section=DFT.m_def)

    tb = SubSection(sub_section=TB.m_def)

    lattice_model_hamiltonian = SubSection(
        sub_section=LatticeModelHamiltonian.m_def, repeats=True
    )

    gw = SubSection(sub_section=GW.m_def)

    bse = SubSection(sub_section=BSE.m_def)

    dmft = SubSection(sub_section=DMFT.m_def)

    force_field = SubSection(sub_section=ForceField.m_def)

    core_hole = SubSection(sub_section=CoreHoleSpectra.m_def)

    k_mesh = SubSection(sub_section=KMesh.m_def)

    frequency_mesh = SubSection(sub_section=FrequencyMesh.m_def, repeats=True)

    time_mesh = SubSection(sub_section=TimeMesh.m_def, repeats=True)

    electronic = SubSection(sub_section=Electronic.m_def)

    scf = SubSection(sub_section=Scf.m_def)

    atom_parameters = SubSection(
        sub_section=AtomParameters.m_def, repeats=True, label_quantity='label'
    )

    molecule_parameters = SubSection(
        sub_section=MoleculeParameters.m_def, repeats=True, label_quantity='label'
    )

    electrons_representation = SubSection(
        sub_section=BasisSetContainer.m_def, repeats=True, label_quantity='type'
    )

    photon = SubSection(sub_section=Photon.m_def, repeats=True)


m_package.__init_metainfo__()
