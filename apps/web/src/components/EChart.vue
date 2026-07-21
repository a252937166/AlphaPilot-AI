<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps<{ option: Record<string, unknown>; height?: string }>()
const host = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

function render() {
  if (!host.value) return
  if (!chart) chart = echarts.init(host.value, undefined, { renderer: 'canvas' })
  chart.setOption(props.option as echarts.EChartsOption, true)
}

onMounted(() => {
  render()
  resizeObserver = new ResizeObserver(() => chart?.resize())
  if (host.value) resizeObserver.observe(host.value)
})

watch(
  () => props.option,
  () => render(),
  { deep: true },
)

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="host" :style="{ width: '100%', height: props.height || '260px' }" />
</template>
