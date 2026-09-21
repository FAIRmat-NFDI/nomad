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

"""
This elasticsearch extension for the Metainfo allows to define how quantities are
added to Elasticsearch indices.

This extension supports a single search index: ``entry_index``, holding documents of the
``entry_type`` document type.

The document type is a subset of the metainfo schema; documents have the exact same
structure as archives, but with only some of the quantities. Which quantities are in these
documents can be defined in the metainfo by using the :class:`Elasticsearch` annotation on
quantity definitions.

Entry documents start with the metainfo entry root section.

Here is a small metainfo example:

.. code-block:: python

    class Entry(MSection):

        entry_id = Quantity(
            type=str,
            a_elasticsearch=Elasticsearch())

        upload_create_time = Quantity(
            type=Datetime,
            a_elasticsearch=Elasticsearch())

        results = SubSection(sub_section=Results.m_def, a_elasticsearch=Elasticsearch())


    class Results(MSection):

        material = SubSection(sub_section=Material.m_def, a_elasticsearch=Elasticsearch())
        properties = SubSection(sub_section=Properties.m_def, a_elasticsearch=Elasticsearch())


    class Material(MSection):

        material_id = Quantity(
            type=str,
            a_elasticsearch=Elasticsearch())

        formula = Quantity(
            type=str,
            a_elasticsearch=[
                Elasticsearch(),
                Elasticsearch(field='text', mapping='text')])


    class Properties(MSection):

        available_properties = Quantity(
            type=str, shape=['*'],
            a_elasticsearch=Elasticsearch())

        band_gap = Quantity(
            type=float, unit='J',
            a_elasticsearch=Elasticsearch())


The resulting index with a single entry in it would look like this:

.. code-block:: json

    [
        {
            "entry_id": "de54f1",
            "upload_create_time": "2021-02-01 01:23:12",
            "results": {
                "material": {
                    "material_id": "23a8bf",
                    "formula": "H2O"
                },
                "properties": {
                    "available_properties": ["dos", "bs", "band_gap", "energy_total_0"],
                    "band_gap": 0.283e-12
                }
            }
        }
    ]

You can freely define sub-sections and quantities. The only fixed structure that is
required from the metainfo is that the root section has an ``entry_id``.

This extension resolves references during indexing and basically treats referenced
sub-sections as if they were direct sub-sections.

.. autofunction:: index_entry
.. autofunction:: index_entries
.. autofunction:: create_indices


.. autoclass:: Elasticsearch
.. autoclass:: DocumentType
.. autoclass:: Index
"""

import json
import math
import random
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, Optional, cast

from elasticsearch.dsl import Q
from pint import Quantity as PintQuantity

from nomad import utils
from nomad.config import config
from nomad.config.models.plugins import SchemaPackageEntryPoint
from nomad.elastic_compat import TRANSPORT_ERROR_TYPES

from . import DefinitionAnnotation
from .data_type import Datatype, to_elastic_type
from .metainfo import (
    Definition,
    MSection,
    MSectionBound,
    Quantity,
    QuantityReference,
    Reference,
    SchemaPackage,
    Section,
)

if TYPE_CHECKING:
    from nomad.datamodel.datamodel import SearchableQuantity

schema_separator = '#'
dtype_separator = '#'
yaml_prefix = 'entry_id:'
nexus_prefix = 'pynxtools.nomad.schema'
ES_DEFAULT_PAYLOAD_SIZE = 100  # in MB


class PayloadTooLarge(Exception):
    """Raised when a request's payload exceeds Elasticsearch's request size limit (100MB)."""


def _estimate_bytes(obj) -> int | None:
    try:
        if obj is None:
            return None
        if isinstance(obj, bytes | bytearray):
            return len(obj)
        if isinstance(obj, str):
            return len(obj.encode('utf-8'))
        if isinstance(obj, list | tuple):
            total = 0
            for item in obj:
                if isinstance(item, bytes | bytearray):
                    b = item
                elif isinstance(item, str):
                    b = item.encode('utf-8')
                else:
                    b = json.dumps(item, separators=(',', ':')).encode('utf-8')
                total += len(b) + 1
            return total
        if isinstance(obj, dict):
            return len(json.dumps(obj, separators=(',', ':')).encode('utf-8'))
    except Exception:
        return None
    return None


class DocumentType:
    """
    DocumentType allows to create Elasticsearch index mappings and documents based on
    Metainfo definitions and instances. Genrally this class should not be used outside
    the elasticsearch_extension module.

    Attributes:
        root_section_def: The section definition that serves as the root for all documents.
            mapping: The elasticsearch mapping definition.
        indexed_properties: All definitions (quantities and sub sections) that are covered
            by documents of this type.
        quantities: All elasticsearch quantities that in documents of this type. A dictionary
            with full qualified name as key and :class:`Elasticsearch` annotations as
            values.
        metrics: All metrics in this document type. A dictionary with metric names as
            keys and tuples of elasticsearch metric aggregation and respective
            :class:`Elasticsearch` metainfo annotation as values.
        id_field: The quantity (and elasticsearch field) name that is used as unique
            identifier for this type of documents.
    """

    def __init__(self, name: str, id_field: str):
        self.name = name
        self.id_field = id_field
        self.root_section_def = None
        self.mapping: dict[str, Any] = None
        self.indexed_properties: set[Definition] = set()
        self.nested_object_keys: list[str] = []
        self.nested_sections: list[SearchQuantity] = []
        self.quantities: dict[str, SearchQuantity] = {}
        self.suggestions: dict[str, Elasticsearch] = {}
        self.metrics: dict[str, tuple[str, SearchQuantity]] = {}

    def _reset(self):
        self.indexed_properties.clear()
        self.nested_object_keys.clear()
        self.nested_sections.clear()
        self.quantities.clear()
        self.metrics.clear()

    def create_index_doc(self, root: MSection):
        """
        Creates an indexable document from the given archive.
        """
        suggestions: defaultdict = defaultdict(list)

        def transform(quantity, section, value, path):
            """
            Custom transform function that possibly transforms the indexed
            values and also gathers the suggestion values for later storage.
            """
            elasticsearch_annotations = quantity.m_get_annotations(
                Elasticsearch, as_list=True
            )
            for elasticsearch_annotation in elasticsearch_annotations:
                if elasticsearch_annotation.field is None:
                    if elasticsearch_annotation.suggestion:
                        # The suggestion values are saved into a temporary
                        # dictionary. The actual path of the data in the
                        # metainfo is used as a key. The suggestions will also
                        # take into account any given variants of the input.
                        transform_function = elasticsearch_annotation.value
                        variants = elasticsearch_annotation.variants
                        if transform_function is not None:
                            if variants:
                                suggestion_value = []
                                for variant in variants(value):
                                    suggestion_value.extend(transform_function(variant))
                                suggestion_value = list(set(suggestion_value))
                            else:
                                suggestion_value = transform_function(value)
                        else:
                            suggestion_value = value
                        section_path = section.m_path()[len(root.m_path()) :]
                        name = elasticsearch_annotation.property_name
                        if not isinstance(quantity.type, Datatype):
                            suggestion_path = f'{section_path}/{path}/{name}'
                        else:
                            suggestion_path = f'{section_path}/{name}'
                        suggestions[suggestion_path].extend(suggestion_value)
                    else:
                        transform_function = elasticsearch_annotation.value
                        if transform_function is not None:
                            return transform_function(section)

            return value

        def exclude(_p, _s):
            return _p not in self.indexed_properties

        result = root.m_to_dict(
            with_meta=False,
            include_defaults=True,
            include_derived=True,
            resolve_references=True,
            exclude=exclude,
            transform=transform,
        )

        # Add the collected suggestion values
        for path, value in suggestions.items():
            try:
                parts = path.split('/')
                section = result
                for part in parts[:-1]:
                    if part == '':
                        continue
                    try:
                        part = int(part)
                    except ValueError:
                        pass
                    section = section[part]
                section[parts[-1]] = value
            except KeyError:
                # TODO This typically happens when a suggestion is stored in a
                # referenced object that is in the metadata/results sections,
                # e.g. referenced authored that are stored in EELS DB
                # measurements
                logger = utils.get_logger(__name__, doc_type=self.name)
                logger.warn('could not add suggestion to es', path=path)

        # TODO deal with metadata
        metadata = result.get('metadata')
        if metadata is not None:
            del result['metadata']
            result.update(**metadata)

        return result

    def create_mapping(
        self,
        section_def: Section,
        prefix: str | None = None,
        auto_include_subsections: bool = False,
    ):
        """
        Creates an Elasticsearch mapping for the given root section. It traverses all
        sub-sections to create the mapping. It will not create the mapping for nested
        documents. These have to be created manually (e.g. by :func:`create_indices`).
        Will override the existing mapping.

        Arguments:
            section_def: The section definition to create a mapping for.
            prefix: The qualified name of the section within the search index. This
                is used to create the qualified names of quantities and sub-sections.
            auto_include_subsections: Considers all sub and sub sub sections regardless
                of any annotation in the sub section definitions. By default only
                sub sections with elasticsearch annotation are traversed.
        """

        mapping = self._create_mapping_recursive(
            section_def, prefix, auto_include_subsections
        )

        # Register all dynamic quantities
        self.reload_quantities_dynamic()

        return mapping

    def _create_mapping_recursive(
        self,
        section_def: Section,
        prefix: str | None = None,
        auto_include_subsections: bool = False,
        repeats: bool = False,
    ):
        mappings: dict[str, Any] = {}

        for quantity_def in section_def.all_quantities.values():
            elasticsearch_annotations = quantity_def.m_get_annotations(
                Elasticsearch, as_list=True
            )
            for elasticsearch_annotation in elasticsearch_annotations:
                if elasticsearch_annotation.dynamic:
                    continue
                if prefix is None:
                    qualified_name = quantity_def.name
                else:
                    qualified_name = f'{prefix}.{quantity_def.name}'
                if elasticsearch_annotation.suggestion:
                    self.suggestions[qualified_name] = elasticsearch_annotation
                is_section_reference = isinstance(quantity_def.type, Reference)
                is_section_reference &= not isinstance(
                    quantity_def.type, QuantityReference
                )
                if is_section_reference:
                    # Treat referenced sections as sub-sections
                    assert quantity_def.type.target_section_def is not None
                    # TODO e.g. viewers, entry_coauthors, etc. ... should be treated as multiple inner docs
                    # assert quantity_def.is_scalar

                    reference_mapping = self._create_mapping_recursive(
                        cast(Section, quantity_def.type.target_section_def),
                        prefix=qualified_name,
                        repeats=repeats,
                    )
                    if len(reference_mapping['properties']) > 0:
                        mappings[quantity_def.name] = reference_mapping
                else:
                    mapping = mappings.setdefault(
                        elasticsearch_annotation.property_name, {}
                    )
                    fields = elasticsearch_annotation.fields
                    if len(fields) > 0:
                        mapping.setdefault('fields', {}).update(**fields)
                    else:
                        mapping.update(**elasticsearch_annotation.mapping)

                self.indexed_properties.add(quantity_def)
                self._register(elasticsearch_annotation, prefix, repeats)

        for sub_section_def in section_def.all_sub_sections.values():
            annotation = sub_section_def.m_get_annotations(Elasticsearch)
            if annotation is None and not auto_include_subsections:
                continue

            assert not isinstance(annotation, list), (
                'sub sections can only have one elasticsearch annotation'
            )
            continue_with_auto_include_subsections = auto_include_subsections or (
                False if annotation is None else annotation.auto_include_subsections
            )

            if prefix is None:
                qualified_name = sub_section_def.name
            else:
                qualified_name = f'{prefix}.{sub_section_def.name}'

            # TODO deal with metadata
            qualified_name = re.sub(r'\.?metadata', '', qualified_name)
            qualified_name = None if qualified_name == '' else qualified_name

            sub_section_mapping = self._create_mapping_recursive(
                sub_section_def.sub_section,
                prefix=qualified_name,
                auto_include_subsections=continue_with_auto_include_subsections,
                repeats=repeats or sub_section_def.repeats,
            )

            nested = annotation is not None and annotation.nested
            if nested:
                sub_section_mapping['type'] = 'nested'

            if len(sub_section_mapping['properties']) > 0:
                if sub_section_def.name == 'metadata':
                    mappings.update(**sub_section_mapping['properties'])
                else:
                    mappings[sub_section_def.name] = sub_section_mapping
                self.indexed_properties.add(sub_section_def)
                if nested and qualified_name not in self.nested_object_keys:
                    self.nested_object_keys.append(qualified_name)

                    search_quantity = SearchQuantity(
                        annotation=annotation, prefix=prefix
                    )
                    self.nested_sections.append(search_quantity)
                    self.nested_object_keys.sort(key=len)

        self.mapping = dict(properties=mappings)

        return self.mapping

    def reload_quantities_dynamic(self) -> None:
        """Reloads the dynamically mapped quantities from the plugin schemas."""
        from nomad.datamodel.data import EntryData

        # Remove existing dynamic quantities
        for name, quantity in list(self.quantities.items()):
            if quantity.dynamic:
                del self.quantities[name]

        # Gather the list of enabled schema plugins. Raise error if duplicate
        # package name is encountered.
        package_names: set[str] = set()
        packages_from_plugins = {}
        for entry_point in config.plugins.entry_points.filtered_values():
            if isinstance(entry_point, SchemaPackageEntryPoint):
                instance = entry_point.load()
                assert isinstance(instance, SchemaPackage), (
                    f'Error loading entry point "{entry_point.id}": The load method of a schema package entry point must return a SchemaPackage instance'
                )
                packages_from_plugins[entry_point.id] = instance
        for name, package in SchemaPackage.registry.items():
            # If package has no name, it is empty and can be skipped.
            if not name:
                continue
            package_name = name.split('.')[0]
            if package_name in package_names:
                packages_from_plugins[name] = package

        # Use to get all quantities from the given definition, TODO: Circular
        # definitions are here avoided by simply keeping track of which
        # definitions have been used already in the current "branch". When
        # creating the dynamic quantities, this is the only way to prevent
        # infinite recursion, but it should be made possible in the GUI + search
        # API to query arbitrarily deep into the data structure.
        def get_all_quantities(
            m_def, prefix=None, branch=None, repeats=False, level=0, max_level=None
        ):
            if max_level == 0:
                return
            if branch is None:
                branch = set()
            for quantity_name, quantity in m_def.all_quantities.items():
                quantity_name = f'{prefix}.{quantity_name}' if prefix else quantity_name
                yield quantity, quantity_name, repeats, level
            for sub_section_def in m_def.all_sub_sections.values():
                if sub_section_def in branch:
                    continue
                new_branch = set(branch)
                new_branch.add(sub_section_def)
                name = sub_section_def.name
                repeats = sub_section_def.repeats
                full_name = f'{prefix}.{name}' if prefix else name
                yield from get_all_quantities(
                    sub_section_def.sub_section,
                    full_name,
                    new_branch,
                    repeats,
                    level + 1,
                    max_level - 1 if max_level is not None else None,
                )

        quantities_dynamic = {}
        for package in packages_from_plugins.values():
            for section in package.section_definitions:
                if isinstance(section, Section) and issubclass(
                    section.section_cls, EntryData
                ):
                    schema_name = section.qualified_name()
                    if schema_name.startswith('pynxtools'):
                        # Allow App searches for specific AppDefs:
                        selected_path = [
                            # TODO: remove 'Root' once the new pynxtools schema is used
                            'Root',
                            'Entry',
                            'Mpes',
                            'Mpes_arpes',
                            'Xps',
                            'Spm',
                            'Apm',
                            'Em',
                            'Optical_spectroscopy',
                            'Ellipsometry',
                            'Raman',
                        ]
                        if section.name in selected_path:
                            max_level = 3
                        else:
                            max_level = 0
                        selected_path = ['']
                    else:
                        selected_path = ['']
                        max_level = -1
                    for subsection in selected_path:
                        path_prefix = subsection
                        if subsection == '':
                            selected_section = section
                        else:
                            selected_section = section.all_sub_sections[
                                subsection
                            ].sub_section
                            path_prefix = path_prefix + '.'

                        for quantity_def, path, repeats, level in get_all_quantities(
                            selected_section, max_level=max_level
                        ):
                            annotation = create_dynamic_quantity_annotation(
                                quantity_def
                            )
                            if not annotation:
                                continue
                            # TODO: The quantities at the first level of the hierarchy get
                            # registered under both the topmost class name and the class
                            # that actually contains the quantity definition. This is a
                            # workaround to allow base class searches at the top level.
                            # NOTE: This implementation ignores classes that might exist
                            # between the topmost classs and the class that contains the
                            # definition, e.g. if the hierarchy is A->B->C, and C contains
                            # the definition, it won't be available under the class B.
                            class_names = (
                                [schema_name]
                                if level > 0
                                else list(
                                    {
                                        schema_name,
                                        quantity_def.m_parent.qualified_name(),
                                    }
                                )
                            )
                            for class_name in class_names:
                                full_name = f'data.{path_prefix}{path}{schema_separator}{class_name}'
                                search_quantity = SearchQuantity(
                                    annotation,
                                    qualified_name=full_name,
                                    repeats=repeats,
                                )
                                quantities_dynamic[full_name] = search_quantity

        self.quantities.update(quantities_dynamic)

    def _register(self, annotation, prefix, repeats):
        search_quantity = SearchQuantity(
            annotation=annotation, prefix=prefix, repeats=repeats
        )
        name = search_quantity.qualified_name

        assert (
            name not in self.quantities or self.quantities[name] == search_quantity
        ), f'Search quantity names must be unique: {name}'

        self.quantities[name] = search_quantity

        if annotation.metrics is not None:
            for name, metric in annotation.metrics.items():
                assert name not in self.metrics, f'Metric names must be unique: {name}'
                self.metrics[name] = (metric, search_quantity)

        annotation.search_quantity = search_quantity

    def __repr__(self):
        return self.name


class Index:
    """
    Allows to access an Elasticsearch index. It forwards method calls to Python's
    Elasticsearch package for Elasticsearch document APIs like search, index, get, mget,
    bulk, etc. It adds the necessary doc_type and index parameters for you.

    Arguments:
        doc_type: The :class:`DocumentType` instance that describes the document type
            of this index.
        index_config_key: The ``nomad.config.elastic`` config key that holds the name
            for this index.

    Attributes:
        elastic_client: The used Elasticsearch Python package client.
        index_name: The name of the index in Elasticsearch.
    """

    def __init__(self, doc_type: DocumentType, index_config_key: str):
        self.doc_type = doc_type
        self.index_config_key = index_config_key

    def __elasticsearch_operation(self, name: str, *args, **kwargs):
        if 'index' not in kwargs:
            kwargs['index'] = self.index_name

        try:
            return getattr(self.elastic_client, name)(*args, **kwargs)
        except TRANSPORT_ERROR_TYPES as e:
            status = getattr(e, 'status_code', None) or getattr(e, 'status', None)
            if status == 413:
                payload = kwargs.get('body', kwargs.get('operations', None))
                estimated_bytes = _estimate_bytes(payload)
                size_mb = (
                    f'{(estimated_bytes or 0) / 1_000_000:.1f}'
                    if estimated_bytes is not None
                    else 'unknown'
                )
                raise PayloadTooLarge(
                    f'Failed to index upload data in Elasticsearch during {name} operation due to too large payload '
                    f'({size_mb} MB). The payload for a single index operation is limited to '
                    f'{ES_DEFAULT_PAYLOAD_SIZE} MB by default.'
                ) from e
            raise

    @property
    def index_name(self):
        return getattr(config.elastic, self.index_config_key)

    @property
    def elastic_client(self):
        from nomad.infrastructure import elastic_client

        return elastic_client

    def __getattr__(self, name):
        if name not in ['get', 'index', 'mget', 'bulk', 'search']:
            return super().__getattribute__(name)

        def wrapper(*args, **kwargs):
            return self.__elasticsearch_operation(name, *args, **kwargs)

        return wrapper

    def create_index(self, upsert: bool = False):
        """Initially creates the index with the mapping of its document type."""
        assert self.doc_type.mapping is not None, 'The mapping has to be created first.'
        logger = utils.get_logger(__name__, index=self.index_name)
        if not self.elastic_client.indices.exists(index=self.index_name):
            # TODO the settings emulate the path_analyzer used in the v0 elasticsearch_dsl
            # based index, configured by nomad.datamodel.datamodel::path_analyzer
            self.elastic_client.indices.create(
                index=self.index_name,
                body={
                    'settings': {
                        'analysis': {
                            'analyzer': {
                                'path_analyzer': {
                                    'tokenizer': 'path_tokenizer',
                                    'type': 'custom',
                                }
                            },
                            'tokenizer': {
                                'path_tokenizer': {'pattern': '/', 'type': 'pattern'}
                            },
                        }
                    },
                    'mappings': self.doc_type.mapping,
                },
            )
            logger.info('elasticsearch index created')
        elif upsert:
            self.elastic_client.indices.put_mapping(
                index=self.index_name, body=self.doc_type.mapping
            )
            logger.info('elasticsearch index updated')
        else:
            logger.info('elasticsearch index exists')

    def delete(self):
        if self.elastic_client.indices.exists(index=self.index_name):
            self.elastic_client.indices.delete(index=self.index_name)

    def refresh(self):
        self.elastic_client.indices.refresh(index=self.index_name)


# TODO type 'doc' because it's the default used by elasticsearch_dsl and the v0 entries index.
# 'entry' would be more descriptive.
entry_type = DocumentType('doc', id_field='entry_id')

entry_index = Index(entry_type, index_config_key='entries_index')


def get_tokenizer(regex):
    """Returns a function that tokenizes a given string using the provided
    regular epression.
    """

    def tokenizer(value):
        tokens = []
        if value:
            tokens.append(value)
            for match in re.finditer(regex, value):
                if match:
                    token = value[match.end() :]
                    if token != '':
                        # Notice how we artificially extend the token by taking the
                        # prefix and adding it at the end. This way the token
                        # remains unique so that it will be returned by
                        # ElasticSearch when "skip_duplicates" is used in the
                        # query.
                        tokens.append(f'{token} {value[: match.end()]}')
        return tokens

    return tokenizer


tokenizer_default = get_tokenizer(r'[_\s\.\/-]+')


class Elasticsearch(DefinitionAnnotation):
    """
    A metainfo annotation for quantity definitions. This annotation can be used multiple
    times on the same quantity (e.g. to define Elasticsearch fields with differrent mapping
    types). Each annotation will create a field in the respective elasticsearch document type.

    This annotation has to be used on all sub sections that lead to quantities that should
    be included. On sub sections an inner document mapping is applied and all other
    arguments are ignored.

    Arguments:
        mapping: The Elasticsearch mapping for the underlying elasticsearch field. The
            default depends on the quantity type. You can provide the elasticsearch type
            name, a full dictionary with additional elasticsearch mapping parameters, or
            an elasticsearch_dsl mapping object.
        field: Allows to specify sub-field name. There has to be another annotation on the
            same quantity with the default name. The custom field name is concatenated
            to the default. This will create an additional mapping for this
            quantity. In queries this can be used like an additional field, but the
            quantity is only stored once (under the quantity name) in the source document.
        value:
            A callable that is applied to the containering section to get a value for
            this quantity when saving the section in the elastic search index. By default
            this will be the serialized quantity value.
        index:
            A boolean that indicates if this quantity should be indexed or merely be
            part of the elastic document ``_source`` without being indexed for search.
        values:
            If the quantity is used in aggregations for a fixed set of values,
            use this parameter to preset these values. On aggregation, elasticsearch
            will only return values that exist in the search results. This allows to
            create 0 statistic values and return consistent set of values. If the underlying
            quantity is an Enum, the values are determined automatically.
        default_aggregation_size:
            The of values to return by default if this quantity is used in aggregation.
            If no value is given and there are not fixed value, 10 will be used.
        metrics:
            If the quantity is used as a metric for aggregating, this has to
            be used to define a valid elasticsearch metrics aggregations, e.g.
            'sum' or 'cardinality'. It is a dictionary with metric name as key,
            and elasticsearch aggregation name as values.
        many_all:
            Multiple values can be used to search based on a property. If no operator
            is given by the user, a logical or is used, i.e., all those entries that
            have at least one the values are selected (any operator). Set many_all to true
            to alter the default behavior to a logical and (all operator). If the user
            provides an operator, the provided operator is used regardless. Usually
            many_all is only sensible for properties that can have multiple value per
            entry (e.g. elements).
        auto_include_subsections:
            If true all sub and sub sub sections are considered for search even if
            there are no elasticsearch annotations in the sub section definitions.
            By default only sub sections with elasticsearch annotation are considered
            during index mapping creation.
        nested:
            If true the section is mapped to elasticsearch nested object and all queries
            become nested queries. Only applicable to sub sections.
        suggestion:
            Controls the suggestions that are built for this field. Leave
            undefined if no suggestions are required. Can be a custom callable
            that transforms a string into a list of suggestion values, or one
            of the preset strings:
            - simple: Only the value is stored as an ES field.
            - default: The value is split into tokens using whitespace, dot and
              forward slash
        variants:
            A callable that is applied to a search value to get a list of
            alternative forms of the input. Used to augment the available
            suggestions with alternative forms.
        normalizer:
            A callable that is used to transform the search input when
            targeting this field. Note that this does not affect the way the
            value is indexed.
        es_query: The Elasticsearch query type that is used when querying for the annotated
            quantity, e.g. match, term, match_phrase. Default is 'match'.
        dynamic: Whether this quantity should be stored inside a shared flat nested
            field in ES instead of being assigned it's own mapping.

    Attributes:
        name:
            The name of the quantity (plus additional field if set).
        definition: The metainfo definition associated with this annotation.
        search_quantity: The entry type SearchQuantity associated with this annotation.
    """

    def __init__(
        self,
        mapping: str | dict[str, Any] | None = None,
        field: str | None = None,
        es_field: str | None = None,
        value: Callable[[MSectionBound], Any] | None = None,
        index: bool = True,
        values: list[str] | None = None,
        default_aggregation_size: int | None = None,
        metrics: dict[str, str] | None = None,
        many_all: bool = False,
        auto_include_subsections: bool = False,
        nested: bool = False,
        suggestion: str | Callable[[MSectionBound], Any] | None = None,
        variants: Callable[[str], list[str]] | None = None,
        normalizer: Callable[[Any], Any] | None = None,
        es_query: str = 'match',
        _es_field: str | None = None,
        definition: Definition | None = None,
        dynamic: bool = False,
    ):
        # TODO remove _es_field if it is not necessary anymore to enforce a specific mapping
        # for v0 compatibility
        if suggestion:
            for arg in [field, mapping, es_field, _es_field]:
                if arg is not None:
                    raise ValueError(
                        f'You cannot modify the way suggestions are mapped or named.'
                    )
            # If no tokenizer is specified, the suggestion is stored as a field
            # that holds only the original value.
            if suggestion == 'simple':
                field = 'suggestion'
            elif suggestion == 'default':
                value = tokenizer_default
            elif callable(suggestion):
                value = suggestion
            else:
                raise ValueError(
                    'Please provide the suggestion as one of the predefined '
                    'shortcuts, False or a custom callable.'
                )

        if variants and not callable(variants):
            raise ValueError('Please provide the variants as a custom callable.')
        self.variants = variants

        if normalizer and not callable(normalizer):
            raise ValueError('Please provide the normalizer as a custom callable.')
        self.normalizer = normalizer

        self._custom_mapping = mapping
        self.field = field
        self.es_query = es_query
        self._es_field = field if _es_field is None else _es_field
        self.value = value
        self.index = index
        self._mapping: dict[str, Any] = None
        self.default_aggregation_size = default_aggregation_size
        self.values = values
        self.metrics = metrics
        self.many_all = many_all
        self.auto_include_subsections = auto_include_subsections
        self.nested = nested
        self.suggestion = suggestion
        self.search_quantity = None
        self.definition = definition
        self.dynamic = dynamic

    @property
    def values(self):
        return self._values

    @values.setter
    def values(self, value):
        self._values = value
        if self.default_aggregation_size is None and self._values is not None:
            self.default_aggregation_size = len(self._values)

    @property
    def mapping(self) -> dict[str, Any]:
        if self._mapping is not None:
            return self._mapping

        def compute_mapping(quantity: Quantity) -> dict[str, Any]:
            """Used to generate an ES mapping based on the quantity definition if
            no custom mapping is provided.
            """
            if isinstance(quantity.type, Datatype):
                return {'type': to_elastic_type(quantity.type, self.dynamic)}

            if self.dynamic:
                raise NotImplementedError(
                    f'Quantity type {quantity.type} for dynamic quantity {quantity} is not supported.'
                )
            if isinstance(quantity.type, QuantityReference):
                return compute_mapping(quantity.type.target_quantity_def)
            elif isinstance(quantity.type, Reference):
                raise NotImplementedError(
                    'Resolving section references is not supported.'
                )
            else:
                raise NotImplementedError(
                    f'Quantity type {quantity.type} for quantity {quantity} is not supported.'
                )

        if self.suggestion:
            from elasticsearch.dsl import Completion

            # The standard analyzer will retain numbers unlike the simple
            # analyzer which is the default.
            self._mapping = Completion(analyzer='standard').to_dict()
        elif self._custom_mapping is not None:
            from elasticsearch.dsl import Field

            if isinstance(self._custom_mapping, Field):
                self._mapping = self._custom_mapping.to_dict()
            elif isinstance(self._custom_mapping, str):
                self._mapping = dict(type=self._custom_mapping)
            else:
                self._mapping = self._custom_mapping
        else:
            self._mapping = compute_mapping(cast(Quantity, self.definition))

        if not self.index:
            self._mapping['index'] = False

        # For all keyword mappings, we ignore text that is bigger than the limit
        # set by lucene, see more in the ES docs:
        # https://www.elastic.co/guide/en/elasticsearch/reference/7.17/ignore-above.html
        if self._mapping['type'] == 'keyword':
            self._mapping['ignore_above'] = 8191

        return self._mapping

    @property
    def fields(self) -> dict[str, Any]:
        if self._es_field == '' or self._es_field is None:
            return {}

        return {self._es_field: self.mapping}

    @property
    def property_name(self) -> str:
        if self.suggestion and not self.field:
            return f'{self.definition.name}__suggestion'
        return self.definition.name

    @property
    def name(self) -> str:
        if self.field is not None:
            return f'{self.property_name}.{self.field}'
        elif self.suggestion:
            return f'{self.property_name}__suggestion'
        else:
            return self.property_name

    def __repr__(self):
        if self.definition is None:
            return super().__repr__()

        return f'Elasticsearch({self.definition})'

    @property
    def aggregatable(self):
        if isinstance(self.definition.type, Reference):
            return False

        field_type = self.mapping['type']
        if self.dynamic and field_type == 'text':
            return True
        return field_type == 'keyword' or field_type == 'boolean'

    def m_to_dict(self):
        if self.search_quantity:
            return self.search_quantity.qualified_name
        else:
            return self.name


class SearchQuantity:
    """
    This is used to represent search quantities. It is different from a metainfo quantity
    because the same metainfo quantity can appear multiple times at different places in
    an archive (an search index document). A search quantity is uniquely identified by
    a qualified name that pin points its place in the sub-section hierarchy.

    Attributes:
        annotation: The ES annotation that this search quantity is based on
        qualified_field:
            The full qualified name of the resulting elasticsearch field in the entry
            document type. This will be the quantity name (plus additional
            field or suggestion postfix if set) with subsection names up to the
            root of the metainfo data.
        search_field:
            The full qualified name of the field in the elasticsearch index.
        qualified_name:
            Same name as qualified_field. This will be used to address the search
            property in our APIs.
        repeats: Whether this quantity is inside at least one repeatable section
    """

    def __init__(
        self,
        annotation: Elasticsearch,
        prefix: str | None = None,
        qualified_name: str | None = None,
        repeats: bool = False,
    ):
        """
        Args:
            annotation: The elasticsearch annotation that this search quantity is based on.
            doc_type: The elasticsearch document type that this search quantity appears in.
            prefix: The prefix to build the full qualified name for this search quantity.
        """
        self.annotation = annotation

        qualified_field = self.annotation.definition.name
        if prefix is not None:
            qualified_field = f'{prefix}.{qualified_field}'

        if annotation.suggestion:
            qualified_field = f'{qualified_field}__suggestion'

        self.search_field = qualified_field
        if not (annotation._es_field == '' or annotation._es_field is None):
            self.search_field = f'{qualified_field}.{annotation._es_field}'

        if annotation.field is not None:
            qualified_field = f'{qualified_field}.{annotation.field}'

        self.qualified_field = qualified_field
        self.qualified_name = qualified_field
        self.repeats = repeats

        if annotation.dynamic:
            self.qualified_name = qualified_name
            self.search_field = self.get_dynamic_path()
            self.dynamic_filter = self.get_dynamic_filter()

    def get_dynamic_path(self):
        """Returns the dynamic field name for this quantity."""
        mapping = self.annotation.mapping['type']
        field_name = get_searchable_quantity_value_field(
            self.annotation, aggregation=True
        )
        if field_name is None:
            raise ValueError(
                f'quantity "{self.annotation.qualified_name}" has unsupported search index mapping "{mapping}".',
                loc=['aggregation', 'quantity'],
            )
        return f'search_quantities.{field_name}'

    def get_dynamic_filter(self):
        """Returns a filter for this quantity."""
        if self.dynamic:
            path, schema, _ = parse_quantity_name(self.qualified_name)
            searchable_quantity = create_searchable_quantity(
                self.definition, path, schema_name=schema
            )

            segments = path.split('.')
            part_queries = [
                Q(
                    'term',
                    **{f'search_quantities__segments__{i + 1}.path.keyword': part},
                )
                for i, part in enumerate(segments)
            ]
            part_queries.append(
                Q(
                    'term',
                    **{f'search_quantities__segments__1.definitions.keyword': schema},
                )
            )

            filter_path = Q(
                'bool',
                should=[
                    # Old style query
                    Q('term', search_quantities__id=searchable_quantity.id),
                    # New style query
                    Q('bool', must=part_queries),
                ],
                minimum_should_match=1,
            )

            return filter_path

    def get_query(self, value: Any, query_type: str | None = None):
        """Returns an ES query for this quantity, the query type depends on the
        annotation. Also normalizes the value.
        """
        normalizer = self.annotation.normalizer
        if normalizer:
            value = (
                [normalizer(v) for v in value]
                if isinstance(value, list)
                else normalizer(value)
            )

        sub_query = Q(
            query_type or self.annotation.es_query, **{self.search_field: value}
        )
        return self.wrap_dynamic(sub_query)

    def wrap_dynamic(self, sub_query: Any):
        """For dynamic quantities, wraps the given query in a nested query to
        target them correctly.
        """
        if self.dynamic:
            return Q(
                'nested',
                path='search_quantities',
                query=self.dynamic_filter & sub_query,
            )
        return sub_query

    @property
    def definition(self):
        return self.annotation.definition

    def __repr__(self):
        if self.definition is None:
            return super().__repr__()

        return f'SearchQuantity({self.qualified_field})'

    def __getattr__(self, name):
        return getattr(self.annotation, name)


def create_indices(entry_section_def: Section | None = None):
    """
    Creates the mapping for the entry document type and creates the index in
    Elasticsearch. A prior created mapping will be replaced.
    """
    if entry_section_def is None:
        from nomad.datamodel import EntryArchive

        entry_section_def = EntryArchive.m_def

    entry_type._reset()
    entry_type.create_mapping(entry_section_def)

    entry_index.create_index(upsert=True)  # TODO update the existing v0 index


def delete_indices():
    entry_index.delete()


def index_entry(entry: MSection, **kwargs):
    """
    Upserts the given entry in the entry index.
    """
    index_entries([entry], **kwargs)


def _generate_entry_batches(
    entries: list[dict[str, Any]],
    entry_type,
    max_size: int,
    logger,
) -> Iterator[list[dict[str, Any]]]:
    """
    Generate batches of Elasticsearch bulk actions from entries.

    Streams through entries and yields batches of action-document pairs that fit
    within max_size. Memory efficient - generates batches on-the-fly without
    building an intermediate list.

    Args:
        entries: List of entry dictionaries with 'entry_id' keys.
        entry_type: Object with create_index_doc method for converting entries.
        max_size: Maximum batch size in bytes.
        logger: Logger for error reporting.

    Yields:
        Lists of alternating action and document dicts for ES bulk indexing.
    """
    current_batch: list[dict[str, Any]] = []
    current_size = 0

    for entry in entries:
        try:
            entry_index_doc = entry_type.create_index_doc(entry)
            action = {'index': {'_id': entry['entry_id']}}

            pair_size = (_estimate_bytes(action) or 0) + (
                _estimate_bytes(entry_index_doc) or 0
            )

            # Start new batch if adding this pair would exceed max_size
            if current_size + pair_size > max_size and current_batch:
                yield current_batch
                current_batch = []
                current_size = 0

            current_batch.extend([action, entry_index_doc])
            current_size += pair_size

        except Exception as e:
            logger.error(
                'could not create entry index doc',
                entry_id=entry['entry_id'],
                exc_info=e,
            )

    # Yield final batch if it contains any items
    if current_batch:
        yield current_batch


def index_entries(entries: list, refresh: bool = False) -> dict[str, str]:
    """
    Upserts the given entries in the entry index. Optionally updates the materials index
    as well. Returns a dictionary of the format {entry_id: error_message} for all entries
    that failed to index.
    """
    if not entries:
        return {}

    logger = utils.get_logger('nomad.search', n_entries=len(entries))
    MAX_PAYLOAD_SIZE = config.elastic.max_payload_size
    rv = {}

    def _is_retryable_bulk_error(exc: Exception) -> bool:
        status_code = getattr(exc, 'status_code', None)
        return status_code in {429, 503}

    def perform_bulk(batch):
        nonlocal rv
        timer_kwargs: dict[str, Any] = {'n_actions': len(batch)}
        try:
            timer_kwargs['size'] = _estimate_bytes(batch)
        except Exception:
            pass
        with utils.timer(
            logger,
            'perform bulk index of entries',
            lnr_event='failed to bulk index entries',
            **timer_kwargs,
        ):
            indexing_result = None
            for attempt in range(1, config.elastic.bulk_retry_attempts + 1):
                try:
                    indexing_result = entry_index.bulk(
                        body=batch,
                        refresh=refresh,
                        timeout=f'{config.elastic.bulk_timeout}s',
                        request_timeout=config.elastic.bulk_timeout,
                    )
                    break
                except TRANSPORT_ERROR_TYPES as e:
                    if (
                        not _is_retryable_bulk_error(e)
                        or attempt >= config.elastic.bulk_retry_attempts
                    ):
                        raise

                    backoff = min(
                        config.elastic.bulk_retry_max_backoff,
                        config.elastic.bulk_retry_initial_backoff
                        * (2 ** (attempt - 1)),
                    )
                    sleep_time = backoff + random.uniform(
                        0, config.elastic.bulk_retry_jitter
                    )
                    logger.warning(
                        'retrying rejected elasticsearch bulk request',
                        attempt=attempt,
                        max_attempts=config.elastic.bulk_retry_attempts,
                        status_code=getattr(e, 'status_code', None),
                        sleep_time=sleep_time,
                    )
                    time.sleep(sleep_time)

            assert indexing_result is not None
            if indexing_result.get('errors'):
                for item in indexing_result.get('items', []):
                    status = item.get('index', {}).get('status')
                    if status is not None and status >= 400:
                        rv[item['index']['_id']] = str(item['index']['error'])

    with utils.timer(logger, 'bulk index of entries'):
        for batch in _generate_entry_batches(
            entries, entry_type, MAX_PAYLOAD_SIZE, logger
        ):
            perform_bulk(batch)

    return rv


def get_searchable_quantity_value_field(
    annotation: Elasticsearch, aggregation: bool = False
):
    """Get the target field for based on the annotation and whether the field
    should be used in aggregation or not.

    Args:
        annotation: Annotation of the targeted field.
        aggregation: Whether the field is used for aggregation or not.
    """
    mapping = annotation.mapping['type']
    if mapping == 'boolean':
        return 'bool_value'
    elif mapping == 'text':
        if aggregation:
            return 'str_value.keyword'
        return 'str_value'
    elif mapping == 'date':
        return 'datetime_value'
    elif mapping == 'text':
        return 'str_value'
    elif mapping == 'long':
        return 'int_value'
    elif mapping == 'double':
        return 'float_value'

    return None


def create_dynamic_quantity_annotation(quantity_def: Quantity) -> Elasticsearch | None:
    """Given a quantity definition, this function will return the corresponding
    ES annotation if one can be built.
    """
    if quantity_def.shape != []:
        return None
    try:
        annotation = Elasticsearch(definition=quantity_def, dynamic=True)
        annotation.mapping['type']
    except NotImplementedError:
        return None

    return annotation


def find_base_classes(section):
    """Loops over the inheritance tree of the the given section and finds
    all definitions that this section fullfills. ArchiveSection and
    EntryData are for now ignored because they are too generic for any
    meaningful queries.
    """
    from nomad.datamodel.data import ArchiveSection, EntryData

    return [
        cls.m_def
        for cls in section.__mro__
        if issubclass(cls, MSection)
        and cls.m_def
        and cls != ArchiveSection
        and cls != EntryData
    ]


def create_searchable_quantity(
    quantity_def: Quantity,
    quantity_path: Quantity,
    sections: list[MSection] | None = None,
    path_archive: str | None = None,
    schema_name: str | None = None,
) -> Optional['SearchableQuantity']:
    """Transforms a quantity definition into a SearchQuantity."""
    from nomad.datamodel.datamodel import SearchableQuantity

    annotation = create_dynamic_quantity_annotation(quantity_def)
    if not annotation:
        return None
    mapping = annotation.mapping['type']
    quantity_m_def = quantity_def.qualified_name()
    names = quantity_path.split('.')
    segments = {}

    # Add segments for section definitions (index 0 is reserved for the root and is not
    # populated).
    i_segment = 1
    if sections:
        for i, section in enumerate(sections[1:], start=1):
            segments[str(i)] = {
                'path': names[i - 1],
                'definitions': [
                    d.qualified_name() for d in find_base_classes(type(section))
                ],
            }
            i_segment += 1

    # Add quantity definition as the last segment.
    segments[str(i_segment)] = {
        'path': names[-1],
        'definitions': [quantity_m_def.rsplit('.', 1)[0]],
    }

    searchable_quantity = SearchableQuantity(
        id=f'{quantity_path}{schema_separator}{schema_name}'
        if schema_name
        else quantity_path,
        path_archive=path_archive,
        definition=quantity_m_def,
        segments=segments,
    )

    # If a section is given, also store the value
    if sections:
        section = sections[-1]
        logger = utils.get_logger(__name__)
        value = section.m_get(quantity_def)
        if value is None:
            return None
        try:
            value_field_name = get_searchable_quantity_value_field(annotation)
            if value_field_name is None:
                return None

            def drop_value(value, mapping):
                if mapping == 'text':
                    value = str(value)
                elif mapping == 'date':
                    value = value.isoformat()
                elif mapping == 'long':
                    if isinstance(value, PintQuantity):
                        value = int(value.m)
                    elif isinstance(value, dict):
                        return None
                    else:
                        value = int(value)
                elif mapping == 'boolean':
                    value = bool(value)
                elif mapping == 'double':
                    if isinstance(value, PintQuantity):
                        value = float(value.m)
                    elif isinstance(value, dict):
                        return None
                    else:
                        value = float(value)
                    if not math.isfinite(value):
                        logger.warn(
                            'skipped indexing NaN value',
                            path_archive=path_archive,
                        )
                        return None
                return value

            if isinstance(value, dict):
                for k in value:
                    value_temp = drop_value(value[k].value, mapping)
                    if value_temp is not None:
                        value = value_temp
                        break
            else:
                value = drop_value(value, mapping)
            if value is None:
                return None
            setattr(searchable_quantity, value_field_name, value)

        except Exception as e:
            logger.error(
                'error in indexing dynamic quantity',
                path_archive=path_archive,
                exc_info=e,
            )
            return None
    return searchable_quantity


def parse_quantity_name(name: str) -> tuple[str, str | None, str | None]:
    """Used to parse a quantity name into three parts:
    - path: Path in the schema
    - schema (optional): Schema identifider
    - dtype (optional): Data type contained in the name
    """
    dtype = None
    schema = None
    parts = name.split(schema_separator, 1)
    if len(parts) == 2:
        path, schema = parts
    else:
        path = parts[0]
    if schema:
        parts = schema.split(dtype_separator, 1)
        if len(parts) == 2:
            schema, dtype = parts
        else:
            schema = parts[0]
    return path, schema, dtype
