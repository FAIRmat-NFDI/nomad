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
API endpoint to receive telemetry data (in logstash format) from local installations.
"""

import socket
import zlib
from enum import Enum
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.routing import APIRouter

from nomad import utils
from nomad.app.v1.routers.auth import get_current_user
from nomad.auth.scopes import Scope
from nomad.config import config

from ..models import User

logger = utils.get_logger(__name__)

router = APIRouter()


class APITag(str, Enum):
    DEFAULT = 'federation'


@router.post(
    '/logs',
    tags=[APITag.DEFAULT],
    summary='Receive logs in logstash format from other Nomad installations and store into central logstash '
    'for further analysis.',
)
async def logs(
    request: Request,
    _user: Annotated[
        User,
        Depends(get_current_user([Scope.FEDERATION_WRITE])),
    ],
):
    content_encoding = request.headers.get('Content-Encoding')

    if content_encoding is not None and content_encoding not in ['gzip']:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"\"'Content-Encoding': '{content_encoding}'\" not supported",
        )

    content = await request.body()

    # * 2 to have some wiggle room if the oasis does not have the exact same configuration
    # it is still enough to protect against accidental/malicious large log transfers
    if len(content) > config.logtransfer.transfer_capacity * 2:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f'log size too large, max size is {config.logtransfer.transfer_capacity}',
        )

    elif content.startswith(b'{'):
        # even gzip compressed content might already be decompressed by proxy
        logs = content

    elif content_encoding is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'unsupported content type, content starts with {str(content[:5])}',
        )

    else:
        try:
            logs = zlib.decompress(content)
        except (OSError, zlib.error):
            import traceback

            traceback.print_exc()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail='decompressing gzip request failed',
            )

    # read IP address from header (typically set by nginx)
    try:
        # in case nginx forwards the request, then the original IP address (not the one from the nginx instance)
        # is contained in the header
        # TODO: Here we use the "traditional" X-Forwarded-For header. However, this header can not be trusted as it
        #  can easily be spoofed. NGINX promotes a "Forwarded" header (which needs additional configuration of Nginx)
        #   From https://www.nginx.com/resources/wiki/start/topics/examples/forwarded/
        #   > RFC 7239 standardizes a new Forwarded header to carry this information in a more organized way
        #   > The major benefit of Forwarded is extensibility. For example, with X-Forwarded-For, you don’t know which
        #   > IP address to trust without hardcoded rules such as “take the 2nd last IP address, but only if the
        #   > request comes from 10.0.0.0/8”. Whereas with Forwarded, your trusted front-end proxy could include a
        #   > secret token to identify itself.
        header = request.headers.get('X-Forwarded-For')
        if header is not None:
            ip_address = header.split(',')[0].strip()  # TODO: validate IP address?
        else:
            raise KeyError('No header "X-forwarded-For" found.')
    except KeyError:
        # read IP from request directly (note that this is not necessarily an IP address,
        # e.g. can also be string 'localhost'.
        if request.client is None:
            ip_address = 'unknown'
        else:
            ip_address = str(request.client.host)

    if not config.logstash.enabled:
        raise HTTPException(
            status_code=500,
            detail=f'this nomad installation cannot receive logs, logstash is not enabled',
        )

    logstash_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        logstash_socket.connect((config.logstash.host, int(config.logstash.tcp_port)))

        for log in logs.splitlines():
            log = log.strip()
            # sanity checks whether 'log' has a valid logstash format - final validation is performed by logstash itself
            if len(log) > 2 and log.endswith(b'}'):
                # augment IP address to end of log
                log = log[:-1] + f', "ip_address": "{ip_address}"}}\n'.encode()
                # forward log to central logstash
                # TODO: should check return whether it was successful?
                logstash_socket.send(log)
            else:
                pass  # drop log
    except Exception as e:
        logger.error('Error when submitting logs to logstash.', exc_info=e)
        raise HTTPException(
            status_code=500, detail='Could not process logs internally.'
        )
    finally:
        logstash_socket.close()

    return {'received_logs_size': len(logs)}
