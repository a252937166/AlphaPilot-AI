export function fmtNum(value: unknown, digits = 2): string {
  const num = Number(value)
  if (value === null || value === undefined || Number.isNaN(num)) return '—'
  return num.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function fmtPct(value: unknown, digits = 2, alreadyPct = true): string {
  const num = Number(value)
  if (value === null || value === undefined || Number.isNaN(num)) return '—'
  const pct = alreadyPct ? num : num * 100
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct.toFixed(digits)}%`
}

export function pctClass(value: unknown): string {
  const num = Number(value)
  if (value === null || value === undefined || Number.isNaN(num) || num === 0) return ''
  return num > 0 ? 'up' : 'down'
}

export function fmtAmount(value: unknown): string {
  const num = Number(value)
  if (value === null || value === undefined || Number.isNaN(num)) return '—'
  if (Math.abs(num) >= 1e8) return `${(num / 1e8).toFixed(2)}亿`
  if (Math.abs(num) >= 1e4) return `${(num / 1e4).toFixed(1)}万`
  return num.toFixed(0)
}

export function fmtTime(value: unknown): string {
  if (!value) return '—'
  const date = new Date(String(value))
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', {
    hour12: false,
    timeZone: 'Asia/Shanghai',
  })
}

export function fmtDate(value: unknown): string {
  if (!value) return '—'
  return String(value).slice(0, 10)
}

export const ACTION_META: Record<string, { label: string; cls: string }> = {
  BUY_CANDIDATE: { label: '买入候选', cls: 'green' },
  ADD: { label: '加仓', cls: 'green' },
  WATCH: { label: '观察', cls: 'blue' },
  HOLD: { label: '持有', cls: 'gray' },
  REDUCE: { label: '减仓', cls: 'red' },
  EXIT: { label: '退出', cls: 'red' },
  STOP: { label: '风控止损', cls: 'red' },
  REVIEW_REQUIRED: { label: '需人工复核', cls: 'yellow' },
}

export const REGIME_META: Record<string, { label: string; cls: string }> = {
  risk_on: { label: 'Risk-On 偏多', cls: 'green' },
  risk_off: { label: 'Risk-Off 避险', cls: 'red' },
  trend_up: { label: '趋势上涨', cls: 'green' },
  trend_down: { label: '趋势下跌', cls: 'red' },
  range: { label: '震荡整理', cls: 'blue' },
  event_shock: { label: '事件冲击', cls: 'yellow' },
}

export function actionMeta(action: string | null | undefined) {
  return (action && ACTION_META[action]) || { label: action || '—', cls: 'gray' }
}

export function regimeMeta(regime: string | null | undefined) {
  return (regime && REGIME_META[regime]) || { label: regime || '未知', cls: 'gray' }
}

// Solid stepped colors like a proper market heatmap: vivid, no muddy alpha blends.
export function heatColor(changePct: number): string {
  const value = Number(changePct) || 0
  if (value >= 3) return '#22b357'
  if (value >= 1.5) return '#1e9d4d'
  if (value >= 0.6) return '#1b7d42'
  if (value >= 0.15) return '#1b5c38'
  if (value > -0.15) return '#273350'
  if (value > -0.6) return '#75303a'
  if (value > -1.5) return '#a03236'
  if (value > -3) return '#c73a3a'
  return '#e34747'
}
