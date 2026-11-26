# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

import asyncio
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
from .utils.forum import get_thread
from .utils.systemd import is_systemd_service

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)

BACKUP_HEALTHCHECK_URL: str | None = getattr(config, "BACKUP_HEALTHCHECK_URL", None)

# A full dump is ~1.2 MB (November 2026); far below that means something went wrong, not that the data shrank.
MIN_DUMP_BYTES = 100_000
# The last line of every complete plain-text `pg_dump` output.
DUMP_COMPLETE_MARKER = b"-- PostgreSQL database dump complete"


def check_dump(path: Path) -> None:
    """Raise ``RuntimeError`` unless the dump at ``path`` looks complete.

    Guards against posting an empty or cut-off file as a "successful" backup when ``pg_dump`` exits with 0 anyway.
    """
    size = path.stat().st_size
    if size < MIN_DUMP_BYTES:
        msg = f"The dump is only {size} bytes, expected at least {MIN_DUMP_BYTES}."
        raise RuntimeError(msg)
    with path.open("rb") as file:
        file.seek(-1024, 2)  # the end of the file is enough, and `size` is known to be bigger than this
        if DUMP_COMPLETE_MARKER not in file.read():
            msg = f"The dump ({size} bytes) doesn't end with {DUMP_COMPLETE_MARKER.decode()!r}, so it's cut off."
            raise RuntimeError(msg)


async def psql(query: str) -> list[list[str]]:
    """Run a query on the bots' database with ``psql`` and return its rows as lists of strings.

    Raises ``RuntimeError`` when ``psql`` fails.
    """
    returncode, output = await shell.run(
        "psql",
        "--no-psqlrc",
        "--tuples-only",
        "--no-align",
        "--field-separator=\t",
        f"--dbname={const.DATABASE_NAME}",
        f"--command={query}",
    )
    if returncode != 0:
        msg = f"psql exited with code {returncode}: {output.strip()}"
        raise RuntimeError(msg)
    return [line.split("\t") for line in output.splitlines() if line]


DATABASE_QUERY = """
select current_setting('server_version'),
       pg_size_pretty(pg_database_size(current_database())),
       extract(epoch from pg_postmaster_start_time())::bigint
"""
TABLES_QUERY = """
select schemaname || '.' || relname, pg_size_pretty(pg_total_relation_size(relid)), n_live_tup
from pg_stat_user_tables
order by pg_total_relation_size(relid) desc
limit 5
"""
# The bots' pools don't set `application_name`, so connections can't be told apart per bot; the total is what matters:
# once it reaches `max_connections`, new ones get refused.
CONNECTIONS_QUERY = """
select count(*), count(*) filter (where state = 'active'), current_setting('max_connections')
from pg_stat_activity
where datname = current_database()
"""


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
        return await get_thread(self.bot, const.DATABASE_BACKUP_THREAD_ID)

    async def send_backup(self) -> None:
        """Dump the database with ``pg_dump``, send the dump to the backups post and ping the backup healthcheck."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{const.DATABASE_NAME}.sql"
            returncode, output = await shell.run("pg_dump", "-f", str(path), const.DATABASE_NAME, timeout=300)
            if returncode != 0:
                msg = f"pg_dump exited with code {returncode}: {output.strip()}"
                raise RuntimeError(msg)
            check_dump(path)
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
            await interaction.response.send_message(f"{const.HU_EMOTE} Not for you.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await self.send_backup()
        except Exception as error:
            log.exception("Database backup failed")
            await interaction.followup.send(f"❌ Backup failed: {error}"[:2000])
            return
        await interaction.followup.send(f"✅ Sent the backup to <#{const.DATABASE_BACKUP_THREAD_ID}>.")

    async def last_backup_line(self) -> str:
        """Describe the newest backup in the backups post: its size, age and a link to it."""
        thread = await self.get_backup_thread()
        async for message in thread.history(limit=20):
            if message.attachments:
                size = message.attachments[0].size / 1024**2
                return f"💾 Last backup: {size:.1f} MB, <t:{int(message.created_at.timestamp())}:R> · {message.jump_url}"
        return f"⚠️ No backup among the last 20 messages of <#{const.DATABASE_BACKUP_THREAD_ID}>"

    @app_commands.command()
    @app_commands.default_permissions(manage_guild=True)
    async def db(self, interaction: discord.Interaction[LalaBot]) -> None:
        """🐘 Show the database's health: size, biggest tables, connections and the last backup."""
        if interaction.user.id != const.OWNER_ID:
            await interaction.response.send_message(f"{const.HU_EMOTE} Not for you.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            database, tables, connections = await asyncio.gather(
                psql(DATABASE_QUERY), psql(TABLES_QUERY), psql(CONNECTIONS_QUERY)
            )
        except (OSError, TimeoutError, RuntimeError) as error:
            await interaction.followup.send(f"❌ Couldn't query the database: {error}"[:2000])
            return

        version, size, started = database[0]
        total, active, maximum = connections[0]
        # Warn at 80%: each bot's pool holds its connections open, so the count only grows with new bots or pools.
        connections_mark = "⚠️" if int(total) * 100 >= int(maximum) * 80 else "🔌"
        lines = [
            # `server_version` is like "19.0 (Ubuntu 19.0-2.pgdg22.04+1)"
            f"🐘 **`{const.DATABASE_NAME}`**: {size}, PostgreSQL {version.split()[0]} running since <t:{started}:R>",
            f"{connections_mark} Connections: {total} of max {maximum}, {active} active",
            "📦 Biggest tables:",
            *(f"- `{name}` {table_size}, {int(rows):,} rows" for name, table_size, rows in tables),
            await self.last_backup_line(),
        ]
        await interaction.followup.send("\n".join(lines))


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Backup(bot))
