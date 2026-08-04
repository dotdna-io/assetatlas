# SPDX-License-Identifier: AGPL-3.0-or-later
"""Apply a reference-data bundle without going through the HTTP API.

Run inside the api container:

    python -m src.modules.reference_data.apply --latest
    python -m src.modules.reference_data.apply --stdin < bundle.tar.gz

Exists so the installer can seed mac_vendor_ranges and os_eol_data at install
time. A fresh install otherwise lands with ZERO mac_vendor_ranges rows — there is
no COPY block for it in schema-full.sql and the migrations that touch it are
DDL-only — so MAC-vendor enrichment silently resolves nothing until an admin
happens to find Settings > Data > Reference Data.

Deliberately a thin shim over ReferenceDataService.apply_bundle(), the same call
the blueprint makes: that is where sha256 verification, app-compat checks and the
single-transaction apply live. Reimplementing any of it here would create a
second path that drifts.

Why not call the HTTP API from the installer instead: the endpoints are
@jwt_required + @require_admin, so it would have to log in as the admin account
it has just seeded, and that account may carry TOTP. Running in-process sidesteps
authentication entirely, and follows the precedent already set by install.sh
shelling into this container to bcrypt the admin password.

Exit codes:
    0  applied successfully
    1  failed (bad bundle, DB error, bad arguments)
    2  upstream unavailable — no network, or the bundle repo is unreachable.
       Distinct because it is the expected outcome on an air-gapped host and the
       caller should treat it as skippable rather than broken.
"""

import argparse
import logging
import sys

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UPSTREAM_UNAVAILABLE = 2


def _build_source(args):
    """Import lazily: these pull in the app package, and --help should work even
    if the runtime config is incomplete."""
    from src.api.blueprints.reference_data import BUNDLE_REPO
    from src.modules.reference_data.sources import (
        GitHubReleaseSource,
        UploadedBytesSource,
    )

    if args.stdin:
        data = sys.stdin.buffer.read()
        if not data:
            raise ValueError("no bundle bytes on stdin")
        return UploadedBytesSource(data=data, uploaded_filename=args.label or 'bundled')
    return GitHubReleaseSource(repo=BUNDLE_REPO, version=args.version)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='python -m src.modules.reference_data.apply',
        description='Apply a reference-data bundle (mac_vendor_ranges, os_eol_data).',
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--latest', action='store_true',
                       help='fetch the newest bundle from the public bundle repo')
    group.add_argument('--stdin', action='store_true',
                       help='read a bundle tarball from stdin (air-gapped path)')
    parser.add_argument('--version', default=None,
                        help='with --latest, apply this specific bundle version')
    parser.add_argument('--label', default=None,
                        help='with --stdin, a filename to record in the history')
    parser.add_argument('--initiated-by', default='installer',
                        help='recorded in the apply history (default: installer)')
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format='%(message)s')

    from src.api.factory import create_app
    from src.modules.reference_data.sources import UpstreamUnavailableError

    app = create_app()
    with app.app_context():
        try:
            source = _build_source(args)
        except ValueError as e:
            print(f'reference-data: {e}', file=sys.stderr)
            return EXIT_FAILED

        try:
            result = app.reference_data_service.apply_bundle(
                source, initiated_by=args.initiated_by,
            )
        except UpstreamUnavailableError as e:
            # Expected on an air-gapped host. Not an error to shout about.
            print(f'reference-data: upstream unavailable ({e})', file=sys.stderr)
            return EXIT_UPSTREAM_UNAVAILABLE

        if result.status != 'success':
            print(f'reference-data: bundle {result.bundle_version} rejected: '
                  f'{result.error_message}', file=sys.stderr)
            return EXIT_FAILED

        # Read the model's real fields directly rather than via getattr defaults:
        # a renamed field should break loudly here, not silently report "0 rows"
        # and leave an operator believing reference data landed when it did not.
        parts = []
        for d in result.datasets:
            if d.applied:
                parts.append(f'{d.name} {d.rows_before}->{d.rows_after}')
            else:
                parts.append(f'{d.name} skipped ({d.skipped_reason or "no reason given"})')
        print(f'reference-data: applied bundle {result.bundle_version} '
              f'[{"; ".join(parts) or "no datasets"}]')
        return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
