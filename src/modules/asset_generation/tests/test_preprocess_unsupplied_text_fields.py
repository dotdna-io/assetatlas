# SPDX-License-Identifier: AGPL-3.0-or-later
"""preprocess() must not fabricate 0.0 into text fields no source supplied.

preprocess() lifts owner/location/user_field/... out of extended_fields. When
one of those keys has values somewhere in the batch (ServiceNow's location)
and another has none at all (user_field), pandas infers float64 for the
all-missing column, and the numeric zero-fill turned it into 0.0. The
aggregator then stringified it, so every asset read user_field = {0.0} —
and owner = {0.0} too, until an owner-mapping source synced.
"""
import pandas as pd

from src.modules.asset_generation.domain.asset_enricher import AssetEnricher


def _raw_rows():
    return pd.DataFrame([
        {'hostname': ['ws-01'], 'ip': None, 'mac': None, 'fqdn': None,
         'sn': ['SN-1'], 'os': 'Windows', 'manufacturer': None, 'model': None,
         'vendor_category': None, 'data_source': 'ServiceNow',
         'extended_fields': {'location': 'Melville'}},
        {'hostname': ['ws-02'], 'ip': None, 'mac': None, 'fqdn': None,
         'sn': ['SN-2'], 'os': 'Windows', 'manufacturer': None, 'model': None,
         'vendor_category': None, 'data_source': 'Corp SentinelOne',
         'extended_fields': {'siteId': '7'}},
    ])


def test_unsupplied_text_fields_stay_missing():
    out = AssetEnricher().preprocess(_raw_rows())

    assert out['user_field'].isna().all(), out['user_field'].tolist()
    assert out['owner'].isna().all(), out['owner'].tolist()


def test_mapped_user_lands_in_user_field():
    """FieldMapper → raw row → preprocess: the full handoff for `user`."""
    from src.modules.integrations.services.field_mapper import FieldMapper

    mapped = FieldMapper(
        {'hostname': 'computerName', 'user': 'lastLoggedInUserName', 'siteId': 'siteId'}
    ).map({'computerName': 'WS-01', 'lastLoggedInUserName': 'julie.auger', 'siteId': '7'})
    raw = {k: mapped.get(k) for k in (
        'hostname', 'ip', 'mac', 'fqdn', 'sn', 'os', 'manufacturer', 'model',
        'vendor_category', 'extended_fields')}
    raw['data_source'] = 'Corp SentinelOne'

    out = AssetEnricher().preprocess(pd.DataFrame([raw]))

    assert out['user_field'].tolist() == ['julie.auger']
    assert out['extended_fields'].iloc[0] == {'siteId': '7'}


def test_numeric_text_value_is_kept_verbatim_and_missing_stays_missing():
    """A JSON number in a text field must not become 7.0, nor its gaps 0.0."""
    rows = _raw_rows()
    rows.at[0, 'extended_fields'] = {'location': 7}

    out = AssetEnricher().preprocess(rows)

    assert str(out['location'].iloc[0]) == '7'
    assert pd.isna(out['location'].iloc[1])


def test_user_lift_fills_rows_a_direct_user_field_left_empty():
    """One source maps a custom `user_field`, another the framework `user`."""
    rows = _raw_rows()
    rows.at[0, 'extended_fields'] = {'user_field': 'svc-owner'}
    rows.at[1, 'extended_fields'] = {'user': 'julie.auger'}

    out = AssetEnricher().preprocess(rows)

    assert out['user_field'].tolist() == ['svc-owner', 'julie.auger']


def test_supplied_text_field_keeps_its_values():
    out = AssetEnricher().preprocess(_raw_rows())

    assert out['location'].iloc[0] == 'Melville'
    assert pd.isna(out['location'].iloc[1])
