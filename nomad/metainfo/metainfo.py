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

import importlib
import inspect
import itertools
import json
import re
import sys
import warnings
from collections.abc import Callable as TypingCallable
from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy, deepcopy
from functools import wraps
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast
from urllib.parse import urldefrag

import docstring_parser
import jmespath
import pint
from json_stream import streamable_dict, streamable_list
from pydantic import BaseModel, TypeAdapter, ValidationError
from typing_extensions import deprecated

from nomad.metainfo.data_type import (  # noqa: F401
    JSON,
    URL,
    Bytes,
    Callable,
    Capitalized,
    Datatype,
    Datetime,
    Dimension,
    Enum,
    ExactNumber,
    File,
    InexactNumber,
    Number,
    Unit,
    check_dimensionality,
    m_str,
    normalize_type,
    to_json_schema_type,
)
from nomad.metainfo.data_type import Any as AnyType
from nomad.metainfo.util import (
    MQuantity,
    MSubSectionList,
    convert_to,
    default_hash,
    dict_to_named_list,
    metainfo_to_json_schema,
    quantity_to_json_schema,
    resolve_variadic_name,
    to_dict,
)
from nomad.units import ureg as units

from .annotation import (
    Annotation,
    AnnotationModel,
    DefinitionAnnotation,
    SectionAnnotation,
)

if TYPE_CHECKING:
    from nomad.datamodel.context import Context

    T = TypeVar('T')

m_package: Package | None = None

is_bootstrapping: bool = True
is_initializing_proto: bool = True
MSectionBound = TypeVar('MSectionBound', bound='MSection')

_UNSET_ = '__UNSET__'
_HASH_OBJ = type['hashlib._Hash']  # type: ignore
_suspended_change_tracking_sections: ContextVar[tuple[MSection, ...]] = ContextVar(
    '_suspended_change_tracking_sections', default=()
)


@contextmanager
def _without_change_tracking(section: MSection):
    suspended_sections = _suspended_change_tracking_sections.get()
    token = _suspended_change_tracking_sections.set((*suspended_sections, section))
    try:
        yield
    finally:
        _suspended_change_tracking_sections.reset(token)


# Metainfo errors


class MetainfoError(Exception):
    """Metainfo related errors."""

    pass


class DeriveError(MetainfoError):
    """An error occurred while computing a derived value."""

    pass


class MetainfoReferenceError(MetainfoError):
    """An error indicating that a reference could not be resolved."""

    pass


class MProxy:
    """
    A placeholder object that acts as reference to a value that is not yet resolved.

    A proxy is a replacement for an actual section (or quantity) that the proxy represents.
    The replaced section (or quantity) is identified by a reference. References
    are URL strings that identify a section (or quantity).

    If a proxy is accessed (i.e. like its proxies' counterpart would be accessed), it
    tries to resolve its reference and access the proxied element. If the reference
    cannot be resolved an exception is raised.

    There are different kinds of reference urls. Here are a few examples:

    .. code-block::
        /run/0/calculation/1  # same archive (legacy version)
        #/run/0/calculation/1  # same archive
        ../upload/archive/mainfile/{mainfile}#/run/0  # same upload
        /entries/{entry_id}/archive#/run/0/calculation/1  # same NOMAD
        /uploads/{upload_id}/archive/{entry_id}#/run/0/calculation/1  # same NOMAD
        https://my-oasis.de/api/v1/uploads/{upload_id}/archive/{entry_id}#/run/0/calculation/1  # global

    A URL can have 3 relevant path. The archive path as anchor string (after the `#`).
    The API part (more or less the URL's path). The oasis part (more or less the domain).

    The archive path starts at the `EntryArchive` root and uses property names and indices
    to navigate. The API and oasis parts correspond to NOMAD's v1 api. A path starting
    with `../upload` replaces `.../api/v1/uploads/{upload_id}` and allows to access the
    upload of the archive that contains the reference.

    The actual algorithm for resolving proxies is in `MSection.m_resolve()`.

    Attributes:
        m_proxy_value: The reference represented as a URL string.
        m_proxy_section:
            The section context, i.e. the section that this proxy is contained in. This
            section will provide the context for resolving the reference. For example,
            if the reference only has an archive part, this archive part is resolved
            starting with the root of `m_proxy_section`.
        m_proxy_context:
            Optional Context instance. Default is None and the m_context of the m_proxy_section
            is used.
    """

    def __init__(
        self,
        m_proxy_value: str,
        *,
        m_proxy_section: MSection | None = None,
        m_proxy_context: Context | None = None,
        m_proxy_type: QuantityReference | None = None,
    ):
        self.m_proxy_value = m_proxy_value
        self.m_proxy_section = m_proxy_section
        self.m_proxy_context = m_proxy_context
        self._proxy_type = m_proxy_type
        self.m_proxy_resolved = None

        if self._effective_context:
            self.m_proxy_value = self._effective_context.normalize_reference(
                self.m_proxy_section, self.m_proxy_value
            )

    @property
    def _effective_context(self) -> Context | None:
        if self.m_proxy_context is not None:
            return self.m_proxy_context

        if self.m_proxy_section is None:
            return None

        if self.m_proxy_section.m_context is not None:
            return self.m_proxy_section.m_context

        return self.m_proxy_section.m_root().m_context  # type: ignore

    def m_serialize_proxy_value(self):
        if self._proxy_type is None:
            return self.m_proxy_value

        return f'{self.m_proxy_value}/{self._proxy_type.target_quantity_def.name}'

    @staticmethod
    def _resolve_fragment(root: MSection, fragment: str):
        """
        Resolve a fragment starting from the given root section.
        Need to account for some special cases here.
            1. Loading a custom package from mongodb yields a missing 'definitions' path.
            2. Referencing a local custom section often does not include 'definitions' in the path.
        """
        if isinstance(root, Definition):
            resolved = root
            if isinstance(resolved, Package) and 'definitions' in fragment:
                fragment = fragment.replace('/definitions', '')
        elif isinstance(attached := getattr(root, 'definitions', None), Package):
            resolved = attached
        else:
            return root.m_resolve(fragment)

        for segment in filter(None, fragment.split('/')):
            for content in resolved.m_contents():
                if isinstance(content, Definition) and content.name == segment:
                    resolved = content
                    break
            else:
                return root.m_resolve(fragment)

        return resolved

    def _resolve_by_definition_id(self, definition_id: str):
        if not definition_id:
            return None

        return self._effective_context.fetch_section(None, definition_id)

    @staticmethod
    def _resolve_by_definition_name(definition_name: str):
        if '.' not in definition_name:
            return None

        package_name, section_name = definition_name.rsplit('.', 1)

        if package := Package.registry.get(package_name):
            package_name = package.name

        try:
            module = importlib.import_module(package_name)
            section = getattr(module, section_name, None)
            if section_def := getattr(section, 'm_def', None):
                return section_def
        except ImportError:
            pass

    def _resolve_by_relative_name(self, definition_name: str):
        if not isinstance(self.m_proxy_section, Definition):
            return None

        def _resolve_name(name: str, section: Definition):
            if section is None:
                return None

            if section.name == name and section != self.m_proxy_section:
                return section

            if isinstance(section, Section):
                for candidate in (
                    section.all_aliases.get(name),
                    section.all_inner_section_definitions.get(name),
                ):
                    if candidate and candidate != self.m_proxy_section:
                        return candidate
            elif isinstance(section, Package):
                resolved = section.all_definitions.get(name)
                if resolved and resolved != self.m_proxy_section:
                    return resolved

            if isinstance(parent := section.m_parent, Definition):
                return _resolve_name(name, parent)

        result = self.m_proxy_section
        for segment in definition_name.split('.'):
            result = _resolve_name(segment, result)

        if result:
            return result

        raise MetainfoReferenceError(
            f'Could not resolve {self.m_proxy_value} from scope {self.m_proxy_section}.'
        )

    def _resolve_impl(self):
        """
        This is the actual implementation of the resolution logic.
        It handles all possible kinds of references and does not differentiate between
        references to definitions and references to data.
        The following forms shall be taken into account.
        1. 'xxxxxxx@1s2d3a33...'
            This is a reference to a definition with its ID appended.
            Always try to resolve it by its ID, fall back to a name based resolution.
        2. 'a.b.c.d'
            This is a reference to a built-in python definition.
            Currently only used in 'm_def' thus 'a.b.c' is the module name and 'd' is the section name.
            Try to load it from the registry as all built-in definitions are cached in the registry.
        3. 'a_section_name'
            This is a pure section name, which is used in the input data of JSON/YAML archive files.
            This exists purely for convenience because it is cumbersome to provide a full path in JSON/YAML inputs.
            This will only exist in raw input files.
            Once loaded, all such references will be converted to the proper form.
        4. '#/path/to/section'
            This is a relative path within the same archive, pointing to either definition or data.
            Resolve it starting from the root of the current archive.
        5. '/uploads/<upload_id>/archive/<entry_id>#/path/to/section'
            This is a absolute path in an archive (maybe the current one), pointing to either definition or data.
            Need to first load the archive then resolve it starting from the root of the target archive.
        """
        if matches := MSectionReference.pattern.fullmatch(self.m_proxy_value):
            definition_name, definition_id = matches.groups()

            if result := self._resolve_by_definition_id(definition_id):
                return result

            if result := self._resolve_by_definition_name(definition_name):
                return result

            if result := self._resolve_by_relative_name(definition_name):
                return result

        if '@' in (archive_url := self.m_proxy_value):
            archive_url, definition_id = archive_url.rsplit('@', 1)
            if result := self._resolve_by_definition_id(definition_id):
                return result

        archive_url, fragment = urldefrag(
            archive_url if '#' in archive_url else f'#{archive_url}'
        )

        if not archive_url:
            return self._resolve_fragment(self.m_proxy_section.m_root(), fragment)

        if self._effective_context:
            return self._resolve_fragment(
                self._effective_context.resolve_archive_url(archive_url), fragment
            )

        raise MetainfoReferenceError(
            'Proxy with archive url, but no context to resolve it.'
        )

    def m_proxy_resolve(self):
        if self.m_proxy_resolved is not None:
            return self.m_proxy_resolved

        self.m_proxy_resolved = self._resolve_impl()

        if self.m_proxy_resolved is not None and isinstance(self, MProxy):
            self.__class__ = self.m_proxy_resolved.__class__
            self.__dict__.update(**self.m_proxy_resolved.__dict__)

        return self.m_proxy_resolved

    def __getattr__(self, key):
        if self.m_proxy_resolve() is not None:
            return getattr(self.m_proxy_resolved, key)

        raise MetainfoReferenceError(f'could not resolve {self.m_proxy_value}')

    def __repr__(self):
        return f'{self.__class__.__name__}({self.m_proxy_value})'


SectionProxy = MProxy


class QuantityType(Datatype):
    __slots__ = ()
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def serialize_self(self):
        return {
            'type_kind': 'custom',
            'type_data': f'{self.__class__.__module__}.{self.__class__.__name__}',
        } | self.flags

    def normalize(self, value, **kwargs):
        section = kwargs.get('section')

        try:
            return normalize_type(value).attach_definition(section)
        except (ValueError, KeyError, AttributeError):
            pass

        if inspect.isclass(value) and issubclass(value, Reference):
            # this is to account for AuthorReference and UserReference, etc.
            value = value()  # type: ignore

        if isinstance(value, Reference):
            if isinstance((proxy := value.target_section_def), MProxy):
                proxy.m_proxy_section = section
            return value.attach_definition(section)

        if isinstance(value, type) and hasattr(value, 'm_def'):
            return Reference(value.m_def).attach_definition(section)

        if isinstance(value, Quantity):
            return QuantityReference(value).attach_definition(section)

        if isinstance(value, dict):
            type_kind, type_data = value['type_kind'], value['type_data']
            if type_kind == 'reference':
                return Reference(
                    SectionProxy(type_data, m_proxy_section=section)
                ).attach_definition(section)

            if type_kind == 'quantity_reference':
                return QuantityReference(
                    MProxy(type_data, m_proxy_section=section)
                ).attach_definition(section)

        if isinstance(value, str):
            if value.startswith(('np.', 'numpy.')):
                raise MetainfoError(f'{value} is not a valid numpy type.')

            return Reference(
                SectionProxy(value, m_proxy_section=section)
            ).attach_definition(section)

        raise MetainfoError(f'Type {value} is not a valid quantity type.')

    def serialize(self, value, **kwargs):
        if isinstance(value, Datatype):
            return value.serialize_self()
        elif isinstance(value, Reference):
            # When requesting stable references, use this simplified serialization
            if type(value) in {
                Reference,
                QuantityReference,
                MSectionReference,
            } and kwargs.get('stable_references', False):
                target = value.target_section_def
                type_data = f'{target.qualified_name()}@{target.definition_id}'
                return {
                    'type_kind': 'quantity_reference'
                    if type(value) is QuantityReference
                    else 'reference',
                    'type_data': type_data,
                }
            else:
                transform = kwargs.get('transform')
                serialized = value.serialize_self(kwargs.get('section'))
                return transform(serialized) if transform is not None else serialized

        raise MetainfoError(f'Type {value} is not a valid quantity type.')


class Reference:
    def __init__(self, target_definition):
        self.target_section_def = (
            target_definition.m_def  # type: ignore
            if isinstance(target_definition, type)
            else target_definition
        )
        self._definition = None  # host

    def attach_definition(self, definition):
        self._definition = definition
        if (
            isinstance(proxy := self.target_section_def, MProxy)
            and proxy.m_proxy_section is None
        ):
            proxy.m_proxy_section = definition
        return self

    @property
    def target_definition_id(self):
        return self.target_section_def.definition_id

    @property
    def _proxy_type(self):
        return (
            MSectionReference() if self._definition is None else self._definition.type
        )

    def _check_shape(self, value):
        dimension: int = 0
        target = value
        while isinstance(target, list):
            dimension += 1
            if len(target) == 0:
                break
            # assuming consistent data
            target = target[0]

        if dimension != (
            0 if self._definition is None else len(self._definition.shape)
        ):
            raise ValueError(f'Invalid shape for {value}.')

        return value

    def serialize_self(self, section):
        if (context := section.m_root().m_context) is not None:
            try:
                type_data = context.create_reference(
                    section, self._definition, self.target_section_def
                )
            except AssertionError:
                type_data = None

            if type_data is None:
                type_data = self.target_section_def.qualified_name()
        else:
            type_data = self.target_section_def.m_path()

        return {'type_kind': 'reference', 'type_data': type_data}

    def _normalize_impl(self, value, **kwargs):
        section: MSection | None = kwargs.get('section', None)
        context: Context | None = kwargs.get('context', None)

        if isinstance(value, MProxy):
            value = value.m_proxy_value

        if isinstance(value, str | int | dict):
            if isinstance(value, str):
                if context is None and section is not None:
                    context = section.m_root().m_context
                value = (
                    context.normalize_reference(section, value) if context else value
                )

            assert not isinstance(self._proxy_type, QuantityReference)
            if isinstance(self, MSectionReference):
                return SectionProxy(
                    value, m_proxy_section=section, m_proxy_context=context
                )
            return MProxy(value, m_proxy_section=section, m_proxy_context=context)

        if (
            self.target_section_def.m_follows(Definition.m_def)
            and isinstance(value, type)
            and (definition := getattr(value, 'm_def', None)) is not None
        ):
            if definition.m_follows(self.target_section_def):
                return definition

        if not isinstance(value, MSection):
            raise TypeError(
                f'The value {value} is not a section and can not be used as a reference.'
            )

        if value.m_follows(self.target_section_def.m_resolved()):
            return value

        raise TypeError(f'{value} is not a valid value of {self._definition}.')

    # noinspection PyUnusedLocal
    def normalize(self, value, *, section=None, context=None, **kwargs):
        def _convert(_v):
            if isinstance(_v, list):
                return [_convert(v) for v in _v]

            return self._normalize_impl(_v, section=section, context=context)

        return self._check_shape(_convert(value))

    def _serialize_impl(self, section, value):
        if (context := section.m_root().m_context) is None:
            return value.m_path()

        if reference := context.create_reference(section, self._definition, value):
            return reference

        return value.qualified_name()

    def serialize(self, value, *, section, transform=None, **kwargs):
        def _convert(v, p=None):
            if isinstance(v, list):
                return [
                    _convert(x, [i] if p is None else p + [i]) for i, x in enumerate(v)
                ]

            if isinstance(v, MProxy) and v.m_proxy_resolved is None:
                intermediate = v.m_serialize_proxy_value()
            # When requesting stable references, use this simplified serialization
            elif (
                kwargs.get('stable_references', False)
                and isinstance(v, MSection)
                and hasattr(v, 'qualified_name')
                and hasattr(v, 'definition_id')
            ):
                intermediate = f'{v.qualified_name()}@{v.definition_id}'
            else:
                intermediate = self._serialize_impl(section, v)

            return intermediate if transform is None else transform(intermediate, p)

        return _convert(value)


class MSectionReference(Reference):
    """
    !!! FOR INTERNAL USE ONLY !!!
    """

    pattern = re.compile(r'(?:entry_id:)?(\w+(?:\.\w+)*)(?:@([a-f0-9]{40}))?')

    def __init__(self):
        super().__init__(Section.m_def)

    def _normalize_impl(self, value, **kwargs):
        if isinstance(value, str) and self.pattern.fullmatch(value):
            return SectionProxy(
                value,
                m_proxy_section=kwargs.get('section', None),
                m_proxy_context=kwargs.get('context', None),
            )

        return super()._normalize_impl(value, **kwargs)

    def _serialize_impl(self, section, value):
        if (
            (pkg := value.m_root()) is not section.m_root()
            and isinstance(value, Section)
            and isinstance(pkg, Package)
            and pkg.name not in (None, '*')
        ):
            if (qualified_name := value.qualified_name()) != f'{pkg.name}.{value.name}':
                raise ValueError(
                    'References to other packages for nested definitions are not supported.'
                )
            return qualified_name

        return super()._serialize_impl(section, value)


class QuantityReference(Reference):
    def __init__(self, quantity_def):
        super().__init__(quantity_def.m_parent)
        self.target_quantity_def = quantity_def

    @property
    def target_definition_id(self):
        return self.target_quantity_def.definition_id

    def serialize_self(self, section):
        type_data = self.target_quantity_def.m_path()
        return {
            'type_kind': 'quantity_reference',
            'type_data': type_data,
        }

    def _normalize_impl(self, value, **kwargs):
        if isinstance(value, str):
            assert isinstance(self._proxy_type, QuantityReference)
            return MProxy(
                value.rsplit('/', 1)[0],
                m_proxy_section=kwargs.get('section', None),
                m_proxy_context=kwargs.get('context', None),
                m_proxy_type=self._proxy_type,
            )

        if not value.m_is_set(self.target_quantity_def):
            return _UNSET_

        return super()._normalize_impl(value, **kwargs)

    def _serialize_impl(self, section, value):
        parent_path: str = super()._serialize_impl(section, value)

        return (
            parent_path.split('@', maxsplit=1)[0] + f'/{self.target_quantity_def.name}'
        )


# Metainfo data storage and reflection interface
class MObjectMeta(type):
    def __new__(cls, cls_name, bases, dct):
        do_init = dct.pop('do_init', True)

        template = super().__new__(cls, cls_name, bases, dct)

        init = getattr(template, '__init_cls__')
        if init is not None and do_init and not is_bootstrapping:
            init()

        return template


def constraint(warning):
    """A decorator for methods implementing constraints."""
    f = None
    if not isinstance(warning, bool):
        f = warning
        warning = False

    def decorator(_f):
        setattr(_f, 'm_constraint', True)
        setattr(_f, 'm_warning', warning)
        return _f

    return decorator if f is None else decorator(f)


def _track_changes(section: MSection | None):
    suspended_sections = _suspended_change_tracking_sections.get()

    while section is not None:
        if any(
            section is suspended_section for suspended_section in suspended_sections
        ):
            return

        section.m_mod_count += 1
        section = section.m_parent


def metainfo_modifier(method):
    """
    Decorate a method as a modifier that modifies the section.
    Whenever the method is called, increase the counter.
    """
    assert method.__name__ != '__set__'

    def wrapper(self, *args, **kwargs):
        _track_changes(self)
        return method(self, *args, **kwargs)

    return wrapper


def metainfo_setter(method):
    """
    Decorate a method as a modifier that modifies the section.
    Whenever the method is called, increase the counter.
    This differs from `modifier` in that it is used for setter methods.
    """
    assert method.__name__ == '__set__'
    assert method.__code__.co_argcount >= 3

    @wraps(method)
    def wrapper(self, obj, value, **kwargs):
        if obj is None:
            raise KeyError('Cannot overwrite definition. Only data can be set.')

        _track_changes(obj)
        return method(self, obj, value, **kwargs)

    return wrapper


# TODO find a way to make this a subclass of collections.abs.Mapping
class MSection(metaclass=MObjectMeta):
    """
    The base-class for all *section defining classes* and respectively the base-class
    for all section objects.

    While we use *section classes* to *define sections*, it is important to note that
    the *section class* is a different Python object than the actual *section definition*.
    For each *section class* (a Python class), we automatically generate a *section definition*
    Python object that instantiates :class:`Section`. :class:`MSection` and :class:`Section`
    are completely different classes. :class:`MSection` is used as a base-class for all
    *section defining* classes and :class:`Section` is a *section class* that defines the
    section `Section`.

    Attributes:
        m_def: Each *section class* (and also *section instance*) has a build-in
            property ``m_def`` that refers to the actual *section definition*. While this defined
            automatically, you can do it manually to provide additional characteristics that cannot
            be covered in a Python class definition.

    All `section instances` indirectly instantiate the :class:`MSection` and therefore all
    members of :class:`MSection` are available on all `section instances`. :class:`MSection`
    provides many special attributes and functions (they all start with ``m_``) that allow
    to reflect on a `section's definition` and allow to manipulate the `section instance`
    without a priori knowledge of the `section definition`.

    .. automethod:: m_set
    .. automethod:: m_get
    .. automethod:: m_add_values
    .. automethod:: m_get_sub_section
    .. automethod:: m_get_sub_sections
    .. automethod:: m_create
    .. automethod:: m_add_sub_section
    .. automethod:: m_remove_sub_section

    There are some specific attributes for section instances that are subsections of
    another section. While subsections are directly accessible from the containing
    section by using the Python property that represents the subsection (e.g.
    `run.section_system`), there is also a way to navigate from the subsection to
    the containing section (`parent section`) using these Python properties:

    Attributes:
        m_parent:
            If this section is a subsection, this references the parent section instance.

        m_parent_sub_section:
            If this section is a subsection, this is the :class:`SubSection` that defines
            this relationship.

        m_parent_index:
            For repeatable sections, parent keep a list of subsections. This is the index
            of this section in the respective parent subsection list.

        m_context: The :class:`MContext` that manages this (root-)section.

    Often some general tasks have to be performed on a whole tree of sections without
    knowing about the definitions in advance. The following methods allow to access
    subsections reflectively.

    .. automethod:: m_traverse
    .. automethod:: m_all_contents
    .. automethod:: m_contents
    .. automethod:: m_xpath

    Each section and all its quantities and contents can be transformed into a general
    JSON-serializable Python dictionary. Similarly, a section can be instantiated from
    such a Python dictionary. This allows to save and load sections to JSON-files or
    by other compatible means (e.g. document databases, binary JSON flavours).

    .. automethod:: m_to_dict
    .. automethod:: m_from_dict
    .. automethod:: m_update_from_dict
    .. automethod:: m_to_json
    """

    m_def: Section | None = None

    def __init__(
        self, m_def: Section | None = None, m_context: Context | None = None, **kwargs
    ):
        self.m_def: Section = m_def
        self.m_parent: MSection | None = None
        self.m_parent_sub_section: SubSection | None = None
        self.m_parent_index = -1
        self.m_context = m_context
        self.m_mod_count = 0
        self.m_cache: dict = {}  # dictionary for caching temporary values that are not persisted to the archive
        self.m_annotations: dict = {}

        # get missing m_def from class
        cls_def = self.__class__.m_def
        if self.m_def is None:
            self.m_def = cls_def

        # check m_def
        if cls_def is not None:
            if self.m_def != cls_def:
                raise MetainfoError('Section class and section definition must match.')

            if self.m_def.extends_base_section:
                raise MetainfoError(
                    'Section extends another section and cannot be instantiated.'
                )

        if is_bootstrapping:
            # this manual checking is required only during bootstrapping
            # for normal cases it will be handled via `m_update`
            if (target := kwargs.pop('type', None)) is not None:
                kwargs['type'] = QuantityType().normalize(target, section=self)

            self.__dict__.update(kwargs)
        else:
            self.m_update_from_dict(kwargs, skip_virtual=False)

    @classmethod
    def __init_cls__(cls):
        # ensure that the m_def is defined
        # do not accidentally get the m_def from a potential base section
        m_def = cls.__dict__.get('m_def')
        if m_def is None:
            m_def = Section()
            setattr(cls, 'm_def', m_def)

        # Use class name if name is not explicitly defined
        if m_def.name is None:
            m_def.name = cls.__name__
        m_def._section_cls = cls

        # add base sections
        base_sections: list[Section] = []
        for base_cls in cls.__bases__:
            if base_cls != MSection:
                if (base_section := getattr(base_cls, 'm_def', None)) is None:
                    raise TypeError(
                        'Section defining classes must have MSection or a descendant of base classes.'
                    )
                base_sections.append(base_section)

        if len(base_sections) > 0:
            m_def.m_set(Section.base_sections, base_sections)

        # transfer names, descriptions, constraints, event_handlers
        constraints: set[str] = set()
        event_handlers: set[TypingCallable] = set(m_def.event_handlers)
        for name, attr in cls.__dict__.items():
            # transfer names and descriptions for properties, init properties
            if isinstance(attr, Attribute | Property):
                attr.name = name
                if attr.description is not None:
                    attr.description = re.sub(
                        r'\(https?://[^)]*\)',
                        lambda m: re.sub(r'\n', '', m.group(0)),
                        inspect.cleandoc(attr.description).strip(),
                    )
                    attr.__doc__ = attr.description

                if isinstance(attr, Quantity):
                    m_def.m_add_sub_section(Section.quantities, attr)
                elif isinstance(attr, SubSection):
                    m_def.m_add_sub_section(Section.sub_sections, attr)
                elif isinstance(attr, Attribute):
                    m_def.m_add_sub_section(Section.attributes, attr)
            elif inspect.isclass(attr):
                inner_section_def = getattr(attr, 'm_def', None)
                if isinstance(inner_section_def, Section):
                    m_def.m_add_sub_section(
                        Section.inner_section_definitions, inner_section_def
                    )
            elif inspect.isfunction(attr):
                method_name = attr.__name__

                # transfer constraints
                if getattr(attr, 'm_constraint', False):
                    constraints.add(method_name)

                # register event_handlers from event_handler methods
                if method_name.startswith(('on_set', 'on_add_sub_section')):
                    event_handlers.add(attr)

        # add handler and constraints from base sections
        for base_section in m_def.all_base_sections:
            constraints.update(base_section.constraints)
            event_handlers.update(base_section.event_handlers)

        if len(constraints) > 0:
            m_def.constraints = list(sorted(constraints))
        if len(event_handlers) > 0:
            m_def.event_handlers = list(
                sorted(event_handlers, key=lambda x: x.__name__)
            )

        # add section cls' section to the module's package
        module_name = cls.__module__
        pkg = Package.from_module(module_name)
        pkg.m_add_sub_section(Package.section_definitions, cls.m_def)

        # apply_google_docstrings
        # Parses the Google doc string of the given class and properly updates the
        # definition descriptions.

        # This allows to document quantities and subsections with 'Args:' in the section
        # class. It will remove the 'Args' section from the section definition and will
        # set the respective pieces to the quantity and subsection descriptions.
        docstring = cls.__doc__
        if docstring is not None:
            parsed_docstring = docstring_parser.parse(docstring)
            short = parsed_docstring.short_description
            dsc = parsed_docstring.long_description

            if short and dsc:
                description = f'{short.strip()} {dsc.strip()}'
            elif short:
                description = short.strip()
            elif dsc:
                description = dsc.strip()
            else:
                description = None

            if m_def.description is None:
                m_def.description = description

            for param in parsed_docstring.params:
                prop = m_def.all_properties.get(param.arg_name)
                if prop is not None:
                    if prop.description is None:
                        prop.description = param.description

        for content in m_def.m_all_contents(depth_first=True, include_self=True):
            cast(Definition, content).__init_metainfo__()

    def __setattr__(self, name, value):
        if self.m_def is None:
            return super().__setattr__(name, value)

        alias_pool = self.m_def.all_aliases

        if alias_pool is not None and name in alias_pool:
            name = alias_pool[name].name
        elif self.m_def.has_variable_names and not name.startswith(('m_', 'a_', '_')):
            if resolved_name := resolve_variadic_name(self.m_def.all_properties, name):
                name = resolved_name.name

        return super().__setattr__(name, value)

    @property
    def m_key(self) -> str | int:
        """
        Return the key of the current section.
        The key can be used to identify different sections in a list.
        It is an alternative to index.
        The key is defined in `key_quantity` of the parent subsection.
        For example, if `key_quantity='field'`, then the value `self.field` will be used as the key.
        If `key_quantity` is not defined, it falls back to `label_quantity` for backward compatibility.
        If the above two are not defined, the `name` of the current section will be returned.
        If `name` is not defined, the index will be finally returned.

        Returns:
            str | int: The key of the current section.
        """
        default_name = getattr(self, 'name', self.m_parent_index)

        if (subsection := self.m_parent_sub_section) is None:
            return default_name

        if subsection.key_quantity is not None:
            key_quantity = subsection.key_quantity
        elif (label := subsection.more.get('label_quantity')) is not None:
            key_quantity = label
        else:
            return default_name

        quantity_def = self.m_def.all_quantities.get(key_quantity)
        if not isinstance(quantity_def.type, m_str | Enum):
            raise TypeError(f'Key quantity {key_quantity} must be of type str.')

        if self.m_is_set(quantity_def):
            return self.m_get(quantity_def)

        return default_name

    def __getattr__(self, name):
        if name.startswith('a_'):
            if (annotation_name := name[2:]) in self.m_annotations:
                return self.m_get_annotations(annotation_name)
        elif name in self.m_def.all_aliases or self.m_def.has_variable_names:
            # the first case will not throw
            # the second case will throw if the name is not found
            # we do not blindly try `m_get` for all unidentified names since it is very slow
            try:
                return self.m_get(name)
            except (MetainfoError, ValueError):
                pass

        raise AttributeError(name)

    def _ensure_definition(
        self,
        def_or_name: Property | str,
        *,
        hint: str | None = None,
    ) -> Property:
        """
        Return the definition.
        The definition is ensured to be a valid definition defined in the current section.

        Parameters:
            def_or_name: The definition of the target property.
                It can also be the name/alias of the property.
            hint: Only used for quantities that have variadic names.
                The hint is the name of one of the attributes defined in the target quantity.
                This will be used to help identify which quantity to check.
        """
        if isinstance(def_or_name, str):
            definition = resolve_variadic_name(
                self.m_def.all_aliases, def_or_name, hint
            )
        else:
            definition = def_or_name
            if (
                not is_initializing_proto
                and definition not in self.m_def.all_properties.values()
            ):
                raise MetainfoError(f'Property {definition} is not defined.')

        return definition

    # The following are the basic methods to set/get/append/remove a property in a section.
    # The property can be either a quantity or a subsection.
    # They shall be used as the sole gateway to access the properties of a section.
    # They may further delegate the actual operations to the `__set__` and `__get__` methods of the properties.

    def m_set(
        self,
        def_or_name: Property | str,
        value,
        *,
        hint: str | None = None,
        skip_virtual: bool = False,
        index: int | None = None,
        context: Context | None = None,
        **kwargs,
    ):
        """
        Set the given value for the given property.

        Parameters:
            def_or_name: The definition of the target property.
                It can also be the name/alias of the property.
            value: The value to set.
                A None value in `overwrite` mode will unset the property.
            hint: Only used for quantities that have variadic names.
                The hint is the name of one of the attributes defined in the target quantity.
                This will be used to help identify which quantity to set.
            skip_virtual: If true, skip setting virtual properties.
            index: Only used for repeating subsections in 'overwrite' mode.
                No effect in 'append' mode.
            context: The context to use when deserializing the value.
                This is used in setting sections, while the value is given as a serialized dictionary.
            kwargs: Additional keyword arguments to pass to the property setter.
        """
        definition: Property = self._ensure_definition(def_or_name, hint=hint)

        if isinstance(definition, SubSection):
            if isinstance(value, list):
                # this is used when deserializing a list of subsections
                for v in value:
                    self.m_append(definition, v, context=context, **kwargs)
                return

            if isinstance(value, dict):
                value = definition.sub_section.section_cls.m_from_dict(
                    value, m_context=context, m_parent=self, **kwargs
                )
        elif definition.virtual and skip_virtual:
            return

        if index is None:
            return definition.__set__(self, value, **kwargs)

        def _populate(_target):
            # overwrite mode with valid index
            if (diff := index - len(_target) + 1) > 0:
                _target.extend([None] * diff)
            _target[index] = value

        # index is not None
        if isinstance(definition, SubSection):
            if definition.repeats:
                # guaranteed to be a MSubSectionList
                _populate(definition.__get__(self))
            else:
                definition.__set__(self, value, **kwargs)
        elif isinstance(definition, Quantity):
            if definition.is_scalar:
                target = value
            else:
                # maybe this setting a single element shall not be allowed?
                target = definition.__get__(self)
                if target is None:
                    target = []
                _populate(target)
            # regardless, need to revalidate the value
            definition.__set__(self, target, **kwargs)
        else:
            # should never reach here
            raise NotImplementedError

    def m_get(
        self,
        def_or_name: Property | str | list[Property | str],
        *,
        full: bool = False,
        hint: str | None = None,
        index: int | slice | str | None = None,
        as_list: bool = False,
    ):
        """
        Retrieve the given property of the current section.

        Parameters:
            def_or_name: The definition of the target property.
                It can also be the name/alias of the property.
                A list of names or definitions can be provided to get multiple properties at once.
            full: Only used for quantities that use full storage.
                If True, the MQuantity object instead of the value will be returned.
            hint: Only used for quantities that have variadic names.
                The hint is the name of one of the attributes defined in the target quantity.
                This will be used to help identify which quantity to use.
            index: Only used if the property is a list.
                It can be an integer, a slice, for both quantities and subsections.
                It can also be a string, for subsections only.
            as_list: If True, return the value wrapped in a list.

        Returns:
            The value of the property.
        """
        if isinstance(def_or_name, list):
            return [
                self.m_get(x, full=full, hint=hint, index=index, as_list=as_list)
                for x in def_or_name
            ]

        definition = self._ensure_definition(def_or_name, hint=hint)

        def _wrap(_v):
            if _v is None:
                return [] if as_list else None

            return [_v] if as_list else _v

        if isinstance(definition, Quantity):
            if definition.use_full_storage:
                actual_name = (
                    def_or_name if isinstance(def_or_name, str) else def_or_name.name
                )
                return _wrap(
                    definition.__get__(self, full=full, actual_name=actual_name)
                )

            target = definition.__get__(self)
            if isinstance(target, list) and index is not None:
                assert isinstance(index, int | slice)
                try:
                    sliced = target[index]
                except IndexError:
                    sliced = None
            else:
                sliced = target

            # to account for the case where index is a slice
            return sliced if isinstance(sliced, list) else _wrap(sliced)

        if isinstance(definition, SubSection):
            target = definition.__get__(self)
            if not definition.repeats or target is None:
                return _wrap(target)

            if isinstance(index, str) and target.has_duplicated_key():
                raise MetainfoError(f'Multiple sections with key {index} exist.')

            try:
                sliced = target if index is None else target[index]
            except (KeyError, IndexError):
                sliced = None

            # to account for the case where index is a slice
            return sliced if isinstance(sliced, list) else _wrap(sliced)

        # should never reach here
        raise NotImplementedError

    def m_append(
        self,
        def_or_name: Property | str,
        value,
        *,
        hint: str | None = None,
        skip_virtual: bool = False,
        context: Context | None = None,
        **kwargs,
    ):
        """
        Append the given value to the given property.
        If it is a scalar quantity or non-repeating subsection, this is equivalent to `m_set`.
        If the property is not set, an empty list will be created.

        Parameters:
            def_or_name: The definition of the target property.
                It can also be the name/alias of the property.
            value: The value to set.
                A None value in `overwrite` mode will unset the property.
            hint: Only used for quantities that have variadic names.
                The hint is the name of one of the attributes defined in the target quantity.
                This will be used to help identify which quantity to set.
            skip_virtual: If true, skip setting virtual properties.
            context: The context to use when deserializing the value.
                This is used in setting sections, while the value is given as a serialized dictionary.
        """
        definition: Property = self._ensure_definition(def_or_name, hint=hint)

        if isinstance(definition, SubSection):
            if isinstance(value, list):
                assert definition.repeats
                # this is used when deserializing a list of subsections
                for v in value:
                    self.m_append(definition, v, context=context, **kwargs)
                return

            if isinstance(value, dict):
                value = definition.sub_section.section_cls.m_from_dict(
                    value, m_context=context, m_parent=self, **kwargs
                )

            if definition.repeats:
                definition.__get__(self).append(value)
            else:
                definition.__set__(self, value, **kwargs)
        elif isinstance(definition, Quantity):
            if definition.virtual and skip_virtual:
                return

            if definition.is_scalar:
                target = value
            else:
                # maybe this setting/appending a single element shall not be allowed?
                target = definition.__get__(self) or []
                target.append(value)
            # regardless, need to revalidate the value
            definition.__set__(self, target, **kwargs)
        else:
            # should never reach here
            raise NotImplementedError

    def m_remove(
        self,
        def_or_name: Property | str,
        *,
        hint: str | None = None,
        index: int | slice | str | None = None,
        mode: Literal['keep_size', 'pop'] = 'pop',
    ):
        """
        Remove the given property of the current section.

        Parameters:
            def_or_name: The definition of the target property.
                It can also be the name/alias of the property.
            hint: Only used for quantities that have variadic names.
                The hint is the name of one of the attributes defined in the target quantity.
                This will be used to help identify which quantity to use.
            index: Only used if the property is a list.
                It can be an integer, a slice, for both quantities and subsections.
                It can also be a string, for subsections only.
            mode: Only used if the target property is a list.
                If 'pop', pop the value and shrink the list.
                If 'keep_size', replace the existing value with None.
        """
        definition = self._ensure_definition(def_or_name, hint=hint)

        def _modify(_target):
            if _target is not None:
                if mode == 'keep_size':
                    _target[index] = None
                else:
                    _target.pop(index)
            return _target

        if isinstance(definition, SubSection):
            if definition.repeats:
                _modify(definition.__get__(self))
            else:
                definition.__set__(self, None)
        elif isinstance(definition, Quantity):
            if definition.use_full_storage:
                self.__dict__.get(definition.name, {}).pop(def_or_name, None)
            elif definition.is_scalar:
                definition.__set__(self, None)
            else:
                definition.__set__(self, _modify(definition.__get__(self)))
        else:
            # should never reach here
            raise NotImplementedError

    def m_get_quantity_definition(self, quantity_name: str, hint: str | None = None):
        """
        Get the definition of the quantity with the target name.

        An optional hint string can be provided. The hint should be the name of one of attributes
        defined in the target quantity.
        """
        return resolve_variadic_name(self.m_def.all_quantities, quantity_name, hint)

    def m_is_set(self, def_or_name: Property | str, *, hint: str | None = None) -> bool:
        """
        Check if the given property is set for the current section.

        Parameters:
            def_or_name: The definition of the target property.
                It can also be the name/alias of the property.
            hint: Only used for quantities that have variadic names.
                The hint is the name of one of the attributes defined in the target quantity.
                This will be used to help identify which quantity to check.
        """
        try:
            definition = self._ensure_definition(def_or_name, hint=hint)
        except (ValueError, MetainfoError):
            raise MetainfoError(
                f'{def_or_name} is not found in the definition of {self},'
                ' are you calling this method on the correct object?'
            )

        # derived quantity is always set
        if isinstance(definition, Quantity) and definition.derived is not None:
            return True

        return definition.name in self.__dict__

    @metainfo_modifier
    def _on_add_sub_section(
        self, sub_section_def: SubSection, sub_section: MSection, parent_index: int
    ):
        """
        Update parent information of the section to be added.
        Call the event handlers for adding a section.
        """
        if sub_section is None:
            return

        if not is_initializing_proto and not isinstance(
            sub_section, sub_section_def.sub_section.section_cls
        ):
            warnings.warn(
                f'{sub_section} is not derived from its definition {sub_section_def}.',
                category=SyntaxWarning,
            )

        sub_section.m_parent = self
        sub_section.m_parent_sub_section = sub_section_def
        sub_section.m_parent_index = parent_index

        for handler in self.m_def.event_handlers:
            if handler.__name__.startswith('on_add_sub_section'):
                handler(self, sub_section_def, sub_section)

    @metainfo_modifier
    def _on_remove_sub_section(
        self, sub_section_def: SubSection, sub_section: MSection
    ):
        """
        Update parent information of the section to be removed.
        Call the event handlers for removing a section.
        """
        if sub_section is None:
            return

        sub_section.m_parent = None
        sub_section.m_parent_sub_section = None
        sub_section.m_parent_index = -1

        for handler in self.m_def.event_handlers:
            if handler.__name__.startswith('on_remove_sub_section'):
                handler(self, sub_section_def, sub_section)

    def m_add_sub_section(
        self,
        sub_section_def: SubSection,
        sub_section: MSection | None,
    ) -> None:
        """Adds the given section instance as a subsection of the given subsection definition."""
        self.m_append(sub_section_def, sub_section)

    def m_remove_sub_section(self, sub_section_def: SubSection, index: int) -> None:
        """Removes the exiting section for a non-repeatable subsection"""
        self.m_remove(sub_section_def, index=index)

    def m_get_sub_section(self, sub_section_def: SubSection, index) -> MSection | None:
        return self.m_get(sub_section_def, index=index)

    def m_get_sub_sections(self, sub_section_def: SubSection) -> list | MSubSectionList:
        return self.m_get(sub_section_def, as_list=True)

    def m_sub_section_count(self, sub_section_def: SubSection) -> int:
        """Returns the number of subsections for the given subsection definition."""
        return len(self.m_get_sub_sections(sub_section_def))

    def m_set_section_attribute(self, name: str, value: Any) -> None:
        """
        Set attribute for the current section.
        """
        self.__set_attribute(None, name, value)

    def m_set_quantity_attribute(
        self, quantity_def: str | Quantity, name: str, value: Any
    ) -> None:
        """
        Set attribute for the given quantity.
        """
        self.__set_attribute(quantity_def, name, value)

    @metainfo_modifier
    def __set_attribute(
        self, tgt_property: str | None | Definition, attr_name: str, attr_value: Any
    ):
        """
        Set attribute for current section for a quantity of the current section.

        For attributes of the current section, use None as the target property.
        For attributes of a quantity, use the quantity name/definition as the target property.

        Both the quantity name and the attribute name can be variadic.

        Arguments:
            tgt_property: The name or definition of the quantity to set the attribute for, can be None.
            attr_name: The name of the attribute to set.
            attr_value: The value of the attribute to set.
        """
        tgt_name: str | None = (
            tgt_property.name if isinstance(tgt_property, Definition) else tgt_property
        )

        tgt_def, tgt_attr = self.m_def.get_attribute(tgt_property, attr_name)

        attr_value = tgt_attr.type.normalize(attr_value, section=self)

        if isinstance(tgt_def, Quantity) and tgt_def.use_full_storage:
            m_storage: dict | None = self.__dict__.get(tgt_def.name, None)
            m_quantity: MQuantity | None = (
                m_storage.get(tgt_property, None) if m_storage else None
            )
            if m_quantity is None:
                m_quantity = MQuantity(tgt_name, None)
                self.m_set(tgt_def, m_quantity)
            m_quantity.m_set_attribute(attr_name, attr_value)
        elif tgt_property is None:
            # indicating that the attribute is for the current section
            if 'm_attributes' not in self.__dict__:
                self.__dict__['m_attributes'] = {}
            self.__dict__['m_attributes'][attr_name] = attr_value

    def m_get_section_attribute(self, name: str) -> Any:
        """
        Get attribute for the current section.
        """
        return self.__get_attribute(None, name)

    def m_get_quantity_attribute(self, quantity_def: str, name: str) -> Any:
        """
        Get attribute for the given quantity.
        """
        return self.__get_attribute(quantity_def, name)

    def __get_attribute(self, tgt_property: str | None, attr_name: str):
        """
        Get the attribute of a quantity of the current section, or of the current section itself.
        """
        tgt_def: Definition = (
            tgt_property
            if tgt_property is None
            else self.m_def.get_attribute(tgt_property, attr_name)[0]
        )

        # section attributes
        if tgt_def is None:
            if 'm_attributes' not in self.__dict__:
                return None

            return self.__dict__['m_attributes'].get(attr_name, None)

        # quantity attributes
        m_storage: dict | None = self.__dict__.get(tgt_def.name, None)
        if m_storage is None:
            return None

        m_quantity: MQuantity | None = m_storage.get(tgt_property, None)
        if m_quantity is None:
            return None

        return m_quantity.attributes.get(attr_name, None)

    def m_create(
        self,
        section_cls: type[MSectionBound],
        sub_section_def: SubSection | None = None,
        **kwargs,
    ) -> MSectionBound:
        """Creates a section instance and adds it to this section provided there is a
        corresponding subsection.

        Args:
            section_cls: The section class for the subsection to create
            sub_section_def: If there are multiple subsections for the given class,
                this must be used to explicitly state the subsection definition.
        """

        section_def = section_cls.m_def.m_resolved()  # some input may be a proxy
        sub_section_defs = self.m_def.all_sub_sections_by_section.get(section_def, [])
        n_sub_section_defs = len(sub_section_defs)
        if n_sub_section_defs == 0:
            raise TypeError(
                f'There is no subsection to hold a {section_def} in {self.m_def}.'
            )

        if n_sub_section_defs > 1 and sub_section_def is None:
            raise MetainfoError(
                f'There are multiple subsections to hold a {section_def} in {self.m_def}, '
                f'but no subsection definition is explicitly given.'
            )

        if sub_section_def is not None and sub_section_def not in sub_section_defs:
            raise MetainfoError(
                f'The given subsection class {section_cls} does not '
                f'match the given subsection definition {sub_section_def}.'
            )

        if sub_section_def is None:
            sub_section_def = sub_section_defs[0]

        sub_section = section_cls(**kwargs)
        self.m_add_sub_section(sub_section_def, sub_section)

        return sub_section

    def m_setdefault(self, path: str | list):
        """
        Ensure the given path (e.g., 'a/b/1/c/2/d') exists and return the target section instance.
        The path is relative to the current section.

        Parameters:
            path: The path to the target section instance.
                Could be a string or a list.
                If it is a string, it should be separated by '/' or '.'.
                If it is a list, each element should be a string or an integer.
        """
        if isinstance(stack := path, str):
            stack = [x for x in stack.replace('/', '.').split('.') if x]

        if len(stack) == 0:
            return self

        def isint(s):
            if isinstance(s, str):
                return (s[1:] if s[0] in ('-', '+') else s).isdigit()
            return isinstance(s, int)

        child_name = stack.pop(0)
        child_index = int(stack.pop(0)) if len(stack) > 0 and isint(stack[0]) else None
        child_def = self._ensure_definition(child_name)
        assert isinstance(child_def, SubSection), (
            f'Could not find section definition with name "{child_name}".'
        )
        if (child_instance := self.m_get(child_def, index=child_index)) is None:
            child_instance = child_def.sub_section.section_cls()
            self.m_set(child_def, child_instance, index=child_index)

        if len(stack) == 0:
            return child_instance

        if isinstance(child_instance, MSection):
            return child_instance.m_setdefault(stack)

        raise MetainfoError(f'Please provide index for {child_def}.')

    @deprecated('Directly use `typing.cast` instead.')
    def m_as(self, _):
        return self

    def m_follows(
        self, definition: Section, *, self_as_definition: bool = False
    ) -> bool:
        """
        Determines if this section's definition is or is derived from the given definition.

        Arguments:
            definition: The definition to check against.
            self_as_definition: If True, the current section is considered as a definition
                instead of a data section. This is useful when directly checking the definitions
                without the need to create a dummy data section.
        """
        if not isinstance(definition, Section):
            raise TypeError(f'{definition} is not an instance of class Section.')

        target = self if self_as_definition else self.m_def

        return definition in itertools.chain(target.all_base_sections, [target])

    def m_to_dict(
        self,
        with_meta: bool = False,
        with_root_def: bool = False,
        with_out_meta: bool = False,
        with_def_id: bool = False,
        with_index: bool = False,
        include_defaults: bool = False,
        include_derived: bool = False,
        resolve_references: bool = False,
        stable_references: bool = False,
        categories: list[Category | type[MCategory]] | None = None,
        include: TypingCallable[[Definition, MSection], bool] | None = None,
        exclude: TypingCallable[[Definition, MSection], bool] | None = None,
        transform: TypingCallable[[Definition, MSection, Any, str], Any] | None = None,
        subsection_as_dict: bool = False,
        return_as_generator: bool = False,
    ) -> dict:
        """
        Returns the data of this section as a (json serializable) dictionary.

        With its default configuration, it is the opposite to :func:`MSection.m_from_dict`.

        There are a lot of ways to customize the behavior, e.g. to generate JSON for
        databases, search engines, etc.

        Arguments:
            with_meta: Include information about the section definition, the sections
                position in its parent, and annotations. For Definition instances this
                information will be included regardless; the section definition will
                always be included if the subsection definition references a base section
                and the concrete subsection is derived from this base section.
            with_out_meta: Exclude information `with_meta` information, even from
                Definition instances.
            with_root_def: Include the m_def for the top-level section. This allows to
                identify the used "schema" based on the root section definition.
            with_def_id: Include the definition id for the top-level section. This
                allows detection different versions of section definition used.
            with_index: Include index for subsections.
            include_defaults: Include default values of unset quantities.
            include_derived: Include values of derived quantities.
            resolve_references:
                Treat references as the sections and values they represent. References
                must not create circles; there is no check and danger of endless looping.
            categories: A list of category classes or category definitions that is used
                to filter the included quantities and subsections. Only applied to
                properties of this section, not on subsections. Is overwritten
                by partial.
            include: A function that determines if a property (quantity or subsection) will
                be included in the results. It takes the property definition and the current
                section as arguments. The function returns true for including and false for
                excluding the property. Include is applied recursively on subsections.
                Overrides categories.
            exclude: A function that determines if a property (quantity or subsection) will
                be excluded from the results. It takes the property definition and the current
                section as arguments. The function returns true for excluding and false for
                including the property. Exclude is applied recursively on subsections.
                Overrides categories.
            transform: A function that determines serialized quantity values.
                It takes the quantity definition, current section, the default
                serialized value and the metainfo path with respect to the
                document root as arguments. Depending on where this is used, you
                might have to ensure that the result is JSON-serializable.  By
                default, values are serialized to JSON according to the quantity
                type.
            stable_references: If true, use stable identifiers for definition
                references (e.g. base_sections, sub_section) instead of path-based
                keys. Stable identifiers use the format `qualified_name@tag`.
            subsection_as_dict: If true, try to serialize subsections as dictionaries.
                Only possible when the keys are unique. Otherwise, serialize as list.
            return_as_generator: If true, return as a generator instead of a dict.
        """
        if isinstance(self, Definition) and not with_out_meta:
            with_meta = True

        if subsection_as_dict:
            with_index = True

        kwargs: dict = dict(
            with_meta=with_meta,
            with_out_meta=with_out_meta,
            with_def_id=with_def_id,
            with_index=with_index,
            include_defaults=include_defaults,
            include_derived=include_derived,
            resolve_references=resolve_references,
            stable_references=stable_references,
            exclude=exclude,
            transform=transform,
            subsection_as_dict=subsection_as_dict,
            return_as_generator=return_as_generator,
        )

        assert not (include is not None and exclude is not None), (
            'You can only include or exclude, not both.'
        )

        if include is not None:

            def exclude(*args, **kwargs):  # pylint: disable=function-redefined
                return not include(*args, **kwargs)

            kwargs['exclude'] = exclude

        elif exclude is None:
            if categories is None:

                def exclude(prop, section):  # pylint: disable=function-redefined
                    return False

                kwargs['exclude'] = exclude
            else:
                category_defs: list[Category] = []
                for category in categories:
                    if issubclass(category, MCategory):  # type: ignore
                        category_defs.append(category.m_def)  # type: ignore
                    elif isinstance(category, Category):
                        category_defs.append(category)
                    else:
                        raise TypeError(f'{category} is not a category')

                def exclude(prop, section):  # pylint: disable=function-redefined
                    return not any(
                        prop in v.get_all_definitions() for v in category_defs
                    )

        def serialize_quantity(quantity, path, target_value=None):
            # get the value to be serialized
            # explicitly assigned the target value overrides the value from the section
            if target_value is None:
                target_value = self.__dict__.get(quantity.name, None)

            if target_value is None:
                if quantity.derived is None:
                    target_value = quantity.default
                else:
                    try:
                        target_value = quantity.derived(self)
                    except Exception:  # noqa
                        target_value = quantity.default

            def _transform_wrapper(_value, _stack=None):
                _path = path
                if _stack is not None:
                    _path += '/' + '/'.join(str(i) for i in _stack)
                return (
                    _value
                    if transform is None
                    else transform(quantity, self, _value, _path)
                )

            quantity_type = quantity.type

            if isinstance(quantity_type, Datatype) or not resolve_references:
                return quantity_type.serialize(
                    target_value,
                    section=self,
                    transform=_transform_wrapper,
                    stable_references=stable_references,
                )

            # need to resolve references
            if isinstance(quantity_type, QuantityReference):
                target_definition = quantity_type.target_quantity_def
                target_name = target_definition.name
                target_type = target_definition.type

                def _serialize_resolved(v, p=None):
                    if isinstance(v, list):
                        return [
                            _serialize_resolved(x, [i] if p is None else p + [i])
                            for i, x in enumerate(v)
                        ]

                    resolved_section = v.m_resolved()
                    try:
                        # should not use the following line alone
                        # to account for derived quantities
                        resolved_value = resolved_section.__dict__[target_name]
                    except KeyError:
                        # should not use the following line directly as
                        # it returns `pint.Quantity` for quantities with units
                        # here we want to get the value of the quantity stored in memory
                        resolved_value = getattr(resolved_section, target_name)

                    return target_type.serialize(
                        resolved_value,
                        section=resolved_section,
                        transform=_transform_wrapper,
                    )

                return _serialize_resolved(target_value)

            # other references
            def _serialize_section(v, p):
                if isinstance(v, list):
                    return [_serialize_section(x, f'{p}/{i}') for i, x in enumerate(v)]

                ref_kwargs = {k: v for k, v in kwargs.items() if k != 'transform'}
                if transform:

                    def _new_transform(_q, _s, _v, _):
                        return transform(_q, _s, _v, p)

                    ref_kwargs['transform'] = _new_transform

                return v.m_resolved().m_to_dict(**ref_kwargs)

            return _serialize_section(target_value, path)

        def serialize_attributes(attr_map: dict, all_attr: dict):
            result: dict = {}
            for attr_key, attr_value in attr_map.items():
                attr_def = resolve_variadic_name(all_attr, attr_key)
                result[attr_key] = attr_def.type.serialize(attr_value, section=self)
            return result

        def serialize_full(quantity_def: Quantity, values: dict[str, MQuantity]):
            result: dict = {}
            for m_quantity in values.values():
                m_result: dict = {
                    'm_value': serialize_quantity(quantity_def, '', m_quantity.value)
                }
                if m_quantity.unit:
                    m_result['m_unit'] = str(m_quantity.unit)
                if m_quantity.original_unit:
                    m_result['m_original_unit'] = str(m_quantity.original_unit)
                if m_quantity.attributes:
                    if a_result := serialize_attributes(
                        m_quantity.attributes, quantity_def.all_attributes
                    ):
                        m_result['m_attributes'] = a_result
                result[m_quantity.name] = m_result

            return result

        def serialize_annotation(annotation):
            if isinstance(annotation, Annotation):
                return annotation.m_to_dict()

            if isinstance(annotation, BaseModel):
                return annotation.model_dump()

            if not isinstance(annotation, dict):
                return str(annotation)

            try:
                json.dumps(annotation)
                return annotation
            except Exception:  # noqa
                return str(annotation)

        def items() -> Iterable[tuple[str, Any]]:
            # metadata
            if (
                with_meta
                or with_root_def
                or (
                    self.m_parent
                    and self.m_parent_sub_section.sub_section != self.m_def
                )
            ):
                if stable_references:
                    if isinstance(self, Definition):
                        # For Definition instances, use the instance's own
                        # identity (e.g. 'nomad.datamodel.metainfo.plot')
                        # rather than the meta-type (e.g. 'nomad.metainfo.metainfo.Package').
                        yield (
                            'm_def',
                            f'{self.qualified_name()}@{self.definition_id}',
                        )
                    else:
                        yield (
                            'm_def',
                            f'{self.m_def.qualified_name()}@{self.m_def.definition_id}',
                        )
                else:
                    yield 'm_def', self.m_def.definition_reference(self)
                if with_def_id:
                    yield 'm_def_id', self.m_def.definition_id

            if with_meta or with_index:
                if self.m_parent_index != -1:
                    yield 'm_parent_index', self.m_parent_index

            if with_meta:
                if self.m_parent_sub_section is not None:
                    yield 'm_parent_sub_section', self.m_parent_sub_section.name

            if len(self.m_annotations) > 0:
                m_annotations: dict = {
                    k: [
                        serialize_annotation(item)
                        for item in (v if isinstance(v, list) else [v])
                    ]
                    for k, v in self.m_annotations.items()
                }
                yield 'm_annotations', m_annotations

            # section attributes
            if attributes := self.__dict__.get('m_attributes', {}):
                yield (
                    'm_attributes',
                    serialize_attributes(attributes, self.m_def.all_attributes),
                )

            # quantities
            sec_path = self.m_path()
            for name, quantity in self.m_def.all_quantities.items():
                path = f'{sec_path}/{name}'
                if exclude(quantity, self):
                    continue

                try:
                    if quantity.virtual:
                        if include_derived and quantity.derived is not None:
                            yield name, serialize_quantity(quantity, path)
                        continue

                    if not self.m_is_set(quantity) and (
                        not include_defaults or not quantity.m_is_set(Quantity.default)
                    ):
                        continue

                    if quantity.use_full_storage:
                        yield name, serialize_full(quantity, self.__dict__[name])
                    else:
                        yield name, serialize_quantity(quantity, path)

                except ValueError as e:
                    raise ValueError(f'Value error ({str(e)}) for {quantity}')

            # subsections
            for name, sub_section_def in self.m_def.all_sub_sections.items():
                if exclude(sub_section_def, self):
                    continue

                if sub_section_def.repeats:
                    if self.m_sub_section_count(sub_section_def) > 0:
                        subsections = self.m_get_sub_sections(sub_section_def)
                        assert isinstance(subsections, MSubSectionList)
                        if subsection_as_dict and not subsections.has_duplicated_key():
                            serialised_dict: dict = {
                                item.m_key: item.m_to_dict(**kwargs)
                                for item in subsections
                                if item is not None
                            }
                            yield name, serialised_dict
                        elif return_as_generator:

                            def _subsection_gen(_subsections):
                                for item in _subsections:
                                    if item is None:
                                        yield None
                                    else:
                                        yield item.m_to_dict(**kwargs)

                            yield name, streamable_list(_subsection_gen(subsections))
                        else:
                            serialised_list: list = [
                                None if item is None else item.m_to_dict(**kwargs)
                                for item in subsections
                            ]
                            yield name, serialised_list
                elif (
                    sub_section := self.m_get_sub_section(sub_section_def, -1)
                ) is not None:
                    yield name, sub_section.m_to_dict(**kwargs)

        if not return_as_generator:
            return {key: value for key, value in items()}

        return streamable_dict(items())

    def m_update(self, **kwargs):
        """
        Updates all quantities and subsections with the given arguments.
        This is a convenience method that calls :func:`m_update_from_dict`.
        """
        return self.m_update_from_dict(kwargs)

    def m_update_from_dict(self, data: dict, **kwargs):
        """
        Updates this section with the serialized data from the given dict, e.g., data
        produced by :func:`m_to_dict`.
        """
        m_context: Context = self.m_context or self.m_root().m_context  # type: ignore

        # todo: the hack flag shall be removed
        # it is required due to json serialize nan and inf to null
        treat_none_as_nan = kwargs.get('treat_none_as_nan', False)
        skip_virtual = kwargs.get('skip_virtual', True)
        force_none = kwargs.get('force_none', False)

        if not isinstance(new_annotations := data.get('m_annotations', {}), dict):
            raise MetainfoError(
                f'The provided m_annotations is not a dict. {type(new_annotations).__name__} was provided.'
            )

        # get annotations from input data
        all_annotations: dict = new_annotations | {
            k[2:]: v for k, v in data.items() if k.startswith('a_')
        }

        for section_annotation in self.m_def.m_get_annotations(
            SectionAnnotation, as_list=True
        ):
            all_annotations.update(section_annotation.new(self))

        for annotation_name, annotation in all_annotations.items():
            AnnotationModel.model_rebuild()
            if annotation_model := AnnotationModel.m_registry.get(annotation_name):
                adapter = TypeAdapter(annotation_model)

                def to_model(_data):
                    if isinstance(_data, list):
                        return [to_model(_v) for _v in _data]

                    if _data is None:
                        return None

                    try:
                        _annotation = (
                            _data
                            if isinstance(_data, AnnotationModel)
                            else adapter.validate_python(_data)
                        )

                        if isinstance(self, Definition):
                            _annotation.m_definition = self

                        return _annotation
                    except ValidationError as e:
                        return AnnotationModel(m_error=str(e))

                annotation = to_model(annotation)

            if annotation:
                self.m_annotations[annotation_name] = annotation

        # need to deserialize the definitions first as they are needed for the rest
        # need to deserialize the metadata first as they are needed for the rest
        processed = []
        for item in ('metadata', 'definitions'):
            if item in data:
                try:
                    self.m_set(item, data[item], context=m_context)
                    processed.append(item)
                except (ValueError, MetainfoError):
                    pass

        if 'definitions' in processed:
            if self.metadata:
                self.definitions.entry_id = self.metadata.entry_id
                self.definitions.upload_id = self.metadata.upload_id
            else:
                self.definitions.entry_id = data.get('metadata', {}).get(
                    'entry_id', None
                )
                self.definitions.upload_id = data.get('metadata', {}).get(
                    'upload_id', None
                )

        for name, value in data.items():
            if name in processed or name.startswith(('m_', 'a_')):
                continue

            try:
                definition = self._ensure_definition(name)
            except (ValueError, MetainfoError):
                continue

            if isinstance(definition, SubSection):
                self.m_set(
                    definition,
                    list(sorted(value.values(), key=lambda x: x['m_parent_index']))
                    if definition.repeats and isinstance(value, dict)
                    else value,
                    context=m_context,
                    treat_none_as_nan=treat_none_as_nan,
                )
            else:
                self.m_set(
                    definition,
                    value,
                    # todo: the hack flag shall be removed
                    # it is required in editing, None is used to remove the value in indexing
                    force_none=force_none,
                    treat_none_as_nan=treat_none_as_nan,
                    skip_virtual=skip_virtual,
                )

        for name, value in data.get('m_attributes', {}).items():
            self.m_set_section_attribute(name, value)

        return self

    @classmethod
    def m_from_dict(
        cls: type[MSectionBound], data: dict[str, Any], **kwargs
    ) -> MSectionBound:
        """Creates a Section from the given serializable data dictionary.

        This is the 'opposite' of :func:`m_to_dict`. It takes a deserialized dict, e.g.
        loaded from JSON, and turns it into an instance of the given Section class.
        """
        return MSection.from_dict(data, cls=cls, **kwargs)

    @staticmethod
    def from_dict(
        dct: dict[str, Any],
        cls: type[MSectionBound] | None = None,
        m_parent: MSection | None = None,
        m_context: Context | None = None,
        **kwargs,
    ) -> MSectionBound:
        """Creates a section from the given serializable data dictionary.

        This is the 'opposite' of :func:`m_to_dict`. It is similar to the classmethod
        `m_from_dict`, but does not require a specific class. You can provide a class
        through the optional parameter. Otherwise, the section definition is read from
        the `m_def` key in the section data.
        """

        treat_none_as_nan: bool = kwargs.pop('treat_none_as_nan', False)

        if m_context:
            for entry_url, archive_json in dct.pop('m_ref_archives', {}).items():
                m_context.cache_archive(
                    entry_url, MSection.from_dict(archive_json, m_context=m_context)
                )

        def _ensure_definition(m_def: str | None, m_def_id: str | None):
            """
            If `m_def_id` is given, always try to get it from mongodb.
            If not cached, then consider `m_def`.
            If it does not match the existing definition, initialize it.
            """

            current_id = getattr(getattr(cls, 'm_def', {}), 'definition_id', None)
            if m_def_id is not None:
                if m_def_id == current_id:
                    return cls  # cls is not None

                if m_context and (new_def := m_context.fetch_section(m_def, m_def_id)):  # type: ignore
                    return new_def.section_cls

            if m_def is None:
                return cls

            # if m_def is present, always load it
            if (ref_section := m_parent) is not None:
                # archive with custom schema may directly use the section definition without
                # '/definitions/' prefix, thus the following line
                # if necessary, it will be switched back to the root later in resolution
                ref_section = (
                    getattr(ref_section.m_root(), 'definitions', None) or ref_section
                )

            # whether it is a python module name or a reference to an archive
            # it shall be able to be resolved
            target_section_def = MSectionReference().normalize(
                m_def,  # noqa
                section=ref_section,
                context=m_context,
            )
            return target_section_def.section_cls

        cls = _ensure_definition(dct.get('m_def', None), dct.get('m_def_id', None))

        assert cls is not None, 'Section definition or class needs to be known.'

        if m_context:
            kwargs['m_context'] = m_context

        section = cls(**kwargs)
        section.m_parent = m_parent

        with _without_change_tracking(section):
            section.m_update_from_dict(dct, treat_none_as_nan=treat_none_as_nan)

        # Advance the version once after bulk hydration so any cached values
        # potentially touched during load are considered stale afterwards.
        section.m_mod_count += 1

        return section

    def m_to_json(self, **kwargs) -> str:
        """Returns the data of this section as a JSON string."""
        return json.dumps(self.m_to_dict(), **kwargs)

    def m_all_contents(
        self,
        depth_first: bool = False,
        include_self: bool = False,
        stop: TypingCallable[[MSection], bool] | None = None,
    ) -> Iterable[MSection]:
        """
        Returns an iterable over all sub and sub subsections.

        Arguments:
            depth_first: A boolean indicating that children should be returned before
                parents.
            include_self: A boolean indicating that the results should contain this section.
            stop: A predicate that determines if the traversal should be stopped or if
                children should be returned. The sections for which this returns True
                are included in the results.
        """
        if include_self and not depth_first:
            yield self

        if stop is None or not stop(self):
            for content in self.m_contents():
                yield from content.m_all_contents(
                    depth_first=depth_first, include_self=True, stop=stop
                )

        if include_self and depth_first:
            yield self

    def m_traverse(
        self,
        return_all_sections: bool = False,
    ) -> Iterable[tuple[list[MSection] | MSection, Any, int, list[str | int]]]:
        """
        Performs a depth-first traversal of the data and yields a tuple for each
        encountered property.

        Arguments:
            return_all_definitions: If True, the first element of the yielded tuple
                contains a list of all sections from the root to the leaf section.
                If False, the first element is always just the section containing the property.

        The tuple is organized as follows:
          1. The parent section definition or list of all containing sections from the
             root to the leaf section if return_all_sections is True.
          2. Definition of the section or quantity.
          3. Parent index
          4. List that contains the path segments to the section or quantity.
        """
        empty = True
        for key, _ in self.__dict__.items():
            property_def = self.m_def.all_properties.get(key)
            if property_def is None:
                continue
            empty = False

            if isinstance(property_def, SubSection):
                repeats = property_def.repeats
                for i_repeated, sub_section in enumerate(
                    self.m_get_sub_sections(property_def)
                ):
                    parent_sections: list[MSection] = [self]
                    for (
                        sub_sections,
                        definition,
                        index,
                        sub_path,
                    ) in sub_section.m_traverse(return_all_sections):
                        parent_path: list[str | int] = [key]
                        if repeats:
                            parent_path.append(i_repeated)
                        yield (
                            (
                                (parent_sections + sub_sections)
                                if return_all_sections
                                else sub_sections
                            ),
                            definition,
                            index,
                            parent_path + sub_path,
                        )
                    yield (
                        (parent_sections if return_all_sections else self),
                        property_def,
                        sub_section.m_parent_index,
                        [key],
                    )
            else:
                yield ([self] if return_all_sections else self), property_def, -1, [key]

        if empty:
            yield ([self] if return_all_sections else self), None, -1, []

    def m_pretty_print(self, indent=None):
        """Pretty prints the containment hierarchy"""
        if indent is None:
            indent = []
        if len(indent) == 0:
            indent_str = ''
        else:
            indent_str = ''.join(['   ' if last else ' │ ' for last in indent[:-1]])
            indent_str += ' └╴' if indent[-1] else ' ├╴'
        print(indent_str + str(self))

        contents = list(self.m_contents())
        length = len(contents)
        for i, content in enumerate(contents):
            content.m_pretty_print(indent + [i == length - 1])

    def m_contents(self) -> Iterable[MSection]:
        """Returns an iterable over all direct subsections."""
        for sub_section_def in self.m_def.all_sub_sections.values():
            for sub_section in self.m_get_sub_sections(sub_section_def):
                if sub_section is not None:
                    yield sub_section

    def m_path(
        self, *, package_path: bool = False, reference_like: bool = False
    ) -> str:
        """
        Generate the path of this section or the specified quantity within the section hierarchy.

        Parameters:
            package_path: If True, the path starts from the package instead of other roots.
            reference_like: If True, rewrite custom definition paths to reference-like paths.
        """
        if package_path and isinstance(self, Package):
            if not self.m_is_custom_package:
                return self.name or '*'

            if not reference_like:
                return f'entry_id:{self.entry_id}'

            return f'../uploads/{self.upload_id}/archive/{self.entry_id}#definitions'

        if self.m_parent is None:
            return '/'

        segment = self.m_parent_sub_section.name
        if self.m_parent_index != -1:
            segment += f'/{self.m_parent_index:d}'

        parent_path = self.m_parent.m_path(
            package_path=package_path, reference_like=reference_like
        ).rstrip('/')
        return f'{parent_path}/{segment}'

    def m_root(self, cls: type[MSectionBound] | None = None) -> MSectionBound:
        """Returns the first parent of the parent section that has no parent; the root."""
        if self.m_parent is None:
            # todo: mypy bug https://github.com/python/mypy/issues/14458
            return cast(cls, self) if cls else self  # type: ignore
        else:
            return self.m_parent.m_root(cls)

    def m_parent_as(self, cls: type[MSectionBound] | None = None) -> MSectionBound:
        """Returns the parent section with the given section class type."""
        # todo: mypy bug https://github.com/python/mypy/issues/14458
        return cast(cls, self.m_parent) if cls else self.m_parent  # type: ignore

    def m_resolved(self):
        """
        Returns the original resolved object, if this instance used to be a proxy.

        For most purposes a resolved proxy is equal to the section it was resolved to.
        The exception are hashes. So if you want to use a potential former proxy in
        a hash table and make it really equal to the section it was resolved to, use
        the result of this method instead of the section/proxy itself.
        """
        return getattr(self, 'm_proxy_resolved', self)

    def m_resolve(self, path_with_id: str):
        """
        Resolves the given path or dotted quantity name using this section as context and
        returns the sub_section or value.

        Arguments:
            path_with_id: The reference URL. See `MProxy` for details on reference URLs.
        """
        path = path_with_id.split('@', maxsplit=1)[0]

        section = self.m_root() if path.startswith('/') else self

        path_stack = [x for x in path.split('/') if x]
        path_stack.reverse()
        while len(path_stack) > 0:
            prop_name = path_stack.pop()
            prop_def = section.m_def.all_aliases.get(prop_name, None)

            if prop_def is None:
                raise MetainfoReferenceError(
                    f'Could not resolve {path}, property {prop_name} does not exist'
                )

            if isinstance(prop_def, SubSection):
                if prop_def.repeats:
                    if len(path_stack) == 0:
                        return section.m_get_sub_sections(prop_def)

                    token: str = path_stack.pop()
                    index = int(token) if token.lstrip('-').isnumeric() else token

                    try:
                        section = section.m_get_sub_section(prop_def, index)
                    except Exception:
                        raise MetainfoReferenceError(
                            f'Could not resolve {path}, there is no subsection for {prop_name} at {index}.'
                        )

                else:
                    section = section.m_get_sub_section(prop_def, -1)
                    if section is None:
                        raise MetainfoReferenceError(
                            f'Could not resolve {path}, there is no subsection {prop_name}.'
                        )

            elif isinstance(prop_def, Quantity):
                if not section.m_is_set(prop_def):
                    raise MetainfoReferenceError(
                        f'Could not resolve {path}, {prop_name} is not set in {section}.'
                    )

                quantity = section.m_get(prop_def)
                while len(path_stack) > 0:
                    if not isinstance(quantity, list):
                        raise MetainfoReferenceError(
                            f'Could not resolve {path}, no property {prop_name}.'
                        )
                    quantity = quantity[int(path_stack.pop())]

                return quantity

        return section

    def m_get_annotation(self, key: str | type[T], default: T | None = None) -> T:
        return self.m_get_annotations(key, default)

    def m_get_annotations(self, key: str | type, default=None, as_list: bool = False):
        """
        Convenience method to get annotations

        Arguments:
            key: Either the optional annotation name or an annotation class. In the first
                case the annotation is returned, regardless of its type. In the second
                case, all names and list for names are iterated and all annotations of the
                given class are returned.
            default: The default, if no annotation is found. None is the default `default`.
            as_list: Returns a list, no matter how many annotations have been found.
        """
        if isinstance(key, str):
            value = self.m_annotations.get(key, default)
            if as_list and not isinstance(value, list | tuple):
                return [value]

            return value

        if isinstance(key, type):
            result_list: list = []
            for values in self.m_annotations.values():
                if isinstance(values, tuple | list):
                    result_list.extend(v for v in values if isinstance(v, key))
                elif isinstance(values, key):
                    result_list.append(values)

            if not as_list:
                if (result_len := len(result_list)) == 0:
                    return default
                if result_len == 1:
                    return result_list[0]

            return result_list

        raise TypeError('Key must be str or annotation class.')

    def m_all_validate(self):
        """Evaluates all constraints in the whole section hierarchy, incl. this section."""
        all_errors: list[str] = []
        all_warnings: list[str] = []
        for section in self.m_all_contents(include_self=True):
            more_errors, more_warnings = section.m_validate()
            all_errors.extend(more_errors)
            all_warnings.extend(more_warnings)

        return all_errors, all_warnings

    def m_validate(self) -> tuple[list[str], list[str]]:
        """Evaluates all constraints and shapes of this section and returns a list of errors."""
        all_errors: list[str] = []
        all_warnings: list[str] = []

        for constraint_name in self.m_def.constraints:
            constraint_func = getattr(self, constraint_name)
            try:
                constraint_func()
            except AssertionError as _e:
                message = str(_e).strip() or f'Constraint {constraint_name} violated.'
                if getattr(constraint_func, 'm_warning', False):
                    all_warnings.append(message)
                else:
                    all_errors.append(message)

        def _validate(_annotation):
            if isinstance(_annotation, list):
                for x in _annotation:
                    _validate(x)
                return

            if isinstance(_annotation, AnnotationModel):
                if _annotation.m_error:
                    # This annotation could not be parsed and only contains the error.
                    all_errors.append(_annotation.m_error)
                    return

                try:
                    # Trigger model validation by re-assigning the definition
                    _annotation.m_definition = self
                except ValidationError as e:
                    all_errors.append(
                        f'Annotation validation error for {self}: {str(e)}'
                    )

        _validate(list(self.m_annotations.values()))

        return all_errors, all_warnings

    def m_copy(self, deep=False):
        return deepcopy(self) if deep else copy(self)

    def __deepcopy__(self, memo):
        shallow_list = ('m_context',)
        unique_list = (
            'm_def',  # will be automatically set
            'm_parent',  # will be automatically set if self is attached to something
            'm_parent_sub_section',  # will be automatically set if self is attached to something
            '_cached_hash',  # unique to each instance
        )

        new_copy = self.m_def.section_cls()

        memo[id(self)] = new_copy
        # we do need to provide custom implementations of deepcopy for MSection
        # to ensure the inheritance structure is preserved
        for k, v in self.__dict__.items():
            # no need to deepcopy attributes unique to a specific instance
            if k in unique_list:
                continue

            if k in shallow_list:
                # no need to deepcopy metainfo related attributes
                new_copy.__dict__[k] = v
            elif isinstance(v, MSubSectionList):
                new_copy.m_set(
                    v.sub_section_def,
                    [x if x is None else deepcopy(x, memo) for x in v],
                )
            elif isinstance(v, MSection):
                new_copy.m_set(v.m_parent_sub_section, deepcopy(v, memo))
            else:
                new_copy.__dict__[k] = deepcopy(v, memo)

        return new_copy

    def m_warning(self, *args, **kwargs):
        if self.m_context is not None:
            self.m_context.warning(*args, **kwargs)

    def __repr__(self):
        m_section_name = self.m_def.name
        # name_quantity_def = self.m_def.all_quantities.get('name', None)
        # if name_quantity_def is not None:
        #     name = self.m_get(name_quantity_def)
        try:
            main = f'{self.__dict__["name"]}:{m_section_name}'
        except KeyError:
            main = m_section_name

        more = ''
        props = [prop for prop in self.m_def.all_properties if prop in self.__dict__]

        if len(props) > 10:
            more = f', +{len(props) - 10:d} more properties'

        return f'{main}({", ".join(props[0:10])}{more})'

    def __getitem__(self, key):
        try:
            return self.m_resolve(key.replace('.', '/'))
        except MetainfoReferenceError:
            raise KeyError(key)

    def __iter__(self):
        return self.m_def.all_properties.__iter__()

    def __len__(self):
        return len(self.m_def.all_properties)

    def get(self, key):
        return self.__dict__.get(key, None)

    def values(self):
        return [v for k, v in self.__dict__.items() if not k.startswith('m_')]

    def m_xpath(self, expression: str, dict: bool = True):
        """
        Provides an interface to jmespath search functionality.

        Arguments:
            expression: A string compatible with the jmespath specs representing the
                search. See https://jmespath.org/ for complete description.
            dict: Specifies if search result is to be converted to string.

        .. code-block:: python

            metainfo_section.m_xpath('code_name')
            metainfo_section.m_xpath('systems[-1].system_type')
            metainfo_section.m_xpath('sccs[0].system.atom_labels')
            metainfo_section.m_xpath('systems[?system_type == `molecule`].atom_labels')
            metainfo_section.m_xpath('sccs[?energy_total < `1.0E-23`].system')

        """
        result = jmespath.search(expression, self)
        return to_dict(result) if dict else result


class MCategory(metaclass=MObjectMeta):
    m_def: Category | None = None

    @classmethod
    def __init_cls__(cls):
        # ensure that the m_def is defined
        m_def = cls.m_def
        if m_def is None:
            m_def = Category()
            setattr(cls, 'm_def', m_def)

        # transfer name and description to m_def
        m_def.name = cls.__name__
        if cls.__doc__ is not None:
            m_def.description = inspect.cleandoc(cls.__doc__).strip()

        # add section cls' section to the module's package
        module_name = cls.__module__
        pkg = Package.from_module(module_name)
        pkg.m_add_sub_section(Package.category_definitions, cls.m_def)


# Metainfo M3 (i.e. definitions of definitions)


class Definition(MSection):
    """
    :class:`Definition` is the common base class for all metainfo definitions.

    All metainfo `definitions` (sections, quantities, subsections, packages, ...) share
    some common properties.

    Attributes:
        name: Each `definition` has a name. Names have to be valid Python identifier.
            They can contain letters, numbers and _, but must not start with a number.
            This also qualifies them as identifier in most storage formats, databases,
            makes them URL safe, etc.

            Names must be unique within the :class:`Package` or :class:`Section` that
            this definition is part of.

            By convention, we use capitalized `CamelCase` identifier to refer to *sections
            definitions* (i.e. section definitions are represented by Python classes),
            lower case `snake_case` identifier for variables that hold *sections*, and for
            *properties* (i.e. fields in a Python class) we typically use lower
            case `snake_case` identifier. Subsections are often prefixed with ``section_``
            to clearly separate subsections from quantities.

            Generally, you do not have to set this attribute manually, it will be derived
            from Python identifiers automatically.

        label: Each `definition` can have an optional label. Label are like names, but
            do not have to adhere to the Python identifier syntax.

        description: The description can be an arbitrary human-readable text that explains
            what a definition is about. For section definitions you do not have to set
            this manually as it will be derived from the classes doc string. Quantity and
            subsection descriptions can also be taken from the containing section class'
            doc-string ``Attributes:`` section.

        links: Each definition can be accompanied by a list of URLs. These should point
            to resources that further explain the definition.

        aliases: A list of alternative names. For quantities and subsections these
            can be used to access the respective property with a different name from
            its containing section. Package aliases will be considered when resolving
            Python references, e.g. in `m_def`.

        variable:
            A boolean that indicates this property has variable parts in its name.
            If this is set to true, all capital letters in the name can be
            replaced with arbitrary strings. However, variable names work similar to
            aliases and can be considered on-demand aliases. Other aliases and the
            defined name will work as well. Thus, variable names are only resolved
            at runtime by the Python interface and are not directly serialized.
            However, the variable name is set in a meta attribute `m_source_name`
            automatically for properties (but not attributes).
            Variable names are only implemented for Quantity, SubSection,
            Attribute.

        deprecated: If set this definition is marked deprecated. The value should be a
            string that describes how to replace the deprecated definition.

        categories: All metainfo definitions can be put into one or more `categories`.
            Categories allow to organize the definitions themselves. It is different from
            sections, which organize the data (e.g. quantity values) and not the definitions
            of data (e.g. quantities definitions). See :ref:`metainfo-categories` for more
            details.

        more: A dictionary that contains additional definition properties that are not
            part of the metainfo. Those can be passed as additional kwargs to definition
            constructors. The values must be JSON serializable.

        attributes:
            The attributes that can further qualify property values.

        all_attributes:
            A virtual convenient property that provides all attributes as a dictionary
            from attribute name to attribute. This includes meta attributes (starting with m_)
            that are defined for all properties of the same kind (sub_section or quantity).
    """

    name: Quantity = None
    label: Quantity = None
    description: Quantity = None
    links: Quantity = None
    categories: Quantity = None
    deprecated: Quantity = None
    aliases: Quantity = None
    variable: Quantity = None
    more: Quantity = None

    attributes: SubSection = None

    all_attributes: Quantity = None

    def __init__(self, *args, **kwargs):
        self._cached_count: int | None = None
        self._cached_hash: _HASH_OBJ | None = None

        # a definition could potentially be loaded from mongodb with an outdated hashing algorithm
        # thus the then definition ID shall be treated as a snapshot (revision) ID that may not comply
        # with the current hashing algorithm
        # when it is loaded from mongodb, the snapshot ID will be set
        # when it is set, we always return the snapshot ID as definition ID
        # regardless of the hashing algorithm
        self.snapshot_id = None

        if is_bootstrapping:
            super().__init__(*args, **kwargs)
            return

        # We add all kwargs that are not meta props, annotations, or metainfo properties
        # to the more property.
        more = {}
        new_kwargs = {}
        for key, value in kwargs.items():
            if key.startswith(('m_', 'a_')) or key in self.__class__.m_def.all_aliases:
                new_kwargs[key] = value
            else:
                more[key] = value

        super().__init__(*args, **new_kwargs)
        self.more = more  # type: ignore

    def __init_metainfo__(self):
        """
        An initialization method that is called after the class context of the definition
        has been initialized. For example, it is called on all quantities of a section
        class after the class was created. If metainfo definitions are created without
        a class context, this method must be called manually on all definitions.
        """
        for annotation in self.m_get_annotations(DefinitionAnnotation, as_list=True):
            annotation.init_annotation(self)

    def init_metainfo(self):
        """
        Calls __init_metainfo__ on all its children. This is necessary if the
        package, section was created without corresponding python classes
        packages, etc.
        """
        self.__init_metainfo__()
        for content in self.m_all_contents(depth_first=True):
            content.__init_metainfo__()

    def __getattr__(self, name):
        if self.more and name in self.more:
            return self.more[name]

        return super().__getattr__(name)

    def __repr__(self):
        return f'{self.qualified_name()}:{self.m_def.name}'

    @constraint(warning=False)
    def valid_name(self):
        # package names are not effectively used in reference system thus not checked
        # otherwise all names of definitions must be valid identifiers
        if isinstance(self, Package) or not self.name or self.name.isidentifier():
            return

        if all(x.isidentifier() for x in self.name.split('.')):
            return

        if not isinstance(self, Package) or not self.m_is_custom_package:
            raise AssertionError(f'Definition {self.name} is not valid.')

    def m_is_set(self, def_or_name: Property | str, *, hint: str | None = None) -> bool:
        definition = self._ensure_definition(def_or_name, hint=hint)

        if definition == Definition.more:
            return len(self.more) > 0

        return super().m_is_set(definition)

    def qualified_name(self):
        name = self.name if self.name else '*'
        if self.m_parent and self.m_parent.m_follows(Definition.m_def):
            return f'{self.m_parent.qualified_name()}.{name}'

        return name

    def on_set(self, quantity_def, value):
        if quantity_def == Definition.categories:
            for category in value:
                category.definitions.add(self)

    def m_to_dict(self, **kwargs) -> dict:  # type: ignore[override]
        value: dict = super().m_to_dict(**kwargs)

        if not kwargs.get('return_as_generator', False):
            if kwargs.get('with_def_id', False):
                value['definition_id'] = self.definition_id
            return value

        def nested():
            yield from value.items()
            if kwargs.get('with_def_id', False):
                yield 'definition_id', self.definition_id

        return streamable_dict(nested())

    def m_to_json_schema(
        self,
        add_unit_value=False,
        add_section_subtypes=False,
        add_property_subtypes=False,
        exclude=None,
    ) -> dict[str, Any]:
        """
        Generate JSON Schema for this Section, referencing each
        property (Quantity or SubSection) via `$defs`.
        """
        return metainfo_to_json_schema(
            m_def=self,
            add_unit_value=add_unit_value,
            add_section_subtypes=add_section_subtypes,
            add_property_subtypes=add_property_subtypes,
            exclude=exclude,
        )

    def _hash_seed(self) -> str:
        """
        Generates a unique representation for computing the hash of a definition.

        The order of aliases is not important.
        """
        seed: str = str(self.name)
        seed += 'T' if self.variable else 'F'
        if len(self.aliases) > 0:
            seed += ''.join([str(i) for i in sorted(self.aliases)])

        return seed

    def hash(self) -> _HASH_OBJ:
        """
        Generates a hash object based on the unique representation of the definition.
        """
        if self._cached_hash is None or self._cached_count != self.m_mod_count:
            self._cached_count = self.m_mod_count
            self._cached_hash = default_hash()
            self._cached_hash.update(self._hash_seed().encode('utf-8'))

            for item in self.attributes:
                if self is not item:
                    self._cached_hash.update(item.hash().digest())

        return self._cached_hash

    @property
    def definition_id(self) -> str:
        """
        Syntax sugar.

        Returns the hash digest.
        """
        return self.snapshot_id or self.hash().hexdigest()

    def definition_reference(self, source):
        """
        Creates a reference string that points to this definition from the
        given source section.
        """
        definition_reference = self.qualified_name()

        if definition_reference.startswith('entry_id:'):
            # This is not from a python module, use archive reference instead
            if context := self.m_root().m_context:
                relative_name = context.create_reference(source, None, self)
                if relative_name:
                    definition_reference = relative_name

        return definition_reference


class Attribute(Definition):
    """
    Attributes can be used to qualify all properties (subsections and quantities)
    with simple primitive values.

    Attributes:
        type: The type of the attribute. Needs to be a primitive type that is a subclass of `Datatype`.
        shape: The shape of the attribute. Need to be a list, similar to the shape of a quantity.
    """

    type: Quantity = None
    shape: Quantity = None

    @constraint(warning=False)
    def is_primitive(self):
        if not isinstance(self.type, Datatype):
            raise AssertionError(f'Attribute {self} must have primitive type.')

    def _hash_seed(self) -> str:
        return (
            super()._hash_seed()
            + json.dumps(QuantityType().serialize(self.type, section=self))
            + ''.join(str(x) for x in self.shape)
        )


class Property(Definition):
    """
    A common base-class for section properties: subsections and quantities.
    """

    def get_from_dict(
        self, data: dict[str, Any], default_value: Any = None
    ) -> tuple[str | None, Any]:
        """
        Attempts to read the property from a dict. Returns the used alias and value as
        tuple.
        """
        for name in itertools.chain([self.name], self.aliases):
            if name in data:
                return name, data[name]
        return None, default_value

    def get_base_property(self) -> Property | None:
        """
        Retrieve a potential overwritten property from a base-class.
        """
        assert self.m_parent and isinstance(self.m_parent, Section), (
            'Property must be property of a section.'
        )
        for base_section in self.m_parent_as(Section).all_base_sections.values():
            if base_property := base_section.all_properties.get(self.name):
                if base_property.m_def != self.m_def:
                    raise MetainfoError(
                        'Cannot overwrite a property of different metainfo type.'
                    )
                return base_property

        return None


class Quantity(Property):
    """
    To define quantities, instantiate :class:`Quantity` as a class attribute values in
    a `section classes`. The name of a quantity is automatically taken from its `section class`
    attribute. You can provide all other attributes to the constructor with keyword arguments

    See :ref:`metainfo-sections` to learn about `section classes`.
    In Python terms, ``Quantity`` is a descriptor. Descriptors define how to get and
    set attributes in a Python object. This allows us to use sections like regular
    Python objects and quantity like regular Python attributes.

    Each quantity must define a basic data type and a shape. The values of a quantity must
    fulfil the given type. The default shape is a single value. Quantities can also have
    physical units. Units are applied to all values.

    Attributes:
        type:
            Defines the datatype of quantity values. This is the type of individual elements
            in a potentially complex shape. If you define a list of integers for example,
            the `shape` would be list and the `type` integer:
            ``Quantity(type=int, shape=['0..*'])``.

            The `type` can be one of:

            - a build-in primitive Python type: ``int``, ``str``, ``bool``, ``float``
            - an instance of :class:`MEnum`, e.g. ``MEnum('one', 'two', 'three')``
            - a section to define references to other sections as quantity values
            - a custom meta-info :class:`Datatype`, see :ref:`metainfo-custom-types`
            - a numpy `dtype`, e.g. ``np.float32``
            - ``typing.Any`` to support any value

            If set to `dtype`, this quantity will use a numpy array or scalar to store values
            internally. If a regular (nested) Python list or Python scalar is given, it will
            be automatically converted. The given `dtype` will be used in the numpy value.

            To define a reference, either a `section class` or instance of :class:`Section`
            can be given. See :ref:`metainfo-sections` for details. Instances of the given section
            constitute valid values for this type. Upon serialization, references section
            instance will represent with metainfo URLs. See :ref:`metainfo-urls`.

            For quantities with more than one dimension, only numpy arrays and `dtypes`
            are allowed.

        shape:
            The shape of the quantity. It defines its dimensionality.

            A shape is a list, where each item defines one dimension.
            Each dimension can be:

            - an integer that defines the exact size of the dimension, e.g. ``[3]`` is the
              shape of a 3D spacial vector
            - a string that specifies a possible range, e.g. ``0..*``, ``1..*``, ``3..6``
            - the name of an int typed and shapeless quantity in the same section which
              values define the length of this dimension, e.g. ``number_of_atoms`` defines
              the length of ``atom_positions``

            Range specifications define lower and upper bounds for the possible dimension
            length. The ``*`` can be used to denote an arbitrarily high upper bound.

            Quantities with dimensionality (length of the shape) higher than 1, must be
            numpy arrays. Their type must be a `dtype`.

        is_scalar:
            Derived quantity that is True, if this quantity has shape of length 0

        unit:
            The physics unit for this quantity. It is optional.

            Units are represented with the Pint Python package. Pint defines units and
            their algebra. You can either use *pint* units directly, e.g. ``units.m / units.s``.
            The metainfo provides a preconfigured *pint* unit registry :py:data:`ureg`.
            You can also provide the unit as *pint* parsable string, e.g. ``'meter / seconds'`` or
            ``'m/s'``.

        dimensionality:
            The dimensionality of the quantity. It is optional.

            If set, it will be used to validate the compatibility between chosen unit and the target
            dimensionality.

        default:
            The default value for this quantity. The value must match type and shape.

            Be careful with a default value like ``[]`` as it will be the default value for
            all occurrences of this quantity.

    Quantities are mapped to Python properties on all section objects that instantiate
    the Python class/section definition that has this quantity. This means quantity values
    can be read and set like normal Python attributes.

    In some cases it might be desirable to have virtual and read only quantities that are
    not real quantities used for storing values, but rather define an interface to other
    quantities. Examples for this are `synonyms` and `derived` quantities.

    Attributes:
        derived:
            A Python callable that takes the containing section as input and outputs the
            value for this quantity. This quantity cannot be set directly, its value
            is only derived by the given callable. The callable is executed when this
            quantity is get. Derived quantities are always virtual.

        cached:
            A bool indicating that derived values should be cached unless the underlying
            section has changed.

        virtual:
            A boolean that determines if this quantity is virtual. Virtual quantities can
            be got/set like regular quantities, but their values are not (de-)serialized,
            hence never permanently stored.

        flexible_unit:
            A boolean to indicate if this quantity may have a unit that is not the default unit.
            In this case, the quantity will be stored in full storage mode as a `MQuantity`.

        use_full_storage:
            A derived boolean that indicates if this quantity should be stored in full storage mode.
            It will be set to True if `flexible_unit` is True, or `variable` is True, or it has attributes.
    """

    type: Quantity = None
    shape: Quantity = None
    unit: Quantity = None
    dimensionality: Quantity = None
    default: Quantity = None
    derived: Quantity = None
    cached: Quantity = None
    virtual: Quantity = None

    is_scalar: Quantity = None
    use_full_storage: Quantity = None
    flexible_unit: Quantity = None

    # TODO derived_from = Quantity(type=Quantity, shape=['0..*'])
    def __init_metainfo__(self):
        super().__init_metainfo__()

        if self.derived is not None:
            self.virtual = True

    def __get__(self, obj, cls=None, **kwargs):
        if obj is None:
            return self

        if self.name in obj.__dict__:
            value = obj.__dict__[self.name]
            actual_name = kwargs.get('actual_name', self.name)
            # appears to be a quantity using full storage
            # cannot use .use_full_storage as this is not set yet
            if isinstance(value, dict) and actual_name in value:
                if kwargs.get('full', False):  # full storage requested
                    return value[actual_name]
                value = value[actual_name].get()
        elif self.derived is not None:
            try:
                if not self.cached:
                    return self.derived(obj)

                cached = obj.__dict__.setdefault(f'_cached_{self.name}', [-1, None])
                if cached[0] != obj.m_mod_count:
                    cached[0] = obj.m_mod_count
                    cached[1] = self.derived(obj)
                return cached[1]
            except Exception as e:
                raise DeriveError(f'Could not derive value for {self}: {str(e)}')
        elif isinstance(self.default, dict | list):
            value = self.default.copy()
        else:
            value = self.default

        if value is None:
            return value

        if isinstance(self.type, QuantityReference):

            def _unwrap(_v):
                if isinstance(_v, list):
                    return [_unwrap(x) for x in _v]

                return getattr(_v, self.type.target_quantity_def.name)

            value = _unwrap(value)

        # no need to append unit if it is already a quantity from full storage
        if isinstance(value, units.Quantity):
            return value

        # value is still the raw {name: MQuantity} dict when the caller didn't
        # request one specific stored instance (no actual_name kwarg) -- there
        # is no single magnitude here to attach a unit to.
        if (
            self.unit is not None
            and isinstance(self.type, Number)
            and not isinstance(value, dict)
        ):
            # Note that we cannot do simple multiplication here as Pint units
            # with offsets do not support multiplication:
            # https://pint.readthedocs.io/en/stable/user/nonmult.html
            return units.Quantity(value, self.unit)

        return value

    @metainfo_setter
    def __set__(self, obj, value, **kwargs):
        if self.derived is not None:
            raise MetainfoError(f'The quantity {self} is derived and cannot be set.')

        item_name: str = self.name

        if value is None:
            if kwargs.get('force_none', False):
                # todo: this is due to the bug in editing which set None as label to remove it in indexing
                # this should be removed when the bug is fixed
                obj.__dict__[item_name] = value
            else:
                # This implements the implicit "unset" semantics of assigned None as a value
                to_remove: dict | None = obj.__dict__.pop(item_name, None)
                # if full storage is used, also need to clear quantities created for convenient access
                if self.use_full_storage and to_remove:
                    # self.__dict__[full_name] is guaranteed to be a 'dict[str, MQuantity]'
                    for key in to_remove.keys():
                        obj.__dict__.pop(key, None)
            if not (
                kwargs.get('treat_none_as_nan', False)
                and isinstance(self.type, InexactNumber)
            ):
                return

        if not self.use_full_storage:
            value = self.type.normalize(value, section=obj, **kwargs)
            if isinstance(self.type, Reference):
                if value == _UNSET_:
                    return

                if isinstance(value, list):
                    value = [v for v in value if v != _UNSET_]

            obj.__dict__[item_name] = value
        else:
            if isinstance(value, dict):
                for k, v in value.items():
                    self.__set__(obj, MQuantity.from_dict(k, v))
                return

            # it is a repeating quantity w/o attributes
            # the actual value/name/unit would be wrapped into 'MQuantity'
            # check if there is an existing item
            m_quantity: MQuantity
            m_attribute: dict = {}
            if isinstance(value, MQuantity):
                m_quantity = value
                if not self.variable:
                    if not m_quantity.name:
                        m_quantity.name = item_name
                    elif m_quantity.name != item_name:
                        raise MetainfoError(
                            f'The name of {value} must match definition name {item_name}.'
                        )
                elif not m_quantity.name:
                    raise MetainfoError(
                        f'The name must be provided for variadic quantity {item_name}.'
                    )

                # swap to add attributes via the setter to allow validation
                m_attribute = m_quantity.attributes
                m_quantity.attributes = {}
            elif not self.variable:
                try:
                    m_quantity = obj.__dict__[item_name][item_name]
                    if isinstance(value, pint.Quantity):
                        m_quantity.value = value.m
                        m_quantity.unit = value.u
                    else:
                        m_quantity.value = value
                except KeyError:
                    m_quantity = MQuantity(item_name, value)
            else:
                raise MetainfoError(
                    "Variadic quantities only accept raw values wrapped in 'MQuantity'."
                )

            m_quantity.value = self.type.normalize(
                m_quantity.value, section=obj, **kwargs
            )

            if self.unit is None:
                # no prescribed unit, need to check dimensionality, no need to convert
                check_dimensionality(self, m_quantity.unit)
            else:
                try:
                    m_quantity.value = convert_to(
                        m_quantity.value, m_quantity.unit, self.unit
                    )
                except (ValueError, TypeError):
                    raise MetainfoError(
                        f'Could not convert {m_quantity.unit} to {self.unit}'
                    )
                m_quantity.unit = self.unit

            # store under variable name with suffix
            obj.__dict__.setdefault(item_name, {})[m_quantity.name] = m_quantity

            for k, v in m_attribute.items():
                obj.m_set_quantity_attribute(m_quantity.name, k, v)

        for handler in obj.m_def.event_handlers:
            if handler.__name__.startswith('on_set'):
                handler(obj, self, value)

    def __delete__(self, obj):
        if obj is None:
            # class (def) case
            raise KeyError(
                'Cannot delete quantity definition. Only values can be deleted.'
            )

        # object (instance) case
        raise NotImplementedError('Deleting quantity values is not supported.')

    @constraint(warning=False)
    def has_type(self):
        if self.type is None:
            raise AssertionError(f'The quantity {self} must define a type.')

        if isinstance(self.type, Reference):
            try:
                self.type.target_section_def.m_resolved()
            except MetainfoReferenceError as e:
                raise AssertionError(f'Cannot resolve {self}: {str(e)}')

    @constraint(warning=False)
    def correct_dimensionality(self):
        check_dimensionality(self, self.unit)

    @constraint(warning=True)
    def dimensions(self):
        for dimension in self.shape:
            if not isinstance(dimension, str) or not dimension.isidentifier():
                continue

            dim_quantity = self.m_parent.all_quantities.get(dimension, None)

            assert dim_quantity is not None, (
                f'Dimensions ({dimension}) must be quantities of the same section ({self.m_parent}).'
            )

            assert (
                isinstance(dim_quantity.type, ExactNumber)
                and len(dim_quantity.shape) == 0
            ), (
                f'Dimensions ({dimension}) must be shapeless ({dim_quantity.shape}) '
                f'and int ({dim_quantity.type}) typed.'
            )

    def _hash_seed(self) -> str:
        """
        Generate a unique representation for this quantity.

        The custom type MUST have the method `_hash_seed()` to be used to generate a proper id.

        Returns:
            str: The generated unique representation.
        """
        if isinstance(self.type, Reference):
            reference_seed = self.type.target_definition_id
        else:
            reference_seed = json.dumps(
                QuantityType().serialize(self.type, section=self)
            )

        return (
            super()._hash_seed()
            + reference_seed
            + ''.join(str(x) for x in self.shape)
            + f'{str(self.unit)}{"N" if self.default is None else str(self.default)}'
            + str(self.dimensionality)
            + ('T' if self.virtual else 'F')
        )

    def m_to_json_schema(
        self,
        add_unit_value=False,
        add_section_subtypes=False,
        add_property_subtypes=False,
        exclude=None,
    ) -> dict[str, Any]:
        """
        Generate a JSON Schema (Draft 2020-12) for this Quantity.
        """
        return quantity_to_json_schema(
            self,
            add_unit_value=add_unit_value,
            add_property_subtypes=add_property_subtypes,
        )


class DirectQuantity(Quantity):
    """Used for quantities that would cause indefinite loops due to bootstrapping."""

    __slots__ = '_name', '_default'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._name = kwargs.get('name')
        self._default = kwargs.get('default')

    def __get__(self, obj, cls=None, **kwargs):
        if obj is None:
            return self

        return obj.__dict__.get(self._name, self._default)

    @metainfo_setter
    def __set__(self, obj, value, **kwargs):
        if value is None:
            obj.__dict__.pop(self._name, None)
        else:
            if self._name == 'type':
                value = QuantityType().normalize(value, section=obj)

            obj.__dict__[self._name] = value


class SubSection(Property):
    """
    Like quantities, subsections are defined in a `section class` as attributes
    of this class. Unlike quantities, each subsection definition becomes a property of
    the corresponding `section definition` (parent). A subsection definition references
    another `section definition` as the subsection (child). As a consequence, parent
    `section instances` can contain child `section instances` as subsections.

    Contrary to the old NOMAD metainfo, we distinguish between subsection the section
    and subsection the property. This allows to use on child `section definition` as
    subsection of many parent `section definitions`.

    Attributes:
        sub_section: A :class:`Section` or Python class object for a `section class`. This
            will be the child `section definition`. The defining section the child
            `section definition`.

        repeats: A boolean that determines whether this subsection can appear multiple
            times in the parent section.
    """

    used_sections: dict[Section, list[SubSection]] = {}

    sub_section: Quantity = None
    repeats: Quantity = None
    key_quantity: Quantity = None

    def __get__(self, obj, cls=None):
        if obj is None:
            return self

        if self.repeats:
            return obj.__dict__.setdefault(self.name, MSubSectionList(obj, self))

        return obj.__dict__.get(self.name, None)

    @metainfo_setter
    def __set__(self, obj, value, **kwargs):
        """
        Accepts a single section or a list of sections.
        Accepts None to unset the subsection.
        """
        existing: MSection | MSubSectionList | None = self.__get__(obj)
        if self.repeats:
            if value is not None and not isinstance(value, list | set):
                raise TypeError(
                    'Cannot set a repeating subsection directly, modify the list, e.g. via append.'
                )
            existing = cast(MSubSectionList, existing)
            existing.clear()
            if isinstance(value, list | set):
                existing.extend(value)
        else:
            if existing is value:
                return
            obj._on_remove_sub_section(self, existing)
            if value is None:
                obj.__dict__.pop(self.name, None)
            else:
                obj.__dict__[self.name] = value
                obj._on_add_sub_section(self, value, -1)

    def __delete__(self, obj):
        raise NotImplementedError('Deleting subsections is not supported.')

    @constraint(warning=False)
    def has_sub_section(self):
        assert self.sub_section is not None, (
            'Each subsection must define the section that is used as subsection via the "sub_section" quantity'
        )
        try:
            assert not isinstance(self.sub_section.m_resolved(), MProxy), (
                'Cannot resolve "sub_section"'
            )
        except MetainfoReferenceError as e:
            raise AssertionError(f'Cannot resolve "sub_section": {str(e)}')

    def hash(self) -> _HASH_OBJ:
        if self._cached_hash is None or self._cached_count != self.m_mod_count:
            self._cached_hash = super().hash()
            self._cached_hash.update(('T' if self.repeats else 'F').encode('utf-8'))
            self._cached_hash.update(self.sub_section.hash().digest())

        return self._cached_hash


class Section(Definition):
    """
    Instances of the class :class:`Section` are created by writing Python classes
    that extend :class:`MSection` like this:

    .. code-block:: python

        class SectionName(BaseSection):
            \'\'\' Section description \'\'\'
            m_def = Section(**section_attributes)

            quantity_name = Quantity(**quantity_attributes)
            sub_section_name = SubSection(**sub_section_attributes)

    We call such classes *section classes*. They are not the *section definition*, but just
    representation of it in Python syntax. The *section definition* (in instance of :class:`Section`)
    will be created for each of these classes and stored in the ``m_def`` property. See
    :ref:`metainfo-reflection` for more details.

    Most of the attributes for a :class:`Section` instance will be set automatically from
    the section class:

    Attributes:
        quantities:
            The quantities definitions of this section definition as list of :class:`Quantity`.
            Will be automatically set from the `section class`.

        sub_sections:
            The subsection definitions of this section definition as list of :class:`SubSection`.
            Will be automatically set from the `section class`.

        base_sections:
            A list of `section definitions` (:class:`Section`). By default this definition will
            inherit all quantity and subsection definitions from the given section definitions.
            This behavior might be altered with ``extends_base_section``.

            If there are no base sections to define, you have to use :class:`MSection`.

    The Metainfo supports two inheritance mechanism. By default, it behaves like regular
    Python inheritance and the class inherits all its base
    classes' properties. The other mode (enabled via ``extends_base_section=True``), will
    add all subclass properties to the base-class. This is used throughout the NOMAD metainfo
    to add code-specific metadata to common section definitions. Here is an example:

    .. code-block:: python

        class Method(MSection):
            code_name = Quantity(str)

        class VASPMethod(Method):
            m_def = Section(extends_base_section=True)
            x_vasp_some_incar_parameter = Quantity(str)

        method = Method()
        method.x_vasp_same_incar_parameter = 'value'

    In this example, the section class ``VASPMethod`` defines a section definition that inherits
    from section definition ``Method``. The quantity `x_vasp_some_incar_parameter` will
    be added to `Method` and can be used in regular `Method` instances.

    The following :class:`Section` attributes manipulate the inheritance semantics:

    Attributes:
        extends_base_section:
            If True, this definition must have exactly one ``base_sections``.
            Instead of inheriting properties, the quantity and subsection definitions
            of this section will be added to the base section.

            This allows to add further properties to an existing section definition.
            To use such extension on section instances in a type-safe manner
            :py:func:`MSection.m_as` can be used to cast the base section to the extending
            section.

        extending_sections:
            A list of `section definitions` (:class:`Section`). These are those sections
            that add their properties to this section via :attr:`extends_base_section`.
            This quantity will be set automatically.

        inheriting_sections:
            A list of `section definitions` (:class:`Section`). These are those sections
            that inherit (i.e. are subclasses) of this section.


    Besides defining quantities and subsections, a section definition can also provide
    constraints that are used to validate a section and its quantities and subsections.
    Constraints allow to define more specific data structures beyond types and shapes.
    But constraints are not enforced automatically, sections have to be explicitly
    validated in order to evaluate constraints.

    Constrains can be defined via methods with the ``constraint`` decorator:

    .. code-block:: python

        class System(MSection):
            lattice = Quantity(float, shape=[3, 3], unit='meter')

            @constraint
            def non_empty_lattice(self):
                assert np.abs(np.linalg.det(self.lattice.magnitude)) > 0

        system = System()
        system.m_validate()

    Attributes:
        constraints:
            Constraints are rules that a section must fulfil to be valid. This allows to implement
            semantic checks that go behind mere type or shape checks. This quantity takes
            the names of constraints as string. Constraints have to be implemented as methods
            with the :func:`constraint` decorator. They can raise :class:`ConstraintViolated`
            or an AssertionError to indicate that the constraint is not fulfilled for the ``self``
            section. This quantity will be set automatically from all constraint methods in the
            respective section class. To run validation of a section use :py:meth:`MSection.m_validate`.

    Other attributes and helper properties:

    Attributes:
        inherited_sections:
            A helper attribute that gives direct and indirect base sections and extending
            sections including this section. These are all sections that this sections
            gets its properties from.

        all_base_sections:
            A helper attribute that gives direct and indirect base sections.

        all_inheriting_sections:
            A helper attribute that gives direct and indirect inheriting sections.

        all_properties:
            A helper attribute that gives all properties (subsection and quantity) definitions
            including inherited properties and properties from extending sections as a
            dictionary with names and definitions.

        all_quantities:
            A helper attribute that gives all quantity definition including inherited ones
            and ones from extending sections as a dictionary that maps names (strings)
            to :class:`Quantity`.

        all_sub_sections:
            A helper attribute that gives all subsection definition including inherited ones
            and ones from extending sections as a dictionary that maps names (strings)
            to :class:`SubSection`.

        all_sub_sections_by_section:
            A helper attribute that gives all subsection definition including inherited ones
            and ones from extending sections as a dictionary that maps section classes
            (i.e. Python class objects) to lists of :class:`SubSection`.

        all_aliases:
            A helper attribute that gives all aliases for all properties including
            inherited properties and properties form extending sections as a
            dictionary with aliases and the definitions.

        all_inner_section_definitions:
            A helper attribute that gives all inner_section_definitions including
            their aliases by name.

        path: Shortest path from a root section to this section. This is not the path
            in the metainfo schema (`m_path`) but an archive path in potential data.

        event_handlers:
            Event handler are functions that get called when the section data is changed.
            There are two types of events: ``set`` and ``add_sub_section``. The handler type
            is determined by the handler (i.e. function) name: ``on_set`` and ``on_add_sub_section``.
            The handler arguments correspond to :py:meth:`MSection.m_set` (section, quantity_def, value) and
            :py:meth:`MSection.m_add_sub_section` (section, sub_section_def, sub_section).
            Handler are called after the respective action was performed. This quantity is
            automatically populated with handler from the section classes methods. If there
            is a method ``on_set`` or ``on_add_sub_section``, it will be added as handler.
    """

    quantities: SubSection = None
    sub_sections: SubSection = None
    inner_section_definitions: SubSection = None

    base_sections: Quantity = None
    extending_sections: Quantity = None
    extends_base_section: Quantity = None
    inheriting_sections: Quantity = None
    constraints: Quantity = None
    event_handlers: Quantity = None

    inherited_sections: Quantity = None
    all_base_sections: Quantity = None
    all_inheriting_sections: Quantity = None
    all_properties: Quantity = None
    all_quantities: Quantity = None
    all_sub_sections: Quantity = None
    all_sub_sections_by_section: Quantity = None
    all_aliases: Quantity = None
    all_inner_section_definitions: Quantity = None
    has_variable_names: Quantity = None
    path: Quantity = None

    def __init__(self, *args, validate: bool = True, **kwargs):
        self._section_cls: type[MSection] | None = None

        super().__init__(*args, **kwargs)
        self.validate = validate

    def m_validate(self) -> tuple[list[str], list[str]]:
        if not self.validate:
            return [], []

        return super().m_validate()

    @property
    def section_cls(self) -> type[MSection]:
        """
        A helper attribute that gives the `section class` as a Python class object.
        """
        if self._section_cls is None:
            # set a temporary to avoid endless recursion
            self._section_cls = type(self.name, (MSection,), dict(do_init=False))

            # Create a section class if this does not exist. This happens if the section
            # is not created through a class definition.
            attrs = {
                prop.name: prop
                for prop in itertools.chain(self.quantities, self.sub_sections)
            }

            for name, inner_section_def in self.all_inner_section_definitions.items():
                attrs[name] = inner_section_def.section_cls

            attrs.update(m_def=self, do_init=False)
            bases = (
                tuple(base_section.section_cls for base_section in self.base_sections)
                if len(self.base_sections) > 0
                else (MSection,)
            )
            self._section_cls = type(self.name, bases, attrs)

        return self._section_cls

    def __init_metainfo__(self):
        super().__init_metainfo__()

        # Regular inheritance: register as a normal child
        if not self.extends_base_section:
            for base_section in self.base_sections:
                if self not in base_section.inheriting_sections:
                    base_section.inheriting_sections += [self]  # cannot use append here

        # Extension: register as an extender of its single base section
        elif len(self.base_sections) == 1:
            base_section = self.base_sections[0]
            for name, attr in self.section_cls.__dict__.items():
                if isinstance(attr, Property):
                    setattr(base_section.section_cls, name, attr)
            if self not in base_section.extending_sections:
                base_section.extending_sections += [self]  # cannot use append here

        else:
            raise MetainfoError(
                f'Section {self} extend the base section, but has no or more than one base section.'
            )

        # Transfer properties of inherited and overwritten property definitions that
        # have not been overwritten
        base_props: dict[str, Property] = dict()
        for base_section in self.all_base_sections:
            base_props.update(**base_section.all_properties)

        def _common_base(_derived, _base):
            _derived_mro = _derived.__class__.mro()
            _base_mro = set(_base.__class__.mro())
            return next((_item for _item in _derived_mro if _item in _base_mro), None)

        for derived_prop in itertools.chain(self.quantities, self.sub_sections):
            if (base_prop := base_props.get(derived_prop.name)) is None:
                continue

            # ensure both properties are of the same type
            if _common_base(derived_prop, base_prop) in (None, Property):
                raise MetainfoError('Cannot inherit from different property types.')

            for item in derived_prop.m_def.all_quantities.values():
                if not derived_prop.m_is_set(item) and base_prop.m_is_set(item):
                    derived_prop.m_set(item, base_prop.m_get(item))

    def get_attribute(self, definition, attr_name: str) -> tuple[Definition, Attribute]:
        """
        Resolve the attribute within this section definition based on parent definition
        and attribute name. The definition can be None for section attributes, a
        quantity definition, or the name of a variadically named quantity.
        In the case of variadic/template name, the name is also resolved by checking naming pattern.
        """

        # find the section or quantity where attribute is defined
        tgt_def: Definition | None = None
        if definition is None:
            tgt_def = self
        elif isinstance(definition, Definition):
            tgt_def = definition
        else:
            try:
                tgt_def = resolve_variadic_name(self.all_properties, definition)
            except ValueError:
                pass

        if tgt_def is None:
            raise ValueError(
                f'Cannot find the definition by the given name {definition}'
            )

        # find the corresponding attribute
        try:
            tgt_attr = resolve_variadic_name(tgt_def.all_attributes, attr_name)
        except ValueError:
            tgt_attr = None

        if tgt_attr is not None:
            return tgt_def, tgt_attr

        raise ValueError(
            f'The attribute name {attr_name} is not found in the given property.'
        )

    @constraint
    def unique_names(self):
        names: set[str] = set()
        for base in self.extending_sections:
            for quantity in itertools.chain(base.quantities, base.sub_sections):
                for alias in quantity.aliases:
                    names.add(alias)
                names.add(quantity.name)

        for definition in itertools.chain(self.quantities, self.sub_sections):
            assert definition.name not in names, (
                f'All names in a section must be unique. '
                f'Name {definition.name} of {definition} in {definition.m_parent} already exists in {self}.'
            )
            names.add(definition.name)
            for alias in definition.aliases:
                assert alias not in names, (
                    f'All names (incl. aliases) in a section must be unique. '
                    f'Alias {alias} of {definition} in {definition.m_parent} already exists in {self}.'
                )
                names.add(alias)

    @constraint
    def resolved_base_sections(self):
        for base_section in self.base_sections:
            try:
                base_section.m_resolved()
            except MetainfoReferenceError as e:
                raise AssertionError(f'Cannot resolve base_section: {str(e)}')

    @classmethod
    def m_from_dict(cls, data: dict[str, Any], **kwargs):
        for list_quantity in [
            Section.quantities,
            Section.sub_sections,
            Section.inner_section_definitions,
        ]:
            alias, potential_dict_value = list_quantity.get_from_dict(data)
            if alias:
                data[alias] = dict_to_named_list(potential_dict_value)

        _, sub_sections = Section.sub_sections.get_from_dict(data, [])
        for sub_section in sub_sections:
            alias, section_def = SubSection.sub_section.get_from_dict(sub_section)
            if not section_def or isinstance(section_def, str):
                continue

            (
                inner_sections_alias,
                inner_sections,
            ) = Section.inner_section_definitions.get_from_dict(data)
            if not inner_sections_alias:
                inner_sections = []
                data['inner_section_definitions'] = inner_sections

            inner_sections.append(section_def)
            name = sub_section.get('name')
            if name:
                name = name.title().replace('_', '')
            section_def['name'] = name
            sub_section[alias] = name

        if 'base_section' in data:
            data['base_sections'] = [data.pop('base_section')]

        return super().m_from_dict(data, **kwargs)

    def hash(self) -> _HASH_OBJ:
        if self._cached_hash is not None and self._cached_count == self.m_mod_count:
            return self._cached_hash

        self._cached_hash = super().hash()
        self._cached_hash.update(self.m_path(package_path=True).encode('utf-8'))
        self._cached_hash.update(
            ('T' if self.extends_base_section else 'F').encode('utf-8')
        )

        for item in itertools.chain(
            self.quantities,
            self.sub_sections,
            self.base_sections,
            self.inner_section_definitions,
        ):
            if self is not item:
                self._cached_hash.update(item.hash().digest())

        return self._cached_hash


class Package(Definition):
    """Packages organize metainfo definitions alongside Python modules

    Each Python module with metainfo Definition (explicitly or implicitly) has a member
    ``m_package`` with an instance of this class. Definitions (categories, sections) in
    Python modules are automatically added to the module's :class:`Package`.
    Packages are not nested and rather have the fully qualified Python module name as
    name.

    This allows to inspect all definitions in a Python module and automatically puts
    module name and docstring as :class:`Package` name and description.

    Besides the regular :class:`Definition` attributes, packages can have the following
    attributes:

    Attributes:
        section_definitions: All `section definitions` in this package as :class:`Section`
            objects.

        category_definitions: All `category definitions` in this package as :class:`Category`
            objects.

        all_definitions: A helper attribute that provides all section and category definitions
            by name and aliases.

        errors:
            A list of errors. These issues prevent the section definition from being usable.

        warnings:
            A list of warnings. These still allow to use the section definition.
    """

    section_definitions: SubSection = None
    category_definitions: SubSection = None

    all_definitions: Quantity = None
    dependencies: Quantity = None

    registry: dict[str, Package] = {}
    """ A static member that holds all currently known packages. """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.errors, self.warnings = [], []
        self.upload_id = None
        self.entry_id = None

        # used to indicate if a package is loaded from mongodb
        # if so skip writing back to mongodb to save some resources
        self.loaded_from_mongodb = False

    @property
    def m_is_custom_package(self) -> bool:
        """Whether this package is backed by an uploaded schema entry."""
        return self.upload_id is not None and self.entry_id is not None

    def __init_metainfo__(self):
        super().__init_metainfo__()

        # register the package and all its aliases for python packages
        if (
            not self.m_is_custom_package
            and self.name
            and re.match(r'^\w+(\.\w+)*$', self.name)
            and Package.registry.get(self.name, None) is not self
        ):
            for alias in itertools.chain(self.aliases, [self.name]):
                if alias in Package.registry and 'pytest' not in sys.modules:
                    raise MetainfoError(
                        f'Package {alias} is already registered for package {Package.registry[alias]}.'
                    )
                Package.registry[alias] = self

        # access potential SectionProxies to resolve them
        for content in self.m_all_contents():
            if isinstance(content, SubSection):
                SubSection.used_sections.setdefault(
                    content.sub_section.m_resolved(), []
                ).append(content)
            elif isinstance(content, Section):
                for base_section in content.base_sections:
                    base_section.m_resolved()

        # validate
        if is_bootstrapping:
            return

        self.errors, self.warnings = self.m_all_validate()
        if len(self.errors) > 0:
            raise MetainfoError(
                f'One constraint was violated: {str(self.errors[0]).strip(".")} '
                f'(there are {len(self.errors) - 1:d} more violations)'
            )

    @staticmethod
    def from_module(module_name: str):
        module = sys.modules[module_name]

        pkg: Package = getattr(module, 'm_package', None)
        if pkg is None:
            pkg = Package()
            setattr(module, 'm_package', pkg)

        pkg.name = module_name
        if pkg.description is None and module.__doc__ is not None:
            pkg.description = inspect.cleandoc(module.__doc__).strip()

        return pkg

    @classmethod
    def m_from_dict(cls, data: dict[str, Any], **kwargs):
        for list_quantity in [
            Package.section_definitions,
            Package.category_definitions,
        ]:
            alias, potential_dict_value = list_quantity.get_from_dict(data)
            if alias:
                data[alias] = dict_to_named_list(potential_dict_value)

        return super().m_from_dict(data, **kwargs)

    def qualified_name(self):
        if self.m_is_custom_package:
            # if entry_id is set, it is coming from custom definition stored in archive or mongo
            # check m_parent as well as the above condition may not be met in tests
            return f'entry_id:{self.entry_id or "*"}'

        return super().qualified_name()

    def m_resolve_path(self, path_stack: list):
        if len(path_stack) == 0:
            return self

        path_str = '/'.join(path_stack)
        current_pos = path_stack.pop(0)
        section = self.all_definitions.get(current_pos, None)
        try:
            while len(path_stack) > 0:
                current_pos = path_stack.pop(0)
                section = section.all_properties.get(current_pos, None)
        except Exception:  # noqa
            section = None

        if section is not None:
            return section

        return self.m_resolve(path_str)

    def normalize(self, archive, logger=None):
        if archive.definitions == self and archive.metadata:
            if archive.metadata.entry_name is None and self.name and self.name != '*':
                archive.metadata.entry_name = self.name

    def hash(self) -> _HASH_OBJ:
        if self._cached_hash is None or self._cached_count != self.m_mod_count:
            self._cached_hash = super().hash()

            for item in self.section_definitions:  # pylint: disable=not-an-iterable
                if self is not item:
                    self._cached_hash.update(item.hash().digest())

        return self._cached_hash


class Category(Definition):
    """Categories allow to organize metainfo definitions (not metainfo data like sections do)

    Each definition, including categories themselves, can belong to a set of categories.
    Categories therefore form a hierarchy of concepts that definitions can belong to, i.e.
    they form a `is a` relationship.

    Attributes:
        definitions: A helper attribute that gives all definitions that are directly or
            indirectly in this category.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.definitions: set[Definition] = set()

    def get_all_definitions(
        self, definitions: set[Definition] | None = None
    ) -> set[Definition]:
        """
        Helper method that collects all non category definitions, including those
        in categories of this category.
        """
        if definitions is None:
            definitions = set()

        for definition in self.definitions:
            if isinstance(definition, MCategory):
                definition = definition.m_def

            if isinstance(definition, Category):
                definition.get_all_definitions(definitions)

            if definition not in definitions:
                definitions.add(definition)

        return definitions


Section.m_def = Section(name='Section')
Section.m_def.m_def = Section.m_def
Section.m_def._section_cls = Section

Definition.m_def = Section(name='Definition')
Attribute.m_def = Section(name='Attribute')
Property.m_def = Section(name='Property')
Quantity.m_def = Section(name='Quantity')
SubSection.m_def = Section(name='SubSection')
Category.m_def = Section(name='Category')
Package.m_def = Section(name='Package')

Attribute.type = DirectQuantity(type=QuantityType, name='type')
Attribute.shape = DirectQuantity(
    type=Dimension, shape=['0..*'], name='shape', default=[]
)

Definition.name = DirectQuantity(type=str, name='name')
Definition.label = DirectQuantity(type=str, name='label')
Definition.description = Quantity(type=str, name='description')
Definition.links = Quantity(type=str, shape=['0..*'], name='links')
Definition.categories = Quantity(
    type=Reference(Category.m_def), shape=['0..*'], default=[], name='categories'
)
Definition.deprecated = Quantity(type=str, name='deprecated')
Definition.aliases = Quantity(type=str, shape=['0..*'], default=[], name='aliases')
Definition.variable = Quantity(type=bool, name='variable', default=False)
Definition.more = Quantity(type=JSON, name='more', default={})
Definition.attributes = SubSection(
    sub_section=Attribute.m_def, name='attributes', repeats=True
)


def derived(**kwargs):
    def decorator(f) -> Quantity:
        kwargs.setdefault('name', f.__name__)
        kwargs.setdefault(
            'description', f.__doc__.strip() if f.__doc__ is not None else None
        )
        kwargs.setdefault('type', AnyType())

        return Quantity(derived=f, **kwargs)

    return decorator


# virtual=True has to be set manually, due to bootstrapping hen-egg problems
@derived(cached=True, virtual=True)
def all_attributes(self: Section | Property) -> dict[str, Attribute]:
    if isinstance(self, Section):
        return {
            definition.name: definition
            for section in self.inherited_sections
            for definition in section.attributes
        }

    result: dict[str, Attribute] = {}
    for section in self.m_parent.inherited_sections:
        if (target := section.all_properties.get(self.name)) is not None:
            result.update(
                {definition.name: definition for definition in target.attributes}
            )
    return result


Definition.all_attributes = all_attributes

Section.quantities = SubSection(
    sub_section=Quantity.m_def, name='quantities', repeats=True
)
Section.sub_sections = SubSection(
    sub_section=SubSection.m_def, name='sub_sections', repeats=True
)
Section.inner_section_definitions = SubSection(
    sub_section=Section.m_def,
    name='inner_section_definitions',
    repeats=True,
    aliases=['inner_section_defs', 'section_defs', 'inner_sections', 'sections'],
)

Section.base_sections = Quantity(
    type=MSectionReference(), shape=['0..*'], default=[], name='base_sections'
)
Section.extending_sections = Quantity(
    type=MSectionReference(), shape=['0..*'], default=[], name='extending_sections'
)
Section.extends_base_section = Quantity(
    type=bool, default=False, name='extends_base_section'
)
Section.inheriting_sections = Quantity(
    type=MSectionReference(),
    shape=['0..*'],
    default=[],
    name='inheriting_sections',
    virtual=True,
)
Section.constraints = Quantity(type=str, shape=['0..*'], default=[], name='constraints')
Section.event_handlers = Quantity(
    type=Callable, shape=['0..*'], name='event_handlers', virtual=True, default=[]
)


@derived(cached=True)
def inherited_sections(self) -> list[Section]:
    result: list[Section] = []
    result.extend(self.all_base_sections)
    result.extend(self.extending_sections)
    result.append(self)  # self needs to come last for inheritance order
    return result


@derived(cached=False)
def all_base_sections(self) -> list[Section]:
    result: list[Section] = []
    for base_section in self.base_sections:
        if isinstance(base_section, SectionProxy):
            # In some reference resolution contexts, it is important to reevaluate later
            _track_changes(self)
            continue
        for base_base_section in base_section.all_base_sections:
            if isinstance(base_base_section, SectionProxy):
                # In some reference resolution contexts, it is important to reevaluate later
                _track_changes(self)
                continue
            result.append(base_base_section)
        result.append(base_section)
    return result


@derived(cached=True)
def all_inheriting_sections(self) -> list[Section]:
    # dict as ordered set: a plain set iterates in identity-hash order, which
    # varies between processes and makes polymorphic section resolution
    # (and thus parser output ordering) non-deterministic
    result: dict[Section, None] = {}
    for inheriting_section in self.inheriting_sections:
        if isinstance(inheriting_section, SectionProxy):
            # In some reference resolution contexts, it is important to reevaluate later
            _track_changes(self)
            continue
        for inheriting_inheriting_section in inheriting_section.all_inheriting_sections:
            if isinstance(inheriting_inheriting_section, SectionProxy):
                # In some reference resolution contexts, it is important to reevaluate later
                _track_changes(self)
                continue
            result[inheriting_inheriting_section] = None
        result[inheriting_section] = None
    return list(result)


@derived(cached=True)
def all_properties(self) -> dict[str, Property]:
    return self.all_quantities | self.all_sub_sections


@derived(cached=True)
def all_quantities(self) -> dict[str, Quantity]:
    return {
        definition.name: definition
        for section in self.inherited_sections
        for definition in section.quantities
    }


@derived(cached=True)
def all_sub_sections(self) -> dict[str, SubSection]:
    return {
        definition.name: definition
        for section in self.inherited_sections
        for definition in section.sub_sections
    }


@derived(cached=True)
def all_sub_sections_by_section(self) -> dict[Section, list[SubSection]]:
    result: dict[Section, list[SubSection]] = dict()
    for definition in self.all_sub_sections.values():
        result.setdefault(definition.sub_section.m_resolved(), []).append(definition)
    return result


@derived(cached=True)
def all_aliases(self) -> dict[str, Property]:
    result: dict[str, Property] = dict()
    for definition in self.all_properties.values():
        result.update({alias: definition for alias in definition.aliases})
        result[definition.name] = definition
    return result


@derived(cached=True)
def all_inner_section_definitions(self) -> dict[str, Section]:
    result: dict[str, Section] = dict()
    for base_section_or_self in itertools.chain(self.all_base_sections, [self]):
        for section in base_section_or_self.inner_section_definitions:
            if section.name:
                result[section.name] = section
            result.update({alias: section for alias in section.aliases})
    return result


@derived(cached=True)
def has_variable_names(self) -> bool:
    return any(value.variable for value in self.all_properties.values())


@derived(cached=True)
def section_path(self) -> str:
    used_in_sub_sections: list[SubSection] = SubSection.used_sections.get(self, [])  # type: ignore
    if len(used_in_sub_sections) == 0:
        return None if self.name == 'EntryArchive' else '__no_archive_path__'

    if len(used_in_sub_sections) > 1:
        return '__ambiguous__'

    parent_section = used_in_sub_sections[0].m_parent
    parent_path = parent_section.path

    if parent_path is None:
        return used_in_sub_sections[0].name

    if parent_path.startswith('__'):
        return parent_path

    return f'{parent_path}.{used_in_sub_sections[0].name}'


Section.inherited_sections = inherited_sections
Section.all_base_sections = all_base_sections
Section.all_inheriting_sections = all_inheriting_sections
Section.all_properties = all_properties
Section.all_quantities = all_quantities
Section.all_sub_sections = all_sub_sections
Section.all_sub_sections_by_section = all_sub_sections_by_section
Section.all_aliases = all_aliases
Section.all_inner_section_definitions = all_inner_section_definitions
Section.has_variable_names = has_variable_names
Section.path = section_path

SubSection.repeats = Quantity(type=bool, name='repeats', default=False)
SubSection.key_quantity = Quantity(type=str, name='key_quantity', default=None)

SubSection.sub_section = Quantity(
    type=MSectionReference(),
    name='sub_section',
    aliases=['section_definition', 'section_def', 'section'],
)

Quantity.m_def._section_cls = Quantity
Quantity.type = DirectQuantity(type=QuantityType, name='type')
Quantity.shape = DirectQuantity(
    type=Dimension, shape=['0..*'], name='shape', default=[]
)
Quantity.unit = Quantity(type=Unit, name='unit')
Quantity.dimensionality = DirectQuantity(type=str, name='dimensionality')
Quantity.default = DirectQuantity(type=Any, default=None, name='default')
Quantity.derived = DirectQuantity(
    type=Callable, default=None, name='derived', virtual=True
)
Quantity.virtual = DirectQuantity(type=bool, default=False, name='virtual')
Quantity.is_scalar = Quantity(
    type=bool, name='is_scalar', derived=lambda quantity: len(quantity.shape) == 0
)
Quantity.use_full_storage = Quantity(
    type=bool,
    name='use_full_storage',
    derived=lambda quantity: (
        quantity.flexible_unit or quantity.variable or len(quantity.attributes) > 0
    ),
)
Quantity.flexible_unit = Quantity(type=bool, name='flexible_unit', default=False)
Quantity.cached = Quantity(type=bool, name='cached', default=False)

Package.section_definitions = SubSection(
    sub_section=Section.m_def,
    name='section_definitions',
    repeats=True,
    aliases=['section_defs', 'sections'],
)

Package.category_definitions = SubSection(
    sub_section=Category.m_def,
    name='category_definitions',
    repeats=True,
    aliases=['category_defs'],
)


@derived(cached=True)
def all_definitions(self):
    result: dict[str, Definition] = dict()
    for sub_section_def in [Package.section_definitions, Package.category_definitions]:
        for definition in self.m_get_sub_sections(sub_section_def):
            result[definition.name] = definition
            result.update({alias: definition for alias in definition.aliases})
    return result


@derived(cached=True)
def dependencies(self):
    """
    All packages that provide definitions needed by this package. Being
    'needed' includes categories, base sections, and referenced definitions.
    """
    to_add = set()
    for content in self.m_all_contents():
        if isinstance(content, Definition):
            to_add.update(category.m_parent for category in content.categories)
        if isinstance(content, Section):
            to_add.update(section.m_parent for section in content.base_sections)
        elif isinstance(content, Quantity) and isinstance(content.type, Reference):
            to_add.add(content.type.target_section_def.m_parent)
    to_add.discard(self)

    result = set()
    while len(to_add) > 0:
        if (dependency := to_add.pop()) not in result:
            result.add(dependency)
            to_add.update(dependency.dependencies)

    return result


Package.all_definitions = all_definitions
Package.dependencies = dependencies

is_bootstrapping = False  # noqa

Definition.__init_cls__()
Attribute.__init_cls__()
Property.__init_cls__()
Section.__init_cls__()
Package.__init_cls__()
Category.__init_cls__()
Quantity.__init_cls__()
SubSection.__init_cls__()

is_initializing_proto = False  # noqa

AnnotationModel.model_rebuild()

SchemaPackage = Package
MEnum = Enum
