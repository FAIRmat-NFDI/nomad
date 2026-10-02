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
import math
import os
import re
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import numpy as np
import pandas as pd
import yaml
from cachetools import LRUCache, cached

from nomad import utils
from nomad.datamodel.data import ArchiveSection
from nomad.datamodel.metainfo.annotations import (
    TabularAnnotation,
    TabularFileModeEnum,
    TabularMode,
    TabularParserAnnotation,
)
from nomad.metainfo import MSection, Package, Property, Quantity, Reference, Section
from nomad.metainfo.metainfo import MetainfoError, MProxy, SubSection
from nomad.metainfo.util import MSubSectionList
from nomad.parsing import MatchingParser
from nomad.units import ureg
from nomad.utils import generate_entry_id

# We define a simple base schema for tabular data. The parser will then generate more
# specialized sections based on the table headers. These specialized definitions will use
# this as base section definition.
# TODO maybe this should be moved to nomad.datamodel.metainfo
m_package = Package()


root_mapping = {'root': '#root'}


def get_nested_value(data, path):
    current = data
    for key in path:
        if key in current:
            current = current[key]
        else:
            return None
    return current


def create_archive(entry_dict, context, file_name, file_type, logger):
    if not context.raw_path_exists(file_name):
        with context.raw_file(file_name, 'w') as outfile:
            if file_type == 'json':
                json.dump(entry_dict, outfile)
            elif file_type == 'yaml':
                yaml.dump(entry_dict, outfile)
        context.upload.process_updated_raw_file(file_name, allow_modify=True)
    else:
        logger.error(
            f'{file_name} archive file already exists.'
            f'If you intend to reprocess the older archive file, remove the existing one and run reprocessing again.'
        )


def traverse_to_target_data_file(section, path_list: list[str]):
    if len(path_list) == 0 and (isinstance(section, str) or section is None):
        return section
    else:
        try:
            temp = path_list.pop(0)
            return traverse_to_target_data_file(getattr(section, temp), path_list)
        except AttributeError:
            raise MetainfoError(f'The path {temp} in path_to_data_file does not exist')


class TabularParserError(Exception):
    """Tabular-parser related errors."""

    pass


class TableData(ArchiveSection):
    """Table data"""

    fill_archive_from_datafile = Quantity(
        type=bool,
        a_eln=dict(component='BoolEditQuantity'),
        description="""While checked, it allows the parser to fill all the Quantities from the data file.
        Be cautious though! as checking this box will cause overwriting your fields with data parsed from the data file
        """,
        default=True,
    )

    def normalize(self, archive, logger):
        super().normalize(archive, logger)

        if self.fill_archive_from_datafile:
            for quantity_def in self.m_def.all_quantities.values():
                annotation = quantity_def.m_get_annotations('tabular_parser')
                annotation = (
                    annotation[0] if isinstance(annotation, list) else annotation
                )
                # this normalizer potentially creates new archives,.to avoid recursive call of the normalizer created by
                # this normalizer, we check for if the parent is already parsed
                if annotation and not archive.data.m_parent.metadata.entry_name:
                    self.tabular_parser(quantity_def, archive, logger, annotation)

    def tabular_parser(
        self,
        quantity_def: Quantity,
        archive,
        logger,
        annotation: TabularParserAnnotation,
    ):
        if logger is None:
            logger = utils.get_logger(__name__)

        if not quantity_def.is_scalar:
            raise NotImplementedError(
                'CSV parser is only implemented for single files.'
            )

        if self.m_get(quantity_def) is None:
            pass
        elif (
            quantity_def.default and not self.m_get(quantity_def)
        ) or quantity_def.default == self.m_get(quantity_def):
            datafile_path = quantity_def.default
            if re.match(r'^#data/(\w+/)*\w+$', datafile_path):
                self.m_set(
                    quantity_def,
                    traverse_to_target_data_file(
                        archive, datafile_path.split('#')[1].split('/')
                    ),
                )

        data_file = self.m_get(quantity_def)
        if not data_file or re.match(r'^#data/(\w+/)*\w+$', data_file):
            return

        parsing_options = dict(annotation.parsing_options)
        with archive.m_context.raw_file(data_file) as f:
            data = read_table_data(data_file, f, **parsing_options)

        mapping_options = annotation.mapping_options
        if mapping_options:
            row_sections_counter: dict[str, int] = {}
            for mapping_option in mapping_options:
                try:
                    file_mode = mapping_option.file_mode
                    mapping_mode = mapping_option.mapping_mode
                    column_sections = (
                        mapping_option.sections
                        if mapping_mode == TabularMode.column
                        else None
                    )
                    row_sections = (
                        mapping_option.sections
                        if mapping_mode == TabularMode.row
                        else None
                    )
                    if row_sections:
                        keys = [key.split('/')[0] for key in row_sections]
                        for key in keys:
                            if key in row_sections_counter:
                                row_sections_counter[key] += 1
                            else:
                                row_sections_counter[key] = 0
                except Exception:
                    raise TabularParserError(
                        "Couldn't extract the list of mapping_options. Double-check the mapping_options"
                    )

                if file_mode == TabularFileModeEnum.current_entry:
                    # Checking for any quantities in the root level of the TableData that is
                    # supposed to be filled from the excel file
                    for quantity_name, quantity in self.m_def.all_properties.items():
                        if (
                            isinstance(quantity, Quantity)
                            and getattr(self, quantity_name) is None
                            and quantity.m_get_annotations('tabular') is not None
                        ):
                            tab = (
                                quantity.m_get_annotations('tabular', as_list=True)
                                or []
                            )
                            if not tab or tab[0] is None:
                                continue
                            col_data = tab[0].name
                            if '/' in col_data:
                                # extract the sheet & col names if there is a '/' in the 'name'
                                sheet_name, col_name = col_data.split('/')
                                if sheet_name not in list(data):
                                    continue
                                try:
                                    df = pd.DataFrame.from_dict(data.loc[0, sheet_name])
                                    tmp = np.array(df.loc[:, col_name])
                                    self.m_set(
                                        quantity,
                                        tmp.item() if quantity.is_scalar else tmp,
                                    )
                                except Exception:
                                    continue
                            else:
                                # Otherwise, assume the sheet_name is the first sheet of Excel/csv
                                try:
                                    df = pd.DataFrame.from_dict(data.iloc[0, 0])
                                    tmp = np.array(df.loc[:, col_data])
                                    self.m_set(
                                        quantity,
                                        tmp.item() if quantity.is_scalar else tmp,
                                    )
                                except Exception:
                                    continue
                    if column_sections and root_mapping['root'] in column_sections:
                        parse_columns(data, self)
                        column_sections = [
                            s for s in column_sections if s != root_mapping['root']
                        ]
                    if column_sections:
                        _parse_column_mode(self, column_sections, data, logger=logger)
                    if row_sections:
                        _parse_row_mode(self, row_sections, data, logger)

                if file_mode == TabularFileModeEnum.multiple_new_entries:
                    for index, row_section in enumerate(row_sections):
                        if index > 0:
                            if logger is not None:
                                logger.warning(
                                    f'{row_section} is not parsed.'
                                    f'Consider creating a new mapping mode to create new entries from this section'
                                )
                        else:
                            if row_section == root_mapping['root']:
                                self._parse_entry_mode(
                                    data,
                                    self.m_def,
                                    archive,
                                    is_root=root_mapping['root'],
                                    data_file=data_file,
                                    logger=logger,
                                )
                            else:
                                entry_section_list = row_section.split('/')
                                entry_section_instance = create_subsection(
                                    self.m_def.all_properties[
                                        entry_section_list.pop(0)
                                    ],
                                    entry_section_list,
                                )

                                self._parse_entry_mode(
                                    data, entry_section_instance, archive, logger=logger
                                )

                if file_mode == TabularFileModeEnum.single_new_entry:
                    if column_sections:
                        self._parse_single_new_entry(
                            parse_columns, data, column_sections, archive, None, logger
                        )
                    if row_sections:
                        self._parse_single_new_entry(
                            _parse_row_mode,
                            data,
                            row_sections,
                            archive,
                            row_sections_counter,
                            logger,
                        )

        else:
            parse_columns(data, self)

        # If the `fill_archive_from_datafile` checkbox is set to be hidden for this specific section, parser's logic
        # also needs to be modified to 'Always run the parser if it's been called'.
        eln_annotation = self.m_def.m_get_annotations('eln', None)
        try:
            self.fill_archive_from_datafile = (
                'fill_archive_from_datafile' in eln_annotation.hide
            )
        except AttributeError:
            self.fill_archive_from_datafile = False

    def _parse_single_new_entry(
        self, parser, data, section_list, archive, sections_counter, logger
    ):
        section_to_write = {}
        for single_entry_section in section_list:
            target_section_str = (
                single_entry_section.split('/')[0]
                if '/' in single_entry_section
                else single_entry_section
            )
            if not section_to_write:
                if target_section_str == root_mapping['root']:
                    target_section = self.m_def.section_cls()
                    section_to_entry = target_section
                elif target_section_str in single_entry_section:
                    target_section = self.m_def.all_properties[
                        target_section_str
                    ].sub_section.section_cls()
                    section_to_entry = target_section
                is_quantity_def = False
                for quantity_def in target_section.m_def.all_quantities.values():
                    if isinstance(quantity_def.type, Reference):
                        try:
                            section_to_entry = (
                                quantity_def.type.target_section_def.section_cls()
                            )
                            is_quantity_def = True
                        except AttributeError:
                            continue
                section_to_write = section_to_entry
            if not any(
                item.name in ('EntryData', 'ArchiveSection')
                for item in section_to_entry.m_def.all_base_sections
            ):
                logger.warning(
                    f'make sure to inherit from EntryData in the base sections of {section_to_entry.m_def.name}'
                )
            if not is_quantity_def:
                pass
                # raise TabularParserError(
                #     f"To create a new entry from {target_section_str}, it should be of type Reference.")
            if parser.__code__.co_argcount == 2:
                parser(data, section_to_write)
            else:
                # If there is a match, then remove the matched sections from row_sections so the main entry
                # does not populate the matched row_section
                matched_rows = [re.sub(r'^.*?\/', '', single_entry_section)]
                parser(section_to_write, matched_rows, data, logger)
            entry_name = set_entry_name(
                quantity_def, target_section, with_index=True, index=0
            )

            counter = sections_counter[target_section_str] if target_section_str else ''
            filename = f'{target_section.m_def.name}{counter}.archive.yaml'
            child_entry_id = generate_entry_id(
                archive.m_context.upload_id, filename, None
            )

            if is_quantity_def:
                ref_quantity_proxy = MProxy(
                    m_proxy_value=f'../upload/archive/{child_entry_id}#/data',
                    m_proxy_context=self.m_context,
                )
                target_section.m_set(quantity_def, ref_quantity_proxy)

                if hasattr(self, single_entry_section.split('/')[0]):
                    setattr(self, single_entry_section.split('/')[0], None)
                self.m_add_sub_section(
                    self.m_def.all_properties[single_entry_section.split('/')[0]],
                    target_section,
                )

        from nomad.datamodel import EntryArchive, EntryMetadata

        section_to_entry.fill_archive_from_datafile = False
        child_archive = EntryArchive(
            data=section_to_entry,
            m_context=archive.m_context,
            metadata=EntryMetadata(
                upload_id=archive.m_context.upload_id, entry_name=entry_name
            ),
        )
        create_archive(
            child_archive.m_to_dict(), archive.m_context, filename, 'yaml', logger
        )

    def _parse_entry_mode(
        self, data, subsection_def, archive, is_root=False, data_file=None, logger=None
    ):
        section = None
        is_referenced_section = False
        if is_root:
            section = subsection_def
            quantity_def = subsection_def
            child_sections = parse_table(data, subsection_def, logger=logger)
        else:
            for quantity_def in subsection_def.sub_section.all_quantities.values():
                if isinstance(quantity_def.type, Reference):
                    try:
                        section = quantity_def.type.target_section_def.section_cls
                        is_referenced_section = True
                        break
                    except AttributeError:
                        continue
            if not section:
                section = subsection_def.sub_section.section_cls()
            child_sections = parse_table(data, section.m_def, logger=logger)

        try:
            all_base_sections = section.all_base_sections
        except Exception:
            all_base_sections = section.m_def.all_base_sections
        if not any(item.name == 'EntryData' for item in all_base_sections):
            logger.warning(
                f'make sure to inherit from EntryData in your base sections in {section.name}'
            )

        try:
            if getattr(self, subsection_def.name):
                setattr(
                    self, subsection_def.name, MSubSectionList(self, subsection_def)
                )
        except AttributeError:
            pass

        # creating new entries for each new child_archives
        from nomad.datamodel import EntryArchive, EntryMetadata

        # if mode is #root when creating multiple new entries, then append the first child to the current entry,
        # and create new ones from second child onwards
        ref_entry_name = None
        if is_root:
            first_child = child_sections.pop(0)
            try:
                ref_entry_name: str = first_child.m_def.more.get('label_quantity', None)
            except Exception:
                ref_entry_name = first_child.m_def.name
            first_child.m_context = archive.m_context
            self.m_update_from_dict(first_child.m_to_dict())

        file_type = 'yaml'
        for index, child_section in enumerate(child_sections):
            try:
                mainfile_name = getattr(
                    child_section, section.m_def.more.get('label_quantity', None)
                )
            except (AttributeError, TypeError):
                logger.info(
                    'could not extract the mainfile from metadata. Setting a default name.'
                )
                mainfile_name = section.m_def.name

            if mainfile_name.endswith('yaml'):
                mainfile_name = mainfile_name.split('.archive.yaml')[0]
            elif mainfile_name.endswith('json'):
                mainfile_name = mainfile_name.split('.archive.json')[0]
                file_type = 'json'
            if '.entry_data' in mainfile_name:
                return
            if ref_entry_name:
                ref_entry_name: str = child_section.m_def.more.get(
                    'label_quantity', None
                )
                filename = f'{getattr(child_section, ref_entry_name, None)}_{index}.{child_section.m_def.name}.archive.{file_type}'
            else:
                filename = f'{mainfile_name}_{index}.{child_section.m_def.name}.archive.{file_type}'

            entry_name: str = set_entry_name(
                quantity_def, child_section, with_index=False
            )

            try:
                for data_quantity_def in child_section.m_def.all_quantities.values():
                    annotation = data_quantity_def.m_get_annotations('tabular_parser')
                    if annotation:
                        child_section.m_update_from_dict(
                            {annotation.m_definition.name: data_file}
                        )
                child_section.fill_archive_from_datafile = False
                child_archive = EntryArchive(
                    data=child_section,
                    m_context=archive.m_context,
                    metadata=EntryMetadata(
                        upload_id=archive.m_context.upload_id, entry_name=entry_name
                    ),
                )
            except Exception:
                raise TabularParserError('New entries could not be generated.')
            create_archive(
                child_archive.m_to_dict(),
                archive.m_context,
                filename,
                file_type,
                logger=logger,
            )

            if is_referenced_section:
                child_entry_id = generate_entry_id(
                    archive.m_context.upload_id, filename, None
                )
                ref_quantity_proxy = MProxy(
                    m_proxy_value=f'../upload/archive/{child_entry_id}#/data',
                    m_proxy_context=self.m_context,
                )
                section_ref: MSection = subsection_def.sub_section.section_cls()
                section_ref.m_set(quantity_def, ref_quantity_proxy)

                self.m_add_sub_section(subsection_def, section_ref)


m_package.__init_metainfo__()


def set_entry_name(quantity_def, child_section, with_index=True, index=0) -> str:
    try:
        name = child_section.m_def.more.get('label_quantity', None)
        entry_name = (
            f'{child_section[name]}_{index}' if with_index else f'{child_section[name]}'
        )
    except Exception:
        if name := getattr(child_section.m_def, 'name'):
            entry_name = f'{name}_{index}' if with_index else f'{name}'
        elif isinstance(quantity_def.type, Reference):
            entry_name = (
                f'{quantity_def.type.target_section_def.name}_{index}'
                if with_index
                else f'{quantity_def.type.target_section_def.name}'
            )
        else:
            entry_name = (
                f'{quantity_def.name}_{index}' if with_index else f'{quantity_def.name}'
            )
    return entry_name


def _parse_column_mode(main_section, list_of_columns, data, logger=None):
    for column_section in list_of_columns:
        if column_section != root_mapping['root']:
            try:
                column_section_list = column_section.split('/')
                section = create_subsection(
                    main_section.m_def.all_properties[column_section_list.pop(0)],
                    column_section_list,
                ).sub_section.section_cls()
            except Exception:
                logger.error(
                    f'{column_section} sub_section does not exist. There might be a problem in schema definition'
                )
            parse_columns(data, section)
            setattr(main_section, column_section, section)


def append_section_to_subsection(
    main_section, section_name: str, source_section: MSection
):
    section_name_list = section_name.split('/')
    top_level_section = section_name_list[0]
    self_updated = getattr(main_section, top_level_section)
    section_updated = source_section
    for section_path in section_name_list[1:]:
        self_updated = self_updated[section_path]
        section_updated = section_updated[section_path]
    if len(section_name_list) == 1:
        self_updated.append(section_updated)
    else:
        self_updated.append(section_updated[0])


def _parse_row_mode(main_section, row_sections, data, logger):
    # Getting list of all repeating sections where new instances are going to be read from excel/csv file
    # and appended.
    section_names: list[str] = row_sections

    # A list to track if the top-most level section has ever been visited
    list_of_visited_sections: list[str] = []

    for section_name in section_names:
        section_name_list = section_name.split('/')
        section_name_str = section_name_list[0]
        try:
            target_sub_section = main_section.m_def.all_properties[section_name_str]
            section_def = target_sub_section.sub_section
        except Exception:
            raise TabularParserError('row-mode failed to parse the list of subsections')

        if not list_of_visited_sections.count(section_name_str):
            list_of_visited_sections.append(section_name_str)

            # The (sub)section needs to be cleared first
            if target_sub_section.repeats:
                setattr(
                    main_section,
                    section_name_str,
                    MSubSectionList(main_section, target_sub_section),
                )
            else:
                setattr(main_section, section_name_str, section_def.section_cls())

        sections = parse_table(data, section_def, logger=logger)
        for section in sections:
            append_section_to_subsection(main_section, section_name, section)


def create_subsection(section, section_name):
    if len(section_name) < 2:
        return section
    else:
        create_subsection(
            section.sub_section.all_properties[section_name.pop(0)], section_name
        )


def _get_relative_path(section_def) -> Iterator[str]:
    if section_def.m_parent:
        yield from _get_relative_path(section_def.m_parent)
    yield (
        section_def.m_parent_sub_section.name
        if section_def.m_parent_sub_section
        else section_def.m_def.name
    )


@cached(LRUCache(maxsize=10))
def _create_column_to_quantity_mapping(section_def: Section):
    mapping: dict[str, Callable[[MSection, Any], MSection]] = {}

    def add_section_def(section_def: Section, path: list[tuple[SubSection, Section]]):
        properties: set[Property] = set()

        for quantity in section_def.all_quantities.values():
            if quantity in properties:
                continue
            properties.add(quantity)

            annotation = quantity.m_get_annotations('tabular')
            annotation = annotation[0] if isinstance(annotation, list) else annotation
            if annotation and annotation.name:
                col_name = annotation.name

                if col_name in mapping:
                    raise MetainfoError(
                        f'The schema has non unique column names. {col_name} exists twice. '
                        f'Column names must be unique, to be used for tabular parsing.'
                    )

                def set_value(
                    section: MSection,
                    value,
                    section_path_to_top_subsection=[],
                    path=path,
                    quantity=quantity,
                    annotation: TabularAnnotation = annotation,
                ):
                    for sub_section, path_section_def in path:
                        next_section = None
                        try:
                            next_section = section.m_get_sub_section(sub_section, -1)
                        except (KeyError, IndexError):
                            pass
                        if not next_section:
                            next_section = path_section_def.section_cls()
                            section.m_add_sub_section(sub_section, next_section)
                        section = next_section

                    if annotation and annotation.unit:
                        value = ureg.Quantity(
                            value.to_numpy() if isinstance(value, pd.Series) else value,
                            annotation.unit,
                        )

                    # NaN values are not supported in the metainfo. Set as None
                    # which means that they are not stored.
                    if isinstance(value, float) and math.isnan(value):
                        value = None

                    if isinstance(value, int | float | str | pd.Timestamp):
                        value = np.array([value])

                    if value is not None:
                        if len(value.shape) == 1 and len(quantity.shape) == 0:
                            if len(value) == 1:
                                value = value[0]
                            elif len(value) == 0:
                                value = None
                            else:
                                raise MetainfoError(
                                    f'The shape of {quantity.name} does not match the given data.'
                                )
                        elif len(value.shape) != len(quantity.shape):
                            raise MetainfoError(
                                f'The shape of {quantity.name} does not match the given data.'
                            )

                    section.m_set(quantity, value)
                    _section_path_list: list[str] = list(_get_relative_path(section))
                    _section_path_str: str = '/'.join(_section_path_list)
                    section_path_to_top_subsection.append(_section_path_str)

                mapping[col_name] = set_value

        for sub_section in section_def.all_sub_sections.values():
            if sub_section in properties:
                continue
            next_base_section = sub_section.sub_section
            properties.add(sub_section)
            add_section_def(
                next_base_section,
                path
                + [
                    (
                        sub_section,
                        next_base_section,
                    )
                ],
            )

    add_section_def(section_def, [])
    return mapping


def parse_columns(pd_dataframe, section: MSection):
    """
    Parses the given pandas dataframe and adds columns (all values as array) to
    the given section.
    """
    import pandas as pd

    data: pd.DataFrame = pd_dataframe

    mapping = _create_column_to_quantity_mapping(section.m_def)  # type: ignore
    data_dict = data.to_dict()
    for column in mapping:
        if '/' in column:
            # extract the sheet & col names if there is a '/' in the 'name'
            sheet_name, col_name = column.split('/')
            if sheet_name not in list(data_dict[0]):
                raise ValueError(
                    f"The sheet name {sheet_name} doesn't exist in the excel file"
                )
            df = pd.DataFrame.from_dict(data_dict[0][sheet_name])
            # trimming the column names from leading/trailing white-spaces
            _strip_whitespaces_from_df_columns(df)
            mapping[column](section, df.loc[:, col_name])
        else:
            # Otherwise, assume the sheet_name is the first sheet of Excel/csv
            df = pd.DataFrame.from_dict(data.iloc[0, 0])

            # trimming the column names from leading/trailing white-spaces
            _strip_whitespaces_from_df_columns(df)
            if column in df:
                mapping[column](section, df.loc[:, column])


def parse_table(pd_dataframe, section_def: Section, logger):
    """
    Parses the given pandas dataframe and creates a section based on the given
    section_def for each row. The sections are filled with the cells from
    their respective row.
    """
    import pandas as pd

    data: pd.DataFrame = pd_dataframe
    data_dict = data.to_dict()
    sections: list[MSection] = []
    sheet_name = list(data_dict[0])[0]

    mapping = _create_column_to_quantity_mapping(section_def)  # type: ignore

    # data object contains the entire excel file with all of its sheets (given that an
    # excel file is provided, otherwise it contains the csv file). if a sheet_name is provided,
    # the corresponding sheet_name from the data is extracted, otherwise its assumed that
    # the columns are to be extracted from first sheet of the excel file.
    for column in mapping:
        if column == section_def.name:
            continue
        if '/' in column:
            sheet_name = column.split('/')[0]

    logger.info(f'Reading data from the sheet {sheet_name}!')
    df = pd.DataFrame.from_dict(data_dict[0][sheet_name])

    # trimming the column names from leading/trailing white-spaces
    _strip_whitespaces_from_df_columns(df)

    # Extracting column with exact same names. For each similar column that will be mapped to a quantity, we need
    # to append a section the proper subsection.
    max_no_of_repeated_columns = 0
    for col in list(df):
        try:
            no_of_stacked_section = int(col.split('.')[1])
            max_no_of_repeated_columns = max(
                no_of_stacked_section, max_no_of_repeated_columns
            )
        except ValueError as e:
            logger.error(
                'No dot (.) is allowed in the column name.',
                details=dict(column=col),
                exc_info=e,
            )
        except Exception:
            continue

    path_quantities_to_top_subsection: set[str] = set()
    for row_index, row in df.iterrows():
        for col_index in range(0, max_no_of_repeated_columns + 1):
            section = section_def.section_cls()
            try:
                for column in mapping:
                    col_name = column.split('/')[1] if '/' in column else column
                    col_name = f'{col_name}.{col_index}' if col_index > 0 else col_name

                    if col_name in df:
                        try:
                            temp_quantity_path_container: list[str] = []
                            mapping[column](
                                section,
                                row[col_name],
                                section_path_to_top_subsection=temp_quantity_path_container,
                            )
                        except Exception as e:
                            logger.error(
                                'could not parse cell',
                                details=dict(row=row_index, column=col_name),
                                exc_info=e,
                            )
                        if (
                            col_index > 0
                            and temp_quantity_path_container[0].split('/')[1:]
                        ):
                            path_quantities_to_top_subsection.update(
                                temp_quantity_path_container
                            )
                        elif (
                            col_index > 0
                            and not temp_quantity_path_container[0].split('/')[1:]
                        ):
                            raise TabularParserError(
                                f'there is a repeated column {column} that is not placed into a subsection in the schema. Please fix the schema.'
                            )
            except Exception as e:
                logger.error(
                    'could not parse row', details=dict(row=row_index), exc_info=e
                )

            # if there is no other similar columns/quantities in the Excel file, or it is the first time this quantity
            # is getting parsed, just create the section and append it to list of sections. Otherwise, append the
            # appropriate section to the repeating subsection.
            if col_index == 0:
                sections.append(section)
            else:
                try:
                    for item in path_quantities_to_top_subsection:
                        section_name: list[str] = item.split('/')[1:]
                        _append_subsections_from_section(
                            section_name, sections[row_index], section
                        )
                except Exception as e:
                    logger.error(
                        'could not append repeating columns to the subsection',
                        details=dict(row=row_index),
                        exc_info=e,
                    )
    return sections


def _strip_whitespaces_from_df_columns(df):
    transformed_column_names: dict[str, str] = {}
    for col_name in list(df.columns):
        cleaned_col_name = col_name.strip().split('.')[0]
        count = 0
        for string in transformed_column_names.values():
            if cleaned_col_name == string.split('.')[0]:
                count += 1
        if count:
            transformed_column_names.update({col_name: f'{cleaned_col_name}.{count}'})
        else:
            transformed_column_names.update({col_name: col_name.strip()})
    df.rename(columns=transformed_column_names, inplace=True)


def _append_subsections_from_section(
    section_name: list[str], target_section: MSection, source_section: MSection
):
    if len(section_name) == 1:
        for (
            sub_section_name,
            sub_section_content,
        ) in target_section.m_def.all_sub_sections.items():
            if sub_section_name == section_name[0] and sub_section_content.repeats:
                target_section[section_name[0]].append(
                    source_section[section_name[0]][0]
                )
    else:
        top_level_section = section_name.pop(0)
        target_section = getattr(target_section, top_level_section)
        source_section = getattr(source_section, top_level_section)
        if isinstance(target_section, list) and len(target_section) != 0:
            target_section = target_section[0]
            source_section = source_section[0]
        _append_subsections_from_section(section_name, target_section, source_section)


def read_table_data(
    path,
    file_or_path=None,
    comment: str | None = None,
    sep: str | None = None,
    skiprows: list[int] | int | None = None,
    separator: str | None = None,
    filters: dict | None = None,
):
    import pandas as pd

    def filter_columns(df: pd.DataFrame, filters: None | dict) -> pd.DataFrame:
        if not filters:
            return df

        if '__has_key' in filters:
            column_name = filters['__has_key']
            if column_name in df.columns:
                return df[[column_name]]

        if '__has_all_keys' in filters:
            columns = [col for col in filters['__has_all_keys'] if col in df.columns]
            if len(columns) == len(filters['__has_all_keys']):
                return df[columns]

        if '__has_only_keys' in filters:
            columns = [col for col in filters['__has_only_keys'] if col in df.columns]
            if set(columns) == set(filters['__has_only_keys']):
                return df[columns]

        return df

    if file_or_path is None:
        file_or_path = path

    if path.endswith('.xls') or path.endswith('.xlsx'):
        excel_file: pd.ExcelFile = pd.ExcelFile(
            file_or_path if isinstance(file_or_path, str) else file_or_path.name
        )
        data = {
            0: {
                sheet: filter_columns(
                    pd.read_excel(
                        excel_file,
                        skiprows=skiprows,
                        sheet_name=sheet,
                        comment=comment,
                    ),
                    filters,
                ).to_dict()
                for sheet in excel_file.sheet_names
            }
        }
    else:
        df = pd.read_csv(
            file_or_path,
            engine='python',
            comment=comment,
            sep=sep if sep else separator,
            skiprows=skiprows,
            skipinitialspace=True,
        )
        df = filter_columns(df, filters)
        data = {0: {0: df.to_dict()}}

    return pd.DataFrame(data)


class TabularDataParser(MatchingParser):
    creates_children = True

    def __init__(self) -> None:
        super().__init__(
            name='parsers/tabular',
            code_name='tabular data',
            domain=None,
            mainfile_mime_re=r'text/.*|application/.*',
            mainfile_name_re=r'.*\.archive\.(csv|xlsx?)$',
        )

    def _get_schema(self, filename: str, mainfile: str):
        dir = os.path.dirname(filename)
        match = re.match(
            r'^(.+\.)?([\w\-]+)\.archive\.(csv|xlsx?)$', os.path.basename(filename)
        )
        if not match:
            return None

        schema_name = match.group(2)
        for extension in ['yaml', 'yml', 'json']:
            schema_file_base = f'{schema_name}.archive.{extension}'
            schema_file = os.path.join(dir, schema_file_base)
            if not os.path.exists(schema_file):
                continue
            return os.path.join(os.path.dirname(mainfile), schema_file_base)

        return None

    def is_mainfile(
        self,
        filename: str,
        mime: str,
        buffer: bytes,
        decoded_buffer: str,
        compression: str | None = None,
    ) -> bool | Iterable[str]:
        # We use the main file regex capabilities of the superclass to check if this is a
        # .csv file
        import pandas as pd

        is_tabular = super().is_mainfile(
            filename, mime, buffer, decoded_buffer, compression
        )
        if not is_tabular:
            return False

        try:
            data = read_table_data(filename)
        except Exception:
            # If this cannot be parsed as a .csv file, we don't match with this file
            return False
        data = pd.DataFrame.from_dict(data.iloc[0, 0])
        return [str(item) for item in range(0, data.shape[0])]

    def parse(self, logger=None, **kwargs):
        if logger is None:
            logger = utils.get_logger(__name__)
        logger.error(
            """
        You are trying to use the legacy tabular parser. Now it is encapsulated in buitl-in TableData
        """
        )
