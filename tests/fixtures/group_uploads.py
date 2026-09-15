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
Fields:
- C: coauthors (users)
- R: reviewers (users)
- CG: coauthor_groups (groups)
- RG: reviewer_groups (groups)

Values:
- XMN: field X with userM and userN
- XgKLgMN: field X with groupKL and groupMN
"""

from collections.abc import Sequence

import pytest

from nomad.utils.exampledata import ExampleData


@pytest.fixture(scope='session')
def group_upload_molds(
    convert_user_labels_to_ids, convert_group_labels_to_ids, user1, user2
):
    """Return a dict: upload label -> upload data (dict)."""
    default = {'main_author': user1}
    molds = {
        'no_group': {},
        'user2': {'main_author': user2},
        'embargo0': {'published': True, 'embargo_length': 0},
        'embargo3': {'published': True, 'embargo_length': 3},
        'C2': {'coauthors': ['user2']},
        'R2': {'reviewers': ['user2']},
        'CGg1': {'coauthor_groups': ['group1']},
        'CGg2': {'coauthor_groups': ['group2']},
        'CGg123': {'coauthor_groups': ['group123']},
        'CGg12m3': {'coauthor_groups': ['group12m3']},
        'CGd234': {'coauthor_groups': ['dirty234']},
        'RGg1': {'reviewer_groups': ['group1']},
        'RGg2': {'reviewer_groups': ['group2']},
        'RGg123': {'reviewer_groups': ['group123']},
        'RGd234': {'reviewer_groups': ['dirty234']},
        'RGg12m3': {'reviewer_groups': ['group12m3']},
        'RGall': {'reviewer_groups': ['all']},
        'full_agents': {
            'coauthors': ['user2', 'user4'],
            'reviewers': ['user3', 'user5', 'user7'],
            'coauthor_groups': ['group2', 'group8', 'group14'],
            'reviewer_groups': ['group3', 'group8', 'group15'],
        },
    }
    molds = {k: {**default, **v} for k, v in molds.items()}

    for label, mold in molds.items():
        mold['upload_id'] = f'id_{label}'

        convert = convert_user_labels_to_ids
        mold['coauthors'] = convert(mold.get('coauthors', []))
        mold['reviewers'] = convert(mold.get('reviewers', []))

        convert = convert_group_labels_to_ids
        mold['coauthor_groups'] = convert(mold.get('coauthor_groups', []))
        mold['reviewer_groups'] = convert(mold.get('reviewer_groups', []))

    return molds


@pytest.fixture(scope='session')
def create_group_uploads_from_molds(group_upload_molds):
    """Returned function creates and returns uploads with given labels.

    Use `yield from create_group_uploads_from_molds(...)` for the actual upload fixtures.
    """

    def create(labels: Sequence[str], save_kwargs=None):
        data = ExampleData()
        for label in labels:
            mold = group_upload_molds[label]
            upload_id = mold['upload_id']
            entry_id = f'{upload_id}_1'

            data.create_upload(**mold)
            data.create_entry(upload_id=upload_id, entry_id=entry_id)

        if save_kwargs is None:
            save_kwargs = {}

        data.save(**save_kwargs)
        yield data
        data.delete()

    return create


@pytest.fixture(scope='module')
def uploads_get_groups(create_group_uploads_from_molds, elastic_module, groups_module):
    """Create and return uploads for testing get uploads with groups."""
    labels = (
        'no_group',
        'CGg2',
        'RGg2',
        'CGg123',
        'CGg12m3',
        'CGd234',
        'RGg123',
        'RGg12m3',
        'RGd234',
        'RGall',
    )
    yield from create_group_uploads_from_molds(labels)


@pytest.fixture
def upload_full_agents(create_group_uploads_from_molds):
    """Create and return an upload with all coauthor/reviewer user and groups filled."""
    labels = ('full_agents',)
    yield from create_group_uploads_from_molds(labels)


@pytest.fixture(scope='module')
def uploads_agent_write_access(
    create_group_uploads_from_molds,
    elastic_module,
    groups_module,
):
    """Create and return uploads for testing agent write access."""
    labels = (
        'C2',
        'R2',
        'CGg2',
        'RGg2',
        'CGg123',
        'RGg123',
        'CGg12m3',
        'RGg12m3',
        'CGd234',
        'RGd234',
        'RGall',
        'full_agents',
    )
    yield from create_group_uploads_from_molds(labels)


@pytest.fixture(scope='class')
def uploads_search_query_groups(
    create_group_uploads_from_molds,
    elastic_module,
    groups_module,
):
    """Create and return uploads for testing search query with groups."""
    labels = (
        'embargo0',
        'embargo3',
        'no_group',
        'CGg1',
        'RGg1',
        'CGg2',
        'RGg2',
        'CGg123',
        'RGg123',
        'CGg12m3',
        'RGg12m3',
        'CGd234',
        'RGd234',
        'RGall',
        'user2',
    )
    yield from create_group_uploads_from_molds(
        labels, save_kwargs={'with_files': False, 'with_mongo': False}
    )


@pytest.fixture(scope='function')
def uploads_graph_access_via_group(
    create_group_uploads_from_molds,
    elastic_function,
    groups_function,
):
    labels = ('CGg2', 'RGg2')
    yield from create_group_uploads_from_molds(labels)
