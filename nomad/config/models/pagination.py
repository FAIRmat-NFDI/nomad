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
from inspect import cleandoc  # utils.strip caused circular imports

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError


class Direction(StrEnum):
    """
    Order direction, either ascending (`asc`) or descending (`desc`)
    """

    asc = 'asc'
    desc = 'desc'


class PaginationBaseModel(BaseModel):
    """Defines request-agnostic pagination parameters (size, ordering)."""

    page_size: int | None = Field(
        10,
        description=cleandoc("""
            The page size, e.g. the maximum number of items contained in one response.
            A `page_size` of 0 will return no results.
        """),
    )
    order_by: str | None = Field(
        None,
        description=cleandoc("""
            The results are ordered by the values of this field. If omitted, default
            ordering is applied.
        """),
    )
    order: Direction | None = Field(
        Direction.asc,
        description=cleandoc("""
            The ordering direction of the results based on `order_by`. Its either
            ascending `asc` or descending `desc`. Default is `asc`.
        """),
    )

    model_config = ConfigDict(use_enum_values=True)

    @field_validator('page_size')
    @classmethod
    def validate_page_size(cls, page_size):
        if page_size < 0:
            raise PydanticCustomError('invalid_page_size', 'page_size must be >= 0')
        return page_size
