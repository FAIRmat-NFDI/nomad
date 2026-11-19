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

import bz2
import gzip
import json
import lzma
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from io import BytesIO
from typing import Any, Optional, cast

import h5py
import jmespath
import jmespath.visitor
import numpy as np
from jsonpath_ng.parser import JsonPathParser
from lxml import etree
from pydantic import BaseModel, Field, PrivateAttr, model_validator

from nomad.datamodel import EntryArchive
from nomad.datamodel.metainfo.annotations import Mapper as MapperAnnotation
from nomad.metainfo import MSection, SubSection
from nomad.parsing.file_parser import TextParser as TextFileParser
from nomad.parsing.parser import ArchiveParser
from nomad.units import ureg
from nomad.utils import get_logger

"""
Mapping parser framework for declarative data transformation and file format conversion.

This module provides a flexible, annotation-driven system for parsing various file formats
(XML, HDF5, JSON, text) and transforming data between representations. The framework supports:

Architecture:
    - Path-based data access using jmespath or jsonpath_ng expressions
    - Declarative mapping specifications via metainfo annotations or dictionaries
    - Transformation functions for complex data manipulations
    - Bidirectional conversion between file formats and Python dictionaries

Key Components:
    - :class:`MappingParser`: Abstract base class for format-specific parsers
    - :class:`Mapper`/:class:`Transformer`: Declarative mapping and transformation specification
    - :class:`Path`/:class:`Data`: Path resolution and data extraction abstractions
    - Specialized parsers: :class:`XMLParser`, :class:`HDF5Parser`, :class:`MetainfoParser`, :class:`TextParser`

Terminology:
    **path (lowercase)**:
        A string expression for navigating nested data structures. Can be:
        - jmespath syntax: 'a.b[0].c' (see :class:`JmespathParser`)
        - jsonpath_ng syntax: '$.a.b[0].c'
        Context determines which parser is used.

    **Path (class)**:
        :class:`Path` object that wraps a path string with resolution logic.
        Handles relative vs absolute paths, parent relationships, and computed
        attributes (relative_path, absolute_path, reduced_path).

    **mapper (lowercase)**:
        Generic term for a mapping specification. Can refer to:
        - Dictionary specification passed to :meth:`BaseMapper.from_dict`
        - String/tuple in annotation (e.g., `a_hdf5=Mapper(mapper='source.path')`)
        - :class:`BaseMapper` subclass instance (Mapper or Transformer)

    **Mapper (class)**:
        :class:`Mapper` class - a composite containing sub-mappers for nested transformations.
        Distinguished from :class:`Transformer` by having a `mappers` list.

    **Transformer (class)**:
        :class:`Transformer` class - executes a function on source data paths.
        Distinguished from :class:`Mapper` by having `function_name` and `function_args`.

    **data_object**:
        Format-specific representation of parsed data:
        - h5py.Group for HDF5
        - etree.Element for XML
        - MSection instance for metainfo
        - TextFileParser for text files

    **source/target** (in mapping context):
        - source: Where to read data from (input side of transformation)
        - target: Where to write data to (output side of transformation)
        In :class:`BaseMapper`: `source` is a :class:`Data` object specifying input,
        `target` is a :class:`Data` object specifying output path.

Data Flow Pipeline:
    The complete transformation from source file to target data_object follows these steps:

    **Step 1: Source Loading**
        source.filepath → source.load_file() → source.data_object
        - Parser reads file and creates format-specific representation
        - Example: HDF5Parser loads file into h5py.Group object

    **Step 2: Source Serialization**
        source.data_object → source.to_dict() → source.data
        - Format-specific object converted to dictionary
        - Attributes stored with attribute_prefix (@), values with value_key (__value)
        - Example: h5py.Group becomes nested dict with @attr keys

    **Step 3: Mapper Execution**
        source.data → mapper.get_data() → transformed_dict
        - Mapper recursively traverses source.data extracting values via paths
        - Transformers call functions on parser to reshape/compute data
        - Paths resolved via PathParser (jmespath or jsonpath_ng)
        - Result is dict with relative path keys (e.g., {'.positions': [...], '.species': [...]})

    **Step 4: Target Population**
        transformed_dict → target.set_data() → target.data
        - Transformed dict merged into target.data using Path.set_data()
        - Update modes control merge behavior (merge/append/replace)
        - Paths created automatically if missing

    **Step 5: Target Deserialization**
        target.data → target.from_dict() → target.data_object
        - Dictionary deserialized into format-specific object
        - Example: MetainfoParser populates MSection via m_set/m_add_sub_section
        - Example: HDF5Parser creates Groups/Datasets from dict

    Complete Example:
        >>> # Start with HDF5 file
        >>> with HDF5Parser(filepath='input.h5') as source:
        ...     # Step 1: h5py.Group loaded
        ...     # Step 2: Converted to {'calculation': {'energy': 1.5}}
        ...     with MetainfoParser(data_object=MySection()) as target:
        ...         # Mapper defined via annotations or from_dict
        ...         # Step 3: {'calculation.energy'} → {'.energy': 1.5 * ureg.eV}
        ...         source.convert(target)
        ...         # Step 4: {'.energy': ...} set in target.data
        ...         # Step 5: MySection.energy populated with 1.5 * ureg.eV

Usage Pattern:
    1. Load source data via a :class:`MappingParser` subclass
    2. Define mappings using annotations or :meth:`BaseMapper.from_dict`
    3. Convert source to target using :meth:`MappingParser.convert`
    4. Target parser receives transformed data in its native dictionary format

Example:
    >>> with HDF5Parser(filepath='data.h5') as source:
    ...     with MetainfoParser(data_object=MySection()) as target:
    ...         source.convert(target)

API Stability:
    **Parser Developer Interface** (stable):
        - :class:`MappingParser`: Abstract base class for file parsers
        - :class:`XMLParser`, :class:`HDF5Parser`, :class:`MetainfoParser`, :class:`TextParser`: Format-specific parsers
        - :meth:`MappingParser.convert`: Main conversion entry point
        - :meth:`MappingParser.parse`: Parse and build mapper from annotations
        - :class:`MapperAnnotation`: Annotation dataclass for metainfo
        - :meth:`BaseMapper.from_dict`: Construct mappers from dict specifications
        - :class:`Mapper`/:class:`Transformer` (declarative usage only): Use in annotations
          or dict specifications passed to :meth:`BaseMapper.from_dict`. Do not instantiate
          or modify instances directly.

        These classes provide stable interfaces for parsing files and defining mappings.
        Safe to use, extend, and rely upon across NOMAD versions.

        **Declarative usage example**:
            >>> # Stable: Using Mapper in annotation
            >>> class MySection(MSection):
            ...     energy = Quantity(type=float, a_hdf5=Mapper(mapper='calc.energy'))
            >>>
            >>> # Stable: Using dict specification
            >>> mapper_dict = {'mapper': 'calc.energy', 'target': '.energy'}
            >>> mapper = BaseMapper.from_dict(mapper_dict)  # Stable factory method
            >>>
            >>> # Unstable: Direct instantiation (avoid)
            >>> mapper = Mapper(mappers=[...])  # Internal API, may change

    **Internal, unstable APIs**:
        - :class:`JmespathOptions`, :class:`TreeInterpreter`, :class:`ParsedResult`: Jmespath customization internals
        - :class:`JmespathParser`, :class:`PathParser`: Path resolution internals
        - :class:`Path`, :class:`Data`: Internal data structures
        - :class:`BaseMapper`: Mapper base class (use :meth:`BaseMapper.from_dict` instead)
        - :class:`MetainfoMapper`, :class:`MetainfoBaseMapper`: Metainfo-specific internals

        These are implementation details. API may change without notice.
        Do not instantiate, subclass, or rely on these classes directly.

Extension Guide:
    To create a new format parser, subclass :class:`MappingParser` and implement:

    >>> class MyFormatParser(MappingParser):
    ...     def load_file(self) -> Any:
    ...         '''Load file into format-specific object.'''
    ...         return load_my_format(self.filepath)
    ...
    ...     def to_dict(self, **kwargs) -> dict[str | int, Any]:
    ...         '''Convert format-specific object to dictionary.
    ...
    ...         Use self.attribute_prefix (default '@') for attributes.
    ...         Use self.value_key (default '__value') for values with attributes.
    ...         '''
    ...         return {'key': self.data_object.extract_data()}
    ...
    ...     def from_dict(self, dct: dict[str, Any]) -> None:
    ...         '''Populate data_object from dictionary.'''
    ...         for key, value in dct.items():
    ...             self.data_object.set_field(key, value)
    ...
    ...     def build_mapper(self) -> BaseMapper:
    ...         '''Optional: Build mapper from format-specific metadata.
    ...
    ...         If not needed, inherit default implementation or return empty Mapper.
    ...         '''
    ...         return Mapper.from_dict({'mapper': []})

    Your parser will automatically inherit convert(), parse(), and path resolution.
"""

MAPPING_ANNOTATION_KEY = 'mapping'
"""Key for accessing mapping annotations in metainfo definitions.

Used by `MetainfoParser` to retrieve mapper specifications from quantity/section annotations.
Multiple annotation keys can coexist (e.g., 'hdf5', 'xml', 'json') for format-specific mappings.
"""

COMPRESSIONS = {
    b'\x1f\x8b\x08': ('gz', gzip.open),
    b'\x42\x5a\x68': ('bz2', bz2.open),
    b'\xfd\x37\x7a': ('xz', lzma.open),
}
"""Mapping of file magic numbers to compression formats and their open functions.

Used by `MappingParser.open` property to automatically detect and handle compressed files.
Maps the first 3 bytes of a file to (extension, open_function) tuples for gzip, bzip2, and xz.
"""


class JmespathOptions(jmespath.visitor.Options):
    """Extended options for custom jmespath operations.

    .. warning::
        Internal implementation detail. API may change without notice.
        Use the stable Parser Developer API instead.

    Extends the standard jmespath Options with custom flags for controlling
    search and modification behavior in `TreeInterpreter` and `ParsedResult`.

    Attributes:
        pop (bool): Whether to remove data from source during traversal (default: False).
        search (bool): Whether in search mode (True) or set mode (False) (default: True).
                      In search mode, missing paths return None. In set mode, missing
                      paths are created automatically.

    Args:
        **kwargs: Custom option attributes. Attributes matching parent class are passed
                 to `jmespath.visitor.Options`, others are stored as instance attributes.
    """

    def __init__(self, **kwargs):
        self.pop = False
        self.search = True

        for key in list(kwargs.keys()):
            if not hasattr(super(), key):
                setattr(self, key, kwargs[key])
                del kwargs[key]
        super().__init__(**kwargs)


LOGGER = get_logger(__name__)


def _normalize_update_mode_spec(update_mode: Any) -> dict[str, Any]:
    """Normalize update_mode into a nested spec dictionary."""
    if isinstance(update_mode, dict):
        return update_mode
    return {'__update_mode': update_mode}


def _get_update_mode(update_mode_spec: Any, key: str | None = None) -> Any:
    """Return update mode or child update mode spec for a key.

    If key is None, return the current level mode string (or None).
    If key is provided, return a child mode spec dict, inheriting the parent
    mode when no explicit key-specific entry is found.
    """
    spec = _normalize_update_mode_spec(update_mode_spec)
    if key is None:
        return spec.get('__update_mode')

    normalized_key = key.lstrip('.')
    child_spec = spec.get(normalized_key, spec.get(f'.{normalized_key}'))
    if child_spec is not None:
        return _normalize_update_mode_spec(child_spec)

    return _normalize_update_mode_spec(spec.get('__update_mode'))


class TreeInterpreter(jmespath.visitor.TreeInterpreter):
    """Extended jmespath interpreter supporting path creation and data modification.

    .. warning::
        Internal implementation detail. API may change without notice.
        Use the stable Parser Developer API instead.

    Extends standard jmespath traversal to enable:
    - Automatic creation of missing paths during set operations
    - Tracking of traversal context (stack, indices, keys) for data modification
    - Parent node tracking for conditional path creation logic

    The interpreter maintains a traversal stack to record the path taken through
    the data structure, enabling both read and write operations on nested data.

    Attributes:
        stack (list): Stack of dictionaries/lists traversed during path evaluation.
        indices (list[list[int]]): Indices accessed at each stack level (for arrays).
        keys (list[str]): Keys accessed at each stack level (for dictionaries).
        _parent_key (str): Internal key for storing parent node type in AST nodes.

    Dependencies:
        Uses: :class:`JmespathOptions` to control search vs. set behavior
        Used by: :class:`ParsedResult` for path traversal and data modification

    Example:
        Given data = {'a': {'b': [{'c': 1}, {'c': 2}]}} and path 'a.b[1].c':

        During traversal:
        - After 'a': stack=[{'b': [...]}, keys=['a'], indices=[[]]
        - After 'b': stack=[{'b': [...]}, [...]], keys=['a', 'b'], indices=[[], []]
        - After '[1]': stack=[{'b': [...]}, [...], {'c': 2}], keys=['a', 'b', 'c'], indices=[[], [1], []]

        This allows `ParsedResult.set()` to navigate back through the stack
        and modify the data at the correct nested location.

    Args:
        options (JmespathOptions | None): Options controlling search vs. set behavior.
    """

    def __init__(self, options=None):
        self.stack = []
        self._current_node = None
        self.current_stack = None
        self._parent = None
        self.nodes = []
        self.indices = []
        self.keys = []
        self._cache = []
        self._parent_key = '__parent'
        super().__init__(options)

    def visit(self, node: dict[str, Any], *args, **kwargs) -> Any:
        """Visit a node in the jmespath AST, annotating children with parent context.

        Marks each child node with its parent's type (e.g., 'index_expression', 'pipe')
        to enable context-aware decisions in specialized visit methods. The stack itself
        is populated by `visit_field`, not this method.

        Args:
            node (dict): AST node with 'type' and 'children' keys.
            *args: Passed to parent visit method.
            **kwargs: Passed to parent visit method.

        Returns:
            Any: Result of visiting the node.
        """
        node_type = node.get('type')
        for child in node.get('children'):
            if hasattr(child, 'get'):
                child[self._parent_key] = node_type

        value = super().visit(node, *args, **kwargs)
        node.pop(self._parent_key, None)
        return value

    def visit_field(self, node: dict[str, Any], value: dict[str, Any] | list) -> Any:
        """Visit a field access node, creating paths if in set mode.

        This is where the traversal stack is populated. In set mode (search=False),
        missing fields are automatically created. In search mode, missing fields return None.

        The field node represents a key access like 'b' in the jmespath expression 'a.b.c'.
        See `JmespathParser` for jmespath syntax details.

        Behavior:
        - Lists: Takes the last element (or creates one in set mode if empty)
        - Set mode + index_expression parent: Creates arrays for fields like 'a.b[0]'
        - Set mode: Creates empty dicts/arrays using setdefault
        - Updates stack/indices/keys for later modification by `ParsedResult.set()`

        Args:
            node (dict): AST node with 'value' key containing the field name.
            value (dict | list): Current data being traversed.

        Returns:
            Any | None: The field value, or None if not found (search mode) or not a dict.
        """
        parent = node.get(self._parent_key, None)
        # Handle list values: take last element or create one in set mode
        if isinstance(value, list):
            if not value and not self._options.search:
                value.append({})  # Create empty dict for path creation
            if not value:
                return None  # Empty list in search mode
            value = value[-1]  # Access last element for field lookup
        if not hasattr(value, 'get'):
            return None  # Value is not dict-like, cannot access fields
        value = cast(dict[str, Any], value)

        # In set mode, create missing fields automatically
        if not self._options.search:
            # If parent is index_expression (e.g., 'a.b[0]'), ensure field is a list
            if parent == 'index_expression' and not isinstance(
                value.get(node['value']), list
            ):
                value[node['value']] = []

            # Create field if missing: array for index_expression parent, dict otherwise
            value.setdefault(node['value'], [] if parent == 'index_expression' else {})

        # Track indices for nested structures: if current value is same as parent's
        # field value, we're accessing it at index 0
        if self.stack and not self.indices[-1]:
            parent_stack = self.stack[-1].get(self.keys[-1], {})
            if value == parent_stack or (
                isinstance(parent_stack, list) and value in parent_stack
            ):
                self.indices[-1] = [0]

        # Add current field to traversal stack (unless in comparator context)
        if parent != 'comparator':
            self.indices.append(
                []
            )  # Indices for this level (populated by visit_index/visit_slice)
            self.stack.append(value)  # Current dict/list
            self.keys.append(node['value'])  # Field name

        try:
            return value.get(node['value'])
        except AttributeError:
            return None

    def visit_index_expression(self, node: dict[str, Any], value: Any) -> Any:
        value = super().visit_index_expression(node, value)
        if node.get(self._parent_key) == 'pipe' and self.indices:
            self.indices[-1] = []
        return value

    def visit_index(self, node: dict[str, Any], value: list) -> Any:
        if not isinstance(value, list):
            return None

        index = node['value']
        n_value = len(value)
        # In search mode, out-of-bounds access returns None
        if self._options.search and index >= n_value:
            return None

        # In set mode, extend array to accommodate index
        # Calculate how many elements to add: handles both positive and negative indices
        n_target = abs(index) - n_value + (0 if index < 0 else 1)
        value.extend([{} for _ in range(n_target)])

        # Record which index was accessed for later stack navigation
        if self.indices:
            self.indices[-1] = [index]
        return value[index]

    def visit_slice(self, node: dict[str, Any], value: list) -> list | None:
        if not isinstance(value, list):
            return None

        # Parse slice notation: [start:stop:step]
        s = slice(*node['children'])
        n_value = len(value)
        # Calculate which indices the slice will access
        indices = list(range(s.start or 0, s.stop or n_value or 1, s.step or 1))
        if indices:
            max_index = max(np.abs(indices))
            min_index = min(indices)
            # Calculate how many elements needed to accommodate slice range
            n_target = (
                max_index
                - n_value
                + (0 if min_index < 0 and max_index == -min_index else 1)
            )

            # In search mode, out-of-bounds slice returns None
            if max_index >= n_value and self._options.search:
                return None

            # In set mode, extend array to fit slice
            value.extend([{} for _ in range(n_target)])
        # if isinstance(value, h5py.Group):
        #     return [g for g in value.values()][s]
        # Record which indices were accessed for later stack navigation
        self.indices[-1] = indices
        return value[s]


class ParsedResult(jmespath.parser.ParsedResult):
    """Extended parsed jmespath expression supporting bidirectional data access.

    .. warning::
        Internal implementation detail. API may change without notice.
        Use the stable Parser Developer API instead.

    Extends the standard jmespath `ParsedResult` to support both search (read) and
    set (write) operations on nested data structures. Uses `TreeInterpreter` to
    track traversal context for modification.

    The set operation works by:
    1. Traversing the path using :class:`TreeInterpreter` (which records stack/indices/keys)
    2. Navigating back through the stack to the modification point
    3. Setting the new data at the correct nested location
    4. Optionally removing (popping) the modified data from the structure

    Dependencies:
        Uses: :class:`TreeInterpreter` for path traversal with stack tracking
        Uses: :class:`JmespathOptions` to configure search vs. set mode
        Used by: :class:`JmespathParser` (returns ParsedResult from parse())
        Used by: :class:`PathParser` (calls search/set methods)

    Example:
        >>> parser = JmespathParser()
        >>> result = parser.parse('a.b[1].c')
        >>> data = {'a': {'b': [{'c': 3}, {'c': 4}]}}
        >>> result.search(data)  # Returns: 4
        # Index [1] selects the second element {'c': 4}, then '.c' accesses its value
        >>> result.set(data, 99)  # Sets data['a']['b'][1]['c'] = 99
        >>> data  # {'a': {'b': [{'c': 3}, {'c': 99}]}}
    """

    def _set_value(
        self, value: dict[str, Any], options: JmespathOptions, data: Any
    ) -> tuple[Any, Any]:
        """Internal method to traverse path and optionally set/pop data.

        Uses `TreeInterpreter` to visit the parsed expression tree, building up
        the traversal stack. Then navigates the stack to set or remove data.

        Args:
            value: The dictionary to traverse.
            options: Controls search vs. set mode and pop behavior.
            data: New data to set at the path location. If None and not popping,
                 only traversal is performed (search mode).

        Returns:
            tuple[Any, Any | list[Any]]: (result found at path, affected values).
            - result: The data found at the path during traversal
            - affected values: List of values at modified locations after setting
              (or removed values if popping). Single value if one location affected,
              list if multiple (e.g., slice operations). Empty list if data is None.
        """
        # Traverse path, building stack of dicts/lists visited
        self._interpreter = TreeInterpreter(options=options)
        result = self._interpreter.visit(self.parsed, value)

        values: list[Any] = []
        # If just searching (not setting/popping), return the found value
        if not options.pop and data is None:
            return result, values

        # Filter stack to only include levels that contain actual values to modify
        stack, stack_indices, stack_keys = [], [], []
        for n, s in enumerate(self._interpreter.stack):
            # Include if this is the final level (where data sits)
            add = s == self._interpreter.stack[-1]
            if not add:
                # Or include if the field value is a leaf (not a dict/list)
                val = s[self._interpreter.keys[n]]
                add = val and not hasattr(
                    val[0] if isinstance(val, list) else val, 'get'
                )
            if add:
                stack.append(s)
                stack_indices.append(self._interpreter.indices[n])
                stack_keys.append(self._interpreter.keys[n])

        # Set data at each level in the filtered stack
        for n, indices in enumerate(stack_indices):
            # If data is list matching stack levels, use corresponding element
            d = (
                data[n]
                if isinstance(data, list)
                and len(data) > 1
                and len(data) == len(stack_indices)
                else data
            )
            # No indices: setting a simple field (e.g., 'a.b.c')
            if not indices:
                stack[n][stack_keys[n]] = d
                v = (
                    stack[n][stack_keys[n]]
                    if not options.pop
                    else stack[n].pop(stack_keys[n])
                )
                values.append(v)
                continue
            # Indices present: setting array elements (e.g., 'a.b[0:3].c')
            map_data = isinstance(d, list) and len(d) == len(indices)
            # Iterate backwards to avoid index shifting when popping
            for nd in range(len(indices) - 1, -1, -1):
                index = indices[nd]
                # If data list matches indices, map element-wise; otherwise broadcast same value
                stack[n][stack_keys[n]][index] = d[nd] if map_data else d
                v = (
                    stack[n][stack_keys[n]][index]
                    if not options.pop
                    else stack[n][stack_keys[n]].pop(index)
                )
                values.append(v)

        return result, values[0] if len(values) == 1 else values

    def search(self, value: dict[str, Any], **kwargs) -> Any:
        """Search for data at the jmespath expression path.

        Traverses the data structure following the parsed jmespath expression.
        Returns None if the path doesn't exist.

        Args:
            value: Dictionary to search in.
            **kwargs: Additional options passed to `JmespathOptions`.

        Returns:
            Any: Data found at the path, or None if path doesn't exist.
        """
        options = JmespathOptions(search=True, **kwargs)
        return self._set_value(value, options, None)[0]

    def set(self, value: dict[str, Any], data: Any, **kwargs) -> Any | list[Any]:
        """Set data at the jmespath expression path, creating missing paths.

        Traverses the data structure following the parsed jmespath expression,
        automatically creating any missing intermediate dictionaries or arrays.
        Sets the provided data at the final location.

        Args:
            value: Dictionary to modify (modified in-place).
            data: Data to set at the path location.
            **kwargs: Additional options (e.g., pop=True to remove after setting).

        Returns:
            Any | list[Any]: The data now at the path location(s). Returns a list
            if the path affects multiple locations (e.g., slice), single value otherwise.
        """
        options = JmespathOptions(search=False, **kwargs)
        return self._set_value(value, options, data)[1]


class JmespathParser(jmespath.parser.Parser):
    """Extended jmespath parser with bidirectional data access (search and set).

    .. warning::
        Internal implementation detail. API may change without notice.
        Use the stable Parser Developer API instead.

    Extends the standard jmespath parser to return `ParsedResult` objects that support
    both reading (search) and writing (set) operations on nested data structures.

    Jmespath Path Syntax:
        Jmespath expressions navigate nested dictionaries and lists using a query language.
        Common patterns used in this framework:

        - Field access: `a.b.c` accesses nested dict keys
        - Array indexing: `a.b[0]` accesses first element
        - Negative indexing: `a.b[-1]` accesses last element
        - Slicing: `a.b[0:3]` accesses elements 0, 1, 2
        - Filtering: `a.b[?@.c=='value']` filters array by condition
        - Pipes: `a.b | [0]` chains operations
        - Current node: `@` refers to current data in expressions

    Examples:
        >>> parser = JmespathParser()
        >>> data = {'systems': [{'atoms': [{'symbol': 'H'}, {'symbol': 'O'}]}]}
        >>> parser.parse('systems[0].atoms[1].symbol').search(data)
        'O'
        >>> parser.parse('systems[0].atoms[0].symbol').set(data, 'C')
        'C'

    See Also:
        - https://jmespath.org for complete jmespath specification
        - `ParsedResult` for search/set operation details
        - `TreeInterpreter` for path creation behavior in set mode
    """

    def parse(self, expression: str) -> ParsedResult:
        """Parse a jmespath expression into a `ParsedResult` with search/set support.

        Args:
            expression: Jmespath path expression string.

        Returns:
            ParsedResult: Parsed expression supporting search() and set() operations.
        """
        parsed_result = super().parse(expression)
        return ParsedResult(parsed_result.expression, parsed_result.parsed)


class PathParser(BaseModel):
    """Abstraction over different path query languages (jmespath, jsonpath_ng).

    .. warning::
        Internal implementation detail. API may change without notice.
        Use the stable Parser Developer API instead.

    Provides a unified interface for path-based data access regardless of the
    underlying query language. Acts as a factory/adapter that delegates to the
    appropriate parser implementation based on `parser_name`.

    Relationship to other parsers:
        - Creates and uses `JmespathParser` when parser_name='jmespath' (default)
        - Creates and uses `JsonPathParser` when parser_name='jsonpath_ng'
        - Used by `Path` and `Data` objects to perform actual path operations

    Attributes:
        parser_name: Name of the parser backend ('jmespath' or 'jsonpath_ng').
    """

    parser_name: str = Field(
        'jmespath', description="""Name of the parser to perform parsing."""
    )

    def get_data(self, path: str, source: dict[str, Any], **kwargs) -> Any:
        """Retrieve data from source using the configured parser.

        Args:
            path: Path expression in the configured parser's syntax.
            source: Dictionary to search.
            **kwargs: Parser-specific options (e.g., pop=True for jmespath).

        Returns:
            Any: Data found at path, or None if not found or parser unsupported.
        """
        if self.parser_name == 'jmespath':

            def _get(path, source, **kwargs):
                return JmespathParser().parse(path).search(source, **kwargs)

            return _get(path, source, **kwargs)
        elif self.parser_name == 'jsonpath_ng':

            def _get(path, source, **kwargs):
                parser = JsonPathParser().parse(path)
                results = [match.value for match in parser.find(source)]
                if kwargs.get('pop'):
                    # TODO is find and filter somehow can be performed simultaneously
                    parser.filter(lambda v: True, source)
                return results[0] if len(results) == 1 else results

            return _get(path, source, **kwargs)

        return None

    def set_data(self, path: str, target: dict[str, Any], data: Any, **kwargs) -> Any:
        """Set data in target at the specified path using the configured parser.

        Args:
            path: Path expression in the configured parser's syntax.
            target: Dictionary to modify (modified in-place).
            data: Data to set at the path location.
            **kwargs: Parser-specific options (e.g., pop=True for jmespath).

        Returns:
            Any: The data at the path location after setting, or None if parser unsupported.
        """
        if self.parser_name == 'jmespath':

            def _set(path, target, data, **kwargs):
                return JmespathParser().parse(path).set(target, data, **kwargs)

            return _set(path, target, data, **kwargs)

        elif self.parser_name == 'jsonpath_ng':

            def _set(path, target, data, **kwargs):
                return JsonPathParser().parse(path).update(target, data)

            return _set(path, target, data)

        return None


class Path(BaseModel, validate_assignment=True):
    """Path specification with support for relative and absolute resolution.

    .. warning::
        Internal implementation detail. API may change without notice.
        Use the stable Parser Developer API instead.

    Wraps path expressions (jmespath or jsonpath_ng) with automatic resolution of
    relative paths based on parent context. Supports both data retrieval and
    modification with various update modes.

    Attributes (example: parent=Path(path='a.b'), child=Path(path='.c[0]', parent=parent)):
        path:           User-defined path string                           -> '.c[0]'
        parent:         Parent path for relative resolution                -> Path(path='a.b')
        relative_path:  Path with leading '.' removed                      -> 'c[0]'
        absolute_path:  Full path from root (parent + relative)            -> 'a.b.c[0]'
        reduced_path:   Absolute path without indices/filters              -> 'a.b.c'
        parser:         PathParser instance for path operations

    Dependencies:
        Uses: :class:`PathParser` to execute get_data/set_data operations
        Used by: :class:`Data` to wrap path access
        Used by: :class:`Transformer` for function_args specification
        Used by: :meth:`MappingParser.set_data` for recursive path setting
    """

    path: str = Field('', description="""User-defined path to the data.""")
    parent: Optional['Path'] = Field(None, description="""Parent path.""")
    relative_path: str = Field('', description="""Relative path to the data.""")
    absolute_path: str = Field('', description="""Absolute path to the data.""")
    reduced_path: str = Field('', description="""Reduced absolute path.""")
    parser: PathParser = Field(
        PathParser(), description="""The parser to use to search and set data."""
    )

    @model_validator(mode='before')
    def get_relative_path(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Pydantic validator to compute path attributes before model construction.

        The `@model_validator(mode='before')` decorator runs this method before field
        assignment, allowing in-place modification of the values dictionary to compute
        derived path attributes.

        Computation steps:
        1. Strip leading '.' from path to get relative_path
        2. Join parent.absolute_path with relative_path to get absolute_path
        3. Remove indices/filters from absolute_path to get reduced_path

        Validation behavior:
            - **Timing**: Runs BEFORE field assignment and type checking
            - **Trigger**: Called during Path(...) construction or field assignment with validate_assignment=True
            - **Error propagation**: Exceptions raised here become pydantic.ValidationError
            - **Return requirement**: Must return the (possibly modified) values dict

        Common validation errors:
            - AttributeError: If parent is not None but lacks absolute_path
              Solution: Ensure parent is a valid Path object
            - TypeError: If path is not a string
              Solution: Provide path as string, not Path object

        Example:
            Input:  values = {'path': '.c[0]', 'parent': Path(path='a.b')}
            Step 1: relative_path = 'c[0]'
            Step 2: absolute_path = 'a.b' + '.' + 'c[0]' = 'a.b.c[0]'
            Step 3: reduced_path = 'a.b.c'  (removes '[0]')
            Output: values updated in-place with these computed fields

        Args:
            values: Dictionary of field values being validated/assigned.

        Returns:
            dict[str, Any]: Modified values dictionary with computed path fields added.

        Raises:
            AttributeError: If parent is not None but lacks absolute_path attribute.
        """
        relative_path = values.get('path', '')
        parent = values.get('parent')
        relative_path = re.sub(r'(?:^|(?<=\s))\.', '', relative_path)
        values['relative_path'] = relative_path

        absolute_path = relative_path
        if parent:
            segments = [parent.absolute_path, absolute_path]
            absolute_path = '.'.join([s for s in segments if s != '@' and s])
        values['absolute_path'] = absolute_path

        values['reduced_path'] = re.sub(r'\[.+?\]|\|', '', absolute_path)

        return values

    def is_relative_path(self) -> bool:
        """Check if this path is relative (has leading '.' or a parent).

        Returns:
            bool: True if path is relative, False if absolute.
        """
        return self.relative_path != self.path or self.parent is not None

    def get_data(self, source: dict[str, Any], **kwargs) -> Any:
        """Retrieve data from source at this path's location.

        Args:
            source: Dictionary to search.
            **kwargs: Options for parser (e.g., default=value to return if not found).

        Returns:
            Any: Data at path, or kwargs['default'] if path not found or error occurs.
        """
        try:
            return self.parser.get_data(self.relative_path, source, **kwargs)
        except Exception:
            return kwargs.get('default')

    def set_data(self, data: Any, target: dict[str, Any], **kwargs) -> Any:
        """Set data at this path's location with various update strategies.

        Sets data at the path location, merging with existing data according to
        update_mode. The target dictionary is modified in-place.

        Update modes:
            String modes (global behavior):
                - 'replace': Completely replace existing data at this path
                - 'append': Keep existing data if present, otherwise use new data
                - 'merge' (default): Recursively merge dictionaries, align list elements
                - 'merge@start' (lists): Align source[0] with target[0]
                - 'merge@last' (lists): Align source[-1] with target[-1]
                - 'merge@N' (lists): Align source[N] with target[0] (negative N supported)
                - 'merge@end' (lists): Alias for 'merge@last'

            Nested dictionary mode (per-key control):
                The framework automatically builds nested update_mode specifications from
                mapper hierarchies via `build_update_mode_tree()`. This allows per-subsection
                control during multi-pass parsing.

                Internal structure (automatically constructed)::

                    {
                        '__update_mode': 'merge',     # Mode for this level
                        'key_name': {                 # Per-key override
                            '__update_mode': 'append',
                            'nested_key': {'__update_mode': 'replace'}
                        }
                    }

                The `__update_mode` key sets the mode for the current level. Child keys
                inherit parent mode unless explicitly overridden.

                Use case: Multi-pass parsing with polymorphic subsections

                Users specify string update_mode on individual annotations. The framework
                automatically constructs the nested tree:

                >>> # Schema annotations (user-defined)
                >>> add_mapping_annotation(Simulation.model_method, 'key1', '.@', update_mode='merge')
                >>> add_mapping_annotation(KMesh.m_def, 'key1', '.kmesh', update_mode='append')
                >>> add_mapping_annotation(KDensity.m_def, 'key1', '.kdensity', update_mode='append')
                >>>
                >>> # Framework automatically constructs nested tree and applies during convert()
                >>> source1.convert(target)  # First pass: KMesh instance appended
                >>> source2.convert(target)  # Second pass: KDensity instance appended
                >>> # Result: Both KMesh and KDensity instances preserved in list

        List merge behavior:
            For 'merge@last': start = len(source) - len(target), so if source=[1,2,3,4,5]
            and target=[A,B], start=3, merging source[3] with target[0] and source[4] with
            target[1]. Non-overlapping source elements (0,1,2) are inserted into target.

            Out of bounds: Elements outside the merge window are inserted at their source
            index position, growing the target list.

        Args:
            data: New data to set at the path.
            target: Dictionary to modify (modified in-place).
            **kwargs: Options including update_mode (str | dict), passed to parser.set_data.

        Returns:
            Any: The data at the path location after setting and merging.

        Examples:
            >>> # Basic merge modes
            >>> path = Path(path='a.b')
            >>> target = {'a': {'b': [10, 20]}}
            >>> path.set_data([1, 2, 3, 4, 5], target, update_mode='merge@last')
            >>> target  # {'a': {'b': [1, 2, 3, 10, 20]}}

            >>> # Nested mode (typically passed by framework, not users)
            >>> # This example shows internal mechanics only
            >>> target = {
            ...     'items': [{
            ...         'entries': [{'kind': 'existing', 'value': 1}]
            ...     }]
            ... }
            >>> nested_spec = {
            ...     '__update_mode': 'merge',
            ...     '.entries': {'__update_mode': 'append'}
            ... }
            >>> path.set_data(
            ...     [{'entries': [{'kind': 'new', 'label': 'x'}]}],
            ...     target,
            ...     update_mode=nested_spec
            ... )
            >>> # Result: entries list now has both 'existing' and 'new' entries
        """
        cur_data = self.get_data(target, **kwargs)
        update_mode = _normalize_update_mode_spec(kwargs.get('update_mode'))
        path = self.relative_path

        def update(current: Any, incoming: Any, update_mode_spec: Any):
            mode = _get_update_mode(update_mode_spec)
            # Type mismatch: keep incoming if append mode, otherwise use current
            if not isinstance(current, type(incoming)):
                return (
                    incoming if mode == 'append' and incoming is not None else current
                )

            # Dictionary merge: recursively merge all keys
            if isinstance(current, dict):
                if mode != 'replace':
                    for key in list(current.keys()):
                        child_mode = _get_update_mode(update_mode_spec, key)
                        # Recursively update each key (prefix with '.' for relative path)
                        relative_key = key if key.startswith('.') else f'.{key}'
                        incoming[relative_key] = update(
                            current.get(key), incoming.get(relative_key), child_mode
                        )
                return incoming

            # List merge: complex alignment logic based on merge_at position
            if isinstance(current, list):
                merge = re.match(r'merge(?:@(.+))*', mode or '')
                if merge:
                    merge_at = merge.groups()[0]
                    # Calculate starting index in incoming for alignment.
                    if not merge_at or merge_at == 'start':
                        start = 0  # Align current[0] with target[0]
                    elif merge_at in ('last', 'end'):
                        start = len(incoming) - len(
                            current
                        )  # Align current[-1] with target[-1]
                    else:
                        if not re.fullmatch(r'-?\d+', merge_at):
                            raise ValueError(
                                f'Invalid merge mode "{mode}": merge index must be an integer, '
                                'or one of "start" or "last".'
                            )
                        start = int(merge_at)  # Align current[N] with target[0]

                    # If current starts before incoming index 0, preserve that left segment
                    # by prepending it, then continue merging at the shifted index.
                    current_to_merge = current
                    if start < 0:
                        left_overhang = min(-start, len(current_to_merge))
                        if left_overhang:
                            incoming[:0] = current_to_merge[:left_overhang]
                            current_to_merge = current_to_merge[left_overhang:]
                            start = left_overhang

                    for n, d in enumerate(current_to_merge):
                        target_index = start + n
                        # If within merge window, recursively merge with target element
                        if 0 <= target_index < len(incoming):
                            incoming[target_index] = update(
                                d, incoming[target_index], update_mode_spec
                            )
                        else:
                            # Outside merge window, insert existing element at aligned index
                            insert_at = max(0, min(target_index, len(incoming)))
                            incoming.insert(insert_at, d)
                elif mode == 'append':
                    # Append mode: prepend all current elements
                    for n, d in enumerate(current):
                        incoming.insert(n, update(d, {}, update_mode_spec))
                return incoming

            # Scalar values: keep incoming if append mode, otherwise use current
            return incoming if mode == 'append' and incoming is not None else current

        new_data = self.parser.set_data(path, target, data, **kwargs)

        update(cur_data, new_data, update_mode)

        return new_data


Path.model_rebuild()


class Data(BaseModel, validate_assignment=True):
    """Wrapper for data access via either a direct path or a transformation function.

    .. warning::
        Internal implementation detail. API may change without notice.
        Use the stable Parser Developer API instead.

    Provides a unified interface for data extraction that can be either a simple
    path lookup or a complex transformation involving multiple source paths and
    a function. Automatically resolves relative paths based on parent context.

    Usage patterns:
        - Simple path: `Data(path=Path(path='a.b.c'))` - direct data access
        - Transformation: `Data(transformer=Transformer(...))` - computed data access
        - Auto-inferred path: If transformer has one arg, that becomes the path

    Attributes:
        path: Path to the data (may be auto-set from transformer's single argument).
        transformer: Transformer for computed data extraction (e.g., reshaping arrays).
        parent: Parent path for relative path resolution.
        path_parser: Parser for path operations (propagated to path/transformer args).

    Dependencies:
        Uses: :class:`Path` for direct path access
        Uses: :class:`Transformer` for computed transformations
        Uses: :class:`PathParser` (propagated to children)
        Used by: :class:`BaseMapper` for source/target specification
    """

    path: Path | None = Field(None, description="""Path to the data.""")
    transformer: 'Transformer | None' = Field(
        None, description="""Transformer to extract data."""
    )
    parent: Path | None = Field(None, description="""Parent path.""")
    path_parser: PathParser | None = Field(
        None, description="""Parser used to search and set data."""
    )

    @model_validator(mode='before')
    def set_attributes(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Pydantic validator to configure path, parent, and parser relationships.

        Propagates parent and parser settings to nested path/transformer arguments,
        and auto-infers path from transformer if it has a single argument.

        Configuration steps:
        1. If no path but transformer exists: set path from transformer's single arg
           or use '@' (current node) for multi-arg transformers
        2. If parent exists: propagate to all relative paths in transformer args
        3. If path_parser exists: propagate to path and all transformer args

        Validation behavior:
            - **Timing**: Runs BEFORE field assignment (mode='before')
            - **Side effects**: Modifies nested objects (Path, Transformer args) in-place
            - **Error propagation**: Errors from nested Path/Transformer validators propagate
              as pydantic.ValidationError with nested error details

        Args:
            values: Dictionary of field values being validated.

        Returns:
            dict[str, Any]: Modified values with configured relationships.

        Raises:
            pydantic.ValidationError: If nested Path or Transformer construction fails.
        """
        if values.get('path') is None and values.get('transformer'):
            transformer = values['transformer']
            if len(transformer.function_args) == 1:
                values['path'] = transformer.function_args[0]
            else:
                values['path'] = Path(path='@')

        if values.get('parent'):
            if values.get('transformer'):
                for arg in values['transformer'].function_args:
                    if arg.is_relative_path():
                        arg.parent = values['parent']
            if values.get('path') and values['path'].is_relative_path():
                values['path'].parent = values['parent']

        if values.get('path_parser'):
            if values.get('path'):
                values['path'].parser = values['path_parser']
            if values.get('transformer'):
                for arg in values['transformer'].function_args:
                    arg.parser = values['path_parser']

        return values

    def get_data(
        self,
        source_data: dict[str, Any],
        parser: 'MappingParser | None' = None,
        **kwargs,
    ) -> Any:
        """Extract data via transformer or direct path lookup.

        Args:
            source_data: Dictionary to extract from (for relative paths).
            parser: MappingParser instance providing absolute data root and functions.
            **kwargs: Options passed to path/transformer get_data methods.

        Returns:
            Any: Transformed data (if transformer) or raw path data (if simple path).
        """
        if self.transformer:
            value = self.transformer.get_data(source_data, parser, **kwargs)
            return self.transformer.normalize_data(value)

        if self.path:
            return self.path.get_data(
                source_data if self.path.is_relative_path() else parser.data, **kwargs
            )


class BaseMapper(BaseModel):
    """Base class for mapping specifications between source and target data.

    .. warning::
        Internal implementation detail. API may change without notice.
        Do not instantiate or subclass directly. Use :meth:`BaseMapper.from_dict`
        to construct mappers from dictionary specifications or metainfo annotations.

    Provides the foundation for declarative data transformation through `Mapper`
    (nested mappers) and `Transformer` (function-based transformations). Mappers
    are typically constructed via the `from_dict` factory method from dictionary
    specifications or metainfo annotations.

    Attributes:
        source: Where to read data from (path or transformer).
        target: Where to write data to (path).
        indices: Which array elements to process (list of ints or function name string).
        order: Execution priority (0=container/Mapper, 1=transformation/Transformer).
        remove: Whether to remove data from source after reading.
        cache: Whether to cache the transformation result.
        all_paths: Internal list of all absolute paths for remove optimization.
    """

    source: 'Data | None' = Field(None, description="""Source data.""")
    target: 'Data | None' = Field(None, description="""Target data.""")
    indices: list[int] | str | None = Field(
        None, description="""List of indices of data to include."""
    )
    order: int | None = Field(None, description="""Execution order.""")
    remove: bool | None = Field(None, description="""Remove data from source.""")
    cache: bool | None = Field(None, description="""Store the result of the mapper.""")
    all_paths: list[str] = Field(
        [], description="""List of all unindexed abs. paths."""
    )
    update_mode: str = Field(
        'merge', description="""Mode to update target with source."""
    )
    # Internal attribute to store child mappers for multiple mapper sources
    _child_mappers: dict[str, 'BaseMapper'] = PrivateAttr({})

    def get_data(self, source_data: Any, parser: 'MappingParser', **kwargs) -> Any:
        """Extract data from source (implemented by subclasses).

        Args:
            source_data: Source dictionary to extract from.
            parser: MappingParser providing functions and absolute data root.
            **kwargs: Additional options (e.g., remove, debug).

        Returns:
            Any: Extracted/transformed data.
        """
        return None

    def normalize_data(self, data: Any) -> Any:
        """Post-process extracted data (e.g., apply units). Override in subclasses.

        Args:
            data: Raw extracted data.

        Returns:
            Any: Normalized data.
        """
        return data

    def __setitem__(self, key, value):
        self._child_mappers[key] = value

    def __getitem__(self, key):
        return self._child_mappers.get(key)

    def __iter__(self):
        return iter(self._child_mappers.values() or [self])

    def __len__(self):
        return len(self._child_mappers or [self])

    @staticmethod
    def from_dict(
        dct: dict[str, Any], parent: 'BaseMapper | None' = None
    ) -> 'BaseMapper':
        """Factory method to construct mapper objects from dictionary specifications.

        Converts various dictionary formats into `Transformer` or `Mapper` instances.
        The dictionary structure determines which mapper type is created:

        Dictionary keys:
            source (str | Path | tuple | Data): Where to read data
                - str/Path: Direct path to source data
                - tuple: (function_name, [arg_paths], {kwargs}) for transformation
                - Data: Pre-configured Data object
            target (str | Path | Data): Where to write data (similar formats as source)
            mapper: Determines the mapper type created:
                - str/Path: Creates Transformer with identity function
                - (func_name, [arg_paths]) or (func_name, [arg_paths], {kwargs}): Transformer
                - [dict, ...]: Creates nested Mapper with sub-mappers
            path: Shorthand for mapper (alternative to 'mapper' key)
            function_name, function_args: Alternative to tuple format in mapper
            indices (list[int] | str): Which array elements to process
            remove (bool): Remove source data after reading
            cache (bool): Cache transformation results
            path_parser (str): Parser type ('jmespath' or 'jsonpath_ng')

        Args:
            dct: Dictionary specification of the mapper.
            parent: Parent mapper for inheriting source/target context.

        Returns:
            BaseMapper: Constructed Transformer or Mapper instance.

        Examples:
            # Simple path mapping (identity):
            >>> BaseMapper.from_dict({'mapper': 'a.b', 'target': 'c.d'})
            # Returns Transformer with identity function from 'a.b' to 'c.d'

            # Transformation:
            >>> BaseMapper.from_dict({
            ...     'mapper': ('reshape', ['a.b', 'a.n_rows']),
            ...     'target': 'c.matrix'
            ... })
            # Returns Transformer calling parser.reshape(a.b, a.n_rows) -> c.matrix

            # Nested mapping:
            >>> BaseMapper.from_dict({
            ...     'source': 'input.data',
            ...     'mapper': [
            ...         {'mapper': 'x', 'target': 'position_x'},
            ...         {'mapper': 'y', 'target': 'position_y'}
            ...     ]
            ... })
            # Returns Mapper with two Transformer sub-mappers
        """
        paths: dict[str, Data] = {}
        path_parser = dct.get('path_parser')

        for ptype in ['source', 'target']:
            path = dct.get(ptype)
            if isinstance(path, str):
                path_obj = Data(path=Path(path=path))
            elif isinstance(path, tuple):
                args = [Path(path=p) for p in path[1]]
                path_obj = Data(
                    transformer=Transformer(function_name=path[0], function_args=args)
                )
                if len(path) == 3:
                    path_obj.transformer.function_kwargs = path[2]
                path_obj.transformer.cache = dct.get('cache')
            elif isinstance(path, Data):
                path_obj = path
            else:
                path_obj = None

            if path_obj:
                parent_path = getattr(parent, ptype, None)
                if parent_path is not None:
                    path_obj.parent = parent_path.path
                if path_parser:
                    path_obj.path_parser = PathParser(parser_name=path_parser)
                paths[ptype] = path_obj

        mapper = (
            dct.get('mapper')
            or dct.get('path')
            or (dct.get('function_name'), dct.get('function_args'))
        )
        obj: BaseMapper = BaseMapper()
        if dct.get('mappers'):
            mapper = []
            obj = Mapper()
            for n, v in enumerate(dct.get('mappers', [])):
                obj[n] = BaseMapper.from_dict(v, parent)

        elif isinstance(mapper, tuple) and None in mapper:
            return obj

        def add_path_attrs(path: Path):
            if path.is_relative_path():
                source_path = paths.get('source', parent.source if parent else None)
                if source_path:
                    path.parent = source_path.path
            if path_parser:
                path.parser = PathParser(parser_name=path_parser)

        if isinstance(mapper, str | Path):
            path = Path(path=mapper) if isinstance(mapper, str) else mapper
            obj = Transformer()
            add_path_attrs(path)
            obj.function_args.append(path)

        elif (
            isinstance(mapper, tuple | list)
            and len(mapper) in [2, 3]
            and isinstance(mapper[0], str)
            and isinstance(mapper[1], list)
        ):
            function_args = []
            for v in mapper[1]:
                arg = v
                if isinstance(v, str):
                    arg = Path(path=v)
                add_path_attrs(arg)
                function_args.append(arg)
            obj = Transformer(function_name=mapper[0], function_args=function_args)
            if len(mapper) == 3:
                obj.function_kwargs = mapper[2]

        elif isinstance(mapper, list):
            if mapper and isinstance(mapper[0], dict):
                obj = Mapper()
        else:
            LOGGER.error('Unknown mapper type.')

        for key in ['indices', 'remove', 'cache', 'update_mode']:
            if dct.get(key) is not None:
                setattr(obj, key, dct.get(key))
        if paths.get('source'):
            obj.source = paths.get('source')
        if paths.get('target'):
            obj.target = paths.get('target')

        if isinstance(obj, Mapper):
            mappers = []
            for v in mapper:
                m = BaseMapper.from_dict(v, obj)
                mappers.append(m)
            obj.mappers = mappers

        return obj

    def get_required_paths(self) -> list[str]:
        """Extract all source paths required by this mapper and its sub-mappers.

        Traverses the mapper tree to collect all absolute paths referenced in source
        data extraction. Used by parsers with `parse_only_required=True` to optimize
        parsing by only loading necessary data.

        Returns:
            list[str]: Unique list of all absolute paths (without indices) needed.

        Example:
            >>> mapper = BaseMapper.from_dict({
            ...     'mapper': [
            ...         {'mapper': ('func', ['a.b[0].c', 'a.d']), 'target': 'x'},
            ...         {'mapper': 'e.f', 'target': 'y'}
            ...     ]
            ... })
            >>> mapper.get_required_paths()
            ['a', 'a.b', 'a.b.c', 'a.d', 'e', 'e.f']
        """

        def get_path_segments(parsed: dict[str, Any]) -> list[str]:
            segments: list[str] = []
            value = parsed.get('value')
            ptype = parsed.get('type')

            if ptype == 'comparator':
                return segments

            if value and ptype == 'field':
                segments.append(value)

            for children in parsed.get('children', []):
                if not isinstance(children, dict):
                    continue
                segments.extend(get_path_segments(children))

            return segments

        def filter_path(path: str) -> list[str]:
            parsed = JmespathParser().parse(path).parsed
            segments = get_path_segments(parsed)
            return ['.'.join(segments[:n]) for n in range(1, len(segments) + 1)]

        def get_paths(mapper: BaseMapper) -> list[str]:
            paths = []
            for m in mapper:
                paths.extend(get_paths(m))

            if mapper.source and mapper.source.transformer:
                for path in mapper.source.transformer.function_args:
                    paths.extend(filter_path(path.absolute_path))

            if isinstance(mapper, Mapper):
                for sub_mapper in mapper.mappers:
                    paths.extend(get_paths(sub_mapper))

            elif isinstance(mapper, Transformer):
                for path in mapper.function_args:
                    paths.extend(filter_path(path.absolute_path))

            return paths

        return list(set(get_paths(self)))


class Transformer(BaseMapper):
    """Mapper that applies a transformation function to source data.

    Executes a function (defined as a static method on the parser class) with
    arguments extracted from source data paths. If no function is specified,
    applies identity transformation (returns data unchanged).

    The transformation function must be defined in the :class:`MappingParser` subclass
    as a static method or regular method. Arguments are extracted from source
    data using the paths specified in `function_args`.

    Attributes:
        function_name: Name of the method to call on the parser instance.
        function_args: List of Path objects specifying where to get function arguments.
        function_kwargs: Additional keyword arguments to pass to the function.
        order: Execution priority (1 for transformers, higher than Mapper's 0).

    Dependencies:
        Uses: :class:`Path` objects in function_args to extract argument data
        Uses: :meth:`Path.get_data` to retrieve values from source
        Calls: Method on :class:`MappingParser` instance (named by function_name)
        Used by: :class:`Mapper` (executes transformers in its mappers list)
        Used by: :class:`Data` (when Data.transformer is set)

    Example:
        Define transformation function in parser:
        >>> class MyParser(MappingParser):
        ...     @staticmethod
        ...     def reshape_eigenvalues(array: np.ndarray, n_spin: int, n_k: int):
        ...         array = np.transpose(array)[0].T
        ...         return np.reshape(array, (n_spin, n_k, len(array[0])))

        Use in mapping:
        >>> transformer = Transformer(
        ...     function_name='reshape_eigenvalues',
        ...     function_args=[
        ...         Path(path='eigenvalues'),
        ...         Path(path='n_spin_channels'),
        ...         Path(path='n_kpoints')
        ...     ]
        ... )
    """

    function_name: str = Field(
        '', description="""Name of the function defined in the parser."""
    )
    function_args: list[Path] = Field(
        [], description="""Paths to the data as arguments to the function."""
    )
    function_kwargs: dict[str, Any] = Field(
        {}, description="""Keyword args to pass to function."""
    )
    order: int = 1

    def get_data(
        self, source_data: dict[str, Any], parser: 'MappingParser', **kwargs
    ) -> Any:
        """Execute transformation function with arguments from source paths.

        Extracts data from each path in `function_args`, then calls the function
        specified by `function_name` on the parser instance. If no function_name
        is provided, applies identity transformation.

        Data removal optimization: When remove=True, data is only popped from source
        if this is the last mapper referencing that path. This is determined by checking
        if `all_paths.count(reduced_path) <= 1`, where all_paths is populated by the
        parent Mapper with all paths used across all sub-mappers. This prevents removing
        data that other mappers still need.

        Args:
            source_data: Source dictionary for relative path lookups.
            parser: MappingParser instance providing the transformation function
                   and absolute data root.
            **kwargs: Options including remove (bool, default from self.remove) and
                     debug (bool, re-raise exceptions if True).

        Returns:
            Any: Transformed data, or None if transformation fails (unless debug=True).

        Raises:
            RuntimeError: If transformation fails and debug=True in kwargs.
        """
        remove: bool = kwargs.get('remove', self.remove)
        func = (
            getattr(parser, self.function_name, None)
            if self.function_name
            else lambda x: x
        )
        args = [
            m.get_data(
                source_data if m.is_relative_path() else parser.data,
                pop=remove and self.all_paths.count(m.reduced_path) <= 1,
            )
            for m in self.function_args
        ]
        try:
            return (
                func(*args)
                if not self.function_kwargs
                else func(*args, **self.function_kwargs)
            )
        except Exception as e:
            if kwargs.get('debug'):
                raise RuntimeError(f'Error evaluating {self.function_name}: {e}')
            return None


Data.model_rebuild()


class Mapper(BaseMapper, validate_assignment=True):
    """Composite mapper containing multiple sub-mappers with orchestrated execution.

    Executes a list of sub-mappers (Transformer or nested Mapper instances) to
    transform source data into a target dictionary. Supports caching, filtering
    by indices, and automatic path dependency tracking for optimized data removal.

    The Mapper acts as a container coordinating:
    - Sequential execution of sub-mappers (sorted by order if needed)
    - Caching of transformation results to avoid redundant computation
    - Filtering of array data by indices
    - Aggregation of results into a target dictionary

    Attributes:
        mappers: List of BaseMapper instances (Transformer or Mapper) to execute.
        order: Execution priority (0 for Mapper, executed before Transformer's 1).
        __cache: Internal cache for transformation results (when cache=True).

    Caching:
        Two types of caching occur during :meth:`get_data` execution:

        **Source Transformer Cache** (mapper.source.transformer.cache=True):
            - **When**: Before extracting source data for a sub-mapper
            - **Key**: mapper.source.transformer.function_name
            - **Value**: Result of mapper.source.get_data()
            - **Purpose**: Avoid re-extracting/transforming the same source data
            - **Example**: Multiple sub-mappers use same expensive source transformation

        **Transformer Result Cache** (mapper.cache=True on Transformer sub-mapper):
            - **When**: After executing a Transformer sub-mapper
            - **Key**: mapper.function_name
            - **Value**: List of transformation results
            - **Purpose**: Avoid re-executing transformation on each iteration
            - **Example**: Transformer used in multiple iterations with same input

        **Cache Lifecycle**:
            - **Created**: During Mapper construction (__cache = {})
            - **Populated**: First execution of get_data() for cached mappers
            - **Checked**: Before each source extraction or transformer execution
            - **Scope**: Per Mapper instance (not shared across Mapper instances)
            - **Cleared**: Never (persists for Mapper lifetime)

        **Execution Order**:
            1. Check source transformer cache (if mapper.source.transformer.cache)
            2. If miss, execute mapper.source.get_data() and cache result
            3. Check transformer result cache (if isinstance(mapper, Transformer) and mapper.cache)
            4. If miss, iterate and execute mapper.get_data(), cache results

    Dependencies:
        Contains: List of :class:`BaseMapper` instances (Transformer or nested Mapper)
        Calls: :meth:`BaseMapper.get_data` on each sub-mapper (recursive for nested Mappers)
        Calls: :meth:`BaseMapper.normalize_data` on transformation results
        Calls: :meth:`Data.get_data` if mapper.source is set
        Used by: :class:`MappingParser` as the top-level mapper
        Used by: :class:`MetainfoParser` (builds Mapper tree from annotations)

    Example:
        >>> mapper = Mapper(mappers=[
        ...     Transformer(
        ...         function_name='extract_positions',
        ...         function_args=[Path(path='atoms')],
        ...         target=Data(path=Path(path='positions'))
        ...     ),
        ...     Transformer(
        ...         function_name='extract_species',
        ...         function_args=[Path(path='atoms')],
        ...         target=Data(path=Path(path='species'))
        ...     )
        ... ])
        >>> result = mapper.get_data({'atoms': [...]}, parser)
        >>> result  # {'positions': [...], 'species': [...]}
    """

    mappers: list[BaseMapper] = Field([], description="""List of sub mappers.""")
    order: int = 0
    __cache: dict[str, Any] = {}

    @model_validator(mode='before')
    def set_attributes(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Pydantic validator to propagate all_paths and remove settings to sub-mappers.

        Collects all reduced paths from all sub-mappers and propagates them to each
        sub-mapper's all_paths attribute. This enables data removal optimization:
        each Transformer can check if it's the last reference to a path before popping.

        Also propagates the remove flag to all sub-mappers for consistent behavior.

        Validation behavior:
            - **Timing**: Runs BEFORE field assignment (mode='before')
            - **Side effects**: Recursively modifies all sub-mappers' all_paths and remove attributes
            - **Execution**: Traverses entire mapper tree to collect and distribute path information

        Args:
            values: Dictionary of field values being validated.

        Returns:
            dict[str, Any]: Modified values with all_paths populated.
        """

        def get_paths(mapper: BaseMapper) -> list[str]:
            # Recursively collect all reduced_path strings from Transformer function_args
            paths = []
            if isinstance(mapper, Transformer):
                # Leaf: extract reduced paths from all function arguments
                paths.extend([p.reduced_path for p in mapper.function_args])
            elif isinstance(mapper, Mapper):
                # Composite: recursively collect from all sub-mappers
                for m in mapper.mappers:
                    for mn in m:
                        paths.extend(get_paths(mn))
            return paths

        def set_paths(mapper: BaseMapper, paths: list[str]):
            # Recursively propagate all_paths to this mapper and all descendants
            mapper.all_paths = paths
            if isinstance(mapper, Mapper):
                for m in mapper.mappers:
                    set_paths(m, paths)

        def set_remove(mapper: BaseMapper, remove: bool):
            # Recursively propagate remove flag to this mapper and all descendants
            if mapper.remove is None:
                mapper.remove = remove
            if isinstance(mapper, Mapper):
                for m in mapper.mappers:
                    set_remove(m, remove)

        # Phase 1: Collect all paths from entire mapper tree
        paths = []
        for mapper in values.get('mappers', []):
            for m in mapper:
                paths.extend(get_paths(m))

        # Phase 2: Propagate collected paths and remove flag to all mappers
        for mapper in values.get('mappers', []):
            # Only set paths if not already provided (allow override)
            if not values.get('all_paths'):
                set_paths(mapper, paths)
            # Always propagate remove flag
            set_remove(mapper, values.get('remove'))
            for m in mapper:
                set_remove(m, values.get('remove'))

        # Phase 3: Store collected paths on parent Mapper
        if not values.get('all_paths'):
            values['all_paths'] = paths

        return values

    def get_data(
        self, source_data: dict[str, Any], parser: 'MappingParser', **kwargs
    ) -> Any:
        """Execute all sub-mappers and aggregate results into a dictionary.

        Iterates through sub-mappers, executing each one and collecting results.
        Handles caching, array iteration, indices filtering, and empty value detection.

        Execution flow for each sub-mapper:
        1. Determine source data (from mapper.source or use parent source_data)
        2. Check cache if mapper.source.transformer.cache=True
        3. Iterate over source data if it's a list, otherwise treat as single item
        4. Execute mapper.get_data() for each item (RECURSIVE for nested Mappers)
        5. Filter by indices if specified (can be list or function name on parser)
        6. Skip empty values (None, [], {}, empty arrays)
        7. Normalize and aggregate non-empty values
        8. Store result at mapper.target.path in output dictionary

        Recursion: When a sub-mapper is a `Mapper`, step 4 recursively calls this
        method, creating a tree-like execution structure for deeply nested mappings.

        Args:
            source_data: Source dictionary to extract from.
            parser: MappingParser instance providing functions and absolute data root.
            **kwargs: Options passed to sub-mapper get_data methods.

        Returns:
            dict[str, Any]: Dictionary mapping target paths to transformed values.
                           Single values if indices=None, lists if indices specified.
        """
        dct = {}

        def is_not_value(value: Any) -> bool:
            # Empty numpy array
            if isinstance(value, np.ndarray):
                return value.size == 0
            # Pint quantity: check underlying magnitude
            if hasattr(value, 'magnitude'):
                return is_not_value(value.magnitude)

            # Check equality with common empty values
            not_value: Any
            for not_value in [None, [], {}]:
                test = value == not_value
                # Handle numpy array comparison returning array of bools
                result = test.any() if isinstance(test, np.ndarray) else test
                if result:
                    return bool(result)

            return False

        def get_mapper_data(mapper: BaseMapper) -> list[Any]:
            # Start with full source data unless mapper has custom source
            data = source_data
            if mapper.source:
                data = None
                # Check source transformer cache first
                if mapper.source.transformer and mapper.source.transformer.cache:
                    data = self.__cache.get(mapper.source.transformer.function_name)
                # Cache miss: extract and transform source data
                if data is None:
                    data = mapper.source.get_data(source_data, parser, **kwargs)
                    # Populate source transformer cache
                    if mapper.source.transformer and mapper.source.transformer.cache:
                        self.__cache.setdefault(
                            mapper.source.transformer.function_name, data
                        )

            # Resolve indices: can be direct list or parser attribute name
            indices = mapper.indices
            if isinstance(indices, str):
                # Fetch attribute from parser (e.g., 'atom_indices')
                indices = getattr(parser, indices, [])
                if callable(indices):
                    indices = indices()

            # Check transformer result cache
            value: list[Any] = []
            if isinstance(mapper, Transformer) and mapper.cache:
                value = self.__cache.get(mapper.function_name, value)

            # Cache miss or no caching: execute mapper on each data element
            if not value:
                for n, d in enumerate(data if isinstance(data, list) else [data]):
                    v = mapper.get_data(d, parser, **kwargs)
                    # Filter by indices if specified (only include matching positions)
                    if indices and n not in indices:
                        continue
                    # Filter out empty values
                    if not is_not_value(v):
                        value.append(v)
                # Populate transformer result cache
                if value and mapper.cache and isinstance(mapper, Transformer):
                    self.__cache.setdefault(mapper.function_name, value)
            # Store normalized values in result dict
            if value:
                return [mapper.normalize_data(v) for v in value]
            return value

        for mapper in self.mappers:
            value = []
            for m in mapper:
                mapper_value = get_mapper_data(m)
                value.extend(
                    mapper_value if isinstance(mapper_value, list) else [mapper_value]
                )
            if value:
                dct[mapper.target.path.path] = (
                    value[0] if mapper.indices is None else value
                )
        return dct

    def sort(self, recursive: bool = True) -> None:
        """Sort sub-mappers by execution order.

        Sorts mappers list in-place by the `order` attribute. By default, Mapper
        instances have order=0 and Transformer instances have order=1, ensuring
        container mappers execute before transformations.

        Args:
            recursive: If True, recursively sort all nested Mapper instances.
        """
        self.mappers.sort(key=lambda m: m.order)
        if recursive:
            for mapper in self.mappers:
                if isinstance(mapper, Mapper):
                    mapper.sort()


Mapper.model_rebuild()


class MappingParser(ABC):
    """Abstract base class for file format parsers with bidirectional dict conversion.

    Provides a framework for parsing files into dictionaries and converting between
    different file formats through declarative mappers. Each subclass implements
    format-specific loading (:meth:`load_file`), serialization (:meth:`to_dict`), and
    deserialization (:meth:`from_dict`) methods.

    Architecture:
        - `data_object`: Format-specific representation (e.g., h5py.Group, etree.Element)
        - `data`: Dictionary representation of the file (lazy-loaded via :meth:`to_dict`)
        - `mapper`: Specification for transforming to/from other parsers
        - `convert`: Method to transform data to another parser using a mapper

    Attribute representation:
        For formats with attributes (XML, HDF5), attributes are stored with a prefix:

        data = {
          'a': {
            'b': [
              {'@name': 'item1', '__value': 'value1'},
              {'@name': 'item2', '__value': 'value2'}
            ]
          }
        }

        Access with jmespath: `a.b[?"@name"=='item2'].__value` returns 'value2'

    Class attributes:
        parse_only_required (bool): Only parse paths needed by mapper (optimization).
        attribute_prefix (str): Prefix for attribute keys (default '@').
        value_key (str): Key for element value when attributes present (default '__value').
        logger: Logger instance for this module.

    Dependencies:
        Uses: :class:`BaseMapper` (typically :class:`Mapper`) for transformation specification
        Calls: :meth:`BaseMapper.get_data` via :meth:`convert` to transform data
        Calls: :meth:`Path.set_data` via :meth:`set_data` to populate target dictionary
        Subclassed by: :class:`HDF5Parser`, :class:`XMLParser`, :class:`MetainfoParser`, :class:`TextParser`

    Example:
        >>> with HDF5Parser(filepath='data.h5') as source:
        ...     with MetainfoParser(data_object=MySection()) as target:
        ...         source.convert(target)  # Uses target's metainfo annotations as mapper
    """

    parse_only_required: bool = False
    attribute_prefix: str = '@'
    value_key: str = '__value'
    logger = get_logger(__name__)

    def __init__(self, **kwargs):
        """Initialize parser with optional filepath, data_object, or mapper.

        Args:
            **kwargs: Initialization options:
                - filepath (str): Path to file to parse
                - data_object: Format-specific data object to populate (e.g., empty MSection
                              instance for MetainfoParser, or existing h5py.Group for HDF5Parser)
                - data (dict): Pre-loaded dictionary data (optional)
                - mapper (BaseMapper): Mapping specification
                - required_paths (list[str]): Paths to parse (if parse_only_required=True)
                - open (Callable): Custom file open function
        """
        for key, val in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, val)
        self._mapper: BaseMapper = kwargs.get('mapper')
        self._filepath: str = kwargs.get('filepath')
        self._data: dict[str, Any] = kwargs.get('data', {})
        self._data_object: Any = kwargs.get('data_object')
        self._required_paths: list[str] = kwargs.get('required_paths', [])
        self._open: Callable = kwargs.get('open')

    @abstractmethod
    def load_file(self) -> Any:
        """Load file into format-specific data object (implemented by subclasses).

        Returns:
            Any: Format-specific object (e.g., h5py.Group, etree.Element, TextFileParser).
        """
        return {}

    @abstractmethod
    def to_dict(self, **kwargs) -> dict[str | int, Any]:
        """Convert data_object to dictionary representation (implemented by subclasses).

        Returns:
            dict[str | int, Any]: Dictionary with attribute_prefix and value_key conventions.
        """
        return {}

    @abstractmethod
    def from_dict(self, dct: dict[str, Any]):
        """Populate data_object from dictionary (implemented by subclasses).

        Args:
            dct: Dictionary data to deserialize into data_object.
        """
        pass

    def build_mapper(self) -> BaseMapper:
        """Build default mapper for this parser (override in subclasses).

        Returns:
            BaseMapper: Empty Mapper by default. Subclasses like MetainfoParser
                       build mappers from annotations.
        """
        return Mapper()

    @property
    def open(self):
        """Get appropriate file open function, auto-detecting compression.

        Checks file magic bytes against COMPRESSIONS dict to detect gzip, bzip2, or xz
        compression. Returns the appropriate open function (gzip.open, bz2.open, etc.)
        or standard open for uncompressed files.

        Returns:
            Callable: Open function to use (gzip.open, bz2.open, lzma.open, or open).
        """
        if self._open is not None:
            return self._open

        with open(self.filepath, 'rb') as f:
            open_compressed = COMPRESSIONS.get(f.read(3))

        return open_compressed[1] if open_compressed is not None else open

    @property
    def filepath(self) -> str:
        return self._filepath

    @filepath.setter
    def filepath(self, value: str):
        self._filepath = value
        self._data_object = None
        self._data = None
        self._open = None

    @property
    def data(self):
        if not self._data:
            try:
                self._data = self.to_dict()
            except Exception:
                pass
        return self._data

    @property
    def data_object(self):
        if self._data_object is None:
            self._data_object = self.load_file()
        return self._data_object

    @data_object.setter
    def data_object(self, value: Any):
        self._data_object = value
        self._data = None
        self._filepath = None

    @property
    def mapper(self) -> BaseMapper:
        if self._mapper is None:
            self._mapper = self.build_mapper()
        return self._mapper

    @mapper.setter
    def mapper(self, value: BaseMapper):
        self._mapper = value

    def set_data(self, data: Any, target: dict[str, Any], **kwargs) -> None:
        """Recursively set dictionary data into target, creating paths as needed.

        Takes transformed mapper output (nested dicts with path keys like '.a.b') and
        sets each path into the target dictionary using Path.set_data(). Leading dots
        are stripped from keys when writing to target (e.g., '.energy' becomes 'energy'),
        so target dictionaries have keys matching metainfo attribute names.

        Update mode behavior:
            If `update_mode` kwarg is not provided, this method constructs a nested
            update_mode specification by traversing the mapper hierarchy via
            `build_update_mode_tree()`. This allows each mapper to specify its own
            `update_mode` via the annotation, enabling fine-grained control during
            multi-pass parsing of polymorphic subsections.

            If `update_mode` kwarg IS provided, it overrides the mapper hierarchy.

        Args:
            data: Dictionary with path keys, list of dicts, or direct value.
            target: Target dictionary to modify in-place.
            **kwargs: Options including:
                - update_mode (str | dict | None): Override default merge behavior
                - remove (bool): Remove data from source after setting
                - mapper (BaseMapper): Used to build update_mode tree if update_mode=None

        Examples:
            >>> # Auto mode from mapper annotations
            >>> parser.set_data(
            ...     {'.energy': 1.5, '.forces': [[0, 0, 1]]},
            ...     target,
            ...     mapper=my_mapper  # Uses my_mapper.update_mode
            ... )
            >>>
            >>> # Override with global mode
            >>> parser.set_data(
            ...     {'.numerical_settings': [...]},
            ...     target,
            ...     update_mode='append'  # All keys use append
            ... )
            >>>
            >>> # Override with nested mode
            >>> parser.set_data(
            ...     {'.numerical_settings': [...]},
            ...     target,
            ...     update_mode={
            ...         '__update_mode': 'merge',
            ...         '.numerical_settings': {'__update_mode': 'append'}
            ...     }
            ... )
        """

        def build_update_mode_tree(mapper: 'BaseMapper') -> dict[str, Any]:
            """Recursively build nested update_mode specification from mapper hierarchy.

            Called automatically by the framework when `update_mode` kwarg is None in
            `convert()`. Traverses the mapper tree, extracting each mapper's `update_mode`
            field and building a nested dictionary keyed by target path. This allows each
            subsection to specify its own merge behavior during multi-pass parsing.

            Users specify string update_mode on individual annotations; this function
            constructs the nested tree for internal use.

            Args:
                mapper: Root mapper to traverse

            Returns:
                Nested dict with '__update_mode' at each level and child specs by path

            Example output (automatically constructed from annotations):
                {
                    '__update_mode': 'merge',
                    'numerical_settings': {
                        '__update_mode': 'append',
                        '.kmesh': {'__update_mode': 'merge'}
                    }
                }
            """
            # Set this mapper's mode at current level
            spec: dict[str, Any] = {'__update_mode': mapper.update_mode}
            # Recursively add child mappers' modes
            if isinstance(mapper, Mapper):
                for sub_mapper in mapper.mappers:
                    # Key by target path so _get_update_mode can find child specs
                    if sub_mapper.target:
                        spec[sub_mapper.target.path.path] = build_update_mode_tree(
                            sub_mapper
                        )
            return spec

        if isinstance(data, dict):
            update_mode_all = cast(
                str | dict[str, Any] | None, kwargs.get('update_mode')
            )
            if update_mode_all is None:
                mapper = cast(BaseMapper | None, kwargs.get('mapper'))
                update_mode = (
                    build_update_mode_tree(mapper)
                    if mapper is not None
                    else _normalize_update_mode_spec('merge')
                )
            else:
                update_mode = _normalize_update_mode_spec(update_mode_all)

            for key in list(data.keys()):
                path = Path(path=key)
                update_mode_n = _get_update_mode(update_mode, key)
                data_to_add = data.pop(key) if kwargs.get('remove') else data[key]
                new_data = path.set_data(
                    data_to_add,
                    data if path.is_relative_path() else target,
                    update_mode=update_mode_n,
                )
                self.set_data(new_data, target, remove=True, update_mode=update_mode_n)

        elif isinstance(data, list):
            for val in data:
                self.set_data(val, target, **kwargs)

    def get_data(
        self,
        mapper: BaseMapper,
        source_data: dict[str, Any],
    ) -> Any:
        """Execute mapper to extract/transform data.

        Convenience method that delegates to mapper.get_data(source_data, self).

        Args:
            mapper: Mapper or Transformer to execute.
            source_data: Source dictionary to extract from.

        Returns:
            Any: Extracted/transformed data.
        """
        return mapper.get_data(source_data, self)

    def convert(
        self,
        target: 'MappingParser',
        mapper: 'BaseMapper | None' = None,
        update_mode: str | None = None,
        remove: bool = False,
        debug: bool = False,
    ) -> None:
        """Transform this parser's data into target parser using a mapper.

        Main method for converting between file formats or data representations.
        Executes the mapper to transform source data into target's dictionary format,
        then deserializes into target's data_object.

        Process:
        1. Use target.mapper if no mapper provided
        2. Get required paths from mapper (if parse_only_required=True)
        3. Extract source data (from mapper.source or use self.data)
        4. Execute mapper to get transformed dictionary
        5. Set transformed data into target.data
        6. Call target.from_dict to populate target.data_object

        Args:
            target: Destination parser to receive transformed data.
            mapper: Mapping specification (uses target.mapper if None).
            update_mode: How to merge data ('merge', 'replace', 'append', etc.).
            remove: Remove source data after extraction (for memory efficiency).
            debug: Re-raise exceptions from transformers instead of returning None.

        Example:
            >>> with HDF5Parser(filepath='input.h5') as source:
            ...     with XMLParser(filepath='output.xml') as target:
            ...         mapper = Mapper.from_dict({...})
            ...         source.convert(target, mapper)
        """
        if mapper is None:
            mapper = target.mapper
        if self.parse_only_required and mapper and not self._required_paths:
            self._required_paths = mapper.get_required_paths()
        source_data = self.data
        if mapper.source:
            source_data = mapper.source.get_data(self.data, self)
        result = mapper.get_data(source_data, self, remove=remove, debug=debug)
        target.set_data(result, target.data, update_mode=update_mode, mapper=mapper)
        target.from_dict(target.data)

    def close(self):
        if hasattr(self._data_object, 'close'):
            self._data_object.close()
        self._data_object = None
        self._data = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __repr__(self) -> str:
        string = f'{self.__class__.__name__}'
        if self.filepath:
            string += f'({os.path.basename(self.filepath)})'
        if self._data_object:
            string += f': {type(self._data_object).__name__}'
        if self._data:
            keys = list(self._data.keys())
            keys = keys[: min(len(keys), 5)]
            string += f' -> data.keys: {", ".join([key for key in keys])}'
            if len(self._data.keys()) > 5:
                string += '...'
        return string


class MetainfoBaseMapper(BaseMapper):
    @staticmethod
    def from_dict(
        dct: dict[str, Any], parent: BaseMapper | None = None
    ) -> 'BaseMapper':
        parent = BaseMapper.from_dict(dct) if parent is None else parent

        if isinstance(parent, Transformer):
            transformer = MetainfoTransformer()
            for key in type(parent).model_fields.keys():
                val = getattr(parent, key)
                if val is not None:
                    setattr(transformer, key, val)
            for key in ['unit', 'search']:
                if dct.get(key):
                    setattr(transformer, key, dct.get(key))
            return transformer
        elif isinstance(parent, Mapper):
            mdct = dct.get('mapper')
            mapper = MetainfoMapper()
            for key in type(parent).model_fields.keys():
                val = getattr(parent, key)
                if val is not None:
                    setattr(mapper, key, val)
            if dct.get('m_def'):
                mapper.m_def = dct.get('m_def')
            for n, obj in enumerate(parent.mappers):
                parent.mappers[n] = MetainfoBaseMapper.from_dict(mdct[n], obj)
                mdct_n = mdct[n].get('mappers', [])
                if len(mdct_n) != len(obj):
                    continue
                for m, obj_m in enumerate(obj):
                    parent.mappers[n][m] = MetainfoBaseMapper.from_dict(
                        mdct_n[m], obj_m
                    )
            mapper.mappers = parent.mappers
            return mapper

        return parent


class MetainfoMapper(MetainfoBaseMapper, Mapper):
    """Metainfo-specific mapper with section definition tracking.

    .. warning::
        Internal implementation detail. API may change without notice.
        Do not instantiate directly. Generated by :meth:`MetainfoParser.build_mapper`.
    """

    m_def: str = Field(None, description="""Section definition.""")

    def get_data(
        self, source_data: dict[str, Any], parser: MappingParser, **kwargs
    ) -> Any:
        dct = super().get_data(source_data, parser, **kwargs)
        if self.m_def:
            dct['.m_def'] = self.m_def
        return dct


class MetainfoTransformer(MetainfoBaseMapper, Transformer):
    unit: str | None = Field(None, description="""Pint unit to be applied to value.""")
    search: str | None = Field(None, description="""Path to search value.""")

    def normalize_data(self, value: Any):
        if self.search:
            path = Path(path=self.search)
            value = path.get_data(value)
        if self.unit is not None and value is not None and not hasattr(value, 'units'):
            value = value * ureg(self.unit)
        return value


class MetainfoParser(MappingParser):
    """Parser for NOMAD metainfo sections with annotation-driven mapper generation.

    Automatically builds mappers from metainfo annotations, enabling declarative
    data transformation from source formats (XML, HDF5, JSON) to metainfo sections.
    Annotations are specified using the `Mapper` annotation on sections and quantities.

    The parser traverses the metainfo schema and constructs a mapper tree by reading
    annotations with the key specified by `annotation_key` (default: 'mapping').
    Multiple annotation keys can be used for format-specific mappings (e.g., 'hdf5', 'xml').

    Attributes:
        annotation_key: Key to look up in metainfo annotations (default: 'mapping').
        max_nested_level: Maximum depth for recursive section traversal (default: 3).

    Dependencies:
        Uses: :class:`MapperAnnotation` from metainfo annotations
        Uses: :class:`MSection` as data_object type
        Calls: :meth:`build_mapper` to construct :class:`MetainfoMapper` tree from annotations
        Calls: :meth:`MSection.m_get_annotations` to retrieve mapping annotations
        Calls: :meth:`MSection.m_set`/:meth:`MSection.m_add_sub_section` in :meth:`from_dict`

    Example:
        >>> class MySection(MSection):
        ...     energy = Quantity(
        ...         type=float,
        ...         a_mapper=Mapper(mapper='calculation.energy', unit='eV')
        ...     )
        >>>
        >>> with HDF5Parser(filepath='data.h5') as source:
        ...     with MetainfoParser(data_object=MySection()) as target:
        ...         target.annotation_key = 'hdf5'
        ...         source.convert(target)  # Auto-builds mapper from annotations
    """

    def __init__(self, **kwargs):
        self._annotation_key: str = kwargs.get('annotation_key', 'mapping')
        self.max_nested_level: int = 3
        super().__init__(**kwargs)

    @property
    def annotation_key(self) -> str:
        return self._annotation_key

    @annotation_key.setter
    def annotation_key(self, value):
        self._annotation_key = value
        self._mapper = None

    def load_file(self) -> MSection | None:
        if self._data_object is not None:
            with open(self.filepath) as f:
                return self._data_object.m_from_dict(json.load(f))

        elif self.filepath:
            try:
                archive = EntryArchive()
                ArchiveParser().parse(self.filepath, archive)
                return archive
            except Exception:
                # TODO: Correct the logger method in a separate error-handling fix.
                self.logger.errror('Error loading archive file.')  # typos: ignore

        return None

    def to_dict(self, **kwargs) -> dict[str | int, Any]:
        if self.data_object is not None:
            return self.data_object.m_to_dict()
        return {}

    def from_dict(self, dct: dict[str, Any], root: MSection | None = None) -> None:
        """Deserialize dictionary into metainfo section instances.

        Note: At this stage, leading dots have already been stripped from keys.
        While mappers use relative paths with dots ('.energy', '.@'), set_data()
        removes them before calling from_dict(). Keys are attribute names only.

        Args:
            dct: Dictionary with attribute names as keys (no leading dots).
            root: Section instance to populate. Defaults to self.data_object.
        """
        # if self.data_object is not None:
        #     self.data_object = self.data_object.m_from_dict(dct)
        # return

        # TODO this is a temporary fix for nomad_simulations  PhysicalProperty
        # error with m_from_dict
        if self.data_object is None:
            return

        if root is None:
            root = self.data_object

        for key, val in dct.items():
            if not hasattr(root, key):
                continue

            section = getattr(root.m_def.section_cls, key)
            if isinstance(section, SubSection):
                val_list = [val] if isinstance(val, dict) else val

                # Track indices of empty sub-sections to remove after iteration
                empty_indices = []

                for n, val_n in enumerate(val_list):
                    # Resolve type PER ITEM (for heterogeneous lists)
                    item_m_def = val_n.get('m_def')
                    section_def = section.sub_section
                    if (
                        item_m_def is not None
                        and item_m_def != section.qualified_name()
                    ):
                        for isection in section.sub_section.all_inheriting_sections:
                            if isection.qualified_name() == item_m_def:
                                section_def = isection
                                break

                    quantities = section_def.all_quantities
                    try:
                        sub_section = root.m_get_sub_section(section, n)
                    except Exception:
                        sub_section = None
                    if sub_section is None:
                        sub_section = section_def.section_cls()
                        # sub_section = section_def.section_cls(
                        #     **{
                        #         n: val_n.get(n)
                        #         for n, q in quantities.items()
                        #         if not q.derived and n in val_n
                        #     }
                        # )
                        if root.m_context:
                            sub_section.m_root().m_context = root.m_context
                        root.m_add_sub_section(section, sub_section)
                    self.from_dict(val_n, sub_section)
                    # Check if sub-section is empty
                    if not [
                        v
                        for v in sub_section.values()
                        if (isinstance(v, list | np.ndarray) and len(v)) or v
                    ]:
                        empty_indices.append(n)

                # Remove empty sub-sections in reverse order to avoid index shifts
                for n in reversed(empty_indices):
                    root.m_remove_sub_section(section, index=n)
                continue

            if key == 'm_def':
                continue

            try:
                root.m_set(root.m_get_quantity_definition(key), val)
            except Exception:
                pass

    def build_mapper(self, max_level: int | None = None) -> BaseMapper:
        """Build mapper tree from metainfo annotations on data_object schema.

        Recursively traverses the metainfo section definition, collecting annotations
        with key `annotation_key` from sections and quantities. Constructs a nested
        `MetainfoMapper` tree where each node corresponds to a section or quantity.

        Annotation lookup order (for sections):
        1. Check SubSection itself for annotations
        2. Check section definition (section.sub_section) for annotations
        3. If still none, search all_inheriting_sections for annotations
        4. When found on inheriting section, use that section's definition and set
           `m_def` in mapper to resolve the correct polymorphic type

        This inheritance search enables polymorphism: a base section without annotations
        can defer to specialized inheriting sections that do have format-specific mappings.

        Args:
            max_level: Maximum depth for **self-referential** sections (defaults to
                      self.max_nested_level=3). Prevents infinite recursion when a section
                      references itself (e.g., Section.parent: Section). The level counter
                      only increments for circular references; non-circular sub-sections
                      are traversed without depth limit.

        Returns:
            BaseMapper: MetainfoMapper tree with source paths from annotations
                       and target paths from schema structure.

        Example with inheritance:
            >>> class BaseProperty(MSection):
            ...     pass  # No annotation
            >>> class Energy(BaseProperty):
            ...     value = Quantity(type=float, a_hdf5=Mapper(mapper='energy'))
            >>>
            >>> # When building mapper for a section containing BaseProperty subsection,
            >>> # it will find Energy's annotation via all_inheriting_sections
        """

        def fill_mapper(
            mapper: dict[str, Any],
            annotation: MapperAnnotation,
            attributes: list[str],
        ) -> None:
            for key in attributes:
                value = getattr(annotation, key, None)
                if value is not None:
                    mapper.setdefault(key, value)

        def build_section_mapper(
            section: SubSection | MSection, level: int = 0, m_def: str | None = None
        ) -> dict[str, Any]:
            mapper: dict[str, Any] = {}
            # Stop recursion for self-referential sections (e.g., Section.parent: Section)
            if level >= (max_level or self.max_nested_level):
                return mapper

            # Get section definition: SubSection.sub_section or MSection.m_def
            section_def = (
                section.sub_section
                if isinstance(section, SubSection)
                else section.m_def
            )

            if not section_def:
                return mapper

            # Phase 1: Find annotation via 3-level lookup
            # Level 1: Try SubSection itself (if applicable)
            annotation: MapperAnnotation = (
                (section if isinstance(section, SubSection) else section_def)
                .m_get_annotations(MAPPING_ANNOTATION_KEY, {})
                .get(self.annotation_key)
            )

            if not annotation:
                # Level 2: Try section definition
                annotation = section_def.m_get_annotations(
                    MAPPING_ANNOTATION_KEY, {}
                ).get(self.annotation_key)

            if isinstance(section, SubSection) and not annotation:
                # Level 3: Search all inheriting sections for annotations (polymorphism)
                for inheriting_section in section_def.all_inheriting_sections or []:
                    section_annotation = inheriting_section.m_get_annotations(
                        MAPPING_ANNOTATION_KEY, {}
                    ).get(self.annotation_key)
                    section_name = inheriting_section.qualified_name()
                    if section_annotation:
                        annotation = section_annotation
                        # Found annotation on derived section: use that section's schema
                        # TODO this does not work as it will applies to base class
                        # section.sub_section = inheriting_section
                        # TODO this is a hacky patch, metainfo should have an alternative
                        # way to resolve the sub-section def
                        if m_def is not None and section_name == m_def:
                            section_def = inheriting_section
                            mapper['m_def'] = section_name
                            break
                        elif m_def is None:
                            # we add all annotated inheriting sections as mappers
                            section_mapper = build_section_mapper(
                                section, level=level, m_def=section_name
                            )
                            if section_mapper:
                                mapper.setdefault('mappers', [])
                                fill_mapper(
                                    section_mapper,
                                    annotation,
                                    ['remove', 'cache', 'path_parser', 'indices'],
                                )
                                mapper['mappers'].append(section_mapper)
                if mapper.get('mappers', []):
                    parent_is_root = (
                        section.name in self.data_object.m_def.all_sub_sections
                    )
                    mapper['target'] = f'{"" if parent_is_root else "."}{section.name}'
                    mapper['indices'] = []
                    fill_mapper(
                        mapper,
                        annotation,
                        ['remove', 'cache', 'path_parser', 'update_mode'],
                    )
                    return mapper

            # No annotation found anywhere: section not mapped
            if not annotation:
                return mapper

            # Phase 2: Build section-level mapper from annotation
            fill_mapper(
                mapper, annotation, ['remove', 'cache', 'path_parser', 'update_mode']
            )
            mapper['source'] = annotation.mapper

            # Phase 3: Collect quantity mappers (leaf values)
            mapper['mapper'] = []
            for name, quantity_def in section_def.all_quantities.items():
                qannotation = quantity_def.m_get_annotations(
                    MAPPING_ANNOTATION_KEY, {}
                ).get(self.annotation_key)
                if qannotation:
                    # Build relative target path (root section uses absolute '', others use '.name')
                    quantity_mapper = {
                        'mapper': qannotation.mapper,
                        'target': f'{"" if section == self.data_object else "."}{name}',
                    }
                    fill_mapper(
                        quantity_mapper,
                        qannotation,
                        ['remove', 'cache', 'path_parser', 'unit', 'search'],
                    )
                    mapper['mapper'].append(quantity_mapper)

            # Phase 4: Recursively collect sub-section mappers
            # Build list of IDs to detect self-references (circular dependencies)
            all_ids = [section_def.definition_id]
            all_ids.extend([s.definition_id for s in section_def.all_base_sections])
            for name, sub_section in section_def.all_sub_sections.items():
                # avoid recursion
                # if sub_section.sub_section.definition_id in all_ids:
                #     continue
                # Check if this is a self-reference (e.g., Section.parent: Section)
                nested = sub_section.sub_section.definition_id in all_ids
                # Increment level only for self-references; non-circular sub-sections traverse freely
                sub_section_mapper = build_section_mapper(
                    sub_section, level + (1 if nested else 0)
                )
                # Only add if mapper has content (sub-section was annotated)
                if sub_section_mapper and sub_section_mapper.get('mapper'):
                    # Build relative target path
                    sub_section_mapper['target'] = (
                        f'{"" if section == self.data_object else "."}{name}'
                    )
                    # Repeating sub-sections use list indices, non-repeating use None
                    sub_section_mapper['indices'] = [] if sub_section.repeats else None
                    # Check if SubSection itself has annotation (for custom source paths)
                    sannotation = sub_section.m_get_annotations(
                        MAPPING_ANNOTATION_KEY, {}
                    ).get(self.annotation_key)
                    if sannotation:
                        sub_section_mapper['source'] = sannotation.mapper
                        fill_mapper(
                            sub_section_mapper,
                            sannotation,
                            ['remove', 'cache', 'path_parser', 'indices'],
                        )
                if sub_section_mapper:
                    mapper['mapper'].append(sub_section_mapper)

            return mapper

        dct = build_section_mapper(self.data_object)
        return MetainfoMapper.from_dict(dct)


class HDF5Parser(MappingParser):
    """Parser for HDF5 files with bidirectional Group/Dataset to dictionary conversion.

    Converts HDF5 hierarchical structure (Groups and Datasets) to dictionaries using
    the attribute_prefix ('@') and value_key ('__value') conventions for HDF5 attributes.

    HDF5 structure mapping:
        - Groups become nested dictionaries
        - Datasets become values (or dicts if they have attributes)
        - Attributes become keys with '@' prefix
        - Dataset values with attributes stored under '__value' key

    Example HDF5 to dict conversion:
        HDF5:
            /calculation/energy (Dataset: 1.5, attrs: {'units': 'eV'})
            /calculation/forces (Dataset: [[1,2,3]])

        Dict:
            {'calculation': {
                'energy': {'@units': 'eV', '__value': 1.5},
                'forces': [[1, 2, 3]]
            }}

    The parser supports parse_only_required optimization to only load specific
    HDF5 paths needed by the mapper.
    """

    def load_file(self, **kwargs) -> h5py.Group:
        try:
            filepath = kwargs.get('file', self.filepath)
            mode = (
                'w'
                if isinstance(filepath, str) and not os.path.isfile(filepath)
                else 'r'
            )
            return h5py.File(filepath, kwargs.get('mode', mode))
        except Exception:
            self.logger.error('Cannot read HDF5 file.')

    def to_dict(self, **kwargs) -> dict[str | int, Any]:
        if self.data_object is None:
            return {}

        def set_attributes(val: h5py.Dataset | h5py.Group, dct: dict[str | int, Any]):
            for name, attr in val.attrs.items():
                dct[f'{self.attribute_prefix}{name}'] = (
                    attr.tolist() if hasattr(attr, 'tolist') else attr
                )

        def group_to_dict(
            group: h5py.Group, root: dict[str | int, Any] | list[dict[str | int, Any]]
        ):
            for key, val in group.items():
                # Convert numeric keys to int (e.g., '0', '1', '2' for list indices)
                key = int(key) if key.isdecimal() else key
                # Build dot-separated path for required_paths filtering (skip numeric parts)
                path = '.'.join(
                    [p for p in val.name.split('/') if not p.isdecimal() and p]
                )
                # Skip if parse_only_required=True and path not in required list
                if self._required_paths and path not in self._required_paths:
                    continue
                # Case 1: List root + Group value (e.g., root=[{}, {}], val=Group at index 0)
                if isinstance(root, list) and isinstance(val, h5py.Group):
                    # Recursively fill the list element dict
                    group_to_dict(val, root[key])
                    set_attributes(val, root[key])
                # Case 2: Dict root + Group value (most common case)
                elif isinstance(root, dict) and isinstance(val, h5py.Group):
                    # Determine if Group represents a list (has numeric child keys) or dict
                    default: list[dict[str, Any]] = [
                        {} if k.isdecimal() else None for k in val.keys()
                    ]
                    # Use list if all children are numeric, dict otherwise
                    group_to_dict(
                        val, root.setdefault(key, {} if None in default else default)
                    )
                    # Ensure empty Groups become {} not []
                    if not root[key]:
                        root[key] = {}
                    set_attributes(val, root[key])
                # Case 3: Dataset value (leaf data)
                elif isinstance(val, h5py.Dataset):
                    # Read dataset data
                    data = val[()]
                    # Convert numpy arrays and bytes to Python types
                    v = (
                        data.astype(str if data.dtype == np.object_ else data.dtype)
                        if isinstance(data, np.ndarray)
                        else data.decode()
                        if isinstance(data, bytes)
                        else data
                    )
                    # Convert numpy types to Python lists
                    v = v.tolist() if hasattr(v, 'tolist') else v
                    # If Dataset has attributes, wrap value in dict with '__value' key
                    attrs = list(val.attrs.keys())
                    if attrs:
                        root[key] = {self.value_key: v}
                        set_attributes(val, root[key])
                    else:
                        # No attributes: store value directly
                        root[key] = v  # type: ignore
            return root

        dct: dict[str | int, Any] = {}
        group_to_dict(self.data_object, dct)
        return dct

    def from_dict(self, dct: dict[str, Any]) -> None:
        if self._data_object is not None:
            self._data_object.close()

        root = self.load_file(mode='a', file=self.filepath or BytesIO())

        def dict_to_hdf5(dct: dict[str, Any], root: h5py.Group) -> h5py.Group:
            for key, val in dct.items():
                if key.startswith(self.attribute_prefix):
                    root.attrs[key.lstrip(self.attribute_prefix)] = val
                elif isinstance(val, dict) and self.value_key not in val:
                    group = root.require_group(key)
                    dict_to_hdf5(val, group)
                elif isinstance(val, list) and val and isinstance(val[0], dict):
                    data = {}
                    for n, v in enumerate(val):
                        if self.value_key not in v:
                            group = root.require_group(f'{key}/{n}')
                            dict_to_hdf5(v, group)
                        else:
                            data[f'{key}/{n}'] = v
                    dict_to_hdf5(data, root)
                else:
                    attrs = val if isinstance(val, dict) else {}
                    v = attrs.get(self.value_key, None) if attrs else val
                    if v is None:
                        continue

                    if isinstance(v, list):
                        v = np.array(v)

                    shape = v.shape if hasattr(v, 'shape') else ()
                    dtype = v.dtype.type if hasattr(v, 'dtype') else type(v)
                    if dtype in [np.str_, str]:
                        dtype = h5py.string_dtype()
                    dataset = root.require_dataset(key, shape, dtype)
                    dataset[...] = v.tolist() if hasattr(v, 'tolist') else v
                    for name, attr in attrs.items():
                        if name == self.value_key:
                            continue
                        dataset.attrs[name.lstrip(self.attribute_prefix)] = attr

            return root

        self._data_object = dict_to_hdf5(dct, root)


class XMLParser(MappingParser):
    """Parser for XML files with bidirectional Element to dictionary conversion.

    Converts XML elements, attributes, and text to dictionaries using attribute_prefix
    ('@') and value_key ('__value') conventions. Uses lxml for parsing with streaming
    support (iterparse) for memory-efficient processing.

    XML structure mapping:
        - Elements become dictionary keys
        - Attributes become keys with '@' prefix
        - Text content becomes the value (or stored under '__value' if attributes exist)
        - Repeated elements become lists
        - Numeric text is automatically parsed to int/float

    Example XML to dict conversion:
        XML:
            <calculation>
                <energy units="eV">1.5</energy>
                <atom index="0">H</atom>
                <atom index="1">O</atom>
            </calculation>

        Dict:
            {'calculation': {
                'energy': {'@units': 'eV', '__value': 1.5},
                'atom': [
                    {'@index': 0, '__value': 'H'},
                    {'@index': 1, '__value': 'O'}
                ]
            }}

    See: https://lxml.de/ for underlying XML processing library.
    """

    def from_dict(self, dct: dict[str, Any]) -> None:
        def to_string(val: Any) -> str | None:
            val = val.tolist() if hasattr(val, 'tolist') else val
            if not isinstance(val, list):
                return str(val)
            string = ''
            for v in val:
                if not isinstance(v, str | float | int):
                    return None
                string += f' {v}'
            return string.strip()

        def data_to_element(
            tag: str, data: Any, root: etree._Element | None = None
        ) -> etree._Element:
            if tag.startswith(self.attribute_prefix) and root is not None:
                root.set(tag.lstrip(self.attribute_prefix), data)
            elif tag.startswith(self.value_key) and root is not None:
                root.text = to_string(data)
            elif isinstance(data, dict):
                root = (
                    etree.Element(tag) if root is None else etree.SubElement(root, tag)
                )
                for key, val in data.items():
                    data_to_element(key, val, root)
            elif isinstance(data, list):
                string = to_string(data)
                if string is not None:
                    element = etree.SubElement(root, tag)
                    element.text = string
                else:
                    for val in data:
                        data_to_element(tag, val, root)
            elif hasattr(data, 'tolist'):
                data_to_element(tag, data.tolist(), root)
            else:
                element = etree.SubElement(root, tag)
                element.text = to_string(data)
            return root

        self._data_object = data_to_element('root', dct).getchildren()[0]

    def to_dict(self, **kwargs) -> dict[str | int, Any]:
        def convert(text: str) -> Any:
            val = text.strip()
            try:
                val_array = np.array(val.split(), dtype=float)
                if np.all(np.mod(val_array, 1) == 0):
                    val_array = np.array(val_array, dtype=int)
                val_array = val_array.tolist()
                return val_array[0] if len(val_array) == 1 else val_array
            except Exception:
                return val

        # Stack of dicts representing nested elements
        stack: list[dict[str | int, Any]] = []
        results: dict[str | int, Any] = {}
        if self.filepath is None:
            return results

        current_path = ''
        # TODO determine if iterparse is better than iterwalk
        with self.open(self.filepath, 'rb') as f:
            # Stream parse XML: emit 'start' and 'end' events for each element
            for event, element in etree.iterparse(f, events=('start', 'end')):
                tag = element.tag
                if event == 'start':
                    # Build dot-separated path as we descend the XML tree
                    current_path = tag if not current_path else f'{current_path}.{tag}'
                    # Skip if parse_only_required=True and path not in required list
                    if (
                        self._required_paths
                        and current_path not in self._required_paths
                    ):
                        continue
                    # Push new dict onto stack for this element
                    stack.append({tag: {}})
                else:
                    # 'end' event: element is fully parsed, pop from stack
                    path = current_path
                    # Move up one level in path (e.g., 'a.b.c' -> 'a.b')
                    current_path = current_path.rsplit('.', 1)[0]
                    if self._required_paths and path not in self._required_paths:
                        continue
                    # Pop completed element from stack
                    data = stack.pop(-1)
                    text = element.text.strip() if element.text else None
                    attrib = element.attrib
                    # Process attributes (prefix with '@')
                    if attrib:
                        data.setdefault(tag, {})
                        data[tag].update(
                            (f'{self.attribute_prefix}{k}', v)
                            for k, v in attrib.items()
                        )
                    # Process text content (convert numeric strings)
                    if text:
                        value = convert(text)
                        # If attributes exist, store text under '__value' key
                        if attrib or data[tag]:
                            data[tag][self.value_key] = value
                        else:
                            # No attributes: store text value directly
                            data[tag] = value
                    # Merge into parent element (if stack not empty)
                    if stack and data:
                        # Get parent dict from stack
                        parent = stack[-1][list(stack[-1].keys())[0]]
                        # Handle repeated elements (convert to list)
                        if tag in parent:
                            # Special case: nested list (list of lists) needs wrapping
                            if (
                                isinstance(data[tag], list)
                                and isinstance(parent[tag], list)
                                and parent[tag]
                                and not isinstance(parent[tag][0], list)
                            ):
                                parent[tag] = [parent[tag]]
                            # Append to existing list
                            if isinstance(parent[tag], list):
                                parent[tag].append(data[tag])
                            else:
                                # First repeat: convert to list
                                parent[tag] = [
                                    parent[tag],
                                    data[tag],
                                ]
                        else:
                            # First occurrence: store directly
                            # parent[tag] = [data[tag]] if attrib else data[tag]
                            parent[tag] = data[tag]
                    else:
                        # No parent: this is the root element
                        results = data
        return results

    def load_file(self) -> etree._Element:
        try:
            return etree.parse(self.filepath)
        except Exception:
            self.logger.error('Cannot read XML file')


class TextParser(MappingParser):
    """Adapter for NOMAD's TextFileParser (nomad.parsing.file_parser.TextParser).

    Wraps TextFileParser to make it compatible with the MappingParser framework,
    enabling text file parsing with regex-based matchers to be used in mapping
    conversions. The TextFileParser results become the dictionary representation.

    Attributes:
        text_parser (TextFileParser): The TextFileParser instance to wrap.

    Note:
        from_dict is not implemented as text files are typically source-only formats.
        Set text_parser attribute with a configured TextFileParser instance.

    Example:
        >>> from nomad.parsing.file_parser import TextParser as TextFileParser
        >>> text_parser_instance = TextFileParser(quantities=[...])
        >>> parser = TextParser(
        ...     filepath='output.log',
        ...     text_parser=text_parser_instance
        ... )
        >>> data = parser.to_dict()  # Returns TextFileParser results
    """

    text_parser: TextFileParser | None = None

    def to_dict(self, **kwargs) -> dict[str | int, Any]:
        if self.data_object:
            self.data_object.parse()
            return self.data_object._results
        return {}

    def from_dict(self, dct: dict[str, Any]):
        raise NotImplementedError

    def load_file(self) -> Any:
        if self.filepath and self.text_parser is not None:
            self.text_parser.findlazy = True
            self.text_parser.mainfile = self.filepath
        return self.text_parser


if __name__ == '__main__':
    from nomad.parsing.file_parser.mapping_parser import MetainfoParser
    from tests.parsing.test_mapping_parser import (
        BSection,
        ExampleHDF5Parser,
        ExampleSection,
    )

    with MetainfoParser() as archive_parser, ExampleHDF5Parser() as hdf5_parser:
        archive_parser.annotation_key = 'hdf5'
        archive_parser.data_object = ExampleSection(b=[BSection(v=np.eye(2))])

        d = dict(
            g=dict(
                g1=dict(v=[dict(d=np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]]))]),
                v=['x', 'y', 'z'],
                g=dict(
                    c1=dict(
                        i=[4, 6],
                        f=[
                            {'@index': 0, '__value': 1},
                            {'@index': 2, '__value': 2},
                            {'@index': 1, '__value': 1},
                        ],
                        d=[dict(e=[3, 0, 4, 8, 1, 6]), dict(e=[1, 7, 8, 3, 9, 1])],
                    ),
                    c=dict(
                        v=[dict(d=np.eye(3), e=np.zeros(3)), dict(d=np.ones((3, 3)))]
                    ),
                ),
            )
        )

        hdf5_parser.from_dict(d)

        hdf5_parser.convert(archive_parser)
