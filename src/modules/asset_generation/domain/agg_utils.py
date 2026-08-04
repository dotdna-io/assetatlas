# SPDX-License-Identifier: AGPL-3.0-or-later
"""Utilities for working with post-aggregation DataFrame values.

After the aggregation stage, most string columns become lists of unique values
(e.g. ['Dell Inc.'] instead of 'Dell Inc.'). Functions here extract scalar
strings from these list-or-scalar values for downstream consumers that expect
plain strings.
"""

import numpy as np
import pandas as pd

_SKIP_VALUES = frozenset(('', 'none', 'null', 'unknown', 'n/a'))


def extract_scalar(val) -> str:
    """Extract first useful string from a post-aggregation value (list, ndarray, or scalar).

    Skips None, NaN, empty strings, and common placeholder values.
    Always returns a string (empty string on miss).
    """
    if isinstance(val, (list, np.ndarray)):
        for v in val:
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                s = str(v).strip()
                if s.lower() not in _SKIP_VALUES:
                    return s
        return ''
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return ''
    if pd.isna(val):
        return ''
    s = str(val).strip()
    return s if s.lower() not in _SKIP_VALUES else ''
