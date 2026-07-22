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
  confidence: number
  reasons: string[]
  created_at: string
  [key: string]: unknown
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
  mode: 'confirm_to_trade'
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

  disclosures: (symbol: string, sync = false) =>
    request<any>(`/v1/disclosures/${symbol}?sync=${sync}`),
  syncDisclosures: (symbol: string) =>
    request<any>(`/v1/disclosures/${symbol}/sync`, { method: 'POST' }),

  dailyReport: () => request<any>('/v1/reports/daily'),
  generateDailyReport: () => request<any>('/v1/reports/daily/generate', { method: 'POST' }),

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
    request<any>(`/v1/trades/proposals/${id}/approve`, { method: 'POST' }),
  rejectProposal: (id: number) =>
    request<any>(`/v1/trades/proposals/${id}/reject`, { method: 'POST' }),
}
