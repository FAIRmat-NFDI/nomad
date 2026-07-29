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
from nomad.config.models.plugins import ParserEntryPoint
from pydantic import Field


class CountryParserEntryPoint(ParserEntryPoint):
    def load(self):
        from nomad_countries.parsers.country import CountryParser

        return CountryParser(**self.dict())


country = CountryParserEntryPoint(
    name='CountryParser',
    description='Parser defined using the new plugin mechanism.',
    mainfile_name_re='^.*\.data\.txt$',
    mainfile_contents_re='#Country=\w+',
)
