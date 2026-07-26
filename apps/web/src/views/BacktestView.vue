<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  Activity,
  BarChart3,
  Beaker,
  CircleCheck,
  CircleX,
  History,
  Play,
  RefreshCw,
  ShieldCheck,
  SlidersHorizontal,
  TriangleAlert,
} from 'lucide-vue-next'
import {
  api,
  type BacktestComparisonResponse,
  type BacktestDailyResponse,
  type BacktestReportResponse,
  type BacktestRunRecord,
  type BacktestRunRequest,
  type FactorClassification,
  type FactorDiagnosisResponse,
  type FactorEvaluationStatus,
  type FactorICWindow,
  type FactorICWindowResponse,
} from '../api'
import {
  CHART_COLORS,
  areaGradient,
  categoryAxis,
  glowLine,
  tooltipStyle,
  valueAxis,
} from '../chartTheme'
import EChart from '../components/EChart.vue'
import { fmtAmount, fmtNum, fmtPct, fmtTime, pctClass } from '../format'

const runs = ref<BacktestRunRecord[]>([])
const selectedRun = ref<BacktestRunRecord | null>(null)
const daily = ref<BacktestDailyResponse | null>(null)
const report = ref<BacktestReportResponse | null>(null)
const listLoading = ref(true)
const detailLoading = ref(false)
const starting = ref(false)
const error = ref('')
const activeSection = ref<'runs' | 'factors'>('runs')
const diagnosisMode = ref<'m3' | 'm2'>('m3')
const diagnosis = ref<FactorDiagnosisResponse | null>(null)
const factorWindows = ref<FactorICWindowResponse | null>(null)
const selectedFactorWindowKey = ref('')
const comparison = ref<BacktestComparisonResponse | null>(null)
const diagnosisLoading = ref(false)
const diagnosisError = ref('')
const comparisonError = ref('')
let pollTimer: number | undefined
let selectionToken = 0
let diagnosisRequestToken = 0

const form = reactive({
  startDate: '',
  endDate: '',
  rebalanceFreq: '5d' as '5d' | '10d' | '20d',
  topPct: 10,
  initialCapital: 1_000_000,
  commissionBps: 2.5,
  commissionMin: 5,
  stampDutyBps: 10,
  transferBps: 0.2,
  slippageBps: 5,
})

const GATE_LABELS: Record<string, { label: string; note: string }> = {
  positive_significant_ic: { label: 'IC 显著为正', note: 't ≥ 1.96' },
  positive_net_return: { label: '扣成本为正', note: '净收益 > 0' },
  beats_csi300: { label: '跑赢沪深300', note: '累计超额 > 0' },
  beats_equal_weight_market: { label: '跑赢等权市场', note: '累计超额 > 0' },
  top_layer_beats_bottom: { label: 'Top 胜 Bottom', note: 'G10 − G1 > 0' },
}

const FACTOR_LABELS: Record<string, string> = {
  momentum_20d: '20日动量',
  momentum_60d: '60日动量',
  volatility_20d: '20日波动',
  turnover_change_5d: '5日活跃度',
  net_inflow_5d: '5日净流入',
  roe: 'ROE',
  net_profit_yoy: '净利润同比',
  ocf_to_profit: '现金流/利润',
  debt_ratio: '负债率',
  revenue_yoy: '营收同比',
  pe_percentile: 'PE 分位',
  pb_percentile: 'PB 分位',
  sector_strength: '板块强度',
}

const CLASSIFICATION_LABELS: Record<
  FactorClassification,
  { label: string; tone: string }
> = {
  significant_positive: { label: '显著正向', tone: 'positive' },
  significant_reverse: { label: '显著反向', tone: 'negative' },
  ineffective: { label: '弱证据', tone: 'weak' },
  insufficient_data: { label: '样本不足', tone: 'missing' },
  history_excluded_pit_gap: { label: '历史排除', tone: 'live' },
}

const EVALUATION_LABELS: Record<
  FactorEvaluationStatus,
  { label: string; note: string; tone: string }
> = {
  measured: { label: '可测', note: '本窗已有有效截面', tone: 'positive' },
  evaluated_no_sample: { label: '已算 · n=0', note: '执行过但没有合格 PIT 截面', tone: 'weak' },
  not_evaluated: { label: '未评估', note: '本窗没有该因子的计算记录', tone: 'missing' },
  live_only: { label: '仅实时', note: '历史数据不可诚实回填', tone: 'live' },
  history_excluded_pit_gap: {
    label: '历史排除',
    note: '历史成分 PIT 不可重建；仅保留前向因子',
    tone: 'live',
  },
}

function factorWindowKey(window: Pick<FactorICWindow, 'start_date' | 'end_date'>): string {
  return `${window.start_date}|${window.end_date}`
}

const selectedFactorWindow = computed(() =>
  factorWindows.value?.windows.find(
    (window) => factorWindowKey(window) === selectedFactorWindowKey.value,
  ) ?? null,
)
const m3FactorWindows = computed(() =>
  factorWindows.value?.windows.filter(
    (window) => window.research_stage !== 'legacy_or_other',
  ) ?? [],
)
const isFormalM3 = computed(
  () =>
    diagnosis.value?.sample.research_stage === 'm3_s7_formal'
    || selectedFactorWindow.value?.research_stage === 'm3_s7_formal',
)
const m3RequestedCount = computed(() =>
  isFormalM3.value
    ? diagnosis.value?.sample.expected_factors.length
      ?? selectedFactorWindow.value?.expected_factors.length
      ?? 0
    : diagnosis.value?.coverage.preliminary_requested_count
      ?? selectedFactorWindow.value?.preliminary_requested_count
      ?? 0,
)
const m3MeasurableCount = computed(() =>
  isFormalM3.value
    ? diagnosis.value?.sample.available_count
      ?? selectedFactorWindow.value?.measurable_count
      ?? 0
    : diagnosis.value?.coverage.preliminary_measurable_count
      ?? selectedFactorWindow.value?.preliminary_measurable_count
      ?? 0,
)
const m3NoSampleCount = computed(() =>
  isFormalM3.value
    ? diagnosis.value?.factors.filter(
      (factor) => factor.evaluation_status === 'evaluated_no_sample',
    ).length ?? selectedFactorWindow.value?.evaluated_no_sample_count ?? 0
    : diagnosis.value?.coverage.preliminary_evaluated_no_sample_count
      ?? selectedFactorWindow.value?.preliminary_evaluated_no_sample_count
      ?? 0,
)

const diagnosisTabCount = computed(() => {
  if (diagnosisMode.value === 'm3') {
    return m3RequestedCount.value || null
  }
  return diagnosis.value?.sample.factor_count ?? null
})

function symmetricFactorIcExtent(values: Array<number | null>): number {
  const observed = Math.max(
    0,
    ...values
      .filter((value): value is number => value !== null && Number.isFinite(value))
      .map((value) => Math.abs(value)),
  )
  if (observed === 0) return 0.1
  const padded = observed * 1.15
  const step = padded <= 0.05
    ? 0.01
    : padded <= 0.1
      ? 0.02
      : padded <= 0.25
        ? 0.05
        : 0.1
  return Math.ceil(padded / step) * step
}

const gateEntries = computed(() => {
  const gates = report.value?.conclusion.gates ?? {}
  return Object.entries(GATE_LABELS).map(([key, meta]) => ({
    key,
    ...meta,
    passed: Boolean(gates[key]),
  }))
})

const selectedRunning = computed(() => selectedRun.value?.status === 'running')
const selectedFailed = computed(() => selectedRun.value?.status === 'failed')
const anyRunning = computed(() => runs.value.some((run) => run.status === 'running'))

const effectiveSeries = computed(() => {
  const source = daily.value
  const coverage = report.value?.coverage
  if (!source?.dates.length) {
    return {
      dates: [] as string[],
      nav: [] as number[],
      benchmark: [] as number[],
      market: [] as number[],
      rankIc: [] as Array<number | null>,
    }
  }
  const requestedStart = coverage?.effective_start_date
  const located = requestedStart ? source.dates.indexOf(requestedStart) : 0
  const start = located >= 0 ? located : 0
  const dates = source.dates.slice(start)
  const normalize = (values: number[]) => {
    const sliced = values.slice(start)
    const base = Number(sliced[0])
    if (!Number.isFinite(base) || base <= 0) return sliced.map(() => 0)
    return sliced.map((value) => Number((((value / base) - 1) * 100).toFixed(6)))
  }
  return {
    dates,
    nav: normalize(source.nav),
    benchmark: normalize(source.benchmark_nav),
    market: normalize(source.market_nav),
    rankIc: source.rank_ic.slice(start),
  }
})

const navOption = computed<Record<string, unknown>>(() => {
  const series = effectiveSeries.value
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      confine: true,
      valueFormatter: (value: number | null) =>
        value === null || value === undefined ? '暂不可用' : `${Number(value).toFixed(2)}%`,
      ...tooltipStyle,
    },
    legend: {
      top: 0,
      right: 2,
      itemWidth: 18,
      itemHeight: 2,
      textStyle: { color: CHART_COLORS.text3, fontSize: 10 },
      data: ['策略净值', '沪深300', '等权市场'],
    },
    grid: { left: 54, right: 18, top: 34, bottom: 34 },
    xAxis: categoryAxis(series.dates, {
      boundaryGap: false,
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 10,
        hideOverlap: true,
        formatter: (value: string) => value.slice(5),
      },
    }),
    yAxis: valueAxis({
      scale: true,
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 10,
        formatter: '{value}%',
      },
    }),
    series: [
      {
        name: '策略净值',
        type: 'line',
        data: series.nav,
        showSymbol: false,
        smooth: 0.08,
        lineStyle: glowLine(CHART_COLORS.cyan, 2),
        itemStyle: { color: CHART_COLORS.cyan },
        areaStyle: { color: areaGradient(CHART_COLORS.cyan, 0.11) },
      },
      {
        name: '沪深300',
        type: 'line',
        data: series.benchmark,
        showSymbol: false,
        smooth: 0.06,
        lineStyle: { color: CHART_COLORS.accentHi, width: 1.55 },
        itemStyle: { color: CHART_COLORS.accentHi },
      },
      {
        name: '等权市场',
        type: 'line',
        data: series.market,
        showSymbol: false,
        smooth: 0.06,
        lineStyle: { color: CHART_COLORS.slate, width: 1.3, type: 'dashed' },
        itemStyle: { color: CHART_COLORS.slate },
      },
    ],
  }
})

const icOption = computed<Record<string, unknown>>(() => {
  const series = effectiveSeries.value
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      confine: true,
      valueFormatter: (value: number | null) =>
        value === null || value === undefined ? '无有效截面' : Number(value).toFixed(4),
      ...tooltipStyle,
    },
    grid: { left: 48, right: 14, top: 16, bottom: 34 },
    xAxis: categoryAxis(series.dates, {
      boundaryGap: false,
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 10,
        hideOverlap: true,
        formatter: (value: string) => value.slice(5),
      },
    }),
    yAxis: valueAxis({
      min: -1,
      max: 1,
      axisLabel: { color: CHART_COLORS.text3, fontSize: 10 },
    }),
    series: [
      {
        name: 'Rank IC',
        type: 'line',
        data: series.rankIc,
        showSymbol: false,
        connectNulls: false,
        lineStyle: glowLine(CHART_COLORS.purple, 1.6),
        itemStyle: { color: CHART_COLORS.purple },
        markLine: {
          symbol: 'none',
          silent: true,
          label: { show: false },
          lineStyle: { color: CHART_COLORS.line2, type: 'dashed' },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  }
})

const layerOption = computed<Record<string, unknown>>(() => {
  const layers = report.value?.layers
  const values = (layers?.mean_daily_returns ?? []).map((value) =>
    value === null ? null : Number((value * 100).toFixed(6)),
  )
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: (value: number | null) =>
        value === null || value === undefined ? '暂不可用' : `${Number(value).toFixed(4)}%`,
      ...tooltipStyle,
    },
    grid: { left: 54, right: 16, top: 18, bottom: 34 },
    xAxis: categoryAxis(layers?.labels ?? []),
    yAxis: valueAxis({
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 10,
        formatter: '{value}%',
      },
    }),
    series: [
      {
        name: '平均日收益',
        type: 'bar',
        data: values.map((value, index) => ({
          value,
          itemStyle: {
            color: index === values.length - 1
              ? CHART_COLORS.cyan
              : index === 0
                ? CHART_COLORS.down
                : CHART_COLORS.accent,
            opacity: index === 0 || index === values.length - 1 ? 0.95 : 0.56,
            borderRadius: [3, 3, 0, 0],
          },
        })),
        barMaxWidth: 30,
      },
    ],
  }
})

const calibrationOption = computed<Record<string, unknown>>(() => {
  const calibration = report.value?.probability_calibration
  const curve = calibration?.curve ?? []
  return {
    animation: false,
    tooltip: { trigger: 'axis', confine: true, ...tooltipStyle },
    grid: { left: 48, right: 16, top: 18, bottom: 34 },
    xAxis: valueAxis({
      min: 0,
      max: 1,
      name: '预测概率',
      nameTextStyle: { color: CHART_COLORS.text3, fontSize: 10 },
    }),
    yAxis: valueAxis({
      min: 0,
      max: 1,
      name: '实际频率',
      nameTextStyle: { color: CHART_COLORS.text3, fontSize: 10 },
    }),
    series: [
      {
        name: '理想校准',
        type: 'line',
        data: [[0, 0], [1, 1]],
        symbol: 'none',
        lineStyle: { color: CHART_COLORS.line2, type: 'dashed' },
      },
      {
        name: '实际校准',
        type: 'line',
        data: curve
          .filter((item) => item.predicted_mean !== null && item.actual_rate !== null)
          .map((item) => [item.predicted_mean, item.actual_rate]),
        symbolSize: 7,
        lineStyle: glowLine(CHART_COLORS.warn, 1.8),
        itemStyle: { color: CHART_COLORS.warn },
      },
    ],
  }
})

const factorIcOption = computed<Record<string, unknown>>(() => {
  const factors = diagnosis.value?.factors ?? []
  const extent = symmetricFactorIcExtent(factors.map((item) => item.ic_mean))
  const values = factors.map((item) => ({
    value: item.ic_mean,
    factor: item.factor,
    tStat: item.t_stat,
    periods: item.n_periods,
    evaluationStatus: item.evaluation_status,
    itemStyle: {
      color: item.ic_mean === null
        ? CHART_COLORS.line2
        : item.ic_mean >= 0
          ? CHART_COLORS.up
          : CHART_COLORS.down,
      opacity: item.ic_mean === null ? 0.35 : 0.9,
    },
  }))
  return {
    animation: false,
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (params: any) => {
        const item = params.data
        if (item.value === null || item.value === undefined) {
          const state = EVALUATION_LABELS[item.evaluationStatus as FactorEvaluationStatus]
          return [
            FACTOR_LABELS[item.factor] ?? item.factor,
            `状态：${state?.label ?? '暂不可用'}`,
            state?.note ?? '没有可展示的 IC',
          ].join('<br/>')
        }
        return [
          FACTOR_LABELS[item.factor] ?? item.factor,
          `IC：${Number(item.value).toFixed(4)}`,
          `t：${item.tStat === null ? '暂不可用' : Number(item.tStat).toFixed(3)}`,
          `截面：${item.periods}`,
        ].join('<br/>')
      },
      ...tooltipStyle,
    },
    grid: { left: 104, right: 24, top: 14, bottom: 30 },
    xAxis: valueAxis({
      min: -extent,
      max: extent,
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 11,
        formatter: (value: number) => value.toFixed(2),
      },
    }),
    yAxis: categoryAxis(
      factors.map((item) => FACTOR_LABELS[item.factor] ?? item.factor),
      {
        inverse: true,
        axisLabel: {
          color: CHART_COLORS.text2,
          fontSize: 11,
          width: 88,
          overflow: 'truncate',
        },
      },
    ),
    series: [
      {
        name: diagnosisMode.value === 'm3' ? 'train 窗 Rank IC' : 'M2 full 窗 Rank IC',
        type: 'bar',
        data: values,
        barMaxWidth: 13,
        markLine: {
          silent: true,
          symbol: 'none',
          label: { show: false },
          lineStyle: { color: CHART_COLORS.line2 },
          data: [{ xAxis: 0 }],
        },
      },
    ],
  }
})

const correlationOption = computed<Record<string, unknown>>(() => {
  const correlation = diagnosis.value?.correlation
  const factors = correlation?.factors ?? []
  const cells: Array<[number, number, number]> = []
  correlation?.values.forEach((row, y) => {
    row.forEach((value, x) => {
      if (value !== null) cells.push([x, y, value])
    })
  })
  const labels = factors.map((factor) => FACTOR_LABELS[factor] ?? factor)
  return {
    animation: false,
    tooltip: {
      confine: true,
      formatter: (params: any) => {
        const [x, y, value] = params.data as [number, number, number]
        const periods = correlation?.n_periods[y]?.[x] ?? 0
        return [
          `${labels[y]} × ${labels[x]}`,
          `相关：${Number(value).toFixed(4)}`,
          `有效截面：${periods}`,
        ].join('<br/>')
      },
      ...tooltipStyle,
    },
    grid: { left: 106, right: 86, top: 20, bottom: 88 },
    xAxis: categoryAxis(labels, {
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 11,
        rotate: 48,
        interval: 0,
      },
    }),
    yAxis: categoryAxis(labels, {
      inverse: true,
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 11,
        width: 94,
        overflow: 'truncate',
      },
    }),
    visualMap: {
      min: -1,
      max: 1,
      calculable: false,
      orient: 'vertical',
      right: 2,
      top: 'center',
      itemHeight: 130,
      text: ['+1', '-1'],
      textStyle: { color: CHART_COLORS.text3, fontSize: 11 },
      inRange: {
        color: ['#7f1d1d', '#182137', '#065f46'],
      },
    },
    series: [
      {
        type: 'heatmap',
        data: cells,
        label: {
          show: true,
          color: '#dbe5f6',
          fontSize: 11,
          formatter: (params: any) => Number(params.data[2]).toFixed(2),
        },
        itemStyle: {
          borderColor: 'rgba(148,163,198,0.12)',
          borderWidth: 1,
        },
        emphasis: {
          itemStyle: {
            borderColor: CHART_COLORS.accentHi,
            borderWidth: 1,
          },
        },
      },
    ],
  }
})

const factorWeightsOption = computed<Record<string, unknown>>(() => {
  const weights = diagnosis.value?.weights
  const factors = weights?.factors ?? []
  const labels = factors.map((factor) => FACTOR_LABELS[factor] ?? factor)
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: (value: number | null) =>
        value === null || value === undefined ? '未赋权' : Number(value).toFixed(4),
      ...tooltipStyle,
    },
    legend: {
      top: 0,
      right: 2,
      itemWidth: 14,
      itemHeight: 3,
      textStyle: { color: CHART_COLORS.text3, fontSize: 10 },
      data: ['v1 静态', 'v2 train IC_IR'],
    },
    grid: { left: 104, right: 24, top: 34, bottom: 30 },
    xAxis: valueAxis({
      min: -0.4,
      max: 0.25,
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 10,
        formatter: (value: number) => value.toFixed(1),
      },
    }),
    yAxis: categoryAxis(labels, {
      inverse: true,
      axisLabel: {
        color: CHART_COLORS.text2,
        fontSize: 10,
        width: 88,
        overflow: 'truncate',
      },
    }),
    series: [
      {
        name: 'v1 静态',
        type: 'bar',
        data: factors.map((factor) => weights?.v1.weights[factor] ?? 0),
        itemStyle: { color: CHART_COLORS.slate, opacity: 0.72 },
        barMaxWidth: 8,
      },
      {
        name: 'v2 train IC_IR',
        type: 'bar',
        data: factors.map((factor) => weights?.v2.weights[factor] ?? 0),
        itemStyle: { color: CHART_COLORS.purple, opacity: 0.9 },
        barMaxWidth: 8,
      },
    ],
  }
})

function normalizedReturns(values: number[]): number[] {
  const base = Number(values[0])
  if (!Number.isFinite(base) || base <= 0) return values.map(() => 0)
  return values.map((value) => Number((((value / base) - 1) * 100).toFixed(6)))
}

const comparisonNavOption = computed<Record<string, unknown>>(() => {
  const curve = comparison.value?.curve
  const dates = curve?.dates ?? []
  const series = [
    {
      name: 'v2 重构',
      data: normalizedReturns(curve?.v2_nav ?? []),
      color: CHART_COLORS.purple,
      width: 2,
      type: 'solid',
    },
    {
      name: 'v1 基线',
      data: normalizedReturns(curve?.v1_nav ?? []),
      color: CHART_COLORS.slate,
      width: 1.4,
      type: 'dashed',
    },
    {
      name: '沪深300',
      data: normalizedReturns(curve?.csi300_nav ?? []),
      color: CHART_COLORS.accentHi,
      width: 1.3,
      type: 'solid',
    },
    {
      name: '等权市场',
      data: normalizedReturns(curve?.market_nav ?? []),
      color: CHART_COLORS.cyan,
      width: 1.3,
      type: 'dashed',
    },
  ]
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      confine: true,
      valueFormatter: (value: number | null) =>
        value === null || value === undefined ? '暂不可用' : `${Number(value).toFixed(2)}%`,
      ...tooltipStyle,
    },
    legend: {
      top: 0,
      right: 2,
      itemWidth: 17,
      itemHeight: 2,
      textStyle: { color: CHART_COLORS.text3, fontSize: 10 },
      data: series.map((item) => item.name),
    },
    grid: { left: 52, right: 18, top: 34, bottom: 34 },
    xAxis: categoryAxis(dates, {
      boundaryGap: false,
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 10,
        hideOverlap: true,
        formatter: (value: string) => value.slice(5),
      },
    }),
    yAxis: valueAxis({
      scale: true,
      axisLabel: {
        color: CHART_COLORS.text3,
        fontSize: 10,
        formatter: '{value}%',
      },
    }),
    series: series.map((item) => ({
      name: item.name,
      type: 'line',
      data: item.data,
      showSymbol: false,
      lineStyle: {
        color: item.color,
        width: item.width,
        type: item.type,
      },
      itemStyle: { color: item.color },
    })),
  }
})

function errorMessage(value: unknown): string {
  return value instanceof Error ? value.message : String(value)
}

function statusLabel(run: BacktestRunRecord | null): string {
  if (!run) return '未选择'
  if (run.status === 'completed') return '已完成'
  if (run.status === 'failed') return '失败'
  return '计算中'
}

function statusClass(run: BacktestRunRecord | null): string {
  if (!run) return 'gray'
  if (run.status === 'completed') return 'green'
  if (run.status === 'failed') return 'red'
  return 'yellow'
}

function syncForm(run: BacktestRunRecord) {
  form.startDate = run.start_date
  form.endDate = run.end_date
  if (['5d', '10d', '20d'].includes(run.rebalance_freq)) {
    form.rebalanceFreq = run.rebalance_freq as '5d' | '10d' | '20d'
  }
  form.topPct = Number((run.top_pct * 100).toFixed(2))
  const params = run.params ?? {}
  const cost = params.cost_model ?? {}
  form.initialCapital = Number(params.initial_capital ?? form.initialCapital)
  form.commissionBps = Number(cost.commission_bps ?? form.commissionBps)
  form.commissionMin = Number(cost.commission_min ?? form.commissionMin)
  form.stampDutyBps = Number(cost.stamp_duty_bps ?? form.stampDutyBps)
  form.transferBps = Number(cost.transfer_bps ?? form.transferBps)
  form.slippageBps = Number(cost.slippage_bps ?? form.slippageBps)
}

function updateRun(run: BacktestRunRecord) {
  const index = runs.value.findIndex((item) => item.id === run.id)
  if (index >= 0) runs.value[index] = run
  else runs.value.unshift(run)
}

function strongestCompletedRun(items: BacktestRunRecord[]): BacktestRunRecord | null {
  return items.reduce<BacktestRunRecord | null>((best, run) => {
    if (run.status !== 'completed') return best
    if (!best) return run
    const tradingDays = Number(run.summary.trading_days ?? 0)
    const bestTradingDays = Number(best.summary.trading_days ?? 0)
    if (tradingDays > bestTradingDays) return run
    if (tradingDays === bestTradingDays && run.id > best.id) return run
    return best
  }, null)
}

function clearPoll() {
  if (pollTimer !== undefined) {
    window.clearTimeout(pollTimer)
    pollTimer = undefined
  }
}

function schedulePoll() {
  clearPoll()
  const running = runs.value.find((run) => run.status === 'running')
  if (!running) return
  pollTimer = window.setTimeout(async () => {
    const id = running.id
    try {
      const detail = await api.backtest(id)
      updateRun(detail.run)
      if (selectedRun.value?.id === id) {
        selectedRun.value = detail.run
      }
      if (
        selectedRun.value?.id === id
        && (detail.run.status === 'completed' || detail.run.status === 'failed')
      ) {
        await selectRun(id, false)
        return
      }
    } catch (exc: unknown) {
      error.value = `状态轮询失败：${errorMessage(exc)}`
    }
    schedulePoll()
  }, 3_000)
}

async function selectRun(id: number, updateForm = true) {
  clearPoll()
  const token = ++selectionToken
  detailLoading.value = true
  error.value = ''
  report.value = null
  daily.value = null
  try {
    const [detailResult, dailyResult] = await Promise.all([
      api.backtest(id),
      api.backtestDaily(id),
    ])
    if (token !== selectionToken) return
    selectedRun.value = detailResult.run
    daily.value = dailyResult
    updateRun(detailResult.run)
    if (updateForm) syncForm(detailResult.run)
    if (detailResult.run.status === 'completed') {
      report.value = await api.backtestReport(id)
    }
  } catch (exc: unknown) {
    if (token === selectionToken) {
      error.value = `回测结果暂不可用：${errorMessage(exc)}`
    }
  } finally {
    if (token === selectionToken) {
      detailLoading.value = false
      schedulePoll()
    }
  }
}

async function loadRuns() {
  listLoading.value = true
  error.value = ''
  try {
    const response = await api.backtests(50)
    runs.value = response.runs
    const current = selectedRun.value
      ? response.runs.find((run) => run.id === selectedRun.value?.id)
      : null
    const preferred = current
      ?? strongestCompletedRun(response.runs)
      ?? response.runs[0]
    if (preferred) await selectRun(preferred.id)
  } catch (exc: unknown) {
    error.value = `回测列表暂不可用：${errorMessage(exc)}`
  } finally {
    listLoading.value = false
  }
}

function comparisonPair(
  items: BacktestRunRecord[],
): { v1: number; v2: number } | null {
  const completed = items.filter((run) => run.status === 'completed')
  const v2Runs = completed
    .filter((run) => run.signal_id === 'composite-v2')
    .sort((left, right) => right.id - left.id)
  for (const v2 of v2Runs) {
    const v1 = completed.find((candidate) =>
      candidate.signal_id === 'composite-v1'
      && candidate.start_date === v2.start_date
      && candidate.end_date === v2.end_date
      && candidate.rebalance_freq === v2.rebalance_freq
      && candidate.top_pct === v2.top_pct
      && candidate.params.initial_capital === v2.params.initial_capital
      && JSON.stringify(candidate.params.cost_model) === JSON.stringify(v2.params.cost_model),
    )
    if (v1) return { v1: v1.id, v2: v2.id }
  }
  return null
}

async function loadDiagnosis(force = false) {
  if (diagnosis.value && !force) return
  const token = ++diagnosisRequestToken
  const requestedMode = diagnosisMode.value
  const isCurrent = () =>
    token === diagnosisRequestToken && diagnosisMode.value === requestedMode
  diagnosisLoading.value = true
  diagnosisError.value = ''
  comparisonError.value = ''
  if (isCurrent()) comparison.value = null
  try {
    if (requestedMode === 'm3') {
      const catalog = force || !factorWindows.value
        ? await api.factorICWindows('train')
        : factorWindows.value
      if (!isCurrent()) return
      factorWindows.value = catalog
      const selected = catalog.windows.find(
        (window) =>
          factorWindowKey(window) === selectedFactorWindowKey.value
          && window.research_stage !== 'legacy_or_other',
      ) ?? catalog.default_window
      if (!selected) {
        if (!isCurrent()) return
        diagnosis.value = null
        diagnosisError.value = '尚无 M3 train 因子窗口；test 仍保持封存。'
        return
      }
      selectedFactorWindowKey.value = factorWindowKey(selected)
      const result = await api.factorDiagnosis('train', {
        startDate: selected.start_date,
        endDate: selected.end_date,
      })
      if (!isCurrent()) return
      if (
        !result.available
        || !result.sample.selection.exact_window
        || result.sample.start_date !== selected.start_date
        || result.sample.end_date !== selected.end_date
      ) {
        throw new Error(
          `精确窗口校验失败：请求 ${selected.start_date} → ${selected.end_date}，拒绝展示错窗结果。`,
        )
      }
      diagnosis.value = result
      return
    }

    const result = await api.factorDiagnosis('full')
    if (!isCurrent()) return
    diagnosis.value = result
    try {
      const refreshedRuns = (await api.backtests(50)).runs
      if (!isCurrent()) return
      runs.value = refreshedRuns
      const pair = comparisonPair(refreshedRuns)
      if (!pair) return
      const comparisonResult = await api.backtestCompare(pair.v1, pair.v2)
      if (!isCurrent()) return
      comparison.value = comparisonResult
    } catch (exc: unknown) {
      if (!isCurrent()) return
      comparison.value = null
      comparisonError.value = `样本外对照暂不可用：${errorMessage(exc)}`
    }
  } catch (exc: unknown) {
    if (!isCurrent()) return
    diagnosis.value = null
    comparison.value = null
    diagnosisError.value = `因子诊断暂不可用：${errorMessage(exc)}`
  } finally {
    if (isCurrent()) diagnosisLoading.value = false
  }
}

async function switchDiagnosisMode(mode: 'm3' | 'm2') {
  if (mode === diagnosisMode.value && diagnosis.value) return
  diagnosisMode.value = mode
  diagnosis.value = null
  comparison.value = null
  diagnosisError.value = ''
  comparisonError.value = ''
  await loadDiagnosis(true)
}

async function selectFactorWindow(event: Event) {
  selectedFactorWindowKey.value = (event.target as HTMLSelectElement).value
  diagnosis.value = null
  comparison.value = null
  comparisonError.value = ''
  await loadDiagnosis()
}

async function switchSection(section: 'runs' | 'factors') {
  activeSection.value = section
  if (section === 'factors') {
    clearPoll()
    await loadDiagnosis()
    return
  }
  schedulePoll()
}

async function refreshActiveSection() {
  if (activeSection.value === 'factors') {
    await loadDiagnosis(true)
    return
  }
  await loadRuns()
}

async function startBacktest() {
  starting.value = true
  error.value = ''
  const payload: BacktestRunRequest = {
    signal_id: 'composite-v1',
    start_date: form.startDate || null,
    end_date: form.endDate || null,
    rebalance_freq: form.rebalanceFreq,
    top_pct: Number(form.topPct) / 100,
    initial_capital: Number(form.initialCapital),
    cost_model: {
      commission_bps: Number(form.commissionBps),
      commission_min: Number(form.commissionMin),
      stamp_duty_bps: Number(form.stampDutyBps),
      transfer_bps: Number(form.transferBps),
      slippage_bps: Number(form.slippageBps),
    },
  }
  try {
    const response = await api.startBacktest(payload)
    updateRun(response.run)
    await selectRun(response.run.id, false)
  } catch (exc: unknown) {
    error.value = `回测未启动：${errorMessage(exc)}`
  } finally {
    starting.value = false
  }
}

onMounted(loadRuns)
onUnmounted(() => {
  diagnosisRequestToken += 1
  clearPoll()
})
</script>

<template>
  <div class="backtest-page">
    <header class="page-head research-head">
      <div>
        <h1>策略回测 / 研究</h1>
        <span class="sub">
          {{ activeSection === 'runs'
            ? 'PIT 信号 → T+1 成交 → 全成本 → 双基准 → 诚实结论'
            : '因子 IC → 方向审计 → train 定权 → test 样本外裁定' }}
        </span>
      </div>
      <div class="head-actions">
        <span
          v-if="activeSection === 'runs'"
          class="badge"
          :class="statusClass(selectedRun)"
        >
          <span class="status-pulse" :class="{ live: selectedRunning }" />
          {{ statusLabel(selectedRun) }}
        </span>
        <span
          v-else
          class="badge"
          :class="diagnosisMode === 'm3'
            ? 'blue'
            : comparison?.verdict.status === 'failed' ? 'red' : 'yellow'"
        >
          {{ diagnosisMode === 'm3'
            ? 'M3 TRAIN · TEST 封存'
            : comparison?.verdict.status === 'failed' ? 'M2 未通过' : 'M2 历史证据' }}
        </span>
        <span
          v-if="activeSection === 'runs' && selectedRun"
          class="run-ref mono"
        >
          RUN #{{ selectedRun.id }}
        </span>
        <span v-else-if="activeSection === 'factors'" class="run-ref mono">
          {{ diagnosisMode === 'm3'
            ? selectedFactorWindow
              ? `${selectedFactorWindow.start_date} → ${selectedFactorWindow.end_date} · JOB #${selectedFactorWindow.research_run_id}`
              : 'TRAIN SNAPSHOT'
            : 'M2 FULL SNAPSHOT' }}
        </span>
        <button
          class="btn"
          :disabled="listLoading || detailLoading || diagnosisLoading"
          @click="refreshActiveSection"
        >
          <RefreshCw
            :size="12"
            :class="{ spin: listLoading || detailLoading || diagnosisLoading }"
          />
          刷新证据
        </button>
      </div>
    </header>

    <nav class="research-tabs" aria-label="回测研究视图">
      <button
        :aria-pressed="activeSection === 'runs'"
        :class="{ active: activeSection === 'runs' }"
        @click="switchSection('runs')"
      >
        严格回测
      </button>
      <button
        :aria-pressed="activeSection === 'factors'"
        :class="{ active: activeSection === 'factors' }"
        @click="switchSection('factors')"
      >
        因子诊断
        <span v-if="diagnosisTabCount !== null" class="tab-count mono">
          {{ diagnosisTabCount }}
        </span>
      </button>
    </nav>

    <div
      v-if="activeSection === 'runs' && error"
      class="banner error page-message"
      role="alert"
    >
      <TriangleAlert :size="14" />
      {{ error }}
    </div>

    <div
      v-if="activeSection === 'factors' && diagnosisError"
      class="banner error page-message"
      role="alert"
    >
      <TriangleAlert :size="14" />
      {{ diagnosisError }}
    </div>

    <div
      v-if="activeSection === 'factors' && comparisonError"
      class="banner page-message"
      role="alert"
    >
      <TriangleAlert :size="14" />
      {{ comparisonError }}；因子诊断证据仍保留。
    </div>

    <template v-if="activeSection === 'runs'">
    <div class="research-top-grid">
      <section class="panel config-panel">
        <div class="panel-title">
          <span><SlidersHorizontal :size="13" /> 回测协议</span>
          <span class="extra">固定信号 · 无随机</span>
        </div>
        <div class="protocol-mark">
          <ShieldCheck :size="15" />
          <span>只读研究</span>
          <small>不连接提案、订单或交易执行</small>
        </div>
        <div class="field-grid">
          <label class="field full">
            <span>信号</span>
            <select class="input" disabled>
              <option>composite-v1 · 静态权重</option>
            </select>
          </label>
          <label class="field date-field">
            <span>开始日期</span>
            <input v-model="form.startDate" class="input mono" type="date" />
          </label>
          <label class="field date-field">
            <span>结束日期</span>
            <input v-model="form.endDate" class="input mono" type="date" />
          </label>
          <label class="field">
            <span>调仓频率</span>
            <select v-model="form.rebalanceFreq" class="input">
              <option value="5d">每 5 个交易日</option>
              <option value="10d">每 10 个交易日</option>
              <option value="20d">每 20 个交易日</option>
            </select>
          </label>
          <label class="field">
            <span>多头比例</span>
            <span class="suffix-input">
              <input v-model.number="form.topPct" class="input mono" type="number" min="1" max="100" step="1" />
              <i>%</i>
            </span>
          </label>
          <label class="field capital-field">
            <span>初始资金</span>
            <input v-model.number="form.initialCapital" class="input mono" type="number" min="10000" step="10000" />
          </label>
        </div>
        <details class="cost-protocol" open>
          <summary>全成本模型 <span class="mono">5 项</span></summary>
          <div class="cost-grid">
            <label><span>佣金 bps</span><input v-model.number="form.commissionBps" class="input mono" type="number" min="0" step="0.1" /></label>
            <label><span>最低佣金</span><input v-model.number="form.commissionMin" class="input mono" type="number" min="0" step="1" /></label>
            <label><span>印花税 bps</span><input v-model.number="form.stampDutyBps" class="input mono" type="number" min="0" step="0.1" /></label>
            <label><span>过户费 bps</span><input v-model.number="form.transferBps" class="input mono" type="number" min="0" step="0.1" /></label>
            <label><span>滑点 bps</span><input v-model.number="form.slippageBps" class="input mono" type="number" min="0" step="0.1" /></label>
          </div>
        </details>
        <button
          class="btn primary run-button"
          :disabled="starting || anyRunning"
          @click="startBacktest"
        >
          <RefreshCw v-if="starting" :size="13" class="spin" />
          <Play v-else :size="13" fill="currentColor" />
          {{ starting ? '正在创建审计任务…' : anyRunning ? '已有回测计算中' : '启动严格回测' }}
        </button>
        <p class="run-hint">全市场完整区间通常需 20–40 分钟（取决于本机负载）；关闭页面不会改变参数。</p>
      </section>

      <section
        class="verdict-panel"
        :class="{
          positive: report?.conclusion.alpha_supported,
          negative: report && !report.conclusion.alpha_supported,
          pending: selectedRunning,
        }"
      >
        <div v-if="report" class="verdict-content">
          <div class="verdict-kicker">
            <Beaker :size="14" />
            ALPHA EVIDENCE GATE
            <span class="mono">{{ report.coverage.effective_trading_days }}D</span>
          </div>
          <h2>{{ report.conclusion.headline }}</h2>
          <p>{{ report.conclusion.policy }}</p>
          <div class="gate-tape" aria-label="Alpha 结论门">
            <article
              v-for="(gate, gateIndex) in gateEntries"
              :key="gate.key"
              :class="{ pass: gate.passed }"
            >
              <span class="gate-index mono">{{ String(gateIndex + 1).padStart(2, '0') }}</span>
              <CircleCheck v-if="gate.passed" :size="15" />
              <CircleX v-else :size="15" />
              <b>{{ gate.label }}</b>
              <small>{{ gate.note }}</small>
            </article>
          </div>
          <section class="metric-deck verdict-metrics" aria-label="回测核心指标">
            <article>
              <span>Rank IC 均值</span>
              <strong class="num" :class="pctClass(report.rank_ic.mean)">{{ fmtNum(report.rank_ic.mean, 4) }}</strong>
              <small>t = {{ fmtNum(report.rank_ic.t_stat, 3) }} · {{ report.rank_ic.samples }} 日</small>
            </article>
            <article>
              <span>IC_IR</span>
              <strong class="num" :class="pctClass(report.rank_ic.ic_ir)">{{ fmtNum(report.rank_ic.ic_ir, 3) }}</strong>
              <small>正 IC 占比 {{ fmtPct(report.rank_ic.positive_ratio, 1, false) }}</small>
            </article>
            <article>
              <span>净年化收益</span>
              <strong class="num" :class="pctClass(report.net_long_performance.ann_return)">
                {{ fmtPct(report.net_long_performance.ann_return, 2, false) }}
              </strong>
              <small>累计 {{ fmtPct(report.net_long_performance.total_return, 2, false) }}</small>
            </article>
            <article>
              <span>夏普比率</span>
              <strong class="num" :class="pctClass(report.net_long_performance.sharpe)">{{ fmtNum(report.net_long_performance.sharpe, 3) }}</strong>
              <small>年化波动 {{ fmtPct(report.net_long_performance.ann_volatility, 2, false) }}</small>
            </article>
            <article>
              <span>最大回撤</span>
              <strong class="num down">{{ fmtPct(report.net_long_performance.max_drawdown, 2, false) }}</strong>
              <small>Calmar {{ fmtNum(report.net_long_performance.calmar, 3) }}</small>
            </article>
            <article>
              <span>相对沪深300</span>
              <strong class="num" :class="pctClass(report.benchmarks.excess_total_return.vs_csi300)">
                {{ fmtPct(report.benchmarks.excess_total_return.vs_csi300, 2, false) }}
              </strong>
              <small>等权超额 {{ fmtPct(report.benchmarks.excess_total_return.vs_equal_weight_market, 2, false) }}</small>
            </article>
            <article>
              <span>年化换手</span>
              <strong class="num">{{ fmtPct(report.turnover.annualized, 1, false) }}</strong>
              <small>{{ report.turnover.rebalance_days }} 次调仓 · 单次 {{ fmtPct(report.turnover.mean_rebalance, 1, false) }}</small>
            </article>
            <article>
              <span>成本 / 初始资金</span>
              <strong class="num" :class="pctClass(report.costs.to_initial_capital ? -report.costs.to_initial_capital : null)">
                {{ fmtNum(Number(report.costs.to_initial_capital) * 100, 2) }}%
              </strong>
              <small>{{ fmtAmount(report.costs.total) }} · {{ fmtNum(report.costs.bps_of_traded_notional, 2) }} bps</small>
            </article>
          </section>
          <div class="verdict-foot">
            <span>有效区间 <b class="mono">{{ report.coverage.effective_start_date }}</b> → <b class="mono">{{ report.coverage.requested_last_trade_date }}</b></span>
            <span>预热排除 <b class="mono">{{ report.coverage.warmup_days_excluded_from_performance }}</b> 日</span>
            <span>生成于 <b class="mono">{{ fmtTime(report.generated_at) }}</b></span>
          </div>
        </div>
        <div v-else-if="selectedRunning" class="verdict-state">
          <span class="orbit"><Activity :size="22" /></span>
          <h2>正在逐日重放 PIT 信号</h2>
          <p>只使用当时可见数据，T 日决策、T+1 开盘成交。完成后自动刷新证据门。</p>
          <div class="progress-ruler"><i /></div>
        </div>
        <div v-else-if="selectedFailed" class="verdict-state failed-state">
          <CircleX :size="24" />
          <h2>本次回测失败</h2>
          <p>{{ selectedRun?.error || '未提供失败原因；请检查数据覆盖后重新运行。' }}</p>
        </div>
        <div v-else class="verdict-state">
          <Beaker :size="24" />
          <h2>选择一条已完成回测</h2>
          <p>证据门只展示真实报告；无结果时不会填充示例收益或默认结论。</p>
        </div>
      </section>
    </div>
    <div class="chart-grid">
      <section class="panel nav-panel">
        <div class="panel-title">
          <span><Activity :size="13" /> 策略净值 vs 双基准</span>
          <span class="extra">从首笔成交前一日统一归零</span>
        </div>
        <EChart
          v-if="report && effectiveSeries.dates.length"
          :option="navOption"
          height="318px"
          :aria-label="`${report.coverage.effective_start_date}起策略、沪深300与等权市场累计收益`"
        />
        <div v-else class="chart-empty">
          <BarChart3 :size="22" />
          <span>{{ selectedRunning ? '回测完成后生成净值序列。' : '暂无可展示的净值数据。' }}</span>
        </div>
      </section>

      <section class="panel ic-panel">
        <div class="panel-title">
          <span>Rank IC 时序</span>
          <span class="extra">Spearman · 日频</span>
        </div>
        <EChart
          v-if="report && effectiveSeries.rankIc.length"
          :option="icOption"
          height="318px"
          :aria-label="`Rank IC 均值 ${fmtNum(report.rank_ic.mean, 4)}`"
        />
        <div v-else class="chart-empty"><span>等待有效信号截面。</span></div>
      </section>
    </div>

    <div class="analysis-grid">
      <section class="panel layer-panel">
        <div class="panel-title">
          <span>10 分层收益</span>
          <span class="extra">
            {{ report?.layers.strictly_monotonic ? '严格单调' : '非严格单调' }}
          </span>
        </div>
        <EChart
          v-if="report?.layers.mean_daily_returns.length"
          :option="layerOption"
          height="260px"
          :aria-label="`G10 减 G1 为 ${fmtPct(report.layers.top_minus_bottom, 4, false)}`"
        />
        <div v-else class="chart-empty"><span>暂无分层收益。</span></div>
        <div v-if="report" class="chart-caption">
          <span>G10 − G1 <b class="num" :class="pctClass(report.layers.top_minus_bottom)">{{ fmtPct(report.layers.top_minus_bottom, 4, false) }}</b></span>
          <span>分层秩相关 <b class="num">{{ fmtNum(report.layers.monotonic_rank_ic, 3) }}</b></span>
          <span>每组样本 <b class="num">{{ report.layers.observations[0] ?? 0 }}</b> 日</span>
        </div>
      </section>

      <section class="panel calibration-panel">
        <div class="panel-title">
          <span>概率校准</span>
          <span class="extra">Brier + 可靠性曲线</span>
        </div>
        <EChart
          v-if="report?.probability_calibration.available"
          :option="calibrationOption"
          height="220px"
          aria-label="预测概率可靠性曲线"
        />
        <div v-else class="calibration-unavailable">
          <span class="calibration-glyph mono">P ≠ S</span>
          <div>
            <b>本信号不具备概率语义</b>
            <p>{{ report?.probability_calibration.reason || '完成报告后判断校准能力。' }}</p>
          </div>
        </div>
        <div v-if="report?.long_short_gross_diagnostic.available" class="gross-diagnostic">
          <TriangleAlert :size="13" />
          <div>
            <b>{{ report.long_short_gross_diagnostic.label }}</b>
            <span>
              累计 {{ fmtPct(report.long_short_gross_diagnostic.metrics?.total_return, 2, false) }}；
              未扣融券成本，不可视为可交易净收益。
            </span>
          </div>
        </div>
      </section>
    </div>

    <div class="evidence-grid">
      <section class="panel limitations-panel">
        <div class="panel-title">
          <span><TriangleAlert :size="13" /> 结论边界</span>
          <span class="extra">{{ report?.limitations.length ?? 0 }} 项显式局限</span>
        </div>
        <div v-if="report?.limitations.length" class="limitation-list">
          <article v-for="item in report.limitations" :key="item.code">
            <span class="severity" :class="item.severity">{{ item.severity === 'high' ? 'HIGH' : item.severity === 'medium' ? 'MED' : 'LOW' }}</span>
            <div>
              <b class="mono">{{ item.code }}</b>
              <p>{{ item.text }}</p>
            </div>
          </article>
        </div>
        <div v-else class="empty-hint">选择已完成回测后展示局限，绝不隐藏不利条件。</div>
      </section>

      <section class="panel audit-panel">
        <div class="panel-title">
          <span><ShieldCheck :size="13" /> 可复现审计</span>
          <span class="extra">参数快照</span>
        </div>
        <template v-if="selectedRun">
          <div class="audit-row"><span>决策 / 成交</span><b>T 收盘 → T+1 开盘</b></div>
          <div class="audit-row"><span>复权 / 来源</span><b>审计日线 + adj factor</b></div>
          <div class="audit-row"><span>调仓 / Top</span><b class="mono">{{ selectedRun.rebalance_freq }} · {{ fmtNum(selectedRun.top_pct * 100, 0) }}%</b></div>
          <div class="audit-row"><span>回测区间</span><b class="mono">{{ selectedRun.start_date }} → {{ selectedRun.end_date }}</b></div>
          <div class="audit-row"><span>权重版本</span><b class="mono">{{ selectedRun.params.weight_version || '—' }}</b></div>
          <div class="audit-row"><span>随机种子</span><b>{{ selectedRun.params.random_seed === null ? '无随机' : selectedRun.params.random_seed }}</b></div>
          <div class="audit-row"><span>幸存者偏差</span><b class="down">存在 · 已披露</b></div>
        </template>
        <div v-else class="empty-hint">暂无参数快照。</div>
      </section>
    </div>

    <section class="panel history-panel">
      <div class="panel-title">
        <span><History :size="13" /> 回测档案</span>
        <span class="extra">最近 {{ runs.length }} 条 · 点击切换证据</span>
      </div>
      <div v-if="runs.length" class="history-scroll">
        <table class="tbl history-table">
          <thead>
            <tr>
              <th>Run</th><th>状态</th><th>信号 / 参数</th><th>区间</th>
              <th class="r">最终净值</th><th class="r">成本</th><th>创建时间</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="run in runs"
              :key="run.id"
              :class="{ selected: selectedRun?.id === run.id }"
              @click="selectRun(run.id)"
            >
              <td class="mono">#{{ run.id }}</td>
              <td><span class="badge" :class="statusClass(run)">{{ statusLabel(run) }}</span></td>
              <td><b>{{ run.signal_id }}</b><small>{{ run.rebalance_freq }} · Top {{ fmtNum(run.top_pct * 100, 0) }}%</small></td>
              <td class="mono xs">{{ run.start_date }} → {{ run.end_date }}</td>
              <td class="r num" :class="pctClass(Number(run.summary.final_nav || 0) - 1)">
                {{ run.status === 'completed' ? fmtNum(run.summary.final_nav, 4) : '—' }}
              </td>
              <td class="r num">{{ run.status === 'completed' ? fmtAmount(run.summary.total_cost) : '—' }}</td>
              <td class="xs dim">{{ fmtTime(run.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-hint">
        {{ listLoading ? '正在读取回测档案…' : '还没有回测记录；先运行一组严格基线。' }}
      </div>
    </section>

    <p class="research-disclaimer">
      回测用于研究验证，不构成收益承诺。负面结论不会触发自动调参，也不会连接任何交易执行路径。
    </p>
    </template>

    <template v-else>
      <section class="research-mode-bar" aria-label="因子证据模式">
        <div class="mode-tabs" role="group" aria-label="选择因子研究阶段">
          <button
            :aria-pressed="diagnosisMode === 'm3'"
            :class="{ active: diagnosisMode === 'm3' }"
            @click="switchDiagnosisMode('m3')"
          >
            {{ isFormalM3 ? 'M3 S7 正式' : 'M3 train 预验' }}
            <span>{{ isFormalM3 ? '精确 train · 正式 lineage' : '默认 · test 封存' }}</span>
          </button>
          <button
            :aria-pressed="diagnosisMode === 'm2'"
            :class="{ active: diagnosisMode === 'm2' }"
            @click="switchDiagnosisMode('m2')"
          >
            M2 历史证据
            <span>主动查看 full / test</span>
          </button>
        </div>
        <label v-if="diagnosisMode === 'm3' && m3FactorWindows.length" class="window-picker">
          <span>精确 train 窗</span>
          <select
            class="input"
            :value="selectedFactorWindowKey"
            :disabled="diagnosisLoading"
            @change="selectFactorWindow"
          >
            <option
              v-for="window in m3FactorWindows"
              :key="factorWindowKey(window)"
              :value="factorWindowKey(window)"
            >
              {{ window.start_date }} → {{ window.end_date }}
              · {{ window.research_stage === 'm3_s7_formal'
                ? `${window.measurable_count}/${window.expected_factors.length}`
                : `${window.preliminary_measurable_count}/${window.preliminary_requested_count}` }} 可测
              · Job #{{ window.research_run_id }}
              {{ window.research_stage === 'm3_s7_formal'
                ? '· 正式多年窗'
                : window.research_stage === 'm3_preliminary_flow'
                  ? '· 资金流独立窗'
                  : '· 多年主窗' }}
            </option>
          </select>
        </label>
        <div v-else-if="diagnosisMode === 'm2'" class="mode-caution">
          <TriangleAlert :size="15" />
          仅回看 M2；不得据此修改 M3 权重
        </div>
      </section>

      <section
        v-if="diagnosisLoading && !diagnosis"
        class="panel diagnosis-loading"
        aria-live="polite"
      >
        <span class="orbit"><Activity :size="22" /></span>
        <div>
          <h2>{{ diagnosisMode === 'm3' ? '正在读取精确 train 窗' : '正在读取 M2 历史证据' }}</h2>
          <p>
            {{ diagnosisMode === 'm3'
              ? isFormalM3
                ? '只读取正式 JobRun 的精确 train 结果，不现场重算 full/test，也不触发权重重算。'
                : '只读取持久化 train 结果，不请求 full/test，也不触发权重重算。'
              : '用户已主动切换；载入 full 诊断与同协议样本外对照。' }}
          </p>
        </div>
      </section>

      <div v-else-if="diagnosis" class="diagnosis-workspace">
        <section
          v-if="diagnosisMode === 'm2'"
          class="m2-verdict"
          :class="comparison?.verdict.status ?? 'pending'"
        >
          <div class="m2-verdict-copy">
            <div class="verdict-kicker">
              <Beaker :size="14" />
              OUT-OF-SAMPLE VERDICT
              <span class="mono">
                {{ comparison ? `RUN #${comparison.v1.run_id} / #${comparison.v2.run_id}` : 'NO PAIR' }}
              </span>
            </div>
            <h2>
              {{ comparison?.verdict.status === 'failed'
                ? '❌ M2 仍失败：没有样本外可信 alpha'
                : comparison?.verdict.headline || '等待同协议 v1/v2 样本外对照' }}
            </h2>
            <p>
              {{ comparison?.verdict.headline
                || '未找到同一 test 窗、调仓频率与成本协议的两条已完成运行；不进行错窗比较。' }}
            </p>
            <div class="evidence-warning">
              <TriangleAlert :size="16" />
              <div>
                <strong>{{ diagnosis.sample.evidence_label }}</strong>
                <span>
                  现有证据不足以宣布“重构成功”，更不能据此恢复 paper_auto。
                </span>
              </div>
            </div>
          </div>
          <div class="m2-scoreboard">
            <article>
              <span>v1 test IC</span>
              <strong class="num" :class="pctClass(comparison?.v1.rank_ic.mean)">
                {{ fmtNum(comparison?.v1.rank_ic.mean, 4) }}
              </strong>
              <small>t {{ fmtNum(comparison?.v1.rank_ic.t_stat, 3) }}</small>
            </article>
            <article>
              <span>v2 test IC</span>
              <strong class="num" :class="pctClass(comparison?.v2.rank_ic.mean)">
                {{ fmtNum(comparison?.v2.rank_ic.mean, 4) }}
              </strong>
              <small>t {{ fmtNum(comparison?.v2.rank_ic.t_stat, 3) }} · 未达 |2|</small>
            </article>
            <article>
              <span>v2 扣成本</span>
              <strong class="num" :class="pctClass(comparison?.v2.net_long.total_return)">
                {{ fmtPct(comparison?.v2.net_long.total_return, 2, false) }}
              </strong>
              <small>v1 {{ fmtPct(comparison?.v1.net_long.total_return, 2, false) }}</small>
            </article>
            <article>
              <span>v2 vs 等权</span>
              <strong
                class="num"
                :class="pctClass(comparison?.v2.benchmarks.excess_total_return.vs_equal_weight_market)"
              >
                {{ fmtPct(comparison?.v2.benchmarks.excess_total_return.vs_equal_weight_market, 2, false) }}
              </strong>
              <small>成本 {{ fmtPct(comparison?.v2.costs.to_initial_capital, 2, false) }}</small>
            </article>
          </div>
        </section>

        <section v-else class="m3-preview">
          <div class="m3-preview-copy">
            <div class="preview-title">
              <Beaker :size="17" />
              <div>
                <h2>{{ isFormalM3 ? 'M3 S7 正式 train 诊断' : 'M3 初步 train 诊断' }}</h2>
                <p>
                  精确窗口已锁定至 Job #{{ diagnosis.sample.research_run_id }}；
                  {{ isFormalM3
                    ? 'full/train/test 已由该 JobRun 计算，本页只展示 train，禁止回看 test 调权。'
                    : 'test 窗未读取，当前结果不用于调权。' }}
                </p>
              </div>
            </div>
            <p class="preview-boundary">{{ diagnosis.sample.evidence_label }}</p>
          </div>
          <div class="preview-counts" aria-label="M3 预验覆盖统计">
            <span>
              <b class="mono">{{ m3RequestedCount }}</b>
              {{ isFormalM3 ? '历史候选' : '先行因子' }}
            </span>
            <span>
              <b class="mono up">{{ m3MeasurableCount }}</b>
              本窗可测
            </span>
            <span>
              <b class="mono">{{ isFormalM3 ? m3NoSampleCount : diagnosis.coverage.financial_pending_count }}</b>
              {{ isFormalM3 ? '已算 n=0' : '财务待 S2' }}
            </span>
            <span>
              <b class="mono">
                {{
                  diagnosis.coverage.live_only_count
                    + diagnosis.coverage.history_excluded_pit_gap_count
                }}
              </b>
              非历史候选
            </span>
          </div>
        </section>

        <section class="diagnosis-meta-strip" aria-label="因子诊断覆盖">
          <span>
            {{ diagnosisMode === 'm3' ? '精确 TRAIN 窗' : 'M2 FULL 窗' }}
            <b class="mono">{{ diagnosis.sample.start_date || '—' }} → {{ diagnosis.sample.end_date || '—' }}</b>
          </span>
          <span>
            {{ diagnosisMode === 'm3' ? (isFormalM3 ? '候选可测' : '先行可测') : '可测因子' }}
            <b class="mono">
              {{ diagnosisMode === 'm3'
                ? `${m3MeasurableCount} / ${m3RequestedCount}`
                : `${diagnosis.sample.available_count} / ${diagnosis.sample.factor_count}` }}
            </b>
          </span>
          <span>
            {{ diagnosisMode === 'm3' ? '已算但 n=0' : '弱证据' }}
            <b class="mono">
              {{ diagnosisMode === 'm3'
                ? m3NoSampleCount
                : diagnosis.classification_counts.ineffective }}
            </b>
          </span>
          <span>
            {{ diagnosisMode === 'm3'
              ? (isFormalM3 ? '历史排除' : '财务待回填')
              : '样本不足' }}
            <b class="mono">
              {{ diagnosisMode === 'm3'
                ? isFormalM3
                  ? diagnosis.coverage.history_excluded_pit_gap_count
                  : diagnosis.coverage.financial_pending_count
                : diagnosis.classification_counts.insufficient_data }}
            </b>
          </span>
          <span>
            数据更新
            <b class="mono">{{ fmtTime(diagnosis.sample.updated_at) }}</b>
          </span>
        </section>

        <div class="diagnosis-chart-grid" :class="{ preview: diagnosisMode === 'm3' }">
          <section class="panel">
            <div class="panel-title">
              <span>
                <BarChart3 :size="13" />
                {{ diagnosisMode === 'm3' ? 'M3 train Rank IC' : 'M2 full Rank IC' }}
              </span>
              <span class="extra">正绿 · 负红 · 四种缺失状态不补零</span>
            </div>
            <EChart
              v-if="diagnosis.available"
              :option="factorIcOption"
              height="420px"
              :aria-label="diagnosisMode === 'm3'
                ? 'M3 精确 train 窗因子 Rank IC 条形图'
                : 'M2 full 窗十三因子 Rank IC 条形图'"
            />
            <div v-else class="chart-empty">
              <span>暂无持久化 IC 快照；不会使用示例值。</span>
            </div>
          </section>

          <section v-if="diagnosisMode === 'm2'" class="panel">
            <div class="panel-title">
              <span><SlidersHorizontal :size="13" /> v1 → v2 权重变化</span>
              <span class="extra">train 单次 IC_IR · test 未参与</span>
            </div>
            <EChart
              :option="factorWeightsOption"
              height="420px"
              aria-label="v1 与 v2 十三因子权重对比"
            />
            <div class="chart-caption">
              <span>方法 <b class="mono">{{ diagnosis.weights.method }}</b></span>
              <span>
                test 调权
                <b :class="diagnosis.weights.test_window_used_for_weights ? 'down' : 'up'">
                  {{ diagnosis.weights.test_window_used_for_weights ? '是' : '否' }}
                </b>
              </span>
            </div>
          </section>
        </div>

        <section class="panel correlation-panel">
          <div class="panel-title">
            <span>相关矩阵 / 冗余审计</span>
            <span class="extra">
              20D 决策截面 · 至少 {{ diagnosis.correlation.minimum_pair_periods }} 期 · |ρ| &gt; {{ diagnosis.correlation.threshold }}
            </span>
          </div>
          <EChart
            v-if="diagnosis.correlation.available"
            class="correlation-chart"
            :option="correlationOption"
            height="570px"
            aria-label="13 因子横截面相关热力图"
          />
          <div v-else class="chart-empty">
            <span>相关性有效截面不足；空白单元格不按 0 处理。</span>
          </div>
          <details v-if="diagnosis.correlation.available" class="correlation-table-fallback">
            <summary>查看相关矩阵数据表</summary>
            <div class="correlation-table-scroll">
              <table class="tbl correlation-table">
                <caption>相关系数；“未评估”不是 0</caption>
                <thead>
                  <tr>
                    <th scope="col">因子</th>
                    <th
                      v-for="factor in diagnosis.correlation.factors"
                      :key="`corr-head-${factor}`"
                      scope="col"
                    >
                      {{ FACTOR_LABELS[factor] || factor }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(factor, rowIndex) in diagnosis.correlation.factors"
                    :key="`corr-row-${factor}`"
                  >
                    <th scope="row">{{ FACTOR_LABELS[factor] || factor }}</th>
                    <td
                      v-for="(value, columnIndex) in diagnosis.correlation.values[rowIndex]"
                      :key="`corr-${rowIndex}-${columnIndex}`"
                      class="num"
                    >
                      {{ value === null ? '未评估' : fmtNum(value, 2) }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </details>
          <div class="correlation-foot">
            <span>{{ diagnosis.correlation.limitation }}</span>
            <b>
              冗余对：
              {{ diagnosis.correlation.redundant_pairs.length
                ? diagnosis.correlation.redundant_pairs.length
                : '0（无可靠 |ρ| > 0.8）' }}
            </b>
          </div>
        </section>

        <section v-if="diagnosisMode === 'm2'" class="panel comparison-panel">
          <div class="panel-title">
            <span><Activity :size="13" /> test 窗净值对照</span>
            <span v-if="comparison" class="extra">
              {{ comparison.protocol.start_date }} → {{ comparison.protocol.end_date }}
              · {{ comparison.protocol.rebalance_freq }} · 同成本
            </span>
            <span v-else class="extra">严格同窗匹配</span>
          </div>
          <EChart
            v-if="comparison?.curve.dates.length"
            :option="comparisonNavOption"
            height="330px"
            aria-label="样本外 test 窗 v1 v2 与双基准净值对照"
          />
          <div v-else class="chart-empty">
            <span>没有可比的 v1/v2 同协议运行；拒绝拼接不同区间曲线。</span>
          </div>
          <div v-if="comparison" class="comparison-policy">
            <ShieldCheck :size="14" />
            <span>{{ comparison.verdict.policy }}</span>
          </div>
        </section>

        <section class="panel factor-audit-panel">
          <div class="panel-title">
            <span><ShieldCheck :size="13" /> 因子方向与源码审计</span>
            <span class="extra">{{ diagnosis.source_audit.verdict }}</span>
          </div>
          <div class="factor-table-scroll">
            <table class="tbl factor-table">
              <thead>
                <tr>
                  <th>因子</th>
                  <th class="r">IC</th>
                  <th class="r">t</th>
                  <th class="r">期数</th>
                  <th>评估状态</th>
                  <th>分类</th>
                  <th>原始计算 / 方向</th>
                  <th>审计结论</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in diagnosis.factors" :key="item.factor">
                  <td>
                    <b>{{ FACTOR_LABELS[item.factor] || item.factor }}</b>
                    <small class="mono">{{ item.factor }}</small>
                  </td>
                  <td class="r num" :class="pctClass(item.ic_mean)">
                    {{ fmtNum(item.ic_mean, 4) }}
                  </td>
                  <td class="r num">{{ fmtNum(item.t_stat, 3) }}</td>
                  <td class="r num">{{ item.n_periods }}</td>
                  <td>
                    <span
                      class="factor-state"
                      :class="EVALUATION_LABELS[item.evaluation_status].tone"
                      :title="EVALUATION_LABELS[item.evaluation_status].note"
                    >
                      {{ EVALUATION_LABELS[item.evaluation_status].label }}
                    </span>
                  </td>
                  <td>
                    <span
                      class="factor-state"
                      :class="CLASSIFICATION_LABELS[item.classification].tone"
                    >
                      {{ CLASSIFICATION_LABELS[item.classification].label }}
                    </span>
                  </td>
                  <td>
                    <code>{{ item.direction_audit.formula }}</code>
                    <small>{{ item.direction_audit.raw_direction }}</small>
                  </td>
                  <td class="audit-verdict">
                    {{ item.direction_audit.verdict }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="diagnosis-limitations">
          <div>
            <TriangleAlert :size="16" />
            <span>结论边界</span>
          </div>
          <ul>
            <li v-for="item in diagnosis.limitations" :key="item">{{ item }}</li>
            <li
              v-for="item in diagnosisMode === 'm2' ? comparison?.limitations || [] : []"
              :key="`compare-${item}`"
            >
              {{ item }}
            </li>
          </ul>
        </section>

        <p class="research-disclaimer">
          {{ diagnosisMode === 'm3'
            ? isFormalM3
              ? 'M3 当前展示正式 JobRun 的精确 train：full/test 不在本页展开，禁止回看调权，也不连接提案、委托或交易。'
              : 'M3 当前只展示 train 预验：test 继续封存，不据此调权，也不连接提案、委托或交易。'
            : 'M2 是只读历史研究：失败不会触发自动调参、提案、委托或交易。' }}
        </p>
      </div>

      <section v-else class="panel diagnosis-empty">
        <TriangleAlert :size="22" />
        <h2>诊断证据暂不可用</h2>
        <p>未使用占位数据。请确认 API 健康后点击“刷新证据”。</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.backtest-page {
  min-width: 0;
}
.research-head {
  align-items: center;
}
.research-head > div:first-child {
  display: grid;
  gap: 2px;
}
.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}
.run-ref {
  color: var(--text-3);
  font-size: 11px;
}
.status-pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.status-pulse.live {
  animation: research-pulse 1.5s ease-out infinite;
}
.page-message {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.research-tabs {
  display: flex;
  gap: 22px;
  border-bottom: 1px solid var(--line-1);
  margin: -2px 0 14px;
}
.research-tabs button {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 0;
  padding: 9px 1px 10px;
  color: var(--text-3);
  background: transparent;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.research-tabs button::after {
  content: '';
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 2px;
  background: transparent;
}
.research-tabs button:hover,
.research-tabs button.active {
  color: var(--text-1);
}
.research-tabs button.active::after {
  background: var(--accent-hi);
}
.tab-count {
  min-width: 20px;
  border: 1px solid var(--line-2);
  border-radius: 3px;
  padding: 1px 4px;
  color: var(--text-3);
  font-size: 10px;
  text-align: center;
}
.research-top-grid {
  display: grid;
  grid-template-columns: 390px minmax(0, 1fr);
  gap: 12px;
  align-items: stretch;
  margin-bottom: 12px;
}
.config-panel {
  overflow: visible;
}
.panel-title > span:first-child {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.protocol-mark {
  display: grid;
  grid-template-columns: 20px auto 1fr;
  align-items: center;
  gap: 6px;
  border: 1px solid rgba(52, 211, 153, 0.2);
  border-radius: 7px;
  padding: 7px 9px;
  margin-bottom: 12px;
  color: var(--up);
  background: rgba(52, 211, 153, 0.045);
}
.protocol-mark span {
  font-size: 11px;
  font-weight: 650;
}
.protocol-mark small {
  color: var(--text-3);
  font-size: 9.5px;
  text-align: right;
}
.field-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 9px;
}
.field {
  display: grid;
  grid-column: span 2;
  gap: 4px;
}
.field.full {
  grid-column: 1 / -1;
}
.field.date-field {
  grid-column: span 3;
}
.field.capital-field {
  grid-column: span 2;
}
.field > span:first-child,
.cost-grid label > span {
  color: var(--text-3);
  font-size: 10.5px;
}
.field .input {
  width: 100%;
  min-width: 0;
}
.suffix-input {
  position: relative;
  display: flex;
}
.suffix-input .input {
  padding-right: 28px;
}
.suffix-input i {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: 10px;
  font-style: normal;
}
.cost-protocol {
  border-top: 1px solid var(--line-1);
  margin-top: 12px;
  padding-top: 10px;
}
.cost-protocol summary {
  display: flex;
  justify-content: space-between;
  color: var(--text-2);
  font-size: 11px;
  cursor: pointer;
  list-style: none;
}
.cost-protocol summary::-webkit-details-marker {
  display: none;
}
.cost-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
  margin-top: 8px;
}
.cost-grid label {
  display: grid;
  gap: 3px;
}
.cost-grid label:last-child {
  grid-column: span 2;
}
.cost-grid .input {
  width: 100%;
}
.run-button {
  width: 100%;
  margin-top: 12px;
}
.run-hint {
  margin-top: 7px;
  color: var(--text-3);
  font-size: 9.5px;
  line-height: 1.45;
  text-align: center;
}
.verdict-panel {
  position: relative;
  min-height: 100%;
  border: 1px solid var(--line-2);
  border-radius: var(--r-lg);
  overflow: hidden;
  background:
    linear-gradient(112deg, rgba(96, 165, 250, 0.08), transparent 42%),
    linear-gradient(180deg, var(--surface-2), rgba(7, 12, 24, 0.96));
}
.verdict-panel::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0.24;
  background: repeating-linear-gradient(
    90deg,
    transparent 0 35px,
    rgba(148, 163, 198, 0.08) 35px 36px
  );
  mask-image: linear-gradient(to bottom, transparent, #000 78%, #000);
}
.verdict-panel.negative {
  border-color: rgba(248, 113, 113, 0.32);
  box-shadow: inset 3px 0 0 rgba(248, 113, 113, 0.72);
}
.verdict-panel.positive {
  border-color: rgba(52, 211, 153, 0.34);
  box-shadow: inset 3px 0 0 rgba(52, 211, 153, 0.76);
}
.verdict-panel.pending {
  border-color: rgba(251, 191, 36, 0.3);
}
.verdict-content {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  min-height: 100%;
  padding: 20px 22px 14px;
}
.verdict-kicker {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.12em;
}
.verdict-kicker span {
  margin-left: auto;
  color: var(--text-2);
  letter-spacing: 0;
}
.verdict-content h2 {
  max-width: 860px;
  margin-top: 15px;
  font-size: clamp(18px, 2vw, 25px);
  line-height: 1.35;
  letter-spacing: -0.025em;
  text-wrap: balance;
}
.negative .verdict-content h2 {
  color: #fecaca;
}
.positive .verdict-content h2 {
  color: #a7f3d0;
}
.verdict-content > p {
  max-width: 900px;
  margin-top: 7px;
  color: var(--text-2);
  font-size: 11px;
}
.gate-tape {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0;
  border: 1px solid var(--line-1);
  border-radius: 9px;
  margin-top: 22px;
  overflow: hidden;
  background: rgba(4, 7, 15, 0.48);
}
.gate-tape article {
  position: relative;
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  align-items: center;
  gap: 6px;
  min-height: 72px;
  padding: 10px;
  border-right: 1px solid var(--line-1);
  color: var(--down);
}
.gate-tape article:last-child {
  border-right: 0;
}
.gate-tape article.pass {
  color: var(--up);
}
.gate-index {
  position: absolute;
  top: 5px;
  right: 7px;
  color: var(--text-3);
  font-size: 8px;
}
.gate-tape b {
  min-width: 0;
  color: var(--text-1);
  font-size: 10.5px;
  font-weight: 600;
  line-height: 1.25;
  word-break: keep-all;
}
.gate-tape small {
  grid-column: 2;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: 8.5px;
}
.verdict-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin-top: auto;
  padding-top: 13px;
  color: var(--text-3);
  font-size: 9.5px;
}
.verdict-foot b {
  color: var(--text-2);
  font-weight: 500;
}
.verdict-state {
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  align-content: center;
  min-height: 330px;
  padding: 28px;
  color: var(--text-3);
  text-align: center;
}
.verdict-state h2 {
  margin-top: 12px;
  color: var(--text-1);
  font-size: 18px;
}
.verdict-state p {
  max-width: 560px;
  margin-top: 6px;
  font-size: 11px;
}
.failed-state {
  color: var(--down);
}
.orbit {
  display: grid;
  place-items: center;
  width: 54px;
  height: 54px;
  border: 1px solid rgba(251, 191, 36, 0.32);
  border-radius: 50%;
  color: var(--warn);
  animation: orbit-glow 1.8s ease-in-out infinite;
}
.progress-ruler {
  width: min(440px, 80%);
  height: 3px;
  margin-top: 20px;
  overflow: hidden;
  background: rgba(148, 163, 198, 0.1);
}
.progress-ruler i {
  display: block;
  width: 32%;
  height: 100%;
  background: linear-gradient(90deg, transparent, var(--warn), transparent);
  animation: ruler-scan 1.8s ease-in-out infinite;
}
.metric-deck {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}
.verdict-metrics {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 18px 0 0;
}
.metric-deck article,
.metric-skeletons > div {
  min-height: 94px;
  border: 1px solid var(--line-1);
  border-radius: 9px;
  padding: 11px 12px;
  background: linear-gradient(160deg, rgba(37, 99, 235, 0.07), transparent 48%), var(--surface-1);
}
.metric-deck article {
  display: grid;
  align-content: center;
}
.verdict-metrics article {
  min-height: 88px;
  border-color: rgba(148, 163, 198, 0.13);
  background:
    linear-gradient(160deg, rgba(37, 99, 235, 0.09), transparent 52%),
    rgba(4, 7, 15, 0.34);
}
.metric-deck article > span {
  color: var(--text-3);
  font-size: 10px;
}
.metric-deck strong {
  margin-top: 3px;
  font-size: 19px;
  line-height: 1.25;
}
.metric-deck small {
  margin-top: 3px;
  color: var(--text-3);
  font-size: 9px;
  line-height: 1.35;
}
.chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(320px, 0.8fr);
  gap: 12px;
  margin-bottom: 12px;
}
.analysis-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(340px, 0.85fr);
  gap: 12px;
  margin-bottom: 12px;
}
.chart-empty {
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  min-height: 260px;
  color: var(--text-3);
  font-size: 11px;
}
.chart-caption {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  border-top: 1px solid var(--line-1);
  margin: 0 -16px -16px;
  padding: 8px 16px;
  color: var(--text-3);
  font-size: 9.5px;
}
.chart-caption b {
  color: var(--text-2);
}
.calibration-unavailable {
  display: grid;
  grid-template-columns: 74px 1fr;
  align-items: center;
  gap: 14px;
  min-height: 146px;
  padding: 12px 14px;
}
.calibration-glyph {
  display: grid;
  place-items: center;
  width: 68px;
  height: 68px;
  border: 1px dashed rgba(251, 191, 36, 0.42);
  border-radius: 50%;
  color: var(--warn);
  background: rgba(251, 191, 36, 0.045);
  font-size: 15px;
}
.calibration-unavailable b {
  font-size: 12px;
}
.calibration-unavailable p {
  margin-top: 5px;
  color: var(--text-3);
  font-size: 10.5px;
  line-height: 1.55;
}
.gross-diagnostic {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  border-top: 1px solid var(--line-1);
  margin: 0 -16px -16px;
  padding: 10px 16px;
  color: var(--warn);
}
.gross-diagnostic div {
  display: grid;
  gap: 2px;
}
.gross-diagnostic b {
  color: var(--text-2);
  font-size: 10px;
}
.gross-diagnostic span {
  color: var(--text-3);
  font-size: 9.5px;
}
.evidence-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(300px, 0.55fr);
  gap: 12px;
  margin-bottom: 12px;
}
.limitation-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 14px;
}
.limitation-list article {
  display: grid;
  grid-template-columns: 34px 1fr;
  gap: 8px;
  border-bottom: 1px solid var(--line-1);
  padding: 9px 0;
}
.severity {
  align-self: start;
  border: 1px solid var(--line-2);
  border-radius: 4px;
  padding: 2px 3px;
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: 7px;
  text-align: center;
}
.severity.high {
  border-color: rgba(248, 113, 113, 0.32);
  color: var(--down);
}
.severity.medium {
  border-color: rgba(251, 191, 36, 0.3);
  color: var(--warn);
}
.limitation-list b {
  color: var(--text-2);
  font-size: 9px;
  font-weight: 500;
}
.limitation-list p {
  margin-top: 3px;
  color: var(--text-3);
  font-size: 9.5px;
  line-height: 1.45;
}
.audit-panel {
  align-self: start;
}
.audit-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--line-1);
  padding: 8px 0;
  font-size: 10px;
}
.audit-row:last-child {
  border-bottom: 0;
}
.audit-row span {
  color: var(--text-3);
}
.audit-row b {
  color: var(--text-2);
  font-size: 9.5px;
  font-weight: 500;
  text-align: right;
}
.history-panel {
  margin-bottom: 10px;
}
.history-scroll {
  max-width: 100%;
  overflow-x: auto;
}
.history-table {
  min-width: 920px;
}
.history-table tbody tr {
  cursor: pointer;
}
.history-table tbody tr.selected {
  background: rgba(59, 130, 246, 0.1);
  box-shadow: inset 2px 0 0 var(--accent-hi);
}
.history-table td b {
  display: block;
  font-size: 11px;
}
.history-table td small {
  display: block;
  color: var(--text-3);
  font-size: 9px;
}
.diagnosis-workspace {
  display: grid;
  gap: 12px;
}
.research-mode-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  border-bottom: 1px solid var(--line-1);
  padding: 0 0 12px;
  margin-bottom: 12px;
}
.mode-tabs {
  display: inline-flex;
  align-items: stretch;
  gap: 4px;
  border: 1px solid var(--line-1);
  border-radius: 9px;
  padding: 3px;
  background: var(--surface-1);
}
.mode-tabs button {
  display: grid;
  gap: 1px;
  border: 0;
  border-radius: 6px;
  padding: 7px 11px;
  color: var(--text-2);
  background: transparent;
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  transition: color var(--t-fast), background var(--t-fast);
}
.mode-tabs button span {
  color: var(--text-3);
  font-size: 11px;
}
.mode-tabs button:hover {
  color: var(--text-1);
  background: rgba(96, 165, 250, 0.06);
}
.mode-tabs button.active {
  color: var(--text-1);
  background: var(--surface-3);
}
.window-picker {
  display: grid;
  grid-template-columns: auto minmax(280px, 420px);
  align-items: center;
  gap: 9px;
  color: var(--text-2);
  font-size: 11px;
}
.window-picker .input {
  min-width: 280px;
  font-family: var(--font-mono);
  font-size: 11px;
}
.mode-caution {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--warn);
  font-size: 11px;
}
.diagnosis-loading,
.diagnosis-empty {
  display: grid;
  place-items: center;
  align-content: center;
  gap: 10px;
  min-height: 360px;
  color: var(--warn);
  text-align: center;
}
.diagnosis-loading {
  grid-template-columns: 58px auto;
  justify-content: center;
  text-align: left;
}
.diagnosis-loading h2,
.diagnosis-empty h2 {
  color: var(--text-1);
  font-size: 16px;
}
.diagnosis-loading p,
.diagnosis-empty p {
  margin-top: 4px;
  color: var(--text-3);
  font-size: 12px;
}
.m3-preview {
  display: flex;
  align-items: stretch;
  justify-content: space-between;
  gap: 24px;
  border: 1px solid rgba(96, 165, 250, 0.28);
  border-radius: var(--r-lg);
  padding: 20px;
  background: var(--surface-2);
}
.m3-preview-copy {
  display: grid;
  align-content: center;
  gap: 12px;
  min-width: 0;
}
.preview-title {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  color: var(--accent-hi);
}
.preview-title h2 {
  color: var(--text-1);
  font-size: 20px;
  letter-spacing: -0.02em;
}
.preview-title p,
.preview-boundary {
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.55;
}
.preview-counts {
  display: grid;
  grid-template-columns: repeat(4, minmax(86px, 1fr));
  min-width: min(100%, 460px);
  border: 1px solid var(--line-1);
  background: rgba(4, 7, 15, 0.28);
}
.preview-counts span {
  display: grid;
  align-content: center;
  gap: 2px;
  min-height: 84px;
  border-right: 1px solid var(--line-1);
  padding: 12px;
  color: var(--text-3);
  font-size: 11px;
}
.preview-counts span:last-child {
  border-right: 0;
}
.preview-counts b {
  color: var(--text-1);
  font-size: 21px;
  line-height: 1.2;
}
.m2-verdict {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(390px, 0.65fr);
  gap: 28px;
  border: 1px solid rgba(251, 191, 36, 0.3);
  border-radius: var(--r-lg);
  padding: 22px;
  background: var(--surface-2);
  box-shadow: inset 3px 0 0 rgba(251, 191, 36, 0.7);
}
.m2-verdict.failed {
  border-color: rgba(248, 113, 113, 0.36);
  box-shadow: inset 3px 0 0 rgba(248, 113, 113, 0.82);
}
.m2-verdict.improved {
  border-color: rgba(52, 211, 153, 0.34);
  box-shadow: inset 3px 0 0 rgba(52, 211, 153, 0.78);
}
.m2-verdict-copy {
  min-width: 0;
}
.m2-verdict-copy h2 {
  max-width: 760px;
  margin-top: 14px;
  color: #fecaca;
  font-size: clamp(19px, 2vw, 26px);
  line-height: 1.32;
  letter-spacing: -0.02em;
  text-wrap: balance;
}
.m2-verdict.improved .m2-verdict-copy h2 {
  color: #a7f3d0;
}
.m2-verdict-copy > p {
  max-width: 760px;
  margin-top: 7px;
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.6;
}
.evidence-warning {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  border-top: 1px solid rgba(251, 191, 36, 0.2);
  margin-top: 18px;
  padding-top: 13px;
  color: var(--warn);
}
.evidence-warning div {
  display: grid;
  gap: 3px;
}
.evidence-warning strong {
  font-size: 11px;
}
.evidence-warning span {
  color: var(--text-3);
  font-size: 11px;
}
.m2-scoreboard {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-self: stretch;
  border: 1px solid var(--line-1);
  background: rgba(4, 7, 15, 0.32);
}
.m2-scoreboard article {
  display: grid;
  align-content: center;
  min-height: 96px;
  padding: 13px 15px;
  border-right: 1px solid var(--line-1);
  border-bottom: 1px solid var(--line-1);
}
.m2-scoreboard article:nth-child(2n) {
  border-right: 0;
}
.m2-scoreboard article:nth-last-child(-n + 2) {
  border-bottom: 0;
}
.m2-scoreboard span {
  color: var(--text-3);
  font-size: 11px;
}
.m2-scoreboard strong {
  margin-top: 3px;
  font-size: 20px;
}
.m2-scoreboard small {
  margin-top: 2px;
  color: var(--text-3);
  font-size: 11px;
}
.diagnosis-meta-strip {
  display: grid;
  grid-template-columns: 1.6fr repeat(4, minmax(110px, 0.7fr));
  border: 1px solid var(--line-1);
  background: var(--surface-1);
}
.diagnosis-meta-strip > span {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  border-right: 1px solid var(--line-1);
  padding: 9px 12px;
  color: var(--text-3);
  font-size: 11px;
}
.diagnosis-meta-strip > span:last-child {
  border-right: 0;
}
.diagnosis-meta-strip b {
  color: var(--text-2);
  font-size: 11px;
  font-weight: 550;
  text-align: right;
}
.diagnosis-chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.diagnosis-chart-grid.preview {
  grid-template-columns: minmax(0, 1fr);
}
.correlation-panel,
.comparison-panel,
.factor-audit-panel {
  min-width: 0;
}
.correlation-foot {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  border-top: 1px solid var(--line-1);
  margin: 0 -16px -16px;
  padding: 9px 16px;
  color: var(--text-3);
  font-size: 11px;
}
.correlation-foot b {
  color: var(--text-2);
  font-weight: 550;
  text-align: right;
}
.comparison-policy {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  border-top: 1px solid rgba(52, 211, 153, 0.18);
  margin: 0 -16px -16px;
  padding: 10px 16px;
  color: var(--up);
}
.comparison-policy span {
  color: var(--text-3);
  font-size: 11px;
  line-height: 1.5;
}
.correlation-table-fallback {
  border-top: 1px solid var(--line-1);
  margin-top: 10px;
  padding-top: 10px;
}
.correlation-table-fallback summary {
  width: fit-content;
  color: var(--accent-hi);
  font-size: 11px;
  cursor: pointer;
}
.correlation-table-scroll {
  max-width: 100%;
  margin-top: 9px;
  overflow-x: auto;
}
.correlation-table {
  min-width: 1160px;
  font-size: 11px;
}
.correlation-table caption {
  padding: 0 0 7px;
  color: var(--text-3);
  text-align: left;
}
.correlation-table th,
.correlation-table td {
  min-width: 78px;
  text-align: center;
}
.correlation-table th:first-child {
  position: sticky;
  left: 0;
  z-index: 1;
  min-width: 112px;
  color: var(--text-2);
  background: var(--surface-1);
  text-align: left;
}
.factor-table-scroll {
  max-width: 100%;
  overflow-x: auto;
}
.factor-table {
  min-width: 1260px;
  table-layout: fixed;
}
.factor-table th:nth-child(1) { width: 145px; }
.factor-table th:nth-child(2),
.factor-table th:nth-child(3) { width: 72px; }
.factor-table th:nth-child(4) { width: 58px; }
.factor-table th:nth-child(5),
.factor-table th:nth-child(6) { width: 98px; }
.factor-table th:nth-child(7) { width: 315px; }
.factor-table td {
  vertical-align: top;
  line-height: 1.45;
}
.factor-table td > b,
.factor-table td > small {
  display: block;
}
.factor-table td > b {
  font-size: 12px;
}
.factor-table td > small {
  margin-top: 2px;
  color: var(--text-3);
  font-size: 11px;
}
.factor-table code {
  display: block;
  overflow-wrap: anywhere;
  color: var(--text-2);
  background: transparent;
  font-size: 11px;
  white-space: normal;
}
.factor-state {
  display: inline-block;
  border: 1px solid var(--line-2);
  border-radius: 3px;
  padding: 2px 5px;
  color: var(--text-3);
  font-size: 11px;
  white-space: nowrap;
}
.factor-state.positive {
  border-color: rgba(52, 211, 153, 0.3);
  color: var(--up);
}
.factor-state.negative {
  border-color: rgba(248, 113, 113, 0.32);
  color: var(--down);
}
.factor-state.weak {
  border-color: rgba(251, 191, 36, 0.28);
  color: var(--warn);
}
.factor-state.missing {
  color: var(--text-3);
}
.factor-state.live {
  border-color: rgba(96, 165, 250, 0.32);
  color: var(--accent-hi);
}
.audit-verdict {
  color: var(--text-3);
  font-size: 11px;
}
.diagnosis-limitations {
  display: grid;
  grid-template-columns: 130px 1fr;
  border: 1px solid rgba(251, 191, 36, 0.24);
  background: rgba(251, 191, 36, 0.035);
}
.diagnosis-limitations > div {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  border-right: 1px solid rgba(251, 191, 36, 0.18);
  padding: 14px;
  color: var(--warn);
  font-size: 11px;
  font-weight: 600;
}
.diagnosis-limitations ul {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px 24px;
  margin: 0;
  padding: 13px 16px 13px 32px;
  color: var(--text-3);
  font-size: 11px;
  line-height: 1.5;
}
.research-disclaimer {
  color: var(--text-3);
  font-size: 11px;
  line-height: 1.55;
  text-align: center;
}
.spin {
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
@keyframes research-pulse {
  0% { box-shadow: 0 0 0 0 currentColor; }
  70%, 100% { box-shadow: 0 0 0 5px transparent; }
}
@keyframes orbit-glow {
  50% { box-shadow: 0 0 22px rgba(251, 191, 36, 0.28); }
}
@keyframes ruler-scan {
  from { transform: translateX(-110%); }
  to { transform: translateX(320%); }
}
@media (prefers-reduced-motion: reduce) {
  .spin,
  .status-pulse.live,
  .orbit,
  .progress-ruler i {
    animation: none;
  }
}
@media (max-width: 1240px) {
  .metric-deck {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .gate-tape {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .gate-tape article:nth-child(3) {
    border-right: 0;
  }
  .gate-tape article:nth-child(n + 4) {
    border-top: 1px solid var(--line-1);
  }
  .m2-verdict {
    grid-template-columns: 1fr;
  }
  .m3-preview {
    display: grid;
  }
  .preview-counts {
    width: 100%;
  }
  .m2-scoreboard {
    max-width: none;
  }
}
@media (max-width: 1040px) {
  .research-top-grid,
  .chart-grid,
  .analysis-grid,
  .evidence-grid,
  .diagnosis-chart-grid {
    grid-template-columns: 1fr;
  }
  .config-panel {
    max-width: none;
  }
  .limitation-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .diagnosis-meta-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .diagnosis-meta-strip > span {
    border-bottom: 1px solid var(--line-1);
  }
  .diagnosis-meta-strip > span:nth-child(2n) {
    border-right: 0;
  }
  .diagnosis-meta-strip > span:last-child {
    border-bottom: 0;
  }
  .research-mode-bar {
    align-items: stretch;
    flex-direction: column;
  }
  .window-picker {
    grid-template-columns: auto minmax(0, 1fr);
  }
  .window-picker .input {
    min-width: 0;
  }
}
@media (max-width: 760px) {
  .research-head {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .head-actions {
    width: 100%;
    flex-wrap: wrap;
    margin-left: 0;
  }
  .head-actions .run-ref {
    flex: 1 1 190px;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .head-actions .btn {
    min-height: 44px;
    margin-left: 0;
  }
  .research-tabs button,
  .mode-tabs button,
  .window-picker .input {
    min-height: 44px;
  }
  .correlation-table-fallback summary {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
  }
  .metric-deck {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .gate-tape {
    grid-template-columns: 1fr;
  }
  .gate-tape article,
  .gate-tape article:nth-child(3) {
    border-right: 0;
    border-top: 1px solid var(--line-1);
  }
  .gate-tape article:first-child {
    border-top: 0;
  }
  .limitation-list {
    grid-template-columns: 1fr;
  }
  .m2-verdict {
    gap: 20px;
    padding: 18px;
  }
  .diagnosis-limitations {
    grid-template-columns: 1fr;
  }
  .diagnosis-limitations > div {
    border-right: 0;
    border-bottom: 1px solid rgba(251, 191, 36, 0.18);
  }
  .diagnosis-limitations ul {
    grid-template-columns: 1fr;
  }
  .correlation-foot {
    display: grid;
  }
  .correlation-foot b {
    text-align: left;
  }
  .correlation-chart {
    display: none;
  }
  .correlation-table-fallback:not([open]) > .correlation-table-scroll {
    display: block;
  }
  .mode-tabs {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
  .window-picker {
    grid-template-columns: 1fr;
  }
  .preview-counts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .preview-counts span:nth-child(2) {
    border-right: 0;
  }
  .preview-counts span:nth-child(-n + 2) {
    border-bottom: 1px solid var(--line-1);
  }
}
@media (max-width: 480px) {
  .field-grid,
  .cost-grid,
  .metric-deck {
    grid-template-columns: 1fr;
  }
  .field,
  .field.full,
  .field.date-field,
  .field.capital-field,
  .cost-grid label:last-child {
    grid-column: auto;
  }
  .protocol-mark {
    grid-template-columns: 20px 1fr;
  }
  .protocol-mark small {
    grid-column: 1 / -1;
    text-align: left;
  }
  .verdict-content {
    padding-inline: 16px;
  }
  .verdict-foot {
    display: grid;
  }
  .research-tabs {
    gap: 16px;
  }
  .mode-tabs {
    grid-template-columns: 1fr;
  }
  .m3-preview {
    padding: 16px;
  }
  .preview-counts {
    grid-template-columns: 1fr;
  }
  .preview-counts span,
  .preview-counts span:nth-child(2) {
    border-right: 0;
    border-bottom: 1px solid var(--line-1);
  }
  .preview-counts span:last-child {
    border-bottom: 0;
  }
  .m2-scoreboard,
  .diagnosis-meta-strip {
    grid-template-columns: 1fr;
  }
  .m2-scoreboard article,
  .m2-scoreboard article:nth-child(2n),
  .m2-scoreboard article:nth-last-child(-n + 2) {
    border-right: 0;
    border-bottom: 1px solid var(--line-1);
  }
  .m2-scoreboard article:last-child,
  .diagnosis-meta-strip > span:last-child {
    border-bottom: 0;
  }
  .diagnosis-meta-strip > span {
    border-right: 0;
  }
}
</style>
