from pathlib import Path

# Discord Message
MADGE_EMOTE = "<:DankMadgeThreat:1125591898241892482>"

# Discord Snowflakes
OWNER_ID = 312204139751014400
DATABASE_BACKUP_CHANNEL_ID = 1541985585810378823

# VPS
# systemd services on the VPS, mapped to the log file the bot writes (`None`: the bot only logs to the journal).
SERVICES: dict[str, Path | None] = {
    "alubot": Path("/home/ubuntu/AluBot/.temp/alubot.log"),
    "dota2bot": Path("/home/ubuntu/Dota2Bot/.temp/d2b.log"),
    "irebot": Path("/home/ubuntu/IrenesBot/.temp/irebot.log"),
    "lalabot": None,
}
# PostgreSQL database with the data of all the bots on the VPS.
DATABASE_NAME = "alubot"
