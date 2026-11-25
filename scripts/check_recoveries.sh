#!/bin/sh
# Post "back up" to the crash alerts' Discord webhook once a service that crashed has been running steadily again,
# with how many more failures there were after the last crash alert (the ones the alert throttling swallowed).
# notify_failure.sh rewrites $STATE_DIR/<unit> on every crash (alerted or throttled), so that file's modification time
# is the time of the last crash. Run every 5 minutes by vps-resources.service.

WEBHOOK_URL_FILE=/home/ubuntu/.config/failure-webhook-url
STATE_DIR=/home/ubuntu/.cache/notify-failure
ANNOUNCED_DIR=/home/ubuntu/.cache/notify-recovery
# Only announce a recovery after this much uptime, so a crash loop doesn't post "back up" on every restart.
STABLE_SECONDS=300

[ -r "$WEBHOOK_URL_FILE" ] || exit 0
[ -d "$STATE_DIR" ] || exit 0
NOW=$(date +%s)

# The very first run only marks the crashes that happened before this script existed as announced,
# instead of posting a "back up" message for each of them.
if [ ! -d "$ANNOUNCED_DIR" ]; then
    mkdir -p "$ANNOUNCED_DIR"
    for state in "$STATE_DIR"/*.service; do
        [ -e "$state" ] && stat -c %Y "$state" > "$ANNOUNCED_DIR/$(basename "$state")"
    done
    exit 0
fi

for state in "$STATE_DIR"/*.service; do
    [ -e "$state" ] || continue
    unit=$(basename "$state")
    last_crash=$(stat -c %Y "$state")
    announced=0
    [ -r "$ANNOUNCED_DIR/$unit" ] && read -r announced < "$ANNOUNCED_DIR/$unit"
    # the last crash is already announced as recovered
    [ "$last_crash" -gt "$announced" ] || continue
    [ "$(systemctl is-active "$unit")" = active ] || continue
    active_since=$(date -d "$(systemctl show -p ActiveEnterTimestamp --value "$unit")" +%s 2>/dev/null) || continue
    # still the run that crashed, or not up long enough yet
    [ "$active_since" -gt "$last_crash" ] && [ $((NOW - active_since)) -ge "$STABLE_SECONDS" ] || continue

    last_sent=0
    suppressed=0
    read -r last_sent suppressed < "$state"
    message="✅ \`$unit\` is back up, running for $(((NOW - active_since) / 60)) min"
    if [ "${suppressed:-0}" -gt 0 ]; then
        message="$message, after $suppressed more failure(s) since the last alert"
    fi
    curl -fsS -m 10 -H "Content-Type: application/json" -d "{\"content\": \"$message\"}" \
        "$(cat "$WEBHOOK_URL_FILE")" || continue

    # Those failures are reported now, so the next crash alert shouldn't count them again; the throttle stays as is.
    echo "${last_sent:-0} 0" > "$state"
    stat -c %Y "$state" > "$ANNOUNCED_DIR/$unit"
done
