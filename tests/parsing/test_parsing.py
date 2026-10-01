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
import os
from shutil import copyfile
from unittest.mock import MagicMock, patch

import pytest

from nomad import files, utils
from nomad.datamodel import EntryArchive
from nomad.parsing import BrokenParser, MatchingParser, MatchingParserInterface
from nomad.parsing.parsers import match_parser, parser_dict, parsers, run_parser
from nomad.utils import dump_json

parser_examples = [
    ('parsers/random', 'test/data/parsers/random_0'),
    ('parsers/template', 'tests/data/templates/template.json'),
    ('parsers/exciting', 'tests/data/parsers/exciting/Ag/INFO.OUT'),
    ('parsers/exciting', 'tests/data/parsers/exciting/GW/INFO.OUT'),
    ('parsers/exciting', 'tests/data/parsers/exciting/nitrogen/INFO.OUT_nitrogen'),
    ('parsers/exciting', 'tests/data/parsers/exciting/nitrogen/INFO.OUT_carbon'),
    ('parsers/vasp', 'tests/data/parsers/vasp/vasp.xml'),
    ('parsers/vasp', 'tests/data/parsers/vasp_compressed/vasp.xml.gz'),
    ('parsers/vasp', 'tests/data/parsers/vasp_outcar/OUTCAR'),
    ('parsers/fhi-aims', 'tests/data/parsers/fhi-aims/aims.out'),
    # ('parsers/fhi-vibes', 'tests/data/parsers/fhi-vibes/molecular_dynamics.nc'),
    ('parsers/cp2k', 'tests/data/parsers/cp2k/si_bulk8.out'),
    ('parsers/crystal', 'tests/data/parsers/crystal/si.out'),
    ('parsers/cpmd', 'tests/data/parsers/cpmd/geo_output.out'),
    ('parsers/nwchem', 'tests/data/parsers/nwchem/single_point/output.out'),
    ('parsers/bigdft', 'tests/data/parsers/bigdft/n2_output.out'),
    ('parsers/wien2k', 'tests/data/parsers/wien2k/AlN/AlN_ZB.scf'),
    ('parsers/ams', 'tests/data/parsers/band_adf.out'),
    ('parsers/gaussian', 'tests/data/parsers/gaussian/aniline.out'),
    ('parsers/abinit', 'tests/data/parsers/abinit/Fe.out'),
    ('parsers/quantumespresso', 'tests/data/parsers/quantum-espresso/benchmark.out'),
    ('parsers/orca', 'tests/data/parsers/orca/orca3dot2706823.out'),
    ('parsers/castep', 'tests/data/parsers/castep/BC2N-Pmm2-Raman.castep'),
    ('parsers/dl-poly', 'tests/data/parsers/dl-poly/OUTPUT'),
    ('parsers/lib-atoms', 'tests/data/parsers/lib-atoms/gp.xml'),
    ('parsers/octopus', 'tests/data/parsers/octopus/stdout.txt'),
    (
        'parsers/phonopy',
        'tests/data/parsers/phonopy/phonopy-FHI-aims-displacement-01/control.in',
    ),
    ('parsers/gpaw', 'tests/data/parsers/gpaw/Fe2.gpw'),
    ('parsers/gpaw', 'tests/data/parsers/gpaw2/H2_lcao.gpw2'),
    ('parsers/atk', 'tests/data/parsers/atk/Si2.nc'),
    ('parsers/gulp', 'tests/data/parsers/gulp/example6.got'),
    ('parsers/siesta', 'tests/data/parsers/siesta/Fe/out'),
    ('parsers/elk', 'tests/data/parsers/elk/Al/INFO.OUT'),
    (
        'parsers/elastic',
        'dependencies/parsers/elastic/test/examples/2nd/INFO_ElaStic',
    ),  # 70Mb file 2big4git
    ('parsers/turbomole', 'tests/data/parsers/turbomole/acrolein.out'),
    ('parsers/gamess', 'tests/data/parsers/gamess/exam01.out'),
    ('parsers/dmol', 'tests/data/parsers/dmol3/h2o.outmol'),
    ('parser/fleur', 'tests/data/parsers/fleur/out'),  # double-check
    ('parser/molcas', 'tests/data/parsers/molcas/test000.input.out'),
    ('parsers/qbox', 'tests/data/parsers/qbox/01_h2ogs.r'),
    ('parser/onetep', 'tests/data/parsers/onetep/fluor/12-difluoroethane.out'),
    ('parsers/lobster', 'tests/data/parsers/lobster/NaCl/lobsterout'),
    ('parsers/aflow', 'tests/data/parsers/aflow/Ag1Co1O2_ICSD_246157/aflowlib.json'),
    ('parsers/atomate', 'tests/data/parsers/atomate/mp-1/materials.json'),
    (
        'parsers/asr',
        'tests/data/parsers/asr/archive_ccdc26c4f32546c5a00ad03a093b73dc.json',
    ),
    ('parsers/psi4', 'tests/data/parsers/psi4/adc1/output.ref'),
    (
        'parsers/yambo',
        'tests/data/parsers/yambo/hBN/r-10b_1Ry_HF_and_locXC_gw0_em1d_ppa',
    ),
    ('parsers/archive', 'tests/data/parsers/archive.json'),
]

# We need to remove some cases with external mainfiles, which might not exist
# in all testing environments (e.g. in the nomad docker image)
fixed_parser_examples = []
for parser, mainfile in parser_examples:
    if os.path.exists(mainfile) or mainfile.startswith('tests'):
        fixed_parser_examples.append((parser, mainfile))
parser_examples = fixed_parser_examples

correct_num_output_files = 124


def create_reference(data, pretty):
    if pretty:
        return json.dumps(data, indent=2)
    else:
        return json.dumps(data, separators=(',', ':'))


@pytest.fixture(scope='function')
def assert_parser_result(log_output):
    def _assert(
        entry_archive: EntryArchive,
        has_errors: bool = False,
        has_warnings: bool | None = None,
    ):
        errors_exist = False
        warnings_exist = False
        for record in log_output.entries:
            if record['log_level'] in ['error', 'critical']:
                errors_exist = True
            if record['log_level'] in ['warning']:
                warnings_exist = True
        assert has_errors == errors_exist
        if has_warnings is not None:
            assert has_warnings == warnings_exist

    return _assert


def assert_parser_dir_unchanged(previous_wd, current_wd):
    """Assert working directory has not been changed from parser."""
    assert previous_wd == current_wd


def run_singular_parser(parser_name, mainfile) -> EntryArchive:
    """Runs a singular parser (a parser which creates no child entries) and adds metadata."""
    parser = parser_dict[parser_name]
    assert not parser.creates_children
    archives = run_parser(mainfile, parser, logger=utils.get_logger(__name__))
    return add_metadata(archives[0], parser_name=parser_name)


@pytest.fixture
def parsed_vasp_example() -> EntryArchive:
    return run_singular_parser(
        'parsers/vasp', 'dependencies/parsers/vasp/test/examples/xml/perovskite.xml'
    )


@pytest.fixture
def parsed_template_example() -> EntryArchive:
    return run_singular_parser('parsers/template', 'tests/data/templates/template.json')


def parse_file(parser_name_and_mainfile) -> EntryArchive:
    parser_name, mainfile = parser_name_and_mainfile
    return run_singular_parser(parser_name, mainfile)


@pytest.fixture(params=parser_examples, ids=lambda spec: '{}-{}'.format(*spec))
def parsed_example(request) -> EntryArchive:
    parser_name, mainfile = request.param
    result = run_singular_parser(parser_name, mainfile)
    return result


def add_metadata(entry_archive: EntryArchive, **kwargs) -> EntryArchive:
    entry_metadata = entry_archive.metadata
    entry_metadata.upload_id = 'test_upload_id'
    entry_metadata.entry_id = 'test_entry_id'
    entry_metadata.entry_hash = 'test_entry_hash'
    entry_metadata.mainfile = 'test/mainfile.txt'
    entry_metadata.m_update(**kwargs)
    return entry_archive


@pytest.mark.parametrize('parser_name, mainfile', parser_examples)
def test_parser(parser_name, mainfile, assert_parser_result):
    previous_wd = os.getcwd()  # Get Working directory before parsing.
    parsed_example = run_singular_parser(parser_name, mainfile)
    assert_parser_result(parsed_example)
    # Check that cwd has not changed.
    assert_parser_dir_unchanged(previous_wd, current_wd=os.getcwd())


def test_broken_xml_vasp(assert_parser_result):
    parser_name, mainfile = 'parsers/vasp', 'tests/data/parsers/vasp/broken.xml'
    previous_wd = os.getcwd()  # Get Working directory before parsing.
    parsed_example = run_singular_parser(parser_name, mainfile)
    assert_parser_result(parsed_example, has_warnings=True)
    # Check that cwd has not changed.
    assert_parser_dir_unchanged(previous_wd, current_wd=os.getcwd())


@pytest.fixture(scope='function')
def with_latin_1_file(raw_files_function):
    copyfile('tests/data/latin-1.out', 'tests/data/parsers/latin-1.out')
    yield
    os.remove('tests/data/parsers/latin-1.out')


@pytest.mark.parametrize(
    'parsers, num_output_files',
    [
        (
            [
                [
                    MatchingParserInterface(
                        'workflowparsers.fhivibes.parser.FHIVibesParser',
                        code_name='parsers/fhivibes',
                        mainfile_name_re=(r'^.*\.(nc)$'),
                        mainfile_mime_re=r'(application/x-hdf)',
                        mainfile_binary_header_re=rb'^\x89HDF',
                        mainfile_contents_dict={'__has_all_keys': ['I', 'a', 'b']},
                    )
                ],
                1,
            ]
        ),
        (
            [
                [
                    MatchingParserInterface(
                        'workflowparsers.fhivibes.parser.FHIVibesParser',
                        code_name='parsers/fhivibes',
                        mainfile_mime_re=r'(application/x-hdf)',
                        mainfile_binary_header_re=rb'^\x89HDF',
                        mainfile_contents_dict={'__has_key': 'aims_uuid'},
                    )
                ],
                1,
            ]
        ),
        (
            [
                [
                    MatchingParserInterface(
                        'workflowparsers.fhivibes.parser.FHIVibesParser',
                        code_name='parsers/fhivibes',
                        mainfile_mime_re=r'(application/x-hdf)',
                        mainfile_binary_header_re=rb'^\x89HDF',
                        mainfile_contents_dict={'a': [0, 0, 0]},
                    )
                ],
                1,
            ]
        ),
        (
            [
                [
                    MatchingParserInterface(
                        'workflowparsers.fhivibes.parser.FHIVibesParser',
                        code_name='parsers/fhivibes',
                        mainfile_mime_re=r'(application/x-hdf)',
                        mainfile_binary_header_re=rb'^\x89HDF',
                        mainfile_contents_dict={'I': [0, 0]},
                    )
                ],
                0,
            ]
        ),
        (parsers, correct_num_output_files),
    ],
)
def test_match(
    raw_files_function,
    with_latin_1_file,
    no_warn,
    parsers,
    num_output_files,
    monkeypatch,
):
    example_upload_id = 'example_upload_id'
    upload_files = files.StagingUploadFiles(example_upload_id, create=True)
    upload_files.add_rawfiles('tests/data/parsers')

    if parsers:
        monkeypatch.setattr('nomad.parsing.parsers.parsers', parsers)

    matched_mainfiles = {}
    for path_info in upload_files.raw_listdir(recursive=True, files_only=True):
        mainfile = path_info.path
        parser, _mainfile_keys = match_parser(
            upload_files.raw_file_object(mainfile).os_path
        )
        if parser is not None and not isinstance(parser, BrokenParser):
            matched_mainfiles[mainfile] = parser

    assert len(matched_mainfiles) >= num_output_files, ', '.join(
        [f'{parser.name}: {mainfile}' for mainfile, parser in matched_mainfiles.items()]
    )


def test_match_parser_matching_order(tmp_path, monkeypatch):
    mainfile = tmp_path / 'mainfile.txt'
    mainfile.write_text('contents')
    parsers_to_match = [
        MatchingParser(name='unordered'),
        MatchingParser(name='ordered-later', matching_order=-1),
        MatchingParser(name='ordered-first', matching_order=-2),
    ]
    monkeypatch.setattr('nomad.parsing.parsers.parsers', parsers_to_match)

    parser, _ = match_parser(str(mainfile))

    assert parser.name == 'ordered-first'


def test_match_parser_sorts_equal_matching_orders_alphabetically(tmp_path, monkeypatch):
    mainfile = tmp_path / 'mainfile.txt'
    mainfile.write_text('contents')
    parsers_to_match = [
        MatchingParser(name='second-alphabetically', matching_order=-1),
        MatchingParser(name='first-alphabetically', matching_order=-1),
    ]
    monkeypatch.setattr('nomad.parsing.parsers.parsers', parsers_to_match)

    parser, _ = match_parser(str(mainfile))

    assert parser.name == 'first-alphabetically'


def test_match_parser_uses_registration_order_without_matching_order(
    tmp_path, monkeypatch
):
    mainfile = tmp_path / 'mainfile.txt'
    mainfile.write_text('contents')
    parsers_to_match = [
        MatchingParser(name='registered-first'),
        MatchingParser(name='registered-second'),
    ]
    monkeypatch.setattr('nomad.parsing.parsers.parsers', parsers_to_match)

    parser, _ = match_parser(str(mainfile))

    assert parser.name == 'registered-first'


def parser_in_dir(dir):
    for root, _, files in os.walk(dir):
        for file_name in files:
            file_path = os.path.join(root, file_name)

            if 'test' not in file_path:
                continue

            parser, mainfile_keys = match_parser(file_path)
            if parser is not None:
                try:
                    archives = run_parser(file_path, parser, mainfile_keys)

                    # check if the result can be dumped
                    for archive in archives:
                        dump_json(archive.m_to_dict())
                except Exception as e:
                    print(file_path, parser, 'FAILURE', e)
                    import traceback

                    traceback.print_exc()
                else:
                    print(file_path, parser, 'SUCCESS')


@pytest.fixture
def matching_parser():
    return MatchingParser(
        mainfile_mime_re=r'application/json|text/csv|application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        mainfile_name_re=r'.*\.(json|xlsx|csv)',
    )


@pytest.mark.parametrize(
    'mime, mocked_dict, file_content, expected_result',
    [
        pytest.param(
            'application/json',
            {'__has_all_keys': ['key1', 'key2']},
            {'key1': 'value1', 'key2': 'value2'},
            True,
            id='json_content_match',
        ),
        pytest.param(
            'application/json',
            {'__has_all_keys': ['key1', 'key2']},
            {'key1': 'value1', 'key3': 'value3'},
            False,
            id='json_content_no_match',
        ),
        pytest.param(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            {'Sheet1': {'__has_all_keys': ['col1', 'col2']}, '__has_comment': '#'},
            {'Sheet1': {'col1': 'value1', 'col2': 'value2'}},
            True,
            id='excel_content_match',
        ),
        pytest.param(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            {'Sheet1': {'__has_all_keys': ['col1', 'col2']}, '__has_comment': '#'},
            {'Sheet1': {'col1': 'value1', 'col3': 'value3'}},
            False,
            id='excel_content_no_match',
        ),
        pytest.param(
            'text/csv',
            {'__has_all_keys': ['col1', 'col2']},
            {'col1': ['value1'], 'col2': ['value2']},
            True,
            id='csv_content_match',
        ),
        pytest.param(
            'text/csv',
            {'__has_all_keys': ['col1', 'col2']},
            {'col1': ['value1'], 'col3': ['value3']},
            False,
            id='csv_content_no_match',
        ),
    ],
)
def test_is_mainfile_with_mocked_content(
    matching_parser, mime, mocked_dict, file_content, expected_result
):
    """Test is_mainfile with mocked content (JSON, Excel)."""
    import pandas as pd

    matching_parser._mainfile_contents_dict = mocked_dict

    with patch('builtins.open', new_callable=MagicMock) as mock_open:
        if mime.startswith('application/json'):
            with patch('json.load', return_value=file_content):
                result = matching_parser.is_mainfile(
                    filename='mocked_file.json',
                    mime=mime,
                    buffer=b'',
                    decoded_buffer='',
                )
                assert result == expected_result

        elif mime.startswith(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        ):
            with patch(
                'nomad.parsing.tabular.read_table_data',
                return_value=(MagicMock(to_dict=lambda: file_content),),
            ):
                result = matching_parser.is_mainfile(
                    filename='mocked_file.xlsx',
                    mime=mime,
                    buffer=b'',
                    decoded_buffer='',
                )
                assert result == expected_result
        elif mime == 'text/csv':
            with patch(
                'pandas.read_csv',
                return_value=pd.DataFrame(file_content),
            ):
                result = matching_parser.is_mainfile(
                    filename='mocked_file.csv',
                    mime=mime,
                    buffer=b'',
                    decoded_buffer='',
                )
                assert result == expected_result


if __name__ == '__main__':
    import os
    import sys

    assert len(sys.argv) == 2 and os.path.isdir(sys.argv[1]), (
        'One argument with an directory path is required.'
    )

    parser_in_dir(sys.argv[1])
