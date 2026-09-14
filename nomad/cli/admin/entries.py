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

import click

from .admin import admin


@admin.group(help='Entry related commands')
def entries():
    pass


@entries.command(help='Delete selected entries from mongo and elastic')
@click.argument('ENTRIES', nargs=-1)
@click.option(
    '--skip-es', help='Keep the elastic index version of the data.', is_flag=True
)
@click.option('--skip-mongo', help='Keep uploads and entries in mongo.', is_flag=True)
def rm(entries, skip_es, skip_mongo):
    from nomad import infrastructure, search
    from nomad import processing as proc

    infrastructure.setup_mongo()
    infrastructure.setup_elastic()

    print(f'{len(entries)} entries selected, deleting ...')

    if not skip_es:
        for entry in entries:
            search.delete_entry(entry_id=entry, refresh=True)

    if not skip_mongo:
        proc.Entry.objects(entry_id__in=entries).delete()  # type: ignore
