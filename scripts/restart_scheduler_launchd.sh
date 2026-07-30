#!/bin/zsh

set -euo pipefail

readonly script_dir="${0:A:h}"

"${script_dir}/stop_scheduler_launchd.sh"
exec "${script_dir}/start_scheduler_launchd.sh"
