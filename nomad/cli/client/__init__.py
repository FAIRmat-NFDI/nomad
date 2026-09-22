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


from nomad.cli.cli import cli
from nomad.config import config


@cli.group(help='Commands that use the nomad API to do useful things')
@click.option(
    '-n',
    '--url',
    default=config.client.url,
    help=f'The URL where nomad is running, default is "{config.client.url}".',
)
@click.option(
    '-u', '--user', default=None, help='the user name to login, default is no login.'
)
@click.option(
    '-w',
    '--password',
    default=(
        config.client.password.get_secret_value() if config.client.password else None
    ),
    help='the password used to login.',
)
@click.option(
    '--token-via-api',
    is_flag=True,
    help='retrieve the access token from the api not keycloak.',
)
@click.option(
    '--no-ssl-verify',
    help='disables SSL verificaton when talking to nomad.',
    is_flag=True,
)
@click.pass_context
def client(
    ctx, url: str, user: str, password: str, no_ssl_verify: bool, token_via_api: bool
):
    config.client.url = url

    ctx.obj.user = user
    ctx.obj.password = password
    ctx.obj.token_via_api = token_via_api


def _create_auth(ctx):
    print(f'Used nomad is {config.client.url}')
    print(
        f'Used user from CLI args is {ctx.obj.user}, using config.client.user {config.client.user}'
    )

    from nomad.client import Auth

    if ctx.obj.user is None:
        return Auth(
            user=config.client.user,
            password=(
                config.client.password.get_secret_value()
                if config.client.password
                else None
            ),
            from_api=ctx.obj.token_via_api,
        )
    else:
        return Auth(
            user=ctx.obj.user, password=ctx.obj.password, from_api=ctx.obj.token_via_api
        )


@client.command(help='Runs a few example operations as a test.')
@click.option(
    '--skip-parsers', is_flag=True, help='Skip extensive upload and parser tests.'
)
@click.option(
    '--skip-publish',
    is_flag=True,
    help='Skip publish the upload. Should not be done on an production environment.',
)
@click.option('--skip-doi', is_flag=True, help='Skip assigning a doi to a dataset.')
@click.pass_context
def integrationtests(ctx, skip_parsers, skip_publish, skip_doi):
    import sys

    if ctx.obj.user is None:
        print(
            'Authorization is required. Please provide credentials via --user, --password.'
        )
        sys.exit(1)

    from .integrationtests import integrationtests

    integrationtests(_create_auth(ctx), skip_parsers, skip_publish, skip_doi)


@client.command(
    help='Metainfo compatibility tests against data in a NOMAD installation.'
)
@click.pass_context
def datatests(ctx):
    from .datatests import datatests

    datatests(_create_auth(ctx))


@client.command(
    help='Upload files to nomad. The given path can be a single file or a directory. '
    'For file(s) that are not .zip or .tar files, a temporary .zip file '
    'will be created and uploaded.'
)
@click.argument('PATH', nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    '--upload-name',
    help='Optional name for the upload of a single file. Will be ignored on directories.',
)
@click.option(
    '--local-path',
    is_flag=True,
    default=False,
    help='Upload files "offline": files will not be uploaded, but processed were they are. '
    'Only works when run on the nomad host.',
)
@click.option(
    '--publish',
    is_flag=True,
    default=False,
    help='Automatically move upload out of the staging area after successful processing',
)
@click.option(
    '--ignore-path-prefix',
    is_flag=True,
    default=False,
    help='Ignores common path prefixes when creating an upload.',
)
@click.pass_context
def upload(
    ctx,
    path,
    upload_name: str,
    local_path: bool,
    publish: bool,
    ignore_path_prefix: bool,
):
    import os
    import sys
    import zipfile
    import tempfile

    from nomad.client import upload_file as client_upload_file

    auth = _create_auth(ctx)
    paths = path

    prefix = os.path.commonprefix(paths)
    if not os.path.isdir(prefix):
        prefix = os.path.dirname(prefix)
    file_paths = []
    zip_paths = []

    def add_file(file):
        _, ext = os.path.splitext(file)
        if ext in ['.zip', '.tar', 'tgz', 'tar.gz']:
            zip_paths.append(file)
        else:
            file_paths.append(file)

    for file_path in paths:
        if os.path.isfile(file_path):
            add_file(file_path)

        elif os.path.isdir(file_path):
            for dirpath, _, filenames in os.walk(file_path):
                for filename in filenames:
                    add_file(os.path.join(dirpath, filename))

    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
        click.echo(f'create temporary zipfile from {len(file_paths)} files')
        with zipfile.ZipFile(temp_zip.name, 'w') as zip_file:
            for file_path in file_paths:
                if file_path.startswith(prefix) and ignore_path_prefix:
                    arcname = file_path[len(prefix) :]
                else:
                    arcname = file_path
                zip_file.write(file_path, arcname=arcname)
        zip_paths.append(temp_zip.name)

        for zip_path in zip_paths:
            click.echo(f'uploading {zip_path}')
            upload_name = (
                upload_name if upload_name is not None else os.path.basename(path)
            )

            result = client_upload_file(
                zip_path,
                auth,
                upload_name=upload_name,
                local_path=local_path,
                publish=publish,
            )
            if result is None:
                sys.exit(1)


@client.command(help='Run processing locally.')
@click.argument('ENTRY_ID', nargs=1, required=True, type=str)
@click.option('--override', is_flag=True, help='Override existing local entry data.')
@click.option(
    '--show-archive/--no-archive',
    default=True,
    help='Whether to print out the archive data into stdout.',
)
@click.option(
    '--show-metadata', is_flag=True, help='Print the extracted repo metadata.'
)
@click.option('--skip-normalizers', is_flag=True, help='Do not normalize.')
@click.option('--not-strict', is_flag=True, help='Also match artificial parsers.')
@click.pass_context
def local(
    ctx, entry_id, show_archive, show_metadata, skip_normalizers, not_strict, **kwargs
):
    import sys
    import json

    from nomad.client import LocalEntryProcessing

    print(f'Using {config.client.url}')
    auth = _create_auth(ctx)

    with LocalEntryProcessing(entry_id, auth=auth, **kwargs) as local:
        print(
            f'Data being saved to .volumes/fs/tmp/repro_{entry_id} if not already there'
        )
        entry_archives = local.parse(strict=not not_strict)

        for entry_archive in entry_archives:
            if not skip_normalizers:
                local.normalize_all(entry_archive=entry_archive)

            if show_archive:
                json.dump(entry_archive.m_to_dict(), sys.stdout, indent=2)

            if show_metadata:
                metadata = entry_archive.metadata
                metadata.apply_archive_metadata(entry_archive)
                json.dump(metadata.m_to_dict(), sys.stdout, indent=4)


@client.command(
    help='Synchronizes the NOMAD database with the given external database.'
)
@click.argument('db_name', nargs=1, required=True)
@click.argument('root_url', nargs=1, required=True)
@click.option(
    '--outfile', default=None, help='File to read/write files missing in NOMAD database'
)
@click.option(
    '--nomadfile', default=None, help='File to read/write files in NOMAD database'
)
@click.option(
    '--dbfile', default=None, help='File to read/write files in given database'
)
@click.option(
    '--local_path',
    default='/nomad/fairdi/external',
    help='Directory to which the files will be downloaded',
)
@click.option(
    '--parallel',
    default=2,
    help='Number of processes to spawn to download/upload files',
)
@click.option(
    '--do-download',
    is_flag=True,
    default=False,
    help='Flag to automatically download downloaded files',
)
@click.option(
    '--do-upload',
    is_flag=True,
    default=False,
    help='Flag to automatically upload downloaded files',
)
@click.option(
    '--do-publish',
    is_flag=True,
    default=False,
    help='Flag to automatically publish upload',
)
@click.option(
    '--cleanup', is_flag=True, default=False, help='Flag to clean up downloaded files'
)
@click.pass_context
def synchdb(ctx, **kwargs):
    from nomad.cli.aflow import DbUpdater

    db = DbUpdater(auth=_create_auth(ctx), **kwargs)
    db.update()
