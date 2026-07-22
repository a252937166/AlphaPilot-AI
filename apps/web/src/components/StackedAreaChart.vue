<script setup lang="ts">
import { computed } from 'vue'
import {
  areaGradient,
  categoryAxis,
  CHART_COLORS,
  tooltipStyle,
  valueAxis,
} from '../chartTheme'
import EChart from './EChart.vue'

type StackedSeries = {
  name: string
  color: string
  values: number[]
}

const props = withDefaults(
  defineProps<{
    dates: string[]
    series: StackedSeries[]
    height?: string
  }>(),
  { height: '270px' },
)

const normalized = computed(() => {
  if (!props.dates.length || !props.series.length) return null

  const dates = props.dates.map((date) => String(date).trim())
  const series = props.series.map((item) => ({
    name: item.name.trim(),
    color: item.color.trim(),
    values: item.values.map(Number),
  }))
  const valuesStayWithinWhole = dates.every((_, index) =>
    series.reduce((sum, item) => sum + (item.values[index] ?? Number.POSITIVE_INFINITY), 0) <= 1.001,
  )
  const valid = dates.every(Boolean) && valuesStayWithinWhole && series.every(
    (item) =>
      item.name.length > 0 &&
      item.color.length > 0 &&
      item.values.length === dates.length &&
      item.values.every((value) => Number.isFinite(value) && value >= 0 && value <= 1),
  )

  return valid ? { dates, series } : null
})

const emptyText = computed(() =>
  props.dates.length || props.series.length
    ? '风格概率数据不完整，暂无法绘制'
    : '暂无风格概率数据',
)

const ariaLabel = computed(() => {
  const data = normalized.value
  if (!data) return emptyText.value
  const range = data.dates.length > 1
    ? `${data.dates[0]} 至 ${data.dates[data.dates.length - 1]}`
    : data.dates[0]
  const latestIndex = data.dates.length - 1
  const latest = data.series
    .map((item) => `${item.name} ${(item.values[latestIndex] * 100).toFixed(1)}%`)
    .join('，')
  return `风格概率堆叠面积图，${range}，最新一期：${latest}`
})

const option = computed<Record<string, unknown>>(() => {
  const data = normalized.value
  if (!data) return {}

  return {
    animation: false,
    color: data.series.map((item) => item.color),
    tooltip: {
      trigger: 'axis',
      ...tooltipStyle,
      confine: true,
      valueFormatter: (value: unknown) => {
        const probability = Number(value)
        return Number.isFinite(probability) ? `${(probability * 100).toFixed(1)}%` : '—'
      },
    },
    legend: {
      bottom: 0,
      left: 'center',
      icon: 'roundRect',
      itemWidth: 12,
      itemHeight: 3,
      itemGap: 18,
      textStyle: { color: CHART_COLORS.text2, fontSize: 10 },
    },
    grid: { left: 44, right: 12, top: 12, bottom: 48, containLabel: false },
    xAxis: categoryAxis(data.dates, {
      boundaryGap: false,
      axisLabel: {
        color: CHART_COLORS.text2,
        fontSize: 10,
        fontFamily: "ui-monospace,'SF Mono',Menlo,monospace",
        hideOverlap: true,
      },
    }),
    yAxis: valueAxis({
      min: 0,
      max: 1,
      interval: 0.25,
      axisLabel: {
        color: CHART_COLORS.text2,
        fontSize: 10,
        fontFamily: "ui-monospace,'SF Mono',Menlo,monospace",
        formatter: (value: number) => `${Math.round(value * 100)}%`,
      },
    }),
    series: data.series.map((item) => ({
      name: item.name,
      type: 'line',
      stack: 'total',
      data: item.values,
      showSymbol: false,
      symbol: 'none',
      lineStyle: { color: item.color, width: 1.5 },
      areaStyle: { color: areaGradient(item.color, 0.42), opacity: 1 },
      emphasis: { focus: 'series' },
    })),
  }
})
</script>

<template>
  <div class="stacked-area-chart" :style="{ minHeight: props.height }">
    <div v-if="!normalized" class="chart-empty" role="status">{{ emptyText }}</div>
    <div v-else class="chart-canvas" role="img" :aria-label="ariaLabel">
      <EChart :option="option" :height="props.height" aria-hidden="true" />
    </div>
  </div>
</template>

<style scoped>
.stacked-area-chart,
.chart-canvas {
  width: 100%;
}

.chart-empty {
  min-height: inherit;
  display: grid;
  place-items: center;
  padding: var(--s4);
  color: var(--text-2);
  font-size: 12px;
  text-align: center;
}
</style>
