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

import logging
import os

import click

from nomad import utils
from nomad.config import config


class POPO(dict):
    """
    A dict subclass that uses attributes as key/value pairs.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def __getattr__(self, name):
        if name in self:
            return self[name]
        else:
            raise AttributeError('No such attribute: ' + name)

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        if name in self:
            del self[name]
        else:
            raise AttributeError('No such attribute: ' + name)


def config_file_option(command):
    """
    Adds the `-f/--config-file` option to a command. The value is not used here: the
    files are already applied by `nomad.cli.config_files` before any nomad module is
    imported. This declaration documents the option in `--help` and validates that the
    given files exist. Commands carrying it must be accepted by
    `nomad.cli.config_files._accepts_config_file`.
    """
    return click.option(
        '-f',
        '--config-file',
        type=click.Path(exists=True, dir_okay=False),
        multiple=True,
        expose_value=False,
        help=(
            'Specify one or more NOMAD config yaml files. Can be repeated, later '
            'files overwrite earlier ones.'
        ),
    )(command)


@click.group(
    help=(
        "This is the entry point to nomad's command line interface CLI. "
        'It uses a sub-command structure similar to the git command.'
    )
)
@click.option('-v', '--verbose', help='sets log level to info', is_flag=True)
@click.option('--debug', help='sets log level to debug', is_flag=True)
@click.option('--log-label', type=str, help='Label applied to logg entries.')
@config_file_option
@click.pass_context
def cli(ctx, verbose: bool, debug: bool, log_label: str):
    config.meta.service = os.environ.get('NOMAD_SERVICE', 'cli')
    config.meta.label = log_label

    if debug:
        config.services.console_log_level = logging.DEBUG
    elif verbose:
        config.services.console_log_level = logging.INFO
    else:
        config.services.console_log_level = logging.WARNING
    utils.set_console_log_level(config.services.console_log_level)


def run_cli():
    try:
        return cli(obj=POPO())  # pylint: disable=E1120,E1123
    except ImportError:
        import sys

        if next(arg for arg in sys.argv if arg == '-v') is not None:
            import traceback

            traceback.print_exc()

        print(
            'You are accessing functionality that requires extra dependencies.\n'
            'Check the NOMAD documentation or install all extra dependencies:\n'
            '  pip install nomad-lab[infrastructure,dev]',
            file=sys.stderr,
        )

        sys.exit(1)
