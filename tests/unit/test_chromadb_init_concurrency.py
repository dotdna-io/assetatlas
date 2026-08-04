# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Regression test for the gunicorn 4-worker ChromaDB init race.

ChromaDB 1.x bootstraps its internal SQLite metadata schema (table
`collections` etc.) without IF NOT EXISTS. With four gunicorn workers
all calling ChromaDBStore() near-simultaneously on a fresh data dir,
some workers crashed with `(code: 1) table collections already exists`,
leaving SuggestionService unavailable in those workers.

The fix in chromadb_store.py wraps client init in an fcntl.flock-guarded
critical section. This test exercises the race by spawning four real
processes that all init against the same fresh persist path. Without the
lock, this test reproduces the failure intermittently; with the lock it
is deterministic.

Marked `slow` because each process pays ChromaDB's ~1-2s import cost.
"""
import multiprocessing as mp
from pathlib import Path

import pytest


def _init_one_worker(persist_path: str):
    """Subprocess entry point. Must be top-level so spawn() can pickle it."""
    from src.services.embedding.chromadb_store import ChromaDBStore
    from src.services.embedding.config import EmbeddingConfig

    cfg = EmbeddingConfig(chromadb_path=persist_path)
    store = ChromaDBStore(config=cfg)
    stats = store.get_collection_stats()
    return {
        "fields": stats["fields_count"],
        "values": stats["values_count"],
    }


@pytest.mark.slow
def test_chromadb_init_is_safe_under_4_concurrent_workers(tmp_path):
    """All four gunicorn-style concurrent inits succeed on a fresh path."""
    persist_path = str(tmp_path / "chromadb")
    Path(persist_path).mkdir(parents=True, exist_ok=True)

    # Use spawn explicitly: matches the gunicorn default and is the worst
    # case (each worker re-imports chromadb fresh, no shared state).
    ctx = mp.get_context("spawn")
    with ctx.Pool(4) as pool:
        results = pool.map(_init_one_worker, [persist_path] * 4)

    assert len(results) == 4
    for r in results:
        assert r["fields"] == 0
        assert r["values"] == 0

    lock_file = Path(persist_path) / ".init.lock"
    assert lock_file.exists(), "init lock should be created in the persist dir"
