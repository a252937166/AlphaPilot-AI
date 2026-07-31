#!/usr/bin/env bash

set -euo pipefail

: "${TASK_ROOT:?TASK_ROOT is required}"
: "${TASK_SERVICE:?TASK_SERVICE is required}"
: "${TASK_SYMBOL_MIN:?TASK_SYMBOL_MIN is required}"
: "${TASK_SYMBOL_MAX_EXCLUSIVE:?TASK_SYMBOL_MAX_EXCLUSIVE is required}"
: "${TASK_SCHEDULE:?TASK_SCHEDULE is required}"
: "${TASK_APP_ARCHIVE:?TASK_APP_ARCHIVE is required}"
: "${TASK_SEED_DATABASE:?TASK_SEED_DATABASE is required}"
: "${TASK_APP_SHA256:?TASK_APP_SHA256 is required}"
: "${TASK_SEED_SHA256:?TASK_SEED_SHA256 is required}"

if [[ ! "${TASK_ROOT}" =~ ^/opt/alphapilot-s2-[A-Za-z0-9-]+$ ]]; then
    echo "invalid TASK_ROOT" >&2
    exit 2
fi
if [[ ! "${TASK_SERVICE}" =~ ^alphapilot-s2-financial-[a-z0-9-]+$ ]]; then
    echo "invalid TASK_SERVICE" >&2
    exit 2
fi
if [[ ! "${TASK_SYMBOL_MIN}" =~ ^[0-9]{1,6}$ ]]; then
    echo "invalid TASK_SYMBOL_MIN" >&2
    exit 2
fi
if [[ ! "${TASK_SYMBOL_MAX_EXCLUSIVE}" =~ ^[0-9]{1,7}$ ]]; then
    echo "invalid TASK_SYMBOL_MAX_EXCLUSIVE" >&2
    exit 2
fi
if (( TASK_SYMBOL_MIN >= TASK_SYMBOL_MAX_EXCLUSIVE )); then
    echo "invalid symbol range" >&2
    exit 2
fi
if [[ ! "${TASK_SCHEDULE}" =~ ^[0-2][0-9]:[0-5][0-9]:[0-5][0-9]$ ]]; then
    echo "invalid TASK_SCHEDULE" >&2
    exit 2
fi
if [[ ! "${TASK_APP_ARCHIVE}" =~ ^"${TASK_ROOT}"/incoming/alphapilot-s2-app-[A-Za-z0-9._-]+\.tgz$ ]]; then
    echo "invalid TASK_APP_ARCHIVE" >&2
    exit 2
fi
if [[ ! "${TASK_SEED_DATABASE}" =~ ^"${TASK_ROOT}"/incoming/alphapilot-s2-seed-[A-Za-z0-9._-]+\.db$ ]]; then
    echo "invalid TASK_SEED_DATABASE" >&2
    exit 2
fi
if [[ ! "${TASK_APP_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "invalid TASK_APP_SHA256" >&2
    exit 2
fi
if [[ ! "${TASK_SEED_SHA256}" =~ ^[0-9a-f]{64}$ ]]; then
    echo "invalid TASK_SEED_SHA256" >&2
    exit 2
fi

readonly service_user="alphapilot-s2"
readonly app_archive="${TASK_APP_ARCHIVE}"
readonly seed_database="${TASK_SEED_DATABASE}"
readonly app_sha256="${TASK_APP_SHA256}"
readonly seed_sha256="${TASK_SEED_SHA256}"
readonly service_unit="/etc/systemd/system/${TASK_SERVICE}.service"
readonly timer_unit="/etc/systemd/system/${TASK_SERVICE}.timer"

umask 027

[[ "$(sha256sum "${app_archive}" | awk '{print $1}')" == "${app_sha256}" ]]
[[ "$(sha256sum "${seed_database}" | awk '{print $1}')" == "${seed_sha256}" ]]

if ! id "${service_user}" >/dev/null 2>&1; then
    useradd \
        --system \
        --home-dir "${TASK_ROOT}" \
        --shell /sbin/nologin \
        "${service_user}"
fi

install -d -o root -g "${service_user}" -m 0750 "${TASK_ROOT}"
install -d -o "${service_user}" -g "${service_user}" -m 0750 \
    "${TASK_ROOT}/data" \
    "${TASK_ROOT}/exports" \
    "${TASK_ROOT}/logs"
install -d -o root -g root -m 0700 "${TASK_ROOT}/ops-backups"

app_stage="$(mktemp -d "${TASK_ROOT}/.app-stage.XXXXXX")"
trap 'rm -rf "${app_stage}"' EXIT
tar -xzf "${app_archive}" -C "${app_stage}"
if [[ -e "${TASK_ROOT}/app" ]]; then
    backup_path="${TASK_ROOT}/ops-backups/app.$(date +%Y%m%dT%H%M%S)"
    mv "${TASK_ROOT}/app" "${backup_path}"
fi
mv "${app_stage}" "${TASK_ROOT}/app"
trap - EXIT
chown -R root:"${service_user}" "${TASK_ROOT}/app"
chmod -R g+rX,g-w,o-rwx "${TASK_ROOT}/app"

if [[ ! -x "${TASK_ROOT}/.venv/bin/python" ]]; then
    python3.11 -m venv "${TASK_ROOT}/.venv"
fi
"${TASK_ROOT}/.venv/bin/python" -m pip install \
    --disable-pip-version-check \
    --no-input \
    --no-cache-dir \
    --quiet \
    "${TASK_ROOT}/app" \
    "baostock==0.9.3"
chown -R root:"${service_user}" "${TASK_ROOT}/.venv"
chmod -R g+rX,g-w,o-rwx "${TASK_ROOT}/.venv"

install -o "${service_user}" -g "${service_user}" -m 0640 \
    "${seed_database}" \
    "${TASK_ROOT}/data/alphapilot-s2.db"

env_temp="$(mktemp "${TASK_ROOT}/.env.XXXXXX")"
trap 'rm -f "${env_temp}"' EXIT
{
    echo "ALPHAPILOT_APP_ENV=backfill"
    echo "ALPHAPILOT_DATABASE_URL=sqlite:///${TASK_ROOT}/data/alphapilot-s2.db"
    echo "ALPHAPILOT_DEFAULT_DATA_PROVIDER=baostock"
    echo "ALPHAPILOT_SCHEDULER_ENABLED=false"
    echo "ALPHAPILOT_MARKET_POLL_ENABLED=false"
    echo "ALPHAPILOT_BAOSTOCK_FINANCIAL_SYNC_ENABLED=false"
    echo "ALPHAPILOT_VALUATION_SYNC_ENABLED=false"
    echo "ALPHAPILOT_FUTU_ENABLE_QUOTE=false"
    echo "ALPHAPILOT_FUTU_ENABLE_TRADE_QUERY=false"
    echo "ALPHAPILOT_FUTU_ENABLE_ACCOUNT_MUTATION=false"
    echo "ALPHAPILOT_FUTU_ENABLE_TRADE=false"
    echo "ALPHAPILOT_TRADING_MODE=research"
    echo "ALPHAPILOT_LIVE_TRADING_ENABLED=false"
    echo "ALPHAPILOT_PAPER_TRADING_ENABLED=false"
    echo "ALPHAPILOT_PAPER_AUTO_TRADING_ENABLED=false"
    echo "ALPHAPILOT_TRADING_HALTED=true"
    echo "ALPHAPILOT_BAOSTOCK_LOCK_FILE=${TASK_ROOT}/data/baostock-host.lock"
    echo "PYTHONDONTWRITEBYTECODE=1"
    echo "PYTHONUNBUFFERED=1"
} >"${env_temp}"
install -o root -g "${service_user}" -m 0640 "${env_temp}" "${TASK_ROOT}/.env"
rm -f "${env_temp}"
trap - EXIT

runner_temp="$(mktemp "${TASK_ROOT}/.runner.XXXXXX")"
trap 'rm -f "${runner_temp}"' EXIT
{
    echo '#!/usr/bin/env bash'
    echo
    echo 'set -euo pipefail'
    echo
    echo "readonly remote_root='${TASK_ROOT}'"
    echo 'readonly log_file="${remote_root}/logs/financial-backfill.log"'
    echo
    echo 'umask 027'
    echo 'exec /usr/bin/flock --nonblock "${remote_root}/data/financial-backfill.lock" \'
    echo '  /usr/bin/env \'
    echo '    -u ALL_PROXY -u all_proxy \'
    echo '    -u HTTP_PROXY -u http_proxy \'
    echo '    -u HTTPS_PROXY -u https_proxy \'
    echo '    -u FTP_PROXY -u ftp_proxy \'
    echo '    -u NO_PROXY -u no_proxy \'
    echo '    -u ALPHAPILOT_BAOSTOCK_SOCKS5_PROXY \'
    echo '  "${remote_root}/.venv/bin/python" -u \'
    echo '    "${remote_root}/app/scripts/run_financial_backfill.py" \'
    echo '      --quarters 40 \'
    echo '      --batch-size 25 \'
    echo '      --max-provider-requests 39999 \'
    echo "      --symbol-min ${TASK_SYMBOL_MIN} \\"
    echo "      --symbol-max-exclusive ${TASK_SYMBOL_MAX_EXCLUSIVE} \\"
    echo '      --probe-before-run \'
    echo '  >>"${log_file}" 2>&1'
} >"${runner_temp}"
bash -n "${runner_temp}"
install -o root -g "${service_user}" -m 0750 \
    "${runner_temp}" \
    "${TASK_ROOT}/run-financial-backfill.sh"
rm -f "${runner_temp}"
trap - EXIT

exporter_temp="$(mktemp "${TASK_ROOT}/.exporter.XXXXXX")"
trap 'rm -f "${exporter_temp}"' EXIT
{
    echo '#!/usr/bin/env bash'
    echo
    echo 'set -euo pipefail'
    echo
    echo "readonly remote_root='${TASK_ROOT}'"
    echo
    echo 'umask 027'
    echo 'exec /usr/bin/flock --nonblock "${remote_root}/exports/financial-export.lock" \'
    echo '  /usr/bin/env \'
    echo '    -u ALL_PROXY -u all_proxy \'
    echo '    -u HTTP_PROXY -u http_proxy \'
    echo '    -u HTTPS_PROXY -u https_proxy \'
    echo '    -u FTP_PROXY -u ftp_proxy \'
    echo '    -u NO_PROXY -u no_proxy \'
    echo '    -u ALPHAPILOT_BAOSTOCK_SOCKS5_PROXY \'
    echo '  "${remote_root}/.venv/bin/python" -u \'
    echo '    "${remote_root}/app/scripts/export_financial_snapshot.py" \'
    echo '      --source-db "${remote_root}/data/alphapilot-s2.db" \'
    echo '      --output-db "${remote_root}/exports/financial-s2-latest.db"'
} >"${exporter_temp}"
bash -n "${exporter_temp}"
install -o root -g "${service_user}" -m 0750 \
    "${exporter_temp}" \
    "${TASK_ROOT}/export-financial-snapshot.sh"
rm -f "${exporter_temp}"
trap - EXIT

service_temp="$(mktemp "/etc/systemd/system/.${TASK_SERVICE}.service.XXXXXX")"
timer_temp="$(mktemp "/etc/systemd/system/.${TASK_SERVICE}.timer.XXXXXX")"
trap 'rm -f "${service_temp}" "${timer_temp}"' EXIT
{
    echo '[Unit]'
    echo "Description=AlphaPilot S2 financial backfill ${TASK_SERVICE}"
    echo 'Wants=network-online.target'
    echo 'After=network-online.target'
    echo "ConditionPathExists=${TASK_ROOT}/data/alphapilot-s2.db"
    echo
    echo '[Service]'
    echo 'Type=oneshot'
    echo "User=${service_user}"
    echo "Group=${service_user}"
    echo "WorkingDirectory=${TASK_ROOT}/app"
    echo "EnvironmentFile=${TASK_ROOT}/.env"
    echo "ExecStart=${TASK_ROOT}/run-financial-backfill.sh"
    echo 'TimeoutStartSec=6h'
    echo 'Restart=no'
    echo 'Nice=10'
    echo 'IOSchedulingClass=idle'
    echo 'CPUQuota=40%'
    echo 'MemoryMax=256M'
    echo 'MemorySwapMax=384M'
    echo 'TasksMax=64'
    echo 'LimitNOFILE=1024'
    echo 'UMask=0027'
    echo 'NoNewPrivileges=true'
    echo 'PrivateDevices=true'
    echo 'PrivateTmp=true'
    echo 'ProtectHome=true'
    echo 'ProtectSystem=strict'
    echo "ReadOnlyPaths=${TASK_ROOT}/app ${TASK_ROOT}/.venv ${TASK_ROOT}/.env"
    echo "ReadWritePaths=${TASK_ROOT}/data ${TASK_ROOT}/logs ${TASK_ROOT}/exports"
} >"${service_temp}"
{
    echo '[Unit]'
    echo "Description=Schedule ${TASK_SERVICE} outside BaoStock maintenance"
    echo
    echo '[Timer]'
    echo "OnCalendar=*-*-* ${TASK_SCHEDULE} Asia/Shanghai"
    echo 'Persistent=true'
    echo 'RandomizedDelaySec=30s'
    echo "Unit=${TASK_SERVICE}.service"
    echo
    echo '[Install]'
    echo 'WantedBy=timers.target'
} >"${timer_temp}"

install -o root -g root -m 0644 "${service_temp}" "${service_unit}"
install -o root -g root -m 0644 "${timer_temp}" "${timer_unit}"
rm -f "${service_temp}" "${timer_temp}"
trap - EXIT

systemd-analyze verify "${service_unit}" "${timer_unit}"
systemd-analyze calendar "*-*-* ${TASK_SCHEDULE} Asia/Shanghai" >/dev/null
systemctl daemon-reload

runuser -u "${service_user}" -- bash -c '
    set -euo pipefail
    cd "$1"
    test -r "$2"
    test -x "$3"
    test -r "$4"
    test -x "$5"
    test -x "$6"
    test -r "$7"
    test -w "$7"
    test -w "$8"
    test -w "$9"
    for directory in "$8" "$9" "${10}"; do
        probe="${directory}/.alphapilot-permission-check.$$"
        : >"${probe}"
        rm -f "${probe}"
    done
' _ \
    "${TASK_ROOT}/app" \
    "${TASK_ROOT}/.env" \
    "${TASK_ROOT}/.venv/bin/python" \
    "${TASK_ROOT}/app/scripts/run_financial_backfill.py" \
    "${TASK_ROOT}/run-financial-backfill.sh" \
    "${TASK_ROOT}/export-financial-snapshot.sh" \
    "${TASK_ROOT}/data/alphapilot-s2.db" \
    "${TASK_ROOT}/data" \
    "${TASK_ROOT}/logs" \
    "${TASK_ROOT}/exports"

if find "${TASK_ROOT}/app" "${TASK_ROOT}/.venv" \
    \( -type f -o -type d \) -perm /0022 -print -quit |
    grep -q .; then
    echo "immutable runtime tree contains group/world-writable entries" >&2
    exit 1
fi

runuser -u "${service_user}" -- \
    env PYTHONDONTWRITEBYTECODE=1 \
    "${TASK_ROOT}/.venv/bin/python" \
    - "${TASK_ROOT}/data/alphapilot-s2.db" <<'PY'
import json
import sqlite3
import sys

import baostock  # noqa: F401
import pandas  # noqa: F401
import sqlalchemy  # noqa: F401

path = sys.argv[1]
import alphapilot  # noqa: F401

with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
    connection.execute("PRAGMA query_only=ON")
    result = {
        "quick_check": connection.execute("PRAGMA quick_check").fetchone()[0],
        "rows": connection.execute(
            "SELECT COUNT(*) FROM financial_indicators"
        ).fetchone()[0],
        "symbols": connection.execute(
            "SELECT COUNT(DISTINCT symbol) FROM financial_indicators"
        ).fetchone()[0],
        "job_runs": connection.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0],
        "trade_proposals": connection.execute(
            "SELECT COUNT(*) FROM trade_proposals"
        ).fetchone()[0],
        "broker_orders": connection.execute(
            "SELECT COUNT(*) FROM broker_orders"
        ).fetchone()[0],
    }
print(json.dumps(result, sort_keys=True))
PY

systemctl reset-failed "${TASK_SERVICE}.service" || true
systemctl enable "${TASK_SERVICE}.timer"
echo "timer enabled but intentionally not started; activate only after operator approval"

printf 'service_unit_sha='
sha256sum "${service_unit}" | awk '{print $1}'
printf 'timer_unit_sha='
sha256sum "${timer_unit}" | awk '{print $1}'
printf 'runner_sha='
sha256sum "${TASK_ROOT}/run-financial-backfill.sh" | awk '{print $1}'
printf 'next_timer='
systemctl list-timers "${TASK_SERVICE}.timer" --no-legend --no-pager
