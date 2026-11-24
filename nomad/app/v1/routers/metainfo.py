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

from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, Field

from nomad.app.v1.models import HTTPExceptionModel
from nomad.app.v1.routers.auth import get_current_user
from nomad.app.v1.utils import create_responses
from nomad.auth.scopes import Scope
from nomad.mongo.package import PackageDefinition
from nomad.utils import strip

from ..models import User

#
# FastAPI router for the metainfo API.
#


router = APIRouter()


class APITag(StrEnum):
    DEFAULT = 'metainfo'


class PackageDefinitionResponse(BaseModel):
    entry_id: str | None = Field(
        None, description='The entry ID of the upload that contains the package.'
    )
    upload_id: str | None = Field(
        None, description='The upload ID of the upload that contains the package.'
    )
    snapshot_package_id: str | None = Field(
        None, description='The sha1 based 40-digit long unique ID for the package.'
    )
    snapshot_section_id: str | None = Field(
        None, description='The section definition ID to be used to retrieve package.'
    )
    snapshot_section_ids: list | None = Field(
        None, description='A list of section unique IDs defined in this package.'
    )
    data: dict | None = Field(
        None, description='The JSON representation of the package.'
    )


@router.get(
    '/{definition_id}',
    tags=[APITag.DEFAULT],
    summary='Get the definition of package that contains the target ID based section definition.',
    response_model=PackageDefinitionResponse,
    responses=create_responses(
        (
            status.HTTP_404_NOT_FOUND,
            {
                'model': HTTPExceptionModel,
                'description': strip(
                    """Package not found. The given section definition is not contained in any packages."""
                ),
            },
        )
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_package_definition(
    definition_id: Annotated[
        str,
        Path(
            regex=PackageDefinition.id_pattern,
            description='The section definition id to be used to retrieve package.',
        ),
    ],
    _user: Annotated[User, Depends(get_current_user([Scope.METAINFO_READ]))],
):
    """
    Retrieve the package that contains the target section.
    """
    mongo_package = PackageDefinition.get_by(definition_id)

    return PackageDefinitionResponse(
        entry_id=mongo_package['entry_id'],
        upload_id=mongo_package['upload_id'],
        snapshot_package_id=mongo_package['snapshot_package_id'],
        snapshot_section_id=definition_id,
        snapshot_section_ids=mongo_package['snapshot_section_ids'],
        data=mongo_package['package_definition'],
    )
