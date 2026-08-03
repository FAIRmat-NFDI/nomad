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

from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse

from nomad.app.v1.models import User
from nomad.app.v1.routers.auth import get_current_user
from nomad.auth.scopes import Scope
from nomad.metainfo.util import MDefNotFound, MDefWithoutMetainfo
from nomad.schemas import get_schema as get_schema_definition

router = APIRouter()


class APITag(str, Enum):
    DEFAULT = 'schemas'


class SerializationFormat(str, Enum):
    JSONSCHEMA = 'jsonschema'
    METAINFO = 'metainfo'


@router.get(
    '/{schema_id}',
    tags=[APITag.DEFAULT],
    summary='Return a serialization of a specific data schema.',
)
def get_schema(
    _user: Annotated[User, Depends(get_current_user([Scope.SCHEMAS_READ]))],
    schema_id: Annotated[
        str,
        Path(
            description="""
Schema identifier given as a fully qualified Python class name, optionally followed by
`@<tag>` to pin a specific version. For example:
  - `package_name.schema_packages.calculations.MySchema` — resolves to the most recently added definition.
  - `package_name.schema_packages.calculations.MySchema@<definition_id>` — resolves and
    verifies that the definition ID matches the given tag.

Note that for now only the tag corresponding to the most recently added definition is
supported, but in the future we may allow access to older versions of the schema by their
tags.
"""
        ),
    ],
    format: Annotated[
        SerializationFormat,
        Query(
            description="""
Format for the returned schema. Available formats:
  - `jsonschema` (default) - JSON Schema (Draft 2020-12) representation.

    Always-included keywords:
      - `"$schema"`: The JSONSchema version
      - `"$id"`: Identifier as a resolvable URL

    Optional extras (added only when present):
      - `"title"`: Name of the quantity/section
      - `"description"`: Description of the quantity/section
      - `"unit"`: Unit of a quantity as string

    Type Data Keywords:
      - `"type"`                   - type of the quantity/section.
      - Scalar quantities are mapped to the corresponding JSON type, e.g. `number`.
      - Arrays: `shape` is walked from left to right, wrapping each dimension
        in a nested ``{"type": "array", …, "items": {…}}``. Array shape is
        mapped into `minItems`/`maxItems` as follows:
          - `n` (int)              - `minItems = maxItems = n`
          - `"*"`                  - unbounded: no min/max keys
          - `"a.."`                - `minItems = a`
          - `"..b"`                - `maxItems = b`
          - `"a..b"`               - `minItems = a`, `maxItems = b`
          - any other string       - treated as `"*"`
      - Optional fields for quantities:
          - `"default"`      - Default value of a quantity
          - `"enum"`         - List of allowed values for an Enum quantity
          - `"minimum"`      - Minimum value for a quantity (from ELN annotation)
          - `"maximum"`      - Maximum value for a quantity (from ELN annotation)
      - Reference quantities are mapped to type strings
      - Sections have the `object` type
  - `metainfo` - The NOMAD-specific serialization format with full fidelity to the original schema.
"""
        ),
    ] = SerializationFormat.JSONSCHEMA,
    unit_value: Annotated[
        bool,
        Query(
            description="""Expand schema fields into a `value`/`unit` object schema.

    This converts a flat schema property with an unit associated with it into an object like:

    {
        "properties": {
            "value": <original field schema>,
            "unit": {"type": "string", "enum": [unit]}
        }
    }
    """
        ),
    ] = False,
    section_subtypes: Annotated[
        bool,
        Query(description='Include subtypes of the specified schema in the output.'),
    ] = False,
    property_subtypes: Annotated[
        bool,
        Query(
            description='Include subtypes of the properties of the specified schema in the output.'
        ),
    ] = False,
):
    """
    Returns the serialized schema for the given id. The returned
    schema is serialized in the format specified by the `format` query parameter.
    """

    # Split the identifier into qualified name and optional tag
    qualified_name, _, tag = schema_id.partition('@')

    # Resolve class
    try:
        section = get_schema_definition(qualified_name, tag or None)
    except MDefNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Could not resolve {qualified_name} to a valid schema class or property.',
        ) from e
    except MDefWithoutMetainfo as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    if format == SerializationFormat.JSONSCHEMA:
        return JSONResponse(
            content=section.m_to_json_schema(
                add_unit_value=unit_value,
                add_section_subtypes=section_subtypes,
                add_property_subtypes=property_subtypes,
            ),
            media_type='application/schema+json',
        )
    if format == SerializationFormat.METAINFO:
        return JSONResponse(
            content=section.m_def.m_to_dict(),
            media_type='application/json',
        )
