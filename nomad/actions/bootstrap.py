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

"""Composition root for the Actions bounded context."""

from nomad.actions.application import ActionCreation, ActionService
from nomad.actions.asset_adapter import ActionAssetAdapter
from nomad.actions.mongo_repository import (
    MongoAsyncActionRepository,
    MongoSyncActionRepository,
)
from nomad.actions.plugin_adapter import PluginActionCatalog
from nomad.actions.ports import AsyncActionRepository, SyncActionRepository
from nomad.actions.workflow_adapter import (
    TemporalAsyncActionWorkflow,
    TemporalSyncActionWorkflow,
)

async_action_repository: AsyncActionRepository = MongoAsyncActionRepository()
sync_action_repository: SyncActionRepository = MongoSyncActionRepository()

creation = ActionCreation(PluginActionCatalog(), ActionAssetAdapter())
workflow = TemporalAsyncActionWorkflow()
action_service = ActionService(
    repository=sync_action_repository,
    workflow=TemporalSyncActionWorkflow(workflow),
    a_repository=async_action_repository,
    a_workflow=workflow,
    creation=creation,
)
