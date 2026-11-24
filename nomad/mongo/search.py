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

from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING, Any, get_args, get_origin

from mongoengine.queryset.visitor import Q
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from nomad.processing import ProcessStatus

if TYPE_CHECKING:
    from nomad.app.v1.routers.uploads import UploadProcDataQuery


class MongoQueryError(Exception):
    """Raised when an API-level mongo query cannot be normalized or translated."""


class StringMatchType(StrEnum):
    exact = 'exact'
    fuzzy = 'fuzzy'


class StringMatch(BaseModel):
    model_config = ConfigDict(extra='forbid')

    value: str
    type: StringMatchType = StringMatchType.exact


class NormalizedUploadProcDataQuery(BaseModel):
    upload_id: list[str] | None = None
    doi: list[str] | None = None
    upload_name: list[StringMatch] | None = None
    is_processing: bool | None = None
    is_published: bool | None = None
    process_status: str | None = None
    is_owned: bool | None = None

    @field_validator('*', mode='before')
    @classmethod
    def normalize_string_match_fields(
        cls, raw_values: Any, info: ValidationInfo
    ) -> Any:
        if info.field_name is None:
            return raw_values

        field = cls.model_fields.get(info.field_name)
        if field is None:
            return raw_values

        if not _is_string_match_list_annotation(field.annotation):
            return raw_values

        field_name = info.field_name

        if raw_values is None:
            return None

        values = raw_values if isinstance(raw_values, list) else [raw_values]

        normalized: list[StringMatch] = []
        for raw_value in values:
            if isinstance(raw_value, StringMatch):
                normalized.append(raw_value)
                continue

            if isinstance(raw_value, str):
                stripped_value = raw_value.strip()
                if stripped_value.startswith('{') and stripped_value.endswith('}'):
                    try:
                        parsed_value = json.loads(stripped_value)
                    except json.JSONDecodeError:
                        parsed_value = None

                    if isinstance(parsed_value, dict):
                        try:
                            normalized.append(StringMatch.model_validate(parsed_value))
                            continue
                        except ValidationError as e:
                            raise MongoQueryError(
                                f'Invalid {field_name} query value: {raw_value}'
                            ) from e

                normalized.append(
                    StringMatch(value=raw_value, type=StringMatchType.exact)
                )
                continue

            if isinstance(raw_value, dict):
                try:
                    normalized.append(StringMatch.model_validate(raw_value))
                    continue
                except ValidationError as e:
                    raise MongoQueryError(
                        f'Invalid {field_name} query value: {raw_value}'
                    ) from e

            value = getattr(raw_value, 'value', None)
            match_type = getattr(raw_value, 'type', None)
            if value is not None:
                candidate = {'value': value}
                if match_type is not None:
                    candidate['type'] = match_type
                try:
                    normalized.append(StringMatch.model_validate(candidate))
                    continue
                except ValidationError as e:
                    raise MongoQueryError(
                        f'Invalid {field_name} query value: {raw_value}'
                    ) from e

            raise MongoQueryError(
                f'Unsupported {field_name} query value type: {type(raw_value).__name__}'
            )

        return normalized


def _is_string_match_list_annotation(annotation: Any) -> bool:
    if annotation is list[StringMatch]:
        return True

    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        return len(args) == 1 and args[0] is StringMatch

    if origin is None:
        return False

    non_none_args = [arg for arg in get_args(annotation) if arg is not type(None)]
    if not non_none_args:
        return False

    return any(_is_string_match_list_annotation(arg) for arg in non_none_args)


def create_mongo_query(
    query: UploadProcDataQuery,
    *,
    base_query: Q | None = None,
    auth_user_id: str | None = None,
) -> Q:
    """
    Translate uploads query model into a mongoengine Q object.

    Returns the translated mongo query.
    """

    normalized_query = NormalizedUploadProcDataQuery(
        upload_id=query.upload_id,
        doi=query.doi,
        upload_name=getattr(query, 'upload_name', None),
        is_processing=query.is_processing,
        is_published=query.is_published,
        process_status=query.process_status,
        is_owned=query.is_owned,
    )

    mongo_query = base_query if base_query is not None else Q()

    if normalized_query.upload_id:
        mongo_query &= Q(upload_id__in=normalized_query.upload_id)

    if normalized_query.doi:
        mongo_query &= Q(doi__in=normalized_query.doi)

    if normalized_query.upload_name:
        mongo_query &= _upload_name_query(normalized_query.upload_name)

    if normalized_query.process_status is not None:
        mongo_query &= Q(process_status=normalized_query.process_status)
    elif normalized_query.is_processing is True:
        mongo_query &= Q(process_status__in=ProcessStatus.STATUSES_PROCESSING)
    elif normalized_query.is_processing is False:
        mongo_query &= Q(process_status__in=ProcessStatus.STATUSES_NOT_PROCESSING)

    if normalized_query.is_published is True:
        mongo_query &= Q(publish_time__ne=None)
    elif normalized_query.is_published is False:
        mongo_query &= Q(publish_time=None)

    if normalized_query.is_owned is not None:
        if auth_user_id is None:
            raise MongoQueryError(
                'The is_owned filter requires an authenticated user id in the query context.'
            )
        if normalized_query.is_owned:
            mongo_query &= Q(main_author=auth_user_id)
        else:
            mongo_query &= Q(main_author__ne=auth_user_id)

    return mongo_query


def _upload_name_query(values: list[StringMatch]) -> Q:
    exact_values: list[str] = []
    fuzzy_values: list[str] = []

    for value in values:
        if value.type == StringMatchType.exact:
            exact_values.append(value.value)
        elif value.type == StringMatchType.fuzzy:
            fuzzy_value = value.value.strip()
            if not fuzzy_value:
                raise MongoQueryError(
                    'The upload_name query parameter must contain at least one non-empty value for fuzzy matching.'
                )
            fuzzy_values.append(fuzzy_value)

    if not exact_values and not fuzzy_values:
        raise MongoQueryError(
            'The upload_name query parameter did not contain any valid values.'
        )

    return _string_match_query('upload_name', exact_values, fuzzy_values)


def _string_match_query(
    field_name: str,
    exact_values: list[str],
    fuzzy_values: list[str],
) -> Q:
    query = Q()

    if exact_values:
        query |= Q(**{f'{field_name}__in': exact_values})

    if fuzzy_values:
        fuzzy_query = Q()
        for fuzzy_value in fuzzy_values:
            fuzzy_query |= Q(**{f'{field_name}__icontains': fuzzy_value})
        query |= fuzzy_query

    return query
