# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

import asyncio
import os
from typing import cast

# Variables systemd gives this service for talking back to it; they're meant for the bot's own process only.
# A child that inherits them may notify systemd too, which logs "reception only permitted for main PID".
SYSTEMD_NOTIFY_VARIABLES = ("NOTIFY_SOCKET", "WATCHDOG_USEC", "WATCHDOG_PID")


def child_env() -> dict[str, str]:
    """Get this process's environment for a child process, minus the systemd notify variables."""
    return {key: value for key, value in os.environ.items() if key not in SYSTEMD_NOTIFY_VARIABLES}


# `timeout` is a parameter, not left to the caller's `asyncio.timeout`: cancelling would leave the process running.
async def run(*args: str, timeout: float = 60.0) -> tuple[int, str]:  # noqa: ASYNC109
    """Run a program and return its exit code together with its combined stdout and stderr.

    The process is killed and ``TimeoutError`` is raised if it takes longer than ``timeout`` seconds.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=child_env(),
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    # `communicate()` waits for the process to exit, so the return code is set by now.
    return cast("int", process.returncode), stdout.decode(errors="replace")
