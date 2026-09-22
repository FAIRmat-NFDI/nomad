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


from typing import Annotated

import requests
from fastapi import APIRouter, Depends, HTTPException

from nomad.auth.scopes import Scope
from nomad.config import config
from nomad.north import get_mounts, get_tools
from nomad.utils import get_logger

from ..models import HTTPExceptionModel, User
from ..models.north import Mount, ServerModel, StateEnum, ToolModel
from .auth import get_current_user

router = APIRouter()
logger = get_logger(__name__)

hub_api_headers = {
    'Authorization': f'Bearer {config.north.hub_service_api_token.get_secret_value()}'
}


async def check_tool(name: str) -> ToolModel:
    # Raise an error when tool is not defined
    tools = get_tools()
    if name not in tools:
        raise HTTPException(status_code=404, detail='The tool does not exist.')

    return tools[name]


@router.get(
    '/tools',
    summary='Get a list of all configured tools and their current state.',
)
async def tools() -> list[ToolModel]:
    return list(get_tools().values())


@router.get(
    '/tools/{name}',
    summary='Get a list of all configured tools and their current state.',
    responses={
        401: {'model': HTTPExceptionModel, 'description': 'Authorization error.'},
        404: {'model': HTTPExceptionModel, 'description': 'The tool does not exist.'},
    },
)
async def tool(
    tool: Annotated[ToolModel, Depends(check_tool)],
) -> ToolModel:
    return tool


@router.get(
    '/mounts/{name}',
    summary='Get all the  mounts for a specific user and a tool.',
    responses={
        401: {'model': HTTPExceptionModel, 'description': 'Authorization error.'},
        404: {'model': HTTPExceptionModel, 'description': 'The tool does not exist.'},
    },
)
def mounts(
    tool: Annotated[ToolModel, Depends(check_tool)],
    user: Annotated[
        User, Depends(get_current_user([Scope.NORTH_READ], allow_anonymous=False))
    ],
) -> list[Mount]:
    return get_mounts(user, tool)


@router.get(
    '/servers',
    summary='Get information for a specific tool.',
    responses={
        401: {'model': HTTPExceptionModel, 'description': 'Authorization error.'},
    },
)
def servers(
    user: Annotated[
        User, Depends(get_current_user([Scope.NORTH_READ], allow_anonymous=False))
    ],
) -> list[ServerModel]:
    url = f'{config.hub_url()}/api/users/{user.username}'
    response = requests.get(url, headers=hub_api_headers)

    if response.status_code == 404:
        # The user does not exist yet
        return []

    servers = []
    servers_response = response.json()['servers']
    for name in get_tools().keys():
        if name not in servers_response:
            server = ServerModel(name=name, state=StateEnum.stopped)
        else:
            server_info = servers_response[name]

            if 'upload_ids' in server_info.get('user_options', {}):
                upload_ids = server_info['user_options']['upload_ids']
            else:
                upload_ids = {}

            if server_info['ready']:
                server = ServerModel(
                    name=name, state=StateEnum.running, upload_ids=upload_ids
                )
            elif server_info['pending']:
                server = ServerModel(
                    name=name, state=StateEnum.starting, upload_ids=upload_ids
                )
            else:
                server = ServerModel(name=name, state=StateEnum.stopped)

        servers.append(server)

    return servers


@router.get(
    '/servers/{name}',
    summary='Get status information for a specific tool.',
    responses={
        401: {'model': HTTPExceptionModel, 'description': 'Authorization error.'},
        404: {'model': HTTPExceptionModel, 'description': 'The tool does not exist.'},
    },
)
def server(
    tool: Annotated[ToolModel, Depends(check_tool)],
    user: Annotated[
        User, Depends(get_current_user([Scope.NORTH_READ], allow_anonymous=False))
    ],
) -> ServerModel:
    # url = f'{config.hub_url()}/api/users/{user.username}/servers/{tool.name}'
    url = f'{config.hub_url()}/api/users/{user.username}'
    response = requests.get(url, headers=hub_api_headers)

    if response.status_code == 404:
        # The user does not exist yet
        return ServerModel(name=tool.name, state=StateEnum.stopped)
    elif tool.name not in response.json()['servers']:
        # The tool does not exist yet
        return ServerModel(name=tool.name, state=StateEnum.stopped)

    server = response.json()['servers'][tool.name]
    if 'upload_ids' in server.get('user_options', {}):
        upload_ids = server['user_options']['upload_ids']
    else:
        upload_ids = {}

    if server['ready']:
        return ServerModel(
            name=tool.name,
            state=StateEnum.running,
            upload_ids=server.get('upload_ids', upload_ids),
        )
    elif server['pending']:
        return ServerModel(
            name=tool.name,
            state=StateEnum.starting,
            upload_ids=server.get('upload_ids', upload_ids),
        )
    else:
        return ServerModel(name=tool.name, state=StateEnum.stopped)


@router.post(
    '/servers/{name}',
    summary='Start a tool.',
    responses={
        401: {'model': HTTPExceptionModel, 'description': 'Authorization error.'},
        404: {'model': HTTPExceptionModel, 'description': 'The tool does not exist.'},
    },
    response_model_exclude_unset=True,
    response_model_exclude_none=True,
)
def start(
    tool: Annotated[ToolModel, Depends(check_tool)],
    user: Annotated[
        User, Depends(get_current_user([Scope.NORTH_RUN], allow_anonymous=False))
    ],
):
    url = f'{config.hub_url()}/api/users/{user.username}'
    response = requests.get(url, headers=hub_api_headers)

    # Make sure the user exists
    if response.status_code == 404:
        response = requests.post(url, headers=hub_api_headers)
        logger.info('created north user', user_id=user.user_id)

    url = f'{config.hub_url()}/api/users/{user.username}/servers/{tool.name}'
    response = requests.post(url, headers=hub_api_headers)


@router.delete(
    '/servers/{name}',
    summary='Stop and remove a NORTH tool.',
    responses={
        401: {'model': HTTPExceptionModel, 'description': 'Authorization error.'},
        404: {'model': HTTPExceptionModel, 'description': 'The tool does not exist.'},
    },
)
def delete(
    tool: Annotated[ToolModel, Depends(check_tool)],
    user: Annotated[
        User, Depends(get_current_user([Scope.NORTH_RUN], allow_anonymous=False))
    ],
):
    url = f'{config.hub_url()}/api/users/{user.username}/servers/{tool.name}'
    response = requests.delete(url, json={'remove': True}, headers=hub_api_headers)

    if response.status_code == 404:
        raise HTTPException(
            status_code=404, detail='The tool does not exist or is already stopped.'
        )
