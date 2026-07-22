#!/bin/zsh

set -euo pipefail

readonly service_label="${ALPHAPILOT_API_LAUNCHD_LABEL:-com.alphapilot.api}"
readonly log_dir="${HOME}/Library/Logs/AlphaPilot-AI"
readonly launch_domain="gui/$(/usr/bin/id -u)"
readonly service_target="${launch_domain}/${service_label}"
readonly host="127.0.0.1"
readonly port="8000"

if [[ ! "${service_label}" =~ '^[A-Za-z0-9._-]+$' ]]; then
    print -u2 "Invalid API launchd label: ${service_label}"
    exit 1
fi

service_pid() {
    /bin/launchctl print "${service_target}" 2>/dev/null \
        | /usr/bin/sed -nE 's/^[[:space:]]*pid = ([0-9]+)$/\1/p' \
        | /usr/bin/head -n 1 || true
}

listener_pids() {
    /usr/sbin/lsof -nP -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true
}

loaded=0
if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    loaded=1
fi

managed_pid="$(service_pid)"
listeners="$(listener_pids)"
if (( loaded )) && [[ -n "${managed_pid}" ]]; then
    while IFS= read -r listener_pid; do
        if [[ -n "${listener_pid}" && "${listener_pid}" == "${managed_pid}" ]]; then
            print "AlphaPilot API is healthy under launchd (${host}:${port}, pid=${managed_pid})."
            print "Logs: ${log_dir}/api.stdout.log and ${log_dir}/api.stderr.log"
            exit 0
        fi
    done <<<"${listeners}"
fi

if (( loaded )); then
    if [[ -n "${listeners}" ]]; then
        print -u2 "AlphaPilot API is loaded (pid=${managed_pid:-none}), but port ${port} belongs to non-managed PID(s) $(print -r -- "${listeners}" | /usr/bin/tr '\n' ' ')."
    else
        print -u2 "AlphaPilot API is loaded but not listening on ${host}:${port} (pid=${managed_pid:-none})."
    fi
    print -u2 "Inspect ${log_dir}/api.stderr.log; no process was modified."
    exit 1
fi

if [[ -n "${listeners}" ]]; then
    print -u2 "AlphaPilot API is not loaded; non-managed PID(s) $(print -r -- "${listeners}" | /usr/bin/tr '\n' ' ') use port ${port} and were left untouched."
else
    print -u2 "AlphaPilot API launchd service is not loaded and port ${port} is free."
fi
exit 1
