-- SPDX-License-Identifier: AGPL-3.0-or-later
--
-- PostgreSQL database dump
--


-- Dumped from database version 16.6
-- Dumped by pg_dump version 18.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'Migration 015: Dropped invalid_assets table (2025-10-29). Excluded records now tracked only in excluded_records table.';


--
-- Name: timescaledb; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS timescaledb WITH SCHEMA public;


--
-- Name: EXTENSION timescaledb; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION timescaledb IS 'Enables scalable inserts and complex queries for time-series data (Community Edition)';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: asset_search_join(text[]); Type: FUNCTION; Schema: public; Owner: -
-- IMMUTABLE wrapper around array_to_string(arr, ' ') so the Explorer keyword
-- search expression (idx_assets_search_trgm) is indexable. array_to_string is
-- only STABLE; joining a text[] with a constant separator is deterministic.
--

CREATE FUNCTION public.asset_search_join(text[]) RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $_$ SELECT array_to_string($1, ' ') $_$;


--
-- Name: cleanup_old_batches(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cleanup_old_batches() RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM ingestion_batches
    WHERE completed_at < NOW() - INTERVAL '30 days'
    AND status = 'completed';

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;


--
-- Name: cleanup_old_job_executions(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cleanup_old_job_executions() RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM job_executions
    WHERE started_at < NOW() - INTERVAL '90 days'
    AND status != 'running';  -- Don't delete running jobs even if old (might be stuck)

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    -- Log the cleanup
    RAISE NOTICE 'Deleted % old job execution records', deleted_count;

    RETURN deleted_count;
END;
$$;


--
-- Name: cleanup_raw_asset_data(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cleanup_raw_asset_data() RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM raw_asset_data
    WHERE ingested_at < NOW() - INTERVAL '7 days';

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;


--
-- Name: cleanup_raw_source_data(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cleanup_raw_source_data() RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM raw_source_data
    WHERE ingested_at < NOW() - INTERVAL '7 days';

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;


--
-- Name: get_job_health_status(character varying); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.get_job_health_status(p_job_id character varying) RETURNS TABLE(health_status character varying, recent_failures integer, success_rate numeric, avg_duration numeric, last_success timestamp with time zone, last_failure timestamp with time zone, is_stuck boolean)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        CASE
            WHEN recent_failures > 5 THEN 'critical'
            WHEN recent_failures > 2 THEN 'warning'
            WHEN last_exec_status = 'running' AND
                 EXTRACT(EPOCH FROM (NOW() - last_exec_started)) > 3600 THEN 'stuck'
            ELSE 'healthy'
        END::VARCHAR as health_status,
        COALESCE(recent_failures, 0)::INTEGER,
        COALESCE(recent_success_rate, 0)::NUMERIC,
        COALESCE(recent_avg_duration, 0)::NUMERIC,
        recent_last_success,
        recent_last_failure,
        (last_exec_status = 'running' AND
         EXTRACT(EPOCH FROM (NOW() - last_exec_started)) > 3600)::BOOLEAN as is_stuck
    FROM (
        SELECT
            COUNT(*) FILTER (WHERE status = 'failed' AND started_at > NOW() - INTERVAL '24 hours') as recent_failures,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE status = 'success') / NULLIF(COUNT(*), 0),
                2
            ) as recent_success_rate,
            AVG(duration_seconds) FILTER (WHERE status = 'success') as recent_avg_duration,
            MAX(started_at) FILTER (WHERE status = 'success') as recent_last_success,
            MAX(started_at) FILTER (WHERE status = 'failed') as recent_last_failure,
            (SELECT status FROM job_executions WHERE job_id = p_job_id ORDER BY started_at DESC LIMIT 1) as last_exec_status,
            (SELECT started_at FROM job_executions WHERE job_id = p_job_id ORDER BY started_at DESC LIMIT 1) as last_exec_started
        FROM job_executions
        WHERE job_id = p_job_id
        AND started_at > NOW() - INTERVAL '7 days'
    ) stats;
END;
$$;


--
-- Name: set_posture_sub_rule_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_posture_sub_rule_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


--
-- Name: update_field_registry_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_field_registry_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_reports_timestamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_reports_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_rule_execution_stats(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_rule_execution_stats() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE tag_rules
    SET
        last_executed_at = NEW.executed_at,
        execution_count = execution_count + 1,
        last_execution_duration_ms = NEW.execution_time_ms,
        last_execution_assets_matched = NEW.assets_matched,
        last_execution_assets_passed = NEW.assets_passed,
        last_execution_assets_failed = NEW.assets_failed
    WHERE id = NEW.rule_id;

    RETURN NEW;
END;
$$;


--
-- Name: update_settings_timestamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_settings_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_system_prefs_timestamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_system_prefs_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.last_modified = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_tag_rule_modified(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_tag_rule_modified() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.last_modified = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_updated_at_column(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: update_user_prefs_timestamp(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_user_prefs_timestamp() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.last_modified = NOW();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alert_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_rules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    type text NOT NULL,
    severity text NOT NULL,
    description text NOT NULL,
    query text,
    threshold jsonb,
    enabled boolean DEFAULT true NOT NULL,
    source text DEFAULT 'system'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    target_filters jsonb,
    compliance_criteria jsonb,
    silence_duration integer,
    created_by uuid,
    evaluation_type text DEFAULT 'sql'::text NOT NULL,
    detector_key text,
    evaluation_cadence text DEFAULT 'pipeline'::text NOT NULL,
    subject_type text DEFAULT 'asset'::text NOT NULL,
    CONSTRAINT alert_rules_cadence_check CHECK ((evaluation_cadence = ANY (ARRAY['pipeline'::text, 'interval'::text]))),
    CONSTRAINT alert_rules_evaluation_type_check CHECK ((evaluation_type = ANY (ARRAY['sql'::text, 'filter'::text, 'external'::text, 'anomaly'::text]))),
    CONSTRAINT alert_rules_path_check CHECK ((((evaluation_type = 'sql'::text) AND (query IS NOT NULL) AND (target_filters IS NULL) AND (compliance_criteria IS NULL)) OR ((evaluation_type = 'filter'::text) AND (query IS NULL) AND ((target_filters IS NOT NULL) OR (compliance_criteria IS NOT NULL))) OR ((evaluation_type = 'external'::text) AND (query IS NULL) AND (target_filters IS NULL) AND (compliance_criteria IS NULL)) OR ((evaluation_type = 'anomaly'::text) AND (query IS NULL) AND (target_filters IS NULL) AND (compliance_criteria IS NULL)))),
    CONSTRAINT alert_rules_severity_check CHECK ((severity = ANY (ARRAY['critical'::text, 'high'::text, 'medium'::text, 'low'::text]))),
    CONSTRAINT alert_rules_source_check CHECK ((source = ANY (ARRAY['system'::text, 'user'::text, 'prometheus'::text]))),
    CONSTRAINT alert_rules_subject_type_check CHECK ((subject_type = ANY (ARRAY['asset'::text, 'integration'::text, 'audit_event'::text, 'pipeline_run'::text, 'metric'::text, 'system'::text]))),
    CONSTRAINT alert_rules_type_check CHECK ((type = ANY (ARRAY['security'::text, 'compliance'::text, 'operational'::text, 'data_quality'::text])))
);


--
-- Name: TABLE alert_rules; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.alert_rules IS 'SQL-based alerting rule definitions evaluated post-pipeline';


--
-- Name: COLUMN alert_rules.slug; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert_rules.slug IS 'Unique identifier for dedup (e.g., os-eol-critical)';


--
-- Name: COLUMN alert_rules.query; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert_rules.query IS 'SQL query returning matching assets or aggregate metrics';


--
-- Name: COLUMN alert_rules.threshold; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert_rules.threshold IS 'JSONB: {metric, operator, value, unit} — current_value computed at eval time';


--
-- Name: COLUMN alert_rules.source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alert_rules.source IS 'system | user | prometheus (added 2026-04-24). For prometheus rules, query column stores PromQL.';


--
-- Name: alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alerts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    rule_id uuid NOT NULL,
    type text NOT NULL,
    severity text NOT NULL,
    title text NOT NULL,
    description text NOT NULL,
    threshold jsonb,
    status text DEFAULT 'active'::text NOT NULL,
    asset_count integer DEFAULT 0 NOT NULL,
    assets_by_category jsonb DEFAULT '{}'::jsonb NOT NULL,
    data_sources text[] DEFAULT '{}'::text[] NOT NULL,
    asset_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    source text DEFAULT 'system'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    fingerprint text,
    labels jsonb,
    evidence jsonb,
    last_pipeline_run_id uuid,
    subject_type text DEFAULT 'asset'::text NOT NULL,
    subjects jsonb,
    CONSTRAINT alerts_severity_check CHECK ((severity = ANY (ARRAY['critical'::text, 'high'::text, 'medium'::text, 'low'::text]))),
    CONSTRAINT alerts_status_check CHECK ((status = ANY (ARRAY['active'::text, 'resolved'::text, 'acknowledged'::text]))),
    CONSTRAINT alerts_type_check CHECK ((type = ANY (ARRAY['security'::text, 'compliance'::text, 'operational'::text, 'data_quality'::text])))
);


--
-- Name: TABLE alerts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.alerts IS 'Fired alert instances — denormalized for fast frontend reads';


--
-- Name: alert_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    alert_id uuid NOT NULL,
    rule_id uuid NOT NULL,
    pipeline_run_id uuid NOT NULL,
    status text NOT NULL,
    severity text NOT NULL,
    asset_count integer DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alert_channels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_channels (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    type text NOT NULL,
    name text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alert_category_channels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_category_channels (
    category text NOT NULL,
    channel_id uuid NOT NULL,
    min_severity text DEFAULT 'low'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT alert_category_channels_category_check CHECK ((category = ANY (ARRAY['security'::text, 'compliance'::text, 'operational'::text, 'data_quality'::text, 'custom'::text]))),
    CONSTRAINT alert_category_channels_min_severity_check CHECK ((min_severity = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text, 'critical'::text])))
);


--
-- Name: alert_deliveries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_deliveries (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    alert_id uuid,
    channel_id uuid NOT NULL,
    channel_type text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    next_retry_at timestamp with time zone DEFAULT now() NOT NULL,
    last_error text,
    response_code integer,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    trigger text DEFAULT 'created'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    sent_at timestamp with time zone
);


--
-- Name: metrics_forwarding_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.metrics_forwarding_config (
    id integer DEFAULT 1 NOT NULL,
    endpoint_url text,
    auth_type text DEFAULT 'none'::text NOT NULL,
    auth_secret text,
    username text,
    external_labels jsonb DEFAULT '{}'::jsonb NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by text,
    CONSTRAINT metrics_forwarding_config_auth_type CHECK ((auth_type = ANY (ARRAY['none'::text, 'bearer'::text, 'basic'::text]))),
    CONSTRAINT metrics_forwarding_config_singleton CHECK ((id = 1))
);


--
-- Name: COLUMN alerts.fingerprint; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alerts.fingerprint IS 'Alertmanager-provided stable hash of the alert''s labels. NULL for non-Prometheus alerts.';


--
-- Name: COLUMN alerts.labels; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.alerts.labels IS 'Prometheus labels JSONB (e.g. instance, service, job). NULL for non-Prometheus alerts.';


--
-- Name: api_keys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.api_keys (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    key_prefix character varying(8) NOT NULL,
    key_hash character varying(128) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    last_used_at timestamp with time zone,
    last_used_ip inet,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE api_keys; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.api_keys IS 'API keys for external system authentication against /api/v1/ endpoints';


--
-- Name: COLUMN api_keys.key_prefix; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.key_prefix IS 'First 8 chars of the key (e.g. aa_a1b2c) for UI identification';


--
-- Name: COLUMN api_keys.key_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.api_keys.key_hash IS 'SHA-256 hash of the full API key — plaintext is never stored';


--
-- Name: asset_chronicle; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_chronicle (
    "time" timestamp with time zone NOT NULL,
    asset_id uuid NOT NULL,
    event_type text NOT NULL,
    generation_run_id uuid,
    changed_attributes text[],
    attribute_changes jsonb,
    full_snapshot jsonb,
    operation_id uuid,
    causation text,
    related_asset_id uuid,
    id bigint NOT NULL
);

-- Convert to TimescaleDB hypertable
SELECT public.create_hypertable('public.asset_chronicle', 'time', if_not_exists => TRUE, migrate_data => TRUE);


--
-- Name: COLUMN asset_chronicle.generation_run_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_chronicle.generation_run_id IS 'UUID of the asset generation pipeline run that created this event. Links to asset_generation_service generation_run_id.';


--
-- Name: COLUMN asset_chronicle.changed_attributes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_chronicle.changed_attributes IS 'Array of attribute names that changed in this event. Enables efficient queries like "find all events where hostname changed"';


--
-- Name: COLUMN asset_chronicle.attribute_changes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_chronicle.attribute_changes IS 'JSONB map of detailed changes: {attr: {old: [...], new: [...], added: [...], removed: [...]}}. Provides granular change details.';


--
-- Name: COLUMN asset_chronicle.full_snapshot; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_chronicle.full_snapshot IS 'Complete asset state at event time. Used for asset_created, asset_readded, and periodic snapshots. Enables point-in-time reconstruction without replaying all events.';


--
-- Name: COLUMN asset_chronicle.operation_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_chronicle.operation_id IS 'Links all events that occurred as part of a single logical operation (e.g., merge operation affecting multiple assets)';


--
-- Name: COLUMN asset_chronicle.causation; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_chronicle.causation IS 'Why this event occurred: scheduled_scan, manual_update, merge_operation, data_import, etc. Enables root cause analysis.';


--
-- Name: COLUMN asset_chronicle.related_asset_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_chronicle.related_asset_id IS 'For operations involving multiple assets (like merges), references the related asset. Enables tracking asset relationships.';


--
-- Name: asset_chronicle_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.asset_chronicle_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: asset_chronicle_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.asset_chronicle_id_seq OWNED BY public.asset_chronicle.id;


--
-- Name: asset_conflicts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_conflicts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    generation_run_id uuid NOT NULL,
    conflict_type character varying(50) NOT NULL,
    severity character varying(20) NOT NULL,
    asset_id uuid,
    component character varying(255) NOT NULL,
    conflicting_asset_id uuid,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    status character varying(20) DEFAULT 'open'::character varying NOT NULL,
    resolution_strategy character varying(50),
    resolved_at timestamp with time zone,
    resolved_by character varying(255),
    resolution_notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: asset_id_mappings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_id_mappings (
    component text NOT NULL,
    asset_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE asset_id_mappings; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.asset_id_mappings IS 'Maps asset components (hostname, ip, mac, sn) to asset IDs for deduplication';


--
-- Name: asset_manual_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_manual_tags (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid NOT NULL,
    tag_name character varying(100) NOT NULL,
    tag_color character varying(7),
    created_by character varying(200),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: asset_merges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_merges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    primary_asset_id uuid NOT NULL,
    secondary_asset_id uuid NOT NULL,
    merge_strategy character varying(20) NOT NULL,
    merged_by character varying(200),
    merged_at timestamp with time zone DEFAULT now(),
    field_resolutions jsonb
);


--
-- Name: asset_providers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_providers (
    id bigint NOT NULL,
    asset_id uuid NOT NULL,
    data_source text NOT NULL,
    data_source_category text,
    hostname text,
    fqdn text,
    ip inet,
    mac macaddr,
    sn text,
    os text,
    manufacturer text,
    model text,
    vendor_category text,
    data_source_fields jsonb,
    updated_at timestamp with time zone DEFAULT now(),
    category_prediction jsonb,
    generation_run_id uuid,
    integration_id uuid,
    os_normalized text,
    category text,
    subcategory text
);


--
-- Name: COLUMN asset_providers.category_prediction; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_providers.category_prediction IS 'Rule-based category prediction result stored as JSONB.
Structure: {"category": "server", "subcategory": "server", "confidence": 1.0, "rule_type": "os_pattern"}
Fallback: {"category": "miscellaneous", "subcategory": "unknown", "confidence": 0.5, "rule_type": "fallback"}';


--
-- Name: COLUMN asset_providers.os_normalized; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_providers.os_normalized IS 'Normalized OS value from this provider (for consistency checks)';


--
-- Name: COLUMN asset_providers.category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_providers.category IS 'Asset category from this provider (for consistency checks)';


--
-- Name: COLUMN asset_providers.subcategory; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_providers.subcategory IS 'Asset subcategory from this provider (for consistency checks)';


--
-- Name: asset_providers_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_providers_history (
    history_id bigint NOT NULL,
    archived_at timestamp with time zone DEFAULT now() NOT NULL,
    generation_run_id uuid,
    id bigint NOT NULL,
    asset_id uuid NOT NULL,
    data_source text NOT NULL,
    data_source_category text,
    hostname text,
    fqdn text,
    ip inet,
    mac macaddr,
    sn text,
    os text,
    manufacturer text,
    model text,
    vendor_category text,
    data_source_fields jsonb,
    updated_at timestamp with time zone,
    category_prediction jsonb,
    os_eol_date date,
    os_support_end_date date,
    os_eol_status character varying(50)
);


--
-- Name: COLUMN asset_providers_history.os_eol_date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.asset_providers_history.os_eol_date IS 'Historical EOL date at the time of this provider record. Allows tracking of EOL status changes over time.';


--
-- Name: asset_providers_history_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.asset_providers_history_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: asset_providers_history_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.asset_providers_history_history_id_seq OWNED BY public.asset_providers_history.history_id;


--
-- Name: asset_providers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.asset_providers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: asset_providers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.asset_providers_id_seq OWNED BY public.asset_providers.id;


--
-- Name: asset_tags_v2; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_tags_v2 (
    id bigint NOT NULL,
    asset_id uuid NOT NULL,
    tag_name character varying(255) NOT NULL,
    tag_rule_id character varying(64) NOT NULL,
    applied_at timestamp with time zone DEFAULT now(),
    tag_type character varying(20) NOT NULL,
    execution_id bigint,
    CONSTRAINT asset_tags_v2_tag_type_check CHECK (((tag_type)::text = ANY (ARRAY[('pass'::character varying)::text, ('fail'::character varying)::text, ('classification'::character varying)::text])))
);


--
-- Name: asset_tags_v2_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.asset_tags_v2_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: asset_tags_v2_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.asset_tags_v2_id_seq OWNED BY public.asset_tags_v2.id;


--
-- Name: assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assets (
    id uuid NOT NULL,
    asset text[],
    hostname text[],
    fqdn text[],
    ip text[],
    mac text[],
    sn text[],
    category text,
    subcategory text,
    os_normalized text,
    mac_vendor_enriched text[],
    manufacturer text[],
    model text[],
    data_source text[],
    data_source_category text[],
    vendor_category text[],
    confidence numeric(3,2),
    ipam_location text[],
    owner text[],
    location text[],
    user_field text[],
    extended_attributes jsonb,
    first_time timestamp with time zone,
    last_time timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now(),
    os_enrichment jsonb,
    os text[],
    os_eol_date date,
    os_support_end_date date,
    os_extended_support_date date,
    os_is_lts boolean,
    os_eol_status character varying(50),
    os_eol_checked_at timestamp with time zone,
    first_seen timestamp with time zone,
    last_seen timestamp with time zone,
    missing_since timestamp with time zone,
    last_generation_run_id uuid,
    merged_into uuid,
    confidence_breakdown jsonb,
    tags jsonb DEFAULT '{}'::jsonb,
    ip_primary inet,
    category_predicted text,
    subcategory_predicted text,
    category_is_overridden boolean DEFAULT false,
    posture_rules jsonb DEFAULT '{}'::jsonb NOT NULL,
    os_orig text,
    asset_label_override text,
    unified_values jsonb,
    CONSTRAINT chk_os_eol_status CHECK (((os_eol_status)::text = ANY (ARRAY[('active'::character varying)::text, ('approaching_eol'::character varying)::text, ('end_of_life'::character varying)::text, ('unknown'::character varying)::text, (NULL::character varying)::text])))
);


--
-- Name: asset_label_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE IF NOT EXISTS public.asset_label_overrides (
    asset_id uuid NOT NULL,
    label text NOT NULL,
    overridden_by character varying(200),
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT asset_label_overrides_pkey PRIMARY KEY (asset_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_label_overrides_active
    ON public.asset_label_overrides USING btree (asset_id) WHERE (active = true);


--
-- Tables added by migrations after this dump was last regenerated, hand-synced so fresh
-- installs (which bootstrap from this file, not the migrations) get the current schema:
--   pipeline_stage_durations (099), custom_subcategories (103), chat_providers (104),
--   anomaly_profiles (117).
--

CREATE TABLE IF NOT EXISTS public.chat_providers (
    provider text NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    api_key_encrypted text DEFAULT ''::text NOT NULL,
    base_url text DEFAULT ''::text NOT NULL,
    prompt_cache boolean DEFAULT true NOT NULL,
    thinking_enabled boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    foundry_protocol text DEFAULT ''::text NOT NULL,
    CONSTRAINT chat_providers_pkey PRIMARY KEY (provider),
    CONSTRAINT chat_providers_provider_check CHECK ((provider = ANY (ARRAY['anthropic'::text, 'openai'::text, 'openai_compat'::text, 'aws_bedrock'::text, 'foundry'::text])))
);

INSERT INTO public.chat_providers (provider, model) VALUES
    ('anthropic', 'claude-sonnet-4-6'),
    ('openai', 'gpt-4o-mini'),
    ('openai_compat', ''),
    ('aws_bedrock', ''),
    ('foundry', '')
ON CONFLICT (provider) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.custom_subcategories (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    category text NOT NULL,
    name text NOT NULL,
    created_by text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT custom_subcategories_pkey PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS custom_subcategories_category_idx ON public.custom_subcategories USING btree (category);
CREATE UNIQUE INDEX IF NOT EXISTS custom_subcategories_category_name_lower_idx ON public.custom_subcategories USING btree (category, lower(name));

CREATE TABLE IF NOT EXISTS public.pipeline_stage_durations (
    run_id uuid NOT NULL,
    stage_key text NOT NULL,
    phase text NOT NULL,
    duration_seconds numeric NOT NULL,
    started_at timestamp with time zone NOT NULL,
    CONSTRAINT pipeline_stage_durations_pkey PRIMARY KEY (run_id, stage_key, started_at)
);
CREATE INDEX IF NOT EXISTS idx_psd_stage_started ON public.pipeline_stage_durations USING btree (stage_key, started_at DESC);
SELECT public.create_hypertable('public.pipeline_stage_durations', 'started_at', if_not_exists => TRUE, migrate_data => TRUE);
SELECT public.add_retention_policy('public.pipeline_stage_durations', INTERVAL '90 days', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS public.anomaly_profiles (
    profile_kind text NOT NULL,
    key text NOT NULL,
    value text NOT NULL,
    first_seen timestamp with time zone NOT NULL,
    last_seen timestamp with time zone NOT NULL,
    seen_count integer DEFAULT 1 NOT NULL,
    CONSTRAINT anomaly_profiles_pkey PRIMARY KEY (profile_kind, key, value)
);


--
-- Name: COLUMN assets.asset; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.asset IS 'Array of asset identifiers from multiple sources';


--
-- Name: COLUMN assets.hostname; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.hostname IS 'Array of hostnames from multiple sources';


--
-- Name: COLUMN assets.fqdn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.fqdn IS 'Array of fully qualified domain names from multiple sources';


--
-- Name: COLUMN assets.ip; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.ip IS 'Array of IP addresses (as text) from multiple sources';


--
-- Name: COLUMN assets.mac; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.mac IS 'Array of MAC addresses (as text) from multiple sources';


--
-- Name: COLUMN assets.sn; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.sn IS 'Array of serial numbers from multiple sources';


--
-- Name: COLUMN assets.category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.category IS 'Array of categories aggregated from all provider records for this asset';


--
-- Name: COLUMN assets.subcategory; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.subcategory IS 'Array of subcategories aggregated from all provider records for this asset';


--
-- Name: COLUMN assets.os_normalized; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.os_normalized IS 'Single best normalized OS value selected based on confidence, specificity, and recency';


--
-- Name: COLUMN assets.mac_vendor_enriched; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.mac_vendor_enriched IS 'Array of MAC vendor enrichments';


--
-- Name: COLUMN assets.manufacturer; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.manufacturer IS 'Array of manufacturers from multiple sources';


--
-- Name: COLUMN assets.model; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.model IS 'Array of models from multiple sources';


--
-- Name: COLUMN assets.vendor_category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.vendor_category IS 'Array of vendor categories from multiple sources';


--
-- Name: COLUMN assets.os_enrichment; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.os_enrichment IS 'OS enrichment data (CPE, vendor, product, etc.) for the selected best OS';


--
-- Name: COLUMN assets.os; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.os IS 'Array of all raw OS values from all data sources (before normalization)';


--
-- Name: COLUMN assets.os_eol_date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.os_eol_date IS 'End-of-life date for the operating system. Date when the OS version no longer receives support or updates.';


--
-- Name: COLUMN assets.os_support_end_date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.os_support_end_date IS 'End of standard support date. May differ from EOL date if extended support is available.';


--
-- Name: COLUMN assets.os_extended_support_date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.os_extended_support_date IS 'End of extended/paid support date, if applicable (e.g., Ubuntu ESM, Windows ESU).';


--
-- Name: COLUMN assets.os_is_lts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.os_is_lts IS 'Indicates if the OS version is a Long-Term Support (LTS) release. LTS versions typically have longer support periods.';


--
-- Name: COLUMN assets.os_eol_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.os_eol_status IS 'Current EOL status:
- "active": EOL date is more than 6 months away
- "approaching_eol": EOL date is within 6 months
- "end_of_life": EOL date has passed
- "unknown": No EOL data available for this OS';


--
-- Name: COLUMN assets.os_eol_checked_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.os_eol_checked_at IS 'Timestamp when EOL information was last checked/updated for this asset.
Used to identify assets that need re-enrichment.';


--
-- Name: COLUMN assets.first_seen; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.first_seen IS 'Timestamp when this asset was first discovered in any generation run. Preserved across runs for lifecycle tracking.';


--
-- Name: COLUMN assets.last_seen; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.last_seen IS 'Timestamp when this asset was last seen in a generation run. Updated every run the asset appears. Used for inactivity detection.';


--
-- Name: COLUMN assets.last_generation_run_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.last_generation_run_id IS 'UUID of the generation run that last updated this asset. Links to generation_run_id in pipeline.';


--
-- Name: COLUMN assets.confidence_breakdown; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.confidence_breakdown IS 'Confidence scoring breakdown: sub-scores, penalties, and metadata from AssetScorer V2';


--
-- Name: COLUMN assets.ip_primary; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.ip_primary IS 'Most recently observed IP address for this asset';


--
-- Name: COLUMN assets.category_predicted; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.category_predicted IS 'Original predicted category before any manual override';


--
-- Name: COLUMN assets.subcategory_predicted; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.subcategory_predicted IS 'Original predicted subcategory before any manual override';


--
-- Name: COLUMN assets.category_is_overridden; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.category_is_overridden IS 'Whether category was changed by a manual override';


--
-- Name: COLUMN assets.os_orig; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.os_orig IS 'Single raw OS value that corresponds to the selected best OS (os_normalized)';


--
-- Name: COLUMN assets.unified_values; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets.unified_values IS 'Materialized Unified Field values keyed by slug. Written only by refresh_unified_values (the pipeline materialize stages); deliberately absent from bulk_upsert_assets, so the TRUNCATE+INSERT rebuild leaves it NULL until the materialize stage runs.';


--
-- Name: assets_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assets_history (
    history_id bigint NOT NULL,
    archived_at timestamp with time zone DEFAULT now() NOT NULL,
    generation_run_id uuid,
    id uuid NOT NULL,
    asset text[],
    hostname text[],
    fqdn text[],
    ip text[],
    mac text[],
    sn text[],
    category text,
    subcategory text,
    os_normalized text,
    mac_vendor_enriched text[],
    manufacturer text[],
    model text[],
    data_source text[],
    data_source_category text[],
    vendor_category text[],
    confidence numeric(3,2),
    ipam_location text[],
    owner text[],
    location text[],
    user_field text[],
    extended_attributes jsonb,
    first_time timestamp with time zone,
    last_time timestamp with time zone,
    updated_at timestamp with time zone,
    os_enrichment jsonb,
    os text[],
    first_seen timestamp with time zone,
    last_seen timestamp with time zone,
    missing_since timestamp with time zone,
    last_generation_run_id uuid,
    confidence_breakdown jsonb,
    os_orig text,
    os_eol_status character varying(50),
    os_eol_date date,
    os_is_lts boolean,
    tags jsonb DEFAULT '{}'::jsonb,
    posture_rules jsonb DEFAULT '{}'::jsonb,
    unified_values jsonb
);


--
-- Name: COLUMN assets_history.os_normalized; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets_history.os_normalized IS 'Single best normalized OS value selected based on confidence, specificity, and recency';


--
-- Name: COLUMN assets_history.os_enrichment; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets_history.os_enrichment IS 'OS enrichment data (CPE, vendor, product, etc.) for the selected best OS';


--
-- Name: COLUMN assets_history.os; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets_history.os IS 'Array of all raw OS values from all data sources (before normalization)';


--
-- Name: COLUMN assets_history.first_seen; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets_history.first_seen IS 'Historical snapshot: When this asset was first discovered (as of this archive time).';


--
-- Name: COLUMN assets_history.last_seen; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets_history.last_seen IS 'Historical snapshot: When this asset was last seen (as of this archive time).';


--
-- Name: COLUMN assets_history.last_generation_run_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets_history.last_generation_run_id IS 'Historical snapshot: Which generation run created this snapshot.';


--
-- Name: COLUMN assets_history.confidence_breakdown; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets_history.confidence_breakdown IS 'Confidence scoring breakdown: sub-scores, penalties, and metadata from AssetScorer V2';


--
-- Name: COLUMN assets_history.os_orig; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets_history.os_orig IS 'Single raw OS value that corresponds to the selected best OS (os_normalized)';


--
-- Name: COLUMN assets_history.unified_values; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.assets_history.unified_values IS 'Historical snapshot of assets.unified_values at archive time.';


--
-- Name: assets_history_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.assets_history_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: assets_history_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.assets_history_history_id_seq OWNED BY public.assets_history.history_id;


--
-- Name: audit_trail; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_trail (
    id bigint NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    user_id uuid,
    username character varying(255),
    method character varying(10) NOT NULL,
    path character varying(500) NOT NULL,
    status_code integer NOT NULL,
    ip_address character varying(45),
    user_agent text,
    request_body jsonb,
    response_summary character varying(500),
    duration_ms integer,
    request_id character varying(64),
    category text,
    resource_type text,
    resource_id text,
    action text,
    details jsonb
);


--
-- Name: COLUMN audit_trail.category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_trail.category IS 'Coarse classification of the audited action, supplied by annotate_audit (default ''admin''). NULL for rows with no semantic annotation.';


--
-- Name: COLUMN audit_trail.resource_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_trail.resource_type IS 'Kind of resource the request acted on (e.g. integration, asset), supplied by annotate_audit. NULL when nothing annotated the request.';


--
-- Name: COLUMN audit_trail.resource_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_trail.resource_id IS 'Identifier of the specific resource acted on (uuid, slug, int-as-text), supplied by annotate_audit. NULL when not applicable or nothing annotated.';


--
-- Name: COLUMN audit_trail.action; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_trail.action IS 'Verb describing what was done to the resource (e.g. create, update, delete), supplied by annotate_audit. NULL when nothing annotated the request.';


--
-- Name: COLUMN audit_trail.details; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.audit_trail.details IS 'Structured annotation context as JSONB, sanitized with the same recursive redaction as request_body and capped at 64 KB. NULL when the annotation carried no details.';


--
-- Name: audit_trail_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.audit_trail_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: audit_trail_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.audit_trail_id_seq OWNED BY public.audit_trail.id;


--
-- Name: batch_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.batch_logs (
    id bigint NOT NULL,
    batch_id uuid NOT NULL,
    log_level character varying(20) NOT NULL,
    message text NOT NULL,
    details jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE batch_logs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.batch_logs IS 'Detailed execution logs for batches';


--
-- Name: COLUMN batch_logs.log_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.batch_logs.log_level IS 'Log level: INFO, WARNING, ERROR';


--
-- Name: COLUMN batch_logs.message; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.batch_logs.message IS 'Log message';


--
-- Name: COLUMN batch_logs.details; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.batch_logs.details IS 'Additional log details as JSON';


--
-- Name: batch_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.batch_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: batch_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.batch_logs_id_seq OWNED BY public.batch_logs.id;


--
-- Name: batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.batches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    integration_id uuid NOT NULL,
    status character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    trigger_type character varying(50) DEFAULT 'manual'::character varying NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    record_count integer DEFAULT 0 NOT NULL,
    error_message text,
    error_details jsonb,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE batches; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.batches IS 'Tracks integration execution runs with metadata and results';


--
-- Name: COLUMN batches.status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.batches.status IS 'Batch status: pending, running, completed, failed, partial';


--
-- Name: COLUMN batches.trigger_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.batches.trigger_type IS 'How batch was triggered: scheduled, manual, api';


--
-- Name: COLUMN batches.record_count; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.batches.record_count IS 'Number of records collected in this batch';


--
-- Name: COLUMN batches.error_message; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.batches.error_message IS 'Error message if batch failed';


--
-- Name: COLUMN batches.error_details; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.batches.error_details IS 'Detailed error information as JSON';


--
-- Name: COLUMN batches.metadata; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.batches.metadata IS 'Additional batch metadata (field_mode, etc)';


--
-- Name: category_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.category_overrides (
    asset_id uuid NOT NULL,
    category text NOT NULL,
    overridden_by text,
    overridden_at timestamp with time zone DEFAULT now(),
    reason text,
    subcategory character varying(100),
    active boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now(),
    features_snapshot jsonb
);


--
-- Name: COLUMN category_overrides.features_snapshot; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.category_overrides.features_snapshot IS 'Asset features at correction time (manufacturer, model, os_normalized, mac_vendor, vendor_category). Used as training signal for ML retraining.';


--
-- Name: chat_feedback; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_feedback (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    message_id uuid NOT NULL,
    user_id uuid NOT NULL,
    rating smallint NOT NULL,
    comment text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chat_feedback_rating_check CHECK ((rating = ANY (ARRAY['-1'::integer, 1])))
);


--
-- Name: chat_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_messages (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    seq integer NOT NULL,
    role text NOT NULL,
    content text,
    tool_calls jsonb,
    tool_result_ref uuid,
    input_tokens integer,
    output_tokens integer,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chat_messages_role_check CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text, 'tool'::text, 'system'::text])))
);


--
-- Name: chat_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    title text NOT NULL,
    model text NOT NULL,
    provider text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);


--
-- Name: chat_tool_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_tool_calls (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    message_id uuid NOT NULL,
    tool_name text NOT NULL,
    arguments jsonb NOT NULL,
    result text,
    result_bytes integer,
    duration_ms integer,
    status text NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    artifact jsonb,
    CONSTRAINT chat_tool_calls_status_check CHECK ((status = ANY (ARRAY['ok'::text, 'error'::text, 'denied'::text, 'timeout'::text])))
);


--
-- Name: compliance_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.compliance_rules (
    id integer NOT NULL,
    name text NOT NULL,
    category text NOT NULL,
    condition_operators jsonb NOT NULL,
    conditions jsonb NOT NULL,
    description text,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: compliance_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.compliance_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: compliance_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.compliance_rules_id_seq OWNED BY public.compliance_rules.id;


--
-- Name: detection_alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.detection_alerts (
    id bigint NOT NULL,
    rule_id character varying(64) NOT NULL,
    execution_id bigint,
    triggered_at timestamp with time zone DEFAULT now(),
    resolved_at timestamp with time zone,
    acknowledged_at timestamp with time zone,
    acknowledged_by character varying(100),
    condition character varying(100) NOT NULL,
    threshold_value numeric(10,2),
    actual_value numeric(10,2),
    severity character varying(20) NOT NULL,
    status character varying(20) DEFAULT 'active'::character varying,
    message text,
    metadata jsonb,
    CONSTRAINT detection_alerts_severity_check CHECK (((severity)::text = ANY (ARRAY[('Low'::character varying)::text, ('Medium'::character varying)::text, ('High'::character varying)::text, ('Critical'::character varying)::text]))),
    CONSTRAINT detection_alerts_status_check CHECK (((status)::text = ANY (ARRAY[('active'::character varying)::text, ('resolved'::character varying)::text, ('suppressed'::character varying)::text])))
);


--
-- Name: detection_alerts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.detection_alerts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: detection_alerts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.detection_alerts_id_seq OWNED BY public.detection_alerts.id;


--
-- Name: enrichment_datasets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.enrichment_datasets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    match_field text NOT NULL,
    match_type text DEFAULT 'exact'::text NOT NULL,
    target_field text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    row_count integer DEFAULT 0 NOT NULL,
    matched_count integer,
    uploaded_by text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: enrichment_rows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.enrichment_rows (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    dataset_id uuid NOT NULL,
    match_value text NOT NULL,
    enrich_value text NOT NULL
);


--
-- Name: excluded_records; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.excluded_records (
    id bigint NOT NULL,
    generation_run_id uuid NOT NULL,
    excluded_at timestamp with time zone DEFAULT now() NOT NULL,
    raw_record_id bigint,
    integration_id uuid,
    raw_source_id bigint,
    data_source text,
    batch_id uuid,
    hostname text,
    fqdn text,
    ip inet,
    mac macaddr,
    sn text,
    os text,
    manufacturer text,
    model text,
    vendor_category text,
    exclusion_reason text NOT NULL,
    exclusion_category text NOT NULL,
    has_hostname boolean,
    has_ip boolean,
    has_mac boolean,
    has_sn boolean,
    extended_fields jsonb,
    exclusion_details jsonb
);


--
-- Name: TABLE excluded_records; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.excluded_records IS 'Records excluded from asset generation with reasons and execution metadata';


--
-- Name: COLUMN excluded_records.generation_run_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.excluded_records.generation_run_id IS 'UUID of the asset generation execution';


--
-- Name: COLUMN excluded_records.exclusion_reason; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.excluded_records.exclusion_reason IS 'Human-readable reason for exclusion';


--
-- Name: COLUMN excluded_records.exclusion_category; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.excluded_records.exclusion_category IS 'Category of exclusion (hostname_quality, ip_quality, etc.)';


--
-- Name: COLUMN excluded_records.exclusion_details; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.excluded_records.exclusion_details IS 'Detailed JSON breakdown of specific quality issues';


--
-- Name: excluded_records_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.excluded_records_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: excluded_records_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.excluded_records_id_seq OWNED BY public.excluded_records.id;


--
-- Name: field_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.field_registry (
    id integer NOT NULL,
    field_name character varying(100) NOT NULL,
    field_type character varying(20) NOT NULL,
    db_path character varying(200) NOT NULL,
    display_name character varying(100) NOT NULL,
    description text NOT NULL,
    description_source character varying(20),
    data_source character varying(100),
    searchable boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT field_registry_description_source_check CHECK (((description_source)::text = ANY (ARRAY[('hardcoded'::character varying)::text, ('llm'::character varying)::text, ('manual'::character varying)::text]))),
    CONSTRAINT field_registry_field_type_check CHECK (((field_type)::text = ANY (ARRAY[('static'::character varying)::text, ('extended'::character varying)::text])))
);


--
-- Name: field_registry_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.field_registry_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: field_registry_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.field_registry_id_seq OWNED BY public.field_registry.id;


--
-- Name: ingestion_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ingestion_batches (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    integration_id uuid NOT NULL,
    status text DEFAULT 'running'::text NOT NULL,
    raw_records_count integer DEFAULT 0,
    mapped_records_count integer DEFAULT 0,
    failed_records_count integer DEFAULT 0,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    mapping_started_at timestamp with time zone,
    completed_at timestamp with time zone,
    error_message text,
    asset_generation_started_at timestamp with time zone,
    asset_generation_completed_at timestamp with time zone,
    assets_created_count integer DEFAULT 0,
    assets_updated_count integer DEFAULT 0,
    asset_generation_status text,
    mapping_hash character varying(16),
    CONSTRAINT ingestion_batches_asset_generation_status_check CHECK (((asset_generation_status IS NULL) OR (asset_generation_status = ANY (ARRAY['processing'::text, 'completed'::text, 'failed'::text])))),
    CONSTRAINT ingestion_batches_status_check CHECK ((status = ANY (ARRAY['running'::text, 'mapping'::text, 'completed'::text, 'failed'::text])))
);


--
-- Name: COLUMN ingestion_batches.mapping_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ingestion_batches.mapping_hash IS 'SHA-256 hash (first 16 chars) of field_mapping config at time of mapping';


--
-- Name: integration_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.integration_groups (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    integration_type text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: integration_technologies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.integration_technologies (
    id text NOT NULL,
    name text NOT NULL,
    category text NOT NULL,
    logo_path text NOT NULL,
    direct_connector text,
    siem_passthrough_supported boolean DEFAULT true NOT NULL,
    default_transports text[] DEFAULT ARRAY['splunk'::text, 'log_analytics'::text, 'databricks'::text, 'sentinelone_siem'::text] NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: COLUMN integration_technologies.id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integration_technologies.id IS 'Slug used as integration_type when creating an integration (e.g. ''palo-alto-networks'').';


--
-- Name: COLUMN integration_technologies.direct_connector; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integration_technologies.direct_connector IS 'Connector key when a direct integration also exists (NULL for SIEM-only tiles).';


--
-- Name: COLUMN integration_technologies.default_transports; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integration_technologies.default_transports IS 'Transport connector keys offered by default in the wizard.';


--
-- Name: integrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.integrations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    integration_type text NOT NULL,
    category text NOT NULL,
    status text DEFAULT 'enabled'::text NOT NULL,
    config jsonb NOT NULL,
    field_mapping jsonb NOT NULL,
    last_sync_at timestamp with time zone,
    last_sync_status text,
    last_error text,
    sync_count integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    schedule_enabled boolean DEFAULT false NOT NULL,
    schedule_cron character varying(100),
    schedule_timezone character varying(50) DEFAULT 'UTC'::character varying,
    next_run_at timestamp with time zone,
    is_running boolean DEFAULT false NOT NULL,
    logo text,
    description text,
    last_connection_test_at timestamp with time zone,
    last_connection_test_status text,
    last_connection_test_message text,
    transformations jsonb DEFAULT '{}'::jsonb,
    data_filters jsonb DEFAULT '[]'::jsonb,
    group_id uuid,
    source_method character varying DEFAULT 'direct'::character varying,
    preserve_fields jsonb DEFAULT '[]'::jsonb,
    CONSTRAINT integrations_connection_test_status_check CHECK (((last_connection_test_status IS NULL) OR (last_connection_test_status = ANY (ARRAY['success'::text, 'error'::text])))),
    CONSTRAINT integrations_status_check CHECK ((status = ANY (ARRAY['enabled'::text, 'disabled'::text, 'error'::text])))
);


--
-- Name: COLUMN integrations.schedule_enabled; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.schedule_enabled IS 'Whether automated scheduling is enabled';


--
-- Name: COLUMN integrations.schedule_cron; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.schedule_cron IS 'Cron expression for schedule (e.g., "0 * * * *" for hourly)';


--
-- Name: COLUMN integrations.schedule_timezone; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.schedule_timezone IS 'Timezone for schedule evaluation';


--
-- Name: COLUMN integrations.next_run_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.next_run_at IS 'Calculated next run time';


--
-- Name: COLUMN integrations.is_running; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.is_running IS 'Whether integration is currently executing';


--
-- Name: COLUMN integrations.last_connection_test_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.last_connection_test_at IS 'Timestamp of the most recent connection test';


--
-- Name: COLUMN integrations.last_connection_test_status; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.last_connection_test_status IS 'Status of last connection test: success, error';


--
-- Name: COLUMN integrations.last_connection_test_message; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.last_connection_test_message IS 'Message or error details from last connection test';


--
-- Name: COLUMN integrations.transformations; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.transformations IS 'Field transformation definitions (separate from field mappings to avoid naming conflicts)';


--
-- Name: COLUMN integrations.data_filters; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.data_filters IS 'Array of filter rules applied during ingestion. Each rule: {field, operator, value}';


--
-- Name: COLUMN integrations.source_method; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.source_method IS 'How data is collected: direct = technology API, or a transport connector type (splunk, log_analytics, etc.)';


--
-- Name: COLUMN integrations.preserve_fields; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.integrations.preserve_fields IS 'Array of source field names marked Data Source Specific (DSS/preserve) in the wizard; authoritative for DSS-vs-mapping classification. DSS fields also stay as {field:field} self-mappings in field_mapping (#809).';


--
-- Name: ipam_networks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ipam_networks (
    id integer NOT NULL,
    network cidr NOT NULL,
    location text NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: ipam_networks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ipam_networks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ipam_networks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ipam_networks_id_seq OWNED BY public.ipam_networks.id;


--
-- Name: job_executions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.job_executions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    job_id character varying(255) NOT NULL,
    job_name character varying(255),
    execution_id uuid NOT NULL,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    duration_seconds numeric(10,2),
    status character varying(20) NOT NULL,
    records_processed integer,
    error_message text,
    message text,
    attempt_number integer DEFAULT 1,
    max_attempts integer DEFAULT 3,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT job_executions_status_check CHECK (((status)::text = ANY (ARRAY[('running'::character varying)::text, ('success'::character varying)::text, ('failed'::character varying)::text, ('timeout'::character varying)::text, ('skipped'::character varying)::text])))
);


--
-- Name: TABLE job_executions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.job_executions IS 'Audit trail and history of all scheduler job executions';


--
-- Name: job_execution_stats; Type: MATERIALIZED VIEW; Schema: public; Owner: -
--

CREATE MATERIALIZED VIEW public.job_execution_stats AS
 SELECT job_id,
    job_name,
    count(*) AS total_executions,
    count(*) FILTER (WHERE ((status)::text = 'success'::text)) AS successful_executions,
    count(*) FILTER (WHERE ((status)::text = 'failed'::text)) AS failed_executions,
    count(*) FILTER (WHERE ((status)::text = 'timeout'::text)) AS timeout_executions,
    count(*) FILTER (WHERE ((status)::text = 'skipped'::text)) AS skipped_executions,
    avg(duration_seconds) FILTER (WHERE ((status)::text = 'success'::text)) AS avg_duration_seconds,
    min(duration_seconds) FILTER (WHERE ((status)::text = 'success'::text)) AS min_duration_seconds,
    max(duration_seconds) FILTER (WHERE ((status)::text = 'success'::text)) AS max_duration_seconds,
    sum(records_processed) FILTER (WHERE ((status)::text = 'success'::text)) AS total_records_processed,
    max(started_at) AS last_execution_at,
    max(started_at) FILTER (WHERE ((status)::text = 'success'::text)) AS last_success_at,
    max(started_at) FILTER (WHERE ((status)::text = 'failed'::text)) AS last_failure_at,
    round(((100.0 * (count(*) FILTER (WHERE ((status)::text = 'success'::text)))::numeric) / (NULLIF(count(*), 0))::numeric), 2) AS success_rate
   FROM public.job_executions
  WHERE (started_at > (now() - '30 days'::interval))
  GROUP BY job_id, job_name
  WITH NO DATA;


--
-- Name: MATERIALIZED VIEW job_execution_stats; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON MATERIALIZED VIEW public.job_execution_stats IS 'Aggregated job statistics for monitoring (refresh hourly)';


--
-- Name: mac_vendor_ranges; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mac_vendor_ranges (
    id integer NOT NULL,
    mac_prefix text NOT NULL,
    vendor text NOT NULL,
    created_at timestamp with time zone DEFAULT now(),
    vendor_short text,
    vendor_address text,
    block_type text,
    is_private boolean DEFAULT false,
    data_source text,
    last_updated timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE mac_vendor_ranges; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.mac_vendor_ranges IS 'MAC address vendor information from multiple authoritative sources (IEEE, Wireshark, maclookup.app). Updated daily via multi-source import.';


--
-- Name: COLUMN mac_vendor_ranges.mac_prefix; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.mac_vendor_ranges.mac_prefix IS 'MAC address prefix (OUI) in XX:XX:XX format';


--
-- Name: COLUMN mac_vendor_ranges.vendor; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.mac_vendor_ranges.vendor IS 'Full vendor/manufacturer name';


--
-- Name: COLUMN mac_vendor_ranges.created_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.mac_vendor_ranges.created_at IS 'When this record was first created in our database';


--
-- Name: COLUMN mac_vendor_ranges.vendor_short; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.mac_vendor_ranges.vendor_short IS 'Shortened vendor name for display (from Wireshark manuf database)';


--
-- Name: COLUMN mac_vendor_ranges.vendor_address; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.mac_vendor_ranges.vendor_address IS 'Full vendor address (from IEEE official registry)';


--
-- Name: COLUMN mac_vendor_ranges.block_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.mac_vendor_ranges.block_type IS 'MAC address block type: MA-L (24-bit), MA-M (28-bit), MA-S (36-bit), CID (Company ID), or IAB (Individual Address Block)';


--
-- Name: COLUMN mac_vendor_ranges.is_private; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.mac_vendor_ranges.is_private IS 'Whether company info is private/confidential per IEEE registry';


--
-- Name: COLUMN mac_vendor_ranges.data_source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.mac_vendor_ranges.data_source IS 'Primary data source: IEEE (most authoritative), Wireshark (community curated), or maclookup.app (aggregated)';


--
-- Name: COLUMN mac_vendor_ranges.last_updated; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.mac_vendor_ranges.last_updated IS 'When the vendor last updated their info with IEEE (from source metadata)';


--
-- Name: COLUMN mac_vendor_ranges.updated_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.mac_vendor_ranges.updated_at IS 'When this record was last updated by our import process';


--
-- Name: mac_vendor_ranges_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mac_vendor_ranges_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mac_vendor_ranges_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mac_vendor_ranges_id_seq OWNED BY public.mac_vendor_ranges.id;


--
-- Name: mapping_errors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.mapping_errors (
    id bigint NOT NULL,
    batch_id uuid NOT NULL,
    raw_source_id bigint,
    error_type text NOT NULL,
    error_message text NOT NULL,
    field_name text,
    raw_data jsonb,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: mapping_errors_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.mapping_errors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mapping_errors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.mapping_errors_id_seq OWNED BY public.mapping_errors.id;


--
-- Name: merge_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.merge_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    merge_date timestamp with time zone DEFAULT now() NOT NULL,
    merged_from uuid[] NOT NULL,
    merged_into uuid NOT NULL,
    generation_run_id uuid,
    confidence_score double precision,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    performed_by character varying(255) DEFAULT 'system'::character varying,
    pre_merge_snapshot jsonb,
    reversible_until timestamp with time zone,
    reversed_at timestamp with time zone,
    reversal_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: merge_prevention_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.merge_prevention_rules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    rule_name character varying(100) NOT NULL,
    description text,
    rule_type character varying(50) NOT NULL,
    rule_definition jsonb NOT NULL,
    enabled boolean DEFAULT true,
    priority integer DEFAULT 50,
    created_by character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    last_evaluated_at timestamp with time zone,
    evaluation_count integer DEFAULT 0,
    violation_count integer DEFAULT 0
);


--
-- Name: confidence_threshold_high(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE OR REPLACE FUNCTION public.confidence_threshold_high() RETURNS double precision
    LANGUAGE sql STABLE
    AS $$
    SELECT COALESCE(
        (SELECT value::float FROM public.system_settings
         WHERE category = 'asset_generation' AND key = 'confidence_threshold_high'),
        0.70
    );
$$;


--
-- Name: confidence_threshold_medium(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE OR REPLACE FUNCTION public.confidence_threshold_medium() RETURNS double precision
    LANGUAGE sql STABLE
    AS $$
    SELECT COALESCE(
        (SELECT value::float FROM public.system_settings
         WHERE category = 'asset_generation' AND key = 'confidence_threshold_medium'),
        0.45
    );
$$;


--
-- Name: mv_asset_quality_metrics; Type: MATERIALIZED VIEW; Schema: public; Owner: -
--

CREATE MATERIALIZED VIEW public.mv_asset_quality_metrics AS
 SELECT
        CASE
            WHEN (lower(category) = ANY (ARRAY['ot'::text, 'iot'::text])) THEN 'OT/IoT'::text
            ELSE COALESCE(category, 'Uncategorized'::text)
        END AS category,
    avg(confidence) AS avg_confidence,
    count(*) AS count,
        CASE
            WHEN (avg(confidence) >= public.confidence_threshold_high()) THEN 'high'::text
            WHEN (avg(confidence) >= public.confidence_threshold_medium()) THEN 'medium'::text
            ELSE 'low'::text
        END AS confidence_level,
    count(*) FILTER (WHERE (confidence >= public.confidence_threshold_high())) AS high_count,
    count(*) FILTER (WHERE ((confidence >= public.confidence_threshold_medium()) AND (confidence < public.confidence_threshold_high()))) AS medium_count,
    count(*) FILTER (WHERE (confidence < public.confidence_threshold_medium())) AS low_count
   FROM public.assets
  GROUP BY
        CASE
            WHEN (lower(category) = ANY (ARRAY['ot'::text, 'iot'::text])) THEN 'OT/IoT'::text
            ELSE COALESCE(category, 'Uncategorized'::text)
        END
  WITH NO DATA;


--
-- Name: mv_dashboard_summary; Type: MATERIALIZED VIEW; Schema: public; Owner: -
--

CREATE MATERIALIZED VIEW public.mv_dashboard_summary AS
 SELECT 'summary'::text AS view_key,
    ( SELECT count(*) AS count
           FROM public.assets) AS total_assets,
    ( SELECT count(*) AS count
           FROM public.assets
          WHERE ((assets.first_seen)::date = CURRENT_DATE)) AS assets_added_today,
    ( SELECT json_agg(row_to_json(cats.*) ORDER BY cats.count DESC) AS json_agg
           FROM ( SELECT COALESCE(assets.category, 'Uncategorized'::text) AS category,
                    count(*) AS count,
                    round((((count(*))::numeric * 100.0) / NULLIF(sum(count(*)) OVER (), (0)::numeric)), 1) AS percentage
                   FROM public.assets
                  GROUP BY assets.category) cats) AS category_breakdown,
    ( SELECT json_agg(row_to_json(conf.*) ORDER BY conf.sort_order) AS json_agg
           FROM ( SELECT levels.level,
                    count(*) AS count,
                    round((((count(*))::numeric * 100.0) / NULLIF(sum(count(*)) OVER (), (0)::numeric)), 1) AS percentage,
                        CASE levels.level
                            WHEN 'high'::text THEN 1
                            WHEN 'medium'::text THEN 2
                            WHEN 'low'::text THEN 3
                            ELSE NULL::integer
                        END AS sort_order
                   FROM ( SELECT
                                CASE
                                    WHEN (assets.confidence >= public.confidence_threshold_high()) THEN 'high'::text
                                    WHEN (assets.confidence >= public.confidence_threshold_medium()) THEN 'medium'::text
                                    ELSE 'low'::text
                                END AS level
                           FROM public.assets) levels
                  GROUP BY levels.level) conf) AS confidence_distribution,
    now() AS refreshed_at
  WITH NO DATA;


--
-- Name: mv_provider_coverage; Type: MATERIALIZED VIEW; Schema: public; Owner: -
--

CREATE MATERIALIZED VIEW public.mv_provider_coverage AS
 WITH pipeline_sync AS (
         SELECT ingestion_batches.integration_id,
            max(ingestion_batches.started_at) AS last_sync_at
           FROM public.ingestion_batches
          WHERE (ingestion_batches.asset_generation_status = 'completed'::text)
          GROUP BY ingestion_batches.integration_id
        ), ingestion_run AS (
         SELECT ingestion_batches.integration_id,
            max(ingestion_batches.started_at) AS last_run_at
           FROM public.ingestion_batches
          WHERE (ingestion_batches.status = 'completed'::text)
          GROUP BY ingestion_batches.integration_id
        )
 SELECT i.id,
    i.name,
    i.integration_type,
    i.status,
    i.logo,
    ps.last_sync_at,
    ir.last_run_at,
    count(DISTINCT ap.asset_id) AS asset_count,
    round((avg(a.confidence) * (100)::numeric), 0) AS avg_confidence,
    count(DISTINCT a.id) FILTER (WHERE (a.confidence >= public.confidence_threshold_high())) AS high_conf_count,
    count(DISTINCT a.id) FILTER (WHERE ((a.confidence >= public.confidence_threshold_medium()) AND (a.confidence < public.confidence_threshold_high()))) AS medium_conf_count,
    count(DISTINCT a.id) FILTER (WHERE (a.confidence < public.confidence_threshold_medium())) AS low_conf_count
   FROM ((((public.integrations i
     LEFT JOIN pipeline_sync ps ON ((ps.integration_id = i.id)))
     LEFT JOIN ingestion_run ir ON ((ir.integration_id = i.id)))
     LEFT JOIN public.asset_providers ap ON ((i.id = ap.integration_id)))
     LEFT JOIN public.assets a ON ((ap.asset_id = a.id)))
  WHERE (i.status = 'enabled'::text)
  GROUP BY i.id, i.name, i.integration_type, i.status, i.logo, ps.last_sync_at, ir.last_run_at
  WITH NO DATA;


--
-- Name: mv_provider_coverage_grouped; Type: MATERIALIZED VIEW; Schema: public; Owner: -
--

CREATE MATERIALIZED VIEW public.mv_provider_coverage_grouped AS
 WITH pipeline_sync AS (
         SELECT ingestion_batches.integration_id,
            max(ingestion_batches.started_at) AS last_sync_at
           FROM public.ingestion_batches
          WHERE (ingestion_batches.asset_generation_status = 'completed'::text)
          GROUP BY ingestion_batches.integration_id
        ), ingestion_run AS (
         SELECT ingestion_batches.integration_id,
            max(ingestion_batches.started_at) AS last_run_at
           FROM public.ingestion_batches
          WHERE (ingestion_batches.status = 'completed'::text)
          GROUP BY ingestion_batches.integration_id
        ), group_sync AS (
         SELECT i.group_id,
            max(ps.last_sync_at) AS last_sync_at,
            max(ir.last_run_at) AS last_run_at
           FROM ((public.integrations i
             LEFT JOIN pipeline_sync ps ON ((ps.integration_id = i.id)))
             LEFT JOIN ingestion_run ir ON ((ir.integration_id = i.id)))
          WHERE (i.group_id IS NOT NULL)
          GROUP BY i.group_id
        )
 SELECT ig.id,
    ig.name,
    ig.integration_type,
    'group'::text AS source_type,
    ( SELECT i2.logo
           FROM public.integrations i2
          WHERE (i2.group_id = ig.id)
         LIMIT 1) AS logo,
    count(DISTINCT ap.asset_id) AS asset_count,
    round((avg(a.confidence) * (100)::numeric), 0) AS avg_confidence,
    count(DISTINCT a.id) FILTER (WHERE (a.confidence >= public.confidence_threshold_high())) AS high_conf_count,
    count(DISTINCT a.id) FILTER (WHERE ((a.confidence >= public.confidence_threshold_medium()) AND (a.confidence < public.confidence_threshold_high()))) AS medium_conf_count,
    count(DISTINCT a.id) FILTER (WHERE (a.confidence < public.confidence_threshold_medium())) AS low_conf_count,
    gs.last_sync_at,
    gs.last_run_at
   FROM ((((public.integration_groups ig
     JOIN public.integrations i ON (((i.group_id = ig.id) AND (i.status = 'enabled'::text))))
     LEFT JOIN group_sync gs ON ((gs.group_id = ig.id)))
     LEFT JOIN public.asset_providers ap ON ((ap.integration_id = i.id)))
     LEFT JOIN public.assets a ON ((ap.asset_id = a.id)))
  GROUP BY ig.id, ig.name, ig.integration_type, gs.last_sync_at, gs.last_run_at
UNION ALL
 SELECT i.id,
    i.name,
    i.integration_type,
    'integration'::text AS source_type,
    i.logo,
    count(DISTINCT ap.asset_id) AS asset_count,
    round((avg(a.confidence) * (100)::numeric), 0) AS avg_confidence,
    count(DISTINCT a.id) FILTER (WHERE (a.confidence >= public.confidence_threshold_high())) AS high_conf_count,
    count(DISTINCT a.id) FILTER (WHERE ((a.confidence >= public.confidence_threshold_medium()) AND (a.confidence < public.confidence_threshold_high()))) AS medium_conf_count,
    count(DISTINCT a.id) FILTER (WHERE (a.confidence < public.confidence_threshold_medium())) AS low_conf_count,
    ps.last_sync_at,
    ir.last_run_at
   FROM ((((public.integrations i
     LEFT JOIN pipeline_sync ps ON ((ps.integration_id = i.id)))
     LEFT JOIN ingestion_run ir ON ((ir.integration_id = i.id)))
     LEFT JOIN public.asset_providers ap ON ((ap.integration_id = i.id)))
     LEFT JOIN public.assets a ON ((ap.asset_id = a.id)))
  WHERE ((i.status = 'enabled'::text) AND (i.group_id IS NULL))
  GROUP BY i.id, i.name, i.integration_type, i.logo, ps.last_sync_at, ir.last_run_at
  WITH NO DATA;


--
-- Name: os_eol_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.os_eol_data (
    id integer NOT NULL,
    product_name character varying(255) NOT NULL,
    cycle character varying(100) NOT NULL,
    release_label character varying(255),
    codename character varying(100),
    release_date date,
    eol_date date,
    support_end_date date,
    extended_support_date date,
    latest_version character varying(100),
    latest_release_date date,
    lts boolean DEFAULT false,
    link text,
    data_source character varying(50) DEFAULT 'endoflife.date'::character varying,
    raw_data jsonb,
    last_synced timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE os_eol_data; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.os_eol_data IS 'Repository of operating system end-of-life dates from authoritative sources like endoflife.date.
Used to enrich assets with OS EOL status information.';


--
-- Name: COLUMN os_eol_data.product_name; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.os_eol_data.product_name IS 'Normalized product name (e.g., "windows", "ubuntu", "macos"). Maps to endoflife.date product identifiers.';


--
-- Name: COLUMN os_eol_data.cycle; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.os_eol_data.cycle IS 'Version/release cycle identifier (e.g., "10-22h2" for Windows 10 22H2, "24.04" for Ubuntu 24.04 LTS).';


--
-- Name: COLUMN os_eol_data.eol_date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.os_eol_data.eol_date IS 'Date when the OS version reaches end-of-life and no longer receives any support or updates.';


--
-- Name: COLUMN os_eol_data.support_end_date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.os_eol_data.support_end_date IS 'Date when standard support ends (may differ from EOL for products with extended support).';


--
-- Name: COLUMN os_eol_data.extended_support_date; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.os_eol_data.extended_support_date IS 'Date when extended/paid support ends (if available). For Ubuntu ESM, Windows ESU, etc.';


--
-- Name: os_eol_data_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.os_eol_data_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: os_eol_data_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.os_eol_data_id_seq OWNED BY public.os_eol_data.id;


--
-- Name: password_reset_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_reset_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    token_hash character varying(255) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    used_at timestamp with time zone,
    token_type text DEFAULT 'reset'::text NOT NULL,
    CONSTRAINT password_reset_tokens_token_type_check CHECK ((token_type = ANY (ARRAY['reset'::text, 'invite'::text])))
);


--
-- Name: TABLE password_reset_tokens; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.password_reset_tokens IS 'One-time-use tokens for password reset flow. Tokens expire after 1 hour and can only be used once.';


--
-- Name: COLUMN password_reset_tokens.token_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.password_reset_tokens.token_hash IS 'SHA-256 hash of the reset token sent via email. Never store raw tokens in the database.';


--
-- Name: COLUMN password_reset_tokens.used_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.password_reset_tokens.used_at IS 'Timestamp when token was used. NULL means token has not been used yet.';


--
-- Name: COLUMN password_reset_tokens.token_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.password_reset_tokens.token_type IS 'Discriminator for the token flow: ''reset'' (self-service password reset, 1h TTL) or ''invite'' (admin-issued user onboarding, 7d TTL).';


--
-- Name: pipeline_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    triggered_by character varying(20) NOT NULL,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    current_stage character varying(50),
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    duration_seconds numeric(10,2),
    stats jsonb,
    error_message text,
    created_at timestamp with time zone DEFAULT now(),
    data_settled_at timestamp with time zone
);


--
-- Name: posture_categories; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.posture_categories (
    id character varying(64) NOT NULL,
    name text NOT NULL,
    description text NOT NULL,
    icon text NOT NULL,
    applicable_categories jsonb NOT NULL,
    display_order smallint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_evaluated_at timestamp with time zone
);


--
-- Name: posture_sub_rule_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.posture_sub_rule_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    sub_rule_id uuid NOT NULL,
    category_id character varying(64) NOT NULL,
    action text NOT NULL,
    before_state jsonb,
    after_state jsonb,
    changed_by character varying(255) NOT NULL,
    changed_by_username character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT posture_sub_rule_history_action_check CHECK ((action = ANY (ARRAY['create'::text, 'update'::text, 'toggle'::text, 'delete'::text])))
);


--
-- Name: posture_sub_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.posture_sub_rules (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    category_id character varying(64) NOT NULL,
    name text NOT NULL,
    description text,
    enabled boolean DEFAULT true NOT NULL,
    target_filters jsonb NOT NULL,
    compliance_criteria jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    output_value jsonb,
    output_mode text,
    display_order smallint DEFAULT 0 NOT NULL,
    output_field text,
    CONSTRAINT posture_sub_rules_output_consistency CHECK ((((output_mode IS NULL) AND (output_value IS NULL)) OR ((output_mode IS NOT NULL) AND (output_value IS NOT NULL)))),
    CONSTRAINT posture_sub_rules_output_mode_check CHECK ((output_mode = ANY (ARRAY['field'::text, 'text'::text])))
);


--
-- Name: posture_tier_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.posture_tier_snapshots (
    id bigint NOT NULL,
    snapshot_at timestamp with time zone DEFAULT now() NOT NULL,
    secured_count integer NOT NULL,
    monitored_count integer NOT NULL,
    discovered_count integer NOT NULL
);


--
-- Name: posture_coverage_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.posture_coverage_snapshots (
    id bigint NOT NULL,
    snapshot_at timestamp with time zone DEFAULT now() NOT NULL,
    control_id text NOT NULL,
    asset_category text NOT NULL,
    pass_count integer NOT NULL,
    fail_count integer NOT NULL,
    na_count integer NOT NULL
);


--
-- Name: posture_tier_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.posture_tier_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: posture_tier_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.posture_tier_snapshots_id_seq OWNED BY public.posture_tier_snapshots.id;


--
-- Name: posture_coverage_snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.posture_coverage_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: posture_coverage_snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.posture_coverage_snapshots_id_seq OWNED BY public.posture_coverage_snapshots.id;


--
-- Name: raw_asset_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_asset_data (
    id bigint NOT NULL,
    integration_id uuid NOT NULL,
    raw_source_id bigint,
    hostname text[],
    fqdn text[],
    ip text[],
    mac text[],
    sn text[],
    os text,
    manufacturer text,
    model text,
    vendor_category text,
    extended_fields jsonb,
    batch_id uuid NOT NULL,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    mapped_at timestamp with time zone DEFAULT now() NOT NULL,
    first_time timestamp with time zone,
    last_time timestamp with time zone
);


--
-- Name: COLUMN raw_asset_data.first_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.raw_asset_data.first_time IS 'Earliest observation timestamp from source';


--
-- Name: COLUMN raw_asset_data.last_time; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.raw_asset_data.last_time IS 'Most recent observation timestamp from source';


--
-- Name: raw_asset_data_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.raw_asset_data_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: raw_asset_data_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.raw_asset_data_id_seq OWNED BY public.raw_asset_data.id;


--
-- Name: raw_source_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_source_data (
    id bigint NOT NULL,
    integration_id uuid NOT NULL,
    raw_payload jsonb NOT NULL,
    batch_id uuid NOT NULL,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    source_record_id text
);


--
-- Name: TABLE raw_source_data; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.raw_source_data IS 'Stores raw JSON records from integration sources';


--
-- Name: COLUMN raw_source_data.source_record_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.raw_source_data.source_record_id IS 'Original record ID from source system';


--
-- Name: raw_source_data_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.raw_source_data_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: raw_source_data_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.raw_source_data_id_seq OWNED BY public.raw_source_data.id;


--
-- Name: recent_job_executions; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.recent_job_executions AS
 SELECT id,
    job_id,
    job_name,
    execution_id,
    started_at,
    completed_at,
    duration_seconds,
    status,
    records_processed,
    error_message,
    message,
    attempt_number,
    max_attempts,
    created_at,
        CASE
            WHEN (((status)::text = 'running'::text) AND (EXTRACT(epoch FROM (now() - started_at)) > (3600)::numeric)) THEN 'stuck'::character varying
            ELSE status
        END AS effective_status
   FROM public.job_executions je
  WHERE (started_at > (now() - '7 days'::interval))
  ORDER BY started_at DESC;


--
-- Name: VIEW recent_job_executions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.recent_job_executions IS 'Recent job executions (last 7 days) with stuck detection';


--
-- Name: reference_data_updates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reference_data_updates (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    bundle_version text NOT NULL,
    source text NOT NULL,
    initiated_by text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    status text DEFAULT 'running'::text NOT NULL,
    error_message text,
    datasets jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT reference_data_updates_source_check CHECK ((source = ANY (ARRAY['upload'::text, 'github'::text]))),
    CONSTRAINT reference_data_updates_status_check CHECK ((status = ANY (ARRAY['running'::text, 'success'::text, 'failed'::text])))
);


--
-- Name: TABLE reference_data_updates; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.reference_data_updates IS 'History of reference-data bundle applies (mac_vendor, os_eol). One row per apply attempt.';


--
-- Name: reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reports (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title character varying(255) NOT NULL,
    description text,
    charts jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    is_default boolean DEFAULT false,
    CONSTRAINT reports_title_not_empty CHECK (((title)::text <> ''::text))
);


--
-- Name: TABLE reports; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.reports IS 'System-wide reports with chart configurations (shared across all users)';


--
-- Name: COLUMN reports.charts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.reports.charts IS 'JSONB array of chart configurations (layout, query config, visual settings)';


--
-- Name: COLUMN reports.created_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.reports.created_by IS 'User who created the report (audit trail)';


--
-- Name: COLUMN reports.updated_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.reports.updated_by IS 'User who last updated the report (audit trail)';


--
-- Name: COLUMN reports.is_default; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.reports.is_default IS 'Indicates if this is the default report (only one allowed)';


--
-- Name: reports_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reports_audit_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    report_id uuid,
    action character varying(50) NOT NULL,
    changed_by uuid,
    changed_by_username character varying(255),
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    changes jsonb,
    CONSTRAINT valid_action CHECK (((action)::text = ANY (ARRAY[('created'::character varying)::text, ('updated'::character varying)::text, ('deleted'::character varying)::text])))
);


--
-- Name: TABLE reports_audit_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.reports_audit_log IS 'Audit trail for all report changes (who, when, what)';


--
-- Name: rule_execution_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rule_execution_history (
    id bigint NOT NULL,
    rule_id character varying(64) NOT NULL,
    executed_at timestamp with time zone DEFAULT now(),
    execution_time_ms integer,
    status character varying(20) NOT NULL,
    error_message text,
    assets_matched integer DEFAULT 0,
    assets_passed integer,
    assets_failed integer,
    tags_applied integer DEFAULT 0,
    tags_removed integer DEFAULT 0,
    triggered_by character varying(50),
    triggered_by_user character varying(100),
    generation_run_id uuid,
    CONSTRAINT rule_execution_history_status_check CHECK (((status)::text = ANY (ARRAY[('success'::character varying)::text, ('failed'::character varying)::text, ('partial'::character varying)::text]))),
    CONSTRAINT rule_execution_history_triggered_by_check CHECK (((triggered_by)::text = ANY (ARRAY[('scheduled'::character varying)::text, ('manual'::character varying)::text, ('api'::character varying)::text, ('asset_generation_pipeline'::character varying)::text])))
);


--
-- Name: rule_execution_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.rule_execution_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: rule_execution_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.rule_execution_history_id_seq OWNED BY public.rule_execution_history.id;


--
-- Name: saved_filters; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.saved_filters (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    owner_id uuid NOT NULL,
    name text NOT NULL,
    description text,
    payload jsonb NOT NULL,
    visibility text DEFAULT 'private'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone,
    CONSTRAINT saved_filters_visibility_check CHECK ((visibility = ANY (ARRAY['private'::text, 'link'::text, 'org'::text])))
);


--
-- Name: saved_filter_grants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.saved_filter_grants (
    saved_filter_id uuid NOT NULL,
    user_id uuid NOT NULL,
    granted_by uuid,
    granted_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    filename text NOT NULL,
    applied_at timestamp with time zone DEFAULT now()
);


--
-- Name: settings_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.settings_audit_log (
    id integer NOT NULL,
    setting_id integer NOT NULL,
    category character varying(50) NOT NULL,
    key character varying(100) NOT NULL,
    old_value text,
    new_value text,
    changed_by character varying(255) NOT NULL,
    changed_by_username character varying(255),
    change_reason text,
    ip_address character varying(45),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: settings_audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.settings_audit_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: settings_audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.settings_audit_log_id_seq OWNED BY public.settings_audit_log.id;


--
-- Name: split_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.split_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    split_date timestamp with time zone DEFAULT now() NOT NULL,
    split_from uuid NOT NULL,
    split_into uuid[] NOT NULL,
    split_reason character varying(100) NOT NULL,
    component_distribution jsonb NOT NULL,
    performed_by character varying(255) NOT NULL,
    approval_required boolean DEFAULT false,
    approved_by character varying(255),
    approved_at timestamp with time zone,
    prevent_auto_remerge boolean DEFAULT true,
    remerge_prevention_expires_at timestamp with time zone,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: split_prevention; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.split_prevention (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id_1 uuid NOT NULL,
    asset_id_2 uuid NOT NULL,
    split_history_id uuid,
    created_by character varying(255) NOT NULL,
    reason text,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: system_dashboard_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_dashboard_preferences (
    id integer DEFAULT 1 NOT NULL,
    working_dashboard jsonb DEFAULT '{"charts": []}'::jsonb NOT NULL,
    last_modified timestamp with time zone DEFAULT now() NOT NULL,
    last_modified_by uuid,
    CONSTRAINT single_row_check CHECK ((id = 1))
);


--
-- Name: TABLE system_dashboard_preferences; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.system_dashboard_preferences IS 'System-wide working dashboard (shared by all users, no real-time sync)';


--
-- Name: system_info; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_info (
    id integer DEFAULT 1 NOT NULL,
    installed_version text NOT NULL,
    installed_at timestamp with time zone DEFAULT now() NOT NULL,
    last_updated_at timestamp with time zone DEFAULT now() NOT NULL,
    runtime text NOT NULL,
    install_mode text NOT NULL,
    opt_in_monitoring boolean DEFAULT false NOT NULL,
    opt_in_llm boolean DEFAULT false NOT NULL,
    llm_model text,
    install_dir text NOT NULL,
    install_log_path text DEFAULT '/var/log/assetatlas/install.log'::text NOT NULL,
    CONSTRAINT system_info_id_check CHECK ((id = 1)),
    CONSTRAINT system_info_install_mode_check CHECK ((install_mode = ANY (ARRAY['online'::text, 'offline'::text]))),
    CONSTRAINT system_info_runtime_check CHECK ((runtime = ANY (ARRAY['docker'::text, 'podman'::text])))
);


--
-- Name: system_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_settings (
    id integer NOT NULL,
    category character varying(50) NOT NULL,
    key character varying(100) NOT NULL,
    value text NOT NULL,
    value_type character varying(20) NOT NULL,
    default_value text NOT NULL,
    description text,
    validation_rule text,
    is_sensitive boolean DEFAULT false,
    requires_restart boolean DEFAULT false,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_by character varying(255)
);


--
-- Name: system_settings_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.system_settings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: system_settings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.system_settings_id_seq OWNED BY public.system_settings.id;


--
-- Name: tag_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tag_rules (
    id character varying(64) NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    type character varying(20) NOT NULL,
    enabled boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    last_modified timestamp with time zone DEFAULT now(),
    last_executed_at timestamp with time zone,
    execution_count integer DEFAULT 0,
    last_execution_duration_ms integer,
    last_execution_assets_matched integer DEFAULT 0,
    last_execution_assets_passed integer,
    last_execution_assets_failed integer,
    target_filters jsonb NOT NULL,
    tags jsonb NOT NULL,
    compliance_criteria jsonb,
    detection_config jsonb,
    CONSTRAINT tag_rules_type_check CHECK (((type)::text = ANY (ARRAY[('compliance'::character varying)::text, ('classification'::character varying)::text]))),
    CONSTRAINT valid_classification_rule CHECK ((((type)::text <> 'classification'::text) OR (((tags -> 'label'::text) IS NOT NULL) AND ((tags -> 'color'::text) IS NOT NULL)))),
    CONSTRAINT valid_compliance_rule CHECK ((((type)::text <> 'compliance'::text) OR ((compliance_criteria IS NOT NULL) AND ((tags -> 'pass'::text) IS NOT NULL) AND ((tags -> 'fail'::text) IS NOT NULL)))),
    CONSTRAINT valid_target_filters CHECK ((jsonb_typeof(target_filters) = 'array'::text))
);


--
-- Name: teams_account_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.teams_account_links (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    aad_object_id text NOT NULL,
    tenant_id text NOT NULL,
    user_id uuid NOT NULL,
    linked_at timestamp with time zone DEFAULT now() NOT NULL,
    linked_via_ip inet
);


--
-- Name: teams_conversation_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.teams_conversation_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    conversation_id text NOT NULL,
    user_id uuid NOT NULL,
    session_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_active_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: teams_link_used_jtis; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.teams_link_used_jtis (
    jti text NOT NULL,
    consumed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_mfa; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_mfa (
    user_id uuid NOT NULL,
    mfa_enabled boolean DEFAULT false NOT NULL,
    mfa_secret text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    label text DEFAULT 'Authenticator app'::text NOT NULL,
    type text DEFAULT 'totp'::text NOT NULL,
    CONSTRAINT user_mfa_type_check CHECK ((type = 'totp'::text))
);


--
-- Name: TABLE user_mfa; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_mfa IS 'Multi-factor authentication configuration for users. Currently supports TOTP (Time-based One-Time Password)
via apps like Google Authenticator or Authy. Implementation planned for Phase 5 post-launch.';


--
-- Name: COLUMN user_mfa.mfa_secret; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_mfa.mfa_secret IS 'Base32-encoded TOTP secret key. Encrypted at rest. Used to generate 6-digit verification codes.';


--
-- Name: user_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_sessions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    refresh_token_hash character varying(255) NOT NULL,
    access_token_jti character varying(255) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    ip_address inet,
    user_agent text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    last_used_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE user_sessions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.user_sessions IS 'Active user sessions tracking for JWT authentication. Each session has a refresh token that can be used
to obtain new access tokens. Sessions expire after 7 days or can be manually revoked.';


--
-- Name: COLUMN user_sessions.refresh_token_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_sessions.refresh_token_hash IS 'SHA-256 hash of the refresh token. Never store raw tokens in the database.';


--
-- Name: COLUMN user_sessions.access_token_jti; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_sessions.access_token_jti IS 'JWT ID (jti) of the current access token. Used for immediate revocation via Redis blacklist.';


--
-- Name: COLUMN user_sessions.is_active; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.user_sessions.is_active IS 'Session status. Set to false when user logs out or admin revokes session.';


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    username character varying(255) NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255) NOT NULL,
    full_name character varying(255),
    role character varying(50) DEFAULT 'user'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    email_verified boolean DEFAULT false NOT NULL,
    must_change_password boolean DEFAULT false NOT NULL,
    password_changed_at timestamp with time zone DEFAULT now() NOT NULL,
    failed_login_attempts integer DEFAULT 0 NOT NULL,
    locked_until timestamp with time zone,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    CONSTRAINT chk_user_role CHECK (((role)::text = ANY (ARRAY[('user'::character varying)::text, ('admin'::character varying)::text, ('super_admin'::character varying)::text])))
);


--
-- Name: TABLE users; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.users IS 'User accounts for AssetAtlas backend API. Supports JWT authentication, role-based access control,
and account lockout for security.';


--
-- Name: COLUMN users.password_hash; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.users.password_hash IS 'Bcrypt hashed password (12 rounds). Never store plaintext passwords.';


--
-- Name: COLUMN users.role; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.users.role IS 'User role for RBAC: user (standard access), admin (management functions), super_admin (full access).';


--
-- Name: COLUMN users.must_change_password; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.users.must_change_password IS 'Flag requiring password change on next login. Used when admin creates user or after password reset.';


--
-- Name: COLUMN users.password_changed_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.users.password_changed_at IS 'Timestamp of the last password update. Used by the password expiration check (auth.password_expiration_days). Updated by UserRepository.update_password.';


--
-- Name: COLUMN users.failed_login_attempts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.users.failed_login_attempts IS 'Counter for failed login attempts. Resets to 0 on successful login. Account locks after 5 failed attempts.';


--
-- Name: COLUMN users.locked_until; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.users.locked_until IS 'Timestamp until which the account is locked. NULL means account is not locked. Locks for 30 minutes after 5 failed attempts.';


--
-- Name: asset_chronicle id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE public.asset_chronicle ALTER COLUMN id SET DEFAULT nextval('public.asset_chronicle_id_seq'::regclass);


--
-- Name: asset_providers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_providers ALTER COLUMN id SET DEFAULT nextval('public.asset_providers_id_seq'::regclass);


--
-- Name: asset_providers_history history_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_providers_history ALTER COLUMN history_id SET DEFAULT nextval('public.asset_providers_history_history_id_seq'::regclass);


--
-- Name: asset_tags_v2 id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_tags_v2 ALTER COLUMN id SET DEFAULT nextval('public.asset_tags_v2_id_seq'::regclass);


--
-- Name: assets_history history_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets_history ALTER COLUMN history_id SET DEFAULT nextval('public.assets_history_history_id_seq'::regclass);


--
-- Name: audit_trail id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_trail ALTER COLUMN id SET DEFAULT nextval('public.audit_trail_id_seq'::regclass);


--
-- Name: batch_logs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batch_logs ALTER COLUMN id SET DEFAULT nextval('public.batch_logs_id_seq'::regclass);


--
-- Name: compliance_rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compliance_rules ALTER COLUMN id SET DEFAULT nextval('public.compliance_rules_id_seq'::regclass);


--
-- Name: detection_alerts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detection_alerts ALTER COLUMN id SET DEFAULT nextval('public.detection_alerts_id_seq'::regclass);


--
-- Name: excluded_records id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.excluded_records ALTER COLUMN id SET DEFAULT nextval('public.excluded_records_id_seq'::regclass);


--
-- Name: field_registry id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.field_registry ALTER COLUMN id SET DEFAULT nextval('public.field_registry_id_seq'::regclass);


--
-- Name: ipam_networks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ipam_networks ALTER COLUMN id SET DEFAULT nextval('public.ipam_networks_id_seq'::regclass);


--
-- Name: mac_vendor_ranges id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mac_vendor_ranges ALTER COLUMN id SET DEFAULT nextval('public.mac_vendor_ranges_id_seq'::regclass);


--
-- Name: mapping_errors id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mapping_errors ALTER COLUMN id SET DEFAULT nextval('public.mapping_errors_id_seq'::regclass);


--
-- Name: os_eol_data id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.os_eol_data ALTER COLUMN id SET DEFAULT nextval('public.os_eol_data_id_seq'::regclass);


--
-- Name: posture_tier_snapshots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posture_tier_snapshots ALTER COLUMN id SET DEFAULT nextval('public.posture_tier_snapshots_id_seq'::regclass);


--
-- Name: posture_coverage_snapshots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posture_coverage_snapshots ALTER COLUMN id SET DEFAULT nextval('public.posture_coverage_snapshots_id_seq'::regclass);


--
-- Name: raw_asset_data id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_asset_data ALTER COLUMN id SET DEFAULT nextval('public.raw_asset_data_id_seq'::regclass);


--
-- Name: raw_source_data id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_source_data ALTER COLUMN id SET DEFAULT nextval('public.raw_source_data_id_seq'::regclass);


--
-- Name: rule_execution_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_execution_history ALTER COLUMN id SET DEFAULT nextval('public.rule_execution_history_id_seq'::regclass);


--
-- Name: settings_audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings_audit_log ALTER COLUMN id SET DEFAULT nextval('public.settings_audit_log_id_seq'::regclass);


--
-- Name: system_settings id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_settings ALTER COLUMN id SET DEFAULT nextval('public.system_settings_id_seq'::regclass);


--
-- Data for Name: alert_rules; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.alert_rules (id, name, slug, type, severity, description, query, threshold, enabled, source, subject_type, created_at, updated_at, target_filters, compliance_criteria, silence_duration, created_by, evaluation_type, evaluation_cadence) FROM stdin;
ecc48369-7984-4f53-ac4a-d88147be92ad	Stale Assets — Retirement Candidates	stale-assets-retirement	security	medium	Assets not seen in 30+ days — candidates for retirement.	SELECT id, hostname[1] as hostname, category, subcategory, ip[1]::text as ip, os_normalized, last_time\n FROM assets\n WHERE last_time < now() - interval '30 days'	{"unit": "assets", "value": 30, "metric": "days_since_seen", "operator": ">="}	t	system	asset	2026-05-03 07:03:38.632641+00	2026-05-03 07:03:38.632641+00	\N	\N	\N	\N	sql	pipeline
a2728c11-b433-4ec7-b004-517fd2371c35	Data Source Sync Failures	sync-failures	operational	critical	Data source integrations where the last sync failed.	SELECT id AS subject_id,\n       name AS subject_label,\n       integration_type || ' · sync failed' AS subject_sublabel\nFROM integrations\nWHERE last_sync_status = 'failed' AND status = 'enabled'	{"unit": "integrations", "value": 0, "metric": "sync_failures", "operator": ">"}	t	system	integration	2026-05-03 07:03:38.632641+00	2026-05-03 07:03:38.632641+00	\N	\N	\N	\N	sql	pipeline
188095b9-9851-45db-a42b-213d327d4790	Data Source Stale	data-source-stale	operational	high	A data source is overdue for its own scheduled sync by more than the grace window (Alerting setting DATA_SOURCE_STALE_GRACE_MINUTES, default 60 minutes), or has not refreshed at all within DATA_SOURCE_MAX_SYNC_AGE_HOURS (default 48). Sources with no schedule, and syncs currently in flight, are not checked.	SELECT i.id AS subject_id,\n       i.name AS subject_label,\n       concat_ws(' · ',\n           i.integration_type,\n           CASE WHEN COALESCE(i.next_run_at, i.last_sync_at, i.created_at, '-infinity'::timestamptz) <\n                     now() - (COALESCE((SELECT s.value::int\n                                        FROM system_settings s\n                                        WHERE s.category = 'alerting'\n                                          AND s.key = 'DATA_SOURCE_STALE_GRACE_MINUTES'\n                                          AND trim(s.value) ~ '^[0-9]{1,6}$'), 60) || ' minutes')::interval\n                THEN COALESCE('overdue for its scheduled run since '\n                              || to_char(i.next_run_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') || ' UTC',\n                              'overdue for its scheduled run (next run never computed)')\n           END,\n           CASE WHEN COALESCE(i.last_sync_at, i.created_at, '-infinity'::timestamptz) <\n                     now() - (COALESCE((SELECT s.value::int\n                                        FROM system_settings s\n                                        WHERE s.category = 'alerting'\n                                          AND s.key = 'DATA_SOURCE_MAX_SYNC_AGE_HOURS'\n                                          AND trim(s.value) ~ '^[0-9]{1,6}$'), 48) || ' hours')::interval\n                THEN COALESCE('has not refreshed since '\n                              || to_char(i.last_sync_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') || ' UTC',\n                              'has never refreshed')\n           END\n       ) AS subject_sublabel\nFROM integrations i\nWHERE i.status = 'enabled'\n  AND i.schedule_enabled\n  AND NOT i.is_running\n  AND (COALESCE(i.next_run_at, i.last_sync_at, i.created_at, '-infinity'::timestamptz) <\n       now() - (COALESCE((SELECT s.value::int\n                          FROM system_settings s\n                          WHERE s.category = 'alerting'\n                            AND s.key = 'DATA_SOURCE_STALE_GRACE_MINUTES'\n                            AND trim(s.value) ~ '^[0-9]{1,6}$'), 60) || ' minutes')::interval\n       OR\n       COALESCE(i.last_sync_at, i.created_at, '-infinity'::timestamptz) <\n       now() - (COALESCE((SELECT s.value::int\n                          FROM system_settings s\n                          WHERE s.category = 'alerting'\n                            AND s.key = 'DATA_SOURCE_MAX_SYNC_AGE_HOURS'\n                            AND trim(s.value) ~ '^[0-9]{1,6}$'), 48) || ' hours')::interval)	{"unit": "integrations", "value": 0, "metric": "stale_integrations", "operator": ">", "grace_setting": "alerting.DATA_SOURCE_STALE_GRACE_MINUTES", "max_sync_age_setting": "alerting.DATA_SOURCE_MAX_SYNC_AGE_HOURS"}	t	system	integration	2026-05-03 07:03:38.632641+00	2026-05-03 07:03:38.632641+00	\N	\N	\N	\N	sql	interval
0789155f-ad75-4849-bfa4-59f062aa208e	Assets Disappeared	assets-disappeared	operational	high	More than 10% of assets disappeared since the previous pipeline run.	WITH recent AS (\n     SELECT (stats->>'total_assets')::int as total_assets, completed_at,\n            LAG((stats->>'total_assets')::int) OVER (ORDER BY completed_at) as prev_total\n     FROM pipeline_runs\n     WHERE status = 'completed'\n       AND stats IS NOT NULL\n       AND stats->>'total_assets' IS NOT NULL\n     ORDER BY completed_at DESC LIMIT 2\n )\n SELECT pr.id, NULL as hostname, NULL as category, NULL as subcategory,\n        NULL as ip, NULL as os_normalized, pr.completed_at as last_time\n FROM pipeline_runs pr\n JOIN recent r ON pr.completed_at = r.completed_at\n WHERE r.prev_total > 0\n   AND (r.prev_total - r.total_assets)::float / r.prev_total > 0.10\n LIMIT 1	{"unit": "percent", "value": 10, "metric": "assets_disappeared_pct", "operator": ">"}	t	system	pipeline_run	2026-05-03 07:03:38.632641+00	2026-05-03 07:03:38.632641+00	\N	\N	\N	\N	sql	pipeline
2fc8408b-c268-4556-b191-1c200d9ef0a1	Pipeline stale	pipeline-stale	operational	critical	Fires when no successful asset-generation run has completed in 2x the expected cadence.	\N	\N	t	system	system	2026-05-03 07:03:40.231714+00	2026-05-03 07:03:40.231714+00	\N	\N	\N	\N	external	pipeline
f7eb6321-738b-481f-8377-862f73fbfdc2	Pipeline last run failed	pipeline-last-run-failed	operational	high	Fires when the most recently finished asset-generation run ended in failure.	\N	\N	t	system	system	2026-05-03 07:03:40.231714+00	2026-05-03 07:03:40.231714+00	\N	\N	\N	\N	external	pipeline
e2e93ab9-a20e-4d2b-b0cd-3d0c0b4d974f	Pipeline stuck	pipeline-stuck	operational	high	Fires when a pipeline run has been running (or pending) longer than the configured max expected duration.	\N	\N	t	system	system	2026-05-03 07:03:40.231714+00	2026-05-03 07:03:40.231714+00	\N	\N	\N	\N	external	pipeline
7c9e6a41-3d2b-4f8a-9e10-3a1b2c3d0031	Posture Tier Drop — Secured to Discovered	posture-tier-drop-secured-to-discovered	security	high	An asset dropped from Secured to Discovered posture in the last 24 hours — it lost the coverage that had it protected.	SELECT DISTINCT a.id, a.hostname[1] AS hostname, a.category, a.subcategory,\n       a.ip[1]::text AS ip, a.os_normalized, a.last_time\nFROM asset_chronicle ac\nJOIN assets a ON a.id = ac.asset_id\nWHERE ac.event_type = 'attribute_updated'\n  AND ac.time >= now() - interval '24 hours'\n  AND ac.attribute_changes ? 'posture_rules'\n  AND (ac.attribute_changes -> 'posture_rules' -> 'old' -> 0 -> 'secured'   ->> 'state') = 'true'\n  AND (ac.attribute_changes -> 'posture_rules' -> 'new' -> 0 -> 'secured'   ->> 'state') IS DISTINCT FROM 'true'\n  AND (ac.attribute_changes -> 'posture_rules' -> 'new' -> 0 -> 'monitored' ->> 'state') IS DISTINCT FROM 'true'	{"min":1}	t	system	asset	2026-07-03 00:00:00+00	2026-07-03 00:00:00+00	\N	\N	\N	\N	sql	pipeline
7c9e6a41-3d2b-4f8a-9e10-3a1b2c3d0032	Posture Tier Drop — Secured Coverage Fell Over 5%	posture-tier-drop-secured-to-monitored-pct	security	medium	The number of Secured assets fell more than 5% since the previous posture check.	WITH latest_two AS (\n    SELECT secured_count,\n           ROW_NUMBER() OVER (ORDER BY snapshot_at DESC, id DESC) AS rn\n    FROM posture_tier_snapshots\n),\ncur AS (SELECT secured_count FROM latest_two WHERE rn = 1),\nprev AS (SELECT secured_count FROM latest_two WHERE rn = 2)\nSELECT '7c9e6a41-3d2b-4f8a-9e10-3a1b2c3d0032'::uuid AS id, NULL AS hostname, NULL AS category,\n       NULL AS subcategory, NULL AS ip, NULL AS os_normalized, now() AS last_time\nFROM cur, prev\nWHERE prev.secured_count > 0\n  AND (prev.secured_count - cur.secured_count)::float / prev.secured_count * 100 > 5	{"metric":"secured_to_monitored_pct","operator":">","value":5,"unit":"percent"}	t	system	metric	2026-07-03 00:00:00+00	2026-07-03 00:00:00+00	\N	\N	\N	\N	sql	pipeline
7c9e6a41-3d2b-4f8a-9e10-3a1b2c3d003b	Security Coverage Below Floor (Server/Workstation)	ootb-security-coverage-floor	security	high	More than 5% of servers and workstations have no security tool (endpoint security or vulnerability management) covering them.	\N	{"condition":"exceeds","value":5,"unit":"percent"}	t	system	asset	2026-07-03 00:00:00+00	2026-07-03 00:00:00+00	[{"field":{"kind":"static","name":"Category"},"op":"any","value":["server","workstation"]}]	{"logic":"all","conditions":[{"field":{"kind":"static","name":"Data Source Category"},"op":"none","value":["endpoint security","vulnerability management"]}]}	\N	\N	filter	pipeline
7c9e6a41-3d2b-4f8a-9e10-3a1b2c3d003c	Asset With Public IP Address	ootb-public-ip-asset	security	high	This asset has a public, internet-routable IP address, which may expose it to the internet.	SELECT DISTINCT a.id, a.hostname[1] AS hostname, a.category, a.subcategory,\n       a.ip[1]::text AS ip, a.os_normalized, a.last_time\nFROM assets a\nWHERE EXISTS (\n    SELECT 1 FROM (\n        SELECT addr::inet AS ia\n        FROM unnest(a.ip) AS addr\n        WHERE addr ~ '^((25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\\.){3}(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])$'\n    ) v\n    WHERE NOT (v.ia <<= '0.0.0.0/8'::inet\n            OR v.ia <<= '10.0.0.0/8'::inet\n            OR v.ia <<= '100.64.0.0/10'::inet\n            OR v.ia <<= '127.0.0.0/8'::inet\n            OR v.ia <<= '169.254.0.0/16'::inet\n            OR v.ia <<= '172.16.0.0/12'::inet\n            OR v.ia <<= '192.168.0.0/16'::inet\n            OR v.ia <<= '224.0.0.0/4'::inet\n            OR v.ia <<= '240.0.0.0/4'::inet)\n)	{"min":1}	t	system	asset	2026-07-03 00:00:00+00	2026-07-03 00:00:00+00	\N	\N	\N	\N	sql	pipeline
7c9e6a41-3d2b-4f8a-9e10-3a1b2c3d003d	Pipeline Run Recorded No Changes	ootb-chronicle-silence	operational	high	The latest run found assets but logged no changes, which can mean it silently failed to track history.	WITH latest AS (\n    SELECT id, stats, completed_at\n    FROM pipeline_runs\n    WHERE status = 'completed'\n    ORDER BY created_at DESC\n    LIMIT 1\n)\nSELECT id, NULL AS hostname, NULL AS category, NULL AS subcategory,\n       NULL AS ip, NULL AS os_normalized, completed_at AS last_time\nFROM latest\nWHERE stats -> 'chronicle_events' IS NULL\n  AND (stats ->> 'total_assets')::int > 0	{"min":1}	t	system	pipeline_run	2026-07-03 00:00:00+00	2026-07-03 00:00:00+00	\N	\N	\N	\N	sql	pipeline
7c9e6a41-3d2b-4f8a-9e10-3a1b2c3d0041	Auth — Admin/Super-Admin Role Granted	auth-admin-role-granted	security	critical	A user was granted admin or super-admin access in the last 24 hours.	SELECT a.id AS subject_id,\n       (a.details ->> 'username') || ' → ' || (a.details ->> 'new_role') AS subject_label,\n       'was ' || (a.details ->> 'old_role') || ' · ' || (a.details ->> 'ip_address') AS subject_sublabel\nFROM audit_trail a\nWHERE a."timestamp" > now() - interval '24 hours'\n  AND a.category = 'auth'\n  AND a.resource_type = 'authentication'\n  AND a.action = 'success'\n  AND a.details ->> 'event' = 'user_updated'\n  AND a.details -> 'changes' ? 'role'\n  AND a.details ->> 'new_role' IN ('admin','super_admin')\n  AND COALESCE(a.details ->> 'old_role', '') NOT IN ('admin','super_admin')	{"min":1}	t	system	audit_event	2026-07-03 00:00:00+00	2026-07-04 00:00:00+00	\N	\N	1440	\N	sql	interval
\.


--
-- Data for Name: alerts; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.alerts (id, rule_id, type, severity, title, description, threshold, status, asset_count, assets_by_category, data_sources, asset_ids, source, created_at, updated_at, resolved_at, fingerprint, labels) FROM stdin;
\.


--
-- Data for Name: api_keys; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.api_keys (id, user_id, name, key_prefix, key_hash, expires_at, is_active, last_used_at, last_used_ip, created_at) FROM stdin;
\.


--
-- Data for Name: asset_conflicts; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.asset_conflicts (id, generation_run_id, conflict_type, severity, asset_id, component, conflicting_asset_id, evidence, status, resolution_strategy, resolved_at, resolved_by, resolution_notes, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: asset_id_mappings; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.asset_id_mappings (component, asset_id, created_at, updated_at, first_seen, last_seen) FROM stdin;
\.


--
-- Data for Name: asset_manual_tags; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.asset_manual_tags (id, asset_id, tag_name, tag_color, created_by, created_at) FROM stdin;
\.


--
-- Data for Name: asset_merges; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.asset_merges (id, primary_asset_id, secondary_asset_id, merge_strategy, merged_by, merged_at, field_resolutions) FROM stdin;
\.


--
-- Data for Name: asset_providers; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.asset_providers (id, asset_id, data_source, data_source_category, hostname, fqdn, ip, mac, sn, os, manufacturer, model, vendor_category, data_source_fields, updated_at, category_prediction, generation_run_id, integration_id, os_normalized, category, subcategory) FROM stdin;
\.


--
-- Data for Name: asset_providers_history; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.asset_providers_history (history_id, archived_at, generation_run_id, id, asset_id, data_source, data_source_category, hostname, fqdn, ip, mac, sn, os, manufacturer, model, vendor_category, data_source_fields, updated_at, category_prediction, os_eol_date, os_support_end_date, os_eol_status) FROM stdin;
\.


--
-- Data for Name: asset_tags_v2; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.asset_tags_v2 (id, asset_id, tag_name, tag_rule_id, applied_at, tag_type, execution_id) FROM stdin;
\.


--
-- Data for Name: assets; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.assets (id, asset, hostname, fqdn, ip, mac, sn, category, subcategory, os_normalized, mac_vendor_enriched, manufacturer, model, data_source, data_source_category, vendor_category, confidence, ipam_location, owner, location, user_field, extended_attributes, first_time, last_time, updated_at, os_enrichment, os, os_eol_date, os_support_end_date, os_extended_support_date, os_is_lts, os_eol_status, os_eol_checked_at, first_seen, last_seen, last_generation_run_id, merged_into, confidence_breakdown, tags, ip_primary, category_predicted, category_is_overridden, posture_rules, os_orig) FROM stdin;
\.


--
-- Data for Name: assets_history; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.assets_history (history_id, archived_at, generation_run_id, id, asset, hostname, fqdn, ip, mac, sn, category, subcategory, os_normalized, mac_vendor_enriched, manufacturer, model, data_source, data_source_category, vendor_category, confidence, ipam_location, owner, location, user_field, extended_attributes, first_time, last_time, updated_at, os_enrichment, os, first_seen, last_seen, last_generation_run_id, confidence_breakdown, os_orig) FROM stdin;
\.


--
-- Data for Name: audit_trail; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.audit_trail (id, "timestamp", user_id, username, method, path, status_code, ip_address, user_agent, request_body, response_summary, duration_ms, request_id) FROM stdin;
\.


--
-- Data for Name: batch_logs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.batch_logs (id, batch_id, log_level, message, details, created_at) FROM stdin;
\.


--
-- Data for Name: batches; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.batches (id, integration_id, status, trigger_type, started_at, completed_at, record_count, error_message, error_details, metadata, created_at) FROM stdin;
\.


--
-- Data for Name: category_overrides; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.category_overrides (asset_id, category, overridden_by, overridden_at, reason, subcategory, active, updated_at, features_snapshot) FROM stdin;
\.


--
-- Data for Name: chat_feedback; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.chat_feedback (id, message_id, user_id, rating, comment, created_at) FROM stdin;
\.


--
-- Data for Name: chat_messages; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.chat_messages (id, session_id, seq, role, content, tool_calls, tool_result_ref, input_tokens, output_tokens, error, created_at) FROM stdin;
\.


--
-- Data for Name: chat_sessions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.chat_sessions (id, user_id, title, model, provider, created_at, updated_at, deleted_at) FROM stdin;
\.


--
-- Data for Name: chat_tool_calls; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.chat_tool_calls (id, message_id, tool_name, arguments, result, result_bytes, duration_ms, status, error, created_at, artifact) FROM stdin;
\.


--
-- Data for Name: compliance_rules; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.compliance_rules (id, name, category, condition_operators, conditions, description, enabled, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: detection_alerts; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.detection_alerts (id, rule_id, execution_id, triggered_at, resolved_at, acknowledged_at, acknowledged_by, condition, threshold_value, actual_value, severity, status, message, metadata) FROM stdin;
\.


--
-- Data for Name: enrichment_datasets; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.enrichment_datasets (id, name, match_field, match_type, target_field, status, row_count, matched_count, uploaded_by, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: enrichment_rows; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.enrichment_rows (id, dataset_id, match_value, enrich_value) FROM stdin;
\.


--
-- Data for Name: excluded_records; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.excluded_records (id, generation_run_id, excluded_at, raw_record_id, integration_id, raw_source_id, data_source, batch_id, hostname, fqdn, ip, mac, sn, os, manufacturer, model, vendor_category, exclusion_reason, exclusion_category, has_hostname, has_ip, has_mac, has_sn, extended_fields, exclusion_details) FROM stdin;
\.


--
-- Data for Name: field_registry; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.field_registry (id, field_name, field_type, db_path, display_name, description, description_source, data_source, searchable, created_at, updated_at) FROM stdin;
1	hostname	static	hostname	Hostname	hostname - computer name, machine name, device name, server name, workstation name, PC name, node name, system name, NetBIOS name, DNS short name, DNS name. The unique name identifying a computer on a network (without domain suffix). Examples: SERVER01, LAPTOP-JOHN	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
2	ip	static	ip	IP Address	ip - IP address, network address, IPv4, IPv6, internet protocol, host IP, device IP, layer 3 address, network layer address. The logical network address. Examples: 192.168.1.100, 10.0.0.5	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
3	mac	static	mac	MAC Address	mac - MAC address, hardware address, physical address, ethernet address, NIC address, layer 2 address, data link address, burnt-in address, network interface card address. The hardware identifier (AA:BB:CC:DD:EE:FF format).	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
4	sn	static	sn	Serial Number	sn - serial number, serial, service tag, asset tag, hardware ID, chassis serial, product serial, S/N, id, identifier, unique number. The manufacturer tracking number on the device sticker.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
5	fqdn	static	fqdn	FQDN	fqdn - fully qualified domain name, full hostname, full DNS name, complete hostname, full domain name. The complete name with domain: server01.corp.company.com	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
6	category	static	category	Category	category - device type, asset type, classification, type, device class, asset class, hardware type. The high-level grouping: Server, Workstation, Laptop, Desktop, VM, Network Device, IoT, Printer, Mobile Device.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
7	subcategory	static	subcategory	Subcategory	subcategory - subtype, detailed type, specific classification, secondary category, device subtype. Granular classification like Web Server or Gaming Laptop within the main category.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
8	os_normalized	static	os_normalized	Operating System	os_normalized - operating system, OS, platform, system software, Windows, Linux, macOS, Ubuntu, Red Hat, OS version, OS type, system type. What software runs the computer.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
9	manufacturer	static	manufacturer	Manufacturer	manufacturer - vendor, make, brand, OEM, hardware vendor, producer, company name. Who made the hardware: Dell, HP, Cisco, Apple, Lenovo, VMware.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
10	model	static	model	Model	model - model number, product name, hardware model, device model, SKU, part number, form factor, chassis type. The specific product: PowerEdge R740, MacBook Pro, ThinkPad X1.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
11	location	static	location	Location	location - site, building, floor, room, rack, data center, datacenter, DC, rack location, physical location, where, office, branch, campus, facility. Where the device is physically placed.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
12	owner	static	owner	Owner	owner - assigned to, user, responsible, contact, department, team, custodian, manager, assignee, accountable, cost center, who owns, ownership. Who is accountable for this asset.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
13	data_source	static	data_source	Data Source	data_source - source, integration, provider, discovered by, origin, imported from, source system, discovery source. Which system discovered this asset.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
14	data_source_category	static	data_source_category	Data Source Category	data_source_category - source type, source category, integration type, discovery method. Category of the data source: EDR, CMDB, Directory Service, Network Scanner, Cloud Platform.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
15	first_seen	static	first_seen	First Seen	first_seen - discovered, created, added, first discovered, initial discovery, when added, discovery date, first detected, first found. When the asset was first found by AssetAtlas.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
16	last_seen	static	last_seen	Last Seen	last_seen - last active, last updated, recent, latest, last observation, last check-in, most recent, last activity. When the asset was most recently seen.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
17	confidence	static	confidence	Confidence	confidence - confidence score, score, quality, accuracy, certainty, reliability, data quality, trust score. How reliable the asset data is (0-100%).	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
18	os_eol_status	static	os_eol_status	EOL Status	os_eol_status - EOL status, end of life, end of support, supported, unsupported, lifecycle, support status, retired, decommissioned, patch status. Whether the OS is still supported.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
19	ipam_location	static	ipam_location	IPAM Location	ipam_location - IPAM, IP management, subnet, network location, network segment, IP location, subnet location. Location information from IP Address Management system based on network subnet assignments.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
20	mac_vendor_enriched	static	mac_vendor_enriched	MAC Vendor	mac_vendor_enriched - MAC vendor, NIC vendor, OUI, OUI vendor, OUI lookup, network card vendor, network card manufacturer, ethernet vendor. The vendor identified from MAC address OUI prefix lookup.	hardcoded	\N	t	2026-05-03 07:03:39.094141+00	2026-05-03 07:03:39.094141+00
21	ip_private	static	extended_attributes->'AssetAtlas'->'ip_private'	Private IP	ip_private - private IP, internal IP, RFC1918, private address, LAN IP, internal address, non-routable IP. IP addresses in private ranges (10.x, 172.16-31.x, 192.168.x, CGNAT 100.64.x, IPv6 ULA). Classified by the pipeline using Python ipaddress.is_global.	hardcoded	\N	t	2026-05-03 07:03:39.176682+00	2026-05-03 07:03:39.176682+00
22	ip_public	static	extended_attributes->'AssetAtlas'->'ip_public'	Public IP	ip_public - public IP, external IP, internet IP, globally routable IP, WAN IP, public address. IP addresses reachable on the public internet. Classified by the pipeline using Python ipaddress.is_global.	hardcoded	\N	t	2026-05-03 07:03:39.176682+00	2026-05-03 07:03:39.176682+00
23	ip_type	static	extended_attributes->'AssetAtlas'->>'ip_type'	IP Type	ip_type - IP type, IP classification, address type, network type, private or public. Summary classification of an asset's IPs: private_only (all internal), public_only (all internet-routable), mixed (both), none (no IPs).	hardcoded	\N	t	2026-05-03 07:03:39.176682+00	2026-05-03 07:03:39.176682+00
24	geo_countries	static	extended_attributes->'AssetAtlas'->'geo_countries'	Geo Country	geo_countries - country, geo country, geolocation country, IP country, location country, country code, ISO country, nation, territory. Countries associated with the asset's public IP addresses, derived from DB-IP geolocation lookup. Values are country names (e.g. "United States", "Germany"). Multiple countries possible when an asset has public IPs in different regions.	hardcoded	\N	t	2026-05-03 07:03:39.287568+00	2026-05-03 07:03:39.287568+00
25	geo_cities	static	extended_attributes->'AssetAtlas'->'geo_cities'	Geo City	geo_cities - city, geo city, geolocation city, IP city, location city, metro area, municipality, town. Cities associated with the asset's public IP addresses, derived from DB-IP geolocation lookup. Accuracy is typically metro-area level (~50 km). Multiple cities possible when an asset has public IPs in different locations.	hardcoded	\N	t	2026-05-03 07:03:39.287568+00	2026-05-03 07:03:39.287568+00
26	geo_regions	static	extended_attributes->'AssetAtlas'->'geo_regions'	Geo Region	geo_regions - region, geo region, state, province, subdivision, geolocation region, IP region, location region, territory. Regions or states associated with the asset's public IP addresses, derived from DB-IP geolocation lookup. Uses first-level administrative subdivisions (e.g. "California", "Bavaria", "New South Wales").	hardcoded	\N	t	2026-05-03 07:03:39.287568+00	2026-05-03 07:03:39.287568+00
27	os_eol_date	static	os_eol_date	OS EOL Date	os_eol_date - EOL date, end of life date, end of support date, OS EOL date, support end date, retirement date, sunset date. The date when the asset's operating system reaches end of life and stops receiving security updates. Derived from OS normalization and EOL reference data during the pipeline.	hardcoded	\N	t	2026-05-03 07:03:39.316229+00	2026-05-03 07:03:39.316229+00
28	os_is_lts	static	os_is_lts	OS Is LTS	os_is_lts - LTS, long term support, is LTS, OS LTS, LTSC, long term servicing, stable release, extended support. Whether the asset's operating system is a Long Term Support release. LTS releases receive extended security updates and have longer support lifecycles. Derived from OS normalization during the pipeline.	hardcoded	\N	t	2026-05-03 07:03:39.316229+00	2026-05-03 07:03:39.316229+00
29	user_field	static	user_field	User Field	user_field - user field, custom field, user defined, user data, custom attribute, user note, user label. A free-text field that can be set per asset by users for custom categorization, notes, or metadata not captured by other fields.	hardcoded	\N	t	2026-05-03 07:03:39.316229+00	2026-05-03 07:03:39.316229+00
\.


--
-- Data for Name: ingestion_batches; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.ingestion_batches (id, integration_id, status, raw_records_count, mapped_records_count, failed_records_count, started_at, mapping_started_at, completed_at, error_message, asset_generation_started_at, asset_generation_completed_at, assets_created_count, assets_updated_count, asset_generation_status, mapping_hash) FROM stdin;
\.


--
-- Data for Name: integration_groups; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.integration_groups (id, name, integration_type, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: integration_technologies; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.integration_technologies (id, name, category, logo_path, direct_connector, siem_passthrough_supported, default_transports, description, created_at, updated_at) FROM stdin;
aruba	Aruba	Vendor	/logos/aruba.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Aruba network data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
aternity	Aternity	Vendor	/logos/aternity.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Aternity DEM data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
avaya	Avaya	Vendor	/logos/avaya.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Avaya communications data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
aws	AWS	Vendor	/logos/aws.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull AWS asset data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
azure	Azure	Vendor	/logos/azure.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Azure asset data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
check-point	Check Point	Vendor	/logos/checkpoint.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Check Point security data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
cisco	Cisco	Vendor	/logos/cisco.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Cisco asset data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
citrix	Citrix	Vendor	/logos/citrix.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Citrix data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
claroty	Claroty	Vendor	/logos/claroty.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Claroty OT/IoT data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
palo-alto-cortex	Palo Alto Cortex	Vendor	/logos/cortex.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Palo Alto Cortex XDR data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
cybereason	Cybereason	Vendor	/logos/cybereason.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Cybereason EDR data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
cylance	Cylance	Vendor	/logos/cylance.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Cylance EDR data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
datadog	Datadog	Vendor	/logos/datadog.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Datadog asset data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
dynatrace	Dynatrace	Vendor	/logos/dynatrace.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Dynatrace data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
eset	ESET	Vendor	/logos/eset.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull ESET endpoint data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
forescout	Forescout	Vendor	/logos/forescout.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Forescout NAC data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
fortinet	Fortinet	Vendor	/logos/fortinet.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Fortinet security data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
google-cloud	Google Cloud	Vendor	/logos/google_cloud.svg	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull GCP asset data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
hp	HP	Vendor	/logos/hp.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull HP device data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
huawei	Huawei	Vendor	/logos/huawei.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Huawei device data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
ibm	IBM	Vendor	/logos/ibm.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull IBM data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
ivanti	Ivanti	Vendor	/logos/ivanti.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Ivanti endpoint/UEM data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
kaspersky	Kaspersky	Vendor	/logos/kaspersky.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Kaspersky endpoint data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
lenovo	Lenovo	Vendor	/logos/lenovo.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Lenovo device data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
malwarebytes	Malwarebytes	Vendor	/logos/malwarebytes.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Malwarebytes endpoint data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
manageengine	ManageEngine	Vendor	/logos/manage_engine.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull ManageEngine ITSM data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
mcafee	McAfee	Vendor	/logos/mcafee.webp	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull McAfee endpoint data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
new-relic	New Relic	Vendor	/logos/new_relic.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull New Relic data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
nozomi-networks	Nozomi Networks	Vendor	/logos/nozomi.webp	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Nozomi OT/IoT data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
oracle	Oracle	Vendor	/logos/oracle.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Oracle data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
palo-alto-networks	Palo Alto Networks	Vendor	/logos/paloalto.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Palo Alto Networks firewall data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
prtg	PRTG	Vendor	/logos/prtg.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull PRTG monitoring data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
rapid7	Rapid7	Vendor	/logos/rapid7.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Rapid7 vuln/EDR data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
ricoh	Ricoh	Vendor	/logos/ricoh.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Ricoh device data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
rockwell-automation	Rockwell Automation	Vendor	/logos/rockwell automation.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Rockwell Automation OT data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
samsung	Samsung	Vendor	/logos/samsung.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Samsung device data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
sccm	Microsoft SCCM / MECM	Vendor	/logos/sccm.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull SCCM/MECM inventory data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
snow-software	Snow Software	Vendor	/logos/snow software.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Snow Software SAM data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
snowflake	Snowflake	Vendor	/logos/snowflake.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Snowflake-hosted asset data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
sonicwall	SonicWall	Vendor	/logos/sonicwall.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull SonicWall security data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
sophos	Sophos	Vendor	/logos/sophos.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Sophos endpoint data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
symantec	Symantec	Vendor	/logos/symantec.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Symantec endpoint data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
tanium	Tanium	Vendor	/logos/tanium.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Tanium endpoint data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
tenable	Tenable	Vendor	/logos/tenable.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Tenable vuln data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
trellix	Trellix	Vendor	/logos/trellix.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Trellix endpoint data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
trend-micro	Trend Micro	Vendor	/logos/trend_micro.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Trend Micro endpoint data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
vmware	VMware	Vendor	/logos/vmware.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull VMware data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
zabbix	Zabbix	Vendor	/logos/zabbix.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Zabbix monitoring data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
zscaler	Zscaler	Vendor	/logos/zscaler.svg	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Zscaler proxy data via SIEM/datalake	2026-05-03 07:03:40.503023+00	2026-05-03 07:03:40.503023+00
windows-events	Windows Events	Vendor	/logos/windows.svg	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Pull Windows Event Log data via SIEM/datalake	2026-05-27 00:00:00+00	2026-05-27 00:00:00+00
custom	Custom (via SIEM/datalake)	Vendor	/logos/assetatlas.png	\N	t	{splunk,log_analytics,databricks,sentinelone_siem}	Ingest custom data via SIEM/datalake	2026-05-27 00:00:00+00	2026-05-27 00:00:00+00
\.


--
-- Data for Name: integrations; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.integrations (id, name, integration_type, category, status, config, field_mapping, last_sync_at, last_sync_status, last_error, sync_count, created_at, updated_at, schedule_enabled, schedule_cron, schedule_timezone, next_run_at, is_running, logo, description, last_connection_test_at, last_connection_test_status, last_connection_test_message, transformations, data_filters, group_id, source_method, preserve_fields) FROM stdin;
\.


--
-- Data for Name: ipam_networks; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.ipam_networks (id, network, location, description, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: job_executions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.job_executions (id, job_id, job_name, execution_id, started_at, completed_at, duration_seconds, status, records_processed, error_message, message, attempt_number, max_attempts, created_at) FROM stdin;
\.


--
-- Data for Name: mapping_errors; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.mapping_errors (id, batch_id, raw_source_id, error_type, error_message, field_name, raw_data, created_at) FROM stdin;
\.


--
-- Data for Name: merge_history; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.merge_history (id, merge_date, merged_from, merged_into, generation_run_id, confidence_score, evidence, performed_by, pre_merge_snapshot, reversible_until, reversed_at, reversal_reason, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: merge_prevention_rules; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.merge_prevention_rules (id, rule_name, description, rule_type, rule_definition, enabled, priority, created_by, created_at, updated_at, last_evaluated_at, evaluation_count, violation_count) FROM stdin;
\.


--
-- Data for Name: password_reset_tokens; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.password_reset_tokens (id, user_id, token_hash, expires_at, created_at, used_at, token_type) FROM stdin;
\.


--
-- Data for Name: pipeline_runs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.pipeline_runs (id, triggered_by, status, current_stage, started_at, completed_at, duration_seconds, stats, error_message, created_at) FROM stdin;
\.


--
-- Data for Name: posture_categories; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.posture_categories (id, name, description, icon, applicable_categories, display_order, created_at, last_evaluated_at) FROM stdin;
vulnerability_scanning	Vulnerability Scanning	Assets covered by vulnerability scanning tools	M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z	["server", "workstation", "network"]	2	2026-05-03 07:03:39.623834+00	\N
domain_joined	Domain Joined	Assets joined to an Active Directory domain	M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z	["server", "workstation"]	3	2026-05-03 07:03:39.623834+00	\N
device_management	Device Management	Assets enrolled in device management (MDM/UEM)	M17 1.01L7 1c-1.1 0-2 .9-2 2v18c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2V3c0-1.1-.9-1.99-2-1.99zM17 19H7V5h10v14z	["mobile"]	4	2026-05-03 07:03:39.623834+00	\N
monitored	Monitored	Assets visible to monitoring or SIEM platforms	M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z	["server", "ot", "network"]	5	2026-05-03 07:03:39.623834+00	\N
secured	Secured	Assets with active endpoint detection and response (EDR) agents	M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z	["server", "workstation"]	1	2026-05-03 07:03:39.623834+00	\N
\.


--
-- Data for Name: posture_sub_rule_history; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.posture_sub_rule_history (id, sub_rule_id, category_id, action, before_state, after_state, changed_by, changed_by_username, created_at) FROM stdin;
\.


--
-- Data for Name: posture_sub_rules; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.posture_sub_rules (id, category_id, name, description, enabled, target_filters, compliance_criteria, created_at, updated_at, output_value, output_mode, display_order, output_field) FROM stdin;
fddde466-18cc-4901-80e0-229d29cdafc5	secured	Endpoint security coverage	Asset is seen by an endpoint-security data source with at least medium confidence and activity in the last 15 days.	t	[{"value": "server,workstation", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]	{"logic": "all", "conditions": [{"value": "endpoint security", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}, {"value": "medium,high", "field_id": {"kind": "virtual", "name": "confidence_level"}, "operator": "any"}, {"value": "15d", "field_id": {"kind": "static", "name": "Last Seen"}, "operator": "within_last"}]}	2026-05-03 07:03:40.12438+00	2026-05-03 07:03:40.12438+00	\N	\N	0	\N
07673b1d-9a50-4f75-8202-f0a918552343	secured	Endpoint security coverage (legacy EDR category)	Asset is seen by a data source still using the legacy EDR category, with at least medium confidence and activity in the last 15 days.	t	[{"value": "server,workstation", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]	{"logic": "all", "conditions": [{"value": "EDR", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}, {"value": "medium,high", "field_id": {"kind": "virtual", "name": "confidence_level"}, "operator": "any"}, {"value": "15d", "field_id": {"kind": "static", "name": "Last Seen"}, "operator": "within_last"}]}	2026-05-03 07:03:40.12438+00	2026-05-03 07:03:40.12438+00	\N	\N	0	\N
d1bd165e-e616-4e2c-ad1b-a5f72affcedf	vulnerability_scanning	Vulnerability scanning coverage	Asset is seen by at least one vulnerability-management data source.	t	[{"value": "server,workstation,network", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]	{"logic": "any", "conditions": [{"value": "vulnerability management", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}]}	2026-05-03 07:03:40.12438+00	2026-05-03 07:03:40.12438+00	\N	\N	0	\N
e5c33177-fa23-4c90-b715-6cfd7e625af5	device_management	MDM enrollment	Mobile asset is seen by at least one mobile-device-management data source.	t	[{"value": "mobile", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]	{"logic": "any", "conditions": [{"value": "mobile device management", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}]}	2026-05-03 07:03:40.12438+00	2026-05-03 07:03:40.12438+00	\N	\N	0	\N
428b8760-0851-4e9b-82b8-5b387d9b64fb	monitored	Monitoring coverage	Asset is seen by an observability, SIEM, or network-monitoring data source.	t	[{"value": "server,ot,network", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]	{"logic": "any", "conditions": [{"value": "observability", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}, {"value": "SIEM", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}, {"value": "Network Monitoring", "field_id": {"kind": "static", "name": "Data Source Category"}, "operator": "contains"}]}	2026-05-03 07:03:40.12438+00	2026-05-03 07:03:40.12438+00	\N	\N	0	\N
84338835-0a51-4254-9c8c-5992515647c9	domain_joined	Active Directory domain membership	Windows server or workstation seen by an Active Directory data source with at least medium confidence and activity in the last 15 days.	t	[{"value": "server,workstation", "field_id": {"kind": "static", "name": "Category"}, "operator": "any"}]	{"logic": "all", "conditions": [{"value": "windows", "field_id": {"kind": "static", "name": "OS Normalized"}, "operator": "contains"}, {"value": "Active Directory", "field_id": {"kind": "static", "name": "Data Source"}, "operator": "contains"}, {"value": "medium,high", "field_id": {"kind": "virtual", "name": "confidence_level"}, "operator": "any"}, {"value": "15d", "field_id": {"kind": "static", "name": "Last Seen"}, "operator": "within_last"}]}	2026-05-03 07:03:40.12438+00	2026-05-03 07:03:40.12438+00	\N	\N	0	\N
\.


--
-- Data for Name: posture_tier_snapshots; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.posture_tier_snapshots (id, snapshot_at, secured_count, monitored_count, discovered_count) FROM stdin;
\.


--
-- Data for Name: raw_asset_data; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.raw_asset_data (id, integration_id, raw_source_id, hostname, fqdn, ip, mac, sn, os, manufacturer, model, vendor_category, extended_fields, batch_id, ingested_at, mapped_at, first_time, last_time) FROM stdin;
\.


--
-- Data for Name: raw_source_data; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.raw_source_data (id, integration_id, raw_payload, batch_id, ingested_at, source_record_id) FROM stdin;
\.


--
-- Data for Name: reference_data_updates; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.reference_data_updates (id, bundle_version, source, initiated_by, started_at, finished_at, status, error_message, datasets) FROM stdin;
\.


--
-- Data for Name: reports; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.reports (id, title, description, charts, created_at, created_by, updated_at, updated_by, is_default) FROM stdin;
\.


--
-- Data for Name: reports_audit_log; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.reports_audit_log (id, report_id, action, changed_by, changed_by_username, changed_at, changes) FROM stdin;
\.


--
-- Data for Name: rule_execution_history; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.rule_execution_history (id, rule_id, executed_at, execution_time_ms, status, error_message, assets_matched, assets_passed, assets_failed, tags_applied, tags_removed, triggered_by, triggered_by_user, generation_run_id) FROM stdin;
\.


--
-- Data for Name: settings_audit_log; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.settings_audit_log (id, setting_id, category, key, old_value, new_value, changed_by, changed_by_username, change_reason, ip_address, created_at) FROM stdin;
\.


--
-- Data for Name: split_history; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.split_history (id, split_date, split_from, split_into, split_reason, component_distribution, performed_by, approval_required, approved_by, approved_at, prevent_auto_remerge, remerge_prevention_expires_at, notes, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: split_prevention; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.split_prevention (id, asset_id_1, asset_id_2, split_history_id, created_by, reason, expires_at, created_at) FROM stdin;
\.


--
-- Data for Name: system_dashboard_preferences; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.system_dashboard_preferences (id, working_dashboard, last_modified, last_modified_by) FROM stdin;
\.


--
-- Data for Name: system_info; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.system_info (id, installed_version, installed_at, last_updated_at, runtime, install_mode, opt_in_monitoring, opt_in_llm, llm_model, install_dir, install_log_path) FROM stdin;
1	0.0.0-pre-system-info	2026-05-03 07:03:40.400571+00	2026-05-03 07:03:40.400571+00	docker	online	f	f	\N	/opt/assetatlas	/var/log/assetatlas/install.log
\.


--
-- Data for Name: system_settings; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.system_settings (id, category, key, value, value_type, default_value, description, validation_rule, is_sensitive, requires_restart, created_at, updated_at, updated_by) FROM stdin;
1	database	DB_SLOW_QUERY_THRESHOLD_MS	100	integer	100	Threshold in milliseconds for logging slow queries	{"min": 1, "max": 10000}	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
2	database	DB_ENABLE_QUERY_LOGGING	true	boolean	true	Enable database query logging	\N	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
3	database	DB_LOG_ALL_QUERIES	false	boolean	false	Log ALL queries (very verbose, use with caution)	\N	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
4	cache	CACHE_ENABLE_LOGGING	true	boolean	true	Enable cache operation logging	\N	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
5	cache	CACHE_LOG_HITS	false	boolean	false	Log cache hits (verbose, useful for debugging)	\N	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
6	performance	PERF_ENABLE_TRACKING	true	boolean	true	Enable performance tracking and logging	\N	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
7	performance	PERF_ENABLE_BUDGET_WARNINGS	true	boolean	true	Enable warnings when endpoints exceed performance budgets	\N	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
8	performance	PERF_DEFAULT_BUDGET_MS	1000	integer	1000	Default performance budget in milliseconds	{"min": 100, "max": 10000}	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
9	performance	PERF_ENDPOINT_BUDGETS	{}	json	{}	Per-endpoint performance budgets (JSON)	\N	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
10	general	LOG_LEVEL	INFO	string	INFO	Application log level	{"enum": ["DEBUG", "INFO", "WARNING", "ERROR"]}	f	t	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
11	general	LOG_REQUEST_BODY_ON_ERROR	true	boolean	true	Log request body when errors occur	\N	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
12	retention	LOG_RETENTION_DAYS	30	integer	30	Application log retention period in days	{"min": 7, "max": 365}	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
13	retention	LOG_AUTO_CLEANUP_ENABLED	true	boolean	true	Enable automatic log cleanup	\N	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
14	retention	LOG_ROTATION_SIZE_MB	100	integer	100	Log file rotation size in megabytes	{"min": 10, "max": 1000}	f	t	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
15	retention	AUDIT_LOG_RETENTION_DAYS	365	integer	365	Audit log retention period in days (minimum 90 for compliance)	{"min": 90, "max": 730}	f	f	2026-05-03 07:03:38.776701	2026-05-03 07:03:38.776701	\N
16	retention	AUDIT_TRAIL_RETENTION_DAYS	90	integer	90	Number of days to retain audit trail records	{"min": 7, "max": 730}	f	f	2026-05-03 07:03:38.985287	2026-05-03 07:03:38.985287	\N
17	retention	AUDIT_TRAIL_LOG_READS	false	boolean	false	Whether to log GET/read requests in the audit trail	\N	f	f	2026-05-03 07:03:38.985287	2026-05-03 07:03:38.985287	\N
18	retention	RAW_SOURCE_DATA_RETENTION_DAYS	90	integer	90	Number of days to retain raw source data from integrations	{"min": 7, "max": 730}	f	f	2026-05-03 07:03:38.985287	2026-05-03 07:03:38.985287	\N
19	retention	ASSET_CHRONICLE_RETENTION_DAYS	90	integer	90	Number of days to retain asset chronicle (history) records	{"min": 7, "max": 730}	f	f	2026-05-03 07:03:38.985287	2026-05-03 07:03:38.985287	\N
20	retention	EXCLUDED_RECORDS_RETENTION_DAYS	90	integer	90	Number of days to retain excluded asset records	{"min": 7, "max": 730}	f	f	2026-05-03 07:03:38.985287	2026-05-03 07:03:38.985287	\N
21	retention	BATCH_DATA_RETENTION_DAYS	90	integer	90	Number of days to retain batch processing data	{"min": 7, "max": 730}	f	f	2026-05-03 07:03:38.985287	2026-05-03 07:03:38.985287	\N
23	retention	ASSETS_HISTORY_RETENTION_DAYS	90	integer	90	Keep asset history snapshots from the last N days	\N	f	f	2026-05-03 07:03:39.150567	2026-05-03 07:03:39.150567	\N
24	retention	ASSETS_HISTORY_RETENTION_COUNT	5	integer	5	Keep latest N asset history snapshots (regardless of age)	\N	f	f	2026-05-03 07:03:39.150567	2026-05-03 07:03:39.150567	\N
25	retention	ASSET_PROVIDERS_HISTORY_RETENTION_DAYS	90	integer	90	Keep asset providers history snapshots from the last N days	\N	f	f	2026-05-03 07:03:39.150567	2026-05-03 07:03:39.150567	\N
26	retention	ASSET_PROVIDERS_HISTORY_RETENTION_COUNT	5	integer	5	Keep latest N asset providers history snapshots (regardless of age)	\N	f	f	2026-05-03 07:03:39.150567	2026-05-03 07:03:39.150567	\N
27	retention	BATCH_RETENTION_DAYS	7	integer	7	Ingestion batch retention — keep batches from the last N days	\N	f	f	2026-05-03 07:03:39.229702	2026-05-03 07:03:39.229702	\N
28	retention	BATCH_RETENTION_COUNT	5	integer	5	Ingestion batch retention — keep latest N batches per integration (regardless of age)	\N	f	f	2026-05-03 07:03:39.229702	2026-05-03 07:03:39.229702	\N
29	email	enabled	false	boolean	false	Master toggle for outbound email.	\N	f	f	2026-05-03 07:03:40.177446	2026-05-03 07:03:40.177446	\N
30	email	smtp_host		string		SMTP relay hostname.	\N	f	f	2026-05-03 07:03:40.177446	2026-05-03 07:03:40.177446	\N
31	email	smtp_port	587	integer	587	SMTP relay port.	{"min": 1, "max": 65535}	f	f	2026-05-03 07:03:40.177446	2026-05-03 07:03:40.177446	\N
32	email	smtp_security	starttls	string	starttls	Transport security mode.	{"enum": ["none", "starttls", "tls"]}	f	f	2026-05-03 07:03:40.177446	2026-05-03 07:03:40.177446	\N
33	email	smtp_username		string		SMTP auth username (blank for unauthenticated relay).	\N	f	f	2026-05-03 07:03:40.177446	2026-05-03 07:03:40.177446	\N
34	email	smtp_password		string		SMTP auth password; stored encrypted at rest via Fernet helper.	\N	t	f	2026-05-03 07:03:40.177446	2026-05-03 07:03:40.177446	\N
35	email	from_address		string		From address on outbound mail.	\N	f	f	2026-05-03 07:03:40.177446	2026-05-03 07:03:40.177446	\N
36	email	from_display_name	AssetAtlas	string	AssetAtlas	Display name shown alongside the From address.	\N	f	f	2026-05-03 07:03:40.177446	2026-05-03 07:03:40.177446	\N
37	email	reply_to_address		string		Optional Reply-To address.	\N	f	f	2026-05-03 07:03:40.177446	2026-05-03 07:03:40.177446	\N
38	email	frontend_url		string		Public base URL used to build links in outbound emails.	\N	f	f	2026-05-03 07:03:40.177446	2026-05-03 07:03:40.177446	\N
39	auth	password_reset_rate_limit_per_ip_per_hour	5	integer	5	Maximum number of password-reset request submissions accepted per source IP per rolling hour. Protects against email enumeration and spam.	{"min": 1, "max": 1000}	f	f	2026-05-03 07:03:40.293545	2026-05-03 07:03:40.293545	\N
41	auth	invite_token_ttl_hours	168	integer	168	Lifetime of admin-issued user-onboarding invite tokens, in hours. Default 168 = 7 days.	{"min": 1, "max": 720}	f	f	2026-05-03 07:03:40.293545	2026-05-03 07:03:40.293545	\N
42	auth	reset_token_ttl_hours	1	integer	1	Lifetime of self-service password-reset tokens, in hours.	{"min": 1, "max": 24}	f	f	2026-05-03 07:03:40.293545	2026-05-03 07:03:40.293545	\N
43	auth	token_verify_rate_limit_per_ip_per_hour	20	integer	20	Maximum number of public token-handling requests (password-reset verify, onboarding validate, onboarding complete) accepted per source IP per rolling hour. Default 20 is lenient enough for legitimate wizard page reloads while still discouraging abuse.	{"min": 1, "max": 1000}	f	f	2026-05-03 07:03:40.320372	2026-05-03 07:03:40.320372	\N
44	ldap	enabled	false	boolean	false	Master toggle for LDAP authentication.	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
45	ldap	server_url		string		LDAP server URL (ldap://host:389 or ldaps://host:636).	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
46	ldap	use_ssl	true	boolean	true	Use LDAPS (TLS). Disabling means cleartext bind credentials over the wire.	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
47	ldap	verify_cert	true	boolean	true	Validate server TLS certificate. Disable only for self-signed dev environments.	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
48	ldap	connect_timeout_seconds	10	integer	10	LDAP connection + bind timeout in seconds.	{"min": 1, "max": 60}	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
49	ldap	bind_dn		string		Service account DN used to search for users (e.g., CN=svc_assetatlas,OU=Service Accounts,DC=corp,DC=com).	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
50	ldap	bind_password		string		Service account password; stored encrypted at rest via Fernet helper.	\N	t	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
51	ldap	base_dn		string		Base DN for user search (e.g., DC=corp,DC=com).	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
52	ldap	user_search_filter	(&(objectClass=person)(|(sAMAccountName={username})(mail={username})(userPrincipalName={username})))	string	(&(objectClass=person)(|(sAMAccountName={username})(mail={username})(userPrincipalName={username})))	LDAP filter to find a user by their submitted username. The literal "{username}" is replaced with the safely-escaped input.	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
53	ldap	attribute_mail	mail	string	mail	LDAP attribute that holds the user's email address.	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
54	ldap	attribute_full_name	displayName	string	displayName	LDAP attribute that holds the user's display name (cn is a common fallback).	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
55	ldap	attribute_groups	memberOf	string	memberOf	LDAP attribute listing the groups the user belongs to (multi-valued, full DN per value).	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
56	ldap	group_to_role_mapping	{}	json	{}	JSON object mapping LDAP group DN (case-insensitive) to AssetAtlas role. Example: {"CN=AssetAtlas Admins,OU=Groups,DC=corp,DC=com": "admin"}. Roles: user|admin|super_admin.	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
57	ldap	default_role	user	string	user	Role assigned when no group in group_to_role_mapping matches the user.	{"enum": ["user", "admin", "super_admin"]}	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
58	ldap	allow_local_fallback	true	boolean	true	When LDAP rejects credentials, fall through to local password verification. Disable to enforce LDAP-only logins.	\N	f	f	2026-05-03 07:03:40.34643	2026-05-03 07:03:40.34643	\N
59	oidc	enabled	false	boolean	false	Master toggle for OIDC SSO authentication.	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
60	oidc	provider_name	Single sign-on	string	Single sign-on	Display label on the LoginPage button (e.g., "Microsoft Entra", "Google", "Single sign-on").	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
61	oidc	discovery_url		string		OIDC discovery document URL (e.g., https://login.microsoftonline.com/<tenant-id>/v2.0/.well-known/openid-configuration).	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
62	oidc	client_id		string		OIDC application (client) ID issued by the identity provider.	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
63	oidc	client_secret		string		OIDC client secret; stored encrypted at rest via Fernet helper.	\N	t	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
64	oidc	redirect_uri		string		Public callback URL registered in the IdP (e.g., https://app.example.com/api/auth/oidc/callback). MUST match what is configured in the IdP exactly.	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
65	oidc	frontend_complete_url	/oidc-complete	string	/oidc-complete	Frontend route the callback redirects to with the one-time exchange code (relative path).	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
66	oidc	scopes	openid email profile	string	openid email profile	Space-separated OIDC scopes to request. Add a groups scope if your provider exposes group claims (e.g., "openid email profile groups" for some IdPs, or no extra scope for Entra with groups in the token).	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
67	oidc	claim_mail	email	string	email	ID token claim that holds the user's email address (Entra: "email" or "preferred_username").	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
68	oidc	claim_name	name	string	name	ID token claim that holds the user's display name.	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
69	oidc	claim_groups	groups	string	groups	ID token claim that holds the user's groups (multi-valued; values typically full DNs or group object IDs depending on IdP).	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
70	oidc	group_to_role_mapping	{}	json	{}	JSON object mapping group claim values (case-insensitive) to AssetAtlas roles. Example: {"00000000-0000-0000-0000-000000000001": "admin"}. Roles: user|admin|super_admin.	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
71	oidc	default_role	user	string	user	Role assigned when no group in group_to_role_mapping matches the user.	{"enum": ["user", "admin", "super_admin"]}	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
72	oidc	allow_local_fallback	true	boolean	true	Continue to allow local password authentication on the LoginPage. Disable to require SSO for everyone.	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
73	oidc	auto_create_users	true	boolean	true	Provision local user records on first successful SSO sign-in. Disable to require admins to pre-create users by email.	\N	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
74	oidc	http_timeout_seconds	10	integer	10	HTTP timeout for outbound calls to the IdP (discovery, token exchange, JWKS).	{"min": 1, "max": 60}	f	f	2026-05-03 07:03:40.373156	2026-05-03 07:03:40.373156	\N
75	oidc	allowed_email_domains		string		Comma-separated list of email domains permitted to auto-provision a local account via OIDC (e.g., "example.com,corp.io"). Empty = all domains allowed. Only consulted when oidc.auto_create_users = true.	\N	f	f	2026-05-03 07:03:40.427328	2026-05-03 07:03:40.427328	\N
76	auth	password_expiration_days	180	integer	180	Number of days a local-account password remains valid before the user is required to change it on next login. 0 = never expires (recommended per NIST 800-63B). Has no effect on LDAP or OIDC users.	{"min": 0, "max": 3650}	f	f	2026-05-05 00:00:00.000000	2026-05-05 00:00:00.000000	\N
77	asset_generation	removal_grace_period_days	30	integer	30	Days an asset absent from all inputs is kept (carried forward, silent) before it is hard-removed. 0 = remove immediately (legacy). Effective value is capped by mapping_expiration_days.	\N	f	f	2026-06-24 00:00:00+00	2026-06-24 00:00:00+00	\N
78	chat	tool_routing_enabled	false	boolean	false	Advertise only a core tool set + question-relevant tools to the chat LLM (with find_tools recovery), instead of all tools. Reduces input tokens per turn.	\N	f	f	2026-06-25 00:00:00+00	2026-06-25 00:00:00+00	\N
79	chat	tool_routing_top_k	5	integer	5	How many question-relevant gated tools to advertise per turn when tool_routing_enabled is on.	{"min": 1, "max": 20}	f	f	2026-06-25 00:00:00+00	2026-06-25 00:00:00+00	\N
80	system	enable_update_check	false	boolean	false	Allow a daily check against the public release feed for a newer AssetAtlas version. Off by default; makes no outbound network call while disabled (safe for air-gapped installs).	\N	f	f	2026-07-03 00:00:00+00	2026-07-03 00:00:00+00	\N
81	alerting	DATA_SOURCE_STALE_GRACE_MINUTES	60	integer	60	Minutes an integration may be past its own scheduled sync time before the Data Source Stale alert fires.	{"min": 5, "max": 10080}	f	f	2026-07-29 00:00:00+00	2026-07-29 00:00:00+00	\N
82	alerting	DATA_SOURCE_MAX_SYNC_AGE_HOURS	48	integer	48	Hours an integration may go without any sync at all before the Data Source Stale alert fires, regardless of its schedule.	{"min": 1, "max": 8760}	f	f	2026-07-29 00:00:00+00	2026-07-29 00:00:00+00	\N
\.


--
-- Data for Name: tag_rules; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.tag_rules (id, name, description, type, enabled, created_at, last_modified, last_executed_at, execution_count, last_execution_duration_ms, last_execution_assets_matched, last_execution_assets_passed, last_execution_assets_failed, target_filters, tags, compliance_criteria, detection_config) FROM stdin;
\.


--
-- Data for Name: teams_account_links; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.teams_account_links (id, aad_object_id, tenant_id, user_id, linked_at, linked_via_ip) FROM stdin;
\.


--
-- Data for Name: teams_conversation_sessions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.teams_conversation_sessions (id, conversation_id, user_id, session_id, created_at, last_active_at) FROM stdin;
\.


--
-- Data for Name: teams_link_used_jtis; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.teams_link_used_jtis (jti, consumed_at) FROM stdin;
\.


--
-- Data for Name: user_mfa; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_mfa (user_id, mfa_enabled, mfa_secret, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: user_sessions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_sessions (id, user_id, refresh_token_hash, access_token_jti, expires_at, ip_address, user_agent, is_active, created_at, last_used_at) FROM stdin;
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: -
--
-- Admin user is seeded in a LOCKED state (matching migration 025):
--   * is_active = false
--   * password_hash is a deliberately invalid bcrypt sentinel
-- Activate it via the installer (deploy/installer/install.sh) or
-- scripts/reset_admin_password.py <password>.

COPY public.users (id, username, email, password_hash, full_name, role, is_active, email_verified, must_change_password, failed_login_attempts, locked_until, last_login_at, created_at, updated_at, created_by) FROM stdin;
5ffc4827-6d69-46d4-b5b8-7084009f14a2	admin	admin@assetatlas.local	$2b$12$LOCKED.RUN.INSTALLER.OR.RESET.SCRIPT.TO.SET.A.REAL.PASSWORD	System Administrator	super_admin	f	t	t	0	\N	\N	2026-05-03 07:03:38.253485+00	2026-05-03 07:03:38.253485+00	\N
\.


--
-- Name: asset_chronicle_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.asset_chronicle_id_seq', 1, false);


--
-- Name: asset_providers_history_history_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.asset_providers_history_history_id_seq', 1, false);


--
-- Name: asset_providers_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.asset_providers_id_seq', 1, false);


--
-- Name: asset_tags_v2_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.asset_tags_v2_id_seq', 1, false);


--
-- Name: assets_history_history_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.assets_history_history_id_seq', 1, false);


--
-- Name: audit_trail_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.audit_trail_id_seq', 1, false);


--
-- Name: batch_logs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.batch_logs_id_seq', 1, false);


--
-- Name: compliance_rules_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.compliance_rules_id_seq', 1, false);


--
-- Name: detection_alerts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.detection_alerts_id_seq', 1, false);


--
-- Name: excluded_records_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.excluded_records_id_seq', 1, false);


--
-- Name: field_registry_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.field_registry_id_seq', 29, true);


--
-- Name: ipam_networks_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.ipam_networks_id_seq', 1, false);


--
-- Name: mac_vendor_ranges_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.mac_vendor_ranges_id_seq', 1, false);


--
-- Name: mapping_errors_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.mapping_errors_id_seq', 1, false);


--
-- Name: os_eol_data_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.os_eol_data_id_seq', 1, false);


--
-- Name: posture_tier_snapshots_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.posture_tier_snapshots_id_seq', 1, false);


--
-- Name: posture_coverage_snapshots_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.posture_coverage_snapshots_id_seq', 1, false);


--
-- Name: raw_asset_data_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.raw_asset_data_id_seq', 1, false);


--
-- Name: raw_source_data_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.raw_source_data_id_seq', 1, false);


--
-- Name: rule_execution_history_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.rule_execution_history_id_seq', 1, false);


--
-- Name: settings_audit_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.settings_audit_log_id_seq', 1, false);


--
-- Name: system_settings_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.system_settings_id_seq', 82, true);


--
-- Name: alert_rules alert_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_rules
    ADD CONSTRAINT alert_rules_pkey PRIMARY KEY (id);


--
-- Name: alert_rules alert_rules_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_rules
    ADD CONSTRAINT alert_rules_slug_key UNIQUE (slug);


--
-- Name: alerts alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_pkey PRIMARY KEY (id);


--
-- Name: alert_events alert_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_events
    ADD CONSTRAINT alert_events_pkey PRIMARY KEY (id);


--
-- Name: alert_channels alert_channels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_channels
    ADD CONSTRAINT alert_channels_pkey PRIMARY KEY (id);


--
-- Name: alert_channels alert_channels_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_channels
    ADD CONSTRAINT alert_channels_name_key UNIQUE (name);


--
-- Name: alert_category_channels alert_category_channels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_category_channels
    ADD CONSTRAINT alert_category_channels_pkey PRIMARY KEY (category, channel_id);


--
-- Name: alert_deliveries alert_deliveries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_deliveries
    ADD CONSTRAINT alert_deliveries_pkey PRIMARY KEY (id);


--
-- Name: metrics_forwarding_config metrics_forwarding_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.metrics_forwarding_config
    ADD CONSTRAINT metrics_forwarding_config_pkey PRIMARY KEY (id);


--
-- Name: api_keys api_keys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_pkey PRIMARY KEY (id);


--
-- Name: asset_chronicle asset_chronicle_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.asset_chronicle
    ADD CONSTRAINT asset_chronicle_pkey PRIMARY KEY ("time", id);


--
-- Name: asset_conflicts asset_conflicts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_conflicts
    ADD CONSTRAINT asset_conflicts_pkey PRIMARY KEY (id);


--
-- Name: asset_id_mappings asset_id_mapping_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_id_mappings
    ADD CONSTRAINT asset_id_mapping_pkey PRIMARY KEY (component);


--
-- Name: asset_manual_tags asset_manual_tags_asset_id_tag_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_manual_tags
    ADD CONSTRAINT asset_manual_tags_asset_id_tag_name_key UNIQUE (asset_id, tag_name);


--
-- Name: asset_manual_tags asset_manual_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_manual_tags
    ADD CONSTRAINT asset_manual_tags_pkey PRIMARY KEY (id);


--
-- Name: asset_merges asset_merges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_merges
    ADD CONSTRAINT asset_merges_pkey PRIMARY KEY (id);


--
-- Name: asset_providers_history asset_providers_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_providers_history
    ADD CONSTRAINT asset_providers_history_pkey PRIMARY KEY (history_id);


--
-- Name: asset_providers asset_providers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_providers
    ADD CONSTRAINT asset_providers_pkey PRIMARY KEY (id);


--
-- Name: asset_tags_v2 asset_tags_v2_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_tags_v2
    ADD CONSTRAINT asset_tags_v2_pkey PRIMARY KEY (id);


--
-- Name: assets_history assets_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets_history
    ADD CONSTRAINT assets_history_pkey PRIMARY KEY (history_id);


--
-- Name: assets assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);


--
-- Name: audit_trail audit_trail_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_trail
    ADD CONSTRAINT audit_trail_pkey PRIMARY KEY (id);


--
-- Name: batch_logs batch_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batch_logs
    ADD CONSTRAINT batch_logs_pkey PRIMARY KEY (id);


--
-- Name: batches batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_pkey PRIMARY KEY (id);


--
-- Name: category_overrides category_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.category_overrides
    ADD CONSTRAINT category_overrides_pkey PRIMARY KEY (asset_id);


--
-- Name: chat_feedback chat_feedback_message_id_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_feedback
    ADD CONSTRAINT chat_feedback_message_id_user_id_key UNIQUE (message_id, user_id);


--
-- Name: chat_feedback chat_feedback_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_feedback
    ADD CONSTRAINT chat_feedback_pkey PRIMARY KEY (id);


--
-- Name: chat_messages chat_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_pkey PRIMARY KEY (id);


--
-- Name: chat_messages chat_messages_session_id_seq_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_session_id_seq_key UNIQUE (session_id, seq);


--
-- Name: chat_sessions chat_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_sessions
    ADD CONSTRAINT chat_sessions_pkey PRIMARY KEY (id);


--
-- Name: chat_tool_calls chat_tool_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_tool_calls
    ADD CONSTRAINT chat_tool_calls_pkey PRIMARY KEY (id);


--
-- Name: compliance_rules compliance_rules_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compliance_rules
    ADD CONSTRAINT compliance_rules_name_key UNIQUE (name);


--
-- Name: compliance_rules compliance_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compliance_rules
    ADD CONSTRAINT compliance_rules_pkey PRIMARY KEY (id);


--
-- Name: detection_alerts detection_alerts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detection_alerts
    ADD CONSTRAINT detection_alerts_pkey PRIMARY KEY (id);


--
-- Name: enrichment_datasets enrichment_datasets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.enrichment_datasets
    ADD CONSTRAINT enrichment_datasets_pkey PRIMARY KEY (id);


--
-- Name: enrichment_rows enrichment_rows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.enrichment_rows
    ADD CONSTRAINT enrichment_rows_pkey PRIMARY KEY (id);


--
-- Name: excluded_records excluded_records_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.excluded_records
    ADD CONSTRAINT excluded_records_pkey PRIMARY KEY (id);


--
-- Name: field_registry field_registry_field_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.field_registry
    ADD CONSTRAINT field_registry_field_name_key UNIQUE (field_name);


--
-- Name: field_registry field_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.field_registry
    ADD CONSTRAINT field_registry_pkey PRIMARY KEY (id);


--
-- Name: ingestion_batches ingestion_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_batches
    ADD CONSTRAINT ingestion_batches_pkey PRIMARY KEY (id);


--
-- Name: integration_groups integration_groups_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_groups
    ADD CONSTRAINT integration_groups_name_key UNIQUE (name);


--
-- Name: integration_groups integration_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_groups
    ADD CONSTRAINT integration_groups_pkey PRIMARY KEY (id);


--
-- Name: integration_technologies integration_technologies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_technologies
    ADD CONSTRAINT integration_technologies_pkey PRIMARY KEY (id);


--
-- Name: integrations integrations_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integrations
    ADD CONSTRAINT integrations_name_key UNIQUE (name);


--
-- Name: integrations integrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integrations
    ADD CONSTRAINT integrations_pkey PRIMARY KEY (id);


--
-- Name: ipam_networks ipam_networks_network_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ipam_networks
    ADD CONSTRAINT ipam_networks_network_key UNIQUE (network);


--
-- Name: ipam_networks ipam_networks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ipam_networks
    ADD CONSTRAINT ipam_networks_pkey PRIMARY KEY (id);


--
-- Name: job_executions job_executions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.job_executions
    ADD CONSTRAINT job_executions_pkey PRIMARY KEY (id);


--
-- Name: mac_vendor_ranges mac_vendor_ranges_mac_prefix_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mac_vendor_ranges
    ADD CONSTRAINT mac_vendor_ranges_mac_prefix_key UNIQUE (mac_prefix);


--
-- Name: mac_vendor_ranges mac_vendor_ranges_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mac_vendor_ranges
    ADD CONSTRAINT mac_vendor_ranges_pkey PRIMARY KEY (id);


--
-- Name: mapping_errors mapping_errors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mapping_errors
    ADD CONSTRAINT mapping_errors_pkey PRIMARY KEY (id);


--
-- Name: merge_history merge_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.merge_history
    ADD CONSTRAINT merge_history_pkey PRIMARY KEY (id);


--
-- Name: merge_prevention_rules merge_prevention_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.merge_prevention_rules
    ADD CONSTRAINT merge_prevention_rules_pkey PRIMARY KEY (id);


--
-- Name: merge_prevention_rules merge_prevention_rules_rule_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.merge_prevention_rules
    ADD CONSTRAINT merge_prevention_rules_rule_name_key UNIQUE (rule_name);


--
-- Name: os_eol_data os_eol_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.os_eol_data
    ADD CONSTRAINT os_eol_data_pkey PRIMARY KEY (id);


--
-- Name: os_eol_data os_eol_data_product_name_cycle_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.os_eol_data
    ADD CONSTRAINT os_eol_data_product_name_cycle_key UNIQUE (product_name, cycle);


--
-- Name: password_reset_tokens password_reset_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_pkey PRIMARY KEY (id);


--
-- Name: password_reset_tokens password_reset_tokens_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_token_hash_key UNIQUE (token_hash);


--
-- Name: pipeline_runs pipeline_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_runs
    ADD CONSTRAINT pipeline_runs_pkey PRIMARY KEY (id);


--
-- Name: posture_categories posture_categories_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posture_categories
    ADD CONSTRAINT posture_categories_pkey PRIMARY KEY (id);


--
-- Name: posture_sub_rule_history posture_sub_rule_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posture_sub_rule_history
    ADD CONSTRAINT posture_sub_rule_history_pkey PRIMARY KEY (id);


--
-- Name: posture_sub_rules posture_sub_rules_category_name_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posture_sub_rules
    ADD CONSTRAINT posture_sub_rules_category_name_unique UNIQUE (category_id, name);


--
-- Name: posture_sub_rules posture_sub_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posture_sub_rules
    ADD CONSTRAINT posture_sub_rules_pkey PRIMARY KEY (id);


--
-- Name: posture_tier_snapshots posture_tier_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posture_tier_snapshots
    ADD CONSTRAINT posture_tier_snapshots_pkey PRIMARY KEY (id);


--
-- Name: posture_coverage_snapshots posture_coverage_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posture_coverage_snapshots
    ADD CONSTRAINT posture_coverage_snapshots_pkey PRIMARY KEY (id);


--
-- Name: raw_asset_data raw_asset_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_asset_data
    ADD CONSTRAINT raw_asset_data_pkey PRIMARY KEY (id);


--
-- Name: raw_source_data raw_source_data_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_source_data
    ADD CONSTRAINT raw_source_data_pkey PRIMARY KEY (id);


--
-- Name: reference_data_updates reference_data_updates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reference_data_updates
    ADD CONSTRAINT reference_data_updates_pkey PRIMARY KEY (id);


--
-- Name: reports_audit_log reports_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports_audit_log
    ADD CONSTRAINT reports_audit_log_pkey PRIMARY KEY (id);


--
-- Name: reports reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT reports_pkey PRIMARY KEY (id);


--
-- Name: rule_execution_history rule_execution_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_execution_history
    ADD CONSTRAINT rule_execution_history_pkey PRIMARY KEY (id);


--
-- Name: saved_filters saved_filters_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_filters
    ADD CONSTRAINT saved_filters_pkey PRIMARY KEY (id);


--
-- Name: saved_filter_grants saved_filter_grants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_filter_grants
    ADD CONSTRAINT saved_filter_grants_pkey PRIMARY KEY (saved_filter_id, user_id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (filename);


--
-- Name: settings_audit_log settings_audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings_audit_log
    ADD CONSTRAINT settings_audit_log_pkey PRIMARY KEY (id);


--
-- Name: split_history split_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.split_history
    ADD CONSTRAINT split_history_pkey PRIMARY KEY (id);


--
-- Name: split_prevention split_prevention_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.split_prevention
    ADD CONSTRAINT split_prevention_pkey PRIMARY KEY (id);


--
-- Name: system_dashboard_preferences system_dashboard_preferences_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_dashboard_preferences
    ADD CONSTRAINT system_dashboard_preferences_pkey PRIMARY KEY (id);


--
-- Name: system_info system_info_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_info
    ADD CONSTRAINT system_info_pkey PRIMARY KEY (id);


--
-- Name: system_settings system_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT system_settings_pkey PRIMARY KEY (id);


--
-- Name: tag_rules tag_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tag_rules
    ADD CONSTRAINT tag_rules_pkey PRIMARY KEY (id);


--
-- Name: teams_account_links teams_account_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams_account_links
    ADD CONSTRAINT teams_account_links_pkey PRIMARY KEY (id);


--
-- Name: teams_conversation_sessions teams_conversation_sessions_conversation_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams_conversation_sessions
    ADD CONSTRAINT teams_conversation_sessions_conversation_id_key UNIQUE (conversation_id);


--
-- Name: teams_conversation_sessions teams_conversation_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams_conversation_sessions
    ADD CONSTRAINT teams_conversation_sessions_pkey PRIMARY KEY (id);


--
-- Name: teams_link_used_jtis teams_link_used_jtis_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams_link_used_jtis
    ADD CONSTRAINT teams_link_used_jtis_pkey PRIMARY KEY (jti);


--
-- Name: system_settings unique_setting; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT unique_setting UNIQUE (category, key);


--
-- Name: split_prevention unique_split_pair; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.split_prevention
    ADD CONSTRAINT unique_split_pair UNIQUE (asset_id_1, asset_id_2);


--
-- Name: user_mfa user_mfa_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_mfa
    ADD CONSTRAINT user_mfa_pkey PRIMARY KEY (id);

CREATE INDEX IF NOT EXISTS idx_user_mfa_user_id ON public.user_mfa USING btree (user_id);


--
-- Name: user_sessions user_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_pkey PRIMARY KEY (id);


--
-- Name: user_sessions user_sessions_refresh_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_refresh_token_hash_key UNIQUE (refresh_token_hash);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: asset_chronicle_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS asset_chronicle_time_idx ON public.asset_chronicle USING btree ("time" DESC);


--
-- Name: idx_alert_rules_enabled_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alert_rules_enabled_source ON public.alert_rules USING btree (enabled, source) WHERE (enabled = true);


--
-- Name: idx_alerts_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alerts_created_at ON public.alerts USING btree (created_at);


--
-- Name: idx_alerts_fingerprint_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alerts_fingerprint_active ON public.alerts USING btree (rule_id, fingerprint) WHERE (status = 'active'::text);


--
-- Name: idx_alerts_rule_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alerts_rule_id ON public.alerts USING btree (rule_id);


--
-- Name: idx_alerts_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alerts_severity ON public.alerts USING btree (severity);


--
-- Name: idx_alerts_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alerts_status ON public.alerts USING btree (status);


--
-- Name: idx_alerts_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alerts_type ON public.alerts USING btree (type);


--
-- Name: idx_alert_events_alert_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alert_events_alert_id ON public.alert_events USING btree (alert_id, created_at);


--
-- Name: idx_alert_events_pipeline_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alert_events_pipeline_run ON public.alert_events USING btree (pipeline_run_id);


--
-- Name: idx_alert_category_channels_channel; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alert_category_channels_channel ON public.alert_category_channels USING btree (channel_id);


--
-- Name: idx_alert_deliveries_pending; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alert_deliveries_pending ON public.alert_deliveries USING btree (next_retry_at) WHERE (status = 'pending'::text);


--
-- Name: idx_alert_deliveries_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alert_deliveries_created_at ON public.alert_deliveries USING btree (created_at);


--
-- Name: idx_api_keys_active_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_keys_active_expires ON public.api_keys USING btree (is_active, expires_at);


--
-- Name: idx_api_keys_key_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_api_keys_key_hash ON public.api_keys USING btree (key_hash);


--
-- Name: idx_api_keys_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_api_keys_user_id ON public.api_keys USING btree (user_id);


--
-- Name: idx_asset_id_mappings_last_seen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_id_mappings_last_seen ON public.asset_id_mappings USING btree (last_seen);


--
-- Name: idx_asset_manual_tags_asset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_manual_tags_asset ON public.asset_manual_tags USING btree (asset_id);


--
-- Name: idx_asset_merges_primary; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_merges_primary ON public.asset_merges USING btree (primary_asset_id);


--
-- Name: idx_asset_merges_secondary; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_merges_secondary ON public.asset_merges USING btree (secondary_asset_id);


--
-- Name: idx_asset_providers_data_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_providers_data_source ON public.asset_providers USING btree (data_source);


--
-- Name: idx_asset_providers_integration_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_providers_integration_id ON public.asset_providers USING btree (integration_id);


--
-- Name: idx_asset_tags_v2_applied; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_tags_v2_applied ON public.asset_tags_v2 USING btree (applied_at DESC);


--
-- Name: idx_asset_tags_v2_asset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_tags_v2_asset ON public.asset_tags_v2 USING btree (asset_id);


--
-- Name: idx_asset_tags_v2_execution; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_tags_v2_execution ON public.asset_tags_v2 USING btree (execution_id) WHERE (execution_id IS NOT NULL);


--
-- Name: idx_asset_tags_v2_rule; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_tags_v2_rule ON public.asset_tags_v2 USING btree (tag_rule_id);


--
-- Name: idx_asset_tags_v2_tag_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_tags_v2_tag_name ON public.asset_tags_v2 USING btree (tag_name);


--
-- Name: idx_asset_tags_v2_unique_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_asset_tags_v2_unique_active ON public.asset_tags_v2 USING btree (asset_id, tag_name, tag_rule_id);


--
-- Name: idx_assets_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_category ON public.assets USING btree (category);


--
-- Name: idx_assets_confidence_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_confidence_id ON public.assets USING btree (confidence DESC NULLS LAST, id ASC);


--
-- Name: idx_assets_eol_checked; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_eol_checked ON public.assets USING btree (os_eol_checked_at DESC);


--
-- Name: idx_assets_eol_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_eol_date ON public.assets USING btree (os_eol_date) WHERE (os_eol_date IS NOT NULL);


--
-- Name: idx_assets_eol_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_eol_status ON public.assets USING btree (os_eol_status) WHERE (os_eol_status IS NOT NULL);


--
-- Name: idx_assets_extended; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_extended ON public.assets USING gin (extended_attributes);


--
-- Name: idx_assets_first_seen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_first_seen ON public.assets USING btree (first_seen);


--
-- Name: INDEX idx_assets_first_seen; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.idx_assets_first_seen IS 'Supports queries like "show newly discovered assets" for change tracking.';


--
-- Name: idx_assets_history_archived; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_history_archived ON public.assets_history USING btree (archived_at DESC);


--
-- Name: idx_assets_history_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_history_category ON public.assets_history USING btree (category);


--
-- Name: idx_assets_history_first_seen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_history_first_seen ON public.assets_history USING btree (id, first_seen);


--
-- Name: idx_assets_history_generation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_history_generation ON public.assets_history USING btree (generation_run_id);


--
-- Name: idx_assets_history_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_history_hostname ON public.assets_history USING gin (hostname);


--
-- Name: idx_assets_history_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_history_id ON public.assets_history USING btree (id);


--
-- Name: idx_assets_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_hostname ON public.assets USING gin (hostname);


--
-- Name: idx_assets_ip; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_ip ON public.assets USING gin (ip);


--
-- Name: idx_assets_search_trgm; Type: INDEX; Schema: public; Owner: -
-- Mirrors migration 112_add_assets_search_trgm_index.sql. The expression MUST
-- stay structurally identical to _ASSET_SEARCH_EXPR in
-- src/modules/assets/repositories/asset_repository.py (same columns, order and
-- separators) or the Explorer keyword search will not use the index.
--

CREATE INDEX idx_assets_search_trgm ON public.assets USING gin (
    (
        coalesce(public.asset_search_join(hostname), '') || ' ' ||
        coalesce(public.asset_search_join(ip), '') || ' ' ||
        coalesce(public.asset_search_join(mac), '') || ' ' ||
        coalesce(public.asset_search_join(sn), '') || ' ' ||
        coalesce(public.asset_search_join(fqdn), '') || ' ' ||
        coalesce(public.asset_search_join(manufacturer), '') || ' ' ||
        coalesce(public.asset_search_join(model), '') || ' ' ||
        coalesce(public.asset_search_join(data_source), '') || ' ' ||
        coalesce(public.asset_search_join(owner), '') || ' ' ||
        coalesce(public.asset_search_join(location), '') || ' ' ||
        coalesce(public.asset_search_join(user_field), '') || ' ' ||
        coalesce(category, '') || ' ' ||
        coalesce(subcategory, '') || ' ' ||
        coalesce(os_normalized, '') || ' ' ||
        coalesce(extended_attributes::text, '')
    ) public.gin_trgm_ops
);


--
-- Name: idx_assets_last_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_last_run ON public.assets USING btree (last_generation_run_id);


--
-- Name: INDEX idx_assets_last_run; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.idx_assets_last_run IS 'Links assets to their last generation run for audit trail and debugging.';


--
-- Name: idx_assets_last_seen_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_last_seen_id ON public.assets USING btree (last_seen DESC NULLS LAST, id ASC);


--
-- Name: idx_assets_last_time_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_last_time_id ON public.assets USING btree (last_time DESC NULLS LAST, id ASC);


--
-- Name: idx_assets_mac; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_mac ON public.assets USING gin (mac);


--
-- Name: idx_assets_merged_into; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_merged_into ON public.assets USING btree (merged_into) WHERE (merged_into IS NOT NULL);


--
-- Name: idx_assets_os_enrichment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_os_enrichment ON public.assets USING gin (os_enrichment);


--
-- Name: idx_assets_sn; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_sn ON public.assets USING gin (sn);


--
-- Name: idx_assets_tags; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_tags ON public.assets USING gin (tags);


--
-- Name: idx_assets_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_updated_at ON public.assets USING btree (updated_at DESC);


--
-- Name: idx_audit_trail_method; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_trail_method ON public.audit_trail USING btree (method);


--
-- Name: idx_audit_trail_path; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_trail_path ON public.audit_trail USING btree (path text_pattern_ops);


--
-- Name: idx_audit_trail_status_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_trail_status_code ON public.audit_trail USING btree (status_code);


--
-- Name: idx_audit_trail_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_trail_timestamp ON public.audit_trail USING btree ("timestamp" DESC);


--
-- Name: idx_audit_trail_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_trail_user_id ON public.audit_trail USING btree (user_id);


--
-- Name: idx_batch_logs_batch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batch_logs_batch ON public.batch_logs USING btree (batch_id);


--
-- Name: idx_batch_logs_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batch_logs_created_at ON public.batch_logs USING btree (created_at DESC);


--
-- Name: idx_batch_logs_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batch_logs_level ON public.batch_logs USING btree (log_level);


--
-- Name: idx_batches_integration; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batches_integration ON public.ingestion_batches USING btree (integration_id);


--
-- Name: idx_batches_integration_ingestion_completed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batches_integration_ingestion_completed ON public.ingestion_batches USING btree (integration_id, started_at DESC) WHERE (status = 'completed'::text);


--
-- Name: idx_batches_integration_pipeline_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batches_integration_pipeline_started ON public.ingestion_batches USING btree (integration_id, started_at DESC) WHERE (asset_generation_status = 'completed'::text);


--
-- Name: idx_batches_integration_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batches_integration_status ON public.batches USING btree (integration_id, status);


--
-- Name: idx_batches_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batches_started ON public.ingestion_batches USING btree (started_at DESC);


--
-- Name: idx_batches_started_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batches_started_at ON public.batches USING btree (started_at DESC);


--
-- Name: idx_batches_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_batches_status ON public.ingestion_batches USING btree (status);


--
-- Name: idx_chat_feedback_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_feedback_user_id ON public.chat_feedback USING btree (user_id);


--
-- Name: idx_chat_messages_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_messages_created_at ON public.chat_messages USING btree (created_at);


--
-- Name: idx_chat_sessions_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_sessions_updated_at ON public.chat_sessions USING btree (updated_at);


--
-- Name: idx_chat_sessions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_sessions_user_id ON public.chat_sessions USING btree (user_id, updated_at DESC) WHERE (deleted_at IS NULL);


--
-- Name: idx_chat_tool_calls_message; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_tool_calls_message ON public.chat_tool_calls USING btree (message_id);


--
-- Name: idx_chat_tool_calls_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_tool_calls_name ON public.chat_tool_calls USING btree (tool_name, created_at DESC);


--
-- Name: idx_chronicle_asset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_chronicle_asset ON public.asset_chronicle USING btree (asset_id, "time" DESC);


--
-- Name: idx_chronicle_changed_attributes; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_chronicle_changed_attributes ON public.asset_chronicle USING gin (changed_attributes);


--
-- Name: INDEX idx_chronicle_changed_attributes; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.idx_chronicle_changed_attributes IS 'GIN index for array queries. Enables fast lookups like "find all events where hostname changed"';


--
-- Name: idx_chronicle_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_chronicle_event_type ON public.asset_chronicle USING btree (event_type, "time" DESC);


--
-- Name: INDEX idx_chronicle_event_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.idx_chronicle_event_type IS 'Enables efficient filtering by event type in reports.';


--
-- Name: idx_chronicle_gen_run_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_chronicle_gen_run_time ON public.asset_chronicle USING btree (generation_run_id, "time" DESC) WHERE (generation_run_id IS NOT NULL);


--
-- Name: idx_chronicle_generation_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_chronicle_generation_run ON public.asset_chronicle USING btree (generation_run_id);


--
-- Name: idx_chronicle_operation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_chronicle_operation_id ON public.asset_chronicle USING btree (operation_id) WHERE (operation_id IS NOT NULL);


--
-- Name: INDEX idx_chronicle_operation_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.idx_chronicle_operation_id IS 'Partial index for operation_id. Enables fast queries for all events in a logical operation.';


--
-- Name: idx_chronicle_related_asset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_chronicle_related_asset ON public.asset_chronicle USING btree (related_asset_id) WHERE (related_asset_id IS NOT NULL);


--
-- Name: INDEX idx_chronicle_related_asset; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.idx_chronicle_related_asset IS 'Partial index for related_asset_id. Enables fast queries for merge-related events.';


--
-- Name: idx_chronicle_time_asset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_chronicle_time_asset ON public.asset_chronicle USING btree ("time" DESC, asset_id);


--
-- Name: INDEX idx_chronicle_time_asset; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.idx_chronicle_time_asset IS 'Optimizes time-range queries for specific assets. Supports efficient event history retrieval.';


--
-- Name: idx_chronicle_time_asset_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_chronicle_time_asset_unique ON public.asset_chronicle USING btree ("time", asset_id);


--
-- Name: INDEX idx_chronicle_time_asset_unique; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.idx_chronicle_time_asset_unique IS 'Ensures no duplicate events for the same asset at the same timestamp. Previously was the primary key.';


--
-- Name: idx_compliance_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_compliance_category ON public.compliance_rules USING btree (category);


--
-- Name: idx_conflicts_asset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conflicts_asset_id ON public.asset_conflicts USING btree (asset_id);


--
-- Name: idx_conflicts_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conflicts_created_at ON public.asset_conflicts USING btree (created_at);


--
-- Name: idx_conflicts_generation_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conflicts_generation_run ON public.asset_conflicts USING btree (generation_run_id);


--
-- Name: idx_conflicts_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conflicts_severity ON public.asset_conflicts USING btree (severity);


--
-- Name: idx_conflicts_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conflicts_status ON public.asset_conflicts USING btree (status);


--
-- Name: idx_dashboard_prefs_last_modified_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dashboard_prefs_last_modified_by ON public.system_dashboard_preferences USING btree (last_modified_by);


--
-- Name: idx_detection_alerts_execution; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_detection_alerts_execution ON public.detection_alerts USING btree (execution_id) WHERE (execution_id IS NOT NULL);


--
-- Name: idx_detection_alerts_rule; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_detection_alerts_rule ON public.detection_alerts USING btree (rule_id);


--
-- Name: idx_detection_alerts_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_detection_alerts_severity ON public.detection_alerts USING btree (severity);


--
-- Name: idx_detection_alerts_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_detection_alerts_status ON public.detection_alerts USING btree (status) WHERE ((status)::text = 'active'::text);


--
-- Name: idx_detection_alerts_triggered; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_detection_alerts_triggered ON public.detection_alerts USING btree (triggered_at DESC);


--
-- Name: idx_enrichment_rows_dataset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_enrichment_rows_dataset ON public.enrichment_rows USING btree (dataset_id);


--
-- Name: idx_enrichment_rows_match; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_enrichment_rows_match ON public.enrichment_rows USING btree (match_value);


--
-- Name: idx_enrichment_rows_match_lower; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_enrichment_rows_match_lower ON public.enrichment_rows USING btree (dataset_id, lower(match_value));


--
-- Name: idx_excluded_records_batch_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_batch_id ON public.excluded_records USING btree (batch_id);


--
-- Name: idx_excluded_records_data_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_data_source ON public.excluded_records USING btree (data_source);


--
-- Name: idx_excluded_records_excluded_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_excluded_at ON public.excluded_records USING btree (excluded_at);


--
-- Name: idx_excluded_records_exclusion_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_exclusion_category ON public.excluded_records USING btree (exclusion_category);


--
-- Name: idx_excluded_records_exclusion_details; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_exclusion_details ON public.excluded_records USING gin (exclusion_details);


--
-- Name: idx_excluded_records_exclusion_reason; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_exclusion_reason ON public.excluded_records USING btree (exclusion_reason);


--
-- Name: idx_excluded_records_extended_fields; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_extended_fields ON public.excluded_records USING gin (extended_fields);


--
-- Name: idx_excluded_records_generation_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_generation_run ON public.excluded_records USING btree (generation_run_id);


--
-- Name: idx_excluded_records_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_hostname ON public.excluded_records USING btree (hostname) WHERE (hostname IS NOT NULL);


--
-- Name: idx_excluded_records_integration_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_integration_id ON public.excluded_records USING btree (integration_id);


--
-- Name: idx_excluded_records_ip; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_ip ON public.excluded_records USING btree (ip) WHERE (ip IS NOT NULL);


--
-- Name: idx_excluded_records_mac; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_mac ON public.excluded_records USING btree (mac) WHERE (mac IS NOT NULL);


--
-- Name: idx_excluded_records_raw_record_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_raw_record_id ON public.excluded_records USING btree (raw_record_id);


--
-- Name: idx_excluded_records_sn; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_excluded_records_sn ON public.excluded_records USING btree (sn) WHERE (sn IS NOT NULL);


--
-- Name: idx_execution_history_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_execution_history_date ON public.rule_execution_history USING btree (executed_at DESC);


--
-- Name: idx_execution_history_generation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_execution_history_generation ON public.rule_execution_history USING btree (generation_run_id) WHERE (generation_run_id IS NOT NULL);


--
-- Name: idx_execution_history_rule; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_execution_history_rule ON public.rule_execution_history USING btree (rule_id, executed_at DESC);


--
-- Name: idx_execution_history_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_execution_history_status ON public.rule_execution_history USING btree (status);


--
-- Name: idx_field_registry_data_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_field_registry_data_source ON public.field_registry USING btree (data_source);


--
-- Name: idx_field_registry_field_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_field_registry_field_name ON public.field_registry USING btree (field_name);


--
-- Name: idx_field_registry_field_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_field_registry_field_type ON public.field_registry USING btree (field_type);


--
-- Name: idx_field_registry_searchable; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_field_registry_searchable ON public.field_registry USING btree (searchable) WHERE (searchable = true);


--
-- Name: idx_integration_groups_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_integration_groups_type ON public.integration_groups USING btree (integration_type);


--
-- Name: idx_integrations_connection_test; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_integrations_connection_test ON public.integrations USING btree (last_connection_test_at) WHERE (last_connection_test_at IS NOT NULL);


--
-- Name: idx_integrations_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_integrations_created_at ON public.integrations USING btree (created_at DESC);


--
-- Name: idx_integrations_group_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_integrations_group_id ON public.integrations USING btree (group_id);


--
-- Name: idx_integrations_schedule; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_integrations_schedule ON public.integrations USING btree (schedule_enabled, next_run_at) WHERE (schedule_enabled = true);


--
-- Name: idx_integrations_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_integrations_status ON public.integrations USING btree (status);


--
-- Name: idx_integrations_transformations; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_integrations_transformations ON public.integrations USING gin (transformations);


--
-- Name: idx_integrations_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_integrations_type ON public.integrations USING btree (integration_type);


--
-- Name: idx_ipam_network; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ipam_network ON public.ipam_networks USING gist (network inet_ops);


--
-- Name: idx_job_execution_stats_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_job_execution_stats_job_id ON public.job_execution_stats USING btree (job_id);


--
-- Name: idx_job_executions_execution_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_job_executions_execution_id ON public.job_executions USING btree (execution_id);


--
-- Name: idx_job_executions_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_job_executions_job_id ON public.job_executions USING btree (job_id);


--
-- Name: idx_job_executions_job_id_started_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_job_executions_job_id_started_at ON public.job_executions USING btree (job_id, started_at DESC);


--
-- Name: idx_job_executions_started_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_job_executions_started_at ON public.job_executions USING btree (started_at DESC);


--
-- Name: idx_job_executions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_job_executions_status ON public.job_executions USING btree (status);


--
-- Name: idx_mac_vendor_block_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mac_vendor_block_type ON public.mac_vendor_ranges USING btree (block_type);


--
-- Name: idx_mac_vendor_is_private; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mac_vendor_is_private ON public.mac_vendor_ranges USING btree (is_private);


--
-- Name: idx_mac_vendor_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mac_vendor_source ON public.mac_vendor_ranges USING btree (data_source);


--
-- Name: idx_mac_vendor_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mac_vendor_updated ON public.mac_vendor_ranges USING btree (updated_at);


--
-- Name: idx_mapping_asset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mapping_asset_id ON public.asset_id_mappings USING btree (asset_id);


--
-- Name: idx_mapping_errors_batch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mapping_errors_batch ON public.mapping_errors USING btree (batch_id);


--
-- Name: idx_mapping_errors_raw_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mapping_errors_raw_source_id ON public.mapping_errors USING btree (raw_source_id);


--
-- Name: idx_mapping_errors_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mapping_errors_type ON public.mapping_errors USING btree (error_type);


--
-- Name: idx_mapping_updated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mapping_updated ON public.asset_id_mappings USING btree (updated_at DESC);


--
-- Name: idx_merge_history_generation_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_merge_history_generation_run ON public.merge_history USING btree (generation_run_id);


--
-- Name: idx_merge_history_merge_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_merge_history_merge_date ON public.merge_history USING btree (merge_date);


--
-- Name: idx_merge_history_merged_from; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_merge_history_merged_from ON public.merge_history USING gin (merged_from);


--
-- Name: idx_merge_history_merged_into; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_merge_history_merged_into ON public.merge_history USING btree (merged_into);


--
-- Name: idx_merge_history_reversed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_merge_history_reversed_at ON public.merge_history USING btree (reversed_at) WHERE (reversed_at IS NOT NULL);


--
-- Name: idx_merge_history_reversible_until; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_merge_history_reversible_until ON public.merge_history USING btree (reversible_until) WHERE ((reversible_until IS NOT NULL) AND (reversed_at IS NULL));


--
-- Name: idx_merge_rules_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_merge_rules_enabled ON public.merge_prevention_rules USING btree (enabled) WHERE (enabled = true);


--
-- Name: idx_merge_rules_priority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_merge_rules_priority ON public.merge_prevention_rules USING btree (priority DESC);


--
-- Name: idx_merge_rules_rule_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_merge_rules_rule_type ON public.merge_prevention_rules USING btree (rule_type);


--
-- Name: idx_mv_asset_quality_metrics_category; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_mv_asset_quality_metrics_category ON public.mv_asset_quality_metrics USING btree (category);


--
-- Name: idx_mv_dashboard_summary_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_mv_dashboard_summary_key ON public.mv_dashboard_summary USING btree (view_key);


--
-- Name: idx_mv_provider_coverage_grouped_id_type; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_mv_provider_coverage_grouped_id_type ON public.mv_provider_coverage_grouped USING btree (id, source_type);


--
-- Name: idx_mv_provider_coverage_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_mv_provider_coverage_id ON public.mv_provider_coverage USING btree (id);


--
-- Name: idx_os_eol_eol_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_os_eol_eol_date ON public.os_eol_data USING btree (eol_date) WHERE (eol_date IS NOT NULL);


--
-- Name: idx_os_eol_last_synced; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_os_eol_last_synced ON public.os_eol_data USING btree (last_synced DESC);


--
-- Name: idx_os_eol_product; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_os_eol_product ON public.os_eol_data USING btree (product_name);


--
-- Name: idx_os_eol_product_cycle; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_os_eol_product_cycle ON public.os_eol_data USING btree (product_name, cycle);


--
-- Name: idx_password_reset_tokens_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_password_reset_tokens_expires_at ON public.password_reset_tokens USING btree (expires_at);


--
-- Name: idx_password_reset_tokens_token_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_password_reset_tokens_token_hash ON public.password_reset_tokens USING btree (token_hash);


--
-- Name: idx_password_reset_tokens_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_password_reset_tokens_user_id ON public.password_reset_tokens USING btree (user_id);


--
-- Name: idx_password_reset_tokens_user_id_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_password_reset_tokens_user_id_type ON public.password_reset_tokens USING btree (user_id, token_type);


--
-- Name: idx_pipeline_runs_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pipeline_runs_created ON public.pipeline_runs USING btree (created_at DESC);


--
-- Name: idx_pipeline_runs_settled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pipeline_runs_settled ON public.pipeline_runs USING btree (data_settled_at DESC) WHERE (data_settled_at IS NOT NULL);


--
-- Name: idx_pipeline_runs_started_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pipeline_runs_started_at ON public.pipeline_runs USING btree (started_at DESC);


--
-- Name: idx_pipeline_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pipeline_runs_status ON public.pipeline_runs USING btree (status);


--
-- Name: idx_posture_sub_rule_history_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_posture_sub_rule_history_category ON public.posture_sub_rule_history USING btree (category_id, created_at DESC);


--
-- Name: idx_posture_sub_rule_history_sub_rule; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_posture_sub_rule_history_sub_rule ON public.posture_sub_rule_history USING btree (sub_rule_id, created_at DESC);


--
-- Name: idx_posture_sub_rules_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_posture_sub_rules_category ON public.posture_sub_rules USING btree (category_id);


--
-- Name: idx_posture_sub_rules_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_posture_sub_rules_enabled ON public.posture_sub_rules USING btree (category_id, enabled) WHERE (enabled = true);


--
-- Name: idx_posture_sub_rules_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_posture_sub_rules_order ON public.posture_sub_rules USING btree (category_id, display_order);


--
-- Name: idx_posture_tier_snapshots_at_desc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_posture_tier_snapshots_at_desc ON public.posture_tier_snapshots USING btree (snapshot_at DESC);


--
-- Name: idx_posture_coverage_snapshots_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_posture_coverage_snapshots_lookup ON public.posture_coverage_snapshots USING btree (control_id, snapshot_at DESC);


--
-- Name: idx_providers_asset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_asset_id ON public.asset_providers USING btree (asset_id);


--
-- Name: idx_providers_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_category ON public.asset_providers USING btree (category) WHERE (category IS NOT NULL);


--
-- Name: idx_providers_category_prediction; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_category_prediction ON public.asset_providers USING gin (category_prediction) WHERE (category_prediction IS NOT NULL);


--
-- Name: idx_providers_data_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_data_source ON public.asset_providers USING btree (data_source);


--
-- Name: idx_providers_generation_run; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_generation_run ON public.asset_providers USING btree (generation_run_id);


--
-- Name: idx_providers_history_archived; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_history_archived ON public.asset_providers_history USING btree (archived_at DESC);


--
-- Name: idx_providers_history_asset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_history_asset_id ON public.asset_providers_history USING btree (asset_id);


--
-- Name: idx_providers_history_data_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_history_data_source ON public.asset_providers_history USING btree (data_source);


--
-- Name: idx_providers_history_generation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_history_generation ON public.asset_providers_history USING btree (generation_run_id);


--
-- Name: idx_providers_history_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_history_id ON public.asset_providers_history USING btree (id);


--
-- Name: idx_providers_integration_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_integration_id ON public.asset_providers USING btree (integration_id);


--
-- Name: idx_providers_os_normalized; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_providers_os_normalized ON public.asset_providers USING btree (os_normalized) WHERE (os_normalized IS NOT NULL);


--
-- Name: idx_raw_asset_batch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_asset_batch ON public.raw_asset_data USING btree (batch_id);


--
-- Name: idx_raw_asset_data_raw_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_asset_data_raw_source_id ON public.raw_asset_data USING btree (raw_source_id);


--
-- Name: idx_raw_asset_extended; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_asset_extended ON public.raw_asset_data USING gin (extended_fields);


--
-- Name: idx_raw_asset_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_asset_hostname ON public.raw_asset_data USING gin (hostname);


--
-- Name: idx_raw_asset_ingested; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_asset_ingested ON public.raw_asset_data USING btree (ingested_at);


--
-- Name: idx_raw_asset_integration; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_asset_integration ON public.raw_asset_data USING btree (integration_id);


--
-- Name: idx_raw_asset_ip; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_asset_ip ON public.raw_asset_data USING gin (ip);


--
-- Name: idx_raw_asset_mac; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_asset_mac ON public.raw_asset_data USING gin (mac);


--
-- Name: idx_raw_asset_sn; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_asset_sn ON public.raw_asset_data USING gin (sn);


--
-- Name: idx_raw_data_batch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_data_batch ON public.raw_source_data USING btree (batch_id);


--
-- Name: idx_raw_data_integration; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_data_integration ON public.raw_source_data USING btree (integration_id);


--
-- Name: idx_raw_data_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_data_source_id ON public.raw_source_data USING btree (integration_id, source_record_id);


--
-- Name: idx_raw_source_batch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_source_batch ON public.raw_source_data USING btree (batch_id);


--
-- Name: idx_raw_source_ingested; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_source_ingested ON public.raw_source_data USING btree (ingested_at);


--
-- Name: idx_raw_source_integration; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_source_integration ON public.raw_source_data USING btree (integration_id);


--
-- Name: idx_raw_source_payload; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_source_payload ON public.raw_source_data USING gin (raw_payload);


--
-- Name: idx_reference_data_updates_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reference_data_updates_started ON public.reference_data_updates USING btree (started_at DESC);


--
-- Name: idx_reports_audit_changed_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_audit_changed_at ON public.reports_audit_log USING btree (changed_at DESC);


--
-- Name: idx_reports_audit_changed_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_audit_changed_by ON public.reports_audit_log USING btree (changed_by);


--
-- Name: idx_reports_audit_report_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_audit_report_id ON public.reports_audit_log USING btree (report_id);


--
-- Name: idx_reports_charts_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_charts_gin ON public.reports USING gin (charts);


--
-- Name: idx_reports_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_created_at ON public.reports USING btree (created_at DESC);


--
-- Name: idx_reports_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_created_by ON public.reports USING btree (created_by);


--
-- Name: idx_reports_is_default; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_is_default ON public.reports USING btree (is_default) WHERE (is_default = true);


--
-- Name: idx_reports_title; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_title ON public.reports USING btree (title);


--
-- Name: idx_reports_unique_default; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_reports_unique_default ON public.reports USING btree (is_default) WHERE (is_default = true);


--
-- Name: idx_reports_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_updated_at ON public.reports USING btree (updated_at DESC);


--
-- Name: idx_reports_updated_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reports_updated_by ON public.reports USING btree (updated_by);


--
-- Name: idx_saved_filter_grants_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_saved_filter_grants_user ON public.saved_filter_grants USING btree (user_id);


--
-- Name: idx_saved_filters_org; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_saved_filters_org ON public.saved_filters USING btree (updated_at DESC) WHERE ((visibility = 'org'::text) AND (deleted_at IS NULL));


--
-- Name: idx_saved_filters_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_saved_filters_owner ON public.saved_filters USING btree (owner_id, updated_at DESC) WHERE (deleted_at IS NULL);


--
-- Name: uq_saved_filters_owner_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_saved_filters_owner_name ON public.saved_filters USING btree (owner_id, lower(name)) WHERE (deleted_at IS NULL);


--
-- Name: idx_settings_audit_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_settings_audit_created ON public.settings_audit_log USING btree (created_at DESC);


--
-- Name: idx_settings_audit_setting; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_settings_audit_setting ON public.settings_audit_log USING btree (setting_id);


--
-- Name: idx_settings_audit_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_settings_audit_user ON public.settings_audit_log USING btree (changed_by);


--
-- Name: idx_settings_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_settings_category ON public.system_settings USING btree (category);


--
-- Name: idx_settings_updated_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_settings_updated_at ON public.system_settings USING btree (updated_at DESC);


--
-- Name: idx_split_history_performed_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_split_history_performed_by ON public.split_history USING btree (performed_by);


--
-- Name: idx_split_history_split_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_split_history_split_date ON public.split_history USING btree (split_date);


--
-- Name: idx_split_history_split_from; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_split_history_split_from ON public.split_history USING btree (split_from);


--
-- Name: idx_split_history_split_into; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_split_history_split_into ON public.split_history USING gin (split_into);


--
-- Name: idx_split_prevention_asset_1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_split_prevention_asset_1 ON public.split_prevention USING btree (asset_id_1);


--
-- Name: idx_split_prevention_asset_2; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_split_prevention_asset_2 ON public.split_prevention USING btree (asset_id_2);


--
-- Name: idx_split_prevention_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_split_prevention_expires_at ON public.split_prevention USING btree (expires_at) WHERE (expires_at IS NOT NULL);


--
-- Name: idx_system_prefs_dashboard_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_system_prefs_dashboard_gin ON public.system_dashboard_preferences USING gin (working_dashboard);


--
-- Name: idx_tag_rules_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tag_rules_created ON public.tag_rules USING btree (created_at DESC);


--
-- Name: idx_tag_rules_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tag_rules_created_at ON public.tag_rules USING btree (created_at DESC);


--
-- Name: idx_tag_rules_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tag_rules_enabled ON public.tag_rules USING btree (enabled) WHERE (enabled = true);


--
-- Name: idx_tag_rules_last_executed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tag_rules_last_executed ON public.tag_rules USING btree (last_executed_at DESC);


--
-- Name: idx_tag_rules_target_filters; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tag_rules_target_filters ON public.tag_rules USING gin (target_filters);


--
-- Name: idx_tag_rules_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tag_rules_type ON public.tag_rules USING btree (type);


--
-- Name: idx_user_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_sessions_expires_at ON public.user_sessions USING btree (expires_at);


--
-- Name: idx_user_sessions_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_sessions_is_active ON public.user_sessions USING btree (is_active);


--
-- Name: idx_user_sessions_jti; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_sessions_jti ON public.user_sessions USING btree (access_token_jti);


--
-- Name: idx_user_sessions_refresh_token_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_sessions_refresh_token_hash ON public.user_sessions USING btree (refresh_token_hash);


--
-- Name: idx_user_sessions_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_user_sessions_user_id ON public.user_sessions USING btree (user_id);


--
-- Name: idx_users_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_created_at ON public.users USING btree (created_at DESC);


--
-- Name: idx_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_email ON public.users USING btree (email);


--
-- Name: idx_users_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_is_active ON public.users USING btree (is_active);


--
-- Name: idx_users_locked_until; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_locked_until ON public.users USING btree (locked_until) WHERE (locked_until IS NOT NULL);


--
-- Name: idx_users_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_role ON public.users USING btree (role);


--
-- Name: idx_users_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_username ON public.users USING btree (username);


--
-- Name: teams_account_links_aad_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX teams_account_links_aad_uniq ON public.teams_account_links USING btree (aad_object_id, tenant_id);


--
-- Name: teams_account_links_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX teams_account_links_user_idx ON public.teams_account_links USING btree (user_id);


--
-- Name: teams_conversation_sessions_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX teams_conversation_sessions_user_idx ON public.teams_conversation_sessions USING btree (user_id);


--
-- Name: teams_link_used_jtis_consumed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX teams_link_used_jtis_consumed_idx ON public.teams_link_used_jtis USING btree (consumed_at);


--
-- Name: chat_sessions chat_sessions_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER chat_sessions_updated_at BEFORE UPDATE ON public.chat_sessions FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: saved_filters saved_filters_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER saved_filters_updated_at BEFORE UPDATE ON public.saved_filters FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: reports reports_updated_at_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER reports_updated_at_trigger BEFORE UPDATE ON public.reports FOR EACH ROW EXECUTE FUNCTION public.update_reports_timestamp();


--
-- Name: system_settings settings_updated_at_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER settings_updated_at_trigger BEFORE UPDATE ON public.system_settings FOR EACH ROW EXECUTE FUNCTION public.update_settings_timestamp();


--
-- Name: system_dashboard_preferences system_prefs_last_modified_trigger; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER system_prefs_last_modified_trigger BEFORE UPDATE ON public.system_dashboard_preferences FOR EACH ROW EXECUTE FUNCTION public.update_system_prefs_timestamp();


--
-- Name: posture_sub_rules trg_posture_sub_rules_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_posture_sub_rules_updated_at BEFORE UPDATE ON public.posture_sub_rules FOR EACH ROW EXECUTE FUNCTION public.set_posture_sub_rule_updated_at();


--
-- Name: field_registry trigger_field_registry_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trigger_field_registry_updated_at BEFORE UPDATE ON public.field_registry FOR EACH ROW EXECUTE FUNCTION public.update_field_registry_updated_at();


--
-- Name: rule_execution_history trigger_update_rule_stats; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trigger_update_rule_stats AFTER INSERT ON public.rule_execution_history FOR EACH ROW EXECUTE FUNCTION public.update_rule_execution_stats();


--
-- Name: tag_rules trigger_update_tag_rule_modified; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trigger_update_tag_rule_modified BEFORE UPDATE ON public.tag_rules FOR EACH ROW EXECUTE FUNCTION public.update_tag_rule_modified();


--
-- Name: asset_chronicle ts_insert_blocker; Type: TRIGGER; Schema: public; Owner: -
--



--
-- Name: asset_id_mappings update_asset_id_mapping_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_asset_id_mapping_updated_at BEFORE UPDATE ON public.asset_id_mappings FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: assets update_assets_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_assets_updated_at BEFORE UPDATE ON public.assets FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: integrations update_integrations_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_integrations_updated_at BEFORE UPDATE ON public.integrations FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: user_mfa update_user_mfa_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_user_mfa_updated_at BEFORE UPDATE ON public.user_mfa FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: users update_users_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: alert_rules alert_rules_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_rules
    ADD CONSTRAINT alert_rules_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: alerts alerts_rule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT alerts_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES public.alert_rules(id) ON DELETE CASCADE;


--
-- Name: alert_events alert_events_alert_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_events
    ADD CONSTRAINT alert_events_alert_id_fkey FOREIGN KEY (alert_id) REFERENCES public.alerts(id) ON DELETE CASCADE;


--
-- Name: alert_category_channels alert_category_channels_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_category_channels
    ADD CONSTRAINT alert_category_channels_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.alert_channels(id) ON DELETE CASCADE;


--
-- Name: alert_deliveries alert_deliveries_alert_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_deliveries
    ADD CONSTRAINT alert_deliveries_alert_id_fkey FOREIGN KEY (alert_id) REFERENCES public.alerts(id) ON DELETE CASCADE;


--
-- Name: alert_deliveries alert_deliveries_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_deliveries
    ADD CONSTRAINT alert_deliveries_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.alert_channels(id) ON DELETE CASCADE;


--
-- Name: api_keys api_keys_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.api_keys
    ADD CONSTRAINT api_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: asset_providers asset_providers_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_providers
    ADD CONSTRAINT asset_providers_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES public.assets(id) ON DELETE CASCADE;


--
-- Name: batch_logs batch_logs_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batch_logs
    ADD CONSTRAINT batch_logs_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.batches(id) ON DELETE CASCADE;


--
-- Name: batches batches_integration_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_integration_id_fkey FOREIGN KEY (integration_id) REFERENCES public.integrations(id) ON DELETE CASCADE;


--
-- Name: chat_feedback chat_feedback_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_feedback
    ADD CONSTRAINT chat_feedback_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.chat_messages(id) ON DELETE CASCADE;


--
-- Name: chat_feedback chat_feedback_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_feedback
    ADD CONSTRAINT chat_feedback_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: chat_messages chat_messages_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_messages
    ADD CONSTRAINT chat_messages_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.chat_sessions(id) ON DELETE CASCADE;


--
-- Name: chat_sessions chat_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_sessions
    ADD CONSTRAINT chat_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: chat_tool_calls chat_tool_calls_message_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_tool_calls
    ADD CONSTRAINT chat_tool_calls_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.chat_messages(id) ON DELETE CASCADE;


--
-- Name: enrichment_rows enrichment_rows_dataset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.enrichment_rows
    ADD CONSTRAINT enrichment_rows_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.enrichment_datasets(id) ON DELETE CASCADE;


--
-- Name: asset_tags_v2 fk_asset_tags_v2_asset; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_tags_v2
    ADD CONSTRAINT fk_asset_tags_v2_asset FOREIGN KEY (asset_id) REFERENCES public.assets(id) ON DELETE CASCADE;


--
-- Name: asset_tags_v2 fk_asset_tags_v2_rule; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_tags_v2
    ADD CONSTRAINT fk_asset_tags_v2_rule FOREIGN KEY (tag_rule_id) REFERENCES public.tag_rules(id) ON DELETE CASCADE;


--
-- Name: detection_alerts fk_detection_alerts_execution; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detection_alerts
    ADD CONSTRAINT fk_detection_alerts_execution FOREIGN KEY (execution_id) REFERENCES public.rule_execution_history(id) ON DELETE SET NULL;


--
-- Name: detection_alerts fk_detection_alerts_rule; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.detection_alerts
    ADD CONSTRAINT fk_detection_alerts_rule FOREIGN KEY (rule_id) REFERENCES public.tag_rules(id) ON DELETE CASCADE;


--
-- Name: rule_execution_history fk_execution_history_rule; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule_execution_history
    ADD CONSTRAINT fk_execution_history_rule FOREIGN KEY (rule_id) REFERENCES public.tag_rules(id) ON DELETE CASCADE;


--
-- Name: asset_providers fk_providers_integration; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_providers
    ADD CONSTRAINT fk_providers_integration FOREIGN KEY (integration_id) REFERENCES public.integrations(id) ON DELETE SET NULL;


--
-- Name: excluded_records fk_raw_record; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.excluded_records
    ADD CONSTRAINT fk_raw_record FOREIGN KEY (raw_record_id) REFERENCES public.raw_asset_data(id) ON DELETE SET NULL;


--
-- Name: ingestion_batches ingestion_batches_integration_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_batches
    ADD CONSTRAINT ingestion_batches_integration_id_fkey FOREIGN KEY (integration_id) REFERENCES public.integrations(id);


--
-- Name: integrations integrations_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integrations
    ADD CONSTRAINT integrations_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.integration_groups(id) ON DELETE SET NULL;


--
-- Name: mapping_errors mapping_errors_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mapping_errors
    ADD CONSTRAINT mapping_errors_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.ingestion_batches(id) ON DELETE CASCADE;


--
-- Name: mapping_errors mapping_errors_raw_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.mapping_errors
    ADD CONSTRAINT mapping_errors_raw_source_id_fkey FOREIGN KEY (raw_source_id) REFERENCES public.raw_source_data(id) ON DELETE SET NULL;


--
-- Name: password_reset_tokens password_reset_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_tokens
    ADD CONSTRAINT password_reset_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: posture_sub_rules posture_sub_rules_category_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posture_sub_rules
    ADD CONSTRAINT posture_sub_rules_category_id_fkey FOREIGN KEY (category_id) REFERENCES public.posture_categories(id) ON DELETE RESTRICT;


--
-- Name: raw_asset_data raw_asset_data_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_asset_data
    ADD CONSTRAINT raw_asset_data_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.ingestion_batches(id) ON DELETE CASCADE;


--
-- Name: raw_asset_data raw_asset_data_integration_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_asset_data
    ADD CONSTRAINT raw_asset_data_integration_id_fkey FOREIGN KEY (integration_id) REFERENCES public.integrations(id);


--
-- Name: raw_asset_data raw_asset_data_raw_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_asset_data
    ADD CONSTRAINT raw_asset_data_raw_source_id_fkey FOREIGN KEY (raw_source_id) REFERENCES public.raw_source_data(id) ON DELETE SET NULL;


--
-- Name: raw_source_data raw_source_data_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_source_data
    ADD CONSTRAINT raw_source_data_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.ingestion_batches(id) ON DELETE CASCADE;


--
-- Name: raw_source_data raw_source_data_integration_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_source_data
    ADD CONSTRAINT raw_source_data_integration_id_fkey FOREIGN KEY (integration_id) REFERENCES public.integrations(id);


--
-- Name: reports_audit_log reports_audit_log_changed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports_audit_log
    ADD CONSTRAINT reports_audit_log_changed_by_fkey FOREIGN KEY (changed_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: reports_audit_log reports_audit_log_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports_audit_log
    ADD CONSTRAINT reports_audit_log_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.reports(id) ON DELETE CASCADE;


--
-- Name: reports reports_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT reports_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: reports reports_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reports
    ADD CONSTRAINT reports_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: saved_filters saved_filters_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_filters
    ADD CONSTRAINT saved_filters_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: saved_filter_grants saved_filter_grants_saved_filter_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_filter_grants
    ADD CONSTRAINT saved_filter_grants_saved_filter_id_fkey FOREIGN KEY (saved_filter_id) REFERENCES public.saved_filters(id) ON DELETE CASCADE;


--
-- Name: saved_filter_grants saved_filter_grants_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_filter_grants
    ADD CONSTRAINT saved_filter_grants_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: saved_filter_grants saved_filter_grants_granted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.saved_filter_grants
    ADD CONSTRAINT saved_filter_grants_granted_by_fkey FOREIGN KEY (granted_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: settings_audit_log settings_audit_log_setting_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings_audit_log
    ADD CONSTRAINT settings_audit_log_setting_id_fkey FOREIGN KEY (setting_id) REFERENCES public.system_settings(id) ON DELETE CASCADE;


--
-- Name: split_prevention split_prevention_split_history_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.split_prevention
    ADD CONSTRAINT split_prevention_split_history_id_fkey FOREIGN KEY (split_history_id) REFERENCES public.split_history(id);


--
-- Name: system_dashboard_preferences system_dashboard_preferences_last_modified_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_dashboard_preferences
    ADD CONSTRAINT system_dashboard_preferences_last_modified_by_fkey FOREIGN KEY (last_modified_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: teams_account_links teams_account_links_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams_account_links
    ADD CONSTRAINT teams_account_links_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: teams_conversation_sessions teams_conversation_sessions_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams_conversation_sessions
    ADD CONSTRAINT teams_conversation_sessions_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.chat_sessions(id) ON DELETE CASCADE;


--
-- Name: teams_conversation_sessions teams_conversation_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.teams_conversation_sessions
    ADD CONSTRAINT teams_conversation_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_mfa user_mfa_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_mfa
    ADD CONSTRAINT user_mfa_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_sessions user_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_sessions
    ADD CONSTRAINT user_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: users users_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: mv_asset_quality_metrics; Type: MATERIALIZED VIEW DATA; Schema: public; Owner: -
--

REFRESH MATERIALIZED VIEW public.mv_asset_quality_metrics;


--
-- Name: mv_dashboard_summary; Type: MATERIALIZED VIEW DATA; Schema: public; Owner: -
--

REFRESH MATERIALIZED VIEW public.mv_dashboard_summary;


--
-- Name: mv_provider_coverage; Type: MATERIALIZED VIEW DATA; Schema: public; Owner: -
--

REFRESH MATERIALIZED VIEW public.mv_provider_coverage;


--
-- Name: mv_provider_coverage_grouped; Type: MATERIALIZED VIEW DATA; Schema: public; Owner: -
--

REFRESH MATERIALIZED VIEW public.mv_provider_coverage_grouped;


--
-- Name: unified_fields; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.unified_fields (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug       TEXT NOT NULL,
    name       TEXT NOT NULL,
    function   TEXT NOT NULL CHECK (function IN ('coalesce','union','max','min')),
    sources    JSONB NOT NULL,
    data_type  TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    materialized_at TIMESTAMPTZ
);


--
-- Name: COLUMN unified_fields.materialized_at; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.unified_fields.materialized_at IS 'Stamped by refresh_unified_values after a successful full-table refresh of this field; NULL means the field has no usable materialized values. Definition changes clear it.';


--
-- Name: unified_fields unified_fields_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

DROP TRIGGER IF EXISTS unified_fields_updated_at ON public.unified_fields;
CREATE TRIGGER unified_fields_updated_at
    BEFORE UPDATE ON public.unified_fields
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: uq_unified_fields_slug; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS uq_unified_fields_slug
    ON public.unified_fields (lower(slug));


--
-- Name: uq_unified_fields_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX IF NOT EXISTS uq_unified_fields_name
    ON public.unified_fields (lower(name));


--
-- Name: aa_try_numeric(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE OR REPLACE FUNCTION public.aa_try_numeric(t text) RETURNS numeric AS $$
BEGIN RETURN t::numeric; EXCEPTION WHEN others THEN RETURN NULL; END;
$$ LANGUAGE plpgsql IMMUTABLE;


--
-- Name: aa_try_ts(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE OR REPLACE FUNCTION public.aa_try_ts(t text) RETURNS timestamptz AS $$
BEGIN RETURN t::timestamptz; EXCEPTION WHEN others THEN RETURN NULL; END;
$$ LANGUAGE plpgsql STABLE;


--
-- Seed data for migration 117 (anomaly-detection rules + settings). Placed here, after all
-- table/sequence/constraint definitions, so the INSERT ... ON CONFLICT targets (alert_rules
-- slug unique, system_settings (category, key) unique) and the system_settings.id sequence
-- default all already exist on a fresh load.
--

INSERT INTO public.alert_rules (name, slug, description, type, severity, enabled, source, evaluation_type, detector_key, threshold)
VALUES
('Mass New Assets', 'anomaly-mass-new-assets',
 'An unusually large number of new assets appeared this run compared with what''s normal here.',
 'operational', 'medium', true, 'system', 'anomaly', 'mass_new_assets',
 '{"z_medium": 3, "z_high": 5, "z_critical": 8, "min_count": 20, "window_runs": 30, "min_samples": 8}'::jsonb),
('Mass Disappearance', 'anomaly-mass-missing',
 'An unusual number of assets stopped being seen across all sources this run.',
 'security', 'medium', true, 'system', 'anomaly', 'mass_missing',
 '{"z_medium": 3, "z_high": 5, "z_critical": 8, "min_count": 20, "window_runs": 30, "min_samples": 8}'::jsonb),
('Source Volume Anomaly', 'anomaly-source-volume',
 'A data source returned far more or far fewer assets than usual, or failed to return a batch.',
 'operational', 'medium', true, 'system', 'anomaly', 'source_volume',
 '{"z_medium": 3, "z_high": 5, "z_critical": 8, "min_count": 25, "min_pct": 15, "window_runs": 30, "min_samples": 8}'::jsonb),
('Attribute Churn', 'anomaly-attribute-churn',
 'An unusual amount of attribute changes this run — either one field across many assets, or scattered across many.',
 'data_quality', 'medium', true, 'system', 'anomaly', 'attribute_churn',
 '{"z_medium": 3, "z_high": 5, "z_critical": 8, "min_count": 100, "systemic_pct": 20, "window_runs": 30, "min_samples": 8}'::jsonb),
('Out-of-Profile Device in Segment', 'anomaly-segment-outlier',
 'A new asset doesn''t fit the kind of devices that normally live on its network segment.',
 'security', 'medium', true, 'system', 'anomaly', 'segment_outlier',
 '{"severity": "medium"}'::jsonb),
('First-seen manufacturer or OS', 'anomaly-first-seen-value',
 'A manufacturer or OS never seen before in this fleet appeared on a new asset.',
 'security', 'low', true, 'system', 'anomaly', 'first_seen_value',
 '{"severity": "low"}'::jsonb),
('Security source coverage loss', 'anomaly-security-source-loss',
 'Multiple assets lost a security-class data source this run while still visible via other sources.',
 'security', 'high', true, 'system', 'anomaly', 'security_source_loss',
 '{"z_medium": 3, "min_count": 5, "window_days": 90, "min_samples": 8, "severity": "high"}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO public.system_settings (category, key, value, value_type, default_value, description)
VALUES
('anomaly_detection', 'security_source_categories', '["endpoint security"]', 'json', '["endpoint security"]',
 'data_source_category values that count as security tooling (D1/D2 detectors)'),
('anomaly_detection', 'security_source_names', '[]', 'json', '[]',
 'Explicit data_source names treated as security tooling, in addition to the categories'),
('anomaly_detection', 'segment_min_population', '10', 'integer', '10',
 'Minimum pre-existing assets in a segment before profiling it (C1)'),
('anomaly_detection', 'segment_homogeneity_pct', '90', 'integer', '90',
 'Dominant-category share (percent) required to call a segment homogeneous (C1)'),
('anomaly_detection', 'aggregation_threshold', '10', 'integer', '10',
 'Collapse per-entity findings into one aggregate alert above this count per detector per run')
ON CONFLICT (category, key) DO NOTHING;


--
-- Seed data for migration 118 (anomaly-detection phase-2 detectors + coverage settings).
-- Mirrors src/database/migrations/118_add_phase2_anomaly_rules.sql; same ON CONFLICT targets
-- as the migration-117 block above.
--

INSERT INTO public.alert_rules (name, slug, description, type, severity, enabled, source, evaluation_type, detector_key, threshold)
VALUES
('Identity Churn', 'anomaly-identity-churn',
 'An unusual spike in assets being merged, re-added, or replaced this run — a sign of unstable identity matching.',
 'data_quality', 'medium', true, 'system', 'anomaly', 'identity_churn',
 '{"z_medium": 3, "z_high": 5, "z_critical": 8, "min_count": 50, "window_runs": 30, "min_samples": 8}'::jsonb),
('Confidence Drop', 'anomaly-confidence-shift',
 'More assets than usual now have low-confidence data.',
 'data_quality', 'medium', true, 'system', 'anomaly', 'confidence_shift',
 '{"z_medium": 3, "min_pp": 5, "window_runs": 30, "min_samples": 8}'::jsonb),
('Per-Asset Change Burst', 'anomaly-asset-change-burst',
 'One or more assets changed an unusually large number of fields at once (often a re-image or device replacement).',
 'data_quality', 'medium', true, 'system', 'anomaly', 'asset_change_burst',
 '{"min_attrs": 8, "systemic_pct": 20}'::jsonb),
('Persistent asset thrash', 'anomaly-asset-thrash',
 'Assets whose attributes oscillate across many recent runs — almost always two sources fighting over a value.',
 'data_quality', 'medium', true, 'system', 'anomaly', 'asset_thrash',
 '{"window_runs": 10, "min_runs": 8, "systemic_pct": 20}'::jsonb),
('Identity swap', 'anomaly-identity-swap',
 'A stable hostname now reports a different serial, or a stable IP a different MAC — hardware swap, name impersonation, or spoofing/ARP takeover.',
 'security', 'high', true, 'system', 'anomaly', 'identity_swap',
 '{"stability_days": 14, "min_seen": 3}'::jsonb),
('Unprotected newcomer in secured segment', 'anomaly-unprotected-newcomer',
 'A newly-seen asset carries no security tooling while its network segment is otherwise security-covered.',
 'security', 'high', true, 'system', 'anomaly', 'unprotected_newcomer',
 '{"settle_min_days": 2, "settle_max_days": 7}'::jsonb)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO public.system_settings (category, key, value, value_type, default_value, description)
VALUES
('anomaly_detection', 'segment_coverage_pct', '80', 'integer', '80',
 'Peer coverage share (percent) above which a segment counts as security-covered (D2)'),
('anomaly_detection', 'eligible_categories_for_coverage', '["Server", "Workstation", "Laptop"]', 'json', '["Server", "Workstation", "Laptop"]',
 'Asset categories expected to carry security tooling')
ON CONFLICT (category, key) DO NOTHING;


--
-- Seed data for migration 126 (alert_events retention settings).
-- Mirrors src/database/migrations/126_create_alert_events.sql.
--

INSERT INTO public.system_settings (category, key, value, value_type, default_value, description)
VALUES
('retention', 'ALERT_EVENTS_RETENTION_DAYS', '90', 'integer', '90',
 'Keep alert firing-history rows from the last N days'),
('retention', 'ALERT_EVENTS_RETENTION_COUNT', '30', 'integer', '30',
 'Keep alert firing-history for the latest N pipeline runs (regardless of age)')
ON CONFLICT (category, key) DO NOTHING;


--
-- Seed data for the two ML anomaly rules (migration 121, shadow mode removed by migration 129 /
-- #902). Mirrors src/database/migrations/121_add_ml_shadow_rules.sql minus the dead
-- threshold.mode key; same ON CONFLICT (slug) target as the migration-117/118 blocks above.
-- Both rules seed enabled and fire through the normal alert path like every other detector.
--

INSERT INTO public.alert_rules (name, slug, description, type, severity, enabled, source, evaluation_type, detector_key, threshold)
VALUES
('Unusual New Asset', 'anomaly-ml-newcomer',
 'A newly added asset looks unlike anything else in your environment (its type, OS, maker, and sources don''t match known patterns).',
 'security', 'medium', true, 'system', 'anomaly', 'ml_newcomer',
 '{"pct_medium": 0.5, "pct_high": 0.1, "min_fit_rows": 500, "fit_budget_seconds": 10, "top_k_categories": 15, "max_fit_rows": 25000}'::jsonb),
('Unusual Change Behavior', 'anomaly-ml-behavior',
 'An asset is changing in a way that doesn''t match how similar assets normally change.',
 'data_quality', 'medium', true, 'system', 'anomaly', 'ml_behavior',
 '{"pct_medium": 0.5, "pct_high": 0.1, "min_fit_rows": 500, "fit_budget_seconds": 10, "window_runs": 30}'::jsonb)
ON CONFLICT (slug) DO NOTHING;


--
-- Seed data for migration 127 (alert delivery framework settings).
-- Mirrors src/database/migrations/127_create_alert_delivery.sql.
--

INSERT INTO public.system_settings (category, key, value, value_type, default_value, description)
VALUES
('alerting', 'ALERT_DELIVERY_ENABLED', 'true', 'boolean', 'true',
 'Master switch for outbound alert delivery (enqueue + drain)'),
('alerting', 'ALERT_DELIVERY_MAX_ATTEMPTS', '5', 'integer', '5',
 'Max send attempts per delivery before it is marked failed'),
('alerting', 'ALERT_DELIVERY_TIMEOUT_SECONDS', '10', 'integer', '10',
 'Per-attempt send timeout in seconds'),
('alerting', 'ALERT_DELIVERY_DRAIN_INTERVAL_SECONDS', '25', 'integer', '25',
 'How often the scheduler worker drains pending deliveries'),
('retention', 'ALERT_DELIVERIES_RETENTION_DAYS', '30', 'integer', '30',
 'Keep alert delivery/audit rows from the last N days')
ON CONFLICT (category, key) DO NOTHING;


--
-- Seed data for migration 130 (metrics forwarding config singleton row).
-- Mirrors src/database/migrations/130_create_metrics_forwarding_config.sql.
--

INSERT INTO public.metrics_forwarding_config (id, auth_type, enabled)
VALUES (1, 'none', false)
ON CONFLICT (id) DO NOTHING;


--
-- Name: merge_denylist; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.merge_denylist (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    identifier_type  text NOT NULL,
    identifier_value text NOT NULL,
    raw_value        text NOT NULL,
    reason           text,
    created_by       text NOT NULL,
    enabled          boolean NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now(),
    match_type       text NOT NULL DEFAULT 'exact',
    range_start      bigint,
    range_end        bigint,
    CONSTRAINT merge_denylist_type_check
        CHECK (identifier_type IN ('hostname', 'fqdn', 'mac', 'serial')),
    CONSTRAINT merge_denylist_match_type_check
        CHECK (match_type IN ('exact', 'prefix', 'suffix', 'range')),
    CONSTRAINT merge_denylist_type_match_check
        CHECK (
            (identifier_type = 'mac' AND match_type IN ('exact', 'prefix', 'range'))
            OR (identifier_type IN ('hostname', 'fqdn')
                AND match_type IN ('exact', 'prefix', 'suffix'))
            OR (identifier_type = 'serial' AND match_type = 'exact')
        ),
    CONSTRAINT merge_denylist_range_bounds_check
        CHECK (
            (match_type = 'range'
             AND range_start IS NOT NULL AND range_end IS NOT NULL
             AND range_start >= 0 AND range_end >= range_start)
            OR (match_type <> 'range' AND range_start IS NULL AND range_end IS NULL)
        ),
    CONSTRAINT merge_denylist_unique
        UNIQUE (identifier_type, identifier_value, match_type)
);


--
-- Name: idx_merge_denylist_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX IF NOT EXISTS idx_merge_denylist_enabled
    ON public.merge_denylist (enabled) WHERE enabled;


--
-- PostgreSQL database dump complete
--

--
-- Name: schema_migrations bootstrap stamp; Type: DATA; Schema: public; Owner: -
--
-- Every migration this file already embodies, recorded as applied.
--
-- Without this, a fresh install left schema_migrations EMPTY while creating the
-- 'assets' table, which is exactly the signal deploy/lib/migrate.sh's
-- bootstrap_if_empty() uses to infer 'installed before the migration runner
-- existed, therefore fully migrated'. It then stamped every file on disk as
-- applied without running any of it. That heuristic is for genuine legacy
-- adoption; it must not be the path every new customer install takes, because
-- it masks drift: a migration added here but not mirrored into this file would
-- be recorded as applied on fresh installs and skipped forever, silently and
-- permanently, with no error on this or any later upgrade.
--
-- With the list explicit, schema_migrations is non-empty on a fresh install, so
-- bootstrap_if_empty() does not fire and any migration missing from this list
-- applies normally — drift self-heals instead of being hidden.
--
-- KEEP IN SYNC with src/database/migrations/. Enforced by
-- tests/unit/test_schema_full_migration_stamp.py, which runs in the
-- 'pipeline + delivery domain (hard gate)' required check.
--

INSERT INTO public.schema_migrations (filename) VALUES
    ('003_create_excluded_records_table.sql'),
    ('003_update_assets_schema_for_arrays.sql'),
    ('004_simplify_network_types_to_text.sql'),
    ('005_rename_asset_id_mapping_to_plural.sql'),
    ('006_create_assets_history.sql'),
    ('007_create_asset_providers_history.sql'),
    ('007_remove_is_risky_os_eol.sql'),
    ('008_create_invalid_assets.sql'),
    ('009_add_temporal_tracking_to_mappings.sql'),
    ('010_create_conflict_merge_split_tables.sql'),
    ('011_add_generation_run_id_to_chronicle.sql'),
    ('012_consolidated_chronicle_schema.sql'),
    ('013_drop_legacy_chronicle_fields.sql'),
    ('014_add_lifecycle_columns.sql'),
    ('014_add_os_enrichment_jsonb.sql'),
    ('015_drop_invalid_assets_table.sql'),
    ('016_remove_unused_asset_fields.sql'),
    ('017_os_normalized_to_single_value.sql'),
    ('018_add_os_orig_and_os_array.sql'),
    ('019_fix_os_orig_to_single_value.sql'),
    ('020_add_category_prediction_columns.sql'),
    ('021_add_os_eol_tracking.sql'),
    ('022_enhance_mac_vendor_ranges.sql'),
    ('023_add_os_category_to_providers.sql'),
    ('024_authentication_schema.sql'),
    ('025_create_super_admin.sql'),
    ('026_add_schedule_fields.sql'),
    ('027_add_data_storage_tables.sql'),
    ('028_add_connection_test_fields.sql'),
    ('029_add_transformations_field.sql'),
    ('030_add_job_execution_history.sql'),
    ('031_reporting_persistence.sql'),
    ('032_report_workflow.sql'),
    ('033_add_fk_cascade_indexes.sql'),
    ('034_add_ip_primary.sql'),
    ('035_add_category_ml_columns.sql'),
    ('036_create_pipeline_runs.sql'),
    ('037_add_alert_rules_and_alerts.sql'),
    ('038_fix_alert_rule_queries.sql'),
    ('039_create_edit_asset_tables.sql'),
    ('040_add_confidence_breakdown.sql'),
    ('041_add_data_filters_to_integrations.sql'),
    ('042_system_settings.sql'),
    ('043_add_performance_indexes.sql'),
    ('044_dashboard_materialized_views.sql'),
    ('045_add_confidence_counts_to_quality_metrics.sql'),
    ('045b_reorganize_data_source_categories.sql'),
    ('045c_add_enrichment_tables.sql'),
    ('046_add_integration_groups.sql'),
    ('047_audit_trail_and_data_retention.sql'),
    ('048_add_insight_query_indexes.sql'),
    ('049_add_tags_jsonb_column.sql'),
    ('050_create_field_registry.sql'),
    ('051_seed_core_field_registry.sql'),
    ('052_fix_array_indexes_btree_to_gin.sql'),
    ('052_history_retention_settings.sql'),
    ('052_seed_ip_classification_fields.sql'),
    ('053_add_features_snapshot_to_overrides.sql'),
    ('053_batch_retention_settings.sql'),
    ('054_raw_asset_data_array_identifiers.sql'),
    ('055_seed_ip_geolocation_fields.sql'),
    ('056_seed_missing_core_fields.sql'),
    ('057_add_mapping_hash_to_ingestion_batches.sql'),
    ('058_add_time_columns_to_raw_asset_data.sql'),
    ('059_add_conflict_alert_rules.sql'),
    ('060_add_source_method.sql'),
    ('061_create_api_keys.sql'),
    ('062_unify_last_sync_to_pipeline_batch.sql'),
    ('063_last_sync_use_batch_started_at.sql'),
    ('064_add_last_run_to_provider_coverage.sql'),
    ('064_add_posture_rules.sql'),
    ('065_add_posture_rule_output.sql'),
    ('066_posture_output_mode.sql'),
    ('067_posture_metadata_and_output_check.sql'),
    ('068_posture_sub_rule_display_order.sql'),
    ('069_posture_sub_rule_history.sql'),
    ('070_rename_endpoint_protection_to_secured.sql'),
    ('071_restructure_field_identifiers.sql'),
    ('072_posture_output_value_to_jsonb.sql'),
    ('073_clear_orphan_posture_state.sql'),
    ('074_add_chat_tables.sql'),
    ('075_chat_sessions_updated_at_index.sql'),
    ('076_chat_messages_created_at_index.sql'),
    ('077_add_chat_tool_call_artifact.sql'),
    ('078_extend_alert_rules_for_user_rules.sql'),
    ('079_posture_tier_snapshots.sql'),
    ('080_reseed_posture_default_sub_rules.sql'),
    ('081_remove_deprecated_alert_rules.sql'),
    ('082_add_email_settings.sql'),
    ('083_prometheus_alerts_schema.sql'),
    ('084_add_evaluation_type_and_watchdog_rules.sql'),
    ('085_add_token_type_to_password_reset_tokens.sql'),
    ('086_add_auth_settings.sql'),
    ('087_add_token_verify_rate_limit_setting.sql'),
    ('088_add_ldap_settings.sql'),
    ('089_add_oidc_settings.sql'),
    ('090_add_system_info_table.sql'),
    ('091_add_oidc_email_allowlist.sql'),
    ('092_arrayify_owner_location_user_ipam.sql'),
    ('093_add_integration_technologies.sql'),
    ('094_reference_data_updates.sql'),
    ('095_add_network_configuration_management_category.sql'),
    ('095_add_teams_bridge.sql'),
    ('096_seed_teams_settings.sql'),
    ('097_add_teams_messaging_endpoint.sql'),
    ('098_add_security_public_base_url.sql'),
    ('099_pipeline_stage_durations.sql'),
    ('099_remove_mfa_backup_codes.sql'),
    ('100_remove_require_mfa_for_admins.sql'),
    ('101_password_expiration_foundation.sql'),
    ('102_multi_method_user_mfa.sql'),
    ('103_add_asset_label_overrides.sql'),
    ('103_add_custom_subcategories.sql'),
    ('103_add_oidc_post_logout_redirect_uri.sql'),
    ('104_chat_providers_table.sql'),
    ('105_extend_chat_providers_for_bedrock.sql'),
    ('106_add_foundry_chat_provider.sql'),
    ('107_add_telemetry_derived_category.sql'),
    ('108_add_windows_events_custom_tiles.sql'),
    ('109_normalize_operator_codes.sql'),
    ('110_confidence_threshold_functions_and_views.sql'),
    ('111_posture_coverage_snapshots.sql'),
    ('112_add_assets_search_trgm_index.sql'),
    ('113_create_saved_filters.sql'),
    ('114_create_unified_fields.sql'),
    ('115_add_removal_grace_period.sql'),
    ('116_add_chat_routing_settings.sql'),
    ('117_add_anomaly_detection.sql'),
    ('118_add_phase2_anomaly_rules.sql'),
    ('119_add_ootb_rules_and_cadence.sql'),
    ('120_add_auth_audit_rules.sql'),
    ('121_add_ml_shadow_rules.sql'),
    ('122_remove_low_signal_ootb_rules.sql'),
    ('123_sharpen_auth_role_granted_rule.sql'),
    ('124_add_preserve_fields_to_integrations.sql'),
    ('125_add_privileged_account_created_rule.sql'),
    ('126_create_alert_events.sql'),
    ('127_create_alert_delivery.sql'),
    ('128_alert_category_routing.sql'),
    ('129_remove_ml_shadow_mode.sql'),
    ('130_create_metrics_forwarding_config.sql'),
    ('131_create_merge_denylist.sql'),
    ('132_remove_seeded_alert_rules.sql'),
    ('133_add_alert_subject_type.sql'),
    ('134_improve_alert_copy.sql'),
    ('135_add_denylist_match_types.sql'),
    ('136_add_history_chart_columns.sql'),
    ('137_fix_assets_disappeared_metric.sql'),
    ('138_rename_sso_default_label.sql'),
    ('139_drop_prometheus_placeholder_rules.sql'),
    ('140_make_data_source_stale_schedule_aware.sql'),
    ('141_add_pipeline_runs_data_settled_at.sql'),
    ('142_qualify_confidence_threshold_function_bodies.sql'),
    ('143_add_subcategory_predicted.sql'),
    ('144_add_audit_trail_semantic_columns.sql'),
    ('145_repoint_auth_role_granted_rule.sql'),
    ('146_archive_and_drop_auth_audit_log.sql'),
    ('147_revise_ootb_posture_rules.sql'),
    ('148_add_assets_sort_indexes.sql'),
    ('149_add_unified_values_materialization.sql')
ON CONFLICT DO NOTHING;
