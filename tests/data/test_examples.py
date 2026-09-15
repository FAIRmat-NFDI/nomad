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

import asyncio

import pytest

from nomad.processing import Entry, ProcessStatus
from tests.normalizing.conftest import run_processing
from tests.test_utils import create_upload


@pytest.mark.parametrize(
    'mainfile, assert_xpaths',
    [
        pytest.param('schema.archive.yaml', [], id='schema'),
        pytest.param(
            'sample.archive.json', ['data.processes.pvd_evaporation.time'], id='sample'
        ),
        pytest.param('PVD-P.archive.json', [], id='instrument'),
        pytest.param('Zinc_Selenide.archive.json', [], id='chemical'),
    ],
)
def test_eln(mainfile, assert_xpaths, raw_files_function, no_warn):
    mainfile_directory = 'examples/data/eln'
    archive = run_processing(mainfile_directory, mainfile)

    for xpath in assert_xpaths:
        assert archive.m_xpath(xpath) is not None


@pytest.mark.parametrize(
    'mainfile, assert_xpaths',
    [
        pytest.param(
            'tabular-parser-col-mode.archive.yaml', ['data.My_Quantity'], id='col_mode'
        ),
        pytest.param(
            'tabular-parser-row-mode.archive.yaml',
            ['data.My_Subsection.My_Section[4].My_Quantity'],
            id='row_mode',
        ),
    ],
)
def test_sample_tabular(mainfile, assert_xpaths, raw_files_function, no_warn):
    mainfile_directory = 'examples/data/docs'
    archive = run_processing(mainfile_directory, mainfile)

    for xpath in assert_xpaths:
        assert archive.m_xpath(xpath) is not None


_sample_entry_mode_cases = [
    (
        [
            'examples/data/docs/tabular-parser-entry-mode.archive.yaml',
            'examples/data/docs/tabular-parser-entry-mode.xlsx',
        ],
        5,
    ),
    (
        [
            'examples/data/docs/tabular-parser-complex.archive.yaml',
            'examples/data/docs/data_file_1.csv',
            'examples/data/docs/data_file_2.csv',
        ],
        6,
    ),
]


@pytest.mark.asyncio
async def test_sample_entry_mode(
    mongo_function,
    user1,
    raw_files_function,
    monkeypatch,
    temporal_worker,
):
    async with temporal_worker():
        for i, (test_files, number_of_entries) in enumerate(_sample_entry_mode_cases):
            upload_id = f'test_upload_id_{i}'
            upload = await asyncio.to_thread(
                create_upload, upload_id, user1.user_id, test_files
            )
            assert upload is not None
            assert upload.processed_entries_count == number_of_entries

            for entry in Entry.objects(upload_id=upload_id):
                assert entry.process_status == ProcessStatus.SUCCESS


_tabular_doc_examples_cases = [
    (
        [
            'examples/data/docs/tabular-parser_1_column_current-entry_to-root.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_2_column_current-entry_to-path.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_3_row_current-entry_to-path.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_4_column_single-new-entry_to-path.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_5_row_single-new-entry_to-path.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_6_row_multiple-new-entries_to-root.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_7_row_multiple-new-entries_to-path.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_8_row_current-entry_to-path_subsubsection.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_np1_row_current-entry_to-root.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_np2_column_single-new-entry_to-root.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_np3_row_single-new-entry_to-root.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_np4_column_multiple-new-entries_to-root.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
    (
        [
            'examples/data/docs/tabular-parser_np5_column_multiple-new-entries_to-path.archive.yaml',
            'examples/data/docs/test.xlsx',
        ],
        ProcessStatus.SUCCESS,
    ),
]


@pytest.mark.asyncio
async def test_tabular_doc_examples(
    mongo_function,
    user1,
    raw_files_function,
    monkeypatch,
    temporal_worker,
):
    async with temporal_worker():
        for i, (test_files, status) in enumerate(_tabular_doc_examples_cases):
            upload_id = f'test_upload_id_{i}'
            upload = await asyncio.to_thread(
                create_upload, upload_id, user1.user_id, test_files
            )
            assert upload is not None

            for entry in Entry.objects(upload_id=upload_id):
                assert entry.process_status == status
