# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Tests for the veto on the SN-conflict split.

_detect_and_fix_sn_conflicts splits an asset carrying two distinct serials —
two serials on one asset means two physical devices got merged. It skips the
split when are_sns_similar() reads every pair as one device spelled two ways.

These tests pin which serials earn that veto, end-to-end through the service.
The veto is the last thing standing between a wrong merge and a permanent one:
nothing downstream re-checks it, so a serial pair wrongly called "similar" is a
wrong merge that no run will ever undo (#995).
"""

import pandas as pd
from unittest.mock import Mock

from src.modules.asset_generation.services.asset_generation_service import (
    AssetGenerationService,
)


class TestSNConflictSplitVeto:
    """Test which serial pairs stop _detect_and_fix_sn_conflicts from splitting."""

    def _make_service(self):
        """Create an AssetGenerationService with mocked DB dependencies."""
        mock_db = Mock()
        mock_db.execute_query.return_value = []
        mock_db.conn_manager.is_scope_active.return_value = False
        return AssetGenerationService(db_adapter=mock_db)

    def _merge_two_rows_via_shared_hostname(self, service, sn1, sn2):
        """Build the post-ID-assignment pipeline state for two rows merged on a hostname.

        A shared hostname is one of this repo's documented over-merge routes.
        Everything but the two serials is held constant, so a test that changes
        only sn1/sn2 changes only the veto decision.
        """
        # _detect_and_fix_sn_conflicts discards suspicious serials BEFORE consulting
        # the veto — it only runs when 2+ survive. A fixture serial that starts
        # failing this filter would leave "stayed merged" passing for the wrong
        # reason, so pin the precondition rather than let the test go quietly hollow.
        for sn in (sn1, sn2):
            assert not service.validator.is_sn_suspicious(sn), (
                f"Fixture {sn} is filtered as suspicious, so it never reaches "
                "are_sns_similar and this test would assert nothing"
            )

        # Physical (non-virtual) MACs, so get_all_mappings() includes them.
        components_row1 = ['shared-host', '00:1a:2b:3c:4d:5e', f'sn:{sn1.lower()}']
        components_row2 = ['shared-host', '3c:22:fb:aa:bb:cc', f'sn:{sn2.lower()}']

        first_id = service.identifier.group_components(components_row1)
        merged_id = service.identifier.group_components(components_row2)
        assert first_id == merged_id, "Should merge via shared hostname"

        return pd.DataFrame({
            'id': [merged_id, merged_id],
            'sn': [sn1, sn2],
            'hostname': ['shared-host', 'shared-host'],
            'mac': ['00:1A:2B:3C:4D:5E', '3C:22:FB:AA:BB:CC'],
            '_parsed_components': [components_row1, components_row2],
        })

    def test_batch_sequential_serials_are_split(self):
        """Two machines off one production line must be split back apart.

        Buy 50 identical laptops and their service tags are sequential. If any
        two of them merge, this split is the only thing that catches it. Reading
        `5CG8123ABC` and `5CG8123ABD` as a typo of each other made that
        impossible — the over-merge became permanent and silent (#995).
        """
        service = self._make_service()
        df = self._merge_two_rows_via_shared_hostname(service, '5CG8123ABC', '5CG8123ABD')

        conflicts_fixed = service._detect_and_fix_sn_conflicts(df)

        assert conflicts_fixed == 1, (
            "Sequential service tags are two devices; the split must run"
        )
        assert len(df['id'].unique()) == 2, "Expected the asset to split in two"

    def test_confusable_typo_serials_stay_merged(self):
        """One device whose serial two sources spell differently must stay one asset.

        Identical setup to the test above — only the serials differ. `5CG8123ABO`
        vs `5CG8123AB0` is someone reading an O for a 0 off the label. Splitting
        here would invent a device that does not exist, which is exactly what the
        veto is for and why #995 narrowed it rather than deleting it.
        """
        service = self._make_service()
        df = self._merge_two_rows_via_shared_hostname(service, '5CG8123ABO', '5CG8123AB0')

        conflicts_fixed = service._detect_and_fix_sn_conflicts(df)

        assert conflicts_fixed == 0, (
            "O/0 is a misread label, not a second device; the split must be vetoed"
        )
        assert len(df['id'].unique()) == 1, "Expected the asset to stay merged"
