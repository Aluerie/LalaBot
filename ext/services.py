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


def service_choices(*, exclude: tuple[str, ...] = (), with_log_file: bool = False) -> list[app_commands.Choice[str]]:
    """Build the choices of a ``service`` option: every service with its emoji, minus the excluded ones."""
    return [
        app_commands.Choice(name=f"{service.emoji} {name}", value=name)
        for name, service in const.SERVICES.items()
        if name not in exclude and (service.log_file or not with_log_file)
    ]


def label(name: str) -> str:
    """Format a service name for a Discord message, with its emoji."""
    service = const.SERVICES.get(name)
    return f"{service.emoji} **{name}**" if service else f"**{name}**"


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

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(service=service_choices(with_log_file=True))
    async def logs(self, interaction: discord.Interaction[LalaBot], service: str) -> None:
        """🪵 Get the log file that a bot writes itself.

        Parameters
        ----------
        service
            Bot to get the log file of.
        """
        path = const.SERVICES[service].log_file
        if path is None or not path.is_file():
            await interaction.response.send_message(f"❌ No log file at `{path}`.")
            return
        await interaction.response.defer()
        await interaction.followup.send(file=discord.File(path))


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Services(bot))
