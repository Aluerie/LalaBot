from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
import time
from typing import TYPE_CHECKING

from . import shell

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)


async def show_services(services: Iterable[str], properties: Iterable[str]) -> dict[str, dict[str, str]]:
    """Get the given properties of systemd services via ``systemctl show``, keyed by the service name.

    Raises ``RuntimeError`` when ``systemctl`` fails.
    """
    returncode, output = await shell.run("systemctl", "show", *services, f"--property=Id,{','.join(properties)}")
    if returncode != 0:
        msg = f"systemctl show exited with code {returncode}: {output.strip()}"
        raise RuntimeError(msg)
    result: dict[str, dict[str, str]] = {}
    # `systemctl show` prints one block of `Key=Value` lines per unit, separated by blank lines.
    for block in output.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        result[props.pop("Id", "?").removesuffix(".service")] = props
    return result


def monotonic_to_unix(usec: str) -> int | None:
    """Convert a systemd ``*TimestampMonotonic`` property (microseconds since boot) to a Unix timestamp.

    Returns ``None`` for ``0``, which systemd uses for "never". The monotonic clock is the same one Python's
    ``time.monotonic()`` reads on Linux, so this needs no timezone or date parsing.
    """
    if not usec.isdigit() or int(usec) == 0:
        return None
    return round(time.time() - time.monotonic() + int(usec) / 1_000_000)


def is_systemd_service() -> bool:
    """Whether this process runs as a systemd service, i.e. in prod on the VPS rather than at home.

    systemd sets ``INVOCATION_ID`` in the environment of every service it starts.
    """
    return "INVOCATION_ID" in os.environ


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
