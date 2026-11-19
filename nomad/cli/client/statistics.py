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

"""
A command that generates various statistics.
"""

# TODO This has to be migrated to v1. This was not done immediately, because we wanted
# to revise the statistics anyways: https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/issues/611

# import matplotlib.scale as mscale
# import matplotlib.transforms as mtransforms
# import matplotlib.pyplot as plt
# import matplotlib.ticker as ticker
# import numpy as np
# import click
# import json
# import datetime
# import subprocess

# from nomad.config import config
# from .client import client


# def codes(client, minimum=1, **kwargs):
#     data = client.repo.search(per_page=1, **kwargs).response().result

#     x_values = sorted([
#         code for code, values in data.statistics['dft.code_name'].items()
#         if code != 'not processed' and values.get('calculations', 1000) >= minimum], key=lambda x: x.lower())

#     return data.statistics, x_values, 'code_name', 'code'


# def dates(client, minimum=1, **kwargs):
#     data = client.repo.search(per_page=1, date_histogram=True, **kwargs).response().result

#     x_values = list([
#         x for x in data.statistics['date_histogram'].keys()])

#     return data.statistics, x_values, 'date_histogram', 'month'


# def error_fig(client):
#     _, labels, _, _ = codes(client)

#     def code_values(metric='code_runs', **kwargs):
#         result = client.repo.search(
#             per_page=1,
#             owner='admin',
#             metrics=[] if metric == 'code_runs' else metric,
#             **kwargs).response().result

#         return {
#             code: values[metric]
#             for code, values in result.quantities['dft.code_name'].items()
#             if code != 'not processed' and (not labels or code in labels) > 0}

#     # get the data
#     all_entries = code_values()
#     parser_failure_label = 'parser failure'
#     error_types = [
#         {'name': parser_failure_label, 'search': dict(system='not processed')},
#         {'name': 'failed system classification', 'search': dict(system='unavailable')},
#         {'name': 'no basis set available', 'search': dict(basis_set='unavailable')},
#         {'name': 'no XC functional available', 'search': dict(xc_functional='unavailable')}
#     ]
#     errors = {
#         error_type['name']: {
#             code: failures
#             for code, failures in code_values(**error_type['search']).items()}
#         for error_type in error_types}
#     errors_rates = {
#         error_type['name']: {
#             code: 0 if all_entries[code] == 0 else failures / all_entries[code]
#             for code, failures in code_values(**error_type['search']).items()}
#         for error_type in error_types}

#     fig, axs = plt.subplots(figsize=(15, 12), dpi=72, nrows=2)

#     def draw_error_chart(errors, ax, colors, entries=None, mul=1, scale=0.5):
#         n_bars = len(errors) - 1
#         leg_colors = list(colors)

#         x = np.arange(len(labels))  # the label locations
#         width = 0.7 / n_bars  # the width of the bars
#         plt.sca(ax)
#         plt.xticks(rotation=90)

#         if entries is not None:
#             ax.bar(x, [entries[code] for code in labels], width * n_bars, label='all entries', color=colors.pop(0))

#         i = -1
#         not_processed = [errors[parser_failure_label][code] * mul for code in labels]
#         ax.bar(x, not_processed, width * n_bars, label=parser_failure_label, color=colors.pop(0))
#         for key, values in errors.items():
#             if key != parser_failure_label:
#                 ax.bar(x + i * width, [values[code] * mul for code in labels], width, label=key, bottom=not_processed, color=colors.pop(0))
#                 i += 1

#         # Add some text for labels, title and custom x-axis tick labels, etc.
#         ax.set_yscale('power', exponent=scale)
#         ax.set_xticks(x)
#         ax.set_xticklabels(labels)
#         ax.legend()
#         leg = ax.get_legend()
#         for i in range(0, len(leg_colors)):
#             leg.legendHandles[i].set_color(leg_colors[i])

#         fig.tight_layout()

#     ax = axs[0]
#     ax.set_title('Absolute number of entries with parser errors or missing repository metadata compared to all entries per code')
#     ax.set_ylabel('number of entries', )
#     colors = ['grey', 'red', 'yellow', 'orange', 'brown']
#     draw_error_chart(errors, ax, entries=all_entries, mul=1, scale=0.25, colors=colors)
#     ax.yaxis.set_major_formatter(ticker.StrMethodFormatter('{x:,.0f}'))

#     ax = axs[1]
#     ax.set_title('Relative rates of entries with parser errors or missing repository metadata per code')
#     ax.set_ylabel('rate in %', )
#     colors = ['red', 'yellow', 'orange', 'brown']
#     draw_error_chart(errors_rates, ax, mul=100, colors=colors)

#     plt.show()

#     return fig, plt


# class Metric:
#     def __init__(self, metric, label=None, power=None, multiplier=1, format=None, cumulate=False):
#         if label is None:
#             label = metric

#         self.metric = metric
#         self.agg = None
#         self.label = label
#         self.multiplier = multiplier
#         self.power = power
#         self.format = format
#         self.cumulate = cumulate

#     def draw_axis(self, axis, data, x_values, x_positions, width, color, only=False):
#         label_color = 'black' if only else color
#         value_map = {
#             x: values[self.metric]
#             for x, values in data[self.agg].items()
#             if x in x_values}

#         if self.power is not None:
#             axis.set_yscale('power', exponent=self.power)
#         else:
#             axis.set_yscale('log')
#         axis.set_ylabel(self.label, color=label_color)

#         if self.format is not None:
#             axis.yaxis.set_major_formatter(ticker.StrMethodFormatter(self.format))

#         y_values = [value_map[x] * self.multiplier for x in x_values]
#         if self.cumulate:
#             y_values = np.array(y_values).cumsum()
#         axis.bar(x_positions, y_values, width, label=self.label, color=color, align='edge')
#         axis.tick_params(axis='y', labelcolor=label_color)

#         # TODO remove
#         # for x, v in zip(x_positions, y_values):
#         #     axis.text(x + .1, v, ' {:,}'.format(int(v)), color=color, fontweight='bold', rotation=90)
#         # import matplotlib.lines as mlines
#         # line = mlines.Line2D([min(x_positions), max(x_positions)], [72, 72], color=color)
#         # axis.add_line(line)


# def bar_plot(
#         client, retrieve, metric1, metric2=None, title=None, format_xlabel=None,
#         xlim={}, ylim=dict(bottom=1), **kwargs):
#     if format_xlabel is None:
#         format_xlabel = lambda x: x

#     metrics = [] if metric1.metric == 'code_runs' else [metric1.metric]
#     if metric2 is not None:
#         metrics += [] if metric2.metric == 'code_runs' else [metric2.metric]

#     data, x_values, agg, agg_label = retrieve(client, metrics=metrics, statistics=['dft.code_name'], **kwargs)
#     metric1.agg = agg
#     if metric2 is not None:
#         metric2.agg = agg

#     fig, ax1 = plt.subplots(figsize=(7, 4), dpi=72)
#     x = np.arange(len(x_values))
#     width = 0.8 / 2
#     if metric2 is None:
#         width = 0.8
#     plt.sca(ax1)
#     plt.xticks(rotation=90)
#     ax1.set_xticks(x)
#     ax1.set_xticklabels([format_xlabel(value) if value != 'Quantum Espresso' else 'Q. Espresso' for value in x_values])
#     ax1.margins(x=0.01)
#     ax1.set_xlim(**xlim)
#     # i = 0
#     # for label in ax1.xaxis.get_ticklabels():
#     #     label.set_visible(i % 4 == 0)
#     #     i += 1

#     if title is None:
#         title = 'Number of %s' % metric1.label
#         if metric2 is not None:
#             title += ' and %s' % metric2.label
#         title += ' per %s' % agg_label
#         ax1.set_title(title)
#     elif title != '':
#         ax1.set_title(title)

#     metric1.draw_axis(ax1, data, x_values, x - (width / 2), width, 'tab:blue', only=metric2 is None)
#     ax1.set_ylim(**ylim)
#     # ax1.set_yticks([40, 30, 20, 10, 5, 1, 0.5, 0.1])
#     if not metric2:
#         ax1.grid(which='major', axis='y', linestyle='-')

#     if metric2:
#         ax2 = ax1.twinx()  # instantiate a second axes that shares the same x-axis
#         metric2.draw_axis(
#             ax2, data, x_values, x + width / 2, width, 'tab:red')
#         ax2.set_ylim(bottom=1)

#     fig.tight_layout()

#     return fig, plt


# @client.command(help='Generate various matplotlib charts')
# @click.option('--errors', is_flag=True, help='Two charts with relative and absolute parser/normalizer errors per code.')
# @click.option('--x-axis', type=str, help='Aggregation used for x-axis, values are "code" and "time".')
# @click.option('--y-axis', multiple=True, type=str, help='Metrics used for y-axis, values are "entries", "energies", "calculations", "users".')
# @click.option('--cumulate', is_flag=True, help='Cumulate over x-axis.')
# @click.option('--title', type=str, help='Override chart title with given value.')
# @click.option('--total', is_flag=True, help='Provide total sums of key metrics.')
# @click.option('--save', type=str, help='Save to given file instead of showing the plot.')
# @click.option('--power', type=float, help='User power scale instead of log with the given inverse power.')
# @click.option('--open-access', is_flag=True, help='Only consider Open-Access data.')
# @click.option('--minimum', type=int, default=1, help='Only consider codes with at least the given amount of entries.')
# def statistics_plot(errors, title, x_axis, y_axis, cumulate, total, save, power, open_access, minimum):
#     from .client import create_client
#     client = create_client()

#     class PowerScale(mscale.ScaleBase):
#         name = 'power'

#         def __init__(self, axis, exponent, **kwargs):
#             mscale.ScaleBase.__init__(self, axis, **kwargs)
#             self.exponent = exponent

#         def set_default_locators_and_formatters(self, axis):
#             axis.set_major_locator(ticker.AutoLocator())
#             axis.set_major_formatter(ticker.ScalarFormatter())
#             axis.set_minor_locator(ticker.NullLocator())
#             axis.set_minor_formatter(ticker.NullFormatter())

#         def limit_range_for_scale(self, vmin, vmax, minpos):
#             return max(0., vmin), vmax

#         class Transform(mtransforms.Transform):
#             input_dims = 1
#             output_dims = 1
#             is_separable = True

#             def __init__(self, exponent):
#                 super().__init__()
#                 self.exponent = exponent

#             def transform_non_affine(self, a):
#                 return np.array(a)**self.exponent

#             def inverted(self):
#                 return PowerScale.Transform(1 / self.exponent)

#         def get_transform(self):
#             return self.Transform(self.exponent)

#     mscale.register_scale(PowerScale)

#     kwargs = {}
#     if cumulate:
#         kwargs.update(
#             power=1,
#             multiplier=1e-6,
#             format='{x:,.1f}M')
#     elif power is not None:
#         kwargs.update(
#             power=1 / power,
#             multiplier=1e-6,
#             format='{x:,.1f}M')

#     metrics = {
#         'entries': Metric(
#             'code_runs',
#             label='entries (code runs)',
#             cumulate=cumulate,
#             **kwargs),
#         'users': Metric(
#             'users',
#             cumulate=cumulate,
#             label='users that provided data'),
#         'energies': Metric(
#             'total_energies',
#             label='total energy calculations',
#             cumulate=cumulate,
#             **kwargs),
#         'calculations': Metric(
#             'calculations',
#             label='calculations (e.g. total energy)',
#             cumulate=cumulate,
#             **kwargs)
#     }

#     if errors:
#         fig, plt = error_fig(client)

#     owner = 'all' if open_access else 'admin'

#     if x_axis is not None:
#         assert 1 <= len(y_axis) <= 2, 'Need 1 or 2 y axis'

#         kwargs = {}
#         if x_axis == 'code':
#             x_axis = codes
#             kwargs.update(ylim=dict(bottom=1))
#         elif x_axis == 'time':
#             x_axis = dates
#             kwargs.update(
#                 ylim=dict(bottom=1),
#                 format_xlabel=lambda x: datetime.datetime.fromtimestamp(int(x) / 1000).strftime('%b %y'))
#         else:
#             assert False, 'x axis can only be "code" or "time"'

#         y_axis = [metrics[y] for y in y_axis]

#         fig, plt = bar_plot(
#             client, x_axis, *y_axis, title=title, owner=owner, minimum=minimum, **kwargs)

#     if errors or x_axis is not None:
#         if save is not None:
#             fig.savefig(save, bbox_inches='tight')
#         else:
#             plt.show()

#     if total:
#         data = client.repo.search(
#             per_page=1, owner=owner, statistics=True,
#             metrics=['total_energies', 'calculations', 'uploaders', 'authors', 'datasets']).response().result
#         print(json.dumps(data.statistics['total'], indent=4))


# @client.command(help='Generate table with basic statistics summary.')
# @click.option('--html', is_flag=True, help='Output HTML instead of plain text table.')
# @click.option('--geometries', is_flag=True, help='Use geometries not unique geometries.')
# @click.option('--public-path', type=str, default=config.fs.public, help='The path to the public data. Default is %s.' % config.fs.public)
# def statistics_table(html, geometries, public_path):
#     # get more stats for files
#     # uploads: find . -maxdepth 2 | wc -l
#     # public archive: find . -regex '.*archive.*public.*zip' -type f -print0 | du --files0-from=- -ch | grep total$
#     # public raw: find . -regex '.*raw.*public.*zip' -type f -print0 | du --files0-from=- -ch | grep total$

#     def get_statistic(response, quantity, value, metric):
#         quantity_data = response.statistics.get(quantity)
#         if quantity_data is None:
#             return 0
#         value_data = quantity_data.get(value)
#         if value_data is None:
#             return 0

#         value = value_data.get(metric)
#         return value if value is not None else 0

#     from nomad.cli.client import create_client
#     client = create_client()

#     geometry_metric = 'dft.unique_geometries' if not geometries else 'dft.geometries'

#     # search scc with system type
#     data_all = client.repo.search(
#         per_page=1, metrics=['dft.calculations'], statistics=['dft.system', 'dft.code_name']).response().result

#     entries = get_statistic(data_all, 'total', 'all', 'code_runs')
#     calculations = get_statistic(data_all, 'total', 'all', 'dft.calculations')
#     calculations_1d = get_statistic(data_all, 'dft.system', '1D', 'dft.calculations') \
#         + get_statistic(data_all, 'dft.system', 'atom', 'dft.calculations') \
#         + get_statistic(data_all, 'dft.system', 'molecule / cluster', 'dft.calculations')

#     calculations_2d = get_statistic(data_all, 'dft.system', '2D / surface', 'dft.calculations')
#     calculations_2d += get_statistic(data_all, 'dft.system', '2D', 'dft.calculations')
#     calculations_2d += get_statistic(data_all, 'dft.system', 'surface', 'dft.calculations')
#     calculations_3d = get_statistic(data_all, 'dft.system', 'bulk', 'dft.calculations')

#     metrics_all = client.repo.search(per_page=1, metrics=[geometry_metric, 'dft.quantities']).response().result
#     geometries = get_statistic(metrics_all, 'total', 'all', geometry_metric)
#     quantities = get_statistic(metrics_all, 'total', 'all', 'dft.quantities')

#     # search calcs quantities=section_k_band
#     band_structures = get_statistic(
#         client.repo.search(per_page=1, **{'dft.quantities': ['section_k_band']}).response().result,
#         'total', 'all', 'code_runs')

#     # search calcs quantities=section_dos
#     dos = get_statistic(
#         client.repo.search(per_page=1, **{'dft.quantities': ['section_dos']}).response().result,
#         'total', 'all', 'code_runs')

#     phonons = get_statistic(
#         client.repo.search(per_page=1, **{'dft.code_name': 'Phonopy'}).response().result,
#         'total', 'all', 'code_runs')

#     # files and sized
#     def run_shell_command(command):
#         process = subprocess.run(['bash', '-c', command], stdout=subprocess.PIPE)
#         out = process.stdout.decode('utf-8')
#         if process.stderr is not None:
#             err = process.stderr.decode('utf-8')
#             print('There is an error: %s' % str(err.strip()))
#         return out.split('\t')[0].strip()

#     archive_data = run_shell_command((
#         'find %s -regex \'.*archive.*public.*zip\' '
#         '-type f -print0 | du --files0-from=- -ch | grep total$') % public_path)
#     raw_data = run_shell_command((
#         'find %s -regex \'.*raw.*public.*zip\' '
#         '-type f -print0 | du --files0-from=- -ch | grep total$') % public_path)
#     n_uploads = run_shell_command(
#         'find %s -regex \'.*raw.*public.*zip\' -type f | wc -l' % public_path)

#     try:
#         n_uploads = '{:,}'.format(int(n_uploads))
#     except Exception:
#         pass

#     if not html:
#         print('''
#             Entries: {:,.0f}
#             Calculations, e.g. total energies: {:,.0f}
#             Geometries: {:,.0f}
#             Bulk crystals: {:,.0f}
#             2D / Surfaces: {:,.0f}
#             Atoms / Molecules: {:,.0f}
#             DOS: {:,.0f}
#             Band structures: {:,.0f}
#             Total parsed quantities: {:,.0f}
#             Public raw data: {}B
#             Public archive data: {}B
#             Number of uploads: {}
#         '''.format(
#             entries,
#             calculations,
#             geometries,
#             calculations_3d,
#             calculations_2d,
#             calculations_1d,
#             dos,
#             band_structures,
#             quantities,
#             raw_data,
#             archive_data,
#             n_uploads
#         ))

#     else:
#         print('''
#             <div class="container">
#                 <p>The <i>NOMAD Archive</i> stores calculations performed
#                 with all the most important and widely used electronic-structure and force-field codes
#                 in a code-independent format.
#                 </p>
#                 <p>Summary statistics of the Archive content (last update in {}):</p>
#                 <table class="table" style="text-align: left; max-width: 700px;">
#                     <thead>
#                         <tr>
#                         <th scope="col">Metric</th>
#                         <th scope="col">Value</th>
#                         </tr>
#                     </thead>
#                     <tbody>
#                         <tr>
#                         <td scope="row">Entries, i.e. code runs</td>
#                         <td>{:,.0f}</td>
#                         </tr>
#                         <tr>
#                         <td scope="row">Calculations, e.g. total energies</td>
#                         <td>{:,.0f}</td>
#                         </tr>
#                         <tr>
#                         <td scope="row">Geometries</td>
#                         <td>{:,.0f}</td>
#                         </tr>
#                         <tr>
#                         <td scope="row">Bulk Crystals</td>
#                         <td>{:,.0f}</td>
#                         </tr>
#                         <tr>
#                         <td scope="row">Surfaces</td>
#                         <td>{:,.0f}</td>
#                         </tr>
#                         <tr>
#                         <td scope="row">Molecules/Clusters</td>
#                         <td>{:,.0f}</td>
#                         </tr>
#                         <tr>
#                         <td scope="row">DOS</td>
#                         <td>{:,.0f}</td>
#                         </tr>
#                         <tr>
#                         <td scope="row">Band Structures</td>
#                         <td>{:,.0f}</td>
#                         </tr>
#                         <tr>
#                         <td scope="row">Phonon Calculations</th>
#                         <td>{:,.0f}</td>
#                         </tr>
#                         <tr>
#                         <td scope="row">Overall parsed quantities</td>
#                         <td>{:,.0f}</td>
#                         </tr>
#                     </tbody>
#                 </table>
#                 <p>
#                     Furthermore:
#                 </p>
#                 <ul>
#                     <li><b>{}</b> Uploads with <b>{}B</b> of raw data</li>
#                     <li><b>{}B</b> of archive data</li>
#                     <li>Data classified using <b>168</b> public metadata of the NOMAD Meta Info and <b>2,360</b> code-specific metadata</li>
#                 </ul>
#                 <p>
#                     For more and interactive statistics, use the <i>metadata</i> view of
#                     the <a href="https://nomad-lab.eu/prod/rae/gui/search">NOMAD Repository and Archvi search</a>.
#                 </p>
#                 <p>
#                     90% of VASP calculations are provided by
#                         <a href="http://aflowlib.org">AFLOWlib</a> (S. Curtarolo),
#                         <a href="http://oqmd.org"> OQMD</a> (C. Wolverton) and
#                         <a href="https://materialsproject.org">Materials Project</a> (K. Persson).
#                 </p>
#                 <p>
#                     You can further explore the statistics in the below dynamic histograms. To
#                     change the displayed quantity, select from the "Quantities" drop-down. To
#                     filter the data, click histogram bars for different filter combinations.
#                     To reset filters, click "Reset Filters".
#                 </p>
#                 <p>
#                     The archive data is represented in a code-independent, structured
#                     form. The archive structure and all quantities are described via the
#                     <a href="https://nomad-lab.eu/prod/rae/gui/metainfo">NOMAD Metainfo</a>.
#                     The NOMAD Metainfo defines a conceptual model to store the values connected
#                     to atomistic or <i>ab initio</i> calculations. A clear and usable metadata definition
#                     is a prerequisites to preparing the data for analysis that everybody
#                     can contribute to.
#                 </p>
#                 <p>
#                     In collaboration with the <a href="http://www.bbdc.berlin/">Berlin Big Data Center (BBDC)</a>,
#                     we use the Apache Flink infrastructure to support and go beyond the standard MapReduce model to enable
#                     rapid and complex queries.
#                 </p>
#                 <p>
#                     Contact concerning general aspects of the CoE: <a href="mailto:pietsch@fhi-berlin.mpg.de">Jessica Pietsch</a>
#                 </p>
#                 <p>
#                     Contact concerning the NOMAD Archive:
#                         <a href="mailto:markus.scheidgen@physik.hu-berlin.de">Markus Scheidgen</a>,
#                         <a href="mailto:ghiringhelli@fhi-berlin.mpg.de">Luca Ghiringhelli</a>
#                 </p>
#             </div>
#         '''.format(
#             datetime.datetime.now().strftime('%b %Y'),
#             entries,
#             calculations,
#             geometries,
#             calculations_3d,
#             calculations_2d,
#             calculations_1d,
#             dos,
#             band_structures,
#             phonons,
#             quantities,
#             n_uploads,
#             raw_data,
#             archive_data
#         ))
