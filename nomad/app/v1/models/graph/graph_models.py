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

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from nomad.app.v1.models.graph.utils import (
    generate_request_model,
    generate_response_model,
    mapped,
)
from nomad.app.v1.models.models import Metadata, MetadataResponse
from nomad.app.v1.routers.datasets import Dataset as DatasetV1
from nomad.app.v1.routers.datasets import DatasetPagination
from nomad.app.v1.routers.uploads import (
    EntryProcData,
    EntryProcDataPagination,
    PaginationResponse,
    UploadProcData,
    UploadProcDataPagination,
    UploadProcDataQuery,
)
from nomad.datamodel.data import User as UserModel
from nomad.graph.model import (
    DatasetQuery,
    MetainfoPagination,
    MetainfoQuery,
    RequestConfig,
)
from nomad.metainfo.pydantic_extension import PydanticModel

from ..groups import UserGroup, UserGroupMember, UserGroupPagination, UserGroupQuery


class Error(BaseModel):
    error_type: str
    message: str


RecursionOptions = RequestConfig

DirectoryRequestOptions = RequestConfig


class DirectoryResponseOptions(BaseModel):
    pagination: PaginationResponse


class GraphDirectory(BaseModel):
    m_errors: list[Error]
    m_is: Literal['Directory']
    m_request: DirectoryRequestOptions
    m_response: DirectoryResponseOptions
    m_children: GraphDirectory | GraphFile


class GraphFile(BaseModel):
    m_errors: list[Error]
    m_is: Literal['File']
    m_request: DirectoryRequestOptions
    path: str
    size: int
    entry: GraphEntry | None = None
    # The old API also had those, but they can be grabbed from entry:
    # parser_name, entry_id, archive
    # This is similar to the question for "m_parent" in Directory. At least we need
    # to navigate from Entry to mainfile to directory, but we could also but a
    # mainfile_directory into Entry?
    parent: GraphDirectory


class ArchiveListResponseOptions(BaseModel):
    pagination: PaginationResponse


class ArchiveResponseOptions(BaseModel):
    subsection_lists: dict[str, ArchiveListResponseOptions] | None = Field(
        None,
        description="""
        The pages of paginated repeating subsections of this section, by name.
        A list cannot carry an `m_response` of its own.
        """,
    )


class MSection(BaseModel):
    m_errors: list[Error]
    m_request: RecursionOptions
    m_response: ArchiveResponseOptions
    m_def: MDef
    m_children: Any = None


class MDef(MSection):
    m_def: str  # type: ignore
    m_def_id: str


class GraphEntry(mapped(EntryProcData, mainfile='mainfile_path', entry_metadata=None)):  # type: ignore
    m_errors: list[Error]
    mainfile: GraphFile
    upload: GraphUpload
    archive: MSection
    metadata: GraphEntryMetadata
    matching_layouts: list[dict[str, Any]]
    default_layout_id: str
    resolved_layout_id: str
    resolved_archive_request: dict[str, Any]


class EntriesRequestOptions(BaseModel):
    # The old API does not support any queries
    pagination: EntryProcDataPagination | None = None


class EntriesResponseOptions(BaseModel):
    pagination: PaginationResponse | None = None
    # The "upload" was only necessary, because in the old API you would not get the upload.
    # In the graph API, the upload would be the parent anyways
    # upload: Upload


class GraphEntries(BaseModel):
    m_request: EntriesRequestOptions
    m_response: EntriesResponseOptions
    m_children: GraphEntry


class GraphUser(
    UserModel.m_def.m_get_annotation(PydanticModel).model,  # type: ignore
):
    m_request: RecursionOptions
    # This is more complicated as the user can have different roles in different uploads.
    # This would only refer to uploads with the user as main_author.
    # For many clients and use-cases uploads.m_request.query will be the
    # more generic or only option
    uploads: GraphUploads | None
    entries: GraphEntries | None
    datasets: GraphDatasets | None
    model_config = ConfigDict(
        extra='forbid',
    )


class GraphUsers(BaseModel):
    m_errors: list[Error]
    m_children: GraphUser


class GraphUpload(
    mapped(  # type: ignore
        UploadProcData,
        entries='n_entries',
        main_author=GraphUser,
        coauthors=list[GraphUser],
        reviewers=list[GraphUser],
        viewers=list[GraphUser],
        writers=list[GraphUser],
    ),
):
    # The old API includes some extra data here:
    processing_successful: int = Field(
        description='Number of entries that has been processed successfully.'
    )
    processing_failed: int = Field(
        description='Number of entries that failed to process.'
    )

    entries: GraphEntries = Field(description='The entries contained in this upload.')
    files: GraphDirectory = Field(
        description="This upload's root directory for all files (raw data)."
    )

    model_config = ConfigDict(
        extra='forbid',
    )


class UploadRequestOptions(BaseModel):
    pagination: UploadProcDataPagination | None = None
    query: UploadProcDataQuery | None = None


class UploadResponseOptions(BaseModel):
    pagination: PaginationResponse | None = None
    query: UploadProcDataQuery | None = None


class GraphUploads(BaseModel):
    m_request: UploadRequestOptions
    m_response: UploadResponseOptions
    m_errors: list[Error]
    m_children: GraphUpload


class GraphEntryMetadata(BaseModel, extra='allow'):
    entry: GraphEntry
    m_children: Any = None


class SearchRequestOptions(BaseModel):
    query: Metadata | None = None


class SearchResponseOptions(BaseModel):
    query: MetadataResponse | None = None


class GraphSearch(BaseModel):
    m_request: SearchRequestOptions
    m_response: SearchResponseOptions
    m_errors: list[Error]
    m_children: GraphEntryMetadata


class GraphDataset(mapped(DatasetV1, query=None, entries=None)):  # type: ignore
    pass


class DatasetRequestOptions(BaseModel):
    pagination: DatasetPagination | None = None
    query: DatasetQuery | None = None


class DatasetResponseOptions(BaseModel):
    pagination: PaginationResponse | None = None
    query: DatasetQuery | None = None


class GraphDatasets(BaseModel):
    m_request: DatasetRequestOptions
    m_response: DatasetResponseOptions
    m_errors: list[Error]
    m_children: GraphDataset


class MetainfoRequestOptions(BaseModel):
    pagination: MetainfoPagination | None = None
    query: MetainfoQuery | None = None


class MetainfoResponseOptions(BaseModel):
    pagination: PaginationResponse | None = None
    query: MetainfoQuery | None = None


class GraphMetainfo(BaseModel):
    m_request: MetainfoRequestOptions
    m_response: MetainfoResponseOptions
    m_errors: list[Error]
    m_children: MSection


class GraphUserGroupMember(UserGroupMember):
    user: GraphUser


class GraphUserGroup(
    mapped(  # type: ignore
        UserGroup,
        owner=GraphUser,
        members=list[GraphUser],
        members_info=list[GraphUserGroupMember],
    )
):
    m_errors: list[Error]


class GroupRequestOptions(BaseModel):
    pagination: UserGroupPagination | None
    query: UserGroupQuery | None


class GroupResponseOptions(BaseModel):
    pagination: PaginationResponse | None
    query: UserGroupQuery | None


class GraphGroups(BaseModel):
    m_errors: list[Error]
    m_children: GraphUserGroup
    m_request: GroupRequestOptions
    m_response: GroupResponseOptions


class Graph(BaseModel):
    users: GraphUsers
    entries: GraphEntries
    uploads: GraphUploads
    datasets: GraphDatasets
    search: GraphSearch
    metainfo: GraphMetainfo
    groups: GraphGroups


GraphRequest = generate_request_model(Graph)
GraphResponse = generate_response_model(Graph)
