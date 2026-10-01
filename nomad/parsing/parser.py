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

import importlib
import json
import os
import re
from abc import ABCMeta, abstractmethod
from collections.abc import Iterable
from functools import lru_cache
from typing import IO, Any

import numpy as np
import yaml

from nomad import utils
from nomad.config import config
from nomad.datamodel import EntryArchive, EntryMetadata


class Parser(metaclass=ABCMeta):
    """
    Instances specify a parser. It allows to find *main files* from  given uploaded
    and extracted files. Further, allows to run the parser on those 'main files'.
    """

    name = 'parsers/parser'
    level = 0
    matching_order: int = 0
    creates_children = False
    aliases: list[str] = []
    """
    Level 0 parsers are run first, then level 1, and so on. Normally the value should be 0,
    use higher values only when a parser depends on other parsers.
    """

    def __init__(self):
        self.domain = 'dft'
        self.metadata = None

    @abstractmethod
    def is_mainfile(
        self,
        filename: str,
        mime: str,
        buffer: bytes,
        decoded_buffer: str,
        compression: str | None = None,
    ) -> bool | Iterable[str]:
        """
        Checks if a file is a mainfile for the parser. Should return True or a set of
        *keys* (non-empty strings) if it is a mainfile, otherwise a falsey value.

        The option to return a set of keys should only be used by parsers that have
        `creates_children == True`. These create multiple entries for one mainfile, namely
        a *main* entry and some number of *child* entries. Most parsers, however, have
        `creates_children == False` and thus only generate a main entry, no child entries,
        and these should thus just return a boolean value.

        If the return value is a set of keys, a main entry will be created when parsing,
        plus one child entry for each key in the returned set. The key value will be stored
        in the field `mainfile_key` of the corresponding child entry. Main entries have
        `mainfile_key == None`.

        The combination (`upload_id`, `mainfile`, `mainfile_key`) uniquely identifies an entry
        (regardless of it's a main entry or child entry).

        Arguments:
            filename: The filesystem path to the mainfile
            mime: The mimetype of the mainfile guessed with libmagic
            buffer: The first 2k of the mainfile contents
            compression: The compression of the mainfile ``[None, 'gz', 'bz2']``
        """
        pass

    @abstractmethod
    def parse(
        self,
        mainfile: str,
        archive: EntryArchive,
        logger=None,
        child_archives: dict[str, EntryArchive] | None = None,
    ) -> None:
        """
        Runs the parser on the given mainfile and populates the result in the given
        archive root_section. It allows to be run repeatedly for different mainfiles.

        Args:
            mainfile: A path to a mainfile that this parser can parse.
            archive: An instance of the section :class:`EntryArchive`. It might contain
                a section ``metadata`` with information about the entry.
            logger: A optional logger
            child_archives: a dictionary with {mainfile_key : EntryArchive} for each child,
                for the parse function to populate with data.
        """
        pass

    def after_normalization(self, archive: EntryArchive, logger=None) -> None:
        """
        This is called after the archive produced by `parsed` has been normalized. This
        allows to apply additional code-specific processing steps based on the normalized data.

        Args:
            archive: An instance of the section :class:`EntryArchive`. It might contain
                a section ``metadata`` with information about the entry.
            logger: A optional logger
        """
        pass

    @classmethod
    def main(cls, mainfile, mainfile_keys: list[str] | None = None):
        archive = EntryArchive()
        archive.m_create(EntryMetadata)
        if mainfile_keys:
            child_archives = {}
            for mainfile_key in mainfile_keys:
                child_archive = EntryArchive()
                child_archive.m_create(EntryMetadata)
                child_archives[mainfile_key] = child_archive
            kwargs = dict(child_archives=child_archives)
        else:
            kwargs = {}

        cls().parse(mainfile, archive, **kwargs)  # pylint: disable=no-value-for-parameter
        return archive


class BrokenParser(Parser):
    """
    A parser implementation that just fails and is used to match mainfiles with known
    patterns of corruption.
    """

    name = 'parsers/broken'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.code_name = 'corrupted mainfile'
        self._patterns = [
            re.compile(
                r'^pid=[0-9]+'
            ),  # some 'mainfile' contain list of log-kinda information with pids
            re.compile(r'^Can\'t open .* library:.*'),  # probably bad code runs
        ]

    def is_mainfile(
        self,
        filename: str,
        mime: str,
        buffer: bytes,
        decoded_buffer: str,
        compression: str | None = None,
    ) -> bool:
        if decoded_buffer is not None:
            for pattern in self._patterns:
                if pattern.search(decoded_buffer) is not None:
                    return True

        return False

    def parse(self, mainfile: str, archive, logger=None, child_archives=None):
        raise Exception('Failed on purpose.')


class MatchingParser(Parser):
    """
    A parser implementation that uses regular expressions to match mainfiles.

    Arguments:
        name: The internally used name for the parser. The prefix 'parser/' will
            be automatically added for legacy reasons.
        code_name: The displayed name for the parser
        code_homepage: The homepage of the code or input format
        code_catogory: An optional category for the code.
        mainfile_mime_re: A regexp that is used to match against a files mime type
        mainfile_contents_re: A regexp that is used to match the first 1024 bytes of a
            potential mainfile.
        mainfile_contents_dict: A nested dictionary to match the contents of the file. If provided
            will load the file and match the value of the key(s) provided.
        mainfile_name_re: A regexp that is used to match the paths of potential mainfiles
        mainfile_alternative: If True files are mainfile if no mainfile_name_re matching file
            is present in the same directory.
        matching_order: The order in which the parser is considered when matching files.
            Lower values are considered first.
        domain: The domain that this parser should be used for. Default is 'dft'.
        supported_compressions: A list of [gz, bz2], if the parser supports compressed files
    """

    def __init__(
        self,
        name: str | None = None,
        code_name: str | None = None,
        code_homepage: str | None = None,
        code_category: str | None = None,
        mainfile_contents_re: str | None = None,
        mainfile_binary_header: bytes | None = None,
        mainfile_binary_header_re: bytes | None = None,
        mainfile_mime_re: str = r'text/.*',
        mainfile_name_re: str = r'.*',
        mainfile_alternative: bool = False,
        mainfile_contents_dict: dict | None = None,
        level: int = 0,
        matching_order: int = 0,
        domain='dft',
        metadata: dict | None = None,
        supported_compressions: list[str] = [],
        **kwargs,
    ) -> None:
        super().__init__()

        self.name = name
        self.code_name = code_name
        self.code_homepage = code_homepage
        self.code_category = code_category
        self.metadata = metadata

        self.domain = domain
        self.level = level
        self.matching_order = matching_order
        self._mainfile_binary_header = mainfile_binary_header
        self._mainfile_mime_re = re.compile(mainfile_mime_re)
        self._mainfile_name_re = re.compile(mainfile_name_re)
        self._mainfile_alternative = mainfile_alternative

        # Assign private variable this way to avoid static check issue.
        if mainfile_contents_re is not None:
            self._mainfile_contents_re = re.compile(mainfile_contents_re)
        else:
            self._mainfile_contents_re = None
        if mainfile_binary_header_re is not None:
            self._mainfile_binary_header_re = re.compile(mainfile_binary_header_re)
        else:
            self._mainfile_binary_header_re = None
        self._mainfile_contents_dict = mainfile_contents_dict
        self._supported_compressions = supported_compressions

        self._ls = lru_cache(maxsize=16)(os.listdir)

    def __repr__(self):
        return self.name

    def read_metadata_file(self, metadata_file: str) -> dict[str, Any]:
        """
        Read parser metadata from a yaml file.
        """
        logger = utils.get_logger(__name__)
        try:
            with open(metadata_file, encoding='UTF-8') as f:
                parser_metadata = yaml.load(f, Loader=yaml.SafeLoader)
        except Exception as e:
            logger.warning('failed to read parser metadata', exc_info=e)
            raise

        return parser_metadata

    def is_mainfile(
        self,
        filename: str,
        mime: str,
        buffer: bytes,
        decoded_buffer: str,
        compression: str | None = None,
    ) -> bool | Iterable[str]:
        if self._mainfile_binary_header is not None:
            if self._mainfile_binary_header not in buffer:
                return False

        if self._mainfile_binary_header_re is not None:
            if buffer is not None:
                if self._mainfile_binary_header_re.search(buffer) is None:
                    return False
            else:
                return False

        if self._mainfile_contents_re is not None:
            if decoded_buffer is not None:
                if self._mainfile_contents_re.search(decoded_buffer) is None:
                    return False
            else:
                return False

        if self._mainfile_mime_re.match(mime) is None:
            return False

        if compression is not None and compression not in self._supported_compressions:
            return False

        if self._mainfile_name_re.fullmatch(filename) is None:
            if not self._mainfile_alternative:
                return False

            directory = os.path.dirname(filename)
            basename = os.path.basename(filename)
            siblings = [
                s
                for s in self._ls(directory)
                if s != basename and os.path.isfile(os.path.join(directory, s))
            ]
            matched_siblings = []
            for sibling in siblings:
                sibling = os.path.join(directory, sibling)
                sibling_is_mainfile = (
                    self._mainfile_name_re.fullmatch(sibling) is not None
                )
                if sibling_is_mainfile:
                    # not mainfile if sibling matches current basename
                    sibling_basename = os.path.basename(sibling)
                    if sibling_basename.rsplit('.', 1)[0] == basename.rsplit('.', 1)[0]:
                        return False
                    matched_siblings.append(
                        len(
                            [
                                s
                                for s in siblings
                                if s != sibling_basename
                                and sibling_basename.rsplit('.', 1)[0]
                                == s.rsplit('.', 1)[0]
                            ]
                        )
                        > 0
                    )

            # not mainfile if there are no other files with the same basename as any
            # of the mainfile siblings
            # e.g. for primary .out files with secondary .xml files:
            #     main.out main.xml aux.out another.xml
            # only main.out, aux.out and another.xml should match
            if matched_siblings and True not in matched_siblings:
                return False

        if self._mainfile_contents_dict is not None:
            import h5py

            def match(value, reference):
                if not isinstance(value, dict):
                    equal = value == (
                        reference[()]
                        if isinstance(reference, h5py.Dataset)
                        else reference
                    )
                    return equal.all() if isinstance(equal, np.ndarray) else equal

                if not hasattr(reference, 'keys'):
                    return False

                matches = []
                reference_keys = list(reference.keys())
                tmp = value.pop('__has_comment', None)
                for key, val in value.items():
                    if key == '__has_key':
                        matches.append(any([re.match(val, k) for k in reference_keys]))
                    elif key == '__has_all_keys':
                        assert isinstance(val, list) and isinstance(
                            reference_keys, list
                        )
                        matches.append(False not in [v in reference_keys for v in val])
                    elif key == '__has_only_keys':
                        assert isinstance(val, list) and isinstance(
                            reference_keys, list
                        )
                        matches.append(False not in [v in val for v in reference_keys])
                    else:
                        if key not in reference_keys:
                            matches.append(False)
                            continue

                        matches.append(match(val, reference[key]))
                if tmp:
                    value.update({'__has_comment': tmp})
                return False not in matches

            is_match = False
            if (
                mime.startswith('application/json')
                or mime.startswith('text/plain')
                and not re.match(r'.+\.(?:csv|xlsx?)$', filename)
            ):
                try:
                    if filename.endswith('.son'):
                        with open(filename) as f:
                            text = re.search(r'([\s\S]+?)[\-\=]{3}', f.read())
                        is_match = match(
                            self._mainfile_contents_dict, json.loads(text.group(1))
                        )
                    is_match = match(
                        self._mainfile_contents_dict, json.load(open(filename))
                    )
                except Exception:
                    pass
            elif mime.startswith('application/x-hdf'):
                try:
                    with h5py.File(filename) as f:
                        is_match = match(self._mainfile_contents_dict, f)
                except Exception:
                    pass
            elif re.match(r'.+\.(?:csv|xlsx?)$', filename):
                from nomad.parsing.tabular import read_table_data

                try:
                    comment = self._mainfile_contents_dict.get('__has_comment', None)
                    table_data = read_table_data(
                        filename, comment=comment, filters=self._mainfile_contents_dict
                    )[0]
                    data = (
                        table_data[0]
                        if filename.endswith('csv')
                        else table_data.to_dict()
                    )

                    is_match = match(self._mainfile_contents_dict, data)
                except Exception:
                    pass
            elif mime == 'application/octet-stream' and re.match(r'.+\.nc$', filename):
                from scipy.io import netcdf_file

                try:
                    with netcdf_file(filename) as f:
                        is_match = match(self._mainfile_contents_dict, f.__dict__)
                except Exception:
                    pass

            if not is_match:
                return False

        return True

    def parse(
        self, mainfile: str, archive: EntryArchive, logger=None, child_archives=None
    ) -> None:
        raise NotImplementedError()


# TODO remove this after merging hdf5 reference, only for parser compatibility
def to_hdf5(value: Any, f: str | IO, path: str):
    import h5py

    with h5py.File(f, 'a') as root:
        segments = path.rsplit('/', 1)
        group = root.require_group(segments[0]) if len(segments) == 2 else root
        dataset = group.require_dataset(
            segments[-1],
            shape=value.shape if hasattr(value, 'shape') else (),
            dtype=value.dtype if hasattr(value, 'dtype') else None,
        )
        dataset[...] = value.magnitude if hasattr(value, 'magnitude') else value
    return f'{f if isinstance(f, str) else os.path.basename(f.name)}#{path}'


def import_class(class_name, class_description: str | None = None):
    logger = utils.get_logger(__name__)
    try:
        module_path, cls = class_name.rsplit('.', 1)
        module = importlib.import_module(module_path)
        instance = getattr(module, cls)

    except Exception as e:
        if not class_description:
            logger.error('cannot import', exc_info=e)
        else:
            logger.error(f'cannot import {class_description}', exc_info=e)
        raise e

    return instance


class MatchingParserInterface(MatchingParser):
    """
    An interface to the NOMAD parsers.

    Arguments:
        parser_class_name:
            path specification in python style up to the parser class
            in case of a plugin, the path starts from `src/`.
            E.g. `nomad_parser.parsers.parser.Parser`
            for a `Parser` under `<plugin_root>/src/nomad_parser/parsers/parser.py`.
    """

    def __init__(self, parser_class_name: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._parser_class_name = parser_class_name
        self._mainfile_parser = None

    def new_parser_instance(self):
        """Forgets the existing parser instance and forces the creation of a new one."""
        self._mainfile_parser = None
        return self._mainfile_parser

    @property
    def mainfile_parser(self):
        if self._mainfile_parser is None:
            try:
                Parser = self.import_parser_class()
                self._mainfile_parser = Parser()
            except Exception as e:
                logger = utils.get_logger(__name__)
                logger.error('cannot instantiate parser.', exc_info=e)
                raise e
        return self._mainfile_parser

    def parse(
        self, mainfile: str, archive: EntryArchive, logger=None, child_archives=None
    ):
        # TODO include child_archives in parse
        if child_archives:
            self.mainfile_parser._child_archives = child_archives
        self.mainfile_parser.parse(mainfile, archive, logger)

    def import_parser_class(self):
        return import_class(self._parser_class_name, 'parser')

    def is_mainfile(
        self,
        filename: str,
        mime: str,
        buffer: bytes,
        decoded_buffer: str,
        compression: str | None = None,
    ) -> bool | Iterable[str]:
        is_mainfile = super().is_mainfile(
            filename=filename,
            mime=mime,
            buffer=buffer,
            decoded_buffer=decoded_buffer,
            compression=compression,
        )
        if is_mainfile:
            try:
                # try to resolve mainfile keys from parser
                mainfile_keys = self.mainfile_parser.get_mainfile_keys(
                    filename=filename, decoded_buffer=decoded_buffer
                )
                self.creates_children = True
                return mainfile_keys
            except Exception:
                return is_mainfile
        return is_mainfile


class ArchiveParser(MatchingParser):
    def __init__(self):
        super().__init__(
            name='parsers/archive',
            code_name=config.services.unavailable_value,
            domain=None,
            level=-1,
            mainfile_mime_re='.*',
            mainfile_name_re=r'.*(archive|metainfo)\.(json|yaml|yml)$',
        )

    def validate_definitions(self, archive, logger=None):
        if not archive or not archive.definitions:
            return
        errors, warnings = archive.definitions.m_all_validate()
        has_definition_errors = len(errors) > 0
        if logger:
            for error in errors:
                logger.error('Validation error', details=error)
            for warning in warnings:
                logger.warn('Validation warning', details=warning)

        if has_definition_errors:
            raise Exception('Archive contains definitions that have validation errors')

    def parse_file(self, mainfile, f, archive, logger=None):
        try:
            if mainfile.endswith('.json'):
                import json

                archive_data = json.load(f)
            else:
                import yaml

                archive_data = yaml.load(f, Loader=yaml.SafeLoader)
        except Exception as e:
            if logger:
                logger.error('Cannot parse archive json or yaml.', exc_info=e)
            raise e

        if metadata_data := archive_data.pop(EntryArchive.metadata.name, None):
            self.domain = metadata_data.get('domain')
            for quantity_name in ['entry_name', 'references', 'comment']:
                if value := metadata_data.get(quantity_name, None):
                    archive.metadata.m_set(quantity_name, value)

        archive.m_update_from_dict(archive_data, treat_none_as_nan=True)

    def parse(
        self, mainfile: str, archive: EntryArchive, logger=None, child_archives=None
    ):
        with open(mainfile) as f:
            self.parse_file(mainfile, f, archive, logger)

        self.validate_definitions(archive, logger)


class ElnMatchingParser(MatchingParser):
    """A parser implementation that matches mainfiles and creates an ELN section
    for the data file.
    This parser is used to create an ELN section for the data file, which can be
    used to annotate the data file with metadata and other information.
    """

    def __init__(
        self,
        eln_m_def: str | None = None,
        raw_file_m_def: str | None = None,
        update: bool = False,
        data_type: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.eln_m_def = eln_m_def
        self.raw_file_m_def = raw_file_m_def
        self.update = update
        self.data_type = data_type

    def eln_section(self, mainfile: str, archive: EntryArchive) -> dict:
        return dict(
            m_def=self.eln_m_def,
            data_file=archive.m_context.get_relative_path(mainfile),
        )

    def eln_name(self, mainfile: str, archive: EntryArchive) -> str:
        relative_path = archive.m_context.get_relative_path(mainfile)
        return f'{".".join(relative_path.split(".")[:-1])}.archive.json'

    def parse(
        self, mainfile: str, archive: EntryArchive, logger=None, child_archives=None
    ):
        context = archive.m_context
        eln_name = self.eln_name(mainfile, archive)

        archive.m_update_from_dict(
            dict(
                data=dict(
                    m_def=self.raw_file_m_def,
                    eln=f'{context.get_reference(eln_name)}#/data',
                    data_type=self.data_type,
                    file_ending=os.path.splitext(mainfile)[1],
                    file_size=os.path.getsize(mainfile),
                ),
            )
        )
        archive.metadata.entry_name = f'Data file {context.get_relative_path(mainfile)}'

        if not self.update and context.raw_path_exists(eln_name):
            return

        with context.update_entry(eln_name, write=True, process=True) as content:
            data_dict: dict = content.get('data', {})
            update_dict = self.eln_section(mainfile, archive)
            data_dict.update(update_dict)
            content['data'] = data_dict


class MissingParser(MatchingParser):
    """
    A parser implementation that just fails and is used to match mainfiles with known
    patterns of corruption.
    """

    name = 'parsers/missing'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def parse(
        self, mainfile: str, archive: EntryArchive, logger=None, child_archives=None
    ):
        raise Exception(f'The code {self.code_name} is not yet supported.')
