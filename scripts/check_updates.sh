#!/bin/sh
# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
# Post a Discord webhook alert when security updates or a reboot have been waiting for too long.
# unattended-upgrades installs security updates daily, so ones still pending after a week mean it's broken
# (it once failed silently for years). It doesn't reboot by itself, so a new kernel waits for a manual reboot.
# Run every 5 minutes by vps-resources.timer; reminds every REMIND_SECONDS while a problem lasts.

WEBHOOK_URL_FILE=/home/ubuntu/.config/failure-webhook-url
STATE_DIR=/home/ubuntu/.cache/vps-updates
REBOOT_REQUIRED_FILE=/var/run/reboot-required
MAX_WAIT_SECONDS=604800
REMIND_SECONDS=86400

[ -r "$WEBHOOK_URL_FILE" ] || exit 0
mkdir -p "$STATE_DIR"
NOW=$(date +%s)

post() {
    curl -fsS -m 10 -H "Content-Type: application/json" -d "{\"content\": \"$1\"}" "$(cat "$WEBHOOK_URL_FILE")"
}

# report NAME WAITING_SINCE ALERT RESOLVED: post ALERT (and remind) once NAME has waited for MAX_WAIT_SECONDS,
# and RESOLVED once it's over, if it was alerted. WAITING_SINCE is a unix time, or empty when nothing waits.
report() {
    state="$STATE_DIR/$1"
    if [ -n "$2" ]; then
        [ $((NOW - $2)) -ge "$MAX_WAIT_SECONDS" ] || return 0
        last_sent=0
        [ -r "$state" ] && read -r last_sent < "$state"
        if [ $((NOW - last_sent)) -ge "$REMIND_SECONDS" ]; then
            post "<@312204139751014400> ⚠️ $3 (for $(((NOW - $2) / 86400)) days)" && echo "$NOW" > "$state"
        fi
    elif [ -e "$state" ]; then
        post "$4" && rm -f "$state"
    fi
}

# Security updates: the package lists are refreshed daily by apt-daily.timer, so the count is at most a day old.
# A state file remembers since when some have been pending.
pending=$(apt list --upgradable 2>/dev/null | grep -c -- '-security')
since_file="$STATE_DIR/security-updates-since"
if [ "$pending" -gt 0 ]; then
    [ -r "$since_file" ] || echo "$NOW" > "$since_file"
    read -r since < "$since_file"
else
    rm -f "$since_file"
    since=
fi
report "security-updates" "$since" \
    "$pending security update(s) on the VPS aren't installed: is unattended-upgrades broken?" \
    "✅ The VPS's security updates are installed."

# Reboot: the file appears when an installed update (usually a new kernel) needs one, and is gone after it.
since=
[ -e "$REBOOT_REQUIRED_FILE" ] && since=$(stat -c %Y "$REBOOT_REQUIRED_FILE")
report "reboot" "$since" \
    "The VPS needs a reboot to finish installing updates: \`sudo reboot\`" \
    "✅ The VPS is rebooted, the updates are in use."
