from __future__ import annotations

import datetime as dt
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, override

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import config

from .utils import const, shell
from .utils.systemd import is_systemd_service

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)

BACKUP_HEALTHCHECK_URL: str | None = getattr(config, "BACKUP_HEALTHCHECK_URL", None)


async def ping_backup_healthcheck(*, failed: bool = False) -> None:
    """Ping the healthchecks.io check of the backups: after every successful backup, or with ``/fail`` after a failed one.

    healthchecks.io then alerts when the daily backup fails or doesn't happen at all.
    Only pings when running as a systemd service, so a backup made at home doesn't count.
    """
    if BACKUP_HEALTHCHECK_URL is None or not is_systemd_service():
        return
    url = f"{BACKUP_HEALTHCHECK_URL}/fail" if failed else BACKUP_HEALTHCHECK_URL
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session,
            session.get(url) as response,
        ):
            response.raise_for_status()
    except TimeoutError, aiohttp.ClientError:
        log.warning("Failed to ping the backup healthcheck", exc_info=True)


class Backup(commands.Cog):
    """Backs up the VPS PostgreSQL database to its post in the bot-stuff forum every day.

    The daily task only runs as a systemd service, so a local run at home doesn't post backups too.
    """

    def __init__(self, bot: LalaBot) -> None:
        self.bot: LalaBot = bot

    @override
    async def cog_load(self) -> None:
        if not is_systemd_service():
            log.info("Not running as a systemd service, so not backing up the database daily.")
            return
        if BACKUP_HEALTHCHECK_URL is None:
            log.warning("BACKUP_HEALTHCHECK_URL is not set in config.py, so nothing notices missing backups.")
        self.daily_backup.start()

    @override
    async def cog_unload(self) -> None:
        self.daily_backup.cancel()

    async def get_backup_thread(self) -> discord.Thread:
        thread_id = const.DATABASE_BACKUP_THREAD_ID
        # Archived threads aren't cached, hence the fetch. Sending a message to an archived thread unarchives it.
        thread = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
        if not isinstance(thread, discord.Thread):
            msg = f"Channel {thread_id} is not a thread."
            raise TypeError(msg)
        return thread

    async def send_backup(self) -> None:
        """Dump the database with ``pg_dump``, send the dump to the backups post and ping the backup healthcheck."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{const.DATABASE_NAME}.sql"
            returncode, output = await shell.run("pg_dump", "-f", str(path), const.DATABASE_NAME, timeout=300)
            if returncode != 0:
                msg = f"pg_dump exited with code {returncode}: {output.strip()}"
                raise RuntimeError(msg)
            thread = await self.get_backup_thread()
            await thread.send(file=discord.File(path))
        await ping_backup_healthcheck()

    @tasks.loop(time=dt.time(hour=18, minute=5, tzinfo=dt.UTC))
    async def daily_backup(self) -> None:
        # An exception would stop the loop for good, so report it and wait for tomorrow instead.
        try:
            await self.send_backup()
        except Exception:
            log.exception("Daily database backup failed")
            await ping_backup_healthcheck(failed=True)
            try:
                thread = await self.get_backup_thread()
                await thread.send(f"<@{const.OWNER_ID}> ❌ Daily database backup failed, see `/journal lalabot`.")
            except Exception:
                log.exception("Failed to report the failed database backup")

    @daily_backup.before_loop
    async def before_daily_backup(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    async def backup(self, interaction: discord.Interaction[LalaBot]) -> None:
        """💾 Back up the database to the bot-stuff forum right now."""
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
        await interaction.followup.send(f"✅ Sent the backup to <#{const.DATABASE_BACKUP_THREAD_ID}>.")


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Backup(bot))
