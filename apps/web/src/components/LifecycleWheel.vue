<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECElementEvent, TooltipComponentFormatterCallbackParams } from 'echarts'
import { CHART_COLORS, tooltipStyle } from '../chartTheme'

type LifecycleStage = {
  key: string
  label: string
  sectors: string[]
}

const props = withDefaults(
  defineProps<{
    stages: LifecycleStage[]
    height?: string
  }>(),
  { height: '226px' },
)

const STAGE_COLORS = [
  CHART_COLORS.accent,
  CHART_COLORS.cyan,
  CHART_COLORS.up,
  CHART_COLORS.warn,
  CHART_COLORS.purple,
]

const host = ref<HTMLDivElement | null>(null)
const hoveredKey = ref<string | null>(null)
const selectedKey = ref<string | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const normalized = computed(() => {
  if (!props.stages.length) return []
  const stages = props.stages.map((stage) => ({
    key: stage.key.trim(),
    label: stage.label.trim(),
    sectors: stage.sectors.map((sector) => sector.trim()).filter(Boolean),
  }))
  if (stages.some((stage) => !stage.key || !stage.label)) return []
  return stages
})

const dominantStage = computed(() => {
  const stages = normalized.value
  if (!stages.length) return null
  const dominant = stages.reduce((best, stage) =>
    stage.sectors.length > best.sectors.length ? stage : best,
  )
  return dominant.sectors.length ? dominant : null
})

const activeStage = computed(() => {
  const key = hoveredKey.value ?? selectedKey.value
  return normalized.value.find((stage) => stage.key === key) ?? dominantStage.value
})

const ariaLabel = computed(() => {
  if (!normalized.value.length) return '暂无板块生命周期数据'
  const dominant = dominantStage.value?.label ?? '暂无主导阶段'
  const counts = normalized.value
    .map((stage) => `${stage.label}${stage.sectors.length}个板块`)
    .join('，')
  return `板块生命周期轮盘，当前主导阶段：${dominant}。${counts}`
})

function stageColor(index: number): string {
  return STAGE_COLORS[index % STAGE_COLORS.length] ?? CHART_COLORS.accent
}

function disposeChart() {
  resizeObserver?.disconnect()
  resizeObserver = null
  chart?.dispose()
  chart = null
}

function setHoveredFromChart(event: ECElementEvent) {
  if (event.componentType !== 'series' || !Number.isInteger(event.dataIndex)) return
  hoveredKey.value = normalized.value[event.dataIndex]?.key ?? null
}

function clearChartHover() {
  hoveredKey.value = null
}

function render() {
  if (!host.value || !normalized.value.length) return
  if (!chart) {
    chart = echarts.init(host.value, undefined, { renderer: 'canvas' })
    chart.on('mouseover', setHoveredFromChart)
    chart.on('mouseout', clearChartHover)
    resizeObserver = new ResizeObserver(() => chart?.resize())
    resizeObserver.observe(host.value)
  }

  chart.setOption(
    {
      animation: false,
      tooltip: {
        ...tooltipStyle,
        trigger: 'item',
        confine: true,
        renderMode: 'richText',
        formatter: (params: TooltipComponentFormatterCallbackParams) => {
          const point = Array.isArray(params) ? params[0] : params
          const stage = Number.isInteger(point?.dataIndex)
            ? normalized.value[point.dataIndex]
            : undefined
          return stage ? `${stage.label}  ${stage.sectors.length} 个板块` : ''
        },
      },
      series: [
        {
          type: 'pie',
          radius: ['54%', '78%'],
          center: ['50%', '49%'],
          startAngle: 90,
          clockwise: true,
          avoidLabelOverlap: true,
          itemStyle: {
            borderColor: '#0a101e',
            borderWidth: 2,
            borderRadius: 3,
          },
          label: {
            show: true,
            color: CHART_COLORS.text2,
            fontSize: 10,
            formatter: '{b}',
          },
          labelLine: {
            length: 8,
            length2: 5,
            lineStyle: { color: CHART_COLORS.line2 },
          },
          emphasis: {
            scale: false,
            itemStyle: { borderColor: CHART_COLORS.text2 },
          },
          data: normalized.value.map((stage, index) => ({
            name: stage.label,
            value: 1,
            itemStyle: { color: stageColor(index) },
          })),
        },
      ],
    } satisfies echarts.EChartsOption,
    true,
  )
}

onMounted(() => render())

watch(
  () => props.stages,
  async () => {
    hoveredKey.value = null
    if (selectedKey.value && !normalized.value.some((stage) => stage.key === selectedKey.value)) {
      selectedKey.value = null
    }
    if (!normalized.value.length) disposeChart()
    await nextTick()
    render()
  },
  { deep: true },
)

onBeforeUnmount(() => disposeChart())
</script>

<template>
  <div class="lifecycle-wheel">
    <div v-if="!normalized.length" class="wheel-empty" :style="{ minHeight: props.height }" role="status">
      暂无板块生命周期数据
    </div>
    <template v-else>
      <div class="wheel-visual" role="img" :aria-label="ariaLabel">
        <div ref="host" class="wheel-canvas" :style="{ height: props.height }" aria-hidden="true" />
        <div class="wheel-center" aria-hidden="true">
          <span>{{ activeStage?.label ?? '暂无主导阶段' }}</span>
          <strong class="num">{{ activeStage?.sectors.length ?? 0 }}</strong>
          <small>个板块</small>
        </div>
      </div>

      <div class="stage-tabs" aria-label="选择生命周期阶段">
        <button
          v-for="(stage, index) in normalized"
          :key="stage.key"
          type="button"
          class="stage-tab"
          :class="{ active: activeStage?.key === stage.key }"
          :aria-pressed="selectedKey === stage.key"
          @mouseenter="hoveredKey = stage.key"
          @mouseleave="hoveredKey = null"
          @focus="hoveredKey = stage.key"
          @blur="hoveredKey = null"
          @click="selectedKey = selectedKey === stage.key ? null : stage.key"
        >
          <i :style="{ backgroundColor: stageColor(index) }" />
          {{ stage.label }}
        </button>
      </div>

      <div class="sector-region" aria-live="polite">
        <div class="sector-heading">
          <span>{{ activeStage?.label ?? '暂无主导阶段' }}</span>
          <span class="num">{{ activeStage?.sectors.length ?? 0 }}</span>
        </div>
        <div v-if="activeStage?.sectors.length" class="sector-list">
          <span v-for="sector in activeStage.sectors" :key="sector" class="sector-chip">{{ sector }}</span>
        </div>
        <div v-else class="sector-empty">该阶段暂无归属板块</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.lifecycle-wheel {
  width: 100%;
}

.wheel-visual {
  position: relative;
  width: 100%;
}

.wheel-canvas {
  width: 100%;
}

.wheel-center {
  position: absolute;
  left: 50%;
  top: 49%;
  width: 92px;
  transform: translate(-50%, -50%);
  display: grid;
  justify-items: center;
  pointer-events: none;
  color: var(--text-2);
  font-size: 11px;
  line-height: 1.25;
}

.wheel-center strong {
  margin-top: 2px;
  color: var(--text-1);
  font-size: 19px;
  font-weight: 700;
}

.wheel-center small {
  color: var(--text-2);
  font-size: 10.5px;
}

.stage-tabs {
  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: var(--s1) var(--s2);
  margin-top: -2px;
}

.stage-tab {
  border: 0;
  border-radius: var(--r-sm);
  padding: 4px 6px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: transparent;
  color: var(--text-2);
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
  transition: color var(--t-fast), background var(--t-fast);
}

.stage-tab:hover,
.stage-tab.active {
  background: rgba(148, 163, 198, 0.08);
  color: var(--text-1);
}

.stage-tab i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.sector-region {
  margin-top: var(--s3);
  padding-top: var(--s3);
  border-top: 1px solid var(--line-1);
}

.sector-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  color: var(--text-2);
  font-size: 11px;
}

.sector-heading .num {
  color: var(--text-2);
}

.sector-list {
  max-height: 78px;
  margin-top: var(--s2);
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  overflow: auto;
}

.sector-chip {
  border-radius: var(--r-sm);
  padding: 3px 7px;
  background: rgba(59, 130, 246, 0.1);
  color: var(--accent-hi);
  font-size: 10px;
  line-height: 1.35;
}

.sector-empty,
.wheel-empty {
  display: grid;
  place-items: center;
  color: var(--text-2);
  font-size: 12px;
  text-align: center;
}

.sector-empty {
  min-height: 34px;
  margin-top: var(--s2);
}

@media (prefers-reduced-motion: reduce) {
  .stage-tab {
    transition: none;
  }
}
</style>
