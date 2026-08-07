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

import re
from typing import Any

import h5py
import numpy as np
import pint
from h5py import File

from nomad.datamodel.metainfo.annotations import H5WebAnnotation
from nomad.metainfo.data_type import NonPrimitive
from nomad.utils import get_logger

LOGGER = get_logger(__name__)


_H5_FILE_ = re.compile(
    r'(?:.*?/uploads/(?P<upload_id>.+?)/(?P<directory>.+?)/)*(?P<file_id>.+?)#(?P<path>.+)'
)


def match_hdf5_reference(reference: str):
    """
    Match reference to HDF5 upload path syntax.
    """
    if not (match := _H5_FILE_.match(reference)):
        return None

    return match.groupdict()


class HDF5Wrapper:
    def __init__(self, file: str, path: str):
        self.file: str = file
        self.path: str = path
        self.handler: h5py.File | None = None

    def __enter__(self):
        self._close()
        self.handler = h5py.File(self.file, 'a')
        return self.handler[self.path]

    def __exit__(self, exc_type, exc_value, traceback):
        self._close()

    def _close(self):
        if self.handler:
            self.handler.close()


class HDF5Reference(NonPrimitive):
    """
    A utility class for handling HDF5 file operations within a given archive.

    This class provides static methods to read, write, and manage datasets and attributes
    in HDF5 files. It ensures that the operations are performed correctly by resolving
    necessary file and upload information from the provided paths and archives.

    Methods
    -------
    _get_upload_files(archive, path: str)
        Retrieves the file ID, path, and upload files object from the given path.

    write_dataset(archive, value: Any, path: str) -> None
        Writes a given value to an HDF5 file at the specified path.

    read_dataset(archive, path: str) -> Any
        Reads a dataset from an HDF5 file at the specified path.

    _normalize_impl(self, value, **kwargs)
        Validates if the given value matches the HDF5 reference format.
    """

    @staticmethod
    def _get_upload_files(archive, path: str):
        match = match_hdf5_reference(path)
        file_id = match['file_id']

        if not file_id:
            LOGGER.error('Invalid HDF5 path.')

        from nomad import files
        from nomad.datamodel.context import ServerContext

        upload_id = match['upload_id']
        if not upload_id:
            upload_id, _ = ServerContext._get_ids(archive, required=True)
        return file_id, match['path'], files.UploadFiles.get(upload_id)

    @staticmethod
    def write_dataset(
        archive,
        value: Any,
        path: str,
        attributes: dict = {},
        quantity_name: str | None = None,
    ) -> None:
        """
        Write value to HDF5 file specified in path following the form
        filename.h5#/path/to/dataset. upload_id is resolved from archive.

        If attributes is provided, will write them to the hdf5 dataset referenced by
        archive quantity with name quantity_name and to its parent group.
        """
        file, path, upload_files = HDF5Reference._get_upload_files(archive, path)
        mode = 'r+b' if upload_files.raw_isfile(file) else 'wb'
        with (
            upload_files.raw_file(file, mode) as raw_file,
            h5py.File(raw_file, 'a') as f,
        ):
            if path in f:
                del f[path]
            f[path] = getattr(value, 'magnitude', value)
            dataset = f[path]

            if attributes and quantity_name:
                path_segments = path.rsplit('/', 1)
                if len(path_segments) == 1:
                    return

                unit = attributes.get('unit')
                long_name = attributes.get('long_name')
                if unit:
                    unit = unit if isinstance(unit, str) else format(unit, '~')
                    dataset.attrs['units'] = unit
                    long_name = f'{long_name} ({unit})'
                if long_name:
                    dataset.attrs['long_name'] = long_name

                target_attributes = {
                    key: val
                    for key, val in attributes.items()
                    if key not in ['signal', 'axes']
                }
                target_attributes['NX_class'] = 'NXdata'
                signal = attributes.get('signal')
                if quantity_name == signal:
                    target_attributes['signal'] = path_segments[1]

                axes = attributes.get('axes', [])
                axes = [axes] if isinstance(axes, str) else axes
                if quantity_name in axes:
                    axes[axes.index(quantity_name)] = path_segments[1]
                    target_attributes['axes'] = axes

                group = dataset.parent
                group.attrs.update(target_attributes)

    @staticmethod
    def read_dataset(archive, path: str) -> Any:
        """
        Read HDF5 dataset from file specified in path following the form
        filename.h5#/path/to/dataset. upload_id is resolved from archive.
        """
        file, path, upload_files = HDF5Reference._get_upload_files(archive, path)
        with upload_files.raw_file(file, 'rb') as raw_file, h5py.File(raw_file) as f:
            return (f[file] if file in f else f)[path][()]

    def _normalize_impl(self, value, **kwargs):
        if match_hdf5_reference(value) is not None:
            return value

        raise ValueError(f'Invalid HDF5 reference: {value}.')


class HDF5Dataset(NonPrimitive):
    """
    A utility class for handling HDF5 dataset operations within a given archive.

    This class provides methods to serialize and normalize HDF5 datasets, ensuring
    that the datasets are correctly referenced and stored within the HDF5 files.

    Methods
    -------
    _serialize_impl(self, value, **kwargs)
        Serializes the given value into a reference string for HDF5 storage.

    _normalize_impl(self, value, **kwargs)
        Normalizes the given value, ensuring it is a valid HDF5 dataset reference
        or a compatible data type (e.g., str, np.ndarray, h5py.Dataset, pint.Quantity).
    """

    def _serialize_impl(self, value, **kwargs):
        if isinstance(value, str):
            return value

        section = kwargs.get('section')

        if not (section_context := section.m_root().m_context):
            raise ValueError('Cannot normalize HDF5 value without context.')

        upload_id, entry_id = section_context._get_ids(section.m_root(), required=True)

        return f'/uploads/{upload_id}/archive/{entry_id}#{section.m_path()}/{self._definition.name}'

    def _normalize_impl(self, value, **kwargs):
        """
        In memory, it is represented by either a reference string or a h5py.Dataset.
        The h5py.Dataset handles file access and data storage under the hood for us.
        There is no need to additionally manage file access here.
        """
        section = kwargs.get('section')
        archive = section.m_root()

        if not (section_context := archive.m_context):
            raise ValueError('Cannot normalize HDF5 value without context.')

        if not isinstance(
            value, str | np.ndarray | h5py.Dataset | pint.Quantity | HDF5Wrapper
        ):
            raise ValueError(f'Invalid HDF5 dataset value: {value}.')

        from nomad.files import FSUtility, UploadFiles

        upload_files = UploadFiles.get(section_context.upload_id)
        hdf5_path: str = upload_files.archive_hdf5_location(archive.entry_id)

        if isinstance(value, str):
            if not (match := match_hdf5_reference(value)):
                # seems to be an illegal reference, do not try to resolve it
                return value

            file, path = match['file_id'], match['path']

            with FSUtility.open_h5(
                hdf5_path, 'r', fs=getattr(upload_files, 'storage_fs', None)
            ) as hdf5_file:
                if file in hdf5_file:
                    segment = f'{file}/{path}'
                else:
                    segment = path

        else:
            unit = self._definition.unit
            if isinstance(value, pint.Quantity):
                if unit is not None:
                    value = value.to(unit).magnitude
                else:
                    unit = value.units
                    value = value.magnitude

            if isinstance(value, HDF5Wrapper):
                if hdf5_path != value.file:
                    raise ValueError('Cannot reference another HDF5 archive.')

            def resolve_h5_root(path: str, group: h5py.Group) -> tuple[str, h5py.Group]:
                if path.startswith('/'):
                    return resolve_h5_root(path[1:], group.file)
                elif path.startswith('../'):
                    return resolve_h5_root(path[3:], group.parent)
                else:
                    return path, group

            def join_path(root: str, base: str):
                base_segment = base.split('/', 1)
                part = base_segment[1] if len(base_segment) > 1 else ''
                if base_segment[0] == '..':
                    return join_path(root.rsplit('/', 1)[0], part)
                elif base_segment[0] == '.':
                    return join_path(root, part)
                else:
                    return base if base.startswith('/') else f'{root}/{base}'

            segment = f'{section.m_path()}/{self._definition.name}'
            with File(hdf5_path, 'a') as hdf5_file:
                target_group = hdf5_file.require_group(section.m_path())
                if self._definition.name in target_group:
                    del target_group[self._definition.name]

                target_group[self._definition.name] = (
                    hdf5_file[value.path] if isinstance(value, HDF5Wrapper) else value
                )
                target_dataset = target_group[self._definition.name]
                # add attrs
                if unit is not None:
                    unit = format(unit, '~')
                    target_dataset.attrs['units'] = unit

                annotation_key = 'h5web'
                s_annotation: H5WebAnnotation = section.m_def.m_get_annotation(
                    annotation_key
                )
                if not s_annotation:
                    s_annotation = section.m_get_annotation(annotation_key)
                if s_annotation:
                    # h5 web does not support full paths, so map to the dataset name
                    annotation_dct = s_annotation.dict()
                    for a_key, a_val in annotation_dct.items():
                        if (
                            a_key not in ['signal', 'axes', 'auxiliary_signals']
                            or a_val is None
                        ):
                            continue
                        list_val = [a_val] if isinstance(a_val, str) else a_val
                        for n, path in enumerate(list_val):
                            path_segments = path.rsplit('/', 1)
                            list_val[n] = path_segments[-1]
                            if (
                                len(path_segments) == 1
                                or path_segments[-1] in target_group
                            ):
                                continue
                            # link dataset if in a different group
                            path, source_group = resolve_h5_root(path, target_group)
                            source_dataset = source_group.get(path)
                            if source_dataset is None:
                                raise ValueError('Reference to non-existent dataset.')
                            target_group[path_segments[-1]] = source_dataset
                            # and errors dataset if available
                            source_errors = source_group.get(f'{path}_errors')
                            if source_errors is not None:
                                target_group[f'{path_segments[-1]}_errors'] = (
                                    source_errors
                                )
                        annotation_dct[a_key] = (
                            list_val if isinstance(a_val, list) else list_val[0]
                        )

                    target_group.attrs.update(
                        {
                            key: val
                            for key, val in annotation_dct.items()
                            if val is not None
                        }
                    )
                    # when errors is specified in section annotation, assign this to
                    # signal
                    if s_annotation.errors and s_annotation.signal:
                        path, source_group = resolve_h5_root(
                            s_annotation.errors, target_group
                        )
                        errors_key = f'{s_annotation.signal.split("/")[-1]}_errors'
                        if (
                            errors_ds := source_group.get(path)
                        ) is not None and errors_key not in target_group:
                            target_group[errors_key] = errors_ds
                    target_group.attrs['NX_class'] = 'NXdata'

                q_annotation = self._definition.m_get_annotation(annotation_key)

                # label
                long_name = q_annotation.long_name if q_annotation else None
                if long_name is None:
                    long_name = self._definition.name
                    long_name = f'{long_name} ({unit})' if unit else long_name
                target_dataset.attrs['long_name'] = long_name

                # indices
                indices = q_annotation.indices if q_annotation else None
                if indices is not None:
                    target_group.attrs[f'{self._definition.name}_indices'] = indices

                # errors
                errors_path = q_annotation.errors if q_annotation else None
                if errors_path is not None:
                    errors_path, source_group = resolve_h5_root(
                        errors_path, target_group
                    )
                    errors_ds = source_group.get(errors_path)
                    path = f'{self._definition.name}_errors'
                    if errors_ds is not None and target_group.get(path) is None:
                        target_group[path] = errors_ds
                else:
                    # handle case when errors is defined after the dataset
                    # iterate over all quantities in sub-sectionssection to check for
                    # errors annotation
                    for sub_section in section.m_root().m_all_contents():
                        for name, quantity in sub_section.m_def.all_quantities.items():
                            if not (
                                q_annotation := quantity.m_get_annotation(
                                    annotation_key
                                )
                            ) or not (errors_path := q_annotation.errors):
                                continue
                            ref_path = join_path(sub_section.m_path(), errors_path)
                            if ref_path == path:
                                errors_key = f'{name}_errors'
                                source_group = hdf5_file.get(sub_section.m_path())
                                if source_group and errors_key not in source_group:
                                    source_group[errors_key] = target_dataset

        return HDF5Wrapper(hdf5_path, segment)
