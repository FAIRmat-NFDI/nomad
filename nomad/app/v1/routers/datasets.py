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

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi import Query as FastApiQuery
from mongoengine.errors import MongoEngineException
from pydantic import BaseModel, Field, field_validator

from nomad import datamodel, processing, utils
from nomad.app.v1.routers.auth import get_current_user
from nomad.auth.scopes import Scope
from nomad.config import config
from nomad.datacite import DataCiteException
from nomad.datamodel import Dataset as DatasetDefinitionCls
from nomad.metainfo.elasticsearch_extension import entry_type
from nomad.mongo.doi import DOI
from nomad.search import search, update_by_query
from nomad.utils import create_uuid, strip

from ..models import (
    Any_,
    Direction,
    HTTPExceptionModel,
    MetadataPagination,
    MetadataRequired,
    Owner,
    Pagination,
    PaginationResponse,
    Query,
    User,
)
from ..utils import create_responses, parameter_dependency_from_model
from .entries import _do_exhaustive_search

router = APIRouter()


class APITag(str, Enum):
    DEFAULT = 'datasets'


logger = utils.get_logger(__name__)


_bad_id = (
    status.HTTP_404_NOT_FOUND,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        Dataset not found. The given id does not match any dataset."""
        ),
    },
)

_forbidden_user = (
    status.HTTP_403_FORBIDDEN,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The dataset can only be edited by the user who created the dataset."""
        ),
    },
)

_existing_name = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The chosen dataset name is already taken. Datesets of the same user must have a
        unique name.
    """
        ),
    },
)

_dataset_is_fixed = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The dataset already has a DOI and cannot be changed anymore.
    """
        ),
    },
)

_dataset_has_unpublished_contents = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The dataset has unpublished contents. No DOI can be assigned at the moment.
        Publish the dataset contents first.
    """
        ),
    },
)

_dataset_is_empty = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The dataset is empty. No DOI can be assigned at this moment. Add some published
        contents to the dataset first.
    """
        ),
    },
)

_datacite_not_enabled = (
    status.HTTP_403_FORBIDDEN,
    {
        'model': HTTPExceptionModel,
        'description': 'The DataCite DOI service is not enabled on this deployment.',
    },
)

_dataset_doi_no_document = (
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The dataset has a DOI assigned but the corresponding DOI document could not be found.
    """
        ),
    },
)

_dataset_doi_unpublished = (
    status.HTTP_400_BAD_REQUEST,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        The dataset has a DOI assigned but the DOI is unpublished.
    """
        ),
    },
)

_datacite_draft_failed = (
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        An error occurred while creating the DOI draft at DataCite. Please try again later or contact the administrator if the problem persists.
    """
        ),
    },
)

_db_set_doi_draft_failed = (
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        An error occurred while saving the DOI draft to the database. Please try again later or contact the administrator if the problem persists.
    """
        ),
    },
)

_db_set_dataset_doi_failed = (
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        An error occurred while saving the dataset with the assigned DOI to the database. Please contact the administrator.
    """
        ),
    },
)

_datacite_publish_failed = (
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        An error occurred while publishing the DOI at DataCite. Please contact the administrator.
    """
        ),
    },
)

_db_publish_dataset_doi_failed = (
    status.HTTP_500_INTERNAL_SERVER_ERROR,
    {
        'model': HTTPExceptionModel,
        'description': strip(
            """
        An error occurred while saving the published DOI state to the database. Please contact the administrator.
    """
        ),
    },
)


def _create_exception(status_code, response_dict):
    return HTTPException(status_code, detail=response_dict.get('description'))


Dataset = datamodel.Dataset.m_def.a_pydantic.model


def _delete_dataset(user: User, dataset_id, dataset):
    es_query = cast(Query, {'datasets.dataset_id': dataset_id})
    entries = _do_exhaustive_search(
        owner=Owner.user,
        query=es_query,
        user=user,
        required=MetadataRequired(include=['entry_id']),
    )
    entry_ids = [entry['entry_id'] for entry in entries]
    mongo_query = {'_id': {'$in': entry_ids}}

    dataset.delete()

    if len(entry_ids) > 0:
        processing.Entry._get_collection().update_many(
            mongo_query, {'$pull': {'datasets': dataset.dataset_id}}
        )
        update_by_query(
            """
                int index = -1;
                for (int i = 0; i < ctx._source.datasets.length; i++) {
                    if (ctx._source.datasets[i].dataset_id == params.dataset_id) {
                        index = i
                    }
                }
                if (index != -1) {
                    ctx._source.datasets.remove(index);
                }
            """,
            params=dict(dataset_id=dataset_id),
            query=es_query,
            user_id=user.user_id,
            refresh=True,
        )


class DatasetPagination(Pagination):
    @field_validator('order_by')
    @classmethod
    def validate_order_by(cls, order_by):  # pylint: disable=no-self-argument
        if order_by is None:
            return order_by
        assert order_by in (
            'dataset_create_time',
            'dataset_modified_time',
            'dataset_name',
        ), 'order_by must be a valid attribute'
        return order_by

    @field_validator('page_after_value')
    @classmethod
    def validate_page_after_value(cls, page_after_value, values):  # pylint: disable=no-self-argument
        # Validation handled elsewhere
        return page_after_value

    def order_result(self, result):
        if self.order_by is None:
            return result

        prefix: str = '-' if self.order == Direction.desc else '+'
        order_list: list = [f'{prefix}{self.order_by}']
        if self.order_by == 'dataset_create_time':
            order_list.append('dataset_id')
        else:
            order_list.extend(['dataset_create_time', 'dataset_id'])

        return result.order_by(*order_list)


dataset_pagination_parameters = parameter_dependency_from_model(
    'dataset_pagination_parameters',
    DatasetPagination,
)


class DatasetsResponse(BaseModel):
    pagination: PaginationResponse | None = Field(None)
    data: list[Dataset] = Field(None)  # type: ignore


class DatasetResponse(BaseModel):
    dataset_id: str = Field(..., description=strip("""The unique dataset id. """))
    data: Dataset = Field()  # type: ignore


class DatasetType(str, Enum):
    owned = 'owned'
    foreign = 'foreign'


class DatasetCreate(BaseModel):  # type: ignore
    dataset_name: str | None = Field(None, description='The new name for the dataset.')
    dataset_type: DatasetType | None = Field(None)
    query: Query | None = Field(None)
    entries: list[str] | None = Field(None)


@router.get(
    '',
    tags=[APITag.DEFAULT],
    summary='Get a list of datasets',
    response_model=DatasetsResponse,
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_datasets(
    request: Request,
    pagination: Annotated[DatasetPagination, Depends(dataset_pagination_parameters)],
    _user: Annotated[
        User,
        Depends(get_current_user([Scope.DATASETS_READ])),
    ],
    dataset_id: Annotated[str | None, FastApiQuery()] = None,
    dataset_name: Annotated[str | None, FastApiQuery()] = None,
    user_id: Annotated[list[str] | None, FastApiQuery()] = None,
    dataset_type: Annotated[str | None, FastApiQuery()] = None,
    doi: Annotated[str | None, FastApiQuery()] = None,
    prefix: Annotated[str | None, FastApiQuery()] = None,
):
    """
    Retrieves all datasets that match the given criteria.
    """

    mongodb_objects = DatasetDefinitionCls.m_def.a_mongo.objects
    query_params = dict(
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        user_id__in=user_id,
        dataset_type=dataset_type,
        doi=doi,
    )
    if prefix is not None and prefix != '':
        query_params.update(dataset_name=re.compile(f'^{prefix}.*', re.IGNORECASE))  # type: ignore
    query_params = {k: v for k, v in query_params.items() if v is not None}

    mongodb_query = pagination.order_result(mongodb_objects(**query_params))

    start = pagination.get_simple_index()
    end = start + pagination.page_size

    pagination_response = PaginationResponse(
        total=mongodb_query.count(), **pagination.dict()
    )
    pagination_response.populate_simple_index_and_urls(request)

    return {'pagination': pagination_response, 'data': list(mongodb_query[start:end])}


@router.get(
    '/{dataset_id}',
    tags=[APITag.DEFAULT],
    summary='Get a list of datasets',
    response_model=DatasetResponse,
    responses=create_responses(_bad_id),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def get_dataset(
    dataset_id: Annotated[
        str, Path(description='The unique dataset id of the dataset to retrieve.')
    ],
    _user: Annotated[
        User,
        Depends(get_current_user([Scope.DATASETS_READ])),
    ],
):
    """
    Retrieves the dataset with the given id.
    """
    mongodb_objects = DatasetDefinitionCls.m_def.a_mongo.objects
    dataset = mongodb_objects(dataset_id=dataset_id).first()

    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='The dataset with the given id does not exist.',
        )

    return {'dataset_id': dataset_id, 'data': dataset}


@router.post(
    '',
    tags=[APITag.DEFAULT],
    summary='Create a new dataset',
    response_model=DatasetResponse,
    responses=create_responses(_existing_name),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def post_datasets(
    create: DatasetCreate,
    user: Annotated[
        User,
        Depends(get_current_user([Scope.DATASETS_WRITE], allow_anonymous=False)),
    ],
):
    """
    Create a new dataset.
    """

    now = datetime.now(timezone.utc)
    dataset_type = (
        create.dataset_type if create.dataset_type is not None else DatasetType.owned
    )

    # check if name already exists
    existing_dataset = DatasetDefinitionCls.m_def.a_mongo.objects(
        user_id=user.user_id, dataset_name=create.dataset_name
    ).first()
    if existing_dataset is not None:
        raise HTTPException(
            status_code=_existing_name[0],
            detail=_existing_name[1]['description'],
        )

    # create dataset
    dataset = DatasetDefinitionCls(
        dataset_id=create_uuid(),
        dataset_name=create.dataset_name,
        user_id=user.user_id,
        dataset_create_time=now,
        dataset_modified_time=now,
        dataset_type=dataset_type,
    )
    dataset.a_mongo.create()

    # add dataset to entries in mongo and elastic
    # TODO this should be part of a new edit API
    if dataset_type != DatasetType.owned:
        if create.query is not None:
            dataset.query = create.model_dump()['query']
        dataset.entrys = create.entries
        empty = True
    else:
        # add dataset to entries in mongo and elastic
        # TODO this should be part of a new edit API
        if create.entries is not None:
            es_query = cast(Query, {'entry_id': Any_(any=create.entries)})
        elif create.query is not None:
            es_query = create.query
        else:
            es_query = None

        if es_query is None:
            empty = True
        else:
            entries = _do_exhaustive_search(
                owner=Owner.user,
                query=es_query,
                user=user,
                required=MetadataRequired(include=['entry_id']),
            )
            entry_ids = [entry['entry_id'] for entry in entries]
            mongo_query = {'_id': {'$in': entry_ids}}
            empty = len(entry_ids) == 0

    if not empty:
        processing.Entry._get_collection().update_many(
            mongo_query, {'$push': {'datasets': dataset.dataset_id}}
        )
        update_by_query(
            """
                if (ctx._source.datasets == null) {
                    ctx._source.datasets = new ArrayList();
                }
                ctx._source.datasets.add(params.dataset);
            """,
            params=dict(dataset=entry_type.create_index_doc(dataset)),
            query=es_query,
            user_id=user.user_id,
            refresh=True,
        )

    return {'dataset_id': dataset.dataset_id, 'data': dataset}


@router.delete(
    '/{dataset_id}',
    tags=[APITag.DEFAULT],
    summary='Delete a dataset',
    response_model=DatasetResponse,
    responses=create_responses(_bad_id, _dataset_is_fixed, _forbidden_user),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def delete_dataset(
    dataset_id: Annotated[
        str, Path(description='The unique dataset id of the dataset to delete.')
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.DATASETS_DELETE], allow_anonymous=False)),
    ],
):
    """
    Delete an dataset.
    """

    dataset = DatasetDefinitionCls.m_def.a_mongo.objects(dataset_id=dataset_id).first()
    if dataset is None:
        raise HTTPException(status_code=_bad_id[0], detail=_bad_id[1]['description'])

    if dataset.doi is not None and not user.is_admin:
        raise HTTPException(
            status_code=_existing_name[0],
            detail=_dataset_is_fixed[1]['description'],
        )

    if dataset.user_id != user.user_id:
        raise HTTPException(
            status_code=_forbidden_user[0],
            detail=_forbidden_user[1]['description'],
        )

    # delete dataset from entries in mongo and elastic
    # TODO this should be part of a new edit API
    _delete_dataset(user=user, dataset_id=dataset_id, dataset=dataset)

    return {'dataset_id': dataset.dataset_id, 'data': dataset}


@router.post(
    '/{dataset_id}/action/doi',
    tags=[APITag.DEFAULT],
    summary='Assign a DOI to a dataset',
    response_model=DatasetResponse,
    responses=create_responses(
        _datacite_not_enabled,
        _bad_id,
        _dataset_is_fixed,
        _dataset_has_unpublished_contents,
        _forbidden_user,
        _dataset_is_empty,
        _datacite_draft_failed,
        _db_set_doi_draft_failed,
        _db_set_dataset_doi_failed,
        _datacite_publish_failed,
        _db_publish_dataset_doi_failed,
    ),
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def assign_doi(
    dataset_id: Annotated[
        str, Path(description='The unique dataset id of the dataset to assign DOI.')
    ],
    user: Annotated[
        User,
        Depends(get_current_user([Scope.DATASETS_ASSIGN_DOI], allow_anonymous=False)),
    ],
):
    """
    Assign a DOI at DataCite to this dataset.

    Conditions:
        - The DataCite service must be enabled on this deployment.
        - The dataset must contain at least one entry.
        - The user must be the owner of the dataset.
    """

    if not config.datacite.enabled:
        raise _create_exception(*_datacite_not_enabled)

    # Check if dataset exists
    dataset = DatasetDefinitionCls.m_def.a_mongo.objects(dataset_id=dataset_id).first()
    if dataset is None:
        raise _create_exception(*_bad_id)

    # Check if current user is owner of the dataset
    if dataset.user_id != user.user_id:
        raise _create_exception(*_forbidden_user)

    # Check if dataset already has a DOI
    if dataset.doi is not None:
        doi = DOI.objects(doi=dataset.doi).first()
        if doi is None:
            raise _create_exception(*_dataset_doi_no_document)

        if doi.state != 'findable':
            raise _create_exception(*_dataset_doi_unpublished)

        raise _create_exception(*_dataset_is_fixed)

    # Check if dataset is empty
    response = search(
        owner='admin',
        query={'datasets.dataset_id': dataset_id},
        pagination=MetadataPagination(page_size=0),
        user_id=config.services.admin_user_id,
    )
    if response.pagination.total == 0:
        raise _create_exception(*_dataset_is_empty)

    # Check if dataset has unpublished contents
    response = search(
        owner='admin',
        query={'datasets.dataset_id': dataset_id, 'published': False},
        pagination=MetadataPagination(page_size=0),
        user_id=config.services.admin_user_id,
    )

    if response.pagination.total > 0:
        raise _create_exception(*_dataset_has_unpublished_contents)

    doi = DOI.create()  # does not save DOI document

    try:
        doi.create_draft(
            title=f'NOMAD dataset: {dataset.dataset_name}',
            publicationYear=datetime.now(timezone.utc).year,
            user=user,
        )  # saves DOI document
    except DataCiteException:
        raise _create_exception(*_datacite_draft_failed)
    except MongoEngineException:
        raise _create_exception(*_db_set_doi_draft_failed)

    try:
        dataset.doi = doi.doi
        dataset.save()
    except MongoEngineException:
        raise _create_exception(*_db_set_dataset_doi_failed)

    try:
        doi.make_findable()
    except DataCiteException:
        raise _create_exception(*_datacite_publish_failed)
    except MongoEngineException:
        raise _create_exception(*_db_publish_dataset_doi_failed)

    return {'dataset_id': dataset.dataset_id, 'data': dataset}
