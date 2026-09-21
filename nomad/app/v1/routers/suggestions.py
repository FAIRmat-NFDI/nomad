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

from collections import defaultdict
from enum import Enum
from typing import Annotated

from elasticsearch.dsl import Search
from elasticsearch.dsl.utils import AttrList
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from nomad.app.v1.routers.auth import get_current_user
from nomad.auth.scopes import Scope
from nomad.elastic_compat import REQUEST_ERROR_TYPES, execute_search
from nomad.metainfo.elasticsearch_extension import entry_index, entry_type
from nomad.tracing import traced

from ..models import User

router = APIRouter()


class APITag(str, Enum):
    DEFAULT = 'suggestions'


# This is a dynamically create enum class for enumerating all allowed
# quantities. FastAPI uses python enums to validate and document options.
suggestable_quantities: set[str] | None = None


class SuggestionError(Exception):
    pass


class Suggestion(BaseModel):
    value: str | None = Field(None, description='The returned suggestion.')
    weight: float | None = Field(None, description='The suggestion weight.')


class Quantity(BaseModel):
    name: str | None = Field(None, description='The name of the targeted quantity.')
    size: int = Field(
        5,
        description='The maximum number of suggestion results to query for this quantity.',
    )


class SuggestionsRequest(BaseModel):
    quantities: list[Quantity] | None = Field(  # type: ignore
        None, description='List of quantities for which the suggestions are retrieved.'
    )
    input: str | None = Field(
        None,
        description='The input that is used as a basis for returning a suggestion.',
    )


@router.post(
    '',
    tags=[APITag.DEFAULT],
    summary='Get a list of suggestions for the given quantity names and input.',
    response_model=dict[str, list[Suggestion]],
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
@traced(span_name='suggestions.get_suggestions')
def get_suggestions(
    request: Request,
    data: SuggestionsRequest,
    _user: Annotated[User, Depends(get_current_user([Scope.SUGGESTIONS_READ]))],
):
    global suggestable_quantities
    if suggestable_quantities is None:
        suggestable_quantities = set(entry_type.suggestions.keys())

    search = Search(index=entry_index.index_name)
    names = [x.name for x in data.quantities]
    names_es = [x.name.replace('.', '-') for x in data.quantities if x.name is not None]
    for index, quantity in enumerate(data.quantities):
        name_es = names_es[index]
        if quantity.name not in suggestable_quantities:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[
                    dict(
                        msg=(
                            f'The string "{quantity.name}" does not represent a suggestable quantity. '
                            f'Possible values are {", ".join(suggestable_quantities)}.'
                        ),
                        loc=['quantities', index],
                    )
                ],
            )

        annotation = entry_type.suggestions[quantity.name]
        postfix = '.suggestion' if annotation.field else '__suggestion'
        search = search.suggest(
            name_es,
            data.input,
            completion={
                'field': f'{quantity.name}{postfix}',
                'size': quantity.size,
                'skip_duplicates': True,
                # 'fuzzy': {},
            },
        )
    search = search.extra(_source=names)

    try:
        # For some reason calling the search.extra()-method messes up the type
        # information for the Search-object. This is why linting is disabled
        # here.
        es_response = execute_search(search)
    except REQUEST_ERROR_TYPES as e:
        raise SuggestionError from e

    # We return the original field in the source document.
    response: dict[str, list[Suggestion]] = defaultdict(list)
    suggestions: dict[str, dict[str, float]] = defaultdict(dict)

    def add_suggestion(name, value, weight):
        values = suggestions[name]
        if value not in values or values[value] < weight:
            suggestions[name][value] = weight

    for name, name_es in zip(names, names_es):
        variants = entry_type.suggestions[name].variants
        for suggest_option in es_response.suggest[name_es][0].options:
            weight = suggest_option._score

            # We use the original input text to do the matching. This works
            # better than the text returned by the completion suggester
            # (suggest_option.text), since it can match several items if there are
            # multiple values per quantity.
            text = data.input
            if text is not None:
                text = text.lower().strip()
            else:
                text = ''

            # Nested fields use the nested document as _source: we need to
            # modify the path accordingly.
            try:
                nested_field = suggest_option._nested.field
            except AttributeError:
                nested_field = None
            quantity_path = name[len(nested_field) :] if nested_field else name

            # Gather all options recursively. The source may contain
            # inner/nested sections and also values may be lists.
            def gather_options(root, parts, options):
                original = root
                for i, part in enumerate(parts):
                    if part != '':
                        original = original[part]
                        if isinstance(original, AttrList):
                            for item in original:
                                gather_options(item, parts[i + 1 :], options)
                            return
                original_type = type(original)
                if original_type is str:
                    options.append(original)
                elif original_type is list:
                    for item in original:
                        options.append(item)

            options: list[str] = []
            parts = quantity_path.split('.')
            gather_options(suggest_option._source, parts, options)

            # There may be multiple options and we have to look which options
            # were actually matched (the completion suggester does not have a
            # mechanism for saving which item in a list was matched.). If the
            # value has several variants, we have to expand the original source
            # value and return only the best match.
            for option in options:
                if variants:
                    best_match = float('Inf')
                    best_option = option
                    for variant in variants(option):
                        match_start = variant.lower().strip().find(text)
                        if match_start >= 0 and match_start < best_match:
                            best_match = match_start
                            best_option = variant
                    add_suggestion(name, best_option, weight)
                elif text in option.lower().strip():
                    add_suggestion(name, option, weight)

    for name, suggestion in suggestions.items():
        for value, weight in suggestion.items():
            response[name].append(Suggestion(value=value, weight=weight))

    return response
