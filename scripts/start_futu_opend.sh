#!/bin/zsh

set -euo pipefail

readonly keychain_service="${FUTU_KEYCHAIN_SERVICE:-com.alphapilot.futu.opend}"
readonly service_label="${FUTU_LAUNCHD_LABEL:-com.alphapilot.futu.opend}"
readonly opend_dir="${FUTU_OPEND_DIR:-/Users/ouyangduning/Library/Application Support/AlphaPilot-AI/FutuOpenD/10.9.6908}"
readonly opend_bin="${opend_dir}/FutuOpenD.app/Contents/MacOS/FutuOpenD"
readonly runtime_dir="${FUTU_RUNTIME_DIR:-/Users/ouyangduning/.alphapilot-futu}"
readonly log_dir="${FUTU_LOG_DIR:-/Users/ouyangduning/Library/Logs/AlphaPilot-AI/FutuOpenD}"
readonly config_template="${0:A:h:h}/config/futu_opend.template.xml"
readonly launch_agent_template="${0:A:h:h}/config/futu_opend.launchagent.template.plist"
readonly host="${ALPHAPILOT_FUTU_HOST:-127.0.0.1}"
readonly port="${ALPHAPILOT_FUTU_PORT:-11111}"
readonly config_file="${runtime_dir}/FutuOpenD.xml"
readonly launch_agent_file="${HOME}/Library/LaunchAgents/${service_label}.plist"
readonly launch_domain="gui/$(/usr/bin/id -u)"
readonly service_target="${launch_domain}/${service_label}"
readonly launcher_log="${log_dir}/launcher.log"

config_tmp=""
launch_agent_tmp=""
service_loaded=0

cleanup_failed_start() {
    if (( service_loaded )); then
        /bin/launchctl bootout "${service_target}" >/dev/null 2>&1 || true
    fi
    for runtime_artifact in "${config_tmp}" "${launch_agent_tmp}" "${config_file}" "${launch_agent_file}"; do
        if [[ -n "${runtime_artifact}" && -e "${runtime_artifact}" ]]; then
            /bin/unlink "${runtime_artifact}" 2>/dev/null || true
        fi
    done
}

trap cleanup_failed_start EXIT INT TERM

if [[ ! -x "${opend_bin}" ]]; then
    print -u2 "Futu OpenD CLI was not found at: ${opend_bin}"
    exit 1
fi

for required_template in "${config_template}" "${launch_agent_template}"; do
    if [[ ! -f "${required_template}" ]]; then
        print -u2 "Futu OpenD template was not found at: ${required_template}"
        exit 1
    fi
done

mkdir -p "${runtime_dir}" "${log_dir}" "${launch_agent_file:h}"
chmod 700 "${runtime_dir}" "${log_dir}"

if /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
    if /usr/sbin/lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
        print "Futu OpenD is already running under launchd (${host}:${port})."
        trap - EXIT INT TERM
        exit 0
    fi
    /bin/launchctl bootout "${service_target}" >/dev/null 2>&1 || true
fi

if /usr/sbin/lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    print -u2 "Port ${port} is already in use; refusing to start a second OpenD instance."
    exit 1
fi

keychain_metadata="$(/usr/bin/security find-generic-password -s "${keychain_service}" 2>/dev/null)" || {
    print -u2 "No Futu credential was found in macOS Keychain (service=${keychain_service})."
    exit 1
}
login_account="$(print -r -- "${keychain_metadata}" | /usr/bin/sed -n 's/.*"acct"<blob>="\([^"]*\)".*/\1/p')"
unset keychain_metadata

if [[ -z "${login_account}" ]] || ! print -rn -- "${login_account}" | /usr/bin/grep -Eq '^[A-Za-z0-9@._+-]+$'; then
    print -u2 "The Futu account stored in Keychain has an unsupported format."
    exit 1
fi

login_password="$(/usr/bin/security find-generic-password -a "${login_account}" -s "${keychain_service}" -w)"
login_pwd_md5="$(print -rn -- "${login_password}" | /sbin/md5 -q)"
unset login_password

umask 077
config_tmp="$(/usr/bin/mktemp "${runtime_dir}/FutuOpenD.xml.XXXXXX")"
/usr/bin/sed \
    -e "s|__FUTU_HOST__|${host}|g" \
    -e "s|__FUTU_PORT__|${port}|g" \
    -e "s|__FUTU_LOGIN_ACCOUNT__|${login_account}|g" \
    -e "s|__FUTU_LOGIN_PWD_MD5__|${login_pwd_md5}|g" \
    -e "s|__FUTU_LOG_PATH__|${log_dir}|g" \
    "${config_template}" >"${config_tmp}"
unset login_pwd_md5
chmod 600 "${config_tmp}"
/bin/mv "${config_tmp}" "${config_file}"
config_tmp=""

launch_agent_tmp="$(/usr/bin/mktemp "${runtime_dir}/launch-agent.plist.XXXXXX")"
/usr/bin/sed \
    -e "s|__FUTU_SERVICE_LABEL__|${service_label}|g" \
    -e "s|__FUTU_OPEND_BIN__|${opend_bin}|g" \
    -e "s|__FUTU_CONFIG_FILE__|${config_file}|g" \
    -e "s|__FUTU_LAUNCHER_LOG__|${launcher_log}|g" \
    "${launch_agent_template}" >"${launch_agent_tmp}"
/usr/bin/plutil -lint "${launch_agent_tmp}" >/dev/null
chmod 600 "${launch_agent_tmp}"
/bin/mv "${launch_agent_tmp}" "${launch_agent_file}"
launch_agent_tmp=""

/bin/launchctl bootstrap "${launch_domain}" "${launch_agent_file}"
service_loaded=1

for _ in {1..60}; do
    if /usr/sbin/lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
        print "Futu OpenD API is listening under launchd (${host}:${port})."
        trap - EXIT INT TERM
        exit 0
    fi
    if ! /bin/launchctl print "${service_target}" >/dev/null 2>&1; then
        print -u2 "Futu OpenD launchd service exited before opening ${host}:${port}. See ${launcher_log}."
        exit 1
    fi
    sleep 1
done

print -u2 "Futu OpenD is still starting after 60 seconds. See ${launcher_log}."
exit 1
