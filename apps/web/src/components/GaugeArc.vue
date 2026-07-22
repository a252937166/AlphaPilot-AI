<script setup lang="ts">
import { computed } from 'vue'
import EChart from './EChart.vue'

/** Mockup-style mini gauge: gradient arc, mono score in the center. */
const props = defineProps<{
  value: number | null | undefined // 0..1
  label?: string
  size?: string
  format?: 'score10' | 'score100' | 'percent'
  ariaLabel?: string
}>()

const score = computed(() => {
  if (props.value === null || props.value === undefined) return null
  const value = Number(props.value)
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null
})

const option = computed(() => {
  const pct = (score.value ?? 0) * 100
  return {
    animation: false,
    series: [
      {
        type: 'gauge',
        startAngle: 210,
        endAngle: -30,
        min: 0,
        max: 100,
        radius: '98%',
        center: ['50%', '58%'],
        progress: {
          show: true,
          width: 7,
          roundCap: true,
          itemStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 1,
              y2: 0,
              colorStops: [
                { offset: 0, color: '#22d3ee' },
                { offset: 1, color: pct >= 55 ? '#34d399' : '#3b82f6' },
              ],
            },
            shadowColor: 'rgba(34,211,238,0.5)',
            shadowBlur: 10,
          },
        },
        axisLine: {
          roundCap: true,
          lineStyle: { width: 7, color: [[1, 'rgba(148,163,198,0.13)']] },
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        pointer: { show: false },
        detail: {
          valueAnimation: false,
          formatter: (value: number) => {
            if (score.value === null) return '—'
            if (props.format === 'percent') return `${Math.round(value)}%`
            if (props.format === 'score100') return String(Math.round(value))
            return (value / 10).toFixed(1)
          },
          color: '#eef2fa',
          fontSize: props.format === 'score100' ? 32 : 15,
          fontWeight: 700,
          fontFamily: "ui-monospace,'SF Mono',Menlo,monospace",
          offsetCenter: [0, props.format === 'score100' ? 4 : 8],
        },
        data: [{ value: pct }],
      },
    ],
  }
})

const accessibleLabel = computed(() => {
  if (props.ariaLabel) return props.ariaLabel
  const prefix = props.label || '评分'
  if (score.value === null) return `${prefix}暂无数据`
  if (props.format === 'percent') return `${prefix}${Math.round(score.value * 100)}%`
  if (props.format === 'score100') return `${prefix}${Math.round(score.value * 100)}分`
  return `${prefix}${(score.value * 10).toFixed(1)}分`
})
</script>

<template>
  <div style="display: grid; place-items: center">
    <EChart :option="option" :height="props.size || '78px'" :aria-label="accessibleLabel" />
    <div v-if="props.label" class="xs dim" style="margin-top: -12px">{{ props.label }}</div>
  </div>
</template>
