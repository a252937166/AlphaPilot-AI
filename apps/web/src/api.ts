const BASE = '/api'

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
  runScreen: (body: { symbols: string[]; top_n: number; provider?: string | null }) =>
    request<any>('/v1/screens/run', { method: 'POST', body: JSON.stringify(body) }),
  latestScreen: () => request<any>('/v1/screens/latest'),

  stockOverview: (symbol: string) => request<any>(`/v1/stocks/${symbol}/overview`),
  stockBars: (symbol: string, days = 120) =>
    request<any>(`/v1/stocks/${symbol}/bars?days=${days}`),

  watchlist: () => request<any>('/v1/watchlist'),
  watchlistTrack: () => request<any>('/v1/watchlist/track'),
  watchlistUpsert: (body: Record<string, unknown>) =>
    request<any>('/v1/watchlist', { method: 'POST', body: JSON.stringify(body) }),
  watchlistDelete: (symbol: string) =>
    request<any>(`/v1/watchlist/${symbol}`, { method: 'DELETE' }),

  alerts: (limit = 60) => request<any>(`/v1/alerts?limit=${limit}`),
  refreshAlerts: () => request<any>('/v1/alerts/refresh', { method: 'POST' }),
  ackAlert: (id: number) => request<any>(`/v1/alerts/${id}/acknowledge`, { method: 'POST' }),

  sectors: (refresh = false) => request<any>(`/v1/sectors/strength?refresh=${refresh}`),
  marketRegime: () => request<any>('/v1/market/regime?symbol=SH.000001'),
  marketIndices: (days = 60) => request<any>(`/v1/market/indices?history_days=${days}`),
  marketBreadth: () => request<any>('/v1/market/breadth'),

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
