from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from ext import services
from ext.services import (
    codeblock,
    fit_newest,
    format_journal_entry,
    format_version_line,
    pop_reboot_marker,
    write_reboot_marker,
)

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


def test_format_journal_entry_with_date() -> None:
    line = json.dumps({"__REALTIME_TIMESTAMP": "1795667597000000", "MESSAGE": "Started alubot."})
    assert format_journal_entry(line, with_date=True) == "26/11 04:33:17 Started alubot."


def test_fit_newest_keeps_the_newest_lines_in_order() -> None:
    assert fit_newest(["old", "middle", "new"], budget=11) == ["middle", "new"]


def test_fit_newest_cuts_a_too_long_newest_line() -> None:
    assert fit_newest(["old", "x" * 50], budget=10) == ["x" * 10]


COMMIT = "abc1234\x001795667597\x00✨ Add a thing"


def test_version_line_up_to_date() -> None:
    line = format_version_line("alubot", commit=COMMIT, behind="0", dirty=False, started=1795667600)
    assert line == "✅ 🦢 **alubot** `abc1234` ✨ Add a thing (<t:1795667597:R>)"


def test_version_line_behind_dirty_and_not_restarted() -> None:
    line = format_version_line("irebot", commit=COMMIT, behind="2", dirty=True, started=1795667000)
    assert line.startswith("⚠️ 💜 **irebot** `abc1234`")
    assert line.endswith(
        "\n-# ⚠️ 2 commit(s) behind GitHub · ✏️ has local changes · ⚠️ started before this commit, so it runs an older version"
    )


def test_version_line_without_github() -> None:
    line = format_version_line("dota2bot", commit=COMMIT, behind=None, dirty=False, started=None)
    assert line.startswith("❔ ⚔️ **dota2bot**")
    assert line.endswith("\n-# ❔ couldn't reach GitHub")


def test_version_line_without_a_checkout() -> None:
    assert format_version_line("lalabot", commit=None, behind=None, dirty=False, started=None).startswith("❔")
