#!/bin/sh
# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
# Ping the healthchecks.io check of the VPS monitoring itself, so a broken monitoring setup gets noticed too.
# Run every 5 minutes by vps-resources.timer, after the other checks. Those are earlier ExecStart= lines,
# so when one of them fails, this doesn't run, the pings stop and healthchecks.io alerts.
# It also makes sure the Discord webhook all the VPS alerts go to still exists, and pings /fail if it doesn't:
# that alert reaches me through healthchecks.io's own notifications, not through the webhook.

HEALTHCHECK_URL_FILE=/home/ubuntu/.config/monitoring-healthcheck-url
WEBHOOK_URL_FILE=/home/ubuntu/.config/failure-webhook-url

[ -r "$HEALTHCHECK_URL_FILE" ] || exit 0
url=$(cat "$HEALTHCHECK_URL_FILE")

fail() {
    curl -fsS -m 10 -o /dev/null --data-raw "$1" "$url/fail"
}

if [ ! -s "$WEBHOOK_URL_FILE" ]; then
    fail "There's no Discord webhook URL in $WEBHOOK_URL_FILE, so the VPS alerts can't be sent."
    exit
fi
# GET on a webhook URL only describes the webhook; 401/404 mean it's deleted or its token changed.
# Any other failure (Discord down, network) can't tell, so it doesn't fail the check.
status=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$(cat "$WEBHOOK_URL_FILE")")
case $status in
    401 | 404) fail "The Discord webhook in $WEBHOOK_URL_FILE is gone (HTTP $status), so the VPS alerts can't be sent." ;;
    *) curl -fsS -m 10 -o /dev/null "$url" ;;
esac
