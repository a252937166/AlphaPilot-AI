<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import { api } from '../api'
import { fmtNum, fmtPct, pctClass, regimeMeta } from '../format'
import EChart from '../components/EChart.vue'
import Sparkline from '../components/Sparkline.vue'
import { CHART_COLORS, categoryAxis, glowLine, tooltipStyle, valueAxis } from '../chartTheme'

const loading = ref(true)
const error = ref('')
const regime = ref<any>(null)
const indices = ref<any>(null)
const breadth = ref<any>(null)

const quotes = computed(() => indices.value?.quotes ?? [])
const series = computed(() => indices.value?.series ?? {})

const INDEX_COLORS: Record<string, string> = {
  'SH.000001': CHART_COLORS.cyan,
  'SZ.399001': CHART_COLORS.accent,
  'SZ.399006': CHART_COLORS.purple,
  'SH.000300': CHART_COLORS.warn,
  'SH.000905': CHART_COLORS.up,
}

function sparkData(symbol: string): number[] {
  return (series.value[symbol] || []).map((point: any) => Number(point.close))
}

const trendOption = computed(() => {
  const symbols = Object.keys(series.value)
  if (!symbols.length) return {}
  const nameOf: Record<string, string> = {}
  for (const entry of indices.value?.symbols || []) nameOf[entry.symbol] = entry.name
  const base = series.value[symbols[0]] || []
  const dates = base.map((point: any) => point.date)
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle,
      valueFormatter: (value: number) => `${Number(value).toFixed(2)}%`,
    },
    legend: {
      textStyle: { color: CHART_COLORS.text3, fontSize: 10 },
      top: 0,
      itemWidth: 14,
      itemHeight: 2,
    },
    grid: { left: 40, right: 10, top: 26, bottom: 22 },
    xAxis: categoryAxis(dates),
    yAxis: valueAxis({ axisLabel: { formatter: '{value}%', color: CHART_COLORS.text3, fontSize: 10 } }),
    series: symbols.map((symbol) => {
      const points = series.value[symbol] || []
      const first = Number(points[0]?.close || 1)
      return {
        name: nameOf[symbol] || symbol,
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

const breadthGauge = computed(() => {
  const advancers = breadth.value?.advancers ?? 0
  const decliners = breadth.value?.decliners ?? 0
  const total = advancers + decliners || 1
  const ratio = Math.round((advancers / total) * 100)
  return {
    animation: false,
    series: [
      {
        type: 'gauge',
        startAngle: 200,
        endAngle: -20,
        min: 0,
        max: 100,
        progress: {
          show: true,
          width: 8,
          itemStyle: { color: ratio >= 50 ? CHART_COLORS.up : CHART_COLORS.down },
        },
        axisLine: { lineStyle: { width: 8, color: [[1, 'rgba(148,163,198,0.12)']] } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        pointer: { show: false },
        detail: {
          valueAnimation: false,
          formatter: '{value}%',
          color: '#eef2fa',
          fontSize: 20,
          fontFamily: "ui-monospace,'SF Mono',Menlo,monospace",
          offsetCenter: [0, 0],
        },
        data: [{ value: ratio }],
      },
    ],
  }
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [regimeResult, indicesResult] = await Promise.all([
      api.marketRegime().catch(() => null),
      api.marketIndices(60),
    ])
    regime.value = regimeResult
    indices.value = indicesResult
    breadth.value = await api.marketBreadth().catch(() => null)
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
      <h1>大盘监控</h1>
      <span class="sub">指数 · 状态机 · 样本宽度</span>
      <div style="margin-left: auto">
        <button class="btn ghost" :disabled="loading" @click="load">
          <RefreshCw :size="12" :class="{ spin: loading }" /> 刷新
        </button>
      </div>
    </div>

    <div v-if="error" class="banner error" style="margin-bottom: 12px">{{ error }}</div>

    <div v-if="loading && !indices" class="grid" style="grid-template-columns: 1fr 300px">
      <div class="grid">
        <div class="skeleton" style="height: 64px" />
        <div class="grid" style="grid-template-columns: repeat(5, 1fr)">
          <div v-for="n in 5" :key="n" class="skeleton" style="height: 96px" />
        </div>
        <div class="skeleton" style="height: 300px" />
      </div>
      <div class="grid"><div class="skeleton" style="height: 280px" /></div>
    </div>

    <div v-else class="grid" style="grid-template-columns: minmax(0, 1fr) 300px; align-items: start">
      <div class="grid">
        <!-- 状态 -->
        <div class="panel" style="display: flex; align-items: center; gap: 16px" v-if="regime">
          <div>
            <div class="xs dim">市场状态 · 基准 SH.000001</div>
            <div style="font-size: 21px; font-weight: 700; margin-top: 1px" :class="regimeMeta(regime.regime).cls === 'red' ? 'down' : regimeMeta(regime.regime).cls === 'green' ? 'up' : ''">
              {{ regimeMeta(regime.regime).label }}
            </div>
          </div>
          <span class="badge blue num">置信 {{ (regime.confidence * 100).toFixed(0) }}%</span>
          <div class="xs dim" style="flex: 1">{{ (regime.explanation || [])[0] }}</div>
        </div>

        <!-- 指数卡片 -->
        <div class="grid" style="grid-template-columns: repeat(5, 1fr)" v-if="quotes.length">
          <div v-for="quote in quotes" :key="quote.symbol" class="stat-card" style="padding: 10px 12px">
            <div class="label">{{ quote.name }}</div>
            <div class="value" style="font-size: 16px">{{ fmtNum(quote.last) }}</div>
            <div class="xs num" :class="pctClass(quote.change_pct)">{{ fmtPct(quote.change_pct) }}</div>
            <Sparkline :data="sparkData(quote.symbol)" :color="INDEX_COLORS[quote.symbol]" height="30px" />
          </div>
        </div>
        <div v-else class="panel empty-hint">指数实时快照需要 Futu OpenD 行情</div>

        <!-- 走势对比 -->
        <div class="panel">
          <div class="panel-title">指数走势对比 <span class="extra">近 60 交易日 · 归一化涨跌幅</span></div>
          <EChart v-if="Object.keys(series).length" :option="trendOption" height="290px" />
          <div v-else class="empty-hint">暂无历史数据（BaoStock/Futu 均不可用时降级）</div>
        </div>
      </div>

      <!-- 右栏 -->
      <div class="grid">
        <div class="panel">
          <div class="panel-title">市场宽度 <span class="extra">板块抽样</span></div>
          <template v-if="breadth">
            <EChart :option="breadthGauge" height="130px" />
            <div style="display: flex; justify-content: space-around; text-align: center">
              <div>
                <div class="num up" style="font-size: 17px; font-weight: 650">{{ breadth.advancers }}</div>
                <div class="xs dim">上涨</div>
              </div>
              <div>
                <div class="num" style="font-size: 17px; font-weight: 650">{{ breadth.unchanged }}</div>
                <div class="xs dim">平盘</div>
              </div>
              <div>
                <div class="num down" style="font-size: 17px; font-weight: 650">{{ breadth.decliners }}</div>
                <div class="xs dim">下跌</div>
              </div>
            </div>
            <div class="xs dim" style="margin-top: 10px">{{ breadth.note }}</div>
          </template>
          <div v-else class="empty-hint">需要 Futu 行情</div>
        </div>

        <div class="panel" v-if="regime">
          <div class="panel-title">状态机指标</div>
          <div class="kv" v-for="(value, key) in regime.features" :key="key">
            <span class="k mono xs">{{ key }}</span>
            <span class="num xs" :class="pctClass(value)">{{ fmtNum(value, 4) }}</span>
          </div>
        </div>

        <div class="panel">
          <div class="panel-title">观察提示</div>
          <div class="xs dim" style="line-height: 1.8">
            状态机为规则分类器，输出带置信度。宽度基于板块抽样股票池，非全市场统计。
            指数历史优先 BaoStock，实时快照走富途。
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
