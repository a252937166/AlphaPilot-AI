from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from time import sleep
from typing import Any
from urllib.parse import quote

from alphapilot.jobs.financial_transfer import import_financial_snapshot

_SSH_TARGET_PATTERN = re.compile(r"^[A-Za-z0-9._@:-]+$")
_REMOTE_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._/-]+$")
_EXPECTED_TRADING_SAFETY_COUNTS = (1, 1)
_TRANSFER_MODES = ("scp", "rsync")
_RSYNC_RETRY_DELAYS_SECONDS = (1.0, 3.0, 5.0)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Snapshot the remote S2 staging DB, pull it, and merge missing rows locally."
    )
    parser.add_argument("--ssh-target", required=True)
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--remote-root", type=Path, default=Path("/opt/alphapilot-s2"))
    parser.add_argument(
        "--remote-exporter",
        type=Path,
        help=(
            "Optional absolute remote wrapper that prints the export manifest. "
            "Use this when the staging host runs the exporter in a container."
        ),
    )
    parser.add_argument("--target-db", type=Path, required=True)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument(
        "--transfer-mode",
        choices=_TRANSFER_MODES,
        default="scp",
        help="Snapshot transport; rsync is reserved for the resumable Dog Cloud pull.",
    )
    parser.add_argument(
        "--rsync-bin",
        type=Path,
        help=(
            "Absolute GNU rsync path for --transfer-mode=rsync. It must support "
            "--append-verify; macOS /usr/bin/rsync does not."
        ),
    )
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _validated_ssh_port(port: int) -> int:
    if not 1 <= port <= 65_535:
        raise ValueError(f"SSH port must be between 1 and 65535: {port}")
    return port


def _ssh_transport_options(port: int) -> list[str]:
    return [
        "-p",
        str(_validated_ssh_port(port)),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]


def _scp_transport_options(port: int) -> list[str]:
    return [
        # CentOS 7's old SFTP server has repeatedly stalled near EOF. The
        # validated absolute remote path makes legacy SCP mode safe here, and
        # keeps a failed transfer safely retryable by the next idempotent run.
        "-O",
        "-P",
        str(_validated_ssh_port(port)),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]


def _validated_transfer_source(ssh_target: str, remote_snapshot: str) -> str:
    if not _SSH_TARGET_PATTERN.fullmatch(ssh_target):
        raise ValueError(f"invalid SSH target: {ssh_target!r}")
    if not _REMOTE_PATH_PATTERN.fullmatch(remote_snapshot):
        raise ValueError(f"invalid remote snapshot path: {remote_snapshot!r}")
    return f"{ssh_target}:{remote_snapshot}"


def _validated_rsync_binary(path: Path | None) -> Path:
    if path is None:
        raise ValueError(
            "--rsync-bin is required for rsync transfers so the audited GNU rsync "
            "binary is explicit"
        )
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise ValueError(f"rsync binary path must be absolute: {expanded}")
    resolved = expanded.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f"rsync binary must be an executable absolute file: {resolved}")
    return resolved


def _require_rsync_append_verify(rsync_bin: Path) -> None:
    try:
        completed = subprocess.run(
            [str(rsync_bin), "--help"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"could not verify rsync --append-verify capability: {rsync_bin}"
        ) from exc
    help_text = f"{completed.stdout}\n{completed.stderr}"
    if "--append-verify" not in help_text:
        raise RuntimeError(
            f"rsync binary does not support required --append-verify: {rsync_bin}"
        )


def _rsync_command(
    *,
    rsync_bin: Path,
    ssh_target: str,
    ssh_port: int,
    remote_snapshot: str,
    partial_path: Path,
) -> list[str]:
    source = _validated_transfer_source(ssh_target, remote_snapshot)
    ssh_command = shlex.join(["/usr/bin/ssh", *_ssh_transport_options(ssh_port)])
    return [
        str(rsync_bin),
        "-z",
        "--partial",
        "--append-verify",
        "-e",
        ssh_command,
        "--",
        source,
        str(partial_path),
    ]


def _run_resumable_rsync(
    *,
    rsync_bin: Path | None,
    ssh_target: str,
    ssh_port: int,
    remote_snapshot: str,
    partial_path: Path,
) -> int:
    resolved_rsync = _validated_rsync_binary(rsync_bin)
    command = _rsync_command(
        rsync_bin=resolved_rsync,
        ssh_target=ssh_target,
        ssh_port=ssh_port,
        remote_snapshot=remote_snapshot,
        partial_path=partial_path,
    )
    _require_rsync_append_verify(resolved_rsync)
    if partial_path.is_symlink() or (partial_path.exists() and not partial_path.is_file()):
        raise RuntimeError(f"rsync partial path is not a regular file: {partial_path}")
    partial_path.touch(mode=0o600, exist_ok=True)
    os.chmod(partial_path, 0o600)
    attempts = len(_RSYNC_RETRY_DELAYS_SECONDS) + 1
    for attempt in range(1, attempts + 1):
        try:
            subprocess.run(
                command,
                check=True,
                timeout=900,
            )
            if not partial_path.is_file():
                raise RuntimeError(
                    f"rsync reported success without a partial file: {partial_path}"
                )
            os.chmod(partial_path, 0o600)
            return attempt
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            if partial_path.exists() and partial_path.is_file():
                os.chmod(partial_path, 0o600)
            if attempt == attempts:
                raise RuntimeError(
                    "resumable rsync failed after "
                    f"{attempts} attempts; partial retained at {partial_path}"
                ) from exc
            sleep(_RSYNC_RETRY_DELAYS_SECONDS[attempt - 1])
    raise AssertionError("unreachable")


def _finalize_snapshot(
    downloaded_path: Path,
    destination: Path,
    *,
    expected_sha256: str,
) -> str:
    local_sha256 = _sha256(downloaded_path)
    if local_sha256 != expected_sha256:
        raise RuntimeError(
            "remote/local snapshot checksum mismatch: "
            f"remote={expected_sha256}, local={local_sha256}"
        )
    os.chmod(downloaded_path, 0o600)
    os.replace(downloaded_path, destination)
    return local_sha256


def _require_production_trading_safety_counts(path: Path) -> tuple[int, int]:
    target_path = path.expanduser().resolve()
    if not target_path.is_file():
        raise FileNotFoundError(f"target database does not exist: {target_path}")
    uri = f"file:{quote(str(target_path))}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            proposals = int(
                connection.execute("SELECT COUNT(*) FROM trade_proposals").fetchone()[0]
            )
            orders = int(
                connection.execute("SELECT COUNT(*) FROM broker_orders").fetchone()[0]
            )
    except sqlite3.Error as exc:
        raise RuntimeError(
            "target database is missing readable trading safety tables"
        ) from exc
    observed = (proposals, orders)
    if observed != _EXPECTED_TRADING_SAFETY_COUNTS:
        raise RuntimeError(
            "financial snapshot pull requires production trading safety counts "
            f"{_EXPECTED_TRADING_SAFETY_COUNTS}, observed={observed}"
        )
    return observed


def _run_remote_export(
    ssh_target: str,
    ssh_port: int,
    remote_root: Path,
    remote_exporter: Path | None,
) -> dict[str, Any]:
    remote_root_text = str(remote_root)
    if not _SSH_TARGET_PATTERN.fullmatch(ssh_target):
        raise ValueError(f"invalid SSH target: {ssh_target!r}")
    if not _REMOTE_PATH_PATTERN.fullmatch(remote_root_text):
        raise ValueError(f"invalid remote root: {remote_root_text!r}")

    remote_database = remote_root / "data/alphapilot-s2.db"
    remote_snapshot = remote_root / "exports/financial-s2-latest.db"
    if remote_exporter is None:
        remote_python = remote_root / ".venv/bin/python"
        remote_script = remote_root / "app/scripts/export_financial_snapshot.py"
        remote_command = shlex.join(
            [
                str(remote_python),
                str(remote_script),
                "--source-db",
                str(remote_database),
                "--output-db",
                str(remote_snapshot),
            ]
        )
    else:
        remote_exporter_text = str(remote_exporter)
        if not _REMOTE_PATH_PATTERN.fullmatch(remote_exporter_text):
            raise ValueError(f"invalid remote exporter: {remote_exporter_text!r}")
        remote_command = shlex.join([remote_exporter_text])
    completed = subprocess.run(
        [
            "/usr/bin/ssh",
            *_ssh_transport_options(ssh_port),
            ssh_target,
            remote_command,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not output_lines:
        raise RuntimeError("remote financial snapshot command returned no manifest")
    manifest = json.loads(output_lines[-1])
    if not isinstance(manifest, dict) or not isinstance(manifest.get("sha256"), str):
        raise RuntimeError(f"invalid remote financial snapshot manifest: {manifest!r}")
    manifest["remote_snapshot"] = str(remote_snapshot)
    return manifest


def pull_and_import(
    *,
    ssh_target: str,
    ssh_port: int = 22,
    remote_root: Path,
    remote_exporter: Path | None,
    target_db: Path,
    snapshot_dir: Path,
    transfer_mode: str = "scp",
    rsync_bin: Path | None = None,
) -> dict[str, Any]:
    if transfer_mode not in _TRANSFER_MODES:
        raise ValueError(f"unsupported financial snapshot transfer mode: {transfer_mode}")
    if transfer_mode == "scp" and rsync_bin is not None:
        raise ValueError("--rsync-bin may only be used with --transfer-mode=rsync")
    local_snapshot_dir = snapshot_dir.expanduser().resolve()
    local_snapshot_dir.mkdir(parents=True, exist_ok=True)
    lock_path = local_snapshot_dir / ".financial-s2-pull.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another financial snapshot pull is already running") from exc

        safety_before = _require_production_trading_safety_counts(target_db)
        remote_manifest = _run_remote_export(
            ssh_target,
            ssh_port,
            remote_root,
            remote_exporter,
        )
        destination = local_snapshot_dir / "financial-s2-latest.db"
        transfer_attempts = 1
        if transfer_mode == "rsync":
            partial_path = local_snapshot_dir / ".financial-s2-latest.db.rsync-partial"
            transfer_attempts = _run_resumable_rsync(
                rsync_bin=rsync_bin,
                ssh_target=ssh_target,
                ssh_port=ssh_port,
                remote_snapshot=str(remote_manifest["remote_snapshot"]),
                partial_path=partial_path,
            )
            # A checksum mismatch keeps the fixed partial for the next
            # --append-verify run; it must never replace the accepted snapshot.
            local_sha256 = _finalize_snapshot(
                partial_path,
                destination,
                expected_sha256=str(remote_manifest["sha256"]),
            )
        else:
            with tempfile.NamedTemporaryFile(
                prefix=".financial-s2-latest.",
                suffix=".db",
                dir=local_snapshot_dir,
                delete=False,
            ) as temp_handle:
                temp_path = Path(temp_handle.name)
            try:
                subprocess.run(
                    [
                        "/usr/bin/scp",
                        "-C",
                        "-q",
                        *_scp_transport_options(ssh_port),
                        _validated_transfer_source(
                            ssh_target,
                            str(remote_manifest["remote_snapshot"]),
                        ),
                        str(temp_path),
                    ],
                    check=True,
                    timeout=600,
                )
                local_sha256 = _finalize_snapshot(
                    temp_path,
                    destination,
                    expected_sha256=str(remote_manifest["sha256"]),
                )
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

        imported = import_financial_snapshot(destination, target_db)
        safety_after = _require_production_trading_safety_counts(target_db)
        return {
            "remote": remote_manifest,
            "local_snapshot": str(destination),
            "local_sha256": local_sha256,
            "transfer_mode": transfer_mode,
            "transfer_attempts": transfer_attempts,
            "trading_safety_counts_before": safety_before,
            "trading_safety_counts_after": safety_after,
            "result": imported,
        }


def main() -> int:
    arguments = _arguments()
    result = pull_and_import(
        ssh_target=arguments.ssh_target,
        ssh_port=arguments.ssh_port,
        remote_root=arguments.remote_root,
        remote_exporter=arguments.remote_exporter,
        target_db=arguments.target_db,
        snapshot_dir=arguments.snapshot_dir,
        transfer_mode=arguments.transfer_mode,
        rsync_bin=arguments.rsync_bin,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
