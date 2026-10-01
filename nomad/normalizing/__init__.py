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
After parsing entries have to be normalized with a set of *normalizers*.
In NOMAD-coe those were programmed in python (we'll reuse) and scala (we'll rewrite).

Currently the normalizers are:
- system.py (contains aspects of format stats, system, system type, and symmetry normalizer)
- optimade.py
- fhiaims.py
- dos.py

The normalizers are available via

.. autodata:: nomad.normalizing.normalizers

There is one ABC for all normalizer:

.. autoclass::nomad.normalizing.normalizer.Normalizer
    :members:
"""

import importlib
from typing import Any
from collections.abc import Iterator
from collections import UserList

from nomad.config import config

from nomad.config.models.plugins import (
    NormalizerEntryPoint,
)
from .normalizer import Normalizer


def import_normalizer(class_name: str):
    try:
        package, cls = class_name.rsplit('.', 1)
        return getattr(importlib.import_module(package), cls)
    except Exception as e:
        raise ImportError(f'Cannot import normalizer {class_name}', e)


class SortedNormalizers(UserList):
    def __iter__(self) -> Iterator:
        self.sort(key=lambda x: x.normalizer_level)
        return super().__iter__()


class NormalizerInterface:
    def __init__(self, path: str) -> None:
        self._path = path
        self._cls = None
        self.archive = None

    @property
    def normalizer_class(self):
        if self._cls is None:
            self._cls = import_normalizer(self._path)
        return self._cls

    def normalize(self, logger=None):
        self.normalizer.normalize(self.archive, logger)

    def __call__(self, *args: Any) -> Any:
        self.archive = args[0]
        self.normalizer = self.normalizer_class(*args[1:])
        return self

    def __getattr__(self, name: str):
        return getattr(self.normalizer_class, name, None)


class NormalizerInterfaceNew:
    def __init__(self, normalizer: Normalizer, level: int) -> None:
        self.normalizer = normalizer
        self.archive = None
        self.level = level

    def normalize(self, logger=None):
        self.normalizer.normalize(self.archive, logger)

    def __call__(self, *args: Any) -> Any:
        self.archive = args[0]
        return self

    def __getattr__(self, name: str):
        if name == '__name__':
            return self.normalizer.__class__.__name__
        if name == 'normalizer_level':
            return self.normalizer.normalizer_level or self.level
        return getattr(self.normalizer, name, None)


config.load_plugins()
enabled_entry_points = []
if config.plugins is not None:
    enabled_entry_points = config.plugins.entry_points.filtered_values()
normalizers = SortedNormalizers([])

for normalizer in config.normalize.normalizers.filtered_values():
    normalizers.append(NormalizerInterface(normalizer))

# Load normalizers using new plugin mechanism
for entry_point in enabled_entry_points:
    if isinstance(entry_point, NormalizerEntryPoint):
        instance = entry_point.load()
        assert isinstance(instance, Normalizer), (
            f'Error loading entry point "{entry_point.id}": The load method of a normalizer entry point must return a Normalizer instance'
        )
        normalizers.append(
            NormalizerInterfaceNew(instance, entry_point.execution_order)
        )
