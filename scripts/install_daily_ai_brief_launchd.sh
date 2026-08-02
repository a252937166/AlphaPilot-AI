#!/bin/zsh

set -euo pipefail
umask 077

readonly service_label="${ALPHAPILOT_DAILY_AI_BRIEF_LABEL:-com.alphapilot.daily-ai-brief}"
readonly project_dir="${0:A:h:h}"
readonly venv_python="${project_dir}/.venv/bin/python"
readonly template="${project_dir}/config/daily_ai_brief.launchagent.template.plist"
readonly runner="${project_dir}/scripts/run_daily_ai_brief.py"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly log_dir="${HOME}/Library/Logs/AlphaPilot-AI"
readonly stdout_log="${log_dir}/daily-ai-brief.stdout.log"
readonly stderr_log="${log_dir}/daily-ai-brief.stderr.log"
readonly label_pattern='^[A-Za-z0-9._-]+$'
current_uid="$(/usr/bin/id -u)"
readonly current_uid
readonly service_target="gui/${current_uid}/${service_label}"
readonly service_domain="gui/${current_uid}"

if [[ ! "${service_label}" =~ ${label_pattern} ]]; then
    print -u2 "Invalid daily-ai-brief launchd label: ${service_label}"
    exit 1
fi
if [[ ! -x "${venv_python}" || ! -f "${template}" || ! -f "${runner}" ]]; then
    print -u2 "Daily-ai-brief runtime, template, or runner is missing."
    exit 1
fi
if [[ -e "${launch_agent_file}" ]]; then
    installed_label="$(/usr/bin/plutil -extract Label raw -o - "${launch_agent_file}" 2>/dev/null || true)"
    if [[ "${installed_label}" != "${service_label}" ]]; then
        print -u2 "Refusing to replace unexpected plist: ${launch_agent_file}"
        exit 1
    fi
fi

/bin/mkdir -p "${launch_agent_file:h}" "${log_dir}"
/bin/chmod 700 "${log_dir}"
temporary_plist="$(/usr/bin/mktemp "${TMPDIR:-/tmp}/daily-ai-brief.plist.XXXXXX")"
trap '/bin/rm -f "${temporary_plist}"' EXIT

"${venv_python}" - \
    "${template}" \
    "${temporary_plist}" \
    "${service_label}" \
    "${venv_python}" \
    "${project_dir}" \
    "${stdout_log}" \
    "${stderr_log}" <<'PY'
from html import escape
from pathlib import Path
import sys

template, output, label, python, project, stdout, stderr = sys.argv[1:]
rendered = Path(template).read_text(encoding="utf-8")
for placeholder, value in {
    "__BRIEF_SERVICE_LABEL__": label,
    "__BRIEF_VENV_PYTHON__": python,
    "__BRIEF_PROJECT_DIR__": project,
    "__BRIEF_STDOUT_LOG__": stdout,
    "__BRIEF_STDERR_LOG__": stderr,
}.items():
    rendered = rendered.replace(placeholder, escape(value, quote=True))
if "__BRIEF_" in rendered:
    raise SystemExit("daily-ai-brief template contains an unresolved placeholder")
Path(output).write_text(rendered, encoding="utf-8")
PY

/usr/bin/plutil -lint "${temporary_plist}" >/dev/null
/bin/chmod 600 "${temporary_plist}"

if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    /bin/launchctl bootout "${service_target}"
fi
/bin/cp "${temporary_plist}" "${launch_agent_file}"
/bin/chmod 600 "${launch_agent_file}"
/bin/launchctl bootstrap "${service_domain}" "${launch_agent_file}"

if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    print "AlphaPilot daily AI brief is scheduled (weekdays 21:07 local)."
else
    print -u2 "Daily-ai-brief service failed to load; inspect ${stderr_log}."
    exit 1
fi
