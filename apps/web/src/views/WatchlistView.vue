<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Plus, RefreshCw, X, Zap } from 'lucide-vue-next'
import { api } from '../api'
import { actionMeta, fmtNum, fmtPct, pctClass } from '../format'
import ConfRing from '../components/ConfRing.vue'
import EChart from '../components/EChart.vue'
import { SERIES_PALETTE, tooltipStyle } from '../chartTheme'

const router = useRouter()
const loading = ref(true)
const refreshing = ref(false)
const error = ref('')
const rows = ref<any[]>([])
const activeGroup = ref('all')

const form = ref({ symbol: '', display_name: '', cost_price: '', group_name: 'core' })

const GROUP_LABELS: Record<string, string> = {
  all: '全部',
  core: '核心持仓',
  watch: '观察池',
  priority: '高优先级',
}

const groups = computed(() => {
  const names = new Set(rows.value.map((row) => row.group_name || 'core'))
  return ['all', ...names]
})

const visible = computed(() =>
  activeGroup.value === 'all'
    ? rows.value
    : rows.value.filter((row) => (row.group_name || 'core') === activeGroup.value),
)

const groupDonut = computed(() => {
  const counter: Record<string, number> = {}
  for (const row of rows.value) {
    const key = GROUP_LABELS[row.group_name || 'core'] || row.group_name
    counter[key] = (counter[key] || 0) + 1
  }
  return {
    animation: false,
    tooltip: { ...tooltipStyle },
    legend: { bottom: 0, textStyle: { color: '#9aa7c4', fontSize: 10 }, itemWidth: 8, itemHeight: 8 },
    series: [
      {
        type: 'pie',
        radius: ['58%', '78%'],
        center: ['50%', '42%'],
        label: { show: false },
        itemStyle: { borderColor: '#0a0f1c', borderWidth: 2 },
        data: Object.entries(counter).map(([name, value], index) => ({
          name,
          value,
          itemStyle: { color: SERIES_PALETTE[index % SERIES_PALETTE.length] },
        })),
      },
    ],
  }
})

const focus = computed(() =>
  rows.value
    .filter((row) => row.alert_action && !['HOLD', 'WATCH'].includes(row.alert_action))
    .slice(0, 5),
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.watchlistTrack()
    rows.value = data.rows || []
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  } finally {
    loading.value = false
  }
}

async function addItem() {
  if (!form.value.symbol.trim()) return
  try {
    await api.watchlistUpsert({
      symbol: form.value.symbol.trim(),
      display_name: form.value.display_name.trim() || undefined,
      cost_price: form.value.cost_price ? Number(form.value.cost_price) : undefined,
      group_name: form.value.group_name,
    })
    form.value = { symbol: '', display_name: '', cost_price: '', group_name: 'core' }
    await load()
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  }
}

async function removeItem(symbol: string) {
  try {
    await api.watchlistDelete(symbol)
    rows.value = rows.value.filter((row) => row.symbol !== symbol)
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  }
}

async function refreshSignals() {
  refreshing.value = true
  error.value = ''
  try {
    await api.refreshAlerts()
    await load()
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  } finally {
    refreshing.value = false
  }
}

function thesisMeta(state: string) {
  if (state === 'weakened') return { label: '逻辑转弱', cls: 'red' }
  if (state === 'strengthened') return { label: '逻辑强化', cls: 'green' }
  return { label: '逻辑不变', cls: 'gray' }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>自选追踪</h1>
      <span class="sub">信号变化 · 预测漂移 · 公告事件</span>
      <div style="margin-left: auto; display: flex; gap: 8px">
        <button class="btn" :disabled="refreshing" @click="refreshSignals">
          <Zap :size="12" /> {{ refreshing ? '重算中…' : '重算信号' }}
        </button>
        <button class="btn ghost" :disabled="loading" @click="load">
          <RefreshCw :size="12" :class="{ spin: loading }" />
        </button>
      </div>
    </div>

    <div v-if="error" class="banner error" style="margin-bottom: 12px">{{ error }}</div>

    <div class="grid" style="grid-template-columns: minmax(0, 1fr) 280px; align-items: start">
      <div class="grid">
        <!-- 添加行 -->
        <div class="panel" style="padding: 12px 14px">
          <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
            <input v-model="form.symbol" class="input mono" style="width: 120px" placeholder="代码 600519" @keyup.enter="addItem" />
            <input v-model="form.display_name" class="input" style="width: 130px" placeholder="名称（可选）" />
            <input v-model="form.cost_price" class="input num" style="width: 110px" placeholder="成本价（可选）" />
            <select v-model="form.group_name" class="input">
              <option value="core">核心持仓</option>
              <option value="watch">观察池</option>
              <option value="priority">高优先级</option>
            </select>
            <button class="btn primary" @click="addItem"><Plus :size="13" /> 加入追踪</button>
          </div>
        </div>

        <!-- 分组 -->
        <div style="display: flex; gap: 6px">
          <button
            v-for="group in groups"
            :key="group"
            class="btn"
            :class="activeGroup === group ? 'primary' : 'ghost'"
            style="padding: 4px 12px; font-size: 12px"
            @click="activeGroup = group"
          >
            {{ GROUP_LABELS[group] || group }}
            <span class="xs" style="opacity: 0.65" v-if="group === 'all'">{{ rows.length }}</span>
          </button>
        </div>

        <!-- 主表 -->
        <div class="panel" style="padding-bottom: 6px">
          <table class="tbl" v-if="visible.length">
            <thead>
              <tr>
                <th>代码/名称</th><th class="r">最新价</th><th class="r">成本</th><th class="r">盈亏</th>
                <th>信号</th><th>置信</th><th class="r">20日预期</th><th>逻辑</th><th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in visible" :key="row.symbol">
                <td class="sym" @click="router.push(`/stock/${row.symbol}`)">
                  <div class="name">{{ row.display_name || row.symbol }}</div>
                  <div class="code">{{ row.symbol }}</div>
                </td>
                <td class="r">
                  <div class="num">{{ fmtNum(row.last) }}</div>
                  <div class="xs num" :class="pctClass(row.change_pct)">{{ fmtPct(row.change_pct) }}</div>
                </td>
                <td class="r num dim">{{ fmtNum(row.cost_price) }}</td>
                <td class="r num" :class="pctClass(row.pnl_pct)">{{ fmtPct(row.pnl_pct) }}</td>
                <td>
                  <span class="badge" :class="actionMeta(row.alert_action).cls">
                    {{ actionMeta(row.alert_action).label }}
                  </span>
                </td>
                <td><ConfRing :value="row.confidence_20d" :size="30" /></td>
                <td class="r num" :class="pctClass(row.expected_return_20d)">
                  {{ fmtPct(row.expected_return_20d, 2, false) }}
                </td>
                <td>
                  <span class="badge" :class="thesisMeta(row.thesis_state).cls">{{ thesisMeta(row.thesis_state).label }}</span>
                </td>
                <td>
                  <button class="btn ghost" style="padding: 2px 6px" @click="removeItem(row.symbol)">
                    <X :size="12" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-hint">{{ loading ? '加载中…' : '该分组暂无股票' }}</div>
        </div>
      </div>

      <!-- 右栏 -->
      <div class="grid">
        <div class="panel">
          <div class="panel-title">分组构成</div>
          <EChart v-if="rows.length" :option="groupDonut" height="170px" />
          <div v-else class="empty-hint">暂无数据</div>
        </div>
        <div class="panel">
          <div class="panel-title">今日重点跟踪</div>
          <div v-if="focus.length">
            <div v-for="row in focus" :key="row.symbol" class="feed-row">
              <div style="flex: 1">
                <div style="display: flex; justify-content: space-between; align-items: center">
                  <b style="font-size: 12.5px">{{ row.display_name || row.symbol }}</b>
                  <span class="badge" :class="actionMeta(row.alert_action).cls">
                    {{ actionMeta(row.alert_action).label }}
                  </span>
                </div>
                <div class="xs dim num" style="margin-top: 2px">
                  20日 {{ row.p_up_20d ? (row.p_up_20d * 100).toFixed(0) + '%' : '—' }}
                  · 预期 {{ fmtPct(row.expected_return_20d, 1, false) }}
                </div>
              </div>
            </div>
          </div>
          <div v-else class="empty-hint">暂无需重点关注的信号</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.spin {
  animation: rotate 0.9s linear infinite;
}
@keyframes rotate {
  to {
    transform: rotate(360deg);
  }
}
</style>
