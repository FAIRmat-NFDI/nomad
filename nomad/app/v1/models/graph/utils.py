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

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from types import UnionType
from typing import (
    Any,
    ForwardRef,
    Literal,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    create_model,
    model_validator,
)
from pydantic.config import ConfigDict as BaseConfigDict

ref_prefix = '#/components/schemas'
request_suffix = 'Request'
response_suffix = 'Response'
graph_model_export = False


def json_schema_extra(schema: dict[str, Any], model: type[_DictModel]) -> None:
    if 'm_children' not in model.__annotations__:
        raise TypeError(
            f'No m_children field defined for dict model {model.__name__}. '
        )
    children_annotation = model.__annotations__['m_children']
    value_type = get_args(get_args(children_annotation)[0])[1]
    if value_type is None:
        raise TypeError(
            f"Could not determine m_children's type. Did you miss to call update_forward_refs()?"
        )

    if get_origin(value_type) in (Union, UnionType):
        value_types = get_args(value_type)
    else:
        value_types = (value_type,)

    types = []
    hasAny = False
    for value_type in value_types:
        if value_type == Any:
            hasAny = True
            break

        if isinstance(value_type, ForwardRef):
            value_type = value_type.__forward_value__

        if value_type == Literal['*']:
            types.append({'enum': ['*'], 'type': 'string'})
        else:
            # This forces all model names to be unique. Pydantic
            # replaces non unique model names with qualified names.
            # We are just using the plain name here. Unfortunately,
            # there is no way to get the "long_model_name" map from pydantic
            # to put the right names here. Therefore, in the presence
            # of non-unique names, we are using the wrong references.
            # Things depending on the openapi schema will cause issues.
            # i.e. https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/issues/1958
            module_name = value_type.__module__
            full_class_name = f'{module_name}.{value_type.__qualname__}'.replace(
                '.', '__'
            )
            types.append({'$ref': f'#/$defs/{full_class_name}-Input__1'})

    if 'properties' in schema:
        for property in schema['properties'].values():
            # Collect $ref if present
            if '$ref' in property:
                types.append(property)

            # Check for nested references inside anyOf
            types.extend(ref for ref in property.get('anyOf', []) if '$ref' in ref)

        schema['properties'].pop('m_children')

    if hasAny:
        schema['additionalProperties'] = True
    else:
        schema['additionalProperties'] = {'anyOf': types}


class _DictModel(BaseModel):
    @classmethod
    def process_extra(cls, values):
        if not isinstance(values, dict):
            return values
        m_children = values.setdefault('m_children', {})
        type_ = cls.model_fields['m_children'].annotation
        for name in list(values):
            if name not in cls.model_fields:
                value = values[name]
                values.pop(name)
                try:
                    value = TypeAdapter(type_).validate_python({name: value})[name]
                    m_children[name] = value
                except ValidationError as exc:
                    # m_children is always a Union and the last possible type is
                    # Literal['*']. Respectively the last validation errors comes from
                    # this type. It is usually confusing and not helpful to the user.
                    # Therefore, we pop it.
                    errors = exc.errors()
                    if len(errors) > 1:
                        errors.pop()
                    raise ValidationError.from_exception_data(
                        title=name, line_errors=errors
                    )

        return values

    model_config = ConfigDict(
        extra='allow',
        json_schema_extra=json_schema_extra,
    )


class _NoChildrenDictModel(BaseModel):
    @classmethod
    def process_extra(cls, values):
        """
        Processes extra values by handling nested BaseModel instances
        and merging 'm_children' into the main dictionary.
        """
        if isinstance(values, BaseModel):
            values = values.model_dump(exclude_none=True)
        if not isinstance(values, dict):
            return values

        m_children = values.pop('m_children', {})
        return {**m_children, **values}


def _get_request_type(type_hint: Any, ns: ModelNamespace) -> Any:
    origin, args = get_origin(type_hint), get_args(type_hint)

    if origin is list or type_hint in [str, int, bool, datetime, Any]:
        return Literal['*']

    if origin is None and issubclass(type_hint, BaseModel):
        return _generate_model(type_hint, request_suffix, _get_request_type, ns)

    if origin is dict:
        key_type, value_type = args
        return dict[key_type, _get_request_type(value_type, ns)]  # type: ignore

    # This is about Optional[T], which is translated to Union[None, T]
    if origin in (Union, UnionType) and len(args) == 2:
        if isinstance(None, args[1]):
            return _get_request_type(args[0], ns)
        if isinstance(None, args[0]):
            return _get_request_type(args[1], ns)

    if origin in (Union, UnionType):
        union_types = tuple(_get_request_type(type_, ns) for type_ in args)
        return Union[union_types]  # type: ignore

    raise NotImplementedError(type_hint)


def _get_response_type(type_hint: Any, ns: ModelNamespace) -> Any:
    origin, args = get_origin(type_hint), get_args(type_hint)
    if type_hint in [str, int, bool, datetime, Any]:
        return type_hint

    if origin is None and issubclass(type_hint, BaseModel):
        return _generate_model(type_hint, response_suffix, _get_response_type, ns)

    if origin is list:
        value_type = args[0]
        return list[_get_response_type(value_type, ns)]  # type: ignore

    if origin is dict:
        key_type, value_type = args
        # TODO is this really necessary?
        if value_type == type_hint:
            # We have detected direct type recursion, like in
            # Path = Dict[str, 'Path']
            return type_hint
        return dict[key_type, _get_response_type(value_type, ns)]  # type: ignore

    if origin is None and issubclass(type_hint, Enum):
        return type_hint

    # This is about Optional[T], which is translated to Union[None, T]
    if origin in (Union, UnionType) and len(args) == 2:
        if isinstance(None, args[1]):
            return _get_response_type(args[0], ns)
        if isinstance(None, args[0]):
            return _get_response_type(args[1], ns)

    if origin in (Union, UnionType):
        union_types = tuple(_get_response_type(type_, ns) for type_ in args)
        return Union[union_types]  # type: ignore

    raise NotImplementedError(type_hint)


ModelNamespace = dict[str, type[BaseModel] | ForwardRef]


def _generate_model(
    source_model: type[BaseModel] | Any,
    suffix: str,
    generate_type: Callable[[type, ModelNamespace], type],
    ns: ModelNamespace,
    **kwargs,
):
    if source_model == Any:
        return Any

    # We need to populate a forward ref for the model in the ns use it in recursion cases.
    result_model_name = f'{source_model.__name__}{suffix}'
    if (
        graph_model_export
        and result_model_name.startswith('Graph')
        and result_model_name not in ['GraphRequest', 'GraphResponse']
    ):
        result_model_name = result_model_name[5:]

    is_ns_origin = len(ns) == 0
    if result_model_name not in ns:
        ns[result_model_name] = ForwardRef(result_model_name)
    else:
        return ns[result_model_name]

    type_hints = get_type_hints(source_model)
    fields = dict(**kwargs)

    for field_name, type_hint in type_hints.items():
        if field_name.startswith('__'):
            continue

        if field_name == 'm_children':
            origin, args = get_origin(type_hint), get_args(type_hint)
            if origin in (Union, UnionType):
                types = args
            else:
                types = (type_hint,)
            if not all(
                type_ == Any
                or (isinstance(type_, type) and issubclass(type_, BaseModel))
                for type_ in types
            ):
                raise TypeError(
                    'Only Pydantic model classes (or Unions thereof) are supported as m_children types.'
                )
            value_types = tuple(
                _generate_model(type_, suffix, generate_type, ns) for type_ in types
            )
            # TODO we always add Literal['*'] at the end. Maybe it should be configurable
            # which models want to support '*' values for their children?
            value_type = Union[value_types + (Literal['*'],)]  # type: ignore
            fields['m_children'] = (dict[str, cast(type, value_type)] | None, None)  # type: ignore
            continue

        if field_name == 'm_request':
            if suffix == request_suffix:
                fields[field_name] = (type_hint | None, None)
            continue

        if field_name == 'm_response':
            if suffix == response_suffix:
                fields[field_name] = (type_hint | None, None)
            continue

        if field_name == 'm_is':
            fields[field_name] = (type_hint | None, None)
            continue

        if field_name == 'm_errors':
            if suffix == response_suffix:
                fields[field_name] = (type_hint | None, None)  # type: ignore
            continue

        if field_name.startswith('m_') and field_name not in ['m_def', 'm_def_id']:
            raise NotImplementedError(
                f'The internal field {field_name} is not implemented.'
            )

        fields[field_name] = (generate_type(type_hint, ns) | None, None)

    config = source_model.model_config
    if config.get('extra', 'ignore') == 'ignore' and 'm_children' not in fields:
        config = ConfigDict(**{**BaseConfigDict(extra='forbid'), **config})  # type: ignore

    validators = {}
    if 'm_children' in fields:
        config = ConfigDict(  # type: ignore
            **{
                **_DictModel.model_config,
                **config,
            }
        )
        if suffix == request_suffix:
            validators = {
                'process_extra': model_validator(mode='before')(
                    _DictModel.process_extra.__func__  # type: ignore
                )
            }
    else:
        validators = {
            'process_extra': model_validator(mode='before')(
                _NoChildrenDictModel.process_extra.__func__  # type: ignore
            ),
        }

    result_model = create_model(
        result_model_name,
        __module__=source_model.__module__,
        __validators__=validators,  # type: ignore
        __config__=config,
        **fields,
    )

    # We need to replace the forward ref in the ns with the finished model. We also
    # need to update all forward refs after the whole model has been created.
    ns[result_model_name] = result_model
    if is_ns_origin:
        for model in ns.values():
            if isinstance(model, type):
                model.model_rebuild()
                # There is a bug in pydantics BaseModel.update_forward_refs and it does not
                # recognize forward refs in Union types. Therefore we do our own impl.
                # https://github.com/pydantic/pydantic/issues/3345
                # from typing import Union, get_origin, get_args, ForwardRef
                # from pydantic import TypeAdapter

                # for field in model.model_fields.values():
                #     if get_origin(field.annotation) is Union:
                #         union_types = [
                #             TypeAdapter.eval_type_lenient(type_, globals_=globals(), locals_=ns)
                #             if isinstance(type_, ForwardRef) else type_
                #             for type_ in get_args(field.type_)
                #         ]
                #         field.annotation = Union[tuple(union_types)]
    assert (
        getattr(sys.modules[source_model.__module__], result_model_name, result_model)
        == result_model
        or graph_model_export
    ), f'Model class with name {result_model_name} already exists.'
    setattr(sys.modules[source_model.__module__], result_model_name, result_model)

    return result_model


def mapped(model: type[BaseModel], **mapping: str | type) -> type[BaseModel]:
    """
    Creates a new pydantic model based on the given model. The mapping argument allows
    to either change the name of a field in the input model or change the type of a field
    in the given input model or remove the field.

    Arguments:
        model: the input model.
        **kwargs: field names and either the new field name or the new field type or
            None to remove the field.

    Returns:
        a pydantic model with the mapped field and the same base as the input model
    """

    def create_field(field_info):
        return Field(
            default=field_info.default,
            alias=field_info.alias,
            title=field_info.title,
            description=field_info.description,
        )

    fields = {}
    for name, field in model.model_fields.items():
        if name not in mapping:
            fields[name] = (field.annotation, create_field(field))
            continue

        new_name_or_annotation = mapping[name]
        old_field = model.model_fields[name]

        if new_name_or_annotation is None:
            continue

        if isinstance(new_name_or_annotation, str):
            new_name = new_name_or_annotation
            annotation = old_field.annotation
        else:
            new_name = name
            annotation = new_name_or_annotation

        fields[new_name] = (annotation, create_field(old_field))

    return create_model(  # type: ignore
        model.__name__, **fields, __module__=model.__module__, __base__=model.__base__
    )


def generate_request_model(source_model: type[BaseModel]):
    return _generate_model(source_model, request_suffix, _get_request_type, dict())


def generate_response_model(source_model: type[BaseModel]):
    return _generate_model(source_model, response_suffix, _get_response_type, dict())
