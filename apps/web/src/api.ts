const BASE = '/api'

export type NotificationKind = 'alert' | 'event' | 'job' | 'system'
export type NotificationLevel = 'info' | 'warn' | 'error'

export interface NotificationItem {
  id: number
  kind: NotificationKind
  ref_id: string
  title: string
  body: string
  level: NotificationLevel
  read_at: string | null
  created_at: string
}

export interface NotificationListResponse {
  notifications: NotificationItem[]
}

export interface StyleDailyPoint {
  trade_date: string
  growth_pct: number
  value_pct: number
  defensive_pct: number
  balanced_pct: number
  model_version: string
}

export interface StyleDailyResponse {
  requested_days: number
  available_days: number
  series: StyleDailyPoint[]
}

export interface ScreenDiffResponse {
  current_run_id: number
  previous_run_id: number | null
  baseline_missing: boolean
  new: string[]
  dropped: string[]
  stayed: number
}

export type StyleTag = 'growth' | 'value' | 'defensive' | 'balanced'
export type RiskLevel = 'low' | 'mid' | 'high'
export type ScreenSort = 'score' | 'expected_return' | 'win_rate'

export interface ScreenFilter {
  universe: 'all' | 'watchlist' | 'custom'
  symbols?: string[] | null
  industries?: string[] | null
  style?: StyleTag | null
  risk_level?: RiskLevel | null
  min_market_cap?: number | null
  top_n: number
  sort_by: ScreenSort
  horizon_days: 5 | 20
  provider?: string | null
  lookback_days?: number
}

export interface ScreenCandidateSummary {
  rank: number
  symbol: string
  score: number
  trend_score: number | null
  risk_score: number | null
  quality_placeholder_score: number | null
  p_up_5d: number | null
  p_up_20d: number | null
  expected_return_5d: number | null
  expected_return_20d: number | null
  confidence_5d: number | null
  confidence_20d: number | null
  display_name: string | null
  industry: string | null
  style: StyleTag | null
  risk_level: RiskLevel | null
  market_cap: number | null
  trade_date: string | null
  win_rate_20d: number | null
  forecast_source: string | null
  reasons: string[]
  warnings: string[]
  [key: string]: unknown
}

export interface LatestScreenResponse {
  id: number
  universe: string
  filters: Record<string, unknown>
  provider: string
  model_version: string
  requested: number
  succeeded: number
  failed: Record<string, string>
  candidates: ScreenCandidateSummary[]
  created_at: string
}

export interface PersistedScreeningResponse {
  run_id: number
  generated_at: string
  provider: string
  model_version: string
  requested: number
  succeeded: number
  failed: Record<string, string>
  candidates: ScreenCandidateSummary[]
}

export interface FactorWeightsResponse {
  version: string
  profile: string
  weights: Record<string, number>
}

export interface StyleExposureSlice {
  style: StyleTag
  count: number
  pct: number
}

export interface StyleExposureResponse {
  run_id: number
  total_candidates: number
  exposure: StyleExposureSlice[]
}

export interface IndustriesResponse {
  count: number
  industries: string[]
}

export interface AlertItem {
  id: number
  symbol: string
  action: string
  urgency: string
  confidence: number
  suggested_position_change: number
  target_low: number | null
  target_high: number | null
  suggested_notional: number | null
  reasons: string[]
  invalidation: string | null
  model_version: string | null
  as_of: string | null
  expires_at: string | null
  acknowledged: boolean
  created_at: string
}

export interface AlertListResponse {
  alerts: AlertItem[]
}

export interface AlertRefreshResponse {
  created: AlertItem[]
}

export type ThesisState = 'strengthened' | 'unchanged' | 'weakened'

export interface WatchlistTransitionPoint {
  date: string
  strengthened: number
  unchanged: number
  weakened: number
}

export interface WatchlistSummaryResponse {
  strengthened: number
  unchanged: number
  weakened: number
  transitions_7d: WatchlistTransitionPoint[]
}

export type WatchlistEventCategory = 'announcement' | 'calendar' | 'capital' | 'other'

export interface WatchlistRecentEvent {
  id: number
  event_type: string
  category: WatchlistEventCategory
  title: string
  summary: string | null
  source_ref: string | null
  direction: number | null
  strength: number | null
  occurred_at: string
}

export interface WatchlistTrackRow {
  symbol: string
  display_name: string | null
  group_name: string | null
  industry: string | null
  cost_price: number | null
  quantity: number | null
  thesis: string | null
  thesis_state: ThesisState
  last: number | null
  change_pct: number | null
  pnl_pct: number | null
  p_up_20d: number | null
  expected_return_20d: number | null
  confidence_20d: number | null
  forecast_as_of: string | null
  alert_action: string | null
  alert_urgency: string | null
  alert_confidence: number | null
  recent_events: WatchlistRecentEvent[]
}

export interface WatchlistTrackResponse {
  rows: WatchlistTrackRow[]
  count: number
}

export interface WatchlistUpsertRequest {
  symbol: string
  group_name?: string
  display_name?: string
  cost_price?: number
  quantity?: number
}

export interface WatchlistItemResponse {
  item: {
    symbol: string
    group_name: string | null
    display_name: string | null
    cost_price: number | null
    quantity: number | null
    thesis: string | null
    thesis_state: ThesisState
    created_at: string
    updated_at: string
  }
}

export interface PortfolioPosition {
  symbol: string
  qty: number
  mv: number
  industry: string
}

export interface PortfolioIndustrySlice {
  industry: string
  market_value: number
  weight: number
}

export interface PortfolioOverviewResponse {
  available: boolean
  snapshot: {
    trade_date: string
    total_value: number
    cash: number
    positions: PortfolioPosition[]
    daily_return?: number | null
    benchmark_return?: number | null
    excess_return?: number | null
    drawdown?: number | null
    source: string
  } | null
  market_value: number
  account_market_value: number
  market_value_gap: number
  industry_distribution: PortfolioIndustrySlice[]
  missing_industry_count: number
  valuation_basis: 'futu_sim_market_value' | null
  valuation_basis_label: string | null
  warning: string | null
}

export interface PortfolioAttributionResponse {
  available: boolean
  requested_days: number
  available_days: number
  dates: string[]
  nav: number[]
  benchmark_nav: Array<number | null>
  excess_cum: number | null
  max_drawdown: number | null
  benchmark_drawdown: number | null
  benchmark_symbol: string
  warning: string | null
}

export type StockBarFrequency = 'd' | 'w' | 'm'
export type StockSignalMarker = 'B' | 'S'
export type StockEventFilter = 'all' | 'disclosure' | 'earnings_preview' | 'unlock' | 'dividend'

export interface StockQuote {
  last: number | null
  change_pct: number | null
  open: number | null
  high: number | null
  low: number | null
  volume: number | null
  amount: number | null
  turnover_rate: number | null
  pe_ttm: number | null
  market_cap: number | null
  float_cap: number | null
  pb: number | null
  as_of: string | null
  fundamentals_as_of: string | null
  source: string
  ohlc_source: string
  ohlc_trade_date: string | null
}

export interface StockSecurity {
  symbol?: string
  name?: string | null
  board?: string | null
  listed_date?: string | null
  status?: string | null
  [key: string]: unknown
}

export interface StockHorizonForecast {
  horizon_days: number
  p_up: number
  expected_return: number
  q10: number
  q50: number
  q90: number
  confidence: number
}

export interface StockForecastPayload {
  symbol: string
  as_of: string
  provider: string
  model_version: string
  data_points: number
  features: Record<string, number>
  horizons: Record<string, StockHorizonForecast>
  warnings: string[]
}

export interface StockAlertPayload {
  action: string
  urgency: string
  confidence: number
  suggested_position_change: number
  reasons: string[]
  invalidation: string
  model_version: string
  as_of: string
  expires_at: string
}

export interface StockScoreRadarItem {
  key: string
  name: string
  value: number
  max: number
  available_inputs: number
  required_inputs: number
  degraded: boolean
}

export interface StockScorePayload {
  symbol: string
  trade_date: string
  tech: number
  capital: number
  fundamental: number
  valuation: number
  sentiment: number
  composite: number
  model_version: string
  dimension_weights: Record<string, number>
  radar: StockScoreRadarItem[]
  inputs: Record<
    string,
    { raw: number | null; zscore: number | null; available: boolean; model_version: string | null }
  >
  missing_factors: string[]
  degraded_dimensions: string[]
  input_coverage: number
  degraded: boolean
  degradation_reason: string | null
}

export interface StockDisclosure {
  id: number
  title: string
  url: string | null
  published_at: string
  [key: string]: unknown
}

export interface StockOverviewResponse {
  symbol: string
  security: StockSecurity | null
  quote: StockQuote | null
  forecast: StockForecastPayload
  alert: StockAlertPayload
  disclosures: StockDisclosure[]
  score: StockScorePayload | null
  score_error: string | null
}

export interface StockBar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
  amount: number | null
}

export interface StockBarsResponse {
  symbol: string
  freq?: StockBarFrequency
  frequency?: StockBarFrequency
  source: string
  warnings: string[]
  bars: StockBar[]
}

export interface StockSignalItem {
  id: number
  symbol: string
  action: string
  marker: StockSignalMarker
  confidence: number
  target_low: number | null
  target_high: number | null
  suggested_notional: number | null
  reasons: string[]
  invalidation: string | null
  model_version: string | null
  expires_at: string | null
  forecast_snapshot_id: number
  forecast_provider: string
  trade_eligible: boolean
  trade_date: string
  close: number | null
  close_source: string | null
  as_of: string | null
  created_at: string
}

export interface StockSignalsResponse {
  symbol: string
  from: string | null
  to: string | null
  count: number
  excluded_count: number
  warnings: string[]
  signals: StockSignalItem[]
}

export interface StockInsightDriver {
  text: string
  tag: '利多' | '利空' | '中性'
  source_ref: string
}

export interface StockInsightResponse {
  symbol: string
  generated_at: string
  core_view: string
  drivers: StockInsightDriver[]
  model_version: string
  source: 'llm' | 'rule' | string
}

export interface StockCalendarEvent {
  id: number
  symbol: string
  event_type: 'earnings_preview' | 'unlock' | 'dividend' | string
  event_date: string
  title: string
  payload: Record<string, unknown>
  source: string
  available_time: string
}

export interface StockCalendarResponse {
  symbol: string
  from: string
  to: string
  days: number
  events: StockCalendarEvent[]
}

export interface StockIntradayPoint {
  time: string
  price: number
  avg_price: number | null
  volume: number | null
}

export type StockIntradayResponse = Record<string, StockIntradayPoint[]>

export interface TradeProposalInput {
  proposal_id: string
  idempotency_key: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  estimated_notional: number
  confidence: number
  market_data_as_of: string
  model_version: string
  mode: 'confirm_to_trade' | 'paper_auto'
  source_alert_id?: number | null
  metadata?: Record<string, unknown>
}

export interface TradeProposalRequest {
  proposal: TradeProposalInput
  portfolio?: {
    equity: number
    cash: number
    daily_pnl_pct: number
    current_position_pct: number
    sector_position_pct: number
    open_orders_for_symbol: number
  }
}

export interface TradeRiskDecision {
  approved: boolean
  reasons: string[]
  evaluated_at: string
  requires_human_confirmation: boolean
}

export interface PersistedTradeProposal {
  id: number
  proposal_id: string
  idempotency_key: string | null
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  estimated_notional: number
  confidence: number
  mode: string
  status: string
  source_alert_id: number | null
  proposal: TradeProposalInput
  risk_decision: TradeRiskDecision
  created_at: string
  reviewed_at: string | null
}

export interface CreateTradeProposalResponse {
  proposal: PersistedTradeProposal
  risk_decision: TradeRiskDecision
}

export interface TradeProposalListResponse {
  proposals: PersistedTradeProposal[]
}

export interface BrokerOrder {
  id: number
  proposal_id: string
  futu_order_id: string | null
  symbol: string
  side: 'BUY' | 'SELL'
  order_type: string
  price: number
  qty: number
  status: string
  filled_qty: number
  avg_fill_price: number | null
  environment: string
  error: string | null
  created_at: string
  updated_at: string
}

export interface BrokerOrderListResponse {
  orders: BrokerOrder[]
}

export interface ExecuteTradeProposalResponse {
  order: BrokerOrder
  proposal: PersistedTradeProposal
}

export interface ForecastHitSample {
  symbol: string
  as_of: string
  p_up_1d: number
  realized_return_1d: number
  hit: boolean
}

export interface ForecastHitStats {
  evaluated: number
  hits: number
  hit_rate: number | null
  samples: ForecastHitSample[]
}

export interface SignalAttributionRow {
  alert_id: number
  symbol: string
  action: string
  created_at: string
  evidence_as_of: string | null
  horizon_days: number
  origin_date: string
  maturity_date: string
  realized_return: number
  contribution: number
  hit: boolean | null
  evaluated_at: string
  model_version: string
}

export interface SignalAttribution {
  horizon_days: number
  model_version: string
  outcomes: number
  directional_evaluated: number
  hit_rate_directional: number | null
  previous_report_date: string | null
  previous_hit_rate_directional: number | null
  hit_rate_change: number | null
  hit_rate_change_pp: number | null
  top_hits: SignalAttributionRow[]
  top_misses: SignalAttributionRow[]
  by_action: Record<string, {
    outcomes: number
    directional_evaluated: number
    hits: number
    hit_rate: number | null
    contribution_total: number
  }>
}

export interface ImprovementStatistic {
  kind: 'statistic'
  source: 'statistics'
  source_label: string
  ref: string
  dimension: string
  dimension_label: string
  group: string
  group_label: string
  outcomes: number
  directional_evaluated: number
  hits: number
  hit_rate: number | null
  contribution_total: number
  text: string
}

export interface ImprovementSuggestion {
  kind: 'suggestion'
  source: 'llm'
  source_label: string
  title: string
  text: string
  basis_refs: string[]
  basis: ImprovementStatistic[]
}

export interface ImprovementSuggestions {
  source: 'llm' | 'statistics' | string
  source_label: string
  suggestions: ImprovementSuggestion[]
  statistics: ImprovementStatistic[]
  empty_reason: string | null
  fallback_reason: string | null
  sector_membership_basis: string
  volatility_basis: string
  provider?: string | null
  model?: string | null
}

export interface ReportEvent {
  id: number
  symbol: string | null
  event_type: string
  type_label: string
  type_color: string
  title: string
  summary: string | null
  direction: number | null
  strength: number | null
  occurred_at: string
  source_ref: string | null
}

export interface ReportEventTimeline {
  items: ReportEvent[]
  empty_reason: string | null
  timezone: string
}

export interface ReportAlert {
  id: number
  symbol: string
  action: string
  urgency: string
  confidence: number
  reasons: string[]
  created_at: string
}

export interface SectorCallExcess {
  available?: boolean
  average_excess?: number | null
  sample_count?: number
  top3?: Array<Record<string, unknown>>
  warning?: string | null
}

export interface DailyReportResponse {
  report_date: string
  generated_at: string
  indices: Array<Record<string, unknown>>
  watchlist: Array<Record<string, unknown>>
  watchlist_gainers: Array<Record<string, unknown>>
  watchlist_losers: Array<Record<string, unknown>>
  forecast_hit_stats: ForecastHitStats
  signal_attribution: SignalAttribution
  improvement_suggestions: ImprovementSuggestions
  event_timeline: ReportEventTimeline
  sector_call_excess?: SectorCallExcess | null
  alerts: ReportAlert[]
  disclosures: Array<Record<string, unknown>>
  ai_summary: {
    source: string
    text: string
    provider?: string | null
    model?: string | null
  }
  tomorrow_focus: Array<{
    symbol: string
    display_name?: string | null
    reason: string
  }>
  disclaimer: string
}

export interface SectorStrengthItem {
  plate_code: string
  plate_name: string
  sampled: number
  avg_change_pct: number
  up_ratio: number
  turnover: number
  strength: number
  rank: number
  leader_code: string
  leader_name: string
  leader_change_pct: number
  net_inflow: number | null
  main_inflow: number | null
  flow_trade_date: string | null
  flow_source: string | null
}

export interface SectorStrengthResponse {
  as_of: string
  cached: boolean
  stale?: boolean
  error?: string
  sectors: SectorStrengthItem[]
}

export type SectorHorizon = 5 | 10 | 20
export type SectorLifecycle = 'boom' | 'rising' | 'decline' | 'bottoming' | 'recovery'

export interface SectorForecastRow {
  rank: number
  plate_code: string
  plate_name: string
  trade_date: string
  horizon: SectorHorizon
  score: number | null
  expected_excess: number | null
  win_rate: number | null
  lifecycle: SectorLifecycle | null
  rsi14: number | null
  reversal_score: number | null
  model_version: string
  net_inflow: number | null
  net_inflow_5d: number | null
  flow_coverage_days: number
  flow_source: string | null
  leader_code: string | null
  leader_name: string | null
  leader_change_pct: number | null
}

export interface SectorForecastResponse {
  as_of: string
  horizon: SectorHorizon
  model_version: string
  flow_mode: 'full' | 'no-flow'
  backtest_scope: 'fixed-current-membership'
  degraded_reason: string | null
  available: boolean
  count: number
  rows: SectorForecastRow[]
  flow_as_of: string | null
  flow_window_days: number
  strength_as_of: string | null
  input_trade_date: string | null
  input_coverage: {
    latest_symbol_count: number
    forecast_symbol_count: number
    reference_trade_date: string
    reference_symbol_count: number
    ratio: number
    minimum_ratio: number
  } | null
  ignored_forecast_dates: string[]
  stale: boolean
  warning: string | null
  reason?: string
  counts?: Record<SectorLifecycle | 'unclassified', number>
}

export interface SectorLeaderItem {
  rank: number
  symbol: string
  name: string | null
  correlation: number
  return_20d: number
  observations?: number
}

export interface SectorLeadersResponse {
  plate_code: string
  plate_name: string
  as_of: string
  lookback_sessions: number
  method: 'pearson-daily-return'
  source: string
  sources: string[]
  mock_excluded: boolean
  membership_scope: 'fixed-current-membership'
  membership_refreshed_at: string
  constituent_count: number
  eligible_members: number
  leader: {
    symbol: string
    name: string | null
    return_20d: number
  }
  count: number
  rows: SectorLeaderItem[]
}

export type MarketRegime =
  | 'risk_on'
  | 'risk_off'
  | 'trend_up'
  | 'trend_down'
  | 'range'
  | 'event_shock'

export interface MarketRegimeResponse {
  symbol: string
  regime: MarketRegime
  confidence: number
  as_of: string
  features: Record<string, number>
  explanation: string[]
}

export interface MarketSentimentResponse {
  score: number
  label: string
  subs: {
    breadth: number
    limitup: number
    volume: number
    volatility: number
  }
  money_effect: string
  liquidity: string
  risk_hint: string
  as_of: string
  model_version: string
  source_snapshot_id: number
  inputs: Record<string, Record<string, unknown>>
  history_samples: Record<string, number>
  sample_sizes: Record<string, number>
  degraded_components: string[]
  missing_inputs: string[]
  degraded: boolean
  degradation_reason: string | null
  weights: Record<string, number>
  source: Record<string, unknown>
}

export interface MarketIndexSymbol {
  symbol: string
  name: string
}

export interface MarketIndexQuote extends MarketIndexSymbol {
  last: number | null
  change_pct: number | null
  amount: number | null
  as_of: string | null
}

export interface MarketIndexDailyPoint {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
  amount: number | null
}

export interface MarketIndicesResponse {
  quotes: MarketIndexQuote[]
  series: Record<string, MarketIndexDailyPoint[]>
  symbols: MarketIndexSymbol[]
}

export type MarketIntradayResponse = Record<string, StockIntradayPoint[]>

export interface MarketBreadthFullResponse {
  ts: string
  advancers: number
  decliners: number
  unchanged: number
  limit_up: number
  limit_down: number
  broken_boards: number
  up_gt4: number
  down_gt4: number
  total_amount: number
  avg_change_pct: number
  median_change_pct: number
  source: string
  prior_ts: string | null
  prior_advancers: number | null
  prior_decliners: number | null
  prior_unchanged: number | null
  prior_limit_up: number | null
  prior_limit_down: number | null
  prior_broken_boards: number | null
  prior_avg_change_pct: number | null
  prior_total_amount: number | null
  prior_time_gap_seconds: number | null
  prior_comparable: boolean
  amount_delta: number | null
  amount_delta_pct: number | null
}

export type MarketMonitorLevel = 'info' | 'warn'

export interface MarketMonitorFeedItem {
  ts: string
  text: string
  level: MarketMonitorLevel
}

export interface MarketMonitorFeedResponse {
  count: number
  items: MarketMonitorFeedItem[]
}

export interface CrossMarketDatum {
  name?: string
  value?: number | null
  last?: number | null
  daily_balance?: number | null
  change_pct?: number | null
  contract?: string
  as_of: string | null
  source: string | null
  note?: string
}

export interface CrossMarketResponse {
  fx_usdcny: CrossMarketDatum
  us_futures: CrossMarketDatum
  commodities: CrossMarketDatum
  northbound: CrossMarketDatum
}

export interface JobRunItem {
  id: number
  job_name: string
  started_at: string
  finished_at: string | null
  status: string
  stats: Record<string, unknown>
  error: string | null
}

export interface JobRunsResponse {
  runs: JobRunItem[]
}

export type BacktestRunStatus = 'running' | 'completed' | 'failed'

export interface BacktestCostModel {
  commission_bps: number
  commission_min: number
  stamp_duty_bps: number
  transfer_bps: number
  slippage_bps: number
}

export interface BacktestRunRequest {
  name?: string
  signal_id: 'composite-v1' | 'composite-v2'
  window?: 'full' | 'train' | 'test' | null
  start_date?: string | null
  end_date?: string | null
  rebalance_freq: '5d' | '10d' | '20d'
  top_pct: number
  initial_capital: number
  cost_model: BacktestCostModel
}

export interface BacktestRunRecord {
  id: number
  name: string
  signal_id: string
  start_date: string
  end_date: string
  rebalance_freq: string
  top_pct: number
  params: Record<string, any>
  status: BacktestRunStatus
  error: string | null
  summary: Record<string, any>
  created_at: string
  report_available: boolean
}

export interface BacktestRunResponse {
  run: BacktestRunRecord
}

export interface BacktestStartResponse extends BacktestRunResponse {
  message: string
}

export interface BacktestListResponse {
  runs: BacktestRunRecord[]
}

export interface BacktestDailyResponse {
  run_id: number
  status: BacktestRunStatus
  dates: string[]
  nav: number[]
  benchmark_nav: number[]
  market_nav: number[]
  rank_ic: Array<number | null>
  long_ret: Array<number | null>
  ls_ret: Array<number | null>
  turnover: Array<number | null>
  n_eligible: number[]
  group_returns: Array<Array<number | null>>
}

export interface BacktestMetricSet {
  observations: number
  total_return: number | null
  ann_return: number | null
  ann_volatility: number | null
  sharpe: number | null
  max_drawdown: number | null
  calmar: number | null
}

export interface BacktestLimitation {
  code: string
  severity: 'high' | 'medium' | 'low'
  text: string
}

export type BacktestReportRun = Omit<
  BacktestRunRecord,
  'error' | 'summary' | 'report_available'
>

export interface BacktestReportResponse {
  run: BacktestReportRun
  generated_at: string
  coverage: {
    trading_days: number
    requested_first_trade_date: string
    requested_last_trade_date: string
    first_execution_date: string | null
    effective_start_date: string
    effective_trading_days: number
    warmup_days_excluded_from_performance: number
    rank_ic_days: number
    rank_ic_unavailable_days: number
    day_errors: Array<Record<string, string>>
    missing_benchmark_days: number
  }
  rank_ic: {
    samples: number
    mean: number | null
    std: number | null
    ic_ir: number | null
    t_stat: number | null
    positive_ratio: number | null
  }
  layers: {
    labels: string[]
    mean_daily_returns: Array<number | null>
    observations: number[]
    top_minus_bottom: number | null
    monotonic_rank_ic: number | null
    strictly_monotonic: boolean
  }
  net_long_performance: BacktestMetricSet
  long_short_gross_diagnostic: {
    available: boolean
    costed: false
    tradable: false
    label?: string
    metrics?: BacktestMetricSet
    warning?: string
    reason?: string
  }
  benchmarks: {
    csi300: BacktestMetricSet
    equal_weight_market: BacktestMetricSet
    excess_total_return: {
      vs_csi300: number | null
      vs_equal_weight_market: number | null
    }
  }
  turnover: {
    trading_days: number
    rebalance_days: number
    total: number
    mean_rebalance: number | null
    median_rebalance: number | null
    max: number | null
    annualized: number | null
  }
  costs: {
    total: number
    initial_capital: number
    to_initial_capital: number | null
    total_traded: number
    bps_of_traded_notional: number | null
  }
  probability_calibration: {
    available: boolean
    reason?: string
    samples?: number
    brier_score?: number | null
    curve?: Array<{
      bin: number
      lower: number
      upper: number
      count: number
      predicted_mean: number | null
      actual_rate: number | null
    }>
  }
  conclusion: {
    status: 'alpha_supported_in_sample' | 'no_reliable_alpha_evidence'
    alpha_supported: boolean
    headline: string
    gates: Record<string, boolean>
    failed_gates: string[]
    policy: string
  }
  limitations: BacktestLimitation[]
}

export type FactorClassification =
  | 'significant_positive'
  | 'significant_reverse'
  | 'ineffective'
  | 'insufficient_data'
  | 'history_excluded_pit_gap'

export interface FactorDirectionAudit {
  formula: string
  raw_direction: string
  verdict: string
  bug_found: boolean
}

export interface FactorDiagnosisItem {
  factor: string
  evaluation_status: FactorEvaluationStatus
  ic_mean: number | null
  ic_ir: number | null
  t_stat: number | null
  ic_positive_ratio: number | null
  long_short: number | null
  n_periods: number
  classification: FactorClassification
  direction: 'positive' | 'negative' | 'unknown'
  direction_audit_required: boolean
  recommendation: string
  economic_note: string
  redundant: boolean
  retained_factor: string | null
  direction_audit: FactorDirectionAudit
}

export type FactorEvaluationStatus =
  | 'measured'
  | 'evaluated_no_sample'
  | 'not_evaluated'
  | 'live_only'
  | 'history_excluded_pit_gap'

export type FactorResearchStage =
  | 'm3_preliminary_multi_year'
  | 'm3_preliminary_flow'
  | 'legacy_or_other'

export interface FactorICWindow {
  sample_tag: 'train' | 'test' | 'full'
  start_date: string
  end_date: string
  updated_at: string | null
  research_run_id: number | null
  research_stage: FactorResearchStage
  expected_factors: string[]
  evaluated_count: number
  measurable_count: number
  evaluated_no_sample_count: number
  factors: string[]
  preliminary_requested_count: number
  preliminary_evaluated_count: number
  preliminary_measurable_count: number
  preliminary_evaluated_no_sample_count: number
}

export interface FactorICWindowResponse {
  sample_tag: 'train' | 'test' | 'full'
  default_policy: string
  default_window: FactorICWindow | null
  windows: FactorICWindow[]
  scope: {
    preliminary_requested_factors: string[]
    preliminary_requested_count: number
    financial_pending_factors: string[]
    financial_pending_count: number
    live_only_factors: string[]
    live_only_count: number
    historical_factor_candidates: string[]
    historical_factor_candidate_count: number
    history_excluded_pit_gap_factors: string[]
    history_excluded_pit_gap_count: number
    test_window_sealed: boolean
  }
}

export interface FactorICResponse {
  available: boolean
  sample_tag: 'train' | 'test' | 'full'
  start_date: string | null
  end_date: string | null
  research_stage: FactorResearchStage | null
  research_run_id: number | null
  expected_factors: string[]
  factor_count: number
  available_count: number
  updated_at: string | null
  selection: {
    exact_window: boolean
    default_policy: string
    research_stage: FactorResearchStage | null
    research_run_id: number | null
    expected_factors: string[]
  }
  coverage: {
    preliminary_requested_count: number
    preliminary_evaluated_count: number
    preliminary_measurable_count: number
    preliminary_evaluated_no_sample_count: number
    preliminary_not_evaluated_count: number
    financial_pending_count: number
    financial_pending_factors: string[]
    live_only_count: number
    live_only_factors: string[]
    historical_factor_candidate_count: number
    historical_factor_candidates: string[]
    history_excluded_pit_gap_count: number
    history_excluded_pit_gap_factors: string[]
  }
  factors: Array<{
    factor: string
    evaluation_status: FactorEvaluationStatus
    ic_mean: number | null
    ic_ir: number | null
    t_stat: number | null
    ic_positive_ratio: number | null
    long_short: number | null
    n_periods: number
    updated_at: string | null
  }>
  limitations: string[]
}

export interface FactorDiagnosisResponse {
  available: boolean
  sample: {
    tag: 'train' | 'test' | 'full'
    start_date: string | null
    end_date: string | null
    research_stage: FactorResearchStage | null
    research_run_id: number | null
    expected_factors: string[]
    factor_count: number
    available_count: number
    updated_at: string | null
    selection: {
      exact_window: boolean
      default_policy: string
      research_stage: FactorResearchStage | null
      research_run_id: number | null
      expected_factors: string[]
    }
    evidence_label: string
  }
  coverage: FactorICResponse['coverage']
  factors: FactorDiagnosisItem[]
  classification_counts: Record<FactorClassification, number>
  correlation: {
    available: boolean
    method: string
    minimum_pair_periods: number
    threshold: number
    factors: string[]
    values: Array<Array<number | null>>
    n_periods: number[][]
    available_cells: number
    redundant_pairs: Array<{
      left: string
      right: string
      correlation: number
      n_periods: number
    }>
    limitation: string
  }
  redundancy_groups: Array<{
    factors: string[]
    retained_factor: string | null
    rule: string
  }>
  weights: {
    factors: string[]
    v1: {
      version: string
      profile: string
      weights: Record<string, number>
    }
    v2: {
      version: string
      profile: string
      weights: Record<string, number>
    }
    delta: Record<string, number>
    method: string
    test_window_used_for_weights: boolean
  }
  source_audit: {
    factor_source: string
    audited_factor_count: number
    calculation_bug_found: boolean
    verdict: string
  }
  conclusion: {
    status: string
    headline: string
    policy: string
  }
  limitations: string[]
}

export interface BacktestComparisonResponse {
  protocol: {
    start_date: string
    end_date: string
    rebalance_freq: string
    top_pct: number
    same_window_and_costs: boolean
    weights_frozen_before_test: boolean
  }
  v1: {
    run_id: number
    signal_id: 'composite-v1'
    rank_ic: BacktestReportResponse['rank_ic']
    net_long: BacktestMetricSet
    long_short_gross: BacktestReportResponse['long_short_gross_diagnostic']
    benchmarks: BacktestReportResponse['benchmarks']
    costs: BacktestReportResponse['costs']
  }
  v2: {
    run_id: number
    signal_id: 'composite-v2'
    rank_ic: BacktestReportResponse['rank_ic']
    net_long: BacktestMetricSet
    long_short_gross: BacktestReportResponse['long_short_gross_diagnostic']
    benchmarks: BacktestReportResponse['benchmarks']
    costs: BacktestReportResponse['costs']
  }
  delta: {
    rank_ic_mean: number | null
    net_total_return: number | null
  }
  curve: {
    dates: string[]
    v1_nav: number[]
    v2_nav: number[]
    csi300_nav: number[]
    market_nav: number[]
  }
  verdict: {
    status: 'improved' | 'partial' | 'failed'
    headline: string
    significant_positive_ic: boolean
    beats_equal_weight_market: boolean
    policy: string
  }
  limitations: string[]
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = `${response.status}`
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      /* keep status text */
    }
    throw new ApiError(response.status, detail)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<any>('/health'),
  dashboard: () => request<any>('/v1/dashboard/overview'),

  screenUniverse: () => request<{ symbols: string[] }>('/v1/screens/universe'),
  runScreen: (body: ScreenFilter) =>
    request<PersistedScreeningResponse>('/v1/screens/run', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  latestScreen: () => request<LatestScreenResponse>('/v1/screens/latest'),
  screenDiff: () => request<ScreenDiffResponse>('/v1/screens/diff'),
  screenStyleExposure: (runId?: number) =>
    request<StyleExposureResponse>(
      `/v1/screens/style-exposure${runId ? `?run_id=${runId}` : ''}`,
    ),
  factorWeights: () => request<FactorWeightsResponse>('/v1/factors/weights'),
  metaIndustries: () => request<IndustriesResponse>('/v1/meta/industries'),

  stockOverview: (symbol: string) =>
    request<StockOverviewResponse>(`/v1/stocks/${encodeURIComponent(symbol)}/overview`),
  stockBars: (symbol: string, days = 160, freq: StockBarFrequency = 'd') =>
    request<StockBarsResponse>(
      `/v1/stocks/${encodeURIComponent(symbol)}/bars?days=${days}&freq=${freq}`,
    ),
  stockSignals: (symbol: string, start?: string, end?: string) => {
    const params = new URLSearchParams()
    if (start) params.set('start', start)
    if (end) params.set('end', end)
    const query = params.size ? `?${params.toString()}` : ''
    return request<StockSignalsResponse>(
      `/v1/stocks/${encodeURIComponent(symbol)}/signals${query}`,
    )
  },
  stockInsight: (symbol: string) =>
    request<StockInsightResponse>(`/v1/stocks/${encodeURIComponent(symbol)}/insight`),
  stockCalendar: (symbol: string, days = 90) =>
    request<StockCalendarResponse>(
      `/v1/stocks/${encodeURIComponent(symbol)}/calendar?days=${days}`,
    ),
  stockIntraday: (symbol: string) =>
    request<StockIntradayResponse>(
      `/v1/market/intraday?symbols=${encodeURIComponent(symbol)}`,
    ),

  watchlist: () => request<any>('/v1/watchlist'),
  watchlistTrack: () => request<WatchlistTrackResponse>('/v1/watchlist/track'),
  watchlistSummary: () => request<WatchlistSummaryResponse>('/v1/watchlist/summary'),
  watchlistUpsert: (body: WatchlistUpsertRequest) =>
    request<WatchlistItemResponse>('/v1/watchlist', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  watchlistDelete: (symbol: string) =>
    request<{ removed: string }>(`/v1/watchlist/${symbol}`, { method: 'DELETE' }),

  portfolioOverview: () => request<PortfolioOverviewResponse>('/v1/portfolio/overview'),
  portfolioAttribution: (days = 60) =>
    request<PortfolioAttributionResponse>(`/v1/portfolio/attribution?days=${days}`),

  alerts: (limit = 60) => request<AlertListResponse>(`/v1/alerts?limit=${limit}`),
  refreshAlerts: (symbols?: string[]) =>
    request<AlertRefreshResponse>('/v1/alerts/refresh', {
      method: 'POST',
      ...(symbols !== undefined ? { body: JSON.stringify({ symbols }) } : {}),
    }),
  ackAlert: (id: number) => request<any>(`/v1/alerts/${id}/acknowledge`, { method: 'POST' }),

  notifications: (unreadOnly = false, limit = 100) =>
    request<NotificationListResponse>(
      `/v1/notifications?unread_only=${unreadOnly}&limit=${limit}`,
    ),
  notificationUnreadCount: () =>
    request<{ unread_count: number }>('/v1/notifications/unread-count'),
  readNotifications: (body: { ids: number[] } | { all: true }) =>
    request<{ updated: number; unread_count: number }>('/v1/notifications/read', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  sectors: (refresh = false) =>
    request<SectorStrengthResponse>(`/v1/sectors/strength?refresh=${refresh}`),
  sectorForecast: (horizon: SectorHorizon) =>
    request<SectorForecastResponse>(`/v1/sectors/forecast?horizon=${horizon}`),
  sectorLifecycle: (horizon: SectorHorizon) =>
    request<SectorForecastResponse>(`/v1/sectors/lifecycle?horizon=${horizon}`),
  sectorOverbought: (horizon: SectorHorizon) =>
    request<SectorForecastResponse>(`/v1/sectors/overbought?horizon=${horizon}`),
  sectorReversal: (horizon: SectorHorizon) =>
    request<SectorForecastResponse>(`/v1/sectors/reversal?horizon=${horizon}`),
  sectorLeaders: (plateCode: string) =>
    request<SectorLeadersResponse>(
      `/v1/sectors/${encodeURIComponent(plateCode)}/leaders`,
    ),
  styleDaily: (days = 60) => request<StyleDailyResponse>(`/v1/style/daily?days=${days}`),
  marketRegime: () =>
    request<MarketRegimeResponse>('/v1/market/regime?symbol=SH.000001'),
  marketSentiment: () => request<MarketSentimentResponse>('/v1/market/sentiment'),
  marketIndices: (days = 60) =>
    request<MarketIndicesResponse>(`/v1/market/indices?history_days=${days}`),
  marketIntraday: (symbol = 'SH.000001') =>
    request<MarketIntradayResponse>(
      `/v1/market/intraday?symbols=${encodeURIComponent(symbol)}`,
    ),
  marketBreadth: () => request<any>('/v1/market/breadth'),
  marketBreadthFull: () => request<MarketBreadthFullResponse>('/v1/market/breadth-full'),
  marketMonitorFeed: (limit = 20) =>
    request<MarketMonitorFeedResponse>(`/v1/market/monitor-feed?limit=${limit}`),
  marketCross: () => request<CrossMarketResponse>('/v1/market/cross'),
  jobRuns: (limit = 50) => request<JobRunsResponse>(`/v1/jobs/runs?limit=${limit}`),

  backtests: (limit = 50) =>
    request<BacktestListResponse>(`/v1/backtest?limit=${limit}`),
  backtest: (id: number) =>
    request<BacktestRunResponse>(`/v1/backtest/${id}`),
  startBacktest: (body: BacktestRunRequest) =>
    request<BacktestStartResponse>('/v1/backtest/run', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  backtestDaily: (id: number) =>
    request<BacktestDailyResponse>(`/v1/backtest/${id}/daily`),
  backtestReport: (id: number) =>
    request<BacktestReportResponse>(`/v1/backtest/${id}/report`),
  factorICWindows: (sampleTag: 'train' | 'test' | 'full' = 'train') =>
    request<FactorICWindowResponse>(
      `/v1/backtest/factors/windows?sample_tag=${encodeURIComponent(sampleTag)}`,
    ),
  factorIC: (
    sampleTag: 'train' | 'test' | 'full' = 'train',
    window?: { startDate: string; endDate: string },
  ) => {
    const params = new URLSearchParams({ sample_tag: sampleTag })
    if (window) {
      params.set('start_date', window.startDate)
      params.set('end_date', window.endDate)
    }
    return request<FactorICResponse>(`/v1/backtest/factors/ic?${params.toString()}`)
  },
  factorDiagnosis: (
    sampleTag: 'train' | 'test' | 'full' = 'train',
    window?: { startDate: string; endDate: string },
  ) => {
    const params = new URLSearchParams({ sample_tag: sampleTag })
    if (window) {
      params.set('start_date', window.startDate)
      params.set('end_date', window.endDate)
    }
    return request<FactorDiagnosisResponse>(
      `/v1/backtest/factors/diagnosis?${params.toString()}`,
    )
  },
  backtestCompare: (v1: number, v2: number) =>
    request<BacktestComparisonResponse>(
      `/v1/backtest/compare?v1=${encodeURIComponent(v1)}&v2=${encodeURIComponent(v2)}`,
    ),

  disclosures: (symbol: string, sync = false) =>
    request<any>(`/v1/disclosures/${symbol}?sync=${sync}`),
  syncDisclosures: (symbol: string) =>
    request<any>(`/v1/disclosures/${symbol}/sync`, { method: 'POST' }),

  dailyReport: () => request<DailyReportResponse>('/v1/reports/daily'),
  generateDailyReport: () =>
    request<DailyReportResponse>('/v1/reports/daily/generate', { method: 'POST' }),

  evaluateTrade: (body: TradeProposalRequest) =>
    request<TradeRiskDecision>('/v1/trades/evaluate', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  proposals: () => request<TradeProposalListResponse>('/v1/trades/proposals'),
  createProposal: (body: TradeProposalRequest) =>
    request<CreateTradeProposalResponse>('/v1/trades/proposals', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  approveProposal: (id: number) =>
    request<{ proposal: PersistedTradeProposal }>(
      `/v1/trades/proposals/${id}/approve`,
      { method: 'POST' },
    ),
  executeProposal: (id: number) =>
    request<ExecuteTradeProposalResponse>(
      `/v1/trades/proposals/${id}/execute`,
      { method: 'POST' },
    ),
  rejectProposal: (id: number) =>
    request<{ proposal: PersistedTradeProposal }>(
      `/v1/trades/proposals/${id}/reject`,
      { method: 'POST' },
    ),
  orders: (limit = 100) =>
    request<BrokerOrderListResponse>(`/v1/orders?limit=${limit}`),
}
