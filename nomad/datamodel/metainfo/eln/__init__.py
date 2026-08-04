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
import importlib
import re
import warnings
from typing import TYPE_CHECKING, Any

import numpy as np
from unidecode import unidecode

from nomad.datamodel.metainfo.plot import PlotSection

from nomad import utils
from nomad.common import now
from nomad.datamodel.data import (
    ArchiveSection,
    BasicElnCategory,
    EntryData,
    AuthorReference,
)
from nomad.datamodel.metainfo.annotations import (
    ELNAnnotation,
)
from nomad.datamodel.metainfo.basesections.v1 import (
    Activity,
    Analysis,
    AnalysisResult,
    BaseSection,
    Collection,
    CompositeSystem,
    ElementalComposition,
    Entity,
    Experiment,
    HDF5Normalizer,
    Instrument,
    Measurement,
    MeasurementResult,
    Process,
    PureSubstance,
    PublicationReference,
    ReadableIdentifiers,
    SynthesisMethod,
    System,
)
from nomad.datamodel.metainfo.basesections.v1 import (
    CompositeSystem as Ensemble,  # For legacy support
)
from nomad.datamodel.metainfo.basesections.v1 import (
    SystemComponent as Component,
)
from nomad.datamodel.metainfo.common import ProvenanceTracker
from nomad.datamodel.results import (
    ELN,
    BandGap,
    BandGapDeprecated,
    BandStructureElectronic,
    ElectronicProperties,
    Material,
    OptoelectronicProperties,
    Properties,
    Results,
    SolarCell,
)
from nomad.datamodel.results import ElementalComposition as ResultsElementalComposition
from nomad.metainfo import Datetime, Package, Quantity, Reference, Section, SubSection
from nomad.metainfo.metainfo import Category, MCategory, MEnum, MProxy, MSection
from nomad.units import ureg

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

    from nomad.datamodel import EntryArchive


class _LazyEQEAnalyzer:
    """Lazily import expensive EQEAnalyzer."""

    def __new__(cls, *args, **kwargs):
        warnings.warn(
            "Importing 'EQEAnalyzer' from this module is deprecated. "
            "Please import it directly from 'nomad.datamodel.metainfo.eln.eqe_parser'.",
            DeprecationWarning,
            stacklevel=2,
        )

        EQEAnalyzer = importlib.import_module(
            'nomad.datamodel.metainfo.eln.eqe_parser'
        ).EQEAnalyzer
        return EQEAnalyzer(*args, **kwargs)


EQEAnalyzer = _LazyEQEAnalyzer

if TYPE_CHECKING:
    from structlog.stdlib import (
        BoundLogger,
    )


def add_band_gap(archive, band_gap):
    """Adds a band gap value (in eV) with the additional section structure for solar
    cell data.eV=
    """
    if band_gap is not None:
        bg = BandGapDeprecated(value=np.float64(band_gap) * ureg('eV'))
        band_gap = BandGap(
            value=np.float64(band_gap) * ureg('eV'),
            provenance=ProvenanceTracker(label='solar_cell_database'),
        )  # TODO: check label
        band_structure = BandStructureElectronic(
            band_gap=[bg]
        )  # TODO: to be removed after reparsing
        electronic = ElectronicProperties(
            band_structure_electronic=[band_structure], band_gap=[band_gap]
        )
        archive.results.properties.electronic = electronic


def add_solar_cell(archive):
    """Adds metainfo structure for solar cell data."""
    if not archive.results:
        archive.results = Results()
    if not archive.results.properties:
        archive.results.properties = Properties()
    if not archive.results.properties.optoelectronic:
        archive.results.properties.optoelectronic = OptoelectronicProperties()
    if not archive.results.properties.optoelectronic.solar_cell:
        archive.results.properties.optoelectronic.solar_cell = SolarCell()


m_package = Package(name='eln')


class User(MSection):
    user = Quantity(
        type=AuthorReference,
        description='The corresponding user for the activity.',
        a_eln=dict(component='AuthorEditQuantity'),
    )


class ElnBaseSection(ArchiveSection):
    """
    A generic abstract base section for ELNs that provides a few commonly used properties.

    If you inherit from this section, but do not need some quantities, list those
    quantities in the `eln.hide` annotation of your inheriting section definition.

    Besides predefining some quantities, these base sections will add some metadata
    to NOMAD's search. A particular example are `tags`, if you define a string
    or enum quantity in your sections named `tags`, its values will be searchable.
    """

    name = Quantity(
        type=str,
        description='A short human readable and descriptive name.',
        a_eln=dict(component='StringEditQuantity', label='short name'),
    )

    datetime = Quantity(
        type=Datetime,
        description='The date and time associated with this section.',
        a_eln=dict(component='DateTimeEditQuantity'),
    )

    lab_id = Quantity(
        type=str,
        description="""An ID string that is unique at least for the lab that produced this
            data.""",
        a_eln=dict(component='StringEditQuantity', label='ID'),
    )

    description = Quantity(
        type=str,
        description='Any information that cannot be captured in the other fields.',
        a_eln=dict(component='RichTextEditQuantity'),
    )

    def normalize(self, archive, logger):
        super().normalize(archive, logger)

        if isinstance(self, EntryData):
            if archive.data == self and self.name:
                archive.metadata.entry_name = self.name
            elif self.name is None:
                self.name = archive.metadata.entry_name.split('.')[0].replace('_', ' ')
            EntryData.normalize(self, archive, logger)

        if not archive.results:
            archive.results = Results(eln=ELN())
        if not archive.results.eln:
            archive.results.eln = ELN()

        if self.datetime is None:
            self.datetime = now()

        if self.lab_id:
            if archive.results.eln.lab_ids is None:
                archive.results.eln.lab_ids = []
            if self.lab_id not in archive.results.eln.lab_ids:
                archive.results.eln.lab_ids.append(self.lab_id)
        else:
            if archive.results.eln.lab_ids and len(archive.results.eln.lab_ids) > 0:
                self.lab_id = archive.results.eln.lab_ids[0]

        if getattr(self, 'name'):
            if archive.results.eln.names is None:
                archive.results.eln.names = []
            archive.results.eln.names.append(self.name)

        if getattr(self, 'description'):
            if archive.results.eln.descriptions is None:
                archive.results.eln.descriptions = []
            archive.results.eln.descriptions.append(self.description)

        if getattr(self, 'tags', None):
            if archive.results.eln.tags is None:
                archive.results.eln.tags = []
            tags = self.tags
            if isinstance(tags, list):
                archive.results.eln.tags.extend(tags)
            else:
                archive.results.eln.tags.append(tags)

        if not archive.results.eln.sections:
            archive.results.eln.sections = []
        archive.results.eln.sections.append(self.m_def.name)


class BasicEln(ElnBaseSection, EntryData):
    """The most basic ELN to instantiate."""

    m_def = Section(
        categories=[BasicElnCategory], a_eln=dict(lane_width='600px'), label='Basic ELN'
    )

    tags = Quantity(
        type=str,
        shape=['*'],
        description='Add a tag that can be used for search.',
        a_eln=dict(component='StringEditQuantity'),
    )


class ELNProcess(Process, EntryData):
    """
    A basic electronic lab notebook for a material processing activity.
    """

    m_def = Section(
        categories=[BasicElnCategory],
        a_eln=ELNAnnotation(
            lane_width='600px',
        ),
        a_template=dict(
            process_identifiers=dict(),
        ),
        label='Material Processing ELN',
    )
    tags = Quantity(
        type=str,
        shape=['*'],
        description='Add a tag that can be used for search.',
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
        ),
        a_template=dict(
            process_identifiers=dict(),
        ),
    )
    process_identifiers = SubSection(
        section_def=ReadableIdentifiers,
    )


class BasicMeasurementResult(MeasurementResult):
    """
    A basic section for describing the result of a measurement.
    """

    m_def = Section(
        label='Measurement Result',
    )
    result = Quantity(
        type=str,
        a_eln=ELNAnnotation(
            component='RichTextEditQuantity',
        ),
    )


class ELNMeasurement(Measurement, EntryData):
    """
    A basic electronic lab notebook for a measurement activity.
    """

    m_def = Section(
        categories=[BasicElnCategory],
        a_eln=ELNAnnotation(
            lane_width='600px',
        ),
        a_template=dict(
            measurement_identifiers=dict(),
        ),
        label='Measurement ELN',
    )
    tags = Quantity(
        type=str,
        shape=['*'],
        description='Add a tag that can be used for search.',
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
        ),
    )
    measurement_identifiers = SubSection(
        section_def=ReadableIdentifiers,
    )
    results = Measurement.results.m_copy()
    results.section_def = BasicMeasurementResult


class BasicAnalysisResult(AnalysisResult):
    """
    A basic section for describing the result of an analysis.
    """

    m_def = Section(
        label='Analysis Result',
    )
    result = Quantity(
        type=str,
        a_eln=ELNAnnotation(
            component='RichTextEditQuantity',
        ),
    )


class ELNAnalysis(Analysis, EntryData):
    """
    A basic electronic lab notebook for an analysis activity.
    """

    m_def = Section(
        categories=[BasicElnCategory],
        a_eln=ELNAnnotation(
            lane_width='600px',
        ),
        a_template=dict(
            analysis_identifiers=dict(),
        ),
        label='Analysis ELN',
    )
    tags = Quantity(
        type=str,
        shape=['*'],
        description='Add a tag that can be used for search.',
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
        ),
    )
    analysis_identifiers = SubSection(
        section_def=ReadableIdentifiers,
    )
    outputs = Analysis.inputs.m_copy()
    outputs.section_def = BasicAnalysisResult


class ELNSample(CompositeSystem, EntryData):
    """
    A basic electronic lab notebook for a generic sample.
    """

    m_def = Section(
        categories=[BasicElnCategory],
        a_eln=ELNAnnotation(
            lane_width='600px',
        ),
        a_template=dict(
            sample_identifiers=dict(),
        ),
        label='Generic Sample ELN',
    )
    tags = Quantity(
        type=str,
        shape=['*'],
        description='Add a tag that can be used for search.',
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
        ),
    )
    sample_identifiers = SubSection(
        section_def=ReadableIdentifiers,
    )


class ELNSubstance(PureSubstance, EntryData):
    """
    A basic electronic lab notebook for a generic sample.
    """

    m_def = Section(
        categories=[BasicElnCategory],
        a_eln=ELNAnnotation(
            lane_width='600px',
        ),
        a_template=dict(
            substance_identifiers=dict(),
            substance=dict(
                m_def='nomad.datamodel.metainfo.basesections.v1.PubChemPureSubstanceSection'
            ),
        ),
        label='Substance ELN',
    )
    tags = Quantity(
        type=str,
        shape=['*'],
        description='Add a tag that can be used for search.',
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
        ),
    )
    substance_identifiers = SubSection(
        section_def=ReadableIdentifiers,
    )


class ELNInstrument(Instrument, EntryData):
    """
    A basic electronic lab notebook for a generic instrument.
    """

    m_def = Section(
        categories=[BasicElnCategory],
        a_eln=ELNAnnotation(
            lane_width='600px',
        ),
        a_template=dict(
            instrument_identifiers=dict(),
        ),
        label='Instrument ELN',
    )
    tags = Quantity(
        type=str,
        shape=['*'],
        description='Add a tag that can be used for search.',
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
        ),
    )
    instrument_identifiers = SubSection(
        section_def=ReadableIdentifiers,
    )


class ELNCollection(Collection, EntryData):
    """
    A basic electronic lab notebook for a collection of entities.
    """

    m_def = Section(
        categories=[BasicElnCategory],
        a_eln=ELNAnnotation(
            lane_width='600px',
        ),
        a_template=dict(
            collection_identifiers=dict(),
        ),
        label='Collection ELN',
    )
    tags = Quantity(
        type=str,
        shape=['*'],
        description='Add a tag that can be used for search.',
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
        ),
    )
    collection_identifiers = SubSection(
        section_def=ReadableIdentifiers,
    )


class ELNExperiment(Experiment, EntryData):
    """
    A basic electronic lab notebook for a collection of activities.
    """

    m_def = Section(
        categories=[BasicElnCategory],
        a_eln=ELNAnnotation(
            lane_width='600px',
        ),
        a_template=dict(
            experiment_identifiers=dict(),
        ),
        label='Experiment ELN',
    )
    tags = Quantity(
        type=str,
        shape=['*'],
        description='Add a tag that can be used for search.',
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
        ),
    )
    experiment_identifiers = SubSection(
        section_def=ReadableIdentifiers,
    )


class AnnotatedFile(ArchiveSection):
    """
    A Section for storing an annotated file
    """

    m_def = Section(
        a_eln=dict(overview=True),
        label_quantity='file',
    )

    file = Quantity(
        type=str,
        description='Single workflow files.',
        a_browser=dict(adaptor='RawFileAdaptor'),
        a_eln=ELNAnnotation(
            component='FileEditQuantity',
        ),
    )
    description = Quantity(
        type=str,
        description='A quantity for storing the annotation.',
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
        ),
    )


class ElnFileManager(ElnBaseSection, EntryData):
    """
    Define a section for storing files
    """

    m_def = Section(categories=[BasicElnCategory])

    Files = SubSection(
        section_def=AnnotatedFile,
        repeats=True,
    )


class ElnParserSection(BaseSection, EntryData):
    """
    An electronic lab notebook section that is used to annotate data from a raw file.
    """

    data_file = Quantity(type=str)

    def data_file_name(self, archive: 'EntryArchive') -> str:
        """
        Returns the name of the data file associated with this section.
        Args:
            archive (EntryArchive): The archive containing the section.
        Returns:
            str: The name of the data file.
        """
        with archive.m_context.raw_file(self.data_file) as file:
            return file.name


class ElnParserRawFile(EntryData):
    """
    An entry data section that is used to index a raw file with an associated ELN entry.
    This section (and any of its subsections) should not contain ELN annotations.
    If you need ELN annotations, you should create an `ElnParserSection` and reference
    this section in the `eln` quantity of the `ElnParserRawFile`.
    """

    eln = Quantity(
        type=ElnParserSection,
        description='An ELN section that is used to annotate the raw file.',
    )
    data_type = Quantity(
        type=str,
        description="""
        The type of the data contained in the raw file, e.g. X-Ray Diffraction.
        """,
    )
    file_ending = Quantity(
        type=str,
        description='The file ending of the raw file, e.g. .txt, .csv, .hdf5.',
    )
    file_size = Quantity(
        type=np.float64,
        description='The size of the raw file in bytes.',
        unit='bytes',
    )


# Legacy sections:
class SampleID(ArchiveSection):
    """
    A base section that can be used for sample IDs.
    If the `sample_owner`, `sample_short_name`, `institute`, and `creation_datetime`
    quantities are provided, the sample_id will be automatically created as a combination
    of these four quantities.
    """

    institute = Quantity(
        type=str,
        description='Alias/short name of the home institute of the owner, i.e. *HZB*.',
        a_eln=dict(component='StringEditQuantity'),
    )
    sample_owner = Quantity(
        type=str,
        shape=[],
        description='Name or alias of the process operator, e.g. jmp',
        a_eln=dict(component='StringEditQuantity'),
    )
    creation_datetime = Quantity(
        type=Datetime,
        description='Creation date of the sample.',
        a_eln=dict(component='DateTimeEditQuantity'),
    )
    sample_short_name = Quantity(
        type=str,
        description='''A short name of the sample (the identifier scribed on the smaple,
         or in the sample container), e.g. 4001-8, YAG-2-34.
         This is to be managed and decided internally by the labs,
         although we recomend to avoid the following characters on it: "_", "/", "\" and "."''',
        a_eln=dict(component='StringEditQuantity'),
    )
    sample_id = Quantity(
        type=str,
        description="""Full sample id. Ideally a human readable sample id convention,
        which is simple, understandable and still having chances of becoming unique.
        If the `sample_owner`, `sample_short_name`, `ìnstitute`, and `creation_datetime`
        are provided, this will be formed automatically by joining these components by an underscore (_).
        Spaces in any of the individual components will be replaced with hyphens (-).
        An example would be hzb_oah_20200602_4001-08""",
        a_eln=dict(component='StringEditQuantity'),
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        The normalizer for the `SampleID` class.
        If sample owner is not filled the field will be filled by the first two letters of
        the first name joined with the first two letters of the last name of the author.
        If the institute is not filled a institute abreviations will be constructed from
        the author's affiliation.
        If no creation datetime is filled, the datetime will be taken from the `datetime`
        property of the parent, if it exists, otherwise the current date and time will be
        used.
        If no short name is filled, the name will be taken from the parent name, if it
        exists, otherwise it will be taken from the archive metadata entry name, if it
        exists, and finally if no other options are available it will use the name of the
        mainfile.

        Args:
            archive (EntryArchive): The archive containing the section that is being
            normalized.
            logger ('BoundLogger'): A structlog logger.
        """
        super().normalize(archive, logger)

        if self.sample_owner is None or self.institute is None:
            author = archive.metadata.main_author
            if author and self.sample_owner is None:
                first_short = unidecode(author.first_name)[:2]
                last_short = unidecode(author.last_name)[:2]
                self.sample_owner = first_short + last_short
            if author and self.institute is None:
                unwanted_words = ('zu', 'of', 'the', 'fur', 'für')
                institute = ''
                all_words = re.split(' |-|_|,|:|;', unidecode(author.affiliation))
                wanted_words = [w for w in all_words if w.lower() not in unwanted_words]
                for word in wanted_words:
                    word_remainder = word
                    while word_remainder and len(institute) < 3:
                        letter = word_remainder[:1]
                        if letter.isupper():
                            institute += letter
                        word_remainder = word_remainder[1:]
                if len(institute) < min(len(author.affiliation), 2):
                    if len(wanted_words) < 3:
                        institute = author.affiliation[:3].upper()
                    else:
                        institute = ''.join([w[:1] for w in wanted_words[:3]]).upper()
                self.institute = institute

        if self.creation_datetime is None:
            if self.m_parent and getattr(self.m_parent, 'datetime', None):
                self.creation_datetime = self.m_parent.datetime
            else:
                self.creation_datetime = now()

        if self.sample_short_name is None:
            if self.m_parent and getattr(self.m_parent, 'name', None):
                name = self.m_parent.name
            elif archive.metadata.entry_name:
                name = archive.metadata.entry_name
            else:
                name = archive.metadata.mainfile
            self.sample_short_name = re.sub(r'_|\s', '-', name.split('.')[0])

        if (
            self.institute
            and self.sample_short_name
            and self.sample_owner
            and self.creation_datetime
        ):
            creation_date = self.creation_datetime.strftime('%Y%m%d')
            sample_owner = self.sample_owner.replace(' ', '-')
            sample_id_list = [
                self.institute,
                sample_owner,
                creation_date,
                self.sample_short_name,
            ]
            self.sample_id = '_'.join(sample_id_list)

        if not archive.results:
            archive.results = Results(eln=ELN())
        if not archive.results.eln:
            archive.results.eln = ELN()

        if self.sample_id:
            if not archive.results.eln.lab_ids:
                archive.results.eln.lab_ids = []
            if self.sample_id not in archive.results.eln.lab_ids:
                archive.results.eln.lab_ids.append(self.sample_id)

        if not archive.results.eln.sections:
            archive.results.eln.sections = []
        archive.results.eln.sections.append(self.m_def.name)


class CASExperimentalProperty(MSection):
    """A section for experimental properties retrieved from the CAS API."""

    name = Quantity(type=str, description='CAS experimental property name.')

    property = Quantity(type=str, description='CAS experimental property.')

    sourceNumber = Quantity(type=str, description='CAS experimental property source.')


class CASPropertyCitation(MSection):
    """A section for citations of the experimental properties retrieved from the CAS API."""

    docUri = Quantity(type=str, description='CAS property citation document uri.')

    sourceNumber = Quantity(type=int, decription='CAS property citation source number.')

    source = Quantity(type=str, description='CAS property citation source.')


class Substance(System):
    """A base section for any substance defined in the ELN."""

    name = Quantity(
        type=str,
        description='The name of the substance entry.',
        a_eln=dict(component='StringEditQuantity', label='substance name'),
    )

    lab_id = Quantity(
        type=str,
        description="""
        A human readable substance ID that is at least unique for the lab.
        """,
        a_eln=dict(component='StringEditQuantity', label='substance ID'),
    )

    cas_uri = Quantity(
        type=str,
        description='CAS uri',
        a_eln=dict(component='StringEditQuantity', label='CAS uri'),
    )

    cas_number = Quantity(
        type=str,
        description='CAS number.',
        a_eln=dict(component='StringEditQuantity', label='CAS number'),
    )

    cas_name = Quantity(
        type=str,
        description='CAS name.',
        a_eln=dict(component='StringEditQuantity', label='CAS name'),
    )

    image = Quantity(
        type=str,
        description='CAS image.',
        a_eln=dict(component='FileEditQuantity'),
        a_browser=dict(adaptor='RawFileAdaptor', label='image of substance'),
    )

    inchi = Quantity(
        type=str, description='CAS inchi.', a_eln=dict(component='StringEditQuantity')
    )

    inchi_key = Quantity(
        type=str,
        description='CAS inchi key.',
        a_eln=dict(component='StringEditQuantity'),
    )

    smile = Quantity(
        type=str, description='CAS smile.', a_eln=dict(component='StringEditQuantity')
    )

    canonical_smile = Quantity(
        type=str,
        description='CAS canonical smile.',
        a_eln=dict(component='StringEditQuantity'),
    )

    molecular_formula = Quantity(
        type=str,
        description='CAS molecular formula.',
        a_eln=dict(component='StringEditQuantity'),
    )

    molecular_mass = Quantity(
        type=np.dtype(np.float64),
        unit='Da',
        description='CAS molecular mass.',
        a_eln=dict(component='NumberEditQuantity'),
    )

    cas_experimental_properties = SubSection(
        section_def=CASExperimentalProperty, repeats=True
    )

    cas_property_citations = SubSection(section_def=CASPropertyCitation, repeats=True)

    cas_synonyms = Quantity(type=str, shape=['*'], description='CAS synonyms.')

    description = Quantity(
        type=str,
        description="""
        A field for adding additional information about the substance that is not captured
        by the other quantities and subsections.
        """,
        a_eln=dict(
            component='RichTextEditQuantity', label='detailed substance description'
        ),
    )

    def _populate_from_cas(self, archive, logger: Any) -> None:
        """Private method for populating the attributes from a call to the CAS API using
        the `cas_number`.
        Will overwrite exisiting CAS attributes if the query provides a value for them.
        I.e. all attributes that begin with `cas_`.

        Args:
            archive (EntryArchive): The archive that is being normalized.
            logger (Any): A structlog logger.
        """
        import httpx2

        response = httpx2.get(
            f'https://commonchemistry.cas.org/api/detail?cas_rn={self.cas_number}',
            timeout=2,
        )
        if response.status_code == 200:
            response_dict = response.json()

            self.cas_uri = response_dict.get('uri', None)

            cas_name = response_dict.get('name', None)
            if cas_name:
                self.cas_name = re.sub(r'<.*?>', '', cas_name)
                if not self.name:
                    self.name = self.cas_name

            if not self.image:
                image = response_dict.get('image', None)
                if image:
                    self.image = f'cas_{self.cas_number}_image.svg'
                    with archive.m_context.raw_file(self.image, 'w') as fh:
                        fh.write(image)

            if not self.inchi:
                self.inchi = response_dict.get('inchi', None)

            if not self.inchi_key:
                self.inchi_key = response_dict.get('inchiKey', None)

            if not self.smile:
                self.smile = response_dict.get('smile', None)

            if not self.canonical_smile:
                self.canonical_smile = response_dict.get('canonicalSmile', None)

            if not self.molecular_formula:
                molecular_formula = response_dict.get('molecularFormula', None)
                if molecular_formula:
                    self.molecular_formula = re.sub(r'<.*?>', '', molecular_formula)

            if not self.molecular_mass:
                molecular_mass = response_dict.get('molecularMass', None)
                if molecular_mass:
                    try:
                        self.molecular_mass = float(molecular_mass)
                    except ValueError as e:
                        logger.warn(
                            f"Could not convert molecular mass results'{molecular_mass}'"
                            + 'returned from CAS api request.',
                            exc_info=e,
                        )

            experimental_properties = response_dict.get('experimentalProperties', [])
            props = [
                CASExperimentalProperty.m_from_dict(p) for p in experimental_properties
            ]
            if len(props) > 0:
                self.cas_experimental_properties = props

            property_citations = response_dict.get('propertyCitations', [])
            citations = [CASPropertyCitation.m_from_dict(c) for c in property_citations]
            if len(citations) > 0:
                self.cas_property_citations = citations

            self.cas_synonyms = response_dict.get('synonyms', [])

        elif response.status_code == 404:
            logger.warn(f'No CAS entry found with CAS number: {self.cas_number}')
        elif response.status_code >= 500:
            logger.warn(f'Remote server error on CAS API call.')
        else:
            logger.warn(
                f'Unexpected response code: {response.status_code} from CAS API call.'
            )

    def _cas_search_unique(self, search: str, archive, logger: Any) -> bool:
        """Private method for performing a search of the CAS API and populating the
        attributes with the CAS number of any unique search result.

        Args:
            search (str): The string to search the CAS API with.
            archive (EntryArchive): The archive that is being normalized.
            logger (Any): A structlog logger.

        Returns:
            bool: Whether the search found a unique result.
        """
        import httpx2

        response = httpx2.get(
            f'https://commonchemistry.cas.org/api/search?q={search}', timeout=2
        )
        if response.status_code == 200:
            response_dict = response.json()
            n_hits = response_dict.get('count', 0)
            if n_hits == 0:
                logger.info(f"Queried the CAS API for '{search}' without result.")
                return False
            elif n_hits == 1:
                try:
                    self.cas_number = response_dict['results'][0]['rn']
                    self._populate_from_cas(archive, logger)
                    return True
                except KeyError as e:
                    logger.warn('Unknown response format from CAS API.', exc_info=e)
            else:
                logger.warn(
                    f"Query to CAS API for '{search}' returned {n_hits} hits, please "
                    + 'specify further.'
                )
                return False
        return False

    def _find_cas(self, archive, logger: Any) -> None:
        """Private method for finding the CAS number using the filled attributes in the
        following order:
        1. `cas_name`
        2. `inchi`
        3. `inchi_key`
        4. `smile`
        5. `canonical_smile`
        6. `name`
        The first unique hit will populate the `cas_number` attribute and return.

        Args:
            archive (EntryArchive): The archive that is being normalized.
            logger (Any): A structlog logger.
        """
        for key in (
            self.cas_name,
            self.inchi,
            self.inchi_key,
            self.smile,
            self.canonical_smile,
            self.name,
        ):
            if key and self._cas_search_unique(key, archive, logger):
                return

    def normalize(self, archive, logger: Any) -> None:
        """The normalizer method for the `Substance` class.
        This method will attempt to get data on the substance instance from the CAS API:
        https://commonchemistry.cas.org/api-overview
        If a CAS number is specified the details are retrieved directly.
        Otherwise a search query is made for the filled attributes in the following order:
        1. `cas_name`
        2. `inchi`
        3. `inchi_key`
        4. `smile`
        5. `canonical_smile`
        6. `name`

        Args:
            archive (EntryArchive): The archive that is being normalized.
            logger (Any): A structlog logger.
        """
        super().normalize(archive, logger)
        if logger is None:
            logger = utils.get_logger(__name__)
        logger.warn(
            'The Substance section in nomad.datamodel.metainfo.eln is deprecated. '
            'Please use the nomad.datamodel.metainfo.basesections.Substance instead. '
            'Note that the new section nests the substance properties in the subsection '
            '`substance` instead.'
        )
        from nomad.atomutils import Formula

        if self.cas_number:
            self.cas_number = self.cas_number.strip()
            self._populate_from_cas(archive, logger)
        else:
            self._find_cas(archive, logger)

        if self.molecular_formula:
            if not archive.results:
                archive.results = Results()
            if not archive.results.material:
                archive.results.material = Material()

            try:
                formula = Formula(self.molecular_formula)
                formula.populate(archive.results.material)
                if not self.elemental_composition:
                    mass_fractions = formula.mass_fractions()
                    for element, fraction in formula.atomic_fractions().items():
                        self.elemental_composition.append(
                            ElementalComposition(
                                element=element,
                                atomic_fraction=fraction,
                                mass_fraction=mass_fractions[element],
                            )
                        )
            except Exception as e:
                logger.warn('Could not analyse chemical formula.', exc_info=e)

        super().normalize(archive, logger)


class ElnWithFormulaBaseSection(ElnBaseSection):
    """
    A generic abstract base section for ELNs that provides a few commonly used for
    items with a chemical formula, e.g. chemicals or samples.
    """

    chemical_formula = Quantity(
        type=str,
        description=(
            'The chemical formula. This will be used directly and '
            'indirectly in the search. The formula will be used itself as well as '
            'the extracted chemical elements.'
        ),
        a_eln=dict(component='StringEditQuantity'),
    )

    def normalize(self, archive, logger):
        super().normalize(archive, logger)

        if logger is None:
            logger = utils.get_logger(__name__)
        from nomad.atomutils import Formula

        if self.chemical_formula:
            if not archive.results:
                archive.results = Results()
            if not archive.results.material:
                archive.results.material = Material()
            try:
                formula = Formula(self.chemical_formula)
            except Exception as e:
                logger.warn('could not analyse chemical formula', exc_info=e)
            else:
                try:
                    formula.populate(archive.results.material)
                except ValueError as e:
                    logger.info(
                        'composition information already defined, skipping populating it based on formula',
                        exc_info=e,
                    )


class Chemical(ElnWithFormulaBaseSection):
    """A ELN base section that can be used for chemicals."""

    pass


class Sample(ElnWithFormulaBaseSection):
    """A ELN base section that can be used for samples."""

    pass


class ElnWithStructureFile(ArchiveSection):
    """
    A base section for for parsing crystal structure files, e.g. `.cif`, and
    populating the Material section in Results.
    """

    structure_file = Quantity(
        type=str,
        description='The structure file.',
        a_eln=dict(component='FileEditQuantity'),
    )

    def normalize(self, archive, logger):
        super().normalize(archive, logger)

        if self.structure_file:
            from ase.io import read

            from nomad.normalizing import normalizers
            from nomad.normalizing.results import ResultsNormalizer

            system_normalizer_cls = None
            for normalizer in normalizers:
                if normalizer.__name__ == 'SystemNormalizer':
                    system_normalizer_cls = normalizer
                    break

            from nomad.datamodel.metainfo import runschema
            from nomad.normalizing.optimade import OptimadeNormalizer

            with archive.m_context.raw_file(self.structure_file) as f:
                try:
                    structure = read(f.name)
                except Exception as e:
                    raise ValueError('could not read structure file') from e

                run = runschema.run.Run()
                archive.run.append(run)
                system = runschema.system.System()
                system.atoms = runschema.system.Atoms()
                try:
                    system.atoms.lattice_vectors = structure.get_cell() * ureg.angstrom
                except Exception as e:
                    logger.warn(
                        'Could not parse lattice vectors from structure file.',
                        exc_info=e,
                    )
                system.atoms.labels = structure.get_chemical_symbols()
                system.atoms.positions = structure.get_positions() * ureg.angstrom
                try:
                    system.atoms.periodic = structure.get_pbc()
                except Exception as e:
                    logger.warn(
                        'Could not parse periodicity from structure file.', exc_info=e
                    )
                    system.atoms.periodic = [True, True, True]
                system.atoms.species = structure.get_atomic_numbers()
                archive.run[0].system = [system]
                program = runschema.run.Program()
                archive.run[0].program = program
                archive.run[0].program.name = 'Structure File Importer'
                if system_normalizer_cls:
                    system_normalizer = system_normalizer_cls(archive)
                    system_normalizer.normalize()
                optimade_normalizer = OptimadeNormalizer()
                optimade_normalizer.normalize(archive)
                results_normalizer = ResultsNormalizer()
                results_normalizer.normalize(archive)

        # TODO: rewrite it in a way in which the run section is not needed and System is
        # directly added to the archive.data
        # set run to None if exist
        if archive.run:
            archive.run = None


# Solar cell sections:
class SolarCellDefinition(ArchiveSection):
    stack_sequence = Quantity(
        type=str,
        shape=['*'],
        description="""
            The stack sequence describing the cell. Use the following formatting guidelines
            - Start with the substrate to the left and list the materials in each layer of the device
            - If two materials, e.g. A and B, are mixed in one layer, list the materials in alphabetic order and separate them with semicolons, as in (A; B)
            - The absorber layer in other databases is commonly stated with a generaic name as “Perovskite”, regardless of composition, mixtures, dimensionality etc.
                There are other fields to describe in depth the absorber layer.
        """,
        a_eln=dict(component='EnumEditQuantity', props=dict(suggestions=sorted([]))),
    )

    solar_cell_area = Quantity(
        type=np.dtype(np.float64),
        unit='cm**2',
        shape=[],
        description="""
            The total cell area in cm^2.
            The total area is defined as the area that would provide photovoltaic performance.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    architecture = Quantity(
        type=str,
        shape=[],
        description="""
            The cell architecture with respect to the direction of current flow and
            the order in which layers are deposited.
            The two most common are nip (also referred to as normal) and pin (also referred to as inverted)
            but there are also a few others, e.g. Back contacted.
            - *nip* architecture means that the electrons are collected at the substrate side.
            The typical example is in perovskite solar cells when a TiO2 electron selective contact is deposited
            between the perovskite and the substrate (e.g. SLG | FTO | TiO2-c | Perovskite | …)
            - *pin* architecture means that it instead is the holes that are collected at the substrate side. The typical example is when a PEDOT:PSS hole selective contact is deposited between the perovskite and the substrate (e.g. SLG | FTO | PEDOT:PSS |Perovskite | …)
        """,
        a_eln=dict(
            component='EnumEditQuantity',
            props=dict(
                suggestions=[
                    'Unknown',
                    'Pn-Heterojunction',
                    'Front contacted',
                    'Back contacted',
                    'pin',
                    'nip',
                    'Schottky',
                ]
            ),
        ),
    )

    def normalize(self, archive, logger):
        super().normalize(archive, logger)
        add_solar_cell(archive)
        if self.stack_sequence:
            if '/' in self.stack_sequence:
                archive.results.properties.optoelectronic.solar_cell.device_stack = (
                    self.stack_sequence.split('/')
                )
            elif '|' in self.stack_sequence:
                archive.results.properties.optoelectronic.solar_cell.device_stack = (
                    self.stack_sequence.split(' | ')
                )
            else:
                archive.results.properties.optoelectronic.solar_cell.device_stack = (
                    self.stack_sequence
                )

        if self.architecture:
            archive.results.properties.optoelectronic.solar_cell.device_architecture = (
                self.architecture
            )
        if self.solar_cell_area:
            archive.results.properties.optoelectronic.solar_cell.device_area = (
                self.solar_cell_area
            )
        if not archive.results.material:
            archive.results.material = Material()
        material = archive.results.material
        if material.functional_type is None:
            material.functional_type = ['semiconductor', 'solar cell']


class SolarCellLayer(ArchiveSection):
    solar_cell_layer_type = Quantity(
        type=str,
        shape=[],
        description='type of the layer',
        a_eln=dict(
            component='EnumEditQuantity',
            props=dict(
                suggestions=[
                    'Substrate',
                    'Absorber',
                    'Hole Transport Layer',
                    'Electron Transport Layer',
                    'Contact',
                    'Buffer',
                    'p-type contact',
                    'n-type contact',
                    'other',
                ]
            ),
        ),
    )

    layer_name = Quantity(
        type=str,
        shape=['0..*'],
        description="""
            The name of the layer.
        """,
        a_eln=dict(component='EnumEditQuantity', props=dict(suggestions=[])),
    )

    layer_thickness = Quantity(
        type=np.dtype(np.float64),
        unit=('nm'),
        shape=[],
        description="""
            The thickness of the layer in nm.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    def normalize(self, archive, logger):
        super().normalize(archive, logger)
        add_solar_cell(archive)
        if self.layer_name:
            if self.solar_cell_layer_type == 'Absorber':
                archive.results.properties.optoelectronic.solar_cell.absorber = [
                    self.layer_name
                ]
            elif self.solar_cell_layer_type == 'Substrate':
                archive.results.properties.optoelectronic.solar_cell.substrate = [
                    self.layer_name
                ]
            elif self.solar_cell_layer_type == 'Hole Transport Layer':
                archive.results.properties.optoelectronic.solar_cell.hole_transport_layer = [
                    self.layer_name
                ]
            elif self.solar_cell_layer_type == 'Electron Transport Layer':
                archive.results.properties.optoelectronic.solar_cell.electron_transport_layer = [
                    self.layer_name
                ]
            elif self.solar_cell_layer_type == 'Contact':
                archive.results.properties.optoelectronic.solar_cell.back_contact = [
                    self.layer_name
                ]


class SolarCellBaseSectionWithOptoelectronicProperties(ArchiveSection):
    bandgap = Quantity(
        type=np.dtype(np.float64),
        unit=('eV'),
        shape=[],
        description="""
            The bandgap of the solar cell.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    def normalize(self, archive, logger):
        super().normalize(archive, logger)
        add_solar_cell(archive)
        add_band_gap(archive, self.bandgap)


class SolarCellJV(PlotSection):
    m_def = Section(
        label_quantity='cell_name',
        a_plotly_graph_object=[
            {'data': {'x': '#voltage', 'y': '#current_density'}},
            {'data': {'x': '#voltage', 'y': '#current_density'}},
        ],
    )

    data_file = Quantity(
        type=str,
        a_eln=dict(component='FileEditQuantity'),
        a_browser=dict(adaptor='RawFileAdaptor'),
    )

    certified_values = Quantity(
        type=bool,
        shape=[],
        description="""
            TRUE if the IV data is measured by an independent and certification institute.
            If your solar simulator is calibrated by a calibrated reference diode,
            that does not count as a certified result.
        """,
        a_eln=dict(component='BoolEditQuantity'),
    )

    certification_institute = Quantity(
        type=str,
        shape=[],
        description="""
            The name of the certification institute that has measured the certified device.
            Example:
            Newport
            NIM, National Institute of Metrology of China
            KIER, Korea Institute of Energy Research
        """,
        a_eln=dict(
            component='EnumEditQuantity',
            props=dict(
                suggestions=sorted(
                    [
                        'National Institute ofMetrology, China',
                        'Quality supervision＆Testing Center of Chemical＆Physical Power Sources of Information Industry',
                        'CREST, Photovoltaic Meaasurement and calibration Laboratory at Universit of Loughborough',
                        'Photovoltaic and Wind Power Systems Quality Test Center, Chinese Academy of Sciences',
                        'NREL',
                        'Institute of Metrology (NIM) of China',
                        'PVEVL, National Central University, Taiwan',
                        'NIM, National Institute of Metrology of China',
                        'Fraunhofer ISE',
                        'SIMIT, Shanghai Institute of Microsystem and Information Technology',
                        'Newport',
                        'CSIRO, PV Performance Lab at Monash University',
                        'AIST, National Institute of Advanced Industrial Science and Technology',
                        'CPVT, National Center of Supervision and Inspection on Solar Photovoltaic Products Quality of China',
                        'KIER, Korea Institute of Energy Research',
                        'Newport Corporation',
                        'Solar Power Lab at Arizona State University',
                    ]
                )
            ),
        ),
    )

    light_intensity = Quantity(
        type=np.dtype(np.float64),
        unit=('mW/cm**2'),
        shape=[],
        default=100.0,
        description="""
            The light intensity during the IV measurement
            - If there are uncertainties, only state the best estimate, e.g. write 100 and not 90-100.
            - Standard AM 1.5 illumination correspond to 100 mW/cm2
            - If you need to convert from illumination given in lux; at 550 nm, 1 mW/cm2 corresponds to 6830 lux. Be aware that the conversion change with the spectrum used. As a rule of thumb for general fluorescent/LED light sources, around 0.31mW corresponded to 1000 lux. If your light intensity is measured in lux, it probably means that your light spectra deviates quite a lot from AM 1.5, wherefore it is very important that you also specify the light spectra in the next column.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    open_circuit_voltage = Quantity(
        type=np.dtype(np.float64),
        unit='V',
        shape=[],
        description="""
            Open circuit voltage.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    short_circuit_current_density = Quantity(
        type=np.dtype(np.float64),
        unit='mA / cm**2',
        shape=[],
        description="""
            Short circuit current density.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    fill_factor = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
            Fill factor.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    efficiency = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        description="""
            Power conversion efficiency.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    potential_at_maximum_power_point = Quantity(
        type=np.dtype(np.float64),
        unit='V',
        shape=[],
        description="""
            The potential at the maximum power point, Vmp.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    current_density_at_maximun_power_point = Quantity(
        type=np.dtype(np.float64),
        unit='mA / cm**2',
        shape=[],
        description="""
            The current density at the maximum power point, *Jmp*.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    series_resistance = Quantity(
        type=np.dtype(np.float64),
        unit='ohm*cm**2',
        shape=[],
        description="""
            The series resistance as extracted from the *J-V* curve.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    shunt_resistance = Quantity(
        type=np.dtype(np.float64),
        unit='ohm*cm**2',
        shape=[],
        description="""
            The shunt resistance as extracted from the *J-V* curve.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    def derive_n_values(self):
        if self.current_density is not None:
            return len(self.current_density)
        if self.voltage is not None:
            return len(self.voltage)
        else:
            return 0

    n_values = Quantity(type=int, derived=derive_n_values)

    def update_results(self, archive):
        if self.open_circuit_voltage is not None:
            archive.results.properties.optoelectronic.solar_cell.open_circuit_voltage = self.open_circuit_voltage
        if self.short_circuit_current_density is not None:
            archive.results.properties.optoelectronic.solar_cell.short_circuit_current_density = self.short_circuit_current_density
        if self.fill_factor is not None:
            archive.results.properties.optoelectronic.solar_cell.fill_factor = (
                self.fill_factor
            )
        if self.efficiency is not None:
            archive.results.properties.optoelectronic.solar_cell.efficiency = (
                self.efficiency
            )
        if self.light_intensity is not None:
            archive.results.properties.optoelectronic.solar_cell.illumination_intensity = self.light_intensity

    def normalize(self, archive, logger):
        super().normalize(archive, logger)

        add_solar_cell(archive)
        self.update_results(archive)


class SolarCellJVCurve(SolarCellJV):
    def cell_params(self):
        """
        Calculates basic solar cell parametes form a current density (mA/cm**2)
        voltage (V) curve.

        Returns:
            Voc (V) open circuit voltage
            Jsc (mA/cm**2) short circuit current density
            FF fill factor in absolute values (0-1)
            efficiency power conversion efficiency in percentage (0-100)
        """
        from scipy import interpolate

        j_v_interpolated = interpolate.interp1d(self.current_density, self.voltage)
        Voc = j_v_interpolated(0).item()
        v_j_interpolated = interpolate.interp1d(self.voltage, self.current_density)
        Isc = v_j_interpolated(0).item()
        if Isc >= 0:
            idx = np.argmax(self.voltage * self.current_density)
        else:
            idx = np.argmin(self.voltage * self.current_density)
        Vmp = self.voltage[idx]
        Imp = self.current_density[idx]
        Isc = abs(Isc)
        FF = abs(Vmp.magnitude * Imp.magnitude / (Voc * Isc))
        efficiency = Voc * FF * Isc
        return Voc, Isc, FF, efficiency

    cell_name = Quantity(
        type=str,
        shape=[],
        description='Cell identification name.',
        a_eln=dict(component='StringEditQuantity'),
    )

    current_density = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='mA/cm^2',
        description='Current density array of the *JV* curve.',
    )

    voltage = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='V',
        description='Voltage array of the of the *JV* curve.',
    )

    def normalize(self, archive, logger):
        super().normalize(archive, logger)
        if self.current_density is not None:
            if self.voltage is not None:
                (
                    self.open_circuit_voltage,
                    self.short_circuit_current_density,
                    self.fill_factor,
                    self.efficiency,
                ) = self.cell_params()
                self.update_results(archive)


class SolarCellEQE(PlotSection):
    m_def = Section(
        a_eln=dict(lane_width='600px'),
        a_plotly_graph_object=[
            {'data': {'x': '#photon_energy_array', 'y': '#raw_eqe_array'}},
            {'data': {'x': '#raw_photon_energy_array', 'y': '#raw_eqe_array'}},
            {'data': {'x': '#raw_wavelength_array', 'y': '#raw_eqe_array'}},
            {'data': {'x': '#photon_energy_array', 'y': '#eqe_array'}},
            {'data': {'x': '#wavelength_array', 'y': '#eqe_array'}},
            {'data': {'x': '#photon_energy_array', 'y': '#eqe_array'}},
        ],
    )

    eqe_data_file = Quantity(
        type=str,
        description="""
                    Drop here your eqe file and click save for processing.
                    """,
        a_eln=dict(component='FileEditQuantity'),
        a_browser=dict(adaptor='RawFileAdaptor'),
    )

    header_lines = Quantity(
        type=np.dtype(np.int64),
        default=0,
        description="""
        Number of header lines in the file. Edit in case your file has a header.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    light_bias = Quantity(
        type=np.dtype(np.float64),
        unit=('mW/cm**2'),
        shape=[],
        description="""
        The light intensity of any bias light during the EQE measurement.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    bandgap_eqe = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='eV',
        description="""
        Bandgap derived from the EQE spectrum.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    integrated_jsc = Quantity(
        type=np.dtype(np.float64),
        unit='mA / cm**2',
        shape=[],
        description="""
        The integrated short circuit current density $J_{SC}$ from the product of the EQE spectrum
        with the *AM 1.5G* sun spectrum.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    integrated_j0rad = Quantity(
        type=np.dtype(np.float64),
        unit='mA / cm**2',
        shape=[],
        description="""
        The integrated $J_{0, Rad}$ derived from the EQE data.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    voc_rad = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='V',
        description="""
        Radiative $V_{OC}$ derived from the EQE data in V.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    urbach_energy = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='eV',
        description="""
        Urbach energy fitted from the eqe in eV.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    urbach_energy_fit_std_dev = Quantity(
        type=np.dtype(np.float64),
        shape=[],
        unit='eV',
        description="""
        Standard deviation of the fitted Urbach energy parameter from the eqe in eV.
        """,
    )

    def derive_n_values(self):
        if self.eqe_array is not None:
            return len(self.eqe_array)
        if self.photon_energy_array is not None:
            return len(self.photon_energy_array)
        else:
            return 0

    n_values = Quantity(type=int, derived=derive_n_values)

    def derive_n_raw_values(self):
        if self.raw_eqe_array is not None:
            return len(self.raw_eqe_array)
        if self.raw_photon_energy_array is not None:
            return len(self.raw_photon_energy_array)
        else:
            return 0

    n_raw_values = Quantity(type=int, derived=derive_n_raw_values)

    raw_eqe_array = Quantity(
        type=np.dtype(np.float64),
        shape=['n_raw_values'],
        description='EQE array of the spectrum',
    )

    raw_photon_energy_array = Quantity(
        type=np.dtype(np.float64),
        shape=['n_raw_values'],
        unit='eV',
        description='Raw Photon energy array of the eqe spectrum',
    )

    raw_wavelength_array = Quantity(
        type=np.dtype(np.float64),
        shape=['n_raw_values'],
        unit='nanometer',
        description='Raw wavelength array of the eqe spectrum',
    )

    eqe_array = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        description='EQE array of the spectrum',
    )

    wavelength_array = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='nanometer',
        description='Interpolated/extrapolated wavelength array with *E<sub>u</sub>* of the eqe spectrum ',
    )

    photon_energy_array = Quantity(
        type=np.dtype(np.float64),
        shape=['n_values'],
        unit='eV',
        description='Interpolated/extrapolated photon energy array with a *E<sub>u</sub>*  of the eqe spectrum',
    )

    def normalize(self, archive, logger):
        super().normalize(archive, logger)

        if self.eqe_data_file:
            with archive.m_context.raw_file(self.eqe_data_file) as f:
                # Import `EQEAnalyzer` is slow (owing to scipy)
                from nomad.datamodel.metainfo.eln.eqe_parser import EQEAnalyzer

                eqe_dict = EQEAnalyzer(
                    f.name, header_lines=self.header_lines
                ).eqe_dict()
                self.measured = True
                self.bandgap_eqe = eqe_dict['bandgap']
                self.integrated_jsc = eqe_dict['jsc'] * ureg('A/m**2')
                self.integrated_j0rad = (
                    eqe_dict['j0rad'] * ureg('A/m**2')
                    if 'j0rad' in eqe_dict
                    else logger.warning('The j0rad could not be calculated.')
                )
                self.voc_rad = (
                    eqe_dict['voc_rad']
                    if 'voc_rad' in eqe_dict
                    else logger.warning('The voc_rad could not be calculated.')
                )
                self.urbach_energy = (
                    eqe_dict['urbach_e']
                    if 'urbach_e' in eqe_dict
                    else logger.warning('The urbach_energy could not be calculated.')
                )
                if 'error_urbach_std' in eqe_dict:
                    self.urbach_energy_fit_std_dev = eqe_dict['error_urbach_std']
                self.photon_energy_array = np.array(
                    eqe_dict['interpolated_photon_energy']
                )
                self.raw_photon_energy_array = np.array(eqe_dict['photon_energy_raw'])
                self.eqe_array = np.array(eqe_dict['interpolated_eqe'])
                self.raw_eqe_array = np.array(eqe_dict['eqe_raw'])

        if self.photon_energy_array is not None:
            self.wavelength_array = self.photon_energy_array.to('nm', 'sp')  # pylint: disable=E1101
            self.raw_wavelength_array = self.raw_photon_energy_array.to('nm', 'sp')  # pylint: disable=E1101

        add_solar_cell(archive)
        add_band_gap(archive, self.bandgap_eqe)


m_package.__init_metainfo__()
