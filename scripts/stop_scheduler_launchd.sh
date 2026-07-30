#!/bin/zsh

set -euo pipefail

readonly service_label="${ALPHAPILOT_SCHEDULER_LAUNCHD_LABEL:-com.alphapilot.scheduler}"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly label_pattern='^[A-Za-z0-9._-]+$'
current_uid="$(/usr/bin/id -u)"
readonly current_uid
readonly service_target="gui/${current_uid}/${service_label}"

if [[ ! "${service_label}" =~ ${label_pattern} ]]; then
    print -u2 "Invalid scheduler launchd label: ${service_label}"
    exit 1
fi

was_loaded=false
if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    was_loaded=true
    /bin/launchctl bootout "${service_target}"
fi
for _ in {1..60}; do
    if ! /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    print -u2 "Scheduler did not stop cleanly; its plist was retained."
    exit 1
fi

if [[ -e "${launch_agent_file}" ]]; then
    installed_label="$(/usr/bin/plutil -extract Label raw -o - \
        "${launch_agent_file}" 2>/dev/null || true)"
    if [[ "${installed_label}" != "${service_label}" ]]; then
        print -u2 "Refusing to remove unexpected plist: ${launch_agent_file}"
        exit 1
    fi
    /bin/unlink "${launch_agent_file}"
fi

if [[ "${was_loaded}" == true ]]; then
    print "AlphaPilot scheduler launchd service stopped."
else
    print "AlphaPilot scheduler was not loaded; matching stale plist was cleaned."
fi
