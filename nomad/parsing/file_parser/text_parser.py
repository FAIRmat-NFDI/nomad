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


import io
import mmap
import re
from collections.abc import Callable
from typing import Any

import numpy as np
import pint

from nomad.metainfo import Quantity as mQuantity
from nomad.parsing.file_parser import FileParser
from nomad.utils import get_logger


class ParsePattern:
    def __init__(self, **kwargs):
        self._head = kwargs.get('head', '')
        self._key = kwargs.get('key', '')
        value = kwargs.get('value', 're_float_array')
        if value.startswith('re_'):
            token = ''
            if 'float' in value:
                token += r'Ee\+\d\.\-'
            if 'int' in value:
                token += r'\d'
            if 'str' in value:
                token += r'\w'
            if 'array' in value:
                token += r' '
            value = rf'[{token}]+'
        self._value = value
        self._tail = kwargs.get('tail', '\n')
        self._re_pattern = None

    @property
    def re_pattern(self):
        if self._re_pattern is None:
            head = rf'{self._head}[\s\S]*?' if self._head else ''
            key = rf'{self._key}\s*\:*\=*\s*' if self._key else ''
            self._re_pattern = rf'{head}{key}\s*\:*\=*\s*({self._value}){self._tail}'
        return self._re_pattern

    def __call__(self, text, repeats=True):
        values = []
        units = []
        if repeats:
            for res in self.re_pattern.finditer(text):
                unit = res.groupdict().get('__unit', None)
                values.append(
                    ''.join(
                        [
                            group.decode()
                            for group in res.groups()
                            if group and group != unit
                        ]
                    )
                )
                units.append(unit.decode() if unit is not None else None)
        else:
            res = self.re_pattern.search(text)
            if res is not None:
                unit = res.groupdict().get('__unit', None)
                units.append(unit.decode() if unit is not None else None)
                values.append(
                    ''.join(
                        [
                            group.decode()
                            for group in res.groups()
                            if group and group != unit
                        ]
                    )
                )


class Quantity:
    """
    Class to define a quantity to be parsed in the TextParser.

    Arguments:
        quantity: string to identify the name or a metainfo quantity to initialize the
            quantity object.
        re_pattern: pattern to be used by re for matching. Ideally, overlaps among
            quantities for a given parser should be avoided.
        sub_parser: instance of TextParser to perform local parsing
            within a matched block
        str_operation: external function to be performed on a matched block
        dtype: data type of the quantity
        unit: unit of the quantity
        shape: shape of the quantity
        repeats: denotes if multiple matches are expected
        convert: switch automatic data type conversion
        comment: character to denote a line to be ignored

    """

    def __init__(
        self,
        quantity: str | mQuantity,
        re_pattern: str | list | ParsePattern,
        **kwargs,
    ):
        self.name: str
        self.dtype: str | Any
        self.unit: str
        self.shape: list[int]
        if isinstance(quantity, str):
            self.name = quantity
            self.dtype = None
            self.unit = None
            self.shape = None
        elif isinstance(quantity, mQuantity):
            self.name = quantity.name
            self.dtype = (
                quantity.type.type
                if isinstance(quantity.type, np.dtype)
                else quantity.type
            )
            self.unit = quantity.unit
            # check if metainfo shape has dependencies
            self.shape = quantity.shape
            if False in [str(i).isdigit() for i in self.shape]:
                self.shape = None
        # override metainfo
        self.dtype = kwargs.get('dtype', self.dtype)
        self.unit = kwargs.get('unit', self.unit)
        self.shape = kwargs.get('shape', self.shape)
        self._re_pattern: str = (
            re_pattern.re_pattern
            if isinstance(re_pattern, ParsePattern)
            else '|'.join(re_pattern)
            if isinstance(re_pattern, list)
            else re_pattern
        )
        if isinstance(re_pattern, str):
            # reformulate regular expression to capture range
            match = re.findall(r'_capture:(.+?)(?:__(?:start|end)|\Z)', re_pattern)
            re_patterns = [m for m in match] if match else [re_pattern]
        elif isinstance(re_pattern, ParsePattern):
            re_patterns = [re_pattern.re_pattern]
        else:
            re_patterns = re_pattern
        self.re_patterns = [re.compile(p.encode()) for p in re_patterns]
        self.multiline = kwargs.get(
            'multiline', isinstance(re_pattern, str) and len(re_patterns) == 1
        )
        self.exact_match = kwargs.get('exact_match', not self.multiline)
        self.units_mapping = kwargs.get('units_mapping', {})
        self.str_operation: Callable = kwargs.get('str_operation', None)
        self.sub_parser: TextParser = kwargs.get('sub_parser', None)
        self.repeats: bool = kwargs.get('repeats', False)
        self.convert: bool = kwargs.get('convert', True)
        self.flatten: bool = kwargs.get('flatten', True)
        self.reduce: bool = kwargs.get('reduce', True)
        self.comment: str = kwargs.get('comment', None)

    @property
    def re_pattern(self):
        """
        Returns a compiled re pattern.
        """
        if isinstance(self._re_pattern, str):
            re_pattern = self._re_pattern.replace('__unit', f'__unit_{self.name}')
            self._re_pattern = re.compile(re_pattern.encode())
        return self._re_pattern

    @re_pattern.setter
    def re_pattern(self, val: str):
        self._re_pattern = val

    def to_data(self, val_raw: str):
        """
        Converts the parsed block into data.
        """

        def convert(val):
            if isinstance(val, str):
                if self.dtype is None:
                    if val.isdecimal():
                        return int(val)
                    else:
                        try:
                            return float(val)
                        except Exception:
                            pass
                else:
                    try:
                        return self.dtype(val)
                    except Exception:
                        pass

                return val

            elif isinstance(val, list | np.ndarray):
                try:
                    dtype = float if self.dtype is None else self.dtype
                    val_test = np.array(val, dtype=dtype)
                    if self.dtype is None:
                        if np.all(np.mod(val_test, 1) == 0):
                            val_test = np.array(val_test, dtype=int)
                            dtype = int
                    return val_test

                except Exception:
                    self.dtype = None
                    return [convert(v) for v in val]

            elif isinstance(val, dict):
                return {k: convert(v) for k, v in val.items()}

            else:
                return val

        if not val_raw:
            return

        if self.comment is not None:
            if val_raw.strip()[0] == self.comment:
                return

        data: Any = val_raw

        if self.str_operation is not None:
            data = self.str_operation(val_raw)

        elif self.flatten:
            data = val_raw.strip().split()
            if self.reduce:
                data = data[0] if len(data) == 1 else data

        if self.convert:
            data = convert(data)

        if isinstance(data, np.ndarray) and self.shape:
            try:
                data = np.reshape(data, self.shape)
            except Exception:
                pass

        return data

    def __repr__(self) -> str:
        if not self.sub_parser:
            return self.name
        sub_quantities = [q.name for q in self.sub_parser.quantities]
        return f'{self.name}({", ".join(sub_quantities[:5])}{"..." if len(sub_quantities) > 5 else ""})'


class TextParser(FileParser):
    """
    Parser for unstructured text files using the re module. The quantities to be parsed
    are given as a list of Quantity objects which specifies the regular expression. The mmap
    module is used to handle the file. By default, re.find_all is used to get matches
    for performance reasons. In this case, overlap is not tolerated in the re patterns.
    To avoid this, set findall to False to switch to re.finditer.

    Arguments:
        mainfile: the path to the file to be parsed
        quantities: list of Quantity objects to be parsed.
        logger: optional logger
        findall: if True will employ re.findall, otherwise re.finditer
        file_offset: offset in reading the file
        file_length: length of the chunk to be read from the file
        allow_overlap: if True, will match each quantity to the file block
        max_lines: maximum number of lines to cache in a multiline search
        line_parsing: if True will perform line by line matching
    """

    def __init__(
        self,
        mainfile: str | None = None,
        quantities: list[Quantity] | None = None,
        logger=None,
        **kwargs,
    ):
        if logger is None:
            logger = get_logger(__name__)
        super().__init__(mainfile, logger=logger, open=kwargs.get('open', None))
        self._quantities: list[Quantity] = quantities
        self.findall: bool = kwargs.get('findall', True)
        self.findlazy: bool = kwargs.get('findlazy', None)
        self._file_length: int = kwargs.get('file_length', 0)
        self._file_offset: int = kwargs.get('file_offset', 0)
        self._file_pad: int = 0
        self._parsed: list[int] = []
        # True if multiple quantities can match a line
        self.allow_overlap = kwargs.get('allow_overlap', False)
        # maximum number of lines to cache in a multiline search
        self.max_lines = kwargs.get('max_lines', 10)
        self.line_parsing = kwargs.get('line_parsing', False)
        if quantities is None:
            self.init_quantities()
        # check quantity patterns are valid
        re_has_group = re.compile(r'\(.+\)')
        for i in range(len(self._quantities) - 1, -1, -1):
            if self._quantities[i].sub_parser:
                continue
            try:
                assert (
                    re_has_group.search(self._quantities[i].re_pattern.pattern.decode())
                    is not None
                )
            except Exception as e:
                self.logger.error(
                    'Invalid quantity pattern',
                    exc_info=e,
                    data=dict(quantity=self.quantities[i].name),
                )
                self._quantities.pop(i)
        self._re_findall: re.Pattern = None

    def copy(self):
        """
        Returns a copy of the object excluding the parsed results.
        """
        return TextParser(
            self.mainfile,
            self.quantities,
            self.logger,
            findall=self.findall,
            findlazy=self.findlazy,
            allow_overlap=self.allow_overlap,
            max_lines=self.max_lines,
            line_parsing=self.line_parsing,
        )

    def init_quantities(self):
        """
        Initializes the quantities list.
        """
        self._quantities = []

    @property
    def quantities(self):
        """
        Returns the list of quantities to be parsed.
        """
        return self._quantities

    @quantities.setter
    def quantities(self, val: list[Quantity]):
        """
        Sets the quantities list.
        """
        self._file_handler = None
        self._results = None
        self._quantities = val

    @property
    def file_offset(self):
        """
        Integer offset in loading the file taking into account mmap pagination.
        """
        return self._file_offset

    @file_offset.setter
    def file_offset(self, val: int):
        """
        Sets starting point where the file is read.
        """
        self._file_pad = val % mmap.PAGESIZE
        self._file_offset = (val // mmap.PAGESIZE) * mmap.PAGESIZE
        self.reset()

    @property
    def file_length(self):
        """
        Length of the file chunk to be loaded.
        """
        return self._file_length

    @file_length.setter
    def file_length(self, val: int):
        """
        Sets the length of the file to be read.
        """
        self._file_length = val
        self.reset()

    @property
    def file_mmap(self):
        """
        Memory mapped representation of the file.
        """
        if self._file_handler is None:
            with self.open(self.mainfile, 'rb') as f:
                if isinstance(f, io.TextIOWrapper):
                    self._file_handler = mmap.mmap(
                        f.fileno(),
                        self._file_length,
                        access=mmap.ACCESS_COPY,
                        offset=self._file_offset,
                    )
                    # set the extra chunk loaded before the intended offset to empty
                    self._file_handler[: self._file_pad] = b' ' * self._file_pad
                else:
                    self._file_handler = [(0, f.seek(0, 2))]
            self._file_pad = 0
        return self._file_handler

    @property
    def file_pointers(self):
        """
        List of (start, end) indices of blocks in the file to be parsed.
        """
        if self._file_handler is None:
            with self.open(self.mainfile, 'rb') as f:
                self._file_handler = [(0, f.seek(0, 2))]
        return self._file_handler

    def keys(self):
        """
        Returns all the quantity names.
        """
        return [quantity.name for quantity in self.quantities]

    def items(self):
        """
        Returns an iterable name, value of the parsed quantities
        """
        for key in self.keys():
            yield key, self.get(key)

    def _add_value(self, quantity: Quantity, value: list[str], units):
        """
        Converts the list of parsed blocks into data and apply the corresponding units.
        """
        try:
            value_processed = [quantity.to_data(val) for val in value]
            for n, _ in enumerate(value_processed):
                unit = units[n] if units[n] else quantity.unit
                if not unit:
                    continue
                if isinstance(unit, str):
                    value_processed[n] = pint.Quantity(value_processed[n], unit)
                else:
                    value_processed[n] = value_processed[n] * unit

            if not quantity.repeats and value_processed:
                value_processed = value_processed[0]

            self._results[quantity.name] = value_processed
        except Exception:
            self.logger.warning(
                'Error setting value', data=dict(quantity=quantity.name)
            )

    def _load_block(self) -> bytes | mmap.mmap:
        """
        Loads the file block to be parsed.
        """
        if not isinstance(self._file_handler, list):
            return self._file_handler

        with self.open(self.mainfile, 'rb') as f:
            block = b''
            for span in self._file_handler:
                f.seek(span[0] + self._file_offset)
                block += f.read(span[1] - span[0])
            return block

    def _parse_quantities(self, quantities: list[Quantity]):
        """
        Parse a list of quantities.
        """
        if len(self._results) == 0 and self._re_findall is not None:
            # attempt at optimization
            re_findall_b = self._re_findall
        else:
            re_findall = '|'.join([q.re_pattern.pattern.decode() for q in quantities])
            if len(quantities) == 1:
                # necessary to add a dummy variable to make multiple matches
                re_findall = f'{re_findall}|(__dummy__)'
            re_findall_b = re.compile(re_findall.encode())
            if self._re_findall is None:
                self._re_findall = re_findall_b

        # map matches to quantities
        block = self._load_block()
        matches = re.findall(re_findall_b, block)
        current_index = 0
        for quantity in quantities:
            values = []
            units = []
            n_groups = quantity.re_pattern.groups

            non_empty_matches = []
            for match in matches:
                if isinstance(match, bytes):
                    match = [match]
                non_empty_match = [
                    m for m in match[current_index : current_index + n_groups] if m
                ]
                if not non_empty_match:
                    continue
                non_empty_matches.append(non_empty_match)
            index_unit = quantity.re_pattern.groupindex.get(
                f'__unit_{quantity.name}', None
            )
            for non_empty_match in non_empty_matches:
                try:
                    if index_unit is not None:
                        unit = non_empty_match.pop(index_unit - 1)
                        units.append(unit.decode())

                    else:
                        units.append(None)

                    values.append(' '.join([m.decode() for m in non_empty_match]))
                except Exception:
                    self.logger.error(
                        'Error parsing quantities.', data=dict(quantity=quantity.name)
                    )

            current_index += n_groups

            if not values:
                continue

            self._add_value(quantity, values, units)

    def _parse_quantity(self, quantity: Quantity):
        """
        Parse a single quantity.
        """
        value = []
        units = []
        block = self._load_block()
        re_matches = (
            quantity.re_pattern.finditer(block)
            if quantity.repeats
            else [quantity.re_pattern.search(block)]
        )
        for res in re_matches:
            if res is None:
                continue
            if quantity.sub_parser is not None:
                sub_parser = quantity.sub_parser.copy()
                sub_parser.mainfile = self.mainfile
                sub_parser.logger = self.logger
                if sub_parser.findlazy is None:
                    sub_parser.findlazy = self.findlazy
                start = res.span(1)[0]
                sub_parser._file_offset = self._file_offset + start
                sub_parser._file_handler = [
                    (res.span(n + 1)[0] - start, res.span(n + 1)[1] - start)
                    for n in range(len(res.groups()))
                ]
                value.append(sub_parser if sub_parser.findlazy else sub_parser.parse())

            else:
                try:
                    unit = res.groupdict().get(f'__unit_{quantity.name}', None)
                    units.append(unit.decode() if unit is not None else None)
                    value.append(
                        ' '.join(
                            [
                                group.decode()
                                for group in res.groups()
                                if group and group != unit
                            ]
                        )
                    )
                except Exception:
                    self.logger.error('Error parsing quantity.')

        if not value:
            return

        if quantity.sub_parser is not None:
            self._results[quantity.name] = value if quantity.repeats else value[0]

        else:
            self._add_value(quantity, value, units)

    def _parse_line(self, key=None):
        self._blocks = [[[None] * len(q.re_patterns)] for q in self.quantities]
        self._units = [None] * len(self.quantities)
        self._multiline = True in [q.multiline for q in self.quantities]
        self._repeats = True in [q.repeats for q in self.quantities]
        with self.open(self.mainfile, 'rb') as fileobj:
            fileobj.seek(self._file_offset)
            # for multiline support
            lines = b''
            n_lines = 0
            parsed = []
            while True:
                position = fileobj.tell()
                line = fileobj.readline()
                if not line:
                    break
                if self._file_length > 0 and position > (
                    self._file_offset + self._file_length
                ):
                    break
                if not self._repeats and False not in [
                    None not in block[-1] for block in self._blocks
                ]:
                    break
                if self._multiline:
                    if n_lines > self.max_lines:
                        n_lines = 0
                        lines = b''
                    n_lines += 1
                    lines += line
                for n_q, quantity in enumerate(self.quantities):
                    if n_q in parsed:
                        continue
                    blocks = self._blocks[n_q][-1]
                    n_re = [n for n, p in enumerate(blocks) if p is None]
                    if not n_re:
                        if not quantity.repeats:
                            parsed.append(n_q)
                            continue
                        else:
                            blocks = [None] * len(quantity.re_patterns)
                            self._blocks[n_q].append(blocks)
                            n_re = [0]

                    if quantity.multiline:
                        match = re.search(quantity.re_patterns[n_re[0]], lines)
                    else:
                        # faster matching
                        if quantity.exact_match:
                            match = re.match(quantity.re_patterns[n_re[0]], line)
                        else:
                            match = re.search(quantity.re_patterns[n_re[0]], line)
                    if match:
                        lines = b''
                        if quantity.sub_parser:
                            block = [
                                match.span(n + 1) for n in range(len(match.groups()))
                            ]
                            if not block:
                                # if nothing is captured capture the whole block
                                block = [match.span()]
                            block = [(s + position, e + position) for s, e in block]
                            blocks[n_re[0]] = block
                        else:
                            values = [g or b'' for g in match.groups()]
                            unit_index = quantity.re_patterns[n_re[0]].groupindex.get(
                                '__unit'
                            )
                            if unit_index:
                                self._units[n_q] = values.pop(unit_index - 1).decode()
                            blocks[n_re[0]] = b' '.join(values).decode()

                        if not self.allow_overlap:
                            break

            for n_q, quantity in enumerate(self.quantities):
                if quantity.sub_parser:
                    data = []
                    for blocks in self._blocks[n_q]:
                        if None in blocks:
                            continue
                        sub_parser = quantity.sub_parser.copy()
                        sub_parser.mainfile = self.mainfile
                        sub_parser.line_parsing = True
                        sub_parser.allow_overlap = self.allow_overlap
                        sub_parser.max_lines = self.max_lines
                        sub_parser._file_offset = blocks[0][0][0]
                        sub_parser._file_length = (
                            blocks[-1][-1][-1] - sub_parser._file_offset
                        )
                        data.append(sub_parser)
                    if data:
                        self._results.setdefault(
                            quantity.name, data if quantity.repeats else data[0]
                        )
                else:
                    blocks = self._blocks.pop(n_q)
                    self._blocks.insert(n_q, None)
                    data = [' '.join(block) for block in blocks if None not in block]
                    if data:
                        data = [quantity.to_data(d) for d in data]
                        unit = (
                            quantity.units_mapping.get(
                                self._units[n_q], self._units[n_q]
                            )
                            or quantity.unit
                        )
                        if unit:
                            data = [
                                pint.Quantity(d, unit)
                                if isinstance(unit, str)
                                else d * unit
                                for d in data
                            ]
                        self._results[quantity.name] = (
                            data if quantity.repeats else data[0]
                        )

    def parse(self, key=None):
        """
        Triggers parsing of quantity with name key, if key is None will parse all quantities.

        Returns file parser.
        """
        if self._results is None:
            self._results = dict()

        if self.line_parsing:
            self._parse_line()
            return

        if self.file_mmap is None:
            return self

        if self.findall:
            if len(self._results) > 1:
                return self

            n_results = 0
            while True:
                # use find all to parse quantities with no sub_parser.
                quantities_findall = [
                    q
                    for q in self.quantities
                    if q.name not in self._results and q.sub_parser is None
                ]
                if not quantities_findall:
                    break

                # recursively parse quantities
                self._parse_quantities(quantities_findall)

                if n_results == len(self._results):
                    # will stop if no more matches are found
                    break
                n_results = len(self._results)

            for quantity in self._quantities:
                if quantity.sub_parser is not None:
                    self._parse_quantity(quantity)

        else:
            for quantity in self._quantities:
                if quantity.name == key or key is None:
                    if quantity.name not in self._results:
                        self._parse_quantity(quantity)

        # free up memory
        if self.findall:
            if isinstance(self._file_handler, mmap.mmap):
                self._file_handler.close()

        return self

    def clear(self):
        """
        Deletes the file mapping for all sub parsers.
        """
        for quantity in self.quantities:
            if quantity.sub_parser is not None:
                quantity.sub_parser.clear()
        self._file_handler = None


class DataTextParser(TextParser):
    """
    Parser for structured data text files using numpy.loadtxt

    Arguments:
        mainfile: the file to be parsed
        dtype: data type
    """

    def __init__(self, **kwargs):
        self._dtype: type = kwargs.get('dtype', float)
        self._mainfile_contents: str = kwargs.get('mainfile_contents', '')
        super().__init__(**kwargs)

    def parse(self, key=None):
        super().parse(key=key)
        if key == 'data' or not self._results:
            try:
                data = None
                if self.mainfile is not None:
                    data = np.loadtxt(
                        self.mainfile,
                        **{
                            key: val
                            for key, val in self._kwargs.items()
                            if key in np.loadtxt.__code__.co_varnames
                        },
                    )
                else:
                    if not self._mainfile_contents and self.mainfile_obj:
                        with self.open_mainfile_obj() as mainfile_obj:
                            self._mainfile_contents = mainfile_obj.read()
                    if self._mainfile_contents:
                        buffer = self._mainfile_contents
                        if isinstance(buffer, str):
                            buffer = buffer.encode()
                        if buffer:
                            data = np.frombuffer(buffer, dtype=self._dtype)
                if data is not None:
                    self._results['data'] = data
            except Exception:
                self.logger.error('Failed to load data file.')
