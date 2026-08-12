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
Command line interface (CLI) for nomad. Provides a group/sub-command structure, think git,
that offers various functionality to the command line user.

Use it from the command line with ``nomad --help`` or ``python -m nomad.cli --help`` to learn
more.
"""

from .config_files import apply_config_files_from_argv

apply_config_files_from_argv()

from . import dev, parse, client, admin, clean  # noqa: E402
from .cli import run_cli, cli  # noqa: E402
