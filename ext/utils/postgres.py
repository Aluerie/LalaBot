# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

from . import const, shell


async def psql(query: str) -> list[list[str]]:
    """Run a query on the bots' database with ``psql`` and return its rows as lists of strings.

    Values are separated by tabs, so queries must keep tabs and newlines out of the values they return.
    Raises ``RuntimeError`` when ``psql`` fails.
    """
    returncode, output = await shell.run(
        "psql",
        "--no-psqlrc",
        "--tuples-only",
        "--no-align",
        "--field-separator=\t",
        f"--dbname={const.DATABASE_NAME}",
        f"--command={query}",
    )
    if returncode != 0:
        msg = f"psql exited with code {returncode}: {output.strip()}"
        raise RuntimeError(msg)
    return [line.split("\t") for line in output.splitlines() if line]
