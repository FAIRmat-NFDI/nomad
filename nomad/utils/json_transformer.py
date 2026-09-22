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

import re
from copy import deepcopy
from typing import Any

import jmespath

from nomad.datamodel.metainfo.annotations import Condition, Rule, Rules


class Transformer:
    def __init__(
        self,
        mapping_dict: dict[str, Rules]
        | Rules
        | Rule
        | dict[str, Any]
        | list[Any]
        | None = None,
        *,
        source: str | None = None,
        target: str | None = None,
        default_value: Any = None,
        conditions: list[Condition] | None = None,
        use_rule: str | None = None,
    ):
        if target is not None:
            rule = Rule(
                source=source,
                target=target,
                default_value=default_value,
                conditions=conditions,
                use_rule=use_rule,
            )
            self.mapping_dict = {'default': Rules(name='default', rules={'rule': rule})}
        elif mapping_dict is None:
            self.mapping_dict = {}
        elif isinstance(mapping_dict, Rules):
            name = mapping_dict.name or 'default'
            self.mapping_dict = {name: mapping_dict}
        elif isinstance(mapping_dict, Rule):
            self.mapping_dict = {
                'default': Rules(name='default', rules={'rule': mapping_dict})
            }
        elif isinstance(mapping_dict, dict):
            if mapping_dict and all(
                isinstance(v, Rules) for v in mapping_dict.values()
            ):
                self.mapping_dict = mapping_dict
            elif 'target' in mapping_dict:
                rule = Rule(**mapping_dict)
                self.mapping_dict = {
                    'default': Rules(name='default', rules={'rule': rule})
                }
            elif mapping_dict and all(
                isinstance(v, (Rule, dict)) and (isinstance(v, Rule) or 'target' in v)
                for v in mapping_dict.values()
            ):
                rules_dict: dict[str, Rule] = {}
                for k, v in mapping_dict.items():
                    if isinstance(v, Rule):
                        rules_dict[k] = v
                    elif isinstance(v, dict):
                        rules_dict[k] = Rule(**v)
                self.mapping_dict = {'default': Rules(name='default', rules=rules_dict)}
            else:
                self.mapping_dict = mapping_dict
        elif isinstance(mapping_dict, list):
            rules_dict = {}
            for i, r in enumerate(mapping_dict):
                if isinstance(r, Rule):
                    rules_dict[f'rule_{i}'] = r
                elif isinstance(r, dict):
                    rules_dict[f'rule_{i}'] = Rule(**r)
            self.mapping_dict = {'default': Rules(name='default', rules=rules_dict)}
        else:
            self.mapping_dict = mapping_dict

    @classmethod
    def map(
        cls,
        data: Any,
        source: str | None = None,
        target: str | None = None,
        default_value: Any = None,
        conditions: list[Condition] | None = None,
        use_rule: str | None = None,
        rule: Rule | dict[str, Any] | None = None,
        rules: Rules | dict[str, Any] | list[Any] | None = None,
        target_data: Any = None,
        inplace: bool = False,
        delete_sources: bool = False,
    ) -> Any:
        """
        Convenience method to transform data with minimal arguments in a single call.

        Examples:
            Transformer.map(data, source='a.b', target='c.d')
            Transformer.map(data, target='sub_systems[n].m_def', default_value='Element', inplace=True)
            Transformer.map(data, rule={'source': 'a', 'target': 'b'})
        """
        transformer = cls(
            mapping_dict=rules or rule,
            source=source,
            target=target,
            default_value=default_value,
            conditions=conditions,
            use_rule=use_rule,
        )
        return transformer.transform(
            data,
            target_data=target_data,
            inplace=inplace,
            delete_sources=delete_sources,
        )

    @staticmethod
    def has_array_notation(rule: Rule) -> bool:
        """
        Checks whether a rule uses array index placeholders in target or source.
        """
        if rule.target:
            _, target_indices = Transformer.get_path_sections(
                rule.target, is_target=True
            )
            if target_indices:
                return True
        if rule.source:
            _, source_indices = Transformer.get_path_sections(rule.source)
            if source_indices:
                return True
        return False

    @staticmethod
    def parse_path(path: str) -> list[str | int]:
        """
        Parses a JMESPath-like path into a list of keys and indices.

        Args:
            path (str): The path string to parse.

        Returns:
            list[Union[str, int]]: A list containing string keys and integer indices.
        """
        if not path:
            return []
        pattern = re.compile(r'([^\[\].]+)|\[(\d+)\]')
        parts = []
        for match in pattern.finditer(path):
            key, index = match.groups()
            if key is not None:
                parts.append(key)
            elif index is not None:
                parts.append(int(index))
        return parts

    @staticmethod
    def apply_condition(
        condition: Condition,
        source: dict[str, Any],
        default_path: str | None = None,
    ) -> bool:
        path = (
            condition.regex_condition.regex_path
            or default_path
            or condition.regex_condition.regex_pattern
        )
        value = jmespath.search(path, source)
        if value is None:
            return False
        value_str = str(value)
        return bool(re.match(condition.regex_condition.regex_pattern, value_str))

    @staticmethod
    def set_value(path: str, value: Any, data: Any) -> None:
        """
        Sets a value in a nested dictionary or list based on the provided path.

        Args:
            path (str): The JMESPath-like path indicating where to set the value.
            value (Any): The value to set.
            data (Any): The target data structure (dict or list).

        Raises:
            TypeError: If there's a mismatch between expected and actual data types.
            IndexError: If a list index is out of bounds.
        """
        parts = Transformer.parse_path(path)
        current = data

        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                if isinstance(part, int):
                    if not isinstance(current, list):
                        raise TypeError(
                            f"Expected list at path '{'.'.join(map(str, parts[:i]))}', "
                            f'but got {type(current).__name__}'
                        )
                    while len(current) <= part:
                        current.append(None)
                    current[part] = value
                else:
                    if not isinstance(current, dict):
                        raise TypeError(
                            f"Expected dict at path '{'.'.join(map(str, parts[:i]))}', "
                            f'but got {type(current).__name__}'
                        )
                    current[part] = value
            else:
                next_part = parts[i + 1]
                if isinstance(part, int):
                    if not isinstance(current, list):
                        raise TypeError(
                            f"Expected list at path '{'.'.join(map(str, parts[:i]))}', "
                            f'but got {type(current).__name__}'
                        )
                    while len(current) <= part:
                        current.append({})
                    if current[part] is None:
                        current[part] = [] if isinstance(next_part, int) else {}
                    current = current[part]
                else:
                    if not isinstance(current, dict):
                        raise TypeError(
                            f"Expected dict at path '{'.'.join(map(str, parts[:i]))}', "
                            f'but got {type(current).__name__}'
                        )
                    if part not in current or current[part] is None:
                        current[part] = [] if isinstance(next_part, int) else {}
                    elif isinstance(next_part, int) and not isinstance(
                        current[part], list
                    ):
                        raise TypeError(
                            f"Expected list at path '{'.'.join(map(str, parts[: i + 1]))}', "
                            f'but got {type(current[part]).__name__}'
                        )
                    elif isinstance(next_part, str) and not isinstance(
                        current[part], dict
                    ):
                        raise TypeError(
                            f"Expected dict at path '{'.'.join(map(str, parts[: i + 1]))}', "
                            f'but got {type(current[part]).__name__}'
                        )
                    current = current[part]

    @staticmethod
    def get_array_regex(path, prefix_only=False):
        """
        Converts a path with array indexes given as [*]/[n] to a regex pattern that matches and captures paths with any index.
        """
        re_pattern = re.escape(path)
        suffix = r'(?:[\.\[].*)?$' if prefix_only else '$'
        re_pattern = (
            '^' + re.sub(r'\\\[((n|\\\*)\d*?)\\\]', r'\[(\\d+)\]', re_pattern) + suffix
        )
        return re.compile(re_pattern)

    @staticmethod
    def get_all_paths(data, current_path=''):
        paths = [current_path] if current_path else []
        if isinstance(data, dict):
            for k, v in data.items():
                new_path = f'{current_path}.{k}' if current_path else k
                paths.extend(Transformer.get_all_paths(v, new_path))
        elif isinstance(data, list):
            for i, v in enumerate(data):
                new_path = f'{current_path}[{i}]'
                paths.extend(Transformer.get_all_paths(v, new_path))
        return paths

    @staticmethod
    def get_new_path(match, path, sections):
        """
        Constructs a new path by replacing the array index placeholders with actual index values from the matched path.
        """
        new_path = ''
        start = 0
        for ngroup, group in enumerate(match.groups()):
            new_path += path[start : sections[ngroup].span()[0]] + f'[{group}]'
            start = sections[ngroup].span()[1]
        new_path += path[start:]
        return new_path

    @staticmethod
    def get_array_match_path(path, sections):
        sections_to_remove = [
            i.group() for i in sections if i.group('filter') or i.group('multi_select')
        ]
        for i in sections_to_remove:
            path = path.replace(i, '')
        return path

    @staticmethod
    def get_path_sections(path, is_target: bool = False):
        if not path:
            return [], []
        pattern_str = (
            (
                r'(?P<index>\[(?:n\d*|\*)\])|'
                r'(?P<filter>\[(?:\?.*?\]|\]))|'
                r'(?P<multi_select>\.?(?:\[(?!\?)[^\]]*\]|\{[^\}]*\}))'
            )
            if is_target
            else (
                r'(?P<index>\[(?:n\d*)\])|'
                r'(?P<filter>\[(?:\*\]|\?.*?\]|\]))|'
                r'(?P<multi_select>\.?(?:\[(?!\?)[^\]]*\]|\{[^\}]*\}))'
            )
        )
        capture_pattern = re.compile(pattern_str)
        sections = [x for x in capture_pattern.finditer(path)]
        index_sections = [i for i in sections if i.group('index')]
        return sections, index_sections

    @staticmethod
    def _resolve_array_rule(data_paths, rule, name):
        """
        Resolves a single rule with array indexes of type [n<number>] and generates new rules for all matching paths in the source data.
        """
        c = 0
        resolved_rules = {}

        source_path = rule.source
        target_path = rule.target

        source_sections, source_index_sections = Transformer.get_path_sections(
            source_path
        )

        target_sections, target_index_sections = Transformer.get_path_sections(
            target_path, is_target=True
        )
        if not source_index_sections and not target_index_sections:
            return {name: Rules(name=name, rules={name: rule})}
        if source_path is not None:
            if len(source_index_sections) != len(target_index_sections):
                raise ValueError(
                    'Different number of array index placeholders between source and target'
                )
            if [i.group() for i in source_index_sections] != [
                i.group() for i in target_index_sections
            ]:
                raise ValueError(
                    'Mismatch between source and target array index placeholders'
                )

        if source_path is not None and source_index_sections:
            match_path = source_path
            match_sections = source_sections
            match_index_sections = source_index_sections
        else:
            match_path = target_path
            match_sections = target_sections
            match_index_sections = target_index_sections

        last_index_span_end = match_index_sections[-1].span()[1]
        prefix_path = match_path[:last_index_span_end]

        array_match_path = Transformer.get_array_match_path(prefix_path, match_sections)
        re_pattern = Transformer.get_array_regex(array_match_path, prefix_only=True)

        seen_indices = set()
        for i in data_paths:
            match = re_pattern.match(i)
            if match:
                groups = match.groups()
                if groups in seen_indices:
                    continue
                seen_indices.add(groups)

                if source_path is not None:
                    new_source_path = Transformer.get_new_path(
                        match, source_path, source_index_sections
                    )
                else:
                    new_source_path = None

                new_target_path = Transformer.get_new_path(
                    match, target_path, target_index_sections
                )

                new_conditions = None
                if rule.conditions:
                    placeholder_map = {
                        sec.group(): val
                        for sec, val in zip(match_index_sections, groups)
                    }
                    new_conditions = []
                    for cond in rule.conditions:
                        cond_copy = cond.copy(deep=True)
                        regex_path = cond_copy.regex_condition.regex_path
                        if regex_path:
                            _, cond_index_sections = Transformer.get_path_sections(
                                regex_path
                            )
                            if cond_index_sections:
                                new_regex_path = ''
                                start = 0
                                for sec in cond_index_sections:
                                    ph = sec.group()
                                    idx_val = placeholder_map.get(ph)
                                    if (
                                        idx_val is None
                                        and len(match_index_sections) == 1
                                    ):
                                        idx_val = groups[0]
                                    if idx_val is not None:
                                        new_regex_path += (
                                            regex_path[start : sec.span()[0]]
                                            + f'[{idx_val}]'
                                        )
                                        start = sec.span()[1]
                                new_regex_path += regex_path[start:]
                                cond_copy.regex_condition.regex_path = new_regex_path
                        new_conditions.append(cond_copy)

                resolved_rules[f'{name}_resolved_{c}'] = Rule(
                    source=new_source_path,
                    target=new_target_path,
                    conditions=new_conditions,
                    default_value=rule.default_value,
                    use_rule=rule.use_rule,
                )
                c += 1
        return {
            name: Rules(
                name=name,
                rules=resolved_rules,
            )
        }

    @staticmethod
    def resolve_array_rules(data_paths, rules, mapping_name=''):
        """
        Resolves rules with array indexes of type [n<number>] to apply the transformations to all matching paths in the source data.
        """
        resolved_rules = {}
        if isinstance(rules, dict):
            for rule_name, rule in rules.items():
                resolved_rules.update(
                    Transformer.resolve_array_rules(data_paths, rule, rule_name)
                )
        elif isinstance(rules, Rules):
            for rule_name, rule in rules.rules.items():
                resolved_rules.update(
                    Transformer._resolve_array_rule(data_paths, rule, rule_name)
                )
        elif isinstance(rules, Rule):
            return Transformer._resolve_array_rule(data_paths, rules, '')

        if mapping_name:
            r_rules = {
                k: v for n, r in resolved_rules.items() for k, v in r.rules.items()
            }
            resolved_rules = {
                mapping_name: Rules(
                    name=getattr(rules, 'name', rule_name), rules=r_rules
                )
            }

        return resolved_rules

    @staticmethod
    def delete_path(data, path):
        if not path:
            return
        parts = Transformer.parse_path(path)
        current = data
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                try:
                    del current[part]
                except Exception:
                    print(f'{path} is not present in the data, skipping deletion')
            else:
                try:
                    current = current[part]
                except Exception:
                    print(f'{path} is not present in the data, skipping deletion')

    @staticmethod
    def delete_source_paths(data, rules):
        """
        Deletes the source paths present in the rules from the source data.
        """
        if rules is None:
            return data
        if isinstance(rules, dict):
            for rule_name, rule in rules.items():
                data = Transformer.delete_source_paths(data, rule)
        elif isinstance(rules, Rules):
            for rule_name, rule in rules.rules.items():
                if rule.source:
                    try:
                        Transformer.delete_path(data, rule.source)
                    except Exception as e:
                        print(rule.source, e)
        elif isinstance(rules, Rule):
            if rules.source:
                try:
                    Transformer.delete_path(data, rules.source)
                except Exception as e:
                    print(rules.source, e)
        return data

    def resolve_reference(
        self, rule: Rule, all_rules: dict[str, Rules], visited=None
    ) -> Rule:
        """
        Resolves a rule reference specified in the `use_rule` field.

        Args:
            rule (Rule): The current rule that may reference another rule.
            all_rules (dict[str, Rules]): All available rule sets.
            visited (set, optional): Set of visited references to detect circular dependencies.

        Returns:
            Rule: The resolved rule with overridden fields if a reference exists.
        """
        if visited is None:
            visited = set()

        if rule.use_rule and rule.use_rule.startswith('#'):
            if rule.use_rule in visited:
                raise ValueError(
                    f"Circular reference detected for use_rule '{rule.use_rule}'."
                )
            visited.add(rule.use_rule)

            ref_path = rule.use_rule[1:]
            try:
                mapping_name, rule_name = ref_path.split('.', 1)
            except ValueError:
                raise ValueError(
                    f"Invalid use_rule format: '{rule.use_rule}'. Expected format '#mapping.rule_name'."
                )

            if mapping_name not in all_rules:
                raise ValueError(
                    f"Mapping name '{mapping_name}' not found in the mapping dictionary."
                )

            referenced_rules = all_rules[mapping_name].rules
            if rule_name not in referenced_rules:
                raise ValueError(
                    f"Rule name '{rule_name}' not found in mapping '{mapping_name}'."
                )

            referenced_rule = referenced_rules[rule_name]
            rule = rule.override_fields(referenced_rule)
            rule.use_rule = referenced_rule.use_rule

            rule = self.resolve_reference(rule, all_rules, visited)

        return rule

    def transform_dict(
        self,
        rule: Rule,
        source: dict[str, Any],
        target: Any,
        all_rules: dict[str, Rules],
        parent_source_path: str = '',
        parent_target_path: str = '',
        visited=None,
        array_rules: bool = False,
    ) -> Any:
        """
        Transforms the source dictionary into the target based on the provided rule.

        Args:
            rule (Rule): The transformation rule to apply.
            source (dict[str, Any]): The source data.
            target (Any): The target data structure.
            all_rules (dict[str, 'Rules']): All available rule sets.
            parent_source_path (str, optional): The parent source path.
            parent_target_path (str, optional): The parent target path.
            visited (set, optional): Set of visited references to detect circular dependencies.

        Returns:
            Any: The updated target data structure.
        """
        resolved_rule = self.resolve_reference(rule, all_rules, visited)
        is_array_rule = array_rules or Transformer.has_array_notation(resolved_rule)
        if is_array_rule:
            source_data_paths = Transformer.get_all_paths(source)
            target_data_paths = Transformer.get_all_paths(target) if target else []
            data_paths = list(dict.fromkeys(source_data_paths + target_data_paths))
            resolved_array_rules = self._resolve_array_rule(
                data_paths, resolved_rule, 'resolved_array_rule'
            )
            for rule_name, array_rule in resolved_array_rules[
                'resolved_array_rule'
            ].rules.items():
                target = self.transform_dict(
                    array_rule,
                    source,
                    target,
                    all_rules,
                    parent_source_path,
                    parent_target_path,
                    visited,
                    array_rules=False,
                )
            return target

        current_source_path = resolved_rule.source or parent_source_path
        current_target_path = resolved_rule.target or parent_target_path

        conditions_met = True
        if resolved_rule.conditions:
            conditions_met = all(
                self.apply_condition(cond, source, current_source_path)
                for cond in resolved_rule.conditions
            )

        source_value = None
        if current_source_path:
            source_value = jmespath.search(current_source_path, source)

        if conditions_met:
            if source_value is not None:
                self.set_value(current_target_path, source_value, target)
            elif resolved_rule.default_value is not None:
                self.set_value(current_target_path, resolved_rule.default_value, target)
        else:
            if (
                current_source_path is not None
                and resolved_rule.default_value is not None
            ):
                if any(
                    (cond.regex_condition.regex_path or current_source_path)
                    == current_source_path
                    for cond in resolved_rule.conditions
                ):
                    self.set_value(
                        current_target_path, resolved_rule.default_value, target
                    )

        return target

    def dict_to_dict(
        self,
        source: dict[str, Any],
        rules: Rules,
        target: Any = None,
        array_rules: bool = False,
    ) -> Any:
        """
        Applies all rules in a Rules object to transform the source dictionary into the target.

        Args:
            source (dict[str, Any]): The source data.
            rules (Rules): The set of rules to apply.
            target (Optional[Any], optional): The initial target data structure. Defaults to None.

        Returns:
            Any: The transformed target data structure.
        """
        if target is None:
            target = {} if isinstance(source, dict) else []
        for rule_name, rule in rules.rules.items():
            self.transform_dict(
                rule, source, target, self.mapping_dict, array_rules=array_rules
            )
        return target

    def transform(
        self,
        source_data: dict[str, Any],
        mapping_name: str | None = None,
        target_data: Any = None,
        inplace: bool = False,
        array_rules: bool = False,
        delete_sources: bool = False,
        *,
        source: str | None = None,
        target: str | None = None,
        default_value: Any = None,
        conditions: list[Condition] | None = None,
        rule: Rule | dict[str, Any] | None = None,
    ) -> Any:
        """
        Transforms the source data into the target data based on the specified mapping.

        Args:
            source_data (dict[str, Any]): The source JSON data.
            mapping_name (str): The name of the mapping to use. Default is None.
            target_data (Optional[Any], optional): The initial target data structure. Defaults to None.
            inplace (bool, optional): Whether to perform the transformation in place. Defaults to False.
            array_rules (bool, optional): Whether to resolve array rules. Defaults to False.
            delete_sources (bool, optional): Whether to delete source paths after transformation. Defaults to False.
            source (str, optional): Optional source path for direct single-rule transformation.
            target (str, optional): Optional target path for direct single-rule transformation.
            default_value (Any, optional): Optional default value for direct single-rule transformation.
            conditions (list[Condition], optional): Optional conditions for direct single-rule transformation.
            rule (Rule or dict, optional): Optional rule for direct single-rule transformation.

        Raises:
            ValueError: If the specified mapping name does not exist.

        Returns:
            Any: The transformed target data structure.
        """
        if inplace:
            target_data = deepcopy(source_data)

        if target is not None:
            single_rule = Rule(
                source=source,
                target=target,
                default_value=default_value,
                conditions=conditions,
            )
            mapping = Rules(name='default', rules={'rule': single_rule})
        elif rule is not None:
            single_rule = rule if isinstance(rule, Rule) else Rule(**rule)
            mapping = Rules(name='default', rules={'rule': single_rule})
        else:
            if not mapping_name:
                if len(self.mapping_dict) == 1:
                    mapping_name = list(self.mapping_dict.keys())[0]
                elif 'default' in self.mapping_dict:
                    mapping_name = 'default'
                elif isinstance(source_data, dict) and source_data:
                    first_key = list(source_data.keys())[0]
                    if first_key in self.mapping_dict:
                        mapping_name = first_key
                    elif self.mapping_dict:
                        mapping_name = list(self.mapping_dict.keys())[0]

            if not mapping_name or mapping_name not in self.mapping_dict:
                raise ValueError(
                    f"Mapping name '{mapping_name}' not found in the transformation dictionary"
                )
            mapping = self.mapping_dict[mapping_name]

        try:
            if target_data is None and any(
                rule.target.startswith('[') for rule in mapping.rules.values()
            ):
                target_data = []
            transformed_data = self.dict_to_dict(
                source_data, mapping, target_data, array_rules=array_rules
            )
            if delete_sources:
                transformed_data = self.delete_source_paths(transformed_data, mapping)
            return transformed_data
        except Exception as e:
            raise e
