from __future__ import annotations

import datetime as dt

import pytest

from ext.report import UnitSummary, count_boots, format_duration, format_summary, summarize_unit

SINCE = 1_000_000.0
NOW = SINCE + 7 * 86400


def test_no_events_and_up_means_no_downtime() -> None:
    assert summarize_unit([], since=SINCE, now=NOW, up_now=True) == UnitSummary()


def test_no_events_and_down_means_down_all_the_time() -> None:
    assert summarize_unit([], since=SINCE, now=NOW, up_now=False).downtime == NOW - SINCE


def test_restart_counts_the_gap_between_stop_and_start() -> None:
    events = [
        (SINCE + 100, "Stopping alubot..."),
        (SINCE + 101, "alubot.service: Deactivated successfully."),
        (SINCE + 130, "Started alubot."),
    ]
    summary = summarize_unit(events, since=SINCE, now=NOW, up_now=True)
    assert summary == UnitSummary(downtime=30, crashes=0, auto_restarts=0, starts=1)


def test_crash_and_auto_restart() -> None:
    events = [
        (SINCE + 100, "irebot.service: Main process exited, code=exited, status=1/FAILURE"),
        (SINCE + 100, "irebot.service: Failed with result 'exit-code'."),
        (SINCE + 130, "irebot.service: Scheduled restart job, restart counter is at 1."),
        (SINCE + 160, "Started irebot."),
    ]
    summary = summarize_unit(events, since=SINCE, now=NOW, up_now=True)
    assert summary == UnitSummary(downtime=60, crashes=1, auto_restarts=1, starts=1)


def test_starting_up_first_means_it_was_down_since_the_start() -> None:
    events = [(SINCE + 50, "Started dota2bot.")]
    assert summarize_unit(events, since=SINCE, now=NOW, up_now=True).downtime == 50


def test_still_down_counts_until_now() -> None:
    events = [(NOW - 20, "Stopping lalabot...")]
    assert summarize_unit(events, since=SINCE, now=NOW, up_now=False).downtime == 20


def test_count_boots_only_counts_boots_in_the_period() -> None:
    since = dt.datetime(2026, 11, 1, tzinfo=dt.UTC).timestamp()
    list_boots = (
        "-2 aaa Sun 2026-09-20 05:30:13 UTC—Thu 2026-11-26 01:48:06 UTC\n"
        "-1 bbb Thu 2026-11-26 01:48:17 UTC—Thu 2026-11-26 04:30:10 UTC\n"
        " 0 ccc Thu 2026-11-26 04:31:00 UTC—Thu 2026-11-26 05:00:00 UTC\n"
    )
    assert count_boots(list_boots, since=since) == 2


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0 s"), (45.4, "45 s"), (180, "3 min"), (7505, "2 h 5 min"), (97200, "1 d 3 h"), (90061, "1 d 1 h")],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_format_summary_never_rounds_up_to_100_percent() -> None:
    summary = UnitSummary(downtime=1, crashes=1, auto_restarts=2, starts=3)
    line = format_summary("alubot", summary, period=7 * 86400)
    assert line == "🦢 **alubot** 99.99% up · down 1 s · 💥 1 crash · 2 auto-restarts · 3 starts"


def test_format_summary_of_a_quiet_week() -> None:
    assert format_summary("irebot", UnitSummary(), period=7 * 86400) == "💜 **irebot** 100% up · 0 starts"
