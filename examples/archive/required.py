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
import json

from nomad.graph.graph_reader import ArchiveReader

# assume this is our archive
archive = {
    'metadata': {'upload_id': 'test_upload', 'entry_id': 'test_entry'},
    'results': {
        'properties': {
            'electronic': {
                'dos_electronic': [
                    {'energies': '/run/0/calculation/0/dos_electronic/0/energies'}
                ]
            }
        }
    },
    'run': [
        {
            'system': [
                {
                    'atoms': {
                        'labels': ['He', 'Ca', 'Fn', 'Cu'],
                        'atomic_numbers': 12,
                        'periodic': True,
                    },
                    'symmetry': [{'space_group_number': 221}],
                    'unknown': 'unknown',
                }
            ],
            'calculation': [
                {
                    'system_ref': '/run/0/system/0',
                    'energy': {'total': {'value': 0.2}},
                    'dos_electronic': [{'energies': [0, 1, 2, 3, 4, 5]}],
                    'eigenvalues': [],
                }
            ],
        }
    ],
    'workflow': [{'calculation_result_ref': '/run/0/calculation/0'}],
}

# read all resolved under results
query = {
    'results': {
        'm_request': {
            'directive': 'resolved',
            'include': ['*'],
            'resolve_inplace': True,
        }
    }
}

print(json.dumps(ArchiveReader.read_required(archive, query)))
# {
#   "archive":{
#     "results":{
#       "properties":{
#         "electronic":{
#           "dos_electronic":[
#             {
#               "energies":[
#                 0,
#                 1,
#                 2,
#                 3,
#                 4,
#                 5
#               ]
#             }
#           ]
#         }
#       }
#     }
#   }
# }

query = {
    'workflow[0]': {
        'calculation_result_ref': {
            'm_request': {
                'resolve-inplace': True,
                'depth': 0,  # read keys only at parent level
            },
            'system_ref': {
                'm_request': {
                    'directive': 'resolved',
                    'include': ['*'],  # read all under system_ref
                    'depth': None,  # override parent's setting
                }
            },
        }
    }
}

print(json.dumps(ArchiveReader.read_required(archive, query)))
# {
#   "archive":{
#     "workflow":[
#       {
#         "calculation_result_ref":{
#           "energy":"__INTERNAL__:../uploads/test_upload/archive/test_entry#/workflow/0/calculation_result_ref/energy",
#           "dos_electronic":"__INTERNAL__:../uploads/test_upload/archive/test_entry#/workflow/0/calculation_result_ref/dos_electronic",
#           "eigenvalues":"__INTERNAL__:../uploads/test_upload/archive/test_entry#/workflow/0/calculation_result_ref/eigenvalues",
#           "system_ref":{
#             "atoms":{
#               "labels":[
#                 "He",
#                 "Ca",
#                 "Fn",
#                 "Cu"
#               ],
#               "atomic_numbers":12,
#               "periodic":true
#             },
#             "symmetry":[
#               {
#                 "space_group_number":221
#               }
#             ]
#           }
#         }
#       }
#     ]
#   }
# }

query = {
    'workflow[0]': {
        'calculation_result_ref': {
            'm_request': {
                'resolve-inplace': True,
                'directive': 'resolved',
                'include': [
                    'e*',
                    's*',
                ],  # patter matching, this pattern does not propagate to children
            }
        }
    }
}

print(json.dumps(ArchiveReader.read_required(archive, query)))
# {
#     "archive": {
#         "workflow": [
#             {
#                 "calculation_result_ref": {
#                     "system_ref": {
#                         "atoms": {
#                             "labels": [
#                                 "He",
#                                 "Ca",
#                                 "Fn",
#                                 "Cu"
#                             ],
#                             "atomic_numbers": 12,
#                             "periodic": true
#                         },
#                         "symmetry": [
#                             {
#                                 "space_group_number": 221
#                             }
#                         ]
#                     },
#                     "energy": {
#                         "total": {
#                             "value": 0.2
#                         }
#                     },
#                     "eigenvalues": [
#
#                     ]
#                 }
#             }
#         ]
#     }
# }


query = {
    'workflow[0]': {
        'calculation_result_ref': {
            'm_request': {
                'resolve-inplace': True,
                'directive': 'resolved',
                'include': [
                    's*'
                ],  # patter matching, this pattern does not propagate to children
                'max_list_size': 2,  # limit list size to 2
            }
        }
    },
}

print(json.dumps(ArchiveReader.read_required(archive, query)))
# {
#   "archive":{
#     "workflow":[
#       {
#         "calculation_result_ref":{
#           "system_ref":{
#             "atoms":{
#               "labels":"__INTERNAL__:../uploads/test_upload/archive/test_entry#/workflow/0/calculation_result_ref/system_ref/atoms/labels",
#               "atomic_numbers":12,
#               "periodic":true
#             },
#             "symmetry":[
#               {
#                 "space_group_number":221
#               }
#             ]
#           }
#         }
#       }
#     ]
#   }
# }

query = {
    'workflow[0]': {
        'calculation_result_ref': {
            'm_request': {
                'resolve-inplace': True,  # parent resolve inplace
                'directive': 'resolved',
                'include': ['s*'],
            },
            'system_ref': {
                'm_request': {
                    'resolve-inplace': False,  # but child does not
                }
            },
        }
    }
}

with ArchiveReader(query) as reader:
    print(json.dumps(reader.read(archive)))
# {
#   "archive":{
#     "workflow":[
#       {
#         "calculation_result_ref":{
#           "system_ref":"../uploads/test_upload/archive/test_entry#/run/0/system/0"
#         }
#       }
#     ]
#   },
#   "m_ref_archives":{
#     "../uploads/test_upload/archive/test_entry":{
#       "run":[
#         {
#           "system":[
#             {
#               "atoms":{
#                 "labels":[
#                   "He",
#                   "Ca",
#                   "Fn",
#                   "Cu"
#                 ],
#                 "atomic_numbers":12,
#                 "periodic":true
#               },
#               "symmetry":[
#                 {
#                   "space_group_number":221
#                 }
#               ]
#             }
#           ]
#         }
#       ]
#     }
#   }
# }
