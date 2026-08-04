# SPDX-License-Identifier: AGPL-3.0-or-later
from unittest.mock import MagicMock, patch

from src.modules.reference_data.service import ReferenceDataService
from src.modules.reference_data.sources import UploadedBytesSource


@patch('src.modules.reference_data.service.fetch_release_bytes')
def test_check_upstream_describes_latest_without_applying(mock_fetch, make_bundle, stub_metrics):
    """check_upstream returns a summary; it must NOT touch the DB or write history."""
    mock_fetch.return_value = (make_bundle(bundle_version='2026.17.2'), '2026.17.2')
    repo = MagicMock()
    repo.latest_successful.return_value = {'bundle_version': '2026.17.1'}
    cm = MagicMock()
    svc = ReferenceDataService(repo, cm, stub_metrics, app_version='2026.18.15')
    result = svc.check_upstream(repo='org/bundles')
    assert result['available'] is True
    assert result['upstream_version'] == '2026.17.2'
    assert result['current_version'] == '2026.17.1'
    assert {d['name'] for d in result['datasets']} == {'mac_vendor_ranges', 'os_eol_data'}
    repo.create_run.assert_not_called()
    repo.finish_run.assert_not_called()


@patch('src.modules.reference_data.service.fetch_release_bytes')
def test_check_upstream_reports_already_at_latest(mock_fetch, make_bundle, stub_metrics):
    mock_fetch.return_value = (make_bundle(bundle_version='2026.17.1'), '2026.17.1')
    repo = MagicMock()
    repo.latest_successful.return_value = {'bundle_version': '2026.17.1'}
    cm = MagicMock()
    svc = ReferenceDataService(repo, cm, stub_metrics, app_version='2026.18.15')
    result = svc.check_upstream(repo='org/bundles')
    assert result['available'] is False
    assert result['upstream_version'] == '2026.17.1'
    assert result['current_version'] == '2026.17.1'


def test_get_status_sweeps_stale_running_rows_first(stub_metrics):
    repo = MagicMock()
    repo.latest_successful.return_value = None
    repo.list_recent.return_value = []
    cm = MagicMock()
    svc = ReferenceDataService(repo, cm, stub_metrics, app_version='2026.18.14')
    svc.get_status()
    repo.sweep_stale_running.assert_called_once()


@patch('src.modules.reference_data.datasets.psycopg2.extras.execute_batch')
def test_apply_runs_dataset_apply_then_marks_success(_mock_batch, make_bundle, stub_metrics):
    repo = MagicMock()
    repo.create_run.return_value = 'run-1'
    cm = MagicMock()
    cur = MagicMock()
    cur.fetchone.side_effect = [(0,), (1,), (0,), (1,)]  # before/after × 2 datasets
    conn = MagicMock()
    conn.cursor.return_value = cur
    cm.get_connection.return_value.__enter__.return_value = conn
    svc = ReferenceDataService(repo, cm, stub_metrics, app_version='2026.5.0')
    result = svc.apply_bundle(UploadedBytesSource(make_bundle()), initiated_by='admin@x')
    assert result.status == 'success'
    assert {d.name for d in result.datasets} == {'mac_vendor_ranges', 'os_eol_data'}
    repo.create_run.assert_called_once()
    repo.finish_run.assert_called_once()
    conn.commit.assert_called_once()


def test_apply_does_not_commit_on_dataset_error(make_bundle, stub_metrics):
    """The connection manager's __exit__ rolls back on exception; the
    service must NOT commit and MUST mark the run failed."""
    repo = MagicMock()
    repo.create_run.return_value = 'run-2'
    cm = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = (0,)
    cur.execute.side_effect = RuntimeError('boom')
    conn = MagicMock()
    conn.cursor.return_value = cur
    cm.get_connection.return_value.__enter__.return_value = conn
    svc = ReferenceDataService(repo, cm, stub_metrics, app_version='2026.5.0')
    result = svc.apply_bundle(UploadedBytesSource(make_bundle()), initiated_by='admin@x')
    assert result.status == 'failed'
    conn.commit.assert_not_called()
    repo.finish_run.assert_called_once()
