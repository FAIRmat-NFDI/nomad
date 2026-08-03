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

import fnmatch
import re
from enum import Enum
from threading import Lock
from typing import Annotated, Any

import jmespath
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from nomad.app.v1.models import HTTPExceptionModel, User
from nomad.app.v1.models.pagination import Pagination
from nomad.app.v1.routers.auth import get_current_user
from nomad.app.v1.utils import create_responses
from nomad.auth.scopes import Scope
from nomad.config import config
from nomad.config.models.plugins import AppEntryPoint
from nomad.config.models.ui import (
    App,
    Menu,
    MenuItemHistogram,
    MenuItemNestedObject,
    MenuItemPeriodicTable,
    MenuItemTerms,
    WidgetHistogram,
    WidgetPeriodicTable,
    WidgetScatterPlot,
    WidgetTerms,
)
from nomad.datamodel import EntryArchive
from nomad.metainfo.elasticsearch_extension import (
    Elasticsearch,
    dtype_separator,
    entry_type,
    schema_separator,
)

# Compile the regex once at module load time.
SCHEMA_REGEX = re.compile(r'#[a-zA-Z0-9_.#]+')
_bad_app_not_found = (
    status.HTTP_404_NOT_FOUND,
    {
        'model': HTTPExceptionModel,
        'description': 'Could not find an app with the path "{app_path}".',
    },
)
_bad_search_quantity_parse = (
    status.HTTP_422_UNPROCESSABLE_CONTENT,
    {
        'model': HTTPExceptionModel,
        'description': 'Could not load or parse the requested search quantity.',
    },
)


class APITag(str, Enum):
    DEFAULT = 'apps'


router = APIRouter()

# Caches for entry point apps and search quantities
app_entry_points_cache: dict[str, AppEntryPoint] = {}
app_cache: dict[str, dict[str, Any]] = {}
app_search_quantity_cache: dict[str, list['SearchQuantity']] = {}
all_search_quantities: dict[str, 'SearchQuantity'] = {}

_init_lock = Lock()
_initialized = False


class DataType(str, Enum):
    INT = 'int'
    FLOAT = 'float'
    TIMESTAMP = 'timestamp'
    STRING = 'string'
    ENUM = 'enum'
    BOOLEAN = 'boolean'
    UNKNOWN = 'unknown'


class SearchQuantity(BaseModel):
    quantity: str = Field(description='Name of the search quantity.')
    quantity_normalized: str = Field(
        ...,
        description='The name of the quantity normalized for queries.',
        exclude=True,
    )
    description: str | None = Field(
        None, description='Description of the search quantity.'
    )
    dtype: DataType | None = Field(None, description='Type of the search quantity.')
    unit: str | None = Field(None, description='Unit of the search quantity.')
    shape: str | list[str] | None = Field(
        None, description='Shape of the search quantity.'
    )
    aliases: list[str] | None = Field(
        None, description='Aliases for the search quantity.'
    )
    aggregatable: bool = Field(
        False, description='Whether the search quantity is aggregatable.'
    )
    dynamic: bool = Field(False, description='Whether the search quantity is dynamic.')
    repeats: bool = Field(False, description='Whether the search quantity repeats.')
    nested: bool = Field(
        False, description='Whether the search quantity is inside a nested object.'
    )


class SearchQuantityQuery(BaseModel):
    input: str | None = Field(
        None,
        description='The input used for filtering down the returned search quantitities',
    )
    dtype: str | list[str] | None = Field(
        None, description='The data type of the search quantity.'
    )
    aggregatable: bool | None = Field(
        None, description='Whether the search quantity is aggregatable.'
    )


class SearchQuantityRequest(BaseModel):
    app_path: str | None = Field(
        None,
        description='Path of the app in which the search quantities should be searched for.',
    )
    query: SearchQuantityQuery = Field(
        SearchQuantityQuery(),
        description='Query for filtering search quantities.',
    )
    pagination: Pagination = Field(description='Controls the pagination of the values.')


def get_datatype(type_obj) -> DataType:
    """
    Converts a metainfo data type object to a simplified string representation.

    Args:
        type_obj (Any): The data type object.

    Returns:
        DataType: Simplified data type value.
    """
    type_data = (
        type_obj.get('type_data') if type_obj and isinstance(type_obj, dict) else None
    )
    type_kind = (
        type_obj.get('type_kind') if type_obj and isinstance(type_obj, dict) else None
    )

    if isinstance(type_data, str) and 'int' in type_data:
        return DataType.INT
    elif isinstance(type_data, str) and 'float' in type_data:
        return DataType.FLOAT
    elif type_data in [
        'nomad.metainfo.metainfo._Datetime',
        'nomad.metainfo.data_type.Datetime',
    ]:
        return DataType.TIMESTAMP
    elif type_data == 'str':
        return DataType.STRING
    elif isinstance(type_kind, str) and type_kind.lower() == 'enum':
        return DataType.ENUM
    elif type_data == 'bool':
        return DataType.BOOLEAN
    else:
        return DataType.UNKNOWN


def parse_quantity_name(full_name: str) -> dict[str, str | None]:
    """
    Used to split a quantity name into path, schema and dtype.
    """
    if not full_name:
        return {}

    parts = full_name.split(schema_separator, 1)
    if len(parts) == 2:
        path, schema = parts
    else:
        path, schema = full_name, None

    if schema:
        dtype_parts = schema.split(dtype_separator, 1)
        if len(dtype_parts) == 2:
            schema_new, dtype = dtype_parts
        else:
            schema_new, dtype = schema, None
    else:
        schema_new, dtype = None, None

    return {'path': path, 'schema': schema_new, 'dtype': dtype}


def parse_jmespath(input: str) -> dict[str, Any]:
    """
    Parses a JMESPath expression and extracts the targeted quantity along with additional details.
    """
    match = SCHEMA_REGEX.search(input)
    schema = ''
    path = input
    if match:
        schema = match.group(0)
        path = input[: match.start()] + input[match.end() :]

    try:
        ast = jmespath.compile(path).parsed
    except Exception as e:
        return {
            'quantity': None,
            'path': None,
            'extras': None,
            'error': str(e),
            'schema': '',
        }

    def traverse(node):
        # Scalar nodes do not affect paths
        if not isinstance(node, dict):
            return [], []

        # Fields extend the main path
        if node['type'] == 'field':
            return [node['value']], []

        # Recursively traverse child nodes to gather their paths
        children = node.get('children', [])
        node_type = node.get('type')
        node_value = node.get('value')
        child_path_lists = []
        child_aux_path_list = []
        main_path_list = []
        aux_paths = []
        for child in children:
            child_path_list, child_aux_paths = traverse(child)
            child_path_lists.append(child_path_list)
            child_aux_path_list.append(child_aux_paths)
        # In map functions we save expression reference in the reverse order
        if node_type == 'function_expression' and node_value == 'map':
            child_path_lists.reverse()
            for child_path in child_path_lists:
                main_path_list.extend(child_path)
        # In filter projections we save each filter field in extras. Note that there can
        # be multiple conditions
        elif node_type == 'filter_projection':
            for child_path in child_path_lists[0:2]:
                main_path_list.extend(child_path)
            for path in child_path_lists[2]:
                aux_path = []
                aux_path.extend(child_path_lists[0])
                aux_path.append(path)
                aux_paths.append(aux_path)
        # In *_by we save the referenced variable as an auxiliary path
        elif node_type == 'function_expression' and node_value in {'min_by', 'max_by'}:
            main_path_list.extend(child_path_lists[0])
            aux_path = []
            aux_path.extend(child_path_lists[0])
            aux_path.extend(child_path_lists[1])
            aux_paths.append(aux_path)
        #  For other types we simply extend definitions and extras in the order they are
        #  defined in
        else:
            for child_path in child_path_lists:
                main_path_list.extend(child_path)
            for child_aux_path in child_aux_path_list:
                aux_paths.extend(child_aux_path)

        return main_path_list, aux_paths

    main_path_list, aux_path_list = traverse(ast)
    quantity = '.'.join(main_path_list) + schema
    extras = set(['.'.join(x) + schema for x in aux_path_list])
    if quantity in extras:
        extras.remove(quantity)
    extras_list = list(extras)

    return {
        'quantity': quantity,
        'extras': extras_list,
        'path': path,
        'schema': schema,
        'error': None,
    }


def normalize_name(input: str) -> str:
    """
    Normalizes the input string.
    """
    return input.replace('_', ' ').replace('.', ' ').replace(' ', '').lower()


def get_search_quantity(
    search_quantity, name: str, is_section: bool = False, repeats: bool = False
) -> SearchQuantity:
    """
    Constructs a SearchQuantity object from metadata.
    """
    if is_section:
        keys = ['description', 'nested', 'repeats']
        metadict = search_quantity.sub_section.m_to_dict(with_meta=True)
        instanceMeta = search_quantity.m_to_dict(with_meta=True)
        metadict['repeats'] = repeats or instanceMeta.get('repeats')
        es_annotations = search_quantity.m_get_annotations(Elasticsearch, as_list=True)
        metadict['nested'] = any(x.nested for x in es_annotations)
    else:
        keys = [
            'description',
            'dtype',
            'unit',
            'shape',
            'aliases',
            'aggregatable',
            'dynamic',
            'repeats',
        ]
        metadict = search_quantity.definition.m_to_dict(with_meta=True)
        metadict['aggregatable'] = search_quantity.aggregatable
        metadict['dynamic'] = search_quantity.dynamic
        metadict['repeats'] = search_quantity.repeats
        metadict['dtype'] = get_datatype(metadict['type'])
    result = SearchQuantity(quantity=name, quantity_normalized=normalize_name(name))
    for key in keys:
        val = metadict.get(key)
        if val is not None:
            setattr(result, key, val)
    return result


def glob(path: str, include: list[str] | None, exclude: list[str] | None) -> bool:
    """
    Determines if path matches include and not exclude.
    """
    match: bool = False if include else True
    for pattern in include or []:
        if fnmatch.fnmatch(path, pattern):
            match = True
            break

    for pattern in exclude or []:
        if fnmatch.fnmatch(path, pattern):
            match = False
            break

    return match


def prefilter_search_quantities(
    search_quantities: dict[str, SearchQuantity], app_path: str
) -> list[SearchQuantity]:
    """Pre-filters for a specific app based on its entry point."""
    entry_point = app_entry_points_cache.get(app_path)
    assert entry_point is not None
    sq_filter = entry_point.app.search_quantities
    assert sq_filter is not None
    return [
        sq
        for key, sq in search_quantities.items()
        if glob(key, sq_filter.include, sq_filter.exclude)
    ]


def match_search_quantities(
    search_quantities: list[SearchQuantity], query: SearchQuantityQuery
) -> list[SearchQuantity]:
    """Filters and sorts search quantities."""
    dtypes = (
        {query.dtype}
        if isinstance(query.dtype, str)
        else set(query.dtype)
        if query.dtype
        else None
    )
    input_norm = normalize_name(query.input) if query.input else None
    filtered = [
        sq
        for sq in search_quantities
        if (dtypes is None or (sq.dtype.value if sq.dtype else None) in dtypes)
        and (query.aggregatable is None or query.aggregatable == sq.aggregatable)
        and (input_norm is None or input_norm in sq.quantity_normalized)
    ]
    if query.input:
        filtered.sort(key=lambda sq: len(sq.quantity) - len(query.input))
    return filtered


def initialize_search_quantities():
    """
    Race-safe initialization for the all search quantities; global and app-specific. This
    should be called only after all of the plugin have been loaded in order to have all of
    the search quantities available.
    """
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        _initialize_search_quantities()
        _initialized = True


def _initialize_search_quantities():
    """This is an internal function that does the heavy operations and makes it easier to
    test that the initialization works as expected.
    """
    # Entry points
    for entry_point in config.plugins.entry_points.filtered_values():
        if isinstance(entry_point, AppEntryPoint):
            app_entry_points_cache[entry_point.app.path] = entry_point
    # Base quantities
    all_search_quantities.clear()
    for key, value in entry_type.quantities.items():
        if not value.annotation.suggestion:
            all_search_quantities[key] = get_search_quantity(value, key)

    # Section quantities
    def _get_sections(m_def, prefix=None, repeats=False):
        for sub in m_def.all_sub_sections.values():
            name = f'{prefix}.{sub.name}' if prefix else sub.name
            info = get_search_quantity(sub, name, True, repeats)
            all_search_quantities[name] = info
            _get_sections(sub.sub_section, name, info.repeats)

    _get_sections(EntryArchive.results.sub_section, 'results')

    # Pre-build per-app filtered search quantities
    for app_path, ep in app_entry_points_cache.items():
        if app_path not in app_search_quantity_cache:
            app_search_quantity_cache[app_path] = prefilter_search_quantities(
                all_search_quantities, app_path
            )


@router.get(
    '/entry-points',
    tags=[APITag.DEFAULT],
    summary='Get all apps',
    response_model=dict[str, Any],
    response_model_exclude_none=True,
)
def get_entry_points(
    _user: Annotated[
        User,
        Depends(get_current_user([Scope.APPS_READ])),
    ],
):
    """Entry point for getting information about all apps"""
    initialize_search_quantities()

    apps = []
    for id, ep in app_entry_points_cache.items():
        app = ep.app.model_dump()
        app['id'] = id
        apps.append(app)
    return {'data': apps}


def _build_app_response(app: App) -> dict[str, Any]:
    search_quantities: dict[str, Any] = {}

    def add_jmespath(name: str, location: str | None = None):
        data = parse_jmespath(name)
        if data['error']:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Could not parse the app search quantity "{name}" used in {location}. Please verify that any JMESPath expressions are valid. Error: {data["error"]}',
            )
        for q in [data['quantity']] + data['extras']:
            add_search(q, location)

    def add_search(name: str, location: str | None = None):
        if name in search_quantities:
            return

        # Search quantities with an explicit dtype are not validated
        parsed = parse_quantity_name(name)
        if parsed['dtype']:
            return

        sq = all_search_quantities.get(name)
        if sq is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Could not load the app search quantity "{name}" used in {location}. Please check for typos and ensure that the corresponding schema package is installed and contains the requested definition. Note that currently only scalar quantities can be used in the apps, and data from e.g. lists or matrices cannot be accessed.',
            )
        search_quantities[name] = sq.model_dump()

    # Columns
    for column in app.columns or []:
        add_jmespath(column.search_quantity, 'the results table column')

    # Widgets
    if app.dashboard and app.dashboard.widgets:
        for widget in app.dashboard.widgets:
            if isinstance(widget, (WidgetPeriodicTable | WidgetTerms)):
                add_search(widget.search_quantity)
            elif isinstance(widget, WidgetHistogram) and widget.x:
                qty = (
                    widget.x if isinstance(widget.x, str) else widget.x.search_quantity
                )
                add_search(qty)
            elif isinstance(widget, WidgetScatterPlot):
                if widget.x:
                    add_jmespath(
                        widget.x.search_quantity
                        if not isinstance(widget.x, str)
                        else widget.x,
                        'the x-axis of a scatter plot widget',
                    )
                if widget.y:
                    add_jmespath(
                        widget.y.search_quantity
                        if not isinstance(widget.y, str)
                        else widget.y,
                        'the y-axis of a scatter plot widget',
                    )
                if widget.markers and widget.markers.color:
                    cd = widget.markers.color
                    add_jmespath(
                        cd.search_quantity if not isinstance(cd, str) else cd,
                        'the marker color of a scatter plot widget',
                    )

    # Menus
    def load_menu(menu: Menu | MenuItemNestedObject):
        for item in menu.items or []:
            if isinstance(item, Menu):
                load_menu(item)
            if isinstance(item, MenuItemNestedObject):
                add_search(item.path, 'a menu nested object item')
                load_menu(item)
            elif isinstance(item, MenuItemTerms):
                add_search(item.search_quantity, 'a menu terms item')
            elif isinstance(item, MenuItemHistogram):
                if isinstance(item.x, str):
                    add_search(item.x, 'a menu histogram item')
                else:
                    add_search(item.x.search_quantity, 'a menu histogram item')
            elif isinstance(item, MenuItemPeriodicTable):
                add_search(item.search_quantity, 'a menu periodic table item')

    if app.menu:
        load_menu(app.menu)
    # Filters_locked
    for key in (app.filters_locked or {}).keys():
        add_search(key, 'filters_locked')

    return {
        'app': app.model_dump(),
        'search_quantities': search_quantities,
    }


@router.get(
    '/entry-points/{app_path}',
    tags=[APITag.DEFAULT],
    summary='Get a specific app',
    response_model=dict[str, Any],
    response_model_exclude_none=True,
    responses=create_responses(_bad_app_not_found, _bad_search_quantity_parse),
)
def get_entry_point(
    app_path: str,
    _user: Annotated[
        User,
        Depends(get_current_user([Scope.APPS_READ])),
    ],
):
    """Entry point for getting information about a specific app"""
    initialize_search_quantities()

    # Check that the entry points exists
    entry_point = app_entry_points_cache.get(app_path)
    if entry_point is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'Could not find an app with the path "{app_path}".',
        )

    # Check the cache, if not found populate it
    if app_path not in app_cache:
        app_cache[app_path] = _build_app_response(entry_point.app)

    return app_cache[app_path]


@router.post(
    '/validate',
    tags=[APITag.DEFAULT],
    summary='Validate an app setup',
    response_model=dict[str, Any],
    response_model_exclude_none=True,
    responses=create_responses(_bad_search_quantity_parse),
)
def validate(
    app: App,
    _user: Annotated[
        User,
        Depends(get_current_user([Scope.APPS_READ])),
    ],
):
    """Validates the given app configuration and return the validated version together
    with the search quantities that are used in it.
    """
    initialize_search_quantities()

    return _build_app_response(app)


@router.post(
    '/search-quantities',
    tags=[APITag.DEFAULT],
    summary='Search and filter search quantities',
    response_model=list[SearchQuantity],
    response_model_exclude_none=True,
    responses=create_responses(_bad_app_not_found),
)
def get_entry_point_search_quantities(
    data: SearchQuantityRequest,
    _user: Annotated[
        User,
        Depends(get_current_user([Scope.APPS_READ])),
    ],
):
    """Entry point for suggestions for search quantities"""
    initialize_search_quantities()

    if data.app_path:
        if data.app_path not in app_entry_points_cache:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f'Could not find an app with the path "{data.app_path}".',
            )
        sqs = app_search_quantity_cache.get(data.app_path)
        if sqs is None:
            sqs = prefilter_search_quantities(all_search_quantities, data.app_path)
            app_search_quantity_cache[data.app_path] = sqs
    else:
        sqs = list(all_search_quantities.values())
    matched = match_search_quantities(sqs, data.query)
    page = data.pagination.page or 1
    size = data.pagination.page_size or 10
    return matched[(page - 1) * size : page * size]
