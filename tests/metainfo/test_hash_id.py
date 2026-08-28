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

from nomad.metainfo import MEnum, Package, Quantity, Section


def simple_quantity():
    return Quantity(
        name='test',
        type=str,
        shape=[],
        description="""Sample description""",
        aliases=['alias1', 'alias2'],
    )


def test_quantity():
    """
    The following properties affect the hash of a Quantity:
        name
        aliases
        type
        shape
        unit
        default
        virtual

    """
    q1 = simple_quantity()
    q2 = simple_quantity()

    ref_hash = q1.definition_id

    assert ref_hash == q2.definition_id

    # order of aliases does not matter
    q2.aliases = ['alias2', 'alias1']
    q2.hash()
    assert ref_hash == q2.definition_id

    # description does not matter
    q2.description = 'Some other text'
    q2.hash()
    assert ref_hash == q2.definition_id

    # different aliases matter
    q2.aliases = ['alias2', 'alias1', 'alias3']
    assert ref_hash != q2.definition_id

    # type matters
    q2 = simple_quantity()
    q2.type = float
    assert ref_hash != q2.definition_id

    # default value matters
    q2 = simple_quantity()
    q2.default = 'default'
    assert ref_hash != q2.definition_id

    # shape matters
    q2 = simple_quantity()
    q2.shape = [1]
    assert ref_hash != q2.definition_id

    # default value matters
    q2 = simple_quantity()
    q2.default = 'default'
    assert ref_hash != q2.definition_id

    # virtual matters
    q2 = simple_quantity()
    q2.virtual = True
    assert ref_hash != q2.definition_id

    q2 = simple_quantity()
    q2.type = MEnum('aad', 'wwa', 'qe')
    q2.hash()
    assert ref_hash != q2.definition_id

    # order of enum values do not matter
    ref_hash = q2.definition_id
    q2.type = MEnum('wwa', 'qe', 'aad')
    q2.hash()
    assert ref_hash == q2.definition_id


def test_definition_id_caching():
    q = simple_quantity()
    assert q._cached_def_id is None
    def_id_1 = q.definition_id
    assert q._cached_def_id == def_id_1

    # Reading again should hit the cached string without re-hashing
    def_id_2 = q.definition_id
    assert def_id_1 is def_id_2

    # Modifying the definition should invalidate the cache and return a new definition_id
    q.name = 'new_test_name'
    def_id_3 = q.definition_id
    assert def_id_3 != def_id_1
    assert q._cached_def_id == def_id_3


def test_definition_id_prefers_snapshot_id_over_cache():
    q = simple_quantity()
    hashed = q.definition_id
    q.snapshot_id = 'snapshot-from-mongo'
    assert q.definition_id == 'snapshot-from-mongo'
    assert q._cached_def_id == hashed


def test_definition_id_cache_includes_section_hash():
    package = Package(name='def_id_cache_pkg')
    section = Section(name='Sec')
    package.m_add_sub_section(Package.section_definitions, section)

    cached = section.definition_id
    assert cached == section.hash().hexdigest()
    assert cached is section.definition_id

    # Section.hash() includes m_path; same section name under another package must differ.
    other_package = Package(name='def_id_cache_pkg_other')
    other_section = Section(name='Sec')
    other_package.m_add_sub_section(Package.section_definitions, other_section)
    assert other_section.definition_id != cached
