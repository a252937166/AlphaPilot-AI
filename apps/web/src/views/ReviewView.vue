<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Activity,
  BrainCircuit,
  CalendarClock,
  ChartNoAxesCombined,
  RefreshCw,
  Sparkles,
  Target,
  TriangleAlert,
} from 'lucide-vue-next'
import { ApiError, api } from '../api'
import type {
  DailyReportResponse,
  ImprovementStatistic,
  PortfolioAttributionResponse,
  ReportEvent,
  SignalAttributionRow,
} from '../api'
import {
  CHART_COLORS,
  categoryAxis,
  glowLine,
  tooltipStyle,
  valueAxis,
} from '../chartTheme'
import EChart from '../components/EChart.vue'
import { actionMeta, fmtNum, fmtPct, fmtTime, pctClass } from '../format'

type ReviewRow = SignalAttributionRow & { review_kind: 'hit' | 'miss' }

const router = useRouter()
const loadingReport = ref(true)
const loadingAttribution = ref(true)
const generating = ref(false)
const reportError = ref('')
const attributionError = ref('')
const reportMissing = ref(false)
const report = ref<DailyReportResponse | null>(null)
const attribution = ref<PortfolioAttributionResponse | null>(null)

const signalAttribution = computed(() => report.value?.signal_attribution ?? null)
const improvement = computed(() => report.value?.improvement_suggestions ?? null)
const timeline = computed(() => report.value?.event_timeline ?? null)

const reviewRows = computed<ReviewRow[]>(() => {
  const data = signalAttribution.value
  if (!data) return []
  return [
    ...data.top_hits.map((row) => ({ ...row, review_kind: 'hit' as const })),
    ...data.top_misses.map((row) => ({ ...row, review_kind: 'miss' as const })),
  ]
})

const sectorCall = computed(() => {
  const raw = report.value?.sector_call_excess
  if (!raw) {
    return {
      value: null,
      detail: report.value
        ? '日报接口未返回板块判断归因'
        : '生成日报后才可评估',
    }
  }
  const value = finiteNumber(raw.average_excess)
  const sampleCount = finiteNumber(raw.sample_count)
  return {
    value,
    detail: raw.warning
      || (sampleCount !== null ? `Top 3 中 ${sampleCount} 个可评估` : '样本数暂不可用'),
  }
})

const performanceChartOption = computed(() => {
  const data = attribution.value
  if (!data?.available || !data.dates.length || !data.nav.length) return {}
  const portfolio = data.nav.map((value) => Number(((value - 1) * 100).toFixed(4)))
  const benchmark = data.benchmark_nav.map((value) =>
    value === null ? null : Number(((value - 1) * 100).toFixed(4)),
  )
  const singlePoint = data.dates.length === 1
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle,
      valueFormatter: (value: number | null) =>
        value === null || value === undefined ? '暂不可用' : `${Number(value).toFixed(2)}%`,
    },
    legend: {
      top: 0,
      right: 4,
      itemWidth: 18,
      itemHeight: 2,
      textStyle: { color: CHART_COLORS.text3, fontSize: 10 },
      data: ['模拟组合', '沪深300'],
    },
    grid: { left: 48, right: 16, top: 32, bottom: 28 },
    xAxis: categoryAxis(data.dates.map((date) => date.slice(5))),
    yAxis: valueAxis({
      scale: true,
      axisLabel: {
        formatter: '{value}%',
        color: CHART_COLORS.text3,
        fontSize: 10,
      },
    }),
    series: [
      {
        name: '模拟组合',
        type: 'line',
        data: portfolio,
        symbol: singlePoint ? 'circle' : 'none',
        symbolSize: 7,
        smooth: data.dates.length > 2 ? 0.16 : false,
        connectNulls: false,
        lineStyle: glowLine(CHART_COLORS.cyan, 2),
        itemStyle: { color: CHART_COLORS.cyan },
        emphasis: { focus: 'series' },
        markLine: {
          symbol: 'none',
          silent: true,
          label: { show: false },
          lineStyle: { color: CHART_COLORS.line2, type: 'dashed', width: 1 },
          data: [{ yAxis: 0 }],
        },
      },
      {
        name: '沪深300',
        type: 'line',
        data: benchmark,
        symbol: singlePoint ? 'circle' : 'none',
        symbolSize: 7,
        smooth: data.dates.length > 2 ? 0.16 : false,
        connectNulls: false,
        lineStyle: { color: CHART_COLORS.slate, width: 1.6, type: 'dashed' },
        itemStyle: { color: CHART_COLORS.slate },
        emphasis: { focus: 'series' },
      },
    ],
  }
})

const performanceAriaLabel = computed(() => {
  const data = attribution.value
  if (!data?.available || !data.dates.length) return '组合净值与沪深300暂不可用'
  const start = data.dates[0]
  const end = data.dates[data.dates.length - 1]
  const excess = data.excess_cum === null ? '累计超额暂不可用' : `累计超额${fmtPct(data.excess_cum, 2, false)}`
  return `${start}至${end}，模拟组合与沪深300累计收益曲线，${excess}`
})

const suggestionItems = computed(() => improvement.value?.suggestions ?? [])
const statisticItems = computed(() => {
  if (suggestionItems.value.length) return []
  return (improvement.value?.statistics ?? []).slice(0, 5)
})

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') {
    return null
  }
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function ratioLabel(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? '—' : fmtPct(value, digits, false)
}

function sourceLabel(): string {
  const summary = report.value?.ai_summary
  if (!summary) return '尚未生成'
  const identity = [summary.provider, summary.model].filter(Boolean).join(' · ')
  const generic = summary.source === 'llm' ? '模型生成' : '规则生成'
  return identity ? `${generic} · ${identity}` : generic
}

function suggestionSourceLabel(): string {
  const value = improvement.value
  if (!value) return '尚未生成'
  const identity = [value.provider, value.model].filter(Boolean).join(' · ')
  const generic = value.source === 'llm' ? '模型建议' : '统计降级'
  return identity ? `${generic} · ${identity}` : generic
}

function eventBadgeClass(item: ReportEvent): string {
  return ['blue', 'yellow', 'purple', 'red', 'gray'].includes(item.type_color)
    ? item.type_color
    : 'gray'
}

function statisticTitle(item: ImprovementStatistic): string {
  return `${item.dimension_label} · ${item.group_label}`
}

async function load() {
  loadingReport.value = true
  loadingAttribution.value = true
  reportError.value = ''
  attributionError.value = ''
  reportMissing.value = false

  const [reportResult, attributionResult] = await Promise.allSettled([
    api.dailyReport(),
    api.portfolioAttribution(60),
  ])

  if (reportResult.status === 'fulfilled') {
    report.value = reportResult.value
  } else if (reportResult.reason instanceof ApiError && reportResult.reason.status === 404) {
    report.value = null
    reportMissing.value = true
  } else {
    report.value = null
    reportError.value = `今日复盘报告暂不可用：${String(reportResult.reason?.message || reportResult.reason)}`
  }
  loadingReport.value = false

  if (attributionResult.status === 'fulfilled') {
    attribution.value = attributionResult.value
  } else {
    attribution.value = null
    attributionError.value =
      `组合归因暂不可用：${String(attributionResult.reason?.message || attributionResult.reason)}`
  }
  loadingAttribution.value = false
}

async function generate() {
  generating.value = true
  reportError.value = ''
  try {
    report.value = await api.generateDailyReport()
    reportMissing.value = false
  } catch (exc: any) {
    reportError.value = `复盘生成失败：${String(exc.message || exc)}`
  } finally {
    generating.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="review-page">
    <div class="page-head review-head">
      <div>
        <h1>AI 复盘</h1>
        <span class="sub">组合表现 → 信号归因 → 改进建议 → 重要事件</span>
      </div>
      <div class="report-meta" v-if="report">
        <span class="mono">{{ report.report_date }}</span>
        <span>{{ sourceLabel() }}</span>
      </div>
      <button class="btn primary generate-button" :disabled="generating" @click="generate">
        <RefreshCw v-if="generating" :size="12" class="spin" />
        <Sparkles v-else :size="12" />
        {{ generating ? '生成中…' : report ? '重新生成今日复盘' : '生成今日复盘' }}
      </button>
    </div>

    <div v-if="reportError" class="banner error page-message" role="alert">{{ reportError }}</div>
    <div v-if="attributionError" class="banner error page-message" role="alert">{{ attributionError }}</div>

    <div v-if="report" class="summary-strip">
      <Sparkles :size="15" />
      <div>
        <span class="summary-label">今日结论 · {{ sourceLabel() }}</span>
        <p>{{ report.ai_summary?.text || '模型未返回有效结论，保留结构化归因供人工复核。' }}</p>
      </div>
      <time class="mono">{{ fmtTime(report.generated_at) }}</time>
    </div>
    <div v-else-if="reportMissing && !loadingReport" class="missing-strip">
      <BrainCircuit :size="15" />
      <span>今日复盘尚未生成；组合归因仍独立展示，不会用昨日结论或示例文本补位。</span>
    </div>

    <div class="attribution-cards" aria-label="归因核心指标">
      <article class="metric-card">
        <span class="metric-icon cyan"><ChartNoAxesCombined :size="15" /></span>
        <div>
          <span class="metric-label">累计超额收益</span>
          <strong
            class="num"
            :class="pctClass(attribution?.excess_cum)"
          >
            {{ ratioLabel(attribution?.excess_cum) }}
          </strong>
          <small>
            {{ attribution?.available_days && attribution.available_days >= 2
              ? `相对 ${attribution.benchmark_symbol}`
              : `需至少 2 日快照，当前 ${attribution?.available_days ?? 0} 日` }}
          </small>
        </div>
      </article>

      <article class="metric-card">
        <span class="metric-icon slate"><Activity :size="15" /></span>
        <div>
          <span class="metric-label">最大回撤对比</span>
          <strong class="num" :class="pctClass(attribution?.max_drawdown)">
            {{ ratioLabel(attribution?.max_drawdown) }}
          </strong>
          <small>沪深300 {{ ratioLabel(attribution?.benchmark_drawdown) }}</small>
        </div>
      </article>

      <article class="metric-card">
        <span class="metric-icon amber"><Target :size="15" /></span>
        <div>
          <span class="metric-label">五日方向命中率</span>
          <strong class="num">
            {{ ratioLabel(signalAttribution?.hit_rate_directional) }}
          </strong>
          <small>
            <template v-if="signalAttribution?.hit_rate_change_pp !== null && signalAttribution?.hit_rate_change_pp !== undefined">
              较 {{ signalAttribution.previous_report_date }} {{ fmtPct(signalAttribution.hit_rate_change_pp, 2) }}
            </template>
            <template v-else>
              方向样本 {{ signalAttribution?.directional_evaluated ?? 0 }} 条，环比暂不可用
            </template>
          </small>
        </div>
      </article>

      <article class="metric-card">
        <span class="metric-icon purple"><BrainCircuit :size="15" /></span>
        <div>
          <span class="metric-label">板块判断实际超额</span>
          <strong class="num" :class="pctClass(sectorCall.value)">
            {{ ratioLabel(sectorCall.value) }}
          </strong>
          <small>{{ sectorCall.detail }}</small>
        </div>
      </article>
    </div>

    <section class="panel performance-panel">
      <div class="performance-head">
        <div>
          <h2>组合净值 vs 沪深300</h2>
          <p>统一归一化为起始日 0%，基准缺口保留为空。</p>
        </div>
        <span v-if="attribution" class="range-label mono">
          {{ attribution.dates[0] || '—' }} → {{ attribution.dates.at(-1) || '—' }}
        </span>
      </div>
      <div v-if="loadingAttribution" class="skeleton chart-skeleton" />
      <EChart
        v-else-if="attribution?.available && attribution.dates.length"
        :option="performanceChartOption"
        :height="attribution.dates.length < 2 ? '220px' : '300px'"
        :aria-label="performanceAriaLabel"
      />
      <div v-else class="empty-chart">
        <ChartNoAxesCombined :size="22" />
        <span>{{ attribution?.warning || '尚无真实模拟组合快照，无法绘制净值曲线。' }}</span>
      </div>
      <div v-if="attribution?.warning" class="data-warning">
        <TriangleAlert :size="12" /> {{ attribution.warning }}
      </div>
    </section>

    <div class="review-grid">
      <section class="panel outcome-panel">
        <div class="panel-title">
          <span>机会 / 错误复盘</span>
          <span class="extra">五个交易日归因 · 贡献收益可复算</span>
        </div>
        <div
          v-if="reviewRows.length"
          class="table-scroll"
          tabindex="0"
          aria-label="机会与错误复盘表，可横向滚动"
        >
          <table class="tbl outcome-table">
            <thead>
              <tr>
                <th>结论</th><th>标的</th><th>动作</th><th>归因区间</th>
                <th class="r">实际收益</th><th class="r">贡献收益</th><th>模型版本</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in reviewRows" :key="`${row.review_kind}-${row.alert_id}`">
                <td>
                  <span class="badge" :class="row.review_kind === 'hit' ? 'green' : 'red'">
                    {{ row.review_kind === 'hit' ? '机会命中' : '错误信号' }}
                  </span>
                </td>
                <td>
                  <button class="symbol-link num" @click="router.push(`/stock/${row.symbol}`)">
                    {{ row.symbol }}
                  </button>
                </td>
                <td>
                  <span class="badge" :class="actionMeta(row.action).cls">
                    {{ actionMeta(row.action).label }}
                  </span>
                </td>
                <td class="xs dim mono">{{ row.origin_date }} → {{ row.maturity_date }}</td>
                <td class="r num" :class="pctClass(row.realized_return)">
                  {{ fmtPct(row.realized_return, 2, false) }}
                </td>
                <td class="r num" :class="pctClass(row.contribution)">
                  {{ fmtPct(row.contribution, 2, false) }}
                </td>
                <td class="xs dim mono">{{ row.model_version }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-hint">
          {{ loadingReport
            ? '正在读取提醒归因…'
            : '暂无完成五日观察窗的机会或错误样本；不会提前评价未成熟信号。' }}
        </div>
      </section>

      <section class="panel advice-panel">
        <div class="panel-title">
          <span>改进建议</span>
          <span class="extra">{{ suggestionSourceLabel() }}</span>
        </div>
        <div v-if="suggestionItems.length" class="advice-list">
          <article v-for="(item, index) in suggestionItems" :key="`${item.title}-${index}`">
            <span class="advice-index num">{{ String(index + 1).padStart(2, '0') }}</span>
            <div>
              <h3>{{ item.title }}</h3>
              <p>{{ item.text }}</p>
              <small>
                证据：{{ item.basis.map((basis) => `${basis.dimension_label}·${basis.group_label}`).join(' / ') }}
              </small>
            </div>
          </article>
        </div>
        <div v-else-if="statisticItems.length" class="advice-list statistics">
          <div v-if="improvement?.fallback_reason" class="fallback-note">
            {{ improvement.fallback_reason }}
          </div>
          <article v-for="item in statisticItems" :key="item.ref">
            <span class="advice-index"><Activity :size="13" /></span>
            <div>
              <h3>{{ statisticTitle(item) }}</h3>
              <p>{{ item.text }}</p>
              <small>贡献收益 {{ fmtPct(item.contribution_total, 2, false) }}</small>
            </div>
          </article>
        </div>
        <div v-else class="empty-hint">
          {{ improvement?.empty_reason
            || (reportMissing ? '生成日报后展示模型建议；模型不可用时自动展示统计证据。' : '暂无可形成建议的成熟样本。') }}
        </div>
      </section>
    </div>

    <section class="panel timeline-panel">
      <div class="panel-title">
        <span>重要事件时间线</span>
        <span class="extra"><CalendarClock :size="12" /> {{ timeline?.timezone || 'Asia/Shanghai' }}</span>
      </div>
      <ol v-if="timeline?.items.length" class="event-timeline">
        <li v-for="item in timeline.items" :key="item.id">
          <time class="mono">{{ fmtTime(item.occurred_at) }}</time>
          <span class="event-rail"><i /></span>
          <div class="event-card">
            <div>
              <span class="badge" :class="eventBadgeClass(item)">{{ item.type_label }}</span>
              <b v-if="item.symbol" class="mono">{{ item.symbol }}</b>
            </div>
            <h3>{{ item.title }}</h3>
            <p>{{ item.summary || '该事件暂无摘要，保留标题与来源供核对。' }}</p>
            <small v-if="item.source_ref" class="mono">来源 {{ item.source_ref }}</small>
          </div>
        </li>
      </ol>
      <div v-else class="empty-hint">
        {{ timeline?.empty_reason
          || (reportMissing ? '今日复盘尚未生成，事件时间线暂不可用。' : '当日暂无已入库的重要事件。') }}
      </div>
    </section>

    <div v-if="report?.disclaimer" class="disclaimer">{{ report.disclaimer }}</div>
  </div>
</template>

<style scoped>
.review-page {
  min-width: 0;
}
.review-head {
  align-items: center;
}
.review-head > div:first-child {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}
.report-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  color: var(--text-3);
  font-size: 10.5px;
}
.report-meta span + span {
  border-left: 1px solid var(--line-2);
  padding-left: 8px;
}
.generate-button {
  margin-left: auto;
}
.page-message {
  margin-bottom: 12px;
}
.summary-strip,
.missing-strip {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: start;
  gap: 10px;
  border-block: 1px solid rgba(34, 211, 238, 0.18);
  margin-bottom: 12px;
  padding: 11px 2px;
}
.summary-strip > svg,
.missing-strip > svg {
  margin-top: 3px;
  color: var(--cyan);
}
.summary-label {
  color: var(--accent-hi);
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.04em;
}
.summary-strip p {
  margin-top: 2px;
  color: var(--text-2);
  font-size: 12.5px;
  line-height: 1.6;
}
.summary-strip time {
  color: var(--text-3);
  font-size: 10px;
}
.missing-strip {
  grid-template-columns: auto 1fr;
  color: var(--text-3);
  font-size: 12px;
}
.attribution-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  border: 1px solid var(--line-1);
  border-radius: var(--r-md);
  margin-bottom: 12px;
  overflow: hidden;
  background: var(--line-1);
}
.metric-card {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: start;
  gap: 10px;
  min-width: 0;
  padding: 14px;
  background: linear-gradient(160deg, rgba(37, 99, 235, 0.055), transparent 52%),
    var(--surface-1);
}
.metric-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 7px;
}
.metric-icon.cyan {
  color: var(--cyan);
  background: rgba(34, 211, 238, 0.09);
}
.metric-icon.slate {
  color: var(--text-2);
  background: rgba(148, 163, 198, 0.09);
}
.metric-icon.amber {
  color: var(--warn);
  background: rgba(251, 191, 36, 0.09);
}
.metric-icon.purple {
  color: #c4b5fd;
  background: rgba(167, 139, 250, 0.1);
}
.metric-card > div {
  display: grid;
  min-width: 0;
}
.metric-label {
  color: var(--text-3);
  font-size: 10.5px;
}
.metric-card strong {
  margin: 3px 0 1px;
  font-size: 20px;
  font-weight: 650;
  letter-spacing: -0.025em;
}
.metric-card small {
  overflow: hidden;
  color: var(--text-3);
  font-size: 10px;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.performance-panel {
  margin-bottom: 12px;
}
.performance-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--line-1);
  margin: -16px -16px 5px;
  padding: 11px 16px;
}
.performance-head h2 {
  font-size: 12.5px;
}
.performance-head p {
  margin-top: 2px;
  color: var(--text-3);
  font-size: 10.5px;
}
.range-label {
  color: var(--text-3);
  font-size: 10px;
}
.chart-skeleton {
  height: 300px;
}
.empty-chart {
  display: grid;
  place-items: center;
  gap: 8px;
  min-height: 260px;
  color: var(--text-3);
  font-size: 12px;
  text-align: center;
}
.data-warning {
  display: flex;
  align-items: center;
  gap: 6px;
  border-top: 1px solid var(--line-1);
  margin: 2px -16px -16px;
  padding: 8px 16px;
  color: var(--warn);
  font-size: 10.5px;
}
.review-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.62fr) minmax(300px, 0.88fr);
  gap: 12px;
  align-items: start;
  margin-bottom: 12px;
}
.outcome-panel {
  padding-bottom: 6px;
}
.table-scroll {
  max-width: 100%;
  overflow-x: auto;
}
.outcome-table {
  min-width: 830px;
}
.symbol-link {
  border: 0;
  padding: 0;
  color: var(--accent-hi);
  background: transparent;
  font-size: 12px;
  font-weight: 650;
  cursor: pointer;
}
.symbol-link:hover {
  text-decoration: underline;
}
.advice-list {
  display: grid;
}
.advice-list article {
  display: grid;
  grid-template-columns: 24px 1fr;
  gap: 9px;
  border-bottom: 1px solid var(--line-1);
  padding: 10px 0;
}
.advice-list article:first-of-type {
  padding-top: 2px;
}
.advice-list article:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}
.advice-index {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 1px solid rgba(96, 165, 250, 0.28);
  border-radius: 6px;
  color: var(--accent-hi);
  background: rgba(37, 99, 235, 0.08);
  font-size: 9px;
}
.advice-list h3 {
  font-size: 12px;
  font-weight: 600;
}
.advice-list p {
  margin-top: 3px;
  color: var(--text-2);
  font-size: 11px;
  line-height: 1.55;
}
.advice-list small {
  display: block;
  margin-top: 4px;
  color: var(--text-3);
  font-size: 9.5px;
  line-height: 1.4;
}
.fallback-note {
  border-left: 2px solid var(--warn);
  margin-bottom: 5px;
  padding: 5px 8px;
  color: var(--text-3);
  font-size: 10px;
  line-height: 1.45;
}
.timeline-panel {
  margin-bottom: 10px;
}
.event-timeline {
  display: grid;
  margin: 0;
  padding: 0;
  list-style: none;
}
.event-timeline li {
  display: grid;
  grid-template-columns: 138px 18px 1fr;
  gap: 4px;
  min-height: 72px;
}
.event-timeline time {
  padding-top: 2px;
  color: var(--text-3);
  font-size: 10px;
  text-align: right;
}
.event-rail {
  position: relative;
  display: flex;
  justify-content: center;
}
.event-rail::after {
  content: '';
  position: absolute;
  top: 9px;
  bottom: -4px;
  width: 1px;
  background: var(--line-2);
}
.event-timeline li:last-child .event-rail::after {
  display: none;
}
.event-rail i {
  position: relative;
  z-index: 1;
  width: 7px;
  height: 7px;
  border: 1px solid var(--accent-hi);
  border-radius: 50%;
  margin-top: 5px;
  background: var(--surface-1);
  box-shadow: 0 0 8px rgba(96, 165, 250, 0.4);
}
.event-card {
  border-bottom: 1px solid var(--line-1);
  padding: 0 0 11px 8px;
}
.event-timeline li:last-child .event-card {
  border-bottom: 0;
}
.event-card > div {
  display: flex;
  align-items: center;
  gap: 7px;
}
.event-card > div b {
  color: var(--text-3);
  font-size: 10px;
  font-weight: 500;
}
.event-card h3 {
  margin-top: 5px;
  font-size: 12px;
  font-weight: 600;
}
.event-card p {
  margin-top: 2px;
  color: var(--text-3);
  font-size: 10.5px;
  line-height: 1.45;
}
.event-card small {
  display: block;
  margin-top: 4px;
  color: var(--text-3);
  font-size: 9px;
}
.disclaimer {
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
@media (prefers-reduced-motion: reduce) {
  .spin {
    animation: none;
  }
}
@media (max-width: 1120px) {
  .attribution-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .review-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 760px) {
  .review-head {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .review-head > div:first-child {
    display: grid;
    gap: 2px;
  }
  .report-meta {
    order: 3;
    width: 100%;
  }
  .generate-button {
    margin-left: auto;
  }
  .summary-strip {
    grid-template-columns: auto 1fr;
  }
  .summary-strip time {
    grid-column: 2;
  }
  .event-timeline li {
    grid-template-columns: 1fr;
    gap: 4px;
    border-bottom: 1px solid var(--line-1);
    padding: 9px 0;
  }
  .event-timeline li:last-child {
    border-bottom: 0;
  }
  .event-timeline time {
    text-align: left;
  }
  .event-rail {
    display: none;
  }
  .event-card {
    border: 0;
    padding: 0;
  }
}
@media (max-width: 520px) {
  .attribution-cards {
    grid-template-columns: 1fr;
  }
  .performance-head {
    flex-direction: column;
  }
  .generate-button {
    width: 100%;
    margin-left: 0;
  }
  .report-meta {
    flex-wrap: wrap;
  }
}
</style>
