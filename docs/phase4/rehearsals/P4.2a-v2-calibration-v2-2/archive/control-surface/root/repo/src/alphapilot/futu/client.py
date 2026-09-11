from __future__ import annotations

import base64
import inspect
import math
import socket
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, date, datetime
from enum import Enum
from functools import lru_cache
from queue import Empty, Full, Queue
from threading import RLock
from time import monotonic
from typing import Any

import numpy as np
import pandas as pd

from alphapilot.core.config import Settings, get_settings


class FutuClientError(RuntimeError):
    """Base error for the local Futu OpenD bridge."""


class FutuUnavailableError(FutuClientError):
    """The SDK or OpenD service is unavailable."""


class FutuCallValidationError(FutuClientError):
    """A method or argument cannot be safely mapped to the SDK."""


class FutuMethodNotAllowedError(FutuClientError):
    """The requested SDK method is outside the audited surface."""


class FutuSDKError(FutuClientError):
    """OpenD returned RET_ERROR for an otherwise valid request."""


class FutuFeatureDisabledError(FutuClientError):
    """The requested capability is present but disabled by configuration."""


# This list mirrors the public read/query surface on futu-api 10.9.6908.
# Keeping it explicit prevents internal socket, callback, and test methods from
# becoming remotely callable merely because a later SDK adds a similarly named method.
QUOTE_READ_METHODS = frozenset(
    {
        "filter_competition",
        "get_ark_active_transaction",
        "get_ark_fund_holding",
        "get_ark_stock_dynamic",
        "get_broker_queue",
        "get_capital_distribution",
        "get_capital_flow",
        "get_code_change",
        "get_company_executive_background",
        "get_company_executives",
        "get_company_operational_efficiency",
        "get_company_profile",
        "get_corporate_actions_buybacks",
        "get_corporate_actions_dividends",
        "get_corporate_actions_stock_splits",
        "get_cur_kline",
        "get_daily_short_volume",
        "get_delay_statistics",
        "get_derivative_unusual",
        "get_dividend_calendar",
        "get_dividend_rank",
        "get_earnings_beat_rank",
        "get_earnings_calendar",
        "get_economic_calendar",
        "get_event_contract",
        "get_event_contract_category",
        "get_event_contract_event_list",
        "get_event_contract_kline",
        "get_event_contract_milestone_list",
        "get_event_contract_order_book",
        "get_event_contract_series_list",
        "get_event_contract_snapshot",
        "get_event_contract_ticker",
        "get_fed_watch_dot_plot",
        "get_fed_watch_target_rate",
        "get_financial_unusual",
        "get_financials_earnings_price_history",
        "get_financials_earnings_price_move",
        "get_financials_revenue_breakdown",
        "get_financials_statements",
        "get_future_info",
        "get_global_state",
        "get_heat_map_data",
        "get_high_dividend_soe_rank",
        "get_history_kl_quota",
        "get_holding_change_list",
        "get_hot_list",
        "get_indicator_list",
        "get_industrial_chain_by_plate",
        "get_industrial_chain_detail",
        "get_industrial_chain_list",
        "get_industrial_plate_info",
        "get_industrial_plate_stock",
        "get_insider_holder_list",
        "get_insider_trade_list",
        "get_institution_distribution",
        "get_institution_holding_change",
        "get_institution_holding_list",
        "get_institution_list",
        "get_institution_profile",
        "get_ipo_list",
        "get_login_user_id",
        "get_macro_indicator_history",
        "get_macro_indicator_list",
        "get_market_snapshot",
        "get_market_state",
        "get_option_chain",
        "get_option_earnings_screener",
        "get_option_event",
        "get_option_event_alert",
        "get_option_exercise_probability",
        "get_option_expiration_date",
        "get_option_market_statistic",
        "get_option_quote",
        "get_option_rank",
        "get_option_screen",
        "get_option_seller_screener",
        "get_option_strategy",
        "get_option_strategy_analysis",
        "get_option_strategy_spread",
        "get_option_underlying_his_statistic",
        "get_option_underlying_his_volatility",
        "get_option_underlying_overview",
        "get_option_underlying_rank",
        "get_option_volatility",
        "get_option_zero_dte_contract",
        "get_option_zero_dte_screener",
        "get_order_book",
        "get_owner_plate",
        "get_period_change_rank",
        "get_plate_list",
        "get_plate_stock",
        "get_price_reminder",
        "get_rating_change",
        "get_referencestock_list",
        "get_rehab",
        "get_research_analyst_consensus",
        "get_research_morningstar_report",
        "get_research_rating_summary",
        "get_rise_fall_distribution",
        "get_rt_data",
        "get_rt_ticker",
        "get_search_news",
        "get_search_quote",
        "get_security_firm",
        "get_shareholders_holder_detail",
        "get_shareholders_holding_changes",
        "get_shareholders_institutional",
        "get_shareholders_overview",
        "get_short_interest",
        "get_short_selling_rank",
        "get_stock_basicinfo",
        "get_stock_filter",
        "get_stock_quote",
        "get_stock_screen",
        "get_technical_unusual",
        "get_top_movers_rank",
        "get_top_ten_buy_sell_brokers",
        "get_us_after_hours_rank",
        "get_us_overnight_rank",
        "get_us_pre_market_rank",
        "get_user_info",
        "get_user_security",
        "get_user_security_group",
        "get_valid_combo_list",
        "get_valuation_detail",
        "get_valuation_plate_stock_list",
        "get_warrant",
        "get_warrant_screen",
        "query_subscription",
        "request_combo_quotes",
        "request_history_event_contract_kline",
        "request_history_kline",
        "request_indicator_calc_async",
        "request_trading_days",
    }
)

QUOTE_SUBSCRIPTION_METHODS = frozenset(
    {
        "subscribe",
        "subscribe_event_contract",
        "unsubscribe",
        "unsubscribe_all",
        "unsubscribe_all_event_contract",
        "unsubscribe_event_contract",
    }
)

QUOTE_ACCOUNT_MUTATION_METHODS = frozenset(
    {
        "modify_user_security",
        "set_option_event_alert",
        "set_price_reminder",
        "verification",
    }
)

TRADE_QUERY_METHODS = frozenset(
    {
        "accinfo_query",
        "acctradinginfo_query",
        "comboorder_tradinginfo_query",
        "deal_list_query",
        "get_acc_cash_flow",
        "get_acc_list",
        "get_global_state",
        "get_margin_ratio",
        "history_deal_list_query",
        "history_order_list_query",
        "order_fee_query",
        "order_list_query",
        "position_list_query",
    }
)

TRADE_MUTATION_METHODS = frozenset(
    {
        "cancel_all_order",
        "change_order",
        "modify_order",
        "place_combo_order",
        "place_order",
    }
)

PERMANENTLY_BLOCKED_METHODS = frozenset({"unlock_trade"})

FUTU_VALUE_TYPES = frozenset(
    {
        "AccumulateFilter",
        "ComboLeg",
        "CustomIndicatorFilter",
        "DividendRankFilter",
        "EarningsBeatRankFilter",
        "EarningsCalendarFilter",
        "EarningsFilter",
        "FinancialFilter",
        "HighDividendSOERankFilter",
        "HotListFilter",
        "OptionDataFilter",
        "OptionEventAlertItem",
        "OptionEventFilter",
        "OptionEventSort",
        "OptionRankFilter",
        "OptionScreenRequest",
        "OptionStrategyLeg",
        "PatternFilter",
        "PeriodChangeRankFilter",
        "SellerFilter",
        "SimpleFilter",
        "SimpleRankFilter",
        "StockScreenRequest",
        "TimeFilter",
        "UnderlyingRankFilter",
        "WarrantRequest",
        "WarrantScreenRequest",
        "ZeroDteContractFilter",
        "ZeroDteFilter",
    }
)

TRADE_CONTEXTS = frozenset({"security", "future", "crypto"})

QUOTE_PUSH_HANDLERS = {
    "BrokerHandlerBase": "broker_queue",
    "CurKlineHandlerBase": "kline",
    "EventContractKlineHandlerBase": "event_contract_kline",
    "EventContractOrderBookHandlerBase": "event_contract_order_book",
    "EventContractTickerHandlerBase": "event_contract_ticker",
    "IndicatorCalcHandlerBase": "indicator_calc",
    "OptionEventHandlerBase": "option_event",
    "OrderBookHandlerBase": "order_book",
    "PriceReminderHandlerBase": "price_reminder",
    "RTDataHandlerBase": "rt_data",
    "StockQuoteHandlerBase": "stock_quote",
    "SysNotifyHandlerBase": "system",
    "TickerHandlerBase": "ticker",
}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return _json_safe(value.value)
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"__bytes_base64__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, np.ndarray):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, pd.DataFrame):
        records = [
            {str(key): _json_safe(item) for key, item in record.items()}
            for record in value.to_dict(orient="records")
        ]
        return {
            "type": "dataframe",
            "columns": [str(column) for column in value.columns],
            "row_count": len(records),
            "records": records,
        }
    if isinstance(value, pd.Series):
        return {str(key): _json_safe(item) for key, item in value.to_dict().items()}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            str(key): _json_safe(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return str(value)


class FutuClient:
    """Audited bridge over the full public Futu quote and trade context surfaces."""

    def __init__(self, settings: Settings, sdk_module: Any | None = None):
        self.settings = settings
        self._sdk_module_override = sdk_module
        self._quote_context: Any | None = None
        self._quote_handlers: list[Any] = []
        self._quote_lock = RLock()
        self._event_lock = RLock()
        self._event_subscribers: set[Queue[dict[str, Any]]] = set()
        self._event_sequence = 0
        self._status_lock = RLock()
        self._status_cached_at = 0.0
        self._status_cache: dict[str, Any] | None = None

    def _module(self) -> Any:
        if self._sdk_module_override is not None:
            return self._sdk_module_override
        try:
            import futu
        except ImportError as exc:
            raise FutuUnavailableError(
                'futu-api is not installed. Run: pip install -e ".[futu]"'
            ) from exc
        return futu

    def _require_opend_tcp(self) -> None:
        """Fail fast before the Futu SDK starts its own reconnect loop."""

        if self._sdk_module_override is not None:
            return
        try:
            with socket.create_connection(
                (self.settings.futu_host, self.settings.futu_port),
                timeout=0.35,
            ):
                return
        except OSError as exc:
            self.close()
            raise FutuUnavailableError(
                "无法连接 Futu OpenD"
                f"（{self.settings.futu_host}:{self.settings.futu_port}），"
                "请确认 OpenD 已启动。"
            ) from exc

    def _ensure_quote_context(self) -> Any:
        if not self.settings.futu_enable_quote:
            raise FutuFeatureDisabledError("Futu quote access is disabled by configuration.")
        if self._quote_context is None:
            futu = self._module()
            try:
                context = futu.OpenQuoteContext(
                    host=self.settings.futu_host,
                    port=self.settings.futu_port,
                    security_firm=self.settings.futu_security_firm,
                    ai_type=1,
                )
                self._install_quote_handlers(context, futu)
                self._quote_context = context
            except Exception as exc:
                raise FutuUnavailableError(f"Could not connect to Futu OpenD: {exc}") from exc
        return self._quote_context

    def _install_quote_handlers(self, context: Any, futu: Any) -> None:
        handlers: list[Any] = []
        for handler_base_name, event_type in QUOTE_PUSH_HANDLERS.items():
            handler_base = getattr(futu, handler_base_name, None)
            if handler_base is None:
                continue
            client = self

            def on_recv_rsp(
                handler_self: Any,
                response: Any,
                *,
                _handler_base: Any = handler_base,
                _event_type: str = event_type,
                _client: FutuClient = client,
            ) -> Any:
                result = _handler_base.on_recv_rsp(handler_self, response)
                if (
                    isinstance(result, tuple)
                    and len(result) >= 2
                    and result[0] == futu.RET_OK
                ):
                    _client._publish_event(_event_type, result[1])
                return result

            handler_class = type(
                f"AlphaPilot{handler_base_name}",
                (handler_base,),
                {"on_recv_rsp": on_recv_rsp},
            )
            handler = handler_class()
            context.set_handler(handler)
            handlers.append(handler)
        self._quote_handlers = handlers

    def _publish_event(self, event_type: str, data: Any) -> None:
        with self._event_lock:
            if not self._event_subscribers:
                return
            self._event_sequence += 1
            event = {
                "type": event_type,
                "sequence": self._event_sequence,
                "received_at": datetime.now(UTC).isoformat(),
                "data": _json_safe(data),
            }
            for event_queue in tuple(self._event_subscribers):
                try:
                    event_queue.put_nowait(event)
                except Full:
                    with suppress(Empty):
                        event_queue.get_nowait()
                    with suppress(Full):
                        event_queue.put_nowait(event)

    def subscribe_events(self, *, max_queue_size: int = 500) -> Queue[dict[str, Any]]:
        if max_queue_size < 1 or max_queue_size > 10_000:
            raise FutuCallValidationError("Event queue size must be between 1 and 10000.")
        with self._quote_lock:
            self._require_opend_tcp()
            self._ensure_quote_context()
        event_queue: Queue[dict[str, Any]] = Queue(maxsize=max_queue_size)
        with self._event_lock:
            self._event_subscribers.add(event_queue)
        return event_queue

    def unsubscribe_events(self, event_queue: Queue[dict[str, Any]]) -> None:
        with self._event_lock:
            self._event_subscribers.discard(event_queue)

    def close(self) -> None:
        with self._quote_lock:
            if self._quote_context is not None:
                try:
                    self._quote_context.close()
                finally:
                    self._quote_context = None
                    self._quote_handlers = []

    def _decode_value(self, value: Any, *, depth: int = 0) -> Any:
        if depth > 12:
            raise FutuCallValidationError("Futu argument nesting exceeds 12 levels.")
        if isinstance(value, list):
            return [self._decode_value(item, depth=depth + 1) for item in value]
        if not isinstance(value, Mapping):
            return value

        if set(value) == {"__bytes_base64__"}:
            encoded_value = value["__bytes_base64__"]
            if not isinstance(encoded_value, str):
                raise FutuCallValidationError("__bytes_base64__ must be a string.")
            try:
                return base64.b64decode(encoded_value, validate=True)
            except ValueError as exc:
                raise FutuCallValidationError("Invalid base64-encoded Futu value.") from exc

        if "__futu_constant__" in value:
            constant_path = value["__futu_constant__"]
            if not isinstance(constant_path, str) or constant_path.count(".") != 1:
                raise FutuCallValidationError(
                    "__futu_constant__ must look like WarrantMarket.HK."
                )
            container_name, member_name = constant_path.split(".", 1)
            if container_name.startswith("_") or member_name.startswith("_"):
                raise FutuCallValidationError("Private Futu constants are not accessible.")
            futu = self._module()
            container = getattr(futu, container_name, None)
            container_module = str(getattr(container, "__module__", ""))
            if not container_module.startswith(("futu.common.constant", "futu.quote")):
                raise FutuCallValidationError(f"Unsupported Futu constant: {constant_path}")
            if not hasattr(container, member_name):
                raise FutuCallValidationError(f"Unknown Futu constant: {constant_path}")
            return getattr(container, member_name)

        if "__futu_type__" not in value:
            return {
                str(key): self._decode_value(item, depth=depth + 1)
                for key, item in value.items()
            }

        type_name = value["__futu_type__"]
        if not isinstance(type_name, str) or type_name not in FUTU_VALUE_TYPES:
            raise FutuCallValidationError(f"Unsupported Futu value type: {type_name}")
        futu = self._module()
        constructor = getattr(futu, type_name, None)
        if constructor is None or not inspect.isclass(constructor):
            raise FutuCallValidationError(
                f"Futu value type {type_name} is unavailable in the installed SDK."
            )

        raw_args = value.get("args", [])
        raw_kwargs = value.get("kwargs", {})
        raw_attributes = value.get("attributes", {})
        raw_calls = value.get("calls", [])
        if not isinstance(raw_args, list) or not isinstance(raw_kwargs, Mapping):
            raise FutuCallValidationError("Futu value args/kwargs have invalid shapes.")
        if not isinstance(raw_attributes, Mapping) or not isinstance(raw_calls, list):
            raise FutuCallValidationError("Futu value attributes/calls have invalid shapes.")

        args = [self._decode_value(item, depth=depth + 1) for item in raw_args]
        kwargs = {
            str(key): self._decode_value(item, depth=depth + 1)
            for key, item in raw_kwargs.items()
        }
        try:
            instance = constructor(*args, **kwargs)
        except Exception as exc:
            raise FutuCallValidationError(f"Could not construct {type_name}: {exc}") from exc

        for attribute_name, raw_attribute_value in raw_attributes.items():
            normalized_name = str(attribute_name)
            if normalized_name.startswith("_") or not hasattr(instance, normalized_name):
                raise FutuCallValidationError(
                    f"Unsupported attribute {type_name}.{normalized_name}."
                )
            setattr(
                instance,
                normalized_name,
                self._decode_value(raw_attribute_value, depth=depth + 1),
            )

        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                raise FutuCallValidationError("Futu value calls must be objects.")
            call_name = raw_call.get("method")
            if not isinstance(call_name, str) or call_name.startswith("_"):
                raise FutuCallValidationError("Futu value call method is invalid.")
            call_target = getattr(instance, call_name, None)
            if not callable(call_target):
                raise FutuCallValidationError(f"Unsupported value call {type_name}.{call_name}.")
            call_args = raw_call.get("args", [])
            call_kwargs = raw_call.get("kwargs", {})
            if not isinstance(call_args, list) or not isinstance(call_kwargs, Mapping):
                raise FutuCallValidationError("Futu value call args/kwargs have invalid shapes.")
            try:
                call_target(
                    *[self._decode_value(item, depth=depth + 1) for item in call_args],
                    **{
                        str(key): self._decode_value(item, depth=depth + 1)
                        for key, item in call_kwargs.items()
                    },
                )
            except Exception as exc:
                raise FutuCallValidationError(
                    f"Futu value call {type_name}.{call_name} failed: {exc}"
                ) from exc
        return instance

    def _unwrap_result(self, surface: str, method: str, result: Any) -> Any:
        futu = self._module()
        if not isinstance(result, tuple) or not result:
            return result
        ret_code = result[0]
        if ret_code != futu.RET_OK:
            message = result[1] if len(result) > 1 else "unknown OpenD error"
            raise FutuSDKError(f"{surface}.{method} failed: {message}")
        payload = result[1:]
        if not payload:
            return None
        if len(payload) == 1:
            return payload[0]
        return payload

    def quote_call_raw(
        self,
        method: str,
        args: list[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
    ) -> Any:
        if method in QUOTE_READ_METHODS or method in QUOTE_SUBSCRIPTION_METHODS:
            pass
        elif method in QUOTE_ACCOUNT_MUTATION_METHODS:
            if not self.settings.futu_enable_account_mutation:
                raise FutuFeatureDisabledError(
                    "Futu watchlist/reminder/account mutation is disabled by configuration."
                )
        else:
            raise FutuMethodNotAllowedError(f"Futu quote method is not allowed: {method}")

        decoded_args = [self._decode_value(item) for item in (args or [])]
        decoded_kwargs = {
            str(key): self._decode_value(item) for key, item in (kwargs or {}).items()
        }
        self._require_opend_tcp()
        with self._quote_lock:
            context = self._ensure_quote_context()
            call_target = getattr(context, method, None)
            if not callable(call_target):
                raise FutuCallValidationError(
                    f"Quote method {method} is unavailable in the installed SDK."
                )
            try:
                result = call_target(*decoded_args, **decoded_kwargs)
            except FutuClientError:
                raise
            except (TypeError, ValueError) as exc:
                raise FutuCallValidationError(
                    f"Invalid arguments for Futu quote method {method}: {exc}"
                ) from exc
            except Exception as exc:
                self.close()
                raise FutuUnavailableError(f"Futu quote call {method} failed: {exc}") from exc
            return self._unwrap_result("quote", method, result)

    def quote_call(
        self,
        method: str,
        args: list[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = self.quote_call_raw(method, args, kwargs)
        return {"ok": True, "surface": "quote", "method": method, "data": _json_safe(data)}

    def _new_trade_context(self, context_kind: str, market: str) -> Any:
        if context_kind not in TRADE_CONTEXTS:
            raise FutuCallValidationError(
                f"Unknown Futu trade context {context_kind}; use security, future, or crypto."
            )
        self._require_opend_tcp()
        futu = self._module()
        common_kwargs = {
            "host": self.settings.futu_host,
            "port": self.settings.futu_port,
            "security_firm": self.settings.futu_security_firm,
            "ai_type": 1,
        }
        try:
            if context_kind == "security":
                return futu.OpenSecTradeContext(
                    filter_trdmarket=market.upper(), **common_kwargs
                )
            if context_kind == "future":
                return futu.OpenFutureTradeContext(**common_kwargs)
            return futu.OpenCryptoTradeContext(**common_kwargs)
        except Exception as exc:
            message = f"Could not open Futu {context_kind} context: {exc}"
            raise FutuUnavailableError(message) from exc

    def trade_call(
        self,
        context_kind: str,
        method: str,
        *,
        args: list[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
        market: str = "HK",
        environment: str = "SIMULATE",
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        normalized_environment = environment.upper()
        if normalized_environment not in {"SIMULATE", "REAL"}:
            raise FutuCallValidationError("Trade environment must be SIMULATE or REAL.")
        if context_kind == "crypto" and normalized_environment != "REAL":
            raise FutuCallValidationError("Futu crypto trading supports REAL environment only.")

        if method in TRADE_QUERY_METHODS:
            if not self.settings.futu_enable_trade_query:
                raise FutuFeatureDisabledError(
                    "Futu account and trade queries are disabled by configuration."
                )
        elif method in TRADE_MUTATION_METHODS:
            if not self.settings.futu_enable_trade:
                raise FutuFeatureDisabledError("Futu trade mutation is disabled by configuration.")
            if normalized_environment == "REAL":
                if not self.settings.live_trading_enabled:
                    raise FutuFeatureDisabledError("Live trading is disabled by configuration.")
                if confirmation != "SUBMIT_REAL_ORDER":
                    raise FutuFeatureDisabledError(
                        "A real trade requires confirmation=SUBMIT_REAL_ORDER."
                    )
        elif method in PERMANENTLY_BLOCKED_METHODS:
            raise FutuMethodNotAllowedError(
                "unlock_trade is never exposed; unlock OpenD outside AlphaPilot if authorized."
            )
        else:
            raise FutuMethodNotAllowedError(f"Futu trade method is not allowed: {method}")

        decoded_args = [self._decode_value(item) for item in (args or [])]
        decoded_kwargs = {
            str(key): self._decode_value(item) for key, item in (kwargs or {}).items()
        }
        context = self._new_trade_context(context_kind, market)
        try:
            call_target = getattr(context, method, None)
            if not callable(call_target):
                raise FutuCallValidationError(
                    f"Trade method {method} is unavailable in the installed SDK."
                )
            signature = inspect.signature(call_target)
            if "trd_env" in signature.parameters:
                supplied_environment = decoded_kwargs.get("trd_env")
                if supplied_environment is not None and str(supplied_environment).upper() != (
                    normalized_environment
                ):
                    raise FutuCallValidationError(
                        "kwargs.trd_env conflicts with the request environment."
                    )
                decoded_kwargs["trd_env"] = normalized_environment
            try:
                result = call_target(*decoded_args, **decoded_kwargs)
            except FutuClientError:
                raise
            except (TypeError, ValueError) as exc:
                raise FutuCallValidationError(
                    f"Invalid arguments for Futu trade method {method}: {exc}"
                ) from exc
            except Exception as exc:
                raise FutuUnavailableError(f"Futu trade call {method} failed: {exc}") from exc
            data = self._unwrap_result(context_kind, method, result)
            return {
                "ok": True,
                "surface": f"trade:{context_kind}",
                "method": method,
                "environment": normalized_environment,
                "data": _json_safe(data),
            }
        finally:
            context.close()

    @staticmethod
    def _method_signature(context_class: Any, method: str) -> str | None:
        call_target = getattr(context_class, method, None)
        if not callable(call_target):
            return None
        try:
            return str(inspect.signature(call_target))
        except (TypeError, ValueError):
            return None

    def capabilities(self) -> dict[str, Any]:
        try:
            futu = self._module()
        except FutuUnavailableError:
            futu = None

        quote_class = getattr(futu, "OpenQuoteContext", None) if futu is not None else None
        trade_class = getattr(futu, "OpenSecTradeContext", None) if futu is not None else None

        quote_capabilities: list[dict[str, Any]] = []
        for category, method_names, enabled in (
            ("read", QUOTE_READ_METHODS, self.settings.futu_enable_quote),
            ("subscription", QUOTE_SUBSCRIPTION_METHODS, self.settings.futu_enable_quote),
            (
                "account_mutation",
                QUOTE_ACCOUNT_MUTATION_METHODS,
                self.settings.futu_enable_account_mutation,
            ),
        ):
            for method_name in sorted(method_names):
                signature = (
                    self._method_signature(quote_class, method_name)
                    if quote_class is not None
                    else None
                )
                quote_capabilities.append(
                    {
                        "method": method_name,
                        "category": category,
                        "available": signature is not None,
                        "enabled": enabled,
                        "signature": signature,
                    }
                )

        trade_capabilities: list[dict[str, Any]] = []
        for category, method_names, enabled in (
            ("query", TRADE_QUERY_METHODS, self.settings.futu_enable_trade_query),
            ("mutation", TRADE_MUTATION_METHODS, self.settings.futu_enable_trade),
            ("blocked", PERMANENTLY_BLOCKED_METHODS, False),
        ):
            for method_name in sorted(method_names):
                signature = (
                    self._method_signature(trade_class, method_name)
                    if trade_class is not None
                    else None
                )
                trade_capabilities.append(
                    {
                        "method": method_name,
                        "category": category,
                        "available": signature is not None,
                        "enabled": enabled,
                        "signature": signature,
                    }
                )

        return {
            "sdk_installed": futu is not None,
            "sdk_version": str(getattr(futu, "__version__", "unknown")) if futu else None,
            "quote": quote_capabilities,
            "trade": trade_capabilities,
            "trade_contexts": sorted(TRADE_CONTEXTS),
            "push_event_types": sorted(QUOTE_PUSH_HANDLERS.values()),
            "value_types": sorted(FUTU_VALUE_TYPES),
            "value_encoding": {
                "constant": {"__futu_constant__": "WarrantMarket.HK"},
                "object": {
                    "__futu_type__": "SimpleFilter",
                    "attributes": {"stock_field": "CUR_PRICE", "filter_min": 10},
                },
            },
        }

    def status(self, *, max_age_seconds: float = 3.0) -> dict[str, Any]:
        with self._status_lock:
            now = monotonic()
            if (
                self._status_cache is not None
                and now - self._status_cached_at <= max_age_seconds
            ):
                return dict(self._status_cache)

            base: dict[str, Any] = {
                "enabled": self.settings.futu_enable_quote,
                "host": self.settings.futu_host,
                "port": self.settings.futu_port,
                "sdk_installed": False,
                "sdk_version": None,
                "opend_reachable": False,
                "qot_logined": False,
                "trd_logined": False,
                "healthy": False,
            }
            try:
                futu = self._module()
                base["sdk_installed"] = True
                base["sdk_version"] = str(getattr(futu, "__version__", "unknown"))
            except FutuUnavailableError as exc:
                base["error"] = str(exc)
                self._status_cache = base
                self._status_cached_at = now
                return dict(base)

            try:
                with socket.create_connection(
                    (self.settings.futu_host, self.settings.futu_port), timeout=0.35
                ):
                    base["opend_reachable"] = True
            except OSError as exc:
                base["error"] = f"OpenD TCP probe failed: {exc}"
                self._status_cache = base
                self._status_cached_at = now
                return dict(base)

            if not self.settings.futu_enable_quote:
                base["error"] = "Futu quote access is disabled by configuration."
                self._status_cache = base
                self._status_cached_at = now
                return dict(base)

            try:
                state = self.quote_call_raw("get_global_state")
                if not isinstance(state, Mapping):
                    raise FutuSDKError("quote.get_global_state returned an invalid payload.")
                base.update(
                    {
                        "server_version": state.get("server_ver"),
                        "qot_logined": bool(state.get("qot_logined")),
                        "trd_logined": bool(state.get("trd_logined")),
                        "markets": {
                            key.removeprefix("market_"): value
                            for key, value in state.items()
                            if str(key).startswith("market_")
                        },
                    }
                )
                base["healthy"] = bool(base["qot_logined"])
            except FutuClientError as exc:
                base["error"] = str(exc)

            self._status_cache = base
            self._status_cached_at = now
            return dict(base)


@lru_cache(maxsize=1)
def get_futu_client() -> FutuClient:
    return FutuClient(get_settings())
