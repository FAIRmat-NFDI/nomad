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

import os
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from nomad import files, processing
from nomad.datamodel import EntryArchive, EntryData, EntryMetadata
from nomad.datamodel.context import ServerContext
from nomad.datamodel.hdf5 import HDF5Dataset, HDF5Reference
from nomad.metainfo import Quantity

external_file = 'tests/data/datamodel/context.h5'


@pytest.fixture
def test_context():
    upload_files = files.StagingUploadFiles('test_upload', create=True)
    upload = processing.Upload(upload_id='test_upload')

    upload_files.add_rawfiles(
        external_file,
        os.path.dirname(external_file),
    )
    return ServerContext(upload=upload)


@pytest.mark.parametrize(
    'quantity_type, value',
    [
        [HDF5Dataset, np.ones(3)],
        [HDF5Dataset, np.array([1, 2, 3], dtype=np.int32)],
        [HDF5Dataset, np.array([1, 2, 3], dtype=np.int32)],
        [HDF5Reference, f'{external_file}#/data/value'],
        [HDF5Reference, 'raw.h5#/data/value'],
    ],
)
def test_hdf5(test_context, quantity_type, value):
    class SectionForTest(EntryData):
        quantity = Quantity(type=quantity_type)

    archive = EntryArchive(
        m_context=test_context,
        metadata=EntryMetadata(entry_id='test_entry', upload_id='test_upload'),
        data=SectionForTest(),
    )
    if isinstance(value, str) and value.startswith('raw.h5'):
        HDF5Reference.write_dataset(archive, np.ones(5), value)

    archive.data.quantity = value

    serialized = archive.m_to_dict()

    if quantity_type == HDF5Dataset:
        assert (
            serialized['data']['quantity']
            == '/uploads/test_upload/archive/test_entry#/data/quantity'
        )
    else:
        assert serialized['data']['quantity'] == value
        filename, path = serialized['data']['quantity'].split('#')
        with (
            test_context.upload_files.raw_file(filename, 'rb') as raw_file,
            h5py.File(raw_file) as f,
        ):
            quantity = HDF5Reference.read_dataset(archive, value)
            assert (quantity == f[path][()]).all()


def test_hdf5_dataset_uses_staging_files_for_server_context(test_context, monkeypatch):
    class SectionForTest(EntryData):
        quantity = Quantity(type=HDF5Dataset)

    archive = EntryArchive(
        m_context=test_context,
        metadata=EntryMetadata(entry_id='test_entry', upload_id='test_upload'),
        data=SectionForTest(),
    )

    def fail_if_called(*args, **kwargs):
        pytest.fail('HDF5 dataset normalization must not select files generically')

    monkeypatch.setattr(files.UploadFiles, 'get', fail_if_called)

    archive.data.quantity = np.ones(3)

    assert os.path.isfile(test_context.upload_files.archive_hdf5_location('test_entry'))


def test_hdf5_dataset_falls_back_when_upload_has_no_staging_files(
    test_context, monkeypatch
):
    class SectionForTest(EntryData):
        quantity = Quantity(type=HDF5Dataset)

    upload_files = test_context.upload_files
    test_context.upload = SimpleNamespace(upload_id='test_upload')
    monkeypatch.setattr(files.UploadFiles, 'get', lambda upload_id: upload_files)
    archive = EntryArchive(
        m_context=test_context,
        metadata=EntryMetadata(entry_id='test_entry', upload_id='test_upload'),
        data=SectionForTest(),
    )

    archive.data.quantity = np.ones(3)

    assert os.path.isfile(upload_files.archive_hdf5_location('test_entry'))
