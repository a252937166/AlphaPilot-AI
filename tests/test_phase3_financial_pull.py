from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "pull_financial_snapshot.py"
)
_SPEC = importlib.util.spec_from_file_location("pull_financial_snapshot", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
pull_financial_snapshot = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = pull_financial_snapshot
_SPEC.loader.exec_module(pull_financial_snapshot)


def test_lax_ssh_port_is_rendered_for_ssh_and_scp() -> None:
    assert pull_financial_snapshot._ssh_transport_options(49_291) == [
        "-p",
        "49291",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]
    assert pull_financial_snapshot._scp_transport_options(49_291) == [
        "-O",
        "-P",
        "49291",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]


def test_remote_export_invocation_passes_lax_port_to_ssh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Completed:
        stdout = '{"sha256":"abc123"}\n'

    def fake_run(arguments: list[str], **kwargs: object) -> _Completed:
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return _Completed()

    monkeypatch.setattr(pull_financial_snapshot.subprocess, "run", fake_run)

    manifest = pull_financial_snapshot._run_remote_export(
        "root@192.220.61.180",
        49_291,
        Path("/opt/alphapilot-s2-lax"),
        Path("/opt/alphapilot-s2-lax/export-financial-snapshot.sh"),
    )

    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    assert arguments[:4] == ["/usr/bin/ssh", "-p", "49291", "-o"]
    assert arguments[-2] == "root@192.220.61.180"
    assert manifest["remote_snapshot"] == (
        "/opt/alphapilot-s2-lax/exports/financial-s2-latest.db"
    )


@pytest.mark.parametrize("port", [0, -1, 65_536])
def test_invalid_ssh_port_is_rejected(port: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 65535"):
        pull_financial_snapshot._ssh_transport_options(port)


def _trading_safety_database(path: Path, *, proposals: int, orders: int) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE trade_proposals (id INTEGER PRIMARY KEY);
            CREATE TABLE broker_orders (id INTEGER PRIMARY KEY);
            """
        )
        connection.executemany(
            "INSERT INTO trade_proposals (id) VALUES (?)",
            [(index + 1,) for index in range(proposals)],
        )
        connection.executemany(
            "INSERT INTO broker_orders (id) VALUES (?)",
            [(index + 1,) for index in range(orders)],
        )


def test_financial_pull_requires_exact_production_trading_counts(
    tmp_path: Path,
) -> None:
    valid = tmp_path / "valid.db"
    invalid = tmp_path / "invalid.db"
    _trading_safety_database(valid, proposals=1, orders=1)
    _trading_safety_database(invalid, proposals=0, orders=0)

    assert pull_financial_snapshot._require_production_trading_safety_counts(
        valid
    ) == (1, 1)
    with pytest.raises(RuntimeError, match=r"observed=\(0, 0\)"):
        pull_financial_snapshot._require_production_trading_safety_counts(invalid)


def _executable_stub(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def test_rsync_command_uses_audited_resume_and_ssh_options(tmp_path: Path) -> None:
    rsync_bin = _executable_stub(tmp_path / "rsync")
    partial = tmp_path / ".financial-s2-latest.db.rsync-partial"

    command = pull_financial_snapshot._rsync_command(
        rsync_bin=rsync_bin,
        ssh_target="root@206.237.18.80",
        ssh_port=22,
        remote_snapshot="/opt/alphapilot-s2-dog/exports/financial-s2-latest.db",
        partial_path=partial,
    )

    assert command[:4] == [
        str(rsync_bin),
        "-z",
        "--partial",
        "--append-verify",
    ]
    assert command[4:6] == ["-e", command[5]]
    assert "BatchMode=yes" in command[5]
    assert "-p 22" in command[5]
    assert "ServerAliveInterval=15" in command[5]
    assert "ServerAliveCountMax=3" in command[5]
    assert command[-3:] == [
        "--",
        (
            "root@206.237.18.80:"
            "/opt/alphapilot-s2-dog/exports/financial-s2-latest.db"
        ),
        str(partial),
    ]


def test_rsync_retries_the_same_fixed_partial_and_then_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rsync_bin = _executable_stub(tmp_path / "rsync")
    partial = tmp_path / ".financial-s2-latest.db.rsync-partial"
    calls: list[list[str]] = []
    delays: list[float] = []

    def accept_append_verify(_rsync_bin: Path) -> None:
        return None

    def fake_run(arguments: list[str], **_kwargs: object) -> object:
        calls.append(arguments)
        if len(calls) < 3:
            raise pull_financial_snapshot.subprocess.CalledProcessError(1, arguments)
        partial.write_bytes(b"complete")
        return object()

    monkeypatch.setattr(
        pull_financial_snapshot,
        "_require_rsync_append_verify",
        accept_append_verify,
    )
    monkeypatch.setattr(pull_financial_snapshot.subprocess, "run", fake_run)
    monkeypatch.setattr(pull_financial_snapshot, "sleep", delays.append)

    attempts = pull_financial_snapshot._run_resumable_rsync(
        rsync_bin=rsync_bin,
        ssh_target="root@206.237.18.80",
        ssh_port=22,
        remote_snapshot="/opt/alphapilot-s2-dog/exports/financial-s2-latest.db",
        partial_path=partial,
    )

    assert attempts == 3
    assert calls[0] == calls[1] == calls[2]
    assert all(call[-1] == str(partial) for call in calls)
    assert delays == [1.0, 3.0]
    assert partial.read_bytes() == b"complete"
    assert os.stat(partial).st_mode & 0o777 == 0o600


def test_rsync_failure_retains_existing_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rsync_bin = _executable_stub(tmp_path / "rsync")
    partial = tmp_path / ".financial-s2-latest.db.rsync-partial"
    partial.write_bytes(b"verified-prefix")
    attempts = 0

    def accept_append_verify(_rsync_bin: Path) -> None:
        return None

    def fail_run(arguments: list[str], **_kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        raise pull_financial_snapshot.subprocess.CalledProcessError(12, arguments)

    def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(
        pull_financial_snapshot,
        "_require_rsync_append_verify",
        accept_append_verify,
    )
    monkeypatch.setattr(pull_financial_snapshot.subprocess, "run", fail_run)
    monkeypatch.setattr(pull_financial_snapshot, "sleep", no_sleep)

    with pytest.raises(RuntimeError, match="partial retained"):
        pull_financial_snapshot._run_resumable_rsync(
            rsync_bin=rsync_bin,
            ssh_target="root@206.237.18.80",
            ssh_port=22,
            remote_snapshot="/opt/alphapilot-s2-dog/exports/financial-s2-latest.db",
            partial_path=partial,
        )

    assert partial.read_bytes() == b"verified-prefix"
    assert os.stat(partial).st_mode & 0o777 == 0o600
    assert attempts == 4


def test_finalize_snapshot_is_sha_gated_and_atomic(tmp_path: Path) -> None:
    partial = tmp_path / ".financial-s2-latest.db.rsync-partial"
    destination = tmp_path / "financial-s2-latest.db"
    destination.write_bytes(b"accepted-old")
    partial.write_bytes(b"candidate")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        pull_financial_snapshot._finalize_snapshot(
            partial,
            destination,
            expected_sha256="not-the-candidate-sha",
        )

    assert destination.read_bytes() == b"accepted-old"
    assert partial.read_bytes() == b"candidate"

    expected = pull_financial_snapshot._sha256(partial)
    observed = pull_financial_snapshot._finalize_snapshot(
        partial,
        destination,
        expected_sha256=expected,
    )
    assert observed == expected
    assert destination.read_bytes() == b"candidate"
    assert not partial.exists()
    assert os.stat(destination).st_mode & 0o777 == 0o600


def test_rsync_capability_check_rejects_macos_openrsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rsync_bin = _executable_stub(tmp_path / "rsync")

    class _Completed:
        stdout = "openrsync: protocol version 29"
        stderr = ""

    def fake_run(arguments: list[str], **_kwargs: object) -> _Completed:
        assert arguments == [str(rsync_bin), "--help"]
        return _Completed()

    monkeypatch.setattr(pull_financial_snapshot.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="does not support required --append-verify"):
        pull_financial_snapshot._require_rsync_append_verify(rsync_bin)


def test_rsync_binary_must_be_explicit_executable_absolute_path() -> None:
    with pytest.raises(ValueError, match="path must be absolute"):
        pull_financial_snapshot._validated_rsync_binary(Path("rsync"))
    with pytest.raises(ValueError, match="required for rsync transfers"):
        pull_financial_snapshot._validated_rsync_binary(None)
