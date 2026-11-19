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
import datetime
import os
import re

import pytest
import yaml

from nomad import files
from nomad.config import config
from nomad.datamodel.context import ClientContext
from nomad.datamodel.datamodel import EntryArchive, EntryMetadata
from nomad.parsing.parser import ArchiveParser
from nomad.parsing.tabular import read_table_data
from nomad.processing import Entry, ProcessStatus, Upload
from nomad.utils import generate_entry_id, strip
from tests.normalizing.conftest import run_normalize


def quantity_generator(quantity_name, header_name, shape, to_dict=False):
    if to_dict:
        return {
            quantity_name: {
                'type': 'str',
                'shape': shape,
                'm_annotations': {'tabular': {'name': header_name}},
            }
        }
    else:
        base_case = f"""{quantity_name}:
                type: str
                {shape}
                m_annotations:
                  tabular:
                    name: {header_name}"""
        return re.sub(r'\n\s*\n', '\n', base_case)


@pytest.mark.parametrize(
    'schema',
    [
        pytest.param(
            strip(
                """
            definitions:
                name: 'testing_modes'
                sections:
                    My_schema:
                        base_section: nomad.datamodel.data.EntryData
                        sub_sections:
                            process:
                                section:
                                    base_section: nomad.parsing.tabular.TableData
                                    quantities:
                                        data_file:
                                            type: str
                                            m_annotations:
                                                tabular_parser:
                                                    parsing_options:
                                                        comment: '#'
                                        quantity_1:
                                            type: str
                                            m_annotations:
                                                tabular:
                                                    name: column_1
            data:
                m_def: My_schema
                process:
                    data_file: Test.xlsx
        """
            ),
            id='w/o_sheetName_colMode',
        ),
        pytest.param(
            strip(
                """
            definitions:
                name: 'testing_modes'
                sections:
                    My_schema:
                        base_section: nomad.datamodel.data.EntryData
                        sub_sections:
                            process:
                                section:
                                    base_section: nomad.parsing.tabular.TableData
                                    quantities:
                                        data_file:
                                            type: str
                                            m_annotations:
                                                tabular_parser:
                                                    parsing_options:
                                                        comment: '#'
                                        quantity_1:
                                            type: str
                                            m_annotations:
                                                tabular:
                                                    name: sheet_1/column_1
            data:
                m_def: My_schema
                process:
                    data_file: Test.xlsx
        """
            ),
            id='w_sheetName_colMode',
        ),
        pytest.param(
            strip(
                """
            definitions:
                name: 'testing_modes'
                sections:
                    My_schema:
                        base_section: nomad.datamodel.data.EntryData
                        sub_sections:
                            process:
                                section:
                                    base_section: nomad.parsing.tabular.TableData
                                    quantities:
                                        data_file:
                                            type: str
                                            description: |
                                                A reference to an uploaded .xlsx
                                            m_annotations:
                                                tabular_parser:
                                                    parsing_options:
                                                        comment: '#'
                                        quantity_1:
                                            type: str
                                            m_annotations:
                                                tabular:
                                                    name: sheet_1/column_1
                                        quantity_2:
                                            type: np.float64
                                            shape: ['*']
                                            unit: K
                                            m_annotations:
                                                tabular:
                                                    name: sheet_2/column_2
            data:
                m_def: My_schema
                process:
                    data_file: Test.xlsx
        """
            ),
            id='w_sheetName_colMode_multi_quantities',
        ),
        pytest.param(
            strip(
                """
            definitions:
                name: 'testing_similar_cols_in_datafile'
                sections:
                    My_schema:
                        base_section: nomad.parsing.tabular.TableData
                        quantities:
                            data_file:
                                type: str
                                m_annotations:
                                    tabular_parser:
                                        parsing_options:
                                            comment: '#'
                                        mapping_options:
                                            -   mapping_mode: row
                                                file_mode: current_entry
                                                sections:
                                                    -  test_subsection
                        sub_sections:
                            test_subsection:
                                repeats: true
                                section:
                                    sub_sections:
                                        my_subsection:
                                            repeats: true
                                            section:
                                                quantities:
                                                    my_quantity_1:
                                                        type: str
                                                        m_annotations:
                                                            tabular:
                                                                name: sheet_3/quantity_1
                                                    my_quantity_2:
                                                        type: str
                                                        m_annotations:
                                                            tabular:
                                                                name: sheet_3/quantity_2
            data:
                m_def: My_schema
                data_file: Test.xlsx
        """
            ),
            id='row mode with similar multiple columns in the excel sheet',
        ),
        pytest.param(
            strip(
                """
        definitions:
            name: 'testing_mixed_mode'
            sections:
                MyColumnMode:
                    quantities:
                        my_quantity_1:
                            type: np.float64
                            shape: ['*']
                            m_annotations:
                                tabular:
                                    name: sheet_4/Header 1
                    sub_sections:
                        MyColumnModeSubsection:
                            section:
                                quantities:
                                    my_quantity_2:
                                        type: str
                                        shape: ['*']
                                        m_annotations:
                                            tabular:
                                                name: sheet_4/Header 2
                                    my_quantity_3:
                                        type: str
                                        shape: ['*']
                                        m_annotations:
                                            tabular:
                                                name: sheet_4/Header 3
                MyRowMode:
                    sub_sections:
                        MyRowModeSubsection:
                            repeats: true
                            section:
                                quantities:
                                    my_quantity_4:
                                        type: str
                                        m_annotations:
                                            tabular:
                                                name: sheet_4/Header 4
                MyMixedMode:
                    base_sections:
                        - nomad.parsing.tabular.TableData
                    quantities:
                        data_file:
                            type: str
                            default: datafile.xlsx
                            m_annotations:
                                tabular_parser:
                                    parsing_options:
                                        comment: '#'
                                    mapping_options:
                                        -   mapping_mode: row
                                            file_mode: current_entry
                                            sections:
                                                -  MySubsection2
                                        -   mapping_mode: column
                                            file_mode: current_entry
                                            sections:
                                                -  MySubsection1
                    sub_sections:
                        MySubsection1:
                            section: '#/MyColumnMode'
                        MySubsection2:
                            repeats: true
                            section:
                                base_section: '#/MyRowMode'
        data:
            m_def: MyMixedMode
            data_file: Test.xlsx
    """
            ),
            id='mixed mode',
        ),
    ],
)
def test_tabular_complex_schema(raw_files_function, monkeypatch, schema):
    """
    Testing TabularParser parser. This feature creates an entry out of each row from the given excel/csv file
    """
    _, schema_file = get_files(schema)
    excel_file = os.path.join(
        os.path.dirname(__file__), '../../tests/data/parsers/tabular/Test.xlsx'
    )

    class MyContext(ClientContext):
        def raw_file(self, path, *args, **kwargs):
            return open(excel_file, *args, **kwargs)

    context = MyContext(local_dir='')

    main_archive, _ = get_archives(context, schema_file, 'sample_id')
    ArchiveParser().parse(schema_file, main_archive)
    run_normalize(main_archive)

    assert main_archive.data is not None
    if 'testing_modes' in schema:
        assert 'quantity_1' in main_archive.data.process
        assert main_archive.data.process.quantity_1 == 'value_1'
        if 'quantity_2' in main_archive.data.process:
            assert len(main_archive.data.process['quantity_2']) == 6
    elif 'testing_similar_cols_in_datafile' in schema:
        assert (
            len(main_archive.data.test_subsection) == 2
        )  # 2 rows in sheet_3 of the Excel file
        for row_index, test_subsection in enumerate(main_archive.data.test_subsection):
            for data_index, my_subsection in enumerate(test_subsection.my_subsection):
                assert (
                    my_subsection['my_quantity_1'] == f'q1_d{data_index}_r{row_index}'
                )
                assert (
                    my_subsection['my_quantity_2'] == f'q2_d{data_index}_r{row_index}'
                )
    elif 'testing_mixed_mode' in schema:
        assert len(main_archive.data.MySubsection2) == 3
        assert (
            main_archive.data.MySubsection2[0].MyRowModeSubsection[0].my_quantity_4
            == 'my_str1'
        )
        assert len(main_archive.data.MySubsection1.my_quantity_1) == 3
        assert (
            len(main_archive.data.MySubsection1.MyColumnModeSubsection.my_quantity_2)
            == 3
        )
        assert (
            len(main_archive.data.MySubsection1.MyColumnModeSubsection.my_quantity_3)
            == 3
        )


@pytest.mark.asyncio
async def test_tabular_entry_mode(
    mongo_function, user1, raw_files_function, monkeypatch, temporal_worker
):
    upload = Upload(upload_id='test_upload_id', main_author=user1.user_id)
    upload.save()
    files.StagingUploadFiles(upload_id=upload.upload_id, create=True)
    upload.staging_upload_files.add_rawfiles('tests/data/parsers/tabular/')
    async with temporal_worker():
        await asyncio.to_thread(upload.process_upload)
        await upload.await_workflows()
    assert upload is not None
    assert upload.processed_entries_count == 1

    for entry in Entry.objects(upload_id='test_upload_id'):
        assert entry.process_status == ProcessStatus.SUCCESS


@pytest.mark.parametrize(
    'test_case, sub_sections_placeholder, mapping_options_placeholder, csv_content',
    [
        pytest.param(
            'test_1', {}, [], 'header_0,header_1\n0_0,0_1\n1_0,1_1', id='simple'
        ),
        pytest.param(
            'test_2',
            {'my_substance': {'section': '#/Mysection'}},
            [
                {
                    'mapping_mode': 'column',
                    'file_mode': 'current_entry',
                    'sections': ['my_substance'],
                }
            ],
            'header_0,header_1\n0_0,0_1\n1_0,1_1',
            id='nested',
        ),
    ],
)
def test_tabular_column_mode(
    raw_files_function,
    monkeypatch,
    test_case,
    sub_sections_placeholder,
    mapping_options_placeholder,
    csv_content,
):
    """
    Testing the TableData normalizer using default mode (column mode). This feature creates a list of values
    out of the given column in the excel/csv file for the given quantity.
    """
    base_schema = {
        'definitions': {
            'name': 'Eln',
            'sections': {
                'Mysection': {
                    'quantities': quantity_generator(
                        'quantity_1', 'header_1', shape=['*'], to_dict=True
                    )
                },
                'My_schema': {
                    'base_section': 'nomad.parsing.tabular.TableData',
                    'quantities': {
                        'data_file': {
                            'type': 'str',
                            'description': test_case,
                            'm_annotations': {
                                'tabular_parser': {
                                    'parsing_options': {'comment': '#'},
                                    'mapping_options': mapping_options_placeholder,
                                }
                            },
                        },
                        'quantity_0': {
                            'type': 'str',
                            'shape': ['*'],
                            'm_annotations': {'tabular': {'name': 'header_0'}},
                        },
                    },
                    'sub_sections': sub_sections_placeholder,
                },
            },
        },
        'data': {'m_def': 'My_schema', 'data_file': 'test.my_schema.archive.csv'},
    }
    schema = yaml.dump(base_schema)
    csv_file, schema_file = get_files(schema, csv_content)

    class MyContext(ClientContext):
        def raw_file(self, path, *args, **kwargs):
            return open(csv_file, *args, **kwargs)

    context = MyContext(local_dir='')

    main_archive, _ = get_archives(context, schema_file, 'sample_id')
    ArchiveParser().parse(schema_file, main_archive)
    run_normalize(main_archive)

    assert main_archive.data is not None
    if 'test_1' in schema:
        assert main_archive.data.quantity_0 == ['0_0', '1_0']
    elif 'test_2' in schema:
        assert main_archive.data.my_substance.quantity_1 == ['0_1', '1_1']


@pytest.mark.parametrize(
    'test_case, section_placeholder, target_sub_section_placeholder, sub_sections_placeholder, csv_content',
    [
        pytest.param(
            'test_1',
            None,
            ['my_substance1'],
            {
                'my_substance1': {
                    'repeats': True,
                    'section': {'base_section': 'Substance1'},
                }
            },
            'header_0,header_1\n0_0,0_1\n1_0,1_1',
            id='simple_1_section',
        ),
        pytest.param(
            'test_2',
            {
                'Substance2': {
                    'quantities': quantity_generator(
                        'quantity_2', 'header_2', shape=[], to_dict=True
                    )
                }
            },
            ['my_substance1', 'my_substance2'],
            {
                'my_substance1': {
                    'repeats': True,
                    'section': {'base_section': 'Substance1'},
                },
                'my_substance2': {
                    'repeats': True,
                    'section': {'base_section': 'Substance2'},
                },
            },
            'header_0,header_1,header_2\n0_0,0_1,0_2\n1_0,1_1,1_2',
            id='simple_2_sections',
        ),
        pytest.param(
            'test_3',
            None,
            ['subsection_1/my_substance1'],
            {
                'subsection_1': {
                    'section': {
                        'sub_sections': {
                            'my_substance1': {
                                'repeats': True,
                                'section': {'base_section': 'Substance1'},
                            }
                        }
                    }
                }
            },
            'header_0,header_1\n0_0,0_1\n1_0,1_1',
            id='nested',
        ),
    ],
)
def test_tabular_row_mode(
    raw_files_function,
    monkeypatch,
    test_case,
    section_placeholder,
    target_sub_section_placeholder,
    sub_sections_placeholder,
    csv_content,
):
    """
    Testing the TableData normalizer with mode set to row. This feature is used to create a section out of each row in a
    given sheet_name of an excel file or a csv file, and append it to the repeating (sub)section(s).
    """
    base_schema = {
        'definitions': {
            'name': 'Eln',
            'sections': {
                'Substance1': {
                    'quantities': quantity_generator(
                        'quantity_4', 'header_0', shape=[], to_dict=True
                    )
                },
                'Substance2': {
                    'quantities': quantity_generator(
                        'quantity_2', 'header_2', shape=[], to_dict=True
                    )
                },
                'My_schema': {
                    'base_section': 'nomad.parsing.tabular.TableData',
                    'quantities': {
                        'data_file': {
                            'type': 'str',
                            'description': test_case,
                            'm_annotations': {
                                'tabular_parser': {
                                    'parsing_options': {'comment': '#'},
                                    'mapping_options': [
                                        {
                                            'mapping_mode': 'row',
                                            'file_mode': 'current_entry',
                                            'sections': target_sub_section_placeholder,
                                        }
                                    ],
                                }
                            },
                        }
                    },
                    'sub_sections': sub_sections_placeholder,
                },
            },
        },
        'data': {'m_def': 'My_schema', 'data_file': 'test.my_schema.archive.csv'},
    }
    schema = yaml.dump(base_schema)
    csv_file, schema_file = get_files(schema, csv_content)

    class MyContext(ClientContext):
        def raw_file(self, path, *args, **kwargs):
            return open(csv_file, *args, **kwargs)

    context = MyContext(local_dir='')

    main_archive, _ = get_archives(context, schema_file, 'sample_id')
    ArchiveParser().parse(schema_file, main_archive)
    run_normalize(main_archive)

    assert main_archive.data is not None
    if 'test_1' in schema:
        assert len(main_archive.data.my_substance1) == 2
        ii = 0
        for item in main_archive.data.my_substance1:
            assert item.quantity_4 == f'{ii}_0'
            ii += 1
    elif 'test_2' in schema:
        assert len(main_archive.data.my_substance2) == 2
        ii = 0
        for item in main_archive.data.my_substance2:
            assert item.quantity_2 == f'{ii}_2'
            ii += 1
    elif 'test_3' in schema:
        assert len(main_archive.data.subsection_1.my_substance1) == 2
        ii = 0
        for item in main_archive.data.subsection_1.my_substance1:
            assert item.quantity_4 == f'{ii}_0'
            ii += 1


@pytest.mark.parametrize(
    'schema,content',
    [
        pytest.param(
            strip(
                """
            definitions:
                name: 'space in header'
                sections:
                    MyTable:
                        base_section: nomad.parsing.tabular.TableData
                        quantities:
                            data_file:
                              type: str
                              m_annotations:
                                tabular_parser:
                                    parsing_options:
                                      comment: '#'
                                    mapping_options:
                                    -   mapping_mode: column
                                        file_mode: current_entry
                                        sections:
                                        -   '#root'
                            header_0:
                                m_annotations:
                                    tabular:
                                        name: header_0
                                type: str
                            header_1:
                                type: str
                                m_annotations:
                                    tabular:
                                        name: header_1
            data:
                m_def: MyTable
                data_file: test.my_schema.archive.csv
        """
            ),
            strip(
                """
            header_0, header_1
            a,b
        """
            ),
            id='space in header',
        ),
        pytest.param(
            strip(
                """
            definitions:
                name: 'datetime in row mode'
                sections:
                    MyTableWithDatetime:
                        base_section: nomad.parsing.tabular.TableData
                        m_annotations:
                            eln:
                        quantities:
                            data_file:
                                type: str
                                m_annotations:
                                    tabular_parser:
                                        parsing_options:
                                            comment: '#'
                                        mapping_options:
                                        -   mapping_mode: row
                                            file_mode: current_entry
                                            sections:
                                            -   MySubsection
                        sub_sections:
                            MySubsection:
                                repeats: true
                                section:
                                    quantities:
                                        header_0:
                                            type: Datetime
                                            m_annotations:
                                                tabular:
                                                    name: "Header_0"
            data:
                m_def: MyTableWithDatetime
                data_file: test.my_schema.archive.csv
        """
            ),
            strip(
                """
            Header_0, Header_1
            2018-03-19 13:01:47+01, 1
            2018-03-19 13:01:48+01, 2
            2018-03-19 13:01:49+01, 3
        """
            ),
            id='datetime in row mode',
        ),
        pytest.param(
            strip(
                """
        definitions:
          name: 'data file as reference'
          sections:
            Base_Class:
              quantities:
                name:
                  type: str
            Main_Class:
              base_sections:
                - nomad.parsing.tabular.TableData
              quantities:
                csv_file:
                  type: str
                  default: 'placeholder'
                  m_annotations:
                    tabular_parser:
                        parsing_options:
                            comment: '#'
                        mapping_options:
                        -   mapping_mode: row
                            file_mode: current_entry
                            sections:
                            -   subsection_1
              sub_sections:
                subsection_1:
                  repeats: True
                  section: '#/Class_1'
            Class_1:
              base_sections:
              - '#/Base_Class'
              sub_sections:
                subsection_1:
                  repeats: true
                  section:
                    quantities:
                      header_0:
                        type: str
                        m_annotations:
                          tabular:
                            name: header_0
        data:
          m_def: Main_Class
          csv_file: test.my_schema.archive.csv
          subsection_1:
          - m_def: Class_1
        """
            ),
            strip(
                """
            header_0,header_1
            0_0,0_1
            1_0,1_1
        """
            ),
            id='data file as reference',
        ),
    ],
)
def test_tabular_csv(raw_files_function, monkeypatch, schema, content):
    """Tests that missing data is handled correctly. Pandas by default
    interprets missing numeric values as NaN, which are incompatible with
    metainfo.
    """
    csv_file, schema_file = get_files(schema, content)

    class MyContext(ClientContext):
        def raw_file(self, path, *args, **kwargs):
            return open(csv_file, *args, **kwargs)

    context = MyContext(local_dir='')

    main_archive, _ = get_archives(context, schema_file, 'sample_id')
    ArchiveParser().parse(schema_file, main_archive)
    run_normalize(main_archive)
    if re.search('space in header', schema):
        assert main_archive.data.header_1 is not None
    elif re.search('datetime in row mode', schema):
        for instance in [0, 1, 2]:
            assert isinstance(
                main_archive.data.MySubsection[instance].header_0, datetime.datetime
            )
    elif re.search('data file as reference', schema):
        assert len(main_archive.data.subsection_1) == 2
        assert len(main_archive.data.subsection_1[0].subsection_1) == 1
        assert main_archive.data.subsection_1[0].subsection_1[0].header_0 == '0_0'
        assert main_archive.data.subsection_1[1].subsection_1[0].header_0 == '1_0'


@pytest.mark.parametrize(
    'base_sections',
    [
        pytest.param(
            [
                'nomad.parsing.tabular.TableData',
                'nomad.datamodel.metainfo.plot.PlotSection',
            ],
            id='TableData first',
        ),
        pytest.param(
            [
                'nomad.datamodel.metainfo.plot.PlotSection',
                'nomad.parsing.tabular.TableData',
            ],
            id='PlotSection first',
        ),
    ],
)
def test_tabular_plot_section(raw_files_function, monkeypatch, base_sections):
    """Tests that plotly_express figures of a section inheriting from both TableData
    and PlotSection contain the data read from the data file, independent of the
    order of the base sections.
    """
    schema = yaml.safe_dump(
        {
            'definitions': {
                'name': 'tabular plot section',
                'sections': {
                    'Optical_absorption': {
                        'base_sections': ['nomad.datamodel.data.EntryData']
                        + base_sections,
                        'quantities': {
                            'data_file': {
                                'type': 'str',
                                'm_annotations': {
                                    'eln': {'component': 'FileEditQuantity'},
                                    'tabular_parser': {
                                        'parsing_options': {
                                            'comment': '#',
                                            'skiprows': [1],
                                        },
                                        'mapping_options': [
                                            {
                                                'mapping_mode': 'column',
                                                'file_mode': 'current_entry',
                                                'sections': ['#root'],
                                            }
                                        ],
                                    },
                                },
                            },
                            'wavelength': {
                                'type': 'np.float64',
                                'unit': 'nm',
                                'shape': ['*'],
                                'm_annotations': {'tabular': {'name': 'Wavelength'}},
                            },
                            'absorbance': {
                                'type': 'np.float64',
                                'shape': ['*'],
                                'm_annotations': {'tabular': {'name': 'Absorbance'}},
                            },
                        },
                        'm_annotations': {
                            'plotly_express': {
                                'method': 'line',
                                'x': '#wavelength',
                                'y': '#absorbance',
                                'layout': {'title': {'text': 'Optical Spectrum'}},
                            }
                        },
                    }
                },
            },
            'data': {
                'm_def': 'Optical_absorption',
                'data_file': 'test.my_schema.archive.csv',
            },
        }
    )
    content = strip(
        """
        Wavelength,Absorbance
        nm,a.u.
        400,0.1
        500,0.2
        600,0.3
    """
    )
    csv_file, schema_file = get_files(schema, content)

    class MyContext(ClientContext):
        def raw_file(self, path, *args, **kwargs):
            return open(csv_file, *args, **kwargs)

    context = MyContext(local_dir='')

    main_archive, _ = get_archives(context, schema_file, 'sample_id')
    ArchiveParser().parse(schema_file, main_archive)
    run_normalize(main_archive)

    data = main_archive.data
    assert list(data.wavelength.magnitude) == [400, 500, 600]
    assert list(data.absorbance) == [0.1, 0.2, 0.3]

    assert len(data.figures) == 1
    figure = data.figures[0].figure
    assert figure['layout']['title']['text'] == 'Optical Spectrum'
    trace = figure['data'][0]
    assert list(trace.get('x', [])) == [400, 500, 600], 'figure trace has no x data'
    assert list(trace.get('y', [])) == [0.1, 0.2, 0.3], 'figure trace has no y data'


def parse_and_normalize_csv(schema: dict, content: str) -> EntryArchive:
    """Parses and normalizes an archive using `schema`, where the data file of the
    TableData section resolves to a csv file with `content`.
    """
    csv_file, schema_file = get_files(yaml.safe_dump(schema), strip(content))

    class MyContext(ClientContext):
        def raw_file(self, path, *args, **kwargs):
            return open(csv_file, *args, **kwargs)

    context = MyContext(local_dir='')

    main_archive, _ = get_archives(context, schema_file, 'sample_id')
    ArchiveParser().parse(schema_file, main_archive)
    run_normalize(main_archive)
    return main_archive


def column_mode_data_file():
    return {
        'type': 'str',
        'm_annotations': {
            'tabular_parser': {
                'parsing_options': {'comment': '#'},
                'mapping_options': [
                    {
                        'mapping_mode': 'column',
                        'file_mode': 'current_entry',
                        'sections': ['#root'],
                    }
                ],
            }
        },
    }


def test_tabular_parsing_error_runs_base_normalizers(raw_files_function):
    """Tests that the other base section normalizers still run if parsing the
    data file fails.
    """
    data_file = column_mode_data_file()
    data_file['shape'] = ['*']  # non-scalar data files are not supported
    schema = {
        'definitions': {
            'name': 'tabular parsing error',
            'sections': {
                'MyTable': {
                    'base_sections': [
                        'nomad.parsing.tabular.TableData',
                        'nomad.datamodel.data.EntryData',
                    ],
                    'quantities': {'data_file': data_file},
                }
            },
        },
        'data': {'m_def': 'MyTable', 'data_file': ['test.my_schema.archive.csv']},
    }
    content = """
        header_0,header_1
        a,b
    """

    archive = parse_and_normalize_csv(schema, content)

    assert archive.metadata.entry_type == 'MyTable'


@pytest.mark.parametrize(
    'base_sections',
    [
        pytest.param(
            ['nomad.parsing.tabular.TableData', 'nomad.datamodel.data.EntryData'],
            id='TableData first',
        ),
        pytest.param(
            ['nomad.datamodel.data.EntryData', 'nomad.parsing.tabular.TableData'],
            id='EntryData first',
        ),
    ],
)
def test_tabular_entry_data_order(raw_files_function, monkeypatch, base_sections):
    """Tests that the data file is parsed if TableData is listed before EntryData.
    EntryData sets the entry_name, which must not be mistaken for an entry
    created by the tabular parser itself.
    """
    schema = {
        'definitions': {
            'name': 'tabular entry data order',
            'sections': {
                'MyTable': {
                    'base_sections': base_sections,
                    'quantities': {
                        'data_file': column_mode_data_file(),
                        'header_0': {
                            'type': 'str',
                            'm_annotations': {'tabular': {'name': 'header_0'}},
                        },
                    },
                }
            },
        },
        'data': {'m_def': 'MyTable', 'data_file': 'test.my_schema.archive.csv'},
    }
    content = """
        header_0,header_1
        a,b
    """

    archive = parse_and_normalize_csv(schema, content)

    assert archive.data.header_0 == 'a'


@pytest.mark.parametrize(
    'base_sections',
    [
        pytest.param(
            [
                'nomad.parsing.tabular.TableData',
                'nomad.datamodel.metainfo.basesections.BaseSection',
            ],
            id='TableData first',
        ),
        pytest.param(
            [
                'nomad.datamodel.metainfo.basesections.BaseSection',
                'nomad.parsing.tabular.TableData',
            ],
            id='BaseSection first',
        ),
    ],
)
def test_tabular_base_section_eln_results(
    raw_files_function, monkeypatch, base_sections
):
    """Tests that BaseSection quantities read from the data file are added to
    results.eln, independent of the order of the base sections.
    """
    schema = {
        'definitions': {
            'name': 'tabular base section',
            'sections': {
                'MySample': {
                    'base_sections': base_sections,
                    'quantities': {
                        'data_file': column_mode_data_file(),
                        'lab_id': {
                            'type': 'str',
                            'm_annotations': {'tabular': {'name': 'Lab ID'}},
                        },
                        'description': {
                            'type': 'str',
                            'm_annotations': {'tabular': {'name': 'Description'}},
                        },
                    },
                }
            },
        },
        'data': {
            'm_def': 'MySample',
            'name': 'my sample',
            'data_file': 'test.my_schema.archive.csv',
        },
    }
    content = """
        Lab ID,Description
        S-001,grown at 300 K
    """

    archive = parse_and_normalize_csv(schema, content)

    assert archive.data.lab_id == 'S-001'
    assert archive.data.description == 'grown at 300 K'
    assert archive.results.eln.lab_ids == ['S-001']
    assert archive.results.eln.descriptions == ['grown at 300 K']


@pytest.mark.parametrize(
    'base_sections',
    [
        pytest.param(
            [
                'nomad.parsing.tabular.TableData',
                'nomad.datamodel.metainfo.basesections.System',
            ],
            id='TableData first',
        ),
        pytest.param(
            [
                'nomad.datamodel.metainfo.basesections.System',
                'nomad.parsing.tabular.TableData',
            ],
            id='System first',
        ),
    ],
)
def test_tabular_system_elemental_composition(
    raw_files_function, monkeypatch, base_sections
):
    """Tests that the elemental composition read from the data file is completed
    and added to results.material, independent of the order of the base sections.
    """
    schema = {
        'definitions': {
            'name': 'tabular system',
            'sections': {
                'MyComposition': {
                    'base_sections': [
                        'nomad.datamodel.metainfo.basesections.ElementalComposition'
                    ],
                    'quantities': {
                        'element': {
                            'type': 'str',
                            'm_annotations': {'tabular': {'name': 'Element'}},
                        },
                        'atomic_fraction': {
                            'type': 'np.float64',
                            'm_annotations': {'tabular': {'name': 'Atomic fraction'}},
                        },
                    },
                },
                'MySystem': {
                    'base_sections': base_sections,
                    'quantities': {
                        'data_file': {
                            'type': 'str',
                            'm_annotations': {
                                'tabular_parser': {
                                    'parsing_options': {'comment': '#'},
                                    'mapping_options': [
                                        {
                                            'mapping_mode': 'row',
                                            'file_mode': 'current_entry',
                                            'sections': ['elemental_composition'],
                                        }
                                    ],
                                }
                            },
                        }
                    },
                    'sub_sections': {
                        'elemental_composition': {
                            'repeats': True,
                            'section': '#/MyComposition',
                        }
                    },
                },
            },
        },
        'data': {
            'm_def': 'MySystem',
            'name': 'my system',
            'data_file': 'test.my_schema.archive.csv',
        },
    }
    content = """
        Element,Atomic fraction
        Ga,0.5
        As,0.5
    """

    archive = parse_and_normalize_csv(schema, content)

    composition = archive.data.elemental_composition
    assert [comp.element for comp in composition] == ['Ga', 'As']
    assert all(comp.mass_fraction is not None for comp in composition)
    assert sorted(archive.results.material.elements) == ['As', 'Ga']


@pytest.mark.parametrize(
    'schema,content',
    [
        pytest.param(
            strip(
                """
        definitions:
            name: 'TableData checkbox'
            sections:
                Main_Class:
                    base_section: nomad.parsing.tabular.TableData
                    quantities:
                        data_file:
                            type: str
                            m_annotations:
                                tabular_parser:
                                    parsing_options:
                                        comment: '#'
                                    mapping_options:
                                    -   mapping_mode: row
                                        file_mode: current_entry
                                        sections:
                                        -   MySubsection
                    sub_sections:
                        MySubsection:
                            repeats: true
                            section:
                                quantities:
                                    header_0:
                                        type: str
                                        m_annotations:
                                            tabular:
                                                name: header_0
        data:
          m_def: Main_Class
          data_file: test.my_schema.archive.csv
        """
            ),
            strip(
                """
            header_0,header_1
            a,b
        """
            ),
            id='checking checkbox',
        ),
        pytest.param(
            strip(
                """
        definitions:
            name: 'hiding checkbox'
            sections:
                Main_Class:
                    base_section: nomad.parsing.tabular.TableData
                    m_annotations:
                        eln:
                            hide: ['fill_archive_from_datafile']
                    quantities:
                        data_file:
                            type: str
                            m_annotations:
                                tabular_parser:
                                    parsing_options:
                                        comment: '#'
                                    mapping_options:
                                    -   mapping_mode: row
                                        file_mode: current_entry
                                        sections:
                                        -   MySubsection
                    sub_sections:
                        MySubsection:
                            repeats: true
                            section:
                                quantities:
                                    header_0:
                                        type: str
                                        m_annotations:
                                            tabular:
                                                name: header_0
        data:
          m_def: Main_Class
          data_file: test.my_schema.archive.csv
        """
            ),
            strip(
                """
            header_0,header_1
            a,b
        """
            ),
            id='hide checkbox',
        ),
    ],
)
def test_tabular_checkbox(raw_files_function, monkeypatch, schema, content):
    """Tests that missing data is handled correctly. Pandas by default
    interprets missing numeric values as NaN, which are incompatible with
    metainfo.
    """
    csv_file, schema_file = get_files(schema, content)

    class MyContext(ClientContext):
        def raw_file(self, path, *args, **kwargs):
            return open(csv_file, *args, **kwargs)

    context = MyContext(local_dir='')

    main_archive, _ = get_archives(context, schema_file, 'sample_id')
    ArchiveParser().parse(schema_file, main_archive)
    run_normalize(main_archive)

    if 'TableData checkbox' in schema:
        assert main_archive.data.MySubsection[0].header_0 == 'a'
        assert main_archive.data.fill_archive_from_datafile is False
        main_archive.data.MySubsection[0].header_0 = 'c'
        run_normalize(main_archive)
        assert main_archive.data.MySubsection[0].header_0 == 'c'
    elif 'fill_archive_from_datafile' in schema:
        assert main_archive.data.MySubsection[0].header_0 == 'a'
        assert main_archive.data.fill_archive_from_datafile is True
        main_archive.data.MySubsection[0].header_0 = 'c'
        run_normalize(main_archive)
        assert main_archive.data.MySubsection[0].header_0 == 'c'


def get_files(schema=None, content=None):
    """Prepares files containing schema and data in the temporary file
    directory.
    """
    if schema:
        schema_file = os.path.join(config.fs.tmp, 'my_schema.archive.yaml')
        with open(schema_file, 'w') as f:
            f.write(schema)
    else:
        schema_file = None
    if content:
        mainfile = os.path.join(config.fs.tmp, 'test.my_schema.archive.csv')
        with open(mainfile, 'w') as f:
            f.write(content)
    else:
        mainfile = None
    return mainfile, schema_file


def get_context(upload_id, schema_file):
    """Prepares a custom context for testing."""

    class MyContext(ClientContext):
        def load_raw_file(
            self,
            path: str,
            upload_id: str,
            installation_url: str,
            url: str | None = None,
        ):
            archive = super().load_raw_file(path, upload_id, installation_url, url)
            archive.metadata = EntryMetadata(
                upload_id=upload_id, entry_id=generate_entry_id(upload_id, schema_file)
            )
            return archive

    return MyContext(local_dir='')


def get_archives(context, mainfile, upload_id, keys=None):
    """Prepares a main archive and child archives for parsing."""
    main_archive = EntryArchive(
        m_context=context,
        metadata=EntryMetadata(
            upload_id=upload_id,
            mainfile=mainfile,
            entry_id=generate_entry_id(upload_id, mainfile),
        ),
    )
    if keys:
        child_archives = {
            key: EntryArchive(
                m_context=context,
                metadata=EntryMetadata(
                    upload_id=upload_id,
                    mainfile=mainfile,
                    mainfile_key=key,
                    entry_id=generate_entry_id(upload_id, mainfile, key),
                ),
            )
            for key in keys
        }
    else:
        child_archives = None

    return main_archive, child_archives


def test_read_table_data_excel():
    expected_data = {
        0: {
            'sheet_4': {
                'Header 1': {0: 1, 1: 2, 2: 3},
                'Header 2': {0: 'a', 1: 'b', 2: 'c'},
                'Header 3': {0: 'test1', 1: 'test2', 2: 'test3'},
                'Header 4': {0: 'my_str1', 1: 'my_str2', 2: 'my_str3'},
                'Header 5': {0: 11, 1: 12, 2: 13},
                'Header 6': {0: 'test11', 1: 'test12', 2: 'test13'},
            }
        }
    }
    excel_file = os.path.join(
        os.path.dirname(__file__), '../../tests/data/parsers/tabular/Test.xlsx'
    )

    result_df = read_table_data(excel_file, comment='#')

    result_dict = result_df.to_dict()

    assert result_dict[0]['sheet_4'] == expected_data[0]['sheet_4']
