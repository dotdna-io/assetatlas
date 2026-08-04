# SPDX-License-Identifier: AGPL-3.0-or-later
"""Setting definition for the asset removal grace period (#795)."""

from src.api.modules.settings.settings_schema import (
    APPLICATION_SETTINGS,
    get_default_value,
)


def _defn():
    return APPLICATION_SETTINGS['asset_generation']['removal_grace_period_days']


def test_default_is_30_days():
    assert get_default_value('asset_generation', 'removal_grace_period_days') == 30


def test_zero_is_allowed_disabled_sentinel():
    # 0 = disabled (legacy immediate removal) must be a valid value
    assert _defn().validate(0) is True


def test_range_is_0_to_365():
    d = _defn()
    assert d.validate(-1) is False
    assert d.validate(366) is False
    assert d.validate(365) is True
    assert d.validate(30) is True


def test_type_is_integer():
    d = _defn()
    assert d.type == 'integer'
    assert d.validate(1.5) is False
