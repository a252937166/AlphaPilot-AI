<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  AlertTriangle,
  ArrowUpRight,
  Link2,
  RefreshCw,
  RotateCcw,
  TrendingUp,
  Waves,
} from 'lucide-vue-next'
import {
  api,
  type SectorForecastResponse,
  type SectorForecastRow,
  type SectorHorizon,
  type SectorLeadersResponse,
  type SectorLifecycle,
} from '../api'
import { CHART_COLORS, SERIES_PALETTE, tooltipStyle } from '../chartTheme'
import EChart from '../components/EChart.vue'
import LifecycleWheel from '../components/LifecycleWheel.vue'
import { fmtAmount, fmtDate, fmtNum, fmtPct, pctClass } from '../format'

type FlowMode = 'inflow' | 'outflow'

type FlowSlice = {
  key: string
  name: string
  raw: number
  value: number
  count?: number
}

const HORIZONS: SectorHorizon[] = [5, 10, 20]
const LIFECYCLE_META: Array<{ key: SectorLifecycle; label: string }> = [
  { key: 'rising', label: '上涨期' },
  { key: 'boom', label: '繁荣期' },
  { key: 'decline', label: '回落期' },
  { key: 'bottoming', label: '筑底期' },
  { key: 'recovery', label: '复苏期' },
]

const router = useRouter()
const horizon = ref<SectorHorizon>(5)
const loading = ref(true)
const reloading = ref(false)
const error = ref('')
const forecast = ref<SectorForecastResponse | null>(null)
const lifecycle = ref<SectorForecastResponse | null>(null)
const overbought = ref<SectorForecastResponse | null>(null)
const reversal = ref<SectorForecastResponse | null>(null)
const panelErrors = ref({ lifecycle: '', overbought: '', reversal: '' })
const selectedPlateCode = ref<string | null>(null)
const leaders = ref<SectorLeadersResponse | null>(null)
const leadersLoading = ref(false)
const leadersError = ref('')
const flowMode = ref<FlowMode>('inflow')
let loadEpoch = 0
let leadersEpoch = 0

const forecastRows = computed(() => forecast.value?.rows ?? [])
const selectedRow = computed(
  () => forecastRows.value.find((row) => row.plate_code === selectedPlateCode.value) ?? null,
)
const strongestRows = computed(() => forecastRows.value.slice(0, 3))
const overboughtRows = computed(() => overbought.value?.rows.slice(0, 3) ?? [])
const reversalRows = computed(() => reversal.value?.rows.slice(0, 3) ?? [])

const maxFlowMagnitude = computed(() => {
  const values = forecastRows.value
    .map((row) => finiteNumber(row.net_inflow))
    .filter((value): value is number => value !== null)
    .map(Math.abs)
  return Math.max(...values, 0)
})

const lifecycleStages = computed(() =>
  LIFECYCLE_META.map((stage) => ({
    ...stage,
    sectors: (lifecycle.value?.rows ?? [])
      .filter((row) => row.lifecycle === stage.key)
      .map((row) => row.plate_name),
  })),
)

const unclassifiedLifecycleCount = computed(
  () => lifecycle.value?.counts?.unclassified ?? 0,
)

const flowSlices = computed<FlowSlice[]>(() => {
  const rows = forecastRows.value
    .map((row) => ({ row, value: finiteNumber(row.net_inflow_5d) }))
    .filter(
      (item): item is { row: SectorForecastRow; value: number } =>
        item.value !== null &&
        (flowMode.value === 'inflow' ? item.value > 0 : item.value < 0),
    )
    .sort((left, right) => Math.abs(right.value) - Math.abs(left.value))

  const direct: FlowSlice[] = rows.slice(0, 7).map(({ row, value }) => ({
    key: row.plate_code,
    name: row.plate_name,
    raw: value,
    value: Math.abs(value),
  }))
  const remainder = rows.slice(7)
  if (remainder.length) {
    const raw = remainder.reduce((sum, item) => sum + item.value, 0)
    direct.push({
      key: 'other',
      name: `其他 ${remainder.length} 个板块`,
      raw,
      value: Math.abs(raw),
      count: remainder.length,
    })
  }
  return direct
})

const flowTotal = computed(() => flowSlices.value.reduce((sum, item) => sum + item.value, 0))
const flowSources = computed(() =>
  Array.from(
    new Set(
      forecastRows.value
        .map((row) => row.flow_source?.trim())
        .filter((value): value is string => Boolean(value)),
    ),
  ).join('、'),
)
const flowCoverageDays = computed(() =>
  Math.max(
    ...forecastRows.value.map((row) => finiteNumber(row.flow_coverage_days) ?? 0),
    0,
  ),
)
const flowWindowDays = computed(() => finiteNumber(forecast.value?.flow_window_days))
const flowCoverageLabel = computed(() => {
  const window = flowWindowDays.value
  if (window === null) return '资金流覆盖天数未提供'
  return `当前最长覆盖 ${flowCoverageDays.value}/${window} 日 · 仅完整窗口入图`
})
const modelNotes = computed(() =>
  Array.from(
    new Set(
      [forecast.value?.warning, forecast.value?.degraded_reason].filter(
        (value): value is string => Boolean(value?.trim()),
      ),
    ),
  ),
)

const flowAriaLabel = computed(() => {
  const mode = flowMode.value === 'inflow' ? '净流入' : '净流出'
  const detail = flowSlices.value
    .map((item) => `${item.name}${fmtAmount(item.raw)}`)
    .join('，')
  return `板块五日${mode}分布，总额${fmtAmount(
    flowMode.value === 'inflow' ? flowTotal.value : -flowTotal.value,
  )}${detail ? `。${detail}` : '。暂无完整数据'}`
})

const flowDonut = computed(() => {
  const isInflow = flowMode.value === 'inflow'
  const palette = isInflow
    ? [CHART_COLORS.up, CHART_COLORS.cyan, CHART_COLORS.accent, ...SERIES_PALETTE]
    : [CHART_COLORS.down, CHART_COLORS.warn, CHART_COLORS.purple, ...SERIES_PALETTE]
  return {
    animation: false,
    tooltip: {
      ...tooltipStyle,
      confine: true,
      formatter: (item: { name: string; value: number; percent: number; dataIndex: number }) => {
        const raw = flowSlices.value[item.dataIndex]?.raw
        return `${item.name}<br/>${fmtAmount(raw)} · ${item.percent.toFixed(1)}%`
      },
    },
    title: {
      text: fmtAmount(isInflow ? flowTotal.value : -flowTotal.value),
      subtext: `5日${isInflow ? '净流入' : '净流出'}`,
      left: 'center',
      top: '39%',
      textAlign: 'center',
      textStyle: {
        color: '#eef2fa',
        fontSize: 15,
        fontFamily: "ui-monospace,'SF Mono',Menlo,monospace",
      },
      subtextStyle: { color: '#9aa7c4', fontSize: 10 },
    },
    series: [
      {
        type: 'pie',
        radius: ['56%', '76%'],
        center: ['50%', '50%'],
        label: { show: false },
        itemStyle: { borderColor: '#0a101e', borderWidth: 2 },
        emphasis: { scaleSize: 4 },
        data: flowSlices.value.map((item, index) => ({
          name: item.name,
          value: item.value,
          itemStyle: { color: palette[index % palette.length] },
        })),
      },
    ],
  }
})

function finiteNumber(value: unknown): number | null {
  const parsed = Number(value)
  return value === null || value === undefined || !Number.isFinite(parsed) ? null : parsed
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason)
}

function expectHorizon(
  response: SectorForecastResponse,
  expected: SectorHorizon,
): SectorForecastResponse {
  if (response.horizon !== expected) {
    throw new Error(`接口返回 ${response.horizon} 日数据，与当前 ${expected} 日周期不一致`)
  }
  return response
}

function fmtRatio(value: unknown, digits = 1): string {
  const ratio = finiteNumber(value)
  return ratio === null ? '—' : `${(ratio * 100).toFixed(digits)}%`
}

function scoreWidth(value: unknown): string {
  const score = finiteNumber(value)
  return `${score === null ? 0 : Math.min(100, Math.max(0, score))}%`
}

function flowHeatWidth(row: SectorForecastRow): string {
  const flow = finiteNumber(row.net_inflow)
  return `${flow === null || maxFlowMagnitude.value === 0 ? 0 : (Math.abs(flow) / maxFlowMagnitude.value) * 100}%`
}

function flowHeatLabel(row: SectorForecastRow): string {
  const flow = finiteNumber(row.net_inflow)
  if (flow === null) return `${row.plate_name}最近交易日资金热度数据不足`
  return `${row.plate_name}最近交易日资金${flow >= 0 ? '净流入' : '净流出'}${fmtAmount(flow)}`
}

function stockSymbol(value: string | null | undefined): string | null {
  const normalized = String(value ?? '').split('.').pop() ?? ''
  return /^\d{6}$/.test(normalized) ? normalized : null
}

function openStock(value: string | null | undefined): void {
  const symbol = stockSymbol(value)
  if (symbol) void router.push(`/stock/${symbol}`)
}

async function loadLeaders(plateCode: string): Promise<void> {
  const epoch = ++leadersEpoch
  leadersLoading.value = true
  leadersError.value = ''
  leaders.value = null
  try {
    const response = await api.sectorLeaders(plateCode)
    if (epoch !== leadersEpoch || selectedPlateCode.value !== plateCode) return
    if (response.plate_code !== plateCode) {
      throw new Error(`接口返回板块 ${response.plate_code}，与当前 ${plateCode} 不一致`)
    }
    leaders.value = response
  } catch (reason: unknown) {
    if (epoch !== leadersEpoch || selectedPlateCode.value !== plateCode) return
    leadersError.value = errorMessage(reason)
  } finally {
    if (epoch === leadersEpoch) leadersLoading.value = false
  }
}

function selectSector(row: SectorForecastRow): void {
  if (selectedPlateCode.value === row.plate_code && leaders.value) return
  selectedPlateCode.value = row.plate_code
  void loadLeaders(row.plate_code)
}

async function load(showSkeleton = true): Promise<void> {
  const epoch = ++loadEpoch
  leadersEpoch += 1
  loading.value = showSkeleton
  reloading.value = !showSkeleton
  error.value = ''
  leaders.value = null
  leadersError.value = ''
  leadersLoading.value = false
  panelErrors.value = { lifecycle: '', overbought: '', reversal: '' }
  if (showSkeleton) {
    forecast.value = null
    lifecycle.value = null
    overbought.value = null
    reversal.value = null
  }

  const activeHorizon = horizon.value
  try {
    const [forecastResult, lifecycleResult, overboughtResult, reversalResult] =
      await Promise.allSettled([
        api.sectorForecast(activeHorizon).then((response) =>
          expectHorizon(response, activeHorizon),
        ),
        api.sectorLifecycle(activeHorizon).then((response) =>
          expectHorizon(response, activeHorizon),
        ),
        api.sectorOverbought(activeHorizon).then((response) =>
          expectHorizon(response, activeHorizon),
        ),
        api.sectorReversal(activeHorizon).then((response) =>
          expectHorizon(response, activeHorizon),
        ),
      ])
    if (epoch !== loadEpoch || horizon.value !== activeHorizon) return

    if (forecastResult.status === 'fulfilled') {
      forecast.value = forecastResult.value
    } else {
      forecast.value = null
      error.value = `板块预测加载失败：${errorMessage(forecastResult.reason)}`
    }

    if (lifecycleResult.status === 'fulfilled') lifecycle.value = lifecycleResult.value
    else {
      lifecycle.value = null
      panelErrors.value.lifecycle = errorMessage(lifecycleResult.reason)
    }
    if (overboughtResult.status === 'fulfilled') overbought.value = overboughtResult.value
    else {
      overbought.value = null
      panelErrors.value.overbought = errorMessage(overboughtResult.reason)
    }
    if (reversalResult.status === 'fulfilled') reversal.value = reversalResult.value
    else {
      reversal.value = null
      panelErrors.value.reversal = errorMessage(reversalResult.reason)
    }

    const rows = forecast.value?.rows ?? []
    const currentStillExists = rows.some((row) => row.plate_code === selectedPlateCode.value)
    selectedPlateCode.value = currentStillExists
      ? selectedPlateCode.value
      : (rows[0]?.plate_code ?? null)
    if (selectedPlateCode.value) void loadLeaders(selectedPlateCode.value)
  } finally {
    if (epoch === loadEpoch) {
      loading.value = false
      reloading.value = false
    }
  }
}

function chooseHorizon(value: SectorHorizon): void {
  if (value === horizon.value || loading.value || reloading.value) return
  horizon.value = value
  void load(true)
}

onMounted(() => void load(true))
onBeforeUnmount(() => {
  loadEpoch += 1
  leadersEpoch += 1
})
</script>

<template>
  <div class="sectors-page">
    <header class="page-head sector-page-head">
      <div class="title-block">
        <h1>板块预测</h1>
        <span class="sub">横截面强度、滚动胜率与资金扩散的统一观察面板</span>
      </div>
      <div class="page-actions">
        <div class="horizon-picker" role="group" aria-label="预测周期">
          <span>预测周期</span>
          <div class="tab-pills">
            <button
              v-for="item in HORIZONS"
              :key="item"
              type="button"
              :aria-pressed="horizon === item"
              :class="{ on: horizon === item }"
              :disabled="loading || reloading"
              @click="chooseHorizon(item)"
            >
              {{ item }}日
            </button>
          </div>
        </div>
        <button
          type="button"
          class="btn ghost refresh-button"
          :disabled="loading || reloading"
          @click="load(false)"
        >
          <RefreshCw :size="13" :class="{ spin: loading || reloading }" />
          刷新
        </button>
      </div>
    </header>

    <div v-if="error" class="banner error page-banner" role="alert">
      {{ error }}。页面不会使用历史副本或虚构数据填充，请先核对日线与 sector_forecast 任务。
    </div>

    <div v-if="loading" class="sector-skeleton" aria-label="正在加载板块预测">
      <div class="skeleton skeleton-main" />
      <div class="skeleton skeleton-rail" />
      <div class="skeleton skeleton-short" />
      <div class="skeleton skeleton-short" />
      <div class="skeleton skeleton-short" />
    </div>

    <div v-else-if="!forecast" class="panel primary-empty" role="status">
      <Waves :size="22" />
      <strong>当前没有可审计的板块预测截面</strong>
      <span>预测任务补齐至最新交易日后，可查看 5 / 10 / 20 日真实排名与滚动胜率。</span>
      <button type="button" class="btn" :disabled="reloading" @click="load(false)">
        <RefreshCw :size="13" /> 重新检查
      </button>
    </div>

    <template v-else>
      <div v-if="modelNotes.length" class="model-note" role="note">
        <AlertTriangle :size="14" />
        <div>
          <span v-for="note in modelNotes" :key="note">{{ note }}</span>
        </div>
      </div>

      <div class="coverage-strip" :class="{ stale: forecast.stale }" role="status">
        <div>
          <span>预测数据日期</span>
          <strong class="num">{{ fmtDate(forecast.as_of) }}</strong>
          <span v-if="forecast.stale" class="badge yellow">最近完整截面</span>
        </div>
        <div v-if="forecast.input_coverage">
          <span>最新日线 {{ fmtDate(forecast.input_trade_date) }}</span>
          <strong class="num">
            {{ forecast.input_coverage.latest_symbol_count }}/{{ forecast.input_coverage.reference_symbol_count }}
          </strong>
          <span class="num">
            覆盖 {{ fmtRatio(forecast.input_coverage.ratio, 1) }}
            · 展示阈值 {{ fmtRatio(forecast.input_coverage.minimum_ratio, 0) }}
          </span>
          <span>
            对比基准 {{ fmtDate(forecast.input_coverage.reference_trade_date) }}
          </span>
        </div>
        <div v-else>
          <span>最新输入覆盖率未提供，已按接口返回的预测截面展示。</span>
        </div>
      </div>

      <section class="sector-main-grid" aria-label="板块预测排行与龙头联动">
        <div class="panel ranking-panel">
          <div class="panel-title ranking-title">
            <div class="panel-title-copy">
              <strong>{{ horizon }}日板块预测排行</strong>
              <span>{{ forecast.count }} 个板块 · {{ forecast.model_version }}</span>
            </div>
            <span class="as-of num">截面 {{ fmtDate(forecast.as_of) }}</span>
          </div>

          <div v-if="forecastRows.length" class="table-shell" tabindex="0" aria-label="板块预测排行榜，可横向滚动">
            <table class="tbl sector-table">
              <caption class="sr-only">{{ horizon }}日板块预测排行榜</caption>
              <thead>
                <tr>
                  <th scope="col">排名</th>
                  <th scope="col">板块</th>
                  <th scope="col">热度 · 单日</th>
                  <th scope="col" class="r">资金流 5日</th>
                  <th scope="col">强度</th>
                  <th scope="col" class="r">未来{{ horizon }}日胜率</th>
                  <th scope="col" class="r">预期超额</th>
                  <th scope="col">龙头股</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="row in forecastRows"
                  :key="row.plate_code"
                  :class="{ selected: selectedPlateCode === row.plate_code }"
                >
                  <td class="rank-cell num" :class="{ podium: row.rank <= 3 }">{{ row.rank }}</td>
                  <th scope="row">
                    <button
                      type="button"
                      class="sector-name-button"
                      :aria-pressed="selectedPlateCode === row.plate_code"
                      @click="selectSector(row)"
                    >
                      <span>{{ row.plate_name }}</span>
                      <small class="mono">{{ row.plate_code }}</small>
                    </button>
                  </th>
                  <td>
                    <div
                      v-if="finiteNumber(row.net_inflow) !== null"
                      class="flow-heat"
                      role="img"
                      :aria-label="flowHeatLabel(row)"
                    >
                      <i
                        :class="Number(row.net_inflow) >= 0 ? 'positive' : 'negative'"
                        :style="{ width: flowHeatWidth(row) }"
                      />
                    </div>
                    <span v-else class="cell-note">待数据</span>
                  </td>
                  <td class="r">
                    <div class="flow-value">
                      <span class="num" :class="pctClass(row.net_inflow_5d)">{{ fmtAmount(row.net_inflow_5d) }}</span>
                      <small class="num">{{ row.flow_coverage_days }}/{{ forecast.flow_window_days }}日</small>
                    </div>
                  </td>
                  <td>
                    <div class="score-cell">
                      <span class="num">{{ fmtNum(row.score, 1) }}</span>
                      <span class="score-bar" aria-hidden="true"><i :style="{ width: scoreWidth(row.score) }" /></span>
                    </div>
                  </td>
                  <td class="r num">{{ fmtRatio(row.win_rate, 1) }}</td>
                  <td class="r num" :class="pctClass(row.expected_excess)">
                    {{ fmtPct(row.expected_excess, 2, false) }}
                  </td>
                  <td>
                    <button
                      v-if="stockSymbol(row.leader_code)"
                      type="button"
                      class="stock-link"
                      :aria-label="`查看龙头股${row.leader_name || stockSymbol(row.leader_code)}`"
                      @click="openStock(row.leader_code)"
                    >
                      <span>{{ row.leader_name || stockSymbol(row.leader_code) }}</span>
                      <small class="num" :class="pctClass(row.leader_change_pct)">
                        {{ fmtPct(row.leader_change_pct) }}
                      </small>
                      <ArrowUpRight :size="12" />
                    </button>
                    <span v-else class="cell-note">暂无审计快照</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-hint">该周期没有可展示的预测行。</div>

          <footer class="data-footnote">
            <span>胜率：过去滚动验证中进入 Top 20% 后跑赢截面中位数的比例</span>
            <span v-if="forecast.flow_as_of">资金流截至 {{ fmtDate(forecast.flow_as_of) }}</span>
          </footer>
        </div>

        <aside class="panel leaders-panel" aria-label="联动个股">
          <div class="panel-title">
            <div class="panel-title-copy">
              <strong>联动个股</strong>
              <span>20 日收益相关性</span>
            </div>
            <Link2 :size="14" class="panel-icon" />
          </div>

          <template v-if="selectedRow">
            <div class="selected-sector-heading">
              <div>
                <span>当前板块</span>
                <strong>{{ selectedRow.plate_name }}</strong>
              </div>
              <span class="badge blue num">{{ fmtNum(selectedRow.score, 1) }}</span>
            </div>

            <div v-if="leadersLoading" class="leaders-loading" aria-label="正在计算联动个股">
              <div v-for="index in 5" :key="index" class="skeleton" />
            </div>
            <div v-else-if="leadersError" class="local-state error-state" role="status">
              <AlertTriangle :size="15" />
              <span>联动榜不可用：{{ leadersError }}</span>
            </div>
            <template v-else-if="leaders">
              <div class="leader-anchor">
                <span class="anchor-mark"><TrendingUp :size="14" /></span>
                <div>
                  <span>收益龙头</span>
                  <button type="button" @click="openStock(leaders.leader.symbol)">
                    {{ leaders.leader.name || leaders.leader.symbol }}
                  </button>
                </div>
                <strong class="num" :class="pctClass(leaders.leader.return_20d)">
                  {{ fmtPct(leaders.leader.return_20d, 2, false) }}
                </strong>
              </div>

              <div v-if="leaders.rows.length" class="correlation-list">
                <button
                  v-for="item in leaders.rows"
                  :key="item.symbol"
                  type="button"
                  class="correlation-row"
                  @click="openStock(item.symbol)"
                >
                  <span class="num row-rank">{{ item.rank }}</span>
                  <span class="linked-name">
                    <strong>{{ item.name || item.symbol }}</strong>
                    <small class="mono">{{ item.symbol }}</small>
                  </span>
                  <span class="correlation-value">
                    <span class="correlation-track" aria-hidden="true">
                      <i :style="{ width: `${Math.min(100, Math.abs(item.correlation) * 100)}%` }" />
                    </span>
                    <span class="num">ρ {{ fmtNum(item.correlation, 2) }}</span>
                    <small v-if="item.observations" class="num">n={{ item.observations }}</small>
                  </span>
                  <span class="num linked-return" :class="pctClass(item.return_20d)">
                    {{ fmtPct(item.return_20d, 1, false) }}
                  </span>
                </button>
              </div>
              <div v-else class="local-state">没有满足 21 根完整日线与有效相关性的成份股。</div>

              <div class="method-note">
                Pearson 日收益 · {{ leaders.lookback_sessions }} 个交易日 · 截至 {{ fmtDate(leaders.as_of) }}
                · 有效 {{ leaders.eligible_members }}/{{ leaders.constituent_count }} 只
                <span v-if="leaders.sources.length || leaders.source">
                  · 来源 {{ leaders.sources.join('、') || leaders.source }}
                </span>
              </div>
            </template>
            <div v-else class="local-state">选择排行中的板块后加载联动榜。</div>
          </template>
          <div v-else class="local-state">当前没有可选择的板块。</div>
        </aside>
      </section>

      <section class="signals-grid" aria-label="板块信号摘要">
        <article class="panel signal-panel bullish-panel">
          <div class="panel-title">
            <span class="signal-title"><TrendingUp :size="14" /> 最强看多</span>
            <span class="extra">横截面 score</span>
          </div>
          <div v-if="strongestRows.length" class="signal-list">
            <button
              v-for="row in strongestRows"
              :key="row.plate_code"
              type="button"
              @click="selectSector(row)"
            >
              <span class="num signal-rank">{{ row.rank }}</span>
              <span class="signal-name">{{ row.plate_name }}</span>
              <strong class="num">{{ fmtNum(row.score, 1) }}</strong>
              <span class="num" :class="pctClass(row.expected_excess)">
                {{ fmtPct(row.expected_excess, 1, false) }}
              </span>
            </button>
          </div>
          <div v-else class="local-state">暂无有效预测行。</div>
        </article>

        <article class="panel signal-panel overbought-panel">
          <div class="panel-title">
            <span class="signal-title"><AlertTriangle :size="14" /> 超买预警</span>
            <span class="extra">RSI(14) &gt; 70</span>
          </div>
          <div v-if="panelErrors.overbought" class="local-state error-state">
            {{ panelErrors.overbought }}
          </div>
          <div v-else-if="overboughtRows.length" class="signal-list">
            <button
              v-for="row in overboughtRows"
              :key="row.plate_code"
              type="button"
              @click="selectSector(row)"
            >
              <span class="num signal-rank">{{ row.rank }}</span>
              <span class="signal-copy">
                <span class="signal-name">{{ row.plate_name }}</span>
                <span v-if="finiteNumber(row.net_inflow) !== null" class="signal-flow">
                  <span class="signal-flow-track" aria-hidden="true">
                    <i
                      :class="Number(row.net_inflow) >= 0 ? 'positive' : 'negative'"
                      :style="{ width: flowHeatWidth(row) }"
                    />
                  </span>
                  <small class="num">热度 {{ fmtAmount(row.net_inflow) }}</small>
                </span>
                <small v-else class="cell-note">热度待数据</small>
              </span>
              <span class="badge yellow">超买</span>
              <strong class="num">RSI {{ fmtNum(row.rsi14, 1) }}</strong>
            </button>
          </div>
          <div v-else class="local-state">当前没有 RSI 超过 70 的板块。</div>
        </article>

        <article class="panel signal-panel reversal-panel">
          <div class="panel-title">
            <span class="signal-title"><RotateCcw :size="14" /> 反转潜力</span>
            <span class="extra">低位 + 资金转正</span>
          </div>
          <div v-if="panelErrors.reversal" class="local-state error-state">
            {{ panelErrors.reversal }}
          </div>
          <div v-else-if="reversal && !reversal.available" class="local-state">
            {{ reversal.reason || '资金流历史不足，暂不能计算反转潜力。' }}
          </div>
          <div v-else-if="reversalRows.length" class="signal-list">
            <button
              v-for="row in reversalRows"
              :key="row.plate_code"
              type="button"
              @click="selectSector(row)"
            >
              <span class="num signal-rank">{{ row.rank }}</span>
              <span class="signal-name">{{ row.plate_name }}</span>
              <span class="badge green">反转信号</span>
              <strong class="num">{{ fmtNum(row.reversal_score, 1) }}</strong>
            </button>
          </div>
          <div v-else class="local-state">当前没有满足反转条件的板块。</div>
        </article>
      </section>

      <section class="lower-grid" aria-label="生命周期与资金流分布">
        <div class="panel lifecycle-panel">
          <div class="panel-title">
            <div class="panel-title-copy">
              <strong>板块生命周期</strong>
              <span>强度趋势、资金方向与 RSI 规则分类</span>
            </div>
            <span class="badge gray">{{ horizon }}日截面</span>
          </div>
          <div v-if="panelErrors.lifecycle" class="local-state error-state">
            生命周期不可用：{{ panelErrors.lifecycle }}
          </div>
          <LifecycleWheel
            v-else-if="lifecycle?.rows.length"
            :stages="lifecycleStages"
            height="248px"
          />
          <div v-else class="local-state">当前截面没有可分类的板块。</div>
          <div v-if="unclassifiedLifecycleCount" class="method-note">
            另有 {{ unclassifiedLifecycleCount }} 个板块因输入不足未分类，未强行归入任一阶段。
          </div>
        </div>

        <div class="panel flow-panel">
          <div class="panel-title flow-title">
            <div class="panel-title-copy">
              <strong>资金流向分布</strong>
              <span>{{ flowCoverageLabel }}</span>
            </div>
            <div class="tab-pills" role="group" aria-label="资金流方向">
              <button
                type="button"
                :aria-pressed="flowMode === 'inflow'"
                :class="{ on: flowMode === 'inflow' }"
                @click="flowMode = 'inflow'"
              >
                净流入
              </button>
              <button
                type="button"
                :aria-pressed="flowMode === 'outflow'"
                :class="{ on: flowMode === 'outflow' }"
                @click="flowMode = 'outflow'"
              >
                净流出
              </button>
            </div>
          </div>

          <div v-if="flowSlices.length" class="flow-layout">
            <EChart :option="flowDonut" height="244px" :aria-label="flowAriaLabel" />
            <div class="flow-legend" aria-hidden="true">
              <div v-for="(item, index) in flowSlices" :key="item.key" class="flow-legend-row">
                <i
                  :style="{
                    background:
                      flowMode === 'inflow'
                        ? [CHART_COLORS.up, CHART_COLORS.cyan, CHART_COLORS.accent, ...SERIES_PALETTE][index % (SERIES_PALETTE.length + 3)]
                        : [CHART_COLORS.down, CHART_COLORS.warn, CHART_COLORS.purple, ...SERIES_PALETTE][index % (SERIES_PALETTE.length + 3)],
                  }"
                />
                <span>{{ item.name }}</span>
                <strong class="num">{{ fmtAmount(item.raw) }}</strong>
                <small class="num">{{ ((item.value / flowTotal) * 100).toFixed(1) }}%</small>
              </div>
            </div>
          </div>
          <div v-else class="local-state flow-empty">
            <Waves :size="18" />
            <span>
              {{
                forecast.flow_mode === 'no-flow'
                  ? `资金流历史尚未形成完整窗口（${flowCoverageLabel}），当前不绘制分布。`
                  : `当前没有完整窗口内的${flowMode === 'inflow' ? '净流入' : '净流出'}板块。`
              }}
            </span>
          </div>
          <div class="method-note">
            <span v-if="forecast.flow_as_of">截至 {{ fmtDate(forecast.flow_as_of) }}</span>
            <span v-if="flowSources"> · 来源 {{ flowSources }}</span>
            <span v-if="!forecast.flow_as_of">暂无完整资金流日期</span>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.sectors-page {
  min-width: 0;
}

.sector-page-head {
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
}

.title-block {
  display: flex;
  align-items: baseline;
  gap: var(--s3);
  min-width: 0;
}

.page-actions,
.horizon-picker {
  display: flex;
  align-items: center;
  gap: var(--s2);
}

.page-actions {
  margin-left: auto;
}

.horizon-picker > span {
  color: var(--text-2);
  font-size: 11.5px;
  white-space: nowrap;
}

.refresh-button {
  min-height: 30px;
}

.page-banner {
  margin-bottom: var(--s3);
}

.model-note {
  display: flex;
  align-items: flex-start;
  gap: var(--s2);
  margin-bottom: var(--s3);
  padding: 9px 12px;
  border: 1px solid rgba(251, 191, 36, 0.22);
  border-radius: var(--r-md);
  background: rgba(251, 191, 36, 0.055);
  color: var(--text-2);
  font-size: 11.5px;
  line-height: 1.6;
}

.model-note svg {
  flex: none;
  margin-top: 2px;
  color: var(--warn);
}

.model-note > div {
  display: grid;
  gap: 3px;
}

.coverage-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px var(--s5);
  margin-bottom: var(--s3);
  padding: 9px 12px;
  border: 1px solid var(--line-1);
  border-radius: var(--r-md);
  background: rgba(59, 130, 246, 0.055);
  color: var(--text-2);
  font-size: 10.5px;
}

.coverage-strip.stale {
  border-color: rgba(251, 191, 36, 0.22);
  background: rgba(251, 191, 36, 0.045);
}

.coverage-strip > div {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.coverage-strip strong {
  color: var(--text-1);
  font-size: 11px;
}

.sector-skeleton {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 330px);
  gap: var(--s3);
}

.skeleton-main {
  min-height: 510px;
}

.skeleton-rail {
  min-height: 510px;
}

.skeleton-short {
  min-height: 164px;
}

.primary-empty {
  min-height: 320px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: var(--s3);
  text-align: center;
  color: var(--text-2);
}

.primary-empty strong {
  color: var(--text-1);
  font-size: 14px;
}

.primary-empty span {
  max-width: 58ch;
}

.sector-main-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(300px, 342px);
  gap: var(--s3);
  align-items: stretch;
}

.ranking-panel,
.leaders-panel,
.signal-panel,
.lifecycle-panel,
.flow-panel {
  min-width: 0;
}

.ranking-panel {
  padding-bottom: 10px;
}

.panel-title-copy {
  min-width: 0;
  display: grid;
  gap: 1px;
}

.panel-title-copy strong {
  font-size: 12.5px;
  font-weight: 650;
}

.panel-title-copy span {
  overflow: hidden;
  color: var(--text-2);
  font-size: 10.5px;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.as-of {
  flex: none;
  color: var(--text-2);
  font-size: 10.5px;
  font-weight: 400;
}

.table-shell {
  max-width: 100%;
  max-height: 506px;
  overflow: auto;
  overscroll-behavior: contain;
}

.sector-table {
  min-width: 980px;
}

.sector-table th,
.sector-table td {
  padding-block: 9px;
}

.sector-table thead th {
  z-index: 1;
  background: var(--surface-2);
}

.sector-table tbody tr.selected {
  background: rgba(59, 130, 246, 0.105);
}

.sector-table tbody tr.selected th:first-of-type {
  box-shadow: inset 2px 0 var(--accent-hi);
}

.rank-cell {
  width: 44px;
  color: var(--text-2);
}

.rank-cell.podium {
  color: var(--warn);
  font-weight: 700;
}

.sector-name-button,
.stock-link,
.leader-anchor button {
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.sector-name-button {
  display: grid;
  justify-items: start;
  gap: 1px;
  text-align: left;
}

.sector-name-button > span {
  color: var(--text-1);
  font-weight: 650;
}

.sector-name-button small {
  color: var(--text-2);
  font-size: 10.5px;
  font-weight: 400;
}

.sector-name-button:hover > span,
.stock-link:hover > span,
.leader-anchor button:hover {
  color: var(--accent-hi);
}

.flow-heat {
  width: 76px;
  height: 5px;
  overflow: hidden;
  border-radius: 3px;
  background: rgba(148, 163, 198, 0.12);
}

.flow-heat i {
  display: block;
  height: 100%;
  border-radius: inherit;
}

.flow-heat i.positive {
  background: linear-gradient(90deg, var(--accent), var(--up));
}

.flow-heat i.negative {
  background: linear-gradient(90deg, var(--warn), var(--down));
}

.score-cell {
  display: flex;
  align-items: center;
  gap: var(--s2);
}

.flow-value {
  display: grid;
  justify-items: end;
  gap: 1px;
}

.flow-value small {
  color: var(--text-2);
  font-size: 10px;
}

.score-cell > .num {
  width: 34px;
  color: var(--cyan);
  font-weight: 650;
}

.score-cell .score-bar {
  width: 64px;
}

.stock-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 26px;
  text-align: left;
}

.stock-link > span {
  max-width: 86px;
  overflow: hidden;
  color: var(--text-1);
  font-weight: 600;
  text-overflow: ellipsis;
}

.stock-link small {
  font-size: 10px;
}

.stock-link svg {
  color: var(--text-2);
}

.cell-note {
  color: var(--text-2);
  font-size: 10.5px;
}

.data-footnote {
  display: flex;
  justify-content: space-between;
  gap: var(--s3);
  padding-top: 9px;
  color: var(--text-2);
  font-size: 10.5px;
  line-height: 1.5;
}

.panel-icon {
  color: var(--cyan);
}

.selected-sector-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
  padding-bottom: var(--s3);
  border-bottom: 1px solid var(--line-1);
}

.selected-sector-heading > div {
  display: grid;
  gap: 2px;
}

.selected-sector-heading span:first-child {
  color: var(--text-2);
  font-size: 10.5px;
}

.selected-sector-heading strong {
  font-size: 14px;
}

.leaders-loading {
  display: grid;
  gap: var(--s2);
  padding-top: var(--s4);
}

.leaders-loading .skeleton {
  min-height: 43px;
}

.leader-anchor {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 12px 0;
  border-bottom: 1px solid var(--line-1);
}

.anchor-mark {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: var(--r-sm);
  background: rgba(52, 211, 153, 0.11);
  color: var(--up);
}

.leader-anchor > div {
  min-width: 0;
  display: grid;
}

.leader-anchor > div > span {
  color: var(--text-2);
  font-size: 10px;
}

.leader-anchor button {
  overflow: hidden;
  color: var(--text-1);
  font-weight: 650;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.correlation-list {
  display: grid;
  margin-top: 4px;
}

.correlation-row {
  width: 100%;
  display: grid;
  grid-template-columns: 18px minmax(84px, 1fr) minmax(72px, 0.8fr) auto;
  align-items: center;
  gap: 8px;
  padding: 9px 0;
  border: 0;
  border-bottom: 1px solid var(--line-1);
  background: transparent;
  color: var(--text-1);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--t-fast);
}

.correlation-row:hover {
  background: rgba(96, 165, 250, 0.05);
}

.row-rank {
  color: var(--text-2);
  font-size: 10px;
}

.linked-name {
  min-width: 0;
  display: grid;
}

.linked-name strong,
.linked-name small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.linked-name strong {
  font-size: 11.5px;
}

.linked-name small {
  color: var(--text-2);
  font-size: 10.5px;
}

.correlation-value {
  min-width: 0;
  display: grid;
  gap: 3px;
  color: var(--text-2);
  font-size: 10.5px;
}

.correlation-value small {
  color: var(--text-2);
  font-size: 10px;
}

.correlation-track {
  height: 3px;
  overflow: hidden;
  border-radius: 2px;
  background: rgba(148, 163, 198, 0.12);
}

.correlation-track i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--cyan);
}

.linked-return {
  font-size: 10px;
}

.method-note {
  margin-top: var(--s3);
  padding-top: var(--s3);
  border-top: 1px solid var(--line-1);
  color: var(--text-2);
  font-size: 10.5px;
  line-height: 1.55;
}

.signals-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--s3);
  margin-top: var(--s3);
}

.signal-title {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.bullish-panel .signal-title {
  color: var(--up);
}

.overbought-panel .signal-title {
  color: var(--warn);
}

.reversal-panel .signal-title {
  color: var(--cyan);
}

.signal-list {
  display: grid;
}

.signal-list button {
  width: 100%;
  min-height: 38px;
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: var(--s2);
  padding: 7px 0;
  border: 0;
  border-bottom: 1px solid var(--line-1);
  background: transparent;
  color: var(--text-1);
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.signal-list button:last-child {
  border-bottom: 0;
}

.signal-list button:hover .signal-name {
  color: var(--accent-hi);
}

.signal-rank {
  color: var(--warn);
  font-size: 10.5px;
  font-weight: 650;
}

.signal-name {
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.signal-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.signal-flow {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-2);
}

.signal-flow-track {
  width: 42px;
  height: 3px;
  overflow: hidden;
  border-radius: 2px;
  background: rgba(148, 163, 198, 0.12);
}

.signal-flow-track i {
  display: block;
  height: 100%;
  border-radius: inherit;
}

.signal-flow-track i.positive {
  background: var(--up);
}

.signal-flow-track i.negative {
  background: var(--down);
}

.signal-flow small {
  font-size: 10px;
}

.signal-list strong {
  font-size: 11px;
}

.lower-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr);
  gap: var(--s3);
  margin-top: var(--s3);
  align-items: stretch;
}

.flow-title {
  align-items: flex-start;
}

.flow-layout {
  display: grid;
  grid-template-columns: minmax(190px, 0.8fr) minmax(170px, 1fr);
  align-items: center;
  gap: var(--s3);
}

.flow-legend {
  min-width: 0;
  display: grid;
  gap: 7px;
}

.flow-legend-row {
  display: grid;
  grid-template-columns: 7px minmax(0, 1fr) auto 42px;
  align-items: center;
  gap: 7px;
  font-size: 10.5px;
}

.flow-legend-row > i {
  width: 7px;
  height: 7px;
  border-radius: 2px;
}

.flow-legend-row > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.flow-legend-row > strong {
  font-size: 10.5px;
  font-weight: 550;
}

.flow-legend-row > small {
  color: var(--text-2);
  font-size: 10px;
  text-align: right;
}

.local-state {
  min-height: 116px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--s2);
  padding: var(--s4);
  color: var(--text-2);
  font-size: 11.5px;
  line-height: 1.6;
  text-align: center;
}

.error-state {
  color: #fca5a5;
}

.flow-empty {
  min-height: 244px;
  flex-direction: column;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.spin {
  animation: rotate 0.9s linear infinite;
}

@keyframes rotate {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1220px) {
  .sector-main-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .leaders-panel {
    min-height: auto;
  }

  .correlation-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0 var(--s4);
  }
}

@media (max-width: 1020px) {
  .signals-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .signals-grid > :last-child {
    grid-column: 1 / -1;
  }

  .lower-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 760px) {
  .sector-page-head {
    align-items: flex-start;
  }

  .title-block {
    width: 100%;
    flex-wrap: wrap;
    row-gap: 2px;
  }

  .page-actions {
    width: 100%;
    justify-content: space-between;
    margin-left: 0;
  }

  .sector-skeleton,
  .signals-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .signals-grid > :last-child {
    grid-column: auto;
  }

  .skeleton-main,
  .skeleton-rail {
    min-height: 280px;
  }

  .correlation-list {
    grid-template-columns: minmax(0, 1fr);
  }

  .flow-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .flow-legend {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .data-footnote {
    flex-direction: column;
    gap: 3px;
  }
}

@media (max-width: 520px) {
  .horizon-picker {
    flex: 1;
    justify-content: space-between;
  }

  .horizon-picker > span {
    display: none;
  }

  .tab-pills button {
    padding-inline: 9px;
  }

  .ranking-title,
  .flow-title {
    align-items: flex-start;
    flex-direction: column;
  }

  .as-of {
    white-space: normal;
  }

  .flow-title .tab-pills {
    align-self: stretch;
  }

  .flow-title .tab-pills button {
    flex: 1;
  }

  .flow-legend {
    grid-template-columns: minmax(0, 1fr);
  }

  .correlation-row {
    grid-template-columns: 16px minmax(84px, 1fr) 66px auto;
    gap: 6px;
  }

  .signal-list button {
    grid-template-columns: 18px minmax(0, 1fr) auto;
  }

  .signal-list button > :last-child:not(:nth-child(3)) {
    grid-column: 2 / -1;
    justify-self: end;
  }
}

@media (prefers-reduced-motion: reduce) {
  .spin {
    animation: none;
  }
}
</style>
