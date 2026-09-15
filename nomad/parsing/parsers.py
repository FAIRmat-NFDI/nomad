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

import bz2
import gzip
import lzma
import os.path
from collections.abc import Callable, Iterable

import magic

from nomad.config import config
from nomad.config.models.plugins import ParserEntryPoint
from nomad.datamodel import EntryArchive, EntryMetadata, results
from nomad.datamodel.context import ClientContext, ServerLocalContext
from nomad.tracing import traced

from .artificial import EmptyParser
from .parser import (
    ArchiveParser,
    BrokenParser,
    MatchingParserInterface,
    MissingParser,
    Parser,
)
from .tabular import TabularDataParser

_compressions: dict[bytes, tuple[str, Callable]] = {
    b'\x1f\x8b\x08': ('gz', gzip.open),
    b'\x42\x5a\x68': ('bz2', bz2.open),
    b'\xfd\x37\x7a': ('xz', lzma.open),
}

encoding_magic = magic.Magic(mime_encoding=True)


@traced
def match_parser(
    mainfile_path: str, strict=True, parser_name: str | None = None
) -> tuple[Parser, list[str]]:
    """
    Performs parser matching. This means it take the given mainfile and potentially
    opens it with the given callback and tries to identify a parser that can parse
    the file.

    This is determined by filename (e.g. *.out), mime type (e.g. text/*, application/xml),
    and beginning file contents.

    Arguments:
        mainfile_path: Path to the mainfile
        strict: Only match strict parsers, e.g. no artificial parsers for missing or empty entries.
        parser_name: Optional, to force the matching to test only a specific parser

    Returns:
        A tuple (`parser`, `mainfile_keys`). The `parser` is the matched parser, and
        `mainfile_keys` defines the keys to use for child entries, if any. If there are
        no child entries, `mainfile_keys` will be None. If no parser matches, we return
        (None, None).
    """
    mainfile = os.path.basename(mainfile_path)
    if mainfile.startswith('.') or mainfile.startswith('~'):
        return None, None

    with open(mainfile_path, 'rb') as f:
        compression, open_compressed = _compressions.get(f.read(3), (None, open))

    with open_compressed(mainfile_path, 'rb') as cf:  # type: ignore
        buffer = cf.read(config.process.parser_matching_size)

    mime_type = magic.from_buffer(buffer, mime=True)

    decoded_buffer = None
    encoding = None
    try:  # Try to open the file as a string for regex matching.
        decoded_buffer = buffer.decode('utf-8')
    except UnicodeDecodeError:
        # This file is either binary or has wrong encoding
        encoding = encoding_magic.from_buffer(buffer)

        if config.services.force_raw_file_decoding:
            encoding = 'iso-8859-1'

        if encoding in ['iso-8859-1']:
            try:
                decoded_buffer = buffer.decode(encoding)
            except Exception:
                pass
    if parser_name:
        parser = parser_dict.get(parser_name)
        assert parser is not None, f'parser by the name `{parser_name}` does not exist'
        parsers_to_check = [parser]
    else:
        parsers_to_check = parsers
    for parser in parsers_to_check:
        if strict and isinstance(parser, MissingParser | EmptyParser):
            continue

        match_result = parser.is_mainfile(
            mainfile_path, mime_type, buffer, decoded_buffer, compression
        )
        if match_result:
            if isinstance(match_result, Iterable):
                assert parser.creates_children, (
                    'Illegal return value - parser does not specify `creates_children`'
                )
                for mainfile_key in match_result:  # type: ignore
                    assert mainfile_key and isinstance(mainfile_key, str), (
                        f'Child keys must be strings, got {type(mainfile_key)}'
                    )
                mainfile_keys = sorted(match_result)  # type: ignore
            else:
                mainfile_keys = None

            # potentially convert the file
            if encoding in ['iso-8859-1']:
                try:
                    with open(mainfile_path, 'rb') as binary_file:
                        content = binary_file.read().decode(encoding)
                except Exception:
                    pass
                else:
                    with open(mainfile_path, 'w') as text_file:
                        text_file.write(content)

            # TODO: deal with multiple possible parser specs
            return parser, mainfile_keys

    if parser_name and parser:
        return parser, None  # Ignore any child entries

    return None, None


def run_parser(
    mainfile_path: str,
    parser: Parser,
    mainfile_keys: list[str] | None = None,
    logger=None,
    server_context: bool = False,
    username: str | None = None,
    password: str | None = None,
    *,
    recursive_kwargs: dict | None = None,
) -> list[EntryArchive]:
    """
    Parses a file, given the path, the parser, and mainfile_keys, as returned by
    :func:`match_parser`, and returns the resulting EntryArchive objects. Parsers that have
    `create_children == False` (the most common case) will generate a list with a single entry,
    for parsers that create children the list will consist of the main entry followed by the
    child entries. The returned archive objects will have minimal metadata.
    """
    directory = os.path.dirname(mainfile_path)
    if server_context:
        # TODO this looks totally wrong. ParserContext is not a server context at all.
        # There should be three different context. Client, Server and Parser. Currently,
        # ClientContext seems to cover both the client and the local parser use-case.
        entry_archive = EntryArchive(m_context=ServerLocalContext(directory))
    else:
        entry_archive = EntryArchive(
            m_context=ClientContext(
                local_dir=directory,
                username=username,
                password=password,
                recursive_kwargs=recursive_kwargs,
            )
        )

    entry_archive.metadata = EntryMetadata()
    entry_archive.metadata.mainfile = mainfile_path

    entry_archives = [entry_archive]
    if mainfile_keys:
        child_archives = {}
        for mainfile_key in mainfile_keys:
            child_archive = EntryArchive()
            child_metadata = child_archive.m_create(EntryMetadata)
            child_metadata.mainfile = mainfile_path
            child_metadata.mainfile_key = mainfile_key
            child_archives[mainfile_key] = child_archive
            entry_archives.append(child_archive)
        kwargs = dict(child_archives=child_archives)
    else:
        kwargs = {}

    cwd = os.getcwd()
    try:
        mainfile_path = os.path.abspath(mainfile_path)
        os.chdir(os.path.abspath(os.path.dirname(mainfile_path)))
        parser.parse(mainfile_path, entry_archive, logger=logger, **kwargs)
    except Exception as e:
        if logger:
            logger.error('parsing was not successful', exc_info=e)
        raise e
    finally:
        os.chdir(cwd)
    for entry_archive in entry_archives:
        if entry_archive.metadata.domain is None:
            entry_archive.metadata.domain = parser.domain

    entry_archives.extend(entry_archives[0].m_context.child_archives)

    return entry_archives


parsers = []
config.load_plugins()
enabled_entry_points = []
if config.plugins is not None:
    enabled_entry_points = config.plugins.entry_points.filtered_values()

# Load parsers using new plugin mechanism. The entry point name is used to
# identify the parser.
for entry_point in enabled_entry_points:
    if isinstance(entry_point, ParserEntryPoint):
        entry_point_name = entry_point.id
        instance = entry_point.load()
        assert isinstance(instance, Parser), (
            f'Error loading entry point "{entry_point.id}": The load method of a parser entry point must return a Parser instance'
        )
        instance.name = entry_point_name
        aliases = list(entry_point.aliases)
        if entry_point.name and entry_point.name not in aliases:
            aliases.append(entry_point.name)
        instance.aliases = aliases
        parsers.append(instance)

parsers.extend([TabularDataParser(), ArchiveParser()])

# There are some entries with PIDs that have mainfiles which do not match what
# the actual parsers expect. We use the EmptyParser to produce placeholder entries
# to keep the PIDs. These parsers will not match for new, non migrated data.
empty_parsers = [
    EmptyParser(
        name='missing/octopus',
        code_name='Octopus',
        code_homepage='https://octopus-code.org/',
        mainfile_name_re=r'(inp)|(.*/inp)',
    ),
    EmptyParser(
        name='missing/crystal',
        code_name='CRYSTAL',
        code_homepage='https://www.crystal.unito.it/index.php',
        mainfile_name_re=r'.*\.cryst\.out',
    ),
    EmptyParser(
        name='missing/wien2k',
        code_name='WIEN2k',
        code_homepage='http://www.wien2k.at/',
        mainfile_name_re=r'.*\.scf',
    ),
    EmptyParser(
        name='missing/fhi-aims',
        code_name='FHI-aims',
        code_homepage='https://aimsclub.fhi-berlin.mpg.de/',
        mainfile_name_re=r'.*\.fhiaims',
    ),
]

if config.process.use_empty_parsers:
    parsers.extend(empty_parsers)

parsers.append(BrokenParser())

""" A dict to access parsers by name. Usually 'parsers/<...>', e.g. 'parsers/vasp'. """
parser_dict: dict[str, Parser] = {
    parser.name: parser for parser in parsers + empty_parsers
}
# Register also aliases
parser_dict.update({alias: parser for parser in parsers for alias in parser.aliases})

# renamed parsers
_renames = {
    'parsers/vaspoutcar': 'parsers/vasp',
    'parser/broken': 'parsers/broken',
    'parser/fleur': 'parsers/fleur',
    'parser/molcas': 'parsers/molcas',
    'parser/octopus': 'parsers/octopus',
    'parser/onetep': 'parsers/onetep',
}

for old_name, new_name in _renames.items():
    if new_name in parser_dict:
        parser_dict[old_name] = parser_dict[new_name]

# register code names as possible statistic value to the dft datamodel
code_names = []
code_metadata = {}
for parser in parsers:
    code_name = getattr(parser, 'code_name', None)
    if (
        getattr(parser, 'domain', None) == 'dft'
        and code_name is not None
        and code_name != 'corrupted mainfile'
        and code_name != 'Template'
    ):
        code_names.append(code_name)
        if parser.metadata:
            code_metadata[code_name] = parser.metadata if parser.metadata else {}
        else:
            code_metadata[code_name] = {}
code_names = sorted(set(code_names), key=lambda code_name: code_name.lower())
results.Simulation.program_name.a_elasticsearch[0].values = code_names + [
    config.services.unavailable_value
]


def import_all_parsers():
    """
    Imports all the parsers. This will instantiate all parser metainfo as a side
    effect.
    """
    for parser in parsers:
        if isinstance(parser, MatchingParserInterface):
            try:
                parser.import_parser_class()  # pylint: disable=no-member

            except Exception as e:
                raise ImportError(f'Failed to load {parser=}') from e
