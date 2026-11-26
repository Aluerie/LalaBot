# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import logging
import shutil
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
from .utils.postgres import psql
from .utils.systemd import is_systemd_service

if TYPE_CHECKING:
    from main import LalaBot


log = logging.getLogger(__name__)

BACKUP_HEALTHCHECK_URL: str | None = getattr(config, "BACKUP_HEALTHCHECK_URL", None)
# rclone destination of the off-site copies, e.g. "b2:my-bucket/alubot"; the credentials live in rclone's own config.
BACKUP_REMOTE: str | None = getattr(config, "BACKUP_REMOTE", None)
# Off-site copies older than this are deleted after each upload; Discord keeps every backup anyway.
REMOTE_KEEP_DAYS = 30

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


def compress(path: Path, name: str) -> Path:
    """Gzip the file at ``path`` into ``name`` next to it and return the new file's path."""
    target = path.with_name(name)
    with path.open("rb") as source, gzip.open(target, "wb") as destination:
        shutil.copyfileobj(source, destination)
    return target


async def rclone(*args: str, timeout: float = 120.0) -> str:  # noqa: ASYNC109
    """Run ``rclone`` with ``args`` and return its output, raising ``RuntimeError`` when it fails."""
    returncode, output = await shell.run("rclone", *args, timeout=timeout)
    if returncode != 0:
        msg = f"rclone {args[0]} exited with code {returncode}: {output.strip()}"
        raise RuntimeError(msg)
    return output


async def upload_off_site(dump: Path, remote: str) -> None:
    """Upload a gzipped copy of ``dump`` to ``remote``, named by today's date, and delete the copies that are too old."""
    archive = await asyncio.to_thread(compress, dump, f"{const.DATABASE_NAME}-{dt.datetime.now(dt.UTC):%Y-%m-%d}.sql.gz")
    await rclone("copyto", str(archive), f"{remote}/{archive.name}")
    await rclone("delete", "--min-age", f"{REMOTE_KEEP_DAYS}d", remote)


async def newest_off_site(remote: str) -> tuple[str, int, str] | None:
    """Get the name, size in bytes and modification time of the newest file in ``remote``, if it has any."""
    output = await rclone("lsf", "--format", "pst", "--separator", "|", remote, timeout=30)
    files = [line.split("|") for line in output.splitlines() if line.strip()]
    if not files:
        return None
    name, size, modified = max(files, key=lambda file: file[2])
    return name, int(size), modified


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
# The total is what matters: once it reaches `max_connections`, new ones get refused.
# Per client, by `application_name` (the bots' pools set it to the bot's name), or the user when it's empty.
CONNECTIONS_QUERY = """
with connections as (
    select coalesce(nullif(application_name, ''), usename, '?') as client, state
    from pg_stat_activity
    where datname = current_database()
),
clients as (
    select client, count(*) as count from connections group by client
)
select (select count(*) from connections),
       (select count(*) from connections where state = 'active'),
       current_setting('max_connections'),
       (select string_agg(client || ' ' || count, ', ' order by count desc, client) from clients)
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
        if BACKUP_REMOTE is None:
            log.warning("BACKUP_REMOTE is not set in config.py, so the backups only go to Discord.")
        self.daily_backup.start()

    @override
    async def cog_unload(self) -> None:
        self.daily_backup.cancel()

    async def get_backup_thread(self) -> discord.Thread:
        return await get_thread(self.bot, const.DATABASE_BACKUP_THREAD_ID)

    async def send_backup(self) -> None:
        """Dump the database with ``pg_dump``, store the dump in both places and ping the backup healthcheck.

        The places are the backups post and, on the VPS, the off-site ``BACKUP_REMOTE``. One failing doesn't stop
        the other, but the backup only counts as done (and pings the healthcheck) when both worked.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{const.DATABASE_NAME}.sql"
            returncode, output = await shell.run("pg_dump", "-f", str(path), const.DATABASE_NAME, timeout=300)
            if returncode != 0:
                msg = f"pg_dump exited with code {returncode}: {output.strip()}"
                raise RuntimeError(msg)
            check_dump(path)

            errors: list[str] = []
            try:
                thread = await self.get_backup_thread()
                await thread.send(file=discord.File(path))
            except Exception as error:
                log.exception("Failed to send the backup to Discord")
                errors.append(f"Discord: {error}")
            # Only from the VPS: `config.py` is copied there from home, so a run at home has the remote set too.
            if BACKUP_REMOTE is not None and is_systemd_service():
                try:
                    await upload_off_site(path, BACKUP_REMOTE)
                except Exception as error:
                    log.exception("Failed to upload the backup off-site")
                    errors.append(f"off-site: {error}")
            if errors:
                raise RuntimeError("; ".join(errors))
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

    async def off_site_line(self) -> str:
        """Describe the newest off-site backup: its name, size and age."""
        if BACKUP_REMOTE is None:
            return "⚠️ No off-site backups: `BACKUP_REMOTE` isn't set in config.py"
        try:
            newest = await newest_off_site(BACKUP_REMOTE)
        except (OSError, TimeoutError, RuntimeError) as error:
            return f"⚠️ Couldn't list the off-site backups: {error}"[:300]
        if newest is None:
            return f"⚠️ No off-site backups in `{BACKUP_REMOTE}` yet"
        name, size, modified = newest
        # `lsf` prints times like "2026-11-26 18:05:12", in UTC
        moment = dt.datetime.strptime(modified[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.UTC)
        return f"☁️ Off-site: `{name}`, {size / 1024:.0f} KB, <t:{int(moment.timestamp())}:R>"

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
        total, active, maximum, per_client = connections[0]
        # Warn at 80%: each bot's pool holds its connections open, so the count only grows with new bots or pools.
        connections_mark = "⚠️" if int(total) * 100 >= int(maximum) * 80 else "🔌"
        lines = [
            # `server_version` is like "19.0 (Ubuntu 19.0-2.pgdg22.04+1)"
            f"🐘 **`{const.DATABASE_NAME}`**: {size}, PostgreSQL {version.split()[0]} running since <t:{started}:R>",
            f"{connections_mark} Connections: {total} of max {maximum}, {active} active ({per_client})",
            "📦 Biggest tables:",
            *(f"- `{name}` {table_size}, {int(rows):,} rows" for name, table_size, rows in tables),
            await self.last_backup_line(),
            await self.off_site_line(),
        ]
        await interaction.followup.send("\n".join(lines))


async def setup(bot: LalaBot) -> None:
    """Load LalaBot extension. Framework of discord.py."""
    await bot.add_cog(Backup(bot))
