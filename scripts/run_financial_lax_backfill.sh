#!/usr/bin/env bash

set -euo pipefail

readonly remote_root="${ALPHAPILOT_LAX_ROOT:-/opt/alphapilot-s2-lax}"
readonly log_file="${remote_root}/logs/financial-backfill.log"

umask 027
/usr/bin/mkdir -p \
  "${remote_root}/data" \
  "${remote_root}/exports" \
  "${remote_root}/logs"

export ALPHAPILOT_BAOSTOCK_LOCK_FILE="${remote_root}/data/baostock-host.lock"
# Keep one request of permanent headroom. On the first deployment day, the
# independently witnessed preflight query consumes that request.
exec /usr/bin/flock --nonblock "${remote_root}/data/financial-backfill.lock" \
  /usr/bin/env \
    -u ALL_PROXY \
    -u all_proxy \
    -u HTTP_PROXY \
    -u http_proxy \
    -u HTTPS_PROXY \
    -u https_proxy \
    -u FTP_PROXY \
    -u ftp_proxy \
    -u NO_PROXY \
    -u no_proxy \
    -u ALPHAPILOT_BAOSTOCK_SOCKS5_PROXY \
  "${remote_root}/.venv/bin/python" \
    -u \
    "${remote_root}/app/scripts/run_financial_backfill.py" \
    --quarters 40 \
    --batch-size 25 \
    --max-provider-requests 39999 \
    --symbol-min 603182 \
    --probe-before-run \
  >>"${log_file}" 2>&1
