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

from enum import Enum
from typing import Any

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic.main import BaseModel

from nomad.metainfo import (
    AnnotationModel,
    Datetime,
    Definition,  # noqa: F401
    MEnum,
    Quantity,
    Reference,
    Section,
)
from nomad.utils import strip

from ...metainfo.data_type import Datatype
from ..data import Query
from .plot import PlotlyError


class ELNComponentEnum(str, Enum):
    StringEditQuantity = 'StringEditQuantity'
    URLEditQuantity = 'URLEditQuantity'
    EnumEditQuantity = 'EnumEditQuantity'
    RadioEnumEditQuantity = 'RadioEnumEditQuantity'
    AutocompleteEditQuantity = 'AutocompleteEditQuantity'
    FileEditQuantity = 'FileEditQuantity'
    BoolEditQuantity = 'BoolEditQuantity'
    NumberEditQuantity = 'NumberEditQuantity'
    SliderEditQuantity = 'SliderEditQuantity'
    DateTimeEditQuantity = 'DateTimeEditQuantity'
    DateEditQuantity = 'DateEditQuantity'
    TimeEditQuantity = 'TimeEditQuantity'
    RichTextEditQuantity = 'RichTextEditQuantity'
    ReferenceEditQuantity = 'ReferenceEditQuantity'
    UserEditQuantity = 'UserEditQuantity'
    AuthorEditQuantity = 'AuthorEditQuantity'
    QueryEditQuantity = 'QueryEditQuantity'
    ActionEditQuantity = 'ActionEditQuantity'


valid_eln_types = {
    'str': ['str'],
    'bool': ['bool'],
    'number': [
        'int',
        'float',
        'np.int64',
        'np.int32',
        'np.int16',
        'np.float64',
        'np.float32',
    ],
    'datetime': ['Datetime'],
    'enum': ['{type_kind: Enum, type_data: [Operator, Responsible_person]}'],
    'user': ['User'],
    'author': ['Author'],
    'reference': [''],
    'query': ['Query'],
}


valid_eln_components = {
    'str': [
        ELNComponentEnum.StringEditQuantity,
        ELNComponentEnum.URLEditQuantity,
        ELNComponentEnum.FileEditQuantity,
        ELNComponentEnum.RichTextEditQuantity,
        ELNComponentEnum.EnumEditQuantity,
    ],
    'bool': [ELNComponentEnum.BoolEditQuantity, ELNComponentEnum.ActionEditQuantity],
    'number': [
        ELNComponentEnum.NumberEditQuantity,
        ELNComponentEnum.SliderEditQuantity,
    ],
    'datetime': [
        ELNComponentEnum.DateTimeEditQuantity,
        ELNComponentEnum.DateEditQuantity,
        ELNComponentEnum.TimeEditQuantity,
    ],
    'enum': [
        ELNComponentEnum.EnumEditQuantity,
        ELNComponentEnum.AutocompleteEditQuantity,
        ELNComponentEnum.RadioEnumEditQuantity,
    ],
    'user': [ELNComponentEnum.AuthorEditQuantity],
    'author': [ELNComponentEnum.AuthorEditQuantity],
    'reference': [ELNComponentEnum.ReferenceEditQuantity],
    'query': [ELNComponentEnum.QueryEditQuantity],
}


class Filter(BaseModel):
    """A filter defined by an include list or and exclude list of the quantities or subsections."""

    include: list[str] | None = Field(
        None,
        description=strip(
            """
            The list of quantity or subsection names to be included.
        """
        ),
    )
    exclude: list[str] | None = Field(
        None,
        description=strip(
            """
            The list of quantity or subsection names to be excluded.
        """
        ),
    )


def validate_display(fields: dict, definition) -> None:
    """
    Checks the values of a `display` annotation against the definition it annotates.

    A display annotation means different things on a section and on a single property:
    on a section `visible` and `editable` are filters that select properties by name,
    while on a quantity or subsection they are booleans that switch that one property on
    or off. Without this check a mismatch is silently ignored by the GUI.
    """
    is_section = isinstance(definition, Section)
    kind = 'section' if is_section else type(definition).__name__.lower()

    for name in ('visible', 'editable'):
        value = fields.get(name)
        if value is None:
            continue
        if is_section:
            assert isinstance(value, Filter), (
                f'The `{name}` of a display annotation on a {kind} must be a filter '
                f'with an `include` and/or an `exclude` list of property names.'
            )
        else:
            assert isinstance(value, bool), (
                f'The `{name}` of a display annotation on a {kind} must be a boolean.'
            )

    assert is_section or fields.get('order') is None, (
        'The `order` of a display annotation can only be used on sections.'
    )
    assert isinstance(definition, Quantity) or fields.get('unit') is None, (
        'The `unit` of a display annotation can only be used on quantities.'
    )


class DisplayAnnotation(AnnotationModel):
    """
    The model that a `display` annotation is validated against.

    The annotation takes a different shape on a section than on a quantity, so this
    model accepts both and checks the values against the annotated definition. Schemas
    written in Python should use the specialized `SectionDisplayAnnotation` and
    `QuantityDisplayAnnotation` instead.
    """

    visible: Filter | bool | None = Field(  # type: ignore
        None,
        description=strip(
            """
            Defines the visible quantities and subsections of a section, or the
            visibility of a single quantity or subsection.
        """
        ),
    )
    editable: Filter | bool | None = Field(
        None,
        description=strip(
            """
            Defines the editable quantities and subsections of a section, or the
            editability of a single quantity or subsection.
        """
        ),
    )
    unit: str | None = Field(
        None,
        description=strip(
            """
            To determine the default display unit for quantity.
        """
        ),
    )
    order: list[str] | None = Field(
        None,
        description=strip(
            """
            To customize the order of the quantities and subsections.
        """
        ),
    )

    @field_validator('m_definition')
    @classmethod
    def validate_definition(cls, definition, values):  # pylint: disable=no-self-argument
        if definition:
            validate_display(values.data, definition)
        return definition


class QuantityDisplayAnnotation(AnnotationModel):
    """
    This annotations control how quantities are displayed in the GUI. Use the
    key `display` to add this annotation. For example in Python:

    ```python
    class Example(EntryData):
        sample_weight = Quantity(type=float, unit='g', a_display={'unit': 'kg', 'editable': False})
    ```

    or in YAML:
    ```yaml
    definitions:
      sections:
        Example:
          quantities:
            sample_weight:
              type: float
              unit: g
              m_annotations:
                display:
                  unit: kg
                  visible: true
                  editable: false
    ```
    """

    visible: bool | None = Field(  # type: ignore
        None,
        description=strip(
            """
            Defines the visibility of a quantity.
        """
        ),
    )
    editable: bool | None = Field(
        None,
        description=strip(
            """
            Defines the editablity of a quantity.
        """
        ),
    )
    unit: str | None = Field(
        None,
        description=strip(
            """
            To determine the default display unit for quantity.
        """
        ),
    )

    @field_validator('m_definition')
    @classmethod
    def validate_definition(cls, definition, values):  # pylint: disable=no-self-argument
        if definition:
            validate_display(values.data, definition)
        return definition


class SectionDisplayAnnotation(AnnotationModel):
    """
    This annotations control how sections are displayed in the GUI. Use the
    key `display` to add this annotation. `visible` and `editable` are filters that
    select properties by name, so they take an `include` and/or an `exclude` list.
    For example in Python:

    ```python
    class Example(MSection):
        m_def = Section(a_display={
            'visible': {'exclude': ['secret_quantity']},
            'order': ['name', 'description'],
        })
    ```

    or in YAML:
    ```yaml
    definitions:
      sections:
        Example:
          m_annotations:
            display:
              visible:
                exclude: [secret_quantity]
              order: [name, description]
    ```
    """

    visible: Filter | None = Field(  # type: ignore
        None,
        description=strip(
            """
            Defines the visible quantities and subsections.
        """
        ),
    )
    editable: Filter | None = Field(
        None,
        description=strip(
            """
            Defines the editable quantities and subsections.
        """
        ),
    )
    order: list[str] | None = Field(
        None,
        description=strip(
            """
            To customize the order of the quantities and subsections.
        """
        ),
    )

    @field_validator('m_definition')
    @classmethod
    def validate_definition(cls, definition, values):  # pylint: disable=no-self-argument
        if definition:
            validate_display(values.data, definition)
        return definition


class SectionProperties(BaseModel):
    """The display settings for quantities and subsections. (Deprecated)"""

    visible: Filter | None = Field(  # type: ignore
        1,
        description=strip(
            """
            Defines the visible quantities and subsections. (Deprecated)
        """
        ),
    )
    editable: Filter | None = Field(
        None,
        description=strip(
            """
            Defines the editable quantities and subsections. (Deprecated)
        """
        ),
    )
    order: list[str] | None = Field(
        None,
        description=strip(
            """
            To customize the order of the quantities and subsections. (Deprecated)
        """
        ),
    )


class ELNAnnotation(AnnotationModel):
    """
    These annotations control how data can be entered and edited.
    Use the key `eln` to add this annotations. For example:

    ```python
    class Sample(EntryData):
        sample_id = Quantity(type=str, a_eln=dict(component='StringEditQuantity'))`)
    ```

    or in YAML schemas:
    ```yaml
    Sample:
      quantities:
        sample_id:
          type: str
          m_annotations:
            eln:
              component: StringEditQuantity
    ```

    An `eln` annotation can be added to *section* and *quantity* definitions to different
    effects. In both cases, it controls how sections and quantities are represented in the GUI
    with different parameters; see below.

    The UI gives an overview about all ELN edit annotations and components
    [here](https://nomad-lab.eu/prod/v1/staging/gui/dev/editQuantity).
    """

    component: ELNComponentEnum | None = Field(
        None,
        description="""
        The form field component that is used to make the annotated quantity editable.
        If no component is given, the quantity won't be editable. This can be used on quantities only.

        The supported values are:

        `StringEditQuantity`: For editing simple short string values.<br/>
        `URLEditQuantity`: For editing strings that are validated to be URLs.<br/>
        `EnumEditQuantity`: For Editing enum values. Uses a dropdown list with enum values. This component may be used for short enumerates.<br/>
        `RadioEnumEditQuantity`: For Editing enum values. Uses radio buttons.<br/>
        `AutocompleteEditQuantity`: For editing enum values. Uses an autocomplete form with dropdown list. This component may be used for longer enumerates.<br/>
        `FileEditQuantity`: For editing a reference to a file. Will allow to choose a file or upload a file.<br/>
        `BoolEditQuantity`: For editing boolean choices.<br/>
        `NumberEditQuantity`: For editing numbers with our without unit.<br/>
        `SliderEditQuantity`: For editing numbers with a horizontal slider widget.<br/>
        `DateTimeEditQuantity`: For editing datetimes.<br/>
        `RichTextEditQuantity`: For editing long styled text with a rich text editor.<br/>
        `ReferenceEditQuantity`: For editing references to other sections.<br/>
        `UserEditQuantity`: For entering user information. Lets you choose a nomad user or enter information manually.<br/>
        `AuthorEditQuantity`: For entering author information manually.
    """,
    )

    label: str | None = Field(
        None,
        description="""
        [Deprecated] ELN label annotation has been deprecated and it is advised to
        utilize display annotation instead. Custom label for the quantity shown on the form field.
        It is recommended to adhere to the convention of using lowercase letters for the label,
        except for abbreviations which could be capitalized.
    """,
    )

    props: dict[str, Any] | None = Field(
        None,
        description="""
        A dictionary with additional props that are passed to the edit component.

        Examples:

        ```yaml
        description:
          type: str
          m_annotations:
            eln:
              component: RichTextEditQuantity
              props:
                height: 200
        time:
          type: Datetime
          m_annotations:
            eln:
              component: DateTimeEditQuantity
              props:
                format: "yyyy-MM-dd HH:mm:ss"
                views: ["hours", "minutes", "seconds"]
        search:
          type: Query
          m_annotations:
            eln:
              component: QueryEditQuantity
              props:
                maxData: 30
                storeInArchive: true
        ```
    """,
    )

    default: Any = Field(
        None,
        description="""
        Prefills any set form field component with the given value. This is different
        from the quantities `default` property. The quantities default is not stored
        in the data; the default value is assumed if no other value is given. The
        ELN form field default value will be stored, even if not changed.
    """,
    )
    defaultDisplayUnit: str | None = Field(
        None,
        description="""
        This attribute is deprecated, use the `unit` attribute of `display`
        annotation instead. Allows to define a default unit to initialize a
        `NumberEditQuantity` with. The unit has to be compatible with the unit
        of the annotation quantity and the annotated quantity must have a unit.
        Only applies to quantities and with `component=NumberEditQuantity`.
    """,
        deprecated=True,
    )

    minValue: int | float | None = Field(
        None,
        description="""
        Allows to specify a minimum value for quantity annotations with number type.
        Will show an error, if outside numbers are entered. Only works on quantities and
        in conjunction with `component=NumberEditQuantity`.
    """,
    )

    maxValue: int | float | None = Field(
        None,
        description="""
        Allows to specify a maximum value for quantity annotations with number type.
        Will show an error, if outside numbers are entered. Only works on quantities and
        in conjunction with `component=NumberEditQuantity`.
    """,
    )

    showSectionLabel: bool | None = Field(
        None,
        description="""
            To customize the ReferenceEditQuantity behaviour. If true the section label will be shown
            instead of referenced file name and the path to the section.
        """,
    )

    hide: list[str] | None = Field(
        None,
        description="""
        This attribute is deprecated. Use `visible` attribute of `display` annotation instead.
        Allows you to hide certain quantities from a section editor. Give a list
        of quantity names. Quantities must exist in the section that this annotation
        is added to. Can only be used in section annotations.
    """,
        deprecated=True,
    )

    overview: bool | None = Field(
        None,
        description="""
        Shows the annotation section on the entry's overview page. Can only be used on
        section annotations.""",
    )

    lane_width: str | int | None = Field(
        None,
        description="""
        Value to overwrite the css width of the lane used to render the annotation
        section and its editor.
    """,
    )

    properties: SectionProperties | None = Field(
        None,
        description="""
        The value to customize the quantities and sub sections of the annotation section.
        The supported keys:
        `visible`: To determine the visible quantities and sub sections by their names<br/>
        `editable`: To render things visible but not editable, e.g. in inheritance situations<br/>
        `order`: # To order things, properties listed in that order first, then the rest<br/>
    """,
    )
    model_config = ConfigDict(validate_assignment=True)

    @field_validator('m_definition')
    @classmethod
    def validate_component(cls, definition, values):  # pylint: disable=no-self-argument
        if not definition:
            return definition

        def assert_component(
            component, quantity_name, quantity_type, accepted_components
        ):
            assert component in accepted_components, (
                f'The component {component} is not compatible with the quantity '
                f'{quantity_name} of the type {quantity_type}. '
                f'Accepted components: {", ".join(accepted_components)}.'
            )

        component = values.data.get('component')
        if not component:
            return definition

        assert isinstance(definition, Quantity), (
            'Only quantities can be eln annotated with a component.'
        )
        quantity = definition
        type_ = quantity.type
        name = quantity.name

        assert len(quantity.shape) <= 1, 'Only scalars or lists can be edited.'

        if isinstance(type_, Datatype):
            if type_.standard_type().startswith('str'):
                assert_component(component, name, 'str', valid_eln_components['str'])
            elif type_.standard_type().startswith('bool'):
                assert_component(component, name, 'bool', valid_eln_components['bool'])
            elif type_.standard_type().startswith(('int', 'float')):
                assert_component(
                    component,
                    name,
                    type_.standard_type(),
                    valid_eln_components['number'],
                )
            elif type_.standard_type().startswith('datetime'):
                assert_component(
                    component, name, 'datetime', valid_eln_components['datetime']
                )
            elif type_.standard_type().startswith('enum'):
                assert_component(component, name, 'enum', valid_eln_components['enum'])
            elif isinstance(type_, Query):
                assert_component(
                    component, name, type(type_).__name__, valid_eln_components['query']
                )
        elif isinstance(type_, type):
            if type_.__name__ == 'str':
                assert_component(
                    component, name, type_.__name__, valid_eln_components['str']
                )
            elif type_.__name__ == 'bool':
                assert_component(
                    component, name, type_.__name__, valid_eln_components['bool']
                )
            elif type_.__name__ == 'User':
                assert_component(
                    component, name, type_.__name__, valid_eln_components['user']
                )
            elif type_.__name__ == 'Author':
                assert_component(
                    component, name, type_.__name__, valid_eln_components['author']
                )
            elif type_.__name__ == 'Query':
                assert_component(
                    component, name, type_.__name__, valid_eln_components['query']
                )

        elif type_ == Datetime:
            assert_component(
                component, name, type(type_).__name__, valid_eln_components['datetime']
            )

        elif isinstance(type_, MEnum):
            assert_component(
                component, name, type(type_).__name__, valid_eln_components['enum']
            )

        elif isinstance(type_, Reference):
            target_class = type_.target_section_def.section_cls
            if target_class.__name__ == 'User':
                assert_component(
                    component, name, target_class.__name__, valid_eln_components['user']
                )
            elif target_class.__name__ == 'Author':
                assert_component(
                    component,
                    name,
                    target_class.__name__,
                    valid_eln_components['author'],
                )
            else:
                assert_component(
                    component,
                    name,
                    type(type_).__name__,
                    valid_eln_components['reference'],
                )

        return definition


class BrowserAdaptors(str, Enum):
    RawFileAdaptor = 'RawFileAdaptor'


class BrowserRenderValues(str, Enum):
    JsonValue = 'JsonValue'
    HtmlValue = 'HtmlValue'


class BrowserAnnotation(AnnotationModel):
    """
    The `browser` annotation allows to specify if the processed data browser needs to
    display a quantity differently. It can be applied to quantities. For example

    ```python
        class Experiment(EntryData):
            description = Quantity(type=str, a_browser=dict(render_value='HtmlValue'))
    ```

    or in yaml

    ```yaml
    Experiment:
      quantities:
        description:
          type: str
          m_annotations:
            browser:
              render_value: HtmlValue
    ```
    """

    adaptor: BrowserAdaptors | None = Field(
        None,
        description="""
      Allows to change the *Adaptor* implementation that is used to render the
      lane for this quantity. Possible values are:

      `RawFileAdaptor`: An adopter that is used to show files, including all file
      actions, like file preview.
    """,
    )
    render_value: BrowserRenderValues | None = Field(
        None,
        description="""
      Allows to change the *Component* used to render the value of the quantity.
      Possible values are:

      `HtmlValue`: Renders a string as HTML.<br/>
      `JsonValue`: Renders a dict or list in a collapsable tree.
    """,
    )


class TabularMode(str, Enum):
    row = 'row'
    column = 'column'


class TabularParsingOptions(BaseModel):
    skiprows: list[int] | int | None = Field(None, description='Number of rows to skip')
    sep: str | None = Field(None, description='Character identifier of a separator')
    comment: str | None = Field(
        None, description='Character identifier of a commented line'
    )
    separator: str | None = Field(None, description='Alias for `sep`')


class TabularFileModeEnum(str, Enum):
    current_entry = 'current_entry'
    single_new_entry = 'single_new_entry'
    multiple_new_entries = 'multiple_new_entries'


class TabularMappingOptions(BaseModel):
    mapping_mode: TabularMode = Field(
        TabularMode.column,
        description="""
    This controls the behaviour of mapping of the extracted data onto NOMAD schema.

    The supported values are:

    `row`: A `list` of paths to the repeating sub-sections where the tabular quantities are to be filled from
        individual rows of the excel/csv file (i.e. in the row mode). Each path is a `/` separated list of
        nested sub-sections. The targeted sub-sections, will be considered when mapping table rows to quantities.
        Has to be used to annotate the quantity that holds the path to the `.csv` or excel file.<br/>
    `column`: A `list` of paths to the sub-sections where the tabular quantities are to be filled from the
        entire column of the excel/csv file (i.e. in the column mode). Each path is a `/`
        separated list of nested sub-sections. The targeted sub-sections, will be
        considered when mapping table columns to quantities. Has to be used to annotate the quantity that
        holds the path to the `.csv` or excel file.<br/>
    `enrty`: A `list` of paths to the (sub)sections where the tabular quantities are to be filled from individual rows
        of the excel/csv file, to create distinct entries. Each path is a
        `/` separated list of nested sub-sections. The targeted (sub)sections, will be
        considered when mapping table rows to quantities. The schema of the resultant entry follows the
        (sub)section's schema. In order to parse the entire schema using entry mode, then set the
        first item in this list to `root`.
        Has to be used to annotate the quantity that
        holds the path to the `.csv` or excel file.<br/>
    """,
    )
    file_mode: TabularFileModeEnum | None = Field(
        None,
        description="""
    This controls the behaviour of the parser towards working physical files in file system.

    The supported values are:

    `current_entry`: Processing the data into the same NOMAD entry.<br/>
    `single_new_entry`: Creating a new entry and processing the data into this new NOMAD entry.<br/>
    `multiple_new_entries`: Creating many new entries and processing the data into these new NOMAD entries.<br/>
    """,
    )
    sections: list[str] | None = Field(
        None,
        description="""
    A `list` of paths to the (sub)sections where the tabular quantities are to be filled from the data
    extracted from the tabular file.
    """,
    )


class TabularParserAnnotation(AnnotationModel):
    """
    Instructs NOMAD to treat a string valued scalar quantity as a file path and
    interprets the contents of this file as tabular data. Supports both
    `.csv` and Excel files.
    """

    parsing_options: TabularParsingOptions = Field(
        TabularParsingOptions(),
        description="""
        Options on how to extract the data from csv/xlsx file. Under the hood, NOMAD uses pandas `Dataframe`
        to parse the data from tabular files. These are the available options that can be passed down to the parser.

        The supported values are:

        `skiprows`: Number of rows to be skipped while reading the file.<br/>
        `sep`: The character used to separate cells (specific to csv files).<br/>
        `comment`: The character denoting the commented lines.<br/>
        `separator`: An alias for `sep`.<br/>
    """,
    )
    mapping_options: list[TabularMappingOptions] = Field(
        [],
        description="""
        A list of directives on how to map the extracted data from the csv/xlsx file to NOMAD. Each directive
        is a distinct directive, which allows for more modular definition of your tabular parser schema.
        If no item is provided, the entire schema is treated to be parsed under column mode.

        The supported values in each item of this list are:

        `mapping_mode`: A `list` of paths to the repeating sub-sections where the tabular quantities are to be filled from
            individual rows of the excel/csv file (i.e. in the row mode). Each path is a `/` separated list of
            nested sub-sections. The targeted sub-sections, will be considered when mapping table rows to quantities.
            Has to be used to annotate the quantity that holds the path to the `.csv` or excel file.<br/>
        `file_mode`: The character used to separate cells (specific to csv files).<br/>
        `sections`: The character denoting the commented lines.<br/>
    """,
    )


class PlotlyExpressTraceAnnotation(BaseModel):
    """
    Allows to plot figures using plotly Express.
    """

    method: str | None = Field(None, description='Plotly express plot method')
    layout: dict | None = Field(None, description='Plotly layout')

    x: list[float] | list[str] | str | None = Field(
        None, description='Plotly express x'
    )
    y: list[float] | list[str] | str | None = Field(
        None, description='Plotly express y'
    )
    z: list[float] | list[str] | str | None = Field(
        None, description='Plotly express z'
    )
    color: list[float] | list[str] | str | None = Field(
        None, description='Plotly express color'
    )
    symbol: str | None = Field(None, description='Plotly express symbol')
    title: str | None = Field(None, description='Plotly express title')


class PlotlyExpressAnnotation(PlotlyExpressTraceAnnotation):
    """
    Allows to plot multi trace figures using plotly Express.

    ```yaml
      sections:
        Example:
          base_sections:
            - 'nomad.datamodel.metainfo.plot.PlotSection'
          m_annotations:
            plotly_express:
              method: scatter
              x: '#xArr'
              y: '#yArr'
              label: 'Example Express Plot'
              index: 0
              open: true
              layout:
                title:
                  text: 'Example Express Plot'
                xaxis:
                  title:
                    text: 'x axis'
                yaxis:
                  title:
                    text: 'y axis'
              traces:
                - method: scatter
                  x: '#xArr'
                  y: '#zArr'
    ```
    """

    label: str | None = Field(None, description='Figure label')
    traces: list[PlotlyExpressTraceAnnotation] = Field(
        [],
        description="""
            List of traces added to the main trace defined by plotly_express method
        """,
    )


class PlotlyGraphObjectAnnotation(BaseModel):
    """
    Allows to plot figures using plotly graph object.

    ```yaml
        Example:
          base_sections:
            - 'nomad.datamodel.metainfo.plot.PlotSection'
          m_annotations:
            plotly_graph_object:
            - data:
                x: '#xArr'
                y: '#xArr'
              layout:
                title:
                  text: 'Plotly Graph Object'
              label: 'Plotly Graph Object'
              index: 1
              open: true
    ```
    """

    label: str | None = Field(None, description='Figure label')
    data: dict | None = Field(None, description='Plotly data')
    layout: dict | None = Field(None, description='Plotly layout')
    config: dict | None = Field(None, description='Plotly config')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.data or not isinstance(self.data, dict):
            raise PlotlyError('data should be a dictionary containing plotly data.')

    @field_validator('data')
    @classmethod
    def validate_data(cls, data):  # pylint: disable=no-self-argument
        assert isinstance(data, dict) and data, strip(
            f"""
            data should be a dictionary containing plotly data.
        """
        )

        return data


class PlotlySubplotsAnnotation(BaseModel):
    """
    Allows to plot figures in subplots.

    ```yaml
        Example:
          base_sections:
            - 'nomad.datamodel.metainfo.plot.PlotSection'
          m_annotations:
            plotly_subplots:
              parameters:
                rows: 2
                cols: 2
              layout:
                title:
                  text: 'All plots'
              plotly_express:
                - method: scatter
                  x: '#xArr'
                  y: '#yArr'
                  title: 'subplot 1'
                - method: scatter
                  x: '#xArr'
                  y: '#zArr'
                  title: 'subplot 2'
                - method: scatter
                  x: '#zArr'
                  y: '#xArr'
                  title: 'subplot 3'
                - method: scatter
                  x: '#zArr'
                  y: '#yArr'
                  title: 'subplot 4'
    ```
    """

    label: str | None = Field(None, description='Figure label')
    layout: dict | None = Field(None, description='Plotly layout')
    parameters: dict | None = Field(
        None,
        description="""
        plotly.subplots.make_subplots parameters i.e. rows, cols, shared_xaxes, shared_xaxes, horizontal_spacing , ...
        See [plotly make_subplots documentation](https://plotly.com/python-api-reference/generated/plotly.subplots.make_subplots.html) for more information.
    """,
    )
    plotly_express: list[PlotlyExpressAnnotation] = Field(
        [],
        description="""
        List of subplots defined by plotly_express method
    """,
    )


class TabularAnnotation(AnnotationModel):
    """
    Allows to map a quantity to a row or a column of a spreadsheet data-file. Should only be used
    in conjunction with `tabular_parser`.
    """

    name: str | None = Field(
        None,
        description="""
        The column name that should be mapped to the annotation quantity. Has to be
        the same string that is used in the header, i.e. first `.csv` line or first excel file `row`.
        For excel files with multiple sheets, the name can have the form `<sheet name>/<column name>`.
        Otherwise, only the first sheets is used. Has to be applied to the
        quantity that a column should be mapped to.
    """,
    )
    unit: str | None = Field(
        None,
        description="""
        The unit of the value in the file. Has to be compatible with the annotated quantity's
        unit. Will be used to automatically convert the value. If this is not defined,
        the values will not be converted. Has to be applied to the
        quantity that a column should be mapped to.
    """,
    )


class HDF5Annotation(AnnotationModel):
    """ """

    path: str | None = Field(
        None,
        description="""
        The path from the root of the h5 file to the target dataset.
    """,
    )
    unit: str | None = Field(
        None,
        description="""
            The unit of the value in the file. Has to be compatible with the annotated quantity's
            unit. Will be used to automatically convert the value. If this is not defined,
            the values will not be converted. Has to be applied to the
            quantity that a dataset should be mapped to.
        """,
    )


class RegexCondition(BaseModel):
    regex_path: str | None = Field(
        None,
        description="""
        The JMESPath to the target key in the dictionary. If not set, the path is assumed to be the same as
        the source path.
        """,
    )
    regex_pattern: str = Field(..., description='Regex pattern to be matched.')


class Condition(BaseModel):
    regex_condition: RegexCondition = Field(
        ..., description='Condition to check for the regex.'
    )


class Rule(BaseModel):
    source: str | None = Field(
        None, description='JMESPath to the source value in the source dictionary.'
    )
    target: str = Field(
        ..., description='JMESPath to the target value in the target dictionary.'
    )
    conditions: list[Condition] | None = Field(
        None, description='Conditions to check prior to applying the transformation.'
    )
    default_value: Any = Field(
        None, description='Default value to set if source is not found.'
    )
    use_rule: str | None = Field(
        None, description='Reference to another rule using #mapping_name.rule_name.'
    )

    def override_fields(self, referenced_rule: 'Rule') -> 'Rule':
        """
        Override fields from the referenced rule.

        Args:
            referenced_rule (Rule): The referenced rule whose fields will be overridden.

        Returns:
            Rule: The current rule with overridden fields from the referenced rule.
        """
        overridden_rule = self.copy()
        # Update non-empty fields from the referenced rule, excluding 'use_rule' to prevent circular references
        for field_name in type(referenced_rule).model_fields:
            field_value = getattr(referenced_rule, field_name)
            if field_value and field_name != 'use_rule':
                setattr(overridden_rule, field_name, field_value)
        return overridden_rule


Rule.model_rebuild()


class Rules(BaseModel):
    name: str | None = Field(
        None, description='Name for the rule set, for identification.'
    )
    other_metadata: str | None = Field(
        None, description='Placeholder for other metadata.'
    )
    rules: dict[str, Rule] = Field(..., description='Dictionary of named rules.')


class H5WebAnnotation(AnnotationModel):
    """
    Provides interface to the H5Web visualizer for HDF5 files.
    The annotation can be used for section and quantity definitions in order to
    include group and dataset attributes to the archive HDF5 file.

    Refer to https://h5web.panosc.eu/ for a more detailed description
    of the annotation fields.
    """

    axes: str | list[str] | None = Field(
        None,
        description="""
        Names of the HDF5Dataset quantities to plot on the independent axes.
        """,
    )
    signal: str | None = Field(
        None,
        description="""
        Name of the HDF5Dataset quantity to plot on the dependent axis.
        """,
    )
    long_name: str | None = Field(
        None,
        description="""
        Label for the hdf5 dataset. Note: this attribute will overwrite also the unit.
        """,
    )
    auxiliary_signals: list[str] | None = Field(
        None,
        description="""
        Additional datasets to include in plot as signal.
        """,
    )
    title: str | None = Field(
        None,
        description="""
        Title of the plot
        """,
    )
    errors: str | None = Field(
        None,
        description="""
        Name of the HDF5Dataset quantity to plot as error bars.
        """,
    )
    indices: int | list[int] | None = Field(
        None,
        description="""
        Indices of the HDF5Dataset to include in plot.
        """,
    )
    paths: list[str] = Field([], description="""List of section paths to visualize.""")


class SchemaAnnotation(AnnotationModel):
    """
    Used to annotate Schemas to control how and if they are displayed in the GUI.

    You can attach this annotation to a schema class definition using the
    `m_def` attribute, for example:

    ```python
    from nomad.datamodel.data import Schema
    from nomad.datamodel.metainfo.annotations import SchemaAnnotation

    class MySchema(Schema):
        m_def = Section(a_schema=SchemaAnnotation(
            label='My Schema',
            enabled=False
        ))
    ```
    """

    label: str = Field(
        description="""
        Custom label to show for this schema in the GUI when instantiating new
        entries from it. If not set, the label set for the section will be used.
        """,
    )
    enabled: bool = Field(
        True,
        description="""
        If set to False, the schema will not be available for instantiating
        entries in the GUI. Note that the schema can still be instantiated
        regularly with Python even it if is disabled in the GUI.
        """,
    )


class Mapper(BaseModel):
    """
    Specifications to map the contents from a source specified by mapper. If string,
    will be a path to the data following the jmespath grammar
    (see https://jmespath.org/specification.html) eg:

        'length(.array.set.set)'

    If additional transformation is required to the data before assignment, one can
    provide a tuple of function name and list of paths to the source data. The data are
    resolved then passed to the function which should be implemented in the parser
    class method.

        For example:

        ('get_eigenvalues_energies',
            [
                '.array.set.set[].set[].r',
                'length(.array.set.set)',
                'length(.array.set.set[0].set)'
            ]
        )
    """

    mapper: str | tuple[str, list[str]] | tuple[str, list[str], dict[str, Any]] = Field(
        '',
        description="""Mapper from dictionary to archive property either as path"""
        """ or Tuple of name of transformer function and list of paths to be resolved"""
        """ as argument to the function.""",
    )
    remove: bool | None = Field(None, description="""Removes data from source.""")
    cache: bool | None = Field(None, description="""Store value.""")
    path_parser: str = Field(
        'jmespath', description="""Name of the parser for paths."""
    )
    unit: str | None = Field(None, description="""Pint unit to be applied to value.""")
    indices: str | list[int] | None = Field(
        None,
        description="""Name of function to evaluate indices or the list of"""
        """ indices to include in data""",
    )
    search: str | None = Field(None, description="""Path to search on value.""")
    update_mode: str = Field(
        'merge',
        description="""
        Mode to update the section with new data during subsequent parsing.

        Available modes:
            - 'merge' (default): Recursively merge dictionaries, align list elements
            - 'append': Keep existing data, add incoming only if existing is None
            - 'replace': Completely replace existing data
            - 'merge@start': Align list[0] with existing[0]
            - 'merge@last': Align list[-1] with existing[-1]
            - 'merge@N': Align list[N] with existing[0]

        In annotation hierarchies, each mapper can specify its own update_mode. Child
        mappers' modes apply to their respective subsections, enabling per-subsection
        control during multi-pass parsing.

        Example:
            add_mapping_annotation(Section.field, 'key', '.path', update_mode='append')
        """,
    )


class MappingAnnotation(AnnotationModel):
    """
    Annotation model used with mapping parser.

    class MySection(MSection):

        m_def = Section(a_mapping={'hdf5': {mapper: 'data'}})
    """

    @model_validator(mode='before')
    def validate_mapper(cls, values):
        for name, value in values.items():
            if name in cls.model_fields:
                continue
            values[name] = Mapper.model_validate(value)
        return values

    class Config:
        extra = 'allow'


AnnotationModel.m_registry['eln'] = ELNAnnotation
AnnotationModel.m_registry['display'] = DisplayAnnotation
AnnotationModel.m_registry['browser'] = BrowserAnnotation
AnnotationModel.m_registry['tabular_parser'] = TabularParserAnnotation
AnnotationModel.m_registry['tabular'] = TabularAnnotation
AnnotationModel.m_registry['hdf5'] = HDF5Annotation
AnnotationModel.m_registry['h5web'] = H5WebAnnotation
AnnotationModel.m_registry['schema'] = SchemaAnnotation
AnnotationModel.m_registry['mapping'] = MappingAnnotation
