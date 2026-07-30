#!/bin/zsh

set -euo pipefail

readonly service_label="${ALPHAPILOT_SCHEDULER_LAUNCHD_LABEL:-com.alphapilot.scheduler}"
readonly api_service_label="${ALPHAPILOT_API_LAUNCHD_LABEL:-com.alphapilot.api}"
readonly project_dir="${0:A:h:h}"
readonly database="${project_dir}/data/alphapilot.db"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly api_launch_agent_file="${HOME}/Library/LaunchAgents/${api_service_label}.plist"
readonly log_dir="${HOME}/Library/Logs/AlphaPilot-AI"
readonly label_pattern='^[A-Za-z0-9._-]+$'
current_uid="$(/usr/bin/id -u)"
readonly current_uid
readonly service_target="gui/${current_uid}/${service_label}"

if [[ ! "${service_label}" =~ ${label_pattern} ]]; then
    print -u2 "Invalid scheduler launchd label: ${service_label}"
    exit 1
fi

if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    /bin/launchctl print "${service_target}" \
        | /usr/bin/grep -E 'state =|runs =|last exit code =|pid =' || true
else
    print "AlphaPilot scheduler LaunchAgent is not loaded."
fi

if [[ -f "${launch_agent_file}" ]]; then
    scheduler_env="$(/usr/bin/plutil \
        -extract EnvironmentVariables.ALPHAPILOT_SCHEDULER_ENABLED \
        raw -o - "${launch_agent_file}" 2>/dev/null || true)"
    print "scheduler-plist-enabled=${scheduler_env:-missing}"
else
    print "scheduler-plist=missing"
fi
if [[ -f "${api_launch_agent_file}" ]]; then
    api_env="$(/usr/bin/plutil \
        -extract EnvironmentVariables.ALPHAPILOT_SCHEDULER_ENABLED \
        raw -o - "${api_launch_agent_file}" 2>/dev/null || true)"
    print "api-plist-scheduler-enabled=${api_env:-missing}"
else
    print "api-plist=missing"
fi

if [[ -f "${database}" ]]; then
    "${project_dir}/.venv/bin/python" - "${database}" <<'PY'
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import sys

database = Path(sys.argv[1]).resolve()
with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
    connection.execute("PRAGMA query_only=ON")
    running = connection.execute(
        "SELECT COUNT(*) FROM job_runs WHERE status='running'"
    ).fetchone()[0]
    print(f"running-job-runs={running}")
    for name in ("poll_market_snapshot", "sync_orders"):
        row = connection.execute(
            """
            SELECT id, status, started_at, finished_at, error
            FROM job_runs
            WHERE job_name=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (name,),
        ).fetchone()
        if row is None:
            print(f"{name}=missing")
            continue
        finished = row[3]
        age = "unknown"
        if finished:
            parsed = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            age = f"{max(0.0, (datetime.now(UTC) - parsed).total_seconds()):.1f}s"
        print(
            f"{name}=id:{row[0]} status:{row[1]} started:{row[2]} "
            f"finished:{finished} age:{age} error:{row[4]}"
        )
PY
else
    print -u2 "database=missing"
fi

print "Logs: ${log_dir}/scheduler.stdout.log and ${log_dir}/scheduler.stderr.log"
