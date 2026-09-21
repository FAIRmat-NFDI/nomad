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

"""Secret-safe serialization for persisted action payloads."""

from dataclasses import asdict, is_dataclass
from typing import Any

from pydantic import BaseModel, SecretBytes, SecretStr


def _to_dict(data: Any) -> dict:
    """Convert data to a dictionary without persisting Pydantic secrets."""

    def _secret_exclusions(val: Any) -> Any:
        """Build a Pydantic exclusion tree from values before serialization."""
        if isinstance(val, (SecretStr, SecretBytes)):
            return True
        if isinstance(val, BaseModel):
            model_exclusions: dict[Any, Any] = {}
            for field_name in type(val).model_fields:
                exclusion = _secret_exclusions(getattr(val, field_name))
                if exclusion:
                    model_exclusions[field_name] = exclusion
            for field_name, field_value in (val.model_extra or {}).items():
                exclusion = _secret_exclusions(field_value)
                if exclusion:
                    model_exclusions[field_name] = exclusion
            return model_exclusions or None
        if isinstance(val, dict):
            dict_exclusions: dict[Any, Any] = {}
            for key, item in val.items():
                exclusion = _secret_exclusions(item)
                if exclusion:
                    dict_exclusions[key] = exclusion
            return dict_exclusions or None
        if isinstance(val, (list, tuple, set)):
            sequence_exclusions: dict[Any, Any] = {}
            for index, item in enumerate(val):
                exclusion = _secret_exclusions(item)
                if exclusion:
                    sequence_exclusions[index] = exclusion
            return sequence_exclusions or None
        return None

    def _remove_secrets(val: Any) -> Any:
        """Recursively remove secrets while preserving normal serialization."""
        if isinstance(val, (SecretStr, SecretBytes)):
            return None
        if isinstance(val, BaseModel):
            serialized = val.model_dump(by_alias=True, exclude=_secret_exclusions(val))
            return _remove_secrets(serialized)
        if isinstance(val, dict):
            new_data = {}
            for k, v in val.items():
                if isinstance(v, (SecretStr, SecretBytes)):
                    continue
                new_data[k] = _remove_secrets(v)
            return new_data
        if isinstance(val, list):
            return [
                _remove_secrets(item)
                for item in val
                if not isinstance(item, (SecretStr, SecretBytes))
            ]
        if isinstance(val, tuple):
            return tuple(
                _remove_secrets(item)
                for item in val
                if not isinstance(item, (SecretStr, SecretBytes))
            )
        if isinstance(val, set):
            return {
                _remove_secrets(item)
                for item in val
                if not isinstance(item, (SecretStr, SecretBytes))
            }
        return val

    if isinstance(data, BaseModel):
        return _remove_secrets(data)
    if is_dataclass(data) and not isinstance(data, type):
        return _remove_secrets(asdict(data))
    if isinstance(data, dict):
        return _remove_secrets(data)
    raise TypeError(f'Unsupported type: {type(data)}')


def serialize_payload(data: Any) -> Any:
    """Serialize results and signal inputs without bypassing secret redaction."""
    if isinstance(data, (SecretStr, SecretBytes)):
        return None
    if isinstance(data, (list, tuple)):
        return [
            serialize_payload(item)
            for item in data
            if not isinstance(item, (SecretStr, SecretBytes))
        ]
    try:
        return _to_dict(data)
    except TypeError:
        return (
            data if isinstance(data, (int, float, bool, str, type(None))) else str(data)
        )
