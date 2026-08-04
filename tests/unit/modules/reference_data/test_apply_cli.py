# SPDX-License-Identifier: AGPL-3.0-or-later
"""The installer's reference-data entrypoint.

deploy/lib/reference_data.sh branches on this module's EXIT CODE, so the 0/1/2
contract is load-bearing across a language boundary: 2 means "upstream
unreachable", which the installer reports as a skip with consequences rather than
a failure, and anything else non-zero is a real error. Collapsing them would make
an air-gapped install look broken, or a broken install look fine.

The service itself is mocked — apply_bundle's verification and transaction
handling are its own concern; what is under test is the shim's dispatch and its
exit codes.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.modules.reference_data import apply as apply_cli


def _result(status='success', datasets=None, version='2026.30.0526', error=None):
    return SimpleNamespace(
        status=status,
        bundle_version=version,
        error_message=error,
        datasets=datasets if datasets is not None else [
            SimpleNamespace(name='mac_vendor_ranges', rows_before=0,
                            rows_after=52000, applied=True, skipped_reason=None),
        ],
    )


@pytest.fixture
def fake_app():
    app = MagicMock()
    app.app_context.return_value.__enter__ = lambda s: None
    app.app_context.return_value.__exit__ = lambda s, *a: False
    return app


def _run(argv, fake_app, stdin=None):
    with patch('src.api.factory.create_app', return_value=fake_app), \
         patch.object(apply_cli.sys, 'stdin', SimpleNamespace(buffer=SimpleNamespace(
             read=lambda: stdin if stdin is not None else b''))):
        return apply_cli.main(argv)


def test_successful_apply_exits_zero(fake_app, capsys):
    fake_app.reference_data_service.apply_bundle.return_value = _result()
    assert _run(['--latest'], fake_app) == apply_cli.EXIT_OK
    out = capsys.readouterr().out
    assert 'mac_vendor_ranges 0->52000' in out


def test_upstream_unavailable_exits_two_not_one(fake_app):
    """The air-gapped case. It must stay distinguishable from a real failure so
    the installer can call it a skip rather than an error."""
    from src.modules.reference_data.sources import UpstreamUnavailableError

    fake_app.reference_data_service.apply_bundle.side_effect = \
        UpstreamUnavailableError('no route to host')
    assert _run(['--latest'], fake_app) == apply_cli.EXIT_UPSTREAM_UNAVAILABLE


def test_rejected_bundle_exits_one(fake_app):
    fake_app.reference_data_service.apply_bundle.return_value = \
        _result(status='failed', error='sha256 mismatch')
    assert _run(['--latest'], fake_app) == apply_cli.EXIT_FAILED


def test_empty_stdin_is_an_error_not_a_silent_success(fake_app):
    """A truncated or absent pipe must not be reported as 'applied'."""
    assert _run(['--stdin'], fake_app, stdin=b'') == apply_cli.EXIT_FAILED
    fake_app.reference_data_service.apply_bundle.assert_not_called()


def test_stdin_builds_an_upload_source(fake_app):
    fake_app.reference_data_service.apply_bundle.return_value = _result()
    assert _run(['--stdin', '--label', 'b.tar.gz'], fake_app, stdin=b'xyz') == apply_cli.EXIT_OK
    src = fake_app.reference_data_service.apply_bundle.call_args[0][0]
    # Assert through the public contract rather than a private attribute: fetch()
    # is what the service actually calls.
    assert src.fetch() == (b'xyz', 'b.tar.gz')
    assert src.label() == 'upload'


def test_latest_builds_a_github_source(fake_app):
    from src.modules.reference_data.sources import GitHubReleaseSource

    fake_app.reference_data_service.apply_bundle.return_value = _result()
    assert _run(['--latest'], fake_app) == apply_cli.EXIT_OK
    src = fake_app.reference_data_service.apply_bundle.call_args[0][0]
    assert isinstance(src, GitHubReleaseSource)


def test_initiated_by_is_recorded(fake_app):
    """install vs update shows in the apply history, so an operator can tell which
    run seeded the data."""
    fake_app.reference_data_service.apply_bundle.return_value = _result()
    _run(['--latest', '--initiated-by', 'update'], fake_app)
    assert fake_app.reference_data_service.apply_bundle.call_args[1]['initiated_by'] == 'update'


def test_skipped_dataset_is_reported_not_hidden(fake_app, capsys):
    """A bundle that applies one dataset and skips another must say so — silence
    would read as full success."""
    fake_app.reference_data_service.apply_bundle.return_value = _result(datasets=[
        SimpleNamespace(name='mac_vendor_ranges', rows_before=0, rows_after=52000,
                        applied=True, skipped_reason=None),
        SimpleNamespace(name='os_eol_data', rows_before=7700, rows_after=7700,
                        applied=False, skipped_reason='already current'),
    ])
    assert _run(['--latest'], fake_app) == apply_cli.EXIT_OK
    out = capsys.readouterr().out
    assert 'os_eol_data skipped (already current)' in out
