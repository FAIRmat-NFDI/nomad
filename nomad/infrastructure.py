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
This module provides function to establish connections to the database, searchengine, etc.
infrastructure services. Usually everything is setup at once with :func:`setup`. This
is run once for each *api* and *worker* process. Individual functions for partial setups
exist to facilitate testing, aspects of :py:mod:`nomad.cli`, etc.
"""

import asyncio
import os
import shutil
import smtplib
import warnings  # TODO put somemore thought into warnings
from email.mime.text import MIMEText
from email.utils import make_msgid

from mongoengine import connect, disconnect
from mongoengine.connection import ConnectionFailure

from nomad import utils
from nomad.config import config
from nomad.elastic_compat import create_elastic_client

# The metainfo is defined and used during imports. This is problematic.
# We import all parsers very early in the infrastructure setup. This will populate
# the metainfo with parser specific definitions, before the metainfo might be used.
from nomad.parsing import parsers  # noqa: F401
from nomad.utils.structlogging import get_logger

warnings.filterwarnings('ignore')

logger = get_logger(__name__)

# The elastic search client
elastic_client = None

# The pymongo mongodb client
mongo_client = None


def setup():
    """
    Uses the current configuration (nomad/config.py and environment) to setup all the
    infrastructure services (repository db, mongo, elastic search) and logging.
    Will create client instances for the databases and has to be called before they
    can be used.
    """
    from nomad.tracing import setup_tracing

    setup_tracing()
    setup_files()
    setup_mongo()
    # index_builtin_packages()
    check_mongo()
    setup_elastic()


def setup_files():
    for directory in [config.fs.public, config.fs.staging, config.fs.tmp]:
        if not os.path.exists(directory):
            os.makedirs(directory)


def setup_mongo():
    """Creates connection to mongodb."""
    global mongo_client
    kwargs = dict(
        db=config.mongo.db_name, host=config.mongo.host, port=config.mongo.port
    )
    if config.mongo.username and config.mongo.password:
        kwargs.update(username=config.mongo.username, password=config.mongo.password)

    try:
        mongo_client = connect(**kwargs)
    except ConnectionFailure:
        disconnect()
        mongo_client = connect(**kwargs)

    logger.info('setup mongo connection')

    from nomad.mongo.cache import SERVER_STATS_CACHE_KEY, MongoCache
    from nomad.mongo.groups import MongoUserGroup
    from nomad.mongo.notifications import Notification
    from nomad.processing import Entry, Upload

    # Drop FastAPI cache entries on startup, but keep the server-stats snapshot.
    db = mongo_client.get_database(config.mongo.db_name)
    db.get_collection('cache').delete_many({'key': {'$ne': SERVER_STATS_CACHE_KEY}})
    MongoCache.ensure_indexes()
    MongoUserGroup.ensure_indexes()
    Notification.ensure_indexes()
    Upload.ensure_indexes()
    Entry.ensure_indexes()

    return mongo_client


# The async pymongo client (used by Beanie for actions)
async_mongo_client = None
async_mongo_loop: asyncio.AbstractEventLoop | None = None


async def init_async_mongo():
    """Initialize Motor + Beanie for async MongoDB access to the action_document collection."""
    global async_mongo_client, async_mongo_loop
    from beanie import init_beanie
    from pymongo import AsyncMongoClient

    from nomad.mongo.action import ActionDocument

    if async_mongo_client is not None:
        return async_mongo_client

    kwargs = dict(host=config.mongo.host, port=config.mongo.port)
    if config.mongo.username and config.mongo.password:
        kwargs.update(username=config.mongo.username, password=config.mongo.password)

    async_mongo_client = AsyncMongoClient(**kwargs, maxPoolSize=50, minPoolSize=50)
    async_mongo_loop = asyncio.get_running_loop()
    # Force connection pool initialization
    await async_mongo_client.admin.command('ping')
    await init_beanie(
        database=async_mongo_client[config.mongo.db_name],
        document_models=[ActionDocument],
    )
    logger.info('setup async mongo connection (Beanie)')
    return async_mongo_client


def index_builtin_packages():
    from nomad.datamodel import all_metainfo_packages
    from nomad.datamodel.context import populate_builtin_packages
    from nomad.metainfo import Package
    from nomad.mongo.package import PackageDefinition

    all_metainfo_packages(False)

    populate_builtin_packages()

    for package in Package.registry.values():
        PackageDefinition.create_new(package, overwrite_existing=False)


def check_mongo():
    db = mongo_client.get_database(config.mongo.db_name)
    names = set(db.list_collection_names())

    expected_names = {
        'action_document',
        'archive',
        'cache',
        'dataset',
        'd_o_i',  # auto-named from class DOI
        'entry',
        'package_definition',
        'notifications',
        'ownership_transfer',
        'upload',
        'user_group',
        'personal_access_tokens',
    }
    if not expected_names.issuperset(names):
        logger.warning(
            f'Expected MongoDB collections: {sorted(expected_names)}; '
            f'but found: {sorted(names)}'
        )

    # regression https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/issues/2281
    if 'mongo_user_group' in names:
        logger.warning("""
            The collection 'mongo_user_group' was found in MongoDB. It should be merged
            into 'user_group'. This is a known issue. Please ask the NOMAD team
            (support@nomad-lab.eu) if you need help with this.
        """)


def setup_elastic():
    """Creates connection to elastic search."""
    global elastic_client
    elastic_client = create_elastic_client()
    logger.info('setup elastic connection')
    from nomad.metainfo.elasticsearch_extension import (
        create_indices as create_v1_indices,
    )

    create_v1_indices()
    logger.info('initialized v1 elastic indices')

    return elastic_client


def reset(remove: bool) -> None:
    """
    Resets the databases mongo, elastic/entries, and all files. Be careful.
    In contrast to :func:`remove`, it will only remove the contents of dbs and indices.
    This function just attempts to remove everything, there is no exception handling
    or any warranty it will succeed.

    Args:
        remove: Do not try to recreate empty databases, remove entirely.
    """
    try:
        if not mongo_client:
            setup_mongo()
        mongo_client.drop_database(config.mongo.db_name)
        logger.info('mongodb resetted')
    except Exception as e:
        logger.error('exception reset mongodb', exc_info=e)

    try:
        from nomad.metainfo.elasticsearch_extension import (
            create_indices,
            delete_indices,
        )

        if not elastic_client:
            setup_elastic()

        delete_indices()

        if not remove:
            create_indices()

        logger.info('elastic index resetted')
    except Exception as e:
        logger.error('exception resetting elastic', exc_info=e)

    try:
        shutil.rmtree(config.fs.staging, ignore_errors=True)
        shutil.rmtree(config.fs.public, ignore_errors=True)

        # delete tmp without the folder
        if os.path.isdir(config.fs.tmp):
            for sub_path in os.listdir(config.fs.tmp):
                path = os.path.join(config.fs.tmp, sub_path)
                try:
                    if os.path.isfile(path):
                        os.unlink(path)
                    elif os.path.isdir(path):
                        shutil.rmtree(path, ignore_errors=True)
                except Exception:
                    pass

        logger.info('files resetted')
    except Exception as e:
        logger.error('exception deleting files', exc_info=e)


def send_mail(name: str, email: str, message: str, subject: str) -> None:
    """Used to programmatically send mails.

    Args:
        name: The email recipient name.
        email: The email recipient address.
        message: The email body.
        subject: The subject line.
    """
    if not config.mail.enabled:
        return

    logger = utils.get_logger(__name__)
    server = smtplib.SMTP(config.mail.host, config.mail.port)

    if config.mail.port == 995:
        try:
            server.starttls()
        except Exception as e:
            logger.warning('Could not use TTS', exc_info=e)

    if config.mail.with_login:
        try:
            server.login(config.mail.user, config.mail.password)
        except Exception as e:
            logger.warning('Could not log into mail server', exc_info=e)
    msg = MIMEText(message)
    msg['Subject'] = subject
    msg['To'] = name
    msg['From'] = config.mail.from_address
    msg['Message-ID'] = make_msgid()
    to_addrs = [email]
    to_addrs = [email]

    if config.mail.cc_address is not None:
        msg['Cc'] = f'The nomad team <{config.mail.cc_address}>'
        to_addrs.append(config.mail.cc_address)

    try:
        server.send_message(msg, from_addr=config.mail.from_address, to_addrs=to_addrs)
    except Exception as e:
        logger.error('Could not send email', exc_info=e)

    server.quit()
