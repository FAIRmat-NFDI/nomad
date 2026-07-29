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
This examplefies how to send raw queries to elasticsearch.

Specifically this will read all materials with fingerprints from the search engine
and store them in a local file. We use composite aggregations with after-based
pagination.
"""

import json

from nomad import config, infrastructure

infrastructure.setup_files()
infrastructure.setup_elastic()


results = []
after = None
count = 0
while True:
    request = {
        'query': {
            'bool': {
                'must': [
                    {
                        'match': {'dft.quantities': 'section_dos_fingerprint'},
                    },
                    {
                        'match': {'published': True},
                    },
                    {'match': {'with_embargo': False}},
                ]
            }
        },
        'size': 0,
        'aggs': {
            'results': {
                'composite': {
                    'sources': {
                        'materials': {
                            'terms': {'field': 'encyclopedia.material.material_id'}
                        }
                    },
                    'size': 10000,
                },
                'aggs': {
                    'calcs': {
                        'top_hits': {
                            'sort': {
                                '_script': {
                                    'type': 'number',
                                    'script': {
                                        'lang': 'painless',
                                        'source': """
                                            int result = 0;
                                            String code = doc['dft.code_name'].value;
                                            String functional = doc['dft.xc_functional'].value;
                                            if (functional == 'GGA') result += 100;
                                            if (code == 'VASP')
                                                result += 1;
                                            else if (code == 'FHI-aims')
                                                result += 2;
                                            return result;
                                        """,
                                    },
                                    'order': 'asc',
                                },
                            },
                            '_source': {
                                'includes': [
                                    'upload_id',
                                    'entry_id',
                                    'dft.code_name',
                                    'dft.xc_functional',
                                ]
                            },
                            'size': 1,
                        }
                    }
                },
            }
        },
    }

    if after is not None:
        request['aggs']['results']['composite']['after'] = after
    res = infrastructure.elastic_client.search(
        index=config.elastic.entries_index, body=request
    )

    if len(res['aggregations']['results']['buckets']) == 0:
        break

    after = res['aggregations']['results']['after_key']
    for material_bucket in res['aggregations']['results']['buckets']:
        material_id = material_bucket['key']['materials']
        entry = material_bucket['calcs']['hits']['hits'][0]['_source']
        upload_id = entry['upload_id']
        entry_id = entry['entry_id']
        results.append(
            dict(material_id=material_id, upload_id=upload_id, entry_id=entry_id)
        )
        count += 1

    print(count)

results.sort(key=lambda item: item['upload_id'])
with open('local/materials.json', 'w') as f:
    f.write(json.dumps(results, indent=2))
