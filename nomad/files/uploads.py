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
Contains classes and functions to create and maintain file structures
for uploads, and some generic file utilities.

There are two different structures for uploads in two different states: *staging* and *public*.
Possible operations on uploads differ based on this state. Staging is used for
processing, heavily editing, creating hashes, etc. Public is supposed to be a
almost readonly (beside metadata) storage.

.. code-block:: sh

    fs/staging/<upload>/raw/**
                       /archive/<entry_id>.msg
    fs/public/<upload>/raw-{access}.plain.zip
                      /archive-{access}.msg.msg

Where `access` is either "public" (non-embargoed) or "restricted" (embargoed).

There is an implicit relationship between files, based on them being in the same
directory. Each directory with at least one *mainfile* is an *entry directory*
and all the files are *aux* files to that mainfile. This is independent of whether the
respective files actually contributes data or not. An entry directory might
contain multiple mainfiles. E.g., user simulated multiple states of the same system, have
one entry based on the other, etc. In this case the other mainfile is an *aux file* to the
original mainfile, and vice versa.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import stat
import tarfile
import warnings
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from functools import cached_property
from typing import IO, Any, Literal, NamedTuple, cast

import magic
import yaml
from fsspec import AbstractFileSystem
from fsspec.implementations.local import LocalFileSystem
from h5py import File
from msglc.reader import LazyItem, LazyReader
from upath import UPath

from nomad import datamodel, utils
from nomad.archive import (
    ArchiveReader,
    combine_archive,
    read_archive,
    to_json,
    write_archive,
)
from nomad.common import (
    get_compression_format,
    has_glob_wildcards,
    is_safe_relative_path,
)
from nomad.config import config
from nomad.config.models.config import BundleExportSettings, BundleImportSettings

from .archive_toc import ArchiveTocIndex, build_archive_toc_index
from .filesystem import (
    DirectoryObject,
    FSUtility,
    PathObject,
    _apply_if_match,
    _is_stale_object_error,
    _remote_read_open_kwargs,
    _versioned_archive_file_object,
    _versioned_archive_file_objects,
    bundle_info_filename,
)
from .index_cache import CachedIndex, IndexCache
from .public_storage import (
    Access,
    choose_pack_fs,
    choose_read_fs_and_marker,
    clear_ready_cache,
    complete_published_write,
    delete_access_artifacts,
    delete_ready_marker,
    detect_published_access,
    load_cached_marker,
    rename_published_artifacts,
)
from .sources import BrowsableFileSource, DiskFileSource, FileSource, _disk_file_source
from .zip_index import RangeTailFile, ZipMemberIndex, open_zip_member

logger = logging.getLogger('nomad.files')


@dataclass(slots=True)
class _RawEntry:
    """Internal lightweight listing entry.

    Stores only the information needed for sorting, paging, and response
    construction. Directory sizes are intentionally not tracked.
    """

    path: str
    is_file: bool
    size: int | None = None


class RawPathInfo(NamedTuple):
    """
    Stores basic info about a file or folder located at a specific raw path.
    """

    path: str
    is_file: bool
    size: int
    access: str


class RawDirPage(NamedTuple):
    """
    A paginated slice of raw directory metadata.
    """

    content: list[RawPathInfo]
    total: int


class RawPathReader:
    """Request-scoped access to a single raw path.

    The base implementation is intentionally thin so staging uploads retain their
    existing filesystem behaviour. ``PublicUploadFiles`` supplies the ZIP-aware
    implementation below.
    """

    def __init__(self, upload_files: UploadFiles, path: str):
        self.upload_files = upload_files
        self.path = path

    def exists(self) -> bool:
        return self.upload_files.raw_exists(self.path)

    def isfile(self) -> bool:
        return self.upload_files.raw_isfile(self.path)

    def mime_type(self) -> str:
        return self.upload_files.raw_file_mime_type(self.path)

    @contextmanager
    def open(self, *args, **kwargs):
        with self.upload_files.raw_file(self.path, *args, **kwargs) as file:
            yield file

    def close(self):
        """Release request-scoped resources, if any."""


class UploadFiles(DirectoryObject):
    """Abstract base class for upload files."""

    def __init__(
        self,
        upload_id: str,
        create: bool = False,
        *,
        fs: AbstractFileSystem | None = None,
    ):
        self.logger = utils.get_logger('nomad.files', upload_id=upload_id)

        super().__init__(os_path=self.base_folder_for(upload_id), create=create, fs=fs)

        if not create and not self.exists():
            raise KeyError(upload_id)

        self.upload_id = upload_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @classmethod
    def _file_area(cls) -> UPath:
        """
        Full path to where the upload files of this class are stored (i.e. either
        staging or public file area).
        """
        raise NotImplementedError()

    @property
    def external_os_path(self):
        """
        Full path to where the upload files of this class are stored on the server.
        This is equal to `self.os_path` if no external path substitutes for staging
        and public area are configured. This is helpful, when nomad is run in a container
        and the mounted path used by nomad are different from the actual paths on the
        host server.
        """
        raise NotImplementedError()

    @classmethod
    def base_folder_for(cls, upload_id: str) -> UPath:
        """
        Full path to the base folder for the upload files (of this class) for the
        specified upload_id.
        """
        return cls._file_area() / upload_id[: config.fs.prefix_size] / upload_id

    @classmethod
    def exists_for(cls, upload_id: str) -> bool:
        """
        If an UploadFiles object (of this class) has been created for this upload_id.
        """
        return cls.base_folder_for(upload_id).exists()

    @staticmethod
    def get(upload_id: str) -> UploadFiles:
        for class_type in (PublicUploadFiles, StagingUploadFiles):
            if class_type.exists_for(upload_id):
                return class_type(upload_id)

        return None  # type: ignore

    def to_staging(
        self, create: bool = False, include_archive: bool = False
    ) -> StagingUploadFiles | None:
        """Casts to or creates corresponding staging upload files or returns None."""
        raise NotImplementedError()

    def is_empty(self) -> bool:
        """If this upload has no content yet."""
        raise NotImplementedError()

    def raw_exists(self, path: str) -> bool:
        """
        Returns True if the specified path is a valid raw path (either file or directory)
        """
        raise NotImplementedError()

    def raw_isfile(self, path: str) -> bool:
        """
        Returns True if the specified path points to a file (rather than a directory).
        """
        raise NotImplementedError()

    def raw_listdir(
        self,
        path: str = '',
        recursive: bool = False,
        files_only: bool = False,
        depth: int = -1,
    ) -> Iterable[RawPathInfo]:
        """
        Returns an iterable of RawPathInfo, one for each element (file or folder) in
        the directory specified by `path`. If `recursive` is set to True, subdirectories are
        also crawled. If `files_only` is set, only the file objects found are returned.
        If path is not a valid directory, the result will be empty. Selecting empty string
        as path (which is the default value) gives the content of the whole raw directory.
        The `path_prefix` argument can be used to filter out elements where the path starts
        with a specific prefix.

        The `depth` argument can be used to limit the depth of the recursion.
        """
        raise NotImplementedError()

    def raw_listdir_page(
        self,
        path: str = '',
        *,
        start: int = 0,
        end: int | None = None,
        recursive: bool = False,
        files_only: bool = False,
        depth: int = -1,
        order: Literal['asc', 'desc'] = 'asc',
        group_directories_first: bool = False,
    ) -> RawDirPage:
        """
        Returns a paginated slice of raw directory metadata.

        Subclasses can override this to avoid materializing full metadata for items outside
        the requested page.
        """
        items = list(self.raw_listdir(path, recursive, files_only, depth))

        if group_directories_first:
            folders = [item for item in items if not item.is_file]
            files = [item for item in items if item.is_file]
            ordered = folders + files
            if order != 'asc':
                ordered = list(reversed(files)) + list(reversed(folders))
        else:
            ordered = items if order == 'asc' else list(reversed(items))

        if end is None:
            end = len(ordered)

        return RawDirPage(content=ordered[start:end], total=len(items))

    def raw_path_exists(self, path: str) -> bool:
        warnings.warn(
            'raw_path_exists() is deprecated; use raw_exists() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.raw_exists(path)

    def raw_path_is_file(self, path: str) -> bool:
        warnings.warn(
            'raw_path_is_file() is deprecated; use raw_isfile() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.raw_isfile(path)

    def raw_directory_list(
        self,
        path: str = '',
        recursive=False,
        files_only=False,
        depth: int = -1,
    ) -> Iterable[RawPathInfo]:
        warnings.warn(
            'raw_directory_list() is deprecated; use raw_listdir() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.raw_listdir(path, recursive, files_only, depth)

    @contextmanager
    def raw_file(self, file_path: str, *args, **kwargs):
        """
        Opens a raw file and returns a file-like object. Additional args, kwargs are
        delegated to the respective `open` call.
        Arguments:
            file_path: The path to the file relative to the upload.
        Raises:
            KeyError: If the file does not exist.
        """
        raise NotImplementedError()

    def raw_file_size(self, file_path: str) -> int:
        """
        Returns:
            The size of the given raw file.
        """
        raise NotImplementedError()

    def raw_file_mime_type(self, file_path: str) -> str:
        assert self.raw_isfile(file_path), (
            'Provided path does not specify a file, or is invalid.'
        )
        with self.raw_file(file_path, 'br') as raw_file:
            return (
                magic.from_buffer(raw_file.read(2048), mime=True)
                or 'application/octet-stream'
            )

    def raw_path_reader(self, path: str) -> RawPathReader:
        """Return a short-lived reader for inspecting and opening one raw path.

        The default reader delegates to the existing raw-file methods. Published
        uploads override this with a reader that reuses the request-scoped ZIP
        member index for the whole download request.
        """
        return RawPathReader(self, path)

    @contextmanager
    def read_archive(self, entry_id: str) -> Iterator[ArchiveReader]:
        """
        Returns an :class:`nomad.archive.ArchiveReader` that contains the
        given entry_id.
        """
        raise NotImplementedError()

    def close(self):
        """Release possibly held system resources (e.g. file handles)."""
        pass

    def delete(self) -> None:
        super().delete()
        if config.fs.prefix_size > 0 and not self._fs.ls(
            parent := os.path.dirname(self.os_path), False
        ):
            self._fs.rm(parent, recursive=True)

    def files_to_bundle(
        self, export_settings: BundleExportSettings
    ) -> Iterable[FileSource]:
        """
        A generator of :class:`FileSource` objects, defining the files/folders to be included in an
        upload bundle when *exporting*. The arguments allows for further filtering of what to include.

        Note, this only yields files to copy from the regular upload directory, not "special" files,
        like the bundle_info.json file, which is created by the :class:`BundleExporter`.
        """
        raise NotImplementedError()

    @classmethod
    def files_from_bundle(
        cls,
        bundle_file_source: BrowsableFileSource,
        import_settings: BundleImportSettings,
    ) -> Iterable[FileSource]:
        """
        Returns an Iterable of :class:`FileSource`, defining the files/folders to be included in an
        upload bundle when *importing*. Only the files specified by the import_settings are included.
        """
        raise NotImplementedError()

    def archive_hdf5_location(self, entry_id: str) -> str:
        """
        Returns the OS path to the target HDF5 file.
        The str will be passed to h5py module for reading and writing.
        We do not provide a raw IO object here, since later this file may be a web resource.
        """
        raise NotImplementedError()


class StagingUploadFiles(UploadFiles):
    def __init__(self, upload_id: str, create: bool = False):
        super().__init__(upload_id, create)

        self._raw_dir = self.join_dir('raw', create)
        self._archive_dir = self.join_dir('archive', create)

    @classmethod
    def _file_area(cls):
        return UPath(config.fs.staging)

    @property
    def _frozen_file(self):
        return self.join_file('.frozen')

    @property
    def external_os_path(self):
        if not config.fs.staging_external:
            return self.os_path

        return self.os_path.replace(config.fs.staging, config.fs.staging_external)

    def to_staging(
        self, create: bool = False, include_archive: bool = False
    ) -> StagingUploadFiles | None:
        return self

    @property
    def size(self) -> int:
        return self._fs.du(self._raw_dir.os_path)

    def _full_path(self, path: str):
        return UPath(self._raw_dir.os_path) / path

    def is_empty(self) -> bool:
        return not self._fs.ls(self._raw_dir.os_path, False)

    def raw_exists(self, path: str) -> bool:
        return is_safe_relative_path(path) and self._fs.exists(self._full_path(path))

    def raw_isfile(self, path: str) -> bool:
        return is_safe_relative_path(path) and self._fs.isfile(self._full_path(path))

    def raw_create_directory(self, path: str):
        assert path and is_safe_relative_path(path), 'Bad path provided'
        self._fs.makedirs(self._full_path(path).as_posix(), True)

    def raw_listdir(
        self,
        path: str = '',
        recursive: bool = False,
        files_only: bool = False,
        depth: int = -1,
    ) -> Iterable[RawPathInfo]:
        if not is_safe_relative_path(path) or depth == 0:
            return

        fs = self._fs
        for target in fs.find(
            os.path.join(self._raw_dir.os_path, path),
            (depth if depth > 0 else None) if recursive else 1,
            not files_only,
        ):
            relpath = UPath(os.path.relpath(target, self._raw_dir.os_path))
            if not (isfile := fs.isfile(target)) and relpath == UPath(path):
                # skip folder itself
                continue
            yield RawPathInfo(
                path=relpath.as_posix(),
                is_file=isfile,
                size=fs.size(target) if isfile else fs.du(target),
                access='unpublished',
            )

    def raw_listdir_page(
        self,
        path: str = '',
        *,
        start: int = 0,
        end: int | None = None,
        recursive: bool = False,
        files_only: bool = False,
        depth: int = -1,
        order: Literal['asc', 'desc'] = 'asc',
        group_directories_first: bool = False,
    ) -> RawDirPage:
        """Return a paginated raw directory listing.

        This implementation is optimized for low metadata overhead:

        - performs a single initial probe to determine whether ``path`` is a file
            or directory
        - collects names and file sizes during the walk where cheaply available
        - sorts and pages entirely in memory after collection
        """
        if not is_safe_relative_path(path) or depth == 0:
            return RawDirPage(content=[], total=0)

        os_path = self._full_path(path).as_posix()
        normalised_path = path.rstrip('/')

        path_kind, path_size = self._probe_path_kind_and_size(os_path)
        if path_kind is None:
            return RawDirPage(content=[], total=0)

        entries: list[_RawEntry] = []

        if path_kind == 'file':
            entries.append(
                _RawEntry(
                    path=normalised_path,
                    is_file=True,
                    size=path_size,
                )
            )
        else:
            self._collect_raw_entries(
                os_path,
                normalised_path,
                entries,
                recursive=recursive,
                files_only=files_only,
                depth=depth,
            )

        # Phase 2 onwards is filesystem-agnostic: sort, slice, and materialize.
        entries = self._sort_raw_entries(
            entries,
            order=order,
            group_directories_first=group_directories_first,
        )

        total = len(entries)

        if end is None or end > total:
            end = total
        start = max(start, 0)
        start = min(start, end)

        page = entries[start:end]

        content = [
            RawPathInfo(
                path=entry.path,
                is_file=entry.is_file,
                size=(entry.size or 0) if entry.is_file else 0,
                access='unpublished',
            )
            for entry in page
        ]

        return RawDirPage(content=content, total=total)

    def _probe_path_kind_and_size(
        self,
        os_path: str,
    ) -> tuple[Literal['file', 'dir'] | None, int | None]:
        """Probe a path once to determine existence, type, and file size.

        Returns:
            ('file', size):
                for a regular file
            ('dir', None):
                for a directory
            (None, None):
                if the path does not exist, is inaccessible, or is not a supported
                file/directory entry

        This avoids the extra metadata round trip of calling ``exists()`` and then
        ``isfile()`` separately.
        """
        if isinstance(self._fs, LocalFileSystem):
            try:
                st = os.stat(os_path, follow_symlinks=False)
            except (FileNotFoundError, PermissionError, OSError):
                return None, None

            mode = st.st_mode
            if stat.S_ISREG(mode):
                return 'file', st.st_size
            if stat.S_ISDIR(mode):
                return 'dir', None

            # Skip symlinks, devices, sockets, etc.
            return None, None

        try:
            info = self._fs.info(os_path)
        except Exception:
            return None, None

        entry_type = info.get('type')
        if entry_type == 'file':
            size = info.get('size')
            try:
                size = int(size) if size is not None else 0
            except (TypeError, ValueError):
                size = 0
            return 'file', size

        if entry_type in {'directory', 'dir'}:
            return 'dir', None

        return None, None

    def _collect_raw_entries(
        self,
        os_path: str,
        relative_path: str,
        entries: list[_RawEntry],
        *,
        recursive: bool,
        files_only: bool,
        depth: int,
    ) -> None:
        """Dispatch to the appropriate walk implementation based on filesystem type."""
        if isinstance(self._fs, LocalFileSystem):
            self._collect_local_raw_entries(
                os_path,
                relative_path,
                entries,
                recursive=recursive,
                files_only=files_only,
                depth=depth,
            )
        else:
            self._collect_generic_raw_entries(
                os_path,
                relative_path,
                entries,
                recursive=recursive,
                files_only=files_only,
                depth=depth,
            )

    @staticmethod
    def _collect_local_raw_entries(
        os_path: str,
        relative_path: str,
        entries: list[_RawEntry],
        *,
        recursive: bool,
        files_only: bool,
        depth: int,
    ) -> None:
        """Collect listing entries from a local filesystem using ``os.scandir``.

        This keeps the local path fast by relying on ``DirEntry`` methods, which
        usually avoid extra stat syscalls compared with naive ``os.listdir`` +
        ``os.stat`` loops.

        File sizes are captured during traversal when cheaply available.
        Directory sizes are never computed.
        """
        remaining_depth = (
            depth if recursive and depth > 0 else (None if recursive else 1)
        )

        def _walk(
            current_os_path: str,
            current_relative_path: str,
            current_depth: int | None,
        ) -> None:
            try:
                with os.scandir(current_os_path) as it:
                    for child in it:
                        child_relative_path = (
                            child.name
                            if not current_relative_path
                            else f'{current_relative_path}/{child.name}'
                        )

                        try:
                            if child.is_file(follow_symlinks=False):
                                try:
                                    size = child.stat(follow_symlinks=False).st_size
                                except (OSError, ValueError):
                                    size = None

                                entries.append(
                                    _RawEntry(
                                        path=child_relative_path,
                                        is_file=True,
                                        size=size,
                                    )
                                )
                                continue

                            if not child.is_dir(follow_symlinks=False):
                                # Skip symlinks, broken entries, devices, etc.
                                continue

                        except OSError:
                            continue

                        if not files_only:
                            entries.append(
                                _RawEntry(
                                    path=child_relative_path,
                                    is_file=False,
                                    size=None,
                                )
                            )

                        if current_depth is None or current_depth > 1:
                            _walk(
                                child.path,
                                child_relative_path,
                                None if current_depth is None else current_depth - 1,
                            )

            except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
                return

        _walk(os_path, relative_path, remaining_depth)

    def _collect_generic_raw_entries(
        self,
        os_path: str,
        relative_path: str,
        entries: list[_RawEntry],
        *,
        recursive: bool,
        files_only: bool,
        depth: int,
    ) -> None:
        """Collect listing entries from a generic fsspec filesystem.

        Uses ``ls(detail=True)`` so the backend can provide file/directory type and,
        where available, file size in the same listing response.

        This is intended to work for non-local backends such as NFS-mounted
        implementations exposed via fsspec, S3-like stores, GCS, and similar
        filesystems.

        Directory sizes are never computed.
        """
        remaining_depth = (
            depth if recursive and depth > 0 else (None if recursive else 1)
        )

        def _walk(
            current_os_path: str,
            current_relative_path: str,
            current_depth: int | None,
        ) -> None:
            try:
                # detail=True is required to distinguish files from directories.
                batch = self._fs.ls(current_os_path, detail=True)
            except Exception:
                return

            for item in batch:
                item_path = item.get('name')
                if not item_path:
                    continue

                item_name = item_path.rstrip('/').rsplit('/', 1)[-1]
                child_relative_path = (
                    item_name
                    if not current_relative_path
                    else f'{current_relative_path}/{item_name}'
                )

                item_type = item.get('type')

                if item_type == 'file':
                    size = item.get('size')
                    try:
                        size = int(size) if size is not None else None
                    except (TypeError, ValueError):
                        size = None

                    entries.append(
                        _RawEntry(
                            path=child_relative_path,
                            is_file=True,
                            size=size,
                        )
                    )
                    continue

                if item_type not in {'directory', 'dir'}:
                    continue

                if not files_only:
                    entries.append(
                        _RawEntry(
                            path=child_relative_path,
                            is_file=False,
                            size=None,
                        )
                    )

                if current_depth is None or current_depth > 1:
                    _walk(
                        item_path,
                        child_relative_path,
                        None if current_depth is None else current_depth - 1,
                    )

        _walk(os_path, relative_path, remaining_depth)

    @staticmethod
    def _sort_raw_entries(
        entries: list[_RawEntry],
        *,
        order: Literal['asc', 'desc'],
        group_directories_first: bool,
    ):
        """Sort entries according to requested ordering.

        When ``group_directories_first`` is enabled, directories and files are
        sorted separately and concatenated. Otherwise entries are sorted only by
        path.
        """
        reverse = order == 'desc'

        if not group_directories_first:
            return sorted(entries, key=lambda e: e.path, reverse=reverse)

        dirs = [e for e in entries if not e.is_file]
        files = [e for e in entries if e.is_file]

        dirs.sort(key=lambda e: e.path, reverse=reverse)
        files.sort(key=lambda e: e.path, reverse=reverse)

        return dirs + files

    @contextmanager
    def raw_file(self, file_path: str, *args, **kwargs):
        assert is_safe_relative_path(file_path)

        full_path = self.raw_file_object(file_path).os_path

        try:
            with self._fs.open(full_path, *args, **kwargs) as f:
                yield f
        except (FileNotFoundError, IsADirectoryError) as e:
            raise KeyError(full_path) from e

    def raw_file_size(self, file_path: str) -> int:
        assert is_safe_relative_path(file_path)
        return self._fs.size(self.raw_file_object(file_path).os_path)

    def raw_file_object(self, file_path: str) -> PathObject:
        assert is_safe_relative_path(file_path)
        return self._raw_dir.join_file(file_path)

    def archive_hdf5_location(self, entry_id: str) -> str:
        return self.join_dir('archive').join_file(f'{entry_id}.h5').os_path

    def write_archive(self, entry_id: str, data: Any) -> int:
        """Writes the data as archive file and returns the archive file size."""
        archive_file_object = self._archive_file_object(entry_id)
        try:
            write_archive(archive_file_object.os_path, {entry_id: data})
        except Exception:
            # in case of failure, remove the possible corrupted archive file
            archive_file_object.delete()

            raise

        return archive_file_object.size

    @contextmanager
    def read_archive(self, entry_id: str) -> Iterator[ArchiveReader]:
        try:
            with read_archive(
                self._archive_file_object(entry_id, True).os_path
            ) as archive:
                yield archive
        except FileNotFoundError as e:
            raise KeyError(entry_id) from e

    def _archive_file_object(self, entry_id: str, fallback: bool = False) -> PathObject:
        def versioned_file_name(version_suffix):
            return f'{entry_id}{version_suffix}.msg'

        return _versioned_archive_file_object(
            self._archive_dir, versioned_file_name, fallback=fallback
        )

    def add_rawfiles(
        self,
        target_path: str | PathObject,
        target_dir: str = '',
        cleanup_source_file_and_dir: bool = False,
        updated_files: set[str] | None = None,
        auto_decompress: bool = True,
    ) -> None:
        """Adds files or directories to the upload, optionally decompressing archives.

        If `path` refers to an archive (ZIP, TAR) and `auto_decompress` is True,
        the archive is extracted before merging. Otherwise, archives are treated as single files.

        Args:
            target_path (str): Path to the file or directory to add.
            target_dir (str, optional): Relative path within the upload's raw directory.
                Defaults to "".
            cleanup_source_file_and_dir (bool, optional): If True, deletes the source path
                and its parent directory after processing. Defaults to False.
            updated_files (set[str], optional): Set to track paths of files updated or added.
            auto_decompress (bool, optional): If True, automatically decompress archives.
                Defaults to True.

        Raises:
            AssertionError: If file format is unrecognized or merge conflicts occur.
        """
        assert not self.is_frozen
        if isinstance(target_path, str):
            assert self._fs.exists(target_path), f'{target_path} does not exist'
            path = target_path
            target_fs = self._fs
            location = target_path
        else:
            assert target_path.exists(), f'{target_path} does not exist'
            path = target_path.os_path
            target_fs = target_path._fs
            location = target_path.location
        assert is_safe_relative_path(target_dir)

        archive_format = (
            get_compression_format(path, fs=target_fs) if auto_decompress else None
        )
        if archive_format == 'error':
            raise ValueError('Bad archive.')

        dst_root = os.path.join(self._raw_dir.os_path, target_dir)

        try:
            if archive_format == 'tar':
                with target_fs.open(location, 'rb') as f:
                    with tarfile.open(fileobj=f, mode='r|*') as tar:
                        for member in tar:
                            rel_path = member.name
                            if not is_safe_relative_path(rel_path):
                                continue
                            dst_path = os.path.join(dst_root, rel_path)
                            if member.isfile():
                                if self._fs.exists(dst_path) and not self._fs.isfile(
                                    dst_path
                                ):
                                    raise ValueError(
                                        f'Cannot merge a file with a directory or vice versa: {rel_path}.'
                                    )
                                self._fs.mkdirs(
                                    os.path.dirname(dst_path), exist_ok=True
                                )
                                with (
                                    tar.extractfile(member) as src_f,
                                    self._fs.open(dst_path, 'wb') as dst_f,
                                ):
                                    shutil.copyfileobj(src_f, dst_f)

                                if updated_files is not None:
                                    updated_files.add(
                                        os.path.join(target_dir, rel_path)
                                    )
                            elif member.isdir():
                                if self._fs.exists(dst_path) and not self._fs.isdir(
                                    dst_path
                                ):
                                    raise ValueError(
                                        f'Cannot merge a file with a directory or vice versa: {rel_path}.'
                                    )
                                self._fs.mkdirs(dst_path, True)
            else:

                @contextmanager
                def open_archive() -> Iterator[tuple[str, str, AbstractFileSystem]]:
                    if archive_format == 'zip':
                        with FSUtility.open_archive(path, fs=target_fs) as _fs:
                            yield '', '', _fs
                    else:
                        yield (
                            path,
                            os.path.dirname(path) if self._fs.isfile(path) else path,
                            self._fs,
                        )

                with open_archive() as pack:
                    src_root, src_parent, src_fs = pack
                    for item, info in src_fs.find(src_root, None, True, True).items():
                        rel_path = os.path.relpath(item, src_parent)
                        dst_path = os.path.join(dst_root, rel_path)
                        if info['type'] == 'file':
                            if self._fs.exists(dst_path) and not self._fs.isfile(
                                dst_path
                            ):
                                raise ValueError(
                                    f'Cannot merge a file with a directory or vice versa: {rel_path}.'
                                )
                            if src_fs is not self._fs or item != dst_path:
                                self._fs.mkdirs(
                                    os.path.dirname(dst_path), exist_ok=True
                                )
                                with (
                                    src_fs.open(item) as src_f,
                                    self._fs.open(dst_path, 'wb') as dst_f,
                                ):
                                    shutil.copyfileobj(src_f, dst_f)

                            if updated_files is not None:
                                updated_files.add(os.path.join(target_dir, rel_path))
                        elif info['type'] == 'directory':
                            if self._fs.exists(dst_path) and not self._fs.isdir(
                                dst_path
                            ):
                                raise ValueError(
                                    f'Cannot merge a file with a directory or vice versa: {rel_path}.'
                                )
                            self._fs.mkdirs(dst_path, True)
        finally:
            if cleanup_source_file_and_dir:
                self._fs.rm(path, recursive=True)
                if self._fs.exists(parent := os.path.dirname(path)) and not self._fs.ls(
                    parent, False
                ):
                    self._fs.rm(parent, recursive=True)

    def delete_rawfiles(self, path, updated_files: set[str] | None = None):
        assert is_safe_relative_path(path)
        raw_os_path = UPath(self.os_path) / 'raw'
        os_path = raw_os_path / path
        if not self._fs.exists(os_path):
            return
        if updated_files is not None:
            updated_files.update(
                os.path.relpath(target, raw_os_path.as_posix())
                for target in self._fs.find(os_path)
            )
        self._fs.rm(os_path, recursive=True)
        if raw_os_path == os_path:
            # Special case - deleting everything, i.e. the entire raw folder. Need to recreate.
            self._fs.makedirs(os_path)

    def copy_or_move(
        self,
        src: str,
        dest: str,
        copy_or_move: Literal['copy', 'move'],
        updated_files: set[str] | None = None,
    ):
        """
        Copies or moves a raw file or folder from `src` to `dest`, both given as
        paths relative to the `raw` folder. Fails if `dest` already exists, or if
        `src` and `dest` contain glob wildcard characters ('*', '?', '[' or ']').
        Does nothing if `src` does not exist.

        If `updated_files` is provided, it is populated with the paths (relative
        to the `raw` folder) of all files affected by the operation: for a move,
        both the source and destination paths of every file that was moved; for a
        rename of a single file, both the old and new path (even when unchanged,
        the destination is added).
        """
        assert is_safe_relative_path(src)
        assert is_safe_relative_path(dest)
        if has_glob_wildcards(src) or has_glob_wildcards(dest):
            # `self._fs.cp`/`self._fs.mv` (fsspec) interpret '*', '?' and '[...]'
            # in paths as glob patterns rather than literal characters, which
            # would silently copy/move the wrong files or blow up with a
            # RecursionError.
            raise ValueError(
                'File and folder names must not contain the wildcard characters '
                "'*', '?', '[' or ']'."
            )
        src_full_path = os.path.join(self._raw_dir.os_path, src)
        dest_full_path = os.path.join(self._raw_dir.os_path, dest)
        src_is_folder = self._fs.isdir(src_full_path)
        mode = copy_or_move.lower()
        if not self._fs.exists(src_full_path):
            return
        if dest == src or dest.startswith(f'{src}/'):
            # Prevent moving/copying a folder into itself or one of its subfolders
            raise ValueError(
                f"Cannot {mode} '{src}' into itself or one of its own subfolders "
                f"('{dest}')."
            )
        if self._fs.exists(dest_full_path):
            raise ValueError(
                f'A {"folder" if src_is_folder else "file"} with the same name already exists.'
            )

        if src_is_folder and updated_files is not None and mode == 'move':
            # Recursively add the paths of all files currently inside the source folder to `updated_files` BEFORE the folder is moved.
            updated_files.update(
                x.path for x in self.raw_listdir(src, recursive=True, files_only=True)
            )
        if mode == 'move':
            self._fs.mv(src_full_path, dest_full_path, recursive=src_is_folder)
        elif mode == 'copy':
            self._fs.cp(src_full_path, dest_full_path, recursive=src_is_folder)
        else:
            raise ValueError('Invalid operation. Must be "copy" or "move".')
        if updated_files is not None:
            if src_is_folder:
                # Recursively add the paths of all files currently inside the destination folder to `updated_files` AFTER the folder is moved.
                updated_files.update(
                    x.path
                    for x in self.raw_listdir(dest, recursive=True, files_only=True)
                )
            else:
                updated_files.add(dest)
                # if both the new and old name are the same then no new entry will be
                # added to the set. but if different, we add the old one so that later on
                # when self.matchall is called in data.py, the old filename is removed
                # from mongo database
                if mode == 'move':
                    updated_files.add(src)

    def metadata_file_cached(self, path_dir: str = ''):
        """
        Gets the content of the metadata file located in the directory defined by `path_dir`.
        The `path_dir` should be relative to the `raw` folder.
        """

        def json_load(_f):
            return json.load(f)

        def yaml_load(_f):
            return yaml.safe_load(_f)

        def dummy_load(_):
            return {}

        loader = {'json': json_load, 'yaml': yaml_load, 'yml': yaml_load}

        base = UPath(self._raw_dir.os_path) / path_dir
        for ext in config.process.metadata_file_extensions:
            if not self._fs.isfile(
                full_path := base / f'{config.process.metadata_file_name}.{ext}'
            ):
                continue
            try:
                with self._fs.open(full_path.as_posix()) as f:
                    return loader.get(ext, dummy_load)(f)
            except Exception as e:
                # ignore the file contents if the file is not parsable, just warn.
                self.logger.warn(
                    'could not parse nomad.yaml/json', path=path_dir, exc_info=e
                )
        return {}

    @property
    def is_frozen(self) -> bool:
        """Returns True if this upload is already *bagged*."""
        return self._frozen_file.exists()

    def pack(
        self,
        entries: list[datamodel.EntryMetadata],
        with_embargo: bool,
        create: bool = True,
        include_raw: bool = True,
        include_archive: bool = True,
    ) -> None:
        """
        Packs raw and/or archive files, to create the contents in the public file area.
        This method should be called when an upload is published, or when a
        published upload has been reprocessed.

        If the public upload files directory does not exist, it will be created.
        If the target archive file or raw file zip exists, they will be overwritten.
        If an archive file or raw file zip with the wrong access exists, they will be deleted.
        This is potentially a long running operation.

        Arguments:
            entries: A list of EntryMetadata to pack in the archive files
            with_embargo: If the upload is embargoed (determines which "access" is used in
                the file names)
            create: if the public upload files directory should be created. True by default.
            include_raw: determines if the raw data should be packed. True by default.
            include_archive: determines of the archive data should be packed. True by default.
        """
        self.logger.info('started to pack upload')

        # freeze the upload
        assert not self.is_frozen, 'Cannot pack an upload that is packed, or packing.'
        with self._fs.open(self._frozen_file.os_path, 'w') as f:
            f.write('frozen')

        # Check embargo flag consistency
        for entry in entries:
            assert entry.with_embargo == with_embargo

        access: Access = 'restricted' if with_embargo else 'public'
        other_access: Access = 'public' if with_embargo else 'restricted'

        # Get or create a target dir in the public area
        target_dir = DirectoryObject(
            PublicUploadFiles.base_folder_for(self.upload_id), create=create
        )
        if os.listdir(target_dir.os_path):
            # Target dir contains files. Check that the target access is identical
            assert PublicUploadFiles(self.upload_id).access == access, (
                'Inconsistent access'
            )

        pack_fs = choose_pack_fs()
        delete_ready_marker(target_dir.os_path)

        # zip archives
        if include_archive:
            with utils.timer(self.logger, 'packed msgpack archive') as log_data:
                log_data.update(
                    number_of_entries=self._pack_archive_files(
                        target_dir,
                        list(entry.entry_id for entry in entries),
                        access,
                        pack_fs,
                    )
                )

        # zip raw files
        if include_raw:
            with utils.timer(self.logger, 'packed raw files'):
                self._pack_raw_files(target_dir, access, pack_fs)

        delete_access_artifacts(
            target_dir.os_path,
            other_access,
            include_raw=include_raw,
            include_archive=include_archive,
        )
        complete_published_write(target_dir.os_path, self.upload_id, access)

    def _pack_archive_files(
        self,
        target_dir: DirectoryObject,
        entries: list[str],
        access: str,
        fs: AbstractFileSystem,
    ):
        def create_iterator():
            for item in entries:
                fo = self._archive_file_object(item)
                yield item, fo if fo.exists() else None

        try:
            combine_archive(target_dir.msg_fp(access, fs=fs), create_iterator(), fs=fs)

            write_h5 = any(
                [
                    self.join_dir('archive').join_file(f'{entry_id}.h5').exists()
                    for entry_id in entries
                ]
            )
            if write_h5:
                with FSUtility.open_h5(
                    target_dir.h5_fp(access, fs=fs).os_path, 'w', fs=fs
                ) as hdf5_target:
                    for entry_id in entries:
                        with File(
                            self.archive_hdf5_location(entry_id), 'a'
                        ) as hdf5_source:
                            group = hdf5_target.create_group(entry_id)
                            for key in hdf5_source.keys():
                                hdf5_source.copy(key, group)
        except Exception as e:
            self.logger.error('exception during packing archives', exc_info=e)
            raise

        return len(entries)

    def _pack_raw_files(
        self, target_dir: DirectoryObject, access: str, fs: AbstractFileSystem
    ):
        try:
            with FSUtility.open_archive(
                target_dir.zip_fp(access, fs=fs).os_path, 'w', fs=fs
            ) as zip_fs:
                for path_info in self.raw_listdir(recursive=True):
                    basename = os.path.basename(path_info.path)
                    # TODO remove extra handling of POTCAR files once processed uploads are published.
                    if basename.startswith('POTCAR'):
                        if not basename.endswith('.stripped'):
                            continue  # Skip the unstripped POTCAR files when publishing
                        if basename.endswith('.stripped.stripped'):
                            continue  # Skip redundantly stripped POTCAR files (created due to bug #979) when publishing
                    zip_fs.put_file(
                        self._raw_dir.join_file(path_info.path).os_path, path_info.path
                    )
        except Exception as e:
            self.logger.error('exception during packing raw files', exc_info=e)
            raise

    def entry_files(
        self, mainfile: str, with_mainfile: bool = True, with_cutoff: bool = True
    ) -> Iterable[str]:
        """
        Returns all the auxfiles and mainfile for a given mainfile. This implements
        nomad's logic about what is part of an entry and what not. The mainfile
        is the first element, the rest is sorted.
        Arguments:
            mainfile: The mainfile path relative to upload
            with_mainfile: Do include the mainfile, default is True
        """
        mainfile_object = self._raw_dir.join_file(mainfile)
        if not mainfile_object.exists():
            raise KeyError(mainfile)

        mainfile_basename = os.path.basename(mainfile)
        entry_dir = os.path.dirname(mainfile_object.os_path)
        entry_relative_dir = entry_dir[len(self._raw_dir.os_path) + 1 :]

        file_count = 0
        aux_files: list[str] = []
        dir_elements = os.listdir(entry_dir)
        dir_elements.sort()
        for dir_element in dir_elements:
            if dir_element != mainfile_basename and os.path.isfile(
                os.path.join(entry_dir, dir_element)
            ):
                aux_files.append(os.path.join(entry_relative_dir, dir_element))
                file_count += 1

            if with_cutoff and file_count > config.process.auxfile_cutoff:
                # If there are too many of them, its probably just a directory with lots of
                # mainfiles/entries. In this case it does not make any sense to provide thousands of
                # aux files.
                break

        aux_files = sorted(aux_files)

        if with_mainfile:
            return [mainfile] + aux_files
        else:
            return aux_files

    def entry_hash(self, mainfile: str, mainfile_key: str) -> str:
        """
        Calculates a hash for the given entry based on file contents and aux file contents.
        Arguments:
            mainfile: The mainfile path relative to the upload that identifies the entry in
                the folder structure.
            mainfile_key: The mainfile_key of the entry (if any)
        Returns:
            The calculated hash
        Raises:
            KeyError: If the mainfile does not exist.
        """
        hash = hashlib.sha512()
        for filepath in self.entry_files(mainfile):
            with self._fs.open(self._raw_dir.join_file(filepath).os_path) as f:
                for data in iter(lambda: f.read(65536), b''):
                    hash.update(data)
        if mainfile_key:
            hash.update(mainfile_key.encode('utf8'))
        return utils.make_websave(hash)

    def files_to_bundle(
        self, export_settings: BundleExportSettings
    ) -> Iterable[FileSource]:
        # Defines files for upload bundles of staging uploads.
        if export_settings.include_raw_files:
            yield DiskFileSource(self.os_path, 'raw')
        if export_settings.include_archive_files:
            yield DiskFileSource(self.os_path, 'archive')

    @classmethod
    def files_from_bundle(
        cls,
        bundle_file_source: BrowsableFileSource,
        import_settings: BundleImportSettings,
    ) -> Iterable[FileSource]:
        # Files to import for a staging upload
        if import_settings.include_raw_files:
            yield bundle_file_source.child('raw')
        if import_settings.include_archive_files:
            yield bundle_file_source.child('archive')
        if import_settings.include_bundle_info:
            yield bundle_file_source.child(bundle_info_filename)


class ZipRawPathReader(RawPathReader):
    """Published raw paths share ``PublicUploadFiles`` request-scoped ZIP state.

    Exists/isfile/open/mime_type delegate to ``PublicUploadFiles``. ``close()``
    is a no-op so download streams can close the reader after
    ``PublicUploadFiles.close()`` without double-closing the ZIP.
    """

    upload_files: PublicUploadFiles


# One range GET covers header, sub-TOC and data for entries up to this size;
# larger entries fall back to the filesystem's block size.
_MAX_ENTRY_BLOCK = 32 * 1024 * 1024


def _archive_toc_sizeof(index: ArchiveTocIndex) -> int:
    return len(index.entries) * 250 + 1024


def _zip_index_sizeof(index: ZipMemberIndex) -> int:
    """Include index contents plus an allowance for cache bookkeeping."""
    return index.memory_size + 1024


_toc_cache: IndexCache[ArchiveTocIndex] = IndexCache(
    'toc',
    decode=ArchiveTocIndex.from_bytes,
    encode=ArchiveTocIndex.to_bytes,
    sizeof=_archive_toc_sizeof,
)
_zip_cache: IndexCache[ZipMemberIndex] = IndexCache(
    'zip',
    decode=ZipMemberIndex.from_bytes,
    encode=ZipMemberIndex.to_bytes,
    sizeof=_zip_index_sizeof,
)


def clear_index_caches() -> None:
    """Drop process-level artifact-probe, ZIP, and archive TOC caches. Intended for tests."""
    clear_ready_cache()
    _toc_cache.clear()
    _zip_cache.clear()


class _SingleEntryArchive(Mapping[str, LazyReader]):
    """Mapping view exposing exactly one combined-archive entry."""

    def __init__(self, entry_id: str, entry: LazyReader):
        self._entry_id = entry_id
        self._entry = entry

    def __getitem__(self, key: str) -> LazyReader:
        if key != self._entry_id:
            raise KeyError(key)
        return self._entry

    def __iter__(self):
        yield self._entry_id

    def __len__(self) -> int:
        return 1


@dataclass
class _EntryAtOffset:
    stack: ExitStack
    entry: LazyReader

    def __enter__(self) -> _EntryAtOffset:
        self.stack.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.stack.__exit__(exc_type, exc_val, exc_tb)


class PublicUploadFiles(UploadFiles):
    def __init__(
        self,
        upload_id: str,
        create: bool = False,
        *,
        fs: AbstractFileSystem | None = None,
    ):
        super().__init__(upload_id, create, fs=fs)
        self._zip: CachedIndex[ZipMemberIndex] | None = None
        self._toc: CachedIndex[ArchiveTocIndex] | None = None

    @classmethod
    def _file_area(cls):
        return UPath(config.fs.public)

    @property
    def external_os_path(self):
        if not config.fs.public_external:
            return self.os_path

        return self.os_path.replace(config.fs.public, config.fs.public_external)

    @cached_property
    def storage_fs(self) -> AbstractFileSystem:
        """The single backend from which this published upload is read."""
        return self._read_selection[0]

    @cached_property
    def _read_selection(self) -> tuple[AbstractFileSystem, Access | None]:
        """Keep the ready marker's access only for the chosen remote backend."""
        storage_fs, marker = choose_read_fs_and_marker(self.os_path)
        return storage_fs, None if marker is None else marker.access

    @cached_property
    def _remote_marker_access(self) -> Access | None:
        """Load the direct-remote marker lazily when access is requested."""
        storage_fs, marker_access = self._read_selection
        if marker_access is not None:
            return marker_access
        if (
            config.fs.public_fs.protocol
            and config.fs.public_fs.read_mode != 'remote_then_local'
        ):
            marker = load_cached_marker(self.os_path, storage_fs)
            return None if marker is None else marker.access
        return None

    @cached_property
    def access(self) -> Access:
        """
        Which "access" is used, either 'public' (uploads without embargo) or 'restricted'
        (uploads with embargo). This is reflected in the names of the files holding the
        raw data and the archive data. The reason for this is so that it should be easy to
        see, by just looking at the files, if a published upload is embargoed or not.

        The access is determined by inspecting which files exist/contain data. If both
        public and restricted files exist/contain data, or if neither exists/contain data,
        a KeyError will be thrown (this should not happen if the upload is correctly packed).
        The read filesystem is tried first; if it has no artifacts, the write
        destination is inspected so a dual-write import can complete before the
        copy to remote. The inspection of the files is only done on the first
        call, and the cached result is used in subsequent calls. The only way to
        change the access is to call :func:`re_pack`.
        """
        marker_access = self._remote_marker_access
        if marker_access is not None:
            return marker_access
        return detect_published_access(self.os_path, self.storage_fs, choose_pack_fs())

    def raw_zip_file_object(self, access: str = None) -> PathObject:
        """
        Gets the raw zip file, either public or restricted, depending on which one is used.
        If both public and restricted files exist, or if none of them exist, a KeyError will
        be thrown.
        """
        return self.zip_fp(access or self.access, fs=self.storage_fs)

    def msg_fp(
        self,
        access: str,
        fallback: bool = False,
        *,
        fs: AbstractFileSystem | None = None,
    ):
        return super().msg_fp(access, fallback=fallback, fs=fs or self.storage_fs)

    def h5_fp(self, access: str, *, fs: AbstractFileSystem | None = None):
        return super().h5_fp(access, fs=fs or self.storage_fs)

    @contextmanager
    def _zip_fs(self, mode: Literal['a', 'w', 'r'] = 'r'):
        if mode != 'r':
            self.close()
        with FSUtility.open_archive(
            self.raw_zip_file_object().os_path, mode, fs=self.storage_fs
        ) as zip_fs:
            yield zip_fs

    def _zip_read_hints(self) -> tuple[int | None, str | None]:
        identity = None if self._zip is None else self._zip.identity
        if identity is None:
            return None, None
        return identity.size, identity.etag

    def _open_raw_zip_fileobj(self) -> IO[bytes]:
        zip_obj = self.raw_zip_file_object()
        fs = self.storage_fs
        location = zip_obj.location
        if isinstance(fs, LocalFileSystem):
            return fs.open(location, 'rb')
        size, _etag = self._zip_read_hints()
        return cast(IO[bytes], RangeTailFile.from_filesystem(fs, location, size=size))

    def _open_zip_member_fileobj(self) -> IO[bytes]:
        """Open the ZIP object for a member read without prefetching the tail."""
        zip_obj = self.raw_zip_file_object()
        fs = self.storage_fs
        size, etag = self._zip_read_hints()
        file_obj = fs.open(
            zip_obj.location, 'rb', **_remote_read_open_kwargs(fs, size=size)
        )
        _apply_if_match(fs, file_obj, etag, size=size)
        return file_obj

    def _parse_raw_zip_index(self) -> ZipMemberIndex:
        fileobj = self._open_raw_zip_fileobj()
        try:
            zip_file = zipfile.ZipFile(fileobj, 'r')
        except Exception:
            with suppress(Exception):
                fileobj.close()
            raise
        try:
            return ZipMemberIndex.from_zipfile(zip_file)
        finally:
            with suppress(Exception):
                zip_file.close()
            if not getattr(fileobj, 'closed', False):
                with suppress(Exception):
                    fileobj.close()

    def _ensure_zip_index(self) -> ZipMemberIndex:
        if self._zip is not None:
            return self._zip.index
        zip_obj = self.raw_zip_file_object()

        def uncached(index: ZipMemberIndex) -> ZipMemberIndex:
            self._zip = CachedIndex(None, index, zip_obj.os_path)
            return index

        if not _zip_cache.is_enabled():
            if zip_obj.exists():
                return uncached(self._parse_raw_zip_index())
            return uncached(ZipMemberIndex.empty())
        cached = _zip_cache.get_or_build(
            self.storage_fs,
            zip_obj.location,
            zip_obj.os_path,
            build=self._parse_raw_zip_index,
        )
        if cached is None:
            return uncached(ZipMemberIndex.empty())
        self._zip = cached
        return cached.index

    def _discard_zip_index_cache(self) -> None:
        if self._zip is not None:
            _zip_cache.discard(self._zip)
            self._zip = None

    def _build_archive_toc(self, msg_file: PathObject) -> ArchiveTocIndex | None:
        try:
            with FSUtility.open(msg_file.os_path, fs=self.storage_fs) as file_obj:
                return build_archive_toc_index(file_obj)
        except ValueError:
            logger.warning(
                'failed to build archive TOC for %s',
                msg_file.location,
                exc_info=True,
            )
            return None

    def _archive_msg_candidates(self) -> list[PathObject]:
        directory = DirectoryObject(self.os_path, fs=self.storage_fs)
        return _versioned_archive_file_objects(
            directory,
            lambda suffix: f'archive-{self.access}{suffix}.msg.msg',
            fs=self.storage_fs,
        )

    def _ensure_archive_toc(self) -> ArchiveTocIndex | None:
        if self._toc is not None:
            return self._toc.index
        if not _toc_cache.is_enabled():
            return None
        # Only the preferred suffix may be served from memory. An older suffix
        # that was cached while the preferred file was absent must not hide a
        # newer archive that has since appeared.
        candidates = self._archive_msg_candidates()
        if candidates:
            preferred = candidates[0]
            cached = _toc_cache.get(self.storage_fs, preferred.location)
            if cached is not None:
                self._toc = cached
                return cached.index
        msg_file = self.msg_fp(self.access, fallback=True)
        self._toc = _toc_cache.get_or_build(
            self.storage_fs,
            msg_file.location,
            msg_file.os_path,
            build=lambda: self._build_archive_toc(msg_file),
        )
        return None if self._toc is None else self._toc.index

    def _discard_archive_toc_cache(self) -> None:
        if self._toc is not None:
            _toc_cache.discard(self._toc)
            self._toc = None

    def _open_entry_at_offset(
        self, toc: ArchiveTocIndex, entry_id: str
    ) -> _EntryAtOffset | None:
        """Open one combined-archive entry at its cached offset; None means fall back."""
        start, end = toc.span(entry_id)
        cached = self._toc
        identity = None if cached is None else cached.identity
        msg_os_path = (
            cached.os_path
            if cached is not None
            else self.msg_fp(self.access, fallback=True).os_path
        )
        object_size = None if identity is None else identity.size
        etag = None if identity is None else identity.etag
        stack = ExitStack()
        try:
            file_obj = stack.enter_context(
                FSUtility.open(
                    msg_os_path,
                    fs=self.storage_fs,
                    block_size=min(end - start, _MAX_ENTRY_BLOCK),
                    size=object_size,
                    if_match=etag,
                )
            )
            file_obj.seek(start)
            entry = LazyReader(file_obj, from_combined=True)
        except (ValueError, OSError):
            stack.close()
            logger.warning(
                'failed to open cached archive entry %s at offset %d',
                entry_id,
                start,
                exc_info=True,
            )
            self._discard_archive_toc_cache()
            return None
        return _EntryAtOffset(stack.pop_all(), entry)

    def close(self):
        self._zip = None
        self._toc = None

    def raw_path_reader(self, path: str) -> RawPathReader:
        return ZipRawPathReader(self, path)

    def archive_hdf5_location(self, entry_id: str) -> str:
        fp = self.h5_fp(self.access)
        if not fp.exists():
            raise FileNotFoundError()

        return fp.os_path

    @contextmanager
    def _open_msg_file(self) -> Iterator[ArchiveReader]:
        msg_file = self.msg_fp(self.access, fallback=True)
        # ``read_archive`` accepts a seekable file object, allowing the selected
        # local backend to remain local even when remote public storage is enabled.
        with FSUtility.open(msg_file.os_path, fs=self.storage_fs) as file_obj:
            with read_archive(file_obj) as archive:
                yield archive

    def to_staging(
        self, create: bool = False, include_archive: bool = False
    ) -> StagingUploadFiles | None:
        if StagingUploadFiles.exists_for(self.upload_id):
            if create:
                raise FileExistsError('Staging upload does already exist')
            return StagingUploadFiles(self.upload_id)

        if not create:
            return None

        staging_upload_files = StagingUploadFiles(self.upload_id, create=True)
        if (raw_zip_file := self.raw_zip_file_object()).exists():
            staging_upload_files.add_rawfiles(raw_zip_file)

        if include_archive:
            with suppress(FileNotFoundError):
                with self._open_msg_file() as archive:
                    for entry_id, data in archive.items():
                        target = data if isinstance(data, LazyItem) else to_json(data)
                        staging_upload_files.write_archive(entry_id.strip(), target)

                with FSUtility.open_h5(
                    self.archive_hdf5_location(''), fs=self.storage_fs
                ) as hdf5_source:
                    for entry_id, data in hdf5_source.items():
                        with File(
                            staging_upload_files.archive_hdf5_location(entry_id), 'w'
                        ) as hdf5_target:
                            for key in data.keys():
                                data.copy(key, hdf5_target)

        return staging_upload_files

    def is_empty(self) -> bool:
        return self._ensure_zip_index().is_empty()

    def delete(self) -> None:
        """Delete every configured copy of this published upload.

        Published files can be read from a remote filesystem with a local fallback.
        Deletion must therefore target both locations independently; in particular, a
        remote error must not leave a readable local fallback behind.
        """
        self.close()
        errors: list[Exception] = []
        try:
            delete_ready_marker(self.os_path)
        except Exception as exc:
            errors.append(exc)

        def delete_directory(fs: AbstractFileSystem, cleanup_prefix: bool) -> None:
            PathObject(self.os_path, fs=fs).delete()

            # Keep the local prefix cleanup performed by DirectoryObject.delete, but
            # make it safe when a retry finds that the upload/prefix is already gone.
            if cleanup_prefix and config.fs.prefix_size > 0:
                parent = os.path.dirname(self.os_path)
                if fs.exists(parent) and not fs.ls(parent, detail=False):
                    fs.rm(parent, recursive=True)

        public_fs = config.fs.public_fs
        if public_fs.protocol is not None:
            try:
                delete_directory(public_fs.target_fs, cleanup_prefix=False)
            except Exception as exc:
                errors.append(exc)

        try:
            delete_directory(self._fs, cleanup_prefix=True)
        except Exception as exc:
            errors.append(exc)

        if len(errors) == 1:
            raise errors[0]
        if errors:
            # Python 3.10 is supported, so retain both failures through exception
            # chaining instead of using the Python 3.11 ExceptionGroup type.
            try:
                raise errors[1]
            except Exception as local_error:
                raise errors[0] from local_error

    def raw_exists(self, path: str) -> bool:
        if not is_safe_relative_path(path):
            return False
        return self._ensure_zip_index().exists(path)

    def raw_isfile(self, path: str) -> bool:
        if not is_safe_relative_path(path):
            return False
        return self._ensure_zip_index().isfile(path)

    def raw_listdir(
        self,
        path: str = '',
        recursive: bool = False,
        files_only: bool = False,
        depth: int = -1,
    ) -> Iterable[RawPathInfo]:
        if not is_safe_relative_path(path) or depth == 0:
            return

        index = self._ensure_zip_index()
        for member in index.listdir(
            path, recursive=recursive, files_only=files_only, depth=depth
        ):
            yield RawPathInfo(
                path=member.path,
                is_file=not member.is_dir,
                size=index.size(member.path),
                access=self.access,
            )

    @contextmanager
    def raw_file(self, file_path: str, *args, **kwargs):
        assert is_safe_relative_path(file_path)
        mode = kwargs.pop('mode', None)
        if len(args) > 0:
            mode = args[0]
        mode = mode or 'rb'
        encoding = kwargs.pop('encoding', None)

        member_file = None
        for retry in (True, False):
            index = self._ensure_zip_index()
            member = index.get(file_path)
            if member is None or member.is_dir or not member.zip_name:
                raise KeyError(file_path)

            fileobj = None
            try:
                fileobj = self._open_zip_member_fileobj()
                member_file = open_zip_member(fileobj, member)
                break
            except Exception as e:
                if fileobj is not None and not getattr(fileobj, 'closed', False):
                    with suppress(Exception):
                        fileobj.close()
                if _is_stale_object_error(e):
                    self._discard_zip_index_cache()
                    if retry:
                        continue
                    raise
                if isinstance(e, (FileNotFoundError, IsADirectoryError, KeyError)):
                    raise KeyError(file_path) from e
                if isinstance(e, zipfile.BadZipFile):
                    if retry:
                        self._discard_zip_index_cache()
                        continue
                    raise KeyError(file_path) from e
                raise
        try:
            if 't' in mode:
                yield io.TextIOWrapper(member_file, encoding=encoding)
            else:
                yield member_file
        finally:
            if not member_file.closed:
                member_file.close()

    def raw_file_size(self, file_path: str) -> int:
        assert is_safe_relative_path(file_path)

        index = self._ensure_zip_index()
        if not index.isfile(file_path):
            raise KeyError(file_path)
        if file_size := index.size(file_path):
            return file_size
        raise KeyError(file_path)

    @contextmanager
    def _open_full_archive(self, entry_id: str) -> Iterator[ArchiveReader]:
        try:
            with self._open_msg_file() as archive:
                if entry_id not in archive:
                    raise KeyError(entry_id)
                yield archive
        except FileNotFoundError as e:
            raise KeyError(entry_id) from e

    @contextmanager
    def read_archive(self, entry_id: str) -> Iterator[ArchiveReader]:
        toc = self._ensure_archive_toc()
        if toc is not None and toc.has_offsets():
            if entry_id not in toc:
                raise KeyError(entry_id)
            if (opened := self._open_entry_at_offset(toc, entry_id)) is not None:
                with opened:
                    yield cast(
                        ArchiveReader, _SingleEntryArchive(entry_id, opened.entry)
                    )
                return
        with self._open_full_archive(entry_id) as archive:
            yield archive

    def re_pack(self, with_embargo: bool) -> None:
        """
        Repacks the files when changing the embargo flag on the upload. That is: when lifting the
        embargo the file names of the raw zip file and the archive file change from containing
        the keyword "restricted" to "public". Adding embargo to a non-embargoed published
        upload is also supported, but only admins should be allowed to do this. The existing
        files are just renamed, so this should be a rather quick operation. The upload must
        be correctly packed (i.e. there cannot be non-empty public and restricted files
        at the same time).
        """
        if (self.access == 'restricted') == with_embargo:
            return

        self.close()

        old_access = self.access
        new_access: Access = 'restricted' if with_embargo else 'public'
        delete_ready_marker(self.os_path)
        rename_published_artifacts(self.os_path, old_access, new_access)
        complete_published_write(self.os_path, self.upload_id, new_access)

        self.__dict__.pop('access', None)
        self.__dict__.pop('storage_fs', None)
        self.__dict__.pop('_read_selection', None)
        self.__dict__.pop('_remote_marker_access', None)

    def files_to_bundle(
        self, export_settings: BundleExportSettings
    ) -> Iterable[FileSource]:
        if export_settings.include_raw_files:
            yield _disk_file_source(self.raw_zip_file_object())

        if export_settings.include_archive_files:
            for artifact in (self.msg_fp(self.access), self.h5_fp(self.access)):
                if artifact.exists():
                    yield _disk_file_source(artifact)

    @classmethod
    def files_from_bundle(
        cls,
        bundle_file_source: BrowsableFileSource,
        import_settings: BundleImportSettings,
    ) -> Iterable[FileSource]:
        for filename in bundle_file_source.find(''):
            if filename.startswith('raw-') and import_settings.include_raw_files:
                yield bundle_file_source.child(filename)
            if (
                filename.startswith('archive-')
                and import_settings.include_archive_files
            ):
                yield bundle_file_source.child(filename)
            if filename == bundle_info_filename and import_settings.include_bundle_info:
                yield bundle_file_source.child(filename)
