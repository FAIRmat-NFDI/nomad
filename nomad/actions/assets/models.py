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
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionAssetPurpose(StrEnum):
    ACTION_START = 'action_start'
    ACTION_SIGNAL = 'action_signal'


ACTION_ASSET_REF_TYPE: Final = 'action_asset_ref'


class ActionAssetRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    nomad_type: Literal['action_asset_ref'] = Field(alias='_nomad_type')
    filename: str
    media_type: str | None = None
    size: int | None = None
    sha256: str | None = None


class ActionAssetUploadResult(ActionAssetRef):
    purpose: ActionAssetPurpose
    action_id: str | None = None
    action_instance_id: str | None = None
    signal_fn_name: str | None = None
