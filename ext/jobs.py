# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""`/jobs`: the scheduled jobs of every bot (reminders, birthdays, ...) and the ones that have problems.

The bots keep their jobs in a `scheduled_jobs` table in their own schema (`shared.scheduler`),
so the schemas are discovered instead of listed by hand.
"""

from __future__ import annotations

import collections
import datetime as dt
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

import discord
from discord import app_commands
from discord.ext import commands

from .utils import const
from .utils.postgres import psql

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from main import LalaBot


SCHEMAS_QUERY = """
select table_schema from information_schema.tables where table_name = 'scheduled_jobs' order by 1
"""
SCHEMA_NAME = re.compile(r"[a-z_][a-z0-9_]*")
"""Schema names go into the query as identifiers, so only plain ones are accepted."""
SCHEMA_SERVICES = {"alubot": "alubot", "irenesbot": "irebot", "dota2bot": "dota2bot", "lalabot": "lalabot"}
"""Bot schemas by the service whose emoji they get."""
DISCORD_SCHEMAS = frozenset({"alubot"})
"""Schemas whose `user_id`s are Discord users, so they can be shown as mentions."""

OVERDUE_AFTER = dt.timedelta(minutes=5)
"""A job this late that nobody claimed means its bot is down or doesn't have the handler loaded."""
STUCK_AFTER = dt.timedelta(minutes=10)
"""A job claimed for this long means its handler hangs (claims last seconds to a minute before the job fires)."""
TEXT_WIDTH = 60
MINUTES_SHOWN_UP_TO = 120
HOURS_SHOWN_UP_TO = 48
MESSAGE_LIMIT = 2000


def jobs_query(schemas: Iterable[str]) -> str:
    """Build the query for the jobs of all the schemas, the ones that fire first first.

    Tabs and newlines are squeezed out of the text values, because ``psql`` output is split on them.
    ``last_error`` is a traceback: only its last line (the exception) is kept.

    Raises ``ValueError`` for schema names that aren't plain identifiers, since they go into the query as they are.
    """
    if bad := [schema for schema in schemas if not SCHEMA_NAME.fullmatch(schema)]:
        msg = f"Not plain schema names: {bad}"
        raise ValueError(msg)
    parts = [
        f"""
        select '{schema}', id, kind, coalesce(key, ''), coalesce(user_id::text, ''),
               extract(epoch from coalesce(retry_at, local_due at time zone timezone))::bigint,
               coalesce(rule, ''), attempts, coalesce(extract(epoch from claimed_at)::bigint::text, ''),
               regexp_replace(coalesce((regexp_match(btrim(last_error, E' \\n'), '[^\\n]*$'))[1], ''), '\\s+', ' ', 'g'),
               regexp_replace(left(coalesce(data ->> 'text', ''), 200), '\\s+', ' ', 'g')
        from "{schema}".scheduled_jobs
        """  # noqa: S608 - the schema names are checked above, everything else is constant
        for schema in schemas
    ]
    return " union all ".join(parts) + " order by 6, 2"


@dataclass(frozen=True, slots=True)
class JobRow:
    """A job as `/jobs` sees it.

    Attributes
    ----------
    schema
        The bot's schema.
    id
        The job's ID.
    kind
        The job's kind, e.g. "reminder".
    key
        The job's key, empty if it has none.
    user_id
        The job's user, empty if it has none.
    fires_at
        Unix time the job fires at: its retry if it's retrying, its due time otherwise.
    rule
        The job's repeat rule, empty if it doesn't repeat.
    attempts
        Failed attempts of the current occurrence.
    claimed_at
        Unix time the job was claimed at, `None` if it isn't.
    error
        The last line of its last error, empty if none.
    text
        The ``text`` in its data (reminders), empty if none.
    """

    schema: str
    id: int
    kind: str
    key: str
    user_id: str
    fires_at: int
    rule: str
    attempts: int
    claimed_at: int | None
    error: str
    text: str

    @classmethod
    def from_fields(cls, fields: Sequence[str]) -> Self:
        """Make a row from the values ``psql`` printed for it."""
        schema, job_id, kind, key, user_id, fires_at, rule, attempts, claimed_at, error, text = fields
        return cls(
            schema=schema,
            id=int(job_id),
            kind=kind,
            key=key,
            user_id=user_id,
            fires_at=int(fires_at),
            rule=rule,
            attempts=int(attempts),
            claimed_at=int(claimed_at) if claimed_at else None,
            error=error,
            text=text,
        )

    @property
    def label(self) -> str:
        """What the job is about: whose it is (a mention for Discord bots, the key otherwise) and a piece of its text."""
        parts: list[str] = []
        if self.user_id and self.schema in DISCORD_SCHEMAS:
            parts.append(f"<@{self.user_id}>")
        elif self.key:
            parts.append(f"`{self.key}`")
        if self.text:
            text = self.text if len(self.text) <= TEXT_WIDTH else self.text[: TEXT_WIDTH - 1] + "…"
            parts.append(f"“{text}”")
        return " ".join(parts)


def ago(seconds: float) -> str:
    """Say how long ago something was, roughly: "12 min", "3 h", "2 d"."""
    minutes = int(seconds // 60)
    if minutes < MINUTES_SHOWN_UP_TO:
        return f"{minutes} min"
    if minutes < HOURS_SHOWN_UP_TO * 60:
        return f"{minutes // 60} h"
    return f"{minutes // (24 * 60)} d"


def problems(jobs: Iterable[JobRow], now: int) -> list[str]:
    """Describe the jobs that need a look: stuck, overdue, retrying or given up on.

    Parameters
    ----------
    jobs
        The jobs of all the bots.
    now
        The current unix time.
    """
    lines: list[str] = []
    for job in jobs:
        where = f"**{job.schema}** `#{job.id}` {job.kind}"
        if job.claimed_at is not None and now - job.claimed_at > STUCK_AFTER.total_seconds():
            lines.append(f"- 🧊 {where}: claimed {ago(now - job.claimed_at)} ago and still not done (a hanging handler?)")
        elif job.claimed_at is None and now - job.fires_at > OVERDUE_AFTER.total_seconds():
            lines.append(f"- ⏳ {where}: {ago(now - job.fires_at)} overdue (the bot is down, or the handler isn't loaded?)")
        if job.attempts:
            lines.append(f"- 🔁 {where}: failed {job.attempts}×, retrying <t:{job.fires_at}:R>: `{job.error}`")
        elif job.error:
            lines.append(f"- ❌ {where}: the last occurrence was given up on: `{job.error}`")
    return lines


def render(schemas: Sequence[str], jobs: Sequence[JobRow], now: int, *, upcoming: int) -> list[str]:
    """Build the lines of `/jobs`: per bot, how many jobs of each kind and the next ones; then the problems.

    Parameters
    ----------
    schemas
        The bot schemas that have a `scheduled_jobs` table.
    jobs
        Their jobs, the ones that fire first first.
    now
        The current unix time.
    upcoming
        How many of the next jobs to list per bot.
    """
    lines = ["🗓️ **Scheduled jobs**"]
    by_schema: dict[str, list[JobRow]] = collections.defaultdict(list)
    for job in jobs:
        by_schema[job.schema].append(job)

    for schema in schemas:
        service = SCHEMA_SERVICES.get(schema, schema)
        emoji = const.SERVICES[service].emoji if service in const.SERVICES else "🤖"
        mine = by_schema[schema]
        if not mine:
            lines.append(f"{emoji} **{schema}**: no jobs")
            continue
        kinds = collections.Counter(job.kind for job in mine)
        summary = ", ".join(f"{kind} {count}" for kind, count in kinds.most_common())
        lines.append(f"{emoji} **{schema}**: {len(mine)} jobs ({summary})")
        for job in mine[:upcoming]:
            repeats = " 🔁" if job.rule else ""
            lines.append(f"- `#{job.id}` {job.kind}{repeats} <t:{job.fires_at}:R> {job.label}".rstrip())
        if len(mine) > upcoming:
            lines.append(f"-# …and {len(mine) - upcoming} more")

    trouble = problems(jobs, now)
    lines.append("⚠️ **Problems**" if trouble else "✅ No problems: nothing stuck, overdue, retrying or given up on.")
    lines.extend(trouble)
    return lines


def chunk(lines: Iterable[str], limit: int = MESSAGE_LIMIT) -> list[str]:
    """Join lines into as few messages as fit Discord's length limit; a too long line is cut."""
    messages: list[str] = []
    current = ""
    for line in lines:
        line = line if len(line) <= limit else line[: limit - 1] + "…"  # noqa: PLW2901
        if current and len(current) + 1 + len(line) > limit:
            messages.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        messages.append(current)
    return messages


class Jobs(commands.Cog):
    """`/jobs`: the bots' scheduled jobs."""

    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(upcoming="How many of the next jobs to list per bot (5 by default).")
    async def jobs(self, interaction: discord.Interaction[LalaBot], upcoming: app_commands.Range[int, 0, 25] = 5) -> None:
        """🗓️ Show the bots' scheduled jobs (reminders, birthdays...) and the ones with problems."""
        if interaction.user.id != const.OWNER_ID:
            await interaction.response.send_message(f"{const.HU_EMOTE} Not for you.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            schemas = [row[0] for row in await psql(SCHEMAS_QUERY) if SCHEMA_NAME.fullmatch(row[0])]
            rows = await psql(jobs_query(schemas)) if schemas else []
        except (OSError, TimeoutError, RuntimeError) as error:
            await interaction.followup.send(f"❌ Couldn't query the database: {error}"[:MESSAGE_LIMIT])
            return

        if not schemas:
            await interaction.followup.send("🗓️ No bot has a `scheduled_jobs` table yet.")
            return
        jobs = [JobRow.from_fields(row) for row in rows]
        for message in chunk(render(schemas, jobs, int(time.time()), upcoming=upcoming)):
            # the mentions only name the jobs' users, nobody should be pinged
            await interaction.followup.send(message, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Jobs(bot))
