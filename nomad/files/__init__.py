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

"""Compatibility facade for NOMAD file-storage utilities.

Implementation is organized by filesystem primitives, streaming sources, and uploads.
"""

from .filesystem import (
    DirectoryObject,
    FSUtility,
    PathObject,
    _ReadStats,
    _TimedReadFile,
    _versioned_archive_file_object,
    bundle_info_filename,
    empty_archive_file_size,
    empty_hdf5_file_size,
    empty_zip_file_size,
    logger,
    measure_fs_reads,
    mkdtemp,
)
from .sources import (
    BrowsableFileSource,
    CombinedFileSource,
    DiskFileSource,
    FileSource,
    StandardJSONDecoder,
    StreamedFile,
    StreamedFileSource,
    ZipFileSource,
    _disk_file_source,
    create_zipstream,
    create_zipstream_async,
    json_to_streamed_file,
    zipfile,
)
from .uploads import (
    PublicUploadFiles,
    RawDirPage,
    RawPathInfo,
    RawPathReader,
    StagingUploadFiles,
    UploadFiles,
    ZipRawPathReader,
    _RawEntry,
    clear_index_caches,
)
from .zip_index import object_identity

__all__ = [
    'BrowsableFileSource',
    'CombinedFileSource',
    'DirectoryObject',
    'DiskFileSource',
    'FSUtility',
    'FileSource',
    'PathObject',
    'PublicUploadFiles',
    'RawDirPage',
    'RawPathInfo',
    'RawPathReader',
    'StandardJSONDecoder',
    'StagingUploadFiles',
    'StreamedFile',
    'StreamedFileSource',
    'UploadFiles',
    'ZipFileSource',
    'ZipRawPathReader',
    'bundle_info_filename',
    'clear_index_caches',
    'create_zipstream',
    'create_zipstream_async',
    'empty_archive_file_size',
    'empty_hdf5_file_size',
    'empty_zip_file_size',
    'json_to_streamed_file',
    'measure_fs_reads',
    'mkdtemp',
    'zipfile',
]
