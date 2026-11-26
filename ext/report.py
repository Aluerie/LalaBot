# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .utils import const, shell
from .utils.forum import get_thread
from .utils.systemd import is_systemd_service, show_services

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)

# `journalctl --list-boots` lines look like `-1 4df67… Sun 2026-09-20 05:30:13 UTC—Thu 2026-11-26 01:48:06 UTC`;
# the first time is when that boot started. The VPS runs in UTC.
BOOT_START = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC")


@dataclass(slots=True)
class UnitSummary:
    """What happened to a systemd service over the report's period."""

    downtime: float = 0.0
    """Seconds the service wasn't up (not "Started", i.e. not ready, for notify services)."""
    crashes: int = 0
    """Times it failed: crashed, got killed by the watchdog, or didn't start in time."""
    auto_restarts: int = 0
    """Times systemd restarted it by itself after a failure."""
    starts: int = 0
    """Times it got up: after deploys, reboots, /restart and auto-restarts."""


def is_up_event(message: str) -> bool:
    """Whether a systemd message about a service means it's up now (for notify services: it reported READY=1)."""
    return message.startswith("Started ")


def is_down_event(message: str) -> bool:
    """Whether a systemd message about a service means it's going or gone down."""
    return message.startswith("Stopping ") or ": Main process exited" in message or ": Failed with result" in message


def summarize_unit(events: list[tuple[float, str]], *, since: float, now: float, up_now: bool) -> UnitSummary:
    """Sum up systemd's own messages about a service, as ``(unix time, message)`` in time order.

    The state at ``since`` is the opposite of the first up/down event; without any, the service has been
    in its current state (``up_now``) the whole time.
    """
    summary = UnitSummary()
    first_up = next((is_up_event(message) for _, message in events if is_up_event(message) or is_down_event(message)), None)
    was_up = up_now if first_up is None else not first_up
    down_since = None if was_up else since
    for timestamp, message in events:
        if ": Failed with result" in message:
            summary.crashes += 1
        if ": Scheduled restart job" in message:
            summary.auto_restarts += 1
        if is_up_event(message):
            summary.starts += 1
            if down_since is not None:
                summary.downtime += timestamp - down_since
                down_since = None
        elif is_down_event(message) and down_since is None:
            down_since = timestamp
    if down_since is not None:
        summary.downtime += now - down_since
    return summary


def count_boots(list_boots: str, *, since: float) -> int:
    """Count the boots in ``journalctl --list-boots`` output that started at or after ``since``."""
    count = 0
    for line in list_boots.splitlines():
        if match := BOOT_START.search(line):
            started = dt.datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.UTC).timestamp()
            count += started >= since
    return count


def format_duration(seconds: float) -> str:
    """Format a duration roughly, with its two biggest units: ``45 s``, ``3 min``, ``2 h 5 min``, ``1 d 3 h``."""
    seconds = round(seconds)
    parts: list[str] = []
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60), ("s", 1)):
        if seconds >= size or (not parts and unit == "s"):
            parts.append(f"{seconds // size} {unit}")
            seconds %= size
        if len(parts) == 2:  # noqa: PLR2004
            break
    return " ".join(parts)


def plural(count: int, word: str, words: str) -> str:
    """``1 crash``, ``2 crashes``: the count with the singular or plural form of the word."""
    return f"{count} {word if count == 1 else words}"


def format_summary(name: str, summary: UnitSummary, *, period: float) -> str:
    """One line of the report: a service's uptime and what happened to it."""
    # Rounded down, so a little downtime never shows as 100%.
    percent = math.floor(10000 * (1 - summary.downtime / period)) / 100
    parts = [f"{percent:g}% up"]
    if summary.downtime >= 1:
        parts.append(f"down {format_duration(summary.downtime)}")
    if summary.crashes:
        parts.append(f"💥 {plural(summary.crashes, 'crash', 'crashes')}")
    if summary.auto_restarts:
        parts.append(plural(summary.auto_restarts, "auto-restart", "auto-restarts"))
    parts.append(plural(summary.starts, "start", "starts"))
    emoji = const.SERVICES[name].emoji if name in const.SERVICES else "❔"
    return f"{emoji} **{name}** " + " · ".join(parts)


def journal_message(entry: dict[str, object]) -> str:
    """The ``MESSAGE`` of a ``journalctl --output=json`` entry, which journald stores as bytes when it isn't UTF-8."""
    message = entry.get("MESSAGE")
    if isinstance(message, list):
        return bytes(message).decode(errors="replace")
    return str(message or "")


async def build_report(days: int) -> str:
    """Build the report of the last ``days`` days: every bot's uptime, crashes and restarts, and the VPS's reboots."""
    now = time.time()
    since = now - days * 86400
    returncode, output = await shell.run(
        "journalctl",
        "_PID=1",  # only systemd's own messages about the services, not what the bots log
        *(f"UNIT={name}.service" for name in const.SERVICES),
        f"--since=@{int(since)}",
        "--output=json",
        "--output-fields=MESSAGE,UNIT",
        "--no-pager",
    )
    if returncode != 0:
        msg = f"journalctl exited with code {returncode}: {output.strip()[-500:]}"
        raise RuntimeError(msg)
    events: defaultdict[str, list[tuple[float, str]]] = defaultdict(list)
    for line in output.splitlines():
        if line.startswith("{"):
            entry = json.loads(line)
            unit = str(entry.get("UNIT", "")).removesuffix(".service")
            events[unit].append((int(entry["__REALTIME_TIMESTAMP"]) / 1_000_000, journal_message(entry)))

    states = await show_services(const.SERVICES, ["ActiveState"])
    _, boots = await shell.run("journalctl", "--list-boots", "--no-pager")

    lines = [f"📈 **Report for the last {days} days** (<t:{int(since)}:d> – <t:{int(now)}:d>)"]
    for name in const.SERVICES:
        up_now = states.get(name, {}).get("ActiveState") == "active"
        summary = summarize_unit(events[name], since=since, now=now, up_now=up_now)
        lines.append(format_summary(name, summary, period=now - since))
    lines.append(f"🔁 VPS reboots: {count_boots(boots, since=since)}")
    return "\n".join(lines)


class Report(commands.Cog):
    """Posts a weekly report of the bots' uptime, crashes and restarts to its post in the bot-stuff forum.

    The weekly post only happens as a systemd service and once `WEEKLY_REPORT_THREAD_ID` is set;
    /report works anytime.
    """

    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot

    @override
    async def cog_load(self) -> None:
        if not is_systemd_service():
            return
        if const.WEEKLY_REPORT_THREAD_ID is None:
            log.warning("WEEKLY_REPORT_THREAD_ID is not set in const.py, so there's no weekly report, only /report.")
            return
        self.weekly_report.start()

    @override
    async def cog_unload(self) -> None:
        self.weekly_report.cancel()

    # Runs daily, but only posts on Mondays: `tasks.loop` has no weekly schedule.
    @tasks.loop(time=dt.time(hour=9, tzinfo=dt.UTC))
    async def weekly_report(self) -> None:
        if dt.datetime.now(dt.UTC).weekday() != 0 or const.WEEKLY_REPORT_THREAD_ID is None:
            return
        # An exception would stop the loop for good, so log it and try again next week instead.
        try:
            thread = await get_thread(self.bot, const.WEEKLY_REPORT_THREAD_ID)
            await thread.send(await build_report(7))
        except Exception:
            log.exception("Weekly report failed")

    @weekly_report.before_loop
    async def before_weekly_report(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    async def report(self, interaction: discord.Interaction[LalaBot], days: app_commands.Range[int, 1, 60] = 7) -> None:
        """📈 Show every bot's uptime, crashes and restarts, and the VPS's reboots, over the last days.

        Parameters
        ----------
        days
            How many of the last days to cover. The journal goes back about two months.
        """
        if interaction.user.id != const.OWNER_ID:
            await interaction.response.send_message(f"{const.HU_EMOTE} Not for you.", ephemeral=True)
            return
        if sys.platform != "linux":
            await interaction.response.send_message("🏠 Running at home, there's no journal of the bots to report on.")
            return
        await interaction.response.defer()
        try:
            report = await build_report(days)
        except (OSError, TimeoutError, RuntimeError) as error:
            await interaction.followup.send(f"❌ Couldn't build the report: {error}"[:2000])
            return
        await interaction.followup.send(report)


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Report(bot))
