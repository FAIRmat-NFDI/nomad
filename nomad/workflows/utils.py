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

import math
from typing import TypeVar

_GENERIC_TEMPORAL_ERROR_MESSAGES = (
    'child workflow failed',
    'child workflow execution failed',
    'workflow execution failed',
    'activity task failed',
    'activity failed',
)
T = TypeVar('T')
MAX_CONCURRENT_ACTIVITIES_PER_WORKFLOW = 1000
CLEANUP_ENTRY_BATCH_SIZE = 100
ENTRY_BATCH_FILE_SIZE = 1000
ENTRY_ACTIVITY_BATCHES_PER_WORKFLOW_RUN = 1000


def generate_batches(
    items: list[T], max_desired_batch_size=1000, max_batches=1000
) -> list[list[T]]:
    """
    Splits a list of items into batches, trying to keep batch size <= max_desired_batch_size,
    while minimizing the number of batches. The number of batches is capped at max_batches.
    """
    total_items = len(items)

    if total_items == 0:
        return []

    # Find the smallest num_batches such that batch_size <= max_desired_batch_size
    for num_batches in range(1, max_batches + 1):
        batch_size = math.ceil(total_items / num_batches)
        if batch_size <= max_desired_batch_size:
            break
    else:
        # If we never found a small enough batch_size, use max_batches
        num_batches = max_batches
        batch_size = math.ceil(total_items / num_batches)

    item_batches = []
    for i in range(num_batches):
        start_idx = i * batch_size
        end_idx = min(start_idx + batch_size, total_items)
        item_batches.append(items[start_idx:end_idx])

    return item_batches


def extract_temporal_error_details(error: Exception) -> str:
    """Return the most specific message from a Temporal error cause chain."""
    messages: list[str] = []
    current: Exception | None = error
    seen: set[int] = set()

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip()
        if message:
            messages.append(message)
        current = getattr(current, 'cause', None)

    if not messages:
        return str(error)

    for message in reversed(messages):
        lowered_message = message.lower()
        if not any(
            generic_message in lowered_message
            for generic_message in _GENERIC_TEMPORAL_ERROR_MESSAGES
        ):
            return message

    return messages[-1]
