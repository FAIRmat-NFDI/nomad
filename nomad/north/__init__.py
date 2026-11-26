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
from functools import cache

from mongoengine.queryset.visitor import Q
from nomad.config import config
from nomad.config.models.plugins import NorthToolEntryPoint
from nomad.datamodel.data import User
from nomad.mongo.groups import MongoUserGroup
from nomad.processing import Upload

from ..app.v1.models.north import Mount, ReadMode, ToolModel


@cache
def get_tools() -> dict[str, ToolModel]:
    """Retrieve all available North tools from configured plugins.

    This function scans the plugin entry points for tools of type 'north_tool',
    filters them to ensure they are NorthToolEntryPoint instances, and creates
    ToolModel objects for each valid tool.

    Returns:
        A dictionary mapping tool names (as URL-safe IDs) to ToolModel instances.
    """
    tools = {}
    for plugin in config.plugins.entry_points.filtered_values():
        if plugin.entry_point_type == 'north_tool':
            if isinstance(plugin, NorthToolEntryPoint):
                name = plugin.id_url_safe
                tools[name] = ToolModel(name=name, **plugin.north_tool.model_dump())
    return tools


def get_upload_dir_name(upload: Upload) -> str:
    """Return the directory, relative to the tool's `mount_path`, where the
    project (upload) is mounted.

    The name only depends on the project id, so that clients (e.g. the NOMAD UI)
    can build the path to a project, folder or file in the tool without asking
    the API, and the directory does not move when the project is renamed. Keep
    this in sync with the NOMAD UI (nomad-gui `NorthToolsProvider`).
    """
    return f'projects/{upload.upload_id}'


def get_mounts(user: User, tool: ToolModel) -> list[Mount]:
    """Get all the mounts for a specific user and a tool.

    There are 3 different kind of mounts:
    - uploads: directories containing raw upload data for uploads the user has access to
    - user specific "work" directory: a writable directory for the user's work
    - tool specific "external" directories: additional directories specified by the tool

    Args:
        user: The user for whom to get mounts
        tool: The tool configuration defining mount paths and external mounts

    Returns:
        A list of Mount objects representing the directories to mount
    """

    mounts: list[Mount] = []

    if user.username in config.fs.north_home_user_folder_map.keys():
        user_home = config.fs.north_home_user_folder_map[user.username]
    else:
        user_home = user.user_id

    # Make sure that the home folder of the user exists
    user_home_dir = os.path.join(config.fs.north_home, user_home)
    if not os.path.exists(user_home_dir):
        os.makedirs(user_home_dir)

    mounts.append(
        Mount(
            source=os.path.join(config.fs.north_home_external, user_home),
            target=os.path.join(tool.mount_path, 'work'),
            mode=ReadMode.rw,
        )
    )

    user_id = str(user.user_id)
    group_ids = MongoUserGroup.get_ids_by_user_id(user_id, include_all=False)

    upload_query = (
        Q(main_author=user_id) | Q(coauthors=user_id) | Q(coauthor_groups__in=group_ids)
    ) & Q(publish_time=None)

    for upload in Upload.objects.filter(upload_query):
        mounts.append(
            Mount(
                source=os.path.join(upload.upload_files.external_os_path, 'raw'),
                target=os.path.join(tool.mount_path, get_upload_dir_name(upload)),
                mode=ReadMode.rw,
                upload_id=upload.upload_id,
            )
        )

    for ext_mount in tool.external_mounts:
        mounts.append(
            Mount(
                source=ext_mount.host_path,
                target=os.path.join(tool.mount_path, ext_mount.bind),
                mode=ReadMode(ext_mount.mode),
            )
        )

    return mounts
