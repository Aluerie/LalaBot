from __future__ import annotations

import time

from ext.utils.systemd import monotonic_to_unix


def test_zero_means_never() -> None:
    assert monotonic_to_unix("0") is None


def test_not_a_number() -> None:
    assert monotonic_to_unix("") is None


def test_now() -> None:
    now_usec = str(int(time.monotonic() * 1_000_000))
    unix = monotonic_to_unix(now_usec)
    assert unix is not None
    assert abs(unix - time.time()) <= 1
