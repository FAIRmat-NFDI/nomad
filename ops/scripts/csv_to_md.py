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
import sys
import urllib.parse
from inspect import cleandoc as strip

import click
import requests


@click.command()
@click.option('--issue-id')
@click.option('--project-id', default='2187')
@click.option('--gitlab-url', default='https://gitlab.mpcdf.mpg.de/api/v4')
@click.option('--access-token')
@click.argument('csv-files', nargs=-1, type=click.Path(exists=True))
def processing_issues(issue_id, project_id, access_token, gitlab_url, csv_files):
    data = []
    for file in csv_files:
        with open(file, encoding='utf-8-sig') as f:
            data += [row for row in csv.DictReader(f)]

    data = reversed(sorted(data, key=lambda item: int(item['n_entries'])))

    filtered_data = {}
    for item in data:
        key = f'{item["event"]}:{item["entry_id"]}'
        existing = filtered_data.setdefault(key, item)
        existing.update(item)
    data = filtered_data.values()

    issues = []
    for item in data:
        issue = (
            strip(f"""
            - [ ] **{item['n_entries']}** entries in {item['n_uploads']} uploads: *{item['event']}*,\\
            entry_id: `{item['entry_id']}`, upload_id: `{item['upload_id']}`,\\
            parser: `{item['parser']}`{f', normalizer: `{item["normalizer"]}`' if item['normalizer'] != '-' else ''}\\
            [process installation](https://nomad-lab.eu/prod/v1/process/gui/entry/id/{item['entry_id']}),
            [production installation](https://nomad-lab.eu/prod/v1/gui/entry/id/{item['entry_id']})

        """)
            + '\n'
        )

        if 'exception' in item:
            issue += f'\n```\n{item["exception"]}\n```\n'

        issue += '\n'

        issues.append(issue)

    if not (issue_id and project_id and access_token):
        for issue in issues:
            print(issue)
        sys.exit(0)

    for issue in issues:
        response = requests.post(
            url=f'{gitlab_url}/projects/{project_id}/issues/{issue_id}/notes?body={urllib.parse.quote(issue)}',
            headers={'PRIVATE-TOKEN': access_token},
        )

        if response.status_code >= 300:
            print(f'success fully posted:\n{issue}')
        else:
            print(f'Could not post an issue ({response.status_code})')
            print(response.text)


if __name__ == '__main__':
    processing_issues()
