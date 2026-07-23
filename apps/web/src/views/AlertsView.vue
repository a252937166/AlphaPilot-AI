<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  AlertTriangle,
  Check,
  CircleDollarSign,
  Clock3,
  Play,
  RefreshCw,
  ShieldCheck,
  Target,
  X,
  Zap,
} from 'lucide-vue-next'
import { api } from '../api'
import type {
  AlertItem,
  BrokerOrder,
  PersistedTradeProposal,
  StockOverviewResponse,
  TradeProposalInput,
  TradeRiskDecision,
} from '../api'
import { actionMeta, fmtNum, fmtPct, fmtTime } from '../format'
import ConfRing from '../components/ConfRing.vue'

type AuditTab = 'proposals' | 'orders'
type OrderState = 'active' | 'filled' | 'exception'

const loading = ref(true)
const refreshing = ref(false)
const alertsError = ref('')
const proposalsError = ref('')
const ordersError = ref('')
const alerts = ref<AlertItem[]>([])
const proposals = ref<PersistedTradeProposal[]>([])
const orders = ref<BrokerOrder[]>([])
const filter = ref('ALL')
const selected = ref<AlertItem | null>(null)
const riskChecking = ref(false)
const riskDecision = ref<TradeRiskDecision | null>(null)
const evaluatedProposal = ref<TradeProposalInput | null>(null)
const proposalCreating = ref(false)
const batchReading = ref(false)
const batchRejecting = ref(false)
const reviewingId = ref<number | null>(null)
const executingId = ref<number | null>(null)
const actionFeedback = ref('')
const actionFeedbackOk = ref(false)
const activeAuditTab = ref<AuditTab>('proposals')
const activeOrderState = ref<OrderState>('filled')
let selectionEpoch = 0

const FILTERS = [
  { key: 'ALL', label: '全部' },
  { key: 'BUY_CANDIDATE', label: '买入候选' },
  { key: 'REDUCE', label: '减仓' },
  { key: 'EXIT', label: '退出' },
  { key: 'REVIEW_REQUIRED', label: '需复核' },
  { key: 'WATCH', label: '观察' },
]

const BUY_ACTIONS = new Set(['BUY_CANDIDATE', 'ADD'])
const SELL_ACTIONS = new Set(['REDUCE', 'EXIT', 'STOP'])
const ACTIVE_ORDER_STATUSES = new Set(['submitting', 'submitted', 'partial'])
const FILLED_ORDER_STATUSES = new Set(['filled'])

const PROPOSAL_STATUS_META: Record<string, { label: string; cls: string }> = {
  pending: { label: '待人工确认', cls: 'blue' },
  approved: { label: '已批准·可执行', cls: 'green' },
  approved_no_execution: { label: '已批准·执行关闭', cls: 'yellow' },
  rejected: { label: '已拒绝', cls: 'gray' },
  rejected_by_risk: { label: '风控拒绝', cls: 'red' },
  executing: { label: '执行中', cls: 'blue' },
  executed: { label: '已执行', cls: 'green' },
  exec_failed: { label: '执行异常', cls: 'red' },
}

const ORDER_STATUS_META: Record<string, { label: string; cls: string }> = {
  submitting: { label: '提交中', cls: 'blue' },
  submitted: { label: '已提交', cls: 'blue' },
  partial: { label: '部分成交', cls: 'yellow' },
  filled: { label: '已成交', cls: 'green' },
  cancelled: { label: '已撤单', cls: 'gray' },
  failed: { label: '执行失败', cls: 'red' },
}

const visibleAlerts = computed(() =>
  filter.value === 'ALL'
    ? alerts.value
    : alerts.value.filter((alert) => alert.action === filter.value),
)

const unreadAlerts = computed(() => alerts.value.filter((alert) => !alert.acknowledged))
const pendingProposals = computed(() =>
  proposals.value.filter((proposal) => proposal.status === 'pending'),
)

const selectedProposal = computed(() => {
  if (!selected.value) return null
  return proposals.value.find((proposal) => proposal.source_alert_id === selected.value?.id) ?? null
})

const selectedOrder = computed(() => {
  const proposal = selectedProposal.value
  if (!proposal) return null
  return orders.value.find((order) => order.proposal_id === proposal.proposal_id) ?? null
})

const orderCounts = computed<Record<OrderState, number>>(() => ({
  active: orders.value.filter((order) => ACTIVE_ORDER_STATUSES.has(order.status)).length,
  filled: orders.value.filter((order) => FILLED_ORDER_STATUSES.has(order.status)).length,
  exception: orders.value.filter(
    (order) => !ACTIVE_ORDER_STATUSES.has(order.status) && !FILLED_ORDER_STATUSES.has(order.status),
  ).length,
}))

const visibleOrders = computed(() =>
  orders.value.filter((order) => orderState(order.status) === activeOrderState.value),
)

const workflowSteps = computed(() => {
  const proposal = selectedProposal.value
  const order = selectedOrder.value
  const reviewed = proposal && proposal.status !== 'pending'
  return [
    { label: '提醒', state: selected.value ? 'done' : 'idle', detail: selected.value ? `#${selected.value.id}` : '未选择' },
    {
      label: '提案',
      state: proposal ? 'done' : 'idle',
      detail: proposal ? proposalStatus(proposal.status).label : '尚未生成',
    },
    {
      label: '人工确认',
      state: reviewed ? (proposal?.status === 'rejected' ? 'blocked' : 'done') : 'idle',
      detail: reviewed ? fmtTime(proposal?.reviewed_at) : '等待确认',
    },
    {
      label: '模拟订单',
      state: order ? (orderState(order.status) === 'exception' ? 'blocked' : 'done') : 'idle',
      detail: order ? orderStatus(order.status).label : '未提交',
    },
  ]
})

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') {
    return null
  }
  const number = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(number) ? number : null
}

function proposalSide(action: unknown): 'BUY' | 'SELL' | null {
  const normalized = String(action || '').toUpperCase()
  if (BUY_ACTIONS.has(normalized)) return 'BUY'
  if (SELL_ACTIONS.has(normalized)) return 'SELL'
  return null
}

function proposalStatus(status: string) {
  return PROPOSAL_STATUS_META[status] ?? { label: status || '未知', cls: 'gray' }
}

function orderStatus(status: string) {
  return ORDER_STATUS_META[status] ?? { label: status || '未知', cls: 'gray' }
}

function orderState(status: string): OrderState {
  if (ACTIVE_ORDER_STATUSES.has(status)) return 'active'
  if (FILLED_ORDER_STATUSES.has(status)) return 'filled'
  return 'exception'
}

function amountLabel(alert: AlertItem): string {
  if (alert.suggested_notional === null) return '暂不可用'
  const prefix = alert.suggested_notional < 0 ? '-¥' : '¥'
  return `${prefix}${fmtNum(Math.abs(alert.suggested_notional), 0)}`
}

function targetLabel(alert: AlertItem): string {
  if (alert.target_low === null || alert.target_high === null) return '暂不可用'
  return `¥${fmtNum(alert.target_low, 2)} – ¥${fmtNum(alert.target_high, 2)}`
}

function dateLabel(value: string): string {
  return fmtTime(value).split(' ')[0] || '—'
}

function clockLabel(value: string): string {
  const time = fmtTime(value).split(' ')[1]
  return time ? time.split(':').slice(0, 2).join(':') : '—'
}

function proposalPreconditions(alert: AlertItem, overview: StockOverviewResponse): string[] {
  const reasons: string[] = []
  const side = proposalSide(alert.action)
  const quote = overview.quote
  const last = finiteNumber(quote?.last)
  const confidence = finiteNumber(alert.confidence)
  const suggestedNotional = finiteNumber(alert.suggested_notional)
  const targetLow = finiteNumber(alert.target_low)
  const targetHigh = finiteNumber(alert.target_high)
  const quoteSource = String(quote?.source || '').trim().toLowerCase()

  if (!side) reasons.push('该提醒不是可生成交易提案的方向性信号。')
  if (last === null || last <= 0) reasons.push('缺少可审计的实时价格。')
  if (!quote?.as_of) reasons.push('行情时间缺失，不能用本机时间代替。')
  if (!quoteSource || quoteSource.includes('mock') || quoteSource === 'unavailable') {
    reasons.push('当前报价没有可审计的真实行情来源。')
  }
  if (confidence === null || confidence < 0 || confidence > 1) {
    reasons.push('来源提醒缺少有效置信度。')
  }
  if (!String(alert.model_version || '').trim()) reasons.push('来源提醒缺少模型版本。')
  if (suggestedNotional === null || suggestedNotional === 0) {
    reasons.push('来源提醒没有有效建议金额。')
  } else if (
    (side === 'BUY' && suggestedNotional < 0)
    || (side === 'SELL' && suggestedNotional > 0)
  ) {
    reasons.push('来源提醒的建议金额方向与交易动作不一致。')
  }
  if (targetLow === null || targetHigh === null || targetLow <= 0 || targetHigh <= targetLow) {
    reasons.push('来源提醒缺少有效目标区间。')
  } else if (last !== null && (targetHigh < last * 0.5 || targetLow > last * 1.5)) {
    reasons.push('来源提醒的目标区间与当前真实价格不在同一量级。')
  }

  const expiresAt = Date.parse(String(alert.expires_at || ''))
  if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
    reasons.push('来源提醒已过期或缺少有效期。')
  }
  if (
    last !== null
    && last > 0
    && suggestedNotional !== null
    && Math.floor(Math.abs(suggestedNotional) / last / 100) * 100 < 100
  ) {
    reasons.push('建议金额不足一手，不能强制放大到 100 股。')
  }
  return reasons
}

function buildProposal(alert: AlertItem, overview: StockOverviewResponse): TradeProposalInput | null {
  const side = proposalSide(alert.action)
  const last = finiteNumber(overview.quote?.last)
  const confidence = finiteNumber(alert.confidence)
  const suggestedNotional = finiteNumber(alert.suggested_notional)
  const marketDataAsOf = overview.quote?.as_of
  const modelVersion = String(alert.model_version || '').trim()
  if (
    !side
    || last === null
    || last <= 0
    || confidence === null
    || suggestedNotional === null
    || !marketDataAsOf
    || !modelVersion
    || !Number.isInteger(alert.id)
    || alert.id <= 0
  ) {
    return null
  }
  const quantity = Math.floor(Math.abs(suggestedNotional) / last / 100) * 100
  if (quantity < 100) return null
  const proposalId = `alert-${alert.id}-${side.toLowerCase()}`
  return {
    proposal_id: proposalId,
    idempotency_key: proposalId,
    symbol: overview.symbol || alert.symbol,
    side,
    quantity,
    estimated_notional: Number((quantity * last).toFixed(2)),
    confidence,
    market_data_as_of: marketDataAsOf,
    model_version: modelVersion,
    mode: 'confirm_to_trade',
    source_alert_id: alert.id,
    metadata: {
      source: 'alerts-view',
      source_suggested_notional: suggestedNotional,
      quote_source: overview.quote?.source,
      target_low: alert.target_low,
      target_high: alert.target_high,
    },
  }
}

function choosePopulatedOrderState() {
  if (orderCounts.value[activeOrderState.value] > 0) return
  if (orderCounts.value.active > 0) activeOrderState.value = 'active'
  else if (orderCounts.value.filled > 0) activeOrderState.value = 'filled'
  else if (orderCounts.value.exception > 0) activeOrderState.value = 'exception'
}

async function load() {
  loading.value = true
  alertsError.value = ''
  proposalsError.value = ''
  ordersError.value = ''
  const [alertResult, proposalResult, orderResult] = await Promise.allSettled([
    api.alerts(80),
    api.proposals(),
    api.orders(100),
  ])

  if (alertResult.status === 'fulfilled') {
    alerts.value = alertResult.value.alerts || []
    const current = alerts.value.find((alert) => alert.id === selected.value?.id)
    if (current || alerts.value[0]) void select(current || alerts.value[0])
    else {
      selected.value = null
      riskDecision.value = null
      evaluatedProposal.value = null
    }
  } else {
    alertsError.value = `提醒暂不可用：${String(alertResult.reason?.message || alertResult.reason)}`
  }

  if (proposalResult.status === 'fulfilled') {
    proposals.value = proposalResult.value.proposals || []
  } else {
    proposalsError.value = `提案流水暂不可用：${String(proposalResult.reason?.message || proposalResult.reason)}`
  }

  if (orderResult.status === 'fulfilled') {
    orders.value = orderResult.value.orders || []
    choosePopulatedOrderState()
  } else {
    ordersError.value = `模拟订单流水暂不可用：${String(orderResult.reason?.message || orderResult.reason)}`
  }
  loading.value = false
}

async function reloadAudit() {
  const [proposalResult, orderResult] = await Promise.allSettled([api.proposals(), api.orders(100)])
  if (proposalResult.status === 'fulfilled') {
    proposals.value = proposalResult.value.proposals || []
    proposalsError.value = ''
  } else {
    proposalsError.value = `提案流水刷新失败：${String(proposalResult.reason?.message || proposalResult.reason)}`
  }
  if (orderResult.status === 'fulfilled') {
    orders.value = orderResult.value.orders || []
    ordersError.value = ''
    choosePopulatedOrderState()
  } else {
    ordersError.value = `模拟订单流水刷新失败：${String(orderResult.reason?.message || orderResult.reason)}`
  }
}

async function refresh() {
  refreshing.value = true
  alertsError.value = ''
  try {
    await api.refreshAlerts()
    await load()
  } catch (exc: any) {
    alertsError.value = `提醒重算失败：${String(exc.message || exc)}`
  } finally {
    refreshing.value = false
  }
}

async function select(alert: AlertItem) {
  const epoch = ++selectionEpoch
  selected.value = alert
  riskDecision.value = null
  evaluatedProposal.value = null
  actionFeedback.value = ''
  actionFeedbackOk.value = false
  riskChecking.value = true
  try {
    const overview = await api.stockOverview(alert.symbol)
    if (epoch !== selectionEpoch) return
    const preconditions = proposalPreconditions(alert, overview)
    const proposal = buildProposal(alert, overview)
    if (preconditions.length || !proposal) {
      riskDecision.value = {
        approved: false,
        reasons: preconditions.length ? preconditions : ['无法由当前真实行情构造交易提案。'],
        evaluated_at: '',
        requires_human_confirmation: true,
      }
      return
    }
    evaluatedProposal.value = proposal
    const decision = await api.evaluateTrade({ proposal })
    if (epoch !== selectionEpoch) return
    riskDecision.value = decision
  } catch (exc: any) {
    if (epoch !== selectionEpoch) return
    riskDecision.value = {
      approved: false,
      reasons: [String(exc.message || exc)],
      evaluated_at: '',
      requires_human_confirmation: true,
    }
  } finally {
    if (epoch === selectionEpoch) riskChecking.value = false
  }
}

async function acknowledge(alert: AlertItem) {
  try {
    await api.ackAlert(alert.id)
    alert.acknowledged = true
  } catch (exc: any) {
    alertsError.value = `提醒标记失败：${String(exc.message || exc)}`
  }
}

async function acknowledgeAll() {
  if (!unreadAlerts.value.length || batchReading.value) return
  batchReading.value = true
  alertsError.value = ''
  const targets = [...unreadAlerts.value]
  let failed = 0
  for (const alert of targets) {
    try {
      await api.ackAlert(alert.id)
      alert.acknowledged = true
    } catch {
      failed += 1
    }
  }
  if (failed) alertsError.value = `${targets.length - failed} 条已标记，${failed} 条失败，请重试。`
  batchReading.value = false
}

async function createProposal() {
  const proposal = evaluatedProposal.value
  if (!proposal || !riskDecision.value?.approved || proposalCreating.value) return
  const epoch = selectionEpoch
  proposalCreating.value = true
  actionFeedback.value = ''
  actionFeedbackOk.value = false
  try {
    const result = await api.createProposal({ proposal })
    if (epoch !== selectionEpoch) return
    riskDecision.value = result.risk_decision
    proposals.value = [
      result.proposal,
      ...proposals.value.filter((item) => item.id !== result.proposal.id),
    ]
    if (!result.risk_decision.approved || result.proposal.status !== 'pending') {
      actionFeedback.value =
        result.risk_decision.reasons.join('；') || `提案状态为 ${result.proposal.status}。`
      return
    }
    actionFeedbackOk.value = true
    actionFeedback.value = '提案已入库，下一步必须由你人工确认后才会提交 SIMULATE 订单。'
    activeAuditTab.value = 'proposals'
    await reloadAudit()
  } catch (exc: any) {
    if (Number(exc?.status) === 409) {
      await reloadAudit()
      const existing = proposals.value.find(
        (item) =>
          item.proposal_id === proposal.proposal_id
          || item.idempotency_key === proposal.idempotency_key,
      )
      if (existing) {
        actionFeedbackOk.value = true
        actionFeedback.value =
          `相同提醒的提案已存在，当前状态：${proposalStatus(existing.status).label}。`
      } else {
        actionFeedback.value = String(exc.message || exc)
      }
    } else if (epoch === selectionEpoch) {
      actionFeedback.value = String(exc.message || exc)
    }
  } finally {
    if (epoch === selectionEpoch) proposalCreating.value = false
  }
}

async function rejectProposal(proposal: PersistedTradeProposal) {
  if (proposal.status !== 'pending' || reviewingId.value !== null) return
  if (!window.confirm(`确认拒绝 ${proposal.symbol} 的待处理提案？该操作会写入审计流水。`)) return
  reviewingId.value = proposal.id
  actionFeedback.value = ''
  try {
    const result = await api.rejectProposal(proposal.id)
    Object.assign(proposal, result.proposal)
    actionFeedbackOk.value = true
    actionFeedback.value = `${proposal.symbol} 提案已拒绝，未创建订单。`
  } catch (exc: any) {
    actionFeedbackOk.value = false
    actionFeedback.value = `提案拒绝失败：${String(exc.message || exc)}`
  } finally {
    reviewingId.value = null
  }
}

async function rejectAllPending() {
  if (!pendingProposals.value.length || batchRejecting.value) return
  if (!window.confirm(`确认批量拒绝 ${pendingProposals.value.length} 条待处理提案？已批准与已执行记录不会受影响。`)) return
  batchRejecting.value = true
  const targets = [...pendingProposals.value]
  let rejected = 0
  for (const proposal of targets) {
    try {
      await api.rejectProposal(proposal.id)
      rejected += 1
    } catch {
      /* Keep going so the final message can report the exact partial result. */
    }
  }
  const failed = targets.length - rejected
  await reloadAudit()
  actionFeedbackOk.value = failed === 0
  actionFeedback.value = failed
    ? `已拒绝 ${rejected} 条，${failed} 条失败，请检查流水状态。`
    : `已拒绝 ${rejected} 条待处理提案，未创建任何订单。`
  batchRejecting.value = false
}

async function confirmExecute(proposal: PersistedTradeProposal) {
  if (!['pending', 'approved'].includes(proposal.status) || executingId.value !== null) return
  const confirmed = window.confirm(
    `确认执行 ${proposal.symbol} ${proposal.side} ${fmtNum(proposal.quantity, 0)} 股？\n`
    + `预计金额 ¥${fmtNum(proposal.estimated_notional, 0)}。\n\n`
    + '只会提交到富途 SIMULATE 模拟账户；仍会重新执行 Kill Switch、配置开关和实时风控检查。',
  )
  if (!confirmed) return

  executingId.value = proposal.id
  actionFeedback.value = ''
  actionFeedbackOk.value = false
  try {
    let executable = proposal
    if (proposal.status === 'pending') {
      const approved = await api.approveProposal(proposal.id)
      Object.assign(proposal, approved.proposal)
      executable = approved.proposal
    }
    if (executable.status !== 'approved') {
      actionFeedback.value =
        `提案已记录人工批准，但执行网关当前关闭（${proposalStatus(executable.status).label}），未创建订单。`
      return
    }
    const result = await api.executeProposal(executable.id)
    Object.assign(proposal, result.proposal)
    orders.value = [
      result.order,
      ...orders.value.filter((order) => order.id !== result.order.id),
    ]
    activeAuditTab.value = 'orders'
    activeOrderState.value = orderState(result.order.status)
    actionFeedbackOk.value = true
    actionFeedback.value = result.order.avg_fill_price !== null
      ? `SIMULATE 订单已${orderStatus(result.order.status).label}，成交均价 ¥${fmtNum(result.order.avg_fill_price, 2)}。`
      : `SIMULATE 订单已进入“${orderStatus(result.order.status).label}”，请在执行流水跟踪结果。`
    await reloadAudit()
  } catch (exc: any) {
    actionFeedback.value = `模拟执行未完成：${String(exc.message || exc)}`
    await reloadAudit()
  } finally {
    executingId.value = null
  }
}

onMounted(load)
</script>

<template>
  <div class="alerts-page">
    <div class="page-head alerts-head">
      <div>
        <h1>交易提醒</h1>
        <span class="sub">提醒 → 风控 → 人工确认 → SIMULATE 订单，全链路可追溯</span>
      </div>
      <div class="head-actions">
        <button
          class="btn ghost"
          :disabled="batchReading || !unreadAlerts.length"
          @click="acknowledgeAll"
        >
          <Check :size="12" /> {{ batchReading ? '标记中…' : `全部已读 ${unreadAlerts.length || ''}` }}
        </button>
        <button class="btn primary" :disabled="refreshing" @click="refresh">
          <Zap :size="12" /> {{ refreshing ? '重算中…' : '重算信号' }}
        </button>
      </div>
    </div>

    <div v-if="alertsError" class="banner error page-message" role="alert">{{ alertsError }}</div>

    <div class="filter-row" aria-label="提醒筛选">
      <button
        v-for="item in FILTERS"
        :key="item.key"
        class="filter-button"
        :class="{ on: filter === item.key }"
        :aria-pressed="filter === item.key"
        @click="filter = item.key"
      >
        {{ item.label }}
        <span class="num">
          {{ item.key === 'ALL' ? alerts.length : alerts.filter((alert) => alert.action === item.key).length }}
        </span>
      </button>
    </div>

    <div class="alerts-layout">
      <section class="panel alert-list-panel" aria-label="提醒列表">
        <div class="panel-title">
          <span>实时提醒</span>
          <span class="extra">{{ loading ? '同步中…' : `${visibleAlerts.length} 条` }}</span>
        </div>
        <div v-if="visibleAlerts.length" class="alert-list">
          <article
            v-for="alert in visibleAlerts"
            :key="alert.id"
            class="alert-row"
            :class="{ selected: selected?.id === alert.id }"
          >
            <button
              type="button"
              class="alert-select"
              :aria-pressed="selected?.id === alert.id"
              :aria-label="`查看 ${alert.symbol} ${actionMeta(alert.action).label} 提醒`"
              @click="select(alert)"
            >
              <span class="alert-date mono">
                <b>{{ dateLabel(alert.created_at) }}</b>
                <span>{{ clockLabel(alert.created_at) }}</span>
              </span>
              <span class="alert-copy">
                <span class="alert-primary">
                  <b class="num">{{ alert.symbol }}</b>
                  <span class="badge" :class="actionMeta(alert.action).cls">
                    {{ actionMeta(alert.action).label }}
                  </span>
                </span>
                <span class="alert-reason">{{ alert.reasons[0] || '暂无理由说明' }}</span>
                <span class="alert-mobile-time mono">
                  {{ dateLabel(alert.created_at) }} {{ clockLabel(alert.created_at) }}
                </span>
              </span>
              <span class="alert-amount num">{{ amountLabel(alert) }}</span>
              <ConfRing :value="alert.confidence" :size="34" />
            </button>
            <button
              v-if="!alert.acknowledged"
              class="read-button"
              :aria-label="`标记 ${alert.symbol} 提醒为已读`"
              @click="acknowledge(alert)"
            >
              知悉
            </button>
            <span v-else class="read-state"><Check :size="11" /> 已读</span>
          </article>
        </div>
        <div v-else class="empty-hint">
          {{ loading ? '正在加载提醒…' : '当前筛选下暂无提醒；不会用示例数据填充。' }}
        </div>
      </section>

      <section class="panel decision-panel" aria-label="提醒决策详情">
        <div class="panel-title">
          <span>决策与执行</span>
          <span class="extra"><ShieldCheck :size="12" /> SIMULATE 隔离</span>
        </div>
        <template v-if="selected">
          <div class="decision-head">
            <div>
              <div class="decision-symbol">
                <b class="num">{{ selected.symbol }}</b>
                <span class="badge" :class="actionMeta(selected.action).cls">
                  {{ actionMeta(selected.action).label }}
                </span>
              </div>
              <div class="decision-time mono">{{ fmtTime(selected.created_at) }} 生成</div>
            </div>
            <ConfRing :value="selected.confidence" :size="48" />
          </div>

          <div class="workflow-rail" aria-label="执行审计轨道">
            <div
              v-for="(step, index) in workflowSteps"
              :key="step.label"
              class="workflow-step"
              :class="step.state"
            >
              <span class="step-index">{{ index + 1 }}</span>
              <span>
                <b>{{ step.label }}</b>
                <small>{{ step.detail }}</small>
              </span>
            </div>
          </div>

          <div class="decision-metrics">
            <div>
              <Target :size="14" />
              <span>目标区间</span>
              <b class="num">{{ targetLabel(selected) }}</b>
            </div>
            <div>
              <CircleDollarSign :size="14" />
              <span>{{ selected.suggested_notional !== null && selected.suggested_notional < 0 ? '建议减仓金额' : '建议金额' }}</span>
              <b class="num">{{ amountLabel(selected) }}</b>
            </div>
            <div>
              <Clock3 :size="14" />
              <span>有效期至</span>
              <b class="mono">{{ fmtTime(selected.expires_at) }}</b>
            </div>
          </div>

          <div class="decision-grid">
            <div class="reason-block">
              <h2>推荐依据</h2>
              <ul>
                <li v-for="(reason, index) in selected.reasons" :key="index">{{ reason }}</li>
                <li v-if="!selected.reasons.length">暂无理由文本。</li>
              </ul>
              <div class="invalidation">
                <AlertTriangle :size="13" />
                <span><b>失效条件</b>{{ selected.invalidation || '暂未提供失效条件。' }}</span>
              </div>
              <div class="model-line">
                <span>仓位变化 <b class="num">{{ fmtPct(selected.suggested_position_change * 100, 0) }}</b></span>
                <span>模型 <b class="mono">{{ selected.model_version || '暂不可用' }}</b></span>
              </div>
            </div>

            <div class="risk-block">
              <h2>实时风控</h2>
              <div v-if="riskChecking" class="skeleton risk-skeleton" />
              <template v-else-if="riskDecision">
                <div
                  v-for="(reason, index) in riskDecision.reasons"
                  :key="index"
                  class="check-row"
                >
                  <Check v-if="riskDecision.approved" :size="12" class="up" />
                  <X v-else :size="12" class="down" />
                  <span>{{ reason }}</span>
                </div>
                <div class="risk-result">
                  <span class="badge" :class="riskDecision.approved ? 'green' : 'red'">
                    {{ riskDecision.approved ? '风控通过' : '暂不可提案' }}
                  </span>
                  <span v-if="riskDecision.requires_human_confirmation">必须人工确认</span>
                </div>
                <button
                  v-if="!selectedProposal"
                  class="btn primary proposal-button"
                  :disabled="!riskDecision.approved || !evaluatedProposal || proposalCreating"
                  @click="createProposal"
                >
                  <Play :size="12" /> {{ proposalCreating ? '入库校验中…' : '生成交易提案' }}
                </button>
                <div v-else class="existing-proposal">
                  <span>关联提案 #{{ selectedProposal.id }}</span>
                  <span class="badge" :class="proposalStatus(selectedProposal.status).cls">
                    {{ proposalStatus(selectedProposal.status).label }}
                  </span>
                </div>
              </template>
              <div v-else class="empty-hint">风控结果暂不可用。</div>
            </div>
          </div>
        </template>
        <div v-else class="empty-hint">选择一条提醒查看目标、依据与执行状态。</div>
      </section>
    </div>

    <div
      v-if="actionFeedback"
      class="banner page-message"
      :class="{ error: !actionFeedbackOk }"
      role="status"
    >
      {{ actionFeedback }}
    </div>

    <section class="panel audit-panel">
      <div class="audit-head">
        <div>
          <h2>审计流水</h2>
          <p>所有时间、状态和成交价均来自后端持久化记录。</p>
        </div>
        <div class="tab-pills" role="tablist" aria-label="审计流水类型">
          <button
            :class="{ on: activeAuditTab === 'proposals' }"
            role="tab"
            :aria-selected="activeAuditTab === 'proposals'"
            @click="activeAuditTab = 'proposals'"
          >
            提案 {{ proposals.length }}
          </button>
          <button
            :class="{ on: activeAuditTab === 'orders' }"
            role="tab"
            :aria-selected="activeAuditTab === 'orders'"
            @click="activeAuditTab = 'orders'"
          >
            执行 {{ orders.length }}
          </button>
        </div>
      </div>

      <template v-if="activeAuditTab === 'proposals'">
        <div v-if="proposalsError" class="inline-error">{{ proposalsError }}</div>
        <div class="audit-toolbar">
          <span>待处理 {{ pendingProposals.length }} 条</span>
          <button
            class="btn danger"
            :disabled="batchRejecting || !pendingProposals.length"
            @click="rejectAllPending"
          >
            <X :size="12" /> {{ batchRejecting ? '拒绝中…' : '批量拒绝待处理' }}
          </button>
        </div>
        <div
          v-if="proposals.length"
          class="table-scroll"
          tabindex="0"
          aria-label="交易提案审计流水，可横向滚动"
        >
          <table class="tbl proposal-table">
            <thead>
              <tr>
                <th>创建时间</th><th>标的</th><th>方向</th><th class="r">数量</th>
                <th class="r">预计金额</th><th>模式</th><th>状态</th><th class="r">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="proposal in proposals" :key="proposal.id">
                <td class="xs dim mono">{{ fmtTime(proposal.created_at) }}</td>
                <td class="num strong">{{ proposal.symbol }}</td>
                <td>
                  <span class="badge" :class="proposal.side === 'BUY' ? 'green' : 'red'">
                    {{ proposal.side }}
                  </span>
                </td>
                <td class="r num">{{ fmtNum(proposal.quantity, 0) }}</td>
                <td class="r num">¥{{ fmtNum(proposal.estimated_notional, 0) }}</td>
                <td class="xs dim">{{ proposal.mode === 'confirm_to_trade' ? '人工确认' : proposal.mode }}</td>
                <td>
                  <span class="badge" :class="proposalStatus(proposal.status).cls">
                    {{ proposalStatus(proposal.status).label }}
                  </span>
                </td>
                <td class="r audit-actions">
                  <button
                    v-if="proposal.status === 'pending'"
                    class="btn ghost"
                    :disabled="reviewingId !== null || executingId !== null"
                    @click="rejectProposal(proposal)"
                  >
                    拒绝
                  </button>
                  <button
                    v-if="['pending', 'approved'].includes(proposal.status)"
                    class="btn primary"
                    :disabled="executingId !== null || reviewingId !== null"
                    @click="confirmExecute(proposal)"
                  >
                    <RefreshCw v-if="executingId === proposal.id" :size="12" class="spin" />
                    <Play v-else :size="12" />
                    {{ executingId === proposal.id ? '执行中…' : '确认执行' }}
                  </button>
                  <span
                    v-else-if="proposal.status === 'approved_no_execution'"
                    class="xs dim"
                  >
                    执行开关关闭
                  </span>
                  <span v-else class="xs dim">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-hint">
          {{ proposalsError ? '无法读取提案；未用缓存或示例补位。' : '暂无提案记录。' }}
        </div>
      </template>

      <template v-else>
        <div v-if="ordersError" class="inline-error">{{ ordersError }}</div>
        <div class="order-state-tabs" role="tablist" aria-label="模拟订单三态筛选">
          <button
            v-for="item in [
              { key: 'active', label: '进行中' },
              { key: 'filled', label: '已成交' },
              { key: 'exception', label: '异常 / 撤单' },
            ]"
            :key="item.key"
            :class="{ on: activeOrderState === item.key }"
            role="tab"
            :aria-selected="activeOrderState === item.key"
            @click="activeOrderState = item.key as OrderState"
          >
            <span class="state-dot" :class="item.key" />
            {{ item.label }}
            <b class="num">{{ orderCounts[item.key as OrderState] }}</b>
          </button>
        </div>
        <div
          v-if="visibleOrders.length"
          class="table-scroll"
          tabindex="0"
          aria-label="模拟订单执行流水，可横向滚动"
        >
          <table class="tbl order-table">
            <thead>
              <tr>
                <th>创建时间</th><th>标的</th><th>方向</th><th class="r">委托价</th>
                <th class="r">成交均价</th><th class="r">成交 / 委托</th><th>环境</th><th>状态</th><th>富途单号</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="order in visibleOrders" :key="order.id">
                <td class="xs dim mono">{{ fmtTime(order.created_at) }}</td>
                <td class="num strong">{{ order.symbol }}</td>
                <td>
                  <span class="badge" :class="order.side === 'BUY' ? 'green' : 'red'">
                    {{ order.side }}
                  </span>
                </td>
                <td class="r num">¥{{ fmtNum(order.price, 2) }}</td>
                <td class="r num">
                  {{ order.avg_fill_price === null ? '—' : `¥${fmtNum(order.avg_fill_price, 2)}` }}
                </td>
                <td class="r num">{{ fmtNum(order.filled_qty, 0) }} / {{ fmtNum(order.qty, 0) }}</td>
                <td><span class="badge purple">{{ order.environment }}</span></td>
                <td>
                  <span class="badge" :class="orderStatus(order.status).cls">
                    {{ orderStatus(order.status).label }}
                  </span>
                  <div v-if="order.error" class="order-error">{{ order.error }}</div>
                </td>
                <td class="xs dim mono">{{ order.futu_order_id || '待回写' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-hint">
          该状态下暂无模拟订单；不会用历史委托或占位数据补齐。
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.alerts-page {
  min-width: 0;
}
.alerts-head {
  align-items: center;
}
.alerts-head > div:first-child {
  display: flex;
  align-items: baseline;
  gap: 12px;
  min-width: 0;
}
.head-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.page-message {
  margin-bottom: 12px;
}
.filter-row {
  display: flex;
  gap: 5px;
  margin-bottom: 12px;
  overflow-x: auto;
  padding-bottom: 2px;
}
.filter-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  flex: none;
  border: 1px solid transparent;
  border-radius: 999px;
  padding: 5px 10px;
  color: var(--text-3);
  background: transparent;
  font: 12px var(--font-ui);
  cursor: pointer;
}
.filter-button:hover {
  color: var(--text-1);
  background: rgba(148, 163, 198, 0.06);
}
.filter-button.on {
  color: #dbeafe;
  border-color: rgba(96, 165, 250, 0.3);
  background: rgba(37, 99, 235, 0.12);
}
.filter-button .num {
  color: var(--text-3);
  font-size: 10px;
}
.alerts-layout {
  display: grid;
  grid-template-columns: minmax(390px, 0.92fr) minmax(520px, 1.38fr);
  gap: 12px;
  align-items: start;
}
.alert-list-panel {
  padding: 6px;
  max-height: 600px;
}
.alert-list-panel .panel-title {
  margin: -6px -6px 2px;
}
.alert-list {
  overflow-y: auto;
  max-height: 536px;
  padding: 2px;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid transparent;
  border-radius: var(--r-md);
  padding: 8px;
  transition: border-color var(--t-fast), background var(--t-fast);
}
.alert-row:hover {
  background: rgba(148, 163, 198, 0.045);
}
.alert-row.selected {
  border-color: rgba(96, 165, 250, 0.36);
  background: rgba(37, 99, 235, 0.09);
}
.alert-select {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
  border: 0;
  padding: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.alert-select:focus-visible,
.read-button:focus-visible,
.filter-button:focus-visible,
.order-state-tabs button:focus-visible {
  outline: 2px solid var(--line-focus);
  outline-offset: 2px;
}
.alert-date {
  display: grid;
  flex: 0 0 72px;
  color: var(--text-3);
  font-size: 10px;
  line-height: 1.35;
}
.alert-date b {
  color: var(--text-2);
  font-weight: 500;
}
.alert-copy {
  display: grid;
  gap: 3px;
  flex: 1;
  min-width: 0;
}
.alert-primary {
  display: flex;
  align-items: center;
  gap: 7px;
}
.alert-primary b {
  font-size: 13px;
}
.alert-reason {
  overflow: hidden;
  color: var(--text-3);
  font-size: 11px;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.alert-mobile-time {
  display: none;
  color: var(--text-3);
  font-size: 9px;
}
.alert-amount {
  flex: 0 0 86px;
  color: var(--text-2);
  font-size: 11px;
  text-align: right;
}
.read-button {
  flex: none;
  border: 0;
  border-left: 1px solid var(--line-1);
  padding: 5px 2px 5px 9px;
  color: var(--accent-hi);
  background: transparent;
  font: 11px var(--font-ui);
  cursor: pointer;
}
.read-state {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  flex: none;
  color: var(--text-3);
  font-size: 10px;
}
.decision-panel {
  min-height: 418px;
}
.decision-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.decision-symbol {
  display: flex;
  align-items: center;
  gap: 8px;
}
.decision-symbol b {
  font-size: 20px;
}
.decision-time {
  margin-top: 4px;
  color: var(--text-3);
  font-size: 10.5px;
}
.workflow-rail {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--line-1);
  border-radius: var(--r-md);
  margin-bottom: 12px;
  overflow: hidden;
}
.workflow-step {
  position: relative;
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  padding: 9px;
  background: rgba(148, 163, 198, 0.025);
}
.workflow-step + .workflow-step {
  border-left: 1px solid var(--line-1);
}
.step-index {
  display: grid;
  place-items: center;
  flex: 0 0 20px;
  height: 20px;
  border: 1px solid var(--line-2);
  border-radius: 50%;
  color: var(--text-3);
  font: 10px var(--font-mono);
}
.workflow-step.done .step-index {
  border-color: rgba(52, 211, 153, 0.45);
  color: var(--up);
  background: rgba(52, 211, 153, 0.09);
}
.workflow-step.blocked .step-index {
  border-color: rgba(248, 113, 113, 0.45);
  color: var(--down);
  background: rgba(248, 113, 113, 0.08);
}
.workflow-step span:last-child {
  display: grid;
  min-width: 0;
}
.workflow-step b {
  font-size: 11px;
  font-weight: 600;
}
.workflow-step small {
  overflow: hidden;
  color: var(--text-3);
  font-size: 9.5px;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.decision-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr 1.18fr;
  border-block: 1px solid var(--line-1);
  margin-bottom: 14px;
}
.decision-metrics > div {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 3px 7px;
  padding: 10px 12px;
  min-width: 0;
}
.decision-metrics > div + div {
  border-left: 1px solid var(--line-1);
}
.decision-metrics svg {
  grid-row: 1 / 3;
  color: var(--cyan);
  margin-top: 2px;
}
.decision-metrics span {
  color: var(--text-3);
  font-size: 10px;
}
.decision-metrics b {
  overflow: hidden;
  font-size: 12px;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.decision-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.12fr) minmax(250px, 0.88fr);
  gap: 16px;
}
.decision-grid h2 {
  margin-bottom: 8px;
  color: var(--text-2);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.reason-block ul {
  display: grid;
  gap: 6px;
  margin: 0;
  padding-left: 17px;
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.55;
}
.reason-block li::marker {
  color: var(--accent-hi);
}
.invalidation {
  display: flex;
  gap: 8px;
  border-top: 1px solid var(--line-1);
  margin-top: 10px;
  padding-top: 9px;
  color: var(--warn);
  font-size: 11px;
  line-height: 1.5;
}
.invalidation svg {
  flex: none;
  margin-top: 2px;
}
.invalidation span {
  display: grid;
  color: var(--text-3);
}
.invalidation b {
  color: var(--warn);
  font-weight: 500;
}
.model-line {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 9px;
  color: var(--text-3);
  font-size: 10px;
}
.model-line b {
  color: var(--text-2);
  font-weight: 500;
}
.risk-block {
  border-left: 1px solid var(--line-1);
  padding-left: 16px;
}
.risk-skeleton {
  height: 138px;
}
.check-row {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  border-bottom: 1px solid var(--line-1);
  padding: 5px 0;
  color: var(--text-2);
  font-size: 11px;
  line-height: 1.45;
}
.check-row svg {
  flex: none;
  margin-top: 2px;
}
.risk-result {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 10px;
  color: var(--text-3);
  font-size: 10px;
}
.proposal-button {
  width: 100%;
  margin-top: 10px;
}
.existing-proposal {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border: 1px solid var(--line-1);
  border-radius: var(--r-sm);
  margin-top: 10px;
  padding: 7px 9px;
  color: var(--text-3);
  font-size: 11px;
}
.audit-panel {
  margin-top: 12px;
  padding-bottom: 6px;
}
.audit-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  border-bottom: 1px solid var(--line-1);
  margin: -16px -16px 10px;
  padding: 11px 16px;
}
.audit-head h2 {
  font-size: 12.5px;
}
.audit-head p {
  margin-top: 2px;
  color: var(--text-3);
  font-size: 10.5px;
}
.audit-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
  color: var(--text-3);
  font-size: 11px;
}
.audit-toolbar .btn,
.audit-actions .btn {
  padding: 4px 8px;
  font-size: 11px;
}
.audit-actions {
  white-space: nowrap;
}
.audit-actions .btn + .btn {
  margin-left: 4px;
}
.table-scroll {
  max-width: 100%;
  overflow-x: auto;
}
.proposal-table {
  min-width: 900px;
}
.order-table {
  min-width: 1040px;
}
.strong {
  font-weight: 650;
}
.inline-error {
  border-left: 2px solid var(--down);
  margin-bottom: 8px;
  padding: 5px 9px;
  color: #fca5a5;
  font-size: 11px;
}
.order-state-tabs {
  display: flex;
  gap: 18px;
  border-bottom: 1px solid var(--line-1);
  margin: -2px 0 6px;
}
.order-state-tabs button {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 0;
  padding: 7px 2px 9px;
  color: var(--text-3);
  background: transparent;
  font: 11.5px var(--font-ui);
  cursor: pointer;
}
.order-state-tabs button.on {
  color: var(--text-1);
}
.order-state-tabs button.on::after {
  content: '';
  position: absolute;
  right: 0;
  bottom: -1px;
  left: 0;
  height: 2px;
  background: var(--accent-hi);
}
.order-state-tabs b {
  color: inherit;
  font-size: 10px;
}
.state-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}
.state-dot.active {
  background: var(--accent-hi);
  box-shadow: 0 0 8px rgba(96, 165, 250, 0.65);
}
.state-dot.filled {
  background: var(--up);
}
.state-dot.exception {
  background: var(--down);
}
.order-error {
  max-width: 220px;
  margin-top: 3px;
  color: var(--down);
  font-size: 9.5px;
  white-space: normal;
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
@media (max-width: 1180px) {
  .alerts-layout {
    grid-template-columns: 1fr;
  }
  .alert-list-panel {
    max-height: 430px;
  }
  .alert-list {
    max-height: 366px;
  }
}
@media (max-width: 760px) {
  .alerts-head,
  .alerts-head > div:first-child {
    align-items: flex-start;
  }
  .alerts-head {
    flex-wrap: wrap;
  }
  .alerts-head > div:first-child {
    display: grid;
    gap: 2px;
  }
  .head-actions {
    width: 100%;
    margin-left: 0;
  }
  .head-actions .btn {
    flex: 1;
  }
  .workflow-rail {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .workflow-step:nth-child(3) {
    border-left: 0;
    border-top: 1px solid var(--line-1);
  }
  .workflow-step:nth-child(4) {
    border-top: 1px solid var(--line-1);
  }
  .decision-metrics {
    grid-template-columns: 1fr;
  }
  .decision-metrics > div + div {
    border-top: 1px solid var(--line-1);
    border-left: 0;
  }
  .decision-grid {
    grid-template-columns: 1fr;
  }
  .risk-block {
    border-top: 1px solid var(--line-1);
    border-left: 0;
    padding-top: 14px;
    padding-left: 0;
  }
  .audit-head {
    align-items: flex-start;
    flex-direction: column;
  }
}
@media (max-width: 520px) {
  .alert-date,
  .alert-amount {
    display: none;
  }
  .alert-mobile-time {
    display: block;
  }
  .alert-select {
    gap: 7px;
  }
  .alert-row {
    gap: 5px;
  }
  .decision-symbol b {
    font-size: 17px;
  }
  .workflow-rail {
    grid-template-columns: 1fr;
  }
  .workflow-step + .workflow-step {
    border-top: 1px solid var(--line-1);
    border-left: 0;
  }
  .audit-head .tab-pills {
    width: 100%;
  }
  .audit-head .tab-pills button {
    flex: 1;
  }
  .order-state-tabs {
    gap: 8px;
    overflow-x: auto;
  }
  .order-state-tabs button {
    flex: none;
  }
}
</style>
