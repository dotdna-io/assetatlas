# SPDX-License-Identifier: AGPL-3.0-or-later
"""ReferenceDataService — orchestrates manifest verification, dataset apply, and history."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from src.modules.reference_data.datasets import REGISTRY
from src.modules.reference_data.manifest import (
    InvalidBundleError,
    IncompatibleAppVersionError,
    parse_and_verify,
)
from src.modules.reference_data.models import (
    BundleApplyResult,
    DatasetApplyResult,
)
from src.modules.reference_data.sources import BundleSource, fetch_release_bytes

logger = logging.getLogger(__name__)


class ReferenceDataService:
    def __init__(self, repository, connection_manager, metrics, app_version: str):
        self.repo = repository
        self.cm = connection_manager
        self.metrics = metrics
        self.app_version = app_version

    def get_status(self) -> dict:
        # Reap any `running` rows abandoned by a prior crashed/reloaded process
        # so the history doesn't show a permanently-stuck run.
        self.repo.sweep_stale_running()
        latest = self.repo.latest_successful()
        return {
            'current_bundle_version': latest['bundle_version'] if latest else None,
            'last_applied_at': latest['finished_at'].isoformat() if latest else None,
            'datasets': latest['datasets'] if latest else [],
            'history': self.repo.list_recent(limit=20),
        }

    def check_upstream(self, repo: str) -> dict:
        """Inspect the latest upstream release without applying.

        Downloads + verifies the bundle, returns a summary the UI can show as a
        confirmation panel. No DB writes; safe to call repeatedly.
        """
        bundle_bytes, upstream_version = fetch_release_bytes(repo, version=None)
        try:
            parsed = parse_and_verify(bundle_bytes, current_app_version=self.app_version)
        except (InvalidBundleError, IncompatibleAppVersionError) as e:
            return {
                'available': False,
                'reason': 'invalid',
                'message': str(e),
                'upstream_version': upstream_version,
                'current_version': (self.repo.latest_successful() or {}).get('bundle_version'),
            }

        latest = self.repo.latest_successful()
        current_version = latest['bundle_version'] if latest else None
        return {
            'available': upstream_version != current_version,
            'upstream_version': upstream_version,
            'current_version': current_version,
            'generated_at': parsed.manifest.generated_at.isoformat(),
            'app_compat_min': parsed.manifest.app_compat.min,
            'datasets': [
                {'name': d.name, 'row_count': d.row_count, 'source': d.source}
                for d in parsed.manifest.datasets
            ],
        }

    def apply_bundle(self, source: BundleSource, initiated_by: Optional[str]) -> BundleApplyResult:
        started = datetime.now(timezone.utc)
        bundle_bytes, _source_hint = source.fetch()

        try:
            parsed = parse_and_verify(bundle_bytes, current_app_version=self.app_version)
        except (InvalidBundleError, IncompatibleAppVersionError) as e:
            logger.warning("reference-data bundle rejected: %s", e)
            return BundleApplyResult(
                bundle_version='(invalid)',
                source=source.label(),
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                status='failed',
                datasets=[],
                error_message=str(e),
            )

        manifest = parsed.manifest
        run_id = self.repo.create_run(manifest.bundle_version, source.label(), initiated_by)

        dataset_results: list[DatasetApplyResult] = []
        try:
            # get_connection() rolls back + releases on any exception inside the
            # `with` block. We just commit on success.
            with self.cm.get_connection() as conn:
                cur = conn.cursor()
                for entry in manifest.datasets:
                    ds = REGISTRY.get(entry.name)
                    if ds is None:
                        raise InvalidBundleError(f"unknown dataset {entry.name!r}")
                    payload = parsed.payloads[entry.payload]
                    r = ds.apply(cur, payload)
                    dataset_results.append(DatasetApplyResult(
                        name=r.name,
                        rows_before=r.rows_before,
                        rows_after=r.rows_after,
                        applied=True,
                    ))
                conn.commit()
        except Exception as e:
            logger.exception("reference-data apply failed mid-flight")
            self.repo.finish_run(
                run_id, 'failed',
                datasets=[d.model_dump() for d in dataset_results],
                error_message=str(e),
            )
            return BundleApplyResult(
                bundle_version=manifest.bundle_version,
                source=source.label(),
                started_at=started,
                finished_at=datetime.now(timezone.utc),
                status='failed',
                datasets=dataset_results,
                error_message=str(e),
            )

        self.repo.finish_run(
            run_id, 'success',
            datasets=[d.model_dump() for d in dataset_results],
        )
        for d in dataset_results:
            self.metrics.reference_data_records_total.labels(dataset=d.name).set(d.rows_after)
            self.metrics.reference_data_last_updated_timestamp.labels(dataset=d.name).set_to_current_time()

        return BundleApplyResult(
            bundle_version=manifest.bundle_version,
            source=source.label(),
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            status='success',
            datasets=dataset_results,
        )
