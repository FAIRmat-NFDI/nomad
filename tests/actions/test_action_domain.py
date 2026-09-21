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

import pytest

from nomad.actions.domain import (
    ActionNotRunningError,
    is_active_status,
    require_active_status,
)


@pytest.mark.parametrize('status', ['PENDING', 'RUNNING'])
def test_active_action_status(status):
    assert is_active_status(status)
    require_active_status(status)


@pytest.mark.parametrize(
    'status', ['COMPLETED', 'FAILED', 'CANCELED', 'TERMINATED', 'TIMED_OUT', 'UNKNOWN']
)
def test_inactive_action_status_cannot_be_stopped(status):
    assert not is_active_status(status)
    with pytest.raises(ActionNotRunningError, match='Action is not running'):
        require_active_status(status)
