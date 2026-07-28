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
import time
import zlib

import requests

from nomad import utils
from nomad.config import config

logger = utils.get_logger(__name__)


def transfer_logs():
    """Transfers previously stored logs to the central nomad deployment."""
    if not config.logtransfer.enabled:
        return

    log_file = os.path.join(config.fs.tmp, config.logtransfer.log_file)
    transfer_file = os.path.join(config.fs.tmp, config.logtransfer.transfer_log_file)

    if not os.path.exists(log_file):
        # no logs yet or no more logs after transfer/rollover
        return

    try:
        os.rename(
            log_file,
            transfer_file,
        )
        time.sleep(config.logtransfer.file_rollover_wait_time)
        file_size = os.path.getsize(transfer_file)

    except Exception as e:
        logger.error(
            'could not rollover logs for transfer',
            exc_info=e,
        )
        return False

    try:
        if file_size < config.logtransfer.transfer_threshold:
            return True

        if file_size > config.logtransfer.transfer_capacity:
            with open(transfer_file, 'rb') as f:
                logs = f.read(config.logtransfer.transfer_capacity)
                logs = logs.rsplit(b'\n', 1)[0]
            logger.warn('logs exceed transfer capacity', size=file_size)
        else:
            with open(transfer_file, 'rb') as f:
                logs = f.read()
    except Exception as e:
        logger.error(
            'could not read logs for transfer',
            exc_info=e,
        )
        return False

    url = f'{config.oasis.central_nomad_deployment_url}/v1/federation/logs'
    try:
        headers = {'Content-Encoding': 'gzip'}

        response = requests.post(
            url,
            headers=headers,
            data=zlib.compress(logs),
        )

        if response.status_code < 300:
            submitted_bytes = response.json()['received_logs_size']
            logger.debug('logs transferred', size=submitted_bytes, url=response.url)
        else:
            logger.error(
                'could not transfer logs',
                status_code=response.status_code,
                data=dict(response_text=response.text),
                url=url,
            )
            return False

    except Exception as e:
        logger.error('could not transfer logs', url=url, exc_info=e)
        return False

    return True
