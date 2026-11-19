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

from typing import Literal

from pydantic import BaseModel, Field

# Literal types

# '[DataCite Metadata Schema: contributorType](https://datacite-metadata-schema.readthedocs.io/en/4/appendices/appendix-1/contributorType/)'
ContributorType = Literal[
    'Contact person',
    'Data collector',
    'Data curator',
    'Data manager',
    'Distributor',
    'Editor',
    'Hosting institution',
    'Producer',
    'Project leader',
    'Project manager',
    'Project member',
    'Registration agency',
    'Registration authority',
    'Related person',
    'Researcher',
    'Research group',
    'Rights holder',
    'Sponsor',
    'Supervisor',
    'Work package leader',
    'Other',
]

DateType = Literal[
    'Accepted',
    'Available',
    'Copyrighted',
    'Collected',
    'Coverage',
    'Issued',
    'Submitted',
    'Updated',
    'Valid',
    'Withdrawn',
    'Other',
]

DescriptionType = Literal[
    'Abstract',
    'Methods',
    'SeriesInformation',
    'TableOfContents',
    'TechnicalInfo',
    'Other',
]

FunderIdentifierType = Literal['Crossref Funder ID', 'GRID', 'ISNI', 'ROR', 'Other']

NameType = Literal['Personal', 'Organizational']

# '[DataCite Metadata Schema: relatedIdentifierType](https://datacite-metadata-schema.readthedocs.io/en/4/appendices/appendix-1/relatedIdentifierType/)'
RelatedIdentifierType = Literal[
    'ARK',
    'arXiv',
    'bibcode',
    'CSTR',
    'DOI',
    'EAN13',
    'EISSN',
    'Handle',
    'IGSN',
    'ISBN',
    'ISSN',
    'ISTC',
    'LISSN',
    'LSID',
    'PMID',
    'PURL',
    'RAiD',
    'RAID',
    'RRID',
    'SWHID',
    'UPC',
    'URL',
    'URN',
    'w3id',
]

# '[DataCite Metadata Schema: relationType](https://datacite-metadata-schema.readthedocs.io/en/4/appendices/appendix-1/relationType/)'
RelationType = Literal[
    'IsCitedBy',
    'Cites',
    'IsSupplementTo',
    'IsSupplementedBy',
    'IsContinuedBy',
    'Continues',
    'IsDescribedBy',
    'Describes',
    'HasMetadata',
    'IsMetadataFor',
    'HasVersion',
    'IsVersionOf',
    'IsNewVersionOf',
    'IsPreviousVersionOf',
    'IsPartOf',
    'HasPart',
    'IsPublishedIn',
    'IsReferencedBy',
    'References',
    'IsDocumentedBy',
    'Documents',
    'IsCompiledBy',
    'Compiles',
    'IsVariantFormOf',
    'IsOriginalFormOf',
    'IsIdenticalTo',
    'IsReviewedBy',
    'Reviews',
    'IsDerivedFrom',
    'IsSourceOf',
    'IsRequiredBy',
    'Requires',
    'IsObsoletedBy',
    'Obsoletes',
    'IsCollectedBy',
    'Collects',
    'IsTranslationOf',
    'HasTranslation',
    'Other',
]

# '[DataCite Metadata Schema: resourceTypeGeneral](https://datacite-metadata-schema.readthedocs.io/en/4/appendices/appendix-1/resourceTypeGeneral/)'
ResourceTypeGeneral = Literal[
    'Audiovisual',
    'Award',
    'Book',
    'BookChapter',
    'Collection',
    'ComputationalNotebook',
    'ConferenceProceeding',
    'DataPaper',
    'Dataset',
    'Dissertation',
    'Event',
    'Image',
    'Instrument',
    'InteractiveResource',
    'Journal',
    'JournalArticle',
    'Model',
    'OutputManagementPlan',
    'PeerReview',
    'PhysicalObject',
    'Poster',
    'Preprint',
    'Presentation',
    'Project',
    'Report',
    'Service',
    'Software',
    'Sound',
    'Standard',
    'StudyRegistration',
    'Text',
    'Workflow',
    'Other',
]

Source = Literal['mds', 'api', 'fabricaForm', 'fabrica', 'ez']
State = Literal['findable', 'registered', 'draft']
TitleType = Literal['AlternativeTitle', 'Subtitle', 'TranslatedTitle', 'Other']


# Component models


class NameIdentifier(BaseModel):
    nameIdentifier: str
    nameIdentifierScheme: str
    schemeUri: str | None = None


class Affiliation(BaseModel):
    afffiliationIdentifier: str | None = None
    affiliationIdentifierScheme: str | None = None
    name: str | None = None
    schemeUri: str | None = None


class Contributor(BaseModel):  # (7) # used in DoiPropertiesMetadata
    nameType: NameType | None = None
    nameIdentifiers: list[NameIdentifier] | None = None
    name: str | None = None
    givenName: str | None = None
    familyName: str | None = None
    affiliation: list[Affiliation] | None = Field(
        None,
        description='Set `affiliation=true` to see additional affiliation information such as the affiliation identifier.',
    )
    contributorType: ContributorType | None = None
    lang: str | None = None


class Creator(BaseModel):  # (2)
    nameType: NameType | None = None
    nameIdentifiers: list[NameIdentifier] | None = None
    name: str | None = None  # (2.1)
    givenName: str | None = None
    familyName: str | None = None
    affiliation: list[Affiliation] | None = Field(
        None,
        description='Set `affiliation=true` to see additional affiliation information such as the affiliation identifier.',
    )
    lang: str | None = None


class Agent(BaseModel):
    name: str | None = None
    nameIdentifiers: list[NameIdentifier] | None = None
    affiliation: list[Affiliation] | None = None
    lang: str | None = None


class Title(BaseModel):
    title: str | None = None
    titleType: TitleType | None = None
    lang: str | None = None


class Publisher(BaseModel):
    name: str | None = None
    publisherIdentifier: str | None = None
    publisherIdentifierScheme: str | None = None
    schemeUri: str | None = None
    lang: str | None = None


class TypesBase(BaseModel):
    resourceTypeGeneral: ResourceTypeGeneral | None = None
    resourceType: str | None = Field(
        None,
        description='[DataCite Metadata Schema: ResourceType](https://datacite-metadata-schema.readthedocs.io/en/4/properties/resourcetype/)',
    )


class TypesRead(TypesBase):
    schemaOrg: str | None = Field(
        None,
        json_schema_extra={'readOnly': True},
    )
    bibtext: str | None = Field(
        None,
        json_schema_extra={'readOnly': True},
    )
    citeproc: str | None = Field(
        None,
        json_schema_extra={'readOnly': True},
    )
    ris: str | None = Field(
        None,
        json_schema_extra={'readOnly': True},
    )


class MetaStat(BaseModel):
    id: str | None = None
    type: str | None = None
    count: int | None = None


class Links(BaseModel):
    self: str | None = None
    next: str | None = None


class IDAndType(BaseModel):
    id: str | None = None
    type: str | None = None


class IDAndTypeSingleContainer(BaseModel):
    data: IDAndType | None = None


class IDAndTypeMultiContainer(BaseModel):
    data: list[IDAndType] | None = None


Client = IDAndTypeSingleContainer
Provider = IDAndTypeSingleContainer
Media = IDAndTypeSingleContainer
References = IDAndTypeMultiContainer
Citations = IDAndTypeMultiContainer
Parts = IDAndTypeMultiContainer
PartOf = IDAndTypeMultiContainer
Versions = IDAndTypeMultiContainer
VersionOf = IDAndTypeMultiContainer


class Relationships(BaseModel):
    client: Client | None = None
    provider: Provider | None = None
    media: Media | None = None
    references: References | None = None
    citations: Citations | None = None
    parts: Parts | None = None
    partOf: PartOf | None = None
    versions: Versions | None = None
    versionOf: VersionOf | None = None


class Container(BaseModel):  # used in DoiPropertiesMetadata, read-only
    type: str | None = None
    identifier: str | None = None
    identifierType: str | None = None
    title: str | None = None
    volume: str | None = None
    issue: str | None = None
    firstPage: str | None = None
    lastPage: str | None = None


class Subject(BaseModel):  # used in DoiPropertiesMetadata
    subject: str | None = Field(None, examples=['Chemical engineering'])
    subjectScheme: str | None = Field(
        None, examples=['Fields of Science and Technology (FOS)']
    )
    schemeUri: str | None = Field(
        None,
        examples=['https://web-archive.oecd.org/2012-06-15/138575-38235147.pdf'],
    )
    valueUri: str | None = None
    lang: str | None = None
    classificationCode: str | None = Field(None, examples=['2.4'])


class Date(BaseModel):  # used in DoiPropertiesMetadata
    date: str | None = None
    dateType: DateType | None = None
    dateInformation: str | None = None


class RelatedIdentifier(BaseModel):  # used in DoiPropertiesMetadata
    relatedIdentifier: str | None = None
    relatedIdentifierType: RelatedIdentifierType | None = None
    relationType: RelationType | None = None
    resourceTypeGeneral: ResourceTypeGeneral | None = None
    relatedMetadataScheme: str | None = None
    schemeUri: str | None = None
    schemeType: str | None = None
    relationTypeInformation: str | None = None


class Rights(BaseModel):  # used in DoiPropertiesMetadata
    rights: str | None = None
    rightsUri: str | None = None
    schemeUri: str | None = None
    rightsIdentifier: str | None = None
    rightsIdentifierScheme: str | None = None
    lang: str | None = None


class Description(BaseModel):  # used in DoiPropertiesMetadata
    description: str | None = None
    descriptionType: DescriptionType | None = None
    lang: str | None = None


class GeoLocationBox(BaseModel):
    westBoundLongitude: float | None = None
    eastBoundLongitude: float | None = None
    southBoundLatitude: float | None = None
    northBoundLatitude: float | None = None


class GeoLocationPoint(BaseModel):
    pointLongitude: float | None = None
    pointLatitude: float | None = None


class GeoLocation(BaseModel):  # used in DoiPropertiesMetadata
    geoLocationBox: object | None = None
    geoLocationPlace: str | None = None
    geoLocationPoint: GeoLocationPoint | None = None
    geoLocationPolygon: object | None = None


class FundingReference(BaseModel):  # used in DoiPropertiesMetadata
    funderName: str | None = None
    funderIdentifier: str | None = None
    funderIdentifierType: FunderIdentifierType | None = None
    awardNumber: str | None = None
    awardTitle: str | None = None
    awardUri: str | None = None


class Identifier(BaseModel):
    identifier: str | None = None
    identifierType: str | None = None


class AlternateIdentifier(BaseModel):
    alternateIdentifier: str | None = None
    alternateIdentifierType: str | None = None


class LandingPage(BaseModel):  # used in Attributes5, read-only
    checked: str | None = None
    url: str | None = None
    contentType: str | None = None
    error: str | None = None
    redirectCount: int | None = None
    redirectUrls: list[str] | None = None
    downloadLatency: float | None = None  # number
    hasSchemaOrg: bool | None = None
    schemaOrgid: str | None = None
    dcIdentifier: str | None = None
    citationDoi: str | None = None
    bodyHasPid: bool | None = None


class OverTime(BaseModel):
    yearMonth: str | None = None
    total: int | None = None


class RelatedItemContributor(BaseModel):
    name: str | None = None
    givenName: str | None = None
    familyName: str | None = None
    nameType: NameType | None = None
    contributorType: ContributorType | None = None


class RelatedItem(BaseModel):  # used in DoiPropertiesMetadata
    relatedItemType: object
    # TODO: Rename to relationType with a compatibility alias in a separate schema fix.
    releationType: object  # typos: ignore
    relatedItemIdentifier: object
    relationTypeInformation: object
    creators: list[Creator] | None = None
    titles: list[Title] | None = None
    volume: str | None = None
    issue: str | None = None
    number: str | None = None
    numberType: str | None = None
    firstPage: str | None = None
    lastPage: str | None = None
    publisher: str | None = None
    publicationYear: int | None = None
    edition: str | None = None
    contributors: list[RelatedItemContributor] | None = None


# Request / Response Base


class DoiPropertiesCoreBase(BaseModel):
    # Attributes1
    doi: str | None = Field(
        None,
        description='The full DOI name.',
        examples=['10.2345/nomad.6789-wxyz'],
    )  # (1) "identifier"
    prefix: str | None = Field(
        None, description='The DOI prefix.'
    )  # (1*) alternative for .doi to autogenerate suffix
    identifiers: list[Identifier] | None = Field(
        None,
        description='Equivalent to the [AlternateIdentifier](https://datacite-metadata-schema.readthedocs.io/en/4/properties/alternateidentifier/) property in the DataCite Metadata Schema. For more information, see [What is the "identifiers" attribute in the REST API?](https://support.datacite.org/docs/what-is-the-identifiers-attribute-in-the-rest-api)',
    )
    alternateIdentifiers: list[AlternateIdentifier] = Field(
        None,
        description='[DataCite Metadata Schema: AlternateIdentifier](https://datacite-metadata-schema.readthedocs.io/en/4/properties/alternateidentifier/)',
    )  # (11) alternateIdentifiers [Optional]
    # Attributes3
    xml: str | None = Field(
        None,
        description='DataCite Metadata Schema XML encoded in Base64 format.',
    )


class DoiPropertiesCoreRead(DoiPropertiesCoreBase):
    # part of Attributes1, read-only
    suffix: str | None = Field(
        None,
        description='The DOI suffix.',
        json_schema_extra={'readOnly': True},
    )


class DoiPropertiesCoreWrite(DoiPropertiesCoreBase):
    # part of Attributes1, write-only
    event: Literal['publish', 'register', 'hide'] | None = Field(
        None,
        description="""Can be set to trigger a [DOI state change](https://support.datacite.org/docs/updating-metadata-with-the-rest-api#changing-the-doi-state). When not set, a [Draft record](https://support.datacite.org/docs/doi-states#draft-record) is created.
            * `publish` - Create a DOI in [Findable state](https://support.datacite.org/docs/doi-states#findable-doi-name) (or change an existing Draft record/Registered DOI to Findable state).
            * `register` - Create a DOI in [Registered state](https://support.datacite.org/docs/doi-states#registered-doi-name) (or change an existing Draft record to Registered state).
            * `hide` - Change a DOI from Findable to Registered state.""",
        json_schema_extra={'writeOnly': True},
    )


class DoiPropertiesMetadataBase(BaseModel):
    # Attributes2 (DoiPropertiesMetadata)
    creators: list[Creator] | None = Field(
        None,
        description='[DataCite Metadata Schema: Creator](https://datacite-metadata-schema.readthedocs.io/en/4/properties/creator/)',
    )  # (2) creators [Mandatory]
    titles: list[Title] | None = Field(
        None,
        description='[DataCite Metadata Schema: Title](https://datacite-metadata-schema.readthedocs.io/en/4/properties/title/)',
    )  # (3) titles [Mandatory]
    publisher: Publisher | str | None = Field(
        None,
        description='[DataCite Metadata Schema: Publisher](https://datacite-metadata-schema.readthedocs.io/en/4/properties/publisher/) Set `publisher=true` to see additional publisher information such as the publisher identifier.',
    )  # (4) publisher [Mandatory]
    container: Container | None = Field(None, json_schema_extra={'readOnly': True})
    publicationYear: int | None = Field(
        None,
        description='[DataCite Metadata Schema: PublicationYear](https://datacite-metadata-schema.readthedocs.io/en/4/properties/publicationyear/)',
    )  # (5) publicationYear [Mandatory]
    subjects: list[Subject] | None = Field(
        None,
        description='[DataCite Metadata Schema: Subject](https://datacite-metadata-schema.readthedocs.io/en/4/properties/subject/)',
    )  # (6) subjects [Recommended]
    contributors: list[Contributor] | None = Field(
        None,
        description='[DataCite Metadata Schema: Contributor](https://datacite-metadata-schema.readthedocs.io/en/4/properties/contributor/)',
    )  # (7) contributors [Recommended]
    dates: list[Date] | None = Field(
        None,
        description='[DataCite Metadata Schema: Date](https://datacite-metadata-schema.readthedocs.io/en/4/properties/date/)',
    )  # (8) dates [Recommended]
    language: str | None = Field(
        None,
        description='[DataCite Metadata Schema: Language](https://datacite-metadata-schema.readthedocs.io/en/4/properties/language/)',
    )  # (9) language [Optional]
    relatedIdentifiers: list[RelatedIdentifier] | None = Field(
        None,
        description='[DataCite Metadata Schema: RelatedIdentifier](https://datacite-metadata-schema.readthedocs.io/en/4/properties/relatedidentifier/)',
    )  # (12) relatedIdentifiers [Recommended]
    sizes: list[str] | None = Field(
        None,
        description='[DataCite Metadata Schema: Size](https://datacite-metadata-schema.readthedocs.io/en/4/properties/size/)',
    )  # (13) sizes [Optional]
    formats: list[str] | None = Field(
        None,
        description='[DataCite Metadata Schema: Format](https://datacite-metadata-schema.readthedocs.io/en/4/properties/format/)',
    )  # (14) formats [Optional]
    version: str | None = Field(
        None,
        description='[DataCite Metadata Schema: Version](https://datacite-metadata-schema.readthedocs.io/en/4/properties/version/)',
    )  # (15) version [Optional]
    rightsList: list[Rights] | None = Field(
        None,
        description='[DataCite Metadata Schema: Rights](https://datacite-metadata-schema.readthedocs.io/en/4/properties/rights/)',
    )  # (16) rightsList [Optional]
    descriptions: list[Description] | None = Field(
        None,
        description='[DataCite Metadata Schema: Description](https://datacite-metadata-schema.readthedocs.io/en/4/properties/description/)',
    )  # (17) descriptions [Recommended]
    geoLocations: list[GeoLocation] | None = Field(
        None,
        description='[DataCite Metadata Schema: GeoLocation](https://datacite-metadata-schema.readthedocs.io/en/4/properties/geolocation/)',
    )  # (18) geoLocations [Recommended]
    fundingReferences: list[FundingReference] | None = Field(
        None,
        description='[DataCite Metadata Schema: FundingReference](https://datacite-metadata-schema.readthedocs.io/en/4/properties/fundingreference/)',
    )  # (19) fundingReferences [Optional]
    relatedItems: list[RelatedItem] | None = Field(
        None,
        description='[DataCite Metadata Schema: RelatedItem](https://datacite-metadata-schema.readthedocs.io/en/4/properties/relateditem/)',
    )  # (20) relatedItems [Optional]


class DoiPropertiesMetadataWrite(DoiPropertiesMetadataBase):
    types: TypesBase | None = None  # (10) types [Mandatory]


class DoiPropertiesMetadataRead(DoiPropertiesMetadataBase):
    types: TypesRead | None = None  # (10) types [Mandatory], some read-only


class DoiPropertiesOtherBase(BaseModel):
    url: str | None = Field(
        None, description='The landing page URL of the DOI.'
    )  # (0) not a metadatum
    contentUrl: list[str] | None = Field(
        None, description='An array of content URLs associated with the DOI.'
    )
    schemaVersion: str | None = Field(
        None,
        description='The DataCite Metadata Schema version of the stored DOI metadata represented as a URL. When creating or updating a DOI, `schemaVersion` is not necessary unless modifying the DataCite Metadata Schema version.',
    )


class DoiPropertiesOtherRead(DoiPropertiesOtherBase):
    # Attributes4 (DoiPropertiesOther) ; read-only
    metadataVersion: int | float | None = Field(
        None,
        description='The version of the stored DataCite metadata, incremented once per update.',
        json_schema_extra={'readOnly': True},
    )  # number
    source: Source | None = Field(
        None,
        description='The system used to create the DOI.',
        json_schema_extra={'readOnly': True},
    )
    isActive: bool | None = Field(
        None,
        description='"true" if the DOI is in [Findable state](https://support.datacite.org/docs/doi-states#findable-doi-name). Otherwise, "false".',
        json_schema_extra={'readOnly': True},
    )
    state: State | None = Field(
        None,
        description='The [state of the DOI](https://support.datacite.org/docs/doi-states).',
        json_schema_extra={'readOnly': True},
    )
    reason: str | None = Field(
        None,
        description='Legacy attribute for EZID compatibility.',
        json_schema_extra={'readOnly': True},
    )


class DoiPropertiesStatsRead(BaseModel):
    # Attributes5; read-only
    viewCount: int | None = Field(
        None,
        description='Total views, pulled from Event Data.',
        json_schema_extra={'readOnly': True},
    )
    viewsOverTime: list[OverTime] | None = Field(
        None, json_schema_extra={'readOnly': True}
    )
    downloadCount: int | None = Field(
        None,
        description='Total downloads, pulled from Event Data.',
        json_schema_extra={'readOnly': True},
    )
    downloadsOverTime: list[OverTime] | None = Field(
        None, json_schema_extra={'readOnly': True}
    )
    referenceCount: int | None = Field(
        None,
        description='Total references, pulled from Event Data.',
        json_schema_extra={'readOnly': True},
    )
    citationCount: int | None = Field(
        None,
        description='Total citations, pulled from Event Data.',
        json_schema_extra={'readOnly': True},
    )
    citationsOverTime: list[OverTime] | None = Field(
        None, json_schema_extra={'readOnly': True}
    )
    partCount: int | None = Field(
        None,
        description='Total number of parts, pulled from Event Data.',
        json_schema_extra={'readOnly': True},
    )
    partOfCount: int | None = Field(
        None,
        description='Total number of parents, pulled from Event Data.',
        json_schema_extra={'readOnly': True},
    )
    versionCount: int | None = Field(
        None,
        description='Total number of versions, pulled from Event Data.',
        json_schema_extra={'readOnly': True},
    )
    versionOfCount: int | None = Field(
        None,
        description='Total number to which this DOI is a version, pulled from Event Data.',
        json_schema_extra={'readOnly': True},
    )
    landingPage: LandingPage | None = Field(
        None,
        description='Data describing the landing page, used by link checking.',
        json_schema_extra={'readOnly': True},
    )


class DoiPropertiesDatesRead(BaseModel):
    # Attributes6 (DoiPropertiesDates) ; read-only
    created: str | None = Field(
        None,
        description='The date the DOI record was created in the DataCite system.',
        json_schema_extra={'readOnly': True},
    )
    registered: str | None = Field(
        None,
        description='The date the DOI was registered in the global handle server.',
        json_schema_extra={'readOnly': True},
    )
    updated: str | None = Field(
        None,
        description='The date the DOI was last updated.',
        json_schema_extra={'readOnly': True},
    )


class DoiDataBase(BaseModel):
    type: Literal['dois'] = 'dois'


# Request models


class DoiRequestAttributes(
    DoiPropertiesCoreWrite,  # one write-only
    DoiPropertiesMetadataWrite,  # no write-only
    DoiPropertiesOtherBase,
):
    pass


class DoiRequestData(DoiDataBase):
    attributes: DoiRequestAttributes


class DoiRequestPayload(BaseModel):
    data: DoiRequestData

    @staticmethod
    def from_attributes(attributes: DoiRequestAttributes) -> 'DoiRequestPayload':
        data = DoiRequestData(attributes=attributes)
        return DoiRequestPayload(data=data)


# Response models


class DoiResponseAttributes(
    DoiPropertiesCoreRead,  # one read-only
    DoiPropertiesMetadataRead,  # some read-only
    DoiPropertiesOtherRead,  # some read-only
    DoiPropertiesStatsRead,  # all read-only
    DoiPropertiesDatesRead,  # all read-only
):
    pass


class DoiResponseData(DoiDataBase):
    id: str | None = None  # read-only
    attributes: DoiResponseAttributes | None = None
    relationships: Relationships | None = None


class DoiResponseMeta(BaseModel):
    total: int | None = None
    totalPages: int | None = None
    page: int | None = None
    states: list[MetaStat] | None = None
    resourceTypes: list[MetaStat] | None = None
    created: list[MetaStat] | None = None
    published: list[MetaStat] | None = None
    registered: list[MetaStat] | None = None
    providers: list[MetaStat] | None = None
    clients: list[MetaStat] | None = None
    affiliations: list[MetaStat] | None = None
    prefixes: list[MetaStat] | None = None
    certificates: list[MetaStat] | None = None
    licenses: list[MetaStat] | None = None
    schemaVersions: list[MetaStat] | None = None
    linkChecksStatus: list[MetaStat] | None = None
    subjects: list[MetaStat] | None = None
    fieldsOfScience: list[MetaStat] | None = None
    citations: list[MetaStat] | None = None
    views: list[MetaStat] | None = None
    downloads: list[MetaStat] | None = None


class DoiMultiResponsePayload(BaseModel):
    data: list[DoiResponseData]
    meta: DoiResponseMeta | None = None
    links: Links | None = None


class DoiSingleResponsePayload(BaseModel):
    data: DoiResponseData
