#!/usr/bin/env bash

set -euo pipefail

readonly remote_root="${ALPHAPILOT_ALIYUN_ROOT:-/opt/alphapilot-s2}"

umask 027
export ALPHAPILOT_BAOSTOCK_LOCK_FILE="${remote_root}/data/baostock-host.lock"
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
  "${remote_root}/.venv/bin/python" \
    -u \
    "${remote_root}/app/scripts/run_financial_backfill.py" \
    --quarters 40 \
    --batch-size 25 \
    --max-provider-requests 40000 \
    --symbol-max-exclusive 300387 \
    --probe-before-run \
  >>"${remote_root}/logs/financial-backfill.log" 2>&1
