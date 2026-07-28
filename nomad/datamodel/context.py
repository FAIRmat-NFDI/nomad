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

import json
import os.path
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from ruamel import yaml

from nomad import utils
from nomad.config import config
from nomad.datamodel import EntryArchive
from nomad.datamodel.datamodel import EntryMetadata
from nomad.datamodel.util import parse_path
from nomad.metainfo import MetainfoReferenceError, MSection, Package, Quantity


def _opener_and_dumper(file_name: str) -> tuple:
    if not file_name.endswith(('json', 'yaml', 'yml')):
        raise AssertionError('Only json/yaml files can be updated.')

    if file_name.endswith('json'):
        loader = json.load
        dumper = json.dump
    else:
        loader = yaml.safe_load  # type: ignore
        dumper = yaml.dump  # type: ignore

    return loader, dumper


def populate_builtin_packages():
    from nomad.schemas import cache_builtin_packages

    cache_builtin_packages()


class Context:
    """
    The nomad implementation of a metainfo context.
    """

    def __init__(self, installation_url: str | None = None):
        # take installation_url and ensure it has no trailing slash
        if installation_url is None:
            self.installation_url = config.api_url(api='api/v1')
        elif installation_url.endswith('/'):
            self.installation_url = installation_url[:-1]
        else:
            self.installation_url = installation_url

        # now check if the installation_url points to 'api/v1'
        if not self.installation_url.endswith('/api/v1'):
            self.installation_url += (
                '/v1' if self.installation_url.endswith('/api') else '/api/v1'
            )

        self.archives: dict[str, MSection] = {}
        self.urls: dict[MSection, str] = {}

    @property
    def child_archives(self):
        return []

    @property
    def upload_id(self):
        return None

    def warning(self, event, **kwargs):
        """
        Used to log (or otherwise handle) warning that are issued, e.g. while serialization,
        reference resolution, etc.
        """
        pass

    @staticmethod
    def _get_ids(root: MSection, required: bool = True) -> tuple:
        upload_id, entry_id = None, None
        if isinstance(root, EntryArchive) and root.metadata:
            upload_id, entry_id = root.metadata.upload_id, root.metadata.entry_id
        if required:
            assert entry_id is not None, 'Only archives with entry_id can be referenced'
            assert upload_id is not None, (
                'Only archives with upload_id can be referenced'
            )
        return upload_id, entry_id

    @staticmethod
    def _normalize_fragment(fragment: str) -> str:
        if fragment and fragment != '' and not fragment.startswith('/'):
            fragment = f'/{fragment}'
        return fragment

    def create_reference(
        self, section: MSection, quantity_def: Quantity, value: MSection
    ) -> str:
        """
        Returns a reference for the given target section (value) based on the given context.
        Allows subclasses to build references across resources, if necessary.

        Arguments:
            section: The containing section.
            quantity_def: The definition of the quantity.
            value: The reference value.

        Raises: MetainfoReferenceError
        """
        fragment = value.m_path()
        target_root: MSection = value.m_root()

        # need to consider packages that are initialised via loading PART of the archive, or via mongo
        # in that case, `_get_ids` will fail as there is no attached archive
        # meanwhile, the reference shall be located under `definitions` in the original archive
        # we distinguish between the two cases by checking if the target_root is a package
        if isinstance(target_root, Package) and target_root.m_is_custom_package:
            return f'../uploads/{target_root.upload_id}/archive/{target_root.entry_id}#definitions/{fragment}'

        source_root: MSection = section.m_root()

        if source_root == target_root:
            return f'#{fragment}'

        if target_root in self.urls:
            return f'{self.urls[target_root]}#{fragment}'

        # when the package is loaded from mongo, there is no metadata
        # and since it is versioned, it is not ideal to create a reference based on processed data
        if getattr(target_root, 'metadata', None) is None:
            return None  # type: ignore

        upload_id, entry_id = self._get_ids(target_root, required=True)
        assert source_root.m_context == self, (
            'Can only create references from archives with this context'
        )
        installation_url = getattr(
            target_root.m_context, 'installation_url', self.installation_url
        )

        if installation_url != self.installation_url:
            return (
                f'{installation_url}/uploads/{upload_id}/archive/{entry_id}#{fragment}'
            )

        source_upload_id, source_entry_id = self._get_ids(source_root, required=False)

        if entry_id == source_entry_id:
            return f'#{fragment}'

        if upload_id == source_upload_id:
            return f'../upload/archive/{entry_id}#{fragment}'

        return f'../uploads/{upload_id}/archive/{entry_id}#{fragment}'

    def normalize_reference(self, source: MSection, url: str) -> str:
        """
        Rewrites the url into a normalized form. E.g., it replaces `..` with absolute paths,
        or replaces mainfiles with ids, etc.

        Arguments:
            source: The source section or root section of the source of the reference.
            url: The reference to normalize.

        Raises: MetainfoReferenceError
        """
        if source is None:
            return url

        url_parts = urlsplit(url)
        fragment = self._normalize_fragment(url_parts.fragment)
        path = url_parts.path

        if (upload_id := self.upload_id) is None:
            upload_id = getattr(
                getattr(source.m_root(), 'metadata', {}), 'upload_id', None
            )

        if match := re.search(r'/archive/mainfile/(.*)$', path):
            assert upload_id, 'Only archives with upload_id can be referenced'
            mainfile = match.group(1)
            return urlunsplit(
                (
                    url_parts.scheme,
                    url_parts.netloc,
                    path.replace(
                        f'/archive/mainfile/{mainfile}',
                        f'/archive/{utils.generate_entry_id(upload_id, mainfile)}',
                    ),
                    url_parts.query,
                    fragment,
                )
            )

        if '/upload/raw' in path:
            return urlunsplit(
                (
                    url_parts.scheme,
                    url_parts.netloc,
                    path.replace('/upload/raw', f'/upload/{upload_id}/raw')
                    if upload_id
                    else path,
                    url_parts.query,
                    fragment,
                )
            )

        return urlunsplit(url_parts[:4] + (fragment,))

    def load_archive(
        self, entry_id: str, upload_id: str, installation_url: str
    ) -> EntryArchive:
        """Loads the archive for the given identification."""
        raise NotImplementedError()

    def resolve_archive(self, *args, **kwargs):
        return self.resolve_archive_url(*args, **kwargs)

    def load_raw_file(
        self, path: str, upload_id: str, installation_url: str, url: str | None = None
    ) -> MSection:
        """Loads a raw file based on the given upload and path. Interpret as metainfo data."""
        raise NotImplementedError()

    def raw_path_exists(self, path: str) -> bool:
        """Use to check if a raw path already exists."""
        raise NotImplementedError()

    def raw_path(self) -> str:
        """The path to the uploads raw files directory."""
        return os.path.curdir

    def raw_file(self, path: str, *args, **kwargs):
        """Open a raw file for reading or writing."""
        raise NotImplementedError

    def process_updated_raw_file(self, path, allow_modify=False):
        """
        Use when parsing or normalizing a file causes another file to be added or modified.
        Call this method from the parse/normalize method for the first file, after adding/updating
        the other file is complete. The provided path should denote the added/modified file.
        If we have a ServerContext and the added/modified file matches a parser, we will
        trigger processing of this file (synchronously or asynchronously, depending on if
        we're processing locally or not). For non-ServerContexts, the method does nothing.

        Note, that *modifying* existing files is discouraged, as this needs to be done with
        care to avoid infinite loops of files modifying each other etc. We would thus recommend
        to only use this method for *adding* files. If you still want to modify existing
        files, you must set the `allow_modify` flag, otherwise the call will raise an exception
        if the entry already exists. Also note that this method should not be used to modify
        the same file (i.e. the file that you're currently parsing/normalizing), only when
        adding/modifying other files.
        """
        pass

    def _parse_url(self, url: str) -> tuple:
        url_results = parse_path(f'{url}#/placeholder', self.upload_id)
        if url_results is None:
            raise MetainfoReferenceError(
                f'the url {url} is not a valid metainfo reference'
            )

        (
            installation_url,
            upload_id,
            entry_id_or_mainfile,
            kind,
            _fragment,
        ) = url_results  # _fragment === /placeholder

        if installation_url is None:
            installation_url = self.installation_url

        return installation_url, upload_id, kind, entry_id_or_mainfile

    def load_url(self, url: str) -> MSection:
        installation_url, upload_id, kind, entry_id_or_mainfile = self._parse_url(url)

        if kind not in ('archive', 'raw'):
            raise MetainfoReferenceError(
                f'the url {url} is not a valid metainfo reference'
            )

        if kind == 'archive':
            return self.load_archive(entry_id_or_mainfile, upload_id, installation_url)

        return self.load_raw_file(
            entry_id_or_mainfile, upload_id, installation_url, url
        )

    def resolve_archive_url(self, url: str) -> MSection:
        if url not in self.archives:
            self.cache_archive(url, self.load_url(url))

        return self.archives[url]

    def cache_archive(self, url: str, archive):
        self.archives[url] = archive
        self.urls[archive] = url

    def get_reference(self, mainfile: str) -> str:
        """
        Get the reference for the given file name.
        """
        raise NotImplementedError

    def get_relative_path(self, mainfile: str) -> str:
        """
        Get the relative path for the given file name.
        """
        raise NotImplementedError

    def fetch_section(self, def_ref: str | None, def_id: str):
        """
        Fetch a section definition by its reference name and ID.
        """
        try:
            from nomad.schemas import get_schema

            return get_schema(def_ref, def_id)
        except Exception:  # noqa
            return None

    @contextmanager
    def update_entry(
        self,
        mainfile: str,
        *,
        write: bool = False,
        process: bool = False,
        **kwargs,
    ) -> Iterator[dict]:
        """
        Open the target file and send it to the updater function.
        The updater function shall return the updated file content.
        The updated file will be stored and processed if needed.

        WARNING:
            If `process=True`, the updated file will be processed immediately.
            Please be aware of the fact that this method may be called during the processing of
            the parent/main file.
            This means if there are any data dependencies, there is a risk of infinite loops,
            racing conditions and/or other unexpected behavior.
            You must carefully design the logic to mitigate these risks.

        To use this function, you shall use the with-statement as follows:

        ```python
        with context.update_entry('mainfile.json',**kwargs) as content:
            # do something with content
        ```

        Parameters:
            mainfile: The relative path (from upload root) to the file to update.
            write: Whether to write the updated file back to the storage.
                If False, no processing will be triggered whatsoever.
            process: Whether to trigger processing of the updated file.
        """
        raise NotImplementedError


class ServerContext(Context):
    def __init__(self, upload=None):
        super().__init__()
        self.upload = upload

    @property
    def upload_files(self):
        if self.upload:
            return self.upload.upload_files

    @property
    def upload_id(self):
        if self.upload:
            return self.upload.upload_id

    def _get_upload_files(self, upload_id: str, installation_url: str):
        if installation_url and self.installation_url != installation_url:
            raise NotImplementedError()

        if self.upload_files and self.upload_files.upload_id == upload_id:
            return self.upload_files

        # delayed import, context is part of datamodel which should be available in
        # base install, files however requires [infrastructure].
        # TODO move server context to some infrastructure package!
        from nomad import files

        upload_files = files.UploadFiles.get(upload_id)
        assert upload_files and upload_files.upload_id == upload_id
        return upload_files

    def load_archive(
        self, entry_id: str, upload_id: str, installation_url: str
    ) -> EntryArchive:
        upload_files = self._get_upload_files(upload_id, installation_url)

        try:
            with upload_files.read_archive(entry_id) as reader:
                from nomad.archive import to_json

                archive_dict = to_json(reader[entry_id])
        except KeyError:
            if upload_id != self.upload_id:
                raise MetainfoReferenceError(
                    f'Referencing another Upload is not allowed.'
                )
            from nomad.processing import Entry

            if entry := Entry.objects(entry_id=entry_id).first():  # type: ignore
                return self.load_raw_file(entry.mainfile, upload_id, installation_url)
            raise MetainfoReferenceError(f'Could not load {entry_id}.')

        context = self
        if upload_id != self.upload_id:
            from nomad.processing import Upload

            context = ServerContext(Upload(upload_id=upload_id))

        return EntryArchive.m_from_dict(archive_dict, m_context=context)

    def load_raw_file(
        self, path: str, upload_id: str, installation_url: str, url: str | None = None
    ) -> EntryArchive:
        upload_files = self._get_upload_files(upload_id, installation_url)

        try:
            # Make sure the archive has proper entry_id, even though we are just
            # loading a raw file. This is important to serialize references into this
            # archive!
            archive = EntryArchive(
                m_context=self,
                metadata=EntryMetadata(
                    upload_id=upload_id,
                    mainfile=path,
                    entry_id=utils.generate_entry_id(upload_id, path),
                ),
            )
            from nomad.parsing.parser import ArchiveParser

            parser = ArchiveParser()
            with upload_files.raw_file(path, 'rt') as f:
                parser.parse_file(path, f, archive)
            if url:
                self.cache_archive(url, archive)
            parser.validate_definitions(archive)
            return archive
        except Exception:
            raise MetainfoReferenceError(f'Could not load {path}.')

    def raw_path(self):
        return self.upload_files._raw_dir.os_path

    def raw_file(self, *args, **kwargs):
        return self.upload_files.raw_file(*args, **kwargs)

    def raw_path_exists(self, path) -> bool:
        return self.upload_files.raw_path_exists(path)

    def process_updated_raw_file(self, path, allow_modify=False):
        self.upload.process_updated_raw_file(path, allow_modify)

    @contextmanager
    def update_entry(
        self,
        mainfile: str,
        *,
        write: bool = False,
        process: bool = False,
        **kwargs,
    ):
        if not self.upload:
            raise AssertionError(
                'A valid upload must be attached before updating files.'
            )

        loader, dumper = _opener_and_dumper(mainfile)

        content: dict = {}
        if self.upload_files.raw_path_exists(mainfile):
            with self.upload_files.raw_file(mainfile, 'r') as f:
                content = loader(f)

        yield content

        if not write:
            return

        with self.upload_files.raw_file(mainfile, 'w') as f:
            dumper(content, f, indent=2)

        if process:
            self.upload.process_updated_raw_file(mainfile, True)

    def get_reference(self, mainfile: str) -> str:
        from nomad.utils import hash

        entry_id = hash(self.upload_id, mainfile)
        return f'../uploads/{self.upload_id}/archive/{entry_id}'

    def get_relative_path(self, mainfile):
        return mainfile.split('/raw/', 1)[1]


class ServerLocalContext(Context):
    def __init__(self, mainfile_dir):
        super().__init__()
        self._mainfile_dir: Path = Path(mainfile_dir)

    def raw_file(self, path, *args, **kwargs):
        return (self._mainfile_dir / path).open(*args, **kwargs)

    def raw_path_exists(self, path: str) -> bool:
        return (self._mainfile_dir / path).exists()


def _validate_url(url):
    return config.api_url(api='api/v1') if url is None else url


class ClientContext(Context):
    """
    Since it is a client side context, use config.client.url by default.
    Otherwise, if invoked from ArchiveQuery, use the url provided by user.

    Arguments:
        - installation_url: The installation_url that should be used for intra installation
            references.
        - local_dir: For intra "upload" references, files will be looked up here.
    """

    def __init__(
        self,
        installation_url: str | None = None,
        *,
        local_dir: str | None = None,
        upload_id: str | None = None,
        username: str | None = None,
        password: str | None = None,
        recursive_kwargs: dict | None = None,
        auth=None,
    ):
        super().__init__(
            config.client.url + '/v1' if installation_url is None else installation_url
        )
        self._installation_url = installation_url
        self.local_dir = local_dir
        if auth:
            self._auth = auth
        else:
            from nomad.client import Auth

            self._auth = Auth(user=username, password=password)
        self._upload_id = upload_id
        self._recursive_kwargs = recursive_kwargs if recursive_kwargs else {}
        self._child_archives: list = []

    @property
    def child_archives(self):
        holder = self._child_archives
        self._child_archives = []
        return holder

    @property
    def upload_id(self):
        return self._upload_id

    def load_archive(
        self, entry_id: str, upload_id: str, installation_url: str
    ) -> EntryArchive:
        # TODO currently upload_id might be None
        if upload_id is None:
            url = f'{_validate_url(installation_url)}/entries/{entry_id}/archive'
        else:
            url = f'{_validate_url(installation_url)}/uploads/{upload_id}/archive/{entry_id}'

        response = requests.get(url, auth=self._auth)

        if response.status_code != 200:
            raise MetainfoReferenceError(
                f'cannot retrieve archive {entry_id} from {installation_url}'
            )

        context = self
        if upload_id != self.upload_id:
            context = ClientContext(
                installation_url=self._installation_url,
                local_dir=self.local_dir,
                upload_id=upload_id,
                auth=self._auth,
            )

        return EntryArchive.m_from_dict(
            response.json()['data']['archive'], m_context=context
        )

    def load_raw_file(
        self, path: str, upload_id: str, installation_url: str, url: str | None = None
    ) -> MSection:
        # TODO currently upload_id might be None
        if upload_id is None:
            # try to find a local file, useful when the context is used for local parsing
            file_path = os.path.join(self.local_dir, path) if self.local_dir else path
            if os.path.exists(file_path):
                from nomad.parsing.parser import ArchiveParser

                with open(file_path) as f:
                    archive = EntryArchive(m_context=self)
                    ArchiveParser().parse_file(file_path, f, archive)
                    return archive

            raise MetainfoReferenceError(f'cannot retrieve raw file without upload id')

        entry_id = utils.generate_entry_id(upload_id, path)
        return self.load_archive(entry_id, upload_id, installation_url)

    def raw_file(self, path, *args, **kwargs):
        file_path = os.path.join(self.local_dir, path)
        return open(file_path, *args, **kwargs)

    def raw_path_exists(self, path: str) -> bool:
        file_path = os.path.join(self.local_dir, path)
        return os.path.exists(file_path)

    def create_reference(
        self, section: MSection, quantity_def: Quantity, value: MSection
    ) -> str:
        try:
            return super().create_reference(section, quantity_def, value)
        except AssertionError:
            return f'<unavailable url>/#{value.m_path()}'

    @contextmanager
    def update_entry(
        self,
        mainfile: str,
        *,
        write: bool = False,
        process: bool = False,
        **kwargs,
    ):
        if not self.local_dir:
            raise AssertionError(
                'A valid local folder must be attached before updating files.'
            )

        loader, dumper = _opener_and_dumper(mainfile)

        file_path: Path = Path(self.local_dir) / mainfile

        content: dict = {}
        if file_path.exists():
            with file_path.open() as f:
                content = loader(f)

        yield content

        if not write:
            return

        with file_path.open('w') as f:
            dumper(content, f, indent=2)

        if process:
            from nomad.client import parse

            self._child_archives.extend(
                parse(
                    file_path.absolute().as_posix(), **(self._recursive_kwargs | kwargs)
                )
            )

    def get_reference(self, mainfile: str) -> str:
        # TODO: use self.local_dir
        return mainfile.rsplit('/', maxsplit=1)[-1]

    def get_relative_path(self, mainfile):
        # TODO: use self.local_dir
        return mainfile.split('/')[-1]
