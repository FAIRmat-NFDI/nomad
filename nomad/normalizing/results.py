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

import os
import re
from typing import Any

import ase.data
import matid.geometry  # pylint: disable=import-error
import numpy as np
from matid import SymmetryAnalyzer  # pylint: disable=import-error

from nomad import atomutils
from nomad.atomutils import Formula
from nomad.config import config
from nomad.datamodel import EntryArchive
from nomad.datamodel.data import ArchiveSection
from nomad.datamodel.metainfo.workflow import Workflow
from nomad.datamodel.results import (
    BandGap,
    BandGapDeprecated,
    BandStructureElectronic,
    BandStructurePhonon,
    BulkModulus,
    DensityCharge,
    DOSElectronic,
    DOSElectronicNew,
    DOSNew,
    DOSPhonon,
    DynamicalProperties,
    EELSMethodology,
    ElectricFieldGradient,
    ElectronicProperties,
    EnergyDynamic,
    EnergyFreeHelmholtz,
    EnergyVolumeCurve,
    GeometryOptimization,
    GreensFunctionsElectronic,
    HeatCapacityConstantVolume,
    MagneticProperties,
    MagneticShielding,
    MagneticSusceptibility,
    Material,
    MDProvenance,
    MeanSquaredDisplacement,
    MechanicalProperties,
    Method,
    MolecularDynamics,
    PressureDynamic,
    Properties,
    RadialDistributionFunction,
    RadiusOfGyration,
    Results,
    ShearModulus,
    Spectra,
    SpectraProvenance,
    SpectroscopicProperties,
    SpinSpinCoupling,
    StructuralProperties,
    TemperatureDynamic,
    ThermodynamicProperties,
    Trajectory,
    VibrationalProperties,
    VolumeDynamic,
)
from nomad.normalizing.common import structures_2d
from nomad.normalizing.material import MaterialNormalizer
from nomad.normalizing.method import MethodNormalizer
from nomad.normalizing.normalizer import Normalizer
from nomad.utils import extract_section, traverse_reversed

try:
    import runschema

    runschema.run_schema_entry_point.load()
    import runschema.calculation
    import runschema.method
    import runschema.system
except Exception as e:
    runschema, simulationworkflowschema = None, None

re_label = re.compile('^([a-zA-Z][a-zA-Z]?)[^a-zA-Z]*')
elements = set(ase.data.chemical_symbols)


def valid_array(array: Any) -> bool:
    """Checks if the given variable is a non-empty array."""
    return array is not None and len(array) > 0


def isint(value: Any) -> bool:
    """Checks if the given variable can be interpreted as an integer."""
    try:
        int(value)
        return True
    except ValueError:
        return False


def is_valid_workflow(workflow: Workflow) -> bool:
    if not workflow:
        return False
    return 'nomad_simulation' not in workflow.m_def.qualified_name()


class ResultsNormalizer(Normalizer):
    domain = None
    normalizer_level = 3

    def normalize(self, archive: EntryArchive, logger=None) -> None:
        self.entry_archive = archive

        # Try to find run section. Note that even the definition
        # may be missing and can cause an AttributeError.
        try:
            self.section_run = archive.run[0]
        except (AttributeError, IndexError):
            self.section_run = None

        # Setup logger
        if logger is not None:
            self.logger = logger.bind(normalizer=self.__class__.__name__)

        results = self.entry_archive.results
        if results is None:
            results = self.entry_archive.m_create(Results)
        if results.properties is None:
            results.m_create(Properties)

        if self.section_run:
            self.normalize_run(logger=self.logger)

        for measurement in self.entry_archive.measurement:
            self.normalize_measurement(measurement)

        self.entry_archive = None
        self.section_run = None

    def normalize_sample(self, sample) -> None:
        material = self.entry_archive.m_setdefault('results.material')

        if sample.elements and len(sample.elements) > 0:
            material.elements = sample.elements
        else:
            # Try to guess elements from sample formula or name
            if sample.chemical_formula:
                try:
                    material.elements = list(
                        set(ase.Atoms(sample.chemical_formula).get_chemical_symbols())
                    )
                except Exception:
                    if sample.name:
                        try:
                            material.elements = list(
                                set(ase.Atoms(sample.name).get_chemical_symbols())
                            )
                        except Exception:
                            pass
        if sample.chemical_formula:
            material.chemical_formula_descriptive = sample.chemical_formula

        try:
            if material.chemical_formula_descriptive:
                formula = Formula(material.chemical_formula_descriptive)
                if not material.elements:
                    material.elements = formula.elements()
                material.elemental_composition = formula.elemental_composition()
                material.chemical_formula_hill = formula.format('hill')
                material.chemical_formula_reduced = formula.format('reduced')
                material.chemical_formula_iupac = formula.format('iupac')
                material.chemical_formula_descriptive = formula.format('descriptive')
        except Exception as e:
            self.logger.warn('could not normalize material', exc_info=e)

    def normalize_measurement(self, measurement) -> None:
        results = self.entry_archive.results

        # Method
        if results.method is None:
            results.method = Method(method_name=measurement.method_abbreviation)

        # Sample
        if results.material is None:
            results.material = Material(elements=[])
        if len(measurement.sample) > 0:
            self.normalize_sample(measurement.sample[0])

        # Results properties for EELSDB
        if measurement.m_xpath('eels.spectrum'):
            properties = results.properties
            spectroscopic = properties.m_create(SpectroscopicProperties)

            spectra = Spectra(
                type='EELS',
                label='experiment',
                n_energies=measurement.eels.spectrum.n_values,
                energies=measurement.eels.spectrum.energy,
                intensities=measurement.eels.spectrum.count,
                intensities_units='counts',
            )
            if measurement.instrument:
                provenance = spectra.m_create(SpectraProvenance)
                provenance.label = 'EELSDB'
                methodology = EELSMethodology(
                    resolution=measurement.instrument[0].eels.resolution,
                    detector_type=measurement.instrument[0].eels.detector_type,
                    min_energy=measurement.instrument[0].eels.min_energy,
                    max_energy=measurement.instrument[0].eels.max_energy,
                )
                provenance.m_add_sub_section(SpectraProvenance.eels, methodology)
            spectroscopic.m_add_sub_section(SpectroscopicProperties.spectra, spectra)

    def normalize_run(self, logger=None) -> None:
        # Fetch different information resources from which data is gathered
        repr_system = None
        for section in self.section_run.system:
            if section.is_representative:
                repr_system = section
                break
        try:
            optimade = self.entry_archive.metadata.optimade
        except Exception:
            optimade = None

        repr_symmetry = None
        if repr_system and repr_system.symmetry:
            repr_symmetry = repr_system.symmetry[0]

        # Create the section and populate the subsections
        results = self.entry_archive.results
        properties, conv_atoms, wyckoff_sets, spg_number = self.properties(
            repr_system, repr_symmetry
        )
        results.properties = properties
        results.material = MaterialNormalizer(
            self.entry_archive,
            repr_system,
            repr_symmetry,
            spg_number,
            conv_atoms,
            wyckoff_sets,
            properties,
            optimade,
            logger,
        ).material()

        results.method = MethodNormalizer(
            self.entry_archive, repr_system, results.material, logger
        ).method()

        # set entry type based on method and material
        workflow = self.entry_archive.workflow2
        if workflow is not None:
            workflow_name = workflow.name if workflow.name else workflow.m_def.name

            tag = ''
            if results.method.simulation:
                tag = 'simulation'
                entry_type = self.entry_archive.metadata.entry_type
                try:
                    if entry_type == workflow_name or not entry_type:
                        method_name = results.method.method_name
                        program_name = results.method.simulation.program_name
                        if workflow_name == 'SinglePoint' and method_name:
                            self.entry_archive.metadata.entry_type = (
                                f'{program_name} {method_name} {workflow_name}'
                            )
                        else:
                            self.entry_archive.metadata.entry_type = (
                                f'{program_name} {workflow_name}'
                            )
                except Exception:
                    if not entry_type:
                        self.entry_archive.metadata.entry_type = workflow_name
            type_tag = f'{self.entry_archive.metadata.entry_type} {tag}'

            # Populate entry_name
            entry_name = self.entry_archive.metadata.entry_name
            if not entry_name or entry_name == os.path.basename(
                self.entry_archive.metadata.mainfile
            ):
                material = results.material
                if material and material.chemical_formula_descriptive:
                    self.entry_archive.metadata.entry_name = (
                        f'{material.chemical_formula_descriptive} {type_tag}'
                    )
                else:
                    self.entry_archive.metadata.entry_name = f'{type_tag}'

    def resolve_band_gap(
        self, path: list[str] = ['run', 'calculation', 'band_gap']
    ) -> list[BandGap]:
        """Extract all band gaps from the given `path` and return them in a list along
        with their provenance.
        """
        bg_root: list[BandGap] = []
        if band_gaps := traverse_reversed(self.entry_archive, path):
            for bg in band_gaps:
                bg_results = BandGap()
                bg_results.index = bg.index
                bg_results.value = bg.value
                bg_results.type = bg.type
                bg_results.energy_highest_occupied = bg.energy_highest_occupied
                bg_results.energy_lowest_unoccupied = bg.energy_lowest_unoccupied
                bg_results.provenance = bg.provenance
                bg_root.insert(0, bg_results)
        return bg_root

    def resolve_band_structure(
        self, path: list[str] = ['run', 'calculation', 'band_structure_electronic']
    ) -> list[BandStructureElectronic]:
        """Returns a new section containing an electronic band structure. In
        the case of multiple valid band structures, only the latest one is
        considered.

        Band structure is reported only under the following conditions:
            - There is a non-empty array of kpoints.
            - There is a non-empty array of energies.
        """
        bs_root: list[BandStructureElectronic] = []
        if band_structures := traverse_reversed(self.entry_archive, path):
            for bs in band_structures:
                if not bs.segment:
                    continue
                valid = True
                for segment in bs.segment:
                    energies = segment.energies
                    k_points = segment.kpoints
                    if not valid_array(energies) or not valid_array(k_points):
                        valid = False
                        break
                if valid:
                    # Fill band structure data to the newer, improved data layout
                    bs_results = BandStructureElectronic()
                    bs_results.reciprocal_cell = bs
                    bs_results.segment = bs.segment
                    bs_results.spin_polarized = (
                        bs_results.segment[0].energies.shape[0] > 1
                    )
                    bs_results.energy_fermi = bs.energy_fermi

                    for info in bs.band_gap:
                        info_new = BandGapDeprecated().m_from_dict(info.m_to_dict())
                        bs_results.m_add_sub_section(
                            BandStructureElectronic.band_gap, info_new
                        )
                    bs_root.insert(0, bs_results)
        return bs_root

    def resolve_dos_deprecated(
        self, path: list[str] = ['run', 'calculation', 'dos_electronic']
    ) -> list[DOSElectronic]:
        """Returns a reference to the section containing an electronic dos. In
        the case of multiple valid DOSes, only the latest one is reported.

        DOS is reported only under the following conditions:
            - There is a non-empty array of dos_values_normalized.
            - There is a non-empty array of dos_energies.

        NOTE: this function will be eventually deprecated. This is because DOSElectronic refers
        to an old schema which will be deleted. The new function `resolve_dos` should be the
        one which persists over time.
        """
        dos_sections = extract_section(self.entry_archive, path, full_list=True)
        # The old mapping does not work for the new spin-polarized schema
        if (
            not dos_sections or len(dos_sections) == 2
        ):  # ? shouldn't len(dos_sections) < 2 to pass
            return []
        dos = dos_sections[0]
        energies = dos.energies
        values = np.array([d.value.magnitude for d in dos.total])
        dos_results = None
        if valid_array(energies) and valid_array(values):
            dos_results = DOSElectronic()
            dos_results.energies = dos
            dos_results.total = dos.total
            dos_results.energy_fermi = dos.energy_fermi
        return [dos_results] if dos_results else []

    def resolve_dos(
        self, path: list[str] = ['run', 'calculation', 'dos_electronic']
    ) -> list[DOSElectronicNew]:
        """Returns a section containing the references for an electronic DOS. This section
        is then stored under `archive.results.properties.electronic.dos_electronic_new`.

        If the calculation is spin-polarized, inside this new section there is a list `data` of
        length 2 and a boolean `spin_polarized` set to true. It also reference the species-,
        atom-, and orbital-projected DOS, if these are present.

        This section is populated only when there are non-empty arrays for energies and DOS.total values.

        Args:
            path (list[str]): the path to the dos_electronic section to be extracted from the
                self.entry_archive.

        Returns:
            List[DOSElectronicNew]: the mapped DOS.
        """
        dos_result = None  # only instantiate `dos_results` if the tests below pass
        if dos_sections := extract_section(self.entry_archive, path, full_list=True):
            for dos_section in dos_sections:
                energies = dos_section.energies
                values = np.array([d.value.magnitude for d in dos_section.total])
                if valid_array(energies) and valid_array(values):
                    dos_result = DOSElectronicNew() if not dos_result else dos_result
                    dos_data = dos_result.m_create(DOSNew)
                    dos_data.energies = dos_section
                    dos_data.total = dos_section.total[-1]
                    dos_data.energy_fermi = dos_section.energy_fermi
                    dos_data.energy_ref = dos_section.energy_ref
                    # Storing deprecated BandGap info
                    for info in dos_section.band_gap:
                        info_new = BandGapDeprecated().m_from_dict(info.m_to_dict())
                        dos_data.m_add_sub_section(DOSNew.band_gap, info_new)
                    # Spin-polarized
                    dos_result.spin_polarized = len(dos_sections) == 2
                    dos_data.spin_channel = dos_section.spin_channel
                    # Projected DOS
                    has_projected = False
                    _projected_sections = {
                        key: value
                        for key, value in dos_section.m_def.all_sub_sections.items()
                        if 'projected' in key
                    }
                    _projected_data = {
                        key: value
                        for key, value in dos_data.m_def.all_quantities.items()
                        if 'projected' in key
                    }
                    for key, value in _projected_sections.items():
                        dos_projected = dos_section.m_get(value)
                        if dos_projected is not None and len(dos_projected) > 0:
                            dos_data.m_set(_projected_data.get(key), dos_projected)
                            has_projected = True
                    dos_result.has_projected = has_projected
        return [dos_result] if dos_result else []

    def resolve_greens_functions(
        self, path: list[str] = ['run', 'calculation', 'greens_functions']
    ) -> list[GreensFunctionsElectronic]:
        """Returns a section containing the references of the electronic Greens functions.
        This section is then stored under `archive.results.properties.electronic`.

        This section is only populated if there are non-zero values of the tau, Matsubara_freq,
        or frequencies, and its respective greens_function quantities.

        Args:
            path (list[str]): the path to the dos_electronic section to be extracted from the
                self.entry_archive.

        Returns:
            List[GreensFunctionsElectronic]: the mapped Greens functions.
        """
        gfs_root: list[GreensFunctionsElectronic] = []
        if greens_functions := traverse_reversed(self.entry_archive, path):
            for gfs in greens_functions:
                gfs_results = GreensFunctionsElectronic()
                # tau-axes quantities
                tau = gfs.tau
                if valid_array(tau):
                    gfs_results.tau = gfs
                    gfs_results.greens_function_tau = (
                        gfs if valid_array(gfs.greens_function_tau) else None
                    )
                # matsubara_freq-axes quantities
                matsubara_freq = gfs.matsubara_freq
                if valid_array(matsubara_freq):
                    gfs_results.matsubara_freq = gfs
                    gfs_results.greens_function_iw = (
                        gfs if valid_array(gfs.greens_function_iw) else None
                    )
                    gfs_results.self_energy_iw = (
                        gfs if valid_array(gfs.self_energy_iw) else None
                    )
                # frequencies-axes quantities
                frequencies = gfs.frequencies
                if valid_array(frequencies):
                    gfs_results.frequencies = gfs
                    gfs_results.greens_function_freq = (
                        gfs if valid_array(gfs.greens_function_freq) else None
                    )
                    gfs_results.self_energy_freq = (
                        gfs if valid_array(gfs.self_energy_freq) else None
                    )
                    gfs_results.hybridization_function_freq = (
                        gfs if valid_array(gfs.hybridization_function_freq) else None
                    )
                # Other GFs quantities
                gfs_results.orbital_occupations = (
                    gfs if valid_array(gfs.orbital_occupations) else None
                )
                gfs_results.quasiparticle_weights = (
                    gfs if valid_array(gfs.quasiparticle_weights) else None
                )
                if gfs.chemical_potential:
                    gfs_results.chemical_potential = gfs
                gfs_results.type = gfs.type if gfs.type else None
                gfs_root.append(gfs_results)
        return gfs_root

    def fetch_charge_density(
        self, path: list[str] = ['run', 'calculation', 'density_charge', 'value_hdf5']
    ) -> list[DensityCharge]:
        return_list: list[DensityCharge] = []
        if runschema and (
            hdf5_wrappers := list(traverse_reversed(self.entry_archive, path))
        ):
            for hdf5_wrapper in hdf5_wrappers:
                d = DensityCharge()
                d.m_set('value_hdf5', hdf5_wrapper.path)
                return_list.append(d)
        return return_list

    def resolve_electric_field_gradient(
        self, path: list[str] = ['run', 'calculation', 'electric_field_gradient']
    ) -> list[ElectricFieldGradient]:
        """Returns a section containing the references for the Electric Field Gradient.
        This section is then stored under `archive.results.properties.electronic`.

        This section is populated only when there is a non empty array of
        `electric_field_gradient.value`.

        Args:
            path (list[str]): the path to the electric field gradient section to be extracted
            from the self.entry_archive.

        Returns:
            list[ElectricFieldGradient]: the mapped Electric Field Gradient.
        """
        mapped_data: list[ElectricFieldGradient] = []
        if stored_data := traverse_reversed(self.entry_archive, path):
            for data in stored_data:
                contribution = data.contribution
                value = data.value
                if valid_array(value):
                    results_data = ElectricFieldGradient(
                        contribution=contribution, value=data
                    )
                    mapped_data.insert(0, results_data)
        return mapped_data

    def resolve_spectra(self, path: list[str]) -> list[Spectra] | None:
        """Returns a section containing the references for a Spectra. This section is then
        stored under `archive.results.properties.spectroscopic`.

        This section is populated only when there are non-empty arrays for energies and intensities.

        Args:
            path (list[str]): the path to the spectra section to be extracted from the
                self.entry_archive.

        Returns:
            list[Spectra]: the mapped Spectra.
        """
        spectra = traverse_reversed(self.entry_archive, path)
        if not spectra:
            return None
        spectra_root: list[Spectra] = []
        for spectrum in spectra:
            n_energies = spectrum.n_energies
            if n_energies and n_energies > 0:
                spectra_results = Spectra(
                    type=spectrum.type, label='computation', n_energies=n_energies
                )
                provenance = spectra_results.m_create(SpectraProvenance)
                provenance.electronic_structure = spectrum.provenance
                energies = spectrum.excitation_energies
                intensities = spectrum.intensities
                if valid_array(energies) and valid_array(intensities):
                    spectra_results.energies = energies
                    spectra_results.intensities = intensities
                    if spectrum.intensities_units:
                        spectra_results.intensities_units = spectrum.intensities_units
                    spectra_root.insert(0, spectra_results)
        return spectra_root

    def resolve_magnetic_shielding(
        self, path: list[str]
    ) -> list[MagneticShielding] | None:
        """Returns a section containing the references for the (atomic) Magnetic Shielding.
        This section is then stored under `archive.results.properties.magnetic`.

        This section is populated only when there is a non empty array of
        `magnetic_shielding.value`.

        Args:
            path (list[str]): the path to the magnetic shielding section to be extracted
            from the self.entry_archive.

        Returns:
            List[MagneticShielding]: the mapped Magnetic Shielding.
        """
        stored_data = traverse_reversed(self.entry_archive, path)
        if not stored_data:
            return None
        mapped_data: list[MagneticShielding] = []
        for data in stored_data:
            value = data.value
            if valid_array(value):
                results_data = MagneticShielding(value=data)
                mapped_data.insert(0, results_data)
        return mapped_data

    def resolve_spin_spin_coupling(
        self, path: list[str]
    ) -> list[SpinSpinCoupling] | None:
        """Returns a section containing the references for the Spin Spin Coupling.
        This section is then stored under `archive.results.properties.magnetic`.

        This section is populated only when there is a non empty array of
        `spin_spin_coupling.value`.

        Args:
            path (list[str]): the path to the spin-spin coupling section to be extracted
            from the self.entry_archive.

        Returns:
            list[SpinSpinCoupling]: the mapped Spin Spin Coupling.
        """
        stored_data = traverse_reversed(self.entry_archive, path)
        if not stored_data:
            return None
        mapped_data: list[SpinSpinCoupling] = []
        for data in stored_data:
            contribution = data.contribution
            value = data.value
            reduced_value = data.reduced_value
            if valid_array(value) or valid_array(reduced_value):
                results_data = SpinSpinCoupling(
                    source='simulation',
                    contribution=contribution,
                    value=data if valid_array(value) else None,
                    reduced_value=data if valid_array(reduced_value) else None,
                )
                mapped_data.insert(0, results_data)
        return mapped_data

    def resolve_magnetic_susceptibility(
        self, path: list[str]
    ) -> list[MagneticSusceptibility] | None:
        """Returns a section containing the references for the Magnetic Susceptibility.
        This section is then stored under `archive.results.properties.magnetic`.

        This section is populated only when there is a non empty array of
        `magnetic_susceptibility.value`.

        Args:
            path (list[str]): the path to the magnetic susceptibility section to be extracted
            from the self.entry_archive.

        Returns:
            list[MagneticSusceptibility]: the mapped Magnetic Susceptibility.
        """
        stored_data = traverse_reversed(self.entry_archive, path)
        if not stored_data:
            return None
        mapped_data: list[MagneticSusceptibility] = []
        for data in stored_data:
            scale_dimension = data.scale_dimension
            value = data.value
            if valid_array(value):
                results_data = MagneticSusceptibility(
                    source='simulation', scale_dimension=scale_dimension, value=data
                )
                mapped_data.insert(0, results_data)
        return mapped_data

    def _resolve_workflow_gs_properties(
        self, methods: list[str], properties: list[str]
    ) -> None:
        """Resolves the ground state (gs) properties passed as a list `properties` (band_gap,
        band_structure, dos) for a given list of `methods` (dft, gw, tb, maxent).

        Args:
            methods (list[str]): the list of methods from which the properties are resolved.
            properties (list[str]): the list of properties to be resolved from `workflow2.results`.
        """
        properties_map = {
            'dos': 'dos_electronic_new',
            'band_structure': 'band_structure_electronic',
        }
        for method in methods:
            name = (
                'MaxEnt'
                if method == 'maxent'
                else 'FirstPrinciples'
                if method == 'first_principles'
                else method.upper()
            )
            for prop in properties:
                property_list = self.electronic_properties.get(
                    properties_map.get(prop, prop)
                )
                method_property_resolved = getattr(self, f'resolve_{prop}')(
                    ['workflow2', 'results', f'{method}_outputs', prop]
                )
                for item in method_property_resolved:
                    item.label = name
                    property_list.append(item)

    def get_gw_workflow_properties(self) -> None:
        """Gets the GW workflow (DFT+GW) properties and stores them in the self.electronic_properties
        dictionary.
        """
        properties = ['band_gap', 'band_structure', 'dos']
        methods = ['dft', 'gw']
        self._resolve_workflow_gs_properties(methods, properties)

    def get_tb_workflow_properties(self):
        """Gets the TB workflow (DFT+TB or GW+TB) properties and stores them in the self.electronic_properties
        dictionary.
        """
        properties = ['band_gap', 'band_structure', 'dos']
        methods = ['first_principles', 'tb']
        self._resolve_workflow_gs_properties(methods, properties)

    def get_dmft_workflow_properties(self) -> None:
        """Gets the DMFT workflow (DFT+TB+DMFT) properties and stores them in the
        self.electronic_properties dictionary.
        """
        properties = ['band_gap', 'band_structure', 'dos']
        methods = ['dft', 'tb']
        self._resolve_workflow_gs_properties(methods, properties)
        # Resolving DMFT Greens functions
        gfs_electronic: list[GreensFunctionsElectronic] = (
            self.electronic_properties.get('greens_functions_electronic')  # type: ignore
        )
        gfs_electronic_dmft = self.resolve_greens_functions(
            ['workflow2', 'results', 'dmft_outputs', 'greens_functions']
        )
        for item in gfs_electronic_dmft:
            item.label = 'DMFT'
            gfs_electronic.append(item)

    def get_maxent_workflow_properties(self) -> None:
        """Gets the MaxEnt workflow (DMFT+MaxEnt) properties and stores them in the self.electronic_properties
        dictionary.
        """
        properties = ['band_gap', 'dos']
        methods = ['maxent']
        self._resolve_workflow_gs_properties(methods, properties)
        # Resolving DMFT Greens functions
        gfs_electronic: list[GreensFunctionsElectronic] = (
            self.electronic_properties.get('greens_functions_electronic')  # type: ignore
        )
        for method in ['dmft', 'maxent']:
            name = 'MaxEnt' if method == 'maxent' else method.upper()
            gfs = self.resolve_greens_functions(
                [
                    'workflow2',
                    'results',
                    f'{method}_outputs',
                    'greens_functions',
                ]
            )
            for item in gfs:
                item.label = name
                gfs_electronic.append(item)

    def get_xs_workflow_properties(self, spectra: list[Spectra]) -> list[Spectra]:
        """Gets the XS workflow (DFT+GW+BSE) workflow properties and stores them in self.electronic_properties
        and in spectra. Then it returns the new Spectra section with the resolved data

        Args:
            spectra (Union[list[Spectra], None]): the input Spectra section resolved from
                `archive.run`.

        Returns:
            Union[List[Spectra], None]: the mapped Spectra from `workflow2.results`.
        """  # ! TODO: double-check typing
        properties = ['band_gap', 'band_structure', 'dos']
        methods = ['dft', 'gw']
        self._resolve_workflow_gs_properties(methods, properties)
        spct_electronic = spectra
        spectra = self.resolve_spectra(
            ['workflow2', 'results', 'spectra', 'spectrum_polarization']
        )
        if spectra:
            spct_electronic = spectra
        return spct_electronic

    def band_structure_phonon(self) -> BandStructurePhonon | None:
        """Returns a new section containing a phonon band structure. In
         the case of multiple valid band structures, only the latest one is
         considered.

        Band structure is reported only under the following conditions:
           - There is a non-empty array of kpoints.
           - There is a non-empty array of energies.
        """
        path = ['run', 'calculation', 'band_structure_phonon']
        for bs in traverse_reversed(self.entry_archive, path):
            if not bs.segment:
                continue
            valid = True
            for segment in bs.segment:
                energies = segment.energies
                k_points = segment.kpoints
                if not valid_array(energies) or not valid_array(k_points):
                    valid = False
                    break
            if valid:
                # Fill band structure data to the newer, improved data layout
                bs_new = BandStructurePhonon()
                bs_new.segment = bs.segment
                return bs_new

        return None

    def dos_phonon(self) -> DOSPhonon | None:
        """Returns a section containing phonon dos data. In the case of
         multiple valid data sources, only the latest one is reported.

        DOS is reported only under the following conditions:
           - There is a non-empty array of values.
           - There is a non-empty array of energies.
        """
        path = ['run', 'calculation', 'dos_phonon']
        for dos in traverse_reversed(self.entry_archive, path):
            energies = dos.energies
            values = np.array([d.value.magnitude for d in dos.total])
            if valid_array(energies) and valid_array(values):
                dos_new = DOSPhonon()
                dos_new.energies = dos
                dos_new.total = dos.total
                return dos_new

        return None

    def energy_free_helmholtz(self) -> EnergyFreeHelmholtz | None:
        """Returns a section Helmholtz free energy data. In the case of
         multiple valid data sources, only the latest one is reported.

        Helmholtz free energy is reported only under the following conditions:
           - There is a non-empty array of temperatures.
           - There is a non-empty array of energies.
        """
        workflow = self.entry_archive.workflow2
        if workflow is None or not hasattr(workflow, 'results'):
            return None
        if not workflow.results or not hasattr(workflow.results, 'temperature'):
            return None

        path = ['workflow2', 'results']

        for thermo_prop in traverse_reversed(self.entry_archive, path):
            temperatures = thermo_prop.temperature
            energies = thermo_prop.vibrational_free_energy_at_constant_volume
            if valid_array(temperatures) and valid_array(energies):
                energy_free = EnergyFreeHelmholtz()
                energy_free.energies = thermo_prop
                energy_free.temperatures = thermo_prop
                return energy_free

        return None

    def heat_capacity_constant_volume(self) -> HeatCapacityConstantVolume | None:
        """Returns a section containing heat capacity data. In the case of
         multiple valid data sources, only the latest one is reported.

        Heat capacity is reported only under the following conditions:
           - There is a non-empty array of temperatures.
           - There is a non-empty array of energies.
        """
        workflow = self.entry_archive.workflow2
        if workflow is None or not hasattr(workflow, 'results'):
            return None
        if not workflow.results or not hasattr(workflow.results, 'temperature'):
            return None

        path = ['workflow2', 'results']
        for thermo_prop in traverse_reversed(self.entry_archive, path):
            temperatures = thermo_prop.temperature
            heat_capacities = thermo_prop.heat_capacity_c_v
            if valid_array(temperatures) and valid_array(heat_capacities):
                heat_cap = HeatCapacityConstantVolume()
                heat_cap.heat_capacities = thermo_prop
                heat_cap.temperatures = thermo_prop
                return heat_cap

        return None

    def geometry_optimization(self) -> GeometryOptimization | None:
        """Populates both geometry optimization methodology and calculated
        properties based on the first found geometry optimization workflow.
        """
        path = ['workflow2']
        for workflow in traverse_reversed(self.entry_archive, path):
            # Check validity
            if workflow.m_def.name == 'GeometryOptimization':
                if not is_valid_workflow(workflow):
                    continue

                geo_opt = GeometryOptimization()
                if workflow.results:
                    geo_opt.trajectory = workflow.results.calculations_ref
                    if workflow.results.calculation_result_ref:
                        geo_opt.system_optimized = (
                            workflow.results.calculation_result_ref.system_ref
                        )
                    geo_opt.energies = workflow.results
                    geo_opt.final_energy_difference = (
                        workflow.results.final_energy_difference
                    )
                    geo_opt.final_force_maximum = workflow.results.final_force_maximum
                    geo_opt.final_displacement_maximum = (
                        workflow.results.final_displacement_maximum
                    )
                if workflow.method is not None:
                    geo_opt.type = workflow.method.type
                    geo_opt.convergence_tolerance_energy_difference = (
                        workflow.method.convergence_tolerance_energy_difference
                    )
                    geo_opt.convergence_tolerance_force_maximum = (
                        workflow.method.convergence_tolerance_force_maximum
                    )
                return geo_opt

        return None

    def get_md_provenance(self, workflow: Workflow) -> MolecularDynamics | None:
        """Retrieves the MD provenance from the given workflow."""
        md = None
        if workflow.m_def.name == 'MolecularDynamics':
            try:
                md = MolecularDynamics()
                md.time_step = workflow.method.integration_timestep
                md.ensemble_type = workflow.method.thermodynamic_ensemble
            except Exception:
                pass
        return md

    def trajectory(self) -> list[Trajectory]:
        """Returns a list of trajectories."""
        path = ['workflow2']
        trajs = []
        for workflow in traverse_reversed(self.entry_archive, path):
            # Check validity
            if workflow.m_def.name == 'MolecularDynamics':
                if not is_valid_workflow(workflow):
                    continue

                traj = Trajectory()
                md = self.get_md_provenance(workflow)
                if md:
                    traj.provenance = MDProvenance(molecular_dynamics=md)

                # Loop through calculations, gather thermodynamics directly
                # from each step in the workflow.
                volume = []
                volume_time = []
                pressure = []
                pressure_time = []
                temperature = []
                temperature_time = []
                potential_energy = []
                potential_energy_time = []

                calculations_ref = []
                if workflow.results and workflow.results.calculations_ref:
                    calculations_ref = workflow.results.calculations_ref
                for calc in calculations_ref:
                    time = calc.time
                    if time is not None:
                        time = time.magnitude
                        if calc.volume is not None:
                            volume.append(calc.volume.magnitude)
                            volume_time.append(time)
                        if calc.pressure is not None:
                            pressure.append(calc.pressure.magnitude)
                            pressure_time.append(time)
                        if calc.temperature is not None:
                            temperature.append(calc.temperature.magnitude)
                            temperature_time.append(time)
                        if calc.energy:
                            if calc.energy.potential is not None:
                                potential_energy.append(
                                    calc.energy.potential.value.magnitude
                                )
                                potential_energy_time.append(time)

                available_properties = []
                if volume:
                    traj.volume = VolumeDynamic(value=volume, time=volume_time)
                    available_properties.append('volume')
                if pressure:
                    traj.pressure = PressureDynamic(value=pressure, time=pressure_time)
                    available_properties.append('pressure')
                if temperature:
                    traj.temperature = TemperatureDynamic(
                        value=temperature, time=temperature_time
                    )
                    available_properties.append('temperature')
                if potential_energy:
                    traj.energy_potential = EnergyDynamic(
                        value=potential_energy, time=potential_energy_time
                    )
                    available_properties.append('energy_potential')
                if available_properties:
                    traj.available_properties = available_properties
                trajs.append(traj)
        return trajs

    def rdf(self) -> list[RadialDistributionFunction]:
        """Returns a list of radial distribution functions."""
        workflow = self.entry_archive.workflow2
        if not is_valid_workflow(workflow):
            return []

        if workflow is None or workflow.m_def.name != 'MolecularDynamics':
            return None

        path = ['workflow2', 'results', 'radial_distribution_functions']
        rdfs = []
        for rdf_workflow in traverse_reversed(self.entry_archive, path):
            rdf_values = rdf_workflow.radial_distribution_function_values
            if rdf_values is not None:
                for rdf_value in rdf_values or []:
                    rdf = RadialDistributionFunction()
                    try:
                        rdf.bins = rdf_value.bins
                        rdf.n_bins = rdf_value.n_bins
                        rdf.value = rdf_value.value
                        rdf.label = rdf_value.label
                        rdf.frame_start = rdf_value.frame_start
                        rdf.frame_end = rdf_value.frame_end
                        rdf.type = rdf_workflow.type
                        md = self.get_md_provenance(
                            rdf_workflow.m_parent.m_parent.m_parent
                        )
                        if md:
                            rdf.provenance = MDProvenance(molecular_dynamics=md)
                    except Exception as e:
                        self.logger.error(
                            'error in resolving radial distribution data', exc_info=e
                        )
                    else:
                        rdfs.append(rdf)

        return rdfs

    def rg(self) -> list[RadiusOfGyration]:
        """Returns a list of Radius of gyration trajectories."""
        path_workflow = ['workflow2']
        rgs: list[RadiusOfGyration] = []
        for workflow in traverse_reversed(self.entry_archive, path_workflow):
            # Check validity
            if not is_valid_workflow(workflow):
                continue
            if workflow.m_def.name == 'MolecularDynamics' and workflow.results:
                results = workflow.results
                md = self.get_md_provenance(workflow)
                if (
                    results.calculations_ref
                    and results.calculations_ref[0].radius_of_gyration
                ):
                    for rg_index, rg in enumerate(
                        results.calculations_ref[0].radius_of_gyration
                    ):
                        for rg_values_index, __ in enumerate(
                            rg.radius_of_gyration_values
                        ):
                            rg_results = RadiusOfGyration()
                            rg_value = []
                            rg_time = []
                            if md:
                                rg_results.provenance = MDProvenance(
                                    molecular_dynamics=md
                                )
                            for calc in results.calculations_ref:
                                if not calc.system_ref:
                                    continue
                                sec_rg = calc.radius_of_gyration[rg_index]
                                rg_results.kind = sec_rg.kind
                                time = calc.time
                                if time is not None:
                                    time = time.magnitude
                                sec_rg_values = sec_rg.radius_of_gyration_values[
                                    rg_values_index
                                ]
                                rg_results.label = sec_rg_values.label
                                rg_results.atomsgroup_ref = sec_rg_values.atomsgroup_ref
                                rg_time.append(time)
                                rg_value.append(sec_rg_values.value.magnitude)
                            rg_results.time = rg_time
                            rg_results.value = rg_value
                    rgs.append(rg_results)
        return rgs

    def msd(self) -> list[MeanSquaredDisplacement]:
        """Returns a list of mean squared displacements."""
        workflow = self.entry_archive.workflow2
        if not is_valid_workflow(workflow):
            return []

        if workflow is None or workflow.m_def.name != 'MolecularDynamics':
            return None

        path = ['workflow2', 'results', 'mean_squared_displacements']
        msds = []
        for msd_workflow in traverse_reversed(self.entry_archive, path):
            msd_values = msd_workflow.mean_squared_displacement_values
            if msd_values is not None:
                for msd_value in msd_values or []:
                    msd = MeanSquaredDisplacement()
                    try:
                        msd.times = msd_value.times
                        msd.n_times = msd_value.n_times
                        msd.value = msd_value.value
                        msd.label = msd_value.label
                        msd.errors = msd_value.errors
                        msd.type = msd_workflow.type
                        msd.direction = msd_workflow.direction
                        msd.error_type = msd_workflow.error_type
                        diffusion_constant = msd_value.diffusion_constant
                        if diffusion_constant is not None:
                            msd.diffusion_constant_value = diffusion_constant.value
                            msd.diffusion_constant_error_type = (
                                diffusion_constant.error_type
                            )
                            msd.diffusion_constant_errors = (
                                diffusion_constant.errors
                                if isinstance(
                                    diffusion_constant.errors, list | np.ndarray
                                )
                                else [diffusion_constant.errors]
                            )

                        md = self.get_md_provenance(
                            msd_workflow.m_parent.m_parent.m_parent
                        )
                        if md:
                            msd.provenance = MDProvenance(molecular_dynamics=md)
                    except Exception as e:
                        self.logger.error(
                            'error in resolving mean squared displacement data',
                            exc_info=e,
                        )
                    else:
                        msds.append(msd)

        return msds

    def properties(
        self, repr_system: ArchiveSection, repr_symmetry: ArchiveSection
    ) -> tuple:
        """Returns a populated Properties subsection."""
        properties = Properties()

        # Structures
        conv_atoms = None
        wyckoff_sets = None
        spg_number = None
        if repr_system:
            original_atoms = repr_system.m_cache.get('representative_atoms')
            if original_atoms:
                structural_type = repr_system.type
                if structural_type == 'bulk':
                    conv_atoms, _, wyckoff_sets, spg_number = self.structures_bulk(
                        repr_symmetry
                    )
                elif structural_type == '2D':
                    conv_atoms, _, wyckoff_sets, spg_number = structures_2d(
                        original_atoms
                    )
                elif structural_type == '1D':
                    conv_atoms, _ = self.structures_1d(original_atoms)

        self.electronic_properties: dict[str, list[Any]] = {
            'band_gap': self.resolve_band_gap(),
            'dos_electronic': self.resolve_dos_deprecated(),
            'dos_electronic_new': self.resolve_dos(),
            'band_structure_electronic': self.resolve_band_structure(),
            'greens_functions_electronic': self.resolve_greens_functions(),
            'density_charge': self.fetch_charge_density(),
            'electric_field_gradient': self.resolve_electric_field_gradient(),
        }
        #   spectroscopic properties list
        spectra = self.resolve_spectra(['run', 'calculation', 'spectra'])
        # Resolving GW, XS workflow properties
        workflow = self.entry_archive.workflow2
        if workflow and is_valid_workflow(workflow):
            workflow_name = workflow.name if workflow.name else workflow.m_def.name
            if workflow_name == 'DFT+GW':
                self.get_gw_workflow_properties()
            elif workflow_name == 'FirstPrinciples+TB':
                self.get_tb_workflow_properties()
            elif workflow_name in ['DFT+TB+DMFT', 'DFT+DMFT', 'TB+DMFT']:
                self.get_dmft_workflow_properties()
            elif workflow_name == 'DMFT+MaxEnt':
                self.get_maxent_workflow_properties()
            elif workflow_name in ['PhotonPolarization', 'BSE']:
                spectra = self.resolve_spectra(
                    ['workflow2', 'results', 'spectrum_polarization']
                )
            elif workflow_name == 'XS':
                spectra = self.get_xs_workflow_properties(spectra)

        # check if a property in `ElectronicProperties` is present
        if any(len(value) > 0 for value in self.electronic_properties.values()):
            electronic = ElectronicProperties()
            for (
                property_name,
                electronic_property,
            ) in self.electronic_properties.items():
                if len(electronic_property) > 0:
                    for prop in electronic_property:
                        electronic.m_append(property_name, prop)
            properties.electronic = electronic

        # Spectroscopic
        if spectra:
            spectroscopic = SpectroscopicProperties()
            spectroscopic.spectra = spectra
            properties.spectroscopic = spectroscopic

        # Magnetic
        magnetic_shielding = self.resolve_magnetic_shielding(
            ['run', 'calculation', 'magnetic_shielding']
        )
        spin_spin_coupling = self.resolve_spin_spin_coupling(
            ['run', 'calculation', 'spin_spin_coupling']
        )
        magnetic_susceptibility = self.resolve_magnetic_susceptibility(
            ['run', 'calculation', 'magnetic_susceptibility']
        )
        if magnetic_shielding or spin_spin_coupling or magnetic_susceptibility:
            magnetic = MagneticProperties()
            if magnetic_shielding:
                magnetic.magnetic_shielding = magnetic_shielding
            if spin_spin_coupling:
                magnetic.spin_spin_coupling = spin_spin_coupling
            if magnetic_susceptibility:
                magnetic.magnetic_susceptibility = magnetic_susceptibility
            properties.magnetic = magnetic

        # Vibrational
        bs_phonon = self.band_structure_phonon()
        dos_phonon = self.dos_phonon()
        energy_free = self.energy_free_helmholtz()
        heat_cap = self.heat_capacity_constant_volume()
        if bs_phonon or dos_phonon or energy_free or heat_cap:
            vibrational = VibrationalProperties()
            if dos_phonon:
                vibrational.dos_phonon = dos_phonon
            if bs_phonon:
                vibrational.band_structure_phonon = bs_phonon
            if energy_free:
                vibrational.energy_free_helmholtz = energy_free
            if heat_cap:
                vibrational.heat_capacity_constant_volume = heat_cap
            properties.vibrational = vibrational

        # Mechanical
        energy_volume_curves = self.energy_volume_curves()
        bulk_modulus = self.bulk_modulus()
        shear_modulus = self.shear_modulus()
        geometry_optimization = self.geometry_optimization()
        if (
            energy_volume_curves
            or bulk_modulus
            or shear_modulus
            or geometry_optimization
        ):
            mechanical = MechanicalProperties()
            for ev in energy_volume_curves:
                mechanical.m_add_sub_section(
                    MechanicalProperties.energy_volume_curve, ev
                )
            for bm in bulk_modulus:
                mechanical.m_add_sub_section(MechanicalProperties.bulk_modulus, bm)
            for sm in shear_modulus:
                mechanical.m_add_sub_section(MechanicalProperties.shear_modulus, sm)
            properties.mechanical = mechanical

        # Geometry optimization
        properties.geometry_optimization = self.geometry_optimization()

        # Thermodynamic
        trajectory = self.trajectory()
        if trajectory:
            thermodynamic = ThermodynamicProperties()
            thermodynamic.trajectory = trajectory
            properties.thermodynamic = thermodynamic

        # Structural
        rdf = self.rdf()
        rg = self.rg()
        if rdf or rg:
            structural = StructuralProperties()
            structural.radial_distribution_function = rdf
            structural.radius_of_gyration = rg
            properties.structural = structural

        # Dynamical
        msd = self.msd()
        if msd:
            dynamical = DynamicalProperties()
            dynamical.mean_squared_displacement = msd
            properties.dynamical = dynamical

        try:
            n_calc = len(self.section_run.calculation)
        except Exception:
            n_calc = 0
        properties.n_calculations = n_calc

        return properties, conv_atoms, wyckoff_sets, spg_number

    def structures_bulk(self, repr_symmetry):
        """The symmetry of bulk structures has already been analyzed. Here we
        use the cached results.
        """
        conv_atoms = None
        prim_atoms = None
        wyckoff_sets = None
        spg_number = None
        if repr_symmetry:
            symmetry_analyzer = repr_symmetry.m_cache.get('symmetry_analyzer')
            if symmetry_analyzer:
                spg_number = symmetry_analyzer.get_space_group_number()
                conv_atoms = symmetry_analyzer.get_conventional_system()
                prim_atoms = symmetry_analyzer.get_primitive_system()

                # For some reason MatID seems to drop the periodicity, reintroduce it here.
                conv_atoms.set_pbc(True)
                prim_atoms.set_pbc(True)
                try:
                    wyckoff_sets = symmetry_analyzer.get_wyckoff_sets_conventional(
                        return_parameters=True
                    )
                except Exception:
                    self.logger.error('Error resolving Wyckoff sets.')
                    wyckoff_sets = []

        return conv_atoms, prim_atoms, wyckoff_sets, spg_number

    def structures_1d(self, original_atoms):
        conv_atoms = None
        prim_atoms = None
        try:
            # First get a symmetry analyzer and the primitive system
            symm_system = original_atoms.copy()
            symm_system.set_pbc(True)
            symmetry_analyzer = SymmetryAnalyzer(
                symm_system,
                config.normalize.symmetry_tolerance,
                config.normalize.flat_dim_threshold,
            )
            prim_atoms = symmetry_analyzer.get_primitive_system()
            prim_atoms.set_pbc(True)

            # Get dimension of system by also taking into account the covalent radii
            dimensions = matid.geometry.get_dimensions(prim_atoms, [True, True, True])
            basis_dimensions = np.linalg.norm(prim_atoms.get_cell(), axis=1)
            gaps = basis_dimensions - dimensions
            periodicity = gaps <= config.normalize.cluster_threshold

            # If one axis is not periodic, return. This only happens if the vacuum
            # gap is not aligned with a cell vector.
            if sum(periodicity) != 1:
                self.logger.warning(
                    'could not detect the periodic dimensions in a 1D system'
                )
                return conv_atoms, prim_atoms

            # Translate to center of mass
            conv_atoms = prim_atoms.copy()
            pbc_cm = matid.geometry.get_center_of_mass(prim_atoms)
            cell_center = 0.5 * np.sum(conv_atoms.get_cell(), axis=0)
            translation = cell_center - pbc_cm
            translation[periodicity] = 0
            conv_atoms.translate(translation)
            conv_atoms.wrap()
            conv_atoms.set_pbc(periodicity)

            # Reduce cell size to just fit the system in the non-periodic dimensions.
            conv_atoms = atomutils.get_minimized_structure(conv_atoms)

            # Swap the cell axes so that the periodic one is always the first
            # basis (=a)
            swap_dim = 0
            for i, periodic in enumerate(periodicity):
                if periodic:
                    periodic_dim = i
                    break
            if periodic_dim != swap_dim:
                atomutils.swap_basis(conv_atoms, periodic_dim, swap_dim)

            prim_atoms = conv_atoms
        except Exception as e:
            self.logger.error(
                'could not construct a conventional system for a 1D material',
                exc_info=e,
            )
        return conv_atoms, prim_atoms

    def energy_volume_curves(self) -> list[EnergyVolumeCurve]:
        """Returns a list containing the found EnergyVolumeCurves."""
        workflow = self.entry_archive.workflow2
        ev_curves: list[EnergyVolumeCurve] = []
        if not is_valid_workflow(workflow):
            return ev_curves

        # workflow must be equation of state
        if (
            workflow is None
            or workflow.m_def.name != 'EquationOfState'
            or workflow.results is None
        ):
            return ev_curves

        # Volumes must be present
        volumes = workflow.results.volumes
        if not valid_array(volumes):
            self.logger.warning('missing eos volumes')
            return ev_curves

        # Raw EV curve
        energies_raw = workflow.results.energies
        if valid_array(energies_raw):
            ev_curves.append(
                EnergyVolumeCurve(
                    type='raw',
                    volumes=workflow.results,
                    energies_raw=workflow.results,
                )
            )
        else:
            self.logger.warning('missing eos energies')

        # Fitted EV curves
        fits = workflow.results.eos_fit
        if not fits:
            return ev_curves
        for fit in fits:
            energies_fitted = fit.fitted_energies
            function_name = fit.function_name
            if valid_array(energies_fitted):
                ev_curves.append(
                    EnergyVolumeCurve(
                        type=function_name,
                        volumes=workflow.results,
                        energies_fit=fit,
                    )
                )

        return ev_curves

    def bulk_modulus(self) -> list[BulkModulus]:
        """Returns a list containing the found BulkModulus."""
        workflow = self.entry_archive.workflow2
        bulk_modulus: list[BulkModulus] = []
        if not is_valid_workflow(workflow):
            return bulk_modulus

        if (
            workflow is None
            or not hasattr(workflow, 'results')
            or workflow.results is None
        ):
            return bulk_modulus

        if workflow.m_def.name == 'Elastic':
            bulk_modulus_vrh = workflow.results.bulk_modulus_hill
            if bulk_modulus_vrh:
                bulk_modulus.append(
                    BulkModulus(
                        type='voigt_reuss_hill_average',
                        value=bulk_modulus_vrh,
                    )
                )
            bulk_modulus_voigt = workflow.results.bulk_modulus_voigt
            if bulk_modulus_voigt:
                bulk_modulus.append(
                    BulkModulus(
                        type='voigt_average',
                        value=bulk_modulus_voigt,
                    )
                )
            bulk_modulus_reuss = workflow.results.bulk_modulus_reuss
            if bulk_modulus_reuss:
                bulk_modulus.append(
                    BulkModulus(
                        type='reuss_average',
                        value=bulk_modulus_reuss,
                    )
                )

        if workflow.m_def.name == 'EquationOfState':
            fits = workflow.results.eos_fit
            if not fits:
                return bulk_modulus

            for fit in fits:
                modulus = fit.bulk_modulus
                function_name = fit.function_name
                if modulus is not None and function_name:
                    bulk_modulus.append(
                        BulkModulus(
                            type=function_name,
                            value=modulus,
                        )
                    )
                else:
                    self.logger.warning(
                        'missing eos fitted energies and/or function name'
                    )

        return bulk_modulus

    def shear_modulus(self) -> list[ShearModulus]:
        """Returns a list containing the found ShearModulus."""
        workflow = self.entry_archive.workflow2
        shear_modulus: list[ShearModulus] = []
        if not is_valid_workflow(workflow):
            return shear_modulus

        if (
            workflow is None
            or not hasattr(workflow, 'results')
            or workflow.results is None
        ):
            return shear_modulus

        if workflow.m_def.name != 'Elastic':
            return shear_modulus

        shear_modulus_vrh = workflow.results.shear_modulus_hill
        if shear_modulus_vrh:
            shear_modulus.append(
                ShearModulus(
                    type='voigt_reuss_hill_average',
                    value=shear_modulus_vrh,
                )
            )
        shear_modulus_voigt = workflow.results.shear_modulus_voigt
        if shear_modulus_voigt:
            shear_modulus.append(
                ShearModulus(
                    type='voigt_average',
                    value=shear_modulus_voigt,
                )
            )
        shear_modulus_reuss = workflow.results.shear_modulus_reuss
        if shear_modulus_reuss:
            shear_modulus.append(
                ShearModulus(
                    type='reuss_average',
                    value=shear_modulus_reuss,
                )
            )

        return shear_modulus
