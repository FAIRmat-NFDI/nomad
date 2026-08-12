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

import os
import sys

CONFIG_FILE_OPTIONS = ('-f', '--config-file')

# Environment variable through which the files are handed over to `nomad.config`. Kept
# in sync with `nomad.config.CONFIG_ENV`, which cannot be imported here because this
# module has to run before `nomad.config`.
CONFIG_ENV = 'NOMAD_CONFIG'

_VALUE_OPTIONS = CONFIG_FILE_OPTIONS + ('--log-label',)


def _accepts_config_file(command_path: tuple[str, ...]) -> bool:
    """
    Whether a ``-f`` at this point of the command path is a config file option. Other
    sub-commands have their own ``-f`` with a different meaning (e.g.
    ``admin uploads convert-archives -f`` is ``--force-repack``), so only the top level
    group and the ``admin run ...`` commands are scanned. Keep this in sync with the
    commands that carry ``nomad.cli.cli.config_file_option``.
    """
    return command_path in ((), ('admin',), ('dev',)) or command_path[:2] in (
        ('admin', 'run'),
        ('dev', 'config'),
    )


def parse_config_files(argv: list[str]) -> list[str]:
    """
    Extracts the values of the ``-f/--config-file`` options from raw command line
    arguments, in the order in which they were given. They can be given at any point of
    the command line that accepts them, e.g. `nomad -f <file> admin run appworker` and
    `nomad admin run appworker -f <file>` are equivalent.
    """
    files: list[str] = []
    command_path: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == '--':
            break
        if not arg.startswith('-') or arg == '-':
            # A sub-command name, or the value of an option that we do not know.
            command_path.append(arg)
            if not _accepts_config_file(tuple(command_path)):
                break
            index += 1
            continue
        if arg in CONFIG_FILE_OPTIONS:
            if index + 1 < len(argv):
                files.append(argv[index + 1])
            index += 2
            continue
        if arg.startswith('--config-file='):
            files.append(arg.split('=', 1)[1])
        elif arg.startswith('-f') and not arg.startswith('--') and len(arg) > 2:
            # Attached value, e.g. `-fnomad.yaml`.
            files.append(arg[2:])
        elif arg in _VALUE_OPTIONS:
            index += 1
        index += 1

    return files


def apply_config_files_from_argv(argv: list[str] | None = None) -> list[str]:
    """
    Applies the config files given on the command line by putting them into
    ``NOMAD_CONFIG``, where :func:`nomad.config._resolve_config_files` picks them up.
    Like ``docker compose -f`` overriding ``COMPOSE_FILE``, they replace whatever
    ``NOMAD_CONFIG`` named before, they are not added to it.

    Has to be called before ``nomad.config`` is imported. Returns the files that were
    applied.
    """
    files = parse_config_files(sys.argv[1:] if argv is None else argv)
    if files:
        os.environ[CONFIG_ENV] = os.pathsep.join(files)

    return files
