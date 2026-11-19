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
import random
import re
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING
from urllib.parse import quote

import h5py
import numpy as np
import requests
from ase.data import atomic_masses, atomic_numbers, chemical_symbols
from unidecode import unidecode

from nomad.datamodel.metainfo.common import default_datetime
from nomad.datamodel.metainfo.workflow import Link, Task, TaskReference, Workflow
from nomad.metainfo import SchemaPackage
from nomad.metainfo.data_type import m_str

if TYPE_CHECKING:
    from structlog.stdlib import BoundLogger

from nomad import utils
from nomad.atomutils import Formula
from nomad.datamodel.data import ArchiveSection, EntryData
from nomad.datamodel.metainfo.annotations import ELNAnnotation, HDF5Annotation
from nomad.datamodel.results import ELN, Material, Results
from nomad.datamodel.results import ElementalComposition as ResultsElementalComposition
from nomad.datamodel.util import create_custom_mapping
from nomad.metainfo import Datetime, Quantity, Reference, Section, SubSection
from nomad.metainfo.util import MEnum
from nomad.units import ureg

PUB_CHEM_PUG_PATH = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound'
CAS_API_PATH = 'https://commonchemistry.cas.org/api'
EXTERNAL_API_TIMEOUT = 5


def throttle_wait():
    """Function for waiting before an API request to prevent throttling."""
    time.sleep(random.randint(1, 3))


def pub_chem_add_throttle_header(response: requests.Response, message: str = '') -> str:
    """Function for adding the PubChem PUG API throttling control header to a message."""
    if 'X-Throttling-Control' in response.headers:
        message += f' (Throttling-Control: {response.headers["X-Throttling-Control"]})'
    return message


m_package = SchemaPackage(aliases=['nomad.datamodel.metainfo.basesections'])


def pub_chem_api_get_properties(
    cid: int, properties: Iterable[str]
) -> requests.Response:
    """
    Function for performing a get request to the PubChem PUG API to get properties for a
    given compound identifier.

    Args:
        cid (int): The compound identifier of the compound of interest.
        properties (Iterable[str]): The properties to retrieve the value for.

    Returns:
        requests.Response: The response as returned from the PubChem PUG API.
    """
    throttle_wait()
    return requests.get(
        url=f'{PUB_CHEM_PUG_PATH}/cid/{cid}/property/{str.join(",", properties)}/JSON',
        timeout=EXTERNAL_API_TIMEOUT,
    )


def pub_chem_api_get_synonyms(cid: int) -> requests.Response:
    """
    Function for performing a get request to the PubChem PUG API to get properties for a
    given compound identifier.

    Args:
        cid (int): The compound identifier of the compound of interest.

    Returns:
        requests.Response: The response as returned from the PubChem PUG API.
    """
    throttle_wait()
    return requests.get(
        url=f'{PUB_CHEM_PUG_PATH}/cid/{cid}/synonyms/JSON',
        timeout=EXTERNAL_API_TIMEOUT,
    )


def pub_chem_api_search(path: str, search: str) -> requests.Response:
    """
    Function for performing a get request to the PubChem PUG API to search the given path
    for a given string.

    Args:
        path (str): The path (property) to search for.
        search (str): The string to search for a match with.

    Returns:
        requests.Response: The response as returned from the PubChem PUG API.
    """
    throttle_wait()
    # encode all special characters, for ex., encode URL-unsafe "#" in smiles C=CC#N
    safe_search_str = quote(search, safe='')
    if path in ['smiles', 'inchi']:
        # for at least these paths, URL with CGI parameter should always work
        return requests.get(
            url=f'{PUB_CHEM_PUG_PATH}/{path}/cids/JSON?{path}={safe_search_str}',
            timeout=EXTERNAL_API_TIMEOUT,
        )
    return requests.get(
        url=f'{PUB_CHEM_PUG_PATH}/{path}/{safe_search_str}/cids/JSON',
        timeout=EXTERNAL_API_TIMEOUT,
    )


def cas_api_search(search: str) -> requests.Response:
    """
    Function for performing a get request to the CAS API to search for a match with the
    given string.

    Args:
        search (str): The string to search for a match with.

    Returns:
        requests.Response: The response as returned from the CAS API.
    """
    return requests.get(
        f'{CAS_API_PATH}/search?q={search}',
        timeout=EXTERNAL_API_TIMEOUT,
    )


def cas_api_details(cas_rn: str) -> requests.Response:
    """
    Function for performing a get request to the CAS API to get the details for the
    substance with the given CAS registry number.

    Args:
        cas_rn (str): The CAS registry number of the substance for which to get details.

    Returns:
        requests.Response: The response as returned from the CAS API.
    """
    return requests.get(
        f'{CAS_API_PATH}/detail?cas_rn={cas_rn}',
        timeout=EXTERNAL_API_TIMEOUT,
    )


def is_cas_rn(candidate: str) -> bool:
    """
    Help function for checking if a candidate string is a valid CAS Registry Number.

    Args:
        candidate (str): The candidate string to be checked.

    Returns:
        bool: Whether or not the candidate string is a valid CAS Registry Number.
    """
    try:
        match = re.fullmatch(
            r'(?P<p1>\d{2,7})-(?P<p2>\d{2})-(?P<check>\d{1})', candidate
        )
        if match is None:
            return False
        check = (
            sum(
                [
                    int(c) * (i + 1)
                    for i, c in enumerate(
                        reversed(match.group('p1') + match.group('p2'))
                    )
                ]
            )
            % 10
        )
        return int(match.group('check')) == check
    except (AttributeError, TypeError):
        return False


class BaseSection(ArchiveSection):
    """
    A generic abstract base section that provides a few commonly used properties.

    If you inherit from this section, but do not need some quantities, list those
    quantities in the `eln.hide` annotation of your inheriting section definition.

    Besides predefining some quantities, these base sections will add some metadata
    to NOMAD's search. A particular example are `tags`, if you define a string
    or enum quantity in your sections named `tags`, its values will be searchable.
    """

    m_def = Section(
        links=['http://purl.obolibrary.org/obo/BFO_0000001'],
    )
    name = Quantity(
        type=str,
        description='A short human readable and descriptive name.',
        a_eln=dict(component='StringEditQuantity'),
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
        a_eln=dict(component='RichTextEditQuantity', props=dict(height=200)),
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        - If the instance is of type `EntryData`, it sets the archive's entry name based on the instance's name.
        - Sets the `datetime` field to the creation time of the entry if it is not already set.
        - Manages the `lab_id` field and updates the archive's `results.eln.lab_ids` list.
        - Adds the instance's `name` and `description` to the archive's `results.eln.names` and `results.eln.descriptions` lists, respectively.
        - Handles the `tags` attribute, if present, and updates the archive's `results.eln.tags` list.
        - Appends the section's name to the archive's `results.eln.sections` list.
        """
        super().normalize(archive, logger)

        if isinstance(self, EntryData):
            if self.name is None:
                self.name = archive.metadata.entry_name.split('.')[0].replace('_', ' ')
            if archive.data == self:
                archive.metadata.entry_name = self.name
            EntryData.normalize(self, archive, logger)

        if not archive.results:
            archive.results = Results(eln=ELN())
        if not archive.results.eln:
            archive.results.eln = ELN()

        if self.datetime is None:
            self.datetime = default_datetime(archive)

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
        # It is not good practice searching for a attributes which is not defined in datamodel
        try:
            tags_attr = getattr(self, 'tags', None)
        except Exception as e:
            tags_attr = None
        if tags_attr:
            if archive.results.eln.tags is None:
                archive.results.eln.tags = []

            tags = self.tags
            if isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, str):
                        archive.results.eln.tags.append(tag)
            elif isinstance(tags, str):
                archive.results.eln.tags.append(tags)

        if not archive.results.eln.sections:
            archive.results.eln.sections = []
        archive.results.eln.sections.append(self.m_def.name)


class Entity(BaseSection):
    """
    An object that persists, endures, or continues to exist through time while maintaining
    its identity.
    """

    m_def = Section(
        links=['http://purl.obolibrary.org/obo/BFO_0000002'],
    )


class ActivityStep(ArchiveSection):
    """
    Any dependant step of an `Activity`.
    """

    m_def = Section()
    name = Quantity(
        type=str,
        description="""
        A short and descriptive name for this step.
        """,
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
            label='Step name',
        ),
    )
    start_time = Quantity(
        type=Datetime,
        description="""
        Optionally, the starting time of the activity step. If omitted, it is assumed to
        follow directly after the previous step.
        """,
        a_eln=ELNAnnotation(component='DateTimeEditQuantity', label='Starting time'),
    )
    comment = Quantity(
        type=str,
        description="""
        Any additional information about the step not captured by the other fields.
        """,
        a_eln=ELNAnnotation(
            component='RichTextEditQuantity',
        ),
    )

    def to_task(self) -> Task:
        """
        Returns the task description of this activity step.

        Returns:
            Task: The activity step as a workflow task.
        """
        task = Task(name=self.name, section=self)
        return task


class Activity(BaseSection):
    """
    An action that has a temporal extension and for some time depends on some entity.
    """

    m_def = Section(
        links=['http://purl.obolibrary.org/obo/BFO_0000015'],
    )
    datetime = Quantity(
        type=Datetime,
        description='The date and time when this activity was started.',
        a_eln=dict(component='DateTimeEditQuantity', label='Starting time'),
    )
    method = Quantity(
        type=str,
        description='A short consistent handle for the applied method.',
    )
    location = Quantity(
        type=str,
        description='location of the experiment.',
        a_eln=dict(component='StringEditQuantity'),
    )
    steps = SubSection(
        section_def=ActivityStep,
        description="""
        An ordered list of all the dependant steps that make up this activity.
        """,
        repeats=True,
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        - Ensures the `results.eln.methods` list is initialized and appends the method or section name.
        - Converts each step in `self.steps` to a task, using the steps `to_task()` method, and assigns it to `archive.workflow2.tasks`.
        """
        super().normalize(archive, logger)

        if archive.results.eln.methods is None:
            archive.results.eln.methods = []
        if self.method:
            archive.results.eln.methods.append(self.method)
        else:
            archive.results.eln.methods.append(self.m_def.name)
        if archive.workflow2 is None:
            archive.workflow2 = Workflow(name=self.name)
        archive.workflow2.tasks = [step.to_task() for step in self.steps]


class SectionReference(ArchiveSection):
    """
    A section used for referencing another section.
    """

    name = Quantity(
        type=str,
        description='A short descriptive name for the role of this reference.',
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
        ),
    )
    reference = Quantity(
        type=ArchiveSection,
        description='A reference to a NOMAD archive section.',
        a_eln=ELNAnnotation(
            component='ReferenceEditQuantity',
            label='Section reference',
        ),
    )


class EntityReference(SectionReference):
    """
    A section used for referencing an Entity.
    """

    reference = Quantity(
        type=Entity,
        description='A reference to a NOMAD `Entity` entry.',
        a_eln=ELNAnnotation(
            component='ReferenceEditQuantity',
            label='Entity reference',
        ),
    )
    lab_id = Quantity(
        type=str,
        description="""
        The readable identifier for the entity.
        """,
        a_eln=ELNAnnotation(component='StringEditQuantity', label='Entity ID'),
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        Will attempt to fill the `reference` from the `lab_id` or vice versa.
        """
        super().normalize(archive, logger)
        if self.reference is None and self.lab_id is not None:
            from nomad.search import MetadataPagination, search

            query = {'results.eln.lab_ids': self.lab_id}
            search_result = search(
                owner='all',
                query=query,
                pagination=MetadataPagination(page_size=1),
                user_id=archive.metadata.main_author.user_id,
            )
            if search_result.pagination.total > 0:
                entry_id = search_result.data[0]['entry_id']
                upload_id = search_result.data[0]['upload_id']
                self.reference = f'../uploads/{upload_id}/archive/{entry_id}#data'
                if search_result.pagination.total > 1:
                    logger.warn(
                        f'Found {search_result.pagination.total} entries with lab_id: '
                        f'"{self.lab_id}". Will use the first one found.'
                    )
            else:
                logger.warn(f'Found no entries with lab_id: "{self.lab_id}".')
        elif self.lab_id is None and self.reference is not None:
            self.lab_id = self.reference.lab_id
        if self.name is None and self.lab_id is not None:
            self.name = self.lab_id


class ExperimentStep(ActivityStep):
    """
    Any dependant step of an `Experiment`.
    """

    activity = Quantity(
        type=Activity,
        description="""
        The activity that makes up this step of the experiment.
        """,
        a_eln=ELNAnnotation(
            component='ReferenceEditQuantity',
        ),
    )
    lab_id = Quantity(
        type=str,
        description="""
        The readable identifier for the activity.
        """,
        a_eln=ELNAnnotation(
            component='StringEditQuantity',
            label='Activity ID',
        ),
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        Will attempt to fill the `activity` from the `lab_id` or vice versa.
        If the activity reference is filled but the start time is not the time will be
        taken from the `datetime` property of the referenced activity.
        """
        super().normalize(archive, logger)
        if self.activity is None and self.lab_id is not None:
            from nomad.search import MetadataPagination, search

            query = {'results.eln.lab_ids': self.lab_id}
            search_result = search(
                owner='all',
                query=query,
                pagination=MetadataPagination(page_size=1),
                user_id=archive.metadata.main_author.user_id,
            )
            if search_result.pagination.total > 0:
                entry_id = search_result.data[0]['entry_id']
                upload_id = search_result.data[0]['upload_id']
                self.activity = f'../uploads/{upload_id}/archive/{entry_id}#data'
                if search_result.pagination.total > 1:
                    logger.warn(
                        f'Found {search_result.pagination.total} entries with lab_id: '
                        f'"{self.lab_id}". Will use the first one found.'
                    )
            else:
                logger.warn(f'Found no entries with lab_id: "{self.lab_id}".')
        elif self.lab_id is None and self.activity is not None:
            self.lab_id = self.activity.lab_id
        if self.name is None and self.lab_id is not None:
            self.name = self.lab_id
        if (
            self.activity is not None
            and self.start_time is None
            and self.activity.datetime
        ):
            self.start_time = self.activity.datetime

    def to_task(self) -> Task:
        if self.activity is None:
            return Task(name=self.name)
        return TaskReference(task=self.activity.m_parent.workflow2)


class Experiment(Activity):
    """
    A section for grouping activities together into an experiment.
    """

    steps = Activity.steps.m_copy()
    steps.section_def = ExperimentStep


class Collection(Entity):
    """
    A section for grouping entities together into a collection.
    """

    entities = SubSection(
        section_def=EntityReference,
        description='References to the entities that make up the collection.',
        repeats=True,
    )


class ElementalComposition(ArchiveSection):
    """
    A section for describing the elemental composition of a system, i.e. the element
    and its atomic fraction.
    """

    m_def = Section(
        label_quantity='element',
    )
    element = Quantity(
        type=MEnum(chemical_symbols[1:]),
        description="""
        The symbol of the element, e.g. 'Pb'.
        """,
        a_eln=dict(component='AutocompleteEditQuantity'),
    )
    atomic_fraction = Quantity(
        type=np.float64,
        description="""
        The atomic fraction of the element in the system it is contained within.
        Per definition a positive value less than or equal to 1.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )
    mass_fraction = Quantity(
        type=np.float64,
        description="""
        The mass fraction of the element in the system it is contained within.
        Per definition a positive value less than or equal to 1.
        """,
        a_eln=dict(component='NumberEditQuantity'),
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        Will add a
        results.material subsection if none exists. Will append the element to the
        elements property of that subsection and a
        nomad.datamodel.results.ElementalComposition instances to the
        elemental_composition property  using the element and atomic fraction from this
        section.
        """
        super().normalize(archive, logger)

        if self.element:
            if not archive.results:
                archive.results = Results()
            if not archive.results.material:
                archive.results.material = Material()

            if self.element not in chemical_symbols:
                logger.warn(
                    f"'{self.element}' is not a valid element symbol and this "
                    'elemental_composition section will be ignored.'
                )
            elif self.element not in archive.results.material.elements:
                archive.results.material.elements += [self.element]
            if self.atomic_fraction or self.mass_fraction:
                comp_result_section = archive.results.material.elemental_composition
                result_composition = ResultsElementalComposition(
                    element=self.element,
                    atomic_fraction=self.atomic_fraction,
                    mass_fraction=self.mass_fraction,
                    mass=atomic_masses[atomic_numbers[self.element]] * ureg.amu,
                )
                existing_elements = [comp.element for comp in comp_result_section]
                if self.element in existing_elements:
                    index = existing_elements.index(self.element)
                    comp_result_section[index].atomic_fraction = self.atomic_fraction
                    comp_result_section[index].mass_fraction = self.mass_fraction
                    comp_result_section[index].mass = (
                        atomic_masses[atomic_numbers[self.element]] * ureg.amu
                    )
                else:
                    comp_result_section.append(result_composition)


class System(Entity):
    """
    A base section for any system of materials which is investigated or used to construct
    other systems.
    """

    elemental_composition = SubSection(
        description="""
        A list of all the elements found in the system together and their respective
        atomic fraction within the system.
        """,
        section_def=ElementalComposition,
        repeats=True,
    )

    def _fill_fractions(self, archive, logger: 'BoundLogger') -> None:
        """
        Private method for attempting to fill missing fractions (atomic or mass) in the
        `ElementalComposition` objects listed in elemental_composition.
        If a single fraction (of mass or atomic) is left blank it will be calculated from
        the others. If after this check, all atomic fractions are filled and no mass
        fractions are, the mass fractions will be calculated from the atomic fractions.
        similarly, the atomic fractions are calculated from the mass ones if all mass and
        no atomic fractions are filled.

        Args:
            archive (EntryArchive): The archive containing the section that is being
            normalized.
            logger ('BoundLogger'): A structlog logger.

        Raises:
            ValueError: If an unknown element is present.
        """
        atomic_fractions = []
        mass_fractions = []
        element_masses = []
        for comp in self.elemental_composition:
            atomic_fractions.append(comp.atomic_fraction)
            mass_fractions.append(comp.mass_fraction)
            if comp.element is None:
                return
            elif comp.element not in atomic_numbers:
                raise ValueError(f'Unknown element symbol: {comp.element}')
            element_masses.append(atomic_masses[atomic_numbers[comp.element]])
        atomic_blanks = atomic_fractions.count(None)
        mass_blanks = mass_fractions.count(None)
        if atomic_blanks == 1:
            index = atomic_fractions.index(None)
            balance = 1 - np.nansum(np.array(atomic_fractions, dtype=np.float64))
            self.elemental_composition[index].atomic_fraction = balance
            self.elemental_composition[index].normalize(archive, logger)
            atomic_fractions[index] = balance
            atomic_blanks = 0
        if mass_blanks == 1:
            index = mass_fractions.index(None)
            balance = 1 - np.nansum(np.array(mass_fractions, dtype=np.float64))
            self.elemental_composition[index].mass_fraction = balance
            self.elemental_composition[index].normalize(archive, logger)
            mass_fractions[index] = balance
            mass_blanks = 0
        if atomic_blanks == 0 and mass_blanks == len(mass_fractions):
            masses = np.array(atomic_fractions) * np.array(element_masses)
            mass_fractions = masses / masses.sum()
            for index, mass_fraction in enumerate(mass_fractions):
                self.elemental_composition[index].mass_fraction = mass_fraction
                self.elemental_composition[index].normalize(archive, logger)
        if mass_blanks == 0 and atomic_blanks == len(atomic_fractions):
            atoms = np.array(mass_fractions) / np.array(element_masses)
            atomic_fractions = atoms / atoms.sum()
            for index, atomic_fraction in enumerate(atomic_fractions):
                self.elemental_composition[index].atomic_fraction = atomic_fraction
                self.elemental_composition[index].normalize(archive, logger)

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        Will attempt to fill mass fractions or
        atomic fractions if left blank.
        """
        super().normalize(archive, logger)

        if len(self.elemental_composition) > 0:
            self._fill_fractions(archive, logger)


class Instrument(Entity):
    """
    A base section that can be used for instruments.
    """

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        Adds the name of the instrument to the `results.eln.instruments` list.
        """
        super().normalize(archive, logger)

        if self.name:
            if archive.results.eln.instruments is None:
                archive.results.eln.instruments = []
            archive.results.eln.instruments.append(self.name)


class InstrumentReference(EntityReference):
    """
    A section used for referencing an Instrument.
    """

    reference = Quantity(
        type=Instrument,
        description='A reference to a NOMAD `Instrument` entry.',
        a_eln=ELNAnnotation(
            component='ReferenceEditQuantity',
            label='Instrument reference',
        ),
    )


class Component(ArchiveSection):
    """
    A section for describing a component and its role in a composite system.
    """

    name = Quantity(
        type=str,
        description='A short name for the component.',
        a_eln=dict(component='StringEditQuantity', label='Component label'),
    )
    mass = Quantity(
        type=np.float64,
        description='The mass of the component.',
        unit='kg',
        a_eln=dict(component='NumberEditQuantity', defaultDisplayUnit='mg'),
    )
    mass_fraction = Quantity(
        type=np.float64,
        description='The mass fraction of the component in the composite system.',
        a_eln=dict(component='NumberEditQuantity'),
    )


class SystemComponent(Component):
    """
    A section for describing a system component and its role in a composite system.
    """

    system = Quantity(
        type=Reference(System.m_def),
        description='A reference to the component system.',
        a_eln=dict(component='ReferenceEditQuantity'),
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        If none is set, the normalizer
        will set the name of the component to be that of the referenced system if it has
        one.
        """
        super().normalize(archive, logger)
        if self.name is None and self.system is not None:
            self.name = self.system.name


class PureSubstanceSection(ArchiveSection):
    """
    A sub section for describing any elemental, molecular or single phase pure substance.
    """

    name = Quantity(
        type=str,
        description='A short name for the substance.',
        a_eln=dict(component='StringEditQuantity', label='Substance name'),
    )
    iupac_name = Quantity(
        type=str,
        description='IUPAC name.',
        a_eln=dict(component='StringEditQuantity'),
    )
    molecular_formula = Quantity(
        type=str,
        description='Molecular formula.',
        a_eln=dict(component='StringEditQuantity'),
    )
    molecular_mass = Quantity(
        type=np.dtype(np.float64),
        links=['https://en.wikipedia.org/wiki/Molecular_mass'],
        unit='Da',
        description="""
        The mass of the most likely isotopic composition for a single molecule,
        corresponding to the most intense ion/molecule peak in a mass spectrum.
        """,
        a_eln=dict(
            component='NumberEditQuantity',
            defaultDisplayUnit='Da',
        ),
    )
    molar_mass = Quantity(
        type=np.dtype(np.float64),
        links=['https://en.wikipedia.org/wiki/Molar_mass'],
        unit='g/mol',
        description="""
        The molar mass is the sum of all atomic masses of the constituent atoms in a
        compound, measured in g/mol. In the absence of explicit isotope labelling,
        averaged natural abundance is assumed. If an atom bears an explicit isotope label,
        100%% isotopic purity is assumed at this location.
        """,
        a_eln=dict(
            component='NumberEditQuantity',
            defaultDisplayUnit='g/mol',
        ),
    )
    monoisotopic_mass = Quantity(
        type=np.dtype(np.float64),
        links=['https://en.wikipedia.org/wiki/Monoisotopic_mass'],
        unit='Da',
        description="""
        The mass of a molecule, calculated using the mass of the most abundant isotope of
        each element.
        """,
        a_eln=dict(
            component='NumberEditQuantity',
            defaultDisplayUnit='Da',
        ),
    )
    inchi = Quantity(
        type=str,
        description='Inchi.',
        a_eln=dict(component='StringEditQuantity'),
    )
    inchi_key = Quantity(
        type=str,
        description='Inchi key.',
        a_eln=dict(component='StringEditQuantity'),
    )
    smile = Quantity(
        type=str,
        description='Smile.',
        a_eln=dict(component='StringEditQuantity'),
    )
    canonical_smile = Quantity(
        type=str,
        description='Canonical smile.',
        a_eln=dict(component='StringEditQuantity'),
    )
    cas_number = Quantity(
        type=str,
        description='CAS number.',
        a_eln=dict(component='StringEditQuantity'),
    )


class PureSubstanceComponent(Component):
    """
    A section for describing a substance component and its role in a composite system.
    """

    substance_name = Quantity(
        type=str,
        description="""
        The name of the substance within the section where this component is contained.
        """,
        a_eln=dict(component='StringEditQuantity'),
    )
    pure_substance = SubSection(
        section_def=PureSubstanceSection,
        description="""
        Section describing the pure substance that is the component.
        """,
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        If none is set, the
        normalizer will set the name of the component to be the molecular formula of the
        substance.
        """
        super().normalize(archive, logger)
        if self.substance_name and self.pure_substance is None:
            self.pure_substance = PureSubstanceSection(name=self.substance_name)
        if self.name is None and self.pure_substance is not None:
            self.name = self.pure_substance.molecular_formula


def elemental_composition_from_formula(formula: Formula) -> list[ElementalComposition]:
    """
    Help function for generating list of `ElementalComposition` instances from
    `nomad.atomutils.Formula` item

    Args:
        formula (Formula): The `nomad.atomutils.Formula` item

    Returns:
        List[ElementalComposition]: List of filled `ElementalComposition` items
    """
    mass_fractions = formula.mass_fractions()
    return [
        ElementalComposition(
            element=element,
            atomic_fraction=fraction,
            mass_fraction=mass_fractions[element],
        )
        for element, fraction in formula.atomic_fractions().items()
    ]


class CompositeSystem(System):
    """
    A base section for a material systems composed of components.
    Each component of the composite system is of a (sub)type of `System`.
    """

    components = SubSection(
        description="""
        A list of all the components of the composite system containing a name, reference
        to the system section and mass of that component.
        """,
        section_def=Component,
        repeats=True,
    )

    @staticmethod
    def _atomic_to_mass(
        composition: list[ElementalComposition], mass: float
    ) -> dict[str, float]:
        """
        Private static method for converting list of ElementalComposition objects to
        dictionary of element masses with the element symbol as key and mass as value.

        Args:
            composition (List[ElementalComposition]): List of ElementalComposition objects
            containing the element symbol and the atomic fraction of that element
            respectively.
            mass (float): The total mass of that component which is simply multiplied with
            the atomic fractions.

        Returns:
            Dict[str, float]: Dictionary of element masses with the element symbol as key
            and the mass as (float) value. If any atomic fraction is None, all masses will
            be None.
        """
        atomic_fractions = []
        atom_masses = []
        for comp in composition:
            if None in [mass, comp.element, comp.atomic_fraction]:
                return {comp.element: None for comp in composition if comp.element}
            atomic_fractions.append(comp.atomic_fraction)
            if comp.element not in atomic_numbers:
                raise ValueError(f'Unknown element symbol: {comp.element}')
            atom_masses.append(atomic_masses[atomic_numbers[comp.element]])
        masses = np.array(atomic_fractions) * np.array(atom_masses)
        mass_array = mass * masses / masses.sum()
        mass_dict: dict[str, float] = {}
        for c, m in zip(composition, mass_array):
            if c.element in mass_dict:
                mass_dict[c.element] += m
            else:
                mass_dict[c.element] = m
        return mass_dict

    @staticmethod
    def _mass_to_atomic(mass_dict: dict[str, float]) -> list[ElementalComposition]:
        """
        Private static method for converting dictionary of elements with their masses
        to a list of ElementalComposition objects containing atomic fractions.

        Args:
            mass_dict (Dict[str, float]): Dictionary of element masses with the element
            symbol as key and the mass as (float) value.

        Returns:
            List[ElementalComposition]: List of ElementalComposition objects containing
            the element symbol and the atomic fraction of that element respectively. If
            any input mass is None all the "atomic_fraction" properties are set to None.
        """
        atoms = []
        elements = []
        for element, mass in mass_dict.items():
            elements.append(element)
            if mass is None:
                return [ElementalComposition(element=symbol) for symbol in mass_dict]
            else:
                atoms.append(mass / atomic_masses[atomic_numbers[element]])
        atoms_array: np.ndarray = np.array(atoms).astype(float)
        total_atoms = atoms_array.sum()
        atomic_fractions = atoms_array / total_atoms

        return [
            ElementalComposition(element=symbol, atomic_fraction=atomic_fraction)
            for atomic_fraction, symbol in zip(atomic_fractions, elements)
        ]

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        If the elemental composition list is
        empty, the normalizer will iterate over the components and extract all the
        elements for populating the elemental composition list. If masses are provided for
        all components and the elemental composition of all components contain atomic
        fractions the normalizer will also calculate the atomic fractions for the
        composite system. The populated elemental composition list is added to the results
        by the normalizer in the `System` super class.
        """
        if logger is None:
            logger = utils.get_logger(__name__)
        mass_fractions = [component.mass_fraction for component in self.components]
        if mass_fractions.count(None) == 1:
            empty_index = mass_fractions.index(None)
            mass_fractions.pop(empty_index)
            self.components[empty_index].mass_fraction = 1 - sum(mass_fractions)
        if not self.elemental_composition:
            mass_dict: dict[str, float] = {}
            if any(component.mass is None for component in self.components):
                if all(component.mass is None for component in self.components):
                    masses = [component.mass_fraction for component in self.components]
                else:

                    def calculate_mass(component, first_nonzero_mass):
                        if component.mass is None:
                            if component.mass_fraction is not None:
                                return first_nonzero_mass * component.mass_fraction
                            return component.mass
                        else:
                            return component.mass.to(ureg.kilogram).magnitude

                    first_nonzero_mass = next(
                        component.mass.to(ureg.kilogram).magnitude
                        for component in self.components
                        if component.mass is not None
                    )
                    masses = [
                        calculate_mass(component, first_nonzero_mass)
                        for component in self.components
                    ]
            else:
                masses = [
                    component.mass.to(ureg.kilogram).magnitude
                    for component in self.components
                ]
            for component, mass in zip(self.components, masses):
                if isinstance(component, PureSubstanceComponent):
                    try:
                        formula = Formula(component.pure_substance.molecular_formula)
                        elemental_composition = elemental_composition_from_formula(
                            formula
                        )
                    except (AttributeError, ValueError):
                        elemental_composition = []
                elif isinstance(component, SystemComponent):
                    elemental_composition = component.system.elemental_composition
                else:
                    elemental_composition = []
                component_dict = self._atomic_to_mass(elemental_composition, mass)
                for element, mass in component_dict.items():
                    if element in mass_dict and mass is not None:
                        if mass_dict[element] is not None:
                            mass_dict[element] += mass
                    else:
                        mass_dict[element] = mass
            self.elemental_composition = self._mass_to_atomic(mass_dict)
        for comp in self.elemental_composition:
            comp.normalize(archive, logger)

        super().normalize(archive, logger)


class CompositeSystemReference(EntityReference):
    """
    A section used for referencing a CompositeSystem.
    """

    reference = Quantity(
        type=CompositeSystem,
        description='A reference to a NOMAD `CompositeSystem` entry.',
        a_eln=ELNAnnotation(
            component='ReferenceEditQuantity',
            label='Composite system reference',
        ),
    )


class ProcessStep(ActivityStep):
    """
    Any dependant step of a `Process`.
    """

    duration = Quantity(
        type=float,
        unit='second',
        description="""
        The duration time of the process step.
        """,
        a_eln=ELNAnnotation(
            component='NumberEditQuantity',
            defaultDisplayUnit='second',
        ),
    )


class Process(Activity):
    """
    A planned process which results in physical changes in a specified input material.
    [ obi : prs obi : mc obi : fg obi : jf obi : bp ]

    Synonyms:
     - preparative method
     - sample preparation
     - sample preparative method
     - material transformations
    """

    m_def = Section(
        links=['http://purl.obolibrary.org/obo/OBI_0000094'],
    )
    end_time = Quantity(
        type=Datetime,
        description='The date and time when this process was finished.',
        a_eln=dict(component='DateTimeEditQuantity', label='Ending time'),
    )
    steps = SubSection(
        section_def=ProcessStep,
        description="""
        An ordered list of all the dependant steps that make up this activity.
        """,
        repeats=True,
    )
    instruments = SubSection(
        section_def=InstrumentReference,
        description="""
        A list of all the instruments and their role in this process.
        """,
        repeats=True,
    )
    samples = SubSection(
        section_def=CompositeSystemReference,
        description="""
        The samples as that have undergone the process.
        """,
        repeats=True,
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        - Sets the start time for each step in `self.steps` if not already set, based on the `datetime` and `duration` fields.
        - Sets the `end_time` field to the calculated end time if it is not already set.
        - Updates the `archive.workflow2.outputs` list with links to the samples processed.
        """
        super().normalize(archive, logger)
        if (
            self.datetime is not None
            and all(step.duration is not None for step in self.steps)
            and any(step.start_time is None for step in self.steps)
        ):
            start = self.datetime
            for step in self.steps:
                if step.start_time is None:
                    step.start_time = start
                start += datetime.timedelta(seconds=step.duration.magnitude)
            if self.end_time is None and start > self.datetime:
                self.end_time = start
        archive.workflow2.outputs = [
            Link(name=sample.name, section=sample.reference) for sample in self.samples
        ]


class ActivityResult(ArchiveSection):
    """
    A section for the results of an `Activity`.
    """

    name = Quantity(
        type=str,
        description='A short and descriptive name for the result.',
        a_eln=dict(component='StringEditQuantity'),
    )


class AnalysisResult(ActivityResult):
    """
    A section for the results of an `Analysis` process.
    """

    pass


class Analysis(Activity):
    """
    A planned process that produces output data from input data.

    Synonyms:
     - data processing
     - data analysis
    """

    m_def = Section(links=['http://purl.obolibrary.org/obo/OBI_0200000'])
    inputs = SubSection(
        section_def=SectionReference,
        description='The input data of the analysis.',
        repeats=True,
    )
    outputs = SubSection(
        section_def=AnalysisResult,
        description='The output data of the analysis.',
        repeats=True,
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        - Updates the `archive.workflow2.inputs` list with links to the input data.
        - Updates the `archive.workflow2.outputs` list with links to the output data.
        """
        super().normalize(archive, logger)
        archive.workflow2.inputs = [
            Link(name=input.name, section=input.reference) for input in self.inputs
        ]
        archive.workflow2.outputs = [
            Link(name=output.name, section=output) for output in self.outputs
        ]


class SynthesisMethod(Process):
    """
    A method used to synthesise a sample.
    """

    m_def = Section(
        links=['http://purl.obolibrary.org/obo/CHMO_0001301'],
    )


class MeasurementResult(ActivityResult):
    """
    A section for the results of an `Measurement` process.
    """

    pass


class Measurement(Activity):
    """
    A planned process with the objective to produce information about the material entity
    that is the evaluant, by physically examining it or its proxies. [ obi : pppb ]
    """

    m_def = Section(
        links=['http://purl.obolibrary.org/obo/OBI_0000070'],
    )
    samples = SubSection(
        section_def=CompositeSystemReference,
        description="""
        A list of all the samples measured during the measurement.
        """,
        repeats=True,
    )
    instruments = SubSection(
        section_def=InstrumentReference,
        description="""
        A list of all the instruments and their role in this process.
        """,
        repeats=True,
    )
    results = SubSection(
        section_def=MeasurementResult,
        description="""
        The result of the measurement.
        """,
        repeats=True,
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        - Updates the `archive.workflow2.inputs` list with links to the input samples.
        - Updates the `archive.workflow2.outputs` list with links to the measurement results.
        """
        super().normalize(archive, logger)
        archive.workflow2.inputs = [
            Link(name=sample.name, section=sample.reference) for sample in self.samples
        ]
        archive.workflow2.outputs = [
            Link(name=result.name, section=result) for result in self.results
        ]


class PureSubstance(System):
    """
    A base section for any elemental, molecular, or single phase pure substance.
    """

    m_def = Section(
        links=['http://purl.obolibrary.org/obo/CHEBI_23367'],
    )
    name = Quantity(
        type=str,
        description='The name of the substance entry.',
        a_eln=dict(component='StringEditQuantity', label='Substance name'),
    )
    lab_id = Quantity(
        type=str,
        description="""
        A human readable substance ID that is at least unique for the lab.
        """,
        a_eln=dict(component='StringEditQuantity', label='Substance ID'),
    )
    description = Quantity(
        type=str,
        description="""
        A field for adding additional information about the substance that is not captured
        by the other quantities and subsections.
        """,
        a_eln=dict(
            component='RichTextEditQuantity',
            label='Detailed substance description',
        ),
    )
    pure_substance = SubSection(
        section_def=PureSubstanceSection,
        description="""
        Section with properties describing the substance.
        """,
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        This method will populate the results.material section and the elemental
        composition sub section using the molecular formula.
        """
        super().normalize(archive, logger)
        if logger is None:
            logger = utils.get_logger(__name__)
        if self.pure_substance and self.pure_substance.molecular_formula:
            if not archive.results:
                archive.results = Results()
            if not archive.results.material:
                archive.results.material = Material()
            try:
                formula = Formula(self.pure_substance.molecular_formula)
                try:
                    formula.populate(archive.results.material)
                except ValueError:
                    logger.info('Not overwriting existing results.material.')
                if not self.elemental_composition:
                    self.elemental_composition = elemental_composition_from_formula(
                        formula
                    )
            except Exception as e:
                logger.warn('Could not analyse chemical formula.', exc_info=e)


class PubChemPureSubstanceSection(PureSubstanceSection):
    """
    A section for pure substances existing as "compounds" in the PubChem database.
    """

    m_def = Section(
        label='PubChem Pure Substance Section',
    )
    pub_chem_cid = Quantity(
        type=int,
        a_eln=dict(
            component='NumberEditQuantity',
        ),
    )
    pub_chem_link = Quantity(
        type=str,
        a_eln=dict(
            component='StringEditQuantity',
        ),
    )

    def _populate_from_cid(self, logger: 'BoundLogger') -> None:
        """
        Private method for populating unfilled properties by searching the PubChem using
        the CID in `pub_chem_cid`.

        Args:
            logger (BoundLogger): A structlog logger.
        """
        properties = {
            'Title': 'name',
            'IUPACName': 'iupac_name',
            'MolecularFormula': 'molecular_formula',
            'ExactMass': 'molecular_mass',
            'MolecularWeight': 'molar_mass',
            'MonoisotopicMass': 'monoisotopic_mass',
            'InChI': 'inchi',
            'InChIKey': 'inchi_key',
            'SMILES': 'smile',
        }
        types = {  # Needed because PubChems API sometimes returns floats as strings
            'Title': str,
            'IUPACName': str,
            'MolecularFormula': str,
            'ExactMass': float,
            'MolecularWeight': float,
            'MonoisotopicMass': float,
            'InChI': str,
            'InChIKey': str,
            'SMILES': str,
        }
        response = pub_chem_api_get_properties(
            cid=self.pub_chem_cid, properties=properties
        )
        if not response.ok:
            msg = (
                f'Property request to PubChem failed with '
                f'{response.status_code} {response.reason}: {response.url}'
            )
            logger.warn(pub_chem_add_throttle_header(response, msg))
            return
        self.pub_chem_link = (
            f'https://pubchem.ncbi.nlm.nih.gov/compound/{self.pub_chem_cid}'
        )
        try:
            property_values = response.json()['PropertyTable']['Properties'][0]
        except (KeyError, IndexError):
            property_values = {}
        for property_name in properties:  # noqa
            if getattr(self, properties[property_name], None) is None:
                try:
                    property_value = property_values[property_name]
                    if not isinstance(property_value, types[property_name]):
                        property_value = types[property_name](property_value)
                    setattr(
                        self,
                        properties[property_name],
                        property_value,
                    )
                except KeyError:
                    logger.warn(
                        f'Property "{property_name}" missing from PubChem response.'
                    )
                except ValueError:
                    logger.warn(
                        f'Property "{property_name}" in PubChem response is not of '
                        f'the expected type "{types[property_name]}".'
                    )
        if self.cas_number is None:
            response = pub_chem_api_get_synonyms(cid=self.pub_chem_cid)
            if not response.ok:
                msg = (
                    f'Synonyms request to PubChem failed with '
                    f'{response.status_code} {response.reason}: {response.url}'
                )
                logger.warn(pub_chem_add_throttle_header(response, msg))
                return
            response_dict = response.json()
            try:
                synonyms = response_dict['InformationList']['Information'][0]['Synonym']
            except (KeyError, IndexError):
                synonyms = []
            for synonym in synonyms:
                if is_cas_rn(synonym):
                    self.cas_number = synonym
                    break

    def _pub_chem_search_unique(
        self, search: str, path: str, logger: 'BoundLogger'
    ) -> bool:
        """
        Private method for searching the PubChem API for CIDs using the provided `path`
        and `search` strings.

        Args:
            search (str): The string containing the search value.
            path (str): The path to search the string for.
            logger (BoundLogger): A structlog logger.

        Returns:
            bool: _description_
        """
        response = pub_chem_api_search(path=path, search=search)
        if response.status_code == 404:
            logger.info(f'No results for PubChem search for {path}="{search}".')
            return False
        elif not response.ok:
            msg = (
                f'PubChem search for {path}="{search}" failed with '
                f'{response.status_code} {response.reason}: {response.url}'
            )
            logger.warn(pub_chem_add_throttle_header(response, msg))
            return False
        try:
            cids = response.json()['IdentifierList']['CID']
        except KeyError:
            logger.warn('CID search request to PubChem response missing CID list.')
            return False
        if len(cids) == 0:
            return False
        elif len(cids) > 1:
            urls = [f'https://pubchem.ncbi.nlm.nih.gov/compound/{cid}' for cid in cids]
            logger.warn(
                f'Search for PubChem CID yielded {len(cids)} results: '
                f'{", ".join(urls)}. Using {urls[0]}'
            )
        self.pub_chem_cid = cids[0]
        return True

    def _find_cid(self, logger: 'BoundLogger') -> None:
        """
        Private method for finding the PubChem CID using the filled attributes in the
        following order:

        1. `smile`
        2. `canonical_smile`
        3. `inchi_key`
        4. `iupac_name`
        5. `name`
        6. `molecular_formula`

        The first hit will populate the `pub_chem_cid` attribute and return.

        Args:
            logger ('BoundLogger'): A structlog logger.
        """
        for search, path in (
            (self.smile, 'smiles'),
            (self.canonical_smile, 'smiles'),
            (self.inchi_key, 'inchikey'),
            (self.iupac_name, 'name'),
            (self.name, 'name'),
            (self.molecular_formula, 'fastformula'),
            (self.name, 'fastformula'),
            (self.cas_number, 'name'),
        ):
            if search and self._pub_chem_search_unique(search, path, logger):
                self._populate_from_cid(logger)
                return

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        This method will attempt to get data on the substance instance from the PubChem
        PUG REST API: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
        If a PubChem CID is specified the details are retrieved directly.
        Otherwise a search query is made for the filled attributes in the following order:

        1. `smile`
        2. `canonical_smile`
        3. `inchi_key`
        4. `iupac_name`
        5. `name`
        6. `molecular_formula`
        7. `cas_number`
        """
        if logger is None:
            logger = utils.get_logger(__name__)

        if self.pub_chem_cid:
            if any(getattr(self, value) is None for value in self.m_def.all_quantities):
                self._populate_from_cid(logger)
        else:
            self._find_cid(logger)

        super().normalize(archive, logger)


class CASExperimentalProperty(ArchiveSection):
    """
    A section for experimental properties retrieved from the CAS API.
    """

    name = Quantity(
        type=str,
        description='CAS experimental property name.',
    )
    property = Quantity(
        type=str,
        description='CAS experimental property.',
    )
    sourceNumber = Quantity(
        type=str,
        description='CAS experimental property source.',
    )


class CASPropertyCitation(ArchiveSection):
    """
    A section for citations of the experimental properties retrieved from the CAS API.
    """

    docUri = Quantity(
        type=str,
        description='CAS property citation document uri.',
    )
    # TODO: Correct the description keyword in a separate metadata behavior fix.
    sourceNumber = Quantity(
        type=int,
        decription='CAS property citation source number.',  # typos: ignore
    )
    source = Quantity(
        type=str,
        description='CAS property citation source.',
    )


class CASPureSubstanceSection(PureSubstanceSection):
    """
    A base section for any `PureSubstance` with a CAS number.
    """

    m_def = Section(
        label='CAS Pure Substance Section',
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
        a_browser=dict(adaptor='RawFileAdaptor', label='Image of substance'),
    )
    cas_experimental_properties = SubSection(
        section_def=CASExperimentalProperty,
        repeats=True,
    )
    cas_property_citations = SubSection(
        section_def=CASPropertyCitation,
        repeats=True,
    )
    cas_synonyms = Quantity(
        type=str,
        shape=['*'],
        description='CAS synonyms.',
    )

    def _populate_from_cas(self, archive, logger: 'BoundLogger') -> None:
        """
        Private method for populating the attributes from a call to the CAS API using
        the `cas_number`.
        Will overwrite existing CAS attributes if the query provides a value for them.
        I.e. all attributes that begin with `cas_`.

        Args:
            archive (EntryArchive): The archive that is being normalized.
            logger ('BoundLogger'): A structlog logger.
        """
        response = cas_api_details(cas_rn=self.cas_number)
        if response.ok:
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
        elif response.status_code is not None and response.status_code >= 500:
            logger.warn('Remote server error on CAS API call.')
        else:
            logger.warn(
                f'Unexpected response code: {response.status_code} from CAS API call.'
            )

    def _cas_search_unique(self, search: str, archive, logger: 'BoundLogger') -> bool:
        """
        Private method for performing a search of the CAS API and populating the
        attributes with the CAS number of any unique search result.

        Args:
            search (str): The string to search the CAS API with.
            archive (EntryArchive): The archive that is being normalized.
            logger ('BoundLogger'): A structlog logger.

        Returns:
            bool: Whether the search found a unique result.
        """
        response = cas_api_search(search=search)
        if response.ok:
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

    def _find_cas(self, archive, logger: 'BoundLogger') -> None:
        """
        Private method for finding the CAS number using the filled attributes in the
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
            logger ('BoundLogger'): A structlog logger.
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

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        This method will attempt to get data on the pure substance instance from the CAS
        API: https://commonchemistry.cas.org/api-overview
        If a CAS number is specified the details are retrieved directly.
        Otherwise a search query is made for the filled attributes in the following order:

        1. `cas_name`
        2. `inchi`
        3. `inchi_key`
        4. `smile`
        5. `canonical_smile`
        6. `name`
        """
        if logger is None:
            logger = utils.get_logger(__name__)

        if self.cas_number:
            self.cas_number = self.cas_number.strip()
            if any(getattr(self, value) is None for value in self.m_def.all_quantities):
                self._populate_from_cas(archive, logger)
        else:
            self._find_cas(archive, logger)

        super().normalize(archive, logger)


class ReadableIdentifiers(ArchiveSection):
    """
    A base section that can be used to generate readable IDs.
    If the `owner`, `short_name`, `institute`, and `datetime`
    quantities are provided, the lab_id will be automatically created as a combination
    of these four quantities.
    """

    institute = Quantity(
        type=str,
        description="""
        Alias/short name of the home institute of the owner, i.e. *HZB*.
        """,
        a_eln=dict(component='StringEditQuantity'),
    )
    owner = Quantity(
        type=str,
        shape=[],
        description="""
        Alias for the owner of the identified thing. This should be unique within the
        institute.
        """,
        a_eln=dict(component='StringEditQuantity'),
    )
    datetime = Quantity(
        type=Datetime,
        description="""
        A datetime associated with the identified thing. In case of an `Activity`, this
        should be the starting time and, in case of an `Entity`, the creation time.
        """,
        a_eln=dict(component='DateTimeEditQuantity'),
    )
    short_name = Quantity(
        type=str,
        description="""
        A short name of the the identified thing (e.g. the identifier scribed on the
        sample, the process number, or machine name), e.g. 4001-8, YAG-2-34.
        This is to be managed and decided internally by the labs, although we recommend
        to avoid the following characters in it: "_", "/", "\\" and ".".
        """,
        a_eln=dict(component='StringEditQuantity'),
    )
    lab_id = Quantity(
        type=str,
        description="""
        Full readable id. Ideally a human readable id convention, which is simple,
        understandable and still have chances of becoming unique.
        If the `owner`, `short_name`, `ìnstitute`, and `datetime` are provided, this will
        be formed automatically by joining these components by an underscore (_).
        Spaces in any of the individual components will be replaced with hyphens (-).
        An example would be hzb_oah_20200602_4001-08.
        """,
        a_eln=dict(component='StringEditQuantity', label='ID'),
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        If owner is not filled the field will be filled by the first two letters of
        the first name joined with the first two letters of the last name of the author.
        If the institute is not filled a institute abbreviations will be constructed from
        the author's affiliation.

        If no datetime is filled, the datetime will be taken from the `datetime`
        property of the parent, if it exists, otherwise the creation time of the entry
        will be used.

        If no short name is filled, the name will be taken from the parent name, if it
        exists, otherwise it will be taken from the archive metadata entry name, if it
        exists, and finally if no other options are available it will use the name of the
        mainfile.
        """
        super().normalize(archive, logger)

        if self.owner is None or self.institute is None:
            author = archive.metadata.main_author
            if author and self.owner is None:
                first_short = unidecode(author.first_name)[:2]
                last_short = unidecode(author.last_name)[:2]
                self.owner = first_short + last_short
            if author and self.institute is None and author.affiliation:
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

        if self.datetime is None:
            if self.m_parent and getattr(self.m_parent, 'datetime', None):
                self.datetime = self.m_parent.datetime
            else:
                self.datetime = default_datetime(archive)

        if self.short_name is None:
            if self.m_parent and getattr(self.m_parent, 'name', None):
                name = self.m_parent.name
            elif archive.metadata.entry_name:
                name = archive.metadata.entry_name
            else:
                name = archive.metadata.mainfile
            self.short_name = re.sub(r'_|\s', '-', name.split('.')[0])

        if self.institute and self.short_name and self.owner and self.datetime:
            creation_date = self.datetime.strftime('%Y%m%d')
            owner = self.owner.replace(' ', '-')
            lab_id_list = [self.institute, owner, creation_date, self.short_name]
            self.lab_id = '_'.join(lab_id_list)

        if not archive.results:
            archive.results = Results(eln=ELN())
        if not archive.results.eln:
            archive.results.eln = ELN()

        if self.lab_id:
            if not archive.results.eln.lab_ids:
                archive.results.eln.lab_ids = []
            if self.lab_id not in archive.results.eln.lab_ids:
                archive.results.eln.lab_ids.append(self.lab_id)

        if not archive.results.eln.sections:
            archive.results.eln.sections = []
        archive.results.eln.sections.append(self.m_def.name)


class PublicationReference(ArchiveSection):
    """
    A base section that can be used for references.
    """

    DOI_number = Quantity(
        type=str,
        shape=[],
        description="""
            The DOI number referring to the published paper or dataset where the data can be found.
            Examples:
            10.1021/jp5126624
            10.1016/j.electacta.2017.06.032
        """,
        a_eln=dict(component='EnumEditQuantity', props=dict(suggestions=[])),
    )

    publication_authors = Quantity(
        type=str,
        shape=['*'],
        description="""
            The authors of the publication.
            If several authors, end with et al. If the DOI number is given correctly,
            this will be extracted automatically from www.crossref.org
        """,
    )
    publication_date = Quantity(
        type=Datetime,
        shape=[],
        description="""
            Publication date.
            If the DOI number is given correctly,
            this will be extracted automatically from www.crossref.org
        """,
    )
    journal = Quantity(
        type=str,
        shape=[],
        description="""
            Name of the journal where the data is published.
            If the DOI number is given correctly,
            this will be extracted automatically from www.crossref.org
        """,
    )
    publication_title = Quantity(
        type=str,
        shape=[],
        description="""
            Title of the publication.
            If the DOI number is given correctly,
            this will be extracted automatically from www.crossref.org
        """,
    )

    def normalize(self, archive, logger: 'BoundLogger') -> None:
        """
        - If a DOI number is provided, retrieves publication details from the CrossRef API.
        - Populates the `publication_authors`, `journal`, `publication_title`, and `publication_date` fields based on the CrossRef response.
        - Ensures the DOI number has the prefix `https://doi.org/`.
        - Updates the archive's metadata references with the DOI number if it is not already present.
        """
        super().normalize(archive, logger)
        import requests

        from nomad.datamodel.datamodel import EntryMetadata

        # Parse journal name, lead author and publication date from crossref
        if self.DOI_number:
            try:
                url = f'https://api.crossref.org/works/{self.DOI_number}?mailto=contact@nomad-lab.eu'
                timeout = 5
                r = requests.get(url, timeout=timeout)
                if r.status_code == 200:
                    temp_dict = r.json()
                    # make sure the doi has the prefix https://doi.org/
                    if self.DOI_number.startswith('10.'):
                        self.DOI_number = 'https://doi.org/' + self.DOI_number
                    self.publication_authors = [
                        f'{v["given"]} {v["family"]}'
                        for v in temp_dict['message']['author']
                    ]
                    self.journal = temp_dict['message']['container-title'][0]
                    self.publication_title = temp_dict['message']['title'][0]

                    # Get publication date in order of preference
                    date_parts = None
                    if 'published' in temp_dict['message']:
                        date_parts = temp_dict['message']['published']['date-parts']
                    elif 'published-print' in temp_dict['message']:
                        date_parts = temp_dict['message']['published-print'][
                            'date-parts'
                        ]
                    elif 'published-online' in temp_dict['message']:
                        date_parts = temp_dict['message']['published-online'][
                            'date-parts'
                        ]
                    if date_parts and date_parts[0]:
                        if len(date_parts[0]) == 1:
                            date_parts[0].extend(
                                [1, 1]
                            )  # add month (January) and day (1) if missing
                        elif len(date_parts[0]) == 2:
                            date_parts[0].append(1)  # add day (1) if missing
                        self.publication_date = datetime.datetime(*date_parts[0])

                    if not archive.metadata:
                        archive.metadata = EntryMetadata()
                    if not archive.metadata.references:
                        archive.metadata.references = []
                    # if any item in the references list starts with 10. add the prefix https://doi.org/
                    for i, ref in enumerate(archive.metadata.references):
                        if ref.startswith('10.'):
                            archive.metadata.references[i] = 'https://doi.org/' + ref
                    if self.DOI_number not in archive.metadata.references:
                        archive.metadata.references.append(self.DOI_number)

                else:
                    logger.warning(f'Could not parse DOI number {self.DOI_number}')
            except Exception as e:
                logger.warning(f'Could not parse crossref for {self.DOI_number}')
                logger.warning(str(e))


class HDF5Normalizer(ArchiveSection):
    def normalize(self, archive, logger):
        super().normalize(archive, logger)
        h5_re = re.compile(
            r'.*\.('
            r'h5|hdf5|he5|h5part|nxs|mat|nc4?'
            r')$',
            re.IGNORECASE,
        )

        for quantity_name, quantity_def in self.m_def.all_quantities.items():
            if (quantity_def.type is str or isinstance(quantity_def.type, m_str)) and (
                match := re.match(
                    h5_re,
                    '' if self.get(quantity_name) is None else self.get(quantity_name),
                )
            ):
                h5_filepath = os.path.join(
                    archive.m_context.upload_files.os_path, 'raw', match.group(0)
                )
                with h5py.File(h5_filepath, 'r') as h5_file:
                    self.hdf5_parser(h5_file, logger)

    def hdf5_parser(self, h5_file, logger):
        if logger is None:
            logger = utils.get_logger(__name__)

        mapping = create_custom_mapping(self.m_def, HDF5Annotation, 'hdf5', 'path')  # type: ignore
        for custom_quantities in mapping:
            h5_path = custom_quantities[0]
            quantity_mapper = custom_quantities[1]
            try:
                dataset = h5_file[h5_path]
                raw = dataset[()] if dataset.shape == () else dataset[:]

                if isinstance(raw, np.ndarray) and isinstance(
                    raw.flat[0], bytes | bytearray
                ):
                    raw = dataset.asstr()[:]

                elif isinstance(raw, bytes | bytearray):
                    raw = dataset.asstr()[()]

                quantity_mapper(self, raw)
            except Exception as e:
                logger.warning(
                    f'Could not map the path {h5_path}.'
                    f'Either the path does not exist or the custom quantity cannot hold the content of'
                    f' h5 dataset',
                    exc_info=e,
                )
