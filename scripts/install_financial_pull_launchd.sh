#!/bin/zsh

set -euo pipefail

readonly service_label="${ALPHAPILOT_FINANCIAL_PULL_LABEL:-com.alphapilot.s2-financial-pull}"
readonly ssh_target="${ALPHAPILOT_S2_SSH_TARGET:-root@47.93.234.51}"
readonly project_dir="${0:A:h:h}"
readonly venv_python="${project_dir}/.venv/bin/python"
readonly gate_script="${project_dir}/scripts/run_shanghai_daily.py"
readonly pull_script="${project_dir}/scripts/pull_financial_snapshot.py"
readonly template="${project_dir}/config/financial_pull.launchagent.template.plist"
readonly target_db="${project_dir}/data/alphapilot.db"
readonly snapshot_dir="${project_dir}/data/phase3-s2"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly runtime_dir="${HOME}/Library/Application Support/AlphaPilot-AI/financial-pull"
readonly state_file="${runtime_dir}/last-success-shanghai-date"
readonly log_dir="${HOME}/Library/Logs/AlphaPilot-AI"
readonly stdout_log="${log_dir}/financial-pull.stdout.log"
readonly stderr_log="${log_dir}/financial-pull.stderr.log"
readonly launch_domain="gui/$(/usr/bin/id -u)"
readonly service_target="${launch_domain}/${service_label}"

if [[ ! "${service_label}" =~ '^[A-Za-z0-9._-]+$' ]]; then
    print -u2 "Invalid financial pull launchd label: ${service_label}"
    exit 1
fi
if [[ ! "${ssh_target}" =~ '^[A-Za-z0-9._@:-]+$' ]]; then
    print -u2 "Invalid financial pull SSH target: ${ssh_target}"
    exit 1
fi
for required_file in \
    "${venv_python}" \
    "${gate_script}" \
    "${pull_script}" \
    "${template}" \
    "${target_db}"; do
    if [[ ! -e "${required_file}" ]]; then
        print -u2 "Required financial pull file is missing: ${required_file}"
        exit 1
    fi
done

/bin/mkdir -p "${launch_agent_file:h}" "${runtime_dir}" "${log_dir}" "${snapshot_dir}"
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
    "${state_file}" \
    "${pull_script}" \
    "${ssh_target}" \
    "${target_db}" \
    "${snapshot_dir}" \
    "${project_dir}" \
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
    state_file,
    script,
    ssh_target,
    target_db,
    snapshot_dir,
    working_directory,
    stdout,
    stderr,
) = sys.argv[1:]
replacements = {
    "__FINANCIAL_PULL_LABEL__": label,
    "__FINANCIAL_PULL_PYTHON__": python,
    "__FINANCIAL_PULL_GATE__": gate,
    "__FINANCIAL_PULL_STATE_FILE__": state_file,
    "__FINANCIAL_PULL_SCRIPT__": script,
    "__FINANCIAL_PULL_SSH_TARGET__": ssh_target,
    "__FINANCIAL_PULL_TARGET_DB__": target_db,
    "__FINANCIAL_PULL_SNAPSHOT_DIR__": snapshot_dir,
    "__FINANCIAL_PULL_WORKING_DIRECTORY__": working_directory,
    "__FINANCIAL_PULL_STDOUT_LOG__": stdout,
    "__FINANCIAL_PULL_STDERR_LOG__": stderr,
}
rendered = Path(template_path).read_text(encoding="utf-8")
for placeholder, value in replacements.items():
    rendered = rendered.replace(placeholder, escape(value, quote=True))
if "__FINANCIAL_PULL_" in rendered:
    raise SystemExit("financial pull LaunchAgent template contains an unresolved placeholder")
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
print "Installed ${service_label}; it runs once daily at/after 04:10 Asia/Shanghai."
