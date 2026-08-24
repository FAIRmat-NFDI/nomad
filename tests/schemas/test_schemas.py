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

import pytest

from nomad.metainfo import MSection, Package, Quantity
from nomad.metainfo.util import MDefNotFound, MDefWithoutMetainfo, resolve_m_def
from nomad.mongo.package import PackageDefinition
from nomad.schemas import _mongo_definition_cache, _mongo_package_cache, get_schema
from tests.metainfo.test_metainfo import SectionWithBoth


@pytest.mark.parametrize(
    'qualified_name, expected',
    [
        pytest.param(
            'tests.metainfo.test_metainfo.SectionWithBoth',
            SectionWithBoth.m_def,
            id='python-section',
        ),
        pytest.param(
            'tests.metainfo.test_metainfo.SectionWithBoth.quantity',
            SectionWithBoth.quantity,
            id='python-quantity',
        ),
        pytest.param(
            'tests.metainfo.test_metainfo.SectionWithBoth.subsection_norepeat',
            SectionWithBoth.subsection_norepeat,
            id='python-subsection',
        ),
    ],
)
def test_get_schema_from_python(qualified_name, expected):
    assert get_schema(qualified_name) == expected
    assert get_schema(qualified_name, expected.definition_id) == expected


def test_resolve_m_def_delegates_to_get_schema():
    with pytest.deprecated_call():
        assert (
            resolve_m_def('tests.metainfo.test_metainfo.SectionWithBoth')
            == SectionWithBoth.m_def
        )


@pytest.mark.parametrize(
    'qualified_name, expected_error',
    [
        pytest.param('nonexistent', MDefNotFound, id='non-existent-module'),
        pytest.param('non.existent', MDefNotFound, id='non-existent-class'),
        pytest.param(
            'tests.metainfo.test_metainfo.SectionWithBoth.nonexistent',
            MDefNotFound,
            id='non-existent-property',
        ),
        pytest.param(
            'nomad.metainfo.data_type.Datatype',
            MDefWithoutMetainfo,
            id='valid-class-no-m_def',
        ),
    ],
)
def test_get_schema_python_errors(qualified_name, expected_error):
    with pytest.raises(expected_error):
        get_schema(qualified_name)


def test_get_schema_from_mongodb_and_caches_package(monkeypatch, mongo_module):
    package = Package.m_from_dict(
        {
            'name': 'tests.datamodel.cached_definitions',
            'section_definitions': [{'name': 'First'}, {'name': 'Second'}],
        }
    )
    package.upload_id = 'test-upload'
    package.entry_id = 'test-schema-entry'
    package.init_metainfo()
    PackageDefinition.create_new(package)
    first, second = package.section_definitions

    get_by_calls = []
    original_get_by = PackageDefinition.get_by

    def count_get_by(cls, snapshot_id):
        get_by_calls.append(snapshot_id)
        return original_get_by(snapshot_id)

    monkeypatch.setattr(PackageDefinition, 'get_by', classmethod(count_get_by))

    assert get_schema(first.qualified_name(), first.definition_id).name == 'First'
    assert get_schema(None, first.definition_id).name == 'First'
    assert get_schema(second.qualified_name(), second.definition_id).name == 'Second'
    assert get_by_calls == [first.definition_id]
    assert _mongo_definition_cache[first.definition_id] == (package.definition_id, 0)
    assert _mongo_definition_cache[second.definition_id] == (package.definition_id, 1)

    del _mongo_package_cache[package.definition_id]
    assert get_schema(second.qualified_name(), second.definition_id).name == 'Second'
    assert get_by_calls == [first.definition_id, second.definition_id]


def test_get_schema_from_mongodb_without_definition_id(mongo_module):
    package = Package(name='tests.custom_schema')
    package.upload_id = 'test-upload'
    package.entry_id = 'test-schema-entry'

    class CustomSchema(MSection):
        value = Quantity(type=str)

    package.section_definitions.append(CustomSchema.m_def)
    PackageDefinition.create_new(package)

    definition = get_schema(CustomSchema.m_def.qualified_name())
    assert definition.definition_id == CustomSchema.m_def.definition_id
    assert definition.qualified_name() == CustomSchema.m_def.qualified_name()


def test_get_schema_rejects_mismatched_name_and_id(mongo_module):
    package = Package(name='tests.custom_schema')
    package.upload_id = 'test-upload'
    package.entry_id = 'test-schema-entry'

    class First(MSection):
        pass

    class Second(MSection):
        pass

    package.section_definitions.extend([First.m_def, Second.m_def])
    PackageDefinition.create_new(package)

    with pytest.raises(MDefNotFound, match='does not match'):
        get_schema(First.m_def.qualified_name(), Second.m_def.definition_id)

    with pytest.raises(MDefNotFound, match='does not match'):
        get_schema(
            'tests.metainfo.test_metainfo.SectionWithBoth',
            First.m_def.definition_id,
        )


def test_get_schema_accepts_content_addressed_bundle_copy(mongo_module):
    """Resolve a bundled schema copy by content ID despite its new entry ID."""
    package = Package(name='tests.bundle_schema_copy')
    package.upload_id = 'source-upload'
    package.entry_id = 'source-entry'

    class CopiedSchema(MSection):
        pass

    package.section_definitions.append(CopiedSchema.m_def)
    PackageDefinition.create_new(package)

    definition = get_schema(
        'entry_id:synthetic-bundle-entry.CopiedSchema',
        CopiedSchema.m_def.definition_id,
    )

    assert definition.name == CopiedSchema.m_def.name
    assert definition.definition_id == CopiedSchema.m_def.definition_id


def test_mongo_schema_context_serializes_definition_reference(mongo_module):
    """Serialize an instance whose section definition was loaded from MongoDB."""
    package = Package(name='tests.serialized_mongo_schema')
    package.upload_id = 'source-upload'
    package.entry_id = 'source-entry'

    class MongoSchema(MSection):
        pass

    package.section_definitions.append(MongoSchema.m_def)
    PackageDefinition.create_new(package)

    definition = get_schema(
        f'entry_id:{package.entry_id}.{MongoSchema.m_def.name}',
        MongoSchema.m_def.definition_id,
    )
    section = definition.section_cls()

    serialized = section.m_to_dict(with_root_def=True, with_def_id=True)

    assert serialized['m_def'].startswith(
        '../uploads/source-upload/archive/source-entry#definitions/'
    )
    assert serialized['m_def_id'] == MongoSchema.m_def.definition_id
