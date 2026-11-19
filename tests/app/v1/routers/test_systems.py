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
from io import BytesIO, StringIO

import ase.io
import numpy as np
import pytest
from ase import Atoms as ASEAtoms

from nomad.app.v1.routers.systems import FormatFeature, WrapModeEnum, format_map
from nomad.datamodel.datamodel import EntryArchive
from nomad.datamodel.metainfo import runschema
from nomad.datamodel.metainfo.system import Atoms
from nomad.datamodel.results import Material, Results, System
from nomad.normalizing.common import ase_atoms_from_nomad_atoms
from nomad.units import ureg
from nomad.utils.exampledata import ExampleData

from .common import assert_browser_download_headers, assert_response


def ase_atoms(content, format):
    """Creates an ase.Atoms object given given file contents and format."""
    format = {'pdb': 'proteindatabank', 'xyz': 'extxyz'}.get(format, format)
    atoms = ase.io.read(StringIO(content), format=format)
    return atoms


def assert_contents(a: str, b: str):
    """Compares two file contents to each other. Differences in trailing
    whitespace are ignored.
    """

    def normalize(a):
        return re.sub(' +\n', '\n', a)

    assert normalize(a) == normalize(b)


def assert_atoms(
    a: ASEAtoms,
    b: ASEAtoms,
    compare_cell: bool = True,
    compare_pbc: bool = False,
    atol: float = 0,
    rtol: float = 1e-3,
):
    """Compares two different ase.Atoms objects to see if they have the same
    structure.
    """
    assert np.allclose(a.get_positions(), b.get_positions(), atol=atol, rtol=rtol)
    if compare_cell:
        assert np.allclose(a.get_cell(), b.get_cell(), atol=atol, rtol=rtol)
    if compare_pbc:
        assert np.array_equal(a.get_pbc(), b.get_pbc())
    assert np.array_equal(a.get_atomic_numbers(), b.get_atomic_numbers())
    assert np.array_equal(a.get_chemical_symbols(), b.get_chemical_symbols())


atoms_with_cell = (
    runschema.system.Atoms(
        n_atoms=2,
        labels=['C', 'H'],
        species=[6, 1],
        positions=np.array([[0, 0, 0], [1, 1, 1]]) * ureg.angstrom,
        lattice_vectors=np.array([[5, 0, 0], [0, 5, 0], [0, 0, 5]]) * ureg.angstrom,
        periodic=[True, True, True],
    )
    if runschema
    else None
)

atoms_without_cell = (
    runschema.system.Atoms(
        n_atoms=2,
        labels=['N', 'O'],
        species=[7, 8],
        positions=np.array([[0, 0, 0], [1, 1, 1]]) * ureg.angstrom,
    )
    if runschema
    else None
)

atoms_missing_positions = (
    runschema.system.Atoms(
        n_atoms=2,
        labels=['N', 'O'],
        species=[7, 8],
    )
    if runschema
    else None
)

atoms_wrap_mode = (
    runschema.system.Atoms(
        n_atoms=2,
        labels=['C', 'H'],
        species=[6, 1],
        positions=np.array([[-15, -15, -15], [17, 17, 17]]) * ureg.angstrom,
        lattice_vectors=np.array([[5, 0, 0], [0, 5, 0], [0, 0, 5]]) * ureg.angstrom,
        periodic=[True, True, True],
    )
    if runschema
    else None
)

atoms_wrap_mode_no_pbc = atoms_wrap_mode.m_copy() if atoms_wrap_mode else None
if atoms_wrap_mode_no_pbc is not None:
    atoms_wrap_mode_no_pbc.periodic = [False, False, False]


@pytest.fixture(scope='module')
def example_data_systems(elastic_module, mongo_module, user1):
    data = ExampleData(main_author=user1)
    upload_id = 'systems_upload'

    data.create_upload(upload_id=upload_id, published=True)
    archive = EntryArchive()
    if runschema:
        archive.run.append(
            runschema.run.Run(
                system=[
                    runschema.system.System(atoms=atoms_with_cell),
                    runschema.system.System(atoms=atoms_missing_positions),
                    runschema.system.System(atoms=atoms_without_cell),
                    runschema.system.System(atoms=atoms_wrap_mode),
                    runschema.system.System(atoms=atoms_wrap_mode_no_pbc),
                ]
            )
        )
    archive = EntryArchive(
        run=[
            runschema.run.Run(
                system=[
                    runschema.system.System(atoms=atoms_with_cell),
                    runschema.system.System(atoms=atoms_missing_positions),
                    runschema.system.System(atoms=atoms_without_cell),
                    runschema.system.System(atoms=atoms_wrap_mode),
                    runschema.system.System(atoms=atoms_wrap_mode_no_pbc),
                ]
            )
        ]
    )
    archive.results = Results(
        material=Material(
            topology=[
                System(atoms=Atoms.m_from_dict(atoms_without_cell.m_to_dict())),
                System(atoms_ref=archive.run[0].system[0].atoms),
                System(atoms_ref=archive.run[0].system[0].atoms, indices=[[0]]),
                System(atoms_ref=archive.run[0].system[0].atoms, indices=[[0]]),
                System(atoms_ref=archive.run[0].system[2].atoms),
            ]
        )
    )

    data.create_entry(
        upload_id=upload_id,
        entry_id='systems_entry_1',
        mainfile='test_content/test_entry/main-file.json',
        entry_archive=archive,
    )

    data.save()

    yield

    data.delete()
    from nomad.search import search

    assert search(query=dict(upload_id=upload_id)).pagination.total == 0


def run_query(entry_id, path, format, client, wrap_mode=None):
    response = client.get(
        f'systems/{entry_id}/?path={path}&format={format}{f"&wrap_mode={wrap_mode}" if wrap_mode else ""}',
        headers={},
    )
    return response


@pytest.mark.parametrize(
    'path, status_code',
    [
        pytest.param('run/0/system/0', 200, id='explicit indexing'),
        pytest.param('run/0/system/-1', 200, id='negative indexing'),
        pytest.param('/run/0/system/0', 200, id='start with slash'),
        pytest.param('/run/0/system/0', 200, id='end with slash'),
        pytest.param('results/material/topology/0', 200, id='saved in topology'),
        pytest.param('results/material/topology/1', 200, id='referenced in topology'),
        pytest.param('run/0/system/1', 500, id='cannot serialize'),
        pytest.param('results/does_not_exist', 404, id='invalid path'),
        pytest.param('results/material/topology/100', 404, id='not found'),
        pytest.param('run/100/system/0', 404, id='not found'),
    ],
)
def test_paths(path, status_code, client, example_data_systems, indices=None):
    response = run_query('systems_entry_1', path, 'pdb', client)
    assert_response(response, status_code)


@pytest.mark.parametrize(
    'format, content_expected, filename',
    [
        pytest.param(
            'pdb',
            """TITLE     NOMAD ENTRY ID: systems_entry_1
REMARK 285 LATTICE VECTORS
REMARK 285  A: 5.000, 0.000, 0.000
REMARK 285  B: 0.000, 5.000, 0.000
REMARK 285  C: 0.000, 0.000, 5.000
REMARK 285 PBC (A, B, C): TRUE, TRUE, TRUE
CRYST1    5.000    5.000    5.000  90.00  90.00  90.00 P 1
MODEL     1
ATOM      1    C MOL     1       0.000   0.000   0.000  1.00  0.00           C
ATOM      2    H MOL     1       1.000   1.000   1.000  1.00  0.00           H
ENDMDL
""",
            'CH.pdb',
            id='pdb',
        ),
        pytest.param(
            'cif',
            """# NOMAD ENTRY ID: systems_entry_1
data_CH
_chemical_formula_structural       CH
_chemical_formula_sum              "C1 H1"
_cell_length_a       5.0
_cell_length_b       5.0
_cell_length_c       5.0
_cell_angle_alpha    90.0
_cell_angle_beta     90.0
_cell_angle_gamma    90.0

_space_group_name_H-M_alt    "P 1"
_space_group_IT_number       1

loop_
  _space_group_symop_operation_xyz
  'x, y, z'

loop_
  _atom_site_type_symbol
  _atom_site_label
  _atom_site_symmetry_multiplicity
  _atom_site_fract_x
  _atom_site_fract_y
  _atom_site_fract_z
  _atom_site_occupancy
  C   C1        1.0  0.0  0.0  0.0  1.0000
  H   H1        1.0  0.2  0.2  0.2  1.0000
""",
            'CH.cif',
            id='cif',
        ),
        pytest.param(
            'xyz',
            """2
Lattice="5.0 0.0 0.0 0.0 5.0 0.0 0.0 0.0 5.0" Properties=species:S:1:pos:R:3 pbc="T T T" nomad_entry_id="systems_entry_1"
C        0.00000000       0.00000000       0.00000000
H        1.00000000       1.00000000       1.00000000
""",
            'CH.xyz',
            id='xyz',
        ),
    ],
)
def test_formats_with_cell(
    format, content_expected, filename, client, example_data_systems
):
    """Test that writing a structure with valid unit cell information produces
    the expected output.
    """
    format_info = format_map[format]
    response = run_query(
        'systems_entry_1', 'run/0/system/0', format_info['label'], client
    )
    assert_response(response, 200)
    assert_browser_download_headers(response, format_info['mime_type'], filename)

    content = response.content.decode('utf-8')
    assert_contents(content, content_expected)

    atoms = ase_atoms(content, format)
    assert_atoms(
        atoms,
        ase_atoms_from_nomad_atoms(atoms_with_cell),
        compare_cell=True,
        compare_pbc=format_info['features'][FormatFeature.PBC],
    )


@pytest.mark.parametrize(
    'format, content_expected, filename',
    [
        pytest.param(
            'pdb',
            """TITLE     NOMAD ENTRY ID: systems_entry_1
MODEL     1
ATOM      1    N MOL     1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2    O MOL     1       1.000   1.000   1.000  1.00  0.00           O
ENDMDL
""",
            'NO.pdb',
            id='pdb',
        ),
        pytest.param(
            'cif',
            """# NOMAD ENTRY ID: systems_entry_1
data_NO
_chemical_formula_structural       NO
_chemical_formula_sum              "N1 O1"
loop_
  _atom_site_type_symbol
  _atom_site_label
  _atom_site_symmetry_multiplicity
  _atom_site_Cartn_x
  _atom_site_Cartn_y
  _atom_site_Cartn_z
  _atom_site_occupancy
  N   N1        1.0  0.0  0.0  0.0  1.0000
  O   O1        1.0  1.0  1.0  1.0  1.0000
""",
            'NO.cif',
            id='cif',
        ),
        pytest.param(
            'xyz',
            """2
Properties=species:S:1:pos:R:3 pbc="F F F" nomad_entry_id="systems_entry_1"
N        0.00000000       0.00000000       0.00000000
O        1.00000000       1.00000000       1.00000000
""",
            'NO.xyz',
            id='xyz',
        ),
    ],
)
def test_formats_without_cell(
    format, content_expected, filename, client, example_data_systems
):
    """Test that writing a structure without unit cell information produces the
    expected output. Note that certain formats cannot be serialized without a
    dummy placeholder cell.
    """
    format_info = format_map[format]
    response = run_query('systems_entry_1', '/run/0/system/2', format, client)
    assert_response(response, 200)
    assert_browser_download_headers(response, format_info['mime_type'], filename)
    content = response.content.decode('utf-8')
    assert_contents(content, content_expected)
    atoms = ase_atoms(content, format)
    assert_atoms(
        atoms,
        ase_atoms_from_nomad_atoms(atoms_without_cell),
        compare_cell=format_info['features'][FormatFeature.NO_UNIT_CELL],
        compare_pbc=format_info['features'][FormatFeature.PBC],
    )


@pytest.mark.parametrize(
    'path, filename, n_atoms',
    [
        pytest.param('results/material/topology/2', 'C.cif', 1, id='1D indices'),
        pytest.param('results/material/topology/3', 'C.cif', 1, id='2D indices'),
    ],
)
def test_indices(path, filename, n_atoms, client, example_data_systems):
    """Test that systems where indices have been specified are returned
    correctly by including only a subset of atoms.
    """
    format_info = format_map['cif']
    response = run_query('systems_entry_1', path, 'cif', client)
    assert_response(response, 200)
    assert_browser_download_headers(response, format_info['mime_type'], filename)
    atoms = ase.io.read(BytesIO(response.content), format='cif')
    assert len(atoms) == n_atoms


@pytest.mark.parametrize(
    'path, wrap_mode, expected_positions',
    [
        pytest.param(
            '/run/0/system/3', None, [[-15, -15, -15], [17, 17, 17]], id='default'
        ),
        pytest.param(
            '/run/0/system/3',
            WrapModeEnum.original,  # type: ignore
            [[-15, -15, -15], [17, 17, 17]],
            id='original',
        ),
        pytest.param(
            '/run/0/system/3',
            WrapModeEnum.wrap,  # type: ignore
            [[0, 0, 0], [2, 2, 2]],
            id='wrap, pbc=[1, 1, 1]',
        ),
        pytest.param(
            '/run/0/system/4',
            WrapModeEnum.wrap,  # type: ignore
            [[-15, -15, -15], [17, 17, 17]],
            id='wrap, pbc=[0, 0, 0]',
        ),
        pytest.param(
            '/run/0/system/3',
            WrapModeEnum.unwrap,  # type: ignore
            [[1.5, 1.5, 1.5], [3.5, 3.5, 3.5]],
            id='unwrap, pbc=[1, 1, 1]',
        ),
        pytest.param(
            '/run/0/system/4',
            WrapModeEnum.unwrap,  # type: ignore
            [[-15, -15, -15], [17, 17, 17]],
            id='unwrap, pbc=[0, 0, 0]',
        ),
    ],
)
def test_wrap_mode(path, wrap_mode, expected_positions, client, example_data_systems):
    """Test that the wrap_mode parameter is handled correctly."""
    response = run_query('systems_entry_1', path, 'xyz', client, wrap_mode)
    assert_response(response, 200)
    atoms = ase.io.read(StringIO(response.text), format='xyz')
    positions = atoms.get_positions()
    assert np.allclose(positions, expected_positions)
