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

from enum import StrEnum

from pydantic import BaseModel

from nomad.config.models.north import NORTHTool


class ToolModel(NORTHTool):
    name: str


class StateEnum(StrEnum):
    running = 'running'
    starting = 'starting'
    stopping = 'stopping'
    stopped = 'stopped'


class ServerModel(BaseModel):
    name: str
    state: StateEnum | None = None
    upload_ids: dict[str, str] | None = None
    # url: str | None = None


class ReadMode(StrEnum):
    ro = 'ro'
    rw = 'rw'


class Kind(StrEnum):
    """Type of the mount. Different spawners may support different types of mounts.
    - bind: in case of Dockerspawner this equivalent to bind mounts. In case of KubeSpawner this equivalent to hostpath mounts.
    - volume: reuse or create a new volume.
    """

    bind = 'bind'
    volume = 'volume'


class Mount(BaseModel):
    source: str
    target: str
    kind: Kind = Kind.bind
    mode: ReadMode = ReadMode.ro
    upload_id: str | None = None


class MountedUpload(BaseModel):
    upload_id: str
    mount_dir: str | None = None
    is_mounted: bool | None = None
