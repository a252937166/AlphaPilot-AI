<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Activity,
  AlertTriangle,
  ChevronRight,
  Grid3x3,
  RefreshCw,
  Sparkles,
  Star,
  TrendingDown,
  TrendingUp,
} from 'lucide-vue-next'
import {
  api,
  type AlertItem,
  type JobRunItem,
  type LatestScreenResponse,
  type MarketBreadthFullResponse,
  type ScreenDiffResponse,
  type SectorStrengthItem,
  type SectorStrengthResponse,
  type StyleDailyPoint,
} from '../api'
import {
  actionMeta,
  fmtAmount,
  fmtNum,
  fmtPct,
  fmtTime,
  heatColor,
  pctClass,
  regimeMeta,
} from '../format'
import ConfRing from '../components/ConfRing.vue'
import EChart from '../components/EChart.vue'
import GaugeArc from '../components/GaugeArc.vue'
import StackedAreaChart from '../components/StackedAreaChart.vue'
import {
  CHART_COLORS,
  areaGradient,
  categoryAxis,
  glowLine,
  tooltipStyle,
  valueAxis,
} from '../chartTheme'

const router = useRouter()
const loading = ref(true)
const error = ref('')
const partialErrors = ref<Record<string, string>>({})
const data = ref<any>(null)
const allAlerts = ref<AlertItem[]>([])
const sectorRows = ref<SectorStrengthItem[]>([])
const sectorSnapshot = ref<SectorStrengthResponse | null>(null)
const styleHistory = ref<StyleDailyPoint[]>([])
const styleRequestedDays = ref(60)
const latestScreen = ref<LatestScreenResponse | null>(null)
const screenDiff = ref<ScreenDiffResponse | null>(null)
const marketBreadth = ref<MarketBreadthFullResponse | null>(null)
const jobRuns = ref<JobRunItem[]>([])
const indexSeries = ref<Record<string, { date: string; close: number }[]>>({})
const indexNames = ref<Record<string, string>>({})
const activeIndex = ref('SH.000001')
const chartMode = ref<'single' | 'compare' | 'style'>('single')
const heatMode = ref<'strength' | 'change' | 'flow'>('strength')
let loadRequestVersion = 0

const INDEX_COLORS: Record<string, string> = {
  'SH.000001': CHART_COLORS.cyan,
  'SZ.399001': CHART_COLORS.accent,
  'SZ.399006': CHART_COLORS.purple,
  'SH.000300': CHART_COLORS.warn,
  'SH.000905': CHART_COLORS.up,
}

const regime = computed(() => data.value?.regime)
const indices = computed(() => data.value?.indices ?? [])
const sectors = computed(() => sectorRows.value)
const watchlist = computed(() => data.value?.watchlist ?? [])
const feedAlerts = computed(() => data.value?.alerts ?? [])
const compareMode = computed(() => chartMode.value === 'compare')

/* --- today's deduped signal stats (one per symbol, latest wins) --- */
const todayLatest = computed(() => {
  const today = new Date().toDateString()
  const bySymbol: Record<string, AlertItem> = {}
  for (const alert of allAlerts.value) {
    if (new Date(alert.created_at).toDateString() !== today) continue
    if (!bySymbol[alert.symbol] || alert.created_at > bySymbol[alert.symbol].created_at) {
      bySymbol[alert.symbol] = alert
    }
  }
  return Object.values(bySymbol)
})
const highConfidence = computed(() =>
  todayLatest.value.filter((alert) => Number(alert.confidence) >= 0.6),
)
const alertsMayBeTruncated = computed(() => allAlerts.value.length >= 200)
const riskSignals = computed(() =>
  todayLatest.value.filter((alert) => ['REDUCE', 'EXIT', 'STOP'].includes(alert.action)),
)

const opportunitySnapshotValid = computed(() => {
  const candidates = latestScreen.value?.candidates
  const diff = screenDiff.value
  return Boolean(
    Array.isArray(candidates) &&
      diff &&
      Number(latestScreen.value?.id) === diff.current_run_id &&
      diff.new.length + diff.stayed === candidates.length,
  )
})
const opportunities = computed(() =>
  opportunitySnapshotValid.value
    ? latestScreen.value?.candidates.filter((item) => Number(item.score) >= 70) || []
    : [],
)

function advanceRatio(advancers: unknown, decliners: unknown): number | null {
  const up = Number(advancers)
  const down = Number(decliners)
  if (!Number.isFinite(up) || !Number.isFinite(down) || up + down <= 0) return null
  return (up / (up + down)) * 100
}

const breadthRatio = computed(() =>
  advanceRatio(marketBreadth.value?.advancers, marketBreadth.value?.decliners),
)
const priorBreadthRatio = computed(() =>
  advanceRatio(marketBreadth.value?.prior_advancers, marketBreadth.value?.prior_decliners),
)
const breadthDeltaPp = computed(() => {
  if (breadthRatio.value === null || priorBreadthRatio.value === null) return null
  return breadthRatio.value - priorBreadthRatio.value
})

const activeQuote = computed(() =>
  indices.value.find((item: any) => item.symbol === activeIndex.value),
)

const styleChartSeries = computed(() => [
  {
    name: '成长',
    color: CHART_COLORS.cyan,
    values: styleHistory.value.map((point) => point.growth_pct),
  },
  {
    name: '价值',
    color: CHART_COLORS.up,
    values: styleHistory.value.map((point) => point.value_pct),
  },
  {
    name: '防御',
    color: CHART_COLORS.purple,
    values: styleHistory.value.map((point) => point.defensive_pct),
  },
  {
    name: '均衡',
    color: CHART_COLORS.slate,
    values: styleHistory.value.map((point) => point.balanced_pct),
  },
])

const maxAbsFlow = computed(() => {
  const values = sectors.value
    .map((sector) => sector.net_inflow)
    .filter((value: unknown) => value !== null && Number.isFinite(Number(value)))
    .map(Number)
  return Math.max(1, ...values.map((value: number) => Math.abs(value)))
})

const flowAsOf = computed(() => {
  const dates = Array.from(
    new Set(
      sectors.value
        .map((sector) => sector.flow_trade_date)
        .filter((value: unknown): value is string => typeof value === 'string' && value.length > 0),
    ),
  )
  return dates.length === 1 ? dates[0] : null
})

const sectorSnapshotLabel = computed(() => {
  if (partialErrors.value.sectors) return '板块数据暂不可用'
  if (!sectorSnapshot.value) return '板块快照加载中'
  const asOf = fmtTime(sectorSnapshot.value.as_of)
  return sectorSnapshot.value.stale
    ? `缓存回退 · ${asOf}`
    : `Futu 行情快照 · ${asOf}`
})

function heatInput(sector: SectorStrengthItem): number | null {
  if (heatMode.value === 'strength') return (Number(sector.strength) - 5) * 0.6
  if (heatMode.value === 'change') return Number(sector.avg_change_pct)
  if (sector.net_inflow === null || sector.net_inflow === undefined) return null
  const flow = Number(sector.net_inflow)
  return Number.isFinite(flow) ? (flow / maxAbsFlow.value) * 3 : null
}

function heatBackground(sector: SectorStrengthItem): string {
  const value = heatInput(sector)
  return value === null || !Number.isFinite(value) ? '#273350' : heatColor(value)
}

function heatPrimary(sector: SectorStrengthItem): string {
  if (heatMode.value === 'strength') return `${fmtNum(sector.strength, 1)} / 10`
  if (heatMode.value === 'change') return fmtPct(sector.avg_change_pct)
  return fmtAmount(sector.net_inflow)
}

function heatSecondary(sector: SectorStrengthItem): string {
  if (heatMode.value === 'strength') {
    const upRatio = Number(sector.up_ratio)
    const upText = Number.isFinite(upRatio) ? `${(upRatio * 100).toFixed(0)}%` : '—'
    return `排名 ${sector.rank ?? '—'} · 上涨 ${upText}`
  }
  if (heatMode.value === 'change') return `强度 ${fmtNum(sector.strength, 1)} · 样本 ${sector.sampled ?? '—'}`
  return sector.flow_trade_date
    ? `${sector.flow_trade_date} · ${sector.flow_source || '来源未知'}`
    : '该资金流截面无数据'
}

function heatTitle(sector: SectorStrengthItem): string {
  return `${sector.plate_name}｜强度 ${fmtNum(sector.strength, 2)}｜涨跌 ${fmtPct(sector.avg_change_pct)}｜资金流 ${fmtAmount(sector.net_inflow)}`
}

/* --- index chart: single (area+glow) or all-compare (normalized) --- */
const indexChartOption = computed(() => {
  const symbols = Object.keys(indexSeries.value)
  if (!symbols.length) return {}
  if (!compareMode.value) {
    const points = indexSeries.value[activeIndex.value] || []
    if (!points.length) return {}
    const closes = points.map((point) => Number(point.close))
    const last = closes[closes.length - 1]
    const color = last >= closes[0] ? CHART_COLORS.cyan : CHART_COLORS.down
    return {
      animation: false,
      tooltip: {
        trigger: 'axis',
        ...tooltipStyle,
        valueFormatter: (value: number) => Number(value).toFixed(2),
      },
      grid: { left: 52, right: 14, top: 16, bottom: 22 },
      xAxis: categoryAxis(points.map((point) => point.date.slice(5))),
      yAxis: valueAxis({ scale: true }),
      series: [
        {
          name: indexNames.value[activeIndex.value] || activeIndex.value,
          type: 'line',
          data: closes,
          symbol: 'none',
          smooth: 0.15,
          lineStyle: glowLine(color),
          itemStyle: { color },
          areaStyle: { color: areaGradient(color, 0.3) },
          markLine: {
            symbol: 'none',
            label: {
              show: true,
              position: 'insideEndTop',
              color,
              fontSize: 10,
              fontFamily: "ui-monospace,'SF Mono',Menlo,monospace",
              formatter: (params: any) => Number(params.value).toFixed(2),
            },
            lineStyle: { color, type: 'dashed', opacity: 0.5, width: 1 },
            data: [{ yAxis: last }],
          },
        },
      ],
    }
  }
  // compare mode: normalized % lines with glow
  const base = indexSeries.value[symbols[0]] || []
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle,
      valueFormatter: (value: number) => `${Number(value).toFixed(2)}%`,
    },
    legend: { textStyle: { color: CHART_COLORS.text3, fontSize: 10 }, top: 0, itemWidth: 14, itemHeight: 2 },
    grid: { left: 42, right: 14, top: 26, bottom: 22 },
    xAxis: categoryAxis(base.map((point: any) => point.date.slice(5))),
    yAxis: valueAxis({
      axisLabel: { formatter: '{value}%', color: CHART_COLORS.text3, fontSize: 10 },
    }),
    series: symbols.map((symbol) => {
      const points = indexSeries.value[symbol] || []
      const first = Number(points[0]?.close || 1)
      return {
        name: indexNames.value[symbol] || symbol,
        type: 'line',
        symbol: 'none',
        smooth: 0.15,
        lineStyle: glowLine(INDEX_COLORS[symbol], 1.4),
        itemStyle: { color: INDEX_COLORS[symbol] },
        emphasis: { focus: 'series' },
        data: points.map((point: any) => ((Number(point.close) / first - 1) * 100).toFixed(3)),
      }
    }),
  }
})

const JOB_LABELS: Record<string, string> = {
  sync_daily_bars: '同步日线',
  compute_factors: '计算因子',
  compute_style_daily: '更新风格概率',
  sync_sector_flows: '同步板块资金流',
  sector_forecast: '更新板块预测',
  poll_market_snapshot: '采集全市场快照',
  evaluate_alerts: '评估提醒结果',
  snapshot_portfolio: '生成组合快照',
  sync_orders: '同步模拟订单',
}

/* --- recent activity timeline from persisted job runs + signal batches --- */
const activities = computed(() => {
  const events: { id: string; time: string; text: string; cls: string }[] = []
  const seenJobs = new Set<string>()
  for (const run of jobRuns.value) {
    if (seenJobs.has(run.job_name)) continue
    seenJobs.add(run.job_name)
    const status = run.status === 'ok'
      ? '完成'
      : run.status === 'degraded'
        ? '降级完成'
        : run.status === 'failed'
          ? '失败'
          : '运行中'
    events.push({
      id: `job:${run.id}`,
      time: run.started_at,
      text: `${JOB_LABELS[run.job_name] || run.job_name} · ${status} · #${run.id}`,
      cls: run.status === 'failed'
        ? 'red'
        : run.status === 'running' || run.status === 'degraded'
          ? 'yellow'
          : 'green',
    })
    if (seenJobs.size >= 5) break
  }

  const batches: Record<string, { count: number; time: string }> = {}
  for (const alert of allAlerts.value.slice(0, 40)) {
    const key = String(alert.created_at).slice(0, 16)
    const current = batches[key]
    batches[key] = {
      count: (current?.count || 0) + 1,
      time: current?.time || String(alert.created_at),
    }
  }
  for (const [minute, batch] of Object.entries(batches).slice(0, 2)) {
    events.push({
      id: `alerts:${minute}`,
      time: batch.time,
      text: `重算自选信号 · ${batch.count} 条`,
      cls: 'blue',
    })
  }
  return events
    .sort((a, b) => (a.time < b.time ? 1 : -1))
    .slice(0, 7)
    .map((event) => ({ ...event, display: fmtTime(event.time) }))
})

async function load() {
  const requestVersion = ++loadRequestVersion
  loading.value = true
  error.value = ''
  const loadErrors: Record<string, string> = {}
  async function optional<T>(key: string, promise: Promise<T>): Promise<T | null> {
    try {
      return await promise
    } catch (exc: any) {
      loadErrors[key] = String(exc?.message || exc)
      return null
    }
  }
  try {
    const [
      overview,
      indicesResult,
      alertsResult,
      styleResult,
      sectorsResult,
      diffResult,
      latestResult,
      breadthResult,
      jobsResult,
    ] = await Promise.all([
      api.dashboard(),
      optional('indices', api.marketIndices(60)),
      optional('alerts', api.alerts(200)),
      optional('style', api.styleDaily(60)),
      optional('sectors', api.sectors(false)),
      optional('screenDiff', api.screenDiff()),
      optional('latestScreen', api.latestScreen()),
      optional('breadth', api.marketBreadthFull()),
      optional('jobs', api.jobRuns(30)),
    ])
    if (requestVersion !== loadRequestVersion) return
    data.value = overview
    partialErrors.value = loadErrors
    allAlerts.value = alertsResult?.alerts || []
    styleHistory.value = styleResult?.series || []
    styleRequestedDays.value = styleResult?.requested_days || 60
    sectorSnapshot.value = sectorsResult
    sectorRows.value = sectorsResult?.sectors || []
    screenDiff.value = diffResult
    latestScreen.value = latestResult
    marketBreadth.value = breadthResult
    jobRuns.value = jobsResult?.runs || []
    indexSeries.value = indicesResult?.series || {}
    indexNames.value = {}
    for (const entry of indicesResult?.symbols || []) {
      indexNames.value[entry.symbol] = entry.name
    }
  } catch (exc: any) {
    if (requestVersion === loadRequestVersion) error.value = String(exc.message || exc)
  } finally {
    if (requestVersion === loadRequestVersion) loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>市场总览</h1>
      <span class="sub">AI 综合解读 · 数据驱动 · 不构成投资建议</span>
      <div class="page-head-actions">
        <button class="btn ghost refresh-button" @click="load" :disabled="loading">
          <RefreshCw :size="13" :class="{ spin: loading }" />
          {{ loading ? '刷新中' : '刷新' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="banner error" style="margin-bottom: 12px">加载失败：{{ error }}</div>

    <!-- skeleton -->
    <div v-if="loading && !data" class="overview-layout">
      <div class="grid overview-main">
        <div class="overview-stats">
          <div v-for="n in 4" :key="n" class="skeleton" style="height: 96px" />
        </div>
        <div class="skeleton" style="height: 290px" />
        <div class="skeleton" style="height: 150px" />
        <div class="skeleton" style="height: 240px" />
      </div>
      <div class="grid overview-side">
        <div class="skeleton" style="height: 300px" />
        <div class="skeleton" style="height: 220px" />
      </div>
    </div>

    <div v-if="data" class="overview-layout">
      <div class="grid overview-main">
        <!-- 统计卡行 -->
        <div class="overview-stats">
          <div class="stat-card">
            <div class="stat-main">
              <div class="label">市场状态</div>
              <div style="margin: 6px 0 3px">
                <span class="badge" :class="regimeMeta(regime?.regime).cls" style="font-size: 13px; padding: 3px 10px">
                  {{ regimeMeta(regime?.regime).label }}
                </span>
              </div>
              <div class="delta">状态机 · {{ regime?.source || '—' }}</div>
            </div>
            <div class="stat-viz" style="width: 84px">
              <GaugeArc :value="regime?.confidence" size="76px" />
            </div>
          </div>

          <div class="stat-card">
            <div class="stat-main">
              <div class="label">今日机会数</div>
              <div class="value glow-cyan">{{ opportunitySnapshotValid ? opportunities.length : '—' }}</div>
              <div class="delta">
                <template v-if="opportunitySnapshotValid">
                  新入 {{ screenDiff?.new.length || 0 }} · 留存 {{ screenDiff?.stayed || 0 }} · 评分 ≥ 70
                </template>
                <template v-else-if="partialErrors.screenDiff || partialErrors.latestScreen">
                  选股统计暂不可用
                </template>
                <template v-else>选股批次不一致，刷新后重试</template>
              </div>
            </div>
          </div>

          <div class="stat-card">
            <div class="stat-main">
              <div class="label">高置信信号</div>
              <div class="value glow-green">{{ partialErrors.alerts ? '—' : highConfidence.length }}</div>
              <div class="delta">
                {{ partialErrors.alerts
                  ? '提醒数据暂不可用'
                  : alertsMayBeTruncated
                    ? '最近 200 条口径，结果可能截断'
                    : '置信度 ≥ 60% · 今日按标的去重' }}
              </div>
            </div>
          </div>

          <div class="stat-card">
            <div class="stat-main">
              <div class="label">
                全市场宽度
                <span
                  v-if="breadthDeltaPp !== null"
                  class="breadth-delta num"
                  :class="breadthDeltaPp >= 0 ? 'up' : 'down'"
                >
                  <TrendingUp v-if="breadthDeltaPp >= 0" :size="11" />
                  <TrendingDown v-else :size="11" />
                  较昨日 {{ breadthDeltaPp > 0 ? '+' : '' }}{{ breadthDeltaPp.toFixed(1) }}pp
                </span>
              </div>
              <div
                class="value"
                :class="breadthRatio === null ? '' : breadthRatio >= 50 ? 'glow-green' : 'glow-red'"
              >
                {{ breadthRatio === null ? '—' : `${breadthRatio.toFixed(1)}%` }}
              </div>
              <div class="delta">
                <template v-if="marketBreadth">
                  上涨 {{ marketBreadth.advancers }} / 下跌 {{ marketBreadth.decliners }}
                </template>
                <template v-else>全市场快照暂不可用</template>
              </div>
              <div v-if="marketBreadth" class="breadth-chips num">
                <span>涨停 {{ marketBreadth.limit_up }}</span>
                <span>跌停 {{ marketBreadth.limit_down }}</span>
                <span>炸板 {{ marketBreadth.broken_boards }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 指数走势 -->
        <div class="panel">
          <div class="panel-title">
            <span style="display: inline-flex; align-items: baseline; gap: 10px">
              指数走势
              <template v-if="chartMode === 'single' && activeQuote">
                <span class="num" style="font-size: 15px; font-weight: 650">{{ fmtNum(activeQuote.last) }}</span>
                <span class="num xs" :class="pctClass(activeQuote.change_pct)">{{ fmtPct(activeQuote.change_pct) }}</span>
              </template>
            </span>
            <span class="overview-chart-controls">
              <span class="tab-pills" v-if="chartMode === 'single'">
                <button
                  v-for="(name, symbol) in indexNames"
                  :key="symbol"
                  :class="{ on: activeIndex === symbol }"
                  :aria-pressed="activeIndex === symbol"
                  @click="activeIndex = String(symbol)"
                >
                  {{ name }}
                </button>
              </span>
              <span class="tab-pills">
                <button :class="{ on: chartMode === 'single' }" :aria-pressed="chartMode === 'single'" @click="chartMode = 'single'">单指数</button>
                <button :class="{ on: chartMode === 'compare' }" :aria-pressed="chartMode === 'compare'" @click="chartMode = 'compare'">全部对比</button>
                <button :class="{ on: chartMode === 'style' }" :aria-pressed="chartMode === 'style'" @click="chartMode = 'style'">风格概率</button>
              </span>
            </span>
          </div>
          <template v-if="chartMode === 'style'">
            <StackedAreaChart
              v-if="styleHistory.length"
              :dates="styleHistory.map((point) => point.trade_date)"
              :series="styleChartSeries"
              height="250px"
            />
            <div v-if="styleHistory.length" class="chart-footnote">
              已有 {{ styleHistory.length }} / {{ styleRequestedDays }} 个交易日
              <template v-if="styleHistory.length < 2"> · 历史积累中，当前以单点显示</template>
            </div>
            <div v-else class="empty-hint">
              {{ partialErrors.style ? `风格概率暂不可用：${partialErrors.style}` : '暂无风格概率数据' }}
            </div>
          </template>
          <EChart
            v-else-if="Object.keys(indexSeries).length"
            :option="indexChartOption"
            height="250px"
          />
          <div v-else class="empty-hint">
            {{ partialErrors.indices ? `指数历史暂不可用：${partialErrors.indices}` : '指数历史加载中…' }}
          </div>
        </div>

        <!-- 板块热力 -->
        <div class="panel">
          <div class="panel-title overview-panel-title">
            <span>
              板块全景热力图
              <small class="panel-count num">{{ sectors.length }} 个行业</small>
            </span>
            <span class="heat-controls">
              <span
                class="extra"
                :title="sectorSnapshot?.stale ? sectorSnapshot.error || '板块行情源暂不可用' : undefined"
              >
                {{ heatMode === 'flow' && !partialErrors.sectors
                  ? `资金截面 ${flowAsOf || '暂无'}${sectorSnapshot?.stale ? ` · ${sectorSnapshotLabel}` : ''}`
                  : sectorSnapshotLabel }}
              </span>
              <span class="tab-pills">
                <button :class="{ on: heatMode === 'strength' }" :aria-pressed="heatMode === 'strength'" @click="heatMode = 'strength'">强度</button>
                <button :class="{ on: heatMode === 'change' }" :aria-pressed="heatMode === 'change'" @click="heatMode = 'change'">涨跌</button>
                <button :class="{ on: heatMode === 'flow' }" :aria-pressed="heatMode === 'flow'" @click="heatMode = 'flow'">资金流</button>
              </span>
            </span>
          </div>
          <div v-if="sectors.length" class="heat-grid overview-heat-grid">
            <div
              v-for="sector in sectors"
              :key="sector.plate_code"
              class="heat-tile"
              :class="{ 'no-flow': heatMode === 'flow' && sector.net_inflow == null }"
              :style="{ background: heatBackground(sector) }"
              :title="heatTitle(sector)"
            >
              <div class="t-name">{{ sector.plate_name }}</div>
              <div class="t-val">{{ heatPrimary(sector) }}</div>
              <div class="t-sub">{{ heatSecondary(sector) }}</div>
            </div>
          </div>
          <div v-else class="empty-hint">
            {{ partialErrors.sectors ? `板块数据暂不可用：${partialErrors.sectors}` : '暂无板块数据' }}
          </div>
        </div>

        <!-- 自选表 -->
        <div class="panel" style="padding-bottom: 6px">
          <div class="panel-title">
            自选股追踪
            <router-link to="/watchlist" class="extra" style="display: inline-flex; align-items: center; gap: 2px">
              全部自选 <ChevronRight :size="12" />
            </router-link>
          </div>
          <table class="tbl" v-if="watchlist.length">
            <thead>
              <tr>
                <th>代码 / 名称</th><th class="r">最新价</th><th class="r">涨跌</th><th>信号</th>
                <th>置信度</th><th class="r">20日预期</th><th>逻辑</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in watchlist" :key="row.symbol">
                <td class="sym" @click="router.push(`/stock/${row.symbol}`)">
                  <div style="display: flex; align-items: center; gap: 8px">
                    <Star :size="12" style="color: var(--warn); flex: none" fill="currentColor" />
                    <div>
                      <div class="name">{{ row.display_name || row.symbol }}</div>
                      <div class="code">{{ row.symbol }}</div>
                    </div>
                  </div>
                </td>
                <td class="r num">{{ fmtNum(row.last) }}</td>
                <td class="r num" :class="pctClass(row.change_pct)">{{ fmtPct(row.change_pct) }}</td>
                <td>
                  <span class="badge" :class="actionMeta(row.alert_action).cls">
                    {{ actionMeta(row.alert_action).label }}
                  </span>
                </td>
                <td><ConfRing :value="row.confidence_20d" :size="30" /></td>
                <td class="r num" :class="pctClass(row.expected_return_20d)">
                  {{ fmtPct(row.expected_return_20d, 2, false) }}
                </td>
                <td class="xs dim">{{ row.thesis_state === 'unchanged' ? '不变' : row.thesis_state }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-hint">自选列表为空，去「自选追踪」页添加</div>
        </div>
      </div>

      <!-- 右栏 -->
      <div class="grid overview-side">
        <!-- AI 结论（图标要点） -->
        <div class="panel">
          <div class="panel-title">
            <span style="display: inline-flex; align-items: center; gap: 6px">
              <Sparkles :size="13" style="color: var(--accent-hi)" /> 今日 AI 结论
            </span>
            <span class="extra mono">{{ data.ai_summary?.source === 'llm' ? 'LLM' : '规则' }} · {{ fmtTime(data.as_of) }}</span>
          </div>
          <div class="banner" style="padding: 9px 12px; font-size: 12px; margin-bottom: 12px">
            <span class="muted">{{ data.ai_summary?.text }}</span>
          </div>
          <div style="display: grid; gap: 11px">
            <div style="display: flex; gap: 10px" v-if="regime">
              <span class="icon-chip blue"><TrendingUp :size="14" /></span>
              <div style="min-width: 0">
                <div style="font-size: 12.5px; font-weight: 600">
                  市场状态：{{ regimeMeta(regime.regime).label }}
                  <span class="num dim xs">置信 {{ (regime.confidence * 100).toFixed(0) }}%</span>
                </div>
                <div class="xs dim">{{ (regime.explanation || [])[0] }}</div>
              </div>
            </div>
            <div style="display: flex; gap: 10px" v-if="sectors.length">
              <span class="icon-chip cyan"><Grid3x3 :size="14" /></span>
              <div style="min-width: 0">
                <div style="font-size: 12.5px; font-weight: 600">
                  {{ sectors[0].plate_name }}板块领涨
                  <span class="num xs" :class="pctClass(sectors[0].avg_change_pct)">{{ fmtPct(sectors[0].avg_change_pct) }}</span>
                </div>
                <div class="xs dim">
                  上涨占比 {{ (sectors[0].up_ratio * 100).toFixed(0) }}% · 龙头 {{ sectors[0].leader_name }}
                  {{ fmtPct(sectors[0].leader_change_pct) }}
                </div>
              </div>
            </div>
            <div style="display: flex; gap: 10px" v-if="marketBreadth">
              <span class="icon-chip green"><Activity :size="14" /></span>
              <div style="min-width: 0">
                <div style="font-size: 12.5px; font-weight: 600">
                  全市场宽度 <span class="num up">{{ marketBreadth.advancers }}</span> 涨 /
                  <span class="num down">{{ marketBreadth.decliners }}</span> 跌
                </div>
                <div class="xs dim">
                  涨停 {{ marketBreadth.limit_up }} · 跌停 {{ marketBreadth.limit_down }} · 炸板 {{ marketBreadth.broken_boards }}
                </div>
              </div>
            </div>
            <div style="display: flex; gap: 10px">
              <span class="icon-chip amber"><AlertTriangle :size="14" /></span>
              <div style="min-width: 0">
                <div style="font-size: 12.5px; font-weight: 600">
                  风险提示：{{ partialErrors.alerts
                    ? '提醒数据暂不可用'
                    : riskSignals.length
                      ? `${riskSignals.length} 条减仓/退出信号`
                      : '暂无风险信号' }}
                </div>
                <div class="xs dim">
                  {{ partialErrors.alerts
                    ? '无法判断当前是否存在风险信号'
                    : riskSignals.length
                      ? (riskSignals[0].reasons || [])[0]
                      : '基线模型输出，仅供工程验证' }}
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 交易提醒 -->
        <div class="panel">
          <div class="panel-title">
            交易提醒
            <router-link to="/alerts" class="extra" style="display: inline-flex; align-items: center; gap: 2px">
              更多 <ChevronRight :size="12" />
            </router-link>
          </div>
          <div v-if="feedAlerts.length">
            <div v-for="alert in feedAlerts.slice(0, 5)" :key="alert.id" class="feed-row">
              <span class="badge" :class="actionMeta(alert.action).cls">{{ actionMeta(alert.action).label }}</span>
              <div style="flex: 1; min-width: 0">
                <div class="num" style="font-weight: 600; font-size: 12px">{{ alert.symbol }}</div>
                <div class="xs dim" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis">
                  {{ (alert.reasons || [])[0] }}
                </div>
              </div>
              <span class="xs dim mono">{{ fmtTime(alert.created_at).slice(-8, -3) }}</span>
            </div>
          </div>
          <div v-else class="empty-hint">暂无提醒</div>
        </div>

        <!-- 近期活动 -->
        <div class="panel">
          <div class="panel-title">近期活动</div>
          <div v-if="partialErrors.jobs" class="activity-error">
            任务审计暂不可用：{{ partialErrors.jobs }}
          </div>
          <ul class="timeline" v-if="activities.length">
            <li v-for="event in activities" :key="event.id" :class="`activity-${event.cls}`">
              <div class="activity-time mono">{{ event.display }}</div>
              <div style="font-size: 12px">{{ event.text }}</div>
            </li>
          </ul>
          <div v-else-if="!partialErrors.jobs" class="empty-hint">暂无活动记录</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overview-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 328px;
  align-items: start;
  gap: var(--s3);
}

.overview-main,
.overview-side {
  min-width: 0;
}

.page-head-actions {
  margin-left: auto;
}

.refresh-button {
  white-space: nowrap;
}

.overview-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--s3);
}

.overview-chart-controls,
.heat-controls {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--s2);
  flex-wrap: wrap;
}

.overview-chart-controls .tab-pills,
.heat-controls .tab-pills {
  flex: 0 0 auto;
  max-width: 100%;
  overflow-x: auto;
}

.overview-chart-controls .tab-pills button,
.heat-controls .tab-pills button {
  flex: 0 0 auto;
  white-space: nowrap;
}

.overview-panel-title {
  align-items: flex-start;
  gap: var(--s3);
}

.panel-count {
  margin-left: 6px;
  color: var(--text-2);
  font-size: 10px;
  font-weight: 500;
}

.breadth-delta {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 9.5px;
}

.breadth-chips {
  display: flex;
  gap: 7px;
  margin-top: 5px;
  color: var(--text-2);
  font-size: 9.5px;
  white-space: nowrap;
}

.chart-footnote {
  margin-top: -7px;
  color: var(--text-2);
  font-size: 10.5px;
  text-align: right;
}

.error-text {
  color: var(--down);
}

.overview-heat-grid {
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
}

.overview-heat-grid .heat-tile {
  min-height: 66px;
}

.overview-heat-grid .heat-tile.no-flow {
  border: 1px dashed rgba(148, 163, 198, 0.22);
}

.overview-heat-grid .t-sub {
  color: rgba(255, 255, 255, 0.82);
}

.activity-time {
  margin-bottom: 2px;
  color: var(--text-2);
  font-size: 10.5px;
}

.activity-error {
  margin-bottom: 10px;
  color: var(--down);
  font-size: 11px;
  line-height: 1.45;
}

.timeline li.activity-green::before {
  background: var(--up);
}

.timeline li.activity-yellow::before {
  background: var(--warn);
}

.timeline li.activity-red::before {
  background: var(--down);
}

.overview-main > .panel {
  min-width: 0;
  overflow-x: auto;
}

.spin {
  animation: rotate 0.9s linear infinite;
}

@media (max-width: 1180px) {
  .overview-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .overview-side {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .overview-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .overview-side {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 560px) {
  .page-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: center;
    column-gap: var(--s2);
    row-gap: 2px;
  }

  .page-head h1,
  .page-head .sub {
    min-width: 0;
    white-space: nowrap;
  }

  .page-head h1 {
    grid-column: 1;
    grid-row: 1;
  }

  .page-head .sub {
    grid-column: 1;
    grid-row: 2;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .page-head-actions {
    grid-column: 2;
    grid-row: 1 / span 2;
    margin-left: 0;
  }

  .overview-stats {
    grid-template-columns: minmax(0, 1fr);
  }

  .overview-panel-title,
  .overview-chart-controls,
  .heat-controls {
    width: 100%;
    justify-content: flex-start;
  }

  .overview-chart-controls {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
  }

  .overview-chart-controls > .tab-pills {
    width: max-content;
  }

  .overview-heat-grid {
    grid-template-columns: repeat(auto-fill, minmax(88px, 1fr));
  }
}

@keyframes rotate {
  to {
    transform: rotate(360deg);
  }
}
</style>
