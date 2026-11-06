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

from .client import DataCiteClient, DataCiteException
from .models import (
    DoiRequestAttributes,
    DoiSingleResponsePayload,
    DoiMultiResponsePayload,
)
from .service import (
    create_attributes_from_args,
    create_attributes_from_dataset,
    create_attributes_from_upload,
    create_doi,
    create_doi_for_dataset,
    create_doi_for_upload,
    publish_doi,
    delete_doi,
)
from .utils import (
    generate_dataset_target_url,
    generate_project_target_url,
    generate_unique_doi_name,
)
