<script setup>
import { computed, toRef } from 'vue'
import { useCountUp } from '@/composables/useCountUp'

const props = defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: 0 },
  suffix: { type: String, default: '' },
  hint: { type: String, default: '' },
  tone: { type: String, default: 'brand' },
  icon: { type: String, default: 'DataLine' },
  clickable: { type: Boolean, default: false },
})

const numeric = computed(() =>
  typeof props.value === 'number' ? props.value : Number(props.value),
)
const animated = useCountUp(toRef(props, 'value'))

const shown = computed(() =>
  Number.isNaN(numeric.value) ? props.value : Math.round(animated.value),
)
</script>

<template>
  <div
    class="tile"
    :class="[`tone-${tone}`, { clickable, liftable: clickable }]"
    v-ripple
  >
    <span class="tile-icon">
      <el-icon :size="19"><component :is="icon" /></el-icon>
    </span>
    <div class="tile-body">
      <div class="tile-label">{{ label }}</div>
      <div class="tile-value">
        <span class="tile-number">{{ shown }}</span>
        <small v-if="suffix">{{ suffix }}</small>
      </div>
      <div v-if="hint" class="tile-hint">{{ hint }}</div>
    </div>
    <span class="tile-arc"></span>
  </div>
</template>

<style scoped>
.tile {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 13px;
  padding: 18px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--line);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
  min-width: 0;
}

.clickable {
  cursor: pointer;
}

/* 右下角的弧线，替代常见的方块背景色 */
.tile-arc {
  position: absolute;
  width: 108px;
  height: 108px;
  right: -42px;
  bottom: -56px;
  border-radius: 50%;
  border: 16px solid currentColor;
  opacity: 0.12;
  pointer-events: none;
  transition: transform var(--dur-slow) var(--spring);
}

.tile:hover .tile-arc {
  transform: scale(1.16) translate(-5px, -5px);
}

.tone-brand {
  color: var(--brand);
}
.tone-ok {
  color: var(--ok);
}
.tone-warn {
  color: var(--warn);
}
.tone-danger {
  color: var(--danger);
}

.tile-icon {
  width: 40px;
  height: 40px;
  border-radius: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: currentColor;
  position: relative;
}

.tile-icon :deep(.el-icon) {
  color: #fff;
  position: relative;
  z-index: 1;
}

.tile-body {
  min-width: 0;
  position: relative;
  z-index: 1;
}

.tile-label {
  font-size: 15px;
  color: var(--ink-soft);
}

.tile-value {
  display: flex;
  align-items: baseline;
  gap: 4px;
  margin-top: 1px;
}

.tile-number {
  font-size: 29px;
  font-weight: 700;
  line-height: 1.25;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.5px;
}

.tile-value small {
  font-size: 14.5px;
  color: var(--ink-soft);
}

.tile-hint {
  font-size: 13.5px;
  color: var(--ink-soft);
  margin-top: 1px;
}
</style>
