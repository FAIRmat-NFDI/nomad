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

import json
import logging
import os
import threading
import warnings
from enum import Enum
from importlib.metadata import entry_points, version
from typing import Any, Literal
from urllib.parse import quote

from fsspec import AbstractFileSystem, filesystem
from fsspec.implementations.local import LocalFileSystem
from msglc.config import config as msglc_config
from msglc.config import configure
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)

from nomad.auth.scopes import _resolve_scopes
from nomad.common import is_email
from nomad.config.models.pagination import PaginationBaseModel

from .common import ConfigBaseModel, Options, OptionsGlob
from .north import NORTH
from .plugins import EntryPoints, EntryPointType, PluginPackage, Plugins
from .ui import UI

logger = logging.getLogger(__name__)


_DEFAULT_API_KEY = 'default-api-secret-that-is-long-enough'


class ModeEnum(str, Enum):
    PRODUCTION = 'production'
    DEVELOPMENT = 'development'


try:
    __version__ = version('nomad-lab')
except Exception:  # noqa
    # package is not installed
    pass

warnings.filterwarnings('ignore', message='numpy.dtype size changed')
warnings.filterwarnings('ignore', message='numpy.ufunc size changed')
warnings.filterwarnings('ignore', category=DeprecationWarning)


def normalize_loglevel(value):
    """Used to normalize log level with pydantic validators."""
    plain_value = value
    if plain_value is None:
        return logging.INFO
    else:
        try:
            return int(plain_value)
        except ValueError:
            return getattr(logging, plain_value)


class Services(ConfigBaseModel):
    """
    Contains basic configuration of the NOMAD services (app, worker, north).
    """

    api_host: str = Field(
        'localhost',
        description="""
        The external hostname that clients can use to reach this NOMAD installation.
    """,
    )
    api_port: str | int = Field(
        8000,
        description="""
        The port used to expose the NOMAD app and api to clients.
    """,
    )
    api_base_path: str = Field(
        '/nomad-oasis',
        description="""
        The base path prefix for the NOMAD app and api.
    """,
    )
    api_secret: SecretStr = Field(
        SecretStr(_DEFAULT_API_KEY),
        min_length=32,
        exclude=True,
        description="""
        A secret that is used to issue download and other tokens.
    """,
    )
    mode: ModeEnum = Field(
        ModeEnum.PRODUCTION,
        description="""
        The mode to run NOMAD in, defaults to production mode. Affects the security features and you should only run NOMAD in development mode if the instance is not publicly available.
    """,
    )
    api_timeout: int = Field(
        600,
        description="""
        If the NOMAD app is run with gunicorn as process manager, this timeout (in s) is passed
        and worker processes will be restarted, if they do not respond in time.
    """,
    )
    https: bool = Field(
        False,
        description="""
        Set to `True`, if external clients are using *SSL* to connect to this installation.
        Requires to setup a reverse-proxy (e.g. the one used in the docker-compose
        based installation) that handles the *SSL* encryption.
    """,
    )
    https_upload: bool = Field(
        False,
        description="""
        Set to `True`, if upload curl commands should suggest the use of SSL for file
        uploads. This can be configured independently of `https` to suggest large file
        via regular HTTP.
    """,
    )
    admin_user_id: str = Field(
        '00000000-0000-0000-0000-000000000000',
        description="""
        The admin user `user_id`. All users are treated the same; there are no
        particular authorization information attached to user accounts. However, the
        API will grant the user with the given `user_id` more rights, e.g. using the
        `admin` owner setting in accessing data.
    """,
    )

    encyclopedia_base: str = Field(
        'https://nomad-lab.eu/prod/rae/encyclopedia/#',
        description="""
            This enables links to the given *encyclopedia* installation in the UI.
        """,
    )
    optimade_enabled: bool = Field(
        True, description="""If true, the app will serve the optimade API."""
    )
    dcat_enabled: bool = Field(
        True, description="""If true the app will serve the DCAT API."""
    )
    h5grove_enabled: bool = Field(
        True, description="""If true the app will serve the h5grove API."""
    )
    dashboard_frame_ancestors: list[str] = Field(
        default_factory=lambda: ["'self'"],
        description="""
        CSP ``frame-ancestors`` sources permitted to embed dashboard plugin
        entry points in an iframe. Use ``'self'`` to only allow the NOMAD
        GUI; add explicit origins (e.g. ``https://example.com``) to allow
        other hosts. Applied to responses from
        ``{api_base_path}/dashboards/``.
        """,
    )

    console_log_level: int | str = Field(
        logging.WARNING,
        description="""
        The log level that controls console logging for all NOMAD services (app, worker, north).
        The level is given in Python `logging` log level numbers.
    """,
    )

    actions_log_level: int | str = Field(
        logging.INFO,
        description="""
        The log level that controls action logging for all NOMAD Actions.
        The level is given in Python `logging` log level numbers.
        Note: Users will be able to view these logs via the UI.
    """,
    )

    upload_limit: int = Field(
        10,
        description="""
        The maximum allowed unpublished uploads per user. If a user exceeds this
        amount, the user cannot add more uploads.
    """,
    )
    force_raw_file_decoding: bool = Field(
        False,
        description="""
        By default, text raw-files are interpreted with utf-8 encoding. If this fails,
        the actual encoding is guessed. With this setting, we force to assume iso-8859-1
        encoding, if a file is not decodable with utf-8.
    """,
    )
    max_entry_download: int = Field(
        50000,
        description="""
        There is an inherent limit in page-based pagination with Elasticsearch. If you
        increased this limit with your Elasticsearch, you can also adopt this setting
        accordingly, changing the maximum amount of entries that can be paginated with
        page-base pagination.

        Page-after-value-based pagination is independent and can be used without limitations.
    """,
    )
    max_entry_metadata_download: int = Field(
        100_000,
        description='The maximum amount of entries metadata that can be downloaded.',
    )
    unavailable_value: str = Field(
        'unavailable',
        description="""
        Value that is used in `results` section Enum fields (e.g. system type, spacegroup, etc.)
        to indicate that the value could not be determined.
    """,
    )
    app_token_max_expires_in: int = Field(
        30 * 24 * 60 * 60,
        description="""
        Maximum expiration time for an app token in seconds. Requests with a higher value
        will be declined.
    """,
    )
    html_resource_http_max_age: int = Field(
        60,
        description="""
        Used for the max_age cache-control directive on statically served html, js, css
        resources.
    """,
    )
    image_resource_http_max_age: int = Field(
        30 * 24 * 60 * 60,
        description="""
        Used for the max_age cache-control directive on statically served image
        resources.
    """,
    )
    upload_members_group_search_enabled: bool = Field(
        True,
        description='If true, the GUI will show a search for groups as upload members.',
    )
    log_api_queries: bool = Field(
        True,
        description='If true, all queries to the /entries/query API endpoint will be logged.',
    )

    collect_server_stats: bool = Field(
        True,
        description='If true, server statistics will be collected periodically.',
    )
    collect_server_stats_cron_expression: str = Field(
        '1 1 * * *',
        description='Cron expression for scheduling server stats collection.',
    )

    # Validators
    _console_log_level = field_validator('console_log_level', mode='before')(
        normalize_loglevel
    )
    _actions_log_level = field_validator('actions_log_level', mode='before')(
        normalize_loglevel
    )

    @field_validator('api_base_path', mode='before')
    @classmethod
    def normalize_api_base_path(cls, value: str) -> str:
        """Normalize the public deployment base path.

        The configured value remains a public URL path (and consequently uses ``/``
        for a root deployment).  ``route_prefix`` below is the corresponding value
        for composing routes, where the root is represented by an empty prefix.
        """
        if not isinstance(value, str):
            raise ValueError('services.api_base_path must be a string')

        value = value.strip()
        if value == '/':
            return '/'
        if not value:
            raise ValueError('services.api_base_path must not be empty')
        if not value.startswith('/'):
            raise ValueError('services.api_base_path must start with "/"')
        if '//' in value:
            raise ValueError('services.api_base_path must not contain empty segments')
        return value.rstrip('/')

    @property
    def route_prefix(self) -> str:
        """The deployment base path suitable for composing application routes.

        Root deployments use an empty prefix so that ``join_path('api', 'v1')``
        becomes ``/api/v1`` rather than ``//api/v1``.
        """
        if self.api_base_path == '/':
            return ''
        return self.api_base_path

    def join_path(self, *parts: str) -> str:
        """Join URL path components below this deployment's public base path."""
        segments = [part.strip('/') for part in parts if part and part.strip('/')]
        if not segments:
            return self.route_prefix or '/'
        return f'{self.route_prefix}/{"/".join(segments)}'

    def api_url(
        self,
        ssl: bool = True,
        api: str = 'api',
        api_host: str | None = None,
        api_port: int | None = None,
    ):
        """
        Returns the url of the current running nomad API. This is for server-side use.
        This is not the NOMAD url to use as a client, use `nomad.config.client.url` instead.
        """
        return f'{self.base_url(ssl, api_host, api_port)}{self.join_path(api)}'

    def base_url(
        self,
        ssl: bool = True,
        api_host: str | None = None,
        api_port: int | None = None,
    ) -> str:
        """Return the scheme and authority of this deployment, without its path."""
        if api_port is None:
            api_port = self.api_port  # type: ignore
        if api_host is None:
            api_host = self.api_host
        protocol = 'https' if self.https and ssl else 'http'
        host_and_port = api_host
        if api_port not in [80, 443]:
            host_and_port += ':' + str(api_port)
        return f'{protocol}://{host_and_port}'


def resolve_scopes_valid(scope_options):
    """Contains a concrete set of scopes for unauthenticated user as resolved from
    unauthenticated_user_scopes."""

    # If no include/exclude is given, return all scopes.
    if not scope_options:
        return _resolve_scopes('*:*')

    include = None
    exclude = None
    if isinstance(scope_options, OptionsGlob):
        include = scope_options.include
        exclude = scope_options.exclude
    elif isinstance(scope_options, dict):
        include = scope_options.get('include')
        exclude = scope_options.get('exclude')
    else:
        raise TypeError(
            "Auth scope configuration must be a mapping with 'include'/'exclude'."
        )

    # If no include is given, default to all scopes. This is different from an empty list.
    if include is None:
        include = {'*:*'}
    # If no exclude is given, default to empty set.
    if not exclude:
        exclude = {}

    return _resolve_scopes(include) - _resolve_scopes(exclude)


class Auth(ConfigBaseModel):
    """
    Authentication/authorization-related configurations.
    """

    require_authentication: bool = Field(
        False,
        description="""
            If True, all API requests require authentication (=users must be logged in).
            If false, unauthenticated users can still perform actions as defined by
            `unauthenticated_user_scopes`.
        """,
    )
    reject_unauthorized_users: bool = Field(
        True,
        description="""
            If True, any users that are not specified in `auth.authorized_users` are
            rejected access with an HTTP 403 response code. Multiple NOMAD deployments may
            share the same keycloak instance, and users can thus be authenticated
            correctly, without having authorization to access the deployment. If False,
            unauthorized users can still perform actions as defined by
            `unathorized_user_scopes`.
        """,
    )
    authorized_users: list[str] | None = Field(
        None,
        description="""
            A list of usernames that are authorized to access this
            NOMAD deployment. If not specified, all users recognized by the Keycloak
            instance are allowed.
        """,
    )

    @field_validator('authorized_users')
    @classmethod
    def normalize_authorized_users(cls, v: list[str] | None) -> list[str] | None:
        """Cast usernames to lowercase to avoid case-sensitivity issues."""
        if v is None:
            return None

        users = list(dict.fromkeys(user.lower().strip() for user in v))

        if any(is_email(user) for user in users):
            logger.warning(
                'whitelisting users with email is deprecated, please use username instead.'
            )

        return users

    unauthenticated_user_scopes: OptionsGlob = Field(
        OptionsGlob(include=['*:read']),
        description="""
            Controls the API scopes granted to unauthenticated (=not logged in) users.

            Semantics:
                - `include` defines the baseline granted scopes. Defaults to all scopes if
                  not defined or null.
                - `exclude` removes scopes from that baseline. Defaults to an empty set.
                - Wildcards are supported (e.g. `"*:read"` for read-only, `"*:*"` for all).

            For the complete list of scopes (resources/actions), refer to the
            `nomad.auth.scopes.Scope` Enum.
        """,
    )
    unauthorized_user_scopes: OptionsGlob = Field(
        OptionsGlob(include=['*:read']),
        description="""
            Controls the API scopes granted to unauthorized users (=users who can log in
            through Keycloak, but that are not allowed to access this NOMAD deployment).

            Semantics:
                - `include` defines the baseline granted scopes. Defaults to all scopes if
                  not defined or null.
                - `exclude` removes scopes from that baseline. Defaults to an empty set.
                - Wildcards are supported (e.g. `"*:read"` for read-only, `"*:*"` for all).

            For the complete list of scopes (resources/actions), refer to the
            `nomad.auth.scopes.Scope` Enum.
        """,
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unauthenticated_user_scopes_resolved(self) -> set[str]:
        """Contains a concrete set of scopes for unauthenticated user as resolved from
        unauthenticated_user_scopes."""
        return resolve_scopes_valid(self.unauthenticated_user_scopes)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unauthorized_user_scopes_resolved(self) -> set[str]:
        """Contains a concrete set of scopes for unauthorized users as resolved from
        unauthorized_user_scopes."""
        return resolve_scopes_valid(self.unauthorized_user_scopes)

    # Personal access token (PAT) related

    pat_pruning_time: float = Field(
        365,
        gt=0,
        description='Number of days to keep expired and revoked tokens before cleanup.',
    )
    pat_max_lifetime: float | None = Field(
        default=365,
        gt=0,
        description='Max token lifetime in days. None allows infinite lifetime.',
    )
    pat_max_active_per_user: int = Field(
        default=100,
        gt=1,
        description='Max number of active tokens (not expired/revoked) for each user.',
    )


class FooterLink(ConfigBaseModel):
    """
    A model for links to be displayed in the footer.
    """

    title: str = Field(description='The title of the link.')
    url: str = Field(description='The URL of the link.')


class Meta(ConfigBaseModel):
    """
    Metadata about the deployment and how it is presented to clients.
    """

    version: str = Field(__version__, description='The NOMAD version string.')
    commit: str = Field(
        '',
        description="The source-code commit that this installation's NOMAD version is build from.",
    )
    deployment: str = Field(
        'devel', description='Human-friendly name of this nomad deployment.'
    )
    deployment_url: str | None = Field(
        None,
        description="The NOMAD deployment's url. If not explicitly set, will default to the (api url) read from the configuration.",
    )
    label: str | None = Field(
        None,
        description="""
        An additional log-stash data key-value pair added to all logs. Can be used
        to differentiate deployments when analyzing logs.
    """,
    )
    service: str = Field(
        'unknown nomad service',
        description="""
        Name for the service that is added to all logs. Depending on how NOMAD is
        installed, services get a name (app, worker, north) automatically.
    """,
    )

    name: str = Field(
        'NOMAD', description='Web-site title for the NOMAD UI.', deprecated=True
    )
    description: str = Field(
        """This is the central NOMAD deployment hosted by
        [FAIRmat](https://fairmat-nfdi.eu/). This deployment hosts a wide range of
        research data with primary focus on condensed-matter physics and the chemical
        physics of solids. You can access all published data without an account. If you
        want to provide your own data, please log in or register for an account.""",
        description='Description of the NOMAD deployment. Shown at the GUI homepage.',
    )
    homepage: str = Field(
        'https://nomad-lab.eu', description='Provider homepage.', deprecated=True
    )
    source_url: str = Field(
        'https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR',
        description='URL of the NOMAD source-code repository.',
        deprecated=True,
    )

    maintainer_email: str = Field(
        'support@nomad-lab.eu',
        description='Email of the NOMAD deployment maintainer.',
    )
    beta: dict = Field(
        {},
        description="""
        Additional data that describes how the deployment is labeled as a beta-version in the UI.
    """,
    )
    footer_links: list[FooterLink] = Field(
        [], description='A list of links to be displayed in the footer.'
    )


class Oasis(ConfigBaseModel):
    """
    Settings related to the configuration of a NOMAD Oasis deployment.
    """

    is_oasis: bool = Field(
        False,
        description='Set to `True` to indicate that this deployment is a NOMAD Oasis.',
    )
    allowed_users: list[str] | None = Field(
        None,
        description="""Use `auth.authorized_users` instead.
        Previously it would also imply `require_authentication=True`,
        use `auth.require_authentication` instead.
        """,
        deprecated=True,
    )
    uses_central_user_management: bool = Field(
        False,
        description="""
        Set to True to use the central user-management. Typically the NOMAD backend is
        using the configured `keycloak` to access user data. With this, the backend will
        use the API of the central NOMAD (`central_nomad_deployment_url`) instead.
    """,
    )
    central_nomad_deployment_url: str = Field(
        'https://nomad-lab.eu/prod/v1/api',
        description="""
        The URL of the API of the NOMAD deployment that is considered the *central* NOMAD.
    """,
    )
    terms_of_service_url: str = Field(
        '',
        description="""
        The URL of the terms of service.
    """,
    )

    require_authentication: bool | None = Field(
        None,
        description='Use `auth.require_authentication` instead.',
        deprecated=True,
    )


class MetadataCache(ConfigBaseModel):
    """Per-node cache of published indexes and remote-ready markers."""

    enabled: Literal['auto'] | bool = Field(
        'auto',
        description="""Whether to cache parsed ZIP member/archive TOC indexes and
remote-ready marker validations.

``auto`` enables the cache when ``protocol == 's3'``. Set ``true`` to force it
on (for example in local tests) and ``false`` to disable it.""",
    )
    directory: str | None = Field(
        None,
        description="""Directory for cached ZIP/archive TOC index files and remote-ready
marker entries. When unset, entries are stored under
``<config.fs.local_tmp>/nomad-zip-index``. The
directory is shared by all Gunicorn workers on the node.""",
    )
    max_disk_mb: int = Field(
        1024,
        ge=0,
        description="""Maximum total size of each cached index/marker file kind on disk, in MiB.

Set to 0 to disable sweeping (unbounded growth).""",
    )
    memory_max_mb: int = Field(
        64,
        ge=0,
        description="""Maximum in-process cache of parsed indexes, in MiB,
per index kind (ZIP member index and archive TOC each have this budget).

Bounded by estimated live Python footprint, not item count. Set to 0 to
disable the memory cache (the on-disk cache is unchanged).""",
    )
    revalidate_seconds: int = Field(
        60,
        ge=0,
        description="""Seconds to trust a memory-cached identity or disk-cached ready marker
validation before remote metadata calls.

Within this TTL a warm index read performs no remote HEAD/info and a warm
remote-ready marker skips its GET and artifact HEADs. Set to 0 to validate on
every request.""",
    )

    def is_enabled(self, protocol: str | None) -> bool:
        if self.enabled == 'auto':
            return protocol == 's3'
        return bool(self.enabled)


# Connection+bucket pairs for which ``makedirs`` has already succeeded in this
# process. Keyed by protocol, bucket, and serialized ``extra`` (endpoint/credentials).
_ensured_target_fs: set[tuple[str, str, str]] = set()
_ensured_target_fs_lock = threading.Lock()


def reset_target_fs_state() -> None:
    """Drop process-level records of ensured remote buckets. Intended for tests."""
    with _ensured_target_fs_lock:
        _ensured_target_fs.clear()


class NOMADFileSystem(ConfigBaseModel):
    protocol: Literal['s3'] | None = Field(
        None,
        description="""
Protocol name of file system.
If assigned, files will be stored in the designated file system instead of the default local one.
The `fsspec` library will be used internally via `fsspec.filesystem(self.protocol, **self.extra)`.
Thus, `protocol` should be the protocol supported by `fsspec`, for example, `s3`.

Please note this is mainly designed to support Amazon S3 storage, but other remote file systems supported by `fsspec` should work as well.
""",
    )
    extra: dict = Field(
        default_factory=dict,
        description="""
Extra parameters that will be passed to `fsspec.filesystem`.
Different protocols require different configuration parameters.
See [documentation](https://filesystem-spec.readthedocs.io/en/latest/index.html) for more details.

For `s3`, the actual implementation is provided by `s3fs`.
See the corresponding [documentation](https://s3fs.readthedocs.io/en/latest/api.html#s3fs.core.S3FileSystem) for available parameters.

For local development, spin up a `seaweedfs` container using the following command.

```bash
docker run -p 8333:8333 chrislusf/seaweedfs
```

The S3 storage created via the above command accepts anonymous access.
The corresponding configuration will look like this:

```yaml
fs:
  public_fs:
    protocol: s3
    extra:
      anon: true
      endpoint_url: http://localhost:8333
```

For different S3 compatible object storage services, being either provided by cloud storage providers or self-hosted, configurations may vary.
Please consult the service provider for the specific configuration.
It is always possible to validate the connection by creating `s3fs.S3FileSystem` objects separately.
For example, for a default minimum local `seaweedfs` service, the following should not throw any exceptions.

```python
from s3fs import S3FileSystem

S3FileSystem(anon=True, endpoint_url="http://localhost:8333").mkdirs("example_bucket_name", exist_ok=True)
```
""",
    )
    bucket: str = Field(
        'public',
        description='Bucket name for remote files.',
    )
    simplify_path: bool = Field(
        False,
        description="""
The S3 storage does not have a traditional file path structure.
It only involves different buckets with different file IDs (which may include slashes such that they resemble file paths, but do not necessarily have to).

If enabled, the starting prefixes `self.staging` and `self.public` of file paths will be removed.
This is to make S3 storage tidier and avoid unnecessary nesting, making browsing S3 easier.

For instance, if the default local public storage path is `.volumes/fs/public`, a file that would normally be stored locally at

```text
.volumes/fs/public/ex/examples_template_985dc9d7/raw-public.plain.zip
```

would be stored remotely as follows.

With `simplify_path=False`:

```text
public/.volumes/fs/public/ex/examples_template_985dc9d7/raw-public.plain.zip
```

With `simplify_path=True`:

```text
public/ex/examples_template_985dc9d7/raw-public.plain.zip
```
""",
    )
    read_mode: Literal['remote_only', 'remote_then_local'] = Field(
        'remote_only',
        description="""Controls reads of public files when a remote filesystem is configured.

``remote_only`` reads published files only from the configured remote filesystem.
``remote_then_local`` reads from remote storage when a matching
``.nomad-remote-ready.json`` completion marker is present, and falls back to the local
public filesystem otherwise (including while a dual-write copy is still running).
Legacy publishes without a marker select remote only when the local public directory
has no published artifacts. The selected backend is used consistently for an upload;
artifacts are never mixed between the two backends.
""",
    )
    write_mode: Literal['local_only', 'remote_only', 'local_then_remote'] | None = (
        Field(
            None,
            description="""Controls writes of published files when a remote filesystem is configured.

``local_only`` packs published artifacts only onto the local public filesystem.
``remote_only`` packs published artifacts only onto the configured remote filesystem
(historical behaviour when ``protocol`` is set).
``local_then_remote`` packs onto the local public filesystem, copies the finished
objects to remote storage, then writes a ``.nomad-remote-ready.json`` completion
marker. The marker is the read gate for ``remote_then_local``.

If unset, defaults to ``remote_only`` when ``protocol`` is set, otherwise ``local_only``.
""",
        )
    )
    redirect_downloads: bool = Field(
        False,
        description='Redirect whole non-embargoed published raw ZIP downloads to signed remote URLs.',
    )
    signed_url_expiration: int = Field(
        24 * 60 * 60,
        gt=0,
        description='Lifetime in seconds of signed URLs created for published raw ZIP downloads.',
    )
    zip_tail_prefetch_kb: int = Field(
        512,
        ge=0,
        description="""KiB of ZIP tail to prefetch when indexing a remote published raw archive.

The end-of-central-directory record stores absolute offsets from byte 0 of the ZIP.
Prefetching the last N KiB lets ``zipfile.ZipFile`` parse the central directory from RAM
for typical uploads. Set to 0 to fetch the tail on demand. Oversized central directories
fall back to additional range reads.""",
    )
    metadata_cache: MetadataCache = Field(
        default_factory=MetadataCache,
        description="""Per-node cache of ZIP member/archive TOC indexes and remote-ready markers.

Enabled automatically for S3 public storage. Index files are keyed by object
identity (never upload id alone). A process-level memory cache skips the
HEAD/info call within ``revalidate_seconds``; after that TTL, or when memory
caching is disabled, each request validates with one HEAD/info. A validated
remote-ready marker uses the same revalidation period before its marker GET and
artifact HEADs are repeated. Disk files are shared by all workers on the node.""",
    )

    @model_validator(mode='after')
    @classmethod
    def __validate(cls, values):
        if values.protocol == 's3':
            assert values.bucket is not None, (
                'A valid bucket name for remote S3 storage is required.'
            )
        elif values.protocol is not None:
            raise ValueError('Only support S3 for the moment.')

        return values

    @property
    def resolved_write_mode(
        self,
    ) -> Literal['local_only', 'remote_only', 'local_then_remote']:
        """Effective published-write destination.

        An explicit ``write_mode`` wins. Otherwise ``remote_only`` when
        ``protocol`` is set, else ``local_only``.
        """
        if self.write_mode is not None:
            return self.write_mode
        return 'remote_only' if self.protocol is not None else 'local_only'

    def ensure_buffer_size(self):
        if self.protocol == 's3':
            if msglc_config.read_buffer_size < 5 * 2**20:
                configure(read_buffer_size=5 * 2**20)
            if msglc_config.write_buffer_size < 5 * 2**20:
                configure(write_buffer_size=5 * 2**20)

    @property
    def target_fs(self) -> AbstractFileSystem:
        if self.protocol is None:
            return LocalFileSystem()

        remote_fs = filesystem(self.protocol, **self.extra)
        key = (
            self.protocol,
            self.bucket,
            json.dumps(self.extra, sort_keys=True, default=str),
        )
        with _ensured_target_fs_lock:
            already_ensured = key in _ensured_target_fs
        if not already_ensured:
            # Two first callers may both miss and both head_bucket; that is
            # acceptable. Do not hold the lock across the network call.
            try:
                remote_fs.makedirs(self.bucket, exist_ok=True)
            except Exception as e:
                raise RuntimeError(
                    'Cannot establish valid connection to the target file system.'
                ) from e
            with _ensured_target_fs_lock:
                _ensured_target_fs.add(key)

        self.ensure_buffer_size()

        return remote_fs


class FS(ConfigBaseModel):
    tmp: str = Field(
        '.volumes/fs/tmp',
        description='Internal temporary filesystem path used during processing.',
    )
    staging: str = Field(
        '.volumes/fs/staging',
        description='Internal path used for staging uploads before they are processed.',
    )
    staging_external: str | None = Field(
        None,
        description='External/absolute path to staging storage. If None, derived from working_directory.',
    )
    public: str = Field(
        '.volumes/fs/public',
        description='Internal path where public files (e.g. published uploads) are stored.',
    )
    public_external: str | None = Field(
        None,
        description='External/absolute path to the public storage. If None, derived from working_directory.',
    )
    north_home: str = Field(
        '.volumes/fs/north/users',
        description='Internal base path for NORTH user home directories.',
    )
    north_home_external: str | None = Field(
        None,
        description='External/absolute path for NORTH user home directories. If None, derived from working_directory.',
    )
    north_home_user_folder_map: dict[str, str] | None = Field(
        {},
        description="""
        This can be used to mount external folders with already existing user data for every user's work folder in the North tools. For example, if you already store user's data files under their own folders on the server, you can mount them with this into the user's launched North tool e.g. Jupyter notebook.
        The username is on the left hand side and the external folder path on disk on the right hand side. For example:
            north_home_user_folder_map:
                     'nomad username': '/path/on/disk/to/work/folder/specific/for/user'
    """,
    )
    actions: str = Field(
        '.volumes/fs/actions',
        description='Internal path used for storing action logs and artifacts.',
    )
    actions_external: str | None = Field(
        None,
        description='External/absolute path for action artifacts. If None, derived from working_directory.',
    )
    local_tmp: str = Field(
        '/tmp',
        description='Local temporary directory on the host system (outside of NOMAD volumes).',
    )
    prefix_size: int = Field(
        2,
        ge=0,
        description='Number of characters from upload/entry IDs used as directory prefixes in storage.',
    )
    archive_version_suffix: str | list[str] = Field(
        ['v1.2', 'v1'],
        description="""
        This allows to add an additional segment to the names of archive files and
        thereby allows different NOMAD installations to work with the same storage
        directories and raw files, but with separate archives.

        If this is a list, the first string is used. If the file with the first
        string does not exist on read, the system will look for the file with the
        next string, etc.
    """,
    )
    working_directory: str = Field(
        default_factory=os.getcwd,
        description='Base working directory used to resolve relative filesystem paths.',
    )
    external_working_directory: str | None = Field(
        None,
        description='Optional external working directory overriding working_directory for derived paths.',
    )
    public_fs: NOMADFileSystem = Field(
        default_factory=NOMADFileSystem,
        description="""Advanced file system for public storage, with which one can use storage schemes other than local FS.
For example, one can use S3 to store public files.

WARNING: The current implementation is intended only for new installations using different configurations.
Modifying the storage configuration of an existing installation would make previously stored data incompatible,
and no migration path is currently provided.""",
    )

    @model_validator(mode='after')
    @classmethod
    def __validate(cls, values):  # pylint: disable=no-self-argument
        def get_external_path(path):
            if os.path.isabs(path):
                return path
            external_work_dir = values.external_working_directory
            work_dir = external_work_dir or values.working_directory
            return os.path.join(work_dir, path)

        if values.staging_external is None:
            values.staging_external = get_external_path(values.staging)

        if values.public_external is None:
            values.public_external = get_external_path(values.public)

        if values.north_home_external is None:
            values.north_home_external = get_external_path(values.north_home)

        if values.actions_external is None:
            values.actions_external = get_external_path(values.actions)

        return values


class ActionAssets(ConfigBaseModel):
    max_file_size_bytes: int = Field(
        100 * 1024 * 1024,
        description='Maximum allowed action asset file size in bytes.',
    )
    allowed_media_types: list[str] = Field(
        [
            'audio/*',
            'image/*',
            'application/pdf',
            'application/json',
            'text/plain',
            'application/octet-stream',
        ],
        description='Allowed media types for action asset uploads.',
    )
    per_user_quota_bytes: int = Field(
        10 * 1024 * 1024 * 1024,
        description='Maximum combined size of action assets a user can upload.',
    )


class Actions(ConfigBaseModel):
    action_assets: ActionAssets = Field(
        default_factory=ActionAssets,
        description='Action asset upload/storage configuration.',
    )


class Elastic(ConfigBaseModel):
    version: Literal['7', '9'] = Field(
        '7',
        description=(
            'Major version of the Elasticsearch cluster. Version 7 is supported '
            'temporarily and is deprecated; use version 9 for new deployments.'
        ),
    )

    @field_validator('version', mode='before')
    @classmethod
    def transform_version(cls, value: Any) -> Any:
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value

    username: str = Field(
        '',
        description='Username for authenticating with the Elasticsearch server.',
    )
    password: SecretStr = Field(
        SecretStr(''),
        exclude=True,
        description='Password for authenticating with the Elasticsearch server.',
    )
    host: str = Field(
        'localhost',
        description='Hostname or IP address of the Elasticsearch server.',
    )
    port: int = Field(
        9200,
        description='Port on which the Elasticsearch server is listening.',
    )
    scheme: Literal['http', 'https'] = Field(
        'http',
        description=(
            'URL scheme used to connect to the Elasticsearch server. '
            'Elasticsearch 9 requires this; its Python client rejects host:port '
            'without a scheme. Values with a trailing :// (http://, https://) '
            'are accepted.'
        ),
    )

    @field_validator('scheme', mode='before')
    @classmethod
    def transform_scheme(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower().removesuffix('://')
        return value

    timeout: int = Field(
        60,
        description='Default request timeout (in seconds) for Elasticsearch operations.',
    )
    bulk_timeout: int = Field(
        600,
        description='Timeout (in seconds) for bulk Elasticsearch operations.',
    )
    bulk_size: int = Field(
        1000,
        description='Number of documents per bulk indexing/request batch.',
    )
    bulk_retry_attempts: int = Field(
        5,
        ge=1,
        description='Number of retry attempts for rejected Elasticsearch bulk requests.',
    )
    bulk_retry_initial_backoff: float = Field(
        1.0,
        ge=0.0,
        description='Initial backoff in seconds for Elasticsearch bulk retries.',
    )
    bulk_retry_max_backoff: float = Field(
        30.0,
        ge=0.0,
        description='Maximum backoff in seconds for Elasticsearch bulk retries.',
    )
    bulk_retry_jitter: float = Field(
        0.5,
        ge=0.0,
        description='Maximum random jitter in seconds added to Elasticsearch bulk retries.',
    )
    max_payload_size: int = Field(
        90 * 1024 * 1024,  # 90 MB
        description='Maximum payload size sent to the Elasticsearch server in bytes. Note that Elasticsearch has an internal limit of 100MB that you can configure as well.',
    )
    entries_index: str = Field(
        'nomad_entries_v1',
        description='Name of the Elasticsearch index storing entries.',
    )


class ProcessingTimeouts(ConfigBaseModel):
    internal_processing_heartbeat_timeout: int = Field(
        600,  # ten minutes
        description='The maximum interval in seconds that an internal processing activity (e.g. entry processing) can run without reporting progress (sending a heartbeat) before Temporal considers it failed.',
    )
    delete_upload_timeout: int = Field(
        3600,  # 1 hour
        description='The timeout for activities in the delete upload workflow in seconds.',
    )
    process_entry_timeout: int = Field(
        7200,  # 2 hours
        description='The timeout for the process entry activity in seconds.',
    )
    process_upload_timeout: int = Field(
        3600,  # 1 hour
        description='The timeout for activities in the process upload workflow in seconds.',
    )
    edit_upload_metadata_timeout: int = Field(
        3600,  # 1 hour
        description='The timeout for activities in the edit upload metadata workflow in seconds.',
    )
    import_bundle_timeout: int = Field(
        3600,  # 1 hour
        description='The timeout for activities in the import bundle workflow in seconds.',
    )
    publish_upload_timeout: int = Field(
        3600,  # 1 hour
        description='The timeout for activities in the publish upload workflow in seconds.',
    )
    publish_externally_timeout: int = Field(
        3600,  # 1 hour
        description='The timeout for activities in the publish externally workflow in seconds.',
    )
    process_example_upload_timeout: int = Field(
        3600,  # 1 hour
        description='The timeout for activities in the process example upload workflow in seconds.',
    )
    setup_upload_timeout: int = Field(
        300,  # 5 minutes
        description='The timeout for the setup upload activity in seconds.',
    )
    update_files_timeout: int = Field(
        3600,  # 1 hour
        description='The timeout for the update files activity in seconds.',
    )
    match_all_timeout: int = Field(
        3600,  # 1 hour
        description='The timeout for the match all activity in seconds.',
    )
    next_level_entries_timeout: int = Field(
        3600,  # 1 hour
        description='The timeout for the next level entries activity in seconds.',
    )
    cleanup_timeout: int = Field(
        3600,  # 1 hour
        description='The timeout for the cleanup activity in seconds.',
    )
    process_upload_success_timeout: int = Field(
        300,  # 5 minutes
        description='The timeout for the process upload success activity in seconds.',
    )
    remove_workflow_id_timeout: int = Field(
        300,  # 5 minutes
        description='The timeout for the remove workflow id activity in seconds.',
    )
    cleanup_workflow_tmp_dir_timeout: int = Field(
        300,  # 5 minutes
        description='The timeout for the cleanup workflow tmp dir activity in seconds.',
    )


class WorkerConfig(ConfigBaseModel):
    pool_size: int = Field(
        1,
        description="""
            Number of worker processes in the pool. These workers are responsible for
            core NOMAD activities such as entry and upload processing.
        """,
    )
    max_tasks_per_child: int | None = Field(
        None,
        description="""
            Maximum number of tasks a worker process will execute before
            it is restarted to prevent potential memory leaks.
            Note: Bugs have been reported when using the max_tasks_per_child feature that can result in the
            ProcessPoolExecutor hanging in some circumstances. See https://docs.python.org/3/library/concurrent.futures.html for more info.
        """,
    )
    max_concurrent_activities: int | None = Field(
        None,
        description="""
            Maximum number of concurrent activities that each worker process
            can handle. If not set, the worker will use resource-based tuning.
        """,
    )
    target_memory_usage: float = Field(
        0.8,
        description="""
            Target memory usage for the worker tuner. If max_concurrent_activities is not set,
            the worker will try to keep memory usage below this threshold.
            It is not recommended to increase this above 0.8 as it might limit performance.
        """,
    )
    target_cpu_usage: float = Field(
        0.8,
        description="""
            Target CPU usage for the worker tuner. If max_concurrent_activities is not set,
            the worker will try to keep CPU usage below this threshold.
            It is not recommended to increase this above 0.8 as it might limit performance.
        """,
    )
    activity_ramp_throttle: int = Field(
        200,
        description="""
            If max_concurrent_activities is not set, this is the ramp throttle
            for the activity slot supplier in milliseconds.
            It is the minimum time the worker will wait between handing out new slots.
            This value matters because how many resources a task will use cannot be determined ahead of time,
            and thus the system should wait to see how much resources are used before issuing more slots.
        """,
    )
    max_activity_slots: int = Field(
        20,
        description="""
            If max_concurrent_activities is not set, this is the maximum number of
            slots for the activity slot supplier.
        """,
    )
    min_activity_slots: int | None = Field(
        None,
        description="""
            If max_concurrent_activities is not set, this optionally overrides the
            minimum number of activity slots for resource-based tuning.
            If unset, NOMAD uses `pool_size` as the minimum.
        """,
    )
    healthcheck_enabled: bool = Field(
        True,
        description="""
            Enables a lightweight HTTP /health endpoint for worker process health checks.
            The endpoint is intended for container orchestrators such as Docker or Kubernetes.
            It is skipped automatically in development mode to avoid port conflicts when
            running multiple workers on the same machine.
        """,
    )
    healthcheck_host: str = Field(
        '0.0.0.0',
        description='Host/interface used by the worker healthcheck HTTP server.',
    )
    healthcheck_port: int = Field(
        8080,
        description='Port used by the worker healthcheck HTTP server.',
    )


class TemporalOIDC(ConfigBaseModel):
    enabled: bool = Field(
        False,
        description='Use an OAuth 2.0 client-credentials access token to authenticate Temporal clients.',
    )
    token_url: str | None = Field(
        None,
        description='OIDC token endpoint used to obtain Temporal access tokens.',
    )
    client_id: str | None = Field(
        None,
        description='Confidential OIDC client ID representing this NOMAD deployment.',
    )
    client_secret: SecretStr | None = Field(
        None,
        exclude=True,
        description='Confidential OIDC client secret. Configure this through deployment secrets.',
    )
    scope: str | None = Field(
        None,
        description='Optional OAuth 2.0 scope sent with client-credentials token requests.',
    )
    refresh_margin: float = Field(
        30,
        ge=0,
        description='Number of seconds before expiry at which an access token is renewed.',
    )
    request_timeout: float = Field(
        10,
        gt=0,
        description='Timeout in seconds for requests to the OIDC token endpoint.',
    )

    @model_validator(mode='after')
    def validate_client_credentials(self):
        if not self.enabled:
            return self

        missing = [
            field_name
            for field_name in ('token_url', 'client_id', 'client_secret')
            if not getattr(self, field_name)
        ]
        if missing:
            raise ValueError(
                'Temporal OIDC authentication requires: ' + ', '.join(missing)
            )

        self.token_url = self.token_url.rstrip('/')
        return self


class Temporal(ConfigBaseModel):
    host: str = Field(
        'localhost',
        description='Hostname or IP address of the Temporal server.',
    )
    port: int = Field(
        7233,
        description='Port on which the Temporal server is listening.',
    )
    namespace: str = Field(
        'default',
        description='Temporal namespace used for NOMAD workflows.',
    )
    enabled: bool = Field(
        True,
        description='If False, Temporal-based processing is disabled and alternative mechanisms may be used.',
    )
    graceful_shutdown_timeout: int = Field(
        1,
        description='Graceful shutdown timeout (in seconds) for Temporal workers.',
    )
    entry_workflow_batch_concurrency: int = Field(
        5,
        ge=1,
        description="""
            Controls how many entry batch processing workflows are processed at the
            same time. Only used for very large uploads where entries are split
            into temporary batches.
        """,
    )
    entry_concurrency_target: int = Field(
        50,
        ge=1,
        description="""
            Approximate number of entries to process concurrently inside one
            batch-processing workflow. Together with `entry_activity_batch_size`,
            this determines how many batch activities run at the same time.
        """,
    )
    entry_activity_batch_size: int = Field(
        5,
        ge=1,
        description="""
            Number of entries grouped into a single process-entry activity invocation.
            Larger values reduce Temporal scheduling/event overhead for fast entries,
            but increase the amount of work retried together on transient failures.
        """,
    )
    prometheus_bind_address: str | None = Field(
        None,
        description='The bind address for the Prometheus metrics server. If not set, the runtime will not be configured with Prometheus metrics.',
    )
    api_key: SecretStr | None = Field(
        None,
        exclude=True,
        description='API Key used to connect to the Temporal server.',
    )
    oidc: TemporalOIDC = Field(
        default_factory=TemporalOIDC,
        description='OIDC client-credentials authentication for Temporal clients.',
    )
    payload_codec_key: SecretStr | None = Field(
        None,
        min_length=32,
        exclude=True,
        description='Optional key used to encrypt Temporal payloads. When unset, services.api_secret is used by default.',
    )
    payload_codec_key_id: str = Field(
        'default',
        min_length=1,
        description='Identifier stored with payloads encrypted using payload_codec_key.',
    )
    use_tls: bool = Field(
        False,
        description='Whether to use TLS to connect to the Temporal server. Defaults to False. If True and no certificates are provided, default system certificates are used.',
    )
    tls_client_cert: str | None = Field(
        None,
        description='Path to the client certificate (PEM format) or the raw certificate content string to use for mTLS.',
    )
    tls_client_key: SecretStr | None = Field(
        None,
        exclude=True,
        description='Path to the client private key (PEM format) or the raw key content string to use for mTLS.',
    )
    tls_server_root_ca_cert: str | None = Field(
        None,
        description='Path to the server root CA certificate (PEM format) or the raw CA certificate content string to verify the server certificate.',
    )
    tls_domain: str | None = Field(
        None,
        description='The server name / domain to verify TLS against.',
    )
    processing_timeouts: ProcessingTimeouts = Field(
        default_factory=ProcessingTimeouts,
        description='Timeout configuration for individual processing workflows and activities.',
    )

    internal_worker: WorkerConfig = Field(
        default_factory=lambda: WorkerConfig(max_concurrent_activities=1),
        description='Configuration for the internal action worker.',
    )
    cpu_worker: WorkerConfig = Field(
        default_factory=lambda: WorkerConfig(pool_size=12),
        description='Configuration for the CPU action worker.',
    )
    gpu_worker: WorkerConfig = Field(
        default_factory=lambda: WorkerConfig(pool_size=12),
        description='Configuration for the GPU action worker.',
    )

    @model_validator(mode='after')
    def validate_authentication(self):
        if self.oidc.enabled and self.api_key:
            raise ValueError(
                'Configure either temporal.api_key or temporal.oidc, not both.'
            )
        return self


class Keycloak(ConfigBaseModel):
    server_url: str = Field(
        'https://nomad-lab.eu/fairdi/keycloak/auth',
        description='Internal base URL of the Keycloak server used by NOMAD.',
    )
    public_server_url: str | None = Field(
        None,
        description='Publicly reachable Keycloak server URL. Defaults to server_url if not explicitly set.',
    )
    realm_name: str = Field(
        'fairdi_nomad_prod',
        description='Keycloak realm name used for this NOMAD deployment.',
    )
    username: str = Field(
        'admin',
        description='Administrative Keycloak username used for service-level operations.',
    )
    password: SecretStr = Field(
        SecretStr('password'),
        exclude=True,
        description='Administrative Keycloak password used for service-level operations.',
    )
    client_id: str = Field(
        'nomad_public',
        description='Keycloak client ID used by the NOMAD backend/UI.',
    )
    client_secret: SecretStr | None = Field(
        None,
        exclude=True,
        description='Optional Keycloak client secret used for confidential clients.',
    )

    @model_validator(mode='after')
    @classmethod
    def __validate(cls, values):
        values.server_url = values.server_url.rstrip('/')

        if values.public_server_url is None:
            values.public_server_url = values.server_url
        return values


class Mongo(ConfigBaseModel):
    """Connection and usage settings for MongoDB."""

    host: str = Field(
        'localhost', description='The name of the host that runs mongodb.'
    )
    port: int = Field(27017, description='The port to connect with mongodb.')
    db_name: str = Field('nomad_v1', description='The used mongodb database name.')
    username: str | None = Field(
        None,
        description='Optional username for MongoDB authentication.',
    )
    password: SecretStr | None = Field(
        None,
        exclude=True,
        description='Optional password for MongoDB authentication.',
    )
    ownership_transfer_record_ttl: int = Field(
        90 * 24 * 3600,
        description='TTL in seconds for persisted ownership transfer records.',
    )


class Logstash(ConfigBaseModel):
    enabled: bool = Field(
        False,
        description='If True, logs are forwarded to a Logstash instance.',
    )
    host: str = Field(
        'localhost',
        description='Hostname or IP address of the Logstash server.',
    )
    tcp_port: str = Field(
        '5000',
        description='TCP port on which the Logstash server receives logs.',
    )
    level: int | str = Field(
        logging.DEBUG,
        description='Minimum log level for logs sent to Logstash.',
    )

    # Validators
    _level = field_validator('level', mode='before')(normalize_loglevel)

    model_config = ConfigDict(coerce_numbers_to_str=True)


class Tests(ConfigBaseModel):
    default_timeout: int = Field(
        60,
        description='Default timeout (in seconds) used in test utilities and helpers.',
    )
    assume_auth_for_username: str | None = Field(
        None,
        description=(
            'Will assume that all API calls with no authentication have authentication for '
            'the user with the given username.'
        ),
    )


class Mail(ConfigBaseModel):
    enabled: bool = Field(
        False,
        description='If True, NOMAD will attempt to send emails using the configured SMTP server.',
    )
    with_login: bool = Field(
        False,
        description='If True, NOMAD will authenticate to the SMTP server using the given user and password.',
    )
    host: str = Field(
        '',
        description='Hostname or IP address of the SMTP server.',
    )
    port: int = Field(
        8995,
        description='Port of the SMTP server.',
    )
    user: str = Field(
        '',
        description='SMTP username used for authentication (if with_login is True).',
    )
    password: SecretStr = Field(
        SecretStr(''),
        exclude=True,
        description='SMTP password used for authentication (if with_login is True).',
    )
    from_address: str = Field(
        'support@nomad-lab.eu',
        description='Email address used in the From header of outgoing emails.',
    )
    cc_address: str | None = Field(
        None,
        description='Optional email address to CC on outgoing emails.',
    )


class Normalize(ConfigBaseModel):
    normalizers: Options = Field(
        Options(
            include=[
                'OptimadeNormalizer',
                'ResultsNormalizer',
                'MetainfoNormalizer',
            ],
            options=dict(
                PorosityNormalizer='nomad.normalizing.porosity.PorosityNormalizer',
                OptimadeNormalizer='nomad.normalizing.optimade.OptimadeNormalizer',
                ResultsNormalizer='nomad.normalizing.results.ResultsNormalizer',
                MetainfoNormalizer='nomad.normalizing.metainfo.MetainfoNormalizer',
            ),
        )
    )
    system_classification_with_clusters_threshold: int = Field(
        64,
        description="""
            The system size limit for running the dimensionality analysis. For very
            large systems the dimensionality analysis will get too expensive.
        """,
    )
    clustering_size_limit: int = Field(
        1000,
        description="""
            The system size limit for running the system clustering. For very
            large systems the clustering will get too expensive.
        """,
    )
    symmetry_tolerance: float = Field(
        0.1,
        description="""
            Symmetry tolerance controls the precision used by spglib in order to
            find symmetries. The atoms are allowed to move this much from their
            symmetry positions in order for spglib to still detect symmetries.
            The unit is angstroms. The value of 0.1 is used e.g. by Materials
            Project according to
            https://pymatgen.org/pymatgen.symmetry.html#pymatgen.symmetry.analyzer.SpacegroupAnalyzer
        """,
    )
    prototype_symmetry_tolerance: float = Field(
        0.1,
        description="""
            The symmetry tolerance used in aflow prototype matching. Should only be
            changed before re-running the prototype detection.
        """,
    )
    max_2d_single_cell_size: int = Field(
        7,
        description="""
            Maximum number of atoms in the single cell of a 2D material for it to be
            considered valid.
        """,
    )
    cluster_threshold: float = Field(
        2.5,
        description="""
            The distance tolerance between atoms for grouping them into the same
            cluster. Used in detecting system type.
        """,
    )
    angle_rounding: float = Field(
        10.0,
        description="""
            Defines the "bin size" for rounding cell angles for the material hash in degree.
        """,
    )
    flat_dim_threshold: float = Field(
        0.1,
        description="""
            The threshold for a system to be considered "flat". Used e.g. when
            determining if a 2D structure is purely 2-dimensional to allow extra rigid
            transformations that are improper in 3D but proper in 2D.
        """,
    )
    k_space_precision: float = Field(
        150e6,
        description="""
            The threshold for point equality in k-space. Unit: 1/m.
        """,
    )
    band_structure_energy_tolerance: float = Field(
        8.01088e-21,
        description="""
            The energy threshold for how much a band can be on top or below the fermi
            level in order to still detect a gap. Unit: Joule.
        """,
    )
    springer_db_path: str | None = Field(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'normalizing/data/springer.msg'
        )
    )

    @model_validator(mode='after')
    @classmethod
    def __validate(cls, values):  # pylint: disable=no-self-argument
        proto_symmetry_tolerance = values.prototype_symmetry_tolerance
        symmetry_tolerance = values.symmetry_tolerance
        if proto_symmetry_tolerance != symmetry_tolerance:
            raise AssertionError(
                'The AFLOW prototype information is outdated due to changed tolerance '
                'for symmetry detection. Please update the AFLOW prototype information '
                "by running the CLI command 'nomad admin ops prototypes-update --matches-only'"
            )

        springer_db_path = values.springer_db_path
        if springer_db_path and not os.path.exists(springer_db_path):
            values.springer_db_path = None

        return values


class Client(ConfigBaseModel):
    user: str | None = Field(
        None,
        description='Optional username used by the Python NOMAD client for authentication.',
    )
    password: SecretStr | None = Field(
        None,
        exclude=True,
        description='Optional password used by the Python NOMAD client for authentication.',
    )
    access_token: SecretStr | None = Field(
        None,
        exclude=True,
        description='Optional bearer access token used by the Python NOMAD client.',
    )
    url: str = Field(
        'https://nomad-lab.eu/prod/v1/api',
        description='Base URL of the NOMAD API used by the Python client.',
    )


class DataCite(ConfigBaseModel):
    mds_host: str = Field(
        'https://api.test.datacite.org',
        description='Base URL of the DataCite REST API.',
    )
    enabled: bool = Field(
        False,
        description='If True, NOMAD will register DOIs via DataCite for published uploads/datasets.',
    )
    prefix: str = Field(
        '10.83696',  # test prefix
        description='DataCite DOI prefix assigned to this NOMAD deployment.',
    )
    user: str = Field(
        '*',
        description='DataCite username.',
    )
    password: SecretStr = Field(
        SecretStr('*'),
        exclude=True,
        description='DataCite password.',
    )


class GitLab(ConfigBaseModel):
    private_token: SecretStr = Field(
        SecretStr('not set'),
        exclude=True,
        description='Private token used for accessing the GitLab API (e.g. for CI integrations).',
    )


class Process(ConfigBaseModel):
    reuse_parser: bool = Field(
        True,
        description='If True, parser instances may be reused between entries to improve performance.',
    )
    metadata_file_name: str = Field(
        'nomad',
        description='Base name (without extension) for per-upload metadata files.',
    )
    metadata_file_extensions: tuple[str, ...] = Field(
        ('json', 'yaml', 'yml'),
        description='Allowed file extensions for per-upload metadata files.',
    )
    auxfile_cutoff: int = Field(
        100,
        description='Maximum number of auxiliary files considered when matching parsers.',
    )
    parser_matching_size: int = Field(
        150 * 80,
        description='Heuristic size parameter used when selecting parsers based on file size.',
    )
    max_upload_size: int = Field(
        32 * (1024**3),
        description='Maximum allowed upload size in bytes.',
    )
    use_empty_parsers: bool = Field(
        False,
        description='If True, allow parsers that produce empty archives to be used.',
    )
    redirect_stdouts: bool = Field(
        False,
        description="""
        True will redirect lines to stdout (e.g. print output) that occur during
        processing (e.g. created by parsers or normalizers) as log entries.
    """,
    )
    rfc3161_skip_published: bool = Field(
        False,
        description='If True, RFC3161 timestamping is skipped for already published uploads.',
    )
    exclude_potcar: bool = Field(
        True,
        description="""
        True will exclude original VASP POTCAR file from upload.
        """,
    )


class Reprocess(ConfigBaseModel):
    rematch_published: bool = Field(
        True,
        description='If True, published uploads are rematched with parsers during reprocessing.',
    )
    reprocess_existing_entries: bool = Field(
        True,
        description='If True, existing entries in the database are reprocessed.',
    )
    use_original_parser: bool = Field(
        False,
        description='If True, the originally used parser is forced during reprocessing (if available).',
    )
    add_matched_entries_to_published: bool = Field(
        True,
        description='If True, newly matched entries are added to the published set.',
    )
    delete_unmatched_published_entries: bool = Field(
        False,
        description='If True, previously published entries that no longer match are deleted.',
    )
    index_individual_entries: bool = Field(
        False,
        description='If True, individual entries are re-indexed during reprocessing.',
    )


class RFC3161Timestamp(ConfigBaseModel):
    server: str = Field(
        'http://zeitstempel.dfn.de', description='The rfc3161ng timestamping host.'
    )
    cert: str | None = Field(
        None,
        description='Path to the optional rfc3161ng timestamping server certificate.',
    )
    hash_algorithm: str = Field(
        'sha256',
        description='Hash algorithm used by the rfc3161ng timestamping server.',
    )
    username: str | None = Field(
        None,
        description='Optional username for authenticating to the RFC3161 timestamping server.',
    )
    password: SecretStr | None = Field(
        None,
        exclude=True,
        description='Optional password for authenticating to the RFC3161 timestamping server.',
    )


class BundleExportSettings(ConfigBaseModel):
    include_raw_files: bool = Field(
        True, description='If the raw files should be included in the export'
    )
    include_archive_files: bool = Field(
        True, description='If the parsed archive files should be included in the export'
    )
    include_datasets: bool = Field(
        True, description='If the datasets should be included in the export'
    )
    include_schemas: bool = Field(
        False, description='If schemas should be included in the export'
    )


class BundleExport(ConfigBaseModel):
    """Controls behaviour related to exporting bundles."""

    default_cli_bundle_export_path: str = Field(
        './bundles',
        description='Default path used when exporting bundles using the CLI command.',
    )
    default_settings: BundleExportSettings = Field(
        BundleExportSettings(),
        description="""
            General default settings.
        """,
    )
    default_settings_cli: BundleExportSettings | None = Field(
        None,
        description="""
            Additional default settings, applied when exporting using the CLI. This allows
            to override some of the settings specified in the general default settings above.
        """,
    )


class BundleImportSettings(ConfigBaseModel):
    include_raw_files: bool = Field(
        True, description='If the raw files should be included in the import'
    )
    include_archive_files: bool = Field(
        True, description='If the parsed archive files should be included in the import'
    )
    include_datasets: bool = Field(
        True, description='If the datasets should be included in the import'
    )

    include_bundle_info: bool = Field(
        True,
        description='If the bundle_info.json file should be kept (not necessary but may be nice to have.',
    )
    keep_original_timestamps: bool = Field(
        False,
        description="""
            If all timestamps (create time, publish time etc) should be imported from
            the bundle.
        """,
    )
    set_from_oasis: bool = Field(
        True,
        description='If the from_oasis flag and oasis_deployment_url should be set.',
    )

    delete_upload_on_fail: bool = Field(
        False, description='If False, it is just removed from the ES index on failure.'
    )
    delete_bundle_on_fail: bool = Field(
        True, description='Deletes the source bundle if the import fails.'
    )
    delete_bundle_on_success: bool = Field(
        True, description='Deletes the source bundle if the import succeeds.'
    )
    delete_bundle_include_parent_folder: bool = Field(
        True,
        description='When deleting the bundle, also include parent folder, if empty.',
    )

    trigger_processing: bool = Field(
        False,
        description='If the upload should be processed when the import is done (not recommended).',
    )
    process_settings: Reprocess = Field(
        Reprocess(
            rematch_published=True,
            reprocess_existing_entries=True,
            use_original_parser=False,
            add_matched_entries_to_published=True,
            delete_unmatched_published_entries=False,
        ),
        description="""
            When trigger_processing is set to True, these settings control the reprocessing
            behaviour (see the config for `reprocess` for more info). NOTE: reprocessing is
            no longer the recommended method to import bundles.
        """,
    )


class BundleImport(ConfigBaseModel):
    """Controls behaviour related to importing bundles."""

    required_nomad_version: str = Field(
        '1.1.2', description='Minimum  NOMAD version of bundles required for import.'
    )

    default_cli_bundle_import_path: str = Field(
        './bundles',
        description='Default path used when importing bundles using the CLI command.',
    )

    allow_bundles_from_oasis: bool = Field(
        False,
        description='If oasis admins can "push" bundles to this NOMAD deployment.',
    )
    allow_unpublished_bundles_from_oasis: bool = Field(
        False, description='If oasis admins can "push" bundles of unpublished uploads.'
    )

    default_settings: BundleImportSettings = Field(
        BundleImportSettings(),
        description="""
            General default settings.
        """,
    )

    default_settings_cli: BundleImportSettings = Field(
        BundleImportSettings(
            delete_bundle_on_fail=False, delete_bundle_on_success=False
        ),
        description="""
            Additional default settings, applied when importing using the CLI. This allows
            to override some of the settings specified in the general default settings above.
        """,
    )


class Archive(ConfigBaseModel):
    block_size: int = Field(
        1 * 2**20,
        description='Deprecated, not used in the latest storage. In case of using blocked TOC, this is the size of each block.',
    )
    read_buffer_size: int = Field(
        1 * 2**20,
        description='GPFS needs at least 256K to achieve decent performance.',
    )
    copy_chunk_size: int = Field(
        16 * 2**20,
        description="""
        The chunk size of every read of binary data.
        It is used to copy data from one file to another.
        A small value will result in more syscalls, a large value will result in higher peak memory usage.
        """,
    )
    toc_depth: int = Field(
        10,
        description='Deprecated, not used in the latest storage. Depths of table of contents in the archive.',
    )
    small_obj_optimization_threshold: int = Field(
        4 * 2**20,
        description="""
        For any child of lists/dicts whose encoded size is smaller than this value, no TOC will be generated.""",
    )
    fast_loading: bool = Field(
        True,
        description="""
        When enabled, this flag determines whether to read the whole dict/list at once
        when a certain mount of children has been visited.
        This reduces the number of syscalls although data may be repeatedly read.
        Otherwise, always read children one by one. This may slow down the loading as more syscalls are needed.
        """,
    )
    fast_loading_threshold: float = Field(
        0.6,
        description="""
        If the fraction of children that have been visited is less than this threshold, fast loading will be used.
        """,
    )
    trivial_size: int = Field(
        20,
        description="""
        To identify numerical lists.
        """,
    )

    def initialize(self):
        """
        Pass corresponding configurations to the storage layer via msglc.
        """
        configure(
            small_obj_optimization_threshold=self.small_obj_optimization_threshold,
            write_buffer_size=self.read_buffer_size,
            read_buffer_size=self.read_buffer_size,
            fast_loading=self.fast_loading,
            fast_loading_threshold=self.fast_loading_threshold,
            trivial_size=self.trivial_size,
            copy_chunk_size=self.copy_chunk_size,
            # Memory may leak when set to True in concurrent scenarios.
            disable_gc=False,
            # we only deal with data 100% comply with JSON specification
            # disable extended types to improve performance
            has_numpy=False,
            has_set=False,
            has_tuple=False,
        )


class MolIDSourceEnum(str, Enum):
    CACHE = 'cache'
    API = 'api'


class MolID(ConfigBaseModel):
    """
    Configuration for the MolID integration.
    """

    enabled: bool = Field(
        True,
        description='If True, the MolID service is enabled and may be used to resolve chemical compound information from.',
    )
    sources: list[MolIDSourceEnum] = Field(
        [MolIDSourceEnum.CACHE, MolIDSourceEnum.API],
        description='List of sources that are queried in the given order when resolving chemical compound information.',
    )
    cache_write: bool = Field(
        True,
        description='If True, any API reads are persisted to a cache for faster future access.',
    )
    cache_path: str | None = Field(
        None,
        description='Path to the MolID cache file. If not specified, defaults to a `molid_cache.db` file in the `fs.tmp` directory.',
    )


class Pagination(ConfigBaseModel, PaginationBaseModel):
    pass


class Entries(ConfigBaseModel):
    pagination: Pagination = Pagination(
        page_size=5, order_by='process_status', order='asc'
    )


class Uploads(ConfigBaseModel):
    pagination: Pagination = Pagination(
        page_size=10, order_by='upload_create_time', order='desc'
    )
    entries: Entries = Entries()


class TelemetryTracing(ConfigBaseModel):
    enabled: bool = Field(False, description='Enable OpenTelemetry tracing.')
    service_name: str = Field(
        'nomad-FAIR', description='Service name for OpenTelemetry.'
    )
    sampler_ratio: float = Field(
        0.1, description='The ratio of traces to sample (0.0 to 1.0).'
    )
    otlp_endpoint: str | None = Field(
        'http://localhost:4318/v1/traces',
        description='OTLP exporter endpoint (e.g. grpc://localhost:4317 or http://localhost:4318/v1/traces).',
    )


class TelemetryMetrics(ConfigBaseModel):
    api_prometheus_enabled: bool = Field(
        False, description='Enable Prometheus metrics exporter for FastAPI API.'
    )


class Telemetry(ConfigBaseModel):
    tracing: TelemetryTracing = Field(
        default_factory=TelemetryTracing,
        description='Configuration for OpenTelemetry tracing.',
    )
    metrics: TelemetryMetrics = Field(
        default_factory=TelemetryMetrics,
        description='Configuration for metrics reporting (e.g., Prometheus).',
    )


class Config(ConfigBaseModel):
    """Model for the NOMAD configuration."""

    model_config = ConfigDict(validate_by_name=True)

    services: Services = Field(
        default_factory=Services,
        description='Settings for core NOMAD services (API, worker, north).',
    )
    auth: Auth = Field(
        default_factory=Auth,
        description='Authentication/authorization-related configurations.',
    )
    meta: Meta = Field(
        default_factory=Meta,
        description='Metadata and presentation details for this NOMAD deployment.',
    )
    oasis: Oasis = Field(
        default_factory=Oasis,
        description='Configuration options specific to NOMAD Oasis deployments.',
    )
    north: NORTH = Field(
        default_factory=NORTH,
        description='Configuration for NORTH tools and the NORTH hub.',
    )
    fs: FS = Field(
        default_factory=FS,
        description="""Filesystem paths and storage layout used by NOMAD.
WARNING: Modifying the storage configuration of an existing installation would make previously stored data incompatible.""",
    )
    actions: Actions = Field(
        default_factory=Actions,
        description='Settings related to actions features.',
    )
    elastic: Elastic = Field(
        default_factory=Elastic,
        description='Elasticsearch connection details and index configuration.',
    )
    temporal: Temporal = Field(
        default_factory=Temporal,
        description='Temporal.io workflow configuration used for processing.',
    )
    keycloak: Keycloak = Field(
        default_factory=Keycloak,
        description='Keycloak authentication and user management configuration.',
    )
    mongo: Mongo = Field(
        default_factory=Mongo,
        description='MongoDB connection configuration for NOMAD.',
    )
    logstash: Logstash = Field(
        default_factory=Logstash,
        description='Logstash logging and forwarding configuration.',
    )
    telemetry: Telemetry = Field(
        default_factory=Telemetry,
        description='Configuration for NOMAD telemetry.',
    )
    tests: Tests = Field(
        default_factory=Tests,
        description='Settings that influence testing behaviour and assumptions.',
    )
    mail: Mail = Field(
        default_factory=Mail,
        description='Outgoing mail (SMTP) configuration.',
    )
    normalize: Normalize = Field(
        default_factory=Normalize,
        description='Settings controlling normalizers and structural analysis.',
    )
    client: Client = Field(
        default_factory=Client,
        description='Default configuration for the Python NOMAD client.',
    )
    datacite: DataCite = Field(
        default_factory=DataCite,
        description='DataCite DOI registration and credentials configuration.',
    )
    gitlab: GitLab = Field(
        default_factory=GitLab,
        description='GitLab integration and authentication settings.',
    )
    process: Process = Field(
        default_factory=Process,
        description='Primary configuration for upload and entry processing.',
    )
    reprocess: Reprocess = Field(
        default_factory=Reprocess,
        description='Configuration for reprocessing and rematching entries.',
    )
    rfc3161_timestamp: RFC3161Timestamp = Field(
        default_factory=RFC3161Timestamp,
        description='Configuration of RFC3161 timestamping service used by NOMAD.',
    )
    bundle_export: BundleExport = Field(
        default_factory=BundleExport,
        description='Global defaults for bundle export behaviour.',
    )
    bundle_import: BundleImport = Field(
        default_factory=BundleImport,
        description='Global defaults for bundle import behaviour.',
    )
    archive: Archive = Field(
        default_factory=Archive,
        description='Low-level archive storage and performance tuning options.',
    )
    molid: MolID = Field(
        default_factory=MolID,
        description='Configuration for the chemical compound resolution with MolID.',
    )
    ui: UI = Field(
        default_factory=UI,
        description='Configuration for the NOMAD web UI and its endpoints.',
    )
    plugins: Plugins | None = Field(
        default=None,
        description='Resolved plugin configuration, populated by load_plugins().',
    )
    uploads: Uploads = Field(
        default_factory=Uploads,
        alias='projects',
        description='Configuration for uploads/projects presentation.',
    )

    def api_url(
        self,
        ssl: bool = True,
        api: str = 'api',
        api_host: str | None = None,
        api_port: int | None = None,
    ):
        """
        Returns the url of the current running nomad API. This is for server-side use.
        This is not the NOMAD url to use as a client, use `nomad.config.client.url` instead.
        """
        return self.services.api_url(ssl, api, api_host, api_port)

    def gui_url(self, page: str | None = None):
        base = self.services.base_url(True)
        if page is not None:
            return f'{base}{self.services.join_path("gui", page)}'
        return f'{base}{self.services.join_path("gui")}'

    def north_url(self, ssl: bool = True):
        return self.api_url(
            ssl=ssl,
            api='north',
            api_host=self.north.hub_host,
            api_port=self.north.hub_port,  # type: ignore
        )

    def hub_url(self):
        return f'http://{self.north.hub_host}:{self.north.hub_port}{self.services.join_path("north", "hub")}'

    @model_validator(mode='after')
    @classmethod
    def __validate(cls, values):  # pylint: disable=no-self-argument
        services = values.services
        deployment_url = values.meta.deployment_url
        if not deployment_url:
            values.meta.deployment_url = services.api_url()
        north = values.north
        ui = values.ui
        if ui:
            if north:
                values.ui.north.enabled = north.enabled
            if services:
                values.ui.app_base = f'{"https" if services.https else "http"}://{services.api_host}:{services.api_port}{services.route_prefix}'
            if services and north:
                values.ui.north_base = f'{"https" if services.https else "http"}://{north.hub_host}:{north.hub_port}{services.join_path("north")}'

        # Backwards compatibility for auth settings stored in the oasis config.
        if 'require_authentication' in values.oasis.model_fields_set:
            if 'require_authentication' in values.auth.model_fields_set:
                raise ValueError(
                    'You cannot use new and deprecated `require_authentication` together'
                )

            logger.warning(
                'Use auth.require_authentication instead of oasis.require_authentication'
            )
            values.auth.require_authentication = values.oasis.require_authentication

        # Only apply if the deprecated oasis.* field was explicitly set
        # AND the new auth.* field was NOT explicitly set.
        if 'allowed_users' in values.oasis.model_fields_set:
            if 'authorized_users' in values.auth.model_fields_set:
                raise ValueError(
                    'You cannot use new and deprecated user whitelist together'
                )

            logger.warning('Use auth.authorized_users instead of oasis.allowed_users')
            values.auth.authorized_users = values.oasis.allowed_users

            # Previously `oasis.allowed_users` would implicitly enable `require_authentication`
            if (
                'require_authentication' not in values.auth.model_fields_set
                and 'require_authentication' not in values.oasis.model_fields_set
            ):
                logger.warning(
                    'Use auth.require_authentication=True if you want to require '
                    'authentication'
                )
                values.auth.require_authentication = True

        # Fill in the default MolID cache path if not set
        molid_cache_path = values.molid.cache_path
        if molid_cache_path is None:
            molid_cache_path = os.path.join(values.fs.tmp, 'molid_cache.db')
            values.molid.cache_path = molid_cache_path

        return values

    def get_plugin_entry_point(self, id: str) -> EntryPointType:
        """Returns the plugin entry point with the given id. It will
        contain also any overrides included through nomad.yaml.

        Args:
            id: The entry point identifier. Use the identifier given in the plugin
            package pyproject.toml.
        """
        try:
            return self.plugins.entry_points.options[id]
        except KeyError:
            raise KeyError(
                f'Could not find plugin entry point with id "{id}". Make sure that '
                'the plugin package with an up-to-date pyproject.toml is installed and '
                'that you are using the entry point id given in the plugin '
                'pyproject.toml'
            )

    def load_plugins(self):
        """Used to lazy-load the plugins. We cannot instantiate the plugins
        during the initialization of the nomad.config package, because it may
        trigger circular dependency errors for plugins using the config
        (pkgutil.get_loader will run code in the package root __init__). Instead
        this function should be called to instantiate the plugins before the
        nomad application is started.
        """
        from nomad.config import _merge, _plugins
        from nomad.config.models.plugins import NORTHToolEntryPoint

        if self.plugins is None:

            def get_config(key):
                has_old = True
                value_new = None
                value_old = None
                try:
                    value_old = _plugins[key]
                except KeyError:
                    has_old = False
                try:
                    value_new = _plugins['entry_points'][key]
                except KeyError:
                    pass

                return value_old if has_old else value_new

            # Any plugins options defined at the 'plugin' level are merged with
            # the new values. 'plugins.include/exclude' will completely replace
            # the new values in 'plugin.entry_points.include/exclude'.
            entry_points_config = _plugins.setdefault('entry_points', {})
            entry_points_config['include'] = get_config('include')
            entry_points_config['exclude'] = get_config('exclude')
            entry_points_config['options'] = _merge(
                entry_points_config.get('options', {}), _plugins.get('options', {})
            )

            # Handle plugin entry_points (new plugin mechanism). NOTE: Due to
            # this issue: https://github.com/pypa/setuptools/issues/3649 we are
            # ignoring duplicate entry points. Use of set is avoided because it
            # does not have a fixed order: we want entry points to behave more
            # deterministically.
            plugin_entry_point_ids = set()
            plugin_entry_points = entry_points(group='nomad.plugin')
            plugin_packages = {}

            for entry_point in plugin_entry_points:
                key = entry_point.value
                if key in plugin_entry_point_ids:
                    continue
                package_name = entry_point.value.split('.', 1)[0].split(':', 1)[0]
                config_override = (
                    _plugins.get('entry_points', {}).get('options', {}).get(key, {})
                )
                if isinstance(config_override, BaseModel):
                    config_override = config_override.model_dump(exclude_none=True)
                config_override['id'] = key
                config_instance = entry_point.load()
                package_metadata = entry_point.dist.metadata
                url_list = package_metadata.get_all('Project-URL')  # type: ignore
                url_dict = {}
                for url in url_list or []:
                    name, value = url.split(',')
                    url_dict[name.lower()] = value.strip()
                if package_name not in plugin_packages:
                    plugin_package = PluginPackage(
                        name=package_name,
                        description=package_metadata.get('Summary'),  # type: ignore
                        version=entry_point.dist.version,
                        homepage=url_dict.get('homepage'),
                        documentation=url_dict.get('documentation'),
                        repository=url_dict.get('repository'),
                        entry_points=[key],
                    )
                    plugin_packages[package_name] = plugin_package
                else:
                    plugin_packages[package_name].entry_points.append(key)
                config_default = config_instance.dict(exclude_unset=True)
                config_default['plugin_package'] = package_name
                config_class = config_instance.__class__
                config_final = config_class.parse_obj(
                    _merge(config_default, config_override)
                )
                _plugins['entry_points']['options'][key] = config_final
                plugin_entry_point_ids.add(key)
            _plugins['plugin_packages'] = plugin_packages

            # Handle NORTH tools defined in nomad.yaml TODO: This can be removed when we
            # fully migrate to using entry points for the NORTH tools.
            for key, tool in self.north.tools.filtered_items():
                if key not in plugin_entry_point_ids:
                    _plugins['entry_points']['options'][key] = NORTHToolEntryPoint(
                        id=key, north_tool=tool
                    )
                    # If a list of includes is given, add the activated north tools into
                    # it.
                    if entry_points_config.get('include') is not None:
                        entry_points_config['include'].append(key)

            for key, plugin in list(_plugins['entry_points']['options'].items()):
                if key not in plugin_entry_point_ids:
                    if isinstance(plugin, dict):
                        # Handle new style plugins that are declared directly in nomad.yaml
                        if plugin.get('entry_point_type') and not plugin.get('id'):
                            plugin['id'] = key
                        # Config for unknown entry points should be ignored with a warning.
                        else:
                            _plugins['entry_points']['options'].pop(key, None)
                            logger.warning(
                                'Found configuration for non-installed plugin entry point '
                                f'"{key}" in plugins.entry_points.options. The configuration '
                                'will be ignored.'
                            )

            # Assign URL-safe identifiers to all entry points and check for collisions
            self._assign_url_safe_ids(_plugins['entry_points'])

            self.plugins = Plugins.model_validate(_plugins)

    def _assign_url_safe_ids(self, entry_points: dict) -> None:
        """Assigns URL-safe identifiers to all entry points.

        For each entry point, if a custom id_url_safe is provided, it is validated
        for URL-safety. Otherwise, one is generated from the entry point id.
        Also checks for collisions among all id_url_safe values.

        Args:
            entry_points: Dictionary of entry point id to entry point config.

        Raises:
            ValueError: If a custom id_url_safe is not URL-safe or if there are
                collisions among id_url_safe values.
        """
        url_safe_ids: dict[
            str, tuple[str, str]
        ] = {}  # Maps url_safe_id -> original entry_point_id

        # Iterate over all the activated entry points to assign URL-safe identifiers and
        # check for collisions
        for entry_point_id in EntryPoints.model_validate(entry_points).filtered_keys():
            config = entry_points.get('options', {}).get(entry_point_id)
            if not config:
                continue
            # Get id_url_safe, and plugin_type from config (dict or BaseModel)
            if isinstance(config, dict):
                custom_url_safe_id = config.get('id_url_safe')
                entry_point_type = config.get('entry_point_type')
            else:
                custom_url_safe_id = getattr(config, 'id_url_safe', None)
                entry_point_type = getattr(config, 'entry_point_type', None)

            # Assign the custom id, or generate it from the id. Custom id_url_safe is
            # validated by Pydantic field_validator
            if custom_url_safe_id:
                url_safe_id = custom_url_safe_id
            else:
                url_safe_id = quote(entry_point_id.replace(':', '-'), safe='')

            # Check for id collisions within the same entry point type
            if (
                url_safe_id in url_safe_ids
                and url_safe_ids[url_safe_id][1] == entry_point_type
            ):
                raise ValueError(
                    f'URL-safe identifier collision: "{url_safe_id}" is used by both '
                    f'entry points "{url_safe_ids[url_safe_id]}" and "{entry_point_id}". '
                    'Please provide a custom id_url_safe for one of them to resolve '
                    'the collision.'
                )
            url_safe_ids[url_safe_id] = (entry_point_id, entry_point_type)

            # Assign the url_safe_id to the config
            if isinstance(config, dict):
                config['id_url_safe'] = url_safe_id
            else:
                config.id_url_safe = url_safe_id

            # Assign the default mount prefix for internally served API entry points
            if entry_point_type == 'api':
                if isinstance(config, dict):
                    if not config.get('prefix') and not config.get('external_url'):
                        config['prefix'] = f'apis/{url_safe_id}'
                elif not config.prefix and not config.external_url:
                    config.prefix = f'apis/{url_safe_id}'
