# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal, override

import discord
from discord import app_commands
from discord.ext import commands

from .utils import const, shell
from .utils.systemd import monotonic_to_unix, show_services

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)

STATE_EMOJIS = {
    "active": "🟢",
    "reloading": "🟡",
    "activating": "🟡",
    "deactivating": "🟡",
    "inactive": "⚫",
    "failed": "🔴",
}


def service_choices(*, exclude: tuple[str, ...] = ()) -> list[app_commands.Choice[str]]:
    """Build the choices of a ``service`` option: every service with its emoji, minus the excluded ones."""
    return [
        app_commands.Choice(name=f"{service.emoji} {name}", value=name)
        for name, service in const.SERVICES.items()
        if name not in exclude
    ]


def label(name: str) -> str:
    """Format a service name for a Discord message, with its emoji."""
    service = const.SERVICES.get(name)
    return f"{service.emoji} **{name}**" if service else f"**{name}**"


def codeblock(text: str, limit: int = 1800) -> str:
    """Wrap the tail of ``text`` (where commands put their errors) into a Discord code block."""
    return f"```\n{text.strip()[-limit:]}\n```"


REBOOT_REQUIRED_FILE = Path("/var/run/reboot-required")
# The timer's service that runs the VPS's own checks (disk, memory, recoveries, updates); see scripts/.
MONITORING_SERVICE = "vps-resources"
# Same thresholds as scripts/check_resources.sh, which alerts on them.
DISK_MAX_USED_PERCENT = 90
MEMORY_MIN_AVAILABLE_PERCENT = 10


def gigabytes(size: float) -> str:
    """Format a size in bytes as gigabytes with one decimal."""
    return f"{size / 1024**3:.1f} GB"


async def output_of(*args: str) -> str | None:
    """Run a program and get its output, or ``None`` when it's missing or fails."""
    try:
        returncode, output = await shell.run(*args)
    except OSError, TimeoutError:
        return None
    return output if returncode == 0 else None


def resource_lines() -> list[str]:
    """Lines of /vps about uptime, load, memory and disk, read from ``/proc`` and the file system."""
    boot = round(time.time() - float(Path("/proc/uptime").read_text().split()[0]))
    load = os.getloadavg()
    cpus = os.cpu_count() or 1
    meminfo = {
        key: int(value.split()[0]) * 1024
        for key, value in (line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    }
    total, available = meminfo["MemTotal"], meminfo["MemAvailable"]
    memory_percent = available * 100 // total
    disk = shutil.disk_usage("/")
    disk_percent = disk.used * 100 // disk.total

    # ✅ when fine, ⚠️ when it needs a look
    load_mark = "✅" if load[1] < cpus else "⚠️"
    memory_mark = "✅" if memory_percent > MEMORY_MIN_AVAILABLE_PERCENT else "⚠️"
    disk_mark = "✅" if disk_percent < DISK_MAX_USED_PERCENT else "⚠️"
    memory = f"{gigabytes(available)} of {gigabytes(total)} available ({memory_percent}%)"
    disk_used = f"{gigabytes(disk.used)} of {gigabytes(disk.total)} used ({disk_percent}%), {gigabytes(disk.free)} free"
    return [
        f"⏱️ Up since <t:{boot}:f> (<t:{boot}:R>)",
        f"{load_mark} Load {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f} on {cpus} cores",
        f"{memory_mark} Memory: {memory}",
        f"{disk_mark} Disk: {disk_used}",
    ]


def reboot_line() -> str:
    """Line of /vps about whether installed updates wait for a reboot, and since when."""
    if not REBOOT_REQUIRED_FILE.exists():
        return "✅ No reboot needed"
    since = round(REBOOT_REQUIRED_FILE.stat().st_mtime)
    return f"⚠️ Needs a reboot for installed updates since <t:{since}:R>"


async def vps_health_lines() -> list[str]:
    """Collect the lines of /vps: load, memory, disk, pending updates, failed units, databases and monitoring."""
    lines = resource_lines()

    upgradable, failed_units, clusters = await asyncio.gather(
        output_of("apt", "list", "--upgradable"),
        output_of("systemctl", "--failed", "--plain", "--no-legend"),
        output_of("pg_lsclusters", "--no-header"),
    )

    if upgradable is None:
        lines.append("❔ Updates: `apt list` failed")
    else:
        packages = [line for line in upgradable.splitlines() if "/" in line]
        security = sum("-security" in line for line in packages)
        # Security updates waiting for tonight's unattended-upgrades run are normal, so they get an info sign only.
        lines.append(
            f"{'✅' if not security else '🔹'} Updates: {security} security, {len(packages) - security} other pending"
        )
    lines.append(reboot_line())

    if failed_units is None:
        lines.append("❔ Failed units: `systemctl --failed` failed")
    else:
        units = [line.split()[0] for line in failed_units.splitlines() if line.strip()]
        lines.append(f"❌ Failed units: {', '.join(units)}" if units else "✅ No failed systemd units")

    # `pg_lsclusters` lines: version, cluster, port, status, owner, data directory, log file.
    for cluster in (clusters or "").splitlines():
        version, name, port, status, *_ = cluster.split()
        # Stopped clusters are usually old ones kept around on purpose, so they aren't a problem.
        emoji = "🐘" if status.startswith("online") else "⚫"
        lines.append(f"{emoji} PostgreSQL {version} `{name}` on port {port}: {status}")

    try:
        services = await show_services([MONITORING_SERVICE], ["Result", "ExecMainExitTimestampMonotonic"])
    except RuntimeError:
        services = {}
    props = services.get(MONITORING_SERVICE, {})
    result = props.get("Result", "?")
    ran = monotonic_to_unix(props.get("ExecMainExitTimestampMonotonic", ""))
    when = f", last run <t:{ran}:R>" if ran else ""
    lines.append(f"{'✅' if result == 'success' else '❌'} Monitoring checks: {result}{when}")
    return lines


def format_journal_entry(line: str) -> str:
    """Turn one line of ``journalctl --output=json`` into a short ``HH:MM:SS message`` line (time in UTC)."""
    entry = json.loads(line)
    timestamp = dt.datetime.fromtimestamp(int(entry["__REALTIME_TIMESTAMP"]) / 1_000_000, tz=dt.UTC)
    message = entry.get("MESSAGE")
    if isinstance(message, list):  # journald stores messages that aren't valid UTF-8 as a list of bytes
        message = bytes(message).decode(errors="replace")
    return f"{timestamp:%H:%M:%S} {message or ''}"


class Services(commands.Cog):
    """Owner-only commands to manage the bots' systemd services on the VPS from Discord, and to check the VPS's health."""

    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot

    @override
    def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return interaction.user.id == const.OWNER_ID

    @override
    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(f"{const.HU_EMOTE} Not for you.", ephemeral=True)
            return
        log.error("Unhandled error in /%s", interaction.command and interaction.command.name, exc_info=error)
        content = "Something went wrong, see logs."
        if interaction.response.is_done():
            await interaction.followup.send(content)
        else:
            await interaction.response.send_message(content)

    # --- START / STOP / RESTART ---------------------------------------------------------------------------------------

    @staticmethod
    async def systemctl(
        interaction: discord.Interaction[LalaBot],
        request: Literal["restart", "stop", "start"],
        service: str,
    ) -> None:
        """Run ``sudo systemctl <request> <service>`` and reply with how it went."""
        await interaction.response.defer()
        returncode, output = await shell.run("sudo", "systemctl", request, service)
        status = "✅" if returncode == 0 else "❌"
        message = f"{status} `sudo systemctl {request} {service}` exited with code {returncode}."
        if output.strip():
            message += f"\n{codeblock(output)}"
        await interaction.followup.send(message)

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(service=service_choices())
    async def restart(self, interaction: discord.Interaction[LalaBot], service: str) -> None:
        """🔄 Restart a bot's systemd service.

        Parameters
        ----------
        service
            Bot to restart.
        """
        if service == "lalabot":
            # The command below kills this very process, so there would be nobody left to send the result.
            await interaction.response.send_message(f"🔄 Restarting {label(service)}, bye.")
            await asyncio.create_subprocess_exec("sudo", "systemctl", "restart", service)
            return
        await self.systemctl(interaction, "restart", service)

    # LalaBot isn't among /start and /stop choices: it's already running if it gets the command,
    # and once stopped, nobody could start it back from Discord.

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(service=service_choices(exclude=("lalabot",)))
    async def start(self, interaction: discord.Interaction[LalaBot], service: str) -> None:
        """▶️ Start a bot's stopped systemd service.

        Parameters
        ----------
        service
            Bot to start.
        """
        await self.systemctl(interaction, "start", service)

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(service=service_choices(exclude=("lalabot",)))
    async def stop(self, interaction: discord.Interaction[LalaBot], service: str) -> None:
        """⏹️ Stop a bot's systemd service; it stays down until /start.

        Parameters
        ----------
        service
            Bot to stop.
        """
        await self.systemctl(interaction, "stop", service)

    # --- STATUS / JOURNAL / LOGS --------------------------------------------------------------------------------------

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction[LalaBot]) -> None:
        """📊 Show whether each bot is running, since when, and how many times systemd auto-restarted it."""
        await interaction.response.defer()
        try:
            services = await show_services(
                const.SERVICES, ["ActiveState", "SubState", "ActiveEnterTimestampMonotonic", "NRestarts"]
            )
        except RuntimeError as error:
            await interaction.followup.send(f"❌ {codeblock(str(error))}")
            return

        lines: list[str] = []
        for name, props in services.items():
            state = props.get("ActiveState", "?")
            line = f"{STATE_EMOJIS.get(state, '❔')} {label(name)} {state} ({props.get('SubState', '?')})"
            if state == "active" and (since := monotonic_to_unix(props.get("ActiveEnterTimestampMonotonic", ""))):
                line += f" since <t:{since}:R>"
            if (restarts := props.get("NRestarts", "0")) != "0":
                line += f", auto-restarted {restarts} times"
            lines.append(line)
        await interaction.followup.send("\n".join(lines))

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(service=service_choices())
    async def tail(
        self,
        interaction: discord.Interaction[LalaBot],
        service: str,
        lines: app_commands.Range[int, 1, 200] = 30,
    ) -> None:
        """📓 Show the last lines of a bot's systemd journal right in the chat.

        Parameters
        ----------
        service
            Bot to show the journal of.
        lines
            How many of the latest lines to show at most; older ones are cut to fit into one message.
        """
        await interaction.response.defer()
        returncode, output = await shell.run(
            "sudo", "journalctl", f"--unit={service}", f"--lines={lines}", "--output=json", "--output-fields=MESSAGE"
        )
        if returncode != 0:
            await interaction.followup.send(f"❌ `journalctl` exited with code {returncode}.\n{codeblock(output)}")
            return

        entries = [format_journal_entry(line) for line in output.splitlines() if line.startswith("{")]
        # Take the newest lines that fit into one message, keeping room for the header and code block fences.
        shown: list[str] = []
        budget = 1900
        for entry in reversed(entries):
            if len(entry) + 1 > budget:
                if not shown:  # the newest line alone is too long, so show its start
                    shown.append(entry[:budget])
                break
            shown.append(entry)
            budget -= len(entry) + 1
        shown.reverse()

        header = f"📓 Last {len(shown)} journal lines of {label(service)} (UTC):"
        body = "\n".join(shown).replace("```", "`\N{ZERO WIDTH SPACE}``") or "(empty)"
        # `py` highlighting mostly helps with tracebacks, which are what one usually looks for in there.
        await interaction.followup.send(f"{header}\n```py\n{body}\n```")

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(service=service_choices())
    async def journal(
        self,
        interaction: discord.Interaction[LalaBot],
        service: str,
        lines: app_commands.Range[int, 1, 20_000] = 5000,
    ) -> None:
        """📁 Get the last lines of a bot's systemd journal as a file.

        Parameters
        ----------
        service
            Bot to get the journal of.
        lines
            How many of the latest lines to get.
        """
        await interaction.response.defer()
        returncode, output = await shell.run("sudo", "journalctl", f"--unit={service}", f"--lines={lines}", "--no-pager")
        if returncode != 0:
            await interaction.followup.send(f"❌ `journalctl` exited with code {returncode}.\n{codeblock(output)}")
            return
        file = discord.File(io.BytesIO(output.encode()), filename=f"{service}-journal.log")
        await interaction.followup.send(file=file)

    # --- VPS ----------------------------------------------------------------------------------------------------------

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    async def vps(self, interaction: discord.Interaction[LalaBot]) -> None:
        """🖥️ Show the VPS's health: load, memory, disk, pending updates, failed units, databases and monitoring."""
        if sys.platform != "linux":
            await interaction.response.send_message("🏠 Running at home, there's no VPS to check.")
            return
        await interaction.response.defer()
        lines = await vps_health_lines()
        await interaction.followup.send("\n".join(["🖥️ **VPS health**", *lines]))


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Services(bot))
