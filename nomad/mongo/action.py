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

from datetime import datetime
from typing import Annotated, Any

from beanie import Document, Indexed
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from nomad.common import now


class ActionDocument(Document):
    """
    A MongoDB document for storing information about actions.

    Attributes:
        action_id: The ID of the action.
        action_instance_id: The unique ID of the action instance.
        input_data: The input data for the action.
        user_id: The ID of the user who initiated the action.
        upload_id: The ID of the upload associated with the action, if any.
        status: The status of the action.
        results: The results of the action.
        signal_input_requests: List of pending signal input requests.
        created_at: The timestamp when the action was created.
        updated_at: The timestamp when the action was last updated.
    """

    action_id: Annotated[str, Indexed()]
    action_instance_id: Annotated[str, Indexed(unique=True)]
    input_data: Any
    user_id: Annotated[str, Indexed()]
    upload_id: Annotated[str | None, Indexed()] = None
    status: str
    results: Any = None
    signal_input_requests: list[dict] = Field(default_factory=list)
    signal_inputs_submitted: list[dict] = Field(default_factory=list)
    priority_key: int | None = None
    priority_fairness_key: str | None = None
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    class Settings:
        name = 'action_document'
        indexes = [
            # Supports cursor-paginated list queries: WHERE user_id = ? ORDER BY created_at DESC
            IndexModel(
                [('user_id', ASCENDING), ('created_at', DESCENDING)],
                name='user_id_created_at_desc',
            ),
        ]

    async def save(self, *args, **kwargs):
        self.updated_at = now()
        return await super().save(*args, **kwargs)
