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

import copy
import dataclasses
import functools
import re
from typing import TypeAlias, cast

from fastapi import HTTPException
from msglc.reader import LazyDict, LazyList

from nomad import utils
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
from nomad.tracing import traced

from ..datamodel.context import ServerContext, parse_path
from .query import (
    ArchiveQueryError,
    _extract_child,
    _extract_key_and_index,
    _query_archive_key_pattern,
)
from .storage import ArchiveDict, ArchiveError, ArchiveList, ArchiveReader
from .storage_v2 import ArchiveDict as ArchiveDictNew
from .storage_v2 import ArchiveList as ArchiveListNew
from .utils import to_json

GenericList: TypeAlias = LazyList | ArchiveListNew | ArchiveList | list


class RequiredValidationError(Exception):
    def __init__(self, msg, loc):
        super().__init__(msg)
        self.msg = msg
        self.loc = loc


@functools.lru_cache(maxsize=1024)
def _parse_required_key(key: str) -> tuple[str, tuple[int, int] | int]:
    key = key.strip()
    match = _query_archive_key_pattern.match(key)

    if not match:
        raise HTTPException(422, detail=[dict(msg=f'invalid required key', loc=[key])])

    return _extract_key_and_index(match)


def _setdefault(target: dict | list, key, value_type: type):
    if isinstance(target, list):
        if target[key] is None:
            target[key] = value_type()
        return target[key]

    if key not in target:
        target[key] = value_type()

    return target[key]


@dataclasses.dataclass
class RequiredReferencedArchive:
    """
    Hold necessary information for each query so that there is no need to
    pass them around as separate arguments.

    The same RequiredReader object will be reused for different archives,
    so it is chosen to pass archive explicitly as a method argument instead of
    class member. Otherwise, a new RequiredReader object needs to be created.
    """

    entry_id: str = None  # The id of the entry that the current reference is in
    upload_id: str = None  # The id of the upload that the current reference is in
    path_prefix: str = None
    result_root: dict = None
    ref_result_root: dict = None
    archive_root: ArchiveDict | dict = None
    visited_paths: set = dataclasses.field(default_factory=set)
    definition: Definition = None

    def replace(self, **kwargs):
        return dataclasses.replace(self, **kwargs)


class RequiredReader:
    """
    Clients can read only the required parts of an archive. They specify the required
    parts with a required specification like this.

    .. code-block:: python
        {
            "results": "include-resolved",  // contains all the results and all its references
                                            // resolved
            "workflow": {
                "calculation_result_ref": {  // implicitly resolves the reference
                    "calculation_to_system_ref": "*"  // short-hand for include
                    "calculation_to_method_ref": "include"  // resolves and includes the target,
                                                            // but no references in the target
                                                            // are resolved.
                    "*": "*",  // includes everything else ...
                    "section_eigenvalues": "exclude"  // ... but explicitly excluded parts
                }
            }
        }

    The structure has to adhere to metainfo definitions of an archive's subsections and
    quantities. At each point in the specification, children can be replaced with certain
    directives.

    The different directives are:
        * include ('*' is an alias), includes whole subtree, resolves on a ref
        * include-resolved, includes whole subtree, resolves all refs recursively
        * exclude (in combination with wildcard keys), omits it from the result

    This class allows to keep a requirement specification and use it to read with it
    from given upload files and entry ids.

    Attributes:
        - required: The requirement specification as a python dictionary or directive string.
    """

    def __init__(
        self,
        required: dict | str,
        root_section_def: Section | None = None,
        resolve_inplace: bool = False,
        user=None,
    ):
        if root_section_def is None:
            from nomad import datamodel

            self.root_section_def = datamodel.EntryArchive.m_def
        else:
            self.root_section_def = root_section_def

        self.resolve_inplace = resolve_inplace
        self.required = copy.deepcopy(required)

        if (
            isinstance(self.required, dict)
            and (global_config := self.required.pop('resolve-inplace', None))
            is not None
        ):
            if not isinstance(global_config, bool):
                raise RequiredValidationError(
                    'resolve-inplace is not a bool', ['resolve-inplace']
                )
            self.resolve_inplace = global_config

        # store user information that will be used to retrieve references using the same authentication
        self.user = user

    @traced(span_name='archive.RequiredReader.read')
    def read(
        self, archive_reader: ArchiveReader, entry_id: str, upload_id: str
    ) -> dict:
        """
        Reads the archive of the given entry id from the given archive reader and applies
        the instance's requirement specification.
        """
        if entry_id in archive_reader:
            archive_root = archive_reader[entry_id]
        elif (normalized_id := utils.adjust_uuid_size(entry_id)) in archive_reader:
            archive_root = archive_reader[normalized_id]
        else:
            raise KeyError(entry_id)

        result_root: dict = {}
        ref_result_root: dict = {}

        dataset = RequiredReferencedArchive(
            entry_id,
            upload_id,
            '',
            result_root,
            ref_result_root,
            archive_root,
            set(),
            self.root_section_def,
        )

        result = self._apply_required(self.required, archive_root, dataset)
        result_root.update(**cast(dict, result))

        ref_result_root = {k: v for k, v in ref_result_root.items() if v}
        for value in ref_result_root.values():
            value.setdefault('m_def', 'nomad.datamodel.EntryArchive')

        result_root.update({'m_ref_archives': ref_result_root})

        return result_root

    def _resolve_refs(
        self, definition: Definition, archive: dict, dataset: RequiredReferencedArchive
    ) -> dict:
        """Resolves all references in archive."""
        if isinstance(definition, Quantity):
            # it's a quantity ref, the archive is already resolved
            return to_json(archive[definition.name])

        # it's a section ref
        archive = to_json(archive)

        if 'm_def' in archive:
            section_def = self._resolve_definition(
                dataset.upload_id, archive['m_def'].split('@')[0], dataset.archive_root
            )
            result = {'m_def': archive['m_def']}
        else:
            section_def = cast(Section, definition)
            result = {}

        for prop, value in archive.items():
            if (prop_def := section_def.all_properties.get(prop)) is None:
                continue
            if isinstance(prop_def, SubSection):

                def handle_item(v):
                    return self._resolve_refs(
                        prop_def.sub_section.m_resolved(), v, dataset
                    )
            elif isinstance(prop_type := prop_def.type, Reference):
                if isinstance(prop_type, QuantityReference):
                    target_def = prop_type.target_quantity_def.m_resolved()
                else:
                    target_def = prop_type.target_section_def.m_resolved()

                child_dataset = dataset.replace(definition=target_def)

                def handle_item(v):
                    return self._resolve_ref('include-resolved', v, child_dataset)
            else:
                result[prop] = to_json(value)
                continue

            try:
                result[prop] = (
                    [handle_item(item) for item in value]
                    if isinstance(value, GenericList)
                    else handle_item(value)
                )
            except ArchiveError as e:
                # We continue just logging the error. Unresolvable references
                # will appear as unset references in the returned archive.
                utils.get_logger(__name__).error('archive error', exc_info=e)

        return result

    def _resolve_ref(
        self, required: dict | str, path: str, dataset: RequiredReferencedArchive
    ) -> dict | str:
        # The archive item is a reference, the required is still a dict, the references
        # This is a simplified version of the metainfo implementation (m_resolve).
        # It implements the same semantics, but does not apply checks.
        # TODO the metainfo should also provide this implementation

        # check if local reference first so that it does not break backward compatibility
        if not path.startswith('/entries') and not path.startswith('/uploads'):
            if path.startswith('/'):
                # legacy version in the same entry
                return self._resolve_ref_local(required, path, dataset, True)

            if path.startswith('#/'):
                # new version in the same entry
                return self._resolve_ref_local(required, path[1:], dataset, True)

        # it appears to be a local path may or may not be the same archive
        url_parts = parse_path(path, dataset.upload_id)

        # cannot identify the path, return the path
        if url_parts is None:
            return path

        installation, upload_id, entry_id, kind, fragment = url_parts

        if entry_id == dataset.entry_id:
            # it's the same entry, we can resolve it
            return self._resolve_ref_local(required, fragment, dataset, True)

        if installation is None:
            # it is a local archive
            # check circular reference
            new_path = entry_id + fragment
            # circular reference, does not handle
            if new_path in dataset.visited_paths:
                return path

            other_archive = self._retrieve_archive(kind, entry_id, upload_id)
            # fail to parse the archive, the archive may not exist, return the plain path
            if other_archive is None:
                return path

            other_path = f'../uploads/{upload_id}/archive/{entry_id}'
            other_dataset = dataset.replace(
                entry_id=entry_id,
                upload_id=upload_id,
                path_prefix=f'{other_path}#',
                visited_paths=dataset.visited_paths.union({new_path}),
                archive_root=other_archive,
            )

            if self.resolve_inplace:
                # need to resolve it again to get relative position correctly
                return self._resolve_ref_local(
                    required,
                    fragment,
                    other_dataset.replace(result_root=dataset.result_root),
                    False,
                )

            # if not resolved inplace
            # need to create a new path in the result to make sure data does not overlap
            if other_path not in dataset.ref_result_root:
                dataset.ref_result_root[other_path] = {}

            # need to resolve it again to get relative position correctly
            return self._resolve_ref_local(
                required,
                fragment,
                other_dataset.replace(result_root=dataset.ref_result_root[other_path]),
                False,
            )

        # it appears to be a remote reference, won't try to resolve it
        if self.resolve_inplace:
            raise ArchiveQueryError(
                f'resolvable reference to remote archive not implemented: {path}'
            )

        # simply return the intact path if not required to resolve in-place
        return path

    def _resolve_ref_local(
        self,
        required: dict | str,
        path: str,
        dataset: RequiredReferencedArchive,
        same_entry: bool,
    ):
        """
        On enter, path must be relative to the archive root.
        """
        resolved = dataset.archive_root
        path_stack = path.strip('/').split('/')

        try:
            for prop in path_stack:
                resolved = resolved[int(prop) if prop.isdigit() else prop]
        except Exception:
            raise ArchiveError('could not resolve reference')

        # apply required to resolved archive_item
        if isinstance(required, str) and isinstance(dataset.definition, Quantity):
            resolved_result = to_json(resolved)
        else:
            resolved_result = self._apply_required(required, resolved, dataset)  # type: ignore

        # return or add to root depending on self.resolve_inplace
        if self.resolve_inplace:
            return resolved_result

        path_stack.reverse()
        target_container: dict | list = dataset.result_root
        # noinspection PyTypeChecker
        prop_or_index: str | int = None
        while len(path_stack) > 0:
            if prop_or_index is not None:
                target_container = _setdefault(target_container, prop_or_index, dict)

            prop_or_index = path_stack.pop()
            if len(path_stack) > 0 and path_stack[-1].isdigit():
                target_container = _setdefault(target_container, prop_or_index, list)
                prop_or_index = int(path_stack.pop())
                size_diff: int = prop_or_index - len(target_container) + 1
                if size_diff > 0:
                    target_container.extend([None] * size_diff)  # type: ignore

        target_container[prop_or_index] = resolved_result  # type: ignore

        return path if same_entry else f'{dataset.path_prefix}{path}'

    @staticmethod
    def _unwrap_reference(definition):
        if isinstance(definition, Quantity):
            if isinstance(def_type := definition.type, Reference):
                if isinstance(def_type, QuantityReference):
                    definition = def_type.target_quantity_def.m_resolved()
                else:
                    definition = def_type.target_section_def.m_resolved()
        elif isinstance(definition, SubSection):
            definition = definition.sub_section.m_resolved()
        return definition

    def _resolve_definition(self, upload_id, definition: str, archive_root):
        context = None
        if upload_id:
            from nomad.app.v1.routers.uploads import get_upload_with_read_access

            context = ServerContext(
                get_upload_with_read_access(upload_id, self.user, include_others=True)
            )

        def __resolve_definition_in_archive(
            _root: dict,
            _path_stack: list,
            _upload_id: str | None = None,
            _entry_id: str | None = None,
        ):
            custom_def_package: Package = Package.m_from_dict(_root, m_context=context)
            custom_def_package.entry_id = _entry_id
            custom_def_package.upload_id = _upload_id
            custom_def_package.init_metainfo()
            return custom_def_package.m_resolve_path(_path_stack)

        if definition is not None:
            if definition.startswith(('#/', '/')):
                # appears to be a local definition
                return __resolve_definition_in_archive(
                    to_json(archive_root['definitions']),
                    [
                        v
                        for v in definition.split('/')
                        if v not in ('', '#', 'definitions')
                    ],
                )

            # todo: !!!need to unify different formats!!! see also implementation in graph_reader.py
            # check if m_def matches the pattern 'entry_id:example_id.example_section.example_quantity'
            regex = re.compile(r'entry_id:(.+)(?:\.(.+))+')
            if match := regex.match(definition):
                entry_id = match.groups()[0]
                from nomad.processing import Entry

                upload_id = Entry.objects(entry_id=entry_id).first().upload_id  # type: ignore
                archive = self._retrieve_archive('archive', entry_id, upload_id)
                return __resolve_definition_in_archive(
                    to_json(archive['definitions']),
                    list(match.groups()[1:]),
                    upload_id,
                    entry_id,
                )

        proxy = MSectionReference().normalize(definition, context=context)
        return self._unwrap_reference(proxy.section_cls.m_def)

    def _apply_required(
        self,
        required: dict | str,
        archive_item: dict | str,
        dataset: RequiredReferencedArchive,
    ) -> dict | str:
        if archive_item is None:
            return None  # type: ignore

        result: dict = {}

        # avoid the bug in the old reader that primitive key-value is not included in toc
        if isinstance(archive_item, ArchiveDict):
            archive_item = to_json(archive_item)

        if (
            isinstance(archive_item, dict | ArchiveDictNew | LazyDict)
            and 'm_def' in archive_item
        ):
            dataset = dataset.replace(
                definition=self._resolve_definition(
                    dataset.upload_id,
                    archive_item['m_def'].split('@')[0],
                    dataset.archive_root,
                )
            )
            result['m_def'] = archive_item['m_def']

        if (directive := required if isinstance(required, str) else None) is not None:
            if directive == 'include-resolved':
                if isinstance(archive_item, str):
                    return self._resolve_ref(directive, archive_item, dataset)

                return self._resolve_refs(dataset.definition, archive_item, dataset)

            if directive in ['*', 'include']:
                return to_json(archive_item)

            raise ArchiveQueryError(f'unknown directive {required}')

        if isinstance(archive_item, str):
            return self._resolve_ref(required, archive_item, dataset)

        assert isinstance(required, dict)

        def handle_child(_prop, _index, _value):
            if (_prop_def := dataset.definition.all_properties.get(_prop)) is None:
                return

            _prop_def = self._unwrap_reference(_prop_def)

            def apply_kernel(_x):
                return self._apply_required(
                    _value, _x, dataset.replace(definition=_prop_def)
                )

            try:
                _child = _extract_child(archive_item, _prop, _index)

                if isinstance(_child, GenericList):
                    result[_prop] = [apply_kernel(item) for item in _child]
                else:
                    result[_prop] = apply_kernel(_child)
            except ArchiveError as _e:
                # We continue just logging the error. Unresolvable references
                # will appear as unset references in the returned archive.
                utils.get_logger(__name__).error('archive error', exc_info=_e)
            except (KeyError, IndexError):
                pass

        if '*' not in required:
            for key, val in required.items():
                if val != 'exclude':
                    prop, index = _parse_required_key(key)
                    handle_child(prop, index, val)
        else:
            # has wildcard, and potentially exclude, need to go through all children regardless
            prop_map: dict = {}
            for key, value in required.items():
                if key == '*':
                    continue
                prop, index = _parse_required_key(key)
                prop_map[prop] = {'index': index, 'value': value}

            default_required = required.get('*')
            for child in archive_item:
                child_config: dict = prop_map.get(child, {})
                if child_config.get('value') != 'exclude':
                    handle_child(
                        child,
                        child_config.get('index'),
                        child_config.get('value', default_required),
                    )

        return result

    def _retrieve_archive(self, kind: str, id_or_path: str, upload_id: str):
        """
        Retrieves the archive from the server using the stored user credentials.

        The entry_id based API is used.

        The upload_id is only used when the path to mainfile is given to fetch the corresponding entry_id.

        Returns:
            dict: The archive as a dict.
            None: The archive could not be found.
        """

        if kind == 'raw':
            # it is a path to raw file
            # get the corresponding entry id
            from nomad.processing import Entry

            entry: Entry = Entry.objects(  # type: ignore
                upload_id=upload_id, mainfile=id_or_path
            ).first()
            if not entry:
                # cannot find the entry, None will be identified in the caller
                return None

            entry_id = entry.entry_id
        else:
            # it is an entry id
            entry_id = id_or_path

        # do not rely on upload_id, as have to retrieve the archive via API due to user credentials
        from nomad.app.v1.routers.entries import answer_entry_archive_request

        # it should retrieve the minimum information from server with minimal bandwidth
        try:
            response = answer_entry_archive_request(
                dict(entry_id=entry_id), required='*', user=self.user
            )
        except HTTPException:
            # in case of 404, return None to indicate that the archive cannot be found
            return None

        return response['data']['archive']
