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

import gzip
import inspect
import io
import json
import lzma
import os
import urllib.parse
from collections.abc import Iterator
from types import FunctionType
from typing import IO, Any

from fastapi import HTTPException, Query, status  # noqa: F401
from pydantic import BaseModel, ValidationError  # noqa: F401

from nomad.files import StreamedFile, UploadFiles, create_zipstream


def parameter_dependency_from_model(
    name: str, model_cls: type[BaseModel], exclude: list[str] = []
) -> FunctionType:
    """
    Takes a pydantic model class as input and creates a dependency with corresponding
    Query parameter definitions that can be used for GET
    requests.

    This will only work, if the fields defined in the input model can be turned into
    suitable query parameters. Otherwise fastapi will complain down the road.

    Arguments:
        name: Name for the dependency function.
        model_cls: A ``BaseModel`` inheriting model class as input.
    """
    names = []
    annotations: dict[str, type] = {}
    defaults = []
    for field_name, field_model in model_cls.model_fields.items():
        try:
            if field_name not in exclude:
                names.append(field_name)
                annotations[field_name] = field_model.annotation
                defaults.append(
                    Query(field_model.default, description=field_model.description)
                )
        except Exception:
            pass

    code = inspect.cleandoc(
        """
    def {}({}):
        try:
            return {}({})
        except ValidationError as e:
            errors = e.errors()
            for error in errors:
                error['loc'] = ['query'] + list(error['loc'])
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=errors)

    """.format(
            name,
            ', '.join(names),
            model_cls.__name__,  # type: ignore
            ', '.join([f'{name}={name}' for name in names]),
        )
    )

    compiled = compile(code, 'string', 'exec')
    env = {model_cls.__name__: model_cls}  # type: ignore
    env.update(**globals())
    func = FunctionType(compiled.co_consts[0], env, name)
    func.__annotations__ = annotations
    func.__defaults__ = (*defaults,)

    return func


class DownloadItem(BaseModel):
    """Defines an object (file or folder) for download."""

    upload_id: str
    raw_path: str
    zip_path: str
    entry_metadata: dict[str, Any] | None = None


def create_download_stream_zipped(
    download_items: DownloadItem | Iterator[DownloadItem],
    upload_files: UploadFiles | None = None,
    re_pattern: Any = None,
    recursive: bool = False,
    create_manifest_file: bool = False,
    compress: bool = True,
):
    """
    Creates a zip-file stream for downloading raw data with ``StreamingResponse``.

    Arguments:
        download_items: A DownloadItem, or iterator of DownloadItems, defining what to download.
        upload_files: The UploadFiles object, if already opened (for optimiztion).
        re_pattern: A regex object for filtering by filenames (only applicable to directories).
        recursive: if subdirectories should be included (recursively).
        create_manifest_file: if set, a manifest file is created in the root folder.
        compress: if the zip file should be compressed or not
    """

    def streamed_files(upload_files) -> Iterator[StreamedFile]:
        manifest = []
        try:
            items: Iterator[DownloadItem] = (
                iter([download_items])
                if isinstance(download_items, DownloadItem)
                else download_items
            )
            streamed_paths: set[str] = set()

            for download_item in items:
                if upload_files and upload_files.upload_id != download_item.upload_id:
                    # We're switching to a new upload. Close the old.
                    upload_files.close()
                    upload_files = None
                if not upload_files:
                    # Open the requested upload.
                    upload_files = UploadFiles.get(download_item.upload_id)

                if upload_files is None:  # Added check
                    continue  # Skip if upload_files is None

                all_filtered = True
                files_found = False
                if not upload_files.raw_exists(download_item.raw_path):
                    pass
                elif upload_files.raw_isfile(download_item.raw_path):
                    # File
                    if download_item.zip_path not in streamed_paths:
                        streamed_paths.add(download_item.zip_path)
                        yield StreamedFile(
                            path=download_item.zip_path,
                            src=upload_files.raw_file(download_item.raw_path, 'rb'),
                            size=upload_files.raw_file_size(download_item.raw_path),
                        )
                else:
                    # Directory
                    for path_info in upload_files.raw_listdir(
                        download_item.raw_path, recursive, files_only=True
                    ):
                        files_found = True
                        if not re_pattern or re_pattern.search(path_info.path):
                            all_filtered = False
                            relative_path = os.path.relpath(
                                path_info.path, download_item.raw_path
                            )
                            zip_path = os.path.join(
                                download_item.zip_path, relative_path
                            )
                            if zip_path not in streamed_paths:
                                streamed_paths.add(zip_path)
                                yield StreamedFile(
                                    path=zip_path,
                                    src=upload_files.raw_file(path_info.path, 'rb'),
                                    size=path_info.size,
                                )

                if create_manifest_file and download_item.entry_metadata:
                    if not all_filtered or not files_found:
                        manifest.append(download_item.entry_metadata)

            if create_manifest_file:
                manifest_content = json.dumps(manifest).encode()
                yield StreamedFile(
                    path='manifest.json',
                    src=io.BytesIO(manifest_content),
                    size=len(manifest_content),
                )

        finally:
            if upload_files:
                upload_files.close()

    yield from create_zipstream(streamed_files(upload_files), compress=compress)


def create_download_stream_raw_file(
    upload_files: UploadFiles,
    path: str,
    offset: int = 0,
    length: int = -1,
    decompress=False,
    raw_path_reader=None,
):
    """
    Creates a file stream for downloading raw data with ``StreamingResponse``.

    Arguments:
        upload_files: the UploadFiles object, containing the file. Will be closed when done.
        path: the raw path within the upload to the desired file.
        offset: offset within the file (0 by default)
        length: number of bytes to read. -1 by default, which means the remainder of the
            file will be read.
        decompress: decompresses if the file is compressed (and of a supported type).
    """
    try:
        raw_file_context = (
            raw_path_reader.open('rb')
            if raw_path_reader is not None
            else upload_files.raw_file(path, 'rb')
        )
        with upload_files, raw_file_context as raw_file:
            target_file: IO | io.IOBase = raw_file
            if decompress:
                if path.endswith('.gz'):
                    target_file = gzip.GzipFile(
                        filename=path[:3], mode='rb', fileobj=raw_file
                    )
                elif path.endswith('.xz'):
                    target_file = lzma.open(filename=raw_file, mode='rb')

            assert offset >= 0, 'Invalid offset provided'
            assert length > 0 or length == -1, (
                'Invalid length provided. Should be > 0 or equal to -1.'
            )
            if offset > 0:
                target_file.seek(offset)

            if length > 0:
                # Read up to a certain number of bytes
                yield target_file.read(length)
            else:
                # Read until the end of the file.
                while content := target_file.read(1024 * 1024):
                    yield content
    finally:
        if raw_path_reader is not None:
            raw_path_reader.close()


async def create_stream_from_string(content: str):
    """For returning strings as content using"""
    for x in io.BytesIO(content.encode()):
        yield x


def create_responses(*args) -> dict:
    """Pack status code-response pairs into a dictionary."""
    return dict(args)


def browser_download_headers(
    filename: str, media_type: str = 'application/octet-stream'
) -> dict[str, str]:
    """
    Creates standardized headers which tells browsers that they should download the
    data to a file with the specified filename. Note, the `media_type` should normally be
    either `application/octet-stream` or `application/zip`, using for example `application/json`
    will cause most browsers to try to open and view the file instead of downloading it.

    File names may contain any character the file system accepts, including non-ASCII
    characters and control characters such as CR/LF, none of which can be sent verbatim
    in a header value. Following RFC 6266, the name is therefore sent twice: as an ASCII
    `filename` fallback in which every non-printable and non-ASCII character is replaced
    by '_', and as an RFC 5987 percent-encoded `filename*` carrying the exact name.
    Clients that understand `filename*` use it in preference to `filename`.
    """
    assert filename, 'Filename must be specified'
    ascii_fallback = ''.join(c if ' ' <= c <= '~' else '_' for c in filename)
    ascii_fallback = ascii_fallback.replace('\\', '\\\\').replace('"', '\\"')
    return {
        'Content-Type': media_type,
        'Content-Disposition': (
            f'attachment; filename="{ascii_fallback}"; '
            f"filename*=UTF-8''{urllib.parse.quote(filename, safe='')}"
        ),
    }


def update_url_query_arguments(original_url: str, **kwargs) -> str:
    """
    Takes an url, and returns a new url, obtained by updating the query arguments in the
    `original_url` as specified by the kwargs.
    """
    scheme, netloc, path, params, query, fragment = urllib.parse.urlparse(original_url)
    query_dict = urllib.parse.parse_qs(query)
    for k, v in kwargs.items():
        if v is None:
            # Unset the value
            if k in query_dict:
                query_dict.pop(k)
        else:
            query_dict[k] = [str(v)]
    query = urllib.parse.urlencode(query_dict, doseq=True)
    return urllib.parse.urlunparse((scheme, netloc, path, params, query, fragment))


def get_query_keys(
    d: list | dict,
    exclude_keys: list[str] | None = None,
    current_key_string: str = '',
    keys: set[str] | None = None,
) -> set[str]:
    """
    Extracts all unique keys from a dictionary or list.

    Arguments:
        d: Dictionary or list to process
        exclude_keys: Set of keys to exclude
        current_key_string: Current key string
        keys: Set to store keys

    Returns:
        Set of keys
    """
    if keys is None:
        keys = set()
    if exclude_keys is None:
        exclude_keys = []

    if isinstance(d, dict):
        # If dictionary is empty or has no non-excluded keys, add current key string
        valid_items = [
            (k, v)
            for k, v in d.items()
            if isinstance(v, dict | list) or k not in exclude_keys
        ]
        if not valid_items and current_key_string:
            keys.add(current_key_string)
            return keys

        for key, value in d.items():
            # Determine the new key string based on whether key is excluded
            if key in exclude_keys:
                new_key_string = current_key_string
            else:
                new_key_string = (
                    f'{current_key_string}.{key}' if current_key_string else key
                )

            # Only recurse if value is a dict, list, or key is excluded
            if isinstance(value, dict | list):
                keys.update(get_query_keys(value, exclude_keys, new_key_string, keys))
            elif key not in exclude_keys:
                keys.add(new_key_string)

    elif isinstance(d, list):
        if not d and current_key_string:  # Empty list
            keys.add(current_key_string)
            return keys

        for item in d:
            keys.update(get_query_keys(item, exclude_keys, current_key_string, keys))

    else:
        keys.add(current_key_string)

    return keys


def convert_data_to_dict(data: Any) -> dict[str, Any]:
    """
    Converts a pydantic model or a dictionary containing pydantic models to a dictionary.

    Arguments:
        data: A pydantic model or a dictionary containing pydantic models.
    """
    if hasattr(data, 'dict') and callable(getattr(data, 'dict')):
        return data.dict(by_alias=True)
    elif not isinstance(data, dict):
        return data
    return {k: convert_data_to_dict(v) for k, v in data.items()}


def convert_log_to_json(data: Any) -> tuple[str, list[str]]:
    """
    Converts the log field to json string and returns the keys of the data.
    """

    from nomad.app.v1.models.models import ops

    data = convert_data_to_dict(data)
    exclude_keys = list(ops.keys()) + ['and', 'or', 'not']
    data_keys = list(get_query_keys(data, exclude_keys))
    data_json = json.dumps(data, sort_keys=True)
    return data_json, data_keys


def log_query(logger, query, required=None, endpoint='entries/query'):
    """
    Logs the query and required fields for a search.

    Arguments:
        logger: the logger object to use.
        query: the query parameters.
        required: the required query parameters.
        endpoint: the endpoint.
    """
    query_json, query_keys = convert_log_to_json(query)
    if required:
        required_json, required_keys = convert_log_to_json(required)
        logger.info(
            'search query log',
            query=query_json,
            query_keys=query_keys,
            required=required_json,
            required_keys=required_keys,
            endpoint=endpoint,
        )
    else:
        logger.info(
            'search query log',
            query=query_json,
            query_keys=query_keys,
            endpoint=endpoint,
        )
