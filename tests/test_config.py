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
import re
import threading
from typing import get_args

import pytest
import yaml
from pydantic import BaseModel, SecretStr, ValidationError

import nomad.config
from nomad.auth.scopes import _resolve_scopes
from nomad.config import CONFIG_ENV, load_config
from nomad.config.models import config as config_module
from nomad.config.models.config import (
    Auth,
    Config,
    NOMADFileSystem,
    Services,
    reset_target_fs_state,
)
from nomad.config.models.plugins import ParserEntryPoint, SchemaPackageEntryPoint
from nomad.utils import flatten_dict

from .utils import assert_log


def assert_dict_str_str(dct: dict):
    for k, v in dct.items():
        assert isinstance(k, str), f'key {k} is not a string'
        assert isinstance(v, str), f'value {v} is not a string'


def load_test_config(conf_yaml, conf_env, mockopen=None, monkeypatch=None):
    if conf_env:
        assert_dict_str_str(conf_env)
        monkeypatch.setattr('os.environ', conf_env)
    config_file = os.environ.get('NOMAD_CONFIG', 'nomad.yaml')
    if conf_yaml:
        mockopen.write(config_file, yaml.dump(conf_yaml))
        old = os.path.exists
        monkeypatch.setattr(
            'os.path.exists', lambda x: True if x == config_file else old(x)
        )
    return load_config()


def get_config_env(config):
    if not config:
        return {}
    return {
        f'NOMAD_{key.upper()}': value
        for key, value in flatten_dict(config, '_').items()
    }


def load_format(config, format):
    conf_yaml = config if format == 'yaml' else None
    conf_env = get_config_env(config) if format == 'env' else None
    return conf_yaml, conf_env


def assert_config(config, config_expected):
    flattened = flatten_dict(config_expected)
    for key, val in flattened.items():
        root = config
        for part in key.split('.'):
            if isinstance(root, dict):
                root = root[part]
            else:
                root = getattr(root, part)
        assert root == val


def test_config_file_change(mockopen, monkeypatch):
    """Tests that changing the config file path works."""
    conf_yaml = {'fs': {'public': 'test'}}
    conf_env = {'NOMAD_CONFIG': 'test.yaml'}
    config = load_test_config(conf_yaml, conf_env, mockopen, monkeypatch)
    assert_config(config, conf_yaml)


def _write_config(path, config_dict):
    with open(path, 'w') as file:
        file.write(yaml.dump(config_dict))
    return str(path)


@pytest.mark.parametrize('source', ['env', 'files'])
def test_config_multiple_files(source, tmp_path, monkeypatch):
    """
    Tests that several config files are merged in order, with later files overwriting
    earlier ones. They can be given through the environment or directly.
    """
    first = _write_config(
        tmp_path / 'nomad.yaml', {'fs': {'public': 'first', 'staging': 'first'}}
    )
    second = _write_config(tmp_path / 'nomad-dev.yaml', {'fs': {'public': 'second'}})

    if source == 'env':
        monkeypatch.setenv(CONFIG_ENV, os.pathsep.join([first, second]))
        config = load_config()
    else:
        monkeypatch.delenv(CONFIG_ENV, raising=False)
        config = load_config(files=[first, second])

    assert config.fs.public == 'second'
    assert config.fs.staging == 'first'


def test_config_missing_file_warns(tmp_path, monkeypatch):
    """A config file that does not exist is reported instead of silently ignored."""
    existing = _write_config(tmp_path / 'nomad.yaml', {'fs': {'public': 'ok'}})
    missing = str(tmp_path / 'does-not-exist.yaml')
    monkeypatch.setenv(CONFIG_ENV, os.pathsep.join([existing, missing]))

    warnings: list[str] = []
    monkeypatch.setattr(nomad.config.logger, 'warning', warnings.append)

    config = load_config()

    assert config.fs.public == 'ok'
    assert any(missing in warning for warning in warnings)


def test_config_section_is_not_a_mapping(tmp_path, monkeypatch):
    """
    A scalar given for a whole config section must produce a validation error that
    names the offending field, not an AttributeError from the extra field validator.
    """
    monkeypatch.setenv(
        'NOMAD_CONFIG', _write_config(tmp_path / 'nomad.yaml', {'north': 'not a dict'})
    )

    with pytest.raises(ValidationError) as exc_info:
        load_config()

    assert 'north' in str(exc_info.value)


@pytest.mark.parametrize(
    'config_dict',
    [
        pytest.param({'fs': {'public': 'test'}}, id='nested string'),
        pytest.param({'north': {'hub_ip': '1.2.3.4'}}, id='underscore in field name'),
    ],
)
@pytest.mark.parametrize('format', ['yaml', 'env'])
def test_config_success(config_dict, format, mockopen, monkeypatch):
    """Tests that config variables are correctly loaded."""
    conf_yaml, conf_env = load_format(config_dict, format)
    config_obj = load_test_config(conf_yaml, conf_env, mockopen, monkeypatch)
    assert_config(config_obj, config_dict)


@pytest.mark.parametrize(
    'config_dict, warning, formats_with_warning',
    [
        pytest.param(
            {'fs': {'does': 'not exist'}},
            'The following unsupported keys were found in the nomad configuration '
            '(nomad.yaml, defaults.yaml or environment variables): FS: "does"',
            ['yaml', 'env'],
            id='non-existing nested field',
        ),
        pytest.param(
            {'does': 'not exist'},
            'The following unsupported keys were found in the nomad configuration '
            '(nomad.yaml, defaults.yaml or environment variables): Config: "does"',
            ['yaml'],
            id='non-existing top-level field',
        ),
    ],
)
@pytest.mark.parametrize('format', ['yaml', 'env'])
def test_config_warning(
    config_dict,
    format,
    warning,
    formats_with_warning,
    log_output,
    mockopen,
    monkeypatch,
):
    """Tests that extra fields create a warning message."""
    conf_yaml, conf_env = load_format(config_dict, format)
    load_test_config(conf_yaml, conf_env, mockopen, monkeypatch)
    assert_log(
        log_output, 'WARNING', warning, negate=format not in formats_with_warning
    )


@pytest.mark.parametrize(
    'config_dict, error',
    [
        pytest.param(
            {'services': {'api_timeout': 'not_a_number'}},
            (
                '1 validation error for Config\nservices.api_timeout\n  '
                'Input should be a valid integer, unable to parse string as an '
                "integer [type=int_parsing, input_value='not_a_number', input_type=str]"
            ),
            id='invalid type',
        ),
    ],
)
@pytest.mark.parametrize('format', ['yaml', 'env'])
def test_config_error(config_dict, format, error, mockopen, monkeypatch):
    """Tests that validation errors raise exceptions."""
    conf_yaml, conf_env = load_format(config_dict, format)
    with pytest.raises(ValidationError, match=re.escape(error)):
        load_test_config(conf_yaml, conf_env, mockopen, monkeypatch)


@pytest.mark.parametrize(
    'conf_yaml, conf_env, value',
    [
        pytest.param(None, None, '.volumes/fs/public', id='default'),
        pytest.param(
            {'fs': {'public': 'yaml'}}, {}, 'yaml', id='yaml overrides default'
        ),
        pytest.param(
            None, {'NOMAD_FS_PUBLIC': 'env'}, 'env', id='env overrides default'
        ),
        pytest.param(
            {'fs': {'public': 'yaml'}},
            {'NOMAD_FS_PUBLIC': 'env'},
            'env',
            id='env overrides yaml and default',
        ),
    ],
)
def test_config_priority(conf_yaml, conf_env, value, mockopen, monkeypatch):
    """Tests that the priority between model defaults, yaml and environment
    variables is correctly handled."""
    config = load_test_config(conf_yaml, conf_env, mockopen, monkeypatch)
    assert config.fs.public == value


@pytest.mark.parametrize(
    'conf_yaml, conf_env, conf_expected',
    [
        pytest.param(
            {'services': {'api_host': 'example.com', 'api_port': 1234}},
            {'services': {'api_port': '4321', 'https': 'true'}},
            {'services': {'api_host': 'example.com', 'api_port': 4321, 'https': True}},
            id='dictionary: merges',
        ),
        pytest.param(
            {'oasis': {'allowed_users': ['a@x.yz', 'b@x.yz']}},
            {'oasis': {'allowed_users': '["c@x.yz", "d@x.yz"]'}},
            {'oasis': {'allowed_users': ['c@x.yz', 'd@x.yz']}},
            id='list: overrides',
        ),
        pytest.param(
            {'services': {'api_timeout': 100}},
            {'services': {'api_timeout': '200'}},
            {'services': {'api_timeout': 200}},
            id='scalar: overrides',
        ),
    ],
)
def test_config_merge(conf_yaml, conf_env, conf_expected, mockopen, monkeypatch):
    """Tests that configs are correctly merged: dictionaries should be merged,
    everything else overridden."""
    config = load_test_config(
        conf_yaml, get_config_env(conf_env), mockopen, monkeypatch
    )
    config.load_plugins()
    assert_config(config, conf_expected)


@pytest.mark.parametrize(
    'conf_yaml, conf_expected',
    [
        pytest.param(
            {
                'north': {'tools': {'include': []}},
                'plugins': {
                    'include': ['a'],
                    'exclude': ['a'],
                },
            },
            {'plugins': {'entry_points': {'include': ['a'], 'exclude': ['a']}}},
            id='only old values',
        ),
        pytest.param(
            {
                'north': {'tools': {'include': []}},
                'plugins': {
                    'entry_points': {'include': ['b'], 'exclude': ['b']},
                },
            },
            {'plugins': {'entry_points': {'include': ['b'], 'exclude': ['b']}}},
            id='only new values',
        ),
        pytest.param(
            {
                'north': {'tools': {'include': []}},
                'plugins': {
                    'include': ['a'],
                    'exclude': ['a'],
                    'entry_points': {'include': ['b'], 'exclude': ['b']},
                },
            },
            {'plugins': {'entry_points': {'include': ['a'], 'exclude': ['a']}}},
            id='old include and exclude have precedence: non-empty lists',
        ),
        pytest.param(
            {
                'north': {'tools': {'include': []}},
                'plugins': {
                    'include': [],
                    'exclude': [],
                    'entry_points': {'include': ['b'], 'exclude': ['b']},
                },
            },
            {'plugins': {'entry_points': {'include': [], 'exclude': []}}},
            id='old include and exclude have precedence: empty lists',
        ),
        pytest.param(
            {
                'north': {'tools': {'include': []}},
                'plugins': {
                    'include': None,
                    'exclude': None,
                    'entry_points': {'include': ['b'], 'exclude': ['b']},
                },
            },
            {'plugins': {'entry_points': {'include': None, 'exclude': None}}},
            id='old include and exclude have precedence: None',
        ),
        pytest.param(
            {
                'north': {'tools': {'include': []}},
                'plugins': {
                    'options': {
                        'electronicparsers:vasp_parser_entry_point': {
                            'mainfile_name_re': 'a'
                        }
                    },
                    'entry_points': {
                        'options': {
                            'electronicparsers:vasp_parser_entry_point': {
                                'mainfile_name_re': 'b'
                            }
                        }
                    },
                },
            },
            {
                'north': {'tools': {'include': []}},
                'plugins': {
                    'entry_points': {
                        'options': {
                            'electronicparsers:vasp_parser_entry_point': {
                                'mainfile_name_re': 'a',
                                'plugin_package': 'electronicparsers',
                            }
                        }
                    }
                },
            },
            id='old, new and default options are merged with old config having precendence over new values.',
        ),
    ],
)
def test_plugin_entry_points(conf_yaml, conf_expected, mockopen, monkeypatch):
    """Tests that any conflicts between old and new plugin configs are resolved
    correctly."""
    config = load_test_config(conf_yaml, None, mockopen, monkeypatch)
    config.load_plugins()
    assert_config(config, conf_expected)


def test_parser_plugins():
    config = load_config()
    config.load_plugins()
    parsers = [
        entry_point
        for entry_point in config.plugins.entry_points.options.values()
        if isinstance(entry_point, ParserEntryPoint)
    ]
    assert len(parsers) == 68


def test_plugin_polymorphism(mockopen, monkeypatch):
    plugins = {
        'plugins': {
            'options': {
                'schema': {
                    'entry_point_type': 'schema_package',
                    'name': 'test',
                    'plugin_package': 'runschema',
                },
                'parser': {
                    'entry_point_type': 'parser',
                    'name': 'parsers/abinit',
                    'plugin_package': 'electronicparsers',
                },
            }
        }
    }
    config = load_test_config(plugins, None, mockopen, monkeypatch)
    config.load_plugins()
    assert isinstance(
        config.plugins.entry_points.options['schema'], SchemaPackageEntryPoint
    )
    assert isinstance(config.plugins.entry_points.options['parser'], ParserEntryPoint)


def test_missing_plugin_config_warns_and_is_ignored(mockopen, monkeypatch):
    plugins = {
        'plugins': {
            'entry_points': {
                'options': {
                    'nomad_aitoolkit.apps:aitoolkit': {
                        'label': 'configured but not installed'
                    }
                }
            }
        }
    }
    messages = []
    monkeypatch.setattr(
        'nomad.config.models.config.logger.warning',
        messages.append,
    )

    config = load_test_config(plugins, None, mockopen, monkeypatch)

    config.load_plugins()

    assert 'nomad_aitoolkit.apps:aitoolkit' not in config.plugins.entry_points.options
    assert any(
        'Found configuration for non-installed plugin entry point '
        '"nomad_aitoolkit.apps:aitoolkit"' in message
        for message in messages
    )


@pytest.mark.parametrize(
    'conf_yaml, conf_expected',
    [
        pytest.param(
            None,
            {
                'uploads': {
                    'pagination': {
                        'page_size': 10,
                        'order_by': 'upload_create_time',
                        'order': 'desc',
                    },
                    'entries': {
                        'pagination': {
                            'page_size': 5,
                            'order_by': 'process_status',
                            'order': 'asc',
                        }
                    },
                }
            },
            id='default pagination values',
        ),
        pytest.param(
            {
                'uploads': {
                    'pagination': {
                        'page_size': 12,
                        'order_by': 'upload_id',
                        'order': 'asc',
                    },
                    'entries': {
                        'pagination': {
                            'page_size': 6,
                            'order_by': 'entry_id',
                            'order': 'desc',
                        }
                    },
                }
            },
            {
                'uploads': {
                    'pagination': {
                        'page_size': 12,
                        'order_by': 'upload_id',
                        'order': 'asc',
                    },
                    'entries': {
                        'pagination': {
                            'page_size': 6,
                            'order_by': 'entry_id',
                            'order': 'desc',
                        }
                    },
                }
            },
            id='all pagination values changed',
        ),
        pytest.param(
            {'projects': {'pagination': {'page_size': 12}}},
            {'uploads': {'pagination': {'page_size': 12}}},
            id='using alias projects',
        ),
    ],
)
def test_pagination(conf_yaml, conf_expected, mockopen, monkeypatch):
    config = load_test_config(conf_yaml, None, mockopen, monkeypatch)
    assert_config(config, conf_expected)


@pytest.mark.parametrize(
    'entry_point_id, custom_id_url_safe, expected_id_url_safe, should_raise',
    [
        pytest.param(
            'nomad_parser_vasp.parsers:VASPRunParser',
            None,
            'nomad_parser_vasp.parsers-VASPRunParser',
            False,
            id='auto-generate: typical entry point id',
        ),
        pytest.param(
            'simple_id',
            None,
            'simple_id',
            False,
            id='auto-generate: simple id unchanged',
        ),
        pytest.param(
            'some.entry:point',
            'my_custom_id',
            'my_custom_id',
            False,
            id='custom: valid with underscores',
        ),
        pytest.param(
            'some.entry:point',
            'my-custom-id',
            'my-custom-id',
            False,
            id='custom: valid with hyphens',
        ),
        pytest.param(
            'some.entry:point',
            'test%20string',
            'test%20string',
            False,
            id='custom: valid with percent-encoding',
        ),
        pytest.param(
            'some.entry:point',
            '_private_id',
            '_private_id',
            False,
            id='custom: valid starting with underscore',
        ),
        pytest.param(
            'some.entry:point',
            'id123',
            'id123',
            False,
            id='custom: valid with numbers',
        ),
        pytest.param(
            'some.entry:point',
            'valid.id',
            'valid.id',
            False,
            id='custom: valid with dots',
        ),
        pytest.param(
            'some.entry:point',
            'invalid id',
            None,
            True,
            id='custom: invalid with spaces',
        ),
        pytest.param(
            'some.entry:point',
            'invalid:id',
            None,
            True,
            id='custom: invalid with colons',
        ),
    ],
)
def test_id_url_safe(
    entry_point_id,
    custom_id_url_safe,
    expected_id_url_safe,
    should_raise,
    mockopen,
    monkeypatch,
):
    """Tests URL-safe identifier generation, validation, and assignment."""
    config_dict = {
        'plugins': {
            'options': {
                entry_point_id: {
                    'entry_point_type': 'parser',
                    'id_url_safe': custom_id_url_safe,
                }
            },
        }
    }
    conf_yaml, conf_env = load_format(config_dict, 'yaml')
    config = load_test_config(conf_yaml, conf_env, mockopen, monkeypatch)

    if should_raise:
        with pytest.raises(ValueError):
            config.load_plugins()
    else:
        config.load_plugins()
        assert (
            config.plugins.entry_points.options[entry_point_id].id_url_safe
            == expected_id_url_safe
        )


@pytest.mark.parametrize(
    'entry_point_config, expected_prefix',
    [
        pytest.param({}, 'apis/my_package-my_api', id='default-prefix'),
        pytest.param(
            {'id_url_safe': 'custom'}, 'apis/custom', id='default-prefix-custom-id'
        ),
        pytest.param({'prefix': '/my/api/'}, 'my/api', id='custom-prefix'),
        pytest.param(
            {'external_url': 'https://example.com/api'}, None, id='external-url'
        ),
    ],
)
def test_api_entry_point_prefix(
    entry_point_config, expected_prefix, mockopen, monkeypatch
):
    """Tests that API entry points get the default ``apis/{id_url_safe}`` prefix
    unless a custom prefix or an external_url is given."""
    config_dict = {
        'plugins': {
            'options': {
                'my_package:my_api': {
                    'entry_point_type': 'api',
                    **entry_point_config,
                }
            },
        }
    }
    conf_yaml, conf_env = load_format(config_dict, 'yaml')
    config = load_test_config(conf_yaml, conf_env, mockopen, monkeypatch)
    config.load_plugins()

    entry_point = config.plugins.entry_points.options['my_package:my_api']
    assert entry_point.prefix == expected_prefix


@pytest.mark.parametrize(
    'entry_points, collides',
    [
        pytest.param(
            {
                'options': {
                    'pkg.mod:Class': {
                        'id_url_safe': 'A',
                        'entry_point_type': 'schema_package',
                    },
                    'pkg-mod_Class': {
                        'id_url_safe': 'A',
                        'entry_point_type': 'schema_package',
                    },
                }
            },
            True,
            id='same custom id_url_safe and entry point type',
        ),
        pytest.param(
            {
                'options': {
                    'pkg.mod:Class': {
                        'id_url_safe': 'A',
                        'entry_point_type': 'parser',
                    },
                    'pkg-mod_Class': {
                        'id_url_safe': 'A',
                        'entry_point_type': 'schema_package',
                    },
                }
            },
            False,
            id='same custom id_url_safe, different entry point types',
        ),
        pytest.param(
            {
                'exclude': ['pkg.mod:Class'],
                'options': {
                    'pkg.mod:Class': {
                        'id_url_safe': 'A',
                        'entry_point_type': 'schema_package',
                    },
                    'pkg-mod_Class': {
                        'id_url_safe': 'A',
                        'entry_point_type': 'schema_package',
                    },
                },
            },
            False,
            id='no clash if inactive',
        ),
        pytest.param(
            {
                'exclude': ['pkg.mod:*'],
                'options': {
                    'pkg.mod:Class': {
                        'id_url_safe': 'A',
                        'entry_point_type': 'schema_package',
                    },
                    'pkg-mod_Class': {
                        'id_url_safe': 'A',
                        'entry_point_type': 'schema_package',
                    },
                },
            },
            False,
            id='no clash if wildcard excluded',
        ),
        pytest.param(
            {
                'include': ['pkg*'],
                'options': {
                    'pkg.mod:Class': {
                        'id_url_safe': 'A',
                        'entry_point_type': 'schema_package',
                    },
                    'pkg-mod_Class': {
                        'id_url_safe': 'A',
                        'entry_point_type': 'schema_package',
                    },
                },
            },
            True,
            id='clash if wildcard included',
        ),
    ],
)
def test_id_url_safe_collision(entry_points, collides, mockopen, monkeypatch):
    """Tests that URL-safe identifier collisions are detected."""
    config_dict = {
        'plugins': {
            'entry_points': entry_points,
        }
    }

    conf_yaml, conf_env = load_format(config_dict, 'yaml')
    config = load_test_config(conf_yaml, conf_env, mockopen, monkeypatch)

    if collides:
        with pytest.raises(ValueError):
            config.load_plugins()
    else:
        config.load_plugins()


@pytest.mark.parametrize(
    'conf_yaml, conf_expected',
    [
        pytest.param(
            {'keycloak': {'server_url': 'http://example.com/auth'}},
            {'keycloak': {'server_url': 'http://example.com/auth'}},
            id='keycloak-no-slash',
        ),
        pytest.param(
            {'keycloak': {'server_url': 'http://example.com/auth/'}},
            {'keycloak': {'server_url': 'http://example.com/auth'}},
            id='keycloak-with-slash',
        ),
    ],
)
def test_normalized_url(conf_yaml, conf_expected, mockopen, monkeypatch):
    config = load_test_config(conf_yaml, None, mockopen, monkeypatch)
    assert_config(config, conf_expected)


@pytest.mark.parametrize(
    'configured, expected_public, expected_prefix',
    [
        pytest.param('/', '/', '', id='root'),
        pytest.param('/develop', '/develop', '/develop', id='develop'),
        pytest.param('/prod/v1/', '/prod/v1', '/prod/v1', id='trailing-slash'),
    ],
)
def test_services_base_path_normalization(configured, expected_public, expected_prefix):
    services = Services(api_base_path=configured)

    assert services.api_base_path == expected_public
    assert services.route_prefix == expected_prefix
    assert services.join_path('api', 'v1') == f'{expected_prefix}/api/v1'
    assert services.join_path('/gui/') == f'{expected_prefix}/gui'


@pytest.mark.parametrize('configured', ['', 'develop', '/develop//v1'])
def test_services_base_path_rejects_malformed_paths(configured):
    with pytest.raises(ValidationError):
        Services(api_base_path=configured)


def test_credential_fields_are_excluded_from_serialization():
    credential_name = re.compile(
        r'(api_key|access_token|client_key|codec_key|crypt_key|password|private_token|secret|token)$'
    )
    visited = set()

    def check_model(model: BaseModel):
        model_type = type(model)
        if model_type in visited:
            return
        visited.add(model_type)

        for field_name, field in model_type.model_fields.items():
            if credential_name.search(field_name):
                assert field.exclude, (
                    f'{model_type.__name__}.{field_name} must set exclude=True'
                )
                assert field.annotation is SecretStr or SecretStr in get_args(
                    field.annotation
                ), f'{model_type.__name__}.{field_name} must use SecretStr'

            value = getattr(model, field_name)
            if isinstance(value, BaseModel):
                check_model(value)

    check_model(Config())


def test_model_dump_excludes_credentials():
    config = Config(
        services={'api_secret': 'services-secret-that-is-at-least-32-bytes'},
        north={
            'jupyterhub_crypt_key': 'north-crypt-key',
            'hub_service_api_token': 'north-api-token',
        },
        elastic={'password': 'elastic-password'},
        temporal={
            'api_key': 'temporal-api-key',
            'payload_codec_key': 'payload-codec-key-that-is-32-bytes',
            'tls_client_key': 'temporal-client-key',
            'oidc': {'client_secret': 'temporal-oidc-secret'},
        },
        keycloak={
            'password': 'keycloak-password',
            'client_secret': 'keycloak-client-secret',
        },
        mongo={'password': 'mongo-password'},
        mail={'password': 'mail-password'},
        client={'password': 'client-password', 'access_token': 'client-token'},
        datacite={'password': 'datacite-password'},
        gitlab={'private_token': 'gitlab-token'},
        rfc3161_timestamp={'password': 'timestamp-password'},
    )

    dumped = config.model_dump()
    excluded_paths = [
        ('services', 'api_secret'),
        ('north', 'jupyterhub_crypt_key'),
        ('north', 'hub_service_api_token'),
        ('elastic', 'password'),
        ('temporal', 'api_key'),
        ('temporal', 'payload_codec_key'),
        ('temporal', 'tls_client_key'),
        ('temporal', 'oidc', 'client_secret'),
        ('keycloak', 'password'),
        ('keycloak', 'client_secret'),
        ('mongo', 'password'),
        ('mail', 'password'),
        ('client', 'password'),
        ('client', 'access_token'),
        ('datacite', 'password'),
        ('gitlab', 'private_token'),
        ('rfc3161_timestamp', 'password'),
    ]

    for *parents, field_name in excluded_paths:
        section = dumped
        for parent in parents:
            section = section[parent]
        assert field_name not in section

    # Exclusion only affects serialization, not internal consumers.
    assert (
        config.services.api_secret.get_secret_value()
        == 'services-secret-that-is-at-least-32-bytes'
    )
    assert str(config.services.api_secret) == '**********'


@pytest.mark.parametrize(
    'configured, api_url, gui_url, north_url, hub_url',
    [
        pytest.param(
            '/',
            'https://nomad.example/api',
            'https://nomad.example/gui',
            'https://north.example:9000/north',
            'http://north.example:9000/north/hub',
            id='root',
        ),
        pytest.param(
            '/develop',
            'https://nomad.example/develop/api',
            'https://nomad.example/develop/gui',
            'https://north.example:9000/develop/north',
            'http://north.example:9000/develop/north/hub',
            id='develop',
        ),
        pytest.param(
            '/prod/v1/',
            'https://nomad.example/prod/v1/api',
            'https://nomad.example/prod/v1/gui',
            'https://north.example:9000/prod/v1/north',
            'http://north.example:9000/prod/v1/north/hub',
            id='prod',
        ),
    ],
)
def test_config_urls_use_normalized_base_path(
    configured, api_url, gui_url, north_url, hub_url
):
    config = Config(
        services=Services(
            api_base_path=configured,
            api_host='nomad.example',
            api_port=443,
            https=True,
        ),
        north={'hub_host': 'north.example', 'hub_port': 9000},
    )

    assert config.api_url() == api_url
    assert config.gui_url() == gui_url
    assert config.gui_url('search/entries') == f'{gui_url}/search/entries'
    assert config.north_url() == north_url
    assert config.hub_url() == hub_url
    assert (
        config.ui.app_base == f'https://nomad.example:443{config.services.route_prefix}'
    )
    assert config.ui.north_base == north_url


# Tests for `Auth`

auth_scope_cases = [
    pytest.param(
        {},
        _resolve_scopes({'*:*'}),
        id='empty-dict-gives-all',
    ),
    pytest.param(
        {'include': ['*:read']},
        _resolve_scopes({'*:read'}),
        id='include-only',
    ),
    pytest.param(
        {'exclude': ['*:read']},
        _resolve_scopes({'*:*'}) - _resolve_scopes({'*:read'}),
        id='exclude-only',
    ),
    pytest.param(
        {'include': ['*:*'], 'exclude': ['tokens:*']},
        _resolve_scopes({'*:*'}) - _resolve_scopes({'tokens:*'}),
        id='include-and-exclude',
    ),
    pytest.param(
        {'include': ['*:*'], 'exclude': ['*:*']},
        set(),
        id='equal-include-exclude-all',
    ),
    pytest.param(
        {'include': ['tokens:*'], 'exclude': ['tokens:*']},
        set(),
        id='equal-include-exclude_specific',
    ),
    pytest.param(
        {'include': [], 'exclude': ['tokens:*']},
        set(),
        id='empty-include',
    ),
]


@pytest.mark.parametrize('scopes, expected', auth_scope_cases)
def test_unauthenticated_user_scopes_resolved(scopes, expected):
    auth = Auth.model_validate({'unauthenticated_user_scopes': scopes})
    assert auth.unauthenticated_user_scopes_resolved == expected


@pytest.mark.parametrize('scopes, expected', auth_scope_cases)
def test_unauthorized_user_scopes_resolved(scopes, expected):
    auth = Auth.model_validate({'unauthorized_user_scopes': scopes})
    assert auth.unauthorized_user_scopes_resolved == expected


@pytest.mark.parametrize(
    ('conf_yaml', 'conf_expected'),
    [
        pytest.param(
            {'auth': {'authorized_users': None}},
            {'auth': {'authorized_users': None}},
            id='none',
        ),
        pytest.param(
            {'auth': {'authorized_users': []}},
            {'auth': {'authorized_users': []}},
            id='empty',
        ),
        pytest.param(
            {'auth': {'authorized_users': ['alice', 'bob@example.com']}},
            {'auth': {'authorized_users': ['alice', 'bob@example.com']}},
            id='already-normalized',
        ),
        pytest.param(
            {
                'auth': {
                    'authorized_users': [' Alice ', 'BOB@example.com ', '  CHARLIE  ']
                }
            },
            {'auth': {'authorized_users': ['alice', 'bob@example.com', 'charlie']}},
            id='strip-and-lower',
        ),
        pytest.param(
            {'auth': {'authorized_users': ['Alice', 'alice ', ' ALICE']}},
            {'auth': {'authorized_users': ['alice']}},
            id='deduplicated-after-normalization',
        ),
    ],
)
def test_authorized_users(conf_yaml, conf_expected, mockopen, monkeypatch):
    config = load_test_config(conf_yaml, None, mockopen, monkeypatch)
    assert_config(config, conf_expected)


def test_authorized_users_email_whitelist_logs_deprecation_warning(monkeypatch):
    messages = []

    monkeypatch.setattr(config_module.logger, 'warning', messages.append)

    auth = Auth.model_validate(
        {'authorized_users': ['alice@example.com', 'Alice', 'alice@example.com']}
    )

    assert auth.authorized_users == ['alice@example.com', 'alice']
    assert messages == [
        'whitelisting users with email is deprecated, please use username instead.'
    ]


@pytest.mark.parametrize(
    'conf_env, conf_expected',
    [
        pytest.param(
            {'NOMAD_OASIS_ALLOWED_USERS': '["a@x.yz", "b@x.yz"]'},
            {'oasis': {'allowed_users': ['a@x.yz', 'b@x.yz']}},
            id='import-list',
        ),
        pytest.param(
            {
                'NOMAD_SERVICES_HTTPS': 'true',
                'NOMAD_SERVICES_HTTPS_UPLOAD': 'True',
                'NOMAD_SERVICES_FORCE_RAW_FILE_DECODING': 'on',
                'NOMAD_SERVICES_OPTIMADE_ENABLED': 'false',
                'NOMAD_SERVICES_DCAT_ENABLED': 'off',
                'NOMAD_SERVICES_H5GROVE_ENABLED': '0',
            },
            {
                'services': {
                    'https': True,
                    'https_upload': True,
                    'force_raw_file_decoding': True,
                    'optimade_enabled': False,
                    'dcat_enabled': False,
                    'h5grove_enabled': False,
                }
            },
            id='boolean-versions',
        ),
        pytest.param(
            {'NOMAD_ELASTIC_VERSION': '"7"'},
            {'elastic': {'version': '7'}},
            id='string-literal-version',
        ),
        pytest.param(
            {'NOMAD_ELASTIC_SCHEME': '"https"'},
            {'elastic': {'scheme': 'https'}},
            id='string-literal-scheme',
        ),
    ],
)
def test_json_values(conf_env, conf_expected, monkeypatch):
    config = load_test_config({}, conf_env, monkeypatch=monkeypatch)
    assert_config(config, conf_expected)


@pytest.mark.parametrize(
    'conf_yaml, conf_expected',
    [
        # In previous version, `allowed_users` would imply `require_authentication`
        pytest.param(
            {'oasis': {'allowed_users': ['alice']}},
            {
                'auth': {
                    'authorized_users': ['alice'],
                    'require_authentication': True,
                }
            },
            id='deprecated-allowed-users-implies-authentication',
        ),
        # Ensure deprecated `require_authentication` still works with and without `allowed_users`
        pytest.param(
            {'oasis': {'allowed_users': ['alice'], 'require_authentication': True}},
            {
                'auth': {
                    'authorized_users': ['alice'],
                    'require_authentication': True,
                }
            },
            id='deprecated-allowed-users-implies-authentication-overwrite-true',
        ),
        pytest.param(
            {'oasis': {'allowed_users': ['alice'], 'require_authentication': False}},
            {
                'auth': {
                    'authorized_users': ['alice'],
                    'require_authentication': False,
                }
            },
            id='deprecated-allowed-users-implies-authentication-overwrite-false',
        ),
        pytest.param(
            {'oasis': {'require_authentication': True}},
            {'auth': {'require_authentication': True}},
            id='deprecated-require-authentication-true',
        ),
        pytest.param(
            {'oasis': {'require_authentication': False}},
            {'auth': {'require_authentication': False}},
            id='deprecated-require-authentication-false',
        ),
        # Ensure implied `require_authentication` could be explicitly overwritten
        pytest.param(
            {
                'oasis': {'allowed_users': ['alice']},
                'auth': {'require_authentication': False},
            },
            {
                'auth': {
                    'authorized_users': ['alice'],
                    'require_authentication': False,
                }
            },
            id='explicit-auth-require-authentication-false',
        ),
        pytest.param(
            {
                'oasis': {'allowed_users': ['alice']},
                'auth': {'require_authentication': True},
            },
            {
                'auth': {
                    'authorized_users': ['alice'],
                    'require_authentication': True,
                }
            },
            id='explicit-auth-require-authentication-true',
        ),
    ],
)
def test_oasis_allowed_users_backwards_compatibility(
    conf_yaml, conf_expected, mockopen, monkeypatch
):
    config = load_test_config(conf_yaml, None, mockopen, monkeypatch)
    assert_config(config, conf_expected)


@pytest.mark.parametrize(
    'conf_yaml, error',
    [
        pytest.param(
            {
                'oasis': {'require_authentication': True},
                'auth': {'require_authentication': False},
            },
            'You cannot use new and deprecated `require_authentication` together',
            id='require-authentication',
        ),
        pytest.param(
            {
                'oasis': {'allowed_users': ['alice']},
                'auth': {'authorized_users': ['alice']},
            },
            'You cannot use new and deprecated user whitelist together',
            id='user-whitelist',
        ),
    ],
)
def test_oasis_auth_backwards_compatibility_conflicts(
    conf_yaml, error, mockopen, monkeypatch
):
    with pytest.raises(ValidationError, match=re.escape(error)):
        load_test_config(conf_yaml, None, mockopen, monkeypatch)


@pytest.mark.parametrize(
    'conf_yaml, expected_warnings',
    [
        pytest.param(
            {'oasis': {'require_authentication': True}},
            ['Use auth.require_authentication instead of oasis.require_authentication'],
            id='deprecated-require-authentication-warning',
        ),
        pytest.param(
            {'oasis': {'allowed_users': ['alice']}},
            [
                'Use auth.authorized_users instead of oasis.allowed_users',
                'Use auth.require_authentication=True if you want to require authentication',
            ],
            id='deprecated-allowed-users-warnings',
        ),
    ],
)
def test_oasis_auth_backwards_compatibility_warnings(
    conf_yaml, expected_warnings, mockopen, monkeypatch
):
    messages = []
    monkeypatch.setattr(
        'nomad.config.models.config.logger.warning',
        messages.append,
    )

    load_test_config(conf_yaml, None, mockopen, monkeypatch)

    for expected_warning in expected_warnings:
        assert expected_warning in messages


class _CountingFileSystem:
    """Minimal fsspec stand-in that records ``makedirs`` calls."""

    def __init__(self):
        self.makedirs_calls: list[tuple[str, bool]] = []
        self.fail_makedirs = False

    def makedirs(self, path, exist_ok=False):
        self.makedirs_calls.append((path, exist_ok))
        if self.fail_makedirs:
            raise OSError('bucket ensure failed')


def test_target_fs_ensures_bucket_once_per_connection(monkeypatch):
    """``makedirs`` runs once per protocol+bucket+extra in a process, and retries on error."""
    reset_target_fs_state()
    fake_fs = _CountingFileSystem()
    monkeypatch.setattr(
        'nomad.config.models.config.filesystem', lambda protocol, **kwargs: fake_fs
    )

    public_fs = NOMADFileSystem(
        protocol='s3',
        bucket='bucket-a',
        extra={'endpoint_url': 'http://localhost:8333'},
    )

    assert public_fs.target_fs is fake_fs
    assert fake_fs.makedirs_calls == [('bucket-a', True)]

    fake_fs.makedirs_calls.clear()
    public_fs.target_fs
    public_fs.target_fs
    assert fake_fs.makedirs_calls == []

    public_fs.bucket = 'bucket-b'
    public_fs.target_fs
    assert fake_fs.makedirs_calls == [('bucket-b', True)]

    fake_fs.makedirs_calls.clear()
    fake_fs.fail_makedirs = True
    public_fs.bucket = 'bucket-c'
    with pytest.raises(
        RuntimeError,
        match='Cannot establish valid connection to the target file system.',
    ) as exc_info:
        public_fs.target_fs
    assert isinstance(exc_info.value.__cause__, OSError)
    assert fake_fs.makedirs_calls == [('bucket-c', True)]

    fake_fs.makedirs_calls.clear()
    with pytest.raises(
        RuntimeError,
        match='Cannot establish valid connection to the target file system.',
    ):
        public_fs.target_fs
    assert fake_fs.makedirs_calls == [('bucket-c', True)]

    fake_fs.makedirs_calls.clear()
    fake_fs.fail_makedirs = False
    public_fs.target_fs
    assert fake_fs.makedirs_calls == [('bucket-c', True)]
    fake_fs.makedirs_calls.clear()
    public_fs.target_fs
    assert fake_fs.makedirs_calls == []

    reset_target_fs_state()


def test_target_fs_makedirs_does_not_block_other_buckets(monkeypatch):
    """A stalled ``makedirs`` for one bucket must not serialise another key."""
    reset_target_fs_state()
    entered = threading.Event()
    release = threading.Event()

    class BlockingFileSystem(_CountingFileSystem):
        def makedirs(self, path, exist_ok=False):
            if path == 'bucket-block':
                entered.set()
                if not release.wait(timeout=2):
                    raise TimeoutError('release never set')
            super().makedirs(path, exist_ok=exist_ok)

    fake_fs = BlockingFileSystem()
    monkeypatch.setattr(
        'nomad.config.models.config.filesystem', lambda protocol, **kwargs: fake_fs
    )

    blocked = NOMADFileSystem(
        protocol='s3',
        bucket='bucket-block',
        extra={'endpoint_url': 'http://localhost:8333'},
    )
    other = NOMADFileSystem(
        protocol='s3',
        bucket='bucket-other',
        extra={'endpoint_url': 'http://localhost:8333'},
    )
    errors: list[Exception] = []

    def first_caller():
        try:
            blocked.target_fs
        except Exception as exc:
            errors.append(exc)

    def second_caller():
        try:
            if not entered.wait(timeout=2):
                raise TimeoutError('blocked makedirs never started')
            other.target_fs
        except Exception as exc:
            errors.append(exc)
        finally:
            release.set()

    first = threading.Thread(target=first_caller)
    second = threading.Thread(target=second_caller)
    first.start()
    second.start()
    first.join(timeout=3)
    second.join(timeout=3)
    assert errors == []
    assert not first.is_alive()
    assert not second.is_alive()
    assert fake_fs.makedirs_calls == [
        ('bucket-other', True),
        ('bucket-block', True),
    ]

    reset_target_fs_state()


@pytest.mark.parametrize(
    ('kwargs', 'expected'),
    [
        ({}, 'local_only'),
        ({'protocol': 's3'}, 'remote_only'),
        ({'write_mode': 'local_then_remote'}, 'local_then_remote'),
        ({'protocol': 's3', 'write_mode': 'local_only'}, 'local_only'),
        ({'protocol': 's3', 'write_mode': 'local_then_remote'}, 'local_then_remote'),
        ({'write_mode': 'remote_only'}, 'remote_only'),
    ],
)
def test_resolved_write_mode(kwargs, expected):
    public_fs = NOMADFileSystem(**kwargs)
    assert public_fs.resolved_write_mode == expected
