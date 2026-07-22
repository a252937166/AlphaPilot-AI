<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, Clock3, RefreshCw, RotateCcw, Sparkles } from 'lucide-vue-next'
import {
  api,
  type CrossMarketDatum,
  type CrossMarketResponse,
  type MarketBreadthFullResponse,
  type MarketIndicesResponse,
  type MarketIntradayResponse,
  type MarketMonitorFeedResponse,
  type MarketSentimentResponse,
} from '../api'
import { areaGradient, CHART_COLORS, categoryAxis, glowLine, tooltipStyle, valueAxis } from '../chartTheme'
import GaugeArc from '../components/GaugeArc.vue'
import EChart from '../components/EChart.vue'
import { fmtAmount, fmtNum, fmtPct, pctClass } from '../format'

const SHANGHAI_INDEX = 'SH.000001'

type ChartMode = 'intraday' | 'daily'
type CrossCardKey = 'fx' | 'future' | 'commodity' | 'northbound'

interface CrossCardView {
  key: CrossCardKey
  label: string
  value: number | null
  changePct: number | null
  asOf: string | null
  source: string | null
  note: string | null
}

const activeChart = ref<ChartMode>('intraday')
const attemptedAt = ref<string | null>(null)

const sentiment = ref<MarketSentimentResponse | null>(null)
const sentimentLoading = ref(true)
const sentimentError = ref('')

const intraday = ref<MarketIntradayResponse | null>(null)
const intradayLoading = ref(true)
const intradayError = ref('')

const indices = ref<MarketIndicesResponse | null>(null)
const indicesLoading = ref(true)
const indicesError = ref('')

const breadth = ref<MarketBreadthFullResponse | null>(null)
const breadthLoading = ref(true)
const breadthError = ref('')

const monitorFeed = ref<MarketMonitorFeedResponse | null>(null)
const feedLoading = ref(true)
const feedError = ref('')

const crossMarket = ref<CrossMarketResponse | null>(null)
const crossLoading = ref(true)
const crossError = ref('')

async function withTimeout<T>(promise: Promise<T>, label: string, timeoutMs = 15_000): Promise<T> {
  let timer: number | undefined
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = window.setTimeout(
      () => reject(new Error(`${label}超过 ${timeoutMs / 1000} 秒未返回，请检查数据源后重试。`)),
      timeoutMs,
    )
  })
  try {
    return await Promise.race([promise, timeout])
  } finally {
    if (timer !== undefined) window.clearTimeout(timer)
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const raw = String(value).trim()
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(raw)
    ? `${raw.replace(' ', 'T')}+08:00`
    : raw
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return raw
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
    .formatToParts(date)
    .reduce<Record<string, string>>((result, part) => {
      result[part.type] = part.value
      return result
    }, {})
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`
}

function sourceLabel(value: string | null | undefined): string {
  if (!value) return '来源未提供'
  const normalized = value.toLowerCase()
  if (normalized.includes('futu')) return 'Futu OpenD'
  if (normalized.includes('safe') || normalized.includes('pboc')) return '国家外汇管理局'
  if (normalized.includes('ccidx')) return '中证商品指数'
  if (normalized.includes('baostock')) return 'BaoStock'
  return value
}

async function loadSentiment() {
  sentimentLoading.value = true
  sentimentError.value = ''
  try {
    sentiment.value = await withTimeout(api.marketSentiment(), '市场情绪')
  } catch (error: unknown) {
    sentimentError.value = errorMessage(error)
  } finally {
    sentimentLoading.value = false
  }
}

async function loadIntraday() {
  intradayLoading.value = true
  intradayError.value = ''
  try {
    intraday.value = await withTimeout(api.marketIntraday(SHANGHAI_INDEX), '上证分时')
  } catch (error: unknown) {
    intradayError.value = errorMessage(error)
  } finally {
    intradayLoading.value = false
  }
}

async function loadIndices() {
  indicesLoading.value = true
  indicesError.value = ''
  try {
    indices.value = await withTimeout(api.marketIndices(60), '指数日K')
  } catch (error: unknown) {
    indicesError.value = errorMessage(error)
  } finally {
    indicesLoading.value = false
  }
}

async function loadBreadth() {
  breadthLoading.value = true
  breadthError.value = ''
  try {
    breadth.value = await withTimeout(api.marketBreadthFull(), '全市场宽度')
  } catch (error: unknown) {
    breadthError.value = errorMessage(error)
  } finally {
    breadthLoading.value = false
  }
}

async function loadFeed() {
  feedLoading.value = true
  feedError.value = ''
  try {
    monitorFeed.value = await withTimeout(api.marketMonitorFeed(20), '实时监测')
  } catch (error: unknown) {
    feedError.value = errorMessage(error)
  } finally {
    feedLoading.value = false
  }
}

async function loadCrossMarket() {
  crossLoading.value = true
  crossError.value = ''
  try {
    crossMarket.value = await withTimeout(api.marketCross(), '跨市场信号')
  } catch (error: unknown) {
    crossError.value = errorMessage(error)
  } finally {
    crossLoading.value = false
  }
}

async function refreshAll() {
  await Promise.all([
    loadSentiment(),
    loadIntraday(),
    loadIndices(),
    loadBreadth(),
    loadFeed(),
    loadCrossMarket(),
  ])
  attemptedAt.value = new Date().toISOString()
}

const refreshing = computed(
  () =>
    sentimentLoading.value ||
    intradayLoading.value ||
    indicesLoading.value ||
    breadthLoading.value ||
    feedLoading.value ||
    crossLoading.value,
)

const sentimentRatio = computed(() => {
  const score = finiteNumber(sentiment.value?.score)
  return score === null ? null : Math.max(0, Math.min(1, score / 100))
})

const sentimentBadgeClass = computed(() => {
  const score = sentiment.value?.score ?? 50
  if (score >= 60) return 'green'
  if (score < 40) return 'red'
  return 'blue'
})

const sentimentSource = computed(() => {
  const source = sentiment.value?.source
  const snapshotSource = source?.snapshot_source
  return typeof snapshotSource === 'string' ? sourceLabel(snapshotSource) : '全市场聚合快照'
})

function sentimentComponentDegraded(component: 'limitup' | 'volume' | 'volatility'): boolean {
  const degraded = sentiment.value?.degraded_components ?? []
  return degraded.includes(component) || degraded.includes('audit_details')
}

const moneyEffectDegraded = computed(() => sentimentComponentDegraded('limitup'))
const liquidityDegraded = computed(() => sentimentComponentDegraded('volume'))
const riskHintDegraded = computed(() => sentimentComponentDegraded('volatility'))

const moneyEffectText = computed(() => {
  const value = sentiment.value?.money_effect?.trim()
  if (!moneyEffectDegraded.value) return value || '赚钱效应暂不可用'
  if (value && (value.includes('基线不足') || value.includes('暂不可用'))) return value
  return '涨停生态历史基线不足，赚钱效应暂不可用'
})

const liquidityText = computed(() => {
  const value = sentiment.value?.liquidity?.trim()
  if (!liquidityDegraded.value) return value || '资金面暂不可用'
  if (value && (value.includes('基线不足') || value.includes('暂不可用'))) return value
  return '量能历史基线不足，资金面暂不可用'
})

const riskHintText = computed(() => {
  const value = sentiment.value?.risk_hint?.trim()
  if (!riskHintDegraded.value) return value || '风险提示暂不可用'
  if (value && (value.includes('基线不足') || value.includes('暂不可用'))) return value
  return '波动率历史基线不足，风险提示暂不可用'
})

const intradayPoints = computed(() =>
  (intraday.value?.[SHANGHAI_INDEX] ?? []).filter((point) =>
    Number.isFinite(Number(point.price)),
  ),
)

const dailyPoints = computed(() =>
  (indices.value?.series?.[SHANGHAI_INDEX] ?? []).filter((point) =>
    [point.open, point.high, point.low, point.close].every((value) =>
      Number.isFinite(Number(value)),
    ),
  ),
)

const shanghaiQuote = computed(
  () => indices.value?.quotes.find((quote) => quote.symbol === SHANGHAI_INDEX) ?? null,
)

const chartDataAsOf = computed(() => {
  if (activeChart.value === 'intraday') {
    return intradayPoints.value.at(-1)?.time ?? shanghaiQuote.value?.as_of ?? null
  }
  return dailyPoints.value.at(-1)?.date ?? null
})

function intradayTooltip(params: unknown): string {
  if (!Array.isArray(params) || params.length === 0) return ''
  const first = params[0] as { dataIndex?: number }
  const index = first.dataIndex
  if (typeof index !== 'number') return ''
  const point = intradayPoints.value[index]
  if (!point) return ''
  return [
    `<strong>${formatDateTime(point.time)}</strong>`,
    `价格　${fmtNum(point.price, 2)}`,
    `均价　${fmtNum(point.avg_price, 2)}`,
    `成交量 ${fmtAmount(point.volume)}`,
  ].join('<br>')
}

const intradayOption = computed<Record<string, unknown>>(() => {
  const points = intradayPoints.value
  const times = points.map((point) => point.time)
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: 'line', lineStyle: { color: CHART_COLORS.line2 } },
      formatter: intradayTooltip,
      ...tooltipStyle,
    },
    grid: { left: 54, right: 18, top: 18, bottom: 34 },
    xAxis: categoryAxis(times, {
      boundaryGap: false,
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 10,
        hideOverlap: true,
        formatter: (value: string) => value.slice(11, 16),
      },
    }),
    yAxis: valueAxis({
      scale: true,
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 10,
        formatter: (value: number) => Number(value).toFixed(1),
      },
    }),
    series: [
      {
        name: '上证价格',
        type: 'line',
        data: points.map((point) => point.price),
        showSymbol: false,
        smooth: 0.12,
        connectNulls: false,
        lineStyle: glowLine(CHART_COLORS.cyan, 2),
        itemStyle: { color: CHART_COLORS.cyan },
        areaStyle: { color: areaGradient(CHART_COLORS.cyan, 0.14) },
        emphasis: { focus: 'series' },
        valueFormatter: (value: number) => fmtNum(value, 2),
      },
      {
        name: '均价',
        type: 'line',
        data: points.map((point) => point.avg_price),
        showSymbol: false,
        smooth: 0.08,
        connectNulls: true,
        lineStyle: { color: CHART_COLORS.warn, width: 1.35, type: 'dashed' },
        itemStyle: { color: CHART_COLORS.warn },
        emphasis: { focus: 'series' },
        valueFormatter: (value: number) => fmtNum(value, 2),
      },
    ],
  }
})

function dailyTooltip(params: unknown): string {
  if (!Array.isArray(params) || params.length === 0) return ''
  const first = params[0] as { dataIndex?: number }
  const index = first.dataIndex
  if (typeof index !== 'number') return ''
  const point = dailyPoints.value[index]
  if (!point) return ''
  return [
    `<strong>${point.date}</strong>`,
    `开盘　${fmtNum(point.open, 2)}`,
    `最高　${fmtNum(point.high, 2)}`,
    `最低　${fmtNum(point.low, 2)}`,
    `收盘　${fmtNum(point.close, 2)}`,
    `成交量 ${fmtAmount(point.volume)}`,
    `成交额 ${fmtAmount(point.amount)}`,
  ].join('<br>')
}

const dailyOption = computed<Record<string, unknown>>(() => {
  const points = dailyPoints.value
  const dates = points.map((point) => point.date)
  const start = points.length > 45 ? Math.max(0, 100 - (45 / points.length) * 100) : 0
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: 'cross', lineStyle: { color: CHART_COLORS.line2 } },
      formatter: dailyTooltip,
      ...tooltipStyle,
    },
    grid: [
      { left: 54, right: 18, top: 18, height: '64%' },
      { left: 54, right: 18, top: '78%', height: '12%' },
    ],
    xAxis: [
      categoryAxis(dates, {
        boundaryGap: true,
        axisLabel: {
          color: CHART_COLORS.text3,
          fontSize: 10,
          hideOverlap: true,
          formatter: (value: string) => value.slice(5),
        },
      }),
      categoryAxis(dates, {
        gridIndex: 1,
        boundaryGap: true,
        axisLabel: { show: false },
        axisLine: { show: false },
      }),
    ],
    yAxis: [
      valueAxis({
        scale: true,
        axisLabel: {
          color: CHART_COLORS.text3,
          fontSize: 10,
          formatter: (value: number) => Number(value).toFixed(0),
        },
      }),
      valueAxis({ gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } }),
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start, end: 100, zoomOnMouseWheel: 'shift' },
    ],
    series: [
      {
        name: '上证日K',
        type: 'candlestick',
        data: points.map((point) => [point.open, point.close, point.low, point.high]),
        itemStyle: {
          color: CHART_COLORS.up,
          color0: CHART_COLORS.down,
          borderColor: CHART_COLORS.up,
          borderColor0: CHART_COLORS.down,
        },
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: points.map((point) => ({
          value: point.volume,
          itemStyle: {
            color: point.close >= point.open
              ? 'rgba(52,211,153,0.46)'
              : 'rgba(248,113,113,0.46)',
          },
        })),
        barMaxWidth: 7,
      },
    ],
  }
})

const marketCount = computed(
  () =>
    (breadth.value?.advancers ?? 0) +
    (breadth.value?.decliners ?? 0) +
    (breadth.value?.unchanged ?? 0),
)

const advancerRatio = computed(() => {
  if (!breadth.value || marketCount.value <= 0) return 0
  return (breadth.value.advancers / marketCount.value) * 100
})

const unchangedRatio = computed(() => {
  if (!breadth.value || marketCount.value <= 0) return 0
  return (breadth.value.unchanged / marketCount.value) * 100
})

const declinerRatio = computed(() => {
  if (!breadth.value || marketCount.value <= 0) return 0
  return (breadth.value.decliners / marketCount.value) * 100
})

const breadthAmountLabel = computed(() => {
  const timestamp = breadth.value?.ts
  if (!timestamp) return '快照成交额'
  const dataDate = formatDateTime(timestamp).slice(0, 10)
  const today = formatDateTime(new Date().toISOString()).slice(0, 10)
  return dataDate === today ? '今日成交额' : `${dataDate} 成交额`
})

const breadthPriorComparable = computed(() => {
  const gap = finiteNumber(breadth.value?.prior_time_gap_seconds)
  return breadth.value?.prior_comparable === true && gap !== null && gap <= 90
})

const amountDeltaText = computed(() => {
  if (!breadthPriorComparable.value) return '基准时刻不可比 · 暂不计算环比'
  const delta = finiteNumber(breadth.value?.amount_delta)
  if (delta === null) return '昨日同刻成交额基线暂缺 · 暂不计算环比'
  const direction = delta >= 0 ? '+' : '−'
  return `较昨日同刻 ${direction}${fmtAmount(Math.abs(delta))}`
})

const breadthBasisText = computed(() => {
  const priorTs = breadth.value?.prior_ts
  const gap = finiteNumber(breadth.value?.prior_time_gap_seconds)
  if (!priorTs) return '昨日样本暂缺 · 暂不计算环比'
  const timestamp = formatDateTime(priorTs)
  if (!breadthPriorComparable.value) {
    const gapText = gap === null ? '时差未知' : `时差 ${fmtNum(gap, 0)} 秒`
    return `${timestamp} · ${gapText} · 基准时刻不可比`
  }
  return `${timestamp} · 时差 ${fmtNum(gap, 0)} 秒`
})

function crossValue(data: CrossMarketDatum | undefined, field: 'value' | 'last' | 'balance') {
  if (!data) return null
  if (field === 'value') return finiteNumber(data.value)
  if (field === 'last') return finiteNumber(data.last)
  return finiteNumber(data.daily_balance)
}

const crossCards = computed<CrossCardView[]>(() => {
  if (!crossMarket.value) return []
  return [
    {
      key: 'fx',
      label: '美元 / 人民币中间价',
      value: crossValue(crossMarket.value.fx_usdcny, 'value'),
      changePct: finiteNumber(crossMarket.value.fx_usdcny.change_pct),
      asOf: crossMarket.value.fx_usdcny.as_of,
      source: crossMarket.value.fx_usdcny.source,
      note: crossMarket.value.fx_usdcny.note ?? null,
    },
    {
      key: 'future',
      label: crossMarket.value.us_futures.name || '标普 500 期指',
      value: crossValue(crossMarket.value.us_futures, 'last'),
      changePct: finiteNumber(crossMarket.value.us_futures.change_pct),
      asOf: crossMarket.value.us_futures.as_of,
      source: crossMarket.value.us_futures.source,
      note: crossMarket.value.us_futures.note ?? null,
    },
    {
      key: 'commodity',
      label: crossMarket.value.commodities.name || '中证商品期货指数',
      value: crossValue(crossMarket.value.commodities, 'last'),
      changePct: finiteNumber(crossMarket.value.commodities.change_pct),
      asOf: crossMarket.value.commodities.as_of,
      source: crossMarket.value.commodities.source,
      note: crossMarket.value.commodities.note ?? null,
    },
    {
      key: 'northbound',
      label: '北向资金',
      value: crossValue(crossMarket.value.northbound, 'balance'),
      changePct: null,
      asOf: crossMarket.value.northbound.as_of,
      source: crossMarket.value.northbound.source,
      note: crossMarket.value.northbound.note ?? null,
    },
  ]
})

function formatCrossValue(card: CrossCardView): string {
  if (card.value === null) return '—'
  if (card.key === 'northbound') return fmtAmount(card.value)
  return fmtNum(card.value, card.key === 'fx' ? 4 : 2)
}

onMounted(refreshAll)
</script>

<template>
  <div class="market-page" :aria-busy="refreshing">
    <div class="page-head market-head">
      <div>
        <div class="head-title-line">
          <h1>大盘监控</h1>
          <span class="data-tag"><i aria-hidden="true" /> A 股数据</span>
        </div>
        <div class="sub">全市场情绪 · 上证分时 · 跨市场信号</div>
      </div>
      <div class="head-actions">
        <span class="refresh-time">
          最近尝试 {{ attemptedAt ? formatDateTime(attemptedAt) : '—' }}
        </span>
        <button class="btn ghost" type="button" :disabled="refreshing" @click="refreshAll">
          <RefreshCw :size="13" :class="{ spin: refreshing }" aria-hidden="true" />
          {{ refreshing ? '刷新中' : '刷新' }}
        </button>
      </div>
    </div>

    <div class="market-layout">
      <main class="primary-stack">
        <section class="panel signal-stage" aria-labelledby="market-pulse-title">
          <div class="stage-head">
            <div>
              <div id="market-pulse-title" class="stage-title">市场脉冲</div>
              <div class="stage-caption">情绪刻度与上证指数共用同一盘中时间轴</div>
            </div>
            <div v-if="sentiment" class="stage-meta">
              {{ sentimentSource }} · {{ formatDateTime(sentiment.as_of) }}
            </div>
          </div>

          <div class="sentiment-strip">
            <div v-if="sentimentLoading && !sentiment" class="sentiment-skeleton">
              <div class="skeleton gauge-skeleton" />
              <div class="skeleton" style="width: 76px; height: 18px" />
            </div>
            <div v-else-if="sentiment" class="sentiment-score">
              <div class="gauge-wrap">
                <GaugeArc
                  :value="sentimentRatio"
                  format="score100"
                  size="148px"
                  :aria-label="`市场情绪 ${sentiment.score.toFixed(1)} 分，${sentiment.label}`"
                />
                <span class="gauge-unit">/ 100</span>
              </div>
              <div class="score-label-row">
                <span class="badge" :class="sentimentBadgeClass">{{ sentiment.label }}</span>
                <span class="score-model mono">{{ sentiment.model_version }}</span>
              </div>
            </div>
            <div v-else class="module-error" role="alert">
              <AlertTriangle :size="16" aria-hidden="true" />
              <div>
                <b>情绪刻度暂不可用</b>
                <span>{{ sentimentError || '尚无市场情绪快照' }}</span>
              </div>
            </div>

            <div v-if="sentiment" class="sentiment-metrics">
              <div class="sentiment-metric" :class="{ 'degraded-metric': moneyEffectDegraded }">
                <span class="metric-label">赚钱效应</span>
                <strong>{{ moneyEffectText }}</strong>
                <span class="metric-detail mono">
                  {{ moneyEffectDegraded ? '涨停生态分位暂不可用' : `涨停生态 ${fmtNum(sentiment.subs.limitup, 1)}` }}
                </span>
              </div>
              <div class="sentiment-metric" :class="{ 'degraded-metric': liquidityDegraded }">
                <span class="metric-label">资金面</span>
                <strong>{{ liquidityText }}</strong>
                <span class="metric-detail mono">
                  {{ liquidityDegraded ? '量能分位暂不可用' : `量能分位 ${fmtNum(sentiment.subs.volume, 1)}` }}
                </span>
              </div>
              <div
                class="sentiment-metric risk-metric"
                :class="{ 'degraded-metric': riskHintDegraded }"
              >
                <span class="metric-label">风险提示</span>
                <strong>{{ riskHintText }}</strong>
                <span class="metric-detail mono">
                  {{ riskHintDegraded ? '波动安全分位暂不可用' : `波动安全分 ${fmtNum(sentiment.subs.volatility, 1)}` }}
                </span>
              </div>
            </div>
          </div>

          <div v-if="sentiment?.degraded" class="degraded-note" role="status">
            <AlertTriangle :size="13" aria-hidden="true" />
            {{ sentiment.degradation_reason || '部分情绪子项历史基线不足，已按规则降级。' }}
          </div>
          <div v-if="sentimentError && sentiment" class="stale-note" role="status">
            情绪刷新失败，当前保留上一份可用快照：{{ sentimentError }}
          </div>

          <div class="chart-block">
            <div class="chart-toolbar">
              <div class="index-identity">
                <div>
                  <span class="index-name">上证指数</span>
                  <span class="index-code mono">SH.000001</span>
                </div>
                <div class="quote-line">
                  <strong class="num">{{ fmtNum(shanghaiQuote?.last, 2) }}</strong>
                  <span class="num" :class="pctClass(shanghaiQuote?.change_pct)">
                    {{ fmtPct(shanghaiQuote?.change_pct) }}
                  </span>
                </div>
              </div>
              <div class="chart-controls">
                <span class="chart-asof">
                  <Clock3 :size="12" aria-hidden="true" />
                  {{ formatDateTime(chartDataAsOf) }}
                </span>
                <div class="tab-pills" role="tablist" aria-label="上证指数图表周期">
                  <button
                    id="market-tab-intraday"
                    type="button"
                    role="tab"
                    :class="{ on: activeChart === 'intraday' }"
                    :aria-selected="activeChart === 'intraday'"
                    aria-controls="market-chart-panel"
                    @click="activeChart = 'intraday'"
                  >
                    分时
                  </button>
                  <button
                    type="button"
                    role="tab"
                    disabled
                    aria-disabled="true"
                    aria-selected="false"
                    title="当前接口尚未提供最近 5 日分时"
                  >
                    5日
                  </button>
                  <button
                    id="market-tab-daily"
                    type="button"
                    role="tab"
                    :class="{ on: activeChart === 'daily' }"
                    :aria-selected="activeChart === 'daily'"
                    aria-controls="market-chart-panel"
                    @click="activeChart = 'daily'"
                  >
                    日K
                  </button>
                </div>
              </div>
            </div>

            <div
              id="market-chart-panel"
              class="chart-panel"
              role="tabpanel"
              :aria-labelledby="activeChart === 'intraday' ? 'market-tab-intraday' : 'market-tab-daily'"
            >
              <div
                v-if="activeChart === 'intraday' && intradayLoading && !intradayPoints.length"
                class="skeleton chart-skeleton"
              />
              <EChart
                v-else-if="activeChart === 'intraday' && intradayPoints.length"
                :option="intradayOption"
                height="322px"
                aria-label="上证指数最近可用交易日分时图，青色为价格，琥珀色虚线为均价"
              />
              <div v-else-if="activeChart === 'intraday'" class="chart-empty" role="status">
                <AlertTriangle :size="17" aria-hidden="true" />
                <b>最近可用交易日分时暂不可用</b>
                <span>{{ intradayError || 'Futu OpenD 暂未返回有效分时点' }}</span>
              </div>

              <div
                v-if="activeChart === 'daily' && indicesLoading && !dailyPoints.length"
                class="skeleton chart-skeleton"
              />
              <EChart
                v-else-if="activeChart === 'daily' && dailyPoints.length"
                :option="dailyOption"
                height="322px"
                aria-label="上证指数近六十个交易日日K与成交量"
              />
              <div v-else-if="activeChart === 'daily'" class="chart-empty" role="status">
                <AlertTriangle :size="17" aria-hidden="true" />
                <b>日K暂不可用</b>
                <span>{{ indicesError || '指数接口未返回完整 OHLC 数据' }}</span>
              </div>
            </div>

            <div class="chart-foot">
              <span v-if="activeChart === 'intraday'">
                <i class="legend-line cyan" aria-hidden="true" />价格
                <i class="legend-line amber" aria-hidden="true" />均价
                · 来源 Futu OpenD RT_DATA
              </span>
              <span v-else>日K使用指数专用真实 OHLC，不复用个股代码。</span>
              <span class="five-day-note">5日暂不可用：接口未提供跨日分时，未拼接假数据。</span>
            </div>
          </div>
        </section>

        <section class="panel breadth-panel" aria-labelledby="market-breadth-title">
          <div class="panel-head compact-head">
            <div>
              <h2 id="market-breadth-title">全市场宽度</h2>
              <span v-if="breadth">
                {{ formatDateTime(breadth.ts) }} · {{ sourceLabel(breadth.source) }}
              </span>
            </div>
            <span v-if="breadth" class="sample-count mono">样本 {{ marketCount.toLocaleString('zh-CN') }}</span>
          </div>

          <div v-if="breadthLoading && !breadth" class="breadth-skeleton">
            <div v-for="n in 3" :key="n" class="skeleton" />
          </div>
          <div v-else-if="breadth" class="breadth-content">
            <div class="breadth-numbers">
              <div class="breadth-side up-side">
                <span>上涨</span>
                <strong class="num up">{{ breadth.advancers.toLocaleString('zh-CN') }}</strong>
              </div>
              <div class="breadth-side down-side">
                <span>下跌</span>
                <strong class="num down">{{ breadth.decliners.toLocaleString('zh-CN') }}</strong>
              </div>
              <div class="turnover-block">
                <span>{{ breadthAmountLabel }}</span>
                <strong class="num">{{ fmtAmount(breadth.total_amount) }}</strong>
                <small class="num" :class="breadthPriorComparable ? pctClass(breadth.amount_delta) : ''">
                  {{ amountDeltaText }}
                  <template v-if="breadthPriorComparable && breadth.amount_delta_pct !== null">
                    · {{ fmtPct(breadth.amount_delta_pct) }}
                  </template>
                </small>
              </div>
            </div>

            <div class="market-split" aria-label="全市场上涨、平盘与下跌家数占比">
              <div class="split-track">
                <i class="advance" :style="{ width: `${advancerRatio}%` }" />
                <i class="unchanged" :style="{ width: `${unchangedRatio}%` }" />
                <i class="decline" :style="{ width: `${declinerRatio}%` }" />
              </div>
              <div class="split-meta">
                <span>上涨 {{ advancerRatio.toFixed(1) }}%</span>
                <span>平盘 {{ unchangedRatio.toFixed(1) }}%</span>
                <span>下跌 {{ declinerRatio.toFixed(1) }}%</span>
              </div>
            </div>

            <div class="breadth-chips" aria-label="涨跌停和炸板统计">
              <span class="market-chip up-chip">涨停 <b class="num">{{ breadth.limit_up }}</b></span>
              <span class="market-chip down-chip">跌停 <b class="num">{{ breadth.limit_down }}</b></span>
              <span class="market-chip warn-chip">炸板 <b class="num">{{ breadth.broken_boards }}</b></span>
              <span class="market-chip">涨超 4% <b class="num">{{ breadth.up_gt4 }}</b></span>
              <span class="market-chip">跌超 4% <b class="num">{{ breadth.down_gt4 }}</b></span>
              <span class="market-chip">平均涨幅 <b class="num" :class="pctClass(breadth.avg_change_pct)">{{ fmtPct(breadth.avg_change_pct) }}</b></span>
            </div>

            <div class="breadth-basis">
              环比基准：{{ breadthBasisText }}
            </div>
          </div>
          <div v-else class="module-error breadth-error" role="alert">
            <AlertTriangle :size="16" aria-hidden="true" />
            <div>
              <b>全市场宽度暂不可用</b>
              <span>{{ breadthError || '尚无全市场快照' }}</span>
            </div>
          </div>
          <div v-if="breadthError && breadth" class="stale-note" role="status">
            宽度刷新失败，当前保留上一份可用快照：{{ breadthError }}
          </div>
        </section>
      </main>

      <aside class="panel monitor-panel" aria-labelledby="monitor-feed-title">
        <div class="panel-head monitor-head">
          <div>
            <h2 id="monitor-feed-title">
              <Sparkles :size="14" aria-hidden="true" /> 实时监测
            </h2>
            <span>阈值事件聚合 · 最新优先</span>
          </div>
          <button
            class="icon-button"
            type="button"
            :disabled="feedLoading"
            aria-label="单独刷新实时监测"
            @click="loadFeed"
          >
            <RotateCcw :size="13" :class="{ spin: feedLoading }" aria-hidden="true" />
          </button>
        </div>

        <div v-if="feedLoading && !monitorFeed" class="feed-skeleton">
          <div v-for="n in 6" :key="n" class="feed-skeleton-row">
            <div class="skeleton" />
            <div class="skeleton" />
          </div>
        </div>
        <div v-else-if="feedError && !monitorFeed" class="module-error feed-error" role="alert">
          <AlertTriangle :size="16" aria-hidden="true" />
          <div>
            <b>实时监测加载失败</b>
            <span>{{ feedError }}</span>
            <button class="retry-link" type="button" @click="loadFeed">重试</button>
          </div>
        </div>
        <ol v-else-if="monitorFeed?.items.length" class="monitor-list" aria-live="polite">
          <li v-for="(item, index) in monitorFeed.items" :key="`${item.ts}-${index}`">
            <i class="feed-dot" :class="item.level" aria-hidden="true" />
            <div>
              <time class="mono" :datetime="item.ts">{{ formatDateTime(item.ts) }}</time>
              <p>{{ item.text }}</p>
            </div>
          </li>
        </ol>
        <div v-else class="feed-empty" role="status">
          <span class="quiet-pulse" aria-hidden="true" />
          <b>当前没有阈值异动</b>
          <span>监测服务只在事实变化达到阈值时生成条目。</span>
        </div>
        <div v-if="feedError && monitorFeed" class="stale-note" role="status">
          刷新失败，保留上一份监测列表：{{ feedError }}
        </div>
        <div class="monitor-foot">
          <span>{{ monitorFeed?.count ?? 0 }} 条</span>
          <span>规则事实，可选 AI 润色</span>
        </div>
      </aside>
    </div>

    <section class="panel cross-panel" aria-labelledby="cross-market-title">
      <div class="panel-head compact-head">
        <div>
          <h2 id="cross-market-title">跨市场信号</h2>
          <span>独立数据源；不可用项不回填替代值</span>
        </div>
      </div>

      <div v-if="crossLoading && !crossMarket" class="cross-grid">
        <div v-for="n in 4" :key="n" class="skeleton cross-skeleton" />
      </div>
      <div v-else-if="crossError && !crossMarket" class="module-error cross-error" role="alert">
        <AlertTriangle :size="16" aria-hidden="true" />
        <div>
          <b>跨市场信号暂不可用</b>
          <span>{{ crossError }}</span>
        </div>
      </div>
      <div v-else class="cross-grid">
        <article v-for="card in crossCards" :key="card.key" class="cross-card">
          <div class="cross-card-head">
            <span>{{ card.label }}</span>
            <span v-if="card.source" class="source-tag">{{ sourceLabel(card.source) }}</span>
          </div>
          <div class="cross-value-row">
            <strong class="num">{{ formatCrossValue(card) }}</strong>
            <span v-if="card.changePct !== null" class="num" :class="pctClass(card.changePct)">
              {{ fmtPct(card.changePct) }}
            </span>
          </div>
          <p v-if="card.value === null" class="cross-note">{{ card.note || '该数据源当前未返回可验证数值。' }}</p>
          <p v-else class="cross-asof">
            数据时间 {{ formatDateTime(card.asOf) }}
          </p>
        </article>
      </div>
      <div v-if="crossError && crossMarket" class="stale-note" role="status">
        跨市场信号刷新失败，当前保留上一份可用快照：{{ crossError }}
      </div>
    </section>
  </div>
</template>

<style scoped>
.market-page {
  min-width: 0;
}

.market-head {
  align-items: center;
  justify-content: space-between;
}

.head-title-line {
  display: flex;
  align-items: center;
  gap: 10px;
}

.data-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-2);
  font-size: 10.5px;
  letter-spacing: 0.04em;
}

.data-tag i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-3);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.refresh-time {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
}

.market-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 318px;
  gap: var(--s3);
  align-items: start;
}

.primary-stack {
  display: grid;
  gap: var(--s3);
  min-width: 0;
}

.signal-stage {
  padding: 0;
  background:
    radial-gradient(640px 260px at 10% 8%, rgba(34, 211, 238, 0.11), transparent 62%),
    linear-gradient(180deg, rgba(13, 21, 40, 0.97), rgba(8, 14, 27, 0.98));
}

.signal-stage::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(90deg, transparent 49.9%, rgba(34, 211, 238, 0.035) 50%, transparent 50.1%);
}

.stage-head,
.panel-head {
  position: relative;
  z-index: 1;
  min-height: 52px;
  padding: 11px 16px;
  border-bottom: 1px solid var(--line-1);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.stage-title,
.panel-head h2 {
  font-size: 12.5px;
  font-weight: 650;
  color: var(--text-1);
  line-height: 1.4;
}

.stage-caption,
.stage-meta,
.panel-head span {
  color: var(--text-3);
  font-size: 10.5px;
}

.stage-meta {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.sentiment-strip {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 208px minmax(0, 1fr);
  align-items: stretch;
  min-height: 174px;
  border-bottom: 1px solid var(--line-1);
}

.sentiment-score,
.sentiment-skeleton {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border-right: 1px solid var(--line-1);
  padding: 8px 16px 14px;
}

.gauge-wrap {
  position: relative;
  width: 170px;
  margin-top: -10px;
}

.gauge-unit {
  position: absolute;
  left: 50%;
  top: 102px;
  transform: translateX(23px);
  color: var(--text-3);
  font: 10px var(--font-mono);
}

.score-label-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: -22px;
}

.score-model {
  color: var(--text-3);
  font-size: 9.5px;
}

.gauge-skeleton {
  width: 124px;
  height: 72px;
  border-radius: 72px 72px 8px 8px;
  margin-bottom: 16px;
}

.sentiment-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  min-width: 0;
}

.sentiment-metric {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 7px;
  min-width: 0;
  padding: 20px 18px;
  border-right: 1px solid var(--line-1);
}

.sentiment-metric:last-child {
  border-right: 0;
}

.metric-label {
  color: var(--text-3);
  font-size: 10.5px;
}

.sentiment-metric strong {
  color: var(--text-1);
  font-size: 14px;
  font-weight: 650;
  line-height: 1.5;
}

.sentiment-metric.degraded-metric strong,
.sentiment-metric.degraded-metric .metric-detail {
  color: var(--text-2);
}

.risk-metric strong {
  color: #fcd34d;
}

.metric-detail {
  color: var(--text-3);
  font-size: 10px;
}

.degraded-note,
.stale-note {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: flex-start;
  gap: 7px;
  padding: 7px 14px;
  border-bottom: 1px solid rgba(251, 191, 36, 0.15);
  background: rgba(251, 191, 36, 0.055);
  color: #d7b968;
  font-size: 10.5px;
  line-height: 1.55;
}

.stale-note {
  border-color: rgba(248, 113, 113, 0.12);
  background: rgba(248, 113, 113, 0.045);
  color: #d5a2a2;
}

.chart-block {
  position: relative;
  z-index: 1;
}

.chart-toolbar {
  min-height: 74px;
  padding: 12px 16px 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.index-identity {
  min-width: 0;
}

.index-name {
  font-size: 13px;
  font-weight: 650;
}

.index-code {
  margin-left: 8px;
  color: var(--text-3);
  font-size: 10px;
}

.quote-line {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-top: 3px;
}

.quote-line strong {
  color: var(--text-1);
  font-size: 22px;
  line-height: 1.2;
  letter-spacing: -0.035em;
}

.quote-line span {
  font-size: 11.5px;
}

.chart-controls {
  display: flex;
  align-items: center;
  gap: 12px;
}

.chart-asof {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--text-3);
  font: 10px var(--font-mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.tab-pills button:disabled {
  cursor: not-allowed;
  opacity: 0.34;
}

.chart-panel {
  min-height: 322px;
}

.chart-skeleton {
  height: 300px;
  margin: 10px 16px 12px;
}

.chart-empty {
  height: 322px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--text-3);
  text-align: center;
  padding: 28px;
}

.chart-empty b {
  color: var(--text-2);
  font-size: 12.5px;
}

.chart-empty span {
  max-width: 500px;
  font-size: 11px;
  line-height: 1.65;
}

.chart-foot {
  min-height: 35px;
  border-top: 1px solid var(--line-1);
  padding: 8px 16px;
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: var(--text-3);
  font-size: 10px;
}

.legend-line {
  display: inline-block;
  width: 14px;
  height: 2px;
  margin: 0 5px 3px 8px;
  vertical-align: middle;
}

.legend-line:first-child {
  margin-left: 0;
}

.legend-line.cyan {
  background: var(--cyan);
  box-shadow: 0 0 6px rgba(34, 211, 238, 0.55);
}

.legend-line.amber {
  height: 0;
  border-top: 1px dashed var(--warn);
}

.five-day-note {
  color: #b8a16b;
  text-align: right;
}

.panel-head h2 {
  display: flex;
  align-items: center;
  gap: 6px;
}

.compact-head {
  margin: -16px -16px 0;
}

.sample-count {
  color: var(--text-2) !important;
}

.breadth-content {
  padding-top: 14px;
}

.breadth-numbers {
  display: grid;
  grid-template-columns: minmax(120px, 0.72fr) minmax(120px, 0.72fr) minmax(220px, 1.45fr);
  border: 1px solid var(--line-1);
  border-radius: 9px;
  overflow: hidden;
  background: rgba(4, 8, 17, 0.24);
}

.breadth-side,
.turnover-block {
  padding: 12px 14px;
  border-right: 1px solid var(--line-1);
}

.turnover-block {
  border-right: 0;
}

.breadth-side > span,
.turnover-block > span {
  display: block;
  color: var(--text-3);
  font-size: 10.5px;
}

.breadth-side strong,
.turnover-block strong {
  display: block;
  margin-top: 3px;
  font-size: 25px;
  line-height: 1.2;
  letter-spacing: -0.035em;
}

.turnover-block small {
  display: block;
  margin-top: 5px;
  font-size: 10px;
}

.market-split {
  margin-top: 13px;
}

.split-track {
  height: 5px;
  display: flex;
  overflow: hidden;
  border-radius: 8px;
  background: rgba(148, 163, 198, 0.08);
}

.split-track i {
  display: block;
  transition: width var(--t-med);
}

.split-track .advance {
  background: linear-gradient(90deg, rgba(52, 211, 153, 0.46), var(--up));
}

.split-track .unchanged {
  background: rgba(148, 163, 198, 0.36);
}

.split-track .decline {
  background: linear-gradient(90deg, var(--down), rgba(248, 113, 113, 0.42));
}

.split-meta {
  display: flex;
  justify-content: space-between;
  margin-top: 5px;
  color: var(--text-3);
  font: 9.5px var(--font-mono);
}

.breadth-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 13px;
}

.market-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border: 1px solid var(--line-1);
  border-radius: 6px;
  color: var(--text-3);
  background: rgba(148, 163, 198, 0.035);
  font-size: 10.5px;
}

.market-chip b {
  color: var(--text-2);
}

.up-chip {
  border-color: rgba(52, 211, 153, 0.2);
  color: #74c9a6;
}

.up-chip b {
  color: var(--up);
}

.down-chip {
  border-color: rgba(248, 113, 113, 0.2);
  color: #d99999;
}

.down-chip b {
  color: var(--down);
}

.warn-chip {
  border-color: rgba(251, 191, 36, 0.2);
  color: #cfb56a;
}

.warn-chip b {
  color: var(--warn);
}

.breadth-basis {
  margin-top: 11px;
  color: var(--text-3);
  font: 9.5px var(--font-mono);
}

.breadth-skeleton {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  padding-top: 14px;
}

.breadth-skeleton .skeleton {
  height: 86px;
}

.monitor-panel {
  padding: 0;
  min-width: 0;
}

.monitor-head {
  margin: 0;
}

.icon-button {
  width: 28px;
  height: 28px;
  border: 1px solid var(--line-1);
  border-radius: 6px;
  background: transparent;
  color: var(--text-3);
  display: grid;
  place-items: center;
  cursor: pointer;
}

.icon-button:hover:not(:disabled) {
  border-color: var(--line-focus);
  color: var(--text-1);
}

.icon-button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.monitor-list {
  list-style: none;
  max-height: 660px;
  overflow-y: auto;
  padding: 2px 14px;
}

.monitor-list li {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr);
  gap: 9px;
  padding: 11px 0;
  border-bottom: 1px solid var(--line-1);
}

.monitor-list li:last-child {
  border-bottom: 0;
}

.feed-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-top: 5px;
  background: var(--cyan);
  box-shadow: 0 0 8px rgba(34, 211, 238, 0.44);
}

.feed-dot.warn {
  background: var(--warn);
  box-shadow: 0 0 8px rgba(251, 191, 36, 0.46);
}

.monitor-list time {
  display: block;
  color: var(--text-3);
  font-size: 9.5px;
  margin-bottom: 3px;
}

.monitor-list p {
  color: var(--text-2);
  font-size: 11px;
  line-height: 1.65;
}

.monitor-foot {
  min-height: 32px;
  border-top: 1px solid var(--line-1);
  padding: 7px 14px;
  display: flex;
  justify-content: space-between;
  color: var(--text-3);
  font-size: 9.5px;
}

.feed-skeleton {
  padding: 6px 14px;
}

.feed-skeleton-row {
  display: grid;
  gap: 7px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line-1);
}

.feed-skeleton-row .skeleton:first-child {
  width: 118px;
  height: 9px;
}

.feed-skeleton-row .skeleton:last-child {
  height: 27px;
}

.feed-empty {
  min-height: 260px;
  padding: 28px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  text-align: center;
  color: var(--text-3);
}

.feed-empty b {
  color: var(--text-2);
  font-size: 12px;
}

.feed-empty span:last-child {
  font-size: 10.5px;
  line-height: 1.6;
}

.quiet-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--up);
  box-shadow: 0 0 0 5px rgba(52, 211, 153, 0.08);
  margin-bottom: 8px;
}

.module-error {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  gap: 9px;
  color: var(--down);
  padding: 22px;
}

.module-error div {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.module-error b {
  color: #f0b1b1;
  font-size: 11.5px;
}

.module-error span {
  color: var(--text-3);
  font-size: 10.5px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.feed-error {
  min-height: 260px;
}

.breadth-error {
  min-height: 140px;
}

.retry-link {
  align-self: flex-start;
  border: 0;
  background: transparent;
  color: var(--accent-hi);
  padding: 3px 0;
  font-size: 11px;
  cursor: pointer;
}

.cross-panel {
  margin-top: var(--s3);
}

.cross-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  padding-top: 14px;
}

.cross-card {
  min-width: 0;
  min-height: 126px;
  border: 1px solid var(--line-1);
  border-radius: 9px;
  padding: 12px;
  background: rgba(4, 8, 17, 0.24);
}

.cross-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 7px;
  color: var(--text-2);
  font-size: 10.5px;
}

.source-tag {
  color: var(--text-3);
  font: 8.5px var(--font-mono);
  white-space: nowrap;
}

.cross-value-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-top: 9px;
}

.cross-value-row strong {
  color: var(--text-1);
  font-size: 19px;
  letter-spacing: -0.025em;
}

.cross-value-row span {
  font-size: 10.5px;
}

.cross-note,
.cross-asof {
  margin-top: 8px;
  color: var(--text-3);
  font-size: 9.5px;
  line-height: 1.55;
}

.cross-note {
  overflow-wrap: anywhere;
}

.cross-skeleton {
  height: 126px;
}

.cross-error {
  min-height: 100px;
}

.spin {
  animation: rotate 0.9s linear infinite;
}

@keyframes rotate {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1280px) {
  .market-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .monitor-list {
    max-height: 390px;
  }

  .cross-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .sentiment-strip {
    grid-template-columns: minmax(0, 1fr);
  }

  .sentiment-score,
  .sentiment-skeleton {
    min-height: 148px;
    border-right: 0;
    border-bottom: 1px solid var(--line-1);
  }

  .sentiment-metric {
    padding-inline: 12px;
  }

  .sentiment-metric strong {
    font-size: 12.5px;
  }

  .breadth-numbers {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .turnover-block {
    grid-column: 1 / -1;
    border-top: 1px solid var(--line-1);
  }
}

@media (max-width: 620px) {
  .market-head {
    align-items: flex-start;
    gap: 10px;
  }

  .refresh-time,
  .data-tag {
    display: none;
  }

  .sentiment-strip {
    grid-template-columns: minmax(0, 1fr);
  }

  .sentiment-score,
  .sentiment-skeleton {
    min-height: 148px;
    border-right: 0;
    border-bottom: 1px solid var(--line-1);
  }

  .sentiment-metrics {
    grid-template-columns: minmax(0, 1fr);
  }

  .sentiment-metric {
    min-height: 82px;
    border-right: 0;
    border-bottom: 1px solid var(--line-1);
  }

  .sentiment-metric:last-child {
    border-bottom: 0;
  }

  .chart-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .chart-controls {
    width: 100%;
    justify-content: space-between;
  }

  .chart-foot {
    flex-direction: column;
  }

  .five-day-note {
    text-align: left;
  }

  .cross-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 440px) {
  .stage-head {
    align-items: flex-start;
    flex-direction: column;
  }

  .stage-meta {
    text-align: left;
  }

  .chart-controls {
    align-items: flex-start;
    flex-direction: column-reverse;
  }

  .breadth-numbers {
    grid-template-columns: minmax(0, 1fr);
  }

  .breadth-side,
  .turnover-block {
    border-right: 0;
    border-bottom: 1px solid var(--line-1);
  }

  .turnover-block {
    grid-column: auto;
    border-bottom: 0;
  }

  .split-meta {
    font-size: 8.5px;
  }

  .compact-head {
    align-items: flex-start;
    flex-direction: column;
  }
}

@media (prefers-reduced-motion: reduce) {
  .spin {
    animation: none;
  }
}
</style>
