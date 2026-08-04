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

import builtins
import importlib
import logging
import os
import socketserver
import sys
import tempfile
import time
import warnings
from io import StringIO
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient

from nomad.config import config

# make sure to disable logstash (the logs can interfere with the testing, especially for logtransfer)
config.logstash.enabled = False  # noqa: E402  # this must be set *before* the other modules are imported

from nomad import utils
from nomad.app.main import app
from nomad.utils import structlogging

# Set up pytest to pass control to the debugger on an exception.
if os.getenv('_PYTEST_RAISE', '0') != '0':

    @pytest.hookimpl(tryfirst=True)
    def pytest_exception_interact(call):
        raise call.excinfo.value

    @pytest.hookimpl(tryfirst=True)
    def pytest_internalerror(excinfo):
        raise excinfo.value


test_log_level = logging.CRITICAL

warnings.simplefilter('ignore')

structlogging.ConsoleFormatter.short_format = True
setattr(logging, 'Formatter', structlogging.ConsoleFormatter)

pytest_plugins = (
    'tests.fixtures.infrastructure',
    'tests.fixtures.data',
    'tests.fixtures.groups',
    'tests.fixtures.group_uploads',
    'tests.fixtures.mails',
    'tests.fixtures.users',
)


import structlog
from structlog.testing import LogCapture


@pytest.fixture(scope='function')
def log_output():
    cap = LogCapture()
    # Modify `_Configuration.default_processors` set via `configure` but always
    # keep the list instance intact to not break references held by bound
    # loggers.
    processors = structlog.get_config()['processors']
    old_processors = processors.copy()
    try:
        # clear processors list and use LogCapture for testing
        processors.clear()
        processors.append(cap)
        structlog.configure(processors=processors)
        yield cap
    finally:
        # remove LogCapture and restore original processors
        processors.clear()
        processors.extend(old_processors)
        structlog.configure(processors=processors)


def pytest_addoption(parser):
    help = 'Set this < 1.0 to speed up worker cleanup. May leave tasks running.'
    parser.addoption('--celery-inspect-timeout', type=float, default=1.0, help=help)
    help = (
        'Only run tests with these fixtures and exclude ones prefixed with "!".'
        'Does not consider dynamically loaded fixtures (e.g. `request.getfixturevalue`).'
    )
    parser.addoption('--fixture-filters', nargs='+', help=help)
    parser.addoption(
        '--s3-storage',
        action='store_true',
        default=False,
        help='Enable S3-backed storage for tests',
    )


def filter_tests_by_fixtures(items, config):
    """Filter tests by fixture names based on CLI argument `--fixture-filters`.

    Will include tests that have all the fixtures in `--fixture-filters`
    and exclude tests that have any of the fixtures prefixed with '!'.

    Does not consider dynamically loaded fixtures (e.g. `request.getfixturevalue`)."""

    fixture_filters = config.getoption('fixture_filters')
    if not fixture_filters:
        return

    must_filters = set(f for f in fixture_filters if not f.startswith('!'))
    not_filters = set(f[1:] for f in fixture_filters if f.startswith('!'))

    selected_items = []
    deselected_items = []

    for item in items:
        fixtures = getattr(item, 'fixturenames', ())
        if must_filters.issubset(fixtures) and not_filters.isdisjoint(fixtures):
            selected_items.append(item)
        else:
            deselected_items.append(item)

    config.hook.pytest_deselected(items=deselected_items)
    items[:] = selected_items


def pytest_collection_modifyitems(items, config):
    """Manipulate the list of test items (pytest hook)."""
    filter_tests_by_fixtures(items, config)


@pytest.fixture(scope='function')
def tmp():
    parent_directory = '.volumes'
    if not os.path.isdir(parent_directory):
        os.makedirs(parent_directory, exist_ok=True)
    directory = tempfile.TemporaryDirectory(dir=parent_directory, prefix='test_tmp')
    yield directory.name
    directory.cleanup()


@pytest.fixture(scope='session')
def monkeysession(request):
    from _pytest.monkeypatch import MonkeyPatch

    mpatch = MonkeyPatch()
    yield mpatch
    mpatch.undo()


@pytest.fixture(scope='session', autouse=True)
def nomad_logging(monkeysession):
    utils.set_console_log_level(test_log_level)
    monkeysession.setattr('logging.Logger.setLevel', lambda *args, **kwargs: None)
    monkeysession.setattr('logging.Handler.setLevel', lambda *args, **kwargs: None)


@pytest.fixture(scope='function')
def no_warn(log_output):
    yield log_output
    for record in log_output.entries:
        if (
            record['log_level'] in {'error', 'critical', 'warning'}
            and record['event'] != 'Failed to decode simple token'
        ):
            pytest.fail(f'no warning expected, but got {record}')


@pytest.fixture(scope='function')
def with_error(log_output):
    yield log_output
    count = 0
    for record in log_output.entries:
        if record['log_level'] in ['error', 'critical']:
            count += 1

    assert count > 0


@pytest.fixture(scope='function')
def with_warn(log_output):
    yield log_output
    count = 0
    for record in log_output.entries:
        if record['log_level'] in ['warning']:
            count += 1

    assert count > 0


@pytest.fixture(scope='function')
def plugin_schema():
    """Fixture for loading a schema plugin into the config."""
    from nomad.metainfo.elasticsearch_extension import entry_type  # noqa
    from nomad.metainfo import Package  # noqa

    plugin_path = str(Path(__file__, '../data/schemas').resolve())
    plugin_name = 'nomadschemaexample'

    if plugin_path not in sys.path:
        sys.path.insert(0, plugin_path)

    package = importlib.import_module(plugin_name)
    plugin = package.nomadexample_schema

    # Add plugin to config
    if (
        config.plugins is not None
        and config.plugins.entry_points is not None
        and config.plugins.entry_points.options is not None
    ):
        config.plugins.entry_points.options[plugin_name] = plugin

    # Add plugin to Package registry
    package = plugin.load()
    package.__init_metainfo__()  # type: ignore

    # Reload the dynamic quantities so that API is aware of the plugin
    # quantities.
    entry_type.reload_quantities_dynamic()

    yield

    try:
        sys.path.remove(plugin_path)
    except Exception:
        pass

    # Remove package as plugin
    if (
        config.plugins is not None
        and config.plugins.entry_points is not None
        and config.plugins.entry_points.options is not None
    ):
        del config.plugins.entry_points.options[plugin_name]

    # Remove plugin from Package registry
    for key, i_package in Package.registry.items():
        if i_package is package:
            del Package.registry[key]
            break

    # Reload the dynamic quantities so that API is aware of the plugin
    # quantities.
    entry_type.reload_quantities_dynamic()


@pytest.fixture
def reset_config():
    """Fixture that resets configuration."""
    service = config.meta.service
    yield None
    config.meta.service = service
    utils.set_console_log_level(test_log_level)


@pytest.fixture(scope='module')
def api_v1(monkeysession, user_molds):
    """
    This fixture provides an HTTP client with Python requests interface that accesses
    the fast api. The have to provide URLs that start with out leading '/' after '.../api/v1.
    This fixture also patches the actual requests. If some code is using requests to
    connect to the NOMAD v1 at ``nomad.config.client.url``, the patch will redirect to the
    fast api under test.
    """

    test_client = TestClient(app, base_url='http://testserver/api/v1/')

    def call_test_client(method, url, *args, **kwargs):
        url = url.replace(f'{config.client.url}/v1/', '')
        url = url.replace('/api/v1/', '')
        return getattr(test_client, method)(url, *args, **kwargs)

    monkeysession.setattr(
        'requests.get', lambda *args, **kwargs: call_test_client('get', *args, **kwargs)
    )
    monkeysession.setattr(
        'requests.put', lambda *args, **kwargs: call_test_client('put', *args, **kwargs)
    )
    monkeysession.setattr(
        'requests.post',
        lambda *args, **kwargs: call_test_client('post', *args, **kwargs),
    )
    monkeysession.setattr(
        'requests.delete',
        lambda *args, **kwargs: call_test_client('delete', *args, **kwargs),
    )

    def __call__(self, request):
        for user in user_molds.values():
            if user['username'] == self.user or user['email'] == self.user:
                request.headers['Authorization'] = f'Bearer {user["user_id"]}'
        return request

    monkeysession.setattr('nomad.client.api.Auth.__call__', __call__)

    return test_client


@pytest_asyncio.fixture(scope='function')
async def async_api_v1(monkeypatch, user_molds):
    """
    This fixture provides an HTTP client with AsyncClient that accesses
    the fast api. The patch will redirect all requests to the fast api under test.
    """
    transport = ASGITransport(app=app)
    test_client = AsyncClient(transport=transport, base_url='http://testserver/api/v1/')

    monkeypatch.setattr(
        'nomad.client.archive.ArchiveQuery._fetch_url',
        'http://testserver/api/v1/entries/query',
    )
    monkeypatch.setattr(
        'nomad.client.archive.ArchiveQuery._download_url',
        'http://testserver/api/v1/entries/archive/query',
    )

    monkeypatch.setattr('httpx2.AsyncClient.get', getattr(test_client, 'get'))
    monkeypatch.setattr('httpx2.AsyncClient.put', getattr(test_client, 'put'))
    monkeypatch.setattr('httpx2.AsyncClient.post', getattr(test_client, 'post'))
    monkeypatch.setattr('httpx2.AsyncClient.delete', getattr(test_client, 'delete'))

    def mocked_auth_headers(self) -> dict:
        for user in user_molds.values():
            if user['username'] == self.user or user['email'] == self.user:
                return dict(Authorization=f'Bearer {user["user_id"]}')
        return {}

    monkeypatch.setattr('nomad.client.api.Auth.headers', mocked_auth_headers)

    try:
        yield test_client
    finally:
        await test_client.aclose()


@pytest.fixture(scope='module')
def client_with_api_v1(api_v1, monkeysession):
    def call_requests(method, path, *args, **kwargs):
        return getattr(api_v1, method)(path, *args, **kwargs)

    monkeysession.setattr('nomad.client.api._call_requests', call_requests)


@pytest.fixture(scope='function')
def central_logstash_mock(monkeypatch):
    monkeypatch.setattr('nomad.config.config.logstash.enabled', True)

    class TCPServerStore(socketserver.TCPServer):
        received_content = []

        def set_request_timeout(self, timeout):
            # Note acts timeout is on the LogstashCentralHandler socket
            # this seems to behave differently to self.timeout and is
            # particularly useful to interrupt blocking "handle_request()"
            self.RequestHandlerClass.timeout = timeout

    class LogstashCentralHandler(socketserver.StreamRequestHandler):
        def handle(self):
            while True:
                try:
                    line = self.rfile.readline()
                    # print(f'received {line=}')
                except TimeoutError:
                    # print(f'server timed out')
                    line = b''  # if time out, close connection

                if line == b'':
                    # print(f'received closing for LogstashCentralHandler')
                    break

                line = line.strip()
                if len(line) > 0:
                    self.server.received_content.append(line)

    host, port = config.logstash.host, int(config.logstash.tcp_port)

    # print(f"set up mock on host={host} and port={port}")
    logstash_mock_central = TCPServerStore(
        (host, port), LogstashCentralHandler, bind_and_activate=False
    )
    logstash_mock_central.allow_reuse_address = True
    logstash_mock_central.allow_reuse_port = True
    # logstash_mock_central.timeout = 0.3

    # It is in the responsibility of the test to set a time out of the test
    while True:
        try:
            logstash_mock_central.server_bind()
            logstash_mock_central.server_activate()
        except Exception:
            time.sleep(0.001)  # try again
        else:
            break

    yield logstash_mock_central

    # make sure to close the server
    logstash_mock_central.server_close()


class MockFileManager:
    def __init__(self):
        self.files = {}
        self._open = builtins.open

    def open(self, name, mode='r', buffering=-1, **options):
        name = os.path.abspath(name)
        if mode.startswith('r') and name not in self.files:
            # We have to let some files through
            return self._open(name, mode, buffering, **options)
            # This causes stracktraces not to display
            # raise IOError(2, "No such file or directory: '%s'" % name)

        if mode.startswith('w') or (mode.startswith('a') and name not in self.files):
            buf = StringIO()
            buf.close = lambda: None
            self.files[name] = buf

        buf = self.files[name]

        if mode.startswith('r'):
            buf.seek(0)
        elif mode.startswith('a'):
            buf.seek(0)

        return buf

    def write(self, name, text):
        name = os.path.abspath(name)
        buf = StringIO(text)
        buf.close = lambda: None
        self.files[name] = buf

    def read(self, name):
        name = os.path.abspath(name)
        if name not in self.files:
            raise OSError(2, f"No such file or directory: '{name}'")

        return self.files[name].getvalue()


@pytest.fixture
def mockopen(monkeypatch):
    manager = MockFileManager()
    monkeypatch.setattr(builtins, 'open', manager.open)
    return manager


@pytest.fixture(scope='session', autouse=True)
def nomad_parsers(monkeysession):
    from nomad.parsing.artificial import (
        ChaosParser,
        GenerateRandomParser,
        TemplateParser,
    )
    from nomad.parsing.parsers import parser_dict, parsers

    test_parsers = [GenerateRandomParser(), TemplateParser(), ChaosParser()]
    parser_dict.update({parser.name: parser for parser in test_parsers})
    parsers.extend(test_parsers)
    monkeysession.setattr('nomad.parsing.parsers.parsers', parsers)
    monkeysession.setattr('nomad.parsing.parsers.parser_dict', parser_dict)
