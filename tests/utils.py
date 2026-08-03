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

"""Methods to help with testing of nomad@FAIRDI."""

import os.path
import re
import urllib.parse
import zipfile
from collections.abc import Iterable
from logging import LogRecord
from typing import Any

import pytest


class ListWithSortKey(list):
    """List with an attribute `sort_key`.

    Use to sort two lists by the same key with sorted().
    """

    def __init__(self, iterable=(), sort_key=None):
        super().__init__(iterable)
        self.sort_key = sort_key


def assert_log(
    log_output, level: str, event_part: str, negate: bool = False
) -> LogRecord | None:
    """
    Assert whether a log message exists in the logs of the tests at a certain level.

    Parameters
    ----------
    log_output : pytest fixture
        This informs pytest that we want to access the logs from a pytest test.
    level : str
        The level of type of log for which we will search (e.g. 'WARN',
        'ERROR', 'DEBUG').
    event_part : str
        The error message we're after. We search the logs matching level if they
        contain this string.
    negate: Instead asserting that the log exist, assert that it does not exist.
    """
    match = None
    for record in log_output.entries:
        if record['log_level'] == level.lower():
            present = event_part in record['event']
            if present:
                match = record
                # No need to look for more matches since we aren't counting matches.
                break

    assert match is None if negate else match is not None

    return match


def assert_at_least(source, target):
    """
    Compares two dicts recursively and asserts that all information in source equals
    the same information in target. Additional information in target is ignored.
    """
    for key, value in source.items():
        assert key in target, f'{key} with value {value} in {source} is not in {target}'
        if isinstance(value, dict):
            assert_at_least(value, target[key])
        else:
            assert value == target[key], (
                f'{key} with value {value} in {source} is not equal the target value {target[key]} in {target}'
            )


def assert_url_query_args(url: str, **kwargs):
    """
    Parses the url, and checks that the query arguments match the values specified by kwargs.
    """
    __, __, __, __, query, __ = urllib.parse.urlparse(url)
    query_dict = urllib.parse.parse_qs(query)
    for k, v in kwargs.items():
        if v is None:
            assert k not in query_dict
        else:
            assert query_dict[k][0] == str(v)


def build_url(base_url: str, query_args: dict[str, Any]) -> str:
    """
    Takes a base_url and a dictionary, and combines to a url with query arguments.
    Arguments with value None are ignored.
    """
    # Remove args with value None
    query_args_clean = {k: v for k, v in query_args.items() if v is not None}
    if not query_args_clean:
        return base_url
    return base_url + '?' + urllib.parse.urlencode(query_args_clean, doseq=True)


def set_upload_entry_metadata(upload, metadata: dict[str, Any]):
    """
    Sets the provided metadata values on all entries of the given upload.
    """
    from nomad import processing as proc

    for entry in proc.Entry.objects(upload_id=upload.upload_id):
        entry.set_mongo_entry_metadata(**metadata)
        entry.save()


def create_template_upload_file(
    tmp,
    mainfiles: str | list[str] | None = None,
    auxfiles: int = 4,
    directory: str = 'examples_template',
    name: str = 'examples_template.zip',
    more_files: str | list[str] | None = None,
):
    """
    Creates a temporary upload.zip file based on template.json (for the artificial test
    parser) that can be used for test processings.
    """

    if mainfiles is None:
        mainfiles = 'tests/data/proc/templates/template.json'

    if isinstance(mainfiles, str):
        mainfiles = [mainfiles]

    if more_files is None:
        more_files = []

    if isinstance(more_files, str):
        more_files = [more_files]

    upload_path = os.path.join(tmp, name)
    with zipfile.ZipFile(upload_path, 'w') as zf:
        for i in range(0, auxfiles):
            with zf.open(f'{directory}/{i}.aux', 'w') as f:
                f.write(b'content')

        for mainfile in mainfiles:
            zf.write(mainfile, f'{directory}/{os.path.basename(mainfile)}')

        for additional_file in more_files:
            zf.write(
                additional_file, f'{directory}/{os.path.basename(additional_file)}'
            )

    return upload_path


def fake_user_uuid(handle):
    """Return a test user uuid based on the handle."""
    uuid = '00000000-0000-0000-0000-' + str(handle).rjust(12, '0')
    assert len(uuid) == 36
    return uuid


def fake_group_uuid(handle: Any):
    """Return a test user group uuid based on the handle."""
    uuid = str(handle).rjust(22, 'G')
    assert len(uuid) == 22
    return uuid


def generate_convert_label(mapping):
    """Returned function converts labels to values according to mapping,
    also in lists and dicts, returns copies. Missing labels persist."""

    def convert(raw):
        if isinstance(raw, str):
            return mapping.get(raw, raw)

        if isinstance(raw, list):
            return [convert(v) for v in raw]

        if isinstance(raw, dict):
            return {k: convert(v) for k, v in raw.items()}

        return raw

    return convert


def dict_to_params(d):
    """Convert a dict to a list of pytest.param tuples with keys as ids. Return it.

    Can be used to make the parametrize decorator more concise."""
    return [pytest.param(*item, id=id) for id, item in d.items()]


def list_without(lst: Iterable, *items: object) -> list:
    """Return a copy of lst without any of the items."""
    return [x for x in lst if x not in items]


def assert_doi_name(doi_name):
    # Lowercase Crockford alphabet
    cf4 = r'[0-9abcdefghjkmnpqrstvwxyz]{4}'
    assert re.fullmatch(rf'10.83696/nomad\.{cf4}-{cf4}', doi_name)
