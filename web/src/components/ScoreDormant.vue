<script setup>
// 健康评分还没接入时的样子：分数环是空的，维度条是灰的。
// 放在原本显示评分的位置，让人知道"这里会有东西"，但不透露任何分数。
defineProps({
  // 档案页用大块面板；其他地方只需要一个小空环
  compact: { type: Boolean, default: false },
  size: { type: Number, default: 46 },
})

const DIMS = ['急性体征风险', '慢病控制', '个人基线', '波动', '活动量', '睡眠', '趋势']
</script>

<template>
  <span
    v-if="compact"
    class="ring-empty"
    :style="{ width: `${size}px`, height: `${size}px` }"
    title="健康评分尚未接入"
  >
    <span class="ring-dash"></span>
  </span>

  <div v-else class="score-dormant">
    <div class="ring-block">
      <span class="ring-empty large">
        <span class="ring-dash"></span>
        <span class="ring-text">——</span>
      </span>
      <div>
        <div class="title">健康评分尚未接入</div>
        <div class="sub"><i class="dot breathing"></i>评分模型已就绪，等待评分数据</div>
      </div>
    </div>
    <div class="dims">
      <div v-for="label in DIMS" :key="label" class="dim">
        <span class="dim-label">{{ label }}</span>
        <span class="dim-bar"></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ring-empty {
  position: relative;
  display: inline-grid;
  place-items: center;
  width: 46px;
  height: 46px;
  flex-shrink: 0;
}

.ring-empty.large {
  width: 118px;
  height: 118px;
}

.ring-dash {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 4px dashed var(--line);
}

.ring-empty.large .ring-dash {
  border-width: 6px;
}

.ring-text {
  font-size: 30px;
  font-weight: 600;
  letter-spacing: 3px;
  color: #c9c2b6;
}

.score-dormant {
  display: grid;
  grid-template-columns: 1fr;
  gap: 20px;
  align-items: center;
  padding: 18px 20px;
  border-radius: var(--radius-lg);
  border: 1px dashed var(--line);
  background: linear-gradient(135deg, var(--surface), var(--surface-tint));
  color: var(--ink-soft);
  user-select: none;
}

.ring-block {
  display: flex;
  align-items: center;
  gap: 20px;
}

.title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 16.5px;
  font-weight: 600;
  color: var(--ink-mid);
}

.sub {
  margin-top: 6px;
  font-size: 15px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--brand);
  color: var(--brand);
  display: inline-block;
}

.dims {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px 18px;
}

.dim {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 10px 12px;
  border-radius: var(--radius);
  border: 1px dashed var(--line-soft);
}

.dim-label {
  font-size: 15px;
}

.dim-bar {
  height: 6px;
  border-radius: 3px;
  background: var(--line-soft);
}

</style>
