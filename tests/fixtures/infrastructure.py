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

import os
import shutil
import tempfile
import time
from collections.abc import AsyncGenerator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import TypeAlias
from unittest.mock import MagicMock

import elasticsearch
import elasticsearch.exceptions
import pytest
import pytest_asyncio
from mongoengine import OperationError
from pymongo import AsyncMongoClient
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from xdist.scheduler.loadscope import LoadScopeScheduling

from nomad import infrastructure
from nomad.actions import TaskQueue
from nomad.actions.activities.utils import get_nomad_internal_activities
from nomad.actions.workflows.utils import get_nomad_internal_workflows
from nomad.config import config
from nomad.infrastructure import index_builtin_packages


class TrieNode:
    def __init__(self):
        """
        Initialize a TrieNode.
        """
        self.children = {}
        self.is_end_of_word = False
        self.matched_prefix = None


class Trie:
    def __init__(self):
        """
        Initialize a Trie data structure.
        """
        self.root = TrieNode()

    def insert(self, word):
        """
        Insert a word into the Trie.

        Args:
            word (str): The word to be inserted.
        """
        node = self.root
        for char in word:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
        node.is_end_of_word = True
        node.matched_prefix = word


class CustomScheduler(LoadScopeScheduling):
    """
    Custom test scheduler for parallel test execution with pytest-xdist.

    Tests whose nodeid matches an integration prefix share a scope unique to that
    prefix. That keeps tests from the same file (or directory prefix) on one
    worker so they cannot collide, while different integration modules can still
    run in parallel on gw0/gw1/gw2.

    Parser and normalizer suites stay in this list: they previously failed when
    xdist freely load-balanced them. Each still gets its own scope, so they are
    not queued behind cli/upload/archive on a single worker.

    The scheduler uses a Trie for prefix matching.
    """

    integration_tests = [
        'tests/app/v1/routers/test_apps.py',
        'tests/app/v1/routers/test_federation.py',
        'tests/app/v1/routers/uploads/test_uploads_non_xdist.py',
        'tests/app/v1/routers/uploads/test_upload_actions.py',
        'tests/archive/test_archive.py',
        'tests/normalizing',
        'tests/parsing/test_parsing.py',
        'tests/processing/test_rfc3161.py',
        'tests/test_cli.py',
    ]

    def __init__(self, config, log):
        """
        Initialize the CustomScheduler instance and build a Trie with integration tests.
        """
        self.trie = Trie()
        for test in self.integration_tests:
            self.trie.insert(test)
        super().__init__(config, log)

    def _split_scope(self, nodeid):
        """
        Split the scope of the test based on integration tests.

        Args:
            nodeid (str): The identifier of the test.

        Returns:
            str: ``integration-<prefix>`` if nodeid matches an integration test,
            else nodeid itself.
        """
        node = self.trie.root
        for char in nodeid:
            if char in node.children:
                node = node.children[char]
                if node.is_end_of_word:
                    return f'integration-{node.matched_prefix}'
            else:
                break
        return nodeid


def pytest_xdist_make_scheduler(config, log):
    """
    Factory function for creating a custom scheduler for pytest-xdist parallel test execution.

    This function is used by pytest-xdist to create a custom test scheduler for parallel test execution.
    It is responsible for instantiating and returning an instance of the CustomScheduler class,
    which determines how tests are distributed and executed across different workers in parallel.

    Returns:
        CustomScheduler: An instance of the CustomScheduler class, configured for parallel test scheduling.
    """
    return CustomScheduler(config, log)


@pytest.fixture(scope='session', autouse=True)
def raw_files_infra(request):
    parent_directory = Path('.volumes')
    if not os.path.isdir(parent_directory):
        os.makedirs(parent_directory, exist_ok=True)
    directory = tempfile.TemporaryDirectory(dir=parent_directory, prefix='test_fs')
    config.fs.tmp = tempfile.TemporaryDirectory(dir=directory.name, prefix='tmp').name
    config.fs.staging = os.path.relpath(
        tempfile.TemporaryDirectory(dir=directory.name, prefix='staging').name,
        start=parent_directory.parent,
    )
    config.fs.public = os.path.relpath(
        tempfile.TemporaryDirectory(dir=directory.name, prefix='public').name,
        start=parent_directory.parent,
    )
    config.fs.staging_external = os.path.abspath(config.fs.staging)
    config.fs.public_external = os.path.abspath(config.fs.public)
    config.fs.prefix_size = 2
    if request.config.getoption('--s3-storage'):
        config.fs.public_fs.protocol = 's3'
    config.fs.public_fs.extra = {
        'endpoint_url': 'http://localhost:8333',
        'key': 'nomad_test',
        'secret': 'nomad_test',
    }
    clear_raw_files()
    yield
    directory.cleanup()


@pytest.fixture(scope='module')
def raw_files_module(raw_files_infra):
    """Provides cleaned out files directory structure per module. Clears files before test."""
    clear_raw_files()


@pytest.fixture(scope='function')
def raw_files_function(raw_files_infra):
    """Provides cleaned out files directory structure per function. Clears files before test."""
    clear_raw_files()


def clear_raw_files():
    directories = [config.fs.staging, config.fs.public, config.fs.tmp]
    for directory in directories:
        try:
            shutil.rmtree(directory)
        except FileNotFoundError:
            pass

        os.makedirs(directory)


@pytest.fixture(scope='session')
def mongo_db_name(worker_id):
    return f'test_db_{worker_id}'


@pytest.fixture(scope='session')
def mongo_infra(monkeysession, mongo_db_name):
    monkeysession.setattr('nomad.config.mongo.db_name', mongo_db_name)
    # disconnecting and connecting again results in an empty database with mongomock
    monkeysession.setattr('mongoengine.disconnect', lambda *args, **kwargs: None)
    return infrastructure.setup_mongo()


@pytest_asyncio.fixture(scope='function')
async def async_mongo_infra(mongo_infra, mongo_db_name):
    """Initialize AsyncMongoClient for async MongoDB access in tests."""
    async_mongo_client = AsyncMongoClient(
        host=config.mongo.host, port=config.mongo.port
    )
    yield async_mongo_client
    await async_mongo_client.close()


@pytest_asyncio.fixture(scope='function')
async def async_mongo_function(async_mongo_infra, mongo_db_name):
    """Initializes mongo db and beanie for the current function scope.

    Depends on async_mongo_infra to provide the async mongo client.
    """
    from beanie import init_beanie

    from nomad.mongo.action import ActionDocument

    await init_beanie(
        database=async_mongo_infra[mongo_db_name],
        document_models=[ActionDocument],
    )
    yield async_mongo_infra


def clear_mongo(mongo_infra, mongo_db_name):
    # Some test cases need to reset the database connection
    infrastructure.mongo_client.drop_database(mongo_db_name)
    return infrastructure.mongo_client


@pytest.fixture(scope='module')
def mongo_module(mongo_infra, mongo_db_name):
    """Provides a cleaned mocked mongo per module."""
    return clear_mongo(mongo_infra, mongo_db_name)


@pytest.fixture(scope='function')
def mongo_function(mongo_infra, mongo_db_name):
    """Provides a cleaned mocked mongo per function."""
    return clear_mongo(mongo_infra, mongo_db_name)


@pytest.fixture(scope='function')
def mongo_function_with_indexed_def(mongo_function):
    """Provides a cleaned mocked mongo per function."""
    index_builtin_packages()
    return mongo_function


@pytest.fixture(scope='session')
def elastic_test_entries_index(worker_id):
    return f'nomad_entries_v1_test_{worker_id}'


@pytest.fixture(scope='session')
def elastic_test_indices(elastic_test_entries_index):
    return [elastic_test_entries_index]


@pytest.fixture(scope='session')
def elastic_infra(monkeysession, elastic_test_entries_index):
    """Provides elastic infrastructure to the session"""
    monkeysession.setattr(
        'nomad.config.elastic.entries_index', elastic_test_entries_index
    )

    # attempt to remove and recreate all indices
    return clear_elastic_infra([elastic_test_entries_index])


def clear_elastic_infra(indices):
    """
    Removes and re-creates all indices and mappings.
    """
    from elasticsearch_dsl import connections

    connection = connections.create_connection(
        hosts=[f'{config.elastic.host}:{config.elastic.port}']
    )

    for index in indices:
        try:
            connection.indices.delete(index=index)
        except Exception:
            pass

    return infrastructure.setup_elastic()


def clear_elastic(elastic_infra, indices):
    """
    Removes all contents from the existing indices.
    """
    try:
        for index in indices:
            retry_count = 30
            while True:
                try:
                    elastic_infra.delete_by_query(
                        index=index,
                        body=dict(query=dict(match_all={})),
                        wait_for_completion=True,
                        refresh=True,
                    )
                    break  # Success! Break the retry loop
                except elasticsearch.exceptions.ConflictError:
                    if retry_count:
                        # Sleep and try again
                        time.sleep(0.5)
                        retry_count -= 1
                    else:
                        raise
                except elasticsearch.exceptions.NotFoundError:
                    # Happens if a test removed indices without recreating them.
                    clear_elastic_infra(indices)
                    break
                except elasticsearch.exceptions.TransportError:
                    if retry_count:
                        # Sleep and try again
                        time.sleep(1)
                        retry_count -= 1
                    else:
                        raise
    except elasticsearch.exceptions.NotFoundError:
        # Happens if a test removed indices without recreating them.
        clear_elastic_infra(indices)

    assert infrastructure.elastic_client is not None
    return elastic_infra


@pytest.fixture(scope='module')
def elastic_module(elastic_infra, elastic_test_indices):
    """Provides a clean elastic per module. Clears elastic before test."""
    return clear_elastic(elastic_infra, elastic_test_indices)


@pytest.fixture(scope='function')
def elastic_function(elastic_infra, elastic_test_indices):
    """Provides a clean elastic per function. Clears elastic before test."""
    return clear_elastic(elastic_infra, elastic_test_indices)


@pytest.fixture
def reset_infra(mongo_function, elastic_function):
    """Fixture that resets infrastructure after deleting db or search index."""
    yield None


@pytest.fixture(scope='function')
def proc_infra(elastic_function, mongo_function, raw_files_function, monkeypatch):
    """Combines all fixtures necessary for processing (elastic, worker, files, mongo)"""
    return dict(elastic=elastic_function)


TemporalWorkerContext: TypeAlias = Callable[
    [], AbstractAsyncContextManager[WorkflowEnvironment]
]


@pytest.fixture(scope='function')
def temporal_worker(
    elastic_infra,
    mongo_function,
    raw_files_function,
    monkeypatch,
) -> TemporalWorkerContext:
    """Combines all fixtures necessary for temporal processing (elastic, files, mongo)"""
    temporal_activities = get_nomad_internal_activities()
    temporal_workflows = get_nomad_internal_workflows()

    @asynccontextmanager
    async def worker_context() -> AsyncGenerator[WorkflowEnvironment, None]:
        async with await WorkflowEnvironment.start_local() as env:

            async def mock_get_client():
                return env.client

            # mock the get_client function to use the client from the local test server
            monkeypatch.setattr('nomad.processing.data.get_client', mock_get_client)

            with ThreadPoolExecutor(max_workers=1) as executor:
                async with Worker(
                    env.client,
                    task_queue=TaskQueue.NOMAD_INTERNAL_WORKFLOWS,
                    workflows=temporal_workflows,
                    activities=temporal_activities,
                    activity_executor=executor,
                ):
                    yield env

    return worker_context


class DataciteMock:
    """Helper class to en/disable datacite, mock responses for doi requests."""

    def __init__(self, monkeypatch):
        self._monkeypatch = monkeypatch

    def set_enabled(self, enabled: bool):
        self._monkeypatch.setattr(config.datacite, 'enabled', enabled)

    def set_requests(self, status_code: int, response_ok: bool, text: str):
        def request(*args, **kwargs):
            mock_response = MagicMock()
            mock_response.status_code = status_code
            mock_response.ok = response_ok
            mock_response.text = text
            return mock_response

        self._monkeypatch.setattr(f'nomad.datacite.client.requests.request', request)


@pytest.fixture(scope='function')
def datacite_mock(monkeypatch):
    """Enables datacite, mocks success on all requests in doi module."""

    datacite_mock = DataciteMock(monkeypatch)
    datacite_mock.set_enabled(True)
    # external response is always ok; fail cases should not request datacite at all
    datacite_mock.set_requests(200, True, 'Success')
    return datacite_mock


@pytest.fixture(scope='function')
def mock_mongo_fail_save(monkeypatch):
    def failing_save(*args, **kwargs):
        raise OperationError('Enforced mongo error')

    def setup_mock(target):
        monkeypatch.setattr(target, 'save', failing_save, raising=True)

    return setup_mock
