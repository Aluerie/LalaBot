from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ext.backup import DUMP_COMPLETE_MARKER, MIN_DUMP_BYTES, check_dump

if TYPE_CHECKING:
    from pathlib import Path


def test_complete_dump_passes(tmp_path: Path) -> None:
    dump = tmp_path / "dump.sql"
    dump.write_bytes(b"x" * MIN_DUMP_BYTES + b"\n" + DUMP_COMPLETE_MARKER + b"\n\n")
    check_dump(dump)


def test_empty_dump_fails(tmp_path: Path) -> None:
    dump = tmp_path / "dump.sql"
    dump.write_bytes(b"")
    with pytest.raises(RuntimeError, match="only 0 bytes"):
        check_dump(dump)


def test_cut_off_dump_fails(tmp_path: Path) -> None:
    dump = tmp_path / "dump.sql"
    dump.write_bytes(b"x" * (MIN_DUMP_BYTES * 2))
    with pytest.raises(RuntimeError, match="cut off"):
        check_dump(dump)
