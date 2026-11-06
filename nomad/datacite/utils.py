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

import secrets

from nomad.config import config


def generate_dataset_target_url(doi: str) -> str:
    """Generates the URL a dataset DOI resolves to.

    The path below the site root is configured by `datacite.dataset_path`.
    """
    segments = [config.datacite.dataset_path.strip('/'), doi]

    return f'{config.services.base_url(True)}/' + '/'.join(
        segment for segment in segments if segment
    )


def generate_project_target_url(upload_id: str) -> str:
    """Generates the URL a project DOI resolves to.

    The GUI is mounted at the site root, not below the API base path.
    """

    return f'{config.services.base_url(True)}/projects/{upload_id}'


def generate_unique_doi_name() -> str:
    """Generates a unique DOI name of pattern {prefix}/nomad.{random}.

    The prefix is defined in the config. The random part is a base32 Crockford
    string of length 8, split into two groups of 4 characters.
    """
    RANDOM_LENGTH = 8
    RANDOM_SPLIT = 4

    prefix = config.datacite.prefix
    namespace = 'nomad.'
    random_str = generate_random_b32crockford(RANDOM_LENGTH, RANDOM_SPLIT)

    return f'{prefix}/{namespace}{random_str}'


def generate_random_b32crockford(length: int = 8, split: int = 4) -> str:
    """Returns a random base32 Crockford string (lower-case, without check symbol).

    The alphabet includes digits 0-9 and lowercase letters a-z except i, l, o, and u.
    """
    alphabet = '0123456789abcdefghjkmnpqrstvwxyz'  # 32 characters
    number = secrets.randbits(length * 5)  # 5 bits per character

    result = ''
    while number > 0:
        result += alphabet[number & 0b11111]
        number >>= 5
    result = result.ljust(length, '0')  # Pad with zeros if necessary

    if split > 0:
        result = '-'.join(result[i : i + split] for i in range(0, len(result), split))

    return result
