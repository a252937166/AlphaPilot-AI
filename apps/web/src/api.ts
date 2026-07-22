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

export interface MarketBreadthFullResponse {
  ts: string
  advancers: number
  decliners: number
  unchanged: number
  limit_up: number
  limit_down: number
  broken_boards: number
  avg_change_pct: number
  prior_ts: string | null
  prior_advancers: number | null
  prior_decliners: number | null
  prior_unchanged: number | null
  prior_limit_up: number | null
  prior_limit_down: number | null
  prior_broken_boards: number | null
  prior_avg_change_pct: number | null
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
    throw new Error(detail)
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

  stockOverview: (symbol: string) => request<any>(`/v1/stocks/${symbol}/overview`),
  stockBars: (symbol: string, days = 120) =>
    request<any>(`/v1/stocks/${symbol}/bars?days=${days}`),

  watchlist: () => request<any>('/v1/watchlist'),
  watchlistTrack: () => request<any>('/v1/watchlist/track'),
  watchlistUpsert: (body: Record<string, unknown>) =>
    request<any>('/v1/watchlist', { method: 'POST', body: JSON.stringify(body) }),
  watchlistDelete: (symbol: string) =>
    request<any>(`/v1/watchlist/${symbol}`, { method: 'DELETE' }),

  alerts: (limit = 60) => request<AlertListResponse>(`/v1/alerts?limit=${limit}`),
  refreshAlerts: () => request<any>('/v1/alerts/refresh', { method: 'POST' }),
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
  styleDaily: (days = 60) => request<StyleDailyResponse>(`/v1/style/daily?days=${days}`),
  marketRegime: () => request<any>('/v1/market/regime?symbol=SH.000001'),
  marketIndices: (days = 60) => request<any>(`/v1/market/indices?history_days=${days}`),
  marketBreadth: () => request<any>('/v1/market/breadth'),
  marketBreadthFull: () => request<MarketBreadthFullResponse>('/v1/market/breadth-full'),
  jobRuns: (limit = 50) => request<JobRunsResponse>(`/v1/jobs/runs?limit=${limit}`),

  disclosures: (symbol: string, sync = false) =>
    request<any>(`/v1/disclosures/${symbol}?sync=${sync}`),
  syncDisclosures: (symbol: string) =>
    request<any>(`/v1/disclosures/${symbol}/sync`, { method: 'POST' }),

  dailyReport: () => request<any>('/v1/reports/daily'),
  generateDailyReport: () => request<any>('/v1/reports/daily/generate', { method: 'POST' }),

  evaluateTrade: (body: Record<string, unknown>) =>
    request<any>('/v1/trades/evaluate', { method: 'POST', body: JSON.stringify(body) }),
  proposals: () => request<any>('/v1/trades/proposals'),
  createProposal: (body: Record<string, unknown>) =>
    request<any>('/v1/trades/proposals', { method: 'POST', body: JSON.stringify(body) }),
  approveProposal: (id: number) =>
    request<any>(`/v1/trades/proposals/${id}/approve`, { method: 'POST' }),
  rejectProposal: (id: number) =>
    request<any>(`/v1/trades/proposals/${id}/reject`, { method: 'POST' }),
}
