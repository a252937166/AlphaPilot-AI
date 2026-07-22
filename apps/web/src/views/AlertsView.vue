<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, Check, Play, X, Zap } from 'lucide-vue-next'
import { api } from '../api'
import type {
  PersistedTradeProposal,
  StockOverviewResponse,
  TradeProposalInput,
  TradeRiskDecision,
} from '../api'
import { actionMeta, fmtNum, fmtPct, fmtTime } from '../format'
import ConfRing from '../components/ConfRing.vue'

const loading = ref(true)
const refreshing = ref(false)
const error = ref('')
const alerts = ref<any[]>([])
const proposals = ref<any[]>([])
const filter = ref('ALL')
const selected = ref<any>(null)
const riskChecking = ref(false)
const riskDecision = ref<TradeRiskDecision | null>(null)
const evaluatedProposal = ref<TradeProposalInput | null>(null)
const proposalCreating = ref(false)
const proposalFeedback = ref('')
const proposalFeedbackOk = ref(false)
let selectionEpoch = 0

const FILTERS = [
  { key: 'ALL', label: '全部' },
  { key: 'BUY_CANDIDATE', label: '买入候选' },
  { key: 'REDUCE', label: '减仓' },
  { key: 'EXIT', label: '退出' },
  { key: 'REVIEW_REQUIRED', label: '需复核' },
  { key: 'WATCH', label: '观察' },
]

const visibleAlerts = computed(() =>
  filter.value === 'ALL'
    ? alerts.value
    : alerts.value.filter((alert) => alert.action === filter.value),
)

const BUY_ACTIONS = new Set(['BUY_CANDIDATE', 'ADD'])
const SELL_ACTIONS = new Set(['REDUCE', 'EXIT', 'STOP'])

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

function proposalPreconditions(alert: any, overview: StockOverviewResponse): string[] {
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
    (side === 'BUY' && suggestedNotional < 0) ||
    (side === 'SELL' && suggestedNotional > 0)
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
    last !== null &&
    last > 0 &&
    suggestedNotional !== null &&
    Math.floor(Math.abs(suggestedNotional) / last / 100) * 100 < 100
  ) {
    reasons.push('建议金额不足一手，不能强制放大到 100 股。')
  }
  return reasons
}

function buildProposal(alert: any, overview: StockOverviewResponse): TradeProposalInput | null {
  const side = proposalSide(alert.action)
  const last = finiteNumber(overview.quote?.last)
  const confidence = finiteNumber(alert.confidence)
  const suggestedNotional = finiteNumber(alert.suggested_notional)
  const marketDataAsOf = overview.quote?.as_of
  const modelVersion = String(alert.model_version || '').trim()
  const alertId = Number(alert.id)
  if (
    !side ||
    last === null ||
    last <= 0 ||
    confidence === null ||
    suggestedNotional === null ||
    !marketDataAsOf ||
    !modelVersion ||
    !Number.isInteger(alertId) ||
    alertId <= 0
  ) {
    return null
  }
  const quantity = Math.floor(Math.abs(suggestedNotional) / last / 100) * 100
  if (quantity < 100) return null
  const proposalId = `alert-${alertId}-${side.toLowerCase()}`
  return {
    proposal_id: proposalId,
    idempotency_key: proposalId,
    symbol: overview.symbol || String(alert.symbol),
    side,
    quantity,
    estimated_notional: Number((quantity * last).toFixed(2)),
    confidence,
    market_data_as_of: marketDataAsOf,
    model_version: modelVersion,
    mode: 'confirm_to_trade',
    source_alert_id: alertId,
    metadata: {
      source: 'alerts-view',
      source_suggested_notional: suggestedNotional,
      quote_source: overview.quote?.source,
      target_low: alert.target_low,
      target_high: alert.target_high,
    },
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [alertData, proposalData] = await Promise.all([api.alerts(80), api.proposals()])
    alerts.value = alertData.alerts || []
    proposals.value = proposalData.proposals || []
    if (alerts.value.length) {
      const current = alerts.value.find((alert) => alert.id === selected.value?.id)
      void select(current || alerts.value[0])
    }
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  } finally {
    loading.value = false
  }
}

async function refresh() {
  refreshing.value = true
  try {
    await api.refreshAlerts()
    await load()
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  } finally {
    refreshing.value = false
  }
}

async function select(alert: any) {
  const epoch = ++selectionEpoch
  proposalCreating.value = false
  selected.value = alert
  riskDecision.value = null
  evaluatedProposal.value = null
  proposalFeedback.value = ''
  proposalFeedbackOk.value = false
  riskChecking.value = true
  try {
    const overview = await api.stockOverview(String(alert.symbol))
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

async function ack(alert: any) {
  try {
    await api.ackAlert(alert.id)
    alert.acknowledged = true
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  }
}

async function createProposal() {
  const proposal = evaluatedProposal.value
  if (!proposal || !riskDecision.value?.approved || proposalCreating.value) return
  const epoch = selectionEpoch
  proposalCreating.value = true
  proposalFeedback.value = ''
  proposalFeedbackOk.value = false
  try {
    const result = await api.createProposal({ proposal })
    if (epoch !== selectionEpoch) return
    riskDecision.value = result.risk_decision
    proposals.value = [
      result.proposal,
      ...proposals.value.filter((item) => item.id !== result.proposal.id),
    ]
    if (!result.risk_decision.approved || result.proposal.status !== 'pending') {
      proposalFeedback.value =
        result.risk_decision.reasons.join('；') || `提案状态为 ${result.proposal.status}。`
      return
    }
    proposalFeedbackOk.value = true
    proposalFeedback.value = '提案已入库，等待人工确认。'
    try {
      const proposalData = await api.proposals()
      if (epoch === selectionEpoch) proposals.value = proposalData.proposals || []
    } catch (listExc: any) {
      if (epoch === selectionEpoch) {
        proposalFeedback.value = `提案已入库，审计列表暂未刷新：${String(listExc?.message || listExc)}`
      }
    }
  } catch (exc: any) {
    if (Number(exc?.status) === 409) {
      let proposalData: Awaited<ReturnType<typeof api.proposals>>
      try {
        proposalData = await api.proposals()
      } catch (listExc: any) {
        if (epoch === selectionEpoch) {
          proposalFeedback.value = `提案可能已存在，但审计列表刷新失败：${String(listExc?.message || listExc)}`
        }
        return
      }
      if (epoch !== selectionEpoch) return
      proposals.value = proposalData.proposals || []
      const existing = proposals.value.find(
        (item: PersistedTradeProposal) =>
          item.proposal_id === proposal.proposal_id
          || item.idempotency_key === proposal.idempotency_key,
      )
      if (existing) {
        proposalFeedbackOk.value = existing.status === 'pending'
        proposalFeedback.value =
          existing.status === 'pending'
            ? '相同提醒的提案已存在，仍在等待人工确认。'
            : `相同提醒的提案已存在，当前状态：${(STATUS_META[existing.status] || { label: existing.status }).label}。`
      } else {
        proposalFeedback.value = String(exc.message || exc)
      }
    } else if (epoch === selectionEpoch) {
      proposalFeedback.value = String(exc.message || exc)
    }
  } finally {
    if (epoch === selectionEpoch) proposalCreating.value = false
  }
}

async function reviewProposal(proposal: any, approve: boolean) {
  try {
    const result = approve
      ? await api.approveProposal(proposal.id)
      : await api.rejectProposal(proposal.id)
    Object.assign(proposal, result.proposal)
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  }
}

const STATUS_META: Record<string, { label: string; cls: string }> = {
  pending: { label: '待处理', cls: 'blue' },
  approved: { label: '已批准', cls: 'green' },
  approved_no_execution: { label: '已批准·未执行', cls: 'green' },
  rejected: { label: '已拒绝', cls: 'gray' },
  rejected_by_risk: { label: '风控拒绝', cls: 'red' },
}

onMounted(load)
</script>

<template>
  <div class="alerts-page">
    <div class="page-head">
      <h1>交易提醒 / AI执行</h1>
      <span class="sub">结构化提醒 → 风控校验 → 人工确认（执行默认禁用）</span>
      <div style="margin-left: auto">
        <button class="btn primary" :disabled="refreshing" @click="refresh">
          <Zap :size="12" /> {{ refreshing ? '重算中…' : '重算全部信号' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="banner error" style="margin-bottom: 12px">{{ error }}</div>

    <!-- 过滤 -->
    <div style="display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap">
      <button
        v-for="item in FILTERS"
        :key="item.key"
        class="btn"
        :class="filter === item.key ? 'primary' : 'ghost'"
        style="padding: 4px 11px; font-size: 12px"
        @click="filter = item.key"
      >
        {{ item.label }}
        <span class="xs num" style="opacity: 0.65">
          {{ item.key === 'ALL' ? alerts.length : alerts.filter((a) => a.action === item.key).length }}
        </span>
      </button>
    </div>

    <div class="grid alerts-layout">
      <!-- 提醒列表 -->
      <div class="panel" style="padding: 6px">
        <div v-if="visibleAlerts.length">
          <div
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
              <span class="xs dim mono alert-time">{{ fmtTime(alert.created_at).slice(-8, -3) }}</span>
              <span class="badge" :class="actionMeta(alert.action).cls">{{ actionMeta(alert.action).label }}</span>
              <span class="alert-copy">
                <span class="num alert-symbol">{{ alert.symbol }}</span>
                <span class="xs dim alert-reason">{{ (alert.reasons || [])[0] }}</span>
              </span>
              <ConfRing :value="alert.confidence" :size="32" />
            </button>
            <button v-if="!alert.acknowledged" class="btn ghost xs" style="padding: 2px 8px" @click="ack(alert)">
              知悉
            </button>
            <span v-else class="xs dim">已读</span>
          </div>
        </div>
        <div v-else class="empty-hint">{{ loading ? '加载中…' : '暂无提醒，点右上角重算信号' }}</div>
      </div>

      <!-- 风控 -->
      <div class="panel">
        <div class="panel-title">风控校验 <span class="extra">SIMULATE 账户实时组合</span></div>
        <template v-if="selected">
          <div style="margin-bottom: 10px; display: flex; align-items: center; gap: 8px">
            <b class="num">{{ selected.symbol }}</b>
            <span class="badge" :class="actionMeta(selected.action).cls">
              {{ actionMeta(selected.action).label }}
            </span>
          </div>
          <div v-if="riskChecking" class="skeleton" style="height: 120px" />
          <template v-else-if="riskDecision">
            <div v-for="(reason, index) in riskDecision.reasons" :key="index" class="check-row">
              <Check v-if="riskDecision.approved" :size="12" class="up" style="flex: none; margin-top: 2px" />
              <X v-else :size="12" class="down" style="flex: none; margin-top: 2px" />
              <span class="xs muted">{{ reason }}</span>
            </div>
            <div style="margin: 12px 0 0; text-align: center">
              <span class="badge" :class="riskDecision.approved ? 'green' : 'red'" style="font-size: 12.5px; padding: 5px 14px">
                {{ riskDecision.approved ? '全部风控检查通过' : '风控未通过' }}
              </span>
              <div class="xs dim" style="margin-top: 5px" v-if="riskDecision.requires_human_confirmation">
                该模式要求人工确认
              </div>
            </div>
            <button
              class="btn primary"
              style="width: 100%; margin-top: 12px"
              :disabled="!riskDecision.approved || !evaluatedProposal || proposalCreating"
              @click="createProposal"
            >
              <Play :size="12" /> {{ proposalCreating ? '入库校验中…' : '生成交易提案' }}
            </button>
            <div
              v-if="proposalFeedback"
              class="banner"
              :class="proposalFeedbackOk ? '' : 'error'"
              style="margin-top: 8px"
            >
              {{ proposalFeedback }}
            </div>
            <div class="xs dim" style="margin-top: 6px; text-align: center">提案仅入库审计，不提交真实订单</div>
          </template>
        </template>
        <div v-else class="empty-hint">选择左侧提醒查看风控详情</div>
      </div>

      <!-- 详情 -->
      <div class="panel">
        <div class="panel-title">推荐理由</div>
        <template v-if="selected">
          <div style="display: grid; gap: 7px">
            <div v-for="(reason, index) in selected.reasons || []" :key="index" class="xs muted" style="display: flex; gap: 7px">
              <span style="color: var(--accent-hi); flex: none">›</span>
              <span>{{ reason }}</span>
            </div>
          </div>
          <div style="border-top: 1px solid var(--line-1); margin-top: 10px; padding-top: 9px">
            <div class="xs" style="color: var(--warn); display: inline-flex; gap: 4px; align-items: center">
              <AlertTriangle :size="11" /> 失效条件
            </div>
            <div class="xs muted" style="margin-top: 3px">{{ selected.invalidation }}</div>
          </div>
          <div class="kv" style="margin-top: 8px"><span class="k">建议仓位变化</span><span class="num xs">{{ fmtPct(selected.suggested_position_change * 100, 0) }}</span></div>
          <div class="kv"><span class="k">置信度</span><span class="num xs">{{ (selected.confidence * 100).toFixed(0) }}%</span></div>
          <div class="kv"><span class="k">模型</span><span class="mono xs dim">{{ selected.model_version }}</span></div>
          <div class="kv"><span class="k">过期时间</span><span class="mono xs dim">{{ fmtTime(selected.expires_at).slice(5, -3) }}</span></div>
        </template>
        <div v-else class="empty-hint">未选择提醒</div>
      </div>
    </div>

    <!-- 提案流水 -->
    <div class="panel" style="margin-top: 12px; padding-bottom: 6px">
      <div class="panel-title">交易提案审计流水 <span class="extra">执行网关默认禁用</span></div>
      <div v-if="proposals.length" class="table-scroll" tabindex="0" aria-label="交易提案审计流水，可横向滚动">
      <table class="tbl">
        <thead>
          <tr>
            <th>时间</th><th>标的</th><th>方向</th><th class="r">数量</th><th class="r">金额</th>
            <th class="r">置信</th><th>模式</th><th>状态</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="proposal in proposals" :key="proposal.id">
            <td class="xs dim mono">{{ fmtTime(proposal.created_at).slice(5, -3) }}</td>
            <td class="num" style="font-weight: 600">{{ proposal.symbol }}</td>
            <td>
              <span class="badge" :class="proposal.side === 'BUY' ? 'green' : 'red'">{{ proposal.side }}</span>
            </td>
            <td class="r num">{{ proposal.quantity }}</td>
            <td class="r num">¥{{ fmtNum(proposal.estimated_notional, 0) }}</td>
            <td class="r num">{{ (proposal.confidence * 100).toFixed(0) }}%</td>
            <td class="xs dim mono">{{ proposal.mode }}</td>
            <td>
              <span class="badge" :class="(STATUS_META[proposal.status] || { cls: 'gray' }).cls">
                {{ (STATUS_META[proposal.status] || { label: proposal.status }).label }}
              </span>
            </td>
            <td>
              <template v-if="proposal.status === 'pending'">
                <button class="btn ghost xs" style="padding: 2px 8px" @click="reviewProposal(proposal, true)">批准</button>
                <button class="btn ghost xs" style="padding: 2px 8px" @click="reviewProposal(proposal, false)">拒绝</button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
      <div v-else class="empty-hint">暂无提案记录</div>
    </div>
  </div>
</template>

<style scoped>
.alert-row {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 9px 10px;
  border-radius: var(--r-md);
  cursor: pointer;
  border: 1px solid transparent;
  transition: background var(--t-fast), border-color var(--t-fast);
}
.alert-select {
  min-width: 0;
  flex: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.alert-select:focus-visible {
  outline: 2px solid var(--line-focus);
  outline-offset: 3px;
  border-radius: var(--r-sm);
}
.alert-time {
  width: 36px;
  flex: none;
}
.alert-copy {
  min-width: 0;
  flex: 1;
  display: grid;
}
.alert-symbol {
  font-size: 12.5px;
  font-weight: 600;
}
.alert-reason {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.alert-row:hover {
  background: rgba(148, 163, 198, 0.05);
}
.alert-row.selected {
  border-color: rgba(96, 165, 250, 0.4);
  background: rgba(59, 130, 246, 0.09);
}
.check-row {
  display: flex;
  gap: 7px;
  padding: 5px 0;
  border-bottom: 1px solid var(--line-1);
  align-items: flex-start;
}
.check-row:last-of-type {
  border-bottom: none;
}
.alerts-layout {
  grid-template-columns: minmax(340px, 1fr) minmax(270px, 320px) minmax(250px, 300px);
  align-items: start;
}
.table-scroll {
  max-width: 100%;
  overflow-x: auto;
}
.table-scroll .tbl {
  min-width: 780px;
}
@media (max-width: 1220px) {
  .alerts-layout {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .alerts-layout > :first-child {
    grid-column: 1 / -1;
  }
}
@media (max-width: 700px) {
  .alerts-layout {
    grid-template-columns: 1fr;
  }
  .alerts-layout > :first-child {
    grid-column: auto;
  }
  .alert-row,
  .alert-select {
    gap: 7px;
  }
  .alert-time {
    display: none;
  }
}
</style>
