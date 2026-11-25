from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys

log = logging.getLogger(__name__)


def sd_notify(state: str) -> None:
    """Send a state message such as ``READY=1`` or ``WATCHDOG=1`` to systemd.

    Does nothing when the process is not started by a systemd unit with ``Type=notify`` (e.g. local development).
    """
    if sys.platform == "win32":
        return
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):  # abstract namespace socket
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(state.encode())
    except OSError:
        log.exception("Failed to notify systemd with %s", state)


async def watchdog_loop() -> None:
    """Keep pinging the systemd watchdog so systemd restarts the process if its event loop gets stuck.

    Pings at half of the unit's ``WatchdogSec=``; returns immediately when the unit has no watchdog configured.
    """
    watchdog_usec = os.environ.get("WATCHDOG_USEC")
    if not watchdog_usec:
        return
    interval = int(watchdog_usec) / 1_000_000 / 2
    while True:
        sd_notify("WATCHDOG=1")
        await asyncio.sleep(interval)
