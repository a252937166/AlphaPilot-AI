<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Activity,
  AlertTriangle,
  ChevronRight,
  Grid3x3,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Star,
  TrendingUp,
} from 'lucide-vue-next'
import { api } from '../api'
import { actionMeta, fmtNum, fmtPct, fmtTime, heatColor, pctClass, regimeMeta } from '../format'
import ConfRing from '../components/ConfRing.vue'
import EChart from '../components/EChart.vue'
import GaugeArc from '../components/GaugeArc.vue'
import {
  CHART_COLORS,
  areaGradient,
  categoryAxis,
  glowLine,
  tooltipStyle,
  valueAxis,
} from '../chartTheme'

const router = useRouter()
const loading = ref(true)
const error = ref('')
const data = ref<any>(null)
const allAlerts = ref<any[]>([])
const report = ref<any>(null)
const indexSeries = ref<Record<string, { date: string; close: number }[]>>({})
const indexNames = ref<Record<string, string>>({})
const activeIndex = ref('SH.000001')
const compareMode = ref(false)

const INDEX_COLORS: Record<string, string> = {
  'SH.000001': CHART_COLORS.cyan,
  'SZ.399001': CHART_COLORS.accent,
  'SZ.399006': CHART_COLORS.purple,
  'SH.000300': CHART_COLORS.warn,
  'SH.000905': CHART_COLORS.up,
}

const regime = computed(() => data.value?.regime)
const indices = computed(() => data.value?.indices ?? [])
const sectors = computed(() => data.value?.sectors ?? [])
const watchlist = computed(() => data.value?.watchlist ?? [])
const feedAlerts = computed(() => data.value?.alerts ?? [])
const breadth = computed(() => data.value?.breadth)

/* --- today's deduped signal stats (one per symbol, latest wins) --- */
const todayLatest = computed(() => {
  const today = new Date().toDateString()
  const bySymbol: Record<string, any> = {}
  for (const alert of allAlerts.value) {
    if (new Date(alert.created_at).toDateString() !== today) continue
    if (!bySymbol[alert.symbol] || alert.created_at > bySymbol[alert.symbol].created_at) {
      bySymbol[alert.symbol] = alert
    }
  }
  return Object.values(bySymbol)
})
const opportunities = computed(() =>
  todayLatest.value.filter((a: any) => ['BUY_CANDIDATE', 'ADD'].includes(a.action)),
)
const highConfidence = computed(() =>
  todayLatest.value.filter((a: any) => Number(a.confidence) >= 0.6),
)
const riskSignals = computed(() =>
  todayLatest.value.filter((a: any) => ['REDUCE', 'EXIT', 'STOP'].includes(a.action)),
)

const breadthRatio = computed(() => {
  if (!breadth.value) return 50
  const advancers = breadth.value.advancers || 0
  const decliners = breadth.value.decliners || 0
  return Math.round((advancers / Math.max(1, advancers + decliners)) * 100)
})

const activeQuote = computed(() =>
  indices.value.find((item: any) => item.symbol === activeIndex.value),
)

/* --- index chart: single (area+glow) or all-compare (normalized) --- */
const indexChartOption = computed(() => {
  const symbols = Object.keys(indexSeries.value)
  if (!symbols.length) return {}
  if (!compareMode.value) {
    const points = indexSeries.value[activeIndex.value] || []
    if (!points.length) return {}
    const closes = points.map((point) => Number(point.close))
    const last = closes[closes.length - 1]
    const color = last >= closes[0] ? CHART_COLORS.cyan : CHART_COLORS.down
    return {
      animation: false,
      tooltip: {
        trigger: 'axis',
        ...tooltipStyle,
        valueFormatter: (value: number) => Number(value).toFixed(2),
      },
      grid: { left: 52, right: 14, top: 16, bottom: 22 },
      xAxis: categoryAxis(points.map((point) => point.date.slice(5))),
      yAxis: valueAxis({ scale: true }),
      series: [
        {
          name: indexNames.value[activeIndex.value] || activeIndex.value,
          type: 'line',
          data: closes,
          symbol: 'none',
          smooth: 0.15,
          lineStyle: glowLine(color),
          itemStyle: { color },
          areaStyle: { color: areaGradient(color, 0.3) },
          markLine: {
            symbol: 'none',
            label: {
              show: true,
              position: 'insideEndTop',
              color,
              fontSize: 10,
              fontFamily: "ui-monospace,'SF Mono',Menlo,monospace",
              formatter: (params: any) => Number(params.value).toFixed(2),
            },
            lineStyle: { color, type: 'dashed', opacity: 0.5, width: 1 },
            data: [{ yAxis: last }],
          },
        },
      ],
    }
  }
  // compare mode: normalized % lines with glow
  const base = indexSeries.value[symbols[0]] || []
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle,
      valueFormatter: (value: number) => `${Number(value).toFixed(2)}%`,
    },
    legend: { textStyle: { color: CHART_COLORS.text3, fontSize: 10 }, top: 0, itemWidth: 14, itemHeight: 2 },
    grid: { left: 42, right: 14, top: 26, bottom: 22 },
    xAxis: categoryAxis(base.map((point: any) => point.date.slice(5))),
    yAxis: valueAxis({
      axisLabel: { formatter: '{value}%', color: CHART_COLORS.text3, fontSize: 10 },
    }),
    series: symbols.map((symbol) => {
      const points = indexSeries.value[symbol] || []
      const first = Number(points[0]?.close || 1)
      return {
        name: indexNames.value[symbol] || symbol,
        type: 'line',
        symbol: 'none',
        smooth: 0.15,
        lineStyle: glowLine(INDEX_COLORS[symbol], 1.4),
        itemStyle: { color: INDEX_COLORS[symbol] },
        emphasis: { focus: 'series' },
        data: points.map((point: any) => ((Number(point.close) / first - 1) * 100).toFixed(3)),
      }
    }),
  }
})

/* --- recent activity timeline from real records --- */
const activities = computed(() => {
  const events: { time: string; text: string; cls: string }[] = []
  const batches: Record<string, number> = {}
  for (const alert of allAlerts.value.slice(0, 40)) {
    const key = String(alert.created_at).slice(0, 16)
    batches[key] = (batches[key] || 0) + 1
  }
  for (const [minute, count] of Object.entries(batches).slice(0, 4)) {
    events.push({
      time: minute,
      text: `重算自选信号 · ${count} 条`,
      cls: 'blue',
    })
  }
  if (report.value?.generated_at) {
    events.push({ time: report.value.generated_at, text: '生成每日复盘报告', cls: 'purple' })
  }
  if (data.value?.as_of) {
    events.push({ time: data.value.as_of, text: '总览数据聚合刷新', cls: 'cyan' })
  }
  return events
    .sort((a, b) => (a.time < b.time ? 1 : -1))
    .slice(0, 6)
    .map((event) => ({ ...event, display: fmtTime(event.time).slice(-8, -3) }))
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [overview, indicesResult, alertsResult, reportResult] = await Promise.all([
      api.dashboard(),
      api.marketIndices(60).catch(() => null),
      api.alerts(80).catch(() => ({ alerts: [] })),
      api.dailyReport().catch(() => null),
    ])
    data.value = overview
    allAlerts.value = alertsResult.alerts || []
    report.value = reportResult
    if (indicesResult?.series) indexSeries.value = indicesResult.series
    for (const entry of indicesResult?.symbols || []) {
      indexNames.value[entry.symbol] = entry.name
    }
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>市场总览</h1>
      <span class="sub">AI 综合解读 · 数据驱动 · 不构成投资建议</span>
      <div style="margin-left: auto">
        <button class="btn ghost" @click="load" :disabled="loading">
          <RefreshCw :size="13" :class="{ spin: loading }" />
          {{ loading ? '刷新中' : '刷新' }}
        </button>
      </div>
    </div>

    <div v-if="error" class="banner error" style="margin-bottom: 12px">加载失败：{{ error }}</div>

    <!-- skeleton -->
    <div v-if="loading && !data" class="grid" style="grid-template-columns: 1fr 328px">
      <div class="grid">
        <div class="grid" style="grid-template-columns: repeat(4, 1fr)">
          <div v-for="n in 4" :key="n" class="skeleton" style="height: 96px" />
        </div>
        <div class="skeleton" style="height: 290px" />
        <div class="skeleton" style="height: 150px" />
        <div class="skeleton" style="height: 240px" />
      </div>
      <div class="grid">
        <div class="skeleton" style="height: 300px" />
        <div class="skeleton" style="height: 220px" />
      </div>
    </div>

    <div v-if="data" class="grid" style="grid-template-columns: minmax(0, 1fr) 328px; align-items: start">
      <div class="grid">
        <!-- 统计卡行 -->
        <div class="grid" style="grid-template-columns: repeat(4, 1fr)">
          <div class="stat-card">
            <div class="stat-main">
              <div class="label">市场状态</div>
              <div style="margin: 6px 0 3px">
                <span class="badge" :class="regimeMeta(regime?.regime).cls" style="font-size: 13px; padding: 3px 10px">
                  {{ regimeMeta(regime?.regime).label }}
                </span>
              </div>
              <div class="delta">状态机 · {{ regime?.source || '—' }}</div>
            </div>
            <div class="stat-viz" style="width: 84px">
              <GaugeArc :value="regime?.confidence" size="76px" />
            </div>
          </div>

          <div class="stat-card">
            <div class="stat-main">
              <div class="label">今日机会数</div>
              <div class="value glow-cyan">{{ opportunities.length }}</div>
              <div class="delta">
                <template v-if="opportunities.length">
                  {{ opportunities.slice(0, 3).map((a: any) => a.symbol).join(' · ') }}
                </template>
                <template v-else>买入候选/加仓信号</template>
              </div>
            </div>
          </div>

          <div class="stat-card">
            <div class="stat-main">
              <div class="label">高置信信号</div>
              <div class="value glow-green">{{ highConfidence.length }}</div>
              <div class="delta">置信度 ≥ 60% · 今日去重</div>
            </div>
          </div>

          <div class="stat-card">
            <div class="stat-main">
              <div class="label">风险预警</div>
              <div class="value" :class="riskSignals.length ? 'glow-red' : 'glow-green'">
                {{ riskSignals.length }}
              </div>
              <div class="delta">
                <template v-if="riskSignals.length">
                  {{ riskSignals.slice(0, 3).map((a: any) => a.symbol).join(' · ') }}
                </template>
                <template v-else>暂无减仓/退出信号</template>
              </div>
            </div>
            <div class="stat-viz" style="width: 40px">
              <span class="icon-chip" :class="riskSignals.length ? 'amber' : 'green'">
                <AlertTriangle v-if="riskSignals.length" :size="14" />
                <ShieldCheck v-else :size="14" />
              </span>
            </div>
          </div>
        </div>

        <!-- 指数走势 -->
        <div class="panel">
          <div class="panel-title">
            <span style="display: inline-flex; align-items: baseline; gap: 10px">
              指数走势
              <template v-if="!compareMode && activeQuote">
                <span class="num" style="font-size: 15px; font-weight: 650">{{ fmtNum(activeQuote.last) }}</span>
                <span class="num xs" :class="pctClass(activeQuote.change_pct)">{{ fmtPct(activeQuote.change_pct) }}</span>
              </template>
            </span>
            <span style="display: inline-flex; gap: 8px; align-items: center">
              <span class="tab-pills" v-if="!compareMode">
                <button
                  v-for="(name, symbol) in indexNames"
                  :key="symbol"
                  :class="{ on: activeIndex === symbol }"
                  @click="activeIndex = String(symbol)"
                >
                  {{ name }}
                </button>
              </span>
              <span class="tab-pills">
                <button :class="{ on: !compareMode }" @click="compareMode = false">单指数</button>
                <button :class="{ on: compareMode }" @click="compareMode = true">全部对比</button>
              </span>
            </span>
          </div>
          <EChart v-if="Object.keys(indexSeries).length" :option="indexChartOption" height="250px" />
          <div v-else class="empty-hint">指数历史加载中…</div>
        </div>

        <!-- 板块热力 -->
        <div class="panel">
          <div class="panel-title">
            板块强度热力图
            <span class="extra">{{ data.sector_error ? 'Futu 行情不可用' : '板块抽样 · 实时' }}</span>
          </div>
          <div v-if="sectors.length" class="heat-grid">
            <div
              v-for="sector in sectors"
              :key="sector.plate_code"
              class="heat-tile"
              :style="{ background: heatColor(Number(sector.avg_change_pct)) }"
              :title="`强度 ${sector.strength} · 上涨占比 ${(sector.up_ratio * 100).toFixed(0)}%`"
            >
              <div class="t-name">{{ sector.plate_name }}</div>
              <div class="t-val">{{ fmtPct(sector.avg_change_pct) }}</div>
            </div>
          </div>
          <div v-else class="empty-hint">暂无板块数据（Futu 行情不可用时降级）</div>
        </div>

        <!-- 自选表 -->
        <div class="panel" style="padding-bottom: 6px">
          <div class="panel-title">
            自选股追踪
            <router-link to="/watchlist" class="extra" style="display: inline-flex; align-items: center; gap: 2px">
              全部自选 <ChevronRight :size="12" />
            </router-link>
          </div>
          <table class="tbl" v-if="watchlist.length">
            <thead>
              <tr>
                <th>代码 / 名称</th><th class="r">最新价</th><th class="r">涨跌</th><th>信号</th>
                <th>置信度</th><th class="r">20日预期</th><th>逻辑</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in watchlist" :key="row.symbol">
                <td class="sym" @click="router.push(`/stock/${row.symbol}`)">
                  <div style="display: flex; align-items: center; gap: 8px">
                    <Star :size="12" style="color: var(--warn); flex: none" fill="currentColor" />
                    <div>
                      <div class="name">{{ row.display_name || row.symbol }}</div>
                      <div class="code">{{ row.symbol }}</div>
                    </div>
                  </div>
                </td>
                <td class="r num">{{ fmtNum(row.last) }}</td>
                <td class="r num" :class="pctClass(row.change_pct)">{{ fmtPct(row.change_pct) }}</td>
                <td>
                  <span class="badge" :class="actionMeta(row.alert_action).cls">
                    {{ actionMeta(row.alert_action).label }}
                  </span>
                </td>
                <td><ConfRing :value="row.confidence_20d" :size="30" /></td>
                <td class="r num" :class="pctClass(row.expected_return_20d)">
                  {{ fmtPct(row.expected_return_20d, 2, false) }}
                </td>
                <td class="xs dim">{{ row.thesis_state === 'unchanged' ? '不变' : row.thesis_state }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="empty-hint">自选列表为空，去「自选追踪」页添加</div>
        </div>
      </div>

      <!-- 右栏 -->
      <div class="grid">
        <!-- AI 结论（图标要点） -->
        <div class="panel">
          <div class="panel-title">
            <span style="display: inline-flex; align-items: center; gap: 6px">
              <Sparkles :size="13" style="color: var(--accent-hi)" /> 今日 AI 结论
            </span>
            <span class="extra mono">{{ data.ai_summary?.source === 'llm' ? 'LLM' : '规则' }} · {{ fmtTime(data.as_of).slice(-8, -3) }}</span>
          </div>
          <div class="banner" style="padding: 9px 12px; font-size: 12px; margin-bottom: 12px">
            <span class="muted">{{ data.ai_summary?.text }}</span>
          </div>
          <div style="display: grid; gap: 11px">
            <div style="display: flex; gap: 10px" v-if="regime">
              <span class="icon-chip blue"><TrendingUp :size="14" /></span>
              <div style="min-width: 0">
                <div style="font-size: 12.5px; font-weight: 600">
                  市场状态：{{ regimeMeta(regime.regime).label }}
                  <span class="num dim xs">置信 {{ (regime.confidence * 100).toFixed(0) }}%</span>
                </div>
                <div class="xs dim">{{ (regime.explanation || [])[0] }}</div>
              </div>
            </div>
            <div style="display: flex; gap: 10px" v-if="sectors.length">
              <span class="icon-chip cyan"><Grid3x3 :size="14" /></span>
              <div style="min-width: 0">
                <div style="font-size: 12.5px; font-weight: 600">
                  {{ sectors[0].plate_name }}板块领涨
                  <span class="num xs" :class="pctClass(sectors[0].avg_change_pct)">{{ fmtPct(sectors[0].avg_change_pct) }}</span>
                </div>
                <div class="xs dim">
                  上涨占比 {{ (sectors[0].up_ratio * 100).toFixed(0) }}% · 龙头 {{ sectors[0].leader_name }}
                  {{ fmtPct(sectors[0].leader_change_pct) }}
                </div>
              </div>
            </div>
            <div style="display: flex; gap: 10px" v-if="breadth">
              <span class="icon-chip green"><Activity :size="14" /></span>
              <div style="min-width: 0">
                <div style="font-size: 12.5px; font-weight: 600">
                  样本宽度 <span class="num up">{{ breadth.advancers }}</span> 涨 /
                  <span class="num down">{{ breadth.decliners }}</span> 跌
                </div>
                <div class="xs dim">样本均值 {{ fmtPct(breadth.avg_change_pct) }} · 板块抽样非全市场</div>
              </div>
            </div>
            <div style="display: flex; gap: 10px">
              <span class="icon-chip amber"><AlertTriangle :size="14" /></span>
              <div style="min-width: 0">
                <div style="font-size: 12.5px; font-weight: 600">
                  风险提示：{{ riskSignals.length ? `${riskSignals.length} 条减仓/退出信号` : '暂无风险信号' }}
                </div>
                <div class="xs dim">
                  {{ riskSignals.length ? (riskSignals[0].reasons || [])[0] : '基线模型输出，仅供工程验证' }}
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 交易提醒 -->
        <div class="panel">
          <div class="panel-title">
            交易提醒
            <router-link to="/alerts" class="extra" style="display: inline-flex; align-items: center; gap: 2px">
              更多 <ChevronRight :size="12" />
            </router-link>
          </div>
          <div v-if="feedAlerts.length">
            <div v-for="alert in feedAlerts.slice(0, 5)" :key="alert.id" class="feed-row">
              <span class="badge" :class="actionMeta(alert.action).cls">{{ actionMeta(alert.action).label }}</span>
              <div style="flex: 1; min-width: 0">
                <div class="num" style="font-weight: 600; font-size: 12px">{{ alert.symbol }}</div>
                <div class="xs dim" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis">
                  {{ (alert.reasons || [])[0] }}
                </div>
              </div>
              <span class="xs dim mono">{{ fmtTime(alert.created_at).slice(-8, -3) }}</span>
            </div>
          </div>
          <div v-else class="empty-hint">暂无提醒</div>
        </div>

        <!-- 近期活动 -->
        <div class="panel">
          <div class="panel-title">近期活动</div>
          <ul class="timeline" v-if="activities.length">
            <li v-for="(event, index) in activities" :key="index">
              <div class="xs dim mono">{{ event.display }}</div>
              <div style="font-size: 12px">{{ event.text }}</div>
            </li>
          </ul>
          <div v-else class="empty-hint">暂无活动记录</div>
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
