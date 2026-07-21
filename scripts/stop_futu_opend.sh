#!/bin/zsh

set -euo pipefail

readonly service_label="${FUTU_LAUNCHD_LABEL:-com.alphapilot.futu.opend}"
readonly runtime_dir="${FUTU_RUNTIME_DIR:-/Users/ouyangduning/.alphapilot-futu}"
readonly config_file="${runtime_dir}/FutuOpenD.xml"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly launch_domain="gui/$(/usr/bin/id -u)"
readonly service_target="${launch_domain}/${service_label}"

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
    print -u2 "Futu OpenD launchd service did not stop within 15 seconds."
    exit 1
fi

for runtime_artifact in \
    "${runtime_dir}/futu-opend.pid" \
    "${runtime_dir}/stdin-keeper.pid" \
    "${runtime_dir}/stdin.fifo" \
    "${config_file}" \
    "${launch_agent_file}"; do
    if [[ -e "${runtime_artifact}" || -p "${runtime_artifact}" ]]; then
        /bin/unlink "${runtime_artifact}"
    fi
done

if (( was_loaded )); then
    print "Futu OpenD stopped."
else
    print "Futu OpenD was not running; cleaned stale runtime files."
fi
