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
.. autofunc::nomad.utils.create_uuid
.. autofunc::nomad.utils.hash
.. autofunc::nomad.utils.timer

Logging in nomad is structured. Structured logging means that log entries contain
dictionaries with quantities related to respective events. E.g. having the code,
parser, parser version, entry_id, mainfile, etc. for all events that happen during
entry processing. This means the :func:`get_logger` and all logger functions
take keyword arguments for structured data. Otherwise :func:`get_logger` can
be used similar to the standard *logging.getLogger*.

Depending on the configuration all logs will also be send to a central logstash.

.. autofunc::nomad.utils.get_logger
.. autofunc::nomad.utils.hash
.. autofunc::nomad.utils.create_uuid
.. autofunc::nomad.utils.timer
.. autofunc::nomad.utils.lnr
.. autofunc::nomad.utils.strip
"""

from typing import Any, overload
from collections.abc import Iterable
from collections import OrderedDict
import fnmatch
from functools import reduce
from itertools import takewhile
import base64
from contextlib import contextmanager
import json
import uuid
import time
import hashlib
import sys
from datetime import datetime, timedelta, timezone
import collections
import logging
import inspect
from importlib.metadata import PackageNotFoundError, metadata, version

import orjson
import os
import unicodedata
import re

from nomad.config import config


def dump_json(data):
    def default(data):
        if isinstance(data, collections.OrderedDict):
            return dict(data)

        if data.__class__.__name__ == 'BaseList':
            return list(data)

        raise TypeError

    return orjson.dumps(
        data, default=default, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS
    )


@overload
def normalize_datetime_utc(value: None) -> None: ...


@overload
def normalize_datetime_utc(value: datetime) -> datetime: ...


def normalize_datetime_utc(value: datetime | None) -> datetime | None:
    """
    Normalize a datetime value to UTC.

    - naive datetime -> assume UTC
    - aware datetime -> convert to UTC
    - None -> None
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    return value


# Length of hashes and hash-based ids (e.g. entry_id) in nomad.
default_hash_len: int = 28

try:
    from . import structlogging
    from .structlogging import configure_logging

    def get_logger(name, **kwargs):
        """
        Returns a structlog logger that is already attached with a logstash handler.
        Use additional *kwargs* to pre-bind some values to all events.
        """
        return structlogging.get_logger(name, **kwargs)

except ImportError:

    def get_logger(name, **kwargs):
        return ClassicLogger(name, **kwargs)

    def configure_logging(console_log_level=config.services.console_log_level):
        import logging

        logging.basicConfig(level=console_log_level)


class ClassicLogger:
    """
    A logger class that emulates the structlog interface, but uses the classical
    build-in Python logging.
    """

    def __init__(self, name, **kwargs):
        self.kwargs = kwargs
        self.logger = logging.getLogger(name)

    def bind(self, **kwargs):
        all_kwargs = dict(self.kwargs)
        all_kwargs.update(**kwargs)
        return ClassicLogger(self.logger.name, **all_kwargs)

    def __log(self, method_name, event, **kwargs):
        method = getattr(self.logger, method_name)
        all_kwargs = dict(self.kwargs)
        all_kwargs.update(**kwargs)

        message = '{} ({})'.format(
            event,
            ', '.join(
                [f'{str(key)}={str(value)}' for key, value in all_kwargs.items()]
            ),
        )
        method(message)

    def __getattr__(self, key):
        return lambda *args, **kwargs: self.__log(key, *args, **kwargs)


def set_console_log_level(level):
    root = logging.getLogger()
    try:
        from .structlogging import LogstashHandler
    except ImportError:
        for handler in root.handlers:
            handler.setLevel(level)
    else:
        for handler in root.handlers:
            if not isinstance(
                handler,
                LogstashHandler,
            ):
                handler.setLevel(level)


def decode_handle_id(handle_str: str):
    result = 0
    for c in handle_str:
        ordinal = ord(c.lower())
        if 48 <= ordinal <= 57:
            number = ordinal - 48
        elif 97 <= ordinal <= 118:
            number = ordinal - 87
        else:
            raise ValueError()

        result = result * 32 + number

    return result


def generate_entry_id(
    upload_id: str, mainfile: str, mainfile_key: str | None = None
) -> str:
    """
    Generates an id for an entry.
    Arguments:
        upload_id: The id of the upload
        mainfile: The mainfile path (relative to the raw directory).
        mainfile_key: Optional additional key for mainfiles that represent many entries.
    Returns:
        The generated entry id
    """
    if mainfile_key:
        return hash(upload_id, mainfile, mainfile_key)
    return hash(upload_id, mainfile)


def hash(*args, length: int = default_hash_len) -> str:
    """Creates a websafe hash of the given length based on the repr of the given arguments."""
    hash = hashlib.sha512()
    for arg in args:
        hash.update(str(arg).encode('utf-8'))

    return make_websave(hash, length=length)


def make_websave(hash, length: int = default_hash_len) -> str:
    """Creates a websafe string for a hashlib hash object."""
    if length > 0:
        return base64.b64encode(hash.digest(), altchars=b'-_')[:length].decode('utf-8')
    else:
        return base64.b64encode(hash.digest(), altchars=b'-_')[0:-2].decode('utf-8')


def base64_encode(string):
    """
    Removes any `=` used as padding from the encoded string.
    """
    encoded = base64.urlsafe_b64encode(string).decode('utf-8')
    return encoded.rstrip('=')


def base64_decode(string):
    """
    Adds back in the required padding before decoding.
    """
    padding = 4 - (len(string) % 4)
    bytes = (string + ('=' * padding)).encode('utf-8')
    return base64.urlsafe_b64decode(bytes)


def create_uuid() -> str:
    """Returns a web-save base64 encoded random uuid (type 4)."""
    return base64.b64encode(uuid.uuid4().bytes, altchars=b'-_').decode('utf-8')[0:-2]


def adjust_uuid_size(uuid, length: int = default_hash_len):
    """Adds prefixing spaces to a uuid to ensure the default uuid length."""
    uuid = uuid.rjust(length, ' ')
    assert len(uuid) == length, 'uuids must have the right fixed size'
    return uuid


@contextmanager
def lnr(logger, event, **kwargs):
    """
    A context manager that Logs aNd Raises all exceptions with the given logger.

    Arguments:
        logger: The logger that should be used for logging exceptions.
        event: the log message
        **kwargs: additional properties for the structured log
    """
    try:
        yield

    except Exception as e:
        # ignore HTTPException as they are part of the normal app error handling
        if e.__class__.__name__ != 'HTTPException':
            logger.error(event, exc_info=e, **kwargs)
        raise e


@contextmanager
def timer(
    logger,
    event,
    method='info',
    lnr_event: str | None = None,
    log_memory: bool = False,
    **kwargs,
):
    """
    A context manager that takes execution time and produces a log entry with said time.

    Arguments:
        logger: The logger that should be used to produce the log entry.
        event: The log message/event.
        method: The log method that should be used. Must be a valid logger method name.
            Default is 'info'.
        lnr_event: The log message in the case of error.
        log_memory: Log process memory usage before and after.
        **kwargs: Additional logger data that is passed to the log entry.

    Returns:
        The method yields a dictionary that can be used to add further log data.
    """

    def get_rss():
        if os.name != 'nt':
            import resource

            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return 0

    kwargs = dict(kwargs)
    start = time.time()
    if log_memory:
        rss_before = get_rss()
        kwargs['pid'] = os.getpid()
        kwargs['exec_rss_before'] = rss_before

    try:
        yield kwargs
    except Exception as e:
        if lnr_event is not None:
            stop = time.time()
            if log_memory:
                rss_after = get_rss()
                kwargs['exec_rss_after'] = rss_after
                kwargs['exec_rss_delta'] = rss_before - rss_after
            logger.error(lnr_event, exc_info=e, exec_time=stop - start, **kwargs)
        raise e
    finally:
        stop = time.time()
        if log_memory:
            rss_after = get_rss()
            kwargs['exec_rss_after'] = rss_after
            kwargs['exec_rss_delta'] = rss_before - rss_after

    if logger is None:
        print(event, stop - start)
        return

    logger_method = getattr(logger, 'info', None)
    if logger_method is not None:
        logger_method(event, exec_time=stop - start, **kwargs)
    else:
        logger.error(f'Unknown logger method {method}.')


class archive:
    @staticmethod
    def create(upload_id: str, entry_id: str) -> str:
        return f'{upload_id}/{entry_id}'

    @staticmethod
    def items(archive_id: str) -> list[str]:
        return archive_id.split('/')

    @staticmethod
    def item(archive_id: str, index: int) -> str:
        return archive.items(archive_id)[index]

    @staticmethod
    def entry_id(archive_id: str) -> str:
        return archive.item(archive_id, 1)

    @staticmethod
    def upload_id(archive_id: str) -> str:
        return archive.item(archive_id, 0)


def to_tuple(self, *args):
    return tuple(self[arg] for arg in args)


def chunks(list, n):
    """Chunks up the given list into parts of size n."""
    for i in range(0, len(list), n):
        yield list[i : i + n]


class SleepTimeBackoff:
    """
    Provides increasingly larger sleeps. Useful when
    observing long running processes with unknown runtime.
    """

    def __init__(self, start_time: float = 0.1, max_time: float = 5):
        self.current_time = start_time
        self.max_time = max_time

    def __call__(self):
        self.sleep()

    def sleep(self):
        time.sleep(self.current_time)
        self.current_time *= 2
        self.current_time = min(self.max_time, self.current_time)


class ETA:
    def __init__(self, total: int, message: str, interval: int = 1000):
        self.start = time.time()
        self.total = total
        self.count = 0
        self.interval = interval
        self.interval_count = 0
        self.message = message

    def add(self, amount: int = 1):
        self.count += amount
        interval_count = int(self.count / self.interval)
        if interval_count > self.interval_count:
            self.interval_count = interval_count
            delta_t = time.time() - self.start
            eta = delta_t * (self.total - self.count) / self.count
            eta_str = str(timedelta(seconds=eta))
            sys.stdout.write('\r' + (self.message % (self.count, self.total, eta_str)))
            sys.stdout.flush()

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args, **kwargs):
        print('')


def common_prefix(paths):
    """
    Computes the longest common file path prefix (with respect to '/' separated segments).
    Returns empty string is ne common prefix exists.
    """
    common_prefix = None

    for path in paths:
        if common_prefix is None:
            common_prefix = path

        index = 0
        index_last_slash = -1
        for a, b in zip(path, common_prefix):
            if a != b:
                break
            if a == '/':
                index_last_slash = index
            index += 1

        if index_last_slash == -1:
            common_prefix = ''
            break

        common_prefix = common_prefix[: index_last_slash + 1]

    if common_prefix is None:
        common_prefix = ''

    return common_prefix


class RestrictedDict(OrderedDict):
    """Dictionary-like container with predefined set of mandatory and optional
    keys and a set of forbidden values.
    """

    def __init__(
        self,
        mandatory_keys: Iterable | None = None,
        optional_keys: Iterable | None = None,
        forbidden_values: Iterable | None = None,
        lazy: bool = True,
    ):
        """
        Args:
            mandatory_keys: Keys that have to be present.
            optional_keys: Keys that are optional.
            forbidden_values: Values that are forbidden. Only supports hashable values.
            lazy: If false, the values are checked already when inserting. If
                True, the values should be manually checked by calling the
                check()-function.
        """
        super().__init__()

        if isinstance(mandatory_keys, list | tuple | set):
            self._mandatory_keys = set(mandatory_keys)
        elif mandatory_keys is None:
            self._mandatory_keys = set()
        else:
            raise ValueError(
                'Please provide the mandatory_keys as a list, tuple or set.'
            )

        if isinstance(optional_keys, list | tuple | set):
            self._optional_keys = set(optional_keys)
        elif optional_keys is None:
            self._optional_keys = set()
        else:
            raise ValueError(
                'Please provide the optional_keys as a list, tuple or set.'
            )

        if isinstance(forbidden_values, list | tuple | set):
            self._forbidden_values = set(forbidden_values)
        elif forbidden_values is None:
            self._forbidden_values = set()
        else:
            raise ValueError(
                'Please provide the forbidden_values as a list or tuple of values.'
            )

        self._lazy = lazy

    def __setitem__(self, key, value):
        if not self._lazy:
            # Check that only the defined keys are used
            if key not in self._mandatory_keys and key not in self._optional_keys:
                raise KeyError(f"The key '{key}' is not allowed.")

            # Check that forbidden values are not used.
            try:
                match = value in self._forbidden_values
            except TypeError:
                pass  # Unhashable value will not match
            else:
                if match:
                    raise ValueError(f"The value '{key}' is not allowed.")

        super().__setitem__(key, value)

    def check(self, recursive=False):
        # Check that only the defined keys are used
        for key in self.keys():
            if key not in self._mandatory_keys and key not in self._optional_keys:
                raise KeyError(f"The key '{key}' is not allowed.")

        # Check that all mandatory values are all defined
        for key in self._mandatory_keys:
            if key not in self:
                raise KeyError(f"The mandatory key '{key}' is not present.")

        # Check that forbidden values are not used.
        for key, value in self.items():
            match = False
            try:
                match = value in self._forbidden_values
            except TypeError:
                pass  # Unhashable value will not match
            else:
                if match:
                    raise ValueError(
                        f"The value '{value}' is not allowed but was set for key '{key}'."
                    )

        # Check recursively
        if recursive:
            for value in self.values():
                if isinstance(value, RestrictedDict):
                    value.check(recursive)

    def update(self, other):
        for key, value in other.items():
            self.__setitem__(key, value)

    def hash(self) -> str:
        """Creates a hash code from the contents. Ensures consistent ordering."""
        hash_str = json.dumps(self, sort_keys=True)

        return hash(hash_str)


@overload
def strip(docstring: None) -> None: ...


@overload
def strip(docstring: str) -> str: ...


def strip(docstring: str | None) -> str | None:
    """Removes any unnecessary whitespaces from a multiline doc string or description."""
    if docstring is None:
        return None
    return inspect.cleandoc(docstring)


def flatten_dict(src: dict, separator: str = '.', flatten_list: bool = False):
    """
    Flattens nested dictionaries so that all information is stored
    with depth=1.

    Args:
        src: Dictionary to flatten
        separator: String to use as a separator
        flatten_list: Whether lists should be flattened as well.
    """

    def helper_list(src):
        ret = {}
        for i in range(len(src)):
            if isinstance(src[i], dict):
                flat_value = flatten_dict(src[i], separator, flatten_list)
                for inner_key, inner_value in flat_value.items():
                    join_key = separator.join((str(i), inner_key))
                    ret[join_key] = inner_value
            elif isinstance(src[i], list):
                flat_value = helper_list(src[i])
                for inner_key, inner_value in flat_value.items():
                    join_key = separator.join((str(i), inner_key))
                    ret[join_key] = inner_value
            else:
                ret[str(i)] = src[i]

        return ret

    ret = {}
    for key, value in src.items():
        if isinstance(value, dict):
            flat_value = flatten_dict(value, separator, flatten_list)
            for inner_key, inner_value in flat_value.items():
                join_key = separator.join((key, inner_key))
                ret[join_key] = inner_value
        elif isinstance(value, list):
            if flatten_list:
                flat_value = helper_list(value)
                for inner_key, inner_value in flat_value.items():
                    join_key = separator.join((key, inner_key))
                    ret[join_key] = inner_value
            else:
                ret[key] = value
        else:
            ret[key] = value

    return ret


def rebuild_dict(src: dict, separator: str = '.'):
    """
    Rebuilds nested dictionaries from flattened ones.
    """
    separator_len = len(separator)

    def get_indices(key, split_index):
        next_key = key[split_index + separator_len :]
        num_string = list(takewhile(str.isdigit, next_key))
        n_numbers = len(num_string)
        index = int(''.join(num_string)) if n_numbers else None

        if index is not None:
            split_index_list = split_index
            split_index_dict = split_index + n_numbers + separator_len
        else:
            split_index_dict = split_index
            split_index_list = -1

        return split_index_dict, split_index_list, index

    def helper_dict(key, value, result):
        split_index = key.find(separator)

        # Insert dict item
        if split_index == -1:
            result.update({key: value})
        else:
            split_index_dict, split_index_list, next_index = get_indices(
                key, split_index
            )

            # Handle next dictionary
            if (
                split_index_list == -1
                or split_index_dict != -1
                and split_index_dict < split_index_list
            ):
                if result.get(key[:split_index_dict]) is None:
                    result.update({key[:split_index_dict]: {}})
                helper_dict(
                    key[split_index_dict + separator_len :],
                    value,
                    result[key[:split_index_dict]],
                )
            # Handle next list
            else:
                if result.get(key[:split_index_list]) is None:
                    result.update({key[:split_index_list]: []})
                helper_list(
                    key[split_index_list + separator_len :],
                    value,
                    next_index,
                    result[key[:split_index_list]],
                )

    def helper_list(key, value, index, result):
        split_index = key.find(separator)

        # Insert list item
        if split_index == -1:
            result.insert(index, value)
        else:
            split_index_dict, split_index_list, next_index = get_indices(
                key, split_index
            )

            # Grow list to size of this index
            if index > len(result):
                result.extend([None] * (index - len(result)))
            # Handle next dictionary
            if (
                split_index_list == -1
                or split_index_dict != -1
                and split_index_dict < split_index_list
            ):
                try:
                    old_value = result[index]
                except IndexError:
                    old_value = None
                if old_value is None:
                    result.insert(index, {})
                helper_dict(
                    key[split_index_dict + separator_len :], value, result[index]
                )
            # Handle next list
            else:
                if result[index] is None:
                    result.insert(index, [])
                helper_list(
                    key[split_index_list + separator_len :],
                    value,
                    next_index,
                    result[index],
                )

    ret: dict[str, Any] = {}
    for key, value in src.items():
        helper_dict(key, value, ret)

    return ret


def glob(path: str, include: list[str], exclude: list[str]) -> bool:
    """
    Determines if the given path matches include patterns and is not excluded.

    Args:
        path (str): The file path to check.
        include (list[str]): Glob patterns for inclusion.
        exclude (list[str]): Glob patterns for exclusion.

    Returns:
        bool: True if the path is accepted, False otherwise.
    """
    # If includes are specified, return False if none match
    if include and not any(fnmatch.fnmatch(path, pattern) for pattern in include):
        return False

    # Exclude if any exclusion pattern matches
    if exclude and any(fnmatch.fnmatch(path, pattern) for pattern in exclude):
        return False

    return True


def prune_dict(data, include_patterns=None, exclude_patterns=None):
    """
    Prune a nested dictionary based on include and exclude branch patterns.

    Args:
        data: The nested dictionary to prune.
        include_patterns: List of branch patterns to include. Supports wildcards
            like `root.child.*`.
        exclude_patterns: List of branch patterns to exclude. Supports wildcards
            like `root.child.*`.
        parent_key: Used internally for recursion to track the full key path.

    Returns:
        Pruned dictionary.
    """
    if include_patterns is None and exclude_patterns is None:
        return data

    # Preprocess patterns
    def process_patterns(patterns):
        if patterns is None:
            return []
        processed = []
        for pattern in patterns:
            if pattern.endswith('*'):
                parts = pattern.rstrip('*').removesuffix('.').split('.')
                processed.append((parts, True))
            else:
                parts = pattern.split('.')
                processed.append((parts, False))
        return processed

    include_patterns = process_patterns(include_patterns)
    exclude_patterns = process_patterns(exclude_patterns)

    def matches(path, patterns):
        for pattern_parts, wildcard in patterns:
            if wildcard:
                if path[: len(pattern_parts)] == tuple(pattern_parts):
                    return True
            else:
                if path == tuple(pattern_parts):
                    return True
        return False

    def has_descendant_pattern(path, patterns):
        for pattern_parts, _ in patterns:
            if (
                len(pattern_parts) > len(path)
                and tuple(pattern_parts[: len(path)]) == path
            ):
                return True
        return False

    def prune(data, path=()):
        # Check exclude patterns
        if matches(path, exclude_patterns):
            return None  # Excluded

        # Check include patterns
        if include_patterns:
            if matches(path, include_patterns):
                include_current = True
            elif has_descendant_pattern(path, include_patterns):
                include_current = False  # Need to check deeper
            else:
                return None  # Excluded
        else:
            include_current = True

        if isinstance(data, dict):
            new_dict = {}
            for key, value in data.items():
                new_path = path + (str(key),)
                pruned_value = prune(value, new_path)
                if pruned_value is not None:
                    new_dict[key] = pruned_value
            if new_dict:
                return new_dict
            elif include_patterns and matches(path, include_patterns):
                return {}  # Include empty dict if explicitly included
            else:
                return None  # Exclude empty dicts
        elif isinstance(data, list):
            new_list = []
            for item in data:
                pruned_item = prune(item, path)
                if pruned_item is not None:
                    new_list.append(pruned_item)
            if new_list:
                return new_list
            elif include_patterns and matches(path, include_patterns):
                return []  # Include empty list if explicitly included
            else:
                return None  # Exclude empty lists
        else:
            if include_current:
                return data
            else:
                return None

    return prune(data) or {}


def deep_get(dictionary, *keys):
    """
    Helper that can be used to access nested dictionary-like containers using a
    series of paths given as arguments. The path can contain dictionary string
    keys or list indices as integer numbers.

    Raises: ValueError if value not found for path.
    """
    try:
        return reduce(lambda d, key: d[key], keys, dictionary)
    except (IndexError, TypeError, KeyError):
        raise ValueError(f'Could not find path: {keys}')


def slugify(value):
    """
    Taken from https://github.com/django/django/blob/master/django/utils/text.py
    Convert to ASCII if 'allow_unicode' is False. Convert spaces or repeated
    dashes to single dashes. Remove characters that aren't alphanumerics,
    underscores, or hyphens. Convert to lowercase. Also strip leading and
    trailing whitespace, dashes, and underscores.
    """

    value = str(value)
    value = (
        unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    )
    value = re.sub(r'[^\w\s-]', '', value.lower())
    return re.sub(r'[-\s]+', '-', value).strip('-_')


def query_list_to_dict(path_list: list[str | int], value: Any) -> dict[str, Any]:
    """Transforms a list of path fragments into a dictionary query. E.g. the list

    ['run', 0, 'system', 2, 'atoms']

    would be converted into

    {
        'run:0': {
            'system:2': {
                'atoms': <value>
            }
        }
    }

    Args:
        path_list: List of path fragments
        value: The value to place inside the final dictionary key

    Returns:
        A nested dictionary representing the query path.

    """
    returned: dict[str, Any] = {}
    current = returned
    n_items = len(path_list)
    i = 0
    while i < n_items:
        name = path_list[i]
        index = None if i == n_items - 1 else path_list[i + 1]
        if isinstance(index, int):
            i += 1
        else:
            index = None
        key = f'{name}{f"[{index}]" if index is not None else ""}'
        current[key] = value if i == n_items - 1 else {}
        current = current[key]
        i += 1
    return returned


def traverse_reversed(archive: Any, path: list[str]) -> Any:
    """Traverses the given metainfo path in reverse order. Useful in finding the
    latest reported section or value.

    Args:
        archive: The root section to traverse
        path: List of path names to traverse

    Returns:
        Returns the last metainfo section or quantity in the given path or None
        if not found.
    """

    def traverse(root, path, i):
        if not root:
            return
        sections = getattr(root, path[i])
        if isinstance(sections, list):
            for section in reversed(sections):
                if i == len(path) - 1:
                    yield section
                else:
                    yield from traverse(section, path, i + 1)
        else:
            if i == len(path) - 1:
                yield sections
            else:
                yield from traverse(sections, path, i + 1)

    for t in traverse(archive, path, 0):
        if t is not None:
            yield t


def extract_section(root: Any, path: list[str], full_list: bool = False):
    """Extracts a section from source following the path and the last elements of the section
    lists. If full_list is True, the resolved section gives the full list instead of the
    last element.

    Args:
        source: The root section.
        path (str): List containing the sections to extract.
        full_list (bool, optional): For the last element of path, if set to True returns the full
            list instead. Defaults to False.

    Returns:
        root: The resolved section.
    """
    path_len = len(path) - 1
    for index, section in enumerate(path):
        try:
            value = getattr(root, section)
            if full_list and index == path_len:
                root = value
                break
            root = value[-1] if isinstance(value, list) else value
        except Exception:
            return
    return root


def dataframe_to_dict(df, sep='.'):
    """
    Reconstruct dictionary (JSON object) data from a pandas DataFrame.

    Args:
        df: Pandas DataFrame object.
        sep: String used to separate the keys.
    Returns:
        result: Reconstructed nested JSON object or list of Dict objects.
    """

    def is_integer(s):
        try:
            int(s)
            return True
        except ValueError:
            return False

    def insert_value(main_dict, keys, value, parent_key=None, parent_dict=None):
        for index, key in enumerate(keys):
            if is_integer(key):  # List
                key = int(key)
                if not isinstance(main_dict, list):
                    main_dict = [main_dict] if main_dict else []
                    if parent_dict is not None and parent_key is not None:
                        parent_dict[parent_key] = main_dict
                while len(main_dict) <= key:
                    main_dict.append({} if index < len(keys) - 1 else None)
                if index == len(keys) - 1:
                    main_dict[key] = value
                else:
                    parent_key, parent_dict = key, main_dict
                    main_dict = main_dict[key]
            else:  # Dictionary
                if not isinstance(main_dict, dict):
                    dct = {key: main_dict} if main_dict else {}
                    if parent_dict is not None and parent_key is not None:
                        parent_dict[parent_key] = main_dict
                if key not in main_dict:
                    main_dict[key] = {} if index < len(keys) - 1 else value
                if index == len(keys) - 1:
                    main_dict[key] = value
                else:
                    parent_key, parent_dict = key, main_dict
                    main_dict = main_dict[key]

    result = []
    for index, row in df.iterrows():
        current_result = {}
        for col, value in row.items():
            keys = [int(k) if is_integer(k) else k for k in col.split(sep)]
            insert_value(current_result, keys, value)
        result.append(current_result)

    if len(result) == 1:
        return result[0]
    else:
        return result


def dict_to_dataframe(
    dict_data, max_depth=None, sep='.', sort_by='alphabetical', keys_to_filter=None
):
    """
    Convert a nested Dict object or a list of Dict objects into a Pandas DataFrame.

    Args:
        dict_data: Nested Dict object or a list of Dict objects.
        max_depth: The maximum depth level in the Dict structure to flatten.
        sep: String used to separate the keys.
        sort_by: A character denoting the separator between keys.
        keys_to_filter: A list of strings targeting specific columns of the dataframe to be filtered. For example a value ['a.b'] filters the dataframe columns that only starts with 'a.b'.

    Returns:
        result: Pandas DataFrame with flattened and sorted data.
    """

    import pandas as pd

    if not keys_to_filter:
        keys_to_filter = []

    def flatten_dict(
        nested_dict, parent_key='', current_depth=0, df=None, col_name=None
    ):
        """
        Flattens a nested Dict structure into a flat dictionary with a maximum depth.

        Args:
            nested_dict: The nested Dict structure (dict or list).
            parent_key: The base key for the current level in the Dict structure.
            current_depth: The current depth level in the Dict structure.
            df: DataFrame to which the flattened dictionary will be added.
            col_name: Name of the column in the DataFrame where the flattened dictionary will be added.

        Returns:
            items: A flat dictionary with keys combined.
        """
        items = {}
        if max_depth is not None and current_depth > max_depth:
            items[parent_key] = nested_dict
            return items

        if isinstance(nested_dict, list):
            all_dicts = all(isinstance(item, dict) for item in nested_dict)
            if all_dicts:
                for index, element in enumerate(nested_dict):
                    items.update(
                        flatten_dict(
                            element,
                            f'{parent_key}{sep}{index}' if parent_key else str(index),
                            current_depth + 1,
                            df=df,
                            col_name=col_name,
                        )
                    )
            else:
                if df is not None and col_name is not None:
                    df.at[parent_key, col_name] = nested_dict
                else:
                    items[parent_key] = nested_dict
        elif isinstance(nested_dict, dict):
            for key, value in nested_dict.items():
                new_key = f'{parent_key}{sep}{key}' if parent_key else key
                if isinstance(value, dict | list):
                    items.update(
                        flatten_dict(value, new_key, current_depth + 1, df, col_name)
                    )
                else:
                    items[new_key] = value
        else:
            items[parent_key] = nested_dict

        if df is not None and col_name is not None:
            return df.transpose()
        else:
            return items

    def filter_df_columns_by_prefix(df, prefixes=None, sep='.'):
        """
        Filters a pandas dataframe by columns given a list of prefixes.

        Args:
            df: Pandas dataframe object containing the data.
            sep: String used to separate the keys.
            prefixes: List of prefixes indicating the column names to be filtered. for example ['a.b'] filters all columns in the dataframe that starts with 'a.b'.

        Returns:
            result: Pandas DataFrame with filtered data that is sorted alphabetically.
        """

        if not prefixes:
            prefixes = []

        if not prefixes:
            return df

        filtered_columns = []
        for prefix in prefixes:
            filtered_columns.extend(
                [col for col in df.columns if col.startswith(prefix)]
            )

        result = df[filtered_columns].copy()

        for prefix in prefixes:
            prefix_columns = [col for col in filtered_columns if col.startswith(prefix)]
            numeric_columns = [
                col for col in prefix_columns if col.split(sep)[-1].isdigit()
            ]

            for col in prefix_columns:
                if col in numeric_columns:
                    if col.split(sep)[-1][0].isdigit():
                        new_col_name = prefix.split('.')[-1]
                        new_values = result[numeric_columns].values.tolist()
                        result[new_col_name] = new_values
                        result = result.drop(columns=numeric_columns)
                        break

            result.columns = [col.split(prefix + sep)[-1] for col in result.columns]
        result = result.reindex(
            sorted(result.columns), axis=1
        )  # Sorting alphabetically
        return result

    def flatten_data(data):
        if isinstance(data, dict):
            # Single Dict object
            flattened_data = flatten_dict(data)
            result = pd.DataFrame([flattened_data])
        elif isinstance(data, list):
            # List of Dict objects
            flattened_data = [flatten_dict(item) for item in data]
            result = pd.DataFrame(flattened_data)
        else:
            raise ValueError(
                'Input must be a dictionary (JSON object) or a list of dictionaries (JSON objects)'
            )

        if sort_by == 'alphabetical':
            result = result.reindex(sorted(result.columns), axis=1)
        elif isinstance(sort_by, list):
            result = result.reindex(columns=sort_by)

        return result

    df = flatten_data(dict_data)

    if not keys_to_filter:
        return df

    else:
        filtered_df = filter_df_columns_by_prefix(df, keys_to_filter)
        filtered_dict = dataframe_to_dict(filtered_df)
        return pd.json_normalize(filtered_dict, errors='ignore')


def nomad_distro_metadata() -> str | None:
    """
    Retrieves metadata for the 'nomad-distribution' package, including the
    repository URL with latest commit hash.

    Returns:
        The repo url with commit hash or None if unavailable.
    """
    try:
        distro_metadata = metadata('nomad-distribution')

        # Extract repository URL from Project-URL metadata
        project_urls: list[str] = distro_metadata.get_all('Project-URL', [])
        repo_url = next(
            (
                url.split(', ', 1)[1]
                for url in project_urls
                if url.startswith('repository, ')
            ),
            None,
        )

        distro_version = version('nomad-distribution')
        if '+g' in distro_version:
            # Split on '+g' to extract the commit hash from the version string, as 'g' is a Git-specific prefix.
            commit = distro_version.split('+g')[
                -1
            ]  # Extract commit hash if present (setuptools_scm format)
        else:
            commit = (
                f'v{distro_version}'  # Otherwise, assume it's a tag and prefix with 'v'
            )

        if not repo_url or not commit:
            return None

        commit_url = f'{repo_url}/tree/{commit}'

        return commit_url
    except (PackageNotFoundError, IndexError, StopIteration, KeyError):
        return None
