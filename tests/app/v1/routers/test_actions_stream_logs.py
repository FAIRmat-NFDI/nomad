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

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

from nomad.app.v1.routers import actions


@pytest.mark.asyncio
async def test_stream_logs_negative_offset_lines_starts_from_calculated_line(
    tmp_path, monkeypatch
):
    log_file = tmp_path / 'stream.log'
    log_file.write_text('line-1\nline-2\nline-3\n')

    # Should not be needed for first tail chunk, but keep stream safely terminable.
    mock_status = MagicMock()
    type(mock_status).name = PropertyMock(return_value='SUCCESS')

    async def mock_get_action_status(**_):
        return mock_status

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_get_status',
        mock_get_action_status,
    )

    generator = actions.stream_logs(
        str(log_file),
        action_instance_id='id',
        user_id='user',
        first_line=2,
        offset_lines=-2,
    )
    second = await anext(generator)
    third = await anext(generator)
    await generator.aclose()

    assert second == 'line-2\n'
    assert third == 'line-3\n'


@pytest.mark.asyncio
async def test_stream_logs_positive_offset_lines_starts_from_line_index(
    tmp_path: Path, monkeypatch
):
    log_file = tmp_path / 'stream.log'
    log_file.write_text('line-1\nline-2\nline-3\n')

    mock_status = MagicMock()
    type(mock_status).name = PropertyMock(return_value='SUCCESS')

    async def mock_get_action_status(**_):
        return mock_status

    monkeypatch.setattr(
        'nomad.app.v1.routers.actions.action_service.a_get_status',
        mock_get_action_status,
    )

    generator = actions.stream_logs(
        str(log_file),
        action_instance_id='id',
        user_id='user',
        first_line=2,
        offset_lines=1,
    )

    second = await anext(generator)
    third = await anext(generator)
    await generator.aclose()

    assert second == 'line-2\n'
    assert third == 'line-3\n'
