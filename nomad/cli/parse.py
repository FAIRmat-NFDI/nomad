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

import click

from .cli import cli


@cli.command(help='Run parsing and normalizing locally.', name='parse')
@click.argument('MAINFILE', nargs=1, required=True, type=str)
@click.option(
    '--show-archive/--no-archive',
    default=True,
    help='Whether to print out the archive data into stdout.',
)
@click.option(
    '--archive-with-meta',
    is_flag=True,
    default=False,
    help='If the archive is printed, it would include metadata like m_def and m_annotations.',
)
@click.option(
    '--show-metadata',
    is_flag=True,
    default=False,
    help='Print the extracted repo metadata.',
)
@click.option(
    '--preview-plots',
    is_flag=True,
    default=False,
    help='Preview generated plots',
)
@click.option(
    '--skip-normalizers', is_flag=True, default=False, help='Do not run the normalizer.'
)
@click.option('--not-strict', is_flag=True, help='Do also match artificial parsers.')
@click.option(
    '--parser',
    help="""Skip matching and use the provided parser (format: `parsers/<name>`).
              Additional selection rules still apply for parsers with multiple main files.""",
)
@click.option(
    '--server-context',
    is_flag=True,
    default=False,
    help='Whether to use server context.',
)
@click.option('--username', default=None, help='Username for authentication.')
@click.option('--password', default=None, help='Password for authentication.')
@click.option(
    '--save-plot-dir',
    type=click.Path(file_okay=False, writable=True),
    default=None,
    help='Directory to save the plot',
)
@click.option(
    '--theme',
    type=click.Choice(['light', 'dark'], case_sensitive=False),
    default='light',
    help='Color theme to use (light, dark). only applies when previewing or saving plots.',
)
def _parse(
    mainfile,
    show_archive,
    archive_with_meta,
    show_metadata,
    preview_plots,
    skip_normalizers,
    not_strict,
    parser,
    server_context,
    username,
    password,
    save_plot_dir,
    theme,
):
    import json
    import os
    import sys

    from nomad import utils
    from nomad.client import normalize_all, parse
    from nomad.datamodel.metainfo.plot import resolve_plot_references
    from nomad.plots.template import get_plotly_template, update_plotly_layout

    kwargs = dict(
        strict=not not_strict,
        parser_name=parser,
        server_context=server_context,
        username=username,
        password=password,
    )

    entry_archives = parse(mainfile, **kwargs)

    for entry_archive in entry_archives:
        if not skip_normalizers:
            normalize_all(entry_archive)
            entry_archive.metadata.apply_archive_metadata(entry_archive)

        if show_archive:
            json.dump(
                entry_archive.m_to_dict(with_meta=archive_with_meta),
                sys.stdout,
                indent=2,
            )

        if show_metadata:
            metadata = entry_archive.metadata
            metadata.apply_archive_metadata(entry_archive)
            json.dump(metadata.m_to_dict(), sys.stdout, indent=4)

        if save_plot_dir or preview_plots:
            if not entry_archive.data:
                raise ValueError('The entry archive has no data.')
            elif (
                'figures' not in entry_archive.data or not entry_archive.data['figures']
            ):
                raise ValueError('The entry archive has no figures data.')
            import plotly.io as pio

            metadata = entry_archive.metadata

            for i, fig in enumerate(entry_archive.data['figures']):
                logger = utils.get_logger(__name__)
                config = {
                    'scrollZoom': True,
                    'displayModeBar': False,
                    'showTips': False,
                    'responsive': True,
                }
                if 'config' in fig.figure:
                    config.update(fig.figure['config'])

                file_path = (
                    os.path.join(
                        save_plot_dir,
                        f'{metadata.entry_name}_{i}.png',
                    )
                    if save_plot_dir
                    else None
                )

                selected_theme = theme or 'light'
                template = get_plotly_template(selected_theme)
                fig.figure['layout']['template'] = template
                fig.figure['layout'] = update_plotly_layout(fig.figure, selected_theme)

                try:
                    if preview_plots:
                        pio.show(fig.figure, config=config)
                    if file_path:
                        pio.write_image(fig.figure, file_path)
                except ValueError:
                    logger.info('Resolving references')
                    fig_kwargs = {}
                    for k, v in fig.figure.items():
                        fig_kwargs[k] = (
                            resolve_plot_references(
                                v, entry_archive.data, entry_archive, logger
                            )
                            or v
                        )
                    if preview_plots:
                        pio.show(fig_kwargs, config=config)
                    if file_path:
                        pio.write_image(
                            fig_kwargs,
                            file_path,
                        )
