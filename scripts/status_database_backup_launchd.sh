#!/bin/zsh

set -euo pipefail

readonly service_label="${ALPHAPILOT_DATABASE_BACKUP_LABEL:-com.alphapilot.database-backup}"
readonly runtime_dir="${HOME}/Library/Application Support/AlphaPilot-AI/database-backup"
readonly project_dir="${0:A:h:h}"
readonly label_pattern='^[A-Za-z0-9._-]+$'
current_uid="$(/usr/bin/id -u)"
readonly current_uid
readonly service_target="gui/${current_uid}/${service_label}"

if [[ ! "${service_label}" =~ ${label_pattern} ]]; then
    print -u2 "Invalid database-backup launchd label: ${service_label}"
    exit 1
fi

if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    /bin/launchctl print "${service_target}" \
        | /usr/bin/grep -E 'state =|runs =|last exit code =|pid =' || true
else
    print "AlphaPilot database backup LaunchAgent is not loaded."
fi
if [[ -f "${runtime_dir}/last-success-shanghai-date" ]]; then
    print "last-success-shanghai-date=$(<"${runtime_dir}/last-success-shanghai-date")"
else
    print "last-success-shanghai-date=none"
fi
latest_backup="$(/usr/bin/find "${project_dir}/data/backups" -maxdepth 1 -type f -name 'alphapilot-full-*.db' -print 2>/dev/null | /usr/bin/sort | /usr/bin/tail -n 1 || true)"
if [[ -n "${latest_backup}" ]]; then
    latest_manifest="${latest_backup%.db}.manifest.json"
    /bin/ls -lh "${latest_backup}"
    if [[ -f "${latest_manifest}" ]]; then
        /bin/ls -lh "${latest_manifest}"
    else
        print -u2 "latest-backup-manifest=missing"
        exit 1
    fi
else
    print "latest-backup=none"
fi
