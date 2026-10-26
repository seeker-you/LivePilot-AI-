<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    series: number[]
    color?: string
    height?: number
  }>(),
  { color: '#2dd4bf', height: 120 },
)

const el = ref<HTMLElement | null>(null)
const width = ref(0)
let observer: ResizeObserver | null = null

onMounted(() => {
  observer = new ResizeObserver((entries) => {
    width.value = entries[0].contentRect.width
  })
  if (el.value) observer.observe(el.value)
})

onBeforeUnmount(() => observer?.disconnect())

const max = computed(() => Math.max(...props.series, 1))
const last = computed(() => props.series[props.series.length - 1] ?? 0)

const coords = computed(() => {
  const n = props.series.length
  if (n < 2 || width.value === 0) return [] as { x: number; y: number }[]
  const h = props.height
  const pad = 4
  const step = (width.value - pad * 2) / (n - 1)
  return props.series.map((v, i) => ({
    x: pad + i * step,
    y: h - (v / max.value) * (h - pad * 2) - pad,
  }))
})

const linePath = computed(() => {
  if (!coords.value.length) return ''
  return (
    'M' +
    coords.value
      .map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`)
      .join(' L')
  )
})

const areaPath = computed(() => {
  if (!coords.value.length) return ''
  const first = coords.value[0]
  const lastPt = coords.value[coords.value.length - 1]
  return `${linePath.value} L ${lastPt.x.toFixed(1)} ${props.height} L ${first.x.toFixed(1)} ${props.height} Z`
})

const pulse = computed(() => {
  const c = coords.value
  return c.length ? c[c.length - 1] : { x: 0, y: 0 }
})
</script>

<template>
  <div ref="el" class="stream" :style="{ height: `${height}px` }">
    <svg :width="width" :height="height" class="stream-svg">
      <defs>
        <linearGradient :id="`grad-${color.replace('#', '')}`" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" :stop-color="color" stop-opacity="0.28" />
          <stop offset="100%" :stop-color="color" stop-opacity="0" />
        </linearGradient>
      </defs>
      <g class="grid-lines">
        <line v-for="i in 3" :key="i" x1="0" :y1="(height / 4) * i" :x2="width" :y2="(height / 4) * i" />
      </g>
      <path :d="areaPath" :fill="`url(#grad-${color.replace('#', '')})`" />
      <path :d="linePath" fill="none" :stroke="color" stroke-width="2" stroke-linejoin="round" />
      <circle
        :cx="pulse.x"
        :cy="pulse.y"
        r="3.5"
        :fill="color"
        class="pulse-dot"
      />
    </svg>
  </div>
</template>

<style scoped>
.stream {
  position: relative;
  width: 100%;
}

.stream-svg {
  display: block;
}

.grid-lines line {
  stroke: var(--border-soft);
  stroke-width: 1;
  stroke-dasharray: 3 4;
}

.pulse-dot {
  animation: dot-pulse 1.6s ease-in-out infinite;
}

@keyframes dot-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

@media (prefers-reduced-motion: reduce) {
  .pulse-dot {
    animation: none;
  }
}
</style>

