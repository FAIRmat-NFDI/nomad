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

from cachetools import cached
from elasticsearch.dsl import Q
from optimade.filterparser import LarkParser
from optimade.filtertransformers.elasticsearch import ElasticsearchQuantity as Quantity
from optimade.filtertransformers.elasticsearch import (
    ElasticTransformer as OPTElasticTransformer,
)

from .common import provider_specific_fields

_parser = LarkParser(version=(1, 0, 1))


class FilterException(Exception):
    """Raised on parsing a filter expression with syntactic of semantic errors."""

    pass


@cached(cache={})
def _get_transformer(without_prefix, **kwargs):
    from nomad.datamodel import OptimadeEntry

    quantities: dict[str, Quantity] = {
        q.name: Quantity(
            q.name,
            backend_field=f'optimade.{q.name}',
            elastic_mapping_type=q.a_elasticsearch.mapping['type'],
        )
        for q in OptimadeEntry.m_def.all_quantities.values()
        if 'elasticsearch' in q.m_annotations
    }

    quantities['id'] = Quantity(
        'id', backend_field='entry_id', elastic_mapping_type='keyword'
    )
    quantities['immutable_id'] = Quantity(
        'immutable_id', backend_field='entry_id', elastic_mapping_type='keyword'
    )
    quantities['last_modified'] = Quantity(
        'last_modified', backend_field='upload_create_time', elastic_mapping_type='date'
    )

    quantities['elements'].length_quantity = quantities['nelements']
    quantities['elements'].nested_quantity = quantities['elements_ratios']
    quantities['elements_ratios'].nested_quantity = quantities['elements_ratios']

    for name, search_quantity in provider_specific_fields().items():
        names = ['_nmd_' + name]
        if without_prefix:
            names.append(name)

        for name in names:
            if name not in quantities:
                quantities[name] = Quantity(
                    name,
                    backend_field=search_quantity.search_field,
                    elastic_mapping_type=search_quantity.mapping['type'],
                )

    return ElasticTransformer(quantities=quantities, **kwargs)


def parse_filter(filter_str: str, without_prefix=False) -> Any:
    """Parses the given optimade filter str and returns a suitable elastic search query.

    Arguments:
        filter_str: Can be direct user input with no prior processing.
        nomad_properties: Also include the nomad proprietary properties.
        without_prefix: Do not prefix the nomad proprietary properties with _nmd_.

    Raises:
        FilterException: If the given str cannot be parsed, or if there are any semantic
            errors in the given expression.
    """
    from .elasticsearch import NomadStructureMapper

    transformer = _get_transformer(without_prefix, mapper=NomadStructureMapper)

    try:
        parse_tree = _parser.parse(filter_str)
    except Exception as e:
        raise FilterException(f'Syntax error: {str(e)}')

    try:
        query = transformer.transform(parse_tree)
    except Exception as e:
        raise FilterException(f'Semantic error: {str(e)}')

    return query


class ElasticTransformer(OPTElasticTransformer):
    def _query_op(self, quantity, op, value, nested=None):
        """
        Return a range, match, or term query for the given quantity, comparison
        operator, and value
        """
        field = self._field(quantity, nested=nested)
        if op in self.operator_map:
            return Q('range', **{field: {self.operator_map[op]: value}})

        if quantity.elastic_mapping_type == 'text':
            query_type = 'match'
        elif quantity.elastic_mapping_type in [
            'keyword',
            'short',
            'integer',
            'long',
            'float',
            'double',
            'bool',
        ]:
            query_type = 'term'
        else:
            raise NotImplementedError('Quantity has unsupported ES field type')

        if op in ['=', '']:
            return Q(query_type, **{field: value})

        if op == '!=':
            return ~Q(  # pylint: disable=invalid-unary-operand-type
                query_type, **{field: value}
            )

    def _has_query_op(self, quantities, op, predicate_zip_list):
        # We override this to add 'HAS ONLY' support.
        if op == 'HAS ONLY':
            # HAS ONLY can be achieved by rewriting to a combination of HAS ALL and
            # length = n_values. Therefore, it is only support for quantities with a
            # length quantity.
            if len(quantities) > 1:
                raise Exception('HAS ONLY is not supported with zip')
            quantity = quantities[0]

            if quantity.length_quantity is None:
                raise Exception(f'HAS ONLY is not supported by {quantity.name}')

            has_all = super()._has_query_op(quantities, 'HAS ALL', predicate_zip_list)
            has_length = Q(
                'term',
                **{quantity.length_quantity.backend_field: len(predicate_zip_list)},
            )
            return has_all & has_length

        else:
            return super()._has_query_op(quantities, op, predicate_zip_list)

    def property_zip_addon(self, args):
        return args

    def value_zip(self, args):
        return self.value_list(args)

    def value_zip_list(self, args):
        return args
