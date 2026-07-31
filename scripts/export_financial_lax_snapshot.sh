#!/usr/bin/env bash

set -euo pipefail

readonly remote_root="${ALPHAPILOT_LAX_ROOT:-/opt/alphapilot-s2-lax}"

umask 027
exec /usr/bin/flock --nonblock "${remote_root}/exports/financial-export.lock" \
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
    "${remote_root}/app/scripts/export_financial_snapshot.py" \
    --source-db "${remote_root}/data/alphapilot-s2.db" \
    --output-db "${remote_root}/exports/financial-s2-latest.db"
