# SPDX-License-Identifier: AGPL-3.0-or-later
"""
AssetAtlas Backend API Module.

The Flask app is built via the factory pattern in :mod:`src.api.factory`
(``create_app()``).

Usage:
    # Development (Flask dev server)
    flask --app "src.api.factory:create_app()" run

    # Production (gunicorn — note the parentheses, factory syntax)
    gunicorn -w 4 -b 0.0.0.0:5001 "src.api.factory:create_app()"

    # Or via the unified startup script
    ./scripts/start.sh          # development
    ./scripts/start.sh prod     # production
"""
