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

import csv
import functools
import io
import json
import os.path
import tempfile
from collections.abc import Iterator
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any

import anyio
import orjson
import yaml
from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Path,
    Request,
    status,
)
from fastapi import Query as QueryParameter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.main import create_model
from starlette.responses import Response

from nomad import datamodel, files, metainfo, utils
from nomad import processing as proc
from nomad.app.v1.routers.auth import get_current_user
from nomad.archive import ArchiveQueryError, RequiredReader, RequiredValidationError
from nomad.auth.scopes import Scope
from nomad.config import config
from nomad.config.models.config import Reprocess
from nomad.datamodel import EditableUserMetadata
from nomad.datamodel.context import ServerContext
from nomad.files import StreamedFile, create_zipstream_async
from nomad.mongo.groups import MongoUserGroup
from nomad.processing.data import Upload
from nomad.search import (
    AuthenticationRequiredError,
    PermissionDeniedError,
    QueryValidationError,
    SearchError,
    normalize_api_query,
    search,
)
from nomad.search import update_metadata as es_update_metadata
from nomad.tracing import traced
from nomad.utils import strip

from ..models import (
    Aggregation,
    Files,
    HTTPExceptionModel,
    Metadata,
    MetadataEditRequest,
    MetadataPagination,
    MetadataRequired,
    MetadataResponse,
    Owner,
    Pagination,
    PaginationResponse,
    Query,
    QueryParameters,
    TermsAggregation,
    User,
    WithQuery,
    WithQueryAndPagination,
    files_parameters,
    metadata_pagination_parameters,
    metadata_required_parameters,
)
from ..utils import (
    DownloadItem,
    browser_download_headers,
    create_download_stream_raw_file,
    create_download_stream_zipped,
    create_responses,
    log_query,
)

router = APIRouter()


class APITag(str, Enum):
    DEFAULT = 'entries'
    METADATA = 'entries/metadata'
    RAW = 'entries/raw'
    ARCHIVE = 'entries/archive'


logger = utils.get_logger(__name__)

query_parameters = QueryParameters()

archive_required_documentation = strip(
    """
The `required` part allows you to specify what parts of the requested archives
should be returned. The NOMAD Archive is a hierarchical data format and
you can *require* certain branches (i.e. *sections*) in the hierarchy.
By specifying certain sections with specific contents or all contents (via
the directive `"*"`), you can determine what sections and what quantities should
be returned. The default is the whole archive, i.e., `"*"`.

For example to specify that you are only interested in the `metadata`
use:

```json
{
    "metadata": "*"
}
```

Or to only get the `energy_total` from each individual entry, use:
```json
{
    "run": {
        "configuration": {
            "energy": "*"
        }
    }
}
```

You can also request certain parts of a list, e.g. the last calculation:
```json
{
    "run": {
        "calculation[-1]": "*"
    }
}
```

These required specifications are also very useful to get workflow results.
This works because we can use references (e.g. workflow to final result calculation)
and the API will resolve these references and return the respective data.
For example just the total energy value and reduced formula from the resulting
calculation:
```json
{
    "workflow": {
        "calculation_result_ref": {
            "energy": "*",
            "system_ref": {
                "value": {
                    "chemical_composition": "*"
                }
            }
        }
    }
}
```

You can also resolve all references in a branch with the `include-resolved`
directive. This will resolve all references in the branch, and also all references
in referenced sections:
```json
{
    "workflow":
        "calculation_result_ref": "include-resolved"
    }
}
```

By default, the targets of "resolved" references are added to the archive at
their original hierarchy positions.
This means, all references are still references, but they are resolvable within
the returned data, since they targets are now part of the data. Another option
is to add
`"resolve-inplace": true` to the root of required. Here, the reference targets will
replace the references:
```json
{
    "resolve-inplace": true,
    "workflow":
        "calculation_result_ref": "include-resolved"
    }
}
```
"""
)


ArchiveRequired = str | dict[str, Any]

_archive_required_field = Body(
    '*',
    embed=True,
    description=archive_required_documentation,
    examples=[
        {
            'run': {'calculation[-1]': {'energy': '*'}, 'system[-1]': '*'},
            'metadata': '*',
        }
    ],
)


class EntriesArchive(WithQueryAndPagination):
    required: ArchiveRequired | None = _archive_required_field


class EntryArchiveRequest(BaseModel):
    required: ArchiveRequired | None = _archive_required_field


class EntriesArchiveDownload(WithQuery, EntryArchiveRequest):
    files: Files | None = Body(None)


class EntriesRawDir(WithQuery):
    pagination: MetadataPagination | None = Body(None)


class EntriesRaw(WithQuery):
    files: Files | None = Body(None, examples=[{'glob_pattern': 'vasp*.xml*'}])


class EntryRawDirFile(BaseModel):
    path: str | None = Field(None)
    size: int | None = Field(None)


class EntryRawDir(BaseModel):
    entry_id: str | None = Field(None)
    upload_id: str | None = Field(None)
    mainfile: str | None = Field(None)
    mainfile_key: str | None = Field(None)
    files: list[EntryRawDirFile] | None = Field(None)


class EntriesRawDirResponse(EntriesRawDir):
    pagination: PaginationResponse = Field(None)  # type: ignore
    data: list[EntryRawDir] | None = Field(None)


class EntryRawDirResponse(BaseModel):
    entry_id: str = Field(...)
    data: EntryRawDir = Field(...)


class EntryArchive(BaseModel):
    entry_id: str | None = Field(None)
    upload_id: str | None = Field(None)
    parser_name: str | None = Field(None)
    archive: dict[str, Any] | None = Field(None)


class EntriesArchiveResponse(EntriesArchive):
    pagination: PaginationResponse = Field(None)  # type: ignore
    data: list[EntryArchive] | None = Field(None)


class EntryArchiveResponse(EntryArchiveRequest):
    entry_id: str = Field(...)
    data: EntryArchive | None = Field(None)


class EntryMetadataResponse(BaseModel):
    entry_id: str | None = Field(None)
    required: MetadataRequired | None = Field(None)
    data: Any = Field(None, description=strip("""The entry metadata as dictionary."""))


class EntryMetadataEditActionField(BaseModel):
    value: str | None = Field(
        None, description='The value/values that is set as a string.'
    )
    success: bool | None = Field(
        None, description='If this can/could be done. Only in API response.'
    )
    message: str | None = Field(
        None,
        description='A message that details the action result. Only in API response.',
    )


EntryMetadataEditActions: Any = create_model(
    'EntryMetadataEditActions',  # type: ignore
    **{
        quantity.name: (
            EntryMetadataEditActionField | None
            if quantity.is_scalar
            else list[EntryMetadataEditActionField] | None,
            None,
        )
        for quantity in EditableUserMetadata.m_def.definitions
        if isinstance(quantity, metainfo.Quantity)
    },
)


class EntryMetadataEdit(WithQuery):
    verify: bool | None = Field(False, description='If true, no action is performed.')
    actions: EntryMetadataEditActions = Field(  # type: ignore
        None,
        description='Each action specifies a single value (even for multi valued quantities).',
    )  # type: ignore

    @field_validator('owner')
    @classmethod
    def validate_query(cls, owner):  # pylint: disable=no-self-argument
        return Owner.user


class EntryMetadataEditResponse(EntryMetadataEdit):
    success: bool | None = Field(
        None, description='If the overall edit can/could be done. Only in API response.'
    )
    message: str | None = Field(
        None,
        description='A message that details the overall edit result. Only in API response.',
    )


class ArchiveChangeAction(Enum):
    upsert = 'upsert'
    remove = 'remove'


def json_schema_extra(schema: dict[str, Any], model: type['ArchiveChange']):
    schema['properties']['new_value'] = {}


class ArchiveChange(BaseModel):
    path: str
    new_value: Any = None
    action: ArchiveChangeAction = ArchiveChangeAction.upsert

    model_config = ConfigDict(json_schema_extra=json_schema_extra)


class EntryEdit(BaseModel):
    changes: list[ArchiveChange]


class EntryEditResponse(EntryEdit):
    entry_id: str


def _default_sub_section_payload(
    sub_section_def: metainfo.SubSection,
) -> dict[str, Any]:
    """Return a minimal dict payload that identifies the sub-section type."""
    payload: dict[str, Any] = {
        'm_def': sub_section_def.sub_section.qualified_name(),
    }
    if sub_section_def.sub_section.definition_id:
        payload['m_def_id'] = sub_section_def.sub_section.definition_id
    return payload


def _section_def_from_dict(
    section_data: dict[str, Any],
    fallback_def: metainfo.Section,
    archive: metainfo.MSection = None,
) -> metainfo.Section:
    """Resolve the actual Section definition for a raw dict.

    If the dict carries an ``m_def`` key we try to resolve it via the same
    ``MSectionReference`` mechanism that ``MSection.from_dict`` uses, so that
    polymorphic sub-sections (e.g. ELN plug-in types) are handled correctly.
    Falls back to *fallback_def* when resolution fails or the key is absent.
    """
    m_def_name = section_data.get('m_def')
    if not m_def_name:
        return fallback_def
    try:
        # Reuse the same resolution path as MSection.from_dict so that
        # fully-qualified Python class names (e.g. "nomad.datamodel.metainfo.eln.ELNSample")
        # resolve to the real Section definition without needing a DB context.
        proxy = metainfo.MSectionReference().normalize(
            m_def_name,
            section=archive if archive is not None else datamodel.EntryArchive.m_def,
        )
        if isinstance(proxy, metainfo.Section):
            return proxy
        # SectionProxy — trigger resolution
        resolved = proxy.m_proxy_resolve()
        if isinstance(resolved, metainfo.Section):
            return resolved
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f'Could not resolve m_def "{m_def_name}".',
        ) from exc

    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        detail=f'm_def "{m_def_name}" does not resolve to a Section definition.',
    )


def _resolve_archive_change_target_in_dict(
    archive_data: dict[str, Any],
    path: str,
    *,
    create_missing: bool,
    archive: metainfo.MSection = None,
) -> tuple[dict[str, Any], metainfo.Property, int | None]:
    """Walk *archive_data* (a raw dict) to the parent container described by *path*.

    Returns a 3-tuple ``(parent_container, definition, item_index)`` where:

    * ``parent_container`` is the dict/list that directly holds the target value.
    * ``definition`` is the metainfo :class:`Property` for the last path segment.
    * ``item_index`` is ``None`` for singular properties / sub-sections, or an
      ``int`` for repeated sub-sections / indexed quantities.
    """
    if not path:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='Archive change path must not be empty.',
        )

    parts = path.split('/')
    # Start at the root section definition so we can validate each step.
    current_section_def: metainfo.Section = datamodel.EntryArchive.m_def
    current_data: dict[str, Any] = archive_data
    index = 0

    while index < len(parts) - 1:
        property_name = parts[index]

        # Allow the actual section type to differ (polymorphism via m_def).
        current_section_def = _section_def_from_dict(
            current_data, current_section_def, archive=archive
        )

        try:
            definition = current_section_def.all_properties[property_name]
        except KeyError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    f'Invalid archive change path "{path}". '
                    f'Property "{property_name}" is not defined in '
                    f'{current_section_def.qualified_name()}.'
                ),
            ) from exc

        if not isinstance(definition, metainfo.SubSection):
            # The only valid non-subsection mid-path segment is an integer index
            # into a repeated quantity on the *last* non-terminal hop.
            if index == len(parts) - 2 and parts[index + 1].isdigit():
                item_index = int(parts[index + 1])
                if not create_missing:
                    existing_items: list[Any] = current_data.get(definition.name, [])
                    if item_index >= len(existing_items):
                        raise HTTPException(
                            status.HTTP_400_BAD_REQUEST,
                            detail=f'Invalid archive change path "{path}". Index {item_index} out of bounds.',
                        )
                return current_data, definition, item_index
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f'Invalid archive change path "{path}".',
            )

        if definition.repeats:
            if index + 1 >= len(parts) or not parts[index + 1].isdigit():
                if index == len(parts) - 1:
                    return current_data, definition, None
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=f'Invalid archive change path "{path}".',
                )

            sub_section_index = int(parts[index + 1])
            if index + 1 == len(parts) - 1:
                if not create_missing:
                    existing_sub_sections: list[Any] = current_data.get(
                        property_name, []
                    )
                    if sub_section_index >= len(existing_sub_sections):
                        raise HTTPException(
                            status.HTTP_400_BAD_REQUEST,
                            detail=f'Invalid archive change path "{path}". Index {sub_section_index} out of bounds.',
                        )
                return current_data, definition, sub_section_index

            # Navigate into the repeated sub-section list in the raw dict.
            sub_list: list = current_data.setdefault(property_name, [])
            if len(sub_list) <= sub_section_index:
                if not create_missing:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail=f'Invalid archive change path "{path}".',
                    )
                sub_list.extend([None] * (sub_section_index - len(sub_list) + 1))
            if sub_list[sub_section_index] is None:
                if not create_missing:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail=f'Invalid archive change path "{path}".',
                    )
                sub_list[sub_section_index] = {}

            current_data = sub_list[sub_section_index]
            current_section_def = definition.sub_section
            index += 2
            continue

        # Singular sub-section.
        next_data: dict | None = current_data.get(property_name)
        if next_data is None:
            if not create_missing:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=f'Invalid archive change path "{path}".',
                )
            next_data = {}
            current_data[property_name] = next_data

        current_data = next_data
        current_section_def = definition.sub_section
        index += 1

    property_name = parts[-1]

    # Resolve section def one last time for the final hop.
    current_section_def = _section_def_from_dict(
        current_data, current_section_def, archive=archive
    )

    try:
        definition = current_section_def.all_properties[property_name]
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f'Invalid archive change path "{path}". '
                f'Property "{property_name}" is not defined in '
                f'{current_section_def.qualified_name()}.'
            ),
        ) from exc

    return current_data, definition, None


def _apply_archive_change_to_dict(
    archive_data: dict[str, Any],
    change: ArchiveChange,
    archive: metainfo.MSection = None,
) -> None:
    """Apply a single :class:`ArchiveChange` directly to the raw *archive_data* dict.

    ``parent_data`` returned by the resolver is always a section dict.
    ``item_index`` is set when the target is an element of a repeated sub-section list
    or an indexed quantity; in that case the list lives at ``parent_data[definition.name]``.
    """
    parent_data, definition, item_index = _resolve_archive_change_target_in_dict(
        archive_data,
        change.path,
        create_missing=change.action != ArchiveChangeAction.remove,
        archive=archive,
    )

    if change.action == ArchiveChangeAction.remove:
        if item_index is not None:
            # Repeated sub-section or indexed quantity — remove the item and
            # keep lists compact, matching m_remove(..., mode='pop').
            sub_list: list = parent_data.get(definition.name, [])
            if item_index < len(sub_list):
                sub_list.pop(item_index)
                # Strip any trailing Nones that might have been unmasked from
                # previously sparse data.
                while sub_list and sub_list[-1] is None:
                    sub_list.pop()
        else:
            parent_data.pop(definition.name, None)
        return

    # ---- upsert ----
    value = change.new_value

    if item_index is not None:
        sub_list = parent_data.setdefault(definition.name, [])
        if len(sub_list) <= item_index:
            sub_list.extend([None] * (item_index - len(sub_list) + 1))
        sub_list[item_index] = value
    else:
        # Singular property or sub-section — write directly.
        # For singular sub-sections the type is already known from the schema,
        # so omitting m_def matches the output that m_to_dict would have produced.
        parent_data[definition.name] = value


_bad_owner_response_unauthorized = (
    status.HTTP_401_UNAUTHORIZED,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Unauthorized. The given owner requires authorization,
        but no or bad authentication credentials are given."""
        ),
    },
)

_bad_id_response = (
    status.HTTP_404_NOT_FOUND,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Entry not found. The given id does not match any entry."""
        ),
    },
)

_bad_path_response = (
    status.HTTP_404_NOT_FOUND,
    {'model': HTTPExceptionModel, 'description': 'File or directory not found.'},
)

_bad_edit_request = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': 'Edit request could not be executed.',
    },
)


_bad_edit_request_unauthorized = (
    status.HTTP_401_UNAUTHORIZED,
    {
        'model': HTTPExceptionModel,
        'description': 'Authorization required.',
    },
)

_bad_edit_request_forbidden = (
    status.HTTP_403_FORBIDDEN,
    {
        'model': HTTPExceptionModel,
        'description': 'Not enough permissions to execute edit request.',
    },
)

_bad_edit_request_empty_query = (
    status.HTTP_404_NOT_FOUND,
    {'model': HTTPExceptionModel, 'description': 'No matching entries found.'},
)

_raw_response = (
    200,
    {
        'content': {'application/zip': {}},
        'description': strip(
            """
        A zip file with the requested raw files. The file is streamed.
        The content length is not known in advance.
    """
        ),
    },
)

_raw_file_response = (
    200,
    {
        'content': {'application/octet-stream': {}},
        'description': strip(
            """
        A byte stream with raw file contents. The content length is not known in advance.
        If the whole file is requested, the mime-type might be more specific, depending
        on the file contents.
    """
        ),
    },
)

_archives_download_response = (
    200,
    {
        'content': {'application/zip': {}},
        'description': strip(
            """
        A zip file with the requested archive files. The file is streamed.
        The content length is not known in advance.
    """
        ),
    },
)

_archive_download_response = (
    200,
    {
        'content': {'application/json': {}},
        'description': strip(
            """
        A json body with the requested archive.
    """
        ),
    },
)


_bad_archive_required_response = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The given required specification could not be understood."""
        ),
    },
)


_bad_metadata_edit_response = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The given edit actions cannot be performed by you on the given query."""
        ),
    },
)


def perform_search(*args, **kwargs):
    with utils.timer(logger, 'time to handle search'):
        try:
            search_response = search(*args, **kwargs)
            search_response.es_query = None
            return search_response

        except QueryValidationError as e:
            raise RequestValidationError(errors=e.errors)

        except AuthenticationRequiredError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(e))

        except PermissionDeniedError as e:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(e))

        except SearchError as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f'Elasticsearch could not process your query: {str(e)}',
            )


def _is_upload_visible_for_owner(
    upload: Upload, owner: Owner, user: User | None
) -> bool:
    """Return whether an upload belongs to the requested search owner scope."""
    user_id = user.user_id if user is not None else None
    group_ids = MongoUserGroup.get_ids_by_user_id(user_id)
    is_viewer = (user_id is not None and user_id in upload.viewers) or not set(
        group_ids
    ).isdisjoint(upload.viewer_groups)
    is_public = upload.published and not upload.with_embargo

    if owner == Owner.public:
        return is_public
    if owner == Owner.all_:
        return upload.published or is_viewer
    if owner == Owner.visible:
        return is_public or is_viewer

    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=f'Authentication required for owner={owner!r}.',
        )

    if owner == Owner.shared:
        return is_viewer
    if owner == Owner.user:
        return upload.main_author == user_id
    if owner == Owner.staging:
        return not upload.published and is_viewer
    if owner == Owner.admin:
        if not user.is_admin:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail='This can only be used by the admin user.',
            )
        return True

    return False


def _resolve_entry_metadata_from_mongo(
    query: Any, user: User | None, owner: Owner = Owner.visible
) -> dict | None:
    """Resolve core metadata for a visible exact-ID entry directly from MongoDB."""
    if (
        isinstance(query, dict)
        and len(query) == 1
        and 'entry_id' in query
        and isinstance(query['entry_id'], str)
    ):
        entry = proc.Entry.objects(entry_id=query['entry_id']).first()
        if entry is not None:
            upload = Upload.objects(upload_id=entry.upload_id).first()
            if upload is not None and _is_upload_visible_for_owner(upload, owner, user):
                return {
                    'entry_id': entry.entry_id,
                    'upload_id': entry.upload_id,
                    'mainfile': entry.mainfile,
                    'mainfile_key': entry.mainfile_key,
                    'parser_name': entry.parser_name,
                }
    return None


def _single_entry_pagination(
    pagination: MetadataPagination,
) -> tuple[bool, PaginationResponse] | None:
    """Build pagination for one result, or defer unsupported cases to search."""
    if pagination.page_after_value is not None or pagination.order_by not in {
        None,
        'entry_id',
    }:
        return None

    page_size = pagination.page_size if pagination.page_size is not None else 10
    page = pagination.page if pagination.page is not None else 1
    page_offset = pagination.page_offset or 0
    include_entry = page_size > 0 and page == 1 and page_offset == 0
    response_data = pagination.model_dump(exclude_none=True)
    response_data['order_by'] = pagination.order_by or 'entry_id'

    return include_entry, PaginationResponse(total=1, **response_data)


@router.post(
    '/query',
    tags=[APITag.METADATA],
    summary='Search entries and retrieve their metadata',
    response_model=MetadataResponse,
    responses=create_responses(_bad_owner_response_unauthorized),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def post_entries_metadata_query(
    data: Metadata,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    """
    Executes a *query* and returns a *page* of the results with *required* result data
    as well as *statistics* and *aggregated* data.

    This is the basic search operation to retrieve metadata for entries that match
    certain search criteria (`query` and `owner`). All parameters (including `query`, `owner`)
    are optional. Look at the body schema or parameter documentation for more details.

    By default the *empty* search (that returns everything) is performed. Only a small
    page of the search results are returned at a time; use `pagination` in subsequent
    requests to retrieve more data. Each entry has a lot of different *metadata*, use
    `required` to limit the data that is returned.

    The `statistics` and `aggregations` keys will further allow to return statistics
    and aggregated data over all search results.
    """
    res = perform_search(
        owner=data.owner if data.owner is not None else Owner.public,
        query=data.query,
        pagination=data.pagination,
        required=data.required,
        aggregations=data.aggregations,
        user_id=user.user_id if user is not None else None,
    )
    if config.services.log_api_queries and data.query and data.query != {}:
        log_query(logger, data.query)
    return res


@router.get(
    '',
    tags=[APITag.METADATA],
    summary='Search entries and retrieve their metadata',
    response_model=MetadataResponse,
    responses=create_responses(_bad_owner_response_unauthorized),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_entries_metadata(
    request: Request,
    with_query: Annotated[WithQuery, Depends(query_parameters)],
    pagination: Annotated[MetadataPagination, Depends(metadata_pagination_parameters)],
    required: Annotated[MetadataRequired, Depends(metadata_required_parameters)],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    """
    Executes a *query* and returns a *page* of the results with *required* result data.
    This is a version of `/entries/query`. Queries work a little different, because
    we cannot put complex queries into URL parameters.

    In addition to the `q` parameter (see parameter documentation for details), you can use all NOMAD
    search quantities as parameters, e.g. `?atoms=H&atoms=O`. Those quantities can be
    used with additional operators attached to their names, e.g. `?n_atoms__gte=3` for
    all entries with more than 3 atoms. Operators are `all`, `any`, `none`, `gte`,
    `gt`, `lt`, `lte`.
    """

    res = perform_search(
        owner=with_query.owner,
        query=with_query.query,
        pagination=pagination,
        required=required,
        user_id=user.user_id if user is not None else None,
    )
    res.pagination.populate_urls(request)
    return res


def _do_exhaustive_search(
    owner: Owner,
    query: Query,
    required: MetadataRequired,
    user: User,
    page_size: int = 100,
) -> Iterator[dict[str, Any]]:
    """Perform a paginated search.

    Args:
        owner (Owner): The owner defining the search scope.
        query (Query): The query specifying search filters and conditions.
        required (MetadataRequired): Includes and excludes for the response.
        user (User): The user performing the search, used for authorization.
        page_size (int): The number of results per page.
    """
    page_after_value: str | None = None
    while True:
        response = perform_search(
            owner=owner,
            query=query,
            pagination=MetadataPagination(
                page_size=page_size,
                page_after_value=page_after_value,
                order_by='upload_id',
            ),
            required=required,
            user_id=user.user_id if user is not None else None,
        )

        page_after_value = response.pagination.next_page_after_value

        yield from response.data

        if page_after_value is None or len(response.data) == 0:
            break


class _Uploads:
    """
    A helper class that caches subsequent access to upload files the same upload.
    """

    def __init__(self):
        self._upload_files = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_upload_files(self, upload_id: str) -> files.UploadFiles:
        if self._upload_files is not None and self._upload_files.upload_id != upload_id:
            self._upload_files.close()

        if self._upload_files is None or self._upload_files.upload_id != upload_id:
            self._upload_files = files.UploadFiles.get(upload_id)

        return self._upload_files

    def close(self):
        if self._upload_files is not None:
            self._upload_files.close()


def _create_entry_rawdir(entry_metadata: dict[str, Any], uploads: _Uploads):
    entry_id = entry_metadata['entry_id']
    upload_id = entry_metadata['upload_id']
    mainfile = entry_metadata['mainfile']
    mainfile_key = entry_metadata.get('mainfile_key')

    upload_files = uploads.get_upload_files(upload_id)
    mainfile_dir = os.path.dirname(mainfile)

    files = []
    for path_info in upload_files.raw_listdir(mainfile_dir, files_only=True):
        files.append(EntryRawDirFile(path=path_info.path, size=path_info.size))

    return EntryRawDir(
        entry_id=entry_id,
        upload_id=upload_id,
        mainfile=mainfile,
        mainfile_key=mainfile_key,
        files=files,
    )


def _answer_entries_rawdir_request(
    owner: Owner, query: Query, pagination: MetadataPagination, user: User
):
    if owner == Owner.all_:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=strip(
                """
            The owner=all is not allowed for this operation as it will search for entries
            that you might now be allowed to access.
            """
            ),
        )

    single_metadata = _resolve_entry_metadata_from_mongo(query, user, owner)
    single_pagination = (
        _single_entry_pagination(pagination) if single_metadata is not None else None
    )
    if single_metadata is not None and single_pagination is not None:
        include_entry, response_pagination = single_pagination
        entries_metadata = [single_metadata] if include_entry else []
        response_owner = owner
        response_query = normalize_api_query(query)
    else:
        search_response = perform_search(
            owner=owner,
            query=query,
            pagination=pagination,
            required=MetadataRequired(include=['entry_id', 'upload_id', 'mainfile']),
            user_id=user.user_id if user is not None else None,
        )
        entries_metadata = search_response.data
        response_owner = search_response.owner
        response_query = search_response.query
        response_pagination = search_response.pagination

    with _Uploads() as uploads:
        response_data = [
            _create_entry_rawdir(entry_metadata, uploads)
            for entry_metadata in entries_metadata
        ]

    return EntriesRawDirResponse(
        owner=response_owner,
        query=response_query,
        pagination=response_pagination,
        data=response_data,
    )


def _answer_entries_raw_request(
    owner: Owner,
    query: Query,
    files: Files,
    user: User,
    entry_metadata: dict[str, Any] | None = None,
):
    if owner == Owner.all_:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=strip(
                """
            The owner=all is not allowed for this operation as it will search for entries
            that you might now be allowed to access.
            """
            ),
        )

    if entry_metadata is None:
        response = perform_search(
            owner=owner,
            query=query,
            pagination=MetadataPagination(page_size=0),
            required=MetadataRequired(include=[]),
            user_id=user.user_id if user is not None else None,
        )
        total = response.pagination.total
    else:
        entry_metadata = {
            key: entry_metadata[key] for key in ('entry_id', 'upload_id', 'mainfile')
        }
        total = 1

    if total > config.services.max_entry_download:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f'The limit of maximum number of entries in a single download '
                f'({config.services.max_entry_download}) has been exceeded ({total}).'
            ),
        )

    files_params = Files() if files is None else files
    search_includes = ['entry_id', 'upload_id', 'mainfile']

    try:
        # a generator of File objects to create the streamed zip from
        def download_items_generator():
            # go through all entries that match the query
            entries_metadata = (
                [entry_metadata]
                if entry_metadata is not None
                else _do_exhaustive_search(
                    owner,
                    query,
                    required=MetadataRequired(include=search_includes),
                    user=user,
                )
            )
            for current_entry_metadata in entries_metadata:
                current_entry_metadata = dict(current_entry_metadata)
                upload_id = current_entry_metadata['upload_id']
                mainfile = current_entry_metadata['mainfile']
                current_entry_metadata['mainfile'] = os.path.join(upload_id, mainfile)

                mainfile_dir = os.path.dirname(mainfile)
                yield DownloadItem(
                    upload_id=upload_id,
                    raw_path=mainfile_dir,
                    zip_path=os.path.join(upload_id, mainfile_dir),
                    entry_metadata=current_entry_metadata,
                )

        return StreamingResponse(
            create_download_stream_zipped(
                download_items=download_items_generator(),
                re_pattern=files_params.re_pattern,
                recursive=False,
                create_manifest_file=True,
                compress=files_params.compress
                if files_params.compress is not None
                else False,
            ),
            headers=browser_download_headers(
                filename='raw_files.zip', media_type='application/zip'
            ),
        )
    except Exception as e:
        logger.error('exception while streaming download', exc_info=e)
        raise


_entries_rawdir_query_docstring = strip(
    """
    Will perform a search and return a *page* of raw file metadata for entries fulfilling
    the query. This allows you to get a complete list of all rawfiles with their full
    path in their respective upload and their sizes. The first returned files for each
    entry, is their respective *mainfile*.

    Each entry on NOMAD has a set of raw files. These are the files in their original form,
    i.e. as provided by the uploader. More specifically, an entry has a *mainfile*, identified as
    parseable. For CMS entries, the mainfile is usually the main output file of the code. All other
    files in the same directory are considered the entries *auxiliary* no matter their role
    or if they were actually parsed by NOMAD.

    This operation supports the usual `owner`, `query`, and `pagination` parameters.
    """
)


@router.post(
    '/rawdir/query',
    tags=[APITag.RAW],
    summary='Search entries and get their raw files metadata',
    description=_entries_rawdir_query_docstring,
    response_model=EntriesRawDirResponse,
    responses=create_responses(_bad_owner_response_unauthorized),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def post_entries_rawdir_query(
    data: EntriesRawDir,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    return _answer_entries_rawdir_request(
        owner=data.owner if data.owner is not None else Owner.public,
        query=data.query,
        pagination=data.pagination
        if data.pagination is not None
        else MetadataPagination(),
        user=user,
    )


@router.get(
    '/rawdir',
    tags=[APITag.RAW],
    summary='Search entries and get their raw files metadata',
    description=_entries_rawdir_query_docstring,
    response_model=EntriesRawDirResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    responses=create_responses(_bad_owner_response_unauthorized),
)
def get_entries_rawdir(
    request: Request,
    with_query: Annotated[WithQuery, Depends(query_parameters)],
    pagination: Annotated[MetadataPagination, Depends(metadata_pagination_parameters)],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    res = _answer_entries_rawdir_request(
        owner=with_query.owner if with_query.owner is not None else Owner.public,
        query=with_query.query,
        pagination=pagination,
        user=user,
    )
    res.pagination.populate_urls(request)
    return res


_entries_raw_query_docstring = strip(
    """
    This operation will perform a search and stream a .zip file with the raw files of the
    found entries.

    Each entry on NOMAD has a set of raw files. These are the files in their original form,
    i.e. as provided by the uploader. More specifically, an entry has a *mainfile*, identified as
    parseable. For CMS entries, the mainfile is usually the main output file of the code. All other
    files in the same directory are considered the entries *auxiliary* no matter their role
    or if they were actually parsed by NOMAD.

    After performing a search (that uses the same parameters as in all search operations),
    NOMAD will iterate through all results and create a .zip-file with all the entries'
    main and auxiliary files. The files will be organized in the same directory structure
    that they were uploaded in. The respective upload root directories are further prefixed
    with the `upload_id` of the respective uploads. The .zip-file will further contain
    a `manifest.json` with `upload_id`, `entry_id`, and `mainfile` of each entry.
    """
)


@router.post(
    '/raw/query',
    tags=[APITag.RAW],
    summary='Search entries and download their raw files',
    description=_entries_raw_query_docstring,
    response_class=StreamingResponse,
    responses=create_responses(_raw_response, _bad_owner_response_unauthorized),
)
def post_entries_raw_query(
    data: EntriesRaw,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    return _answer_entries_raw_request(
        owner=data.owner if data.owner is not None else Owner.public,
        query=data.query,
        files=data.files if data.files is not None else Files(),
        user=user,
    )


@router.get(
    '/raw',
    tags=[APITag.RAW],
    summary='Search entries and download their raw files',
    description=_entries_raw_query_docstring,
    response_class=StreamingResponse,
    responses=create_responses(_raw_response, _bad_owner_response_unauthorized),
)
def get_entries_raw(
    with_query: Annotated[WithQuery, Depends(query_parameters)],
    files: Annotated[Files, Depends(files_parameters)],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    return _answer_entries_raw_request(
        owner=with_query.owner if with_query.owner is not None else Owner.public,
        query=with_query.query,
        files=files,
        user=user,
    )


@router.get(
    '/export',
    tags=[APITag.METADATA],
    summary='Search entries and download their metadata in selected format',
    response_class=StreamingResponse,
    responses=create_responses(_bad_owner_response_unauthorized),
)
def export_entries_metadata(
    with_query: Annotated[WithQuery, Depends(query_parameters)],
    required: Annotated[MetadataRequired, Depends(metadata_required_parameters)],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
    content_type: Annotated[str, Header()] = 'application/json',
    page_size: Annotated[int, QueryParameter(gt=0)] = 10_000,
):
    """(**Experimental**) Export metadata entries in a selected format.

    This endpoint allows users to export metadata entries in either JSON or CSV format.
    The format must be specified via the `Content-Type` HTTP header:
        - `application/json` → Returns the metadata as a JSON response.
        - `text/csv` → Returns the metadata as a CSV file.
    """
    if with_query.owner == Owner.all_:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=strip(
                """
            The owner=all is not allowed for this operation as it will search for entries
            that you might now be allowed to access.
            """
            ),
        )

    response = perform_search(
        owner=with_query.owner,
        query=with_query.query,
        pagination=MetadataPagination(page_size=0),
        required=MetadataRequired(include=[]),
        user_id=user.user_id if user is not None else None,
    )

    if response.pagination.total > config.services.max_entry_metadata_download:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f'The limit of maximum number of metadata in a single download '
                f'({config.services.max_entry_metadata_download}) has been exceeded ({response.pagination.total}).'
            ),
        )

    def json_stream() -> Iterator[bytes]:
        """Stream metadata in JSON format."""
        first_item: bool = True
        yield b'['  # Start of JSON array

        for entry_metadata in _do_exhaustive_search(
            owner=with_query.owner if with_query.owner is not None else Owner.public,
            query=with_query.query,
            user=user,
            required=required,
            page_size=page_size,
        ):
            if not first_item:
                yield b','  # Separate JSON objects
            first_item = False
            yield json.dumps(entry_metadata, default=str).encode('utf-8')

        yield b']'  # End of JSON array

    def csv_stream() -> Iterator[bytes]:
        """Stream metadata in CSV format."""
        first_row: bool = True
        buffer: io.StringIO = io.StringIO()
        writer: csv.DictWriter | None = None

        for entry_metadata in _do_exhaustive_search(
            owner=with_query.owner if with_query.owner is not None else Owner.public,
            query=with_query.query,
            user=user,
            required=required,
            page_size=page_size,
        ):
            if first_row:
                writer = csv.DictWriter(buffer, fieldnames=entry_metadata.keys())
                yield buffer.getvalue().encode('utf-8')  # Send column headers
                buffer.seek(0)
                buffer.truncate(0)  # Clear buffer
                writer.writeheader()
                first_row = False

            writer.writerow(entry_metadata)
            yield buffer.getvalue().encode('utf-8')  # Send row data
            buffer.seek(0)
            buffer.truncate(0)

    if content_type == 'text/csv':
        return StreamingResponse(
            csv_stream(),
            media_type=content_type,
            headers=browser_download_headers(filename='metadata_export.csv'),
        )

    elif content_type == 'application/json':
        return StreamingResponse(
            json_stream(),
            media_type=content_type,
            headers=browser_download_headers(filename='metadata_export.json'),
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported {content_type=}. Expected 'application/json' or 'text/csv'.",
        )


@traced(span_name='entries.read_archive')
def _read_archive(entry_metadata, uploads, required_reader: RequiredReader):
    entry_id = entry_metadata['entry_id']
    upload_id = entry_metadata['upload_id']
    upload_files = uploads.get_upload_files(upload_id)

    try:
        with upload_files.read_archive(entry_id) as archive:
            return {
                'entry_id': entry_id,
                'parser_name': entry_metadata['parser_name'],
                'archive': required_reader.read(archive, entry_id, upload_id),
            }
    except ArchiveQueryError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))


def _validate_required(required: ArchiveRequired, user) -> RequiredReader:
    try:
        return RequiredReader(required, user=user)
    except RequiredValidationError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[dict(msg=e.msg, loc=['required'] + e.loc)],
        )


@traced(span_name='entries.read_entry_from_archive')
def _read_entry_from_archive(entry: dict, uploads, required_reader: RequiredReader):
    entry_id, upload_id = entry['entry_id'], entry['upload_id']

    # all other exceptions are handled by the caller `_answer_entries_archive_request`
    try:
        upload_files = uploads.get_upload_files(upload_id)

        with upload_files.read_archive(entry_id) as archive:
            entry['archive'] = required_reader.read(archive, entry_id, upload_id)
            return entry
    except ArchiveQueryError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    except KeyError as e:
        logger.error('missing archive', exc_info=e, entry_id=entry_id)

        return None


@traced(span_name='entries.answer_entries_archive_request')
def _answer_entries_archive_request(
    request: Request,
    owner: Owner,
    query: Query,
    pagination: MetadataPagination,
    required: ArchiveRequired,
    user: User,
    populate_url: bool = False,
):
    if owner == Owner.all_:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=strip(
                """The owner=all is not allowed for this operation as it will search for entries
                that you might now be allowed to access."""
            ),
        )

    if required is None:
        required = '*'

    single_metadata = _resolve_entry_metadata_from_mongo(query, user, owner)
    single_pagination = (
        _single_entry_pagination(pagination) if single_metadata is not None else None
    )
    if single_metadata is not None and single_pagination is not None:
        include_entry, response_pagination = single_pagination
        entries: list = (
            [
                {
                    'entry_id': single_metadata['entry_id'],
                    'upload_id': single_metadata['upload_id'],
                    'parser_name': single_metadata['parser_name'],
                }
            ]
            if include_entry
            else []
        )
        response_owner = owner
        response_query = normalize_api_query(query)
    else:
        search_response = perform_search(
            owner=owner,
            query=query,
            pagination=pagination,
            required=MetadataRequired(include=['entry_id', 'upload_id', 'parser_name']),
            user_id=user.user_id if user is not None else None,
        )
        entries = [
            {
                'entry_id': entry['entry_id'],
                'upload_id': entry['upload_id'],
                'parser_name': entry['parser_name'],
            }
            for entry in search_response.data
        ]
        response_owner = search_response.owner
        response_query = search_response.query
        response_pagination = search_response.pagination

    required_reader = _validate_required(required, user)
    response_data = []
    if isinstance(entries, dict):
        entries = [entries]

    with _Uploads() as uploads:
        for entry in entries:
            disconnected = anyio.from_thread.run(request.is_disconnected)
            if disconnected:
                logger.info('client disconnected', endpoint='entries/archive')
                break

            entry_archive = _read_entry_from_archive(entry, uploads, required_reader)
            response_data.append(entry_archive)

        logger.info('read all archives', endpoint='entries/archive')

    response = EntriesArchiveResponse(
        owner=response_owner,
        query=response_query,
        pagination=response_pagination,
        required=required,
    )
    if populate_url:
        response.pagination.populate_urls(request)
    result = response.model_dump(exclude_none=True)
    result['data'] = list(filter(None, response_data))

    return ORJSONResponse(result)


_entries_archive_docstring = strip(
    """
    This operation will perform a search with the given `query` and `owner` and return
    the a *page* of `required` archive data. Look at the body schema or parameter documentation
    for more details. The **GET** version of this operation will only allow to provide
    the full archives.
    """
)


@router.post(
    '/archive/query',
    tags=[APITag.ARCHIVE],
    summary='Search entries and access their archives',
    description=_entries_archive_docstring,
    response_model=EntriesArchiveResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    responses=create_responses(
        _bad_owner_response_unauthorized, _bad_archive_required_response
    ),
)
def post_entries_archive_query(
    request: Request,
    data: EntriesArchive,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    res = _answer_entries_archive_request(
        request=request,
        owner=data.owner if data.owner is not None else Owner.public,
        query=data.query,
        pagination=data.pagination
        if data.pagination is not None
        else MetadataPagination(),
        required=data.required,
        user=user,
    )
    if (
        config.services.log_api_queries
        and data.query
        and data.required
        and data.query != {}
        and data.required != {}
    ):
        log_query(logger, data.query, data.required, 'entries/archive')
    return res


@router.get(
    '/archive',
    tags=[APITag.ARCHIVE],
    summary='Search entries and access their archives',
    description=_entries_archive_docstring,
    response_model=EntriesArchiveResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    responses=create_responses(
        _bad_owner_response_unauthorized, _bad_archive_required_response
    ),
)
def get_entries_archive_query(
    request: Request,
    with_query: Annotated[WithQuery, Depends(query_parameters)],
    pagination: Annotated[MetadataPagination, Depends(metadata_pagination_parameters)],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    return _answer_entries_archive_request(
        request=request,
        owner=with_query.owner if with_query.owner is not None else Owner.public,
        query=with_query.query,
        pagination=pagination,
        required=None,
        user=user,
        populate_url=True,
    )


@traced(span_name='entries.answer_entries_archive_download_request')
def _answer_entries_archive_download_request(
    owner: Owner, query: Query, required: ArchiveRequired, files: Files, user: User
):
    if owner == Owner.all_:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail=strip(
                """
            The owner=all is not allowed for this operation as it will search for entries
            that you might now be allowed to access.
            """
            ),
        )

    files_params = Files() if files is None else files

    response = perform_search(
        owner=owner,
        query=query,
        pagination=MetadataPagination(page_size=0),
        required=MetadataRequired(include=[]),
        user_id=user.user_id if user is not None else None,
    )

    if response.pagination.total > config.services.max_entry_download:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f'The limit of maximum number of entries in a single download'
            f' ({config.services.max_entry_download}) has been exceeded ({response.pagination.total}).',
        )

    manifest = []
    search_includes = ['entry_id', 'upload_id', 'parser_name']

    required_reader = _validate_required(required, user=user)

    # a generator of StreamedFile objects to create the zipstream from
    def streamed_files():
        # go through all entries that match the query
        for entry_metadata in _do_exhaustive_search(
            owner, query, required=MetadataRequired(include=search_includes), user=user
        ):
            path = os.path.join(
                entry_metadata['upload_id'], f'{entry_metadata["entry_id"]}.json'
            )
            try:
                archive_data = _read_archive(entry_metadata, uploads, required_reader)

                f = io.BytesIO(
                    orjson.dumps(  # pylint: disable=maybe-no-member
                        archive_data,
                        option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS,
                    )
                )  # pylint: disable=maybe-no-member

                yield StreamedFile(path=path, src=f, size=f.getbuffer().nbytes)
            except KeyError as e:
                logger.error(
                    'missing archive', entry_id=entry_metadata['entry_id'], exc_info=e
                )

            entry_metadata['path'] = path
            manifest.append(entry_metadata)

        # add the manifest at the end
        manifest_content = json.dumps(manifest, indent=2).encode()
        yield StreamedFile(
            path='manifest.json',
            src=io.BytesIO(manifest_content),
            size=len(manifest_content),
        )

    with _Uploads() as uploads:
        return StreamingResponse(
            create_zipstream_async(
                streamed_files(),
                compress=files_params.compress
                if files_params.compress is not None
                else False,
            ),
            headers=browser_download_headers(
                filename='archives.zip', media_type='application/zip'
            ),
        )


_entries_archive_download_docstring = strip(
    """
    This operation will perform a search with the given `query` and `owner` and stream
    a .zip-file with the full archive contents for all matching entries. This is not
    paginated. Look at the body schema or parameter documentation for more details.
    """
)


@router.post(
    '/archive/download/query',
    tags=[APITag.ARCHIVE],
    summary='Search entries and download their archives',
    description=_entries_archive_download_docstring,
    response_class=StreamingResponse,
    responses=create_responses(
        _archives_download_response,
        _bad_owner_response_unauthorized,
        _bad_archive_required_response,
    ),
)
def post_entries_archive_download_query(
    data: EntriesArchiveDownload,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    return _answer_entries_archive_download_request(
        owner=data.owner if data.owner is not None else Owner.public,
        query=data.query,
        required=data.required,
        files=data.files if data.files is not None else Files(),
        user=user,
    )


@router.get(
    '/archive/download',
    tags=[APITag.ARCHIVE],
    summary='Search entries and download their archives',
    description=_entries_archive_download_docstring,
    response_class=StreamingResponse,
    responses=create_responses(
        _archives_download_response,
        _bad_owner_response_unauthorized,
        _bad_archive_required_response,
    ),
)
def get_entries_archive_download(
    with_query: Annotated[WithQuery, Depends(query_parameters)],
    files: Annotated[Files, Depends(files_parameters)],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    return _answer_entries_archive_download_request(
        owner=with_query.owner if with_query.owner is not None else Owner.public,
        query=with_query.query,
        required='*',
        files=files,
        user=user,
    )


@router.get(
    '/{entry_id}',
    tags=[APITag.METADATA],
    summary='Get the metadata of an entry by its id',
    response_model=EntryMetadataResponse,
    responses=create_responses(_bad_id_response),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_entry_metadata(
    entry_id: Annotated[
        str,
        Path(description='The unique entry id of the entry to retrieve metadata from.'),
    ],
    required: Annotated[MetadataRequired, Depends(metadata_required_parameters)],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    """
    Retrives the entry metadata for the given id.
    """

    query = {'entry_id': entry_id}
    response = perform_search(
        owner=Owner.all_,
        query=query,
        required=required,
        user_id=user.user_id if user is not None else None,
    )

    if response.pagination.total == 0:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail='The entry with the given id does not exist or is not visible to you.',
        )

    return {'entry_id': entry_id, 'required': required, 'data': response.data[0]}


@router.get(
    '/{entry_id}/rawdir',
    tags=[APITag.RAW],
    summary='Get the raw files metadata for an entry by its id',
    response_model=EntryRawDirResponse,
    responses=create_responses(_bad_id_response),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_entry_rawdir(
    entry_id: Annotated[
        str,
        Path(description='The unique entry id of the entry to retrieve raw data from.'),
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    """
    Returns the file metadata for all input and output files (including auxiliary files)
    of the given `entry_id`. The first file will be the *mainfile*.
    """
    query = dict(entry_id=entry_id)
    entry_metadata = _resolve_entry_metadata_from_mongo(query, user)
    if entry_metadata is None:
        response = perform_search(
            owner=Owner.visible,
            query=query,
            required=MetadataRequired(include=['entry_id', 'upload_id', 'mainfile']),
            user_id=user.user_id if user is not None else None,
        )
        if response.pagination.total == 0:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail='The entry with the given id does not exist or is not visible to you.',
            )
        entry_metadata = response.data[0]

    with _Uploads() as uploads:
        return EntryRawDirResponse(
            entry_id=entry_id, data=_create_entry_rawdir(entry_metadata, uploads)
        )


@router.get(
    '/{entry_id}/raw',
    tags=[APITag.RAW],
    summary='Get the raw data of an entry by its id',
    response_class=StreamingResponse,
    responses=create_responses(_bad_id_response, _raw_response),
)
def get_entry_raw(
    entry_id: Annotated[
        str,
        Path(description='The unique entry id of the entry to retrieve raw data from.'),
    ],
    files: Annotated[Files, Depends(files_parameters)],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    """
    Streams a .zip file with the raw files from the requested entry.
    """
    query = dict(entry_id=entry_id)
    entry_metadata = _resolve_entry_metadata_from_mongo(query, user)
    if entry_metadata is None:
        response = perform_search(
            owner=Owner.visible,
            query=query,
            required=MetadataRequired(include=['entry_id']),
            user_id=user.user_id if user is not None else None,
        )
        if response.pagination.total == 0:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail='The entry with the given id does not exist or is not visible to you.',
            )

    return _answer_entries_raw_request(
        owner=Owner.visible,
        query=query,
        files=files,
        user=user,
        entry_metadata=entry_metadata,
    )


@router.get(
    '/{entry_id}/raw/{path}',
    tags=[APITag.RAW],
    summary='Get the raw data of an entry by its id',
    response_class=StreamingResponse,
    responses=create_responses(
        _bad_id_response, _bad_path_response, _raw_file_response
    ),
)
def get_entry_raw_file(
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
    entry_id: Annotated[
        str,
        Path(description='The unique entry id of the entry to retrieve raw data from.'),
    ],
    path: Annotated[
        str,
        Path(
            description="A relative path to a file based on the directory of the entry's mainfile."
        ),
    ],
    offset: Annotated[
        int | None,
        QueryParameter(
            ge=0,
            description=strip(
                """
                Integer offset that marks the start of the contents to retrieve. Default
                is the start of the file."""
            ),
        ),
    ] = 0,
    length: Annotated[
        int | None,
        QueryParameter(
            ge=-1,
            description=strip(
                """
                The amounts of contents in bytes to stream. By default, the remainder of
                the file is streamed."""
            ),
        ),
    ] = -1,
    decompress: Annotated[
        bool | None,
        QueryParameter(
            description=strip(
                """
                Attempt to decompress the contents, if the file is .gz or .xz."""
            )
        ),
    ] = False,
):
    """
    Streams the contents of an individual file from the requested entry.
    """
    query = dict(entry_id=entry_id)
    entry_metadata = _resolve_entry_metadata_from_mongo(query, user)
    if entry_metadata is None:
        response = perform_search(
            owner=Owner.visible,
            query=query,
            required=MetadataRequired(include=['entry_id', 'upload_id', 'mainfile']),
            user_id=user.user_id if user is not None else None,
        )
        if response.pagination.total == 0:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail='The entry with the given id does not exist or is not visible to you.',
            )
        entry_metadata = response.data[0]

    upload_id, mainfile = entry_metadata['upload_id'], entry_metadata['mainfile']
    # The user is allowed to access all files, because the entry is in the "visible" scope
    upload_files = files.UploadFiles.get(upload_id)
    if upload_files is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail='Upload files not found.',
        )

    entry_path = os.path.dirname(mainfile)
    path = os.path.join(entry_path, path)

    if not upload_files.raw_exists(path):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail='The requested file does not exist.',
        )
    # We only provide a specific mime-type, if the whole file is requested. Otherwise,
    # it is unlikely that the provided contents will match the overall file mime-type.
    mime_type = 'application/octet-stream'
    if offset == 0 and (length is None or length < 0):
        mime_type = upload_files.raw_file_mime_type(path)

    return StreamingResponse(
        create_download_stream_raw_file(
            upload_files,
            path,
            offset if offset is not None else 0,
            length if length is not None else -1,
            decompress,
        ),
        media_type=mime_type,
    )


@traced(span_name='entries.answer_entry_archive_request')
def answer_entry_archive_request(
    query: dict, required: ArchiveRequired, user: User, entry_metadata=None
):
    required_reader = _validate_required(required, user)

    if not entry_metadata:
        entry_metadata = _resolve_entry_metadata_from_mongo(query, user)

    if not entry_metadata:
        response = perform_search(
            owner=Owner.visible,
            query=query,
            required=MetadataRequired(include=['entry_id', 'upload_id', 'parser_name']),
            user_id=user.user_id if user is not None else None,
        )

        if response.pagination.total == 0:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail='The entry does not exist or is not visible to you.',
            )

        entry_metadata = response.data[0]

    entry_id = entry_metadata['entry_id']

    with _Uploads() as uploads:
        try:
            return {
                'entry_id': entry_id,
                'required': required,
                'data': {
                    'entry_id': entry_id,
                    'upload_id': entry_metadata['upload_id'],
                    'parser_name': entry_metadata['parser_name'],
                    'archive': _read_archive(entry_metadata, uploads, required_reader)[
                        'archive'
                    ],
                },
            }
        except KeyError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail='The entry does exist, but it has no archive.',
            )


@router.post(
    '/{entry_id}/edit',
    tags=[APITag.RAW],
    summary='Edit a raw mainfile in archive format.',
    response_model=EntryEditResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    responses=create_responses(
        _bad_id_response,
        _bad_edit_request,
        _bad_edit_request_forbidden,
        _bad_edit_request_unauthorized,
    ),
)
@traced(span_name='entries.post_entry_edit')
def post_entry_edit(
    data: EntryEdit,
    entry_id: Annotated[
        str, Path(description='The unique entry id of the entry to edit.')
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_WRITE])),
    ],
):
    response = perform_search(
        owner=Owner.all_,
        query={'entry_id': entry_id},
        required=MetadataRequired(
            include=['writers', 'writer_groups', 'mainfile', 'upload_id', 'published']
        ),
        user_id=user.user_id if user is not None else None,
    )

    if response.pagination.total == 0:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail='The entry with the given id does not exist or is not visible to you.',
        )

    is_admin = user.is_admin
    entry_data = response.data[0]
    writers = [writer['user_id'] for writer in entry_data.get('writers', [])]
    writer_groups = response.data[0].get('writer_groups', [])
    is_writer = user.user_id in writers or not set(
        MongoUserGroup.get_ids_by_user_id(user.user_id)
    ).isdisjoint(writer_groups)

    if not (is_admin or is_writer):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail='Not enough permissions to execute edit request.',
        )

    if entry_data.get('published', False):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail='Editing is only allowed for non published entries.',
        )

    mainfile = entry_data.get('mainfile')
    upload_id = entry_data.get('upload_id')
    upload = Upload.get(upload_id)
    context = ServerContext(upload)
    archive_data: dict | None = None
    with context.raw_file(mainfile, 'rt') as f:
        if mainfile.endswith('.archive.json'):
            archive_data = json.load(f)
        elif mainfile.endswith('.archive.yaml') or mainfile.endswith('.archive.yml'):
            archive_data = yaml.load(f, Loader=yaml.SafeLoader)
        else:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail='The entry mainfile in not in archive format.',
            )

    # Apply changes directly to the raw dict – no full archive deserialisation.
    # This context-only archive resolves relative m_def references to local schemas.
    # TODO no handling of concurrent changes yet
    archive = datamodel.EntryArchive(m_context=context)
    for change in data.changes:
        _apply_archive_change_to_dict(archive_data, change, archive=archive)

    reprocess_settings = Reprocess(
        index_individual_entries=True, reprocess_existing_entries=True
    )

    # We write the edit to a temporary file first because put_file_and_process_local
    # truncates existing files to 0 bytes when the source and target are the same path.
    with tempfile.TemporaryDirectory(dir=config.fs.tmp) as tmp_dir:
        tmp_path = os.path.join(tmp_dir, os.path.basename(mainfile))
        with open(tmp_path, 'w') as f:
            if mainfile.endswith('.json'):
                json.dump(archive_data, f)
            else:
                yaml.dump(archive_data, f, default_flow_style=False, sort_keys=False)

        main_entry = upload.put_file_and_process_local(
            tmp_path,
            os.path.dirname(mainfile),
            reprocess_settings=reprocess_settings,
        )

    entry_id = main_entry.entry_id if main_entry else entry_id

    return {'entry_id': entry_id, 'changes': data.changes}


@router.get(
    '/{entry_id}/archive',
    tags=[APITag.ARCHIVE],
    summary='Get the archive for an entry by its id',
    response_model=EntryArchiveResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    responses=create_responses(_bad_id_response),
)
def get_entry_archive(
    entry_id: Annotated[
        str,
        Path(
            description='The unique entry id of the entry to retrieve archive data from.'
        ),
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    """
    Returns the full archive for the given `entry_id`.
    """
    return ORJSONResponse(
        answer_entry_archive_request(dict(entry_id=entry_id), required='*', user=user)
    )


@router.get(
    '/{entry_id}/archive/download',
    tags=[APITag.ARCHIVE],
    summary='Get the archive for an entry by its id as plain archive json',
    responses=create_responses(_bad_id_response, _archive_download_response),
)
def get_entry_archive_download(
    entry_id: Annotated[
        str,
        Path(
            description='The unique entry id of the entry to retrieve archive data from.'
        ),
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
):
    """
    Returns the full archive for the given `entry_id`.
    """
    response = answer_entry_archive_request(
        dict(entry_id=entry_id), required='*', user=user
    )
    return ORJSONResponse(
        response['data']['archive'],
        media_type='application/json',
        headers={
            'Content-Disposition': f'attachment; filename="{entry_id}.archive.json"',
            'Access-Control-Expose-Headers': 'Content-Disposition',
        },
    )


@router.post(
    '/{entry_id}/archive/query',
    tags=[APITag.ARCHIVE],
    summary='Get the archive for an entry by its id',
    response_model=EntryArchiveResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    responses=create_responses(_bad_id_response, _bad_archive_required_response),
)
def post_entry_archive_query(
    data: EntryArchiveRequest,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_READ])),
    ],
    entry_id: Annotated[
        str,
        Path(
            description='The unique entry id of the entry to retrieve archive data from.'
        ),
    ],
):
    """
    Returns a partial archive for the given `entry_id` based on the `required` specified
    in the body.
    """
    return ORJSONResponse(
        answer_entry_archive_request(
            dict(entry_id=entry_id), required=data.required, user=user
        )
    )


def edit(
    query: Query, user: User, mongo_update: dict[str, Any] | None = None, re_index=True
) -> list[str]:
    # get all entries that have to change
    entry_ids: list[str] = []
    upload_ids: set[str] = set()
    with utils.timer(logger, 'edit query executed'):
        all_entries = _do_exhaustive_search(
            owner=Owner.user,
            query=query,
            required=MetadataRequired(include=['entry_id', 'upload_id']),
            user=user,
        )

        for entry_dict in all_entries:
            entry_ids.append(entry_dict['entry_id'])
            upload_ids.add(entry_dict['upload_id'])

    # perform the update on the mongo db
    with utils.timer(logger, 'edit mongo update executed', size=len(entry_ids)):
        if mongo_update is not None:
            n_updated = proc.Entry.objects(entry_id__in=entry_ids).update(  # type: ignore
                multi=True, **mongo_update
            )
            if n_updated != len(entry_ids):
                logger.error(
                    'edit repo did not update all entries', payload=mongo_update
                )

    # re-index the affected entries in elastic search
    with utils.timer(logger, 'edit elastic update executed', size=len(entry_ids)):
        if re_index:
            updated_metadata: list[datamodel.EntryMetadata] = []
            for entry in proc.Entry.objects(entry_id__in=entry_ids):  # type: ignore
                entry_metadata = entry.mongo_metadata(entry.upload)
                # Ensure that updated fields are marked as "set", even if they are cleared
                entry_metadata.m_update_from_dict(mongo_update, force_none=True)
                # Add to list
                updated_metadata.append(entry_metadata)

            failed = es_update_metadata(updated_metadata, refresh=True)

            if failed > 0:
                logger.error(
                    'edit repo with failed elastic updates',
                    payload=mongo_update,
                    nfailed=failed,
                )

    return list(upload_ids)


def get_quantity_values(quantity, **kwargs):
    """
    Performs the search defined by `kwargs`, aggregated by quantity, and returns the encountered
    values of this quantity.
    """
    response = perform_search(
        **kwargs,
        aggregations=dict(agg=Aggregation(terms=TermsAggregation(quantity=quantity))),
        pagination=Pagination(page_size=0),
    )
    terms = response.aggregations['agg'].terms  # pylint: disable=no-member
    return [bucket.value for bucket in terms.data]


_editable_quantities = {
    quantity.name: quantity for quantity in EditableUserMetadata.m_def.definitions
}


@router.post(
    '/edit_v0',
    tags=[APITag.METADATA],
    summary='Edit the user metadata of a set of entries',
    response_model=EntryMetadataEditResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    responses=create_responses(_bad_metadata_edit_response),
)
def post_entry_metadata_edit(
    response: Response,
    data: EntryMetadataEdit,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_WRITE])),
    ],
):
    """
    Performs or validates edit actions on a set of entries that match a given query.
    """

    # checking the edit actions and preparing a mongo update on the fly
    query = data.query
    data = EntryMetadataEditResponse(**data.model_dump())
    data.query = query  # to dict from dict does not work with the op aliases in queries
    actions = data.actions
    verify = data.verify
    data.success = True
    mongo_update = {}
    main_author_ids = None
    has_error = False
    removed_datasets = None

    with utils.timer(logger, 'edit verified'):
        for action_quantity_name in actions.dict():  # type: ignore
            quantity_actions = getattr(actions, action_quantity_name, None)
            if quantity_actions is None:
                continue

            quantity = _editable_quantities.get(action_quantity_name)
            if quantity is None:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=f'Unknown quantity {action_quantity_name}',
                )

            # TODO this does not work. Because the quantities are not in EditableUserMetadata
            # they are also not in the model and ignored by fastapi. This probably
            # also did not work in the old API.
            if action_quantity_name in ['main_author', 'upload_create_time']:
                if not user.is_admin():
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail=f'Only the admin user can set {quantity.name}',
                    )

            if isinstance(quantity_actions, list) == quantity.is_scalar:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=f'Wrong shape for quantity {action_quantity_name}',
                )

            if not isinstance(quantity_actions, list):
                quantity_actions = [quantity_actions]

            verify_reference = None
            if isinstance(quantity.type, metainfo.Reference):
                verify_reference = quantity.type.target_section_def.section_cls
            mongo_key = quantity.name
            has_error = False
            for action in quantity_actions:
                action.success = True
                action.message = None
                action_value = action.value
                action_value = (
                    action_value if action_value is None else action_value.strip()
                )

                if action_quantity_name == 'with_embargo':
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        detail='Updating the embargo flag on entry level is no longer allowed.',
                    )

                if action_value is None:
                    mongo_value = None

                elif action_value == '':
                    mongo_value = None

                elif verify_reference in [datamodel.User, datamodel.Author]:
                    try:
                        mongo_value = datamodel.User.get(user_id=action_value).user_id
                    except KeyError:
                        action.success = False
                        has_error = True
                        action.message = 'User does not exist'
                        continue

                    if main_author_ids is None:
                        main_author_ids = get_quantity_values(
                            quantity='main_author.user_id',
                            owner=Owner.user,
                            query=data.query,
                            user_id=user.user_id,
                        )
                    if action_value in main_author_ids:
                        action.success = False
                        has_error = True
                        action.message = 'This user is already the main author of an entry in the query'
                        continue

                elif verify_reference == datamodel.Dataset:
                    try:
                        mongo_value = datamodel.Dataset.m_def.a_mongo.get(
                            user_id=user.user_id, dataset_name=action_value
                        ).dataset_id
                    except KeyError:
                        action.message = 'Dataset does not exist and will be created'
                        mongo_value = None
                        if not verify:
                            dataset = datamodel.Dataset(
                                dataset_id=utils.create_uuid(),
                                user_id=user.user_id,
                                dataset_name=action_value,
                                dataset_create_time=datetime.now(timezone.utc),
                            )
                            dataset.a_mongo.create()
                            mongo_value = dataset.dataset_id

                else:
                    mongo_value = action_value

                if len(quantity.shape) == 0:
                    mongo_update[mongo_key] = mongo_value
                else:
                    mongo_values = mongo_update.setdefault(mongo_key, [])
                    if mongo_value is not None:
                        if mongo_value in mongo_values:
                            action.success = False
                            has_error = True
                            action.message = 'Duplicate values are not allowed'
                            continue
                        mongo_values.append(mongo_value)

            if len(quantity_actions) == 0 and len(quantity.shape) > 0:
                mongo_update[mongo_key] = []

            if action_quantity_name == 'datasets':
                # check if datasets edit is allowed and if datasets have to be removed
                old_datasets = get_quantity_values(
                    quantity='datasets.dataset_id',
                    owner=Owner.user,
                    query=data.query,
                    user_id=user.user_id,
                )

                removed_datasets = []
                for dataset_id in old_datasets:
                    if dataset_id not in mongo_update.get(mongo_key, []):
                        removed_datasets.append(dataset_id)

                doi_ds = datamodel.Dataset.m_def.a_mongo.objects(
                    dataset_id__in=removed_datasets, doi__ne=None
                ).first()
                if doi_ds is not None and not user.is_admin:
                    data.success = False
                    data.message = (data.message if data.message else '') + (
                        f'Edit would remove entries from a dataset with DOI ({doi_ds.dataset_name}) '
                    )
                    has_error = True

    # stop here, if client just wants to verify its actions
    if verify:
        return data

    # stop if the action were not ok
    if has_error:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return data

    # perform the change
    mongo_update['last_edit_time'] = datetime.now(timezone.utc)
    edit(data.query, user, mongo_update, True)

    # remove potentially empty old datasets
    if removed_datasets is not None:
        for dataset in removed_datasets:
            if proc.Entry.objects(datasets=dataset).first() is None:  # type: ignore
                datamodel.Dataset.m_def.a_mongo.objects(dataset_id=dataset).delete()

    return data


@router.post(
    '/edit',
    tags=[APITag.METADATA],
    summary='Edit the user metadata of a set of entries',
    response_model=MetadataEditRequest,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    responses=create_responses(
        _bad_edit_request,
        _bad_edit_request_unauthorized,
        _bad_edit_request_forbidden,
        _bad_edit_request_empty_query,
    ),
)
async def post_entries_edit(
    request: Request,
    data: MetadataEditRequest,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.ENTRIES_WRITE], allow_anonymous=False)),
    ],
):
    """
    Updates the metadata of the specified entries.

    **Note:**
      - Only admins can edit some of the fields.
      - Only entry level attributes (like `comment`, `references` etc.) can be set using
        this endpoint; upload level attributes (like `upload_name`, `coauthors`, embargo
        settings, etc) need to be set through the endpoint **uploads/upload_id/edit**.
      - If the upload is published, the only operation permitted using this endpoint is to
        edit the entries in datasets that where created by the current user.
    """
    edit_request_json = await request.json()
    try:
        verified_json = await anyio.to_thread.run_sync(
            functools.partial(
                proc.MetadataEditRequestHandler.edit_metadata,
                edit_request_json,
                None,
                user,
            )
        )
        return verified_json
    except RequestValidationError:
        raise  # A problem which we have handled explicitly. Fastapi does json conversion.
    except Exception as e:
        # The upload is processing or some kind of unexpected error has occured
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
