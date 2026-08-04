# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Asset Identifier - Core asset identification and grouping using UnionFind algorithm.

This module is responsible for:
- Asset ID generation (UUID-based)
- Component grouping using UnionFind algorithm
- Asset identity management
"""

import logging
import uuid
import re
from typing import Dict, Set, Optional

from src.modules.asset_generation.domain.asset_validator import AssetValidator

logger = logging.getLogger(__name__)

# Pre-compiled patterns for component type detection
_UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
_MAC_PATTERN = re.compile(r'^[0-9a-f]{2}(:[0-9a-f]{2}){5}$', re.IGNORECASE)

# Fixed namespace for deriving deterministic asset ids from a component set (#802).
# Stable across processes/runs so the same component set always yields the same id.
_ASSET_ID_NAMESPACE = uuid.UUID('a55e7a55-0000-4000-8000-a55e7a55e7a5')


class UnionFind:
    """
    UnionFind (Disjoint Set Union) data structure for grouping asset components.

    This algorithm efficiently groups components (hostname, IP, MAC, SN) into assets.
    It maintains a parent map where each component points to its representative (asset ID).
    """

    def __init__(self):
        """Initialize UnionFind with empty parent mapping."""
        self.parent: Dict[str, str] = {}
        self.rank: Dict[str, int] = {}  # Track tree depth for union-by-rank

    def get_new_raw_id(self) -> str:
        """
        Generate a new unique UUID-based asset ID.

        Returns:
            UUID string as asset ID
        """
        new_id_str = str(uuid.uuid4())
        while new_id_str in self.parent:
            new_id_str = str(uuid.uuid4())
        self.parent[new_id_str] = new_id_str
        self.rank[new_id_str] = 0
        logger.debug(f"Generated new asset ID: {new_id_str}")
        return new_id_str

    def get_deterministic_raw_id(self, components) -> str:
        """Mint an asset id derived deterministically from a component set.

        The same set of components yields the same id across runs, so a group
        re-discovered from scratch every run — e.g. one whose mappings the
        oversized-group nuke wipes each load — resolves to the SAME id instead
        of a fresh random UUID. A random id churned a new asset every run and,
        under the grace period, accumulated as orphan duplicates (#802).

        Registers the id as self-referential, like get_new_raw_id.
        """
        key = '|'.join(sorted({str(c) for c in components}))
        new_id_str = str(uuid.uuid5(_ASSET_ID_NAMESPACE, key))
        # setdefault (not unconditional assignment) so we never clobber an
        # existing entry on the astronomically-unlikely event that the derived
        # uuid5 already names a loaded component/asset id — mirrors the
        # no-clobber discipline of get_new_raw_id's collision loop.
        self.parent.setdefault(new_id_str, new_id_str)
        self.rank.setdefault(new_id_str, 0)
        return new_id_str

    def find(self, i: str, allow_missing: bool = False) -> Optional[str]:
        """
        Find the root (representative) of the element with path compression.

        Args:
            i: Component identifier
            allow_missing: If True, return None for missing components instead of raising error

        Returns:
            Root asset ID for this component, or None if not found and allow_missing=True

        Raises:
            KeyError: If component not in parent map and allow_missing=False
        """
        i_str = str(i)
        if i_str not in self.parent:
            if allow_missing:
                # Component not in parent map - return None instead of raising error
                return None
            # BUGFIX: Do NOT auto-initialize components in find()
            # This was causing non-deterministic asset IDs when transient components (IPs)
            # got random UUIDs that could lexicographically sort before existing asset IDs
            # Components should be explicitly added via group_components() or other methods
            raise KeyError(f"Component '{i_str}' not in UnionFind parent map")
        if self.parent[i_str] != i_str:
            self.parent[i_str] = self.find(self.parent[i_str])
        return self.parent[i_str]

    def union(self, i: str, j: str) -> bool:
        """
        Union two components into the same asset group.

        Args:
            i: First component identifier
            j: Second component identifier

        Returns:
            True if union was performed, False if already in same group
        """
        try:
            root_i = self.find(str(i))
        except KeyError as e:
            logger.error(f"Union failed: component '{i}' not in parent map. Parent map has {len(self.parent)} entries.")
            raise
        try:
            root_j = self.find(str(j))
        except KeyError as e:
            logger.error(f"Union failed: component '{j}' not in parent map. Parent map has {len(self.parent)} entries.")
            raise

        if root_i == root_j:
            return False

        # Union-by-rank: attach smaller tree under root of larger tree
        rank_i = self.rank.get(root_i, 0)
        rank_j = self.rank.get(root_j, 0)

        if rank_i < rank_j:
            self.parent[root_i] = root_j
            logger.debug(f"Merged {root_i} into {root_j} (rank)")
        elif rank_i > rank_j:
            self.parent[root_j] = root_i
            logger.debug(f"Merged {root_j} into {root_i} (rank)")
        else:
            # Same rank: use lexicographic as tiebreaker for determinism
            if root_i < root_j:
                self.parent[root_j] = root_i
                self.rank[root_i] = rank_i + 1
            else:
                self.parent[root_i] = root_j
                self.rank[root_j] = rank_j + 1
            logger.debug(f"Merged (equal rank, tiebreaker)")
        return True

    def get_all_groups(self) -> Dict[str, Set[str]]:
        """Get all asset groups with their components."""
        groups: Dict[str, Set[str]] = {}
        for component in self.parent.keys():
            root = self.find(component)
            if root not in groups:
                groups[root] = set()
            groups[root].add(component)
        return groups

    def get_components_for_asset(self, asset_id: str) -> Set[str]:
        """Get all components belonging to an asset."""
        components = set()
        for component, parent in self.parent.items():
            if self.find(component) == asset_id:
                components.add(component)
        return components

    def load_from_mapping(self, mapping: Dict[str, str]) -> None:
        """
        Load existing asset ID mappings into UnionFind.

        CRITICAL: Converts all UUIDs to strings and ensures every asset ID points to itself.
        Based on old Splunk implementation that handled this correctly.
        """
        # BUGFIX: Convert UUID objects to strings (database returns UUID objects)
        # Old Splunk code: existing_id_str = str(item.get('id', '')).strip()
        string_mapping = {}
        for component, asset_id in mapping.items():
            component_str = str(component).strip().lower() if component else ""
            asset_id_str = str(asset_id).strip() if asset_id else ""
            if component_str and asset_id_str:
                string_mapping[component_str] = asset_id_str

        self.parent = string_mapping.copy()
        self.rank = {}  # Reset ranks when loading new mapping

        # CRITICAL: Ensure ALL asset IDs (values) point to themselves as keys
        # This matches old Splunk implementation: if existing_id_str not in uf.parent: uf.parent[existing_id_str] = existing_id_str
        # This prevents broken chains when components point to asset IDs that aren't components themselves
        unique_asset_ids = set(string_mapping.values())
        added_asset_ids = 0
        for asset_id in unique_asset_ids:
            if asset_id not in self.parent:
                self.parent[asset_id] = asset_id
                self.rank[asset_id] = 0
                added_asset_ids += 1

        logger.info(f"Loaded {len(string_mapping)} mappings into UnionFind ({len(unique_asset_ids)} unique assets)")
        if added_asset_ids > 0:
            logger.info(f"Added {added_asset_ids} asset IDs as self-referential entries for UnionFind integrity")

    def get_mapping(self) -> Dict[str, str]:
        """Get the complete component -> asset_id mapping."""
        for component in list(self.parent.keys()):
            self.find(component)
        return self.parent.copy()


class AssetIdentifier:
    """
    High-level asset identification service.

    Orchestrates UnionFind algorithm and provides business logic for asset grouping.
    """

    def __init__(self):
        """Initialize asset identifier with new UnionFind instance."""
        self.union_find = UnionFind()
        logger.info("AssetIdentifier initialized")

    @staticmethod
    def normalize_component(component: str) -> str:
        """Normalize a component identifier to lowercase for case-insensitive matching."""
        if not component or not isinstance(component, str):
            return component
        return component.lower()

    @staticmethod
    def _is_uuid_format(value: str) -> bool:
        """
        Check if a string matches UUID format (8-4-4-4-12 hex pattern).

        This is used to filter out asset IDs from component mappings.
        Asset IDs are UUIDs used internally by UnionFind but should not
        be persisted as components in the database.

        Args:
            value: String to check

        Returns:
            True if value matches UUID format, False otherwise
        """
        if not value or not isinstance(value, str):
            return False
        # UUID format: 8-4-4-4-12 hexadecimal digits
        uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        return bool(re.match(uuid_pattern, value.lower()))

    def create_new_asset_id(self) -> str:
        """Create a new unique asset ID."""
        return self.union_find.get_new_raw_id()

    def group_components(self, components: list[str]) -> str:
        """
        Group multiple components into a single asset.

        Args:
            components: List of component identifiers to group

        Returns:
            The asset ID representing this group
        """
        if not components:
            raise ValueError("Cannot group empty list of components")

        normalized_components = [self.normalize_component(c) for c in components]

        # Find all existing asset IDs for these components
        existing_asset_ids = []
        asset_id_source = {}  # Maps asset_id -> first component that introduced it
        components_with_ids = []
        components_without_ids = []

        for component in normalized_components:
            if component in self.union_find.parent:
                asset_id = self.union_find.find(component)
                if asset_id not in existing_asset_ids:
                    existing_asset_ids.append(asset_id)
                    asset_id_source[asset_id] = component
                components_with_ids.append(component)
            else:
                components_without_ids.append(component)

        # Determine the asset ID to use
        if existing_asset_ids:
            primary_asset_id = existing_asset_ids[0]
            # Merge multiple existing asset IDs if found
            for other_id in existing_asset_ids[1:]:
                bridging_component = asset_id_source[other_id]
                if logger.isEnabledFor(logging.WARNING):
                    primary_component = asset_id_source[primary_asset_id]
                    hostnames, macs, sns = [], [], []
                    for c in normalized_components:
                        if c.startswith('sn:'):
                            sns.append(c)
                        elif ':' in c and len(c) == 17:
                            macs.append(c)
                        elif '.' not in c:
                            hostnames.append(c)
                    logger.warning(
                        "MERGE_BRIDGE: Component '%s' bridges asset %s (via '%s') "
                        "with asset %s — merging. Record components: "
                        "hostnames=%s, macs=%s, sns=%s",
                        bridging_component, primary_asset_id, primary_component,
                        other_id, hostnames, macs, sns,
                    )
                self.union_find.union(primary_asset_id, other_id)
            final_asset_id = self.union_find.find(primary_asset_id)
        else:
            # No existing mapping found — mint a NEW asset id derived
            # deterministically from this record's components (#802). A record
            # re-discovered from scratch every run (e.g. one with >15 FQDN
            # components that trips the oversized-group nuke) then resolves to
            # the SAME id each run instead of a fresh random UUID, which churned
            # a new asset every run and accumulated orphan duplicates under the
            # grace period. get_deterministic_raw_id registers it self-referentially.
            final_asset_id = self.union_find.get_deterministic_raw_id(normalized_components)

        # Map ALL components to this asset ID
        for component in components_without_ids:
            self.union_find.parent[component] = final_asset_id

        for component in components_with_ids:
            self.union_find.union(component, final_asset_id)

        return self.union_find.find(final_asset_id)

    def get_asset_id_for_component(self, component: str) -> Optional[str]:
        """Get the asset ID for a component."""
        normalized = self.normalize_component(component)
        if normalized not in self.union_find.parent:
            return None
        return self.union_find.find(normalized)

    def get_or_create_asset_id(self, component: str) -> str:
        """Get existing asset ID for component or create new one."""
        normalized = self.normalize_component(component)
        existing_id = self.get_asset_id_for_component(normalized)
        if existing_id:
            return existing_id
        new_id = self.create_new_asset_id()
        self.union_find.parent[normalized] = new_id
        return new_id

    def merge_assets(self, component1: str, component2: str) -> str:
        """Merge two assets by merging their components."""
        normalized1 = self.normalize_component(component1)
        normalized2 = self.normalize_component(component2)
        self.union_find.union(normalized1, normalized2)
        return self.union_find.find(normalized1)

    # Groups with more distinct hostnames than this are considered over-merged
    # and removed during mapping load, forcing re-discovery from current data.
    MAX_HOSTNAMES_PER_GROUP = 15

    # MACs in groups with more hostnames than this are stripped during mapping
    # load to break docking-station / shared-MAC bridges.  Must match the
    # MAC_HOSTNAME_THRESHOLD used in AssetValidator._build_exclusion_sets.
    MAC_HOSTNAME_THRESHOLD = 2

    @staticmethod
    def _is_mac_format(value: str) -> bool:
        """Check if value looks like a MAC address (xx:xx:xx:xx:xx:xx)."""
        return bool(_MAC_PATTERN.match(value))

    @classmethod
    def _is_hostname_component(cls, c: str) -> bool:
        """True if a mapping component represents a hostname (not SN, MAC, or UUID)."""
        return (
            not c.startswith('sn:')
            and not cls._is_mac_format(c)
            and not _UUID_PATTERN.match(c)
        )

    def load_existing_mappings(self, mappings: Dict[str, str]) -> None:
        """Load existing component->asset mappings with group size safety valve."""
        normalized_mappings = {
            self.normalize_component(comp): asset_id
            for comp, asset_id in mappings.items()
        }
        self.union_find.load_from_mapping(normalized_mappings)

        # Remove any IP components from existing mappings — IPs are never
        # reliable merge identifiers and can cause transitive over-merging.
        ip_removed = 0
        ip_keys = [k for k in self.union_find.parent if AssetValidator.is_valid_ip_address(k)]
        for k in ip_keys:
            self.union_find.parent.pop(k, None)
            self.union_find.rank.pop(k, None)
            ip_removed += 1
        if ip_removed > 0:
            logger.info(f"Mapping hygiene: removed {ip_removed} IP components from existing mappings")

        # Single pass over all groups for two hygiene operations:
        #  1. Nuke entire groups with too many hostnames (>15) — historically
        #     over-merged groups that can't be salvaged.
        #  2. Strip MAC components from groups with >2 hostnames — docking
        #     station / shared MACs that create transitive merge chains.
        #     Removing the MAC breaks the bridge; the next run will assign
        #     the now-disconnected hostnames to separate asset IDs.
        groups = self.union_find.get_all_groups()
        nuked_groups = 0
        nuked_components = 0
        mac_removed = 0
        for representative, components in groups.items():
            hostname_count = sum(
                1 for c in components if self._is_hostname_component(c)
            )
            if hostname_count > self.MAX_HOSTNAMES_PER_GROUP:
                for c in components:
                    self.union_find.parent.pop(c, None)
                    self.union_find.rank.pop(c, None)
                nuked_groups += 1
                nuked_components += len(components)
                logger.warning(
                    f"Mapping hygiene: nuked oversized group {representative} "
                    f"({hostname_count} hostnames, {len(components)} total components)"
                )
            elif hostname_count > self.MAC_HOSTNAME_THRESHOLD:
                for mac in (c for c in components if self._is_mac_format(c)):
                    self.union_find.parent.pop(mac, None)
                    self.union_find.rank.pop(mac, None)
                    mac_removed += 1

        if nuked_groups > 0:
            logger.info(
                f"Mapping hygiene: nuked {nuked_groups} oversized groups "
                f"({nuked_components} total components removed)"
            )
        if mac_removed > 0:
            logger.info(
                f"Mapping hygiene: removed {mac_removed} MAC components from "
                f"groups with >{self.MAC_HOSTNAME_THRESHOLD} hostnames"
            )

        remaining = len(self.union_find.parent)
        logger.info(f"Loaded {remaining} existing asset ID mappings (normalized, post-hygiene)")

    def get_all_mappings(self) -> Dict[str, str]:
        """
        Get all component -> asset_id mappings, excluding transient and generated components.

        Excluded from persistent mappings:
        - IP addresses: Can be reassigned (DHCP, device lifecycle)
        - Virtual MACs: Can be cloned (VM cloning, containers, cloud instances)
        - Self-referential UUIDs: Generated asset IDs where component == asset_id

        Included in mappings:
        - Hostnames (including UUID-formatted hostnames from raw data)
        - Physical MAC addresses
        - Serial numbers

        NOTE: Self-referential UUID entries (component == asset_id) are important for UnionFind
        operations during the run, but should NOT be persisted. UUID values that come from
        actual raw data (like UUID-formatted hostnames) ARE valid identifying components and
        will be preserved.

        Returns:
            Dictionary of component -> asset_id (real identifying components)
        """
        all_mappings = self.union_find.get_mapping()

        # Filter out IPs, virtual MACs, and UUID components
        filtered_mappings = {}
        ip_count = 0
        virtual_mac_count = 0
        uuid_count = 0

        for component, asset_id in all_mappings.items():
            # Skip IP addresses
            if AssetValidator.is_valid_ip_address(component):
                ip_count += 1
                continue

            # Skip virtual MACs
            is_virtual, vendor = AssetValidator.is_virtual_mac(component)
            if is_virtual:
                virtual_mac_count += 1
                logger.debug(f"Filtered virtual MAC from mappings: {component} ({vendor})")
                continue

            # Skip self-referential UUID entries (component == asset_id, both are UUIDs)
            # These are generated asset IDs from UnionFind, not real identifying components
            # BUT: Do NOT filter UUIDs that are actual hostnames from raw data!
            if _UUID_PATTERN.match(component) and component == asset_id:
                uuid_count += 1
                continue

            # Keep this mapping (real component: hostname, MAC, or SN)
            filtered_mappings[component] = asset_id

        # Log filtering statistics
        total_filtered = ip_count + virtual_mac_count + uuid_count
        if total_filtered > 0:
            logger.info(
                f"Filtered {total_filtered} components from mappings: "
                f"{ip_count} IPs, {virtual_mac_count} virtual MACs, {uuid_count} self-referential UUIDs"
            )

        return filtered_mappings

    def get_asset_groups(self) -> Dict[str, Set[str]]:
        """Get all assets with their components."""
        return self.union_find.get_all_groups()

    def get_stats(self) -> Dict[str, any]:
        """Get statistics about current state."""
        groups = self.get_asset_groups()
        return {
            'total_components': len(self.union_find.parent),
            'total_assets': len(groups),
            'avg_components_per_asset':
                len(self.union_find.parent) / len(groups) if groups else 0
        }
