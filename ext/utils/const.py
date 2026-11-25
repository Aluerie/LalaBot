from pathlib import Path
from typing import NamedTuple

# Discord Message
MADGE_EMOTE = "<:DankMadgeThreat:1125591898241892482>"

# Discord Snowflakes
OWNER_ID = 312204139751014400
# Posts (threads) in the bot-stuff forum channel, one per bot feature that posts to Discord.
DATABASE_BACKUP_THREAD_ID = 1575256163321389096


# VPS
class Service(NamedTuple):
    emoji: str
    """Shown next to the service's name in command options and replies."""
    log_file: Path | None
    """Log file the bot writes itself; `None` when the bot only logs to the systemd journal."""


# systemd services on the VPS, by their unit name.
SERVICES: dict[str, Service] = {
    "alubot": Service("🦢", Path("/home/ubuntu/AluBot/.temp/alubot.log")),
    "dota2bot": Service("⚔️", Path("/home/ubuntu/Dota2Bot/.temp/d2b.log")),
    "irebot": Service("💜", Path("/home/ubuntu/IrenesBot/.temp/irebot.log")),
    "lalabot": Service("🖤", None),
}
# PostgreSQL database with the data of all the bots on the VPS.
DATABASE_NAME = "alubot"
