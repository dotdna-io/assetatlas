# SPDX-License-Identifier: AGPL-3.0-or-later
"""Abstract base class for pipeline stages."""

import logging
import time
from abc import ABC, abstractmethod

from src.modules.asset_generation.pipeline.context import PipelineContext

pipeline_timer = logging.getLogger("pipeline.timing")


class PipelineStage(ABC):
    """
    Base class for all pipeline stages.

    Each stage:
    1. Receives a PipelineContext
    2. Reads what it needs from the context
    3. Does its work
    4. Writes its results back to the context
    5. Returns the context
    """

    # Non-critical post-persistence stages set this True. The orchestrator runs
    # them AFTER the core asset transaction commits and outside its scope, so a
    # caught error or a slow external call in them can neither roll back the
    # committed run nor hold the post-TRUNCATE lock (#813). Default: a stage is
    # part of the atomic asset transaction.
    post_commit: bool = False

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable stage name for logging."""

    @property
    @abstractmethod
    def key(self) -> str:
        """Machine key for stats dict (e.g. 'preprocessing')."""

    @property
    def stage_label(self) -> str:
        """Label sent to on_stage_change callback. Override if different from name."""
        return self.name

    def should_skip(self, ctx: PipelineContext) -> bool:
        """Return True to skip this stage. Default: never skip."""
        return False

    @abstractmethod
    def _execute(self, ctx: PipelineContext) -> None:
        """Stage implementation. Mutate ctx in-place."""

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        """Run the stage with timing, logging, and skip support."""
        logger = logging.getLogger(self.__class__.__module__)

        if self.should_skip(ctx):
            logger.info(f"\n[{self.name}] Skipped")
            ctx.stats['stages'][self.key] = {'skipped': True}
            return ctx

        logger.info(f"\n[{self.name}]")
        ctx.notify_stage(self.stage_label)

        # Record the stage we're entering BEFORE running it: if _execute raises,
        # the orchestrator's failure log (pipeline_orchestrator.py:282) must
        # attribute the failure to THIS stage, not the previous one that
        # completed. (#681 latent bug)
        ctx.stats['last_stage'] = self.key

        start = time.time()
        self._execute(ctx)
        duration = time.time() - start

        # Auto-record duration. Stages can add extra keys via direct dict access
        # but we always ensure duration is present.
        if self.key not in ctx.stats['stages']:
            ctx.stats['stages'][self.key] = {}
        ctx.stats['stages'][self.key]['duration'] = duration
        ctx.stats['stages'][self.key]['label'] = self.stage_label

        # Emit structured timing line
        elapsed = time.time() - ctx.pipeline_wall_start
        pipeline_timer.warning(
            "STAGE DONE | stage=%s | dur=%.2fs | elapsed=%.1fs",
            self.key, duration, elapsed,
        )

        return ctx
