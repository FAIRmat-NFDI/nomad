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


from types import ModuleType

import pytest

from nomad.datamodel.hdf5 import HDF5Dataset, HDF5Reference
from nomad.metainfo.data_type import (
    JSON,
    Any,
    Bytes,
    Callable,
    Datatype,
    Datetime,
    Dimension,
    Enum,
    UnresolvedCustom,
    m_bool,
    m_complex128,
    m_float64,
    m_int64,
    m_str,
    normalize_type,
    to_json_schema_type,
)
from nomad.metainfo.metainfo import QuantityType


@pytest.mark.parametrize(
    'input_type, expected',
    [
        (Any(), {}),
        (Callable(), {}),
        (Dimension(), {'anyOf': [{'type': 'integer'}, {'type': 'string'}]}),
        (m_int64(), {'type': 'integer'}),
        (m_float64(), {'type': 'number'}),
        (m_bool(), {'type': 'boolean'}),
        (m_str(), {'type': 'string'}),
        (Enum('a', 'b'), {'type': 'string'}),
        (JSON(), {'type': 'object'}),
        (Datetime(), {'type': 'string', 'format': 'date-time'}),
        (Bytes(), {'type': 'string', 'contentEncoding': 'base64'}),
        (
            m_complex128(),
            {
                'type': 'object',
                'properties': {'re': {'type': 'number'}, 'im': {'type': 'number'}},
            },
        ),
        (
            HDF5Dataset(),
            {
                'type': 'string',
            },
        ),
        (
            HDF5Reference(),
            {
                'type': 'string',
            },
        ),
        (
            QuantityType(),
            {
                'type': 'object',
                'properties': {
                    'type_kind': {'type': 'string'},
                    'type_data': {
                        'anyOf': [
                            {'type': 'string'},
                            {'type': 'array', 'items': {'type': 'string'}},
                        ]
                    },
                },
            },
        ),
    ],
)
def test_to_json_schema_type(input_type, expected):
    actual_schema = to_json_schema_type(input_type)
    assert actual_schema == expected


def test_to_json_schema_type_unsupported():
    class FakeType:
        def standard_type(self):
            return 'nope'

    with pytest.raises(NotImplementedError, match='Unsupported JSON Schema type'):
        to_json_schema_type(FakeType())


class _BoundedLike(m_float64):
    """A datatype whose `json_schema_extra` contributes numeric bounds."""

    def standard_type(self) -> str:
        return 'float'

    def json_schema_extra(self) -> dict:
        return {'minimum': 0.0, 'maximum': 2.0}


@pytest.mark.parametrize(
    'in_type, expected',
    [
        pytest.param(
            _BoundedLike(),
            {'type': 'number', 'minimum': 0.0, 'maximum': 2.0},
            id='hook-adds-bounds',
        ),
        pytest.param(m_float64(), {'type': 'number'}, id='base-adds-nothing'),
    ],
)
def test_to_json_schema_type_merges_json_schema_extra(
    in_type: Datatype, expected: dict
) -> None:
    """A data type contributing extra keywords via `json_schema_extra` has them
    merged onto the base type mapping; the base `Datatype` contributes nothing."""
    assert to_json_schema_type(in_type) == expected


@pytest.fixture
def _no_ghost_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `ghost_plugin.data_types` unimportable, as on a deployment lacking
    the plugin that defines the custom type."""
    import importlib

    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None) -> ModuleType:
        if name == 'ghost_plugin.data_types':
            raise ModuleNotFoundError(f"No module named '{name}'")
        return real_import(name, package)

    monkeypatch.setattr(importlib, 'import_module', fake_import)


@pytest.mark.parametrize(
    'type_dtype, expected_standard_type',
    [
        pytest.param('float', 'float', id='python-float-name'),
        pytest.param('float64', 'float', id='numpy-name-needs-np-prefix'),
        pytest.param('int', 'int', id='python-int-name'),
        pytest.param('int64', 'int', id='numpy-int64-name'),
        pytest.param(None, 'any', id='no-dtype-falls-back-to-any'),
    ],
)
def test_normalize_type_custom_missing_class_falls_back(
    _no_ghost_plugin, type_dtype: str | None, expected_standard_type: str
) -> None:
    """A schema transferred from a deployment with an extra plugin must still load
    where that plugin is absent: an unimportable custom type degrades to a stand-in
    that validates as its `type_dtype` base (numpy names included, else `Any`) and
    re-serializes the original definition verbatim, instead of raising."""
    serialized = {
        'type_kind': 'custom',
        'type_data': 'ghost_plugin.data_types.m_bounded',
    }
    if type_dtype is not None:
        serialized['type_dtype'] = type_dtype
        serialized['type_bound'] = '[0,2]'

    resolved = normalize_type(serialized)

    assert isinstance(resolved, UnresolvedCustom)
    # validates as the reconstructed base, not silently collapsed
    assert resolved.standard_type().startswith(expected_standard_type)
    # the original serialization (incl. the custom class ref) round-trips intact
    assert resolved.serialize_self() == serialized
