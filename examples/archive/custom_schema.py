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
from nomad.datamodel import ArchiveSection, Schema
from nomad.metainfo.metainfo import Datetime, Quantity, SubSection


class Sample(ArchiveSection):
    added_date = Quantity(type=Datetime)
    formula = Quantity(type=str)

    sample_id = Quantity(type=str)

    def normalize(self, archive, logger):
        super().normalize(archive, logger)

        if self.sample_id is None:
            self.sample_id = f'{self.added_date}--{self.formula}'


class SampleDatabase(Schema):
    samples = SubSection(section=Sample, repeats=True)
