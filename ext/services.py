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
from typing import TYPE_CHECKING, Any, Literal, Self, override

import discord
from discord import app_commands
from discord.ext import commands

from .utils import const, shell
from .utils.systemd import monotonic_to_unix, show_services

if TYPE_CHECKING:
    import re

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


def format_journal_entry(line: str, *, with_date: bool = False) -> str:
    """Turn one line of ``journalctl --output=json`` into a short ``HH:MM:SS message`` line (time in UTC).

    With ``with_date``, the line starts with the day too: ``DD/MM HH:MM:SS message``.
    """
    entry = json.loads(line)
    timestamp = dt.datetime.fromtimestamp(int(entry["__REALTIME_TIMESTAMP"]) / 1_000_000, tz=dt.UTC)
    message = entry.get("MESSAGE")
    if isinstance(message, list):  # journald stores messages that aren't valid UTF-8 as a list of bytes
        message = bytes(message).decode(errors="replace")
    time_format = "%d/%m %H:%M:%S" if with_date else "%H:%M:%S"
    return f"{timestamp:{time_format}} {message or ''}"


def fit_newest(entries: list[str], budget: int = 1850) -> list[str]:
    """Take the newest lines (the end of ``entries``) that fit into ``budget`` characters together, in order.

    When even the newest line alone is too long, its start is taken.
    """
    shown: list[str] = []
    for entry in reversed(entries):
        if len(entry) + 1 > budget:
            if not shown:
                shown.append(entry[:budget])
            break
        shown.append(entry)
        budget -= len(entry) + 1
    shown.reverse()
    return shown


def journal_message(header: str, lines: list[str]) -> str:
    """A message with a header and journal lines in a code block."""
    body = "\n".join(lines).replace("```", "`\N{ZERO WIDTH SPACE}``") or "(empty)"
    # `py` highlighting mostly helps with tracebacks, which are what one usually looks for in there.
    return f"{header}\n```py\n{body}\n```"


def format_version_line(
    name: str,
    *,
    commit: str | None,
    behind: str | None,
    dirty: bool,
    started: int | None,
) -> str:
    """One line of /versions: the commit a bot's checkout is at, and whether that's what GitHub and the bot have.

    ``commit`` is ``git log -1 --format=%h%x00%ct%x00%s`` output, ``behind`` the count of commits on GitHub's ``main``
    missing from the checkout (``None`` when GitHub couldn't be reached), ``started`` when the bot last started.
    """
    if commit is None:
        return f"❔ {label(name)}: couldn't read its git checkout"
    short_hash, committed, subject = commit.strip().split("\0", 2)
    notes: list[str] = []
    if behind is None:
        notes.append("❔ couldn't reach GitHub")
    elif int(behind):
        notes.append(f"⚠️ {behind} commit(s) behind GitHub")
    if dirty:
        notes.append("✏️ has local changes")
    if started is not None and started < int(committed):
        notes.append("⚠️ started before this commit, so it runs an older version")
    mark = "✅" if not notes else "⚠️" if any(note.startswith("⚠️") for note in notes) else "❔"
    line = f"{mark} {label(name)} `{short_hash}` {subject[:70]} (<t:{committed}:R>)"
    if notes:
        line += "\n-# " + " · ".join(notes)
    return line


# Written right before /reboot reboots the VPS, so LalaBot knows after the boot where to say that it's back.
REBOOT_MARKER = Path.home() / ".cache" / "lalabot" / "reboot.json"


def write_reboot_marker(channel_id: int) -> None:
    """Remember the channel /reboot was used in and the time, for the "back up" message after the boot."""
    REBOOT_MARKER.parent.mkdir(parents=True, exist_ok=True)
    REBOOT_MARKER.write_text(json.dumps({"channel_id": channel_id, "time": time.time()}))


def pop_reboot_marker() -> tuple[int, float] | None:
    """Read and delete the marker of a /reboot: the channel ID and the time of the reboot, or ``None``."""
    try:
        data = json.loads(REBOOT_MARKER.read_text())
    except FileNotFoundError:
        return None
    finally:
        REBOOT_MARKER.unlink(missing_ok=True)
    return data["channel_id"], data["time"]


class ConfirmReboot(discord.ui.View):
    """Reboot / Cancel buttons under /reboot's question, for the owner only."""

    def __init__(self) -> None:
        super().__init__(timeout=60)
        self.message: discord.InteractionMessage | None = None

    @override
    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return interaction.user.id == const.OWNER_ID

    @override
    async def on_timeout(self) -> None:
        if self.message is not None:
            await self.message.edit(content="⌛ Not rebooting: no answer within a minute.", view=None)

    @discord.ui.button(label="Reboot", emoji="🔁", style=discord.ButtonStyle.danger)
    async def reboot(self, interaction: discord.Interaction, _button: discord.ui.Button[ConfirmReboot]) -> None:
        self.stop()
        await interaction.response.edit_message(content="🔁 Rebooting the VPS, back in a minute or two.", view=None)
        if interaction.channel_id is not None:
            write_reboot_marker(interaction.channel_id)
        # `systemctl reboot` only queues the reboot and returns; systemd then stops every service, LalaBot included.
        returncode, output = await shell.run("sudo", "systemctl", "reboot")
        if returncode != 0:
            pop_reboot_marker()
            await interaction.followup.send(
                f"❌ `sudo systemctl reboot` exited with code {returncode}.\n{codeblock(output)}"
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button[ConfirmReboot]) -> None:
        self.stop()
        await interaction.response.edit_message(content="👌 Not rebooting.", view=None)


# How long to wait for `systemctl start/restart/stop`: AluBot may take up to 5 minutes to get ready
# (`TimeoutStartSec=300`), and an interaction's reply can be edited for 15 minutes.
SYSTEMCTL_TIMEOUT = 600


async def send_systemctl(
    interaction: discord.Interaction,
    request: Literal["restart", "stop", "start"],
    service: str,
) -> None:
    """Run ``sudo systemctl <request> <service>``, saying so right away, then edit the reply into how it went.

    ``systemctl start``/``restart`` only return once the service is ready, which takes a bot a minute or more.
    """
    command = f"`sudo systemctl {request} {service}`"
    await interaction.response.send_message(f"⏳ {command}…")
    started = time.monotonic()
    try:
        returncode, output = await shell.run("sudo", "systemctl", request, service, timeout=SYSTEMCTL_TIMEOUT)
    except TimeoutError:
        # Killing `systemctl` doesn't cancel the job: systemd carries on with it.
        minutes = SYSTEMCTL_TIMEOUT // 60
        await interaction.edit_original_response(content=f"⌛ {command} is still going after {minutes} min, see /status.")
        return
    status = "✅" if returncode == 0 else "❌"
    message = f"{status} {command} exited with code {returncode} after {round(time.monotonic() - started)} s."
    if output.strip():
        message += f"\n{codeblock(output)}"
    await interaction.edit_original_response(content=message)


async def send_restart(interaction: discord.Interaction, service: str) -> None:
    """Restart a service and reply with how it went; LalaBot itself only says bye, since it gets stopped."""
    if service == "lalabot":
        # The command below kills this very process, so there would be nobody left to send the result.
        await interaction.response.send_message(f"🔄 Restarting {label(service)}, bye.")
        await asyncio.create_subprocess_exec("sudo", "systemctl", "restart", service)
        return
    await send_systemctl(interaction, "restart", service)


async def send_tail(interaction: discord.Interaction, service: str, lines: int) -> None:
    """Reply with the last ``lines`` lines of a service's journal that fit into one message."""
    await interaction.response.defer()
    # No sudo needed: the `ubuntu` user is in the `adm` group, which may read the whole system journal.
    returncode, output = await shell.run(
        "journalctl", f"--unit={service}", f"--lines={lines}", "--output=json", "--output-fields=MESSAGE"
    )
    if returncode != 0:
        await interaction.followup.send(f"❌ `journalctl` exited with code {returncode}.\n{codeblock(output)}")
        return
    shown = fit_newest([format_journal_entry(line) for line in output.splitlines() if line.startswith("{")])
    header = f"📓 Last {len(shown)} journal lines of {label(service)} (UTC):"
    await interaction.followup.send(journal_message(header, shown))


async def send_journal(interaction: discord.Interaction, service: str, lines: int) -> None:
    """Reply with the last ``lines`` lines of a service's journal as a file."""
    await interaction.response.defer()
    returncode, output = await shell.run("journalctl", f"--unit={service}", f"--lines={lines}", "--no-pager")
    if returncode != 0:
        await interaction.followup.send(f"❌ `journalctl` exited with code {returncode}.\n{codeblock(output)}")
        return
    file = discord.File(io.BytesIO(output.encode()), filename=f"{service}-journal.log")
    await interaction.followup.send(file=file)


def status_line(name: str, props: dict[str, str]) -> str:
    """Describe a bot's service for /status: its state, since when, and how often systemd auto-restarted it."""
    state = props.get("ActiveState", "?")
    line = f"{STATE_EMOJIS.get(state, '❔')} {label(name)} {state} ({props.get('SubState', '?')})"
    if state == "active" and (since := monotonic_to_unix(props.get("ActiveEnterTimestampMonotonic", ""))):
        line += f" since <t:{since}:R>"
    if (restarts := props.get("NRestarts", "0")) != "0":
        line += f", auto-restarted {restarts} times"
    return line


def status_view(services: dict[str, dict[str, str]], now: int) -> discord.ui.LayoutView:
    """Build /status: a row per bot with a 🔄 Restart button next to it, green when all are up, red otherwise.

    The buttons are the crash alerts' `AlertButton`s, so they work the same way and keep working after restarts.
    """
    all_up = all(props.get("ActiveState") == "active" for props in services.values())
    container = discord.ui.Container[discord.ui.LayoutView](
        accent_colour=discord.Colour.green() if all_up else discord.Colour.red()
    )
    container.add_item(discord.ui.TextDisplay("## 📊 Bots" + ("" if all_up else " · something is down")))
    for name, props in services.items():
        button = AlertButton("restart", name, label="Restart", emoji="🔄")
        # a `DynamicItem` works as an accessory, it's only typed for both view kinds instead of `LayoutView`
        container.add_item(discord.ui.Section(status_line(name, props), accessory=button))  # ty: ignore[invalid-argument-type]
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(f"-# Checked <t:{now}:R> · /vps for the server, /jobs for scheduled jobs"))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(container)
    return view


class AlertButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"alert:(?P<action>restart|tail|journal):(?P<service>[a-z0-9_-]+)",
):
    """🔄 Restart, 📓 Tail and 📁 Journal buttons under the crash alerts that scripts/notify_failure.sh posts.

    The alerts come from a shell script through LalaBot's webhook, so no view of LalaBot's sent these buttons.
    discord.py matches clicks by their custom ID against the template instead, which keeps old alerts' buttons
    working across restarts. Only works with a webhook LalaBot created itself (/alertwebhook): Discord sends
    the clicks to the webhook's application, and doesn't allow such buttons on webhooks of no application.
    """

    def __init__(self, action: str, service: str, *, label: str | None = None, emoji: str | None = None) -> None:
        button = discord.ui.Button[discord.ui.View](
            label=label or action, emoji=emoji, custom_id=f"alert:{action}:{service}"
        )
        super().__init__(button)
        self.action: str = action
        self.service: str = service

    @classmethod
    @override
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> Self:
        return cls(match["action"], match["service"])

    @override
    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id != const.OWNER_ID:
            await interaction.response.send_message(f"{const.HU_EMOTE} Not for you.", ephemeral=True)
            return False
        if self.service not in const.SERVICES:
            await interaction.response.send_message(f"❌ Unknown service `{self.service}`.", ephemeral=True)
            return False
        return True

    @override
    async def callback(self, interaction: discord.Interaction) -> None:
        match self.action:
            case "restart":
                await send_restart(interaction, self.service)
            case "tail":
                await send_tail(interaction, self.service, 30)
            case _:
                await send_journal(interaction, self.service, 5000)


# The alert scripts read their Discord webhook URL from this file; /alertwebhook replaces it.
ALERTS_WEBHOOK_URL_FILE = Path.home() / ".config" / "failure-webhook-url"


def save_alerts_webhook_url(url: str) -> None:
    """Make ``url`` the alerts' webhook, keeping the previous URL next to it as ``*.old``."""
    if ALERTS_WEBHOOK_URL_FILE.exists():
        ALERTS_WEBHOOK_URL_FILE.replace(ALERTS_WEBHOOK_URL_FILE.with_name(ALERTS_WEBHOOK_URL_FILE.name + ".old"))
    ALERTS_WEBHOOK_URL_FILE.write_text(url + "\n")
    ALERTS_WEBHOOK_URL_FILE.chmod(0o600)


async def version_line(name: str, folder: Path, started: int | None) -> str:
    """Build the /versions line of a bot from its git checkout, fetching GitHub's ``main`` first."""
    git = ("git", "-C", str(folder))
    fetched = await output_of(*git, "fetch", "--quiet", "origin", "main")
    commit, behind, status = await asyncio.gather(
        output_of(*git, "log", "-1", "--format=%h%x00%ct%x00%s"),
        output_of(*git, "rev-list", "--count", "HEAD..origin/main"),
        output_of(*git, "status", "--porcelain", "--untracked-files=no"),
    )
    return format_version_line(
        name,
        commit=commit,
        behind=behind.strip() if fetched is not None and behind is not None else None,
        dirty=bool(status and status.strip()),
        started=started,
    )


GREP_SINCE_CHOICES = [
    app_commands.Choice(name="last hour", value="1 hour ago"),
    app_commands.Choice(name="last 6 hours", value="6 hours ago"),
    app_commands.Choice(name="last day", value="1 day ago"),
    app_commands.Choice(name="last week", value="7 days ago"),
    app_commands.Choice(name="last 30 days", value="30 days ago"),
]
# The newest this many matches are fetched; the reply shows what fits and attaches all of them as a file.
GREP_MAX_MATCHES = 1000


class Services(commands.Cog):
    """Owner-only commands to manage the bots' systemd services on the VPS from Discord, and to check the VPS's health."""

    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot

    @override
    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(AlertButton)

    @override
    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(AlertButton)

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
        await send_restart(interaction, service)

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
        await send_systemctl(interaction, "start", service)

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
        await send_systemctl(interaction, "stop", service)

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

        await interaction.followup.send(view=status_view(services, int(time.time())))

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
        await send_tail(interaction, service, lines)

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
        await send_journal(interaction, service, lines)

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(service=service_choices(), since=GREP_SINCE_CHOICES)
    async def grep(
        self,
        interaction: discord.Interaction[LalaBot],
        service: str,
        pattern: app_commands.Range[str, 1, 100],
        since: str = "1 day ago",
    ) -> None:
        """🔎 Search a bot's systemd journal, e.g. for every Traceback of the last day.

        Parameters
        ----------
        service
            Bot to search the journal of.
        pattern
            Regular expression to look for; case-insensitive unless it has uppercase letters.
        since
            How far back to search.
        """
        await interaction.response.defer()
        returncode, output = await shell.run(
            "journalctl",
            f"--unit={service}",
            f"--grep={pattern}",
            f"--since={since}",
            f"--lines={GREP_MAX_MATCHES}",
            "--output=json",
            "--output-fields=MESSAGE",
            "--no-pager",
        )
        entries = [format_journal_entry(line, with_date=True) for line in output.splitlines() if line.startswith("{")]
        if not entries:
            # journalctl exits with 1 both when nothing matches and for errors, e.g. an invalid pattern.
            if returncode in {0, 1} and not output.strip():
                await interaction.followup.send(f"🔎 No `{pattern}` in {label(service)}'s journal since {since}.")
            else:
                await interaction.followup.send(f"❌ `journalctl` exited with code {returncode}.\n{codeblock(output)}")
            return

        shown = fit_newest(entries)
        count = f"{len(entries)}+" if len(entries) == GREP_MAX_MATCHES else str(len(entries))
        header = f"🔎 {count} lines with `{pattern}` in {label(service)}'s journal since {since}"
        header += f", the newest {len(shown)} (UTC):" if len(shown) < len(entries) else " (UTC):"
        files: list[discord.File] = []
        if len(shown) < len(entries):
            files.append(discord.File(io.BytesIO("\n".join(entries).encode()), filename=f"{service}-grep.log"))
        await interaction.followup.send(journal_message(header, shown), files=files)

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    async def versions(self, interaction: discord.Interaction[LalaBot]) -> None:
        """🏷️ Show the commit each bot runs, and whether it's behind GitHub or not restarted since."""
        if sys.platform != "linux":
            await interaction.response.send_message("🏠 Running at home, there are no deployed bots to check.")
            return
        await interaction.response.defer()
        try:
            services = await show_services(const.SERVICES, ["ActiveEnterTimestampMonotonic"])
        except RuntimeError:
            services = {}
        lines = await asyncio.gather(
            *(
                version_line(
                    name,
                    Path.home() / service.folder,
                    monotonic_to_unix(services.get(name, {}).get("ActiveEnterTimestampMonotonic", "")),
                )
                for name, service in const.SERVICES.items()
            )
        )
        await interaction.followup.send("\n".join(lines))

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    async def alertwebhook(self, interaction: discord.Interaction[LalaBot]) -> None:
        """🪝 Create LalaBot's own webhook in this channel and make the VPS alerts post through it, with buttons."""
        if sys.platform != "linux":
            await interaction.response.send_message("🏠 Running at home, the VPS's alerts aren't here.", ephemeral=True)
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Use it in a text channel.", ephemeral=True)
            return
        try:
            webhook = await channel.create_webhook(name="VPS alerts", reason="Alerts with buttons, from /alertwebhook")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I need the Manage Webhooks permission here.", ephemeral=True)
            return
        save_alerts_webhook_url(webhook.url)
        await interaction.response.send_message(
            f"🪝 The VPS alerts now post here through **{webhook.name}**, with buttons. "
            f"The previous URL is kept in `{ALERTS_WEBHOOK_URL_FILE}.old`; "
            "its webhook can be deleted in the channel's Integrations settings.",
            ephemeral=True,
        )

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

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    async def reboot(self, interaction: discord.Interaction[LalaBot]) -> None:
        """🔁 Reboot the VPS, after a confirmation; every bot starts again on its own."""
        if sys.platform != "linux":
            await interaction.response.send_message("🏠 Running at home, there's no VPS to reboot.")
            return
        view = ConfirmReboot()
        await interaction.response.send_message(
            f"🔁 Reboot the VPS? Every bot goes down for a minute or two.\n{reboot_line()}", view=view
        )
        view.message = await interaction.original_response()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # `on_ready` also fires after reconnects, but the marker is gone after the first time.
        if (marker := pop_reboot_marker()) is None:
            return
        channel_id, rebooted_at = marker
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            await channel.send(
                f"✅ The VPS is back up, the reboot took {round(time.time() - rebooted_at)} s.\n{reboot_line()}"
            )


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Services(bot))
