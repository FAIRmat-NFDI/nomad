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

import re
from abc import ABC, abstractmethod
from collections import OrderedDict

import numpy as np
from ase.dft.kpoints import get_monkhorst_pack_size_and_offset, monkhorst_pack

from nomad.config import config
from nomad.datamodel import ArchiveSection, EntryArchive
from nomad.datamodel.results import (
    BSE,
    DFT,
    DMFT,
    GW,
    TB,
    HubbardKanamoriModel,
    Material,
    Method,
    Precision,
    Simulation,
    xc_treatments,
    xc_treatments_extended,
)
from nomad.metainfo import MSection, Section
from nomad.metainfo.data_type import Number
from nomad.units import ureg
from nomad.utils import RestrictedDict


class MethodNormalizer:  # TODO: add normalizer for atom_parameters.label
    def __init__(
        self,
        entry_archive: EntryArchive,
        repr_system: MSection,
        material: Material,
        logger,
    ):
        self.entry_archive = entry_archive
        self.repr_system = repr_system
        self.repr_method = None
        self.material = material
        self.method_name = config.services.unavailable_value
        self.run = self.entry_archive.run[0]
        self.logger = logger

    def method(self) -> Method:
        """Returns the populated results.method section."""
        method = Method()
        repr_method = None
        method_name = config.services.unavailable_value
        methods = self.run.method
        n_methods = len(methods)
        functional_long_name = ''
        settings_basis_set = RestrictedDict(
            mandatory_keys=[None], optional_keys=[None], forbidden_values=[None]
        )
        gs_method = None
        xs_method = None
        method_def = {
            value.sub_section.name: value
            for _, value in Simulation.m_def.all_sub_sections.items()
        }

        def get_method_name(section_method):
            method_name = config.services.unavailable_value
            if section_method.label:
                return section_method.label
            if section_method.electronic and section_method.electronic.method:
                method_name = section_method.electronic.method
            else:
                for method in ['gw', 'tb', 'dmft', 'core_hole', 'bse']:
                    if section_method.m_xpath(method):
                        method_name = getattr(section_method, method).m_def.name
                        break
            return method_name

        def functional_long_name_from_method():
            """
            Long form of exchange-correlation functional, list of components and parameters
            as a string: see https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-meta-info/wikis/metainfo/XC-functional
            """
            if not self.repr_method or not methods:
                return None

            linked_methods = [self.repr_method]
            xc_functional = config.services.unavailable_value
            for method in linked_methods:
                sec_xc_functionals = None
                if method.dft:
                    sec_xc_functionals = method.dft.xc_functional

                if sec_xc_functionals:
                    components = {}
                    for functional in [
                        'exchange',
                        'correlation',
                        'hybrid',
                        'contributions',
                    ]:
                        for component in sec_xc_functionals[functional]:
                            try:
                                cname = component.name
                            except KeyError:
                                pass
                            else:
                                this_component = ''
                                if component.weight is not None:
                                    this_component = str(component.weight) + '*'
                                this_component += cname
                                components[cname] = this_component
                        result_array = []
                        for name in sorted(components):
                            result_array.append(components[name])
                        if len(result_array) >= 1:
                            xc_functional = '+'.join(result_array)
                    if method.dft and method.dft.xc_functional.name is None:
                        method.dft.xc_functional.name = xc_functional
            return xc_functional

        def get_basis_set() -> RestrictedDict:
            """
            Decide which type of basis set settings are applicable to the entry and
            return a corresponding settings as a RestrictedDict.

            Returns:
                RestrictedDict or None: Returns the extracted settings as a
                RestrictedDict. If no suitable basis set settings could be identified,
                returns None.
            """
            settings: MethodNormalizerBasisSet = None
            program_name = None
            if len(self.entry_archive.run) > 0 and self.run.program:
                program_name = self.run.program.name

            if program_name == 'exciting':
                settings = BasisSetExciting(
                    self.entry_archive, self.repr_method, self.repr_system, self.logger
                )
            elif program_name == 'FHI-aims':
                settings = BasisSetFHIAims(
                    self.entry_archive, self.repr_method, self.repr_system, self.logger
                )
            else:
                return None

            return settings.to_dict()

        # workflow_name
        if self.entry_archive.workflow2:
            workflow_name = (
                self.entry_archive.workflow2.name
                if self.entry_archive.workflow2.name
                else self.entry_archive.workflow2.m_def.name
            )
            method.workflow_name = workflow_name
        # if the entry is a GW or XS workflow, keep method_name as DFT+XS

        from .results import is_valid_workflow

        if not is_valid_workflow(self.entry_archive.workflow2):
            pass

        elif method.workflow_name in ['DFT+GW', 'XS']:
            gs_task = self.entry_archive.workflow2.tasks[0]  # Ground-state task
            xs_task = self.entry_archive.workflow2.tasks[
                -1
            ]  # Excited-state (GW or XS) task
            # Trying to resolve the gs_method and the representative method (repr_method)
            gs_method = None
            for input in gs_task.task.tasks[-1].inputs:
                if 'method' in input.name:
                    gs_method = input.section
                    break
            if not gs_method:
                self.logger.warning(
                    'Could not resolve the DFT method section from workflow refs.'
                )
                return method
            repr_method = gs_method
            # Trying to resolve the xs_method section and the method_name
            xs_method = None
            if len(xs_task.task.inputs) > 1:
                xs_method = xs_task.task.inputs[
                    1
                ].section  # Excited-state method (BSE, XS cases)
            else:  # Special GW workflow case
                for input in xs_task.task.tasks[-1].inputs:
                    if 'method' in input.name:
                        xs_method = input.section
                        break
            if not xs_method:
                self.logger.warning(
                    'Could not resolve the excited-state method section (GW, BSE) from workflow refs.'
                )
                return method
            # Finding the proper workflow method_name
            if xs_method.gw:
                method_name = f'{get_method_name(repr_method)}+GW'
            elif xs_method.bse:
                method_name = f'{get_method_name(repr_method)}+BSE'
        elif method.workflow_name in ['DFT+TB+DMFT', 'DFT+DMFT', 'TB+DMFT']:
            repr_method = (
                self.entry_archive.workflow2.method.dmft_method_ref
            )  # DMFT method
            repr_method = repr_method.m_parent
            method_name = method.workflow_name
        elif method.workflow_name == 'DMFT+MaxEnt':
            repr_method = (
                self.entry_archive.workflow2.method.dmft_method_ref
            )  # DMFT method
            repr_method = repr_method.m_parent
            method_name = method.workflow_name
        # if only one method is specified, use it directly
        elif n_methods == 1:
            repr_method = methods[0]
            method_name = get_method_name(repr_method)
        # if several methods have been declared, we need to find the "topmost" method and report it.
        elif n_methods > 1:
            # Method referencing another as "core_method". If core method was
            # given, create new merged method containing all the information.
            for sec_method in methods:
                core_method = sec_method.core_method_ref
                if core_method is not None:
                    if sec_method.electronic:
                        electronic = core_method.electronic
                        if not electronic:
                            electronic = sec_method.electronic.m_def.section_cls()
                            core_method.electronic = electronic
                        core_method.electronic.method = sec_method.electronic.method
                    repr_method = core_method
                    method_name = get_method_name(repr_method)

            # Perturbative methods: we report the "topmost" method (=method
            # that is referencing something but is not itself being
            # referenced).
            referenced_methods = set()
            for sec_method in methods:
                starting_method_ref = sec_method.starting_method_ref
                if starting_method_ref is not None:
                    referenced_methods.add(starting_method_ref.m_path())
            if len(referenced_methods) == n_methods - 1:
                for sec_method in methods:
                    if sec_method.m_path() not in referenced_methods:
                        method_name = get_method_name(sec_method)
                        if method_name != config.services.unavailable_value:
                            repr_method = sec_method
                            break

        self.repr_method = repr_method
        self.method_name = method_name

        # Normalize in case of a APW basis set
        try:
            sec_method = self.run.method[-1]
            compound_type: str = ''
            em_index = 0
            for electrons_representation in sec_method.electrons_representation or []:
                if electrons_representation.type not in (None, 'unavailable'):
                    continue
                for basis_set in electrons_representation.basis_set or []:
                    for orbital in basis_set.orbital or []:
                        if not compound_type:
                            compound_type = orbital.type
                        elif compound_type[:6] != '(L)APW' and orbital.type in [
                            'APW',
                            'LAPW',
                        ]:
                            compound_type = re.sub(r'L?APW', '(L)APW', compound_type)
                        elif orbital.type == 'LO' and 'lo' != compound_type[-2:]:
                            compound_type += '+lo'
                sec_method.electrons_representation[em_index].type = (
                    compound_type if compound_type else None
                )
                em_index += 1
        except (IndexError, AttributeError):
            pass

        if self.method_name in ['DFT', 'NMR']:
            functional_long_name = functional_long_name_from_method()
            settings_basis_set = get_basis_set()

        # Fill electronic method metainfo
        if self.method_name in ['DFT', 'DFT+U', 'NMR']:
            simulation = DFTMethod(
                self.logger,
                entry_archive=self.entry_archive,
                methods=methods,
                repr_method=self.repr_method,
                repr_system=self.repr_system,
                method=method,
                method_name=self.method_name,
                settings_basis_set=settings_basis_set,
                functional_long_name=functional_long_name,
            ).simulation()
        elif self.method_name in [
            'GW',
            'BSE',
            'DFT+GW',
            'DFT+BSE',
        ]:  # TODO extend for 'DFT+GW+BSE'
            simulation = ExcitedStateMethod(
                self.logger,
                entry_archive=self.entry_archive,
                methods=methods,
                repr_method=self.repr_method,
                repr_system=self.repr_system,
                method=method,
                method_def=method_def,
                method_name=self.method_name,
                settings_basis_set=settings_basis_set,
                functional_long_name=functional_long_name,
                xs_method=xs_method,
            ).simulation()
        elif (
            self.method_name == 'CoreHole'
        ):  # TODO check if this is going to be normalized to results
            simulation = Simulation()
            method.method_name = 'CoreHole'
        elif self.method_name in ['TB']:  # TODO extend for 'DFT+TB'
            simulation = TBMethod(
                self.logger,
                repr_method=self.repr_method,
                method=method,
                method_name=self.method_name,
            ).simulation()
        elif self.method_name in [
            'DMFT',
            'DFT+TB+DMFT',
            'DFT+DMFT',
            'TB+DMFT',
            'DMFT+MaxEnt',
        ]:
            simulation = DMFTMethod(
                self.logger,
                repr_method=self.repr_method,
                method=method,
                method_name=self.method_name,
            ).simulation()
        else:
            simulation = Simulation()

        # Fill meshes
        if self.run.m_xpath('method[-1].frequency_mesh'):
            for freq_mesh in self.run.method[-1].frequency_mesh:
                freq_mesh.dimensionality = (
                    1 if freq_mesh.dimensionality is None else freq_mesh.dimensionality
                )

        if self.run.m_xpath('method[-1].time_mesh'):
            for time_mesh in self.run.method[-1].time_mesh:
                time_mesh.dimensionality = (
                    1 if time_mesh.dimensionality is None else time_mesh.dimensionality
                )

        if self.run.m_xpath('method[-1].k_mesh'):
            k_mesh = self.run.method[-1].k_mesh
            k_mesh.dimensionality = (
                3 if not k_mesh.dimensionality else k_mesh.dimensionality
            )
            # Normalize k mesh from grid sampling
            if k_mesh.grid is not None:
                k_mesh.n_points = (
                    np.prod(k_mesh.grid) if not k_mesh.n_points else k_mesh.n_points
                )
                if k_mesh.sampling_method == 'Gamma-centered':
                    points = np.meshgrid(*[np.linspace(0, 1, n) for n in k_mesh.grid])
                    k_mesh.points = np.transpose(
                        np.reshape(
                            points, (len(points), np.size(points) // len(points))
                        )
                    )  # this assumes a gamma-centered grid: we really need the `sampling_method` to be sure
                elif k_mesh.sampling_method == 'Monkhorst-Pack':
                    try:
                        k_mesh.points += monkhorst_pack(k_mesh.grid)
                    except ValueError:
                        pass  # this is a quick workaround: k_mesh.grid should be symmetry reduced
        else:
            if self.run.m_xpath('calculation[-1].eigenvalues[-1].kpoints') is not None:
                k_mesh = (
                    self.run.method[-1]
                    .m_def.all_sub_sections['k_mesh']
                    .sub_section.section_cls()
                )
                self.run.method[-1].k_mesh = k_mesh
                k_mesh.points = self.run.calculation[-1].eigenvalues[-1].kpoints
                k_mesh.n_points = (
                    len(k_mesh.points) if not k_mesh.n_points else k_mesh.n_points
                )
                k_mesh.grid = [len(set(k_mesh.points[:, i])) for i in range(3)]
                if not k_mesh.sampling_method:
                    try:  # TODO double-check
                        _, k_grid_offset = get_monkhorst_pack_size_and_offset(
                            k_mesh.points.real
                        )
                        if not k_grid_offset.all():
                            k_mesh.sampling_method = 'Monkhorst-Pack'
                    except ValueError:
                        k_mesh.sampling_method = 'Gamma-centered'

        # Fill the precision section only if self.run.method exist.
        if methods:
            simulation.precision = Precision()
            k_lattices = self.run.m_xpath('system[-1].atoms.lattice_vectors_reciprocal')
            grid = self.run.m_xpath('method[-1].k_mesh.grid')
            if k_line_density := self.calc_k_line_density(k_lattices, grid):
                if not simulation.precision.k_line_density:
                    simulation.precision.k_line_density = k_line_density

            for em_index, em in enumerate(
                er := getattr(methods[-1], 'electrons_representation', [])
            ):
                if (nt := getattr(em, 'native_tier')) is not None:
                    try:
                        er[em_index].native_tier = f'{self.run.program.name} - {nt}'
                    except AttributeError:
                        pass
                if 'wavefunction' in getattr(em, 'scope', []):
                    simulation.precision.basis_set = getattr(em, 'type')
                    simulation.precision.native_tier = getattr(em, 'native_tier')
                    for bs in getattr(em, 'basis_set', []):
                        if getattr(bs, 'type') is not None:
                            # only one of either will be set
                            simulation.precision.planewave_cutoff = getattr(
                                bs, 'cutoff'
                            )
                            simulation.precision.apw_cutoff = getattr(
                                bs, 'cutoff_fractional'
                            )
                            break

        method.equation_of_state_id = self.equation_of_state_id(
            method.method_id, self.material.chemical_formula_hill
        )
        simulation.program_name = self.run.program.name
        simulation.program_version = self.run.program.version
        simulation.program_version_internal = self.run.program.version_internal
        method.simulation = simulation
        return method

    def equation_of_state_id(self, method_id: str, formula: str):
        """Creates an ID that can be used to group an equation of state
        calculation found within the same upload.
        """
        eos_dict = RestrictedDict(
            mandatory_keys=[
                'upload_id',
                'method_id',
                'formula',
            ],
            forbidden_values=[None],
        )

        # Only calculations from the same upload are grouped
        eos_dict['upload_id'] = self.entry_archive.metadata.upload_id

        # Method
        eos_dict['method_id'] = method_id

        # The formula should be same for EoS (maybe even symmetries)
        if self.material:
            eos_dict['formula'] = self.material.chemical_formula_hill

        # Form a hash from the dictionary
        try:
            eos_dict.check(recursive=True)
        except (KeyError, ValueError):
            pass
        else:
            return eos_dict.hash()

    def calc_k_line_density(
        self, k_lattices: list[list[float]], nks: list[int]
    ) -> float | None:
        """
        Compute the lowest k_line_density value:
        k_line_density (for a uniformly spaced grid) is the number of k-points per reciprocal length unit
        """
        # Check consistency of input
        try:
            if len(k_lattices) != 3 or len(nks) != 3:
                return None
        except (TypeError, ValueError):
            return None

        # Compute k_line_density
        struc_type = self.material.structural_type
        if struc_type == 'bulk':
            return min(
                [  # type: ignore
                    nk / (np.linalg.norm(k_lattice))
                    for k_lattice, nk in zip(k_lattices, nks)
                ]
            )
        else:
            return None


class ElectronicMethod(ABC):
    """Abstract base class for all the specific electronic structure methods (DFT, GW, BSE...)."""

    def __init__(
        self,
        logger,
        entry_archive: EntryArchive | None = None,
        methods: list[ArchiveSection] = [None],
        repr_method: ArchiveSection | None = None,
        repr_system: MSection | None = None,
        method: Method | None = None,
        method_def: dict = {},
        method_name: str = config.services.unavailable_value,
        settings_basis_set: RestrictedDict = RestrictedDict(
            mandatory_keys=[None], optional_keys=[None], forbidden_values=[None]
        ),
        functional_long_name: str = '',
        xs_method: ArchiveSection | None = None,
    ) -> None:
        self._logger = logger
        self._entry_archive = entry_archive
        self._methods = methods
        self._repr_method = repr_method
        self._repr_system = repr_system
        self._method = method
        self._method_def = method_def
        self._method_name = method_name
        self._settings_basis_set = settings_basis_set
        self._functional_long_name = functional_long_name
        self._xs_method = xs_method

    @abstractmethod
    def simulation(self) -> Simulation:
        """Map `run.method` into `results.simulation`"""
        pass


class DFTMethod(ElectronicMethod):
    """DFT Method normalized into results.simulation"""

    def simulation(self) -> Simulation:
        simulation = Simulation()
        self._method.method_name = 'DFT' if self._method_name != 'NMR' else 'NMR'
        dft = DFT()
        dft.basis_set_type = self.basis_set_type(self._repr_method)
        self._method.method_id = self.method_id_dft(
            self._settings_basis_set, self._functional_long_name
        )
        self._method.parameter_variation_id = self.parameter_variation_id_dft(
            self._settings_basis_set, self._functional_long_name
        )  # TODO: check whether it can be decoupled like this
        dft.core_electron_treatment = self.core_electron_treatment()
        if self._repr_method.electronic is not None:
            if self._repr_method.electronic.smearing is not None:
                dft.smearing_kind = self._repr_method.electronic.smearing.kind
                dft.smearing_width = self._repr_method.electronic.smearing.width
            if self._repr_method.electronic.n_spin_channels:
                dft.spin_polarized = bool(
                    self._repr_method.electronic.n_spin_channels > 1
                )
            dft.van_der_Waals_method = self._repr_method.electronic.van_der_waals_method
            dft.relativity_method = self._repr_method.electronic.relativity_method
        try:
            dft.xc_functional_names = self.xc_functional_names(
                self._repr_method.dft.xc_functional
            )
            dft.jacobs_ladder = self.xc_functional_type(dft.xc_functional_names)
            dft.xc_functional_type = dft.jacobs_ladder
            dft.exact_exchange_mixing_factor = self.exact_exchange_mixing_factor(
                dft.xc_functional_names
            )
        except Exception:
            self._logger.warning('Error extracting the DFT XC functional names.')
        if self._repr_method.scf is not None:
            dft.scf_threshold_energy_change = (
                self._repr_method.scf.threshold_energy_change
            )
        simulation.dft = dft
        hubbard_kanamori_models = self.hubbard_kanamori_model(self._methods)
        simulation.dft.hubbard_kanamori_model = (
            hubbard_kanamori_models if len(hubbard_kanamori_models) else None
        )
        return simulation

    def basis_set_type(self, repr_method: ArchiveSection) -> str | None:
        name = None
        for em in repr_method.electrons_representation or []:
            if em.scope:
                if 'wavefunction' in em.scope:
                    em_type = em.type
                    # Provide a mapping in case `em.type` and `basis_set_type` diverge
                    if 'APW' in em_type:
                        name = '(L)APW+lo'
                    elif em_type in ('atom-centered orbitals', 'support functions'):
                        full_stop = False
                        type_options = (
                            'gaussians',
                            'numeric AOs',
                            'psinc functions',
                            'pbeVaspFit2015',
                            'Koga',
                            'Bunge',
                        )
                        for type_option in type_options:
                            for bs in em.basis_set:
                                if bs.type == type_option:
                                    name = type_option
                                    full_stop = True
                                    break
                            if full_stop:
                                break
                    elif em_type == 'gaussians + plane waves':
                        name = 'plane waves'
                    else:
                        name = em_type
                    break
        if name:
            key = name.replace('_', '').replace('-', '').replace(' ', '').lower()
            name_mapping = {
                'realspacegrid': 'real-space grid',
                'planewaves': 'plane waves',
            }
            name = name_mapping.get(key, name)
        if self._entry_archive.m_xpath('run[0].program.name'):
            if self._entry_archive.run[0].program.name in (
                'exciting',
                'FHI-aims',
                'WIEN2k',
                'Elk',
            ):
                name = '(L)APW+lo'
        return name

    def basis_set_name(self) -> str | None:
        try:
            name = self._repr_method.basis_set[0].name
        except Exception:
            name = None
        return name

    def hubbard_kanamori_model(
        self, methods: list[ArchiveSection]
    ) -> list[HubbardKanamoriModel]:
        """Generate a list of normalized HubbardKanamoriModel for `results.method`"""
        hubbard_kanamori_models = []
        for sec_method in methods:
            for param in sec_method.atom_parameters:
                if param.hubbard_kanamori_model is not None:
                    hubb_run = param.hubbard_kanamori_model
                    if all([hubb_run.orbital, hubb_run.double_counting_correction]):
                        hubb_results = HubbardKanamoriModel()
                        hubb_results.atom_label = param.label
                        valid = False
                        for quant in hubb_run.m_def.quantities:
                            quant_value = getattr(hubb_run, quant.name)
                            # all false values, including zero are ignored
                            if quant_value:
                                setattr(hubb_results, quant.name, quant_value)
                                if isinstance(quant.type, Number):
                                    valid = True
                        # do not save if all parameters are set at 0
                        if not valid:
                            continue
                        # U_effective technically only makes sense for Dudarev
                        # but it is computed anyhow to act as a trigger for DFT+U
                        if (
                            hubb_results.u_effective is None
                            and hubb_results.u is not None
                        ):
                            hubb_results.u_effective = hubb_results.u
                            if hubb_results.j is not None:
                                hubb_results.u_effective -= hubb_results.j
                        hubbard_kanamori_models.append(hubb_results)
        return hubbard_kanamori_models

    def method_id_dft(
        self, settings_basis_set: RestrictedDict, functional_long_name: str
    ):
        """Creates a method id for DFT calculations if all required data is present."""
        method_dict = RestrictedDict(
            mandatory_keys=[
                'program_name',
                'functional_long_name',
                'settings_basis_set',
                'scf_threshold_energy_change',
            ],
            optional_keys=(
                'smearing_kind',
                'smearing_width',
                'number_of_eigenvalues_kpoints',
            ),
            forbidden_values=[None],
        )
        method_dict['program_name'] = self._entry_archive.run[0].program.name

        # Functional settings
        method_dict['functional_long_name'] = functional_long_name

        # Basis set settings
        method_dict['settings_basis_set'] = settings_basis_set

        # k-point sampling settings if present. Add number of kpoints as
        # detected from eigenvalues. TODO: we would like to have info on the
        # _reducible_ k-point-mesh:
        #    - or list of reducible k-points
        try:
            smearing_kind = self._repr_method.electronic.smearing.kind
            if smearing_kind is not None:
                method_dict['smearing_kind'] = smearing_kind
            smearing_width = self._repr_method.electronic.smearing.width
            if smearing_width is not None:
                smearing_width = f'{smearing_width:.4f}'
                method_dict['smearing_width'] = smearing_width
        except Exception:
            pass

        try:
            scc = self._entry_archive.run[0].calculation[-1]
            eigenvalues = scc.eigenvalues
            kpt = eigenvalues[-1].kpoints
        except (KeyError, IndexError):
            pass
        else:
            if kpt is not None:
                method_dict['number_of_eigenvalues_kpoints'] = str(len(kpt))

        # SCF convergence settings
        try:
            conv_thr = self._repr_method.scf.threshold_energy_change
            if conv_thr is not None:
                conv_thr = f'{conv_thr.to(ureg.rydberg).magnitude:.13f}'
                method_dict['scf_threshold_energy_change'] = conv_thr
        except Exception:
            pass

        # If all required information is present, save the hash
        try:
            method_dict.check(recursive=True)
        except (KeyError, ValueError):
            pass
        else:
            return method_dict.hash()

    def parameter_variation_id_dft(
        self, settings_basis_set: RestrictedDict, functional_long_name: str
    ):
        """Creates an ID that can be used to group calculations that differ
        only by the used DFT parameters within the same upload.
        """
        # Create ordered dictionary with the values. Order is important for
        param_dict = RestrictedDict(
            mandatory_keys=[
                'upload_id',
                'program_name',
                'program_version',
                'settings_geometry',
                'functional_long_name',
                'scf_threshold_energy_change',
            ],
            optional_keys=('atoms_pseudopotentials',),
            forbidden_values=[None],
        )

        # Only calculations from the same upload are grouped
        param_dict['upload_id'] = self._entry_archive.metadata.upload_id

        # The same code and functional type is required
        param_dict['program_name'] = self._entry_archive.run[0].program.name
        param_dict['program_version'] = self._entry_archive.run[0].program.version

        # Get a string representation of the geometry. It is included as the
        # geometry should remain the same during parameter variation. By simply
        # using the atom labels and positions we assume that their
        # order/translation/rotation does not change.
        geom_dict: OrderedDict = OrderedDict()
        try:
            atoms = self._repr_system.atoms
            atom_labels = atoms.labels
            geom_dict['atom_labels'] = ', '.join(sorted(atom_labels))
            atom_positions = atoms['positions']
            geom_dict['atom_positions'] = np.array2string(
                atom_positions.to(ureg.angstrom).magnitude,  # convert to Angstrom
                formatter={'float_kind': lambda x: f'{x:.6f}'},  # type: ignore
            ).replace('\n', '')
            cell = atoms['lattice_vectors']
            geom_dict['simulation_cell'] = np.array2string(
                cell.to(ureg.angstrom).magnitude,  # convert to Angstrom
                formatter={'float_kind': lambda x: f'{x:.6f}'},  # type: ignore
            ).replace('\n', '')
        except Exception:
            pass
        param_dict['settings_geometry'] = geom_dict

        # TODO: Add other DFT-specific properties
        # considered variations:
        #   - smearing kind/width
        #   - k point grids
        #   - basis set parameters
        # convergence threshold should be kept constant during convtest
        param_dict['functional_long_name'] = functional_long_name
        conv_thr = (
            self._repr_method.scf.threshold_energy_change
            if self._repr_method.scf is not None
            else None
        )
        if conv_thr is not None:
            conv_thr = f'{conv_thr.to(ureg.rydberg).magnitude:.13f}'
        param_dict['scf_threshold_energy_change'] = conv_thr

        # Pseudopotentials are kept constant, if applicable
        if settings_basis_set is not None:
            pseudos = settings_basis_set.get('atoms_pseudopotentials', None)
            if pseudos is not None:
                param_dict['atoms_pseudopotentials'] = pseudos

        # Form a hash from the dictionary
        try:
            param_dict.check(recursive=True)
        except (KeyError, ValueError):
            pass
        else:
            return param_dict.hash()

    def core_electron_treatment(self) -> str:
        treatment = config.services.unavailable_value
        code_name = self._entry_archive.run[0].program.name
        if code_name is not None:
            core_electron_treatments = {
                'VASP': 'pseudopotential',
                'FHI-aims': 'full all electron',
                'exciting': 'full all electron',
                'quantum espresso': 'pseudopotential',
            }
            treatment = core_electron_treatments.get(
                code_name, config.services.unavailable_value
            )
        return treatment

    def xc_functional_names(self, method_xc_functional: Section) -> list[str] | None:
        if self._repr_method:
            functionals = set()
            try:
                for functional_type in [
                    'exchange',
                    'correlation',
                    'hybrid',
                    'contributions',
                ]:
                    functionals.update(
                        [f.name for f in method_xc_functional[functional_type]]
                    )
            except Exception:
                pass
            if functionals:
                return sorted(functionals)
        return None

    def xc_functional_type(
        self,
        xc_functionals: list[str] | None,
        abbrev_mapping: dict[str, str] = xc_treatments,
    ) -> str:
        """Assign the rung on Jacob\'s Ladder based on a set of libxc names.
        The exact name mapping (libxc -> NOMAD) is set in `abbrev_mapping`."""
        # sanity check
        if not xc_functionals:
            return config.services.unavailable_value
        # local variables
        rung_order = {x: i for i, x in enumerate(abbrev_mapping.keys())}
        re_abbrev = re.compile(r'((HYB_)?[A-Z]{3})')
        # extraction rungs
        abbrevs = []
        for functional in xc_functionals:
            try:
                abbrev = re_abbrev.match(functional).group(1)
                abbrev = abbrev.lower() if abbrev == 'HYB_MGG' else abbrev[:3].lower()
                abbrevs.append(abbrev)
            except AttributeError:
                pass
        # return highest rung
        try:
            highest_rung_abbrev = max(abbrevs, key=lambda x: rung_order[x])
        except KeyError:
            return config.services.unavailable_value
        return abbrev_mapping[highest_rung_abbrev]

    def exact_exchange_mixing_factor(self, xc_functional_names: list[str]):
        """Assign the exact exchange mixing factor to `results` section when explicitly stated.
        Else, fall back on XC functional default."""

        def scan_patterns(patterns, xc_name) -> bool:
            return any(x for x in patterns if re.search('_' + x + '$', xc_name))

        if self._repr_method.dft:
            xc_functional = self._repr_method.dft.xc_functional
            for hybrid in xc_functional.hybrid:
                if hybrid.parameters:
                    if 'exact_exchange_mixing_factor' in hybrid.parameters.keys():
                        return hybrid.parameters['exact_exchange_mixing_factor']
        for xc_name in xc_functional_names:
            if not re.search('_XC?_', xc_name):
                continue
            if re.search('_B3LYP[35]?$', xc_name):
                return 0.2
            elif scan_patterns(['HSE', 'PBEH', 'PBE_MOL0', 'PBE_SOL0'], xc_name):
                return 0.25
            elif re.search('_M05$', xc_name):
                return 0.28
            elif re.search('_PBE0_13$', xc_name):
                return 1 / 3
            elif re.search('_PBE38$', xc_name):
                return 3 / 8
            elif re.search('_PBE50$', xc_name):
                return 0.5
            elif re.search('_M06_2X$', xc_name):
                return 0.54
            elif scan_patterns(['M05_2X', 'PBE_2X'], xc_name):
                return 0.56


class ExcitedStateMethod(ElectronicMethod):
    """ExcitedState (GW, BSE, or DFT+GW, DFT+BSE) Method normalized into results.simulation"""

    def simulation(self) -> Simulation:
        xs: None | GW | BSE = None
        simulation = Simulation()
        if 'GW' in self._method_name:
            self._method.method_name = 'GW'
            xs = GW()
        elif 'BSE' in self._method_name:
            self._method.method_name = 'BSE'
            xs = BSE()
        if 'DFT' in self._method_name:
            xs_type = getattr(
                self._xs_method, f'{self._method.method_name.lower()}'
            ).type
            if self._method.method_name == 'BSE':
                xs_solver = getattr(
                    self._xs_method, f'{self._method.method_name.lower()}'
                ).solver
            dft = DFTMethod(
                self._logger,
                entry_archive=self._entry_archive,
                methods=self._methods,
                repr_method=self._repr_method,
                repr_system=self._repr_system,
                method=self._method,
                method_name=self._method_name,
                settings_basis_set=self._settings_basis_set,
                functional_long_name=self._functional_long_name,
            )
            try:
                xs.starting_point_names = dft.xc_functional_names(
                    self._repr_method.dft.xc_functional
                )
                xs.starting_point_type = dft.xc_functional_type(
                    xs.starting_point_names, abbrev_mapping=xc_treatments_extended
                )
            except Exception:
                self._logger.warning('Error extracting the DFT XC functional names.')
            xs.basis_set_type = dft.basis_set_type(self._repr_method)
        else:
            xs_type = getattr(
                self._repr_method, f'{self._method.method_name.lower()}'
            ).type
            if self._method.method_name == 'BSE':
                xs_solver = getattr(
                    self._repr_method, f'{self._method.method_name.lower()}'
                ).solver
        xs.type = xs_type
        if self._method.method_name == 'BSE':
            xs.solver = xs_solver
        simulation.m_add_sub_section(self._method_def[self._method.method_name], xs)
        return simulation


class TBMethod(ElectronicMethod):
    """TB (Wannier, SlaterKoster, DFTB, xTB) Method normalized into results.simulation"""

    def simulation(self) -> Simulation:
        simulation = Simulation()
        self._method.method_name = 'TB'
        tb = TB()
        if self._repr_method.tb.name in ['Slater-Koster', 'Wannier', 'xTB', 'DFTB']:
            tb.type = self._repr_method.tb.name
        if self._repr_method.tb.wannier:
            if self._repr_method.tb.wannier.is_maximally_localized:
                tb.localization_type = 'maximally_localized'
            else:
                tb.localization_type = 'single_shot'
        simulation.tb = tb
        return simulation


class DMFTMethod(ElectronicMethod):
    """DMFT Method normalized into results.simulation"""

    def simulation(self) -> Simulation:
        simulation = Simulation()
        self._method.method_name = 'DMFT'
        dmft = DMFT()
        dmft.impurity_solver_type = self._repr_method.dmft.impurity_solver
        dmft.inverse_temperature = self._repr_method.dmft.inverse_temperature
        dmft.magnetic_state = self._repr_method.dmft.magnetic_state
        # taking U,JH values from the first atom
        hubbard_parameters = None
        if self._repr_method.m_xpath('starting_method_ref.lattice_model_hamiltonian'):
            hubbard_parameters = (
                self._repr_method.starting_method_ref.lattice_model_hamiltonian[
                    0
                ].hubbard_kanamori_model[0]
            )
        elif self._repr_method.m_xpath('starting_method_ref.atom_parameters'):
            hubbard_parameters = self._repr_method.starting_method_ref.atom_parameters[
                0
            ].hubbard_kanamori_model
        if hubbard_parameters is not None:
            dmft.u = hubbard_parameters.u
            dmft.jh = hubbard_parameters.jh
        if self._method_name == 'DMFT+MaxEnt':
            dmft.analytical_continuation = 'MaxEnt'
        simulation.dmft = dmft
        return simulation


class MethodNormalizerBasisSet(ABC):
    """Abstract base class for basis set settings. The idea is to create
    subclasses that inherit this class and hierarchically add new mandatory and
    optional settings with the setup()-function.
    """

    def __init__(self, entry_archive, repr_method, repr_system, logger):
        self._entry_archive = entry_archive
        self._repr_method = repr_method  # only this is used in FHIaims
        self._repr_system = repr_system  # only this is used in exciting
        self._logger = logger
        mandatory, optional = self.setup()
        self.settings = RestrictedDict(mandatory, optional, forbidden_values=[None])

    @abstractmethod
    def to_dict(self) -> RestrictedDict:
        """Used to extract basis set settings from the archive and returning
        them as a RestrictedDict.
        """
        pass

    @abstractmethod
    def setup(self) -> tuple:
        """Used to define a list of mandatory and optional settings for a
        subclass.

        Returns:
            Should return a tuple of two lists: the first one defining
            mandatory keys and the second one defining optional keys.
        """
        mandatory: list = []
        optional: list = []
        return mandatory, optional


class BasisSetFHIAims(MethodNormalizerBasisSet):
    """Basis set settings for FHI-Aims (code-dependent)."""

    def setup(self) -> tuple:
        # Get previously defined values from superclass
        mandatory, optional = super().setup()

        # Add new values
        mandatory += ['fhiaims_basis']

        return mandatory, optional

    def to_dict(self):
        # Get basis set settings for each species
        aims_bs = self._repr_method.x_fhi_aims_section_controlIn_basis_set
        if not aims_bs:
            try:
                aims_bs = (
                    self._repr_method.method_ref.x_fhi_aims_section_controlIn_basis_set
                )
            except Exception:
                pass
        if aims_bs is not None:
            bs_by_species = {}
            for this_aims_bs in aims_bs:
                this_bs_dict = self._values_to_dict(this_aims_bs, level=2)
                this_species = this_aims_bs['x_fhi_aims_controlIn_species_name'][0]
                bs_by_species[this_species] = this_bs_dict

            # Sort alphabetically by species label
            if bs_by_species:
                basis = OrderedDict()
                for k in sorted(bs_by_species.keys()):
                    basis[k] = bs_by_species[k]
                self.settings['fhiaims_basis'] = basis

        return self.settings

    @classmethod
    def _values_to_dict(cls, data, level=0):
        result = None
        if data is None:
            return None
        elif isinstance(data, Section | dict):
            result = OrderedDict()
            for k in sorted(cls._filtered_section_keys(data)):
                v = data.get(k, None)  # type: ignore
                result[k] = cls._values_to_dict(v, level=level + 1)
        elif isinstance(data, (list)):
            result = []
            for k in range(len(data)):
                v = data[k]
                result.append(cls._values_to_dict(v, level=level + 1))
        elif isinstance(data, (np.ndarray)):
            result = data.tolist()
        elif isinstance(data, (MSection)):
            result = data.m_to_dict()
        else:
            result = data
        return result

    @classmethod
    def _filtered_section_keys(cls, section):
        for k in section.keys():
            # skip JSON-specific keys
            if k == '_gIndex':
                continue
            if k == '_name':
                continue
            else:
                # json values and subsections
                yield k


class BasisSetExciting(MethodNormalizerBasisSet):
    """Basis set settings for Exciting (code-dependent)."""

    def setup(self) -> tuple:
        # Get previously defined values from superclass
        mandatory, optional = super().setup()

        # Add new values
        mandatory += [
            'muffin_tin_settings',
            'rgkmax',
            'gkmax',
            'lo',
            'lmaxapw',
        ]

        return mandatory, optional

    def to_dict(self):
        """Special case of basis set settings for Exciting code. See list at:
        https://gitlab.mpcdf.mpg.de/nomad-lab/encyclopedia-general/wikis/FHI-visit-preparation
        """
        # Add the muffin-tin settings for each species ordered alphabetically by atom label
        try:
            groups = self._repr_system.x_exciting_section_atoms_group
            groups = sorted(
                groups, key=lambda group: group.x_exciting_geometry_atom_labels
            )
            muffin_tin_settings = OrderedDict()
            for group in groups:
                label = group.x_exciting_geometry_atom_labels
                try:
                    muffin_tin_settings[f'{label}_muffin_tin_radius'] = (
                        f'{group.x_exciting_muffin_tin_radius.to(ureg.angstrom).magnitude:.6f}'
                    )
                except Exception:
                    muffin_tin_settings[f'{label}_muffin_tin_radius'] = None
                try:
                    muffin_tin_settings[f'{label}_muffin_tin_points'] = (
                        f'{group.x_exciting_muffin_tin_points}'
                    )
                except Exception:
                    muffin_tin_settings[f'{label}_muffin_tin_points'] = None
            self.settings['muffin_tin_settings'] = muffin_tin_settings
        except Exception:
            pass

        # Other important method settings
        system = self._repr_system
        try:
            self.settings['rgkmax'] = f'{system.x_exciting_rgkmax.magnitude:.6f}'
        except Exception:
            pass
        try:
            self.settings['gkmax'] = '%.6f' % (
                1e-10 * system.x_exciting_gkmax.magnitude
            )
        except Exception:
            pass
        try:
            self.settings['lo'] = f'{system.x_exciting_lo}'
        except Exception:
            pass
        try:
            self.settings['lmaxapw'] = f'{system.x_exciting_lmaxapw}'
        except Exception:
            pass

        return self.settings
