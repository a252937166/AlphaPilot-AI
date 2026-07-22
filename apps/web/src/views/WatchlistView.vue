<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Activity,
  CalendarDays,
  Check,
  FileText,
  FolderInput,
  Plus,
  RefreshCw,
  Trash2,
  Zap,
} from 'lucide-vue-next'
import {
  api,
  type PortfolioOverviewResponse,
  type ThesisState,
  type WatchlistEventCategory,
  type WatchlistRecentEvent,
  type WatchlistSummaryResponse,
  type WatchlistTrackRow,
} from '../api'
import { SERIES_PALETTE, tooltipStyle } from '../chartTheme'
import ConfRing from '../components/ConfRing.vue'
import EChart from '../components/EChart.vue'
import Sparkline from '../components/Sparkline.vue'
import { actionMeta, fmtAmount, fmtNum, fmtPct, fmtTime, pctClass } from '../format'

type SummaryCard = {
  key: ThesisState
  label: string
  deltaLabel: string
  color: string
  glow: string
}

type PortfolioSlice = {
  industry: string
  market_value: number
  weight: number
}

const router = useRouter()
const loading = ref(true)
const reloading = ref(false)
const busyAction = ref('')
const error = ref('')
const operationMessage = ref('')
const rows = ref<WatchlistTrackRow[]>([])
const summary = ref<WatchlistSummaryResponse | null>(null)
const portfolio = ref<PortfolioOverviewResponse | null>(null)
const partialErrors = ref({ summary: '', portfolio: '' })
const activeGroup = ref('all')
const selectedSymbols = ref<string[]>([])
const batchGroupName = ref('')
const deleteArmed = ref(false)
let loadEpoch = 0

const form = ref({
  symbol: '',
  display_name: '',
  cost_price: '',
  quantity: '',
  group_name: 'core',
})

const GROUP_LABELS: Record<string, string> = {
  all: '全部',
  core: '核心持仓',
  watch: '观察池',
  priority: '高优先级',
}

const SUMMARY_CARDS: SummaryCard[] = [
  { key: 'strengthened', label: '逻辑强化', deltaLabel: '↑', color: '#34d399', glow: 'green' },
  { key: 'unchanged', label: '逻辑不变', deltaLabel: '—', color: '#60a5fa', glow: 'blue' },
  { key: 'weakened', label: '逻辑转弱', deltaLabel: '↓', color: '#f87171', glow: 'red' },
]

const groups = computed(() => {
  const names = new Set(rows.value.map((row) => row.group_name || 'core'))
  return ['all', ...Array.from(names).sort((a, b) => a.localeCompare(b, 'zh-CN'))]
})

const visibleRows = computed(() =>
  activeGroup.value === 'all'
    ? rows.value
    : rows.value.filter((row) => (row.group_name || 'core') === activeGroup.value),
)

const selectedSet = computed(() => new Set(selectedSymbols.value))
const selectedCount = computed(() => selectedSymbols.value.length)
const interactionLocked = computed(
  () => loading.value || reloading.value || Boolean(busyAction.value),
)
const allVisibleSelected = computed(
  () => visibleRows.value.length > 0 && visibleRows.value.every((row) => selectedSet.value.has(row.symbol)),
)
const someVisibleSelected = computed(
  () => visibleRows.value.some((row) => selectedSet.value.has(row.symbol)) && !allVisibleSelected.value,
)

const focusRows = computed(() =>
  rows.value
    .filter((row) => row.alert_action && !['HOLD', 'WATCH'].includes(row.alert_action))
    .slice(0, 5),
)

const portfolioPresentation = computed(() => {
  const stored = portfolio.value
  const audited = (stored?.industry_distribution ?? []).filter(
    (item) => finiteNumber(item.market_value) !== null && Number(item.market_value) > 0,
  )
  if (stored?.available && audited.length) {
    const tradeDate = stored.snapshot?.trade_date
    return {
      slices: audited,
      total: audited.reduce((sum, item) => sum + Number(item.market_value), 0),
      basis: `${tradeDate ? `截至 ${tradeDate} · ` : ''}${stored.valuation_basis_label || '富途模拟账户持仓市值'}`,
      warning: stored.warning,
      isFallback: false,
    }
  }

  const totals = new Map<string, number>()
  let pricedPositions = 0
  for (const row of rows.value) {
    const cost = finiteNumber(row.cost_price)
    const quantity = finiteNumber(row.quantity)
    if (cost === null || quantity === null || cost <= 0 || quantity <= 0) continue
    const industry = row.industry?.trim() || '未分类'
    totals.set(industry, (totals.get(industry) ?? 0) + cost * quantity)
    pricedPositions += 1
  }
  const total = Array.from(totals.values()).reduce((sum, value) => sum + value, 0)
  const slices: PortfolioSlice[] = Array.from(totals.entries())
    .map(([industry, marketValue]) => ({
      industry,
      market_value: marketValue,
      weight: total > 0 ? marketValue / total : 0,
    }))
    .sort((a, b) => b.market_value - a.market_value)
  const unavailableReason =
    partialErrors.value.portfolio ||
    stored?.warning ||
    (stored?.available ? '富途模拟组合当前无持仓市值' : '尚无富途模拟组合快照')
  return {
    slices,
    total,
    basis: pricedPositions ? `自选股成本价 × 数量（${pricedPositions} 项）` : '暂无可计算的持仓口径',
    warning: pricedPositions ? `${unavailableReason}；当前使用成本口径回退。` : unavailableReason,
    isFallback: true,
  }
})

const portfolioAriaLabel = computed(() => {
  const presentation = portfolioPresentation.value
  const detail = presentation.slices
    .map((item) => `${item.industry} ${(item.weight * 100).toFixed(1)}%`)
    .join('；')
  return `持仓行业配置，总市值 ${fmtAmount(presentation.total)}。估值口径：${presentation.basis}${detail ? `。${detail}` : ''}`
})

const portfolioDonut = computed(() => {
  const data = portfolioPresentation.value.slices
  return {
    animation: false,
    tooltip: {
      ...tooltipStyle,
      formatter: (item: { name: string; value: number; percent: number }) =>
        `${item.name}<br/>${fmtAmount(item.value)} · ${item.percent.toFixed(1)}%`,
    },
    title: {
      text: fmtAmount(portfolioPresentation.value.total),
      subtext: '配置市值',
      left: '31%',
      top: '37%',
      textAlign: 'center',
      textStyle: { color: '#eef2fa', fontSize: 14, fontFamily: "ui-monospace,'SF Mono',Menlo,monospace" },
      subtextStyle: { color: '#9aa7c4', fontSize: 10 },
    },
    legend: {
      type: 'scroll',
      orient: 'vertical',
      right: 2,
      top: 'middle',
      width: '48%',
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { color: '#9aa7c4', fontSize: 10 },
      pageIconColor: '#60a5fa',
      pageIconInactiveColor: '#475569',
      pageTextStyle: { color: '#9aa7c4' },
      formatter: (name: string) => (name.length > 8 ? `${name.slice(0, 8)}…` : name),
    },
    series: [
      {
        type: 'pie',
        radius: ['55%', '73%'],
        center: ['31%', '48%'],
        label: { show: false },
        itemStyle: { borderColor: '#0a101e', borderWidth: 2 },
        data: data.map((item, index) => ({
          name: item.industry,
          value: item.market_value,
          itemStyle: { color: SERIES_PALETTE[index % SERIES_PALETTE.length] },
        })),
      },
    ],
  }
})

function finiteNumber(value: unknown): number | null {
  const number = Number(value)
  return value === null || value === undefined || !Number.isFinite(number) ? null : number
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason)
}

function groupLabel(group: string): string {
  return GROUP_LABELS[group] || group
}

function groupCount(group: string): number {
  if (group === 'all') return rows.value.length
  return rows.value.filter((row) => (row.group_name || 'core') === group).length
}

function thesisMeta(state: ThesisState | string | null | undefined) {
  if (state === 'weakened') return { label: '逻辑转弱', cls: 'red' }
  if (state === 'strengthened') return { label: '逻辑强化', cls: 'green' }
  return { label: '逻辑不变', cls: 'blue' }
}

function summarySeries(state: ThesisState): number[] {
  return (summary.value?.transitions_7d ?? []).map((point) => Number(point[state]) || 0)
}

function summaryAriaLabel(card: SummaryCard): string {
  const points = (summary.value?.transitions_7d ?? [])
    .map((point) => `${point.date} ${Number(point[card.key]) || 0}`)
    .join('，')
  return `${card.label}近七日转入趋势${points ? `：${points}` : '：暂无数据'}`
}

function transitionTotal(state: ThesisState): number {
  return summarySeries(state).reduce((total, value) => total + value, 0)
}

function eventIcon(category: WatchlistEventCategory) {
  if (category === 'announcement') return FileText
  if (category === 'calendar') return CalendarDays
  if (category === 'capital') return Zap
  return Activity
}

function eventClass(category: WatchlistEventCategory): string {
  return category === 'announcement'
    ? 'announcement'
    : category === 'calendar'
      ? 'calendar'
      : category === 'capital'
        ? 'capital'
        : 'other'
}

function eventTooltip(event: WatchlistRecentEvent): string {
  const categoryLabel =
    event.category === 'announcement'
      ? '公告'
      : event.category === 'calendar'
        ? '日历'
        : event.category === 'capital'
          ? '资金'
          : '事件'
  return `${event.title}\n${fmtTime(event.occurred_at)} · ${categoryLabel}`
}

function eventAriaLabel(event: WatchlistRecentEvent): string {
  return `${eventTooltip(event).replace('\n', '，')}，查看个股事件`
}

function openStockEvents(symbol: string): void {
  void router.push({ path: `/stock/${symbol}`, hash: '#stock-events' })
}

function toggleVisibleSelection(): void {
  deleteArmed.value = false
  const visible = visibleRows.value.map((row) => row.symbol)
  if (allVisibleSelected.value) {
    const visibleSet = new Set(visible)
    selectedSymbols.value = selectedSymbols.value.filter((symbol) => !visibleSet.has(symbol))
  } else {
    selectedSymbols.value = Array.from(new Set([...selectedSymbols.value, ...visible]))
  }
}

function onRowSelectionChange(): void {
  deleteArmed.value = false
  operationMessage.value = ''
}

async function load(showSkeleton = true): Promise<void> {
  const epoch = ++loadEpoch
  loading.value = showSkeleton
  reloading.value = !showSkeleton
  selectedSymbols.value = []
  deleteArmed.value = false
  operationMessage.value = ''
  error.value = ''
  partialErrors.value = { summary: '', portfolio: '' }
  try {
    const [trackResult, summaryResult, portfolioResult] = await Promise.allSettled([
      api.watchlistTrack(),
      api.watchlistSummary(),
      api.portfolioOverview(),
    ])
    if (epoch !== loadEpoch) return

    if (trackResult.status === 'fulfilled') {
      rows.value = (trackResult.value.rows ?? []).map((row) => ({
        ...row,
        industry: row.industry || '未分类',
        recent_events: row.recent_events ?? [],
      }))
    } else {
      rows.value = []
      error.value = `自选列表加载失败：${errorMessage(trackResult.reason)}`
    }
    if (summaryResult.status === 'fulfilled') summary.value = summaryResult.value
    else {
      summary.value = null
      partialErrors.value.summary = errorMessage(summaryResult.reason)
    }
    if (portfolioResult.status === 'fulfilled') portfolio.value = portfolioResult.value
    else {
      portfolio.value = null
      partialErrors.value.portfolio = errorMessage(portfolioResult.reason)
    }

    const symbols = new Set(rows.value.map((row) => row.symbol))
    selectedSymbols.value = selectedSymbols.value.filter((symbol) => symbols.has(symbol))
    if (!groups.value.includes(activeGroup.value)) activeGroup.value = 'all'
  } finally {
    if (epoch === loadEpoch) {
      loading.value = false
      reloading.value = false
    }
  }
}

async function addItem(): Promise<void> {
  if (interactionLocked.value) return
  const symbol = form.value.symbol.trim().replace(/^(SH|SZ)\./i, '')
  if (!/^\d{6}$/.test(symbol)) {
    error.value = '请输入 6 位 A 股代码。'
    return
  }
  const cost = form.value.cost_price ? Number(form.value.cost_price) : undefined
  const quantity = form.value.quantity ? Number(form.value.quantity) : undefined
  if (cost !== undefined && (!Number.isFinite(cost) || cost <= 0)) {
    error.value = '成本价必须是大于 0 的数字。'
    return
  }
  if (quantity !== undefined && (!Number.isFinite(quantity) || quantity < 0)) {
    error.value = '持有数量必须是非负数字。'
    return
  }

  busyAction.value = 'add'
  error.value = ''
  operationMessage.value = ''
  try {
    await api.watchlistUpsert({
      symbol,
      display_name: form.value.display_name.trim() || undefined,
      cost_price: cost,
      quantity,
      group_name: form.value.group_name.trim() || 'core',
    })
    operationMessage.value = `${symbol} 已加入追踪。`
    form.value = { symbol: '', display_name: '', cost_price: '', quantity: '', group_name: 'core' }
    await load(false)
  } catch (reason: unknown) {
    error.value = `加入失败：${errorMessage(reason)}`
  } finally {
    busyAction.value = ''
  }
}

async function moveSelected(): Promise<void> {
  const groupName = batchGroupName.value.trim()
  if (interactionLocked.value || !selectedCount.value || !groupName) return
  const targets = [...selectedSymbols.value]
  busyAction.value = 'move'
  error.value = ''
  operationMessage.value = ''
  const failedSymbols: string[] = []
  try {
    for (const symbol of targets) {
      try {
        await api.watchlistUpsert({ symbol, group_name: groupName })
      } catch {
        failedSymbols.push(symbol)
      }
    }
    await load(false)
    const visibleSymbols = new Set(visibleRows.value.map((row) => row.symbol))
    selectedSymbols.value = failedSymbols.filter((symbol) => visibleSymbols.has(symbol))
    if (failedSymbols.length) {
      error.value = `已移动 ${targets.length - failedSymbols.length} 项；${failedSymbols.join('、')} 移动失败，列表已重新同步。`
    } else {
      operationMessage.value = `已新建或更新分组“${groupName}”，并移入 ${targets.length} 项。`
      batchGroupName.value = ''
    }
  } finally {
    busyAction.value = ''
  }
}

async function deleteSelected(): Promise<void> {
  if (interactionLocked.value || !selectedCount.value) return
  if (!deleteArmed.value) {
    deleteArmed.value = true
    operationMessage.value = `尚未删除。请再次确认删除 ${selectedCount.value} 项。`
    return
  }
  const targets = [...selectedSymbols.value]
  busyAction.value = 'delete'
  error.value = ''
  operationMessage.value = ''
  const failedSymbols: string[] = []
  try {
    for (const symbol of targets) {
      try {
        await api.watchlistDelete(symbol)
      } catch {
        failedSymbols.push(symbol)
      }
    }
    await load(false)
    deleteArmed.value = false
    const visibleSymbols = new Set(visibleRows.value.map((row) => row.symbol))
    selectedSymbols.value = failedSymbols.filter((symbol) => visibleSymbols.has(symbol))
    if (failedSymbols.length) {
      error.value = `已删除 ${targets.length - failedSymbols.length} 项；${failedSymbols.join('、')} 删除失败，列表已重新同步。`
    } else {
      operationMessage.value = `已删除 ${targets.length} 项。`
      selectedSymbols.value = []
    }
  } finally {
    busyAction.value = ''
  }
}

async function recomputeSelected(): Promise<void> {
  if (interactionLocked.value || !selectedCount.value) return
  const targets = [...selectedSymbols.value]
  busyAction.value = 'recompute'
  error.value = ''
  operationMessage.value = ''
  try {
    await api.refreshAlerts(targets)
    await load(false)
    operationMessage.value = `已重算 ${targets.length} 项，摘要与提醒已刷新。`
  } catch (reason: unknown) {
    error.value = `批量重算失败：${errorMessage(reason)}`
  } finally {
    busyAction.value = ''
  }
}

watch(activeGroup, () => {
  selectedSymbols.value = []
  deleteArmed.value = false
  operationMessage.value = ''
})

onMounted(() => void load())
</script>

<template>
  <div class="watchlist-page">
    <div class="page-head watchlist-head">
      <div>
        <h1>自选追踪</h1>
        <div class="sub">逻辑漂移、事件与持仓口径统一追踪</div>
      </div>
      <button
        class="btn ghost reload-button"
        type="button"
        :disabled="loading || reloading || Boolean(busyAction)"
        title="刷新自选数据"
        aria-label="刷新自选数据"
        @click="load(false)"
      >
        <RefreshCw :size="14" :class="{ spin: reloading }" />
        <span>刷新</span>
      </button>
    </div>

    <div v-if="error" class="banner error watchlist-banner" role="alert">{{ error }}</div>

    <section class="summary-grid" aria-label="投资逻辑七日摘要">
      <article
        v-for="card in SUMMARY_CARDS"
        :key="card.key"
        class="summary-card"
        :class="`summary-${card.glow}`"
      >
        <div class="summary-copy">
          <span>{{ card.label }}</span>
          <strong class="num" :class="`glow-${card.glow}`">
            {{ summary ? summary[card.key] : '—' }}
          </strong>
          <small v-if="summary" class="num">
            {{ card.deltaLabel }}{{ transitionTotal(card.key) }} · 7日转入
          </small>
          <small v-else>{{ loading ? '读取摘要…' : '摘要暂不可用' }}</small>
        </div>
        <Sparkline
          v-if="summarySeries(card.key).length"
          class="summary-sparkline"
          :data="summarySeries(card.key)"
          :color="card.color"
          height="48px"
          :aria-label="summaryAriaLabel(card)"
        />
      </article>
    </section>
    <div v-if="partialErrors.summary" class="partial-warning" role="status">
      逻辑摘要暂不可用：{{ partialErrors.summary }}
    </div>

    <section class="panel add-panel" aria-labelledby="add-watch-title">
      <div class="panel-title">
        <span id="add-watch-title">加入追踪</span>
        <span class="extra">分组名称可直接输入，不限制预设值</span>
      </div>
      <form class="add-form" @submit.prevent="addItem">
        <label>
          <span>股票代码</span>
          <input v-model="form.symbol" class="input mono" inputmode="numeric" placeholder="600519" maxlength="9" :disabled="interactionLocked" />
        </label>
        <label>
          <span>名称</span>
          <input v-model="form.display_name" class="input" placeholder="可选" :disabled="interactionLocked" />
        </label>
        <label>
          <span>成本价</span>
          <input v-model="form.cost_price" class="input num" inputmode="decimal" placeholder="可选" :disabled="interactionLocked" />
        </label>
        <label>
          <span>数量</span>
          <input v-model="form.quantity" class="input num" inputmode="decimal" placeholder="可选" :disabled="interactionLocked" />
        </label>
        <label class="group-field">
          <span>分组</span>
          <input v-model="form.group_name" class="input" list="watchlist-groups" placeholder="输入任意分组" :disabled="interactionLocked" />
        </label>
        <button class="btn primary add-button" type="submit" :disabled="interactionLocked">
          <Plus :size="14" /> {{ busyAction === 'add' ? '加入中…' : '加入追踪' }}
        </button>
      </form>
      <datalist id="watchlist-groups">
        <option v-for="group in groups.filter((item) => item !== 'all')" :key="group" :value="group">
          {{ groupLabel(group) }}
        </option>
      </datalist>
    </section>

    <nav class="group-tabs" aria-label="自选分组">
      <button
        v-for="group in groups"
        :key="group"
        class="group-tab"
        :class="{ on: activeGroup === group }"
        type="button"
        :disabled="interactionLocked"
        :aria-current="activeGroup === group ? 'page' : undefined"
        @click="activeGroup = group"
      >
        {{ groupLabel(group) }} <span class="num">{{ groupCount(group) }}</span>
      </button>
    </nav>

    <div class="watchlist-layout">
      <section class="panel table-panel" aria-labelledby="tracked-title">
        <div class="panel-title tracked-title-row">
          <span id="tracked-title">追踪明细 <b class="num">{{ visibleRows.length }}</b></span>
          <span class="extra">已选 {{ selectedCount }} 项</span>
        </div>

        <div class="batch-toolbar" aria-label="批量管理">
          <label class="batch-selection">
            <input
              type="checkbox"
              :checked="allVisibleSelected"
              :indeterminate="someVisibleSelected"
              :disabled="interactionLocked"
              :aria-label="allVisibleSelected ? '取消选择当前分组全部股票' : '选择当前分组全部股票'"
              @change="toggleVisibleSelection"
            />
            <span>{{ selectedCount ? `已选 ${selectedCount} 项` : '选择股票后可批量管理' }}</span>
          </label>
          <div class="batch-group">
            <input
              v-model="batchGroupName"
              class="input"
              type="text"
              maxlength="32"
              placeholder="新分组名称"
              aria-label="新分组名称"
              :disabled="interactionLocked"
            />
            <button
              class="btn"
              type="button"
              :disabled="!selectedCount || !batchGroupName.trim() || interactionLocked"
              @click="moveSelected"
            >
              <FolderInput :size="13" /> {{ busyAction === 'move' ? '移动中…' : '新建并移入' }}
            </button>
          </div>
          <div class="batch-actions">
            <button
              class="btn"
              type="button"
              :disabled="!selectedCount || interactionLocked"
              @click="recomputeSelected"
            >
              <Zap :size="13" /> {{ busyAction === 'recompute' ? '重算中…' : '重算' }}
            </button>
            <button
              class="btn danger"
              type="button"
              :disabled="!selectedCount || interactionLocked"
              @click="deleteSelected"
            >
              <Check v-if="deleteArmed" :size="13" />
              <Trash2 v-else :size="13" />
              {{ deleteArmed ? `确认删除 ${selectedCount} 项` : '删除' }}
            </button>
          </div>
        </div>
        <p v-if="operationMessage" class="operation-message" role="status" aria-live="polite">
          {{ operationMessage }}
        </p>

        <div v-if="loading" class="table-loading" aria-label="自选列表加载中">
          <div v-for="index in 5" :key="index" class="skeleton" />
        </div>
        <div v-else-if="visibleRows.length" class="table-scroll">
          <table class="tbl watchlist-table">
            <thead>
              <tr>
                <th class="check-cell"><span class="sr-only">选择</span></th>
                <th>代码 / 名称</th>
                <th class="r">最新价</th>
                <th class="r">成本</th>
                <th class="r">盈亏</th>
                <th>当前信号</th>
                <th>置信度</th>
                <th class="r">20日预期</th>
                <th>逻辑状态</th>
                <th>最新事件</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in visibleRows" :key="row.symbol" :class="{ selected: selectedSet.has(row.symbol) }">
                <td class="check-cell">
                  <label class="check-hit">
                    <input
                      v-model="selectedSymbols"
                      type="checkbox"
                      :value="row.symbol"
                      :disabled="interactionLocked"
                      :aria-label="`选择 ${row.display_name || row.symbol}`"
                      @change="onRowSelectionChange"
                    />
                  </label>
                </td>
                <td>
                  <button class="stock-link" type="button" :disabled="interactionLocked" @click="router.push(`/stock/${row.symbol}`)">
                    <span>{{ row.display_name || row.symbol }}</span>
                    <small class="mono">{{ row.symbol }}</small>
                  </button>
                </td>
                <td class="r">
                  <div class="num">{{ fmtNum(row.last) }}</div>
                  <div class="xs num" :class="pctClass(row.change_pct)">{{ fmtPct(row.change_pct) }}</div>
                </td>
                <td class="r">
                  <div class="num dim">{{ fmtNum(row.cost_price) }}</div>
                  <div v-if="row.quantity" class="xs dim num">{{ fmtNum(row.quantity, 0) }} 股</div>
                </td>
                <td class="r num" :class="pctClass(row.pnl_pct)">{{ fmtPct(row.pnl_pct) }}</td>
                <td><span class="badge" :class="actionMeta(row.alert_action).cls">{{ actionMeta(row.alert_action).label }}</span></td>
                <td><ConfRing :value="row.confidence_20d" :size="30" /></td>
                <td class="r num" :class="pctClass(row.expected_return_20d)">
                  {{ fmtPct(row.expected_return_20d, 2, false) }}
                </td>
                <td><span class="badge" :class="thesisMeta(row.thesis_state).cls">{{ thesisMeta(row.thesis_state).label }}</span></td>
                <td>
                  <div v-if="row.recent_events.length" class="event-icons" :aria-label="`${row.symbol} 最近事件`">
                    <button
                      v-for="event in row.recent_events"
                      :key="`${event.source_ref || event.category}-${event.event_type}-${event.id}`"
                      class="event-button"
                      :class="eventClass(event.category)"
                      type="button"
                      :title="eventTooltip(event)"
                      :aria-label="eventAriaLabel(event)"
                      :disabled="interactionLocked"
                      @click="openStockEvents(row.symbol)"
                    >
                      <component :is="eventIcon(event.category)" :size="14" :stroke-width="1.8" />
                    </button>
                  </div>
                  <span v-else class="dim">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-hint">该分组暂无股票；可在上方加入追踪标的。</div>
      </section>

      <aside class="watchlist-rail">
        <section class="panel portfolio-panel" aria-labelledby="portfolio-title">
          <div class="panel-title">
            <span id="portfolio-title">持仓配置</span>
            <span class="extra" :class="{ fallback: portfolioPresentation.isFallback }">
              {{ portfolioPresentation.isFallback ? '回退口径' : '真实市值' }}
            </span>
          </div>
          <EChart
            v-if="portfolioPresentation.slices.length"
            :option="portfolioDonut"
            height="190px"
            :aria-label="portfolioAriaLabel"
          />
          <div v-else class="empty-hint portfolio-empty">缺少模拟持仓，且自选股没有完整的成本与数量。</div>
          <div class="portfolio-basis">
            <span>估值口径</span>
            <strong>{{ portfolioPresentation.basis }}</strong>
          </div>
          <p v-if="portfolioPresentation.warning" class="portfolio-warning">{{ portfolioPresentation.warning }}</p>
        </section>

        <section class="panel focus-panel" aria-labelledby="focus-title">
          <div class="panel-title">
            <span id="focus-title">今日重点跟踪</span>
            <span class="extra">{{ focusRows.length }} 项</span>
          </div>
          <div v-if="focusRows.length" class="focus-list">
            <button
              v-for="row in focusRows"
              :key="row.symbol"
              class="focus-row"
              type="button"
              :disabled="interactionLocked"
              @click="router.push(`/stock/${row.symbol}`)"
            >
              <span class="focus-copy">
                <strong>{{ row.display_name || row.symbol }}</strong>
                <small class="mono">{{ row.symbol }} · 预期 {{ fmtPct(row.expected_return_20d, 1, false) }}</small>
              </span>
              <span class="badge" :class="actionMeta(row.alert_action).cls">{{ actionMeta(row.alert_action).label }}</span>
            </button>
          </div>
          <div v-else class="empty-hint">暂无需要重点跟踪的方向提醒。</div>
        </section>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.watchlist-page {
  min-width: 0;
}

.watchlist-head {
  align-items: center;
}

.watchlist-head > div:first-child {
  min-width: 0;
}

.reload-button {
  margin-left: auto;
  white-space: nowrap;
}

.watchlist-banner,
.summary-grid,
.add-panel,
.group-tabs {
  margin-bottom: var(--s3);
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--s3);
}

.summary-card {
  min-width: 0;
  min-height: 94px;
  display: flex;
  align-items: center;
  gap: var(--s3);
  padding: 12px 14px;
  border: 1px solid var(--line-1);
  border-radius: var(--r-md);
  background: linear-gradient(180deg, var(--surface-2), var(--surface-1));
  overflow: hidden;
}

.summary-card.summary-green { border-color: rgba(52, 211, 153, 0.24); }
.summary-card.summary-blue { border-color: rgba(96, 165, 250, 0.24); }
.summary-card.summary-red { border-color: rgba(248, 113, 113, 0.24); }

.summary-copy {
  min-width: 92px;
  display: grid;
  align-content: center;
}

.summary-copy > span {
  color: var(--text-2);
  font-size: 12px;
}

.summary-copy strong {
  margin-block: 1px;
  font-size: 25px;
  font-weight: 680;
  line-height: 1.2;
}

.summary-copy small {
  color: var(--text-2);
  font-size: 11px;
}

.summary-sparkline {
  min-width: 64px;
  flex: 1 1 88px;
}

.partial-warning {
  margin: calc(-1 * var(--s2)) 0 var(--s3);
  color: var(--warn);
  font-size: 11px;
}

.add-panel {
  overflow: visible;
}

.add-panel::before {
  border-radius: var(--r-lg) var(--r-lg) 0 0;
}

.add-form {
  display: grid;
  grid-template-columns: 112px minmax(120px, 1fr) 100px 100px minmax(130px, 0.8fr) auto;
  align-items: end;
  gap: var(--s2);
}

.add-form label {
  min-width: 0;
  display: grid;
  gap: 4px;
  color: var(--text-2);
  font-size: 11px;
}

.add-form .input {
  width: 100%;
  min-width: 0;
}

.add-button {
  white-space: nowrap;
}

.group-tabs {
  min-width: 0;
  display: flex;
  gap: 4px;
  overflow-x: auto;
  scrollbar-width: none;
}

.group-tabs::-webkit-scrollbar {
  display: none;
}

.group-tab {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 10px;
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-2);
  font-size: 12px;
  cursor: pointer;
}

.group-tab:hover {
  color: var(--text-1);
  background: rgba(148, 163, 198, 0.07);
}

.group-tab.on {
  border-color: rgba(96, 165, 250, 0.3);
  background: rgba(59, 130, 246, 0.14);
  color: var(--text-1);
}

.group-tab .num {
  color: var(--text-3);
  font-size: 11px;
}

.watchlist-layout {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 304px;
  align-items: start;
  gap: var(--s3);
}

.table-panel {
  min-width: 0;
  padding-bottom: 6px;
}

.tracked-title-row b {
  margin-left: 5px;
  color: var(--text-2);
  font-size: 10px;
  font-weight: 500;
}

.batch-toolbar {
  display: flex;
  align-items: center;
  gap: var(--s2);
  margin: -2px 0 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line-1);
}

.batch-selection,
.batch-group,
.batch-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.batch-selection {
  min-height: 28px;
  color: var(--text-2);
  font-size: 11.5px;
  white-space: nowrap;
}

.batch-group {
  min-width: 0;
  flex: 1;
}

.batch-group .input {
  width: min(170px, 100%);
  min-width: 106px;
}

.batch-actions {
  margin-left: auto;
}

input[type='checkbox'] {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
  cursor: pointer;
}

.check-hit {
  min-width: 28px;
  min-height: 28px;
  display: inline-grid;
  place-items: center;
  cursor: pointer;
}

.operation-message {
  margin: -2px 0 8px;
  color: var(--up);
  font-size: 11px;
}

.table-scroll {
  width: 100%;
  overflow-x: auto;
  overscroll-behavior-inline: contain;
}

.watchlist-table {
  min-width: 1060px;
}

.watchlist-table .check-cell {
  width: 32px;
  padding-inline: 7px;
  text-align: center;
}

.watchlist-table tbody tr.selected {
  background: rgba(59, 130, 246, 0.08);
}

.stock-link {
  display: grid;
  border: 0;
  background: transparent;
  color: var(--text-1);
  text-align: left;
  cursor: pointer;
}

.stock-link span {
  font-weight: 600;
}

.stock-link small {
  color: var(--text-2);
  font-size: 11px;
}

.stock-link:disabled,
.focus-row:disabled {
  cursor: default;
  opacity: 0.72;
}

.stock-link:hover span {
  color: var(--accent-hi);
}

.event-icons {
  display: flex;
  gap: 4px;
}

.event-button {
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: var(--surface-1);
  color: var(--text-2);
  cursor: pointer;
}

.event-button:hover {
  border-color: currentColor;
  background: var(--surface-3);
}

.event-button.announcement { color: var(--purple); }
.event-button.calendar { color: var(--accent-hi); }
.event-button.capital { color: var(--warn); }
.event-button.other { color: var(--text-2); }

.table-loading {
  display: grid;
  gap: var(--s2);
}

.table-loading .skeleton {
  height: 44px;
}

.watchlist-rail {
  min-width: 0;
  display: grid;
  gap: var(--s3);
}

.panel-title .extra.fallback {
  color: var(--warn);
}

.portfolio-basis {
  display: grid;
  gap: 2px;
  padding-top: 8px;
  border-top: 1px solid var(--line-1);
  font-size: 11px;
}

.portfolio-basis span,
.portfolio-warning {
  color: var(--text-2);
}

.portfolio-basis strong {
  color: var(--text-1);
  font-weight: 550;
}

.portfolio-warning {
  margin-top: 7px;
  font-size: 11px;
  line-height: 1.55;
}

.portfolio-empty {
  min-height: 126px;
  display: grid;
  place-items: center;
  line-height: 1.6;
}

.focus-list {
  display: grid;
}

.focus-row {
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 0;
  border: 0;
  border-bottom: 1px solid var(--line-1);
  background: transparent;
  color: var(--text-1);
  text-align: left;
  cursor: pointer;
}

.focus-row:last-child {
  border-bottom: 0;
}

.focus-row:hover strong {
  color: var(--accent-hi);
}

.focus-copy {
  min-width: 0;
  flex: 1;
  display: grid;
}

.focus-copy strong,
.focus-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.focus-copy strong {
  font-size: 12px;
}

.focus-copy small {
  color: var(--text-2);
  font-size: 11px;
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

@media (max-width: 1280px) {
  .add-form {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .add-button {
    min-height: 34px;
  }
}

@media (max-width: 1500px) {
  .watchlist-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .watchlist-rail {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 820px) {
  .summary-grid {
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  }

  .batch-toolbar {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .batch-selection {
    width: 100%;
  }

  .batch-group {
    flex: 1 1 280px;
  }
}

@media (max-width: 620px) {
  .watchlist-head {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: var(--s2);
  }

  .watchlist-head .sub {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .reload-button {
    margin-left: 0;
  }

  .reload-button span {
    display: none;
  }

  .summary-grid,
  .add-form,
  .watchlist-rail {
    grid-template-columns: minmax(0, 1fr);
  }

  .summary-card {
    min-height: 82px;
  }

  .panel-title {
    align-items: flex-start;
  }

  .add-panel .panel-title .extra {
    max-width: 150px;
    text-align: right;
  }

  .batch-group {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    flex-basis: 100%;
  }

  .batch-group .input {
    width: 100%;
  }

  .batch-actions {
    width: 100%;
    margin-left: 0;
  }

  .batch-actions .btn {
    flex: 1;
  }
}

@media (max-width: 380px) {
  .batch-group {
    grid-template-columns: minmax(0, 1fr);
  }

  .batch-group .btn {
    width: 100%;
  }
}

@keyframes rotate {
  to { transform: rotate(360deg); }
}
</style>
