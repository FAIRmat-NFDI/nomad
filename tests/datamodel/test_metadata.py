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

from datetime import datetime, timezone

import numpy as np
import pytest

from nomad.datamodel import EntryData
from nomad.datamodel.data import Author, User
from nomad.datamodel.datamodel import (
    Dataset,
    EntryArchive,
    EntryMetadata,
    SearchableQuantity,
)
from nomad.metainfo import Datetime, MEnum, MSection, Quantity, SubSection
from nomad.metainfo.elasticsearch_extension import schema_separator
from tests.variables import python_schema_name


@pytest.mark.parametrize(
    'source_quantity, source_value, target_quantity, target_value',
    [
        pytest.param(
            Quantity(type=str), 'test', SearchableQuantity.str_value, 'test', id='text'
        ),
        pytest.param(
            Quantity(type=MEnum('one', 'two')),
            'two',
            SearchableQuantity.str_value,
            'two',
            id='keyword',
        ),
        pytest.param(
            Quantity(type=np.float64),
            1.2,
            SearchableQuantity.float_value,
            1.2,
            id='np-double',
        ),
        pytest.param(
            Quantity(type=float),
            1.2,
            SearchableQuantity.float_value,
            1.2,
            id='python-double',
        ),
        pytest.param(Quantity(type=int), 1, SearchableQuantity.int_value, 1, id='long'),
        pytest.param(
            Quantity(type=Datetime),
            datetime.fromtimestamp(0, tz=timezone.utc),
            SearchableQuantity.datetime_value,
            datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc),
            id='date',
        ),
        pytest.param(Quantity(type=str, shape=['*']), ['test'], None, None, id='shape'),
        pytest.param(Quantity(type=str), None, None, None, id='None'),
        pytest.param(
            Quantity(type=str, default='test'), None, None, None, id='default'
        ),
    ],
)
def test_search_quantities(
    source_quantity, source_value, target_quantity, target_value
):
    class Base(MSection):
        test_quantity = source_quantity

    class TestSchema(EntryData, Base):
        pass

    schema = TestSchema()
    if source_value:
        schema.test_quantity = source_value

    archive = EntryArchive(data=schema, metadata=EntryMetadata())
    archive.metadata.apply_archive_metadata(archive)

    if target_quantity:
        assert len(archive.metadata.search_quantities) == 1
    else:
        assert len(archive.metadata.search_quantities) == 0
        return

    searchable_quantity = archive.metadata.search_quantities[0]
    assert searchable_quantity.m_get(target_quantity) == target_value
    assert (
        searchable_quantity.id
        == f'data.test_quantity{schema_separator}test_metadata.TestSchema'
    )
    assert searchable_quantity.definition == 'test_metadata.Base.test_quantity'


def test_search_quantities_nested():
    class MySubSection(MSection):
        value = Quantity(type=str)

    class TestSchema(EntryData, MySubSection):
        children = SubSection(section=MySubSection, repeats=True)

    archive = EntryArchive(metadata=EntryMetadata(entry_name='test'))
    archive.data = TestSchema(value='root')
    archive.data.children.append(MySubSection(value='child1'))
    archive.data.children.append(MySubSection(value='child2'))

    archive.metadata.apply_archive_metadata(archive)
    assert len(archive.metadata.search_quantities) == 3
    data = [(item.id, item.str_value) for item in archive.metadata.search_quantities]
    assert data == [
        (f'data.value{schema_separator}test_metadata.TestSchema', 'root'),
        (f'data.children.value{schema_separator}test_metadata.TestSchema', 'child1'),
        (f'data.children.value{schema_separator}test_metadata.TestSchema', 'child2'),
    ]


def test_section_defs_inclusion():
    """section_defs should content:
    - The actually instantiated sections with `used_directly=True`
    - The sections used through inheritance with `used_directly=False`
    - Should not contain sibling etc. sections that are not used
    """

    class Base(MSection):
        base_value = Quantity(type=str)

    class SiblingSection(Base):
        sibling_value = Quantity(type=str)

    class UsedChild(MSection):
        child_value = Quantity(type=str)

    class UsedSection(Base):
        used_value = Quantity(type=str)
        used_child = SubSection(section=UsedChild)

    class UnusedChild(MSection):
        unused_value = Quantity(type=str)

    class ContainerSchema(EntryData):
        used = SubSection(section=UsedSection)
        unused_child = SubSection(section=UnusedChild)

    archive = EntryArchive(metadata=EntryMetadata())
    archive.data = ContainerSchema()
    archive.data.used = UsedSection(
        base_value='base',
        used_value='used',
        used_child=UsedChild(child_value='child'),
    )

    archive.metadata.apply_archive_metadata(archive)

    section_defs = {
        section_def.definition_qualified_name: section_def.used_directly
        for section_def in archive.metadata.section_defs
    }
    expected_section_defs = {
        ContainerSchema.m_def.qualified_name(): True,
        UsedSection.m_def.qualified_name(): True,
        UsedChild.m_def.qualified_name(): True,
        Base.m_def.qualified_name(): False,
        EntryArchive.m_def.qualified_name(): True,
        EntryMetadata.m_def.qualified_name(): True,
        EntryData.m_def.qualified_name(): False,
        EntryData.m_def.all_base_sections[0].qualified_name(): False,
    }

    assert len(section_defs) == len(expected_section_defs)
    assert section_defs == expected_section_defs


def populate_child(data):
    from nomadschemaexample.schema import MySection

    data.child = MySection(name='test')


def populate_recursive(data):
    from nomadschemaexample.schema import MySectionRecursiveA, MySectionRecursiveB

    data.child_recursive = MySectionRecursiveA(
        child=MySectionRecursiveB(name_b='test_b')
    )


def populate_recursive_secondary(data):
    from nomadschemaexample.schema import MySectionRecursiveA, MySectionRecursiveB

    data.child_recursive = MySectionRecursiveA(
        child=MySectionRecursiveB(child=MySectionRecursiveA(name_a='test_a'))
    )


def populate_reference(data):
    from nomadschemaexample.schema import MySection

    data.child = MySection()
    data.reference_section = data.child


@pytest.mark.parametrize(
    'source_quantity, source_value, target_quantity, target_value, definition',
    [
        pytest.param(
            'name',
            'test',
            SearchableQuantity.str_value,
            'test',
            f'{python_schema_name}.name',
            id='str',
        ),
        pytest.param(
            'count',
            1,
            SearchableQuantity.int_value,
            1,
            f'{python_schema_name}.count',
            id='int',
        ),
        pytest.param(
            'frequency',
            1.0,
            SearchableQuantity.float_value,
            1.0,
            f'{python_schema_name}.frequency',
            id='float',
        ),
        pytest.param(
            'timestamp',
            datetime.fromtimestamp(0, tz=timezone.utc),
            SearchableQuantity.datetime_value,
            datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc),
            f'{python_schema_name}.timestamp',
            id='datetime',
        ),
        pytest.param(
            'child_recursive.child.name_b',
            populate_recursive,
            SearchableQuantity.str_value,
            'test_b',
            'nomadschemaexample.schema.MySectionRecursiveB.name_b',
            id='recursive value: first level',
        ),
        pytest.param(
            'child_recursive.child.child.name_a',
            populate_recursive_secondary,
            SearchableQuantity.str_value,
            'test_a',
            'nomadschemaexample.schema.MySectionRecursiveA.name_a',
            id='recursive value: second level',
        ),
        pytest.param('non_scalar', np.eye(3), None, None, None, id='non-scalar'),
        pytest.param(
            'reference_section',
            populate_reference,
            None,
            datetime(1970, 1, 1, 0, 0, tzinfo=timezone.utc),
            None,
            id='references are skipped',
        ),
        pytest.param(
            'child.name',
            populate_child,
            SearchableQuantity.str_value,
            'test',
            'nomadschemaexample.schema.MySection.name',
            id='child quantity',
        ),
    ],
)
def test_search_quantities_plugin(
    plugin_schema,
    source_quantity,
    source_value,
    target_quantity,
    target_value,
    definition,
):
    """Tests that different types of search quantities are loaded correctly from
    plugin and saved into the search_quantities field."""
    from nomadschemaexample.schema import MySchema

    data = MySchema()

    if callable(source_value):
        source_value(data)
    else:
        setattr(data, source_quantity, source_value)
    archive = EntryArchive(data=data, metadata=EntryMetadata())
    archive.metadata.apply_archive_metadata(archive)

    if target_quantity:
        assert len(archive.metadata.search_quantities) == 1
    else:
        assert len(archive.metadata.search_quantities) == 0
        return

    searchable_quantity = archive.metadata.search_quantities[0]
    assert searchable_quantity.m_get(target_quantity) == target_value
    assert (
        searchable_quantity.id
        == f'data.{source_quantity}{schema_separator}{python_schema_name}'
    )
    assert searchable_quantity.definition == definition


def test_text_search_contents():
    """Test that text search contents are correctly extracted from the data-section."""

    class MySubSection(MSection):
        str_scalar = Quantity(type=str)
        str_array = Quantity(type=str, shape=['*'])
        enum_scalar = Quantity(type=MEnum('enum1', 'enum2'))
        enum_array = Quantity(type=MEnum('enum1', 'enum2'), shape=['*'])

    class MySchema(EntryData, MySubSection):
        children = SubSection(section=MySubSection, repeats=True)

    archive = EntryArchive(metadata=EntryMetadata(entry_name='test'))
    archive.data = MySchema(str_scalar='scalar1', str_array=['array1', ' array2'])
    archive.data.children.append(
        MySubSection(str_scalar=' scalar2', str_array=['  array1  ', 'array3'])
    )
    archive.data.children.append(
        MySubSection(enum_scalar='enum1', enum_array=['enum1', 'enum2'])
    )
    archive.data.children.append(MySubSection(str_scalar='scalar3 '))
    archive.data.children.append(MySubSection(str_scalar='scalar1'))
    archive.data.children.append(MySubSection(str_scalar='  scalar1  '))

    archive.metadata.apply_archive_metadata(archive)
    assert len(archive.metadata.text_search_contents) == 9
    for value in [
        'scalar1',
        'scalar2',
        'scalar3',
        'array1',
        'array2',
        'array3',
        'enum1',
        'enum2',
        'test',  # from metadata.entry_name
    ]:
        assert value in archive.metadata.text_search_contents


def test_text_search_contents_metadata():
    """Test that text search contents are also extracted from "regular" items
    directly on archive.metadata, including dereferenced author names and
    dataset names/DOIs, while internal identifiers/paths and purely technical
    fields are excluded."""
    main_author = User(first_name='Ada', last_name='Lovelace')
    coauthor = Author(first_name='Grace', last_name='Hopper')
    dataset = Dataset(dataset_name='My dataset', doi='10.17172/nomad/test')

    archive = EntryArchive(
        metadata=EntryMetadata(
            upload_id='some-upload-id',
            entry_id='some-entry-id',
            mainfile='raw/some/path/file.out',
            entry_name='file.out',
            upload_name='My upload',
            description='A user provided description',
            comment='A user provided comment',
            main_author=main_author,
            coauthors=[coauthor],
            datasets=[dataset],
            license='CC BY 4.0',
            external_id='external-id-123',
            references=['http://a.example.com', 'http://b.example.com'],
        )
    )

    archive.metadata.apply_archive_metadata(archive)
    contents = archive.metadata.text_search_contents

    for value in [
        'file.out',
        'My upload',
        'A user provided description',
        'A user provided comment',
        'Ada Lovelace',
        'Grace Hopper',
        'My dataset',
        '10.17172/nomad/test',
    ]:
        assert value in contents

    # Internal identifiers, paths and purely technical fields must not leak
    # into the free text search contents.
    for value in [
        'some-upload-id',
        'some-entry-id',
        'raw/some/path/file.out',
        'CC BY 4.0',
        'external-id-123',
        'http://a.example.com',
        'http://b.example.com',
    ]:
        assert value not in contents


def test_text_search_contents_metadata_large_list_excluded():
    """Lists on archive.metadata that exceed the small-list-size limit should
    not be indexed, to avoid polluting the free text search with bulky lists,
    while small lists (e.g. a handful of coauthors) are still indexed."""
    small_coauthors = [Author(first_name='Grace', last_name='Hopper')]
    large_coauthors = [Author(first_name='Person', last_name=str(i)) for i in range(25)]

    small_archive = EntryArchive(metadata=EntryMetadata(coauthors=small_coauthors))
    small_archive.metadata.apply_archive_metadata(small_archive)
    assert 'Grace Hopper' in small_archive.metadata.text_search_contents

    large_archive = EntryArchive(metadata=EntryMetadata(coauthors=large_coauthors))
    large_archive.metadata.apply_archive_metadata(large_archive)
    assert 'Person 0' not in large_archive.metadata.text_search_contents
