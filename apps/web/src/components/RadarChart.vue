<script setup lang="ts">
import { computed } from 'vue'
import { areaGradient, CHART_COLORS, tooltipStyle } from '../chartTheme'
import EChart from './EChart.vue'

type RadarIndicator = {
  name: string
  max: number
}

const props = withDefaults(
  defineProps<{
    indicators: RadarIndicator[]
    values: number[]
    height?: string
  }>(),
  { height: '248px' },
)

const normalized = computed(() => {
  if (!props.indicators.length || props.values.length !== props.indicators.length) return null

  const indicators = props.indicators.map((indicator) => ({
    name: indicator.name.trim(),
    max: Number(indicator.max),
  }))
  const values = props.values.map(Number)
  const valid = indicators.every(
    (indicator) => indicator.name.length > 0 && Number.isFinite(indicator.max) && indicator.max > 0,
  ) && values.every(
    (value, index) =>
      Number.isFinite(value) && value >= 0 && value <= (indicators[index]?.max ?? -1),
  )

  if (!valid) return null
  return {
    indicators,
    values,
  }
})

const emptyText = computed(() =>
  props.indicators.length || props.values.length
    ? '雷达评分数据不完整，暂无法绘制'
    : '暂无雷达评分数据',
)

const ariaLabel = computed(() => {
  const data = normalized.value
  if (!data) return emptyText.value
  return `综合评分雷达图：${data.indicators
    .map((indicator, index) => `${indicator.name} ${data.values[index]} / ${indicator.max}`)
    .join('，')}`
})

const option = computed<Record<string, unknown>>(() => {
  const data = normalized.value
  if (!data) return {}

  return {
    animation: false,
    tooltip: {
      trigger: 'item',
      ...tooltipStyle,
      confine: true,
    },
    radar: {
      center: ['50%', '50%'],
      radius: '68%',
      startAngle: 90,
      splitNumber: 4,
      indicator: data.indicators,
      axisName: {
        color: CHART_COLORS.text2,
        fontSize: 11,
        fontFamily: "-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif",
      },
      axisLine: { lineStyle: { color: CHART_COLORS.line2 } },
      splitLine: { lineStyle: { color: CHART_COLORS.line1 } },
      splitArea: { show: false },
    },
    series: [
      {
        name: '综合评分',
        type: 'radar',
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { color: CHART_COLORS.cyan, width: 1.8 },
        itemStyle: { color: CHART_COLORS.cyan },
        areaStyle: { color: areaGradient(CHART_COLORS.cyan, 0.34) },
        emphasis: { disabled: true },
        data: [{ name: '综合评分', value: data.values }],
      },
    ],
  }
})
</script>

<template>
  <div class="radar-chart" :style="{ minHeight: props.height }">
    <div v-if="!normalized" class="chart-empty" role="status">{{ emptyText }}</div>
    <div v-else class="chart-canvas" role="img" :aria-label="ariaLabel">
      <EChart :option="option" :height="props.height" aria-hidden="true" />
    </div>
  </div>
</template>

<style scoped>
.radar-chart,
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
