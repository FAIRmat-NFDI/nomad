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

from enum import Enum

from pydantic import BaseModel, Field, SecretStr

from nomad.config.models.common import ConfigBaseModel, Options

_jupyterhub_config_description = """
    This setting is forwarded to jupyterhub; refer to the jupyterhub
    [documentation](https://jupyterhub.readthedocs.io/en/stable/api/app.html#).
"""


class NORTHToolMaintainer(BaseModel):
    name: str
    email: str


class ReadMode(str, Enum):
    ro = 'ro'
    rw = 'rw'


class NORTHExternalMount(BaseModel):
    host_path: str
    bind: str
    mode: ReadMode = ReadMode.ro


class NORTHTool(BaseModel):
    display_name: str | None = Field(
        None,
        description='A display name for the tool, e.g. shown in the NOMAD GUI.',
    )
    short_description: str | None = Field(
        None,
        description='A short description of the tool, e.g. shown in the NOMAD GUI.',
    )
    description: str | None = Field(
        None, description='A description of the tool, e.g. shown in the NOMAD GUI.'
    )
    image: str | None = Field(
        None, description='The docker image (incl. tags) to use for the tool.'
    )
    cmd: str | None = Field(
        None, description='The container cmd that is passed to the spawner.'
    )
    image_pull_policy: str = Field(
        'Always', description='The image pull policy used in k8s deployments.'
    )
    privileged: bool = Field(
        False, description='Whether the tool needs to run in privileged mode.'
    )
    seccomp_unconfined: bool = Field(
        False, description='Whether the tool runs with seccomp=unconfined.'
    )
    use_gpu: bool = Field(
        False,
        description='Whether the tool is provided access to available GPU resources.',
    )
    default_url: str | None = Field(
        None,
        description=(
            'An optional path prefix that is added to the container URL to '
            'reach the tool, e.g. "/lab" for jupyterlab.'
        ),
    )
    path_prefix: str | None = Field(
        None,
        description=(
            'An optional path prefix that is added to the container URL to '
            'reach the files, e.g. "lab/tree" for jupyterlab.'
        ),
    )
    with_path: bool = Field(
        False,
        description=(
            'Whether the path of the file or directory the tool was launched '
            'from is forwarded to the tool so it can open or deep-link to that '
            'item. When enabled, the launcher passes the item path to the tool: '
            'in the old hub it is appended to the tool URL via `path_prefix` '
            '(e.g. `lab/tree/<path>` for JupyterLab); in the new hub it is sent '
            'as `upload_id`/`path` query parameters for a handler inside the '
            'tool image to consume. Only meaningful for tools that can open a '
            'file from a URL (e.g. JupyterLab); set to False for tools that '
            'cannot, such as desktop (noVNC) apps like VESTA or FIJI. This does '
            'NOT control whether the tool is offered for a file in the NOMAD '
            'UI. That is determined solely by `file_extensions`.'
        ),
    )
    file_extensions: list[str] = Field(
        [],
        description='The file extensions of files that this tool should be launchable for.',
    )
    mount_path: str | None = Field(
        None,
        description=(
            'The path in the container where uploads and work directories will be mounted, '
            'e.g. /home/jovyan for Jupyter containers.'
        ),
    )
    icon: str | None = Field(
        None,
        description='A URL to an icon that is used to represent the tool in the NOMAD UI.',
    )
    maintainer: list[NORTHToolMaintainer] = Field(
        [], description='The maintainers of the tool.'
    )
    external_mounts: list[NORTHExternalMount] = Field(
        [], description='Additional mounts to be added to tool containers.'
    )


class NORTHTools(Options):
    options: dict[str, NORTHTool] = Field(dict(), description='The available plugin.')


class NORTH(ConfigBaseModel):
    """
    Settings related to the operation of the NOMAD remote tools hub service *north*.
    """

    enabled: bool | None = Field(
        True,
        description="""
        Enables or disables the NORTH API and UI views. This is independent of
        whether you run a jupyter hub or not.
    """,
    )
    enable_new_hub_api: bool = Field(
        False,
        description="""
        Feature flag to enable a new NORTH API
        """,
    )
    hub_connect_ip: str | None = Field(
        None,
        description="""
        Overwrites the default hostname that can be used from within a north container
        to reach the host system.

        Typically has to be set for non Linux hosts. Set this to `host.docker.internal`
        on windows/macos.
    """,
    )
    hub_connect_url: str | None = Field(
        None, description=_jupyterhub_config_description
    )
    hub_ip: str = Field('0.0.0.0', description=_jupyterhub_config_description)
    docker_network: str | None = Field(None, description=_jupyterhub_config_description)
    hub_host: str | None = Field(
        'localhost',
        description="""
        The internal host name that NOMAD services use to connect to the jupyterhub API.
    """,
    )
    hub_port: int | str = Field(
        9000,
        description="""
        The internal port that NOMAD services use to connect to the jupyterhub API.
    """,
    )
    jupyterhub_crypt_key: SecretStr | None = Field(
        None, exclude=True, description=_jupyterhub_config_description
    )

    nomad_host: str | None = Field(
        None, description='The NOMAD app host name that spawned containers use.'
    )
    windows: bool = Field(True, description='Enable windows OS hacks.')
    nomad_access_token_expiry_time: int = Field(
        24 * 3600,
        description=(
            'All tools are run with an access token for the NOMAD api in the NOMAD_CLIENT_ACCESS_TOKEN '
            'environment variable. This token will be automatically used by the nomad-lab Python package, '
            'e.g. if you use the ArchiveQuery to access data. '
            'This option sets the amount of seconds that this token is valid for.'
        ),
    )

    tools: NORTHTools = Field(
        NORTHTools(),
        description="""
            The available north tools. Primarily you should install NORTH tools via
            plugins from v1.4.1 onwards, but for backwards-compatibility you can also
            define them here. Note that any tools included through this configuration will
            be added and included in the `plugins.entry_points` configuration.
        """,
    )

    hub_service_api_token: SecretStr = Field(
        SecretStr('secret-token'),
        exclude=True,
        description="""
        A secret token shared between NOMAD and the NORTH jupyterhub.
        This needs to be the token of an admin service.""",
    )
