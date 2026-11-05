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

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from nomad.datamodel import EntryArchive
from nomad.utils import get_logger
from tests.normalizing.conftest import run_processing


@pytest.mark.parametrize('subplot', [False, True], ids=['figure', 'subplot'])
@pytest.mark.parametrize('extra_trace', [False, True], ids=['main', 'extra-trace'])
def test_plotly_express_grouped_bars(subplot, extra_trace):
    """Normalization must preserve every categorical Plotly Express trace."""
    mainfile = (
        Path(__file__).parents[2]
        / 'data/datamodel/metainfo/plotly/grouped_bars.archive.yaml'
    )
    archive_dict = yaml.safe_load(mainfile.read_text())
    annotations = archive_dict['definitions']['sections']['GroupedBars'][
        'm_annotations'
    ]
    express = annotations['plotly_express']
    if extra_trace:
        grouped_trace = deepcopy(express)
        express.pop('color')
        express['traces'] = [grouped_trace]
    if subplot:
        annotations['plotly_subplots'] = {
            'parameters': {'rows': 1, 'cols': 1},
            'plotly_express': [annotations.pop('plotly_express')],
            'layout': deepcopy(express['layout']),
        }
    archive = EntryArchive.m_from_dict(archive_dict)

    archive.data.normalize(archive, get_logger(__name__))

    assert len(archive.data.figures) == 1
    figure = archive.data.figures[0].figure
    traces = figure['data']
    if extra_trace:
        assert len(traces) == 3
        assert traces[0]['x'] == [0, 1, 2, 3]
        assert traces[0]['y'] == [2, 5, 3, 4]
        traces = traces[1:]
    assert [trace['name'] for trace in traces] == ['A', 'B']
    for trace, positions, values in zip(
        traces, [[0, 2], [1, 3]], [[2, 3], [5, 4]], strict=True
    ):
        assert trace['type'] == 'bar'
        assert trace['x'] == positions
        assert trace['y'] == values
        assert trace['showlegend'] is True
        if subplot:
            assert trace['xaxis'] == 'x'
            assert trace['yaxis'] == 'y'
    assert figure['layout']['title']['text'] == 'Grouped bars'
    assert figure['layout']['showlegend'] is True


def test_plotly_snapshot(raw_files_function):
    directory = 'tests/data/datamodel/metainfo/plotly'
    mainfile = 'plotly.schema.archive.yaml'
    plotly_archive = run_processing(directory, mainfile)

    f = open('tests/data/datamodel/metainfo/plotly/snapshot.archive.json')
    snapshot = json.load(f)
    f.close()

    figures = plotly_archive.data['figures']
    snapshot_figures = snapshot['figures']
    for i in range(0, 4):
        assert json.dumps(figures[i].figure, sort_keys=True) == json.dumps(
            snapshot_figures[i], sort_keys=True
        )
