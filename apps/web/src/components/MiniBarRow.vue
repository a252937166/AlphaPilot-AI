<script setup lang="ts">
import { computed } from 'vue'

type MiniBarItem = {
  label: string
  value: number
  color: string
}

const props = defineProps<{ items: MiniBarItem[] }>()

const normalized = computed(() => {
  if (!props.items.length) return []
  const items = props.items.map((item) => ({
    label: item.label.trim(),
    value: Number(item.value),
    color: item.color.trim(),
  }))
  const valid = items.every(
    (item) =>
      item.label.length > 0 &&
      item.color.length > 0 &&
      Number.isFinite(item.value) &&
      item.value >= 0 &&
      item.value <= 10,
  )
  return valid ? items : []
})

const emptyText = computed(() =>
  props.items.length ? '评分细项数据不完整' : '暂无评分细项',
)

const ariaLabel = computed(() =>
  normalized.value.length
    ? `评分细项：${normalized.value.map((item) => `${item.label} ${item.value} / 10`).join('，')}`
    : emptyText.value,
)

function barHeight(value: number): string {
  return `${Math.max(0, Math.min(100, value * 10))}%`
}
</script>

<template>
  <div v-if="normalized.length" class="mini-bar-row" role="group" :aria-label="ariaLabel">
    <span
      v-for="item in normalized"
      :key="item.label"
      class="mini-bar"
      :title="`${item.label} ${item.value} / 10`"
      role="progressbar"
      :aria-label="item.label"
      :aria-valuenow="item.value"
      aria-valuemin="0"
      aria-valuemax="10"
    >
      <i :style="{ height: barHeight(item.value), backgroundColor: item.color }" />
    </span>
  </div>
  <span v-else class="mini-bar-empty" role="status">{{ emptyText }}</span>
</template>

<style scoped>
.mini-bar-row {
  height: 30px;
  display: inline-flex;
  align-items: flex-end;
  gap: 5px;
  padding: 2px 0;
}

.mini-bar {
  position: relative;
  width: 5px;
  height: 100%;
  overflow: hidden;
  border-radius: 2px;
  background: rgba(148, 163, 198, 0.12);
}

.mini-bar i {
  position: absolute;
  inset: auto 0 0;
  border-radius: inherit;
}

.mini-bar-empty {
  color: var(--text-2);
  font-size: 11px;
  white-space: nowrap;
}
</style>
