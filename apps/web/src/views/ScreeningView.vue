<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Download, Play, RotateCcw } from 'lucide-vue-next'
import {
  api,
  type FactorWeightsResponse,
  type LatestScreenResponse,
  type RiskLevel,
  type ScreenCandidateSummary,
  type ScreenDiffResponse,
  type ScreenFilter,
  type ScreenSort,
  type StyleExposureResponse,
  type StyleTag,
} from '../api'
import { fmtNum, fmtPct, fmtTime, pctClass } from '../format'
import EChart from '../components/EChart.vue'
import GaugeArc from '../components/GaugeArc.vue'
import { CHART_COLORS, tooltipStyle } from '../chartTheme'

type FilterState = {
  style: '' | StyleTag
  riskLevel: '' | RiskLevel
  horizonDays: 5 | 20
  industry: string
  sortBy: ScreenSort
}

type BadgeMeta = { label: string; cls: string; title: string }

const router = useRouter()
const loading = ref(true)
const running = ref(false)
const error = ref('')
const partialErrors = ref<Record<string, string>>({})
const result = ref<LatestScreenResponse | null>(null)
const screenDiff = ref<ScreenDiffResponse | null>(null)
const weights = ref<FactorWeightsResponse | null>(null)
const exposure = ref<StyleExposureResponse | null>(null)
const industries = ref<string[]>([])
const page = ref(1)
const appliedFilterSignature = ref('')
let requestVersion = 0

const filters = ref<FilterState>({
  style: '',
  riskLevel: '',
  horizonDays: 20,
  industry: '',
  sortBy: 'score',
})

const PAGE_SIZE = 10
const STYLE_ORDER: StyleTag[] = ['growth', 'value', 'defensive', 'balanced']
const STYLE_META: Record<StyleTag, { label: string; color: string }> = {
  growth: { label: '成长', color: CHART_COLORS.cyan },
  value: { label: '价值', color: CHART_COLORS.up },
  defensive: { label: '防御', color: CHART_COLORS.purple },
  balanced: { label: '均衡', color: CHART_COLORS.slate },
}
const FACTOR_LABELS: Record<string, string> = {
  net_profit_yoy: '净利润同比',
  pe_percentile: '市盈率分位',
  revenue_yoy: '营收同比',
  net_inflow_5d: '5日资金流',
  momentum_20d: '20日动量',
  sector_strength: '行业强度',
  ocf_to_profit: '现金流质量',
  turnover_change_5d: '5日换手变化',
}

const candidates = computed(() => result.value?.candidates ?? [])
const filterSignature = computed(() => JSON.stringify(filters.value))
const filtersDirty = computed(
  () => Boolean(result.value && appliedFilterSignature.value !== filterSignature.value),
)
const appliedHorizonDays = computed<5 | 20>(() =>
  Number(result.value?.filters?.horizon_days) === 5 ? 5 : 20,
)

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function average(values: unknown[]): number | null {
  const valid = values
    .map(finiteNumber)
    .filter((value): value is number => value !== null)
  return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null
}

const avgScore = computed(() => average(candidates.value.map((item) => item.score)))
function selectedExpectedReturn(candidate: ScreenCandidateSummary): number | null {
  return appliedHorizonDays.value === 5
    ? candidate.expected_return_5d
    : candidate.expected_return_20d
}

function selectedConfidence(candidate: ScreenCandidateSummary): number | null {
  return appliedHorizonDays.value === 5 ? candidate.confidence_5d : candidate.confidence_20d
}

function selectedProbability(candidate: ScreenCandidateSummary): number | null {
  return appliedHorizonDays.value === 5 ? candidate.p_up_5d : candidate.p_up_20d
}

const avgExpectedReturn = computed(() =>
  average(candidates.value.map(selectedExpectedReturn)),
)
const avgWinRate = computed(() => average(candidates.value.map((item) => item.win_rate_20d)))
const confidenceValues = computed(() =>
  candidates.value
    .map((item) => finiteNumber(selectedConfidence(item)))
    .filter((value): value is number => value !== null),
)
const avgConfidence = computed(() =>
  confidenceValues.value.length
    ? confidenceValues.value.reduce((sum, value) => sum + value, 0) /
      confidenceValues.value.length
    : null,
)

const diffSnapshotValid = computed(() => {
  if (!result.value || !screenDiff.value) return false
  if (screenDiff.value.current_run_id !== result.value.id) return false
  const symbols = candidates.value.map((item) => item.symbol)
  if (new Set(symbols).size !== symbols.length) return false
  const current = new Set(symbols)
  if (screenDiff.value.new.some((symbol) => !current.has(symbol))) return false
  return screenDiff.value.new.length + screenDiff.value.stayed === candidates.value.length
})

const newSymbols = computed(() => new Set(screenDiff.value?.new || []))

function recommendationMeta(scoreValue: unknown): BadgeMeta {
  const score = finiteNumber(scoreValue)
  const title = '仅按综合因子评分分桶，不构成交易建议'
  if (score === null) return { label: '暂无结论', cls: 'gray', title }
  if (score >= 80) return { label: '强势买入', cls: 'green', title }
  if (score >= 65) return { label: '买入', cls: 'green', title }
  if (score >= 50) return { label: '观察', cls: 'blue', title }
  return { label: '减持', cls: 'red', title }
}

function statusMeta(candidate: ScreenCandidateSummary): BadgeMeta {
  if (!diffSnapshotValid.value || !screenDiff.value) {
    return { label: '—', cls: 'gray', title: '缺少同批次选股 diff，无法判断状态' }
  }
  if (screenDiff.value.baseline_missing) {
    return { label: '首次运行', cls: 'gray', title: '暂无相同筛选条件的历史基线' }
  }
  if (newSymbols.value.has(candidate.symbol)) {
    return { label: '新入选', cls: 'green', title: '相对上一同条件批次新进入候选集' }
  }
  return { label: '持有中', cls: 'blue', title: '仅表示连续入选，不代表真实持仓' }
}

const pagedCandidates = computed(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return candidates.value.slice(start, start + PAGE_SIZE)
})
const totalPages = computed(() => Math.max(1, Math.ceil(candidates.value.length / PAGE_SIZE)))
const pageNumbers = computed(() => {
  const total = totalPages.value
  if (total <= 5) return Array.from({ length: total }, (_, index) => index + 1)
  const start = Math.max(1, Math.min(page.value - 2, total - 4))
  return Array.from({ length: 5 }, (_, index) => start + index)
})

function goPage(target: number) {
  page.value = Math.max(1, Math.min(totalPages.value, target))
}

const factorRows = computed(() => {
  const entries = Object.entries(weights.value?.weights || {})
  const maxAbs = Math.max(0.000001, ...entries.map(([, value]) => Math.abs(Number(value))))
  return entries.map(([key, rawWeight]) => {
    const weight = Number(rawWeight)
    const pct = weight * 100
    return {
      key,
      label: FACTOR_LABELS[key] || key,
      weight,
      width: `${(Math.abs(weight) / maxAbs) * 100}%`,
      display: `${pct > 0 ? '+' : ''}${Number.isInteger(pct) ? pct.toFixed(0) : pct.toFixed(1)}%`,
    }
  })
})

const exposureOption = computed<Record<string, unknown>>(() => {
  const byStyle = new Map((exposure.value?.exposure || []).map((item) => [item.style, item]))
  return {
    animation: false,
    tooltip: {
      ...tooltipStyle,
      trigger: 'item',
      formatter: (item: any) => `${item.name}<br/>${item.value} 只 · ${Number(item.percent).toFixed(1)}%`,
    },
    title: {
      text: String(exposure.value?.total_candidates || 0),
      subtext: '候选',
      left: 'center',
      top: '34%',
      textStyle: { color: CHART_COLORS.text2, fontSize: 18, fontWeight: 700 },
      subtextStyle: { color: CHART_COLORS.text3, fontSize: 10 },
    },
    legend: {
      bottom: 0,
      textStyle: { color: CHART_COLORS.text2, fontSize: 10 },
      itemWidth: 8,
      itemHeight: 8,
    },
    series: [
      {
        type: 'pie',
        radius: ['53%', '73%'],
        center: ['50%', '43%'],
        label: { show: false },
        itemStyle: { borderColor: '#0a0f1c', borderWidth: 2 },
        data: STYLE_ORDER.map((style) => ({
          name: STYLE_META[style].label,
          value: byStyle.get(style)?.count || 0,
          itemStyle: { color: STYLE_META[style].color },
        })),
      },
    ],
  }
})

const industryMismatch = computed(() => {
  if (!filters.value.industry || filtersDirty.value) return false
  return candidates.value.some((candidate) => candidate.industry !== filters.value.industry)
})

async function optional<T>(key: string, promise: Promise<T>): Promise<T | null> {
  try {
    const value = await promise
    const next = { ...partialErrors.value }
    delete next[key]
    partialErrors.value = next
    return value
  } catch (exc: any) {
    partialErrors.value = {
      ...partialErrors.value,
      [key]: String(exc?.message || exc),
    }
    return null
  }
}

function hydrateFilters(snapshot: LatestScreenResponse) {
  const stored = snapshot.filters || {}
  const style = stored.style
  const riskLevel = stored.risk_level
  const sortBy = stored.sort_by
  const horizonDays = stored.horizon_days
  const storedIndustries = stored.industries
  filters.value = {
    style: STYLE_ORDER.includes(style as StyleTag) ? (style as StyleTag) : '',
    riskLevel: ['low', 'mid', 'high'].includes(String(riskLevel))
      ? (riskLevel as RiskLevel)
      : '',
    horizonDays: Number(horizonDays) === 5 ? 5 : 20,
    industry:
      Array.isArray(storedIndustries) && typeof storedIndustries[0] === 'string'
        ? storedIndustries[0]
        : '',
    sortBy: ['score', 'expected_return', 'win_rate'].includes(String(sortBy))
      ? (sortBy as ScreenSort)
      : 'score',
  }
  appliedFilterSignature.value = JSON.stringify(filters.value)
}

async function loadSnapshotExtras(runId: number, version: number) {
  const [diffResult, exposureResult] = await Promise.all([
    optional('diff', api.screenDiff()),
    optional('exposure', api.screenStyleExposure(runId)),
  ])
  if (version !== requestVersion) return
  screenDiff.value = diffResult
  exposure.value = exposureResult
}

async function loadInitial() {
  const version = ++requestVersion
  loading.value = true
  error.value = ''
  const [industryResult, weightResult, latestResult] = await Promise.all([
    optional('industries', api.metaIndustries()),
    optional('weights', api.factorWeights()),
    optional('latest', api.latestScreen()),
  ])
  if (version !== requestVersion) return
  industries.value = industryResult?.industries || []
  weights.value = weightResult
  if (latestResult) {
    result.value = latestResult
    hydrateFilters(latestResult)
    page.value = 1
    await loadSnapshotExtras(latestResult.id, version)
  } else {
    const latestError = partialErrors.value.latest
    if (latestError?.includes('暂无选股运行记录')) {
      const next = { ...partialErrors.value }
      delete next.latest
      partialErrors.value = next
    } else if (latestError) {
      error.value = `选股结果暂不可用：${latestError}`
    }
  }
  loading.value = false
}

function screenBody(): ScreenFilter {
  return {
    universe: 'all',
    industries: filters.value.industry ? [filters.value.industry] : null,
    style: filters.value.style || null,
    risk_level: filters.value.riskLevel || null,
    top_n: 50,
    sort_by: filters.value.sortBy,
    horizon_days: filters.value.horizonDays,
  }
}

async function run() {
  const version = ++requestVersion
  const submittedSignature = filterSignature.value
  running.value = true
  error.value = ''
  try {
    const body = screenBody()
    const response = await api.runScreen(body)
    if (version !== requestVersion) return
    const snapshot: LatestScreenResponse = {
      id: response.run_id,
      universe: body.universe,
      filters: body as unknown as Record<string, unknown>,
      provider: response.provider,
      model_version: response.model_version,
      requested: response.requested,
      succeeded: response.succeeded,
      failed: response.failed,
      candidates: response.candidates,
      created_at: response.generated_at,
    }
    result.value = snapshot
    appliedFilterSignature.value = submittedSignature
    page.value = 1
    await loadSnapshotExtras(response.run_id, version)
  } catch (exc: any) {
    if (version === requestVersion) error.value = `选股失败：${String(exc?.message || exc)}`
  } finally {
    if (version === requestVersion) running.value = false
  }
}

function resetFilters() {
  filters.value = {
    style: '',
    riskLevel: '',
    horizonDays: 20,
    industry: '',
    sortBy: 'score',
  }
}

function probability(value: unknown): string {
  const number = finiteNumber(value)
  return number === null ? '—' : `${(number * 100).toFixed(1)}%`
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return ''
  const text = String(value)
  const safe = typeof value === 'string' && /^[=+\-@]/.test(text.trimStart()) ? `'${text}` : text
  return `"${safe.replaceAll('"', '""')}"`
}

function shanghaiDateStamp(): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}${values.month}${values.day}`
}

function exportCsv() {
  if (!candidates.value.length) return
  const rows: unknown[][] = [
    ['排名', '代码', '名称', '综合评分', '20日胜率', '证监会行业', 'AI结论', '状态', '5日上涨概率', '20日上涨概率', '5日预期收益', '20日预期收益', '5日置信度', '20日置信度'],
    ...candidates.value.map((candidate) => [
      candidate.rank,
      candidate.symbol,
      candidate.display_name,
      candidate.score,
      finiteNumber(candidate.win_rate_20d),
      candidate.industry,
      recommendationMeta(candidate.score).label,
      statusMeta(candidate).label,
      finiteNumber(candidate.p_up_5d),
      finiteNumber(candidate.p_up_20d),
      finiteNumber(candidate.expected_return_5d),
      finiteNumber(candidate.expected_return_20d),
      finiteNumber(candidate.confidence_5d),
      finiteNumber(candidate.confidence_20d),
    ]),
  ]
  const csv = `\ufeff${rows.map((row) => row.map(csvCell).join(',')).join('\r\n')}`
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `screen_${shanghaiDateStamp()}.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000)
}

onMounted(loadInitial)
</script>

<template>
  <div>
    <div class="page-head screening-page-head">
      <h1>AI选股</h1>
      <span class="sub">全市场因子筛选 · 评分分桶仅供研究，不构成投资建议</span>
    </div>

    <form class="panel filter-panel" @submit.prevent="run">
      <div class="filter-grid">
        <label class="filter-field">
          <span>市场</span>
          <select class="input" disabled aria-label="市场，固定为A股">
            <option>A股</option>
          </select>
        </label>
        <label class="filter-field">
          <span>风格</span>
          <select v-model="filters.style" class="input" :disabled="running">
            <option value="">全部风格</option>
            <option value="growth">成长</option>
            <option value="value">价值</option>
            <option value="defensive">防御</option>
            <option value="balanced">均衡</option>
          </select>
        </label>
        <label class="filter-field">
          <span>风险等级</span>
          <select v-model="filters.riskLevel" class="input" :disabled="running">
            <option value="">全部风险</option>
            <option value="low">低风险</option>
            <option value="mid">中风险</option>
            <option value="high">高风险</option>
          </select>
        </label>
        <label class="filter-field">
          <span>持有周期</span>
          <select v-model.number="filters.horizonDays" class="input" :disabled="running">
            <option :value="5">短期（5日）</option>
            <option :value="20">中期（20日）</option>
          </select>
        </label>
        <label class="filter-field industry-field">
          <span>证监会行业</span>
          <select
            v-model="filters.industry"
            class="input"
            :disabled="running || Boolean(partialErrors.industries)"
          >
            <option value="">全部行业</option>
            <option v-for="industry in industries" :key="industry" :value="industry">
              {{ industry }}
            </option>
          </select>
        </label>
        <label class="filter-field">
          <span>排序方式</span>
          <select v-model="filters.sortBy" class="input" :disabled="running">
            <option value="score">综合评分</option>
            <option value="expected_return">{{ filters.horizonDays }}日预期收益</option>
            <option value="win_rate">20日历史胜率</option>
          </select>
        </label>
      </div>
      <div class="filter-actions">
        <span v-if="partialErrors.industries" class="xs down">行业选项暂不可用：{{ partialErrors.industries }}</span>
        <span v-else-if="filtersDirty" class="xs muted">条件已修改，运行后生效</span>
        <span v-else class="xs dim">全市场候选上限 50 · 当前周期 {{ filters.horizonDays }} 日</span>
        <button type="button" class="btn ghost" :disabled="running" @click="resetFilters">
          <RotateCcw :size="12" /> 重置
        </button>
        <button type="submit" class="btn primary" :disabled="running">
          <Play :size="12" /> {{ running ? '筛选中…' : '运行选股' }}
        </button>
      </div>
    </form>

    <div v-if="error" class="banner error screen-banner">{{ error }}</div>
    <div v-if="industryMismatch" class="banner error screen-banner">
      行业筛选结果与请求不一致，已停止将当前结果视为有效筛选。
    </div>
    <div
      v-if="result && !filtersDirty && filters.sortBy === 'win_rate' && avgWinRate === null"
      class="banner screen-banner"
    >
      当前批次暂无 20 日校准胜率；后端按综合评分回退排序，表格保留空值。
    </div>

    <div v-if="loading && !result" class="screen-layout">
      <div class="grid screen-main">
        <div class="screen-stats">
          <div v-for="index in 4" :key="index" class="skeleton" style="height: 92px" />
        </div>
        <div class="skeleton" style="height: 480px" />
      </div>
      <div class="grid screen-side">
        <div class="skeleton" style="height: 300px" />
        <div class="skeleton" style="height: 220px" />
        <div class="skeleton" style="height: 180px" />
      </div>
    </div>

    <div v-else-if="result" class="screen-layout">
      <div class="grid screen-main">
        <div class="screen-stats">
          <div class="stat-card">
            <div class="label">本次入选</div>
            <div class="value glow-cyan">{{ candidates.length }}</div>
            <div class="delta">
              <template v-if="diffSnapshotValid && !screenDiff?.baseline_missing">
                新入 {{ screenDiff?.new.length || 0 }} · 移出 {{ screenDiff?.dropped.length || 0 }}
              </template>
              <template v-else-if="screenDiff?.baseline_missing">首次同条件运行</template>
              <template v-else>历史对比暂不可用</template>
            </div>
          </div>
          <div class="stat-card">
            <div class="label">平均综合评分</div>
            <div class="value">{{ avgScore === null ? '—' : fmtNum(avgScore, 1) }}</div>
            <div class="delta">评分范围 0–100</div>
          </div>
          <div class="stat-card">
            <div class="label">平均{{ appliedHorizonDays }}日预期</div>
            <div class="value" :class="pctClass(avgExpectedReturn)">
              {{ fmtPct(avgExpectedReturn, 2, false) }}
            </div>
            <div class="delta">基线概率模型</div>
          </div>
          <div class="stat-card">
            <div class="label">平均20日胜率</div>
            <div class="value" :class="pctClass(avgWinRate)">{{ probability(avgWinRate) }}</div>
            <div class="delta">
              {{ avgWinRate === null ? '暂无历史校准样本' : 'Walk-Forward 校准口径' }}
            </div>
          </div>
        </div>

        <div class="panel candidate-panel">
          <div class="panel-title candidate-title">
            <span>
              候选列表
              <small class="extra mono">#{{ result.id }} · {{ fmtTime(result.created_at) }}</small>
            </span>
            <button type="button" class="btn ghost" :disabled="!candidates.length" @click="exportCsv">
              <Download :size="12" /> 导出 CSV
            </button>
          </div>
          <div class="table-scroll">
            <table class="tbl screening-table" aria-label="AI选股候选列表">
              <thead>
                <tr>
                  <th class="identity-col">代码 / 名称</th>
                  <th class="r">综合评分</th>
                  <th class="r">{{ appliedHorizonDays }}日概率</th>
                  <th class="r">{{ appliedHorizonDays }}日预期</th>
                  <th class="r">20日胜率</th>
                  <th>证监会行业</th>
                  <th title="仅按综合因子评分分桶，不构成交易建议">AI结论</th>
                  <th title="连续入选状态不代表真实持仓">状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="candidate in pagedCandidates" :key="candidate.symbol">
                  <td class="sym identity-col" @click="router.push(`/stock/${candidate.symbol}`)">
                    <div class="candidate-name">{{ candidate.display_name || candidate.symbol }}</div>
                    <div class="code">{{ candidate.symbol }}</div>
                  </td>
                  <td class="r">
                    <div class="score-cell">
                      <span class="num glow-cyan">{{ fmtNum(candidate.score, 1) }}</span>
                      <span class="score-bar"><i :style="{ width: `${candidate.score}%` }" /></span>
                    </div>
                  </td>
                  <td class="r num">{{ probability(selectedProbability(candidate)) }}</td>
                  <td class="r num" :class="pctClass(selectedExpectedReturn(candidate))">
                    {{ fmtPct(selectedExpectedReturn(candidate), 2, false) }}
                  </td>
                  <td class="r num">{{ probability(candidate.win_rate_20d) }}</td>
                  <td class="industry-cell" :title="candidate.industry || '行业主档缺失'">
                    {{ candidate.industry || '—' }}
                  </td>
                  <td>
                    <span
                      class="badge"
                      :class="recommendationMeta(candidate.score).cls"
                      :title="recommendationMeta(candidate.score).title"
                    >
                      {{ recommendationMeta(candidate.score).label }}
                    </span>
                  </td>
                  <td>
                    <span
                      class="badge"
                      :class="statusMeta(candidate).cls"
                      :title="statusMeta(candidate).title"
                    >
                      {{ statusMeta(candidate).label }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="table-foot">
            <span class="xs dim">
              共 {{ candidates.length }} 条 · 请求 {{ result.requested }} · 可评分 {{ result.succeeded }} ·
              “持有中”仅指连续入选
            </span>
            <div class="pagination" aria-label="候选列表分页">
              <button type="button" class="page-btn" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
              <button
                v-for="number in pageNumbers"
                :key="number"
                type="button"
                class="page-btn page-number"
                :class="{ on: page === number }"
                :aria-current="page === number ? 'page' : undefined"
                @click="goPage(number)"
              >
                {{ number }}
              </button>
              <span class="page-compact num">{{ page }} / {{ totalPages }}</span>
              <button type="button" class="page-btn" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
            </div>
          </div>
          <div v-if="Object.keys(result.failed || {}).length" class="xs dim failure-note">
            {{ Object.keys(result.failed).length }} 只标的缺少本地预测输入；概率字段保留为空。
          </div>
        </div>
      </div>

      <div class="grid screen-side">
        <div class="panel factor-panel">
          <div class="panel-title">
            本次选股逻辑
            <span class="extra mono">{{ weights ? `${weights.profile} · ${weights.version}` : '真实 YAML' }}</span>
          </div>
          <div v-if="factorRows.length" class="factor-list">
            <div v-for="factor in factorRows" :key="factor.key" class="factor-row">
              <div class="kv factor-head">
                <span class="k">{{ factor.label }} <small v-if="factor.weight < 0">（反向）</small></span>
                <span class="num" :class="factor.weight < 0 ? 'reverse-text' : ''">{{ factor.display }}</span>
              </div>
              <div class="score-bar factor-bar" :class="{ reverse: factor.weight < 0 }">
                <i :style="{ width: factor.width }" />
              </div>
            </div>
          </div>
          <div v-else class="empty-hint">
            {{ partialErrors.weights ? `因子权重暂不可用：${partialErrors.weights}` : '暂无因子权重' }}
          </div>
        </div>

        <div class="panel exposure-panel">
          <div class="panel-title">组合风格暴露 <span class="extra">候选快照</span></div>
          <EChart
            v-if="exposure && exposure.run_id === result.id && exposure.total_candidates > 0"
            :option="exposureOption"
            height="190px"
          />
          <div v-else class="empty-hint">
            {{ partialErrors.exposure
              ? `风格暴露暂不可用：${partialErrors.exposure}`
              : exposure?.total_candidates === 0
                ? '当前候选为空，暂无风格暴露'
                : '风格暴露批次校验中' }}
          </div>
        </div>

        <div class="panel confidence-panel">
          <div class="panel-title">AI信心 <span class="extra">{{ appliedHorizonDays }}日基线</span></div>
          <GaugeArc
            v-if="avgConfidence !== null"
            :value="avgConfidence"
            format="percent"
            size="112px"
            :label="`有效覆盖 ${confidenceValues.length}/${candidates.length}`"
          />
          <div v-else class="empty-hint">当前候选暂无置信度数据</div>
          <div class="xs dim confidence-note">概率模型置信度均值，不代表收益保证。</div>
        </div>
      </div>
    </div>

    <div v-else-if="!loading" class="empty-hint">尚无选股结果，点击「运行选股」生成首个全市场批次</div>
  </div>
</template>

<style scoped>
.filter-panel {
  margin-bottom: var(--s3);
}

.filter-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(112px, 1fr));
  gap: var(--s3);
}

.filter-field {
  display: grid;
  min-width: 0;
  gap: 5px;
  color: var(--text-2);
  font-size: 11px;
}

.filter-field .input {
  width: 100%;
  min-width: 0;
}

.filter-field .input:disabled {
  color: var(--text-2);
  cursor: not-allowed;
  opacity: 0.68;
}

.filter-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--s2);
  margin-top: var(--s3);
}

.filter-actions > span {
  min-width: 0;
  margin-right: auto;
}

.screen-banner {
  margin-bottom: var(--s3);
}

.screen-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 310px;
  align-items: start;
  gap: var(--s3);
}

.screen-main,
.screen-side,
.candidate-panel {
  min-width: 0;
}

.screen-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--s3);
}

.candidate-title {
  gap: var(--s3);
}

.candidate-title > span {
  min-width: 0;
}

.candidate-title small {
  margin-left: 7px;
  font-weight: 400;
}

.table-scroll {
  max-width: 100%;
  overflow-x: auto;
}

.screening-table {
  min-width: 990px;
}

.screening-table .identity-col {
  position: sticky;
  left: 0;
  z-index: 2;
  min-width: 150px;
  background: var(--surface-1);
}

.screening-table thead .identity-col {
  z-index: 3;
  background: var(--surface-2);
}

.candidate-name {
  max-width: 132px;
  overflow: hidden;
  color: var(--text-1);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.score-cell {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.score-cell .score-bar {
  width: 58px;
}

.industry-cell {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.table-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--s3);
  padding: 10px 0 5px;
}

.pagination {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.page-btn {
  min-width: 29px;
  height: 28px;
  border: 1px solid var(--line-1);
  background: transparent;
  color: var(--text-2);
  border-radius: var(--r-sm);
  padding: 0 8px;
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
}

.page-btn:hover:not(:disabled),
.page-btn.on {
  border-color: rgba(96, 165, 250, 0.42);
  background: rgba(59, 130, 246, 0.15);
  color: var(--text-1);
}

.page-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.page-compact {
  display: none;
  color: var(--text-2);
  font-size: 11px;
}

.failure-note {
  padding-top: 6px;
  border-top: 1px solid var(--line-1);
}

.factor-list {
  display: grid;
  gap: 10px;
}

.factor-row {
  min-width: 0;
}

.factor-head {
  padding: 0 0 4px;
}

.factor-head small {
  color: var(--purple);
  font-size: 9.5px;
}

.factor-bar.reverse > i {
  background: linear-gradient(90deg, #7c3aed, var(--purple));
}

.reverse-text {
  color: var(--purple);
}

.confidence-note {
  margin-top: 7px;
  text-align: center;
}

@media (max-width: 1400px) {
  .filter-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 1180px) {
  .screen-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .screen-side {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 900px) {
  .screen-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .screen-side {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .factor-panel {
    grid-column: 1 / -1;
  }
}

@media (max-width: 620px) {
  .filter-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .industry-field {
    grid-column: 1 / -1;
  }

  .filter-actions {
    flex-wrap: wrap;
  }

  .filter-actions > span {
    width: 100%;
    margin-right: 0;
  }
}

@media (max-width: 520px) {
  .screening-page-head {
    display: block;
  }

  .screening-page-head .sub {
    display: block;
    margin-top: 2px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .filter-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .screen-stats {
    grid-template-columns: minmax(0, 1fr);
  }

  .industry-field {
    grid-column: auto;
  }

  .filter-actions .btn {
    flex: 1 1 0;
    white-space: nowrap;
  }

  .screen-side {
    grid-template-columns: minmax(0, 1fr);
  }

  .factor-panel {
    grid-column: auto;
  }

  .candidate-title {
    align-items: flex-start;
  }

  .candidate-title small {
    display: block;
    margin: 2px 0 0;
  }

  .table-foot {
    align-items: flex-start;
    flex-direction: column;
  }

  .pagination {
    justify-content: space-between;
    width: 100%;
  }

  .page-number {
    display: none;
  }

  .page-compact {
    display: inline;
  }
}
</style>
