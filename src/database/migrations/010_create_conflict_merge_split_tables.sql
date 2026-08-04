-- SPDX-License-Identifier: AGPL-3.0-or-later
-- Migration: Create tables for conflict detection, merge tracking, and split operations
-- Purpose: Support Phase 4 - Conflict Resolution & Merge Management
-- Created: 2025-10-27

BEGIN;

-- ============================================================================
-- CONFLICT DETECTION
-- ============================================================================

-- Table to store detected conflicts during asset generation
CREATE TABLE asset_conflicts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generation_run_id UUID NOT NULL,
    conflict_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL, -- 'critical', 'high', 'medium', 'low'
    asset_id UUID,
    component VARCHAR(255) NOT NULL,
    conflicting_asset_id UUID,
    evidence JSONB NOT NULL DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'open', -- 'open', 'resolved', 'ignored'
    resolution_strategy VARCHAR(50),
    resolved_at TIMESTAMP WITH TIME ZONE,
    resolved_by VARCHAR(255),
    resolution_notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes for conflict queries
CREATE INDEX idx_conflicts_generation_run ON asset_conflicts(generation_run_id);
CREATE INDEX idx_conflicts_asset_id ON asset_conflicts(asset_id);
CREATE INDEX idx_conflicts_status ON asset_conflicts(status);
CREATE INDEX idx_conflicts_severity ON asset_conflicts(severity);
CREATE INDEX idx_conflicts_created_at ON asset_conflicts(created_at);

COMMENT ON TABLE asset_conflicts IS 'Stores detected conflicts during asset generation for review and resolution';
COMMENT ON COLUMN asset_conflicts.conflict_type IS 'Type of conflict: same_component_different_assets, overlapping_components, network_boundary_violation, etc.';
COMMENT ON COLUMN asset_conflicts.severity IS 'Severity level: critical (same component different assets), high (overlapping), medium (adjacent), low (similar)';
COMMENT ON COLUMN asset_conflicts.evidence IS 'JSON evidence showing why this is a conflict: sources, timestamps, components involved, etc.';

-- ============================================================================
-- MERGE HISTORY & REVERSIBILITY
-- ============================================================================

-- Table to track all asset merge operations
CREATE TABLE merge_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merge_date TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    merged_from UUID[] NOT NULL, -- Array of asset IDs that were merged
    merged_into UUID NOT NULL, -- Resulting asset ID
    generation_run_id UUID,
    confidence_score FLOAT,
    evidence JSONB NOT NULL DEFAULT '{}',
    performed_by VARCHAR(255) DEFAULT 'system',
    pre_merge_snapshot JSONB, -- Snapshot of assets before merge
    reversible_until TIMESTAMP WITH TIME ZONE, -- After this date, merge cannot be reversed
    reversed_at TIMESTAMP WITH TIME ZONE,
    reversal_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes for merge history queries
CREATE INDEX idx_merge_history_merged_into ON merge_history(merged_into);
CREATE INDEX idx_merge_history_merge_date ON merge_history(merge_date);
CREATE INDEX idx_merge_history_generation_run ON merge_history(generation_run_id);
CREATE INDEX idx_merge_history_reversed_at ON merge_history(reversed_at) WHERE reversed_at IS NOT NULL;
CREATE INDEX idx_merge_history_reversible_until ON merge_history(reversible_until) WHERE reversible_until IS NOT NULL AND reversed_at IS NULL;

-- GIN index for merged_from array searches
CREATE INDEX idx_merge_history_merged_from ON merge_history USING GIN (merged_from);

COMMENT ON TABLE merge_history IS 'Tracks all asset merge operations with evidence and reversibility support';
COMMENT ON COLUMN merge_history.merged_from IS 'Array of asset IDs that were merged into one';
COMMENT ON COLUMN merge_history.merged_into IS 'The resulting asset ID after merge';
COMMENT ON COLUMN merge_history.pre_merge_snapshot IS 'JSON snapshot of all assets involved before merge for potential reversal';
COMMENT ON COLUMN merge_history.reversible_until IS 'Merge can be reversed until this timestamp (default 30 days)';

-- ============================================================================
-- SPLIT OPERATIONS
-- ============================================================================

-- Table to track asset split operations
CREATE TABLE split_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    split_date TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    split_from UUID NOT NULL, -- Original asset ID
    split_into UUID[] NOT NULL, -- Array of new asset IDs created from split
    split_reason VARCHAR(100) NOT NULL,
    component_distribution JSONB NOT NULL, -- Maps components to new asset IDs
    performed_by VARCHAR(255) NOT NULL,
    approval_required BOOLEAN DEFAULT FALSE,
    approved_by VARCHAR(255),
    approved_at TIMESTAMP WITH TIME ZONE,
    prevent_auto_remerge BOOLEAN DEFAULT TRUE,
    remerge_prevention_expires_at TIMESTAMP WITH TIME ZONE,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Indexes for split history queries
CREATE INDEX idx_split_history_split_from ON split_history(split_from);
CREATE INDEX idx_split_history_split_date ON split_history(split_date);
CREATE INDEX idx_split_history_performed_by ON split_history(performed_by);

-- GIN index for split_into array searches
CREATE INDEX idx_split_history_split_into ON split_history USING GIN (split_into);

COMMENT ON TABLE split_history IS 'Tracks all asset split operations with component redistribution';
COMMENT ON COLUMN split_history.split_reason IS 'Reason for split: manual_correction, business_unit_separation, geographic_split, compliance_requirement, etc.';
COMMENT ON COLUMN split_history.component_distribution IS 'JSON mapping of which components went to which new asset IDs';
COMMENT ON COLUMN split_history.prevent_auto_remerge IS 'If true, prevents automatic re-merge of these assets';
COMMENT ON COLUMN split_history.remerge_prevention_expires_at IS 'When the auto-remerge prevention expires (null = never)';

-- ============================================================================
-- MERGE PREVENTION RULES
-- ============================================================================

-- Table to store configurable merge prevention rules
CREATE TABLE merge_prevention_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    rule_type VARCHAR(50) NOT NULL, -- 'network_boundary', 'environment_separation', 'geographic', 'business_unit', 'custom'
    rule_definition JSONB NOT NULL, -- Rule logic in JSON format
    enabled BOOLEAN DEFAULT TRUE,
    priority INTEGER DEFAULT 50, -- 1-100, higher = evaluated first
    created_by VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_evaluated_at TIMESTAMP WITH TIME ZONE,
    evaluation_count INTEGER DEFAULT 0,
    violation_count INTEGER DEFAULT 0
);

-- Indexes for rule queries
CREATE INDEX idx_merge_rules_enabled ON merge_prevention_rules(enabled) WHERE enabled = TRUE;
CREATE INDEX idx_merge_rules_priority ON merge_prevention_rules(priority DESC);
CREATE INDEX idx_merge_rules_rule_type ON merge_prevention_rules(rule_type);

COMMENT ON TABLE merge_prevention_rules IS 'Configurable rules to prevent inappropriate asset merges';
COMMENT ON COLUMN merge_prevention_rules.rule_type IS 'Category of rule: network_boundary, environment_separation, geographic, business_unit, custom';
COMMENT ON COLUMN merge_prevention_rules.rule_definition IS 'JSON definition of the rule logic: conditions, thresholds, exceptions';
COMMENT ON COLUMN merge_prevention_rules.priority IS 'Evaluation priority: 1-100, higher numbers evaluated first';

-- ============================================================================
-- SPLIT PREVENTION (to prevent auto-remerge after manual split)
-- ============================================================================

-- Table to store split asset pairs that should not be auto-merged
CREATE TABLE split_prevention (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id_1 UUID NOT NULL,
    asset_id_2 UUID NOT NULL,
    split_history_id UUID REFERENCES split_history(id),
    created_by VARCHAR(255) NOT NULL,
    reason TEXT,
    expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_split_pair UNIQUE (asset_id_1, asset_id_2)
);

-- Indexes for fast lookups
CREATE INDEX idx_split_prevention_asset_1 ON split_prevention(asset_id_1);
CREATE INDEX idx_split_prevention_asset_2 ON split_prevention(asset_id_2);
CREATE INDEX idx_split_prevention_expires_at ON split_prevention(expires_at) WHERE expires_at IS NOT NULL;

COMMENT ON TABLE split_prevention IS 'Prevents automatic re-merge of manually split assets';
COMMENT ON COLUMN split_prevention.expires_at IS 'When the prevention expires (null = never expires)';

COMMIT;
