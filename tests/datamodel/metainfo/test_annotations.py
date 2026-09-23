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

import pytest
from pydantic import ValidationError

from nomad.datamodel.metainfo.annotations import (
    DisplayAnnotation,
    ELNAnnotation,
    PlotlyGraphObjectAnnotation,
    QuantityDisplayAnnotation,
    SectionDisplayAnnotation,
)
from nomad.datamodel.metainfo.plot import PlotlyError
from nomad.metainfo import Package, Quantity, Section, SubSection


@pytest.mark.parametrize(
    'quantity, annotation, result, error',
    [
        pytest.param(Quantity(type=str), {}, {}, False, id='empty'),
        pytest.param(
            Quantity(type=str),
            {'component': 'StringEditQuantity'},
            None,
            False,
            id='plain',
        ),
        pytest.param(
            Quantity(type=str),
            {'component': 'NumberEditQuantity'},
            None,
            True,
            id='wrong-type',
        ),
        pytest.param(
            Quantity(type=str, shape=[1, 2]),
            {'component': 'StringEditQuantity'},
            None,
            True,
            id='bad-shape',
        ),
    ],
)
def test_eln_validation(quantity, annotation, result, error):
    if error:
        with pytest.raises(ValidationError):
            annotation_model = ELNAnnotation(**annotation)
            annotation_model.m_definition = quantity
    else:
        annotation_model = ELNAnnotation(**annotation)
        annotation_model.m_definition = quantity
        assert annotation_model.model_dump(exclude_none=True) == (result or annotation)


@pytest.mark.parametrize(
    'annotation, result, error_type, error',
    [
        pytest.param({'x': 'x'}, None, PlotlyError, True, id='data-required'),
        pytest.param({'data': {}}, None, ValidationError, True, id='not_empty'),
        pytest.param({'data': {'x': 'x'}}, None, ValidationError, False, id='plain'),
    ],
)
def test_plotly_graph_object_validation(annotation, result, error_type, error):
    if error:
        with pytest.raises(error_type):
            PlotlyGraphObjectAnnotation(**annotation)
    else:
        assert PlotlyGraphObjectAnnotation(**annotation).dict(exclude_none=True) == (
            result or annotation
        )


@pytest.mark.parametrize(
    'definition, annotation, error',
    [
        pytest.param(
            Section(name='test'),
            {'visible': {'exclude': ['a']}},
            False,
            id='section-filter',
        ),
        pytest.param(
            Section(name='test'), {'order': ['a', 'b']}, False, id='section-order'
        ),
        pytest.param(
            Section(name='test'), {'visible': False}, True, id='section-bool-visible'
        ),
        pytest.param(
            Section(name='test'), {'editable': True}, True, id='section-bool-editable'
        ),
        pytest.param(Section(name='test'), {'unit': 'kg'}, True, id='section-unit'),
        pytest.param(Quantity(type=str), {'visible': False}, False, id='quantity-bool'),
        pytest.param(
            Quantity(type=float, unit='g'), {'unit': 'kg'}, False, id='quantity-unit'
        ),
        pytest.param(
            Quantity(type=str),
            {'visible': {'exclude': ['a']}},
            True,
            id='quantity-filter',
        ),
        pytest.param(Quantity(type=str), {'order': ['a']}, True, id='quantity-order'),
        pytest.param(
            SubSection(name='sub'), {'visible': False}, False, id='subsection-bool'
        ),
        pytest.param(
            SubSection(name='sub'),
            {'visible': {'include': ['a']}},
            True,
            id='subsection-filter',
        ),
        pytest.param(
            SubSection(name='sub'), {'unit': 'kg'}, True, id='subsection-unit'
        ),
    ],
)
def test_display_validation(definition, annotation, error):
    """A `display` annotation is checked against the definition it annotates."""
    if error:
        with pytest.raises(ValidationError):
            annotation_model = DisplayAnnotation(**annotation)
            annotation_model.m_definition = definition
    else:
        annotation_model = DisplayAnnotation(**annotation)
        annotation_model.m_definition = definition
        assert annotation_model.model_dump(exclude_unset=True) == annotation


@pytest.mark.parametrize(
    'model, definition, error',
    [
        pytest.param(
            SectionDisplayAnnotation(order=['a']),
            Section(name='test'),
            False,
            id='section-model-on-section',
        ),
        pytest.param(
            SectionDisplayAnnotation(order=['a']),
            Quantity(type=str),
            True,
            id='section-model-on-quantity',
        ),
        pytest.param(
            QuantityDisplayAnnotation(unit='kg'),
            Quantity(type=float, unit='g'),
            False,
            id='quantity-model-on-quantity',
        ),
        pytest.param(
            QuantityDisplayAnnotation(unit='kg'),
            Section(name='test'),
            True,
            id='quantity-model-on-section',
        ),
    ],
)
def test_display_specialized_models(model, definition, error):
    """The specialized display models are checked for where they are used."""
    if error:
        with pytest.raises(ValidationError):
            model.m_definition = definition
    else:
        model.m_definition = definition


@pytest.mark.parametrize(
    'display, valid',
    [
        pytest.param({'visible': {'exclude': ['a']}, 'order': ['a']}, True, id='valid'),
        pytest.param({'visible': False}, False, id='boolean-visible-on-section'),
        pytest.param({'unit': 'kg'}, False, id='unit-on-section'),
    ],
)
def test_display_annotation_is_registered(display, valid):
    """
    A `display` annotation given as plain data is validated rather than silently
    passed through, and a valid one keeps its values.
    """
    package = Package.m_from_dict(
        {
            'm_def': 'nomad.metainfo.metainfo.Package',
            'sections': {
                'Test': {
                    'm_annotations': {'display': display},
                    'quantities': {'a': {'type': 'str'}},
                }
            },
        }
    )
    section = package.all_definitions['Test']
    annotation = section.m_get_annotations('display')
    errors, _ = package.m_all_validate()

    if valid:
        assert isinstance(annotation, DisplayAnnotation)
        assert not errors
        assert section.m_to_dict()['m_annotations']['display'] == [display]
    else:
        # An invalid annotation is replaced by a stub that carries the error.
        assert annotation.m_error is not None
        assert len(errors) == 1
