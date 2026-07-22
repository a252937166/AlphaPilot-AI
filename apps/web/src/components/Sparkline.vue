<script setup lang="ts">
import { computed } from 'vue'
import EChart from './EChart.vue'

const props = defineProps<{
  data: number[]
  color?: string
  height?: string
  ariaLabel?: string
}>()

const option = computed(() => ({
  animation: false,
  grid: { left: 0, right: 0, top: 4, bottom: 0 },
  xAxis: { type: 'category', show: false, data: props.data.map((_, index) => index) },
  yAxis: { type: 'value', show: false, min: 'dataMin', max: 'dataMax' },
  series: [
    {
      type: 'line',
      data: props.data,
      symbol: 'none',
      lineStyle: { width: 1.6, color: props.color || '#22d3ee' },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: (props.color || '#22d3ee') + '55' },
            { offset: 1, color: 'rgba(0,0,0,0)' },
          ],
        },
      },
    },
  ],
}))
</script>

<template>
  <EChart
    :option="option"
    :height="props.height || '42px'"
    :aria-label="props.ariaLabel"
  />
</template>
