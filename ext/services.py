from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
from typing import TYPE_CHECKING, Literal, override

import discord
from discord import app_commands
from discord.ext import commands

from .utils import const, shell

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)

SERVICE_CHOICES = [app_commands.Choice(name=service, value=service) for service in const.SERVICES]
LOG_FILE_CHOICES = [app_commands.Choice(name=service, value=service) for service, path in const.SERVICES.items() if path]

STATE_EMOJIS = {
    "active": "🟢",
    "reloading": "🟡",
    "activating": "🟡",
    "deactivating": "🟡",
    "inactive": "⚫",
    "failed": "🔴",
}


def codeblock(text: str, limit: int = 1800) -> str:
    """Wrap the tail of ``text`` (where commands put their errors) into a Discord code block."""
    return f"```\n{text.strip()[-limit:]}\n```"


def format_journal_entry(line: str) -> str:
    """Turn one line of ``journalctl --output=json`` into a short ``HH:MM:SS message`` line (time in UTC)."""
    entry = json.loads(line)
    timestamp = dt.datetime.fromtimestamp(int(entry["__REALTIME_TIMESTAMP"]) / 1_000_000, tz=dt.UTC)
    message = entry.get("MESSAGE")
    if isinstance(message, list):  # journald stores messages that aren't valid UTF-8 as a list of bytes
        message = bytes(message).decode(errors="replace")
    return f"{timestamp:%H:%M:%S} {message or ''}"


class Services(commands.Cog):
    """Owner-only commands to manage the bots' systemd services on the VPS from Discord."""

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
            await interaction.response.send_message(f"{const.MADGE_EMOTE} Not for you.", ephemeral=True)
            return
        log.error("Unhandled error in /%s", interaction.command and interaction.command.name, exc_info=error)
        content = "Something went wrong, see logs."
        if interaction.response.is_done():
            await interaction.followup.send(content)
        else:
            await interaction.response.send_message(content)

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(service=SERVICE_CHOICES)
    async def systemctl(
        self,
        interaction: discord.Interaction[LalaBot],
        request: Literal["restart", "stop", "start"],
        service: str,
    ) -> None:
        """Perform a `sudo systemctl` shell command."""
        if service == "lalabot" and request == "stop":
            await interaction.response.send_message(
                f"{const.MADGE_EMOTE} Stopping LalaBot can't be undone from Discord. Use SSH if you really mean it.",
                ephemeral=True,
            )
            return

        if service == "lalabot" and request == "restart":
            # The command below kills this very process, so there would be nobody left to send the result.
            await interaction.response.send_message(f"Running `sudo systemctl {request} {service}`, bye.")
            await asyncio.create_subprocess_exec("sudo", "systemctl", request, service)
            return

        await interaction.response.defer()
        returncode, output = await shell.run("sudo", "systemctl", request, service)
        status = "✅" if returncode == 0 else "❌"
        message = f"{status} `sudo systemctl {request} {service}` exited with code {returncode}."
        if output.strip():
            message += f"\n{codeblock(output)}"
        await interaction.followup.send(message)

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction[LalaBot]) -> None:
        """Show whether each service is running, since when, and how many times systemd restarted it."""
        await interaction.response.defer()
        returncode, output = await shell.run(
            "systemctl",
            "show",
            *const.SERVICES,
            "--property=Id,ActiveState,SubState,ActiveEnterTimestamp,NRestarts",
            "--timestamp=unix",
        )
        if returncode != 0:
            await interaction.followup.send(f"❌ `systemctl show` exited with code {returncode}.\n{codeblock(output)}")
            return

        lines: list[str] = []
        # `systemctl show` prints one block of `Key=Value` lines per unit, separated by blank lines.
        for block in output.strip().split("\n\n"):
            props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
            name = props.get("Id", "?").removesuffix(".service")
            state = props.get("ActiveState", "?")
            line = f"{STATE_EMOJIS.get(state, '❔')} **{name}** {state} ({props.get('SubState', '?')})"
            if state == "active" and (timestamp := props.get("ActiveEnterTimestamp", "")).startswith("@"):
                line += f" since <t:{timestamp.removeprefix('@')}:R>"
            if (restarts := props.get("NRestarts", "0")) != "0":
                line += f", auto-restarted {restarts} times"
            lines.append(line)
        await interaction.followup.send("\n".join(lines))

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(service=SERVICE_CHOICES)
    async def journal(
        self,
        interaction: discord.Interaction[LalaBot],
        service: str,
        lines: app_commands.Range[int, 1, 20_000] = 5000,
    ) -> None:
        """Get the last lines of a service's systemd journal as a file.

        Parameters
        ----------
        service
            Service to get the journal of.
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

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(service=SERVICE_CHOICES)
    async def tail(
        self,
        interaction: discord.Interaction[LalaBot],
        service: str,
        lines: app_commands.Range[int, 1, 200] = 30,
    ) -> None:
        """Show the last lines of a service's systemd journal right in the chat.

        Parameters
        ----------
        service
            Service to show the journal of.
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

        header = f"📓 Last {len(shown)} journal lines of `{service}` (UTC):"
        body = "\n".join(shown).replace("```", "`\N{ZERO WIDTH SPACE}``") or "(empty)"
        await interaction.followup.send(f"{header}\n```\n{body}\n```")

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(service=LOG_FILE_CHOICES)
    async def logs(self, interaction: discord.Interaction[LalaBot], service: str) -> None:
        """Get the log file that a bot writes itself.

        Parameters
        ----------
        service
            Bot to get the log file of.
        """
        path = const.SERVICES[service]
        if path is None or not path.is_file():
            await interaction.response.send_message(f"❌ No log file at `{path}`.")
            return
        await interaction.response.defer()
        await interaction.followup.send(file=discord.File(path))


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Services(bot))
