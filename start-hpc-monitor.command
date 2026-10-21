#!/bin/bash
set -euo pipefail

MONITOR_URL="http://127.0.0.1:8765"
SERVICE="gui/$(id -u)/com.zhylar.scuthpc-console"
PLIST="$HOME/Library/LaunchAgents/com.zhylar.scuthpc-console.plist"

is_ready() {
    local page
    page=$(/usr/bin/curl --fail --silent --max-time 2 "$MONITOR_URL/") || return 1
    [[ "$page" == *"<title>SCUTHPC Console</title>"* ]]
}

if ! is_ready; then
    if [[ ! -f "$PLIST" ]]; then
        printf 'Missing monitor service configuration: %s\n' "$PLIST" >&2
        exit 1
    fi

    if /bin/launchctl print "$SERVICE" >/dev/null 2>&1; then
        /bin/launchctl kickstart "$SERVICE"
    else
        /bin/launchctl bootstrap "gui/$(id -u)" "$PLIST"
    fi

    for ((attempt = 0; attempt < 20; attempt++)); do
        if is_ready; then
            break
        fi
        sleep 1
    done
fi

if ! is_ready; then
    printf 'Monitor did not become ready at %s\n' "$MONITOR_URL" >&2
    printf 'Check console.error.log in the SCUTHPC-Console directory.\n' >&2
    exit 1
fi

printf 'SCUTHPC Console: %s\n' "$MONITOR_URL"
/usr/bin/open "$MONITOR_URL"
