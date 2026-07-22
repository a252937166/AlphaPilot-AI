#!/bin/zsh

set -euo pipefail

readonly service_label="${ALPHAPILOT_API_LAUNCHD_LABEL:-com.alphapilot.api}"
readonly project_dir="${0:A:h:h}"
readonly venv_python="${project_dir}/.venv/bin/python"
readonly launch_agent_template="${project_dir}/config/api.launchagent.template.plist"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly runtime_dir="${HOME}/Library/Application Support/AlphaPilot-AI/api"
readonly log_dir="${HOME}/Library/Logs/AlphaPilot-AI"
readonly stdout_log="${log_dir}/api.stdout.log"
readonly stderr_log="${log_dir}/api.stderr.log"
readonly launch_domain="gui/$(/usr/bin/id -u)"
readonly service_target="${launch_domain}/${service_label}"
readonly host="127.0.0.1"
readonly port="8000"

launch_agent_tmp=""
launch_agent_backup=""
installed_plist=0
service_loaded=0
cleanup_ran=0

service_pid() {
    /bin/launchctl print "${service_target}" 2>/dev/null \
        | /usr/bin/sed -nE 's/^[[:space:]]*pid = ([0-9]+)$/\1/p' \
        | /usr/bin/head -n 1 || true
}

listener_pids() {
    /usr/sbin/lsof -nP -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true
}

port_is_listening() {
    /usr/sbin/lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
}

service_owns_port() {
    local managed_pid listener_pid
    managed_pid="$(service_pid)"
    [[ -n "${managed_pid}" ]] || return 1

    while IFS= read -r listener_pid; do
        if [[ -n "${listener_pid}" && "${listener_pid}" == "${managed_pid}" ]]; then
            return 0
        fi
    done <<<"$(listener_pids)"
    return 1
}

cleanup_failed_start() {
    if (( cleanup_ran )); then
        return
    fi
    cleanup_ran=1

    if (( service_loaded )); then
        /bin/launchctl bootout "${service_target}" >/dev/null 2>&1 || true
    fi

    if [[ -n "${launch_agent_tmp}" && -e "${launch_agent_tmp}" ]]; then
        /bin/unlink "${launch_agent_tmp}" 2>/dev/null || true
    fi

    if (( installed_plist )); then
        /bin/unlink "${launch_agent_file}" 2>/dev/null || true
        if [[ -n "${launch_agent_backup}" && -e "${launch_agent_backup}" ]]; then
            /bin/mv "${launch_agent_backup}" "${launch_agent_file}" 2>/dev/null || true
            launch_agent_backup=""
        fi
    fi

    if [[ -n "${launch_agent_backup}" && -e "${launch_agent_backup}" ]]; then
        /bin/unlink "${launch_agent_backup}" 2>/dev/null || true
    fi
}

trap cleanup_failed_start EXIT INT TERM

if [[ ! "${service_label}" =~ '^[A-Za-z0-9._-]+$' ]]; then
    print -u2 "Invalid API launchd label: ${service_label}"
    exit 1
fi

if [[ ! -x "${venv_python}" ]]; then
    print -u2 "AlphaPilot virtualenv Python was not found at: ${venv_python}"
    exit 1
fi

if [[ ! -f "${launch_agent_template}" ]]; then
    print -u2 "AlphaPilot API LaunchAgent template was not found at: ${launch_agent_template}"
    exit 1
fi

if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    for _ in {1..30}; do
        if service_owns_port; then
            print "AlphaPilot API is already running under launchd (${host}:${port}, pid=$(service_pid))."
            trap - EXIT INT TERM
            exit 0
        fi
        if port_is_listening; then
            print -u2 "Port ${port} is held by non-managed PID(s) $(listener_pids | /usr/bin/tr '\n' ' '); the loaded API service was left untouched."
            exit 1
        fi
        sleep 1
    done
    print -u2 "AlphaPilot API is loaded but did not open ${host}:${port}. Run 'make api-status' and inspect ${stderr_log}; use 'make api-restart' to reload it."
    exit 1
fi

if port_is_listening; then
    print -u2 "Port ${port} is already held by PID(s) $(listener_pids | /usr/bin/tr '\n' ' '); refusing to stop or replace a non-managed process."
    exit 1
fi

/bin/mkdir -p "${launch_agent_file:h}" "${runtime_dir}" "${log_dir}"
/bin/chmod 700 "${runtime_dir}" "${log_dir}"

umask 077
launch_agent_tmp="$(/usr/bin/mktemp "${runtime_dir}/api-launch-agent.plist.XXXXXX")"
"${venv_python}" - \
    "${launch_agent_template}" \
    "${launch_agent_tmp}" \
    "${service_label}" \
    "${venv_python}" \
    "${project_dir}" \
    "${stdout_log}" \
    "${stderr_log}" <<'PY'
from __future__ import annotations

from html import escape
from pathlib import Path
import sys

template_path, output_path, label, python, working_directory, stdout, stderr = sys.argv[1:]
replacements = {
    "__API_SERVICE_LABEL__": label,
    "__API_VENV_PYTHON__": python,
    "__API_WORKING_DIRECTORY__": working_directory,
    "__API_STDOUT_LOG__": stdout,
    "__API_STDERR_LOG__": stderr,
}
rendered = Path(template_path).read_text(encoding="utf-8")
for placeholder, value in replacements.items():
    rendered = rendered.replace(placeholder, escape(value, quote=True))
if "__API_" in rendered:
    raise SystemExit("API LaunchAgent template contains an unresolved placeholder")
Path(output_path).write_text(rendered, encoding="utf-8")
PY
/usr/bin/plutil -lint "${launch_agent_tmp}" >/dev/null
/bin/chmod 600 "${launch_agent_tmp}"

if [[ -e "${launch_agent_file}" ]]; then
    launch_agent_backup="$(/usr/bin/mktemp "${runtime_dir}/api-launch-agent.backup.XXXXXX")"
    /bin/cp -p "${launch_agent_file}" "${launch_agent_backup}"
fi
/bin/mv "${launch_agent_tmp}" "${launch_agent_file}"
launch_agent_tmp=""
installed_plist=1

/bin/launchctl bootstrap "${launch_domain}" "${launch_agent_file}"
service_loaded=1

for _ in {1..60}; do
    if service_owns_port; then
        if [[ -n "${launch_agent_backup}" && -e "${launch_agent_backup}" ]]; then
            /bin/unlink "${launch_agent_backup}"
            launch_agent_backup=""
        fi
        print "AlphaPilot API is listening under launchd (${host}:${port}, pid=$(service_pid))."
        trap - EXIT INT TERM
        exit 0
    fi
    if port_is_listening; then
        print -u2 "Port ${port} was claimed by non-managed PID(s) $(listener_pids | /usr/bin/tr '\n' ' ') while the API was starting; no process will be killed."
        exit 1
    fi
    if ! /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
        print -u2 "AlphaPilot API launchd service exited before opening ${host}:${port}. See ${stderr_log}."
        exit 1
    fi
    sleep 1
done

print -u2 "AlphaPilot API did not open ${host}:${port} within 60 seconds. See ${stderr_log}."
exit 1
