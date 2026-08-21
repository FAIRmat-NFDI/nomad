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

import functools
import re
from enum import Enum
from fnmatch import translate
from hashlib import sha1
from typing import Annotated, Union

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from nomad.app.v1.models import Direction, Metadata, MetadataPagination, Pagination
from nomad.app.v1.models.groups import UserGroupPagination, UserGroupQuery
from nomad.app.v1.routers.datasets import DatasetPagination
from nomad.app.v1.routers.uploads import (
    EntryProcDataPagination,
    RawDirPagination,
    UploadProcDataPagination,
    UploadProcDataQuery,
)


class DatasetQuery(BaseModel):
    dataset_id: str | None = Field(None)
    dataset_name: str | None = Field(None)
    user_id: list[str] | None = Field(None)
    dataset_type: str | None = Field(None)
    doi: str | None = Field(None)
    prefix: str | None = Field(None)


class EntryQuery(BaseModel):
    mainfile: list[str] | None = Field(
        None,
        description="""
    Provide a list of regex patterns to match the mainfile. Case sensitive.
    """,
    )
    parser_name: list[str] | None = Field(
        None,
        description="""
    Provide a list of regex patterns to match the parser names. Case sensitive.
    """,
    )
    references: list[str] | None = Field(
        None,
        description="""
    Provide a list of regex patterns to match the references. Case sensitive.
    """,
    )


class MetainfoQuery(BaseModel):
    pass


class MetainfoPagination(Pagination):
    @field_validator('order_by')
    @classmethod
    def validate_order_by(cls, order_by):
        # No validation
        return order_by

    def order_result(self, result):
        return list(sorted(result, reverse=self.order == Direction.desc))

    def paginate_result(self, result, pick_value):
        if self.page is not None:
            start = (self.page - 1) * self.page_size
            end = start + self.page_size
        elif self.page_offset is not None:
            start = self.page_offset
            end = start + self.page_size
        elif self.page_after_value is not None:
            start = 0
            for index, item in enumerate(result):
                if item == self.page_after_value:
                    start = index + 1
                    break
            end = start + self.page_size
        else:
            start, end = 0, self.page_size

        total_size = len(result)
        first, last = min(start, total_size), min(end, total_size)

        return [] if first == last else result[first:last]


class DirectiveType(Enum):
    plain = 'plain'
    resolved = 'resolved'
    auto_from_layout = 'auto_from_layout'

    def __repr__(self):
        return self.value


class DefinitionType(Enum):
    specialized_only = 'specialized_only'
    both = 'both'
    none = 'none'

    def __repr__(self):
        return self.value


class MDefFormatType(Enum):
    full = 'full'
    short = 'short'

    def __repr__(self):
        return self.value


class ResolveType(Enum):
    upload = 'upload'
    user = 'user'
    dataset = 'dataset'
    entry = 'entry'

    def __repr__(self):
        return self.value


def check_pattern(data: frozenset[str] | None) -> frozenset[str] | None:
    if data is not None:
        for value in data:
            assert re.match(r'^[*?+a-zA-z_\d./]*$', value) is not None
        return data
    return None


_WILDCARD_FROZENSET: frozenset[str] = frozenset({'*'})


class RequestConfig(BaseModel):
    """
    A class to represent the query configuration.
    An instance of `RequestConfig` shall be attached to each required field.
    The `RequestConfig` is used to determine the following.
        1. Whether the field should be included/excluded.
        2. For reference, whether the reference should be resolved, and how to resolve it.
    Each field can be handled differently.
    """

    property_name: str | None = Field(
        None,
        description="""
        The name of the current field, either a quantity or a subsection.
        This may differ from the key in the query as the key may contain indices.
        This is for internal use and is automatically set by the `RequestConfig` class.
        User should NOT set this field.
        """,
    )
    directive: DirectiveType = Field(
        DirectiveType.plain,
        description="""
        Indicate whether to include or exclude the current quantity/section.
        References can be resolved using `resolved`.
        The `*` is a shortcut of `plain`.
        The `auto_from_layout` directive can be used for server-side request derivation.
        """,
    )
    layout_id: str | None = Field(
        None,
        description="""
        An optional layout ID for server-side request derivation. Only used when
        `directive` is `auto_from_layout`. If omitted, the backend selects the default
        matching layout. If provided, the layout must exist, be enabled, and match the
        entry.
        """,
    )
    include: Annotated[frozenset[str] | None, AfterValidator(check_pattern)] = Field(
        None,
        description="""
        A list of patterns to match the quantities and subsections of the current section.
        The quantities/sections that match the include patterns AND do not match the include patterns are included.
        Only one of `include` and `exclude` can be set.
        """,
    )

    exclude: Annotated[frozenset[str] | None, AfterValidator(check_pattern)] = Field(
        None,
        description="""
        A list of patterns to match the quantities and subsections of the current section.
        The quantities/sections that match the include patterns AND do not match the include patterns are included.
        Only one of `include` and `exclude` can be set.
        """,
    )
    depth: int | None = Field(
        None,
        ge=0,
        description="""
        Indicate the maximum depth to be retrieved for the current section.
        If `None`, the depth is unlimited.
        This option does not apply to primitive quantities, which are always retrieved.
        """,
    )
    resolve_depth: int | None = Field(
        None,
        ge=0,
        description="""
        Indicate the maximum depth to be resolved for references.
        If `None`, the depth is unlimited.
        """,
    )
    resolve_type: ResolveType | None = Field(
        None,
        description="""
        Indicate how the current data should be interpreted.
        This option does not affect normal quantities/sections and should be left unassigned in most cases.
        If a value is assigned, for example `upload`, the target data will be treated as an upload id and
        the corresponding information will be retrieved.
        The original data will be left as it is if the assigned resolve type cannot find additional information.
        """,
    )
    max_list_size: int | None = Field(
        None,
        ge=0,
        description="""
        Indicate the size limit of lists. If assigned, lists longer than this limit will be ignored.
        """,
    )
    max_dict_size: int | None = Field(
        None,
        ge=0,
        description="""
        Indicate the size limit of dictionaries. If assigned, dictionaries larger than this limit will be ignored.
        """,
    )
    resolve_inplace: bool = Field(
        False,
        description="""
        Indicate whether to resolve references in-place.
        Deprecated, always set to `False`.
        If `false`, the reference string will be kept unchanged.
        The resolved quantity/section will be placed in the same archive.
        """,
    )
    export_whole_package: bool = Field(
        False,
        description="""
        Set to `True` to always get the whole package.
        Set to `False` to definitions per section basis.
        """,
    )
    always_rewrite_references: bool = Field(
        False,
        description="""
        Set to `True` to always rewrite references.
        This yields a more consistent output but has a performance penalty.
        """,
    )
    include_definition: DefinitionType = Field(
        DefinitionType.none,
        description="""
        Indicate whether to include the definition of the current section.
        If `default`, the default original standard definition bundled with the NOMAD will be included.
        If `custom`, the custom definition will be included.
        If `both`, both original and custom definitions will be included, with custom definitions taking precedence.
        If `none`, no definition will be included.
        """,
    )
    m_def_format: MDefFormatType = Field(
        MDefFormatType.full,
        description="""
        Control the format of `m_def` values in the response.
        If `full` (default), the full definition object is returned as-is.
        If `short`, `m_def` is returned as a compact string `"qualified_name@tag"`.
        When set to `short`, `include_definition` is implicitly treated as `both`.
        """,
    )
    index: tuple[int] | tuple[int | None, int | None] | None = Field(
        None,
        description="""
        The start and end index of the current field if it is a list.
        Can be a tuple of one index: (index).
        Or a tuple of two indices: (start, end), in which one of two can be `None`.
        This index field can be optionally used to slice the list.
        """,
    )
    inherit_from_parent: bool = Field(
        True,
        description="""
        Indicate whether to inherit the configuration from the parent section.
        This field only applies to the target section only, i.e., it does not propagate to its children.
        """,
    )
    pagination: None | (
        dict
        | DatasetPagination
        | EntryProcDataPagination
        | MetadataPagination
        | MetainfoPagination
        | RawDirPagination
        | UploadProcDataPagination
        | UserGroupPagination
    ) = Field(
        None,
        description="""
        The pagination configuration used for MongoDB search.
        This setting does not propagate to its children.
        For Token.ENTRIES, Token.UPLOADS and 'm_datasets', different validation rules apply.
        Please refer to `DatasetPagination`, `UploadProcDataPagination`, `MetadataPagination` for details.
        """,
    )
    query: None | (
        dict
        | DatasetQuery
        | EntryQuery
        | Metadata
        | MetainfoQuery
        | UploadProcDataQuery
        | UserGroupQuery
    ) = Field(
        None,
        description="""
        The query configuration used for either mongo or elastic search.
        This setting does not propagate to its children.
        It can only be defined at the root levels including Token.ENTRIES, Token.UPLOADS and 'm_datasets'.
        For Token.ENTRIES, the query is used in elastic search. It must comply with `WithQuery`.
        For Token.UPLOADS, the query is used in mongo search. It must comply with `UploadProcDataQuery`.
        For Token.DATASETS, the query is used in mongo search. It must comply with `DatasetQuery`.
        For Token.GROUPS, the query is used in mongo search. It must comply with `UserGroupQuery`.
        """,
    )
    model_config = ConfigDict(
        extra='forbid', ignored_types=(functools.cached_property,)
    )

    def __init__(self, **data):
        super().__init__(**data)
        if self.include and self.exclude:
            raise ValueError('One and only one of include and exclude can be set.')

        # do not set default using `Field`
        if self.include is None and self.exclude is None:
            self.include = frozenset({'*'})

    @field_validator('resolve_inplace')
    @classmethod
    def _validate_directive(cls, _v):  # pylint: disable=no-self-argument
        return False

    def new(self, query: dict, *, retain_pattern: bool = False) -> RequestConfig:
        """
        Create a new `RequestConfig` instance from a dictionary if possible.
        """
        query_copy: dict = {k.replace('-', '_'): v for k, v in query.items()}

        try:
            if query_copy.pop('inherit_from_parent', None) is False:
                return RequestConfig.model_validate(query_copy)

            if not retain_pattern:
                # override parent's include pattern
                if self.include is not None:
                    query_copy.setdefault('include', None)

                # override parent's exclude pattern
                if self.exclude is not None:
                    query_copy.setdefault('exclude', None)

            return RequestConfig.model_validate(
                dict(
                    self.model_dump(exclude_defaults=True, exclude_none=True),
                    **query_copy,
                )
            )
        except ValidationError:
            raise ValueError(f'Invalid query config: {query}.')

    def if_include(self, key: str) -> bool:
        """
        For a given key, check whether it should be included.
        """
        if self.include:
            if (
                self.include is _WILDCARD_FROZENSET
                or '*' in self.include
                or key in self.include
            ):
                return True
            return any(
                re.match(pattern, key) for pattern in _normalise_pattern(self.include)
            )
        if self.exclude:
            if key in self.exclude:
                return False
            return not any(
                re.match(pattern, key) for pattern in _normalise_pattern(self.exclude)
            )

        # should not reach here
        raise ValueError('Invalid config: neither include nor exclude is set.')

    @functools.cached_property
    def hash(self) -> str:
        return sha1(
            self.model_dump_json(exclude_defaults=True, exclude_none=True).encode(
                'utf-8'
            )
        ).hexdigest()

    def is_plain(self):
        """
        Check if the current configuration is plain.
        A plain configuration retrieves all the data without any further processing.
        This can be used to skip traversing the subtree so that performance can be improved.
        """
        return (
            self.directive == DirectiveType.plain
            and self.include_definition == DefinitionType.none
            and self.always_rewrite_references is False
            and self.index is None
            and self.depth is None
            and self.max_list_size is None
            and self.max_dict_size is None
            and (
                self.include is _WILDCARD_FROZENSET
                or self.include == _WILDCARD_FROZENSET
            )
            and self.m_def_format == MDefFormatType.full
        )


@functools.lru_cache(maxsize=1024)
def _normalise_pattern(pattern: frozenset[str]) -> frozenset[str]:  # pylint: disable=no-self-argument
    """
    Convert a (list of) glob pattern(s) to a (list of) regex patterns.
    """
    return frozenset(translate(v) for v in pattern)


class RequestQuery(dict[str, Union['RequestQuery', dict[str, RequestConfig]]]):
    pass
