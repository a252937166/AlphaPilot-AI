<script setup lang="ts">
import { computed } from 'vue'
import EChart from './EChart.vue'

/** Mockup-style mini gauge: gradient arc, mono score in the center. */
const props = defineProps<{
  value: number | null | undefined // 0..1
  label?: string
  size?: string
}>()

const score = computed(() => Math.max(0, Math.min(1, Number(props.value) || 0)))

const option = computed(() => {
  const pct = score.value * 100
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
          formatter: (value: number) => (value / 10).toFixed(1),
          color: '#eef2fa',
          fontSize: 15,
          fontWeight: 700,
          fontFamily: "ui-monospace,'SF Mono',Menlo,monospace",
          offsetCenter: [0, 8],
        },
        data: [{ value: pct }],
      },
    ],
  }
})
</script>

<template>
  <div style="display: grid; place-items: center">
    <EChart :option="option" :height="props.size || '78px'" />
    <div v-if="props.label" class="xs dim" style="margin-top: -12px">{{ props.label }}</div>
  </div>
</template>
