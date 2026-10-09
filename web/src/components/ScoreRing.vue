<script setup>
import { computed } from 'vue'

// 健康评分环：0~100，颜色跟等级走。score 为空时画一个灰色空环并显示 ——
const props = defineProps({
  score: { type: Number, default: null },
  grade: { type: String, default: '' },
  delta: { type: Number, default: null },
  size: { type: Number, default: 64 },
  stroke: { type: Number, default: 6 },
  showGrade: { type: Boolean, default: true },
  // 深色背景（家属端状态卡）上用白色描边
  inverted: { type: Boolean, default: false },
  // 数据不全、置信度低时只作参考：灰色虚线环，避免和满数据的分数长得一样
  reference: { type: Boolean, default: false },
})

const GRADE_TONE = { 优: 'var(--ok)', 良: 'var(--brand)', 中: 'var(--warn)', 差: 'var(--danger)' }

const radius = computed(() => (props.size - props.stroke) / 2)
const circumference = computed(() => 2 * Math.PI * radius.value)
const offset = computed(() => {
  const ratio = props.score == null ? 0 : Math.max(0, Math.min(100, props.score)) / 100
  return circumference.value * (1 - ratio)
})
const color = computed(() => {
  if (props.score == null) return 'var(--line)'
  if (props.reference) return props.inverted ? 'rgba(255,255,255,0.6)' : 'var(--ink-soft)'
  if (props.inverted) return 'rgba(255,255,255,0.95)'
  return GRADE_TONE[props.grade] || 'var(--brand)'
})
const fontSize = computed(() => Math.round(props.size * 0.31))
</script>

<template>
  <div
    class="ring"
    :class="{ inverted, empty: score == null, reference }"
    :style="{ width: `${size}px`, height: `${size}px` }"
    :title="score == null ? '暂无健康评分' : reference ? `健康评分 ${score} 分（数据不全，仅供参考）` : `健康评分 ${score} 分`"
  >
    <svg :viewBox="`0 0 ${size} ${size}`" class="ring-svg">
      <circle class="track" :cx="size / 2" :cy="size / 2" :r="radius" :stroke-width="stroke" fill="none" />
      <circle
        class="bar"
        :cx="size / 2"
        :cy="size / 2"
        :r="radius"
        :stroke-width="stroke"
        fill="none"
        :stroke="color"
        :stroke-dasharray="circumference"
        :stroke-dashoffset="offset"
        stroke-linecap="round"
      />
    </svg>
    <div class="ring-center">
      <span class="ring-score" :style="{ fontSize: `${fontSize}px` }">{{ score == null ? '——' : score }}</span>
      <span v-if="showGrade && score != null && reference" class="ring-grade" :style="{ color }">参考</span>
      <span v-else-if="showGrade && grade" class="ring-grade" :style="{ color }">{{ grade }}</span>
    </div>
    <span v-if="delta != null && delta !== 0 && score != null" class="ring-delta" :class="delta > 0 ? 'up' : 'down'">
      {{ delta > 0 ? '▲' : '▼' }}{{ Math.abs(delta) }}
    </span>
  </div>
</template>

<style scoped>
.ring {
  position: relative;
  display: inline-block;
  flex-shrink: 0;
}

.ring-svg {
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}

.track {
  stroke: var(--line-soft);
}

.inverted .track {
  stroke: rgba(255, 255, 255, 0.28);
}

.bar {
  transition: stroke-dashoffset 0.9s var(--spring), stroke 0.4s var(--ease-out);
}

.reference .bar {
  opacity: 0.7;
}

.reference .ring-score {
  color: var(--ink-soft);
}

.ring-center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  line-height: 1;
  gap: 2px;
}

.ring-score {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.5px;
  color: var(--ink);
}

.inverted .ring-score {
  color: #fff;
}

.empty .ring-score {
  color: var(--ink-soft);
  letter-spacing: 1px;
}

.ring-grade {
  font-size: 13px;
  font-weight: 600;
}

.ring-delta {
  position: absolute;
  right: -6px;
  top: -4px;
  font-size: 12.5px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--surface);
  border: 1px solid var(--line);
  line-height: 1.4;
}

.ring-delta.up {
  color: var(--ok);
}

.ring-delta.down {
  color: var(--danger);
}

.inverted .ring-delta {
  background: rgba(255, 255, 255, 0.2);
  border-color: transparent;
  color: #fff;
}
</style>
