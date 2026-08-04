# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Mapping Repository - Persistence layer for asset ID mappings.

This module is responsible for:
- Loading asset ID mappings from database
- Saving/updating mappings
- Bulk mapping operations
"""

import logging
from typing import Dict
from uuid import UUID

from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.asset_generation.domain.asset_validator import AssetValidator

logger = logging.getLogger(__name__)


class MappingRepository:
    """
    Repository for asset ID mapping persistence operations.

    Provides high-level interface for storing and retrieving component->asset_id mappings.
    """

    def __init__(self, db_adapter: PostgreSQLAdapter):
        """
        Initialize mapping repository.

        Args:
            db_adapter: PostgreSQL database adapter
        """
        self.db = db_adapter
        self._sn_validator = None  # Lazy-initialized
        logger.info("MappingRepository initialized")

    def _is_suspicious_sn(self, sn_value: str) -> bool:
        """
        Check if a serial number value is suspicious using EnhancedSNValidator.

        Lazy-initializes the validator on first call to avoid import overhead
        when SN filtering is not needed.

        Args:
            sn_value: The raw SN string (after stripping the 'sn:' prefix)

        Returns:
            True if the SN is suspicious and should be filtered out
        """
        if self._sn_validator is None:
            from src.modules.asset_generation.domain.sn_validation_enhanced import (
                EnhancedSNValidator,
            )
            # Vendor validation disabled: we only need placeholder/virtual/entropy checks
            # for the mapping filter, not full vendor-format validation
            self._sn_validator = EnhancedSNValidator(enable_vendor_validation=False)
        return self._sn_validator.is_sn_suspicious(sn_value)

    def load_all_mappings(self) -> Dict[str, UUID]:
        """
        Load all asset ID mappings from database.

        Returns:
            Dictionary of component -> asset_id mappings
        """
        logger.info("Loading all asset ID mappings from database...")
        mappings = self.db.get_all_asset_id_mappings()
        logger.info(f"Loaded {len(mappings)} asset ID mappings")
        return mappings

    def load_mappings_for_components(self, components: list) -> Dict[str, UUID]:
        """
        Load asset ID mappings only for specified components.

        This is more efficient than loading all mappings when you know
        which components will be used (e.g., from the asset field after filtering).

        Args:
            components: List of component identifiers to load mappings for

        Returns:
            Dictionary of component -> asset_id mappings (only for specified components)
        """
        if not components:
            logger.info("No components specified, returning empty mappings")
            return {}

        logger.info(f"Loading asset ID mappings for {len(components)} specific components...")
        mappings = self.db.get_asset_id_mappings_for_components(components)
        logger.info(f"Loaded {len(mappings)} asset ID mappings (out of {len(components)} requested)")
        return mappings

    def save_mapping(self, component: str, asset_id: UUID) -> None:
        """
        Save or update a single asset ID mapping.

        Args:
            component: Component identifier
            asset_id: Asset UUID
        """
        self.db.set_asset_id_mapping(component, asset_id)
        logger.debug(f"Saved mapping: {component} -> {asset_id}")

    def save_mappings_bulk(self, mappings: Dict[str, UUID], use_uuid: bool = True) -> int:
        """
        Save multiple asset ID mappings in bulk with comprehensive filtering.

        Excluded from persistent mappings:
        - IP addresses: Can be reassigned (DHCP, device lifecycle)
        - Virtual MACs: Can be cloned (VM cloning, containers, cloud instances)
        - Invalid values: Empty strings, special characters (\\, /, <, >),
          internal whitespace, parentheses with numbers, placeholders

        Args:
            mappings: Dictionary of component -> asset_id
            use_uuid: Whether to convert string IDs to UUIDs

        Returns:
            Number of mappings saved (after filtering)
        """
        logger.info(f"Bulk saving {len(mappings)} asset ID mappings...")

        # Filter out IPs, virtual MACs, suspicious SNs, and invalid component values BEFORE saving
        filtered_mappings = {}
        ip_count = 0
        virtual_mac_count = 0
        suspicious_sn_count = 0
        invalid_value_count = 0

        for component, asset_id in mappings.items():
            # Skip IP addresses
            if AssetValidator.is_valid_ip_address(component):
                ip_count += 1
                continue

            # Skip virtual MACs
            is_virtual, vendor = AssetValidator.is_virtual_mac(component)
            if is_virtual:
                virtual_mac_count += 1
                continue

            # Skip components with invalid values (special chars, whitespace, etc.)
            if not AssetValidator.is_valid_component_value(component):
                invalid_value_count += 1
                continue

            # Skip suspicious serial numbers (sn: prefixed components)
            if component.startswith("sn:"):
                sn_value = component[3:]  # Strip 'sn:' prefix
                if self._is_suspicious_sn(sn_value):
                    suspicious_sn_count += 1
                    continue

            # Keep this mapping
            filtered_mappings[component] = asset_id

        total_filtered = ip_count + virtual_mac_count + suspicious_sn_count + invalid_value_count
        if total_filtered > 0:
            logger.info(
                f"Filtered {total_filtered} components before saving: "
                f"{ip_count} IPs, {virtual_mac_count} virtual MACs, "
                f"{suspicious_sn_count} suspicious SNs, "
                f"{invalid_value_count} invalid values"
            )

        # Convert dict to list of tuples
        mapping_list = list(filtered_mappings.items())

        # Convert string IDs to UUIDs if needed
        if use_uuid:
            import uuid
            namespace = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')
            converted_mappings = []
            for component, asset_id in mapping_list:
                # Convert asset_id to UUID if it's a string
                if isinstance(asset_id, str):
                    # Check if it's already a valid UUID string - if so, preserve it
                    try:
                        # Try to parse it as a UUID
                        uuid.UUID(asset_id)
                        # It's already a valid UUID, keep it as-is
                        converted_mappings.append((component, asset_id))
                    except (ValueError, AttributeError):
                        # Not a valid UUID, generate one using uuid5
                        uuid_id = str(uuid.uuid5(namespace, asset_id))
                        converted_mappings.append((component, uuid_id))
                else:
                    converted_mappings.append((component, asset_id))
            mapping_list = converted_mappings
            logger.info(f"Converted {len(mapping_list)} mappings to UUID format")

        count = self.db.bulk_set_asset_id_mappings(mapping_list)
        logger.info(
            f"Saved {count} asset ID mappings "
            f"(excluded {ip_count} IPs, {virtual_mac_count} virtual MACs, "
            f"{suspicious_sn_count} suspicious SNs, "
            f"{invalid_value_count} invalid values)"
        )

        return count

    def get_mapping(self, component: str) -> UUID:
        """
        Get asset ID for a component.

        Args:
            component: Component identifier

        Returns:
            Asset UUID if found, None otherwise
        """
        return self.db.get_asset_id_mapping(component)

    def get_mapping_count(self) -> int:
        """
        Get total count of asset ID mappings.

        Returns:
            Number of mappings in database
        """
        mappings = self.load_all_mappings()
        return len(mappings)

    def cleanup_expired_mappings(self, expiration_days: int = 30) -> int:
        """
        Delete component mappings that haven't been seen in the specified number of days.

        This should be called at the start of asset generation to ensure stale
        components don't incorrectly link assets.

        Args:
            expiration_days: Number of days of inactivity before expiration (default: 30)

        Returns:
            Number of mappings deleted
        """
        logger.info(f"Cleaning up component mappings inactive for >{expiration_days} days...")
        count = self.db.delete_expired_mappings(expiration_days)

        if count > 0:
            logger.info(
                f"Cleanup complete: Deleted {count} expired mappings. "
                f"This prevents stale components from incorrectly linking assets."
            )
        else:
            logger.info("No expired mappings found - database is clean")

        return count

    def refresh_mappings_for_assets(self, asset_ids) -> int:
        """
        Keep identifier mappings alive for carried-forward assets (#795).

        Returns the number of mapping rows refreshed.
        """
        if not asset_ids:
            return 0
        count = self.db.touch_asset_mappings(asset_ids)
        logger.info(
            "Kept %d mappings alive for %d carried-forward assets",
            count, len(asset_ids),
        )
        return count
