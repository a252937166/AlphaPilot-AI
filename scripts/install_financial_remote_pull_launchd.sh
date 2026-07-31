#!/bin/zsh

set -euo pipefail

: "${ALPHAPILOT_REMOTE_PULL_LABEL:?ALPHAPILOT_REMOTE_PULL_LABEL is required}"
: "${ALPHAPILOT_REMOTE_PULL_SSH_TARGET:?ALPHAPILOT_REMOTE_PULL_SSH_TARGET is required}"
: "${ALPHAPILOT_REMOTE_PULL_SSH_PORT:?ALPHAPILOT_REMOTE_PULL_SSH_PORT is required}"
: "${ALPHAPILOT_REMOTE_PULL_ROOT:?ALPHAPILOT_REMOTE_PULL_ROOT is required}"
: "${ALPHAPILOT_REMOTE_PULL_AT:?ALPHAPILOT_REMOTE_PULL_AT is required}"
: "${ALPHAPILOT_REMOTE_PULL_WAKE_MINUTE:?ALPHAPILOT_REMOTE_PULL_WAKE_MINUTE is required}"
: "${ALPHAPILOT_REMOTE_PULL_SNAPSHOT_NAME:?ALPHAPILOT_REMOTE_PULL_SNAPSHOT_NAME is required}"

readonly service_label="${ALPHAPILOT_REMOTE_PULL_LABEL}"
readonly ssh_target="${ALPHAPILOT_REMOTE_PULL_SSH_TARGET}"
readonly ssh_port="${ALPHAPILOT_REMOTE_PULL_SSH_PORT}"
readonly remote_root="${ALPHAPILOT_REMOTE_PULL_ROOT}"
readonly pull_at="${ALPHAPILOT_REMOTE_PULL_AT}"
readonly wake_minute="${ALPHAPILOT_REMOTE_PULL_WAKE_MINUTE}"
readonly snapshot_name="${ALPHAPILOT_REMOTE_PULL_SNAPSHOT_NAME}"
readonly project_dir="${0:A:h:h}"
readonly venv_python="${project_dir}/.venv/bin/python"
readonly gate_script="${project_dir}/scripts/run_shanghai_daily.py"
readonly pull_script="${project_dir}/scripts/pull_financial_snapshot.py"
readonly template="${project_dir}/config/financial_remote_pull.launchagent.template.plist"
readonly target_db="${project_dir}/data/alphapilot.db"
readonly snapshot_dir="${project_dir}/data/${snapshot_name}"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly runtime_dir="${HOME}/Library/Application Support/AlphaPilot-AI/${service_label}"
readonly state_file="${runtime_dir}/last-success-shanghai-date"
readonly log_dir="${HOME}/Library/Logs/AlphaPilot-AI"
readonly stdout_log="${log_dir}/${service_label}.stdout.log"
readonly stderr_log="${log_dir}/${service_label}.stderr.log"
readonly launch_domain="gui/$(/usr/bin/id -u)"
readonly service_target="${launch_domain}/${service_label}"
readonly remote_exporter="${remote_root}/export-financial-snapshot.sh"

if [[ ! "${service_label}" =~ '^[A-Za-z0-9._-]+$' ]]; then
    print -u2 "Invalid remote financial pull label"
    exit 1
fi
if [[ ! "${ssh_target}" =~ '^[A-Za-z0-9._@:-]+$' ]]; then
    print -u2 "Invalid remote financial pull SSH target"
    exit 1
fi
if [[ ! "${ssh_port}" =~ '^[0-9]+$' ]] || (( ssh_port < 1 || ssh_port > 65535 )); then
    print -u2 "Invalid remote financial pull SSH port"
    exit 1
fi
if [[ ! "${remote_root}" =~ '^/opt/alphapilot-s2-[A-Za-z0-9-]+$' ]]; then
    print -u2 "Invalid remote financial pull root"
    exit 1
fi
if [[ ! "${pull_at}" =~ '^[0-2][0-9]:[0-5][0-9]$' ]]; then
    print -u2 "Invalid remote financial pull Shanghai time"
    exit 1
fi
if [[ ! "${wake_minute}" =~ '^[0-9]{1,2}$' ]] || (( wake_minute < 0 || wake_minute > 59 )); then
    print -u2 "Invalid remote financial pull wake minute"
    exit 1
fi
if [[ ! "${snapshot_name}" =~ '^phase3-s2-[A-Za-z0-9-]+$' ]]; then
    print -u2 "Invalid remote financial pull snapshot name"
    exit 1
fi
for required_file in \
    "${venv_python}" \
    "${gate_script}" \
    "${pull_script}" \
    "${template}" \
    "${target_db}"; do
    if [[ ! -e "${required_file}" ]]; then
        print -u2 "Required remote financial pull file is missing: ${required_file}"
        exit 1
    fi
done

/bin/mkdir -p \
    "${launch_agent_file:h}" \
    "${runtime_dir}" \
    "${log_dir}" \
    "${snapshot_dir}"
/bin/chmod 700 "${runtime_dir}" "${log_dir}"

umask 077
launch_agent_tmp="$(/usr/bin/mktemp "${runtime_dir}/launch-agent.plist.XXXXXX")"
trap '/bin/unlink "${launch_agent_tmp}" 2>/dev/null || true' EXIT INT TERM
"${venv_python}" - \
    "${template}" \
    "${launch_agent_tmp}" \
    "${service_label}" \
    "${venv_python}" \
    "${gate_script}" \
    "${pull_at}" \
    "${state_file}" \
    "${pull_script}" \
    "${ssh_target}" \
    "${ssh_port}" \
    "${remote_root}" \
    "${remote_exporter}" \
    "${target_db}" \
    "${snapshot_dir}" \
    "${project_dir}" \
    "${wake_minute}" \
    "${stdout_log}" \
    "${stderr_log}" <<'PY'
from __future__ import annotations

from html import escape
from pathlib import Path
import sys

(
    template_path,
    output_path,
    label,
    python,
    gate,
    pull_at,
    state_file,
    script,
    ssh_target,
    ssh_port,
    remote_root,
    remote_exporter,
    target_db,
    snapshot_dir,
    working_directory,
    wake_minute,
    stdout,
    stderr,
) = sys.argv[1:]
replacements = {
    "__REMOTE_PULL_LABEL__": label,
    "__REMOTE_PULL_PYTHON__": python,
    "__REMOTE_PULL_GATE__": gate,
    "__REMOTE_PULL_AT__": pull_at,
    "__REMOTE_PULL_STATE_FILE__": state_file,
    "__REMOTE_PULL_SCRIPT__": script,
    "__REMOTE_PULL_SSH_TARGET__": ssh_target,
    "__REMOTE_PULL_SSH_PORT__": ssh_port,
    "__REMOTE_PULL_ROOT__": remote_root,
    "__REMOTE_PULL_EXPORTER__": remote_exporter,
    "__REMOTE_PULL_TARGET_DB__": target_db,
    "__REMOTE_PULL_SNAPSHOT_DIR__": snapshot_dir,
    "__REMOTE_PULL_WORKING_DIRECTORY__": working_directory,
    "__REMOTE_PULL_WAKE_MINUTE__": wake_minute,
    "__REMOTE_PULL_STDOUT_LOG__": stdout,
    "__REMOTE_PULL_STDERR_LOG__": stderr,
}
rendered = Path(template_path).read_text(encoding="utf-8")
for placeholder, value in replacements.items():
    rendered = rendered.replace(placeholder, escape(value, quote=True))
if "__REMOTE_PULL_" in rendered:
    raise SystemExit("remote financial pull template has an unresolved placeholder")
Path(output_path).write_text(rendered, encoding="utf-8")
PY
/usr/bin/plutil -lint "${launch_agent_tmp}" >/dev/null
/bin/chmod 600 "${launch_agent_tmp}"

if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    /bin/launchctl bootout "${service_target}"
fi
/bin/mv "${launch_agent_tmp}" "${launch_agent_file}"
launch_agent_tmp=""
/bin/launchctl bootstrap "${launch_domain}" "${launch_agent_file}"

trap - EXIT INT TERM
print "Installed ${service_label}; it runs once daily at/after ${pull_at} Asia/Shanghai."
