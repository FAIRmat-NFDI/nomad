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

import builtins
import importlib
import re
import typing
from base64 import b64decode, b64encode
from datetime import date, datetime, timezone
from functools import reduce
from inspect import isclass
from typing import Any as TypingAny
from urllib.parse import urlparse, urlunparse

import numpy as np
import orjson
import pandas as pd
import pint
from dateutil.parser import parse

from nomad.units import ureg
from nomad.utils import get_logger

_logger = get_logger(__name__)


class Datatype:
    """
    Base abstract class for all data types.
    """

    __slots__ = (
        '_definition',
        '_support_array',
        '_disable_shape_check',
        '_disable_type_check',
        '_disable_auto_conversion',
    )

    def __init__(self):
        # the corresponding definition of the quantity, section, etc.
        self._definition = None
        # flag to indicate if the data type supports array like values
        self._support_array: bool = True
        self._disable_shape_check: bool = False
        self._disable_type_check: bool = False
        self._disable_auto_conversion: bool = False

    def __repr__(self):
        return self.__class__.__name__

    def attach_definition(self, definition):
        self._definition = definition
        return self

    def no_shape_check(self):
        self._disable_shape_check = True
        return self

    def no_type_check(self):
        self._disable_type_check = True
        return self

    def no_auto_conversion(self):
        self._disable_auto_conversion = True
        return self

    def _enable_array_support(self):
        self._support_array = True

    def _disable_array_support(self):
        self._support_array = False

    @property
    def support_array(self):
        return self._support_array

    @property
    def flags(self):
        switches: dict = {}
        if self._disable_shape_check:
            switches['disable_shape_check'] = True
        if self._disable_type_check:
            switches['disable_type_check'] = True
        if self._disable_auto_conversion:
            switches['disable_auto_conversion'] = True
        return switches

    def normalize_flags(self, flags: dict):
        self._disable_shape_check = flags.get('disable_shape_check', False)
        self._disable_type_check = flags.get('disable_type_check', False)
        self._disable_auto_conversion = flags.get('disable_auto_conversion', False)
        return self

    @property
    def shape(self):
        return self._definition.shape

    @property
    def is_scalar(self):
        return self.shape is None or len(self.shape) == 0

    @property
    def unit(self):
        return getattr(self._definition, 'unit', None)

    @property
    def flexible_unit(self):
        return getattr(self._definition, 'flexible_unit', False)

    def convertible_from(self, other):
        """
        For the given data type, return a flag indicating if it can be converted from the other data type.
        """
        raise NotImplementedError()

    def serialize_self(self):
        """
        Serialise the data type itself.
        """
        raise NotImplementedError()

    def normalize(self, value, **kwargs):
        """
        Normalise and validate the given value.
        The value may come from various sources, and may be in various formats.
        No assumptions can be made on the given value.
        A whitelist approach shall be used to validate the value.

        The following cases must be considered:
            1. The given input value is manually set.
            2. The given input value is produced by a parser.
            3. The given input value is from a serialized archive.

        This method shall 1) validate type and shape of the given value, according to the definition, 2) convert
        the value in compatible type into the designated type.

        This method shall always return a valid value of the given type.
        Otherwise, it shall raise an exception.
        The returned value will be stored in the corresponding section (python object), and will be
        serialized into the archive or accessed by the user.
        """
        raise NotImplementedError()

    def serialize(self, value, **kwargs):
        """
        Serialise the given valid value.
        The given value is guaranteed to be valid.
        The given value is the actual value stored in the corresponding section.

        This method shall return an object that is JSON serializable.

        Optional keyword arguments:
            section: the section object that the value belongs to
            transform: a function that transforms the value, this function will apply to each element of the value
                if the value is an array, or a nested array.

                The function shall have the following signature:
                    ```python
                    def transform(value, path):
                        pass
                    ```
                The value is the actual value, or the element in the array.
                The path shall be None if the value is a scalar, or a list of indices if the value is an array.
        """
        raise NotImplementedError()

    def standard_type(self):
        """
        Return the equivalent python type of the data type.
        This will be used in generating models for mongodb and elastic search.
        Incompatible types should simply raise an exception.
        """
        type_name: str = self.__class__.__name__.lower()
        if type_name.startswith('m_'):
            type_name = type_name[2:]
        return type_name


class Primitive(Datatype):
    """
    Primitive types that are mostly supported by python and numpy type systems.
    This includes integers, floating point numbers, complex numbers, strings, and booleans.
    Primitive types can be scalar or array like.

    This is an abstract class and should not be instantiated.
    """

    __slots__ = ('_dtype', '_np_base')

    def __init__(self, dtype):
        super().__init__()

        # the actual data type, could be a numpy type or a python type
        self._dtype = dtype
        # base type, np.integer or np.inexact
        # used to determine if the normalized value is a numpy array or a python list
        self._np_base = type(None)

    def __repr__(self):
        return f'{self.__class__.__name__}({self._dtype.__name__})'

    def _check_shape(self, value):
        """
        Check the **shape** of the given value.
        If the definition defines a scalar, the value must be a scalar.
        If the definition defines an array, the value must be an array.
        """
        if self._disable_shape_check:
            # _logger.warning(
            #     f'Not checking shape of {value} for definition {self._definition}.'
            # )
            return value

        if self.is_scalar:
            if isinstance(value, list | np.ndarray):
                raise ValueError(f'Shape mismatch for {value}.')
        else:
            if not self.support_array:
                raise TypeError(
                    'The underlying data type does not support array like values.'
                )
            if isinstance(value, np.ndarray):
                if len(value.shape) != len(self.shape):
                    raise ValueError(f'Invalid shape for {value}.')
            elif isinstance(value, list):
                if len(self.shape) != 1:
                    raise ValueError(f'Python array must be one dimensional.')
            else:
                raise ValueError(f'Shape mismatch for {value}.')

        return value

    def serialize_self(self):
        """
        Serialize the type itself.
        Typically, the data type is serialized as a dictionary with two keys: `type_kind` and `type_data`.
        Flags are also included in the dictionary.
        """

        if self._dtype in (int, float, complex, str, bool):
            return {
                'type_kind': 'python',
                'type_data': self._dtype.__name__,
            } | self.flags

        if issubclass(self._dtype, np.number | np.str_ | np.bool_):
            return {
                'type_kind': 'numpy',
                'type_data': self._dtype.__name__,
            } | self.flags

        raise TypeError(f'Unsupported data type {self._dtype}.')

    def normalize(self, value, **kwargs):
        if value is None:
            return value

        def extract_magnitude(v):
            if isinstance(v, list | tuple):
                return [extract_magnitude(x) for x in v]

            if not isinstance(v, pint.Quantity):
                return v

            if self.unit is not None:
                v = v.to(self.unit)

            return v.magnitude

        value = extract_magnitude(value)

        if self.is_scalar:
            given_type = type(value)
            if given_type is np.ndarray:
                if value.size != 1:
                    raise ValueError(
                        f'Cannot set {value} for the scalar quantity {self._definition}.'
                    )
                # just to extract the scalar of the same type
                # do not use value.item() as it will convert to python type
                # value.max() will also work
                value = value.min()
                given_type = value.dtype.type

            # type match, no need to consider conversion
            if given_type == self._dtype:
                return value

            # no conversion and type mismatch
            if self._disable_auto_conversion:
                raise ValueError(f'Cannot set {value} for {self._definition}.')

            # conversion is allowed, explicitly convertable
            if self.convertible_from(given_type):
                return self._dtype(value)

            # not explicitly convertable, try to convert implicitly
            if self._disable_type_check:
                # if no type check, always return something, even if it is not the same type
                try:
                    return self._dtype(value)
                except Exception:  # noqa
                    return value

            # if type check is enabled, raise an exception
            try:
                new_value = given_type(converted_value := self._dtype(value))
                if new_value == value or np.isclose(new_value, value):
                    return converted_value
                raise ValueError(f'Cannot convert {value} to {self._dtype}.')
            except Exception:
                raise ValueError(f'Cannot convert {value} to {self._dtype}.')

        # the array case

        if isinstance(value, np.ndarray):
            array = value
        elif isinstance(value, pd.DataFrame | pd.Series):
            array = value.to_numpy()
        elif isinstance(value, list | tuple):
            array = np.array(value)
        else:
            raise ValueError(f'Cannot identify type for {value}.')

        original_dtype = array.dtype

        # no conversion and type mismatch
        if self._disable_auto_conversion and original_dtype.type != self._dtype:
            raise ValueError(f'Cannot set {value} for {self._definition}.')

        if original_dtype.type != self._dtype:
            # allow to convert

            if self._disable_type_check:
                # if no type check, try to convert
                # continue if conversion is not possible
                try:
                    array = array.astype(self._dtype)
                except Exception:  # noqa
                    pass
            else:
                # need to avoid this block as performance will be affected
                try:
                    # explicit conversion
                    array = array.astype(self._dtype, casting='safe')
                except TypeError:
                    new_array = array.astype(self._dtype).astype(original_dtype)
                    if isinstance(self, m_str | m_bool):
                        if not np.all(array == new_array):
                            raise ValueError(
                                f'Cannot convert {array} to {self._dtype}.'
                            )
                    elif not np.allclose(array, new_array):
                        raise ValueError(f'Cannot convert {array} to {self._dtype}.')
                    array = new_array

        # python case
        if not issubclass(self._dtype, self._np_base):
            array = array.tolist()

        return self._check_shape(array)

    def serialize(self, value, **kwargs):
        """
        This handles both scalar and array like values.
        """

        transform: typing.Callable | None = kwargs.get('transform', None)

        def _convert(v, p=None):
            if isinstance(v, list):
                return [
                    _convert(x, [i] if p is None else p + [i]) for i, x in enumerate(v)
                ]

            return v if transform is None else transform(v, p)

        if isinstance(value, np.ndarray):
            return _convert(value.tolist())

        if isinstance(value, np.generic):
            return _convert(value.item())

        return _convert(value)


class Number(Primitive):
    """
    Base class for all number types.

    This is an abstract class and should not be instantiated.
    """

    __slots__ = ()

    def __init__(self, dtype):
        super().__init__(dtype)


class ExactNumber(Number):
    """
    Base class for all exact number types (integers).

    This is an abstract class and should not be instantiated.
    """

    __slots__ = ()

    def __init__(self, dtype):
        super().__init__(dtype)
        self._np_base = np.integer


class m_int(ExactNumber):
    """
    This is an abstract class and should not be instantiated.
    """

    __slots__ = ()


class m_int8(m_int):
    """
    64-bit integer data type.
    Since both json and python do not impose any limit on the size of integers, 64-bit integers are used by default.
    np.int32, np.int16, and np.int8 are stored as 64-bit integers.
    """

    __slots__ = ()

    def __init__(self):
        super().__init__(np.int8)

    def convertible_from(self, other):
        if other is np.int8:
            return True

        return False


class m_int16(m_int):
    """
    16-bit integer data type.
    """

    __slots__ = ()

    def __init__(self):
        super().__init__(np.int16)

    def convertible_from(self, other):
        if other in (np.int16, np.int8):
            return True

        return False


class m_int32(m_int):
    """
    32-bit integer data type.
    """

    __slots__ = ()

    def __init__(self, *, dtype: type[int | np.int32] = int):
        if isinstance(dtype, np.dtype):
            dtype = dtype.type

        if dtype not in (int, np.int32):
            raise ValueError(f'Invalid dtype for {self.__class__.__name__}.')

        super().__init__(dtype)

    def convertible_from(self, other):
        if other in (np.int32, np.int16, np.int8):
            return True

        return False


class m_int64(m_int):
    """
    64-bit integer data type.
    """

    __slots__ = ()

    def __init__(self):
        super().__init__(np.int64)

    def convertible_from(self, other):
        if other in (int, np.int64, np.int32, np.int16, np.int8):
            return True

        return False


class InexactNumber(Number):
    """
    Base class for all inexact number types (floating point numbers).

    This is an abstract class and should not be instantiated.
    """

    __slots__ = ()

    def __init__(self, dtype):
        super().__init__(dtype)
        self._np_base = np.inexact

    def normalize(self, value, **kwargs):
        """
        Accept the following additional flags to tweak the behavior.

        Parameters:
            treat_none_as_nan: If True, treat None as NaN.
        """

        def _preprocess(v):
            if isinstance(v, list | tuple):
                return [_preprocess(x) for x in v]

            return float('nan') if v is None else v

        if kwargs.get('treat_none_as_nan', False):
            value = _preprocess(value)

        return super().normalize(value, **kwargs)


class m_float(InexactNumber):
    """
    This is an abstract class and should not be instantiated.
    """

    __slots__ = ()


class m_float16(m_float):
    """
    16-bit floating point number data type.
    """

    __slots__ = ()

    def __init__(self):
        super().__init__(np.float16)

    def convertible_from(self, other):
        if other is np.float16:
            return True

        return False


class m_float32(m_float):
    """
    32-bit floating point number data type.
    """

    __slots__ = ()

    def __init__(self):
        super().__init__(np.float32)

    def convertible_from(self, other):
        if other in (np.float32, np.float16):
            return True

        return False


class m_float64(m_float):
    """
    64-bit floating point number data type.
    """

    __slots__ = ()

    def __init__(self, *, dtype: type[float | np.float64] = float):
        if isinstance(dtype, np.dtype):
            dtype = dtype.type

        if dtype not in (float, np.float64):
            raise ValueError(f'Invalid dtype for {self.__class__.__name__}.')

        super().__init__(dtype)

    def convertible_from(self, other):
        if other in (float, np.float64, np.float32, np.float16):
            return True

        return False


class m_complex(InexactNumber):
    """
    This is an abstract class and should not be instantiated.
    """

    __slots__ = ()
    regex_pattern = re.compile(
        r'^(?=[iIjJ.\d+-])([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?![iIjJ.\d]))?'
        r'([+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)?[iIjJ])?$'
    )


class m_complex128(m_complex):
    """
    128-bit complex number data type (64-bit real and imaginary parts).
    np.complex64 is stored as 128-bit complex numbers.
    """

    __slots__ = ()

    def __init__(self, *, dtype: type[complex | np.complexfloating] = complex):
        if isinstance(dtype, np.dtype):
            dtype = dtype.type

        if dtype not in (complex, np.complex128, np.complex64):
            raise ValueError(f'Invalid dtype for {self.__class__.__name__}.')

        super().__init__(complex if dtype is complex else np.complex128)

    def convertible_from(self, other):
        if other in (complex, np.complex128):
            return True

        return False

    def normalize(self, value, **kwargs):
        if value is None:
            return value

        return self._check_shape(_normalize_complex(value, self._dtype, self.unit))

    def serialize(self, value, **kwargs):
        # scalar
        if type(value) in (complex, np.complex128):
            return {'re': value.real, 'im': value.imag}

        # 1D
        if isinstance(value, list | tuple):
            return {'re': [v.real for v in value], 'im': [v.imag for v in value]}

        # ND
        if isinstance(value, np.ndarray):
            return {'re': value.real.tolist(), 'im': value.imag.tolist()}

        raise ValueError(f'Cannot serialize {value}.')


class m_bool(Primitive):
    """
    Boolean data type.
    """

    __slots__ = ()

    def __init__(self, *, dtype: type[bool | np.bool_] = bool):
        if isinstance(dtype, np.dtype):
            dtype = dtype.type

        if dtype not in (bool, np.bool_):
            raise ValueError(f'Invalid dtype for {self.__class__.__name__}.')

        super().__init__(bool if dtype is bool else np.bool_)

        self._np_base = np.bool_

    def convertible_from(self, other):
        if other in (bool, np.bool_):
            return True

        return False


class m_str(Primitive):
    """
    String data type.
    """

    __slots__ = ()

    def __init__(self, *, dtype: type[str | np.str_] = str):
        if isinstance(dtype, np.dtype):
            dtype = dtype.type

        if dtype not in (str, np.str_):
            raise ValueError(f'Invalid dtype for {self.__class__.__name__}.')

        super().__init__(str if dtype is str else np.str_)

        self._np_base = np.str_

    def convertible_from(self, other):
        if other in (str, np.str_):
            return True

        return False


class NonPrimitive(Datatype):
    """
    Base class for all non-primitive types.

    This is an abstract class and should not be instantiated.
    """

    __slots__ = ()

    def _check_shape(self, value):
        """
        Check the **shape** of the given value.
        If the definition defines a scalar, the value must be a scalar.
        If the definition defines an array, the value must be an array.
        Since the data type is non-primitive, we only support 1D arrays.
        """
        if self._disable_shape_check:
            # _logger.warning(
            #     f'Not checking shape of {value} for definition {self._definition}.'
            # )
            return value

        if self.is_scalar:
            if isinstance(value, list):
                raise ValueError(f'Shape mismatch for {value}.')
        else:
            if not self.support_array:
                raise TypeError(
                    'The underlying data type does not support array like values.'
                )

            if not isinstance(value, list):
                raise ValueError(f'Shape mismatch for {value}.')

            if len(self.shape) != 1:
                raise ValueError(f'Python array must be one dimensional.')

        return value

    def _normalize_impl(self, value, **kwargs):
        """
        The actual normalization that would be applied to each element.
        Subclasses should implement this method.
        """
        raise NotImplementedError()

    def _serialize_impl(self, value, **kwargs):
        """
        The actual serialization that would be applied to each element.
        Subclasses should implement this method.
        """
        return value

    def serialize_self(self):
        return {
            'type_kind': 'custom',
            'type_data': f'{self.__class__.__module__}.{self.__class__.__name__}',
        } | self.flags

    def normalize(self, value, **kwargs):
        if value is None:
            return value

        self._check_shape(value)

        if self.is_scalar:
            return self._normalize_impl(value, **kwargs)

        def _convert(v):
            if isinstance(v, list):
                return [_convert(x) for x in v]

            return self._normalize_impl(v, **kwargs)

        return _convert(value)

    def serialize(self, value, **kwargs):
        """
        Transparently return the given value.
        """

        transform: typing.Callable | None = kwargs.get('transform', None)

        def _convert(v, p=None):
            if isinstance(v, list):
                return [
                    _convert(x, [i] if p is None else p + [i]) for i, x in enumerate(v)
                ]

            intermediate = self._serialize_impl(v, **kwargs)

            return intermediate if transform is None else transform(intermediate, p)

        return _convert(value)


class URL(NonPrimitive):
    """
    URL like data type.
    It is a string that is a valid URL.
    """

    __slots__ = ()

    def _normalize_impl(self, value, **kwargs):
        return urlunparse(urlparse(value))

    def standard_type(self):
        return 'str'


class File(NonPrimitive):
    __slots__ = ()

    def _normalize_impl(self, value, **kwargs):
        if not isinstance(value, str):
            raise TypeError('Files need to be given as URL strings.')

        section = kwargs.get('section')
        if section is None:
            raise ValueError('Cannot normalize File without section.')

        if (context := section.m_root().m_context) is not None:
            return context.normalize_reference(section, value)

        return value

    def standard_type(self):
        return 'str'


class Any(NonPrimitive):
    __slots__ = ()

    def normalize(self, value, **kwargs):
        """
        Transparently return the given value.
        """
        return value

    def serialize(self, value, **kwargs):
        """
        Transparently return the given value.
        """
        return value


class Capitalized(NonPrimitive):
    """
    Capitalized string data type.
    It is a string that is capitalized.
    It is used for names, titles, etc.
    """

    __slots__ = ()

    def _normalize_impl(self, value, **kwargs):
        return value.capitalize()

    def standard_type(self):
        return 'str'


class Bytes(NonPrimitive):
    __slots__ = ()

    def _normalize_impl(self, value, **kwargs):
        if isinstance(value, str):
            return b64decode(value)

        if isinstance(value, bytes):
            return value

        raise TypeError(f'{value} is not a valid bytes object.')

    def _serialize_impl(self, value, **kwargs):
        return b64encode(value).decode('ascii')


class JSON(NonPrimitive):
    __slots__ = ()

    def _normalize_impl(self, value, **kwargs):
        if isinstance(value, dict):
            return orjson.loads(
                orjson.dumps(
                    value,
                    option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY,
                )
            )

        raise TypeError(f'{value} needs to be a dict.')

    def standard_type(self):
        return 'dict'


class Dimension(NonPrimitive):
    __slots__ = ()

    def _normalize_impl(self, value, **kwargs):
        if isinstance(value, int):
            return value

        if isinstance(value, str):
            if value.isdigit():
                return int(value)

            # todo: there is no validation for the moment
            # todo: it shall be enabled
            if (
                True
                or value.isidentifier()
                or re.match(re.compile(r'((\d)\.\.)?(\d|\*)'), value)
            ):
                return value

        raise TypeError(f'{value} is not a valid dimension.')


class Unit(NonPrimitive):
    """
    Unit data type.
    It is stored as a pint.Unit object.
    """

    __slots__ = ()

    def _normalize_impl(self, value, **kwargs):
        if isinstance(value, str):
            unit_obj = ureg.parse_units(value)
        elif isinstance(value, pint.Quantity):
            unit_obj = value.units
        elif isinstance(value, pint.Unit):
            unit_obj = value
        else:
            raise TypeError('Units must be given as str or pint.Unit instances.')

        check_dimensionality(self._definition, unit_obj)

        return unit_obj

    def _serialize_impl(self, value, **kwargs):
        return str(value)

    def standard_type(self):
        return 'str'


class Callable(NonPrimitive):
    __slots__ = ('_nargs',)

    def __init__(self, nargs: int = 0):
        """
        Provide the number of arguments to enable validation.

        Arguments:
            nargs: number of arguments to the callable.
        """
        super().__init__()
        self._nargs = nargs

    def _normalize_impl(self, value, **kwargs):
        if not callable(value):
            raise TypeError(f'{value} is not a valid callable object.')

        if self._nargs > 0 and (
            getattr(getattr(value, '__code__', None), 'co_argcount', 0) != self._nargs
        ):
            raise TypeError(f'Callable {value} should have {self._nargs} arguments.')

        return value

    def _serialize_impl(self, value, **kwargs):
        raise NotImplementedError()


class Datetime(NonPrimitive):
    """
    Datetime data type.
    It is stored as a datetime object.
    """

    __slots__ = ()

    def _normalize_impl(self, value, **kwargs):
        if isinstance(value, datetime):
            datetime_obj = value
        elif isinstance(value, date):
            datetime_obj = datetime(value.year, value.month, value.day)
        elif isinstance(value, str):
            datetime_obj = parse(value)
        elif isinstance(value, int | float):
            datetime_obj = datetime.fromtimestamp(value)
        elif isinstance(value, pd.Timestamp):
            datetime_obj = value.to_pydatetime()
        elif isinstance(value, np.datetime64):
            datetime_obj = pd.Timestamp(value).to_pydatetime()  # noqa
        else:
            raise ValueError(f'Cannot convert {value} to datetime.')

        if datetime_obj.tzinfo is None:
            datetime_obj = datetime_obj.replace(tzinfo=timezone.utc)
        else:
            datetime_obj = datetime_obj.astimezone(timezone.utc)

        return datetime_obj

    def _serialize_impl(self, value, **kwargs):
        return value.isoformat()


class Enum(NonPrimitive):
    def __init__(self, *args, **kwargs):
        super().__init__()

        if len(args) == 1 and isinstance(args[0], list):
            args = args[0]

        self._descriptions: dict = kwargs.pop('m_descriptions', {})

        for arg in args:
            if arg in kwargs:
                raise ValueError(f"Duplicate value '{arg}' provided for enumeration.")
            kwargs[arg] = arg

        self._list: list = list(sorted(kwargs.values()))
        self._unique_values: set = set(self._list)  # allow constant time member check

        if any(not isinstance(enum_value, str) for enum_value in self):
            raise TypeError('All values of an enumeration must be strings.')

        self.__dict__.update(kwargs)

    def __contains__(self, item):
        return item in self._unique_values

    def __getitem__(self, index):
        return self._list[index]

    def __len__(self):
        return len(self._list)

    def _validate_value(self, value: str):
        if value in self:
            return value

        # this is to account for the faulty values that exist in the database
        # the faulty values are the ones look like `enumClass.enumValue`
        # that are generated by calling `str()` on the enum fields
        if (new_value := value.rsplit('.', maxsplit=1)[-1]) in self:
            return new_value

        raise ValueError(f'{value} is not a value of this enumeration.')

    def set_description(self, value: str, description: str):
        self._descriptions[self._validate_value(value)] = description

    def get_description(self, value: str) -> str:
        return self._descriptions.get(self._validate_value(value), '')

    def serialize_self(self):
        result: dict = {'type_kind': 'enum', 'type_data': self._list}
        if self._descriptions:
            result['type_descriptions'] = self._descriptions
        return result | self.flags

    def _normalize_impl(self, value, **kwargs):
        return self._validate_value(value)

    def standard_type(self):
        return 'enum'


def normalize_type(value):
    """
    Normalise the given value to a data type object.
    The value could be a directory, that comes from serialized definition.
    The value could be a string, that comes from the user input.
    The value could be a type, that comes from the user input.
    """

    # we try to implement this factory function in a concise and easy-to-extend manner
    # if the type is passed in as a string, it shall be the string representation of python type/class names
    # try to load the types and call this function again to allow the main body of the function to handle the rest
    if isinstance(value, str):
        value = value.lower()

        if value.startswith('np.'):
            return normalize_type(getattr(np, value[3:]))

        if value.startswith('numpy.'):
            return normalize_type(getattr(np, value[6:]))

        if value == 'string':
            return m_str()

        if value == 'boolean':
            return m_bool()

        if value.endswith('url'):
            return URL()

        if value.endswith('file'):
            return File()

        if value.endswith('any'):
            return Any()

        if value.endswith('capitalized'):
            return Capitalized()

        if value.endswith('bytes'):
            return Bytes()

        if value.endswith('json'):
            return JSON()

        if value.endswith('query'):
            from nomad.datamodel.data import Query

            return Query()

        if value.endswith('datetime'):
            return Datetime()

        if value == 'user':
            from nomad.datamodel.data import UserReference

            return UserReference()

        if value == 'author':
            from nomad.datamodel.data import AuthorReference

            return AuthorReference()

        # if not in numpy, it must be in builtins
        if (builtin_type := getattr(builtins, value, None)) is not None:
            return normalize_type(builtin_type)

        raise ValueError(f'Unsupported data type {value}.')

    # a dictionary is generated by serializing the definition
    if isinstance(value, dict):
        type_kind = value['type_kind'].lower()
        type_data = value.get('type_data', '')

        # python primitive types
        if type_kind == 'python':
            return normalize_type(type_data).normalize_flags(value)

        # User and author types are references, not flag-bearing data types.
        if type_kind in ('user', 'author'):
            return normalize_type(type_data)

        # numpy primitive types
        if type_kind == 'numpy':
            return normalize_type(f'np.{type_data}').normalize_flags(value)

        # python classes
        if type_kind == 'custom':
            module_name, class_name = type_data.rsplit('.', 1)
            if (class_type := globals().get(class_name, None)) is None:
                module = importlib.import_module(module_name)
                class_type = getattr(module, class_name, None)

            if class_type is not None:
                return class_type().normalize_flags(value)

        if type_kind == 'enum':
            return Enum(
                *type_data, m_descriptions=value.get('type_descriptions', {})
            ).normalize_flags(value)

        raise ValueError(f'Unsupported data type {value}.')

    if isinstance(value, Datatype):
        return value

    if isclass(value) and issubclass(value, Datatype):
        if value is Enum:
            raise ValueError('Enumeration type must be created with values.')

        return value()

    # unwrap the type instance in case of definitions such as type=np.dtype(np.int64)
    if isinstance(value, np.dtype):
        value = value.type

    if value is TypingAny:
        return Any()

    if value is int:
        return m_int32()
    if value is float:
        return m_float64()
    if value is complex:
        return m_complex128()
    if value is bool:
        return m_bool()
    if value is str:
        return m_str()
    if value is datetime:
        return Datetime()
    if value in (np.int8, np.uint8):
        return m_int8()
    if value in (np.int16, np.uint16):
        return m_int16()
    if value in (np.int32, np.uint32):
        return m_int32(dtype=np.int32)
    if value in (np.int64, np.uint64):
        return m_int64()
    if value is np.float16:
        return m_float16()
    if value is np.float32:
        return m_float32()
    if value is np.float64:
        return m_float64(dtype=np.float64)
    if value in (np.complex128, np.complex64):
        return m_complex128(dtype=np.complex128)
    if value is np.bool_:
        return m_bool(dtype=np.bool_)
    if value is np.str_:
        return m_str(dtype=np.str_)
    if value is np.datetime64:
        return Datetime()

    raise ValueError(f'Unsupported data type {value}.')


def to_optimade_type(in_type: Datatype) -> str:
    standard_type = in_type.standard_type()

    if standard_type.startswith('int'):
        return 'integer'
    if standard_type.startswith('float'):
        return 'float'
    if standard_type.startswith('complex'):
        return 'complex'
    if standard_type == 'bool':
        return 'boolean'
    if standard_type in ('str', 'enum'):
        return 'string'
    if standard_type == 'datetime':
        return 'timestamp'

    raise NotImplementedError(f'Unsupported optimade data type {in_type}.')


def to_mongo_type(in_type: Datatype):
    from mongoengine import (
        BooleanField,
        DateTimeField,
        DictField,
        FloatField,
        IntField,
        StringField,
    )

    standard_type = in_type.standard_type()

    if standard_type.startswith('int'):
        return IntField
    if standard_type.startswith('float'):
        return FloatField
    if standard_type == 'bool':
        return BooleanField
    if standard_type in ('str', 'enum'):
        return StringField
    if standard_type == 'datetime':
        return DateTimeField
    if standard_type == 'dict':
        return DictField

    raise NotImplementedError(f'Unsupported mongo data type {in_type}.')


def to_pydantic_type(in_type: Datatype):
    standard_type = in_type.standard_type()

    if standard_type.startswith('int'):
        return int | None
    if standard_type.startswith('float'):
        return float | None
    if standard_type.startswith('complex'):
        return complex | None
    if standard_type == 'bool':
        return bool | None
    if standard_type in ('str', 'enum'):
        return str | None
    if standard_type == 'datetime':
        return datetime | None
    if standard_type == 'dict':
        return dict | None
    raise NotImplementedError(f'Unsupported pydantic data type {in_type}.')


def to_elastic_type(in_type: Datatype, dynamic: bool) -> str:
    standard_type = in_type.standard_type()

    if dynamic:
        if standard_type.startswith('int'):
            return 'long'
        if standard_type.startswith('float'):
            return 'double'
        if standard_type == 'bool':
            return 'boolean'
        if standard_type in ('str', 'enum'):
            return 'text'
        if standard_type == 'datetime':
            return 'date'
    else:
        if standard_type in ('str', 'enum'):
            return 'keyword'
        if standard_type == 'float64':
            return 'double'
        if standard_type == 'float32':
            return 'float'
        if standard_type == 'float16':
            return 'half_float'
        if standard_type == 'int64':
            return 'long'
        if standard_type == 'int32':
            return 'integer'
        if standard_type in ('int16', 'int8'):
            return 'short'
        if standard_type == 'bool':
            return 'boolean'
        if standard_type == 'datetime':
            return 'date'

    raise NotImplementedError(f'Unsupported elastic data type {in_type}.')


def to_json_schema_type(in_type: Datatype) -> dict[str, str]:
    """
    Convert a metainfo Datatype to a JSON Schema type.
    """
    std_type = in_type.standard_type()

    if std_type.startswith('int'):
        return {'type': 'integer'}
    if std_type.startswith('float'):
        return {'type': 'number'}
    if std_type == 'bool':
        return {'type': 'boolean'}
    if std_type in ('str', 'enum'):
        return {'type': 'string'}
    if std_type == 'dict':
        return {'type': 'object'}
    if std_type == 'datetime':
        return {'type': 'string', 'format': 'date-time'}

    raise NotImplementedError(f'Unsupported JSON Schema type: {std_type}')


_extra_precision: set = set()


def _add_extra_precision():
    for x in (
        'int64',
        'uint64',
        'float80',
        'float96',
        'float128',
        'float256',
        'complex160',
        'complex192',
        'complex256',
        'complex512',
    ):
        try:
            _extra_precision.add(getattr(np, x))
        except Exception:  # noqa
            pass


if not _extra_precision:
    _add_extra_precision()


def _normalize_complex(value, complex_type, to_unit: str | pint.Unit | None):
    """
    Try to convert a given value to a complex number.
    """

    def __check_precision(_type):
        if _type is type(None):
            return

        if complex_type in (np.complex128, complex):  # 64-bit complex
            if _type in _extra_precision:
                raise ValueError(
                    f'Cannot convert {_type.__name__} to {complex_type.__name__} due to possible loss of precision.'
                )
        elif complex_type == np.complex64:  # 32-bit complex
            if _type in _extra_precision | {
                int,
                float,
                np.int32,
                np.uint32,
                np.float64,
                np.complex128,
            }:
                raise ValueError(
                    f'Cannot convert {_type.__name__} to {complex_type.__name__} due to possible loss of precision.'
                )

    if isinstance(value, pint.Quantity):
        scaled: np.ndarray = value.to(to_unit).magnitude if to_unit else value.magnitude
        return _normalize_complex(scaled, complex_type, None)

    # a list of complex numbers represented by int, float or str
    if isinstance(value, list):
        normalized = [_normalize_complex(v, complex_type, to_unit) for v in value]
        return (
            normalized
            if complex_type is complex
            else np.array(normalized, dtype=complex_type)
        )

    # complex or real part only
    if isinstance(value, int | float | complex | np.number):
        __check_precision(type(value))
        return complex_type(value)

    # np array
    if isinstance(value, np.ndarray):
        __check_precision(value.dtype.type)
        return value.astype(complex_type)

    # dict representation of complex number
    if isinstance(value, dict):
        real = value.get('re')
        imag = value.get('im')
        assert real is not None or imag is not None, (
            'Cannot convert an empty dict to complex number.'
        )

        def __combine(_real, _imag):
            _real_list: bool = isinstance(_real, list)
            _imag_list: bool = isinstance(_imag, list)
            if _real_list or _imag_list:
                if _real is None:
                    return [__combine(None, i) for i in _imag]
                if _imag is None:
                    return [__combine(r, None) for r in _real]
                # leverage short-circuit evaluation, do not change order
                if _real_list and _imag_list and len(_real) == len(_imag):
                    return [__combine(r, i) for r, i in zip(_real, _imag)]

                raise ValueError(
                    'Cannot combine real and imaginary parts of complex numbers.'
                )

            __check_precision(type(_real))
            __check_precision(type(_imag))
            if _real is None:
                return complex_type(_imag) * 1j
            if _imag is None:
                return complex_type(_real)
            return complex_type(_real) + complex_type(_imag) * 1j

        combined = __combine(real, imag)
        return (
            combined
            if complex_type is complex or not isinstance(combined, list)
            else np.array(combined, dtype=complex_type)
        )

    # a string, '1+2j'
    # one of 'i', 'I', 'j', 'J' can be used to represent the imaginary unit
    if isinstance(value, str):
        match = m_complex.regex_pattern.match(value)
        if match is not None:
            return complex_type(reduce(lambda a, b: a.replace(b, 'j'), 'iIJ', value))

    raise ValueError(f'Cannot convert {value} to complex number.')


def check_dimensionality(quantity_def, unit: pint.Unit | None) -> None:
    if quantity_def is None or unit is None:
        return

    dimensionality = getattr(quantity_def, 'dimensionality', None)

    if dimensionality is None:  # not set, do not validate
        return

    if dimensionality in ('dimensionless', '1') and unit.dimensionless:  # dimensionless
        return

    if dimensionality == 'transformation':
        # todo: check transformation dimensionality
        return

    if ureg.Quantity(1 * unit).check(dimensionality):  # dimensional
        return

    raise TypeError(f'Dimensionality {dimensionality} is not met by unit {unit}.')


if __name__ == '__main__':
    pass
