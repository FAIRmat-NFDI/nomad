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

"""Compatibility helpers for the Elasticsearch 7 and 9 Python clients.

NOMAD uses the Elasticsearch 9 DSL as a common query and mapping builder.  The
low-level transport is selected at runtime so installations can temporarily
continue to use an Elasticsearch 7 cluster while migrating to 9.  The two
officially named clients are intentionally kept behind this module; callers
should not import either transport directly.
"""

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Literal

from elasticsearch.exceptions import ApiError as Elasticsearch9ApiError
from elasticsearch.exceptions import ConflictError as Elasticsearch9ConflictError
from elasticsearch.exceptions import NotFoundError as Elasticsearch9NotFoundError
from elasticsearch.exceptions import RequestError as Elasticsearch9RequestError
from elasticsearch.exceptions import TransportError as Elasticsearch9TransportError

_elasticsearch7_exceptions: Any
try:
    import elasticsearch7.exceptions as _elasticsearch7_exceptions
except ImportError:  # pragma: no cover - only relevant to incomplete installations
    _elasticsearch7_exceptions = None

Elasticsearch7ConflictError = getattr(_elasticsearch7_exceptions, 'ConflictError', None)
Elasticsearch7NotFoundError = getattr(_elasticsearch7_exceptions, 'NotFoundError', None)
Elasticsearch7RequestError = getattr(_elasticsearch7_exceptions, 'RequestError', None)
Elasticsearch7TransportError = getattr(
    _elasticsearch7_exceptions, 'TransportError', None
)

from nomad.config import config

if TYPE_CHECKING:
    from elasticsearch.dsl import Search


ElasticVersion = Literal['7', '9']


def _exception_types(*types) -> tuple[type[Exception], ...]:
    """Return the matching exception classes without duplicate entries."""
    return tuple(dict.fromkeys(t for t in types if t is not None))


TRANSPORT_ERROR_TYPES = _exception_types(
    Elasticsearch9ApiError,
    Elasticsearch9TransportError,
    Elasticsearch7TransportError,
)
REQUEST_ERROR_TYPES = _exception_types(
    Elasticsearch9RequestError, Elasticsearch7RequestError
)
CONFLICT_ERROR_TYPES = _exception_types(
    Elasticsearch9ConflictError, Elasticsearch7ConflictError
)
NOT_FOUND_ERROR_TYPES = _exception_types(
    Elasticsearch9NotFoundError, Elasticsearch7NotFoundError
)

# Backwards-friendly names for code that wants to use the selected exception
# family in an ``except`` clause.  They are tuples because both clients expose
# distinct exception classes.
TransportError = TRANSPORT_ERROR_TYPES
RequestError = REQUEST_ERROR_TYPES
ConflictError = CONFLICT_ERROR_TYPES
NotFoundError = NOT_FOUND_ERROR_TYPES
ApiError = Elasticsearch9ApiError

_es7_warning_emitted = False


def configured_version() -> ElasticVersion:
    """Return and validate the configured Elasticsearch major version."""
    value = str(config.elastic.version)
    if value not in ('7', '9'):
        raise ValueError(
            f'Unsupported Elasticsearch version {value!r}; expected "7" or "9".'
        )
    return value  # type: ignore[return-value]


def _warn_if_es7(version: ElasticVersion) -> None:
    """Log the ES7 deprecation once for each Python process."""
    global _es7_warning_emitted
    if version != '7' or _es7_warning_emitted:
        return

    # Use NOMAD's structured logger rather than ``warnings.warn``.  The config
    # module intentionally filters ordinary DeprecationWarning instances, and
    # operators must still see this migration warning in service logs.
    from nomad.utils.structlogging import get_logger

    get_logger(__name__).warning(
        'elasticsearch_v7_deprecated',
        configured_version='7',
        message=(
            'Elasticsearch 7 support is deprecated and will be removed in a '
            'future NOMAD release. Configure Elasticsearch 9 before then.'
        ),
    )
    _es7_warning_emitted = True


def create_elastic_client(*, verify: bool = True):
    """Create the configured official Elasticsearch low-level client.

    Args:
        verify: When true, perform an ``info`` request and reject a cluster whose
            major version does not match the configured client. Tests and callers
            that only need construction can disable this check.
    """
    version = configured_version()
    _warn_if_es7(version)

    http_auth = None
    if config.elastic.username and config.elastic.password:
        http_auth = (
            config.elastic.username,
            config.elastic.password.get_secret_value(),
        )

    # Elasticsearch 9 requires a URL scheme (`http://host:port`). The ES7 client
    # accepted `host:port` and inferred http; we always include the configured
    # scheme so both clients get a valid URL.
    hosts = [f'{config.elastic.scheme}://{config.elastic.host}:{config.elastic.port}']
    common_kwargs: dict[str, Any] = {
        'hosts': hosts,
        'max_retries': 10,
        'retry_on_timeout': True,
    }
    client: Any

    if version == '7':
        # ``elasticsearch7`` is the official co-installable ES7 transport. Its
        # client uses the legacy ``timeout`` and ``http_auth`` parameter names.
        import elasticsearch7

        client = elasticsearch7.Elasticsearch(
            **common_kwargs,
            timeout=config.elastic.timeout,
            http_auth=http_auth,
        )
    else:
        import elasticsearch

        client = elasticsearch.Elasticsearch(
            **common_kwargs,
            request_timeout=config.elastic.timeout,
            basic_auth=http_auth,
        )

    if verify:
        verify_server_version(client, version)
    return client


def _response_body(response: Any) -> Any:
    """Normalize ES7 dictionaries and ES9 ``ObjectApiResponse`` instances."""
    return getattr(response, 'body', response)


def verify_server_version(client: Any, expected: ElasticVersion | None = None) -> str:
    """Verify that the connected cluster has the configured major version.

    Both clients return the version payload in a dictionary, although ES9 wraps
    it in ``ObjectApiResponse``.  A malformed ``info`` response is treated as a
    configuration error instead of silently allowing a mismatched cluster.
    """
    expected = expected or configured_version()
    info = _response_body(client.info())
    version_number = None
    if isinstance(info, Mapping):
        version_data = info.get('version')
        if isinstance(version_data, Mapping):
            version_number = version_data.get('number')

    if not version_number:
        raise RuntimeError(
            'Could not determine the Elasticsearch server version from client.info().'
        )

    server_version = str(version_number)
    server_major = server_version.split('.', 1)[0]
    if server_major != expected:
        raise RuntimeError(
            f'Elasticsearch version {expected} is configured, but the '
            f'connected server reports version {server_version}.'
        )
    return server_version


def get_elastic_client():
    """Return NOMAD's initialized low-level client."""
    from nomad import infrastructure

    if infrastructure.elastic_client is None:
        raise RuntimeError(
            'Elasticsearch has not been initialized. Call infrastructure.setup_elastic() first.'
        )
    return infrastructure.elastic_client


def bulk(client: Any, actions: Iterable[Any], *args, **kwargs):
    """Run the version-appropriate official bulk helper."""
    if configured_version() == '7':
        import elasticsearch7.helpers

        return elasticsearch7.helpers.bulk(client, actions, *args, **kwargs)

    import elasticsearch.helpers

    return elasticsearch.helpers.bulk(client, actions, *args, **kwargs)


def execute_search(search: 'Search', *, ignore_cache: bool = False):
    """Execute an ES9 DSL ``Search`` using NOMAD's selected transport.

    The ES9 DSL assumes its connection returns an object with a ``body``
    attribute. ES7 returns a plain dictionary, so this function normalizes the
    response before constructing the shared DSL response object. It also
    preserves the DSL's response cache semantics.
    """
    if ignore_cache or not hasattr(search, '_response'):
        client = get_elastic_client()
        response = client.search(
            index=search._index,
            body=search.to_dict(),
            **search._params,
        )
        response_class = search._response_class
        search._response = response_class(search, _response_body(response))
    return search._response


def delete_search(search: 'Search'):
    """Delete documents matching a DSL ``Search`` using the selected transport."""
    return get_elastic_client().delete_by_query(
        index=search._index,
        body=search.to_dict(),
        **search._params,
    )
