from __future__ import annotations

import datetime as dt
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, override

import discord
from discord import app_commands
from discord.ext import commands, tasks

from .utils import const, shell
from .utils.systemd import is_systemd_service

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)


class Backup(commands.Cog):
    """Backs up the VPS PostgreSQL database to a Discord channel every day.

    The daily task only runs as a systemd service, so a local run at home doesn't post backups too.
    """

    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot

    @override
    async def cog_load(self) -> None:
        if not is_systemd_service():
            log.info("Not running as a systemd service, so not backing up the database daily.")
            return
        self.daily_backup.start()

    @override
    async def cog_unload(self) -> None:
        self.daily_backup.cancel()

    async def get_backup_channel(self) -> discord.abc.Messageable:
        channel = self.bot.get_channel(const.DATABASE_BACKUP_CHANNEL_ID) or await self.bot.fetch_channel(
            const.DATABASE_BACKUP_CHANNEL_ID
        )
        if not isinstance(channel, discord.abc.Messageable):
            msg = f"Channel {const.DATABASE_BACKUP_CHANNEL_ID} can't receive messages."
            raise TypeError(msg)
        return channel

    async def send_backup(self) -> None:
        """Dump the database with ``pg_dump`` and send the dump to the backup channel."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{const.DATABASE_NAME}.sql"
            returncode, output = await shell.run("pg_dump", "-f", str(path), const.DATABASE_NAME, timeout=300)
            if returncode != 0:
                msg = f"pg_dump exited with code {returncode}: {output.strip()}"
                raise RuntimeError(msg)
            channel = await self.get_backup_channel()
            await channel.send(file=discord.File(path))

    @tasks.loop(time=dt.time(hour=18, minute=5, tzinfo=dt.UTC))
    async def daily_backup(self) -> None:
        # An exception would stop the loop for good, so report it and wait for tomorrow instead.
        try:
            await self.send_backup()
        except Exception:
            log.exception("Daily database backup failed")
            try:
                channel = await self.get_backup_channel()
                await channel.send(f"<@{const.OWNER_ID}> ❌ Daily database backup failed, see `/journal lalabot`.")
            except Exception:
                log.exception("Failed to report the failed database backup")

    @daily_backup.before_loop
    async def before_daily_backup(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command()
    @app_commands.default_permissions(administrator=True)
    async def backup(self, interaction: discord.Interaction[LalaBot]) -> None:
        """Back up the database to the backup channel right now."""
        if interaction.user.id != const.OWNER_ID:
            await interaction.response.send_message(f"{const.MADGE_EMOTE} Not for you.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await self.send_backup()
        except Exception as error:
            log.exception("Database backup failed")
            await interaction.followup.send(f"❌ Backup failed: {error}"[:2000])
            return
        await interaction.followup.send(f"✅ Sent the backup to <#{const.DATABASE_BACKUP_CHANNEL_ID}>.")


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Backup(bot))
