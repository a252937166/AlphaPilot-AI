<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ value: number | null | undefined; size?: number }>()

const pct = computed(() => {
  const num = Number(props.value)
  if (props.value === null || props.value === undefined || Number.isNaN(num)) return 0
  return Math.round(Math.max(0, Math.min(1, num)) * 100)
})

const color = computed(() => {
  if (pct.value >= 70) return '#34d399'
  if (pct.value >= 55) return '#60a5fa'
  if (pct.value >= 40) return '#fbbf24'
  return '#f87171'
})

const ringStyle = computed(() => ({
  width: `${props.size || 34}px`,
  height: `${props.size || 34}px`,
  background: `conic-gradient(${color.value} ${pct.value * 3.6}deg, rgba(148,163,198,0.14) 0deg)`,
}))
</script>

<template>
  <div class="conf-ring" :style="ringStyle" role="img" :aria-label="`置信度 ${pct}%`">
    <span class="inner">{{ pct }}</span>
  </div>
</template>

<style scoped>
.conf-ring {
  border-radius: 50%;
  display: grid;
  place-items: center;
  flex: none;
}
.inner {
  width: calc(100% - 6px);
  height: calc(100% - 6px);
  border-radius: 50%;
  background: var(--surface-2);
  display: grid;
  place-items: center;
  font-size: 10px;
  font-weight: 700;
  color: var(--text-1);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}
</style>
