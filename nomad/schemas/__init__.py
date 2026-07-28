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

"""Lookup helpers for Python and database-backed metainfo schemas."""

from __future__ import annotations

import importlib
from typing import Any, NotRequired, TypedDict, cast

from cachetools import LRUCache

from nomad.metainfo import Definition, Package
from nomad.metainfo.util import MDefNotFound, MDefWithoutMetainfo


# Cache complete packages separately from their definitions. The latter lets a
# section ID identify its package without retaining an evicted package object.
_mongo_package_cache: LRUCache[str, Package] = LRUCache(128)
_mongo_definition_cache: LRUCache[str, tuple[str, int | None]] = LRUCache(8192)


class MongoPackage(TypedDict):
    """MongoDB representation of a serialized metainfo package."""

    snapshot_package_id: str
    snapshot_section_ids: list[str]
    package_definition: dict[str, Any]
    entry_id: NotRequired[str | None]
    upload_id: NotRequired[str | None]


def _cache_package_definitions(
    package: Package,
    package_id: str,
    section_ids: list[str],
) -> None:
    """Cache a package and map its package and section IDs to it."""
    _mongo_package_cache[package_id] = package
    _mongo_definition_cache[package_id] = (package_id, None)
    for section_index, section_id in enumerate(section_ids):
        _mongo_definition_cache[section_id] = (package_id, section_index)


def _get_cached_definition(definition_id: str) -> Definition | None:
    """Return a cached package or section definition, if its package is resident."""
    location = _mongo_definition_cache.get(definition_id)
    if location is None:
        return None

    package_id, section_index = location
    package = _mongo_package_cache.get(package_id)
    if package is None:
        return None

    if section_index is None:
        return package
    if section_index >= len(package.section_definitions):
        return None
    return package.section_definitions[section_index]


def cache_builtin_packages() -> None:
    """Add registered Python packages to the common schema lookup cache."""
    for package in Package.registry.values():
        _cache_package_definitions(
            package,
            package.definition_id,
            [section.definition_id for section in package.section_definitions],
        )


def _get_python_schema(qualified_name: str) -> Definition:
    """Import and resolve a Python schema class or one of its properties."""
    parts = qualified_name.split('.')
    module_path = '.'.join(parts[:-1])
    class_name = parts[-1]

    try:
        module = importlib.import_module(module_path)
        definition = getattr(module, class_name)
    except (ImportError, AttributeError, ValueError):
        try:
            module_path = '.'.join(parts[:-2])
            class_name = parts[-2]
            property_name = parts[-1]
            module = importlib.import_module(module_path)
            definition = getattr(getattr(module, class_name), property_name)
        except (ImportError, AttributeError, ValueError, IndexError) as e:
            raise MDefNotFound(
                f'Could not resolve {qualified_name} to a valid schema class or property.'
            ) from e

    if not hasattr(definition, 'm_def'):
        raise MDefWithoutMetainfo(
            f'{definition=} does not have metainfo definition.',
        )

    # Avoid importing these classes before metainfo initialization completes.
    from nomad.metainfo import Quantity, SubSection

    if isinstance(definition, (SubSection, Quantity)):
        return definition
    return cast(Definition, definition.m_def)


def _find_definition(package: Package, qualified_name: str) -> Definition:
    """Find a definition by qualified name within a loaded MongoDB package."""
    for definition in package.m_all_contents(depth_first=True, include_self=True):
        if (
            isinstance(definition, Definition)
            and definition.qualified_name() == qualified_name
        ):
            return definition
    raise MDefNotFound(f'Could not resolve {qualified_name} to a schema in MongoDB.')


def _load_mongo_package(mongo_package: MongoPackage) -> Package:
    """Deserialize a MongoDB package and add all of its definitions to the cache."""
    snapshot_package_id = mongo_package['snapshot_package_id']
    if package := _mongo_package_cache.get(snapshot_package_id):
        return package

    package = cast(Package, Package.m_from_dict(mongo_package['package_definition']))
    package.upload_id = mongo_package.get('upload_id')
    package.entry_id = mongo_package.get('entry_id')
    package.init_metainfo()
    package.snapshot_id = snapshot_package_id
    package.loaded_from_mongodb = True
    for snapshot, section in zip(
        mongo_package['snapshot_section_ids'], package.section_definitions
    ):
        section.snapshot_id = snapshot

    _cache_package_definitions(
        package, snapshot_package_id, mongo_package['snapshot_section_ids']
    )
    return package


def _get_mongo_schema(
    qualified_name: str | None, definition_id: str | None
) -> Definition:
    """Resolve an upload-scoped schema from MongoDB by ID or latest name."""
    from nomad.mongo.package import PackageDefinition

    if definition_id:
        if definition := _get_cached_definition(definition_id):
            return definition
        mongo_package = cast(MongoPackage, PackageDefinition.get_by(definition_id))
        package = _load_mongo_package(mongo_package)
        definition = _get_cached_definition(definition_id)
        if definition is None:
            raise MDefNotFound(
                f'Could not resolve definition ID {definition_id} to a schema in MongoDB.'
            )
        return definition

    if qualified_name is None:
        raise MDefNotFound('A qualified name or definition ID is required.')

    package_name = qualified_name.split('.', maxsplit=1)[0]
    mongo_package = (
        PackageDefinition.objects(qualified_name=package_name)
        .order_by('-date_created')
        .first()
    )
    if mongo_package is None:
        raise MDefNotFound(
            f'Could not resolve {qualified_name} to a schema in MongoDB.'
        )
    package = _load_mongo_package(
        cast(
            MongoPackage,
            mongo_package.to_mongo().to_dict()
            | {'snapshot_package_id': mongo_package.snapshot_package_id},
        )
    )
    return _find_definition(package, qualified_name)


def get_schema(
    qualified_name: str | None, definition_id: str | None = None
) -> Definition:
    """Return a schema definition from Python or MongoDB.

    ``entry_id:`` names identify upload-scoped schemas stored in MongoDB; all
    other qualified names identify Python definitions. When supplied, the
    definition ID pins the result and must belong to the qualified name.
    """
    if qualified_name is None or qualified_name.startswith('entry_id:'):
        definition = _get_mongo_schema(qualified_name, definition_id)
    else:
        definition = _get_python_schema(qualified_name)

    if definition_id is not None and definition.definition_id != definition_id:
        raise MDefNotFound(
            f'Definition ID {definition_id} does not match {qualified_name}.',
        )
    if (
        definition_id is not None
        and qualified_name is not None
        and qualified_name.startswith('entry_id:')
        and definition.qualified_name() != qualified_name
    ):
        raise MDefNotFound(
            f'Definition ID {definition_id} does not match {qualified_name}.',
        )
    return definition
