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
PAYLOAD=$(python3 -I - "$MESSAGE" "$LOGS" <<'EOF'
import json
import sys


def build(logs: str) -> str:
    return f"{message}\n```py\n{logs}\n```" if logs else message


def discord_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2  # Discord counts UTF-16 code units, so an emoji counts twice


message = sys.argv[1]
logs = sys.argv[2].strip().replace("```", "`\u200b``")  # a zero-width space keeps them from closing the block
while logs and discord_length(build(logs)) > 2000:
    logs = logs.partition("\n")[2]  # drop the oldest line
print(json.dumps({"content": build(logs)}))
EOF
) || PAYLOAD="{\"content\": \"$MESSAGE\"}"

curl -fsS -m 10 -H "Content-Type: application/json" -d "$PAYLOAD" "$(cat "$WEBHOOK_URL_FILE")" || exit 0
echo "$NOW 0" > "$STATE_FILE"
