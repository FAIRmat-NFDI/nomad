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

from typing import Any

from elasticsearch.dsl import Q
from optimade.filterparser import LarkParser
from optimade.models import StructureResource
from optimade.server.entry_collections import EntryCollection
from optimade.server.exceptions import BadRequest
from optimade.server.mappers import StructureMapper
from optimade.server.mappers.entries import classproperty

from nomad import datamodel, files, utils
from nomad.app.v1.models import MetadataPagination, MetadataRequired
from nomad.atomutils import Formula
from nomad.config import config
from nomad.search import search
from nomad.units import ureg

from ...archive import to_json
from .common import provider_specific_fields
from .filterparser import _get_transformer as get_transformer

logger = utils.get_logger(__name__)


class NomadStructureMapper(StructureMapper):
    @classmethod
    def deserialize(cls, results):
        # We are not doing this here, but will do it in the overwritten StructureCollection
        # find method below
        return results

    @classmethod
    def map_back(cls, doc):
        # We are not doing this here, but will do it in the overwritten StructureCollection
        # find method below
        return doc

    @classproperty
    def ALL_ATTRIBUTES(cls) -> set[str]:  # pylint: disable=no-self-argument
        # Delegate to the upstream mapper each time to avoid freezing a stale
        # provider-field snapshot during early import-time initialization.
        return StructureMapper.ALL_ATTRIBUTES  # pylint: disable=no-member


class StructureCollection(EntryCollection):
    def __init__(self):
        super().__init__(
            resource_cls=StructureResource,
            resource_mapper=NomadStructureMapper,
            transformer=get_transformer(
                without_prefix=False, mapper=NomadStructureMapper
            ),
        )

        self.parser = LarkParser(version=(1, 0, 0), variant='default')

        # check aliases do not clash with mongo operators
        self._check_aliases(self.resource_mapper.all_aliases())
        self._check_aliases(self.resource_mapper.all_length_aliases())

    def _base_search_query(self) -> Any:
        return Q('exists', field='optimade.elements') & Q('term', processed=True)

    def __len__(self) -> int:
        # TODO cache
        return search(
            owner='public',
            query=self._base_search_query(),
            pagination=MetadataPagination(page_size=0),
        ).pagination.total

    def count(self, **kwargs) -> int:
        # This seems solely mongodb specific
        raise NotImplementedError()

    def find(self, params):
        (
            results,
            data_returned,
            more_data_available,
            exclude_fields,
            include_fields,
        ) = super().find(params)

        if isinstance(results, list):
            results = self._es_to_optimade_results(
                results, response_fields=include_fields
            )
        else:
            results = self._es_to_optimade_result(
                results, response_fields=include_fields
            )

        results = StructureMapper.deserialize(results)

        return (
            results,
            data_returned,
            more_data_available,
            exclude_fields,
            include_fields,
        )

    def _check_aliases(self, aliases):
        pass

    def _es_to_optimade_result(
        self,
        es_result: dict,
        response_fields: set[str],
        upload_files_cache: dict[str, files.UploadFiles] | None = None,
    ) -> StructureResource:
        if upload_files_cache is None:
            upload_files_cache = {}

        entry_id, upload_id = es_result['entry_id'], es_result['upload_id']
        upload_files = upload_files_cache.get(upload_id)

        if upload_files is None:
            upload_files = files.UploadFiles.get(upload_id)
            if upload_files is None:
                logger.error('missing upload', upload_id=upload_id)
                return None

            upload_files_cache[upload_id] = upload_files

        try:
            with upload_files.read_archive(entry_id) as archive_reader:
                entry_archive_reader = archive_reader[entry_id]
                archive = {'metadata': to_json(entry_archive_reader['metadata'])}

                # Lazy load results if only if results provider specfic field is requested
                def get_results():
                    if 'results' not in archive:
                        archive['results'] = to_json(entry_archive_reader['results'])

                attrs = archive['metadata'].get('optimade', {})

                attrs['immutable_id'] = entry_id
                attrs['id'] = entry_id
                attrs['last_modified'] = archive['metadata']['upload_create_time']

                # TODO this should be removed, once all data is reprocessed with the right normalization
                original_formula = attrs['chemical_formula_hill']
                if original_formula is not None:
                    formula = Formula(original_formula)
                    attrs['chemical_formula_reduced'] = formula.format('reduced')
                    attrs['chemical_formula_anonymous'] = formula.format('anonymous')
                    attrs['chemical_formula_hill'] = formula.format('hill')
                    attrs['chemical_formula_descriptive'] = attrs[
                        'chemical_formula_hill'
                    ]
                dimension_types = attrs['dimension_types']
                if isinstance(dimension_types, int):
                    attrs['dimension_types'] = [1] * dimension_types + [0] * (
                        3 - dimension_types
                    )
                    attrs['nperiodic_dimensions'] = dimension_types
                elif isinstance(dimension_types, list):
                    attrs['nperiodic_dimensions'] = sum(dimension_types)

                if response_fields is not None:
                    for request_field in response_fields:
                        if not request_field.startswith('_nmd_'):
                            continue

                        if request_field == '_nmd_archive_url':
                            attrs[request_field[5:]] = (
                                config.api_url() + f'/archive/{upload_id}/{entry_id}'
                            )
                            continue

                        if request_field == '_nmd_entry_page_url':
                            attrs[request_field[5:]] = config.gui_url(
                                f'entry/id/{upload_id}/{entry_id}'
                            )
                            continue

                        if request_field == '_nmd_raw_file_download_url':
                            attrs[request_field[5:]] = (
                                config.api_url() + f'/raw/calc/{upload_id}/{entry_id}'
                            )
                            continue

                        search_quantity = provider_specific_fields().get(
                            request_field[5:]
                        )
                        if search_quantity is None:
                            # if unknown properties where provided, we will ignore them as per
                            # optimade spec
                            continue

                        try:
                            path = search_quantity.qualified_name.split('.')
                            if path[0] == 'results':
                                get_results()
                            section = archive
                            for segment in path:
                                if isinstance(section, list):
                                    if len(section) == 0:
                                        value = None
                                        break
                                    section = section[0]
                                value = section[segment]
                                section = value

                            # Empty values are not stored and only the magnitude of
                            # Quantities is stored.
                            if value is not None:
                                if isinstance(value, ureg.Quantity):
                                    value = value.magnitude
                                attrs[request_field[5:]] = value
                        except Exception:
                            # TODO there a few things that can go wrong. Most notable the search
                            # quantity might have a path with repeated sections. This won't be
                            # handled right now.
                            pass
                return attrs
        except KeyError:
            logger.error(
                'missing archive entry', upload_id=upload_id, entry_id=entry_id
            )
            return None

    def _es_to_optimade_results(
        self, es_results: list[dict], response_fields: set[str]
    ):
        upload_files_cache: dict[str, files.UploadFiles] = {}
        optimade_results = []
        try:
            for es_result in es_results:
                optimade_result = self._es_to_optimade_result(
                    es_result, response_fields, upload_files_cache
                )
                if optimade_result is not None:
                    optimade_results.append(optimade_result)
        finally:
            for upload_files in upload_files_cache.values():
                upload_files.close()

        return optimade_results

    def _run_db_query(self, criteria: dict[str, Any], single_entry=False):
        sort, order = criteria.get('sort', (('chemical_formula_reduced', 1),))[0]
        sort_quantity = datamodel.OptimadeEntry.m_def.all_quantities.get(sort, None)
        if sort_quantity is None:
            raise BadRequest(detail=f'Unable to sort on field {sort}')
        sort_quantity_a_optimade = sort_quantity.m_get_annotations('optimade')
        if not sort_quantity_a_optimade.sortable:
            raise BadRequest(detail=f'Unable to sort on field {sort}')

        search_query = self._base_search_query()

        filter = criteria.get('filter')
        if filter:
            search_query &= filter

        es_response = search(
            owner='public',
            query=search_query,
            required=MetadataRequired(include=['entry_id', 'upload_id']),
            pagination=MetadataPagination(
                page_size=criteria['limit'],
                page_offset=criteria.get('skip', 0),
                order='asc' if order == 1 else 'desc',
                order_by=f'optimade.{sort}',
            ),
        )

        results = es_response.data

        data_returned = es_response.pagination.total
        more_data_available = (
            data_returned >= criteria.get('skip', 0) + criteria['limit']
        )

        return results, data_returned, more_data_available

    def insert(self, *args, **kwargs):
        # This is used to insert test records during OPT tests. This should never be necessary
        # on our implementation. We just need to implement it, because its marked as
        # @abstractmethod.
        raise NotImplementedError()
