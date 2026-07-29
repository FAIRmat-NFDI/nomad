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
import csv
import re

from nomad.parsing.parser import MatchingParser

from nomad_countries.schema_packages.country import Country, Timeseries


class CountryParser(MatchingParser):
    def parse(self, mainfile, archive, logger):
        data = self.read(mainfile)
        archive.data = Country(
            name=data.get('Country'),
            population=data.get('Population'),
            area=data.get('Area (sq. mi.)'),
            coastline=data.get('Coastline (coast/area ratio)'),
            net_migration=data.get('Net migration'),
            infant_mortality=data.get('Infant mortality (per 1000 births)'),
            literacy=data.get('Literacy (%)'),
            phones=data.get('Phones (per 1000)'),
            birthrate=data.get('Birthrate'),
            deathrate=data.get('Deathrate'),
            agriculture=data.get('Agriculture'),
            industry=data.get('Industry'),
            service=data.get('Service'),
            gdp=self.timeseries(data, 'GDP per capita (constant 2005 US$)'),
            birth_rate=self.timeseries(data, 'Birth rate, crude (per 1,000 people)'),
        )

    def read(self, mainfile):
        data = {}
        with open(mainfile, 'rt') as f:
            # parse the header part
            while True:
                line = f.readline()
                match = re.match(r'#([^=]+)=(.*)', line)
                if not match:
                    break
                if match.group(2) == '':
                    continue
                key, str_value = match.group(1), match.group(2)
                try:
                    value = float(str_value.replace(',', '.'))
                except Exception:
                    value = str_value

                data[key] = value

            # parse the csv part
            reader = csv.reader(f, delimiter='\t')
            for row in reader:
                if not len(row) == 3:
                    continue
                indicator = row[0]
                indicator_data = data.setdefault(indicator, [])
                # let's not overwrite properties from the header
                if not isinstance(indicator_data, list):
                    continue
                indicator_data.append(
                    (
                        row[1],
                        row[2],
                    )
                )

        return data

    def timeseries(self, data, indicator):
        if indicator not in data:
            return None

        return Timeseries(
            year=[int(year) for year, _ in data[indicator]],
            value=[float(value) for _, value in data[indicator]],
        )
