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
Generates a msgpack database of springer-related quantities downloaded from
http://materials.springer.com. The database is structured as

space_group_number : normalized_formula : springer_id : entry
"""

import os.path
import re
import time
from typing import Any

import bs4
import requests

from nomad import archive
from nomad.archive import read_archive
from nomad.config import config

required_items = {
    'Alphabetic Formula:': 'alphabetic_formula',
    'Classification by Properties:': 'classification',
    'Compound Class(es):': 'compound_classes',
    'Dataset ID': 'id',
    'Space Group:': 'space_group_number',
    'Phase Label(s):': 'phase_labels',
}

spaces_re = re.compile(r'\s+')
search_re = re.compile(' href="(/isp/[^"]+)')
formula_re = re.compile(r'([A-Z][a-z]?)([0-9.]*)|\[(.*?)\]([0-9]+)')


def _update_dict(dict0: dict[str, float], dict1: dict[str, float]):
    for key, val in dict1.items():
        if key in dict0:
            dict0[key] += val
        else:
            dict0[key] = val


def _components(formula_str: str, multiplier: float = 1.0) -> dict[str, float]:
    # match atoms and molecules (in brackets)
    components = formula_re.findall(formula_str)

    symbol_amount: dict[str, float] = {}
    for component in components:
        element, amount_e, molecule, amount_m = component
        if element:
            if not amount_e:
                amount_e = 1.0
            _update_dict(symbol_amount, {element: float(amount_e) * multiplier})

        elif molecule:
            if not amount_m:
                amount_m = 1.0
            _update_dict(
                symbol_amount, _components(molecule, float(amount_m) * multiplier)
            )

    return symbol_amount


def normalize_formula(formula_str: str) -> str:
    symbol_amount = _components(formula_str)

    total = sum(symbol_amount.values())
    symbol_normamount = {e: round(a / total * 100.0) for e, a in symbol_amount.items()}

    formula_sorted = [
        f'{s}{symbol_normamount[s]}' for s in sorted(list(symbol_normamount.keys()))
    ]

    return ''.join(formula_sorted)


def parse_springer_entry(htmltext: str) -> dict[str, str]:
    """
    Parser the quantities in required_items from an html text.
    """
    soup = bs4.BeautifulSoup(htmltext, 'html.parser')
    results = {}
    for item in soup.find_all(attrs={'class': 'data-list__item'}):
        key = item.find(attrs={'class': 'data-list__item-key'})
        if not key:
            continue

        key_str = key.string.strip()
        if key_str not in required_items:
            continue

        value = item.find(attrs={'class': 'data-list__item-value'})
        value = spaces_re.sub(' ', value.get_text()).strip()
        final_key = required_items[key_str]

        if final_key == 'classification':
            value = [x.strip() for x in value.split(',')]
            value = [x for x in value if x != '–']

        if final_key == 'compound_classes':
            value = [x.strip() for x in value.split(',')]
            value = [x for x in value if x != '–']

        results[final_key] = value
        if len(results) >= len(required_items):
            break

    normalized_formula = None
    for formula_type in ['alphabetic_formula', 'phase_labels']:
        formula = results.get(formula_type, None)
        if formula:
            try:
                normalized_formula = normalize_formula(formula)
                break
            except Exception:
                pass

    results['normalized_formula'] = normalized_formula

    for item in soup.find_all(attrs={'class': 'about_content_comments'}):
        version_match = re.search(r'Version (.+)\.', item.string.strip())
        if version_match:
            results['version'] = version_match.group(1)
            break

    return results


def _merge_dict(dict0: dict[str, Any], dict1: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(dict1, dict) or not isinstance(dict0, dict):
        return dict1

    for k, v in dict1.items():
        if k in dict0:
            dict0[k] = _merge_dict(dict0[k], v)
        else:
            dict0[k] = v
    return dict0


def _download(path: str, max_n_query: int = 10, retry_time: int = 120) -> str:
    n_query = 0
    while True:
        response = requests.get(path)
        if response.status_code == 200:
            break
        if n_query > max_n_query:
            break
        n_query += 1
        time.sleep(retry_time)

    if response.status_code != 200:
        response.raise_for_status()

    return response.text


def update_springer(max_n_query: int = 10, retry_time: int = 120):
    """
    Downloads the springer quantities related to a structure from springer and updates
    database.
    """
    # load database
    # querying database with unavailable dataset leads to error,
    # get toc keys first by making an empty query
    with read_archive(config.normalize.springer_db_path) as springer_archive:
        archive_keys = springer_archive.keys()

    sp_data = archive.query_archive(
        config.normalize.springer_db_path, {spg: '*' for spg in archive_keys}
    )

    sp_ids: list[str] = []
    for spg in sp_data:
        if not isinstance(sp_data[spg], dict):
            continue
        for formula in sp_data[spg]:
            sp_ids += list(sp_data[spg][formula].keys())

    page = 1
    while True:
        # check springer database for new entries by comparing with local database
        root = f'http://materials.springer.com/search?searchTerm=&pageNumber={page}&datasourceFacet=sm_isp&substanceId='
        req_text = _download(root, max_n_query, retry_time)
        if 'Sorry,' in req_text:
            break

        paths = search_re.findall(req_text)

        if len(paths) == 0:
            break

        for path in paths:
            sp_id = os.path.basename(path)
            if sp_id in sp_ids:
                continue

            path = f'http://materials.springer.com{path}'
            req_text = _download(path, max_n_query, retry_time)
            try:
                data = parse_springer_entry(req_text)
            except Exception:
                continue

            space_group_number = data.get('space_group_number', None)
            normalized_formula = data.get('normalized_formula', None)
            if space_group_number is None or normalized_formula is None:
                continue
            aformula = data.get('alphabetic_formula', None)
            if aformula is None:
                aformula = data.get('phase_labels', None)
            compound = data.get('compound_classes', None)
            classification = data.get('classification', None)

            entry = dict(
                aformula=aformula,
                url=path,
                compound=compound,
                classification=classification,
            )
            sp_data = _merge_dict(
                sp_data, {str(space_group_number): {normalized_formula: {sp_id: entry}}}
            )

        page += 1

    archive.write_archive(config.normalize.springer_db_path, sp_data)
