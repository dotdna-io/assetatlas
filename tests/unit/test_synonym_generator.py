# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for SynonymGenerator.update_lookup_table_for_fields.

Focus: the `replace` mode used by per-field Regen and "Regenerate All". These
isolate the strip/merge behaviour (rule + embedding layers are stubbed out) so
they run without the ~490 MB embedding model.
"""
import json
from types import SimpleNamespace


def _make_gen(tmp_path, monkeypatch):
    from src.services.embedding.synonym_generator import SynonymGenerator
    gen = SynonymGenerator.__new__(SynonymGenerator)
    gen.config = SimpleNamespace(
        synonym_output_path=str(tmp_path / 'synonym_lookup.json'),
        synonym_similarity_threshold=0.62,
        model_name='test-model',
    )
    # Non-empty so _load_fields() is skipped; we hand-populate the definitions.
    gen._field_embeddings = {'beszel_cores': object()}
    gen._field_definitions = {
        'beszel_cores': SimpleNamespace(
            field_name='beszel_cores', data_source='Beszel',
            description='beszel_cores - CPU cores.',
            display_name='Beszel Cores', field_type='extended'),
    }
    gen.llm_synonym_gen = None
    # No rule/embedding matches → the term loop adds nothing, isolating the
    # replace/strip behaviour under test.
    monkeypatch.setattr(
        'src.services.embedding.synonym_generator.match_rules',
        lambda term, descs: [])
    monkeypatch.setattr(gen, '_find_best_match', lambda term: None)
    return gen


def test_replace_strips_target_field_terms_but_keeps_core(tmp_path, monkeypatch):
    gen = _make_gen(tmp_path, monkeypatch)
    lookup = tmp_path / 'synonym_lookup.json'
    lookup.write_text(json.dumps({
        'synonyms': {
            'cpu cores': 'beszel__cores',     # target field's stale terms
            'processors': 'beszel__cores',
            'hostname': 'hostname',           # Core — must survive
            'computer name': 'hostname',
        },
        'fields': {},
    }))

    gen.update_lookup_table_for_fields(
        ['beszel_cores'], use_llm=False, replace=True)

    result = json.loads(lookup.read_text())['synonyms']
    # Target field's old terms are gone (replaced, not appended)…
    assert 'cpu cores' not in result
    assert 'processors' not in result
    # …and every Core entry is preserved verbatim.
    assert result['hostname'] == 'hostname'
    assert result['computer name'] == 'hostname'


def test_no_replace_keeps_existing_target_terms(tmp_path, monkeypatch):
    # Without replace, a field's existing terms are preserved (append-only) —
    # the contrast case that proves `replace` is what removes them.
    gen = _make_gen(tmp_path, monkeypatch)
    lookup = tmp_path / 'synonym_lookup.json'
    lookup.write_text(json.dumps({
        'synonyms': {'cpu cores': 'beszel__cores', 'hostname': 'hostname'},
        'fields': {},
    }))

    gen.update_lookup_table_for_fields(
        ['beszel_cores'], use_llm=False, replace=False)

    result = json.loads(lookup.read_text())['synonyms']
    assert result['cpu cores'] == 'beszel__cores'   # still there
    assert result['hostname'] == 'hostname'


def test_replace_never_strips_a_static_field(tmp_path, monkeypatch):
    # Defense in depth: even if a Core/static field name is passed with
    # replace=True, its curated entries must not be stripped.
    gen = _make_gen(tmp_path, monkeypatch)
    gen._field_definitions['hostname'] = SimpleNamespace(
        field_name='hostname', data_source=None, description='x',
        display_name='Hostname', field_type='static')
    lookup = tmp_path / 'synonym_lookup.json'
    lookup.write_text(json.dumps({
        'synonyms': {'hostname': 'hostname', 'computer name': 'hostname'},
        'fields': {},
    }))

    gen.update_lookup_table_for_fields(
        ['hostname'], use_llm=False, replace=True)

    result = json.loads(lookup.read_text())['synonyms']
    assert result['hostname'] == 'hostname'          # not stripped
    assert result['computer name'] == 'hostname'


def test_seed_core_fills_missing_core_synonyms(tmp_path, monkeypatch):
    # seed_core=True restores authoritative Core synonyms even from an empty
    # file (the "Regenerate All recovers a complete table" case).
    gen = _make_gen(tmp_path, monkeypatch)
    gen._field_definitions['hostname'] = SimpleNamespace(
        field_name='hostname', data_source=None,
        description='hostname - computer name.',
        display_name='Hostname', field_type='static')
    lookup = tmp_path / 'synonym_lookup.json'
    lookup.write_text(json.dumps({'synonyms': {}, 'fields': {}}))

    gen.update_lookup_table_for_fields(
        ['beszel_cores'], use_llm=False, replace=True, seed_core=True)

    result = json.loads(lookup.read_text())['synonyms']
    assert result.get('hostname') == 'hostname'
    assert result.get('computer name') == 'hostname'


def test_no_seed_core_leaves_core_absent(tmp_path, monkeypatch):
    # Contrast: without seed_core the empty file stays without Core synonyms
    # (proves the seeding is what fills them, not a side effect).
    gen = _make_gen(tmp_path, monkeypatch)
    gen._field_definitions['hostname'] = SimpleNamespace(
        field_name='hostname', data_source=None, description='x',
        display_name='Hostname', field_type='static')
    lookup = tmp_path / 'synonym_lookup.json'
    lookup.write_text(json.dumps({'synonyms': {}, 'fields': {}}))

    gen.update_lookup_table_for_fields(
        ['beszel_cores'], use_llm=False, replace=True)

    result = json.loads(lookup.read_text())['synonyms']
    assert 'hostname' not in result
