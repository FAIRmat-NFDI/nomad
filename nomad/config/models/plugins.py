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
import shutil
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Literal, Union, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from nomad.actions.shared.constant import TaskQueue
from nomad.common import download_file, get_package_path, is_safe_relative_path, is_url
from nomad.config.models.north import NORTHTool

from .common import Options
from .ui import App

example_prefix = '__examples__'

if TYPE_CHECKING:
    from fastapi import FastAPI

    from nomad.actions import Action
    from nomad.metainfo import SchemaPackage
    from nomad.normalizing import Normalizer as NormalizerBaseClass
    from nomad.parsing import Parser as ParserBaseClass


class EntryPoint(BaseModel):
    """Base model for a NOMAD plugin entry points."""

    id: str | None = Field(
        None,
        description='Unique identifier corresponding to the entry point name. Automatically set to the plugin entry point name in pyproject.toml.',
        json_schema_extra={'hidden': True},
    )
    id_url_safe: str | None = Field(
        None,
        description="""URL-safe identifier for the plugin entry point. If no custom name
        is provided, one is automatically generated from the entry point id. Any custom
        identifier is checked for URL-safety and for collisions with other entry points
        with the same type within the same NOMAD deployment.""",
    )

    @field_validator('id_url_safe')
    @classmethod
    def validate_url_safe(cls, v: str | None) -> str | None:
        """Validates that id_url_safe is URL-safe.

        A URL-safe identifier contains only lowercase alphanumeric characters, dots,
        underscores, tildes, percent-encoded symbols or hyphens.
        """
        if v is not None and not re.match(r'^[a-zA-Z0-9\.~%_-]*$', v):
            raise ValueError(
                f'id_url_safe "{v}" is not URL-safe. URL-safe identifiers must contain '
                'only letters, numbers, underscores, percent-encoded characters, tildes '
                'and hyphens.'
            )
        return v

    entry_point_type: str = Field(
        description='Determines the entry point type.',
        json_schema_extra={'hidden': True},
    )
    name: str | None = Field(None, description='Name of the plugin entry point.')
    description: str | None = Field(
        None, description='A human readable description of the plugin entry point.'
    )
    plugin_package: str | None = Field(
        None,
        description='The plugin package from which this entry points comes from.',
        json_schema_extra={'hidden': True},
    )

    def dict_safe(self):
        """Used to serialize the non-confidential parts of a plugin model. This
        function can be overridden in subclasses to expose more information.
        """
        return self.model_dump(
            include=EntryPoint.model_fields.keys(), exclude_none=True
        )


class AppEntryPoint(EntryPoint):
    """Base model for app plugin entry points."""

    entry_point_type: Literal['app'] = Field(
        'app',
        description='Determines the entry point type.',
        json_schema_extra={'hidden': True},
    )
    app: App = Field(description='The app configuration.')

    def dict_safe(self):
        return self.model_dump(
            include=AppEntryPoint.model_fields.keys(), exclude_none=True
        )


class SchemaPackageEntryPoint(EntryPoint):
    """Base model for schema package plugin entry points."""

    entry_point_type: Literal['schema_package'] = Field(
        'schema_package',
        description='Specifies the entry point type.',
        json_schema_extra={'hidden': True},
    )

    def load(self) -> 'SchemaPackage':
        """Used to lazy-load a schema package instance. You should override this
        method in your subclass. Note that any Python module imports required
        for the schema package should be done within this function as well."""
        pass


class NormalizerEntryPoint(EntryPoint):
    """Base model for normalizer plugin entry points."""

    entry_point_type: Literal['normalizer'] = Field(
        'normalizer',
        description='Determines the entry point type.',
        json_schema_extra={'hidden': True},
    )
    level: int = Field(
        0,
        description="""
        Integer that determines the execution order of this normalizer within
        the processing of an individual entry. Normalizers with the lowest level
        is run first.
        """,
    )

    def load(self) -> 'NormalizerBaseClass':
        """Used to lazy-load a normalizer instance. You should override this
        method in your subclass. Note that any Python module imports required
        for the normalizer class should be done within this function as well."""
        pass


class ParserEntryPoint(EntryPoint):
    """Base model for parser plugin entry points."""

    entry_point_type: Literal['parser'] = Field(
        'parser',
        description='Determines the entry point type.',
        json_schema_extra={'hidden': True},
    )
    level: int = Field(
        0,
        description="""
        Integer that determines the execution order of this parser within an
        upload. Parser with lowest level will be executed first. Note that this
        only controls the order in which matched parsers are executed, but does
        not affect the order in which parsers are matched to files.
    """,
    )
    aliases: list[str] = Field([], description="""List of alternative parser names.""")
    mainfile_contents_re: str | None = Field(
        None,
        description="""
        A regular expression that is applied the content of a potential mainfile.
        If this expression is given, the parser is only considered for a file, if the
        expression matches.
    """,
    )
    mainfile_name_re: str = Field(
        r'.*',
        description="""
        A regular expression that is applied the name of a potential mainfile.
        If this expression is given, the parser is only considered for a file, if the
        expression matches.
    """,
    )
    mainfile_mime_re: str = Field(
        r'.*',
        description="""
        A regular expression that is applied the mime type of a potential
        mainfile. If this expression is given, the parser is only considered
        for a file, if the expression matches.
    """,
    )
    mainfile_binary_header: bytes | None = Field(
        None,
        description="""
        Matches a binary file if the given bytes are included in the file.
    """,
    )
    mainfile_binary_header_re: bytes | None = Field(
        None,
        description="""
        Matches a binary file if the given binary regular expression bytes matches the
        file contents.
    """,
    )
    mainfile_alternative: bool = Field(
        False,
        description="""
        If True, the parser only matches a file, if no other file in the same directory
        matches a parser.
    """,
    )
    mainfile_contents_dict: dict | None = Field(
        None,
        description="""
        Used to check the contents of structured data files like JSON, HDF5 or
        csv/excel files. Parser will match the file if the dictionary provided matches
        the contents of the file. One can also use the following reserved keys for
        special queries:

            - `__has_key: str`

                Plain string or regular expression of a key in the data to match

            - `__has_all_keys: list[str]`

                List of keys that must be present in the data to match

            - `__has_only_keys: list[str]`

                List of keys that must exclusively be present in the data

            - `__has_comment: str` (only for csv/xlsx files)

                Comments that are ignored, must be at the top level of the dictionary

        In case of a csv/excel file for example, one can set this attribute to
        `{'__has_all_keys': [<column names>]}` in order to check if certain columns
        exist in a given sheet. Also in order to check if a certain
        sheet name with specific column names exist, one may set this attribute to:
        `{'<sheet name>': {'__has_all_keys': [<column names>]}}`.
    """,
    )
    supported_compressions: list[str] = Field(
        [],
        description="""
        Files compressed with the given formats (e.g. xz, gz) are uncompressed and
        matched like normal files.
    """,
    )

    def load(self) -> 'ParserBaseClass':
        """Used to lazy-load a parser instance. You should override this method
        in your subclass. Note that any Python module imports required for the
        parser class should be done within this function as well."""
        pass

    def dict_safe(self):
        # The binary data types are removed from the safe serialization: binary
        # data is not JSON serializable.
        keys = set(list(ParserEntryPoint.model_fields.keys()))
        keys.remove('mainfile_binary_header_re')
        keys.remove('mainfile_binary_header')

        return self.model_dump(include=keys, exclude_none=True)


class ElnParserEntryPoint(ParserEntryPoint):
    """Parser entry point model for matching of a file to generate an associated ELN
    archive.
    """

    eln_m_def: str = Field(
        'nomad.datamodel.metainfo.eln.ElnParserSection',
        description="""
        The ELN schema that is used for the data section of the created archive.
        """,
    )
    raw_file_m_def: str = Field(
        'nomad.datamodel.metainfo.eln.ElnParserRawFile',
        description="""
        The schema that is set as the data section of the matched mainfile.
        """,
    )
    update: bool = Field(
        False,
        description="""
        If True, the parser will update the existing ELN data.
        """,
    )
    data_type: str = Field(
        'raw file',
        description="""
        The type of the data that is in the matched file.
        """,
    )

    def load(self) -> 'ParserBaseClass':
        from nomad.parsing.parser import ElnMatchingParser

        return ElnMatchingParser(**self.model_dump())


class UploadResource(BaseModel):
    """Represents a request to include a certain resource into an example
    upload. Can point to a local folder/file, or alternatively to an online
    resource that can be downloaded.
    """

    path: str = Field(
        description="""
        Path to a file/folder within the python package (filepaths should start
        from the package root directory) or to an online URL.
        """
    )
    target: str = Field(
        '', description='File path within the upload where the file should be stored.'
    )


class ExampleUploadEntryPoint(EntryPoint):
    """Base model for example upload plugin entry points."""

    entry_point_type: Literal['example_upload'] = Field(
        'example_upload',
        description='Determines the entry point type.',
        json_schema_extra={'hidden': True},
    )
    category: str | None = Field(description='Category for the example upload.')
    title: str | None = Field(description='Title of the example upload.')
    description: str | None = Field(
        description='Longer description of the example upload.'
    )
    resources: None | (
        # Note that the order here matters: pydantic may interpret a dictionary
        # as a list of strings instead of an UploadResource object if the order
        # is wrong here.
        list[UploadResource | str] | UploadResource | str
    ) = Field(None, description='List of data resources for this example upload.')
    from_examples_directory: bool = Field(
        False,
        description='Whether this example upload should be read from the "examples" directory.',
        json_schema_extra={'hidden': True},
    )

    def get_package_path(self):
        """Once all built-in example uploads have been removed, this function
        call can be replaced with `get_package_path(self.plugin_package)`.
        """
        return (
            os.path.abspath('examples/data')
            if self.from_examples_directory
            else get_package_path(self.plugin_package)
        )

    def resolve_resource(self, resource: UploadResource, upload_path: str):
        """Used to resolve an `UploadResource` by downloading/copying it's
        contents to the upload path.

        Args:
            resource: The resource to resolve.
            upload_path: The root folder for the upload where the data should be
                stored.
        """

        # Perform initial checks
        url = False
        copy_folder_contents = False
        source = resource.path
        target = resource.target
        if is_url(source):
            url = True
        else:
            content_suffix = '/*'
            if source.endswith(content_suffix):
                copy_folder_contents = True
                source = source[: -len(content_suffix)]

            # Check that only safe relative paths are given as target
            assert is_safe_relative_path(target), (
                f'Upload resource target "{resource.target}" in example upload "{self.id}" is targeting files outside the upload directory.'
            )

            # Check that only safe relative paths are given as source
            assert is_safe_relative_path(source), (
                f'Upload resource path "{resource.path}" in example upload "{self.id}" is targeting files outside the Python package directory.'
            )

            source = os.path.join(self.get_package_path(), source)

            # Validate that files/folders exist
            assert os.path.exists(source), (
                f'Upload resource path "{resource.path}" in example upload "{self.id}" could not be found.'
            )

        # Determine file target location and create missing folder structures.
        # If no target is given, the file/folder is copied as it is to the
        # upload root location. If the target points to an existing folder, the
        # file/folder is copied there. Otherwise a new file/folder is
        # constructed at the given target location, also creating any missing
        # folder structures. This behaviour is similar to the unix `cp` command
        # (with the addition of automatic folder creation).
        if resource.target:
            target = os.path.join(upload_path, target)
            if os.path.isdir(target):
                if not copy_folder_contents:
                    target = os.path.join(target, os.path.basename(source))
            else:
                os.makedirs(
                    target if copy_folder_contents else os.path.dirname(target),
                    exist_ok=True,
                )
        else:
            target = (
                upload_path
                if copy_folder_contents
                else os.path.join(upload_path, os.path.basename(source))
            )

        # Download online resources
        if url:
            download_file(source, target)
        # Copy file/folder from python package
        else:
            if os.path.isdir(source):
                if copy_folder_contents:
                    for item in os.listdir(source):
                        src_path = os.path.join(source, item)
                        dest_path = os.path.join(target, item)
                        if os.path.isdir(src_path):
                            shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
                        else:
                            shutil.copyfile(src_path, dest_path)
                else:
                    shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copyfile(source, target)

    def load(self, upload_path: str):
        """Function for adding files/folders into the upload. Called when the
        example upload is instantiated.

        You may overload this method to customize the resource loading. Make use
        of the 'resolve_resource' function that can handle e.g. file downloading
        and file resolution within the Python package for you. When fetching or
        creating files with custom logic, remember to store the resulting files
        to the given `upload_path`.

        Args:
            upload_path: The filepath for the upload root folder. Any
                downloaded/created/copied files should be placed in this folder
                for them to end up correctly in the upload.
        """
        for resource in self.resources:
            self.resolve_resource(cast(UploadResource, resource), upload_path)

    def dict_safe(self):
        return self.model_dump(
            include=ExampleUploadEntryPoint.model_fields.keys(), exclude_none=True
        )

    @model_validator(mode='before')
    @classmethod
    def _validate(cls, values):
        # Normalize all different forms of input to a list of
        # ExampleUploadResource objects.
        if isinstance(values, BaseModel):
            values = values.model_dump(exclude_none=True)
        resources = values.get('resources', None)
        resource_objects = []
        if resources:
            if not isinstance(resources, list):
                resources = [resources]
            for resource in resources:
                if isinstance(resource, str):
                    resource = {'path': resource}
                elif isinstance(resource, UploadResource):
                    resource = resource.dict()
                resource_objects.append(resource)

        values['resources'] = resource_objects

        return values


class APIEntryPoint(EntryPoint):
    """Base model for API plugin entry points.

    An API entry point either (a) returns a FastAPI instance from ``load()``
    that will be mounted under ``{api_base_path}/apis/{id_url_safe}`` (or
    under a custom ``prefix``), or (b) declares an ``external_url`` pointing
    to an API served by a separate service.
    """

    entry_point_type: Literal['api'] = Field(
        'api',
        description='Specifies the entry point type.',
        json_schema_extra={'hidden': True},
    )

    prefix: str | None = Field(
        None,
        description=(
            'The path under which the API is mounted, relative to the base path of '
            'the NOMAD backend. If not set, it is automatically generated as '
            '``apis/{id_url_safe}``, which guarantees a predictable, collision-free '
            'location. You should not set this manually unless you know what you '
            'are doing: a custom prefix must not collide with any other route served '
            'by the NOMAD backend. Cannot be combined with ``external_url``.'
        ),
    )

    external_url: str | None = Field(
        None,
        description=(
            'Absolute URL to an API served outside of NOMAD. When set, '
            '``load()`` is not called and no mount is performed.'
        ),
    )

    @model_validator(mode='before')
    @classmethod
    def _validate(cls, v):
        import urllib.parse

        if isinstance(v, BaseModel):
            v = v.model_dump(exclude_none=True)

        prefix = v.get('prefix')
        if prefix is not None:
            if not isinstance(prefix, str):
                raise ValueError('prefix must be a string')
            if urllib.parse.quote(prefix) != prefix:
                raise ValueError('prefix must be a valid URL path')
            v['prefix'] = prefix.strip('/') or None

        external_url = v.get('external_url')
        if external_url:
            parsed = urllib.parse.urlparse(external_url)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc:
                raise ValueError('external_url must be an absolute http(s) URL')
            if v.get('prefix'):
                raise ValueError('prefix cannot be combined with external_url')

        return v

    def load(self) -> 'FastAPI | None':
        """Used to lazy-load the API instance. You should override this
        method in your subclass when serving the API from inside NOMAD. Leave
        as-is when using ``external_url``. Note that any Python module imports
        required for the API should be done within this function as well."""
        return None

    def dict_safe(self):
        """Used to serialize the non-confidential parts of a plugin model. This
        function can be overridden in subclasses to expose more information.
        """
        return self.model_dump(
            include=APIEntryPoint.model_fields.keys(), exclude_none=True
        )


class DashboardEntryPoint(EntryPoint):
    """Base model for dashboard plugin entry points.

    A dashboard entry point either (a) returns a FastAPI instance from
    ``load()`` that will be mounted under
    ``{api_base_path}/dashboards/{id_url_safe}``, or (b) declares an
    ``external_url`` pointing to a dashboard served by a separate service.
    """

    entry_point_type: Literal['dashboard'] = Field(
        'dashboard',
        description='Specifies the entry point type.',
        json_schema_extra={'hidden': True},
    )

    external_url: str | None = Field(
        None,
        description=(
            'Absolute URL to a dashboard served outside of NOMAD. When set, '
            '``load()`` is not called and no mount is performed.'
        ),
    )

    launch_modes: list[Literal['embedded', 'tab']] = Field(
        ['embedded', 'tab'],
        description=(
            'Supported launch modes for the dashboard. "embedded" renders the '
            'dashboard inside an iframe in the NOMAD GUI; "tab" opens it in a '
            'new browser tab. The order matters: the first entry is the '
            'default action when the dashboard is clicked in the GUI.'
        ),
    )

    icon: str | None = Field(
        None,
        description=(
            'Optional absolute URL of an icon to show in the dashboards menu. '
            'The icon must be hosted by the dashboard plugin (or any other '
            'reachable origin).'
        ),
    )

    @model_validator(mode='before')
    @classmethod
    def _validate(cls, v):
        import urllib.parse

        if isinstance(v, BaseModel):
            v = v.model_dump(exclude_none=True)

        external_url = v.get('external_url')
        if external_url:
            parsed = urllib.parse.urlparse(external_url)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc:
                raise ValueError('external_url must be an absolute http(s) URL')

        icon = v.get('icon')
        if icon:
            parsed = urllib.parse.urlparse(icon)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc:
                raise ValueError('icon must be an absolute http(s) URL')

        if 'launch_modes' in v:
            if not v['launch_modes']:
                raise ValueError('launch_modes must contain at least one entry')

        return v

    def load(self) -> 'FastAPI | None':
        """Lazy-load the dashboard's FastAPI instance. Override in subclasses
        when serving the dashboard from inside NOMAD. Leave as-is when using
        ``external_url``.
        """
        return None

    def dict_safe(self):
        return self.model_dump(
            include=DashboardEntryPoint.model_fields.keys(), exclude_none=True
        )


class ActionEntryPoint(EntryPoint):
    """Base model for action plugin entry points."""

    entry_point_type: Literal['action'] = Field(
        'action',
        description='Determines the entry point type.',
        json_schema_extra={'hidden': True},
    )
    task_queue: str = Field(
        default=TaskQueue.CPU, description='Determines the task queue for this action'
    )
    groups: list[str] | None = Field(
        None,
        description='List of groups that are allowed to start/execute the given action.',
    )
    users: list[str] | None = Field(
        None,
        description='List of user IDs that are allowed to start/execute the given action.',
    )
    priority_key: int | None = Field(
        None,
        le=5,
        ge=1,
        description='Priority key for the action. Lower values indicate higher priority.',
    )
    priority_fairness_key: Literal['user_id'] | None = Field(
        None,
        description='Priority fairness key for the action. Determines how priority is assigned between users.',
    )

    @model_validator(mode='before')
    @classmethod
    def _validate(cls, values):
        if isinstance(values, BaseModel):
            values = values.model_dump(exclude_none=True)
        return values

    def load(self) -> 'Action':
        """Used to load an action instance. You should override this method in your subclass."""
        pass

    def dict_safe(self):
        return self.model_dump(
            include=ActionEntryPoint.model_fields.keys(), exclude_none=True
        )


class PluginBase(BaseModel):
    """
    Base model for a NOMAD plugin.

    This should not be used. Plugins should instantiate concrete Plugin models like
    Parser or Schema.
    """

    plugin_type: str = Field(
        description='The type of the plugin.',
    )
    id: str | None = Field(None, description='The unique identifier for this plugin.')
    name: str = Field(
        description='A short descriptive human readable name for the plugin.'
    )
    description: str | None = Field(
        None, description='A human readable description of the plugin.'
    )
    plugin_documentation_url: str | None = Field(
        None, description='The URL to the plugins main documentation page.'
    )
    plugin_source_code_url: str | None = Field(
        None, description='The URL of the plugins main source code repository.'
    )

    def dict_safe(self):
        """Used to serialize the non-confidential parts of a plugin model. This
        function can be overridden in subclasses to expose more information.
        """
        return self.model_dump(
            include=PluginBase.model_fields.keys(), exclude_none=True
        )


class NORTHToolEntryPoint(EntryPoint):
    """Base model for NORTH tool plugin entry points."""

    entry_point_type: Literal['north_tool'] = Field(
        'north_tool',
        description='Determines the entry point type.',
        json_schema_extra={'hidden': True},
    )
    north_tool: NORTHTool

    def load(self) -> NORTHTool:
        return self.north_tool

    def dict_safe(self):
        return self.model_dump(
            include=NORTHToolEntryPoint.model_fields.keys(), exclude_none=True
        )


# Backwards compatibility
NorthToolEntryPoint = NORTHToolEntryPoint


EntryPointType = Union[  # noqa
    SchemaPackageEntryPoint,
    ParserEntryPoint,
    NormalizerEntryPoint,
    AppEntryPoint,
    ExampleUploadEntryPoint,
    APIEntryPoint,
    DashboardEntryPoint,
    ActionEntryPoint,
    NORTHToolEntryPoint,
]


class EntryPoints(Options):
    options: dict[str, EntryPointType] = Field(
        dict(), description='The available plugin entry points.'
    )

    def filtered_keys(self) -> list[str]:
        """Return entry point keys matching the include and exclude patterns."""
        available_keys = list(self.options)

        if self.include is None:
            included_keys = available_keys
        else:
            included_keys = []
            for pattern in self.include:
                included_keys.extend(
                    key
                    for key in available_keys
                    if key not in included_keys and fnmatchcase(key, pattern)
                )

        excluded_patterns = self.exclude or []
        return [
            key
            for key in included_keys
            if not any(fnmatchcase(key, pattern) for pattern in excluded_patterns)
        ]


class PluginPackage(BaseModel):
    name: str = Field(
        description='Name of the plugin Python package, read from pyproject.toml.'
    )
    description: str | None = Field(
        None, description='Package description, read from pyproject.toml.'
    )
    version: str | None = Field(
        None, description='Plugin package version, read from pyproject.toml.'
    )
    homepage: str | None = Field(
        None,
        description='Link to the plugin package homepage, read from pyproject.toml.',
    )
    documentation: str | None = Field(
        None,
        description='Link to the plugin package documentation page, read from pyproject.toml.',
    )
    repository: str | None = Field(
        None,
        description='Link to the plugin package source code repository, read from pyproject.toml.',
    )
    entry_points: list[str] = Field(
        description='List of entry point ids contained in this package, read form pyproject.toml'
    )


class Plugins(BaseModel):
    entry_points: EntryPoints = Field(
        description='Used to control plugin entry points.'
    )
    plugin_packages: dict[str, PluginPackage] = Field(
        description="""
        Contains the installed installed plugin packages with the package name
        used as a key. This is autogenerated and should not be modified.
        """
    )
