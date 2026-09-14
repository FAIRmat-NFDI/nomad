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

"""All generic entry metadata and related classes."""

import os.path
from enum import Enum
from typing import Any

import rfc3161ng
from elasticsearch_dsl import analyzer, tokenizer

from nomad import utils
from nomad.datamodel.metainfo.common import FastAccess
from nomad.metainfo.elasticsearch_extension import (
    Elasticsearch,
    create_searchable_quantity,
    material_entry_type,
)
from nomad.metainfo.elasticsearch_extension import entry_type as es_entry_type
from nomad.metainfo.mongoengine_extension import Mongo, MongoDocument
from nomad.metainfo.pydantic_extension import PydanticModel

from ..metainfo import (
    JSON,
    Bytes,
    Datetime,
    Definition,
    MCategory,
    MEnum,
    MSection,
    Package,
    Quantity,
    Section,
    SubSection,
)
from ..metainfo.data_type import m_str
from ..metainfo.metainfo import Reference
from .util import parse_path

# This is usually defined automatically when the first metainfo definition is evaluated, but
# due to the next imports requiring the m_package already, this would be too late.
m_package = Package()

from .results import Results  # noqa: I001
from .data import (
    Author,
    EntryData,
    ArchiveSection,
    User,
    UserReference,
    AuthorReference,
)
from .optimade import OptimadeEntry
from .metainfo.simulation.legacy_workflows import Workflow as LegacySimulationWorkflow
from .metainfo.workflow import Workflow
from .metainfo.measurements import Measurement
from .metainfo.tabulartree import TabularTree
from nomad.models.common import ProcessStatus

try:
    from runschema.run import Run as run_def
except Exception:
    run_def = None


has_mongo = False

logger = utils.get_logger(__name__)


def _check_mongo_connection():
    try:
        from nomad.infrastructure import mongo_client
    except Exception:
        return

    global has_mongo
    if has_mongo or mongo_client is None:
        return

    try:
        mongo_client.server_info()
        has_mongo = True
    except Exception as e:
        logger.warning('MongoDB connection failed.', exc_info=e)


class AuthLevel(int, Enum):
    """
    Used to decorate fields with the authorization level required to edit them (using `a_auth_level`).
    * `none`: No authorization required
    * `coauthor`: You must be at least a coauthor of the upload to edit the field.
    * `main_author`: You must be the main author of the upload to edit the field.
    * `admin`: You must be admin to edit the field.
    """

    none = 0
    coauthor = 1
    main_author = 2
    admin = 3


path_analyzer = analyzer(
    'path_analyzer', tokenizer=tokenizer('path_tokenizer', 'pattern', pattern='/')
)


def PathSearch():
    return [
        Elasticsearch(_es_field='keyword'),
        Elasticsearch(
            mapping=dict(type='text', analyzer=path_analyzer.to_dict()),
            field='path',
            _es_field='',
        ),
    ]


quantity_analyzer = analyzer(
    'quantity_analyzer',
    tokenizer=tokenizer('quantity_tokenizer', 'pattern', pattern='.'),
)


def QuantitySearch():
    return [
        Elasticsearch(material_entry_type, _es_field='keyword'),
        Elasticsearch(
            material_entry_type,
            mapping=dict(type='text', analyzer=path_analyzer.to_dict()),
            field='path',
            _es_field='',
        ),
    ]


class Dataset(MSection):
    """A Dataset is attached to one or many entries to form a set of data.

    Args:
        dataset_id: The unique identifier for this dataset as a string. It should be
            a randomly generated UUID, similar to other nomad ids.
        dataset_name: The human-readable name of the dataset as string. The dataset name must be
            unique for the user.
        user_id: The unique user_id of the owner and creator of this dataset. The owner
            must not change after creation.
        doi: The optional Document Object Identifier (DOI) associated with this dataset.
            Nomad can register DOIs that link back to the respective representation of
            the dataset in the nomad UI. This quantity holds the string representation of
            this DOI. There is only one per dataset. The DOI is just the DOI name, not its
            full URL, e.g. "10.17172/nomad/2019.10.29-1".
        pid: The original NOMAD CoE Repository dataset PID. Old DOIs still reference
            datasets based on this id. Is not used for new datasets.
        dataset_create_time: The date when the dataset was first created.
        dataset_modified_time: The date when the dataset was last modified. An owned dataset
            can only be extended after a DOI was assigned. A foreign dataset cannot be changed
            once a DOI was assigned.
        dataset_type: The type determined if a dataset is owned, i.e. was created by
            the authors of the contained entries; or if a dataset is foreign,
            i.e. it was created by someone not necessarily related to the entries.
    """

    m_def = Section(a_mongo=MongoDocument(), a_pydantic=PydanticModel())

    dataset_id = Quantity(
        type=str,
        a_mongo=Mongo(primary_key=True),
        a_elasticsearch=Elasticsearch(material_entry_type),
    )
    dataset_name = Quantity(
        type=str,
        a_mongo=Mongo(index=True),
        a_elasticsearch=[
            Elasticsearch(material_entry_type),
            Elasticsearch(suggestion='default'),
        ],
    )
    user_id = Quantity(type=str, a_mongo=Mongo(index=True))
    doi = Quantity(
        type=str,
        a_mongo=Mongo(index=True),
        a_elasticsearch=Elasticsearch(material_entry_type),
    )
    pid = Quantity(type=str, a_mongo=Mongo(index=True))
    dataset_create_time = Quantity(
        type=Datetime, a_mongo=Mongo(index=True), a_elasticsearch=Elasticsearch()
    )
    dataset_modified_time = Quantity(
        type=Datetime, a_mongo=Mongo(index=True), a_elasticsearch=Elasticsearch()
    )
    dataset_type = Quantity(
        type=MEnum('owned', 'foreign'),
        a_mongo=Mongo(index=True),
        a_elasticsearch=Elasticsearch(),
    )
    query = Quantity(type=JSON, a_mongo=Mongo())
    entries = Quantity(type=str, shape=['*'], a_mongo=Mongo())


class DatasetReference(Reference):
    def __init__(self):
        super().__init__(Dataset.m_def)

    def _normalize_impl(self, value, **kwargs):
        if isinstance(value, Dataset):
            return value

        if isinstance(value, str):
            try:
                if (target := Dataset.m_def.a_mongo.get(dataset_id=value)) is not None:
                    return target
            except Exception:  # noqa
                return value

        raise ValueError(f'Cannot normalize {value}.')

    def _serialize_impl(self, section, value):
        if isinstance(value, str):
            return value

        return value.dataset_id


class EditableUserMetadata(MCategory):
    """NOMAD entry metadata quantities that can be edited by the user before or after publish."""

    pass


class MongoUploadMetadata(MCategory):
    """The field is defined on the Upload mongo document."""

    pass


class MongoEntryMetadata(MCategory):
    """The field is defined on the Entry mongo document."""

    pass


class MongoSystemMetadata(MCategory):
    """
    The field is managed directly by the system/process (or derived from data managed by the
    system/process), and should never be updated from an :class:`EntryMetadata` object.
    """

    pass


class DomainMetadata(MCategory):
    """NOMAD entry quantities that are determined by the uploaded data."""

    pass


def derive_origin(entry: 'EntryMetadata') -> str:
    if entry.external_db is not None:
        return str(entry.external_db)

    if entry.main_author:
        return entry.main_author.name

    return None


def derive_authors(entry: 'EntryMetadata') -> list[User]:
    if entry.external_db == 'EELS Data Base':
        return list(entry.entry_coauthors)

    if entry.entry_type == 'AIToolkitNotebook':
        return list(entry.entry_coauthors)

    authors: list[User] = [entry.main_author]
    if entry.coauthors:
        authors.extend(entry.coauthors)
    if entry.entry_coauthors:
        authors.extend(entry.entry_coauthors)
    return authors


class CompatibleSectionDef(MSection):
    """A directly used section definition or one of its recursive base sections."""

    definition_qualified_name = Quantity(
        type=str,
        description='The qualified name of the compatible section.',
        a_elasticsearch=Elasticsearch(material_entry_type),
    )
    definition_id = Quantity(
        type=str,
        description='The definition id of the compatible section.',
        a_elasticsearch=Elasticsearch(material_entry_type),
    )
    used_directly = Quantity(
        type=bool,
        description='If the compatible section is directly used as base section.',
        a_elasticsearch=Elasticsearch(material_entry_type),
    )


class EntryArchiveReference(MSection):
    m_def = Section(label='ArchiveReference')

    target_reference = Quantity(
        type=str,
        description='The full url like reference of the the target.',
        a_elasticsearch=Elasticsearch(),
    )
    target_entry_id = Quantity(
        type=str,
        description='The id of the entry containing the target.',
        a_elasticsearch=Elasticsearch(),
    )
    target_mainfile = Quantity(
        type=str,
        description='The mainfile of the entry containing the target.',
        a_elasticsearch=Elasticsearch(),
    )
    target_upload_id = Quantity(
        type=str,
        description='The id of the upload containing the target.',
        a_elasticsearch=Elasticsearch(),
    )
    target_name = Quantity(
        type=str,
        description='The name of the target quantity/section.',
        a_elasticsearch=Elasticsearch(),
    )
    target_path = Quantity(
        type=str,
        description='The path of the target quantity/section in its archive.',
        a_elasticsearch=Elasticsearch(),
    )
    source_name = Quantity(
        type=str,
        description='The name of the source (self) quantity/section in its archive.',
        a_elasticsearch=Elasticsearch(),
    )
    source_path = Quantity(
        type=str,
        description='The path of the source (self) quantity/section in its archive.',
        a_elasticsearch=Elasticsearch(),
    )
    source_quantity = Quantity(
        type=str,
        description='A reference to the quantity definition that defines the reference',
        a_elasticsearch=Elasticsearch(),
    )


class SearchableQuantity(MSection):
    # To be deprecated: 'id' no longer enough to identify the definition
    id = Quantity(
        type=str,
        description="""
            The full identifier for this quantity that contains the path in the schema +
            schema name.
        """,
        a_elasticsearch=[Elasticsearch()],
    )
    # To be deprecated: definition available in `segments`
    definition = Quantity(
        type=str,
        description='A reference to the quantity definition.',
        a_elasticsearch=Elasticsearch(),
    )
    path_archive = Quantity(
        type=str,
        description='Path of the value within the archive. This path also include list indices, e.g. data.section[0].value',
        a_elasticsearch=Elasticsearch(),
    )
    segments = Quantity(
        type=JSON,
        description="""
        Contains the definitions and path for each segment in the search quantity path.
        For example, if you have a path `data.subsection.value` defined in a custom
        schema, the stored segments would be:

        segments = {
          '1': {
            'path': 'data'
            'definitions': [
                'mypackage.schema_packages.simulation.MySimulation',
                'mypackage.schema_packages.simulation.Simulation',
            ]
          },
          '2': {
            'path': 'calculation',
            'definitions': [
                'mypackage.schema_packages.simulation.MyCalculation,
                'mypackage.schema_packages.simulation.Calculation,
            ']
          },
          '-1': {
            'path': 'value',
            'definitions': ['mypackage.schema_packages.simulation.Calculation']
          },
        }

        Note that:
         - The keys are the segment indices.
         - The key 0 is reserved for the root section (i.e. EntryArchive) but none of the
           information is currently stored.
         - The key -1 is used for the last segment, which is the quantity. This allows
           queries starting from the end as well, and helps with retrieving the quantity
           definition. Note that the definition stored for the quantity points to the
           section that ultimately defines the quantity, which may not be clear from the
           definition of the parent section due to inheritance.
        """,
        a_elasticsearch=Elasticsearch(mapping={'type': 'object', 'dynamic': True}),
    )
    bool_value = Quantity(
        type=bool,
        description='The value mapped as an ES boolean field.',
        a_elasticsearch=Elasticsearch(mapping='boolean'),
    )
    str_value = Quantity(
        type=str,
        description="""
        The value mapped as an ES text and keyword field.
        """,
        a_elasticsearch=[
            Elasticsearch(mapping='text'),
            Elasticsearch(mapping='keyword', field='keyword'),
        ],
    )
    int_value = Quantity(
        type=int,
        description='The value mapped as an ES long number field.',
        a_elasticsearch=Elasticsearch(mapping='long'),
    )
    float_value = Quantity(
        type=float,
        description='The value mapped as an ES double number field.',
        a_elasticsearch=Elasticsearch(mapping='double'),
    )
    datetime_value = Quantity(
        type=Datetime,
        description='The value mapped as an ES date field.',
        a_elasticsearch=Elasticsearch(mapping='date'),
    )


class RFC3161Timestamp(MSection):
    token_seed = Quantity(
        type=str, description='The entry hash used to get timestamp token.'
    )
    token = Quantity(type=Bytes, description='The token returned by RFC3161 server.')
    tsa_server = Quantity(type=str, description='The address of RFC3161 server.')
    timestamp = Quantity(type=Datetime, description='The RFC3161 timestamp.')

    @property
    def verify_timestamp(self):
        """
        Verify token by converting it to a timestamp ad-hoc.
        """
        return rfc3161ng.get_timestamp(self.token)


class EntryMetadata(MSection):
    """
    Attributes:
        upload_id: The id of the upload (random UUID).
        upload_name: The user provided upload name.
        upload_create_time: The time that the upload was created
        entry_id: The unique mainfile based entry id.
        entry_hash: The raw file content based checksum/hash of this entry.
        entry_create_time: The time that the entry was created
        last_edit_time: The date and time the user metadata was last edited.
        parser_name: The NOMAD parser used for the last processing.
        mainfile: The path to the mainfile from the root directory of the uploaded files.
        mainfile_key: Key used to differentiate between different *child entries* of an entry.
            For parent entries and entries that do not have any children, the value should
            be empty.
        files: A list of all files, relative to upload.
        pid: The unique, sequentially enumerated, integer PID that was used in the legacy
            NOMAD CoE. It allows to resolve URLs of the old NOMAD CoE Repository.
        raw_id: The code specific identifier extracted from the entry's raw files by the parser,
            if supported.
        external_id: A user provided external id. Usually the id for an entry in an external
            database where the data was imported from.
        published: Indicates if the entry is published.
        publish_time: The time when the upload was published.
        with_embargo: Indicated if this entry is under an embargo. Entries with embargo are
            only visible to the main author, the upload coauthors, and the upload reviewers.
        license: A short license description (e.g. CC BY 4.0), that refers to the
            license of this entry.
        processed: Boolean indicating if this entry was successfully processed and archive
            data and entry metadata is available.
        last_processing_time: The date and time of the last processing.
        processing_errors: Errors that occurred during processing.
        nomad_version: A string that describes the version of the nomad software that was
            used to do the last successful processing.
        nomad_commit: The NOMAD commit used for the last processing.
        comment: An arbitrary string with user provided information about the entry.
        references: A list of URLs for resources that are related to the entry.
        external_db: The repository or external database where the original data resides.
        origin: A short human readable description of the entries origin. Usually it is the
            handle of an external database/repository or the name of the main author.
        main_author: Id of the main author of this entry.
        coauthors: A list of co-authors for the whole upload. These can view and edit the
            upload when in staging, and view it also if it is embargoed.
        coauthor_groups: List of co-author groups, cf. `coauthors`.
        entry_coauthors: Ids of all co-authors (excl. the main author and upload coauthors)
            specified on the entry level, rather than on the upload level. They are shown
            as authors of this entry alongside its main author and upload coauthors.
        reviewers: Ids of users who can review the upload which this entry belongs to. Like
            the main author and the upload coauthors, reviewers can find, see, and download
            all data from the upload and all its entries, even if it is in staging or has
            an embargo.
        reviewer_groups: List of reviewer groups, cf. `reviewers`.
        datasets: Ids of all datasets that this entry appears in
        readonly: It indicates that no manual editing should happen
    """

    m_def = Section(label='Metadata')

    upload_id = Quantity(
        type=str,
        categories=[MongoUploadMetadata],
        description='The persistent and globally unique identifier for the upload of the entry',
        a_elasticsearch=Elasticsearch(
            material_entry_type, metrics=dict(n_uploads='cardinality')
        ),
    )

    upload_name = Quantity(
        type=str,
        categories=[MongoUploadMetadata, EditableUserMetadata],
        description='The user provided upload name',
        a_elasticsearch=[
            Elasticsearch(material_entry_type),
            Elasticsearch(suggestion='default'),
        ],
    )

    upload_create_time = Quantity(
        type=Datetime,
        categories=[MongoUploadMetadata, EditableUserMetadata],
        description='The date and time when the upload was created in nomad',
        a_auth_level=AuthLevel.admin,
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    description = Quantity(
        type=str,
        categories=[MongoUploadMetadata, EditableUserMetadata],
        description='Further information about the upload.',
        a_elasticsearch=[
            Elasticsearch(material_entry_type),
            Elasticsearch(suggestion='default'),
        ],
    )

    entry_id = Quantity(
        type=str,
        description='A persistent and globally unique identifier for the entry',
        categories=[MongoEntryMetadata, MongoSystemMetadata],
        a_elasticsearch=Elasticsearch(
            material_entry_type, metrics=dict(n_entries='cardinality')
        ),
    )

    entry_name = Quantity(
        type=str,
        description='A brief human readable name for the entry.',
        a_elasticsearch=[
            Elasticsearch(material_entry_type, _es_field='keyword'),
            Elasticsearch(suggestion='default'),
            Elasticsearch(
                material_entry_type,
                field='prefix',
                es_query='match_phrase_prefix',
                mapping='text',
                _es_field='',
            ),
        ],
    )

    entry_type = Quantity(
        type=str,
        description='The main schema definition. This is the name of the section used for data.',
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    calc_id = Quantity(
        type=str,
        description='Legacy field name, use `entry_id` instead.',
        derived=lambda entry: entry.entry_id,
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    entry_hash = Quantity(
        # Note: This attribute is not stored in ES
        type=str,
        description='A raw file content based checksum/hash',
        categories=[MongoEntryMetadata],
    )

    entry_timestamp = SubSection(
        sub_section=RFC3161Timestamp, description='A timestamp based on RFC3161.'
    )

    entry_create_time = Quantity(
        type=Datetime,
        categories=[MongoEntryMetadata, MongoSystemMetadata, EditableUserMetadata],
        description='The date and time when the entry was created in nomad',
        a_auth_level=AuthLevel.admin,
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    last_edit_time = Quantity(
        # Note: This attribute is not stored in ES
        type=Datetime,
        categories=[MongoEntryMetadata],
        description='The date and time the user metadata was last edited.',
    )

    parser_name = Quantity(
        type=str,
        categories=[MongoEntryMetadata, MongoSystemMetadata],
        description='The NOMAD parser used for the last processing',
        a_elasticsearch=Elasticsearch(),
    )

    mainfile = Quantity(
        type=str,
        categories=[MongoEntryMetadata, MongoSystemMetadata],
        description='The path to the mainfile from the root directory of the uploaded files',
        a_elasticsearch=PathSearch() + [Elasticsearch(suggestion='default')],
    )

    mainfile_key = Quantity(
        type=str,
        categories=[MongoEntryMetadata, MongoSystemMetadata],
        description="""
            Key used to differentiate between different *child entries* of an entry.
            For parent entries and entries that do not have any children, the value should
            be empty.
        """,
        a_elasticsearch=PathSearch(),
    )

    text_search_contents = Quantity(
        type=str,
        shape=['*'],
        description="""Contains text contents that should be considered when
        doing free text queries for entries.""",
        a_elasticsearch=[Elasticsearch(mapping='text', es_query='match_bool_prefix')],
    )

    files = Quantity(
        type=str,
        shape=['0..*'],
        description="""
            The paths to the files within the upload that belong to this entry.
            All files within the same directory as the entry's mainfile are considered the
            auxiliary files that belong to the entry.
        """,
        a_elasticsearch=PathSearch(),
    )

    pid = Quantity(
        type=str,
        description="""
            The unique, sequentially enumerated, integer PID that was used in the legacy
            NOMAD CoE. It allows to resolve URLs of the old NOMAD CoE Repository.
        """,
        categories=[MongoEntryMetadata],
        a_elasticsearch=Elasticsearch(es_entry_type),
    )

    raw_id = Quantity(
        type=str,
        description="""
            The code specific identifier extracted from the entry's raw files by the parser,
            if supported.
        """,
        a_elasticsearch=Elasticsearch(es_entry_type),
    )

    external_id = Quantity(
        type=str,
        categories=[MongoEntryMetadata, EditableUserMetadata],
        description="""
            A user provided external id. Usually the id for an entry in an external database
            where the data was imported from.
        """,
        a_elasticsearch=Elasticsearch(),
    )

    published = Quantity(
        type=bool,
        default=False,
        description='Indicates if the entry is published',
        categories=[MongoUploadMetadata],
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    publish_time = Quantity(
        type=Datetime,
        categories=[MongoUploadMetadata, EditableUserMetadata],
        description='The date and time when the upload was published in nomad',
        a_auth_level=AuthLevel.admin,
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    with_embargo = Quantity(
        type=bool,
        default=False,
        categories=[MongoUploadMetadata, MongoSystemMetadata],
        description='Indicated if this entry is under an embargo',
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    embargo_length = Quantity(
        # Note: This attribute is not stored in ES
        type=int,
        categories=[MongoUploadMetadata, EditableUserMetadata],
        description='The length of the requested embargo period, in months',
    )

    license = Quantity(
        # Note: This attribute is not stored in ES
        type=str,
        description="""
            A short license description (e.g. CC BY 4.0), that refers to the
            license of this entry.
        """,
        default='CC BY 4.0',
        categories=[MongoUploadMetadata, EditableUserMetadata],
        a_auth_level=AuthLevel.admin,
    )

    processed = Quantity(
        type=bool,
        default=False,
        categories=[MongoEntryMetadata, MongoSystemMetadata],
        description='Indicates that the entry is successfully processed.',
        a_elasticsearch=Elasticsearch(),
    )

    process_status = Quantity(
        type=MEnum(
            ProcessStatus.READY,
            ProcessStatus.PENDING,
            ProcessStatus.RUNNING,
            ProcessStatus.WAITING_FOR_RESULT,
            ProcessStatus.SUCCESS,
            ProcessStatus.FAILURE,
            ProcessStatus.DELETED,
        ),
        description='The status of the current or last completed processing step.',
        a_elasticsearch=Elasticsearch(),
    )

    last_processing_time = Quantity(
        type=Datetime,
        description='The date and time of the last processing.',
        categories=[MongoEntryMetadata],
        a_elasticsearch=Elasticsearch(),
    )

    processing_errors = Quantity(
        type=str,
        shape=['*'],
        description='Errors that occurred during processing',
        a_elasticsearch=Elasticsearch(),
    )

    nomad_version = Quantity(
        type=str,
        description='The NOMAD version used for the last processing',
        categories=[MongoEntryMetadata],
        a_elasticsearch=Elasticsearch(),
    )

    nomad_commit = Quantity(
        type=str,
        description='The NOMAD commit used for the last processing',
        categories=[MongoEntryMetadata],
        a_elasticsearch=Elasticsearch(),
    )

    nomad_distro_commit_url = Quantity(
        type=str,
        description='The NOMAD distro commit url used for the last processing',
        categories=[MongoEntryMetadata],
        a_elasticsearch=Elasticsearch(),
    )
    comment = Quantity(
        type=str,
        categories=[MongoEntryMetadata, EditableUserMetadata],
        description='A user provided comment for this entry',
        a_elasticsearch=Elasticsearch(mapping='text'),
    )

    references = Quantity(
        type=str,
        shape=['0..*'],
        categories=[MongoEntryMetadata, EditableUserMetadata],
        description='User provided references (URLs) for this entry',
        a_elasticsearch=Elasticsearch(),
    )

    external_db = Quantity(
        type=MEnum(
            'The Perovskite Database Project',
            'EELS Data Base',
            'Materials Project',
            'AFLOW',
            'OQMD',
            'Kyoto Phonopy Database',
            'CatTestHub',
            'Catalyst Acquisition by Data Sharing (CADS)',
            'Open Catalyst Project',
        ),
        categories=[MongoUploadMetadata, EditableUserMetadata],
        description='The repository or external database where the original data resides',
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    origin = Quantity(
        type=str,
        description="""
            A short human readable description of the entries origin. Usually it is the
            handle of an external database/repository or the name of the main author.
        """,
        derived=derive_origin,
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    main_author = Quantity(
        type=UserReference,
        categories=[MongoUploadMetadata, EditableUserMetadata],
        description='The main author of the entry',
        a_auth_level=AuthLevel.admin,
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    coauthors = Quantity(
        # Note: This attribute is not stored in ES
        type=AuthorReference,
        shape=['0..*'],
        default=[],
        categories=[MongoUploadMetadata, EditableUserMetadata],
        description="""
            A user provided list of co-authors for the whole upload. These can view and edit the
            upload when in staging, and view it also if it is embargoed.
        """,
    )

    coauthor_groups = Quantity(
        # Note: This attribute is not stored in ES
        type=str,
        shape=['0..*'],
        default=[],
        categories=[MongoUploadMetadata, EditableUserMetadata],
        description="""
            A list of co-author groups for the whole upload, cf. `coauthors`.
        """,
    )

    entry_coauthors = Quantity(
        # Note: This attribute is not stored in ES
        type=AuthorReference,
        shape=['0..*'],
        default=[],
        categories=[MongoEntryMetadata],
        description="""
            A user provided list of co-authors specific for this entry. This is a legacy field,
            for new uploads, coauthors should be specified on the upload level only.
        """,
    )

    reviewers = Quantity(
        # Note: This attribute is not stored in ES
        type=UserReference,
        shape=['0..*'],
        default=[],
        categories=[MongoUploadMetadata, EditableUserMetadata],
        description="""
            A user provided list of reviewers. Reviewers can see the whole upload, also if
            it is unpublished or embargoed
        """,
    )

    reviewer_groups = Quantity(
        # Note: This attribute is not stored in ES
        type=str,
        shape=['0..*'],
        default=[],
        categories=[MongoUploadMetadata, EditableUserMetadata],
        description="""
            A list of reviewer groups, cf. `reviewers`.
        """,
    )

    authors = Quantity(
        type=AuthorReference,
        shape=['0..*'],
        description='All authors (main author and co-authors)',
        derived=derive_authors,
        a_elasticsearch=Elasticsearch(
            material_entry_type, metrics=dict(n_authors='cardinality')
        ),
    )

    writers = Quantity(
        type=UserReference,
        shape=['0..*'],
        description='All writers (main author, upload coauthors)',
        derived=lambda entry: (
            ([entry.main_author] if entry.main_author is not None else [])
            + entry.coauthors
        ),
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    writer_groups = Quantity(
        type=str,
        shape=['0..*'],
        description='Groups with write access (= coauthor groups).',
        derived=lambda entry: entry.coauthor_groups,
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    viewers = Quantity(
        type=UserReference,
        shape=['0..*'],
        description='All viewers (main author, upload coauthors, and reviewers)',
        derived=lambda entry: (
            ([entry.main_author] if entry.main_author is not None else [])
            + entry.coauthors
            + entry.reviewers
        ),
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    viewer_groups = Quantity(
        type=str,
        shape=['0..*'],
        description='Groups with read access (= coauthor groups + reviewer groups).',
        derived=lambda entry: entry.coauthor_groups + entry.reviewer_groups,
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    datasets = Quantity(
        type=DatasetReference(),
        shape=['0..*'],
        default=[],
        categories=[MongoEntryMetadata, EditableUserMetadata],
        description='A list of user curated datasets this entry belongs to.',
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    optimade = SubSection(
        sub_section=OptimadeEntry,
        description='Metadata used for the optimade API.',
        a_elasticsearch=Elasticsearch(es_entry_type),
    )

    domain = Quantity(
        type=MEnum('dft', 'ems', 'nexus'),
        description='The material science domain',
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    n_quantities = Quantity(
        type=int,
        default=0,
        description='Number of metainfo quantities parsed from the entry.',
        a_elasticsearch=Elasticsearch(metrics=dict(n_quantities='sum')),
    )

    quantities = Quantity(
        type=str,
        shape=['0..*'],
        description='All quantities that are used by this entry.',
        a_elasticsearch=QuantitySearch(),
    )

    sections = Quantity(
        type=str,
        shape=['*'],
        description='All sections that are present in this entry. This field is deprecated and will be removed.',
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    section_defs = SubSection(
        sub_section=CompatibleSectionDef,
        repeats=True,
        description='All sections that are compatible with the present sections in this entry.',
        a_elasticsearch=Elasticsearch(material_entry_type, nested=True),
    )

    entry_references = SubSection(
        sub_section=EntryArchiveReference,
        repeats=True,
        a_elasticsearch=Elasticsearch(nested=True),
    )

    search_quantities = SubSection(
        sub_section=SearchableQuantity,
        repeats=True,
        a_elasticsearch=Elasticsearch(nested=True),
    )

    readonly = Quantity(
        type=bool,
        default=False,
        description='Indicates if the entry shall not be edited manually',
        a_elasticsearch=Elasticsearch(material_entry_type),
    )

    def apply_archive_metadata(self, archive):
        quantities = set()
        sections_set = set()
        n_quantities = 0

        section_paths = {}
        entry_references = []
        search_quantities = []
        keywords_set = set()

        # Metadata quantities that are human-readable, descriptive information
        # (e.g. names, comments, author references) rather than internal
        # identifiers, hashes, paths, or purely derived/duplicate values. Only
        # these are useful for free text search and therefore included in
        # `text_search_contents`.
        metadata_text_search_includes = {
            'upload_name',
            'description',
            'entry_name',
            'comment',
            'origin',
            'main_author',
            'coauthors',
            'entry_coauthors',
            'reviewers',
            'authors',
            'datasets',
            'domain',
        }
        # Lists longer than this are considered too technical/bulky to be useful
        # for free text search (e.g. `references`) and are skipped. Small, but
        # important lists, like the list of authors, are still indexed.
        metadata_text_search_max_list_size = 20

        def add_text_search_keywords(
            _section, _property_def, max_list_size: int | None = None
        ):
            """
            Extracts text keywords from the given quantity and adds them to
            `keywords_set`, unless too many unique values are already stored. String
            and enum quantities are added directly. Reference quantities that point
            to authors/users or datasets are resolved and their human-readable
            name/DOI is added instead of the (non-descriptive) reference itself.
            """
            if len(keywords_set) >= 10000:
                return

            prop_type = _property_def.type
            if isinstance(prop_type, MEnum | m_str):
                values = _section.m_get(_property_def)
                if not values:
                    return
                if not _property_def.shape:
                    values = [values]
            elif isinstance(
                prop_type, UserReference | AuthorReference | DatasetReference
            ):
                references = _section.m_get(_property_def)
                if not references:
                    return
                if not _property_def.shape:
                    references = [references]

                values = []
                for ref in references:
                    if isinstance(ref, User) or isinstance(ref, Author):
                        values.append(ref.name)
                    elif isinstance(ref, Dataset):
                        values.append(ref.dataset_name)
                        values.append(ref.doi)
                values = [v for v in values if v is not None]

            else:
                return

            if max_list_size is not None and len(values) > max_list_size:
                return

            for val in values:
                keywords_set.add(str(val)[0:500].strip())

        _check_mongo_connection()

        def get_section_path(_section):
            if (_section_path := section_paths.get(_section)) is None:
                if parent := _section.m_parent:
                    _section_path = (
                        _section.m_parent_sub_section.name
                        if (parent_path := get_section_path(parent)) == ''
                        else f'{parent_path}.{_section.m_parent_sub_section.name}'
                    )
                else:
                    _section_path = ''
                section_paths[_section] = _section_path
                quantities.add(_section_path)

            return _section_path

        def create_reference_section(
            url_reference: str, current_def: Definition, quantity_path: str
        ) -> EntryArchiveReference | None:
            try:
                if (parse_result := parse_path(url_reference, self.upload_id)) is None:
                    return None
            except Exception:  # noqa
                return None

            _, upload_id, entry_id_or_mainfile, kind, path = parse_result
            if entry_id_or_mainfile is None:
                return None

            if not upload_id:
                upload_id = archive.metadata.upload_id

            target_name = path
            for name in reversed(path.split('/')):
                if not name.isdigit():
                    target_name = name
                    break

            ref_item = EntryArchiveReference()
            ref_item.target_reference = url_reference
            if kind != 'raw':
                entry_id = entry_id_or_mainfile
            elif upload_id:
                entry_id = utils.generate_entry_id(upload_id, entry_id_or_mainfile)
            else:
                entry_id = None

            ref_item.target_entry_id = entry_id
            ref_item.target_upload_id = upload_id
            ref_item.target_name = target_name
            ref_item.target_path = path
            ref_item.source_name = current_def.name
            ref_item.source_path = quantity_path
            ref_item.source_quantity = current_def.qualified_name()

            from ..processing import Entry

            entry_objects = Entry.objects(entry_id=entry_id)  # type: ignore
            ref_item.target_mainfile = (
                entry_objects.first().mainfile if entry_objects.count() == 1 else None
            )

            return ref_item

        def collect_references(
            current_section: MSection, current_def: Definition, quantity_path: str
        ):
            """
            Receives a definition of a quantity 'current_def' and checks if it is a reference to another entry.
            If yes, add the value to 'ref_pool'.
            """
            if not has_mongo:
                return

            if isinstance(current_def, Quantity):
                # for quantities
                if not isinstance(current_def.type, Reference):
                    return

                try:
                    current_value = current_section.m_get(current_def)
                except Exception:  # noqa
                    return
            else:
                # for subsections
                target_section = current_def.section_def
                if not hasattr(target_section, 'm_proxy_value'):
                    return

                current_value = target_section

            ref_list: list = []
            if hasattr(current_value, 'm_proxy_value'):
                ref_list = [current_value]
            elif isinstance(current_value, list):
                ref_list = [v for v in current_value if hasattr(v, 'm_proxy_value')]

            for ref in ref_list:
                if reference_section := create_reference_section(
                    ref.m_proxy_value, current_def, quantity_path
                ):
                    entry_references.append(reference_section)

        # Determine the schema name to use for searchable quantities
        schema_name = None
        if hasattr(archive, 'data') and archive.data:
            schema_name = archive.data.m_def.qualified_name()

        for sections, property_def, _, location in archive.m_traverse(
            return_all_sections=True
        ):
            section = sections[-1] if sections else None
            sections_set.add(section.m_def)

            if property_def is None:
                continue

            section_path = get_section_path(section)
            quantity_path = (
                f'{section_path}.{property_def.name}'
                if section_path
                else property_def.name
            )
            quantities.add(quantity_path)
            n_quantities += 1

            collect_references(section, property_def, quantity_path)

            if isinstance(property_def, Quantity):
                if section_path.startswith('data'):
                    # From each string dtype, we get a truncated sample to put into
                    # the keywords field, unless we are already storing too many unique values.
                    add_text_search_keywords(section, property_def)
                    if searchable_quantity := create_searchable_quantity(
                        property_def,
                        quantity_path,
                        sections,
                        '.'.join([str(x) for x in location]),
                        schema_name,
                    ):
                        search_quantities.append(searchable_quantity)
                elif (
                    section_path == 'metadata'
                    and property_def.name in metadata_text_search_includes
                ):
                    # Also cover "regular" items directly on archive.metadata (e.g.
                    # author names, dataset names/DOIs, comments, descriptions, ...)
                    # so they can be found through the free text search bar.
                    add_text_search_keywords(
                        section,
                        property_def,
                        max_list_size=metadata_text_search_max_list_size,
                    )

        # We collected entry_references, quantities, and sections before adding these
        # data to the archive itself. We manually add them here.
        if len(entry_references) > 0:
            quantities.update(
                f'metadata.entry_references.{v.name}'
                for v in EntryArchiveReference.m_def.quantities
            )
            quantities.add('metadata.entry_references')
            sections_set.add(EntryArchiveReference.m_def)

        if len(quantities) > 0:
            quantities.add('metadata.quantities')

        if len(sections_set) > 0:
            quantities.add('metadata.sections')
            quantities.add('metadata.section_defs')
            quantities.update(
                f'metadata.section_defs.{v.name}'
                for v in CompatibleSectionDef.m_def.quantities
            )

        compatible_sections: dict[Section, bool] = {
            section: True for section in sections_set
        }
        for section in sections_set:
            for base_section in section.all_base_sections:
                compatible_sections.setdefault(base_section, False)

        self.entry_references.extend(entry_references)
        self.search_quantities.extend(search_quantities)
        self.quantities = sorted(list(quantities))
        self.sections = sorted(section.qualified_name() for section in sections_set)
        self.n_quantities = n_quantities
        self.text_search_contents = list(keywords_set)

        def generate_compatible(_s, used_directly: bool):
            return CompatibleSectionDef(
                definition_qualified_name=_s.qualified_name(),
                definition_id=_s.definition_id,
                used_directly=used_directly,
            )

        self.section_defs = sorted(
            [
                generate_compatible(section, used_directly=used_directly)
                for section, used_directly in compatible_sections.items()
            ],
            key=lambda x: x.definition_qualified_name,
        )


class EntryArchive(ArchiveSection):
    m_def = Section(label='Entry')

    entry_id = Quantity(
        type=str,
        description='The unique primary id for this entry.',
        derived=lambda entry: entry.metadata.entry_id,
    )

    if run_def:
        run = SubSection(sub_section=run_def, repeats=True)
    measurement = SubSection(sub_section=Measurement, repeats=True)

    data = SubSection(sub_section=EntryData)

    workflow = SubSection(
        sub_section=LegacySimulationWorkflow, repeats=True, categories=[FastAccess]
    )
    workflow2 = SubSection(sub_section=Workflow, categories=[FastAccess])

    metadata = SubSection(
        sub_section=EntryMetadata,
        categories=[FastAccess],
        a_elasticsearch=Elasticsearch(),
    )

    processing_logs = Quantity(
        type=Any,
        shape=['0..*'],
        description='The processing logs for this entry as a list of structlog entries.',
    )

    results = SubSection(
        sub_section=Results,
        categories=[FastAccess],
        a_elasticsearch=Elasticsearch(auto_include_subsections=True),
    )

    tabular_tree = SubSection(sub_section=TabularTree, repeats=False)
    definitions = SubSection(sub_section=Package)

    def normalize(self, archive, logger):
        super().normalize(archive, logger)

        if not archive.metadata.entry_type:
            if archive.definitions is not None:
                archive.metadata.entry_type = 'Schema'

        if not archive.metadata.entry_name:
            if archive.definitions is not None:
                archive.metadata.entry_name = archive.definitions.name

        if not archive.metadata.entry_name and archive.metadata.mainfile:
            archive.metadata.entry_name = os.path.basename(archive.metadata.mainfile)


m_package.__init_metainfo__()
