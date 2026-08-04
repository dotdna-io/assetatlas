# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Log Cleanup Service

Manages log file cleanup based on configurable retention policies.
Integrates with SettingsManager for runtime configuration.
"""

import os
import gzip
import shutil
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class CleanupResult:
    """Result of a cleanup operation"""
    timestamp: str
    compressed_files: int
    deleted_files: int
    errors: List[str]
    disk_space_freed_bytes: int
    duration_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LogCleanupService:
    """
    Manages log file cleanup with configurable retention policies.

    Features:
    - Compress old logs after configurable days
    - Delete logs based on per-type retention periods
    - Track cleanup history and statistics
    - Support dry-run mode

    Every clock in here is UTC-aware. `st_mtime` is a true epoch, so an mtime must
    be read with `tz=timezone.utc` and compared against `datetime.now(timezone.utc)`;
    the previous mix of naive-local mtimes and naive-UTC cutoffs skewed every
    retention decision by the host's UTC offset (#1162).
    """

    DEFAULT_RETENTION = {
        "access": 30,
        "app": 30,
        "error": 90,
        "security": 365,
        "performance": 60
    }

    DEFAULT_SETTINGS = {
        "retention_days": DEFAULT_RETENTION,
        "compression_after_days": 7,
        "archive_enabled": False,
        "archive_path": "",
        "cleanup_schedule": "0 2 * * *",
        "enabled": True
    }

    def __init__(self, log_base_dir: str = "logs", connection_manager=None):
        """
        Initialize LogCleanupService.

        Args:
            log_base_dir: Base directory for logs (relative to project root)
            connection_manager: Optional database connection manager for persistent config
        """
        self.log_base_dir = Path(log_base_dir).resolve()
        self.connection_manager = connection_manager
        self._last_cleanup_result: Optional[CleanupResult] = None

        logger.info(f"LogCleanupService initialized with base dir: {self.log_base_dir}")

    def get_settings(self) -> Dict[str, Any]:
        """Get current cleanup settings from database or defaults."""
        if self.connection_manager:
            try:
                import json
                from psycopg2 import extras
                with self.connection_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                    cursor.execute(
                        "SELECT value FROM system_settings WHERE category = 'log_retention' AND key = 'config'"
                    )
                    result = cursor.fetchone()
                    if result:
                        settings = json.loads(result['value'])
                        # Merge with defaults for any missing keys
                        merged = self.DEFAULT_SETTINGS.copy()
                        merged.update(settings)
                        return merged
            except Exception as e:
                logger.warning(f"Failed to load settings from database: {e}, using defaults")

        return self.DEFAULT_SETTINGS.copy()

    def update_settings(self, new_settings: Dict[str, Any]) -> bool:
        """Update cleanup settings in database."""
        if not self.connection_manager:
            logger.warning("No connection manager configured, cannot persist settings")
            return False

        try:
            import json
            from psycopg2 import extras
            current = self.get_settings()
            # Deep merge for nested dicts like retention_days
            for key, value in new_settings.items():
                if key == 'retention_days' and isinstance(value, dict):
                    if 'retention_days' not in current:
                        current['retention_days'] = {}
                    current['retention_days'].update(value)
                else:
                    current[key] = value

            with self.connection_manager.get_cursor(cursor_factory=extras.RealDictCursor) as (cursor, conn):
                # Use upsert pattern - include default_value which is required
                cursor.execute(
                    """
                    INSERT INTO system_settings (category, key, value, value_type, default_value, description, updated_at)
                    VALUES ('log_retention', 'config', %s, 'json', %s, 'Log retention and cleanup configuration', NOW())
                    ON CONFLICT (category, key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    (json.dumps(current), json.dumps(self.DEFAULT_SETTINGS))
                )
                conn.commit()

            logger.info(f"Log retention settings updated: {list(new_settings.keys())}")
            return True
        except Exception as e:
            logger.error(f"Failed to update settings: {e}")
            return False

    def run_cleanup(self, dry_run: bool = False) -> CleanupResult:
        """
        Execute log cleanup based on current settings.

        Args:
            dry_run: If True, only report what would be done

        Returns:
            CleanupResult with statistics
        """
        start_time = datetime.now(timezone.utc)
        settings = self.get_settings()

        if not settings.get("enabled", True):
            logger.info("Log cleanup is disabled")
            return CleanupResult(
                timestamp=start_time.isoformat().replace('+00:00', 'Z'),
                compressed_files=0,
                deleted_files=0,
                errors=["Cleanup is disabled"],
                disk_space_freed_bytes=0,
                duration_seconds=0
            )

        logger.info(f"Starting log cleanup (dry_run={dry_run})")

        errors = []
        compressed_count = 0
        deleted_count = 0
        space_freed = 0

        # Step 1: Compress old logs
        try:
            compressed_count, compress_space = self._compress_old_logs(
                settings["compression_after_days"],
                dry_run
            )
            space_freed += compress_space
        except Exception as e:
            error_msg = f"Compression failed: {str(e)}"
            logger.error(error_msg)
            errors.append(error_msg)

        # Step 2: Delete expired logs
        try:
            deleted_count, delete_space = self._delete_expired_logs(
                settings["retention_days"],
                dry_run
            )
            space_freed += delete_space
        except Exception as e:
            error_msg = f"Deletion failed: {str(e)}"
            logger.error(error_msg)
            errors.append(error_msg)

        duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        result = CleanupResult(
            timestamp=start_time.isoformat().replace('+00:00', 'Z'),
            compressed_files=compressed_count,
            deleted_files=deleted_count,
            errors=errors,
            disk_space_freed_bytes=space_freed,
            duration_seconds=round(duration, 2)
        )

        self._last_cleanup_result = result

        logger.info(
            f"Cleanup completed: compressed={compressed_count}, "
            f"deleted={deleted_count}, freed={space_freed} bytes, "
            f"duration={duration:.2f}s, errors={len(errors)}"
        )

        return result

    def _compress_old_logs(self, days_threshold: int, dry_run: bool) -> tuple[int, int]:
        """
        Compress log files older than threshold.

        Returns:
            Tuple of (files_compressed, bytes_saved)
        """
        count = 0
        space_saved = 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_threshold)

        # Find rotated log files (*.log.1, *.log.2, etc.)
        for log_file in self.log_base_dir.rglob("*.log.[0-9]"):
            if not log_file.is_file():
                continue

            # Check modification time — both sides UTC-aware, or the comparison
            # is off by the host's UTC offset (#1162)
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=timezone.utc)
            if mtime >= cutoff:
                continue

            if dry_run:
                logger.info(f"[DRY-RUN] Would compress: {log_file}")
                count += 1
            else:
                try:
                    original_size = log_file.stat().st_size
                    compressed_path = Path(str(log_file) + ".gz")

                    with open(log_file, 'rb') as f_in:
                        with gzip.open(compressed_path, 'wb', compresslevel=6) as f_out:
                            shutil.copyfileobj(f_in, f_out)

                    # Remove original after successful compression
                    log_file.unlink()

                    compressed_size = compressed_path.stat().st_size
                    space_saved += original_size - compressed_size
                    count += 1

                    logger.debug(f"Compressed {log_file} ({original_size} -> {compressed_size})")
                except Exception as e:
                    logger.error(f"Failed to compress {log_file}: {e}")

        return count, space_saved

    def _delete_expired_logs(
        self,
        retention_days: Dict[str, int],
        dry_run: bool
    ) -> tuple[int, int]:
        """
        Delete log files exceeding retention period.

        Returns:
            Tuple of (files_deleted, bytes_freed)
        """
        total_count = 0
        total_space = 0

        # Map of log type to directory and file pattern
        log_types = {
            "access": ("api", "access.log"),
            "app": ("api", "app.log"),
            "error": ("api", "error.log"),
            "security": ("security", "alerts.log"),
            "performance": ("performance", "slow.log")
        }

        for log_type, (subdir, base_name) in log_types.items():
            retention = retention_days.get(log_type, 30)
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention)

            log_dir = self.log_base_dir / subdir
            if not log_dir.exists():
                continue

            # Find rotated and compressed versions — one glob covers both, since
            # `app.log.1` and `app.log.1.gz` alike match `app.log.*`. Pairing it
            # with an `app.log.*.gz` glob would only re-match the compressed half
            # and count every one of them twice (#1213).
            for log_file in log_dir.glob(f"{base_name}.*"):
                if not log_file.is_file():
                    continue

                # Skip the current log file (no rotation suffix)
                if log_file.name == base_name:
                    continue

                # Both sides UTC-aware — see _compress_old_logs (#1162)
                mtime = datetime.fromtimestamp(
                    log_file.stat().st_mtime, tz=timezone.utc
                )
                if mtime >= cutoff:
                    continue

                if dry_run:
                    logger.info(f"[DRY-RUN] Would delete: {log_file}")
                    total_count += 1
                else:
                    try:
                        size = log_file.stat().st_size
                        log_file.unlink()
                        total_count += 1
                        total_space += size
                        logger.debug(f"Deleted {log_file} ({size} bytes)")
                    except Exception as e:
                        logger.error(f"Failed to delete {log_file}: {e}")

        return total_count, total_space

    def get_status(self) -> Dict[str, Any]:
        """Get current cleanup service status and statistics."""
        settings = self.get_settings()

        # Calculate current disk usage
        total_size = 0
        file_count = 0

        for log_file in self.log_base_dir.rglob("*"):
            if log_file.is_file():
                total_size += log_file.stat().st_size
                file_count += 1

        # Get oldest file
        oldest_file = None
        oldest_time = None

        for log_file in self.log_base_dir.rglob("*.log*"):
            if log_file.is_file():
                mtime = log_file.stat().st_mtime
                if oldest_time is None or mtime < oldest_time:
                    oldest_time = mtime
                    oldest_file = str(log_file.relative_to(self.log_base_dir))

        status = {
            "enabled": settings.get("enabled", True),
            "schedule": settings.get("cleanup_schedule", "0 2 * * *"),
            "retention_days": settings.get("retention_days", self.DEFAULT_RETENTION),
            "compression_after_days": settings.get("compression_after_days", 7),
            "disk_usage": {
                "total_bytes": total_size,
                "total_mb": round(total_size / (1024 * 1024), 2),
                "file_count": file_count
            },
            "oldest_file": oldest_file,
            # oldest_time is a raw st_mtime epoch, so `now` has to be a true epoch too:
            # naive datetime.utcnow().timestamp() would re-read the UTC wall-clock as
            # local and skew the age by the host's UTC offset (#1162).
            # `is not None`, not a truthiness test: an mtime of exactly 0.0 (epoch —
            # e.g. a log restored from an archive that carried no timestamps) is a
            # real file, and reporting `oldest_file` while nulling its age is worse
            # than reporting a large age.
            "oldest_file_age_days": round((datetime.now(timezone.utc).timestamp() - oldest_time) / 86400, 1) if oldest_time is not None else None,
            "last_cleanup": self._last_cleanup_result.to_dict() if self._last_cleanup_result else None
        }

        return status
