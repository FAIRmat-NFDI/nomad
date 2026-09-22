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

"""Streaming and browsable file-source implementations."""

from __future__ import annotations

import io
import json
import os
import shutil
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from typing import IO, Any

import zipstream
from fsspec import AbstractFileSystem
from fsspec.implementations.local import LocalFileSystem
from fsspec.implementations.zip import ZipFileSystem
from pathvalidate import sanitize_filepath
from pydantic import BaseModel
from upath import UPath

from nomad.common import is_safe_relative_path
from nomad.config import config

from .filesystem import FSUtility, PathObject


class StreamedFile(BaseModel):
    """
    Convenience class for representing a streamed file, together with information about
    file size and an associated path.
    """

    src: Any = None
    path: str
    size: int


def _is_published_artifact_filename(file_path: str) -> bool:
    name = os.path.basename(file_path)
    return (name.startswith('archive-') and name.endswith(('.msg', '.h5'))) or (
        name.startswith('raw-') and name.endswith('.zip')
    )


class FileSource(ABC):
    """
    An abstract class which represents a generic "file source", from which some number of files
    can be retrieved. There are several different ways to create a file source, see subclasses.
    The files in the source are associated with paths and have known sizes.
    """

    def __init__(self, fs: AbstractFileSystem | None = None):
        self._fs = fs or LocalFileSystem()

    @abstractmethod
    def to_streamed_files(self) -> Iterable[StreamedFile]:
        """
        Retrieves the files in the source as :class:`StreamedFile` objects.
        The caller should close the streams when consumed.
        """
        ...

    def to_zipfile(self, path, overwrite: bool = False):
        """
        Generates a zip file from the files in this FileSource and stores it to disk. The
        zipfile content is created by calling :func:`to_zipstream`.
        """
        assert not self._fs.isdir(path), (
            'Exporting to zip file requires a file path, not directory.'
        )
        assert overwrite or not self._fs.exists(path), (
            '`path` already exists. Use `overwrite` to overwrite.'
        )
        with self._fs.open(path, 'wb') as f:
            for chunk in create_zipstream(self.to_streamed_files()):
                f.write(chunk)

    def to_disk(
        self, destination_dir: str, move_files: bool = False, overwrite: bool = False
    ):
        """
        Writes the files from this FileSource to disk, uncompressed. The default implementation
        makes use of :func:`to_streamed_files`. The `destination_dir` should be a directory
        (it will be created if it does not exist). The `move_files` argument instructs
        the method to move the source files if possible.
        """
        dest_path = UPath(destination_dir)
        self._fs.mkdirs(dest_path, exist_ok=True)

        pack_directly_to_remote = (
            config.fs.public_fs.resolved_write_mode == 'remote_only'
            and not FSUtility.is_local(dest_path.as_posix())
        )

        for streamed_file in self.to_streamed_files():
            full_path = dest_path / streamed_file.path
            if pack_directly_to_remote and _is_published_artifact_filename(
                streamed_file.path
            ):
                remote_path = FSUtility.upath(full_path)
                if remote_path.exists():
                    assert overwrite, 'Target already exists and `overwrite` is False'
                with remote_path.open('wb') as output, streamed_file.src as src:
                    while chunk := src.read(config.archive.copy_chunk_size):
                        output.write(chunk)
                continue

            if full_path.exists():
                assert overwrite, 'Target already exists and `overwrite` is False'
            self._fs.mkdirs(full_path.parent, exist_ok=True)
            with (
                self._fs.open(full_path.as_posix(), 'wb') as output_file,
                streamed_file.src,
            ):
                shutil.copyfileobj(streamed_file.src, output_file)

    def close(self):
        """Perform "closing" of the source, if applicable."""
        pass


class BrowsableFileSource(FileSource, ABC):
    """
    A :class:`FileSource` which can be "browsed", like a folder on disk or a zip archive.
    """

    @abstractmethod
    def open(self, path, mode='rb') -> IO:
        """Opens a file by the specified path."""
        ...

    @abstractmethod
    def find(self, path: str) -> list[str]:
        """
        Returns a list of directory contents, located in the directory denoted by `path`
        in this file source.
        """
        ...

    @abstractmethod
    def child(self, path: str) -> BrowsableFileSource:
        """
        Creates a new instance of :class:`BrowsableFileSource` which just contains the
        files located under the specified path.
        """
        ...


class StreamedFileSource(FileSource):
    """
    A :class:`FileSource` created from a single :class:`StreamedFile`.
    """

    def __init__(
        self, streamed_file: StreamedFile, fs: AbstractFileSystem | None = None
    ):
        super().__init__(fs)
        self._file = streamed_file

    def to_streamed_files(self) -> Iterable[StreamedFile]:
        yield self._file


class DiskFileSource(BrowsableFileSource):
    """
    A :class:`FileSource` corresponding to a single file or a folder on disk. The object
    is identified by a `base_path` and a `relative path`. The `base_path` should be a folder,
    the `relative_path` is optional, and used for selecting only a specific file or folder
    located under `base_folder`. The paths of the files retrieved from this source are given
    relative to the `base_path`.
    """

    def __init__(
        self,
        base_path: str,
        relative_path: str | None = None,
        fs: AbstractFileSystem | None = None,
    ):
        super().__init__(fs)
        assert self._fs.isdir(base_path)
        if relative_path:
            relative_path = sanitize_filepath(relative_path)
            assert is_safe_relative_path(relative_path), 'Unsafe relative_path received'
            self.full_path = os.path.join(base_path, relative_path)
            assert self._fs.exists(self.full_path)
        else:
            self.full_path = base_path
        self.base_path = base_path
        self.relative_path = relative_path

    def to_streamed_files(self) -> Iterable[StreamedFile]:
        for target_path in self._fs.find(self.full_path):
            yield StreamedFile(
                path=os.path.relpath(target_path, self.base_path),
                src=self._fs.open(target_path, 'rb'),
                size=self._fs.size(target_path),
            )

    def to_disk(
        self, destination_dir: str, move_files: bool = False, overwrite: bool = False
    ):
        destination_path = UPath(destination_dir)
        if self.relative_path:
            destination_path /= self.relative_path

        self._fs.mkdirs(destination_path.parent, exist_ok=True)

        if self._fs.exists(destination_path):
            assert overwrite, (
                f'Target {destination_path} already exists and `overwrite` is False'
            )

        self._fs.put(self.full_path, destination_path, recursive=True)

        if move_files:
            self._fs.rm(self.full_path, recursive=True)

    def open(self, path, mode='rb') -> IO:
        assert is_safe_relative_path(path)
        return self._fs.open(os.path.join(self.base_path, path), mode)

    def find(self, path: str) -> list[str]:
        assert is_safe_relative_path(path)
        return self._fs.find(os.path.join(self.base_path, path))

    def child(self, path: str) -> DiskFileSource:
        assert is_safe_relative_path(path)
        return DiskFileSource(self.base_path, path)


def _disk_file_source(path_obj: PathObject) -> DiskFileSource:
    location = path_obj.location
    return DiskFileSource(
        os.path.dirname(location), os.path.basename(location), path_obj._fs
    )


class ZipFileSource(BrowsableFileSource):
    """
    Allows us to "wrap" a :class:`zipfile.ZipFile` object and use it as a :class:`BrowsableFileSource`,
    i.e. it denotes a resource (single file or folder) stored in a ZipFile.
    """

    def __init__(
        self,
        zip_file: str | None,
        sub_path: str = '',
        fs: AbstractFileSystem | None = None,
        zip_fs: ZipFileSystem | None = None,
    ):
        super().__init__(fs)
        assert is_safe_relative_path(sub_path)
        self.sub_path = sub_path
        self._zip_fs = zip_fs or ZipFileSystem(zip_file)
        self._owns_zip_fs = zip_fs is None

    def to_streamed_files(self) -> Iterable[StreamedFile]:
        for target_path in self._zip_fs.find(self.sub_path):
            yield StreamedFile(
                path=target_path,
                src=self._zip_fs.open(target_path),
                size=self._zip_fs.size(target_path),
            )

    def open(self, path, mode='rb') -> IO:
        assert 'r' in mode, 'Mode must be a read mode'
        assert all(c in 'rbt' for c in mode), f'Invalid mode for open command: {mode}'
        f = self._zip_fs.open(path)
        return io.TextIOWrapper(f) if 't' in mode else f

    def find(self, path: str) -> list[str]:
        return self._zip_fs.find(path)

    def child(self, path: str) -> ZipFileSource:
        assert is_safe_relative_path(path), 'Unsafe path provided'
        if self.sub_path:
            assert path.startswith(self.sub_path + os.path.sep), (
                'Provided `path` is not a sub path.'
            )
        return ZipFileSource(
            None,
            path,
            fs=self._fs,
            zip_fs=self._zip_fs,
        )

    def close(self):
        if self._owns_zip_fs:
            self._zip_fs.close()


class CombinedFileSource(FileSource):
    """
    Class for defining a :class:`FileSource` by combining multiple "subsources" into one.
    """

    def __init__(
        self, file_sources: Iterable[FileSource], fs: AbstractFileSystem | None = None
    ):
        """file_sources: an Iterable for getting FileSources."""
        super().__init__(fs)
        self._files = file_sources

    def to_streamed_files(self) -> Iterable[StreamedFile]:
        for file in self._files:
            yield from file.to_streamed_files()

    def to_disk(
        self, destination_dir: str, move_files: bool = False, overwrite: bool = False
    ):
        for file in self._files:
            file.to_disk(destination_dir, move_files, overwrite)


class StandardJSONDecoder(json.JSONDecoder):
    """Our standard JSONDecoder, with support for marshaling of datetime objects"""

    def __init__(self, *args, **kwargs):
        def dict_to_object(d: dict):
            if len(d) == 1 and (v := d.get('$datetime')) is not None:
                return datetime.fromisoformat(v)
            return d

        kwargs['object_hook'] = dict_to_object
        super().__init__(**kwargs)


def json_to_streamed_file(json_dict: dict[str, Any], path: str) -> StreamedFile:
    """Converts a json dictionary structure to a :class:`StreamedFile`."""

    class StandardJSONEncoder(json.JSONEncoder):
        """Our standard JSONEncoder with support for marshaling of datetime objects"""

        def default(self, obj):
            if isinstance(obj, datetime):
                return {'$datetime': obj.isoformat()}
            return super().default(obj)

    json_bytes = json.dumps(json_dict, cls=StandardJSONEncoder).encode()
    return StreamedFile(path=path, src=io.BytesIO(json_bytes), size=len(json_bytes))


def create_zipstream(streamed_files: Iterable[StreamedFile], compress: bool = False):
    """
    Creates a zip stream, i.e. a streamed zip file.
    """
    zs = zipstream.ZipStream(
        compress_type=zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED,
        compress_level=9,
    )

    def content_generator(file):
        with file.src as f:
            while data := f.read(1024 * 1024):
                yield data

    for streamed_file in streamed_files:
        zs.add(content_generator(streamed_file), streamed_file.path)

    yield from zs


async def create_zipstream_async(
    streamed_files: Iterable[StreamedFile], compress: bool = False
):
    for x in create_zipstream(streamed_files, compress):
        yield x
