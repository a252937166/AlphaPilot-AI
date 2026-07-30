from __future__ import annotations

import plistlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _render_plist(path: Path, prefix: str) -> dict[str, object]:
    rendered = path.read_text(encoding="utf-8")
    replacements = {
        f"__{prefix}_SERVICE_LABEL__": f"com.alphapilot.{prefix.lower()}",
        f"__{prefix}_VENV_PYTHON__": "/project/.venv/bin/python",
        f"__{prefix}_WORKING_DIRECTORY__": "/project",
        f"__{prefix}_STDOUT_LOG__": "/logs/stdout.log",
        f"__{prefix}_STDERR_LOG__": "/logs/stderr.log",
    }
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    assert f"__{prefix}_" not in rendered
    payload = plistlib.loads(rendered.encode())
    assert isinstance(payload, dict)
    return payload


def test_api_and_scheduler_plists_cannot_both_enable_scheduler() -> None:
    api = _render_plist(
        PROJECT_ROOT / "config" / "api.launchagent.template.plist",
        "API",
    )
    scheduler = _render_plist(
        PROJECT_ROOT / "config" / "scheduler.launchagent.template.plist",
        "SCHEDULER",
    )

    api_environment = api["EnvironmentVariables"]
    scheduler_environment = scheduler["EnvironmentVariables"]
    assert isinstance(api_environment, dict)
    assert isinstance(scheduler_environment, dict)
    assert api_environment["ALPHAPILOT_SCHEDULER_ENABLED"] == "false"
    assert scheduler_environment["ALPHAPILOT_SCHEDULER_ENABLED"] == "true"
    assert scheduler_environment["ALPHAPILOT_TRADING_MODE"] == "research"
    assert scheduler_environment["ALPHAPILOT_LIVE_TRADING_ENABLED"] == "false"
    assert scheduler_environment["ALPHAPILOT_PAPER_AUTO_TRADING_ENABLED"] == "false"
    assert scheduler_environment["ALPHAPILOT_FUTU_ENABLE_ACCOUNT_MUTATION"] == "false"
    assert scheduler_environment["ALPHAPILOT_BAOSTOCK_FINANCIAL_SYNC_ENABLED"] == "false"
    assert scheduler["KeepAlive"] is True
    assert scheduler["ExitTimeOut"] == 300


def test_scheduler_scripts_only_target_exact_service() -> None:
    script_names = (
        "start_scheduler_launchd.sh",
        "stop_scheduler_launchd.sh",
        "restart_scheduler_launchd.sh",
        "status_scheduler_launchd.sh",
    )
    for name in script_names:
        contents = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "pkill" not in contents
        assert "killall" not in contents
        assert "com.alphapilot.*" not in contents

    start = (PROJECT_ROOT / "scripts" / "start_scheduler_launchd.sh").read_text(encoding="utf-8")
    first_bootout = start.index("/bin/launchctl bootout")
    assert start.index('! -f "${database}"') < first_bootout
    assert start.index('! -f "${api_launch_agent_file}"') < first_bootout
    assert start.index("ALPHAPILOT_SCHEDULER_ENABLED") < first_bootout
