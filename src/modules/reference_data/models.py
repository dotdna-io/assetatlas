# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pydantic models for reference-data bundle manifest and apply results."""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class DatasetEntry(BaseModel):
    name: Literal['mac_vendor_ranges', 'os_eol_data']
    payload: str
    sha256: str
    row_count: int
    source: str
    apply_strategy: Literal['truncate_replace', 'upsert_by_product_cycle']


class AppCompat(BaseModel):
    min: str  # CalVer / SemVer-like dotted-int string


class Manifest(BaseModel):
    format_version: int = Field(ge=1, le=1)  # only v1 supported in this release
    bundle_version: str
    generated_at: datetime
    app_compat: AppCompat
    datasets: List[DatasetEntry]
    # No whole-bundle sha256 — per-dataset sha256s cover all payload bytes,
    # and Pydantic Literal types constrain dataset names/strategies. A signed
    # manifest (deferred to a follow-up) would cover the metadata too.


class DatasetApplyResult(BaseModel):
    name: str
    rows_before: int
    rows_after: int
    applied: bool
    skipped_reason: Optional[str] = None


class BundleApplyResult(BaseModel):
    bundle_version: str
    source: Literal['upload', 'github']
    started_at: datetime
    finished_at: datetime
    status: Literal['success', 'failed']
    datasets: List[DatasetApplyResult]
    error_message: Optional[str] = None
