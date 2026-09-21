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

"""
API endpoint that deliver backend configuration details.
"""

from enum import Enum
from typing import Annotated

from fastapi import Depends
from fastapi.routing import APIRouter
from pydantic.fields import Field
from pydantic.main import BaseModel

from nomad.app.v1.routers.auth import get_current_user
from nomad.auth.scopes import Scope
from nomad.config.models.plugins import PluginPackage
from nomad.models.common import UTCDateTime
from nomad.server_stats import load_server_stats, uncached_info_payload
from nomad.utils import strip

from ..models import User

router = APIRouter()


class APITag(str, Enum):
    DEFAULT = 'info'


class MetainfoModel(BaseModel):
    all_package: str | None = Field(
        None,
        description=strip(
            """
        Name of the metainfo package that references all available packages, i.e.
        the complete metainfo."""
        ),
    )

    root_section: str | None = Field(
        None,
        description=strip(
            """
        Name of the topmost section, e.g. section run for computational material science
        data."""
        ),
    )


class StatisticsModel(BaseModel):
    n_entries: int | None = Field(None, description='Number of entries in NOMAD')
    n_uploads: int | None = Field(None, description='Number of uploads in NOMAD')
    n_quantities: int | None = Field(
        None,
        description='Accumulated number of quantities over all entries in the Archive',
    )
    n_calculations: int | None = Field(
        None,
        description='Accumulated number of calculations, e.g. total energy calculations in the Archive',
    )
    n_materials: int | None = Field(None, description='Number of materials in NOMAD')
    public_data_size: int | None = Field(
        None, description='Total size of public data (in bytes) in NOMAD'
    )


class CodeInfoModel(BaseModel):
    code_name: str | None = Field(None, description='Name of the code or input format')
    code_homepage: str | None = Field(
        None, description='Homepage of the code or input format'
    )


class InfoModel(BaseModel):
    collect_time: UTCDateTime | None = Field(
        None, description='When the cached server statistics were last collected.'
    )
    parsers: list[str]
    metainfo_packages: list[str]
    codes: list[CodeInfoModel]
    normalizers: list[str]
    plugin_entry_points: list[dict] | None = Field(
        None,
        description='List of plugin entry points that are activated in this deployment.',
    )
    plugin_packages: list[PluginPackage] | None = Field(
        None,
        description='List of plugin packages that are installed in this deployment.',
    )
    statistics: StatisticsModel | None = Field(
        None, description='General NOMAD statistics'
    )
    search_quantities: dict | None = Field(None, deprecated=True)
    version: str
    deployment: str
    oasis: bool
    # TODO this should be removed in later releases, once most regular NOMAD users
    # should have switched to a new GUI version.
    git: dict | None = Field(
        None,
        deprecated=True,
        description=strip(
            """
        A deprecated field that always contains an empty value to retain some compatibility
        with older GUIs.
    """
        ),
    )


@router.get(
    '',
    tags=[APITag.DEFAULT],
    summary='Get information about the nomad backend and its configuration',
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
    response_model=InfoModel,
)
def get_info(
    _user: Annotated[
        User,
        Depends(get_current_user([Scope.INFO_READ])),
    ],
):
    """Return cached information about the nomad backend.

    This endpoint never collects statistics. It only reads the snapshot written
    by the Temporal server-stats workflow.
    """
    payload = load_server_stats()
    if payload is None:
        return uncached_info_payload()
    return payload
