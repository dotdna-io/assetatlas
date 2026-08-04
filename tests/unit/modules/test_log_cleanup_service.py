# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for LogCleanupService -- retention must not depend on the host timezone.

Issue #1162: ``datetime.fromtimestamp(st_mtime)`` returns naive *local* time while
``datetime.utcnow()`` returns naive *UTC*. Comparing the two skews every retention
decision by the host's UTC offset -- a host east of UTC keeps files longer than
configured, a host west of UTC compresses and deletes them early. Nothing caught it
because the containers run UTC, where the offset is zero.

The parity tests below run the same decision under ``TZ=UTC``,
``TZ=America/Los_Angeles`` and ``TZ=Asia/Kolkata`` and require an identical answer.
They drive ``_compress_old_logs`` / ``_delete_expired_logs`` directly on purpose:
``run_cleanup`` wraps both in a ``try/except`` that appends to ``errors`` and returns
a zero count, so a half-fixed comparison (aware on one side, naive on the other)
raises ``TypeError`` there and presents as a cleanup that silently did nothing.
``TestRunCleanup`` covers that swallowing path separately.
"""

import logging
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from src.modules.logs.services.cleanup_service import LogCleanupService
from tests.tz_helpers import (
    EAST_OF_UTC_TZ,
    UTC_TZ,
    WEST_OF_UTC_TZ,
    local_timezone,
)

DAY = 86400
HOUR = 3600

# The service logs through `logging.getLogger(__name__)`, so its records carry
# its own module name -- taken from the class so a move cannot stale it out.
CLEANUP_LOGGER = LogCleanupService.__module__

# Smaller than every timezone offset under test, so a file this far inside the
# threshold is only ever misjudged by a wrong-clock comparison.
MARGIN = 2 * HOUR

OFFSET_TIMEZONES = [WEST_OF_UTC_TZ, EAST_OF_UTC_TZ]


def _write_log(path: Path, age_seconds: float) -> float:
    """Create ``path`` with an mtime ``age_seconds`` in the past; return that mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('log line\n')
    mtime = time.time() - age_seconds
    os.utime(path, (mtime, mtime))
    return mtime


def _service(tmp_path: Path) -> LogCleanupService:
    """Service rooted at a temp log tree, with no DB (so settings are the defaults)."""
    return LogCleanupService(log_base_dir=str(tmp_path))


class TestCompression:
    """_compress_old_logs -- rotated files older than the threshold get compressed."""

    def test_compresses_file_older_than_threshold(self, tmp_path):
        _write_log(tmp_path / 'api' / 'app.log.1', 10 * DAY)
        count, _ = _service(tmp_path)._compress_old_logs(7, dry_run=True)
        assert count == 1

    def test_keeps_file_newer_than_threshold(self, tmp_path):
        _write_log(tmp_path / 'api' / 'app.log.1', 2 * DAY)
        count, _ = _service(tmp_path)._compress_old_logs(7, dry_run=True)
        assert count == 0

    def test_real_run_replaces_original_with_gzip(self, tmp_path):
        log_file = tmp_path / 'api' / 'app.log.1'
        _write_log(log_file, 10 * DAY)

        count, _ = _service(tmp_path)._compress_old_logs(7, dry_run=False)

        assert count == 1
        assert not log_file.exists()
        assert (tmp_path / 'api' / 'app.log.1.gz').exists()


class TestCompressionIsTimezoneIndependent:
    """The compression cutoff must be the same instant in every timezone (#1162)."""

    @pytest.mark.parametrize('tz', OFFSET_TIMEZONES)
    def test_file_just_inside_threshold_is_kept(self, tmp_path, tz):
        """6d22h old with a 7-day threshold: keep it, whatever the host TZ says."""
        _write_log(tmp_path / 'api' / 'app.log.1', 7 * DAY - MARGIN)
        service = _service(tmp_path)

        with local_timezone(UTC_TZ):
            utc_count, _ = service._compress_old_logs(7, dry_run=True)
        with local_timezone(tz):
            offset_count, _ = service._compress_old_logs(7, dry_run=True)

        assert utc_count == 0
        assert offset_count == utc_count, (
            f'TZ={tz} compressed {offset_count} file(s) where UTC compressed '
            f'{utc_count}: the cutoff is being read on two different clocks'
        )

    @pytest.mark.parametrize('tz', OFFSET_TIMEZONES)
    def test_file_just_outside_threshold_is_compressed(self, tmp_path, tz):
        """7d2h old with a 7-day threshold: compress it, whatever the host TZ says."""
        _write_log(tmp_path / 'api' / 'app.log.1', 7 * DAY + MARGIN)
        service = _service(tmp_path)

        with local_timezone(UTC_TZ):
            utc_count, _ = service._compress_old_logs(7, dry_run=True)
        with local_timezone(tz):
            offset_count, _ = service._compress_old_logs(7, dry_run=True)

        assert utc_count == 1
        assert offset_count == utc_count, (
            f'TZ={tz} compressed {offset_count} file(s) where UTC compressed '
            f'{utc_count}: the cutoff is being read on two different clocks'
        )


class TestDeletion:
    """_delete_expired_logs -- rotated files past their retention window go away."""

    RETENTION = {'app': 30}

    def test_deletes_file_older_than_retention(self, tmp_path):
        _write_log(tmp_path / 'api' / 'app.log.1', 45 * DAY)
        count, _ = _service(tmp_path)._delete_expired_logs(self.RETENTION, dry_run=True)
        assert count == 1

    def test_keeps_file_inside_retention(self, tmp_path):
        _write_log(tmp_path / 'api' / 'app.log.1', 5 * DAY)
        count, _ = _service(tmp_path)._delete_expired_logs(self.RETENTION, dry_run=True)
        assert count == 0

    def test_never_deletes_the_live_log_file(self, tmp_path):
        """app.log has no rotation suffix, so it is out of scope however old it is."""
        live_log = tmp_path / 'api' / 'app.log'
        _write_log(live_log, 400 * DAY)

        count, _ = _service(tmp_path)._delete_expired_logs(self.RETENTION, dry_run=False)

        assert count == 0
        assert live_log.exists()

    def test_real_run_removes_the_file(self, tmp_path):
        log_file = tmp_path / 'api' / 'app.log.1'
        _write_log(log_file, 45 * DAY)

        count, freed = _service(tmp_path)._delete_expired_logs(self.RETENTION, dry_run=False)

        assert count == 1
        assert freed > 0
        assert not log_file.exists()


class TestDeletionIsTimezoneIndependent:
    """The retention cutoff must be the same instant in every timezone (#1162)."""

    RETENTION = {'app': 30}

    @pytest.mark.parametrize('tz', OFFSET_TIMEZONES)
    def test_file_just_inside_retention_is_kept(self, tmp_path, tz):
        _write_log(tmp_path / 'api' / 'app.log.1', 30 * DAY - MARGIN)
        service = _service(tmp_path)

        with local_timezone(UTC_TZ):
            utc_count, _ = service._delete_expired_logs(self.RETENTION, dry_run=True)
        with local_timezone(tz):
            offset_count, _ = service._delete_expired_logs(self.RETENTION, dry_run=True)

        assert utc_count == 0
        assert offset_count == utc_count, (
            f'TZ={tz} would delete {offset_count} file(s) where UTC would delete '
            f'{utc_count}: retention is being enforced on two different clocks'
        )

    @pytest.mark.parametrize('tz', OFFSET_TIMEZONES)
    def test_file_just_outside_retention_is_deleted(self, tmp_path, tz):
        _write_log(tmp_path / 'api' / 'app.log.1', 30 * DAY + MARGIN)
        service = _service(tmp_path)

        with local_timezone(UTC_TZ):
            utc_count, _ = service._delete_expired_logs(self.RETENTION, dry_run=True)
        with local_timezone(tz):
            offset_count, _ = service._delete_expired_logs(self.RETENTION, dry_run=True)

        assert utc_count == 1
        assert offset_count == utc_count, (
            f'TZ={tz} would delete {offset_count} file(s) where UTC would delete '
            f'{utc_count}: retention is being enforced on two different clocks'
        )


class TestDeletionVisitsEachFileOnce:
    """Every expired file is counted -- and unlinked -- exactly once (#1213).

    ``app.log.1.gz`` already matches ``app.log.*``, so pairing that glob with a
    second ``app.log.*.gz`` one covered every compressed log twice and inflated
    the dry-run count by one per ``.gz``. Every other case in this file uses an
    uncompressed ``app.log.1``, matched by one of the two patterns only, so none
    of them reproduces this.

    The real-run case below is a guard rather than a reproducer: the two
    patterns were two separate ``glob()`` calls run in sequence, so the second
    scanned the directory *after* the first pass had unlinked the file and found
    nothing left to fail on. It is here because that immunity came from the
    sequencing and not from pathlib -- a directory's ``scandir`` is materialised
    at the top of each glob, so entries unlinked mid-loop are still handed out.
    A "collect the matches, then iterate" rewrite would therefore stat a path it
    had already deleted and log a delete failure for a file that was in fact
    deleted correctly.
    """

    RETENTION = {'app': 30}

    def test_dry_run_counts_an_expired_gz_once(self, tmp_path):
        # Content is irrelevant -- deletion stat()s the file, never opens it.
        _write_log(tmp_path / 'api' / 'app.log.1.gz', 45 * DAY)

        count, _ = _service(tmp_path)._delete_expired_logs(self.RETENTION, dry_run=True)

        assert count == 1

    def test_dry_run_counts_plain_and_compressed_files_once_each(self, tmp_path):
        _write_log(tmp_path / 'api' / 'app.log.1', 45 * DAY)
        _write_log(tmp_path / 'api' / 'app.log.2.gz', 45 * DAY)

        count, _ = _service(tmp_path)._delete_expired_logs(self.RETENTION, dry_run=True)

        assert count == 2, (
            'an operator sizing a cleanup must see the real file count, not one '
            'inflated by however many of them are compressed'
        )

    def test_real_run_deletes_an_expired_gz_without_reporting_a_failure(self, tmp_path, caplog):
        """The cleanup succeeded, so nothing may claim in the log that it didn't."""
        gz_file = tmp_path / 'api' / 'app.log.1.gz'
        _write_log(gz_file, 45 * DAY)

        with caplog.at_level(logging.ERROR, logger=CLEANUP_LOGGER):
            count, freed = _service(tmp_path)._delete_expired_logs(
                self.RETENTION, dry_run=False
            )

        assert count == 1
        assert freed > 0
        assert not gz_file.exists()
        assert 'Failed to delete' not in caplog.text, (
            f'the file was deleted correctly, but the run reported otherwise: '
            f'{caplog.text}'
        )


class TestRunCleanup:
    """run_cleanup -- the public path, whose try/except would hide a clock TypeError."""

    def test_reports_no_errors_under_a_non_utc_host_timezone(self, tmp_path):
        """A naive-vs-aware comparison raises here and is swallowed into `errors`."""
        _write_log(tmp_path / 'api' / 'app.log.1', 10 * DAY)

        with local_timezone(WEST_OF_UTC_TZ):
            result = _service(tmp_path).run_cleanup(dry_run=True)

        assert result.errors == []
        # 10 days old: past the 7-day compression threshold, inside the 30-day
        # retention for `app`.
        assert result.compressed_files == 1
        assert result.deleted_files == 0

    def test_timestamp_is_a_utc_labelled_instant(self, tmp_path):
        """The reported start time must parse back to the instant it was taken."""
        with local_timezone(WEST_OF_UTC_TZ):
            before = time.time()
            result = _service(tmp_path).run_cleanup(dry_run=True)
            after = time.time()

        parsed = datetime.fromisoformat(result.timestamp.replace('Z', '+00:00'))
        assert parsed.tzinfo is not None, (
            f'{result.timestamp!r} carries no offset, so any consumer parsing it '
            f'reads it in ITS local timezone'
        )
        assert before <= parsed.timestamp() <= after

    def test_duration_is_not_skewed_by_the_host_timezone(self, tmp_path):
        with local_timezone(WEST_OF_UTC_TZ):
            result = _service(tmp_path).run_cleanup(dry_run=True)

        assert 0 <= result.duration_seconds < 60


class TestStatusOldestFileAge:
    """get_status()['oldest_file_age_days'] -- a true epoch on both sides (#1162)."""

    @pytest.mark.parametrize('tz', [UTC_TZ] + OFFSET_TIMEZONES)
    def test_age_matches_the_file_mtime_in_every_timezone(self, tmp_path, tz):
        _write_log(tmp_path / 'api' / 'app.log.1', 3 * DAY)

        with local_timezone(tz):
            status = _service(tmp_path).get_status()

        assert status['oldest_file'] == str(Path('api') / 'app.log.1')
        assert status['oldest_file_age_days'] == pytest.approx(3.0, abs=0.05), (
            f"TZ={tz} reports {status['oldest_file_age_days']} days for a file "
            f'3.0 days old: `now` and the mtime are on different clocks'
        )

    def test_age_is_none_without_log_files(self, tmp_path):
        assert _service(tmp_path).get_status()['oldest_file_age_days'] is None

    def test_epoch_zero_mtime_still_reports_an_age(self, tmp_path):
        """A file whose mtime is exactly 0.0 is a real file, not a missing one.

        It happens to logs restored from an archive that carried no timestamps,
        and 0.0 is falsy — so a truthiness guard reports `oldest_file` while
        nulling its age, which reads as "there is no oldest file" while naming one.
        """
        log_file = tmp_path / 'api' / 'app.log.1'
        _write_log(log_file, 0)
        os.utime(log_file, (0, 0))

        status = _service(tmp_path).get_status()

        assert status['oldest_file'] == str(Path('api') / 'app.log.1')
        # 1970 is a long time ago; the point is that it is a number, not None.
        assert status['oldest_file_age_days'] > 10_000
