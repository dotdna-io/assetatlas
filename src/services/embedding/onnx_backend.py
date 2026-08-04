# SPDX-License-Identifier: AGPL-3.0-or-later
"""ONNX Runtime backend for the embedding model.

Replaces torch + sentence-transformers for inference. Same weights, same maths,
different kernel: `onnx/model.onnx` is the fp32 export of the safetensors this
previously loaded, so vectors are equivalent to float32 rounding noise
(measured worst-case cosine 0.99999976 over 2000 real strings, with identical
top-10 ordering on every query — see tests/unit/services/embedding/
test_onnx_parity.py and issue #1070).

Why: torch (CPU) + sentence-transformers cost 282 MB compressed, and onnxruntime
+ tokenizers were ALREADY installed as chromadb dependencies, so the swap removes
that much from the shared base image at zero added cost. api, scheduler and mcp
are one image under three names, so it lands on all of them at once, and on the
offline bundle. (teams-bridge no longer shares that image — it builds slim from
python:3.13-slim and never had the embedding stack to save on, #1069.)

Deliberately NOT a general sentence-transformers reimplementation. It reproduces
exactly the three-stage pipeline this model declares in its modules.json —
Transformer -> Pooling -> Normalize — and nothing else.
"""

import json
import logging
from pathlib import Path
from typing import List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import onnxruntime as ort
    from tokenizers import Tokenizer
except ImportError:  # pragma: no cover - exercised only on an incomplete install
    ort = None
    Tokenizer = None


def _snapshot_dir(model_name: str, cache_folder: str) -> Path:
    """Resolve a HuggingFace cache snapshot directory.

    Layout is <cache>/models--<org>--<name>/snapshots/<commit>/. There is
    normally exactly one snapshot; if a cache ever holds several, pick the
    newest so behaviour is deterministic rather than filesystem-order dependent.
    """
    repo_dir = Path(cache_folder) / f"models--{model_name.replace('/', '--')}"
    snapshots = sorted(
        (p for p in (repo_dir / 'snapshots').glob('*') if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
    )
    if not snapshots:
        raise FileNotFoundError(f"No model snapshot under {repo_dir}")
    return snapshots[-1]


def _pooling_mode(snapshot: Path) -> str:
    """Read the pooling mode the model declares, rather than assuming.

    BGE uses CLS pooling. Hardcoding that would silently produce wrong vectors if
    the model were ever swapped for a mean-pooling one, and wrong-but-plausible
    embeddings are exactly the failure this backend must not introduce.
    """
    cfg_path = snapshot / '1_Pooling' / 'config.json'
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"{cfg_path} missing — cannot determine pooling mode; refusing to guess."
        )
    cfg = json.loads(cfg_path.read_text())
    if cfg.get('pooling_mode_cls_token'):
        return 'cls'
    if cfg.get('pooling_mode_mean_tokens'):
        return 'mean'
    raise ValueError(
        f"Unsupported pooling configuration in {cfg_path}: {cfg}. "
        "Only CLS and mean pooling are implemented."
    )


class OnnxTextEncoder:
    """Minimal drop-in for the one SentenceTransformer method this codebase uses.

    Exposes `encode()` with the same keyword arguments EmbeddingService already
    passes, so no call site changes.
    """

    def __init__(self, model_name: str, cache_folder: str, max_seq_length: int = 512):
        if ort is None or Tokenizer is None:
            raise ImportError("onnxruntime and tokenizers are required for the ONNX backend")

        snapshot = _snapshot_dir(model_name, cache_folder)
        onnx_path = snapshot / 'onnx' / 'model.onnx'
        if not onnx_path.exists():
            raise FileNotFoundError(
                f"{onnx_path} missing. Dockerfile.base must ship the ONNX export; "
                "see issue #1070."
            )

        self.pooling = _pooling_mode(snapshot)
        self._max_seq_length = max_seq_length

        self._tokenizer = Tokenizer.from_file(str(snapshot / 'tokenizer.json'))
        pad_id = self._tokenizer.token_to_id('[PAD]')
        if pad_id is None:
            pad_id = 0
        self._tokenizer.enable_truncation(max_length=max_seq_length)
        self._tokenizer.enable_padding(pad_id=pad_id, pad_token='[PAD]')

        # Left on onnxruntime's own thread heuristics: the pipeline already runs
        # batched, and this matches what sentence-transformers did before.
        self._session = ort.InferenceSession(
            str(onnx_path), providers=['CPUExecutionProvider'],
        )
        self._input_names = [i.name for i in self._session.get_inputs()]
        # Select the output by NAME for the same reason inputs are: exports
        # differ, and some emit a pooler_output alongside the token-level states.
        # Taking index 0 blindly would pool a (batch, dim) tensor as if it were
        # (batch, seq, dim) and produce silent garbage rather than an error.
        output_names = [o.name for o in self._session.get_outputs()]
        self._output_name = (
            'last_hidden_state' if 'last_hidden_state' in output_names else output_names[0]
        )
        logger.info(
            "ONNX embedding backend ready (%s pooling, inputs=%s, output=%s)",
            self.pooling, self._input_names, self._output_name,
        )

    def _feed(self, batch: List[str]) -> Tuple[dict, np.ndarray]:
        """Return (session feed, attention mask).

        The mask comes back separately because pooling always needs it, while the
        feed carries only the inputs this particular export declares — reading
        the mask back out of the feed would KeyError on an export that folds
        attention_mask into the graph, which is the very case the name-filtering
        below exists to tolerate.
        """
        encoded = self._tokenizer.encode_batch(batch)
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        types = np.array([e.type_ids for e in encoded], dtype=np.int64)
        # Match by name: exports differ in input order and in whether they take
        # token_type_ids at all.
        available = {'input_ids': ids, 'attention_mask': mask, 'token_type_ids': types}
        feed = {name: available[name] for name in self._input_names if name in available}
        return feed, mask

    def _pool(self, hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
        if hidden.ndim != 3:
            raise ValueError(
                f"output {self._output_name!r} has shape {hidden.shape}; expected "
                "token-level hidden states (batch, sequence, dim). Pooling a "
                "pre-pooled tensor would silently produce wrong vectors."
            )
        if self.pooling == 'cls':
            # .copy() is load-bearing: hidden[:, 0] is a VIEW, so retaining it
            # pins the whole (batch, sequence, dim) activation array alive for
            # every batch until encode() returns — measured 11x retention on
            # short strings, and it scales with sequence length.
            return hidden[:, 0].copy()
        # Mean pooling must ignore padding, or short texts in a padded batch get
        # their embedding diluted by [PAD] positions.
        m = mask[..., None].astype(hidden.dtype)
        return (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)

    def encode(
        self,
        sentences: List[str],
        batch_size: int = 32,
        show_progress_bar: bool = False,   # accepted for signature parity; unused
        normalize_embeddings: bool = True,
        convert_to_numpy: bool = True,     # accepted for signature parity; always numpy
    ) -> np.ndarray:
        if isinstance(sentences, str):
            sentences = [sentences]
        if not sentences:
            return np.array([])

        out = []
        for start in range(0, len(sentences), batch_size):
            batch = sentences[start:start + batch_size]
            feed, mask = self._feed(batch)
            hidden = self._session.run([self._output_name], feed)[0]
            pooled = self._pool(hidden, mask)
            out.append(pooled)

        embeddings = np.vstack(out).astype(np.float32)
        if normalize_embeddings:
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings = embeddings / np.clip(norms, 1e-12, None)
        return embeddings

    def eval(self):
        """Set-evaluation-mode no-op — NOT the `eval()` builtin.

        Named to match torch's `Module.eval()`, which EmbeddingService._load_model
        calls on the model it loads. ONNX graphs are already inference-only, so
        there is nothing to switch off. Executes nothing.
        """
        return self

    @property
    def max_seq_length(self) -> int:
        """The truncation limit actually in force, matching SentenceTransformer's
        attribute of the same name. Previously hardcoded to None, which would have
        raised a TypeError in any arithmetic the moment something read it."""
        return self._max_seq_length
