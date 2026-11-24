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

from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import ORJSONResponse

from nomad.app.v1.routers.auth import get_current_user
from nomad.auth.scopes import Scope
from nomad.graph.graph_reader import (
    ConfigError,
    GeneralReader,
    MongoReader,
    Token,
    UserReader,
)
from nomad.graph.lazy_wrapper import LazyWrapper
from nomad.tracing import traced

from ..models import User
from .entries import EntriesArchive

router = APIRouter()


class APITag(StrEnum):
    DEFAULT = 'graph'


def unwrap_response(result):
    if isinstance(result, dict):
        for key, value in result.items():
            if isinstance(value, LazyWrapper):
                result[key] = value.to_json()
            elif key != Token.ARCHIVE:
                unwrap_response(value)
    elif isinstance(result, list):
        for idx, value in enumerate(result):
            if isinstance(value, LazyWrapper):
                result[idx] = value.to_json()
            else:
                unwrap_response(value)


class GraphJSONResponse(ORJSONResponse):
    def render(self, content):
        content.pop(GeneralReader.__CACHE__, None)

        unwrap_response(content)
        return super().render(content)


def relocate_children(request):
    if not isinstance(request, dict) or not request:
        return
    request.update(request.pop('m_children', {}))
    for child in request.values():
        relocate_children(child)


@router.post(
    '/raw_query',
    tags=[APITag.DEFAULT],
    summary='INTERNAL USE ONLY. Query the database with a graph style without verification.',
    description='INTERNAL USE ONLY. Query the database with a graph style without verification.',
    response_class=GraphJSONResponse,
    include_in_schema=False,
)
@traced(span_name='graph.raw_query')
async def raw_query(
    user: Annotated[User, Depends(get_current_user([Scope.GRAPH_READ]))],
    query=Body(...),
):
    relocate_children(query)
    with MongoReader(query, user=user) as reader:
        return GraphJSONResponse(await reader.read())


@router.post(
    '/query',
    tags=[APITag.DEFAULT],
    summary='Query the database with a graph style API.',
    description="""Use a GraphQL style query to query the database and fetch the desired data.
    The query is a JSON object that describes the data to be fetched, similar to a GraphQL query.
    This allows for flexible queries, including nested data structures, and avoids over-/under-fetching.
    One can compose complex queries (navigating from one node to another in the graph) and retrieve data in a single request.
    Please refer to the documentation for more details on how to structure the query.""",
    # response_model=GraphResponse,
    response_class=GraphJSONResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
@traced(span_name='graph.basic_query')
async def basic_query(
    user: Annotated[User, Depends(get_current_user([Scope.GRAPH_READ]))],
    # todo: may need to re-enable validation
    #   as of June 2025, it is not working
    # query: GraphRequest = Body(...),
    query=Body(...),
):
    try:
        query_dict = query
        relocate_children(query_dict)
        with MongoReader(query_dict, user=user) as reader:
            response: dict = await reader.read()
    except ConfigError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        raise HTTPException(422, detail=str(e))

    return GraphJSONResponse(response)


@router.post(
    '/archive/query',
    tags=[APITag.DEFAULT],
    summary='Search entries and access their archives.',
    response_class=GraphJSONResponse,
    include_in_schema=False,
)
@traced(span_name='graph.archive_query')
async def archive_query(
    data: EntriesArchive,
    user: Annotated[User, Depends(get_current_user([Scope.GRAPH_READ]))],
):
    graph_dict: dict = {Token.SEARCH: {'m_request': {'query': {}}}}
    root_request: dict = graph_dict[Token.SEARCH]['m_request']['query']

    if data.pagination:
        root_request['pagination'] = data.pagination
    if data.query:
        root_request['query'] = data.query
    if data.owner:
        root_request['owner'] = data.owner

    if data.required is None:
        graph_dict[Token.SEARCH]['m_request']['directive'] = 'plain'
    else:
        graph_dict[Token.SEARCH]['*'] = {Token.ENTRIES: {Token.ARCHIVE: data.required}}

    if not root_request:
        del graph_dict[Token.SEARCH]['m_request']['query']

    with UserReader(graph_dict, user=user) as reader:
        response: dict = await reader.read(user.user_id if user else '')

    return GraphJSONResponse(response)
