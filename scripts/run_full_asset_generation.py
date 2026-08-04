#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Full Asset Generation Run

Runs the complete asset generation pipeline using the latest completed batch
from each enabled integration. Uses the same code path as the UI trigger.

Features:
- Loads latest completed batch per enabled integration
- Generates unique generation_run_id
- Archives previous assets to history
- Full pipeline execution with change tracking
- Comprehensive logging and statistics
"""

import logging
import os
import sys
from pathlib import Path
from datetime import datetime
import time

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.postgres_connection import get_connection_manager
from src.database.postgres_adapter import PostgreSQLAdapter
from src.modules.integrations.services.asset_generation_trigger_service import AssetGenerationTriggerService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).parent.parent / 'logs' / f'full_generation_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
        )
    ]
)
logger = logging.getLogger(__name__)


def main():
    """Execute full asset generation using the trigger service."""

    logger.info("=" * 80)
    logger.info("FULL ASSET GENERATION - LATEST BATCHES FROM ENABLED INTEGRATIONS")
    logger.info("=" * 80)

    # Verify database connection
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        logger.error("DATABASE_URL environment variable not set!")
        sys.exit(1)

    logger.info(f"Database: {database_url.split('@')[1] if '@' in database_url else 'configured'}")

    try:
        start_time = time.time()

        # Initialize database
        logger.info("\n[1/3] Initializing database connection...")
        conn_manager = get_connection_manager()
        db_adapter = PostgreSQLAdapter(conn_manager)

        # Initialize trigger service (same code path as UI)
        logger.info("\n[2/3] Initializing asset generation trigger service...")
        trigger_service = AssetGenerationTriggerService(db_adapter=db_adapter)

        # Run asset generation via trigger service
        logger.info("\n[3/3] Running asset generation pipeline...")
        result = trigger_service.generate_assets_for_latest_batches()

        total_duration = time.time() - start_time

        # Print results
        logger.info("\n" + "=" * 80)
        if result.get('success'):
            logger.info("FULL ASSET GENERATION COMPLETED")
        else:
            logger.info("FULL ASSET GENERATION FAILED")
        logger.info("=" * 80)

        logger.info(f"Success:              {result.get('success')}")
        logger.info(f"Batches processed:    {result.get('batches_processed', 0)}")
        logger.info(f"Batches failed:       {result.get('batches_failed', 0)}")
        logger.info(f"Assets created:       {result.get('total_assets_created', 0):,}")
        logger.info(f"Assets updated:       {result.get('total_assets_updated', 0):,}")
        logger.info(f"Duration:             {result.get('duration_seconds', total_duration):.2f}s")

        if result.get('error'):
            logger.error(f"Error:                {result['error']}")

        if result.get('batch_results'):
            logger.info("\nPer-batch details:")
            for br in result['batch_results']:
                logger.info(
                    f"  Batch {br['batch_id']}: "
                    f"{br.get('input_records', 0):,} records -> "
                    f"{br.get('assets_created', 0):,} assets"
                )

        # Verify results in database
        logger.info("\n" + "=" * 80)
        logger.info("VERIFYING DATABASE STATE")
        logger.info("=" * 80)

        current_assets = db_adapter.execute_query(
            "SELECT COUNT(*) as count FROM assets",
            (),
            fetch=True
        )[0]['count']

        history_assets = db_adapter.execute_query(
            "SELECT COUNT(*) as count FROM assets_history",
            (),
            fetch=True
        )[0]['count']

        logger.info(f"Current assets table: {current_assets:,} assets (latest generation)")
        logger.info(f"History assets table: {history_assets:,} assets (all past snapshots)")

        logger.info("\n" + "=" * 80)
        if result.get('success'):
            logger.info("SUCCESS - Full asset generation completed!")
        else:
            logger.info("FAILED - Check logs for details")
        logger.info("=" * 80)

        if not result.get('success'):
            sys.exit(1)

    except Exception as e:
        logger.error(f"Asset generation failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
