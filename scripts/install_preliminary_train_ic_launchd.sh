#!/bin/zsh

set -euo pipefail

readonly service_label="${ALPHAPILOT_TRAIN_IC_LABEL:-com.alphapilot.p3-preliminary-train-ic}"
readonly start_date="${ALPHAPILOT_TRAIN_IC_START_DATE:-2019-01-02}"
readonly end_date="${ALPHAPILOT_TRAIN_IC_END_DATE:-2026-07-24}"
readonly project_dir="${0:A:h:h}"
readonly venv_python="${project_dir}/.venv/bin/python"
readonly runner_script="${project_dir}/scripts/run_preliminary_train_ic.py"
readonly template="${project_dir}/config/preliminary_train_ic.launchagent.template.plist"
readonly runtime_dir="${HOME}/Library/Application Support/AlphaPilot-AI/p3-train-preview"
readonly log_dir="${HOME}/Library/Logs/AlphaPilot-AI"
readonly launch_agent_file="${runtime_dir}/${service_label}.plist"
readonly stdout_log="${log_dir}/p3-train-preview.stdout.log"
readonly stderr_log="${log_dir}/p3-train-preview.stderr.log"
readonly launch_domain="gui/$(/usr/bin/id -u)"
readonly service_target="${launch_domain}/${service_label}"

if [[ ! "${service_label}" =~ '^[A-Za-z0-9._-]+$' ]]; then
    print -u2 "Invalid preliminary train IC launchd label: ${service_label}"
    exit 1
fi
for required_file in "${venv_python}" "${runner_script}" "${template}"; do
    if [[ ! -e "${required_file}" ]]; then
        print -u2 "Required preliminary train IC file is missing: ${required_file}"
        exit 1
    fi
done

(
    cd "${project_dir}"
    "${venv_python}" - "${start_date}" "${end_date}" <<'PY'
from datetime import date
import sys

from alphapilot.core.config import get_settings

start = date.fromisoformat(sys.argv[1])
end = date.fromisoformat(sys.argv[2])
if end < start:
    raise SystemExit("preliminary train IC end date is earlier than start date")
settings = get_settings()
violations = []
if settings.trading_mode != "research":
    violations.append(f"trading_mode={settings.trading_mode!r}")
if settings.live_trading_enabled:
    violations.append("live_trading_enabled=true")
if settings.paper_auto_trading_enabled:
    violations.append("paper_auto_trading_enabled=true")
if settings.futu_enable_account_mutation:
    violations.append("futu_enable_account_mutation=true")
if violations:
    raise SystemExit(
        "Refusing preliminary train IC outside the read-only safety state: "
        + ", ".join(violations)
    )
PY
)

/bin/mkdir -p "${runtime_dir}" "${log_dir}"
/bin/chmod 700 "${runtime_dir}" "${log_dir}"

umask 077
launch_agent_tmp="$(/usr/bin/mktemp "${runtime_dir}/launch-agent.plist.XXXXXX")"
trap '/bin/unlink "${launch_agent_tmp}" 2>/dev/null || true' EXIT INT TERM
"${venv_python}" - \
    "${template}" \
    "${launch_agent_tmp}" \
    "${service_label}" \
    "${venv_python}" \
    "${runner_script}" \
    "${start_date}" \
    "${end_date}" \
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
    script,
    start_date,
    end_date,
    working_directory,
    stdout,
    stderr,
) = sys.argv[1:]
replacements = {
    "__TRAIN_IC_LABEL__": label,
    "__TRAIN_IC_PYTHON__": python,
    "__TRAIN_IC_SCRIPT__": script,
    "__TRAIN_IC_START_DATE__": start_date,
    "__TRAIN_IC_END_DATE__": end_date,
    "__TRAIN_IC_WORKING_DIRECTORY__": working_directory,
    "__TRAIN_IC_STDOUT_LOG__": stdout,
    "__TRAIN_IC_STDERR_LOG__": stderr,
}
rendered = Path(template_path).read_text(encoding="utf-8")
for placeholder, value in replacements.items():
    rendered = rendered.replace(placeholder, escape(value, quote=True))
if "__TRAIN_IC_" in rendered:
    raise SystemExit("preliminary train IC template has an unresolved placeholder")
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
print "Started ${service_label}; one-shot train-only IC logs: ${stderr_log}"
