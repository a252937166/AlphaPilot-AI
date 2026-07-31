#!/usr/bin/env bash

set -euo pipefail

readonly remote_root="${ALPHAPILOT_DOGCLOUD_ROOT:-/opt/alphapilot-s2-dog}"
readonly image="${ALPHAPILOT_DOGCLOUD_IMAGE:-alphapilot-s2-financial:20260725}"

umask 027
# SQLite WAL readers need the database directory writable for -shm/-wal
# coordination even though this exporter only performs an online backup.
exec /usr/bin/flock --nonblock "${remote_root}/exports/financial-export.lock" \
  /usr/bin/docker run --rm \
    --network none \
    --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --pids-limit 64 \
    --memory 256m \
    --memory-swap 384m \
    --cpus 0.5 \
    --env-file "${remote_root}/.env" \
    --mount "type=bind,src=${remote_root}/data,dst=/data" \
    --mount "type=bind,src=${remote_root}/exports,dst=/exports" \
    "${image}" \
    python -u scripts/export_financial_snapshot.py \
      --source-db /data/alphapilot-s2.db \
      --output-db /exports/financial-s2-latest.db
