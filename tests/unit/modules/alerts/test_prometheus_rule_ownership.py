# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who owns a Prometheus alertname in alert_rules.

Two guards in two modules agree on one column, and a seed row broke both at once:

    PrometheusWebhookService._lookup_rule_id
        WHERE slug = %(slug)s AND source = 'prometheus'
    sync_prometheus_rules
        ON CONFLICT (slug) DO UPDATE ... WHERE alert_rules.source = 'prometheus'

Migration 119 seeded four Prometheus alertnames as source='system' placeholders.
The sync's INSERT then conflicted, its DO UPDATE was filtered out by the source
guard, and no prometheus-owned row was ever written — so every one of those
alerts was dropped on arrival with "No alert_rules row for alertname=...".
Seven of eleven rules worked, and only because nothing had seeded placeholders
for them.

These tests pin the invariant that made the failure possible, not the four
specific slugs: a slug that Prometheus evaluates must not be seeded under some
other source.
"""
import glob
import os
import re

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
ALERTS_DIR = os.path.join(REPO_ROOT, "monitoring", "alerts")
SCHEMA_FULL = os.path.join(REPO_ROOT, "src", "database", "schema-full.sql")
SYNC_MODULE = os.path.join(REPO_ROOT, "src", "modules", "alerts", "prometheus_sync.py")
WEBHOOK_MODULE = os.path.join(REPO_ROOT, "src", "modules", "alerts", "prometheus_webhook_service.py")


def _prometheus_alertnames():
    names = set()
    for path in glob.glob(os.path.join(ALERTS_DIR, "*.yml")):
        with open(path) as f:
            data = yaml.safe_load(f)
        for group in data.get("groups") or []:
            for rule in group.get("rules") or []:
                if rule.get("alert"):
                    names.add(rule["alert"])
    return names


def _seeded_alert_rules():
    """(slug, source) for every alert_rules row in the fresh-install schema.

    schema-full.sql carries the seed as a tab-separated COPY block; the header
    names the column order, which is read rather than assumed so a column added
    ahead of slug/source cannot silently shift the fields being compared.
    """
    with open(SCHEMA_FULL) as f:
        lines = f.read().split("\n")

    rows = []
    for i, line in enumerate(lines):
        if not line.startswith("COPY public.alert_rules ("):
            continue
        cols = [c.strip() for c in line[line.index("(") + 1:line.index(")")].split(",")]
        slug_at, source_at = cols.index("slug"), cols.index("source")
        for row in lines[i + 1:]:
            if row == "\\.":
                break
            fields = row.split("\t")
            if len(fields) > max(slug_at, source_at):
                rows.append((fields[slug_at], fields[source_at]))
        break
    return rows


def test_fixtures_are_present():
    """Collection pin — every test below is vacuous if either side reads empty."""
    assert _prometheus_alertnames(), "no alertnames parsed from monitoring/alerts/"
    assert _seeded_alert_rules(), "no alert_rules rows parsed from schema-full.sql"


def test_no_seeded_rule_shadows_a_prometheus_alertname():
    """The regression.

    A seeded row under any source other than 'prometheus' takes the slug, and
    both the sync and the webhook lookup then refuse to touch it — the alert is
    accepted over the wire and silently dropped.
    """
    alertnames = _prometheus_alertnames()
    shadowed = [
        f"{slug} seeded as source={source!r} but evaluated by Prometheus"
        for slug, source in _seeded_alert_rules()
        if slug in alertnames and source != "prometheus"
    ]
    assert not shadowed, "\n".join(shadowed)


def test_sync_and_webhook_agree_on_the_owning_source():
    """Both modules gate on the same literal.

    Compared as source text on purpose: the value is embedded in SQL string
    literals, not bound as a parameter, so nothing at runtime would notice the
    two drifting apart.
    """
    sync_sql = open(SYNC_MODULE).read()
    webhook_sql = open(WEBHOOK_MODULE).read()
    assert "source = 'prometheus'" in webhook_sql, "webhook lookup no longer filters on source"
    assert "alert_rules.source = 'prometheus'" in sync_sql, "sync upsert no longer guards on source"


def test_sync_pins_the_system_subject_type():
    """Migration 139 hands these slugs to the sync; the column default is 'asset'.

    Prometheus alerts carry no assets (the webhook inserts asset_count 0 and an
    empty asset_ids), and migration 133 uses 'system' for this class of rule. If
    the sync stops setting it, the rows it recreates silently become asset alerts.
    """
    sync_sql = open(SYNC_MODULE).read()
    assert "subject_type" in sync_sql, "sync no longer sets subject_type"
    # Present in both halves of the upsert, or an existing row keeps the old value.
    assert re.search(r"subject_type\s*=\s*'system'", sync_sql), "DO UPDATE does not pin subject_type"
    assert "'system')" in sync_sql, "INSERT does not pin subject_type"
