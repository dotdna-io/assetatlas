# SPDX-License-Identifier: AGPL-3.0-or-later
"""Data access for anomaly detection — raw SQL via db_adapter (%(name)s style).

Thin layer: every method returns rows/scalars; shaping (nesting segment
profiles, series extraction) lives in the detectors so this stays testable
with a mock db_adapter.
"""

ENABLED_RULES_SQL = """
    SELECT id, name, slug, description, type, severity, enabled, source,
           evaluation_type, detector_key, threshold, silence_duration
    FROM alert_rules
    WHERE evaluation_type = 'anomaly' AND enabled = true
    ORDER BY slug
"""

RUN_STATS_SQL = """
    SELECT started_at, triggered_by, stats
    FROM pipeline_runs
    WHERE status = 'completed' AND stats IS NOT NULL AND started_at < %(before)s
    ORDER BY started_at DESC LIMIT %(limit)s
"""

CREATED_ASSETS_SQL = """
    SELECT c.asset_id, a.category, a.ip, a.manufacturer, a.os_normalized,
           a.data_source, a.data_source_category, a.confidence, a.first_seen
    FROM asset_chronicle c JOIN assets a ON a.id = c.asset_id
    WHERE c.generation_run_id = %(grid)s::uuid AND c.event_type = 'asset_created'
      AND COALESCE(c.causation, 'scheduled_scan') != 'pipeline_merge'
"""

# Capped evidence-sample asset_ids for a given event_type this run (no
# join to assets — the sample only needs ids for later label resolution).
GET_EVENT_ASSET_IDS_SQL = """
    SELECT DISTINCT c.asset_id
    FROM asset_chronicle c
    WHERE c.generation_run_id = %(grid)s::uuid AND c.event_type = %(event_type)s
      AND COALESCE(c.causation, 'scheduled_scan') != 'pipeline_merge'
    LIMIT %(limit)s
"""

DS_LOSS_EVENTS_SQL = """
    SELECT c.asset_id, c.attribute_changes->'data_source' AS ds_change,
           a.category, a.data_source AS current_sources
    FROM asset_chronicle c JOIN assets a ON a.id = c.asset_id
    WHERE c.generation_run_id = %(grid)s::uuid AND c.event_type = 'attribute_updated'
      AND 'data_source' = ANY(c.changed_attributes)
      AND COALESCE(c.causation, 'scheduled_scan') != 'pipeline_merge'
      AND jsonb_array_length(COALESCE(c.attribute_changes->'data_source'->'removed', '[]'::jsonb)) > 0
"""

DS_LOSS_HISTORY_SQL = """
    SELECT c.generation_run_id, max(c."time") AS run_time, count(*) AS loss_count
    FROM asset_chronicle c
    WHERE c.event_type = 'attribute_updated' AND 'data_source' = ANY(c.changed_attributes)
      AND COALESCE(c.causation, 'scheduled_scan') != 'pipeline_merge'
      AND jsonb_array_length(COALESCE(c.attribute_changes->'data_source'->'removed', '[]'::jsonb)) > 0
      AND c."time" > now() - (%(days)s || ' days')::interval
      AND c.generation_run_id IS DISTINCT FROM %(exclude_grid)s::uuid
    GROUP BY c.generation_run_id ORDER BY run_time
"""

# Segment = /24 of the FIRST RFC1918 address in ip[] — the same rule as
# detectors/population.derive_segment (spec §14.5), so a created asset and its
# segment profile are keyed identically. The CASE-guarded cast means ::inet only
# ever sees a strict-octet-validated string, so a malformed ip[] element (e.g.
# '256.1.1.1') can't abort the whole query.
_STRICT_IPV4 = (r'^(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
                r'(\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])){3}$')
SEGMENT_PROFILE_SQL = """
    SELECT sub.segment, a.category, count(*) AS n
    FROM assets a
    CROSS JOIN LATERAL (
        SELECT network(set_masklen(g.ip_inet, 24))::text AS segment
        FROM (
            SELECT (CASE WHEN e.ip ~ %(strict_ipv4)s THEN e.ip::inet END) AS ip_inet, e.ord
            FROM unnest(a.ip) WITH ORDINALITY AS e(ip, ord)
        ) g
        WHERE g.ip_inet IS NOT NULL
          AND g.ip_inet <<= ANY (ARRAY['10.0.0.0/8'::inet, '172.16.0.0/12'::inet,
                                       '192.168.0.0/16'::inet])
        ORDER BY g.ord
        LIMIT 1
    ) sub
    WHERE a.category IS NOT NULL
      AND a.first_seen < %(run_started)s
      AND sub.segment = ANY(%(segments)s)
    GROUP BY sub.segment, a.category
"""

SOURCE_CATEGORY_SQL = """
    SELECT data_source[1] AS source, data_source_category[1] AS category, count(*) AS n
    FROM assets
    WHERE array_length(data_source, 1) = 1 AND array_length(data_source_category, 1) = 1
    GROUP BY 1, 2
"""

ATTR_BREAKDOWN_SQL = """
    SELECT attr, count(*) AS n FROM asset_chronicle c, unnest(c.changed_attributes) AS attr
    WHERE c.generation_run_id = %(grid)s::uuid AND c.event_type = 'attribute_updated'
      AND COALESCE(c.causation, 'scheduled_scan') != 'pipeline_merge'
    GROUP BY attr ORDER BY n DESC
"""

# A4 scattered churn: attribute_updated events with at least one NON-systemic
# changed attribute. Counting EVENTS (not attr occurrences) with a subset check
# avoids the old `total - systemic_sum` double-subtraction on events that touched
# both a systemic and a non-systemic attribute.
SCATTERED_EVENT_COUNT_SQL = """
    SELECT count(*) AS n FROM asset_chronicle c
    WHERE c.generation_run_id = %(grid)s::uuid AND c.event_type = 'attribute_updated'
      AND COALESCE(c.causation, 'scheduled_scan') != 'pipeline_merge'
      AND NOT (COALESCE(c.changed_attributes, ARRAY[]::text[]) <@ %(systemic)s::text[])
"""

MISSING_ONSETS_SQL = """
    SELECT count(*) AS n FROM assets WHERE missing_since >= %(run_started)s
"""

ACTIVE_ANOMALY_ALERTS_SQL = """
    SELECT id, fingerprint, created_at, severity FROM alerts
    WHERE rule_id = %(rule_id)s AND status = 'active' AND fingerprint IS NOT NULL
"""

RECENT_FP_SQL = """
    SELECT DISTINCT fingerprint FROM alerts
    WHERE rule_id = %(rule_id)s AND created_at > %(since)s AND fingerprint IS NOT NULL
"""

# B3 cross-day dedup: any fingerprint starting with a given (dateless) prefix.
# The prefix is escaped + wildcard-appended in Python so LIKE metacharacters in
# it ('identity_swap', underscores/percent in hostnames/serials) stay literal.
RECENT_FP_LIKE_SQL = """
    SELECT DISTINCT fingerprint FROM alerts
    WHERE rule_id = %(rule_id)s AND created_at > %(since)s AND fingerprint IS NOT NULL
      AND fingerprint LIKE %(prefix)s ESCAPE '\\'
"""

CONF_HIST_SQL = """
    SELECT count(*) FILTER (WHERE confidence < %(med)s)                          AS low,
           count(*) FILTER (WHERE confidence >= %(med)s AND confidence < %(high)s) AS medium,
           count(*) FILTER (WHERE confidence >= %(high)s)                         AS high
    FROM assets
"""

# --- Phase-2 detector queries -------------------------------------------------

# B1a: distinct changed attributes per asset THIS run (excl. pipeline_merge),
# HAVING >= min_attrs. Systemic-attr exclusion happens in the detector.
ASSET_CHANGE_BURSTS_SQL = """
    SELECT c.asset_id, array_agg(DISTINCT attr) AS attrs, count(DISTINCT attr) AS n
    FROM asset_chronicle c, unnest(c.changed_attributes) AS attr
    WHERE c.generation_run_id = %(grid)s::uuid AND c.event_type = 'attribute_updated'
      AND COALESCE(c.causation, 'scheduled_scan') != 'pipeline_merge'
    GROUP BY c.asset_id
    HAVING count(DISTINCT attr) >= %(min_attrs)s
"""

# The last %(runs)s generation runs (by time) that produced non-merge
# attribute_updated events — the true "window_runs" window B1b thresholds on. A
# day-based window would be cadence-dependent (over-fire on sub-daily reruns,
# starve on sparse schedules) and make the "N of the last M runs" narrative false.
_RECENT_RUNS_CTE = """
    WITH recent_runs AS (
        SELECT generation_run_id, max("time") AS rt
        FROM asset_chronicle
        WHERE event_type = 'attribute_updated' AND generation_run_id IS NOT NULL
          AND COALESCE(causation, 'scheduled_scan') != 'pipeline_merge'
        GROUP BY generation_run_id
        ORDER BY rt DESC
        LIMIT %(runs)s
    )
"""

# B1b: per asset per run over the last %(runs)s runs, the distinct attributes it
# changed. generation_run_id is the run key; max(time) buckets the group (spec §3).
ASSET_RUN_ACTIVITY_SQL = _RECENT_RUNS_CTE + """
    SELECT c.asset_id, c.generation_run_id AS run_id,
           max(c."time") AS run_date, array_agg(DISTINCT attr) AS attrs
    FROM asset_chronicle c, unnest(c.changed_attributes) AS attr
    WHERE c.generation_run_id IN (SELECT generation_run_id FROM recent_runs)
      AND c.event_type = 'attribute_updated'
      AND COALESCE(c.causation, 'scheduled_scan') != 'pipeline_merge'
    GROUP BY c.asset_id, c.generation_run_id
"""

# B1b: per-run per-attribute change counts over the same last %(runs)s runs, so
# the detector can rebuild each run's systemic set. count(*) matches A4/B1a's
# get_attribute_breakdown so "systemic" is defined identically across the B-family.
RUN_ATTR_TOTALS_SQL = _RECENT_RUNS_CTE + """
    SELECT c.generation_run_id AS run_id, attr, count(*) AS n
    FROM asset_chronicle c, unnest(c.changed_attributes) AS attr
    WHERE c.generation_run_id IN (SELECT generation_run_id FROM recent_runs)
      AND c.event_type = 'attribute_updated'
      AND COALESCE(c.causation, 'scheduled_scan') != 'pipeline_merge'
    GROUP BY c.generation_run_id, attr
"""

# B2: missing_since NULL<->set transitions per asset across history snapshots.
PRESENCE_FLAPS_SQL = """
    WITH h AS (
        SELECT id AS asset_id, archived_at, (missing_since IS NOT NULL) AS missing,
               LAG(missing_since IS NOT NULL)
                   OVER (PARTITION BY id ORDER BY archived_at) AS prev_missing
        FROM assets_history
        WHERE archived_at > now() - (%(days)s || ' days')::interval
    )
    SELECT asset_id, count(*) AS flips
    FROM h WHERE prev_missing IS NOT NULL AND missing != prev_missing
    GROUP BY asset_id HAVING count(*) >= %(min_flaps)s
"""

# B3: current stable-binding candidates.
#  hostname_sn — assets with exactly one serial and a hostname (name<->serial).
CURRENT_HOSTNAME_SN_SQL = """
    SELECT lower(hostname[1]) AS key, sn[1] AS value, id AS asset_id
    FROM assets
    WHERE array_length(sn, 1) = 1 AND array_length(hostname, 1) >= 1
      AND hostname[1] IS NOT NULL AND trim(hostname[1]) != ''
      AND sn[1] IS NOT NULL AND trim(sn[1]) != ''
"""

# B3: ip_mac — assets with exactly one MAC, keyed by the FIRST RFC1918 address in
# ip[] (ip_primary is NULL fleet-wide, §14.5). Same address-picking rule as
# detectors/population.derive_segment / SEGMENT_PROFILE_SQL, but returns the host
# address instead of the /24.
CURRENT_IP_MAC_SQL = """
    SELECT sub.ip_host AS key, a.mac[1] AS value, a.id AS asset_id
    FROM assets a
    CROSS JOIN LATERAL (
        SELECT host(g.ip_inet) AS ip_host
        FROM (
            SELECT (CASE WHEN e.ip ~ %(strict_ipv4)s THEN e.ip::inet END) AS ip_inet, e.ord
            FROM unnest(a.ip) WITH ORDINALITY AS e(ip, ord)
        ) g
        WHERE g.ip_inet IS NOT NULL
          AND g.ip_inet <<= ANY (ARRAY['10.0.0.0/8'::inet, '172.16.0.0/12'::inet,
                                       '192.168.0.0/16'::inet])
        ORDER BY g.ord
        LIMIT 1
    ) sub
    WHERE array_length(a.mac, 1) = 1
      AND a.mac[1] IS NOT NULL AND trim(a.mac[1]) != ''
"""

# D2: assets first seen inside the settle window [now-settle_max, now-settle_min].
SETTLE_WINDOW_ASSETS_SQL = """
    SELECT id AS asset_id, category, ip, data_source, first_seen
    FROM assets
    WHERE first_seen >= %(run_started)s - (%(settle_max)s || ' days')::interval
      AND first_seen <= %(run_started)s - (%(settle_min)s || ' days')::interval
"""

# D2: per segment, eligible-category ESTABLISHED peers and how many carry >= 1
# security source. Coverage is measured over peers first seen BEFORE the settle
# window (first_seen < run_start - settle_max) so this run's unprotected
# newcomers — which are settle_min..settle_max days old, NOT this run — don't
# dilute their own segment's coverage (a bulk unprotected drop would otherwise
# push the segment below the threshold and suppress the very alert D2 exists for).
SEGMENT_COVERAGE_SQL = """
    SELECT sub.segment, count(*) AS eligible_n,
           count(*) FILTER (WHERE a.data_source && %(security)s::text[]) AS covered_n
    FROM assets a
    CROSS JOIN LATERAL (
        SELECT network(set_masklen(g.ip_inet, 24))::text AS segment
        FROM (
            SELECT (CASE WHEN e.ip ~ %(strict_ipv4)s THEN e.ip::inet END) AS ip_inet, e.ord
            FROM unnest(a.ip) WITH ORDINALITY AS e(ip, ord)
        ) g
        WHERE g.ip_inet IS NOT NULL
          AND g.ip_inet <<= ANY (ARRAY['10.0.0.0/8'::inet, '172.16.0.0/12'::inet,
                                       '192.168.0.0/16'::inet])
        ORDER BY g.ord
        LIMIT 1
    ) sub
    WHERE LOWER(a.category) = ANY(%(eligible)s)
      AND a.first_seen < %(run_started)s - (%(settle_max)s || ' days')::interval
      AND sub.segment = ANY(%(segments)s)
    GROUP BY sub.segment
"""

# --- Phase-5 ML detector queries ---------------------------------------------

# ML newcomer fit set: the pre-existing fleet (assets first seen before this run),
# as RAW feature columns. The detector shapes these into feature vectors in Python
# with the SAME function it applies to this run's created assets (get_created_assets
# now returns the same columns), so fit and score rows are feature-identical.
# `ORDER BY id LIMIT` is a deterministic cap (max_fit_rows), not a sample.
ML_NEWCOMER_FIT_SQL = """
    SELECT category, os_normalized, manufacturer, data_source, data_source_category,
           confidence
    FROM assets
    WHERE first_seen < %(run_started)s
    ORDER BY id
    LIMIT %(limit)s
"""

# ML behavior fit/score set: per-asset change-behaviour aggregates over the last
# %(runs)s runs. Reuses B1b's recent-runs window CTE + the same non-merge
# attribute_updated filters (no duplicated window SQL). The detector both fits and
# scores on these rows — the fleet's behaviour distribution vs each asset's window.
ML_BEHAVIOR_ROWS_SQL = _RECENT_RUNS_CTE + """
    SELECT c.asset_id,
           count(DISTINCT c.generation_run_id) AS runs_active,
           count(DISTINCT attr)                AS distinct_attrs,
           count(*)                            AS event_count
    FROM asset_chronicle c, unnest(c.changed_attributes) AS attr
    WHERE c.generation_run_id IN (SELECT generation_run_id FROM recent_runs)
      AND c.event_type = 'attribute_updated'
      AND COALESCE(c.causation, 'scheduled_scan') != 'pipeline_merge'
    GROUP BY c.asset_id
"""

# --- Alert-copy asset-label resolution (Task 4) -------------------------------

# Canonical asset_label COALESCE — mirrors src/modules/fields/catalog.py:180-193
# exactly, so anomaly alert copy shows the same label Explorer shows.
ASSET_LABELS_SQL = """
    SELECT id,
           COALESCE(NULLIF(asset_label_override, ''), NULLIF(hostname[1], ''),
                    NULLIF(fqdn[1], ''), NULLIF(mac[1], ''), NULLIF(sn[1], ''),
                    NULLIF(ip[1], '')) AS label
    FROM assets
    WHERE id = ANY(%(ids)s::uuid[])
"""


def _unambiguous_pairs(rows):
    """(key, value, asset_id) triples where the key maps to exactly ONE value
    across the fleet. A key carried by several assets with different values (a
    shared hostname like 'localhost', a reused private IP) is not an identity
    binding — dropping it stops B3 from manufacturing a false identity-swap."""
    values = {}
    for r in rows:
        k, v, aid = r.get('key'), r.get('value'), r.get('asset_id')
        if k and v is not None:
            values.setdefault(k, {})[v] = aid
    out = []
    for k, vs in values.items():
        if len(vs) == 1:
            (v, aid), = vs.items()
            out.append((k, v, aid))
    return out


class AnomalyRepository:
    def __init__(self, db_adapter):
        self.db = db_adapter

    def get_enabled_anomaly_rules(self):
        return self.db.execute_query(ENABLED_RULES_SQL) or []

    def get_recent_run_stats(self, before, limit=120):
        return self.db.execute_query(RUN_STATS_SQL, {'before': before, 'limit': limit}) or []

    def get_created_assets(self, generation_run_id):
        return self.db.execute_query(
            CREATED_ASSETS_SQL, {'grid': str(generation_run_id)}) or []

    def get_event_asset_ids(self, generation_run_id, event_type, limit=25):
        """A capped sample of asset_ids that had `event_type` in this run — the
        evidence sample for metric alerts whose count refers to a natural asset
        list (mass-new, re-added). Order is unspecified (a sample, not a ranking)."""
        rows = self.db.execute_query(
            GET_EVENT_ASSET_IDS_SQL,
            {'grid': str(generation_run_id), 'event_type': event_type, 'limit': limit}) or []
        return [str(r['asset_id']) for r in rows if r.get('asset_id')]

    def get_datasource_loss_events(self, generation_run_id):
        return self.db.execute_query(
            DS_LOSS_EVENTS_SQL, {'grid': str(generation_run_id)}) or []

    def get_datasource_loss_history(self, days, exclude_grid=None):
        return self.db.execute_query(
            DS_LOSS_HISTORY_SQL,
            {'days': days, 'exclude_grid': str(exclude_grid) if exclude_grid else None}) or []

    def get_segment_profiles(self, segments, run_started_at):
        return self.db.execute_query(
            SEGMENT_PROFILE_SQL,
            {'segments': segments, 'run_started': run_started_at,
             'strict_ipv4': _STRICT_IPV4}) or []

    def get_attribute_breakdown(self, generation_run_id):
        return self.db.execute_query(
            ATTR_BREAKDOWN_SQL, {'grid': str(generation_run_id)}) or []

    def get_scattered_event_count(self, generation_run_id, systemic_attrs):
        """A4: count of this run's attribute_updated events that changed at least
        one attribute outside the systemic set (exact scattered-churn count)."""
        rows = self.db.execute_query(
            SCATTERED_EVENT_COUNT_SQL,
            {'grid': str(generation_run_id), 'systemic': sorted(systemic_attrs)}) or []
        return int(rows[0]['n']) if rows else 0

    def get_source_category_map(self):
        """data_source name -> majority data_source_category (over single-source assets)."""
        rows = self.db.execute_query(SOURCE_CATEGORY_SQL) or []
        best = {}  # source -> (category, n)
        for r in rows:
            source, category, n = r.get('source'), r.get('category'), r.get('n') or 0
            if source is None or category is None:
                continue
            if source not in best or n > best[source][1]:
                best[source] = (category, n)
        return {source: cat for source, (cat, _n) in best.items()}

    def get_missing_onset_count(self, run_started_at):
        rows = self.db.execute_query(MISSING_ONSETS_SQL, {'run_started': run_started_at}) or []
        return int(rows[0]['n']) if rows else 0

    def get_active_anomaly_alerts(self, rule_id):
        return self.db.execute_query(ACTIVE_ANOMALY_ALERTS_SQL, {'rule_id': rule_id}) or []

    def get_recent_alert_fingerprints(self, rule_id, since):
        rows = self.db.execute_query(
            RECENT_FP_SQL, {'rule_id': rule_id, 'since': since}) or []
        return {r['fingerprint'] for r in rows if r.get('fingerprint')}

    def get_recent_fingerprints_like(self, rule_id, prefix, since):
        """Recent fingerprints for a rule that start with `prefix` (dateless).
        Used by B3 to suppress a given old->new flip for `stability_days`."""
        like = (prefix.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                + '%')
        rows = self.db.execute_query(
            RECENT_FP_LIKE_SQL,
            {'rule_id': rule_id, 'prefix': like, 'since': since}) or []
        return {r['fingerprint'] for r in rows if r.get('fingerprint')}

    def get_total_assets(self):
        """Current fleet size (committed assets). A4 needs it for the systemic
        share; it is not present in the in-stage gen-stats at s20."""
        rows = self.db.execute_query("SELECT count(*) AS n FROM assets") or []
        return int(rows[0]["n"]) if rows else 0

    def get_confidence_histogram(self, med, high):
        """{low, medium, high} fleet confidence counts (warms A6 for Phase 2)."""
        rows = self.db.execute_query(CONF_HIST_SQL, {'med': med, 'high': high}) or []
        r = rows[0] if rows else {}
        return {'low': int(r.get('low') or 0), 'medium': int(r.get('medium') or 0),
                'high': int(r.get('high') or 0)}

    def get_confidence_boundaries(self):
        """(medium, high) confidence boundaries — same asset_generation settings +
        shared defaults the fleet-wide bands use, so A6 bins assets identically to
        dashboard/charts/chat. Lives here so both the service (s20 histogram) and
        the A6 detector (via ctx.repo) resolve boundaries the same way."""
        from src.api.modules.settings.settings_schema import DEFAULT_CONFIDENCE_THRESHOLDS
        default_med, default_high = DEFAULT_CONFIDENCE_THRESHOLDS
        rows = self.db.execute_query(
            "SELECT key, value FROM system_settings WHERE category = 'asset_generation' "
            "AND key IN ('confidence_threshold_medium', 'confidence_threshold_high')") or []
        vals = {r['key']: r['value'] for r in rows}

        def parse(key, default):
            try:
                return float(vals[key])
            except (KeyError, TypeError, ValueError):
                return default
        return (parse('confidence_threshold_medium', default_med),
                parse('confidence_threshold_high', default_high))

    # --- Phase-2 detector reads ----------------------------------------------

    def get_asset_change_bursts(self, generation_run_id, min_attrs):
        """B1a: [{asset_id, attrs, n}] — assets that changed >= min_attrs distinct
        attributes this run (before systemic exclusion)."""
        return self.db.execute_query(
            ASSET_CHANGE_BURSTS_SQL,
            {'grid': str(generation_run_id), 'min_attrs': min_attrs}) or []

    def get_asset_run_activity(self, window_runs):
        """B1b: [{asset_id, run_id, run_date, attrs}] — per asset per run over the
        last window_runs runs."""
        return self.db.execute_query(
            ASSET_RUN_ACTIVITY_SQL, {'runs': window_runs}) or []

    def get_run_attr_totals(self, window_runs):
        """B1b: [{run_id, attr, n}] — per-run per-attribute change counts over the
        last window_runs runs."""
        return self.db.execute_query(
            RUN_ATTR_TOTALS_SQL, {'runs': window_runs}) or []

    def get_presence_flaps(self, window_days, min_flaps):
        """B2: [{asset_id, flips}] — assets with >= min_flaps present<->missing
        transitions in the window."""
        return self.db.execute_query(
            PRESENCE_FLAPS_SQL, {'days': window_days, 'min_flaps': min_flaps}) or []

    def get_current_bindings(self):
        """B3: current identity-binding candidates for each kind, as (key, value,
        asset_id) triples. A binding contributes only when the asset is unambiguous
        (single sn / single mac) AND the key resolves to a single value fleet-wide
        (see _unambiguous_pairs — collision guard)."""
        hn = self.db.execute_query(CURRENT_HOSTNAME_SN_SQL) or []
        im = self.db.execute_query(CURRENT_IP_MAC_SQL, {'strict_ipv4': _STRICT_IPV4}) or []
        return {'hostname_sn': _unambiguous_pairs(hn), 'ip_mac': _unambiguous_pairs(im)}

    def get_settle_window_assets(self, run_started_at, settle_min_days, settle_max_days):
        """D2: [{asset_id, category, ip, data_source, first_seen}] for assets first
        seen in [now-settle_max, now-settle_min]."""
        return self.db.execute_query(
            SETTLE_WINDOW_ASSETS_SQL,
            {'run_started': run_started_at,
             'settle_min': settle_min_days, 'settle_max': settle_max_days}) or []

    def get_segment_coverage(self, segments, eligible_categories, security_sources,
                             run_started_at, settle_max_days):
        """D2: [{segment, eligible_n, covered_n}] over eligible-category peers
        established before the settle window (first_seen < run_start -
        settle_max_days), so the newcomers under evaluation are excluded."""
        return self.db.execute_query(
            SEGMENT_COVERAGE_SQL,
            {'segments': segments, 'eligible': eligible_categories,
             'security': security_sources, 'run_started': run_started_at,
             'settle_max': settle_max_days, 'strict_ipv4': _STRICT_IPV4}) or []

    # --- Phase-5 ML detector reads -------------------------------------------

    def get_ml_newcomer_fit_rows(self, run_started_at, limit):
        """ML newcomer: pre-existing fleet feature rows (deterministic id-ordered
        cap at `limit` = max_fit_rows)."""
        return self.db.execute_query(
            ML_NEWCOMER_FIT_SQL, {'run_started': run_started_at, 'limit': limit}) or []

    def get_ml_behavior_rows(self, window_runs):
        """ML behavior: [{asset_id, runs_active, distinct_attrs, event_count}] per
        asset over the last `window_runs` runs (fit and score population)."""
        return self.db.execute_query(ML_BEHAVIOR_ROWS_SQL, {'runs': window_runs}) or []

    # --- Alert-copy label resolution (Task 4) --------------------------------

    def get_asset_labels(self, ids):
        """{asset_id: canonical asset_label} for the given ids (mirrors fields/catalog.py
        'asset_label'). Unlabeled/absent ids are omitted. Empty input -> {}."""
        if not ids:
            return {}
        results = self.db.execute_query(ASSET_LABELS_SQL, {'ids': list(ids)}) or []
        return {str(r['id']): r['label'] for r in results if r.get('label')}
