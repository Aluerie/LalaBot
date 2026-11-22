import datetime

# Watcher
ALERT_AFTER = datetime.timedelta(minutes=25)
"""How long a check must keep failing before the owner gets pinged (with the ~15 min loop: 3rd failed check in a row)."""
HEARTBEAT_MAX_AGE = datetime.timedelta(minutes=15)
"""IreBot counts as down when its last heartbeat message is older than this."""
IREBOT_SERVICE = "irebot"

# Colors
DISCORD_COLOR = 0x5865F2
TWITCH_COLOR = 0x9146FF

# Discord Message
MADGE_EMOTE = "<:DankMadgeThreat:1125591898241892482>"
OWNER_ID = 312204139751014400
MENTION_OWNER = f"<@{OWNER_ID}>"

# Discord Snowflakes
HEARTBEAT_CHANNEL_ID = 1548502460010733649
LALAWATCH_CHANNEL_ID = 1548664280558084166

TEST_GUILD_ID = 759916212842659850

ALUBOT_ID = 713124699663499274
LALA_BOT_ID = 812763204010246174
