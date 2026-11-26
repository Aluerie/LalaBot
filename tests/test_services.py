from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from ext import services
from ext.services import codeblock, format_journal_entry, pop_reboot_marker, write_reboot_marker

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_format_journal_entry() -> None:
    line = json.dumps({"__REALTIME_TIMESTAMP": "1795667597000000", "MESSAGE": "Started alubot."})
    assert format_journal_entry(line) == "04:33:17 Started alubot."


def test_format_journal_entry_with_a_non_utf8_message() -> None:
    line = json.dumps({"__REALTIME_TIMESTAMP": "1795667597000000", "MESSAGE": [104, 105, 255]})
    assert format_journal_entry(line) == "04:33:17 hi\N{REPLACEMENT CHARACTER}"


def test_codeblock_keeps_the_end() -> None:
    assert codeblock("  start middle end  ", limit=3) == "```\nend\n```"


def test_reboot_marker_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "REBOOT_MARKER", tmp_path / "lalabot" / "reboot.json")
    assert pop_reboot_marker() is None
    before = time.time()
    write_reboot_marker(123)
    marker = pop_reboot_marker()
    assert marker is not None
    assert marker[0] == 123
    assert before <= marker[1] <= time.time()
    assert pop_reboot_marker() is None  # it's gone after being read once
