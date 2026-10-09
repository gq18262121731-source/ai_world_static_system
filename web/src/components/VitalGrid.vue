<script setup>
import { computed } from 'vue'

const props = defineProps({
  assessment: { type: Object, default: null },
  // 可点选时，指标卡可作为其他页面的页签使用；老人详情页默认直接展示全部曲线
  selectable: { type: Boolean, default: false },
  modelValue: { type: String, default: '' },
  // 没有任何读数时五张卡照常摆着，值是 --；这里决定标签写"无数据"还是"未接入"
  emptyTag: { type: String, default: '无数据' },
})
const emit = defineEmits(['update:modelValue'])

const STATUS_TEXT = { normal: '正常', warning: '偏离', danger: '异常', unknown: '无数据' }
const STATUS_PRIORITY = { unknown: 0, normal: 1, warning: 2, danger: 3 }
const ORDER = ['heart_rate', 'spo2', 'temperature', 'blood_pressure']
const DEFAULTS = {
  heart_rate: { label: '心率', unit: '次/分' },
  spo2: { label: '血氧', unit: '%' },
  temperature: { label: '体温', unit: '℃' },
  blood_pressure: { label: '血压', unit: 'mmHg' },
}

function format(key, value) {
  if (value === null || value === undefined) return '--'
  return key === 'temperature' ? Number(value).toFixed(1) : Math.round(value)
}

const vitals = computed(() => {
  const source = props.assessment?.vitals || {}
  return ORDER.map((key) => {
    if (key !== 'blood_pressure') {
      return {
        key,
        ...DEFAULTS[key],
        status: 'unknown',
        latest: null,
        min: null,
        max: null,
        ...(source[key] || {}),
      }
    }

    const systolic = source.systolic || {}
    const diastolic = source.diastolic || {}
    const status = [systolic.status, diastolic.status]
      .filter(Boolean)
      .sort((a, b) => (STATUS_PRIORITY[b] || 0) - (STATUS_PRIORITY[a] || 0))[0] || 'unknown'
    return {
      key,
      ...DEFAULTS[key],
      status,
      latest: [systolic.latest, diastolic.latest],
      min: [systolic.min, diastolic.min],
      max: [systolic.max, diastolic.max],
    }
  })
})

const hasReadings = computed(() => Object.keys(props.assessment?.vitals || {}).length > 0)

function statusText(vital) {
  if (vital.status === 'unknown' && !hasReadings.value) return props.emptyTag
  return STATUS_TEXT[vital.status]
}

function formatValue(vital, field) {
  const value = vital[field]
  if (vital.key !== 'blood_pressure') return format(vital.key, value)
  return value.map((item) => format('systolic', item)).join('/')
}

function select(key) {
  if (props.selectable) emit('update:modelValue', key)
}
</script>

<template>
  <div class="vitals" :class="{ selectable }">
    <div
      v-for="vital in vitals"
      :key="vital.key"
      class="vital"
      :class="[`is-${vital.status}`, { active: selectable && vital.key === modelValue }]"
      :role="selectable ? 'tab' : undefined"
      :tabindex="selectable ? 0 : undefined"
      :aria-selected="selectable ? vital.key === modelValue : undefined"
      @click="select(vital.key)"
      @keydown.enter.prevent="select(vital.key)"
      @keydown.space.prevent="select(vital.key)"
    >
      <div class="name">
        {{ vital.label }}
        <span v-if="selectable && vital.key === modelValue" class="active-hint">曲线</span>
      </div>
      <div class="value">
        {{ formatValue(vital, 'latest') }}<span class="unit">{{ vital.unit }}</span>
      </div>
      <div class="meta">
        <el-tag size="small" :type="{ normal: 'success', warning: 'warning', danger: 'danger' }[vital.status] || 'info'" effect="plain">
          {{ statusText(vital) }}
        </el-tag>
        <span class="range">
          24h {{ formatValue(vital, 'min') }} ~ {{ formatValue(vital, 'max') }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.vitals {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}

.vital {
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 12px;
  border-left-width: 3px;
  background: var(--surface);
  transition: transform var(--dur) var(--ease-out), box-shadow var(--dur) var(--ease-out),
    background-color var(--dur) var(--ease-out);
}

.selectable .vital {
  cursor: pointer;
  user-select: none;
}

.selectable .vital:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-sm, 0 4px 14px rgba(38, 48, 43, 0.08));
}

.selectable .vital:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

.vital.active {
  background: var(--brand-tint);
  border-color: var(--brand);
  box-shadow: 0 0 0 1px var(--brand) inset;
}

.vital.is-normal {
  border-left-color: var(--ok);
}

.vital.is-warning {
  border-left-color: var(--warn);
}

.vital.is-danger {
  border-left-color: var(--danger);
}

.name {
  font-size: 15px;
  color: var(--ink-soft);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.active-hint {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--brand);
  background: var(--surface);
  border: 1px solid var(--brand);
  border-radius: 999px;
  padding: 0 7px;
  line-height: 1.5;
}

.value {
  font-size: 24px;
  font-weight: 600;
  margin: 4px 0 8px;
}

.unit {
  font-size: 14px;
  font-weight: 400;
  color: var(--ink-soft);
  margin-left: 3px;
}

.meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.range {
  font-size: 13px;
  color: var(--ink-soft);
}
</style>
