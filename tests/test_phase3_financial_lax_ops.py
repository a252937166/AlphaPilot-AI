from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

_ROOT = Path(__file__).resolve().parent.parent
_BOUNDARY_A_B = 300_387
_BOUNDARY_B_US38 = 600_235
_BOUNDARY_US38_USSEA = 603_730


def _text(relative_path: str) -> str:
    return (_ROOT / relative_path).read_text(encoding="utf-8")


def test_remote_runner_shards_are_mutually_exclusive() -> None:
    aliyun = _text("scripts/run_financial_aliyun_backfill.sh")
    dog = _text("scripts/run_financial_dogcloud_backfill.sh")
    us38 = _text("scripts/run_financial_us38_backfill.sh")
    ussea = _text("scripts/run_financial_ussea_backfill.sh")

    assert f"--symbol-max-exclusive {_BOUNDARY_A_B}" in aliyun
    assert "--symbol-min" not in aliyun
    assert f"--symbol-min {_BOUNDARY_A_B}" in dog
    assert f"--symbol-max-exclusive {_BOUNDARY_B_US38}" in dog
    assert f"--symbol-min {_BOUNDARY_B_US38}" in us38
    assert f"--symbol-max-exclusive {_BOUNDARY_US38_USSEA}" in us38
    assert f"--symbol-min {_BOUNDARY_US38_USSEA}" in ussea
    assert "--symbol-max-exclusive 1000000" in ussea
    for runner in (aliyun, dog):
        assert "--max-provider-requests 40000" in runner
        assert "--probe-before-run" in runner
        assert "/usr/bin/flock --nonblock" in runner
    for runner in (us38, ussea):
        assert "--max-provider-requests 39999" in runner
        assert "--probe-before-run" in runner
        assert "/usr/bin/flock --nonblock" in runner
        assert "-u ALPHAPILOT_BAOSTOCK_SOCKS5_PROXY" in runner


def test_new_us_environments_keep_every_trading_gate_closed() -> None:
    for relative_path, database_path in (
        (
            "config/financial_us38.env",
            "sqlite:////opt/alphapilot-s2-us38/data/alphapilot-s2.db",
        ),
        (
            "config/financial_ussea.env",
            "sqlite:////opt/alphapilot-s2-ussea/data/alphapilot-s2.db",
        ),
    ):
        environment = _text(relative_path)
        parsed = {
            key: value
            for line in environment.splitlines()
            if line and not line.startswith("#")
            for key, value in [line.split("=", maxsplit=1)]
        }
        assert parsed["ALPHAPILOT_DATABASE_URL"] == database_path
        assert parsed["ALPHAPILOT_SCHEDULER_ENABLED"] == "false"
        assert parsed["ALPHAPILOT_FUTU_ENABLE_ACCOUNT_MUTATION"] == "false"
        assert parsed["ALPHAPILOT_FUTU_ENABLE_TRADE"] == "false"
        assert parsed["ALPHAPILOT_TRADING_MODE"] == "research"
        assert parsed["ALPHAPILOT_LIVE_TRADING_ENABLED"] == "false"
        assert parsed["ALPHAPILOT_PAPER_TRADING_ENABLED"] == "false"
        assert parsed["ALPHAPILOT_PAPER_AUTO_TRADING_ENABLED"] == "false"
        assert parsed["ALPHAPILOT_TRADING_HALTED"] == "true"


def test_new_us_systemd_units_are_resource_limited_and_avoid_maintenance() -> None:
    expected_schedules = {
        "us38": "OnCalendar=*-*-* 09:05:00 Asia/Shanghai",
        "ussea": "OnCalendar=*-*-* 09:15:00 Asia/Shanghai",
    }
    for suffix, expected_schedule in expected_schedules.items():
        service = _text(f"config/financial_{suffix}.service")
        timer = _text(f"config/financial_{suffix}.timer")

        assert "CPUQuota=40%" in service
        assert "MemoryMax=256M" in service
        assert "MemorySwapMax=384M" in service
        assert "TasksMax=64" in service
        assert "Nice=10" in service
        assert "NoNewPrivileges=true" in service
        assert "ProtectSystem=strict" in service
        assert "[Install]" not in service
        assert expected_schedule in timer
        assert "Persistent=true" in timer


def test_new_us_deployer_grants_only_service_group_read_execute_access() -> None:
    deployer = _text("scripts/deploy_financial_remote_worker.sh")

    for required_name in (
        "TASK_APP_ARCHIVE",
        "TASK_SEED_DATABASE",
        "TASK_APP_SHA256",
        "TASK_SEED_SHA256",
    ):
        assert f": \"${{{required_name}:?" in deployer
    assert 'readonly app_archive="${TASK_APP_ARCHIVE}"' in deployer
    assert 'readonly seed_database="${TASK_SEED_DATABASE}"' in deployer
    assert 'readonly app_sha256="${TASK_APP_SHA256}"' in deployer
    assert 'readonly seed_sha256="${TASK_SEED_SHA256}"' in deployer
    assert "alphapilot-s2-app-20260726T2350CST.tgz" not in deployer
    assert "alphapilot-s2-seed-20260726T2350CST.db" not in deployer
    assert 'install -d -o root -g "${service_user}" -m 0750 "${TASK_ROOT}"' in deployer
    assert 'chown -R root:"${service_user}" "${TASK_ROOT}/app"' in deployer
    assert 'chmod -R g+rX,g-w,o-rwx "${TASK_ROOT}/app"' in deployer
    assert 'chown -R root:"${service_user}" "${TASK_ROOT}/.venv"' in deployer
    assert 'chmod -R g+rX,g-w,o-rwx "${TASK_ROOT}/.venv"' in deployer
    assert 'install -o root -g "${service_user}" -m 0640' in deployer
    assert deployer.count('install -o root -g "${service_user}" -m 0750') == 2
    assert 'runuser -u "${service_user}" -- bash -c' in deployer
    assert 'runuser -u "${service_user}" -- \\\n' in deployer
    assert "\\( -type f -o -type d \\) -perm /0022" in deployer
    assert "import baostock" in deployer
    assert "import pandas" in deployer
    assert "import sqlalchemy" in deployer
    assert 'systemctl enable --now "${TASK_SERVICE}.timer"' not in deployer
    assert 'systemctl enable "${TASK_SERVICE}.timer"' in deployer
    assert deployer.index('runuser -u "${service_user}"') < deployer.index(
        'systemctl enable "${TASK_SERVICE}.timer"'
    )


def test_lax_environment_keeps_every_trading_gate_closed() -> None:
    environment = _text("config/financial_lax.env")

    expected = {
        "ALPHAPILOT_DATABASE_URL": ("sqlite:////opt/alphapilot-s2-lax/data/alphapilot-s2.db"),
        "ALPHAPILOT_SCHEDULER_ENABLED": "false",
        "ALPHAPILOT_FUTU_ENABLE_ACCOUNT_MUTATION": "false",
        "ALPHAPILOT_FUTU_ENABLE_TRADE": "false",
        "ALPHAPILOT_TRADING_MODE": "research",
        "ALPHAPILOT_LIVE_TRADING_ENABLED": "false",
        "ALPHAPILOT_PAPER_TRADING_ENABLED": "false",
        "ALPHAPILOT_PAPER_AUTO_TRADING_ENABLED": "false",
        "ALPHAPILOT_TRADING_HALTED": "true",
    }
    parsed = {
        key: value
        for line in environment.splitlines()
        if line and not line.startswith("#")
        for key, value in [line.split("=", maxsplit=1)]
    }
    assert parsed | expected == parsed


def test_lax_systemd_limits_are_supported_by_centos7_systemd219() -> None:
    service = _text("config/financial_lax.service")
    timer = _text("config/financial_lax.timer")

    assert "CPUQuota=50%" in service
    assert "MemoryLimit=300M" in service
    assert "\nMemoryMax=" not in service
    assert "LimitNPROC=64" in service
    assert "\nTasksMax=" not in service
    assert "Nice=10" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=full" in service
    assert "ProtectSystem=strict" not in service
    assert "[Install]" not in service
    assert "WantedBy=multi-user.target" not in service
    assert "OnCalendar=*-*-* 00:10:00" in timer
    assert "Persistent=true" not in timer


def test_lax_pull_uses_nonstandard_ssh_port_and_shanghai_0440_gate() -> None:
    template_path = _ROOT / "config/financial_lax_pull.launchagent.template.plist"
    template = template_path.read_text(encoding="utf-8")
    installer = _text("scripts/install_financial_lax_pull_launchd.sh")

    ElementTree.parse(template_path)
    assert "<string>--ssh-port</string>" in template
    assert "<string>__LAX_PULL_SSH_PORT__</string>" in template
    assert "<string>__LAX_PULL_GATE__</string>" in template
    assert "<string>__LAX_PULL_STATE_FILE__</string>" in template
    assert "<string>04:40</string>" in template
    assert "<key>Hour</key>" not in template
    assert "<integer>40</integer>" in template
    assert "com.alphapilot.s2-financial-pull-lax" in installer
    assert "root@192.220.61.180" in installer
    assert "49291" in installer
    assert "data/phase3-s2-lax" in installer
    assert "<string>--transfer-mode</string>" not in template


def test_dog_pull_uses_resumable_rsync_and_shanghai_0425_gate() -> None:
    template_path = _ROOT / "config/financial_dog_pull.launchagent.template.plist"
    template = template_path.read_text(encoding="utf-8")
    installer = _text("scripts/install_financial_dog_pull_launchd.sh")

    ElementTree.parse(template_path)
    assert "<string>--transfer-mode</string>" in template
    assert "<string>rsync</string>" in template
    assert "<string>--rsync-bin</string>" in template
    assert "<string>__DOG_PULL_RSYNC_BIN__</string>" in template
    assert "<string>__DOG_PULL_GATE__</string>" in template
    assert "<string>__DOG_PULL_STATE_FILE__</string>" in template
    assert "<string>04:25</string>" in template
    assert "<key>Hour</key>" not in template
    assert "<integer>25</integer>" in template
    assert "ALPHAPILOT_DOG_RSYNC_BIN:-/usr/local/bin/rsync" in installer
    assert "--append-verify" in installer


def test_aliyun_pull_uses_shanghai_0410_gate() -> None:
    template_path = _ROOT / "config/financial_pull.launchagent.template.plist"
    template = template_path.read_text(encoding="utf-8")
    installer = _text("scripts/install_financial_pull_launchd.sh")

    ElementTree.parse(template_path)
    assert "<string>__FINANCIAL_PULL_GATE__</string>" in template
    assert "<string>__FINANCIAL_PULL_STATE_FILE__</string>" in template
    assert "<string>04:10</string>" in template
    assert "<key>Hour</key>" not in template
    assert "<integer>10</integer>" in template
    assert "run_shanghai_daily.py" in installer
    assert "at/after 04:10 Asia/Shanghai" in installer


def test_generic_remote_pull_supports_unique_host_paths_and_shanghai_gate() -> None:
    template_path = _ROOT / "config/financial_remote_pull.launchagent.template.plist"
    template = template_path.read_text(encoding="utf-8")
    installer = _text("scripts/install_financial_remote_pull_launchd.sh")

    ElementTree.parse(template_path)
    assert "__REMOTE_PULL_ROOT__" in template
    assert "__REMOTE_PULL_EXPORTER__" in template
    assert "__REMOTE_PULL_SNAPSHOT_DIR__" in template
    assert "__REMOTE_PULL_AT__" in template
    assert "__REMOTE_PULL_WAKE_MINUTE__" in template
    assert "ALPHAPILOT_REMOTE_PULL_LABEL" in installer
    assert "ALPHAPILOT_REMOTE_PULL_SSH_TARGET" in installer
    assert "ALPHAPILOT_REMOTE_PULL_SSH_PORT" in installer
    assert "ALPHAPILOT_REMOTE_PULL_SNAPSHOT_NAME" in installer
