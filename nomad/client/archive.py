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

import asyncio
import threading
from asyncio import Semaphore
from itertools import islice
from time import monotonic
from typing import Any

from click import progressbar
from httpx2 import AsyncClient, Timeout
from keycloak import KeycloakOpenID

from nomad import metainfo as mi
from nomad.config import config
from nomad.datamodel import ClientContext, EntryArchive
from nomad.utils import dict_to_dataframe


class RunThread(threading.Thread):
    def __init__(self, func, args, kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.result = None
        super().__init__()

    def run(self):
        self.result = asyncio.run(self.func(*self.args, **self.kwargs))


def run_async(func, *args, **kwargs):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # In jupyter there is already a loop running
        thread = RunThread(func, args, kwargs)
        thread.start()
        thread.join()
        return thread.result
    else:
        # Create our own loop
        return asyncio.run(func(*args, **kwargs))


def _collect(
    required, parent_section: mi.Section | None = None, parent_path: str | None = None
) -> set:
    """
    Flatten required quantities for uncoupled query
    """

    quantities: set = set()

    if not isinstance(required, dict):
        return quantities

    for key, value in required.items():
        # some keys may index, get the exact name
        definition_name = key.split('[')[0]

        # validate definition name
        definition = None
        if parent_section:
            definition = parent_section.all_properties.get(definition_name)

        if parent_path:
            definition_name = f'{parent_path}.{definition_name}'

        if not definition:
            # We have to stop here because we cannot further statically analyze the
            # required. We have to assume the remainder is based on a polymorphic subsection,
            # and we cannot know the exact subsection type.
            return quantities

        quantities.add(definition_name)

        if isinstance(definition, mi.SubSection):
            quantities.update(_collect(value, definition.section_def, definition_name))
        elif isinstance(definition, mi.Quantity) and isinstance(
            definition.type, mi.Reference
        ):
            next_parent_section = definition.type.target_section_def.m_resolved()
            parent_path = next_parent_section.path
            if parent_path in ['__ambiguous__', '__no_archive_path__']:
                continue
            quantities.update(_collect(value, next_parent_section, parent_path))

    return quantities


class ArchiveQuery:
    """
    The async implementation works well with a large number of uploads, each has a small number of
    entries.

    Authentication is created by using valid `username` and `password`.
    If any is invalid, authenticated access is not available.

    Setting a high value for `semaphore` may cause the server to return 500, 502, 504 errors.

    Use `max_requests_per_second` to control the number of requests per second in case the server
    has a rate limit.

    Params:
        owner (str): ownership scope
        query (dict): query
        required (dict): required properties
        url (str): server url, if not specified, the default official NOMAD one is used
        after (str): specify the starting upload id to query, if users have knowledge of uploads,
            they may wish to start from a specific upload, default: ''
        results_max (int): maximum results to query, default: 1000
        page_size (int): size of page in each query, cannot exceed the limit 10000, default: 100
        batch_size (int): size of page in each download request, default: 10
        username (str): username for authenticated access, default: ''
        password (str): password for authenticated access, default: ''
        retry (int): number of retry when fetching uploads, default: 4
        sleep_time (float): sleep time for retry, default: 4.
        semaphore (int): number of concurrent downloads, this depends on server settings, default: 4
        max_requests_per_second (int): maximum requests per second, default: 999999
    """

    def __init__(
        self,
        owner: str = 'visible',
        query: dict | None = None,
        required: dict | None = None,
        url: str | None = None,
        after: str | None = None,
        results_max: int = 1000,
        page_size: int = 100,
        batch_size: int = 10,
        username: str | None = None,
        password: str | None = None,
        retry: int = 1,
        sleep_time: float = 4.0,
        from_api: bool = False,
        semaphore: int = 8,
        max_requests_per_second: int = 20,
    ):
        self._owner: str = owner
        self._required = required if required is not None else '*'
        self._query_list: list[dict] = [
            {'quantities': list(_collect(self._required, EntryArchive.m_def))}
        ]
        if query:
            self._query_list.append(query)
        self._url: str = url if url else config.client.url + '/v1'
        self._after: str = after
        self._results_max: int = results_max if results_max > 0 else 1000
        self._page_size: int = min(page_size, 9999) if page_size > 0 else 100
        self._page_size = min(self._page_size, self._results_max)
        self._batch_size: int = batch_size if batch_size > 0 else 10
        self._retry: int = retry if retry >= 0 else 4
        self._sleep_time: float = sleep_time if sleep_time > 0.0 else 4.0
        self._semaphore = min(10, semaphore) if semaphore > 0 else 4
        self._results_actual: int = 0
        self._max_requests_per_second: int = max_requests_per_second

        self._start_time: float = 0.0
        self._accumulated_requests: int = 0

        from nomad.client import Auth

        self._auth = Auth(user=username, password=password, from_api=from_api)

        self._oidc = KeycloakOpenID(
            server_url=config.keycloak.public_server_url,
            realm_name=config.keycloak.realm_name,
            client_id=config.keycloak.client_id,
        )

        # local data storage
        self._entries: list[tuple[str, str]] = []
        self._entries_dict: list[dict] = []
        self._current_after: str = self._after
        self._current_results: int = 0

        # check if URL has the form of http(s)://<hostname>/api/v1
        # https://nomad-lab.eu/prod/v1/api/v1
        if self._url.endswith('/'):
            self._url = self._url[:-1]
        if not self._url.endswith('/api/v1'):
            self._url += '/v1' if self._url.endswith('/api') else '/api/v1'

    @property
    def _query(self) -> dict:
        return {'and': self._query_list}

    @property
    def _fetch_url(self) -> str:
        return f'{self._url}/entries/query'

    @property
    def _download_url(self) -> str:
        return f'{self._url}/entries/archive/query'

    @property
    def _fetch_request(self) -> dict:
        """
        Generate fetch request.
        """

        request: dict = {
            'owner': self._owner,
            'query': self._query,
            'pagination': {'page_size': self._page_size},
            'required': {'include': ['entry_id', 'upload_id']},
        }

        if self._current_after:
            request['pagination']['page_after_value'] = self._current_after

        return request

    def _download_request(self, entry_ids: list[str]) -> dict:
        """
        Generate download request.
        """

        request: dict[str, Any] = dict(owner=self._owner, required=self._required)
        request['query'] = {'and': []}
        for t_list in self._query_list:
            request['query']['and'].append(t_list)
        request['query']['and'].append({'entry_id:any': entry_ids})
        request.setdefault('pagination', {'page_size': len(entry_ids)})

        return request

    def clear(self):
        """
        Clear all fetched and downloaded data. Users can then call .fetch() and .download() again.
        """

        self._entries = []
        self._entries_dict = []
        self._current_after = self._after
        self._current_results = 0
        self._results_actual = 0

    @property
    def _actual_max(self):
        """
        The actual maximum number of entries available.
        """

        if self._results_actual:
            return min(self._results_actual, self._results_max)

        return self._results_max

    @property
    def _allowed_requests(self) -> float:
        """
        The number of requests allowed since the start of the download.
        This is controlled by the maximum number of requests per second.
        """
        duration: float = monotonic() - self._start_time
        return self._max_requests_per_second * duration

    async def _fetch_async(self, number: int) -> int:
        """
        There is no need to perform fetching asynchronously as the required number of uploads
        depends on previous queries.

        It is just a wrapper to avoid the `requests` library.

        Params:
            number (int): approx. number of **entries** to be fetched

        Returns:
            The number of entries fetched
        """

        # if the maximum number of entries has been previously fetched
        # not going to fetch more entries
        if self._current_results >= self._actual_max:
            return 0

        # get all entries at once
        if number == 0:
            number = self._actual_max - self._current_results

        num_retry: int = 0
        num_entry: int = 0

        async with AsyncClient(timeout=Timeout(timeout=300)) as session:
            while True:
                response = await session.post(
                    self._fetch_url,
                    json=self._fetch_request,
                    headers=self._auth.headers(),
                )

                if response.status_code >= 400:
                    if response.status_code < 500:
                        response_json = response.json()
                        reason = (
                            response_json.get('description')
                            or response_json.get('detail')
                            or 'unknown reason'
                        )
                        raise ValueError(
                            f'Server returns {response.status_code}: {reason}'
                        )
                    if response.status_code in (500, 502, 504):
                        if num_retry > self._retry:
                            print('Maximum retry reached.')
                            break
                        else:
                            print(f'Retrying in {self._sleep_time} seconds...')
                            await asyncio.sleep(self._sleep_time)
                            num_retry += 1
                            continue

                response_json = response.json()

                pagination = response_json['pagination']
                self._current_after = pagination.get('next_page_after_value', None)
                self._results_actual = pagination.get('total', 0)

                data = [
                    (entry['entry_id'], entry['upload_id'])
                    for entry in response_json['data']
                ]
                current_size: int = len(data)

                # no more entries
                if current_size == 0:
                    break

                if self._current_results + current_size > self._actual_max:
                    # current query has sufficient entries to exceed the limit
                    data = data[: self._actual_max - self._current_results]
                    self._current_results += len(data)
                    self._entries.extend(data)
                    break
                else:
                    # current query should be added
                    num_entry += current_size
                    self._current_results += current_size
                    self._entries.extend(data)

                    # if exceeds the required number, exit
                    # `self._current_after` is automatically set
                    if num_entry >= number:
                        break

                if self._current_after is None:
                    break

        print(f'{num_entry} entries are qualified and added to the download list.')

        return num_entry

    async def _download_async(
        self, number: int, *, as_plain_dict: bool = False
    ) -> list[EntryArchive]:
        """
        Download required entries asynchronously.

        Params:
            number (int): number of **entries** to download
            as_plain_dict (bool): return as plain dictionary

        Returns:
            A list of EntryArchive
        """
        semaphore = Semaphore(self._semaphore)

        actual_number: int = min(number, len(self._entries))

        self._start_time = monotonic()
        self._accumulated_requests = 0

        def batched(iterable, chunk_size):
            iterator = iter(iterable)
            while chunk := list(islice(iterator, chunk_size)):
                yield chunk

        with progressbar(
            length=actual_number, label=f'Downloading {actual_number} entries...'
        ) as bar:
            async with AsyncClient(timeout=Timeout(timeout=300)) as session:
                tasks = [
                    asyncio.create_task(
                        self._acquire(ids, session, semaphore, bar, as_plain_dict)
                    )
                    for ids in batched(self._entries[:actual_number], self._batch_size)
                ]
                results = await asyncio.gather(*tasks)

        return [archive for result in results if result for archive in result]  # type: ignore

    async def _acquire(
        self,
        ids: list[tuple[str, str]],
        session: AsyncClient,
        semaphore: Semaphore,
        bar,
        as_plain_dict: bool,
    ) -> list[EntryArchive | dict] | None:
        """
        Perform the download task.

        Params:
            ids (list[tuple[str, str]]): a list of tuples of entry id and upload id

            session (httpx2.AsyncClient): HTTPX2 client

            semaphore (asyncio.Semaphore): semaphore

        Returns:
            A list of EntryArchive
        """
        entry_ids = [x for x, _ in ids]

        request = self._download_request(entry_ids)

        async with semaphore:
            while self._accumulated_requests > self._allowed_requests:
                await asyncio.sleep(0.1)

            self._accumulated_requests += 1

            response = await session.post(
                self._download_url, json=request, headers=self._auth.headers()
            )
            bar.update(len(entry_ids))
            for item in ids:
                self._entries.remove(item)

            if response.status_code >= 400:
                print(
                    f'Request returns {response.status_code}, will retry in the next download call...'
                )
                self._entries.extend(ids)
                return None

            # successfully downloaded data
            results: list = []

            response_json: list = response.json()['data']

            if as_plain_dict:
                results = [x['archive'] for x in response_json]
            else:
                for index in range(len(ids)):
                    entry_id, upload_id = ids[index]
                    context = ClientContext(
                        self._url, upload_id=upload_id, auth=self._auth
                    )
                    result = EntryArchive.m_from_dict(
                        response_json[index]['archive'], m_context=context
                    )

                    if not result:
                        print(
                            f'No result returned for id {entry_id}, is the query proper?'
                        )

                    results.append(result)

            return results

    def fetch(self, number: int = 0) -> int:
        """
        Fetch uploads from remote.

        Params:
            number (int): number of **entries** to fetch

        Returns:
            The number of entries fetched
        """

        print('Fetching remote uploads...')

        return run_async(self._fetch_async, number)

    def download(
        self, number: int = 0, *, as_plain_dict: bool = False
    ) -> list[EntryArchive]:
        """
        Download fetched entries from remote.
        Automatically call .fetch() if not fetched.

        Params:
            number (int): number of **entries** to download at a single time

        Returns:
            A list of downloaded EntryArchive
        """

        pending_size: int = len(self._entries)

        # download all at once
        if number == 0:
            number = pending_size

        if number == 0:
            # empty list, fetch as many as possible
            number = self.fetch()
        elif pending_size < number:
            # if not sufficient fetched entries, fetch first
            self.fetch(number - pending_size)

        async_query = run_async(
            self._download_async, number, as_plain_dict=as_plain_dict
        )
        self._entries_dict.append(async_query)
        return async_query

    async def async_fetch(self, number: int = 0) -> int:
        """
        Asynchronous interface for use in a running event loop.
        """

        print('Fetching remote uploads...')

        return await self._fetch_async(number)

    async def async_download(
        self, number: int = 0, *, as_plain_dict: bool = False
    ) -> list[EntryArchive]:
        """
        Asynchronous interface for use in a running event loop.
        """

        pending_size: int = len(self._entries)

        # download all at once
        if number == 0:
            number = pending_size

        if number == 0:
            # empty list, fetch as many as possible
            number = await self.async_fetch()
        elif pending_size < number:
            # if not sufficient fetched entries, fetch first
            await self.async_fetch(number - pending_size)

        return await self._download_async(number, as_plain_dict=as_plain_dict)

    def entry_list(self) -> list[tuple[str, str]]:
        return self._entries

    def entries_to_dataframe(
        self,
        keys_to_filter: list[str] | None = None,
        resolve_references: bool = False,
        query_selection: str | list[str] = 'last',
    ):
        """
        Interface to convert the archives to pandas dataframe.
        Params:
            keys_to_filter (int): number of **entries** to download at a single time
            resolve_references (bool): boolean if the references are to be resolved
            query_selection (str or list[int]): selection of which archives to be used for conversion.
                Available options are either 'last', 'all' or a list of indices
                that each denoting the index of download call (e.g. [0,2,1])
        Returns:
            pandas dataframe of the downloaded (and selected) archives
        """
        t_list: list[Any] | dict = []
        if query_selection == 'all':
            t_list = [item for sublist in self._entries_dict for item in sublist]
        elif query_selection == 'last':
            t_list = self._entries_dict[-1]
        elif isinstance(query_selection, list):
            if not all(isinstance(i, int) for i in query_selection):
                raise TypeError("All elements in 'query_selection' must be integers.")
            t_list = [
                item
                for i, sublist in enumerate(self._entries_dict)
                if i in query_selection
                for item in sublist
            ]
        else:
            return

        list_of_entries_dict = [
            aq.m_to_dict(resolve_references=resolve_references) for aq in t_list
        ]
        if not keys_to_filter:
            keys_to_filter = []
        return dict_to_dataframe(list_of_entries_dict, keys_to_filter=keys_to_filter)
