<script setup>
import { computed } from 'vue'

const props = defineProps({
  title: { type: String, default: '' },
  hint: { type: String, default: '' },
  icon: { type: String, default: '' },
  // 12 栅格中占据的列数与行数，用于构成不对称布局
  span: { type: Number, default: 4 },
  rows: { type: Number, default: 1 },
  tone: { type: String, default: 'plain' }, // plain / brand / tint
  interactive: { type: Boolean, default: false },
  padded: { type: Boolean, default: true },
})

// 中等屏幕上只保留"半行 / 整行"两档：窄卡并排，宽卡独占一行，
// 避免出现 6+12 这种必然在右侧留白的组合
const mdSpan = computed(() => (props.span <= 4 ? 6 : 12))
</script>

<template>
  <section
    class="bento"
    :class="[`tone-${tone}`, { liftable: interactive, 'is-padded': padded }]"
    :style="{ '--span': span, '--rows': rows, '--span-md': mdSpan }"
  >
    <header v-if="title" class="bento-head">
      <span v-if="icon" class="bento-icon">
        <el-icon :size="16"><component :is="icon" /></el-icon>
      </span>
      <h3 class="bento-title">{{ title }}</h3>
      <slot name="head" />
      <span v-if="hint" class="bento-hint">{{ hint }}</span>
    </header>
    <slot />
  </section>
</template>

<style scoped>
.bento {
  grid-column: span var(--span);
  grid-row: span var(--rows);
  border-radius: var(--radius-lg);
  border: 1px solid var(--line);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
  min-width: 0;
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: hidden;
}

.bento.is-padded {
  padding: 20px;
}

.tone-tint {
  background: linear-gradient(160deg, var(--surface-tint), var(--surface));
}

.tone-brand {
  background: linear-gradient(155deg, var(--brand-deep) 0%, var(--brand) 52%, #4ec2ac 100%);
  border-color: transparent;
  color: #fff;
}

.bento-head {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 15px;
}

.bento-icon {
  width: 28px;
  height: 28px;
  border-radius: 9px;
  background: var(--brand-soft);
  color: var(--brand);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.tone-brand .bento-icon {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
}

.bento-title {
  font-size: 16.5px;
  font-weight: 600;
  margin: 0;
  white-space: nowrap;
}

.bento-hint {
  margin-left: auto;
  font-size: 14px;
  color: var(--ink-soft);
  white-space: nowrap;
}

.tone-brand .bento-hint {
  color: rgba(255, 255, 255, 0.8);
}

@media (max-width: 1000px) {
  .bento {
    grid-column: span var(--span-md);
  }
}

@media (max-width: 720px) {
  .bento {
    grid-column: span 12;
    grid-row: span 1;
  }
}
</style>
