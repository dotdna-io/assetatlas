# SPDX-License-Identifier: AGPL-3.0-or-later
"""P5 (#735): when a turn exhausts its tool budget, the apology must tell the
user any partial results already shown are incomplete."""
from src.api.blueprints.chat import service


def test_apology_disclaims_partial_results():
    msg = service._SUMMARY_FALLBACK_APOLOGY.format(cause="tool_iterations_exceeded")
    low = msg.lower()
    assert "incomplete" in low or "disregard" in low or "not the final" in low
    assert "tool_iterations_exceeded" in msg
