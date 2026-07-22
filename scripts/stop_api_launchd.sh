#!/bin/zsh

set -euo pipefail

readonly service_label="${ALPHAPILOT_API_LAUNCHD_LABEL:-com.alphapilot.api}"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly launch_domain="gui/$(/usr/bin/id -u)"
readonly service_target="${launch_domain}/${service_label}"
readonly port="8000"

if [[ ! "${service_label}" =~ '^[A-Za-z0-9._-]+$' ]]; then
    print -u2 "Invalid API launchd label: ${service_label}"
    exit 1
fi

was_loaded=0
if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    was_loaded=1
    /bin/launchctl bootout "${service_target}"
fi

for _ in {1..15}; do
    if ! /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    print -u2 "AlphaPilot API launchd service did not stop within 15 seconds; its plist was retained."
    exit 1
fi

if [[ -e "${launch_agent_file}" ]]; then
    plist_label="$(/usr/bin/plutil -extract Label raw -o - "${launch_agent_file}" 2>/dev/null || true)"
    if [[ "${plist_label}" == "${service_label}" ]]; then
        /bin/unlink "${launch_agent_file}"
    else
        print -u2 "Refusing to remove unexpected plist at ${launch_agent_file}; launchd service is stopped."
        exit 1
    fi
fi

if /usr/sbin/lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    listener_pids="$(/usr/sbin/lsof -nP -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null | /usr/bin/tr '\n' ' ')"
    print -u2 "AlphaPilot launchd service is stopped, but non-managed PID(s) ${listener_pids} still use port ${port}; they were left untouched."
fi

if (( was_loaded )); then
    print "AlphaPilot API launchd service stopped."
else
    print "AlphaPilot API launchd service was not running; any matching stale plist was cleaned."
fi
