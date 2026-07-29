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
import numpy as np

from nomad.metainfo import MSection, Quantity, SubSection


class Element(MSection):
    label = Quantity(type=str)
    density = Quantity(type=np.float64, unit='g/cm**3')
    isotopes = Quantity(type=np.int32, shape=['*'])


class Sample(MSection):
    composition = Quantity(type=str)
    elements = SubSection(section=Element, repeats=True)
