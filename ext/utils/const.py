from typing import NamedTuple

# Discord Message
HU_EMOTE = "<:hu:1563745689010184232>"

# Discord Snowflakes
OWNER_ID = 312204139751014400
# Posts (threads) in the bot-stuff forum channel, one per bot feature that posts to Discord.
DATABASE_BACKUP_THREAD_ID = 1575256163321389096


# VPS
class Service(NamedTuple):
    emoji: str
    """Shown next to the service's name in command options and replies."""


# systemd services on the VPS, by their unit name.
SERVICES: dict[str, Service] = {
    "alubot": Service("🦢"),
    "dota2bot": Service("⚔️"),
    "irebot": Service("💜"),
    "lalabot": Service("🖤"),
}
# PostgreSQL database with the data of all the bots on the VPS.
DATABASE_NAME = "alubot"
