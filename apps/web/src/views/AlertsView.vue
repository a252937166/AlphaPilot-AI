<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { AlertTriangle, Check, Play, X, Zap } from 'lucide-vue-next'
import { api } from '../api'
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
const riskDecision = ref<any>(null)

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

const DEMO_PORTFOLIO = {
  equity: 1_000_000,
  cash: 500_000,
  daily_pnl_pct: 0,
  current_position_pct: 0.05,
  sector_position_pct: 0.12,
  open_orders_for_symbol: 0,
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [alertData, proposalData] = await Promise.all([api.alerts(80), api.proposals()])
    alerts.value = alertData.alerts || []
    proposals.value = proposalData.proposals || []
    if (!selected.value && alerts.value.length) select(alerts.value[0])
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
  selected.value = alert
  riskDecision.value = null
  riskChecking.value = true
  try {
    const change = Number(alert.suggested_position_change || 0)
    const notional = Math.max(10_000, Math.abs(change) * DEMO_PORTFOLIO.equity)
    riskDecision.value = await api.evaluateTrade({
      proposal: {
        proposal_id: `ui-${alert.id}-${Date.now()}`,
        idempotency_key: `ui-${alert.id}`,
        symbol: alert.symbol,
        side: change >= 0 ? 'BUY' : 'SELL',
        quantity: 100,
        estimated_notional: notional,
        confidence: alert.confidence,
        market_data_as_of: new Date().toISOString(),
        model_version: alert.model_version || 'unknown',
        mode: 'confirm_to_trade',
        source_alert_id: String(alert.id),
      },
      portfolio: DEMO_PORTFOLIO,
    })
  } catch (exc: any) {
    riskDecision.value = { approved: false, reasons: [String(exc.message || exc)] }
  } finally {
    riskChecking.value = false
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
  if (!selected.value) return
  const alert = selected.value
  const change = Number(alert.suggested_position_change || 0)
  try {
    await api.createProposal({
      proposal: {
        proposal_id: `alert-${alert.id}-${Date.now()}`,
        idempotency_key: `alert-${alert.id}-${new Date().toISOString().slice(0, 10)}`,
        symbol: alert.symbol,
        side: change >= 0 ? 'BUY' : 'SELL',
        quantity: 100,
        estimated_notional: Math.max(10_000, Math.abs(change) * DEMO_PORTFOLIO.equity),
        confidence: alert.confidence,
        market_data_as_of: new Date().toISOString(),
        model_version: alert.model_version || 'unknown',
        mode: 'confirm_to_trade',
        source_alert_id: String(alert.id),
      },
      portfolio: DEMO_PORTFOLIO,
    })
    const proposalData = await api.proposals()
    proposals.value = proposalData.proposals || []
  } catch (exc: any) {
    error.value = String(exc.message || exc)
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
  <div>
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

    <div class="grid" style="grid-template-columns: minmax(0, 1fr) 320px 300px; align-items: start">
      <!-- 提醒列表 -->
      <div class="panel" style="padding: 6px">
        <div v-if="visibleAlerts.length">
          <div
            v-for="alert in visibleAlerts"
            :key="alert.id"
            class="alert-row"
            :class="{ selected: selected?.id === alert.id }"
            @click="select(alert)"
          >
            <span class="xs dim mono" style="width: 36px">{{ fmtTime(alert.created_at).slice(-8, -3) }}</span>
            <span class="badge" :class="actionMeta(alert.action).cls">{{ actionMeta(alert.action).label }}</span>
            <div style="flex: 1; min-width: 0">
              <div class="num" style="font-weight: 600; font-size: 12.5px">{{ alert.symbol }}</div>
              <div class="xs dim" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis">
                {{ (alert.reasons || [])[0] }}
              </div>
            </div>
            <ConfRing :value="alert.confidence" :size="32" />
            <button v-if="!alert.acknowledged" class="btn ghost xs" style="padding: 2px 8px" @click.stop="ack(alert)">
              知悉
            </button>
            <span v-else class="xs dim">已读</span>
          </div>
        </div>
        <div v-else class="empty-hint">{{ loading ? '加载中…' : '暂无提醒，点右上角重算信号' }}</div>
      </div>

      <!-- 风控 -->
      <div class="panel">
        <div class="panel-title">风控校验 <span class="extra">演示组合 ¥1,000,000</span></div>
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
              :disabled="!riskDecision.approved"
              @click="createProposal"
            >
              <Play :size="12" /> 生成交易提案
            </button>
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
      <table class="tbl" v-if="proposals.length">
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
</style>
