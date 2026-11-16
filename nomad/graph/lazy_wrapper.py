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
This file includes some wrappers that delay certain costly operations.
The idea is to wrap the object, or the operation into a transparent wrapper.
The evaluation is only performed when the results are really needed.

Different wrappers are catered for different types of objects/operations.
"""

from __future__ import annotations

from functools import cached_property

from nomad.datamodel import User


class LazyWrapper:
    def to_json(self):
        raise NotImplementedError


class LazyUserWrapper(LazyWrapper):
    """
    Hold a user id and resolve it to a user object when needed.
    If the user is not found, the user id is returned.
    """

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.user = None
        self.requested = False

    def _resolve(self):
        if not self.requested:
            self.requested = True

            try:
                self.user = User.get(user_id=self.user_id)
            except Exception:  # noqa
                self.user = self.user_id

            if self.user is None:
                self.user = self.user_id
            else:
                self.user = self.user.m_to_dict(
                    with_out_meta=True, include_derived=True
                )

        return self.user

    def __getitem__(self, item):
        self._resolve()
        return self.user.__getitem__(item)

    def __iter__(self):
        self._resolve()
        return self.user.__iter__()

    def __len__(self):
        self._resolve()
        return self.user.__len__()

    def to_json(self):
        return self._resolve()


class CachedUpload:
    """
    Wrap an upload object and cache some properties.
    Those properties involve additional queries.
    They are not cached in the original object, and it cannot be done there.
    """

    def __init__(self, upload) -> None:
        self.upload = upload

    @cached_property
    def n_entries(self):
        return self.upload.total_entries_count

    @cached_property
    def processing_successful(self):
        return self.upload.successful_entries_count

    @cached_property
    def processing_failed(self):
        return self.upload.failed_entries_count


class LazyUploadTotalCount(LazyWrapper):
    def __init__(self, upload: CachedUpload):
        self.upload = upload

    def to_json(self):
        return self.upload.n_entries


class LazyUploadSuccessCount(LazyWrapper):
    def __init__(self, upload: CachedUpload):
        self.upload = upload

    def to_json(self):
        return self.upload.processing_successful


class LazyUploadFailureCount(LazyWrapper):
    def __init__(self, upload: CachedUpload):
        self.upload = upload

    def to_json(self):
        return self.upload.processing_failed
