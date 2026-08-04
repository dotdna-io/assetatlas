# SPDX-License-Identifier: AGPL-3.0-or-later
"""Process-timezone helpers for tests that must prove clock-correctness.

``datetime.fromtimestamp(x)`` with no ``tz`` reads the *process* timezone, and
there is no injection point to mock. The only way to prove such a conversion is
offset-independent is therefore to run it under more than one timezone, which
means mutating ``os.environ['TZ']`` + ``time.tzset()``.

``local_timezone()`` always restores both, including the common case where ``TZ``
was never set: a leaked ``TZ`` silently changes every sibling test's idea of
local time, which is far worse than no test at all.

POSIX only — ``time.tzset()`` does not exist on Windows. The repo targets Linux
containers and macOS dev boxes.

Usage::

    with local_timezone(WEST_OF_UTC_TZ):
        result = service.decide()
"""

import os
import time
from contextlib import contextmanager

# The control: no offset, which is what every container runs today.
UTC_TZ = 'UTC'

# Two zones on opposite sides of UTC, both far enough out that an offset-sensitive
# comparison flips well outside any test tolerance. Kolkata's half-hour offset also
# catches code that assumes offsets are whole hours.
WEST_OF_UTC_TZ = 'America/Los_Angeles'  # UTC-8, UTC-7 under DST
EAST_OF_UTC_TZ = 'Asia/Kolkata'         # UTC+5:30, no DST


@contextmanager
def local_timezone(name):
    """Run the block with the process timezone set to ``name``, then restore it.

    Args:
        name: Anything libc accepts in ``TZ`` -- an Olson name ('UTC',
            'America/Los_Angeles') or a POSIX offset string ('XXX+8').
    """
    previous = os.environ.get('TZ')
    try:
        # Inside the try, not before it: anything that raises between mutating TZ
        # and yielding must still hit the restore, or the new zone leaks into every
        # later test in the process.
        os.environ['TZ'] = name
        time.tzset()

        # libc silently falls back to UTC for a zone it cannot load (no tzdata in
        # the image, typically). Left unchecked, every parity test would still pass
        # — vacuously — and quietly stop being load-bearing.
        if name != UTC_TZ and (time.timezone, time.altzone) == (0, 0):
            raise RuntimeError(
                f'TZ={name} resolved to UTC (offset 0), so nothing here can prove '
                f'anything about timezone handling. Is tzdata installed?'
            )
        yield
    finally:
        if previous is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = previous
        time.tzset()
