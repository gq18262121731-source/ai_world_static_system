<script setup>
import { computed } from 'vue'

/**
 * 不依赖任何地图服务商的相对位置示意图。
 * 以本次外出的起点为圆心，按米换算成画布坐标绘制电子围栏与行走轨迹。
 */
const props = defineProps({
  origin: { type: Object, default: null },
  points: { type: Array, default: () => [] },
  radius: { type: Number, default: 500 },
  size: { type: Number, default: 320 },
})

const METERS_PER_DEG_LAT = 111320
const METERS_PER_DEG_LNG = 92000

const offsets = computed(() => {
  if (!props.origin) return []
  return props.points.map((point) => ({
    x: (point.lng - props.origin.lng) * METERS_PER_DEG_LNG,
    y: (point.lat - props.origin.lat) * METERS_PER_DEG_LAT,
    distance: point.distance_from_origin_m,
  }))
})

const scale = computed(() => {
  const maxOffset = offsets.value.reduce(
    (max, item) => Math.max(max, Math.abs(item.x), Math.abs(item.y)),
    props.radius,
  )
  return (props.size / 2 - 24) / (maxOffset * 1.12 || 1)
})

const center = computed(() => props.size / 2)

const projected = computed(() =>
  offsets.value.map((item) => ({
    x: center.value + item.x * scale.value,
    y: center.value - item.y * scale.value,
    distance: item.distance,
  })),
)

const path = computed(() =>
  projected.value.map((p, index) => `${index === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '),
)

const fenceRadius = computed(() => props.radius * scale.value)
const last = computed(() => projected.value[projected.value.length - 1] || null)
const outside = computed(() => (last.value?.distance || 0) > props.radius)
</script>

<template>
  <div class="track-map">
    <svg :width="size" :height="size" :viewBox="`0 0 ${size} ${size}`">
      <circle :cx="center" :cy="center" :r="fenceRadius" class="fence" />
      <circle :cx="center" :cy="center" :r="fenceRadius * 0.5" class="fence-inner" />
      <line :x1="0" :y1="center" :x2="size" :y2="center" class="axis" />
      <line :x1="center" :y1="0" :x2="center" :y2="size" class="axis" />

      <path v-if="projected.length > 1" :d="path" class="track" />
      <circle
        v-for="(point, index) in projected"
        :key="index"
        :cx="point.x"
        :cy="point.y"
        r="2.5"
        class="point"
      />

      <circle :cx="center" :cy="center" r="6" class="home" />
      <text :x="center + 9" :y="center - 6" class="label">家</text>

      <template v-if="last">
        <circle :cx="last.x" :cy="last.y" r="7" :class="['current', { outside }]" />
        <text :x="last.x + 10" :y="last.y + 4" class="label">当前</text>
      </template>
    </svg>

    <div class="legend">
      <span><i class="dot fence-dot"></i>电子围栏 {{ radius }} 米</span>
      <span><i class="dot track-dot"></i>行走轨迹 {{ points.length }} 点</span>
      <span v-if="last" :class="outside ? 'status-danger' : 'status-normal'">
        当前离家 {{ Math.round(last.distance) }} 米{{ outside ? '（已越界）' : '' }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.track-map {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}

svg {
  background: var(--surface-tint);
  border: 1px solid var(--line);
  border-radius: 10px;
}

.fence {
  fill: rgba(47, 111, 235, 0.06);
  stroke: var(--brand);
  stroke-dasharray: 5 4;
  stroke-width: 1.2;
}

.fence-inner {
  fill: none;
  stroke: rgba(47, 111, 235, 0.25);
  stroke-dasharray: 3 4;
  stroke-width: 1;
}

.axis {
  stroke: var(--line-soft);
  stroke-width: 1;
}

.track {
  fill: none;
  stroke: var(--ok);
  stroke-width: 2;
  stroke-linejoin: round;
}

.point {
  fill: var(--ok);
  opacity: 0.75;
}

.home {
  fill: var(--brand);
}

.current {
  fill: var(--warn);
}

.current.outside {
  fill: var(--danger);
}

.label {
  font-size: 13px;
  fill: var(--ink-soft);
}

.legend {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 14px;
  color: var(--ink-soft);
}

.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 5px;
}

.fence-dot {
  background: var(--brand);
}

.track-dot {
  background: var(--ok);
}
</style>
