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

from __future__ import annotations

from contextlib import contextmanager

import pytest

from nomad.graph.graph_reader import EntryReader
from nomad.layouts import (
    build_layout_context,
    calculate_request_from_layout,
    compile_layout,
    create_layout_plan,
    derive_request_from_layout,
    get_layout_query_intent,
    registry,
)


def setup_function():
    registry.reset()


def teardown_function():
    registry.reset()


def test_build_layout_context_merges_extra_context():
    context = build_layout_context(
        {'entry_id': 'entry-id', 'upload_id': 'upload-id'},
        {
            'quantities': ['results.properties.plugin'],
            'sections': ['plugin.schema.Section'],
            'results': {'properties': {'plugin': True}},
        },
    )

    assert context['quantities'] == ['results.properties.plugin']
    assert context['sections'] == ['plugin.schema.Section']
    assert context['results'] == {'properties': {'plugin': True}}


def test_builtin_widget_defaults_are_applied_to_derived_requests():
    compiled = compile_layout({'type': 'workflow'}, {})
    derived_request: dict = {}
    calculate_request_from_layout(compiled, derived_request)

    reference_request = {
        'm_request': {
            'directive': 'plain',
            'always_rewrite_references': True,
        }
    }
    link_request = {'name': '*', 'section': reference_request}
    assert derived_request['workflow2'] == {
        'name': '*',
        'inputs': link_request,
        'outputs': link_request,
        'tasks': {
            'name': '*',
            'section': reference_request,
            'task': reference_request,
            'inputs': link_request,
            'outputs': link_request,
        },
    }


def test_entry_layout_helpers_only_require_search_when_needed():
    intent = get_layout_query_intent({'entry_id': '*'})
    assert not intent.requires_search_metadata
    assert not intent.requires_layout_resolution

    intent = get_layout_query_intent({'results': '*'})
    assert intent.requires_search_metadata
    assert not intent.requires_layout_resolution

    layout_resolution_request = {'matching_layouts': '*'}
    intent = get_layout_query_intent(layout_resolution_request)
    assert intent.requires_search_metadata
    assert intent.requires_layout_resolution

    auto_archive_request = {'archive': {'m_request': {'directive': 'auto_from_layout'}}}
    intent = get_layout_query_intent(auto_archive_request)
    assert intent.requires_search_metadata
    assert intent.requires_layout_resolution


def test_default_layout_keeps_top_level_data_expanded_with_figures():
    derived_request = derive_request_from_layout(
        'default',
        {
            'quantities': ['data', 'data.figures'],
            'sections': [],
            'results': {},
        },
    )

    assert derived_request['data']['m_request']['directive'] == 'plain'
    assert derived_request['data']['m_request']['include_definition'] == 'both'
    assert derived_request['data']['m_request']['m_def_format'] == 'short'
    assert derived_request['data']['m_request']['depth'] == 2
    assert 'exclude' not in derived_request['data']['m_request']
    assert derived_request['data']['m_def']['m_request']['m_def_format'] == 'short'
    assert derived_request['data']['figures'] == '*'


def test_layout_plan_returns_compiled_layouts_and_selected_request():
    plan = create_layout_plan(
        {
            'quantities': ['data', 'data.figures', 'workflow2'],
            'sections': [],
            'results': {},
        }
    )

    assert plan.default_layout_id == 'default'
    assert plan.resolved_layout_id == 'default'
    assert [layout['id'] for layout in plan.matching_layouts] == ['default']
    assert plan.matching_layouts[0]['overview']['type'] == 'container'
    assert plan.archive_request['data']['figures'] == '*'
    assert plan.archive_request['workflow2']['tasks']['task']['m_request'] == {
        'directive': 'plain',
        'always_rewrite_references': True,
    }
    assert 'request' not in str(plan.matching_layouts[0]['overview'])


def test_layout_plan_uses_requested_matching_layout_instead_of_default():
    plan = create_layout_plan(
        {
            'quantities': [
                'results.method.simulation.program_name',
                'results.properties.catalytic',
            ],
            'sections': [],
            'results': {},
        },
        requested_layout_id='catalysis',
    )

    assert [layout['id'] for layout in plan.matching_layouts] == [
        'simulation',
        'catalysis',
    ]
    assert plan.default_layout_id == 'simulation'
    assert plan.resolved_layout_id == 'catalysis'


@pytest.mark.parametrize('layout_id', ['missing', 'simulation'])
def test_layout_plan_rejects_unknown_or_non_matching_layout(layout_id):
    with pytest.raises(ValueError):
        create_layout_plan({}, requested_layout_id=layout_id)


def test_entry_layout_read_opens_archive_once(monkeypatch):
    required = {
        'matching_layouts': '*',
        'resolved_layout_id': '*',
        'archive': {'m_request': {'directive': 'auto_from_layout'}},
    }
    reader = EntryReader(required)
    archive = {
        'metadata': {
            'entry_id': 'entry-id',
            'upload_id': 'upload-id',
            'quantities': [],
            'sections': [],
            'results': {},
        }
    }
    opens = 0

    async def retrieve_entry(_entry_id):
        return {'entry_id': 'entry-id', 'upload_id': 'upload-id'}

    @contextmanager
    def load_archive(_upload_id, _entry_id):
        nonlocal opens
        opens += 1
        yield archive

    async def read_layout_archive(_archive, _request):
        return {'metadata': archive['metadata']}

    monkeypatch.setattr(reader, 'retrieve_entry', retrieve_entry)
    monkeypatch.setattr(reader, 'load_archive', load_archive)
    monkeypatch.setattr(reader, '_read_layout_archive', read_layout_archive)

    response = reader.sync_read('entry-id')

    assert opens == 1
    assert response['resolved_layout_id'] == 'default'
    assert response['matching_layouts'][0]['id'] == 'default'
