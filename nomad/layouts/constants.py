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

from typing import Any

_PROPERTY_VALUE_NODE_TYPES = (
    'quantity',
    'richText',
    'imagePreview',
    'plot',
    'hdf5',
)


def _resolved_request(
    *, exclude: list[str] | None = None, depth: int | None = None
) -> dict[str, Any]:
    """Build a graph request that resolves references for a widget payload."""
    request: dict[str, Any] = {'directive': 'resolved'}
    if exclude is not None:
        request['exclude'] = exclude
    if depth is not None:
        request['depth'] = depth
    return {'m_request': request}


def _plain_reference_request() -> dict[str, Any]:
    """Request a reference value without resolving its target section."""
    return {
        'm_request': {
            'directive': 'plain',
            'always_rewrite_references': True,
        }
    }


def _workflow_link_request() -> dict[str, Any]:
    return {
        'name': '*',
        'section': _plain_reference_request(),
    }


_BAND_STRUCTURE_ELECTRONIC = {
    'results': {
        'properties': {
            'electronic': {
                'band_structure_electronic': {
                    'segment': _resolved_request(exclude=['occupations']),
                    'reciprocal_cell': _resolved_request(),
                }
            }
        }
    }
}


_BAND_STRUCTURE_VIBRATIONAL = {
    'results': {
        'properties': {
            'vibrational': {
                'band_structure_phonon': {
                    'segment': _resolved_request(exclude=['occupations']),
                    'reciprocal_cell': _resolved_request(),
                }
            }
        }
    }
}


BUILTIN_NODE_TYPE_DEFAULTS: dict[str, Any] = {
    'brillouin_zone_electronic': {'request': _BAND_STRUCTURE_ELECTRONIC},
    'brillouin_zone_vibrational': {'request': _BAND_STRUCTURE_VIBRATIONAL},
    'band_structure_electronic': {'request': _BAND_STRUCTURE_ELECTRONIC},
    'band_structure_vibrational': {'request': _BAND_STRUCTURE_VIBRATIONAL},
    'material': {'request': {'results': {'material': '*'}}},
    'dos_electronic': {
        'request': {
            'results': {
                'properties': {
                    'electronic': {
                        'dos_electronic': _resolved_request(),
                        'dos_electronic_new': {
                            'data': {
                                'energies': _resolved_request(),
                                'total': _resolved_request(),
                                'energy_fermi': '*',
                                'energy_ref': '*',
                                'spin_channel': '*',
                                'band_gap': _resolved_request(),
                            }
                        },
                    }
                }
            }
        }
    },
    'dos_vibrational': {
        'request': {
            'results': {
                'properties': {'vibrational': {'dos_phonon': _resolved_request()}}
            }
        }
    },
    'heat_capacity': {
        'request': {
            'results': {
                'properties': {
                    'vibrational': {
                        'heat_capacity_constant_volume': {
                            'heat_capacities': _resolved_request(),
                            'temperatures': _resolved_request(),
                        }
                    }
                }
            }
        }
    },
    'helmholtz_free_energy': {
        'request': {
            'results': {
                'properties': {
                    'vibrational': {
                        'energy_free_helmholtz': {
                            'energies': _resolved_request(),
                            'temperatures': _resolved_request(),
                        }
                    }
                }
            }
        }
    },
    'radial_distribution_function': {
        'request': {
            'results': {
                'properties': {
                    'structural': {'radial_distribution_function': _resolved_request()}
                }
            }
        }
    },
    'radius_of_gyration': {
        'request': {
            'results': {
                'properties': {
                    'structural': {'radius_of_gyration': _resolved_request()}
                }
            }
        }
    },
    'geometry_optimization': {
        'request': {
            'results': {
                'properties': {
                    'geometry_optimization': {'energies': _resolved_request()}
                }
            }
        }
    },
    'trajectories': {
        'request': {
            'results': {
                'properties': {
                    'thermodynamic': {
                        'trajectory': {
                            'temperature': _resolved_request(),
                            'pressure': _resolved_request(),
                            'energy_potential': _resolved_request(),
                            'available_properties': _resolved_request(),
                        }
                    }
                }
            }
        }
    },
    'workflow': {
        'request': {
            'workflow2': {
                'name': '*',
                'inputs': _workflow_link_request(),
                'outputs': _workflow_link_request(),
                'tasks': {
                    'name': '*',
                    'section': _plain_reference_request(),
                    'task': _plain_reference_request(),
                    'inputs': _workflow_link_request(),
                    'outputs': _workflow_link_request(),
                },
            }
        }
    },
    'energy_volume_curve': {
        'request': {
            'results': {
                'properties': {
                    'mechanical': {'energy_volume_curve': _resolved_request()}
                }
            }
        }
    },
}

_LAYOUT_CONTEXT_OVERRIDE_KEYS = frozenset(('quantities', 'sections', 'results'))
_LAYOUT_SEARCH_METADATA_KEYS = frozenset(('quantities', 'sections', 'results'))
_LAYOUT_RESOLUTION_KEYS = frozenset(
    ('default_layout_id', 'matching_layouts', 'resolved_layout_id')
)
