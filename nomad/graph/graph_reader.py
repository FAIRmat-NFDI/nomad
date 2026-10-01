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

import asyncio
import copy
import dataclasses
import functools
import os
import re
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager, nullcontext
from threading import Lock
from typing import Any, TypeAlias

import orjson
from cachetools import TTLCache
from fastapi import HTTPException
from mongoengine import Q
from msglc.reader import LazyDict, LazyList, LazyReader
from pydantic import ValidationError

from nomad import utils
from nomad.app.v1.models import (
    Metadata,
    MetadataPagination,
    Pagination,
    PaginationResponse,
    UserGroupPagination,
    UserGroupQuery,
)
from nomad.app.v1.routers.datasets import DatasetPagination
from nomad.app.v1.routers.entries import perform_search
from nomad.app.v1.routers.uploads import (
    EntryProcDataPagination,
    RawDirPagination,
    UploadProcDataPagination,
    UploadProcDataQuery,
    entry_to_pydantic,
    get_upload_with_read_access,
    upload_to_pydantic,
)
from nomad.archive import ArchiveDict, ArchiveList, to_json
from nomad.archive.storage_v2 import ArchiveDict as ArchiveDictNew
from nomad.archive.storage_v2 import ArchiveList as ArchiveListNew
from nomad.datamodel import Dataset, EntryArchive, ServerContext, User
from nomad.datamodel.metainfo.plot import PlotlyFigure
from nomad.datamodel.util import parse_path
from nomad.files import RawPathInfo, UploadFiles, measure_fs_reads
from nomad.graph.lazy_wrapper import (
    CachedUpload,
    LazyUploadFailureCount,
    LazyUploadSuccessCount,
    LazyUploadTotalCount,
    LazyUserWrapper,
)
from nomad.graph.model import (
    _WILDCARD_FROZENSET,
    ArchivePagination,
    DatasetQuery,
    DefinitionType,
    DirectiveType,
    EntryQuery,
    MDefFormatType,
    MetainfoPagination,
    MetainfoQuery,
    RequestConfig,
    ResolveType,
)
from nomad.layouts import (
    build_layout_context,
    create_layout_plan,
    get_layout_query_intent,
)
from nomad.metainfo import (
    Definition,
    MSectionReference,
    Package,
    Quantity,
    QuantityReference,
    Reference,
    Section,
    SubSection,
)
from nomad.metainfo.data_type import Datatype
from nomad.metainfo.util import MSubSectionList, split_python_definition
from nomad.mongo.groups import MongoUserGroup, get_mongo_user_group
from nomad.mongo.package import PackageDefinition
from nomad.mongo.search import MongoQueryError, create_mongo_query
from nomad.processing import Entry, Upload
from nomad.tracing import trace_span, traced

logger = utils.get_logger(__name__)


GenericList: TypeAlias = list | ArchiveList | ArchiveListNew | LazyList
GenericDict: TypeAlias = dict | ArchiveDict | ArchiveDictNew | LazyDict


@dataclasses.dataclass(frozen=True)
class _LayoutArchiveRequest:
    raw: dict[str, Any]
    normalized: dict | RequestConfig


@dataclasses.dataclass(frozen=True)
class _EntryLayoutReadPlan:
    """Precomputed entry/response/archive updates for layout-aware reads."""

    entry_updates: dict[str, Any] = dataclasses.field(default_factory=dict)
    response_updates: dict[str, Any] = dataclasses.field(default_factory=dict)
    archive_request: _LayoutArchiveRequest | None = None


_OBJECT_SETATTR = object.__setattr__


def _copy_request_config(config: RequestConfig, **updates: Any) -> RequestConfig:
    """Copy an already validated config."""

    # The internal shortcut is deliberately limited to the exact model whose
    # invariants are known here. Subclasses may add fields, private attributes,
    # or a different extra policy and must use Pydantic's supported copy path.
    if (
        type(config) is not RequestConfig
        or not updates.keys() <= RequestConfig.model_fields.keys()
    ):
        copied = config.model_copy(update=updates)
        copied.__dict__.pop('hash', None)
        return copied

    copied = object.__new__(type(config))
    d = config.__dict__.copy()
    d.update(updates)
    d.pop('hash', None)
    _OBJECT_SETATTR(copied, '__dict__', d)
    _OBJECT_SETATTR(
        copied,
        '__pydantic_fields_set__',
        config.__pydantic_fields_set__ | updates.keys(),
    )
    _OBJECT_SETATTR(
        copied,
        '__pydantic_extra__',
        None if config.__pydantic_extra__ is None else config.__pydantic_extra__.copy(),
    )
    _OBJECT_SETATTR(
        copied,
        '__pydantic_private__',
        None
        if config.__pydantic_private__ is None
        else config.__pydantic_private__.copy(),
    )
    return copied


@dataclasses.dataclass
class _ReaderCache:
    """State shared by readers participating in one graph request."""

    server_contexts: dict[str | None, ServerContext] = dataclasses.field(
        default_factory=dict
    )
    definitions: dict[tuple[str | None, str | None, str | None, str | None], Any] = (
        dataclasses.field(default_factory=dict)
    )
    short_definition_references: dict[int, tuple[Any, tuple[int, ...], str]] = (
        dataclasses.field(default_factory=dict)
    )
    property_definitions: dict[tuple[int, str], tuple[Any, Any]] = dataclasses.field(
        default_factory=dict
    )


def _normalize_layout_archive_request(
    raw_request: dict[str, Any],
) -> _LayoutArchiveRequest:
    normalized_request, _ = _parse_required(raw_request, ArchiveReader)
    return _LayoutArchiveRequest(raw=raw_request, normalized=normalized_request)


@dataclasses.dataclass(frozen=True)
class Token:
    """
    Define the special tokens used to link one model/document/database to another.
    Ideally, these tokens should not collide with any existing keys.
    It is thus recommended to use a prefix to avoid collision.
    """

    DEF = 'm_def'
    DEFID = 'm_def_id'
    RAW = 'files'
    ARCHIVE = 'archive'
    ENTRY = 'entry'
    ENTRIES = 'entries'
    UPLOAD = 'upload'
    UPLOADS = 'uploads'
    USER = 'user'
    USERS = 'users'
    DATASET = 'dataset'
    DATASETS = 'datasets'
    GROUP = 'group'
    GROUPS = 'groups'
    METAINFO = 'metainfo'
    SEARCH = 'search'
    METADATA = 'metadata'
    MAINFILE = 'mainfile'
    RESPONSE = 'm_response'
    ERROR = 'm_errors'


@dataclasses.dataclass(frozen=True)
class QueryError:
    NOACCESS = 'NOACCESS'
    NOTFOUND = 'NOTFOUND'
    ARCHIVEERROR = 'ARCHIVEERROR'
    GENERAL = 'GENERAL'


def dataset_to_pydantic(item):
    """
    Do NOT optimise this function.
    Function names are used to determine the type of the object.
    """
    return item.to_json()


def group_to_pydantic(item):
    """
    Do NOT optimise this function.
    Function names are used to determine the type of the object.
    """
    return item.to_json()


class ArchiveError(Exception):
    """
    An exception raised when an error occurs in the archive.
    """

    pass


class ConfigError(Exception):
    """
    An exception raised when an error occurs in the configuration.
    """

    pass


async def goto_child(container, key: str | int | list):
    if not isinstance(key, list):
        return container[key]

    target = container
    for v in key:
        target = await goto_child(target, v)
    return target


async def async_get(container, key, default=None):
    return container.get(key, default)

    # if isinstance(container, dict):
    #     return container.get(key, default)
    #
    # return await asyncio.to_thread(container.get, key, default)


async def async_to_json(data):
    return to_json(data)

    # return await asyncio.to_thread(to_json, data)


_NODE_SENTINEL = object()


@dataclasses.dataclass(slots=True)
class GraphNode:
    upload_id: str  # the upload id of the current node
    entry_id: str  # the entry id of the current node
    current_path: list[str]  # the path in the result container
    result_root: dict  # the root of the result container
    ref_result_root: dict  # collection of referenced archives that should never change
    archive: Any  # current node in the archive
    archive_root: None | ArchiveDict | dict  # the root of the archive
    definition: Any  # definition of the current node
    visited_path: set[str]  # visited paths, for tracking circular references
    current_depth: int  # current depth, for tracking depth limit
    reader: Any  # the reader used to read the archive # pylint: disable=E0601
    result_cursor: ResultCursor | None = None

    def descend(self, key: str, **kwargs) -> GraphNode:
        return self.replace(
            current_path=self.current_path + [key],
            result_cursor=_node_cursor(self).child(key),
            **kwargs,
        )

    def replace(
        self,
        upload_id: Any = _NODE_SENTINEL,
        entry_id: Any = _NODE_SENTINEL,
        current_path: Any = _NODE_SENTINEL,
        result_root: Any = _NODE_SENTINEL,
        ref_result_root: Any = _NODE_SENTINEL,
        archive: Any = _NODE_SENTINEL,
        archive_root: Any = _NODE_SENTINEL,
        definition: Any = _NODE_SENTINEL,
        visited_path: Any = _NODE_SENTINEL,
        current_depth: Any = _NODE_SENTINEL,
        reader: Any = _NODE_SENTINEL,
        result_cursor: Any = _NODE_SENTINEL,
    ):
        """
        Create a new `ArchiveNode` instance with the attributes of the current instance replaced.
        """
        new_current_path = (
            self.current_path if current_path is _NODE_SENTINEL else current_path
        )
        new_result_cursor = (
            self.result_cursor if result_cursor is _NODE_SENTINEL else result_cursor
        )
        if (
            result_cursor is _NODE_SENTINEL
            and current_path is not _NODE_SENTINEL
            and current_path != self.current_path
        ):
            new_result_cursor = None
        return GraphNode(
            self.upload_id if upload_id is _NODE_SENTINEL else upload_id,
            self.entry_id if entry_id is _NODE_SENTINEL else entry_id,
            new_current_path,
            self.result_root if result_root is _NODE_SENTINEL else result_root,
            self.ref_result_root
            if ref_result_root is _NODE_SENTINEL
            else ref_result_root,
            self.archive if archive is _NODE_SENTINEL else archive,
            self.archive_root if archive_root is _NODE_SENTINEL else archive_root,
            self.definition if definition is _NODE_SENTINEL else definition,
            self.visited_path if visited_path is _NODE_SENTINEL else visited_path,
            self.current_depth if current_depth is _NODE_SENTINEL else current_depth,
            self.reader if reader is _NODE_SENTINEL else reader,
            new_result_cursor,
        )

    @property
    def _prefix_to_remove(self) -> str:
        """Duplicated prefix to remove."""
        return f'uploads/{self.upload_id}/entries/{self.entry_id}/archive/'

    def generate_reference(self, path: list | None = None) -> str:
        """
        Generate a reference string using a given path or the current path.
        """
        actual_path: list = path if path is not None else self.current_path
        actual_ref: str = (
            '/'.join(actual_path)
            if actual_path and isinstance(actual_path[0], str)
            else '/'.join(str(v) for v in actual_path)
        )
        if self.upload_id and self.entry_id:
            actual_ref = actual_ref.removeprefix(self._prefix_to_remove)
        return f'{self._generate_prefix()}#/{actual_ref}'

    def _generate_prefix(self) -> str:
        return f'../uploads/{self.upload_id}/archive/{self.entry_id}'

    async def goto(self, reference: str, resolve_inplace: bool) -> GraphNode:
        if reference.startswith(('/', '#')):
            return await self._goto_local(reference, resolve_inplace)

        return await self._goto_remote(reference, resolve_inplace)

    async def _goto_local(self, reference: str, resolve_inplace: bool) -> GraphNode:
        """
        Go to a local reference.
        Since it is a local reference, only need to walk to the proper position.
        """
        path_stack: list = [v for v in reference.lstrip('/#').split('/') if v]

        if (reference_url := self.generate_reference(path_stack)) in self.visited_path:
            raise ArchiveError(f'Circular reference detected: {reference_url}.')

        try:
            if self.archive_root is None:
                raise ArchiveError(f'Archive root is None for {reference}.')
            target = await _goto_path(self.archive_root, path_stack)
        except (KeyError, IndexError):
            raise ArchiveError(f'Archive {self.entry_id} does not contain {reference}.')

        return await self._switch_root(
            self.replace(
                archive=target, visited_path=self.visited_path.union({reference_url})
            ),
            resolve_inplace,
            reference_url,
        )

    async def _goto_remote(self, reference: str, resolve_inplace: bool) -> GraphNode:
        """
        Go to a remote archive, which can be either in the same server or another installation.
        """
        # this is a global reference, get the target archive first
        if (parse_result := parse_path(reference, self.upload_id)) is None:
            raise ArchiveError(f'Invalid reference: {reference}.')

        installation, other_upload_id, id_or_file, kind, path = parse_result

        if installation is not None:
            # todo: support cross installation reference
            raise ArchiveError(
                f'Cross installation reference is not supported yet: {reference}.'
            )

        if kind == 'raw':
            # it is a path to raw file
            # get the corresponding entry id
            other_entry: Entry = Entry.objects(  # type: ignore
                upload_id=other_upload_id, mainfile=id_or_file
            ).first()
            if not other_entry:
                # cannot find the entry, None will be identified in the caller
                raise ArchiveError(
                    f'File {id_or_file} does not exist in upload {other_upload_id}.'
                )

            other_entry_id = other_entry.entry_id
        else:
            # it is an entry id
            other_entry_id = id_or_file

        path_stack: list = [v for v in path.split('/') if v]

        other_prefix: str = f'../uploads/{other_upload_id}/archive/{other_entry_id}'
        if (
            reference_url := f'{other_prefix}#/{"/".join(str(v) for v in path_stack)}'
        ) in self.visited_path:
            raise ArchiveError(f'Circular reference detected: {reference_url}.')

        # get the archive
        with self.reader.load_archive(
            other_upload_id, other_entry_id
        ) as other_archive_root:
            if other_archive_root is None:
                raise ArchiveError(
                    f'Could not load archive for {other_upload_id}/{other_entry_id}.'
                )

            try:
                # now go to the target path
                other_target = await _goto_path(other_archive_root, path_stack)
            except (KeyError, IndexError):
                raise ArchiveError(f'Archive {other_entry_id} does not contain {path}.')

            return await self._switch_root(
                self.replace(
                    upload_id=other_upload_id,
                    entry_id=other_entry_id,
                    visited_path=self.visited_path.union({reference_url}),
                    archive=other_target,
                    archive_root=other_archive_root,
                ),
                resolve_inplace,
                reference_url,
            )

    async def _switch_root(
        self, node: GraphNode, resolve_inplace: bool, reference_url: str
    ):
        if resolve_inplace:
            return node

        ref_path_string = _convert_ref_to_path_string(reference_url)
        _node_cursor(self).set(ref_path_string)

        target_path = _convert_ref_to_path(reference_url)
        return node.replace(
            current_path=target_path,
            result_root=self.ref_result_root,
            result_cursor=ResultCursor.from_path(self.ref_result_root, target_path),
        )


async def _goto_path(target_root: GenericDict, path_stack: list) -> Any:
    """
    Go to the specified path in the data.
    """
    for key in path_stack:
        target_root = await goto_child(target_root, int(key) if key.isdigit() else key)
    return target_root


async def _if_exists(target_root: dict, path_stack: list) -> bool:
    """
    Check if specified path in the data.
    """
    try:
        for key in path_stack:
            target_root = await goto_child(
                target_root, int(key) if key.isdigit() else key
            )
    except (KeyError, IndexError):
        return False
    return target_root is not None


@functools.lru_cache(maxsize=1024)
def _convert_ref_to_path(ref: str, upload_id: str | None = None) -> list:
    # Parse archive references before checking for Python definition names. Raw
    # mainfile paths commonly contain dots (for example ``vasprun.xml.gz``),
    # which must not make an otherwise valid archive reference look like a
    # metainfo reference.
    parse_result = parse_path(ref, upload_id)
    if parse_result is None:
        stripped_ref = ref.strip('.')
        if '.' in stripped_ref or re.compile(r'^\w*(\.\w*)*$').match(
            ref.split('/section_definitions', maxsplit=1)[0]
        ):
            module_path, _ = split_python_definition(stripped_ref)
            return [Token.METAINFO, '.'.join(module_path[:-1])] + module_path[-1].split(
                '/'
            )
        raise ArchiveError(f'Invalid reference: {ref}.')

    installation, upload_id, entry_id, kind, path = parse_result
    if upload_id is None:
        raise ArchiveError(f'Invalid reference: {ref}, "upload_id" should be present.')

    path_stack: list = []

    if installation:
        path_stack.append(installation)

    path_stack.append(Token.UPLOADS)
    path_stack.append(upload_id)

    if kind == 'raw':
        path_stack.append(Token.RAW)
        path_stack.extend(v for v in entry_id.split('/') if v)
        path_stack.append(Token.ENTRIES)
    else:
        path_stack.append(Token.ENTRIES)
        path_stack.append(entry_id)

    path_stack.append(Token.ARCHIVE)
    path_stack.extend(v for v in path.split('/') if v)

    return path_stack


@functools.lru_cache(maxsize=1024)
def _convert_ref_to_path_string(ref: str, upload_id: str | None = None) -> str:
    return '/'.join(_convert_ref_to_path(ref, upload_id))


def _to_response_config(config: RequestConfig, exclude: list | None = None, **kwargs):
    response_config = config.model_dump(exclude_unset=True, exclude_none=True)

    for item in ('include', 'exclude'):
        if isinstance(x := response_config.pop(item, None), frozenset):
            response_config[item] = list(x)  # type: ignore

    response_config.pop('property_name', None)
    if exclude:
        for item in exclude:
            response_config.pop(item, None)
    response_config.update(kwargs)
    return response_config


def _merge_result_list(target: list, source: list):
    for index, value in enumerate(source):
        if index >= len(target):
            target.append(value)
        elif isinstance(target[index], dict) and isinstance(value, dict):
            _merge_result_dict(target[index], value)
        elif isinstance(target[index], list) and isinstance(value, list):
            _merge_result_list(target[index], value)
        elif target[index] is None:
            target[index] = value
        elif value is not None and target[index] != value:
            logger.warning(
                f'Cannot merge {target[index]} and {value}, potential conflicts.'
            )


def _merge_result_dict(target: dict, source: dict):
    for key, value in source.items():
        if key not in target or target[key] is None:
            target[key] = value
        elif isinstance(target[key], set) and isinstance(value, set):
            target[key].update(value)
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _merge_result_dict(target[key], value)
        elif isinstance(target[key], list) and isinstance(value, list):
            _merge_result_list(target[key], value)
        elif value is not None and target[key] != value:
            logger.warning(
                f'Cannot merge {target[key]} and {value}, potential conflicts.'
            )


_SCALAR_TYPES = (str, int, float, bool)


def _slot_get(container: dict | list, key: str | int) -> Any:
    if isinstance(container, dict):
        assert isinstance(key, str)
        return container.get(key)
    assert isinstance(key, int)
    if (extra := max(key - len(container) + 1, 0)) > 0:
        container.extend([None] * extra)
    return container[key]


def _slot_set(container: dict | list, key: str | int, value: Any) -> None:
    if isinstance(container, dict):
        assert isinstance(key, str)
        container[key] = value
    else:
        assert isinstance(key, int)
        if (extra := max(key - len(container) + 1, 0)) > 0:
            container.extend([None] * extra)
        container[key] = value


class ResultCursor:
    """Denotes a slot (parent container + key) in the result tree.

    Nothing exists in the result tree until ``set()`` or ``container()`` is
    called. Digit keys denote list indices, mirroring ``_populate_result``.
    """

    __slots__ = ('_parent', '_key', '_container')

    def __init__(
        self,
        container: dict | list | None = None,
        *,
        parent: ResultCursor | None = None,
        key: str | int | None = None,
    ):
        self._parent = parent
        self._key = key
        self._container = container

    @classmethod
    def from_path(cls, root: dict, path: list[str]) -> ResultCursor:
        cursor = cls(root)
        for segment in path:
            cursor = cursor.child(segment)
        return cursor

    def child(self, key: str) -> ResultCursor:
        key_or_index: str | int = int(key) if key.isdigit() else key
        return ResultCursor(parent=self, key=key_or_index)

    def _parent_container(self) -> dict | list:
        assert self._parent is not None and self._key is not None
        return self._parent.container(list if isinstance(self._key, int) else dict)

    def container(self, kind: type[dict] | type[list]) -> dict | list:
        if self._parent is None:
            assert isinstance(self._container, kind)
            return self._container

        if self._container is not None and isinstance(self._container, kind):
            return self._container

        parent = self._parent_container()
        key = self._key
        assert key is not None
        existing = _slot_get(parent, key)
        if isinstance(existing, kind):
            child = existing
        else:
            child = kind()
            _slot_set(parent, key, child)
        self._container = child
        return child

    def set(self, value: Any) -> None:
        if self._parent is None:
            assert isinstance(self._container, dict)
            new_value = to_json(value)
            assert isinstance(new_value, dict)
            _merge_result_dict(self._container, new_value)
            return

        parent = self._parent_container()
        key = self._key
        assert key is not None
        _write_result_at(parent, key, value)


def _write_result_at(
    target_container: dict | list,
    key_or_index: str | int,
    value: Any,
) -> None:
    val_type = type(value)
    if val_type in _SCALAR_TYPES or value is None:
        _slot_set(target_container, key_or_index, value)
        return

    new_value = to_json(value)
    if new_value is value and isinstance(value, dict | list):
        new_value = value.copy()

    existing = _slot_get(target_container, key_or_index)

    if existing is None:
        _slot_set(target_container, key_or_index, new_value)
    elif isinstance(new_value, dict) and isinstance(existing, dict):
        _merge_result_dict(existing, new_value)
    elif isinstance(new_value, list) and isinstance(existing, list):
        _merge_result_list(existing, new_value)
    else:
        _slot_set(target_container, key_or_index, new_value)


def _node_cursor(node: GraphNode) -> ResultCursor:
    """The node's result cursor, derived lazily from its path for readers that do not track one."""
    return node.result_cursor or ResultCursor.from_path(
        node.result_root, node.current_path
    )


async def _populate_result(
    container_root: dict,
    path: list,
    value,
    *,
    path_like=False,
    overwrite_existing_str=False,
):
    """
    For the given path and the root of the target container, populate the value.

    If `path_like` is set to `True`,
    the path will be treated as a true file system path such that
    numerical values are not interpreted as list indices.
    """

    def _set_default(
        container: dict | list, k_or_i: str | int, value_type: type
    ) -> dict | list:
        """
        Initialise empty containers at the given key or index.
        """
        if isinstance(container, dict):
            assert isinstance(k_or_i, str)
            if overwrite_existing_str and isinstance(container.get(k_or_i, None), str):
                container[k_or_i] = value_type()
            else:
                container.setdefault(k_or_i, value_type())
            return container[k_or_i]

        assert isinstance(k_or_i, int)
        if container[k_or_i] is None:
            container[k_or_i] = value_type()
        elif overwrite_existing_str and isinstance(container[k_or_i], str):
            container[k_or_i] = value_type()
        return container[k_or_i]

    if len(path) == 0:
        assert isinstance(container_root, dict) and isinstance(
            new_value := to_json(value), dict
        )
        _merge_result_dict(container_root, new_value)
        return

    target_container: dict | list = container_root
    key_or_index: None | str | int = None

    stack_idx: int = 0
    while stack_idx < len(path):
        if key_or_index is not None:
            target_container = _set_default(target_container, key_or_index, dict)
        key_or_index = path[stack_idx]
        stack_idx += 1
        if path_like:
            continue
        while stack_idx < len(path) and path[stack_idx].isdigit():
            target_container = _set_default(target_container, key_or_index, list)
            key_or_index = int(path[stack_idx])
            if (extra_element := max(key_or_index - len(target_container) + 1, 0)) > 0:
                target_container.extend([None] * extra_element)  # type: ignore
            stack_idx += 1

    # the target container does not necessarily have to be a dict or a list
    # if the result is striped due to large size, it will be replaced by a string
    val_type = type(value)
    if val_type in _SCALAR_TYPES or value is None:
        new_value = value
    else:
        new_value = await async_to_json(value)
        if new_value is value and isinstance(value, dict | list):
            new_value = value.copy()

    if isinstance(target_container, list):
        assert isinstance(key_or_index, int)
        if target_container[key_or_index] is None:
            target_container[key_or_index] = new_value
        elif isinstance(target_container[key_or_index], str) and overwrite_existing_str:
            target_container[key_or_index] = new_value
        elif isinstance(new_value, dict):
            _merge_result_dict(target_container[key_or_index], new_value)
        elif isinstance(new_value, list):
            _merge_result_list(target_container[key_or_index], new_value)
        else:
            target_container[key_or_index] = new_value
    elif isinstance(target_container, dict):
        assert isinstance(key_or_index, str)
        if (
            isinstance(target_container.get(key_or_index, None), str)
            and overwrite_existing_str
        ):
            target_container[key_or_index] = new_value
        elif isinstance(new_value, dict):
            target_container.setdefault(key_or_index, {})
            _merge_result_dict(target_container[key_or_index], new_value)
        elif isinstance(new_value, list):
            target_container.setdefault(key_or_index, [])
            _merge_result_list(target_container[key_or_index], new_value)
        else:
            target_container[key_or_index] = new_value


@functools.lru_cache(maxsize=1024)
def _parse_key(key: str | None) -> tuple:
    """
    Parse the name and index from the given key.
    The actual length of the target list is not known at this moment.
    The normalisation will be done when reading the actual data.
    """
    if key is None:
        return None, None

    # A[1]
    if matches := re.match(r'^([a-zA-z_\d]+)\[(-?\d+)]$', key):
        name = matches.group(1)
        start = int(matches.group(2))
        return name, (start,)

    # A[1:], A[:1], A[1:2]
    if matches := re.match(r'^([a-zA-z_\d]+)\[(-?\d+)?:(-?\d+)?]$', key):
        name = matches.group(1)
        start = int(matches.group(2)) if matches.group(2) else 0
        end = int(matches.group(3)) if matches.group(3) else None
        return name, (start, end)

    return key, None


def _normalise_required(
    required,
    config: RequestConfig,
    *,
    key: str | None = None,
    reader_type: type[GeneralReader] | None = None,
):
    """
    Normalise the required dictionary.
    On exit, all leaves must be `RequestConfig` instances.
    """
    if isinstance(required, list):
        return [
            None
            if v is None
            else _normalise_required(v, config, key=key, reader_type=reader_type)
            for v in required
        ]

    name, index = _parse_key(key)

    # discard pagination and query so that they are not passed to the children
    config_dict: dict = {
        'property_name': name,
        'index': index,
        'pagination': None,
        'query': None,
    }

    can_query: bool = False

    if name in GeneralReader.__USER_ID__ or name == Token.USER or name == Token.USERS:
        reader_type = UserReader
    elif name == Token.GROUP or name == Token.GROUPS:
        reader_type = UserGroupReader
        can_query = True
    elif name in GeneralReader.__UPLOAD_ID__:
        reader_type = UploadReader
    elif name == Token.UPLOAD or name == Token.UPLOADS:
        reader_type = UploadReader
        can_query = True
    elif name in GeneralReader.__DATASET_ID__:
        reader_type = DatasetReader
    elif name == Token.DATASET or name == Token.DATASETS:
        reader_type = DatasetReader
        can_query = True
    elif name in GeneralReader.__ENTRY_ID__:
        reader_type = EntryReader
    elif name == Token.ENTRY or name == Token.ENTRIES:
        reader_type = EntryReader
        can_query = True
    elif name == Token.SEARCH:
        reader_type = ElasticSearchReader
        can_query = True
    elif name == Token.METAINFO:
        reader_type = MetainfoBrowser
        can_query = True
    elif name == Token.RAW or name == Token.MAINFILE:
        reader_type = FileSystemReader
    elif name == Token.ARCHIVE:
        reader_type = ArchiveReader

    _validate_config = functools.partial(
        reader_type.validate_config, key if key else 'top level'
    )

    # for backward compatibility
    if isinstance(required, str):
        if required in ('*', 'include'):
            return _validate_config(config.new(dict(config_dict, directive='plain')))
        if required == 'include-resolved':
            return _validate_config(config.new(dict(config_dict, directive='resolved')))

    if isinstance(required, dict):
        # check if there is a config dict
        new_config_dict = required.pop(GeneralReader.__CONFIG__, {})

        def _populate_query(_config_dict):
            if can_query:
                if new_query := required.pop('query', None):
                    _config_dict['query'] = new_query
                if new_pagination := required.pop('pagination', None):
                    _config_dict['pagination'] = new_pagination
                if GeneralReader.__WILDCARD__ in required:
                    _config_dict.setdefault(
                        'pagination', {'page': 1}
                    )  # a default pagination

        if isinstance(new_config_dict, dict):
            _populate_query(new_config_dict)
            combined: RequestConfig = config.new(dict(config_dict, **new_config_dict))
        elif isinstance(new_config_dict, str):
            child_config_dict: dict = {}
            if new_config_dict in ('*', 'include'):
                child_config_dict['directive'] = 'plain'
            elif new_config_dict == 'include-resolved':
                child_config_dict['directive'] = 'resolved'
            else:
                raise ConfigError(f'Invalid config: {new_config_dict}.')
            _populate_query(child_config_dict)
            combined = config.new(dict(config_dict, **child_config_dict))
        else:
            raise ConfigError(f'Invalid config: {new_config_dict}.')

        if len(required) == 0:
            # no more children, it is a leaf config
            return _validate_config(combined)

        # otherwise, it is a nested query
        subtree: dict = {
            k: _normalise_required(v, combined, key=k, reader_type=reader_type)
            for k, v in required.items()
        }
        if new_config_dict:
            # if there is a config dict, we need to add it to the subtree
            subtree[GeneralReader.__CONFIG__] = _validate_config(combined)
        return subtree

    # all other cases are not valid
    raise ConfigError(f'Invalid required config: {required}.')


def _parse_required(
    required_query: dict | str, reader_type: type[GeneralReader]
) -> tuple[dict | RequestConfig, RequestConfig]:
    # extract global config if present
    # do not modify the original dict as the same dict may be used elsewhere
    if isinstance(required_query, dict):
        required_copy = copy.deepcopy(required_query)

        global_dict: dict = {}

        # for backward compatibility
        resolve_inplace: bool | None = None
        for key in ('resolve_inplace', 'resolve-inplace'):
            if resolve_inplace is None:
                resolve_inplace = required_copy.pop(key, None)
        if isinstance(resolve_inplace, bool):
            global_dict['resolve_inplace'] = resolve_inplace

        # normalise the query by replacing '-' with '_'
        global_config = RequestConfig.model_validate(global_dict)
        # extract query config for each field
        return _normalise_required(
            required_copy, global_config, reader_type=reader_type
        ), global_config

    if required_query in ('*', 'include'):
        # for backward compatibility
        global_config = RequestConfig.model_validate({'directive': 'plain'})
        return global_config, global_config

    if required_query == 'include-resolved':
        # for backward compatibility
        global_config = RequestConfig.model_validate({'directive': 'resolved'})
        return global_config, global_config

    raise ConfigError(f'Invalid required config: {required_query}.')


@functools.lru_cache(maxsize=1024)
def _normalise_index(index: tuple | None, length: int) -> range:
    """
    Normalise the index to a [-length,length).
    """
    # all items
    if index is None:
        return range(length)

    def _bound(v):
        if v < -length:
            return 0
        if v >= length:
            return length - 1
        return length + v if v < 0 else v

    # one item
    if len(index) == 1:
        start = _bound(index[0])
        return range(start, start + 1)

    # a range of items
    start, end = index
    if start is None:
        start = 0
    if end is None:
        end = length

    return range(_bound(start), _bound(end) + 1)


def _child_pagination(config: RequestConfig) -> ArchivePagination:
    """
    The pagination that the children of a paginated node inherit: the page size.
    The page only applies to the requested repeating subsection.
    """
    assert isinstance(config.pagination, ArchivePagination)
    return ArchivePagination(page_size=config.pagination.page_size)


def _unwrap_subsection(target):
    return target.sub_section.m_resolved() if isinstance(target, SubSection) else target


def _explicit_indices(required: dict, name: str, length: int) -> set[int]:
    """
    The indices of the list `name` that are explicitly requested as `name[i]` keys.
    """
    indices: set[int] = set()
    for key in required:
        if key == GeneralReader.__CONFIG__:
            continue
        other_name, other_index = _parse_key(key)
        if other_name == name and other_index is not None:
            indices.update(_normalise_index(other_index, length))
    return indices


def _get_property_definition(node: GraphNode, name: str):
    """
    Resolve a property definition for the current node, falling back to the
    archive object's runtime definition when the carried graph definition is
    missing inherited properties.
    """

    for candidate in (
        getattr(node, 'definition', None),
        getattr(node.archive, 'm_def', None),
    ):
        if candidate is None:
            continue

        cache = node.reader._reader_cache.property_definitions
        cache_key = (id(candidate), name)
        cached = cache.get(cache_key)
        if cached is not None and cached[0] is candidate:
            child_definition = cached[1]
        else:
            resolved = (
                candidate.sub_section
                if isinstance(candidate, SubSection)
                else candidate
            )
            if hasattr(resolved, 'm_resolved'):
                resolved = resolved.m_resolved()

            all_properties = getattr(resolved, 'all_properties', None)
            child_definition = (
                all_properties.get(name, None) if all_properties is not None else None
            )
            # Retain the candidate alongside the result so object IDs cannot be
            # accidentally reused during a long graph request.
            cache[cache_key] = (candidate, child_definition)

        if child_definition is not None:
            return child_definition

    return None


def _definition_tree_version(definition: Any) -> tuple[int, ...]:
    """Return ``m_mod_count`` along the parent chain, starting at ``definition``.

    ``qualified_name()`` includes ancestor names, but renaming a parent only
    increments the parent's ``m_mod_count``. Keying the short-ref cache on the
    child's count alone would return a stale ``package.Section@id`` string.
    """
    counts: list[int] = []
    seen: set[int] = set()
    current = definition
    while current is not None:
        ident = id(current)
        if ident in seen:
            break
        seen.add(ident)
        counts.append(getattr(current, 'm_mod_count', 0))
        current = getattr(current, 'm_parent', None)
    return tuple(counts)


def _store_short_definition_reference(
    definition: Any, reference: str, version: tuple[int, ...]
) -> None:
    try:
        cache_store = object.__getattribute__(definition, '__dict__')
    except AttributeError:
        return
    cache_store['_cached_short_def_ref'] = reference
    cache_store['_cached_short_def_ref_version'] = version


def _get_short_definition_reference(cache: _ReaderCache, definition: Any) -> str:
    # Level 1: definition-level cache across requests, keyed by ancestor versions
    version = _definition_tree_version(definition)
    cached_ref = getattr(definition, '_cached_short_def_ref', None)
    if (
        cached_ref is not None
        and getattr(definition, '_cached_short_def_ref_version', None) == version
    ):
        return cached_ref

    # Level 2: request-local cache for objects that cannot store instance attrs
    cache_key = id(definition)
    cached_in_cache = cache.short_definition_references.get(cache_key)
    if (
        cached_in_cache is not None
        and cached_in_cache[0] is definition
        and cached_in_cache[1] == version
    ):
        return cached_in_cache[2]

    reference = f'{definition.qualified_name()}@{definition.definition_id}'
    cache.short_definition_references[cache_key] = (definition, version, reference)
    _store_short_definition_reference(definition, reference, version)
    return reference


class GeneralReader:
    # controls the name of configuration
    # it will be extracted from the query dict to generate the configuration object
    __CONFIG__: str = 'm_request'
    # controls the wildcard identifier
    # wildcard is used in mongo data (upload, entry, dataset) to apply a different configuration
    # to records other than the explicitly specified ones
    __WILDCARD__: str = '*'
    # controls the names of fields that are treated as user id, for those fields,
    # implicit resolve is supported and explicit resolve does not require an explicit resolve type
    __USER_ID__: set = {
        'main_author',
        'coauthors',
        'reviewers',
        'viewers',
        'writers',
        'entry_coauthors',
        'user_id',
        'owner',  # from UserGroup
        'members',  # from UserGroup
    }
    # controls the names of fields that are treated as entry id, for those fields,
    # implicit resolve is supported and explicit resolve does not require an explicit resolve type
    __ENTRY_ID__: set = {'entry_id'}
    # controls the names of fields that are treated as upload id, for those fields,
    # implicit resolve is supported and explicit resolve does not require an explicit resolve type
    __UPLOAD_ID__: set = {'upload_id'}
    # controls the names of fields that are treated as dataset id
    __DATASET_ID__: set = {'datasets'}
    __ALL_ID_FIELDS__: frozenset[str] = frozenset(
        __USER_ID__ | __ENTRY_ID__ | __UPLOAD_ID__ | __DATASET_ID__
    )
    __CACHE__: str = '__CACHE__'

    def __init__(
        self,
        required_query: dict | str | RequestConfig,
        *,
        user=None,
        init: bool = True,
        config: RequestConfig | None = None,
        global_root: dict | None = None,
        reader_cache: _ReaderCache | None = None,
    ):
        """
        Supports two modes of initialisation:
        1. Provide `required_query` and `user` only.
            This is the mode that should be used by the external.
            The reader will be initialised with `required_query` and `user`.
            The `required_query` is a dict and will be validated.
            The corresponding configuration dicts will be converted to `RequestConfig` objects.
        2. Provide `required_query`, `user`, `init=False`, `config` and optionally, `global_root`.
            This is the mode used by the internal to switch between different readers.
            The `init` flag must be set to `False` to indicate that the reader should not initialise itself.
            The `required_query` is validated and converted, can be either a `dict` or a `RequestConfig`.
            The `config` is a `RequestConfig` object that holds the parent configuration.
            It is necessary as not all subtrees have a configuration.
            The parent configuration needs to be passed down to the children.
            The `global_root` is used in child readers to allow them to populate data to global root.
            This helps to reduce the nesting level of the final response dict.
        """

        # maybe used to retrieve additional information
        self.user: User = user
        # use to provide a link to the final response dict
        # so that some data can be populated in different places
        self.global_root: dict = global_root
        self._reader_cache = reader_cache or _ReaderCache()

        # for cacheing
        # can only store uploads in the reader
        # due to limitations of upload class (open/close limitations)
        self.upload_pool: dict[str, UploadFiles] = {}

        self.required_query: dict | RequestConfig
        if not init:
            assert config is not None
            self.global_config: RequestConfig = config
            assert not isinstance(required_query, str)
            self.required_query = required_query
        else:
            assert not isinstance(required_query, RequestConfig)
            self.required_query, self.global_config = _parse_required(
                required_query, self.__class__
            )

        self.errors: dict[str, set] = {}

    def _populate_error_list(self, container: dict):
        if not self.errors:
            return

        error_list = container.setdefault(Token.ERROR, [])
        for error_type, error_set in self.errors.items():
            error_list.extend(
                {'error_type': error_type, 'message': error_item}
                for error_item in error_set
            )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        for upload in self.upload_pool.values():
            upload.close()

    def _get_server_context(self, upload_id: str | None) -> ServerContext:
        """Return one authorized server context per upload and graph request."""
        if upload_id not in self._reader_cache.server_contexts:
            upload = (
                get_upload_with_read_access(upload_id, self.user, include_others=True)
                if upload_id
                else None
            )
            self._reader_cache.server_contexts[upload_id] = ServerContext(upload)

        return self._reader_cache.server_contexts[upload_id]

    @property
    def auth_user_id(self) -> str:
        return self.user.user_id if self.user else ''

    @property
    def auth_user_is_admin(self) -> bool:
        return self.user.is_admin if self.user else False

    def _log(
        self,
        message: str,
        *,
        error_type: str = QueryError.GENERAL,
        to_response: bool = True,
    ):
        if to_response:
            error_set = self.errors.setdefault(error_type, set())
            if message in error_set:
                return
            error_set.add(message)
        logger.debug(message)

    def _check_cache(self, path: str | list, config_hash=None) -> bool:
        """
        Check if the given path has been cached.
        Optionally, using the config hash to identify different configurations.
        """
        if isinstance(path, list):
            path = '/'.join(path)

        cache_pool = self.global_root.setdefault(GeneralReader.__CACHE__, {})

        return (
            config_hash is None
            and path in cache_pool
            or config_hash in cache_pool.get(path, set())
        )

    def _cache_hash(self, path: str | list, config_hash=None):
        """
        Check if the given path has been cached.
        Optionally, using the config hash to identify different configurations.
        """
        if isinstance(path, list):
            path = '/'.join(path)

        self.global_root.setdefault(GeneralReader.__CACHE__, {}).setdefault(
            path, set()
        ).add(config_hash)

    async def retrieve_user(self, user_id: str) -> str | dict:
        # `me` is a convenient way to refer to the current user
        if user_id == 'me':
            user_id = self.auth_user_id

        def _retrieve():
            return User.get(user_id=user_id)

        try:
            user: User = await asyncio.to_thread(_retrieve)
        except Exception as e:
            self._log(str(e), to_response=False)
            return user_id

        if user is None:
            self._log(
                f'The value {user_id} is not a valid user id.',
                error_type=QueryError.NOTFOUND,
            )
            return user_id

        return user.m_to_dict(with_out_meta=True, include_derived=True)

    @staticmethod
    async def _overwrite_group(item: MongoUserGroup):
        # todo: this is a quick dirty fix to convert the group object to a dictionary
        # todo: it shall be formalised in the future
        group_dict = item.to_mongo().to_dict()
        group_dict['group_id'] = group_dict.pop('_id', None)

        # to be consistent with the other parts
        # all user ids are resolved to user objects
        group_dict['owner'] = LazyUserWrapper(group_dict['owner'])
        group_dict['members'] = [
            LazyUserWrapper(member) for member in group_dict['members']
        ]
        # add user field next to user_id
        for member in group_dict['members_info']:
            member['user'] = LazyUserWrapper(member['user_id'])

        return group_dict

    async def retrieve_group(self, group_id: str) -> str | dict:
        """
        Retrieve the group for the given group id.
        Returns a plain dictionary if the group is found, otherwise return the given group id.
        """

        def _retrieve():
            return get_mongo_user_group(group_id)

        try:
            group: MongoUserGroup = await asyncio.to_thread(_retrieve)
        except Exception as e:
            self._log(str(e), to_response=False)
            return group_id

        if group is None:
            self._log(
                f'The value {group_id} is not a valid group id.',
                error_type=QueryError.NOTFOUND,
            )
            return group_id

        return await self._overwrite_group(group)

    @staticmethod
    async def _overwrite_upload(item: Upload):
        plain_dict = orjson.loads(
            upload_to_pydantic(item, include_total_count=False).model_dump_json()
        )
        plain_dict.pop('entries', None)
        cached_item = CachedUpload(item)
        plain_dict['n_entries'] = LazyUploadTotalCount(cached_item)
        plain_dict['processing_successful'] = LazyUploadSuccessCount(cached_item)
        plain_dict['processing_failed'] = LazyUploadFailureCount(cached_item)

        if main_author := plain_dict.pop('main_author', None):
            plain_dict['main_author'] = LazyUserWrapper(main_author)
        for name in ('coauthors', 'reviewers', 'viewers', 'writers'):
            if (items := plain_dict.pop(name, None)) is not None:
                plain_dict[name] = [LazyUserWrapper(item) for item in items]

        return plain_dict

    async def retrieve_upload(self, upload_id: str) -> str | dict:
        try:
            upload: Upload = await asyncio.to_thread(
                get_upload_with_read_access, upload_id, self.user, include_others=True
            )
        except HTTPException as e:
            if e.status_code == 404:
                self._log(
                    f'The value {upload_id} is not a valid upload id.',
                    error_type=QueryError.NOTFOUND,
                )
            else:
                self._log(
                    f'No access to upload {upload_id}.', error_type=QueryError.NOACCESS
                )
            return upload_id

        return await self._overwrite_upload(upload)

    @staticmethod
    def _overwrite_entry(item: Entry):
        plain_dict = orjson.loads(entry_to_pydantic(item).model_dump_json())
        plain_dict.pop('entry_metadata', None)
        if mainfile := plain_dict.pop('mainfile', None):
            plain_dict['mainfile_path'] = mainfile
        if datasets := plain_dict.pop('datasets', None):
            plain_dict['dataset_ids'] = datasets

        return plain_dict

    def _retrieve_entry_if_visible(self, entry_id: str) -> Entry | None:
        entry = Entry.objects(entry_id=entry_id).first()  # type: ignore
        if entry is None:
            return None

        # Build a mongo query equivalent to search owner='all' visibility.
        # owner='all' semantics:
        # - visible if published
        # - or visible to viewers (user viewers and viewer groups)
        user_id = self.auth_user_id
        group_ids = MongoUserGroup.get_ids_by_user_id(user_id)

        query = (
            Q(publish_time__ne=None)
            | Q(coauthor_groups__in=group_ids)
            | Q(reviewer_groups__in=group_ids)
        )
        if user_id is not None:
            query |= (
                Q(main_author=user_id) | Q(coauthors=user_id) | Q(reviewers=user_id)
            )

        upload_visible = Upload.objects(Q(upload_id=entry.upload_id) & query).first()  # type: ignore
        if upload_visible is None:
            return None

        return entry

    @traced(span_name='graph.entry.retrieve')
    async def retrieve_entry(self, entry_id: str) -> str | dict:
        if (
            entry := await asyncio.to_thread(self._retrieve_entry_if_visible, entry_id)
        ) is None:
            self._log(
                f'The value {entry_id} is not a valid entry id or not visible to current user.',
                error_type=QueryError.NOACCESS,
            )
            return entry_id

        return self._overwrite_entry(entry)

    async def retrieve_dataset(self, dataset_id: str) -> str | dict:
        def _retrieve():
            return Dataset.m_def.a_mongo.objects(dataset_id=dataset_id).first()

        if (dataset := await asyncio.to_thread(_retrieve)) is None:
            self._log(
                f'The value {dataset_id} is not a valid dataset id.',
                error_type=QueryError.NOTFOUND,
            )
            return dataset_id

        if dataset.user_id != self.auth_user_id:
            self._log(
                f'No access to dataset {dataset_id}.', error_type=QueryError.NOACCESS
            )
            return dataset_id

        return dataset.to_mongo().to_dict()

    @contextmanager
    def load_archive(self, upload_id: str, entry_id: str) -> Iterator[GenericDict]:
        if upload_id not in self.upload_pool:
            # get the archive
            # does the current user have access to the target archive?
            try:
                upload: Upload = get_upload_with_read_access(
                    upload_id, self.user, include_others=True
                )
            except HTTPException:
                raise ArchiveError(
                    f'Current user does not have access to upload {upload_id}.'
                )

            if upload.upload_files is None:
                raise ArchiveError(f'Upload {upload_id} does not exist.')

            self.upload_pool[upload_id] = upload.upload_files
            self._reader_cache.server_contexts[upload_id] = ServerContext(upload)

        try:
            with trace_span(
                'graph.archive.load',
                attributes={'upload_id': upload_id, 'entry_id': entry_id},
            ) as span:
                measurement = (
                    measure_fs_reads()
                    if span is not None and span.is_recording()
                    else nullcontext(None)
                )
                with (
                    measurement as stats,
                    self.upload_pool[upload_id].read_archive(entry_id) as reader,
                ):
                    if isinstance(archive := reader[entry_id], LazyReader):
                        # unwrap to reveal the actual container
                        archive = archive.unwrap()
                    try:
                        # The caller walks the archive while this generator is
                        # suspended, so lazy reads happen within this context.
                        # Record the accumulated stats once the walk finishes.
                        yield archive
                    finally:
                        if stats is not None and span is not None:
                            span.set_attribute('fs.read_bytes', stats.read_bytes)
                            span.set_attribute(
                                'fs.read_time_ms', stats.read_time * 1000
                            )
        except KeyError:
            raise ArchiveError(
                f'Archive {entry_id} does not exist in upload {upload_id}.'
            )

    async def _apply_resolver(self, node: GraphNode, config: RequestConfig):
        if_skip: bool = config.property_name not in GeneralReader.__UPLOAD_ID__
        if_skip &= config.property_name not in GeneralReader.__USER_ID__
        if_skip &= config.property_name not in GeneralReader.__DATASET_ID__
        if_skip &= config.property_name not in GeneralReader.__ENTRY_ID__
        if_skip &= config.resolve_type is None
        if_skip |= config.directive is DirectiveType.plain
        if if_skip:
            return node.archive

        if not isinstance(node.archive, str):
            self._log(f'The value {node.archive} is not a valid id.', to_response=False)
            return node.archive

        if config.resolve_type is ResolveType.user:
            return await self.retrieve_user(node.archive)
        if config.resolve_type is ResolveType.upload:
            return await self.retrieve_upload(node.archive)
        if config.resolve_type is ResolveType.entry:
            return await self.retrieve_entry(node.archive)
        if config.resolve_type is ResolveType.dataset:
            return await self.retrieve_dataset(node.archive)

        return node.archive

    async def _resolve_list(
        self,
        node: GraphNode,
        config: RequestConfig,
        *,
        omit_keys=None,
        wildcard: bool = False,
        omit_indices: set[int] | None = None,
    ):
        """
        Resolve the items of a list.
        Items in `omit_indices` are explicitly requested (`name[i]`) next to the
        list and are resolved by the caller, like `omit_keys` for sections.
        """
        # The list contents are intentionally not traced individually; the
        # enclosing archive walk span provides the useful aggregate timing.
        new_config = (
            config if config.index is None else _copy_request_config(config, index=None)
        )
        _node_cursor(node).container(list)
        for i in _normalise_index(config.index, len(node.archive)):
            if omit_indices is not None and i in omit_indices:
                continue
            archive_item = await goto_child(node.archive, i)
            child = node.descend(str(i), archive=archive_item)
            if new_config.is_plain() and isinstance(node.definition, Quantity):
                _node_cursor(child).set(archive_item)
                continue
            await self._resolve(
                child,
                new_config,
                omit_keys=omit_keys,
                wildcard=wildcard,
            )

    async def _walk(
        self,
        node: GraphNode,
        required: dict | RequestConfig,
        parent_config: RequestConfig,
    ):
        raise NotImplementedError()

    async def _resolve(
        self,
        node: GraphNode,
        config: RequestConfig,
        *,
        omit_keys=None,
        wildcard: bool = False,
    ):
        raise NotImplementedError()

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        raise NotImplementedError()

    @contextmanager
    def _prepare_reading(self):
        """
        Prepare the result container for reading.
        Also populate the error list.
        """
        response: dict = {}

        if self.global_root is None:
            self.global_root = response
            has_global_root: bool = False
        else:
            has_global_root = True

        yield response

        self._populate_error_list(response)

        # if there is a global root, it is a sub-query, no need to clear it
        # if there is no global root, it is a top-level query, clear it
        # mainly to make the reader re-entrant, although one reader should not be used multiple times
        if not has_global_root:
            self.global_root = None

    async def read(self, *args, **kwargs):
        raise NotImplementedError()

    def sync_read(self, *args, **kwargs):
        return asyncio.run(self.read(*args, **kwargs))


# module level TTL cache for caching packages
__lock_pool = Lock()
__package_pool: TTLCache = TTLCache(maxsize=128, ttl=300)


def _fetch_package(key: str) -> Package | None:
    with __lock_pool:
        return __package_pool.get(key, None)


def _cache_package(key: str, package: Package):
    with __lock_pool:
        __package_pool[key] = package


class ArchiveLikeReader(GeneralReader):
    """
    An abstract class for `ArchiveReader` and `DefinitionReader`.
    """

    async def _retrieve_definition(
        self,
        m_def: str | None,
        m_def_id: str | None = None,
        node: GraphNode | None = None,
    ):
        """
        Retrieve a definition from an archive.
        The definition is identified by `m_def` and/or `m_def_id`.
        It could be a local definition that is defined in the `definitions` section.
        In this case, we initialise a new `Package` object and resolve the path.
        It could also be a reference to another entry in another upload.
        In this case, we need to load the archive.
        If m_def_id is given, try to load it from mongodb.

        todo: more flexible definition retrieval, accounting for definition id, mismatches, etc.
        """

        cache_key = (
            node.upload_id if node else None,
            node.entry_id if node else None,
            m_def,
            m_def_id,
        )
        if cache_key in self._reader_cache.definitions:
            return self._reader_cache.definitions[cache_key]

        def cache(definition):
            self._reader_cache.definitions[cache_key] = definition
            return definition

        def new_context(_id):
            return self._get_server_context(_id)

        async def __resolve_definition_in_archive(
            _root,
            _path_stack: list,
            _upload_id: str | None = None,
            _entry_id: str | None = None,
        ):
            """
            This path is no longer used when m_def_id is written to the archive.
            For which the custom definitions will be directly loaded from the database.
            """
            cache_key: str = f'{_upload_id}:{_entry_id}'

            custom_package: Package | None = _fetch_package(cache_key)
            if custom_package is None:
                custom_package = Package.m_from_dict(
                    await async_to_json(await goto_child(_root, 'definitions')),
                    m_context=new_context(_upload_id),
                )
                # package loaded in this way does not have an attached archive
                # we manually set the upload_id and entry_id so that
                # correct references can be generated in the corresponding method
                custom_package.entry_id = _entry_id
                custom_package.upload_id = _upload_id
                custom_package.init_metainfo()
                if (
                    upload := Upload.objects(upload_id=_upload_id).first()  # type: ignore
                ) is not None and upload.published:
                    # only cache published data that will not be changed
                    _cache_package(cache_key, custom_package)

            return custom_package.m_resolve_path(_path_stack)

        if m_def_id is not None:
            if new_def := new_context(node.upload_id if node else None).fetch_section(
                m_def, m_def_id
            ):
                return cache(new_def)

        if m_def is not None:
            if m_def.startswith(('#/', '/')):
                # appears to be a local definition
                return cache(
                    await __resolve_definition_in_archive(
                        node.archive_root,
                        [
                            v
                            for v in m_def.split('/')
                            if v not in ('', '#', 'definitions')
                        ],
                        await goto_child(node.archive_root, ['metadata', 'upload_id']),
                        await goto_child(node.archive_root, ['metadata', 'entry_id']),
                    )
                )
            # todo: !!!need to unify different formats!!!
            # check if m_def matches the pattern 'entry_id:example_id.example_section.example_quantity'
            if m_def.startswith('entry_id:'):
                tokens = m_def[9:].split('.')
                entry_id = tokens.pop(0)
                entry_record = Entry.objects(entry_id=entry_id).first()  # type: ignore
                upload_id = entry_record.upload_id
                if (
                    cached_package := _fetch_package(f'{upload_id}:{entry_id}')
                ) is not None:  # early fetch to avoid loading archive from disk
                    return cache(cached_package.m_resolve_path(tokens))
                with self.load_archive(upload_id, entry_id) as archive:
                    return cache(
                        await __resolve_definition_in_archive(
                            archive, tokens, upload_id, entry_id
                        )
                    )

        # use the conventional approach
        proxy = MSectionReference().normalize(
            m_def, context=new_context(node.upload_id)
        )
        return cache(proxy.section_cls.m_def)


class MongoReader(GeneralReader):
    @functools.cached_property
    def entries(self):
        return Entry.objects(upload_id__in=[v.upload_id for v in self.uploads])  # type: ignore

    @functools.cached_property
    def uploads(self):
        group_ids = MongoUserGroup.get_ids_by_user_id(self.auth_user_id)
        return Upload.objects(  # type: ignore
            Q(main_author=self.auth_user_id)
            | Q(reviewers=self.auth_user_id)
            | Q(coauthors=self.auth_user_id)
            | Q(reviewer_groups__in=group_ids)
            | Q(coauthor_groups__in=group_ids)
        )

    @functools.cached_property
    def datasets(self):
        return Dataset.m_def.a_mongo.objects(user_id=self.auth_user_id)

    @traced(span_name='graph.mongo.query_es')
    async def _query_es(self, config: RequestConfig):
        search_params: dict = {
            'owner': 'user' if self.auth_user_id else 'public',
            'user_id': self.auth_user_id or None,
            'query': {},
            # 'required': MetadataRequired(include=['entry_id'])
        }
        search_query: dict = {}

        if config.query:
            assert isinstance(config.query, Metadata)

            search_query = config.query.model_dump(exclude_none=True)  # type: ignore

            if config.query.owner:
                search_params['owner'] = config.query.owner
            if config.query.query:
                search_params['query'] = config.query.query
            if config.query.pagination:
                search_params['pagination'] = config.query.pagination
            if config.query.required:
                search_params['required'] = config.query.required
            if config.query.aggregations:
                search_params['aggregations'] = config.query.aggregations

        if config.pagination and (not config.query or not config.query.pagination):  # type: ignore
            search_params['pagination'] = config.pagination

        search_response = await asyncio.to_thread(perform_search, **search_params)
        # overwrite the pagination to the new one from the search response
        config.pagination = search_response.pagination

        def _overwrite(item):
            if mainfile := item.pop('mainfile', None):
                item['mainfile_path'] = mainfile
            return item

        return search_query, {
            v['entry_id']: _overwrite(v) for v in search_response.data
        }

    @traced(span_name='graph.mongo.query_entries')
    async def _query_entries(self, config: RequestConfig):
        if not config.query:
            return None, self.entries

        assert isinstance(config.query, EntryQuery)

        mongo_query = Q()
        if config.query.parser_name:
            parser_name = Q()
            for item in config.query.parser_name:
                if item:
                    parser_name |= Q(parser_name__regex=item)
            mongo_query &= parser_name

        if config.query.mainfile:
            mainfile = Q()
            for item in config.query.mainfile:
                if item:
                    mainfile |= Q(mainfile__regex=item)
            mongo_query &= mainfile

        if config.query.references:
            references = Q()
            for item in config.query.references:
                if item:
                    references |= Q(references__regex=item)
            mongo_query &= references

        return config.query.model_dump(exclude_unset=True), self.entries.filter(
            mongo_query
        )

    @traced(span_name='graph.mongo.query_uploads')
    async def _query_uploads(self, config: RequestConfig):
        if not config.query:
            return None, self.uploads

        assert isinstance(config.query, UploadProcDataQuery)

        try:
            mongo_query = create_mongo_query(
                config.query,
                auth_user_id=self.auth_user_id,
            )
        except MongoQueryError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return config.query.model_dump(exclude_unset=True), self.uploads.filter(
            mongo_query
        )

    @traced(span_name='graph.mongo.query_datasets')
    async def _query_datasets(self, config: RequestConfig):
        if not config.query:
            return None, self.datasets

        assert isinstance(config.query, DatasetQuery)

        mongo_query = Q()
        if config.query.dataset_id:
            mongo_query &= Q(dataset_id=config.query.dataset_id)
        if config.query.dataset_name:
            mongo_query &= Q(dataset_name=config.query.dataset_name)
        if config.query.user_id:
            mongo_query &= Q(user_id__in=config.query.user_id)
        if config.query.dataset_type:
            mongo_query &= Q(dataset_type=config.query.dataset_type)
        if config.query.doi:
            mongo_query &= Q(doi=config.query.doi)
        if config.query.prefix:
            mongo_query &= Q(
                dataset_name=re.compile(rf'^{config.query.prefix}.*$', re.IGNORECASE)
            )

        return config.query.model_dump(exclude_unset=True), self.datasets.filter(
            mongo_query
        )

    @staticmethod
    async def _query_groups(config: RequestConfig):
        query: UserGroupQuery = (
            config.query
            if isinstance(config.query, UserGroupQuery)
            else UserGroupQuery()
        )
        return query.model_dump(exclude_unset=True), MongoUserGroup.get_by_query(query)

    @traced(span_name='graph.mongo.normalise')
    async def _normalise(
        self, mongo_result, config: RequestConfig, transformer: Callable
    ) -> tuple[dict, PaginationResponse | None]:
        """
        Apply pagination and transform to the mongo search results.
        """
        pagination_response: PaginationResponse | None = None
        # PaginationResponse comes from elastic search that has been processed
        # later we skip applying pagination
        if isinstance(config.pagination, PaginationResponse):
            pagination_response = config.pagination
        elif isinstance(config.pagination, Pagination):
            pagination_response = PaginationResponse(
                total=mongo_result.count() if mongo_result else 0,
                **config.pagination.model_dump(),
            )
        elif callable(getattr(mongo_result, 'count', None)):
            # looks like a QuerySet
            pagination_response = PaginationResponse(total=mongo_result.count())

        if transformer is None:
            return mongo_result, pagination_response

        if mongo_result is None:
            return {}, pagination_response

        def _pick_id(_item):
            if transformer == upload_to_pydantic:
                return _item.upload_id
            if transformer == dataset_to_pydantic:
                return _item.dataset_id
            if transformer == entry_to_pydantic:
                return _item.entry_id
            if transformer == group_to_pydantic:
                return _item.group_id

            raise ValueError(f'Should not reach here.')

        def _populate_next_page_after_value():
            if mongo_result:
                pagination_response.next_page_after_value = _pick_id(
                    mongo_result[len(mongo_result) - 1]
                )

        if config.pagination is None:
            # still apply a default pagination to avoid unnecessarily large results
            mongo_result = mongo_result[: pagination_response.page_size]
            _populate_next_page_after_value()
        elif not isinstance(config.pagination, PaginationResponse):
            # apply pagination when config.pagination is present
            # if it is a PaginationResponse, it means the pagination has been applied in the search
            assert isinstance(config.pagination, Pagination)

            mongo_result = config.pagination.order_result(mongo_result)

            mongo_result = config.pagination.paginate_result(mongo_result, _pick_id)
            _populate_next_page_after_value()

        if transformer == upload_to_pydantic:
            mongo_dict = {
                v['upload_id']: v
                for v in [await self._overwrite_upload(item) for item in mongo_result]
            }
        elif transformer == dataset_to_pydantic:
            mongo_dict = {
                v['dataset_id']: v
                for v in [orjson.loads(transformer(item)) for item in mongo_result]
            }
        elif transformer == entry_to_pydantic:
            mongo_dict = {
                v['entry_id']: v
                for v in [self._overwrite_entry(item) for item in mongo_result]
            }
        elif transformer == group_to_pydantic:
            mongo_dict = {
                v['group_id']: v
                for v in [await self._overwrite_group(item) for item in mongo_result]
            }
        else:
            raise ValueError(f'Should not reach here.')

        return mongo_dict, pagination_response

    @traced(span_name='graph.mongo.read')
    async def read(self):
        """
        All read() methods, including the ones in the subclasses, should return a dict as the response.
        It performs the following tasks:
            1. Define the default maximum scope the current user can reach by defining any of `self.uploads`,
                `self.entries` and `self.datasets`.
            2. Define the current `ArchiveNode` object.
            3. Call `_walk()` to walk through the request tree.

        In this method, as a general reader, it populates all three: `self.uploads`, `self.entries` and `self.datasets`.
        In other methods, it may only populate one or two of them, which represents the available edges to the current
        node.
        For example, in a `UploadReader`, it only populates `self.entries`, which implies that from an upload, one can
        only navigate to its entries, using `Token.ENTRIES` token.
        """
        with self._prepare_reading() as response:
            await self._walk(
                GraphNode(
                    upload_id='__NOT_NEEDED__',
                    entry_id='__NOT_NEEDED__',
                    current_path=[],
                    result_root=response,
                    ref_result_root=self.global_root,
                    archive=None,
                    archive_root=None,
                    definition=None,
                    visited_path=set(),
                    current_depth=0,
                    reader=self,
                ),
                self.required_query,
                self.global_config,
            )

            return response

    async def _walk(
        self,
        node: GraphNode,
        required: dict | RequestConfig,
        parent_config: RequestConfig,
    ):
        if isinstance(required, RequestConfig):
            return await self._resolve(node, required)

        has_config: bool = GeneralReader.__CONFIG__ in required
        has_wildcard: bool = GeneralReader.__WILDCARD__ in required

        current_config: RequestConfig = required.get(
            GeneralReader.__CONFIG__, parent_config
        )

        if has_wildcard:
            wildcard_config = required[GeneralReader.__WILDCARD__]
            if isinstance(wildcard_config, RequestConfig):
                # use the wildcard config to filter the current scope
                await self._resolve(
                    node, wildcard_config, omit_keys=required.keys(), wildcard=True
                )
            elif isinstance(node.archive, dict):
                # nested fuzzy query, add all available keys to the required
                # !!!
                # DO NOT directly use .update() as it will modify the original dict
                # !!!
                extra_required = {
                    k: wildcard_config for k in node.archive.keys() if k not in required
                }
                required = required | extra_required
        elif has_config:
            # use the inherited/assigned config to filter the current scope
            await self._resolve(node, current_config, omit_keys=required.keys())

        offload_pack: dict = {
            'user': self.user,
            'init': False,
            'config': current_config,
            'global_root': self.global_root,
            'reader_cache': self._reader_cache,
        }

        for key, value in required.items():
            if key in (GeneralReader.__CONFIG__, GeneralReader.__WILDCARD__):
                continue

            async def offload_read(
                reader_cls: type[GeneralReader], *args, read_list=False
            ):
                try:
                    with reader_cls(value, **offload_pack) as reader:
                        await _populate_result(
                            node.result_root,
                            node.current_path + [key],
                            [await reader.read(item) for item in args[0]]
                            if read_list
                            else await reader.read(*args),
                        )
                except ConfigError:
                    raise
                except Exception as exc:
                    self._log(str(exc))

            if key == Token.RAW and self.__class__ is UploadReader:
                # hitting the bottom of the current scope
                await offload_read(FileSystemReader, node.upload_id)
                continue

            if key == Token.METADATA and self.__class__ is EntryReader:
                preloaded_metadata = node.archive.get(Token.METADATA)
                if isinstance(preloaded_metadata, dict):
                    # Layout-aware reads already loaded archive metadata. Walk that
                    # snapshot from an isolated root so the literal ``metadata`` path
                    # does not trigger searchable-edge offloading for every child.
                    metadata_result: dict[str, Any] = {}
                    await self._walk(
                        node.replace(
                            archive=preloaded_metadata,
                            current_path=[],
                            result_root=metadata_result,
                        ),
                        value,
                        current_config,
                    )
                    await _populate_result(
                        node.result_root,
                        node.current_path + [key],
                        metadata_result,
                    )
                else:
                    # Regular entry reads continue to resolve metadata through ES.
                    await offload_read(ElasticSearchReader, node.entry_id)
                continue

            if key in (Token.UPLOAD, Token.UPLOADS) and self.__class__ is EntryReader:
                # hitting the bottom of the current scope
                await offload_read(UploadReader, node.upload_id)
                continue

            if key == Token.MAINFILE and self.__class__ is EntryReader:
                # hitting the bottom of the current scope
                await offload_read(
                    FileSystemReader, node.upload_id, node.archive['mainfile_path']
                )
                continue

            if key == Token.ARCHIVE and self.__class__ is EntryReader:
                # hitting the bottom of the current scope
                await offload_read(ArchiveReader, node.upload_id, node.entry_id)
                continue

            if (
                key in (Token.ENTRY, Token.ENTRIES)
                and self.__class__ is ElasticSearchReader
            ):
                # hitting the bottom of the current scope
                await offload_read(EntryReader, node.archive['entry_id'])
                continue

            if key == Token.METAINFO and self.__class__ is MongoReader:
                # hitting the bottom of the current scope
                await offload_read(MetainfoBrowser)
                continue

            if isinstance(node.archive, dict) and isinstance(value, dict):
                # treat it as a normal key
                # and handle in applying resolver if it is a leaf node
                if key in GeneralReader.__ENTRY_ID__:
                    # offload to the upload reader if it is a nested query
                    if isinstance(entry_id := node.archive.get(key, None), str):
                        await offload_read(EntryReader, entry_id)
                        continue

                if key in GeneralReader.__UPLOAD_ID__:
                    # offload to the entry reader if it is a nested query
                    if isinstance(upload_id := node.archive.get(key, None), str):
                        await offload_read(UploadReader, upload_id)
                        continue

                if key in GeneralReader.__USER_ID__:
                    # offload to the user reader if it is a nested query
                    if user_id := node.archive.get(key, None):
                        await offload_read(
                            UserReader, user_id, read_list=isinstance(user_id, list)
                        )
                        continue

            if isinstance(value, RequestConfig):
                child_config = value
            elif isinstance(value, dict) and GeneralReader.__CONFIG__ in value:
                child_config = value[GeneralReader.__CONFIG__]
            else:
                child_config = current_config.new({'query': None, 'pagination': None})

            async def offload_walk(query_set, transformer):
                response_path: list = node.current_path + [key, Token.RESPONSE]

                if isinstance(value, dict) and GeneralReader.__CONFIG__ in value:
                    await _populate_result(
                        node.result_root,
                        response_path,
                        _to_response_config(
                            child_config, exclude=['query', 'pagination']
                        ),
                    )

                query, filtered = query_set
                if query is not None:
                    await _populate_result(
                        node.result_root, response_path + ['query'], query
                    )

                if filtered is None:
                    return

                if isinstance(value, dict) and GeneralReader.__WILDCARD__ not in value:
                    result, pagination = {}, None
                else:
                    result, pagination = await self._normalise(
                        filtered, child_config, transformer
                    )
                if pagination is not None:
                    pagination_dict = pagination.model_dump()
                    if pagination_dict.get('order_by', None) == 'mainfile':
                        pagination_dict['order_by'] = 'mainfile_path'
                    await _populate_result(
                        node.result_root,
                        response_path + ['pagination'],
                        pagination_dict,
                    )
                await self._walk(
                    node.replace(
                        archive={k: k for k in result}
                        if isinstance(value, RequestConfig)
                        else result,
                        current_path=node.current_path + [key],
                    ),
                    value,
                    current_config,
                )

            if (
                key
                in (
                    Token.ENTRY,
                    Token.ENTRIES,
                    Token.UPLOAD,
                    Token.UPLOADS,
                    Token.USER,
                    Token.USERS,
                    Token.DATASET,
                    Token.DATASETS,
                    Token.GROUP,
                    Token.GROUPS,
                )
                and isinstance(value, dict)
                and GeneralReader.__CONFIG__ not in value
                and GeneralReader.__WILDCARD__ not in value
            ):
                await self._walk(
                    node.replace(archive={}, current_path=node.current_path + [key]),
                    value,
                    current_config,
                )
                continue

            if key == Token.SEARCH:
                await offload_walk(await self._query_es(child_config), None)
                continue
            if key == Token.ENTRY or key == Token.ENTRIES:
                await offload_walk(
                    await self._query_entries(child_config), entry_to_pydantic
                )
                continue
            if key == Token.UPLOAD or key == Token.UPLOADS:
                await offload_walk(
                    await self._query_uploads(child_config), upload_to_pydantic
                )
                continue
            if key == Token.DATASET or key == Token.DATASETS:
                await offload_walk(
                    await self._query_datasets(child_config), dataset_to_pydantic
                )
                continue
            if key == Token.GROUP or key == Token.GROUPS:
                await offload_walk(
                    await self._query_groups(child_config), group_to_pydantic
                )
                continue
            if key == Token.USER or key == Token.USERS:
                await offload_walk(
                    (
                        None,
                        {
                            k: v
                            for k, v in value.items()
                            if k
                            not in (
                                GeneralReader.__CONFIG__,
                                GeneralReader.__WILDCARD__,
                            )
                        },
                    ),
                    None,
                )
                continue

            if len(node.current_path) > 0 and node.current_path[-1] in __M_SEARCHABLE__:
                await offload_read(__M_SEARCHABLE__[node.current_path[-1]], key)
                continue

            # key may contain index, cached
            name, index = _parse_key(key)

            if name not in node.archive:
                continue

            child_archive = node.archive[name]
            child_path: list = node.current_path + [name]

            if isinstance(value, RequestConfig):
                await self._resolve(
                    node.replace(archive=child_archive, current_path=child_path), value
                )
            elif isinstance(value, dict):
                # should never reach here in most cases
                # most mongo data is a 1-level tree
                # second level implies it's delegated to another reader
                async def __walk(__archive, __path):
                    await self._walk(
                        node.replace(archive=__archive, current_path=__path),
                        value,
                        current_config,
                    )

                if isinstance(child_archive, list):
                    for i in _normalise_index(index, len(child_archive)):
                        await __walk(child_archive[i], child_path + [str(i)])
                else:
                    await __walk(child_archive, child_path)
            elif isinstance(value, list):
                # optionally support alternative syntax
                pass
            else:
                # should never reach here
                raise ConfigError(f'Invalid required config: {value}.')

    async def _resolve(
        self,
        node: GraphNode,
        config: RequestConfig,
        *,
        omit_keys=None,
        wildcard: bool = False,
    ):
        if isinstance(node.archive, list):
            return await self._resolve_list(node, config)

        if not isinstance(node.archive, dict):
            # primitive type data is always included
            # this is not affected by size limit nor by depth limit
            return await _populate_result(
                node.result_root,
                node.current_path,
                await self._apply_resolver(node, config),
            )

        if (
            config.directive is DirectiveType.resolved
            and len(node.current_path) > 1
            and node.current_path[-2] in __M_SEARCHABLE__
        ):
            offload_reader = __M_SEARCHABLE__[node.current_path[-2]]
            try:
                with offload_reader(
                    config,
                    user=self.user,
                    init=False,
                    config=config,
                    global_root=self.global_root,
                ) as reader:
                    await _populate_result(
                        node.result_root,
                        node.current_path,
                        await reader.read(node.current_path[-1]),
                    )
            except Exception as e:
                self._log(str(e))
            return

        if wildcard:
            assert omit_keys is not None

        for key in node.archive.keys():
            new_config: dict = {'property_name': key, 'index': None}
            if wildcard:
                if any(k.startswith(key) for k in omit_keys):
                    continue
            else:
                if (
                    not config.if_include(key)
                    or omit_keys is not None
                    and any(k.startswith(key) for k in omit_keys)
                ):
                    continue
                new_config['include'] = ['*']
                new_config['exclude'] = None

            # need to retrain the include/exclude pattern for wildcard
            await self._resolve(
                node.replace(
                    archive=node.archive[key],
                    current_path=node.current_path + [key],
                    current_depth=node.current_depth + 1,
                ),
                config.new(new_config, retain_pattern=wildcard),
            )

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        if config.include_definition is not DefinitionType.none:
            raise ConfigError(
                f'Including definitions is not supported in {cls.__name__} @ {key}.'
            )
        if config.depth:
            raise ConfigError(
                f'Limiting result depth is not supported in {cls.__name__} @ {key}.'
            )
        if config.resolve_depth:
            raise ConfigError(
                f'Limiting resolve depth is not supported in {cls.__name__} @ {key}.'
            )
        if config.max_list_size or config.max_dict_size:
            raise ConfigError(
                f'Limiting container size is not supported in {cls.__name__} @ {key}.'
            )

        if (
            (config.pagination or config.query)
            and key not in __M_SEARCHABLE__
            and key != 'top level'
        ):
            raise ConfigError(
                f'Pagination and query are only supported for searchable keys '
                f'{__M_SEARCHABLE__.keys()} in {cls.__name__}. Revise config @ {key}.'
            )

        return config


class UploadReader(MongoReader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_upload_id = None

    @functools.cached_property
    def entries(self):
        return Entry.objects(upload_id=self.target_upload_id)  # type: ignore

    # noinspection PyMethodOverriding
    async def read(self, upload_id: str) -> dict:  # type: ignore
        with self._prepare_reading() as response:
            # if it is a string, no access
            if isinstance(target_upload := await self.retrieve_upload(upload_id), dict):
                self.target_upload_id = upload_id

                await self._walk(
                    GraphNode(
                        upload_id=upload_id,
                        entry_id='__NOT_NEEDED__',
                        current_path=[],
                        result_root=response,
                        ref_result_root=self.global_root,
                        archive=target_upload,
                        archive_root=None,
                        definition=None,
                        visited_path=set(),
                        current_depth=0,
                        reader=self,
                    ),
                    self.required_query,
                    self.global_config,
                )

            return response

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        try:
            if config.query is not None:
                config.query = UploadProcDataQuery.model_validate(config.query)
            if config.pagination is not None:
                config.pagination = UploadProcDataPagination.model_validate(
                    config.pagination
                )
        except Exception as e:
            raise ConfigError(str(e))

        return super().validate_config(key, config)


class DatasetReader(MongoReader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_dataset_id = None

    @functools.cached_property
    def entries(self):
        return Entry.objects(datasets=self.target_dataset_id)  # type: ignore

    @functools.cached_property
    def uploads(self):
        return Upload.objects(  # type: ignore
            upload_id__in=list({v['upload_id'] for v in self.entries})
        )

    # noinspection PyMethodOverriding
    async def read(self, dataset_id: str) -> dict:  # type: ignore
        with self._prepare_reading() as response:
            # if it is a string, no access
            if isinstance(
                target_dataset := await self.retrieve_dataset(dataset_id), dict
            ):
                self.target_dataset_id = dataset_id

                await self._walk(
                    GraphNode(
                        upload_id='__NOT_NEEDED__',
                        entry_id='__NOT_NEEDED__',
                        current_path=[],
                        result_root=response,
                        ref_result_root=self.global_root,
                        archive=target_dataset,
                        archive_root=None,
                        definition=None,
                        visited_path=set(),
                        current_depth=0,
                        reader=self,
                    ),
                    self.required_query,
                    self.global_config,
                )

            return response

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        try:
            if config.query is not None:
                config.query = DatasetQuery.model_validate(config.query)
            if config.pagination is not None:
                config.pagination = DatasetPagination.model_validate(config.pagination)
        except Exception as e:
            raise ConfigError(str(e))

        return super().validate_config(key, config)


class EntryReader(MongoReader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_entry_id = None

    @functools.cached_property
    def datasets(self):
        return Dataset.m_def.a_mongo.objects(entries=self.target_entry_id)

    @traced(span_name='graph.entry.build_layout_read_plan')
    async def _build_layout_read_plan(
        self, target_entry: dict[str, Any], archive: GenericDict
    ) -> _EntryLayoutReadPlan:
        """
        Resolve layout state before the normal graph walk.

        Search metadata comes from Mongo where possible, while archive metadata
        supplies the quantities/sections/results needed by layout matching and
        request derivation.
        """
        intent = get_layout_query_intent(self.required_query)
        if not (intent.requires_search_metadata or intent.requires_layout_resolution):
            return _EntryLayoutReadPlan()

        archive_metadata = await goto_child(archive, 'metadata')
        metadata_dict = await async_to_json(archive_metadata)
        layout_context = build_layout_context(target_entry, metadata_dict)
        plan = create_layout_plan(
            layout_context,
            requested_layout_id=intent.requested_layout_id,
        )
        archive_request = _normalize_layout_archive_request(plan.archive_request)

        entry_updates = {
            'metadata': metadata_dict,
            'default_layout_id': plan.default_layout_id,
            'matching_layouts': plan.matching_layouts,
            'resolved_layout_id': plan.resolved_layout_id,
        }
        response_updates = {
            'resolved_layout_id': plan.resolved_layout_id,
            'resolved_archive_request': archive_request.raw,
        }

        return _EntryLayoutReadPlan(
            entry_updates=entry_updates,
            response_updates=response_updates,
            archive_request=archive_request,
        )

    @traced(span_name='graph.entry.read_layout_archive')
    async def _read_layout_archive(
        self, archive: GenericDict, request: _LayoutArchiveRequest
    ) -> dict[str, Any]:
        with ArchiveReader(
            request.normalized,
            user=self.user,
            init=False,
            config=self.global_config,
            global_root=self.global_root,
            reader_cache=self._reader_cache,
        ) as reader:
            return await reader.read(archive)

    # noinspection PyMethodOverriding
    @traced(span_name='graph.entry.read')
    async def read(self, entry_id: str) -> dict:  # type: ignore
        """Read one entry and resolve a server-selected layout when requested."""
        with self._prepare_reading() as response:
            # Entry process data establishes access and supplies the upload needed to
            # open the archive. A string result denotes a missing or inaccessible entry.
            if isinstance(target_entry := await self.retrieve_entry(entry_id), dict):
                self.target_entry_id = entry_id
                required_query = self.required_query
                intent = get_layout_query_intent(required_query)

                if intent.auto_from_layout:
                    layout_plan = _EntryLayoutReadPlan()
                    try:
                        # Keep one archive reader open while metadata selects and compiles
                        # the layout and ArchiveReader materializes its derived payload.
                        with self.load_archive(
                            target_entry['upload_id'], entry_id
                        ) as archive:
                            layout_plan = await self._build_layout_read_plan(
                                target_entry, archive
                            )
                            assert layout_plan.archive_request is not None
                            response[Token.ARCHIVE] = await self._read_layout_archive(
                                archive, layout_plan.archive_request
                            )
                    except ValueError as exc:
                        raise ConfigError(str(exc)) from exc

                    # Expose compiled layout state and preloaded metadata to the normal
                    # graph walk. The archive was already written above, so remove that
                    # edge to prevent a second archive open.
                    target_entry.update(layout_plan.entry_updates)
                    # These fields describe how the server satisfied auto_from_layout and
                    # are returned even though they are not part of the archive itself.
                    response.update(layout_plan.response_updates)
                    if isinstance(required_query, dict):
                        required_query = {
                            key: value
                            for key, value in required_query.items()
                            if key != Token.ARCHIVE
                        }

                # Resolve all remaining requested entry fields with the standard graph
                # machinery, preserving the legacy path when layouts are client-driven.
                await self._walk(
                    GraphNode(
                        upload_id=target_entry['upload_id'],
                        entry_id=entry_id,
                        current_path=[],
                        result_root=response,
                        ref_result_root=self.global_root,
                        archive=target_entry,
                        archive_root=None,
                        definition=None,
                        visited_path=set(),
                        current_depth=0,
                        reader=self,
                    ),
                    required_query,
                    self.global_config,
                )

            return response

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        try:
            if config.query is not None:
                config.query = EntryQuery.model_validate(config.query)
            if config.pagination is not None:
                config.pagination = EntryProcDataPagination.model_validate(
                    config.pagination
                )
        except Exception as e:
            raise ConfigError(str(e))

        return super().validate_config(key, config)


class ElasticSearchReader(EntryReader):
    async def retrieve_entry(self, entry_id: str) -> str | dict:
        search_response = await asyncio.to_thread(
            perform_search,
            owner='all',
            query={'entry_id': entry_id},
            user_id=self.auth_user_id or None,
        )

        if search_response.pagination.total == 0:
            self._log(
                f'The value {entry_id} is not a valid entry id or not visible to current user.',
                error_type=QueryError.NOACCESS,
            )
            return entry_id

        plain_dict = search_response.data[0]
        if mainfile := plain_dict.pop('mainfile', None):
            plain_dict['mainfile_path'] = mainfile

        return plain_dict

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        try:
            if config.query is not None:
                config.query = Metadata.model_validate(config.query)
            if config.pagination is not None:
                config.pagination = MetadataPagination.model_validate(config.pagination)
        except Exception as e:
            raise ConfigError(str(e))

        return MongoReader.validate_config(key, config)


class UserReader(MongoReader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_user_id = None

    @functools.cached_property
    def entries(self):
        return Entry.objects(upload_id__in=[v.upload_id for v in self.uploads])  # type: ignore

    @functools.cached_property
    def uploads(self):
        target_group_ids = MongoUserGroup.get_ids_by_user_id(self.target_user_id)
        mongo_query = (
            Q(main_author=self.target_user_id)
            | Q(reviewers=self.target_user_id)
            | Q(coauthors=self.target_user_id)
            | Q(reviewer_groups__in=target_group_ids)
            | Q(coauthor_groups__in=target_group_ids)
        )
        # self.user must have access to the upload
        if self.target_user_id != self.auth_user_id and not self.auth_user_is_admin:
            auth_group_ids = MongoUserGroup.get_ids_by_user_id(self.auth_user_id)
            mongo_query &= (
                Q(main_author=self.auth_user_id)
                | Q(reviewers=self.auth_user_id)
                | Q(coauthors=self.auth_user_id)
                | Q(reviewer_groups__in=auth_group_ids)
                | Q(coauthor_groups__in=auth_group_ids)
            )

        return Upload.objects(mongo_query)  # type: ignore

    @functools.cached_property
    def datasets(self):
        return Dataset.m_def.a_mongo.objects(
            dataset_id__in={v for e in self.entries if e.datasets for v in e.datasets}
        )

    # noinspection PyMethodOverriding
    async def read(self, user_id_or_dict: str | dict | LazyUserWrapper):  # type: ignore
        with self._prepare_reading() as response:
            if isinstance(user_id_or_dict, LazyUserWrapper):
                user_id_or_dict = user_id_or_dict.to_json()

            target_user: str | dict
            if isinstance(user_id_or_dict, dict):
                target_user = user_id_or_dict
                user_id: str = target_user['user_id']
            elif isinstance(user_id_or_dict, str):
                if user_id_or_dict == 'me':
                    user_id = self.auth_user_id
                else:
                    user_id = user_id_or_dict
                # if user_id == '' there is no auth user thus set to empty dict
                target_user = await self.retrieve_user(user_id) if user_id else {}
            else:
                # should not reach here
                raise NotImplementedError

            if isinstance(target_user, str):
                # does not exist
                self._log(
                    f'User ID {user_id} does not exist.', error_type=QueryError.NOTFOUND
                )
            else:
                self.target_user_id = user_id

                await self._walk(
                    GraphNode(
                        upload_id='__NOT_NEEDED__',
                        entry_id='__NOT_NEEDED__',
                        current_path=[],
                        result_root=response,
                        ref_result_root=self.global_root,
                        archive=target_user,
                        archive_root=None,
                        definition=None,
                        visited_path=set(),
                        current_depth=0,
                        reader=self,
                    ),
                    self.required_query,
                    self.global_config,
                )

            return response

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        if config.query is not None:
            raise ConfigError('User reader does not support query.')
        if config.pagination is not None:
            raise ConfigError('User reader does not support pagination.')

        return super().validate_config(key, config)


class UserGroupReader(MongoReader):
    # noinspection PyMethodOverriding
    async def read(self, group_id: str):  # type: ignore
        with self._prepare_reading() as response:
            if isinstance(target_group := await self.retrieve_group(group_id), str):
                # does not exist
                self._log(
                    f'Group ID {group_id} does not exist.',
                    error_type=QueryError.NOTFOUND,
                )
            else:
                await self._walk(
                    GraphNode(
                        upload_id='__NOT_NEEDED__',
                        entry_id='__NOT_NEEDED__',
                        current_path=[],
                        result_root=response,
                        ref_result_root=self.global_root,
                        archive=target_group,
                        archive_root=None,
                        definition=None,
                        visited_path=set(),
                        current_depth=0,
                        reader=self,
                    ),
                    self.required_query,
                    self.global_config,
                )

            return response

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        try:
            if config.query is not None:
                config.query = UserGroupQuery.model_validate(config.query)
            if config.pagination is not None:
                config.pagination = UserGroupPagination.model_validate(
                    config.pagination
                )
        except Exception as e:
            raise ConfigError(str(e))

        return super().validate_config(key, config)


class FileSystemReader(GeneralReader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._root_path: list = []

    async def read(self, upload_id: str, path: str | None = None) -> dict:
        self._root_path = [v for v in path.split('/') if v] if path else []

        with self._prepare_reading() as response:
            try:
                upload: Upload = get_upload_with_read_access(
                    upload_id, self.user, include_others=True
                )
            except HTTPException:
                self._log(
                    f'Current user does not have access to upload {upload_id}.',
                    error_type=QueryError.NOACCESS,
                )
            else:
                if upload_id not in self.upload_pool:
                    self.upload_pool[upload_id] = upload.upload_files
                    self._reader_cache.server_contexts[upload_id] = ServerContext(
                        upload
                    )
                upload_files = self.upload_pool[upload_id]
                await self._walk(
                    GraphNode(
                        upload_id=upload_id,
                        entry_id='__NOT_NEEDED__',
                        current_path=[],
                        result_root=response,
                        ref_result_root=self.global_root,
                        archive=upload_files,
                        archive_root=None,
                        definition=None,
                        visited_path=set(),
                        current_depth=0,
                        reader=self,
                    ),
                    self.required_query,
                    self.global_config,
                )

            return response

    @staticmethod
    def _to_abs_path(rel_path: list) -> list:
        abs_path: list = []
        # condense the path
        for p in rel_path:
            for pp in p.split('/'):  # to consider '../../../'
                if pp in ('.', ''):
                    continue
                if pp == '..':
                    if abs_path:
                        abs_path.pop()
                elif pp == '...':
                    abs_path = []
                else:
                    abs_path.append(pp)
        return abs_path

    async def _walk(
        self,
        node: GraphNode,
        required: dict | RequestConfig,
        parent_config: RequestConfig,
    ):
        if isinstance(required, RequestConfig):
            return await self._resolve(node, required)

        if GeneralReader.__CONFIG__ in required:
            # resolve the current tree if config is present
            # excluding explicitly assigned keys
            current_config: RequestConfig = required[GeneralReader.__CONFIG__]
            await self._resolve(node, current_config, omit_keys=required.keys())
        else:
            current_config = parent_config

        full_path: list = self._root_path + node.current_path
        full_path_str: str = '/'.join(self._to_abs_path(full_path))
        is_current_path_file: bool = node.archive.raw_isfile(full_path_str)

        if not is_current_path_file:
            await _populate_result(
                node.result_root, full_path + ['m_is'], 'Directory', path_like=True
            )

        if Token.ENTRY in required:
            # implicit resolve
            if is_current_path_file and (
                results := await self._offload(
                    node.upload_id, full_path_str, required[Token.ENTRY], current_config
                )
            ):
                await _populate_result(
                    node.result_root, full_path + [Token.ENTRY], results, path_like=True
                )

        for key, value in required.items():
            if key == GeneralReader.__CONFIG__:
                continue

            child_path: list = node.current_path + [key]

            if not node.archive.raw_exists(
                '/'.join(self._to_abs_path(self._root_path + child_path))
            ):
                continue

            await self._walk(
                node.replace(
                    current_path=child_path,
                    current_depth=node.current_depth + 1,
                ),
                value,
                current_config,
            )

    async def _resolve(
        self,
        node: GraphNode,
        config: RequestConfig,
        *,
        omit_keys=None,
        wildcard: bool = False,
    ):
        # at the point, it is guaranteed that the current path exists, but it could be a relative path
        full_path: list = self._root_path + node.current_path
        abs_path: list = self._to_abs_path(full_path)

        os_path: str = '/'.join(abs_path)
        if not node.archive.raw_isfile(os_path):
            await _populate_result(
                node.result_root, full_path + ['m_is'], 'Directory', path_like=True
            )

        ref_path = ['/'.join(self._root_path)]
        if ref_path[0]:
            ref_path += node.current_path
        else:
            ref_path = node.current_path

        if config.pagination is not None:
            assert isinstance(config.pagination, RawDirPagination)
            start: int = config.pagination.get_simple_index()
            pagination: dict = config.pagination.model_dump(exclude_none=True)
        else:
            start = 0
            pagination = dict(page=1, page_size=10, order='asc')
        end: int = int(start) + int(pagination.get('page_size', 10))

        page = node.archive.raw_listdir_page(
            os_path,
            start=start,
            end=end,
            recursive=True,
            depth=config.depth if config.depth else -1,
            order=pagination.get('order', 'asc'),
            group_directories_first=True,
        )
        pagination['total'] = page.total

        await _populate_result(
            node.result_root,
            full_path + [Token.RESPONSE],
            _to_response_config(config, pagination=pagination),
            path_like=True,
        )

        page_files: list[RawPathInfo] = []
        for file in page.content:
            if not config.if_include(file.path):
                continue

            page_files.append(file)

        resolved_entries: dict[str, dict] = {}
        if config.directive is DirectiveType.resolved:
            resolved_entries = await self._batch_implicit_resolve(
                node.upload_id, page_files, omit_keys=omit_keys
            )

        for file in page_files:
            results = file._asdict()
            results.pop('access', None)
            if results.pop('is_file'):
                results['m_is'] = 'File'
            else:
                results = {'m_is': 'Directory'}
            if resolved := resolved_entries.get(file.path):
                results[Token.ENTRY] = resolved

            # need to consider the relative path and the absolute path conversion
            file_path: list = [
                v for v in file.path.split('/') if v
            ]  # path from upload root

            if not (result_path := ref_path + file_path[len(abs_path) :]):
                result_path = [file_path[-1]]

            await _populate_result(
                node.result_root, result_path, results, path_like=True
            )

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        try:
            if config.pagination is not None:
                config.pagination = RawDirPagination.model_validate(config.pagination)
        except Exception as e:
            raise ConfigError(str(e))

        return config

    async def _offload(
        self, upload_id: str, main_file: str, required, parent_config: RequestConfig
    ) -> dict:
        if entry := Entry.objects(upload_id=upload_id, mainfile=main_file).first():  # type: ignore  # type: ignore
            with EntryReader(
                required,
                user=self.user,
                init=False,
                config=parent_config,
                global_root=self.global_root,
                reader_cache=self._reader_cache,
            ) as reader:
                return await reader.read(entry.entry_id)
        return {}

    async def _batch_implicit_resolve(
        self, upload_id: str, files: list[RawPathInfo], *, omit_keys=None
    ) -> dict[str, dict]:
        """Resolve entry payloads for a file page with one batched mainfile lookup."""
        mainfiles = [
            file.path
            for file in files
            if file.is_file
            and (
                omit_keys is None
                or all(not file.path.endswith(os.path.sep + k) for k in omit_keys)
            )
        ]
        if not mainfiles:
            return {}

        def _retrieve():
            resolved: dict[str, dict] = {}
            for entry in Entry.objects(  # type: ignore
                upload_id=upload_id, mainfile__in=mainfiles
            ).order_by('mainfile', 'mainfile_key', 'entry_id'):
                resolved.setdefault(entry.mainfile, self._overwrite_entry(entry))
            return resolved

        return await asyncio.to_thread(_retrieve)


def _is_quantity_reference(definition) -> bool:
    # todo: those three should be handled differently as they are not references
    from nomad.datamodel.data import AuthorReference, UserReference
    from nomad.datamodel.datamodel import DatasetReference

    return (
        isinstance(definition, Quantity)
        and isinstance(definition.type, Reference)
        and not isinstance(
            definition.type, UserReference | AuthorReference | DatasetReference
        )
    )


class ArchiveReader(ArchiveLikeReader):
    """
    This class provides functionalities to read an archive with the required fields.
    A sample query will look like the following.

    .. code-block:: python
    {
        "results": { "m_request": RequestConfigInstance },
        "workflow": {
            "m_request": RequestConfigInstance,
            "calculation_result_ref": {
                "system_ref": { "m_request": RequestConfigInstance }
            }
        }
    }

    Usage:
    Since the reader needs to cache files, it is important to call the close method on exit.
    This can be done in three ways:
        1. Use plain create-read-close. Catch exceptions by yourself.
            >>> query = {}
            >>> user = {}
            >>> archive = {}
            >>> reader = ArchiveReader(query, user)
            >>> result = reader.sync_read(archive)
            >>> reader.close() # important
        2. Use context manager.
            >>> with ArchiveReader(query, user) as reader:
            >>>     result = reader.sync_read(archive)
        3. Use static method.
            >>> result = ArchiveReader.read_required(query, user, archive)
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @staticmethod
    def __if_strip(node: GraphNode, config: RequestConfig, *, depth_check: bool = True):
        if (
            isinstance(node.archive, GenericList | GenericDict)  # type: ignore
            and len(node.archive) == 0
        ):
            # an empty container is cheaper than a reference to it
            return False

        if (
            config.max_list_size is not None
            and isinstance(node.archive, GenericList)  # type: ignore
            and len(node.archive) > config.max_list_size
        ):
            return True

        if (
            config.max_dict_size is not None
            and isinstance(node.archive, GenericDict)  # type: ignore
            and len(node.archive) > config.max_dict_size
        ):
            return True

        if (
            depth_check
            and config.depth is not None
            and node.current_depth >= config.depth
        ):
            return True

        return False

    @traced(span_name='graph.archive.read')
    async def read(self, *args) -> dict:
        """
        Read the given archive with the required fields.
        Takes two forms of arguments:
            1. archive: dict | ArchiveDict
            2. upload_id: str, entry_id: str
        """

        async def _task(_archive):
            metadata = await goto_child(_archive, 'metadata')

            with self._prepare_reading() as response:
                with trace_span('graph.archive.walk'):
                    await self._walk(
                        GraphNode(
                            upload_id=await goto_child(metadata, 'upload_id'),
                            entry_id=await goto_child(metadata, 'entry_id'),
                            current_path=[],
                            result_root=response,
                            ref_result_root=self.global_root,
                            archive=_archive,
                            archive_root=_archive,
                            definition=EntryArchive.m_def,
                            visited_path=set(),
                            current_depth=0,
                            reader=self,
                            result_cursor=ResultCursor(response),
                        ),
                        self.required_query,
                        self.global_config,
                    )

                return response

        if len(args) == 1:
            return await _task(args[0])

        with self.load_archive(*args) as archive:
            return await _task(archive)

    async def _walk(
        self,
        node: GraphNode,
        required: dict | RequestConfig,
        parent_config: RequestConfig,
    ):
        """
        Walk through the archive according to the required query.
        The parent config is passed down to the children in case there is no config in any subtree.
        """
        if isinstance(required, RequestConfig):
            return await self._resolve(node, required)

        if required.pop(GeneralReader.__WILDCARD__, None):
            self._log(
                "Wildcard '*' as field name is not supported in archive query as its data is not homogeneous.",
                error_type=QueryError.NOTFOUND,
            )

        current_config: RequestConfig = required.get(
            GeneralReader.__CONFIG__, parent_config
        )

        # if it is a subtree, itself needs to be resolved
        if GeneralReader.__CONFIG__ in required:
            # keys explicitly given will be resolved later on during tree traversal
            # thus omit here to avoid duplicate resolve
            await self._resolve(node, current_config, omit_keys=required.keys())

        # update node definition if required
        node = await self._check_definition(node, current_config)
        try:
            # in case of a reference, resolve it implicitly
            node = await self._check_reference(
                node, current_config, implicit_resolve=True
            )
        except ValueError as e:
            self._log(str(e), error_type=QueryError.ARCHIVEERROR)
            return

        # walk through the required fields
        for key, value in required.items():
            if key == GeneralReader.__CONFIG__:
                continue

            if key == Token.DEFID:
                # ignore definition ID
                continue

            if key == Token.DEF:
                if current_config.m_def_format is MDefFormatType.short:
                    # m_def was already written as a compact string by _check_definition
                    continue
                if isinstance(node.definition, Quantity):
                    self._log(
                        f'Only support "m_def" token on sections, try defining "m_def" request on the parent.'
                    )
                    continue
                with DefinitionReader(
                    value,
                    user=self.user,
                    init=False,
                    config=current_config,
                    global_root=self.global_root,
                    reader_cache=self._reader_cache,
                ) as reader:
                    await _populate_result(
                        node.result_root,
                        node.current_path + [Token.DEF],
                        await reader.read(node.definition),
                    )
                continue

            # key may contain index, cached
            name, index = _parse_key(key)

            try:
                child_archive = await async_get(node.archive, name, None)
            except AttributeError as e:
                # implicit resolve failed, or wrong path given
                self._log(str(e), error_type=QueryError.NOTFOUND)
                continue

            # this may be a dict, a list, or a primitive value
            if child_archive is None:
                self._log(
                    f'Field {name} is not found in archive {node.generate_reference()}.',
                    error_type=QueryError.NOTFOUND,
                )
                continue

            # could just be a quantity
            child_definition = _get_property_definition(node, name)
            if child_definition is None:
                self._log(
                    f'Definition {name} is not found.',
                    error_type=QueryError.NOTFOUND,
                )
                continue

            is_list: bool = isinstance(child_archive, GenericList)  # type: ignore

            if (
                is_list
                and isinstance(child_definition, SubSection)
                and not child_definition.repeats
            ):
                self._log(f'Definition {key} is not repeatable.')
                continue

            if isinstance(value, RequestConfig):
                # Quantities are atomic from the graph reader's perspective, so a
                # plain request can copy them directly. Subsections still need a
                # definition-aware walk: archive dictionaries may contain fields
                # that are not part of their metainfo definition.
                if value.is_plain() and isinstance(child_definition, Quantity):
                    _node_cursor(node).child(name).set(child_archive)
                    continue

                child_node = node.descend(
                    name, archive=child_archive, definition=child_definition
                )
                await self._resolve_figure(child_node, node, value)
                # items explicitly requested next to the list (`name[i]`) are
                # walked on their own, like explicitly requested keys of a section
                omit_indices = (
                    _explicit_indices(required, name, len(child_archive))
                    if is_list and index is None
                    else None
                )
                await self._resolve(child_node, value, omit_indices=omit_indices)
            elif isinstance(value, dict):
                # this is a nested query, keep walking down the tree
                if is_list:
                    if GeneralReader.__CONFIG__ in value:
                        index = value[GeneralReader.__CONFIG__].index or index

                    list_node = node.descend(
                        name,
                        archive=child_archive,
                        definition=child_definition,
                    )
                    for i in _normalise_index(index, len(child_archive)):
                        await self._walk(
                            list_node.descend(str(i), archive=child_archive[i]),
                            value,
                            current_config,
                        )
                else:
                    await self._walk(
                        node.descend(
                            name, archive=child_archive, definition=child_definition
                        ),
                        value,
                        current_config,
                    )
            elif isinstance(value, list):
                # optionally support alternative syntax
                pass
            else:
                # should never reach here
                raise ConfigError(f'Invalid required config: {value}.')

    @staticmethod
    async def _resolve_figure(
        child: GraphNode, parent: GraphNode, config: RequestConfig
    ):
        """
        Ensure a Figure object is properly resolved.
        """
        if not isinstance(
            child.definition, SubSection
        ) or not child.definition.sub_section.m_follows(
            PlotlyFigure.m_def, self_as_definition=True
        ):
            return

        if config.directive is not DirectiveType.resolved:
            return

        async def _visit(_node):
            if isinstance(_node, GenericDict):
                for v in _node.values():
                    await _visit(v)
            elif isinstance(_node, GenericList):
                if len(_node) > 0 and isinstance(_node[0], int | float):
                    # looks like a data array
                    return
                for v in _node:
                    await _visit(v)
            elif isinstance(_node, str):
                _node = [x for x in _node.lstrip('#').split('/') if x]
                if _node and await _if_exists(parent.archive, _node):
                    await _populate_result(
                        parent.result_root,
                        parent.current_path + _node,
                        await _goto_path(parent.archive, _node),
                    )

        if not isinstance(child.archive, GenericList):
            return await _visit(child.archive)

        for i in _normalise_index(config.index, len(child.archive)):
            await _visit(child.archive[i])

    async def _resolve(
        self,
        node: GraphNode,
        config: RequestConfig,
        *,
        omit_keys=None,
        wildcard: bool = False,
        omit_indices: set[int] | None = None,
    ):
        """
        Resolve the given node.

        If omit_keys is given, the keys matching any of the omit_keys will not be resolved.
        Those come from explicitly given fields in the required query.
        They are handled by the caller. Likewise, `omit_indices` are the explicitly
        requested items of a list.
        """
        if isinstance(node.archive, GenericList):  # type: ignore
            if (
                isinstance(node.definition, Quantity)
                and isinstance(node.definition.type, Datatype)
                and config.index is None
            ):
                return await _populate_result(
                    node.result_root,
                    node.current_path,
                    f'__INTERNAL__:{node.generate_reference()}'
                    if self.__if_strip(node, config)
                    else node.archive,
                )
            if isinstance(node.definition, SubSection):
                node = node.replace(definition=_unwrap_subsection(node.definition))
            if (
                isinstance(node.definition, Section)
                and config.pagination is not None
                and config.index is None
            ):
                # an indexed request (`name[i]`) paginates the selected items instead
                config = await self._paginate_list(node, config)
                if config is None:
                    return
            return await self._resolve_list(
                node,
                config,
                omit_keys=omit_keys,
                wildcard=wildcard,
                omit_indices=omit_indices,
            )

        # no matter if to resolve, it is always necessary to replace the definition with potential custom definition
        node = await self._check_definition(node, config)
        try:
            # if it needs to resolve, it is necessary to check references
            node = await self._check_reference(
                node, config, implicit_resolve=omit_keys is not None
            )
        except ValueError as e:
            self._log(str(e), error_type=QueryError.ARCHIVEERROR)
            return

        if not isinstance(node.archive, GenericDict):  # type: ignore
            # primitive type data is always included
            # this is not affected by size limit nor by depth limit
            if (
                _is_quantity_reference(node.definition)
                and config.always_rewrite_references
            ):
                try:
                    if node.archive.startswith(('/', '#')):
                        # normalize a local reference
                        target_reference = node.generate_reference(
                            [v for v in node.archive.lstrip('/#').split('/') if v]
                        )
                    else:
                        target_reference = node.archive

                    result_to_write = _convert_ref_to_path_string(
                        target_reference, node.upload_id
                    )
                except Exception:  # noqa
                    result_to_write = await self._apply_resolver(node, config)
            else:
                result_to_write = await self._apply_resolver(node, config)

            _node_cursor(node).set(result_to_write)
            return

        if isinstance(node.definition, Quantity):
            # the container size limit does not recursively apply to JSON
            result_to_write = (
                f'__INTERNAL__:{node.generate_reference()}'
                if self.__if_strip(node, config)
                else await self._apply_resolver(node, config)
            )
            _node_cursor(node).set(result_to_write)
            return

        if not config.include_quantities:
            # without quantities, a section is listed even if it has no subsections
            _node_cursor(node).container(dict)
        if config.pagination is not None:
            # only repeating subsections are paged, those below inherit the page size
            config = _copy_request_config(config, pagination=_child_pagination(config))

        for key in node.archive.keys():
            if key in (Token.DEF, Token.DEFID):
                continue

            if config.if_include(key) and (
                omit_keys is None or all(not k.startswith(key) for k in omit_keys)
            ):
                child_definition = _get_property_definition(node, key)

                if child_definition is None:
                    self._log(f'Definition {key} is not found.')
                    continue

                if not config.include_quantities and isinstance(
                    child_definition, Quantity
                ):
                    continue

                child_archive = await goto_child(node.archive, key)
                if (
                    isinstance(child_definition, Quantity)
                    and not _is_quantity_reference(child_definition)
                    and key not in GeneralReader.__ALL_ID_FIELDS__
                    and config.resolve_type is None
                    and (
                        child_archive is None
                        or type(child_archive) in (str, int, float, bool)
                    )
                ):
                    _node_cursor(node).child(key).set(child_archive)
                    continue

                if is_subsection := isinstance(child_definition, SubSection):
                    child_definition = child_definition.sub_section

                child_node = node.descend(
                    key,
                    archive=child_archive,
                    definition=child_definition,
                    current_depth=node.current_depth + 1,
                )

                if self.__if_strip(child_node, config, depth_check=is_subsection):
                    _node_cursor(node).child(key).set(
                        f'__INTERNAL__:{child_node.generate_reference()}'
                    )
                    continue

                if (
                    (
                        config.include is _WILDCARD_FROZENSET
                        or config.include == _WILDCARD_FROZENSET
                    )
                    and config.exclude is None
                    and config.index is None
                    and (
                        config.property_name == key
                        or (
                            config.property_name not in GeneralReader.__ALL_ID_FIELDS__
                            and key not in GeneralReader.__ALL_ID_FIELDS__
                        )
                    )
                ):
                    child_config = config
                else:
                    child_config = _copy_request_config(
                        config,
                        property_name=key,
                        include=_WILDCARD_FROZENSET,
                        exclude=None,
                        index=None,
                    )

                if child_config.is_plain():
                    _node_cursor(node).child(key).set(child_node.archive)
                else:
                    await self._resolve_figure(child_node, node, child_config)
                    await self._resolve(child_node, child_config)

    async def _paginate_list(
        self, node: GraphNode, config: RequestConfig
    ) -> RequestConfig | None:
        """
        Apply `pagination` to a repeating subsection. The page is reported in the
        `m_response` of the parent section, as a list cannot carry its own. Returns
        the config that walks the items of the page, or `None` if the page is empty.
        """
        assert isinstance(config.pagination, ArchivePagination)
        start, end, response = config.pagination.resolve_window(len(node.archive))
        await _populate_result(
            node.result_root,
            node.current_path[:-1]
            + [Token.RESPONSE, 'subsection_lists', node.current_path[-1], 'pagination'],
            response,
        )
        _node_cursor(node).container(list)
        if start >= end:
            return None

        # the end of an index is inclusive
        return _copy_request_config(
            config, index=(start, end - 1), pagination=_child_pagination(config)
        )

    async def _check_definition(
        self, node: GraphNode, config: RequestConfig
    ) -> GraphNode:
        """
        Check the existence of custom definition.
        If positive, overwrite the corresponding information of the current node.
        """

        if not isinstance(node.archive, GenericDict):  # type: ignore
            return node

        custom_def: str | None = await async_get(node.archive, 'm_def', None)
        custom_def_id: str | None = await async_get(node.archive, 'm_def_id', None)

        use_qualified = config.m_def_format is MDefFormatType.short

        if custom_def is None and custom_def_id is None:
            if use_qualified or config.include_definition is DefinitionType.both:
                definition = _unwrap_subsection(node.definition)
                if use_qualified:
                    def_val = _get_short_definition_reference(
                        self._reader_cache, definition
                    )
                    _node_cursor(node).child(Token.DEF).set(def_val)
                else:
                    with DefinitionReader(
                        RequestConfig(
                            directive=DirectiveType.plain,
                            export_whole_package=config.export_whole_package,
                        ),
                        user=self.user,
                        init=False,
                        config=config,
                        global_root=self.global_root,
                        reader_cache=self._reader_cache,
                    ) as reader:
                        await _populate_result(
                            node.result_root,
                            node.current_path + [Token.DEF],
                            await reader.read(definition),
                        )
            return node

        try:
            new_def = await self._retrieve_definition(custom_def, custom_def_id, node)
        except Exception as e:
            self._log(
                f'Failed to retrieve definition: {e}', error_type=QueryError.NOTFOUND
            )
            return node

        if use_qualified:
            def_val = _get_short_definition_reference(self._reader_cache, new_def)
            _node_cursor(node).child(Token.DEF).set(def_val)
        elif config.include_definition is not DefinitionType.none:
            with DefinitionReader(
                RequestConfig(
                    directive=DirectiveType.plain,
                    export_whole_package=config.export_whole_package,
                ),
                user=self.user,
                init=False,
                config=config,
                global_root=self.global_root,
                reader_cache=self._reader_cache,
            ) as reader:
                await _populate_result(
                    node.result_root,
                    node.current_path + [Token.DEF],
                    await reader.read(new_def),
                )

        return node.replace(definition=new_def)

    async def _check_reference(
        self, node: GraphNode, config: RequestConfig, *, implicit_resolve: bool = False
    ) -> GraphNode:
        """
        Check the existence of custom definition.
        If positive, overwrite the corresponding information of the current node.
        """
        original_def = node.definition

        # resolve subsections
        if isinstance(original_def, SubSection):
            return node.replace(definition=_unwrap_subsection(original_def))

        if isinstance(original_def, Section):
            return node

        # if not a quantity reference, early return
        if not _is_quantity_reference(original_def):
            return node

        # for quantity references, early return if no need to resolve
        if not implicit_resolve and config.directive is not DirectiveType.resolved:
            return node

        if not isinstance(node.archive, str):
            raise ValueError(
                f'A reference string is expected at {"/".join(node.current_path)}.'
            )

        # maximum resolve depth reached, do not resolve further
        if config.resolve_depth and len(node.visited_path) == config.resolve_depth:
            return node

        try:
            resolved_node = await node.goto(node.archive, config.resolve_inplace)
        except ArchiveError as e:
            # cannot resolve somehow
            # treat it as a normal string
            # populate to the result
            self._log(str(e), error_type=QueryError.ARCHIVEERROR)
            await _populate_result(node.result_root, node.current_path, node.archive)
            return node

        ref = original_def.type
        target = (
            ref.target_quantity_def
            if isinstance(ref, QuantityReference)
            else ref.target_section_def
        )
        # need to check custom definition again since the referenced archive may have a custom definition
        return await self._check_definition(
            resolved_node.replace(definition=target.m_resolved()), config
        )

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        if config.query is not None:
            raise ConfigError(f'Query is not supported in {cls.__name__} @ {key}.')
        if config.pagination is not None:
            try:
                config.pagination = ArchivePagination.model_validate(
                    config.pagination
                    if isinstance(config.pagination, dict)
                    else config.pagination.model_dump(exclude_unset=True)
                )
            except ValidationError as e:
                raise ConfigError(f'Invalid pagination in {cls.__name__} @ {key}: {e}.')

        return config

    @staticmethod
    def read_required(
        archive: ArchiveDict | dict, required_query: dict | str, user=None
    ):
        """
        A helper wrapper.
        """
        with ArchiveReader(required_query, user=user) as reader:
            return reader.sync_read(archive)


class DefinitionReader(ArchiveLikeReader):
    async def read(self, archive: Definition) -> dict:
        with self._prepare_reading() as response:
            response[Token.DEF] = {}
            response[Token.DEFID] = archive.definition_id

            await self._walk(
                GraphNode(
                    upload_id='__NONE__',
                    entry_id='__NONE__',
                    current_path=[Token.DEF],
                    result_root=response,
                    ref_result_root=self.global_root,
                    archive=archive,
                    archive_root=None,
                    definition=None,
                    visited_path=set(),
                    current_depth=0,
                    reader=self,
                ),
                self.required_query,
                self.global_config,
            )

            return response

    async def _walk(
        self,
        node: GraphNode,
        required: dict | RequestConfig,
        parent_config: RequestConfig,
    ):
        if isinstance(required, RequestConfig):
            return await self._resolve(
                await self._switch_root(node, inplace=required.resolve_inplace),
                required,
            )

        current_config: RequestConfig = required.get(
            GeneralReader.__CONFIG__, parent_config
        )

        node = await self._switch_root(node, inplace=current_config.resolve_inplace)

        # if it is a subtree, itself needs to be resolved
        if GeneralReader.__CONFIG__ in required:
            # keys explicitly given will be resolved later on during tree traversal
            # thus omit here to avoid duplicate resolve
            await self._resolve(node, current_config, omit_keys=required.keys())

        def __convert(m_def):
            return _convert_ref_to_path_string(
                m_def.m_path(package_path=True, reference_like=True)
            )

        for key, value in required.items():
            if key == GeneralReader.__CONFIG__:
                continue

            # key may contain index, cached
            name, index = _parse_key(key)

            child_def = getattr(node.archive, name, None)
            if child_def is None:
                continue

            is_list: bool = isinstance(child_def, MSubSectionList)

            # for derived quantities like 'all_properties', 'all_quantities', etc.
            # normalise them to maps
            is_plain_container: bool = (
                False if is_list else isinstance(child_def, list | set | dict)
            )

            child_path: list = node.current_path + [name]

            # to avoid infinite loop
            # put a reference string here and skip it later
            if is_list:
                for i in _normalise_index(index, len(child_def)):
                    if child_def[i] is not node.archive:
                        continue
                    await _populate_result(
                        node.result_root, child_path + [str(i)], __convert(child_def[i])
                    )
                    break  # early return assuming children do not repeat
            elif is_plain_container:
                # this is a derived quantity like 'all_properties', 'all_quantities', etc.
                # just write reference strings to the corresponding paths
                # whether they shall be resolved or not is determined by the config and will be handled later
                if isinstance(child_def, dict):
                    await _populate_result(node.result_root, child_path, {})
                    for k, v in child_def.items():
                        await _populate_result(
                            node.result_root, child_path + [k], __convert(v)
                        )
                elif isinstance(child_def, set | list):
                    await _populate_result(node.result_root, child_path, [])
                    for i, v in enumerate(child_def):
                        await _populate_result(
                            node.result_root, child_path + [str(i)], __convert(v)
                        )
                else:
                    # should never reach here
                    raise  # noqa: PLE0704
            elif child_def is node.archive:
                assert isinstance(child_def, Definition)
                await _populate_result(
                    node.result_root, child_path, __convert(child_def)
                )

            async def __handle_derived(__func):
                if isinstance(child_def, dict):
                    for _k, _v in child_def.items():
                        await __func(child_path + [_k], _v)
                elif isinstance(child_def, set | list):
                    for _i, _v in enumerate(child_def):
                        await __func(child_path + [str(_i)], _v)
                else:
                    # should never reach here
                    raise  # noqa: PLE0704

            if isinstance(value, RequestConfig):
                # this is a leaf, resolve it according to the config
                async def __resolve(__path, __target):
                    __archive = _unwrap_subsection(__target)
                    if __archive is node.archive:
                        return
                    await self._resolve(
                        await self._switch_root(
                            node.replace(current_path=__path, archive=__archive),
                            inplace=value.resolve_inplace,
                        ),
                        value,
                    )

                if is_list:
                    for i in _normalise_index(index, len(child_def)):
                        await __resolve(child_path + [str(i)], child_def[i])
                elif is_plain_container:
                    if value.directive is DirectiveType.resolved:
                        await __handle_derived(__resolve)
                else:
                    await __resolve(child_path, child_def)
            elif isinstance(value, dict):
                # this is a nested query, keep walking down the tree
                async def __walk(__path, __target):
                    __archive = _unwrap_subsection(__target)
                    if __archive is node.archive:
                        return
                    await self._walk(
                        node.replace(current_path=__path, archive=__archive),
                        value,
                        current_config,
                    )

                if is_list:
                    # field[start:end]: dict
                    for i in _normalise_index(index, len(child_def)):
                        await __walk(child_path + [str(i)], child_def[i])
                elif is_plain_container:
                    await __handle_derived(__walk)
                else:
                    await __walk(child_path, child_def)
            elif isinstance(value, list):
                # optionally support alternative syntax
                pass
            else:
                # should never reach here
                raise ConfigError(f'Invalid required config: {value}.')

    async def _resolve(
        self,
        node: GraphNode,
        config: RequestConfig,
        *,
        omit_keys=None,
        wildcard: bool = False,
    ):
        if isinstance(node.archive, list):
            return await self._resolve_list(node, config)

        def __unwrap_ref(ref_type: Reference):
            """
            For a quantity reference, need to unwrap the reference to get the target definition.
            """
            return (
                ref_type.target_quantity_def
                if isinstance(ref_type, QuantityReference)
                else ref_type.target_section_def
            )

        def __convert(m_def):
            return _convert_ref_to_path_string(
                m_def.m_path(package_path=True, reference_like=True)
            )

        def __override_path(q, s, v, p):
            """
            Normalise all definition identifiers with unique global reference.
            """

            if isinstance(s, Quantity) and isinstance(v, dict):
                if isinstance(s.type, Reference):
                    v['type_data'] = __convert(__unwrap_ref(s.type))
            elif (
                isinstance(s, SubSection)
                and q.name == 'sub_section'
                and isinstance(v, str)
            ):
                v = __convert(s.sub_section)
            elif (
                isinstance(s, Section)
                and isinstance(v, str)
                and isinstance(q.type, MSectionReference)
            ):
                v = __convert(s.m_resolve(p))

            return v

        def __unique_name(item):
            # quantities may have identical names in different sections
            # cannot just use the name as the key
            # sections are guaranteed to have unique names
            return (
                f'{item.m_parent.name}.{item.name}'
                if isinstance(item, Quantity)
                else item.name
            )

        #
        # the actual logic starts here
        #

        # the following forces definitions being resolved per package
        if config.export_whole_package:
            pkg = node.archive
            while not isinstance(pkg, Package) and pkg.m_parent is not None:
                pkg = pkg.m_parent
            if pkg is not node.archive:
                node = await self._switch_root(
                    node.replace(archive=pkg),
                    inplace=config.resolve_inplace,
                )

        # use current path as the unique package identifier
        # instead of generating a new one from definition
        if not config.if_include('/'.join(node.current_path)):
            return

        # rewrite quantity type data with global reference
        if not self._check_cache(node.current_path, config.hash):
            self._cache_hash(node.current_path, config.hash)
            await _populate_result(
                node.result_root,
                node.current_path,
                node.archive.m_to_dict(
                    with_out_meta=True, with_def_id=True, transform=__override_path
                ),
                # the target location may contain a reference string already
                # the string was added during switching root
                # we allow it to be overwritten here
                overwrite_existing_str=True,
            )
            if isinstance(node.archive, Package):
                # always export the following for packages
                for name in ('all_quantities', 'all_sub_sections', 'all_base_sections'):
                    container: set = set()
                    for section in node.archive.section_definitions:
                        target = getattr(section, name)
                        container.update(
                            _unwrap_subsection(v)
                            for v in (
                                target
                                if isinstance(target, list | set)
                                else target.values()
                            )
                        )
                    output: dict = {__unique_name(v): __convert(v) for v in container}
                    await _populate_result(
                        node.result_root,
                        node.current_path + [name],
                        {k: v for k, v in sorted(output.items(), key=lambda x: x[1])},
                    )

        if isinstance(node.archive, Quantity):
            if isinstance(ref := node.archive.type, Reference):
                target = __unwrap_ref(ref)
                ref_str: str = target.m_path(package_path=True, reference_like=True)
                path_stack: list = _convert_ref_to_path(ref_str)
                # check if it has been populated
                if ref_str not in node.visited_path and not await _if_exists(
                    node.ref_result_root, path_stack
                ):
                    await self._resolve(
                        node.replace(
                            archive=target,
                            current_path=path_stack,
                            result_root=node.ref_result_root,
                            visited_path=node.visited_path.union({ref_str}),
                            current_depth=node.current_depth + 1,
                        ),
                        config,
                    )
            # no need to do anything for quantity
            # as quantities do no contain additional contents to be extracted
            return

        #
        # the following is for section and package
        #

        # no need to recursively resolve all relevant definitions if the directive is plain
        if config.directive == DirectiveType.plain:
            return

        # for definition, use either result depth or resolve depth to limit the recursion
        if config.depth is not None and node.current_depth + 1 > config.depth:
            return
        if (
            config.resolve_depth is not None
            and len(node.visited_path) + 1 > config.resolve_depth
        ):
            return

        async def __visit(_definition, _items):
            for _name in _items:
                for _index, _base in enumerate(getattr(_definition, _name, [])):
                    _section = _unwrap_subsection(_base)
                    _ref_str = _section.m_path(package_path=True, reference_like=True)
                    _path_stack = _convert_ref_to_path(_ref_str)
                    if _section is _definition or self._check_cache(
                        _path_stack, config.hash
                    ):
                        continue
                    await self._resolve(
                        await self._switch_root(
                            node.replace(
                                archive=_section,
                                current_path=node.current_path + [_name, str(_index)],
                                visited_path=node.visited_path.union({_ref_str}),
                                current_depth=node.current_depth + 1,
                            ),
                            inplace=config.resolve_inplace,
                        ),
                        config,
                    )

        if isinstance(node.archive, Package):
            for section in node.archive.section_definitions:
                await __visit(section, ('base_sections',))
        else:
            await __visit(
                node.archive,
                ('base_sections', 'sub_sections', 'quantities'),
            )

    @staticmethod
    async def _switch_root(node: GraphNode, *, inplace: bool) -> GraphNode:
        """
        Depending on whether to resolve in place, adapt the current root of the result tree.
        If NOT in place, write a global reference string to the current place, then switch to the referenced root.
        """
        if inplace:
            return node

        if isinstance(node.archive, Package):
            return node.replace(
                result_root=node.ref_result_root,
                current_path=[
                    Token.UPLOADS,
                    node.archive.upload_id,
                    Token.ENTRIES,
                    node.archive.entry_id,
                    Token.ARCHIVE,
                    'definitions',
                ]  # reconstruct the path to the definition in the archive
                if node.archive.entry_id and node.archive.upload_id
                else [
                    Token.METAINFO,
                    node.archive.name,
                ],  # otherwise a built-in package
            )

        # we always put a reference string at the current location
        # since the section may belong to another package
        # its definition may be placed in at the current location, or another location
        ref_str: str = node.archive.m_path(package_path=True, reference_like=True)
        if not isinstance(node.archive, Quantity):
            await _populate_result(
                node.result_root,
                node.current_path,
                _convert_ref_to_path_string(ref_str),
            )

        return node.replace(
            result_root=node.ref_result_root, current_path=_convert_ref_to_path(ref_str)
        )

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        return config


class MetainfoBrowser(DefinitionReader):
    """
    A special implementation of definition reader.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pagination_response: dict | None = None

    def _apply_query(self, config: RequestConfig) -> list[str]:
        if config.query is None:
            all_keys: list = list(Package.registry.keys())
        else:
            raise NotImplementedError
            # todo: implement query based filtering

        total: int = len(all_keys)

        default_pagination = config.pagination
        if default_pagination is not None:
            assert isinstance(default_pagination, MetainfoPagination)
            all_keys = default_pagination.order_result(all_keys)
            all_keys = default_pagination.paginate_result(all_keys, None)
        else:
            default_pagination = MetainfoPagination()

        # we use the class member to cache the response
        # it will be written to the result tree later
        # we do not direct perform writing here to avoid turning all methods async
        self._pagination_response = default_pagination.model_dump()
        self._pagination_response['total'] = total

        return all_keys

    def _filter_registry(self, config: RequestConfig, omit_keys=None) -> Iterator[str]:
        """
        Filter the registry based on the given config.
        """
        for pkg_name in self._apply_query(config):
            if not config.if_include(pkg_name):
                continue
            if omit_keys is not None and pkg_name in omit_keys:
                continue
            yield pkg_name

    async def _generate_package(
        self,
    ) -> AsyncIterator[tuple[str, Package, RequestConfig | dict]]:
        if isinstance(self.required_query, RequestConfig):
            for name in self._filter_registry(self.required_query):
                yield name, Package.registry[name], self.required_query
        else:
            has_wildcard: bool = GeneralReader.__WILDCARD__ in self.required_query

            if GeneralReader.__CONFIG__ in self.required_query:
                current_config: RequestConfig = self.required_query[
                    GeneralReader.__CONFIG__
                ]
                if has_wildcard:
                    child_config = self.required_query[GeneralReader.__WILDCARD__]
                else:
                    child_config = current_config.new(
                        {
                            'index': None,  # ignore index requirements for children
                            'query': None,  # ignore query for children
                            'pagination': None,  # ignore pagination for children
                        },
                        retain_pattern=True,  # alert: should the include/exclude pattern be retained?
                    )
                for name in self._filter_registry(
                    current_config, omit_keys=self.required_query.keys()
                ):
                    yield name, Package.registry[name], child_config
            elif has_wildcard:
                raise ValueError(
                    'Wildcard is not supported when no parent config is defined.'
                )

            for key, value in self.required_query.items():
                if key in (GeneralReader.__CONFIG__, GeneralReader.__WILDCARD__):
                    continue

                if key in Package.registry:
                    yield key, Package.registry[key], value
                elif PackageDefinition.has_definition(key):
                    # looks like a valid existing definition ID
                    try:
                        target = await self._retrieve_definition(None, key)
                        yield target.qualified_name(), target, value
                    except Exception as e:
                        self._log(f'Failed to retrieve definition: {e}')
                elif key.startswith('entry_id:'):
                    try:
                        yield key, await self._retrieve_definition(key), value
                    except Exception as e:
                        self._log(f'Failed to retrieve definition: {e}')
                else:
                    self._log(
                        f'Cannot identify definition format: {key}',
                        error_type=QueryError.NOTFOUND,
                    )

    async def read(self) -> dict:  # type: ignore # noqa
        with self._prepare_reading() as response:
            current_config = self.global_config
            if isinstance(self.required_query, dict):
                current_config = self.required_query.get(
                    GeneralReader.__CONFIG__, current_config
                )

            async for pkg_name, pkg_definition, pkg_query in self._generate_package():
                response.setdefault(pkg_name, {})
                await self._walk(
                    GraphNode(
                        upload_id='__NONE__',
                        entry_id='__NONE__',
                        current_path=[pkg_name],
                        result_root=response,
                        ref_result_root=self.global_root,
                        archive=pkg_definition,
                        archive_root=None,
                        definition=None,
                        visited_path=set(),
                        current_depth=0,
                        reader=self,
                    ),
                    pkg_query,
                    current_config,
                )

            if self._pagination_response is not None:
                await _populate_result(
                    response, [Token.RESPONSE, 'pagination'], self._pagination_response
                )
                # reset the cache to ensure re-entrance
                self._pagination_response = None

            return response

    @classmethod
    def validate_config(cls, key: str, config: RequestConfig):
        try:
            if config.query is not None:
                config.query = MetainfoQuery.model_validate(config.query)
            if config.pagination is not None:
                config.pagination = MetainfoPagination.model_validate(config.pagination)
        except Exception as e:
            raise ConfigError(str(e))

        return config


__M_SEARCHABLE__: dict = {
    Token.SEARCH: ElasticSearchReader,
    Token.METADATA: ElasticSearchReader,
    Token.ENTRY: EntryReader,
    Token.ENTRIES: EntryReader,
    Token.UPLOAD: UploadReader,
    Token.UPLOADS: UploadReader,
    Token.USER: UserReader,
    Token.USERS: UserReader,
    Token.DATASET: DatasetReader,
    Token.DATASETS: DatasetReader,
    Token.GROUP: UserGroupReader,
    Token.GROUPS: UserGroupReader,
}
