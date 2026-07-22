<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  AlertTriangle,
  Check,
  ChevronRight,
  CircleDollarSign,
  Minus,
  RefreshCw,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  X,
} from 'lucide-vue-next'
import {
  ApiError,
  api,
  type StockBar,
  type StockBarFrequency,
  type StockCalendarEvent,
  type StockEventFilter,
  type StockInsightResponse,
  type StockIntradayPoint,
  type StockOverviewResponse,
  type StockSignalItem,
  type TradeProposalInput,
  type TradeRiskDecision,
} from '../api'
import { actionMeta, fmtAmount, fmtDate, fmtNum, fmtPct, fmtTime, pctClass } from '../format'
import EChart from '../components/EChart.vue'
import { CHART_COLORS, areaGradient, glowLine, tooltipStyle } from '../chartTheme'

type ChartTab = StockBarFrequency | 'intraday'
type TradeSide = 'BUY' | 'SELL'
type EventItem = {
  key: string
  kind: StockEventFilter | 'other'
  rawType: string
  date: string
  title: string
  source: string
  url: string | null
}
type MarkerSignal = StockSignalItem & {
  plotDate: string
  plotClose: number
  stackIndex: number
}

const route = useRoute()
const router = useRouter()
const symbol = computed(() => String(route.params.symbol || '600519').replace(/^(SH|SZ)\./i, ''))

const loading = ref(true)
const error = ref('')
const syncing = ref(false)
const overview = ref<StockOverviewResponse | null>(null)
const insight = ref<StockInsightResponse | null>(null)
const calendarEvents = ref<StockCalendarEvent[]>([])
const insightLoading = ref(false)
const calendarLoading = ref(false)
const sectionErrors = ref({ insight: '', calendar: '', chart: '' })

const chartTab = ref<ChartTab>('d')
const chartLoading = ref(false)
const bars = ref<StockBar[]>([])
const signals = ref<StockSignalItem[]>([])
const intraday = ref<StockIntradayPoint[]>([])
const chartSource = ref('')
const chartWarnings = ref<string[]>([])
const selectedChartSignal = ref<MarkerSignal | null>(null)

const eventFilter = ref<StockEventFilter>('all')
const dialogRef = ref<HTMLDialogElement | null>(null)
const dialogReturnFocus = ref<HTMLElement | null>(null)
const tradeSide = ref<TradeSide>('BUY')
const tradeSignal = ref<StockSignalItem | null>(null)
const tradeProposal = ref<TradeProposalInput | null>(null)
const tradeDecision = ref<TradeRiskDecision | null>(null)
const tradeChecking = ref(false)
const tradeSubmitting = ref(false)
const tradeError = ref('')
const toast = ref('')

let loadEpoch = 0
let chartEpoch = 0
let tradeEpoch = 0
let syncEpoch = 0
let toastTimer: number | undefined

const forecast = computed(() => overview.value?.forecast ?? null)
const alert = computed(() => overview.value?.alert ?? null)
const quote = computed(() => overview.value?.quote ?? null)
const score = computed(() => overview.value?.score ?? null)
const horizons = computed(() => forecast.value?.horizons ?? {})

const CHART_TABS: Array<{ key: ChartTab; label: string }> = [
  { key: 'd', label: '日 K' },
  { key: 'w', label: '周 K' },
  { key: 'm', label: '月 K' },
  { key: 'intraday', label: '分时' },
]

const EVENT_TABS: Array<{ key: StockEventFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'disclosure', label: '公告' },
  { key: 'earnings_preview', label: '业绩' },
  { key: 'unlock', label: '解禁' },
  { key: 'dividend', label: '分红' },
]

const HORIZON_CARDS = [
  { label: '1 日', key: '1d' },
  { label: '5 日', key: '5d' },
  { label: '20 日', key: '20d' },
]

async function scrollToRequestedEventSection(): Promise<void> {
  if (route.hash !== '#stock-events') return
  await nextTick()
  const target = document.getElementById('stock-events')
  if (!target) return
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  target.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' })
}

const SCORE_KEYS = [
  { key: 'tech', label: '技术' },
  { key: 'capital', label: '资金' },
  { key: 'fundamental', label: '基本面' },
  { key: 'valuation', label: '估值' },
  { key: 'sentiment', label: '情绪' },
] as const

function finiteNumber(value: unknown): number | null {
  const number = Number(value)
  return value === null || value === undefined || !Number.isFinite(number) ? null : number
}

function bareSymbol(value: string): string {
  return value.trim().toUpperCase().replace(/^(SH|SZ)\./, '')
}

function escapeHtml(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function tradeDateLabel(value: unknown): string {
  const dateText = String(value ?? '').slice(0, 10)
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateText)
  if (!match) return '交易日期未知'
  const parsed = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return `${dateText} · ${weekdays[parsed.getDay()]}`
}

function signedPrice(value: number | null): string {
  if (value === null) return '—'
  return `${value > 0 ? '+' : ''}${fmtNum(value, 2)}`
}

function tooltipMetric(label: string, value: string): string {
  return `<div style="display:flex;align-items:baseline;justify-content:space-between;gap:12px">
    <span style="color:${CHART_COLORS.text3};font-size:11px">${escapeHtml(label)}</span>
    <span style="color:#eef2fa;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums">${escapeHtml(value)}</span>
  </div>`
}

function movingAverage(values: number[], window: number): (number | null)[] {
  return values.map((_, index) => {
    if (index < window - 1) return null
    const slice = values.slice(index - window + 1, index + 1)
    return slice.reduce((sum, value) => sum + value, 0) / window
  })
}

const plottedSignals = computed<MarkerSignal[]>(() => {
  if (!bars.value.length) return []
  const dates = bars.value.map((bar) => bar.date)
  const counts = new Map<string, number>()
  return signals.value.flatMap((signal) => {
    const plotDate = dates.find((date) => date >= signal.trade_date) ?? dates.at(-1)
    if (!plotDate) return []
    const plotBar = bars.value.find((bar) => bar.date === plotDate)
    const plotClose = finiteNumber(signal.close) ?? finiteNumber(plotBar?.close)
    if (plotClose === null) return []
    const countKey = `${plotDate}-${signal.marker}`
    const stackIndex = counts.get(countKey) ?? 0
    counts.set(countKey, stackIndex + 1)
    return [{ ...signal, plotDate, plotClose, stackIndex }]
  })
})

function signalTooltip(signal: MarkerSignal): string {
  const reasons = signal.reasons.length
    ? signal.reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join('')
    : '<li>暂无理由文本</li>'
  const color = signal.marker === 'B' ? CHART_COLORS.up : CHART_COLORS.down
  return `<div style="width:min(280px,72vw);padding:12px 14px;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif">
    <div style="display:flex;justify-content:space-between;gap:12px;padding-bottom:8px;border-bottom:1px solid ${CHART_COLORS.line1}">
      <strong style="color:${color};font-size:13px">${signal.marker === 'B' ? '买入类信号' : '减仓类信号'} · ${escapeHtml(signal.action)}</strong>
      <span style="color:${CHART_COLORS.text3};font-size:11px">${escapeHtml(signal.trade_date)}</span>
    </div>
    <ul style="margin:9px 0 0;padding-left:16px;color:#eef2fa;font-size:12px;line-height:1.65">${reasons}</ul>
    <div style="margin-top:8px;color:${CHART_COLORS.text3};font-size:10px">置信度 ${escapeHtml(fmtPct(signal.confidence, 0, false))} · 提醒 #${signal.id}</div>
  </div>`
}

function formatKlineTooltip(params: any, averages: Record<string, (number | null)[]>): string {
  if (!Array.isArray(params) && params?.componentType === 'markPoint' && params?.data?.signal) {
    return signalTooltip(params.data.signal as MarkerSignal)
  }
  const points = Array.isArray(params) ? params : [params]
  const dataIndex = points.find((point) => Number.isInteger(point?.dataIndex))?.dataIndex
  if (dataIndex === undefined) return ''
  const bar = bars.value[dataIndex]
  if (!bar) return ''
  const open = finiteNumber(bar.open)
  const close = finiteNumber(bar.close)
  const low = finiteNumber(bar.low)
  const high = finiteNumber(bar.high)
  const previousClose = dataIndex > 0 ? finiteNumber(bars.value[dataIndex - 1]?.close) : null
  const change = close !== null && previousClose !== null ? close - previousClose : null
  const changePct = change !== null && previousClose ? (change / previousClose) * 100 : null
  const amplitude = high !== null && low !== null && previousClose ? ((high - low) / previousClose) * 100 : null
  const trendColor =
    change === null || change === 0
      ? CHART_COLORS.text2
      : change > 0
        ? CHART_COLORS.up
        : CHART_COLORS.down
  const changeLabel = change === null ? '首个交易日' : `${signedPrice(change)} · ${fmtPct(changePct)}`
  return `<div style="width:min(304px,72vw);padding:12px 14px;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding-bottom:10px;border-bottom:1px solid ${CHART_COLORS.line1}">
      <div><div style="color:#eef2fa;font-size:13px;font-weight:650">${tradeDateLabel(bar.date)}</div><div style="margin-top:2px;color:${CHART_COLORS.text3};font-size:10px">${escapeHtml(CHART_TABS.find((item) => item.key === chartTab.value)?.label ?? 'K 线')}</div></div>
      <div style="color:${trendColor};font-size:12px;font-weight:650;white-space:nowrap">${escapeHtml(changeLabel)}</div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:22px;row-gap:7px;padding:10px 0">
      ${tooltipMetric('开盘', fmtNum(open, 2))}${tooltipMetric('最高', fmtNum(high, 2))}
      ${tooltipMetric('收盘', fmtNum(close, 2))}${tooltipMetric('最低', fmtNum(low, 2))}
      ${tooltipMetric('前收', fmtNum(previousClose, 2))}${tooltipMetric('振幅', amplitude === null ? '—' : fmtPct(amplitude))}
      ${tooltipMetric('成交量', bar.volume === null ? '—' : `${fmtAmount(bar.volume)}股`)}${tooltipMetric('成交额', bar.amount === null ? '—' : fmtAmount(bar.amount))}
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;padding-top:10px;border-top:1px solid ${CHART_COLORS.line1}">
      <div style="color:${CHART_COLORS.warn};font-size:10px">MA5 <span style="display:block;color:#eef2fa;font-size:11px">${fmtNum(averages.MA5?.[dataIndex], 2)}</span></div>
      <div style="color:${CHART_COLORS.cyan};font-size:10px">MA20 <span style="display:block;color:#eef2fa;font-size:11px">${fmtNum(averages.MA20?.[dataIndex], 2)}</span></div>
      <div style="color:${CHART_COLORS.purple};font-size:10px">MA60 <span style="display:block;color:#eef2fa;font-size:11px">${fmtNum(averages.MA60?.[dataIndex], 2)}</span></div>
    </div>
  </div>`
}

const klineOption = computed(() => {
  if (!bars.value.length) return {}
  const dates = bars.value.map((bar) => bar.date)
  const closes = bars.value.map((bar) => Number(bar.close))
  const averages = {
    MA5: movingAverage(closes, 5),
    MA20: movingAverage(closes, 20),
    MA60: movingAverage(closes, 60),
  }
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle,
      confine: true,
      padding: 0,
      axisPointer: {
        type: 'cross',
        lineStyle: { color: 'rgba(148,163,198,.55)', type: 'dashed' },
        crossStyle: { color: 'rgba(148,163,198,.55)', type: 'dashed' },
        label: { show: false },
      },
      formatter: (params: any) => formatKlineTooltip(params, averages),
    },
    legend: {
      data: ['MA5', 'MA20', 'MA60'],
      textStyle: { color: CHART_COLORS.text3, fontSize: 10 },
      top: 0,
      itemWidth: 14,
      itemHeight: 2,
    },
    grid: [
      { left: 50, right: 12, top: 26, height: '61%' },
      { left: 50, right: 12, top: '79%', height: '14%' },
    ],
    xAxis: [
      {
        type: 'category',
        data: dates,
        axisLabel: { color: CHART_COLORS.text3, fontSize: 10 },
        axisLine: { lineStyle: { color: CHART_COLORS.line2 } },
        axisTick: { show: false },
      },
      { type: 'category', gridIndex: 1, data: dates, show: false },
    ],
    yAxis: [
      {
        scale: true,
        splitLine: { lineStyle: { color: CHART_COLORS.line1 } },
        axisLabel: { color: CHART_COLORS.text3, fontSize: 10 },
      },
      { gridIndex: 1, show: false },
    ],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 30, end: 100 }],
    series: [
      {
        name: 'K',
        type: 'candlestick',
        data: bars.value.map((bar) => [bar.open, bar.close, bar.low, bar.high]),
        itemStyle: {
          color: CHART_COLORS.up,
          color0: CHART_COLORS.down,
          borderColor: CHART_COLORS.up,
          borderColor0: CHART_COLORS.down,
        },
        markPoint: {
          symbol: 'pin',
          symbolSize: 38,
          tooltip: { trigger: 'item' },
          data: plottedSignals.value.map((signal) => ({
            name: signal.marker,
            value: signal.marker,
            coord: [signal.plotDate, signal.plotClose],
            symbolOffset: [0, signal.marker === 'B' ? -18 - signal.stackIndex * 14 : 18 + signal.stackIndex * 14],
            itemStyle: { color: signal.marker === 'B' ? CHART_COLORS.up : CHART_COLORS.down },
            label: { color: '#04100b', fontSize: 10, fontWeight: 800 },
            signal,
          })),
        },
      },
      { name: 'MA5', type: 'line', data: averages.MA5, symbol: 'none', lineStyle: { width: 1, color: CHART_COLORS.warn } },
      { name: 'MA20', type: 'line', data: averages.MA20, symbol: 'none', lineStyle: { width: 1, color: CHART_COLORS.cyan } },
      { name: 'MA60', type: 'line', data: averages.MA60, symbol: 'none', lineStyle: { width: 1, color: CHART_COLORS.purple } },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: bars.value.map((bar) => ({
          value: bar.volume,
          itemStyle: {
            color: Number(bar.close) >= Number(bar.open) ? 'rgba(52,211,153,.45)' : 'rgba(248,113,113,.45)',
          },
        })),
      },
    ],
  }
})

const intradayOption = computed(() => {
  if (!intraday.value.length) return {}
  const labels = intraday.value.map((point) => point.time)
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle,
      formatter: (params: any[]) => {
        const price = params.find((item) => item.seriesName === '现价')
        const average = params.find((item) => item.seriesName === '均价')
        return `${escapeHtml(price?.axisValueLabel ?? '')}<br/>现价 ${escapeHtml(fmtNum(price?.value, 2))}<br/>均价 ${escapeHtml(fmtNum(average?.value, 2))}`
      },
    },
    grid: { left: 52, right: 14, top: 24, bottom: 30 },
    xAxis: {
      type: 'category',
      data: labels,
      boundaryGap: false,
      axisLine: { lineStyle: { color: CHART_COLORS.line2 } },
      axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.text3, fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      scale: true,
      splitLine: { lineStyle: { color: CHART_COLORS.line1 } },
      axisLabel: { color: CHART_COLORS.text3, fontSize: 10 },
    },
    series: [
      {
        name: '现价',
        type: 'line',
        data: intraday.value.map((point) => point.price),
        symbol: 'none',
        lineStyle: glowLine(CHART_COLORS.cyan, 1.8),
        areaStyle: { color: areaGradient(CHART_COLORS.cyan, 0.2) },
      },
      {
        name: '均价',
        type: 'line',
        data: intraday.value.map((point) => point.avg_price),
        symbol: 'none',
        lineStyle: { color: CHART_COLORS.warn, width: 1.1, type: 'dashed' },
      },
    ],
  }
})

const radarOption = computed(() => {
  if (!score.value) return {}
  return {
    animationDuration: 220,
    tooltip: { ...tooltipStyle, trigger: 'item' },
    radar: {
      radius: '62%',
      splitNumber: 5,
      indicator: score.value.radar.map((item) => ({ name: item.name, max: item.max })),
      axisName: { color: CHART_COLORS.text2, fontSize: 11 },
      axisLine: { lineStyle: { color: CHART_COLORS.line2 } },
      splitLine: { lineStyle: { color: CHART_COLORS.line2 } },
      splitArea: { areaStyle: { color: ['rgba(59,130,246,.015)', 'rgba(59,130,246,.045)'] } },
    },
    series: [
      {
        type: 'radar',
        data: [{ value: score.value.radar.map((item) => item.value), name: '五维评分' }],
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { color: CHART_COLORS.cyan, width: 1.5 },
        itemStyle: { color: CHART_COLORS.cyan },
        areaStyle: { color: 'rgba(34,211,238,.14)' },
      },
    ],
  }
})

function scoreValue(key: (typeof SCORE_KEYS)[number]['key']): number | null {
  return finiteNumber(score.value?.[key])
}

function scoreDegraded(key: (typeof SCORE_KEYS)[number]['key']): boolean {
  return score.value?.radar.find((item) => item.key === key)?.degraded ?? true
}

function calendarKind(eventType: string): StockEventFilter | 'other' {
  const type = eventType.toLowerCase()
  if (type.includes('unlock')) return 'unlock'
  if (type.includes('dividend')) return 'dividend'
  if (type.includes('earn') || type.includes('report') || type.includes('preview')) return 'earnings_preview'
  return 'other'
}

function eventDisplayDate(value: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return fmtDate(value)
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(parsed)
  const byType = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${byType.year}-${byType.month}-${byType.day}`
}

const allEvents = computed<EventItem[]>(() => {
  const disclosures = (overview.value?.disclosures ?? []).map((item) => ({
    key: `disclosure-${item.id}`,
    kind: 'disclosure' as const,
    rawType: 'disclosure',
    date: item.published_at,
    title: item.title,
    source: '巨潮公告',
    url: item.url,
  }))
  const calendar = calendarEvents.value.map((item) => ({
    key: `calendar-${item.id}`,
    kind: calendarKind(item.event_type),
    rawType: item.event_type,
    date: item.event_date,
    title: item.title,
    source: item.source,
    url: null,
  }))
  return [...disclosures, ...calendar].sort((left, right) => right.date.localeCompare(left.date))
})

const filteredEvents = computed(() =>
  eventFilter.value === 'all'
    ? allEvents.value
    : allEvents.value.filter((item) => item.kind === eventFilter.value),
)

function eventLabel(item: EventItem): string {
  return EVENT_TABS.find((tab) => tab.key === item.kind)?.label ?? item.rawType
}

function eventBadge(item: EventItem): string {
  if (item.kind === 'disclosure') return 'blue'
  if (item.kind === 'unlock') return 'red'
  if (item.kind === 'dividend') return 'green'
  return 'yellow'
}

const latestBuySignal = computed(() => latestSignal('BUY'))
const latestSellSignal = computed(() => latestSignal('SELL'))

function latestSignal(side: TradeSide): StockSignalItem | null {
  const marker = side === 'BUY' ? 'B' : 'S'
  return [...signals.value]
    .filter((item) => item.marker === marker && item.trade_eligible)
    .sort((left, right) =>
      `${right.trade_date}-${String(right.id).padStart(12, '0')}`.localeCompare(
        `${left.trade_date}-${String(left.id).padStart(12, '0')}`,
      ),
    )[0] ?? null
}

function showToast(message: string): void {
  toast.value = message
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => {
    toast.value = ''
  }, 6000)
}

async function loadChart(tab: ChartTab): Promise<void> {
  const epoch = ++chartEpoch
  const requestedSymbol = symbol.value
  chartLoading.value = true
  sectionErrors.value.chart = ''
  selectedChartSignal.value = null
  try {
    if (tab === 'intraday') {
      const response = await api.stockIntraday(requestedSymbol)
      if (epoch !== chartEpoch || tab !== chartTab.value || requestedSymbol !== symbol.value) return
      const entry = Object.entries(response).find(([key]) => bareSymbol(key) === bareSymbol(requestedSymbol))
      if (!entry) throw new Error(`分时响应缺少 ${requestedSymbol}，已拒绝展示其他股票数据。`)
      intraday.value = entry[1]
      chartSource.value = 'Futu OpenD · RT_DATA'
      chartWarnings.value = []
      return
    }
    const response = await api.stockBars(requestedSymbol, 160, tab)
    if (
      epoch !== chartEpoch
      || tab !== chartTab.value
      || requestedSymbol !== symbol.value
      || response.symbol !== requestedSymbol
    ) return
    const nextBars = response.bars ?? []
    bars.value = nextBars
    chartSource.value = response.source
    chartWarnings.value = response.warnings ?? []
    const start = nextBars[0]?.date
    const end = nextBars.at(-1)?.date
    try {
      const signalResponse = await api.stockSignals(requestedSymbol, start, end)
      if (
        epoch !== chartEpoch
        || tab !== chartTab.value
        || requestedSymbol !== symbol.value
        || signalResponse.symbol !== requestedSymbol
      ) return
      signals.value = signalResponse.signals ?? []
      chartWarnings.value = [...(response.warnings ?? []), ...(signalResponse.warnings ?? [])]
    } catch (exc: any) {
      if (epoch !== chartEpoch || tab !== chartTab.value || requestedSymbol !== symbol.value) return
      signals.value = []
      chartWarnings.value = [...(response.warnings ?? []), `B/S 信号不可用：${String(exc?.message || exc)}`]
    }
  } catch (exc: any) {
    if (epoch !== chartEpoch || tab !== chartTab.value) return
    sectionErrors.value.chart = String(exc?.message || exc)
    if (tab === 'intraday') intraday.value = []
    else {
      bars.value = []
      signals.value = []
    }
  } finally {
    if (epoch === chartEpoch) chartLoading.value = false
  }
}

function selectChartSignal(signal: unknown): void {
  if (!signal || typeof signal !== 'object') return
  const id = Number((signal as StockSignalItem).id)
  selectedChartSignal.value = plottedSignals.value.find((item) => item.id === id) ?? null
}

async function selectChart(tab: ChartTab): Promise<void> {
  if (chartTab.value === tab && (bars.value.length || intraday.value.length)) return
  chartTab.value = tab
  await loadChart(tab)
}

function onChartTabKey(event: KeyboardEvent, index: number): void {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  let nextIndex = index
  if (event.key === 'ArrowLeft') nextIndex = (index - 1 + CHART_TABS.length) % CHART_TABS.length
  if (event.key === 'ArrowRight') nextIndex = (index + 1) % CHART_TABS.length
  if (event.key === 'Home') nextIndex = 0
  if (event.key === 'End') nextIndex = CHART_TABS.length - 1
  void selectChart(CHART_TABS[nextIndex].key)
  nextTick(() => document.querySelectorAll<HTMLButtonElement>('[data-chart-tab]')[nextIndex]?.focus())
}

function onEventTabKey(event: KeyboardEvent, index: number): void {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  let nextIndex = index
  if (event.key === 'ArrowLeft') nextIndex = (index - 1 + EVENT_TABS.length) % EVENT_TABS.length
  if (event.key === 'ArrowRight') nextIndex = (index + 1) % EVENT_TABS.length
  if (event.key === 'Home') nextIndex = 0
  if (event.key === 'End') nextIndex = EVENT_TABS.length - 1
  eventFilter.value = EVENT_TABS[nextIndex].key
  nextTick(() => document.querySelectorAll<HTMLButtonElement>('[data-event-tab]')[nextIndex]?.focus())
}

async function load(): Promise<void> {
  const epoch = ++loadEpoch
  const requestedSymbol = symbol.value
  chartEpoch += 1
  syncEpoch += 1
  tradeEpoch += 1
  if (dialogRef.value?.open) dialogRef.value.close()
  tradeSignal.value = null
  tradeProposal.value = null
  tradeDecision.value = null
  tradeError.value = ''
  tradeChecking.value = false
  tradeSubmitting.value = false
  loading.value = true
  error.value = ''
  overview.value = null
  insight.value = null
  calendarEvents.value = []
  bars.value = []
  signals.value = []
  intraday.value = []
  chartTab.value = 'd'
  sectionErrors.value = { insight: '', calendar: '', chart: '' }
  selectedChartSignal.value = null
  syncing.value = false

  void loadChart('d')
  insightLoading.value = true
  calendarLoading.value = true

  void api.stockInsight(requestedSymbol).then((response) => {
    if (epoch === loadEpoch && requestedSymbol === symbol.value && response.symbol === requestedSymbol) {
      insight.value = response
    }
  }).catch((exc: any) => {
    if (epoch === loadEpoch && requestedSymbol === symbol.value) {
      sectionErrors.value.insight = String(exc?.message || exc)
    }
  }).finally(() => {
    if (epoch === loadEpoch && requestedSymbol === symbol.value) insightLoading.value = false
  })

  void api.stockCalendar(requestedSymbol, 90).then((response) => {
    if (epoch === loadEpoch && requestedSymbol === symbol.value && response.symbol === requestedSymbol) {
      calendarEvents.value = response.events ?? []
    }
  }).catch((exc: any) => {
    if (epoch === loadEpoch && requestedSymbol === symbol.value) {
      sectionErrors.value.calendar = String(exc?.message || exc)
    }
  }).finally(() => {
    if (epoch === loadEpoch && requestedSymbol === symbol.value) calendarLoading.value = false
  })

  try {
    const response = await api.stockOverview(requestedSymbol)
    if (epoch !== loadEpoch || requestedSymbol !== symbol.value) return
    if (response.symbol !== requestedSymbol) throw new Error(`个股响应串线：期望 ${requestedSymbol}，实际 ${response.symbol}`)
    overview.value = response
    await scrollToRequestedEventSection()
  } catch (exc: any) {
    if (epoch === loadEpoch && requestedSymbol === symbol.value) {
      error.value = String(exc?.message || exc)
    }
  } finally {
    if (epoch === loadEpoch && requestedSymbol === symbol.value) loading.value = false
  }
}

async function syncCninfo(): Promise<void> {
  const epoch = ++syncEpoch
  const requestedSymbol = symbol.value
  syncing.value = true
  try {
    const syncResponse = await api.syncDisclosures(requestedSymbol)
    if (epoch !== syncEpoch || requestedSymbol !== symbol.value) return
    if (syncResponse?.symbol && syncResponse.symbol !== requestedSymbol) {
      throw new Error(`公告同步响应串线：期望 ${requestedSymbol}，实际 ${syncResponse.symbol}`)
    }
    const response = await api.stockOverview(requestedSymbol)
    if (epoch !== syncEpoch || requestedSymbol !== symbol.value) return
    if (response.symbol !== requestedSymbol) throw new Error(`个股响应串线：期望 ${requestedSymbol}，实际 ${response.symbol}`)
    overview.value = response
  } catch (exc: any) {
    if (epoch === syncEpoch && requestedSymbol === symbol.value) {
      error.value = String(exc?.message || exc)
    }
  } finally {
    if (epoch === syncEpoch) syncing.value = false
  }
}

function proposalPreconditions(signal: StockSignalItem, side: TradeSide): string[] {
  const reasons: string[] = []
  const last = finiteNumber(quote.value?.last)
  const targetLow = finiteNumber(signal.target_low)
  const targetHigh = finiteNumber(signal.target_high)
  const suggestedNotional = Math.abs(finiteNumber(signal.suggested_notional) ?? 0)
  const rawSuggestedNotional = finiteNumber(signal.suggested_notional)
  const quoteSource = String(quote.value?.source || '').trim().toLowerCase()
  if (!signal.trade_eligible) reasons.push('来源提醒未通过服务端可交易性校验。')
  if (signal.marker !== (side === 'BUY' ? 'B' : 'S')) reasons.push('提醒方向与当前操作不一致。')
  if (last === null || last <= 0) reasons.push('缺少可审计的实时价格。')
  if (!quote.value?.as_of) reasons.push('行情时间缺失，不能用本机时间代替。')
  if (!quoteSource || quoteSource.includes('mock') || quoteSource === 'unavailable') {
    reasons.push('当前报价没有可审计的真实行情来源。')
  }
  if (!signal.model_version) reasons.push('来源提醒缺少模型版本。')
  if (
    targetLow === null
    || targetHigh === null
    || targetLow <= 0
    || targetHigh <= targetLow
  ) {
    reasons.push('来源提醒缺少有效目标区间。')
  } else if (last !== null && (targetHigh < last * 0.5 || targetLow > last * 1.5)) {
    reasons.push('来源提醒的目标区间与当前真实价格不在同一量级，已拒绝生成提案。')
  }
  if (suggestedNotional <= 0) reasons.push('来源提醒没有正数建议金额。')
  if (
    rawSuggestedNotional !== null
    && ((side === 'BUY' && rawSuggestedNotional < 0) || (side === 'SELL' && rawSuggestedNotional > 0))
  ) {
    reasons.push('来源提醒的建议金额方向与当前操作不一致。')
  }
  if (last !== null && Math.floor(suggestedNotional / last / 100) * 100 < 100) {
    reasons.push('建议金额不足一手，不能强制放大到 100 股。')
  }
  return reasons
}

function buildProposal(signal: StockSignalItem, side: TradeSide): TradeProposalInput | null {
  const last = finiteNumber(quote.value?.last)
  const suggestedNotional = Math.abs(finiteNumber(signal.suggested_notional) ?? 0)
  if (last === null || !quote.value?.as_of || !signal.model_version) return null
  const quantity = Math.floor(suggestedNotional / last / 100) * 100
  if (quantity < 100) return null
  const proposalId = `stock-${signal.id}-${side.toLowerCase()}`
  return {
    proposal_id: proposalId,
    idempotency_key: proposalId,
    symbol: symbol.value,
    side,
    quantity,
    estimated_notional: Number((quantity * last).toFixed(2)),
    confidence: signal.confidence,
    market_data_as_of: quote.value.as_of,
    model_version: signal.model_version,
    mode: 'confirm_to_trade',
    source_alert_id: signal.id,
    metadata: {
      source: 'stock-detail',
      target_low: signal.target_low,
      target_high: signal.target_high,
      suggested_notional: signal.suggested_notional,
      quote_source: quote.value.source,
    },
  }
}

async function openTrade(side: TradeSide, event: MouseEvent): Promise<void> {
  const signal = side === 'BUY' ? latestBuySignal.value : latestSellSignal.value
  if (!signal) return
  const epoch = ++tradeEpoch
  dialogReturnFocus.value = event.currentTarget as HTMLElement
  tradeSide.value = side
  tradeSignal.value = signal
  tradeProposal.value = buildProposal(signal, side)
  tradeDecision.value = null
  tradeError.value = ''
  tradeChecking.value = true
  await nextTick()
  dialogRef.value?.showModal()

  const proposal = tradeProposal.value
  const preconditions = proposalPreconditions(signal, side)
  if (preconditions.length || !tradeProposal.value) {
    tradeDecision.value = {
      approved: false,
      reasons: preconditions.length ? preconditions : ['无法由当前真实行情构造交易提案。'],
      evaluated_at: '',
      requires_human_confirmation: true,
    }
    tradeChecking.value = false
    return
  }
  try {
    const decision = await api.evaluateTrade({ proposal: proposal! })
    if (epoch !== tradeEpoch || !dialogRef.value?.open) return
    tradeDecision.value = decision
  } catch (exc: any) {
    if (epoch !== tradeEpoch || !dialogRef.value?.open) return
    tradeDecision.value = {
      approved: false,
      reasons: [String(exc?.message || exc)],
      evaluated_at: '',
      requires_human_confirmation: true,
    }
  } finally {
    if (epoch === tradeEpoch) tradeChecking.value = false
  }
}

function closeTrade(): void {
  if (!tradeSubmitting.value) dialogRef.value?.close()
}

function onDialogCancel(event: Event): void {
  if (tradeSubmitting.value) event.preventDefault()
}

function onDialogClose(): void {
  tradeEpoch += 1
  tradeChecking.value = false
  tradeSubmitting.value = false
  tradeError.value = ''
  nextTick(() => dialogReturnFocus.value?.focus())
}

async function submitTradeProposal(): Promise<void> {
  if (!tradeProposal.value || !tradeDecision.value?.approved || tradeSubmitting.value) return
  const epoch = tradeEpoch
  const requestedSymbol = symbol.value
  const proposal = tradeProposal.value
  tradeSubmitting.value = true
  tradeError.value = ''
  let persisted = null as Awaited<ReturnType<typeof api.createProposal>>['proposal'] | null
  try {
    const response = await api.createProposal({ proposal })
    if (epoch !== tradeEpoch || requestedSymbol !== symbol.value || !dialogRef.value?.open) return
    if (!response.risk_decision.approved || response.proposal.status !== 'pending') {
      tradeError.value = response.risk_decision.reasons.join('；') || `提案状态为 ${response.proposal.status}，未进入待审队列。`
      return
    }
    persisted = response.proposal
  } catch (exc: any) {
    if (exc instanceof ApiError && exc.status === 409) {
      let response: Awaited<ReturnType<typeof api.proposals>>
      try {
        response = await api.proposals()
      } catch (listExc: any) {
        if (epoch === tradeEpoch && requestedSymbol === symbol.value) {
          tradeError.value = `提案可能已存在，但审计列表刷新失败：${String(listExc?.message || listExc)}`
        }
        return
      }
      if (epoch !== tradeEpoch || requestedSymbol !== symbol.value || !dialogRef.value?.open) return
      persisted = response.proposals.find((item) =>
        item.proposal_id === proposal.proposal_id || item.idempotency_key === proposal.idempotency_key,
      ) ?? null
      if (!persisted) {
        tradeError.value = exc.message
        return
      }
      if (persisted.status !== 'pending') {
        tradeError.value = `相同提案已存在，但状态为 ${persisted.status}，未进入待审队列。`
        return
      }
    } else {
      if (epoch === tradeEpoch && requestedSymbol === symbol.value) {
        tradeError.value = String(exc?.message || exc)
      }
      return
    }
  } finally {
    if (epoch === tradeEpoch && requestedSymbol === symbol.value) tradeSubmitting.value = false
  }
  if (
    !persisted
    || epoch !== tradeEpoch
    || requestedSymbol !== symbol.value
    || !dialogRef.value?.open
  ) return
  dialogRef.value?.close()
  showToast('提案已入库待人工审核；本页不会批准或执行')
}

function holdPosition(): void {
  showToast('已保持观察；“持有”不会创建提案或订单')
}

function gotoAlerts(): void {
  toast.value = ''
  void router.push('/alerts')
}

onMounted(load)
watch(symbol, load)
watch(
  () => route.hash,
  () => void scrollToRequestedEventSection(),
)
onBeforeUnmount(() => {
  loadEpoch += 1
  chartEpoch += 1
  tradeEpoch += 1
  syncEpoch += 1
  window.clearTimeout(toastTimer)
})
</script>

<template>
  <div class="stock-page">
    <div class="page-head stock-page-head">
      <div>
        <h1>个股分析</h1>
        <div class="sub">行情、模型、事件与提案风控统一视图</div>
      </div>
      <span class="stock-code mono">{{ symbol }}</span>
    </div>

    <div v-if="error" class="banner error stock-banner" role="alert">加载失败：{{ error }}</div>

    <div v-if="loading && !overview" class="stock-skeleton">
      <div class="skeleton" style="height: 146px" />
      <div class="stock-layout">
        <div class="grid"><div class="skeleton" style="height: 430px" /><div class="skeleton" style="height: 280px" /></div>
        <div class="grid"><div class="skeleton" style="height: 300px" /><div class="skeleton" style="height: 240px" /></div>
      </div>
    </div>

    <template v-if="overview">
      <section class="panel stock-quote-panel" aria-labelledby="stock-name">
        <div class="quote-primary">
          <div class="identity-line">
            <h2 id="stock-name">{{ overview.security?.name || symbol }}</h2>
            <span class="mono dim">{{ symbol }}</span>
            <span v-if="overview.security?.board" class="badge gray">{{ overview.security.board }}</span>
          </div>
          <div class="price-line">
            <strong class="num" :class="pctClass(quote?.change_pct)">{{ fmtNum(quote?.last) }}</strong>
            <span class="num" :class="pctClass(quote?.change_pct)">{{ fmtPct(quote?.change_pct) }}</span>
          </div>
          <div class="quote-stamp">
            {{ quote?.source || '行情源未知' }} · {{ quote?.as_of ? fmtTime(quote.as_of) : '行情时间缺失' }}
          </div>
        </div>

        <dl class="quote-grid">
          <div><dt>今开</dt><dd class="num">{{ fmtNum(quote?.open) }}</dd></div>
          <div><dt>最高</dt><dd class="num up">{{ fmtNum(quote?.high) }}</dd></div>
          <div><dt>最低</dt><dd class="num down">{{ fmtNum(quote?.low) }}</dd></div>
          <div><dt>换手率</dt><dd class="num">{{ quote?.turnover_rate == null ? '—' : fmtPct(quote.turnover_rate) }}</dd></div>
          <div><dt>市盈率 TTM</dt><dd class="num">{{ fmtNum(quote?.pe_ttm) }}</dd></div>
          <div><dt>总市值</dt><dd class="num">{{ fmtAmount(quote?.market_cap) }}</dd></div>
        </dl>

        <div class="ai-rating">
          <span>AI 评级</span>
          <strong class="num glow-cyan">{{ score ? fmtNum(score.composite, 1) : '—' }}<small>/10</small></strong>
          <span class="badge" :class="actionMeta(alert?.action).cls">{{ actionMeta(alert?.action).label }}</span>
          <em v-if="score?.degraded">部分因子降级</em>
          <em v-else-if="score">评分日期 {{ score.trade_date }}</em>
        </div>
      </section>

      <section class="panel horizon-strip" aria-label="上涨概率预测">
        <div v-for="card in HORIZON_CARDS" :key="card.key" class="horizon-cell">
          <span>{{ card.label }}上涨概率</span>
          <strong class="num" :class="(horizons[card.key]?.p_up ?? 0) >= 0.5 ? 'up' : 'down'">
            {{ horizons[card.key] ? fmtPct(horizons[card.key].p_up, 0, false) : '—' }}
          </strong>
          <small class="num">期望 {{ horizons[card.key] ? fmtPct(horizons[card.key].expected_return, 2, false) : '—' }}</small>
        </div>
      </section>

      <div class="stock-layout">
        <main class="stock-main">
          <section class="panel chart-panel" aria-labelledby="price-chart-title">
            <div class="panel-title chart-title-row">
              <div>
                <span id="price-chart-title">价格走势</span>
                <span class="chart-meta mono">
                  <template v-if="chartTab === 'intraday'">{{ intraday.length }} 个分时点</template>
                  <template v-else>{{ bars[0]?.date || '—' }} → {{ bars.at(-1)?.date || '—' }} · {{ bars.length }} 根</template>
                </span>
              </div>
              <div class="tab-pills" role="tablist" aria-label="价格周期">
                <button
                  v-for="(tab, index) in CHART_TABS"
                  :key="tab.key"
                  data-chart-tab
                  type="button"
                  role="tab"
                  :aria-selected="chartTab === tab.key"
                  :tabindex="chartTab === tab.key ? 0 : -1"
                  :class="{ on: chartTab === tab.key }"
                  @click="selectChart(tab.key)"
                  @keydown="onChartTabKey($event, index)"
                >
                  {{ tab.label }}
                </button>
              </div>
            </div>
            <div class="chart-evidence">
              <span>{{ chartSource || '等待行情源' }}</span>
              <span v-if="chartTab !== 'intraday'">B/S {{ plottedSignals.length }} 条</span>
              <span v-if="chartWarnings.length" class="warn-text" :title="chartWarnings.join('；')">{{ chartWarnings.join('；') }}</span>
            </div>
            <div v-if="chartLoading" class="skeleton chart-skeleton" />
            <div v-else-if="sectionErrors.chart" class="local-empty" role="status">
              <AlertTriangle :size="15" /> {{ sectionErrors.chart }}
            </div>
            <EChart
              v-else-if="chartTab === 'intraday' && intraday.length"
              :option="intradayOption"
              height="390px"
              :aria-label="`${symbol} 当日分时行情`"
            />
            <EChart
              v-else-if="chartTab !== 'intraday' && bars.length"
              :option="klineOption"
              height="390px"
              :aria-label="`${symbol} ${CHART_TABS.find((item) => item.key === chartTab)?.label}，含 ${plottedSignals.length} 条买卖信号`"
              @mark-point-click="selectChartSignal"
            />
            <div v-else class="local-empty">当前周期暂无可展示行情</div>
            <div v-if="chartTab !== 'intraday' && plottedSignals.length" class="signal-browser" aria-label="图中买卖信号">
              <div class="signal-buttons">
                <button
                  v-for="item in plottedSignals"
                  :key="item.id"
                  type="button"
                  :class="['signal-chip', item.marker === 'B' ? 'buy' : 'sell', { on: selectedChartSignal?.id === item.id }]"
                  :aria-pressed="selectedChartSignal?.id === item.id"
                  @click="selectChartSignal(item)"
                >
                  <strong>{{ item.marker }}</strong>
                  <span class="mono">{{ item.trade_date }}</span>
                  <em>{{ item.action }}</em>
                </button>
              </div>
              <div v-if="selectedChartSignal" class="signal-detail" role="status" tabindex="0">
                <div>
                  <strong>{{ selectedChartSignal.marker === 'B' ? '买入类信号' : '减仓类信号' }} · {{ selectedChartSignal.action }}</strong>
                  <span class="mono">{{ selectedChartSignal.trade_date }} · 置信 {{ fmtPct(selectedChartSignal.confidence, 0, false) }}</span>
                </div>
                <ul>
                  <li v-for="(reason, index) in selectedChartSignal.reasons" :key="index">{{ reason }}</li>
                  <li v-if="!selectedChartSignal.reasons.length">暂无理由文本</li>
                </ul>
              </div>
            </div>
          </section>

          <section class="panel score-panel" aria-labelledby="score-title">
            <div class="panel-title">
              <span id="score-title">五维评分</span>
              <span class="extra" v-if="score">{{ score.model_version }} · 覆盖 {{ fmtPct(score.input_coverage, 0, false) }}</span>
            </div>
            <template v-if="score">
              <div class="score-content">
                <div class="score-list">
                  <div v-for="item in SCORE_KEYS" :key="item.key" class="score-row">
                    <div class="score-label">
                      <span>{{ item.label }}</span>
                      <span v-if="scoreDegraded(item.key)" class="badge yellow">降级</span>
                    </div>
                    <strong class="num">{{ fmtNum(scoreValue(item.key), 1) }}</strong>
                    <div class="mini-bar" aria-hidden="true">
                      <i :style="{ width: `${Math.max(0, Math.min(100, (scoreValue(item.key) ?? 0) * 10))}%` }" />
                    </div>
                  </div>
                </div>
                <div class="radar-wrap">
                  <EChart :option="radarOption" height="255px" :aria-label="`${symbol} 五维评分雷达图`" />
                  <div class="radar-score" aria-hidden="true">
                    <strong class="num glow-cyan">{{ fmtNum(score.composite, 1) }}</strong><span>/10</span>
                  </div>
                </div>
              </div>
              <div v-if="score.degradation_reason" class="score-note">
                <AlertTriangle :size="13" /> {{ score.degradation_reason }}
              </div>
            </template>
            <div v-else class="local-empty">{{ overview.score_error || '暂无同日五维评分' }}</div>
          </section>

          <section id="stock-events" class="panel events-panel" aria-labelledby="events-title">
            <div class="panel-title events-title-row">
              <span id="events-title">事件日历</span>
              <button class="btn ghost event-sync" type="button" :disabled="syncing" @click="syncCninfo">
                <RefreshCw :size="12" :class="{ spin: syncing }" /> {{ syncing ? '同步中' : '同步公告' }}
              </button>
            </div>
            <div class="event-tabs" role="tablist" aria-label="事件类型">
              <button
                v-for="(tab, index) in EVENT_TABS"
                :key="tab.key"
                data-event-tab
                type="button"
                role="tab"
                :aria-selected="eventFilter === tab.key"
                :tabindex="eventFilter === tab.key ? 0 : -1"
                :class="{ on: eventFilter === tab.key }"
                @click="eventFilter = tab.key"
                @keydown="onEventTabKey($event, index)"
              >
                {{ tab.label }}
                <span class="num">{{ tab.key === 'all' ? allEvents.length : allEvents.filter((item) => item.kind === tab.key).length }}</span>
              </button>
            </div>
            <div v-if="sectionErrors.calendar && eventFilter !== 'disclosure'" class="local-warning">
              日历接口降级：{{ sectionErrors.calendar }}
            </div>
            <div v-if="calendarLoading && !calendarEvents.length" class="skeleton event-skeleton" />
            <ul v-if="filteredEvents.length" class="timeline event-list">
              <li v-for="item in filteredEvents" :key="item.key">
                <div class="event-date mono">{{ eventDisplayDate(item.date) }}</div>
                <div class="event-copy">
                  <div><span class="badge" :class="eventBadge(item)">{{ eventLabel(item) }}</span><span class="event-source">{{ item.source }}</span></div>
                  <a v-if="item.url" :href="item.url" target="_blank" rel="noopener">{{ item.title }}</a>
                  <span v-else>{{ item.title }}</span>
                </div>
              </li>
            </ul>
            <div v-else-if="!calendarLoading" class="local-empty">当前分类暂无事件；空结果不会用其他类型数据填充</div>
          </section>
        </main>

        <aside class="stock-rail">
          <section class="panel insight-panel" aria-labelledby="insight-title">
            <div class="panel-title">
              <span id="insight-title">AI 个股解读</span>
              <span v-if="insight" class="extra">{{ insight.source === 'llm' ? 'Qwen' : '规则降级' }}</span>
            </div>
            <template v-if="insight">
              <p class="core-view">{{ insight.core_view }}</p>
              <div class="drivers">
                <article v-for="(driver, index) in insight.drivers" :key="`${driver.source_ref}-${index}`">
                  <span class="badge" :class="driver.tag === '利多' ? 'green' : driver.tag === '利空' ? 'red' : 'gray'">{{ driver.tag }}</span>
                  <p>{{ driver.text }}</p>
                  <small>依据：{{ driver.source_ref }}</small>
                </article>
              </div>
              <div class="model-stamp mono">{{ insight.model_version }} · {{ fmtTime(insight.generated_at) }}</div>
            </template>
            <div v-else-if="insightLoading" class="skeleton insight-skeleton" />
            <div v-else class="local-empty">{{ sectionErrors.insight || '暂无可审计的 AI 解读' }}</div>
          </section>

          <section class="panel alert-panel" aria-labelledby="alert-title">
            <div class="panel-title"><span id="alert-title">当前模型提醒</span><span class="extra">{{ alert?.model_version }}</span></div>
            <div class="alert-heading">
              <span class="badge" :class="actionMeta(alert?.action).cls">{{ actionMeta(alert?.action).label }}</span>
              <strong class="num">置信 {{ alert ? fmtPct(alert.confidence, 0, false) : '—' }}</strong>
            </div>
            <ul class="reason-list">
              <li v-for="(reason, index) in alert?.reasons || []" :key="index">{{ reason }}</li>
            </ul>
            <div class="invalidation">
              <AlertTriangle :size="13" />
              <div><span>失效条件</span><p>{{ alert?.invalidation || '—' }}</p></div>
            </div>
          </section>

          <section class="panel evidence-panel" aria-labelledby="evidence-title">
            <div class="panel-title"><span id="evidence-title">数据口径</span></div>
            <dl>
              <div><dt>成交额</dt><dd class="num">{{ fmtAmount(quote?.amount) }}</dd></div>
              <div><dt>成交量</dt><dd class="num">{{ fmtAmount(quote?.volume) }}</dd></div>
              <div><dt>OHLC 口径</dt><dd>{{ quote?.ohlc_source || '—' }}</dd></div>
              <div><dt>OHLC 日期</dt><dd class="mono">{{ fmtDate(quote?.ohlc_trade_date) }}</dd></div>
              <div><dt>财务快照</dt><dd class="mono">{{ fmtDate(quote?.fundamentals_as_of) }}</dd></div>
              <div><dt>上市日期</dt><dd class="mono">{{ overview.security?.listed_date || '—' }}</dd></div>
            </dl>
            <div v-if="forecast?.warnings?.length" class="data-warning">
              <AlertTriangle :size="13" /> {{ forecast.warnings[0] }}
            </div>
          </section>
        </aside>
      </div>

      <section class="panel stock-actions" aria-label="交易提案操作">
        <div class="action-copy">
          <ShieldCheck :size="18" />
          <div><strong>人工确认模式</strong><span>按钮只做风控预检并生成待审提案，不会自动批准或执行订单。</span></div>
        </div>
        <div class="action-buttons">
          <button
            type="button"
            class="btn primary"
            :disabled="!latestBuySignal"
            :title="latestBuySignal ? '基于最新且已验证的买入类提醒预检' : '没有通过来源校验且仍有效的买入类提醒'"
            @click="openTrade('BUY', $event)"
          ><TrendingUp :size="14" /> 买入</button>
          <button type="button" class="btn ghost" @click="holdPosition"><Minus :size="14" /> 持有</button>
          <button
            type="button"
            class="btn danger"
            :disabled="!latestSellSignal"
            :title="latestSellSignal ? '基于最新且已验证的减仓类提醒预检' : '没有通过来源校验且仍有效的减仓类提醒'"
            @click="openTrade('SELL', $event)"
          ><TrendingDown :size="14" /> 减仓</button>
        </div>
      </section>
    </template>

    <dialog ref="dialogRef" class="trade-dialog" aria-labelledby="trade-dialog-title" @close="onDialogClose" @cancel="onDialogCancel">
      <div class="dialog-head">
        <div>
          <span>{{ tradeSide === 'BUY' ? '买入提案' : '减仓提案' }}</span>
          <h2 id="trade-dialog-title">{{ overview?.security?.name || symbol }} <em class="mono">{{ symbol }}</em></h2>
        </div>
        <button type="button" class="dialog-close" aria-label="关闭确认浮层" :disabled="tradeSubmitting" @click="closeTrade"><X :size="18" /></button>
      </div>
      <div v-if="tradeSignal" class="dialog-body">
        <dl class="trade-summary">
          <div><dt>来源提醒</dt><dd class="mono">#{{ tradeSignal.id }} · {{ tradeSignal.action }}</dd></div>
          <div><dt>目标区间</dt><dd class="num">{{ fmtNum(tradeSignal.target_low) }} – {{ fmtNum(tradeSignal.target_high) }}</dd></div>
          <div><dt>建议金额</dt><dd class="num">¥{{ fmtNum(Math.abs(Number(tradeSignal.suggested_notional)), 0) }}</dd></div>
          <div><dt>整手数量</dt><dd class="num">{{ tradeProposal?.quantity ?? '—' }} 股</dd></div>
          <div><dt>预估金额</dt><dd class="num">¥{{ fmtNum(tradeProposal?.estimated_notional, 0) }}</dd></div>
          <div><dt>行情时间</dt><dd class="mono">{{ quote?.as_of ? fmtTime(quote.as_of) : '缺失' }}</dd></div>
        </dl>
        <div class="risk-box" :class="{ pass: tradeDecision?.approved, fail: tradeDecision && !tradeDecision.approved }">
          <div class="risk-title">
            <ShieldCheck v-if="tradeDecision?.approved" :size="16" />
            <AlertTriangle v-else :size="16" />
            <strong>{{ tradeChecking ? '正在读取模拟账户并进行风控预检…' : tradeDecision?.approved ? '风控预检通过' : '风控预检未通过' }}</strong>
          </div>
          <div v-if="tradeChecking" class="skeleton" style="height: 54px; margin-top: 10px" />
          <ul v-else-if="tradeDecision?.reasons.length">
            <li v-for="(reason, index) in tradeDecision.reasons" :key="index">
              <Check v-if="tradeDecision.approved" :size="12" /><X v-else :size="12" />{{ reason }}
            </li>
          </ul>
        </div>
        <div v-if="tradeError" class="banner error" role="alert">{{ tradeError }}</div>
        <p class="dialog-notice">确认后仅写入提案审计表；仍需前往“交易提醒”页人工批准。此处不会调用批准、执行或下单接口。</p>
      </div>
      <div class="dialog-actions">
        <button type="button" class="btn ghost" :disabled="tradeSubmitting" @click="closeTrade">取消</button>
        <button
          type="button"
          class="btn primary"
          :disabled="!tradeDecision?.approved || tradeChecking || tradeSubmitting"
          @click="submitTradeProposal"
        >
          {{ tradeSubmitting ? '写入中…' : '确认生成提案' }}
        </button>
      </div>
    </dialog>

    <div v-if="toast" class="stock-toast" role="status" aria-live="polite">
      <CircleDollarSign :size="17" />
      <span>{{ toast }}</span>
      <button v-if="toast.includes('提案已入库')" type="button" @click="gotoAlerts">去查看 <ChevronRight :size="13" /></button>
      <button type="button" class="toast-close" aria-label="关闭提示" @click="toast = ''"><X :size="14" /></button>
    </div>
  </div>
</template>

<style scoped>
.stock-page {
  min-width: 0;
  padding-bottom: 12px;
}

.stock-page-head {
  align-items: center;
}

.stock-page-head > div:first-child {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.stock-code {
  margin-left: auto;
  color: var(--text-2);
  font-size: 12px;
  padding: 4px 8px;
  border: 1px solid var(--line-1);
  border-radius: var(--r-sm);
}

.stock-banner {
  margin-bottom: 12px;
}

.stock-skeleton,
.stock-main,
.stock-rail {
  display: grid;
  gap: 12px;
}

.stock-quote-panel {
  display: grid;
  grid-template-columns: minmax(230px, 0.8fr) minmax(420px, 1.45fr) minmax(150px, 0.45fr);
  gap: 20px;
  align-items: center;
  margin-bottom: 12px;
  padding: 17px 18px;
}

.identity-line,
.price-line {
  display: flex;
  align-items: baseline;
  gap: 9px;
  flex-wrap: wrap;
}

.identity-line h2 {
  font-size: 18px;
  line-height: 1.25;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.identity-line .mono {
  font-size: 11.5px;
}

.price-line {
  margin-top: 5px;
}

.price-line strong {
  font-size: 30px;
  line-height: 1.1;
  font-weight: 680;
  letter-spacing: -0.025em;
}

.price-line span {
  font-size: 13px;
  font-weight: 650;
}

.quote-stamp {
  color: var(--text-3);
  font-size: 10.5px;
  margin-top: 5px;
}

.quote-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(90px, 1fr));
  row-gap: 10px;
}

.quote-grid > div {
  padding: 0 13px;
  border-left: 1px solid var(--line-1);
}

.quote-grid dt {
  color: var(--text-3);
  font-size: 10.5px;
}

.quote-grid dd {
  margin-top: 2px;
  color: var(--text-1);
  font-size: 12px;
  font-weight: 600;
}

.ai-rating {
  min-width: 0;
  display: grid;
  justify-items: end;
  gap: 3px;
}

.ai-rating > span:first-child {
  color: var(--text-3);
  font-size: 10.5px;
}

.ai-rating strong {
  font-size: 24px;
  line-height: 1.2;
}

.ai-rating strong small {
  color: var(--text-3);
  font-size: 11px;
  font-weight: 500;
}

.ai-rating em {
  max-width: 160px;
  color: var(--text-3);
  font-size: 10px;
  font-style: normal;
  text-align: right;
}

.horizon-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-bottom: 12px;
  padding: 0;
}

.horizon-cell {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: baseline;
  gap: 2px 12px;
  padding: 12px 18px;
}

.horizon-cell + .horizon-cell {
  border-left: 1px solid var(--line-1);
}

.horizon-cell > span {
  color: var(--text-2);
  font-size: 11.5px;
}

.horizon-cell strong {
  grid-row: 1 / span 2;
  grid-column: 2;
  font-size: 21px;
  letter-spacing: -0.02em;
}

.horizon-cell small {
  color: var(--text-3);
  font-size: 10.5px;
}

.stock-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 340px);
  gap: 12px;
  align-items: start;
}

.chart-title-row > div:first-child,
.events-title-row {
  display: flex;
  align-items: center;
  gap: 9px;
}

.events-panel {
  scroll-margin-top: calc(var(--topbar-h) + var(--s3));
}

.chart-title-row {
  align-items: center;
}

.chart-meta {
  color: var(--text-3);
  font-size: 10px;
  font-weight: 400;
}

.chart-evidence {
  min-height: 20px;
  display: flex;
  align-items: center;
  gap: 12px;
  margin: -2px 0 4px;
  color: var(--text-3);
  font-size: 10.5px;
  white-space: nowrap;
  overflow: hidden;
}

.chart-evidence span {
  overflow: hidden;
  text-overflow: ellipsis;
}

.chart-evidence span + span {
  padding-left: 12px;
  border-left: 1px solid var(--line-1);
}

.chart-evidence .warn-text {
  color: var(--warn);
}

.chart-skeleton {
  height: 390px;
}

.signal-browser {
  display: grid;
  gap: 8px;
  margin-top: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--line-1);
}

.signal-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.signal-chip {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border: 1px solid var(--line-1);
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-2);
  font-size: 10.5px;
  cursor: pointer;
}

.signal-chip:hover,
.signal-chip:focus-visible,
.signal-chip.on {
  border-color: var(--line-2);
  background: rgba(148, 163, 198, 0.07);
  color: var(--text-1);
}

.signal-chip strong {
  color: var(--up);
  font-size: 12px;
}

.signal-chip.sell strong {
  color: var(--down);
}

.signal-chip em {
  color: var(--text-3);
  font-style: normal;
}

.signal-detail {
  display: grid;
  grid-template-columns: minmax(180px, 0.35fr) minmax(0, 1fr);
  gap: 14px;
  padding: 9px 10px;
  border-left: 2px solid var(--cyan);
  background: rgba(34, 211, 238, 0.035);
  color: var(--text-2);
}

.signal-detail > div {
  display: grid;
  align-content: start;
  gap: 3px;
}

.signal-detail strong {
  color: var(--text-1);
  font-size: 11.5px;
}

.signal-detail span,
.signal-detail li {
  color: var(--text-3);
  font-size: 10.5px;
  line-height: 1.55;
}

.signal-detail ul {
  margin: 0;
  padding-left: 17px;
}

.event-skeleton,
.insight-skeleton {
  min-height: 112px;
}

.local-empty,
.local-warning {
  min-height: 92px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 20px;
  color: var(--text-3);
  font-size: 12px;
  text-align: center;
}

.local-warning {
  min-height: 0;
  justify-content: flex-start;
  padding: 8px 10px;
  color: var(--warn);
  background: rgba(251, 191, 36, 0.06);
  border-radius: var(--r-sm);
  text-align: left;
}

.score-content {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) minmax(260px, 0.9fr);
  gap: 20px;
  align-items: center;
}

.score-list {
  display: grid;
  gap: 5px;
}

.score-row {
  display: grid;
  grid-template-columns: minmax(72px, 1fr) 42px minmax(90px, 1.6fr);
  gap: 10px;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--line-1);
}

.score-row:last-child {
  border-bottom: 0;
}

.score-label {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-2);
  font-size: 12px;
}

.score-label .badge {
  padding: 0 5px;
  font-size: 9px;
}

.score-row strong {
  text-align: right;
  font-size: 14px;
}

.mini-bar {
  height: 5px;
  background: rgba(148, 163, 198, 0.11);
  border-radius: 3px;
  overflow: hidden;
}

.mini-bar i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--cyan);
}

.radar-wrap {
  position: relative;
  min-width: 0;
}

.radar-score {
  position: absolute;
  inset: 50% auto auto 50%;
  transform: translate(-50%, -44%);
  display: flex;
  align-items: baseline;
  pointer-events: none;
}

.radar-score strong {
  font-size: 22px;
}

.radar-score span {
  color: var(--text-3);
  font-size: 9px;
}

.score-note,
.data-warning {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px solid var(--line-1);
  color: var(--warn);
  font-size: 10.5px;
  line-height: 1.55;
}

.event-sync {
  padding: 3px 8px;
  font-size: 11px;
}

.event-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 13px;
}

.event-tabs button {
  border: 0;
  border-radius: var(--r-sm);
  padding: 5px 10px;
  background: transparent;
  color: var(--text-3);
  font-size: 11.5px;
  white-space: nowrap;
  cursor: pointer;
}

.event-tabs button:hover {
  color: var(--text-1);
  background: rgba(148, 163, 198, 0.06);
}

.event-tabs button.on {
  color: var(--text-1);
  background: rgba(59, 130, 246, 0.16);
}

.event-tabs .num {
  margin-left: 4px;
  color: var(--text-3);
  font-size: 10px;
}

.event-list li {
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  gap: 12px;
  min-height: 48px;
}

.event-date {
  color: var(--text-3);
  font-size: 10.5px;
  padding-top: 2px;
}

.event-copy {
  min-width: 0;
  display: grid;
  gap: 4px;
  color: var(--text-2);
  font-size: 12px;
}

.event-source {
  margin-left: 7px;
  color: var(--text-3);
  font-size: 10px;
}

.event-copy > a,
.event-copy > span {
  overflow: hidden;
  text-overflow: ellipsis;
}

.core-view {
  color: var(--text-1);
  font-size: 13px;
  line-height: 1.75;
  text-wrap: pretty;
}

.drivers {
  display: grid;
  gap: 0;
  margin-top: 12px;
}

.drivers article {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 8px;
  padding: 10px 0;
  border-top: 1px solid var(--line-1);
}

.drivers article p {
  color: var(--text-2);
  font-size: 11.5px;
  line-height: 1.55;
}

.drivers article small {
  grid-column: 2;
  color: var(--text-3);
  font-size: 9.5px;
  overflow-wrap: anywhere;
}

.model-stamp {
  margin-top: 9px;
  color: var(--text-3);
  font-size: 9.5px;
}

.alert-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 9px;
}

.alert-heading strong {
  color: var(--text-2);
  font-size: 11px;
}

.reason-list {
  display: grid;
  gap: 7px;
  list-style: none;
}

.reason-list li {
  position: relative;
  padding-left: 13px;
  color: var(--text-2);
  font-size: 11.5px;
  line-height: 1.55;
}

.reason-list li::before {
  content: '›';
  position: absolute;
  left: 0;
  color: var(--accent-hi);
}

.invalidation {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--line-1);
  color: var(--warn);
}

.invalidation span {
  font-size: 10.5px;
}

.invalidation p {
  margin-top: 2px;
  color: var(--text-3);
  font-size: 10.5px;
  line-height: 1.55;
}

.evidence-panel dl {
  display: grid;
  gap: 2px;
}

.evidence-panel dl > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line-1);
  font-size: 11px;
}

.evidence-panel dl > div:last-child {
  border-bottom: 0;
}

.evidence-panel dt {
  color: var(--text-3);
}

.evidence-panel dd {
  max-width: 58%;
  color: var(--text-2);
  text-align: right;
  overflow-wrap: anywhere;
}

.stock-actions {
  display: flex;
  align-items: center;
  gap: 20px;
  margin-top: 12px;
  padding: 12px 14px;
}

.action-copy {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--accent-hi);
}

.action-copy div {
  min-width: 0;
  display: grid;
}

.action-copy strong {
  color: var(--text-1);
  font-size: 12px;
}

.action-copy span {
  color: var(--text-3);
  font-size: 10.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.action-buttons {
  margin-left: auto;
  display: flex;
  gap: 7px;
}

.action-buttons .btn {
  min-width: 82px;
}

.spin {
  animation: rotate 0.9s linear infinite;
}

@keyframes rotate {
  to { transform: rotate(360deg); }
}

.trade-dialog {
  width: min(540px, calc(100vw - 24px));
  max-height: calc(100vh - 32px);
  padding: 0;
  overflow: auto;
  border: 1px solid var(--line-2);
  border-radius: 12px;
  background: var(--surface-1);
  color: var(--text-1);
  box-shadow: 0 8px 8px rgba(0, 0, 0, 0.48);
}

.trade-dialog::backdrop {
  background: rgba(2, 5, 12, 0.76);
  backdrop-filter: blur(3px);
}

.dialog-head,
.dialog-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
}

.dialog-head {
  justify-content: space-between;
  border-bottom: 1px solid var(--line-1);
}

.dialog-head > div > span {
  color: var(--text-3);
  font-size: 10.5px;
}

.dialog-head h2 {
  margin-top: 2px;
  font-size: 16px;
  line-height: 1.3;
}

.dialog-head h2 em {
  margin-left: 5px;
  color: var(--text-3);
  font-size: 10px;
  font-style: normal;
  font-weight: 500;
}

.dialog-close,
.toast-close {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
}

.dialog-close:hover,
.toast-close:hover {
  background: rgba(148, 163, 198, 0.08);
  color: var(--text-1);
}

.dialog-body {
  display: grid;
  gap: 12px;
  padding: 16px;
}

.trade-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border: 1px solid var(--line-1);
  border-radius: var(--r-md);
}

.trade-summary > div {
  min-width: 0;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line-1);
}

.trade-summary > div:nth-child(odd) {
  border-right: 1px solid var(--line-1);
}

.trade-summary > div:nth-last-child(-n + 2) {
  border-bottom: 0;
}

.trade-summary dt {
  color: var(--text-3);
  font-size: 10px;
}

.trade-summary dd {
  margin-top: 3px;
  color: var(--text-1);
  font-size: 11.5px;
  overflow-wrap: anywhere;
}

.risk-box {
  padding: 12px;
  border: 1px solid var(--line-1);
  border-radius: var(--r-md);
  background: rgba(148, 163, 198, 0.04);
}

.risk-box.pass {
  border-color: rgba(52, 211, 153, 0.28);
  background: rgba(52, 211, 153, 0.06);
}

.risk-box.fail {
  border-color: rgba(248, 113, 113, 0.28);
  background: rgba(248, 113, 113, 0.06);
}

.risk-title {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--text-2);
}

.risk-box.pass .risk-title { color: var(--up); }
.risk-box.fail .risk-title { color: var(--down); }

.risk-title strong {
  font-size: 12px;
}

.risk-box ul {
  display: grid;
  gap: 6px;
  margin-top: 9px;
  list-style: none;
}

.risk-box li {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  color: var(--text-2);
  font-size: 10.5px;
  line-height: 1.5;
}

.risk-box.pass li svg { color: var(--up); }
.risk-box.fail li svg { color: var(--down); }

.dialog-notice {
  color: var(--text-3);
  font-size: 10.5px;
  line-height: 1.55;
}

.dialog-actions {
  justify-content: flex-end;
  border-top: 1px solid var(--line-1);
}

.stock-toast {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: var(--z-toast);
  max-width: min(460px, calc(100vw - 32px));
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 10px 11px 10px 13px;
  border: 1px solid rgba(52, 211, 153, 0.35);
  border-radius: var(--r-md);
  background: #0c1a19;
  color: var(--text-1);
  box-shadow: 0 6px 8px rgba(0, 0, 0, 0.44);
  font-size: 11.5px;
}

.stock-toast > svg {
  color: var(--up);
  flex: none;
}

.stock-toast > span {
  min-width: 0;
}

.stock-toast > button:not(.toast-close) {
  display: inline-flex;
  align-items: center;
  border: 0;
  background: transparent;
  color: var(--accent-hi);
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
}

@media (max-width: 1220px) {
  .stock-quote-panel {
    grid-template-columns: minmax(210px, 0.75fr) minmax(360px, 1.35fr) 135px;
    gap: 12px;
  }

  .quote-grid > div {
    padding-inline: 9px;
  }

  .stock-layout {
    grid-template-columns: minmax(0, 1fr) 300px;
  }
}

@media (max-width: 1040px) {
  .stock-quote-panel {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .quote-grid {
    grid-column: 1 / -1;
    grid-row: 2;
  }

  .stock-layout {
    grid-template-columns: 1fr;
  }

  .stock-rail {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .evidence-panel {
    grid-column: 1 / -1;
  }
}

@media (max-width: 900px) {
  .score-content,
  .stock-rail {
    grid-template-columns: 1fr;
  }

  .evidence-panel {
    grid-column: auto;
  }
}

@media (max-width: 700px) {
  .stock-page-head {
    align-items: flex-start;
  }

  .stock-page-head > div:first-child {
    display: block;
  }

  .stock-page-head .sub {
    margin-top: 2px;
  }

  .stock-quote-panel {
    grid-template-columns: 1fr auto;
    padding: 14px;
  }

  .quote-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .quote-grid > div:nth-child(odd) {
    border-left: 0;
  }

  .ai-rating {
    align-self: start;
  }

  .horizon-strip {
    grid-template-columns: 1fr;
  }

  .horizon-cell + .horizon-cell {
    border-left: 0;
    border-top: 1px solid var(--line-1);
  }

  .chart-title-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .chart-title-row .tab-pills {
    width: 100%;
  }

  .chart-title-row .tab-pills button {
    flex: 1;
  }

  .chart-meta {
    display: block;
    margin-top: 2px;
  }

  .signal-detail {
    grid-template-columns: 1fr;
  }

  .stock-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .action-copy span {
    white-space: normal;
  }

  .action-buttons {
    margin-left: 0;
  }

  .action-buttons .btn {
    flex: 1;
    min-width: 0;
  }
}

@media (max-width: 460px) {
  .stock-code {
    display: none;
  }

  .stock-quote-panel {
    grid-template-columns: 1fr;
  }

  .ai-rating {
    grid-row: 2;
    justify-items: start;
    grid-template-columns: auto auto 1fr;
    align-items: center;
    gap: 6px;
    padding-top: 10px;
    border-top: 1px solid var(--line-1);
  }

  .ai-rating em {
    grid-column: 1 / -1;
    text-align: left;
  }

  .quote-grid {
    grid-row: 3;
  }

  .price-line strong {
    font-size: 27px;
  }

  .chart-evidence {
    flex-wrap: wrap;
    white-space: normal;
  }

  .chart-evidence span + span {
    padding-left: 0;
    border-left: 0;
  }

  .score-row {
    grid-template-columns: minmax(68px, 1fr) 38px minmax(70px, 1fr);
  }

  .event-list li {
    grid-template-columns: 1fr;
    gap: 3px;
  }

  .trade-summary {
    grid-template-columns: 1fr;
  }

  .trade-summary > div,
  .trade-summary > div:nth-child(odd),
  .trade-summary > div:nth-last-child(-n + 2) {
    border-right: 0;
    border-bottom: 1px solid var(--line-1);
  }

  .trade-summary > div:last-child {
    border-bottom: 0;
  }

  .stock-toast {
    right: 12px;
    bottom: 12px;
    left: 12px;
  }
}
</style>
