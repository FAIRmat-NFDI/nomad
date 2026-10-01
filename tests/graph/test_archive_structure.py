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
The structure of an archive without its values: `include_quantities`, `depth`
and `pagination` of the archive reader, as used by the archive tree of the GUI.
"""

import asyncio
import json

import pytest

from nomad.graph.graph_reader import ArchiveReader, ConfigError, GraphNode
from nomad.graph.model import ArchivePagination, RequestConfig
from nomad.metainfo import MSection, Package, Quantity, SubSection

m_package = Package(name='test_archive_structure')


class Leaf(MSection):
    name = Quantity(type=str)
    value = Quantity(type=float)


class Middle(MSection):
    name = Quantity(type=str)
    leaves = SubSection(section_def=Leaf, repeats=True)


class SpecialMiddle(Middle):
    title = Quantity(type=str)
    extra = SubSection(section_def=Leaf)


Wide = type(
    'Wide',
    (MSection,),
    {f's_{i:02d}': SubSection(section_def=Leaf) for i in range(25)},
)


class Root(MSection):
    title = Quantity(type=str)
    wide = SubSection(section_def=Wide)
    items = SubSection(section_def=Middle, repeats=True)
    empty = SubSection(section_def=Leaf, repeats=True)
    single = SubSection(section_def=Leaf)


m_package.__init_metainfo__()

INTERNAL = '__INTERNAL__:'


def _archive():
    items = [{'name': f'item-{i}'} for i in range(25)]
    items[23]['leaves'] = [{'name': f'leaf-{i}', 'value': float(i)} for i in range(3)]
    items[4] = {
        'm_def': SpecialMiddle.m_def.qualified_name(),
        'name': 'special',
        'extra': {'name': 'extra'},
    }
    items[5] = {}
    items[6]['leaves'] = []
    return {
        'title': 'secret title',
        'single': {'name': 'single', 'value': 1.0},
        'wide': {f's_{i:02d}': {'name': f'w-{i}'} for i in range(25)},
        'items': items,
        'empty': [],
    }


def _read(query: dict, archive: dict | None = None) -> tuple[dict, dict[str, set]]:
    archive = _archive() if archive is None else archive
    result: dict = {}
    with ArchiveReader(query) as reader:
        node = GraphNode(
            upload_id='upload',
            entry_id='entry',
            current_path=[],
            result_root=result,
            ref_result_root=result,
            archive=archive,
            archive_root=archive,
            definition=Root.m_def,
            visited_path=set(),
            current_depth=0,
            reader=reader,
        )
        asyncio.run(reader._walk(node, reader.required_query, reader.global_config))
        errors = reader.errors
    return result, errors


def _structure(depth: int = 2, **pagination) -> dict:
    request: dict = {'include_quantities': False, 'depth': depth}
    if pagination:
        request['pagination'] = pagination
    return {'m_request': request}


def _is_stub(value) -> bool:
    return isinstance(value, str) and value.startswith(INTERNAL)


def _page(page_size: int, total: int, page: int = 1) -> dict:
    return {'pagination': {'page': page, 'page_size': page_size, 'total': total}}


def _structure_of(value):
    """The value without the responses of its sections, for structural assertions."""
    if isinstance(value, dict):
        return {k: _structure_of(v) for k, v in value.items() if k != 'm_response'}
    if isinstance(value, list):
        return [_structure_of(v) for v in value]
    return value


@pytest.fixture
def custom_definitions(monkeypatch):
    # resolving custom definitions needs a server context, resolve the python class
    async def retrieve_definition(self, m_def, m_def_id=None, node=None):
        assert m_def == SpecialMiddle.m_def.qualified_name()
        return SpecialMiddle.m_def

    monkeypatch.setattr(ArchiveReader, '_retrieve_definition', retrieve_definition)


def test_structure_without_quantities(custom_definitions):
    result, errors = _read(_structure(depth=2))
    assert not errors

    # only subsections, in archive order
    assert list(result.keys()) == ['single', 'wide', 'items', 'empty']
    # a section without subsections
    assert result['single'] == {}
    # the subsections of a section are walked, their subsections are stubs
    assert list(result['wide'].keys()) == [f's_{i:02d}' for i in range(25)]
    assert all(_is_stub(value) for value in result['wide'].values())
    # items of a repeating subsection do not count as a level
    assert len(result['items']) == 25
    assert result['items'][0] == {}
    assert _is_stub(result['items'][23]['leaves'])
    # a polymorphic item uses its own definition
    assert _is_stub(result['items'][4]['extra'])
    assert result['items'][5] == {}
    # an empty container is returned as is, not as a stub
    assert result['items'][6] == {'leaves': []}
    assert result['empty'] == []

    serialized = json.dumps(result)
    assert 'secret title' not in serialized
    assert 'value' not in serialized
    assert 'm_def' not in serialized


def test_structure_depth_one():
    result, _ = _read(_structure(depth=1))
    assert _is_stub(result['single'])
    assert _is_stub(result['wide'])
    assert _is_stub(result['items'])
    assert result['empty'] == []


def test_empty_containers_are_not_stripped():
    result, _ = _read({'m_request': {'directive': 'plain', 'depth': 1}})
    assert result['title'] == 'secret title'
    assert _is_stub(result['single'])
    assert _is_stub(result['items'])
    assert result['empty'] == []


def test_explicit_quantities_are_returned():
    query = {**_structure(), 'title': {'m_request': {'directive': 'plain'}}}
    result, _ = _read(query)
    assert result['title'] == 'secret title'
    assert 'single' in result


def test_include_quantities_is_inherited_and_can_be_overridden():
    query = {
        **_structure(),
        'single': {'m_request': {'directive': 'plain'}},
        'items[0]': {'m_request': {'include_quantities': True}},
    }
    result, _ = _read(query)
    assert result['single'] == {}
    assert result['items'][0] == {'name': 'item-0'}


def test_include_exclude():
    result, _ = _read(
        {'m_request': {'include_quantities': False, 'include': ['i*', 'w*']}}
    )
    assert list(result.keys()) == ['wide', 'items']

    result, _ = _read(
        {'m_request': {'include_quantities': False, 'exclude': ['items']}}
    )
    assert list(result.keys()) == ['single', 'wide', 'empty']


def test_pagination_applies_to_lists_only():
    # the properties of a section are never paged, its lists are
    result, _ = _read(
        {'m_request': {'directive': 'plain', 'pagination': {'page_size': 2}}}
    )
    assert result.pop('m_response') == {
        'subsection_lists': {'items': _page(2, 25), 'empty': _page(2, 0)}
    }
    assert list(result.keys()) == ['title', 'single', 'wide', 'items', 'empty']
    assert result['single'] == {'name': 'single', 'value': 1.0}
    assert len(result['wide']) == 25
    assert result['items'] == [{'name': 'item-0'}, {'name': 'item-1'}]
    assert result['empty'] == []


def test_list_pagination(custom_definitions):
    result, _ = _read({'items': _structure(depth=1, page_size=20, page_containing=23)})
    # a list cannot carry a response, the parent section reports its page
    assert result['m_response'] == {
        'subsection_lists': {
            'items': {'pagination': {'page': 2, 'page_size': 20, 'total': 25}}
        }
    }
    items = result['items']
    assert len(items) == 25
    assert all(item is None for item in items[:20])
    assert items[20] == {}
    assert _is_stub(items[23]['leaves'])

    result, _ = _read({'items': _structure(depth=1, page_size=7)})
    items = result['items']
    assert len(items) == 7
    assert _is_stub(items[4]['extra'])
    assert items[5] == {}
    # the lists of the items inherit the page size
    assert items[6] == {
        'leaves': [],
        'm_response': {'subsection_lists': {'leaves': _page(7, 0)}},
    }


def test_page_size_propagates_to_children():
    # the page selection does not propagate, the page size does
    result, _ = _read(_structure(depth=2, page_size=3, page=2))
    assert result['m_response'] == {
        'subsection_lists': {'items': _page(3, 25), 'empty': _page(3, 0)}
    }
    assert [key for key in result if not key.startswith('m_')] == [
        'single',
        'wide',
        'items',
        'empty',
    ]
    assert len(result['items']) == 3

    result, _ = _read({'items[23]': _structure(depth=2, page_size=2)})
    assert result['items'][23] == {
        'leaves': [{}, {}],
        'm_response': {'subsection_lists': {'leaves': _page(2, 3)}},
    }


def test_explicit_items_are_walked_once():
    query = {
        'items': _structure(depth=1, page_size=20, page_containing=23),
        'items[23]': _structure(depth=2, page_size=1),
    }
    result, errors = _read(query)
    assert not errors
    item = result['items'][23]
    # the explicit request determines the item, the list walk skips it
    assert item == {
        'leaves': [{}],
        'm_response': {'subsection_lists': {'leaves': _page(1, 3)}},
    }
    assert result['items'][22] == {}


def test_deep_link(custom_definitions):
    """The request of the GUI for the path `items/23/leaves/2`."""
    query = {
        **_structure(depth=2, page_size=20),
        'items': _structure(depth=1, page_size=20, page_containing=23),
        'items[23]': {
            **_structure(depth=2, page_size=20),
            'leaves': _structure(depth=1, page_size=2, page_containing=2),
            'leaves[2]': _structure(depth=2, page_size=20),
        },
    }
    result, errors = _read(query)
    assert not errors

    assert result['m_response'] == {
        'subsection_lists': {
            'items': _page(20, 25, page=2),
            'empty': _page(20, 0),
        },
    }
    assert set(result.keys()) == {'m_response', 'single', 'wide', 'items', 'empty'}

    items = result['items']
    assert all(item is None for item in items[:20])
    assert items[20] == {}
    item = items[23]
    assert item['m_response'] == {
        'subsection_lists': {'leaves': _page(2, 3, page=2)},
    }
    assert item['leaves'] == [None, None, {}]
    assert 'value' not in json.dumps(result)


@pytest.mark.parametrize(
    'request_config',
    [
        pytest.param(
            {'pagination': {'page': 1, 'page_containing': 'x'}},
            id='page-and-containing',
        ),
        pytest.param({'pagination': {'page_size': 0}}, id='page-size'),
        # only the page, its size and the item it contains can be given
        pytest.param({'pagination': {'page_offset': 3}}, id='page-offset'),
        pytest.param({'pagination': {'order_by': 'name'}}, id='order-by'),
        pytest.param({'pagination': {'page_after_value': 'x'}}, id='page-after-value'),
        pytest.param({'query': {'mainfile': ['x']}}, id='query'),
    ],
)
def test_invalid_config(request_config):
    with pytest.raises(ConfigError):
        ArchiveReader({'m_request': request_config})


def test_is_plain():
    assert RequestConfig().is_plain()
    assert not RequestConfig(include_quantities=False).is_plain()
    assert not RequestConfig(pagination={'page_size': 1}).is_plain()


def test_pagination_window():
    pagination = ArchivePagination(page_size=10)
    assert pagination.resolve_window(25) == (
        0,
        10,
        {'page': 1, 'page_size': 10, 'total': 25},
    )
    assert pagination.resolve_window(0) == (
        0,
        0,
        {'page': 1, 'page_size': 10, 'total': 0},
    )
    # the page that contains an item, the first page if there is no such item
    containing = ArchivePagination(page_size=10, page_containing=24)
    assert containing.resolve_window(25) == (
        20,
        25,
        {'page': 3, 'page_size': 10, 'total': 25},
    )
    assert containing.resolve_window(24) == (
        0,
        10,
        {'page': 1, 'page_size': 10, 'total': 24},
    )
    # a page past the end has no items
    assert ArchivePagination(page_size=10, page=5).resolve_window(25) == (
        25,
        25,
        {'page': 5, 'page_size': 10, 'total': 25},
    )
