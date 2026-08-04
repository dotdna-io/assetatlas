# SPDX-License-Identifier: AGPL-3.0-or-later
from src.modules.integrations.models.integration import Integration


class TestSourceMethodField:
    def test_default_source_method(self):
        integration = Integration(
            name='Test', integration_type='crowdstrike',
            category='XDR/EDR', config={}, field_mapping={},
        )
        assert integration.source_method == 'direct'

    def test_source_method_in_to_dict(self):
        integration = Integration(
            name='Test', integration_type='crowdstrike',
            category='XDR/EDR', config={}, field_mapping={},
            source_method='splunk',
        )
        d = integration.to_dict()
        assert d['source_method'] == 'splunk'

    def test_source_method_from_db_row(self):
        row = {
            'name': 'Test', 'integration_type': 'crowdstrike',
            'category': 'XDR/EDR', 'config': {}, 'field_mapping': {},
            'source_method': 'log_analytics',
        }
        integration = Integration.from_db_row(row)
        assert integration.source_method == 'log_analytics'

    def test_source_method_from_db_row_default(self):
        row = {
            'name': 'Test', 'integration_type': 'crowdstrike',
            'category': 'XDR/EDR', 'config': {}, 'field_mapping': {},
        }
        integration = Integration.from_db_row(row)
        assert integration.source_method == 'direct'


class TestPreserveFields:
    """preserve_fields is the wizard's authoritative DSS-vs-mapping
    source-of-truth (#809). It must round-trip through the model so an
    edit-mode load can classify framework-named DSS fields correctly."""

    def test_default_preserve_fields(self):
        integration = Integration(
            name='Test', integration_type='crowdstrike',
            category='XDR/EDR', config={}, field_mapping={},
        )
        assert integration.preserve_fields == []

    def test_preserve_fields_in_to_dict(self):
        integration = Integration(
            name='Test', integration_type='crowdstrike',
            category='XDR/EDR', config={}, field_mapping={'os': 'os'},
            preserve_fields=['os', 'custom_tag'],
        )
        d = integration.to_dict()
        assert d['preserve_fields'] == ['os', 'custom_tag']

    def test_preserve_fields_default_in_to_dict(self):
        integration = Integration(
            name='Test', integration_type='crowdstrike',
            category='XDR/EDR', config={}, field_mapping={},
        )
        assert integration.to_dict()['preserve_fields'] == []

    def test_preserve_fields_from_db_row(self):
        row = {
            'name': 'Test', 'integration_type': 'crowdstrike',
            'category': 'XDR/EDR', 'config': {}, 'field_mapping': {'os': 'os'},
            'preserve_fields': ['os', 'user'],
        }
        integration = Integration.from_db_row(row)
        assert integration.preserve_fields == ['os', 'user']

    def test_preserve_fields_from_db_row_default(self):
        # Legacy rows (pre-migration-124) have no preserve_fields column value.
        row = {
            'name': 'Test', 'integration_type': 'crowdstrike',
            'category': 'XDR/EDR', 'config': {}, 'field_mapping': {},
        }
        integration = Integration.from_db_row(row)
        assert integration.preserve_fields == []
