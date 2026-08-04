# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared helpers for alerts-module unit tests."""


def get_query_and_params(call):
    """Extract (query_str, params_dict) from a mock call, regardless of positional/kwarg."""
    args = call.args
    kwargs = call.kwargs
    query = args[0] if args else kwargs.get("query", "")
    params = args[1] if len(args) > 1 else kwargs.get("params", {})
    return query, params
