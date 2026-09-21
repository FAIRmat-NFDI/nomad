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

"""Action plugin registry and schema adapter."""

import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import BaseModel, TypeAdapter

from nomad.actions.action import get_actions
from nomad.actions.domain import ActionDefinition
from nomad.actions.models import ActionSchemaInfo


def _validate_with_pydantic(func: Callable, arg, entry_point: Any = None):
    """
    Validate the single argument of a function against its type hint using Pydantic.

    Args:
        func: The function with the argument to validate.
        arg: The argument to validate.
        entry_point: Optional action entry point containing active configuration.

    Returns:
        The validated argument.
    """
    hints = get_type_hints(func)

    # get the single non-return annotation
    [(_, param_type)] = [(n, t) for n, t in hints.items() if n != 'return']

    if entry_point is not None and hasattr(param_type, 'get_schema_for_entry_point'):
        param_type = param_type.get_schema_for_entry_point(entry_point)

    adapter = TypeAdapter(param_type)
    return adapter.validate_python(arg)


def _get_non_self_params(func: Callable) -> list[inspect.Parameter]:
    """Return callable parameters excluding a leading ``self`` parameter."""

    sig = inspect.signature(func)  # type: ignore[arg-type]
    return [param for param in sig.parameters.values() if param.name != 'self']


def _get_param_schema(func: Callable, entry_point: Any = None) -> dict[str, Any]:
    """
    Generate a JSON Schema for the single argument of a function.

    This is useful for generating frontend forms for actions.

    Args:
        func: The function with the argument to generate the schema for.
        entry_point: Optional action entry point containing active configuration.

    Returns:
        The JSON schema for the argument.
    """
    hints = get_type_hints(func)

    # get the single non-return annotation
    [(_, param_type)] = [(n, t) for n, t in hints.items() if n != 'return']

    if isinstance(param_type, type) and issubclass(param_type, BaseModel):
        if entry_point is not None and hasattr(
            param_type, 'get_schema_for_entry_point'
        ):
            param_type = param_type.get_schema_for_entry_point(entry_point)
        schema = param_type.model_json_schema(by_alias=True)
    else:
        adapter = TypeAdapter(param_type)
        schema = adapter.json_schema()

    # remove the user_id from the schema,
    # we rely on the user_id of the logged in user instead of form input.
    schema.get('properties', {}).pop('user_id', None)
    required = schema.get('required', [])
    if 'user_id' in required:
        required.remove('user_id')
    return schema


def _get_signal_schema(signal_fn: Callable) -> dict[str, Any]:
    """
    Generate a JSON Schema for the single argument of a signal function.
    Raises ValueError if there is more than one argument (excluding 'self').
    """
    params = _get_non_self_params(signal_fn)

    if len(params) > 1:
        name_str = getattr(signal_fn, '__name__', str(signal_fn))
        raise ValueError(
            f'Signal {name_str} has more than one argument. Only zero or one arguments are supported.'
        )

    if len(params) == 0:
        return {}

    hints = get_type_hints(signal_fn)
    param_type = hints.get(params[0].name, Any)

    if isinstance(param_type, type) and issubclass(param_type, BaseModel):
        return param_type.model_json_schema(by_alias=True)
    else:
        adapter = TypeAdapter(param_type)
        return adapter.json_schema()


def validate_action_arg(action_id: str, arg: Any):
    """
    Validate the argument for a given action's `workflow.run` function
    against its type hint. Raises if the action does not exist or the
    argument is invalid.
    """
    action = get_actions().get(action_id)
    if not action:
        raise ValueError('Action not found')
    return _validate_with_pydantic(action.load().workflow.run, arg, entry_point=action)


def get_all_action_schemas() -> list[ActionSchemaInfo]:
    """
    Return a list of JSON Schemas for all registered actions'
    `workflow.run` parameters, keyed by action_id.
    """
    data: list[ActionSchemaInfo] = []
    for action_id, action in get_actions().items():
        workflow_cls = action.load().workflow

        signals = []
        for attr_name in dir(workflow_cls):
            if attr_name.startswith('__'):
                continue
            attr = getattr(workflow_cls, attr_name, None)
            if hasattr(attr, '__temporal_signal_definition'):
                signal_fn: Callable | None = getattr(
                    getattr(attr, '__temporal_signal_definition'),
                    'fn',
                    attr,
                )
                if signal_fn is not None:
                    schema = _get_signal_schema(signal_fn)
                    # Expose Python method names as the canonical API key.
                    signals.append({attr_name: schema})

        data.append(
            ActionSchemaInfo(
                action_id=action_id,
                json_schema=_get_param_schema(workflow_cls.run, entry_point=action),
                description=action.description,
                task_queue=action.task_queue,
                groups=action.groups,
                users=action.users,
                name=action.name,
                plugin_package=action.plugin_package,
                signals=signals,
            )
        )
    return data


class PluginActionCatalog:
    def get(self, action_id: str) -> ActionDefinition:
        entry = get_actions().get(action_id)
        if entry is None:
            raise ValueError(f'No action data for the given {action_id} ID')
        entry.load()
        return ActionDefinition(
            action_id, entry.priority_key, entry.priority_fairness_key
        )
