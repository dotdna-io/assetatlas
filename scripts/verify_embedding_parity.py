#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Assert the ONNX embedding backend still reproduces the pre-#1070 vectors.

Run at image-build time (Dockerfile.base, model-cache stage) because that is the
only place the model is guaranteed present — models/cache/ is gitignored, so the
equivalent pytest skips in CI and would enforce nothing.

Imports the REAL backend module rather than reimplementing the encode path, so a
change to onnx_backend.py is actually what gets verified.

Usage: verify_embedding_parity.py <cache-dir> <fixtures-dir>
"""
import json
import sys
from pathlib import Path

import numpy as np

MAX_ABS_DIFF = 1e-5
MIN_COSINE = 1 - 1e-6

cache_dir, fixtures = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(Path(__file__).resolve().parent))
from onnx_backend import OnnxTextEncoder  # noqa: E402  (staged flat beside this script)

meta = json.loads((fixtures / 'golden_texts.json').read_text())
expected = np.load(fixtures / 'golden_vectors.npy')

enc = OnnxTextEncoder(meta['model'], cache_folder=str(cache_dir))
actual = enc.encode(meta['texts'], normalize_embeddings=True)

if enc.pooling != meta['pooling']:
    sys.exit(f"FAIL: pooling mode {enc.pooling!r} != reference {meta['pooling']!r}")

max_diff = float(np.abs(actual - expected).max())
cos = np.sum(actual * expected, axis=1)
worst = float(cos.min())

# Ranking is the acceptance criterion — every consumer is top-k.
exp_sim, act_sim = expected @ expected.T, actual @ actual.T
for i in range(len(meta['texts'])):
    a = list(np.argsort(-exp_sim[i])[:10])
    b = list(np.argsort(-act_sim[i])[:10])
    if a != b:
        sys.exit(f"FAIL: top-10 ranking changed for {meta['texts'][i]!r}\n  ref {a}\n  got {b}")

if max_diff >= MAX_ABS_DIFF or worst <= MIN_COSINE:
    sys.exit(f"FAIL: max|diff| {max_diff:.3e} (limit {MAX_ABS_DIFF:.0e}), "
             f"worst cosine {worst:.12f} (limit {MIN_COSINE})")

print(f"embedding parity OK: max|diff| {max_diff:.3e}, worst cosine {worst:.12f}, "
      f"top-10 ranking identical across {len(meta['texts'])} queries")
