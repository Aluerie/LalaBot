from __future__ import annotations

import asyncio
import json

import pytest

from ext.jobs import JobRow, action_query, ago, fit, jobs_query, problems, render

NOW = 1_800_000_000


def job(**changes: object) -> JobRow:
    fields: dict[str, object] = {
        "schema": "alubot",
        "id": 1,
        "kind": "reminder",
        "key": "",
        "user_id": "123",
        "fires_at": NOW + 3600,
        "rule": "",
        "attempts": 0,
        "claimed_at": None,
        "error": "",
        "text": "",
    }
    fields.update(changes)
    return JobRow(**fields)


def test_row_from_psql_fields() -> None:
    row = JobRow.from_fields(["alubot", "7", "birthday", "birthday:1", "1", "1799712000", "", "0", "", "", ""])
    assert row.id == 7
    assert row.fires_at == 1799712000
    assert row.claimed_at is None
    assert row.label == "<@1>"  # AluBot's users are Discord users


def test_label() -> None:
    assert job(key="k", text="t").label == "<@123> “t”"
    assert job(schema="irenesbot", key="k").label == "`k`"  # Twitch users can't be mentioned
    assert job(schema="irenesbot", text="water the plants").label == "“water the plants”"
    assert job(schema="irenesbot", text="x" * 100).label.endswith("…”")
    assert job(user_id="").label == ""


def test_query_covers_every_schema() -> None:
    query = jobs_query(["alubot", "irenesbot"])
    assert '"alubot".scheduled_jobs' in query
    assert '"irenesbot".scheduled_jobs' in query
    assert query.count("union all") == 1


def test_query_refuses_odd_schema_names() -> None:
    with pytest.raises(ValueError, match="plain schema names"):
        jobs_query(['alubot"; drop table x; --'])


def test_ago() -> None:
    assert ago(12 * 60) == "12 min"
    assert ago(3 * 3600) == "3 h"
    assert ago(3 * 86400) == "3 d"


def test_healthy_jobs_have_no_problems() -> None:
    # due soon, and one claimed a moment ago: both normal
    assert problems([job(), job(id=2, fires_at=NOW + 30, claimed_at=NOW - 30)], NOW) == []


def test_problems() -> None:
    found = problems(
        [
            job(id=1, fires_at=NOW - 3600),  # overdue, unclaimed
            job(id=2, fires_at=NOW - 3600, claimed_at=NOW - 3600),  # claimed long ago
            job(id=3, attempts=2, error="RuntimeError: boom"),  # retrying
            job(id=4, rule="FREQ=DAILY", error="KeyError: 'x'"),  # given up on, moved on
            job(id=5, attempts=1, error="E", claimed_at=NOW - 30),  # retrying, but a bot holds it right now
        ],
        NOW,
    )
    overdue, stuck, retrying, given_up, held = found
    assert overdue.text.startswith("⏳ **alubot** `#1`")
    assert "60 min overdue" in overdue.text
    assert overdue.action == "delete"
    assert stuck.text.startswith("🧊 **alubot** `#2`")
    assert stuck.action is None  # a bot holds it: hands off
    assert retrying.text.startswith("🔁 **alubot** `#3`")
    assert "failed 2×" in retrying.text
    assert "RuntimeError: boom" in retrying.text
    assert retrying.action == "retry"
    assert given_up.text.startswith("❌ **alubot** `#4`")
    assert given_up.action == "clear"
    assert held.action is None


def render_json(*args: object, **kwargs: object) -> str:
    async def build() -> str:  # a view needs a running event loop
        return json.dumps(render(*args, **kwargs).to_components(), ensure_ascii=False)  # ty: ignore[invalid-argument-type]

    return asyncio.run(build())


def test_render() -> None:
    jobs = [job(id=i, fires_at=NOW + i, kind="birthday" if i % 2 else "reminder") for i in range(1, 8)]
    text = render_json(["alubot", "irenesbot"], jobs, NOW, upcoming=3)
    assert "**alubot**: 7 jobs (birthday 4, reminder 3)" in text
    assert "…and 4 more" in text
    assert "**irenesbot**: no jobs" in text
    assert "✅ No problems" in text
    assert text.count("- `#") == 3


def test_render_puts_buttons_next_to_problems() -> None:
    text = render_json(["alubot"], [job(id=9, attempts=1, error="E")], NOW, upcoming=5)
    assert "⚠️ Problems (1)" in text
    assert '"custom_id": "jobs:retry:alubot:9"' in text


def test_action_queries_only_touch_unclaimed_jobs() -> None:
    for action in ("retry", "clear", "confirm"):
        query = action_query(action, "alubot", 9)
        assert '"alubot".scheduled_jobs' in query
        assert "id = 9 and claimed_at is null" in query
    with pytest.raises(ValueError, match="plain schema name"):
        action_query("retry", 'x"; drop table y; --', 9)


def test_fit() -> None:
    assert fit("short", 100) == "short"
    assert fit("line one\nline two\nline three", 20) == "line one\nline two\n…"
    assert fit("x" * 50, 10) == "x" * 8 + "…"
