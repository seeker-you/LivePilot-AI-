<script setup lang="ts">
import { ref, watch } from 'vue'
import type { Component } from 'vue'

const props = defineProps<{
  label: string
  value: string | number
  hint?: string
  icon: Component
  tone?: 'default' | 'positive' | 'negative' | 'warning'
}>()

const display = ref<string | number>(props.value)

watch(
  () => props.value,
  (next) => {
    const from = typeof display.value === 'number' ? display.value : 0
    const to = typeof next === 'number' ? next : 0
    if (typeof next !== 'number' || from === to) {
      display.value = next
      return
    }
    const start = performance.now()
    const duration = 350
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      display.value = Math.round(from + (to - from) * eased)
      if (p < 1) requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  },
)
</script>

<template>
  <div class="metric card" :class="tone">
    <div class="metric-head">
      <span class="metric-label">{{ label }}</span>
      <component :is="icon" :size="17" class="metric-icon" />
    </div>
    <div class="metric-value num">{{ display }}</div>
    <div v-if="hint" class="metric-hint">{{ hint }}</div>
  </div>
</template>

<style scoped>
.metric {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.metric-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.metric-label {
  color: var(--text-muted);
  font-size: 12.5px;
}

.metric-icon {
  color: var(--text-faint);
}

.metric-value {
  font-size: 24px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.metric-hint {
  font-size: 12px;
  color: var(--text-faint);
}

.metric.positive .metric-icon,
.metric.positive .metric-value {
  color: var(--positive);
}

.metric.negative .metric-icon,
.metric.negative .metric-value {
  color: var(--negative);
}

.metric.warning .metric-icon,
.metric.warning .metric-value {
  color: var(--warning);
}
</style>
