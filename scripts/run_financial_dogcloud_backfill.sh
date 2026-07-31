#!/usr/bin/env bash

set -euo pipefail

readonly remote_root="${ALPHAPILOT_DOGCLOUD_ROOT:-/opt/alphapilot-s2-dog}"
readonly image="${ALPHAPILOT_DOGCLOUD_IMAGE:-alphapilot-s2-financial:20260725}"
readonly log_file="${remote_root}/logs/financial-backfill.log"

umask 027
/usr/bin/mkdir -p \
  "${remote_root}/data" \
  "${remote_root}/exports" \
  "${remote_root}/logs"

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
  /usr/bin/docker run --rm \
    --name alphapilot-s2-dog-financial \
    --network host \
    --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --pids-limit 128 \
    --memory 384m \
    --memory-swap 512m \
    --cpus 0.75 \
    --env-file "${remote_root}/.env" \
    --mount "type=bind,src=${remote_root}/data,dst=/data" \
    "${image}" \
    python -u scripts/run_financial_backfill.py \
      --quarters 40 \
      --batch-size 25 \
      --max-provider-requests 40000 \
      --symbol-min 300387 \
      --symbol-max-exclusive 600235 \
      --probe-before-run \
  >>"${log_file}" 2>&1
