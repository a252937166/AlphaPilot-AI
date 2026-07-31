#!/bin/zsh

set -euo pipefail

print -u2 "Refusing install: the retired local S2 shard overlaps the final four-exit contract."
exit 1

readonly service_label="${ALPHAPILOT_FINANCIAL_LOCAL_LABEL:-com.alphapilot.s2-financial-local}"
readonly project_dir="${0:A:h:h}"
readonly venv_python="${project_dir}/.venv/bin/python"
readonly runner_script="${project_dir}/scripts/run_financial_backfill.py"
readonly wrapper_script="${project_dir}/scripts/run_financial_local_backfill.py"
readonly template="${project_dir}/config/financial_local.launchagent.template.plist"
readonly target_db="${project_dir}/data/alphapilot.db"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly runtime_dir="${HOME}/Library/Application Support/AlphaPilot-AI/financial-local"
readonly log_dir="${HOME}/Library/Logs/AlphaPilot-AI"
readonly stdout_log="${log_dir}/financial-local.stdout.log"
readonly stderr_log="${log_dir}/financial-local.stderr.log"
readonly launch_domain="gui/$(/usr/bin/id -u)"
readonly service_target="${launch_domain}/${service_label}"

if [[ ! "${service_label}" =~ '^[A-Za-z0-9._-]+$' ]]; then
    print -u2 "Invalid local financial launchd label: ${service_label}"
    exit 1
fi
for required_file in \
    "${venv_python}" \
    "${runner_script}" \
    "${wrapper_script}" \
    "${template}" \
    "${target_db}"; do
    if [[ ! -e "${required_file}" ]]; then
        print -u2 "Required local financial file is missing: ${required_file}"
        exit 1
    fi
done
if /usr/sbin/lsof -nP -iTCP:10030 -sTCP:ESTABLISHED >/dev/null 2>&1; then
    print -u2 "A local BaoStock connection already exists; refusing to start another."
    exit 1
fi
if /usr/bin/pgrep -f "${runner_script}" >/dev/null 2>&1; then
    print -u2 "A local financial backfill runner is already active; refusing a duplicate."
    exit 1
fi

(
    cd "${project_dir}"
    "${venv_python}" - <<'PY'
from alphapilot.core.config import get_settings

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
if settings.scheduler_enabled:
    violations.append(
        "scheduler_enabled=true; local S2 is disabled on the live-product host"
    )
if violations:
    raise SystemExit(
        "Refusing local financial backfill outside the read-only safety state: "
        + ", ".join(violations)
    )
PY
)

/bin/mkdir -p "${launch_agent_file:h}" "${runtime_dir}" "${log_dir}"
/bin/chmod 700 "${runtime_dir}" "${log_dir}"

umask 077
launch_agent_tmp="$(/usr/bin/mktemp "${runtime_dir}/launch-agent.plist.XXXXXX")"
trap '/bin/unlink "${launch_agent_tmp}" 2>/dev/null || true' EXIT INT TERM
"${venv_python}" - \
    "${template}" \
    "${launch_agent_tmp}" \
    "${service_label}" \
    "${venv_python}" \
    "${wrapper_script}" \
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
    wrapper,
    working_directory,
    stdout,
    stderr,
) = sys.argv[1:]
replacements = {
    "__FINANCIAL_LOCAL_LABEL__": label,
    "__FINANCIAL_LOCAL_PYTHON__": python,
    "__FINANCIAL_LOCAL_WRAPPER__": wrapper,
    "__FINANCIAL_LOCAL_WORKING_DIRECTORY__": working_directory,
    "__FINANCIAL_LOCAL_STDOUT_LOG__": stdout,
    "__FINANCIAL_LOCAL_STDERR_LOG__": stderr,
}
rendered = Path(template_path).read_text(encoding="utf-8")
for placeholder, value in replacements.items():
    rendered = rendered.replace(placeholder, escape(value, quote=True))
if "__FINANCIAL_LOCAL_" in rendered:
    raise SystemExit("local financial LaunchAgent template has an unresolved placeholder")
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
print "Installed ${service_label}; >=601121 shard runs now and daily at 00:05 Asia/Shanghai."
