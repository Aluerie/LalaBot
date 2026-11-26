#!/bin/sh
# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
# Post a Discord webhook message when a systemd service stops abnormally (crash, watchdog kill, start timeout).
# Every bot's unit runs this one copy:  ExecStopPost=/bin/sh /home/ubuntu/LalaBot/scripts/notify_failure.sh %n
# The webhook URL is read from the file below (a single line, kept out of git).
# A crash-looping service only gets one message per THROTTLE_SECONDS; the next one counts the failures in between.

WEBHOOK_URL_FILE=/home/ubuntu/.config/failure-webhook-url
STATE_DIR=/home/ubuntu/.cache/notify-failure
THROTTLE_SECONDS=1800

[ "$SERVICE_RESULT" = "success" ] && exit 0
[ -r "$WEBHOOK_URL_FILE" ] || exit 0

mkdir -p "$STATE_DIR"
STATE_FILE="$STATE_DIR/$1"
NOW=$(date +%s)
LAST_SENT=0
SUPPRESSED=0
[ -r "$STATE_FILE" ] && read -r LAST_SENT SUPPRESSED < "$STATE_FILE"

if [ $((NOW - LAST_SENT)) -lt "$THROTTLE_SECONDS" ]; then
    echo "$LAST_SENT $((SUPPRESSED + 1))" > "$STATE_FILE"
    exit 0
fi

MESSAGE="<@312204139751014400> 💥 \`$1\` stopped: result=\`$SERVICE_RESULT\` ($EXIT_CODE $EXIT_STATUS)"
if [ "$SUPPRESSED" -gt 0 ]; then
    MESSAGE="$MESSAGE, plus $SUPPRESSED more failure(s) since the previous alert"
fi

# The service's last log lines, usually ending with the traceback that killed it. The `ubuntu` user may read
# the journal (`adm` group). journald may still be writing the very last lines, so wait a moment.
sleep 2
LOGS=$(journalctl --unit="$1" --lines=60 --output=cat --no-pager 2>/dev/null)

# Python builds the JSON, since log lines are full of quotes, backslashes and backticks;
# it keeps the newest lines that fit into Discord's 2000 characters per message.
# It prints two payloads, one per line: with 🔄 Restart, 📓 Tail and 📁 Journal buttons, and without them.
# The buttons only work through a webhook LalaBot created (/alertwebhook), which also handles the clicks;
# any other webhook rejects them, and then the alert goes out without buttons.
PAYLOADS=$(python3 -I - "$MESSAGE" "$LOGS" "${1%.service}" <<'EOF'
import json
import sys


def build(logs: str) -> str:
    return f"{message}\n```py\n{logs}\n```" if logs else message


def discord_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2  # Discord counts UTF-16 code units, so an emoji counts twice


message, service = sys.argv[1], sys.argv[3]
logs = sys.argv[2].strip().replace("```", "`\u200b``")  # a zero-width space keeps them from closing the block
while logs and discord_length(build(logs)) > 2000:
    logs = logs.partition("\n")[2]  # drop the oldest line
plain = {"content": build(logs)}
buttons = [
    {"type": 2, "style": 2, "label": label, "emoji": {"name": emoji}, "custom_id": f"alert:{action}:{service}"}
    for action, label, emoji in (("restart", "Restart", "🔄"), ("tail", "Tail", "📓"), ("journal", "Journal", "📁"))
]
print(json.dumps({**plain, "components": [{"type": 1, "components": buttons}]}))
print(json.dumps(plain))
EOF
)
WITH_BUTTONS=$(printf '%s\n' "$PAYLOADS" | sed -n 1p)
PLAIN=$(printf '%s\n' "$PAYLOADS" | sed -n 2p)
[ -n "$PLAIN" ] || PLAIN="{\"content\": \"$MESSAGE\"}"

URL=$(cat "$WEBHOOK_URL_FILE")
post() {
    curl -fsS -m 10 -o /dev/null -H "Content-Type: application/json" -d "$2" "$1"
}
{ [ -n "$WITH_BUTTONS" ] && post "$URL?with_components=true" "$WITH_BUTTONS" 2>/dev/null; } || post "$URL" "$PLAIN" || exit 0
echo "$NOW 0" > "$STATE_FILE"
