# SPDX-License-Identifier: AGPL-3.0-or-later
"""Durable per-value memory (anomaly_profiles) for the fleet manufacturer/OS kinds.

Independent of the 90-day chronicle retention: bootstrapped from assets +
assets_history on first run, then upserted incrementally each run.
"""

_COUNT_SQL = "SELECT count(*) AS n FROM anomaly_profiles"

# Bootstrap: seed fleet_manufacturer/fleet_os from the live table first (real
# first/last-seen + counts), then backfill net-new values that only survive in
# history. ON CONFLICT DO NOTHING keeps the live-table rows authoritative.
_BOOTSTRAP_SQLS = [
    """
    INSERT INTO anomaly_profiles (profile_kind, key, value, first_seen, last_seen, seen_count)
    SELECT 'fleet_manufacturer', '*', lower(trim(m)),
           min(COALESCE(first_seen, now())), max(COALESCE(last_seen, now())), count(*)
    FROM assets, unnest(manufacturer) AS m
    WHERE trim(m) != ''
    GROUP BY lower(trim(m))
    ON CONFLICT (profile_kind, key, value) DO NOTHING
    """,
    """
    INSERT INTO anomaly_profiles (profile_kind, key, value, first_seen, last_seen, seen_count)
    SELECT 'fleet_os', '*', lower(trim(os_normalized)),
           min(COALESCE(first_seen, now())), max(COALESCE(last_seen, now())), count(*)
    FROM assets
    WHERE os_normalized IS NOT NULL AND trim(os_normalized) != ''
    GROUP BY lower(trim(os_normalized))
    ON CONFLICT (profile_kind, key, value) DO NOTHING
    """,
    """
    INSERT INTO anomaly_profiles (profile_kind, key, value, first_seen, last_seen, seen_count)
    SELECT 'fleet_manufacturer', '*', lower(trim(m)),
           min(COALESCE(first_seen, now())), max(COALESCE(last_seen, now())), count(*)
    FROM assets_history, unnest(manufacturer) AS m
    WHERE trim(m) != ''
    GROUP BY lower(trim(m))
    ON CONFLICT (profile_kind, key, value) DO NOTHING
    """,
    """
    INSERT INTO anomaly_profiles (profile_kind, key, value, first_seen, last_seen, seen_count)
    SELECT 'fleet_os', '*', lower(trim(os_normalized)),
           min(COALESCE(first_seen, now())), max(COALESCE(last_seen, now())), count(*)
    FROM assets_history
    WHERE os_normalized IS NOT NULL AND trim(os_normalized) != ''
    GROUP BY lower(trim(os_normalized))
    ON CONFLICT (profile_kind, key, value) DO NOTHING
    """,
]

_KNOWN_VALUES_SQL = "SELECT value FROM anomaly_profiles WHERE profile_kind = %(kind)s"

_UPSERT_SQL = """
    INSERT INTO anomaly_profiles (profile_kind, key, value, first_seen, last_seen, seen_count)
    SELECT %(kind)s, '*', v, %(seen)s, %(seen)s, 1
    FROM unnest(%(values)s::text[]) AS v
    ON CONFLICT (profile_kind, key, value) DO UPDATE
        SET last_seen = EXCLUDED.last_seen,
            seen_count = anomaly_profiles.seen_count + 1
"""

# B3 binding kinds (ip_mac / hostname_sn): key is the real key (not '*'), value
# is what that key currently binds to. A binding is "stable" once it has both
# aged past stability_days and been seen at least min_seen times; when a key
# carries several values, the most-seen wins (DISTINCT ON + seen_count DESC).
_STABLE_BINDINGS_SQL = """
    SELECT DISTINCT ON (key) key, value, seen_count,
           floor(EXTRACT(EPOCH FROM (last_seen - first_seen)) / 86400)::int AS age_days
    FROM anomaly_profiles
    WHERE profile_kind = %(kind)s
      AND seen_count >= %(min_seen)s
      AND last_seen - first_seen >= (%(days)s || ' days')::interval
    ORDER BY key, seen_count DESC, value
"""

_UPSERT_BINDINGS_SQL = """
    INSERT INTO anomaly_profiles (profile_kind, key, value, first_seen, last_seen, seen_count)
    SELECT %(kind)s, k, v, %(seen)s, %(seen)s, 1
    FROM unnest(%(keys)s::text[], %(values)s::text[]) AS t(k, v)
    ON CONFLICT (profile_kind, key, value) DO UPDATE
        SET last_seen = EXCLUDED.last_seen,
            seen_count = anomaly_profiles.seen_count + 1
"""


class ProfileStore:
    def __init__(self, db_adapter):
        self.db = db_adapter

    def bootstrap_if_empty(self) -> bool:
        rows = self.db.execute_query(_COUNT_SQL) or []
        if rows and (rows[0].get('n') or 0) > 0:
            return False
        for sql in _BOOTSTRAP_SQLS:
            self.db.execute_query(sql, fetch=False)
        return True

    def known_values(self, kind: str) -> set:
        rows = self.db.execute_query(_KNOWN_VALUES_SQL, {'kind': kind}) or []
        return {r['value'].strip().lower() for r in rows if r.get('value')}

    def upsert_values(self, kind: str, values: list, seen_at) -> None:
        normalized = sorted({v.strip().lower() for v in values if v and v.strip()})
        if not normalized:
            return
        self.db.execute_query(
            _UPSERT_SQL, {'kind': kind, 'values': normalized, 'seen': seen_at}, fetch=False)

    def get_stable_bindings(self, kind: str, stability_days, min_seen) -> dict:
        """{key: {'value', 'seen_count', 'age_days'}} for bindings past the
        stability gate (B3). The stability gate is what makes a DHCP pool address
        invisible: it never accumulates a stable binding."""
        rows = self.db.execute_query(
            _STABLE_BINDINGS_SQL,
            {'kind': kind, 'min_seen': min_seen, 'days': stability_days}) or []
        out = {}
        for r in rows:
            key, value = r.get('key'), r.get('value')
            if key is None or value is None:
                continue
            out[key] = {'value': value, 'seen_count': int(r.get('seen_count') or 0),
                        'age_days': int(r.get('age_days') or 0)}
        return out

    def upsert_bindings(self, kind: str, pairs, seen_at) -> None:
        """Batch-upsert (key, value) binding observations for this run. Duplicate
        pairs are collapsed (a single INSERT can't hit one conflict row twice)."""
        keys, values, seen = [], [], set()
        for k, v, *_ in pairs:
            if k and v and (k, v) not in seen:
                seen.add((k, v))
                keys.append(k)
                values.append(v)
        if not keys:
            return
        self.db.execute_query(
            _UPSERT_BINDINGS_SQL,
            {'kind': kind, 'keys': keys, 'values': values, 'seen': seen_at}, fetch=False)
