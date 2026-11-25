from __future__ import annotations

import asyncio
from typing import cast


# `timeout` is a parameter, not left to the caller's `asyncio.timeout`: cancelling would leave the process running.
async def run(*args: str, timeout: float = 60.0) -> tuple[int, str]:  # noqa: ASYNC109
    """Run a program and return its exit code together with its combined stdout and stderr.

    The process is killed and ``TimeoutError`` is raised if it takes longer than ``timeout`` seconds.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    # `communicate()` waits for the process to exit, so the return code is set by now.
    return cast("int", process.returncode), stdout.decode(errors="replace")
