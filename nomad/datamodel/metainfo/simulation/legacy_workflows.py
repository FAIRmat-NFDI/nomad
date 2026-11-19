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
# Only for purpose of compatibility. Use simulation workflow schema plugin.
# https://github.com/nomad-coe/nomad-schema-plugin-simulation-workflow.git

import numpy as np

from nomad.datamodel.metainfo.common import FastAccess
from nomad.datamodel.metainfo.simulation.calculation import (
    BandStructure,
    Calculation,
    Dos,
)
from nomad.datamodel.metainfo.simulation.run import Run
from nomad.datamodel.metainfo.simulation.system import Atoms, AtomsGroup, System
from nomad.metainfo import (
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

m_package = Package()


class Interface(MSection):
    """
    Section containing results of an interface (stacking fault, gamma surface, etc.)
    workflow.
    """

    m_def = Section(validate=False)

    energy_extrinsic_stacking_fault = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule / m **2',
        description="""
        Value of the relaxed extrinsic stacking fault energy per unit area.
        """,
    )

    energy_intrinsic_stacking_fault = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule / m **2',
        description="""
        Value of the relaxed intrinsic stacking fault energy per unit area.
        """,
    )

    dimensionality = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Dimensionality of the property, i.e. 1 for stacking fault energy and 2 for gamma
        surface.
        """,
    )

    shift_direction = Quantity(
        type=str,
        shape=['dimensionality'],
        description="""
        shift direction of the two crystal parts to calculate the fault energy.
        """,
    )

    n_displacements = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of displacements in the shift to calculate the fault energy.
        """,
    )

    displacement_fraction = Quantity(
        type=np.dtype(np.float64),
        shape=['dimensionality', 'n_displacements'],
        description="""
        Relative displacements of the two crystal parts along the direction indicated by
        shift_direction.
        """,
    )

    energy_fault_plane = Quantity(
        type=np.dtype(np.float64),
        shape=['n_displacements'],
        unit='joule / m ** 2',
        description="""
        Value of the relaxed excess energy per unit area for each displacement.
        """,
    )

    gamma_surface = Quantity(
        type=np.dtype(np.float64),
        shape=['n_displacements', 'n_displacements'],
        unit='joule / m ** 2',
        description="""
        Value of the gamma surface, i.e. the excess energy per unit area calculated for
        each displacement.
        """,
    )

    slip_fraction = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Relative displacement between two crystal parts where the energy is maximum.
        """,
    )

    energy_unstable_stacking_fault = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule / m **2',
        description="""
        Value of the relaxed unstable stacking fault energy per unit area.
        """,
    )

    energy_unstable_twinning_fault = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule / m **2',
        description="""
        Value of the relaxed unstable twinning energy per unit area.
        """,
    )


class Raman(MSection):
    """
    Section containing results of a Raman workflow.
    """

    m_def = Section(validate=False)

    n_modes = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of evaluated vibrational modes.
        """,
    )

    n_atoms = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of atoms in the simulation cell.
        """,
    )

    frequencies = Quantity(
        type=np.dtype(np.float64),
        shape=['n_modes'],
        unit='1 / m',
        description="""
        Calculated value of the Raman frequencies.
        """,
    )


class MagneticOrdering(MSection):
    """
    Section containing results of a magnetic ordering workflow.
    """

    m_def = Section(validate=False)

    n_structures = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of magnetic structures evaluated.
        """,
    )

    n_atoms = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of atoms in the simulation cell.
        """,
    )

    labels = Quantity(
        type=str,
        shape=['n_structures'],
        description="""
        Labels corresponding to each magnetic structure.
        """,
    )

    energies = Quantity(
        type=np.dtype(np.float64),
        shape=['n_structures'],
        unit='joule',
        description="""
        Calculated value of the energies corresponding to each magnetic structure.
        """,
    )

    magnetic_moments = Quantity(
        type=np.dtype(np.float64),
        shape=['n_structures', 'n_atoms'],
        unit='bohr_magneton',
        description="""
        Resulting atomic magnetic moments corresponding to each magnetic structure.
        """,
    )

    magnetic_deformations = Quantity(
        type=np.dtype(np.float64),
        shape=['n_structures'],
        unit='m',
        description="""
        Average atomic displacements after relaxation with respect to the non-magnetic
        case for each magnetic structure.
        """,
    )


class Adsorption(MSection):
    """
    Section containing results of a surface adsorption workflow.
    """

    m_def = Section(validate=False)

    n_sites = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of sites for which the adsorption energy is evaluated.
        """,
    )

    slab_miller_index = Quantity(
        type=np.dtype(np.int32),
        shape=[3],
        description="""
        Miller index of the slab.
        """,
    )

    slab = Quantity(
        type=str,
        shape=[],
        description="""
        Chemical formula of the slab.
        """,
    )

    adsorbate = Quantity(
        type=str,
        shape=[],
        description="""
        Chemical formula of the adsorbate molecule.
        """,
    )

    adsorption_sites = Quantity(
        type=np.dtype(np.float64),
        shape=['n_sites'],
        description="""
        Coordinates of the adsorption sites corresponding to a minimum energy.
        """,
    )

    adsorption_energies = Quantity(
        type=np.dtype(np.float64),
        shape=['n_sites'],
        unit='joule',
        description="""
        Calculated value of the adsorption energy corresponding to each site.
        """,
    )


class ConvexHull(MSection):
    """
    Section containing results of a convex hull workflow.
    """

    m_def = Section(validate=False)

    n_elements = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of elements for which the thermal stability is evaluated. This represents
        the dimensionality of the convex hull.
        """,
    )

    n_points = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of points for which the energies are evaluated.
        """,
    )

    compositions = Quantity(
        type=np.dtype(np.float64),
        shape=['n_points', 'n_elements'],
        description="""
        Normalized composition of the elements corresponding to each point for which the
        energies are evaluated.
        """,
    )

    references = Quantity(
        type=str,
        shape=['n_elements'],
        description="""
        Specifies the reference structure for each element.
        """,
    )

    energy_of_formation = Quantity(
        type=np.dtype(np.float64),
        shape=['n_points'],
        unit='joule',
        description="""
        Values of the heat of formation corresponding to each point.
        """,
    )

    energy_hulll = Quantity(
        type=np.dtype(np.float64),
        shape=['n_points'],
        unit='joule',
        description="""
        Values of the energy above the convex hull corresponding to each point.
        """,
    )


class NudgedElasticBand(MSection):
    """
    Section containing results of a nudged-elastic band workflow.
    """

    m_def = Section(validate=False)

    method = Quantity(
        type=str,
        shape=[],
        description="""
        Specifies the method used in calculating the minimum energy path. Can be one of
        standard, improved_tangent, full_spring_force, spline_interpolation, string.
        """,
    )

    climbing_image = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if climbing image is used.
        """,
    )

    solid_state = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if solid state nudged-elastic band calculation is performed.
        """,
    )

    optimizer = Quantity(
        type=str,
        shape=[],
        description="""
        Specifies the method used in energy minimization.
        """,
    )

    n_images = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of images used in the calculation.
        """,
    )

    spring_constants = Quantity(
        type=np.dtype(np.float64),
        shape=['n_images'],
        unit='newton',
        description="""
        Spring constants for each spring.
        """,
    )

    energy_barrier = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Maximum value of the calculated energy barrier.
        """,
    )

    force_maximum = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='newton',
        description="""
        Maximum force along the minimum energy path.
        """,
    )


class EOSFit(MSection):
    """
    Section containing results of an equation of state fit.
    """

    m_def = Section(validate=False)

    function_name = Quantity(
        type=str,
        shape=[],
        description="""
        Specifies the function used to perform the fitting of the volume-energy data. Value
        can be one of birch_euler, birch_lagrange, birch_murnaghan, mie_gruneisen,
        murnaghan, pack_evans_james, poirier_tarantola, tait, vinet.
        """,
    )

    fitted_energies = Quantity(
        type=np.dtype(np.float64),
        shape=['n_points'],
        unit='joule',
        description="""
        Array of the fitted energies corresponding to each volume.
        """,
    )

    bulk_modulus = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='pascal',
        description="""
        Calculated value of the bulk modulus by fitting the volume-energy data.
        """,
    )

    bulk_modulus_derivative = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Calculated value of the pressure derivative of the bulk modulus.
        """,
    )

    equilibrium_volume = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='m ** 3',
        description="""
        Calculated value of the equilibrium volume by fitting the volume-energy data.
        """,
    )

    equilibrium_energy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Calculated value of the equilibrium energy by fitting the volume-energy data.
        """,
    )

    rms_error = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Root-mean squared value of the error in the fitting.
        """,
    )


class EquationOfState(MSection):
    """
    Section containing results of an equation of state workflow.
    """

    m_def = Section(validate=False)

    n_points = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of volume-energy pairs in data.
        """,
    )

    volumes = Quantity(
        type=np.dtype(np.float64),
        shape=['n_points'],
        unit='m ** 3',
        description="""
        Array of volumes per atom for which the energies are evaluated.
        """,
    )

    energies = Quantity(
        type=np.dtype(np.float64),
        shape=['n_points'],
        unit='joule',
        description="""
        Array of energies corresponding to each volume.
        """,
    )

    eos_fit = SubSection(sub_section=EOSFit.m_def, repeats=True)


class DebyeModel(MSection):
    """
    Section containing results of an debye-model workflow.
    """

    m_def = Section(validate=False)

    n_temperatures = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of temperature evaluations.
        """,
    )

    thermal_conductivity = Quantity(
        type=np.dtype(np.float64),
        shape=['n_temperatures'],
        unit='watt / meter * kelvin',
        description="""
        Calculated value of the thermal conductity.
        """,
    )

    debye_temperature = Quantity(
        type=np.dtype(np.float64),
        shape=['n_temperatures'],
        unit='kelvin',
        description="""
        Calculated value of the Debye temperature.
        """,
    )

    gruneisen_parameter = Quantity(
        type=np.dtype(np.float64),
        shape=['n_temperatures'],
        description="""
        Calculated value of the Gruneisen parameter.
        """,
    )

    thermal_expansion = Quantity(
        type=np.dtype(np.float64),
        shape=['n_temperatures'],
        unit='1 / kelvin',
        description="""
        Calculated value of the thermal expansion coefficient.
        """,
    )

    bulk_modulus_static = Quantity(
        type=np.dtype(np.float64),
        shape=['n_temperatures'],
        unit='pascal',
        description="""
        Calculated value of the static bulk modulus.
        """,
    )

    bulk_modulus_isothermal = Quantity(
        type=np.dtype(np.float64),
        shape=['n_temperatures'],
        unit='pascal',
        description="""
        Calculated value of the static bulk modulus.
        """,
    )


class GeometryOptimization(MSection):
    """
    Section containing the results of a geometry_optimization workflow.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=MEnum('static', 'atomic', 'cell_shape', 'cell_volume'),
        shape=[],
        description="""
        The type of geometry optimization, which denotes what is being optimized.

        Allowed values are:

        | Type                   | Description                               |

        | ---------------------- | ----------------------------------------- |

        | `"static"`             | no optimization |

        | `"atomic"`             | the atomic coordinates alone are updated |

        | `"cell_volume"`         | `"atomic"` + cell lattice parameters are updated isotropically |

        | `"cell_shape"`        | `"cell_volume"` but without the isotropic constraint: all cell parameters are updated |

        """,
    )

    method = Quantity(
        type=str,
        shape=[],
        description="""
        The method used for geometry optimization. Some known possible values are:
        `"steepest_descent"`, `"conjugant_gradient"`, `"low_memory_broyden_fletcher_goldfarb_shanno"`.
        """,
    )

    convergence_tolerance_energy_difference = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        The input energy difference tolerance criterion.
        """,
    )

    convergence_tolerance_force_maximum = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='newton',
        description="""
        The input maximum net force tolerance criterion.
        """,
    )

    convergence_tolerance_displacement_maximum = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='meter',
        description="""
        The input maximum displacement tolerance criterion.
        """,
    )

    final_energy_difference = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        The difference in the energy_total between the last two steps during
        optimization.
        """,
    )

    final_force_maximum = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='newton',
        description="""
        The maximum net force in the last optimization step.
        """,
    )

    final_displacement_maximum = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='meter',
        description="""
        The maximum displacement in the last optimization step with respect to previous.
        """,
    )

    optimization_steps_maximum = Quantity(
        type=int,
        shape=[],
        description="""
        Maximum number of optimization steps.
        """,
    )

    optimization_steps = Quantity(
        type=int,
        shape=[],
        description="""
        Number of saved optimization steps.
        """,
    )

    energies = Quantity(
        type=np.dtype(np.float64),
        unit='joule',
        shape=['optimization_steps'],
        description="""
        List of energy_total values gathered from the single configuration
        calculations that are a part of the optimization trajectory.
        """,
    )

    steps = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        The step index corresponding to each saved configuration.
        """,
    )

    is_converged_geometry = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if the geometry convergence criteria were fulfilled.
        """,
    )


class Phonon(MSection):
    """
    Section containing the results of a phonon workflow.
    """

    m_def = Section(validate=False)

    force_calculator = Quantity(
        type=str,
        shape=[],
        description="""
        Name of the program used to calculate the forces.
        """,
    )

    mesh_density = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='1 / meter ** 3',
        description="""
        Density of the k-mesh for sampling.
        """,
    )

    n_imaginary_frequencies = Quantity(
        type=int,
        shape=[],
        description="""
        Number of modes with imaginary frequencies.
        """,
    )

    random_displacements = Quantity(
        type=bool,
        shape=[],
        description="""
        Identifies if displacements are made randomly.
        """,
    )

    with_non_analytic_correction = Quantity(
        type=bool,
        shape=[],
        description="""
        Identifies if non-analytical term corrections are applied to dynamical matrix.
        """,
    )

    with_grueneisen_parameters = Quantity(
        type=bool,
        shape=[],
        description="""
        Identifies if Grueneisen parameters are calculated.
        """,
    )

    n_bands = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of phonon bands.
        """,
    )

    n_qpoints = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of q points for which phonon properties are evaluated.
        """,
    )

    qpoints = Quantity(
        type=np.dtype(np.float64),
        shape=['n_qpoints', 3],
        description="""
        Value of the qpoints.
        """,
    )

    group_velocity = Quantity(
        type=np.dtype(np.float64),
        shape=['n_qpoints', 'n_bands', 3],
        unit='meter / second',
        description="""
        Calculated value of the group velocity at each qpoint.
        """,
    )

    n_displacements = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of independent displacements.
        """,
    )

    n_atoms = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of atoms in the simulation cell.
        """,
    )

    displacements = Quantity(
        type=np.dtype(np.float64),
        shape=['n_displacements', 'n_atoms', 3],
        unit='meter',
        description="""
        Value of the displacements applied to each atom in the simulation cell.
        """,
    )


class StrainDiagrams(MSection):
    """
    Section containing the information regarding the elastic strains.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=str,
        shape=[],
        description="""
        Kind of strain diagram. Possible values are: energy; cross-validation (cross-
        validation error); d2E (second derivative of the energy wrt the strain)
        """,
    )

    n_eta = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of strain values used in the strain diagram
        """,
    )

    n_deformations = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of deformations.
        """,
    )

    value = Quantity(
        type=np.dtype(np.float64),
        shape=['n_deformations', 'n_eta'],
        description="""
        Values of the energy(units:J)/d2E(units:Pa)/cross-validation (depending on the
        value of strain_diagram_type)
        """,
    )

    eta = Quantity(
        type=np.dtype(np.float64),
        shape=['n_deformations', 'n_eta'],
        description="""
        eta values used the strain diagrams
        """,
    )

    stress_voigt_component = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Voigt component corresponding to the strain diagram
        """,
    )

    polynomial_fit_order = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Order of the polynomial fit
        """,
    )


class Elastic(MSection):
    """
    Section containing the results of an elastic workflow.
    """

    m_def = Section(validate=False)

    energy_stress_calculator = Quantity(
        type=str,
        shape=[],
        description="""
        Name of program used to calculate energy or stress.
        """,
    )

    calculation_method = Quantity(
        type=str,
        shape=[],
        description="""
        Method used to calculate elastic constants, can either be energy or stress.
        """,
    )

    elastic_constants_order = Quantity(
        type=int,
        shape=[],
        description="""
        Order of the calculated elastic constants.
        """,
    )

    n_deformations = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        Number of deformed structures used to calculate the elastic constants. This is
        determined by the symmetry of the crystal.
        """,
    )

    deformation_types = Quantity(
        type=np.str_,
        shape=['n_deformations', 6],
        description="""
        deformation types
        """,
    )

    n_strains = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
        number of equally spaced strains applied to each deformed structure, which are
        generated between the maximum negative strain and the maximum positive one.
        """,
    )

    is_mechanically_stable = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if structure is mechanically stable from the calculated values of the
        elastic constants.
        """,
    )

    fitting_error_maximum = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Maximum error in polynomial fit.
        """,
    )

    strain_maximum = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Maximum strain applied to crystal.
        """,
    )

    elastic_constants_notation_matrix_second_order = Quantity(
        type=np.str_,
        shape=[6, 6],
        description="""
        Symmetry of the second-order elastic constant matrix in Voigt notation
        """,
    )

    elastic_constants_matrix_second_order = Quantity(
        type=np.dtype(np.float64),
        shape=[6, 6],
        unit='pascal',
        description="""
        2nd order elastic constant (stiffness) matrix in pascals
        """,
    )

    elastic_constants_matrix_third_order = Quantity(
        type=np.dtype(np.float64),
        shape=[6, 6, 6],
        unit='pascal',
        description="""
        3rd order elastic constant (stiffness) matrix in pascals
        """,
    )

    compliance_matrix_second_order = Quantity(
        type=np.dtype(np.float64),
        shape=[6, 6],
        unit='1 / pascal',
        description="""
        Elastic compliance matrix in 1/GPa
        """,
    )

    elastic_constants_gradient_matrix_second_order = Quantity(
        type=np.dtype(np.float64),
        shape=[18, 18],
        unit='newton',
        description="""
        gradient of the 2nd order elastic constant (stiffness) matrix in newton
        """,
    )

    bulk_modulus_voigt = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='pascal',
        description="""
        Voigt bulk modulus
        """,
    )

    shear_modulus_voigt = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='pascal',
        description="""
        Voigt shear modulus
        """,
    )

    bulk_modulus_reuss = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='pascal',
        description="""
        Reuss bulk modulus
        """,
    )

    shear_modulus_reuss = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='pascal',
        description="""
        Reuss shear modulus
        """,
    )

    bulk_modulus_hill = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='pascal',
        description="""
        Hill bulk modulus
        """,
    )

    shear_modulus_hill = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='pascal',
        description="""
        Hill shear modulus
        """,
    )

    young_modulus_voigt = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='pascal',
        description="""
        Voigt Young modulus
        """,
    )

    poisson_ratio_voigt = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Voigt Poisson ratio
        """,
    )

    young_modulus_reuss = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='pascal',
        description="""
        Reuss Young modulus
        """,
    )

    poisson_ratio_reuss = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Reuss Poisson ratio
        """,
    )

    young_modulus_hill = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='pascal',
        description="""
        Hill Young modulus
        """,
    )

    poisson_ratio_hill = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Hill Poisson ratio
        """,
    )

    elastic_anisotropy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Elastic anisotropy
        """,
    )

    pugh_ratio_hill = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Pugh ratio defined as the ratio between the shear modulus and bulk modulus
        """,
    )

    debye_temperature = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='kelvin',
        description="""
        Debye temperature
        """,
    )

    speed_sound_transverse = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='meter / second',
        description="""
        Speed of sound along the transverse direction
        """,
    )

    speed_sound_longitudinal = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='meter / second',
        description="""
        Speed of sound along the longitudinal direction
        """,
    )

    speed_sound_average = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='meter / second',
        description="""
        Average speed of sound
        """,
    )

    eigenvalues_elastic = Quantity(
        type=np.dtype(np.float64),
        shape=[6],
        unit='pascal',
        description="""
        Eigenvalues of the stiffness matrix
        """,
    )

    strain_diagrams = SubSection(sub_section=StrainDiagrams.m_def, repeats=True)


class Decomposition(MSection):
    """
    Section containing information about the system to which an unstable compound will
    decompose to.
    """

    m_def = Section(validate=False)

    fraction = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
        Amount of the resulting system.
        """,
    )

    system_ref = Quantity(
        type=Reference(System.m_def),
        shape=[],
        description="""
        Reference to the resulting system.
        """,
    )

    formula = Quantity(
        type=str,
        shape=[],
        description="""
        Chemical formula of the resulting system.
        """,
    )


class Stability(MSection):
    """
    Section containing information regarding the stability of the system.
    """

    m_def = Section(validate=False)

    n_references = Quantity(
        type=int,
        shape=[],
        description="""
        Number of reference systems.
        """,
    )

    systems_ref = Quantity(
        type=Reference(System.m_def),
        shape=['n_references'],
        description="""
        References to the reference systems.
        """,
    )

    formation_energy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Calculated value of the formation energy of the compound.
        """,
    )

    delta_formation_energy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        Energy with respect to the convex hull.
        """,
    )

    is_stable = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if a compound is stable.
        """,
    )

    decomposition = SubSection(sub_section=Decomposition.m_def, repeats=True)


class Thermodynamics(MSection):
    """
    Section containing the results of a thermodynamics workflow.
    """

    m_def = Section(validate=False)

    n_values = Quantity(
        type=int,
        shape=[],
        description="""
        Number of thermodynamics property evaluations.
        """,
    )

    temperature = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='kelvin',
        description="""
        Specifies the temperatures at which properties such as the Helmholtz free energy
        are calculated.
        """,
    )

    pressure = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='pascal',
        description="""
        Array containing the values of the pressure (one third of the trace of the stress
        tensor) corresponding to each property evaluation.
        """,
    )

    helmholtz_free_energy = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule',
        description="""
        Helmholtz free energy per unit cell at constant volume.
        """,
    )

    heat_capacity_c_p = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule / kelvin',
        description="""
        Heat capacity per cell unit at constant pressure.
        """,
    )

    heat_capacity_c_v = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule / kelvin',
        description="""
        Heat capacity per cell unit at constant volume.
        """,
    )

    @derived(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule / (kelvin * kilogram)',
        description="""
        Specific heat capacity at constant volume.
        """,
        cached=True,
    )
    def heat_capacity_c_v_specific(self) -> np.ndarray:
        """Returns the specific heat capacity by dividing the heat capacity per
        cell with the mass of the atoms in the cell.
        """
        import nomad.atomutils

        workflow = self.m_parent
        system = workflow.calculations_ref[0].system_ref
        atomic_numbers = system.atoms.species
        mass_per_unit_cell = nomad.atomutils.get_summed_atomic_mass(atomic_numbers)
        heat_capacity = self.heat_capacity_c_v
        specific_heat_capacity = heat_capacity / mass_per_unit_cell

        return specific_heat_capacity.magnitude

    vibrational_free_energy_at_constant_volume = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule',
        description="""
        Holds the vibrational free energy per cell unit at constant volume.
        """,
    )

    @derived(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule / kilogram',
        description="""
        Stores the specific vibrational free energy at constant volume.
        """,
        cached=True,
    )
    def vibrational_free_energy_at_constant_volume_specific(self) -> np.ndarray:
        import nomad.atomutils

        workflow = self.m_parent
        system = workflow.calculations_ref[0].system_ref
        atomic_numbers = system.atoms.species
        mass_per_unit_cell = nomad.atomutils.get_summed_atomic_mass(atomic_numbers)
        free_energy = self.vibrational_free_energy_at_constant_volume
        specific_free_energy = free_energy / mass_per_unit_cell

        return specific_free_energy.magnitude

    vibrational_free_energy = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule',
        description="""
        Calculated value of the vibrational free energy, F_vib.
        """,
    )

    vibrational_internal_energy = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule',
        description="""
        Calculated value of the vibrational internal energy, U_vib.
        """,
    )

    vibrational_entropy = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule / kelvin',
        description="""
        Calculated value of the vibrational entropy, S.
        """,
    )

    gibbs_free_energy = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule',
        description="""
        Calculated value of the Gibbs free energy, G.
        """,
    )

    entropy = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule / kelvin',
        description="""
        Calculated value of the entropy.
        """,
    )

    internal_energy = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='joule',
        description="""
        Calculated value of the internal energy, U.
        """,
    )

    stability = SubSection(sub_section=Stability.m_def, repeats=False)


class ThermostatParameters(MSection):
    """
    Section containing the parameters pertaining to the thermostat for a molecular dynamics run.
    """

    m_def = Section(validate=False)

    thermostat_type = Quantity(
        type=MEnum(
            'andersen',
            'berendsen',
            'brownian',
            'langevin_goga',
            'langevin_schneider',
            'nose_hoover',
            'velocity_rescaling',
            'velocity_rescaling_langevin',
        ),
        shape=[],
        description="""
        The name of the thermostat used for temperature control. If skipped or an empty string is used, it
        means no thermostat was applied.

        Allowed values are:

        | Thermostat Name        | Description                               |

        | ---------------------- | ----------------------------------------- |

        | `""`                   | No thermostat               |

        | `"andersen"`           | H.C. Andersen, [J. Chem. Phys.
        **72**, 2384 (1980)](https://doi.org/10.1063/1.439486) |

        | `"berendsen"`          | H. J. C. Berendsen, J. P. M. Postma,
        W. F. van Gunsteren, A. DiNola, and J. R. Haak, [J. Chem. Phys.
        **81**, 3684 (1984)](https://doi.org/10.1063/1.448118) |

        | `"brownian"`           | Brownian Dynamics |

        | `"langevin_goga"`           | N. Goga, A. J. Rzepiela, A. H. de Vries,
        S. J. Marrink, and H. J. C. Berendsen, [J. Chem. Theory Comput. **8**, 3637 (2012)]
        (https://doi.org/10.1021/ct3000876) |

        | `"langevin_schneider"`           | T. Schneider and E. Stoll,
        [Phys. Rev. B **17**, 1302](https://doi.org/10.1103/PhysRevB.17.1302) |

        | `"nose_hoover"`        | S. Nosé, [Mol. Phys. **52**, 255 (1984)]
        (https://doi.org/10.1080/00268978400101201); W.G. Hoover, [Phys. Rev. A
        **31**, 1695 (1985) |

        | `"velocity_rescaling"` | G. Bussi, D. Donadio, and M. Parrinello,
        [J. Chem. Phys. **126**, 014101 (2007)](https://doi.org/10.1063/1.2408420) |

        | `"velocity_rescaling_langevin"` | G. Bussi and M. Parrinello,
        [Phys. Rev. E **75**, 056707 (2007)](https://doi.org/10.1103/PhysRevE.75.056707) |
        """,
    )

    reference_temperature = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='kelvin',
        description="""
        The target temperature for the simulation.
        """,
    )

    coupling_constant = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='s',
        description="""
        The time constant for temperature coupling. Need to describe what this means for the various
        thermostat options...
        """,
    )


class BarostatParameters(MSection):
    """
    Section containing the parameters pertaining to the barostat for a molecular dynamics run.
    """

    m_def = Section(validate=False)

    barostat_type = Quantity(
        type=MEnum(
            'berendsen',
            'martyna_tuckerman_tobias_klein',
            'nose_hoover',
            'parrinello_rahman',
            'stochastic_cell_rescaling',
        ),
        shape=[],
        description="""
        The name of the barostat used for temperature control. If skipped or an empty string is used, it
        means no barostat was applied.

        Allowed values are:

        | Barostat Name          | Description                               |

        | ---------------------- | ----------------------------------------- |

        | `""`                   | No thermostat               |

        | `"berendsen"`          | H. J. C. Berendsen, J. P. M. Postma,
        W. F. van Gunsteren, A. DiNola, and J. R. Haak, [J. Chem. Phys.
        **81**, 3684 (1984)](https://doi.org/10.1063/1.448118) |

        | `"martyna_tuckerman_tobias_klein"` | G.J. Martyna, M.E. Tuckerman, D.J. Tobias, and M.L. Klein,
        [Mol. Phys. **87**, 1117 (1996)](https://doi.org/10.1080/00268979600100761);
        M.E. Tuckerman, J. Alejandre, R. López-Rendón, A.L. Jochim, and G.J. Martyna,
        [J. Phys. A. **59**, 5629 (2006)](https://doi.org/10.1088/0305-4470/39/19/S18)|

        | `"nose_hoover"`        | S. Nosé, [Mol. Phys. **52**, 255 (1984)]
        (https://doi.org/10.1080/00268978400101201); W.G. Hoover, [Phys. Rev. A
        **31**, 1695 (1985) |

        | `"parrinello_rahman"`        | M. Parrinello and A. Rahman,
        [J. Appl. Phys. **52**, 7182 (1981)](https://doi.org/10.1063/1.328693);
        S. Nosé and M.L. Klein, [Mol. Phys. **50**, 1055 (1983) |

        | `"stochastic_cell_rescaling"` | M. Bernetti and G. Bussi,
        [J. Chem. Phys. **153**, 114107 (2020)](https://doi.org/10.1063/1.2408420) |
        """,
    )

    coupling_type = Quantity(
        type=MEnum('isotropic', 'semi_isotropic', 'anisotropic'),
        shape=[],
        description="""
        Describes the symmetry of pressure coupling. Specifics can be inferred from the `coupling constant`

        | Type          | Description                               |

        | ---------------------- | ----------------------------------------- |

        | `isotropic`          | Identical coupling in all directions. |

        | `semi_isotropic` | Identical coupling in 2 directions. |

        | `anisotropic`        | General case. |
        """,
    )

    reference_pressure = Quantity(
        type=np.dtype(np.float64),
        shape=[3, 3],
        unit='pascal',
        description="""
        The target pressure for the simulation, stored in a 3x3 matrix, indicating the values for individual directions
        along the diagonal, and coupling between directions on the off-diagonal.
        """,
    )

    coupling_constant = Quantity(
        type=np.dtype(np.float64),
        shape=[3, 3],
        unit='s',
        description="""
        The time constants for pressure coupling, stored in a 3x3 matrix, indicating the values for individual directions
        along the diagonal, and coupling between directions on the off-diagonal. 0 values along the off-diagonal
        indicate no-coupling between these directions.
        """,
    )

    compressibility = Quantity(
        type=np.dtype(np.float64),
        shape=[3, 3],
        unit='1 / pascal',
        description="""
        An estimate of the system's compressibility, used for box rescaling, stored in a 3x3 matrix indicating the values for individual directions
        along the diagonal, and coupling between directions on the off-diagonal. If None, it may indicate that these values
        are incorporated into the coupling_constant, or simply that the software used uses a fixed value that is not available in
        the input/output files.
        """,
    )


class IntegrationParameters(MSection):
    """
    Section containing the parameters for the molecular dynamics integrator.
    """

    m_def = Section(validate=False)

    integrator_type = Quantity(
        type=MEnum(
            'brownian',
            'conjugant_gradient',
            'langevin_goga',
            'langevin_schneider',
            'leap_frog',
            'rRESPA_multitimescale',
            'velocity_verlet',
        ),
        shape=[],
        description="""
        Name of the integrator.

        Allowed values are:

        | Integrator Name          | Description                               |

        | ---------------------- | ----------------------------------------- |

        | `"langevin_goga"`           | N. Goga, A. J. Rzepiela, A. H. de Vries,
        S. J. Marrink, and H. J. C. Berendsen, [J. Chem. Theory Comput. **8**, 3637 (2012)]
        (https://doi.org/10.1021/ct3000876) |

        | `"langevin_schneider"`           | T. Schneider and E. Stoll,
        [Phys. Rev. B **17**, 1302](https://doi.org/10.1103/PhysRevB.17.1302) |

        | `"leap_frog"`          | R.W. Hockney, S.P. Goel, and J. Eastwood,
        [J. Comp. Phys. **14**, 148 (1974)](https://doi.org/10.1016/0021-9991(74)90010-2) |

        | `"velocity_verlet"` | W.C. Swope, H.C. Andersen, P.H. Berens, and K.R. Wilson,
        [J. Chem. Phys. **76**, 637 (1982)](https://doi.org/10.1063/1.442716) |

        | `"rRESPA_multitimescale"` | M. Tuckerman, B. J. Berne, and G. J. Martyna
        [J. Chem. Phys. **97**, 1990 (1992)](https://doi.org/10.1063/1.463137) |
        """,
    )

    integration_timestep = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='s',
        description="""
        The timestep at which the numerical integration is performed.
        """,
    )

    n_steps = Quantity(
        type=int,
        shape=[],
        description="""
        Number of timesteps performed.
        """,
    )

    coordinate_save_frequency = Quantity(
        type=int,
        shape=[],
        description="""
        The number of timesteps between saving the coordinates.
        """,
    )

    velocity_save_frequency = Quantity(
        type=int,
        shape=[],
        description="""
        The number of timesteps between saving the velocities.
        """,
    )

    force_save_frequency = Quantity(
        type=int,
        shape=[],
        description="""
        The number of timesteps between saving the forces.
        """,
    )

    thermodynamics_save_frequency = Quantity(
        type=int,
        shape=[],
        description="""
        The number of timesteps between saving the thermodynamic quantities.
        """,
    )

    thermostat_parameters = SubSection(
        sub_section=ThermostatParameters.m_def, repeats=False
    )

    barostat_parameters = SubSection(
        sub_section=BarostatParameters.m_def, repeats=False
    )


class MolecularDynamicsResults(MSection):
    """
    Section containing the parameters for sampling via molecular dynamics using a force field model.
    """

    m_def = Section(validate=False)

    radial_distribution_functions = SubSection(
        sub_section=SectionProxy('RadialDistributionFunction'), repeats=True
    )

    mean_squared_displacements = SubSection(
        sub_section=SectionProxy('MeanSquaredDisplacement'), repeats=True
    )


class MolecularDynamics(MSection):
    """
    Section containing results of molecular dynamics workflow.
    """

    m_def = Section(validate=False)

    thermodynamic_ensemble = Quantity(
        type=MEnum('NVE', 'NVT', 'NPT', 'NPH'),
        shape=[],
        description="""
        The type of thermodynamic ensemble that was simulated.

        Allowed values are:

        | Thermodynamic Ensemble          | Description                               |

        | ---------------------- | ----------------------------------------- |

        | `"NVE"`           | Constant number of particles, volume, and energy |

        | `"NVT"`           | Constant number of particles, volume, and temperature |

        | `"NPT"`           | Constant number of particles, pressure, and temperature |

        | `"NPH"`           | Constant number of particles, pressure, and enthalpy |
        """,
    )

    finished_normally = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if calculation terminated normally.
        """,
    )

    with_trajectory = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if calculation includes trajectory data.
        """,
    )

    with_thermodynamics = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if calculation contains thermodynamic data.
        """,
    )

    integration_parameters = SubSection(
        sub_section=IntegrationParameters.m_def, repeats=False
    )

    results = SubSection(sub_section=MolecularDynamicsResults.m_def, repeats=False)


class TrajectoryProperty(MSection):
    """
    Generic section containing information about a calculation of any observable
    defined and stored at each individual frame of a trajectory.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=MEnum('molecular', 'atomic'),
        shape=[],
        description="""
        Describes if the observable is calculated at the molecular or atomic level.
        """,
    )

    error_type = Quantity(
        type=str,
        shape=[],
        description="""
        Describes the type of error reported for this observable.
        """,
    )


class EnsemblePropertyValues(MSection):
    """
    Generic section containing information regarding the values of an ensemble property.
    """

    m_def = Section(validate=False)

    label = Quantity(
        type=str,
        shape=[],
        description="""
        Describes the group of atoms/molecule/groups of molecules involved in determining the property.
        """,
    )

    atomsgroup_ref = Quantity(
        type=Reference(AtomsGroup.m_def),
        shape=[],
        description="""
        References to the atoms_group section containing the group of atoms/molecule/groups of molecules for which the property was calculated.
        """,
    )

    n_bins = Quantity(
        type=int,
        shape=[],
        description="""
        Number of bins.
        """,
    )

    frame_start = Quantity(
        type=int,
        shape=[],
        description="""
        Trajectory frame number where the ensemble averaging starts.
        """,
    )

    frame_end = Quantity(
        type=int,
        shape=[],
        description="""
        Trajectory frame number where the ensemble averaging ends.
        """,
    )


class EnsembleProperty(MSection):
    """
    Generic section containing information about a calculation of any static observable
    from a trajectory (i.e., from an ensemble average).
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=MEnum('molecular', 'atomic'),
        shape=[],
        description="""
        Describes if the observable is calculated at the molecular or atomic level.
        """,
    )

    n_smooth = Quantity(
        type=int,
        shape=[],
        description="""
        Number of bins over which the running average was computed for
        the observable `values'.
        """,
    )

    error_type = Quantity(
        type=str,
        shape=[],
        description="""
        Describes the type of error reported for this observable.
        """,
    )

    n_variables = Quantity(
        type=int,
        shape=[],
        description="""
        Number of variables along which the property is determined.
        """,
    )

    variables_name = Quantity(
        type=str,
        shape=['n_variables'],
        description="""
        Name/description of the independent variables along which the observable is defined.
        """,
    )


class RadialDistributionFunctionValues(EnsemblePropertyValues):
    """
    Section containing information regarding the values of
    radial distribution functions (rdfs).
    """

    m_def = Section(validate=False)

    bins = Quantity(
        type=np.dtype(np.float64),
        shape=['n_bins'],
        unit='m',
        description="""
        Distances along which the rdf was calculated.
        """,
    )

    value = Quantity(
        type=np.dtype(np.float64),
        shape=['n_bins'],
        description="""
        Values of the property.
        """,
    )


class RadialDistributionFunction(EnsembleProperty):
    """
    Section containing information about the calculation of
    radial distribution functions (rdfs).
    """

    m_def = Section(validate=False)

    radial_distribution_function_values = SubSection(
        sub_section=RadialDistributionFunctionValues.m_def, repeats=True
    )


class CorrelationFunctionValues(MSection):
    """
    Generic section containing information regarding the values of a correlation function.
    """

    m_def = Section(validate=False)

    label = Quantity(
        type=str,
        shape=[],
        description="""
        Describes the group of atoms/molecule/groups of molecules involved in determining the property.
        """,
    )

    atomsgroup_ref = Quantity(
        type=Reference(AtomsGroup.m_def),
        shape=[1],
        description="""
        References to the atoms_group section containing the group of atoms/molecule/groups of molecules for which the property was calculated.
        """,
    )

    n_times = Quantity(
        type=int,
        shape=[],
        description="""
        Number of times windows for the calculation of the correlation function.
        """,
    )


class CorrelationFunction(MSection):
    """
    Generic section containing information about a calculation of any time correlation
    function from a trajectory.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=MEnum('molecular', 'atomic'),
        shape=[],
        description="""
        Describes if the correlation function is calculated at the molecular or atomic level.
        """,
    )

    direction = Quantity(
        type=MEnum('x', 'y', 'z', 'xy', 'yz', 'xz', 'xyz'),
        shape=[],
        description="""
        Describes the direction in which the correlation function was calculated.
        """,
    )

    error_type = Quantity(
        type=str,
        shape=[],
        description="""
        Describes the type of error reported for this correlation function.
        """,
    )


class DiffusionConstantValues(MSection):
    """
    Section containing information regarding the diffusion constants.
    """

    m_def = Section(validate=False)

    value = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='m^2/s',
        description="""
        Values of the diffusion constants.
        """,
    )

    error_type = Quantity(
        type=str,
        shape=[],
        description="""
        Describes the type of error reported for this observable.
        """,
    )

    errors = Quantity(
        type=np.dtype(np.float64),
        shape=['*'],
        description="""
        Error associated with the determination of the diffusion constant.
        """,
    )


class MeanSquaredDisplacementValues(CorrelationFunctionValues):
    """
    Section containing information regarding the values of a mean squared displacements (msds).
    """

    m_def = Section(validate=False)

    times = Quantity(
        type=np.dtype(np.float64),
        shape=['n_times'],
        unit='s',
        description="""
        Time windows used for the calculation of the msds.
        """,
    )

    value = Quantity(
        type=np.dtype(np.float64),
        shape=['n_times'],
        unit='m^2',
        description="""
        Msd values.
        """,
    )

    errors = Quantity(
        type=np.dtype(np.float64),
        shape=['*'],
        description="""
        Error associated with the determination of the msds.
        """,
    )

    diffusion_constant = SubSection(
        sub_section=DiffusionConstantValues.m_def, repeats=False
    )


class MeanSquaredDisplacement(CorrelationFunction):
    """
    Section containing information about a calculation of any mean squared displacements (msds).
    """

    m_def = Section(validate=False)

    mean_squared_displacement_values = SubSection(
        sub_section=MeanSquaredDisplacementValues.m_def, repeats=True
    )


class GW(MSection):
    """
    Section containing results of a GW workflow
    """

    m_def = Section(validate=False)

    dos_dft = Quantity(
        type=Reference(Dos),
        description="""
        DFT density of states
        """,
    )

    dos_gw = Quantity(
        type=Reference(Dos),
        description="""
        GW density of states
        """,
    )

    band_structure_dft = Quantity(
        type=Reference(BandStructure),
        description="""
        DFT density of states
        """,
    )

    band_structure_gw = Quantity(
        type=Reference(BandStructure),
        description="""
        DFT density of states
        """,
    )


class SinglePoint(MSection):
    """
    Section containing results of a single point workflow.
    """

    m_def = Section(validate=False)

    method = Quantity(
        type=str,
        shape=[],
        description="""
        Calculation method used.
        """,
    )

    n_scf_steps = Quantity(
        type=int,
        shape=[],
        description="""
        Number of self-consistent steps in the calculation
        """,
    )

    final_scf_energy_difference = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='joule',
        description="""
        The difference in the energy between the last two scf steps.
        """,
    )

    is_converged = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if the convergence criteria were fulfilled
        """,
    )

    with_density_of_states = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if the calculation contains density of states data
        """,
    )

    with_bandstructure = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if the calculation contains bandstructure data
        """,
    )

    with_eigenvalues = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if the calculation contains eigenvalue data
        """,
    )

    with_volumetric_data = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if the calculation contains volumetric data
        """,
    )

    with_excited_states = Quantity(
        type=bool,
        shape=[],
        description="""
        Indicates if the calculation contains excited states data
        """,
    )


class Task(MSection):
    """
    Section defining a specific task in the workflow chain. It has an input and an output,
    both can either be a workflow or a calculation and their relation is noted in the
    description.
    """

    m_def = Section(validate=False)

    input_workflow = Quantity(
        type=Reference(SectionProxy('Workflow')),
        shape=[],
        description="""
        Reference to the input workflow.
        """,
    )

    output_workflow = Quantity(
        type=Reference(SectionProxy('Workflow')),
        shape=[],
        description="""
        Reference to the output workflow.
        """,
    )

    input_calculation = Quantity(
        type=Reference(Calculation.m_def),
        shape=[],
        description="""
        Reference to the input calculation.
        """,
    )

    output_calculation = Quantity(
        type=Reference(Calculation.m_def),
        shape=[],
        description="""
        Reference to the output calculation.
        """,
    )

    description = Quantity(
        type=str,
        shape=[],
        description="""
        Describes the relationship between the input and output workflows. For example, if
        a single_point workflow uses the relaxed structure from a geometry_optimization as
        an input, the description may be "relaxed structure from geometry_optimization
        used to calucalate single_point properties"
        """,
    )


class Workflow(MSection):
    """
    Section containing the  results of a workflow.
    """

    m_def = Section(validate=False)

    type = Quantity(
        type=MEnum(
            [
                'GW',
                'single_point',
                'geometry_optimization',
                'phonon',
                'elastic',
                'molecular_dynamics',
                'debye_model',
                'equation_of_state',
                'nudged_elastic_band',
                'convex_hull',
                'adsorption',
                'magnetic_ordering',
                'raman',
                'interface',
                'thermodynamics',
            ]
        ),
        shape=[],
        description="""
        The workflow type.
        """,
    )

    initial_structure = Quantity(
        type=Reference(Atoms.m_def),
        shape=[],
        description="""
        Starting structure for geometry optimization.
        """,
    )

    calculator = Quantity(
        type=str,
        shape=[],
        description="""
        Energy and force calculator.
        """,
    )

    calculation_result_ref = Quantity(
        type=Reference(Calculation.m_def),
        shape=[],
        description="""
        Reference to calculation result. In the case of geometry_optimization and
        molecular dynamics, this corresponds to the final step in the simulation. For the
        rest of the workflow types, it refers to the original system.
        """,
        categories=[FastAccess],
    )

    n_calculations = Quantity(
        type=int,
        shape=[],
        description="""
        Number of calculations in workflow
        """,
    )

    calculations_ref = Quantity(
        type=Reference(Calculation.m_def),
        shape=['n_calculations'],
        description="""
        List of references to each section single_configuration_calculation in the
        simulation.
        """,
    )

    run_ref = Quantity(
        type=Reference(Run.m_def),
        shape=[],
        description="""
        Links the section workflow to the section run that contains the calculations.
        """,
        categories=[FastAccess],
    )

    n_references = Quantity(
        type=np.dtype(np.int32),
        shape=[],
        description="""
         Number of references to the current section workflow.
        """,
    )

    workflows_ref = Quantity(
        type=Reference(SectionProxy('Workflow')),
        shape=['n_references'],
        description="""
        Links the the current section to other workflow sections. Such a link is necessary
        for example between an Debye model that uses a the poisson ratio calculated
        from an elastic workflow.
        """,
        categories=[FastAccess],
    )

    task = SubSection(sub_section=Task.m_def, repeats=True)

    single_point = SubSection(
        sub_section=SinglePoint.m_def, repeats=False, categories=[FastAccess]
    )

    gw = SubSection(sub_section=GW.m_def, repeats=False, categories=[FastAccess])

    geometry_optimization = SubSection(
        sub_section=GeometryOptimization.m_def, repeats=False, categories=[FastAccess]
    )

    phonon = SubSection(
        sub_section=Phonon.m_def, repeats=False, categories=[FastAccess]
    )

    elastic = SubSection(sub_section=Elastic.m_def, repeats=False)

    molecular_dynamics = SubSection(sub_section=MolecularDynamics.m_def, repeats=False)

    debye_model = SubSection(sub_section=DebyeModel.m_def, repeats=False)

    equation_of_state = SubSection(sub_section=EquationOfState.m_def, repeats=False)

    nudged_elastic_band = SubSection(sub_section=NudgedElasticBand.m_def, repeats=False)

    convex_hull = SubSection(sub_section=ConvexHull.m_def, repeats=False)

    adsorption = SubSection(sub_section=Adsorption.m_def, repeats=False)

    magnetic_ordering = SubSection(sub_section=MagneticOrdering.m_def, repeats=False)

    raman = SubSection(sub_section=Raman.m_def, repeats=False)

    interface = SubSection(sub_section=Interface.m_def, repeats=False)

    thermodynamics = SubSection(sub_section=Thermodynamics.m_def, repeats=False)


m_package.__init_metainfo__()
