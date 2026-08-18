from __future__ import annotations

import atexit
import errno
import fcntl
import os
import socket
import struct
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import date
from pathlib import Path
from threading import Lock, local
from time import monotonic, sleep
from typing import IO, Any

import pandas as pd

from alphapilot.core.config import get_settings
from alphapilot.data.base import BarFrequency, DataProviderError, EmptyDailyBarsError

# BaoStock keeps one global socket per process, so calls are serialized. The
# advisory host lock extends that invariant across the API process and detached
# backfill runners sharing one public egress IP.
_baostock_lock = Lock()
_logged_in = False
_active_module: Any | None = None
_process_lock_handle: IO[bytes] | None = None
_usage_state = local()


class _BaoStockSessionMarker:
    __slots__ = ("finished", "used")

    def __init__(self) -> None:
        self.finished = False
        self.used = False


_current_session_scope: ContextVar[_BaoStockSessionMarker | None] = ContextVar(
    "alphapilot_baostock_session_scope",
    default=None,
)
_active_used_scopes: set[_BaoStockSessionMarker] = set()
_PROCESS_LOCK_ENV = "ALPHAPILOT_BAOSTOCK_LOCK_FILE"
_DEFAULT_PROCESS_LOCK_PATH = Path("/tmp/alphapilot-baostock.lock")
_SOCKS5_PROXY_ENV = "ALPHAPILOT_BAOSTOCK_SOCKS5_PROXY"
_SOCKET_TIMEOUT_ENV = "ALPHAPILOT_BAOSTOCK_SOCKET_TIMEOUT_SECONDS"
_LOCK_TIMEOUT_ENV = "ALPHAPILOT_BAOSTOCK_LOCK_TIMEOUT_SECONDS"
_DEFAULT_SOCKET_TIMEOUT_SECONDS = 2.0
_DEFAULT_LOCK_TIMEOUT_SECONDS = 1.0
_LOGIN_ATTEMPTS = 2

_FINANCIAL_QUERIES = {
    "profit": "query_profit_data",
    "growth": "query_growth_data",
    "cash_flow": "query_cash_flow_data",
    "balance": "query_balance_data",
}
_RELOGIN_ERROR_CODES = frozenset(
    {
        "10001001",  # not logged in
        "10002001",  # socket error
        "10002002",  # connect failed
        "10002003",  # connect timeout
        "10002004",  # connection closed
        "10002005",  # send failed
        "10002006",  # send timeout
        "10002007",  # receive failed
        "10002008",  # receive timeout
    }
)
_BLACKLIST_ERROR_CODE = "10001011"


class BaoStockRequestBudgetExceeded(DataProviderError):
    """Raised before a financial query would exceed the configured hard cap."""


class _EOFGuardSocket:
    """Delegate socket operations while turning a peer FIN into an exception.

    BaoStock 0.9.3 keeps calling ``recv`` when it returns ``b\"\"``.  A socket
    timeout does not cover that EOF state, so without this guard the library can
    spin forever while the connection sits in CLOSE_WAIT.
    """

    __slots__ = ("_connection", "_failure", "_request_deadline", "_request_timeout")

    def __init__(self, connection: socket.socket, *, request_timeout: float) -> None:
        self._connection = connection
        self._failure: BaseException | None = None
        self._request_deadline: float | None = None
        self._request_timeout = request_timeout

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    def _record_failure(self, exc: BaseException) -> None:
        if self._failure is None:
            self._failure = exc

    def _start_request(self) -> None:
        self._request_deadline = monotonic() + self._request_timeout

    def _set_remaining_timeout(self) -> None:
        deadline = self._request_deadline
        if deadline is None:
            deadline = monotonic() + self._request_timeout
            self._request_deadline = deadline
        timeout = deadline - monotonic()
        if timeout <= 0:
            failure = TimeoutError("BaoStock request deadline exceeded")
            self._record_failure(failure)
            raise failure
        self._connection.settimeout(timeout)

    def recv(self, size: int, flags: int = 0) -> bytes:
        try:
            self._set_remaining_timeout()
            payload = (
                self._connection.recv(size, flags) if flags else self._connection.recv(size)
            )
        except (OSError, TimeoutError) as exc:
            self._record_failure(exc)
            raise
        if not payload:
            failure = ConnectionError("BaoStock server closed the connection")
            self._record_failure(failure)
            raise failure
        return payload

    def send(self, payload: bytes, flags: int = 0) -> int:
        try:
            self._start_request()
            self._set_remaining_timeout()
            sent = (
                self._connection.send(payload, flags) if flags else self._connection.send(payload)
            )
        except (OSError, TimeoutError) as exc:
            self._record_failure(exc)
            raise
        if payload and sent == 0:
            failure = ConnectionError("BaoStock server closed the connection while sending")
            self._record_failure(failure)
            raise failure
        return sent

    def sendall(self, payload: bytes, flags: int = 0) -> None:
        try:
            self._start_request()
            self._set_remaining_timeout()
            if flags:
                self._connection.sendall(payload, flags)
            else:
                self._connection.sendall(payload)
        except (OSError, TimeoutError) as exc:
            self._record_failure(exc)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _process_lock_path() -> Path:
    configured = os.environ.get(_PROCESS_LOCK_ENV)
    path = Path(configured).expanduser() if configured else _DEFAULT_PROCESS_LOCK_PATH
    if not path.is_absolute():
        raise DataProviderError(f"{_PROCESS_LOCK_ENV} must be an absolute path: {path}")
    return path


def _socks5_endpoint() -> tuple[str, int] | None:
    configured = os.environ.get(_SOCKS5_PROXY_ENV, "").strip()
    if not configured:
        return None
    host, separator, raw_port = configured.rpartition(":")
    if not separator or not host or not raw_port.isdigit():
        raise DataProviderError(f"{_SOCKS5_PROXY_ENV} must use host:port format")
    port = int(raw_port)
    if not 1 <= port <= 65_535:
        raise DataProviderError(f"{_SOCKS5_PROXY_ENV} port is out of range")
    return host, port


def _socket_timeout() -> float:
    configured = os.environ.get(_SOCKET_TIMEOUT_ENV, "").strip()
    if not configured:
        return float(
            getattr(
                get_settings(),
                "baostock_socket_timeout_seconds",
                _DEFAULT_SOCKET_TIMEOUT_SECONDS,
            )
        )
    try:
        timeout = float(configured)
    except ValueError as exc:
        raise DataProviderError(f"{_SOCKET_TIMEOUT_ENV} must be numeric") from exc
    if not 1.0 <= timeout <= 120.0:
        raise DataProviderError(f"{_SOCKET_TIMEOUT_ENV} must be between 1 and 120 seconds")
    return timeout


def _lock_timeout() -> float:
    configured = os.environ.get(_LOCK_TIMEOUT_ENV, "").strip()
    if not configured:
        return float(
            getattr(
                get_settings(),
                "baostock_lock_timeout_seconds",
                _DEFAULT_LOCK_TIMEOUT_SECONDS,
            )
        )
    try:
        timeout = float(configured)
    except ValueError as exc:
        raise DataProviderError(f"{_LOCK_TIMEOUT_ENV} must be numeric") from exc
    if not 0.05 <= timeout <= 120.0:
        raise DataProviderError(f"{_LOCK_TIMEOUT_ENV} must be between 0.05 and 120 seconds")
    return timeout


def _set_remaining_socket_timeout(
    connection: socket.socket,
    deadline: float,
    *,
    operation: str,
) -> None:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError(f"{operation} deadline exceeded")
    connection.settimeout(remaining)


def _recv_exact(
    connection: socket.socket,
    size: int,
    *,
    deadline: float | None = None,
) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        if deadline is not None:
            _set_remaining_socket_timeout(
                connection,
                deadline,
                operation="SOCKS5 handshake",
            )
        chunk = connection.recv(size - len(payload))
        if not chunk:
            raise ConnectionError("SOCKS5 proxy closed the connection")
        payload.extend(chunk)
    return bytes(payload)


def _open_socks5_connection(
    proxy: tuple[str, int],
    target: tuple[str, int],
    *,
    timeout: float,
) -> socket.socket:
    deadline = monotonic() + timeout
    connection = socket.create_connection(proxy, timeout=timeout)
    try:
        _set_remaining_socket_timeout(connection, deadline, operation="SOCKS5 handshake")
        connection.sendall(b"\x05\x01\x00")
        if _recv_exact(connection, 2, deadline=deadline) != b"\x05\x00":
            raise ConnectionError("SOCKS5 proxy rejected no-auth negotiation")

        target_host, target_port = target
        encoded_host = target_host.encode("idna")
        if len(encoded_host) > 255:
            raise ValueError("SOCKS5 target hostname is too long")
        request = (
            b"\x05\x01\x00\x03"
            + bytes([len(encoded_host)])
            + encoded_host
            + struct.pack("!H", target_port)
        )
        _set_remaining_socket_timeout(connection, deadline, operation="SOCKS5 handshake")
        connection.sendall(request)
        version, reply, _reserved, address_type = _recv_exact(
            connection,
            4,
            deadline=deadline,
        )
        if version != 5 or reply != 0:
            raise ConnectionError(f"SOCKS5 connect failed with reply={reply}")
        if address_type == 1:
            _recv_exact(connection, 4, deadline=deadline)
        elif address_type == 3:
            _recv_exact(
                connection,
                _recv_exact(connection, 1, deadline=deadline)[0],
                deadline=deadline,
            )
        elif address_type == 4:
            _recv_exact(connection, 16, deadline=deadline)
        else:
            raise ConnectionError(f"SOCKS5 proxy returned unknown address type={address_type}")
        _recv_exact(connection, 2, deadline=deadline)
        connection.settimeout(timeout)
        return connection
    except Exception:
        connection.close()
        raise


def _open_direct_connection(target: tuple[str, int], *, timeout: float) -> socket.socket:
    connection = socket.create_connection(target, timeout=timeout)
    connection.settimeout(timeout)
    return connection


def _login_with_bounded_socket(bs: Any, proxy: tuple[str, int] | None) -> Any:
    """Give BaoStock both connect and receive operations a hard timeout."""

    from baostock.util import socketutil

    original_connect = socketutil.SocketUtil.connect
    timeout = _socket_timeout()

    def connect(_self: object) -> None:
        target = (
            str(socketutil.cons.BAOSTOCK_SERVER_IP),
            int(socketutil.cons.BAOSTOCK_SERVER_PORT),
        )
        connection = (
            _open_socks5_connection(proxy, target, timeout=timeout)
            if proxy is not None
            else _open_direct_connection(target, timeout=timeout)
        )
        socketutil.context.default_socket = _EOFGuardSocket(
            connection,
            request_timeout=timeout,
        )

    socketutil.SocketUtil.connect = connect
    try:
        return bs.login()
    finally:
        socketutil.SocketUtil.connect = original_connect


def _discard_default_socket() -> None:
    """Close BaoStock's process-global socket even when its logout path fails."""

    try:
        from baostock.common import context as baostock_context
    except ImportError:
        return
    connection = getattr(baostock_context, "default_socket", None)
    if connection is not None:
        with suppress(Exception):
            connection.shutdown(socket.SHUT_RDWR)
        with suppress(Exception):
            connection.close()
    baostock_context.default_socket = None


def _acquire_process_lock() -> None:
    """Refuse to create a second BaoStock socket from the same host."""

    global _process_lock_handle
    if _process_lock_handle is not None:
        return

    path = _process_lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
    except OSError as exc:
        raise DataProviderError(f"BaoStock process lock could not be opened: {path}") from exc
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise DataProviderError(
                "BaoStock process lock is held by another local process; "
                "refusing a second connection"
            ) from exc
        raise DataProviderError(f"BaoStock process lock could not be acquired: {path}") from exc
    _process_lock_handle = handle


def _release_process_lock() -> None:
    global _process_lock_handle
    handle = _process_lock_handle
    _process_lock_handle = None
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _close_baostock_session_locked() -> None:
    global _active_module, _logged_in
    module = _active_module
    _active_module = None
    _logged_in = False
    try:
        if module is not None:
            logout = getattr(module, "logout", None)
            if callable(logout):
                # Connection cleanup must never hide the caller's real result.
                with suppress(Exception):
                    logout()
    finally:
        try:
            _discard_default_socket()
        finally:
            _release_process_lock()


def _invalidate_baostock_session_locked() -> None:
    """Drop a broken session without sending another request on its socket."""

    global _active_module, _logged_in
    _active_module = None
    _logged_in = False
    try:
        _discard_default_socket()
    finally:
        _release_process_lock()


def _default_socket_failure() -> BaseException | None:
    try:
        from baostock.common import context as baostock_context
    except ImportError:
        return None
    connection = getattr(baostock_context, "default_socket", None)
    failure = getattr(connection, "failure", None)
    return failure if isinstance(failure, BaseException) else None


def _result_invalidates_session(result: Any) -> bool:
    error_code = str(getattr(result, "error_code", ""))
    error_message = str(getattr(result, "error_msg", ""))
    return error_code in _RELOGIN_ERROR_CODES or "未登录" in error_message


def _invalidate_failed_result_locked(result: Any) -> None:
    if _result_invalidates_session(result):
        _invalidate_baostock_session_locked()


def _finish_completed_session_scopes_locked() -> None:
    finished = {marker for marker in _active_used_scopes if marker.finished}
    if not finished:
        return
    _active_used_scopes.difference_update(finished)
    if not _active_used_scopes:
        # This path can run in an async dependency finalizer.  Never call the
        # vendor logout there: it performs another blocking network round-trip.
        _invalidate_baostock_session_locked()


@contextmanager
def _baostock_locked(operation: str) -> Iterator[None]:
    """Bound lock contention and invalidate a socket after transport failure."""

    timeout = _lock_timeout()
    if not _baostock_lock.acquire(timeout=timeout):
        raise DataProviderError(
            f"BaoStock in-process lock timed out after {timeout:g}s during {operation}"
        )
    try:
        # A scope finalizer can lose a try-lock race just after the previous
        # owner ran its sweep.  Sweep again before any caller reuses a session.
        _finish_completed_session_scopes_locked()
        try:
            yield
        except Exception as exc:
            failure = _default_socket_failure()
            if failure is not None:
                _invalidate_baostock_session_locked()
                raise DataProviderError(
                    f"BaoStock transport failed during {operation}: {failure}"
                ) from failure
            if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
                _invalidate_baostock_session_locked()
                raise DataProviderError(
                    f"BaoStock transport failed during {operation}: {exc}"
                ) from exc
            raise
        else:
            failure = _default_socket_failure()
            if failure is not None:
                _invalidate_baostock_session_locked()
                raise DataProviderError(
                    f"BaoStock transport failed during {operation}: {failure}"
                ) from failure
    finally:
        try:
            _finish_completed_session_scopes_locked()
        finally:
            _baostock_lock.release()


def close_baostock_session() -> None:
    """Close the shared socket and release the per-host connection lock."""

    with _baostock_locked("session close"):
        _close_baostock_session_locked()
    _usage_state.used = False


def close_baostock_session_if_used() -> None:
    """Close only when the current JobSpec thread touched BaoStock."""

    if not bool(getattr(_usage_state, "used", False)):
        return
    try:
        close_baostock_session()
    finally:
        _usage_state.used = False


@contextmanager
def baostock_session_scope() -> Iterator[None]:
    """Release BaoStock after the last overlapping scope that actually used it.

    A mutable marker is stored in a ContextVar so FastAPI's async dependency
    context and its AnyIO worker thread observe the same usage state.  Unused
    requests do nothing, while overlapping API requests and jobs cannot close
    the process-global BaoStock socket out from under one another.
    """

    marker = _BaoStockSessionMarker()
    token = _current_session_scope.set(marker)
    try:
        yield
    finally:
        _current_session_scope.reset(token)
        if marker.used:
            marker.finished = True
            # Async dependency finalizers run on the event-loop thread.  They
            # must never queue behind a slow BaoStock recv or call logout.  If
            # the lock is busy, its current owner observes ``finished`` and
            # performs the local-only cleanup immediately before releasing it.
            if _baostock_lock.acquire(blocking=False):
                try:
                    _finish_completed_session_scopes_locked()
                finally:
                    _baostock_lock.release()


atexit.register(close_baostock_session)


class BaoStockMarketDataProvider:
    """A-share daily history from BaoStock; it has no real-time snapshot."""

    name = "baostock"

    def __init__(self) -> None:
        self._financial_query_count = 0
        self._financial_query_limit: int | None = None

    @property
    def financial_query_count(self) -> int:
        """Return actual financial query calls made by this provider instance."""

        return self._financial_query_count

    def set_financial_query_limit(self, limit: int | None) -> None:
        """Set a hard per-process cap enforced immediately before every query."""

        if limit is not None and limit <= 0:
            raise ValueError("financial query limit must be positive")
        self._financial_query_limit = limit

    def _call_financial_query(self, query: Any, **kwargs: object) -> Any:
        if (
            self._financial_query_limit is not None
            and self._financial_query_count >= self._financial_query_limit
        ):
            raise BaoStockRequestBudgetExceeded(
                "BaoStock financial query budget exhausted before provider call: "
                f"used={self._financial_query_count}, "
                f"limit={self._financial_query_limit}"
            )
        self._financial_query_count += 1
        return query(**kwargs)

    @staticmethod
    def _module() -> Any:
        try:
            import baostock as bs
        except ImportError as exc:
            raise DataProviderError(
                'BaoStock is not installed. Run: pip install -e ".[cn-data]"'
            ) from exc
        return bs

    @staticmethod
    def _code(symbol: str) -> str:
        upper = symbol.upper().replace(" ", "")
        if "." in upper:
            market, digits = upper.split(".", 1)
            if market in {"SH", "SZ"} and digits.isdigit() and len(digits) == 6:
                return f"{market.lower()}.{digits}"
            raise DataProviderError(f"Unsupported BaoStock symbol: {symbol}")
        digits = "".join(character for character in upper if character.isdigit())
        if len(digits) != 6:
            raise DataProviderError(f"Unsupported A-share symbol: {symbol}")
        prefix = "sh" if digits.startswith(("5", "6", "9")) else "sz"
        return f"{prefix}.{digits}"

    def _ensure_login(self, bs: Any) -> None:
        global _active_module, _logged_in
        marker = _current_session_scope.get()
        if marker is None:
            _usage_state.used = True
        else:
            marker.used = True
            _active_used_scopes.add(marker)
        if _logged_in:
            return
        proxy = _socks5_endpoint()
        _acquire_process_lock()
        last_error_code = "unknown"
        last_error = "unknown error"
        for attempt in range(_LOGIN_ATTEMPTS):
            try:
                result = _login_with_bounded_socket(bs, proxy)
                failure = _default_socket_failure()
                if failure is not None:
                    raise failure
            except Exception as exc:
                last_error_code = type(exc).__name__
                last_error = str(exc) or repr(exc)
                _discard_default_socket()
                if attempt + 1 < _LOGIN_ATTEMPTS:
                    sleep(float(attempt + 1))
                continue
            error_code = str(result.error_code)
            if error_code == "0":
                _logged_in = True
                _active_module = bs
                return
            last_error_code = error_code
            last_error = str(result.error_msg)
            _discard_default_socket()
            if error_code == _BLACKLIST_ERROR_CODE:
                _close_baostock_session_locked()
                raise DataProviderError(f"BaoStock login failed ({last_error_code}): {last_error}")
            if attempt + 1 < _LOGIN_ATTEMPTS:
                sleep(float(attempt + 1))
        _close_baostock_session_locked()
        raise DataProviderError(
            f"BaoStock login failed after {_LOGIN_ATTEMPTS} attempts "
            f"({last_error_code}): {last_error}"
        )

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return self.get_bars(symbol, start, end, "d")

    def get_adjusted_closes(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """Return stable backward-adjusted closes for factor reconstruction."""

        bs = self._module()
        code = self._code(symbol)
        with _baostock_locked("adjusted-close query"):
            self._ensure_login(bs)
            result = bs.query_history_k_data_plus(
                code,
                "date,close",
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency="d",
                adjustflag="1",
            )
            if result.error_code != "0":
                _invalidate_failed_result_locked(result)
                raise DataProviderError(
                    f"BaoStock adjusted-close query failed for {code}: {result.error_msg}"
                )
            rows: list[list[str]] = []
            while result.next():
                rows.append(result.get_row_data())
            if result.error_code != "0":
                _invalidate_failed_result_locked(result)
                raise DataProviderError(
                    f"BaoStock adjusted-close pagination failed for {code}: "
                    f"{result.error_msg}"
                )
        if not rows:
            raise EmptyDailyBarsError(f"BaoStock returned no adjusted closes for {code}")
        frame = pd.DataFrame(rows, columns=["date", "close"])
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        parsed = frame.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
        if parsed.empty:
            raise EmptyDailyBarsError(f"BaoStock returned no valid adjusted closes for {code}")
        return parsed

    def get_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        frequency: BarFrequency,
    ) -> pd.DataFrame:
        if frequency not in {"d", "w", "m"}:
            raise ValueError(f"Unsupported BaoStock bar frequency: {frequency}")
        bs = self._module()
        code = self._code(symbol)
        with _baostock_locked(f"{frequency}-bar query"):
            self._ensure_login(bs)
            rs = bs.query_history_k_data_plus(
                code,
                "date,open,high,low,close,volume,amount",
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                frequency=frequency,
                adjustflag="3",  # unadjusted, matching the AKShare adapter
            )
            if rs.error_code != "0":
                _invalidate_failed_result_locked(rs)
                raise DataProviderError(f"BaoStock query failed for {code}: {rs.error_msg}")
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rs.error_code != "0":
                _invalidate_failed_result_locked(rs)
                raise DataProviderError(
                    f"BaoStock pagination failed for {code}: {rs.error_msg}"
                )

        if not rows:
            label = {"d": "daily", "w": "weekly", "m": "monthly"}[frequency]
            raise EmptyDailyBarsError(f"BaoStock returned no {label} bars for {code}")
        frame = pd.DataFrame(
            rows, columns=["date", "open", "high", "low", "close", "volume", "amount"]
        )
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame[["volume", "amount"]] = frame[["volume", "amount"]].fillna(0.0)
        result = (
            frame.dropna(subset=["date", "open", "high", "low", "close"])
            .sort_values("date")
            .reset_index(drop=True)
        )
        if result.empty:
            label = {"d": "daily", "w": "weekly", "m": "monthly"}[frequency]
            raise EmptyDailyBarsError(f"BaoStock returned no valid {label} bars for {code}")
        return result

    def get_stock_universe(self, trade_date: date) -> pd.DataFrame:
        """Return BaoStock's active security list for an actual trading day."""

        bs = self._module()
        with _baostock_locked("stock-universe query"):
            self._ensure_login(bs)
            rs = bs.query_all_stock(day=trade_date.isoformat())
            if rs.error_code != "0":
                _invalidate_failed_result_locked(rs)
                raise DataProviderError(f"BaoStock universe query failed: {rs.error_msg}")
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rs.error_code != "0":
                _invalidate_failed_result_locked(rs)
                raise DataProviderError(
                    f"BaoStock universe pagination failed: {rs.error_msg}"
                )
            columns = [str(field) for field in rs.fields]
        if not rows:
            raise DataProviderError(f"BaoStock returned no securities for {trade_date.isoformat()}")
        return pd.DataFrame(rows, columns=columns)

    def get_stock_industries(self) -> pd.DataFrame:
        """Return the current CSRC industry mapping through the shared connection."""

        bs = self._module()
        with _baostock_locked("stock-industry query"):
            self._ensure_login(bs)
            rs = bs.query_stock_industry()
            if rs.error_code != "0":
                _invalidate_failed_result_locked(rs)
                raise DataProviderError(f"BaoStock industry query failed: {rs.error_msg}")
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rs.error_code != "0":
                _invalidate_failed_result_locked(rs)
                raise DataProviderError(
                    f"BaoStock industry pagination failed: {rs.error_msg}"
                )
            columns = [str(field) for field in rs.fields]
        if not rows:
            raise DataProviderError("BaoStock returned no stock industries")
        return pd.DataFrame(rows, columns=columns)

    def _get_quarterly_financial_frames(
        self,
        symbol: str,
        year: int,
        quarter: int,
        datasets: tuple[str, ...],
    ) -> dict[str, pd.DataFrame]:
        if year < 1990 or quarter not in {1, 2, 3, 4}:
            raise ValueError("financial year/quarter is out of range")
        digits = "".join(character for character in symbol if character.isdigit())
        if len(digits) != 6 or digits.startswith(("4", "8", "92")):
            raise DataProviderError(
                f"BaoStock quarterly financials do not support symbol: {symbol}"
            )

        bs = self._module()
        code = self._code(symbol)
        frames: dict[str, pd.DataFrame] = {}
        with _baostock_locked("quarterly-financial query"):
            self._ensure_login(bs)
            for dataset in datasets:
                method_name = _FINANCIAL_QUERIES[dataset]
                query = getattr(bs, method_name, None)
                if not callable(query):
                    raise DataProviderError(
                        f"BaoStock module is missing financial query: {method_name}"
                    )
                result = self._call_financial_query(
                    query,
                    code=code,
                    year=year,
                    quarter=quarter,
                )
                if _result_invalidates_session(result):
                    _invalidate_baostock_session_locked()
                    self._ensure_login(bs)
                    result = self._call_financial_query(
                        query,
                        code=code,
                        year=year,
                        quarter=quarter,
                    )
                if result.error_code != "0":
                    _invalidate_failed_result_locked(result)
                    raise DataProviderError(
                        f"BaoStock {dataset} query failed for {code}/{year}Q{quarter}: "
                        f"({result.error_code}) {result.error_msg}"
                    )
                rows: list[list[str]] = []
                while result.next():
                    rows.append(result.get_row_data())
                if result.error_code != "0":
                    _invalidate_failed_result_locked(result)
                    raise DataProviderError(
                        f"BaoStock {dataset} pagination failed for "
                        f"{code}/{year}Q{quarter}: "
                        f"({result.error_code}) {result.error_msg}"
                    )
                columns = [str(field) for field in result.fields]
                frames[dataset] = pd.DataFrame(rows, columns=columns)
        return frames

    def probe_financial_query(
        self,
        symbol: str = "600519",
        year: int = 2025,
        quarter: int = 1,
    ) -> int:
        """Run exactly one real financial query without a query-level retry.

        Detached backfills use this before consuming their daily shard budget.
        A successful login is not sufficient during BaoStock's maintenance
        window because login can remain healthy while every query times out.
        """

        if year < 1990 or quarter not in {1, 2, 3, 4}:
            raise ValueError("financial probe year/quarter is out of range")
        bs = self._module()
        code = self._code(symbol)
        with _baostock_locked("financial-query probe"):
            self._ensure_login(bs)
            query = getattr(bs, "query_profit_data", None)
            if not callable(query):
                raise DataProviderError(
                    "BaoStock module is missing financial probe query: query_profit_data"
                )
            result = self._call_financial_query(
                query,
                code=code,
                year=year,
                quarter=quarter,
            )
            if result.error_code != "0":
                _invalidate_failed_result_locked(result)
                raise DataProviderError(
                    f"BaoStock financial query probe failed for {code}/{year}Q{quarter}: "
                    f"({result.error_code}) {result.error_msg}"
                )
            rows = 0
            while result.next():
                result.get_row_data()
                rows += 1
            if result.error_code != "0":
                _invalidate_failed_result_locked(result)
                raise DataProviderError(
                    f"BaoStock financial query probe pagination failed for "
                    f"{code}/{year}Q{quarter}: "
                    f"({result.error_code}) {result.error_msg}"
                )
            return rows

    def get_quarterly_financials(
        self, symbol: str, year: int, quarter: int
    ) -> dict[str, pd.DataFrame]:
        """Return BaoStock's four quarterly financial datasets under one socket lock."""

        return self._get_quarterly_financial_frames(
            symbol,
            year,
            quarter,
            tuple(_FINANCIAL_QUERIES),
        )

    def get_quarterly_profit(self, symbol: str, year: int, quarter: int) -> pd.DataFrame:
        """Return only the profit dataset for prior-year revenue derivation."""

        return self._get_quarterly_financial_frames(
            symbol,
            year,
            quarter,
            ("profit",),
        )["profit"]

    def get_dividend_data(self, symbol: str, year: int) -> pd.DataFrame:
        """Return one report year's dividend records, including empty results."""

        bs = self._module()
        code = self._code(symbol)
        with _baostock_locked("dividend query"):
            self._ensure_login(bs)
            rs = bs.query_dividend_data(code=code, year=str(year), yearType="report")
            if rs.error_code != "0":
                _invalidate_failed_result_locked(rs)
                raise DataProviderError(
                    f"BaoStock dividend query failed for {code}/{year}: {rs.error_msg}"
                )
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rs.error_code != "0":
                _invalidate_failed_result_locked(rs)
                raise DataProviderError(
                    f"BaoStock dividend pagination failed for {code}/{year}: "
                    f"{rs.error_msg}"
                )
            columns = [str(field) for field in rs.fields]
        return pd.DataFrame(rows, columns=columns)

    def get_forecast_reports(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Return BaoStock earnings-preview publications for a bounded period."""

        bs = self._module()
        code = self._code(symbol)
        with _baostock_locked("forecast-report query"):
            self._ensure_login(bs)
            rs = bs.query_forecast_report(
                code=code,
                start_date=start.isoformat(),
                end_date=end.isoformat(),
            )
            if rs.error_code != "0":
                _invalidate_failed_result_locked(rs)
                raise DataProviderError(
                    f"BaoStock forecast query failed for {code}: {rs.error_msg}"
                )
            rows: list[list[str]] = []
            while rs.next():
                rows.append(rs.get_row_data())
            if rs.error_code != "0":
                _invalidate_failed_result_locked(rs)
                raise DataProviderError(
                    f"BaoStock forecast pagination failed for {code}: {rs.error_msg}"
                )
            columns = [str(field) for field in rs.fields]
        return pd.DataFrame(rows, columns=columns)

    def get_snapshot(self, symbols: list[str]) -> pd.DataFrame:
        raise DataProviderError(
            "BaoStock has no real-time snapshot; route snapshots to futu or akshare."
        )
