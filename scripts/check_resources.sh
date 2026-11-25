#!/bin/sh
# Post a Discord webhook alert when the VPS runs low on disk space or memory.
# Run every 5 minutes by vps-resources.timer. For each problem it posts once when it starts,
# a reminder every REMIND_SECONDS while it lasts, and a "fine again" message when it's over.
# The webhook URL is the crash alerts' one (a single line, kept out of git).

WEBHOOK_URL_FILE=/home/ubuntu/.config/failure-webhook-url
STATE_DIR=/home/ubuntu/.cache/vps-resources
REMIND_SECONDS=21600
DISK_MAX_USED_PERCENT=90
MEMORY_MIN_AVAILABLE_PERCENT=10

[ -r "$WEBHOOK_URL_FILE" ] || exit 0
mkdir -p "$STATE_DIR"
NOW=$(date +%s)

post() {
    curl -fsS -m 10 -H "Content-Type: application/json" -d "{\"content\": \"$1\"}" "$(cat "$WEBHOOK_URL_FILE")"
}

# report NAME IS_LOW DETAILS: alert, remind or announce recovery, remembering the last alert's time in a state file
report() {
    state="$STATE_DIR/$1"
    if [ "$2" = yes ]; then
        last_sent=0
        [ -r "$state" ] && read -r last_sent < "$state"
        if [ $((NOW - last_sent)) -ge "$REMIND_SECONDS" ]; then
            post "<@312204139751014400> ⚠️ The VPS is low on $1: $3" && echo "$NOW" > "$state"
        fi
    elif [ -e "$state" ]; then
        post "✅ The VPS has enough $1 again: $3" && rm -f "$state"
    fi
}

disk_used=$(df -P / | awk 'NR == 2 { sub("%", "", $5); print $5 }')
disk_free=$(df -Ph / | awk 'NR == 2 { print $4 }')
[ "$disk_used" -ge "$DISK_MAX_USED_PERCENT" ] && low=yes || low=no
report "disk space" "$low" "${disk_used}% of / is used, ${disk_free} free"

memory_available=$(awk '/^MemTotal:/ { total = $2 } /^MemAvailable:/ { available = $2 } END { printf "%d", available * 100 / total }' /proc/meminfo)
memory_available_mb=$(awk '/^MemAvailable:/ { printf "%d", $2 / 1024 }' /proc/meminfo)
[ "$memory_available" -le "$MEMORY_MIN_AVAILABLE_PERCENT" ] && low=yes || low=no
report "memory" "$low" "${memory_available}% of RAM is available (${memory_available_mb} MB)"
