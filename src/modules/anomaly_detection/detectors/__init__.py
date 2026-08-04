# SPDX-License-Identifier: AGPL-3.0-or-later
"""Detector registry: detector_key -> Detector class."""
from src.modules.anomaly_detection.detectors.behavior import (
    AssetChangeBurstDetector,
    AssetThrashDetector,
    IdentitySwapDetector,
    PresenceFlapDetector,
)
from src.modules.anomaly_detection.detectors.coverage import (
    SecuritySourceLossDetector,
    UnprotectedNewcomerDetector,
)
from src.modules.anomaly_detection.detectors.ml import (
    MlBehaviorDetector,
    MlNewcomerDetector,
)
from src.modules.anomaly_detection.detectors.population import (
    FirstSeenValueDetector,
    SegmentOutlierDetector,
)
from src.modules.anomaly_detection.detectors.volume import (
    AttributeChurnDetector,
    ConfidenceShiftDetector,
    IdentityChurnDetector,
    MassMissingDetector,
    MassNewAssetsDetector,
    SourceVolumeDetector,
)

DETECTOR_CLASSES = {
    MassNewAssetsDetector.key: MassNewAssetsDetector,
    MassMissingDetector.key: MassMissingDetector,
    SourceVolumeDetector.key: SourceVolumeDetector,
    AttributeChurnDetector.key: AttributeChurnDetector,
    IdentityChurnDetector.key: IdentityChurnDetector,
    ConfidenceShiftDetector.key: ConfidenceShiftDetector,
    AssetChangeBurstDetector.key: AssetChangeBurstDetector,
    AssetThrashDetector.key: AssetThrashDetector,
    PresenceFlapDetector.key: PresenceFlapDetector,
    IdentitySwapDetector.key: IdentitySwapDetector,
    SegmentOutlierDetector.key: SegmentOutlierDetector,
    FirstSeenValueDetector.key: FirstSeenValueDetector,
    SecuritySourceLossDetector.key: SecuritySourceLossDetector,
    UnprotectedNewcomerDetector.key: UnprotectedNewcomerDetector,
    MlNewcomerDetector.key: MlNewcomerDetector,
    MlBehaviorDetector.key: MlBehaviorDetector,
}
