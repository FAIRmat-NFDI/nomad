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

import pytest


@pytest.mark.parametrize(
    'entry, method_identified', [('hash_exciting', True), ('hash_vasp', False)]
)
def test_method_id(entry, method_identified, request):
    """Test that method_id can be detected or is left undetected from certain
    calculations.
    """
    entry = request.getfixturevalue(entry)
    assert (entry.results.method.method_id is not None) == method_identified
    assert (entry.results.method.equation_of_state_id is not None) == method_identified
    assert (
        entry.results.method.parameter_variation_id is not None
    ) == method_identified


def test_basis_set(normalized_example):
    ref_basis_sets = {
        'exciting': ['APW', 'LAPW', 'APW+lo', 'LAPW+lo', '(L)APW', '(L)APW+lo'],
        'fleur': [
            'APW',
            'LAPW',
            'APW+lo',
            'LAPW+lo',
            '(L)APW',
            '(L)APW+lo',
        ],  # TODO: does not match the metainfo name
        'WIEN2k': ['APW', 'LAPW', 'APW+lo', 'LAPW+lo', '(L)APW', '(L)APW+lo'],
        'Elk': ['APW', 'LAPW', 'APW+lo', 'LAPW+lo', '(L)APW', '(L)APW+lo'],
        'ABINIT': ['plane waves'],
        'AFlow': ['plane waves'],
        'atomate': ['plane waves'],
        'CASTEP': ['plane waves'],
        'CPMD': ['plane waves'],
        'MaterialsProject': ['plane waves'],
        'Quantum Espresso': ['plane waves'],
        'VASP': ['plane waves'],
        'AMS': ['atom-centered orbitals'],
        'Crystal': ['atom-centered orbitals'],
        'DMol3': ['atom-centered orbitals'],
        'FHI-aims': ['atom-centered orbitals'],
        'FHI-vibes': ['atom-centered orbitals'],
        'GAMESS': ['atom-centered orbitals'],
        'Gaussian': ['atom-centered orbitals'],
        'LOBSTER': ['atom-centered orbitals'],
        'NWChem': ['atom-centered orbitals'],
        'ORCA': ['atom-centered orbitals'],
        'Psi4': ['atom-centered orbitals'],
        'turbomole': ['atom-centered orbitals'],
        'CP2K': ['gaussians + plane waves', 'plane waves'],
        'BigDFT': ['real-space grid'],
        'Octopus': ['real-space grid'],
        'GPAW': ['real-space grid', 'plane waves', 'atom-centered orbitals'],
        'Siesta': ['real-space grid', 'atom-centered orbitals'],
        'ONETEP': ['support functions'],
        'DL_POLY_4': [],
        'Phonopy': [],
        'ATK': [],
        'gulp': [],
        'elastic': [],
        'qbox': [],
        'YAMBO': [],
    }
    codes_without_methods = ['libAtoms', 'MOLCAS', 'ASR']
    try:
        base = normalized_example.results.method.simulation
    except AttributeError:
        return
    if base is not None:
        program_name = base.program_name
        if program_name in codes_without_methods:
            assert (
                not base.precision
            )  # precision is generated only if run.method exists
        elif program_name != 'not processed':
            reference = ref_basis_sets[program_name]
            if basis_set := base.precision.basis_set:
                assert basis_set in reference + ['unavailable']
