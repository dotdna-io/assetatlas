# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Log Retention and Cleanup Service

Handles automated cleanup and archiving of log files based on retention policies.
Complies with audit trail requirements for compliance and regulatory needs.

Retention Policies:
- Application logs: 30 days (configurable)
- Audit logs: 365 days (minimum 90 days for compliance)
- Compressed archives: Retain for extended period

Usage:
    python -m src.api.utils.log_retention --cleanup
    python -m src.api.utils.log_retention --archive
"""

import os
import gzip
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple
import glob

logger = logging.getLogger(__name__)


class LogRetentionService:
    """Manages log file retention and archiving."""

    # Retention policies (in days)
    AUDIT_LOG_RETENTION_DAYS = 365  # Minimum 90 days for compliance
    APP_LOG_RETENTION_DAYS = 30
    ARCHIVE_RETENTION_DAYS = 730  # 2 years for compressed archives

    def __init__(self, log_dir: str = 'logs'):
        """
        Initialize log retention service.

        Args:
            log_dir: Directory containing log files
        """
        self.log_dir = Path(log_dir)
        self.archive_dir = self.log_dir / 'archive'

        # Create archive directory if it doesn't exist
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def archive_old_logs(self) -> Tuple[int, List[str]]:
        """
        Archive old log files by compressing them.

        Returns:
            Tuple of (count of files archived, list of archived filenames)
        """
        archived_files = []
        count = 0

        # Find log files that should be archived
        log_patterns = ['*.log.*', 'audit.log.*']  # Rotated log files

        for pattern in log_patterns:
            for log_file in self.log_dir.glob(pattern):
                if log_file.is_file() and not log_file.name.endswith('.gz'):
                    try:
                        # Compress the log file
                        archive_name = f"{log_file.name}.gz"
                        archive_path = self.archive_dir / archive_name

                        with open(log_file, 'rb') as f_in:
                            with gzip.open(archive_path, 'wb', compresslevel=9) as f_out:
                                shutil.copyfileobj(f_in, f_out)

                        # Remove original file after successful compression
                        log_file.unlink()

                        archived_files.append(archive_name)
                        count += 1
                        logger.info(f"Archived log file: {log_file.name} -> {archive_name}")

                    except Exception as e:
                        logger.error(f"Failed to archive {log_file.name}: {e}")

        return count, archived_files

    def cleanup_old_logs(self) -> Tuple[int, int]:
        """
        Remove log files older than retention policy.

        Returns:
            Tuple of (audit logs removed, app logs removed)
        """
        now = datetime.now()
        audit_removed = 0
        app_removed = 0

        # Clean up old audit logs (365 days)
        audit_cutoff = now - timedelta(days=self.AUDIT_LOG_RETENTION_DAYS)
        audit_removed += self._cleanup_files('audit.log*', audit_cutoff)

        # Clean up old application logs (30 days)
        app_cutoff = now - timedelta(days=self.APP_LOG_RETENTION_DAYS)
        app_removed += self._cleanup_files('app.log*', app_cutoff)
        app_removed += self._cleanup_files('error.log*', app_cutoff)

        # Clean up very old archives (2 years)
        archive_cutoff = now - timedelta(days=self.ARCHIVE_RETENTION_DAYS)
        archive_removed = self._cleanup_files('archive/*.gz', archive_cutoff)

        logger.info(
            f"Cleanup complete: {audit_removed} audit logs, {app_removed} app logs, "
            f"{archive_removed} archives removed"
        )

        return audit_removed, app_removed

    def _cleanup_files(self, pattern: str, cutoff: datetime) -> int:
        """
        Remove files matching pattern that are older than cutoff date.

        Args:
            pattern: Glob pattern for files to check
            cutoff: Delete files modified before this datetime

        Returns:
            Count of files removed
        """
        removed = 0

        for log_file in self.log_dir.glob(pattern):
            if log_file.is_file():
                try:
                    # Get file modification time
                    mtime = datetime.fromtimestamp(log_file.stat().st_mtime)

                    if mtime < cutoff:
                        log_file.unlink()
                        removed += 1
                        logger.info(f"Removed old log file: {log_file.name} (modified: {mtime})")

                except Exception as e:
                    logger.error(f"Failed to remove {log_file.name}: {e}")

        return removed

    def get_log_statistics(self) -> dict:
        """
        Get statistics about log files and disk usage.

        Returns:
            Dictionary with log statistics
        """
        stats = {
            'audit_logs': {'count': 0, 'size_mb': 0},
            'app_logs': {'count': 0, 'size_mb': 0},
            'archives': {'count': 0, 'size_mb': 0},
            'total_size_mb': 0
        }

        # Count audit logs
        for log_file in self.log_dir.glob('audit.log*'):
            if log_file.is_file():
                stats['audit_logs']['count'] += 1
                stats['audit_logs']['size_mb'] += log_file.stat().st_size / (1024 * 1024)

        # Count application logs
        for pattern in ['app.log*', 'error.log*']:
            for log_file in self.log_dir.glob(pattern):
                if log_file.is_file():
                    stats['app_logs']['count'] += 1
                    stats['app_logs']['size_mb'] += log_file.stat().st_size / (1024 * 1024)

        # Count archives
        for log_file in self.archive_dir.glob('*.gz'):
            if log_file.is_file():
                stats['archives']['count'] += 1
                stats['archives']['size_mb'] += log_file.stat().st_size / (1024 * 1024)

        stats['total_size_mb'] = (
            stats['audit_logs']['size_mb'] +
            stats['app_logs']['size_mb'] +
            stats['archives']['size_mb']
        )

        return stats


def main():
    """
    Main entry point for command-line execution.

    Usage:
        python -m src.api.utils.log_retention --cleanup
        python -m src.api.utils.log_retention --archive
        python -m src.api.utils.log_retention --stats
    """
    import argparse

    parser = argparse.ArgumentParser(description='Log retention and archival service')
    parser.add_argument('--cleanup', action='store_true', help='Remove old log files')
    parser.add_argument('--archive', action='store_true', help='Archive old log files')
    parser.add_argument('--stats', action='store_true', help='Show log statistics')
    parser.add_argument('--log-dir', default='logs', help='Log directory path')

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    service = LogRetentionService(args.log_dir)

    if args.stats:
        stats = service.get_log_statistics()
        print("\n=== Log Statistics ===")
        print(f"Audit logs: {stats['audit_logs']['count']} files, {stats['audit_logs']['size_mb']:.2f} MB")
        print(f"App logs: {stats['app_logs']['count']} files, {stats['app_logs']['size_mb']:.2f} MB")
        print(f"Archives: {stats['archives']['count']} files, {stats['archives']['size_mb']:.2f} MB")
        print(f"Total: {stats['total_size_mb']:.2f} MB")

    if args.archive:
        count, files = service.archive_old_logs()
        print(f"\nArchived {count} log files")
        if files:
            print("Archived files:")
            for f in files:
                print(f"  - {f}")

    if args.cleanup:
        audit_removed, app_removed = service.cleanup_old_logs()
        print(f"\nRemoved {audit_removed} audit logs and {app_removed} application logs")


if __name__ == '__main__':
    main()
