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

from __future__ import annotations

import hashlib
import re
import warnings
from copy import deepcopy
from typing import Any, cast

import pint

from nomad.config import config
from nomad.metainfo.data_type import (
    Enum,
    m_complex,
    m_complex128,
    m_float32,
    m_str,
    to_json_schema_type,
)
from nomad.units import ureg

__hash_method = 'sha1'  # choose from hashlib.algorithms_guaranteed

MEnum = Enum  # type: ignore

UNIT_VALUE_SCHEMA = {
    '$id': 'https://schema.local/definitions/UnitValue',
    'properties': {'value': {'type': 'number'}, 'unit': {'type': 'string'}},
}

JSON_SCHEMA_VERSION: str = 'https://json-schema.org/draft/2020-12/schema'

SCHEMA_ENDPOINT = f'{config.client.url}/v1/schemas'


class MQuantity:
    """
    A simple wrapper to represent complex quantities that may have multiple values,
    additional attributes, and more.
    """

    def __init__(
        self,
        in_name: str | None,
        in_value: Any,
        in_unit: pint.Unit | None = None,
        in_attributes: dict | None = None,
    ):
        """
        The validation of value/unit/attribute is performed at 'MSection' level.
        """
        self.name: str | None = in_name
        if self.name:
            assert isinstance(self.name, str), 'Name must be a string'

        self.unit: pint.Unit | None = None
        if isinstance(in_value, pint.Quantity):
            self.value = in_value.m  # magnitude
            self.unit = cast(pint.Unit, in_value.u)  # unit
            assert in_unit is None, f'Unit is already defined in the value {in_value}'
        else:
            # the input argument is not a pint quantity
            # the unit is set to None
            self.value = in_value
            if isinstance(in_unit, pint.Unit):
                self.unit = in_unit
            elif isinstance(in_unit, str):
                self.unit = ureg.parse_units(in_unit)

        self.original_unit: pint.Unit | None = self.unit

        self.attributes: dict = {}
        if in_attributes is not None:
            self.attributes.update(**in_attributes)
            self.__dict__.update(**in_attributes)

    @staticmethod
    def from_dict(name: str, data: dict) -> MQuantity:
        m_quantity = MQuantity(
            name,
            data['m_value'],
            data.get('m_unit', None),
            data.get('m_attributes', None),
        )

        if 'm_original_unit' in data:
            m_quantity.original_unit = ureg.parse_units(data['m_original_unit'])

        return m_quantity

    @staticmethod
    def wrap(in_value: Any, in_name: str | None = None):
        """
        Syntax sugar to wrap a value into a MQuantity. The name is optional.

        This would be useful for non-variadic primitive quantities with additional attributes.
        """
        return MQuantity(in_name, in_value)

    def __repr__(self):
        return self.name if self.name else 'Unnamed quantity'

    def m_set_attribute(self, name, value):
        """
        Validation is done outside this container
        """
        self.attributes[name] = value

    def get(self):
        if self.unit:
            return ureg.Quantity(self.value, self.unit)

        return self.value


class MSubSectionList(list):
    def __init__(self, section, sub_section_def):
        self.section = section
        self.sub_section_def = sub_section_def
        super().__init__()

    def __setitem__(self, key, value):
        old_value = self[key]
        super().__setitem__(key, value)
        # noinspection PyProtectedMember
        self.section._on_add_sub_section(self.sub_section_def, value, key)
        # noinspection PyProtectedMember
        self.section._on_remove_sub_section(self.sub_section_def, old_value)

    def __getitem__(self, item):
        if isinstance(item, str):
            for sub_section in self:
                if sub_section.m_key == item:
                    return sub_section
            raise KeyError(f'No subsection keyed {item} found.')

        return super().__getitem__(item)

    def __delitem__(self, key):
        self.pop(key)

    def __setslice__(self, i, j, sequence):
        raise NotImplementedError('You can only append subsections.')

    def __delslice__(self, i, j):
        raise NotImplementedError('You can only append subsections.')

    def append(self, value):
        super().append(value)
        # noinspection PyProtectedMember
        self.section._on_add_sub_section(self.sub_section_def, value, len(self) - 1)

    def pop(self, key=-1):
        if key < 0:
            key = key + len(self)
        old_value = super().pop(key)
        for index in range(key, len(self)):
            # noinspection PyProtectedMember
            self.section._on_add_sub_section(self.sub_section_def, self[index], index)

        # noinspection PyProtectedMember
        self.section._on_remove_sub_section(self.sub_section_def, old_value)

        return old_value

    def extend(self, new_value):
        start_index = len(self)
        super().extend(new_value)
        for index, value in enumerate(new_value):
            # noinspection PyProtectedMember
            self.section._on_add_sub_section(
                self.sub_section_def, value, start_index + index
            )

    def insert(self, i, element):
        raise NotImplementedError('You can only append subsections.')

    def remove(self, element):
        raise NotImplementedError('You can only append subsections.')

    def reverse(self):
        raise NotImplementedError('You can only append subsections.')

    def sort(self, *, key=..., reverse=...):
        raise NotImplementedError('You can only append subsections.')

    def clear(self):
        old_values = list(self)
        super().clear()
        for old_value in old_values:
            # noinspection PyProtectedMember
            self.section._on_remove_sub_section(self.sub_section_def, old_value)

    def has_duplicated_key(self) -> bool:
        """
        Check if each section has a unique key.
        The key is from `.m_key` or the index of the section.
        """
        unique_keys: set = set()
        for index, section in enumerate(self):
            if section is not None:
                if section.m_key in unique_keys:
                    return True
                unique_keys.add(section.m_key)

        return False


def to_dict(entries):
    if isinstance(entries, list):
        return [to_dict(entry) for entry in entries]

    # noinspection PyBroadException
    try:
        entries = entries.m_to_dict()
    except Exception:
        pass

    return entries


def convert_to(from_magnitude, from_unit: pint.Unit | None, to_unit: pint.Unit | None):
    """
    Convert a magnitude from one unit to another.

    Arguments:
        from_magnitude: the magnitude to be converted
        from_unit: the unit of the magnitude
        to_unit: the unit to convert to

    Return:
        the converted magnitude
    """

    if to_unit is None:
        return from_magnitude

    from_quantity: pint.Quantity = from_magnitude * from_unit

    return from_quantity.to(to_unit).m


def get_namefit(name: str, concept_name: str, name_any: bool = False) -> int:
    """
    Checks if a given name corresponds to a specified concept name.

    The function evaluates whether the provided `name` matches the `concept_name`, allowing
    for certain variations. A group of uppercase letters in the `concept_name` is treated
    as a freely choosable part of this name.

    If a match is found, this function returns twice the length of the `concept_name` for an
    exact match. Otherwise, it returns the number of matching characters (case insensitive) or
    zero if `name_any` is set to True. If no match is found, it returns -1.

    The function treats uppercase groups independently and counts lowercase matches without
    regard to uppercase group lengths. For example, calling `get_namefit("my_fancy_yet_long_name", "my_SOME_name")`
    would yield a score of 8 for the lowercase matches `my_..._name`.

    All characters in `[a-zA-Z0-9_.]` are considered for matching against uppercase letters.
    Using any other character in the `name` will result in a return value of -1.
    Periods at the beginning or end of the `name` are not allowed, and only exact matches will be considered.

    Examples:

        * `get_namefit("test_name", "TEST_name")` returns 9
        * `get_namefit("te_name", "TEST_name")` returns 7
        * `get_namefit("my_other_name", "TEST_name")` returns 5
        * `get_namefit("test_name", "test_name")` returns 18
        * `get_namefit("test_other", "test_name")` returns -1
        * `get_namefit("something", "XXXX")` returns 0
        * `get_namefit("something", "OTHER")` returns 1

    Args:
        name (str): The name to check for matching.
        concept_name (str): The concept name to match against.
        name_any (bool, optional):
            If True, accepts any name and returns either 0 (match) or -1 (no match).
            Defaults to False.

    Returns:
        int: -1 if no match is found, the number of matching characters (case insensitive),
             or twice the length of `concept_name` for an exact match.
    """
    if concept_name == name:
        return len(concept_name) * 2
    if name.startswith('.') or name.endswith('.'):
        # Don't match anything with a dot at the beginning or end
        return -1

    uppercase_parts_pattern = re.compile(r'[A-Z]+(?:_[A-Z]+)*')
    uppercase_parts = uppercase_parts_pattern.findall(concept_name)

    path_regex = r'([a-zA-Z0-9_.]*)'
    regex_name = concept_name
    uppercase_count = sum(len(part) for part in uppercase_parts)

    for up in uppercase_parts:
        regex_name = regex_name.replace(up, path_regex)

    # Compile the full regex for matching the HDF name
    name_regex = re.compile(rf'^{regex_name}$')
    name_match = name_regex.fullmatch(name)

    if name_match is None:
        return 0 if name_any else -1

    match_count = sum(
        1
        for up, match in zip(uppercase_parts, name_match.groups())
        for s1, s2 in zip(up.upper(), match.upper())
        if s1 == s2
    )

    return len(concept_name) + match_count - uppercase_count


def resolve_variadic_name(definitions: dict, name: str, hint: str | None = None):
    """
    Resolves a property name with variadic patterns to its corresponding definition in the schema.

    For properties with variadic names, it is necessary to check all possible definitions
    in the schema to find the unique and correct definition that matches the naming pattern.

    In the schema, definitions may include properties with placeholders that can match any
    naming segment. For example, the definition name 'FOO_bar' indicates that 'FOO' is a
    placeholder, meaning the actual name in the data could be anything like 'a_bar' or 'b_bar'.

    This function checks each definition to find matches based on the following criteria:

    1. **Exact Match**: If the provided name exactly matches a definition, that definition is returned.
    2. **Pattern Matching**: Definitions that match the naming pattern derived from the name are collected.
    3. **Candidate Collection**: All candidates matching the pattern are gathered.
    4. **Hint Prioritization**: Candidates containing the hint attribute are prioritized.
    5. **Similarity Assessment**: If multiple candidates exist, name similarity is used to determine the best match.

    In case of multiple quantities with identical template/variadic patterns, the following strategy
    is used:
        1. Check all quantities and collect all qualified quantities that match the naming pattern
           in a candidate dictionary, with the values being an integer indicating how big the match is.
        2. Use the optionally provided hint string, which shall be one of attribute names of the desired
            quantity. Check all candidates if this attribute exists. The existence of a hint attribute
            prioritize this quantity, and it will be put into a prioritized dictionary.
        3. If the prioritized candidate dictionary contains multiple matches, the strongest match is selected
           (i.e., the one with the highest integer returned by `get_namefit`.
        4. If no hint is provided, or no candidate has the hint attribute, check all quantities in the
            first candidate dictionary and select the strongest match.

    Args:
        definitions (dict): A dictionary of definitions where keys are property names and values are their corresponding definitions.
        name (str): The property name to resolve against the definitions.
        hint (Optional[str]): An optional hint that, if present in the candidate attributes, prioritizes that candidate.

    Returns:
        The best-matching definition based on the criteria.

    Raises:
        ValueError: If the definitions dictionary is empty or if no proper definition can be found for the given name.
    """
    # Check for an exact name match
    if name in definitions:
        return definitions[name]

    candidates = {}
    hint_candidates = {}

    for dname, definition in definitions.items():
        if not definition.variable:  # TODO: also if type does not match
            continue
        match_score = get_namefit(name, dname)
        if match_score >= 0:
            candidates[dname] = match_score
            # Check if the hint exists in the definition
            if hint and hint in definition.all_attributes:
                hint_candidates[dname] = match_score

    if len(candidates) == 0:
        raise ValueError(f'Cannot find a proper definition for name "{name}".')

    if hint_candidates:
        return definitions[max(hint_candidates, key=hint_candidates.get)]

    # If no hint candidates, find the best candidate from all
    if candidates:
        return definitions[max(candidates, key=candidates.get)]

    raise ValueError(f'Cannot find a proper definition for name "{name}".')


def validate_allowable_unit(
    dimensionality: str | None,
    allowable_list: str | list | pint.Unit | pint.Quantity,
) -> bool:
    """
    For a given list of units, e.g., ['m', 'cm', 'mm'], and a target NX unit token such as 'NX_LENGTH',
    this function checks the compatibility of the target unit with the list of units.

    Returns:
        True if ALL units are compatible with the unit token (dimensionality).
        False if at least one unit cannot be represented by the unit token (dimensionality).
    """
    if not dimensionality:
        return True

    if isinstance(allowable_list, str):
        if dimensionality in ('1', 'dimensionless'):
            return ureg.Quantity(1, allowable_list).dimensionless

        try:
            return ureg.Quantity(1, allowable_list).check(dimensionality)
        except KeyError:
            return False

    if isinstance(allowable_list, pint.Unit | pint.Quantity):
        if dimensionality in ('1', 'dimensionless'):
            return allowable_list.dimensionless

        return allowable_list.dimensionality == dimensionality

    for unit in allowable_list:
        if not validate_allowable_unit(dimensionality, unit):
            return False

    return True


def default_hash():
    """
    Returns a hash object using the designated hash algorithm.
    """
    return hashlib.new(__hash_method)


def split_python_definition(definition_with_id: str) -> tuple[list, str | None]:
    """
    Split a Python type name into names and an optional id.

    Example:
        my_package.my_section@my_id  ==> (['my_package', 'my_section'], 'my_id')

        my_package.my_section       ==> (['my_package', 'my_section'], None)

        my_package/section_definitions/0 ==> (['my_package', 'section_definitions/0'], None)
    """

    def __split(name: str):
        # The definition name must contain at least one dot which comes from the module name.
        # The actual definition could be either a path (e.g., my_package/section_definitions/0)
        # or a name (e.g., my_section).
        # If it is a path (e.g., a.b.c/section_definitions/0), after split at '.', the last segment
        # (c/section_definitions/0) contains the package name (c). It needs to be relocated.
        segments: list = name.split('.')
        if '/' in segments[-1]:
            segments.extend(segments.pop().split('/', 1))
        return segments

    if '@' not in definition_with_id:
        return __split(definition_with_id), None

    definition_names, definition_id = definition_with_id.split('@')
    return __split(definition_names), definition_id


def dict_to_named_list(data) -> list:
    if not isinstance(data, dict):
        return data

    results: list = []
    for key, value in data.items():
        if value is None:
            value = {}
        value.update(dict(name=key))
        results.append(value)
    return results


def camel_case_to_snake_case(obj: dict):
    for k, v in list(obj.items()):
        if k != k.lower() and k != k.upper() and '_' not in k:
            snake_case_key = re.sub(r'(?<!^)(?=[A-Z])', '_', k).lower()
            obj[snake_case_key] = v
            del obj[k]
            k = snake_case_key
        if isinstance(v, dict):
            obj[k] = camel_case_to_snake_case(v)
        if isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    obj[k][i] = camel_case_to_snake_case(item)
    return obj


def get_id_name(qualified_name: str) -> str:
    return str(qualified_name).split(':', maxsplit=1)[0]


def get_id(
    m_def,
    add_unit_value: bool = False,
    add_section_subtypes: bool = False,
    add_property_subtypes: bool = False,
) -> str:
    name = get_id_name(m_def.qualified_name())
    definition_id = m_def.definition_id
    if name == '*':
        name = get_id_name(str(m_def.m_def))
    schema_id = f'{SCHEMA_ENDPOINT}/{name}@{definition_id}'
    options = []
    if add_unit_value:
        options.append('unit_value=true')
    if add_section_subtypes:
        options.append('section_subtypes=true')
    if add_property_subtypes:
        options.append('property_subtypes=true')
    if options:
        schema_id = f'{schema_id}?{"&".join(options)}'
    return schema_id


def add_property_subtypes_check(schema) -> bool:
    if 'items' in schema:
        return add_property_subtypes_check(schema['items'])
    return 'anyOf' in schema or 'property_subtypes' in schema.get('$ref', '')


def metainfo_to_json_schema(
    m_def,
    add_unit_value: bool = False,
    add_section_subtypes: bool = False,
    add_property_subtypes: bool = False,
    exclude=None,
) -> dict[str, Any]:
    """
    Generate JSON Schema for this Section, referencing each property (BaseSections, Subsections or Quantity) via `$defs`.

    This function converts a metainfo Section definition into a JSON Schema format, allowing for
    the inclusion of various properties and definitions.

    Args:
        m_def (Section): The metainfo Section definition to convert to JSON Schema.
        add_section_subtypes (bool, optional): If True, include subtypes of the specified schema in the output. Default is False.
        add_property_subtypes (bool, optional): If True, include subtypes of the properties of the specified schema in the output. Default is False.
        add_unit_value (bool, optional): If True, include UnitValue definition in the schema for quantities with units. Default is False.
        exclude (list, optional): A list of qualified names to exclude from the schema. Default is None.
    Returns:
        dict: A JSON Schema representation of the metainfo Section definition.

    Notes:
        - Quantities are added inline to the schema properties. See `quantity_to_json_schema` for details.
        - SubSections and BaseSections are referenced via `$ref` using their schema ID.
        - BaseSections are handled via `allOf` to support inheritance.
        - The function excludes items whose qualified names match entries in the `exclude` list.
    """

    _defs: dict[str, Any] = {}
    _top_level: bool = True

    def _section_to_json_schema(
        section,
        add_unit_value=False,
        add_section_subtypes=False,
        add_property_subtypes=False,
        exclude=None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        # Function to get the schema for a section and returning its id and reference
        section_id_name = get_id_name(section.qualified_name())
        if section_id_name in exclude:
            return None, None
        # Prevents unlimited recursion due to circular references
        if section_id_name in _defs:
            section_schema = _defs[section_id_name]
        else:
            section_schema = _metainfo_to_json_schema(
                section,
                add_unit_value,
                add_section_subtypes=add_section_subtypes,
                add_property_subtypes=add_property_subtypes,
                exclude=exclude,
                _top_level=False,
            )
            section_schema.pop('$schema', None)
            _defs[section_id_name] = section_schema
        section_id = section_schema['$id']
        description = f'{section.name} {": " + section_schema.get("description", None) if section_schema.get("description", None) is not None else ""}'
        return section_id, {'$ref': section_id, 'description': description}

    def _child_section_to_json_schema(
        child_section,
        add_unit_value=False,
        add_section_subtypes=False,
        add_property_subtypes=False,
        exclude=None,
        repeats=False,
    ) -> dict[str, Any]:
        # Function to handle subsections
        schema: dict[str, Any] = {}
        any_of = []
        child_id_name = get_id_name(child_section.qualified_name())
        child_id, child_schema = _section_to_json_schema(
            child_section,
            add_unit_value,
            add_section_subtypes,
            add_property_subtypes,
            exclude,
        )
        if child_schema is None:
            return {}
        # Add inheriting sections to anyOf
        if add_property_subtypes:
            for inheriting_section in getattr(
                child_section, 'all_inheriting_sections', []
            ):
                inheriting_id, inheriting_schema = _section_to_json_schema(
                    inheriting_section,
                    add_unit_value,
                    add_section_subtypes,
                    add_property_subtypes,
                    exclude,
                )
                if inheriting_schema is not None:
                    any_of.append(inheriting_schema)
            any_of.append(child_schema)
        if add_property_subtypes and len(any_of) > 1:
            schema = (
                {'anyOf': any_of}
                if not repeats
                else {'type': 'array', 'items': {'anyOf': any_of}}
            )
        else:
            schema = (
                {'$ref': child_id}
                if not repeats
                else {'type': 'array', 'items': {'$ref': child_id}}
            )

        if child_section.description is not None:
            schema['description'] = child_section.description

        return schema

    def _metainfo_to_json_schema(
        m_def,
        add_unit_value=False,
        add_section_subtypes=False,
        add_property_subtypes=False,
        exclude=None,
        _top_level=True,
    ) -> dict[str, Any]:
        if exclude is None:
            exclude = []

        schema: dict[str, Any] = {
            '$schema': JSON_SCHEMA_VERSION,
            'title': m_def.name,
            'label': getattr(m_def, 'label', None),
            'description': getattr(m_def, 'description', None),
            'links': getattr(m_def, 'links', []),
            'aliases': getattr(m_def, 'aliases', []),
            # 'categories': getattr(m_def, 'categories', []),
            'type': 'object',
        }

        for k, v in list(schema.items()):
            if v is None or (isinstance(v, list) and not v):
                schema.pop(k)

        id_name = get_id_name(m_def.qualified_name())
        name = id_name
        definition_id = m_def.definition_id
        if name == '*':
            name = get_id_name(str(m_def.m_def))
        schema['$id'] = get_id(
            m_def,
            add_unit_value=add_unit_value,
            add_property_subtypes=add_property_subtypes,
        )
        _defs[id_name] = schema
        properties: dict = {}
        all_of: list = []
        any_of: list = []

        # Add Quantities inline
        for quantity in getattr(m_def, 'quantities', []):
            if get_id_name(quantity.qualified_name()) in exclude:
                continue
            quantity_schema = quantity_to_json_schema(
                quantity, add_unit_value, add_property_subtypes
            )
            quantity_schema.pop('$schema', None)
            quantity_schema.pop('$defs', None)
            properties[quantity.name] = quantity_schema

        # Handles the case where the section itself is a subsection of another section
        if (
            getattr(m_def, 'sub_section', None) is not None
            and get_id_name(m_def.sub_section.qualified_name()) not in exclude
        ):
            child_section = m_def.sub_section
            schema.update(
                _child_section_to_json_schema(
                    child_section,
                    add_unit_value,
                    add_section_subtypes=add_section_subtypes,
                    add_property_subtypes=add_property_subtypes,
                    exclude=exclude,
                    repeats=getattr(m_def, 'repeats', False),
                )
            )

        # Add SubSections as references
        for subsection in getattr(m_def, 'sub_sections', []):
            if get_id_name(subsection.qualified_name()) in exclude:
                continue
            prop_name = subsection.name
            child_section = subsection.sub_section

            properties[prop_name] = _child_section_to_json_schema(
                child_section,
                add_unit_value,
                add_section_subtypes=add_section_subtypes,
                add_property_subtypes=add_property_subtypes,
                exclude=exclude,
                repeats=subsection.repeats,
            )
            if subsection.description is not None:
                properties[prop_name]['description'] = subsection.description

            properties[prop_name]['$id'] = get_id(
                subsection,
                add_unit_value=add_unit_value,
                add_property_subtypes=add_property_subtypes,
            )

        # Add BaseSections as references in allOf
        for base_section in getattr(m_def, 'base_sections', []):
            base_section_id, base_section_schema = _section_to_json_schema(
                base_section,
                add_unit_value,
                add_section_subtypes,
                add_property_subtypes,
                exclude,
            )
            all_of.append(base_section_schema)

        if all_of:
            schema['allOf'] = all_of

        if properties:
            schema['properties'] = properties

        # Only include $defs at the top level to avoid redundancy in nested schemas
        if _top_level:
            # Special case of the m_def having inheriting sections
            if add_section_subtypes:
                for inheriting_section in getattr(m_def, 'all_inheriting_sections', []):
                    inheriting_id, inheriting_schema = _section_to_json_schema(
                        inheriting_section,
                        add_unit_value,
                        add_section_subtypes,
                        add_property_subtypes,
                        exclude,
                    )
                    if inheriting_schema is not None:
                        any_of.append(inheriting_schema)
            if any_of:
                _defs[id_name] = deepcopy(schema)
                any_of.append(
                    {
                        'description': f'{m_def.name} {": " + m_def.description if m_def.description is not None else ""}',
                        '$ref': schema['$id'],
                    }
                )
                schema['anyOf'] = any_of
                for k in ['properties', 'allOf', '$id']:
                    schema.pop(k, None)
            else:
                _defs.pop(id_name, None)
            if _defs:
                schema['$defs'] = _defs
            schema['$id'] = get_id(
                m_def,
                add_unit_value=add_unit_value,
                add_section_subtypes=add_section_subtypes,
                add_property_subtypes=add_property_subtypes,
            )
            return schema

        return schema

    return _metainfo_to_json_schema(
        m_def,
        add_unit_value,
        add_section_subtypes,
        add_property_subtypes,
        exclude,
        _top_level,
    )


def create_complex_type_schema(base_schema: dict[str, Any]) -> dict[str, Any]:
    """
    Create a JSON Schema for complex types, including both the real and imaginary parts.

    Args:
        base_schema (dict): The base schema for the complex type.

    Returns:
        dict: A JSON Schema representation for complex types.
    """
    complex_schema = to_json_schema_type(m_complex128())
    complex_schema['properties']['re'] = base_schema
    complex_schema['properties']['im'] = base_schema
    return complex_schema


def quantity_to_json_schema(
    quantity, add_unit_value: bool = False, add_property_subtypes: bool = False
) -> dict[str, Any]:
    """
    Generate a JSON Schema (Draft 2020-12) for this Quantity.

    Args:
        quantity : The Quantity to convert to JSON Schema.
        add_unit_value: If True, include a UnitValue definition in the schema for quantities with
                    units. Default is False.
    Always-included keywords:
        `"$schema"`  - The JSONSchema version
        `"$id"`      - Identifier as a resolvable URL

    Optional extras (added only when present):
        * `"title"`        - Name of the quantity/section
        * `"description"`  - Description of the quantity/section
        * `"unit"`         - Unit of a quantity as string
        * `"links"`        - List of links for explaining the quantity
        * `"aliases"`       - List of aliases for the quantity
        * `"categories"`    - List of categories for the quantity


    Type Data Keywords:
        * `"type"`                   - type of the quantity
        * Reference quantities are mapped to type strings.
        * Scalar quantities are mapped to the corresponding JSON type, e.g. `number`.
        * Arrays: `shape` is walked from left to right, wrapping each dimension
        in a nested ``{"type": "array", …, "items": {…}}``. Array shape is
        mapped into `minItems`/`maxItems` as follows:
            * `n` (int)              - `minItems = maxItems = n`
            * `"*"`                  - unbounded: no min/max keys
            * `"a.."`                - `minItems = a`
            * `"..b"`                - `maxItems = b`
            * `"a..b"`               - `minItems = a`, `maxItems = b`
            * any other string       - treated as `"*"`
        * Optional extras (added only when present):
            * `"default"`      - Default value of a quantity
            * `"enum"`         - List of allowed values for an Enum quantity
            * `"minimum"`      - Minimum value for a quantity (from ELN annotation)
            * `"maximum"`      - Maximum value for a quantity (from ELN annotation)
    """
    from nomad.metainfo.metainfo import Reference

    def shape_to_json_schema(
        shape: list, base_type: str, value_schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Recursively convert shape and base type to nested JSON Schema arrays."""

        def parse_dim(dim):
            if isinstance(dim, int):
                return {'minItems': dim, 'maxItems': dim}
            if dim == '*':
                return {}
            if isinstance(dim, str):
                if match := re.fullmatch(r'(\d+)?\.\.(\d+|\*)?', dim):
                    min_, max_ = match.groups()
                    out = {}
                    if min_ is not None:
                        out['minItems'] = int(min_)
                    if max_ and max_ != '*':
                        out['maxItems'] = int(max_)
                    return out

                # Assume `shape` to be name of another `Quantity` in the Section
                return parse_dim('*')

            raise TypeError(f'Unsupported shape dimension: {dim}')

        def build(dimensions: list) -> dict[str, Any]:
            if not dimensions:
                return value_schema.copy()
            dim_spec = parse_dim(dimensions[0])
            return {'type': 'array', **dim_spec, 'items': build(dimensions[1:])}

        return build(shape)

    def handle_default_value(quantity, base_type, add_unit_value):
        default_value = getattr(quantity, 'default', None)
        if default_value is not None:
            try:
                default_value = quantity.type.serialize(
                    quantity.type.normalize(default_value)
                )
            except Exception:
                if base_type == 'string':
                    if isinstance(default_value, list):
                        default_value = [str(v) for v in default_value]
                    else:
                        default_value = str(default_value)
                else:
                    raise ValueError(
                        f'Cannot serialize default value {default_value} for quantity {quantity.name}.'
                    )
        if (
            default_value is not None
            and add_unit_value
            and getattr(quantity, 'unit', None)
        ):
            default_value = {
                'value': default_value,
                'unit': str(quantity.unit),
            }
        return default_value

    schema: dict[str, Any] = {
        '$schema': JSON_SCHEMA_VERSION,
        'title': getattr(quantity, 'title', None),
        'description': getattr(quantity, 'description', None),
        'links': getattr(quantity, 'links', []),
        'aliases': getattr(quantity, 'aliases', []),
        # 'categories': getattr(quantity, 'categories', []),
    }

    schema['nomad_type'] = (
        quantity.type.standard_type()
        if hasattr(quantity.type, 'standard_type')
        else str(quantity.type.__class__.__name__)
    )
    for k, v in list(schema.items()):
        if v is None or (isinstance(v, list) and not v):
            schema.pop(k)

    # Determine base type
    if isinstance(quantity.type, Reference):
        base_schema = to_json_schema_type(m_str())
        try:
            reference_section = f'{quantity.type.target_section_def.qualified_name()}@{quantity.type.target_section_def.definition_id}'
        except Exception:
            reference_section = quantity.type.target_section_def.m_path()
        schema['reference_section_def'] = reference_section
    elif isinstance(quantity.type, m_complex):
        base_schema = to_json_schema_type(m_float32())
    else:
        base_schema = to_json_schema_type(quantity.type)

    base_type = base_schema.get('type', None)
    default_value = handle_default_value(quantity, base_type, add_unit_value)
    if default_value is not None:
        schema['default'] = default_value

    name = get_id_name(quantity.qualified_name())
    definition_id = quantity.definition_id
    if name == '*':
        name = get_id_name(str(quantity.m_def))
        # definition_id = quantity.m_def.definition_id
    schema['$id'] = get_id(
        quantity,
        add_unit_value=add_unit_value,
        add_property_subtypes=add_property_subtypes,
    )

    value_schema: dict[str, Any] = base_schema.copy()
    if getattr(quantity, 'unit', None):
        value_schema['unit'] = str(quantity.unit)
    if isinstance(quantity.type, MEnum):
        value_schema['enum'] = quantity.type._list

    eln_annotation = quantity.m_get_annotation('eln', None)
    if eln_annotation and eln_annotation.props:
        if eln_annotation.props.get('minValue', None) is not None:
            value_schema['minimum'] = eln_annotation.props.get('minValue', None)
        if eln_annotation.props.get('maxValue', None) is not None:
            value_schema['maximum'] = eln_annotation.props.get('maxValue', None)

    if not quantity.is_scalar:
        value_schema = shape_to_json_schema(quantity.shape, base_type, value_schema)

    if isinstance(quantity.type, m_complex):
        value_schema = create_complex_type_schema(value_schema.copy())

    if add_unit_value and getattr(quantity, 'unit', None):
        value_schema = {
            'type': 'object',
            'properties': {
                'value': value_schema.copy(),
                'unit': {'type': 'string', 'enum': [str(quantity.unit)]},
            },
        }

    schema.update(value_schema)
    return schema


class MDefNotFound(ValueError):
    """Raised when a metainfo definition cannot be found."""


class MDefWithoutMetainfo(ValueError):
    """Raised when a resolved object does not expose a metainfo definition."""


def resolve_m_def(m_def: str):
    """
    Resolve Section/SubSection/Quantity from qualified name (m_def) such as:
        package_name.schema_packages.calculations.MySchema
        package_name.schema_packages.calculations.MySection.sub_section
        package_name.schema_packages.calculations.quantity

    """
    warnings.warn(
        'resolve_m_def is deprecated; use nomad.schemas.get_schema instead.',
        DeprecationWarning,
        stacklevel=2,
    )

    from nomad.schemas import get_schema

    return get_schema(m_def)
