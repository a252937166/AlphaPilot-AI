#!/bin/zsh

set -euo pipefail
umask 077

readonly service_label="${ALPHAPILOT_SCHEDULER_LAUNCHD_LABEL:-com.alphapilot.scheduler}"
readonly api_service_label="${ALPHAPILOT_API_LAUNCHD_LABEL:-com.alphapilot.api}"
readonly project_dir="${0:A:h:h}"
readonly venv_python="${project_dir}/.venv/bin/python"
readonly runner="${project_dir}/src/alphapilot/scheduler_main.py"
readonly database="${project_dir}/data/alphapilot.db"
readonly template="${project_dir}/config/scheduler.launchagent.template.plist"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly api_launch_agent_file="${HOME}/Library/LaunchAgents/${api_service_label}.plist"
readonly runtime_dir="${HOME}/Library/Application Support/AlphaPilot-AI/scheduler"
readonly log_dir="${HOME}/Library/Logs/AlphaPilot-AI"
readonly stdout_log="${log_dir}/scheduler.stdout.log"
readonly stderr_log="${log_dir}/scheduler.stderr.log"
readonly label_pattern='^[A-Za-z0-9._-]+$'
current_uid="$(/usr/bin/id -u)"
readonly current_uid
readonly service_domain="gui/${current_uid}"
readonly service_target="${service_domain}/${service_label}"

if [[ ! "${service_label}" =~ ${label_pattern} ]]; then
    print -u2 "Invalid scheduler launchd label: ${service_label}"
    exit 1
fi
if [[ ! -x "${venv_python}" || ! -f "${runner}" || ! -f "${template}" ]]; then
    print -u2 "Scheduler runtime, runner, or template is missing."
    exit 1
fi
if [[ ! -f "${database}" ]]; then
    print -u2 "Scheduler source database is missing: ${database}"
    exit 1
fi
if [[ ! -f "${api_launch_agent_file}" ]]; then
    print -u2 "API LaunchAgent plist is missing; refusing a potentially double scheduler."
    exit 1
fi
api_scheduler_enabled="$(/usr/bin/plutil \
    -extract EnvironmentVariables.ALPHAPILOT_SCHEDULER_ENABLED \
    raw -o - "${api_launch_agent_file}" 2>/dev/null || true)"
if [[ "${api_scheduler_enabled}" != "false" ]]; then
    print -u2 "API LaunchAgent does not explicitly disable its embedded scheduler."
    exit 1
fi
if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    if [[ ! -f "${launch_agent_file}" ]]; then
        print -u2 "Refusing update: loaded scheduler has no recoverable plist."
        exit 1
    fi
    scheduler_pid="$(/bin/launchctl print "${service_target}" 2>/dev/null \
        | /usr/bin/sed -nE 's/^[[:space:]]*pid = ([0-9]+)$/\1/p' \
        | /usr/bin/head -n 1 || true)"
    if [[ -n "${scheduler_pid}" ]]; then
        print "AlphaPilot scheduler is already running under launchd (pid=${scheduler_pid})."
        exit 0
    fi
    print -u2 "AlphaPilot scheduler is loaded without a running PID; inspect ${stderr_log}."
    exit 1
fi

running_jobs="$("${venv_python}" - "${database}" <<'PY'
from pathlib import Path
import sqlite3
import sys

database = Path(sys.argv[1]).resolve()
with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
    connection.execute("PRAGMA query_only=ON")
    print(connection.execute(
        "SELECT COUNT(*) FROM job_runs WHERE status='running'"
    ).fetchone()[0])
PY
)"
if [[ "${running_jobs}" != "0" ]]; then
    print -u2 "Refusing scheduler start while ${running_jobs} JobRun row(s) are running."
    exit 1
fi

/bin/mkdir -p "${launch_agent_file:h}" "${runtime_dir}" "${log_dir}"
/bin/chmod 700 "${runtime_dir}" "${log_dir}"
temporary_plist="$(/usr/bin/mktemp "${runtime_dir}/scheduler.plist.XXXXXX")"
previous_plist=""
replacement_started=false
installation_complete=false

# shellcheck disable=SC2329
cleanup() {
    exit_code=$?
    set +e
    if [[ "${replacement_started}" == true && "${installation_complete}" != true ]]; then
        /bin/launchctl bootout "${service_target}" >/dev/null 2>&1
        if [[ -n "${previous_plist:-}" && -e "${previous_plist}" ]]; then
            /bin/cp "${previous_plist}" "${launch_agent_file}"
            /bin/chmod 600 "${launch_agent_file}"
        else
            /bin/unlink "${launch_agent_file}" 2>/dev/null
        fi
    fi
    if [[ -n "${temporary_plist:-}" && -e "${temporary_plist}" ]]; then
        /bin/unlink "${temporary_plist}"
    fi
    if [[ -n "${previous_plist:-}" && -e "${previous_plist}" ]]; then
        /bin/unlink "${previous_plist}"
    fi
    return "${exit_code}"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

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
    "__SCHEDULER_SERVICE_LABEL__": label,
    "__SCHEDULER_VENV_PYTHON__": python,
    "__SCHEDULER_WORKING_DIRECTORY__": project,
    "__SCHEDULER_STDOUT_LOG__": stdout,
    "__SCHEDULER_STDERR_LOG__": stderr,
}.items():
    rendered = rendered.replace(placeholder, escape(value, quote=True))
if "__SCHEDULER_" in rendered:
    raise SystemExit("scheduler template contains an unresolved placeholder")
Path(output).write_text(rendered, encoding="utf-8")
PY

/usr/bin/plutil -lint "${temporary_plist}" >/dev/null
/bin/chmod 600 "${temporary_plist}"
if [[ -e "${launch_agent_file}" ]]; then
    installed_label="$(/usr/bin/plutil -extract Label raw -o - \
        "${launch_agent_file}" 2>/dev/null || true)"
    if [[ "${installed_label}" != "${service_label}" ]]; then
        print -u2 "Refusing to replace unexpected plist: ${launch_agent_file}"
        exit 1
    fi
    previous_plist="$(/usr/bin/mktemp "${runtime_dir}/scheduler.previous.XXXXXX")"
    /bin/cp "${launch_agent_file}" "${previous_plist}"
    /bin/chmod 600 "${previous_plist}"
fi

replacement_started=true
/bin/mv "${temporary_plist}" "${launch_agent_file}"
temporary_plist=""
/bin/launchctl bootstrap "${service_domain}" "${launch_agent_file}"

stable_checks=0
for _ in {1..30}; do
    scheduler_pid="$(/bin/launchctl print "${service_target}" 2>/dev/null \
        | /usr/bin/sed -nE 's/^[[:space:]]*pid = ([0-9]+)$/\1/p' \
        | /usr/bin/head -n 1 || true)"
    if [[ -n "${scheduler_pid}" ]]; then
        (( stable_checks += 1 ))
        if (( stable_checks >= 3 )); then
            installation_complete=true
            print "AlphaPilot scheduler is running under launchd (pid=${scheduler_pid})."
            exit 0
        fi
    else
        stable_checks=0
    fi
    sleep 1
done

print -u2 "AlphaPilot scheduler did not remain running; inspect ${stderr_log}."
exit 1
