from __future__ import annotations

import gzip
from typing import TYPE_CHECKING

import pytest

from ext.backup import DUMP_COMPLETE_MARKER, MIN_DUMP_BYTES, check_dump, compress

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


def test_compress_round_trips(tmp_path: Path) -> None:
    dump = tmp_path / "alubot.sql"
    dump.write_bytes(b"CREATE TABLE things ();\n" * 1000 + DUMP_COMPLETE_MARKER + b"\n")
    archive = compress(dump, "alubot-2026-11-26.sql.gz")
    assert archive == tmp_path / "alubot-2026-11-26.sql.gz"
    assert gzip.decompress(archive.read_bytes()) == dump.read_bytes()
    assert archive.stat().st_size < dump.stat().st_size
