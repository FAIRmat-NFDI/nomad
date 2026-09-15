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

import sys
from collections import OrderedDict
from enum import Enum
from io import BytesIO, StringIO
from typing import Annotated

import ase.io
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import Response

from nomad.app.v1.routers.auth import get_current_user
from nomad.atomutils import Formula, unwrap_positions, wrap_positions
from nomad.auth.scopes import Scope
from nomad.datamodel.metainfo.system import Atoms as NOMADAtoms
from nomad.normalizing.common import ase_atoms_from_nomad_atoms
from nomad.units import ureg
from nomad.utils import deep_get, query_list_to_dict, strip

from ..models import HTTPExceptionModel, User
from ..utils import browser_download_headers, create_responses
from .entries import answer_entry_archive_request

router = APIRouter()


class APITag(str, Enum):
    DEFAULT = 'systems'


def write_pdb(
    atoms: NOMADAtoms, entry_id: str | None = None, formula: str | None = None
) -> str:
    """For writing a PDB file."""

    # Add custom title that contains the entry id.
    lines = []
    if entry_id is not None:
        lines.append(f'TITLE     NOMAD ENTRY ID: {entry_id}\n')

    # PDB files do not contain a field for the full lattice vectors and the PBC.
    # To work around this, they are stored as REMARKs. The info on full lattice
    # vectors and pbc are used by the the GUI visualizer.
    lattice_vectors = atoms.lattice_vectors
    pbc = atoms.periodic
    if lattice_vectors is not None:
        cell = lattice_vectors.to(ureg.angstrom).magnitude
        lines.append('REMARK 285 LATTICE VECTORS\n')
        lines.append(
            f'REMARK 285  A: {cell[0, 0]:.3f}, {cell[0, 1]:.3f}, {cell[0, 2]:.3f}\n'
        )
        lines.append(
            f'REMARK 285  B: {cell[1, 0]:.3f}, {cell[1, 1]:.3f}, {cell[1, 2]:.3f}\n'
        )
        lines.append(
            f'REMARK 285  C: {cell[2, 0]:.3f}, {cell[2, 1]:.3f}, {cell[2, 2]:.3f}\n'
        )
    if pbc is not None:
        pbc = ['TRUE' if x else 'FALSE' for x in pbc]
        lines.append(f'REMARK 285 PBC (A, B, C): {pbc[0]}, {pbc[1]}, {pbc[2]}\n')

    stream = StringIO()
    ase_atoms = ase_atoms_from_nomad_atoms(atoms)
    ase.io.write(stream, ase_atoms, format='proteindatabank')
    stream.seek(0)
    content = ''.join(lines)
    content += stream.read()
    return content


def write_cif(
    atoms: NOMADAtoms, entry_id: str | None = None, formula: str | None = None
) -> str:
    """For writing a CIF file."""
    # The ASE CIF writer expects a BytesIO, unlike other formats supported by ASE.
    byte_stream = BytesIO()
    ase_atoms = ase_atoms_from_nomad_atoms(atoms)
    ase.io.write(byte_stream, ase_atoms, format='cif')
    byte_stream.seek(0)
    content = ''
    if entry_id is not None:
        content = f'# NOMAD ENTRY ID: {entry_id}\n'
    content += byte_stream.read().decode('utf-8')
    content = content.replace('data_image0', f'data_{formula}')
    return content


def write_xyz(atoms: NOMADAtoms, entry_id: str, formula: str | None = None) -> str:
    """For writing an XYZ file."""
    stream = StringIO()
    ase_atoms = ase_atoms_from_nomad_atoms(atoms)
    ase.io.write(stream, ase_atoms, format='extxyz')
    stream.seek(0)
    content = stream.read()
    if entry_id is not None:
        content = content.replace('"\n', f'" nomad_entry_id="{entry_id}"\n', 1)
    return content


class FormatFeature(str, Enum):
    """Contains features that are relevant for the different file formats used to
    serialize atomic configurations.
    """

    NO_UNIT_CELL = 'Cartesian positions without unit cell'
    LATTICE_VECTORS = 'Full lattice vectors'
    PBC = 'Periodic boundary conditions (PBC)'


format_map: dict[str, dict] = OrderedDict(
    {
        'cif': {
            'label': 'cif',
            'description': 'Crystallographic Information File',
            'extension': 'cif',
            'features': {
                FormatFeature.PBC: False,
                FormatFeature.LATTICE_VECTORS: False,
                FormatFeature.NO_UNIT_CELL: True,
            },
            'mime_type': 'chemical/x-cif',
            'writer': write_cif,
        },
        'xyz': {
            'label': 'xyz',
            'description': """XYZ file. The comment line contains information that
        complies with the extended XYZ specification.""",
            'extension': 'xyz',
            'features': {
                FormatFeature.PBC: True,
                FormatFeature.LATTICE_VECTORS: True,
                FormatFeature.NO_UNIT_CELL: True,
            },
            'mime_type': 'chemical/x-xyz',
            'writer': write_xyz,
        },
        'pdb': {
            'label': 'pdb',
            'description': """Protein Data Bank file. Note that valid PDB files
        require a CRYST1 record, while certains systems in NOMAD may not have a
        unit cell associated with them. In this case the returned structure file
        will contain a dummy CRYST1 record in order to load the atomic
        positions.""",
            'features': {
                FormatFeature.PBC: False,
                FormatFeature.LATTICE_VECTORS: False,
                FormatFeature.NO_UNIT_CELL: False,
            },
            'extension': 'pdb',
            'mime_type': 'chemical/x-pdb',
            'writer': write_pdb,
        },
    }
)

format_description = '\n'.join(
    [
        f'- `{format["label"]}`: {format["description"]}'
        for format in format_map.values()
    ]
)
format_features_list = []
format_features_list.append(
    '|'.join(['Format'] + [feature.value for feature in FormatFeature])
)
format_features_list.append('|'.join([':---'] + [':---:'] * (len(FormatFeature))))
for format in format_map.values():
    format_features_list.append(
        '|'.join(
            [format['label']]
            + [
                '&#9745;' if format['features'][feature.value] else '&#9744;'
                for feature in FormatFeature
            ]
        )
    )
format_features = '\n'.join(format_features_list)

if sys.version_info < (3, 11):

    class TempFormatEnum(str, Enum):
        pass
else:
    from enum import StrEnum

    class TempFormatEnum(StrEnum):
        pass


FormatEnum = TempFormatEnum(
    'FormatEnum', {format: format for format in format_map.keys()}
)  # type: ignore


wrap_mode_map: dict[str, dict] = OrderedDict(
    {
        'original': {'description': 'The original positions as set in the data'},
        'wrap': {
            'description': 'Positions are wrapped to be inside the cell respecting periodic boundary conditions'
        },
        'unwrap': {
            'description': strip(
                """
        Positions are reconstructed so that the structure is not split by
        periodic cell boundaries. Note that this produces meaningful results
        only if the system dimensions are smaller than the unit cell.
    """
            )
        },
    }
)


if sys.version_info < (3, 11):

    class TempWrapModeEnum(str, Enum):
        pass
else:
    from enum import StrEnum

    class TempWrapModeEnum(StrEnum):
        pass


WrapModeEnum = TempWrapModeEnum(
    'WrapModeEnum', {wrap_mode: wrap_mode for wrap_mode in wrap_mode_map.keys()}
)  # type: ignore
wrap_mode_description = '\n'.join(
    [f'- `{key}`: {value["description"]}' for [key, value] in wrap_mode_map.items()]
)


_file_response = (
    status.HTTP_200_OK,
    {
        'content': {'application/octet-stream': {}},
        'description': strip(
            """
        A byte stream with file contents. The content length is not known in advance.
        The final mime-type may be more specific depending on the format.
    """
        ),
    },
)

_not_found_response = (
    status.HTTP_404_NOT_FOUND,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Could not find data for given entry id and path. Check that the
        arguments are correct and that you are authorized to access the data.
    """
        ),
    },
)

_serialization_error_response = (
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Could not serialize the system information in the given format. The
        information may be invalid or incomplete for this format.
    """
        ),
    },
)


@router.get(
    '/{entry_id}',
    tags=[APITag.DEFAULT],
    summary=strip(
        """
    Build and retrieve an atomistic structure file from data within an entry.
    """
    ),
    response_class=Response,
    responses=create_responses(
        _file_response, _not_found_response, _serialization_error_response
    ),
)
def get_entry_raw_file(
    user: Annotated[User, Depends(get_current_user([Scope.SYSTEMS_READ]))],
    entry_id: Annotated[
        str,
        Path(
            description='The unique entry id of the entry to retrieve archive data from.'
        ),
    ],
    path: Annotated[
        str,
        Query(
            examples=['run/0/system/0'],
            description=strip(
                """
            Path to a NOMAD System inside the archive. The targeted path should
            point to a system in `run.system` or `results.material.topology`.
            The following path types are supported:

            - `run/0/system/0`: Path to system in `run.system`
            - `results/material/topology/0`: Path to system in `results.material.topology`
            - `run/0/system/-1`: Negative indices are supported."""
            ),
        ),
    ],
    format: Annotated[  # type: ignore[valid-type]
        FormatEnum,
        Query(
            description=f"""The file format for the system. The following formats are supported:

{format_description}

Here is a brief rundown of the different features each format supports:

{format_features}"""
        ),
    ] = 'cif',
    wrap_mode: Annotated[  # type: ignore[valid-type]
        WrapModeEnum,
        Query(
            description=f"""Determines how to handle atomic positions for the requested system. The available options are:

{wrap_mode_description}

            """
        ),
    ] = WrapModeEnum.original,  # type: ignore[attr-defined]
):
    """
    Build and retrieve a structure file containing an atomistic system stored
    within an entry.

    All length dimensions in the structure files are in Ångstroms (=1e-10 meter).

    Note that some formats are more restricted and cannot fully
    describe certains kinds of systems. For examples some entries within NOMAD
    do not contain a unit cell (e.g. molecules), whereas some formats require it
    to be present.
    """
    # Remove prefix
    for prefix in ['#/']:
        if path.startswith(prefix):
            path = path[len(prefix) :]

    # Add indexing
    query_list: list[str | int] = []
    paths = [x for x in path.split('/') if x != '']
    i = 0
    while i < len(paths):
        a = paths[i]
        query_list.append(a)
        try:
            b = int(paths[i + 1])
            i += 1
            query_list.append(b)
        except Exception:
            pass
        i += 1

    # We extract both atoms and atoms_ref
    value = {'atoms': '*'}
    if 'topology' in path:
        value['atoms_ref'] = 'include-resolved'
        value['indices'] = '*'

    # Fetch the specific part of the archive. If path not found, raise exception
    required = query_list_to_dict(query_list, value)
    required['resolve-inplace'] = True
    query = {'entry_id': entry_id}
    try:
        archive = answer_entry_archive_request(query, required=required, user=user)[
            'data'
        ]['archive']
    except Exception as e:
        raise HTTPException(
            status_code=_not_found_response[0],
            detail=_not_found_response[1]['description'],
        ) from e

    # The returned archive contains a single section when an index has been
    # specified, and thus we have to set all original indices to zero when
    # extracting the data.
    try:
        result_dict = deep_get(
            archive, *[0 if isinstance(x, int) else x for x in query_list]
        )
    except Exception as e:
        raise HTTPException(
            status_code=_not_found_response[0],
            detail='The given path does not exist in the archive.',
        ) from e

    # Extract the atoms: they can be given under 'atoms' or 'atoms_ref'
    atoms = result_dict.get('atoms', result_dict.get('atoms_ref'))
    formula = None

    # Write file into stream in memory
    format_info = format_map[format]
    try:
        atoms = NOMADAtoms.m_from_dict(atoms)

        # Use indices to strip down the atoms to include. When the system has
        # several representative indices, the first one is returned.
        if indices := result_dict.get('indices'):
            indices = np.array(indices)
            if len(indices.shape) == 2:
                indices = indices[0]
            if atoms.atomic_numbers is not None:
                atoms.atomic_numbers = atoms.atomic_numbers[indices]
            if atoms.positions is not None:
                atoms.positions = atoms.positions[indices]
            if atoms.species is not None:
                atoms.species = atoms.species[indices]
            if atoms.labels is not None:
                atoms.labels = np.array(atoms.labels)[indices].tolist()

        try:
            formula = Formula(''.join(atoms.labels)).format('iupac')
        except Exception:
            pass

        # Handle wrap mode
        if wrap_mode == WrapModeEnum.wrap:  # type: ignore
            atoms.positions = wrap_positions(
                atoms.positions.magnitude,
                atoms.lattice_vectors.magnitude,
                atoms.periodic,
            )
        elif wrap_mode == WrapModeEnum.unwrap:  # type: ignore
            atoms.positions = unwrap_positions(
                atoms.positions.magnitude,
                atoms.lattice_vectors.magnitude,
                atoms.periodic,
            )

        content = format_info['writer'](atoms, entry_id, formula)
    except Exception as e:
        raise HTTPException(
            status_code=_serialization_error_response[0],
            detail=_serialization_error_response[1]['description'],
        ) from e

    # Return the contents of the stream. A simple Response is used instead of
    # StreamingResponse since the content is already in memory and it is not
    # very big
    return Response(
        content=content,
        media_type=format_info['mime_type'],
        headers=browser_download_headers(
            f'{formula or "system"}.{format_info["extension"]}',
            format_info['mime_type'],
        ),
    )
