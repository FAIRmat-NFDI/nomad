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

import logging
import os

import pytest
import structlog

from nomad.actions import action_logging
from nomad.actions.action_logging import (
    WorkflowRoutingHandler,
    _WorkflowLoggingInboundInterceptor,
)
from nomad.actions.manager import action_log_file_path
from nomad.config import config


def test_interceptor_and_routing_handler(tmp_path, monkeypatch):
    """
    Test the automatic routing via contextvars and standard logging handler.
    """
    monkeypatch.setattr(config.fs, 'actions', str(tmp_path))

    root_logger = logging.getLogger()

    # We clear out existing handlers for the test to avoid duplicate outputs
    # and we add our interceptor handler
    old_handlers = root_logger.handlers[:]
    root_logger.handlers = []

    handler = WorkflowRoutingHandler()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    try:
        # Simulate activity context variable
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(workflow_id='test-routed-workflow')

        # Now emit a generic log from root logger
        root_logger.info('This should magically route!')

        # Test another workflow
        structlog.contextvars.bind_contextvars(workflow_id='another-workflow')
        root_logger.error('Something went wrong over here')

        file1 = action_log_file_path('test-routed-workflow')
        file2 = action_log_file_path('another-workflow')

        assert os.path.exists(file1)
        assert os.path.exists(file2)

        with open(file1) as f:
            content1 = f.read()
            assert 'This should magically route!' in content1
            assert 'Something went wrong' not in content1

        with open(file2) as f:
            content2 = f.read()
            assert 'Something went wrong over here' in content2
            assert 'magically route' not in content2
    finally:
        # Restore old state
        root_logger.handlers = old_handlers
        structlog.contextvars.clear_contextvars()


def test_routing_handler_prefers_action_instance_id(tmp_path, monkeypatch):
    monkeypatch.setattr(config.fs, 'actions', str(tmp_path))

    root_logger = logging.getLogger()
    old_handlers = root_logger.handlers[:]
    root_logger.handlers = []
    handler = WorkflowRoutingHandler()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    try:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            workflow_id='child-workflow-id',
            action_instance_id='root-action-id',
        )
        root_logger.info('root-routed message')

        root_file = action_log_file_path('root-action-id')
        child_file = os.path.join(
            str(tmp_path),
            'child-workflow-id',
            'nomad_system',
            'logs',
            'child-workflow-id.log',
        )

        assert os.path.exists(root_file)
        assert not os.path.exists(child_file)
        with open(root_file) as f:
            assert 'root-routed message' in f.read()
    finally:
        root_logger.handlers = old_handlers
        structlog.contextvars.clear_contextvars()


def test_log_path_does_not_fall_back_to_legacy_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(config.fs, 'actions', str(tmp_path))
    legacy = tmp_path / 'logs' / 'instance.log'
    legacy.parent.mkdir()
    legacy.write_text('old log')

    assert action_log_file_path('instance') == str(
        tmp_path / 'instance' / 'nomad_system' / 'logs' / 'instance.log'
    )
    assert legacy.read_text() == 'old log'


@pytest.mark.asyncio
async def test_interceptor_binds_root_action_context(monkeypatch):
    class _DummyNext:
        async def execute_activity(self, input):
            del input
            return structlog.contextvars.get_contextvars()

    class _Info:
        workflow_id = 'child-workflow-id'

    async def _resolve_root(workflow_id: str) -> str:
        assert workflow_id == 'child-workflow-id'
        return 'root-action-id'

    monkeypatch.setattr(action_logging, '_resolve_root_workflow_id', _resolve_root)
    monkeypatch.setattr(action_logging.activity, 'info', _Info)

    structlog.contextvars.clear_contextvars()
    interceptor = _WorkflowLoggingInboundInterceptor(_DummyNext())
    result = await interceptor.execute_activity(None)

    assert result['workflow_id'] == 'child-workflow-id'
    assert result['action_instance_id'] == 'root-action-id'
