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
Configuration for JuypterHUB. We use JuypterHUB to run remote tools, like jupyter, on
NOMAD servers.
"""

from dockerspawner.dockerspawner import DockerSpawner
from oauthenticator.generic import GenericOAuthenticator

from nomad.config import config

c = get_config()  # type: ignore  # pylint: disable=undefined-variable


def pre_spawn(spawner):
    if spawner.handler.current_user.name != 'nomad-service':
        # Do nothing, will only launch the default image with no volumes.

        # Only the nomad-service can launch specialized tools with mounted volumes
        if spawner.name:
            spawner.log.error(
                f'The {spawner.name} server is not allowed to start this way, raise an error'
            )
            raise NotImplementedError(
                'Only the nomad-service can launch specialized tools.'
            )

        return

    user_home = spawner.user_options.get('user_home')
    if user_home:
        spawner.volumes[user_home['host_path']] = {
            'mode': 'rw',
            'bind': user_home['mount_path'],
        }

    uploads = spawner.user_options.get('uploads', [])
    for upload in uploads:
        spawner.volumes[upload['host_path']] = {
            'mode': 'rw',
            'bind': upload['mount_path'],
        }

    external_mounts = spawner.user_options.get('external_mounts', [])
    for external_mount in external_mounts:
        spawner.volumes[external_mount['host_path']] = {
            'bind': external_mount['bind'],
            'mode': external_mount['mode'],
        }

    environment = spawner.user_options.get('environment', {})
    spawner.environment.update(environment)

    tool = spawner.user_options.get('tool')
    if tool:
        spawner.image = tool.get('image')
        spawner.cmd = tool.get('cmd')

        # Workaround to have specific default_url for specific containers without using profiles
        if tool.get('default_url'):
            spawner.default_url = tool.get('default_url')

        extra_host_config = {}
        if tool.get('privileged'):
            extra_host_config['privileged'] = True

        if tool.get('seccomp_unconfined'):
            extra_host_config['security_opt'] = ['seccomp=unconfined']

        if tool.get('use_gpu'):
            import os

            import docker

            # Check if any GPUs are available
            nvidia_version_path = '/proc/driver/nvidia/version'
            if os.path.exists(nvidia_version_path):
                with open(nvidia_version_path) as file:
                    version_info = file.read()
                spawner.log.info(
                    f'Enabling nvidia driver with driver info:\n{version_info}.'
                )
                extra_host_config['device_requests'] = [
                    docker.types.DeviceRequest(  # type: ignore
                        count=-1,
                        capabilities=[['gpu']],
                    ),
                ]
            else:
                spawner.log.warning(
                    'GPU requested but no nvidia driver found on host. Disabling GPU usage for this container.'
                )
                tool['use_gpu'] = False

        if extra_host_config:
            spawner.extra_host_config.update(extra_host_config)


c.Spawner.pre_spawn_hook = pre_spawn

# configure nomad service
c.JupyterHub.services.append(
    {
        'name': 'nomad-service',
        'admin': True,
        'api_token': config.north.hub_service_api_token.get_secret_value(),
    }
)

# Allow named single-user servers per user (Default: False)
c.JupyterHub.allow_named_servers = True

# If named servers are enabled, default name of server to spawn or open, e.g. by
# user-redirect. (Default: '')
c.JupyterHub.default_server_name = 'jupyter'


# The public facing URL of the whole JupyterHub application. This is the address on which
# the proxy will bind. (Default: 'http://:8000')
c.JupyterHub.bind_url = f'http://:9000{config.services.join_path("north")}'

# configure authenticator
nomad_public_keycloak = f'{config.keycloak.public_server_url.rstrip("/")}/realms/{config.keycloak.realm_name}'
nomad_keycloak = f'{config.keycloak.server_url}/realms/{config.keycloak.realm_name}'
c.JupyterHub.authenticator_class = GenericOAuthenticator
c.GenericOAuthenticator.login_service = 'keycloak'
c.GenericOAuthenticator.client_id = config.keycloak.client_id
c.GenericOAuthenticator.authorize_url = (
    f'{nomad_public_keycloak}/protocol/openid-connect/auth'
)
c.GenericOAuthenticator.token_url = f'{nomad_keycloak}/protocol/openid-connect/token'
c.GenericOAuthenticator.userdata_url = (
    f'{nomad_keycloak}/protocol/openid-connect/userinfo'
)
c.GenericOAuthenticator.userdata_params = {'state': 'state'}
c.GenericOAuthenticator.username_key = 'preferred_username'
c.GenericOAuthenticator.scope = ['openid', 'profile']
c.Authenticator.auto_login = True
c.Authenticator.enable_auth_state = True
c.Authenticator.allow_all = True


# configure docker spawner
class DockerSpawnerWithWindowsFixes(DockerSpawner):
    def _docker(self, method, *args, **kwargs):
        tries = 0
        max_tries = 1
        if method == 'port':
            max_tries = 3
        while tries < max_tries:
            result = super()._docker(method, *args, **kwargs)
            if result is not None:
                break
            import time

            time.sleep(3)
            tries += 1

        return result


c.JupyterHub.spawner_class = (
    DockerSpawner if not config.north.windows else DockerSpawnerWithWindowsFixes
)
c.DockerSpawner.image = 'jupyter/datascience-notebook'
c.DockerSpawner.remove = True
# Prefix for container names. See name_template for full container name for a particular
# user's server. (Default: 'jupyter')
c.DockerSpawner.prefix = 'nomad_oasis_north'

if config.north.docker_network:
    c.DockerSpawner.network_name = config.north.docker_network

if config.north.hub_ip:
    c.JupyterHub.hub_ip = config.north.hub_ip

if config.north.hub_connect_ip:
    c.JupyterHub.hub_connect_ip = config.north.hub_connect_ip

if config.north.hub_connect_url:
    c.DockerSpawner.hub_connect_url = config.north.hub_connect_url

# Fixing: Unexpected error: "Gateway Time-out (504)". Please try again and let us know, if this error keeps happening.
c.DockerSpawner.http_timeout = 5 * 60  # in seconds
c.DockerSpawner.start_timeout = 10 * 60  # in seconds
