<script setup>
import { computed } from 'vue'
import dayjs from 'dayjs'
import { useRealtimeStore } from '@/stores/realtime'

const props = defineProps({
  limit: { type: Number, default: 30 },
  elderId: { type: Number, default: null },
  events: { type: Array, default: null },
})

const realtime = useRealtimeStore()

const EVENT_META = {
  alarm: { label: '告警', type: 'danger' },
  alarm_updated: { label: '告警更新', type: 'warning' },
  alarm_notification: { label: '通知下发', type: 'warning' },
  outing_decision: { label: '外出决策', type: 'primary' },
  outing_started: { label: '外出开始', type: 'success' },
  outing_ended: { label: '外出结束', type: 'info' },
  track_point: { label: '轨迹上报', type: 'info' },
  conversation: { label: '语音对话', type: 'primary' },
  device_command: { label: '指令下发', type: 'info' },
  device_status: { label: '设备状态', type: 'info' },
  health_record: { label: '健康数据', type: 'info' },
}

function describe(item) {
  const data = item.data || {}
  switch (item.event) {
    case 'alarm':
    case 'alarm_updated':
      return `${data.title || '告警'} ${data.description || ''} 状态 ${data.status || ''}`
    case 'alarm_notification':
      return `${data.channel} → ${data.target_name}：${data.content}`
    case 'outing_decision':
      return `决策 ${data.decision}：${(data.reasons || [])[0] || ''}`
    case 'outing_started':
      return `任务 #${data.task_id} 开始，限时 ${data.duration_minutes} 分钟 / ${data.radius_m} 米`
    case 'outing_ended':
      return data.summary
    case 'track_point':
      return `离家 ${Math.round(data.distance_from_origin_m)} 米`
    case 'conversation':
      return `老人：${data.question} → 机器狗：${data.answer}`
    case 'device_command':
      return `${data.device_id} ← ${data.command} ${data.payload?.text || ''}`
    case 'device_status':
      return `${data.device_id} ${data.status === 'online' ? '上线' : '离线'}`
    case 'health_record':
      return `心率 ${data.heart_rate ?? '--'}，血氧 ${data.spo2 ?? '--'}%，血压 ${data.systolic ?? '--'}/${data.diastolic ?? '--'}`
    default:
      return JSON.stringify(data)
  }
}

const items = computed(() => {
  const source = props.events || realtime.events
  return source
    .filter((item) => !props.elderId || item.elder_id === props.elderId)
    .slice(0, props.limit)
})
</script>

<template>
  <div class="feed">
    <el-empty v-if="!items.length" description="等待实时事件…" :image-size="60" />
    <div v-for="(item, index) in items" :key="index" class="item">
      <el-tag size="small" :type="EVENT_META[item.event]?.type || 'info'" effect="plain">
        {{ EVENT_META[item.event]?.label || item.event }}
      </el-tag>
      <div class="body">
        <div class="text">{{ describe(item) }}</div>
        <div class="time">{{ dayjs(item.ts).format('MM-DD HH:mm:ss') }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.feed {
  max-height: 420px;
  overflow-y: auto;
}

.item {
  display: flex;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px dashed var(--line);
  align-items: flex-start;
}

.item:last-child {
  border-bottom: none;
}

.body {
  flex: 1;
  min-width: 0;
}

.text {
  font-size: 15px;
  line-height: 1.6;
  word-break: break-word;
}

.time {
  font-size: 13px;
  color: var(--ink-soft);
  margin-top: 2px;
}
</style>
