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

import datetime
import os

import httpx2
import pytest
import rfc3161ng

from nomad.archive import read_archive, to_json, write_archive
from nomad.datamodel.datamodel import RFC3161Timestamp
from nomad.processing.data import Entry, get_rfc3161_token
from tests.utils import set_upload_entry_metadata


@pytest.mark.parametrize(
    'server,cert,result',
    [
        pytest.param('http://zeitstempel.dfn.de', None, True, id='zeitstempel.dfn.de'),
        pytest.param(
            'http://timestamp.sectigo.com', None, True, id='timestamp.sectigo.com'
        ),
        # pytest.param(
        #     'https://freetsa.org/tsr',
        #     'https://freetsa.org/files/tsa.crt',
        #     True,
        #     id='freetsa.org/tsr',
        # ), # This server response times are unreliable
        pytest.param(
            'http://timestamp.digicert.com/',
            'https://knowledge.digicert.com/content/dam/digicertknowledgebase/attachments/time-stamp/TSACertificate.cer',
            True,
            id='timestamp.digicert.com-correct-cert',
        ),
        pytest.param(
            'http://timestamp.digicert.com/',
            'https://freetsa.org/files/tsa.crt',
            False,
            id='timestamp.digicert.com-wrong-cert',
        ),
    ],
)
def test_rfc3161ng_timestamp(server, cert, result, monkeysession):
    # this is due to requests being used by rfc3161ng
    # requests methods are modified in conftest.py which prohibits calling external servers
    monkeysession.setattr('requests.get', httpx2.get)
    monkeysession.setattr('requests.post', httpx2.post)

    token = get_rfc3161_token('test_hash', server=server, cert=cert)
    if token is not None:
        assert result

        rfc3161ng_time = rfc3161ng.get_timestamp(token)
        assert rfc3161ng_time < datetime.timedelta(seconds=5) + datetime.datetime.now()
        metadata = RFC3161Timestamp()
        metadata.token = token
        new_metadata = RFC3161Timestamp.m_from_dict(metadata.m_to_dict())
        assert new_metadata.token == token
        assert rfc3161ng.get_timestamp(new_metadata.token) == rfc3161ng_time


# flakes sometimes
@pytest.mark.xfail()
def test_rfc3161ng_processing(
    non_empty_processed,
    internal_example_user_metadata,
    monkeypatch,
    proc_infra,
):
    set_upload_entry_metadata(non_empty_processed, internal_example_user_metadata)
    non_empty_processed.publish_upload(embargo_length=12)
    non_empty_processed.block_until_complete()

    published = non_empty_processed
    entry_id = Entry.objects(upload_id=published.upload_id).first().entry_id
    file_path = published.upload_files._create_msg_file_object(
        published.upload_files, published.upload_files.access, fallback=True
    ).os_path

    with read_archive(file_path) as reader:
        archive = to_json(reader[entry_id])
    assert 'entry_timestamp' in archive['metadata']

    original_timestamp = archive['metadata']['entry_timestamp']

    def _re_process():
        published.process_upload()
        published.block_until_complete()
        with read_archive(file_path) as _reader:
            return to_json(_reader[entry_id])

    # 0. assert reprocessing does not change timestamp
    archive = _re_process()
    assert 'entry_timestamp' in archive['metadata']
    assert archive['metadata']['entry_timestamp'] == original_timestamp

    # 1. old timestamp deleted, published, skip published, expect no timestamp
    os.remove(file_path)
    del archive['metadata']['entry_timestamp']
    write_archive(file_path, {entry_id: archive})
    monkeypatch.setattr('nomad.config.process.rfc3161_skip_published', True)
    Entry.objects(entry_id=entry_id).first().update(unset__entry_timestamp=1)
    archive = _re_process()
    assert 'entry_timestamp' not in archive['metadata']

    # 2. published, NOT skip published, expecting timestamp
    monkeypatch.setattr('nomad.config.process.rfc3161_skip_published', False)
    archive = _re_process()
    assert 'entry_timestamp' in archive['metadata']
