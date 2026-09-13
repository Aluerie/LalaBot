from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from main import LalaBot

from .utils import const

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)


class Cmd(commands.Cog):
    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot

    @commands.command()
    async def sync(self, ctx: commands.Context[LalaBot]) -> None:
        await ctx.bot.tree.sync()
        await ctx.send(f"synced the tree {const.MADGE_EMOTE}")

    @app_commands.command()
    async def systemctl(
        self,
        interaction: discord.Interaction[LalaBot],
        request: Literal["restart", "stop", "start"],
        service: Literal["alubot", "irenesbot", "lalabot"],
    ) -> None:
        """Perform a `sudo systemctl` shell command."""
        try:
            result = await asyncio.create_subprocess_shell(f"sudo systemctl {request} {service}")
            await interaction.response.send_message(f"I think we successfully did it. `result={result}`")
        except Exception:
            log.exception("Exception happened during !systemctl command", stack_info=True)
            # it might not go off
            await interaction.response.send_message("Something went wrong.")

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context[LalaBot], error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            # manual list, but whatever.
            await ctx.send(f"{const.MADGE_EMOTE} Use slash commands!")
        elif isinstance(error, (commands.BadLiteralArgument, commands.MissingRequiredArgument)):
            await ctx.send(str(error))


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Cmd(bot))
