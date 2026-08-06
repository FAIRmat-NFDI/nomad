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

import urllib.parse
from enum import Enum

from fastapi import Header, Query, Response
from rdflib import Graph

from nomad.config import config

root_path = config.services.join_path('dcat')
base_url = config.api_url(api='dcat')


def url(*args, **kwargs):
    """Returns the full dcat api url for the given path (args) and query (kwargs) parameters."""
    url = f'{base_url.rstrip("/")}/{"/".join(args).lstrip("/")}'

    if len(kwargs) > 0:
        return f'{url}?{urllib.parse.urlencode(kwargs)}'
    else:
        return url


class Formats(str, Enum):
    xml = 'xml'
    n3 = 'n3'
    turtle = 'turtle'
    nt = 'nt'
    pretty_xml = 'pretty-xml'
    trig = 'trig'


all_repsonse_types = {
    'application/xml': 'xml',
    'application/rdf+prettyxml': 'pretty-xml',
    'application/rdf': 'xml',
    'application/rdf+xml': 'xml',
    'text/plain': 'n3',
    'text/turtle': 'turtle',
    'text/nt': 'nt',
    'text/n3': 'n3',
    'text/rdf+n3': 'n3',
    'text/rdf+nt': 'nt',
    'text/rdf+turtle': 'turtle',
    'application/x-trig': 'trig',
}

response_types = [
    'application/xml',
    'application/rdf+xml',
    'application/rdf+pretty-xml',
    'text/plain',
    'text/turtle',
    'text/rdf+n3',
    'text/rdf+nt',
    'application/x-trig',
]


def rdf_response(
    format: Formats | None = Query(None), accept: str | None = Header(None)
):
    format_ = format.value if format else None
    if format_ is None:
        if accept:
            format_ = all_repsonse_types.get(accept, 'pretty-xml')
        else:
            format_ = 'pretty-xml'

    def create_response(g: Graph):
        try:
            content_type = next(
                key for key, value in all_repsonse_types.items() if value == format_
            )
        except StopIteration:
            content_type = (
                'application/xml'
                if format_ in ['xml', 'pretty-xml']
                else f'text/format'
            )

        return Response(
            g.serialize(format=format_).decode('utf-8'), media_type=content_type
        )

    return create_response
