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

import datetime
import re

import click
import dateutil.parser
import gitlab

from nomad.config import config

print(
    'This script will look for and delete nomad-FAIR container registry tags that are '
    'not version tags and older than 90 days.'
)

gl = gitlab.Gitlab(
    'https://gitlab.mpcdf.mpg.de',
    private_token=config.gitlab.private_token.get_secret_value(),
)

project = next(
    project
    for project in gl.projects.list(search='nomad-FAIR')
    if project.name == 'nomad-FAIR'
)

repository = next(
    repository
    for repository in project.repositories.list()
    if repository.path == 'nomad-lab/nomad-fair'
)

to_delete = []
for tag in repository.tags.list(per_page=1000, requires=['created_at']):
    if re.match(r'v[0-9]+\.[0-9]+\.[0-9]+[-.*]?', tag.name):
        continue

    tag_details = gl.http_get(
        f'/projects/{project.id}/registry/repositories/{repository.id}/tags/{tag.name}'
    )
    created_at = dateutil.parser.parse(tag_details['created_at'])
    age = datetime.datetime.now() - created_at.replace(tzinfo=None)
    if age.days > 90:
        to_delete.append(tag)
        print(f'Mark {tag.name} ({age.days} days old) for deletion')

if click.confirm(f'Do you want to delete {len(to_delete)} tags?', default=True):
    for tag in to_delete:
        tag.delete()

    print(f'Deleted {len(to_delete)} tags.')
