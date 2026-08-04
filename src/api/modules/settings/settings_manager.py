# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Settings Manager - Simplified

Manages application-level settings with hot-reload capability.
Settings are stored in database and defined by settings_schema.py.

IMPORTANT: This manager handles ONLY application settings (features, UI, etc).
Infrastructure settings (logging, database) should use environment variables.

Features:
- Get/set settings with schema validation
- Hot-reload without server restart (30-second cache)
- Type-safe value parsing
- Audit trail for changes
"""

import json
import logging
import time
from typing import Any, Optional, Dict, Tuple
from psycopg2 import extras

from src.api.modules.settings import settings_schema
from src.api.utils.audit_logger import AuditLogger
from src.api.utils.audit_vocab import AuditAction, AuditResource

logger = logging.getLogger(__name__)


class SettingsManager:
    """Manages application settings with database storage and schema validation."""

    # Cache settings for 30 seconds to avoid DB queries on every request
    CACHE_TTL_SECONDS = 30

    def __init__(self, connection_manager):
        """
        Initialize settings manager.

        Args:
            connection_manager: Database connection manager
        """
        self.connection_manager = connection_manager
        self._cache = {}  # {(category, key): value}
        self._cache_timestamp = 0
        logger.info("SettingsManager initialized (application settings only)")

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid (within TTL)."""
        return (time.time() - self._cache_timestamp) < self.CACHE_TTL_SECONDS

    def _refresh_cache(self):
        """Refresh entire settings cache from database."""
        try:
            with self.connection_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(
                    "SELECT category, key, value, value_type FROM system_settings"
                )
                results = cursor.fetchall()

                # Clear and rebuild cache
                self._cache.clear()
                for row in results:
                    cache_key = (row['category'], row['key'])
                    value = self._parse_value(row['value'], row['value_type'])
                    self._cache[cache_key] = value

                self._cache_timestamp = time.time()
                logger.debug(f"Settings cache refreshed with {len(results)} entries")
        except Exception as e:
            logger.error(f"Error refreshing settings cache: {e}", exc_info=True)
            # Keep using stale cache on error

    def get(self, category: str, key: str, default: Optional[Any] = None) -> Any:
        """
        Get setting value with caching and schema fallback.

        Args:
            category: Setting category (e.g., 'logging', 'asset_generation')
            key: Setting key (e.g., 'pipeline_schedule_enabled')
            default: Override default value (optional)

        Returns:
            Setting value (parsed to correct type)
        """
        # Refresh cache if needed
        if not self._is_cache_valid():
            self._refresh_cache()

        # Check cache first
        cache_key = (category, key)
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Not in cache, check database
        try:
            with self.connection_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                cursor.execute(
                    "SELECT value, value_type FROM system_settings WHERE category = %s AND key = %s",
                    (category, key)
                )
                result = cursor.fetchone()

                if result:
                    value = self._parse_value(result['value'], result['value_type'])
                    # Update cache
                    self._cache[cache_key] = value
                    return value

        except Exception as e:
            logger.error(f"Error getting setting {category}.{key}: {e}", exc_info=True)

        # Not in database, return default
        if default is not None:
            return default

        # Last resort: get default from schema
        try:
            return settings_schema.get_default_value(category, key)
        except KeyError:
            logger.warning(f"Setting {category}.{key} not found in schema")
            return None

    def set(
        self,
        category: str,
        key: str,
        value: Any,
        user_id: str,
        username: str,
        reason: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Update setting with validation and audit trail.

        Args:
            category: Setting category
            key: Setting key
            value: New value
            user_id: User ID making change
            username: Username making change
            reason: Reason for change (optional)
            ip_address: IP address of user (optional)

        Returns:
            Tuple of (success, error_message)
        """
        # Validate against schema
        if not settings_schema.validate_setting_value(category, key, value):
            return False, f"Invalid value for {category}.{key}: validation failed"

        try:
            with self.connection_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                # Get current setting
                cursor.execute(
                    """
                    SELECT id, value, value_type
                    FROM system_settings
                    WHERE category = %s AND key = %s
                    """,
                    (category, key)
                )
                current = cursor.fetchone()

                if not current:
                    # Setting doesn't exist in database, create it
                    return self._create_setting(
                        category, key, value, user_id, username, cursor, conn
                    )

                old_value = current['value']
                setting_id = current['id']
                value_type = current['value_type']

                # Convert value to string for storage
                new_value_str = self._serialize_value(value, value_type)

                # Update setting
                cursor.execute(
                    """
                    UPDATE system_settings
                    SET value = %s, updated_by = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (new_value_str, user_id, setting_id)
                )

                # Create audit log entry
                cursor.execute(
                    """
                    INSERT INTO settings_audit_log
                    (setting_id, category, key, old_value, new_value,
                     changed_by, changed_by_username, change_reason, ip_address)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (setting_id, category, key, old_value, new_value_str,
                     user_id, username, reason, ip_address)
                )

                conn.commit()

                # Invalidate cache
                self._cache_timestamp = 0

                # Log to audit file
                AuditLogger.log_config_change(
                    resource=AuditResource.APPLICATION_SETTING,
                    resource_id=f"{category}.{key}",
                    action=AuditAction.UPDATE,
                    user_id=user_id,
                    username=username,
                    changes={key: {'old': old_value, 'new': new_value_str}},
                    reason=reason or f'Setting {category}.{key} updated'
                )

                logger.info(f"Setting {category}.{key} updated by {username}")
                return True, None

        except Exception as e:
            logger.error(f"Error updating setting {category}.{key}: {e}", exc_info=True)
            return False, str(e)

    def _create_setting(
        self,
        category: str,
        key: str,
        value: Any,
        user_id: str,
        username: str,
        cursor,
        conn
    ) -> Tuple[bool, Optional[str]]:
        """Create a new setting in database from schema definition."""
        try:
            # Get definition from schema
            definition = settings_schema.get_setting_definition(category, key)

            value_type = definition.type
            default_value = definition.default
            description = definition.description

            # Convert value to string for storage
            value_str = self._serialize_value(value, value_type)
            default_str = self._serialize_value(default_value, value_type)

            # Insert setting
            cursor.execute(
                """
                INSERT INTO system_settings
                (category, key, value, value_type, default_value, description,
                 is_sensitive, requires_restart, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (category, key, value_str, value_type, default_str, description,
                 False, False, user_id)
            )

            setting_id = cursor.fetchone()['id']

            # Create audit log entry
            cursor.execute(
                """
                INSERT INTO settings_audit_log
                (setting_id, category, key, old_value, new_value,
                 changed_by, changed_by_username, change_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (setting_id, category, key, default_str, value_str,
                 user_id, username, f'Setting {category}.{key} created')
            )

            conn.commit()

            logger.info(f"Setting {category}.{key} created by {username}")
            return True, None

        except KeyError:
            return False, f"Setting {category}.{key} not defined in schema"
        except Exception as e:
            logger.error(f"Error creating setting {category}.{key}: {e}", exc_info=True)
            return False, str(e)

    def get_all(self, category: Optional[str] = None) -> Dict[str, Any]:
        """
        Get all settings, optionally filtered by category.

        Uses schema defaults for settings not yet in database.

        Args:
            category: Optional category filter

        Returns:
            Dictionary of settings grouped by category
        """
        # Start with schema defaults
        settings = {}

        if category:
            # Get defaults for specific category from schema
            schema_settings = settings_schema.APPLICATION_SETTINGS.get(category, {})
            if schema_settings:
                settings[category] = {}
                for key, definition in schema_settings.items():
                    settings[category][key] = {
                        'value': definition.default,
                        'default': definition.default,
                        'description': definition.description,
                        'type': definition.type,
                        'min': definition.min,
                        'max': definition.max,
                        'section': getattr(definition, 'section', None),
                        'updated_at': None,
                        'updated_by': None
                    }
        else:
            # Get defaults for all categories from schema
            for cat, schema_settings in settings_schema.APPLICATION_SETTINGS.items():
                settings[cat] = {}
                for key, definition in schema_settings.items():
                    settings[cat][key] = {
                        'value': definition.default,
                        'default': definition.default,
                        'description': definition.description,
                        'type': definition.type,
                        'min': definition.min,
                        'max': definition.max,
                        'section': getattr(definition, 'section', None),
                        'updated_at': None,
                        'updated_by': None
                    }

        # Override with database values where they exist
        try:
            with self.connection_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                if category:
                    cursor.execute(
                        """
                        SELECT category, key, value, value_type, default_value,
                               description, updated_at, updated_by
                        FROM system_settings
                        WHERE category = %s
                        ORDER BY category, key
                        """,
                        (category,)
                    )
                else:
                    cursor.execute(
                        """
                        SELECT category, key, value, value_type, default_value,
                               description, updated_at, updated_by
                        FROM system_settings
                        ORDER BY category, key
                        """
                    )

                for row in cursor.fetchall():
                    cat = row['category']
                    key = row['key']

                    # Only update if this is a known setting from schema
                    if cat in settings and key in settings[cat]:
                        value = self._parse_value(row['value'], row['value_type'])
                        settings[cat][key].update({
                            'value': value,
                            'default': row['default_value'],
                            'description': row['description'],
                            'type': row['value_type'],
                            'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
                            'updated_by': row['updated_by']
                        })

        except Exception as e:
            logger.warning(f"Error fetching settings from database: {e}")
            # Continue with schema defaults

        return settings

    def reset(
        self,
        category: str,
        key: str,
        user_id: str,
        username: str,
        reason: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Reset setting to default value from schema.

        Args:
            category: Setting category
            key: Setting key
            user_id: User ID making change
            username: Username making change
            reason: Reason for reset (optional)

        Returns:
            Tuple of (success, error_message)
        """
        try:
            # Get default from schema
            default_value = settings_schema.get_default_value(category, key)

            # Use set to update
            return self.set(
                category, key, default_value, user_id, username,
                reason or f"Reset {category}.{key} to default value"
            )

        except KeyError:
            return False, f"Setting {category}.{key} not defined in schema"
        except Exception as e:
            logger.error(f"Error resetting setting {category}.{key}: {e}", exc_info=True)
            return False, str(e)

    def get_schema(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """
        Get the entire settings schema.

        Returns:
            Dictionary representation of settings_schema.APPLICATION_SETTINGS
        """
        return settings_schema.get_schema_dict()

    def _parse_value(self, value: str, value_type: str) -> Any:
        """
        Parse string value to correct type.

        Args:
            value: String value from database
            value_type: Type name ('boolean', 'integer', 'string', 'float')

        Returns:
            Parsed value in correct type
        """
        try:
            if value_type == 'boolean':
                if isinstance(value, bool):
                    return value
                return str(value).lower() in ('true', '1', 'yes', 'on')
            elif value_type == 'integer':
                return int(value)
            elif value_type == 'float':
                return float(value)
            elif value_type == 'json':
                return json.loads(value)
            else:  # string
                return str(value)
        except Exception as e:
            logger.error(f"Error parsing value '{value}' as {value_type}: {e}")
            return value

    def _serialize_value(self, value: Any, value_type: str) -> str:
        """
        Serialize value to string for database storage.

        Args:
            value: Value to serialize
            value_type: Type name

        Returns:
            String representation
        """
        if value_type == 'boolean':
            return 'true' if value else 'false'
        elif value_type == 'json':
            return json.dumps(value)
        else:
            return str(value)
