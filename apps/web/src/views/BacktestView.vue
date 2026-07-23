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
  type BacktestDailyResponse,
  type BacktestReportResponse,
  type BacktestRunRecord,
  type BacktestRunRequest,
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
let pollTimer: number | undefined
let selectionToken = 0

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
      ?? response.runs.find((run) => run.status === 'completed')
      ?? response.runs[0]
    if (preferred) await selectRun(preferred.id)
  } catch (exc: unknown) {
    error.value = `回测列表暂不可用：${errorMessage(exc)}`
  } finally {
    listLoading.value = false
  }
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
onUnmounted(clearPoll)
</script>

<template>
  <div class="backtest-page">
    <header class="page-head research-head">
      <div>
        <h1>策略回测 / 研究</h1>
        <span class="sub">PIT 信号 → T+1 成交 → 全成本 → 双基准 → 诚实结论</span>
      </div>
      <div class="head-actions">
        <span class="badge" :class="statusClass(selectedRun)">
          <span class="status-pulse" :class="{ live: selectedRunning }" />
          {{ statusLabel(selectedRun) }}
        </span>
        <span v-if="selectedRun" class="run-ref mono">RUN #{{ selectedRun.id }}</span>
        <button class="btn" :disabled="listLoading || detailLoading" @click="loadRuns">
          <RefreshCw :size="12" :class="{ spin: listLoading || detailLoading }" />
          刷新证据
        </button>
      </div>
    </header>

    <div v-if="error" class="banner error page-message" role="alert">
      <TriangleAlert :size="14" />
      {{ error }}
    </div>

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
.research-disclaimer {
  color: var(--text-3);
  font-size: 9.5px;
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
}
@media (max-width: 1040px) {
  .research-top-grid,
  .chart-grid,
  .analysis-grid,
  .evidence-grid {
    grid-template-columns: 1fr;
  }
  .config-panel {
    max-width: none;
  }
  .limitation-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 760px) {
  .research-head {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .head-actions {
    width: 100%;
    margin-left: 0;
  }
  .head-actions .btn {
    margin-left: auto;
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
}
</style>
