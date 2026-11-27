# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""`/jobs`: the scheduled jobs of every bot (reminders, birthdays, ...) and the ones that have problems.

The bots keep their jobs in a `scheduled_jobs` table in their own schema (`shared.scheduler`),
so the schemas are discovered instead of listed by hand.
"""

from __future__ import annotations

import collections
import datetime as dt
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Self, override

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .services import ALERTS_WEBHOOK_URL_FILE
from .utils import const
from .utils.postgres import psql
from .utils.systemd import is_systemd_service

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from main import LalaBot


log = logging.getLogger(__name__)

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
BLOCK_LIMIT = 1200
"""Characters per bot's block: a Components V2 message holds 4000 characters of text in all."""
PROBLEM_LIMIT = 300
ALERT_FROM_ATTEMPT = 2
"""A retrying job gets an alert from this many failures of its occurrence on."""
ALERT_CHECK_MINUTES = 5
MAX_ALERT_BUTTONS = 20
MAX_PROBLEM_ROWS = 8
"""Problems shown with a button; a message holds 40 components (a problem row takes 3)."""


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


RUNS_SCHEMAS_QUERY = """
select table_schema from information_schema.tables where table_name = 'scheduled_job_runs' order by 1
"""
OUTCOME_LABELS = {"done": "done", "rescheduled": "put off", "failed": "failed", "gave_up": "gave up", "skipped": "skipped"}
"""How `/jobs` names the outcomes of `scheduled_job_runs` (``stopped`` counts as done)."""


def checked_schemas(schemas: Iterable[str]) -> list[str]:
    """Return the schemas, refusing any that isn't a plain identifier: they go into queries as they are."""
    schemas = list(schemas)
    if bad := [schema for schema in schemas if not SCHEMA_NAME.fullmatch(schema)]:
        msg = f"Not plain schema names: {bad}"
        raise ValueError(msg)
    return schemas


def runs_summary_query(schemas: Iterable[str]) -> str:
    """Build the query for how the last 24 hours of runs went, per schema and outcome."""
    parts = [
        f"""
        select '{schema}', outcome, count(*) from "{schema}".scheduled_job_runs
        where finished_at > now() - interval '24 hours' group by outcome
        """  # noqa: S608 - the schema names are checked, everything else is constant
        for schema in checked_schemas(schemas)
    ]
    return " union all ".join(parts)


def gave_up_query(schemas: Iterable[str], since: int) -> str:
    """Build the query for the occurrences given up on since a unix time, with their error's last line.

    One-off jobs are deleted when they're given up on, so the history is the only place that still knows about them.
    """
    parts = [
        f"""
        select '{schema}', job_id, kind, attempt,
               regexp_replace(coalesce((regexp_match(btrim(error, E' \\n'), '[^\\n]*$'))[1], ''), '\\s+', ' ', 'g')
        from "{schema}".scheduled_job_runs
        where outcome = 'gave_up' and finished_at > to_timestamp({int(since)})
        """  # noqa: S608 - the schema names are checked, `since` is an `int`, everything else is constant
        for schema in checked_schemas(schemas)
    ]
    return " union all ".join(parts)


def runs_line(outcomes: collections.Counter[str]) -> str:
    """Describe a bot's last 24 hours of runs, e.g. "-# last 24 h: 12 done · 1 failed"; empty if there were none."""
    counts = collections.Counter({"done": outcomes["done"] + outcomes["stopped"]})
    counts.update({outcome: count for outcome, count in outcomes.items() if outcome not in {"done", "stopped"}})
    parts = [f"{count} {OUTCOME_LABELS.get(outcome, outcome)}" for outcome, count in counts.items() if count]
    return f"-# last 24 h: {' · '.join(parts)}" if parts else ""


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


@dataclass(frozen=True, slots=True)
class Problem:
    """A job that needs a look, and what can be done about it from `/jobs`.

    Attributes
    ----------
    job
        The job.
    text
        What's wrong, as one line.
    action
        The button next to it: "retry" (run the failing occurrence again now), "clear" (forget the error of an
        occurrence that was given up on), "delete" (a job nobody runs), or `None` (a bot is holding it right now).
    category
        What's wrong: "stuck", "overdue", "retrying" or "given_up".
    """

    job: JobRow
    text: str
    action: Literal["retry", "clear", "delete"] | None
    category: Literal["stuck", "overdue", "retrying", "given_up"]

    @property
    def key(self) -> tuple[str, int, str]:
        """Identify the problem, so the alerts report it once."""
        return (self.job.schema, self.job.id, self.category)

    @property
    def is_alert_worthy(self) -> bool:
        """Whether it's worth an alert: one failed try is often a blip that the retry fixes, so only from the second."""
        return self.category != "retrying" or self.job.attempts >= ALERT_FROM_ATTEMPT


def problems(jobs: Iterable[JobRow], now: int) -> list[Problem]:
    """Find the jobs that need a look: stuck, overdue, retrying or given up on.

    Parameters
    ----------
    jobs
        The jobs of all the bots.
    now
        The current unix time.
    """
    found: list[Problem] = []
    for job in jobs:
        where = f"**{job.schema}** `#{job.id}` {job.kind}"
        if job.claimed_at is not None and now - job.claimed_at > STUCK_AFTER.total_seconds():
            text = f"🧊 {where}: claimed {ago(now - job.claimed_at)} ago and still not done (a hanging handler?)"
            found.append(Problem(job, text, None, "stuck"))
        elif job.claimed_at is None and now - job.fires_at > OVERDUE_AFTER.total_seconds():
            text = f"⏳ {where}: {ago(now - job.fires_at)} overdue (the bot is down, or the handler isn't loaded?)"
            found.append(Problem(job, text, "delete", "overdue"))
        if job.attempts:
            text = f"🔁 {where}: failed {job.attempts}×, retrying <t:{job.fires_at}:R>: `{job.error}`"
            found.append(Problem(job, text, "retry" if job.claimed_at is None else None, "retrying"))
        elif job.error:
            text = f"❌ {where}: the last occurrence was given up on: `{job.error}`"
            found.append(Problem(job, text, "clear" if job.claimed_at is None else None, "given_up"))
    return found


def fit(text: str, limit: int) -> str:
    """Cut text to the limit, at a line break when there is one."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 2]
    return cut[: cut.rfind("\n")] + "\n…" if "\n" in cut else cut + "…"


def schema_block(schema: str, jobs: Sequence[JobRow], *, upcoming: int, runs: collections.Counter[str] | None = None) -> str:
    """Describe one bot's jobs: how many of each kind, the next ones, and how the last 24 hours of runs went."""
    service = SCHEMA_SERVICES.get(schema, schema)
    emoji = const.SERVICES[service].emoji if service in const.SERVICES else "🤖"
    history = runs_line(runs) if runs else ""
    if not jobs:
        return "\n".join(filter(None, [f"{emoji} **{schema}**: no jobs", history]))
    kinds = collections.Counter(job.kind for job in jobs)
    summary = ", ".join(f"{kind} {count}" for kind, count in kinds.most_common())
    lines = [f"{emoji} **{schema}**: {len(jobs)} jobs ({summary})"]
    if history:
        lines.append(history)
    for job in jobs[:upcoming]:
        repeats = " 🔁" if job.rule else ""
        lines.append(f"- `#{job.id}` {job.kind}{repeats} <t:{job.fires_at}:R> {job.label}".rstrip())
    if len(jobs) > upcoming:
        lines.append(f"-# …and {len(jobs) - upcoming} more")
    return fit("\n".join(lines), BLOCK_LIMIT)


BUTTONS: dict[str, tuple[str, str, discord.ButtonStyle]] = {
    "retry": ("Retry now", "▶️", discord.ButtonStyle.primary),
    "clear": ("Clear", "🧹", discord.ButtonStyle.secondary),
    "delete": ("Delete", "🗑️", discord.ButtonStyle.danger),
    "confirm": ("Delete for good", "🗑️", discord.ButtonStyle.danger),
}
"""Label, emoji and style of each `JobButton` action."""


def render(
    schemas: Sequence[str],
    jobs: Sequence[JobRow],
    now: int,
    *,
    upcoming: int,
    runs: dict[str, collections.Counter[str]] | None = None,
) -> discord.ui.LayoutView:
    """Build `/jobs`: per bot, how many jobs of each kind and the next ones; then the problems, each with its button.

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
    runs
        Per schema, how many runs of the last 24 hours had each outcome.
    """
    runs = runs or {}
    by_schema: dict[str, list[JobRow]] = collections.defaultdict(list)
    for job in jobs:
        by_schema[job.schema].append(job)
    trouble = problems(jobs, now)

    container = discord.ui.Container[discord.ui.LayoutView](
        accent_colour=discord.Colour.orange() if trouble else discord.Colour.green()
    )
    container.add_item(discord.ui.TextDisplay("## 🗓️ Scheduled jobs"))
    for schema in schemas:
        block = schema_block(schema, by_schema[schema], upcoming=upcoming, runs=runs.get(schema))
        container.add_item(discord.ui.TextDisplay(block))
    container.add_item(discord.ui.Separator())
    if not trouble:
        container.add_item(discord.ui.TextDisplay("✅ No problems: nothing stuck, overdue, retrying or given up on."))
    else:
        container.add_item(discord.ui.TextDisplay(f"### ⚠️ Problems ({len(trouble)})"))
        for problem in trouble[:MAX_PROBLEM_ROWS]:
            text = fit(problem.text, PROBLEM_LIMIT)
            if problem.action is None:
                container.add_item(discord.ui.TextDisplay(text))
            else:
                button = JobButton(problem.action, problem.job.schema, problem.job.id)
                # a `DynamicItem` works as an accessory, it's only typed for both view kinds instead of `LayoutView`
                container.add_item(discord.ui.Section(text, accessory=button))  # ty: ignore[invalid-argument-type]
        if len(trouble) > MAX_PROBLEM_ROWS:
            container.add_item(discord.ui.TextDisplay(f"-# …and {len(trouble) - MAX_PROBLEM_ROWS} more problems"))
    container.add_item(discord.ui.TextDisplay(f"-# Checked <t:{now}:R>"))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


def action_query(action: str, schema: str, job_id: int) -> str:
    """Build the query of a `JobButton` action. It only touches the job while no bot holds it (``claimed_at``).

    A bot keeps the jobs it claimed in memory until they're done, so changing them under its feet could make it
    run them twice; unclaimed jobs live only in the table.
    """
    if not SCHEMA_NAME.fullmatch(schema):
        msg = f"Not a plain schema name: {schema!r}"
        raise ValueError(msg)
    table = f'"{schema}".scheduled_jobs'
    where = f"where id = {int(job_id)} and claimed_at is null"
    # S608: the schema name is checked above and the ID is an `int`, everything else is constant
    return {
        # the bot claims it on its next poll, within 30 seconds
        "retry": f"update {table} set retry_at = now() {where} and attempts > 0 returning id",  # noqa: S608
        "clear": f"update {table} set last_error = null {where} and attempts = 0 returning id",  # noqa: S608
        "confirm": f"delete from {table} {where} returning kind",  # noqa: S608
    }[action]


class JobButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"jobs:(?P<action>retry|clear|delete|confirm):(?P<schema>[a-z_][a-z0-9_]*):(?P<job_id>\d+)",
):
    """The buttons next to `/jobs` problems: ▶️ Retry now, 🧹 Clear, 🗑️ Delete (and its confirmation).

    The job is in the custom ID, so the buttons keep working after LalaBot restarts.
    """

    def __init__(self, action: str, schema: str, job_id: int, *, label: str | None = None) -> None:
        default_label, emoji, style = BUTTONS[action]
        button = discord.ui.Button[discord.ui.View](
            label=label or default_label, emoji=emoji, style=style, custom_id=f"jobs:{action}:{schema}:{job_id}"
        )
        super().__init__(button)
        self.action: str = action
        self.schema: str = schema
        self.job_id: int = job_id

    @classmethod
    @override
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> Self:
        return cls(match["action"], match["schema"], int(match["job_id"]))

    @override
    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id != const.OWNER_ID:
            await interaction.response.send_message(f"{const.HU_EMOTE} Not for you.", ephemeral=True)
            return False
        return True

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        where = f"**{self.schema}** `#{self.job_id}`"
        if self.action == "delete":
            view = discord.ui.View(timeout=None)
            view.add_item(JobButton("confirm", self.schema, self.job_id))
            await interaction.response.send_message(
                f"🗑️ Delete {where} for good? Its bot won't run it anymore.", view=view, ephemeral=True
            )
            return

        try:
            rows = await psql(action_query(self.action, self.schema, self.job_id))
        except (OSError, TimeoutError, RuntimeError) as error:
            await interaction.response.send_message(f"❌ Couldn't change the job: {error}"[:2000], ephemeral=True)
            return
        if not rows:
            content = f"🤷 Nothing to do for {where}: it's gone, fixed, or a bot is holding it right now."
        else:
            content = {
                "retry": f"▶️ {where} runs again on its bot's next poll, within 30 seconds.",
                "clear": f"🧹 Cleared the error of {where}.",
                "confirm": f"🗑️ Deleted {where} ({rows[0][0]}).",
            }[self.action]
        await interaction.response.send_message(content, ephemeral=True)


async def fetch_jobs() -> tuple[list[str], list[JobRow], dict[str, collections.Counter[str]]]:
    """Query every bot's jobs and how the last 24 hours of their runs went.

    Returns
    -------
    tuple[list[str], list[JobRow], dict[str, collections.Counter[str]]]
        The schemas with a `scheduled_jobs` table, their jobs, and per schema the count of each run outcome.
    """
    schemas = [row[0] for row in await psql(SCHEMAS_QUERY) if SCHEMA_NAME.fullmatch(row[0])]
    if not schemas:
        return [], [], {}
    jobs = [JobRow.from_fields(row) for row in await psql(jobs_query(schemas))]
    runs: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    if run_schemas := [row[0] for row in await psql(RUNS_SCHEMAS_QUERY) if SCHEMA_NAME.fullmatch(row[0])]:
        for schema, outcome, count in await psql(runs_summary_query(run_schemas)):
            runs[schema][outcome] += int(count)
    return schemas, jobs, runs


class Jobs(commands.Cog):
    """`/jobs`: the bots' scheduled jobs; and alerts about new problems with them, in prod."""

    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot
        self.alerted: set[tuple[str, int, str]] = set()
        """Problems already reported, so each is reported once (while it lasts; again if it comes back)."""
        self.checked_at: int = int(time.time())
        """Up to when the history was checked for given-up occurrences."""

    @override
    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(JobButton)
        if is_systemd_service():
            self.alert_about_problems.start()
        else:
            log.info("Not running as a systemd service, so not alerting about job problems.")

    @override
    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(JobButton)
        self.alert_about_problems.cancel()

    # --- ALERTS -------------------------------------------------------------------------------------------------------

    @tasks.loop(minutes=ALERT_CHECK_MINUTES)
    async def alert_about_problems(self) -> None:
        # An exception would stop the loop for good, so log it and try again next time instead.
        try:
            await self.check_for_problems()
        except Exception:
            log.exception("Checking the scheduled jobs for problems failed")

    @alert_about_problems.before_loop
    async def before_alert_about_problems(self) -> None:
        await self.bot.wait_until_ready()

    async def check_for_problems(self) -> None:
        """Post the new problems of the bots' jobs to the alerts channel, with the buttons to deal with them."""
        now = int(time.time())
        schemas, jobs, _ = await fetch_jobs()
        current = [problem for problem in problems(jobs, now) if problem.is_alert_worthy]
        new = [problem for problem in current if problem.key not in self.alerted]
        # forget the solved ones, so a problem that comes back is reported again
        self.alerted = {problem.key for problem in current}

        lines = [f"- {problem.text}" for problem in new]
        existing = {(job.schema, job.id) for job in jobs}
        if run_schemas := [row[0] for row in await psql(RUNS_SCHEMAS_QUERY) if SCHEMA_NAME.fullmatch(row[0])]:
            for schema, job_id, kind, attempt, error in await psql(gave_up_query(run_schemas, self.checked_at)):
                if (schema, int(job_id)) not in existing:  # repeating ones are among the problems above already
                    lines.append(f"- ❌ **{schema}** `#{job_id}` {kind}: given up on after {attempt} tries: `{error}`")
        self.checked_at = now
        if not lines or not schemas:
            return

        view = discord.ui.View(timeout=None)
        actionable = [(problem, problem.action) for problem in new if problem.action is not None]
        for problem, action in actionable[:MAX_ALERT_BUTTONS]:
            view.add_item(JobButton(action, problem.job.schema, problem.job.id, label=f"#{problem.job.id}"))
        content = fit("🗓️ **Scheduled job problems** (more in /jobs)\n" + "\n".join(lines), 2000)
        await self.send_alert(content, view)

    async def send_alert(self, content: str, view: discord.ui.View) -> None:
        """Post to the alerts channel through the alerts' webhook (LalaBot's own, so the buttons work)."""
        url = ALERTS_WEBHOOK_URL_FILE.read_text(encoding="utf-8").strip()
        webhook = discord.Webhook.from_url(url, client=self.bot)
        mentions = discord.AllowedMentions.none()
        try:
            await webhook.send(content, view=view, allowed_mentions=mentions, wait=True)
        except ValueError, discord.HTTPException:
            # e.g. a webhook of no application (made by hand) can't have buttons: the text is what matters
            log.warning("Could not send the job alert with buttons, sending it without", exc_info=True)
            await webhook.send(content, allowed_mentions=mentions, wait=True)

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(upcoming="How many of the next jobs to list per bot (5 by default).")
    async def jobs(self, interaction: discord.Interaction[LalaBot], upcoming: app_commands.Range[int, 0, 10] = 5) -> None:
        """🗓️ Show the bots' scheduled jobs (reminders, birthdays...) and the ones with problems."""
        if interaction.user.id != const.OWNER_ID:
            await interaction.response.send_message(f"{const.HU_EMOTE} Not for you.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            schemas, jobs, runs = await fetch_jobs()
        except (OSError, TimeoutError, RuntimeError) as error:
            await interaction.followup.send(f"❌ Couldn't query the database: {error}"[:2000])
            return

        if not schemas:
            await interaction.followup.send("🗓️ No bot has a `scheduled_jobs` table yet.")
            return
        view = render(schemas, jobs, int(time.time()), upcoming=upcoming, runs=runs)
        # the mentions only name the jobs' users, nobody should be pinged
        await interaction.followup.send(view=view, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Jobs(bot))
