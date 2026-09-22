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
This module contains the DataciteClient class for managing DOIs via their API.
See https://support.datacite.org/docs/api.
"""

import requests
from requests.auth import HTTPBasicAuth

from nomad.config import config
from nomad.utils import get_logger

from .models import DoiRequestAttributes, DoiRequestPayload


class DataCiteException(Exception):
    """DataCite-related errors."""

    pass


def _handle_datacite_errors(response, msg: str, doi: str | None = None):
    """Handles errors from DataCite API responses."""
    if response is None:
        get_logger(__name__).error(
            f'could not {msg}, response is None',
            doi=doi,
        )
        raise DataCiteException()

    if response.status_code >= 300:
        get_logger(__name__).error(
            f'could not {msg}',
            status_code=response.status_code,
            body=response.content,
            doi=doi,
        )
        raise DataCiteException()


class DataCiteClient:
    """
    A class for interacting with the DataCite API.

    Features:
      - Get metadata for DOIs
      - Create DOI
      - Update DOI
      - Delete DOI (draft)

      - Check API accessibility (heartbeat)
    """

    def __init__(self):
        self.enabled = config.datacite.enabled
        self.host = config.datacite.mds_host
        self.auth = HTTPBasicAuth(
            config.datacite.user, config.datacite.password.get_secret_value()
        )
        self.prefix = config.datacite.prefix

    # Raw methods

    def _request(
        self,
        method: str,
        url: str,
        *,
        msg=None,
        doi=None,
        auto_auth: bool = True,
        **kwargs,
    ) -> requests.Response:
        url = f'{self.host}/{url}'
        if auto_auth:
            kwargs.setdefault('auth', self.auth)

        response = requests.request(method, url, **kwargs)
        _handle_datacite_errors(response, msg, doi)

        return response

    def _get(self, url: str, **kwargs) -> requests.Response:
        return self._request('GET', url, **kwargs)

    def _put(self, url: str, **kwargs) -> requests.Response:
        headers: dict = kwargs.setdefault('headers', {})
        headers.setdefault('Content-Type', 'application/vnd.api+json')
        return self._request('PUT', url, **kwargs)

    def _post(self, url: str, **kwargs) -> requests.Response:
        headers: dict = kwargs.setdefault('headers', {})
        headers.setdefault('Content-Type', 'application/vnd.api+json')
        return self._request('POST', url, **kwargs)

    def _delete(self, url: str, **kwargs) -> requests.Response:
        return self._request('DELETE', url, **kwargs)

    # DOI methods

    def get_dois(self, **kwargs) -> list[dict] | None:
        if not self.enabled:
            return None

        response = self._get('dois', msg='GET DOIs', **kwargs)
        return response.json()

    def post_doi(self, attributes: DoiRequestAttributes) -> dict | None:
        if not self.enabled:
            return None

        payload = DoiRequestPayload.from_attributes(attributes).model_dump(
            exclude_none=True
        )
        response = self._post(f'dois', json=payload, msg='POST DOI')
        return response.json()

    def put_doi(self, doi: str, attributes: DoiRequestAttributes) -> dict | None:
        if not self.enabled:
            return None

        payload = DoiRequestPayload.from_attributes(attributes).model_dump(
            exclude_none=True
        )
        response = self._put(f'dois/{doi}', json=payload, msg='PUT DOI', doi=doi)
        return response.json()

    def delete_doi(self, doi: str) -> bool | None:
        if not self.enabled:
            return None

        response = self._delete(f'dois/{doi}', msg='DELETE DOI', doi=doi)
        return response.status_code == 204

    # Misc methods

    def heartbeat(self):
        """Checks if the DataCite API is accessible."""
        response = self._get('heartbeat', msg='heartbeat')
        return response.status_code == 200
