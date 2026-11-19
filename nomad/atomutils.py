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

from __future__ import annotations

import functools
import itertools
import logging
import math
import re
from collections.abc import Iterable
from functools import reduce
from string import ascii_uppercase
from typing import TYPE_CHECKING, Any, cast

import ase.data
import ase.geometry
import numpy as np
from ase import Atoms
from ase.formula import Formula as ASEFormula
from ase.utils import pbc2pbc

from nomad.aflow_prototypes import aflow_prototypes
from nomad.constants import atomic_masses

valid_elements = set(ase.data.chemical_symbols[1:])

if TYPE_CHECKING:
    from nomad.datamodel.results import ElementalComposition, Material, System


def get_summed_atomic_mass(atomic_numbers: np.ndarray) -> float:
    """
    Calculates the summed atomic mass for the given atomic numbers.

    Args:
        atomic_numbers: Array of valid atomic numbers

    Returns:
        The atomic mass in kilograms.
    """
    # It is assumed that the atomic numbers are valid at this point.
    mass = np.sum(atomic_masses[atomic_numbers])
    return mass if not math.isnan(mass) else None


def get_summed_mass(atomic_numbers=None, masses=None, indices=None, atom_labels=None):
    """Used to retrieve the mass of a system given a list or a
    dictionary containing the mass information.
    """
    indices = indices.tolist() if isinstance(indices, np.ndarray) else indices
    if atomic_numbers is not None:
        atomic_numbers = atomic_numbers[indices] if indices else atomic_numbers
        mass = np.sum(atomic_masses[atomic_numbers])
        return mass if not math.isnan(mass) else None
    elif masses:
        try:
            if isinstance(masses, dict) and atom_labels is not None:
                atom_labels = atom_labels[indices] if indices else atom_labels
                return sum(masses[label] for label in atom_labels)
            else:
                masses = masses[indices] if indices else masses
                return sum(masses)
        except Exception:
            return None
    else:
        return None


def get_masses_from_computational_model(
    archive, repr_system: System | None = None, method_index: int = -1
) -> list[float] | dict[str, float]:
    """
    Gets the masses based on the masses provided in atom parameters
    of the computational model. Only returns the mass list in case
    that the atomic masses are not available, e.g., when the elements/species
    are unknown.

    Args:
        archive: the archive containing the computational model
        repr_system: the representative system sub-section of the archive

    Returns:
        List of the masses according to the list of atoms.
    """

    def isclose(a, b, rel_tol=1e-09, abs_tol=0.0):
        return abs(a - b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)

    sec_method = None
    try:
        sec_run = archive.run[-1]
        sec_method = (
            sec_run.method[method_index] if sec_run.get('method') is not None else None
        )
    except IndexError:
        logging.warning(
            'Supplied indices or necessary sections do not exist in archive. Cannot get the atomic masses.'
        )
    model_atom_parameters = sec_method.get('atom_parameters') if sec_method else None
    if not model_atom_parameters:
        return []

    atoms = repr_system.get('atoms') if repr_system else None
    if not atoms:
        return []

    atomic_numbers = (
        atoms.species if atoms.species is not None else atoms.atomic_numbers
    )
    if (
        get_summed_atomic_mass(atomic_numbers) is not None
    ):  # defaults to atomic masses when available
        return []
    atom_labels = atoms.get('labels')

    masses = [params.get('mass') for params in model_atom_parameters]
    if any([mass is None for mass in masses]):
        return []
    if any([math.isnan(mass) for mass in masses]):
        return []

    if atom_labels is None:
        return masses

    if len(atom_labels) != len(
        masses
    ):  # atom params likely corresponds to a dictionary based on labels
        mass_labels = [params.get('label') for params in model_atom_parameters]
        if mass_labels is not None:
            if any(
                [label not in atom_labels for label in mass_labels]
            ):  # cannot perform the mapping between atom_params and atoms with mismatched labels
                return []
            else:
                return {label: mass for label, mass in zip(mass_labels, masses)}
        else:  # cannot perform the mapping between atom_params and atoms without labels
            return []
    else:  # direct correspondence between atom_parameters list and atoms list
        # try to create a dictionary based on atom labels
        mass_dict = {label: mass for label, mass in zip(atom_labels, masses)}
        for label, mass in zip(atom_labels, masses):
            if label in mass_dict and not isclose(
                mass, mass_dict[label]
            ):  # mass uniqueness is not based on the atom labels, use full list instead
                return masses
        return mass_dict


def get_volume(basis: np.ndarray) -> float:
    """
    Calculates the volume of the given parallelepiped.

    Args:
        basis: 3x3 matrix with basis vectors of a parallellepiped as rows.

    Returns:
        Volume of the parallelepiped defined by the basis.
    """
    return np.abs(np.linalg.det(basis))


def is_valid_basis(basis: np.ndarray) -> bool:
    """
    Checks if the given set of basis vectors are valid. Currently does not
    check for linear independence, only for empty rows.

    Args:
        basis: 3x3 matrix with basis vectors as rows.

    Returns:
        True if the basis is valid, False otherwise.
    """
    if basis is None:
        return False
    for row in np.asarray(basis):
        if not np.any(row):
            return False
    return True


def translate_pretty(fractional: np.ndarray, pbc: bool | np.ndarray) -> np.ndarray:
    """Translates atoms such that fractional positions are minimized."""
    pbc = pbc2pbc(pbc)
    assert isinstance(pbc, np.ndarray)

    for i in range(3):
        if not pbc[i]:
            continue

        indices = np.argsort(fractional[:, i])
        sp = fractional[indices, i]

        widths = (np.roll(sp, 1) - sp) % 1.0
        fractional[:, i] -= sp[np.argmin(widths)]
        fractional[:, i] %= 1.0
    return fractional


def get_center_of_positions(
    positions: np.ndarray,
    cell: np.ndarray | None = None,
    pbc: bool | np.ndarray = True,
    weights=None,
    relative=False,
) -> np.ndarray:
    """Calculates the center of positions with the given weighting. Also takes
    the periodicity of the system into account.

    The algorithm is replicated from:
    https://en.wikipedia.org/wiki/Center_of_mass#Systems_with_periodic_boundary_conditions

    Args:
        positions: Positions of the atoms. Whether these are cartesian or
            relative is controlled by the 'relative' argument.
        cell: Unit cell
        pbc: Periodic boundary conditions
        relative: If true, the input and output positions are given relative to
            the unit cell. Otherwise the positions are cartesian.

    Returns:
        The position of the center of mass in the given system.
    """
    pbc = pbc2pbc(pbc)
    assert isinstance(pbc, np.ndarray)
    relative_positions = positions if relative else to_scaled(positions, cell)

    rel_com = np.zeros((1, 3))
    for i_comp in range(3):
        i_pbc = pbc[i_comp]
        i_pos = relative_positions[:, i_comp]
        if i_pbc:
            theta = i_pos * 2 * np.pi
            xi = np.cos(theta)
            zeta = np.sin(theta)
            if weights:
                xi *= weights
                zeta *= weights

            xi_mean = np.mean(xi)
            zeta_mean = np.mean(zeta)

            mean_theta = np.arctan2(-zeta_mean, -xi_mean) + np.pi
            com_rel = mean_theta / (2 * np.pi)
            rel_com[0, i_comp] = com_rel
        else:
            if weights:
                total_weight = np.sum(weights)
                rel_com[0, i_comp] = np.sum(i_pos * weights) / total_weight
            else:
                rel_com[0, i_comp] = np.sum(i_pos)

    return rel_com if relative else to_cartesian(rel_com, cell)


def wrap_positions(
    positions: np.ndarray,
    cell: np.ndarray | None = None,
    pbc: bool | np.ndarray = True,
    center: tuple = (0.5, 0.5, 0.5),
    pretty_translation=False,
    eps: float = 1e-12,
    relative=False,
) -> np.ndarray:
    """
    Wraps the given position so that they are within the unit cell.

    Args:
        positions: Positions of the atoms. Whether these are cartesian or
            relative is controlled by the 'relative' argument.
        cell: Lattice vectors.
        pbc: For each axis in the unit cell decides whether the positions will
            be wrapped along this axis.
        center: The position in fractional coordinates that the wrapped
            positions will be nearest possible to.
        eps: Small number to prevent slightly negative coordinates from being
            wrapped.
        relative: If true, the input and output positions are given relative to
            the unit cell. Otherwise the positions are cartesian.
    """
    pbc = pbc2pbc(pbc)
    assert isinstance(pbc, np.ndarray)
    shift = np.asarray(center) - 0.5 - eps

    # Don't change coordinates when pbc is False
    shift[np.logical_not(pbc)] = 0.0

    relative_pos = positions if relative else to_scaled(positions, cell)
    relative_pos -= shift

    if pretty_translation:
        relative_pos = translate_pretty(relative_pos, pbc)
        shift = np.asarray(center) - 0.5
        shift[np.logical_not(pbc)] = 0.0
        relative_pos += shift
    else:
        for i, periodic in enumerate(pbc):
            if periodic:
                relative_pos[:, i] %= 1.0
                relative_pos[:, i] += shift[i]

    return relative_pos if relative else to_cartesian(relative_pos, cell)


def unwrap_positions(
    positions: np.ndarray,
    cell: np.ndarray,
    pbc: bool | np.ndarray,
    relative=False,
) -> np.ndarray:
    """
    Unwraps the given positions so that continuous structures are not broken by
    cell boundaries.

    Args:
        positions: Positions of the atoms. Whether these are cartesian or
            relative is controlled by the 'relative' argument.
        cell: Lattice vectors.
        pbc: For each axis in the unit cell decides whether the positions will
            be wrapped along this axis.
        center: The position in fractional coordinates that the wrapped
            positions will be nearest possible to.
        eps: Small number to prevent slightly negative coordinates from being
            wrapped.
        relative: If true, the input and output positions are given relative to
            the unit cell. Otherwise the positions are cartesian.
    """
    pbc = pbc2pbc(pbc)
    assert isinstance(pbc, np.ndarray)
    if not any(pbc):
        return positions

    relative_pos = positions if relative else to_scaled(positions, cell)
    center_of_pos = get_center_of_positions(relative_pos, pbc=pbc, relative=True)
    relative_shifted = relative_pos + ([[0.5, 0.5, 0.5]] - center_of_pos)
    wrapped_relative_pos = wrap_positions(relative_shifted, pbc=pbc, relative=True)

    return (
        wrapped_relative_pos if relative else to_cartesian(wrapped_relative_pos, cell)
    )


def chemical_symbols(atomic_numbers: Iterable[int]) -> list[str]:
    """
    Converts atomic numbers to chemical_symbols.

    Args:
        atomic_numbers: The atomic numbers to convert.

    Returns:
        Array of chemical symbols.
    """
    return [ase.data.chemical_symbols[x] for x in atomic_numbers]


def to_scaled(positions: np.ndarray, cell: np.ndarray | None = None) -> np.ndarray:
    """
    Converts cartesian positions into scaled position one using the given
    cell lattice vectors as a basis.

    Args:
        positions: Scaled positions.
        cell: Lattice vectors.

    Returns:
        The given positions in scaled coordinates.
    """
    return np.linalg.solve(complete_cell(cell).T, positions.T).T


def to_cartesian(positions: np.ndarray, cell: np.ndarray | None = None) -> np.ndarray:
    """
    Converts scaled positions into cartesian one using the given cell
    lattice vectors as a basis.

    Args:
        positions: Scaled positions.
        cell: Lattice vectors.

    Returns:
        The given positions in cartesian coordinates.
    """
    cartesian_positions = np.dot(positions, complete_cell(cell))
    return cartesian_positions


def complete_cell(cell: np.ndarray) -> np.ndarray:
    """
    Creates placeholder axes for cells with zero-dimensional lattice vectors
    in order to do linear algebra.

    Args:
        cell: Lattice vectors.

    Returns:
        The given cell with zero-dimensional lattice vectors filled with
        placeholder axes.
    """
    return ase.geometry.complete_cell(cell)


def reciprocal_cell(cell: np.ndarray) -> np.ndarray:
    """
    Returns the reciprocal cell without the factor or 2*Pi.

    Args:
        cell: Lattice vectors.

    Returns:
        Reciprocal cell as a 3x3 array.
    """
    return np.linalg.pinv(cell).transpose()


def find_match(pos: np.ndarray, positions: np.ndarray, eps: float) -> int | None:
    """
    Attempts to find a position within a larger list of positions.

    Args:
        pos: The point to search for
        positions: The points within which the search is performed.
        eps: Match tolerance.

    Returns:
        Index of the matched position or None if match not found.
    """
    displacements = positions - pos
    distances = np.linalg.norm(displacements, axis=1)
    min_arg = np.argmin(distances)
    min_value = distances[min_arg]
    if min_value <= eps:
        return cast(int, min_arg)
    else:
        return None


def cellpar_to_cell(
    cellpar: np.ndarray,
    ab_normal: tuple = (0, 0, 1),
    a_direction: np.ndarray | None = None,
    degrees=False,
) -> np.ndarray:
    """
    Creates a 3x3 cell from the given lattice_parameters.

    The returned cell is orientated such that a and b are normal to `ab_normal`
    and a is parallel to the projection of `a_direction` in the a-b plane.

    Default `a_direction` is (1,0,0), unless this is parallel to `ab_normal`,
    in which case default `a_direction` is (0,0,1).

    The returned cell has the vectors va, vb and vc along the rows. The cell
    will be oriented such that va and vb are normal to `ab_normal` and va will
    be along the projection of `a_direction` onto the a-b plane.

    Args:
        cellpar: Six lattice parameters: [a, b, c, alpha, beta, gamma].
        The following typical convention is used:

            a = length of first basis vector
            b = length of second basis vector
            c = length of third basis vector
            alpha = angle between b and c in radians
            beta  = angle between a and c in radians
            gamma = angle between a and b in radians
        degrees: Use degrees in place of radians.

    Returns:
        Six parameters (in this order) as a numpy
        array. Here is an explanation of each parameter:
    """
    if not degrees:
        cellpar[3:6] *= 180.0 / np.pi

    return ase.geometry.cell.cellpar_to_cell(cellpar, ab_normal, a_direction)  # type: ignore


def cell_to_cellpar(cell: np.ndarray, degrees=False) -> np.ndarray:
    """
    Returns lattice parameters for the given cell.

    Args:
        normalized_cell: The normalized cell as a 3x3 array. Each row is a
            basis vector.
        degrees: Use degrees in place of radians.

    Returns:
        Six parameters [a, b, c, alpha, beta, gamma]. The following typical
        convention ik used:

            a = length of first basis vector
            b = length of second basis vector
            c = length of third basis vector
            alpha = angle between b and c in radians
            beta  = angle between a and c in radians
            gamma = angle between a and b in radians
    """
    # Lengths
    lengths = np.linalg.norm(cell, axis=1)

    # Angles
    angles = np.zeros(3)
    for i in range(3):
        j = (i + 1) % 3
        k = (i + 2) % 3
        angles[i] = np.dot(cell[j], cell[k]) / (lengths[j] * lengths[k])
    angles = np.arccos(np.clip(angles, -1.0, 1.0))
    if degrees:
        angles *= 180.0 / np.pi

    return np.concatenate((lengths, angles), axis=0)


def get_symmetry_string(
    space_group: int, wyckoff_sets: list[Any], is_2d: bool = False
) -> str:
    """
    Used to serialize symmetry information into a string. The Wyckoff
    positions are assumed to be normalized and ordered as is the case if using
    the matid-library.

    The symmetry analysis of 2D structures is run by artificially extending the
    system in the non-periodic direction. Due to this the symmetry information
    may overlap with real 3D structures unless we include a distinguishing
    label for 2D structures.

    Args:
        space_group: 3D space group number
        wyckoff_sets: Wyckoff sets that map a Wyckoff letter to related
            information
        is_2d: Whether the symmetry information is analyzed from a 2D
            structure. If true, a prefix is added to the string to distinguish
            2D from 3D.

    Returns:
        A string that encodes the symmetry properties of an atomistic
        structure.
    """
    wyckoff_strings = []
    for group in wyckoff_sets:
        element = group.element
        wyckoff_letter = group.wyckoff_letter
        n_atoms = len(group.indices)
        i_string = f'{element} {wyckoff_letter} {n_atoms}'
        wyckoff_strings.append(i_string)
    wyckoff_string = ', '.join(sorted(wyckoff_strings))
    if is_2d:
        string = f'2D {space_group} {wyckoff_string}'
    else:
        string = f'{space_group} {wyckoff_string}'

    return string


def get_hill_decomposition(
    atom_labels: np.ndarray, reduced: bool = False
) -> tuple[list[str], list[int]]:
    """
    Given a list of atomic labels, returns the chemical formula using the
    Hill system (https://en.wikipedia.org/wiki/Hill_system) with an exception
    for binary ionic compounds where the cation is always given first.

    Args:
        atom_labels: Atom labels.
        reduced: Whether to divide the number of atoms by the greatest common
            divisor

    Returns:
        An ordered list of chemical symbols and the corresponding counts.
    """
    # Count occurancy of elements
    names = []
    counts = []
    unordered_names, unordered_counts = np.unique(atom_labels, return_counts=True)
    element_count_map = dict(zip(unordered_names, unordered_counts))

    # Apply basic Hill system:
    # 1. Is Carbon part of the system?
    if 'C' in element_count_map:
        names.append('C')
        counts.append(element_count_map['C'])
        del element_count_map['C']

        # 1a. add hydrogren
        if 'H' in element_count_map:
            names.append('H')
            counts.append(element_count_map['H'])
            del element_count_map['H']

    # 2. all remaining elements in alphabetic order
    for element in sorted(element_count_map):
        names.append(element)
        counts.append(element_count_map[element])

    # 3. Binary ionic compounds: cation first, anion second
    # If any of the most electronegative elements is first
    # by alphabetic order, we move it to second
    if len(counts) == 2 and names != ['C', 'H']:
        order = {
            'F': 1,
            'O': 2,
            'N': 3,
            'Cl': 4,
            'Br': 5,
            'C': 6,
            'Se': 7,
            'S': 8,
            'I': 9,
            'As': 10,
            'H': 11,
            'P': 12,
            'Ge': 13,
            'Te': 14,
            'B': 15,
            'Sb': 16,
            'Po': 17,
            'Si': 18,
            'Bi': 19,
        }
        if names[0] in order:
            if names[1] in order:
                if order[names[0]] < order[names[1]]:
                    # For non-metals:
                    # Swap symbols and counts if first element
                    # is more electronegative than the second one,
                    # because the more electronegative element is the anion
                    names[0], names[1] = names[1], names[0]
                    counts[0], counts[1] = counts[1], counts[0]
            else:
                # Swap symbols and counts always if second element
                # is any other element,i.e.,
                # put non-metal last because it is the anion
                names[0], names[1] = names[1], names[0]
                counts[0], counts[1] = counts[1], counts[0]

    # TODO: implement all further exceptions regarding ordering
    #       in chemical formulas:
    #         - ionic compounds (ordering wrt to ionization)
    #         - oxides, acids, hydroxides...

    # Reduce if requested
    if reduced:
        greatest_common_divisor = reduce(math.gcd, counts)
        counts = np.array(counts) / greatest_common_divisor

    return names, counts


def get_formula_string(symbols: Iterable[str], counts: Iterable[int]) -> str:
    """
    Used to form a single formula string from a list of chemical species and
    their counts.

    Args:
        symbols: List of chemical species
        counts: List of chemical species occurrences

    Returns:
        The formula as a string.
    """
    formula = ''
    for symbol, count in zip(symbols, counts):
        if count > 1:
            formula += f'{symbol}{count}'
        else:
            formula += symbol
    return formula


def get_normalized_wyckoff(
    atomic_numbers: np.ndarray, wyckoff_letters: np.ndarray
) -> dict[str, dict[str, int]]:
    """
    Returns a normalized Wyckoff sequence for the given atomic numbers and
    corresponding wyckoff letters. In a normalized sequence the chemical
    species are 'anonymized' by replacing them with upper case alphabets.

    Args:
        atomic_numbers: Array of atomic numbers.
        wyckoff_letters: Array of Wyckoff letters as strings.

    Returns:
        Returns a dictionary that maps each present Wyckoff letter to a
        dictionary. The dictionary contains the number of atoms for each
        species, where the species names have been anonymized in the form
        'X_<index>'.
    """
    # Count the occurrence of each chemical species
    atom_count: dict[int, int] = {}
    for atomic_number in atomic_numbers:
        atom_count[atomic_number] = atom_count.get(atomic_number, 0) + 1

    # Form a dictionary that maps Wyckoff letters to a dictionary with the
    # number of atoms of that Wyckoff letter for each atomic number
    wyc_dict: dict = {}
    for i, wyckoff_letter in enumerate(wyckoff_letters):
        old_val = wyc_dict.get(wyckoff_letter, {})
        atomic_number = atomic_numbers[i]
        old_val[atomic_number] = old_val.get(atomic_number, 0) + 1
        wyc_dict[wyckoff_letter] = old_val
    sorted_wyckoff_letters = list(wyc_dict.keys())
    sorted_wyckoff_letters.sort()

    # Anonymize the atomic species to X_<index>, where the index is calculated
    # by ordering the species.
    def compare_atomic_number(at1, at2):
        def cmpp(a, b):
            return (a < b) - (a > b)

        c = cmpp(atom_count[at1], atom_count[at2])
        if c != 0:
            return c
        for wyckoff_letter in sorted_wyckoff_letters:
            p = wyc_dict[wyckoff_letter]
            c = cmpp(p.get(at1, 0), p.get(at2, 0))
            if c != 0:
                return c
        return 0

    sorted_species = list(atom_count.keys())
    sorted_species.sort(key=functools.cmp_to_key(compare_atomic_number))
    standard_atom_names = {}
    for i, at in enumerate(sorted_species):
        standard_atom_names[at] = f'X_{i}'

    # Rename with anonymized species labels
    standard_wyc: dict = {}
    for wk, ats in wyc_dict.items():
        std_ats = {}
        for at, count in ats.items():
            std_ats[standard_atom_names[at]] = count
        standard_wyc[wk] = std_ats

    # Divide atom counts with greatest common divisor
    if standard_wyc:
        counts = [c for x in standard_wyc.values() for c in x.values()]
        gcd = counts[0]
        for c in counts[1:]:
            gcd = math.gcd(gcd, c)
        if gcd != 1:
            for d in standard_wyc.values():
                for at, c in d.items():
                    d[at] = c // gcd

    return standard_wyc


def search_aflow_prototype(space_group: int, norm_wyckoff: dict) -> dict:
    """
    Searches the AFLOW prototype library for a match for the given space
    group and normalized Wyckoff sequence. The normalized Wyckoff sequence is
    assumed to come from the MatID symmetry routine.

    Currently only contains Part I of the prototype library (M. J. Mehl, D.
    Hicks, C. Toher, O. Levy, R. M. Hanson, G. L. W. Hart, and S. Curtarolo,
    The AFLOW Library of Crystallographic Prototypes: Part 1, Comp. Mat. Sci.
    136, S1-S828 (2017), 10.1016/j.commatsci.2017.01.017)

    Args:
        space_group_number: Space group number
        norm_wyckoff: Normalized Wyckoff occupations

    Returns:
        Dictionary containing the AFLOW prototype information.
    """
    structure_type_info = None
    type_descriptions: Any = aflow_prototypes['prototypes_by_spacegroup'].get(
        space_group, []
    )
    for type_description in type_descriptions:
        current_norm_wyckoffs = type_description.get('normalized_wyckoff_matid')
        if current_norm_wyckoffs and current_norm_wyckoffs == norm_wyckoff:
            structure_type_info = type_description
            break
    return structure_type_info


def get_brillouin_zone(reciprocal_lattice: np.ndarray) -> dict:
    """
    Calculates the Brillouin Zone information from the given reciprocal
    lattice.

    This function uses the crystallographic definition, so there is no factor
    of 2*Pi.

    Args:
        primitive_lattice: The primitive cell as a matrix where rows are the
            cell basis vectors.

    Returns:
        A dictionary containing:
        'vertices': The vertices of the first Brillouin zone
        'faces': The indices of the vertices that make up the faces on the
            first Brillouin zone. The order of these indices matter, because
            only when combined sequentially they form the correct face.
    """
    # Lazily import expensive `scipy`
    from scipy.spatial import Voronoi

    # Create the near lattice points that surround the origin
    b1 = reciprocal_lattice[0, :]
    b2 = reciprocal_lattice[1, :]
    b3 = reciprocal_lattice[2, :]

    list_k_points = []
    for i, j, k in itertools.product([-1, 0, 1], [-1, 0, 1], [-1, 0, 1]):
        list_k_points.append(i * b1 + j * b2 + k * b3)

    # Create the first Brillouin zone by calculating a Voronoi cell starting
    # from the reciprocal cell origin.
    voronoi = Voronoi(list_k_points)
    origin_index = 13

    # Get the vertices. The regions attribute will contain a list of
    # different regions that were found during the Voronoi creation. We want
    # the Voronoi region for the point at the origin.
    point_region = voronoi.point_region[13]
    vertex_indices = voronoi.regions[point_region]
    vertices = voronoi.vertices[vertex_indices].tolist()

    # Create a mapping between the original index and an index in the new list
    index_map = {old_id: new_id for (new_id, old_id) in enumerate(vertex_indices)}

    # The ridges are the faces of a 3D Voronoi cell. Here we search for ridges
    # that are placed between the origin and some other point. These form the
    # BZ faces.
    faces = []
    for key in voronoi.ridge_dict:
        if key[0] == origin_index or key[1] == origin_index:
            ridge_indices = voronoi.ridge_dict[key]
            new_ridge_indices = [index_map[i] for i in ridge_indices]
            faces.append(new_ridge_indices)

    brillouin_zone = {
        'vertices': vertices,
        'faces': faces,
    }
    return brillouin_zone


def get_minimized_structure(atoms: Atoms):
    """
    Reduce cell size to just fit the system in the non-periodic dimensions.

    Args:
        atoms: The structure to minimize

    Returns:
        A new structure where the non-periodic dimension have been minimized.
    """
    min_atoms = atoms.copy()
    pos = atoms.get_scaled_positions(wrap=False)
    cell = atoms.get_cell()
    new_cell = np.array(cell)
    translation = np.zeros(3)
    pbc = atoms.get_pbc()
    for index, periodic in enumerate(pbc):
        if not periodic:
            imin = np.min(pos[:, index])
            imax = np.max(pos[:, index])
            translation -= cell[index, :] * imin
            new_cell[index] = cell[index, :] * (imax - imin)

    min_atoms.translate(translation)
    min_atoms.set_cell(new_cell)

    return min_atoms


def swap_basis(atoms, a, b):
    cell_old = atoms.get_cell()
    pbc_old = atoms.get_pbc()
    cell_new = np.array(cell_old)
    cell_new[a] = cell_old[b]
    cell_new[b] = cell_old[a]
    pbc_new = np.array(pbc_old)
    pbc_new[a] = pbc_old[b]
    pbc_new[b] = pbc_old[a]
    atoms.set_cell(cell_new)
    atoms.set_pbc(pbc_new)


class Formula:
    """Helper class for extracting formulas used by NOMAD."""

    formula_parentheses = re.compile(r'([A-Z][a-z]?)\((\d+)\)')
    symbols = set(ase.data.chemical_symbols[1:])
    placeholder_symbol = 'X'

    def __init__(self, formula: str, unknown: str = 'replace'):
        """
        Args:
            formula: Chemical formula to work on
            unknown: The handling of unknown species. Can be one of:
            - 'replace': Replaces the unknown species with X.
            - 'remove': Removes the unknown species.
            - 'keep': Keeps the labels for the unknown species.

        Raises:
            ValueError if no meaningful formula can be extracted.
        """
        # Try if ASE can directly make sense of the formula
        try:
            self.ase_formula = ASEFormula(formula)
        # Try if can be interpreted as fractional formula
        except Exception:
            try:
                from pymatgen.core import Composition

                self.ase_formula = ASEFormula(
                    Composition(formula).get_integer_formula_and_factor()[0]
                )
            # Try if formula contains parentheses that can be removed
            except Exception:
                self.ase_formula = ASEFormula(self._remove_parentheses(formula))

        count = self.ase_formula.count()
        if unknown == 'remove':
            for key in list(count.keys()):
                if key not in self.symbols:
                    del count[key]
        elif unknown == 'replace':
            for key, value in list(count.items()):
                if key not in self.symbols and key != self.placeholder_symbol:
                    if self.placeholder_symbol not in count:
                        count[self.placeholder_symbol] = value
                    else:
                        count[self.placeholder_symbol] += value
                    del count[key]
        elif unknown == 'keep':
            pass
        else:
            raise ValueError('Invalid option for the argument "unknown"')
        self._count = count
        if len(count) == 0:
            raise ValueError(
                f'Could not extract any species from the formula "{formula}"'
            )
        self._original_formula = formula

    def count(self) -> dict[str, int]:
        """Return dictionary that maps chemical symbol to number of atoms."""
        return self._count.copy()

    def format(self, fmt: str) -> str:
        """
        Args:
            fmt: The used format. Available options:
            - hill: Formula in Hill notation (see chemical_formula_hill)
            - iupac: The IUPAC formula (see chemical_formula_iupac)
            - reduced: Reduced formula (see chemical_formula_reduced)
            - descriptive: Descriptive formula (see chemical_formula_descriptive)
            - anonymous: Anonymized formula (see chemical_formula_anonymous)
            - original: The originally supplied formula format
        """
        if fmt == 'hill':
            return self._formula_hill()
        elif fmt == 'iupac':
            return self._formula_iupac()
        elif fmt == 'reduced':
            return self._formula_reduced()
        elif fmt == 'descriptive':
            return self._formula_descriptive()
        elif fmt == 'anonymous':
            return self._formula_anonymous()
        elif fmt == 'original':
            return self._original_formula
        else:
            raise ValueError(f'Invalid format option "{fmt}"')

    def elements(self) -> list[str]:
        """Returns the list of chemical elements present in the formula."""
        return sorted(self.count().keys())

    def atomic_fractions(self) -> dict[str, float]:
        """
        Returns dictionary that maps chemical symbol to atomic fraction.

        Returns:
            Dict[str, float]: Dictionary with chemical symbol as key and the atomic
            fraction as value.
        """
        count = self.count()
        total_count = sum(count.values())
        atomic_fractions = {key: value / total_count for key, value in count.items()}
        return atomic_fractions

    def mass_fractions(self) -> dict[str, float | None]:
        """
        Returns a dictionary that maps chemical symbol to mass fraction.

        Returns:
            Dict[str, float]: Dictionary with chemical symbol as key and the mass
            fraction as value.
        """
        count = self.count()
        if 'X' in count:  # Cannot determine mass fractions with unknown elements
            return {element: None for element in count}
        masses = {
            element: atomic_masses[ase.data.atomic_numbers[element]] * count
            for element, count in count.items()
        }
        total_mass = sum(masses.values())
        mass_fractions = {
            element: mass / total_mass for element, mass in masses.items()
        }
        return mass_fractions

    def elemental_composition(self) -> list[ElementalComposition]:
        """
        Returns the atomic and mass fractions as a list of
        `ElementalComposition` objects. Any unknown elements are ignored.

        Returns:
            List[ElementalComposition]: The list of `ElementalComposition` objects.
        """
        from nomad.datamodel.results import ElementalComposition

        atomic_fractions = self.atomic_fractions()
        mass_fractions = self.mass_fractions()
        elemental_composition = [
            ElementalComposition(
                element=element,
                atomic_fraction=atomic_fraction,
                mass_fraction=mass_fractions[element],
                mass=atomic_masses[ase.data.atomic_numbers[element]],
            )
            for element, atomic_fraction in atomic_fractions.items()
            if element in valid_elements
        ]
        return elemental_composition

    def populate(
        self,
        section: Material | System,
        descriptive_format: str | None = 'original',
        overwrite: bool = False,
    ) -> None:
        """
        Populates the supplied section with the list of elements and elemental
        compositions as well as the formulae in the formats: hill, reduced, iupac,
        anonymous and descriptive.
        The descriptive formula defaults to the originally supplied formula but can be
        changed to any other format by supplying the `descriptive_format` argument as:
        'hill', 'reduced', 'iupac', 'anonymous' or 'descriptive'. If descriptive_format is
        None, no descriptive formula will be added to the section.

        Args:
            section: The section to be populated with elements and formulae.
            descriptive_format: The format used for the
                descriptive formula (see `format` method for details). If None,
                the materials descriptive formula is not set. Defaults to
                'original'.
            overwrite: Allow the populated metadata to be overwritten.

        Raises:
            ValueError if any of the populated metainfo already exist and would be
            overwritten.
        """
        if not overwrite:
            for quantity in [
                'elements',
                'elemental_composition',
                'chemical_formula_hill',
                'chemical_formula_reduced',
                'chemical_formula_iupac',
                'chemical_formula_anonymous',
                'chemical_formula_descriptive',
            ]:
                if getattr(section, quantity):
                    raise ValueError(
                        'Could not populate compositional data '
                        f'as "{quantity}" is already defined.'
                    )

        section.elements = self.elements()
        section.elemental_composition = self.elemental_composition()
        section.chemical_formula_hill = self.format('hill')
        section.chemical_formula_reduced = self.format('reduced')
        section.chemical_formula_iupac = self.format('iupac')
        section.chemical_formula_anonymous = self.format('anonymous')
        if descriptive_format:
            section.chemical_formula_descriptive = self.format(descriptive_format)

    def _remove_parentheses(self, formula: str) -> str:
        """Used to remove parentheses from a formula. E.g. C(2)O(1) becomes C2O1
        Args:
            formula: the original formula

        Returns:
            The formula without parentheses.
        """
        matches = list(re.finditer(self.formula_parentheses, formula))
        if matches:
            n_matched_chars = sum([len(match[0]) for match in matches])
            n_formula = len(formula.strip())
            if n_matched_chars == n_formula:
                formula = ''.join([f'{match[1]}{match[2]}' for match in matches])
        return formula

    def _formula_hill(self) -> str:
        """Returns the Hill formula."""
        count = self.count()
        count_hill = {}
        if 'C' in count:
            count_hill['C'] = count.pop('C')
            if 'H' in count:
                count_hill['H'] = count.pop('H')
        for symb, n in sorted(count.items()):
            count_hill[symb] = n

        return self._dict2str(count_hill)

    def _formula_iupac(self) -> str:
        """Returns the IUPAC formula."""
        from pymatgen.core.periodic_table import get_el_sp

        count = self.count()
        counts = count.values()
        symbols = list(count.keys())
        gcd = reduce(math.gcd, counts)
        symbols_sorted = sorted(
            symbols,
            key=lambda x: (
                float('inf')
                if x == self.placeholder_symbol
                else get_el_sp(x).iupac_ordering
            ),
        )
        count_iupac = {symbol: int(count[symbol] / gcd) for symbol in symbols_sorted}

        return self._dict2str(count_iupac)

    def _formula_reduced(self) -> str:
        """Returns the reduced formula."""
        count = self.count()
        counts = count.values()
        gcd = reduce(math.gcd, counts)
        count_reduced = {key: int(value / gcd) for key, value in sorted(count.items())}

        return self._dict2str(count_reduced)

    def _formula_descriptive(self) -> str:
        """Returns the descriptive formula. This is a formula ordered using IUPAC for
        inorganic materials and Hill for organic compounds.

        The check is done if 'C' is present in self.count(), except for exceptions of
        carbon-based materials that are still inorganic. Furthermore, some formulas show a
        special ordering which is also considered.

        Ref:
            https://en.wikipedia.org/wiki/List_of_inorganic_compounds
        """
        # List of carbon inorganic compounds in IUPAC notation (ordered in alphabetical order)
        carbon_inorganic_iupac = [
            'Al4C3',
            'B4C',
            'BaCO3',
            'BeCO3',
            'CNH5O3',
            'CaC2',
            'CaCO3',
            'Ce2C3O9',
            'Cf2C3O9',
            'CoCO3',
            'Cs2CO3',
            'CsHCO3',
            'CuCO3',
            'Es2C3O9',
            'Fe2C9O9',
            'Fe3C12O12',
            'FeC5O5',
            'Fr2CO3',
            'Gd2C3O9',
            'Ho2C3O9',
            'La2C3O9',
            'Li2CO3',
            'MgC2',
            'MgCO3',
            'MoC',
            'MoC6O6',
            'Na2C2',
            'Na2CO3',
            'Na2CO4',
            'NiCO3',
            'PbC3O3',
            'Pm2C3O9',
            'Pr2C3O9',
            'RaCO3',
            'Rh6C16O16',
            'SiC',
            'Sm2C3O9',
            'SrC2TaC',
            'SrCO3',
            'SrCO3',
            'Tb2C3O9',
            'Ti3SiC2',
            'TiC',
            'Tl2CO3',
            'VC',
            'WC',
            'WC6O6',
            'Yb2C3O9HfC',
            'ZnCO3',
            'ZrC',
        ]
        # Mapping of special formula ordering different than Hill or IUPAC
        hill_to_special_map = {
            'KCHO3': 'KHCO3',
            'NaCHO3': 'NaHCO3',
            'SrC2H2O6': 'SrH2C2O6',
        }
        formula_iupac = self._formula_iupac()
        formula_descriptive = formula_iupac
        if 'C' in self.count().keys():
            if formula_iupac not in carbon_inorganic_iupac:
                formula_descriptive = self._formula_hill()
            if formula_iupac in hill_to_special_map.keys():
                formula_descriptive = hill_to_special_map[formula_iupac]
        return formula_descriptive

    def _formula_anonymous(self) -> str:
        """Returns the anonymous formula."""
        count = self.count()
        counts = count.values()
        gcd = reduce(math.gcd, counts)
        count_anonymous = {
            ascii_uppercase[index]: int(value / gcd)
            for index, value in enumerate(reversed(sorted(count.values())))
        }
        return self._dict2str(count_anonymous)

    def _dict2str(self, dct: dict[str, int]) -> str:
        """Convert symbol-to-count dict to a string. Omits the chemical
        proportion number 1.

        Args:
            dct: The dictionary of symbol-to-count items to convert.

        Returns:
            Chemical formula as a string.
        """
        return ''.join(symb + (str(n) if n > 1 else '') for symb, n in dct.items())
