# SPDX-License-Identifier: AGPL-3.0-or-later
from src.modules.assets.services.asset_service import decode_merge_component


def test_decode():
    assert decode_merge_component("sn:abc123") == ("serial", "abc123")
    assert decode_merge_component("bc:24:11:22:33:44") == ("mac", "bc:24:11:22:33:44")
    assert decode_merge_component("web01.corp.example") == ("fqdn", "web01.corp.example")
    assert decode_merge_component("web01") == ("hostname", "web01")
