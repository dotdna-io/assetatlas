# SPDX-License-Identifier: AGPL-3.0-or-later
"""Golden-question end-to-end test.

For each entry in ``fixtures/golden_questions.yaml`` we:

1. Seed the DB (idempotent, see ``seeders/golden_seed.sql``).
2. Build a ChatService bound to a ``RecordingLLMClient`` in replay mode and
   mount it on the live Flask app.
3. Create a fresh chat session via the REST API.
4. POST the question to ``/api/chat/stream`` and parse the SSE frames back
   into (tool_calls in order, final assistant text).
5. Assert against the YAML contract: required tools + their arg contracts
   + substrings in the final answer.

Running the test suite normally uses replay mode (``CHAT_LLM_REPLAY=1``,
the default). To re-record: ``CHAT_LLM_REPLAY=0 CHAT_LLM_API_KEY=<real>
pytest tests/integration/chat/test_golden_questions.py -m integration -s``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = Path(__file__).parent / "fixtures"
RECORDINGS_DIR = FIXTURES_DIR / "recordings"
SEEDER_SQL = Path(__file__).parent / "seeders" / "golden_seed.sql"
QUESTIONS_YAML = FIXTURES_DIR / "golden_questions.yaml"

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _load_questions() -> List[Dict[str, Any]]:
    if not QUESTIONS_YAML.exists():
        return []
    with QUESTIONS_YAML.open() as f:
        return list(yaml.safe_load(f) or [])


@pytest.fixture(scope="session")
def seeded_db(live_db):
    """Apply the golden seed once per session. Idempotent — the SQL starts
    with DELETE of rows owned by the sentinel.

    The seed is multi-statement (BEGIN/INSERT/COMMIT) so we use the raw
    connection cursor instead of execute_query (which is single-statement
    and fetches rows). BEGIN/COMMIT are stripped because the connection
    manager already wraps in a transaction.
    """
    sql = SEEDER_SQL.read_text()
    sql = sql.replace("BEGIN;", "").replace("COMMIT;", "")
    with live_db.conn_manager.get_cursor() as (cur, conn):
        cur.execute(sql)
        conn.commit()
    return live_db


@pytest.fixture(scope="session")
def chat_enabled_app(live_app, live_db):
    """Force-init a ChatService on the session-scoped live_app.

    ``TestConfig`` disables chat by default, so we assemble the dependencies
    here and mount the blueprint if it isn't already. The LLM is a recording
    client bound *per test*, so this fixture just installs a placeholder.
    """
    from src.api.blueprints.chat.rate_limit import NullRateLimiter
    from src.api.blueprints.chat.routes import chat_bp
    from src.api.blueprints.chat.service import ChatService
    from src.api.blueprints.chat.tool_bindings import build_registry

    live_app.config["CHAT_ENABLED"] = True
    live_app.config.setdefault("CHAT_MAX_MESSAGES_PER_USER_PER_DAY", 1000)
    live_app.config.setdefault("CHAT_MAX_TOOL_ITERATIONS", 10)
    live_app.config.setdefault("CHAT_MAX_OUTPUT_TOKENS", 2000)
    live_app.config.setdefault("CHAT_TURN_TIMEOUT_SECONDS", 60)

    registry = build_registry(
        db=live_db,
        suggestion_service=getattr(live_app, "suggestion_service", None),
        field_resolver=getattr(live_app, "field_resolver", None),
        dashboard_service=getattr(live_app, "dashboard_service", None),
        pipeline_service=getattr(live_app, "pipeline_service", None),
        scheduler_service=getattr(live_app, "scheduler_service", None),
        integration_service=getattr(live_app, "integration_service", None),
        alerts_service=getattr(live_app, "alerts_service", None),
        settings_manager=getattr(live_app, "settings_manager", None),
    )

    class _PlaceholderLLM:
        provider = "placeholder"
        model = "none"

        async def stream(self, **kwargs: Any):
            raise RuntimeError("Each test must install its own recorded LLM.")

    live_app.chat_service = ChatService(
        db=live_db, llm=_PlaceholderLLM(), registry=registry,
        max_tool_iterations=live_app.config["CHAT_MAX_TOOL_ITERATIONS"],
        max_output_tokens=live_app.config["CHAT_MAX_OUTPUT_TOKENS"],
        turn_timeout_seconds=live_app.config["CHAT_TURN_TIMEOUT_SECONDS"],
    )
    live_app.chat_rate_limiter = NullRateLimiter(
        per_day=live_app.config["CHAT_MAX_MESSAGES_PER_USER_PER_DAY"],
    )

    # Flask raises if the same blueprint is registered twice — skip when the
    # previous test already mounted it.
    if "chat" not in live_app.blueprints:
        live_app.register_blueprint(chat_bp)

    yield live_app

    # Purge chat sessions this test run created (cascade drops messages,
    # tool_calls, feedback). Without this, repeated runs leak rows into the
    # shared integration DB indefinitely.
    live_db.execute_query(
        "DELETE FROM chat_sessions WHERE title LIKE 'golden/%%'",
        {}, fetch=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_sse_frames(raw: bytes) -> List[Tuple[str, Dict[str, Any]]]:
    """Break an SSE body into ``(event, data_dict)`` pairs.

    Fails loud on malformed JSON so a silent regression in the route's
    payload shape can't hide behind a dict wrapper.
    """
    out: List[Tuple[str, Dict[str, Any]]] = []
    for block in raw.decode("utf-8", errors="replace").split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data_parts: List[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_parts.append(line[len("data:"):].strip())
        data_text = "".join(data_parts) or "{}"
        out.append((event, json.loads(data_text)))
    return out


def _match_arg(pattern: Any, actual: Any) -> bool:
    """Contract matcher for one field in args_contract.

    Supports:
      * literal equality: ``"value"`` → args[field] == "value"
      * regex: ``{regex: "..."}`` → ``re.search`` on ``str(actual)``
      * substring: ``{contains: "..."}`` → case-insensitive substring
    Unknown pattern shapes fail closed (return False) so a typo in YAML is
    loud rather than silently passing.
    """
    if isinstance(pattern, dict):
        if "regex" in pattern:
            return re.search(pattern["regex"], str(actual)) is not None
        if "contains" in pattern:
            return str(pattern["contains"]).lower() in str(actual).lower()
        return False
    return pattern == actual


def _match_tool_call(expected: Dict[str, Any],
                     actual: Dict[str, Any]) -> bool:
    if expected.get("name") != actual.get("name"):
        return False
    contract = expected.get("args_contract") or {}
    args = actual.get("arguments") or {}
    for field, pattern in contract.items():
        if field not in args:
            return False
        if not _match_arg(pattern, args[field]):
            return False
    return True


def _collect_turn(client, session_id: str, headers: Dict[str, str],
                  message: str) -> Tuple[str, List[Tuple[str, Dict[str, Any]]]]:
    """POST /api/chat/stream and return (final_text, all_events).

    SSE tool.end frames announce name+id+status but not arguments, so the
    caller reads ``ChatRepository.load_tool_calls_by_message`` separately
    to grab full argument dicts for contract matching.
    """
    resp = client.post(
        "/api/chat/stream",
        headers={**headers, "Accept": "text/event-stream"},
        json={"session_id": session_id, "message": message},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    events = _parse_sse_frames(resp.get_data())

    final_text = "".join(e[1].get("text", "") for e in events
                         if e[0] == "delta")
    return final_text, events


def _tool_calls_for_session(repo, session_id: str) -> List[Dict[str, Any]]:
    """Flatten the per-message tool_call rows for this session into a single
    insertion-ordered list of ``{name, arguments, status}`` dicts."""
    by_message = repo.load_tool_calls_by_message(session_id)
    out: List[Dict[str, Any]] = []
    for rows in by_message.values():
        for r in rows:
            out.append({
                "name": r.tool_name,
                "arguments": r.arguments or {},
                "status": r.status,
            })
    return out


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

_QUESTIONS = _load_questions()


@pytest.mark.parametrize(
    "entry", _QUESTIONS,
    ids=[q.get("id", f"q{i}") for i, q in enumerate(_QUESTIONS)],
)
def test_golden_question(entry, seeded_db, chat_enabled_app,
                         live_client, live_admin_headers):
    from .recording_llm import RecordingError, RecordingLLMClient

    scenario_id = entry["id"]
    recording = RECORDINGS_DIR / f"{scenario_id}.json"

    if os.getenv("CHAT_LLM_REPLAY", "1") == "1" and not recording.exists():
        pytest.skip(
            f"No recording at {recording}. Re-record with "
            "CHAT_LLM_REPLAY=0 + a real CHAT_LLM_API_KEY."
        )

    wrapped: Optional[Any] = None
    if os.getenv("CHAT_LLM_REPLAY") == "0":
        from src.api.blueprints.chat.llm import make_llm_client
        wrapped = make_llm_client(chat_enabled_app.config)

    recorder = RecordingLLMClient(
        scenario=scenario_id, fixtures_dir=RECORDINGS_DIR,
        wrapped=wrapped,
    )

    # Swap the LLM on the already-mounted chat_service instead of rebuilding
    # it so the Flask blueprint registration from chat_enabled_app sticks.
    chat_enabled_app.chat_service.llm = recorder

    # Start a fresh session per question so history/turn state is isolated.
    resp = live_client.post(
        "/api/chat/sessions",
        headers=live_admin_headers,
        json={"title": f"golden/{scenario_id}"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    session_id = (body.get("data") or body).get("session_id") or body["session_id"]

    try:
        final_text, events = _collect_turn(
            live_client, session_id, live_admin_headers,
            entry["question"],
        )
    except RecordingError as e:
        pytest.fail(f"Recording mismatch for {scenario_id}: {e}")

    error_frames = [e for kind, e in events if kind == "error"]
    assert not error_frames, f"Stream emitted error(s): {error_frames}"

    actual_calls = _tool_calls_for_session(
        chat_enabled_app.chat_service.repo, session_id,
    )

    # Match expected tool calls in the order they're listed in the YAML.
    # A call is "matched" when name + arg contract line up; extras and
    # reordered intermediate calls are tolerated so long as every expected
    # entry has a matching actual somewhere in sequence.
    expected = entry.get("expected_tools") or []
    cursor = 0
    for exp in expected:
        hit = False
        while cursor < len(actual_calls):
            if _match_tool_call(exp, actual_calls[cursor]):
                cursor += 1
                hit = True
                break
            cursor += 1
        assert hit, (
            f"Expected tool call not observed: {exp}. "
            f"Actual sequence: {[c['name'] for c in actual_calls]}"
        )

    for needle in entry.get("expected_answer_contains", []) or []:
        assert needle in final_text, (
            f"Expected substring {needle!r} missing from final answer.\n"
            f"Final text: {final_text!r}"
        )


# Sanity: guard against an empty YAML silently passing the file.
def test_question_set_is_non_empty():
    assert _QUESTIONS, "golden_questions.yaml is empty"
