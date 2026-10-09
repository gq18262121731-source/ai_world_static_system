<script setup>
defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: '--' },
  suffix: { type: String, default: '' },
  hint: { type: String, default: '' },
  tone: { type: String, default: 'brand' },
  icon: { type: String, default: 'DataLine' },
})
</script>

<template>
  <div class="stat card" :class="`tone-${tone}`">
    <div class="icon">
      <el-icon :size="21"><component :is="icon" /></el-icon>
    </div>
    <div class="body">
      <div class="label">{{ label }}</div>
      <div class="value">
        {{ value }}<small v-if="suffix">{{ suffix }}</small>
      </div>
      <div v-if="hint" class="hint">{{ hint }}</div>
    </div>
    <div class="glow"></div>
  </div>
</template>

<style scoped>
.stat {
  display: flex;
  align-items: center;
  gap: 15px;
  position: relative;
  overflow: hidden;
}

/* 右上角一抹与语义色呼应的柔光，让卡片不那么干硬 */
.glow {
  position: absolute;
  width: 130px;
  height: 130px;
  border-radius: 50%;
  right: -55px;
  top: -60px;
  opacity: 0.5;
  pointer-events: none;
}

.icon {
  width: 48px;
  height: 48px;
  border-radius: 15px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.tone-brand .icon {
  background: var(--brand-soft);
  color: var(--brand);
}

.tone-brand .glow {
  background: var(--brand-soft);
}

.tone-ok .icon {
  background: var(--ok-soft);
  color: var(--ok);
}

.tone-ok .glow {
  background: var(--ok-soft);
}

.tone-warn .icon {
  background: var(--warn-soft);
  color: var(--warn);
}

.tone-warn .glow {
  background: var(--warn-soft);
}

.tone-danger .icon {
  background: var(--danger-soft);
  color: var(--danger);
}

.tone-danger .glow {
  background: var(--danger-soft);
}

.body {
  position: relative;
  min-width: 0;
}

.label {
  font-size: 15.5px;
  color: var(--ink-soft);
}

.value {
  font-size: 27px;
  font-weight: 600;
  line-height: 1.35;
  letter-spacing: 0.5px;
}

.value small {
  font-size: 15px;
  font-weight: 400;
  color: var(--ink-soft);
  margin-left: 4px;
}

.hint {
  font-size: 14px;
  color: var(--ink-soft);
  margin-top: 2px;
}
</style>
