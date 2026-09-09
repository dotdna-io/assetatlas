# SPDX-License-Identifier: AGPL-3.0-or-later
"""The app factory wires the shared field-catalog version stamp (#1413).

The app-scoped FieldCatalog (built once per worker in ``_init_services``)
must poll the shared Redis version stamp so a unified-field edit on any
worker becomes visible everywhere within the stamp-check interval. Under
TestConfig there is no REDIS_URL, so the source degrades to "unavailable"
and the catalog keeps its legacy TTL behaviour — construction must still
wire it and must not raise.
"""

from unittest.mock import MagicMock, patch

from flask import Flask

from src.api.config import TestConfig
from src.api.factory import _init_services
from src.modules.fields import catalog_version


def test_init_services_wires_the_shared_version_source():
    app = Flask(__name__)
    app.config.from_object(TestConfig)
    app.db_adapter = MagicMock()
    app.connection_manager = MagicMock()

    # The connector registry builds its own technology repository off
    # DATABASE_URL and would otherwise retry real connections with backoff.
    with patch(
        "src.modules.integrations.repositories.technology_repository"
        ".IntegrationTechnologyRepository"
    ):
        _init_services(app, should_log=False)

    assert app.field_resolver.catalog()._version_source is (
        catalog_version.default_source()
    )
