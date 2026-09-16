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
import time
from collections import defaultdict
from datetime import datetime, timezone
from multiprocessing import current_process
from typing import Any, NamedTuple

from mongoengine import Document, IntField, ListField, StringField, ValidationError
from mongoengine.connection import ConnectionFailure

import nomad.patch  # noqa: F401
from nomad import utils
from nomad.config import config
from nomad.models.common import ProcessStatus
from nomad.mongo.fields import UTCDateTimeField


class InvalidId(Exception):
    pass


class ProcNotRegistered(Exception):
    pass


class ProcessAlreadyRunning(Exception):
    pass


class ProcessSyncFailure(Exception):
    pass


class ProcObjectDoesNotExist(Exception):
    pass


class ProcessFailure(Exception):
    """
    Special exception class which allows the user to control how :func:`Proc.fail` should
    be called when the exception is caught from the process function.
    """

    def __init__(self, *errors, log_level=logging.ERROR, **kwargs):
        self._errors = errors
        self._log_level = log_level
        self._kwargs = kwargs


class ProcessFlags(NamedTuple):
    """Flags defined by the @process and @process_local decorators."""

    is_blocking: bool
    clear_queue_on_failure: bool
    is_child: bool
    is_local: bool


class Proc(Document):
    """
    Base class for objects that are subject to processing and need persistent processing
    state. The processing state is persisted in mongo db. Possible processing statuses are
    defined by :class:`ProcessStatus`.

    To initiate a process, an object subclassing Proc must first be created using
    :func:`create`. Processes are then initiated by calling a *process function* on this
    object, which is a member function marked with the decorator @process. Calling a process
    function sets the process_state to PENDING and a celery task is created, which will be
    picked up by a worker, which sets the state to RUNNING and actually executes the
    process function.

    From process_status RUNNING, the process can transition to either SUCCESS, FAILURE or
    WAITING_FOR_RESULT, or result in the deletion of the process object itself (for example
    the process for deleting an upload).

    WAITING_FOR_RESULT means the process needs to wait for the result from spawned child
    processes. When a child process finishes, it will call `try_to_join` on the parent, and
    if it is the parent's last running child process, we will switch to the parent Proc,
    put its status to RUNNING and execute the `join` method on the parent.

    To send the process to status WAITING_FOR_RESULT, the process function must return
    normally, without errors and exceptions, and return the value `ProcessStatus.WAITING_FOR_RESULT`.

    If the process deletes the object itself, the process function should instead return
    `ProcessStatus.DELETED`. If the process function returns normally, without exception
    and without a return value, the process status will be set to SUCCESS.


    Attributes:
        errors: a list of errors that happened during processing.
            NOTE: This value is managed by the framework, do not tamper with this value.
            To fail a process, an exception should be raised.
        warnings: a list of warnings that happened during processing. Warnings do not
            fail a processing run
        last_status_message: A short, human-readable message from the current process, with
            information about what the current process is doing, or information about the
            completion (successful or not) of the last process, if no process is currently
            running.
        complete_time: the time that processing completed (successfully or not).
            NOTE: This value is managed by the framework, do not tamper with this value.
        current_process: the currently or last run asynchronous process
            NOTE: This value is managed by the framework, do not tamper with this value.
        process_status: one of the values defined by :class:`ProcessStatus`.
            NOTE: This value is managed by the framework, do not tamper with this value.
        queue: A list defining queued up calls, waiting to be run. Each item is a triple
            of [func_name, args, kwargs].
            NOTE: This value is managed by the framework, do not tamper with this value.
        sync_counter: An integer, incremented every time a "sync" operation is executed,
            to ensure state consistency and atomicity. There are three types of sync operations:
            when scheduling a process, starting a process, and completing a process.
            NOTE: This value is managed by the framework, do not tamper with this value.
    """

    id_field: str | None = None
    meta: Any = {
        'abstract': True,
    }

    complete_time = UTCDateTimeField()

    errors = ListField(StringField())
    warnings = ListField(StringField())
    last_status_message: str | None = StringField(default=None)

    current_process = StringField(default=None)
    process_status = StringField(default=ProcessStatus.READY)

    worker_hostname = StringField(default=None)
    celery_task_id = StringField(default=None)

    queue = ListField()
    sync_counter = IntField(default=0)

    @property
    def process_running(self) -> bool:
        """
        Returns True of an asynchronous process is currently running (or waiting to run).
        NOTE, the return value will reflect the state when the object was last updated from
        mongo, not necessarily the current state.
        """
        return self.process_status in ProcessStatus.STATUSES_PROCESSING

    @property
    def current_process_flags(self) -> ProcessFlags:
        if not self.current_process:
            return None
        flags = process_flags[self.__class__.__name__]
        curr_process = str(self.current_process)
        return flags.get(curr_process, None) or flags.get(f'_{curr_process}', None)

    @property
    def queue_blocked(self) -> bool:
        """
        If the queue is blocked (i.e. no new @process can be invoked on this object).
        NOTE, the return value will reflect the state when the object was last updated from
        mongo, not necessarily the current state.
        """
        if self.process_status in ProcessStatus.STATUSES_PROCESSING:
            # Check current process
            if self.current_process_flags.is_blocking:
                return True
            # Check queued processes
            for item in self.queue:
                func_name = item[0]
                if process_flags[self.__class__.__name__][func_name].is_blocking:
                    return True
        return False

    def get_logger(self):
        process = current_process()
        worker_id = getattr(process, '_nomad_id', None)
        if worker_id is None:
            worker_id = utils.create_uuid()
            setattr(process, '_nomad_id', worker_id)

        return utils.get_logger(
            'nomad.processing',
            proc=self.__class__.__name__,
            process=self.current_process,
            process_status=self.process_status,
            process_worker_id=worker_id,
        )

    @classmethod
    def create(cls, **kwargs):
        """Factory method that must be used instead of regular constructor."""
        assert (
            kwargs.get('process_status', ProcessStatus.PENDING) == ProcessStatus.PENDING
        ), 'do not set the status manually, its managed'

        self = cls(**kwargs)
        self.process_status = ProcessStatus.READY
        self.save()

        return self

    def reset(
        self,
        force: bool = False,
        worker_hostname: str | None = None,
        process_status: str = ProcessStatus.READY,
        errors: list[str] = None,
        clear_queue: bool = True,
    ):
        """
        Resets the process status. If force is not set, there must be no currently running process.
        NOTE, use this with care! This should normally only be used manually, to fix processes
        that are "stuck" in status processing, for example if the worker has died etc.
        """
        assert not self.process_running or force

        self.current_process = None
        self.process_status = process_status
        self.errors = errors or []
        self.warnings = []
        self.worker_hostname = worker_hostname
        if clear_queue:
            self.queue = []

    @classmethod
    def reset_pymongo_update(
        cls,
        worker_hostname: str | None = None,
        process_status=ProcessStatus.READY,
        errors: list[str] = None,
        clear_queue: bool = True,
    ):
        """
        Returns a pymongo update dict part to reset a Proc.
        NOTE, use this with care! This should normally only be used manually, to fix processes
        that are "stuck" in status processing, for example if the worker has died etc.
        """
        rv = dict(
            current_process=None,
            process_status=process_status,
            errors=errors or [],
            warnings=[],
            worker_hostname=worker_hostname,
        )
        if clear_queue:
            rv['queue'] = []
        return rv

    @classmethod
    def get_by_id(cls, id: str, id_field: str):
        try:
            obj = cls.objects(**{id_field: id}).first()
        except ValidationError:
            raise InvalidId(f'{id} is not a valid id')
        except ConnectionFailure as e:
            raise e

        if obj is None:
            raise KeyError(f'{cls.__name__} with id {id} does not exist')

        return obj

    @classmethod
    def get(cls, obj_id):
        return cls.get_by_id(str(obj_id), 'id')

    @staticmethod
    def log(logger, log_level, msg, **kwargs):
        # TODO there seems to be a bug in structlog, cannot use logger.log
        if log_level == logging.ERROR:
            logger.error(msg, **kwargs)
        elif log_level == logging.WARNING:
            logger.warning(msg, **kwargs)
        elif log_level == logging.INFO:
            logger.info(msg, **kwargs)
        elif log_level == logging.DEBUG:
            logger.debug(msg, **kwargs)
        else:
            logger.critical(msg, **kwargs)

    def fail(self, *errors, log_level=logging.ERROR, complete=True, **kwargs):
        """
        Used to handle when a process fails. Takes strings or exceptions as args.
        The method logs the error(s), updates `self.errors`, `self.last_status_message`,
        `self.process_status`, and calls :func:`on_fail`, and if `complete` == True (default)
        it also saves the object to mongodb. The positional args define the errors. An
        error should normally be an instance of Exception, if not it will be converted to
        a string.

        NOTE, processes should NOT call this method directly, or tamper with self.errors etc.
        Rather, if something goes wrong in a process, it should raise an exception!
        """
        failed_with_exception = False

        # Log the error
        logger = self.get_logger(**kwargs)
        self.errors = []
        for error in errors:
            if isinstance(error, Exception):
                failed_with_exception = True
                self.errors.append(f'{error.__class__.__name__}: {str(error)}')
                Proc.log(
                    logger,
                    log_level,
                    'process failed with exception',
                    exc_info=error,
                    error=str(error),
                )
            else:
                self.errors.append(str(error))

        if not failed_with_exception:
            errors_str = '; '.join([str(error) for error in errors])
            Proc.log(logger, log_level, 'process failed', errors=errors_str)

        self.complete_time = datetime.now(timezone.utc)

        try:
            self.on_fail()
        except Exception as e:
            # Oh my, nothing is going our way today
            Proc.log(logger, logging.ERROR, 'on_fail failed', exc_info=e, error=str(e))

        logger.info('process failed')
        if len(self.errors) > 0:
            self.last_status_message = (
                f'Process {self.current_process} failed: {self.errors[-1]}'
            )

        self.process_status = ProcessStatus.FAILURE
        if complete:
            self._sync_complete_process(force_clear_queue_on_failure=True)

    def warning(self, *warnings, log_level=logging.WARNING, **kwargs):
        """Allows to save warnings. Takes strings or exceptions as args."""
        logger = self.get_logger(**kwargs)

        for warning in warnings:
            warning = str(warning)
            self.warnings.append(warning)
            Proc.log(logger, log_level, 'task with warning', warning=warning)

    def set_last_status_message(self, last_status_message: str):
        """Sets the `last_status_message` and saves."""
        self.last_status_message = last_status_message
        self.save()

    def on_success(self):
        """To be called whenever a process is about to transition to status SUCCESS."""
        pass

    def on_fail(self):
        """To be called whenever a process is about to transition to status FAILURE."""
        pass

    def on_waiting_for_result(self):
        """To be called whenever a process is about to transition to status WAITING_FOR_RESULT."""
        pass

    def block_until_complete(self, interval=0.01):
        """
        Reloads the process constantly until it sees a completed process (FAILURE or SUCCESS).
        Should be used with care as it can block indefinitely. Just intended for testing
        purposes.
        """
        while self.process_running:
            time.sleep(interval)
            self.reload()

    def block_until_complete_or_waiting_for_result(self, interval=0.01):
        """
        Reloads the process constantly until the process is either complete or in status WAITING_FOR_RESULT.
        Should be used with care as it can block indefinitely. Just intended for testing
        purposes.
        """
        while self.process_status in (ProcessStatus.PENDING, ProcessStatus.RUNNING):
            time.sleep(interval)
            self.reload()

    def __str__(self):
        return f'proc celery_task_id={self.celery_task_id} worker_hostname={self.worker_hostname}'

    def parent(self) -> 'Proc':
        """
        When running a process marked with `is_child`, and the process completes (i.e. succeeds or
        fails), this method will be invoked by the framework to determine the object's parent Proc.
        """
        raise NotImplementedError('`parent` not implemented')

    def child_cls(self) -> 'Proc':
        """
        When running a process which spawns child processes and transitions to WAITING_FOR_RESULT,
        this method will be invoked to determine the "child" class when it is time to try to
        join.
        """
        raise NotImplementedError('`child_cls` not implemented')

    def join(self):
        """
        Override, if applicable, to define what to do when joined, i.e. when the process is
        resumed after all child processes are done. Should return either None (if successful)
        or `ProcessStatus.WAITING_FOR_RESULT` if it again wants to wait for child processes.
        """
        raise NotImplementedError('`join` not implemented')

    def _sync_start_local_process(self, func_name: str):
        """
        Used to start a *local* process. If successful, the status transitions to RUNNING
        atomically. The call will fail and raise a :class:`ProcessAlreadyRunning` if any
        other process is currently running.

        This is one of three *sync operations*. See :func:`_sync_schedule_process`
        for more info.
        """
        try_counter = 0
        while True:
            if self.process_running:
                raise ProcessAlreadyRunning(
                    'Another process is running or waiting to run'
                )
            mongo_update = {
                '$set': dict(
                    sync_counter=self.sync_counter + 1,
                    process_status=ProcessStatus.RUNNING,
                    current_process=func_name,
                    last_status_message='Started: ' + func_name,
                    worker_hostname=None,
                    celery_task_id=None,
                    errors=[],
                    warnings=[],
                )
            }
            # Try to update self atomically. Will fail if someone else has managed to write
            # a sync op in between.
            old_record = self._get_collection().find_one_and_update(
                {
                    '$and': [
                        {'_id': self.id},
                        {
                            '$or': [
                                {'sync_counter': self.sync_counter},
                                {'sync_counter': {'$exists': False}},
                            ]
                        },
                    ]
                },
                mongo_update,
            )
            try_counter += 1
            if old_record and old_record.get('sync_counter') == self.sync_counter:
                # We have successfully started the process!
                self.reload()
                return
            # Someone else must have written a sync op (ticked up the sync_counter) in between
            if try_counter >= 3:
                # Three failed attempts - should be virtually impossible!
                raise ProcessSyncFailure(
                    'Failed to start local process too many times - should not happen'
                )
            # Otherwise, sleep, reload, and try again
            time.sleep(0.1)
            self.reload()

    def _sync_complete_process(
        self, force_clear_queue_on_failure=False
    ) -> tuple[str, list[Any], dict[str, Any]]:
        """
        Used to complete a process (when done, successful or not). Returns a triple
        containing information about the next process to run (if any), of the
        form (func_name, args, kwargs).

        There are 3 possibilities:
            1)  There is something in the queue, and the current process was successful
                -> We set status to PENDING and return the next process.
            2)  There is something in the queue, and the current process FAILED:
                -> Behaviour depends on the process decorator flag `clear_queue_on_failure`
                   and the parameter `force_clear_queue_on_failure`:
                        If either is True: We clear the queue, set status to FAILURE and return None.
                        Otherwise: We set status to PENDING and return the next process.
            3)  There is nothing in the queue:
                -> We set the status to the provided value and return None

        This is one of three *sync operations*. See :func:`_sync_schedule_process`
        for more info.
        """
        assert self.process_status in ProcessStatus.STATUSES_COMPLETED
        # As a safety precaution, save all updates made to the object except the status
        # (We want it to have status RUNNING until the atomic read/write finishes)
        process_status = self.process_status
        self.process_status = ProcessStatus.RUNNING
        self.save()
        self.process_status = process_status
        clear_queue_on_failure = force_clear_queue_on_failure or (
            self.current_process_flags
            and self.current_process_flags.clear_queue_on_failure
        )
        try_counter = 0
        while True:
            next_process = None
            mongo_update = {'$set': {'sync_counter': self.sync_counter + 1}}
            if self.queue:
                # Something in the queue
                if (
                    not clear_queue_on_failure
                    or process_status == ProcessStatus.SUCCESS
                ):
                    # Move on to the next process
                    next_process = self.queue[0]
                    next_func_name = next_process[0]
                    mongo_update['$pop'] = {'queue': -1}  # pops the first element
                    mongo_update['$set'].update(
                        process_status=ProcessStatus.PENDING,
                        last_status_message='Pending: ' + next_func_name,
                        current_process=next_func_name,
                    )
                else:
                    # Failed and clear_queue_on_failure is set to True - clear the queue
                    mongo_update['$set'].update(process_status=process_status, queue=[])
            else:
                mongo_update['$set'].update(process_status=process_status)
            # Try to update self atomically. Will fail if someone else has managed to write
            # a sync op in between.
            old_record = self._get_collection().find_one_and_update(
                {'_id': self.id, 'sync_counter': self.sync_counter}, mongo_update
            )
            try_counter += 1
            if old_record and old_record.get('sync_counter') == self.sync_counter:
                # We have successfully completed the process
                return next_process
            # Someone else must have written a sync op (ticked up the sync_counter) in between
            if try_counter >= 3:
                # Three failed attempts - should be virtually impossible!
                raise ProcessSyncFailure(
                    'Failed to complete process too many times - should not happen'
                )
            # Make another attempt
            time.sleep(0.1)
            self.reload()


process_flags: dict[str, dict[str, ProcessFlags]] = defaultdict(dict)
""" { <Proc class name>: { <process func name>: ProcessFlags } } """


def process_local(func):
    """
    The decorator for functions that process locally. These work similarly to functions
    marked with the `@process` decorator, but they are executed directly, in the current
    thread, not via celery. Consequently, they can only be started if no other process is
    running. They are also implicitly blocking, i.e. while running, no other process
    (local or celery-based) can be started or scheduled on the same object. It can also not
    spawn child processes and wait for them using the WAITING_FOR_RESULT mechanism, or itself
    be a child process (as this means joining with a parent process when done).

    If successful, a local process can return a value to the caller (unlike celery processes).
    If unsuccessful, an exception will be raised (note that the usual process handling is
    always applied, i.e. we set self.process_status, self.errors etc. accordingly). The Proc
    object should not have any unsaved changes when a local process is invoked.
    """
    # Determine canonical class name
    cls_name, func_name = func.__qualname__.split('.')

    process_flags[cls_name][func_name] = ProcessFlags(
        is_blocking=True,
        clear_queue_on_failure=False,  # Not relevant, since local processes are always blocking
        is_child=False,
        is_local=True,
    )

    def wrapper(self: Proc, *args, **kwargs):
        logger = self.get_logger()
        logger.debug('Executing local process')
        self._sync_start_local_process(func_name)

        try:
            os.chdir(config.fs.working_directory)
            with utils.timer(logger, 'process executed locally', log_memory=True):
                # Actually call the process function
                rv = func(self, *args, **kwargs)
                if self.errors:
                    # Should be impossible unless the process has tampered with self.errors, which
                    # it should not do. We will treat it essentially as if it had raised an exception
                    raise RuntimeError(
                        'completed with errors but no exception, should not happen'
                    )
                # All looks good
                self.on_success()
                self.process_status = ProcessStatus.SUCCESS
                self.complete_time = datetime.now(timezone.utc)
                self.save()
                if self.warnings:
                    self.last_status_message = (
                        f'Process {func_name} completed with warnings'
                    )
                else:
                    self.last_status_message = (
                        f'Process {func_name} completed successfully'
                    )
                logger.info('completed process')
                return rv
        except SystemExit as e:
            self.fail(e, complete=False)
            raise
        except ProcessFailure as e:
            # Exception with details about how to call self.fail
            self.fail(*e._errors, log_level=e._log_level, complete=False, **e._kwargs)
            raise
        except Exception as e:
            self.fail(e, complete=False)
            raise
        finally:
            self._sync_complete_process()  # Queue should be empty, so nothing more to do

    return wrapper
