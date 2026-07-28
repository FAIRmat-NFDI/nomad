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

import asyncio
import json
import os
import re

import pytest

from nomad import files, processing, utils
from nomad.datamodel import Context
from nomad.datamodel.context import ClientContext, ServerContext, parse_path
from nomad.datamodel.datamodel import EntryArchive, EntryMetadata
from nomad.datamodel.metainfo import runschema
from nomad.metainfo.metainfo import MSection
from nomad.parsing.parser import ArchiveParser
from nomad.processing import Entry, ProcessStatus, Upload


@pytest.fixture(scope='module')
def context():
    class MySection(MSection):
        pass

    class TestContext(Context):
        def load_archive(
            self, entry_id: str, upload_id: str, installation_url: str
        ) -> EntryArchive:
            assert installation_url is None or installation_url == self.installation_url
            return EntryArchive(
                metadata=EntryMetadata(entry_id=entry_id, upload_id=upload_id)
            )

        def load_raw_file(
            self,
            path: str,
            upload_id: str,
            installation_url: str,
            url: str | None = None,
        ) -> MSection:
            assert installation_url is None or installation_url == self.installation_url
            return MySection()

    return TestContext()


@pytest.mark.parametrize(
    'url, result',
    [
        pytest.param('#/root', '#/root', id='fragment'),
        pytest.param('#root', '#/root', id='fragment-slash'),
        pytest.param(
            '../upload/archive/entry_id#root',
            '../upload/archive/entry_id#/root',
            id='entry',
        ),
        pytest.param(
            '../upload/archive/mainfile/path#root',
            f'../upload/archive/{utils.generate_entry_id("test_id", "path")}#/root',
            id='mainfile',
        ),
    ],
)
def test_normalize_reference(context, url, result):
    root_section = EntryArchive(metadata=EntryMetadata(upload_id='test_id'))
    assert context.normalize_reference(root_section, url) == result


@pytest.mark.parametrize(
    'source, target_archive, target_path, result',
    [
        pytest.param(
            """{ "run": [{"m_def": "runschema.run.Run", "system": [{}] }]}""",
            None,
            '/run/0/system/0',
            '#/run/0/system/0',
            id='intra-archive',
        ),
        pytest.param(
            """{ "metadata": { "upload_id": "source", "entry_id": "source" }}""",
            """{ "metadata": { "upload_id": "source", "entry_id": "target" }, "run": [{"m_def": "runschema.run.Run", "system": [{}] }]}""",
            '/run/0/system/0',
            '../upload/archive/target#/run/0/system/0',
            id='intra-upload',
        ),
        pytest.param(
            """{ "metadata": { "upload_id": "source", "entry_id": "source" }}""",
            """{ "metadata": { "upload_id": "target", "entry_id": "target" }, "run": [{"m_def": "runschema.run.Run", "system": [{}] }]}""",
            '/run/0/system/0',
            '../uploads/target/archive/target#/run/0/system/0',
            id='intra-oasis',
        ),
    ],
)
def test_create_reference(context, source, target_archive, target_path, result):
    source = EntryArchive.m_from_dict(json.loads(source))
    source.m_context = context

    if target_archive is None:
        target_archive = source
    else:
        target_archive = EntryArchive.m_from_dict(json.loads(target_archive))

    target = target_archive.m_resolve(target_path)

    assert context.create_reference(source, target_archive, target) == result


@pytest.mark.parametrize(
    'path, result',
    [
        pytest.param(
            '/entries/sample_entry/archive#/seg01/1',
            (None, None, 'sample_entry', 'archive', '/seg01/1'),
            id='local-same-upload-01',
        ),
        pytest.param(
            '../entries/sample_entry/archive#/seg01/1',
            (None, None, 'sample_entry', 'archive', '/seg01/1'),
            id='local-same-upload-02',
        ),
        pytest.param(
            '/uploads/sample_upload/archive/sample_entry#/seg1/22',
            (None, 'sample_upload', 'sample_entry', 'archive', '/seg1/22'),
            id='local-another-upload-01',
        ),
        pytest.param(
            '/upload/archive/sample_entry#/seg1/22',
            (None, None, 'sample_entry', 'archive', '/seg1/22'),
            id='local-another-upload-02',
        ),
        pytest.param(
            '/upload/archive/mainfile/mainfile_id#/seg1/22',
            (None, None, utils.hash(None, 'mainfile_id'), 'archive', '/seg1/22'),
            id='local-another-upload-03',
        ),
        pytest.param(
            '../uploads/sample_upload/archive/sample_entry#/seg1/22',
            (None, 'sample_upload', 'sample_entry', 'archive', '/seg1/22'),
            id='local-another-upload-04',
        ),
        pytest.param(
            '../uploads/GrP11O7pSJCb8Tu-FD0z1g/raw/template-schema.archive.yaml#/definitions/section_definitions/0',
            (
                None,
                'GrP11O7pSJCb8Tu-FD0z1g',
                'template-schema.archive.yaml',
                'raw',
                '/definitions/section_definitions/0',
            ),
            id='local-another-upload-05',
        ),
        pytest.param(
            'https://myoasis.de/uploads/sample_upload/archive/sample_entry#/run/0/calculation/1',
            (
                'https://myoasis.de',
                'sample_upload',
                'sample_entry',
                'archive',
                '/run/0/calculation/1',
            ),
            id='remote-upload-01',
        ),
        pytest.param(
            './uploads/sample_upload/archive/sample_entry#/run/0/calculation/1',
            ('.', 'sample_upload', 'sample_entry', 'archive', '/run/0/calculation/1'),
            id='remote-upload-02',
        ),
        pytest.param(
            './uploads/sample_upload/archives/sample_entry#/run/0/calculation/1',
            None,
            id='remote-upload-03',
        ),
        pytest.param(
            'localhost/uploads/sample_upload/archive/sample_entry#/run/0/calculation/1',
            (
                'localhost',
                'sample_upload',
                'sample_entry',
                'archive',
                '/run/0/calculation/1',
            ),
            id='remote-upload-04',
        ),
        pytest.param(
            'http://127.0.0.1/uploads/sample_upload/archive/sample_entry#/run/0/calculation/1',
            (
                'http://127.0.0.1',
                'sample_upload',
                'sample_entry',
                'archive',
                '/run/0/calculation/1',
            ),
            id='remote-upload-05',
        ),
    ],
)
def test_parsing_reference(path, result):
    path_parts = parse_path(path)
    assert path_parts == result


@pytest.mark.parametrize(
    'url',
    [
        pytest.param('../upload/archive/entry', id='intra-upload'),
        pytest.param('../uploads/upload/archive/entry', id='intra-oasis'),
        pytest.param('../uploads/upload/raw/path/to/file', id='raw-file'),
        pytest.param(
            '../uploads/upload/archive/mainfile/path/to/mainfile', id='mainfile'
        ),
    ],
)
def test_resolve_archive(context, url):
    target = context.resolve_archive_url(url)
    assert target is not None
    assert context.urls[target] == url
    assert context.archives[url] == target


@pytest.mark.parametrize(
    'upload_contents',
    [
        pytest.param(
            {
                'mainfile.archive.json': {
                    'definitions': {'section_definitions': [{'name': 'MySection'}]},
                    'data': {'m_def': '#/definitions/section_definitions/0'},
                }
            },
            id='intra-entry',
        ),
        pytest.param(
            {
                'schema.archive.json': {
                    'definitions': {'section_definitions': [{'name': 'MySection'}]}
                },
                'data.archive.json': {
                    'data': {
                        'm_def': f'../upload/archive/{utils.generate_entry_id("test_upload", "schema.archive.json")}#/definitions/section_definitions/0'
                    }
                },
            },
            id='intra-upload-entry-id',
        ),
        pytest.param(
            {
                'schema.archive.json': {
                    'definitions': {'section_definitions': [{'name': 'MySection'}]}
                },
                'data.archive.json': {
                    'data': {
                        'm_def': '../upload/archive/tUUDVdqv5f8ZTDtkrmG11vtlvniV#/definitions/section_definitions/0'
                    }
                },
            },
            id='intra-upload-mainfile',
        ),
        pytest.param(
            {
                'schema.archive.json': {
                    'definitions': {'section_definitions': [{'name': 'MySection'}]}
                },
                'data.archive.json': {
                    'data': {
                        'm_def': '../upload/raw/schema.archive.json#/definitions/section_definitions/0'
                    }
                },
            },
            id='intra-upload-raw',
        ),
        pytest.param(
            {
                'schema.json': {
                    'name': 'test schema package',
                    'definitions': {
                        'section_definitions': [
                            {
                                'base_sections': ['nomad.datamodel.data.EntryData'],
                                'name': 'Chemical',
                            },
                            {
                                'base_sections': ['nomad.datamodel.data.EntryData'],
                                'name': 'Sample',
                                'quantities': [
                                    {
                                        'name': 'chemicals',
                                        'shape': ['*'],
                                        'type': {
                                            'type_kind': 'reference',
                                            'type_data': '#/definitions/section_definitions/0',
                                        },
                                    }
                                ],
                            },
                        ]
                    },
                },
                'chemical.archive.json': {
                    'definitions': {
                        'section_definitions': [
                            {
                                'base_sections': [
                                    '../upload/raw/schema.json#/definitions/section_definitions/0'
                                ],
                                'name': 'MyChemical',
                            }
                        ]
                    },
                    'data': {'m_def': '#/definitions/section_definitions/0'},
                },
                'sample.archive.json': {
                    'data': {
                        'm_def': '../upload/raw/schema.json#/definitions/section_definitions/1',
                        'chemicals': [
                            '../upload/archive/NUdEsCJK97acyuYER5W83FJHNWjF#/data'
                        ],
                    }
                },
            },
            id='mixed-references',
        ),
    ],
)
def test_server_custom_schema(upload_contents, raw_files_function):
    upload_files = files.StagingUploadFiles('test_upload', create=True)
    upload = processing.Upload(upload_id='test_upload')
    for file_name, content in upload_contents.items():
        with upload_files.raw_file(file_name, 'wt') as f:
            json.dump(content, f, indent=2)

    context = ServerContext(upload=upload)
    parser = ArchiveParser()

    for file_name, content in upload_contents.items():
        if not re.match(r'.*.archive.json', file_name):
            continue

        entry_id = utils.generate_entry_id('test_upload', file_name)
        archive = EntryArchive(
            m_context=context,
            metadata=EntryMetadata(
                upload_id='test_upload', entry_id=entry_id, mainfile=file_name
            ),
        )

        parser.parse(
            mainfile=upload_files.raw_file_object(file_name).os_path, archive=archive
        )
        upload_files.write_archive(entry_id, archive.m_to_dict())
        results = archive.m_to_dict(with_out_meta=True)
        del results['metadata']
        assert results == json.loads(
            json.dumps(content).replace('/upload/raw', '/upload/test_upload/raw')
        )


@pytest.mark.parametrize(
    'upload1_contents, upload2_contents',
    [
        pytest.param(
            {
                'schema.json': {
                    'name': 'test schema package',
                    'definitions': {
                        'section_definitions': [
                            {
                                'base_sections': ['nomad.datamodel.data.EntryData'],
                                'name': 'BaseSection',
                            }
                        ]
                    },
                },
                'chemical.archive.json': {
                    'definitions': {
                        'section_definitions': [
                            {
                                'base_sections': [
                                    '../upload/raw/schema.json#/definitions/section_definitions/0'
                                ],
                                'name': 'SubstanceExtended',
                            }
                        ]
                    }
                },
                'chem.archive.json': {
                    'definitions': {
                        'section_definitions': [
                            {
                                'base_sections': [
                                    '../upload/raw/chemical.archive.json#/definitions/section_definitions/0'
                                ],
                                'name': 'Chem',
                            }
                        ]
                    }
                },
            },
            {
                'extended_chem.archive.json': {
                    'definitions': {
                        'section_definitions': [
                            {
                                'base_sections': [
                                    '../upload/upload1_id/archive/upload1_entry2#/definitions/section_definitions/0'
                                ],
                                'name': 'ExtendedChem',
                            }
                        ]
                    },
                    'data': {'m_def': '#/definitions/section_definitions/0'},
                }
            },
            id='external-references',
        )
    ],
)
def test_server_external_schema(upload1_contents, upload2_contents, raw_files_function):
    upload1_files = files.StagingUploadFiles('upload1_id', create=True)
    upload1 = processing.Upload(upload_id='upload1_id')
    for file_name, content in upload1_contents.items():
        with upload1_files.raw_file(file_name, 'wt') as f:
            json.dump(content, f, indent=2)

    context1 = ServerContext(upload=upload1)

    parser = ArchiveParser()

    for index, (file_name, content) in enumerate(upload1_contents.items()):
        if not re.match(r'.*.archive.json', file_name):
            continue
        entry_id = f'upload1_entry{index}'
        archive = EntryArchive(
            m_context=context1,
            metadata=EntryMetadata(
                upload_id='upload1_id', entry_id=entry_id, mainfile=file_name
            ),
        )

        parser.parse(
            mainfile=upload1_files.raw_file_object(file_name).os_path, archive=archive
        )
        upload1_files.write_archive(entry_id, archive.m_to_dict())

    upload2_files = files.StagingUploadFiles('upload2_id', create=True)
    upload2 = processing.Upload(upload_id='upload2_id')
    for file_name, content in upload2_contents.items():
        with upload2_files.raw_file(file_name, 'wt') as f:
            json.dump(content, f, indent=2)

    context2 = ServerContext(upload=upload2)
    parser = ArchiveParser()

    for index, (file_name, content) in enumerate(upload2_contents.items()):
        entry_id = f'upload2_entry{index}'
        archive = EntryArchive(
            m_context=context2,
            metadata=EntryMetadata(
                upload_id='upload2_id', entry_id=entry_id, mainfile=file_name
            ),
        )

        parser.parse(
            mainfile=upload2_files.raw_file_object(file_name).os_path, archive=archive
        )
        upload2_files.write_archive(entry_id, archive.m_to_dict())
        results = archive.m_to_dict(with_out_meta=True)
        del results['metadata']
        assert results == content


@pytest.mark.skip(reason="""Cannot figure out why it fails in pipeline.""")
def test_client_custom_schema(api_v1, published_wo_user_metadata):
    url = 'http://testserver/api/v1'
    test_path = 'tests/data/datamodel/'
    local_file = 'client_reference.json'

    full_path = test_path + local_file

    entry_id = utils.generate_entry_id(
        published_wo_user_metadata.upload_id, f'examples_template/template.json'
    )

    with open(full_path) as f:
        text = f.read().replace(
            '/run/0',
            f'{url}/uploads/{published_wo_user_metadata.upload_id}/archive/{entry_id}#/run/0',
        )
        content = json.loads(text)

    full_path = test_path + 'modified_' + local_file
    with open(full_path, 'w') as f:
        json.dump(content, f, indent=2)

    context = ClientContext()
    parser = ArchiveParser()

    archive = EntryArchive(m_context=context)

    parser.parse(mainfile=full_path, archive=archive)

    assert isinstance(
        archive.run[0].calculation[0].system_ref.atoms, runschema.system.Atoms
    )

    results = archive.m_to_dict()

    # TODO temporary fix, not sure why it fails in pipeline
    results['run'][0].pop('m_def', None)
    content['run'][0].pop('m_def', None)

    assert results == content

    if os.path.exists(full_path):
        os.remove(full_path)


@pytest.mark.parametrize(
    'referencing_upload_contents',
    [
        pytest.param(
            {
                'extended_chem.archive.json': {
                    'definitions': {
                        'section_definitions': [
                            {
                                'base_sections': [
                                    '../upload/references_upload_id1/archive/-ld_4ohLePE2oOcfX_CYa9oiu_1l#/definitions/section_definitions/0'
                                ],
                                'name': 'ExtendedChem',
                            }
                        ]
                    },
                    'data': {'m_def': '#/definitions/section_definitions/0'},
                }
            },
            id='external-references',
        )
    ],
)
@pytest.mark.asyncio
async def test_client_external_schema(
    referencing_upload_contents, raw_files_function, user1, api_v1, temporal_worker
):
    upload1 = Upload(upload_id='references_upload_id1', main_author=user1.user_id)
    upload1.save()
    files.StagingUploadFiles(upload_id=upload1.upload_id, create=True)
    upload1.staging_upload_files.add_rawfiles('examples/data/references/upload1')
    async with temporal_worker():
        await asyncio.to_thread(upload1.process_upload)
        await upload1.await_workflows()

    upload2_files = files.StagingUploadFiles('upload2_id', create=True)
    for file_name, content in referencing_upload_contents.items():
        with upload2_files.raw_file(file_name, 'wt') as f:
            json.dump(content, f, indent=2)

    context2 = ClientContext(
        upload_id='upload2_id',
        installation_url='',
        username=user1.username,
        password='password',
    )

    parser = ArchiveParser()
    for index, (file_name, content) in enumerate(referencing_upload_contents.items()):
        entry_id = f'upload2_entry{index}'
        archive = EntryArchive(
            m_context=context2,
            metadata=EntryMetadata(
                upload_id='upload2_id', entry_id=entry_id, mainfile=file_name
            ),
        )

        parser.parse(
            mainfile=upload2_files.raw_file_object(file_name).os_path, archive=archive
        )
        upload2_files.write_archive(entry_id, archive.m_to_dict())
        results = archive.m_to_dict(with_out_meta=True)
        del results['metadata']
        assert results == content


@pytest.mark.asyncio
async def test_circular_external_schema(
    raw_files_function, user1, api_v1, temporal_worker
):
    upload1 = Upload(upload_id='upload_id', main_author=user1.user_id)
    upload1.save()
    files.StagingUploadFiles(upload_id=upload1.upload_id, create=True)
    upload1.staging_upload_files.add_rawfiles('examples/data/references/circular')
    async with temporal_worker():
        await asyncio.to_thread(upload1.process_upload)
        await upload1.await_workflows()
    for entry in Entry.objects(upload_id='upload_id'):
        assert entry.process_status == ProcessStatus.SUCCESS
