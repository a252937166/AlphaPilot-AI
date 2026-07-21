<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { AlertTriangle, RefreshCw } from 'lucide-vue-next'
import { api } from '../api'
import { actionMeta, fmtAmount, fmtDate, fmtNum, fmtPct, pctClass } from '../format'
import EChart from '../components/EChart.vue'
import ConfRing from '../components/ConfRing.vue'
import { CHART_COLORS, tooltipStyle } from '../chartTheme'

const route = useRoute()
const symbol = computed(() => String(route.params.symbol || '600519'))
const loading = ref(true)
const syncing = ref(false)
const error = ref('')
const overview = ref<any>(null)
const bars = ref<any[]>([])

const forecast = computed(() => overview.value?.forecast)
const alert = computed(() => overview.value?.alert)
const horizons = computed(() => forecast.value?.horizons ?? {})
const features = computed(() => forecast.value?.features ?? {})

const HORIZON_CARDS = [
  { label: '1日', key: '1d' },
  { label: '5日', key: '5d' },
  { label: '20日', key: '20d' },
]

const FEATURE_LABELS: Record<string, string> = {
  momentum_5d: '5日动量',
  momentum_20d: '20日动量',
  momentum_60d: '60日动量',
  ma_gap_5_20: '均线差 5/20',
  volatility_20d: '20日波动率',
  drawdown_60d: '60日回撤',
  price_position_60d: '60日价位',
  volume_ratio_5_20: '量比 5/20',
}

type KlineBar = {
  date?: unknown
  open?: unknown
  close?: unknown
  low?: unknown
  high?: unknown
  volume?: unknown
  amount?: unknown
}

type TooltipPoint = {
  dataIndex?: number
}

function finiteNumber(value: unknown): number | null {
  const number = Number(value)
  return value === null || value === undefined || !Number.isFinite(number) ? null : number
}

function tradeDateLabel(value: unknown): string {
  const dateText = String(value ?? '').slice(0, 10)
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateText)
  if (!match) return '交易日期未知'
  const parsed = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return `${dateText} · ${weekdays[parsed.getDay()]}`
}

function signedPrice(value: number | null): string {
  if (value === null) return '—'
  return `${value > 0 ? '+' : ''}${fmtNum(value, 2)}`
}

function tooltipMetric(label: string, value: string): string {
  return `<div style="display:flex;align-items:baseline;justify-content:space-between;gap:12px">
    <span style="color:${CHART_COLORS.text3};font-size:11px">${label}</span>
    <span style="color:#eef2fa;font-size:12px;font-weight:600;font-variant-numeric:tabular-nums">${value}</span>
  </div>`
}

function formatKlineTooltip(
  params: TooltipPoint | TooltipPoint[],
  sourceBars: KlineBar[],
  averages: Record<string, (number | null)[]>,
): string {
  const points = Array.isArray(params) ? params : [params]
  const dataIndex = points.find((point) => Number.isInteger(point?.dataIndex))?.dataIndex
  if (dataIndex === undefined) return ''

  const bar = sourceBars[dataIndex]
  if (!bar) return ''
  const open = finiteNumber(bar.open)
  const close = finiteNumber(bar.close)
  const low = finiteNumber(bar.low)
  const high = finiteNumber(bar.high)
  const previousClose = dataIndex > 0 ? finiteNumber(sourceBars[dataIndex - 1]?.close) : null
  const change = close !== null && previousClose !== null ? close - previousClose : null
  const changePct = change !== null && previousClose ? (change / previousClose) * 100 : null
  const amplitude = high !== null && low !== null && previousClose ? ((high - low) / previousClose) * 100 : null
  const trendColor = change === null || change === 0 ? CHART_COLORS.text2 : change > 0 ? CHART_COLORS.up : CHART_COLORS.down
  const changeLabel = change === null ? '首个交易日' : `${signedPrice(change)} · ${fmtPct(changePct)}`
  const volume = finiteNumber(bar.volume)
  const amount = finiteNumber(bar.amount)

  return `<div style="min-width:304px;padding:12px 14px;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif">
    <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding-bottom:10px;border-bottom:1px solid ${CHART_COLORS.line1}">
      <div>
        <div style="color:#eef2fa;font-size:13px;font-weight:650;letter-spacing:.01em">${tradeDateLabel(bar.date)}</div>
        <div style="margin-top:2px;color:${CHART_COLORS.text3};font-size:10px">日 K 线</div>
      </div>
      <div style="color:${trendColor};font-size:12px;font-weight:650;font-variant-numeric:tabular-nums;white-space:nowrap">${changeLabel}</div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:22px;row-gap:7px;padding:10px 0">
      ${tooltipMetric('开盘', fmtNum(open, 2))}
      ${tooltipMetric('最高', fmtNum(high, 2))}
      ${tooltipMetric('收盘', fmtNum(close, 2))}
      ${tooltipMetric('最低', fmtNum(low, 2))}
      ${tooltipMetric('前收', fmtNum(previousClose, 2))}
      ${tooltipMetric('振幅', amplitude === null ? '—' : fmtPct(amplitude))}
      ${tooltipMetric('成交量', volume === null ? '—' : `${fmtAmount(volume)}股`)}
      ${tooltipMetric('成交额', amount === null ? '—' : fmtAmount(amount))}
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;padding-top:10px;border-top:1px solid ${CHART_COLORS.line1}">
      <div style="color:${CHART_COLORS.warn};font-size:10px">MA5 <span style="display:block;margin-top:2px;color:#eef2fa;font-size:11px;font-weight:600;font-variant-numeric:tabular-nums">${fmtNum(averages.MA5?.[dataIndex], 2)}</span></div>
      <div style="color:${CHART_COLORS.cyan};font-size:10px">MA20 <span style="display:block;margin-top:2px;color:#eef2fa;font-size:11px;font-weight:600;font-variant-numeric:tabular-nums">${fmtNum(averages.MA20?.[dataIndex], 2)}</span></div>
      <div style="color:${CHART_COLORS.purple};font-size:10px">MA60 <span style="display:block;margin-top:2px;color:#eef2fa;font-size:11px;font-weight:600;font-variant-numeric:tabular-nums">${fmtNum(averages.MA60?.[dataIndex], 2)}</span></div>
    </div>
  </div>`
}

function movingAverage(values: number[], window: number): (number | null)[] {
  return values.map((_, index) => {
    if (index < window - 1) return null
    const slice = values.slice(index - window + 1, index + 1)
    return slice.reduce((sum, value) => sum + value, 0) / window
  })
}

const klineOption = computed(() => {
  if (!bars.value.length) return {}
  const dates = bars.value.map((bar) => bar.date)
  const ohlc = bars.value.map((bar) => [bar.open, bar.close, bar.low, bar.high])
  const closes = bars.value.map((bar) => Number(bar.close))
  const averages = {
    MA5: movingAverage(closes, 5),
    MA20: movingAverage(closes, 20),
    MA60: movingAverage(closes, 60),
  }
  const volumes = bars.value.map((bar) => ({
    value: bar.volume,
    itemStyle: {
      color:
        Number(bar.close) >= Number(bar.open) ? 'rgba(52,211,153,.45)' : 'rgba(248,113,113,.45)',
    },
  }))
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle,
      confine: true,
      padding: 0,
      axisPointer: {
        type: 'cross',
        lineStyle: { color: 'rgba(148,163,198,.55)', type: 'dashed' },
        crossStyle: { color: 'rgba(148,163,198,.55)', type: 'dashed' },
        label: { show: false },
      },
      extraCssText: 'border-radius:10px;box-shadow:0 16px 44px rgba(0,0,0,.52);overflow:hidden;',
      formatter: (params: TooltipPoint | TooltipPoint[]) =>
        formatKlineTooltip(params, bars.value, averages),
    },
    legend: {
      data: ['MA5', 'MA20', 'MA60'],
      textStyle: { color: CHART_COLORS.text3, fontSize: 10 },
      top: 0,
      itemWidth: 14,
      itemHeight: 2,
    },
    grid: [
      { left: 48, right: 10, top: 24, height: '60%' },
      { left: 48, right: 10, top: '78%', height: '15%' },
    ],
    xAxis: [
      {
        type: 'category',
        data: dates,
        axisLabel: { color: CHART_COLORS.text3, fontSize: 9.5, fontFamily: "ui-monospace,'SF Mono',Menlo,monospace" },
        axisLine: { lineStyle: { color: CHART_COLORS.line2 } },
        axisTick: { show: false },
      },
      { type: 'category', gridIndex: 1, data: dates, show: false },
    ],
    yAxis: [
      {
        scale: true,
        splitLine: { lineStyle: { color: CHART_COLORS.line1 } },
        axisLabel: { color: CHART_COLORS.text3, fontSize: 9.5, fontFamily: "ui-monospace,'SF Mono',Menlo,monospace" },
      },
      { gridIndex: 1, show: false },
    ],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 30, end: 100 }],
    series: [
      {
        name: 'K',
        type: 'candlestick',
        data: ohlc,
        itemStyle: {
          color: CHART_COLORS.up,
          color0: CHART_COLORS.down,
          borderColor: CHART_COLORS.up,
          borderColor0: CHART_COLORS.down,
        },
      },
      { name: 'MA5', type: 'line', data: averages.MA5, symbol: 'none', lineStyle: { width: 1, color: CHART_COLORS.warn } },
      { name: 'MA20', type: 'line', data: averages.MA20, symbol: 'none', lineStyle: { width: 1, color: CHART_COLORS.cyan } },
      { name: 'MA60', type: 'line', data: averages.MA60, symbol: 'none', lineStyle: { width: 1, color: CHART_COLORS.purple } },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes },
    ],
  }
})

async function load() {
  loading.value = true
  error.value = ''
  overview.value = null
  try {
    const [ov, kline] = await Promise.all([
      api.stockOverview(symbol.value),
      api.stockBars(symbol.value, 160),
    ])
    overview.value = ov
    bars.value = kline.bars || []
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  } finally {
    loading.value = false
  }
}

async function syncCninfo() {
  syncing.value = true
  try {
    await api.syncDisclosures(symbol.value)
    overview.value = await api.stockOverview(symbol.value)
  } catch (exc: any) {
    error.value = String(exc.message || exc)
  } finally {
    syncing.value = false
  }
}

onMounted(load)
watch(symbol, load)
</script>

<template>
  <div>
    <div class="page-head">
      <h1>个股分析</h1>
      <span class="sub mono">{{ symbol }}</span>
      <span class="sub">概率预测 + 公告事件 + 技术特征</span>
    </div>

    <div v-if="error" class="banner error" style="margin-bottom: 12px">{{ error }}</div>

    <div v-if="loading && !overview" class="grid" style="grid-template-columns: 1fr 300px">
      <div class="grid">
        <div class="skeleton" style="height: 84px" />
        <div class="grid" style="grid-template-columns: repeat(3, 1fr)">
          <div v-for="n in 3" :key="n" class="skeleton" style="height: 80px" />
        </div>
        <div class="skeleton" style="height: 360px" />
      </div>
      <div class="grid"><div class="skeleton" style="height: 300px" /></div>
    </div>

    <div v-if="overview" class="grid" style="grid-template-columns: minmax(0, 1fr) 300px; align-items: start">
      <div class="grid">
        <!-- 行情头 -->
        <div class="panel" style="display: flex; align-items: center; gap: 20px; flex-wrap: wrap">
          <div>
            <div style="display: flex; align-items: baseline; gap: 8px">
              <span style="font-size: 17px; font-weight: 700">{{ overview.security?.name || symbol }}</span>
              <span class="dim mono xs">{{ symbol }}</span>
              <span class="badge gray" v-if="overview.security?.board">{{ overview.security.board }}</span>
            </div>
            <div style="display: flex; align-items: baseline; gap: 10px; margin-top: 2px">
              <span class="num" style="font-size: 26px; font-weight: 650" :class="pctClass(overview.quote?.change_pct)">
                {{ fmtNum(overview.quote?.last) }}
              </span>
              <span class="num" style="font-size: 13px" :class="pctClass(overview.quote?.change_pct)">
                {{ fmtPct(overview.quote?.change_pct) }}
              </span>
              <span class="xs dim">成交 {{ fmtAmount(overview.quote?.amount) }}</span>
            </div>
          </div>
          <div style="margin-left: auto; display: flex; align-items: center; gap: 14px">
            <div style="text-align: right">
              <div class="xs dim">AI 信号</div>
              <span class="badge" :class="actionMeta(alert?.action).cls" style="font-size: 12.5px; margin-top: 3px">
                {{ actionMeta(alert?.action).label }}
              </span>
            </div>
            <ConfRing :value="alert?.confidence" :size="46" />
          </div>
        </div>

        <!-- 概率卡 -->
        <div class="grid" style="grid-template-columns: repeat(3, 1fr)">
          <div v-for="card in HORIZON_CARDS" :key="card.key" class="stat-card">
            <div class="label">{{ card.label }}上涨概率</div>
            <div class="value" :class="horizons[card.key]?.p_up >= 0.5 ? 'up' : 'down'">
              {{ horizons[card.key] ? (horizons[card.key].p_up * 100).toFixed(0) + '%' : '—' }}
            </div>
            <div class="delta num">
              期望 {{ horizons[card.key] ? fmtPct(horizons[card.key].expected_return, 2, false) : '—' }}
              · [{{ horizons[card.key] ? fmtPct(horizons[card.key].q10, 1, false) : '—' }},
              {{ horizons[card.key] ? fmtPct(horizons[card.key].q90, 1, false) : '—' }}]
            </div>
          </div>
        </div>

        <!-- K线 -->
        <div class="panel">
          <div class="panel-title">
            价格走势 · 日K
            <span class="extra mono">
              {{ bars[0]?.date }} → {{ bars[bars.length - 1]?.date }} · {{ forecast?.provider }} · {{ bars.length }} 个交易日
            </span>
          </div>
          <EChart v-if="bars.length" :option="klineOption" height="360px" />
          <div v-else class="empty-hint">无K线数据</div>
        </div>

        <!-- 公告 -->
        <div class="panel">
          <div class="panel-title">
            事件日历 · 巨潮公告
            <button class="btn ghost xs" style="padding: 3px 8px" :disabled="syncing" @click="syncCninfo">
              <RefreshCw :size="11" :class="{ spin: syncing }" /> {{ syncing ? '同步中' : '同步巨潮' }}
            </button>
          </div>
          <ul class="timeline" v-if="overview.disclosures?.length">
            <li v-for="item in overview.disclosures" :key="item.id">
              <div class="xs dim mono">{{ fmtDate(item.published_at) }}</div>
              <a :href="item.url" target="_blank" rel="noopener" style="font-size: 12.5px">{{ item.title }}</a>
            </li>
          </ul>
          <div v-else class="empty-hint">暂无公告缓存，点击「同步巨潮」拉取</div>
        </div>
      </div>

      <!-- 右栏 -->
      <div class="grid">
        <div class="panel">
          <div class="panel-title">AI 提醒解读</div>
          <div style="display: grid; gap: 7px">
            <div v-for="(reason, index) in alert?.reasons || []" :key="index" class="small muted" style="display: flex; gap: 7px">
              <span style="color: var(--accent-hi); flex: none">›</span>
              <span>{{ reason }}</span>
            </div>
          </div>
          <div style="border-top: 1px solid var(--line-1); padding-top: 8px; margin-top: 10px">
            <div class="xs" style="color: var(--warn); display: inline-flex; align-items: center; gap: 4px">
              <AlertTriangle :size="11" /> 失效条件
            </div>
            <div class="xs muted" style="margin-top: 3px">{{ alert?.invalidation }}</div>
          </div>
          <div class="xs dim mono" style="margin-top: 8px">
            {{ alert?.model_version }} · {{ fmtDate(alert?.as_of) }}
          </div>
        </div>

        <div class="panel">
          <div class="panel-title">技术特征</div>
          <div v-for="(label, key) in FEATURE_LABELS" :key="key" style="margin-bottom: 8px">
            <div class="kv" style="padding: 0 0 3px">
              <span class="k">{{ label }}</span>
              <span class="num xs" :class="pctClass(features[key])">
                {{ String(key).includes('ratio') || String(key).includes('position') ? fmtNum(features[key], 2) : fmtPct(features[key], 2, false) }}
              </span>
            </div>
            <div class="score-bar">
              <i :style="{ width: Math.min(100, Math.abs(Number(features[key] || 0)) * 220) + '%' }" />
            </div>
          </div>
        </div>

        <div class="panel" v-if="overview.security">
          <div class="panel-title">公司信息 <span class="extra">巨潮 WebAPI</span></div>
          <div class="kv"><span class="k">上市板块</span><span>{{ overview.security.board || '—' }}</span></div>
          <div class="kv"><span class="k">上市日期</span><span class="num">{{ overview.security.listed_date || '—' }}</span></div>
          <div class="kv"><span class="k">上市状态</span><span>{{ overview.security.status || '—' }}</span></div>
        </div>

        <div class="panel" v-if="forecast?.warnings?.length">
          <div class="panel-title">数据警示</div>
          <div class="xs dim" style="display: grid; gap: 5px; line-height: 1.6">
            <div v-for="(warning, index) in forecast.warnings" :key="index">{{ warning }}</div>
          </div>
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
