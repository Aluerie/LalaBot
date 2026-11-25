from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from .utils import const

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)


class Cmd(commands.Cog):
    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot

    @commands.command()
    @commands.is_owner()
    async def sync(self, ctx: commands.Context[LalaBot]) -> None:
        await ctx.bot.tree.sync()
        await ctx.send(f"synced the tree {const.MADGE_EMOTE}")

    @app_commands.command()
    async def systemctl(
        self,
        interaction: discord.Interaction[LalaBot],
        request: Literal["restart", "stop", "start"],
        service: Literal["alubot", "irebot", "lalabot"],
    ) -> None:
        """Perform a `sudo systemctl` shell command."""
        if interaction.user.id != const.OWNER_ID:
            await interaction.response.send_message(f"{const.MADGE_EMOTE} Not for you.", ephemeral=True)
            return

        if service == "lalabot" and request != "start":
            # The command below kills this very process, so there would be nobody left to send the result.
            await interaction.response.send_message(f"Running `sudo systemctl {request} {service}`, bye.")
            await asyncio.create_subprocess_exec("sudo", "systemctl", request, service)
            return

        await interaction.response.defer()
        try:
            process = await asyncio.create_subprocess_exec(
                "sudo",
                "systemctl",
                request,
                service,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await process.communicate()
        except Exception:
            log.exception("Exception happened during /systemctl command")
            await interaction.followup.send("Something went wrong, see logs.")
            return

        output = stdout.decode(errors="replace").strip()[:1800]
        status = "✅" if process.returncode == 0 else "❌"
        message = f"{status} `sudo systemctl {request} {service}` exited with code {process.returncode}."
        if output:
            message += f"\n```\n{output}\n```"
        await interaction.followup.send(message)

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context[LalaBot], error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            # manual list, but whatever.
            await ctx.send(f"{const.MADGE_EMOTE} Use slash commands!")
        elif isinstance(error, (commands.BadLiteralArgument, commands.MissingRequiredArgument)):
            await ctx.send(str(error))
        elif isinstance(error, commands.NotOwner):
            await ctx.send(f"{const.MADGE_EMOTE} Not for you.")
        else:
            log.error("Unhandled error in command %s", ctx.command, exc_info=error)


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Cmd(bot))
