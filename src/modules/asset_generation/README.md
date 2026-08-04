# Asset Generation

> **Last Updated:** 2026-02-15

Core asset management system with UnionFind algorithm for intelligent asset identification.

## Quick Usage

```bash
# Run full asset generation pipeline
DATABASE_URL="postgresql://user:password@host/database" python ../scripts/run_full_asset_generation.py
```

## Directory Structure

```
asset_generation/
├── domain/                    # Business logic and domain models
│   ├── asset_identifier.py    # UnionFind algorithm for asset grouping
│   ├── asset_validator.py     # Validation and exclusion logic
│   ├── asset_enricher.py      # OS normalization and MAC vendor enrichment
│   ├── asset_scorer.py        # Confidence scoring (V2 Gates+Gradients)
│   ├── asset_classifier.py    # ML-based category prediction
│   ├── conflict_detector.py   # Conflict detection
│   ├── os_selector.py         # OS selection for aggregated assets
│   ├── vendor_validators.py   # Vendor-specific SN format validation
│   ├── os_eol_enricher.py     # OS End-of-Life enrichment
│   ├── sn_validation_enhanced.py  # Enhanced SN quality detection
│   ├── category_prediction.py # Rule-based category prediction
│   └── os_normalization/      # OS normalization module
├── repositories/              # Data access layer
│   ├── asset_repository.py
│   ├── mapping_repository.py
│   ├── conflict_repository.py
│   ├── excluded_records_repository.py
│   ├── category_override_repository.py
│   └── provider_repository.py
├── services/                  # Application services
│   ├── asset_generation_service.py  # Main pipeline orchestrator
│   ├── chronicle_service.py         # Change tracking
│   ├── eol_data_service.py         # EOL data access
│   └── chronicle_query_helpers.py
└── tests/                     # All tests for asset generation
    ├── test_asset_identifier.py
    ├── test_asset_validator.py
    ├── test_asset_scorer.py
    └── ... (11 test files total)
```

## Core Features

- **Asset ID Assignment** - Groups related assets using UnionFind algorithm
- **Confidence Scoring** - Multi-factor scoring for asset reliability
- **Data Enrichment** - OS normalization, MAC vendor lookup, category prediction
- **ML Classification** - Optional ML-based category prediction
- **Rule-Based Prediction** - Pattern-based category prediction (default)
- **Conflict Detection** - Identifies suspicious asset patterns
- **Change Tracking** - Full audit trail via Chronicle service