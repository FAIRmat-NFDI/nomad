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

"""Filesystem asset implementation of the application asset port."""

from typing import Any

from nomad.actions.assets.models import ActionAssetPurpose
from nomad.actions.assets.service import (
    consume_staged_assets,
    extract_action_asset_refs,
    rollback_consumed_assets,
)


class ActionAssetAdapter:
    def has_assets(self, data: Any) -> bool:
        return bool(extract_action_asset_refs(data))

    async def consume(
        self,
        data: Any,
        *,
        user_id: str,
        instance_id: str,
        action_id: str | None = None,
        signal_fn_name: str | None = None,
    ) -> list[Any]:
        refs = extract_action_asset_refs(data)
        if not refs:
            return []
        return await consume_staged_assets(
            refs=refs,
            user_id=user_id,
            target_action_instance_id=instance_id,
            purpose=ActionAssetPurpose.ACTION_START
            if signal_fn_name is None
            else ActionAssetPurpose.ACTION_SIGNAL,
            **(
                {'action_id': action_id}
                if signal_fn_name is None
                else {'signal_fn_name': signal_fn_name}
            ),
        )

    async def rollback(self, receipt: list[Any]) -> None:
        if receipt:
            await rollback_consumed_assets(receipt)
