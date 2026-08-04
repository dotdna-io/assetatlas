# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Field Group Resolver

Resolves integration instance names to group names for display-time
deduplication. Stateless — takes a group mapping dict and provides
resolution methods.
"""

from typing import Dict, List, Optional


class FieldGroupResolver:

    def __init__(self, group_mapping: Dict[str, str]):
        self.mapping = group_mapping or {}
        self.group_members: Dict[str, List[str]] = {}
        for instance, group in self.mapping.items():
            self.group_members.setdefault(group, []).append(instance)

    def resolve_source(self, instance_name: str) -> str:
        return self.mapping.get(instance_name, instance_name)

    def deduplicate_sources(self, sources: Optional[List[str]]) -> List[str]:
        if not sources:
            return []
        seen = set()
        result = []
        for source in sources:
            resolved = self.resolve_source(source)
            if resolved not in seen:
                seen.add(resolved)
                result.append(resolved)
        return result

    def group_extended_fields(
        self, fields_by_source: Dict[str, List[str]]
    ) -> Dict[str, List[Dict]]:
        """Group extended fields by integration group membership.

        For each group, fields common to ALL members are unified under the
        group name. Non-common fields stay under their instance name.
        Ungrouped sources pass through unchanged.

        Args:
            fields_by_source: {source_name: [field_name, ...]}

        Returns:
            {display_source: [{"field": str, "member_sources": list, "is_grouped": bool}, ...]}
        """
        if not fields_by_source:
            return {}

        result: Dict[str, List[Dict]] = {}

        # Collect fields per group: {group_name: {instance: set(fields)}}
        group_fields: Dict[str, Dict[str, set]] = {}
        ungrouped: Dict[str, List[str]] = {}

        for source, fields in fields_by_source.items():
            group_name = self.mapping.get(source)
            if group_name:
                group_fields.setdefault(group_name, {})[source] = set(fields)
            else:
                ungrouped[source] = fields

        # Process each group — union all fields across members.
        # Each field lists only the members that actually have it
        # (so COALESCE targets the right JSONB keys).
        for group_name, members_fields in group_fields.items():
            all_fields = set.union(*members_fields.values())
            entries = []
            for f in sorted(all_fields):
                sources_with_field = sorted(
                    inst for inst, fset in members_fields.items() if f in fset
                )
                entries.append({
                    "field": f,
                    "member_sources": sources_with_field,
                    "is_grouped": True,
                })
            if entries:
                result[group_name] = entries

        # Ungrouped sources pass through
        for source, fields in ungrouped.items():
            result[source] = [
                {
                    "field": f,
                    "member_sources": [source],
                    "is_grouped": False,
                }
                for f in sorted(fields)
            ]

        return result
