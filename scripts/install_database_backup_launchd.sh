#!/bin/zsh

set -euo pipefail
umask 077

readonly service_label="${ALPHAPILOT_DATABASE_BACKUP_LABEL:-com.alphapilot.database-backup}"
readonly project_dir="${0:A:h:h}"
readonly venv_python="${project_dir}/.venv/bin/python"
readonly template="${project_dir}/config/database_backup.launchagent.template.plist"
readonly runner="${project_dir}/scripts/run_database_backup_daily.py"
readonly database="${project_dir}/data/alphapilot.db"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly runtime_dir="${HOME}/Library/Application Support/AlphaPilot-AI/database-backup"
readonly log_dir="${HOME}/Library/Logs/AlphaPilot-AI"
readonly stdout_log="${log_dir}/database-backup.stdout.log"
readonly stderr_log="${log_dir}/database-backup.stderr.log"
readonly label_pattern='^[A-Za-z0-9._-]+$'
current_uid="$(/usr/bin/id -u)"
readonly current_uid
readonly service_target="gui/${current_uid}/${service_label}"
readonly service_domain="gui/${current_uid}"

if [[ ! "${service_label}" =~ ${label_pattern} ]]; then
    print -u2 "Invalid database-backup launchd label: ${service_label}"
    exit 1
fi
if [[ ! -x "${venv_python}" || ! -f "${template}" || ! -x "${runner}" ]]; then
    print -u2 "Database-backup runtime, template, or runner is missing."
    exit 1
fi
if [[ ! -f "${database}" ]]; then
    print -u2 "Database-backup source database is missing: ${database}"
    exit 1
fi
service_is_loaded=false
if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    service_is_loaded=true
    if [[ ! -e "${launch_agent_file}" ]]; then
        print -u2 "Refusing update: loaded service has no recoverable plist."
        exit 1
    fi
fi
if [[ -e "${launch_agent_file}" ]]; then
    installed_label="$(/usr/bin/plutil -extract Label raw -o - "${launch_agent_file}" 2>/dev/null || true)"
    if [[ "${installed_label}" != "${service_label}" ]]; then
        print -u2 "Refusing to replace unexpected plist: ${launch_agent_file}"
        exit 1
    fi
fi

/bin/mkdir -p "${launch_agent_file:h}" "${runtime_dir}" "${log_dir}"
/bin/chmod 700 "${runtime_dir}" "${log_dir}"
temporary_plist="$(/usr/bin/mktemp "${runtime_dir}/database-backup.plist.XXXXXX")"
previous_plist=""
previous_service_loaded=false
replacement_started=false
installation_complete=false

cleanup() {
    exit_code=$?
    set +e
    if [[ "${replacement_started}" == true && "${installation_complete}" != true ]]; then
        print -u2 "Database-backup LaunchAgent installation failed; restoring prior state."
        /bin/launchctl bootout "${service_target}" >/dev/null 2>&1
        if [[ -n "${previous_plist:-}" && -e "${previous_plist}" ]]; then
            /bin/cp "${previous_plist}" "${launch_agent_file}"
            /bin/chmod 600 "${launch_agent_file}"
            if [[ "${previous_service_loaded}" == true ]]; then
                if ! /bin/launchctl bootstrap "${service_domain}" "${launch_agent_file}"; then
                    print -u2 "Prior plist was restored but its service could not be loaded."
                fi
            fi
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

template_path, output_path, label, python, project, stdout, stderr = sys.argv[1:]
rendered = Path(template_path).read_text(encoding="utf-8")
for placeholder, value in {
    "__BACKUP_SERVICE_LABEL__": label,
    "__BACKUP_VENV_PYTHON__": python,
    "__BACKUP_PROJECT_DIR__": project,
    "__BACKUP_STDOUT_LOG__": stdout,
    "__BACKUP_STDERR_LOG__": stderr,
}.items():
    rendered = rendered.replace(placeholder, escape(value, quote=True))
if "__BACKUP_" in rendered:
    raise SystemExit("database-backup template contains an unresolved placeholder")
Path(output_path).write_text(rendered, encoding="utf-8")
PY

/usr/bin/plutil -lint "${temporary_plist}" >/dev/null
/bin/chmod 600 "${temporary_plist}"
if [[ -e "${launch_agent_file}" ]]; then
    previous_plist="$(/usr/bin/mktemp "${runtime_dir}/database-backup.previous.XXXXXX")"
    /bin/cp "${launch_agent_file}" "${previous_plist}"
    /bin/chmod 600 "${previous_plist}"
fi
previous_service_loaded="${service_is_loaded}"
replacement_started=true
if [[ "${previous_service_loaded}" == true ]]; then
    /bin/launchctl bootout "${service_target}"
fi
/bin/mv "${temporary_plist}" "${launch_agent_file}"
temporary_plist=""
if ! /bin/launchctl bootstrap "${service_domain}" "${launch_agent_file}"; then
    exit 1
fi
installation_complete=true
print "AlphaPilot database backup LaunchAgent installed: ${service_label}"
